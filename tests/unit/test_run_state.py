from enum import StrEnum

import pytest

from mybudongsan.domain.runs import (
    STAGE_TRANSITIONS,
    InvalidTransition,
    RunStage,
    RunStatus,
    validate_transition,
)

EXPECTED_STAGES = [
    "request_approved",
    "discovery_complete",
    "filter_complete",
    "verification_complete",
    "deep_research_complete",
    "report_complete",
    "sync_complete",
]


def test_run_stages_follow_the_exact_ordered_path() -> None:
    assert issubclass(RunStage, StrEnum)
    assert [stage.value for stage in RunStage] == EXPECTED_STAGES
    assert tuple(STAGE_TRANSITIONS) == tuple(RunStage)[:-1]
    assert STAGE_TRANSITIONS[RunStage.REQUEST_APPROVED] is RunStage.DISCOVERY_COMPLETE


def test_run_status_is_a_string_enum() -> None:
    assert issubclass(RunStatus, StrEnum)
    assert RunStatus.RESUMABLE == "resumable"


def test_transition_cannot_skip_a_completed_stage() -> None:
    with pytest.raises(InvalidTransition, match="expected discovery_complete"):
        validate_transition(
            RunStage.REQUEST_APPROVED,
            RunStage.VERIFICATION_COMPLETE,
        )
