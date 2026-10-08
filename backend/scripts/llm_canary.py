"""One-shot real LLM canary. Never invoked by CI or application startup.

Usage:
  LLM_API_KEY=... LLM_MODEL=... LLM_RESPONSE_FORMAT=json_object python -m scripts.llm_canary --confirm-live-call

Only a fixed non-sensitive source excerpt is sent. No retry, no tools, no email.
"""
from __future__ import annotations
import argparse
import json
import os
import sys

from app.config import Settings
from app.llm import ModelClient, EvidenceError, ModelError

CANARY_TEXT = (
    "The AI Pulse canary checks that a model draft includes an exact evidence quote. "
    "This sentence is synthetic, public test content and contains no personal data."
)


def run_canary(settings: Settings) -> dict:
    if settings.llm_mode != "live" or settings.data_mode != "live":
        raise ValueError("Canary requires explicit live LLM and live data modes")
    draft, usage = ModelClient(settings).draft("AI Pulse synthetic canary", CANARY_TEXT)\n    if draft.evidence_quote not in CANARY_TEXT:\n        raise EvidenceError("Canary quote failed exact verification")
    return {
        "status": "passed",
        "provider_model": settings.llm_model,
        "quote_exact": draft.evidence_quote in CANARY_TEXT,
        "draft_requires_review": True,
        "model_calls": usage["model_calls"],
        "prompt_tokens": usage["prompt_tokens"],
        "completion_tokens": usage["completion_tokens"],
        "cost_usd": None,
        "note": "One paid model call may occur; no automatic retry. No generated draft is published.",
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-live-call", action="store_true")
    args = parser.parse_args(argv)
    if not args.confirm_live_call:
        print(json.dumps({"status":"blocked","reason":"explicit_confirmation_required"}))
        return 2
    try:
        settings = Settings(_env_file=None, data_mode="live", llm_mode="live")
        result = run_canary(settings)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ValueError, ModelError, EvidenceError):
        print(json.dumps({"status":"failed","reason":"model_or_evidence_validation_failed",
                          "billing":"unknown_if_request_started"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
