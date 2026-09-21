from datetime import UTC, datetime
from decimal import Decimal
from typing import get_type_hints

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped

from mybudongsan.domain.requests import MoneyRange, RegionCriterion, SearchRequest
from mybudongsan.domain.scoring import EvaluationInput, evaluate_listing
from mybudongsan.storage.models import (
    AssessmentModel,
    ListingModel,
    ListingSnapshotModel,
    ResearchRunModel,
)
from mybudongsan.storage.repositories import AssessmentRepository, RequestRepository, RunRepository


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


@pytest.mark.parametrize(
    ("model", "attribute"),
    [(ListingSnapshotModel, "asking_price"), (AssessmentModel, "score")],
)
def test_numeric_columns_are_annotated_as_decimals(model: type[object], attribute: str) -> None:
    annotations = get_type_hints(model, include_extras=True)
    assert annotations[attribute] == Mapped[Decimal | None]


def test_missing_request_run_rolls_back_and_database_remains_usable(database) -> None:  # type: ignore[no-untyped-def]
    run_repository = RunRepository(database)

    with pytest.raises(IntegrityError):
        run_repository.create("run-missing", "missing-request", 1)

    with database.session() as session:
        count = session.scalar(select(func.count()).select_from(ResearchRunModel))
    assert count == 0

    request = SearchRequest(
        request_id="req-for-run",
        version=1,
        regions=[RegionCriterion(name="서울 강서구")],
        budget=MoneyRange(
            minimum=Decimal("600000000"),  # noqa: FURB157
            maximum=Decimal("900000000"),  # noqa: FURB157
        ),
    )
    RequestRepository(database).save_version(request)
    run_repository.create("run-valid", request.request_id, request.version)

    with database.session() as session:
        run = session.scalar(
            select(ResearchRunModel).where(ResearchRunModel.run_id == "run-valid")
        )
    assert run is not None


def test_assessment_repository_round_trips_complete_evaluation_json(database) -> None:  # type: ignore[no-untyped-def]
    request = SearchRequest(
        request_id="req-assessment",
        version=1,
        regions=[RegionCriterion(name="서울 강서구")],
        budget=MoneyRange(minimum=Decimal(1), maximum=Decimal(2)),
    )
    RequestRepository(database).save_version(request)
    RunRepository(database).create("run-assessment", request.request_id, request.version)

    with database.session() as session:
        listing = ListingModel(
            source="fixture",
            source_listing_id="listing-1",
            created_at=datetime.now(UTC),
        )
        session.add(listing)
        session.flush()
        listing_id = listing.id

    evaluation_input = EvaluationInput(
        required_passed=True,
        excluded_passed=True,
        liquidity=80,
        commute=70,
        price=90,
        residential=60,
        confidence=89,
        evidence_ids_by_dimension={
            "liquidity": [101],
            "commute": [102],
            "price": [103],
            "residential": [104],
        },
    )
    evaluation_result = evaluate_listing(evaluation_input)
    AssessmentRepository(database).save(
        run_id="run-assessment",
        listing_id=listing_id,
        evaluation_input=evaluation_input,
        evaluation_result=evaluation_result,
    )

    loaded = AssessmentRepository(database).get("run-assessment", listing_id)
    assert loaded.evaluation_input == evaluation_input
    assert loaded.evaluation_result == evaluation_result
