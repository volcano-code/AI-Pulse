"""Bounded local-evidence agent with durable audit receipts, not web research.

In extractive mode the route is deterministic (zero model calls). In live mode
native model tool calls choose between TWO read-only, server-scoped tools. Stored
citations prove quote location only; generated claims always remain needs_review.
"""
import json
import time
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, select, update
from .llm import EvidenceError, ModelError
from .models import Brief, Investigation, Snapshot, Workspace
from .research_model import ResearchModelClient, SYSTEM
from .research_retrieval import search_research_evidence
from .schemas import ResearchDraft
from .textutil import digest
from .timeutil import iso, utcnow


class ResearchConflict(ValueError):
    pass


class SearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=2, max_length=400)


class InspectArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence_id: str = Field(pattern=r"^E[1-9][0-9]?$", max_length=3)


def investigation_payload(row):
    return {"id": row.id, "question": row.question, "brief_id": row.brief_id,
            "data_mode": row.data_mode, "generation_mode": row.generation_mode,
            "status": row.status, "created_at": row.created_at,
            "finished_at": row.finished_at, "result": row.result, "trace": row.trace}


def execute_research(factory, settings, request, model_client=None, clock=time.monotonic):
    identity = request.model_dump(exclude={"idempotency_key"}) | {
        "data_mode": settings.data_mode, "llm_mode": settings.llm_mode,
        "model": settings.llm_model if settings.llm_mode == "live" else None,
        "retrieval_mode": settings.retrieval_mode,
        "embedding_provider": getattr(settings, "embedding_provider", "") or None,
        "embedding_model": settings.embedding_model or None,
        "workflow": "bounded-evidence-v2"}
    request_hash = digest(json.dumps(identity, sort_keys=True, ensure_ascii=False))
    with factory.begin() as db:
        # Serialize creation for the single-owner workspace, not a distributed broker.
        db.execute(update(Workspace).where(Workspace.id == 1).values(id=1))
        if request.brief_id:
            brief = db.get(Brief, request.brief_id)
            if not brief or brief.data_mode != settings.data_mode:
                raise LookupError("Brief not found")
        if request.idempotency_key:
            previous = db.scalar(select(Investigation).where(Investigation.request_key == request.idempotency_key))
            if previous:
                if previous.request_hash != request_hash:
                    raise ResearchConflict("Idempotency key belongs to a different research request")
                if previous.status == "running":
                    raise ResearchConflict("Research is running or was interrupted; inspect its record, do not auto-replay")
                return investigation_payload(previous) | {"cached": True}
        if db.scalar(select(func.count()).select_from(Investigation).where(Investigation.status == "running")):
            raise ResearchConflict("One investigation is already active; inspect or reconcile it first")
        row = Investigation(question=request.question, brief_id=request.brief_id,
                            data_mode=settings.data_mode, generation_mode=settings.llm_mode,
                            request_key=request.idempotency_key, request_hash=request_hash)
        db.add(row); db.flush(); run_id = row.id
    started = clock()
    evidence, evidence_keys, trace, usages = {}, {}, [], []
    tool_calls, model_calls, searched = 0, 0, 0
    retrieval_receipts = []

    def event(stage, message, **extra):
        item = {"stage": stage, "message": message, "at": iso(utcnow()), **extra}
        trace.append(item)
        with factory.begin() as db:
            row = db.get(Investigation, run_id)
            row.trace = list(trace)

    def remaining():
        return settings.research_deadline_seconds - (clock() - started)

    def tool(name, arguments):
        nonlocal tool_calls, searched
        if tool_calls >= request.max_tool_calls or remaining() <= 0:
            raise TimeoutError("Research tool/time budget exhausted")
        tool_calls += 1
        if name == "search_saved_evidence":
            args = SearchArgs.model_validate(arguments)
            with factory() as db:
                result = search_research_evidence(db, settings, args.query, request.brief_id)
            searched = max(searched, result["searched_documents"])
            receipt = {
                "requested_mode": result["requested_mode"],
                "effective_mode": result["effective_mode"],
                "degraded": result["degraded"],
                "degraded_reason": result["degraded_reason"],
                "searched_documents": result["searched_documents"],
            }
            retrieval_receipts.append(receipt)
            found = []
            for citation in result["citations"]:
                key = (citation["snapshot_id"], citation["quote_start"], citation["quote_end"])
                eid = evidence_keys.get(key)
                if not eid:
                    eid = f"E{len(evidence) + 1}"
                    evidence_keys[key] = eid
                    evidence[eid] = citation | {"evidence_id": eid}
                found.append(evidence[eid])
            event("tool", "检索已保存的证据", tool=name, query=args.query, found=len(found), retrieval=receipt)
            return {"evidence": found, "searched_documents": result["searched_documents"], "retrieval": receipt}
        if name == "inspect_evidence":
            args = InspectArgs.model_validate(arguments)
            if args.evidence_id not in evidence:
                raise ValueError("Evidence ID was not returned by this investigation")
            citation = evidence[args.evidence_id]
            with factory() as db:
                snap = db.get(Snapshot, citation["snapshot_id"])
                if not snap or snap.text[citation["quote_start"]:citation["quote_end"]] != citation["quote"]:
                    raise EvidenceError("Saved evidence does not match the immutable snapshot")
            event("tool", "复核原文引用位置", tool=name, evidence_id=args.evidence_id)
            return citation | {"verification": "exact_quote_location_only"}
        raise ValueError("Tool not permitted")

    event("start", "只检索本地保存的资料；不访问任意网页、不执行代码、不发送邮件",
          workflow="bounded-evidence-v2", requested_retrieval_mode=settings.retrieval_mode,
          tool_budget=request.max_tool_calls,
          model_budget=request.max_model_calls if settings.llm_mode == "live" else 0)
    status, stop_reason, claims = "completed", "completed", []
    try:
        if settings.llm_mode == "extractive":
            tool("search_saved_evidence", {"query": request.question})
            # Transparent deterministic expansion, not represented as autonomous thinking.
            expanded = request.question
            for term, alternative in {"智能体": "agent", "大模型": "LLM", "开源": "open source", "论文": "paper"}.items():
                if term in request.question:
                    expanded += " " + alternative
            if not evidence and expanded != request.question and tool_calls < request.max_tool_calls:
                tool("search_saved_evidence", {"query": expanded})
            if evidence:
                tool_result = next(iter(evidence.values()))
                if tool_calls < request.max_tool_calls:
                    tool("inspect_evidence", {"evidence_id": tool_result["evidence_id"]})
            else:
                stop_reason = "insufficient_evidence"
        else:
            client = model_client or ResearchModelClient(settings)
            messages = [{"role": "system", "content": SYSTEM},
                        {"role": "user", "content": json.dumps({"question": request.question,
                            "tool_budget": request.max_tool_calls, "model_budget": request.max_model_calls}, ensure_ascii=False)}]
            seen_call_ids = set()
            for _ in range(request.max_model_calls):
                if remaining() <= 0:
                    raise TimeoutError("Research time budget exhausted")
                model_calls += 1
                # Commit before calling: interrupted provider requests are never auto-replayed.
                event("model_request", "请求结构化工具决策或带引用草稿", attempt=model_calls)
                turn = client.next_turn(messages, timeout=min(45, remaining()))
                usage = turn.get("usage") or {}
                safe_usage = {k: v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None
                              for k, v in ((k, usage.get(k)) for k in ("prompt_tokens", "completion_tokens"))}
                usages.append(safe_usage)
                event("model_response", "已收到响应；只保留公开工具事件与用量", usage=safe_usage)
                message = turn["message"]
                messages.append({k: message[k] for k in ("role", "content", "tool_calls") if k in message})
                calls = message.get("tool_calls") or []
                if calls:
                    if not isinstance(calls, list) or len(calls) > request.max_tool_calls - tool_calls:
                        raise TimeoutError("Model requested more tools than the remaining budget")
                    for call in calls:
                        call_id = call.get("id")
                        if not isinstance(call_id, str) or not 1 <= len(call_id) <= 128 or call_id in seen_call_ids:
                            raise ValueError("Invalid or duplicate tool call ID")
                        seen_call_ids.add(call_id)
                        if call.get("type") != "function":
                            raise ValueError("Only declared function tools are accepted")
                        function = call.get("function", {})
                        arguments = json.loads(function.get("arguments", ""))
                        value = tool(function.get("name"), arguments)
                        messages.append({"role": "tool", "tool_call_id": call_id,
                                         "content": json.dumps(value, ensure_ascii=False)})
                    continue
                draft = ResearchDraft.model_validate_json(message.get("content") or "")
                if draft.insufficient_evidence:
                    if draft.claims:
                        raise ValueError("Abstention cannot contain asserted claims")
                    stop_reason = "insufficient_evidence"
                else:
                    if not draft.claims:
                        raise ValueError("Non-abstaining draft must contain cited claims")
                    for claim in draft.claims:
                        if any(eid not in evidence for eid in claim.evidence_ids):
                            raise EvidenceError("Model referenced evidence it never retrieved")
                    claims = [claim.model_dump() for claim in draft.claims]
                    status = "needs_review"
                break
            else:
                stop_reason = "model_budget_exceeded"
                status = "partial"
    except TimeoutError:
        status, stop_reason = "partial", "budget_exceeded"
        event("stop", "已达到工具、模型或总时限预算；保留已获得证据，不继续循环")
    except (ModelError, EvidenceError, ValidationError, ValueError, KeyError, TypeError):
        status, stop_reason = "needs_attention", "model_or_evidence_error"
        event("stop", "模型、工具参数或证据校验失败；未保存未通过的结论，不自动重试模型请求")
    except Exception:
        status, stop_reason = "needs_attention", "internal_error"
        event("stop", "研究执行异常；仅保留已提交证据记录，不暴露上游错误内容")
    # Last gate checks every returned quote again, independent of model decisions.
    with factory() as db:
        valid = {}
        for eid, citation in evidence.items():
            snap = db.get(Snapshot, citation["snapshot_id"])
            if snap and snap.text[citation["quote_start"]:citation["quote_end"]] == citation["quote"]:
                valid[eid] = citation
        if len(valid) != len(evidence):
            claims = []; status, stop_reason = "needs_attention", "evidence_changed"
        evidence = valid
    if stop_reason != "completed":
        claims = []
    answer = ("\n".join(c["text"] + " [" + ", ".join(c["evidence_ids"]) + "]" for c in claims)
              if claims else "找到以下原文证据；本次未生成已核实结论。"
              if evidence else "当前资料中没有匹配到足够证据；不能据此给出结论。")
    result = {"answer": answer, "claims": claims, "citations": list(evidence.values()),
              "stop_reason": stop_reason, "tool_calls": tool_calls, "model_calls": model_calls,
              "searched_documents": searched, "abstained": not claims,
              "retrieval": retrieval_receipts[-1] if retrieval_receipts else {
                  "requested_mode": settings.retrieval_mode,
                  "effective_mode": None,
                  "degraded": None,
                  "degraded_reason": "not_executed",
                  "searched_documents": 0,
              },
              "verification": "quote_location_only", "elapsed_ms": round((clock() - started) * 1000),
              "usage": {"prompt_tokens": sum(u["prompt_tokens"] for u in usages) if usages and all(u["prompt_tokens"] is not None for u in usages) else None,
                        "completion_tokens": sum(u["completion_tokens"] for u in usages) if usages and all(u["completion_tokens"] is not None for u in usages) else None,
                        "cost_usd": 0 if model_calls == 0 else None},
              "limitations": ["不进行自主联网研究", "引用位置匹配不等于语义事实核验", "模型草稿仅供人工复核，不自动分发"]}
    event("finish", "研究记录已完成并保存", status=status, stop_reason=stop_reason)
    with factory.begin() as db:
        row = db.get(Investigation, run_id)
        row.status, row.result, row.finished_at = status, result, iso(utcnow())
        db.flush()
        return investigation_payload(row) | {"cached": False}


def recover_interrupted_research(factory):
    """Call only at startup of the supported single API process.

    Prior incomplete model calls may have been charged. Never execute them again.
    A new explicit request must use a different idempotency key.
    """
    with factory.begin() as db:
        for row in db.scalars(select(Investigation).where(Investigation.status == "running")):
            row.status, row.finished_at = "needs_attention", iso(utcnow())
            row.result = {"stop_reason": "interrupted_before_result", "claims": [], "citations": [],
                          "answer": "上次 API 进程中断；保留审计记录，不自动重放可能已收费的模型请求。",
                          "model_calls": sum(x.get("stage") == "model_request" for x in row.trace),
                          "tool_calls": sum(x.get("stage") == "tool" for x in row.trace),
                          "usage": {"cost_usd": None}}
            row.trace = [*row.trace, {"stage": "interrupted", "message": "API 重启后停止旧研究，未自动重试", "at": iso(utcnow())}]
