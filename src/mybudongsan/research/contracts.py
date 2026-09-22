from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mybudongsan.domain.scoring import DIMENSIONS, EvaluationInput


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

    @field_validator("source_listing_id")
    @classmethod
    def normalize_source_listing_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("source_listing_id must not be blank")
        return normalized


class EvidenceObservation(BaseModel):
    """A local or captured source record ready for canonical persistence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_id: int = Field(gt=0)
    claim: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    source_type: str = Field(min_length=1)
    excerpt: str | None = None
    accessed_at: datetime


class DeepAssessmentObservation(BaseModel):
    """One deeply researched listing with its evidence-backed scoring input."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    listing: ListingObservation
    evidence: tuple[EvidenceObservation, ...] = Field(min_length=1)
    evaluation_input: EvaluationInput

    @model_validator(mode="after")
    def validate_evidence_links(self) -> DeepAssessmentObservation:
        evidence_ids = tuple(item.evidence_id for item in self.evidence)
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("deep assessment evidence IDs must be unique")
        referenced_ids = {
            evidence_id
            for dimension in DIMENSIONS
            for evidence_id in getattr(
                self.evaluation_input.evidence_ids_by_dimension, dimension
            )
        }
        if not referenced_ids.issubset(evidence_ids):
            raise ValueError("evaluation input references evidence outside the deep assessment")
        if set(self.listing.raw_evidence_ids) != set(evidence_ids):
            raise ValueError("listing raw evidence IDs must match deep assessment evidence")
        return self


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
    deep_assessments: tuple[DeepAssessmentObservation, ...] = Field(
        default_factory=tuple,
        max_length=3,
    )

    @model_validator(mode="after")
    def validate_unique_evidence_ids(self) -> ResearchBundle:
        evidence_ids = [
            evidence.evidence_id
            for assessment in self.deep_assessments
            for evidence in assessment.evidence
        ]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("research bundle evidence IDs must be unique")
        return self
