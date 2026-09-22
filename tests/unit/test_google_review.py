from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from mybudongsan.cli import app
from mybudongsan.domain.requests import SearchRequest
from mybudongsan.integrations.drive import DriveBackup
from mybudongsan.integrations.sheets import SheetsProjection


class _Response:
    def __init__(self, payload: Any) -> None:
        self.payload = payload

    def execute(self) -> Any:
        return self.payload


class _ManagedSheets:
    def __init__(self, *, tabs: list[str] | None = None) -> None:
        self.tabs = tabs or ["검색 요청", "조사 현황", "추천 결과", "관심 매물"]
        self.status_keys: list[list[str]] = []
        self.candidate_keys: list[list[str]] = []
        self.get_ranges: list[str] = []
        self.clears: list[str] = []
        self.value_updates: list[dict[str, Any]] = []
        self.tab_updates: list[dict[str, Any]] = []
        self.metadata_reads = 0

    def spreadsheets(self) -> _ManagedSheets:
        return self

    def values(self) -> _ManagedSheets:
        return self

    def get(self, **kwargs: Any) -> _Response:
        if "fields" in kwargs:
            self.metadata_reads += 1
            return _Response({"sheets": [{"properties": {"title": title}} for title in self.tabs]})
        sheet_range = kwargs["range"]
        self.get_ranges.append(sheet_range)
        if sheet_range == "'조사 현황'!A2:A1000":
            return _Response({"values": self.status_keys})
        if sheet_range == "'추천 결과'!A2:B1000":
            return _Response({"values": self.candidate_keys})
        raise AssertionError(f"unexpected read: {sheet_range}")

    def batchUpdate(self, **kwargs: Any) -> _Response:
        body = kwargs["body"]
        if "requests" in body:
            self.tab_updates.append(kwargs)
            return _Response({})
        self.value_updates.append(kwargs)
        return _Response({})

    def clear(self, **kwargs: Any) -> _Response:
        self.clears.append(kwargs["range"])
        return _Response({})


def _request() -> SearchRequest:
    return SearchRequest.model_validate(
        {
            "request_id": "req-1",
            "version": 1,
            "regions": [{"name": "서울"}],
            "budget": {"minimum": 1, "maximum": 2},
            "status": "approved",
        }
    )


def _run(run_id: str) -> dict[str, object]:
    return {"run_id": run_id, "status": "completed", "stage": "report_complete", "completed_at": "now"}


def test_sheets_preserves_other_runs_and_clears_a_smaller_resync_tail() -> None:
    service = _ManagedSheets()
    projection = SheetsProjection(service)
    projection.sync_run("sheet", _request(), _run("run-1"), [
        {"candidate_id": "one"}, {"candidate_id": "two"}, {"candidate_id": "three"}
    ])
    service.status_keys = [["run-1"], ["run-2"]]
    service.candidate_keys = [
        ["run-1", "one"], ["run-1", "two"], ["run-1", "three"], ["run-2", "other"]
    ]
    projection.sync_run("sheet", _request(), _run("run-1"), [{"candidate_id": "one"}])

    assert service.get_ranges[-4:] == [
        "'조사 현황'!A2:A1000",
        "'추천 결과'!A2:B1000",
        "'조사 현황'!A2:A1000",
        "'추천 결과'!A2:B1000",
    ]
    assert service.clears == ["'추천 결과'!A2:K4"]
    all_ranges = [
        entry["range"]
        for update in service.value_updates
        for entry in update["body"]["data"]
    ]
    assert "'조사 현황'!A2:F2" in all_ranges
    assert "'추천 결과'!A2:K2" in all_ranges
    assert all(sheet_range.startswith(("'검색 요청'!A", "'조사 현황'!A", "'추천 결과'!A", "'관심 매물'!A")) for sheet_range in all_ranges)


def test_sheets_refetches_metadata_after_a_missing_tab_race() -> None:
    class RaceSheets(_ManagedSheets):
        def __init__(self) -> None:
            super().__init__(tabs=["검색 요청"])

        def get(self, **kwargs: Any) -> _Response:
            if "fields" in kwargs and self.metadata_reads == 1:
                self.tabs = ["검색 요청", "조사 현황", "추천 결과", "관심 매물"]
            return super().get(**kwargs)

        def batchUpdate(self, **kwargs: Any) -> _Response:
            if "requests" in kwargs["body"]:
                self.metadata_reads = 1
                raise RuntimeError("already exists")
            return super().batchUpdate(**kwargs)

    service = RaceSheets()

    SheetsProjection(service).sync_run("sheet", _request(), _run("run-1"), [])

    assert service.metadata_reads == 2


class _DriveFiles:
    def __init__(self, parent: _Drive) -> None:
        self.parent = parent

    def list(self, **kwargs: Any) -> _Response:
        self.parent.list_calls.append(kwargs)
        return _Response({"files": self.parent.matches.pop(0) if self.parent.matches else []})

    def create(self, **kwargs: Any) -> _Response:
        self.parent.create_calls.append(kwargs)
        return _Response({"id": str(len(self.parent.create_calls))})

    def update(self, **kwargs: Any) -> _Response:
        self.parent.update_calls.append(kwargs)
        return _Response({"id": kwargs["fileId"]})


class _Drive:
    def __init__(self, matches: list[list[dict[str, object]]] | None = None) -> None:
        self.matches = list(matches or [])
        self.list_calls: list[dict[str, Any]] = []
        self.create_calls: list[dict[str, Any]] = []
        self.update_calls: list[dict[str, Any]] = []

    def files(self) -> _DriveFiles:
        return _DriveFiles(self)


def _artifact(tmp_path: Path) -> Path:
    artifact = tmp_path / "req-1_run-1"
    artifact.mkdir()
    for name in ("report.md", "candidates.csv", "run-data.json"):
        (artifact / name).write_text(name, encoding="utf-8")
    evidence = artifact / "evidence"
    evidence.mkdir()
    (evidence / "source.txt").write_text("source", encoding="utf-8")
    (artifact / "ignored.txt").write_text("ignore", encoding="utf-8")
    (evidence / ".DS_Store").write_text("ignore", encoding="utf-8")
    return artifact


def test_drive_requires_allowlisted_artifacts_and_matches_mime_types(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path)
    service = _Drive()
    backup = DriveBackup(service, lock_directory=tmp_path / "locks")

    backup.upload_run("root", "req", "run", datetime(2026, 9, 22, tzinfo=UTC), artifact)

    names = [call["body"]["name"] for call in service.create_calls]
    assert "ignored.txt" not in names
    assert ".DS_Store" not in names
    assert {"report.md", "candidates.csv", "run-data.json", "source.txt"}.issubset(names)
    assert all("mimeType =" in call["q"] for call in service.list_calls)
    assert all("mimeType" in call["body"] for call in service.create_calls)
    (artifact / "report.md").unlink()
    with pytest.raises(RuntimeError, match="필수"):
        backup.upload_run("root", "req", "run", datetime(2026, 9, 22, tzinfo=UTC), artifact)


def test_drive_lock_prevents_api_mutation_and_cleans_up_after_release(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path)
    service = _Drive()
    backup = DriveBackup(service, lock_directory=tmp_path / "locks")
    lock_path = backup.lock_path_for("root")
    lock_path.parent.mkdir(parents=True)
    descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with pytest.raises(RuntimeError, match="잠금"):
            backup.upload_run("root", "req", "run", datetime(2026, 9, 22, tzinfo=UTC), artifact)
        assert not service.list_calls and not service.create_calls
    finally:
        os.close(descriptor)
        lock_path.unlink()

    backup.upload_run("root", "req", "run", datetime(2026, 9, 22, tzinfo=UTC), artifact)
    assert service.create_calls
    assert not lock_path.exists()


@pytest.mark.parametrize(
    "unsafe_message, secret",
    [
        ('{"access_token": "json-secret"}', "json-secret"),
        ("{'refresh_token': 'dict-secret'}", "dict-secret"),
        ("client_secret=equals-secret", "equals-secret"),
        ("id_token?=query-secret", "query-secret"),
        ("Bearer bearer-secret access_token token-secret", "bearer-secret"),
    ],
)
def test_debug_cli_never_reveals_google_service_build_secrets(
    monkeypatch: pytest.MonkeyPatch, unsafe_message: str, secret: str
) -> None:
    class FakeStore:
        def load(self) -> object:
            return object()

    def fail_build(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise RuntimeError(unsafe_message)

    monkeypatch.setattr("mybudongsan.cli.GoogleCredentialStore", FakeStore)
    monkeypatch.setattr("mybudongsan.integrations.sheets.build", fail_build)
    result = CliRunner().invoke(
        app,
        ["--debug", "sheets", "import-request", "--spreadsheet-id", "sheet", "--row", "2"],
    )

    assert result.exit_code != 0
    assert secret not in result.output
    assert secret not in repr(result.exception)
