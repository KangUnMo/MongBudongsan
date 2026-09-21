from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from types import MappingProxyType

from sqlalchemy import desc, select
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
from mybudongsan.domain.scoring import EvaluationInput, EvaluationResult, evaluate_listing
from mybudongsan.research.contracts import ListingObservation
from mybudongsan.storage.database import Database
from mybudongsan.storage.models import (
    AssessmentModel,
    EvidenceModel,
    ListingModel,
    ListingSnapshotModel,
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
class AssessmentRecord:
    run_id: str
    listing_id: int
    evaluation_input: EvaluationInput
    evaluation_result: EvaluationResult
    created_at: datetime


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
        evaluation_result: EvaluationResult | None = None,
    ) -> AssessmentRecord:
        result = evaluation_result or evaluate_listing(evaluation_input)
        with self._database.session() as session:
            assessment = AssessmentModel(
                run_id=run_id,
                listing_id=listing_id,
                passed_gates=result.eligible,
                score=(Decimal(str(result.total_score)) if result.total_score is not None else None),
                risks={"reasons": list(result.reasons)},
                rationale="; ".join(result.reasons) or None,
                input_payload=evaluation_input.model_dump(mode="json"),
                result_payload=result.model_dump(mode="json"),
                created_at=datetime.now(UTC),
            )
            session.add(assessment)
            session.flush()
            return self._to_record(assessment)

    def save_evaluation(
        self,
        *,
        run_id: str,
        listing_id: int,
        evaluation_input: EvaluationInput,
        evaluation_result: EvaluationResult | None = None,
    ) -> AssessmentRecord:
        return self.save(
            run_id=run_id,
            listing_id=listing_id,
            evaluation_input=evaluation_input,
            evaluation_result=evaluation_result,
        )

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

    @staticmethod
    def _to_record(assessment: AssessmentModel) -> AssessmentRecord:
        return AssessmentRecord(
            run_id=assessment.run_id,
            listing_id=assessment.listing_id,
            evaluation_input=EvaluationInput.model_validate(assessment.input_payload),
            evaluation_result=EvaluationResult.model_validate(assessment.result_payload),
            created_at=assessment.created_at,
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

    def get(self, run_id: str) -> RunRecord:
        with self._database.session() as session:
            run = session.scalar(select(ResearchRunModel).where(ResearchRunModel.run_id == run_id))
            if run is None:
                raise ValueError(f"run not found: {run_id}")
            return self._to_record(run)

    def advance(
        self,
        *,
        run_id: str,
        stage: RunStage,
        checkpoint: dict[str, object],
    ) -> RunRecord:
        """Atomically persist the next completed stage or return its prior checkpoint."""
        with self._database.session() as session:
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
