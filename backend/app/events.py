"""Stable event grouping for saved articles.

This is deliberately deterministic and local. It does not claim semantic truth:
it groups similar titles into a stable event id, preserves article provenance, and
records a new EventVersion whenever the member snapshot set changes.
"""
import re
from difflib import SequenceMatcher
from sqlalchemy import select
from .models import Article, Event, EventArticle, EventVersion
from .textutil import digest
from .timeutil import iso, utcnow

STOP = {"the","a","an","and","or","to","for","of","in","on","with","from","new","release","released","update","announcing","introducing",
        "发布","更新","宣布","推出","正式","最新","一个","一种","以及","关于"}
VARIANTS = {"python","typescript","javascript","java","rust","go","swift","kotlin","android","ios","vl","vision","audio"}

def event_tokens(title: str) -> set[str]:
    text = title.casefold()
    out = {x for x in re.findall(r"[a-z0-9][a-z0-9._+-]*", text) if x not in STOP and len(x) > 1}
    for block in re.findall(r"[\u4e00-\u9fff]+", text):
        out.update(block[i:i+2] for i in range(max(0, len(block)-1)) if block[i:i+2] not in STOP)
    return out

def similarity(a: str, b: str) -> float:
    ta, tb = event_tokens(a), event_tokens(b)
    jaccard = len(ta & tb) / max(1, len(ta | tb))
    na = " ".join(sorted(ta)); nb = " ".join(sorted(tb))
    sequence = SequenceMatcher(None, na, nb, autojunk=False).ratio() if na and nb else 0.0
    score = max(jaccard, sequence * 0.82)
    variants_a, variants_b = ta & VARIANTS, tb & VARIANTS
    if variants_a and variants_b and variants_a != variants_b:
        return min(score, 0.40)
    shared = ta & tb
    shared_identifier = any(any(ch.isdigit() for ch in token) or "-" in token for token in shared)
    if shared_identifier and len(shared) >= 2:
        score = max(score, min(0.74, 0.55 + 0.05 * len(shared)))
    return score

def _record_version(db, event: Event):
    rows = db.execute(select(EventArticle.article_id, Article.current_snapshot_id)
                      .join(Article, Article.id == EventArticle.article_id)
                      .where(EventArticle.event_id == event.id)
                      .order_by(EventArticle.article_id)).all()
    state = [{"article_id": aid, "snapshot_id": sid} for aid, sid in rows if sid]
    fingerprint = digest("\n".join(f"{x['article_id']}:{x['snapshot_id']}" for x in state))
    previous = db.scalar(select(EventVersion).where(EventVersion.event_id == event.id,
                                                    EventVersion.fingerprint == fingerprint))
    if previous:
        event.current_version_id = previous.id
        return previous
    version = EventVersion(event_id=event.id, fingerprint=fingerprint, article_snapshots=state)
    db.add(version); db.flush()
    event.current_version_id = version.id
    event.updated_at = iso(utcnow())
    return version

def sync_events(db, mode: str, threshold: float = 0.52) -> dict:
    """Attach ungrouped articles and version changed events. Existing memberships stay stable."""
    existing = set(db.scalars(select(EventArticle.article_id)))
    articles = list(db.scalars(select(Article).where(Article.data_mode == mode).order_by(Article.first_seen_at, Article.id)))
    created = attached = 0
    touched: set[str] = set()
    for article in articles:
        if article.id in existing:
            link = db.scalar(select(EventArticle).where(EventArticle.article_id == article.id))
            if link: touched.add(link.event_id)
            continue
        candidates = list(db.scalars(select(Event).where(Event.data_mode == mode, Event.topic == article.topic)))
        scored = [(similarity(article.title, event.title), event) for event in candidates]
        score, event = max(scored, default=(0.0, None), key=lambda x: x[0])
        if event is None or score < threshold:
            event = Event(title=article.title, topic=article.topic, data_mode=mode)
            db.add(event); db.flush(); created += 1
            score = 1.0
        db.add(EventArticle(event_id=event.id, article_id=article.id, match_score=round(score, 4)))
        attached += 1; touched.add(event.id)
    db.flush()
    versions = 0
    for event_id in touched:
        event = db.get(Event, event_id)
        before = event.current_version_id
        _record_version(db, event)
        versions += int(before != event.current_version_id)
    return {"events_created": created, "articles_attached": attached, "event_versions_created": versions}
