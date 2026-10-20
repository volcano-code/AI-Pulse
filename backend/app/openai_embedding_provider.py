"""Bounded OpenAI-compatible embedding transport; no implicit retries."""
from __future__ import annotations

import json
from urllib.parse import urlsplit

import httpx

from .embedding_gateway import EmbeddingBatch, EmbeddingPurpose

MAX_RESPONSE_BYTES = 2_000_000


class OpenAIEmbeddingProvider:
    provider = "openai"

    def __init__(self, *, api_key: str, model: str, dimensions: int,
                 base_url: str = "https://api.openai.com/v1",
                 timeout_seconds: float = 20.0, transport=None):
        parsed = urlsplit(base_url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment
                or parsed.port not in (None, 443)):
            raise ValueError("Trusted HTTPS embedding base URL required")
        # Custom endpoints are intentionally disallowed for real traffic until
        # they have an explicit allowlist and network egress policy.
        if transport is None and parsed.hostname != "api.openai.com":
            raise ValueError("Embedding provider host is not allowed")
        if not api_key or not model:
            raise ValueError("OpenAI embedding API key and model are required")
        if dimensions < 1 or dimensions > 16000:
            raise ValueError("Invalid embedding dimensions")
        if not 0 < timeout_seconds <= 60:
            raise ValueError("Invalid embedding timeout")
        self.api_key, self.model, self.dimensions = api_key, model, dimensions
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    def embed(self, texts: list[str], *, purpose: EmbeddingPurpose) -> EmbeddingBatch:
        if purpose not in ("document", "query"):
            raise ValueError("Unsupported embedding purpose")
        if not texts or len(texts) > 128 or any(not isinstance(x, str) or not x.strip() for x in texts):
            raise ValueError("Embedding batch must contain 1..128 non-empty strings")
        payload = {
            "input": texts,
            "model": self.model,
            "encoding_format": "float",
            "dimensions": self.dimensions,
        }
        request_id = None
        try:
            with httpx.Client(timeout=self.timeout_seconds, transport=self.transport,
                              trust_env=False, follow_redirects=False) as client:
                with client.stream(
                    "POST", self.base_url + "/embeddings",
                    headers={"Authorization": "Bearer " + self.api_key,
                             "Content-Type": "application/json"},
                    json=payload,
                ) as response:
                    request_id = response.headers.get("x-request-id")
                    if response.status_code >= 300:
                        raise RuntimeError(
                            f"Embedding provider HTTP {response.status_code}; "
                            f"request_id={request_id or 'unknown'}"
                        )
                    chunks, total = [], 0
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > MAX_RESPONSE_BYTES:
                            raise RuntimeError("Embedding response exceeds byte budget")
                        chunks.append(chunk)
            body = json.loads(b"".join(chunks))
            ordered = sorted(body["data"], key=lambda row: row["index"])
            vectors = [row["embedding"] for row in ordered]
            usage = body.get("usage") or {}
        except RuntimeError:
            raise
        except Exception:
            raise RuntimeError(
                "Embedding request failed; billing may be unknown; no automatic retry"
            ) from None
        return EmbeddingBatch(
            vectors=vectors,
            provider=self.provider,
            model=body.get("model") or self.model,
            dimensions=self.dimensions,
            revision=None,
            input_tokens=usage.get("prompt_tokens"),
            request_id=request_id,
        )
