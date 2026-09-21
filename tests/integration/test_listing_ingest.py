from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from mybudongsan.domain.requests import MoneyRange, RegionCriterion, SearchRequest
from mybudongsan.research.contracts import ListingObservation, ResearchBundle
from mybudongsan.research.ingest import ListingIngestService
from mybudongsan.storage.models import EvidenceModel, ListingModel, ListingSnapshotModel
from mybudongsan.storage.repositories import ListingRepository, RequestRepository, RunRepository


def make_observation(**overrides: object) -> ListingObservation:
    values: dict[str, object] = {
        "source": "naver_land",
        "source_listing_id": "12345",
        "canonical_url": "https://example.test/listing/12345",
        "complex_name": "샘플아파트",
        "address": "서울시 구로구",
        "building": "101동",
        "floor": "2층",
        "area_m2": 84.9,
        "asking_price": 850_000_000,
        "status": "active",
        "observed_at": datetime(2026, 9, 21, tzinfo=UTC),
        "broker": "샘플공인",
        "description": "설명",
    }
    values.update(overrides)
    return ListingObservation.model_validate(values)


def create_run(database, suffix: str = "primary") -> str:  # type: ignore[no-untyped-def]
    request = SearchRequest(
        request_id=f"request-listings-{suffix}",
        version=1,
        regions=[RegionCriterion(name="서울 구로구")],
        budget=MoneyRange(minimum=600_000_000, maximum=900_000_000),
    )
    RequestRepository(database).save_version(request)
    run = RunRepository(database).create(f"run-listings-{suffix}", request.request_id, request.version)
    return run.run_id


def create_evidence(database, run_id: str) -> int:  # type: ignore[no-untyped-def]
    with database.session() as session:
        evidence = EvidenceModel(
            run_id=run_id,
            listing_id=None,
            claim="listing capture",
            source_url="https://example.test/listing/12345",
            source_type="browser",
            excerpt=None,
            accessed_at=datetime(2026, 9, 21, tzinfo=UTC),
        )
        session.add(evidence)
        session.flush()
        return evidence.id


def test_ingest_upserts_listing_appends_snapshots_and_connects_evidence(database) -> None:  # type: ignore[no-untyped-def]
    run_id = create_run(database)
    evidence_id = create_evidence(database, run_id)

    service = ListingIngestService(ListingRepository(database))
    first = service.ingest(
        run_id,
        ResearchBundle(discovered=(make_observation(raw_evidence_ids=(evidence_id,)),)),
    )
    second = service.ingest(
        run_id,
        ResearchBundle(discovered=(make_observation(asking_price=830_000_000),)),
    )

    assert first.created == 1
    assert first.updated == 0
    assert second.created == 0
    assert second.updated == 1
    assert second.duplicate_suspected is False
    with database.session() as session:
        listing_count = session.scalar(select(func.count()).select_from(ListingModel))
        snapshot_count = session.scalar(select(func.count()).select_from(ListingSnapshotModel))
        evidence = session.get(EvidenceModel, evidence_id)
    assert listing_count == 1
    assert snapshot_count == 2
    assert evidence is not None
    assert evidence.listing_id is not None


def test_ingest_rejects_unknown_run_before_writing_a_listing(database) -> None:  # type: ignore[no-untyped-def]
    service = ListingIngestService(ListingRepository(database))

    with pytest.raises(ValueError, match="run not found"):
        service.ingest("run-missing", ResearchBundle(discovered=(make_observation(),)))

    with database.session() as session:
        listing_count = session.scalar(select(func.count()).select_from(ListingModel))
        snapshot_count = session.scalar(select(func.count()).select_from(ListingSnapshotModel))
    assert listing_count == 0
    assert snapshot_count == 0


def test_ingest_rejects_evidence_from_a_different_run(database) -> None:  # type: ignore[no-untyped-def]
    run_id = create_run(database, "first")
    other_evidence_id = create_evidence(database, create_run(database, "second"))
    service = ListingIngestService(ListingRepository(database))

    with pytest.raises(ValueError, match="does not belong to run"):
        service.ingest(
            run_id,
            ResearchBundle(discovered=(make_observation(raw_evidence_ids=(other_evidence_id,)),)),
        )

    with database.session() as session:
        listing_count = session.scalar(select(func.count()).select_from(ListingModel))
    assert listing_count == 0


def test_ingest_rejects_missing_evidence_explicitly(database) -> None:  # type: ignore[no-untyped-def]
    run_id = create_run(database)
    service = ListingIngestService(ListingRepository(database))

    with pytest.raises(ValueError, match="evidence not found"):
        service.ingest(
            run_id,
            ResearchBundle(discovered=(make_observation(raw_evidence_ids=(999_999,)),)),
        )


def test_ingest_rejects_cross_listing_evidence_reassignment(database) -> None:  # type: ignore[no-untyped-def]
    run_id = create_run(database)
    evidence_id = create_evidence(database, run_id)
    service = ListingIngestService(ListingRepository(database))
    service.ingest(
        run_id,
        ResearchBundle(discovered=(make_observation(raw_evidence_ids=(evidence_id,)),)),
    )

    with pytest.raises(ValueError, match="different listing"):
        service.ingest(
            run_id,
            ResearchBundle(
                discovered=(
                    make_observation(
                        source_listing_id="new-listing-id",
                        raw_evidence_ids=(evidence_id,),
                    ),
                )
            ),
        )

    with database.session() as session:
        listing_count = session.scalar(select(func.count()).select_from(ListingModel))
        snapshot_count = session.scalar(select(func.count()).select_from(ListingSnapshotModel))
        evidence = session.get(EvidenceModel, evidence_id)
    assert listing_count == 1
    assert snapshot_count == 1
    assert evidence is not None
    assert evidence.listing_id is not None


def test_ingest_allows_evidence_already_connected_to_the_same_listing(database) -> None:  # type: ignore[no-untyped-def]
    run_id = create_run(database)
    evidence_id = create_evidence(database, run_id)
    service = ListingIngestService(ListingRepository(database))
    service.ingest(
        run_id,
        ResearchBundle(discovered=(make_observation(raw_evidence_ids=(evidence_id,)),)),
    )

    summary = service.ingest(
        run_id,
        ResearchBundle(discovered=(make_observation(raw_evidence_ids=(evidence_id,)),)),
    )

    assert summary.updated == 1
    with database.session() as session:
        snapshot_count = session.scalar(select(func.count()).select_from(ListingSnapshotModel))
        evidence = session.get(EvidenceModel, evidence_id)
    assert snapshot_count == 2
    assert evidence is not None
    assert evidence.listing_id is not None


def test_ingest_rolls_back_the_entire_bundle_when_a_later_evidence_fails(database) -> None:  # type: ignore[no-untyped-def]
    run_id = create_run(database)
    evidence_id = create_evidence(database, run_id)
    service = ListingIngestService(ListingRepository(database))

    with pytest.raises(ValueError, match="evidence not found"):
        service.ingest(
            run_id,
            ResearchBundle(
                discovered=(
                    make_observation(raw_evidence_ids=(evidence_id,)),
                    make_observation(source_listing_id="later", raw_evidence_ids=(999_999,)),
                )
            ),
        )

    with database.session() as session:
        listing_count = session.scalar(select(func.count()).select_from(ListingModel))
        snapshot_count = session.scalar(select(func.count()).select_from(ListingSnapshotModel))
        evidence = session.get(EvidenceModel, evidence_id)
    assert listing_count == 0
    assert snapshot_count == 0
    assert evidence is not None
    assert evidence.listing_id is None


def test_ingest_keeps_uncertain_fallback_match_separate_and_flags_it(database) -> None:  # type: ignore[no-untyped-def]
    run_id = create_run(database)
    service = ListingIngestService(ListingRepository(database))

    first = service.ingest(run_id, ResearchBundle(discovered=(make_observation(source_listing_id=None),)))
    second = service.ingest(
        run_id,
        ResearchBundle(discovered=(make_observation(source_listing_id=None, asking_price=830_000_000),)),
    )

    assert first.duplicate_suspected is False
    assert second.duplicate_suspected is True
    with database.session() as session:
        listing_count = session.scalar(select(func.count()).select_from(ListingModel))
    assert listing_count == 2
