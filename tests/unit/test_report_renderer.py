from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mybudongsan.domain.requests import (
    MoneyRange,
    RegionCriterion,
    RequestStatus,
    SearchRequest,
)
from mybudongsan.domain.scoring import EvaluationResult
from mybudongsan.reports.renderer import (
    AssessedCandidate,
    CandidateFinding,
    EvidenceRecord,
    ReportBundle,
    ReportRenderer,
    ReportRunSummary,
    ScenarioLabel,
)
from mybudongsan.research.contracts import ListingObservation


def test_renderer_writes_deterministic_evidence_backed_artifacts(tmp_path: Path) -> None:
    artifacts = ReportRenderer().render(_bundle(), tmp_path)

    assert artifacts.directory == tmp_path / "2026" / "09" / "req-gangseo_gangseo-family"
    assert artifacts.report_path.read_text(encoding="utf-8") == _golden_report()
    assert _read_csv(artifacts.candidates_path) == [
        {
            "candidate_id": "candidate-1",
            "scenario": "종합 추천",
            "complex_name": "강서 한강뷰",
            "address": "서울 강서구 가양동 1",
            "asking_price": "1234000000",
            "status": "active",
            "total_score": "82.5",
            "confidence": "91",
            "recommendable": "true",
            "evidence_ids": "101,102,103",
        },
        {
            "candidate_id": "candidate-2",
            "scenario": "안정성 대안",
            "complex_name": "가양 안정마을",
            "address": "서울 강서구 가양동 2",
            "asking_price": "1180000000",
            "status": "active",
            "total_score": "78.0",
            "confidence": "88",
            "recommendable": "true",
            "evidence_ids": "104",
        },
    ]
    assert _read_json(artifacts.run_data_path) == {
        "candidates": [
            {
                "assessment": {
                    "confidence": 91,
                    "eligible": True,
                    "reasons": [],
                    "recommendable": True,
                    "total_score": 82.5,
                },
                "candidate_id": "candidate-1",
                "evidence_ids": [101, 102, 103],
                "findings": [
                    {"evidence_ids": [101], "section": "가격과 실거래", "text": "최근 실거래 12.1억원"},
                    {"evidence_ids": [102], "section": "교통 및 생활권", "text": "9호선 양천향교역 도보 8분"},
                    {"evidence_ids": [103], "section": "도시계획 및 정책", "text": "공식 고시 확인 필요"},
                ],
                "listing": {
                    "address": "서울 강서구 가양동 1",
                    "area_m2": 84.9,
                    "asking_price": "1234000000",
                    "broker": "가양공인",
                    "building": "101동",
                    "canonical_url": "https://example.test/listing/1",
                    "complex_name": "강서 한강뷰",
                    "description": None,
                    "floor": "10층",
                    "observed_at": "2026-09-21T09:30:00Z",
                    "raw_evidence_ids": [],
                    "source": "fixture",
                    "source_listing_id": "listing-1",
                    "status": "active",
                },
                "risks": ["개발계획 진행 단계 재확인"],
                "scenario": "종합 추천",
            },
            {
                "assessment": {
                    "confidence": 88,
                    "eligible": True,
                    "reasons": [],
                    "recommendable": True,
                    "total_score": 78.0,
                },
                "candidate_id": "candidate-2",
                "evidence_ids": [104],
                "findings": [
                    {"evidence_ids": [104], "section": "위험과 미확인 사항", "text": "전세가 변동 확인 필요"},
                ],
                "listing": {
                    "address": "서울 강서구 가양동 2",
                    "area_m2": 84.7,
                    "asking_price": "1180000000",
                    "broker": "가양공인",
                    "building": "102동",
                    "canonical_url": "https://example.test/listing/2",
                    "complex_name": "가양 안정마을",
                    "description": None,
                    "floor": "7층",
                    "observed_at": "2026-09-21T09:31:00Z",
                    "raw_evidence_ids": [],
                    "source": "fixture",
                    "source_listing_id": "listing-2",
                    "status": "active",
                },
                "risks": ["현장 소음 확인"],
                "scenario": "안정성 대안",
            },
        ],
        "evidence": [
            {
                "accessed_at": "2026-09-21T09:00:00Z",
                "claim": "최근 실거래 12.1억원",
                "evidence_id": 101,
                "excerpt": "2026년 8월 계약",
                "source_type": "transaction",
                "source_url": "https://example.test/transactions/1",
            },
            {
                "accessed_at": "2026-09-21T09:01:00Z",
                "claim": "9호선 양천향교역 도보 8분",
                "evidence_id": 102,
                "excerpt": None,
                "source_type": "map",
                "source_url": "https://example.test/transport/1",
            },
            {
                "accessed_at": "2026-09-21T09:02:00Z",
                "claim": "공식 고시 확인 필요",
                "evidence_id": 103,
                "excerpt": "계획 단계 미확정",
                "source_type": "official",
                "source_url": "https://example.test/plan/1",
            },
            {
                "accessed_at": "2026-09-21T09:03:00Z",
                "claim": "전세가 변동 확인 필요",
                "evidence_id": 104,
                "excerpt": None,
                "source_type": "market",
                "source_url": "https://example.test/market/2",
            },
        ],
        "request": {
            "budget": {"maximum": "1300000000", "minimum": "1000000000"},
            "excluded": ["반지하"],
            "preferred": ["한강 접근성"],
                "regions": [{"allow_expansion": False, "name": "서울 강서구"}],
            "request_id": "req-gangseo",
            "required": ["전용 84㎡ 이상"],
            "special_questions": ["개발계획 진행 단계"],
            "status": "approved",
            "version": 2,
        },
        "run": {
            "completed_at": "2026-09-21T10:00:00Z",
            "run_id": "run-20260921-1",
            "slug": "gangseo-family",
            "stage": "report_complete",
            "status": "running",
        },
    }

    required_headings = [
        "요청 조건 요약",
        "PM 결론",
        "최종 후보 비교",
        "가격과 실거래",
        "교통 및 생활권",
        "도시계획 및 정책",
        "위험과 미확인 사항",
        "현장 방문 및 중개사 문의",
        "출처 및 조사 기준시각",
    ]
    report = artifacts.report_path.read_text(encoding="utf-8")
    assert all(heading in report for heading in required_headings)


def test_renderer_does_not_assign_a_scenario_to_unqualified_candidates(tmp_path: Path) -> None:
    bundle = _bundle().model_copy(
        update={
            "candidates": (
                AssessedCandidate(
                    candidate_id="unqualified",
                    listing=_listing("listing-unqualified", "보류 단지", 1_100_000_000),
                    assessment=EvaluationResult(
                        eligible=True,
                        recommendable=False,
                        total_score=70.0,
                        confidence=80,
                        reasons=("confidence_below_85",),
                    ),
                    evidence_ids=(101,),
                ),
            )
        }
    )

    artifacts = ReportRenderer().render(bundle, tmp_path)

    assert _read_csv(artifacts.candidates_path)[0]["scenario"] == ""
    assert "추천 없음" in artifacts.report_path.read_text(encoding="utf-8")


def test_renderer_supports_an_empty_final_candidate_set(tmp_path: Path) -> None:
    bundle = _bundle().model_copy(update={"candidates": ()})

    artifacts = ReportRenderer().render(bundle, tmp_path)

    assert _read_csv(artifacts.candidates_path) == []
    assert "추천 없음" in artifacts.report_path.read_text(encoding="utf-8")


def test_bundle_rejects_scenario_for_an_unqualified_candidate() -> None:
    with pytest.raises(ValueError, match="qualified"):
        AssessedCandidate(
            candidate_id="unqualified",
            listing=_listing("listing-unqualified", "보류 단지", 1_100_000_000),
            assessment=EvaluationResult(
                eligible=True,
                recommendable=False,
                total_score=70.0,
                confidence=80,
                reasons=("confidence_below_85",),
            ),
            evidence_ids=(101,),
            scenario=ScenarioLabel.OVERALL_RECOMMENDATION,
        )


def _bundle() -> ReportBundle:
    return ReportBundle(
        request=SearchRequest(
            request_id="req-gangseo",
            version=2,
            regions=(RegionCriterion(name="서울 강서구"),),
            budget=MoneyRange(minimum=1_000_000_000, maximum=1_300_000_000),
            required=("전용 84㎡ 이상",),
            preferred=("한강 접근성",),
            excluded=("반지하",),
            special_questions=("개발계획 진행 단계",),
            status=RequestStatus.APPROVED,
        ),
        run=ReportRunSummary(
            run_id="run-20260921-1",
            status="running",
            stage="report_complete",
            completed_at=datetime(2026, 9, 21, 10, 0, tzinfo=UTC),
            slug="gangseo-family",
        ),
        candidates=(
            AssessedCandidate(
                candidate_id="candidate-1",
                listing=_listing("listing-1", "강서 한강뷰", 1_234_000_000),
                assessment=EvaluationResult(
                    eligible=True,
                    recommendable=True,
                    total_score=82.5,
                    confidence=91,
                    reasons=(),
                ),
                evidence_ids=(101, 102, 103),
                scenario=ScenarioLabel.OVERALL_RECOMMENDATION,
                findings=(
                    CandidateFinding(
                        section="가격과 실거래",
                        text="최근 실거래 12.1억원",
                        evidence_ids=(101,),
                    ),
                    CandidateFinding(
                        section="교통 및 생활권",
                        text="9호선 양천향교역 도보 8분",
                        evidence_ids=(102,),
                    ),
                    CandidateFinding(
                        section="도시계획 및 정책",
                        text="공식 고시 확인 필요",
                        evidence_ids=(103,),
                    ),
                ),
                risks=("개발계획 진행 단계 재확인",),
            ),
            AssessedCandidate(
                candidate_id="candidate-2",
                listing=_listing("listing-2", "가양 안정마을", 1_180_000_000),
                assessment=EvaluationResult(
                    eligible=True,
                    recommendable=True,
                    total_score=78.0,
                    confidence=88,
                    reasons=(),
                ),
                evidence_ids=(104,),
                scenario=ScenarioLabel.STABILITY_ALTERNATIVE,
                findings=(
                    CandidateFinding(
                        section="위험과 미확인 사항",
                        text="전세가 변동 확인 필요",
                        evidence_ids=(104,),
                    ),
                ),
                risks=("현장 소음 확인",),
            ),
        ),
        evidence=(
            EvidenceRecord(
                evidence_id=101,
                claim="최근 실거래 12.1억원",
                source_url="https://example.test/transactions/1",
                source_type="transaction",
                excerpt="2026년 8월 계약",
                accessed_at=datetime(2026, 9, 21, 9, 0, tzinfo=UTC),
            ),
            EvidenceRecord(
                evidence_id=102,
                claim="9호선 양천향교역 도보 8분",
                source_url="https://example.test/transport/1",
                source_type="map",
                accessed_at=datetime(2026, 9, 21, 9, 1, tzinfo=UTC),
            ),
            EvidenceRecord(
                evidence_id=103,
                claim="공식 고시 확인 필요",
                source_url="https://example.test/plan/1",
                source_type="official",
                excerpt="계획 단계 미확정",
                accessed_at=datetime(2026, 9, 21, 9, 2, tzinfo=UTC),
            ),
            EvidenceRecord(
                evidence_id=104,
                claim="전세가 변동 확인 필요",
                source_url="https://example.test/market/2",
                source_type="market",
                accessed_at=datetime(2026, 9, 21, 9, 3, tzinfo=UTC),
            ),
        ),
    )


def _listing(source_listing_id: str, complex_name: str, asking_price: int) -> ListingObservation:
    index = "1" if source_listing_id.endswith("1") else "2"
    return ListingObservation(
        source="fixture",
        source_listing_id=source_listing_id,
        canonical_url=f"https://example.test/listing/{index}",
        complex_name=complex_name,
        address=f"서울 강서구 가양동 {index}",
        building=f"10{index}동",
        floor="10층" if index == "1" else "7층",
        area_m2=84.9 if index == "1" else 84.7,
        asking_price=asking_price,
        status="active",
        observed_at=datetime(2026, 9, 21, 9, 30 if index == "1" else 31, tzinfo=UTC),
        broker="가양공인",
    )


def _golden_report() -> str:
    return (Path(__file__).parents[1] / "golden" / "report_expected.md").read_text(encoding="utf-8")


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))
