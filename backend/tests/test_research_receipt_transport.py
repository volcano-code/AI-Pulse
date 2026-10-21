"""Internal receipt-pipe contracts; no request to a real provider."""
import json
import subprocess
import sys
from types import SimpleNamespace
import httpx
import pytest
from app.llm import ModelError
from app.research_model import ResearchModelClient, read_receipt_stream, request_turn


def frame(event, **data):
    return (json.dumps({"event": event, **data}) + "\n").encode()


def config(settings):
    return settings.model_copy(update={"llm_api_key":"DO-NOT-LEAK", "llm_model":"mock-only"})


def test_receipt_is_available_when_model_schema_is_rejected(settings):
    observed = []
    def handler(request):
        return httpx.Response(200, headers={"x-request-id":"req-fixture-1"}, json={
            "choices":[{"finish_reason":"length","message":{"content":"invalid"}}],
            "usage":{"prompt_tokens":17,"completion_tokens":3}})
    with pytest.raises(ModelError):
        request_turn("https://example.test/v1","DO-NOT-LEAK","mock",[],30,1,
                     httpx.MockTransport(handler), lambda **v: observed.append(v))
    assert observed[0] == {"request_id":"req-fixture-1","http_status":200}
    assert observed[1] == {"input_tokens":17,"output_tokens":3}


def test_partial_child_receipt_is_retained_after_actual_process_timeout(settings, monkeypatch):
    original = subprocess.run
    observed = []
    code = ('import sys,time;print(\'{"event":"receipt","receipt":'
            '{"request_id":"req-child-fixture","http_status":200}}\',flush=True);time.sleep(5)')
    def run(cmd, **kwargs):
        # A real, isolated process is killed/reaped by the actual subprocess timeout.
        return original([sys.executable, "-c", code], **kwargs)
    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(ModelError, match="deadline"):
        ResearchModelClient(config(settings)).next_turn([], 0.2, lambda **v: observed.append(v))
    assert len(observed) == 1 and observed[0]["request_id"] == "req-child-fixture"
    assert observed[0]["http_status"] == 200


def test_failed_child_retains_receipt_but_does_not_return_result(settings, monkeypatch):
    observed=[]
    output=frame("receipt",receipt={"request_id":"req-failed","http_status":429})
    monkeypatch.setattr(subprocess,"run",lambda *a,**k:SimpleNamespace(returncode=2,stdout=output))
    with pytest.raises(ModelError,match="Isolated"):
        ResearchModelClient(config(settings)).next_turn([],1,lambda **v:observed.append(v))
    assert observed[0]["http_status"] == 429


def test_successful_child_result_and_metadata_are_separate(settings, monkeypatch):
    output=frame("receipt",receipt={"input_tokens":7,"output_tokens":4})
    output+=frame("result",result={"message":{"role":"assistant","content":"{}"},"usage":{}})
    monkeypatch.setattr(subprocess,"run",lambda *a,**k:SimpleNamespace(returncode=0,stdout=output))
    observed=[]
    result=ResearchModelClient(config(settings)).next_turn([],1,lambda **v:observed.append(v))
    assert result["message"]["content"] == "{}" and observed[0]["input_tokens"] == 7


def test_incomplete_last_frame_is_not_a_success():
    assert read_receipt_stream(b'{"event":"result"', require_result=False) is None
    with pytest.raises(ModelError,match="Missing"):
        read_receipt_stream(b'{"event":"result"')


def test_duplicate_or_oversized_pipe_records_fail_closed():
    result=frame("result",result={"message":{}})
    with pytest.raises(ModelError,match="Duplicate"):
        read_receipt_stream(result+result)
    with pytest.raises(ModelError): read_receipt_stream(b"x"*1_020_001)
    with pytest.raises(ModelError): read_receipt_stream(b"{}\n"*6)


def test_child_receipt_never_forwards_arbitrary_fields():
    seen=[]
    read_receipt_stream(frame("receipt",receipt={"request_id":"req-ok","prompt":"PRIVATE"}),
                        lambda **v:seen.append(v),require_result=False)
    assert "prompt" not in seen[0] and "PRIVATE" not in str(seen)
