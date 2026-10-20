"""Manual, non-mutating live source canary with bounded response sizes."""
from __future__ import annotations
import argparse
import json
import time
import urllib.error
import urllib.request
from app.bootstrap import DEFAULT_SOURCES
from app.feeds import parse_feed

# Keep the probe and the ingestion pipeline on exactly the same endpoints.
SOURCES = tuple((key, url, kind) for key, _, url, kind in DEFAULT_SOURCES)
MAX_BYTES = 2_000_000


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def check_source(name: str, url: str, kind: str, timeout: float = 12) -> dict:
    started = time.monotonic()
    record = {"source": name, "status": "failed", "http_status": None, "items": 0,
              "error_code": None, "elapsed_ms": None}
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "AI-Pulse-Canary/1.0",
            "Accept": "application/rss+xml, application/atom+xml, application/json, */*",
        })
        opener = urllib.request.build_opener(NoRedirect(), urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=timeout) as response:
            record["http_status"] = response.status
            payload = response.read(MAX_BYTES + 1)
            if len(payload) > MAX_BYTES:
                raise ValueError("response_too_large")
            entries = parse_feed(payload, kind, limit=30)
        if entries:
            record.update(status="ok", items=len(entries))
        else:
            # Empty feeds can be legitimate, but are not ingestion success.
            record.update(status="empty", error_code="empty_feed")
    except urllib.error.HTTPError as exc:
        record["http_status"] = exc.code
        record["error_code"] = "http_error"
    except urllib.error.URLError:
        record["error_code"] = "network_error"
    except TimeoutError:
        record["error_code"] = "timeout"
    except ValueError as exc:
        record["error_code"] = "response_too_large" if str(exc) == "response_too_large" else "parse_error"
    except Exception:
        record["error_code"] = "unexpected_error"
    finally:
        record["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    return record


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="source-canary.json")
    parser.add_argument("--timeout", type=float, default=12)
    args = parser.parse_args(argv)
    if not 1 <= args.timeout <= 20:
        parser.error("timeout must be between 1 and 20 seconds")
    results = [check_source(name, url, kind, args.timeout) for name, url, kind in SOURCES]
    report = {"schema_version": 2, "live": True, "paid_model_calls": 0,
              "successful_sources": sum(x["status"] == "ok" for x in results),
              "sources": results}
    from pathlib import Path
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["successful_sources"] == len(SOURCES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
