"""Durable job state, daily schedule, transactional email outbox.

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa
revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('runs') as b:
        b.add_column(sa.Column('engine', sa.String(20), nullable=False, server_default='local'))
        b.add_column(sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'))
        b.add_column(sa.Column('max_attempts', sa.Integer(), nullable=False, server_default='3'))
        for name, size in [('available_at', 40), ('lease_token', 36), ('lease_until', 40), ('request_key', 180)]:
            b.add_column(sa.Column(name, sa.String(size), nullable=True))
        b.add_column(sa.Column('request', sa.JSON(), nullable=False, server_default='{}'))
        b.add_column(sa.Column('checkpoint', sa.JSON(), nullable=False, server_default='{}'))
        b.create_unique_constraint('uq_runs_request_key', ['request_key'])
    op.create_table('daily_schedule',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('local_time', sa.String(5), nullable=False),
        sa.Column('timezone', sa.String(70), nullable=False),
        sa.Column('send', sa.Boolean(), nullable=False),
        sa.Column('updated_at', sa.String(40), nullable=False))
    op.create_table('deliveries',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('brief_id', sa.String(36), sa.ForeignKey('briefs.id'), nullable=False),
        sa.Column('dedupe_key', sa.String(64), nullable=False, unique=True),
        sa.Column('transport', sa.String(20), nullable=False),
        sa.Column('recipient', sa.String(320), nullable=False),
        sa.Column('sender', sa.String(320), nullable=False),
        sa.Column('subject', sa.String(250), nullable=False),
        sa.Column('body_text', sa.Text(), nullable=False),
        sa.Column('message_id', sa.String(200), nullable=False),
        sa.Column('status', sa.String(30), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.String(40), nullable=False),
        sa.Column('available_at', sa.String(40), nullable=False),
        sa.Column('finished_at', sa.String(40), nullable=True),
        sa.Column('lease_token', sa.String(36), nullable=True),
        sa.Column('lease_until', sa.String(40), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('artifact_name', sa.String(100), nullable=True))
    op.create_index('ix_deliveries_brief_id', 'deliveries', ['brief_id'])
    op.create_index('ix_deliveries_status', 'deliveries', ['status'])
    op.create_table('worker_heartbeats',
        sa.Column('id', sa.String(80), primary_key=True),
        sa.Column('last_seen_at', sa.String(40), nullable=False),
        sa.Column('run_id', sa.String(36), nullable=True))


def downgrade():
    op.drop_table('worker_heartbeats')
    op.drop_table('deliveries')
    op.drop_table('daily_schedule')
    with op.batch_alter_table('runs') as b:
        b.drop_constraint('uq_runs_request_key', type_='unique')
        for name in ['checkpoint', 'request', 'request_key', 'lease_until', 'lease_token', 'available_at', 'max_attempts', 'attempts', 'engine']:
            b.drop_column(name)
