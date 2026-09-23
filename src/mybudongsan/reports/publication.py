from __future__ import annotations

import shutil
import tempfile
from hashlib import sha256
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
        expected = self._renderer.paths_for(bundle, output_root)
        if expected.directory.exists():
            self._validate_orphaned_artifacts(run_id, bundle, expected)
            self._persist(run_id, expected)
            return expected
        artifacts = self._renderer.render(bundle, output_root)
        try:
            self._persist(run_id, artifacts)
        except Exception:
            shutil.rmtree(artifacts.directory, ignore_errors=True)
            raise
        return artifacts

    def _persist(self, run_id: str, artifacts: RenderedArtifacts) -> None:
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

    def _validate_orphaned_artifacts(
        self,
        run_id: str,
        bundle: ReportBundle,
        artifacts: RenderedArtifacts,
    ) -> None:
        if artifacts.directory.is_symlink() or not artifacts.directory.is_dir():
            raise ValueError("orphaned report directory is unsafe")
        for path in (
            artifacts.report_path,
            artifacts.candidates_path,
            artifacts.run_data_path,
        ):
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"orphaned report artifact is unsafe: {path.name}")
        stored_bundle = ReportBundle.model_validate_json(
            artifacts.run_data_path.read_text(encoding="utf-8")
        )
        if stored_bundle.run.run_id != run_id or stored_bundle != bundle:
            raise ValueError("orphaned report run-data ownership mismatch")
        with tempfile.TemporaryDirectory() as directory:
            expected = self._renderer.render(bundle, Path(directory))
            for actual_path, expected_path in (
                (artifacts.report_path, expected.report_path),
                (artifacts.candidates_path, expected.candidates_path),
                (artifacts.run_data_path, expected.run_data_path),
            ):
                if sha256(actual_path.read_bytes()).digest() != sha256(
                    expected_path.read_bytes()
                ).digest():
                    raise ValueError(
                        f"orphaned report checksum mismatch: {actual_path.name}"
                    )

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
