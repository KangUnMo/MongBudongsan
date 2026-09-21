# MyBudongsan B-Lite V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a personal Codex-based apartment-purchase research system that turns an approved request into evidence-backed recommendations, persists canonical data in SQLite, syncs concise outputs to Google, and sends bounded lifecycle notifications.

**Architecture:** Codex owns conversation, browser research, specialist dispatch, and final synthesis. A framework-independent Python package owns validation, persistence, checkpoints, deterministic filtering and scoring, report rendering, Google synchronization, and notification bookkeeping. V1 has no HTTP server, worker queue, web UI, or automatic watch scheduler.

**Tech Stack:** Python 3.12+, uv, Typer, Pydantic 2, SQLAlchemy 2, Alembic, Jinja2, Google API Python Client, google-auth-oauthlib, keyring, pytest, Ruff, mypy, Codex skills, ego-browser, PlayMCP `MemoChat`.

## Global Constraints

- SQLite is the sole source of truth; Sheets and Drive are projections and backups.
- A request contains one to five regions and immutable approved versions.
- Default research funnel: discover 15–25, verify up to 7 active listings, deeply assess up to 3.
- Mandatory and excluded criteria are gates; weighted scores cannot override them.
- Default weights are liquidity/price defense 35, commute 30, price reasonableness 25, residential quality/risk 10.
- A recommendation requires research confidence of at least 85.
- One integrated researcher is standard; at most one conditional specialist is added.
- Never relax mandatory criteria without a newly approved request version.
- Retry transient page failures at most three times; resume from the last successful checkpoint.
- Stop after two hours with partial results and missing-evidence notes.
- A run emits at most five lifecycle notifications; only completion and failure also use Gmail.
- WATCH is manually refreshed; no automatic schedule or listing-change notification.
- OAuth and PlayMCP secrets must never enter Git, SQLite, Sheets, generated artifacts, or logs.
- Use test-driven development and commit after every task.

---

## Delivery Milestones

1. **Core application:** Tasks 1–7 produce a fully testable local CLI using fixture research data.
2. **External integrations:** Tasks 8–9 add Google sync and lifecycle notifications behind ports.
3. **Codex workflow:** Tasks 10–11 add PM/researcher skills, live-browser contracts, and an end-to-end acceptance run.

Each milestone must pass its verification gate before the next milestone starts.

## Planned File Structure

```text
.
├── .gitignore
├── .python-version
├── pyproject.toml
├── README.md
├── alembic.ini
├── migrations/
│   ├── env.py
│   └── versions/0001_initial.py
├── src/mybudongsan/
│   ├── __init__.py
│   ├── cli.py
│   ├── config.py
│   ├── domain/
│   │   ├── requests.py
│   │   ├── listings.py
│   │   ├── runs.py
│   │   └── scoring.py
│   ├── storage/
│   │   ├── database.py
│   │   ├── models.py
│   │   └── repositories.py
│   ├── research/
│   │   ├── contracts.py
│   │   └── ingest.py
│   ├── workflows/
│   │   ├── research_run.py
│   │   └── watch.py
│   ├── reports/
│   │   ├── renderer.py
│   │   └── templates/report.md.j2
│   ├── integrations/
│   │   ├── google_auth.py
│   │   ├── sheets.py
│   │   ├── drive.py
│   │   └── gmail.py
│   └── notifications/
│       ├── policy.py
│       └── outbox.py
├── skills/mybudongsan-pm/
│   ├── SKILL.md
│   └── references/
│       ├── researcher.md
│       ├── specialist-market.md
│       ├── specialist-transit.md
│       ├── specialist-urban-planning.md
│       └── report-contract.md
└── tests/
    ├── conftest.py
    ├── fixtures/research_bundle.json
    ├── unit/
    ├── integration/
    └── live/
```

### Task 1: Bootstrap the Package and Validate Purchase Requests

**Files:**
- Create: `.gitignore`
- Create: `.python-version`
- Create: `pyproject.toml`
- Create: `src/mybudongsan/__init__.py`
- Create: `src/mybudongsan/domain/requests.py`
- Create: `tests/unit/test_requests.py`

**Interfaces:**
- Consumes: none
- Produces: `SearchRequest`, `BuyerProfile`, `RequestStatus`, `RegionCriterion`, `MoneyRange`

- [ ] **Step 1: Initialize version control and project metadata**

Run:

```bash
git init
uv init --bare --python 3.12
```

Expected: a new Git repository and a minimal `pyproject.toml` exist.

- [ ] **Step 2: Define dependencies and tool configuration**

Replace `pyproject.toml` with:

```toml
[project]
name = "mybudongsan"
version = "0.1.0"
description = "Evidence-backed apartment purchase research for personal Codex workflows"
requires-python = ">=3.12"
dependencies = [
  "alembic>=1.13",
  "google-api-python-client>=2.0",
  "google-auth-oauthlib>=1.2",
  "jinja2>=3.1",
  "keyring>=25",
  "pydantic>=2.9",
  "sqlalchemy>=2.0",
  "typer>=0.12",
]

[dependency-groups]
dev = [
  "mypy>=1.11",
  "pytest>=8.3",
  "pytest-cov>=5.0",
  "ruff>=0.6",
]

[project.scripts]
mybudongsan = "mybudongsan.cli:app"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/mybudongsan"]

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = ["live: requires user credentials and live websites"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.mypy]
python_version = "3.12"
strict = true
packages = ["mybudongsan"]
```

Create `.python-version` containing `3.12`, and `.gitignore` containing:

```gitignore
.DS_Store
.env
.venv/
.pytest_cache/
.mypy_cache/
.ruff_cache/
__pycache__/
*.pyc
*.sqlite3
data/
artifacts/
client_secret*.json
token*.json
.superpowers/
```

Run `uv sync --dev`. Expected: exits 0 and creates `.venv` plus `uv.lock`.

- [ ] **Step 3: Write failing request-model tests**

Create `tests/unit/test_requests.py`:

```python
from decimal import Decimal

import pytest
from pydantic import ValidationError

from mybudongsan.domain.requests import (
    BuyerProfile,
    MoneyRange,
    RegionCriterion,
    RequestStatus,
    SearchRequest,
)


def test_search_request_accepts_one_to_five_regions() -> None:
    request = SearchRequest(
        request_id="req-001",
        version=1,
        regions=[RegionCriterion(name="서울 강서구")],
        budget=MoneyRange(minimum=Decimal("600000000"), maximum=Decimal("900000000")),
        required=["아파트"],
        preferred=["역 도보 15분 이내"],
        excluded=["반지하"],
    )
    assert request.status is RequestStatus.DRAFT


def test_search_request_rejects_more_than_five_regions() -> None:
    with pytest.raises(ValidationError):
        SearchRequest(
            request_id="req-002",
            version=1,
            regions=[RegionCriterion(name=f"지역 {index}") for index in range(6)],
            budget=MoneyRange(minimum=Decimal("1"), maximum=Decimal("2")),
        )


def test_money_range_rejects_reversed_values() -> None:
    with pytest.raises(ValidationError):
        MoneyRange(minimum=Decimal("900"), maximum=Decimal("600"))


def test_profile_weights_must_total_one_hundred() -> None:
    with pytest.raises(ValidationError):
        BuyerProfile(
            profile_id="profile-001",
            liquidity_weight=35,
            commute_weight=30,
            price_weight=25,
            residential_weight=9,
        )
```

- [ ] **Step 4: Run the tests and verify failure**

Run: `uv run pytest tests/unit/test_requests.py -v`

Expected: collection fails because `mybudongsan.domain.requests` does not exist.

- [ ] **Step 5: Implement the request models**

Create `src/mybudongsan/domain/requests.py` with these public definitions:

```python
from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field, model_validator


class RequestStatus(StrEnum):
    DRAFT = "draft"
    NEEDS_CONFIRMATION = "needs_confirmation"
    APPROVED = "approved"
    RUNNING = "running"
    COMPLETED = "completed"
    RESUMABLE = "resumable"
    CANCELLED = "cancelled"


class MoneyRange(BaseModel):
    minimum: Decimal = Field(ge=0)
    maximum: Decimal = Field(gt=0)

    @model_validator(mode="after")
    def validate_order(self) -> MoneyRange:
        if self.minimum > self.maximum:
            raise ValueError("minimum must not exceed maximum")
        return self


class RegionCriterion(BaseModel):
    name: str = Field(min_length=1)
    allow_expansion: bool = False


class BuyerProfile(BaseModel):
    profile_id: str = Field(min_length=1)
    liquidity_weight: int = 35
    commute_weight: int = 30
    price_weight: int = 25
    residential_weight: int = 10

    @model_validator(mode="after")
    def validate_weight_total(self) -> BuyerProfile:
        total = (
            self.liquidity_weight
            + self.commute_weight
            + self.price_weight
            + self.residential_weight
        )
        if total != 100:
            raise ValueError("profile weights must total 100")
        return self


class SearchRequest(BaseModel):
    request_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    regions: Annotated[list[RegionCriterion], Field(min_length=1, max_length=5)]
    budget: MoneyRange
    required: list[str] = Field(default_factory=list)
    preferred: list[str] = Field(default_factory=list)
    excluded: list[str] = Field(default_factory=list)
    special_questions: list[str] = Field(default_factory=list)
    status: RequestStatus = RequestStatus.DRAFT
```

Create empty `src/mybudongsan/__init__.py` and package directories with `__init__.py` files.

- [ ] **Step 6: Verify and commit**

Run:

```bash
uv run pytest tests/unit/test_requests.py -v
uv run ruff check .
uv run mypy src
```

Expected: all tests pass and both static checks exit 0.

Commit:

```bash
git add .gitignore .python-version pyproject.toml uv.lock src tests/unit/test_requests.py
git commit -m "feat: bootstrap request domain"
```

### Task 2: Add SQLite Models, Migrations, and Repositories

**Files:**
- Create: `alembic.ini`
- Create: `migrations/env.py`
- Create: `migrations/versions/0001_initial.py`
- Create: `src/mybudongsan/storage/database.py`
- Create: `src/mybudongsan/storage/models.py`
- Create: `src/mybudongsan/storage/repositories.py`
- Create: `tests/integration/test_repositories.py`

**Interfaces:**
- Consumes: `SearchRequest`, `RequestStatus`
- Produces: `Database`, `RequestRepository.save_version()`, `RequestRepository.get_version()`, `RunRepository.create()`

- [ ] **Step 1: Write repository integration tests**

Create a temporary SQLite fixture in `tests/conftest.py` that returns a `Database` created from `sqlite+pysqlite:///:memory:` and calls `create_schema()`.

Create `tests/integration/test_repositories.py` with tests that:

```python
from decimal import Decimal

import pytest

from mybudongsan.domain.requests import MoneyRange, RegionCriterion, SearchRequest
from mybudongsan.storage.repositories import RequestRepository


def test_request_versions_are_immutable(database) -> None:  # type: ignore[no-untyped-def]
    repository = RequestRepository(database)
    request = SearchRequest(
        request_id="req-001",
        version=1,
        regions=[RegionCriterion(name="서울 강서구")],
        budget=MoneyRange(minimum=Decimal("600000000"), maximum=Decimal("900000000")),
    )
    repository.save_version(request)
    with pytest.raises(ValueError, match="already exists"):
        repository.save_version(request)


def test_request_round_trip_preserves_regions(database) -> None:  # type: ignore[no-untyped-def]
    repository = RequestRepository(database)
    request = SearchRequest(
        request_id="req-002",
        version=1,
        regions=[RegionCriterion(name="부천시", allow_expansion=True)],
        budget=MoneyRange(minimum=Decimal("500000000"), maximum=Decimal("700000000")),
    )
    repository.save_version(request)
    loaded = repository.get_version("req-002", 1)
    assert loaded == request
```

- [ ] **Step 2: Run tests and verify failure**

Run: `uv run pytest tests/integration/test_repositories.py -v`

Expected: collection fails because storage modules do not exist.

- [ ] **Step 3: Implement database and ORM models**

Implement `Database` with an SQLAlchemy engine, session factory, `session()` context manager, and `create_schema()` method. Implement typed declarative models for these tables:

```text
buyer_profiles
search_requests        unique(request_id, version)
research_runs          unique(run_id), foreign key to request/version
listings               unique(source, source_listing_id)
listing_snapshots      foreign key to listing
evidence               foreign key to run and optional listing
assessments            unique(run_id, listing_id)
reports                foreign key to run
notification_events    unique(run_id, event_type, channel)
```

Store `SearchRequest.model_dump(mode="json")` in a JSON column. `RequestRepository.save_version()` must reject an existing `(request_id, version)` instead of updating it. `get_version()` must validate stored JSON through `SearchRequest.model_validate()`.

- [ ] **Step 4: Add Alembic baseline**

Configure `migrations/env.py` to read `MYBUDONGSAN_DB_URL`, import `Base.metadata`, and use batch mode for SQLite. Create `0001_initial.py` with all nine tables and the exact unique constraints listed above.

Run:

```bash
MYBUDONGSAN_DB_URL=sqlite:///./data/test-migration.sqlite3 uv run alembic upgrade head
MYBUDONGSAN_DB_URL=sqlite:///./data/test-migration.sqlite3 uv run alembic current
```

Expected: upgrade exits 0 and current revision is `0001`.

- [ ] **Step 5: Verify and commit**

Run:

```bash
uv run pytest tests/integration/test_repositories.py -v
uv run ruff check .
uv run mypy src
```

Expected: all checks pass.

Commit:

```bash
git add alembic.ini migrations src/mybudongsan/storage tests/conftest.py tests/integration/test_repositories.py
git commit -m "feat: persist immutable requests and runs"
```

### Task 3: Implement Run State, Checkpoints, and Resumption

**Files:**
- Create: `src/mybudongsan/domain/runs.py`
- Create: `src/mybudongsan/workflows/research_run.py`
- Modify: `src/mybudongsan/storage/repositories.py`
- Create: `tests/unit/test_run_state.py`
- Create: `tests/integration/test_run_resume.py`

**Interfaces:**
- Consumes: `RunRepository`, approved request version
- Produces: `RunStage`, `RunStatus`, `ResearchRunService.start()`, `.advance()`, `.resume()`

- [ ] **Step 1: Write state-machine tests**

Create tests asserting this ordered path:

```python
EXPECTED_STAGES = [
    "request_approved",
    "discovery_complete",
    "filter_complete",
    "verification_complete",
    "deep_research_complete",
    "report_complete",
    "sync_complete",
]
```

Test that skipping from `request_approved` to `verification_complete` raises `InvalidTransition`, that a transient failure changes status to `resumable`, and that `resume(run_id)` returns the first unfinished stage without replaying completed stages.

- [ ] **Step 2: Run tests and verify failure**

Run: `uv run pytest tests/unit/test_run_state.py tests/integration/test_run_resume.py -v`

Expected: collection fails because run-state classes do not exist.

- [ ] **Step 3: Implement the state machine**

Define `RunStage` and `RunStatus` as `StrEnum`. Define the transition map as an immutable module constant. `ResearchRunService.advance()` must execute inside one database transaction and write `checkpoint_payload`, `completed_at`, and the idempotency key `f"{run_id}:{stage.value}"`.

Use this public exception:

```python
class InvalidTransition(ValueError):
    pass
```

`resume()` must return a typed `ResumePoint(run_id: str, next_stage: RunStage, checkpoint: dict[str, object])`.

- [ ] **Step 4: Verify and commit**

Run:

```bash
uv run pytest tests/unit/test_run_state.py tests/integration/test_run_resume.py -v
uv run ruff check .
uv run mypy src
```

Expected: all checks pass.

Commit:

```bash
git add src/mybudongsan/domain/runs.py src/mybudongsan/workflows/research_run.py src/mybudongsan/storage/repositories.py tests
git commit -m "feat: add resumable research workflow"
```

### Task 4: Ingest Listings, Deduplicate, Snapshot, and Refresh WATCH

**Files:**
- Create: `src/mybudongsan/domain/listings.py`
- Create: `src/mybudongsan/research/contracts.py`
- Create: `src/mybudongsan/research/ingest.py`
- Create: `src/mybudongsan/workflows/watch.py`
- Modify: `src/mybudongsan/storage/repositories.py`
- Create: `tests/unit/test_listing_identity.py`
- Create: `tests/integration/test_listing_ingest.py`
- Create: `tests/unit/test_watch_changes.py`

**Interfaces:**
- Consumes: structured browser output matching `ResearchBundle`
- Produces: `ListingObservation`, `ResearchBundle`, `ListingIngestService.ingest()`, `WatchChangeDetector.compare()`

- [ ] **Step 1: Write identity and change-detection tests**

Cover these exact cases:

```python
def test_source_listing_id_is_primary_identity() -> None:
    observation = ListingObservation(
        source="naver_land",
        source_listing_id="12345",
        complex_name="샘플아파트",
        area_m2=84.9,
        asking_price=850_000_000,
    )
    assert build_listing_key(observation) == "naver_land:12345"


def test_fallback_identity_is_stable_without_source_id() -> None:
    first = make_observation(source_listing_id=None, description="첫 설명")
    second = make_observation(source_listing_id=None, description="다른 설명")
    assert build_listing_key(first) == build_listing_key(second)


def test_watch_detects_price_and_status_changes() -> None:
    changes = WatchChangeDetector.compare(
        previous=make_snapshot(price=850_000_000, status="active"),
        current=make_snapshot(price=830_000_000, status="inactive"),
    )
    assert {change.field for change in changes} == {"asking_price", "status"}
```

- [ ] **Step 2: Run tests and verify failure**

Run: `uv run pytest tests/unit/test_listing_identity.py tests/unit/test_watch_changes.py -v`

Expected: collection fails because listing modules do not exist.

- [ ] **Step 3: Implement contracts and deterministic identity**

`ListingObservation` must contain source, optional source ID, canonical URL, complex name, address, optional building/floor, area, asking price, status, observed time, broker, description, and raw evidence IDs.

`build_listing_key()` must use `source:source_listing_id` when present. Otherwise normalize and hash source, complex name, building, floor, rounded area, asking price, and broker. It must never auto-merge an uncertain fuzzy match; return `duplicate_suspected=True` from the ingest result instead.

`ResearchBundle` must enforce at most 25 discovered records, 7 verified records, and 3 deep assessments.

- [ ] **Step 4: Implement persistence and WATCH comparison**

`ListingIngestService.ingest(run_id, bundle)` must upsert the canonical listing, always append a timestamped snapshot, connect evidence, and return `IngestSummary(created, updated, duplicate_suspected)`.

`WatchChangeDetector.compare()` must report only these fields: `asking_price`, `status`, `canonical_url`, and `source_listing_id`. A missing listing becomes `possibly_removed`; a new source ID with the same fallback fingerprint becomes `possibly_relisted`.

- [ ] **Step 5: Verify and commit**

Run:

```bash
uv run pytest tests/unit/test_listing_identity.py tests/unit/test_watch_changes.py tests/integration/test_listing_ingest.py -v
uv run ruff check .
uv run mypy src
```

Expected: all checks pass.

Commit:

```bash
git add src/mybudongsan/domain/listings.py src/mybudongsan/research src/mybudongsan/workflows/watch.py src/mybudongsan/storage/repositories.py tests
git commit -m "feat: ingest listings and track watch changes"
```

### Task 5: Add Mandatory Gates, Weighted Scoring, and Confidence

**Files:**
- Create: `src/mybudongsan/domain/scoring.py`
- Modify: `src/mybudongsan/storage/repositories.py`
- Create: `tests/unit/test_scoring.py`

**Interfaces:**
- Consumes: request criteria, evidence-backed dimension scores
- Produces: `EvaluationInput`, `EvaluationResult`, `evaluate_listing()`

- [ ] **Step 1: Write scoring tests**

Create tests for these behaviors:

```python
def test_required_failure_blocks_recommendation() -> None:
    result = evaluate_listing(make_input(required_passed=False, confidence=100))
    assert result.eligible is False
    assert result.total_score is None


def test_default_weighted_score() -> None:
    result = evaluate_listing(
        make_input(
            liquidity=80,
            commute=70,
            price=90,
            residential=60,
            confidence=90,
        )
    )
    assert result.total_score == 77.5


def test_confidence_below_eighty_five_is_not_recommendable() -> None:
    result = evaluate_listing(make_input(required_passed=True, confidence=84))
    assert result.eligible is True
    assert result.recommendable is False
    assert "confidence_below_85" in result.reasons
```

- [ ] **Step 2: Run tests and verify failure**

Run: `uv run pytest tests/unit/test_scoring.py -v`

Expected: collection fails because scoring definitions do not exist.

- [ ] **Step 3: Implement scoring**

Use `Decimal` internally and return one-decimal-place scores. Validate every dimension in the closed range 0–100. `EvaluationInput` must carry `evidence_ids_by_dimension`; reject a dimension score above zero when its evidence ID list is empty.

`EvaluationResult` must contain:

```python
class EvaluationResult(BaseModel):
    eligible: bool
    recommendable: bool
    total_score: float | None
    confidence: int
    reasons: list[str]
```

Persist the complete input and result JSON so later reports can explain every score.

- [ ] **Step 4: Verify and commit**

Run:

```bash
uv run pytest tests/unit/test_scoring.py -v
uv run pytest --cov=mybudongsan.domain.scoring --cov-report=term-missing
uv run ruff check .
uv run mypy src
```

Expected: tests pass and scoring module coverage is at least 95%.

Commit:

```bash
git add src/mybudongsan/domain/scoring.py src/mybudongsan/storage/repositories.py tests/unit/test_scoring.py
git commit -m "feat: score evidence-backed candidates"
```

### Task 6: Render Reports and Immutable Run Artifacts

**Files:**
- Create: `src/mybudongsan/reports/renderer.py`
- Create: `src/mybudongsan/reports/templates/report.md.j2`
- Create: `tests/unit/test_report_renderer.py`
- Create: `tests/golden/report_expected.md`

**Interfaces:**
- Consumes: approved request, run summary, up to three assessed candidates, evidence
- Produces: `ReportRenderer.render() -> RenderedArtifacts`

- [ ] **Step 1: Write a golden-file test**

The test must render a fixed request and candidates, then compare `report.md`, parsed `candidates.csv`, and parsed `run-data.json` against committed expectations. It must also assert that the report contains:

```python
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
```

- [ ] **Step 2: Run test and verify failure**

Run: `uv run pytest tests/unit/test_report_renderer.py -v`

Expected: collection fails because the report renderer does not exist.

- [ ] **Step 3: Implement deterministic rendering**

Define:

```python
class RenderedArtifacts(BaseModel):
    directory: Path
    report_path: Path
    candidates_path: Path
    run_data_path: Path
```

`ReportRenderer.render(bundle, output_root)` must create `YYYY/MM/{request_id}_{slug}/`, write UTF-8 Markdown and CSV, and serialize JSON with sorted keys. It must support zero, one, two, or three final candidates and must never invent empty rankings.

Use scenario labels `종합 추천`, `안정성 대안`, and `조건 특화 대안` only when a qualified candidate exists for that role.

- [ ] **Step 4: Verify and commit**

Run:

```bash
uv run pytest tests/unit/test_report_renderer.py -v
uv run ruff check .
uv run mypy src
```

Expected: all checks pass and generated files match the golden data.

Commit:

```bash
git add src/mybudongsan/reports tests/unit/test_report_renderer.py tests/golden
git commit -m "feat: render evidence-backed run artifacts"
```

### Task 7: Expose the Local Core Through Typer CLI

**Files:**
- Create: `src/mybudongsan/config.py`
- Create: `src/mybudongsan/cli.py`
- Create: `tests/fixtures/research_bundle.json`
- Create: `tests/integration/test_cli.py`
- Create: `README.md`

**Interfaces:**
- Consumes: all core services from Tasks 1–6
- Produces: `mybudongsan db upgrade`, `request import`, `run start`, `run ingest`, `run resume`, `run report`, `watch refresh`

- [ ] **Step 1: Write CLI tests with Typer CliRunner**

Test these command outcomes:

```text
mybudongsan request import request.json
  -> prints request_id and version
mybudongsan run start req-001 --version 1
  -> prints run_id and request_approved
mybudongsan run ingest RUN_ID tests/fixtures/research_bundle.json
  -> prints discovered, verified, deep counts
mybudongsan run report RUN_ID --output artifacts
  -> prints absolute report path
mybudongsan run resume RUN_ID
  -> prints next unfinished stage
```

The fixture must contain 15 discovered, 7 verified, and 3 deeply assessed candidates without external URLs requiring network access.

- [ ] **Step 2: Run tests and verify failure**

Run: `uv run pytest tests/integration/test_cli.py -v`

Expected: collection fails because `mybudongsan.cli` does not exist.

- [ ] **Step 3: Implement configuration and commands**

`Settings` must resolve paths from explicit CLI arguments first, then `MYBUDONGSAN_DB_URL` and `MYBUDONGSAN_DATA_DIR`, then safe local defaults under `./data`. Commands must return nonzero exit codes with Korean user-facing error messages and must not print tracebacks unless `--debug` is passed.

Do not add a daemon, HTTP listener, scheduler, or interactive TUI.

- [ ] **Step 4: Add operating documentation**

Document environment setup, database creation, fixture run, request JSON format, artifact locations, credential exclusions, and the manual WATCH flow in `README.md`. Include exact commands that pass in a clean checkout.

- [ ] **Step 5: Run the milestone-one gate and commit**

Run:

```bash
uv run pytest -m "not live" -v
uv run ruff check .
uv run mypy src
uv run mybudongsan --help
```

Expected: all tests and static checks pass; help lists the seven command groups.

Commit:

```bash
git add src/mybudongsan/cli.py src/mybudongsan/config.py tests/fixtures tests/integration/test_cli.py README.md
git commit -m "feat: deliver local research cli"
```

### Task 8: Add Google OAuth, Sheets Projection, and Drive Backup

**Files:**
- Create: `src/mybudongsan/integrations/google_auth.py`
- Create: `src/mybudongsan/integrations/sheets.py`
- Create: `src/mybudongsan/integrations/drive.py`
- Create: `tests/unit/test_google_auth.py`
- Create: `tests/integration/test_google_sync.py`
- Modify: `src/mybudongsan/cli.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: SQLite requests, run status, rendered artifacts
- Produces: `GoogleCredentialStore`, `SheetsProjection.sync_run()`, `DriveBackup.upload_run()`

- [ ] **Step 1: Define ports and failing tests**

Use fake Google services to test that:

- OAuth credential JSON is read from `keyring` service `mybudongsan-google`, never a token file.
- Sheets creates exactly `검색 요청`, `조사 현황`, `추천 결과`, and `관심 매물` tabs when absent.
- Result sync writes only managed ranges and never reads them back into SQLite.
- Request import reads only the `검색 요청` tab.
- Drive uploads `report.md`, `candidates.csv`, `run-data.json`, and evidence files to the approved folder structure.
- Repeating sync updates the same files and rows instead of duplicating them.

- [ ] **Step 2: Run tests and verify failure**

Run: `uv run pytest tests/unit/test_google_auth.py tests/integration/test_google_sync.py -v`

Expected: collection fails because Google integration modules do not exist.

- [ ] **Step 3: Implement credential storage and adapters**

`GoogleCredentialStore` must accept the local OAuth client-secret path, retrieve serialized user credentials from Keychain, refresh expired access tokens, and write refreshed credentials back to Keychain. Redact `client_secret`, `access_token`, `refresh_token`, and `id_token` from all exception text and logs.

`SheetsProjection` must use explicit A1 ranges and stable headers. `DriveBackup` must search by request folder plus filename before upload and update an existing file when found.

- [ ] **Step 4: Add CLI commands**

Add:

```text
mybudongsan google login --client-secret PATH
mybudongsan sheets import-request --spreadsheet-id ID --row ROW
mybudongsan sheets sync-run RUN_ID --spreadsheet-id ID
mybudongsan drive upload-run RUN_ID --folder-id ID
```

The login command may open a local OAuth consent browser only during explicit user invocation.

- [ ] **Step 5: Verify and commit**

Run:

```bash
uv run pytest tests/unit/test_google_auth.py tests/integration/test_google_sync.py -v
uv run ruff check .
uv run mypy src
```

Expected: all checks pass without network access.

Commit:

```bash
git add src/mybudongsan/integrations src/mybudongsan/cli.py tests README.md
git commit -m "feat: sync reports with google workspace"
```

### Task 9: Add Bounded Notification Outbox, Gmail, and PlayMCP Handoff

**Files:**
- Create: `src/mybudongsan/notifications/policy.py`
- Create: `src/mybudongsan/notifications/outbox.py`
- Create: `src/mybudongsan/integrations/gmail.py`
- Modify: `src/mybudongsan/storage/repositories.py`
- Modify: `src/mybudongsan/cli.py`
- Create: `tests/unit/test_notification_policy.py`
- Create: `tests/integration/test_notification_outbox.py`

**Interfaces:**
- Consumes: run lifecycle events
- Produces: `NotificationPolicy.plan()`, `NotificationOutbox.enqueue()`, `.pending()`, `.mark_sent()`, `GmailNotifier.send_terminal()`

- [ ] **Step 1: Write policy tests**

Test these exact sequences:

```python
def test_normal_run_emits_four_messages_without_specialist() -> None:
    events = plan_events(specialist=False, needs_action=False)
    assert [event.type for event in events] == [
        "work_started",
        "researcher_assigned",
        "finalizing",
        "completed",
    ]


def test_specialist_run_never_exceeds_five_messages() -> None:
    events = plan_events(specialist=True, needs_action=False)
    assert len(events) == 5
    assert events[-1].type == "completed"


def test_needs_action_replaces_next_progress_message() -> None:
    events = plan_events(specialist=True, needs_action=True)
    assert len(events) <= 5
    assert "needs_action" in [event.type for event in events]
    assert events[-1].type in {"completed", "failed"}
```

- [ ] **Step 2: Run tests and verify failure**

Run: `uv run pytest tests/unit/test_notification_policy.py tests/integration/test_notification_outbox.py -v`

Expected: collection fails because notification modules do not exist.

- [ ] **Step 3: Implement policy and outbox**

Use event types `WORK_STARTED`, `RESEARCHER_ASSIGNED`, `SPECIALIST_ASSIGNED`, `FINALIZING`, `NEEDS_ACTION`, `COMPLETED`, and `FAILED`.

Persist a unique `(run_id, event_type, channel)` row with concise Korean message, status, attempt count, and last error. The five-event cap counts lifecycle event types rather than channel deliveries, so one terminal event may produce both a Kakao and a Gmail row. `enqueue()` must reserve a terminal event slot, enforce the cap, and be idempotent. `mark_sent()` must require the provider message ID or the literal `provider_acknowledged`.

Add CLI commands:

```text
mybudongsan notify pending RUN_ID --channel kakao
mybudongsan notify ack EVENT_ID --provider-id ID
mybudongsan notify fail EVENT_ID --error SAFE_MESSAGE
```

The PM skill will read the pending Kakao payload, call PlayMCP `MemoChat`, and acknowledge the event. Python does not implement an MCP transport client.

- [ ] **Step 4: Implement terminal Gmail delivery**

`GmailNotifier.send_terminal()` must reject nonterminal event types and send plain-text UTF-8 email containing run ID, outcome, concise conclusion, and Drive report link when available. Use the same Keychain-backed Google credentials with Gmail send scope.

- [ ] **Step 5: Verify and commit**

Run:

```bash
uv run pytest tests/unit/test_notification_policy.py tests/integration/test_notification_outbox.py -v
uv run ruff check .
uv run mypy src
```

Expected: all checks pass; fake providers show no duplicate sends after resume.

Commit:

```bash
git add src/mybudongsan/notifications src/mybudongsan/integrations/gmail.py src/mybudongsan/storage/repositories.py src/mybudongsan/cli.py tests
git commit -m "feat: add bounded lifecycle notifications"
```

### Task 10: Package the PM, Researcher, and Conditional Specialist Workflow

**Files:**
- Create: `skills/mybudongsan-pm/SKILL.md`
- Create: `skills/mybudongsan-pm/references/researcher.md`
- Create: `skills/mybudongsan-pm/references/specialist-market.md`
- Create: `skills/mybudongsan-pm/references/specialist-transit.md`
- Create: `skills/mybudongsan-pm/references/specialist-urban-planning.md`
- Create: `skills/mybudongsan-pm/references/report-contract.md`
- Create: `tests/unit/test_skill_contract.py`

**Interfaces:**
- Consumes: CLI JSON schemas and lifecycle notification commands
- Produces: one PM skill and four role contracts

- [ ] **Step 1: Write contract tests before the skill**

Create a test that reads all skill files and asserts:

- PM is the only role that communicates conclusions to the user.
- PM may dispatch one integrated researcher and at most one specialist.
- Every browser role explicitly requires the installed `ego-browser` skill.
- Discovery, verification, and deep-analysis caps are 25, 7, and 3.
- Official sources are preferred and urban plans use `확정`, `추진`, `검토`.
- Search results are not called active listings until an individual listing page is checked.
- Research output validates against `ResearchBundle` JSON.
- PM never auto-relaxes mandatory criteria.
- Kakao calls use only PlayMCP `MemoChat`; no other PlayMCP tool is allowed.
- PM acknowledges a notification only after successful tool delivery.

- [ ] **Step 2: Run tests and verify failure**

Run: `uv run pytest tests/unit/test_skill_contract.py -v`

Expected: fails because the skill files do not exist.

- [ ] **Step 3: Write the PM skill**

`SKILL.md` must define this control loop:

```text
intake -> validate -> show normalized request -> user approval
-> persist request -> start run -> emit WORK_STARTED
-> dispatch integrated researcher -> emit RESEARCHER_ASSIGNED
-> ingest structured bundle -> deterministic filtering
-> optional one-specialist escalation -> optional SPECIALIST_ASSIGNED
-> verify up to 7 -> deeply analyze up to 3
-> emit FINALIZING -> render report -> sync Google
-> emit COMPLETED or FAILED -> deliver pending notifications
```

It must stop for login, CAPTCHA, permission, or required-condition relaxation. It must resume with `mybudongsan run resume` and must not repeat successful stages or notifications.

- [ ] **Step 4: Write role and report contracts**

Each role file must declare its inputs, allowed sources, required output fields, stopping conditions, and what it must not do. The researcher contract must output JSON only after browser evidence has been captured. Specialist files must operate only on the exact question passed by PM and must not redo the whole search.

The report contract must require source URL, accessed time, evidence type, confidence, unsupported claims, and the final status `recommend`, `hold`, `exclude`, or `no_recommendation`.

- [ ] **Step 5: Verify and commit**

Run:

```bash
uv run pytest tests/unit/test_skill_contract.py -v
uv run ruff check .
```

Expected: contract tests pass.

Commit:

```bash
git add skills/mybudongsan-pm tests/unit/test_skill_contract.py
git commit -m "feat: define lean codex research team"
```

### Task 11: Run Acceptance Tests and Document Personal Installation

**Files:**
- Create: `tests/integration/test_end_to_end_fixture.py`
- Create: `tests/live/test_live_research_smoke.py`
- Create: `docs/operations.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: complete V1 package and PM skill
- Produces: repeatable fixture acceptance test, gated live smoke test, installation and recovery guide

- [ ] **Step 1: Write the fixture acceptance test**

The test must execute this complete local flow through service interfaces:

```text
create approved request with five regions
create run
ingest fixture bundle
deduplicate listings
evaluate seven verified listings
select zero to three recommendable candidates
render Markdown/CSV/JSON
enqueue and acknowledge lifecycle notifications
project fake Sheets rows
upload fake Drive artifacts
interrupt after verification
resume without duplicate listings, artifacts, or notifications
```

Assert final run status `sync_complete`, no more than five notification events, and exact artifact checksums across the retry.

- [ ] **Step 2: Run test and fix only integration defects**

Run: `uv run pytest tests/integration/test_end_to_end_fixture.py -v`

Expected: PASS. If it fails, change the smallest owning module and add a focused regression test there before rerunning.

- [ ] **Step 3: Add a guarded live smoke test**

The live test must be skipped unless all of these are set:

```text
MYBUDONGSAN_LIVE_TEST=1
MYBUDONGSAN_TEST_SPREADSHEET_ID
MYBUDONGSAN_TEST_DRIVE_FOLDER_ID
MYBUDONGSAN_TEST_EMAIL
```

It must create one clearly prefixed test row and one small test artifact, send one terminal test email, and leave cleanup identifiers in test output. PlayMCP delivery is verified manually through the PM skill because the MCP call is agent-owned.

- [ ] **Step 4: Write the operations guide**

Document:

- local installation with uv
- initial database migration
- Google personal OAuth setup and Keychain behavior
- PlayMCP connection and `MemoChat` allow-list
- how to create and approve a request
- how to start, inspect, resume, and cancel a run
- how to refresh WATCH manually
- how to locate local and Drive artifacts
- how to recover Google sync and notification failures
- how to delete one run and its artifacts without deleting the whole database
- the two-hour timebox and 85-confidence recommendation gate

- [ ] **Step 5: Run the final offline gate**

Run:

```bash
uv run pytest -m "not live" --cov=mybudongsan --cov-report=term-missing
uv run ruff check .
uv run mypy src
uv run mybudongsan --help
git status --short
```

Expected: tests, lint, and type checks pass; CLI help exits 0; Git shows only intentional documentation or test changes.

- [ ] **Step 6: Perform user-authorized live verification**

After the user has configured OAuth and PlayMCP, run:

```bash
MYBUDONGSAN_LIVE_TEST=1 uv run pytest tests/live/test_live_research_smoke.py -v -m live
```

Expected: Google row, Drive file, and Gmail message are created in the designated test resources. Then run one PM-controlled PlayMCP `MemoChat` test and confirm receipt in the Kakao self-chat.

- [ ] **Step 7: Commit the completed V1**

```bash
git add tests docs README.md
git commit -m "test: verify mybudongsan v1 workflow"
```

## Final Verification Checklist

- [ ] One to five regions validate and approved request versions are immutable.
- [ ] Fixture flow enforces 25/7/3 caps.
- [ ] Mandatory failures never become recommendations.
- [ ] Confidence below 85 never becomes a final recommendation.
- [ ] Duplicate listings create snapshots, not duplicate canonical records.
- [ ] Interrupted runs resume without replaying successful stages.
- [ ] Reports contain source URLs, accessed times, risks, and missing evidence.
- [ ] Sheets and Drive syncs are idempotent and SQLite remains canonical.
- [ ] OAuth and PlayMCP secrets are absent from Git, SQLite, artifacts, and logs.
- [ ] WATCH refresh is manual.
- [ ] Notification count is at most five and terminal events reach both channels.
- [ ] No server, scheduler, worker queue, or web UI was introduced.
