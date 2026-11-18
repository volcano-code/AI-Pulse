"""Disposable replay fixtures; all SMTP calls are intercepted."""
from datetime import timedelta
import smtplib
import pytest
import app.delivery as mail
from app.models import Brief, Delivery
from app.timeutil import iso, utcnow
from test_delivery import FakeSMTP, outbox
from test_durable import durable_client, env


def setup(client, monkeypatch, review=False):
    f, s = env(client)
    ident = outbox(client, review=review)
    clock = [utcnow()]
    deadline = clock[0] + timedelta(seconds=2)
    monkeypatch.setattr(mail, 'utcnow', lambda: clock[0])
    with f.begin() as db:
        db.get(Delivery, ident).expires_at = iso(deadline)
    return f, s, ident, clock, deadline


@pytest.mark.parametrize('crossing,expected', [('login', 'expired'), ('accepted', 'sent'), ('uncertain', 'unknown')])
def test_smtp_deadline_boundary(durable_client, monkeypatch, crossing, expected):
    f, s, ident, clock, deadline = setup(durable_client, monkeypatch)
    class Fake(FakeSMTP):
        log, failure, close_failure = [], None, False
        def login(self, *args):
            super().login(*args)
            if crossing == 'login':
                clock[0] = deadline
        def sendmail(self, *args):
            assert clock[0] < deadline
            super().sendmail(*args)
            clock[0] = deadline + timedelta(seconds=1)
            if crossing == 'uncertain':
                raise smtplib.SMTPServerDisconnected('fixture unknown acceptance')
            return {}
    monkeypatch.setattr(smtplib, 'SMTP', Fake)
    monkeypatch.setattr(smtplib, 'SMTP_SSL', Fake)
    # Fixture-only bypass of replay SMTP prohibition; both constructors above
    # are replaced before dispatch. No real SMTP or network is possible.
    with f.begin() as db:
        db.get(Delivery, ident).transport = 'smtp'
    s = s.model_copy(update={'delivery_transport': 'smtp', 'smtp_host': 'smtp.example.invalid',
                            'smtp_username': 'fixture', 'smtp_password': 'fixture-only'})
    assert mail.deliver_one(f, s) == ident
    with f.begin() as db:
        assert mail.expire_waiting(db, clock[0]) == 0
        row = db.get(Delivery, ident)
        assert row.status == expected and row.finished_at and row.lease_token is None
    assert Fake.log.count('sendmail') == (0 if crossing == 'login' else 1)
    assert Fake.log[-1] == 'close'
    assert mail.deliver_one(f, s) is None


@pytest.mark.parametrize('resolution,expected', [('confirmed_not_sent', 'expired'), ('confirmed_sent', 'sent')])
def test_unknown_resolution_after_deadline(durable_client, monkeypatch, resolution, expected):
    f, s, ident, clock, deadline = setup(durable_client, monkeypatch)
    with f.begin() as db:
        db.get(Delivery, ident).status = 'unknown'
    clock[0] = deadline
    mail.resolve_unknown(f, ident, resolution)
    with f() as db:
        row = db.get(Delivery, ident)
        assert row.status == expected and row.finished_at
        assert resolution in row.last_error
    class Spy:
        def send(self, row):
            pytest.fail('Expired or accepted delivery must never resubmit')
    assert mail.deliver_one(f, s, Spy()) is None


def test_review_at_deadline_cannot_revive_delivery(durable_client, monkeypatch):
    f, s, ident, clock, deadline = setup(durable_client, monkeypatch, review=True)
    clock[0] = deadline
    with f.begin() as db:
        row = db.get(Delivery, ident)
        db.get(Brief, row.brief_id).status = 'published'
        mail.release_reviewed(db, row.brief_id)
    with f() as db:
        row = db.get(Delivery, ident)
        assert row.status == 'expired' and row.attempts == 0 and row.finished_at
    class Spy:
        def send(self, row):
            pytest.fail('Late approval must not submit')
    assert mail.deliver_one(f, s, Spy()) is None


@pytest.mark.parametrize('original_deadline', [False, True])
def test_reenqueue_keeps_first_deadline_and_terminal_state(durable_client, monkeypatch, original_deadline):
    f, s, ident, clock, deadline = setup(durable_client, monkeypatch)
    saved = iso(deadline) if original_deadline else None
    with f.begin() as db:
        row = db.get(Delivery, ident)
        row.expires_at = saved
        brief_id = row.brief_id
    clock[0] = deadline
    with f.begin() as db:
        mail.expire_waiting(db, clock[0])
        for replacement in (None, iso(deadline + timedelta(days=1))):
            row = mail.enqueue_delivery(db, brief_id, s, expires_at=replacement)
            assert row.id == ident and row.expires_at == saved
            assert row.status == ('expired' if original_deadline else 'pending')
