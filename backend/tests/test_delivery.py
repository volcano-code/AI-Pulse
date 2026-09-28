from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from email import policy
from email.parser import BytesParser
from pathlib import Path
import smtplib
from sqlalchemy import select, func
import pytest
from app.briefing import build_brief
from app.config import Settings
from app.delivery import (DeliveryRetryable, DeliveryPermanent, DeliveryUnknown,
    FileTransport, SMTPTransport, deliver_one, enqueue_delivery, message_bytes,
    recover_deliveries, release_reviewed, resolve_unknown)
from app.models import Brief, Delivery
from app.timeutil import iso, utcnow
from test_durable import durable_client, env


def outbox(client, review=False):
    f, s = env(client)
    result = build_brief(f, s)
    with f.begin() as db:
        if review:
            db.get(Brief, result['brief_id']).status = 'needs_review'
        delivery = enqueue_delivery(db, result['brief_id'], s)
        return delivery.id


def test_delivery_deduplicates_same_brief_and_target(durable_client):
    f, s = env(durable_client)
    one, two = outbox(durable_client), outbox(durable_client)
    assert one == two
    with f() as db:
        assert db.scalar(select(func.count()).select_from(Delivery)) == 1


def test_outbox_body_and_message_id_are_frozen(durable_client):
    f, s = env(durable_client)
    ident = outbox(durable_client)
    with f() as db:
        delivery = db.get(Delivery, ident)
        first, second = message_bytes(delivery), message_bytes(delivery)
        assert first == second
        message = BytesParser(policy=policy.default).parsebytes(first)
        assert message['Message-ID'] == delivery.message_id
        assert 'SYNTHETIC' in str(message['Subject'])
        assert 'NOT REAL NEWS' in message.get_content()


def test_file_preview_is_not_labelled_email_sent(durable_client):
    f, s = env(durable_client)
    ident = outbox(durable_client)
    assert deliver_one(f, s) == ident
    assert deliver_one(f, s) is None
    with f() as db:
        row = db.get(Delivery, ident)
        assert row.status == 'file_written' and row.attempts == 1
        assert Path(s.outbox_dir, row.artifact_name).read_bytes() == message_bytes(row)


def test_unreviewed_draft_stays_blocked(durable_client):
    f, s = env(durable_client)
    ident = outbox(durable_client, review=True)
    assert deliver_one(f, s) is None
    with f.begin() as db:
        row = db.get(Delivery, ident)
        assert row.status == 'blocked_review'
        brief = db.get(Brief, row.brief_id)
        brief.status = 'published'
        release_reviewed(db, brief.id)
    assert deliver_one(f, s) == ident


def test_sender_rechecks_review_gate_before_submission(durable_client):
    f, s = env(durable_client)
    ident = outbox(durable_client)
    with f.begin() as db:
        db.get(Brief, db.get(Delivery, ident).brief_id).status = 'needs_review'
    deliver_one(f, s)
    with f() as db:
        assert db.get(Delivery, ident).status == 'blocked_review'
    assert not Path(s.outbox_dir).exists()


def test_concurrent_delivery_dispatch_submits_once(durable_client):
    f, s = env(durable_client)
    ident = outbox(durable_client)
    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(lambda _: deliver_one(f, s), range(10)))
    assert results.count(ident) == 1
    with f() as db:
        assert db.get(Delivery, ident).attempts == 1


def test_file_transport_repeat_keeps_one_identical_file(durable_client):
    f, s = env(durable_client)
    ident = outbox(durable_client)
    with f() as db:
        item = db.get(Delivery, ident)
        sender = FileTransport(s.outbox_dir)
        assert sender.send(item) == sender.send(item)
    assert len(list(Path(s.outbox_dir).glob('*.eml'))) == 1


def test_known_preacceptance_failure_gets_bounded_retry(durable_client):
    f, s = env(durable_client)
    ident = outbox(durable_client)
    class Reject:
        def send(self, item):
            raise DeliveryRetryable('private upstream details must not leak')
    for attempt in range(s.job_max_attempts):
        with f.begin() as db:
            db.get(Delivery, ident).available_at = iso(utcnow() - timedelta(seconds=1))
        deliver_one(f, s, Reject())
        with f() as db:
            row = db.get(Delivery, ident)
            assert row.status == ('failed' if attempt == s.job_max_attempts - 1 else 'pending')
            assert 'private upstream' not in row.last_error
    assert deliver_one(f, s, Reject()) is None


def test_permanent_rejection_not_retried(durable_client):
    f, s = env(durable_client)
    ident = outbox(durable_client)
    class Reject:
        def send(self, item):
            raise DeliveryPermanent('rejected')
    deliver_one(f, s, Reject())
    with f() as db:
        assert db.get(Delivery, ident).status == 'failed'


def test_unknown_outcome_requires_explicit_resolution(durable_client):
    f, s = env(durable_client)
    ident = outbox(durable_client)
    class Unknown:
        def send(self, item):
            raise DeliveryUnknown('connection dropped')
    deliver_one(f, s, Unknown())
    assert deliver_one(f, s) is None
    with f() as db:
        assert db.get(Delivery, ident).status == 'unknown'
    resolve_unknown(f, ident, 'confirmed_not_sent')
    assert deliver_one(f, s) == ident
    with pytest.raises(ValueError):
        resolve_unknown(f, ident, 'confirmed_sent')


def test_manual_confirmed_sent_does_not_resubmit(durable_client):
    f, s = env(durable_client)
    ident = outbox(durable_client)
    with f.begin() as db:
        db.get(Delivery, ident).status = 'unknown'
    resolve_unknown(f, ident, 'confirmed_sent')
    assert deliver_one(f, s) is None


@pytest.mark.parametrize('transport,status', [('smtp', 'unknown'), ('file', 'pending')])
def test_crashed_sender_recovers_conservatively(durable_client, transport, status):
    f, s = env(durable_client)
    ident = outbox(durable_client)
    with f.begin() as db:
        row = db.get(Delivery, ident)
        row.status, row.transport, row.lease_token = 'sending', transport, 'old'
        row.lease_until = iso(utcnow() - timedelta(seconds=1))
    assert recover_deliveries(f) == 1
    with f() as db:
        assert db.get(Delivery, ident).status == status


def test_late_sender_cannot_overwrite_recovered_unknown(durable_client):
    f, s = env(durable_client)
    ident = outbox(durable_client)
    class Slow:
        def send(self, item):
            with f.begin() as db:
                row = db.get(Delivery, item.id)
                row.transport = 'smtp'
                row.lease_until = iso(utcnow() - timedelta(seconds=1))
            recover_deliveries(f)
            return 'late.eml'
    deliver_one(f, s, Slow())
    with f() as db:
        row = db.get(Delivery, ident)
        assert row.status == 'unknown' and row.artifact_name is None


def test_changed_transport_is_not_silently_used(durable_client):
    f, s = env(durable_client)
    ident = outbox(durable_client)
    deliver_one(f, s.model_copy(update={'delivery_transport': 'smtp'}))
    with f() as db:
        assert db.get(Delivery, ident).status == 'failed'


def test_replay_cannot_be_enqueued_for_real_smtp(durable_client):
    f, s = env(durable_client)
    brief = build_brief(f, s)
    with pytest.raises(ValueError), f.begin() as db:
        enqueue_delivery(db, brief['brief_id'], s.model_copy(update={'delivery_transport': 'smtp'}))


@pytest.mark.parametrize('address', ['a@example.com\r\nBcc:evil@evil.com', 'a@example.com,b@example.com', 'not-mail'])
def test_reject_mailbox_header_injection(address):
    with pytest.raises(ValueError):
        Settings(_env_file=None, mail_to=address)


def test_replay_smtp_configuration_rejected():
    with pytest.raises(ValueError):
        Settings(_env_file=None, data_mode='replay', delivery_transport='smtp', smtp_host='smtp.example.com')


class FakeSMTP:
    log = []
    failure = None
    close_failure = False
    def __init__(self, *args, **kwargs):
        self.log.append('connect')
    def ehlo(self):
        self.log.append('ehlo')
    def starttls(self, **kwargs):
        self.log.append('starttls')
    def login(self, *args):
        self.log.append('login')
    def sendmail(self, sender, recipients, data):
        self.log.append('sendmail')
        assert len(recipients) == 1 and isinstance(data, bytes)
        if self.failure:
            raise self.failure
        return {}
    def close(self):
        self.log.append('close')
        if self.close_failure:
            raise smtplib.SMTPServerDisconnected('late QUIT failure')


def smtp_subject(client):
    f, s = env(client)
    ident = outbox(client)
    with f() as db:
        item = db.get(Delivery, ident)
    settings = s.model_copy(update={'smtp_host': 'smtp.example.com', 'smtp_username': 'test', 'smtp_password': 'secret'})
    return item, settings


def test_smtp_tls_auth_and_acceptance_contract(durable_client, monkeypatch):
    item, s = smtp_subject(durable_client)
    FakeSMTP.log, FakeSMTP.failure, FakeSMTP.close_failure = [], None, True
    monkeypatch.setattr('smtplib.SMTP', FakeSMTP)
    assert SMTPTransport(s).send(item) is None
    assert FakeSMTP.log == ['connect', 'ehlo', 'starttls', 'ehlo', 'login', 'sendmail', 'close']
    FakeSMTP.close_failure = False


@pytest.mark.parametrize('failure,exception', [
    (smtplib.SMTPDataError(451, b'try later'), DeliveryRetryable),
    (smtplib.SMTPDataError(550, b'no'), DeliveryPermanent),
    (smtplib.SMTPServerDisconnected('lost after data'), DeliveryUnknown),
    (TimeoutError('lost'), DeliveryUnknown),
    (smtplib.SMTPRecipientsRefused({'reader@example.com': (450, b'temporary')}), DeliveryRetryable),
    (smtplib.SMTPRecipientsRefused({'reader@example.com': (550, b'permanent')}), DeliveryPermanent),
])
def test_smtp_failure_classification(durable_client, monkeypatch, failure, exception):
    item, s = smtp_subject(durable_client)
    FakeSMTP.log, FakeSMTP.failure, FakeSMTP.close_failure = [], failure, False
    monkeypatch.setattr('smtplib.SMTP', FakeSMTP)
    with pytest.raises(exception):
        SMTPTransport(s).send(item)
    FakeSMTP.failure = None


def test_connection_error_before_data_is_safe_to_retry(durable_client, monkeypatch):
    item, s = smtp_subject(durable_client)
    def broken(*args, **kwargs):
        raise OSError('DNS failure')
    monkeypatch.setattr('smtplib.SMTP', broken)
    with pytest.raises(DeliveryRetryable):
        SMTPTransport(s).send(item)


def test_expired_file_sender_exhausts_crash_retry_budget(durable_client):
    f, s = env(durable_client)
    ident = outbox(durable_client)
    with f.begin() as db:
        row = db.get(Delivery, ident)
        row.status, row.lease_token = 'sending', 'abandoned'
        row.attempts = s.job_max_attempts
        row.lease_until = iso(utcnow() - timedelta(seconds=1))
    assert recover_deliveries(f, max_attempts=s.job_max_attempts) == 1
    with f() as db:
        row = db.get(Delivery, ident)
        assert row.status == 'failed' and row.finished_at
    assert deliver_one(f, s) is None


def test_worker_heartbeat_preserves_active_run_until_explicit_clear(durable_client):
    from app.worker import beat
    from app.models import WorkerHeartbeat
    f, s = env(durable_client)
    beat(f, 'worker-test', 'active-run')
    beat(f, 'worker-test')
    with f() as db:
        assert db.get(WorkerHeartbeat, 'worker-test').run_id == 'active-run'
    beat(f, 'worker-test', None)
    with f() as db:
        assert db.get(WorkerHeartbeat, 'worker-test').run_id is None
