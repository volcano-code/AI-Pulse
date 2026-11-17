from pathlib import Path
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from app.config import Settings
from app.db import make_database
from app.models import Brief, Delivery


def test_deadline_migration_preserves_existing_outbox_without_guessing(tmp_path):
    backend = Path(__file__).resolve().parents[1]
    settings = Settings(_env_file=None, database_url=f'sqlite:///{tmp_path / "legacy.db"}')
    config = Config(str(backend / 'alembic.ini'))
    config.set_main_option('script_location', str(backend / 'migrations'))
    config.attributes['settings_override'] = settings
    command.upgrade(config, '0010')
    engine, factory = make_database(settings)
    try:
        with factory.begin() as db:
            brief = Brief(id='legacy-brief', fingerprint='legacy', local_date='2026-01-01',
                          cutoff_at='2026-01-01T00:00:00Z', window_start='2025-12-31T00:00:00Z',
                          timezone='UTC', status='published', data_mode='replay',
                          generation_mode='extractive', preferences={})
            db.add(brief)
            db.flush()
            db.execute(Delivery.__table__.insert().values(id='legacy-delivery', brief_id=brief.id,
                       dedupe_key='legacy', transport='file', recipient='r@example.invalid',
                       sender='s@example.invalid', subject='中文历史', body_text='original body', message_id='<legacy>'))
        with engine.begin() as db:
            db.execute(text('CREATE TABLE expiry_preservation_probe (value TEXT)'))
            db.execute(text("INSERT INTO expiry_preservation_probe VALUES ('中文历史')"))
        for revision in ('0011', '0010', '0011'):
            if revision == '0010':
                command.downgrade(config, revision)
            else:
                command.upgrade(config, revision)
            with engine.connect() as db:
                assert db.scalar(text('SELECT value FROM expiry_preservation_probe')) == '中文历史'
                if revision == '0011':
                    assert db.execute(text('SELECT expires_at FROM deliveries')).all() == [(None,)]
                assert db.execute(text('SELECT id,subject,body_text,status FROM deliveries')).one() == ('legacy-delivery','中文历史','original body','pending')
    finally:
        engine.dispose()
