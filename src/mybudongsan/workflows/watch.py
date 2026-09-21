from __future__ import annotations

from dataclasses import dataclass

from mybudongsan.domain.listings import build_fallback_fingerprint
from mybudongsan.research.contracts import ListingObservation


@dataclass(frozen=True)
class WatchChange:
    field: str
    previous: object | None
    current: object | None


class WatchChangeDetector:
    _FIELDS = ("asking_price", "status", "canonical_url", "source_listing_id")

    @classmethod
    def compare(
        cls,
        previous: ListingObservation | None,
        current: ListingObservation | None,
    ) -> tuple[WatchChange, ...]:
        if previous is not None and current is None:
            return (WatchChange("possibly_removed", previous.source_listing_id, None),)
        if previous is None or current is None:
            return ()
        if (
            previous.source_listing_id != current.source_listing_id
            and build_fallback_fingerprint(previous) == build_fallback_fingerprint(current)
        ):
            return (
                WatchChange(
                    "possibly_relisted",
                    previous.source_listing_id,
                    current.source_listing_id,
                ),
            )
        return tuple(
            WatchChange(field, getattr(previous, field), getattr(current, field))
            for field in cls._FIELDS
            if getattr(previous, field) != getattr(current, field)
        )
