from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ListingObservation(BaseModel):
    """A single structured listing record captured from a browser source."""

    model_config = ConfigDict(frozen=True)

    source: str = Field(min_length=1)
    source_listing_id: str | None = None
    canonical_url: str | None = None
    complex_name: str | None = None
    address: str | None = None
    building: str | None = None
    floor: str | None = None
    area_m2: float | None = Field(default=None, ge=0)
    asking_price: Decimal | None = Field(default=None, ge=0)
    status: str | None = None
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    broker: str | None = None
    description: str | None = None
    raw_evidence_ids: tuple[int, ...] = Field(default_factory=tuple)


class ResearchBundle(BaseModel):
    """Bounded browser output passed between research stages."""

    model_config = ConfigDict(frozen=True)

    discovered: tuple[ListingObservation, ...] = Field(
        default_factory=tuple,
        max_length=25,
    )
    verified: tuple[ListingObservation, ...] = Field(
        default_factory=tuple,
        max_length=7,
    )
    deep_assessments: tuple[ListingObservation, ...] = Field(
        default_factory=tuple,
        max_length=3,
    )
