from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from mybudongsan.cli import app
from mybudongsan.config import Settings

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


def _field(output: str, name: str) -> str:
    for token in output.split():
        if token.startswith(f"{name}="):
            return token.removeprefix(f"{name}=")
    raise AssertionError(f"{name} was not printed: {output}")
