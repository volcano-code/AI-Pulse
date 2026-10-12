import httpx
import pytest
from app.embedding_gateway import EmbeddingGateway
from app.openai_embedding_provider import OpenAIEmbeddingProvider


def provider(handler):
    return OpenAIEmbeddingProvider(
        api_key="test-secret", model="text-embedding-3-small", dimensions=3,
        transport=httpx.MockTransport(handler),
    )


def test_openai_embedding_adapter_contract_and_usage():
    def handler(request):
        assert request.headers["authorization"] == "Bearer test-secret"
        body = __import__("json").loads(request.content)
        assert body["dimensions"] == 3 and body["encoding_format"] == "float"
        return httpx.Response(200, headers={"x-request-id":"req_fixture"}, json={
            "object":"list","model":"text-embedding-3-small",
            "data":[
                {"object":"embedding","index":1,"embedding":[0.0,1.0,0.0]},
                {"object":"embedding","index":0,"embedding":[1.0,0.0,0.0]},
            ],
            "usage":{"prompt_tokens":7,"total_tokens":7},
        })
    batch=EmbeddingGateway(provider(handler)).embed_documents(["one","two"])
    assert batch.vectors == [[1.0,0.0,0.0],[0.0,1.0,0.0]]
    assert batch.provider == "openai" and batch.input_tokens == 7
    assert batch.request_id == "req_fixture"


def test_openai_embedding_adapter_redacts_error_body():
    def handler(request):
        return httpx.Response(429, headers={"x-request-id":"req_rate"}, text="secret echoed input")
    with pytest.raises(RuntimeError) as exc:
        EmbeddingGateway(provider(handler)).embed_query("sensitive query")
    message=str(exc.value)
    assert "429" in message and "req_rate" in message
    assert "sensitive" not in message and "secret echoed" not in message


def test_openai_embedding_adapter_rejects_malformed_vector_count():
    def handler(request):
        return httpx.Response(200,json={"model":"text-embedding-3-small","data":[],"usage":{}})
    with pytest.raises(ValueError):
        EmbeddingGateway(provider(handler)).embed_query("one")


@pytest.mark.parametrize("url", [
    "http://api.openai.com/v1",
    "https://user:pass@api.openai.com/v1",
    "https://api.openai.com/v1?token=secret",
    "https://api.openai.com:444/v1",
])
def test_embedding_transport_rejects_unsafe_urls(url):
    with pytest.raises(ValueError):
        OpenAIEmbeddingProvider(api_key="secret", model="m", dimensions=3, base_url=url)


def test_embedding_transport_rejects_unapproved_host():
    with pytest.raises(ValueError, match="not allowed"):
        OpenAIEmbeddingProvider(api_key="secret", model="m", dimensions=3,
                                base_url="https://localhost/v1")


def test_embedding_transport_rejects_redirect_without_following():
    calls = []
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(307, headers={"location":"https://evil.example/collect"})
    with pytest.raises(RuntimeError, match="307"):
        EmbeddingGateway(provider(handler)).embed_query("private text")
    assert len(calls) == 1


def test_embedding_transport_rejects_oversized_response():
    def handler(request):
        return httpx.Response(200, content=b"x" * 2_000_001)
    with pytest.raises(RuntimeError, match="byte budget"):
        EmbeddingGateway(provider(handler)).embed_query("private text")
