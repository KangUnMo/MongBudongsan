from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from mybudongsan.domain.requests import MoneyRange, RegionCriterion, SearchRequest
from mybudongsan.domain.scoring import EvaluationInput, evaluate_listing
from mybudongsan.storage.database import Database
from mybudongsan.storage.models import EvidenceModel, ListingModel
from mybudongsan.storage.repositories import AssessmentRepository, RequestRepository, RunRepository


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


def test_assessment_payload_upgrade_path_preserves_0001_then_adds_json_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = Path(__file__).resolve().parents[2]
    database_path = tmp_path / "assessment-upgrade.sqlite3"
    database_url = f"sqlite+pysqlite:///{database_path}"
    monkeypatch.setenv("MYBUDONGSAN_DB_URL", database_url)
    alembic_config = Config(str(project_root / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(project_root / "migrations"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)

    command.upgrade(alembic_config, "0001")
    engine = create_engine(database_url)
    assert {column["name"] for column in inspect(engine).get_columns("assessments")} >= {
        "id", "run_id", "listing_id", "passed_gates", "score", "risks", "rationale", "created_at"
    }
    assert "input_payload" not in {column["name"] for column in inspect(engine).get_columns("assessments")}
    assert "result_payload" not in {column["name"] for column in inspect(engine).get_columns("assessments")}
    engine.dispose()

    command.upgrade(alembic_config, "head")
    engine = create_engine(database_url)
    columns = {column["name"]: column for column in inspect(engine).get_columns("assessments")}
    assert {"input_payload", "result_payload"} <= columns.keys()
    assert columns["input_payload"]["nullable"] is False
    assert columns["result_payload"]["nullable"] is False
    engine.dispose()

    database = Database(database_url)
    request = SearchRequest(
        request_id="migration-request",
        version=1,
        regions=[RegionCriterion(name="서울 강서구")],
        budget=MoneyRange(minimum=1, maximum=2),
    )
    RequestRepository(database).save_version(request)
    RunRepository(database).create("migration-run", request.request_id, request.version)
    with database.session() as session:
        listing = ListingModel(
            source="fixture", source_listing_id="migration-listing", created_at=datetime.now(UTC)
        )
        session.add(listing)
        session.flush()
        evidence = [
            EvidenceModel(
                run_id="migration-run",
                listing_id=listing.id,
                claim=f"claim-{index}",
                source_url=f"https://example.test/{index}",
                source_type="fixture",
                excerpt=None,
                accessed_at=datetime.now(UTC),
            )
            for index in range(4)
        ]
        session.add_all(evidence)
        session.flush()
        listing_id = listing.id
        evidence_ids = tuple(item.id for item in evidence)

    evaluation_input = EvaluationInput(
        required_passed=True,
        excluded_passed=True,
        active_listing_confirmed=True,
        minimum_evidence_met=True,
        liquidity=80,
        commute=70,
        price=90,
        residential=60,
        confidence=90,
        evidence_ids_by_dimension={
            dimension: [evidence_id]
            for dimension, evidence_id in zip(
                ("liquidity", "commute", "price", "residential"), evidence_ids, strict=True
            )
        },
    )
    saved = AssessmentRepository(database).save(
        run_id="migration-run", listing_id=listing_id, evaluation_input=evaluation_input
    )
    assert saved.evaluation_result == evaluate_listing(evaluation_input)


def _unique_constraints(inspector: object, table_name: str) -> set[tuple[str, ...]]:
    constraints = inspector.get_unique_constraints(table_name)  # type: ignore[attr-defined]
    return {tuple(constraint["column_names"]) for constraint in constraints}
