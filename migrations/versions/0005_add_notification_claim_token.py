"""Add per-attempt tokens for notification delivery claims."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("notification_events") as batch:
        batch.add_column(
            sa.Column("claim_token", sa.String(length=128), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("notification_events") as batch:
        batch.drop_column("claim_token")
