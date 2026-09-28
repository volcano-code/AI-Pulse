from sqlalchemy import select
from .config import Settings
from .models import Workspace, Source, Run, DailySchedule
from .schemas import Preferences
from .timeutil import iso, utcnow
from .events import sync_events

DEFAULT_SOURCES = [
    ("huggingface", "Hugging Face Blog", "https://huggingface.co/blog/feed.xml", "rss"),
    ("arxiv", "arXiv · cs.AI / cs.CL", "https://export.arxiv.org/api/query?search_query=cat:cs.AI+OR+cat:cs.CL&sortBy=submittedDate&sortOrder=descending&max_results=25", "atom"),
    ("langgraph", "LangGraph Releases", "https://api.github.com/repos/langchain-ai/langgraph/releases?per_page=20", "github"),
]

def bootstrap(session_factory, settings: Settings):
    with session_factory.begin() as db:
        workspace = db.get(Workspace, 1)
        if workspace and workspace.data_mode != settings.data_mode:
            raise RuntimeError("Database data mode mismatch. Live and replay require separate databases.")
        if not workspace:
            db.add(Workspace(id=1, data_mode=settings.data_mode,
                             preferences=Preferences(timezone=settings.timezone).model_dump()))
        for key, name, url, kind in DEFAULT_SOURCES:
            if not db.get(Source, key):
                db.add(Source(id=key, name=name, url=url, kind=kind))
        if not db.get(DailySchedule, 1):
            db.add(DailySchedule(id=1, timezone=settings.timezone))
        db.flush()
        # Backfill the stable event layer when opening a v0.3 database. Idempotent.
        sync_events(db, settings.data_mode)
        # Durable rows belong to the worker, not the API lifespan.
        # An API restart must never reset another process's lease.
        for run in db.scalars(select(Run).where(Run.engine == "local", Run.active_key.is_not(None))):
            run.status, run.active_key = "interrupted", None
            run.error = "API process restarted; submit a new run. Persisted content is retained."
            run.finished_at = iso(utcnow())
