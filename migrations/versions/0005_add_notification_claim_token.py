"""Add per-attempt tokens for notification delivery claims."""

from __future__ import annotations

from secrets import token_urlsafe

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
    notification_events = sa.table(
        "notification_events",
        sa.column("id", sa.Integer()),
        sa.column("status", sa.String(length=32)),
        sa.column("claim_token", sa.String(length=128)),
    )
    connection = op.get_bind()
    dispatching_ids = tuple(
        connection.execute(
            sa.select(notification_events.c.id).where(
                notification_events.c.status == "dispatching"
            )
        ).scalars()
    )
    issued_tokens: set[str] = set()
    for event_id in dispatching_ids:
        claim_token = token_urlsafe(32)
        while claim_token in issued_tokens:
            claim_token = token_urlsafe(32)
        issued_tokens.add(claim_token)
        connection.execute(
            sa.update(notification_events)
            .where(notification_events.c.id == event_id)
            .values(claim_token=claim_token)
        )


def downgrade() -> None:
    with op.batch_alter_table("notification_events") as batch:
        batch.drop_column("claim_token")
