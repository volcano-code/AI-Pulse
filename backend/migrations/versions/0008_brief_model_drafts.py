"""Validated brief drafts, separate from metadata-only provider audit.

Revision ID: 0008
Revises: 0007
"""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "brief_model_drafts",
        sa.Column("call_id", sa.String(36), sa.ForeignKey("provider_calls.id"), primary_key=True),
        sa.Column("snapshot_id", sa.String(36), sa.ForeignKey("snapshots.id"), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("evidence_quote", sa.Text(), nullable=False),
    )
    op.create_index("ix_brief_model_drafts_snapshot_id", "brief_model_drafts", ["snapshot_id"])


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM brief_model_drafts")):
        raise RuntimeError("Refusing to erase validated model drafts; preserve audit history")
    op.drop_index("ix_brief_model_drafts_snapshot_id", table_name="brief_model_drafts")
    op.drop_table("brief_model_drafts")
