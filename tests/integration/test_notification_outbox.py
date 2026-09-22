from __future__ import annotations

import base64
import json
from email import message_from_bytes
from pathlib import Path

import pytest
from sqlalchemy import func, select
from typer.testing import CliRunner

from mybudongsan.cli import app
from mybudongsan.domain.requests import MoneyRange, RegionCriterion, SearchRequest
from mybudongsan.integrations.gmail import GmailNotifier
from mybudongsan.notifications.outbox import NotificationOutbox
from mybudongsan.notifications.policy import (
    NotificationEvent,
    NotificationEventType,
    plan_events,
)
from mybudongsan.storage.database import Database
from mybudongsan.storage.models import NotificationEventModel
from mybudongsan.storage.repositories import RequestRepository, RunRepository


def _seed_run(database: Database, run_id: str = "run-notify") -> None:
    request = SearchRequest(
        request_id=f"request-{run_id}",
        version=1,
        regions=[RegionCriterion(name="서울 강서구")],
        budget=MoneyRange(minimum=1, maximum=2),
    )
    RequestRepository(database).save_version(request)
    RunRepository(database).create(run_id, request.request_id, request.version)


def test_outbox_persists_at_most_five_lifecycle_types_and_two_terminal_channels(
    database: Database,
) -> None:
    _seed_run(database)
    outbox = NotificationOutbox(database)

    for event in plan_events(specialist=True, needs_action=False):
        outbox.enqueue("run-notify", event)

    kakao = outbox.pending("run-notify", channel="kakao")
    gmail = outbox.pending("run-notify", channel="gmail")
    assert [record.event_type for record in kakao] == [
        NotificationEventType.WORK_STARTED,
        NotificationEventType.RESEARCHER_ASSIGNED,
        NotificationEventType.SPECIALIST_ASSIGNED,
        NotificationEventType.FINALIZING,
        NotificationEventType.COMPLETED,
    ]
    assert [record.event_type for record in gmail] == [NotificationEventType.COMPLETED]
    assert gmail[0].payload["message"] == "조사가 완료되었습니다."


def test_enqueue_is_idempotent_across_resume(database: Database) -> None:
    _seed_run(database)
    outbox = NotificationOutbox(database)
    terminal = plan_events(specialist=False, needs_action=False)[-1]

    first = outbox.enqueue("run-notify", terminal)
    resumed = outbox.enqueue("run-notify", terminal)

    assert [record.event_id for record in resumed] == [record.event_id for record in first]
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(NotificationEventModel)) == 2


def test_nonterminal_enqueue_reserves_one_of_five_slots_for_terminal_event(
    database: Database,
) -> None:
    _seed_run(database)
    outbox = NotificationOutbox(database)
    progress = (
        NotificationEvent(NotificationEventType.WORK_STARTED, "시작"),
        NotificationEvent(NotificationEventType.RESEARCHER_ASSIGNED, "조사 담당"),
        NotificationEvent(NotificationEventType.SPECIALIST_ASSIGNED, "전문가 담당"),
        NotificationEvent(NotificationEventType.FINALIZING, "마무리"),
    )
    for event in progress:
        outbox.enqueue("run-notify", event)

    with pytest.raises(ValueError, match="terminal"):
        outbox.enqueue(
            "run-notify",
            NotificationEvent(NotificationEventType.NEEDS_ACTION, "사용자 확인"),
        )

    outbox.enqueue(
        "run-notify",
        NotificationEvent(NotificationEventType.FAILED, "조사에 실패했습니다."),
    )
    assert len(outbox.pending("run-notify", channel="kakao")) == 5


def test_ack_requires_provider_id_and_resume_does_not_redeliver(database: Database) -> None:
    _seed_run(database)
    outbox = NotificationOutbox(database)
    record = outbox.enqueue(
        "run-notify",
        NotificationEvent(NotificationEventType.WORK_STARTED, "조사를 시작했습니다."),
    )[0]

    with pytest.raises(ValueError, match="provider"):
        outbox.mark_sent(record.event_id, "")

    sent = outbox.mark_sent(record.event_id, "provider_acknowledged")
    assert sent.status == "sent"
    assert sent.attempt_count == 1
    assert sent.provider_message_id == "provider_acknowledged"
    assert outbox.pending("run-notify", channel="kakao") == ()
    assert outbox.enqueue(
        "run-notify",
        NotificationEvent(NotificationEventType.WORK_STARTED, "다른 문구"),
    )[0].status == "sent"


def test_failure_bookkeeping_sanitizes_errors(database: Database) -> None:
    _seed_run(database)
    outbox = NotificationOutbox(database)
    record = outbox.enqueue(
        "run-notify",
        NotificationEvent(NotificationEventType.WORK_STARTED, "조사를 시작했습니다."),
    )[0]

    failed = outbox.mark_failed(
        record.event_id,
        "access_token=secret token kakao-secret api_key=playmcp-secret",
    )

    assert failed.status == "failed"
    assert failed.attempt_count == 1
    assert "secret" not in (failed.last_error or "")
    assert outbox.pending("run-notify", channel="kakao") == (failed,)

    sent = outbox.mark_sent(record.event_id, "kakao-retry-1")
    assert sent.status == "sent"
    assert sent.attempt_count == 2
    assert outbox.pending("run-notify", channel="kakao") == ()


class _FakeRequest:
    def __init__(self, response: object) -> None:
        self.response = response

    def execute(self) -> object:
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


class _FakeMessages:
    def __init__(self, parent: _FakeGmail) -> None:
        self.parent = parent

    def send(self, **kwargs: object) -> _FakeRequest:
        self.parent.send_calls.append(kwargs)
        return _FakeRequest(self.parent.response)


class _FakeUsers:
    def __init__(self, parent: _FakeGmail) -> None:
        self.parent = parent

    def messages(self) -> _FakeMessages:
        return _FakeMessages(self.parent)


class _FakeGmail:
    def __init__(self, response: object | None = None) -> None:
        self.response = {"id": "gmail-message-1"} if response is None else response
        self.send_calls: list[dict[str, object]] = []

    def users(self) -> _FakeUsers:
        return _FakeUsers(self)


def test_gmail_sends_plain_text_utf8_terminal_message_with_report_link() -> None:
    service = _FakeGmail()
    notifier = GmailNotifier(service, recipient="owner@example.test")

    provider_id = notifier.send_terminal(
        run_id="run-notify",
        event_type=NotificationEventType.COMPLETED,
        outcome="완료",
        conclusion="추천 후보 3개를 정리했습니다.",
        drive_report_link="https://drive.google.test/report",
    )

    assert provider_id == "gmail-message-1"
    assert service.send_calls[0]["userId"] == "me"
    body = service.send_calls[0]["body"]
    assert isinstance(body, dict)
    raw = body["raw"]
    assert isinstance(raw, str)
    padded = raw + "=" * (-len(raw) % 4)
    email = message_from_bytes(base64.urlsafe_b64decode(padded))
    assert email.get_content_type() == "text/plain"
    assert email.get_content_charset() == "utf-8"
    decoded = email.get_payload(decode=True).decode("utf-8")
    assert "run-notify" in decoded
    assert "완료" in decoded
    assert "추천 후보 3개를 정리했습니다." in decoded
    assert "https://drive.google.test/report" in decoded


def test_gmail_rejects_nonterminal_events_and_sanitizes_provider_errors() -> None:
    notifier = GmailNotifier(_FakeGmail(), recipient="owner@example.test")
    with pytest.raises(ValueError, match="terminal"):
        notifier.send_terminal(
            run_id="run-notify",
            event_type=NotificationEventType.FINALIZING,
            outcome="진행 중",
            conclusion="마무리 중",
        )

    failing = GmailNotifier(
        _FakeGmail(RuntimeError("refresh_token=secret")),
        recipient="owner@example.test",
    )
    with pytest.raises(RuntimeError) as error:
        failing.send_terminal(
            run_id="run-notify",
            event_type=NotificationEventType.FAILED,
            outcome="실패",
            conclusion="다시 확인해 주세요.",
        )
    assert "secret" not in str(error.value)


def test_notify_cli_exposes_pending_kakao_payload_and_records_ack_and_failure(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'notify.sqlite3'}"
    database = Database(database_url)
    database.create_schema()
    _seed_run(database)
    outbox = NotificationOutbox(database)
    first, = outbox.enqueue(
        "run-notify",
        NotificationEvent(NotificationEventType.WORK_STARTED, "조사를 시작했습니다."),
    )
    second, = outbox.enqueue(
        "run-notify",
        NotificationEvent(NotificationEventType.RESEARCHER_ASSIGNED, "조사 담당자를 배정했습니다."),
    )
    runner = CliRunner()

    pending = runner.invoke(
        app,
        ["--db-url", database_url, "notify", "pending", "run-notify", "--channel", "kakao"],
    )
    assert pending.exit_code == 0, pending.output
    payloads = [json.loads(line) for line in pending.output.splitlines()]
    assert [item["event_id"] for item in payloads] == [first.event_id, second.event_id]
    assert payloads[0]["payload"] == {
        "run_id": "run-notify",
        "event_type": "work_started",
        "message": "조사를 시작했습니다.",
    }

    acknowledged = runner.invoke(
        app,
        ["--db-url", database_url, "notify", "ack", str(first.event_id), "--provider-id", "kakao-1"],
    )
    failed = runner.invoke(
        app,
        ["--db-url", database_url, "notify", "fail", str(second.event_id), "--error", "safe timeout"],
    )
    assert acknowledged.exit_code == 0, acknowledged.output
    assert failed.exit_code == 0, failed.output
    assert "status=sent" in acknowledged.output
    assert "status=failed" in failed.output
