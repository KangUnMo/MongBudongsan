from __future__ import annotations

from dataclasses import dataclass

from mybudongsan.research.contracts import ListingObservation, ResearchBundle
from mybudongsan.storage.repositories import ListingRepository


@dataclass(frozen=True)
class IngestSummary:
    created: int
    updated: int
    duplicate_suspected: bool


class ListingIngestService:
    def __init__(self, listing_repository: ListingRepository) -> None:
        self._listing_repository = listing_repository

    def ingest(self, run_id: str, bundle: ResearchBundle) -> IngestSummary:
        results = self._listing_repository.ingest_bundle(run_id, _observations(bundle))
        return IngestSummary(
            created=sum(result.created for result in results),
            updated=sum(not result.created for result in results),
            duplicate_suspected=any(result.duplicate_suspected for result in results),
        )


def _observations(bundle: ResearchBundle) -> tuple[ListingObservation, ...]:
    return bundle.discovered + bundle.verified + bundle.deep_assessments
