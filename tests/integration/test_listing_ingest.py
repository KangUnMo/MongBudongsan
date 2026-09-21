from datetime import UTC, datetime

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


def create_run(database) -> str:  # type: ignore[no-untyped-def]
    request = SearchRequest(
        request_id="request-listings",
        version=1,
        regions=[RegionCriterion(name="서울 구로구")],
        budget=MoneyRange(minimum=600_000_000, maximum=900_000_000),
    )
    RequestRepository(database).save_version(request)
    run = RunRepository(database).create("run-listings", request.request_id, request.version)
    return run.run_id


def test_ingest_upserts_listing_appends_snapshots_and_connects_evidence(database) -> None:  # type: ignore[no-untyped-def]
    run_id = create_run(database)
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
        evidence_id = evidence.id

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
