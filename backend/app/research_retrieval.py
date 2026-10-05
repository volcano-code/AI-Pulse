"""Research retrieval policy: lexical by default, hybrid only when explicitly ready."""
from __future__ import annotations

from .embedding_gateway import EmbeddingGateway, FixtureEmbeddingProvider
from .hybrid_retrieval import hybrid_search
from .retrieval import answer_question, build_scope


def make_embedding_gateway(settings):
    """Return a configured gateway or None.

    Fixture is CI/local-only. Network providers are deliberately not inferred
    from LLM credentials and will be added as explicit adapters.
    """
    provider = getattr(settings, "embedding_provider", "")
    if provider == "fixture":
        return EmbeddingGateway(FixtureEmbeddingProvider(settings.embedding_dim))
    return None


def search_research_evidence(db, settings, query: str, brief_id: str | None, gateway=None) -> dict:
    requested = settings.retrieval_mode
    if requested == "lexical":
        result = answer_question(db, query, brief_id, settings.data_mode)
        return result | {
            "requested_mode": "lexical",
            "effective_mode": "lexical",
            "degraded": False,
            "degraded_reason": None,
        }

    scope = build_scope(db, brief_id, settings.data_mode)
    gateway = gateway or make_embedding_gateway(settings)
    if db.bind.dialect.name != "postgresql":
        query_vector = None
        provider = getattr(settings, "embedding_provider", "") or None
        model = settings.embedding_model or None
        reason = "vector_database_unavailable"
    elif gateway is None:
        query_vector = None
        provider = getattr(settings, "embedding_provider", "") or None
        model = settings.embedding_model or None
        reason = "embedding_provider_unavailable"
    else:
        batch = gateway.embed_query(query)
        if batch.model != settings.embedding_model or batch.dimensions != settings.embedding_dim:
            raise ValueError("Query embedding identity does not match configured corpus identity")
        query_vector = batch.vectors[0]
        provider, model, reason = batch.provider, batch.model, None

    result = hybrid_search(
        db, query, scope,
        query_vector=query_vector,
        embedding_provider=provider or "unavailable",
        embedding_model=model,
        lexical_limit=settings.retrieval_lexical_k,
        vector_limit=settings.retrieval_vector_k,
        final_limit=settings.retrieval_final_k,
    )
    if reason and result["degraded"]:
        result["degraded_reason"] = reason
    return result
