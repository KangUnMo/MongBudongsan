from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class NotificationEventType(StrEnum):
    WORK_STARTED = "work_started"
    RESEARCHER_ASSIGNED = "researcher_assigned"
    SPECIALIST_ASSIGNED = "specialist_assigned"
    FINALIZING = "finalizing"
    NEEDS_ACTION = "needs_action"
    COMPLETED = "completed"
    FAILED = "failed"


TERMINAL_EVENT_TYPES = frozenset(
    {NotificationEventType.COMPLETED, NotificationEventType.FAILED}
)


@dataclass(frozen=True)
class NotificationEvent:
    type: NotificationEventType
    message: str


class NotificationPolicy:
    """Plan no more than five user-visible lifecycle messages for one run."""

    def plan(
        self,
        *,
        specialist: bool,
        needs_action: bool,
        succeeded: bool = True,
    ) -> tuple[NotificationEvent, ...]:
        events = [
            NotificationEvent(
                NotificationEventType.WORK_STARTED,
                "조사를 시작했습니다.",
            ),
            NotificationEvent(
                NotificationEventType.RESEARCHER_ASSIGNED,
                "조사 담당자를 배정했습니다.",
            ),
        ]
        if specialist:
            events.append(
                NotificationEvent(
                    NotificationEventType.SPECIALIST_ASSIGNED,
                    "전문 검토 담당자를 배정했습니다.",
                )
            )
        events.append(
            NotificationEvent(
                NotificationEventType.NEEDS_ACTION
                if needs_action
                else NotificationEventType.FINALIZING,
                "확인이 필요합니다."
                if needs_action
                else "조사 결과를 마무리하고 있습니다.",
            )
        )
        events.append(
            NotificationEvent(
                NotificationEventType.COMPLETED
                if succeeded
                else NotificationEventType.FAILED,
                "조사가 완료되었습니다."
                if succeeded
                else "조사에 실패했습니다.",
            )
        )
        return tuple(events)


def plan_events(
    *,
    specialist: bool,
    needs_action: bool,
    succeeded: bool = True,
) -> tuple[NotificationEvent, ...]:
    return NotificationPolicy().plan(
        specialist=specialist,
        needs_action=needs_action,
        succeeded=succeeded,
    )
