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

## 명시적 Google Workspace 동기화

기본 조사 흐름은 계속 로컬 전용입니다. 아래 명령을 사용자가 명시적으로 실행할 때만 Google API를 호출합니다. SQLite와 이미 발행한 로컬 아티팩트가 정본이며, Sheets와 Drive는 일방향 복사본입니다.

처음 한 번만 로컬 OAuth client secret 경로로 로그인합니다. 이 명령만 로컬 브라우저 동의 화면을 열 수 있습니다. 사용자 자격 증명은 macOS Keychain의 `mybudongsan-google` 서비스에만 저장되며 token 파일은 만들지 않습니다. 만료된 자격 증명은 refresh token으로 갱신한 뒤 Keychain에 다시 저장합니다.

```bash
uv run mybudongsan google login --client-secret /안전한/경로/client_secret.json
```

OAuth 범위는 `spreadsheets`, `drive.file`, `gmail.send`입니다. `gmail.send`는 완료·실패 요약 메일만 보내는 데 사용하며 메일을 읽지 않습니다. 기존 Keychain 자격 증명에 이 범위가 없으면 자동으로 권한을 넓히지 않고 `re-consent` 오류로 중단하므로, 위 로그인 명령을 다시 명시적으로 실행해야 합니다. 다만 `spreadsheets`는 Google 계정의 Spreadsheet 접근을 승인하는 넓은 범위입니다. MyBudongsan은 사용자가 명령에 직접 제공한 `--spreadsheet-id`만 읽고 쓰지만, 그 동작상 제한은 OAuth 범위 자체를 더 좁히지 않습니다. `drive.file`은 앱이 만들었거나 사용자가 선택한 Drive 파일에 적용됩니다. client secret, access/refresh/ID token, Keychain의 자격 증명 JSON은 명령 출력이나 로그에 표시하지 않습니다.

`검색 요청` 시트의 2행 이후에는 아래 순서의 10개 열을 넣습니다. `regions`는 `[{"name":"서울 강서구","allow_expansion":false}]` 형식의 JSON이고, 목록 열은 ` | `로 구분합니다.

```text
request_id, version, regions, budget_minimum, budget_maximum, required,
preferred, excluded, special_questions, status
```

가져오기는 해당 `검색 요청` 행만 읽어 Pydantic 검증 후 SQLite 요청 버전으로 저장합니다. 결과 동기화는 명시 A1 범위에 안정적인 헤더를 쓰고, `검색 요청`, `조사 현황`, `추천 결과`, `관심 매물` 중 없는 탭만 추가합니다. 특히 동기화는 `검색 요청`의 사용자 입력 행(2행 이후)을 절대 덮어쓰지 않고 A1:J1 헤더만 관리합니다. 결과 탭을 다시 읽어 SQLite를 갱신하지 않습니다.

```bash
uv run mybudongsan sheets import-request --spreadsheet-id SPREADSHEET_ID --row 2
uv run mybudongsan sheets sync-run RUN_ID --spreadsheet-id SPREADSHEET_ID
```

Drive 업로드는 저장된 `report_complete` 체크포인트가 가리키는 아티팩트만 사용합니다. 선택한 상위 폴더 아래 `YYYY/MM/<request_id>_<run_slug>/` 구조로 `report.md`, `candidates.csv`, `run-data.json`, 그리고 `evidence/` 아래의 일반·비숨김 근거 파일만 올립니다. 세 필수 파일이 하나라도 없거나 심볼릭 링크면 실패하며, 임의의 최상위 파일·`.DS_Store`·숨김 파일·심볼릭 링크는 업로드하지 않습니다. 폴더와 파일 이름/부모/MIME type을 먼저 검색하므로 같은 실행을 다시 올리면 중복 생성 대신 기존 파일을 갱신합니다.

같은 Drive 상위 폴더에 대한 업로드는 로컬 `data_dir`의 해시된 잠금 파일로 직렬화합니다. 이미 업로드가 진행 중이면 API를 호출하기 전에 한국어 오류와 nonzero 종료 코드로 끝나며, 완료·실패 어느 경우에도 잠금은 정리됩니다.

```bash
uv run mybudongsan drive upload-run RUN_ID --folder-id DRIVE_FOLDER_ID
```

## 제한된 실행 알림 outbox

실행 하나에는 시작, 담당자 배정, 전문 담당자 배정, 마무리, 사용자 확인, 완료 또는 실패 중 최대 5개 lifecycle event type만 저장합니다. 완료와 실패는 Kakao와 Gmail 두 채널 행을 만들지만 같은 lifecycle event 하나로 계산합니다. SQLite의 `(run_id, event_type, channel)` 고유 제약으로 재개 시 같은 알림을 다시 만들지 않습니다.

Python은 Kakao나 PlayMCP 전송을 구현하지 않습니다. 향후 PM skill이 아래 `pending` 명령으로 재시도 가능한 Kakao 행을 원자적으로 claim합니다. 출력된 행은 SQLite에서 `dispatching`으로 바뀌며 attempt count가 증가하므로, 다른 동시 명령이나 재개 실행에는 다시 나오지 않습니다. PM skill은 PlayMCP `MemoChat`을 호출한 뒤, 공급자 메시지 ID 또는 공급자가 ID를 주지 않은 경우 정확한 문자열 `provider_acknowledged`로 확인합니다. 실패 설명에는 비밀값을 넣지 마세요.

```bash
uv run mybudongsan notify pending RUN_ID --channel kakao
uv run mybudongsan notify ack EVENT_ID --provider-id ID
uv run mybudongsan notify fail EVENT_ID --error "safe timeout"
```

`pending`이 JSON을 출력한 뒤 프로세스가 종료되면 자동으로 다시 전송하지 않습니다. 외부 공급자 전송 여부를 먼저 확인하세요. 전송되지 않았음이 확인된 경우에만 출력에 있던 `EVENT_ID`로 `notify fail EVENT_ID --error "safe recovery"`를 실행해 해당 채널 행을 재시도 가능 상태로 돌린 다음 `pending`을 다시 호출합니다. 공급자가 받았는지 불명확할 때 자동 재시도하면 중복 메시지가 생길 수 있으므로 재시도하지 말고 수동으로 확인합니다. 이 수동 복구 계약은 외부 공급자와 SQLite 사이의 exactly-once 전달을 보장한다고 가장하지 않습니다.

완료·실패 Gmail도 먼저 `notify pending RUN_ID --channel gmail`로 같은 outbox claim을 얻은 뒤, 동일한 Keychain 자격 증명을 쓰는 `GmailNotifier` adapter로 plain-text UTF-8 메일을 전송하고 provider message ID로 `notify ack` 해야 합니다. Gmail 실패나 전송 전 crash도 위와 같은 명시적 `notify fail` 절차만 사용합니다. 자동화 테스트는 fake Gmail service만 사용하며 브라우저나 실제 Google API를 열지 않습니다.

## 자격 증명과 오류 처리

`.env`, OAuth client secret, token 파일, `data/`, 생성 아티팩트는 Git에 넣지 마세요. 특히 비밀값을 요청 JSON, fixture, SQLite, 보고서 또는 명령 출력에 넣으면 안 됩니다.

사용자 오류와 명령 파싱 오류(누락 인자, 잘못된 옵션 값, 알 수 없는 명령)는 `오류:`로 시작하는 한국어 메시지와 0이 아닌 종료 코드로 반환됩니다. 진단이 필요할 때만 전역 옵션 `--debug`을 명령 그룹보다 앞에 붙여 traceback을 확인합니다.

```bash
uv run mybudongsan --debug run resume unknown-run
```
