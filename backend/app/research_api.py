from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select
from .models import Investigation, ReadingState
from .reading import article_changes, article_in_mode, mark_read, updates
from .research import execute_research, investigation_payload, ResearchConflict
from .schemas import ReadMarker, ResearchRequest


def research_router(factory, settings):
    router = APIRouter()

    @router.get("/updates")
    def get_updates(limit: int = Query(default=50, ge=1, le=100)):
        with factory() as db:
            return updates(db, settings.data_mode, limit)

    @router.get("/articles/{article_id}/changes")
    def changes(article_id: str, target_snapshot_id: str | None = Query(default=None, max_length=36)):
        with factory() as db:
            try:
                return article_changes(db, article_id, settings.data_mode, target_snapshot_id)
            except LookupError as exc:
                raise HTTPException(404, str(exc)) from None
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from None

    @router.put("/articles/{article_id}/read")
    def read(article_id: str, body: ReadMarker):
        with factory.begin() as db:
            try:
                return mark_read(db, article_id, body.snapshot_id, settings.data_mode)
            except LookupError as exc:
                raise HTTPException(404, str(exc)) from None
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from None

    @router.delete("/articles/{article_id}/read")
    def unread(article_id: str):
        with factory.begin() as db:
            try:
                article_in_mode(db, article_id, settings.data_mode)
            except LookupError as exc:
                raise HTTPException(404, str(exc)) from None
            record = db.get(ReadingState, article_id)
            if record:
                db.delete(record)
        return {"article_id": article_id, "status": "unread"}

    @router.post("/investigations")
    def research(body: ResearchRequest):
        try:
            return execute_research(factory, settings, body)
        except ResearchConflict as exc:
            raise HTTPException(409, str(exc)) from None
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from None

    @router.get("/investigations")
    def investigations(limit: int = Query(default=20, ge=1, le=100)):
        with factory() as db:
            return [investigation_payload(row) for row in db.scalars(select(Investigation)
                    .where(Investigation.data_mode == settings.data_mode)
                    .order_by(Investigation.created_at.desc()).limit(limit))]

    @router.get("/investigations/{run_id}")
    def investigation(run_id: str):
        with factory() as db:
            row = db.get(Investigation, run_id)
            if not row or row.data_mode != settings.data_mode:
                raise HTTPException(404, "Investigation not found")
            return investigation_payload(row)

    return router
