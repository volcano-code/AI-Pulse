"""Mock-transport receipt ordering, independent of live API access."""
import httpx
import pytest
from app.embedding_gateway import EmbeddingGateway
from app.openai_embedding_provider import OpenAIEmbeddingProvider
from app.provider_calls import CallLedger, fingerprint


@pytest.mark.parametrize("status,body", [
    (429, {"error": "DO-NOT-STORE-SECRET"}),
    (200, {"data": [], "usage": {"prompt_tokens": 9}}),
])
def test_embedding_headers_and_usage_survive_invalid_result(client, status, body):
    call = CallLedger(client.app.state.session_factory).reserve(
        operation_key="offline-transport", request_hash=fingerprint("fixture"),
        kind="embedding_backfill", provider="fixture", model="fixture", data_mode="replay")
    adapter = OpenAIEmbeddingProvider(api_key="fake-key-not-real", model="fixture", dimensions=3,
        transport=httpx.MockTransport(lambda _: httpx.Response(status,
            headers={"x-request-id": "req_fixture_header"}, json=body)))
    with pytest.raises((ValueError, RuntimeError)):
        EmbeddingGateway(adapter).embed_documents(["public fixture"], receipt_observer=call.observe)
    call.unknown(elapsed_ms=1)
    saved = call.read()
    assert saved["request_id"] == "req_fixture_header" and saved["http_status"] == status
    assert saved["input_tokens"] == (9 if status == 200 else None)
    assert "DO-NOT-STORE" not in str(saved)
