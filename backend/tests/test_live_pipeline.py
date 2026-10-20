"""Offline contracts only: every fixture run uses a fresh replay workspace."""
import json
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.bootstrap import DEFAULT_SOURCES
from app.events import release_identity, similarity
from app.models import Brief, BriefItem
from app.timeutil import iso, utcnow
from scripts.live_pipeline import audit_saved_data, main, run_pipeline


def payloads(old=False):
    published = iso(utcnow() - timedelta(days=5000 if old else 1))
    return {
        'huggingface': f'<rss><channel><item><title>Fixture blog</title><link>https://example.org/blog</link><description>Fixture evidence only, not a real announcement.</description><pubDate>{published}</pubDate></item></channel></rss>'.encode(),
        'arxiv': f'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Fixture paper</title><link href="https://example.org/paper"/><summary>Fixture paper evidence only.</summary><published>{published}</published></entry></feed>'.encode(),
        'langgraph': json.dumps([
            {'name': 'langgraph==1.2.15', 'html_url': 'https://example.org/release/1', 'body': 'First fixture release evidence.', 'published_at': published},
            {'name': 'langgraph==1.2.16', 'html_url': 'https://example.org/release/2', 'body': 'Second fixture release evidence.', 'published_at': published},
            {'name': 'langgraph-cli==0.4.37', 'html_url': 'https://example.org/release/3', 'body': 'Third fixture release evidence.', 'published_at': published},
        ]).encode(),
    }


def test_fixture_pipeline_migrates_ingests_audits_and_is_not_live(tmp_path):
    with patch('app.llm.ModelClient.draft', side_effect=AssertionError('no paid call')):
        report = run_pipeline(tmp_path / 'run', min_documents=5, fixture_payloads=payloads())
    assert report['status'] == 'passed', report
    assert report['execution'] == 'offline_contract' and report['data_mode'] == 'replay'
    assert report['audit']['totals'] == {'articles': 5, 'snapshots': 5, 'events': 5, 'event_versions': 5}
    assert report['audit']['quotes_validated'] == report['brief']['items'] == 5
    assert report['brief']['idempotent']
    assert report['paid_model_calls'] == report['embedding_calls'] == report['email_attempts'] == 0
    assert json.loads((tmp_path / 'run/report.json').read_text()) == report


def test_pipeline_empty_feed_is_partial_not_success(tmp_path):
    data = payloads(); data['arxiv'] = b'<feed xmlns="http://www.w3.org/2005/Atom"/>'
    report = run_pipeline(tmp_path / 'run', min_documents=1, fixture_payloads=data)
    assert report['status'] == 'partial'
    assert 'source_ingestion_incomplete' in report['failures']
    assert report['audit']['totals']['articles'] == 4


def test_old_publication_dates_are_not_rewritten_to_make_brief_pass(tmp_path):
    report = run_pipeline(tmp_path / 'run', min_documents=5, fixture_payloads=payloads(old=True))
    assert report['status'] == 'partial'
    assert report['brief']['items'] == 0
    assert 'no_current_brief_items' in report['failures']
    assert report['audit']['totals']['articles'] == 5


def test_existing_output_is_never_reused_or_overwritten(tmp_path):
    existing = tmp_path / 'existing'; existing.mkdir(); (existing / 'sentinel').write_text('keep')
    assert main(['--output-dir', str(existing)]) == 2
    assert (existing / 'sentinel').read_text() == 'keep'
    assert not (existing / 'pipeline.db').exists()


def test_migration_failure_retains_sanitized_receipt(tmp_path):
    with patch('scripts.live_pipeline.migrate', side_effect=RuntimeError('secret password')):
        report = run_pipeline(tmp_path / 'run', fixture_payloads=payloads())
    assert report['status'] == 'failed'
    assert report['failures'] == ['pipeline_execution_failed']
    assert 'password' not in json.dumps(report)


def test_quote_tampering_fails_independent_audit(seeded):
    seeded.post('/api/v1/runs', json={'kind': 'brief'})
    factory = seeded.app.state.session_factory
    with factory.begin() as db:
        brief = db.scalar(select(Brief))
        item = db.scalar(select(BriefItem).where(BriefItem.brief_id == brief.id))
        item.evidence_quote = 'not in original source'; db.flush()
        result = audit_saved_data(db, brief, 'replay')
        assert not result['passed']
        assert 'brief_evidence_integrity' in result['failures']


@pytest.mark.parametrize('a,b', [
    ('langgraph==1.2.15', 'langgraph==1.2.16'),
    ('langgraph==1.2.16', 'langgraph-cli==0.4.37'),
    ('langgraph-checkpoint-sqlite==3.1.2', 'langgraph-checkpoint-postgres==3.2.0'),
    ('langgraph-cli==0.4.32', 'langgraph-cli==0.4.32.dev0'),
])
def test_distinct_release_identities_never_merge(a, b):
    assert similarity(a, b) == 0


def test_release_identity_is_conservative_and_normalized():
    assert release_identity('LangGraph == 1.2.16') == ('langgraph', '1.2.16')
    assert release_identity('LangGraph workflows are useful') is None
    assert similarity('langgraph==1.2.16', 'langgraph==1.2.16') == 1
