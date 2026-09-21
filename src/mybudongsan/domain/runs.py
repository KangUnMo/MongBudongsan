from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Final


class RunStage(StrEnum):
    REQUEST_APPROVED = "request_approved"
    DISCOVERY_COMPLETE = "discovery_complete"
    FILTER_COMPLETE = "filter_complete"
    VERIFICATION_COMPLETE = "verification_complete"
    DEEP_RESEARCH_COMPLETE = "deep_research_complete"
    REPORT_COMPLETE = "report_complete"
    SYNC_COMPLETE = "sync_complete"


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    RESUMABLE = "resumable"
    COMPLETED = "completed"


class InvalidTransition(ValueError):
    pass


STAGE_TRANSITIONS: Final[Mapping[RunStage, RunStage]] = MappingProxyType(
    {
        RunStage.REQUEST_APPROVED: RunStage.DISCOVERY_COMPLETE,
        RunStage.DISCOVERY_COMPLETE: RunStage.FILTER_COMPLETE,
        RunStage.FILTER_COMPLETE: RunStage.VERIFICATION_COMPLETE,
        RunStage.VERIFICATION_COMPLETE: RunStage.DEEP_RESEARCH_COMPLETE,
        RunStage.DEEP_RESEARCH_COMPLETE: RunStage.REPORT_COMPLETE,
        RunStage.REPORT_COMPLETE: RunStage.SYNC_COMPLETE,
    }
)


def validate_transition(current_stage: RunStage, next_stage: RunStage) -> None:
    expected_stage = STAGE_TRANSITIONS.get(current_stage)
    if expected_stage is not next_stage:
        expected = expected_stage.value if expected_stage is not None else "no further stage"
        raise InvalidTransition(
            f"cannot transition from {current_stage.value} to {next_stage.value}; "
            f"expected {expected}"
        )
