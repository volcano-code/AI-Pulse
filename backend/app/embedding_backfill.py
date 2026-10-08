"""Bounded, idempotent corpus embedding backfill."""
from __future__ import annotations

from sqlalchemy import or_, select
from .embedding_gateway import EmbeddingGateway
from .embeddings import write_embedding
from .models import EvidenceChunk, Snapshot, Article


def backfill_embeddings(
    db,
    gateway: EmbeddingGateway,
    *,
    batch_size: int = 32,
    max_chunks: int = 500,
    force: bool = False,
    data_mode: str | None = None,
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

    stmt = select(EvidenceChunk)
    if data_mode is not None:
        if data_mode not in ("live", "replay"):
            raise ValueError("Invalid data mode")
        stmt = (stmt.join(Snapshot, EvidenceChunk.snapshot_id == Snapshot.id)
                .join(Article, Snapshot.article_id == Article.id)
                .where(Article.data_mode == data_mode))
    stmt = stmt.order_by(EvidenceChunk.id).limit(max_chunks)
    if not force:
        stmt = stmt.where(or_(
            EvidenceChunk.embedding_provider.is_(None),
            EvidenceChunk.embedding_provider != provider,
            EvidenceChunk.embedding_model != model,
            EvidenceChunk.embedding_dim != dimensions,
        ))
    rows = list(db.scalars(stmt))
    if provider != "fixture" and data_mode != "live":
        raise ValueError("Paid embedding requires an explicit live-only scope")
    embedded = 0
    request_ids = []
    input_tokens = 0
    token_usage_complete = True
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
        if result.input_tokens is None:
            token_usage_complete = False
        else:
            input_tokens += result.input_tokens

    return {
        "provider": provider,
        "model": model,
        "dimensions": dimensions,
        "selected": len(rows),
        "embedded": embedded,
        "request_ids": request_ids,
        "input_tokens": input_tokens if token_usage_complete else None,
        "cost_usd": None,
        "complete": len(rows) < max_chunks,
    }
