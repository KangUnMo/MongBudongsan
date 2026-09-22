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
