"""One daily occurrence per schedule/local date; no historical catch-up flood."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from .durable import add_job, QueueFull
from .models import DailySchedule, Run
from .timeutil import iso, utcnow


def due_instant(day, local_time, zone):
    tz = ZoneInfo(zone)
    hour, minute = map(int, local_time.split(':'))
    wall = datetime(day.year, day.month, day.day, hour, minute)
    # Ambiguous time: first occurrence. Nonexistent time: first valid minute after gap.
    for offset in range(181):
        candidate = wall + timedelta(minutes=offset)
        valid = []
        for fold in (0, 1):
            aware = candidate.replace(tzinfo=tz, fold=fold)
            utc = aware.astimezone(timezone.utc)
            if utc.astimezone(tz).replace(tzinfo=None) == candidate:
                valid.append(utc)
        if valid:
            return min(valid)
    raise ValueError('Unable to resolve local schedule time')


def tick(factory, settings, now=None):
    if settings.task_mode != 'durable':
        return None
    now = now or utcnow()
    try:
        with factory.begin() as db:
            db.execute(update(DailySchedule).where(DailySchedule.id == 1).values(id=1))
            schedule = db.get(DailySchedule, 1)
            if not schedule or not schedule.enabled:
                return None
            day = now.astimezone(ZoneInfo(schedule.timezone)).date()
            if now < due_instant(day, schedule.local_time, schedule.timezone):
                return None
            key = f'daily:{schedule.id}:{day.isoformat()}'
            if db.scalar(select(Run.id).where(Run.request_key == key)):
                return None
            run = add_job(db, 'daily', settings, request={
                'local_date': day.isoformat(), 'timezone': schedule.timezone, 'send': schedule.send,
            }, request_key=key, now=now)
            return run.id
    except (IntegrityError, QueueFull):
        # Duplicate scheduler tick or busy queue; next tick is safe.
        return None


def schedule_payload(schedule):
    return {key: getattr(schedule, key) for key in ['enabled', 'local_time', 'timezone', 'send', 'updated_at']}
