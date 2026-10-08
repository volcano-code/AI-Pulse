"""Explicit, bounded corpus embedding backfill.

Dry run is the default. A write requires --apply and --confirm-provider.
Live provider calls require --allow-paid-api and a live data mode.
"""
from __future__ import annotations
import argparse
import json
from sqlalchemy import or_, select
from app.config import Settings
from app.db import make_database
from app.embedding_gateway import EmbeddingGateway, FixtureEmbeddingProvider
from app.embedding_backfill import backfill_embeddings
from app.models import EvidenceChunk, Snapshot, Article
from app.openai_embedding_provider import OpenAIEmbeddingProvider


def plan_backfill(db, settings, *, max_chunks: int):
    if max_chunks < 1 or max_chunks > 5000:
        raise ValueError("max_chunks must be between 1 and 5000")
    stmt = (select(EvidenceChunk.id)
            .join(Snapshot, EvidenceChunk.snapshot_id == Snapshot.id)
            .join(Article, Snapshot.article_id == Article.id)
            .where(Article.data_mode == settings.data_mode))
    if settings.embedding_provider:
        stmt = stmt.where(or_(
            EvidenceChunk.embedding_provider.is_(None),
            EvidenceChunk.embedding_provider != settings.embedding_provider,
            EvidenceChunk.embedding_model != settings.embedding_model,
            EvidenceChunk.embedding_dim != settings.embedding_dim,
        ))
    ids = list(db.scalars(stmt.order_by(EvidenceChunk.id).limit(max_chunks + 1)))
    return {"selected": min(len(ids), max_chunks), "has_more": len(ids) > max_chunks,
            "provider": settings.embedding_provider, "model": settings.embedding_model,
            "dimensions": settings.embedding_dim, "data_mode": settings.data_mode}


def run(settings, *, apply: bool, confirm_provider: str | None,
        allow_paid_api: bool, max_chunks: int, batch_size: int):
    if not settings.embedding_provider or not settings.embedding_model or not settings.embedding_dim:
        raise ValueError("Embedding provider, model and dimensions are required")
    if batch_size < 1 or batch_size > 128 or max_chunks < 1 or max_chunks > 5000:
        raise ValueError("Invalid backfill budget")
    engine, factory = make_database(settings)
    try:
        if apply and confirm_provider != settings.embedding_provider:
            raise ValueError("Explicit provider confirmation required")
        if apply and settings.embedding_provider == "openai":
            if not allow_paid_api or settings.data_mode != "live" or settings.database_url.startswith("sqlite"):
                raise ValueError("Paid backfill requires authorization, live data and PostgreSQL")
        with factory() as db:
            plan = plan_backfill(db, settings, max_chunks=max_chunks)
        if not apply:
            return {"status":"dry_run", **plan}
        if settings.embedding_provider == "openai":
            if not allow_paid_api or settings.data_mode != "live":
                raise ValueError("Paid embedding calls require explicit authorization and live data")
            provider = OpenAIEmbeddingProvider(
                api_key=settings.embedding_api_key, model=settings.embedding_model,
                dimensions=settings.embedding_dim, base_url=settings.embedding_base_url,
                timeout_seconds=settings.embedding_timeout_seconds)
        elif settings.embedding_provider == "fixture":
            provider = FixtureEmbeddingProvider(settings.embedding_dim)
        else:
            raise ValueError("Unsupported embedding provider")
        # This command is limited to a single data mode. The library backfill
        # must also apply the same scope before any write or provider call.
        with factory.begin() as db:
            result = backfill_embeddings(db, EmbeddingGateway(provider),
                                         batch_size=batch_size, max_chunks=max_chunks,
                                         data_mode=settings.data_mode)
        return {"status":"applied", **result}
    finally:
        engine.dispose()


def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument("--apply",action="store_true")
    parser.add_argument("--confirm-provider")
    parser.add_argument("--allow-paid-api",action="store_true")
    parser.add_argument("--max-chunks",type=int,default=100)
    parser.add_argument("--batch-size",type=int,default=16)
    args=parser.parse_args(argv)
    try:
        result=run(Settings(),apply=args.apply,confirm_provider=args.confirm_provider,
                   allow_paid_api=args.allow_paid_api,max_chunks=args.max_chunks,
                   batch_size=args.batch_size)
        print(json.dumps(result,ensure_ascii=False))
        return 0
    except Exception:
        print(json.dumps({"status":"failed","reason":"backfill_validation_or_execution_failed",
                          "billing":"unknown_if_paid_request_started"}))
        return 1


if __name__=="__main__":
    raise SystemExit(main())
