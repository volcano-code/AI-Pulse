"""Explicit one-call canary with a durable operation ID and no automatic retry.

The input is fixed public synthetic test text, never mixed into the live corpus.
Run Alembic migrations first. Reuse --operation-id to inspect the same attempt.
"""
from __future__ import annotations
import argparse
import json
import time
from urllib.parse import urlsplit
from app.config import Settings
from app.db import make_database
from app.llm import ModelClient, EvidenceError, PROMPT_VERSION, SYSTEM
from app.provider_calls import CallLedger, fingerprint

CANARY_TEXT = (
    "The AI Pulse canary checks that a model draft includes an exact evidence quote. "
    "This sentence is synthetic, public test content and contains no personal data."
)


def run_canary(settings: Settings, *, operation_id: str, confirm_live_call: bool = False,
               transport=None, retry_authorization=None) -> dict:
    if not confirm_live_call:
        raise ValueError("Explicit live call confirmation required")
    if settings.llm_mode != "live" or settings.data_mode != "live":
        raise ValueError("Canary requires explicit live LLM and live data modes")
    url = urlsplit(settings.llm_base_url)
    if (not settings.llm_api_key or not settings.llm_model or not operation_id.strip()
            or len(operation_id) > 128 or url.scheme != "https" or not url.hostname
            or url.username or url.password or url.query or url.fragment):
        raise ValueError("Invalid canary identity/configuration")
    engine, factory = make_database(settings)
    try:
        identity = fingerprint([settings.llm_base_url.rstrip("/"), settings.llm_model,
            settings.llm_response_format, settings.llm_max_output_tokens, PROMPT_VERSION,
            SYSTEM, CANARY_TEXT, "AI Pulse synthetic canary"])
        call = CallLedger(factory).reserve(operation_key="llm-canary:" + operation_id,
            request_hash=identity, kind="llm_canary", provider="fixture" if transport is not None else "openai-compatible",
            model=settings.llm_model, data_mode="canary", retry_authorization=retry_authorization)
        if call.reused:
            saved = call.read()
            return {"status": "passed" if saved["status"] == "succeeded" else "needs_attention",
                    "reused": True, "model_calls": 0, "receipt": saved,
                    "input_kind": "public_synthetic_canary", "draft_requires_review": True}
        started = time.monotonic()
        try:
            client = ModelClient(settings, transport=transport, receipt_observer=call.observe)
            draft, usage = client.draft("AI Pulse synthetic canary", CANARY_TEXT)
            if draft.evidence_quote not in CANARY_TEXT:
                raise EvidenceError("Canary quote failed exact verification")
            with factory.begin() as db:
                call.succeed(db, elapsed_ms=round((time.monotonic()-started)*1000),
                             summary={"quote_exact": True, "draft_requires_review": True})
        except Exception:
            call.unknown(elapsed_ms=round((time.monotonic()-started)*1000))
            return {"status": "needs_attention", "model_calls": None, "dispatch_attempts": 1, "receipt": call.read(),
                    "input_kind": "public_synthetic_canary", "draft_requires_review": True}
        return {"status": "passed", "provider_model": settings.llm_model,
                "quote_exact": True, "draft_requires_review": True,
                "model_calls": 1, "prompt_tokens": usage["prompt_tokens"],
                "completion_tokens": usage["completion_tokens"], "cost_usd": None,
                "receipt": call.read(), "input_kind": "public_synthetic_canary"}
    finally:
        engine.dispose()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-live-call", action="store_true")
    parser.add_argument("--operation-id")
    parser.add_argument("--retry-authorization")
    args = parser.parse_args(argv)
    if not args.confirm_live_call or not args.operation_id:
        print(json.dumps({"status": "blocked", "reason": "explicit_confirmation_required"}))
        return 2
    try:
        settings = Settings(_env_file=None, data_mode="live", llm_mode="live")
        result = run_canary(settings, operation_id=args.operation_id, confirm_live_call=True, retry_authorization=args.retry_authorization)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "passed" else 1
    except Exception:
        print(json.dumps({"status": "failed", "reason": "canary_validation_or_audit_failed",
                          "billing": "inspect_persisted_receipt_before_any_retry"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
