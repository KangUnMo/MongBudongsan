from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest
from googleapiclient.http import MediaInMemoryUpload  # type: ignore[import-untyped]

from mybudongsan.integrations.drive import create_drive_service
from mybudongsan.integrations.gmail import GmailNotifier, create_gmail_service
from mybudongsan.integrations.google_auth import GoogleCredentialStore
from mybudongsan.integrations.sheets import create_sheets_service
from mybudongsan.notifications.policy import NotificationEventType

_REQUIRED_ENV = (
    "MYBUDONGSAN_LIVE_TEST",
    "MYBUDONGSAN_TEST_SPREADSHEET_ID",
    "MYBUDONGSAN_TEST_DRIVE_FOLDER_ID",
    "MYBUDONGSAN_TEST_EMAIL",
)


@pytest.mark.live
def test_user_authorized_google_smoke_creates_only_named_cleanup_targets() -> None:
    missing = [name for name in _REQUIRED_ENV if not os.environ.get(name)]
    if missing or os.environ.get("MYBUDONGSAN_LIVE_TEST") != "1":
        pytest.skip("live smoke requires all four explicit MYBUDONGSAN live-test variables")

    spreadsheet_id = os.environ["MYBUDONGSAN_TEST_SPREADSHEET_ID"]
    drive_folder_id = os.environ["MYBUDONGSAN_TEST_DRIVE_FOLDER_ID"]
    recipient = os.environ["MYBUDONGSAN_TEST_EMAIL"]
    prefix = f"MYBUDONGSAN_LIVE_TEST_{uuid.uuid4().hex[:12]}"
    created_at = datetime.now(UTC).isoformat()
    store = GoogleCredentialStore()

    sheets = create_sheets_service(store)
    row_response = (
        sheets.spreadsheets()
        .values()
        .append(
            spreadsheetId=spreadsheet_id,
            range="'검색 요청'!A:J",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={
                "values": [
                    [
                        prefix,
                        "1",
                        '[{"name":"LIVE TEST ONLY","allow_expansion":false}]',
                        "1",
                        "2",
                        "LIVE TEST ONLY",
                        "",
                        "",
                        created_at,
                        "approved",
                    ]
                ]
            },
        )
        .execute()
    )
    updated_range = row_response.get("updates", {}).get("updatedRange", "unknown")
    print(f"cleanup_sheet_range={updated_range}")

    drive = create_drive_service(store)
    drive_response = (
        drive.files()
        .create(
            body={
                "name": f"{prefix}.txt",
                "parents": [drive_folder_id],
                "mimeType": "text/plain",
            },
            media_body=MediaInMemoryUpload(
                f"{prefix}\ncreated_at={created_at}\n".encode(),
                mimetype="text/plain",
                resumable=False,
            ),
            fields="id,name",
        )
        .execute()
    )
    drive_file_id = str(drive_response["id"])
    print(f"cleanup_drive_file_id={drive_file_id}")

    gmail = GmailNotifier(create_gmail_service(store), recipient=recipient)
    message_id = gmail.send_terminal(
        run_id=prefix,
        event_type=NotificationEventType.COMPLETED,
        outcome="LIVE TEST",
        conclusion="사용자 승인 Google smoke test; 이 메일은 삭제해도 됩니다.",
    )
    print(f"cleanup_gmail_message_id={message_id}")
