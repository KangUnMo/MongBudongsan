from __future__ import annotations

import json
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

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


@dataclass(frozen=True)
class RunDeletionPreview:
    run_id: str
    artifact_directory: Path | None
    row_counts: dict[str, int]


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

    def cancel(self, run_id: str) -> RunRecord:
        return self._run_repository.cancel(run_id)

    def resume(self, run_id: str) -> ResumePoint:
        run = self._run_repository.get(run_id)
        if run.status is RunStatus.COMPLETED:
            raise InvalidTransition("run has already completed")
        if run.status is RunStatus.CANCELLED:
            raise InvalidTransition("cancelled runs cannot resume")
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


class RunDeletionService:
    """Delete exactly one run's owned rows and validated managed artifacts."""

    def __init__(self, run_repository: RunRepository, managed_artifact_root: Path) -> None:
        self._run_repository = run_repository
        self._managed_artifact_root = managed_artifact_root.resolve()

    def preview(self, run_id: str) -> RunDeletionPreview:
        run = self._run_repository.get(run_id)
        checkpoint = run.checkpoints.get(RunStage.REPORT_COMPLETE)
        artifact_directory = (
            None
            if checkpoint is None
            else self._validated_artifact_directory(run_id, checkpoint.checkpoint)
        )
        return RunDeletionPreview(
            run_id=run_id,
            artifact_directory=artifact_directory,
            row_counts=self._run_repository.owned_counts(run_id),
        )

    def delete(self, run_id: str, *, confirmation: str) -> RunDeletionPreview:
        if confirmation != run_id:
            raise ValueError("confirmation must exactly match the run ID")
        preview = self.preview(run_id)
        artifact_directory = preview.artifact_directory
        if artifact_directory is None:
            self._run_repository.delete_owned(run_id)
            return preview
        quarantine = artifact_directory.with_name(
            f".{artifact_directory.name}.delete-{uuid.uuid4().hex}"
        )
        artifact_directory.rename(quarantine)
        try:
            self._run_repository.delete_owned(run_id)
        except Exception:
            quarantine.rename(artifact_directory)
            raise
        shutil.rmtree(quarantine)
        return preview

    def _validated_artifact_directory(
        self, run_id: str, checkpoint: dict[str, object]
    ) -> Path:
        stored_path = checkpoint.get("report_path")
        if not isinstance(stored_path, str):
            raise TypeError("stored report path is invalid")
        report_path = Path(stored_path)
        artifact_directory = report_path.parent
        if artifact_directory.is_symlink() or report_path.is_symlink():
            raise ValueError("managed artifact paths must not be symbolic links")
        resolved_directory = artifact_directory.resolve()
        if resolved_directory == self._managed_artifact_root:
            raise ValueError("refusing to delete the shared managed artifact root")
        try:
            resolved_directory.relative_to(self._managed_artifact_root)
        except ValueError as error:
            raise ValueError("artifact directory is outside the managed artifact root") from error
        required = {
            "report.md": report_path,
            "candidates.csv": report_path.with_name("candidates.csv"),
            "run-data.json": report_path.with_name("run-data.json"),
        }
        for name, path in required.items():
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"managed artifact is missing or unsafe: {name}")
        payload = json.loads(required["run-data.json"].read_text(encoding="utf-8"))
        if payload.get("run", {}).get("run_id") != run_id:
            raise ValueError("artifact run ID does not match the deletion target")
        return resolved_directory
