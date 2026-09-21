from datetime import UTC, datetime

from mybudongsan.research.contracts import ListingObservation
from mybudongsan.workflows.watch import WatchChangeDetector


def make_snapshot(**overrides: object) -> ListingObservation:
    values: dict[str, object] = {
        "source": "naver_land",
        "source_listing_id": "12345",
        "canonical_url": "https://example.test/listing/12345",
        "complex_name": "샘플아파트",
        "address": "서울시 구로구",
        "area_m2": 84.9,
        "asking_price": 850_000_000,
        "status": "active",
        "observed_at": datetime(2026, 9, 21, tzinfo=UTC),
    }
    values.update(overrides)
    return ListingObservation.model_validate(values)


def test_watch_detects_price_and_status_changes() -> None:
    changes = WatchChangeDetector.compare(
        previous=make_snapshot(asking_price=850_000_000, status="active"),
        current=make_snapshot(asking_price=830_000_000, status="inactive"),
    )

    assert {change.field for change in changes} == {"asking_price", "status"}


def test_watch_detects_only_supported_fields() -> None:
    changes = WatchChangeDetector.compare(
        previous=make_snapshot(description="old"),
        current=make_snapshot(description="new", broker="새 중개사"),
    )

    assert changes == ()


def test_missing_listing_is_possibly_removed() -> None:
    changes = WatchChangeDetector.compare(previous=make_snapshot(), current=None)

    assert [change.field for change in changes] == ["possibly_removed"]


def test_new_source_id_with_same_fallback_fingerprint_is_possibly_relisted() -> None:
    changes = WatchChangeDetector.compare(
        previous=make_snapshot(source_listing_id="12345"),
        current=make_snapshot(source_listing_id="67890"),
    )

    assert [change.field for change in changes] == ["possibly_relisted"]
