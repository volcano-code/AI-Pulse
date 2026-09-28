"""Small, database-backed single-workspace queue. Not a Celery implementation.

Claims and completions are conditional SQL writes, with a per-attempt fencing token.
Network operations never hold a database transaction. Delivery has its own outbox.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
import threading
from uuid import uuid4
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from .briefing import build_brief
from .process_fetcher import ProcessFetcher
from .ingestion import fetch_source
from .models import Run, Source, Workspace
from .timeutil import iso, parse_time, utcnow


class LeaseLost(RuntimeError):
    pass


class RetryableJob(RuntimeError):
    pass


class QueueFull(RuntimeError):
    pass


class IdempotencyConflict(ValueError):
    pass


@dataclass(frozen=True)
class Lease:
    run_id: str
    token: str


def add_job(db, kind, settings, request=None, request_key=None, now=None):
    """The caller owns the transaction (also used by the scheduler)."""
    if kind not in {'ingest', 'brief', 'daily'}:
        raise ValueError('Unsupported job kind')
    if kind == 'ingest' and settings.data_mode != 'live':
        raise ValueError('Replay data cannot trigger network ingestion')
    if request_key and (len(request_key) > 180 or not request_key.strip()):
        raise ValueError('Invalid idempotency key')
    # Serialize enqueue count/idempotency checks on the workspace row.
    db.execute(update(Workspace).where(Workspace.id == 1).values(id=1))
    if request_key:
        existing = db.scalar(select(Run).where(Run.request_key == request_key))
        if existing:
            if existing.kind != kind or existing.request != (request or {}):
                raise IdempotencyConflict('Idempotency key already used with a different request')
            return existing
    pending = db.scalar(select(func.count()).select_from(Run).where(
        Run.engine == 'durable', Run.status.in_(['queued', 'running'])))
    if pending >= 20:
        raise QueueFull('Queue limit reached; wait for existing jobs or cancel queued jobs')
    run = Run(kind=kind, engine='durable', status='queued', active_key=None,
              max_attempts=settings.job_max_attempts, request=request or {},
              request_key=request_key, available_at=iso(now or utcnow()))
    db.add(run)
    db.flush()
    return run


def enqueue(factory, kind, settings, request=None, request_key=None, now=None):
    try:
        with factory.begin() as db:
            return add_job(db, kind, settings, request, request_key, now).id
    except IntegrityError:
        if not request_key:
            raise
        with factory() as db:
            existing = db.scalar(select(Run).where(Run.request_key == request_key))
            if existing and existing.kind == kind and existing.request == (request or {}):
                return existing.id
        raise IdempotencyConflict('Idempotency key conflict') from None


def recover_expired(factory, settings, now=None):
    now = now or utcnow()
    recovered = 0
    with factory() as db:
        candidates = [(r.id, r.lease_token) for r in db.scalars(select(Run).where(
            Run.engine == 'durable', Run.status == 'running', Run.lease_until <= iso(now)))]
    for run_id, token in candidates:
        with factory.begin() as db:
            changed = db.execute(update(Run).where(
                Run.id == run_id, Run.lease_token == token,
                Run.status == 'running', Run.lease_until <= iso(now)
            ).values(status='recovering'))
            if not changed.rowcount:
                continue
            run = db.get(Run, run_id)
            # Paid request may have completed remotely, even with no local acknowledgement.
            uncertain = bool(run.checkpoint.get('model_in_flight'))
            terminal = uncertain or run.attempts >= run.max_attempts
            run.status = 'needs_attention' if uncertain else 'failed' if terminal else 'queued'
            run.available_at = iso(now + timedelta(seconds=settings.retry_base_seconds * 2 ** max(0, run.attempts - 1)))
            run.active_key = run.lease_token = run.lease_until = None
            run.finished_at = iso(now) if terminal else None
            run.error = ('Model outcome unknown; automatic replay disabled.' if uncertain
                         else 'Worker lease expired; retry budget exhausted.' if terminal
                         else 'Worker lease expired; safe retry queued.')
            run.events = [*run.events[-199:], {'stage': 'recovery', 'message': run.error, 'at': iso(now)}]
            recovered += 1
    return recovered


def claim(factory, settings, now=None):
    now = now or utcnow()
    with factory() as db:
        run_id = db.scalar(select(Run.id).where(
            Run.engine == 'durable', Run.status == 'queued',
            or_(Run.available_at.is_(None), Run.available_at <= iso(now))
        ).order_by(Run.created_at, Run.id).limit(1))
    if not run_id:
        return None
    token = str(uuid4())
    try:
        with factory.begin() as db:
            changed = db.execute(update(Run).where(
                Run.id == run_id, Run.status == 'queued',
                or_(Run.available_at.is_(None), Run.available_at <= iso(now))
            ).values(status='running', active_key='local-workspace', lease_token=token,
                     lease_until=iso(now + timedelta(seconds=settings.worker_lease_seconds)),
                     attempts=Run.attempts + 1, error=None))
            return Lease(run_id, token) if changed.rowcount else None
    except IntegrityError:
        # A different worker already owns the single-workspace execution slot.
        return None


def fence(db, lease, now=None):
    """No-op write fences later writes in this same database transaction."""
    changed = db.execute(update(Run).where(
        Run.id == lease.run_id, Run.lease_token == lease.token,
        Run.status == 'running', Run.lease_until > iso(now or utcnow())
    ).values(lease_token=lease.token))
    if not changed.rowcount:
        raise LeaseLost('Worker no longer owns this attempt')


def mutate(factory, lease, callback):
    with factory.begin() as db:
        fence(db, lease)
        run = db.get(Run, lease.run_id)
        callback(run)


def heartbeat(factory, settings, lease):
    now = utcnow()
    with factory.begin() as db:
        changed = db.execute(update(Run).where(
            Run.id == lease.run_id, Run.lease_token == lease.token,
            Run.status == 'running', Run.lease_until > iso(now)
        ).values(lease_until=iso(now + timedelta(seconds=settings.worker_lease_seconds))))
        return bool(changed.rowcount)


@contextmanager
def keep_lease(factory, settings, lease):
    stop = threading.Event()
    def renew():
        while not stop.wait(max(1, settings.worker_lease_seconds / 3)):
            try:
                if not heartbeat(factory, settings, lease):
                    return
            except Exception:
                # Do not act on an unconfirmed lease; subsequent fenced writes will fail.
                return
    thread = threading.Thread(target=renew, name='pulse-lease', daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=5)


def cancel_queued(factory, run_id):
    with factory.begin() as db:
        run = db.get(Run, run_id)
        if not run:
            raise LookupError('Run not found')
        changed = db.execute(update(Run).where(Run.id == run_id, Run.engine == 'durable',
            Run.status == 'queued').values(status='cancelled', finished_at=iso(utcnow())))
        if not changed.rowcount:
            raise ValueError('Only queued durable jobs can be cancelled; in-flight operations are not interrupted')


def execute(factory, settings, lease, fetcher=None, model_client=None):
    from .delivery import enqueue_delivery
    def event(stage, message):
        def append(run):
            run.events = [*run.events[-199:], {'stage': stage, 'message': message, 'at': iso(utcnow())}]
        mutate(factory, lease, append)
    def checkpoint(key, value):
        mutate(factory, lease, lambda r: setattr(r, 'checkpoint', {**r.checkpoint, key: value}))
    def guard(db):
        fence(db, lease)
    try:
        with keep_lease(factory, settings, lease):
            event('start', 'Durable worker claimed this attempt; completed stages are retained.')
            with factory() as db:
                run = db.get(Run, lease.run_id)
                kind, request, saved = run.kind, dict(run.request), dict(run.checkpoint)
            if kind == 'daily' and request.get('local_date'):
                from zoneinfo import ZoneInfo
                today = utcnow().astimezone(ZoneInfo(request['timezone'])).date().isoformat()
                if today != request['local_date']:
                    raise ValueError('Scheduled day has passed; do not silently send a late edition')
            source_results = list(saved.get('sources', []))
            if kind in {'ingest', 'daily'} and settings.data_mode == 'live' and not saved.get('ingestion_done'):
                with factory() as db:
                    ids = list(db.scalars(select(Source.id).where(Source.enabled.is_(True)).order_by(Source.id)))
                if not ids:
                    raise ValueError('No enabled source; daily pipeline will not silently generate old content')
                by_id = {item['source_id']: item for item in source_results}
                for source_id in ids:
                    if by_id.get(source_id, {}).get('status') == 'succeeded':
                        continue
                    event('fetch', f'Reading configured source: {source_id}')
                    result = fetch_source(factory, source_id, settings, fetcher or ProcessFetcher(settings), commit_guard=guard)
                    by_id[source_id] = result
                    checkpoint('sources', list(by_id.values()))
                source_results = [by_id[source_id] for source_id in ids]
                if not any(x['status'] == 'succeeded' for x in source_results):
                    raise RetryableJob('No enabled source returned valid content')
                checkpoint('ingestion_done', True)
            brief_result = saved.get('brief')
            if kind in {'brief', 'daily'} and not brief_result:
                if settings.llm_mode == 'live':
                    checkpoint('model_in_flight', True)
                brief_result = build_brief(factory, settings, event, model_client, commit_guard=guard)
                def store_brief(r):
                    r.checkpoint = {**r.checkpoint, 'brief': brief_result, 'model_in_flight': False}
                mutate(factory, lease, store_brief)
            with factory.begin() as db:
                fence(db, lease)
                run = db.get(Run, lease.run_id)
                result = {'sources': source_results}
                if brief_result:
                    result.update(brief_result)
                if kind == 'daily' and request.get('send') and brief_result and brief_result['items']:
                    # Durable job completion and outbox insertion commit together.
                    delivery = enqueue_delivery(db, brief_result['brief_id'], settings)
                    result['delivery_id'] = delivery.id
                    result['delivery_status'] = delivery.status
                elif kind == 'daily' and request.get('send'):
                    result['delivery_status'] = 'skipped_empty_brief'
                run.result = result
                run.status = 'partial' if any(x['status'] == 'failed' for x in source_results) else 'succeeded'
                run.finished_at = iso(utcnow())
                run.active_key = run.lease_token = run.lease_until = None
                run.events = [*run.events[-199:], {'stage': 'done', 'message': 'Committed results and any requested outbox entry.', 'at': iso(utcnow())}]
    except LeaseLost:
        return  # A newer attempt owns the state. Never overwrite it.
    except Exception as exc:
        try:
            def fail(run):
                uncertain = bool(run.checkpoint.get('model_in_flight'))
                retry = isinstance(exc, RetryableJob) and not uncertain and run.attempts < run.max_attempts
                run.status = 'needs_attention' if uncertain else 'queued' if retry else 'failed'
                run.available_at = iso(utcnow() + timedelta(seconds=settings.retry_base_seconds * 2 ** max(0, run.attempts - 1)))
                run.error = ('Model outcome may have incurred charges; manual inspection required.' if uncertain
                             else f'{type(exc).__name__}: pipeline failed; inspect source status and configuration.')
                run.finished_at = None if retry else iso(utcnow())
                run.active_key = run.lease_token = run.lease_until = None
                run.events = [*run.events[-199:], {'stage': 'retry' if retry else 'error', 'message': run.error, 'at': iso(utcnow())}]
            mutate(factory, lease, fail)
        except LeaseLost:
            pass
