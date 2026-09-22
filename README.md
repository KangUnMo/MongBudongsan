# MyBudongsan

개인용 아파트 매수 조사 결과를 로컬 SQLite와 Markdown/CSV/JSON 아티팩트에 남기는 CLI입니다. 이 버전은 단일 사용자용이며 서버, 데몬, 자동 스케줄러, 네트워크 수집 기능을 실행하지 않습니다.

## 시작하기

Python 3.12와 [uv](https://docs.astral.sh/uv/)가 필요합니다. 새 체크아웃에서 아래 명령을 순서대로 실행하면 외부 서비스나 자격 증명 없이 결정론적 fixture 흐름을 실행할 수 있습니다.

```bash
uv sync
mkdir -p .local
cat > .local/request.json <<'JSON'
{
  "request_id": "req-001",
  "version": 1,
  "regions": [{"name": "서울 테스트구"}],
  "budget": {"minimum": 600000000, "maximum": 1000000000},
  "status": "approved"
}
JSON
uv run mybudongsan --data-dir .local/data db upgrade
uv run mybudongsan --data-dir .local/data request import .local/request.json
uv run mybudongsan --data-dir .local/data run start req-001 --version 1 --run-id run-fixture
uv run mybudongsan --data-dir .local/data run ingest run-fixture tests/fixtures/research_bundle.json
uv run mybudongsan --data-dir .local/data run report run-fixture --output .local/artifacts
uv run mybudongsan --data-dir .local/data run resume run-fixture
```

`research_bundle.json`은 네트워크 URL 없이 정확히 15개 발견, 7개 검증, 3개 심층 조사 후보를 포함합니다. 각 심층 후보에는 `fixture://` 로컬 근거 4개와 그 근거 ID를 참조하는 `evaluation_input`이 있습니다. `run ingest`는 근거를 매물에 연결하고 정본 평가 로직으로 세 후보의 평가를 저장한 뒤에만 심층 조사 단계를 완료합니다. 실행 결과의 `report_path`는 절대 경로로 출력됩니다.

## 구성과 데이터 위치

경로 우선순위는 다음과 같습니다.

1. CLI: `--db-url`, `--data-dir`, `run report --output`
2. 환경 변수: `MYBUDONGSAN_DB_URL`, `MYBUDONGSAN_DATA_DIR`
3. 현재 작업 디렉터리의 안전한 기본값: `./data/mybudongsan.sqlite3`, `./data/artifacts`

예를 들어 환경 변수를 사용할 수 있습니다.

```bash
export MYBUDONGSAN_DATA_DIR="$PWD/.local/data"
export MYBUDONGSAN_DB_URL="sqlite+pysqlite:///$PWD/.local/data/mybudongsan.sqlite3"
uv run mybudongsan db upgrade
```

SQLite가 정본 데이터입니다. 데이터베이스 생성과 마이그레이션 적용은 항상 `mybudongsan db upgrade`로 시작합니다.

## 요청 JSON 형식

요청은 버전별로 저장되며, 조사를 시작하려면 `status`가 `approved`여야 합니다. 최소 형식은 다음과 같습니다.

```json
{
  "request_id": "req-001",
  "version": 1,
  "regions": [{"name": "서울 강서구", "allow_expansion": false}],
  "budget": {"minimum": 600000000, "maximum": 900000000},
  "required": ["아파트"],
  "preferred": ["역 도보 15분 이내"],
  "excluded": ["반지하"],
  "special_questions": ["개발계획 진행 단계"],
  "status": "approved"
}
```

`run start`는 `request_approved` 단계로 시작합니다. `run ingest`는 fixture 또는 브라우저에서 이미 캡처한 `ResearchBundle` JSON만 저장하고 인터넷에 연결하지 않습니다. 이후 `run resume`은 저장된 체크포인트에서 다음 미완료 단계를 보여 줍니다.

## 아티팩트

`run report`는 해당 실행의 SQLite 정본 매물·평가·근거를 다시 읽어 최대 3개 후보를 구성하고, 출력 루트 아래 `YYYY/MM/<request_id>_<run_slug>/`에 변경 불가능한 스냅샷을 생성합니다. 저장되지 않은 시나리오, 발견 사항, 추천 문구는 임의로 만들지 않습니다. 렌더링과 보고서 본문의 SQLite 저장이 모두 성공한 뒤에만 실행 단계가 `report_complete`로 바뀝니다.

- `report.md`: 사람이 읽는 보고서
- `candidates.csv`: 후보 표
- `run-data.json`: 보고서에 사용한 정본 입력

같은 run의 같은 출력 위치에 이미 발행된 아티팩트는 덮어쓰지 않습니다. 새 보고서가 필요하면 다른 `--output` 루트를 사용하세요.

## 수동 WATCH

WATCH는 의도적으로 자동 실행하지 않습니다. 두 시점의 단일 `ListingObservation` JSON을 캡처한 뒤 수동으로 비교합니다.

```bash
cat > .local/previous.json <<'JSON'
{"source":"fixture","source_listing_id":"sample-1","asking_price":700000000,"status":"active"}
JSON
cat > .local/current.json <<'JSON'
{"source":"fixture","source_listing_id":"sample-1","asking_price":680000000,"status":"active"}
JSON
uv run mybudongsan watch refresh .local/previous.json .local/current.json
```

관측 부재를 표현하려면 파일 내용 전체를 JSON `null`로 저장합니다. 경로가 존재하지 않는 것은 관측 부재가 아니라 입력 오류이며 한국어 메시지와 0이 아닌 종료 코드로 끝납니다.

```bash
printf 'null\n' > .local/current.json
uv run mybudongsan watch refresh .local/previous.json .local/current.json
```

출력은 가격, 상태, URL, 원본 매물 ID의 변경과 가능한 재등록/삭제를 보여 줍니다. 스케줄러나 알림 전송은 하지 않습니다.

## 자격 증명과 오류 처리

이 단계의 CLI는 자격 증명을 요구하거나 저장하지 않습니다. `.env`, OAuth client secret, token 파일, `data/`, 생성 아티팩트는 Git에 넣지 마세요. 특히 비밀값을 요청 JSON, fixture, SQLite, 보고서 또는 명령 출력에 넣으면 안 됩니다.

사용자 오류와 명령 파싱 오류(누락 인자, 잘못된 옵션 값, 알 수 없는 명령)는 `오류:`로 시작하는 한국어 메시지와 0이 아닌 종료 코드로 반환됩니다. 진단이 필요할 때만 전역 옵션 `--debug`을 명령 그룹보다 앞에 붙여 traceback을 확인합니다.

```bash
uv run mybudongsan --debug run resume unknown-run
```
