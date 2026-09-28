from sqlalchemy import select, func
from app.events import similarity, sync_events
from app.feeds import Entry
from app.ingestion import ingest_entries
from app.models import Article, Event, EventArticle, EventVersion, Snapshot, Source
from app.textutil import digest


def test_title_similarity_groups_same_release_but_not_unrelated():
    assert similarity('LangGraph 1.2 released with durable agents','LangGraph 1.2 release: durable agent runtime') >= .52
    assert similarity('LangGraph 1.2 released with durable agents','Vision benchmark for medical images') < .52


def test_event_membership_is_stable_and_versioned(client):
    f=client.app.state.session_factory
    with f.begin() as db:
        ingest_entries(db,db.get(Source,'huggingface'),[
            Entry('AgentKit 2.0 released with tool budgets','https://example.com/a','first evidence')], 'replay')
        ingest_entries(db,db.get(Source,'langgraph'),[
            Entry('AgentKit 2.0 release adds tool budget controls','https://example.com/b','second evidence')], 'replay')
        result=sync_events(db,'replay')
        assert result['events_created']==1 and result['articles_attached']==2
        event=db.scalar(select(Event))
        assert db.scalar(select(func.count()).select_from(EventArticle))==2
        first_version=event.current_version_id
        assert db.scalar(select(func.count()).select_from(EventVersion))==1
    with f.begin() as db:
        article=db.scalar(select(Article).where(Article.canonical_url=='https://example.com/a'))
        snap=Snapshot(article_id=article.id,title=article.title,text='changed evidence',content_hash=digest('changed evidence'))
        db.add(snap);db.flush();article.current_snapshot_id=snap.id
        result=sync_events(db,'replay')
        event=db.scalar(select(Event))
        assert result['articles_attached']==0 and result['event_versions_created']==1
        assert event.current_version_id != first_version
        assert db.scalar(select(func.count()).select_from(EventVersion))==2


def test_unrelated_articles_create_separate_events(client):
    f=client.app.state.session_factory
    with f.begin() as db:
        ingest_entries(db,db.get(Source,'huggingface'),[
            Entry('Agent runtime release','https://example.com/a','agent evidence'),
            Entry('Multimodal vision benchmark','https://example.com/c','vision evidence')], 'replay')
        sync_events(db,'replay')
        assert db.scalar(select(func.count()).select_from(Event))==2


def test_event_api_exposes_group_and_version(seeded):
    rows=seeded.get('/api/v1/events').json()
    assert rows and all(r['article_count'] >= 1 and r['version_count'] >= 1 for r in rows)
    detail=seeded.get('/api/v1/events/'+rows[0]['id']).json()
    assert detail['current_version_id'] and detail['versions'] and detail['articles']
    assert seeded.get('/api/v1/events/missing').status_code == 404
    status=seeded.get('/api/v1/status').json()
    assert status['capabilities']['event_grouping'] is True and status['events'] >= 1
