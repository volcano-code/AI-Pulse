"""Local operator-only reconciliation. Default read/dry-run; never calls a provider."""
import argparse
import json
from app.config import Settings
from app.db import make_database
from app.provider_reconciliation import inspect_call, reconcile


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--call-id", required=True)
    parser.add_argument("--decision", choices=["keep_blocked", "authorize_retry"])
    parser.add_argument("--billing", choices=["charged", "not_charged", "unknown"])
    parser.add_argument("--expected-fingerprint")
    parser.add_argument("--evidence-sha256")
    parser.add_argument("--actor", help="Operator alias, persisted only as a hash. Never a credential.")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-call-id")
    parser.add_argument("--acknowledge-workers-stopped", action="store_true")
    parser.add_argument("--acknowledge-possible-charge", action="store_true")
    args = parser.parse_args(argv)
    engine = None
    try:
        if args.apply and not args.decision:
            raise ValueError("A decision is required for apply")
        engine, factory = make_database(Settings())
        if args.decision:
            result = reconcile(factory, call_id=args.call_id,
                expected_fingerprint=args.expected_fingerprint, decision=args.decision,
                operator_billing=args.billing, evidence_sha256=args.evidence_sha256,
                actor=args.actor, apply=args.apply, confirm_call_id=args.confirm_call_id,
                workers_stopped=args.acknowledge_workers_stopped,
                acknowledge_possible_charge=args.acknowledge_possible_charge)
        else:
            result = {"status": "read_only", **inspect_call(factory, args.call_id)}
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception:
        print(json.dumps({"status": "blocked", "reason": "reconciliation_validation_or_conflict",
                          "provider_calls": 0}))
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
