"""Hybrid retrieval primitives and bounded fusion.

No provider call happens here. Callers must explicitly supply a query embedding.
If no embedding is available, the caller can expose a lexical degradation rather
than silently pretending vector retrieval ran.
"""
from __future__ import annotations

from collections import defaultdict
from sqlalchemy import select, text
from .embeddings import vector_literal
from .models import Article, Snapshot, Source
from .retrieval import RetrievalScope, lexical_candidates


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


def vector_candidates(
    db,
    query_vector: list[float],
    embedding_model: str,
    limit: int = 50,
    allowed_snapshot_ids: list[str] | None = None,
) -> list[dict]:
    """Exact cosine search. ANN indexes are intentionally not introduced yet.

    allowed_snapshot_ids is a hard evidence scope. Hybrid retrieval must not
    escape a historical brief or other caller-selected snapshot set.
    """
    if db.bind.dialect.name != "postgresql":
        return []
    if not embedding_model or limit < 1 or limit > 100:
        raise ValueError("Invalid vector search request")
    params = {
        "query": vector_literal(query_vector),
        "model": embedding_model,
        "dim": len(query_vector),
        "limit": limit,
    }
    scope = ""
    if allowed_snapshot_ids is not None:
        if not allowed_snapshot_ids:
            return []
        params["snapshot_ids"] = list(dict.fromkeys(allowed_snapshot_ids))
        scope = " AND snapshot_id = ANY(CAST(:snapshot_ids AS varchar[]))"
    rows = db.execute(text(f"""
        SELECT id, snapshot_id, ordinal, text, start_offset, end_offset,
               1 - (embedding_vector <=> CAST(:query AS vector)) AS similarity
        FROM evidence_chunks
        WHERE embedding_vector IS NOT NULL
          AND embedding_model = :model
          AND embedding_dim = :dim
          {scope}
        ORDER BY embedding_vector <=> CAST(:query AS vector), id
        LIMIT :limit
    """), params).mappings()
    return [dict(row) for row in rows]


def fuse_evidence(
    db,
    lexical_citations: list[dict],
    *,
    query_vector: list[float] | None,
    embedding_model: str | None,
    vector_limit: int = 50,
    final_limit: int = 5,
    allowed_snapshot_ids: list[str] | None = None,
) -> tuple[list[dict], dict]:
    """Fuse lexical snapshot ranking with exact vector chunk ranking.

    The lexical result defines the allowed evidence scope. This makes the
    function safe for historical briefs: vector search cannot retrieve a newer
    snapshot that was not part of the caller's lexical scope.
    """
    lexical_ids = list(dict.fromkeys(c["snapshot_id"] for c in lexical_citations))
    authorized_ids = list(dict.fromkeys(allowed_snapshot_ids)) if allowed_snapshot_ids is not None else lexical_ids
    vector_ready = bool(query_vector and embedding_model and db.bind.dialect.name == "postgresql")
    status = retrieval_status("hybrid", vector_available=vector_ready)
    if not vector_ready:
        return lexical_citations[:final_limit], status

    vectors = vector_candidates(
        db, query_vector, embedding_model, limit=vector_limit,
        allowed_snapshot_ids=authorized_ids,
    )
    vector_ids = list(dict.fromkeys(row["snapshot_id"] for row in vectors))
    fused = reciprocal_rank_fusion([lexical_ids, vector_ids], limit=final_limit)
    lexical_by_snapshot = {c["snapshot_id"]: c for c in lexical_citations}
    vector_by_snapshot = {}
    for row in vectors:
        vector_by_snapshot.setdefault(row["snapshot_id"], row)

    missing = [snapshot_id for snapshot_id, _ in fused if snapshot_id not in lexical_by_snapshot]
    metadata = {}
    if missing:
        rows = db.execute(
            select(Article, Snapshot, Source)
            .join(Snapshot, Snapshot.article_id == Article.id)
            .join(Source, Source.id == Article.source_id)
            .where(Snapshot.id.in_(missing))
        ).all()
        metadata = {snapshot.id: (article, snapshot, source) for article, snapshot, source in rows}

    result = []
    for snapshot_id, fusion_score in fused:
        base = lexical_by_snapshot.get(snapshot_id)
        vector = vector_by_snapshot.get(snapshot_id)
        if base is None:
            triple = metadata.get(snapshot_id)
            if triple is None:
                continue
            article, snapshot, source = triple
            base = {
                "article_id": article.id, "snapshot_id": snapshot.id,
                "title": snapshot.title, "source_name": source.name,
                "url": article.canonical_url, "published_at": article.published_at,
            }
        item = dict(base)
        if vector is not None:
            item.update({
                "quote": vector["text"],
                "quote_start": vector["start_offset"],
                "quote_end": vector["end_offset"],
                "vector_similarity": float(vector["similarity"]),
            })
        item["fusion_score"] = fusion_score
        result.append(item)
    return result, status


def retrieval_status(requested_mode: str, *, vector_available: bool) -> dict:
    if requested_mode == "lexical":
        return {"requested_mode": "lexical", "effective_mode": "lexical", "degraded": False, "degraded_reason": None}
    if requested_mode != "hybrid":
        raise ValueError("Unsupported retrieval mode")
    if not vector_available:
        return {"requested_mode": "hybrid", "effective_mode": "lexical", "degraded": True,
                "degraded_reason": "embedding_unavailable"}
    return {"requested_mode": "hybrid", "effective_mode": "hybrid", "degraded": False, "degraded_reason": None}



def hybrid_search(
    db,
    question: str,
    scope: RetrievalScope,
    *,
    query_vector: list[float] | None,
    embedding_model: str | None,
    lexical_limit: int = 50,
    vector_limit: int = 50,
    final_limit: int = 20,
) -> dict:
    """Run two independent candidate generators inside one authorized scope."""
    lexical = lexical_candidates(db, question, scope, limit=lexical_limit)
    citations, status = fuse_evidence(
        db,
        lexical,
        query_vector=query_vector,
        embedding_model=embedding_model,
        vector_limit=vector_limit,
        final_limit=final_limit,
        allowed_snapshot_ids=list(scope.snapshot_ids),
    )
    return {
        **status,
        "citations": citations,
        "searched_documents": len(scope.snapshot_ids),
        "abstained": not citations,
    }
