from __future__ import annotations

import hashlib
import mimetypes
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from googleapiclient.discovery import build  # type: ignore[import-untyped]
from googleapiclient.http import MediaFileUpload  # type: ignore[import-untyped]

from mybudongsan.integrations.google_auth import (
    GoogleCredentialStore,
    sanitized_google_error,
)

_FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
_REQUIRED_ARTIFACTS = ("report.md", "candidates.csv", "run-data.json")


class DriveBackup:
    """Idempotently mirror one approved local report artifact directory to Drive."""

    def __init__(self, service: Any, *, lock_directory: Path | None = None) -> None:
        self._service = service
        self._lock_directory = lock_directory

    def upload_run(
        self,
        root_folder_id: str,
        request_id: str,
        run_id: str,
        completed_at: datetime,
        artifact_directory: Path,
    ) -> None:
        try:
            planned_files = self._planned_files(artifact_directory)
            if not request_id or not run_id:
                raise ValueError("request_id and run_id are required")
            with self._root_lock(root_folder_id, artifact_directory):
                year = self._ensure_folder(root_folder_id, f"{completed_at.year:04d}")
                month = self._ensure_folder(year, f"{completed_at.month:02d}")
                request_folder = self._ensure_folder(month, artifact_directory.name)
                folders: dict[Path, str] = {artifact_directory: request_folder}
                for path in planned_files:
                    parent_id = self._ensure_parent_folders(folders, artifact_directory, path)
                    self._upsert_file(parent_id, path.name, path)
        except Exception as error:  # noqa: BLE001 - an API boundary must never leak values
            raise sanitized_google_error(error) from None

    def lock_path_for(self, root_folder_id: str) -> Path:
        directory = self._lock_directory
        if directory is None:
            raise RuntimeError("Drive upload lock directory is required")
        digest = hashlib.sha256(root_folder_id.encode("utf-8")).hexdigest()
        return directory / f".mybudongsan-drive-{digest}.lock"

    def _planned_files(self, artifact_directory: Path) -> tuple[Path, ...]:
        if not artifact_directory.is_dir() or artifact_directory.is_symlink():
            raise FileNotFoundError(f"report artifact directory was not found: {artifact_directory}")
        required: list[Path] = []
        for name in _REQUIRED_ARTIFACTS:
            path = artifact_directory / name
            if not path.is_file() or path.is_symlink():
                raise ValueError(f"필수 보고서 아티팩트를 찾을 수 없습니다: {name}")
            required.append(path)
        evidence_root = artifact_directory / "evidence"
        evidence_files = () if not evidence_root.is_dir() or evidence_root.is_symlink() else tuple(
            path
            for path in sorted(evidence_root.rglob("*"))
            if path.is_file()
            and not path.is_symlink()
            and not any(part.startswith(".") for part in path.relative_to(artifact_directory).parts)
        )
        return (*required, *evidence_files)

    def _ensure_parent_folders(
        self, folders: dict[Path, str], artifact_directory: Path, path: Path
    ) -> str:
        parent = artifact_directory
        for part in path.relative_to(artifact_directory).parent.parts:
            child = parent / part
            if child not in folders:
                folders[child] = self._ensure_folder(folders[parent], part)
            parent = child
        return folders[parent]

    @contextmanager
    def _root_lock(self, root_folder_id: str, artifact_directory: Path) -> Iterator[None]:
        if self._lock_directory is None:
            self._lock_directory = artifact_directory.parent
        lock_path = self.lock_path_for(root_folder_id)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as error:
            raise RuntimeError("Drive 업로드 잠금이 이미 실행 중입니다") from error
        try:
            yield
        finally:
            os.close(descriptor)
            lock_path.unlink(missing_ok=True)

    def _ensure_folder(self, parent_id: str, name: str) -> str:
        existing = self._find(parent_id, name, _FOLDER_MIME_TYPE)
        if existing is not None:
            return existing
        response = self._service.files().create(
            body={"name": name, "mimeType": _FOLDER_MIME_TYPE, "parents": [parent_id]},
            fields="id",
        ).execute()
        return str(response["id"])

    def _upsert_file(self, parent_id: str, name: str, path: Path) -> None:
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        media = MediaFileUpload(str(path), mimetype=mime_type, resumable=False)
        existing = self._find(parent_id, name, mime_type)
        if existing is None:
            self._service.files().create(
                body={"name": name, "mimeType": mime_type, "parents": [parent_id]},
                media_body=media,
                fields="id",
            ).execute()
            return
        self._service.files().update(
            fileId=existing, body={"name": name, "mimeType": mime_type}, media_body=media, fields="id"
        ).execute()

    def _find(self, parent_id: str, name: str, expected_mime_type: str) -> str | None:
        page_token: str | None = None
        while True:
            response = self._service.files().list(
                q=(f"name = '{_escape_query(name)}' and '{_escape_query(parent_id)}' in parents "
                   f"and mimeType = '{_escape_query(expected_mime_type)}' and trashed = false"),
                spaces="drive", fields="nextPageToken, files(id,name,mimeType,parents)", pageToken=page_token,
            ).execute()
            files = response.get("files", [])
            if not isinstance(files, list):
                files = []
            for item in files:
                parents = item.get("parents") if isinstance(item, dict) else None
                if (
                    isinstance(item, dict)
                    and item.get("name") == name
                    and item.get("mimeType") == expected_mime_type
                    and isinstance(parents, (list, tuple))
                    and parent_id in parents
                    and isinstance(item.get("id"), str)
                ):
                    return str(item["id"])
            next_page_token = response.get("nextPageToken")
            page_token = next_page_token if isinstance(next_page_token, str) else None
            if not page_token:
                return None


def create_drive_service(store: GoogleCredentialStore) -> Any:
    try:
        return build("drive", "v3", credentials=store.load(), cache_discovery=False)
    except Exception as error:  # noqa: BLE001 - service construction is an integration boundary
        raise sanitized_google_error(error) from None


def _escape_query(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")
