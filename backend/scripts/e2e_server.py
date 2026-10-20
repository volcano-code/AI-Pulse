"""Own disposable replay API + SQL worker for native Playwright tests.

No production DB, paid key or real delivery is inherited. PostgreSQL is opt-in
via AI_PULSE_E2E_DATABASE_URL and must be a fresh loopback ai_pulse_e2e database.
"""
from __future__ import annotations
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from sqlalchemy import inspect
from sqlalchemy.engine import make_url
import httpx
from app.bootstrap import bootstrap
from app.briefing import build_brief
from app.config import Settings
from app.db import make_database
from app.demo import seed_demo
from scripts.live_pipeline import isolated_settings, migrate

BACKEND = Path(__file__).resolve().parents[1]


def e2e_settings(directory: Path, database_url: str | None = None) -> Settings:
    safe = isolated_settings(directory, "replay").model_dump()
    if database_url:
        try:
            url = make_url(database_url)
            valid = (url.drivername == "postgresql+psycopg"
                     and url.host in {"127.0.0.1", "localhost", "::1"}
                     and url.database == "ai_pulse_e2e" and not url.query)
        except Exception:
            valid = False
        if not valid:
            raise ValueError("E2E PostgreSQL must be the fresh loopback ai_pulse_e2e database")
        safe["database_url"] = database_url
    safe.update(task_mode="durable", worker_poll_seconds=0.1,
                smtp_password="", smtp_username="", smtp_host="",
                allowed_fetch_hosts="", outbox_dir=str(directory / "outbox"))
    return Settings(_env_file=None, **safe)


def initialize(settings: Settings) -> None:
    engine, factory = make_database(settings)
    try:
        if inspect(engine).get_table_names():
            raise ValueError("E2E refuses a non-empty database; no tables are dropped")
        migrate(settings)
        bootstrap(factory, settings)
        seed_demo(factory, settings)
        build_brief(factory, settings)
    finally:
        engine.dispose()


def child_env(settings: Settings) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key in {
        "PATH", "HOME", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL",
        "PYTHONPATH", "LD_LIBRARY_PATH"}}
    env.update({key.upper(): str(value) for key, value in settings.model_dump().items()})
    env["PYTHONUTF8"] = "1"
    return env


def main() -> int:
    stopped = False
    def stop(*_):
        nonlocal stopped
        stopped = True
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, stop)
    children = []
    directory = None
    try:
        directory = tempfile.TemporaryDirectory(prefix="ai-pulse-e2e-")
        temp = directory.name
        settings = e2e_settings(Path(temp), os.environ.get("AI_PULSE_E2E_DATABASE_URL"))
        initialize(settings)
        env = child_env(settings)
        api = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:create_app", "--factory",
                                "--host", "127.0.0.1", "--port", "8000"], cwd=BACKEND, env=env)
        children.append(api)
        # API bootstrap must finish before the worker starts using the same DB.
        deadline = time.monotonic() + 30
        with httpx.Client(trust_env=False, timeout=1) as client:
            while not stopped and time.monotonic() < deadline:
                if api.poll() is not None: raise RuntimeError("E2E API exited before readiness")
                try:
                    if client.get("http://127.0.0.1:8000/health").status_code == 200: break
                except httpx.HTTPError: pass
                time.sleep(0.1)
            else:
                raise RuntimeError("E2E API readiness deadline exceeded")
        children.append(subprocess.Popen([sys.executable, "-m", "app.worker"], cwd=BACKEND, env=env))
        print("E2E replay API + durable worker ready; no paid APIs or SMTP", flush=True)
        while not stopped:
            if any(child.poll() is not None for child in children):
                raise RuntimeError("E2E child exited unexpectedly")
            time.sleep(0.2)
        return 0
    except Exception:
        print("E2E setup/runtime failed; configuration and credentials suppressed", file=sys.stderr)
        return 1
    finally:
        for child in reversed(children):
            if child.poll() is None: child.terminate()
        for child in reversed(children):
            try: child.wait(timeout=5)
            except subprocess.TimeoutExpired: child.kill(); child.wait()
        if directory is not None:
            directory.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
