import pytest
from sqlalchemy import select
from app.embedding_backfill import backfill_embeddings
from app.embeddings import ensure_snapshot_chunks
from app.models import Snapshot, EvidenceChunk
from app.embedding_gateway import EmbeddingBatch


class PaidSpy:
    provider="openai"
    model="paid-fixture-model"
    dimensions=3
    calls=0
    def embed(self, texts, *, purpose):
        self.calls += 1
        return EmbeddingBatch(vectors=[[1.0,0.0,0.0] for _ in texts],
                              provider=self.provider,model=self.model,dimensions=3,
                              input_tokens=4)


def test_paid_backfill_rejects_unspecified_scope_before_provider_call(seeded):
    from app.embedding_gateway import EmbeddingGateway
    factory=seeded.app.state.session_factory
    spy=PaidSpy()
    with factory.begin() as db:
        for snap in db.scalars(select(Snapshot)):
            ensure_snapshot_chunks(db,snap)
        with pytest.raises(ValueError,match="live-only scope"):
            backfill_embeddings(db,EmbeddingGateway(spy),max_chunks=10)
    assert spy.calls == 0


def test_paid_backfill_cannot_send_replay_documents(seeded):
    from app.embedding_gateway import EmbeddingGateway
    factory=seeded.app.state.session_factory
    spy=PaidSpy()
    with factory.begin() as db:
        for snap in db.scalars(select(Snapshot)):
            ensure_snapshot_chunks(db,snap)
        result=backfill_embeddings(db,EmbeddingGateway(spy),max_chunks=10,data_mode="live")
        assert result["embedded"]==0
        assert result["input_tokens"]==0
        assert result["cost_usd"] is None
    assert spy.calls==0
