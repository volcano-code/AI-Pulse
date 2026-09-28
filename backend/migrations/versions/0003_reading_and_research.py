"""Read-version memory and persisted bounded research traces.

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa
revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("reading_states",
        sa.Column("article_id", sa.String(36), sa.ForeignKey("articles.id"), primary_key=True),
        sa.Column("snapshot_id", sa.String(36), sa.ForeignKey("snapshots.id"), nullable=False),
        sa.Column("read_at", sa.String(40), nullable=False))
    op.create_table("investigations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("request_key", sa.String(128), unique=True, nullable=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("brief_id", sa.String(36), sa.ForeignKey("briefs.id"), nullable=True),
        sa.Column("data_mode", sa.String(20), nullable=False),
        sa.Column("generation_mode", sa.String(20), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("trace", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("finished_at", sa.String(40), nullable=True))


def downgrade():
    op.drop_table("investigations")
    op.drop_table("reading_states")
