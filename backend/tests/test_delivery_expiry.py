from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
import pytest
from sqlalchemy import select
from app.durable import claim, enqueue, execute
from app.models import Delivery
from app.timeutil import utcnow
from test_durable import durable_client, env


@pytest.mark.parametrize('zone', ['UTC', 'Asia/Shanghai'])
def test_scheduled_delivery_waiting_past_local_midnight_is_not_sent(durable_client, monkeypatch, zone):
    import app.durable as jobs
    import app.delivery as mail
    f, s = env(durable_client)
    day = utcnow().astimezone(ZoneInfo(zone)).date()
    deadline = datetime.combine(day + timedelta(days=1), time.min, tzinfo=ZoneInfo(zone)).astimezone(timezone.utc)
    monkeypatch.setattr(jobs, 'utcnow', lambda: deadline - timedelta(seconds=2))
    rid = enqueue(f, 'daily', s, request={'send': True, 'local_date': day.isoformat(), 'timezone': zone})
    execute(f, s, claim(f, s))
    calls = []
    class Spy:
        def send(self, row):
            calls.append(row.id)
            return 'fixture.eml'
    monkeypatch.setattr(mail, 'utcnow', lambda: deadline + timedelta(seconds=1))
    mail.deliver_one(f, s, Spy())
    assert calls == []
    with f() as db:
        row = db.scalar(select(Delivery))
        assert row.status == 'expired' and row.attempts == 0 and row.finished_at


@pytest.mark.parametrize('zone,day,expected', [
    ('America/New_York', '2026-03-08', '2026-03-09T04:00:00.000000Z'),
    ('America/New_York', '2026-11-01', '2026-11-02T05:00:00.000000Z'),
])
def test_scheduled_deadline_uses_next_local_midnight_across_dst(durable_client, monkeypatch, zone, day, expected):
    import app.durable as jobs
    from datetime import date
    f, s = env(durable_client)
    start = datetime.combine(date.fromisoformat(day), time(hour=12), tzinfo=ZoneInfo(zone)).astimezone(timezone.utc)
    monkeypatch.setattr(jobs, 'utcnow', lambda: start)
    enqueue(f, 'daily', s, request={'send': True, 'local_date': day, 'timezone': zone})
    execute(f, s, claim(f, s))
    with f() as db:
        assert db.scalar(select(Delivery)).expires_at == expected


def test_expired_queue_race_and_retry_backoff_never_resubmit(durable_client, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import app.delivery as mail
    from app.timeutil import iso
    from test_delivery import outbox
    f, s = env(durable_client)
    ident = outbox(durable_client)
    deadline = utcnow() + timedelta(seconds=1)
    with f.begin() as db:
        db.get(Delivery, ident).expires_at = iso(deadline)
    calls = []
    class Retry:
        def send(self, row):
            calls.append(row.id)
            raise mail.DeliveryRetryable('fake temporary rejection')
    monkeypatch.setattr(mail, 'utcnow', lambda: deadline - timedelta(microseconds=1))
    mail.deliver_one(f, s, Retry())
    with f() as db:
        assert db.get(Delivery, ident).status == 'pending'
        assert db.get(Delivery, ident).available_at > iso(deadline)
    monkeypatch.setattr(mail, 'utcnow', lambda: deadline)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: mail.deliver_one(f, s, Retry()), range(8)))
    with f() as db:
        row = db.get(Delivery, ident)
        assert row.status == 'expired' and row.attempts == 1
    assert calls == [ident]


@pytest.mark.parametrize('transport,expected', [('file', 'expired'), ('smtp', 'unknown')])
def test_expired_sender_crash_preserves_smtp_uncertainty(durable_client, transport, expected):
    import app.delivery as mail
    from app.timeutil import iso
    from test_delivery import outbox
    f, s = env(durable_client)
    ident = outbox(durable_client)
    with f.begin() as db:
        row = db.get(Delivery, ident)
        row.transport, row.status, row.lease_token = transport, 'sending', 'crashed'
        row.expires_at = row.lease_until = iso(utcnow() - timedelta(seconds=1))
    assert mail.recover_deliveries(f) == 1
    with f() as db:
        assert db.get(Delivery, ident).status == expected


def test_postgres_expiry_claim_race_has_no_submission():
    import os
    from uuid import uuid4
    from concurrent.futures import ThreadPoolExecutor
    from app.config import Settings
    from app.db import make_database
    from app.models import Brief
    from app.timeutil import iso
    import app.delivery as mail
    if os.getenv('AI_PULSE_TEST_POSTGRES') != '1':
        pytest.skip('PostgreSQL integration gate is opt-in')
    settings = Settings(_env_file=None)
    engine, f = make_database(settings)
    ident = str(uuid4())
    deadline = iso(utcnow() - timedelta(seconds=1))
    calls = []
    class Spy:
        def send(self, row):
            calls.append(row.id)
            raise AssertionError('Expired delivery must not reach any transport')
    try:
        with f.begin() as db:
            db.add(Brief(id=ident, fingerprint=ident, local_date='2026-01-01', cutoff_at=deadline,
                         window_start=deadline, timezone='UTC', status='published', data_mode='replay',
                         generation_mode='extractive', preferences={}))
            db.flush()
            db.add(Delivery(id=ident, brief_id=ident, dedupe_key=ident, transport='file',
                            recipient='r@example.invalid', sender='s@example.invalid', subject='fixture',
                            body_text='fixture', message_id=f'<{ident}>', expires_at=deadline))
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: mail.deliver_one(f, settings, Spy()), range(8)))
        with f() as db:
            row = db.get(Delivery, ident)
            assert row.status == 'expired' and row.attempts == 0 and row.finished_at
        assert calls == []
    finally:
        with f.begin() as db:
            db.delete(db.get(Delivery, ident))
            db.flush()
            db.delete(db.get(Brief, ident))
        engine.dispose()
