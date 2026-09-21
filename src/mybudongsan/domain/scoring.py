from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

DIMENSIONS: tuple[str, ...] = ("liquidity", "commute", "price", "residential")
DEFAULT_WEIGHTS: dict[str, int] = {
    "liquidity": 35,
    "commute": 30,
    "price": 25,
    "residential": 10,
}


class EvaluationInput(BaseModel):
    """Evidence-backed inputs used to assess one listing."""

    model_config = ConfigDict(frozen=True)

    required_passed: bool = True
    excluded_passed: bool = True
    liquidity: Decimal = Field(default=Decimal(0), ge=0, le=100)
    commute: Decimal = Field(default=Decimal(0), ge=0, le=100)
    price: Decimal = Field(default=Decimal(0), ge=0, le=100)
    residential: Decimal = Field(default=Decimal(0), ge=0, le=100)
    confidence: int = Field(default=0, ge=0, le=100)
    evidence_ids_by_dimension: dict[str, tuple[int, ...]] = Field(default_factory=dict)
    weights: dict[str, int] = Field(default_factory=lambda: dict(DEFAULT_WEIGHTS))

    dimensions: ClassVar[tuple[str, ...]] = DIMENSIONS

    @model_validator(mode="after")
    def validate_evidence_and_weights(self) -> EvaluationInput:
        if set(self.weights) != set(self.dimensions) or sum(self.weights.values()) != 100:
            raise ValueError("weights must contain all dimensions and total 100")
        for dimension in self.dimensions:
            score = getattr(self, dimension)
            if score > 0 and not self.evidence_ids_by_dimension.get(dimension):
                raise ValueError(f"evidence is required for positive {dimension} score")
        return self


class EvaluationResult(BaseModel):
    eligible: bool
    recommendable: bool
    total_score: float | None
    confidence: int
    reasons: list[str]


def evaluate_listing(evaluation_input: EvaluationInput) -> EvaluationResult:
    """Apply hard gates, then calculate the weighted listing score."""
    reasons: list[str] = []
    if not evaluation_input.required_passed:
        reasons.append("required_failed")
    if not evaluation_input.excluded_passed:
        reasons.append("excluded_matched")

    eligible = not reasons
    total_score: float | None = None
    if eligible:
        weighted_score = sum(
            getattr(evaluation_input, dimension) * Decimal(evaluation_input.weights[dimension])
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
        reasons=reasons,
    )
