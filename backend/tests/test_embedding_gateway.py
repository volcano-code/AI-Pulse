import math
import pytest
from app.embedding_gateway import EmbeddingBatch, EmbeddingGateway, FixtureEmbeddingProvider


def test_fixture_embedding_gateway_is_deterministic_and_purpose_separated():
    gateway = EmbeddingGateway(FixtureEmbeddingProvider(dimensions=8))
    docs1 = gateway.embed_documents(["agent memory", "hybrid retrieval"])
    docs2 = gateway.embed_documents(["agent memory", "hybrid retrieval"])
    query = gateway.embed_query("agent memory")
    assert docs1 == docs2
    assert docs1.provider == "fixture"
    assert docs1.model == "fixture-sha256-v1"
    assert docs1.dimensions == 8
    assert len(docs1.vectors) == 2
    assert docs1.vectors[0] != query.vectors[0]
    assert math.isclose(sum(v * v for v in docs1.vectors[0]), 1.0, rel_tol=1e-6)


def test_embedding_gateway_rejects_invalid_provider_output():
    class BadProvider:
        def embed(self, texts, *, purpose):
            return EmbeddingBatch(vectors=[[1.0]], provider="bad", model="bad", dimensions=2)

    with pytest.raises(ValueError):
        EmbeddingGateway(BadProvider()).embed_query("x")


def test_fixture_provider_rejects_empty_and_oversized_batches():
    provider = FixtureEmbeddingProvider()
    with pytest.raises(ValueError):
        provider.embed([], purpose="query")
    with pytest.raises(ValueError):
        provider.embed(["x"] * 129, purpose="document")
