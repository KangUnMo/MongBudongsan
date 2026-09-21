from __future__ import annotations

import re
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, cast

import typer
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from mybudongsan.config import Settings
from mybudongsan.domain.requests import SearchRequest
from mybudongsan.domain.runs import RunStage
from mybudongsan.reports.renderer import ReportBundle, ReportRenderer, ReportRunSummary
from mybudongsan.research.contracts import ListingObservation, ResearchBundle
from mybudongsan.research.ingest import ListingIngestService
from mybudongsan.storage.database import Database
from mybudongsan.storage.models import ReportModel
from mybudongsan.storage.repositories import ListingRepository, RequestRepository, RunRepository
from mybudongsan.workflows.research_run import ResearchRunService
from mybudongsan.workflows.watch import WatchChangeDetector

app = typer.Typer(help="개인용 부동산 조사 로컬 CLI")
db_app = typer.Typer(help="SQLite 데이터베이스 관리")
request_app = typer.Typer(help="검색 요청 관리")
run_app = typer.Typer(help="조사 실행 관리")
watch_app = typer.Typer(help="수동 WATCH 비교")
app.add_typer(db_app, name="db")
app.add_typer(request_app, name="request")
app.add_typer(run_app, name="run")
app.add_typer(watch_app, name="watch")


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
        run_service = _run_service(database)
        summary = ListingIngestService(ListingRepository(database)).ingest(run_id, bundle)
        run_service.advance(
            run_id,
            RunStage.DISCOVERY_COMPLETE,
            {"discovered_count": len(bundle.discovered), "created": summary.created},
        )
        run_service.advance(
            run_id,
            RunStage.FILTER_COMPLETE,
            {"candidate_count": len(bundle.verified)},
        )
        run_service.advance(
            run_id,
            RunStage.VERIFICATION_COMPLETE,
            {"verified_count": len(bundle.verified)},
        )
        run_service.advance(
            run_id,
            RunStage.DEEP_RESEARCH_COMPLETE,
            {"deep_assessment_count": len(bundle.deep_assessments)},
        )
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
        bundle = ReportBundle(
            request=request,
            run=ReportRunSummary(
                run_id=run.run_id,
                status=run.status.value,
                stage=RunStage.REPORT_COMPLETE.value,
                completed_at=run.completed_at or datetime.now(UTC),
                slug=_slug(run.run_id),
            ),
        )
        artifacts = ReportRenderer().render(bundle, output_root)
        run_service.advance(
            run_id,
            RunStage.REPORT_COMPLETE,
            {"report_path": str(artifacts.report_path)},
        )
        with database.session() as session:
            session.add(
                ReportModel(
                    run_id=run_id,
                    format="markdown",
                    content=artifacts.report_path.read_text(encoding="utf-8"),
                    drive_file_id=None,
                    created_at=datetime.now(UTC),
                )
            )
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


def _database(context: typer.Context) -> Database:
    return Database(_runtime(context).settings.db_url)


def _run_service(database: Database) -> ResearchRunService:
    return ResearchRunService(RequestRepository(database), RunRepository(database))


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
    if not path.exists():
        return None
    return ListingObservation.model_validate_json(path.read_text(encoding="utf-8"))


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
    except Exception as error:
        if _runtime(context).debug:
            traceback.print_exc()
        typer.echo(f"오류: 명령을 완료하지 못했습니다. {error}", err=True)
        raise typer.Exit(code=1) from error
