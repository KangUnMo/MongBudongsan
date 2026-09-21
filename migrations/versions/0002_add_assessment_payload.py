"""Add explainable assessment evaluation payloads."""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "assessments",
        sa.Column(
            "input_payload",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.add_column(
        "assessments",
        sa.Column(
            "result_payload",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.get_bind().execute(
        sa.text(
            "UPDATE assessments "
            "SET input_payload = :input_payload, result_payload = :result_payload"
        ),
        {
            "input_payload": json.dumps(
                {
                    "required_passed": False,
                    "excluded_passed": False,
                    "active_listing_confirmed": False,
                    "minimum_evidence_met": False,
                    "liquidity": 0,
                    "commute": 0,
                    "price": 0,
                    "residential": 0,
                    "confidence": 0,
                    "evidence_ids_by_dimension": {
                        "liquidity": [],
                        "commute": [],
                        "price": [],
                        "residential": [],
                    },
                    "weights": {
                        "liquidity": 35,
                        "commute": 30,
                        "price": 25,
                        "residential": 10,
                    },
                }
            ),
            "result_payload": json.dumps(
                {
                    "eligible": False,
                    "recommendable": False,
                    "total_score": None,
                    "confidence": 0,
                    "reasons": ["legacy_assessment_unverified"],
                }
            ),
        },
    )


def downgrade() -> None:
    op.drop_column("assessments", "result_payload")
    op.drop_column("assessments", "input_payload")
