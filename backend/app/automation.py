"""Authorized automation endpoints; provider credentials are never returned."""
from pathlib import Path
from typing import Literal
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from .delivery import delivery_payload, enqueue_delivery, resolve_unknown
from .durable import enqueue, cancel_queued, QueueFull, IdempotencyConflict
from .models import DailySchedule, Delivery, WorkerHeartbeat
from .schemas import Preferences
from .scheduler import schedule_payload
from .timeutil import iso, parse_time, utcnow


class ScheduleInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool = False
    local_time: str = Field(default='08:00', pattern=r'^(?:[01]\d|2[0-3]):[0-5]\d$')
    timezone: str = 'America/Los_Angeles'
    send: bool = False

    @field_validator('timezone')
    @classmethod
    def valid_zone(cls, value):
        return Preferences.valid_zone(value)


class DailyInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    send: bool = False
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)


class Confirmed(BaseModel):
    confirmed: Literal[True]


class ResolveInput(BaseModel):
    confirmed: Literal[True]
    resolution: Literal['confirmed_sent', 'confirmed_not_sent']


def automation_router(factory, settings):
    router = APIRouter()

    @router.get('/automation')
    def automation_status():
        with factory() as db:
            workers = list(db.scalars(select(WorkerHeartbeat).order_by(WorkerHeartbeat.last_seen_at.desc()).limit(10)))
            return {
                'task_mode': settings.task_mode, 'delivery_transport': settings.delivery_transport,
                'recipient': settings.mail_to, 'schedule': schedule_payload(db.get(DailySchedule, 1)),
                'worker_online': any((utcnow() - parse_time(w.last_seen_at)).total_seconds() < 35 for w in workers),
                'workers': [{'id': w.id, 'last_seen_at': w.last_seen_at, 'run_id': w.run_id} for w in workers],
                'delivery_note': ('Local .eml preview only; no message is sent.' if settings.delivery_transport == 'file'
                                  else 'SMTP accepted is not proof of inbox delivery.'),
            }

    @router.put('/automation/schedule')
    def configure_schedule(body: ScheduleInput):
        if body.enabled and settings.task_mode != 'durable':
            raise HTTPException(409, 'Start API and worker with TASK_MODE=durable before enabling a schedule')
        with factory.begin() as db:
            schedule = db.get(DailySchedule, 1)
            for key, value in body.model_dump().items():
                setattr(schedule, key, value)
            schedule.updated_at = iso(utcnow())
            return schedule_payload(schedule)

    @router.post('/automation/run', status_code=202)
    def run_daily(body: DailyInput):
        if settings.task_mode != 'durable':
            raise HTTPException(409, 'Daily pipeline requires TASK_MODE=durable and a running worker')
        try:
            ident = enqueue(factory, 'daily', settings, request={'send': body.send},
                request_key=f'manual:{body.idempotency_key}' if body.idempotency_key else None)
        except (QueueFull, IdempotencyConflict) as exc:
            raise HTTPException(409, str(exc)) from None
        return {'run_id': ident, 'status': 'queued'}

    @router.post('/runs/{run_id}/cancel')
    def cancel(run_id: str, body: Confirmed):
        try:
            cancel_queued(factory, run_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        return {'run_id': run_id, 'status': 'cancelled'}

    @router.get('/deliveries')
    def deliveries():
        with factory() as db:
            return [delivery_payload(d) for d in db.scalars(select(Delivery).order_by(Delivery.created_at.desc()).limit(50))]

    @router.post('/briefs/{brief_id}/deliver', status_code=202)
    def deliver(brief_id: str, body: Confirmed):
        if settings.task_mode != 'durable':
            raise HTTPException(409, 'Delivery requires the durable worker')
        try:
            with factory.begin() as db:
                return delivery_payload(enqueue_delivery(db, brief_id, settings))
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        except IntegrityError:
            # A concurrent identical insert is safe; retry observes the committed record.
            with factory.begin() as db:
                return delivery_payload(enqueue_delivery(db, brief_id, settings))

    @router.get('/deliveries/{delivery_id}/preview')
    def preview(delivery_id: str):
        with factory() as db:
            delivery = db.get(Delivery, delivery_id)
            if not delivery:
                raise HTTPException(404, 'Delivery not found')
            if delivery.transport != 'file' or delivery.status != 'file_written':
                raise HTTPException(409, 'No completed local preview is available')
            expected = f'{delivery.id}.eml'
            if delivery.artifact_name != expected:
                raise HTTPException(409, 'Unexpected preview artifact name')
            path = Path(settings.outbox_dir) / expected
            if not path.is_file():
                raise HTTPException(404, 'Preview file is missing')
            return FileResponse(path, media_type='message/rfc822', filename=expected)

    @router.post('/deliveries/{delivery_id}/resolve')
    def resolve(delivery_id: str, body: ResolveInput):
        try:
            resolve_unknown(factory, delivery_id, body.resolution)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        return {'id': delivery_id, 'resolution': body.resolution}

    return router
