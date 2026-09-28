from datetime import timedelta, datetime, timezone
from sqlalchemy import select, func
import pytest
from app.models import Article, Snapshot, Source, Workspace, Brief, BriefItem, Run
from app.feeds import Entry
from app.ingestion import ingest_entries
from app.demo import seed_demo
from app.briefing import build_brief, brief_payload, choose_candidates
from app.schemas import Preferences
from app.bootstrap import bootstrap
from app.jobs import new_run, execute_run, BusyError
from app.retrieval import answer_question
from app.timeutil import iso, utcnow


def add(factory, title="Agent release", text="Original source text about a reproducible Agent workflow.",url="https://example.com/test",published=None,first_seen=None):
    with factory.begin() as db:
        source=db.get(Source,"langgraph")
        result=ingest_entries(db,source,[Entry(title,url,text,published or iso(utcnow()-timedelta(hours=1)))],"replay")
        a=db.scalar(select(Article).where(Article.canonical_url==url))
        if first_seen:
            a.first_seen_at=iso(first_seen)
            # The fixture represents a document actually observed before the cutoff.
            db.get(Snapshot,a.current_snapshot_id).captured_at=iso(first_seen)
        return result,a.id,a.current_snapshot_id


def test_deduplication(client):
    f=client.app.state.session_factory
    one=add(f);two=add(f)
    assert one[0]["inserted"]==1 and two[0]["unchanged"]==1
    with f() as db:
        assert db.scalar(select(func.count()).select_from(Article))==1
        assert db.scalar(select(func.count()).select_from(Snapshot))==1


def test_content_update_preserves_old_snapshot(client):
    f=client.app.state.session_factory
    _,aid,old=add(f)
    counts,_,new=add(f,text="Updated source text, not equal to the old Agent workflow.")
    assert counts["updated"]==1 and old!=new
    with f() as db:
        assert db.get(Snapshot,old).text.startswith("Original")
        assert db.get(Article,aid).current_snapshot_id==new


def test_brief_idempotency(seeded):
    f=seeded.app.state.session_factory;s=seeded.app.state.settings
    first=build_brief(f,s);second=build_brief(f,s)
    assert first["brief_id"]==second["brief_id"] and second["cached"]
    with f() as db:
        assert db.scalar(select(func.count()).select_from(Brief))==1


def test_brief_evidence_remains_stable(client):
    f=client.app.state.session_factory;s=client.app.state.settings
    _,aid,old=add(f)
    brief=build_brief(f,s)
    add(f,text="A newer version mentions completely different material.")
    with f() as db:
        data=brief_payload(db,db.get(Brief,brief["brief_id"]))
        assert data["items"][0]["snapshot_id"]==old
        assert data["items"][0]["evidence_quote"].startswith("Original")
        answer=answer_question(db,"reproducible Agent",brief["brief_id"],"replay")
        assert answer["citations"][0]["snapshot_id"]==old


def test_new_content_creates_new_edition(client):
    f=client.app.state.session_factory;s=client.app.state.settings
    add(f);first=build_brief(f,s)
    add(f,text="This updated release contains additional Agent workflow details.")
    second=build_brief(f,s)
    assert first["brief_id"]!=second["brief_id"]

@pytest.mark.parametrize("hours,expected",[(1,1),(71,1),(73,0),(-1,0)])
def test_time_window(client,hours,expected):
    f=client.app.state.session_factory;s=client.app.state.settings
    now=utcnow()
    add(f,published=iso(now-timedelta(hours=hours)),first_seen=now-timedelta(minutes=1))
    assert build_brief(f,s,cutoff=now)["items"]==expected


def test_missing_publication_not_today(client):
    f=client.app.state.session_factory;s=client.app.state.settings
    _,aid,_=add(f)
    with f.begin() as db:
        db.get(Article,aid).published_at=None
    assert build_brief(f,s)["items"]==0


def test_first_seen_after_cutoff_is_excluded(client):
    f=client.app.state.session_factory;s=client.app.state.settings
    now=utcnow();add(f,first_seen=now+timedelta(hours=1))
    assert build_brief(f,s,cutoff=now)["items"]==0


def test_exact_content_grouping(client):
    f=client.app.state.session_factory;s=client.app.state.settings
    add(f,url="https://example.com/a");add(f,url="https://example.com/b")
    assert build_brief(f,s)["items"]==1
    with f() as db:
        assert db.scalar(select(func.count()).select_from(Article))==2


def test_block_keywords(seeded):
    f=seeded.app.state.session_factory;s=seeded.app.state.settings
    with f.begin() as db:
        p=Preferences(blocked_keywords=["测试样本","合成"])
        db.get(Workspace,1).preferences=p.model_dump()
    assert build_brief(f,s)["items"]==0


def test_disabled_sources_excluded(seeded):
    f=seeded.app.state.session_factory;s=seeded.app.state.settings
    with f.begin() as db:
        for source in db.scalars(select(Source)):
            source.enabled=False
    assert build_brief(f,s)["items"]==0


def test_relevance_priority(client):
    f=client.app.state.session_factory;s=client.app.state.settings
    add(f,title="Agent news",url="https://example.com/a",published=iso(utcnow()-timedelta(hours=30)))
    add(f,title="Vision image",text="A vision sample.",url="https://example.com/b")
    with f.begin() as db:
        db.get(Workspace,1).preferences=Preferences(topics=["Agent"],max_items=1).model_dump()
    result=build_brief(f,s)
    with f() as db:
        assert brief_payload(db,db.get(Brief,result["brief_id"]))["items"][0]["title"]=="Agent news"


def test_timezone_local_date(client):
    f=client.app.state.session_factory;s=client.app.state.settings
    cutoff=datetime(2026,9,22,0,30,tzinfo=timezone.utc)
    with f.begin() as db:
        db.get(Workspace,1).preferences=Preferences(timezone="America/Los_Angeles").model_dump()
    result=build_brief(f,s,cutoff=cutoff)
    with f() as db:
        assert db.get(Brief,result["brief_id"]).local_date=="2026-09-21"


def test_mode_cannot_mix_databases(client):
    with pytest.raises(RuntimeError):
        bootstrap(client.app.state.session_factory,client.app.state.settings.model_copy(update={"data_mode":"live"}))


def test_live_cannot_seed_demo(client):
    with pytest.raises(ValueError):
        seed_demo(client.app.state.session_factory,client.app.state.settings.model_copy(update={"data_mode":"live"}))


def test_busy_gate(client):
    f=client.app.state.session_factory
    new_run(f,"brief")
    with pytest.raises(BusyError):
        new_run(f,"brief")


def test_job_persists_progress(seeded):
    f=seeded.app.state.session_factory;s=seeded.app.state.settings
    rid=new_run(f,"brief");execute_run(f,s,rid)
    with f() as db:
        r=db.get(Run,rid)
        assert r.status=="succeeded" and r.active_key is None and r.result["items"]==6
        assert [e["stage"] for e in r.events][-1]=="done"


def test_completed_job_not_reexecuted(seeded):
    f=seeded.app.state.session_factory;s=seeded.app.state.settings
    rid=new_run(f,"brief");execute_run(f,s,rid);execute_run(f,s,rid)
    with f() as db:
        assert sum(e["stage"]=="start" for e in db.get(Run,rid).events)==1


def test_restart_marks_interrupted(client):
    f=client.app.state.session_factory;s=client.app.state.settings
    rid=new_run(f,"brief")
    bootstrap(f,s)
    with f() as db:
        r=db.get(Run,rid)
        assert r.status=="interrupted" and r.active_key is None


def test_search_abstains(seeded):
    result=seeded.post("/api/v1/ask",json={"question":"qzxunmatchedkeyword"}).json()
    assert result["abstained"] and not result["citations"]


def test_search_cites_exact_text(seeded):
    result=seeded.post("/api/v1/ask",json={"question":"Agent 工作流"}).json()
    assert not result["abstained"]
    for c in result["citations"]:
        snap=seeded.get(f"/api/v1/snapshots/{c['snapshot_id']}").json()
        assert snap["text"][c["quote_start"]:c["quote_end"]]==c["quote"]


def test_historical_publication_metadata_is_frozen(client):
    f=client.app.state.session_factory;s=client.app.state.settings
    old_time=iso(utcnow()-timedelta(hours=5))
    add(f,published=old_time)
    one=build_brief(f,s)
    add(f,published=iso(utcnow()-timedelta(hours=1)))
    with f() as db:
        item=brief_payload(db,db.get(Brief,one["brief_id"]))["items"][0]
        assert item["published_at"]==old_time
    two=build_brief(f,s)
    assert one["brief_id"]!=two["brief_id"]


def test_database_rollback_on_bad_evidence(client):
    from app.llm import EvidenceError
    f=client.app.state.session_factory;s=client.app.state.settings
    add(f)
    class Reject:
        def draft(self,*args):
            raise EvidenceError("bad quote")
    with pytest.raises(EvidenceError):
        build_brief(f,s.model_copy(update={"llm_mode":"live"}),model_client=Reject())
    with f() as db:
        assert db.scalar(select(func.count()).select_from(Brief))==0
        assert db.scalar(select(func.count()).select_from(BriefItem))==0


def test_failed_job_releases_active_gate(client):
    from app.llm import ModelError
    f=client.app.state.session_factory;s=client.app.state.settings
    add(f)
    class Reject:
        def draft(self,*args):
            raise ModelError("provider unavailable")
    rid=new_run(f,"brief")
    execute_run(f,s.model_copy(update={"llm_mode":"live"}),rid,model_client=Reject())
    with f() as db:
        run=db.get(Run,rid)
        assert run.status=="failed" and run.active_key is None
    assert new_run(f,"brief")
