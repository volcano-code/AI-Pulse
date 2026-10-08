"""OpenAI embeddings adapter behind the provider-agnostic gateway.

No retry loop lives here yet: retries will be introduced only with explicit
budget accounting. The adapter has a hard timeout and never logs credentials.
"""
from __future__ import annotations

import httpx
from .embedding_gateway import EmbeddingBatch, EmbeddingPurpose


class OpenAIEmbeddingProvider:
    provider = "openai"

    def __init__(self, *, api_key: str, model: str, dimensions: int,
                 base_url: str = "https://api.openai.com/v1",
                 timeout_seconds: float = 20.0, transport=None):
        if not api_key or not model:
            raise ValueError("OpenAI embedding API key and model are required")
        if dimensions < 1 or dimensions > 16000:
            raise ValueError("Invalid embedding dimensions")
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
        with httpx.Client(timeout=self.timeout_seconds, transport=self.transport) as client:
            response = client.post(
                self.base_url + "/embeddings",
                headers={"Authorization": "Bearer " + self.api_key, "Content-Type": "application/json"},
                json=payload,
            )
        request_id = response.headers.get("x-request-id")
        if response.status_code >= 400:
            # Do not include response bodies: providers may echo request data.
            raise RuntimeError(f"Embedding provider HTTP {response.status_code}; request_id={request_id or 'unknown'}")
        try:
            body = response.json()
            ordered = sorted(body["data"], key=lambda row: row["index"])
            vectors = [row["embedding"] for row in ordered]
            usage = body.get("usage") or {}
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("Embedding provider returned an invalid response") from exc
        return EmbeddingBatch(
            vectors=vectors,
            provider=self.provider,
            model=body.get("model") or self.model,
            dimensions=self.dimensions,
            revision=None,
            input_tokens=usage.get("prompt_tokens"),
            request_id=request_id,
        )
