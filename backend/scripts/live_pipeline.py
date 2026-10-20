"""Run the real evidence pipeline in a NEW disposable database, without paid APIs.

From backend: python -m scripts.live_pipeline --output-dir /tmp/pulse-live-run
The CLI only reads DEFAULT_SOURCES. No mail, model, embedding or production DB.
Offline tests must explicitly pass fixture_payloads; their reports say replay.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import func, select

from app.bootstrap import DEFAULT_SOURCES, bootstrap
from app.briefing import brief_payload, build_brief
from app.config import Settings
from app.db import make_database
from app.events import release_identity, sync_events
from app.feeds import parse_feed
from app.ingestion import fetch_source, ingest_entries
from app.models import Article, Brief, BriefItem, Event, EventArticle, EventVersion, Snapshot, Source
from app.process_fetcher import ProcessFetcher
from app.textutil import digest
from app.timeutil import iso, utcnow

BACKEND = Path(__file__).resolve().parents[1]


def isolated_settings(directory: Path, mode: str) -> Settings:
    # Explicit overrides prevent the operator's .env/credentials/DB URL from
    # turning this acceptance check into a paid or production operation.
    return Settings(
        _env_file=None, database_url='sqlite:///' + str(directory / 'pipeline.db'),
        data_mode=mode, llm_mode='extractive', llm_api_key='', llm_model='',
        embedding_provider='', embedding_api_key='', embedding_model='', embedding_dim=0,
        retrieval_mode='lexical', admin_token='', auto_init_db=False, task_mode='local',
        delivery_transport='file', mail_from='canary@example.invalid',
        mail_to='canary@example.invalid', outbox_dir=str(directory / 'unused-outbox'),
        timezone='UTC', allowed_fetch_hosts='huggingface.co,export.arxiv.org,api.github.com',
        max_fetch_bytes=2_000_000, fetch_timeout_seconds=12, fetch_deadline_seconds=20,
    )


def migrate(settings: Settings) -> None:
    config = Config(str(BACKEND / 'alembic.ini'))
    config.set_main_option('script_location', str(BACKEND / 'migrations'))
    config.attributes['settings_override'] = settings
    command.upgrade(config, 'head')


class CaptureReceipt:
    """Capture hashes/byte counts, not raw response bodies or credentials."""
    def __init__(self, fetcher):
        self.fetcher = fetcher
        self.receipt = {}

    def fetch(self, url: str) -> bytes:
        payload = self.fetcher.fetch(url)
        self.receipt = {'response_bytes': len(payload),
                        'response_sha256': hashlib.sha256(payload).hexdigest(),
                        'fetched_at': iso(utcnow())}
        return payload


def audit_saved_data(db, brief: Brief, data_mode: str) -> dict:
    """Independently verify persisted data, without trusting generator flags."""
    failures = []
    totals = {model.__tablename__: db.scalar(select(func.count()).select_from(model))
              for model in (Article, Snapshot, Event, EventVersion)}
    articles = {a.id: a for a in db.scalars(select(Article))}
    snapshots = {s.id: s for s in db.scalars(select(Snapshot))}
    for article in articles.values():
        snap = snapshots.get(article.current_snapshot_id)
        if article.data_mode != data_mode or not snap or snap.article_id != article.id:
            failures.append('article_snapshot_scope')
    for snap in snapshots.values():
        if snap.content_hash != digest(snap.title + '\n' + snap.text):
            failures.append('snapshot_integrity')
    linked = set()
    for event in db.scalars(select(Event)):
        version = db.get(EventVersion, event.current_version_id)
        members = list(db.scalars(select(EventArticle.article_id).where(EventArticle.event_id == event.id)))
        linked.update(members)
        expected = sorted((aid, articles[aid].current_snapshot_id) for aid in members)
        actual = sorted((m['article_id'], m['snapshot_id']) for m in version.article_snapshots) if version else []
        if event.data_mode != data_mode or not version or version.event_id != event.id or expected != actual:
            failures.append('event_version_integrity')
        releases = {release_identity(articles[aid].title) for aid in members}
        releases.discard(None)
        if len(releases) > 1:
            failures.append('conflicting_release_identities')
    if linked != set(articles):
        failures.append('ungrouped_articles')
    count = 0
    for item in db.scalars(select(BriefItem).where(BriefItem.brief_id == brief.id)):
        snap, article = snapshots.get(item.snapshot_id), articles.get(item.article_id)
        if (not snap or not article or snap.article_id != article.id
                or article.data_mode != data_mode or item.source_url != article.canonical_url
                or not (0 <= item.quote_start < item.quote_end <= len(snap.text))
                or snap.text[item.quote_start:item.quote_end] != item.evidence_quote):
            failures.append('brief_evidence_integrity')
        else:
            count += 1
        if (not item.published_at or not brief.window_start <= item.published_at <= brief.cutoff_at
                or snap and snap.captured_at > brief.cutoff_at):
            failures.append('brief_time_window')
    return {'totals': totals, 'quotes_validated': count,
            'failures': sorted(set(failures)), 'passed': not failures}


def render_markdown(payload: dict) -> str:
    # Do not publish full upstream bodies. Full excerpt/offset pairs remain in
    # the disposable DB; this artifact exposes titles, provenance and offsets.
    lines = ['# AI Pulse — 真实资料链路验收简报', '',
             '模式：原文摘录；未调用 LLM，未生成中文分析结论，未发送邮件。',
             '这里只验证保存的引用位置，不证明来源陈述为真。',
             f"信息截止：{payload['cutoff_at']}",
             f"时间窗口起点：{payload['window_start']}", '']
    for item in payload['items']:
        title = item['title'].replace('\n', ' ').replace('[', '(').replace(']', ')')
        lines.extend([f"## {item['position']}. {title}",
                      f"来源：{item['source_name']}；发布时间：{item['published_at']}",
                      f"原始链接：<{item['source_url']}>",
                      f"证据快照：`{item['snapshot_id']}`；字符区间："
                      f"`[{item['quote_start']}, {item['quote_end']})`；校验：`{item['verification']}`", ''])
    return '\n'.join(lines) + '\n'


def run_pipeline(output_dir: Path, *, min_documents: int = 20,
                 fixture_payloads: dict[str, bytes] | None = None) -> dict:
    if not 1 <= min_documents <= 90:
        raise ValueError('min_documents must be between 1 and 90')
    # Exclusive creation is intentional: never overwrite/reuse a user's DB.
    directory = Path(output_dir).absolute()
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    mode = 'live' if fixture_payloads is None else 'replay'
    settings = isolated_settings(directory, mode)
    report = {'schema_version': 1, 'started_at': iso(utcnow()),
              'execution': 'live_network' if mode == 'live' else 'offline_contract',
              'data_mode': mode, 'generation_mode': 'extractive', 'status': 'failed',
              'paid_model_calls': 0, 'embedding_calls': 0, 'email_attempts': 0,
              'sources': [], 'failures': []}
    engine = None
    try:
        migrate(settings)
        engine, factory = make_database(settings)
        bootstrap(factory, settings)
        for key, _, url, kind in DEFAULT_SOURCES:
            started = time.monotonic()
            capture = CaptureReceipt(ProcessFetcher(settings))
            if fixture_payloads is None:
                result = fetch_source(factory, key, settings, capture)
            else:
                # Offline contracts NEVER use a live-labelled workspace.
                try:
                    payload = fixture_payloads[key]
                    entries = parse_feed(payload, kind)
                    if not entries:
                        raise ValueError('empty fixture')
                    with factory.begin() as db:
                        counts = ingest_entries(db, db.get(Source, key), entries, 'replay')
                        if not sum(counts[k] for k in ('inserted', 'updated', 'unchanged')):
                            raise ValueError('no accepted entries')
                        counts.update(sync_events(db, 'replay'))
                    result = {'source_id': key, 'status': 'succeeded', **counts}
                    capture.receipt = {'response_bytes': len(payload),
                                       'response_sha256': hashlib.sha256(payload).hexdigest()}
                except Exception:
                    result = {'source_id': key, 'status': 'failed', 'failure_code': 'fixture_ingest_error'}
            # Upstream exceptions/error bodies must never reach artifacts.
            safe_keys = ('source_id', 'status', 'failure_code', 'inserted', 'updated',
                         'unchanged', 'rejected', 'events_created', 'articles_attached', 'event_versions_created')
            record = {k: result[k] for k in safe_keys if k in result}
            record.update(capture.receipt)
            record.update(endpoint=url, parser_kind=kind, elapsed_ms=round((time.monotonic() - started) * 1000))
            report['sources'].append(record)
        cutoff = utcnow()  # Never replace source publication dates with this.
        built = build_brief(factory, settings, cutoff=cutoff)
        again = build_brief(factory, settings, cutoff=cutoff)
        with factory() as db:
            brief = db.get(Brief, built['brief_id'])
            report['audit'] = audit_saved_data(db, brief, mode)
            payload = brief_payload(db, brief)
        report['brief'] = {'id': built['brief_id'], 'items': built['items'],
                           'cutoff_at': payload['cutoff_at'], 'window_start': payload['window_start'],
                           'generation_mode': payload['generation_mode'],
                           'idempotent': again['cached'] and again['brief_id'] == built['brief_id']}
        (directory / 'brief.md').write_text(render_markdown(payload), encoding='utf-8')
        failed_sources = [r['source_id'] for r in report['sources'] if r['status'] != 'succeeded']
        if failed_sources:
            report['failures'].append('source_ingestion_incomplete')
        if report['audit']['totals']['articles'] < min_documents:
            report['failures'].append('insufficient_documents')
        if not built['items']:
            report['failures'].append('no_current_brief_items')
        if not report['brief']['idempotent']:
            report['failures'].append('brief_not_idempotent')
        report['failures'].extend(report['audit']['failures'])
        report['status'] = 'passed' if not report['failures'] else 'partial'
    except Exception:
        # Keep an explicit failed receipt even for migration/internal errors.
        report['failures'].append('pipeline_execution_failed')
        report['status'] = 'failed'
    finally:
        if engine is not None:
            engine.dispose()
        report['finished_at'] = iso(utcnow())
        (directory / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--min-documents', type=int, default=20)
    args = parser.parse_args(argv)
    try:
        report = run_pipeline(args.output_dir, min_documents=args.min_documents)
    except (ValueError, OSError):
        print(json.dumps({'status': 'blocked', 'reason': 'new_output_directory_and_valid_budget_required'}))
        return 2
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
