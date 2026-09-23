from __future__ import annotations

import os
import runpy
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
        if sheet_range == "'조사 현황'!A2:A":
            return _Response({"values": self.status_keys})
        if sheet_range == "'추천 결과'!A2:B":
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


def test_live_smoke_requires_and_prints_a_created_sheet_row_range(
    capsys: pytest.CaptureFixture[str],
) -> None:
    live_test = runpy.run_path(
        str(Path(__file__).parents[1] / "live" / "test_live_research_smoke.py")
    )
    cleanup_sheet_range = live_test["_cleanup_sheet_range"]

    assert cleanup_sheet_range(
        {"updates": {"updatedRange": "'검색 요청'!A42:J42"}}
    ) == "'검색 요청'!A42:J42"
    assert "cleanup_sheet_range='검색 요청'!A42:J42" in capsys.readouterr().out

    with pytest.raises(AssertionError, match="updatedRange"):
        cleanup_sheet_range({"updates": {}})
    assert "cleanup_sheet_range=unknown" in capsys.readouterr().out


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
        "'조사 현황'!A2:A",
        "'추천 결과'!A2:B",
        "'조사 현황'!A2:A",
        "'추천 결과'!A2:B",
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


def test_sheets_recovers_only_tabs_still_missing_after_a_partial_add_race() -> None:
    class PartialRaceSheets(_ManagedSheets):
        def __init__(self) -> None:
            super().__init__(tabs=["검색 요청"])
            self.attempts: list[list[str]] = []

        def batchUpdate(self, **kwargs: Any) -> _Response:
            if "requests" not in kwargs["body"]:
                return super().batchUpdate(**kwargs)
            titles = [request["addSheet"]["properties"]["title"] for request in kwargs["body"]["requests"]]
            self.attempts.append(titles)
            if len(self.attempts) == 1:
                self.tabs.append("조사 현황")
                raise RuntimeError("already exists")
            self.tabs.extend(titles)
            return _Response({})

    service = PartialRaceSheets()

    SheetsProjection(service).sync_run("sheet", _request(), _run("run-1"), [])

    assert service.attempts == [["조사 현황", "추천 결과", "관심 매물"], ["추천 결과", "관심 매물"]]


def test_sheets_preserves_the_user_request_row_and_finds_keys_after_row_1000() -> None:
    service = _ManagedSheets()
    service.status_keys = [["other"] for _ in range(999)] + [["run-late"]]
    service.candidate_keys = [["other", "candidate"] for _ in range(999)] + [["run-late", "late"]]

    SheetsProjection(service).sync_run("sheet", _request(), _run("run-late"), [{"candidate_id": "late"}])

    written = [entry for update in service.value_updates for entry in update["body"]["data"]]
    request_entries = [entry for entry in written if entry["range"].startswith("'검색 요청'")]
    assert len(request_entries) == 1
    assert request_entries[0]["range"] == "'검색 요청'!A1:J1"
    assert list(request_entries[0]["values"][0]) == [
        "request_id", "version", "regions", "budget_minimum", "budget_maximum", "required",
        "preferred", "excluded", "special_questions", "status",
    ]
    assert {"range": "'조사 현황'!A1001:F1001", "values": [["run-late", "req-1", 1, "completed", "report_complete", "now"]]} in written
    candidate_entry = next(entry for entry in written if entry["range"] == "'추천 결과'!A1001:K1001")
    assert candidate_entry["values"][0][:2] == ["run-late", "late"]
    assert len(candidate_entry["values"][0]) == 11
    assert service.clears == ["'추천 결과'!A1001:K1001"]


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
