import json
from datetime import timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from .config import Settings
from .llm import ModelClient, PROMPT_VERSION, verify_quote
from .models import Article, Snapshot, Source, Workspace, Brief, BriefItem
from .schemas import Preferences
from .textutil import digest
from .timeutil import iso, parse_time, utcnow
from .reading import read_status


def choose_candidates(db, prefs: Preferences, cutoff, mode: str):
    start = cutoff - timedelta(hours=prefs.lookback_hours)
    # Time, enabled source and data-mode filters precede ranking. Unknown/future dates are not news.
    pairs = db.execute(select(Article, Snapshot, Source)
        .join(Snapshot, Article.current_snapshot_id == Snapshot.id)
        .join(Source, Article.source_id == Source.id)
        .where(Article.data_mode == mode, Source.enabled.is_(True),
               Article.published_at.is_not(None), Article.published_at >= iso(start),
               Article.published_at <= iso(cutoff), Article.first_seen_at <= iso(cutoff),
               Snapshot.captured_at <= iso(cutoff))
        .order_by(Article.published_at.desc(), Article.id.asc()).limit(1000)).all()
    ranked = []
    for article, snap, source in pairs:
        text = (snap.title + " " + snap.text).casefold()
        if any(x.casefold() in text for x in prefs.blocked_keywords):
            continue
        age_hours = (cutoff - parse_time(article.published_at)).total_seconds() / 3600
        preferred = article.topic in prefs.topics
        score = (2 if preferred else 0) + max(0, 1 - age_hours / prefs.lookback_hours)
        reason = f"匹配关注主题：{article.topic}" if preferred else "保留主题多样性；位于当前时间窗口"
        ranked.append((score, article.published_at, article.id, article, snap, source, reason))
    ranked.sort(key=lambda x: (-x[0], x[1], x[2]))
    chosen, seen = [], set()
    for _, _, _, article, snap, source, reason in ranked:
        if snap.content_hash in seen:
            continue
        seen.add(snap.content_hash)
        chosen.append((article, snap, source, reason))
        if len(chosen) == prefs.max_items:
            break
    return chosen


def build_brief(session_factory, settings: Settings, progress=lambda *_: None,
                model_client=None, cutoff=None, commit_guard=None) -> dict:
    cutoff = cutoff or utcnow()
    progress("select", "按发布时间、来源开关和用户偏好筛选；旧闻与未来时间不进入日报")
    with session_factory() as db:
        prefs = Preferences.model_validate(db.get(Workspace, 1).preferences)
        chosen = choose_candidates(db, prefs, cutoff, settings.data_mode)
        local_date = cutoff.astimezone(ZoneInfo(prefs.timezone)).date().isoformat()
        source_health = [{"id": s.id, "name": s.name, "last_success_at": s.last_success_at,
                          "last_error": s.last_error, "enabled": s.enabled}
                         for s in db.scalars(select(Source).order_by(Source.id))]
        identity = {"local_date": local_date, "preferences": prefs.model_dump(),
                    "snapshots": [{"id": snap.id, "published_at": a.published_at, "topic": a.topic, "source_id": src.id, "url": a.canonical_url} for a, snap, src, _ in chosen],
                    "data_mode": settings.data_mode, "generation_mode": settings.llm_mode,
                    "model": settings.llm_model if settings.llm_mode == "live" else None,
                    "prompt_version": PROMPT_VERSION,
                    # Poll timestamps are observability data, not a new edition.
                    "source_health": [{k: v for k, v in item.items() if k != "last_success_at"}
                                      for item in source_health]}
        fingerprint = digest(json.dumps(identity, sort_keys=True, ensure_ascii=False))
        existing = db.scalar(select(Brief).where(Brief.fingerprint == fingerprint))
        if existing:
            return {"brief_id": existing.id, "cached": True, "items": len(chosen)}
    drafts, usages = [], []
    for index, (article, snap, source, reason) in enumerate(chosen):
        progress("extract", f"处理 {index + 1}/{len(chosen)}：{source.name}")
        if settings.llm_mode == "live":
            draft, usage = (model_client or ModelClient(settings)).draft(snap.title, snap.text)
            title, summary, quote = draft.title, draft.summary, draft.evidence_quote
            verification = "quote_match_only"
            usages.append(usage)
            progress("model_usage", json.dumps(usage, ensure_ascii=False))
        else:
            title = snap.title
            # Exact excerpt, never labelled a model-generated summary or an independently proven fact.
            quote = snap.text[:380]
            summary, verification = quote, "exact_excerpt"
        start, end = verify_quote(quote, snap.text)
        drafts.append(dict(article_id=article.id, snapshot_id=snap.id, position=index + 1,
                           title=title, summary=summary, evidence_quote=quote,
                           source_name=source.name, source_url=article.canonical_url,
                           published_at=article.published_at, topic=article.topic,
                           quote_start=start, quote_end=end, verification=verification,
                           relevance_reason=reason))
    progress("gate", "原文位置校验通过；模型草稿需人工确认，原文摘录仍只代表来源表述")
    usage = {"model_calls": len(usages),
             "prompt_tokens": sum(u.get("prompt_tokens") or 0 for u in usages) if usages and all(u.get("prompt_tokens") is not None for u in usages) else None,
             "completion_tokens": sum(u.get("completion_tokens") or 0 for u in usages) if usages and all(u.get("completion_tokens") is not None for u in usages) else None,
             "cost_usd": 0 if not usages else None}
    try:
        with session_factory.begin() as db:
            if commit_guard:
                commit_guard(db)
            brief = Brief(fingerprint=fingerprint, local_date=local_date, cutoff_at=iso(cutoff),
                          window_start=iso(cutoff - timedelta(hours=prefs.lookback_hours)),
                          timezone=prefs.timezone, data_mode=settings.data_mode,
                          generation_mode=settings.llm_mode,
                          model=settings.llm_model if settings.llm_mode == "live" else None,
                          status="needs_review" if settings.llm_mode == "live" and drafts else "published",
                          prompt_version=PROMPT_VERSION, preferences=prefs.model_dump(),
                          source_health=source_health, usage=usage)
            db.add(brief)
            db.flush()
            for draft in drafts:
                db.add(BriefItem(brief_id=brief.id, **draft))
            brief_id = brief.id
    except IntegrityError:
        with session_factory() as db:
            cached = db.scalar(select(Brief).where(Brief.fingerprint == fingerprint))
            if cached:
                return {"brief_id": cached.id, "cached": True, "items": len(chosen)}
        raise
    progress("save", f"简报已保存，共 {len(drafts)} 条")
    return {"brief_id": brief_id, "cached": False, "items": len(drafts)}


def brief_payload(db, brief: Brief):
    items = []
    for item in db.scalars(select(BriefItem).where(BriefItem.brief_id == brief.id).order_by(BriefItem.position)):
        article = db.get(Article, item.article_id)
        snap = db.get(Snapshot, item.snapshot_id)
        source = db.get(Source, article.source_id)
        items.append({"id": item.id, "position": item.position, "title": item.title,
                      "summary": item.summary, "article_id": article.id,
                      "snapshot_id": snap.id, "source_name": item.source_name,
                      "source_url": item.source_url, "published_at": item.published_at,
                      "captured_at": snap.captured_at, "scope": snap.scope,
                      "topic": item.topic, "bookmarked": article.bookmarked,
                      "evidence_quote": item.evidence_quote, "quote_start": item.quote_start,
                      "quote_end": item.quote_end, "verification": item.verification,
                      "relevance_reason": item.relevance_reason,
                      "reading": read_status(db, article.id, snap.id)})
    return {"id": brief.id, "local_date": brief.local_date, "created_at": brief.created_at,
            "cutoff_at": brief.cutoff_at, "window_start": brief.window_start,
            "timezone": brief.timezone, "status": brief.status, "data_mode": brief.data_mode,
            "generation_mode": brief.generation_mode, "model": brief.model,
            "approved_at": brief.approved_at, "usage": brief.usage,
            "source_health": brief.source_health, "items": items}
