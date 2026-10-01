from sqlalchemy import select
from app.embedding_backfill import backfill_embeddings
from app.embedding_gateway import EmbeddingGateway, FixtureEmbeddingProvider
from app.embeddings import ensure_snapshot_chunks
from app.models import EvidenceChunk, Snapshot


def test_backfill_is_idempotent_for_same_identity(seeded):
    factory = seeded.app.state.session_factory
    gateway = EmbeddingGateway(FixtureEmbeddingProvider(dimensions=8))
    with factory.begin() as db:
        for snapshot in db.scalars(select(Snapshot)):
            ensure_snapshot_chunks(db, snapshot)
        first = backfill_embeddings(db, gateway, batch_size=2, max_chunks=100)
        assert first["embedded"] > 0
    with factory.begin() as db:
        second = backfill_embeddings(db, gateway, batch_size=2, max_chunks=100)
        assert second["selected"] == 0
        assert second["embedded"] == 0


def test_backfill_reembeds_when_identity_changes(seeded):
    factory = seeded.app.state.session_factory
    with factory.begin() as db:
        snapshot = db.scalar(select(Snapshot).limit(1))
        ensure_snapshot_chunks(db, snapshot)
        one = backfill_embeddings(db, EmbeddingGateway(FixtureEmbeddingProvider(8)), max_chunks=100)
        assert one["embedded"] > 0
    with factory.begin() as db:
        two = backfill_embeddings(db, EmbeddingGateway(FixtureEmbeddingProvider(12)), max_chunks=100)
        assert two["embedded"] > 0
        assert all(x.embedding_dim == 12 for x in db.scalars(select(EvidenceChunk)))
