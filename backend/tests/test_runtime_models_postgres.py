"""Real PostgreSQL transactions with explicit fake model; no paid API calls."""
import os
from types import SimpleNamespace
from uuid import uuid4
import pytest
from sqlalchemy import select, text
from app.audited_models import draft_brief_item
from app.config import Settings
from app.db import make_database
from app.models import Article, Snapshot, Source, Workspace, ProviderCall
from app.provider_calls import CallHandle, CallBlocked
from app.schemas import Preferences
from app.textutil import digest

pytestmark = pytest.mark.skipif(os.getenv("AI_PULSE_TEST_POSTGRES") != "1", reason="PostgreSQL integration gate is opt-in")


@pytest.fixture
def pg():
    ident = "runtime-" + uuid4().hex
    settings=Settings(_env_file=None).model_copy(update={"data_mode":"replay","llm_mode":"live","llm_model":ident})
    engine,factory=make_database(settings)
    created_workspace=False
    try:
        with factory.begin() as db:
            if db.get(Workspace,1) is None:
                db.add(Workspace(id=1,data_mode="replay",preferences=Preferences().model_dump()))
                created_workspace=True
            db.add(Source(id=ident,name="runtime fixture",url="https://example.invalid/feed",kind="rss"));db.flush()
            article=Article(canonical_url="https://example.invalid/"+ident,source_id=ident,title="Runtime fixture",data_mode="replay")
            db.add(article);db.flush()
            value="Explicit synthetic source for the model audit transaction contract."
            snapshot=Snapshot(article_id=article.id,title=article.title,text=value,content_hash=digest(article.title+'\n'+value))
            db.add(snapshot);db.flush();article.current_snapshot_id=snapshot.id
        yield factory,settings,snapshot
    finally:
        with engine.begin() as db:
            db.execute(text("DELETE FROM brief_model_drafts WHERE call_id IN (SELECT id FROM provider_calls WHERE model=:model)"),{"model":ident})
            db.execute(text("DELETE FROM provider_call_resources WHERE call_id IN (SELECT id FROM provider_calls WHERE model=:model)"),{"model":ident})
            db.execute(text("DELETE FROM provider_calls WHERE model=:model"),{"model":ident})
            db.execute(text("DELETE FROM snapshots WHERE article_id IN (SELECT id FROM articles WHERE source_id=:sid)"),{"sid":ident})
            db.execute(text("DELETE FROM articles WHERE source_id=:sid"),{"sid":ident})
            db.execute(text("DELETE FROM sources WHERE id=:sid"),{"sid":ident})
            if created_workspace:db.execute(text("DELETE FROM workspace WHERE id=1"))
        engine.dispose()


class Model:
    def __init__(self, factory):self.factory,self.calls=factory,0
    def draft(self,title,text):
        self.calls+=1
        with self.factory() as db:
            assert db.scalar(select(ProviderCall).where(ProviderCall.kind=="brief_draft",ProviderCall.status=="in_flight"))
        return SimpleNamespace(title=title,summary="Fixture draft",evidence_quote=text[:50]),{"prompt_tokens":9,"completion_tokens":2}


def test_postgres_draft_and_receipt_commit_together_and_reuse(pg):
    f,s,snap=pg;model=Model(f)
    args=(f,s,snap.id,snap.title,snap.text)
    first,usage=draft_brief_item(*args,model_client=model)
    cached,again=draft_brief_item(*args,model_client=model)
    assert model.calls==1 and cached==first and again["model_calls"]==0
    assert CallHandle(f,usage["provider_call_id"],True).read()["status"]=="succeeded"


def test_postgres_draft_rollback_leaves_unknown_blocking_receipt(pg,monkeypatch):
    f,s,snap=pg;model=Model(f)
    args=(f,s,snap.id,snap.title,snap.text)
    original=CallHandle.succeed
    def fail(self,db,**kw):
        original(self,db,**kw)
        raise RuntimeError("injected rollback")
    monkeypatch.setattr(CallHandle,"succeed",fail)
    with pytest.raises(RuntimeError):draft_brief_item(*args,model_client=model)
    with f() as db:
        assert db.scalar(text("SELECT count(*) FROM brief_model_drafts"))==0
        row=db.scalars(select(ProviderCall).where(ProviderCall.model==s.llm_model)).one()
        assert row.status=="unknown" and row.input_tokens==9
    with pytest.raises(CallBlocked):draft_brief_item(*args,model_client=model)
    assert model.calls==1
