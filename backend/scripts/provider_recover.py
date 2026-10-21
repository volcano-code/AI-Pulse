"""One explicitly authorized query/draft retry; never publish or resume research.

Query input is read from stdin, not argv. Canary/backfill have their own bounded
CLIs with --retry-authorization. Keep all original provider/data configuration.
"""
import argparse
import json
import sys
from app.config import Settings
from app.db import make_database
from app.models import Snapshot
from app.audited_models import draft_brief_item
from app.query_embeddings import embed_saved_query
from app.research_retrieval import make_embedding_gateway
from app.provider_reconciliation import inspect_call
from app.provider_calls import CallHandle


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=["query", "brief"])
    parser.add_argument("--authorization-id", required=True)
    parser.add_argument("--call-id", required=True)
    parser.add_argument("--snapshot-id")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-paid-call", action="store_true")
    args = parser.parse_args(argv)
    engine = None
    try:
        settings = Settings()
        engine, factory = make_database(settings)
        info = inspect_call(factory, args.call_id)
        d = info["reconciliation"]
        expected_kind = "query_embedding" if args.kind == "query" else "brief_draft"
        if (not d or d["id"] != args.authorization_id or d["decision"] != "authorize_retry"
                or info["receipt"]["kind"] != expected_kind):
            raise ValueError("Authorization does not match the requested recovery")
        if not args.apply:
            print(json.dumps({"status": "dry_run", "decision": d, "provider_calls": 0}))
            return 0
        if not args.confirm_paid_call:
            raise ValueError("Explicit execution confirmation required")
        if args.kind == "query":
            raw = sys.stdin.buffer.read(1601)
            if len(raw) > 1600:
                raise ValueError("Query input too large")
            query = raw.decode("utf-8").rstrip("\r\n")
            _, audit = embed_saved_query(factory, settings, query, make_embedding_gateway(settings),
                                         retry_authorization=args.authorization_id)
            result = {"status": "succeeded", "audit": audit}
        else:
            with factory() as db:
                snap = db.get(Snapshot, args.snapshot_id)
                if snap is None:
                    raise ValueError("Snapshot required")
            _, usage = draft_brief_item(factory, settings, snap.id, snap.title, snap.text,
                                        retry_authorization=args.authorization_id)
            result = {"status": "needs_review", "usage": usage,
                      "receipt": CallHandle(factory, usage["provider_call_id"], True).read()}
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception:
        print(json.dumps({"status": "needs_attention", "reason": "recovery_blocked_or_unconfirmed",
                          "billing": "inspect_persisted_receipt_before_any_retry"}))
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
