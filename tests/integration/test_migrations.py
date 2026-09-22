import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, inspect, text

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
    legacy_listing_id = _seed_legacy_assessment(engine)
    engine.dispose()

    command.upgrade(alembic_config, "head")
    engine = create_engine(database_url)
    columns = {column["name"]: column for column in inspect(engine).get_columns("assessments")}
    assert {"input_payload", "result_payload"} <= columns.keys()
    assert columns["input_payload"]["nullable"] is False
    assert columns["result_payload"]["nullable"] is False
    engine.dispose()

    legacy_record = AssessmentRepository(Database(database_url)).get(
        "legacy-migration-run", legacy_listing_id
    )
    assert legacy_record.evaluation_input.required_passed is False
    assert legacy_record.evaluation_input.excluded_passed is False
    assert legacy_record.evaluation_input.active_listing_confirmed is False
    assert legacy_record.evaluation_input.minimum_evidence_met is False
    assert legacy_record.evaluation_result.eligible is False
    assert legacy_record.evaluation_result.recommendable is False
    assert legacy_record.evaluation_result.total_score is None
    assert legacy_record.evaluation_result.reasons == ("legacy_assessment_unverified",)

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

    command.downgrade(alembic_config, "0001")
    engine = create_engine(database_url)
    legacy_columns = {column["name"] for column in inspect(engine).get_columns("assessments")}
    assert "input_payload" not in legacy_columns
    assert "result_payload" not in legacy_columns
    with engine.connect() as connection:
        legacy_row = connection.execute(
            text(
                "SELECT passed_gates, score, risks, rationale FROM assessments "
                "WHERE run_id = 'legacy-migration-run'"
            )
        ).mappings().one()
    assert legacy_row["passed_gates"] == 1
    assert legacy_row["score"] == 71.5
    assert json.loads(legacy_row["risks"]) == {"legacy_risk": "preserve"}
    assert legacy_row["rationale"] == "legacy rationale"
    engine.dispose()


def test_listing_snapshot_run_ownership_migration_upgrades_and_downgrades(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = Path(__file__).resolve().parents[2]
    database_path = tmp_path / "snapshot-ownership.sqlite3"
    database_url = f"sqlite+pysqlite:///{database_path}"
    monkeypatch.setenv("MYBUDONGSAN_DB_URL", database_url)
    alembic_config = Config(str(project_root / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(project_root / "migrations"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)

    command.upgrade(alembic_config, "0002")
    engine = create_engine(database_url)
    assert "run_id" not in {column["name"] for column in inspect(engine).get_columns("listing_snapshots")}
    engine.dispose()

    command.upgrade(alembic_config, "head")
    engine = create_engine(database_url)
    columns = {column["name"]: column for column in inspect(engine).get_columns("listing_snapshots")}
    assert columns["run_id"]["nullable"] is True
    assert "run_id" in {
        indexed_column
        for index in inspect(engine).get_indexes("listing_snapshots")
        for indexed_column in index["column_names"]
    }
    engine.dispose()

    command.downgrade(alembic_config, "0002")
    engine = create_engine(database_url)
    assert "run_id" not in {column["name"] for column in inspect(engine).get_columns("listing_snapshots")}
    engine.dispose()


def _seed_legacy_assessment(engine: Engine) -> int:
    created_at = "2026-09-21 00:00:00.000000"
    connection = engine.connect()
    transaction = connection.begin()
    try:
        connection.execute(
            text(
                "INSERT INTO search_requests (request_id, version, payload, created_at) "
                "VALUES (:request_id, :version, :payload, :created_at)"
            ),
            {
                "request_id": "legacy-migration-request",
                "version": 1,
                "payload": json.dumps({"legacy": True}),
                "created_at": created_at,
            },
        )
        connection.execute(
            text(
                "INSERT INTO research_runs "
                "(run_id, request_id, request_version, status, current_stage, checkpoint_payload, "
                "error_message, started_at, completed_at) "
                "VALUES (:run_id, :request_id, :request_version, :status, :current_stage, "
                ":checkpoint_payload, :error_message, :started_at, :completed_at)"
            ),
            {
                "run_id": "legacy-migration-run",
                "request_id": "legacy-migration-request",
                "request_version": 1,
                "status": "completed",
                "current_stage": None,
                "checkpoint_payload": None,
                "error_message": None,
                "started_at": created_at,
                "completed_at": created_at,
            },
        )
        listing_result = connection.execute(
            text(
                "INSERT INTO listings (source, source_listing_id, created_at) "
                "VALUES (:source, :source_listing_id, :created_at)"
            ),
            {
                "source": "legacy-fixture",
                "source_listing_id": "legacy-listing",
                "created_at": created_at,
            },
        )
        listing_id = int(listing_result.lastrowid)
        connection.execute(
            text(
                "INSERT INTO assessments "
                "(run_id, listing_id, passed_gates, score, risks, rationale, created_at) "
                "VALUES (:run_id, :listing_id, :passed_gates, :score, :risks, :rationale, :created_at)"
            ),
            {
                "run_id": "legacy-migration-run",
                "listing_id": listing_id,
                "passed_gates": True,
                "score": 71.5,
                "risks": json.dumps({"legacy_risk": "preserve"}),
                "rationale": "legacy rationale",
                "created_at": created_at,
            },
        )
        transaction.commit()
    except Exception:
        transaction.rollback()
        raise
    finally:
        connection.close()
    return listing_id


def _unique_constraints(inspector: object, table_name: str) -> set[tuple[str, ...]]:
    constraints = inspector.get_unique_constraints(table_name)  # type: ignore[attr-defined]
    return {tuple(constraint["column_names"]) for constraint in constraints}
