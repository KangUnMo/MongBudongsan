from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from mybudongsan.domain.listings import build_listing_key
from mybudongsan.research.contracts import ListingObservation, ResearchBundle


def make_observation(**overrides: object) -> ListingObservation:
    values: dict[str, object] = {
        "source": "naver_land",
        "source_listing_id": None,
        "canonical_url": "https://example.test/listing",
        "complex_name": "샘플아파트",
        "address": "서울시 구로구",
        "building": "101동",
        "floor": "2층",
        "area_m2": 84.9,
        "asking_price": 850_000_000,
        "status": "active",
        "observed_at": datetime(2026, 9, 21, tzinfo=UTC),
        "broker": "샘플공인",
        "description": "기본 설명",
    }
    values.update(overrides)
    return ListingObservation.model_validate(values)


def test_source_listing_id_is_primary_identity() -> None:
    observation = ListingObservation(
        source="naver_land",
        source_listing_id="12345",
        complex_name="샘플아파트",
        area_m2=84.9,
        asking_price=850_000_000,
    )

    assert build_listing_key(observation) == "naver_land:12345"


def test_source_listing_id_is_stripped_before_building_its_primary_key() -> None:
    observation = make_observation(source_listing_id="  12345  ")

    assert observation.source_listing_id == "12345"
    assert build_listing_key(observation) == "naver_land:12345"


def test_blank_source_listing_id_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_observation(source_listing_id="   ")


def test_fallback_identity_is_stable_without_source_id() -> None:
    first = make_observation(source_listing_id=None, description="첫 설명")
    second = make_observation(source_listing_id=None, description="다른 설명")

    assert build_listing_key(first) == build_listing_key(second)


def test_research_bundle_limits_each_research_stage() -> None:
    observation = make_observation()

    with pytest.raises(ValidationError):
        ResearchBundle(discovered=(observation,) * 26)
    with pytest.raises(ValidationError):
        ResearchBundle(verified=(observation,) * 8)
    with pytest.raises(ValidationError):
        ResearchBundle(deep_assessments=(observation,) * 4)
