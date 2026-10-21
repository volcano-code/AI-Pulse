"""HTTP mock contracts for the canary; not real model acceptance."""
import json
import httpx
import pytest
from sqlalchemy import func, select
from app.models import Brief, ProviderCall
from app.provider_calls import CallConflict
from scripts.llm_canary import CANARY_TEXT, main, run_canary


def config(client):
    return client.app.state.settings.model_copy(update={"data_mode":"live", "llm_mode":"live",
        "llm_model":"fixture-model", "llm_api_key":"TEST-SECRET-NOT-REAL"})


def reply(quote=CANARY_TEXT[:60], tokens=12):
    return httpx.Response(200, headers={"x-request-id":"req_fixture_llm"}, json={
        "choices":[{"finish_reason":"stop", "message":{"content":json.dumps({
            "title":"离线契约测试", "summary":"公开测试文本的摘录草稿。", "evidence_quote":quote})}}],
        "usage":{"prompt_tokens":tokens, "completion_tokens":8}})


def test_canary_requires_explicit_confirmation(capsys):
    assert main([]) == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "explicit_confirmation_required"
    assert main(["--confirm-live-call"]) == 2


def test_canary_rejects_non_live_settings(settings):
    with pytest.raises(ValueError):
        run_canary(settings, operation_id="contract", confirm_live_call=True)


def test_canary_contract_does_not_publish_and_same_id_does_not_dispatch_twice(client):
    calls = []
    def handler(request):
        calls.append(request)
        return reply()
    transport = httpx.MockTransport(handler)
    first = run_canary(config(client), operation_id="contract", confirm_live_call=True, transport=transport)
    assert first["status"] == "passed" and first["quote_exact"] and first["draft_requires_review"]
    assert first["receipt"]["request_id"] == "req_fixture_llm"
    assert first["receipt"]["input_tokens"] == 12 and first["cost_usd"] is None
    second = run_canary(config(client), operation_id="contract", confirm_live_call=True, transport=transport)
    assert second["reused"] and second["model_calls"] == 0 and len(calls) == 1
    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Brief)) == 0
        assert db.scalar(select(ProviderCall)).data_mode == "canary"


def test_timeout_creates_unknown_receipt_and_blocks_restart(client):
    calls = []
    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("TEST-SECRET-NOT-REAL")
    args = dict(operation_id="timeout", confirm_live_call=True, transport=httpx.MockTransport(handler))
    first = run_canary(config(client), **args)
    assert first["status"] == "needs_attention"
    assert first["receipt"]["status"] == "unknown" and first["receipt"]["input_tokens"] is None
    assert "TEST-SECRET" not in json.dumps(first)
    second = run_canary(config(client), **args)
    assert second["model_calls"] == 0 and len(calls) == 1


def test_bad_quote_preserves_provider_usage_even_when_validation_fails(client):
    result = run_canary(config(client), operation_id="bad-quote", confirm_live_call=True,
        transport=httpx.MockTransport(lambda _: reply("THIS IS NOT THE PROVIDED SOURCE TEXT")))
    assert result["status"] == "needs_attention"
    assert result["receipt"]["response_received"] and result["receipt"]["http_status"] == 200
    assert result["receipt"]["input_tokens"] == 12 and result["receipt"]["request_id"] == "req_fixture_llm"


def test_key_conflict_blocks_changed_model_before_dispatch(client):
    called = []
    transport = httpx.MockTransport(lambda req: (called.append(req), reply())[1])
    run_canary(config(client), operation_id="same", confirm_live_call=True, transport=transport)
    with pytest.raises(CallConflict):
        run_canary(config(client).model_copy(update={"llm_model":"changed"}),
                   operation_id="same", confirm_live_call=True, transport=transport)
    assert len(called) == 1


def test_missing_ledger_migration_fails_before_network(settings):
    called = []
    s = settings.model_copy(update={"data_mode":"live","llm_mode":"live",
                                   "llm_model":"m","llm_api_key":"not-real"})
    with pytest.raises(Exception):
        run_canary(s, operation_id="missing", confirm_live_call=True,
                   transport=httpx.MockTransport(lambda req: (called.append(req), reply())[1]))
    assert called == []
