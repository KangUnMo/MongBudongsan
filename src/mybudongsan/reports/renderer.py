from __future__ import annotations

import csv
import json
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mybudongsan.domain.requests import RequestStatus, SearchRequest
from mybudongsan.domain.scoring import EvaluationResult
from mybudongsan.research.contracts import ListingObservation


class ScenarioLabel(StrEnum):
    OVERALL_RECOMMENDATION = "종합 추천"
    STABILITY_ALTERNATIVE = "안정성 대안"
    CONDITION_SPECIALIZED_ALTERNATIVE = "조건 특화 대안"


class FindingSection(StrEnum):
    PRICE = "가격과 실거래"
    TRANSPORT = "교통 및 생활권"
    URBAN_PLAN = "도시계획 및 정책"
    RISKS = "위험과 미확인 사항"


class EvidenceRecord(BaseModel):
    """Immutable source material included in a rendered report."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_id: int
    claim: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    source_type: str = Field(min_length=1)
    excerpt: str | None = None
    accessed_at: datetime


class CandidateFinding(BaseModel):
    """A supplied, evidence-linked fact for one candidate report section."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    section: FindingSection
    text: str = Field(min_length=1)
    evidence_ids: tuple[int, ...] = Field(min_length=1)


class AssessedCandidate(BaseModel):
    """An immutable final-candidate projection; renderer never scores or ranks it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_id: str = Field(min_length=1)
    listing: ListingObservation
    assessment: EvaluationResult
    evidence_ids: tuple[int, ...] = ()
    scenario: ScenarioLabel | None = None
    findings: tuple[CandidateFinding, ...] = ()
    risks: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_evidence_and_scenario(self) -> AssessedCandidate:
        if self.scenario is not None and not self.assessment.recommendable:
            raise ValueError("scenario requires a qualified recommendable candidate")
        candidate_evidence_ids = set(self.evidence_ids)
        for finding in self.findings:
            if not set(finding.evidence_ids).issubset(candidate_evidence_ids):
                raise ValueError("finding evidence must belong to the candidate")
        return self


class ReportRunSummary(BaseModel):
    """Stable run metadata used for artifact naming and report provenance."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str = Field(min_length=1)
    status: str = Field(min_length=1)
    stage: str = Field(min_length=1)
    completed_at: datetime
    slug: str = Field(pattern=r"[a-z0-9]+(?:-[a-z0-9]+)*")


class ReportBundle(BaseModel):
    """All canonical inputs necessary to render a deterministic local report."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    request: SearchRequest
    run: ReportRunSummary
    candidates: tuple[AssessedCandidate, ...] = Field(default_factory=tuple, max_length=3)
    evidence: tuple[EvidenceRecord, ...] = ()

    @field_validator("evidence")
    @classmethod
    def validate_unique_evidence_ids(
        cls, evidence: tuple[EvidenceRecord, ...]
    ) -> tuple[EvidenceRecord, ...]:
        evidence_ids = [item.evidence_id for item in evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence IDs must be unique")
        return evidence

    @model_validator(mode="after")
    def validate_canonical_inputs(self) -> ReportBundle:
        if self.request.status is not RequestStatus.APPROVED:
            raise ValueError("report rendering requires an approved request")
        candidate_ids = [candidate.candidate_id for candidate in self.candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("candidate IDs must be unique")
        scenarios = [candidate.scenario for candidate in self.candidates if candidate.scenario]
        if len(scenarios) != len(set(scenarios)):
            raise ValueError("scenario labels must be unique")
        evidence_ids = {item.evidence_id for item in self.evidence}
        for candidate in self.candidates:
            referenced_ids = set(candidate.evidence_ids)
            referenced_ids.update(
                evidence_id
                for finding in candidate.findings
                for evidence_id in finding.evidence_ids
            )
            if not referenced_ids.issubset(evidence_ids):
                raise ValueError("candidate references evidence missing from the bundle")
        return self


class RenderedArtifacts(BaseModel):
    model_config = ConfigDict(frozen=True)

    directory: Path
    report_path: Path
    candidates_path: Path
    run_data_path: Path


class ReportRenderer:
    """Render evidence-backed snapshots without deriving new rankings or claims."""

    _environment = Environment(
        loader=FileSystemLoader(Path(__file__).with_name("templates")),
        undefined=StrictUndefined,
        autoescape=False,
        keep_trailing_newline=False,
    )

    def render(self, bundle: ReportBundle, output_root: Path) -> RenderedArtifacts:
        directory = (
            output_root
            / f"{bundle.run.completed_at.year:04d}"
            / f"{bundle.run.completed_at.month:02d}"
            / f"{bundle.request.request_id}_{bundle.run.slug}"
        )
        artifacts = RenderedArtifacts(
            directory=directory,
            report_path=directory / "report.md",
            candidates_path=directory / "candidates.csv",
            run_data_path=directory / "run-data.json",
        )
        self._ensure_artifacts_are_new(artifacts)
        directory.mkdir(parents=True, exist_ok=True)

        context = self._template_context(bundle)
        artifacts.report_path.write_text(
            self._environment.get_template("report.md.j2").render(**context),
            encoding="utf-8",
        )
        self._write_candidates_csv(artifacts.candidates_path, bundle.candidates)
        artifacts.run_data_path.write_text(
            json.dumps(
                bundle.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return artifacts

    @staticmethod
    def _ensure_artifacts_are_new(artifacts: RenderedArtifacts) -> None:
        existing = [
            path.name
            for path in (artifacts.report_path, artifacts.candidates_path, artifacts.run_data_path)
            if path.exists()
        ]
        if existing:
            raise FileExistsError(f"immutable artifacts already exist: {', '.join(existing)}")

    @staticmethod
    def _template_context(bundle: ReportBundle) -> dict[str, object]:
        findings_by_section = {
            section: tuple(
                (candidate, finding)
                for candidate in bundle.candidates
                for finding in candidate.findings
                if finding.section is section
            )
            for section in FindingSection
        }
        scenario_candidates = tuple(
            candidate for candidate in bundle.candidates if candidate.scenario is not None
        )
        return {
            "bundle": bundle,
            "findings_by_section": findings_by_section,
            "scenario_candidates": scenario_candidates,
            "unassigned_qualified_candidates": tuple(
                candidate
                for candidate in bundle.candidates
                if candidate.assessment.recommendable and candidate.scenario is None
            ),
            "format_datetime": _format_datetime,
            "format_money": _format_money,
        }

    @staticmethod
    def _write_candidates_csv(path: Path, candidates: tuple[AssessedCandidate, ...]) -> None:
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=(
                    "candidate_id",
                    "scenario",
                    "complex_name",
                    "address",
                    "asking_price",
                    "status",
                    "total_score",
                    "confidence",
                    "recommendable",
                    "evidence_ids",
                ),
            )
            writer.writeheader()
            for candidate in candidates:
                writer.writerow(
                    {
                        "candidate_id": candidate.candidate_id,
                        "scenario": candidate.scenario.value if candidate.scenario else "",
                        "complex_name": candidate.listing.complex_name or "",
                        "address": candidate.listing.address or "",
                        "asking_price": _format_money(candidate.listing.asking_price),
                        "status": candidate.listing.status or "",
                        "total_score": (
                            "" if candidate.assessment.total_score is None else str(candidate.assessment.total_score)
                        ),
                        "confidence": str(candidate.assessment.confidence),
                        "recommendable": str(candidate.assessment.recommendable).lower(),
                        "evidence_ids": ",".join(str(item) for item in candidate.evidence_ids),
                    }
                )


def _format_datetime(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _format_money(value: object | None) -> str:
    return "" if value is None else str(value)
