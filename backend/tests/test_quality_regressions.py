"""Regression cases identified by the v0.2 source audit (not in v0.1's 121 tests)."""
from datetime import timedelta
from sqlalchemy import select
from app.briefing import build_brief
from app.feeds import Entry
from app.ingestion import ingest_entries, fetch_source
from app.models import Article, Source
from app.timeutil import iso, utcnow


def test_other_source_cannot_overwrite_provenance(client):
    f = client.app.state.session_factory
    with f.begin() as db:
        original = Entry('Original publisher', 'https://example.com/shared',
                         'Original article with a traceable source.', iso(utcnow()))
        ingest_entries(db, db.get(Source, 'huggingface'), [original], 'replay')
        foreign = Entry('Conflicting publisher', original.url,
                        'Different content published by another feed.', original.published_at)
        counts = ingest_entries(db, db.get(Source, 'langgraph'), [foreign], 'replay')
        article = db.scalar(select(Article).where(Article.canonical_url == original.url))
        assert counts['rejected'] == 1
        assert article.title == original.title
        assert article.source_id == 'huggingface'


def test_all_rejected_entries_do_not_mark_source_healthy(client):
    class InvalidFeed:
        def fetch(self, url):
            return b'<rss><channel><item><title>Bad link</title><link>javascript:alert(1)</link><description>Nonempty text</description></item></channel></rss>'
    f = client.app.state.session_factory
    settings = client.app.state.settings.model_copy(update={'data_mode': 'live'})
    result = fetch_source(f, 'huggingface', settings, InvalidFeed())
    assert result['status'] == 'failed'
    with f() as db:
        source = db.get(Source, 'huggingface')
        assert source.last_success_at is None
        assert source.failure_count == 1


def test_successful_poll_timestamp_alone_does_not_make_new_edition(seeded):
    f, settings = seeded.app.state.session_factory, seeded.app.state.settings
    with f.begin() as db:
        db.get(Source, 'huggingface').last_success_at = iso(utcnow() - timedelta(minutes=10))
    first = build_brief(f, settings)
    with f.begin() as db:
        db.get(Source, 'huggingface').last_success_at = iso(utcnow())
    second = build_brief(f, settings)
    assert first['brief_id'] == second['brief_id']
    assert second['cached'] is True


def test_snapshot_captured_after_cutoff_is_not_used(client):
    from app.models import Snapshot
    f, settings = client.app.state.session_factory, client.app.state.settings
    cutoff = utcnow()
    with f.begin() as db:
        entry = Entry('Updated after cutoff', 'https://example.com/late-snapshot',
                      'This content was only captured after the report cutoff.',
                      iso(cutoff - timedelta(hours=1)))
        ingest_entries(db, db.get(Source, 'langgraph'), [entry], 'replay')
        article = db.scalar(select(Article).where(Article.canonical_url == entry.url))
        article.first_seen_at = iso(cutoff - timedelta(hours=1))
        db.get(Snapshot, article.current_snapshot_id).captured_at = iso(cutoff + timedelta(minutes=1))
    assert build_brief(f, settings, cutoff=cutoff)['items'] == 0
