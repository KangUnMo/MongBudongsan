from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from googleapiclient.discovery import build  # type: ignore[import-untyped]

from mybudongsan.domain.requests import SearchRequest
from mybudongsan.integrations.google_auth import (
    GoogleCredentialStore,
    sanitized_google_error,
)

SHEET_TITLES = ("검색 요청", "조사 현황", "추천 결과", "관심 매물")
_REQUEST_HEADERS = (
    "request_id", "version", "regions", "budget_minimum", "budget_maximum", "required",
    "preferred", "excluded", "special_questions", "status",
)
_STATUS_HEADERS = ("run_id", "request_id", "request_version", "status", "stage", "completed_at")
_CANDIDATE_HEADERS = (
    "run_id", "candidate_id", "complex_name", "address", "asking_price", "status",
    "total_score", "confidence", "recommendable", "scenario", "evidence_ids",
)
_INTEREST_HEADERS = ("candidate_id", "complex_name", "note", "status")
_STATUS_KEY_RANGE = "'조사 현황'!A2:A"
_CANDIDATE_KEY_RANGE = "'추천 결과'!A2:B"


class SheetsProjection:
    """One-way, explicit-range projection of canonical local request and run data."""

    def __init__(self, service: Any) -> None:
        self._service = service

    def import_request(self, spreadsheet_id: str, row: int) -> SearchRequest:
        try:
            if row < 2:
                raise ValueError("검색 요청 import row must be at least 2")
            response = self._service.spreadsheets().values().get(
                spreadsheetId=spreadsheet_id,
                range=f"'검색 요청'!A{row}:J{row}",
            ).execute()
            values = response.get("values", [])
            if len(values) != 1:
                raise ValueError("검색 요청 row was not found")
            return _request_from_row(values[0])
        except Exception as error:  # noqa: BLE001 - an API boundary must never leak values
            raise sanitized_google_error(error) from None

    def sync_run(
        self,
        spreadsheet_id: str,
        request: SearchRequest,
        run: Mapping[str, object],
        candidates: Sequence[Mapping[str, object]],
    ) -> None:
        try:
            self._ensure_tabs(spreadsheet_id)
            run_id = _required_text(run, "run_id")
            status_row = _find_key_row(self._read_key_rows(spreadsheet_id, _STATUS_KEY_RANGE), run_id)
            candidate_keys = self._read_key_rows(spreadsheet_id, _CANDIDATE_KEY_RANGE)
            candidate_rows = self._candidate_rows(run_id, candidates, candidate_keys)
            self._clear_prior_candidates(spreadsheet_id, candidate_keys, run_id)
            status_values = [_STATUS_HEADERS]
            status_data = [
                run_id, request.request_id, request.version, _string(run.get("status")),
                _string(run.get("stage")), _string(run.get("completed_at")),
            ]
            values = [
                {"range": "'검색 요청'!A1:J1", "values": [_REQUEST_HEADERS]},
                {"range": "'조사 현황'!A1:F1", "values": status_values},
                {"range": f"'조사 현황'!A{status_row}:F{status_row}", "values": [status_data]},
                {"range": "'추천 결과'!A1:K1", "values": [_CANDIDATE_HEADERS]},
                {"range": "'관심 매물'!A1:D1", "values": [_INTEREST_HEADERS]},
            ]
            values.extend(
                {"range": f"'추천 결과'!A{row}:K{row}", "values": [candidate_row]}
                for row, candidate_row in candidate_rows
            )
            self._service.spreadsheets().values().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={"valueInputOption": "RAW", "data": values},
            ).execute()
        except Exception as error:  # noqa: BLE001 - an API boundary must never leak values
            raise sanitized_google_error(error) from None

    def _ensure_tabs(self, spreadsheet_id: str) -> None:
        response = self._service.spreadsheets().get(
            spreadsheetId=spreadsheet_id,
            fields="sheets.properties(sheetId,title)",
        ).execute()
        existing = _tab_titles(response)
        missing = [title for title in SHEET_TITLES if title not in existing]
        if not missing:
            return
        try:
            self._add_tabs(spreadsheet_id, missing)
        except Exception:  # noqa: BLE001 - recover only a single concurrent AddSheet conflict
            retry = self._service.spreadsheets().get(
                spreadsheetId=spreadsheet_id,
                fields="sheets.properties(sheetId,title)",
            ).execute()
            still_missing = [title for title in SHEET_TITLES if title not in _tab_titles(retry)]
            if still_missing:
                self._add_tabs(spreadsheet_id, still_missing)

    def _add_tabs(self, spreadsheet_id: str, titles: Sequence[str]) -> None:
        self._service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": title}}} for title in titles]},
        ).execute()

    def _read_key_rows(self, spreadsheet_id: str, sheet_range: str) -> list[list[str]]:
        response = self._service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id, range=sheet_range
        ).execute()
        raw_values = response.get("values", [])
        return [[str(value) for value in row] for row in raw_values if isinstance(row, list)]

    def _clear_prior_candidates(
        self, spreadsheet_id: str, candidate_keys: Sequence[Sequence[str]], run_id: str
    ) -> None:
        rows = [index + 2 for index, key in enumerate(candidate_keys) if key and key[0] == run_id]
        for start, end in _consecutive_ranges(rows):
            self._service.spreadsheets().values().clear(
                spreadsheetId=spreadsheet_id, range=f"'추천 결과'!A{start}:K{end}", body={}
            ).execute()

    @staticmethod
    def _candidate_rows(
        run_id: str,
        candidates: Sequence[Mapping[str, object]],
        candidate_keys: Sequence[Sequence[str]],
    ) -> list[tuple[int, list[str]]]:
        current_rows = {
            key[1]: index + 2
            for index, key in enumerate(candidate_keys)
            if len(key) >= 2 and key[0] == run_id
        }
        reusable_rows = [
            index + 2 for index, key in enumerate(candidate_keys) if key and key[0] == run_id
        ]
        next_row = len(candidate_keys) + 2
        chosen: set[int] = set()
        rows: list[tuple[int, list[str]]] = []
        for candidate in candidates:
            candidate_id = _required_text(candidate, "candidate_id")
            row = current_rows.get(candidate_id)
            if row is None or row in chosen:
                row = next((item for item in reusable_rows if item not in chosen), next_row)
                if row == next_row:
                    next_row += 1
            chosen.add(row)
            rows.append((row, [run_id, *_candidate_row(candidate)]))
        return rows


def _tab_titles(response: Mapping[str, object]) -> set[str]:
    sheets = response.get("sheets", [])
    if not isinstance(sheets, list):
        return set()
    return {
        properties["title"]
        for sheet in sheets
        if isinstance(sheet, dict)
        for properties in [sheet.get("properties", {})]
        if isinstance(properties, dict) and isinstance(properties.get("title"), str)
    }


def _find_key_row(keys: Sequence[Sequence[str]], key: str) -> int:
    for index, row in enumerate(keys, start=2):
        if row and row[0] == key:
            return index
    return len(keys) + 2


def _consecutive_ranges(rows: Sequence[int]) -> list[tuple[int, int]]:
    if not rows:
        return []
    ranges: list[tuple[int, int]] = []
    start = end = rows[0]
    for row in rows[1:]:
        if row == end + 1:
            end = row
            continue
        ranges.append((start, end))
        start = end = row
    ranges.append((start, end))
    return ranges


def create_sheets_service(store: GoogleCredentialStore) -> Any:
    try:
        return build("sheets", "v4", credentials=store.load(), cache_discovery=False)
    except Exception as error:  # noqa: BLE001 - service construction is an integration boundary
        raise sanitized_google_error(error) from None


def _request_from_row(row: Sequence[object]) -> SearchRequest:
    if len(row) != len(_REQUEST_HEADERS):
        raise ValueError("검색 요청 row must contain exactly 10 columns")
    values = [str(value).strip() for value in row]
    return SearchRequest.model_validate({
        "request_id": values[0], "version": values[1], "regions": json.loads(values[2]),
        "budget": {"minimum": values[3], "maximum": values[4]},
        "required": _split(values[5]), "preferred": _split(values[6]), "excluded": _split(values[7]),
        "special_questions": _split(values[8]), "status": values[9],
    })


def _candidate_row(candidate: Mapping[str, object]) -> list[str]:
    return [_string(candidate.get(header)) for header in _CANDIDATE_HEADERS[1:]]


def _split(value: str) -> list[str]:
    return [item.strip() for item in value.split("|") if item.strip()]


def _required_text(values: Mapping[str, object], name: str) -> str:
    value = _string(values.get(name))
    if not value:
        raise ValueError(f"run {name} is required")
    return value


def _string(value: object | None) -> str:
    return "" if value is None else str(value)
