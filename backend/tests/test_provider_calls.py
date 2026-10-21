"""Offline dispatch/audit contracts; no real paid provider traffic."""
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pytest
from sqlalchemy import func, select
from app.models import ProviderCall, ProviderCallResource
from app.provider_calls import CallBlocked, CallConflict, CallLedger, fingerprint, safe_count


def reserve(factory, key="test-operation", payload="public-fixture", resources=None):
    return CallLedger(factory).reserve(operation_key=key, request_hash=fingerprint(payload),
        kind="embedding_backfill", provider="fixture", model="fixture-model",
        data_mode="replay", resource_keys=resources)


def test_intent_is_committed_before_dispatch_and_survives_restart(client):
    factory = client.app.state.session_factory
    one = reserve(factory)
    with factory() as db:
        assert db.get(ProviderCall, one.id).status == "in_flight"
    # A newly constructed journal represents process restart: NEVER retry it.
    two = reserve(factory)
    assert two.id == one.id and two.reused
    with pytest.raises(CallBlocked): two.require_fresh()
    assert two.read()["automatic_retry_allowed"] is False


def test_key_reuse_with_different_request_is_rejected(client):
    factory = client.app.state.session_factory
    reserve(factory)
    with pytest.raises(CallConflict): reserve(factory, payload="different payload")


def test_unique_resource_blocks_different_batch_or_operation(client):
    factory = client.app.state.session_factory
    key = fingerprint("immutable snapshot and offsets")
    one = reserve(factory, resources=[key])
    with pytest.raises(CallBlocked) as exc:
        reserve(factory, key="other-operation", resources=[key])
    assert exc.value.call_id == one.id
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(ProviderCall)) == 1
        assert db.scalar(select(func.count()).select_from(ProviderCallResource)) == 1


def test_concurrent_reservation_has_exactly_one_dispatch_owner(client):
    factory = client.app.state.session_factory
    barrier = Barrier(2)
    def attempt(_):
        barrier.wait()
        return reserve(factory)
    with ThreadPoolExecutor(max_workers=2) as executor:
        rows = list(executor.map(attempt, range(2)))
    assert len({r.id for r in rows}) == 1
    assert sorted(r.reused for r in rows) == [False, True]


def test_unknown_preserves_receipt_and_never_auto_retries(client):
    factory = client.app.state.session_factory
    call = reserve(factory)
    call.observe(request_id="req_offline_1", http_status=200, input_tokens=13, output_tokens=5)
    call.unknown(elapsed_ms=42)
    row = call.read()
    assert row["status"] == "unknown" and row["request_id"] == "req_offline_1"
    assert row["input_tokens"] == 13 and row["output_tokens"] == 5
    with pytest.raises(CallBlocked): reserve(factory).require_fresh()


def test_success_and_receipt_commit_atomically(client):
    factory = client.app.state.session_factory
    call = reserve(factory)
    with pytest.raises(RuntimeError):
        with factory.begin() as db:
            call.succeed(db, elapsed_ms=1, summary={"embedded": 1})
            raise RuntimeError("simulated local commit failure")
    assert call.read()["status"] == "in_flight"
    with factory.begin() as db:
        call.succeed(db, elapsed_ms=2, summary={"embedded": 1})
    call.unknown(elapsed_ms=3)  # Lost commit acknowledgement must not undo success.
    assert call.read()["status"] == "succeeded"


def test_receipts_do_not_store_prompts_operation_ids_or_bad_usage(client):
    factory = client.app.state.session_factory
    call = reserve(factory, key="DO-NOT-STORE-OPERATOR-INPUT", payload="PRIVATE SOURCE BODY")
    call.observe(request_id="malicious\nheader", input_tokens=True, output_tokens=-1)
    output = json.dumps(CallLedger(factory).inspect())
    assert "PRIVATE SOURCE BODY" not in output and "DO-NOT-STORE" not in output
    assert "malicious" not in output
    assert call.read()["input_tokens"] is None and call.read()["output_tokens"] is None
    assert call.read()["cost_usd"] is None


@pytest.mark.parametrize("value", [None, True, -1, 1.2, "12", 2**40])
def test_invalid_usage_is_unknown_not_zero(value):
    assert safe_count(value) is None
