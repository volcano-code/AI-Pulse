"""Read-only capability reporting. Never probe a provider or spend user money."""
from sqlalchemy import inspect, text


def retrieval_capability(settings, engine):
    configured = bool(settings.embedding_provider and settings.embedding_model and settings.embedding_dim)
    result = {
        "supported": True,
        "requested_mode": settings.retrieval_mode,
        "embedding_configured": configured,
        "provider": settings.embedding_provider or None,
        "model": settings.embedding_model or None,
        "dimensions": settings.embedding_dim or None,
        "database": engine.dialect.name,
        "pgvector_ready": None,
        "schema_ready": None,
        "matching_corpus_vectors": None,
        "query_dispatch_authorized": settings.embedding_provider == "fixture" or (
            settings.data_mode == "live" and settings.embedding_query_allow_paid),
        "local_prerequisites_ready": False,
        "reason": None,
        "provider_health": "not_probed",
        "scope": "database_configuration_not_query_acceptance",
    }
    # A status read must neither invoke providers nor mutate audit/corpus rows.
    if settings.retrieval_mode != "hybrid":
        result["reason"] = "hybrid_not_requested"
        return result
    if engine.dialect.name != "postgresql":
        result["reason"] = "vector_database_unavailable"
        return result
    if not configured:
        result["reason"] = "embedding_provider_unavailable"
        return result
    if not result["query_dispatch_authorized"]:
        result["reason"] = "query_embedding_not_authorized"
        return result
    try:
        # Use a separate transaction so failed diagnostics cannot poison /status.
        with engine.connect() as conn:
            conn.execute(text("SET LOCAL statement_timeout = '1000ms'"))
            inspector = inspect(conn)
            required = {"evidence_chunks", "provider_calls", "provider_call_resources", "query_embeddings"}
            result["schema_ready"] = required.issubset(inspector.get_table_names())
            if result["schema_ready"]:
                result["schema_ready"] = "embedding_vector" in {
                    c["name"] for c in inspector.get_columns("evidence_chunks")}
            result["pgvector_ready"] = bool(conn.scalar(text(
                "SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname='vector')")))
            if not result["schema_ready"]:
                result["reason"] = "query_embedding_schema_unavailable"
            elif not result["pgvector_ready"]:
                result["reason"] = "pgvector_unavailable"
            else:
                result["matching_corpus_vectors"] = bool(conn.scalar(text("""
                    SELECT EXISTS(SELECT 1 FROM evidence_chunks
                    WHERE embedding_vector IS NOT NULL AND embedding_provider=:provider
                    AND embedding_model=:model AND embedding_dim=:dim)
                """), {"provider": settings.embedding_provider, "model": settings.embedding_model,
                       "dim": settings.embedding_dim}))
                if not result["matching_corpus_vectors"]:
                    result["reason"] = "corpus_embeddings_unavailable"
                else:
                    result["local_prerequisites_ready"] = True
    except Exception:
        # Do not leak credentials, SQL, schema details or network exception text.
        result["local_prerequisites_ready"] = False
        result["reason"] = "vector_readiness_check_failed"
    return result
