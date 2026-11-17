"""Freeze scheduled outbox submission deadline; legacy/manual entries stay unbounded.

Revision ID: 0011
Revises: 0010
"""
from alembic import op
import sqlalchemy as sa
revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('deliveries', sa.Column('expires_at', sa.String(40), nullable=True))


def downgrade():
    with op.batch_alter_table('deliveries') as batch:
        batch.drop_column('expires_at')
