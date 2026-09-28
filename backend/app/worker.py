"""Run separately from the API: python -m app.worker [--once]."""
import argparse
import os
import socket
import threading
from uuid import uuid4
from .bootstrap import bootstrap
from .config import Settings
from .db import Base, make_database
from .delivery import deliver_one, recover_deliveries
from .durable import claim, execute, recover_expired
from .models import WorkerHeartbeat
from .scheduler import tick
from .timeutil import iso, utcnow


_UNCHANGED = object()

def beat(factory, worker_id, run_id=_UNCHANGED):
    with factory.begin() as db:
        row = db.get(WorkerHeartbeat, worker_id)
        if not row:
            row = WorkerHeartbeat(id=worker_id, last_seen_at=iso(utcnow()))
            db.add(row)
        row.last_seen_at = iso(utcnow())
        if run_id is not _UNCHANGED:
            row.run_id = run_id


def step(factory, settings, worker_id='test-worker'):
    beat(factory, worker_id, None)
    recover_expired(factory, settings)
    recover_deliveries(factory, max_attempts=settings.job_max_attempts)
    tick(factory, settings)
    lease = claim(factory, settings)
    if lease:
        beat(factory, worker_id, lease.run_id)
        execute(factory, settings, lease)
    sent = deliver_one(factory, settings)
    beat(factory, worker_id, None)
    return {'run_id': lease.run_id if lease else None, 'delivery_id': sent}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true', help='One tick, at most one job and one delivery')
    args = parser.parse_args()
    settings = Settings()
    if settings.task_mode != 'durable':
        raise SystemExit('Set TASK_MODE=durable for both API and worker')
    engine, factory = make_database(settings)
    if settings.auto_init_db:
        Base.metadata.create_all(engine)
    bootstrap(factory, settings)
    ident = f'{socket.gethostname()[:30]}-{os.getpid()}-{uuid4().hex[:8]}'
    stop = threading.Event()
    def pulse():
        while not stop.wait(10):
            try:
                beat(factory, ident)
            except Exception:
                pass
    pulse_thread = threading.Thread(target=pulse, daemon=True)
    pulse_thread.start()
    print(f'AI Pulse durable worker started: {ident}; delivery={settings.delivery_transport}', flush=True)
    try:
        while True:
            result = step(factory, settings, ident)
            if result['run_id'] or result['delivery_id']:
                print(result, flush=True)
            if args.once:
                return
            stop.wait(settings.worker_poll_seconds)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        pulse_thread.join(timeout=5)
        engine.dispose()


if __name__ == '__main__':
    main()
