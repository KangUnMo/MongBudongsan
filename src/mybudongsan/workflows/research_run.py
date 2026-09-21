from __future__ import annotations

from dataclasses import dataclass

from mybudongsan.domain.requests import RequestStatus, SearchRequest
from mybudongsan.domain.runs import (
    STAGE_TRANSITIONS,
    InvalidTransition,
    RunStage,
    RunStatus,
)
from mybudongsan.storage.repositories import RequestRepository, RunRecord, RunRepository


@dataclass(frozen=True)
class ResumePoint:
    run_id: str
    next_stage: RunStage
    checkpoint: dict[str, object]


class ResearchRunService:
    def __init__(
        self, request_repository: RequestRepository, run_repository: RunRepository
    ) -> None:
        self._request_repository = request_repository
        self._run_repository = run_repository

    def start(self, run_id: str, request: SearchRequest) -> RunRecord:
        persisted_request = self._request_repository.get_version(request.request_id, request.version)
        if persisted_request.status is not RequestStatus.APPROVED:
            raise ValueError("research runs require an approved request version")
        return self._run_repository.create(
            run_id=run_id,
            request_id=persisted_request.request_id,
            request_version=persisted_request.version,
        )

    def advance(
        self, run_id: str, stage: RunStage, checkpoint: dict[str, object]
    ) -> RunRecord:
        return self._run_repository.advance(
            run_id=run_id,
            stage=stage,
            checkpoint=checkpoint,
        )

    def fail_transient(self, run_id: str, message: str) -> RunRecord:
        return self._run_repository.mark_resumable(run_id, message)

    def resume(self, run_id: str) -> ResumePoint:
        run = self._run_repository.get(run_id)
        if run.status is RunStatus.COMPLETED:
            raise InvalidTransition("run has already completed")
        if run.current_stage is None:
            next_stage = RunStage.REQUEST_APPROVED
        else:
            try:
                next_stage = STAGE_TRANSITIONS[run.current_stage]
            except KeyError as error:
                raise InvalidTransition("run has no unfinished stage") from error
        return ResumePoint(
            run_id=run.run_id,
            next_stage=next_stage,
            checkpoint=run.checkpoint,
        )
