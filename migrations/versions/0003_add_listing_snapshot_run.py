"""Associate new listing snapshots with their owning research run."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("listing_snapshots") as batch:
        batch.add_column(sa.Column("run_id", sa.String(length=255), nullable=True))
        batch.create_foreign_key(
            "fk_listing_snapshots_run_id_research_runs",
            "research_runs",
            ["run_id"],
            ["run_id"],
        )
        batch.create_index("ix_listing_snapshots_run_id", ["run_id"])


def downgrade() -> None:
    with op.batch_alter_table("listing_snapshots") as batch:
        batch.drop_index("ix_listing_snapshots_run_id")
        batch.drop_constraint("fk_listing_snapshots_run_id_research_runs", type_="foreignkey")
        batch.drop_column("run_id")
