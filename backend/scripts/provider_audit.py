"""Read-only provider call receipts. Does not unlock, resend or invoke providers."""
import argparse
import json
from app.config import Settings
from app.db import make_database
from app.provider_calls import CallLedger


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args(argv)
    engine = None
    try:
        if not 1 <= args.limit <= 200: raise ValueError("Invalid limit")
        engine, factory = make_database(Settings())
        print(json.dumps({"calls": CallLedger(factory).inspect(args.limit)}, ensure_ascii=False))
        return 0
    except Exception:
        print(json.dumps({"status": "failed", "reason": "audit_read_failed_check_migrations"}))
        return 1
    finally:
        if engine is not None: engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
