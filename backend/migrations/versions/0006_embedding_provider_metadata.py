"""Embedding provider identity and revision metadata.

Revision ID: 0006
Revises: 0005
"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("evidence_chunks", sa.Column("embedding_provider", sa.String(60), nullable=True))
    op.add_column("evidence_chunks", sa.Column("embedding_revision", sa.String(120), nullable=True))
    op.add_column("evidence_chunks", sa.Column("embedded_at", sa.String(40), nullable=True))
    op.create_index(
        "ix_evidence_chunks_embedding_identity",
        "evidence_chunks",
        ["embedding_provider", "embedding_model", "embedding_dim"],
    )


def downgrade():
    op.drop_index("ix_evidence_chunks_embedding_identity", table_name="evidence_chunks")
    op.drop_column("evidence_chunks", "embedded_at")
    op.drop_column("evidence_chunks", "embedding_revision")
    op.drop_column("evidence_chunks", "embedding_provider")
