import json
import httpx
import pytest
from app.model_gateway import GatewayError, post_chat_completion, safe_usage


def test_gateway_transport_contract():
    def handler(request):
        assert request.url.path.endswith("/chat/completions")
        assert request.headers["authorization"] == "Bearer test-secret"
        assert json.loads(request.content)["model"] == "fixture-model"
        return httpx.Response(200, json={"choices":[{"message":{"content":"{}"},"finish_reason":"stop"}],
                                          "usage":{"prompt_tokens":5,"completion_tokens":2}})
    result=post_chat_completion(base_url="https://api.example.test/v1",api_key="test-secret",
        payload={"model":"fixture-model","messages":[]},timeout=2,
        transport=httpx.MockTransport(handler))
    assert safe_usage(result)=={"prompt_tokens":5,"completion_tokens":2}


def test_gateway_redacts_provider_error():
    def handler(request):
        return httpx.Response(401,text="test-secret sensitive user content")
    with pytest.raises(GatewayError) as exc:
        post_chat_completion(base_url="https://api.example.test/v1",api_key="test-secret",
            payload={"model":"fixture-model"},timeout=2,transport=httpx.MockTransport(handler))
    assert "test-secret" not in str(exc.value)
    assert "sensitive user content" not in str(exc.value)


@pytest.mark.parametrize("url",["http://localhost","https://user:pass@example.test","https://example.test?token=abc"])
def test_gateway_rejects_invalid_base_url(url):
    with pytest.raises(GatewayError):
        post_chat_completion(base_url=url,api_key="secret",payload={"model":"m"},timeout=2)


def test_gateway_rejects_oversized_response():
    def handler(request):
        return httpx.Response(200,content=b"x"*500)
    with pytest.raises(GatewayError,match="byte budget"):
        post_chat_completion(base_url="https://api.example.test",api_key="secret",
            payload={"model":"m"},timeout=2,max_response_bytes=100,
            transport=httpx.MockTransport(handler))


def test_usage_does_not_invent_missing_or_invalid_tokens():
    assert safe_usage({})=={"prompt_tokens":None,"completion_tokens":None}
    assert safe_usage({"usage":{"prompt_tokens":True,"completion_tokens":-2}})=={"prompt_tokens":None,"completion_tokens":None}
