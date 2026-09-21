from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def test_initial_migration_creates_expected_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = Path(__file__).resolve().parents[2]
    database_path = tmp_path / "migration.sqlite3"
    database_url = f"sqlite+pysqlite:///{database_path}"
    monkeypatch.setenv("MYBUDONGSAN_DB_URL", database_url)
    alembic_config = Config(str(project_root / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(project_root / "migrations"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)

    command.upgrade(alembic_config, "head")

    engine = create_engine(database_url)
    inspector = inspect(engine)
    assert set(inspector.get_table_names()) == {
        "alembic_version",
        "assessments",
        "buyer_profiles",
        "evidence",
        "listing_snapshots",
        "listings",
        "notification_events",
        "reports",
        "research_runs",
        "search_requests",
    }
    assert _unique_constraints(inspector, "search_requests") == {("request_id", "version")}
    assert _unique_constraints(inspector, "research_runs") == {("run_id",)}
    assert _unique_constraints(inspector, "listings") == {("source", "source_listing_id")}
    assert _unique_constraints(inspector, "assessments") == {("run_id", "listing_id")}
    assert _unique_constraints(inspector, "notification_events") == {
        ("run_id", "event_type", "channel")
    }
    engine.dispose()


def _unique_constraints(inspector: object, table_name: str) -> set[tuple[str, ...]]:
    constraints = inspector.get_unique_constraints(table_name)  # type: ignore[attr-defined]
    return {tuple(constraint["column_names"]) for constraint in constraints}
