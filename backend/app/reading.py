"""Explicit read markers refer to immutable snapshots, not mutable articles.

Text differences are NOT event clustering, fact changes, or independent verification.
"""
import difflib
import re
from sqlalchemy import select
from .models import Article, Snapshot, Source, ReadingState
from .timeutil import iso, utcnow

MAX_DIFF_CHARS = 30_000
MAX_CHANGES = 80


def article_in_mode(db, article_id, mode):
    article = db.get(Article, article_id)
    if not article or article.data_mode != mode:
        raise LookupError("Article not found")
    return article


def mark_read(db, article_id, snapshot_id, mode):
    article_in_mode(db, article_id, mode)
    snap = db.get(Snapshot, snapshot_id)
    if not snap or snap.article_id != article_id:
        raise ValueError("Snapshot does not belong to this article")
    record = db.get(ReadingState, article_id)
    if record is None:
        record = ReadingState(article_id=article_id, snapshot_id=snapshot_id)
        db.add(record)
    record.snapshot_id, record.read_at = snapshot_id, iso(utcnow())
    db.flush()
    return {"article_id": article_id, "snapshot_id": snapshot_id, "read_at": record.read_at}


def read_status(db, article_id, target_snapshot_id):
    record = db.get(ReadingState, article_id)
    return {"status": "unread" if not record else "read" if record.snapshot_id == target_snapshot_id else "changed_since_read",
            "read_snapshot_id": record.snapshot_id if record else None,
            "read_at": record.read_at if record else None}


def _segments(text):
    # Offsets refer to original Unicode code points; never normalize stored text.
    return [(m.start(), m.end(), m.group()) for m in re.finditer(r"[^\n。！？!?]+[。！？!?]?|\n", text)]


def compare_text(before, after):
    old, new = _segments(before[:MAX_DIFF_CHARS]), _segments(after[:MAX_DIFF_CHARS])
    matcher = difflib.SequenceMatcher(a=[s[2] for s in old], b=[s[2] for s in new], autojunk=True)
    changes = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        for kind, values in (("removed", old[i1:i2]), ("added", new[j1:j2])):
            for start, end, text in values:
                changes.append({"kind": kind, "text": text, "start": start, "end": end})
                if len(changes) > MAX_CHANGES:
                    return {"changes": changes[:MAX_CHANGES], "truncated": True}
    return {"changes": changes, "truncated": len(before) > MAX_DIFF_CHARS or len(after) > MAX_DIFF_CHARS}


def article_changes(db, article_id, mode, target_snapshot_id=None):
    article = article_in_mode(db, article_id, mode)
    current = db.get(Snapshot, target_snapshot_id or article.current_snapshot_id)
    if not current or current.article_id != article_id:
        raise ValueError("Target snapshot does not belong to this article")
    status = read_status(db, article_id, current.id)
    baseline = db.get(Snapshot, status["read_snapshot_id"]) if status["read_snapshot_id"] else None
    difference = compare_text(baseline.text, current.text) if baseline else {"changes": [], "truncated": False}
    return {"article_id": article_id, "title": current.title, "target_snapshot_id": current.id,
            "target_captured_at": current.captured_at, "source_url": article.canonical_url,
            "baseline_snapshot_id": baseline.id if baseline else None,
            "baseline_title": baseline.title if baseline else None,
            "title_changed": bool(baseline and baseline.title != current.title),
            **status, **difference,
            "note": "原文文本差异，不是语义事件聚类或独立事实核验；added 属于目标快照，removed 属于已读快照。"}


def updates(db, mode, limit=50):
    rows = db.scalars(select(Article).join(Source).where(
        Article.data_mode == mode, Source.enabled.is_(True))
        .order_by(Article.published_at.desc(), Article.id).limit(limit)).all()
    return [{"article_id": a.id, "title": a.title, "snapshot_id": a.current_snapshot_id,
             "source_url": a.canonical_url, "source_id": a.source_id,
             "published_at": a.published_at, "topic": a.topic,
             **read_status(db, a.id, a.current_snapshot_id)} for a in rows]
