"""Read-only preflight checks for corpus embedding operations."""
from __future__ import annotations
from sqlalchemy import text
from .embedding_gateway import FixtureEmbeddingProvider


def preflight_database(db, settings) -> dict:
    dialect = db.bind.dialect.name
    if settings.embedding_provider == "openai" and dialect != "postgresql":
        raise ValueError("Paid embedding backfill requires PostgreSQL")
    required = ("evidence_chunks", "snapshots", "articles")
    from sqlalchemy import inspect
    tables = set(inspect(db.bind).get_table_names())
    missing = sorted(set(required) - tables)
    if missing:
        raise ValueError("Database migrations are incomplete: " + ",".join(missing))
    vector_ready = False
    if dialect == "postgresql":
        vector_ready = bool(db.scalar(text("SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname='vector')")))
        if not vector_ready:
            raise ValueError("PostgreSQL pgvector extension is required")
    return {"database":dialect,"pgvector_ready":vector_ready,
            "schema_ready":True,"data_mode":settings.data_mode,
            "provider":settings.embedding_provider,
            "model":settings.embedding_model,"dimensions":settings.embedding_dim}


def preflight_provider(settings, *, apply: bool, confirm_provider: str | None, allow_paid_api: bool) -> None:
    if not settings.embedding_provider or not settings.embedding_model or not settings.embedding_dim:
        raise ValueError("Embedding identity is incomplete")
    if not apply:
        return
    if confirm_provider != settings.embedding_provider:
        raise ValueError("Explicit provider confirmation required")
    if settings.embedding_provider != "fixture":
        if not allow_paid_api or settings.data_mode != "live":
            raise ValueError("Paid provider requires live mode and explicit paid API authorization")
        if not settings.embedding_api_key:
            raise ValueError("Paid provider credentials are required")
