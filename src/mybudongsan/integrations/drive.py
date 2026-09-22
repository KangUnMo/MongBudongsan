from __future__ import annotations

import mimetypes
from datetime import datetime
from pathlib import Path
from typing import Any

from googleapiclient.discovery import build  # type: ignore[import-untyped]
from googleapiclient.http import MediaFileUpload  # type: ignore[import-untyped]

from mybudongsan.integrations.google_auth import GoogleCredentialStore

_FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"


class DriveBackup:
    """Idempotently mirror one approved local report artifact directory to Drive."""

    def __init__(self, service: Any) -> None:
        self._service = service

    def upload_run(
        self,
        root_folder_id: str,
        request_id: str,
        run_id: str,
        completed_at: datetime,
        artifact_directory: Path,
    ) -> None:
        try:
            if not artifact_directory.is_dir():
                raise FileNotFoundError(f"report artifact directory was not found: {artifact_directory}")
            if not request_id or not run_id:
                raise ValueError("request_id and run_id are required")
            year = self._ensure_folder(root_folder_id, f"{completed_at.year:04d}")
            month = self._ensure_folder(year, f"{completed_at.month:02d}")
            request_folder = self._ensure_folder(month, artifact_directory.name)
            folders: dict[Path, str] = {artifact_directory: request_folder}
            for path in sorted(artifact_directory.rglob("*")):
                relative = path.relative_to(artifact_directory)
                if path.is_dir():
                    parent_id = folders[path.parent]
                    folders[path] = self._ensure_folder(parent_id, relative.name)
                elif path.is_file():
                    parent_id = folders[path.parent]
                    self._upsert_file(parent_id, relative.name, path)
        except Exception as error:  # noqa: BLE001 - redact API errors before CLI output
            raise RuntimeError(GoogleCredentialStore.redact_error(error)) from None

    def _ensure_folder(self, parent_id: str, name: str) -> str:
        existing = self._find(parent_id, name)
        if existing is not None:
            return existing
        response = self._service.files().create(
            body={"name": name, "mimeType": _FOLDER_MIME_TYPE, "parents": [parent_id]},
            fields="id",
        ).execute()
        return str(response["id"])

    def _upsert_file(self, parent_id: str, name: str, path: Path) -> None:
        media = MediaFileUpload(str(path), mimetype=mimetypes.guess_type(path.name)[0], resumable=False)
        existing = self._find(parent_id, name)
        if existing is None:
            self._service.files().create(
                body={"name": name, "parents": [parent_id]}, media_body=media, fields="id"
            ).execute()
            return
        self._service.files().update(fileId=existing, media_body=media, fields="id").execute()

    def _find(self, parent_id: str, name: str) -> str | None:
        page_token: str | None = None
        while True:
            response = self._service.files().list(
                q=(f"name = '{_escape_query(name)}' and '{_escape_query(parent_id)}' in parents "
                   "and trashed = false"),
                spaces="drive", fields="nextPageToken, files(id,name,mimeType)", pageToken=page_token,
            ).execute()
            files = response.get("files", [])
            if files:
                return str(files[0]["id"])
            page_token = response.get("nextPageToken")
            if not page_token:
                return None


def create_drive_service(store: GoogleCredentialStore) -> Any:
    return build("drive", "v3", credentials=store.load(), cache_discovery=False)


def _escape_query(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")
