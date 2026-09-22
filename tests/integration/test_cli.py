from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from sqlalchemy import func, select
from typer.testing import CliRunner

from mybudongsan.cli import app
from mybudongsan.config import Settings
from mybudongsan.domain.runs import RunStage
from mybudongsan.reports.renderer import ReportRenderer
from mybudongsan.storage.database import Database
from mybudongsan.storage.models import (
    AssessmentModel,
    EvidenceModel,
    ListingModel,
    ReportModel,
)
from mybudongsan.storage.repositories import ReportRepository, RunRepository

runner = CliRunner()


def test_local_cli_fixture_flow_renders_a_report(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    request_path = tmp_path / "request.json"
    request_path.write_text(
        json.dumps(
            {
                "request_id": "req-001",
                "version": 1,
                "regions": [{"name": "서울 테스트구"}],
                "budget": {"minimum": 600000000, "maximum": 1000000000},
                "status": "approved",
            }
        ),
        encoding="utf-8",
    )

    assert runner.invoke(app, ["--data-dir", str(data_dir), "db", "upgrade"]).exit_code == 0
    imported = runner.invoke(app, ["--data-dir", str(data_dir), "request", "import", str(request_path)])
    assert imported.exit_code == 0
    assert "request_id=req-001 version=1" in imported.output

    started = runner.invoke(
        app,
        ["--data-dir", str(data_dir), "run", "start", "req-001", "--version", "1"],
    )
    assert started.exit_code == 0
    run_id = _field(started.output, "run_id")
    assert "stage=request_approved" in started.output

    fixture_path = Path(__file__).parents[1] / "fixtures" / "research_bundle.json"
    ingested = runner.invoke(
        app,
        ["--data-dir", str(data_dir), "run", "ingest", run_id, str(fixture_path)],
    )
    assert ingested.exit_code == 0
    assert "discovered=15 verified=7 deep=3" in ingested.output

    artifacts = tmp_path / "artifacts"
    reported = runner.invoke(
        app,
        ["--data-dir", str(data_dir), "run", "report", run_id, "--output", str(artifacts)],
    )
    assert reported.exit_code == 0
    report_path = Path(_field(reported.output, "report_path"))
    assert report_path.is_absolute()
    assert report_path.is_file()
    candidates_path = report_path.with_name("candidates.csv")
    run_data_path = report_path.with_name("run-data.json")
    with candidates_path.open(encoding="utf-8", newline="") as handle:
        candidates = list(csv.DictReader(handle))
    run_data = json.loads(run_data_path.read_text(encoding="utf-8"))

    assert len(candidates) == 3
    assert [candidate["candidate_id"] for candidate in candidates] == [
        "fixture:deep-01",
        "fixture:deep-02",
        "fixture:deep-03",
    ]
    assert all(candidate["evidence_ids"] for candidate in candidates)
    assert all(candidate["scenario"] == "" for candidate in candidates)
    assert len(run_data["candidates"]) == 3
    assert len(run_data["evidence"]) == 12
    assert all(item["source_url"].startswith("fixture://") for item in run_data["evidence"])
    assert all(candidate["assessment"]["total_score"] is not None for candidate in run_data["candidates"])
    assert all(candidate["findings"] == [] for candidate in run_data["candidates"])
    assert all(candidate["scenario"] is None for candidate in run_data["candidates"])
    assert run_data["run"]["stage"] == "deep_research_complete"

    database = Database(f"sqlite+pysqlite:///{data_dir / 'mybudongsan.sqlite3'}")
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(ListingModel)) == 25
        assert session.scalar(select(func.count()).select_from(EvidenceModel)) == 12
        assert session.scalar(select(func.count()).select_from(AssessmentModel)) == 3
        assert session.scalar(select(func.count()).select_from(ReportModel)) == 1

    resumed = runner.invoke(app, ["--data-dir", str(data_dir), "run", "resume", run_id])
    assert resumed.exit_code == 0
    assert "next_stage=sync_complete" in resumed.output


def test_watch_refresh_compares_local_observation_files(tmp_path: Path) -> None:
    previous = tmp_path / "previous.json"
    current = tmp_path / "current.json"
    previous.write_text(
        json.dumps({"source": "fixture", "source_listing_id": "one", "asking_price": 700000000}),
        encoding="utf-8",
    )
    current.write_text(
        json.dumps({"source": "fixture", "source_listing_id": "one", "asking_price": 680000000}),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["watch", "refresh", str(previous), str(current)])

    assert result.exit_code == 0
    assert "asking_price" in result.output
    assert "700000000" in result.output
    assert "680000000" in result.output


def test_watch_refresh_treats_explicit_json_null_as_an_absent_observation(
    tmp_path: Path,
) -> None:
    previous = tmp_path / "previous.json"
    current = tmp_path / "current.json"
    previous.write_text(
        json.dumps({"source": "fixture", "source_listing_id": "one"}),
        encoding="utf-8",
    )
    current.write_text("null\n", encoding="utf-8")

    result = runner.invoke(app, ["watch", "refresh", str(previous), str(current)])

    assert result.exit_code == 0
    assert "field=possibly_removed" in result.output


def test_watch_refresh_rejects_a_missing_file_with_a_korean_error(tmp_path: Path) -> None:
    existing = tmp_path / "existing.json"
    existing.write_text("null\n", encoding="utf-8")

    result = runner.invoke(app, ["watch", "refresh", str(tmp_path / "missing.json"), str(existing)])

    assert result.exit_code != 0
    assert "오류:" in result.output
    assert "파일" in result.output
    assert "Traceback" not in result.output


def test_cli_prefers_explicit_data_dir_over_environment(tmp_path: Path) -> None:
    explicit_data = tmp_path / "explicit"
    environment_data = tmp_path / "environment"

    result = runner.invoke(
        app,
        ["--data-dir", str(explicit_data), "db", "upgrade"],
        env={"MYBUDONGSAN_DATA_DIR": str(environment_data)},
    )

    assert result.exit_code == 0
    assert (explicit_data / "mybudongsan.sqlite3").is_file()
    assert not (environment_data / "mybudongsan.sqlite3").exists()


def test_cli_prefers_explicit_db_url_over_environment(tmp_path: Path) -> None:
    explicit_database = tmp_path / "explicit.sqlite3"
    environment_database = tmp_path / "environment.sqlite3"

    result = runner.invoke(
        app,
        ["--db-url", f"sqlite+pysqlite:///{explicit_database}", "db", "upgrade"],
        env={"MYBUDONGSAN_DB_URL": f"sqlite+pysqlite:///{environment_database}"},
    )

    assert result.exit_code == 0
    assert explicit_database.is_file()
    assert not environment_database.exists()


def test_settings_use_environment_before_safe_local_defaults(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MYBUDONGSAN_DATA_DIR", str(tmp_path / "environment"))
    monkeypatch.setenv("MYBUDONGSAN_DB_URL", "sqlite+pysqlite:///environment.sqlite3")

    environment_settings = Settings.resolve()
    default_settings = Settings.resolve(data_dir=tmp_path / "explicit", db_url="sqlite+pysqlite:///explicit.sqlite3")

    assert environment_settings.data_dir == (tmp_path / "environment").resolve()
    assert environment_settings.db_url == "sqlite+pysqlite:///environment.sqlite3"
    assert default_settings.data_dir == (tmp_path / "explicit").resolve()
    assert default_settings.db_url == "sqlite+pysqlite:///explicit.sqlite3"


def test_cli_errors_are_korean_and_only_show_a_traceback_in_debug(tmp_path: Path) -> None:
    normal = runner.invoke(app, ["--data-dir", str(tmp_path), "run", "resume", "missing"])
    debug = runner.invoke(app, ["--debug", "--data-dir", str(tmp_path), "run", "resume", "missing"])

    assert normal.exit_code != 0
    assert "오류:" in normal.output
    assert "Traceback" not in normal.output
    assert debug.exit_code != 0
    assert "오류:" in debug.output
    assert "Traceback" in debug.output


@pytest.mark.parametrize(
    "arguments",
    [
        ["run", "start", "req-001"],
        ["run", "start", "req-001", "--version", "not-an-integer"],
        ["--version"],
        ["unknown-command"],
    ],
)
def test_cli_parse_errors_are_localized_without_a_traceback(arguments: list[str]) -> None:
    result = runner.invoke(app, arguments)

    assert result.exit_code != 0
    assert "오류:" in result.output
    assert "Traceback" not in result.output


def test_cli_parse_errors_show_a_traceback_only_with_root_debug() -> None:
    result = runner.invoke(app, ["--debug", "run", "start", "req-001"])

    assert result.exit_code != 0
    assert "오류:" in result.output
    assert "Traceback" in result.output


def test_cli_parse_errors_do_not_treat_subcommand_debug_as_root_debug() -> None:
    result = runner.invoke(app, ["run", "--debug", "start", "req-001"])

    assert result.exit_code != 0
    assert "오류:" in result.output
    assert "Traceback" not in result.output


@pytest.mark.parametrize("failure_point", ["render", "persist"])
def test_report_failure_does_not_mark_the_run_complete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    data_dir, run_id = _prepare_ingested_run(tmp_path)

    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError(f"forced {failure_point} failure")

    if failure_point == "render":
        monkeypatch.setattr(ReportRenderer, "render", fail)
    else:
        monkeypatch.setattr(ReportRepository, "save_markdown", fail)

    result = runner.invoke(
        app,
        [
            "--data-dir",
            str(data_dir),
            "run",
            "report",
            run_id,
            "--output",
            str(tmp_path / "artifacts"),
        ],
    )

    database = Database(f"sqlite+pysqlite:///{data_dir / 'mybudongsan.sqlite3'}")
    assert result.exit_code != 0
    assert RunRepository(database).get(run_id).current_stage is RunStage.DEEP_RESEARCH_COMPLETE
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(ReportModel)) == 0


def test_run_ingest_accepts_a_bundle_with_no_deep_assessments(tmp_path: Path) -> None:
    data_dir, run_id = _prepare_started_run(tmp_path)
    bundle_path = tmp_path / "shallow-bundle.json"
    bundle_path.write_text(
        json.dumps(
            {
                "discovered": [
                    {"source": "fixture", "source_listing_id": "shallow-1"}
                ],
                "verified": [],
                "deep_assessments": [],
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["--data-dir", str(data_dir), "run", "ingest", run_id, str(bundle_path)],
    )

    assert result.exit_code == 0
    assert "discovered=1 verified=0 deep=0" in result.output


def _prepare_ingested_run(tmp_path: Path) -> tuple[Path, str]:
    data_dir, run_id = _prepare_started_run(tmp_path)
    fixture_path = Path(__file__).parents[1] / "fixtures" / "research_bundle.json"
    assert runner.invoke(
        app,
        [
            "--data-dir",
            str(data_dir),
            "run",
            "ingest",
            run_id,
            str(fixture_path),
        ],
    ).exit_code == 0
    return data_dir, run_id


def _prepare_started_run(tmp_path: Path) -> tuple[Path, str]:
    data_dir = tmp_path / "data"
    request_path = tmp_path / "request.json"
    request_path.write_text(
        json.dumps(
            {
                "request_id": "req-failure",
                "version": 1,
                "regions": [{"name": "서울 테스트구"}],
                "budget": {"minimum": 600000000, "maximum": 1000000000},
                "status": "approved",
            }
        ),
        encoding="utf-8",
    )
    assert runner.invoke(app, ["--data-dir", str(data_dir), "db", "upgrade"]).exit_code == 0
    assert runner.invoke(
        app,
        ["--data-dir", str(data_dir), "request", "import", str(request_path)],
    ).exit_code == 0
    assert runner.invoke(
        app,
        [
            "--data-dir",
            str(data_dir),
            "run",
            "start",
            "req-failure",
            "--version",
            "1",
            "--run-id",
            "run-failure",
        ],
    ).exit_code == 0
    return data_dir, "run-failure"


def _field(output: str, name: str) -> str:
    for token in output.split():
        if token.startswith(f"{name}="):
            return token.removeprefix(f"{name}=")
    raise AssertionError(f"{name} was not printed: {output}")
