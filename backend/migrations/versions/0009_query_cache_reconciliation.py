"""Protected query vectors and immutable operator decisions.

Revision ID: 0009
Revises: 0008
"""
from alembic import op
import sqlalchemy as sa
revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("query_embeddings",
        sa.Column("call_id", sa.String(36), sa.ForeignKey("provider_calls.id"), primary_key=True),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("vector", sa.JSON(), nullable=False),
        sa.Column("vector_hash", sa.String(64), nullable=False))
    op.create_table("provider_reconciliations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("call_id", sa.String(36), sa.ForeignKey("provider_calls.id"), nullable=False, unique=True),
        sa.Column("decision_hash", sa.String(64), nullable=False),
        sa.Column("decision", sa.String(30), nullable=False),
        sa.Column("operator_billing", sa.String(20), nullable=False),
        sa.Column("evidence_sha256", sa.String(64), nullable=False),
        sa.Column("actor_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("replacement_call_id", sa.String(36), sa.ForeignKey("provider_calls.id"), nullable=True, unique=True))


def downgrade():
    for table in ("query_embeddings", "provider_reconciliations"):
        if op.get_bind().scalar(sa.text("SELECT count(*) FROM " + table)):
            raise RuntimeError("Refusing to erase query cache or reconciliation audit history")
    op.drop_table("provider_reconciliations")
    op.drop_table("query_embeddings")
