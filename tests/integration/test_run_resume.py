from datetime import datetime
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
    RequestRepository(database).save_version(approved_request)
    service = ResearchRunService(RunRepository(database))
    service.start("run-skip", approved_request)

    with pytest.raises(InvalidTransition, match="expected discovery_complete"):
        service.advance("run-skip", RunStage.VERIFICATION_COMPLETE, {})


def test_transient_failure_is_resumable_and_resume_skips_completed_checkpoints(
    database, approved_request: SearchRequest
) -> None:  # type: ignore[no-untyped-def]
    RequestRepository(database).save_version(approved_request)
    repository = RunRepository(database)
    service = ResearchRunService(repository)
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
    assert stored_run.checkpoint_payload == {
        "checkpoint": {"discovered_listing_ids": ["listing-1", "listing-2"]},
        "idempotency_key": "run-resume:discovery_complete",
    }

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
