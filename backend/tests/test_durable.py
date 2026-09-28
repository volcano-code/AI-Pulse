from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from sqlalchemy import func, select
import pytest
from fastapi.testclient import TestClient
from app.bootstrap import bootstrap
from app.briefing import build_brief
from app.config import Settings
from app.demo import seed_demo
from app.durable import (Lease, LeaseLost, IdempotencyConflict, QueueFull, cancel_queued, claim,
    enqueue, execute, fence, heartbeat, recover_expired)
from app.main import create_app
from app.models import Run, Brief, Delivery
from app.timeutil import iso, utcnow


@pytest.fixture
def durable_client(tmp_path):
    settings = Settings(_env_file=None, database_url=f'sqlite:///{tmp_path / "durable.db"}',
        task_mode='durable', data_mode='replay', outbox_dir=str(tmp_path / 'mail'), retry_base_seconds=1)
    with TestClient(create_app(settings)) as client:
        seed_demo(client.app.state.session_factory, settings)
        yield client


def env(client):
    return client.app.state.session_factory, client.app.state.settings


def test_api_returns_queued_without_executing_in_api(durable_client):
    response = durable_client.post('/api/v1/runs', json={'kind': 'brief'})
    assert response.status_code == 202
    row = durable_client.get('/api/v1/runs/' + response.json()['run_id']).json()
    assert row['status'] == 'queued' and row['engine'] == 'durable'
    assert row['attempts'] == 0


def test_worker_completes_durable_job(durable_client):
    f, s = env(durable_client)
    rid = enqueue(f, 'brief', s)
    lease = claim(f, s)
    assert lease.run_id == rid
    execute(f, s, lease)
    with f() as db:
        run = db.get(Run, rid)
        assert run.status == 'succeeded' and run.result['items'] == 6
        assert run.checkpoint['brief']['brief_id'] == run.result['brief_id']
        assert run.lease_token is None and run.active_key is None


def test_api_restart_does_not_reset_queued_or_leased_jobs(durable_client):
    f, s = env(durable_client)
    first = enqueue(f, 'brief', s)
    second = enqueue(f, 'brief', s)
    lease = claim(f, s)
    bootstrap(f, s)
    with f() as db:
        assert db.get(Run, first).status == 'running'
        assert db.get(Run, second).status == 'queued'
        assert db.get(Run, first).lease_token == lease.token


def test_api_and_worker_can_reopen_same_persisted_job(durable_client):
    from app.db import make_database
    f, s = env(durable_client)
    rid = enqueue(f, 'brief', s)
    engine2, factory2 = make_database(s)
    try:
        bootstrap(factory2, s)
        execute(factory2, s, claim(factory2, s))
        with f() as db:
            assert db.get(Run, rid).status == 'succeeded'
    finally:
        engine2.dispose()


def test_duplicate_idempotency_key_returns_one_job(durable_client):
    f, s = env(durable_client)
    assert enqueue(f, 'brief', s, request_key='x') == enqueue(f, 'brief', s, request_key='x')
    with f() as db:
        assert db.scalar(select(func.count()).select_from(Run)) == 1


def test_idempotency_key_rejects_different_payload(durable_client):
    f, s = env(durable_client)
    enqueue(f, 'daily', s, {'send': False}, 'x')
    with pytest.raises(IdempotencyConflict):
        enqueue(f, 'daily', s, {'send': True}, 'x')


def test_duplicate_submissions_in_threads(durable_client):
    f, s = env(durable_client)
    with ThreadPoolExecutor(max_workers=6) as pool:
        ids = list(pool.map(lambda _: enqueue(f, 'brief', s, request_key='same-key'), range(12)))
    assert len(set(ids)) == 1


def test_only_one_worker_can_claim_workspace(durable_client):
    f, s = env(durable_client)
    for _ in range(3):
        enqueue(f, 'brief', s)
    with ThreadPoolExecutor(max_workers=4) as pool:
        leases = list(pool.map(lambda _: claim(f, s), range(8)))
    assert len([item for item in leases if item]) == 1


def test_lease_expiry_requeues_with_backoff(durable_client):
    f, s = env(durable_client)
    rid = enqueue(f, 'brief', s)
    lease = claim(f, s)
    future = utcnow() + timedelta(seconds=s.worker_lease_seconds + 1)
    assert recover_expired(f, s, future) == 1
    assert claim(f, s, now=future) is None
    newer = claim(f, s, now=future + timedelta(seconds=2))
    assert newer.token != lease.token
    with f() as db:
        assert db.get(Run, rid).attempts == 2


def test_stale_token_cannot_write_or_renew(durable_client):
    f, s = env(durable_client)
    enqueue(f, 'brief', s)
    lease = claim(f, s)
    stale = Lease(lease.run_id, 'wrong-token')
    with pytest.raises(LeaseLost), f.begin() as db:
        fence(db, stale)
    assert heartbeat(f, s, stale) is False
    execute(f, s, stale)
    with f() as db:
        assert db.get(Run, lease.run_id).status == 'running'


def test_expired_token_cannot_be_resurrected(durable_client):
    f, s = env(durable_client)
    enqueue(f, 'brief', s)
    lease = claim(f, s)
    with f.begin() as db:
        db.get(Run, lease.run_id).lease_until = iso(utcnow() - timedelta(seconds=1))
    assert heartbeat(f, s, lease) is False


def test_exhausted_crash_budget_is_terminal(durable_client):
    f, s = env(durable_client)
    rid = enqueue(f, 'brief', s)
    claim(f, s)
    with f.begin() as db:
        run = db.get(Run, rid)
        run.attempts = run.max_attempts
        run.lease_until = iso(utcnow() - timedelta(seconds=1))
    recover_expired(f, s)
    with f() as db:
        assert db.get(Run, rid).status == 'failed'
    assert claim(f, s) is None


def test_uncertain_paid_model_is_not_automatically_replayed(durable_client):
    f, s = env(durable_client)
    rid = enqueue(f, 'brief', s)
    claim(f, s)
    with f.begin() as db:
        run = db.get(Run, rid)
        run.checkpoint = {'model_in_flight': True}
        run.lease_until = iso(utcnow() - timedelta(seconds=1))
    recover_expired(f, s)
    with f() as db:
        assert db.get(Run, rid).status == 'needs_attention'
    assert claim(f, s) is None


def test_completed_brief_checkpoint_avoids_regeneration(durable_client, monkeypatch):
    f, s = env(durable_client)
    result = build_brief(f, s)
    rid = enqueue(f, 'daily', s, request={'send': True})
    with f.begin() as db:
        db.get(Run, rid).checkpoint = {'brief': result, 'model_in_flight': False}
    monkeypatch.setattr('app.durable.build_brief', lambda *a, **kw: pytest.fail('Must reuse completed stage'))
    execute(f, s, claim(f, s))
    with f() as db:
        assert db.get(Run, rid).status == 'succeeded'
        assert db.scalar(select(func.count()).select_from(Delivery)) == 1


def test_outbox_and_completion_rollback_together(durable_client, monkeypatch):
    import app.delivery
    f, s = env(durable_client)
    original = app.delivery.enqueue_delivery
    def crash_after_insert(db, *args):
        original(db, *args)
        raise RuntimeError('Injected failure before commit')
    monkeypatch.setattr(app.delivery, 'enqueue_delivery', crash_after_insert)
    rid = enqueue(f, 'daily', s, request={'send': True})
    execute(f, s, claim(f, s))
    with f() as db:
        assert db.get(Run, rid).status == 'failed'
        assert db.scalar(select(func.count()).select_from(Delivery)) == 0
        assert db.scalar(select(func.count()).select_from(Brief)) == 1  # Saved stage remains inspectable.


def test_cancel_queued_only(durable_client):
    f, s = env(durable_client)
    rid = enqueue(f, 'brief', s)
    cancel_queued(f, rid)
    assert claim(f, s) is None
    with pytest.raises(ValueError):
        cancel_queued(f, rid)
    with pytest.raises(LookupError):
        cancel_queued(f, 'missing')


def test_queue_limit(durable_client):
    f, s = env(durable_client)
    for _ in range(20):
        enqueue(f, 'brief', s)
    with pytest.raises(QueueFull):
        enqueue(f, 'brief', s)


@pytest.mark.parametrize('kind', ['bogus', 'ingest'])
def test_invalid_or_network_replay_job_rejected(durable_client, kind):
    f, s = env(durable_client)
    with pytest.raises(ValueError):
        enqueue(f, kind, s)


def test_daily_creates_local_preview_outbox_only(durable_client):
    from app.worker import step
    f, s = env(durable_client)
    rid = enqueue(f, 'daily', s, request={'send': True})
    result = step(f, s)
    assert result['run_id'] == rid and result['delivery_id']
    with f() as db:
        delivery = db.get(Delivery, result['delivery_id'])
        assert delivery.status == 'file_written'
        assert Path(s.outbox_dir, delivery.artifact_name).is_file()
        assert 'SYNTHETIC' in delivery.subject
    assert step(f, s) == {'run_id': None, 'delivery_id': None}


def test_daily_stale_date_is_not_silently_delivered(durable_client):
    f, s = env(durable_client)
    rid = enqueue(f, 'daily', s, request={'send': True, 'local_date': '2000-01-01', 'timezone': 'UTC'})
    execute(f, s, claim(f, s))
    with f() as db:
        assert db.get(Run, rid).status == 'failed'
        assert db.scalar(select(func.count()).select_from(Delivery)) == 0
