from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from googleapiclient.discovery import build  # type: ignore[import-untyped]

from mybudongsan.domain.requests import SearchRequest
from mybudongsan.integrations.google_auth import GoogleCredentialStore

SHEET_TITLES = ("검색 요청", "조사 현황", "추천 결과", "관심 매물")
_REQUEST_HEADERS = (
    "request_id", "version", "regions", "budget_minimum", "budget_maximum", "required",
    "preferred", "excluded", "special_questions", "status",
)
_STATUS_HEADERS = ("run_id", "request_id", "request_version", "status", "stage", "completed_at")
_CANDIDATE_HEADERS = (
    "candidate_id", "complex_name", "address", "asking_price", "status", "total_score",
    "confidence", "recommendable", "scenario", "evidence_ids",
)
_INTEREST_HEADERS = ("candidate_id", "complex_name", "note", "status")


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
        except Exception as error:  # noqa: BLE001 - redact API errors before CLI output
            raise RuntimeError(GoogleCredentialStore.redact_error(error)) from None

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
            request_values = [_REQUEST_HEADERS, _request_to_row(request)]
            status_values = [_STATUS_HEADERS, [
                run_id, request.request_id, request.version, _string(run.get("status")),
                _string(run.get("stage")), _string(run.get("completed_at")),
            ]]
            candidate_rows = [_candidate_row(candidate) for candidate in candidates]
            values = [
                {"range": "'검색 요청'!A1:J2", "values": request_values},
                {"range": "'조사 현황'!A1:F2", "values": status_values},
                {"range": f"'추천 결과'!A1:J{max(2, len(candidate_rows) + 1)}", "values": [_CANDIDATE_HEADERS, *candidate_rows]},
                {"range": "'관심 매물'!A1:D1", "values": [_INTEREST_HEADERS]},
            ]
            self._service.spreadsheets().values().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={"valueInputOption": "RAW", "data": values},
            ).execute()
        except Exception as error:  # noqa: BLE001 - redact API errors before CLI output
            raise RuntimeError(GoogleCredentialStore.redact_error(error)) from None

    def _ensure_tabs(self, spreadsheet_id: str) -> None:
        response = self._service.spreadsheets().get(
            spreadsheetId=spreadsheet_id,
            fields="sheets.properties(sheetId,title)",
        ).execute()
        existing = {
            properties["title"]
            for sheet in response.get("sheets", [])
            if isinstance(sheet, dict)
            for properties in [sheet.get("properties", {})]
            if isinstance(properties, dict) and isinstance(properties.get("title"), str)
        }
        missing = [title for title in SHEET_TITLES if title not in existing]
        if missing:
            self._service.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={
                    "requests": [
                        {"addSheet": {"properties": {"title": title}}} for title in missing
                    ]
                },
            ).execute()


def create_sheets_service(store: GoogleCredentialStore) -> Any:
    return build("sheets", "v4", credentials=store.load(), cache_discovery=False)


def _request_to_row(request: SearchRequest) -> list[object]:
    return [
        request.request_id, request.version,
        json.dumps([item.model_dump(mode="json") for item in request.regions], ensure_ascii=False),
        str(request.budget.minimum), str(request.budget.maximum),
        " | ".join(request.required), " | ".join(request.preferred), " | ".join(request.excluded),
        " | ".join(request.special_questions), request.status.value,
    ]


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
    return [_string(candidate.get(header)) for header in _CANDIDATE_HEADERS]


def _split(value: str) -> list[str]:
    return [item.strip() for item in value.split("|") if item.strip()]


def _required_text(values: Mapping[str, object], name: str) -> str:
    value = _string(values.get(name))
    if not value:
        raise ValueError(f"run {name} is required")
    return value


def _string(value: object | None) -> str:
    return "" if value is None else str(value)
