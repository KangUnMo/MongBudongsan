from __future__ import annotations

import base64
import json
from concurrent.futures import ThreadPoolExecutor
from email import message_from_bytes
from pathlib import Path
from threading import Barrier

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

    claimed, = outbox.pending("run-notify", channel="kakao")
    assert claimed.status == "dispatching"
    assert claimed.attempt_count == 1
    assert claimed.claim_token

    with pytest.raises(ValueError, match="provider"):
        outbox.mark_sent(record.event_id, "", claim_token=claimed.claim_token)

    sent = outbox.mark_sent(
        record.event_id,
        "provider_acknowledged",
        claim_token=claimed.claim_token,
    )
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

    claimed, = outbox.pending("run-notify", channel="kakao")
    assert claimed.claim_token
    failed = outbox.mark_failed(
        record.event_id,
        "access_token=secret token kakao-secret api_key=playmcp-secret",
        claim_token=claimed.claim_token,
    )

    assert failed.status == "failed"
    assert failed.attempt_count == 1
    assert "secret" not in (failed.last_error or "")
    retry, = outbox.pending("run-notify", channel="kakao")
    assert retry.status == "dispatching"
    assert retry.attempt_count == 2
    assert retry.claim_token and retry.claim_token != claimed.claim_token

    sent = outbox.mark_sent(
        record.event_id,
        "kakao-retry-1",
        claim_token=retry.claim_token,
    )
    assert sent.status == "sent"
    assert sent.attempt_count == 2
    assert outbox.pending("run-notify", channel="kakao") == ()


@pytest.mark.parametrize(
    ("unsafe_message", "secret"),
    [
        ("Authorization: Bearer top-secret", "top-secret"),
        ("Authorization: Basic basic-secret", "basic-secret"),
        ('{"Authorization": "Bearer json-secret"}', "json-secret"),
        ("{'Authorization': 'Basic dict-secret'}", "dict-secret"),
        ("X-API-Key: api-header-secret", "api-header-secret"),
        ("PlayMCP-API-Key: playmcp-header-secret", "playmcp-header-secret"),
    ],
)
def test_failure_bookkeeping_redacts_entire_auth_header_values(
    database: Database,
    unsafe_message: str,
    secret: str,
) -> None:
    _seed_run(database)
    outbox = NotificationOutbox(database)
    record, = outbox.enqueue(
        "run-notify",
        NotificationEvent(NotificationEventType.WORK_STARTED, "조사를 시작했습니다."),
    )
    claimed, = outbox.pending("run-notify", channel="kakao")
    assert claimed.claim_token

    failed = outbox.mark_failed(
        record.event_id,
        unsafe_message,
        claim_token=claimed.claim_token,
    )

    assert secret not in (failed.last_error or "")
    with database.session() as session:
        stored = session.get(NotificationEventModel, record.event_id)
        assert stored is not None
        assert secret not in (stored.last_error or "")


def test_ack_and_fail_require_a_claimed_dispatching_state(database: Database) -> None:
    _seed_run(database)
    outbox = NotificationOutbox(database)
    record, = outbox.enqueue(
        "run-notify",
        NotificationEvent(NotificationEventType.WORK_STARTED, "조사를 시작했습니다."),
    )

    with pytest.raises(ValueError, match="dispatching"):
        outbox.mark_sent(record.event_id, "provider-1", claim_token="unclaimed")
    with pytest.raises(ValueError, match="dispatching"):
        outbox.mark_failed(record.event_id, "safe timeout", claim_token="unclaimed")

    claimed, = outbox.pending("run-notify", channel="kakao")
    assert claimed.claim_token
    outbox.mark_sent(record.event_id, "provider-1", claim_token=claimed.claim_token)
    with pytest.raises(ValueError, match="dispatching"):
        outbox.mark_failed(record.event_id, "safe timeout", claim_token=claimed.claim_token)


def test_stale_attempt_token_cannot_ack_or_fail_a_newer_claim(database: Database) -> None:
    _seed_run(database)
    outbox = NotificationOutbox(database)
    record, = outbox.enqueue(
        "run-notify",
        NotificationEvent(NotificationEventType.WORK_STARTED, "조사를 시작했습니다."),
    )
    attempt_one, = outbox.pending("run-notify", channel="kakao")
    assert attempt_one.claim_token
    outbox.mark_failed(
        record.event_id,
        "safe timeout",
        claim_token=attempt_one.claim_token,
    )
    attempt_two, = outbox.pending("run-notify", channel="kakao")
    assert attempt_two.claim_token
    assert attempt_two.claim_token != attempt_one.claim_token
    assert attempt_two.attempt_count == 2

    with pytest.raises(ValueError, match="stale"):
        outbox.mark_sent(
            record.event_id,
            "late-provider-id",
            claim_token=attempt_one.claim_token,
        )
    with pytest.raises(ValueError, match="stale"):
        outbox.mark_failed(
            record.event_id,
            "late failure",
            claim_token=attempt_one.claim_token,
        )

    current, = outbox.status("run-notify", state="dispatching", channel="kakao")
    assert current.claim_token == attempt_two.claim_token
    assert current.attempt_count == 2


def test_read_only_status_recovers_a_claim_lost_before_output(database: Database) -> None:
    _seed_run(database)
    outbox = NotificationOutbox(database)
    record, = outbox.enqueue(
        "run-notify",
        NotificationEvent(NotificationEventType.WORK_STARTED, "조사를 시작했습니다."),
    )

    lost_output_claim, = outbox.pending("run-notify", channel="kakao")
    recovered, = outbox.status("run-notify", state="dispatching", channel="kakao")
    observed_again, = outbox.status("run-notify", state="dispatching", channel="kakao")

    assert lost_output_claim.claim_token
    assert recovered.event_id == record.event_id
    assert recovered.claim_token == lost_output_claim.claim_token
    assert recovered.attempt_count == 1
    assert observed_again == recovered

    failed = outbox.mark_failed(
        recovered.event_id,
        "confirmed not delivered",
        claim_token=recovered.claim_token or "",
    )
    assert failed.status == "failed"


def test_concurrent_pending_claims_never_return_the_same_event(tmp_path: Path) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'claim-race.sqlite3'}"
    database = Database(database_url)
    database.create_schema()
    _seed_run(database)
    outbox = NotificationOutbox(database)
    for event in plan_events(specialist=False, needs_action=False)[:2]:
        outbox.enqueue("run-notify", event)
    barrier = Barrier(2)

    def claim() -> tuple[int, ...]:
        barrier.wait()
        claimed = NotificationOutbox(Database(database_url)).pending(
            "run-notify", channel="kakao"
        )
        return tuple(record.event_id for record in claimed)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(lambda _: claim(), range(2)))

    assert set(results) == {(), tuple(sorted((*results[0], *results[1])))}
    assert len(set(results[0]) & set(results[1])) == 0
    assert len((*results[0], *results[1])) == 2
    with database.session() as session:
        stored = tuple(
            session.scalars(
                select(NotificationEventModel).order_by(NotificationEventModel.id)
            )
        )
    assert all(item.status == "dispatching" and item.attempt_count == 1 for item in stored)


def test_concurrent_duplicate_enqueue_returns_one_idempotent_row(tmp_path: Path) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'same-enqueue-race.sqlite3'}"
    database = Database(database_url)
    database.create_schema()
    _seed_run(database)
    barrier = Barrier(2)
    event = NotificationEvent(NotificationEventType.WORK_STARTED, "조사를 시작했습니다.")

    def enqueue() -> tuple[int, ...]:
        barrier.wait()
        records = NotificationOutbox(Database(database_url)).enqueue("run-notify", event)
        return tuple(record.event_id for record in records)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(lambda _: enqueue(), range(2)))

    assert results[0] == results[1]
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(NotificationEventModel)) == 1


def test_concurrent_distinct_enqueue_preserves_terminal_slot(tmp_path: Path) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'cap-race.sqlite3'}"
    database = Database(database_url)
    database.create_schema()
    _seed_run(database)
    outbox = NotificationOutbox(database)
    base_events = plan_events(specialist=True, needs_action=False)[:3]
    for event in base_events:
        outbox.enqueue("run-notify", event)
    barrier = Barrier(2)
    competing = (
        NotificationEvent(NotificationEventType.FINALIZING, "마무리"),
        NotificationEvent(NotificationEventType.NEEDS_ACTION, "확인 필요"),
    )

    def enqueue(event: NotificationEvent) -> str:
        barrier.wait()
        try:
            NotificationOutbox(Database(database_url)).enqueue("run-notify", event)
        except ValueError:
            return "rejected"
        return "stored"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(enqueue, competing))

    assert sorted(outcomes) == ["rejected", "stored"]
    outbox.enqueue(
        "run-notify",
        NotificationEvent(NotificationEventType.COMPLETED, "조사가 완료되었습니다."),
    )
    with database.session() as session:
        event_types = set(
            session.scalars(
                select(NotificationEventModel.event_type).where(
                    NotificationEventModel.run_id == "run-notify"
                )
            )
        )
    assert len(event_types) == 5


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


def test_gmail_delivery_uses_claim_ack_protocol_and_is_not_replayed(
    database: Database,
) -> None:
    _seed_run(database)
    outbox = NotificationOutbox(database)
    terminal = NotificationEvent(
        NotificationEventType.COMPLETED,
        "조사가 완료되었습니다.",
    )
    outbox.enqueue("run-notify", terminal)
    claimed, = outbox.pending("run-notify", channel="gmail")
    service = _FakeGmail()

    provider_id = GmailNotifier(service, recipient="owner@example.test").send_terminal(
        run_id=claimed.run_id,
        event_type=claimed.event_type,
        outcome="완료",
        conclusion="추천 후보를 정리했습니다.",
    )
    assert claimed.claim_token
    outbox.mark_sent(
        claimed.event_id,
        provider_id,
        claim_token=claimed.claim_token,
    )
    outbox.enqueue("run-notify", terminal)

    assert outbox.pending("run-notify", channel="gmail") == ()
    assert len(service.send_calls) == 1


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
    assert all(item["claim_token"] for item in payloads)
    assert payloads[0]["payload"] == {
        "run_id": "run-notify",
        "event_type": "work_started",
        "message": "조사를 시작했습니다.",
    }

    status_arguments = [
        "--db-url", database_url, "notify", "status", "run-notify",
        "--state", "dispatching", "--channel", "kakao",
    ]
    status = runner.invoke(app, status_arguments)
    status_again = runner.invoke(app, status_arguments)
    assert status.exit_code == 0, status.output
    assert status_again.exit_code == 0, status_again.output
    assert status.output == status_again.output
    recovered = [json.loads(line) for line in status.output.splitlines()]
    assert [item["claim_token"] for item in recovered] == [
        payloads[0]["claim_token"],
        payloads[1]["claim_token"],
    ]
    assert all(item["attempt_count"] == 1 for item in recovered)

    acknowledged = runner.invoke(
        app,
        [
            "--db-url", database_url, "notify", "ack", str(first.event_id),
            "--provider-id", "kakao-1", "--claim-token", payloads[0]["claim_token"],
        ],
    )
    failed = runner.invoke(
        app,
        [
            "--db-url", database_url, "notify", "fail", str(second.event_id),
            "--error", "safe timeout", "--claim-token", payloads[1]["claim_token"],
        ],
    )
    assert acknowledged.exit_code == 0, acknowledged.output
    assert failed.exit_code == 0, failed.output
    assert "status=sent" in acknowledged.output
    assert "status=failed" in failed.output


@pytest.mark.parametrize(
    ("unsafe_message", "secret"),
    [
        ("Authorization: Bearer cli-bearer-secret", "cli-bearer-secret"),
        ("Authorization: Basic cli-basic-secret", "cli-basic-secret"),
        ("PlayMCP-API-Key: cli-playmcp-secret", "cli-playmcp-secret"),
    ],
)
def test_notify_cli_redacts_runtime_errors_in_normal_and_debug_output(
    monkeypatch: pytest.MonkeyPatch,
    unsafe_message: str,
    secret: str,
) -> None:
    def fail(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise RuntimeError(unsafe_message)

    monkeypatch.setattr(NotificationOutbox, "mark_failed", fail)
    arguments = [
        "notify", "fail", "1", "--error", "safe input", "--claim-token", "test-token"
    ]

    normal = CliRunner().invoke(app, arguments)
    debug = CliRunner().invoke(app, ["--debug", *arguments])

    assert normal.exit_code != 0
    assert debug.exit_code != 0
    assert "Traceback" not in normal.output
    assert "Traceback" in debug.output
    assert secret not in normal.output
    assert secret not in debug.output
    assert secret not in repr(normal.exception)
    assert secret not in repr(debug.exception)
