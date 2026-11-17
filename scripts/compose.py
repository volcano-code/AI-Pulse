"""Invoke Compose v2 with explicit project/files, without shell aliases or secrets."""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description="AI Pulse Compose v2 wrapper. Example: --postgres -- up -d --build")
    parser.add_argument("--postgres", action="store_true", help="Use the PostgreSQL/pgvector override")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        parser.error("Supply a Compose command after -- (for example: -- config --quiet)")
    docker = shutil.which("docker")
    if not docker:
        print("Docker is missing. Install Docker with the Compose v2 plugin, or use scripts/run_local.py.", file=sys.stderr)
        return 2
    try:
        probe = subprocess.run([docker, "compose", "version"], capture_output=True, text=True)
    except OSError:
        print("Docker could not be executed. Check the installation and executable permissions.", file=sys.stderr)
        return 2
    if probe.returncode:
        print("Docker Compose v2 is unavailable. Verify `docker compose version`; do not pass -p directly to docker.", file=sys.stderr)
        return 2
    root = Path(__file__).resolve().parents[1]
    if not (root / ".env").is_file():
        print("Missing project .env. Run python scripts/create_compose_env.py; it will not overwrite an existing file.", file=sys.stderr)
        return 2
    invocation = [docker, "compose", "--env-file", str(root / ".env"), "-p", "ai-pulse", "-f", str(root / "compose.yaml")]
    if args.postgres:
        invocation += ["-f", str(root / "compose.postgres.yaml")]
    # Preserve argument boundaries. Never echo expanded environment or config.
    return subprocess.run(invocation + command, cwd=root).returncode


if __name__ == "__main__":
    raise SystemExit(main())
