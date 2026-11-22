"""worker heartbeats

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-22 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0009'
down_revision: str | None = '0008'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'worker_heartbeats',
        sa.Column('worker_id', sa.String(length=100), nullable=False),
        sa.Column('provider', sa.String(length=20), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('worker_id'),
    )
    op.create_index(op.f('ix_worker_heartbeats_last_seen_at'), 'worker_heartbeats', ['last_seen_at'], unique=False)
    op.add_column('pipeline_runs', sa.Column('claimed_by', sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column('pipeline_runs', 'claimed_by')
    op.drop_index(op.f('ix_worker_heartbeats_last_seen_at'), table_name='worker_heartbeats')
    op.drop_table('worker_heartbeats')
