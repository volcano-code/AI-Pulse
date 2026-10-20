import pytest
from sqlalchemy import text
from app.db import make_database
from scripts.e2e_server import child_env, e2e_settings, initialize


def test_e2e_ignores_ambient_paid_credentials(monkeypatch, tmp_path):
    for key in ("LLM_API_KEY", "EMBEDDING_API_KEY", "SMTP_PASSWORD", "RANDOM_SECRET"):
        monkeypatch.setenv(key, "DO-NOT-INHERIT")
    monkeypatch.setenv("DATA_MODE", "live")
    monkeypatch.setenv("DATABASE_URL", "postgresql://production.invalid/customer")
    settings = e2e_settings(tmp_path)
    assert settings.data_mode == "replay" and settings.llm_mode == "extractive"
    assert settings.task_mode == "durable" and settings.delivery_transport == "file"
    assert "DO-NOT-INHERIT" not in str(child_env(settings))
    assert "production.invalid" not in settings.database_url
    assert not settings.allowed_fetch_hosts


@pytest.mark.parametrize("url", [
    "postgresql+psycopg://user:password@external.example/ai_pulse_e2e",
    "postgresql+psycopg://user:password@localhost/production",
    "postgresql+psycopg://user:password@localhost/ai_pulse_e2e?host=external.example",
    "sqlite:///existing.db", "not-a-url",
])
def test_e2e_refuses_non_isolated_database(tmp_path, url):
    with pytest.raises(ValueError, match="fresh loopback"):
        e2e_settings(tmp_path, url)


def test_e2e_refuses_nonempty_database_without_dropping_tables(tmp_path):
    settings = e2e_settings(tmp_path)
    engine, _ = make_database(settings)
    with engine.begin() as db: db.execute(text("CREATE TABLE keep_me(id INTEGER)"))
    with pytest.raises(ValueError, match="non-empty"):
        initialize(settings)
    with engine.connect() as db: assert db.execute(text("SELECT count(*) FROM keep_me")).scalar() == 0
    engine.dispose()
