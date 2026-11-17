import importlib.util
from pathlib import Path
from types import SimpleNamespace

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "compose.py"
spec = importlib.util.spec_from_file_location("pulse_compose", SCRIPT)
compose = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compose)


def test_no_docker_is_actionable(monkeypatch, capsys):
    monkeypatch.setattr(compose.shutil, "which", lambda _: None)
    assert compose.main(["--", "up", "-d"]) == 2
    assert "run_local.py" in capsys.readouterr().err


def test_missing_plugin_does_not_run_up(monkeypatch, capsys):
    monkeypatch.setattr(compose.shutil, "which", lambda _: "/usr/bin/docker")
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=1)
    monkeypatch.setattr(compose.subprocess, "run", run)
    assert compose.main(["--", "up", "-d"]) == 2
    assert calls == [["/usr/bin/docker", "compose", "version"]]
    assert "do not pass -p" in capsys.readouterr().err


def test_command_boundaries_and_exit_code(monkeypatch, tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / ".env").write_text("ADMIN_TOKEN=not-printed")
    monkeypatch.setattr(compose, "__file__", str(tmp_path / "scripts" / "compose.py"))
    monkeypatch.setattr(compose.shutil, "which", lambda _: "/usr/bin/docker")
    calls = []
    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0 if len(calls) == 1 else 7)
    monkeypatch.setattr(compose.subprocess, "run", run)
    assert compose.main(["--postgres", "--", "config", "--quiet"]) == 7
    assert calls[1][0] == ["/usr/bin/docker", "compose", "--env-file", str(tmp_path / ".env"),
                           "-p", "ai-pulse", "-f", str(tmp_path / "compose.yaml"),
                           "-f", str(tmp_path / "compose.postgres.yaml"), "config", "--quiet"]
    assert calls[1][1] == {"cwd": tmp_path}


def test_missing_env_does_not_start_compose(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(compose, "__file__", str(tmp_path / "scripts" / "compose.py"))
    monkeypatch.setattr(compose.shutil, "which", lambda _: "/usr/bin/docker")
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(compose.subprocess, "run", run)
    assert compose.main(["--", "up"]) == 2
    assert len(calls) == 1
    assert "create_compose_env.py" in capsys.readouterr().err
