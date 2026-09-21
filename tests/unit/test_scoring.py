from decimal import Decimal

import pytest
from pydantic import ValidationError

from mybudongsan.domain.scoring import EvaluationInput, evaluate_listing


def make_input(**overrides: object) -> EvaluationInput:
    values: dict[str, object] = {
        "required_passed": True,
        "excluded_passed": True,
        "liquidity": 80,
        "commute": 70,
        "price": 90,
        "residential": 60,
        "confidence": 90,
        "evidence_ids_by_dimension": {
            "liquidity": [101],
            "commute": [102],
            "price": [103],
            "residential": [104],
        },
    }
    values.update(overrides)
    return EvaluationInput.model_validate(values)


def test_required_failure_blocks_recommendation() -> None:
    result = evaluate_listing(make_input(required_passed=False, confidence=100))

    assert result.eligible is False
    assert result.recommendable is False
    assert result.total_score is None
    assert "required_failed" in result.reasons


def test_excluded_match_blocks_recommendation() -> None:
    result = evaluate_listing(make_input(excluded_passed=False, confidence=100))

    assert result.eligible is False
    assert result.total_score is None
    assert "excluded_matched" in result.reasons


def test_default_weighted_score() -> None:
    result = evaluate_listing(
        make_input(
            liquidity=80,
            commute=70,
            price=90,
            residential=60,
            confidence=90,
        )
    )

    assert result.total_score == 77.5
    assert result.confidence == 90
    assert result.recommendable is True


def test_score_is_rounded_to_one_decimal_place() -> None:
    result = evaluate_listing(
        make_input(liquidity=80, commute=71, price=90, residential=61)
    )

    assert result.total_score == 77.9


def test_confidence_below_eighty_five_is_not_recommendable() -> None:
    result = evaluate_listing(make_input(required_passed=True, confidence=84))

    assert result.eligible is True
    assert result.recommendable is False
    assert result.total_score == 77.5
    assert "confidence_below_85" in result.reasons


@pytest.mark.parametrize(
    "field",
    ["liquidity", "commute", "price", "residential"],
)
def test_dimension_must_be_between_zero_and_one_hundred(field: str) -> None:
    with pytest.raises(ValidationError):
        make_input(**{field: Decimal("100.1")})


def test_confidence_must_be_between_zero_and_one_hundred() -> None:
    with pytest.raises(ValidationError):
        make_input(confidence=101)


def test_positive_dimension_requires_evidence() -> None:
    with pytest.raises(ValidationError, match="evidence"):
        make_input(
            liquidity=10,
            evidence_ids_by_dimension={
                "liquidity": [],
                "commute": [102],
                "price": [103],
                "residential": [104],
            },
        )


def test_zero_dimension_does_not_require_evidence() -> None:
    result = evaluate_listing(
        make_input(
            liquidity=0,
            evidence_ids_by_dimension={
                "liquidity": [],
                "commute": [102],
                "price": [103],
                "residential": [104],
            },
        )
    )

    assert result.total_score == 49.5


def test_custom_weights_must_total_one_hundred() -> None:
    with pytest.raises(ValidationError, match="weights"):
        make_input(weights={"liquidity": 50, "commute": 30, "price": 25, "residential": 10})
