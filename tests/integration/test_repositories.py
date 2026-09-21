from decimal import Decimal

import pytest

from mybudongsan.domain.requests import MoneyRange, RegionCriterion, SearchRequest
from mybudongsan.storage.repositories import RequestRepository


def test_request_versions_are_immutable(database) -> None:  # type: ignore[no-untyped-def]
    repository = RequestRepository(database)
    request = SearchRequest(
        request_id="req-001",
        version=1,
        regions=[RegionCriterion(name="서울 강서구")],
        budget=MoneyRange(
            minimum=Decimal("600000000"),  # noqa: FURB157
            maximum=Decimal("900000000"),  # noqa: FURB157
        ),
    )
    repository.save_version(request)
    with pytest.raises(ValueError, match="already exists"):
        repository.save_version(request)


def test_request_round_trip_preserves_regions(database) -> None:  # type: ignore[no-untyped-def]
    repository = RequestRepository(database)
    request = SearchRequest(
        request_id="req-002",
        version=1,
        regions=[RegionCriterion(name="부천시", allow_expansion=True)],
        budget=MoneyRange(
            minimum=Decimal("500000000"),  # noqa: FURB157
            maximum=Decimal("700000000"),  # noqa: FURB157
        ),
    )
    repository.save_version(request)
    loaded = repository.get_version("req-002", 1)
    assert loaded == request
