"""Internal one-shot worker. Never expose this module as an HTTP endpoint.

Only a small allow-listed error code is emitted on stderr. Upstream bodies, URLs,
headers, exception strings and credentials are never returned to the parent.
"""
import json
import socket
import sys
import httpx
from .config import Settings
from .fetcher import SafeFetcher, FetchError

SAFE_CODES = {
    "dns_error", "connect_timeout", "read_timeout", "connect_error",
    "http_status", "policy_blocked", "private_destination",
    "invalid_redirect", "response_too_large", "redirect_budget_exceeded",
    "fetch_error", "unexpected_fetch_error",
}

def classify(exc: Exception) -> str:
    if isinstance(exc, socket.gaierror):
        return "dns_error"
    if isinstance(exc, httpx.ConnectTimeout):
        return "connect_timeout"
    if isinstance(exc, httpx.ReadTimeout):
        return "read_timeout"
    if isinstance(exc, httpx.ConnectError):
        return "connect_error"
    if isinstance(exc, httpx.HTTPStatusError):
        return "http_status"
    if isinstance(exc, FetchError):
        return exc.code if exc.code in SAFE_CODES else "fetch_error"
    return "unexpected_fetch_error"

def main():
    try:
        raw = sys.stdin.buffer.read(16_385)
        if len(raw) > 16_384:
            return 2
        request = json.loads(raw)
        settings = Settings(_env_file=None, **request["settings"])
        data = SafeFetcher(settings).fetch(request["url"])
        sys.stdout.buffer.write(data)
        return 0
    except Exception as exc:
        sys.stderr.write(json.dumps({"code": classify(exc)}))
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
