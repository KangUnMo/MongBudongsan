from __future__ import annotations

from mybudongsan.notifications.policy import NotificationEvent
from mybudongsan.storage.database import Database
from mybudongsan.storage.repositories import NotificationRecord, NotificationRepository


class NotificationOutbox:
    """Durable interface used by the workflow and the future PlayMCP PM skill."""

    def __init__(self, database: Database) -> None:
        self._repository = NotificationRepository(database)

    def enqueue(
        self,
        run_id: str,
        event: NotificationEvent,
    ) -> tuple[NotificationRecord, ...]:
        return self._repository.enqueue(run_id, event)

    def pending(self, run_id: str, *, channel: str) -> tuple[NotificationRecord, ...]:
        return self._repository.pending(run_id, channel=channel)

    def mark_sent(self, event_id: int, provider_message_id: str) -> NotificationRecord:
        return self._repository.mark_sent(event_id, provider_message_id)

    def mark_failed(self, event_id: int, error: str) -> NotificationRecord:
        return self._repository.mark_failed(event_id, error)
