from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from mybudongsan.domain.requests import SearchRequest
from mybudongsan.storage.database import Database
from mybudongsan.storage.models import ResearchRunModel, SearchRequestModel


class RequestRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def save_version(self, request: SearchRequest) -> None:
        with self._database.session() as session:
            existing = session.scalar(
                select(SearchRequestModel.id).where(
                    SearchRequestModel.request_id == request.request_id,
                    SearchRequestModel.version == request.version,
                )
            )
            if existing is not None:
                raise ValueError(
                    f"request version already exists: {request.request_id}/{request.version}"
                )
            session.add(
                SearchRequestModel(
                    request_id=request.request_id,
                    version=request.version,
                    payload=request.model_dump(mode="json"),
                    created_at=datetime.now(UTC),
                )
            )

    def get_version(self, request_id: str, version: int) -> SearchRequest:
        with self._database.session() as session:
            stored = session.scalar(
                select(SearchRequestModel).where(
                    SearchRequestModel.request_id == request_id,
                    SearchRequestModel.version == version,
                )
            )
            if stored is None:
                raise ValueError(f"request version not found: {request_id}/{version}")
            return SearchRequest.model_validate(stored.payload)


class RunRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def create(self, run_id: str, request_id: str, request_version: int) -> None:
        with self._database.session() as session:
            session.add(
                ResearchRunModel(
                    run_id=run_id,
                    request_id=request_id,
                    request_version=request_version,
                    started_at=datetime.now(UTC),
                )
            )
