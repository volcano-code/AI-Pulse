"""Shared bounded model transport and response accounting.

No automatic retry. Callers retain their own schema and evidence validation.
"""
from __future__ import annotations
import json
from urllib.parse import urlsplit
import httpx


class GatewayError(RuntimeError):
    pass


def post_chat_completion(*, base_url: str, api_key: str, payload: dict,
                         timeout: float, max_response_bytes: int = 1_000_000,
                         transport=None) -> dict:
    parsed = urlsplit(base_url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment):
        raise GatewayError("Trusted HTTPS model base URL required")
    if not api_key or not payload.get("model"):
        raise GatewayError("Model credentials and model ID are required")
    if not 0 < timeout <= 60 or not 0 < max_response_bytes <= 2_000_000:
        raise GatewayError("Invalid model request budget")
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False,
                          trust_env=False, transport=transport) as client:
            with client.stream("POST", base_url.rstrip("/") + "/chat/completions",
                               headers={"Authorization": "Bearer " + api_key},
                               json=payload) as response:
                response.raise_for_status()
                parts, size = [], 0
                for part in response.iter_bytes():
                    size += len(part)
                    if size > max_response_bytes:
                        raise GatewayError("Model response exceeds byte budget")
                    parts.append(part)
        result = json.loads(b"".join(parts))
        if not isinstance(result, dict) or not isinstance(result.get("choices"), list) or not result["choices"]:
            raise GatewayError("Invalid model response envelope")
        return result
    except GatewayError:
        raise
    except Exception:
        # Provider errors may echo credentials or source content; never propagate them.
        raise GatewayError("Model request failed; billing may be unknown; no automatic retry") from None


def safe_usage(result: dict) -> dict:
    usage = result.get("usage") or {}
    if not isinstance(usage, dict):
        usage = {}
    return {key: value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
            for key, value in ((k, usage.get(k)) for k in ("prompt_tokens", "completion_tokens"))}
