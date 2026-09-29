"""Deterministic evidence chunking and pgvector-safe embedding persistence."""
from __future__ import annotations

import math
from sqlalchemy import delete, select, text
from .models import EvidenceChunk, Snapshot
from .textutil import digest
from .timeutil import iso, utcnow


BOUNDARIES = "\n。！？.!?;；"


def chunk_text(value: str, target_chars: int = 2400, overlap_chars: int = 320) -> list[dict]:
    """Split immutable snapshot text while preserving exact character offsets."""
    if target_chars < 200 or overlap_chars < 0 or overlap_chars >= target_chars:
        raise ValueError("Invalid chunk window")
    if not value:
        return []
    result = []
    start = 0
    ordinal = 0
    size = len(value)
    while start < size:
        hard_end = min(size, start + target_chars)
        end = hard_end
        if hard_end < size:
            floor = start + int(target_chars * 0.6)
            candidates = [value.rfind(mark, floor, hard_end) for mark in BOUNDARIES]
            boundary = max(candidates)
            if boundary >= floor:
                end = boundary + 1
        if end <= start:
            end = hard_end
        piece = value[start:end]
        result.append({
            "ordinal": ordinal,
            "text": piece,
            "text_hash": digest(piece),
            "start_offset": start,
            "end_offset": end,
        })
        ordinal += 1
        if end >= size:
            break
        start = max(start + 1, end - overlap_chars)
    return result


def ensure_snapshot_chunks(db, snapshot: Snapshot) -> list[EvidenceChunk]:
    existing = list(db.scalars(select(EvidenceChunk).where(
        EvidenceChunk.snapshot_id == snapshot.id
    ).order_by(EvidenceChunk.ordinal)))
    if existing:
        return existing
    rows = []
    for item in chunk_text(snapshot.text):
        row = EvidenceChunk(snapshot_id=snapshot.id, **item)
        db.add(row)
        rows.append(row)
    db.flush()
    return rows


def rebuild_snapshot_chunks(db, snapshot: Snapshot) -> list[EvidenceChunk]:
    db.execute(delete(EvidenceChunk).where(EvidenceChunk.snapshot_id == snapshot.id))
    db.flush()
    return ensure_snapshot_chunks(db, snapshot)


def _validated_vector(values: list[float]) -> list[float]:
    if not values or len(values) > 16000:
        raise ValueError("Embedding dimension must be between 1 and 16000")
    result = [float(v) for v in values]
    if not all(math.isfinite(v) for v in result):
        raise ValueError("Embedding values must be finite")
    return result


def vector_literal(values: list[float]) -> str:
    clean = _validated_vector(values)
    return "[" + ",".join(format(v, ".9g") for v in clean) + "]"


def write_embedding(
    db,
    chunk: EvidenceChunk,
    values: list[float],
    model: str,
    *,
    provider: str = "fixture",
    revision: str | None = None,
) -> None:
    clean = _validated_vector(values)
    if not provider or len(provider) > 60:
        raise ValueError("Embedding provider is required")
    if not model or len(model) > 120:
        raise ValueError("Embedding model is required")
    if revision is not None and len(revision) > 120:
        raise ValueError("Embedding revision is too long")
    chunk.embedding_json = clean
    chunk.embedding_provider = provider
    chunk.embedding_model = model
    chunk.embedding_revision = revision
    chunk.embedding_dim = len(clean)
    chunk.embedded_at = iso(utcnow())
    db.flush()
    if db.bind.dialect.name == "postgresql":
        db.execute(text("""
            UPDATE evidence_chunks
            SET embedding_vector = CAST(:embedding AS vector)
            WHERE id = :chunk_id
        """), {"embedding": vector_literal(clean), "chunk_id": chunk.id})
