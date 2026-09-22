from mybudongsan.notifications.policy import NotificationEventType, plan_events


def test_normal_run_emits_four_messages_without_specialist() -> None:
    events = plan_events(specialist=False, needs_action=False)
    assert [event.type for event in events] == [
        "work_started",
        "researcher_assigned",
        "finalizing",
        "completed",
    ]


def test_specialist_run_never_exceeds_five_messages() -> None:
    events = plan_events(specialist=True, needs_action=False)
    assert len(events) == 5
    assert events[-1].type == "completed"


def test_needs_action_replaces_next_progress_message() -> None:
    events = plan_events(specialist=True, needs_action=True)
    assert len(events) <= 5
    assert "needs_action" in [event.type for event in events]
    assert events[-1].type in {"completed", "failed"}


def test_failed_run_uses_failure_as_the_reserved_terminal_event() -> None:
    events = plan_events(specialist=False, needs_action=False, succeeded=False)

    assert events[-1].type is NotificationEventType.FAILED
    assert len(events) == 4
