from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import os
import threading
from uuid import uuid4

import pytest
from sqlalchemy import select, update
from app.config import Settings
from app.db import Base, make_database
from app.durable import Lease, LeaseLost, claim, fence, recover_expired
from app.ingestion import fetch_source
from app.models import Article, FetchRun, Run, Source
from app.timeutil import iso, utcnow

RSS = b'<rss><channel><item><title>Evidence</title><link>https://example.invalid/evidence</link><description>Exact content</description></item></channel></rss>'


@pytest.fixture
def database(tmp_path):
    s = Settings(_env_file=None, database_url=f'sqlite:///{tmp_path / "fetch.db"}',
                 data_mode='live', task_mode='durable', retry_base_seconds=1)
    engine, factory = make_database(s)
    Base.metadata.create_all(engine)
    yield factory, s
    engine.dispose()


def owner(factory):
    ident = uuid4().hex
    lease = Lease(ident, str(uuid4()))
    with factory.begin() as db:
        db.add(Source(id=ident, name='fixture', url='https://example.invalid/feed', kind='rss'))
        db.add(Run(id=ident, kind='ingest', engine='durable', status='running',
                   active_key='local-workspace', attempts=1, max_attempts=3,
                   lease_token=lease.token, lease_until=iso(utcnow() + timedelta(minutes=3))))
    return lease


@pytest.mark.parametrize('fetch_error', [False, True])
def test_expired_fetch_cannot_overwrite_recovered_attempt_and_retry(database, fetch_error):
    factory, settings = database
    old = owner(factory)
    entered, release = threading.Event(), threading.Event()
    class Delayed:
        def fetch(self, url):
            entered.set()
            assert release.wait(10)
            if fetch_error:
                raise OSError('fixture network failure')
            return RSS
    def stale():
        return fetch_source(factory, old.run_id, settings, Delayed(),
                            commit_guard=lambda db: fence(db, old),
                            job_run_id=old.run_id, job_lease_token=old.token)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(stale)
        try:
            assert entered.wait(10)
            with factory.begin() as db:
                db.get(Run, old.run_id).lease_until = iso(utcnow() - timedelta(seconds=1))
            assert recover_expired(factory, settings) == 1
            with factory.begin() as db:
                fetch = db.scalar(select(FetchRun))
                assert fetch.status == 'interrupted' and fetch.finished_at
                assert fetch.job_run_id == old.run_id and fetch.job_lease_token == old.token
                db.get(Run, old.run_id).available_at = iso(utcnow() - timedelta(seconds=1))
            new = claim(factory, settings)
            assert new and new.token != old.token
            class Good:
                def fetch(self, url):
                    return RSS
            result = fetch_source(factory, old.run_id, settings, Good(),
                                  commit_guard=lambda db: fence(db, new),
                                  job_run_id=new.run_id, job_lease_token=new.token)
            assert result['status'] == 'succeeded'
            with factory() as db:
                before = db.get(Source, old.run_id).last_success_at
        finally:
            release.set()
        with pytest.raises(LeaseLost):
            future.result(timeout=10)
    with factory() as db:
        rows = {r.job_lease_token: r for r in db.scalars(select(FetchRun))}
        assert rows[old.token].status == 'interrupted'
        assert rows[new.token].status == 'succeeded'
        source = db.get(Source, old.run_id)
        assert source.last_success_at == before and source.last_error is None and source.failure_count == 0
        assert len(list(db.scalars(select(Article)))) == 1


@pytest.mark.parametrize('postgres', [False, True])
@pytest.mark.parametrize('attempts,expected', [(1, 'queued'), (3, 'failed')])
def test_crash_recovery_is_atomic_and_attempt_scoped(tmp_path, postgres, attempts, expected):
    if postgres and os.getenv('AI_PULSE_TEST_POSTGRES') != '1':
        pytest.skip('PostgreSQL integration gate is opt-in')
    settings = Settings(_env_file=None) if postgres else Settings(_env_file=None, database_url=f'sqlite:///{tmp_path / "crash.db"}')
    engine, factory = make_database(settings)
    if not postgres:
        Base.metadata.create_all(engine)
    lease = owner(factory)
    ids = [str(uuid4()) for _ in range(4)]
    try:
        with factory.begin() as db:
            run = db.get(Run, lease.run_id)
            run.attempts = attempts
            run.lease_until = iso(utcnow() - timedelta(seconds=1))
            for ident, token, status in zip(ids, [lease.token, lease.token, 'other-attempt', None],
                                            ['running', 'succeeded', 'running', 'running']):
                db.add(FetchRun(id=ident, source_id=lease.run_id,
                               job_run_id=lease.run_id if token else None,
                               job_lease_token=token, status=status))
        with ThreadPoolExecutor(max_workers=2) as pool:
            counts = list(pool.map(lambda _: recover_expired(factory, settings), range(2)))
        assert sum(counts) == 1
        with factory() as db:
            rows = [db.get(FetchRun, ident) for ident in ids]
            assert [r.status for r in rows] == ['interrupted', 'succeeded', 'running', 'running']
            assert rows[0].finished_at and rows[0].error
            assert db.get(Run, lease.run_id).status == expected
            assert db.get(Source, lease.run_id).failure_count == 0
    finally:
        with factory.begin() as db:
            for ident in ids:
                row = db.get(FetchRun, ident)
                if row:
                    db.delete(row)
            db.flush()
            db.delete(db.get(Run, lease.run_id))
            db.delete(db.get(Source, lease.run_id))
        engine.dispose()


def test_fetch_recovery_failure_rolls_back_parent_transition(database):
    from sqlalchemy import event
    factory, settings = database
    lease = owner(factory)
    ident = str(uuid4())
    with factory.begin() as db:
        db.get(Run, lease.run_id).lease_until = iso(utcnow() - timedelta(seconds=1))
        db.add(FetchRun(id=ident, source_id=lease.run_id, job_run_id=lease.run_id,
                       job_lease_token=lease.token))
    engine = factory.kw['bind']
    def fail_child_update(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith('UPDATE fetch_runs'):
            raise RuntimeError('injected recovery transaction failure')
    event.listen(engine, 'before_cursor_execute', fail_child_update)
    try:
        with pytest.raises(RuntimeError, match='injected'):
            recover_expired(factory, settings)
    finally:
        event.remove(engine, 'before_cursor_execute', fail_child_update)
    with factory() as db:
        assert db.get(Run, lease.run_id).status == 'running'
        assert db.get(Run, lease.run_id).lease_token == lease.token
        assert db.get(FetchRun, ident).status == 'running'
    assert recover_expired(factory, settings) == 1


def test_execute_passes_attempt_owner_to_ingestion(database):
    from app.durable import execute
    factory, settings = database
    lease = owner(factory)
    class Good:
        def fetch(self, url):
            return RSS
    execute(factory, settings, lease, fetcher=Good())
    with factory() as db:
        row = db.scalar(select(FetchRun))
        assert (row.job_run_id, row.job_lease_token, row.status) == (lease.run_id, lease.token, 'succeeded')
        assert db.get(Run, lease.run_id).status == 'succeeded'
