import pytest
from sqlalchemy import select
from app.config import Settings
from app.embedding_gateway import EmbeddingGateway, FixtureEmbeddingProvider
from app.embedding_backfill import backfill_embeddings
from app.embeddings import ensure_snapshot_chunks
from app.models import Snapshot, EvidenceChunk
from scripts.embedding_backfill import plan_backfill, run


def test_backfill_ops_dry_run_has_no_embedding_side_effects(seeded):
    factory=seeded.app.state.session_factory
    settings=seeded.app.state.settings.model_copy(update={
        "embedding_provider":"fixture","embedding_model":"fixture-sha256-v1","embedding_dim":8,
    })
    with factory.begin() as db:
        for snapshot in db.scalars(select(Snapshot)):
            ensure_snapshot_chunks(db,snapshot)
    with factory() as db:
        before=[(c.id,c.embedding_provider) for c in db.scalars(select(EvidenceChunk))]
        plan=plan_backfill(db,settings,max_chunks=3)
        assert plan["selected"]==3
    with factory() as db:
        after=[(c.id,c.embedding_provider) for c in db.scalars(select(EvidenceChunk))]
    assert before==after


def test_backfill_ops_requires_provider_confirmation(settings):
    s=settings.model_copy(update={
        "embedding_provider":"fixture","embedding_model":"fixture-sha256-v1","embedding_dim":8,
    })
    with pytest.raises(ValueError,match="confirmation"):
        run(s,apply=True,confirm_provider=None,allow_paid_api=False,max_chunks=1,batch_size=1)


def test_backfill_library_scope_filters_live_and_replay(seeded):
    factory=seeded.app.state.session_factory
    with factory.begin() as db:
        for snapshot in db.scalars(select(Snapshot)):
            ensure_snapshot_chunks(db,snapshot)
        result=backfill_embeddings(db,EmbeddingGateway(FixtureEmbeddingProvider(8)),
                                   max_chunks=100,data_mode="live")
        assert result["selected"]==0
