from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from mybudongsan.domain.requests import MoneyRange, RegionCriterion, RequestStatus, SearchRequest
from mybudongsan.domain.runs import InvalidTransition, RunStage, RunStatus
from mybudongsan.storage.models import ResearchRunModel
from mybudongsan.storage.repositories import RequestRepository, RunRepository
from mybudongsan.workflows.research_run import ResearchRunService


@pytest.fixture
def approved_request() -> SearchRequest:
    return SearchRequest(
        request_id="req-run-state",
        version=1,
        regions=[RegionCriterion(name="서울 강서구")],
        budget=MoneyRange(minimum=Decimal(600000000), maximum=Decimal(900000000)),
        status=RequestStatus.APPROVED,
    )


def test_advance_rejects_skipping_from_request_approval(
    database, approved_request: SearchRequest
) -> None:  # type: ignore[no-untyped-def]
    request_repository = RequestRepository(database)
    request_repository.save_version(approved_request)
    service = ResearchRunService(request_repository, RunRepository(database))
    service.start("run-skip", approved_request)

    with pytest.raises(InvalidTransition, match="expected discovery_complete"):
        service.advance("run-skip", RunStage.VERIFICATION_COMPLETE, {})


def test_transient_failure_is_resumable_and_resume_skips_completed_checkpoints(
    database, approved_request: SearchRequest
) -> None:  # type: ignore[no-untyped-def]
    request_repository = RequestRepository(database)
    request_repository.save_version(approved_request)
    repository = RunRepository(database)
    service = ResearchRunService(request_repository, repository)
    service.start("run-resume", approved_request)

    first = service.advance(
        "run-resume",
        RunStage.DISCOVERY_COMPLETE,
        {"discovered_listing_ids": ["listing-1", "listing-2"]},
    )
    assert isinstance(first.completed_at, datetime)
    with database.session() as session:
        stored_run = session.scalar(
            select(ResearchRunModel).where(ResearchRunModel.run_id == "run-resume")
        )
    assert stored_run is not None
    stored_history = stored_run.checkpoint_payload["stages"]
    assert isinstance(stored_history, dict)
    assert stored_history["discovery_complete"]["checkpoint"] == {
        "discovered_listing_ids": ["listing-1", "listing-2"]
    }
    assert (
        stored_history["discovery_complete"]["idempotency_key"]
        == "run-resume:discovery_complete"
    )

    repeated = service.advance(
        "run-resume",
        RunStage.DISCOVERY_COMPLETE,
        {"discovered_listing_ids": ["must-not-replace"]},
    )
    assert repeated.completed_at is not None
    assert first.completed_at is not None
    assert repeated.completed_at.replace(tzinfo=None) == first.completed_at.replace(tzinfo=None)
    assert repeated.checkpoint == {"discovered_listing_ids": ["listing-1", "listing-2"]}

    service.advance("run-resume", RunStage.FILTER_COMPLETE, {"candidate_count": 1})
    service.fail_transient("run-resume", "source temporarily unavailable")

    run = repository.get("run-resume")
    assert run.status is RunStatus.RESUMABLE
    assert run.error_message == "source temporarily unavailable"

    resume_point = service.resume("run-resume")
    assert resume_point.run_id == "run-resume"
    assert resume_point.next_stage is RunStage.VERIFICATION_COMPLETE
    assert resume_point.checkpoint == {"candidate_count": 1}


def test_start_rejects_forged_approved_request_when_persisted_version_is_draft(
    database, approved_request: SearchRequest
) -> None:  # type: ignore[no-untyped-def]
    persisted_draft = approved_request.model_copy(update={"status": RequestStatus.DRAFT})
    request_repository = RequestRepository(database)
    request_repository.save_version(persisted_draft)
    service = ResearchRunService(request_repository, RunRepository(database))

    with pytest.raises(ValueError, match="approved"):
        service.start("run-forged", approved_request)


def test_delayed_duplicate_returns_its_original_checkpoint_without_rewinding_progress(
    database, approved_request: SearchRequest
) -> None:  # type: ignore[no-untyped-def]
    RequestRepository(database).save_version(approved_request)
    repository = RunRepository(database)
    repository.create("run-history", approved_request.request_id, approved_request.version)
    discovery_checkpoint = {"discovered_listing_ids": ["listing-1"]}
    repository.advance(
        run_id="run-history",
        stage=RunStage.DISCOVERY_COMPLETE,
        checkpoint=discovery_checkpoint,
    )
    repository.advance(
        run_id="run-history",
        stage=RunStage.FILTER_COMPLETE,
        checkpoint={"candidate_count": 1},
    )

    duplicate = repository.advance(
        run_id="run-history",
        stage=RunStage.DISCOVERY_COMPLETE,
        checkpoint={"discovered_listing_ids": ["must-not-replace"]},
    )
    current = repository.get("run-history")

    assert duplicate.current_stage is RunStage.FILTER_COMPLETE
    assert duplicate.checkpoint == discovery_checkpoint
    assert current.current_stage is RunStage.FILTER_COMPLETE
    assert current.checkpoint == {"candidate_count": 1}
    assert set(current.checkpoints) == {
        RunStage.REQUEST_APPROVED,
        RunStage.DISCOVERY_COMPLETE,
        RunStage.FILTER_COMPLETE,
    }
    assert current.checkpoints[RunStage.DISCOVERY_COMPLETE].checkpoint == discovery_checkpoint
    assert (
        current.checkpoints[RunStage.DISCOVERY_COMPLETE].idempotency_key
        == "run-history:discovery_complete"
    )


def test_create_does_not_accept_an_arbitrary_status_or_stage(
    database, approved_request: SearchRequest
) -> None:  # type: ignore[no-untyped-def]
    RequestRepository(database).save_version(approved_request)

    with pytest.raises(TypeError):
        RunRepository(database).create(  # type: ignore[call-arg]
            "run-invalid-create",
            approved_request.request_id,
            approved_request.version,
            status=RunStatus.RESUMABLE,
            current_stage=RunStage.FILTER_COMPLETE,
        )


def test_legacy_single_checkpoint_is_read_resumed_and_preserved_on_advance(
    database, approved_request: SearchRequest
) -> None:  # type: ignore[no-untyped-def]
    request_repository = RequestRepository(database)
    request_repository.save_version(approved_request)
    completed_at = datetime.now(UTC)
    legacy_checkpoint = {"discovered_listing_ids": ["legacy-listing-1"]}
    with database.session() as session:
        session.add(
            ResearchRunModel(
                run_id="run-legacy",
                request_id=approved_request.request_id,
                request_version=approved_request.version,
                status=RunStatus.RESUMABLE.value,
                current_stage=RunStage.DISCOVERY_COMPLETE.value,
                checkpoint_payload={
                    "checkpoint": legacy_checkpoint,
                    "idempotency_key": "run-legacy:discovery_complete",
                },
                started_at=completed_at,
                completed_at=completed_at,
            )
        )

    repository = RunRepository(database)
    service = ResearchRunService(request_repository, repository)
    loaded = repository.get("run-legacy")
    assert loaded.checkpoint == legacy_checkpoint
    assert loaded.checkpoints[RunStage.DISCOVERY_COMPLETE].checkpoint == legacy_checkpoint
    assert (
        loaded.checkpoints[RunStage.DISCOVERY_COMPLETE].idempotency_key
        == "run-legacy:discovery_complete"
    )

    resume_point = service.resume("run-legacy")
    assert resume_point.next_stage is RunStage.FILTER_COMPLETE
    assert resume_point.checkpoint == legacy_checkpoint

    service.advance("run-legacy", RunStage.FILTER_COMPLETE, {"candidate_count": 1})
    advanced = repository.get("run-legacy")
    assert advanced.current_stage is RunStage.FILTER_COMPLETE
    assert advanced.checkpoint == {"candidate_count": 1}
    assert advanced.checkpoints[RunStage.DISCOVERY_COMPLETE].checkpoint == legacy_checkpoint
    assert set(advanced.checkpoints) == {
        RunStage.DISCOVERY_COMPLETE,
        RunStage.FILTER_COMPLETE,
    }
