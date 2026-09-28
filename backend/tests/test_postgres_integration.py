import os
import pytest
from sqlalchemy import text
from app.config import Settings
from app.db import make_database
from app.models import Article, EvidenceChunk, Snapshot, Source
from app.embeddings import ensure_snapshot_chunks, write_embedding
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
