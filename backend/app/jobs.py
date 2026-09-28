from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from .briefing import build_brief
from .process_fetcher import ProcessFetcher
from .ingestion import fetch_source
from .models import Run, Source
from .timeutil import iso, utcnow

class BusyError(RuntimeError):
    pass

def new_run(session_factory, kind: str) -> str:
    try:
        with session_factory.begin() as db:
            run = Run(kind=kind, active_key="local-workspace")
            db.add(run)
            db.flush()
            return run.id
    except IntegrityError:
        raise BusyError("已有任务执行中，请查看运行记录") from None


def run_payload(run: Run):
    return {"id": run.id, "kind": run.kind, "status": run.status,
            "created_at": run.created_at, "finished_at": run.finished_at,
            "events": run.events, "result": run.result, "error": run.error,
            "engine": run.engine, "attempts": run.attempts,
            "max_attempts": run.max_attempts, "available_at": run.available_at,
            "lease_until": run.lease_until, "checkpoint": run.checkpoint}


def execute_run(session_factory, settings, run_id: str, fetcher=None, model_client=None):
    with session_factory.begin() as db:
        claim = db.execute(update(Run).where(Run.id == run_id, Run.status == "queued").values(status="running"))
        if not claim.rowcount:
            return
        kind = db.get(Run, run_id).kind
    def progress(stage, message):
        with session_factory.begin() as db:
            run = db.get(Run, run_id)
            run.events = [*run.events, {"stage": stage, "message": message, "at": iso(utcnow())}]
    try:
        progress("start", "本地任务开始；所有数据变更都会入库")
        if kind == "ingest":
            with session_factory() as db:
                source_ids = list(db.scalars(select(Source.id).where(Source.enabled.is_(True)).order_by(Source.id)))
            results = []
            for source_id in source_ids:
                progress("fetch", f"读取来源：{source_id}")
                result = fetch_source(session_factory, source_id, settings, fetcher or ProcessFetcher(settings))
                results.append(result)
                progress("source_result", f"{source_id}: {result['status']}")
            result = {"sources": results}
            failures = sum(x["status"] == "failed" for x in results)
            status = "failed" if results and failures == len(results) else "partial" if failures else "succeeded"
        else:
            result = build_brief(session_factory, settings, progress, model_client)
            status = "succeeded"
        progress("done", "任务完成" if status == "succeeded" else "任务存在采集失败，旧数据未伪装为新数据")
        with session_factory.begin() as db:
            run = db.get(Run, run_id)
            run.status, run.result, run.active_key, run.finished_at = status, result, None, iso(utcnow())
    except Exception as exc:
        with session_factory.begin() as db:
            run = db.get(Run, run_id)
            run.status, run.active_key, run.finished_at = "failed", None, iso(utcnow())
            run.error = f"{type(exc).__name__}: pipeline failed. Check mode, credentials and evidence validation."
