"""Transactional outbox with conservative SMTP result classification.

file_written means a local .eml was saved, NOT an email was delivered.
SMTP 'sent' means the server accepted DATA, NOT inbox delivery/read confirmation.
"""
from datetime import timedelta
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import format_datetime
import os
from pathlib import Path
import smtplib
import ssl
import tempfile
from uuid import uuid4
from sqlalchemy import select, update
from .briefing import brief_payload
from .models import Brief, Delivery
from .textutil import digest
from .timeutil import iso, parse_time, utcnow


class DeliveryRetryable(RuntimeError):
    pass


class DeliveryPermanent(RuntimeError):
    pass


class DeliveryUnknown(RuntimeError):
    pass


def enqueue_delivery(db, brief_id, settings):
    brief = db.get(Brief, brief_id)
    if not brief:
        raise LookupError('Brief not found')
    if settings.delivery_transport == 'smtp' and brief.data_mode != 'live':
        raise ValueError('Synthetic data must never be sent by SMTP')
    key = digest('|'.join([brief_id, settings.delivery_transport, settings.mail_to, settings.mail_from]))
    existing = db.scalar(select(Delivery).where(Delivery.dedupe_key == key))
    if existing:
        return existing
    data = brief_payload(db, brief)
    if not data['items']:
        raise ValueError('Empty briefs are not sent')
    prefix = '[SYNTHETIC REPLAY] ' if brief.data_mode == 'replay' else ''
    subject = f'{prefix}AI Pulse | {brief.local_date}'
    lines = [subject, '', f'Mode: {brief.data_mode} / {brief.generation_mode}',
             f'Cutoff: {brief.cutoff_at}; timezone: {brief.timezone}',
             'Source excerpts and reviewed drafts are not independent fact verification.', '']
    if brief.data_mode == 'replay':
        lines += ['SYNTHETIC TEST DATA. NOT REAL NEWS. LOCAL PREVIEW ONLY.', '']
    for item in data['items']:
        lines += [f"{item['position']}. {item['title']}", item['summary'],
                  f"Source: {item['source_url']}",
                  f"Evidence: {item['snapshot_id']} [{item['quote_start']}, {item['quote_end']})", '']
    ident = str(uuid4())
    delivery = Delivery(id=ident, brief_id=brief_id, dedupe_key=key,
        transport=settings.delivery_transport, recipient=settings.mail_to, sender=settings.mail_from,
        subject=subject, body_text='\n'.join(lines),
        message_id=f'<{ident}@{settings.mail_from.rsplit("@", 1)[-1]}>',
        status='pending' if brief.status == 'published' else 'blocked_review')
    db.add(delivery)
    db.flush()
    return delivery


def release_reviewed(db, brief_id):
    db.execute(update(Delivery).where(Delivery.brief_id == brief_id,
        Delivery.status == 'blocked_review').values(status='pending', available_at=iso(utcnow())))


def message_bytes(delivery):
    message = EmailMessage(policy=SMTP)
    message['From'] = delivery.sender
    message['To'] = delivery.recipient
    message['Subject'] = delivery.subject
    message['Message-ID'] = delivery.message_id
    message['Date'] = format_datetime(parse_time(delivery.created_at))
    message.set_content(delivery.body_text)
    return message.as_bytes()


class FileTransport:
    def __init__(self, directory):
        self.directory = Path(directory)

    def send(self, delivery):
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / f'{delivery.id}.eml'
        # Deterministic bytes and pathname make file-mode recovery idempotent.
        payload = message_bytes(delivery)
        if target.exists() and target.read_bytes() == payload:
            return target.name
        descriptor, temporary = tempfile.mkstemp(prefix='.pulse-', dir=self.directory)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return target.name


class SMTPTransport:
    def __init__(self, settings):
        self.settings = settings

    def send(self, delivery):
        s = self.settings
        client = None
        sending = False
        try:
            context = ssl.create_default_context()
            if s.smtp_security == 'ssl':
                client = smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=s.smtp_timeout_seconds, context=context)
            else:
                client = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=s.smtp_timeout_seconds)
                client.ehlo()
                client.starttls(context=context)
                client.ehlo()
            if s.smtp_username:
                client.login(s.smtp_username, s.smtp_password)
            payload = message_bytes(delivery)
            sending = True
            refused = client.sendmail(delivery.sender, [delivery.recipient], payload)
            if refused:
                code = next(iter(refused.values()))[0]
                if 400 <= code < 500:
                    raise DeliveryRetryable('Recipient temporarily rejected')
                raise DeliveryPermanent('Recipient permanently rejected')
            return None
        except (DeliveryRetryable, DeliveryPermanent):
            raise
        except smtplib.SMTPRecipientsRefused as exc:
            codes = [value[0] for value in exc.recipients.values()]
            if codes and all(400 <= code < 500 for code in codes):
                raise DeliveryRetryable('Recipient temporarily rejected') from None
            raise DeliveryPermanent('Recipient rejected') from None
        except smtplib.SMTPResponseException as exc:
            # A negative reply is a known rejection, not an uncertain success.
            if 400 <= exc.smtp_code < 500:
                raise DeliveryRetryable('SMTP temporarily rejected request') from None
            raise DeliveryPermanent('SMTP permanently rejected request') from None
        except smtplib.SMTPNotSupportedError:
            raise DeliveryPermanent('Required SMTP security/authentication is unavailable') from None
        except (OSError, smtplib.SMTPServerDisconnected):
            if sending:
                raise DeliveryUnknown('Connection lost during send; acceptance is unknown') from None
            raise DeliveryRetryable('SMTP connection failed before message submission') from None
        except Exception:
            if sending:
                raise DeliveryUnknown('Unexpected failure during submission; inspect provider logs') from None
            raise DeliveryPermanent('SMTP configuration or message validation failed') from None
        finally:
            if client is not None:
                try:
                    client.close()  # QUIT failure must not turn an accepted DATA into a retry.
                except Exception:
                    pass


def recover_deliveries(factory, now=None, max_attempts=3):
    now = now or utcnow()
    with factory() as db:
        candidates = [(d.id, d.lease_token, d.transport, d.attempts) for d in db.scalars(select(Delivery).where(
            Delivery.status == 'sending', Delivery.lease_until <= iso(now)))]
    count = 0
    for ident, token, transport, attempts in candidates:
        with factory.begin() as db:
            changed = db.execute(update(Delivery).where(Delivery.id == ident,
                Delivery.status == 'sending', Delivery.lease_token == token,
                Delivery.lease_until <= iso(now)).values(
                    status='unknown' if transport == 'smtp' else 'failed' if attempts >= max_attempts else 'pending',
                    lease_token=None, lease_until=None, available_at=iso(now),
                    finished_at=iso(now) if transport == 'smtp' or attempts >= max_attempts else None,
                    last_error='Sender lease expired. SMTP is not automatically resent; file retries are bounded.'))
            count += changed.rowcount
    return count


def deliver_one(factory, settings, transport=None):
    now = utcnow()
    with factory() as db:
        ident = db.scalar(select(Delivery.id).where(Delivery.status == 'pending',
            Delivery.available_at <= iso(now)).order_by(Delivery.created_at, Delivery.id).limit(1))
    if not ident:
        return None
    token = str(uuid4())
    with factory.begin() as db:
        changed = db.execute(update(Delivery).where(Delivery.id == ident,
            Delivery.status == 'pending', Delivery.available_at <= iso(now)).values(
                status='sending', lease_token=token,
                lease_until=iso(now + timedelta(seconds=max(180, settings.smtp_timeout_seconds * 8))),
                attempts=Delivery.attempts + 1))
        if not changed.rowcount:
            return None
        delivery = db.get(Delivery, ident)
        brief = db.get(Brief, delivery.brief_id)
        if brief.status != 'published':
            delivery.status, delivery.lease_token, delivery.lease_until = 'blocked_review', None, None
            return ident
    status, error, artifact = None, None, None
    try:
        if delivery.transport != settings.delivery_transport:
            raise DeliveryPermanent('Configured transport changed; inspect pending delivery')
        actual = transport or (FileTransport(settings.outbox_dir) if delivery.transport == 'file' else SMTPTransport(settings))
        artifact = actual.send(delivery)
        status = 'file_written' if delivery.transport == 'file' else 'sent'
    except DeliveryRetryable:
        status = 'pending' if delivery.attempts < settings.job_max_attempts else 'failed'
        error = 'Submission rejected before acceptance; bounded retry queued.' if status == 'pending' else 'Delivery retry budget exhausted.'
    except DeliveryPermanent:
        status, error = 'failed', 'Delivery rejected or configuration invalid; inspect server configuration.'
    except DeliveryUnknown:
        status, error = 'unknown', 'Acceptance unknown. Automatic resend disabled; inspect provider logs.'
    except Exception:
        status = ('pending' if delivery.attempts < settings.job_max_attempts else 'failed') if delivery.transport == 'file' else 'unknown'
        error = 'Local file write failed.' if delivery.transport == 'file' else 'Unexpected SMTP outcome; automatic resend disabled.'
    with factory.begin() as db:
        # Token fencing prevents a late sender overwriting an already-recovered outcome.
        db.execute(update(Delivery).where(Delivery.id == ident, Delivery.lease_token == token,
            Delivery.status == 'sending', Delivery.lease_until > iso(utcnow())).values(status=status, last_error=error, artifact_name=artifact,
                lease_token=None, lease_until=None,
                finished_at=None if status == 'pending' else iso(utcnow()),
                available_at=iso(utcnow() + timedelta(seconds=settings.retry_base_seconds * 2 ** max(0, delivery.attempts - 1)))))
    return ident


def resolve_unknown(factory, delivery_id, resolution):
    if resolution not in {'confirmed_sent', 'confirmed_not_sent'}:
        raise ValueError('Unsupported resolution')
    with factory.begin() as db:
        delivery = db.get(Delivery, delivery_id)
        if not delivery:
            raise LookupError('Delivery not found')
        changed = db.execute(update(Delivery).where(Delivery.id == delivery_id,
            Delivery.status == 'unknown').values(
                status='sent' if resolution == 'confirmed_sent' else 'pending',
                last_error=f'Manual resolution: {resolution}',
                finished_at=iso(utcnow()) if resolution == 'confirmed_sent' else None,
                available_at=iso(utcnow())))
        if not changed.rowcount:
            raise ValueError('Only unknown delivery outcomes may be resolved')


def delivery_payload(item):
    return {key: getattr(item, key) for key in ['id', 'brief_id', 'transport', 'recipient', 'subject',
        'status', 'attempts', 'created_at', 'available_at', 'finished_at', 'last_error', 'artifact_name']}
