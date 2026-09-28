from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, date, timezone, timedelta
from sqlalchemy import select, func
from app.models import DailySchedule, Run
from app.scheduler import tick, due_instant
from app.timeutil import parse_time
from test_durable import durable_client, env


def configure(factory, **values):
    with factory.begin() as db:
        row = db.get(DailySchedule, 1)
        row.enabled = True
        row.timezone = 'America/Los_Angeles'
        row.local_time = '08:00'
        for key, value in values.items():
            setattr(row, key, value)


def test_disabled_by_default(durable_client):
    f, s = env(durable_client)
    assert tick(f, s) is None


def test_only_after_local_time_and_once_each_day(durable_client):
    f, s = env(durable_client)
    configure(f)
    morning = datetime(2026, 9, 23, 15, 0, tzinfo=timezone.utc)
    assert tick(f, s, morning - timedelta(seconds=1)) is None
    rid = tick(f, s, morning)
    assert rid
    assert tick(f, s, morning + timedelta(hours=1)) is None
    assert tick(f, s, morning + timedelta(days=1)) != rid


def test_spring_gap_moves_to_first_valid_minute():
    result = due_instant(date(2026, 3, 8), '02:30', 'America/Los_Angeles')
    assert result == datetime(2026, 3, 8, 10, 0, tzinfo=timezone.utc)


def test_autumn_fold_uses_first_occurrence():
    result = due_instant(date(2026, 11, 1), '01:30', 'America/Los_Angeles')
    assert result == datetime(2026, 11, 1, 8, 30, tzinfo=timezone.utc)


def test_repeated_autumn_hour_does_not_double_schedule(durable_client):
    f, s = env(durable_client)
    configure(f, local_time='01:30')
    assert tick(f, s, datetime(2026, 11, 1, 8, 30, tzinfo=timezone.utc))
    assert tick(f, s, datetime(2026, 11, 1, 9, 30, tzinfo=timezone.utc)) is None


def test_chinese_timezone_maps_correct_utc():
    assert due_instant(date(2026, 9, 23), '08:00', 'Asia/Shanghai') == datetime(2026, 9, 23, 0, 0, tzinfo=timezone.utc)


def test_no_historical_catchup_flood(durable_client):
    f, s = env(durable_client)
    configure(f)
    now = datetime(2026, 9, 23, 23, 0, tzinfo=timezone.utc)
    tick(f, s, now)
    with f() as db:
        runs = list(db.scalars(select(Run)))
        assert len(runs) == 1 and runs[0].request['local_date'] == '2026-09-23'


def test_concurrent_ticks_only_enqueue_once(durable_client):
    f, s = env(durable_client)
    configure(f)
    now = datetime(2026, 9, 23, 23, 0, tzinfo=timezone.utc)
    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(lambda _: tick(f, s, now), range(10)))
    assert len([x for x in results if x]) == 1
    with f() as db:
        assert db.scalar(select(func.count()).select_from(Run)) == 1


def test_schedule_change_same_day_does_not_resend(durable_client):
    f, s = env(durable_client)
    configure(f)
    now = datetime(2026, 9, 23, 23, 0, tzinfo=timezone.utc)
    tick(f, s, now)
    configure(f, local_time='09:00', send=True)
    assert tick(f, s, now) is None


def test_local_runner_never_ticks_schedule(durable_client):
    f, s = env(durable_client)
    configure(f)
    assert tick(f, s.model_copy(update={'task_mode': 'local'})) is None
