from __future__ import annotations

from hashlib import sha256
from typing import TYPE_CHECKING
from unicodedata import normalize as unicode_normalize

if TYPE_CHECKING:
    from mybudongsan.research.contracts import ListingObservation


def build_listing_key(observation: ListingObservation) -> str:
    """Return an authoritative source key or a deterministic fallback key."""
    if observation.source_listing_id:
        return f"{observation.source}:{observation.source_listing_id}"
    return f"{observation.source}:fallback:{build_fallback_fingerprint(observation)}"


def build_fallback_fingerprint(observation: ListingObservation) -> str:
    """Fingerprint the stable fields available when a source has no listing ID."""
    values = (
        observation.source,
        observation.complex_name,
        observation.building,
        observation.floor,
        _rounded_area(observation.area_m2),
        observation.asking_price,
        observation.broker,
    )
    normalized = "|".join(_normalize(value) for value in values)
    return sha256(normalized.encode("utf-8")).hexdigest()


def build_coarse_fallback_fingerprint(observation: ListingObservation) -> str:
    """Return a non-authoritative fingerprint used only to flag possible duplicates."""
    values = (
        observation.source,
        observation.complex_name,
        observation.building,
        observation.floor,
        _rounded_area(observation.area_m2),
    )
    normalized = "|".join(_normalize(value) for value in values)
    return sha256(normalized.encode("utf-8")).hexdigest()


def _rounded_area(area_m2: float | None) -> str:
    return "" if area_m2 is None else f"{area_m2:.1f}"


def _normalize(value: object | None) -> str:
    if value is None:
        return ""
    return "".join(
        character
        for character in unicode_normalize("NFKC", str(value)).casefold().strip()
        if character.isalnum()
    )
