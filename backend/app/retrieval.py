"""Bounded lexical evidence lookup. Not advertised as vector RAG or web research."""
import math
import re
from collections import Counter
from sqlalchemy import select
from .models import Article, Snapshot, Source, BriefItem, Brief

STOP = {"the", "a", "an", "is", "what", "of", "and", "to", "in", "for", "了", "的", "什么", "如何", "请问", "是否", "哪些", "今天", "一下", "介绍", "告诉", "我想"}

def tokens(text: str) -> list[str]:
    result = re.findall(r"[a-z0-9][a-z0-9._-]*", text.casefold())
    for part in re.findall(r"[\u4e00-\u9fff]+", text):
        result.extend(part[i:i + 2] for i in range(max(0, len(part) - 1)))
        if len(part) == 1:
            result.append(part)
    return [t for t in result if t not in STOP]


def answer_question(db, question: str, brief_id: str | None, mode: str) -> dict:
    query = set(tokens(question))
    stmt = select(Article, Snapshot, Source).join(Snapshot, Article.current_snapshot_id == Snapshot.id).join(Source, Source.id == Article.source_id)
    if brief_id:
        brief = db.get(Brief, brief_id)
        if not brief:
            raise LookupError("Brief not found")
        # Questions about an old edition must read THAT edition's snapshots, not today's versions.
        stmt = select(Article, Snapshot, Source).join(BriefItem, BriefItem.article_id == Article.id).join(Snapshot, Snapshot.id == BriefItem.snapshot_id).join(Source, Source.id == Article.source_id).where(BriefItem.brief_id == brief_id)
    stmt = stmt.where(Article.data_mode == mode, Source.enabled.is_(True)).order_by(Article.published_at.desc()).limit(1000)
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
    citations = [{"article_id": a.id, "snapshot_id": s.id, "title": s.title,
                  "source_name": src.name, "url": a.canonical_url,
                  "quote": s.text[start:start + 550], "quote_start": start,
                  "quote_end": min(len(s.text), start + 550), "published_at": a.published_at}
                 for _, a, s, src, start in ranked[:5]]
    return {"question": question, "mode": "lexical_evidence", "data_mode": mode,
            "answer": "找到以下原文片段。这里只做本地证据检索，不生成推断结论，也不代表已独立验证。" if citations else "当前资料中没有匹配到足够证据；不能据此给出结论。",
            "citations": citations, "searched_documents": len(rows), "abstained": not citations}
