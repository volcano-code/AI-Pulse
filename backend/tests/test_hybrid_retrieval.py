import math
import pytest
from types import SimpleNamespace
from sqlalchemy import select
from app.embeddings import chunk_text, ensure_snapshot_chunks, vector_literal, write_embedding
from app import hybrid_retrieval
from app.hybrid_retrieval import fuse_evidence, hybrid_search, reciprocal_rank_fusion, retrieval_status, vector_candidates
from app.retrieval import RetrievalScope, build_scope
from app.models import EvidenceChunk, Snapshot


def test_chunk_offsets_are_exact_and_bounded():
    text = ("第一段 Agent evidence。\n" * 120) + ("second section LLM evidence. " * 100)
    chunks = chunk_text(text, target_chars=500, overlap_chars=80)
    assert len(chunks) > 2
    assert chunks[0]["start_offset"] == 0
    assert chunks[-1]["end_offset"] == len(text)
    for item in chunks:
        assert text[item["start_offset"]:item["end_offset"]] == item["text"]
        assert item["end_offset"] > item["start_offset"]
    for left, right in zip(chunks, chunks[1:]):
        assert right["start_offset"] < left["end_offset"]


def test_snapshot_chunking_is_idempotent(seeded):
    factory = seeded.app.state.session_factory
    with factory.begin() as db:
        snapshot = db.scalar(select(Snapshot).limit(1))
        one = ensure_snapshot_chunks(db, snapshot)
        two = ensure_snapshot_chunks(db, snapshot)
        assert [x.id for x in one] == [x.id for x in two]
        assert db.scalar(select(EvidenceChunk).where(EvidenceChunk.snapshot_id == snapshot.id)) is not None


def test_embedding_validation_and_sqlite_storage(seeded):
    factory = seeded.app.state.session_factory
    with factory.begin() as db:
        snapshot = db.scalar(select(Snapshot).limit(1))
        chunk = ensure_snapshot_chunks(db, snapshot)[0]
        write_embedding(db, chunk, [0.1, -0.2, 0.3], "fixture-embedding")
        assert chunk.embedding_dim == 3 and chunk.embedding_model == "fixture-embedding"
        assert chunk.embedding_json == [0.1, -0.2, 0.3]
        assert vector_candidates(db, [0.1, -0.2, 0.3], "fixture-embedding") == []
    with pytest.raises(ValueError):
        vector_literal([math.inf])


def test_rrf_is_deterministic_and_deduplicates_each_ranking():
    fused = reciprocal_rank_fusion([["a", "b", "a"], ["b", "c"]], k=60, limit=3)
    assert [item for item, _ in fused] == ["b", "a", "c"]
    assert fused[0][1] > fused[1][1] > fused[2][1]


def test_hybrid_degradation_is_explicit():
    assert retrieval_status("hybrid", vector_available=False) == {
        "requested_mode": "hybrid", "effective_mode": "lexical",
        "degraded": True, "degraded_reason": "embedding_unavailable"
    }
    assert retrieval_status("hybrid", vector_available=True)["effective_mode"] == "hybrid"
    assert retrieval_status("lexical", vector_available=False)["degraded"] is False


def test_fuse_evidence_degrades_without_query_embedding(seeded):
    factory = seeded.app.state.session_factory
    lexical = [
        {"snapshot_id": "s1", "article_id": "a1", "quote": "one"},
        {"snapshot_id": "s2", "article_id": "a2", "quote": "two"},
    ]
    with factory() as db:
        rows, status = fuse_evidence(
            db, lexical, query_vector=None, embedding_model=None, final_limit=1
        )
    assert rows == lexical[:1]
    assert status["effective_mode"] == "lexical"
    assert status["degraded"] is True


def test_fuse_evidence_rrf_prefers_vector_supported_snapshot(monkeypatch):
    db = SimpleNamespace(bind=SimpleNamespace(dialect=SimpleNamespace(name="postgresql")))
    lexical = [
        {"snapshot_id": "s1", "article_id": "a1", "quote": "lexical one", "quote_start": 0, "quote_end": 11},
        {"snapshot_id": "s2", "article_id": "a2", "quote": "lexical two", "quote_start": 0, "quote_end": 11},
    ]

    def fake_vector_candidates(db, query_vector, embedding_model, limit, allowed_snapshot_ids):
        assert allowed_snapshot_ids == ["s1", "s2"]
        return [{
            "id": "c2", "snapshot_id": "s2", "ordinal": 0,
            "text": "vector evidence", "start_offset": 7, "end_offset": 22,
            "similarity": 0.91,
        }]

    monkeypatch.setattr(hybrid_retrieval, "vector_candidates", fake_vector_candidates)
    rows, status = fuse_evidence(
        db, lexical, query_vector=[1.0, 0.0], embedding_model="fixture-2d", final_limit=2
    )
    assert [row["snapshot_id"] for row in rows] == ["s2", "s1"]
    assert rows[0]["quote"] == "vector evidence"
    assert rows[0]["vector_similarity"] == pytest.approx(0.91)
    assert status["effective_mode"] == "hybrid"



def test_current_scope_contains_more_than_lexical_hits(seeded):
    factory = seeded.app.state.session_factory
    with factory() as db:
        scope = build_scope(db, None, "replay")
        assert len(scope.snapshot_ids) >= 3
        assert len(set(scope.snapshot_ids)) == len(scope.snapshot_ids)


def test_hybrid_vector_can_recover_snapshot_outside_lexical_hits(monkeypatch):
    db = SimpleNamespace(bind=SimpleNamespace(dialect=SimpleNamespace(name="postgresql")))
    scope = RetrievalScope(snapshot_ids=("s1", "s2", "s3"), brief_id=None, data_mode="replay")

    monkeypatch.setattr(hybrid_retrieval, "lexical_candidates", lambda db, question, scope, limit: [
        {"snapshot_id": "s1", "article_id": "a1", "quote": "keyword hit", "quote_start": 0, "quote_end": 11},
    ])

    def fake_vectors(db, query_vector, embedding_model, limit, allowed_snapshot_ids):
        assert allowed_snapshot_ids == ["s1", "s2", "s3"]
        return [{
            "id": "c3", "snapshot_id": "s3", "ordinal": 0,
            "text": "semantic recovery", "start_offset": 5, "end_offset": 22,
            "similarity": 0.94,
        }]

    monkeypatch.setattr(hybrid_retrieval, "vector_candidates", fake_vectors)

    class FakeResult:
        def all(self):
            article = SimpleNamespace(id="a3", canonical_url="https://example.com/3", published_at=None)
            snapshot = SimpleNamespace(id="s3", title="semantic document")
            source = SimpleNamespace(name="fixture")
            return [(article, snapshot, source)]

    db.execute = lambda stmt: FakeResult()
    result = hybrid_search(
        db, "semantic paraphrase", scope,
        query_vector=[0.2, 0.8], embedding_model="fixture-2d",
        lexical_limit=50, vector_limit=50, final_limit=5,
    )
    assert [x["snapshot_id"] for x in result["citations"]] == ["s1", "s3"]
    assert result["citations"][1]["quote"] == "semantic recovery"
    assert result["effective_mode"] == "hybrid"


def test_hybrid_never_passes_snapshots_outside_authorized_scope(monkeypatch):
    db = SimpleNamespace(bind=SimpleNamespace(dialect=SimpleNamespace(name="postgresql")))
    scope = RetrievalScope(snapshot_ids=("old-1", "old-2"), brief_id="brief-old", data_mode="replay")
    monkeypatch.setattr(hybrid_retrieval, "lexical_candidates", lambda *args, **kwargs: [])

    def fake_vectors(db, query_vector, embedding_model, limit, allowed_snapshot_ids):
        assert allowed_snapshot_ids == ["old-1", "old-2"]
        return []

    monkeypatch.setattr(hybrid_retrieval, "vector_candidates", fake_vectors)
    result = hybrid_search(
        db, "historical question", scope,
        query_vector=[1.0, 0.0], embedding_model="fixture-2d",
    )
    assert result["citations"] == []
    assert result["searched_documents"] == 2
