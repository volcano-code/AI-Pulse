"""Stable event grouping and event-version history.

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa
revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("topic", sa.String(30), nullable=False),
        sa.Column("data_mode", sa.String(20), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("updated_at", sa.String(40), nullable=False),
        sa.Column("current_version_id", sa.String(36), nullable=True))
    op.create_index("ix_events_topic", "events", ["topic"])
    op.create_index("ix_events_data_mode", "events", ["data_mode"])
    op.create_table("event_articles",
        sa.Column("event_id", sa.String(36), sa.ForeignKey("events.id"), primary_key=True),
        sa.Column("article_id", sa.String(36), sa.ForeignKey("articles.id"), primary_key=True),
        sa.Column("match_score", sa.Float(), nullable=False),
        sa.UniqueConstraint("article_id", name="uq_event_articles_article"))
    op.create_table("event_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("event_id", sa.String(36), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("article_snapshots", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.UniqueConstraint("event_id", "fingerprint", name="uq_event_versions_fingerprint"))
    op.create_index("ix_event_versions_event_id", "event_versions", ["event_id"])

def downgrade():
    op.drop_table("event_versions")
    op.drop_table("event_articles")
    op.drop_table("events")
