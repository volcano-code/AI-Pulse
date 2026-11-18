"""Associate durable fetches with their owning job attempt.

Revision ID: 0010
Revises: 0009
"""
from alembic import op
import sqlalchemy as sa

revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade():
    # Nullable for legacy and non-durable fetches. Never guess legacy ownership.
    with op.batch_alter_table('fetch_runs') as batch:
        batch.add_column(sa.Column('job_run_id', sa.String(36), nullable=True))
        batch.add_column(sa.Column('job_lease_token', sa.String(36), nullable=True))
        batch.create_foreign_key('fk_fetch_runs_job_run_id', 'runs', ['job_run_id'], ['id'])
        batch.create_index('ix_fetch_runs_job_attempt', ['job_run_id', 'job_lease_token', 'status'])


def downgrade():
    # Only exercise downgrades on disposable test databases.
    with op.batch_alter_table('fetch_runs') as batch:
        batch.drop_index('ix_fetch_runs_job_attempt')
        batch.drop_constraint('fk_fetch_runs_job_run_id', type_='foreignkey')
        batch.drop_column('job_lease_token')
        batch.drop_column('job_run_id')
