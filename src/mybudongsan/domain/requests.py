from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RequestStatus(StrEnum):
    DRAFT = "draft"
    NEEDS_CONFIRMATION = "needs_confirmation"
    APPROVED = "approved"
    RUNNING = "running"
    COMPLETED = "completed"
    RESUMABLE = "resumable"
    CANCELLED = "cancelled"


class MoneyRange(BaseModel):
    minimum: Decimal = Field(ge=0)
    maximum: Decimal = Field(gt=0)

    @model_validator(mode="after")
    def validate_order(self) -> MoneyRange:
        if self.minimum > self.maximum:
            raise ValueError("minimum must not exceed maximum")
        return self


class RegionCriterion(BaseModel):
    name: str = Field(min_length=1)
    allow_expansion: bool = False


class BuyerProfile(BaseModel):
    profile_id: str = Field(min_length=1)
    liquidity_weight: int = 35
    commute_weight: int = 30
    price_weight: int = 25
    residential_weight: int = 10

    @model_validator(mode="after")
    def validate_weight_total(self) -> BuyerProfile:
        total = (
            self.liquidity_weight
            + self.commute_weight
            + self.price_weight
            + self.residential_weight
        )
        if total != 100:
            raise ValueError("profile weights must total 100")
        return self


class SearchRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    regions: Annotated[list[RegionCriterion], Field(min_length=1, max_length=5)]
    budget: MoneyRange
    required: list[str] = Field(default_factory=list)
    preferred: list[str] = Field(default_factory=list)
    excluded: list[str] = Field(default_factory=list)
    special_questions: list[str] = Field(default_factory=list)
    status: RequestStatus = RequestStatus.DRAFT
