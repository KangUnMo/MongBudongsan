from decimal import Decimal

import pytest
from pydantic import ValidationError

from mybudongsan.domain.requests import (
    BuyerProfile,
    MoneyRange,
    RegionCriterion,
    RequestStatus,
    SearchRequest,
)


def test_search_request_accepts_one_to_five_regions() -> None:
    request = SearchRequest(
        request_id="req-001",
        version=1,
        regions=[RegionCriterion(name="서울 강서구")],
        budget=MoneyRange(
            minimum=Decimal("600000000"),  # noqa: FURB157
            maximum=Decimal("900000000"),  # noqa: FURB157
        ),
        required=["아파트"],
        preferred=["역 도보 15분 이내"],
        excluded=["반지하"],
    )
    assert request.status is RequestStatus.DRAFT


def test_search_request_rejects_more_than_five_regions() -> None:
    with pytest.raises(ValidationError):
        SearchRequest(
            request_id="req-002",
            version=1,
            regions=[RegionCriterion(name=f"지역 {index}") for index in range(6)],
            budget=MoneyRange(
                minimum=Decimal("1"),  # noqa: FURB157
                maximum=Decimal("2"),  # noqa: FURB157
            ),
        )


def test_money_range_rejects_reversed_values() -> None:
    with pytest.raises(ValidationError):
        MoneyRange(
            minimum=Decimal("900"),  # noqa: FURB157
            maximum=Decimal("600"),  # noqa: FURB157
        )


def test_profile_weights_must_total_one_hundred() -> None:
    with pytest.raises(ValidationError):
        BuyerProfile(
            profile_id="profile-001",
            liquidity_weight=35,
            commute_weight=30,
            price_weight=25,
            residential_weight=9,
        )


def test_approved_search_request_rejects_direct_version_mutation() -> None:
    request = SearchRequest(
        request_id="req-approved",
        version=1,
        regions=[RegionCriterion(name="서울 강서구")],
        budget=MoneyRange(
            minimum=Decimal("600000000"),  # noqa: FURB157
            maximum=Decimal("900000000"),  # noqa: FURB157
        ),
        status=RequestStatus.APPROVED,
    )

    with pytest.raises(ValidationError):
        request.version = 2


def test_approved_search_request_rejects_collection_mutation() -> None:
    request = SearchRequest(
        request_id="req-approved-collections",
        version=1,
        regions=[RegionCriterion(name="서울 강서구")],
        budget=MoneyRange(
            minimum=Decimal("600000000"),  # noqa: FURB157
            maximum=Decimal("900000000"),  # noqa: FURB157
        ),
        required=["아파트"],
        preferred=["역세권"],
        excluded=["반지하"],
        special_questions=["재건축 여부"],
        status=RequestStatus.APPROVED,
    )

    with pytest.raises(AttributeError):
        request.required.append("신축")
    with pytest.raises(AttributeError):
        request.preferred.append("남향")
    with pytest.raises(AttributeError):
        request.excluded.append("고층")
    with pytest.raises(AttributeError):
        request.special_questions.append("학군")
    with pytest.raises(AttributeError):
        request.regions.append(RegionCriterion(name="서울 양천구"))


def test_approved_search_request_rejects_nested_mutation() -> None:
    request = SearchRequest(
        request_id="req-approved-nested",
        version=1,
        regions=[RegionCriterion(name="서울 강서구")],
        budget=MoneyRange(
            minimum=Decimal("600000000"),  # noqa: FURB157
            maximum=Decimal("900000000"),  # noqa: FURB157
        ),
        status=RequestStatus.APPROVED,
    )

    with pytest.raises(ValidationError):
        request.regions[0].name = "서울 양천구"
    with pytest.raises(ValidationError):
        request.budget.maximum = Decimal("1000000000")  # noqa: FURB157
