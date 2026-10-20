import json
import urllib.error
from io import BytesIO
from scripts import source_canary as canary


def test_canary_success(monkeypatch):
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self, n): return b"<rss><channel><item><title>A</title><link>https://example.org/a</link><description>Text</description></item></channel></rss>"
    class Opener:
        def open(self, request, timeout): return Response()
    monkeypatch.setattr(canary.urllib.request, "build_opener", lambda *args: Opener())
    result = canary.check_source("fixture", "https://example.org/feed", "rss")
    assert result["status"] == "ok" and result["items"] == 1
    assert result["elapsed_ms"] >= 0


def test_canary_network_error_is_sanitized(monkeypatch):
    class Opener:
        def open(self, request, timeout):
            raise urllib.error.URLError("secret.example.invalid private-token")
    monkeypatch.setattr(canary.urllib.request, "build_opener", lambda *args: Opener())
    result = canary.check_source("fixture", "https://example.org/feed", "rss")
    assert result["error_code"] == "network_error"
    assert "secret" not in json.dumps(result)


def test_canary_redirect_is_blocked():
    handler = canary.NoRedirect()
    assert handler.redirect_request(None, None, 302, "redirect", {}, "https://example.org/") is None
