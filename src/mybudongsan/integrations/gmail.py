from __future__ import annotations

import base64
from email.message import EmailMessage
from typing import Any

from googleapiclient.discovery import build  # type: ignore[import-untyped]

from mybudongsan.integrations.google_auth import (
    GoogleCredentialStore,
    sanitized_google_error,
)
from mybudongsan.notifications.policy import (
    TERMINAL_EVENT_TYPES,
    NotificationEventType,
)


class GmailNotifier:
    """Send one plain-text terminal summary through an injected Gmail service."""

    def __init__(self, service: Any, *, recipient: str) -> None:
        if not recipient.strip():
            raise ValueError("Gmail recipient is required")
        self._service = service
        self._recipient = recipient.strip()

    def send_terminal(
        self,
        *,
        run_id: str,
        event_type: NotificationEventType,
        outcome: str,
        conclusion: str,
        drive_report_link: str | None = None,
    ) -> str:
        if event_type not in TERMINAL_EVENT_TYPES:
            raise ValueError("Gmail delivery accepts terminal notification events only")
        message = EmailMessage()
        message["To"] = self._recipient
        message["Subject"] = f"[MyBudongsan] 조사 {outcome} - {run_id}"
        lines = [
            f"실행 ID: {run_id}",
            f"결과: {outcome}",
            f"결론: {conclusion}",
        ]
        if drive_report_link:
            lines.append(f"Drive 보고서: {drive_report_link}")
        message.set_content("\n".join(lines), charset="utf-8")
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii").rstrip("=")
        try:
            response = (
                self._service.users()
                .messages()
                .send(userId="me", body={"raw": raw})
                .execute()
            )
            provider_id = response.get("id") if isinstance(response, dict) else None
            if not isinstance(provider_id, str) or not provider_id:
                raise RuntimeError("Gmail provider did not return a message ID")
            return provider_id
        except Exception as error:  # noqa: BLE001 - provider errors must be sanitized
            raise sanitized_google_error(error) from None


def create_gmail_service(store: GoogleCredentialStore) -> Any:
    try:
        return build("gmail", "v1", credentials=store.load(), cache_discovery=False)
    except Exception as error:  # noqa: BLE001 - service construction is an integration boundary
        raise sanitized_google_error(error) from None
