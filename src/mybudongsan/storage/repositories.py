from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from types import MappingProxyType

from sqlalchemy import desc, select, text
from sqlalchemy.orm import Session

from mybudongsan.domain.listings import (
    build_coarse_fallback_fingerprint,
    build_listing_key,
)
from mybudongsan.domain.requests import SearchRequest
from mybudongsan.domain.runs import (
    InvalidTransition,
    RunStage,
    RunStatus,
    validate_transition,
)
from mybudongsan.domain.scoring import (
    DIMENSIONS,
    EvaluationInput,
    EvaluationResult,
    evaluate_listing,
)
from mybudongsan.integrations.google_auth import redact_google_text
from mybudongsan.notifications.policy import (
    TERMINAL_EVENT_TYPES,
    NotificationEvent,
    NotificationEventType,
)
from mybudongsan.research.contracts import EvidenceObservation, ListingObservation
from mybudongsan.storage.database import Database
from mybudongsan.storage.models import (
    AssessmentModel,
    EvidenceModel,
    ListingModel,
    ListingSnapshotModel,
    NotificationEventModel,
    ReportModel,
    ResearchRunModel,
    SearchRequestModel,
)


class RequestRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def save_version(self, request: SearchRequest) -> None:
        with self._database.session() as session:
            existing = session.scalar(
                select(SearchRequestModel.id).where(
                    SearchRequestModel.request_id == request.request_id,
                    SearchRequestModel.version == request.version,
                )
            )
            if existing is not None:
                raise ValueError(
                    f"request version already exists: {request.request_id}/{request.version}"
                )
            session.add(
                SearchRequestModel(
                    request_id=request.request_id,
                    version=request.version,
                    payload=request.model_dump(mode="json"),
                    created_at=datetime.now(UTC),
                )
            )

    def get_version(self, request_id: str, version: int) -> SearchRequest:
        with self._database.session() as session:
            stored = session.scalar(
                select(SearchRequestModel).where(
                    SearchRequestModel.request_id == request_id,
                    SearchRequestModel.version == version,
                )
            )
            if stored is None:
                raise ValueError(f"request version not found: {request_id}/{version}")
            return SearchRequest.model_validate(stored.payload)


@dataclass(frozen=True)
class ListingUpsertResult:
    listing_id: int
    created: bool
    duplicate_suspected: bool


class ListingRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def ingest_bundle(
        self,
        run_id: str,
        observations: tuple[ListingObservation, ...],
    ) -> tuple[ListingUpsertResult, ...]:
        with self._database.session() as session:
            return self.ingest_bundle_in_session(session, run_id, observations)

    def ingest_bundle_in_session(
        self,
        session: Session,
        run_id: str,
        observations: tuple[ListingObservation, ...],
    ) -> tuple[ListingUpsertResult, ...]:
        self._require_run(session, run_id)
        return tuple(
            self._upsert_observation(session, run_id, observation)
            for observation in observations
        )

    @staticmethod
    def _require_run(session: Session, run_id: str) -> None:
        run_exists = session.scalar(
            select(ResearchRunModel.id).where(ResearchRunModel.run_id == run_id)
        )
        if run_exists is None:
            raise ValueError(f"run not found: {run_id}")

    def _upsert_observation(
        self,
        session: Session,
        run_id: str,
        observation: ListingObservation,
    ) -> ListingUpsertResult:
        listing_key = build_listing_key(observation)
        stored_source_id = listing_key.removeprefix(f"{observation.source}:")
        listing = session.scalar(
            select(ListingModel).where(
                ListingModel.source == observation.source,
                ListingModel.source_listing_id == stored_source_id,
            )
        )
        created = listing is None
        if listing is None:
            listing = ListingModel(
                source=observation.source,
                source_listing_id=stored_source_id,
                created_at=observation.observed_at,
            )
            session.add(listing)
            session.flush()
        assert listing is not None
        duplicate_suspected = observation.source_listing_id is None and created and (
            self._has_uncertain_fallback_match(session, observation, listing.id)
        )
        evidence = self._validate_evidence_ownership(
            session,
            run_id,
            listing.id,
            observation.raw_evidence_ids,
        )
        session.add(
            ListingSnapshotModel(
                listing_id=listing.id,
                run_id=run_id,
                asking_price=observation.asking_price,
                status=observation.status,
                payload={
                    **observation.model_dump(mode="json"),
                    "coarse_fallback_fingerprint": build_coarse_fallback_fingerprint(observation),
                },
                observed_at=observation.observed_at,
            )
        )
        for item in evidence:
            item.listing_id = listing.id
        session.flush()
        return ListingUpsertResult(
            listing_id=listing.id,
            created=created,
            duplicate_suspected=duplicate_suspected,
        )

    @staticmethod
    def _validate_evidence_ownership(
        session: Session,
        run_id: str,
        listing_id: int,
        evidence_ids: tuple[int, ...],
    ) -> tuple[EvidenceModel, ...]:
        if not evidence_ids:
            return ()
        evidence_by_id = {
            evidence.id: evidence
            for evidence in session.scalars(
                select(EvidenceModel).where(EvidenceModel.id.in_(evidence_ids))
            )
        }
        missing_ids = sorted(set(evidence_ids) - evidence_by_id.keys())
        if missing_ids:
            raise ValueError(f"evidence not found: {missing_ids}")
        evidence = tuple(evidence_by_id[evidence_id] for evidence_id in evidence_ids)
        wrong_run_ids = sorted(item.id for item in evidence if item.run_id != run_id)
        if wrong_run_ids:
            raise ValueError(f"evidence does not belong to run {run_id}: {wrong_run_ids}")
        reassigned_ids = sorted(
            item.id
            for item in evidence
            if item.listing_id is not None and item.listing_id != listing_id
        )
        if reassigned_ids:
            raise ValueError(f"evidence already linked to a different listing: {reassigned_ids}")
        return evidence

    @staticmethod
    def _has_uncertain_fallback_match(
        session: Session,
        observation: ListingObservation,
        listing_id: int,
    ) -> bool:
        candidates = session.scalars(
            select(ListingModel).where(
                ListingModel.source == observation.source,
                ListingModel.source_listing_id.startswith("fallback:"),
                ListingModel.id != listing_id,
            )
        )
        coarse_fingerprint = build_coarse_fallback_fingerprint(observation)
        for candidate in candidates:
            snapshot = session.scalar(
                select(ListingSnapshotModel)
                .where(ListingSnapshotModel.listing_id == candidate.id)
                .order_by(desc(ListingSnapshotModel.observed_at), desc(ListingSnapshotModel.id))
            )
            if (
                snapshot is not None
                and snapshot.payload.get("coarse_fallback_fingerprint") == coarse_fingerprint
            ):
                return True
        return False


@dataclass(frozen=True)
class PersistedEvidence:
    local_evidence_id: int
    evidence_id: int


@dataclass(frozen=True)
class EvidenceRecord:
    evidence_id: int
    claim: str
    source_url: str
    source_type: str
    excerpt: str | None
    accessed_at: datetime


class EvidenceRepository:
    """Persist and project evidence while preserving run ownership."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def save_for_run(
        self,
        run_id: str,
        evidence: tuple[EvidenceObservation, ...],
    ) -> tuple[PersistedEvidence, ...]:
        with self._database.session() as session:
            return self.save_for_run_in_session(session, run_id, evidence)

    @staticmethod
    def save_for_run_in_session(
        session: Session,
        run_id: str,
        evidence: tuple[EvidenceObservation, ...],
    ) -> tuple[PersistedEvidence, ...]:
        ListingRepository._require_run(session, run_id)
        stored: list[PersistedEvidence] = []
        for item in evidence:
            model = EvidenceModel(
                run_id=run_id,
                listing_id=None,
                claim=item.claim,
                source_url=item.source_url,
                source_type=item.source_type,
                excerpt=item.excerpt,
                accessed_at=item.accessed_at,
            )
            session.add(model)
            session.flush()
            stored.append(
                PersistedEvidence(
                    local_evidence_id=item.evidence_id,
                    evidence_id=model.id,
                )
            )
        return tuple(stored)

    def list_for_run(
        self,
        run_id: str,
        *,
        evidence_ids: tuple[int, ...] | None = None,
    ) -> tuple[EvidenceRecord, ...]:
        with self._database.session() as session:
            ListingRepository._require_run(session, run_id)
            statement = select(EvidenceModel).where(EvidenceModel.run_id == run_id)
            if evidence_ids is not None:
                statement = statement.where(EvidenceModel.id.in_(evidence_ids))
            evidence = session.scalars(statement.order_by(EvidenceModel.id))
            return tuple(
                EvidenceRecord(
                    evidence_id=item.id,
                    claim=item.claim,
                    source_url=item.source_url,
                    source_type=item.source_type,
                    excerpt=item.excerpt,
                    accessed_at=item.accessed_at,
                )
                for item in evidence
            )


@dataclass(frozen=True)
class AssessmentRecord:
    run_id: str
    listing_id: int
    evaluation_input: EvaluationInput
    evaluation_result: EvaluationResult
    created_at: datetime


@dataclass(frozen=True)
class AssessedListingRecord:
    listing_id: int
    listing: ListingObservation
    assessment: AssessmentRecord


class AssessmentRepository:
    """Persist an assessment together with all data needed to explain it."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def save(
        self,
        *,
        run_id: str,
        listing_id: int,
        evaluation_input: EvaluationInput,
        session: Session | None = None,
    ) -> AssessmentRecord:
        if session is not None:
            return self._save_in_session(session, run_id, listing_id, evaluation_input)
        with self._database.session() as transaction:
            return self._save_in_session(transaction, run_id, listing_id, evaluation_input)

    def _save_in_session(
        self,
        session: Session,
        run_id: str,
        listing_id: int,
        evaluation_input: EvaluationInput,
    ) -> AssessmentRecord:
        ListingRepository._require_run(session, run_id)
        self._require_listing(session, listing_id)
        canonical_input = EvaluationInput.model_validate(evaluation_input.model_dump(mode="json"))
        self._validate_evidence_ownership(session, run_id, listing_id, canonical_input)
        result = evaluate_listing(canonical_input)
        assessment = AssessmentModel(
            run_id=run_id,
            listing_id=listing_id,
            passed_gates=result.eligible,
            score=(Decimal(str(result.total_score)) if result.total_score is not None else None),
            risks={"reasons": list(result.reasons)},
            rationale="; ".join(result.reasons) or None,
            input_payload=canonical_input.model_dump(mode="json"),
            result_payload=result.model_dump(mode="json"),
            created_at=datetime.now(UTC),
        )
        session.add(assessment)
        session.flush()
        return self._to_record(assessment)

    def get(self, run_id: str, listing_id: int) -> AssessmentRecord:
        with self._database.session() as session:
            assessment = session.scalar(
                select(AssessmentModel).where(
                    AssessmentModel.run_id == run_id,
                    AssessmentModel.listing_id == listing_id,
                )
            )
            if assessment is None:
                raise ValueError(f"assessment not found: {run_id}/{listing_id}")
            return self._to_record(assessment)

    def list_for_run(
        self,
        run_id: str,
        *,
        limit: int = 3,
    ) -> tuple[AssessedListingRecord, ...]:
        with self._database.session() as session:
            ListingRepository._require_run(session, run_id)
            assessments = tuple(
                session.scalars(
                    select(AssessmentModel)
                    .where(AssessmentModel.run_id == run_id)
                    .order_by(AssessmentModel.id)
                    .limit(limit)
                )
            )
            projected: list[AssessedListingRecord] = []
            for assessment in assessments:
                snapshot = session.scalar(
                    select(ListingSnapshotModel)
                    .where(
                        ListingSnapshotModel.listing_id == assessment.listing_id,
                        ListingSnapshotModel.run_id == run_id,
                    )
                    .order_by(
                        desc(ListingSnapshotModel.observed_at),
                        desc(ListingSnapshotModel.id),
                    )
                )
                if snapshot is None:
                    legacy_snapshot = session.scalar(
                        select(ListingSnapshotModel.id)
                        .where(
                            ListingSnapshotModel.listing_id == assessment.listing_id,
                            ListingSnapshotModel.run_id.is_(None),
                        )
                        .limit(1)
                    )
                    if legacy_snapshot is not None:
                        raise ValueError(
                            "레거시 매물 스냅샷의 실행 소유권이 불명확하거나 "
                            "0003 마이그레이션이 필요합니다: "
                            f"{assessment.listing_id}"
                        )
                    raise ValueError(
                        f"listing snapshot not found: {assessment.listing_id}"
                    )
                projected.append(
                    AssessedListingRecord(
                        listing_id=assessment.listing_id,
                        listing=ListingObservation.model_validate(snapshot.payload),
                        assessment=self._to_record(assessment),
                    )
                )
            return tuple(projected)

    @staticmethod
    def _require_listing(session: Session, listing_id: int) -> None:
        listing_exists = session.scalar(select(ListingModel.id).where(ListingModel.id == listing_id))
        if listing_exists is None:
            raise ValueError(f"listing not found: {listing_id}")

    @staticmethod
    def _validate_evidence_ownership(
        session: Session,
        run_id: str,
        listing_id: int,
        evaluation_input: EvaluationInput,
    ) -> None:
        evidence_ids = tuple(
            evidence_id
            for dimension in DIMENSIONS
            for evidence_id in getattr(evaluation_input.evidence_ids_by_dimension, dimension)
        )
        if not evidence_ids:
            return
        evidence_by_id = {
            evidence.id: evidence
            for evidence in session.scalars(
                select(EvidenceModel).where(EvidenceModel.id.in_(evidence_ids))
            )
        }
        missing_ids = sorted(set(evidence_ids) - evidence_by_id.keys())
        if missing_ids:
            raise ValueError(f"evidence not found: {missing_ids}")
        evidence = tuple(evidence_by_id[evidence_id] for evidence_id in evidence_ids)
        wrong_run_ids = sorted(item.id for item in evidence if item.run_id != run_id)
        if wrong_run_ids:
            raise ValueError(f"evidence does not belong to run {run_id}: {wrong_run_ids}")
        other_listing_ids = sorted(
            item.id
            for item in evidence
            if item.listing_id is not None and item.listing_id != listing_id
        )
        if other_listing_ids:
            raise ValueError(
                f"evidence linked to a different listing: {other_listing_ids}"
            )

    @staticmethod
    def _to_record(assessment: AssessmentModel) -> AssessmentRecord:
        return AssessmentRecord(
            run_id=assessment.run_id,
            listing_id=assessment.listing_id,
            evaluation_input=EvaluationInput.model_validate(assessment.input_payload),
            evaluation_result=EvaluationResult.model_validate(assessment.result_payload),
            created_at=assessment.created_at,
        )


class ReportRepository:
    """Persist rendered report content after artifact publication succeeds."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def save_markdown(
        self, run_id: str, content: str, *, session: Session | None = None
    ) -> None:
        if session is not None:
            self._save_markdown_in_session(session, run_id, content)
            return
        with self._database.session() as transaction:
            self._save_markdown_in_session(transaction, run_id, content)

    @staticmethod
    def _save_markdown_in_session(session: Session, run_id: str, content: str) -> None:
        ListingRepository._require_run(session, run_id)
        session.add(
            ReportModel(
                run_id=run_id,
                format="markdown",
                content=content,
                drive_file_id=None,
                created_at=datetime.now(UTC),
            )
        )


@dataclass(frozen=True)
class NotificationRecord:
    event_id: int
    run_id: str
    event_type: NotificationEventType
    channel: str
    payload: dict[str, object]
    status: str
    attempt_count: int
    last_error: str | None
    provider_message_id: str | None
    created_at: datetime
    sent_at: datetime | None


class NotificationRepository:
    """Persist bounded notification deliveries in the canonical SQLite database."""

    _MAX_EVENT_TYPES = 5
    _MAX_NONTERMINAL_EVENT_TYPES = 4

    def __init__(self, database: Database) -> None:
        self._database = database

    def enqueue(
        self,
        run_id: str,
        event: NotificationEvent,
    ) -> tuple[NotificationRecord, ...]:
        channels = ("kakao", "gmail") if event.type in TERMINAL_EVENT_TYPES else ("kakao",)
        with self._write_session() as session:
            ListingRepository._require_run(session, run_id)
            existing = tuple(
                session.scalars(
                    select(NotificationEventModel)
                    .where(
                        NotificationEventModel.run_id == run_id,
                        NotificationEventModel.event_type == event.type.value,
                    )
                    .order_by(NotificationEventModel.id)
                )
            )
            if existing:
                return tuple(self._to_record(item) for item in existing)

            event_types = {
                NotificationEventType(value)
                for value in session.scalars(
                    select(NotificationEventModel.event_type).where(
                        NotificationEventModel.run_id == run_id
                    )
                )
            }
            terminal_types = event_types & TERMINAL_EVENT_TYPES
            if event.type in TERMINAL_EVENT_TYPES:
                if terminal_types:
                    raise ValueError("a different terminal notification is already stored")
                if len(event_types) >= self._MAX_EVENT_TYPES:
                    raise ValueError("notification lifecycle event cap exceeded")
            else:
                nonterminal_count = len(event_types - TERMINAL_EVENT_TYPES)
                if terminal_types or nonterminal_count >= self._MAX_NONTERMINAL_EVENT_TYPES:
                    raise ValueError("notification terminal event slot must remain reserved")

            created_at = datetime.now(UTC)
            payload: dict[str, object] = {
                "run_id": run_id,
                "event_type": event.type.value,
                "message": event.message,
            }
            models = [
                NotificationEventModel(
                    run_id=run_id,
                    event_type=event.type.value,
                    channel=channel,
                    payload=payload,
                    status="pending",
                    attempt_count=0,
                    last_error=None,
                    provider_message_id=None,
                    created_at=created_at,
                    sent_at=None,
                )
                for channel in channels
            ]
            session.add_all(models)
            session.flush()
            return tuple(self._to_record(item) for item in models)

    def pending(self, run_id: str, *, channel: str) -> tuple[NotificationRecord, ...]:
        if channel not in {"kakao", "gmail"}:
            raise ValueError("channel must be kakao or gmail")
        with self._write_session() as session:
            ListingRepository._require_run(session, run_id)
            models = tuple(
                session.scalars(
                    select(NotificationEventModel)
                    .where(
                        NotificationEventModel.run_id == run_id,
                        NotificationEventModel.channel == channel,
                        NotificationEventModel.status.in_(("pending", "failed")),
                    )
                    .order_by(NotificationEventModel.id)
                )
            )
            for model in models:
                model.status = "dispatching"
                model.attempt_count += 1
            session.flush()
            return tuple(self._to_record(item) for item in models)

    def mark_sent(self, event_id: int, provider_message_id: str) -> NotificationRecord:
        provider_message_id = provider_message_id.strip()
        if not provider_message_id:
            raise ValueError("provider message ID or provider_acknowledged is required")
        with self._write_session() as session:
            model = self._get(session, event_id)
            if model.status != "dispatching":
                raise ValueError("notification event must be dispatching before acknowledgement")
            model.status = "sent"
            model.last_error = None
            model.provider_message_id = provider_message_id
            model.sent_at = datetime.now(UTC)
            session.flush()
            return self._to_record(model)

    def mark_failed(self, event_id: int, error: str) -> NotificationRecord:
        safe_error = redact_google_text(error).strip()[:500]
        with self._write_session() as session:
            model = self._get(session, event_id)
            if model.status != "dispatching":
                raise ValueError("notification event must be dispatching before failure")
            model.status = "failed"
            model.last_error = safe_error
            model.provider_message_id = None
            model.sent_at = None
            session.flush()
            return self._to_record(model)

    @contextmanager
    def _write_session(self) -> Iterator[Session]:
        with self._database.session() as session:
            if self._database.engine.dialect.name == "sqlite":
                session.execute(text("BEGIN IMMEDIATE"))
            yield session

    @staticmethod
    def _get(session: Session, event_id: int) -> NotificationEventModel:
        model = session.get(NotificationEventModel, event_id)
        if model is None:
            raise ValueError(f"notification event not found: {event_id}")
        return model

    @staticmethod
    def _to_record(model: NotificationEventModel) -> NotificationRecord:
        return NotificationRecord(
            event_id=model.id,
            run_id=model.run_id,
            event_type=NotificationEventType(model.event_type),
            channel=model.channel,
            payload=dict(model.payload),
            status=model.status,
            attempt_count=model.attempt_count,
            last_error=model.last_error,
            provider_message_id=model.provider_message_id,
            created_at=model.created_at,
            sent_at=model.sent_at,
        )


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    request_id: str
    request_version: int
    status: RunStatus
    current_stage: RunStage | None
    checkpoint: dict[str, object]
    checkpoints: Mapping[RunStage, StageCheckpoint]
    error_message: str | None
    completed_at: datetime | None


@dataclass(frozen=True)
class StageCheckpoint:
    checkpoint: dict[str, object]
    completed_at: datetime
    idempotency_key: str


class RunRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def create(
        self,
        run_id: str,
        request_id: str,
        request_version: int,
    ) -> RunRecord:
        completed_at = datetime.now(UTC)
        initial_checkpoint = StageCheckpoint(
            checkpoint={"request_id": request_id, "request_version": request_version},
            completed_at=completed_at,
            idempotency_key=f"{run_id}:{RunStage.REQUEST_APPROVED.value}",
        )
        with self._database.session() as session:
            run = ResearchRunModel(
                run_id=run_id,
                request_id=request_id,
                request_version=request_version,
                status=RunStatus.RUNNING.value,
                current_stage=RunStage.REQUEST_APPROVED.value,
                checkpoint_payload=self._serialize_history(
                    {RunStage.REQUEST_APPROVED: initial_checkpoint}
                ),
                started_at=datetime.now(UTC),
                completed_at=completed_at,
            )
            session.add(run)
            session.flush()
            return self._to_record(run)

    def get(self, run_id: str, *, session: Session | None = None) -> RunRecord:
        if session is not None:
            return self._get_in_session(session, run_id)
        with self._database.session() as transaction:
            return self._get_in_session(transaction, run_id)

    @staticmethod
    def _get_in_session(session: Session, run_id: str) -> RunRecord:
        run = session.scalar(select(ResearchRunModel).where(ResearchRunModel.run_id == run_id))
        if run is None:
            raise ValueError(f"run not found: {run_id}")
        return RunRepository._to_record(run)

    def advance(
        self,
        *,
        run_id: str,
        stage: RunStage,
        checkpoint: dict[str, object],
        session: Session | None = None,
    ) -> RunRecord:
        """Atomically persist the next completed stage or return its prior checkpoint."""
        if session is not None:
            return self._advance_in_session(session, run_id, stage, checkpoint)
        with self._database.session() as transaction:
            return self._advance_in_session(transaction, run_id, stage, checkpoint)

    def _advance_in_session(
        self,
        session: Session,
        run_id: str,
        stage: RunStage,
        checkpoint: dict[str, object],
    ) -> RunRecord:
        run = session.scalar(select(ResearchRunModel).where(ResearchRunModel.run_id == run_id))
        if run is None:
            raise ValueError(f"run not found: {run_id}")
        history = self._history(run)
        if stage in history:
            return self._to_record(run, checkpoint_stage=stage)
        if run.current_stage is None:
            raise InvalidTransition("run has not completed request approval")
        validate_transition(RunStage(run.current_stage), stage)

        completed_at = datetime.now(UTC)
        history[stage] = StageCheckpoint(
            checkpoint=dict(checkpoint),
            completed_at=completed_at,
            idempotency_key=f"{run_id}:{stage.value}",
        )
        run.current_stage = stage.value
        run.status = (
            RunStatus.COMPLETED.value
            if stage is RunStage.SYNC_COMPLETE
            else RunStatus.RUNNING.value
        )
        run.checkpoint_payload = self._serialize_history(history)
        run.error_message = None
        run.completed_at = completed_at
        session.flush()
        return self._to_record(run)

    def mark_resumable(self, run_id: str, message: str) -> RunRecord:
        with self._database.session() as session:
            run = session.scalar(select(ResearchRunModel).where(ResearchRunModel.run_id == run_id))
            if run is None:
                raise ValueError(f"run not found: {run_id}")
            if run.status == RunStatus.COMPLETED.value:
                raise InvalidTransition("completed runs cannot become resumable")
            run.status = RunStatus.RESUMABLE.value
            run.error_message = message
            session.flush()
            return self._to_record(run)

    @staticmethod
    def _serialize_history(
        history: Mapping[RunStage, StageCheckpoint],
    ) -> dict[str, object]:
        return {
            "stages": {
                stage.value: {
                    "checkpoint": entry.checkpoint,
                    "completed_at": entry.completed_at.isoformat(),
                    "idempotency_key": entry.idempotency_key,
                }
                for stage, entry in history.items()
            }
        }

    @staticmethod
    def _history(run: ResearchRunModel) -> dict[RunStage, StageCheckpoint]:
        payload = run.checkpoint_payload or {}
        stored_stages = payload.get("stages")
        if isinstance(stored_stages, dict):
            return {
                RunStage(stage): RunRepository._to_stage_checkpoint(run.run_id, stage, entry)
                for stage, entry in stored_stages.items()
            }
        if run.current_stage is None:
            return {}
        return {
            RunStage(run.current_stage): RunRepository._to_stage_checkpoint(
                run.run_id,
                run.current_stage,
                payload,
                fallback_completed_at=run.completed_at or run.started_at,
            )
        }

    @staticmethod
    def _to_stage_checkpoint(
        run_id: str,
        stage: str,
        entry: object,
        *,
        fallback_completed_at: datetime | None = None,
    ) -> StageCheckpoint:
        if not isinstance(entry, dict):
            raise TypeError(f"invalid checkpoint history for run: {run_id}")
        checkpoint = entry.get("checkpoint", entry)
        completed_at = entry.get("completed_at", fallback_completed_at)
        idempotency_key = entry.get("idempotency_key", f"{run_id}:{stage}")
        if not isinstance(checkpoint, dict):
            raise TypeError(f"invalid checkpoint payload for run: {run_id}")
        if isinstance(completed_at, str):
            completed_at = datetime.fromisoformat(completed_at)
        if not isinstance(completed_at, datetime):
            raise TypeError(f"invalid checkpoint completion time for run: {run_id}")
        if not isinstance(idempotency_key, str):
            raise TypeError(f"invalid checkpoint idempotency key for run: {run_id}")
        return StageCheckpoint(
            checkpoint=dict(checkpoint),
            completed_at=completed_at,
            idempotency_key=idempotency_key,
        )

    @staticmethod
    def _to_record(run: ResearchRunModel, checkpoint_stage: RunStage | None = None) -> RunRecord:
        history = RunRepository._history(run)
        current_stage = RunStage(run.current_stage) if run.current_stage is not None else None
        selected_stage = checkpoint_stage or current_stage
        checkpoint = history[selected_stage].checkpoint if selected_stage is not None else {}
        return RunRecord(
            run_id=run.run_id,
            request_id=run.request_id,
            request_version=run.request_version,
            status=RunStatus(run.status),
            current_stage=current_stage,
            checkpoint=dict(checkpoint),
            checkpoints=MappingProxyType(history),
            error_message=run.error_message,
            completed_at=run.completed_at,
        )
