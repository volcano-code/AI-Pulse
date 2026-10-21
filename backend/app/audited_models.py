"""Runtime chat-call audit. No automatic retry of an unconfirmed dispatch.

Brief drafts are validated application artifacts, stored separately from the
metadata-only ledger. Research turns are audited but never auto-replayed.
Injected clients are contract-test seams, not user-configurable providers.
"""
from __future__ import annotations

from contextlib import contextmanager
import time
from urllib.parse import urlsplit

from sqlalchemy import select, update

from .llm import ModelClient, ModelError, PROMPT_VERSION, SYSTEM, verify_quote
from .models import Article, BriefModelDraft, Investigation, Snapshot, Workspace
from .provider_calls import CallBlocked, CallLedger, fingerprint, safe_count
from .schemas import LLMDraft


def model_identity(settings, client=None) -> dict:
    """Fingerprint actual model semantics; never store endpoint credentials."""
    fixture = client is not None and (
        not isinstance(client, ModelClient) or client.transport is not None
    )
    # ResearchModelClient also supports an explicit offline transport seam.
    from .research_model import ResearchModelClient
    if isinstance(client, ResearchModelClient):
        fixture = client.transport is not None
    config = client.settings if isinstance(client, (ModelClient, ResearchModelClient)) else settings
    if not fixture:
        p = urlsplit(config.llm_base_url)
        if (settings.data_mode != "live" or settings.llm_mode != "live"
                or not config.llm_api_key or not config.llm_model
                or p.scheme != "https" or not p.hostname or p.username
                or p.password or p.query or p.fragment):
            raise ModelError("Explicit live workspace and trusted model configuration required")
    return {
        "provider": "fixture" if fixture else "openai-compatible",
        "model": config.llm_model or "injected-fixture",
        "endpoint_hash": fingerprint(config.llm_base_url.rstrip("/")),
        "max_output_tokens": config.llm_max_output_tokens,
        "response_format": config.llm_response_format,
    }


def brief_model_identity(settings, client=None) -> dict:
    return model_identity(settings, client) | {
        "prompt_version": PROMPT_VERSION, "prompt_hash": fingerprint(SYSTEM),
        "schema_hash": fingerprint(LLMDraft.model_json_schema()),
        "audit_version": "runtime-brief-v1",
    }


def _elapsed(started):
    return round((time.monotonic() - started) * 1000)


def _unknown(handle, started):
    try:
        handle.unknown(elapsed_ms=_elapsed(started))
    except Exception:
        # The already committed in_flight intent remains a blocking reservation.
        # A DB outage must never turn an uncertain call into retryable work.
        pass


def _check_snapshot(db, settings, snapshot_id, title, text):
    workspace = db.get(Workspace, 1)
    snap = db.get(Snapshot, snapshot_id)
    article = db.get(Article, snap.article_id) if snap else None
    if (not workspace or workspace.data_mode != settings.data_mode or not article
            or article.data_mode != settings.data_mode or snap.title != title or snap.text != text):
        raise ModelError("Snapshot/workspace scope does not match the requested draft")


def draft_brief_item(factory, settings, snapshot_id, title, text, *,
                     model_client=None, commit_guard=None, retry_authorization=None):
    identity = brief_model_identity(settings, model_client)
    request_hash = fingerprint({"config": identity, "snapshot_id": snapshot_id,
                                "title": title, "text": text[:7000],
                                "snapshot_text_hash": fingerprint(text)})
    def guard(db):
        # Missing migration must fail BEFORE a potentially billable request.
        db.execute(select(BriefModelDraft.call_id).limit(1))
        if commit_guard:
            commit_guard(db)
        _check_snapshot(db, settings, snapshot_id, title, text)
    handle = CallLedger(factory).reserve(
        operation_key="brief-draft:" + request_hash, request_hash=request_hash,
        kind="brief_draft", provider=identity["provider"], model=identity["model"],
        data_mode=settings.data_mode, commit_guard=guard, retry_authorization=retry_authorization,
    )
    if handle.reused:
        with factory() as db:
            _check_snapshot(db, settings, snapshot_id, title, text)
            saved = db.get(BriefModelDraft, handle.id)
            state = handle.read()["status"]
            if state != "succeeded" or saved is None or saved.snapshot_id != snapshot_id:
                raise CallBlocked(handle.id, state)
            draft = LLMDraft(title=saved.title, summary=saved.summary,
                             evidence_quote=saved.evidence_quote)
            verify_quote(draft.evidence_quote, text[:7000])
        return draft, {"model_calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
                       "cost_usd": 0, "provider_call_id": handle.id, "reused": True}
    started = time.monotonic()
    try:
        # Observers belong to this invocation, never mutate a shared client.
        client = model_client or ModelClient(settings)
        if isinstance(client, ModelClient):
            draft, usage = ModelClient(client.settings, client.transport, handle.observe).draft(title, text)
        else:
            draft, usage = client.draft(title, text)
        if not isinstance(usage, dict):
            usage = {}
        handle.observe(input_tokens=usage.get("prompt_tokens"),
                       output_tokens=usage.get("completion_tokens"))
        draft = LLMDraft.model_validate(draft, from_attributes=True)
        verify_quote(draft.evidence_quote, text[:7000])
        with factory.begin() as db:
            guard(db)
            db.add(BriefModelDraft(call_id=handle.id, snapshot_id=snapshot_id,
                                  title=draft.title, summary=draft.summary,
                                  evidence_quote=draft.evidence_quote))
            handle.succeed(db, elapsed_ms=_elapsed(started),
                           summary={"quote_exact": True, "draft_requires_review": True})
        return draft, {"model_calls": 1,
                       "prompt_tokens": safe_count(usage.get("prompt_tokens")),
                       "completion_tokens": safe_count(usage.get("completion_tokens")),
                       "cost_usd": None, "provider_call_id": handle.id, "reused": False}
    except BaseException:
        _unknown(handle, started)
        raise


@contextmanager
def research_turn(factory, settings, run_id, number, messages, client, timeout,
                  on_reserved):
    """A success means the turn passed the caller's protocol/evidence checks.

    No generated content is put in the ledger, and no research resumption or
    exactly-once billing is implied. Final research storage has its own transaction.
    """
    from .research_model import ResearchModelClient, SYSTEM as RESEARCH_SYSTEM, TOOLS
    identity = model_identity(settings, client)
    request_hash = fingerprint({"config": identity, "messages": messages,
                                "system": RESEARCH_SYSTEM, "tools": TOOLS,
                                "schema": "bounded-evidence-runtime-v1"})
    def guard(db):
        workspace = db.get(Workspace, 1)
        row = db.get(Investigation, run_id)
        if (not workspace or workspace.data_mode != settings.data_mode or not row
                or row.data_mode != settings.data_mode or row.generation_mode != "live"):
            raise ModelError("Research/workspace scope mismatch")
        changed = db.execute(update(Investigation).where(
            Investigation.id == run_id, Investigation.status == "running"
        ).values(status="running"))
        if changed.rowcount != 1:
            raise ModelError("Research is no longer active")
    handle = CallLedger(factory).reserve(
        operation_key=f"research:{run_id}:{number}", request_hash=request_hash,
        kind="research_turn", provider=identity["provider"], model=identity["model"],
        data_mode=settings.data_mode, commit_guard=guard,
    )
    handle.require_fresh()
    started = time.monotonic()
    try:
        on_reserved(handle.id)
        try:
            if isinstance(client, ResearchModelClient):
                turn = client.next_turn(messages, timeout, receipt_observer=handle.observe)
            else:
                turn = client.next_turn(messages, timeout)
        except TimeoutError:
            # Transport uncertainty is different from exhausting a tool budget.
            raise ModelError("Research call outcome unknown; automatic retry disabled") from None
        usage = turn.get("usage") or {}
        if not isinstance(usage, dict):
            usage = {}
        handle.observe(input_tokens=usage.get("prompt_tokens"),
                       output_tokens=usage.get("completion_tokens"))
        yield turn
        with factory.begin() as db:
            guard(db)
            handle.succeed(db, elapsed_ms=_elapsed(started), summary={"turn_accepted": True})
    except BaseException:
        _unknown(handle, started)
        raise
