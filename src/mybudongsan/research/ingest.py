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
        created = 0
        updated = 0
        duplicate_suspected = False
        for observation in _observations(bundle):
            result = self._listing_repository.upsert_snapshot(run_id, observation)
            created += int(result.created)
            updated += int(not result.created)
            duplicate_suspected = duplicate_suspected or result.duplicate_suspected
        return IngestSummary(
            created=created,
            updated=updated,
            duplicate_suspected=duplicate_suspected,
        )


def _observations(bundle: ResearchBundle) -> tuple[ListingObservation, ...]:
    return bundle.discovered + bundle.verified + bundle.deep_assessments
