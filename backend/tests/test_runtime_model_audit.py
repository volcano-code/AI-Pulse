"""Runtime audit contracts only: synthetic corpus, no paid traffic or SMTP."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
import threading
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.audited_models import draft_brief_item
from app.briefing import build_brief, choose_candidates
from app.llm import EvidenceError, ModelError
from app.models import Brief, BriefModelDraft, Investigation, ProviderCall, Snapshot, Workspace
from app.provider_calls import CallBlocked, CallHandle, CallLedger, fingerprint
from app.research import execute_research, recover_interrupted_research, ResearchConflict
from app.schemas import Preferences, ResearchRequest
from app.timeutil import utcnow


class Drafts:
    def __init__(self, fail_at=None):
        self.calls = 0
        self.fail_at = fail_at
    def draft(self, title, text):
        self.calls += 1
        if self.calls == self.fail_at:
            raise TimeoutError("DO-NOT-STORE-SECRET")
        return SimpleNamespace(title=title, summary="来源摘录草稿，待人工复核。", evidence_quote=text[:60]), {
            "model_calls": 1, "prompt_tokens": 12, "completion_tokens": 7,
        }


@pytest.fixture
def env(seeded):
    factory = seeded.app.state.session_factory
    settings = seeded.app.state.settings.model_copy(update={
        "llm_mode": "live", "llm_model": "runtime-fixture", "llm_api_key": "DO-NOT-STORE-SECRET",
    })
    with factory() as db:
        snap = db.scalars(select(Snapshot).order_by(Snapshot.id)).first()
    return factory, settings, snap


def draft(env, client, **kwargs):
    f, s, snap = env
    return draft_brief_item(f, s, snap.id, snap.title, snap.text, model_client=client, **kwargs)


def test_brief_intent_precedes_call_and_atomic_draft_can_be_reused(env):
    f, s, snap = env
    class AssertIntent(Drafts):
        def draft(self, title, text):
            with f() as db:
                row = db.scalars(select(ProviderCall)).one()
                assert row.status == "in_flight" and row.kind == "brief_draft"
                assert row.provider == "fixture"
                assert db.scalar(select(func.count()).select_from(BriefModelDraft)) == 0
            return super().draft(title, text)
    provider = AssertIntent()
    one, usage = draft(env, provider)
    two, again = draft(env, provider)
    assert provider.calls == 1 and one == two and again["reused"]
    assert usage["model_calls"] == 1 and again["model_calls"] == 0
    assert again["prompt_tokens"] == 0 and again["provider_call_id"] == usage["provider_call_id"]
    receipt = CallLedger(f).inspect()[0]
    assert receipt["status"] == "succeeded" and receipt["input_tokens"] == 12
    assert receipt["summary"]["draft_requires_review"] is True
    assert snap.text not in json.dumps(receipt) and "DO-NOT-STORE" not in json.dumps(receipt)


def test_later_item_timeout_preserves_first_draft_and_restart_does_not_resend(env):
    f, s, _ = env
    first = Drafts(fail_at=2)
    with pytest.raises(TimeoutError):
        build_brief(f, s, model_client=first)
    with f() as db:
        assert db.scalar(select(func.count()).select_from(BriefModelDraft)) == 1
        assert db.scalar(select(func.count()).select_from(Brief)) == 0
    assert sorted(x["status"] for x in CallLedger(f).inspect()) == ["succeeded", "unknown"]
    second = Drafts()
    # A later cutoff and a new client cannot bypass the same-snapshot reservation.
    with pytest.raises(CallBlocked):
        build_brief(f, s, model_client=second, cutoff=utcnow() + timedelta(minutes=1))
    assert second.calls == 0


def test_failed_final_edition_commit_reuses_all_validated_drafts(env):
    f, s, _ = env
    with f() as db:
        count = len(choose_candidates(db, Preferences(), utcnow(), s.data_mode))
    def guard(db):
        if db.scalar(select(func.count()).select_from(BriefModelDraft)) == count:
            raise RuntimeError("final edition failure injection")
    first = Drafts()
    with pytest.raises(RuntimeError):
        build_brief(f, s, model_client=first, commit_guard=guard)
    assert first.calls == count
    second = Drafts()
    result = build_brief(f, s, model_client=second)
    with f() as db:
        brief = db.get(Brief, result["brief_id"])
        assert brief.status == "needs_review"
        assert brief.usage["model_calls"] == 0 and brief.usage["reused_model_results"] == count
        assert len(brief.usage["provider_call_ids"]) == count
        assert brief.usage["prompt_tokens"] == 0
    assert second.calls == 0


def test_brief_write_rollback_does_not_publish_or_allow_retry(env, monkeypatch):
    original = CallHandle.succeed
    def fail(self, db, **kwargs):
        original(self, db, **kwargs)
        raise RuntimeError("injected before commit")
    monkeypatch.setattr(CallHandle, "succeed", fail)
    provider = Drafts()
    with pytest.raises(RuntimeError): draft(env, provider)
    with env[0]() as db:
        assert db.scalar(select(func.count()).select_from(BriefModelDraft)) == 0
    assert CallLedger(env[0]).inspect()[0]["status"] == "unknown"
    with pytest.raises(CallBlocked): draft(env, provider)
    assert provider.calls == 1


def test_concurrent_same_snapshot_has_single_dispatch(env):
    entered, release = threading.Event(), threading.Event()
    class Slow(Drafts):
        def draft(self, title, text):
            entered.set()
            assert release.wait(5)
            return super().draft(title, text)
    one, two = Slow(), Drafts()
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(draft, env, one)
        try:
            assert entered.wait(5)
            with pytest.raises(CallBlocked): draft(env, two)
        finally: release.set()
        future.result(timeout=5)
    assert one.calls == 1 and two.calls == 0


def test_bad_quote_keeps_usage_but_no_draft(env):
    class Bad(Drafts):
        def draft(self, title, text):
            value, usage = super().draft(title, text)
            value.evidence_quote = "This sentence does not exist in the saved source."
            return value, usage
    with pytest.raises(EvidenceError): draft(env, Bad())
    receipt = CallLedger(env[0]).inspect()[0]
    assert receipt["status"] == "unknown" and receipt["input_tokens"] == 12
    with env[0]() as db:
        assert db.scalar(select(func.count()).select_from(BriefModelDraft)) == 0


def test_changed_model_parameters_do_not_silently_reuse_draft(env):
    f, s, snap = env
    one = Drafts(); draft(env, one)
    changed = (f, s.model_copy(update={"llm_max_output_tokens": s.llm_max_output_tokens + 1}), snap)
    draft(changed, one)
    assert one.calls == 2


def test_lost_worker_fence_blocks_before_network_or_intent(env):
    provider = Drafts()
    def deny(db): raise RuntimeError("lease lost")
    with pytest.raises(RuntimeError): draft(env, provider, commit_guard=deny)
    assert provider.calls == 0 and CallLedger(env[0]).inspect() == []


def test_replay_workspace_cannot_dispatch_a_default_real_client(env):
    f, s, snap = env
    with pytest.raises(ModelError, match="Explicit live"):
        draft_brief_item(f, s, snap.id, snap.title, snap.text)
    assert CallLedger(f).inspect() == []


def test_snapshot_scope_checked_before_dispatch(env):
    f, s, snap = env
    provider = Drafts()
    with pytest.raises(ModelError, match="scope"):
        draft_brief_item(f, s, snap.id, snap.title, "wrong source", model_client=provider)
    assert provider.calls == 0 and CallLedger(f).inspect() == []


def test_success_without_saved_artifact_never_reissues_request(env):
    provider = Drafts(); _, usage = draft(env, provider)
    with env[0].begin() as db:
        db.delete(db.get(BriefModelDraft, usage["provider_call_id"]))
    with pytest.raises(CallBlocked): draft(env, provider)
    assert provider.calls == 1


class Turns:
    def __init__(self, fail=False): self.calls, self.fail = 0, fail
    def next_turn(self, messages, timeout):
        self.calls += 1
        if self.fail: raise TimeoutError("DO-NOT-STORE-SECRET")
        if self.calls == 1:
            message = {"tool_calls": [{"id": "call1", "type": "function", "function": {
                "name": "search_saved_evidence", "arguments": '{"query":"Agent"}',
            }}]}
        else:
            message = {"content": json.dumps({"claims": [{"text": "测试来源讨论 Agent。", "evidence_ids": ["E1"]}], "insufficient_evidence": False})}
        return {"message": {"role": "assistant", **message}, "usage": {"prompt_tokens": 11, "completion_tokens": 5}}


def test_research_turns_are_linked_and_repeated_request_is_not_redispatched(env):
    f, s, _ = env; model = Turns()
    request = ResearchRequest(question="Agent", idempotency_key="audited-research")
    result = execute_research(f, s, request, model)
    assert result["status"] == "needs_review" and model.calls == 2
    receipts = result["result"]["provider_calls"]
    assert len(receipts) == 2 and all(x["status"] == "succeeded" for x in receipts)
    assert all(x["kind"] == "research_turn" for x in receipts)
    assert result["result"]["usage"]["prompt_tokens"] == 22
    linked = [e["provider_call_id"] for e in result["trace"] if e["stage"] == "model_request"]
    assert linked == [x["call_id"] for x in receipts]
    assert execute_research(f, s, request, model)["cached"] and model.calls == 2
    assert "DO-NOT-STORE" not in json.dumps(result)


def test_research_transport_timeout_is_unknown_not_budget_exhaustion(env):
    f, s, _ = env; model = Turns(fail=True)
    request = ResearchRequest(question="Agent", idempotency_key="timeout")
    result = execute_research(f, s, request, model)
    assert result["status"] == "needs_attention"
    assert result["result"]["stop_reason"] != "budget_exceeded"
    assert result["result"]["provider_calls"][0]["status"] == "unknown"
    assert result["result"]["usage"]["prompt_tokens"] is None
    execute_research(f, s, request, model)
    assert model.calls == 1


def test_research_unknown_second_turn_does_not_report_partial_usage_as_complete(env):
    f, s, _ = env
    class FailsSecond(Turns):
        def next_turn(self, messages, timeout):
            self.fail = self.calls == 1
            return super().next_turn(messages, timeout)
    result = execute_research(f, s, ResearchRequest(question="Agent"), FailsSecond())
    assert result["result"]["usage"]["prompt_tokens"] is None
    assert [r["status"] for r in result["result"]["provider_calls"]] == ["succeeded", "unknown"]
    assert result["result"]["provider_calls"][0]["input_tokens"] == 11


def test_changed_research_configuration_rejects_same_request_id(env):
    f, s, _ = env
    request = ResearchRequest(question="Agent", idempotency_key="config-bound")
    execute_research(f, s, request, Turns())
    with pytest.raises(ResearchConflict):
        execute_research(f, s.model_copy(update={"llm_max_output_tokens": s.llm_max_output_tokens + 1}), request, Turns())


def test_research_restart_closes_inflight_audit_without_replay(env):
    f, s, _ = env
    request = ResearchRequest(question="Agent", idempotency_key="restart")
    result = execute_research(f, s, request, Turns())
    rid = result["id"]
    with f.begin() as db:
        row = db.get(Investigation, rid); row.status = "running"; row.result = {}
        call = db.get(ProviderCall, result["result"]["provider_calls"][-1]["call_id"])
        call.status = "in_flight"; call.finished_at = None
    recover_interrupted_research(f)
    provider = Turns()
    again = execute_research(f, s, request, provider)
    assert again["status"] == "needs_attention" and again["cached"] and provider.calls == 0
    assert len(again["result"]["provider_calls"]) == 2
    assert again["result"]["provider_calls"][-1]["status"] == "unknown"
