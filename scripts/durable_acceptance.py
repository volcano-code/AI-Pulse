"""Fault-injection smoke: kill a claimed worker process, recover using real workers.

Uses an isolated temporary replay database and local .eml files. No live network,
SMTP, or paid models. The killed child executes the real claim function then pauses;
this exercises death after claim, not every possible crash point.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / 'backend'
sys.path.insert(0, str(BACKEND))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = {'mode': 'synthetic-replay', 'smtp_submissions': 0, 'model_requests': 0, 'checks': []}
    with tempfile.TemporaryDirectory(prefix='pulse-process-') as tmp:
        url = f'sqlite:///{Path(tmp) / "test.db"}'
        env = {**os.environ, 'DATABASE_URL': url, 'DATA_MODE': 'replay',
               'LLM_MODE': 'extractive', 'TASK_MODE': 'durable', 'DELIVERY_TRANSPORT': 'file',
               'WORKER_LEASE_SECONDS': '10', 'RETRY_BASE_SECONDS': '1',
               'OUTBOX_DIR': str(Path(tmp) / 'outbox'), 'AUTO_INIT_DB': 'false'}
        logs = []
        def run(*command):
            result = subprocess.run([sys.executable, *command], cwd=BACKEND, env=env,
                                    capture_output=True, text=True, timeout=30)
            logs.append({'command': 'python ' + ' '.join(command), 'returncode': result.returncode,
                         'stdout': result.stdout, 'stderr': result.stderr})
            if result.returncode:
                raise RuntimeError(result.stderr or result.stdout)
            return result
        run('-m', 'alembic', 'upgrade', 'head')
        run('-m', 'app.cli', 'demo')
        from app.config import Settings
        from app.db import make_database
        from app.durable import enqueue
        from app.models import Run, Delivery
        from sqlalchemy import select
        settings = Settings(_env_file=None, database_url=url, data_mode='replay',
            llm_mode='extractive', task_mode='durable', worker_lease_seconds=10,
            retry_base_seconds=1, delivery_transport='file', outbox_dir=env['OUTBOX_DIR'])
        engine, factory = make_database(settings)
        ident = enqueue(factory, 'daily', settings, {'send': True}, 'process-crash-smoke')
        ready = Path(tmp) / 'claimed.json'
        child_code = """
import json, time
from pathlib import Path
from app.config import Settings
from app.db import make_database
from app.durable import claim
s=Settings(); engine,factory=make_database(s); lease=claim(factory,s)
Path(%r).write_text(json.dumps({'run_id': lease.run_id, 'token': lease.token}))
time.sleep(120)
""" % str(ready)
        child = subprocess.Popen([sys.executable, '-c', child_code], cwd=BACKEND, env=env,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            end = time.monotonic() + 10
            while not ready.exists() and time.monotonic() < end and child.poll() is None:
                time.sleep(0.05)
            assert ready.exists(), 'Child did not claim the job'
            assert json.loads(ready.read_text())['run_id'] == ident
            child.kill()
            child.wait(timeout=5)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
        report['killed_child_exitcode'] = child.returncode
        with factory() as db:
            row = db.get(Run, ident)
            assert row.status == 'running' and row.attempts == 1
        report['checks'].append('Abrupt process death leaves the claimed task persisted')
        # Real elapsed time: no monkeypatch of utcnow or lease expiration.
        time.sleep(10.5)
        run('-m', 'app.worker', '--once')
        time.sleep(1.2)
        run('-m', 'app.worker', '--once')
        with factory() as db:
            row = db.get(Run, ident)
            assert row.status == 'succeeded' and row.attempts == 2, (row.status, row.error)
            deliveries = list(db.scalars(select(Delivery)))
            assert len(deliveries) == 1 and deliveries[0].status == 'file_written'
            report.update(task_status=row.status, attempts=row.attempts,
                          deliveries=len(deliveries), delivery_status=deliveries[0].status)
        report['checks'].append('A new real worker recovers the same task on its second attempt')
        report['checks'].append('The recovered daily pipeline writes exactly one local outbox file')
        run('-m', 'app.worker', '--once')
        files = list(Path(env['OUTBOX_DIR']).glob('*.eml'))
        assert len(files) == 1 and b'SYNTHETIC' in files[0].read_bytes()
        report['checks'].append('Another fresh worker process produces no duplicate delivery')
        engine.dispose()
        report['commands'] = logs
    report['passed'] = True
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + '\n')
    print(text)

if __name__ == '__main__':
    main()
