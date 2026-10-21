"""Scoped retrieval with durable query-call audit and explicit degradation."""
from __future__ import annotations
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker
from .embedding_gateway import EmbeddingGateway, FixtureEmbeddingProvider
from .hybrid_retrieval import hybrid_search
from .openai_embedding_provider import OpenAIEmbeddingProvider
from .query_embeddings import embed_saved_query, QueryEmbeddingUnavailable
from .provider_calls import CallBlocked
from .retrieval import answer_question, build_scope


def make_embedding_gateway(settings):
    provider = getattr(settings, "embedding_provider", "")
    if provider == "fixture":
        return EmbeddingGateway(FixtureEmbeddingProvider(settings.embedding_dim))
    if provider == "openai":
        return EmbeddingGateway(OpenAIEmbeddingProvider(
            api_key=settings.embedding_api_key, model=settings.embedding_model,
            dimensions=settings.embedding_dim, base_url=settings.embedding_base_url,
            timeout_seconds=settings.embedding_timeout_seconds))
    return None


def search_research_evidence(db, settings, query: str, brief_id: str | None, gateway=None) -> dict:
    if settings.retrieval_mode == "lexical":
        return answer_question(db, query, brief_id, settings.data_mode) | {
            "requested_mode": "lexical", "effective_mode": "lexical",
            "degraded": False, "degraded_reason": None,
            "query_embedding": {"status": "not_requested", "embedding_calls": 0}}
    scope = build_scope(db, brief_id, settings.data_mode)
    provider, model = settings.embedding_provider or None, settings.embedding_model or None
    vector, reason, audit = None, None, {"status": "not_requested", "embedding_calls": 0}
    if db.bind.dialect.name != "postgresql":
        reason = "vector_database_unavailable"
    elif not scope.snapshot_ids:
        reason = "empty_evidence_scope"
    elif not provider:
        reason = "embedding_provider_unavailable"
    elif provider != "fixture" and not settings.embedding_query_allow_paid:
        reason = "query_embedding_not_authorized"
    elif db.new or db.dirty or db.deleted:
        reason = "query_embedding_requires_read_only_session"
    elif not db.scalar(text("""SELECT EXISTS(SELECT 1 FROM evidence_chunks
        WHERE embedding_vector IS NOT NULL AND embedding_provider=:provider
        AND embedding_model=:model AND embedding_dim=:dim
        AND snapshot_id = ANY(CAST(:snapshots AS varchar[])))"""),
        {"provider": provider, "model": model, "dim": settings.embedding_dim,
         "snapshots": list(scope.snapshot_ids)}):
        reason = "scoped_corpus_embeddings_unavailable"
    else:
        try:
            gateway = gateway or make_embedding_gateway(settings)
            if gateway is None:
                reason = "embedding_provider_unavailable"
            else:
                factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
                vector, audit = embed_saved_query(factory, settings, query, gateway)
                audit = {"status": "cached" if audit["reused"] else "succeeded", **audit}
        except QueryEmbeddingUnavailable as exc:
            reason = exc.reason
            audit = {"status": "needs_attention", "receipt": exc.call,
                     "embedding_calls": None, "automatic_retry_allowed": False}
        except CallBlocked as exc:
            reason = "query_embedding_blocked"
            audit = {"status": "needs_attention", "call_id": exc.call_id,
                     "embedding_calls": 0, "automatic_retry_allowed": False}
        except Exception:
            # Fail closed if configuration, migration or audit persistence fails.
            # Never fall back to an untracked provider call.
            reason = "query_embedding_audit_unavailable"
            audit = {"status": "unavailable", "embedding_calls": None}
    result = hybrid_search(db, query, scope, query_vector=vector,
        embedding_provider=provider or "unavailable", embedding_model=model,
        lexical_limit=settings.retrieval_lexical_k, vector_limit=settings.retrieval_vector_k,
        final_limit=settings.retrieval_final_k)
    if reason:
        result["degraded_reason"] = reason
    return result | {"query_embedding": audit}
