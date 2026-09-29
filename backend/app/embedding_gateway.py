"""Embedding provider boundary.

The gateway is intentionally provider-agnostic. Production adapters may perform
network I/O, while FixtureEmbeddingProvider keeps CI deterministic and offline.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import math
from typing import Literal, Protocol

EmbeddingPurpose = Literal["document", "query"]


@dataclass(frozen=True)
class EmbeddingBatch:
    vectors: list[list[float]]
    provider: str
    model: str
    dimensions: int
    revision: str | None = None
    input_tokens: int | None = None
    request_id: str | None = None


class EmbeddingProvider(Protocol):
    def embed(self, texts: list[str], *, purpose: EmbeddingPurpose) -> EmbeddingBatch: ...


class FixtureEmbeddingProvider:
    """Deterministic non-semantic vectors for contracts and CI only."""

    provider = "fixture"
    model = "fixture-sha256-v1"

    def __init__(self, dimensions: int = 8):
        if dimensions < 2 or dimensions > 256:
            raise ValueError("Fixture dimensions must be between 2 and 256")
        self.dimensions = dimensions

    def embed(self, texts: list[str], *, purpose: EmbeddingPurpose) -> EmbeddingBatch:
        if purpose not in ("document", "query"):
            raise ValueError("Unsupported embedding purpose")
        if not texts or len(texts) > 128:
            raise ValueError("Embedding batch must contain 1..128 texts")
        vectors = []
        for value in texts:
            if not isinstance(value, str) or not value.strip():
                raise ValueError("Embedding inputs must be non-empty strings")
            seed = sha256((purpose + "\0" + value).encode("utf-8")).digest()
            raw = [((seed[i % len(seed)] / 255.0) * 2.0 - 1.0) for i in range(self.dimensions)]
            norm = math.sqrt(sum(v * v for v in raw)) or 1.0
            vectors.append([v / norm for v in raw])
        return EmbeddingBatch(
            vectors=vectors,
            provider=self.provider,
            model=self.model,
            dimensions=self.dimensions,
            revision="fixture-v1",
        )


class EmbeddingGateway:
    def __init__(self, provider: EmbeddingProvider):
        self.provider = provider

    def embed_documents(self, texts: list[str]) -> EmbeddingBatch:
        return self._validated(self.provider.embed(texts, purpose="document"), len(texts))

    def embed_query(self, text: str) -> EmbeddingBatch:
        return self._validated(self.provider.embed([text], purpose="query"), 1)

    @staticmethod
    def _validated(batch: EmbeddingBatch, expected: int) -> EmbeddingBatch:
        if len(batch.vectors) != expected or batch.dimensions < 1:
            raise ValueError("Provider returned an invalid embedding batch")
        for vector in batch.vectors:
            if len(vector) != batch.dimensions or not all(math.isfinite(float(v)) for v in vector):
                raise ValueError("Provider returned an invalid embedding vector")
        if not batch.provider or not batch.model:
            raise ValueError("Provider identity is required")
        return batch
