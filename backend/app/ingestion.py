from sqlalchemy import select
from .config import Settings
from .feeds import Entry, parse_feed
from .models import Article, Snapshot, Source, FetchRun
from .events import sync_events
from .textutil import canonical_url, clean_html, digest, topic_for
from .timeutil import iso, utcnow


def ingest_entries(db, source: Source, entries: list[Entry], mode: str) -> dict:
    counts = {"inserted": 0, "updated": 0, "unchanged": 0, "rejected": 0}
    for item in entries:
        try:
            url = canonical_url(item.url)
        except ValueError:
            counts["rejected"] += 1
            continue
        title = clean_html(item.title)[:1000]
        body = clean_html(item.text) or title
        if not title:
            counts["rejected"] += 1
            continue
        # Full source provenance is kept on each article. Exact-content grouping happens at selection.
        checksum = digest(title + "\n" + body)
        article = db.scalar(select(Article).where(Article.canonical_url == url))
        # A URL owned by another feed is not permission to overwrite its evidence.
        # Keep the original provenance; cross-source associations need a separate model.
        if article is not None and article.source_id != source.id:
            counts["rejected"] += 1
            continue
        new = article is None
        if new:
            article = Article(canonical_url=url, source_id=source.id, title=title,
                              published_at=item.published_at, updated_at=item.updated_at,
                              data_mode=mode, topic=topic_for(title + " " + body, source.id))
            db.add(article)
            db.flush()
        elif article.data_mode != mode:
            raise ValueError("Mixed data modes are forbidden")
        snap = db.scalar(select(Snapshot).where(Snapshot.article_id == article.id,
                                               Snapshot.content_hash == checksum))
        if snap and article.current_snapshot_id == snap.id:
            counts["unchanged"] += 1
            # Metadata correction does not rewrite an immutable snapshot.
            if item.published_at:
                article.published_at = item.published_at
            if item.updated_at:
                article.updated_at = item.updated_at
            continue
        if not snap:
            snap = Snapshot(article_id=article.id, title=title, text=body, content_hash=checksum)
            db.add(snap)
            db.flush()
        article.current_snapshot_id, article.title = snap.id, title
        article.topic = topic_for(title + " " + body, source.id)
        article.updated_at = item.updated_at or article.updated_at
        article.published_at = item.published_at or article.published_at
        counts["inserted" if new else "updated"] += 1
    return counts


def fetch_source(session_factory, source_id: str, settings: Settings, fetcher, commit_guard=None) -> dict:
    if settings.data_mode != "live":
        raise ValueError("Network ingestion is disabled in replay mode")
    with session_factory.begin() as db:
        if commit_guard:
            commit_guard(db)
        source = db.get(Source, source_id)
        if source is None or not source.enabled:
            return {"source_id": source_id, "status": "skipped"}
        attempt = FetchRun(source_id=source.id)
        db.add(attempt)
        db.flush()
        attempt_id, url, kind = attempt.id, source.url, source.kind
        source.last_attempt_at = iso(utcnow())
    try:
        payload = fetcher.fetch(url)
        entries = parse_feed(payload, kind)
        if not entries:
            raise ValueError("No parseable entries returned; not treating this as fresh content")
        with session_factory.begin() as db:
            if commit_guard:
                commit_guard(db)
            source = db.get(Source, source_id)
            counts = ingest_entries(db, source, entries, settings.data_mode)
            if not sum(counts[k] for k in ("inserted", "updated", "unchanged")):
                raise ValueError("All feed entries were rejected; no valid content was observed")
            event_counts = sync_events(db, settings.data_mode)
            counts.update(event_counts)
            attempt = db.get(FetchRun, attempt_id)
            attempt.status, attempt.counts, attempt.finished_at = "succeeded", counts, iso(utcnow())
            source.last_success_at, source.last_error, source.failure_count = iso(utcnow()), None, 0
        return {"source_id": source_id, "status": "succeeded", **counts}
    except Exception as exc:
        # Never leak provider keys, request headers or untrusted upstream bodies.
        raw_code = getattr(exc, "code", None)
        code = raw_code if isinstance(raw_code, str) else ("feed_parse_error" if isinstance(exc, (ValueError, SyntaxError)) or type(exc).__name__ == "ParseError" else "source_error")
        message = f"{type(exc).__name__}[{code}]: source fetch/parse failed; check URL, network and feed format"
        with session_factory.begin() as db:
            if commit_guard:
                commit_guard(db)
            attempt = db.get(FetchRun, attempt_id)
            attempt.status, attempt.error, attempt.finished_at = "failed", message, iso(utcnow())
            source = db.get(Source, source_id)
            source.last_error, source.failure_count = message, source.failure_count + 1
        return {"source_id": source_id, "status": "failed", "error": message, "failure_code": code}
