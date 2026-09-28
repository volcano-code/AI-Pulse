"""Evidence chunks and PostgreSQL pgvector capability.

Revision ID: 0005
Revises: 0004

SQLite keeps JSON embeddings for deterministic tests. PostgreSQL additionally
gets pgvector's native vector column. Downgrade deliberately leaves the vector
extension installed; production rollback should not drop shared extensions.
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "evidence_chunks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("snapshot_id", sa.String(36), sa.ForeignKey("snapshots.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("text_hash", sa.String(64), nullable=False),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.Column("embedding_json", sa.JSON(), nullable=True),
        sa.Column("embedding_model", sa.String(120), nullable=True),
        sa.Column("embedding_dim", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.UniqueConstraint("snapshot_id", "ordinal", name="uq_evidence_chunks_snapshot_ordinal"),
    )
    op.create_index("ix_evidence_chunks_snapshot_id", "evidence_chunks", ["snapshot_id"])
    op.create_index("ix_evidence_chunks_text_hash", "evidence_chunks", ["text_hash"])

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
        op.execute("ALTER TABLE evidence_chunks ADD COLUMN embedding_vector vector")


def downgrade():
    op.drop_table("evidence_chunks")
