"""Crash/restart contracts using fixture vectors, never paid API calls."""
from dataclasses import replace
import pytest
from sqlalchemy import func, select
from app.durable_backfill import durable_backfill
from app.embedding_gateway import EmbeddingGateway, FixtureEmbeddingProvider
from app.models import EvidenceChunk, ProviderCall
from app.provider_calls import CallLedger


class Spy(FixtureEmbeddingProvider):
    def __init__(self, fail_at=None):
        super().__init__(8)
        self.calls, self.fail_at = 0, fail_at
    def embed(self, texts, *, purpose):
        self.calls += 1
        if self.calls == self.fail_at:
            raise TimeoutError("DO-NOT-LOG-SECRET")
        value = super().embed(texts, purpose=purpose)
        return replace(value, input_tokens=11, request_id=f"req_fixture_{self.calls}")


def embedded(factory):
    with factory() as db:
        return db.scalar(select(func.count()).select_from(EvidenceChunk)
                         .where(EvidenceChunk.embedding_json.is_not(None)))


def run(factory, spy, **kwargs):
    return durable_backfill(factory, EmbeddingGateway(spy), data_mode="replay", **kwargs)


def test_prior_batches_survive_later_timeout_and_restart_is_blocked(seeded):
    f = seeded.app.state.session_factory
    spy = Spy(fail_at=2)
    first = run(f, spy, batch_size=2, max_chunks=4)
    assert first["status"] == "needs_attention" and first["embedded"] == 2
    assert embedded(f) == 2 and spy.calls == 2
    assert [c["status"] for c in first["calls"]] == ["succeeded", "unknown"]
    assert first["input_tokens"] is None and "DO-NOT-LOG" not in str(first)
    # A new provider instance AND a different batch size cannot evade the guard.
    restarted = Spy()
    again = run(f, restarted, batch_size=1, max_chunks=4)
    assert again["status"] == "needs_attention" and restarted.calls == 0
    assert embedded(f) == 2


def test_successful_restart_skips_saved_vectors_and_finishes_remaining(seeded):
    f = seeded.app.state.session_factory
    one = Spy()
    first = run(f, one, batch_size=2, max_chunks=2)
    assert first["status"] == "applied" and first["embedded"] == 2 and not first["complete"]
    next_spy = Spy()
    second = run(f, next_spy, batch_size=1, max_chunks=100)
    assert second["status"] == "applied" and second["complete"]
    assert second["embedded"] > 0
    last = Spy()
    assert run(f, last)["embedded"] == 0 and last.calls == 0


def test_vector_write_failure_rolls_back_only_current_batch(seeded, monkeypatch):
    import app.durable_backfill as module
    f = seeded.app.state.session_factory
    original = module.write_embedding
    writes = 0
    def fail_fourth(*args, **kwargs):
        nonlocal writes
        writes += 1
        original(*args, **kwargs)
        if writes == 4: raise RuntimeError("simulated write failure after remote response")
    monkeypatch.setattr(module, "write_embedding", fail_fourth)
    spy = Spy()
    report = run(f, spy, batch_size=2, max_chunks=4)
    assert report["status"] == "needs_attention" and embedded(f) == 2
    saved = report["calls"][-1]
    assert saved["status"] == "unknown" and saved["input_tokens"] == 11
    assert saved["request_id"] == "req_fixture_2"


def test_invalid_vectors_keep_usage_and_block_retry(seeded):
    class Bad(Spy):
        def embed(self, texts, *, purpose):
            value = super().embed(texts, purpose=purpose)
            return replace(value, vectors=[[float("nan")] * 8 for _ in texts])
    f = seeded.app.state.session_factory
    report = run(f, Bad(), max_chunks=1)
    assert report["status"] == "needs_attention" and embedded(f) == 0
    assert report["calls"][0]["input_tokens"] == 11
    assert report["calls"][0]["status"] == "unknown"


def test_process_death_leaves_non_expiring_dispatch_intent(seeded):
    class Killed(Spy):
        def embed(self, texts, *, purpose): raise SystemExit("simulated process exit")
    f = seeded.app.state.session_factory
    with pytest.raises(SystemExit): run(f, Killed(), max_chunks=1)
    assert CallLedger(f).inspect()[0]["status"] == "in_flight"
    replacement = Spy()
    assert run(f, replacement, max_chunks=1)["status"] == "needs_attention"
    assert replacement.calls == 0


def test_wrong_mode_and_missing_authorization_never_dispatch(seeded):
    f = seeded.app.state.session_factory
    class Paid(Spy): provider = "openai"
    spy = Paid()
    with pytest.raises(ValueError, match="authorization"):
        durable_backfill(f, EmbeddingGateway(spy), data_mode="live")
    assert spy.calls == 0
    with pytest.raises(ValueError, match="mode mismatch"):
        durable_backfill(f, EmbeddingGateway(Spy()), data_mode="live")


def test_provider_failure_does_not_erase_all_batch_audit(seeded):
    f = seeded.app.state.session_factory
    run(f, Spy(fail_at=2), max_chunks=6, batch_size=1)
    with f() as db:
        assert db.scalar(select(func.count()).select_from(ProviderCall)) == 2


def test_cli_dry_run_leaves_call_ledger_empty(seeded):
    from scripts.embedding_backfill import run as cli_run
    s = seeded.app.state.settings.model_copy(update={"embedding_provider":"fixture",
        "embedding_model":"fixture-sha256-v1", "embedding_dim":8})
    result = cli_run(s, apply=False, confirm_provider=None, allow_paid_api=False, max_chunks=2, batch_size=1)
    assert result["status"] == "dry_run"
    assert CallLedger(seeded.app.state.session_factory).inspect() == []
    written = cli_run(s, apply=True, confirm_provider="fixture", allow_paid_api=False, max_chunks=2, batch_size=1)
    assert written["status"] == "applied" and written["batches_completed"] == 2
    assert len(CallLedger(seeded.app.state.session_factory).inspect()) == 2
