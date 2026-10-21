from pathlib import Path
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from app.db import make_database
from app.models import ProviderCall
from app.provider_calls import CallLedger, fingerprint


def test_migration_roundtrip_preserves_existing_tables_and_refuses_audit_loss(settings):
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "migrations"))
    config.attributes["settings_override"] = settings
    command.upgrade(config, "0006")
    engine, factory = make_database(settings)
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE keep_existing_data (id INTEGER PRIMARY KEY)"))
            conn.execute(text("INSERT INTO keep_existing_data VALUES (7)"))
        command.upgrade(config, "head")
        assert "provider_calls" in inspect(engine).get_table_names()
        command.downgrade(config, "-1")
        command.upgrade(config, "head")
        CallLedger(factory).reserve(operation_key="migration-fixture", request_hash=fingerprint("fixture"),
            kind="llm_canary", provider="fixture", model="fixture", data_mode="canary")
        with pytest.raises(RuntimeError, match="audit history"):
            command.downgrade(config, "-1")
        with engine.connect() as conn:
            assert conn.scalar(text("SELECT id FROM keep_existing_data")) == 7
            assert conn.scalar(text("SELECT count(*) FROM provider_calls")) == 1
    finally:
        engine.dispose()
