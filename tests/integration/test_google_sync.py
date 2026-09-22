from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from mybudongsan.cli import app
from mybudongsan.domain.requests import SearchRequest
from mybudongsan.integrations.drive import DriveBackup
from mybudongsan.integrations.sheets import SheetsProjection
from mybudongsan.storage.database import Database
from mybudongsan.storage.repositories import RequestRepository


class FakeRequest:
    def __init__(self, payload: Any) -> None:
        self.payload = payload

    def execute(self) -> Any:
        return self.payload


class FakeSheetsValues:
    def __init__(self, parent: FakeSheets) -> None:
        self.parent = parent

    def get(self, **kwargs: Any) -> FakeRequest:
        self.parent.get_calls.append(kwargs)
        return FakeRequest({"values": [self.parent.request_row]})

    def batchUpdate(self, **kwargs: Any) -> FakeRequest:
        self.parent.value_updates.append(kwargs)
        return FakeRequest({})


class FakeSpreadsheets:
    def __init__(self, parent: FakeSheets) -> None:
        self.parent = parent

    def get(self, **kwargs: Any) -> FakeRequest:
        self.parent.metadata_calls.append(kwargs)
        return FakeRequest({"sheets": [{"properties": {"title": title}} for title in self.parent.tabs]})

    def batchUpdate(self, **kwargs: Any) -> FakeRequest:
        self.parent.tab_updates.append(kwargs)
        for request in kwargs["body"]["requests"]:
            self.parent.tabs.append(request["addSheet"]["properties"]["title"])
        return FakeRequest({})

    def values(self) -> FakeSheetsValues:
        return FakeSheetsValues(self.parent)


class FakeSheets:
    def __init__(self, tabs: list[str] | None = None) -> None:
        self.tabs = list(tabs or [])
        self.request_row = [
            "req-1", "1", '[{"name":"서울 강서구","allow_expansion":false}]', "600", "900",
            "아파트", "역세권", "", "", "approved",
        ]
        self.metadata_calls: list[dict[str, Any]] = []
        self.get_calls: list[dict[str, Any]] = []
        self.tab_updates: list[dict[str, Any]] = []
        self.value_updates: list[dict[str, Any]] = []

    def spreadsheets(self) -> FakeSpreadsheets:
        return FakeSpreadsheets(self)


def _request() -> SearchRequest:
    return SearchRequest.model_validate(
        {
            "request_id": "req-1",
            "version": 1,
            "regions": [{"name": "서울 강서구"}],
            "budget": {"minimum": 600, "maximum": 900},
            "required": ["아파트"],
            "preferred": ["역세권"],
            "status": "approved",
        }
    )


def test_sheets_creates_missing_tabs_once_syncs_explicit_ranges_and_imports_only_request() -> None:
    service = FakeSheets(["검색 요청"])
    projection = SheetsProjection(service)

    imported = projection.import_request("sheet-1", 2)
    projection.sync_run(
        "sheet-1",
        _request(),
        {
            "run_id": "run-1",
            "status": "completed",
            "stage": "report_complete",
            "completed_at": "2026-09-22T00:00:00Z",
        },
        [{"candidate_id": "one", "complex_name": "단지", "total_score": 90}],
    )
    projection.sync_run(
        "sheet-1",
        _request(),
        {"run_id": "run-1", "status": "completed", "stage": "report_complete", "completed_at": "2026-09-22T00:00:00Z"},
        [{"candidate_id": "one", "complex_name": "단지", "total_score": 90}],
    )

    assert imported == _request()
    assert service.get_calls[0] == {"spreadsheetId": "sheet-1", "range": "'검색 요청'!A2:J2"}
    assert [call["range"] for call in service.get_calls[1:]] == [
        "'조사 현황'!A2:A",
        "'추천 결과'!A2:B",
        "'조사 현황'!A2:A",
        "'추천 결과'!A2:B",
    ]
    assert service.tab_updates[0]["body"]["requests"] == [
        {"addSheet": {"properties": {"title": "조사 현황"}}},
        {"addSheet": {"properties": {"title": "추천 결과"}}},
        {"addSheet": {"properties": {"title": "관심 매물"}}},
    ]
    assert len(service.tab_updates) == 1
    assert all("!A" in entry["range"] for update in service.value_updates for entry in update["body"]["data"])
    assert all("검색 요청" not in call.get("range", "") for call in service.metadata_calls)


@dataclass
class FakeFile:
    id: str
    name: str
    parents: tuple[str, ...]
    mime_type: str


class FakeDriveFiles:
    def __init__(self, parent: FakeDrive) -> None:
        self.parent = parent

    def list(self, **kwargs: Any) -> FakeRequest:
        self.parent.list_calls.append(kwargs)
        q = kwargs["q"]
        parent = q.split("'")[3]
        name = q.split("'")[1].replace("\\'", "'")
        return FakeRequest({"files": [f.__dict__ | {"mimeType": f.mime_type} for f in self.parent.file_records if f.name == name and parent in f.parents]})

    def create(self, **kwargs: Any) -> FakeRequest:
        self.parent.create_calls.append(kwargs)
        metadata = kwargs["body"]
        file = FakeFile(
            str(len(self.parent.file_records) + 1),
            metadata["name"],
            tuple(metadata["parents"]),
            metadata.get("mimeType", "application/octet-stream"),
        )
        self.parent.file_records.append(file)
        return FakeRequest({"id": file.id})

    def update(self, **kwargs: Any) -> FakeRequest:
        self.parent.update_calls.append(kwargs)
        return FakeRequest({"id": kwargs["fileId"]})


class FakeDrive:
    def __init__(self) -> None:
        self.file_records: list[FakeFile] = []
        self.list_calls: list[dict[str, Any]] = []
        self.create_calls: list[dict[str, Any]] = []
        self.update_calls: list[dict[str, Any]] = []

    def files(self) -> FakeDriveFiles:
        return FakeDriveFiles(self)


def test_drive_creates_approved_hierarchy_and_updates_existing_files(tmp_path: Path) -> None:
    artifact = tmp_path / "req-1_run-1"
    artifact.mkdir()
    for name in ("report.md", "candidates.csv", "run-data.json"):
        (artifact / name).write_text(name, encoding="utf-8")
    evidence = artifact / "evidence"
    evidence.mkdir()
    (evidence / "source.txt").write_text("source", encoding="utf-8")
    service = FakeDrive()
    backup = DriveBackup(service)
    completed_at = datetime(2026, 9, 22, tzinfo=UTC)

    backup.upload_run("root", "req-1", "run-1", completed_at, artifact)
    backup.upload_run("root", "req-1", "run-1", completed_at, artifact)

    assert [call["body"]["name"] for call in service.create_calls[:3]] == ["2026", "09", "req-1_run-1"]
    assert len(service.create_calls) == 8
    assert len(service.update_calls) == 4
    assert all("trashed = false" in call["q"] for call in service.list_calls)


def test_sheets_import_request_cli_saves_the_validated_request(
    tmp_path: Path, monkeypatch: Any
) -> None:
    class FakeProjection:
        def __init__(self, service: object) -> None:
            del service

        def import_request(self, spreadsheet_id: str, row: int) -> SearchRequest:
            assert (spreadsheet_id, row) == ("sheet-1", 2)
            return _request()

    class FakeStore:
        def load(self) -> object:
            return object()

    monkeypatch.setattr("mybudongsan.cli.GoogleCredentialStore", FakeStore)
    monkeypatch.setattr("mybudongsan.cli.create_sheets_service", lambda store: object())
    monkeypatch.setattr("mybudongsan.cli.SheetsProjection", FakeProjection)
    data_dir = tmp_path / "data"
    runner = CliRunner()
    assert runner.invoke(app, ["--data-dir", str(data_dir), "db", "upgrade"]).exit_code == 0

    result = runner.invoke(
        app,
        ["--data-dir", str(data_dir), "sheets", "import-request", "--spreadsheet-id", "sheet-1", "--row", "2"],
    )

    assert result.exit_code == 0, result.output
    assert RequestRepository(Database(f"sqlite+pysqlite:///{data_dir / 'mybudongsan.sqlite3'}")).get_version("req-1", 1) == _request()


def test_google_api_errors_are_redacted_before_they_reach_cli_output(tmp_path: Path) -> None:
    class FailingSheets:
        def spreadsheets(self) -> FailingSheets:
            return self

        def values(self) -> FailingSheets:
            return self

        def get(self, **kwargs: object) -> FakeRequest:
            del kwargs
            raise RuntimeError("access_token=exposed")

    class FailingDrive:
        def files(self) -> FailingDrive:
            return self

        def list(self, **kwargs: object) -> FakeRequest:
            del kwargs
            raise RuntimeError("refresh_token=exposed")

    with pytest.raises(RuntimeError) as sheets_error:
        SheetsProjection(FailingSheets()).import_request("sheet", 2)
    with pytest.raises(RuntimeError) as drive_error:
        DriveBackup(FailingDrive()).upload_run(
            "root", "req", "run", datetime(2026, 9, 22, tzinfo=UTC), tmp_path
        )

    assert "exposed" not in str(sheets_error.value)
    assert "exposed" not in str(drive_error.value)
