from sqlalchemy import select
from app.embedding_backfill import backfill_embeddings
from app.embedding_gateway import EmbeddingGateway, FixtureEmbeddingProvider
from app.embeddings import ensure_snapshot_chunks
from app.models import Snapshot


def test_backfill_receipt_counts_completed_batches(seeded):
    factory=seeded.app.state.session_factory
    with factory.begin() as db:
        for snapshot in db.scalars(select(Snapshot)):
            ensure_snapshot_chunks(db,snapshot)
        result=backfill_embeddings(
            db,EmbeddingGateway(FixtureEmbeddingProvider(8)),
            batch_size=2,max_chunks=100,data_mode="replay")
        assert result["embedded"] > 0
        assert result["batches_completed"] == (result["embedded"] + 1) // 2
        assert result["cost_usd"] is None
    with factory.begin() as db:
        second=backfill_embeddings(
            db,EmbeddingGateway(FixtureEmbeddingProvider(8)),
            batch_size=2,max_chunks=100,data_mode="replay")
        assert second["embedded"] == 0
        assert second["batches_completed"] == 0
