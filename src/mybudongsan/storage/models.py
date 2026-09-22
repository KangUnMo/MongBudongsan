from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class BuyerProfileModel(Base):
    __tablename__ = "buyer_profiles"

    profile_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SearchRequestModel(Base):
    __tablename__ = "search_requests"
    __table_args__ = (UniqueConstraint("request_id", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[str] = mapped_column(String(255))
    version: Mapped[int]
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ResearchRunModel(Base):
    __tablename__ = "research_runs"
    __table_args__ = (
        UniqueConstraint("run_id"),
        ForeignKeyConstraint(
            ("request_id", "request_version"),
            ("search_requests.request_id", "search_requests.version"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(String(255))
    request_id: Mapped[str] = mapped_column(String(255))
    request_version: Mapped[int]
    status: Mapped[str] = mapped_column(String(64), default="pending")
    current_stage: Mapped[str | None] = mapped_column(String(64))
    checkpoint_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ListingModel(Base):
    __tablename__ = "listings"
    __table_args__ = (UniqueConstraint("source", "source_listing_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(64))
    source_listing_id: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ListingSnapshotModel(Base):
    __tablename__ = "listing_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"))
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("research_runs.run_id"), index=True
    )
    asking_price: Mapped[Decimal | None] = mapped_column(Numeric)
    status: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EvidenceModel(Base):
    __tablename__ = "evidence"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("research_runs.run_id"))
    listing_id: Mapped[int | None] = mapped_column(ForeignKey("listings.id"))
    claim: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(64))
    excerpt: Mapped[str | None] = mapped_column(Text)
    accessed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AssessmentModel(Base):
    __tablename__ = "assessments"
    __table_args__ = (UniqueConstraint("run_id", "listing_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("research_runs.run_id"))
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"))
    passed_gates: Mapped[bool]
    score: Mapped[Decimal | None] = mapped_column(Numeric)
    risks: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    rationale: Mapped[str | None] = mapped_column(Text)
    input_payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    result_payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ReportModel(Base):
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("research_runs.run_id"))
    format: Mapped[str] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(Text)
    drive_file_id: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NotificationEventModel(Base):
    __tablename__ = "notification_events"
    __table_args__ = (UniqueConstraint("run_id", "event_type", "channel"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("research_runs.run_id"))
    event_type: Mapped[str] = mapped_column(String(64))
    channel: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    attempt_count: Mapped[int] = mapped_column(default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    provider_message_id: Mapped[str | None] = mapped_column(String(255))
    claim_token: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
