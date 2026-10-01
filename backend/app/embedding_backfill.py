"""Bounded, idempotent corpus embedding backfill."""
from __future__ import annotations

from sqlalchemy import or_, select
from .embedding_gateway import EmbeddingGateway
from .embeddings import write_embedding
from .models import EvidenceChunk


def backfill_embeddings(
    db,
    gateway: EmbeddingGateway,
    *,
    batch_size: int = 32,
    max_chunks: int = 500,
    force: bool = False,
) -> dict:
    if batch_size < 1 or batch_size > 128:
        raise ValueError("batch_size must be between 1 and 128")
    if max_chunks < 1 or max_chunks > 5000:
        raise ValueError("max_chunks must be between 1 and 5000")

    provider = getattr(gateway.provider, "provider", None)
    model = getattr(gateway.provider, "model", None)
    dimensions = getattr(gateway.provider, "dimensions", None)
    if not provider or not model or not dimensions:
        raise ValueError("Embedding provider must expose provider, model and dimensions")

    stmt = select(EvidenceChunk).order_by(EvidenceChunk.id).limit(max_chunks)
    if not force:
        stmt = stmt.where(or_(
            EvidenceChunk.embedding_provider.is_(None),
            EvidenceChunk.embedding_provider != provider,
            EvidenceChunk.embedding_model != model,
            EvidenceChunk.embedding_dim != dimensions,
        ))
    rows = list(db.scalars(stmt))
    embedded = 0
    request_ids = []
    for offset in range(0, len(rows), batch_size):
        batch_rows = rows[offset:offset + batch_size]
        result = gateway.embed_documents([row.text for row in batch_rows])
        if result.provider != provider or result.model != model or result.dimensions != dimensions:
            raise ValueError("Embedding provider identity changed during backfill")
        for row, vector in zip(batch_rows, result.vectors, strict=True):
            write_embedding(
                db, row, vector, result.model,
                provider=result.provider, revision=result.revision,
            )
            embedded += 1
        if result.request_id:
            request_ids.append(result.request_id)

    return {
        "provider": provider,
        "model": model,
        "dimensions": dimensions,
        "selected": len(rows),
        "embedded": embedded,
        "request_ids": request_ids,
        "complete": len(rows) < max_chunks,
    }
