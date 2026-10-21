"""Real PostgreSQL journal/pgvector transactions; fixture providers only."""
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4
import pytest
from sqlalchemy import select, text
from app.config import Settings
from app.db import make_database
from app.durable_backfill import durable_backfill
from app.embedding_gateway import EmbeddingGateway, FixtureEmbeddingProvider
from app.embeddings import ensure_snapshot_chunks
from app.models import (Article, Snapshot, Source, Workspace, ProviderCall, ProviderCallResource)
from app.provider_calls import CallLedger, fingerprint
from app.schemas import Preferences
from app.textutil import digest

pytestmark = pytest.mark.skipif(os.getenv("AI_PULSE_TEST_POSTGRES") != "1",
                              reason="PostgreSQL integration gate is opt-in")


def test_postgres_unique_intent_and_atomic_vector_receipt():
    engine, factory = make_database(Settings(_env_file=None))
    suffix = uuid4().hex[:12]
    sid, url = "audit-" + suffix, "https://example.invalid/audit/" + suffix
    call_ids = []
    created_workspace = False
    snapshot_id = None
    try:
        barrier = Barrier(2)
        def attempt(_):
            barrier.wait()
            return CallLedger(factory).reserve(operation_key="pg-race-"+suffix,
                request_hash=fingerprint("fixture"), kind="llm_canary", provider="fixture",
                model="fixture", data_mode="canary")
        with ThreadPoolExecutor(max_workers=2) as executor:
            calls = list(executor.map(attempt, [1, 2]))
        assert sorted(x.reused for x in calls) == [False, True]
        call_ids.append(calls[0].id)
        with factory.begin() as db:
            if db.get(Workspace, 1) is None:
                db.add(Workspace(id=1, data_mode="replay", preferences=Preferences().model_dump()))
                created_workspace = True
            source = Source(id=sid, name="Audit fixture", url=url, kind="rss")
            db.add(source); db.flush()
            article = Article(canonical_url=url, title="Audit fixture", source_id=sid, data_mode="replay")
            db.add(article); db.flush()
            snapshot = Snapshot(article_id=article.id, title=article.title,
                text="Fixture provider call audit text", content_hash=digest("fixture"))
            db.add(snapshot); db.flush(); snapshot_id = snapshot.id
            article.current_snapshot_id = snapshot.id
            chunk = ensure_snapshot_chunks(db, snapshot)[0]
            chunk_id = chunk.id
        report = durable_backfill(factory, EmbeddingGateway(FixtureEmbeddingProvider(8)),
                                  data_mode="replay", batch_size=1, max_chunks=100)
        call_ids.extend(c["call_id"] for c in report["calls"])
        assert report["status"] == "applied" and report["embedded"] >= 1
        with factory() as db:
            assert db.execute(text("SELECT vector_dims(embedding_vector) FROM evidence_chunks WHERE id=:id"),
                              {"id":chunk_id}).scalar() == 8
            assert all(db.get(ProviderCall, c["call_id"]).status == "succeeded" for c in report["calls"])
        assert durable_backfill(factory, EmbeddingGateway(FixtureEmbeddingProvider(8)),
                                data_mode="replay")["embedded"] == 0
    finally:
        with engine.begin() as conn:
            for cid in call_ids:
                conn.execute(text("DELETE FROM provider_call_resources WHERE call_id=:id"), {"id":cid})
                conn.execute(text("DELETE FROM provider_calls WHERE id=:id"), {"id":cid})
            if snapshot_id:
                conn.execute(text("DELETE FROM evidence_chunks WHERE snapshot_id=:id"), {"id":snapshot_id})
            conn.execute(text("DELETE FROM snapshots WHERE article_id IN (SELECT id FROM articles WHERE source_id=:source_id)"), {"source_id":sid})
            conn.execute(text("DELETE FROM articles WHERE source_id=:source_id"), {"source_id":sid})
            conn.execute(text("DELETE FROM sources WHERE id=:id"), {"id":sid})
            if created_workspace: conn.execute(text("DELETE FROM workspace WHERE id=1"))
        engine.dispose()


def test_postgres_partial_batch_survives_and_restart_does_not_dispatch():
    engine, factory = make_database(Settings(_env_file=None))
    suffix = uuid4().hex[:12]
    sid, url = "partial-" + suffix, "https://example.invalid/partial/" + suffix
    call_ids, snapshot_id, created_workspace = [], None, False
    class FaultProvider(FixtureEmbeddingProvider):
        calls = 0
        def embed(self, texts, *, purpose):
            self.calls += 1
            if self.calls == 2: raise TimeoutError("offline fixture fault")
            return super().embed(texts, purpose=purpose)
    try:
        with factory.begin() as db:
            if db.get(Workspace, 1) is None:
                db.add(Workspace(id=1, data_mode="replay", preferences=Preferences().model_dump()))
                created_workspace = True
            db.add(Source(id=sid, name="Partial fixture", url=url, kind="rss")); db.flush()
            a = Article(canonical_url=url, title="Partial fixture", source_id=sid, data_mode="replay")
            db.add(a); db.flush()
            snap = Snapshot(article_id=a.id, title=a.title, text="public fixture " * 300,
                            content_hash=digest("public fixture " * 300))
            db.add(snap); db.flush(); snapshot_id = snap.id
            a.current_snapshot_id = snap.id
            assert len(ensure_snapshot_chunks(db, snap)) >= 2
        provider = FaultProvider(8)
        result = durable_backfill(factory, EmbeddingGateway(provider), data_mode="replay", batch_size=1)
        call_ids = [c["call_id"] for c in result["calls"]]
        assert result["status"] == "needs_attention" and result["embedded"] == 1
        assert [c["status"] for c in result["calls"]] == ["succeeded", "unknown"]
        with engine.connect() as conn:
            assert conn.scalar(text("SELECT count(*) FROM evidence_chunks WHERE snapshot_id=:id AND embedding_vector IS NOT NULL"), {"id": snapshot_id}) == 1
        again = FaultProvider(8)
        retry = durable_backfill(factory, EmbeddingGateway(again), data_mode="replay", batch_size=2)
        assert retry["status"] == "needs_attention" and again.calls == 0
    finally:
        with engine.begin() as conn:
            for cid in call_ids:
                conn.execute(text("DELETE FROM provider_call_resources WHERE call_id=:id"), {"id":cid})
                conn.execute(text("DELETE FROM provider_calls WHERE id=:id"), {"id":cid})
            if snapshot_id: conn.execute(text("DELETE FROM evidence_chunks WHERE snapshot_id=:id"), {"id":snapshot_id})
            conn.execute(text("DELETE FROM snapshots WHERE article_id IN (SELECT id FROM articles WHERE source_id=:id)"), {"id":sid})
            conn.execute(text("DELETE FROM articles WHERE source_id=:id"), {"id":sid})
            conn.execute(text("DELETE FROM sources WHERE id=:id"), {"id":sid})
            if created_workspace: conn.execute(text("DELETE FROM workspace WHERE id=1"))
        engine.dispose()
