"""Real PostgreSQL transactions, fixture vectors only. Opt-in disposable CI DB."""
import os
from datetime import timedelta
from uuid import uuid4
import pytest
from sqlalchemy import select, text
from app.config import Settings
from app.db import make_database
from app.models import Article, Snapshot, Source, Workspace, ProviderCall
from app.schemas import Preferences
from app.embeddings import ensure_snapshot_chunks, write_embedding
from app.embedding_gateway import EmbeddingGateway
from app.briefing import build_brief
from app.research_retrieval import search_research_evidence
from app.textutil import digest
from app.timeutil import iso, utcnow
from test_query_recovery import Spy
import test_query_recovery as contracts

pytestmark=pytest.mark.skipif(os.getenv("AI_PULSE_TEST_POSTGRES")!="1",reason="PostgreSQL integration gate is opt-in")


@pytest.fixture
def pg_scope():
    settings=Settings(_env_file=None).model_copy(update={"embedding_provider":"fixture",
        "embedding_model":"fixture-sha256-v1","embedding_dim":8,"retrieval_mode":"hybrid"})
    engine,factory=make_database(settings)
    assert engine.dialect.name=="postgresql"
    with factory.begin() as db:
        before=set(db.scalars(select(ProviderCall.id)))
        created=db.get(Workspace,1) is None
        if created: db.add(Workspace(id=1,data_mode="replay",preferences=Preferences().model_dump()))
    try: yield factory,settings
    finally:
        with factory.begin() as db:
            ids=set(db.scalars(select(ProviderCall.id)))-before
        with engine.begin() as c:
            for cid in ids:
                c.execute(text("DELETE FROM provider_reconciliations WHERE call_id=:id OR replacement_call_id=:id"),{"id":cid})
                c.execute(text("DELETE FROM provider_call_resources WHERE call_id=:id"),{"id":cid})
                c.execute(text("DELETE FROM query_embeddings WHERE call_id=:id"),{"id":cid})
            for cid in ids: c.execute(text("DELETE FROM provider_calls WHERE id=:id"),{"id":cid})
            if created: c.execute(text("DELETE FROM workspace WHERE id=1"))
        engine.dispose()


def test_pg_authorization_is_consumed_once_under_race(pg_scope):
    contracts.test_concurrent_ticket_consumption_and_resource_transfer(pg_scope)


def test_pg_timeout_recovery(pg_scope):
    contracts.test_query_timeout_blocked_until_explicit_ticket(pg_scope)


def test_pg_cache_receipt_atomicity(pg_scope):
    contracts.test_query_atomic_output_receipt_rollback_stays_blocked(pg_scope)


def test_pg_cached_vector_does_not_cache_historical_evidence_scope(pg_scope):
    f,s=pg_scope;p=Spy();g=EmbeddingGateway(p);sid="scope-"+uuid4().hex
    aid,bid=None,None
    try:
        with f.begin() as db:
            db.add(Source(id=sid,name="fixture",url="https://example.invalid/feed",kind="rss",enabled=True));db.flush()
            a=Article(canonical_url="https://example.invalid/"+sid,source_id=sid,title="SYNTHETIC agent",
                      data_mode="replay",published_at=iso(utcnow()-timedelta(hours=1)))
            db.add(a);db.flush();aid=a.id
            old=Snapshot(article_id=aid,title=a.title,text="SYNTHETIC agent original evidence",content_hash=digest("old"))
            db.add(old);db.flush();a.current_snapshot_id=old.id;old_id=old.id
            for ch in ensure_snapshot_chunks(db,old): write_embedding(db,ch,[1.0]*8,p.model,provider=p.provider)
        b=build_brief(f,s);bid=b["brief_id"]
        with f.begin() as db:
            new=Snapshot(article_id=aid,title="SYNTHETIC update",text="SYNTHETIC agent changed evidence",content_hash=digest("new"))
            db.add(new);db.flush();db.get(Article,aid).current_snapshot_id=new.id;new_id=new.id
            for ch in ensure_snapshot_chunks(db,new): write_embedding(db,ch,[1.0]*8,p.model,provider=p.provider)
        with f() as db: historical=search_research_evidence(db,s,"agent",bid,gateway=g)
        with f() as db: current=search_research_evidence(db,s,"agent",None,gateway=g)
        assert historical["effective_mode"]==current["effective_mode"]=="hybrid"
        assert {r["snapshot_id"] for r in historical["citations"]}=={old_id}
        assert {r["snapshot_id"] for r in current["citations"]}=={new_id}
        assert p.calls==1 and current["query_embedding"]["reused"]
        with f.begin() as db: db.get(Source,sid).enabled=False
        with f() as db: disabled=search_research_evidence(db,s,"agent",bid,gateway=g)
        assert not disabled["citations"] and p.calls==1
    finally:
        with f.begin() as db:
            if bid:
                db.execute(text("DELETE FROM brief_items WHERE brief_id=:id"),{"id":bid})
                db.execute(text("DELETE FROM briefs WHERE id=:id"),{"id":bid})
            if aid:
                db.execute(text("DELETE FROM evidence_chunks WHERE snapshot_id IN (SELECT id FROM snapshots WHERE article_id=:id)"),{"id":aid})
                db.execute(text("DELETE FROM snapshots WHERE article_id=:id"),{"id":aid})
                db.execute(text("DELETE FROM articles WHERE id=:id"),{"id":aid})
            db.execute(text("DELETE FROM sources WHERE id=:id"),{"id":sid})
