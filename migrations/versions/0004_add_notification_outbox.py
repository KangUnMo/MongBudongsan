"""Turn legacy notification receipts into a durable delivery outbox."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("notification_events") as batch:
        batch.add_column(
            sa.Column(
                "status",
                sa.String(length=32),
                nullable=False,
                server_default="pending",
            )
        )
        batch.add_column(
            sa.Column(
                "attempt_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch.add_column(sa.Column("last_error", sa.Text(), nullable=True))
        batch.add_column(
            sa.Column("provider_message_id", sa.String(length=255), nullable=True)
        )
        batch.add_column(
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.current_timestamp(),
            )
        )
        batch.alter_column(
            "sent_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=True,
        )
    op.execute(
        sa.text(
            "UPDATE notification_events "
            "SET status = 'sent', attempt_count = 1, created_at = sent_at, "
            "provider_message_id = 'provider_acknowledged'"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE notification_events "
            "SET sent_at = COALESCE(sent_at, created_at) "
            "WHERE sent_at IS NULL"
        )
    )
    with op.batch_alter_table("notification_events") as batch:
        batch.alter_column(
            "sent_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
        )
        batch.drop_column("created_at")
        batch.drop_column("provider_message_id")
        batch.drop_column("last_error")
        batch.drop_column("attempt_count")
        batch.drop_column("status")
