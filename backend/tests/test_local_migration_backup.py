import importlib.util
import os
from pathlib import Path
import sqlite3

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

BACKEND = Path(__file__).resolve().parents[1]
SCRIPT = BACKEND.parent / "scripts" / "run_local.py"
spec = importlib.util.spec_from_file_location("pulse_local", SCRIPT)
local = importlib.util.module_from_spec(spec)
spec.loader.exec_module(local)


def config(database):
    value = Config(str(BACKEND / "alembic.ini"))
    value.set_main_option("script_location", str(BACKEND / "migrations"))
    value.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    return value


def make_db(path, revision=None):
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE important_data (value TEXT)")
        db.execute("INSERT INTO important_data VALUES ('中文 evidence')")
        if revision:
            db.execute("CREATE TABLE alembic_version (version_num TEXT)")
            db.execute("INSERT INTO alembic_version VALUES (?)", (revision,))


@pytest.mark.parametrize("revision", [None, "0003", "0008", "unrecognized-revision"])
def test_old_or_unknown_database_gets_verified_backup(tmp_path, revision):
    source = tmp_path / "source.db"
    make_db(source, revision)
    backup = local.backup_before_migration(f"sqlite:///{source}", BACKEND)
    assert backup and backup.is_file()
    with sqlite3.connect(backup) as db:
        assert db.execute("SELECT value FROM important_data").fetchone() == ("中文 evidence",)
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    if os.name == "posix":
        assert backup.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(source) as db:
        db.execute("DELETE FROM important_data")
    with sqlite3.connect(backup) as db:
        assert db.execute("SELECT COUNT(*) FROM important_data").fetchone() == (1,)


def test_current_head_does_not_create_repeated_backups(tmp_path):
    source = tmp_path / "current.db"
    head = ScriptDirectory.from_config(config(source)).get_current_head()
    make_db(source, head)
    assert local.backup_before_migration(f"sqlite:///{source}", BACKEND) is None
    assert not list(tmp_path.glob("*.bak"))


@pytest.mark.parametrize("url", ["sqlite:///:memory:", "postgresql+psycopg://example.invalid/pulse", "sqlite:///missing.db"])
def test_no_file_to_backup_does_not_create_one(tmp_path, url):
    assert local.backup_before_migration(url, tmp_path) is None
    assert list(tmp_path.iterdir()) == []


def test_corrupt_database_fails_closed(tmp_path):
    source = tmp_path / "broken.db"
    source.write_bytes(b"not a sqlite database")
    with pytest.raises(RuntimeError, match="migration was not started"):
        local.backup_before_migration(f"sqlite:///{source}", BACKEND)
    assert source.read_bytes() == b"not a sqlite database"
    assert not list(tmp_path.glob("*.bak"))


def test_backup_write_failure_propagates(tmp_path, monkeypatch):
    source = tmp_path / "source.db"
    make_db(source, "0003")
    def fail(*args, **kwargs):
        raise PermissionError("test-only unwritable destination")
    monkeypatch.setattr(local.os, "open", fail)
    with pytest.raises(PermissionError):
        local.backup_before_migration(f"sqlite:///{source}", BACKEND)
    assert not list(tmp_path.glob("*.bak"))


def test_actual_v0003_upgrade_preserves_backup_and_data(tmp_path, monkeypatch):
    source = tmp_path / "migration.db"
    # env.py reads DATABASE_URL; only this disposable test database is used.
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{source}")
    cfg = config(source)
    command.upgrade(cfg, "0003")
    with sqlite3.connect(source) as db:
        db.execute("CREATE TABLE preserved_test_marker (value TEXT)")
        db.execute("INSERT INTO preserved_test_marker VALUES ('keep me')")
    backup = local.backup_before_migration(f"sqlite:///{source}", BACKEND)
    assert backup
    command.upgrade(cfg, "head")
    with sqlite3.connect(backup) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone() == ("0003",)
        assert db.execute("SELECT value FROM preserved_test_marker").fetchone() == ("keep me",)
    with sqlite3.connect(source) as db:
        assert db.execute("SELECT value FROM preserved_test_marker").fetchone() == ("keep me",)
    assert local.backup_before_migration(f"sqlite:///{source}", BACKEND) is None


@pytest.mark.parametrize("suffix", ["?uri=true", "?uri=1"])
def test_sqlite_uri_cannot_bypass_backup(tmp_path, suffix):
    source = tmp_path / "uri.db"
    make_db(source, "0003")
    with pytest.raises(ValueError, match="SQLite URI databases"):
        local.backup_before_migration(f"sqlite:///file:{source}{suffix}", BACKEND)
    with sqlite3.connect(source) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone() == ("0003",)
    assert not list(tmp_path.glob("*.bak"))
