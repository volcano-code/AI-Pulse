import os
import pytest
from sqlalchemy import text
from app.config import Settings
from app.db import make_database
from app.models import Article, EvidenceChunk, Snapshot, Source
from app.embeddings import ensure_snapshot_chunks, write_embedding
from app.embedding_backfill import backfill_embeddings
from app.embedding_gateway import EmbeddingGateway, FixtureEmbeddingProvider
from app.hybrid_retrieval import vector_candidates
from app.textutil import digest


pytestmark = pytest.mark.skipif(
    os.getenv("AI_PULSE_TEST_POSTGRES") != "1",
    reason="PostgreSQL integration gate is opt-in",
)


def test_pgvector_extension_and_exact_cosine_search():
    settings = Settings(_env_file=None)
    engine, factory = make_database(settings)
    assert engine.dialect.name == "postgresql"
    try:
        with engine.connect() as conn:
            assert conn.scalar(text("SELECT extversion FROM pg_extension WHERE extname='vector'"))
        with factory.begin() as db:
            source = Source(id="pgvector-test", name="pgvector test", url="https://example.com/feed",
                            kind="rss", enabled=True)
            db.add(source)
            db.flush()  # PostgreSQL must observe the FK parent before the child fixture.
            article = Article(canonical_url="https://example.com/vector-test", source_id=source.id,
                              title="Vector test", data_mode="replay", topic="Agent")
            db.add(article); db.flush()
            snapshot = Snapshot(article_id=article.id, title=article.title,
                                text="alpha evidence\nbeta evidence", content_hash=digest("alpha evidence\nbeta evidence"))
            db.add(snapshot); db.flush(); article.current_snapshot_id = snapshot.id
            chunks = ensure_snapshot_chunks(db, snapshot)
            write_embedding(db, chunks[0], [1.0, 0.0, 0.0], "fixture-3d")
        with factory() as db:
            rows = vector_candidates(db, [1.0, 0.0, 0.0], "fixture-3d", limit=5)
            assert rows and rows[0]["snapshot_id"] == snapshot.id
            assert float(rows[0]["similarity"]) > 0.999
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM evidence_chunks WHERE snapshot_id IN (SELECT id FROM snapshots WHERE article_id IN (SELECT id FROM articles WHERE source_id='pgvector-test'))"))
            conn.execute(text("DELETE FROM snapshots WHERE article_id IN (SELECT id FROM articles WHERE source_id='pgvector-test')"))
            conn.execute(text("DELETE FROM articles WHERE source_id='pgvector-test'"))
            conn.execute(text("DELETE FROM sources WHERE id='pgvector-test'"))
        engine.dispose()



def test_pgvector_fixture_backfill_is_idempotent_and_searchable():
    settings = Settings(_env_file=None)
    engine, factory = make_database(settings)
    gateway = EmbeddingGateway(FixtureEmbeddingProvider(dimensions=8))
    source_id = "pgvector-backfill"
    try:
        with factory.begin() as db:
            source = Source(id=source_id, name="backfill test", url="https://example.com/backfill",
                            kind="rss", enabled=True)
            db.add(source); db.flush()
            article = Article(canonical_url="https://example.com/backfill-vector", source_id=source.id,
                              title="Backfill vector test", data_mode="replay", topic="Agent")
            db.add(article); db.flush()
            snapshot = Snapshot(article_id=article.id, title=article.title,
                                text="semantic evidence for backfill", content_hash=digest("semantic evidence for backfill"))
            db.add(snapshot); db.flush(); article.current_snapshot_id = snapshot.id
            ensure_snapshot_chunks(db, snapshot)
        with factory.begin() as db:
            first = backfill_embeddings(db, gateway, max_chunks=100)
            assert first["embedded"] >= 1
        with factory.begin() as db:
            second = backfill_embeddings(db, gateway, max_chunks=100)
            assert second["selected"] == 0
        query = gateway.embed_documents(["semantic evidence for backfill"]).vectors[0]
        with factory() as db:
            rows = vector_candidates(db, query, gateway.provider.model, limit=5,
                                     embedding_provider=gateway.provider.provider,
                                     allowed_snapshot_ids=[snapshot.id])
            assert rows and rows[0]["snapshot_id"] == snapshot.id
            assert float(rows[0]["similarity"]) > 0.999
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM evidence_chunks WHERE snapshot_id IN (SELECT id FROM snapshots WHERE article_id IN (SELECT id FROM articles WHERE source_id=:source_id))"), {"source_id": source_id})
            conn.execute(text("DELETE FROM snapshots WHERE article_id IN (SELECT id FROM articles WHERE source_id=:source_id)"), {"source_id": source_id})
            conn.execute(text("DELETE FROM articles WHERE source_id=:source_id"), {"source_id": source_id})
            conn.execute(text("DELETE FROM sources WHERE id=:source_id"), {"source_id": source_id})
        engine.dispose()
