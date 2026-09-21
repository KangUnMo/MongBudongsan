from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select

from mybudongsan.domain.requests import SearchRequest
from mybudongsan.domain.runs import (
    InvalidTransition,
    RunStage,
    RunStatus,
    validate_transition,
)
from mybudongsan.storage.database import Database
from mybudongsan.storage.models import ResearchRunModel, SearchRequestModel


class RequestRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def save_version(self, request: SearchRequest) -> None:
        with self._database.session() as session:
            existing = session.scalar(
                select(SearchRequestModel.id).where(
                    SearchRequestModel.request_id == request.request_id,
                    SearchRequestModel.version == request.version,
                )
            )
            if existing is not None:
                raise ValueError(
                    f"request version already exists: {request.request_id}/{request.version}"
                )
            session.add(
                SearchRequestModel(
                    request_id=request.request_id,
                    version=request.version,
                    payload=request.model_dump(mode="json"),
                    created_at=datetime.now(UTC),
                )
            )

    def get_version(self, request_id: str, version: int) -> SearchRequest:
        with self._database.session() as session:
            stored = session.scalar(
                select(SearchRequestModel).where(
                    SearchRequestModel.request_id == request_id,
                    SearchRequestModel.version == version,
                )
            )
            if stored is None:
                raise ValueError(f"request version not found: {request_id}/{version}")
            return SearchRequest.model_validate(stored.payload)


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    request_id: str
    request_version: int
    status: RunStatus
    current_stage: RunStage | None
    checkpoint: dict[str, object]
    error_message: str | None
    completed_at: datetime | None


class RunRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def create(
        self,
        run_id: str,
        request_id: str,
        request_version: int,
        *,
        status: RunStatus = RunStatus.PENDING,
        current_stage: RunStage | None = None,
        checkpoint: dict[str, object] | None = None,
    ) -> RunRecord:
        completed_at = datetime.now(UTC) if current_stage is not None else None
        with self._database.session() as session:
            run = ResearchRunModel(
                run_id=run_id,
                request_id=request_id,
                request_version=request_version,
                status=status.value,
                current_stage=current_stage.value if current_stage is not None else None,
                checkpoint_payload=self._checkpoint_payload(run_id, current_stage, checkpoint),
                started_at=datetime.now(UTC),
                completed_at=completed_at,
            )
            session.add(run)
            session.flush()
            return self._to_record(run)

    def get(self, run_id: str) -> RunRecord:
        with self._database.session() as session:
            run = session.scalar(select(ResearchRunModel).where(ResearchRunModel.run_id == run_id))
            if run is None:
                raise ValueError(f"run not found: {run_id}")
            return self._to_record(run)

    def advance(
        self,
        *,
        run_id: str,
        stage: RunStage,
        checkpoint: dict[str, object],
    ) -> RunRecord:
        """Atomically persist the next completed stage or return its prior checkpoint."""
        with self._database.session() as session:
            run = session.scalar(select(ResearchRunModel).where(ResearchRunModel.run_id == run_id))
            if run is None:
                raise ValueError(f"run not found: {run_id}")
            if run.current_stage == stage.value:
                return self._to_record(run)
            if run.current_stage is None:
                raise InvalidTransition("run has not completed request approval")
            validate_transition(RunStage(run.current_stage), stage)

            run.current_stage = stage.value
            run.status = (
                RunStatus.COMPLETED.value
                if stage is RunStage.SYNC_COMPLETE
                else RunStatus.RUNNING.value
            )
            run.checkpoint_payload = self._checkpoint_payload(run_id, stage, checkpoint)
            run.error_message = None
            run.completed_at = datetime.now(UTC)
            session.flush()
            return self._to_record(run)

    def mark_resumable(self, run_id: str, message: str) -> RunRecord:
        with self._database.session() as session:
            run = session.scalar(select(ResearchRunModel).where(ResearchRunModel.run_id == run_id))
            if run is None:
                raise ValueError(f"run not found: {run_id}")
            if run.status == RunStatus.COMPLETED.value:
                raise InvalidTransition("completed runs cannot become resumable")
            run.status = RunStatus.RESUMABLE.value
            run.error_message = message
            session.flush()
            return self._to_record(run)

    @staticmethod
    def _checkpoint_payload(
        run_id: str, stage: RunStage | None, checkpoint: dict[str, object] | None
    ) -> dict[str, object] | None:
        if stage is None:
            return None
        return {
            "checkpoint": checkpoint or {},
            "idempotency_key": f"{run_id}:{stage.value}",
        }

    @staticmethod
    def _to_record(run: ResearchRunModel) -> RunRecord:
        payload = run.checkpoint_payload or {}
        stored_checkpoint = payload.get("checkpoint", payload)
        if not isinstance(stored_checkpoint, dict):
            raise TypeError(f"invalid checkpoint payload for run: {run.run_id}")
        return RunRecord(
            run_id=run.run_id,
            request_id=run.request_id,
            request_version=run.request_version,
            status=RunStatus(run.status),
            current_stage=RunStage(run.current_stage) if run.current_stage is not None else None,
            checkpoint=dict(stored_checkpoint),
            error_message=run.error_message,
            completed_at=run.completed_at,
        )
