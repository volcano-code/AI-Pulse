"""A hard wall-clock boundary around DNS, redirects and response reading.

Only the child performs HTTP. The parent never receives provider credentials from
it and never retries implicitly. subprocess.run kills/waits for a timed-out child.
This does NOT replace network-level egress controls against DNS rebinding.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
from .fetcher import FetchError


class FetchDeadlineExceeded(FetchError):
    def __init__(self, message: str = "Source process exceeded its wall-clock budget"):
        super().__init__(message, "deadline_exceeded")


class ProcessFetcher:
    def __init__(self, settings):
        self.settings = settings

    def fetch(self, url: str) -> bytes:
        s = self.settings
        request = {"url": url, "settings": {
            "allowed_fetch_hosts": s.allowed_fetch_hosts,
            "max_fetch_bytes": s.max_fetch_bytes,
            "fetch_timeout_seconds": s.fetch_timeout_seconds,
            "fetch_user_agent": s.fetch_user_agent,
        }}
        # Deliberately do not forward .env, model keys, mail credentials or proxies.
        environment = {k: v for k, v in os.environ.items()
                       if k in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL"}}
        environment["PYTHONUTF8"] = "1"
        try:
            result = subprocess.run(
                [sys.executable, "-m", "app.fetch_process"],
                input=json.dumps(request).encode(), stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, cwd=Path(__file__).resolve().parents[1],
                env=environment, timeout=s.fetch_deadline_seconds, check=False,
            )
        except subprocess.TimeoutExpired:
            raise FetchDeadlineExceeded() from None
        if result.returncode:
            code = "isolated_fetch_failed"
            try:
                diagnostic = json.loads((result.stderr or b"")[:256])
                candidate = diagnostic.get("code")
                if isinstance(candidate, str) and candidate.replace("_", "").isalnum():
                    code = candidate[:64]
            except (ValueError, TypeError, AttributeError):
                pass
            raise FetchError("Isolated source fetch failed; upstream details were suppressed", code)
        if not result.stdout or len(result.stdout) > s.max_fetch_bytes:
            raise FetchError("Empty response or response exceeds byte budget", "invalid_child_output")
        return result.stdout
