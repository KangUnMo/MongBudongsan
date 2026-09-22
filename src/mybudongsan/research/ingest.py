from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256

from sqlalchemy.orm import Session

from mybudongsan.domain.runs import RunStage
from mybudongsan.domain.scoring import DIMENSIONS, DimensionEvidence
from mybudongsan.research.contracts import (
    DeepAssessmentObservation,
    ListingObservation,
    ResearchBundle,
)
from mybudongsan.storage.database import Database
from mybudongsan.storage.repositories import (
    AssessmentRepository,
    EvidenceRepository,
    ListingRepository,
    ListingUpsertResult,
    RunRepository,
)


@dataclass(frozen=True)
class IngestSummary:
    created: int
    updated: int
    duplicate_suspected: bool
    results: tuple[ListingUpsertResult, ...]


class ListingIngestService:
    def __init__(self, listing_repository: ListingRepository) -> None:
        self._listing_repository = listing_repository

    def ingest(self, run_id: str, bundle: ResearchBundle) -> IngestSummary:
        results = self._listing_repository.ingest_bundle(run_id, _observations(bundle))
        return self._summary(results)

    def ingest_in_session(
        self, session: Session, run_id: str, bundle: ResearchBundle
    ) -> IngestSummary:
        results = self._listing_repository.ingest_bundle_in_session(
            session, run_id, _observations(bundle)
        )
        return self._summary(results)

    @staticmethod
    def _summary(results: tuple[ListingUpsertResult, ...]) -> IngestSummary:
        return IngestSummary(
            created=sum(result.created for result in results),
            updated=sum(not result.created for result in results),
            duplicate_suspected=any(result.duplicate_suspected for result in results),
            results=results,
        )


class ResearchBundleIngestService:
    """Atomically persist one evidence-backed research bundle and its checkpoints."""

    def __init__(self, database: Database) -> None:
        self._database = database
        self._run_repository = RunRepository(database)
        self._evidence_repository = EvidenceRepository(database)
        self._listing_service = ListingIngestService(ListingRepository(database))
        self._assessment_repository = AssessmentRepository(database)

    def ingest(self, run_id: str, bundle: ResearchBundle) -> IngestSummary:
        fingerprint = _bundle_fingerprint(bundle)
        with self._database.session() as session:
            run = self._run_repository.get(run_id, session=session)
            completed = run.checkpoints.get(RunStage.DEEP_RESEARCH_COMPLETE)
            if completed is not None:
                return _retry_summary(completed.checkpoint, fingerprint)
            if run.current_stage is not RunStage.REQUEST_APPROVED:
                raise ValueError("조사 실행이 수집을 시작할 수 있는 단계가 아닙니다")

            persisted_bundle = self._persist_evidence(session, run_id, bundle)
            summary = self._listing_service.ingest_in_session(session, run_id, persisted_bundle)
            deep_count = len(persisted_bundle.deep_assessments)
            deep_results = summary.results[-deep_count:] if deep_count else ()
            for deep_assessment, listing_result in zip(
                persisted_bundle.deep_assessments,
                deep_results,
                strict=True,
            ):
                self._assessment_repository.save(
                    run_id=run_id,
                    listing_id=listing_result.listing_id,
                    evaluation_input=deep_assessment.evaluation_input,
                    session=session,
                )
            self._run_repository.advance(
                run_id=run_id,
                stage=RunStage.DISCOVERY_COMPLETE,
                checkpoint={"discovered_count": len(bundle.discovered), "created": summary.created},
                session=session,
            )
            self._run_repository.advance(
                run_id=run_id,
                stage=RunStage.FILTER_COMPLETE,
                checkpoint={"candidate_count": len(bundle.verified)},
                session=session,
            )
            self._run_repository.advance(
                run_id=run_id,
                stage=RunStage.VERIFICATION_COMPLETE,
                checkpoint={"verified_count": len(bundle.verified)},
                session=session,
            )
            self._run_repository.advance(
                run_id=run_id,
                stage=RunStage.DEEP_RESEARCH_COMPLETE,
                checkpoint={
                    "deep_assessment_count": len(bundle.deep_assessments),
                    "ingest_fingerprint": fingerprint,
                    "created": summary.created,
                    "updated": summary.updated,
                    "duplicate_suspected": summary.duplicate_suspected,
                },
                session=session,
            )
            return summary

    def _persist_evidence(
        self, session: Session, run_id: str, bundle: ResearchBundle
    ) -> ResearchBundle:
        evidence = tuple(
            item for assessment in bundle.deep_assessments for item in assessment.evidence
        )
        persisted = self._evidence_repository.save_for_run_in_session(session, run_id, evidence)
        evidence_id_map = {item.local_evidence_id: item.evidence_id for item in persisted}
        return bundle.model_copy(
            update={
                "deep_assessments": tuple(
                    _remap_deep_assessment(assessment, evidence_id_map)
                    for assessment in bundle.deep_assessments
                )
            }
        )


def _bundle_fingerprint(bundle: ResearchBundle) -> str:
    payload = json.dumps(
        bundle.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def _retry_summary(checkpoint: dict[str, object], fingerprint: str) -> IngestSummary:
    stored_fingerprint = checkpoint.get("ingest_fingerprint")
    if stored_fingerprint != fingerprint:
        raise ValueError("이미 처리된 실행에 다른 조사 결과를 다시 넣을 수 없습니다")
    created = checkpoint.get("created")
    updated = checkpoint.get("updated")
    duplicate_suspected = checkpoint.get("duplicate_suspected")
    if not isinstance(created, int) or not isinstance(updated, int) or not isinstance(
        duplicate_suspected, bool
    ):
        raise TypeError("기존 조사 실행의 수집 요약이 올바르지 않습니다")
    return IngestSummary(
        created=created,
        updated=updated,
        duplicate_suspected=duplicate_suspected,
        results=(),
    )


def _remap_deep_assessment(
    assessment: DeepAssessmentObservation,
    evidence_id_map: dict[int, int],
) -> DeepAssessmentObservation:
    remapped_dimensions = DimensionEvidence(
        **{
            dimension: tuple(
                evidence_id_map[evidence_id]
                for evidence_id in getattr(
                    assessment.evaluation_input.evidence_ids_by_dimension, dimension
                )
            )
            for dimension in DIMENSIONS
        }
    )
    evaluation_input = assessment.evaluation_input.model_copy(
        update={"evidence_ids_by_dimension": remapped_dimensions}
    )
    listing = assessment.listing.model_copy(
        update={
            "raw_evidence_ids": tuple(
                evidence_id_map[evidence_id] for evidence_id in assessment.listing.raw_evidence_ids
            )
        }
    )
    return assessment.model_copy(update={"listing": listing, "evaluation_input": evaluation_input})


def _observations(bundle: ResearchBundle) -> tuple[ListingObservation, ...]:
    return (
        bundle.discovered
        + bundle.verified
        + tuple(assessment.listing for assessment in bundle.deep_assessments)
    )
