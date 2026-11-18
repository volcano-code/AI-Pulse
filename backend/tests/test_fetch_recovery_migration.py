from pathlib import Path
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from app.config import Settings
from app.db import make_database


def test_fetch_owner_migration_preserves_legacy_history(tmp_path):
    backend = Path(__file__).resolve().parents[1]
    settings = Settings(_env_file=None, database_url=f'sqlite:///{tmp_path / "migration.db"}')
    cfg = Config(str(backend / 'alembic.ini'))
    cfg.set_main_option('script_location', str(backend / 'migrations'))
    cfg.attributes['settings_override'] = settings
    command.upgrade(cfg, '0009')
    engine, _ = make_database(settings)
    try:
        with engine.begin() as db:
            db.execute(text("INSERT INTO sources (id,name,url,kind,enabled,failure_count) VALUES ('legacy','中文来源','https://example.invalid','rss',1,0)"))
            db.execute(text("INSERT INTO fetch_runs (id,source_id,started_at,status,counts,error) VALUES ('old','legacy','2026-01-01T00:00:00Z','running','{}','保留原记录')"))
        for revision in ('0010', '0009', '0010'):
            if revision == '0009':
                command.downgrade(cfg, revision)
            else:
                command.upgrade(cfg, revision)
            with engine.connect() as db:
                assert db.execute(text('SELECT id,source_id,status,error FROM fetch_runs')).one() == ('old','legacy','running','保留原记录')
                assert db.scalar(text('PRAGMA foreign_key_check')) is None
                if revision == '0010':
                    assert db.execute(text('SELECT job_run_id,job_lease_token FROM fetch_runs')).one() == (None,None)
                    assert any(i['name'] == 'ix_fetch_runs_job_attempt' for i in inspect(db).get_indexes('fetch_runs'))
    finally:
        engine.dispose()
