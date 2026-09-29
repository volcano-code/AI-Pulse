"""Bounded lexical evidence retrieval with an explicit authorized snapshot scope."""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from sqlalchemy import select
from .models import Article, Snapshot, Source, BriefItem, Brief

STOP = {"the", "a", "an", "is", "what", "of", "and", "to", "in", "for", "了", "的", "什么", "如何", "请问", "是否", "哪些", "今天", "一下", "介绍", "告诉", "我想"}


@dataclass(frozen=True)
class RetrievalScope:
    snapshot_ids: tuple[str, ...]
    brief_id: str | None
    data_mode: str


def tokens(text: str) -> list[str]:
    result = re.findall(r"[a-z0-9][a-z0-9._-]*", text.casefold())
    for part in re.findall(r"[\u4e00-\u9fff]+", text):
        result.extend(part[i:i + 2] for i in range(max(0, len(part) - 1)))
        if len(part) == 1:
            result.append(part)
    return [t for t in result if t not in STOP]


def build_scope(db, brief_id: str | None, mode: str, limit: int = 1000) -> RetrievalScope:
    """Return the immutable snapshot IDs the caller is authorized to search.

    Historical brief scope is pinned to BriefItem.snapshot_id. Current scope is
    pinned to each article's current snapshot at query time. Ranking is a
    separate concern and must never redefine this security/history boundary.
    """
    if limit < 1 or limit > 5000:
        raise ValueError("Invalid retrieval scope limit")
    if brief_id:
        brief = db.get(Brief, brief_id)
        if not brief:
            raise LookupError("Brief not found")
        stmt = (
            select(BriefItem.snapshot_id)
            .join(Article, BriefItem.article_id == Article.id)
            .join(Source, Source.id == Article.source_id)
            .where(
                BriefItem.brief_id == brief_id,
                Article.data_mode == mode,
                Source.enabled.is_(True),
            )
            .order_by(BriefItem.position)
            .limit(limit)
        )
    else:
        stmt = (
            select(Article.current_snapshot_id)
            .join(Source, Source.id == Article.source_id)
            .where(
                Article.data_mode == mode,
                Source.enabled.is_(True),
                Article.current_snapshot_id.is_not(None),
            )
            .order_by(Article.published_at.desc())
            .limit(limit)
        )
    ids = tuple(x for x in db.scalars(stmt) if x)
    return RetrievalScope(snapshot_ids=ids, brief_id=brief_id, data_mode=mode)


def lexical_candidates(db, question: str, scope: RetrievalScope, limit: int = 50) -> list[dict]:
    if limit < 1 or limit > 100:
        raise ValueError("Invalid lexical candidate limit")
    if not scope.snapshot_ids:
        return []
    query = set(tokens(question))
    stmt = (
        select(Article, Snapshot, Source)
        .join(Snapshot, Snapshot.article_id == Article.id)
        .join(Source, Source.id == Article.source_id)
        .where(
            Snapshot.id.in_(scope.snapshot_ids),
            Article.data_mode == scope.data_mode,
            Source.enabled.is_(True),
        )
    )
    rows = db.execute(stmt).all()
    docs = [Counter(tokens(snap.title + " " + snap.text)) for _, snap, _ in rows]
    df = Counter(t for doc in docs for t in doc)
    average = sum(sum(d.values()) for d in docs) / max(1, len(docs))
    ranked = []
    for (article, snap, source), bag in zip(rows, docs):
        score = 0.0
        for token in query & bag.keys():
            freq = bag[token]
            inverse = math.log(1 + (len(docs) - df[token] + 0.5) / (df[token] + 0.5))
            score += inverse * freq * 2.2 / (freq + 1.2 * (0.25 + 0.75 * sum(bag.values()) / max(1, average)))
        if score <= 0:
            continue
        positions = [snap.text.casefold().find(t) for t in query if t in snap.text.casefold()]
        start = max(0, min(positions or [0]) - 70)
        ranked.append((score, article, snap, source, start))
    ranked.sort(key=lambda row: (-row[0], row[1].id))
    return [{
        "article_id": article.id,
        "snapshot_id": snap.id,
        "title": snap.title,
        "source_name": source.name,
        "url": article.canonical_url,
        "quote": snap.text[start:start + 550],
        "quote_start": start,
        "quote_end": min(len(snap.text), start + 550),
        "published_at": article.published_at,
        "lexical_score": float(score),
    } for score, article, snap, source, start in ranked[:limit]]


def answer_question(db, question: str, brief_id: str | None, mode: str) -> dict:
    scope = build_scope(db, brief_id, mode)
    citations = lexical_candidates(db, question, scope, limit=5)
    return {
        "question": question,
        "mode": "lexical_evidence",
        "data_mode": mode,
        "answer": "找到以下原文片段。这里只做本地证据检索，不生成推断结论，也不代表已独立验证。" if citations else "当前资料中没有匹配到足够证据；不能据此给出结论。",
        "citations": citations,
        "searched_documents": len(scope.snapshot_ids),
        "abstained": not citations,
    }
