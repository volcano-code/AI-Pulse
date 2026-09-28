import argparse
import json
from .bootstrap import bootstrap
from .briefing import build_brief
from .config import Settings
from .db import Base, make_database
from .demo import seed_demo
from .jobs import execute_run, new_run
from .models import Run


def main():
    parser = argparse.ArgumentParser(description="AI Pulse local administration")
    parser.add_argument("command", choices=["init", "demo", "ingest", "brief"])
    args = parser.parse_args()
    settings = Settings()
    engine, factory = make_database(settings)
    Base.metadata.create_all(engine)
    # CLI init/demo should not be run concurrently with the API process.
    bootstrap(factory, settings)
    if args.command == "demo":
        result = {"seed": seed_demo(factory, settings), "brief": build_brief(factory, settings)}
    elif args.command in {"ingest", "brief"}:
        run_id = new_run(factory, args.command)
        execute_run(factory, settings, run_id)
        with factory() as db:
            run = db.get(Run, run_id)
            result = {"run_id": run.id, "status": run.status, "result": run.result, "error": run.error}
    else:
        result = {"initialized": True, "data_mode": settings.data_mode}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    engine.dispose()
    return 1 if result.get("status") == "failed" else 2 if result.get("status") == "partial" else 0

if __name__ == "__main__":
    raise SystemExit(main())
