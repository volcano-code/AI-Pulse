"""Live-mode pipeline contracts using explicitly fake feeds/models.

These tests exercise mode-specific code, NOT real external service integration.
All databases are disposable; no SMTP transport is used.
"""
from datetime import timedelta
import json
from urllib.parse import urlsplit
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, func
from app.config import Settings
from app.main import create_app
from app.durable import enqueue, claim, execute
from app.ingestion import fetch_source
from app.models import Source, Run, Brief, Delivery
from app.timeutil import iso, utcnow


@pytest.fixture
def live_env(tmp_path):
    settings = Settings(_env_file=None, database_url=f'sqlite:///{tmp_path / "contract.db"}',
        data_mode='live', llm_mode='extractive', task_mode='durable', delivery_transport='file',
        outbox_dir=str(tmp_path / 'mail'), retry_base_seconds=1)
    with TestClient(create_app(settings)) as client:
        yield client.app.state.session_factory, settings


class FakeFeeds:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def fetch(self, url):
        self.calls.append(url)
        if self.fail:
            raise TimeoutError('Simulated source unavailable')
        host = urlsplit(url).hostname
        date = iso(utcnow() - timedelta(minutes=10))
        if host == 'api.github.com':
            return json.dumps([{'name': 'SYNTHETIC Agent release fixture',
                'html_url': 'https://example.invalid/fake-release', 'body': 'Synthetic release evidence for contract testing only.',
                'published_at': date}]).encode()
        return f'<rss><channel><item><title>SYNTHETIC Agent fixture {host}</title><link>https://example.invalid/{host}</link><description>Synthetic source evidence for contract testing only.</description><pubDate>{date}</pubDate></item></channel></rss>'.encode()


def test_live_mode_mock_feed_daily_reaches_transactional_outbox(live_env):
    f, s = live_env
    fake = FakeFeeds()
    rid = enqueue(f, 'daily', s, {'send': True})
    execute(f, s, claim(f, s), fetcher=fake)
    with f() as db:
        row = db.get(Run, rid)
        assert row.status == 'succeeded' and len(fake.calls) == 3
        assert row.checkpoint['ingestion_done'] is True and row.result['items'] == 3
        delivery = db.get(Delivery, row.result['delivery_id'])
        assert delivery.status == 'pending' and delivery.transport == 'file'


def test_all_mock_sources_failing_gets_bounded_retry_without_old_brief(live_env):
    f, s = live_env
    rid = enqueue(f, 'daily', s, {'send': True})
    fake = FakeFeeds(fail=True)
    for attempt in range(s.job_max_attempts):
        with f.begin() as db:
            db.get(Run, rid).available_at = iso(utcnow() - timedelta(seconds=1))
        execute(f, s, claim(f, s), fetcher=fake)
        with f() as db:
            assert db.get(Run, rid).status == ('failed' if attempt == s.job_max_attempts - 1 else 'queued')
            assert db.scalar(select(func.count()).select_from(Brief)) == 0
            assert db.scalar(select(func.count()).select_from(Delivery)) == 0
    assert len(fake.calls) == 9


def test_successful_source_checkpoint_is_not_refetched_on_resume(live_env):
    f, s = live_env
    fake = FakeFeeds()
    completed = fetch_source(f, 'huggingface', s, fake)
    assert completed['status'] == 'succeeded'
    rid = enqueue(f, 'daily', s)
    with f.begin() as db:
        db.get(Run, rid).checkpoint = {'sources': [completed]}
    fake.calls.clear()
    execute(f, s, claim(f, s), fetcher=fake)
    assert len(fake.calls) == 2 and all('huggingface.co' not in url for url in fake.calls)
    with f() as db:
        assert db.get(Run, rid).status == 'succeeded'


def test_no_enabled_sources_is_explicit_failure(live_env):
    f, s = live_env
    with f.begin() as db:
        for source in db.scalars(select(Source)):
            source.enabled = False
    rid = enqueue(f, 'daily', s)
    execute(f, s, claim(f, s), fetcher=FakeFeeds())
    with f() as db:
        assert db.get(Run, rid).status == 'failed'
        assert db.scalar(select(func.count()).select_from(Brief)) == 0


def test_uncertain_model_contract_failure_needs_attention_not_auto_replay(live_env):
    f, s = live_env
    fetch_source(f, 'huggingface', s, FakeFeeds())
    live_model_settings = s.model_copy(update={'llm_mode': 'live', 'llm_model': 'mock-only', 'llm_api_key': 'mock-not-a-real-key'})
    class ModelTimeout:
        calls = 0
        def draft(self, title, text):
            self.calls += 1
            raise TimeoutError('Mock remote outcome unknown')
    model = ModelTimeout()
    rid = enqueue(f, 'brief', live_model_settings)
    execute(f, live_model_settings, claim(f, live_model_settings), model_client=model)
    with f() as db:
        assert db.get(Run, rid).status == 'needs_attention'
        assert db.get(Run, rid).checkpoint['model_in_flight'] is True
        assert db.scalar(select(func.count()).select_from(Delivery)) == 0
    assert model.calls == 1 and claim(f, s) is None
