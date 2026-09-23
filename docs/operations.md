# MyBudongsan 개인 운영 가이드

이 문서는 개인용 V1을 깨끗한 체크아웃에서 설치하고, 로컬 조사 실행을 운영하며,
사용자가 명시적으로 허용한 경우에만 Google 및 PlayMCP 연동을 검증하는 절차다.
SQLite가 유일한 정본이다. Sheets와 Drive는 복사본이며, 서버·스케줄러·worker
queue·웹 UI는 사용하지 않는다.

## 1. 설치와 초기 마이그레이션

Python 3.12 이상과 `uv`가 설치된 깨끗한 체크아웃에서 실행한다.

```bash
uv sync --frozen
export MYBUDONGSAN_DATA_DIR="$PWD/data"
uv run mybudongsan db upgrade
uv run mybudongsan --help
```

기본 데이터베이스는 `$MYBUDONGSAN_DATA_DIR/mybudongsan.sqlite3`, 기본 아티팩트
루트는 `$MYBUDONGSAN_DATA_DIR/artifacts`다. `.env`, OAuth client secret, token,
PlayMCP credential, `data/`, 생성 아티팩트를 Git에 추가하지 않는다. 비밀값은
요청 JSON, SQLite, Sheets, 아티팩트, 로그, 오류 설명에도 넣지 않는다.

## 2. 개인 Google OAuth와 Keychain

Google Cloud의 개인 프로젝트에서 Desktop app OAuth client를 만들고 Sheets,
Drive, Gmail API를 활성화한다. 다운로드한 client secret JSON은 저장소 밖의
개인 경로에 둔다. 로그인은 사용자가 아래 명령을 직접 실행한 경우에만 브라우저를
연다.

```bash
uv run mybudongsan google login --client-secret /개인/보안/경로/client_secret.json
```

권한 범위는 `spreadsheets`, `drive.file`, `gmail.send`다. credential은 파일이나
SQLite가 아니라 macOS Keychain의 서비스 `mybudongsan-google`, 계정 `default`에만
저장된다. 만료된 access token은 refresh token으로 갱신되어 Keychain에 다시
저장된다. `re-consent` 오류가 나오거나 범위를 변경했다면 같은 `google login`
명령을 다시 명시적으로 실행한다. 명령 출력에 credential이나 token을 붙여 넣지
않는다.

## 3. PlayMCP 연결과 허용 목록

Codex의 MCP/connector 설정에서 개인 PlayMCP 연결을 승인한다. 도구 허용 목록에는
정확히 `PlayMCP:MemoChat`만 포함한다. PM skill 실행 전에 그 완전한 도구 이름이
보이는지 확인한다. 보이지 않으면 Kakao 전달을 중단하고 연결 또는 권한을 수정한다.
다른 PlayMCP 도구로 대체하지 않으며 Python 테스트나 CLI에서 PlayMCP를 호출하지
않는다. 최종 라이브 검증은 PM이 `PlayMCP:MemoChat` 1회만 호출하고 카카오 나챗방
수신을 사람이 확인한다.

## 4. 요청 생성과 승인

CLI import는 JSON의 상태를 임의로 승인하지 않는다. 사용자에게 정규화된 조건을
보여 주고 명시적 승인을 받은 뒤 `status`를 `approved`로 저장한다. 지역은 1~5개다.

```bash
mkdir -p .local
cat > .local/request.json <<'JSON'
{
  "request_id": "req-001",
  "version": 1,
  "regions": [
    {"name": "서울 강서구", "allow_expansion": false},
    {"name": "서울 양천구", "allow_expansion": false}
  ],
  "budget": {"minimum": 600000000, "maximum": 1000000000},
  "required": ["아파트"],
  "preferred": ["역 도보 15분 이내"],
  "excluded": ["반지하"],
  "special_questions": ["개발계획 진행 단계"],
  "status": "approved"
}
JSON
uv run mybudongsan request import .local/request.json
```

개인 Google Sheet의 `검색 요청` 행을 가져오려면 다음 명령을 쓴다. 이 명령도
Pydantic 검증 후 SQLite에 새 요청 버전을 저장하며 기존 버전을 덮어쓰지 않는다.

```bash
uv run mybudongsan sheets import-request --spreadsheet-id SPREADSHEET_ID --row 2
```

## 5. 실행 시작, 조회, 재개, 취소

```bash
uv run mybudongsan run start req-001 --version 1 --run-id run-001
uv run mybudongsan run inspect run-001
uv run mybudongsan run resume run-001
uv run mybudongsan run cancel run-001
```

`inspect`는 SQLite의 status, 현재 stage, checkpoint를 JSON으로 출력한다. `resume`은
다음 미완료 stage만 보여 주므로 완료된 수집·아티팩트·알림을 다시 만들지 않는다.
`cancel`은 실행을 `cancelled`로 보존하며 데이터를 삭제하지 않는다. 취소한 실행은
재개할 수 없다.

브라우저 조사는 PM skill과 `ego-browser`가 만든 유효한 `ResearchBundle` JSON을
CLI에 넘기는 별도 수동 단계다. fixture로 오프라인 흐름을 확인하는 명령은 다음과
같다.

```bash
uv run mybudongsan run ingest run-001 tests/fixtures/research_bundle.json
uv run mybudongsan run report run-001
uv run mybudongsan run inspect run-001
```

한 실행의 상한은 발견 25, 검증 7, 심층 3이다. 심층 후보라도 모든 mandatory gate를
통과하고 confidence가 85 이상이어야만 추천할 수 있다. 하나라도 실패하거나 85
미만이면 보류·제외·추천 없음으로 남긴다. 통합 researcher는 1명이고, 중요한 미해결
질문이 실제 후보 결론을 바꿀 때만 specialist를 최대 1명 사용한다. 실행의 조사
timebox는 2시간이며, 시간이 끝나면 확인된 근거와 미확인 사항을 그대로 보고하고
범위를 자동 완화하거나 사실을 만들어 내지 않는다.

## 6. 수동 WATCH

WATCH는 예약 실행하지 않는다. 이전/현재 `ListingObservation` JSON을 준비한 뒤 사람이
명시적으로 갱신한다. 관측 부재는 파일 내용 전체를 JSON `null`로 저장한다.

```bash
uv run mybudongsan watch refresh .local/previous.json .local/current.json
```

## 7. 로컬·Sheets·Drive 아티팩트

기본 보고서는 `data/artifacts/YYYY/MM/<request_id>_<run_slug>/` 아래의
`report.md`, `candidates.csv`, `run-data.json`이다. 정확한 경로는 `run report` 출력과
`run inspect`의 `report_complete.checkpoint.report_path`에서 확인한다.

로컬 보고서를 SQLite에서 다시 읽어 Google에 단방향 투영한다.

```bash
uv run mybudongsan sheets sync-run run-001 --spreadsheet-id SPREADSHEET_ID
uv run mybudongsan drive upload-run run-001 --folder-id DRIVE_FOLDER_ID
```

Sheets는 `조사 현황`과 `추천 결과`의 run/candidate key를 재사용하고, Drive는 선택한
폴더 아래 `YYYY/MM/<request_id>_<run_slug>/`를 재사용하므로 같은 명령의 재시도는
중복 행·파일 대신 갱신이다. 두 서비스의 내용으로 SQLite를 역갱신하지 않는다.

## 8. Google 및 알림 실패 복구

Google 오류가 나면 먼저 `run inspect`로 로컬 `report_complete` checkpoint와 세
파일을 확인한다. credential 오류면 `google login`으로 재동의한 뒤 실패한
`sheets sync-run` 또는 `drive upload-run` 명령만 다시 실행한다. SQLite와 로컬
아티팩트가 정본이므로 외부 복사본을 수동 편집해 복구하지 않는다. Drive 잠금 오류는
다른 업로드가 끝났는지 확인한 후 같은 명령을 다시 실행한다.

알림은 event type 최대 5개이며 terminal `completed`/`failed`만 Kakao와 Gmail 두
채널 모두에 존재한다. Kakao 전달 전에는 반드시 claim한다.

```bash
uv run mybudongsan notify pending run-001 --channel kakao
uv run mybudongsan notify status run-001 --state dispatching --channel kakao
uv run mybudongsan notify ack EVENT_ID --provider-id PROVIDER_ID --claim-token CLAIM_TOKEN
```

PM은 출력된 같은 `event_id`와 `claim_token`으로 `PlayMCP:MemoChat` 전달 영수증을
대조한 뒤에만 `ack`한다. timeout 또는 crash로 전달 여부가 불명확하면 재전송하지
않고 `status`로 같은 claim을 조회한다. 미전달이 확인된 경우에만 다음 명령으로
실패 처리하고 그 복구 차례를 끝낸다. 다음 수동 `pending`이 새 claim token을 만든다.

```bash
uv run mybudongsan notify fail EVENT_ID --error "confirmed not delivered" --claim-token CLAIM_TOKEN
```

Gmail terminal event도 동일하다.

```bash
uv run mybudongsan notify pending run-001 --channel gmail
uv run mybudongsan notify status run-001 --state dispatching --channel gmail
uv run mybudongsan notify ack EVENT_ID --provider-id GMAIL_MESSAGE_ID --claim-token CLAIM_TOKEN
```

Gmail 전송 자체는 PM이 terminal Gmail adapter로 수행한다. claim 전송 여부가
불명확하면 Kakao와 동일하게 자동 재전송하지 않는다. claim token, payload 본문,
OAuth/PlayMCP credential은 사용자 보고서나 로그에 복사하지 않는다.

## 9. 단일 실행 안전 삭제

삭제는 기본적으로 미리보기만 한다. SQLite 파일, 요청 버전, canonical listing,
다른 run, 공유 아티팩트 상위 폴더는 삭제하지 않는다.

```bash
uv run mybudongsan run delete run-001
uv run mybudongsan run delete run-001 --execute --confirm run-001
```

두 번째 명령의 `--confirm`은 대상 run ID와 정확히 같아야 한다. 실행 시 해당 run이
소유한 snapshot, evidence, assessment, report, notification 행과 run 행, 그리고
기본 `data_dir/artifacts` 안에서 `run-data.json`의 run ID까지 검증된 단일 아티팩트
디렉터리만 제거한다. 경로가 managed root 밖이거나 root 자체거나 symlink이거나 세
필수 파일이 없으면 전체 삭제를 거부한다.

삭제는 되돌릴 수 없다. `dry_run=true` 미리보기의 row count와 경로를 확인하고,
필요하면 SQLite와 아티팩트 디렉터리를 별도 백업한 뒤 실행한다. 복구는 그 백업에서만
가능하다. 단순 중지는 `cancel`을 사용한다. custom `run report --output`처럼 managed
root 밖에 발행한 실행은 안전 삭제가 의도적으로 거부되므로 외부 파일을 자동으로
지우지 않는다.

## 10. 오프라인 완료와 사용자 승인 라이브 검증

일상 검증은 외부 서비스나 credential을 사용하지 않는다.

```bash
uv run pytest -m "not live" --cov=mybudongsan --cov-report=term-missing
uv run ruff check .
uv run mypy src
uv run mybudongsan --help
```

다음 라이브 smoke는 사용자가 테스트 전용 Sheet, Drive 폴더, 수신 이메일을 지정하고
명시적으로 승인한 뒤에만 실행한다. 테스트는 접두사 `MYBUDONGSAN_LIVE_TEST_`가 붙은
행 1개, 작은 텍스트 파일 1개, terminal 이메일 1개만 만들고 삭제 식별자를 출력한다.

```bash
MYBUDONGSAN_LIVE_TEST=1 \
MYBUDONGSAN_TEST_SPREADSHEET_ID=TEST_SPREADSHEET_ID \
MYBUDONGSAN_TEST_DRIVE_FOLDER_ID=TEST_DRIVE_FOLDER_ID \
MYBUDONGSAN_TEST_EMAIL=TEST_EMAIL \
uv run pytest tests/live/test_live_research_smoke.py -v -m live -s
```

이후 PM skill에서 `PlayMCP:MemoChat` 테스트 1회만 수행하고 카카오 나챗방 수신을
사람이 확인한다. 이 문서의 오프라인 완료는 실제 Google/PlayMCP 전달 성공을 뜻하지
않는다.
