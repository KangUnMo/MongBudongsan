from __future__ import annotations

import json
import re
import sys
import traceback
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, cast

import typer
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url
from typer import _click
from typer.core import TyperGroup

from mybudongsan.config import Settings
from mybudongsan.domain.requests import SearchRequest
from mybudongsan.domain.runs import RunStage
from mybudongsan.domain.scoring import DIMENSIONS, DimensionEvidence
from mybudongsan.integrations.drive import DriveBackup, create_drive_service
from mybudongsan.integrations.google_auth import GoogleCredentialStore, redact_google_text
from mybudongsan.integrations.sheets import SheetsProjection, create_sheets_service
from mybudongsan.notifications.outbox import NotificationOutbox
from mybudongsan.reports.publication import ReportPublicationService
from mybudongsan.reports.renderer import (
    AssessedCandidate,
    ReportBundle,
    ReportRunSummary,
)
from mybudongsan.reports.renderer import (
    EvidenceRecord as ReportEvidenceRecord,
)
from mybudongsan.research.contracts import ListingObservation, ResearchBundle
from mybudongsan.research.ingest import ResearchBundleIngestService
from mybudongsan.storage.database import Database
from mybudongsan.storage.repositories import (
    AssessmentRepository,
    EvidenceRepository,
    NotificationRecord,
    RequestRepository,
    RunRecord,
    RunRepository,
)
from mybudongsan.workflows.research_run import ResearchRunService
from mybudongsan.workflows.watch import WatchChangeDetector


class KoreanTyperGroup(TyperGroup):
    """Render Click/Typer parse failures with the same Korean error contract."""

    def main(
        self,
        args: Sequence[str] | None = None,
        prog_name: str | None = None,
        complete_var: str | None = None,
        standalone_mode: bool = True,
        windows_expand_args: bool = True,
        **extra: Any,
    ) -> Any:
        parsed_args = list(sys.argv[1:] if args is None else args)
        try:
            result = super().main(
                args=args,
                prog_name=prog_name,
                complete_var=complete_var,
                standalone_mode=False,
                windows_expand_args=windows_expand_args,
                **extra,
            )
        except _click.ClickException as error:
            if _root_debug_requested(parsed_args):
                traceback.print_exc()
            typer.echo(f"오류: 입력을 확인해 주세요. {error.format_message()}", err=True)
            if standalone_mode:
                raise SystemExit(error.exit_code) from error
            raise
        if standalone_mode and isinstance(result, int) and result != 0:
            raise SystemExit(result)
        return result


def _root_debug_requested(args: Sequence[str]) -> bool:
    index = 0
    while index < len(args):
        token = args[index]
        if token == "--debug":
            return True
        if token in {"--db-url", "--data-dir"}:
            index += 2
            continue
        if token.startswith(("--db-url=", "--data-dir=")) or token in {
            "--no-debug",
            "--install-completion",
            "--show-completion",
            "--help",
        }:
            index += 1
            continue
        return False
    return False


app = typer.Typer(cls=KoreanTyperGroup, help="개인용 부동산 조사 로컬 CLI")
db_app = typer.Typer(help="SQLite 데이터베이스 관리")
request_app = typer.Typer(help="검색 요청 관리")
run_app = typer.Typer(help="조사 실행 관리")
watch_app = typer.Typer(help="수동 WATCH 비교")
google_app = typer.Typer(help="명시적으로 실행하는 Google OAuth 로그인")
sheets_app = typer.Typer(help="Google Sheets 요청/결과 투영")
drive_app = typer.Typer(help="Google Drive 보고서 백업")
notify_app = typer.Typer(help="알림 outbox claim과 PlayMCP 전달 확인 관리")
app.add_typer(db_app, name="db")
app.add_typer(request_app, name="request")
app.add_typer(run_app, name="run")
app.add_typer(watch_app, name="watch")
app.add_typer(google_app, name="google")
app.add_typer(sheets_app, name="sheets")
app.add_typer(drive_app, name="drive")
app.add_typer(notify_app, name="notify")


@dataclass(frozen=True)
class Runtime:
    settings: Settings
    debug: bool


@app.callback()
def main(
    context: typer.Context,
    db_url: Annotated[str | None, typer.Option(help="SQLite 데이터베이스 URL")] = None,
    data_dir: Annotated[Path | None, typer.Option(help="로컬 데이터 디렉터리")] = None,
    debug: Annotated[bool, typer.Option(help="오류 traceback 표시")] = False,
) -> None:
    """CLI 옵션, 환경 변수, ./data 순서로 로컬 경로를 결정합니다."""
    context.obj = Runtime(settings=Settings.resolve(data_dir=data_dir, db_url=db_url), debug=debug)


@db_app.command("upgrade")
def db_upgrade(context: typer.Context) -> None:
    _run(context, lambda: _upgrade_database(_runtime(context).settings))


@request_app.command("import")
def request_import(context: typer.Context, request_path: Path) -> None:
    def operation() -> None:
        request = SearchRequest.model_validate_json(request_path.read_text(encoding="utf-8"))
        RequestRepository(_database(context)).save_version(request)
        typer.echo(f"request_id={request.request_id} version={request.version}")

    _run(context, operation)


@google_app.command("login")
def google_login(
    context: typer.Context,
    client_secret: Annotated[Path, typer.Option("--client-secret", help="로컬 OAuth client secret JSON")],
) -> None:
    """명시적으로 요청했을 때에만 로컬 OAuth 동의 브라우저를 엽니다."""
    _run(context, lambda: GoogleCredentialStore(client_secret).login())


@sheets_app.command("import-request")
def sheets_import_request(
    context: typer.Context,
    spreadsheet_id: Annotated[str, typer.Option(help="사용자가 선택한 Google Spreadsheet ID")],
    row: Annotated[int, typer.Option(min=2, help="검색 요청 행 번호")],
) -> None:
    def operation() -> None:
        request = SheetsProjection(create_sheets_service(GoogleCredentialStore())).import_request(
            spreadsheet_id, row
        )
        RequestRepository(_database(context)).save_version(request)
        typer.echo(f"request_id={request.request_id} version={request.version}")

    _run(context, operation)


@sheets_app.command("sync-run")
def sheets_sync_run(
    context: typer.Context,
    run_id: str,
    spreadsheet_id: Annotated[str, typer.Option(help="사용자가 선택한 Google Spreadsheet ID")],
) -> None:
    def operation() -> None:
        database = _database(context)
        run = RunRepository(database).get(run_id)
        request = RequestRepository(database).get_version(run.request_id, run.request_version)
        bundle, _ = _stored_report_bundle(run)
        SheetsProjection(create_sheets_service(GoogleCredentialStore())).sync_run(
            spreadsheet_id, request, _sheet_run_payload(run), _sheet_candidates(bundle)
        )
        typer.echo(f"run_id={run_id} spreadsheet_id={spreadsheet_id}")

    _run(context, operation)


@drive_app.command("upload-run")
def drive_upload_run(
    context: typer.Context,
    run_id: str,
    folder_id: Annotated[str, typer.Option(help="사용자가 선택한 Drive 상위 폴더 ID")],
) -> None:
    def operation() -> None:
        run = RunRepository(_database(context)).get(run_id)
        _, artifact_directory = _stored_report_bundle(run)
        report_checkpoint = run.checkpoints.get(RunStage.REPORT_COMPLETE)
        if report_checkpoint is None:
            raise ValueError("보고서가 발행된 실행만 Drive에 업로드할 수 있습니다")
        DriveBackup(
            create_drive_service(GoogleCredentialStore()),
            lock_directory=_runtime(context).settings.data_dir,
        ).upload_run(
            folder_id, run.request_id, run.run_id, report_checkpoint.completed_at, artifact_directory
        )
        typer.echo(f"run_id={run_id} folder_id={folder_id}")

    _run(context, operation)


@notify_app.command("pending")
def notify_pending(
    context: typer.Context,
    run_id: str,
    channel: Annotated[str, typer.Option(help="kakao 또는 gmail 채널")] = "kakao",
) -> None:
    """재시도 가능한 payload를 원자적으로 claim하고 JSON Lines로 출력합니다."""

    def operation() -> None:
        records = NotificationOutbox(_database(context)).pending(run_id, channel=channel)
        _echo_notification_records(records)

    _run(context, operation)


@notify_app.command("status")
def notify_status(
    context: typer.Context,
    run_id: str,
    state: Annotated[
        str,
        typer.Option("--state", help="pending, dispatching, failed 또는 sent"),
    ],
    channel: Annotated[
        str | None,
        typer.Option("--channel", help="선택 사항: kakao 또는 gmail 채널"),
    ] = None,
) -> None:
    """현재 outbox 상태와 claim token을 변경 없이 JSON Lines로 조회합니다."""

    def operation() -> None:
        records = NotificationOutbox(_database(context)).status(
            run_id,
            state=state,
            channel=channel,
        )
        _echo_notification_records(records)

    _run(context, operation)


@notify_app.command("ack")
def notify_ack(
    context: typer.Context,
    event_id: int,
    provider_id: Annotated[
        str,
        typer.Option(
            "--provider-id",
            help="공급자 메시지 ID 또는 provider_acknowledged",
        ),
    ],
    claim_token: Annotated[
        str,
        typer.Option("--claim-token", help="pending/status 출력의 현재 claim token"),
    ],
) -> None:
    """Claim된 PlayMCP 또는 Gmail 전달의 공급자 확인을 기록합니다."""

    def operation() -> None:
        record = NotificationOutbox(_database(context)).mark_sent(
            event_id,
            provider_id,
            claim_token=claim_token,
        )
        typer.echo(f"event_id={record.event_id} status={record.status}")

    _run(context, operation)


@notify_app.command("fail")
def notify_fail(
    context: typer.Context,
    event_id: int,
    error: Annotated[str, typer.Option("--error", help="비밀값이 없는 안전한 오류 설명")],
    claim_token: Annotated[
        str,
        typer.Option("--claim-token", help="pending/status 출력의 현재 claim token"),
    ],
) -> None:
    """Claim된 전달을 정제된 오류와 함께 수동 재시도 가능 상태로 돌립니다."""

    def operation() -> None:
        record = NotificationOutbox(_database(context)).mark_failed(
            event_id,
            error,
            claim_token=claim_token,
        )
        typer.echo(f"event_id={record.event_id} status={record.status}")

    _run(context, operation)


@run_app.command("start")
def run_start(
    context: typer.Context,
    request_id: str,
    version: Annotated[int, typer.Option(min=1)],
    run_id: Annotated[str | None, typer.Option(help="재현 가능한 실행 ID")] = None,
) -> None:
    def operation() -> None:
        database = _database(context)
        request_repository = RequestRepository(database)
        request = request_repository.get_version(request_id, version)
        run = ResearchRunService(request_repository, RunRepository(database)).start(
            run_id or f"run-{uuid.uuid4().hex}", request
        )
        if run.current_stage is None:
            raise RuntimeError("실행 시작 단계가 저장되지 않았습니다")
        typer.echo(f"run_id={run.run_id} stage={run.current_stage.value}")

    _run(context, operation)


@run_app.command("ingest")
def run_ingest(context: typer.Context, run_id: str, bundle_path: Path) -> None:
    def operation() -> None:
        bundle = ResearchBundle.model_validate_json(bundle_path.read_text(encoding="utf-8"))
        database = _database(context)
        summary = ResearchBundleIngestService(database).ingest(run_id, bundle)
        typer.echo(
            f"discovered={len(bundle.discovered)} verified={len(bundle.verified)} "
            f"deep={len(bundle.deep_assessments)} created={summary.created} updated={summary.updated}"
        )

    _run(context, operation)


@run_app.command("resume")
def run_resume(context: typer.Context, run_id: str) -> None:
    def operation() -> None:
        point = _run_service(_database(context)).resume(run_id)
        typer.echo(f"run_id={point.run_id} next_stage={point.next_stage.value}")

    _run(context, operation)


@run_app.command("report")
def run_report(
    context: typer.Context,
    run_id: str,
    output: Annotated[Path | None, typer.Option(help="보고서 출력 루트")] = None,
) -> None:
    def operation() -> None:
        database = _database(context)
        request_repository = RequestRepository(database)
        run_repository = RunRepository(database)
        run_service = _run_service(database)
        run = run_repository.get(run_id)
        if run_service.resume(run_id).next_stage is not RunStage.REPORT_COMPLETE:
            raise ValueError("보고서는 심층 조사가 완료된 실행에서만 만들 수 있습니다")
        request = request_repository.get_version(run.request_id, run.request_version)
        output_root = (output or _runtime(context).settings.data_dir / "artifacts").resolve()
        assessed_records = AssessmentRepository(database).list_for_run(run_id, limit=3)
        candidates = tuple(
            AssessedCandidate(
                candidate_id=_candidate_id(record.listing, record.listing_id),
                listing=record.listing,
                assessment=record.assessment.evaluation_result,
                evidence_ids=_evaluation_evidence_ids(
                    record.assessment.evaluation_input.evidence_ids_by_dimension
                ),
            )
            for record in assessed_records
        )
        candidate_evidence_ids = tuple(
            dict.fromkeys(
                evidence_id
                for candidate in candidates
                for evidence_id in candidate.evidence_ids
            )
        )
        evidence = tuple(
            ReportEvidenceRecord(
                evidence_id=item.evidence_id,
                claim=item.claim,
                source_url=item.source_url,
                source_type=item.source_type,
                excerpt=item.excerpt,
                accessed_at=item.accessed_at,
            )
            for item in EvidenceRepository(database).list_for_run(
                run_id,
                evidence_ids=candidate_evidence_ids,
            )
        )
        if run.current_stage is None or run.completed_at is None:
            raise RuntimeError("보고서 실행 메타데이터가 완전하지 않습니다")
        bundle = ReportBundle(
            request=request,
            run=ReportRunSummary(
                run_id=run.run_id,
                status=run.status.value,
                stage=run.current_stage.value,
                completed_at=run.completed_at,
                slug=_slug(run.run_id),
            ),
            candidates=candidates,
            evidence=evidence,
        )
        artifacts = ReportPublicationService(database).publish(run_id, bundle, output_root)
        typer.echo(f"report_path={artifacts.report_path.resolve()}")

    _run(context, operation)


@watch_app.command("refresh")
def watch_refresh(context: typer.Context, previous_path: Path, current_path: Path) -> None:
    def operation() -> None:
        previous = _read_observation(previous_path)
        current = _read_observation(current_path)
        changes = WatchChangeDetector.compare(previous, current)
        if not changes:
            typer.echo("changes=0")
            return
        for change in changes:
            typer.echo(f"field={change.field} previous={change.previous} current={change.current}")

    _run(context, operation)


def _runtime(context: typer.Context) -> Runtime:
    return cast(Runtime, context.find_root().obj)


def _echo_notification_records(records: tuple[NotificationRecord, ...]) -> None:
    for record in records:
        typer.echo(
            json.dumps(
                {
                    "event_id": record.event_id,
                    "run_id": record.run_id,
                    "event_type": record.event_type.value,
                    "channel": record.channel,
                    "payload": record.payload,
                    "status": record.status,
                    "attempt_count": record.attempt_count,
                    "claim_token": record.claim_token,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )


def _database(context: typer.Context) -> Database:
    return Database(_runtime(context).settings.db_url)


def _run_service(database: Database) -> ResearchRunService:
    return ResearchRunService(RequestRepository(database), RunRepository(database))


def _stored_report_bundle(run: RunRecord) -> tuple[ReportBundle, Path]:
    """Read only the published artifact path recorded by the canonical SQLite run."""
    checkpoint = run.checkpoints.get(RunStage.REPORT_COMPLETE)
    if checkpoint is None:
        raise ValueError("보고서가 발행된 실행만 Google에 동기화할 수 있습니다")
    stored_path = checkpoint.checkpoint.get("report_path")
    if not isinstance(stored_path, str):
        raise TypeError("보고서 아티팩트 경로가 저장되지 않았습니다")
    report_path = Path(stored_path)
    run_data_path = report_path.with_name("run-data.json")
    if not report_path.is_file() or not run_data_path.is_file():
        raise FileNotFoundError("저장된 보고서 아티팩트를 찾을 수 없습니다")
    bundle = ReportBundle.model_validate_json(run_data_path.read_text(encoding="utf-8"))
    if bundle.run.run_id != run.run_id:
        raise ValueError("저장된 보고서 아티팩트의 실행 ID가 일치하지 않습니다")
    return bundle, report_path.parent


def _sheet_run_payload(run: RunRecord) -> dict[str, object]:
    return {
        "run_id": run.run_id,
        "status": run.status.value,
        "stage": run.current_stage.value if run.current_stage else "",
        "completed_at": run.completed_at.isoformat() if run.completed_at else "",
    }


def _sheet_candidates(bundle: ReportBundle) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "candidate_id": candidate.candidate_id,
            "complex_name": candidate.listing.complex_name,
            "address": candidate.listing.address,
            "asking_price": candidate.listing.asking_price,
            "status": candidate.listing.status,
            "total_score": candidate.assessment.total_score,
            "confidence": candidate.assessment.confidence,
            "recommendable": candidate.assessment.recommendable,
            "scenario": candidate.scenario.value if candidate.scenario else "",
            "evidence_ids": ",".join(str(item) for item in candidate.evidence_ids),
        }
        for candidate in bundle.candidates
    )


def _upgrade_database(settings: Settings) -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    database_path = make_url(settings.db_url).database
    if database_path not in {None, ":memory:"}:
        Path(database_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    project_root = Path(__file__).resolve().parents[2]
    alembic_config = Config(str(project_root / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(project_root / "migrations"))
    alembic_config.set_main_option("sqlalchemy.url", settings.db_url)
    alembic_config.attributes["mybudongsan_db_url"] = settings.db_url
    command.upgrade(alembic_config, "head")


def _read_observation(path: Path) -> ListingObservation | None:
    if not path.is_file():
        raise FileNotFoundError(f"관측 파일을 찾을 수 없습니다: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload is None:
        return None
    return ListingObservation.model_validate(payload)


def _evaluation_evidence_ids(evidence: DimensionEvidence) -> tuple[int, ...]:
    return tuple(
        dict.fromkeys(
            evidence_id
            for dimension in DIMENSIONS
            for evidence_id in getattr(evidence, dimension)
        )
    )


def _candidate_id(listing: ListingObservation, listing_id: int) -> str:
    source_listing_id = listing.source_listing_id or f"listing-{listing_id}"
    return f"{listing.source}:{source_listing_id}"


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not slug:
        raise ValueError("run_id must contain an ASCII letter or number")
    return slug


def _run(context: typer.Context, operation: Callable[[], None]) -> None:
    try:
        operation()
    except typer.Exit:
        raise
    except Exception as error:  # noqa: BLE001 - the CLI boundary sanitizes every failure
        safe_message = redact_google_text(str(error))
        if _runtime(context).debug:
            typer.echo("Traceback (most recent call last):", err=True)
            for frame in traceback.format_tb(error.__traceback__):
                typer.echo(frame.rstrip(), err=True)
            typer.echo(f"{type(error).__name__}: {safe_message}", err=True)
        typer.echo(f"오류: 명령을 완료하지 못했습니다. {safe_message}", err=True)
        raise typer.Exit(code=1) from None
