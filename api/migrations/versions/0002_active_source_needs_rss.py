"""An active source must have an RSS feed URL.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-21
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing rows that would violate the rule are deactivated rather than failing the migration.
    op.execute("UPDATE sources SET active = false WHERE active AND rss_url IS NULL")
    op.create_check_constraint("ck_sources_active_needs_rss", "sources", "NOT active OR rss_url IS NOT NULL")


def downgrade() -> None:
    op.drop_constraint("ck_sources_active_needs_rss", "sources", type_="check")
