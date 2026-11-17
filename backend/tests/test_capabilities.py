from unittest.mock import MagicMock

from app.capabilities import retrieval_capability
from app.config import Settings


def fixture_settings(**kwargs):
    return Settings(_env_file=None, data_mode="replay", retrieval_mode="hybrid",
                    embedding_provider="fixture", embedding_model="fixture-sha256-v1",
                    embedding_dim=8, **kwargs)


def test_status_reports_support_not_success(client):
    value = client.get("/api/v1/status").json()
    assert value["capabilities"]["vector_search"] is True
    assert value["retrieval"]["requested_mode"] == "lexical"
    assert value["retrieval"]["reason"] == "hybrid_not_requested"
    assert value["retrieval"]["local_prerequisites_ready"] is False
    assert value["retrieval"]["provider_health"] == "not_probed"


def test_sqlite_hybrid_is_not_available(client):
    result = retrieval_capability(fixture_settings(), client.app.state.engine)
    assert result["embedding_configured"] is True
    assert result["reason"] == "vector_database_unavailable"
    assert result["local_prerequisites_ready"] is False


def test_paid_queries_remain_opt_in():
    engine = MagicMock()
    engine.dialect.name = "postgresql"
    settings = Settings(_env_file=None, data_mode="live", retrieval_mode="hybrid",
                        embedding_provider="openai", embedding_model="example", embedding_dim=8,
                        embedding_api_key="do-not-disclose")
    result = retrieval_capability(settings, engine)
    assert result["reason"] == "query_embedding_not_authorized"
    assert result["query_dispatch_authorized"] is False
    assert result["pgvector_ready"] is None
    assert result["schema_ready"] is None
    assert result["matching_corpus_vectors"] is None
    assert "do-not-disclose" not in str(result)
    engine.connect.assert_not_called()


def test_missing_metadata_does_not_probe_database():
    engine = MagicMock()
    engine.dialect.name = "postgresql"
    result = retrieval_capability(Settings(_env_file=None, retrieval_mode="hybrid"), engine)
    assert result["reason"] == "embedding_provider_unavailable"
    engine.connect.assert_not_called()


def test_probe_failure_is_sanitized():
    engine = MagicMock()
    engine.dialect.name = "postgresql"
    engine.connect.side_effect = RuntimeError("secret connection string")
    result = retrieval_capability(fixture_settings(), engine)
    assert result["reason"] == "vector_readiness_check_failed"
    assert "secret" not in str(result)


def test_postgres_readiness_is_not_provider_or_scope_acceptance(monkeypatch):
    engine = MagicMock()
    engine.dialect.name = "postgresql"
    conn = engine.connect.return_value.__enter__.return_value
    inspector = MagicMock()
    inspector.get_table_names.return_value = ["evidence_chunks", "provider_calls", "provider_call_resources", "query_embeddings"]
    inspector.get_columns.return_value = [{"name": "embedding_vector"}]
    monkeypatch.setattr("app.capabilities.inspect", lambda _: inspector)
    conn.scalar.side_effect = [True, True]
    result = retrieval_capability(fixture_settings(), engine)
    assert result["local_prerequisites_ready"] is True
    assert result["provider_health"] == "not_probed"
    assert result["scope"] == "database_configuration_not_query_acceptance"
    assert result["reason"] is None


def test_missing_schema_does_not_query_vector_column(monkeypatch):
    engine = MagicMock()
    engine.dialect.name = "postgresql"
    conn = engine.connect.return_value.__enter__.return_value
    inspector = MagicMock()
    inspector.get_table_names.return_value = []
    monkeypatch.setattr("app.capabilities.inspect", lambda _: inspector)
    conn.scalar.return_value = True
    result = retrieval_capability(fixture_settings(), engine)
    assert result["reason"] == "query_embedding_schema_unavailable"
    assert conn.scalar.call_count == 1
