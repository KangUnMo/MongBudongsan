from datetime import UTC, datetime
from decimal import Decimal
from typing import get_type_hints

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped

from mybudongsan.domain.requests import MoneyRange, RegionCriterion, SearchRequest
from mybudongsan.domain.scoring import EvaluationInput, EvaluationResult, evaluate_listing
from mybudongsan.research.contracts import EvidenceObservation, ListingObservation
from mybudongsan.storage.models import (
    AssessmentModel,
    EvidenceModel,
    ListingModel,
    ListingSnapshotModel,
    ResearchRunModel,
)
from mybudongsan.storage.repositories import (
    AssessmentRepository,
    EvidenceRepository,
    ListingRepository,
    RequestRepository,
    RunRepository,
)


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
        evidence_ids = _add_evidence(session, "run-assessment", listing_id, count=4)

    evaluation_input = EvaluationInput(
        required_passed=True,
        excluded_passed=True,
        active_listing_confirmed=True,
        minimum_evidence_met=True,
        liquidity=80,
        commute=70,
        price=90,
        residential=60,
        confidence=89,
        evidence_ids_by_dimension={
            "liquidity": [evidence_ids[0]],
            "commute": [evidence_ids[1]],
            "price": [evidence_ids[2]],
            "residential": [evidence_ids[3]],
        },
    )
    AssessmentRepository(database).save(
        run_id="run-assessment",
        listing_id=listing_id,
        evaluation_input=evaluation_input,
    )

    loaded = AssessmentRepository(database).get("run-assessment", listing_id)
    assert loaded.evaluation_input == evaluation_input
    assert loaded.evaluation_result == evaluate_listing(evaluation_input)


def test_report_projection_reads_latest_listing_and_only_run_owned_evidence(database) -> None:  # type: ignore[no-untyped-def]
    request = SearchRequest(
        request_id="req-projection",
        version=1,
        regions=[RegionCriterion(name="서울 강서구")],
        budget=MoneyRange(minimum=Decimal(1), maximum=Decimal(2)),
    )
    RequestRepository(database).save_version(request)
    RunRepository(database).create("run-projection", request.request_id, request.version)
    RunRepository(database).create("run-other", request.request_id, request.version)
    evidence_repository = EvidenceRepository(database)
    persisted = evidence_repository.save_for_run(
        "run-projection",
        (
            EvidenceObservation(
                evidence_id=1,
                claim="가격 근거",
                source_url="fixture://projection/price",
                source_type="fixture",
                accessed_at=datetime(2026, 9, 21, tzinfo=UTC),
            ),
            EvidenceObservation(
                evidence_id=2,
                claim="같은 실행의 미참조 근거",
                source_url="fixture://projection/unreferenced",
                source_type="fixture",
                accessed_at=datetime(2026, 9, 21, tzinfo=UTC),
            ),
        ),
    )
    evidence_repository.save_for_run(
        "run-other",
        (
            EvidenceObservation(
                evidence_id=1,
                claim="다른 실행 근거",
                source_url="fixture://other/price",
                source_type="fixture",
                accessed_at=datetime(2026, 9, 21, tzinfo=UTC),
            ),
        ),
    )
    evidence_id = persisted[0].evidence_id
    listing_repository = ListingRepository(database)
    first = ListingObservation(
        source="fixture",
        source_listing_id="projection-1",
        asking_price=700_000_000,
        observed_at=datetime(2020, 9, 21, 10, tzinfo=UTC),
        raw_evidence_ids=(evidence_id,),
    )
    latest = first.model_copy(
        update={
            "asking_price": Decimal(680_000_000),
            "observed_at": datetime(2020, 9, 21, 11, tzinfo=UTC),
        }
    )
    listing_id = listing_repository.ingest_bundle("run-projection", (first,))[0].listing_id
    listing_repository.ingest_bundle("run-projection", (latest,))
    evaluation_input = EvaluationInput(
        active_listing_confirmed=True,
        minimum_evidence_met=True,
        price=80,
        confidence=90,
        evidence_ids_by_dimension={"price": [evidence_id]},
    )
    AssessmentRepository(database).save(
        run_id="run-projection",
        listing_id=listing_id,
        evaluation_input=evaluation_input,
    )
    later_run_snapshot = latest.model_copy(
        update={
            "asking_price": Decimal(650_000_000),
            "observed_at": datetime(2020, 9, 21, 12, tzinfo=UTC),
            "raw_evidence_ids": (),
        }
    )
    listing_repository.ingest_bundle("run-other", (later_run_snapshot,))

    projected = AssessmentRepository(database).list_for_run("run-projection")
    evidence = evidence_repository.list_for_run(
        "run-projection",
        evidence_ids=(evidence_id,),
    )

    assert len(projected) == 1
    assert projected[0].listing.asking_price == Decimal(680_000_000)
    assert projected[0].assessment.evaluation_result == evaluate_listing(evaluation_input)
    assert [item.claim for item in evidence] == ["가격 근거"]


def test_assessment_repository_recomputes_canonical_result_and_rejects_forged_result(
    database,
) -> None:  # type: ignore[no-untyped-def]
    run_id, listing_id, evidence_ids = _seed_assessment_context(database, "canonical")
    evaluation_input = _evaluation_input(evidence_ids[:4])
    forged_result = EvaluationResult(
        eligible=True,
        recommendable=True,
        total_score=100.0,
        confidence=100,
        reasons=[],
    )

    with pytest.raises(TypeError):
        AssessmentRepository(database).save(
            run_id=run_id,
            listing_id=listing_id,
            evaluation_input=evaluation_input,
            evaluation_result=forged_result,
        )

    saved = AssessmentRepository(database).save(
        run_id=run_id,
        listing_id=listing_id,
        evaluation_input=evaluation_input,
    )
    assert saved.evaluation_result == evaluate_listing(evaluation_input)


@pytest.mark.parametrize(
    ("evidence_kind", "message"),
    [
        ("missing", "evidence not found"),
        ("wrong_run", "does not belong to run"),
        ("other_listing", "linked to a different listing"),
    ],
)
def test_assessment_repository_rejects_evidence_outside_assessment_scope(
    database, evidence_kind: str, message: str
) -> None:  # type: ignore[no-untyped-def]
    run_id, listing_id, evidence_ids = _seed_assessment_context(database, evidence_kind)
    invalid_id = {
        "missing": 999999,
        "wrong_run": evidence_ids[4],
        "other_listing": evidence_ids[5],
    }[evidence_kind]

    with pytest.raises(ValueError, match=message):
        AssessmentRepository(database).save(
            run_id=run_id,
            listing_id=listing_id,
            evaluation_input=_evaluation_input((invalid_id,) * 4),
        )

    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(AssessmentModel)) == 0


def test_assessment_repository_rejects_true_minimum_evidence_gate_without_evidence(
    database,
) -> None:  # type: ignore[no-untyped-def]
    request = SearchRequest(
        request_id="req-assessment-empty-evidence",
        version=1,
        regions=[RegionCriterion(name="서울 강서구")],
        budget=MoneyRange(minimum=Decimal(1), maximum=Decimal(2)),
    )
    RequestRepository(database).save_version(request)
    run_id = "run-assessment-empty-evidence"
    RunRepository(database).create(run_id, request.request_id, request.version)
    with database.session() as session:
        listing = ListingModel(
            source="fixture",
            source_listing_id="listing-empty-evidence",
            created_at=datetime.now(UTC),
        )
        session.add(listing)
        session.flush()
        listing_id = listing.id

    valid_input = EvaluationInput(
        required_passed=True,
        excluded_passed=True,
        active_listing_confirmed=True,
        minimum_evidence_met=False,
        confidence=100,
    )
    invalid_input = valid_input.model_copy(update={"minimum_evidence_met": True})

    with pytest.raises(ValueError, match="minimum_evidence_met"):
        AssessmentRepository(database).save(
            run_id=run_id,
            listing_id=listing_id,
            evaluation_input=invalid_input,
        )

    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(AssessmentModel)) == 0


def _seed_assessment_context(database, suffix: str) -> tuple[str, int, tuple[int, ...]]:  # type: ignore[no-untyped-def]
    request = SearchRequest(
        request_id=f"req-assessment-{suffix}",
        version=1,
        regions=[RegionCriterion(name="서울 강서구")],
        budget=MoneyRange(minimum=Decimal(1), maximum=Decimal(2)),
    )
    RequestRepository(database).save_version(request)
    run_id = f"run-assessment-{suffix}"
    RunRepository(database).create(run_id, request.request_id, request.version)
    other_run_id = f"other-run-{suffix}"
    RunRepository(database).create(other_run_id, request.request_id, request.version)
    with database.session() as session:
        listing = ListingModel(
            source="fixture",
            source_listing_id=f"listing-{suffix}",
            created_at=datetime.now(UTC),
        )
        other_listing = ListingModel(
            source="fixture",
            source_listing_id=f"other-listing-{suffix}",
            created_at=datetime.now(UTC),
        )
        session.add_all((listing, other_listing))
        session.flush()
        valid_ids = _add_evidence(session, run_id, listing.id, count=4)
        wrong_run_ids = _add_evidence(session, other_run_id, None, count=1)
        other_listing_ids = _add_evidence(session, run_id, other_listing.id, count=1)
    return run_id, listing.id, (*valid_ids, *wrong_run_ids, *other_listing_ids)


def _evaluation_input(evidence_ids: tuple[int, ...]) -> EvaluationInput:
    return EvaluationInput(
        required_passed=True,
        excluded_passed=True,
        active_listing_confirmed=True,
        minimum_evidence_met=True,
        liquidity=80,
        commute=70,
        price=90,
        residential=60,
        confidence=90,
        evidence_ids_by_dimension={dimension: [evidence_id] for dimension, evidence_id in zip(
            ("liquidity", "commute", "price", "residential"), evidence_ids, strict=True
        )},
    )


def _add_evidence(
    session, run_id: str, listing_id: int | None, *, count: int
) -> tuple[int, ...]:  # type: ignore[no-untyped-def]
    evidence = [
        EvidenceModel(
            run_id=run_id,
            listing_id=listing_id,
            claim=f"claim-{index}",
            source_url=f"https://example.test/{run_id}/{index}",
            source_type="fixture",
            excerpt=None,
            accessed_at=datetime.now(UTC),
        )
        for index in range(count)
    ]
    session.add_all(evidence)
    session.flush()
    return tuple(item.id for item in evidence)
