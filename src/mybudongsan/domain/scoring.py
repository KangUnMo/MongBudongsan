from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

DIMENSIONS: tuple[str, ...] = ("liquidity", "commute", "price", "residential")
DEFAULT_WEIGHTS: dict[str, int] = {
    "liquidity": 35,
    "commute": 30,
    "price": 25,
    "residential": 10,
}


class DimensionEvidence(BaseModel):
    """Immutable evidence identifiers grouped by scoring dimension."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    liquidity: tuple[int, ...] = ()
    commute: tuple[int, ...] = ()
    price: tuple[int, ...] = ()
    residential: tuple[int, ...] = ()


class EvaluationWeights(BaseModel):
    """Immutable scoring weights with a fixed, complete dimension set."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    liquidity: int = Field(default=35, ge=0, le=100)
    commute: int = Field(default=30, ge=0, le=100)
    price: int = Field(default=25, ge=0, le=100)
    residential: int = Field(default=10, ge=0, le=100)

    @model_validator(mode="after")
    def validate_total(self) -> EvaluationWeights:
        if sum(getattr(self, dimension) for dimension in DIMENSIONS) != 100:
            raise ValueError("weights must total 100")
        return self


class EvaluationInput(BaseModel):
    """Evidence-backed inputs used to assess one listing."""

    model_config = ConfigDict(frozen=True)

    required_passed: bool = True
    excluded_passed: bool = True
    active_listing_confirmed: bool
    minimum_evidence_met: bool
    liquidity: Decimal = Field(default=Decimal(0), ge=0, le=100)
    commute: Decimal = Field(default=Decimal(0), ge=0, le=100)
    price: Decimal = Field(default=Decimal(0), ge=0, le=100)
    residential: Decimal = Field(default=Decimal(0), ge=0, le=100)
    confidence: int = Field(default=0, ge=0, le=100)
    evidence_ids_by_dimension: DimensionEvidence = Field(default_factory=DimensionEvidence)
    weights: EvaluationWeights = Field(default_factory=EvaluationWeights)

    @model_validator(mode="after")
    def validate_evidence(self) -> EvaluationInput:
        evidence_ids = tuple(
            evidence_id
            for dimension in DIMENSIONS
            for evidence_id in getattr(self.evidence_ids_by_dimension, dimension)
        )
        if self.minimum_evidence_met and not evidence_ids:
            raise ValueError("minimum_evidence_met requires at least one evidence ID")
        for dimension in DIMENSIONS:
            score = getattr(self, dimension)
            if score > 0 and not getattr(self.evidence_ids_by_dimension, dimension):
                raise ValueError(f"evidence is required for positive {dimension} score")
        return self


class EvaluationResult(BaseModel):
    """Immutable canonical output of an assessment evaluation."""

    model_config = ConfigDict(frozen=True)

    eligible: bool
    recommendable: bool
    total_score: float | None
    confidence: int
    reasons: tuple[str, ...]


def evaluate_listing(evaluation_input: EvaluationInput) -> EvaluationResult:
    """Apply mandatory gates, then calculate the weighted listing score."""
    reasons: list[str] = []
    if not evaluation_input.required_passed:
        reasons.append("required_failed")
    if not evaluation_input.excluded_passed:
        reasons.append("excluded_matched")
    if not evaluation_input.active_listing_confirmed:
        reasons.append("active_listing_unconfirmed")
    if not evaluation_input.minimum_evidence_met:
        reasons.append("minimum_evidence_not_met")

    eligible = not reasons
    total_score: float | None = None
    if eligible:
        weighted_score = sum(
            getattr(evaluation_input, dimension)
            * Decimal(getattr(evaluation_input.weights, dimension))
            for dimension in DIMENSIONS
        ) / Decimal(100)
        rounded_score = weighted_score.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        total_score = float(rounded_score)

    if evaluation_input.confidence < 85:
        reasons.append("confidence_below_85")

    return EvaluationResult(
        eligible=eligible,
        recommendable=eligible and evaluation_input.confidence >= 85,
        total_score=total_score,
        confidence=evaluation_input.confidence,
        reasons=tuple(reasons),
    )
