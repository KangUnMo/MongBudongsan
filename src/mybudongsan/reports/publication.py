from __future__ import annotations

import shutil
from pathlib import Path

from mybudongsan.domain.runs import RunStage
from mybudongsan.reports.renderer import RenderedArtifacts, ReportBundle, ReportRenderer
from mybudongsan.storage.database import Database
from mybudongsan.storage.repositories import ReportRepository, RunRepository


class ReportPublicationService:
    """Publish artifacts, then atomically persist their report row and checkpoint."""

    def __init__(self, database: Database) -> None:
        self._database = database
        self._report_repository = ReportRepository(database)
        self._run_repository = RunRepository(database)
        self._renderer = ReportRenderer()

    def publish(
        self, run_id: str, bundle: ReportBundle, output_root: Path) -> RenderedArtifacts:
        run = self._run_repository.get(run_id)
        published = run.checkpoints.get(RunStage.REPORT_COMPLETE)
        if published is not None:
            return self._published_artifacts(run_id, published.checkpoint)
        artifacts = self._renderer.render(bundle, output_root)
        try:
            with self._database.session() as session:
                self._report_repository.save_markdown(
                    run_id,
                    artifacts.report_path.read_text(encoding="utf-8"),
                    session=session,
                )
                self._run_repository.advance(
                    run_id=run_id,
                    stage=RunStage.REPORT_COMPLETE,
                    checkpoint={"report_path": str(artifacts.report_path)},
                    session=session,
                )
        except Exception:
            shutil.rmtree(artifacts.directory, ignore_errors=True)
            raise
        return artifacts

    @staticmethod
    def _published_artifacts(
        run_id: str, checkpoint: dict[str, object]
    ) -> RenderedArtifacts:
        stored_path = checkpoint.get("report_path")
        if not isinstance(stored_path, str):
            raise TypeError(f"stored report path is invalid for run: {run_id}")
        report_path = Path(stored_path)
        artifacts = RenderedArtifacts(
            directory=report_path.parent,
            report_path=report_path,
            candidates_path=report_path.with_name("candidates.csv"),
            run_data_path=report_path.with_name("run-data.json"),
        )
        for path in (
            artifacts.report_path,
            artifacts.candidates_path,
            artifacts.run_data_path,
        ):
            if not path.is_file() or path.is_symlink():
                raise FileNotFoundError(f"stored report artifact is missing: {path.name}")
        return artifacts
