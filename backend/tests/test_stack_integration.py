"""Upgrade the stacked changes against disposable pre-stack SQLite history."""
import importlib.util
from pathlib import Path
import sqlite3
from contextlib import closing

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from app.config import Settings
from app.db import make_database
from app.models import Brief, Delivery, FetchRun, Source


def test_0009_backup_upgrade_preserves_fetch_and_outbox_history(tmp_path):
    backend = Path(__file__).resolve().parents[1]
    source = tmp_path / 'pre_stack.db'
    settings = Settings(_env_file=None, database_url=f'sqlite:///{source}', data_mode='replay')
    config = Config(str(backend / 'alembic.ini'))
    config.set_main_option('script_location', str(backend / 'migrations'))
    config.attributes['settings_override'] = settings
    command.upgrade(config, '0009')
    engine, factory = make_database(settings)
    try:
        with factory.begin() as db:
            db.add(Source(id='legacy', name='中文来源', url='https://example.invalid/feed', kind='rss'))
            db.add(Brief(id='legacy-brief', fingerprint='legacy', local_date='2026-01-01',
                         cutoff_at='2026-01-01T00:00:00Z', window_start='2025-12-31T00:00:00Z',
                         timezone='UTC', status='published', data_mode='replay',
                         generation_mode='extractive', preferences={}))
            db.flush()
            # Core inserts omit the new nullable columns, so these work on 0009.
            db.execute(FetchRun.__table__.insert().values(id='legacy-fetch', source_id='legacy',
                       status='running', counts={'inserted': 2}, error='旧采集记录'))
            for state in ('pending', 'unknown', 'sent'):
                db.execute(Delivery.__table__.insert().values(id=state, brief_id='legacy-brief',
                           dedupe_key=state, transport='file', recipient='r@example.invalid',
                           sender='s@example.invalid', subject='中文历史', body_text='不可覆盖的正文',
                           message_id=f'<{state}>', status=state, attempts=2))
        with engine.connect() as db:
            before_fetch = db.execute(text('SELECT * FROM fetch_runs')).mappings().all()
            before_mail = db.execute(text('SELECT * FROM deliveries ORDER BY id')).mappings().all()
        spec = importlib.util.spec_from_file_location('stack_local', backend.parent / 'scripts/run_local.py')
        launcher = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(launcher)
        backup = launcher.backup_before_migration(settings.database_url, backend)
        assert backup and backup != source
        backup_bytes = backup.read_bytes()
        command.upgrade(config, 'head')
        with engine.connect() as db:
            assert db.scalar(text('SELECT version_num FROM alembic_version')) == ScriptDirectory.from_config(config).get_current_head()
            assert db.execute(text('PRAGMA foreign_key_check')).all() == []
            assert db.execute(text('PRAGMA integrity_check')).all() == [('ok',)]
            after_fetch = db.execute(text('SELECT * FROM fetch_runs')).mappings().all()
            after_mail = db.execute(text('SELECT * FROM deliveries ORDER BY id')).mappings().all()
            assert [{k:r[k] for k in before_fetch[0]} for r in after_fetch] == [dict(r) for r in before_fetch]
            assert [{k:r[k] for k in before_mail[0]} for r in after_mail] == [dict(r) for r in before_mail]
            assert all(r['job_run_id'] is None and r['job_lease_token'] is None for r in after_fetch)
            assert all(r['expires_at'] is None for r in after_mail)
            assert 'ix_fetch_runs_job_attempt' in {i['name'] for i in inspect(db).get_indexes('fetch_runs')}
        assert launcher.backup_before_migration(settings.database_url, backend) is None
        assert backup.read_bytes() == backup_bytes
        with closing(sqlite3.connect(backup)) as db:
            assert db.execute('SELECT version_num FROM alembic_version').fetchone() == ('0009',)
            assert db.execute('SELECT status FROM fetch_runs').fetchone() == ('running',)
            assert db.execute('SELECT count(*) FROM deliveries').fetchone() == (3,)
    finally:
        engine.dispose()
