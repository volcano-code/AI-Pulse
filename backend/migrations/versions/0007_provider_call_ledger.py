"""Durable dispatch intent and per-resource reservations.

Revision ID: 0007
Revises: 0006
"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("provider_calls",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("operation_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("provider", sa.String(60), nullable=False),
        sa.Column("model", sa.String(150), nullable=False),
        sa.Column("data_mode", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("input_count", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(200)),
        sa.Column("http_status", sa.Integer()),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("output_tokens", sa.Integer()),
        sa.Column("response_received", sa.Boolean(), nullable=False),
        sa.Column("elapsed_ms", sa.Integer()),
        sa.Column("error_code", sa.String(60)),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("finished_at", sa.String(40)))
    op.create_index("ix_provider_calls_status", "provider_calls", ["status"])
    op.create_table("provider_call_resources",
        sa.Column("resource_key", sa.String(64), primary_key=True),
        sa.Column("call_id", sa.String(36), sa.ForeignKey("provider_calls.id"), nullable=False))
    op.create_index("ix_provider_call_resources_call_id", "provider_call_resources", ["call_id"])


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM provider_calls")):
        raise RuntimeError("Refusing to erase provider-call audit history; archive and reconcile first")
    op.drop_index("ix_provider_call_resources_call_id", table_name="provider_call_resources")
    op.drop_table("provider_call_resources")
    op.drop_index("ix_provider_calls_status", table_name="provider_calls")
    op.drop_table("provider_calls")
