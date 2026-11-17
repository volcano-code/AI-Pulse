"""Run AI Pulse locally. Durable mode starts a separate worker process."""
import argparse
import importlib.util
import os
from pathlib import Path
import socket
import subprocess
import sys


def backup_before_migration(database_url: str, backend: Path) -> Path | None:
    """Back up an existing SQLite database unless it is already at source head.

    Call before Alembic or any app initialization. Fail closed on backup errors;
    this is an offline migration safeguard, not a hot backup/restore workflow.
    """
    from contextlib import closing
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from datetime import datetime, timezone
    from sqlalchemy.engine import make_url
    import sqlite3

    url = make_url(database_url)
    if not url.drivername.startswith("sqlite") or not url.database or url.database == ":memory:":
        return None
    if "uri" in url.query or url.database.startswith("file:"):
        raise ValueError("SQLite URI databases are not supported by the safe local launcher; use a plain file path")
    existing = Path(url.database)
    if not existing.is_absolute():
        existing = backend / existing
    if not existing.is_file():
        return None
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "migrations"))
    target_heads = set(ScriptDirectory.from_config(config).get_heads())
    if not target_heads:
        raise RuntimeError("No migration head found; refusing an unprotected migration")
    with closing(sqlite3.connect(existing)) as source:
        try:
            has_version = source.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='alembic_version'").fetchone()
            versions = {row[0] for row in source.execute("SELECT version_num FROM alembic_version")} if has_version else set()
            if versions == target_heads:
                return None
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            backup = existing.with_name(existing.name + "." + stamp + ".bak")
            # Exclusive reservation refuses an improbable filename collision;
            # restrictive mode avoids making copied private data world-readable.
            fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
            try:
                with closing(sqlite3.connect(backup)) as target:
                    source.backup(target)
                    if target.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                        raise RuntimeError("SQLite backup integrity check failed")
            except BaseException:
                backup.unlink(missing_ok=True)  # only this newly created failed copy
                raise
            return backup
        except sqlite3.DatabaseError as exc:
            raise RuntimeError("SQLite backup failed; migration was not started") from exc


def main():
    parser=argparse.ArgumentParser(description="Start AI Pulse on http://127.0.0.1:8000")
    parser.add_argument("--demo",action="store_true",help="Explicit synthetic replay data in a separate database")
    parser.add_argument("--durable",action="store_true",help="Start the database-backed worker for daily scheduling and outbox")
    parser.add_argument("--database-url",default="",help="Optional custom database; data mode is checked at startup")
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    with socket.socket() as sock:
        if sock.connect_ex(("127.0.0.1",8000))==0:
            raise SystemExit("Port 8000 is already in use. Stop the existing process before starting or reseeding.")
    required=["fastapi","pydantic_settings","sqlalchemy","httpx","defusedxml","uvicorn","alembic"]
    missing=[m for m in required if importlib.util.find_spec(m) is None]
    if missing:
        raise SystemExit("Missing dependencies: "+", ".join(missing)+". Install backend/requirements.txt in your virtual environment.")
    mode="replay" if args.demo else "live"
    dbfile=root/"data"/f"{mode}.db"
    dbfile.parent.mkdir(exist_ok=True)
    env={**os.environ,"DATA_MODE":mode,"DATABASE_URL":args.database_url or f"sqlite:///{dbfile.as_posix()}"}
    env["TASK_MODE"] = "durable" if args.durable else "local"
    env["OUTBOX_DIR"] = os.environ.get("OUTBOX_DIR", str(root / "data" / "outbox" / mode))
    if args.demo:
        env["LLM_MODE"]="extractive"
        env["DELIVERY_TRANSPORT"]="file"
    backend=root/"backend"
    backup = backup_before_migration(env["DATABASE_URL"], backend)
    if backup:
        print(f"Pre-migration SQLite backup: {backup}", flush=True)
    subprocess.run([sys.executable,"-m","alembic","upgrade","head"],cwd=backend,env=env,check=True)
    subprocess.run([sys.executable,"-m","app.cli","demo" if args.demo else "init"],cwd=backend,env=env,check=True)
    print(f"\nAI Pulse | data={mode} | http://127.0.0.1:8000 | Ctrl+C to stop",flush=True)
    children = []
    try:
        if args.durable:
            children.append(subprocess.Popen([sys.executable,"-m","app.worker"],cwd=backend,env=env))
        server = subprocess.Popen([sys.executable,"-m","uvicorn","app.main:create_app","--factory","--host","127.0.0.1","--port","8000","--workers","1"],cwd=backend,env=env)
        children.append(server)
        return server.wait()
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=8)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()

if __name__=="__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
