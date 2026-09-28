"""Hybrid retrieval primitives.

The lexical path remains the safe fallback. Native vector search is enabled only
on PostgreSQL when a query embedding is explicitly supplied; no hidden provider
call happens in this module.
"""
from __future__ import annotations

from collections import defaultdict
from sqlalchemy import text
from .embeddings import vector_literal


def reciprocal_rank_fusion(rankings: list[list[str]], k: int = 60, limit: int = 20) -> list[tuple[str, float]]:
    if k <= 0 or limit <= 0:
        raise ValueError("k and limit must be positive")
    scores: dict[str, float] = defaultdict(float)
    for ranking in rankings:
        seen = set()
        for rank, item_id in enumerate(ranking, start=1):
            if item_id in seen:
                continue
            seen.add(item_id)
            scores[item_id] += 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:limit]


def vector_candidates(db, query_vector: list[float], embedding_model: str, limit: int = 50) -> list[dict]:
    """Exact cosine search. ANN indexes are intentionally not introduced yet."""
    if db.bind.dialect.name != "postgresql":
        return []
    if not embedding_model or limit < 1 or limit > 100:
        raise ValueError("Invalid vector search request")
    rows = db.execute(text("""
        SELECT id, snapshot_id, ordinal, text, start_offset, end_offset,
               1 - (embedding_vector <=> CAST(:query AS vector)) AS similarity
        FROM evidence_chunks
        WHERE embedding_vector IS NOT NULL
          AND embedding_model = :model
          AND embedding_dim = :dim
        ORDER BY embedding_vector <=> CAST(:query AS vector), id
        LIMIT :limit
    """), {
        "query": vector_literal(query_vector),
        "model": embedding_model,
        "dim": len(query_vector),
        "limit": limit,
    }).mappings()
    return [dict(row) for row in rows]


def retrieval_status(requested_mode: str, *, vector_available: bool) -> dict:
    if requested_mode == "lexical":
        return {"requested_mode": "lexical", "effective_mode": "lexical", "degraded": False, "degraded_reason": None}
    if requested_mode != "hybrid":
        raise ValueError("Unsupported retrieval mode")
    if not vector_available:
        return {"requested_mode": "hybrid", "effective_mode": "lexical", "degraded": True,
                "degraded_reason": "embedding_unavailable"}
    return {"requested_mode": "hybrid", "effective_mode": "hybrid", "degraded": False, "degraded_reason": None}
