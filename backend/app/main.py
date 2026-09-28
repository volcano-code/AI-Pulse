import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from sqlalchemy import func, select
from . import __version__
from .bootstrap import bootstrap
from .automation import automation_router
from .durable import enqueue, QueueFull, IdempotencyConflict
from .delivery import release_reviewed
from .briefing import brief_payload
from .config import Settings
from .db import Base, make_database
from .jobs import BusyError, execute_run, new_run, run_payload
from .models import Article, Snapshot, Source, Workspace, Brief, Run, FetchRun, Event, EventArticle, EventVersion
from .retrieval import answer_question
from .research_api import research_router
from .research import recover_interrupted_research
from .schemas import Preferences, RunRequest, SourcePatch, BookmarkPatch, Question, Approval
from .security import authorize
from .timeutil import iso, utcnow

STATIC = Path(__file__).parent / "static"

def create_app(settings: Settings | None = None):
    settings = settings or Settings()
    engine, factory = make_database(settings)
    @asynccontextmanager
    async def lifespan(app):
        if settings.auto_init_db:
            Base.metadata.create_all(engine)
        bootstrap(factory, settings)
        recover_interrupted_research(factory)
        yield
        engine.dispose()
    app = FastAPI(title="AI Pulse", version=__version__, lifespan=lifespan,
                  description="Single-owner milestone 1. Replay and live data are isolated.")
    app.state.settings, app.state.session_factory = settings, factory
    app.state.engine = engine
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver", "api"])
    api = APIRouter(prefix="/api/v1", dependencies=[Depends(authorize)])
    def get_db():
        with factory() as db:
            yield db
    DB = Annotated[object, Depends(get_db)]

    @app.get("/health")
    def health():
        with factory() as db:
            db.execute(select(1))
        return {"status": "ok", "version": __version__}

    @api.get("/status")
    def status(db: DB):
        return {"version": __version__, "data_mode": settings.data_mode,
                "generation_mode": settings.llm_mode,
                "model": settings.llm_model if settings.llm_mode == "live" else None,
                "articles": db.scalar(select(func.count()).select_from(Article)),
                "events": db.scalar(select(func.count()).select_from(Event)),
                "snapshots": db.scalar(select(func.count()).select_from(Snapshot)),
                "briefs": db.scalar(select(func.count()).select_from(Brief)),
                "sources": db.scalar(select(func.count()).select_from(Source)),
                "runtime": "database-worker" if settings.task_mode == "durable" else "single-process-local", "server_time": iso(utcnow()),
                "capabilities": {"bounded_evidence_research": True, "read_version_memory": True,
                                 "event_grouping": True, "event_version_history": True,
                                 "killable_source_fetch": True, "langgraph": False, "vector_search": False, "autonomous_web_research": False,
                                 "automatic_delivery": settings.task_mode == "durable",
                                 "durable_tasks": settings.task_mode == "durable",
                                 "smtp_delivery_configured": settings.delivery_transport == "smtp", "lexical_evidence_search": True}}

    @api.get("/sources")
    def sources(db: DB):
        return [{"id": s.id, "name": s.name, "url": s.url, "kind": s.kind,
                 "enabled": s.enabled, "last_success_at": s.last_success_at,
                 "last_attempt_at": s.last_attempt_at, "last_error": s.last_error,
                 "failure_count": s.failure_count}
                for s in db.scalars(select(Source).order_by(Source.id))]

    @api.patch("/sources/{source_id}")
    def patch_source(source_id: str, body: SourcePatch, db: DB):
        source = db.get(Source, source_id)
        if not source:
            raise HTTPException(404, "Source not found")
        source.enabled = body.enabled
        db.commit()
        return {"id": source.id, "enabled": source.enabled}

    @api.get("/preferences")
    def preferences(db: DB):
        return db.get(Workspace, 1).preferences

    @api.put("/preferences")
    def set_preferences(body: Preferences, db: DB):
        db.get(Workspace, 1).preferences = body.model_dump()
        db.commit()
        return body

    @api.get("/articles")
    def articles(db: DB, q: str = "", bookmarked: bool = False,
                 limit: int = Query(default=30, ge=1, le=100), offset: int = Query(default=0, ge=0)):
        stmt = select(Article)
        if q:
            stmt = stmt.where(Article.title.icontains(q, autoescape=True))
        if bookmarked:
            stmt = stmt.where(Article.bookmarked.is_(True))
        items = db.scalars(stmt.order_by(Article.published_at.desc(), Article.id).offset(offset).limit(limit))
        return [{"id": a.id, "title": a.title, "source_id": a.source_id,
                 "source_url": a.canonical_url, "published_at": a.published_at,
                 "updated_at": a.updated_at, "first_seen_at": a.first_seen_at,
                 "event_time": a.event_time, "topic": a.topic,
                 "bookmarked": a.bookmarked, "snapshot_id": a.current_snapshot_id,
                 "data_mode": a.data_mode} for a in items]

    @api.get("/events")
    def events(db: DB, limit: int = Query(default=30, ge=1, le=100), offset: int = Query(default=0, ge=0)):
        rows = list(db.scalars(select(Event).where(Event.data_mode == settings.data_mode)
                               .order_by(Event.updated_at.desc(), Event.id).offset(offset).limit(limit)))
        result = []
        for event in rows:
            members = db.execute(select(Article, EventArticle.match_score)
                                 .join(EventArticle, EventArticle.article_id == Article.id)
                                 .where(EventArticle.event_id == event.id)
                                 .order_by(Article.published_at.desc(), Article.id)).all()
            result.append({"id": event.id, "title": event.title, "topic": event.topic,
                           "created_at": event.created_at, "updated_at": event.updated_at,
                           "current_version_id": event.current_version_id,
                           "article_count": len(members),
                           "version_count": db.scalar(select(func.count()).select_from(EventVersion)
                                                      .where(EventVersion.event_id == event.id))})
        return result

    @api.get("/events/{event_id}")
    def event_detail(event_id: str, db: DB):
        event = db.get(Event, event_id)
        if not event or event.data_mode != settings.data_mode:
            raise HTTPException(404, "Event not found")
        members = db.execute(select(Article, EventArticle.match_score)
                             .join(EventArticle, EventArticle.article_id == Article.id)
                             .where(EventArticle.event_id == event.id)
                             .order_by(Article.published_at.desc(), Article.id)).all()
        versions = list(db.scalars(select(EventVersion).where(EventVersion.event_id == event.id)
                                   .order_by(EventVersion.created_at.desc(), EventVersion.id)))
        return {"id": event.id, "title": event.title, "topic": event.topic,
                "created_at": event.created_at, "updated_at": event.updated_at,
                "current_version_id": event.current_version_id,
                "articles": [{"id": article.id, "title": article.title,
                              "source_id": article.source_id, "source_url": article.canonical_url,
                              "snapshot_id": article.current_snapshot_id, "published_at": article.published_at,
                              "match_score": score} for article, score in members],
                "versions": [{"id": version.id, "created_at": version.created_at,
                              "article_snapshots": version.article_snapshots} for version in versions]}

    @api.get("/snapshots/{snapshot_id}")
    def snapshot(snapshot_id: str, db: DB):
        snap = db.get(Snapshot, snapshot_id)
        if not snap:
            raise HTTPException(404, "Snapshot not found")
        article = db.get(Article, snap.article_id)
        return {"id": snap.id, "title": snap.title, "text": snap.text,
                "content_hash": snap.content_hash, "captured_at": snap.captured_at,
                "scope": snap.scope, "source_url": article.canonical_url}

    @api.patch("/articles/{article_id}/bookmark")
    def bookmark(article_id: str, body: BookmarkPatch, db: DB):
        article = db.get(Article, article_id)
        if not article:
            raise HTTPException(404, "Article not found")
        article.bookmarked = body.bookmarked
        db.commit()
        return {"id": article.id, "bookmarked": article.bookmarked}

    @api.post("/runs", status_code=202)
    def start_run(body: RunRequest, background_tasks: BackgroundTasks):
        if body.kind == "ingest" and settings.data_mode != "live":
            raise HTTPException(409, "Replay 模式禁止联网采集；切换独立 live 数据库后重启")
        try:
            if settings.task_mode == "durable":
                run_id = enqueue(factory, body.kind, settings,
                    request_key=f"manual:{body.idempotency_key}" if body.idempotency_key else None)
            else:
                run_id = new_run(factory, body.kind)
                background_tasks.add_task(execute_run, factory, settings, run_id)
        except (BusyError, QueueFull, IdempotencyConflict) as exc:
            raise HTTPException(409, str(exc)) from None
        return {"run_id": run_id, "status": "queued"}

    @api.get("/runs")
    def runs(db: DB):
        return [run_payload(r) for r in db.scalars(select(Run).order_by(Run.created_at.desc()).limit(30))]

    @api.get("/runs/{run_id}")
    def run(run_id: str, db: DB):
        item = db.get(Run, run_id)
        if not item:
            raise HTTPException(404, "Run not found")
        return run_payload(item)

    @api.get("/briefs")
    def briefs(db: DB):
        return [{"id": b.id, "local_date": b.local_date, "created_at": b.created_at,
                 "status": b.status, "data_mode": b.data_mode, "generation_mode": b.generation_mode}
                for b in db.scalars(select(Brief).order_by(Brief.created_at.desc()).limit(40))]

    @api.get("/briefs/latest")
    def latest(db: DB):
        brief = db.scalar(select(Brief).order_by(Brief.created_at.desc()).limit(1))
        if not brief:
            raise HTTPException(404, "尚未生成简报")
        return brief_payload(db, brief)

    @api.get("/briefs/{brief_id}")
    def get_brief(brief_id: str, db: DB):
        brief = db.get(Brief, brief_id)
        if not brief:
            raise HTTPException(404, "Brief not found")
        return brief_payload(db, brief)

    @api.post("/briefs/{brief_id}/approve")
    def approve(brief_id: str, body: Approval, db: DB):
        brief = db.get(Brief, brief_id)
        if not brief:
            raise HTTPException(404, "Brief not found")
        brief.status, brief.approved_at = "published", iso(utcnow())
        release_reviewed(db, brief.id)
        db.commit()
        return {"id": brief.id, "status": brief.status, "approved_at": brief.approved_at}

    @api.get("/briefs/{brief_id}/markdown")
    def export(brief_id: str, db: DB):
        brief = db.get(Brief, brief_id)
        if not brief:
            raise HTTPException(404, "Brief not found")
        if brief.status != "published":
            raise HTTPException(409, "模型草稿尚未人工复核，不能作为正式简报导出")
        data = brief_payload(db, brief)
        lines = [f"# AI Pulse · {brief.local_date}", "", f"数据：{brief.data_mode} | 生成：{brief.generation_mode}",
                 f"窗口：{brief.window_start} — {brief.cutoff_at}；显示时区：{brief.timezone}",
                 "", "注意：摘录仅代表来源表述；模型内容经人工确认不等同于独立事实核验。", ""]
        if brief.data_mode == "replay":
            lines += ["> REPLAY：以下内容为合成测试数据，不是真实新闻。", ""]
        for item in data["items"]:
            lines += [f"## {item['position']}. {item['title']}", "", item["summary"], "",
                      f"来源：{item['source_url']}", f"证据快照：{item['snapshot_id']}，字符 {item['quote_start']}–{item['quote_end']}", ""]
        return PlainTextResponse("\n".join(lines), media_type="text/markdown",
                                 headers={"Content-Disposition": f'attachment; filename="ai-pulse-{brief.local_date}.md"'})

    @api.post("/ask")
    def ask(body: Question, db: DB):
        try:
            return answer_question(db, body.question, body.brief_id, settings.data_mode)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from None

    @api.post("/ask/stream")
    def ask_stream(body: Question, db: DB):
        try:
            result = answer_question(db, body.question, body.brief_id, settings.data_mode)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from None
        async def events():
            def encode(event, data):
                return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
            yield encode("retrieval", {"searched_documents": result["searched_documents"]})
            for citation in result["citations"]:
                yield encode("citation", citation)
                await asyncio.sleep(0)
            yield encode("done", result)
        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    api.include_router(automation_router(factory, settings))
    api.include_router(research_router(factory, settings))
    app.include_router(api)
    if STATIC.exists():
        app.mount("/static", StaticFiles(directory=STATIC), name="static")
        @app.get("/", include_in_schema=False)
        def index():
            return FileResponse(STATIC / "index.html")
    return app
