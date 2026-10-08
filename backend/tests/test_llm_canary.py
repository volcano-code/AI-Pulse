import json
from types import SimpleNamespace
from scripts.llm_canary import CANARY_TEXT, main, run_canary


def test_canary_requires_explicit_confirmation(capsys):
    assert main([]) == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "explicit_confirmation_required"


def test_canary_rejects_non_live_settings(settings):
    import pytest
    with pytest.raises(ValueError):
        run_canary(settings)


def test_canary_contract_does_not_publish(monkeypatch, settings):
    import scripts.llm_canary as canary
    calls = []
    class FakeClient:
        def __init__(self, config):
            pass
        def draft(self, title, text):
            calls.append((title,text))
            return SimpleNamespace(evidence_quote=CANARY_TEXT[:30]), {
                "model_calls":1,"prompt_tokens":12,"completion_tokens":8
            }
    monkeypatch.setattr(canary, "ModelClient", FakeClient)
    config = settings.model_copy(update={"data_mode":"live","llm_mode":"live","llm_model":"fixture-model"})
    result = run_canary(config)
    assert result["status"] == "passed"
    assert result["quote_exact"] is True
    assert result["draft_requires_review"] is True
    assert result["cost_usd"] is None
    assert calls and calls[0][1] == CANARY_TEXT
