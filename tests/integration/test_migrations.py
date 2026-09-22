import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, inspect, text

from mybudongsan.domain.requests import MoneyRange, RegionCriterion, SearchRequest
from mybudongsan.domain.scoring import EvaluationInput, evaluate_listing
from mybudongsan.notifications.outbox import NotificationOutbox
from mybudongsan.research.contracts import ListingObservation
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


def test_snapshot_run_ownership_migration_backfills_an_unambiguous_legacy_assessment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    alembic_config, database_url = _migration_config(tmp_path, monkeypatch, "safe-backfill")
    command.upgrade(alembic_config, "0002")
    engine = create_engine(database_url)
    _seed_0002_report_projection_data(engine, ("legacy-run",))
    engine.dispose()

    command.upgrade(alembic_config, "0003")
    engine = create_engine(database_url)
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT run_id FROM listing_snapshots WHERE id = 1")
        ).scalar_one() == "legacy-run"
    engine.dispose()

    projected = AssessmentRepository(Database(database_url)).list_for_run("legacy-run")
    assert len(projected) == 1
    assert projected[0].listing.source_listing_id == "legacy-listing"

    command.downgrade(alembic_config, "0002")
    engine = create_engine(database_url)
    assert "run_id" not in {
        column["name"] for column in inspect(engine).get_columns("listing_snapshots")
    }
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT asking_price FROM listing_snapshots WHERE id = 1")
        ).scalar_one() == 700_000_000
    engine.dispose()


def test_snapshot_run_ownership_migration_leaves_ambiguous_legacy_rows_unowned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    alembic_config, database_url = _migration_config(tmp_path, monkeypatch, "ambiguous-backfill")
    command.upgrade(alembic_config, "0002")
    engine = create_engine(database_url)
    _seed_0002_report_projection_data(engine, ("legacy-run-one", "legacy-run-two"))
    engine.dispose()

    command.upgrade(alembic_config, "0003")
    engine = create_engine(database_url)
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT run_id FROM listing_snapshots WHERE id = 1")
        ).scalar_one() is None
    engine.dispose()

    with pytest.raises(ValueError, match="레거시 매물 스냅샷"):
        AssessmentRepository(Database(database_url)).list_for_run("legacy-run-one")


def test_notification_outbox_migration_replaces_legacy_sent_only_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    alembic_config, database_url = _migration_config(tmp_path, monkeypatch, "notification-outbox")
    command.upgrade(alembic_config, "0003")
    engine = create_engine(database_url)
    legacy_columns = {
        column["name"]: column
        for column in inspect(engine).get_columns("notification_events")
    }
    assert legacy_columns["sent_at"]["nullable"] is False
    assert "status" not in legacy_columns
    legacy_sent_at = "2026-09-22 08:00:00.000000"
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO search_requests (request_id, version, payload, created_at) "
                "VALUES ('notify-request', 1, '{}', :sent_at)"
            ),
            {"sent_at": legacy_sent_at},
        )
        connection.execute(
            text(
                "INSERT INTO research_runs "
                "(run_id, request_id, request_version, status, current_stage, "
                "checkpoint_payload, error_message, started_at, completed_at) "
                "VALUES ('notify-run', 'notify-request', 1, 'completed', 'sync_complete', "
                "'{}', NULL, :sent_at, :sent_at)"
            ),
            {"sent_at": legacy_sent_at},
        )
        connection.execute(
            text(
                "INSERT INTO notification_events "
                "(run_id, event_type, channel, payload, sent_at) "
                "VALUES ('notify-run', 'completed', 'kakao', '{}', :sent_at)"
            ),
            {"sent_at": legacy_sent_at},
        )
    engine.dispose()

    command.upgrade(alembic_config, "0004")
    engine = create_engine(database_url)
    columns = {
        column["name"]: column
        for column in inspect(engine).get_columns("notification_events")
    }
    assert columns["sent_at"]["nullable"] is True
    assert {
        "status",
        "attempt_count",
        "last_error",
        "provider_message_id",
        "created_at",
    } <= columns.keys()
    with engine.connect() as connection:
        legacy = connection.execute(
            text(
                "SELECT status, attempt_count, created_at, sent_at, provider_message_id "
                "FROM notification_events WHERE run_id = 'notify-run'"
            )
        ).mappings().one()
    assert legacy["status"] == "sent"
    assert legacy["attempt_count"] == 1
    assert legacy["created_at"] == legacy["sent_at"]
    assert legacy["provider_message_id"] == "provider_acknowledged"
    engine.dispose()

    command.upgrade(alembic_config, "head")
    engine = create_engine(database_url)
    columns = {
        column["name"]: column
        for column in inspect(engine).get_columns("notification_events")
    }
    assert columns["claim_token"]["nullable"] is True
    with engine.connect() as connection:
        assert connection.execute(
            text(
                "SELECT claim_token FROM notification_events "
                "WHERE run_id = 'notify-run'"
            )
        ).scalar_one() is None
    engine.dispose()


def test_claim_token_migration_recovers_existing_dispatching_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    alembic_config, database_url = _migration_config(
        tmp_path,
        monkeypatch,
        "notification-claim-recovery",
    )
    command.upgrade(alembic_config, "0004")
    engine = create_engine(database_url)
    created_at = "2026-09-23 08:00:00.000000"
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO search_requests (request_id, version, payload, created_at) "
                "VALUES ('recovery-request', 1, '{}', :created_at)"
            ),
            {"created_at": created_at},
        )
        connection.execute(
            text(
                "INSERT INTO research_runs "
                "(run_id, request_id, request_version, status, current_stage, "
                "checkpoint_payload, error_message, started_at, completed_at) "
                "VALUES ('recovery-run', 'recovery-request', 1, 'completed', "
                "'sync_complete', '{}', NULL, :created_at, :created_at)"
            ),
            {"created_at": created_at},
        )
        connection.execute(
            text(
                "INSERT INTO notification_events "
                "(run_id, event_type, channel, payload, status, attempt_count, "
                "last_error, provider_message_id, created_at, sent_at) VALUES "
                "('recovery-run', 'work_started', 'kakao', :first_payload, "
                "'dispatching', 1, NULL, NULL, :created_at, NULL), "
                "('recovery-run', 'researcher_assigned', 'kakao', :second_payload, "
                "'dispatching', 2, NULL, NULL, :created_at, NULL), "
                "('recovery-run', 'specialist_assigned', 'kakao', '{}', "
                "'pending', 0, NULL, NULL, :created_at, NULL), "
                "('recovery-run', 'finalizing', 'kakao', '{}', "
                "'failed', 1, 'safe timeout', NULL, :created_at, NULL), "
                "('recovery-run', 'completed', 'kakao', '{}', "
                "'sent', 1, NULL, 'provider_acknowledged', :created_at, :created_at)"
            ),
            {
                "created_at": created_at,
                "first_payload": json.dumps({"message": "first"}),
                "second_payload": json.dumps({"message": "second"}),
            },
        )
    engine.dispose()

    command.upgrade(alembic_config, "head")
    database = Database(database_url)
    outbox = NotificationOutbox(database)
    dispatching = outbox.status("recovery-run", state="dispatching", channel="kakao")

    assert [record.attempt_count for record in dispatching] == [1, 2]
    assert [record.payload for record in dispatching] == [
        {"message": "first"},
        {"message": "second"},
    ]
    tokens = [record.claim_token for record in dispatching]
    assert all(tokens)
    assert len(set(tokens)) == 2
    with database.engine.connect() as connection:
        nullable_states = connection.execute(
            text(
                "SELECT status, claim_token FROM notification_events "
                "WHERE status IN ('pending', 'failed', 'sent') ORDER BY status"
            )
        ).all()
    assert all(claim_token is None for _, claim_token in nullable_states)

    with pytest.raises(ValueError, match="stale"):
        outbox.mark_sent(
            dispatching[0].event_id,
            "wrong-attempt",
            claim_token="not-the-recovery-token",
        )
    sent = outbox.mark_sent(
        dispatching[0].event_id,
        "recovered-provider-id",
        claim_token=dispatching[0].claim_token or "",
    )
    failed = outbox.mark_failed(
        dispatching[1].event_id,
        "confirmed not delivered",
        claim_token=dispatching[1].claim_token or "",
    )
    assert sent.status == "sent"
    assert failed.status == "failed"


def _migration_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> tuple[Config, str]:
    project_root = Path(__file__).resolve().parents[2]
    database_url = f"sqlite+pysqlite:///{tmp_path / f'{name}.sqlite3'}"
    monkeypatch.setenv("MYBUDONGSAN_DB_URL", database_url)
    alembic_config = Config(str(project_root / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(project_root / "migrations"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)
    return alembic_config, database_url


def _seed_0002_report_projection_data(engine: Engine, run_ids: tuple[str, ...]) -> None:
    observation = ListingObservation(
        source="legacy-fixture",
        source_listing_id="legacy-listing",
        asking_price=700_000_000,
        observed_at=datetime(2026, 9, 21, tzinfo=UTC),
    )
    evaluation_input = EvaluationInput(
        active_listing_confirmed=False,
        minimum_evidence_met=False,
    )
    evaluation_result = evaluate_listing(evaluation_input)
    created_at = "2026-09-21 00:00:00.000000"
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO search_requests (request_id, version, payload, created_at) "
                "VALUES ('legacy-request', 1, :payload, :created_at)"
            ),
            {"payload": json.dumps({}), "created_at": created_at},
        )
        for run_id in run_ids:
            connection.execute(
                text(
                    "INSERT INTO research_runs "
                    "(run_id, request_id, request_version, status, current_stage, checkpoint_payload, "
                    "error_message, started_at, completed_at) "
                    "VALUES (:run_id, 'legacy-request', 1, 'running', 'deep_research_complete', "
                    "'{}', NULL, :created_at, :created_at)"
                ),
                {"run_id": run_id, "created_at": created_at},
            )
        listing_id = connection.execute(
            text(
                "INSERT INTO listings (source, source_listing_id, created_at) "
                "VALUES ('legacy-fixture', 'legacy-listing', :created_at)"
            ),
            {"created_at": created_at},
        ).lastrowid
        assert listing_id is not None
        connection.execute(
            text(
                "INSERT INTO listing_snapshots "
                "(listing_id, asking_price, status, payload, observed_at) "
                "VALUES (:listing_id, 700000000, NULL, :payload, :observed_at)"
            ),
            {
                "listing_id": listing_id,
                "payload": json.dumps(observation.model_dump(mode="json")),
                "observed_at": created_at,
            },
        )
        for run_id in run_ids:
            connection.execute(
                text(
                    "INSERT INTO assessments "
                    "(run_id, listing_id, passed_gates, score, risks, rationale, input_payload, "
                    "result_payload, created_at) "
                    "VALUES (:run_id, :listing_id, :passed_gates, NULL, NULL, NULL, :input_payload, "
                    ":result_payload, :created_at)"
                ),
                {
                    "run_id": run_id,
                    "listing_id": listing_id,
                    "passed_gates": evaluation_result.eligible,
                    "input_payload": json.dumps(evaluation_input.model_dump(mode="json")),
                    "result_payload": json.dumps(evaluation_result.model_dump(mode="json")),
                    "created_at": created_at,
                },
            )


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
