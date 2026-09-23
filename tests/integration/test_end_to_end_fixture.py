from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from mybudongsan.domain.requests import MoneyRange, RegionCriterion, RequestStatus, SearchRequest
from mybudongsan.domain.runs import RunStage, RunStatus
from mybudongsan.integrations.drive import DriveBackup
from mybudongsan.integrations.sheets import SheetsProjection
from mybudongsan.notifications.outbox import NotificationOutbox
from mybudongsan.notifications.policy import NotificationEventType, plan_events
from mybudongsan.reports.publication import ReportPublicationService
from mybudongsan.reports.renderer import (
    AssessedCandidate,
    ReportBundle,
    ReportRunSummary,
)
from mybudongsan.reports.renderer import (
    EvidenceRecord as ReportEvidenceRecord,
)
from mybudongsan.research.contracts import ResearchBundle
from mybudongsan.research.ingest import ResearchBundleIngestService
from mybudongsan.storage.database import Database
from mybudongsan.storage.models import (
    AssessmentModel,
    ListingModel,
    ListingSnapshotModel,
    NotificationEventModel,
    ReportModel,
)
from mybudongsan.storage.repositories import (
    AssessmentRepository,
    EvidenceRepository,
    RequestRepository,
    RunRepository,
)
from mybudongsan.workflows.research_run import ResearchRunService


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def execute(self) -> dict[str, object]:
        return self._payload


class _SheetsValues:
    def __init__(self, service: _SheetsService) -> None:
        self._service = service

    def get(self, **kwargs: Any) -> _Response:
        sheet_range = str(kwargs["range"])
        if sheet_range == "'조사 현황'!A2:A":
            values = [[run_id] for run_id in self._service.run_rows]
        elif sheet_range == "'추천 결과'!A2:B":
            values = [[run_id, candidate_id] for run_id, candidate_id in self._service.candidates]
        else:
            values = []
        return _Response({"values": values})

    def clear(self, **kwargs: Any) -> _Response:
        del kwargs
        return _Response({})

    def batchUpdate(self, **kwargs: Any) -> _Response:
        for entry in kwargs["body"]["data"]:
            sheet_range = str(entry["range"])
            values = entry["values"]
            if sheet_range.startswith("'조사 현황'!A") and sheet_range != "'조사 현황'!A1:F1":
                self._service.run_rows[str(values[0][0])] = list(values[0])
            if sheet_range.startswith("'추천 결과'!A") and sheet_range != "'추천 결과'!A1:K1":
                key = (str(values[0][0]), str(values[0][1]))
                self._service.candidates[key] = list(values[0])
        return _Response({})


class _SheetsSpreadsheets:
    def __init__(self, service: _SheetsService) -> None:
        self._service = service

    def get(self, **kwargs: Any) -> _Response:
        del kwargs
        return _Response(
            {"sheets": [{"properties": {"title": title}} for title in self._service.tabs]}
        )

    def batchUpdate(self, **kwargs: Any) -> _Response:
        for request in kwargs["body"]["requests"]:
            self._service.tabs.add(str(request["addSheet"]["properties"]["title"]))
        return _Response({})

    def values(self) -> _SheetsValues:
        return _SheetsValues(self._service)


class _SheetsService:
    def __init__(self) -> None:
        self.tabs = {"검색 요청"}
        self.run_rows: dict[str, list[object]] = {}
        self.candidates: dict[tuple[str, str], list[object]] = {}

    def spreadsheets(self) -> _SheetsSpreadsheets:
        return _SheetsSpreadsheets(self)


class _DriveFiles:
    def __init__(self, service: _DriveService) -> None:
        self._service = service

    def list(self, **kwargs: Any) -> _Response:
        query = str(kwargs["q"])
        name, parent, mime_type = _drive_query_values(query)
        matches = [
            record
            for record in self._service.files_by_id.values()
            if record["name"] == name
            and record["parent"] == parent
            and record["mimeType"] == mime_type
        ]
        return _Response(
            {
                "files": [
                    {
                        "id": record["id"],
                        "name": record["name"],
                        "mimeType": record["mimeType"],
                        "parents": [record["parent"]],
                    }
                    for record in matches
                ]
            }
        )

    def create(self, **kwargs: Any) -> _Response:
        body = kwargs["body"]
        file_id = f"drive-{len(self._service.files_by_id) + 1}"
        self._service.files_by_id[file_id] = {
            "id": file_id,
            "name": str(body["name"]),
            "parent": str(body["parents"][0]),
            "mimeType": str(body.get("mimeType", "application/octet-stream")),
        }
        return _Response({"id": file_id})

    def update(self, **kwargs: Any) -> _Response:
        self._service.updated_ids.append(str(kwargs["fileId"]))
        return _Response({"id": str(kwargs["fileId"])})


class _DriveService:
    def __init__(self) -> None:
        self.files_by_id: dict[str, dict[str, str]] = {}
        self.updated_ids: list[str] = []

    def files(self) -> _DriveFiles:
        return _DriveFiles(self)


def _drive_query_values(query: str) -> tuple[str, str, str]:
    parts = query.split("'")
    return parts[1].replace("\\'", "'"), parts[3], parts[5]


def test_v1_fixture_acceptance_is_idempotent_across_resume(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'acceptance.sqlite3'}")
    database.create_schema()
    request_repository = RequestRepository(database)
    run_repository = RunRepository(database)
    run_service = ResearchRunService(request_repository, run_repository)
    request = _approved_request()
    request_repository.save_version(request)
    run_service.start("run-acceptance", request)

    fixture_path = Path(__file__).parents[1] / "fixtures" / "research_bundle.json"
    fixture = ResearchBundle.model_validate_json(fixture_path.read_text(encoding="utf-8"))
    assert (len(fixture.discovered), len(fixture.verified), len(fixture.deep_assessments)) == (
        15,
        7,
        3,
    )
    deep_assessments = list(fixture.deep_assessments)
    deep_assessments[1] = deep_assessments[1].model_copy(
        update={
            "evaluation_input": deep_assessments[1].evaluation_input.model_copy(
                update={"required_passed": False}
            )
        }
    )
    deep_assessments[2] = deep_assessments[2].model_copy(
        update={
            "evaluation_input": deep_assessments[2].evaluation_input.model_copy(
                update={"confidence": 84}
            )
        }
    )
    verified = list(fixture.verified)
    verified[0] = verified[0].model_copy(
        update={
            "source_listing_id": fixture.discovered[0].source_listing_id,
            "complex_name": fixture.discovered[0].complex_name,
            "address": fixture.discovered[0].address,
        }
    )
    fixture = fixture.model_copy(
        update={
            "verified": tuple(verified),
            "deep_assessments": tuple(deep_assessments),
        }
    )

    ingest = ResearchBundleIngestService(database)
    verification = ingest.ingest_through_verification("run-acceptance", fixture)
    interrupted = run_repository.get("run-acceptance")
    assert interrupted.current_stage is RunStage.VERIFICATION_COMPLETE
    assert RunStage.DEEP_RESEARCH_COMPLETE not in interrupted.checkpoints
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(AssessmentModel)) == 0
    run_service.fail_transient("run-acceptance", "simulated interruption after verification")
    assert run_service.resume("run-acceptance").next_stage is RunStage.DEEP_RESEARCH_COMPLETE
    first_ingest = ingest.resume_from_verification("run-acceptance", fixture)
    resumed_ingest = ingest.resume_from_verification("run-acceptance", fixture)
    assert first_ingest.created == 24
    assert first_ingest.updated == 1
    assert first_ingest.created == verification.created + 3
    assert resumed_ingest.created == first_ingest.created
    assert resumed_ingest.results == ()
    ingested_run = run_repository.get("run-acceptance")
    assert ingested_run.checkpoints[RunStage.VERIFICATION_COMPLETE].checkpoint[
        "verified_count"
    ] == 7
    assert ingested_run.checkpoints[RunStage.DEEP_RESEARCH_COMPLETE].checkpoint[
        "deep_assessment_count"
    ] == 3

    assessments = AssessmentRepository(database).list_for_run("run-acceptance", limit=7)
    assert len(assessments) <= 3
    results = [item.assessment.evaluation_result for item in assessments]
    assert [result.recommendable for result in results] == [True, False, False]
    assert "required_failed" in results[1].reasons
    assert "confidence_below_85" in results[2].reasons

    report_bundle = _report_bundle(database, request, "run-acceptance")
    publication = ReportPublicationService(database)
    artifacts = publication.publish("run-acceptance", report_bundle, tmp_path / "artifacts")
    original_checksums = _checksums(artifacts.directory)

    # Simulate an interruption after local verification but before external sync. A resumed
    # publication must reuse the immutable artifacts and SQLite report row.
    run_service.fail_transient("run-acceptance", "simulated interruption after artifact checks")
    resumed_artifacts = publication.publish(
        "run-acceptance", report_bundle, tmp_path / "artifacts"
    )
    assert _checksums(resumed_artifacts.directory) == original_checksums

    sheets_service = _SheetsService()
    sheets = SheetsProjection(sheets_service)
    run = run_repository.get("run-acceptance")
    candidates = _sheet_candidates(report_bundle)
    for _ in range(2):
        sheets.sync_run("sheet-fixture", request, _sheet_run(run), candidates)
    assert set(sheets_service.run_rows) == {"run-acceptance"}
    assert set(sheets_service.candidates) == {
        ("run-acceptance", candidate["candidate_id"]) for candidate in candidates
    }

    drive_service = _DriveService()
    drive = DriveBackup(drive_service, lock_directory=tmp_path)
    for _ in range(2):
        drive.upload_run(
            "drive-root",
            request.request_id,
            "run-acceptance",
            report_bundle.run.completed_at,
            artifacts.directory,
        )
    assert len(drive_service.files_by_id) == 6
    assert len(drive_service.updated_ids) == 3

    outbox = NotificationOutbox(database)
    planned = plan_events(specialist=True, needs_action=False)
    for event in planned:
        outbox.enqueue("run-acceptance", event)
        outbox.enqueue("run-acceptance", event)
    for channel in ("kakao", "gmail"):
        for delivery in outbox.pending("run-acceptance", channel=channel):
            assert delivery.claim_token
            outbox.mark_sent(
                delivery.event_id,
                f"fake-{channel}-{delivery.event_id}",
                claim_token=delivery.claim_token,
            )
        assert outbox.pending("run-acceptance", channel=channel) == ()

    final = run_service.advance(
        "run-acceptance",
        RunStage.SYNC_COMPLETE,
        {"sheets": "sheet-fixture", "drive": "drive-root", "notifications": "sent"},
    )
    assert final.current_stage is RunStage.SYNC_COMPLETE
    assert final.status is RunStatus.COMPLETED
    stored_final = run_repository.get("run-acceptance")
    assert stored_final.current_stage is RunStage.SYNC_COMPLETE
    assert stored_final.status is RunStatus.COMPLETED
    assert stored_final.checkpoint == final.checkpoint

    with database.session() as session:
        listings = tuple(session.scalars(select(ListingModel)))
        snapshots = tuple(session.scalars(select(ListingSnapshotModel)))
        duplicate_listing = next(
            listing
            for listing in listings
            if listing.source_listing_id == fixture.discovered[0].source_listing_id
        )
        duplicate_snapshots = [
            snapshot for snapshot in snapshots if snapshot.listing_id == duplicate_listing.id
        ]
        verified_snapshots = [
            snapshot
            for snapshot in snapshots
            if snapshot.payload["observed_at"]
            == fixture.verified[0].model_dump(mode="json")["observed_at"]
        ]
        assert len(listings) == 24
        assert len(duplicate_snapshots) == 2
        assert len(verified_snapshots) == 7
        assert session.scalar(select(func.count()).select_from(ListingSnapshotModel)) == 25
        deep_count = session.scalar(select(func.count()).select_from(AssessmentModel))
        assert deep_count == 3
        assert len(report_bundle.candidates) <= 3
        assert session.scalar(select(func.count()).select_from(ReportModel)) == 1
        event_types = set(session.scalars(select(NotificationEventModel.event_type)))
        assert len(event_types) <= 5
        terminal_channels = set(
            session.execute(
                select(NotificationEventModel.channel).where(
                    NotificationEventModel.event_type == NotificationEventType.COMPLETED.value
                )
            ).scalars()
        )
        assert terminal_channels == {"kakao", "gmail"}


def _approved_request() -> SearchRequest:
    return SearchRequest(
        request_id="request-acceptance",
        version=1,
        regions=tuple(
            RegionCriterion(name=name)
            for name in ("서울 강서구", "서울 양천구", "서울 구로구", "서울 마포구", "서울 영등포구")
        ),
        budget=MoneyRange(minimum=600_000_000, maximum=1_000_000_000),
        required=("active",),
        excluded=("mandatory gate failure",),
        status=RequestStatus.APPROVED,
    )


def _report_bundle(database: Database, request: SearchRequest, run_id: str) -> ReportBundle:
    run = RunRepository(database).get(run_id)
    candidates = tuple(
        AssessedCandidate(
            candidate_id=f"{item.listing.source}:{item.listing.source_listing_id}",
            listing=item.listing,
            assessment=item.assessment.evaluation_result,
            evidence_ids=tuple(
                dict.fromkeys(
                    evidence_id
                    for dimension in ("liquidity", "commute", "price", "residential")
                    for evidence_id in getattr(
                        item.assessment.evaluation_input.evidence_ids_by_dimension, dimension
                    )
                )
            ),
        )
        for item in AssessmentRepository(database).list_for_run(run_id, limit=3)
        if item.assessment.evaluation_result.recommendable
    )
    evidence_ids = tuple(
        dict.fromkeys(evidence_id for candidate in candidates for evidence_id in candidate.evidence_ids)
    )
    evidence = tuple(
        ReportEvidenceRecord(
            evidence_id=item.evidence_id,
            claim=item.claim,
            source_url=item.source_url,
            source_type=item.source_type,
            excerpt=item.excerpt,
            accessed_at=item.accessed_at,
        )
        for item in EvidenceRepository(database).list_for_run(run_id, evidence_ids=evidence_ids)
    )
    assert run.current_stage is RunStage.DEEP_RESEARCH_COMPLETE
    assert run.completed_at is not None
    return ReportBundle(
        request=request,
        run=ReportRunSummary(
            run_id=run_id,
            status=run.status.value,
            stage=run.current_stage.value,
            completed_at=run.completed_at,
            slug="run-acceptance",
        ),
        candidates=candidates,
        evidence=evidence,
    )


def _checksums(directory: Path) -> dict[str, str]:
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.iterdir())
        if path.is_file()
    }


def _sheet_run(run: object) -> Mapping[str, object]:
    assert hasattr(run, "run_id")
    return {
        "run_id": run.run_id,
        "status": run.status.value,
        "stage": run.current_stage.value,
        "completed_at": run.completed_at.isoformat(),
    }


def _sheet_candidates(bundle: ReportBundle) -> Sequence[Mapping[str, object]]:
    return tuple(
        {
            "candidate_id": candidate.candidate_id,
            "complex_name": candidate.listing.complex_name,
            "confidence": candidate.assessment.confidence,
            "recommendable": candidate.assessment.recommendable,
        }
        for candidate in bundle.candidates
    )
