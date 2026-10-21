"""Audited query vectors, not cached search results. Unknown calls stay blocked."""
from __future__ import annotations
import time
from sqlalchemy import select
from .embedding_gateway import EmbeddingBatch, EmbeddingGateway
from .models import QueryEmbedding, Workspace
from .provider_calls import CallBlocked, CallLedger, fingerprint


class QueryEmbeddingUnavailable(RuntimeError):
    def __init__(self, reason, call=None):
        self.reason, self.call = reason, call
        super().__init__(reason)


def _validate(batch, provider, model, dimensions):
    EmbeddingGateway._validated(batch, 1)
    if (batch.provider, batch.model, batch.dimensions) != (provider, model, dimensions):
        raise ValueError("Query embedding identity changed")
    if not any(float(v) != 0 for v in batch.vectors[0]):
        raise ValueError("Zero query vector cannot be used for cosine search")


def embed_saved_query(factory, settings, query, gateway, *, retry_authorization=None):
    if not isinstance(query, str) or not 2 <= len(query.strip()) <= 400 or len(query) > 400:
        raise ValueError("Query must contain 2..400 characters")
    p = gateway.provider
    provider, model, dim = p.provider, p.model, p.dimensions
    if (provider != settings.embedding_provider or model != settings.embedding_model or
            dim != settings.embedding_dim or type(dim) is not int or not 1 <= dim <= 16000):
        raise ValueError("Query provider does not match configured corpus")
    if provider != "fixture" and (settings.data_mode != "live" or not settings.embedding_query_allow_paid):
        raise QueryEmbeddingUnavailable("query_embedding_not_authorized")
    identity = fingerprint(["query-vector-v1", settings.data_mode, provider, model, dim,
                            getattr(p, "base_url", "fixture").rstrip("/"), query])
    def guard(db):
        db.execute(select(QueryEmbedding.call_id).limit(1))
        w = db.get(Workspace, 1)
        if not w or w.data_mode != settings.data_mode:
            raise ValueError("Query workspace scope mismatch")
        if provider != "fixture" and db.bind.dialect.name != "postgresql":
            raise ValueError("Paid query embeddings require PostgreSQL")
    handle = CallLedger(factory).reserve(operation_key="query:" + identity, request_hash=identity,
        kind="query_embedding", provider=provider, model=model, data_mode=settings.data_mode,
        commit_guard=guard, retry_authorization=retry_authorization)
    if handle.reused:
        with factory() as db:
            guard(db)
            saved = db.get(QueryEmbedding, handle.id)
            state = handle.read()
            if state["status"] != "succeeded" or saved is None:
                raise QueryEmbeddingUnavailable("query_embedding_blocked", state)
            batch = EmbeddingBatch(vectors=[saved.vector], provider=provider, model=model, dimensions=dim)
            if saved.dimensions != dim or saved.vector_hash != fingerprint(saved.vector):
                raise QueryEmbeddingUnavailable("query_embedding_cache_invalid", state)
            _validate(batch, provider, model, dim)
            return batch.vectors[0], {"reused": True, "embedding_calls": 0, "input_tokens": 0, "receipt": state}
    started = time.monotonic()
    try:
        batch = gateway.embed_query(query, receipt_observer=handle.observe)
        _validate(batch, provider, model, dim)
        vector = [float(v) for v in batch.vectors[0]]
        with factory.begin() as db:
            guard(db)
            db.add(QueryEmbedding(call_id=handle.id, dimensions=dim,
                                  vector=vector, vector_hash=fingerprint(vector)))
            handle.succeed(db, elapsed_ms=round((time.monotonic()-started)*1000), summary={"embedded": 1})
        return vector, {"reused": False, "embedding_calls": 1,
                        "input_tokens": handle.read()["input_tokens"], "receipt": handle.read()}
    except BaseException as exc:
        try:
            handle.unknown(elapsed_ms=round((time.monotonic()-started)*1000))
        except Exception:
            pass  # The durable in_flight record still blocks another dispatch.
        if not isinstance(exc, Exception):
            raise
        try:
            saved = handle.read()
        except Exception:
            saved = None
        raise QueryEmbeddingUnavailable("query_embedding_unconfirmed", saved) from None
