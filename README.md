# Slack → Notion 월별 아카이버

Slack의 월별 메시지와 이미지를 채널별로 Notion 데이터베이스에 보관하는 자동화 도구입니다. 매월 GitHub Actions로 실행하거나 필요할 때 수동으로 실행할 수 있습니다.

외부 Python 패키지가 필요하지 않으며 Python 표준 라이브러리만 사용합니다. Python 3.9 이상이 필요합니다(`zoneinfo` 사용).

## 구조

```text
archive.py              CLI 진입점, 실행 흐름, mock 데이터
app/models.py           MonthWindow, DownloadedFile, 월 계산, Notion DB 스키마 상수
app/http_client.py      재시도·백오프를 포함한 JSON/multipart HTTP
app/slack_client.py     채널 탐색, 메시지 조회, 파일 다운로드
app/notion_client.py    스키마 검증, 파일 업로드, 아카이브 행 생성
app/renderer.py         Notion 블록과 Markdown 미리보기 생성
tests/test_archive.py   단위 테스트
```

Notion DB의 속성 이름과 상태 값은 `app/models.py`에 한 벌만 정의합니다. 미리보기(`renderer`)와 실제 기록(`notion_client`)이 같은 상수를 참조해야 미리보기가 실제와 다른 상태를 표시하지 않습니다.

## 동작 방식

한 번 실행하면 다음 순서로 처리합니다.

1. 봇이 접근할 수 있는 Slack 채널을 탐색합니다.
2. 지정한 월의 메시지와 같은 달에 작성된 스레드 답글을 조회합니다.
3. Notion DB의 `채널`·`기간` Select 옵션과 대조해 없는 라벨을 생성합니다.
4. 동일한 `채널 + 기간` 행이 있는지 확인합니다.
5. 새 행의 본문에 메시지, 이미지, 첨부파일 정보와 Slack 원문 링크를 저장합니다.
6. 저장 상태를 `진행 중`에서 `완료` 또는 `실패`로 변경합니다.

채널 접근 범위는 `AUTO_JOIN_PUBLIC_CHANNELS` 하나로 결정합니다.

| 설정값 | 처리 대상 |
|---|---|
| `false` 또는 미설정 | 봇이 이미 참여한 공개·비공개 채널 |
| `true` | 봇이 참여한 채널 + 자동 참여 가능한 모든 공개 채널 |

비공개 채널은 `true`로 설정해도 자동 참여할 수 없습니다. 해당 채널에서 봇을 직접 초대해야 합니다.

## 저장 결과

채널과 월마다 Notion DB 행 하나를 생성합니다. 행의 상세 페이지 본문에 실제 메시지와 이미지가 들어갑니다.

| 이름 | 채널 | 기간 | 상태 |
|---|---|---|---|
| `Slack · 2026-08 · #general` | `#general` | `2026-08` | `완료` |
| `Slack · 2026-08 · #product` | `#product` | `2026-08` | `완료` |

동일한 `채널 + 기간` 행이 `완료` 상태로 이미 있으면 기존 내용을 덮어쓰지 않고 건너뜁니다. `진행 중`이나 `실패`로 남은 미완료 행은 휴지통으로 보낸 뒤 다시 만듭니다.

## 빠른 데모

토큰 없이 샘플 출력과 테스트를 확인할 수 있습니다.

```bash
python3 archive.py --mock --month 2026-08
python3 -m unittest discover -s tests -v
```

Mock 모드는 Notion에 데이터를 쓰거나 이미지를 다운로드하지 않으므로 `--mock`과 `--publish`는 함께 사용할 수 없습니다.

## 설치

### 1. Slack 앱 만들기

Slack API의 **Your Apps → Create New App → From scratch**에서 앱을 생성합니다.

기본 Bot Token Scopes:

```text
channels:read
channels:history
users:read
files:read
```

`AUTO_JOIN_PUBLIC_CHANNELS=true`를 사용하려면 다음 권한도 추가합니다.

```text
channels:join
```

초대받은 비공개 채널도 아카이빙하려면 다음 권한을 추가합니다.

```text
groups:read
groups:history
```

권한 설정 후 앱을 워크스페이스에 설치하고 Bot User OAuth Token(`xoxb-...`)을 복사합니다. 권한을 나중에 추가했다면 앱을 다시 설치해야 새 권한이 토큰에 반영됩니다.

자동 참여를 사용하지 않는 경우 아카이빙할 채널에서 다음 명령으로 봇을 초대합니다.

```text
/invite @봇이름
```

### 2. Notion DB 준비하기

1. Notion `Settings → Connections`에서 내부 연결을 생성하고 토큰을 복사합니다.
2. 아카이브용 데이터베이스에 아래 속성을 정확한 이름과 타입으로 만듭니다.
3. 데이터베이스의 `Connections`에 생성한 내부 연결을 추가합니다.
4. `Manage data sources → Copy data source ID`에서 데이터 소스 ID를 복사합니다.

필수 속성:

| 속성명 | 타입 | 필요한 값 |
|---|---|---|
| `이름` | Title | 자동 생성 |
| `채널` | Select | 옵션 자동 생성 |
| `기간` | Select | 옵션 자동 생성 |
| `상태` | Status | `진행 중`, `완료`, `실패` |

`채널`과 `기간` 옵션은 미리 만들 필요가 없습니다. 실행 시 Slack 채널과 대상 월을 확인해 누락된 옵션만 추가하며 기존 옵션은 보존합니다.

### 3. GitHub Actions 설정하기

저장소의 **Settings → Secrets and variables → Actions**에서 다음 값을 등록합니다.

Repository secrets:

| 이름 | 필수 | 설명 |
|---|---|---|
| `SLACK_BOT_TOKEN` | 예 | Slack Bot User OAuth Token |
| `NOTION_TOKEN` | 예 | Notion 내부 연결 토큰 |

Repository variables:

| 이름 | 필수 | 설명 |
|---|---|---|
| `NOTION_DATA_SOURCE_ID` | 예 | 아카이브 DB의 데이터 소스 ID |
| `SLACK_WORKSPACE_URL` | 권장 | `https://workspace.slack.com` 형식. 원문 링크 생성에 사용 |
| `MAX_IMAGE_MB` | 아니요 | 이미지 하나의 최대 크기. 기본값 `200`, 허용 범위 `1`~`5120` |
| `AUTO_JOIN_PUBLIC_CHANNELS` | 아니요 | `false`가 기본값이며, `true`면 모든 공개 채널에 자동 참여 |

`SLACK_CHANNELS` 같은 채널 목록 변수는 사용하지 않습니다.

## 실행

### 자동 실행

[archive.yml](.github/workflows/archive.yml)은 매월 **1일·11일·21일** 00:17 UTC(= 09:17 KST)에 실행되어 지난달 메시지를 아카이빙합니다. GitHub Actions의 cron은 UTC만 지원하므로 워크플로에는 UTC 기준 시각을 적습니다.

한 달에 세 번 실행하는 이유는 장애 대비입니다. 예정된 실행 시각에 GitHub Actions 자체가 중단되어 있으면 그 실행은 지연되는 것이 아니라 **큐에 등록되지 않고 사라지며, 실패 알림조차 남지 않습니다.** 한 달에 한 번만 실행하면 그 한 번을 놓칠 때 해당 월 전체가 조용히 유실됩니다. 11일과 21일 실행은 이를 위한 안전망입니다.

추가 실행의 비용은 거의 없습니다. 이미 `완료`인 채널은 Notion 조회 한 번으로 건너뛰며, **Slack 메시지를 다시 읽지 않습니다.** Slack 조회는 해당 채널이 실제로 아카이빙이 필요하다고 판정된 뒤에만 수행합니다.

31일은 일부러 제외했습니다. 31일이 없는 달이 다섯 달이라 오히려 간격이 불규칙해지기 때문입니다.

각 실행 안에서도 스텝이 최대 3회까지 재시도합니다(5분·10분 간격). 이는 다음 예정 실행이 열흘 뒤인 상황에서 발생한 일시적 Slack·Notion 오류를 복구하기 위한 것입니다.

### GitHub에서 수동 실행

1. 저장소의 **Actions** 탭을 엽니다.
2. **Archive Slack to Notion**을 선택합니다.
3. **Run workflow**를 누릅니다.
4. `month`에 `YYYY-MM` 형식의 월을 입력하고 실행합니다.

`month`를 비우면 KST 기준 지난달을 처리합니다.

GitHub CLI로도 실행할 수 있습니다.

```bash
# 특정 월
gh workflow run archive.yml \
  --repo Kimgyuilli/slack-notion-monthly-archive \
  -f month=2026-08

# 지난달
gh workflow run archive.yml \
  --repo Kimgyuilli/slack-notion-monthly-archive
```

### 로컬에서 실행

```bash
export SLACK_BOT_TOKEN="xoxb-..."
export NOTION_TOKEN="ntn_..."
export NOTION_DATA_SOURCE_ID="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
export SLACK_WORKSPACE_URL="https://workspace.slack.com"
export AUTO_JOIN_PUBLIC_CHANNELS="false"

# Slack 데이터만 읽고 Markdown으로 미리보기
python3 archive.py --month 2026-08

# Notion에 실제 저장
python3 archive.py --month 2026-08 --publish

# KST 기준 지난달을 Notion에 저장
python3 archive.py --publish

# 이미지 크기 한도를 50MB로 낮춰서 저장
python3 archive.py --month 2026-08 --publish --max-image-mb 50
```

`--publish`가 없으면 Notion에는 아무것도 쓰지 않습니다. `--max-image-mb`는 `MAX_IMAGE_MB` 환경 변수보다 우선하며, 이미지를 내려받지 않는 미리보기에서는 사용하지 않습니다.

## 저장 범위

- 대상 월에 작성된 최상위 메시지
- 대상 월에 작성된 해당 메시지의 스레드 답글
- 사용자 표시 이름과 멘션
- 링크와 리액션 수
- Slack에 직접 업로드된 이미지 원본
- 이미지가 아닌 첨부파일의 이름
- 각 메시지의 Slack 원문 링크
- 채널·기간 라벨과 처리 상태

20MB 이하 이미지는 단일 업로드하고, 그보다 큰 이미지는 10MB 단위 multipart 방식으로 업로드합니다. 이미지 업로드가 실패하거나 `MAX_IMAGE_MB`를 초과하면 파일명과 Slack 원문 링크를 남기고 다른 메시지의 아카이빙은 계속합니다.

## 재실행과 오류 처리

- Slack·Notion API의 일시 오류와 `429` 응답은 자동 재시도합니다.
- 동일한 `채널 + 기간` 행은 중복 생성하지 않습니다.
- 본문 저장에 실패하면 해당 행의 상태를 `실패`로 변경합니다.
- 상태가 `완료`인 행만 "이미 처리됨"으로 건너뜁니다. 이 경우 Slack 메시지를 다시 읽지 않습니다.
- `진행 중`이나 `실패`로 남은 행은 본문이 불완전하므로 **자동으로 휴지통에 보내고 다시 생성합니다.** 중복 판정이 `채널 + 기간`만 보기 때문에, 이런 행을 그대로 두면 재실행해도 영원히 건너뛰게 되어 어떤 재시도로도 복구할 수 없습니다. 복구된 행은 `복구`로 출력되고 요약에 `미완료 행 복구 N개`로 집계됩니다.
- 휴지통의 페이지는 Notion이 30일간 보관하므로, 자동 복구가 버린 부분 저장 본문도 필요하면 수동으로 되살릴 수 있습니다.
- `AUTO_JOIN_PUBLIC_CHANNELS=false`로 변경해도 이미 참여한 공개 채널에서는 자동으로 나가지 않습니다. 제외하려면 해당 채널에서 봇을 제거해야 합니다.

## API 한도

이 도구의 처리량은 코드보다 Slack·Notion API의 한도가 결정합니다.

### Slack

| 항목 | 값 |
|---|---|
| `conversations.history`·`replies` | 내부 앱은 Tier 3(분당 50회+), 한 번에 최대 1,000건 |
| 페이지 크기 | `1000`으로 요청. 요청 수를 줄여 tier 예산을 아낍니다 |
| `users.list` | 200건씩 조회(더 큰 값은 권장되지 않음) |
| `429` 응답 | `Retry-After`를 따르며 자동 재시도 |

**중요**: 2025-05-29부터 **Marketplace에 등재되지 않은 배포용(unlisted) 앱**은 `conversations.history`와 `conversations.replies`가 **분당 1회, 요청당 15건**으로 제한됩니다(기존 설치분은 2025-09-02부터 적용). 이 한도에서는 월별 아카이빙이 현실적으로 불가능합니다.

이 도구는 자기 워크스페이스에 직접 설치하는 **내부 앱**을 전제하며, 내부 앱은 이 변경에서 제외되어 기존 한도를 유지합니다. 앱을 워크스페이스 외부에 배포하지 마세요.

스레드마다 `conversations.replies` 1회가 필요하므로 스레드 수가 실행 시간을 지배합니다. 스레드 3,000개면 분당 50회 기준 약 1시간입니다. 워크플로의 `timeout-minutes`는 350(GitHub 호스티드 잡 상한 360분)으로 설정되어 있습니다.

### Notion

| 항목 | 값 | 대응 |
|---|---|---|
| 요청 속도 | 평균 초당 3회 | 요청 사이 0.35초 대기 |
| 요청당 블록 수 | 최대 100개 | 100개 단위로 분할 |
| 요청 크기 | **최대 500KB** | 400KB를 넘기 전에 분할 |
| `text.content` | 최대 2,000자 | 1,900자 단위로 분할 |
| 배열 요소 수 | 최대 100개 | Slack 메시지 상한(40,000자)이면 최대 24개 |
| 파일 크기 | 무료 5MiB, 유료 5GiB | `MAX_IMAGE_MB` 기본값은 `200` |

요청 크기 한도는 블록 개수만으로 나누면 넘길 수 있습니다. 한글은 UTF-8에서 한 자가 3바이트라, 2,000자 정도의 글 100개만 모여도 한 요청이 600KB를 넘습니다. 그래서 개수와 바이트를 함께 보고 분할합니다.

**무료 워크스페이스는 파일 하나가 5MiB로 제한됩니다.** `MAX_IMAGE_MB` 기본값 `200`은 유료 플랜 기준이므로, 무료 플랜이라면 `5`로 낮추세요. 초과한 이미지는 업로드에 실패하고 파일명과 원문 링크만 남습니다.

## 제한 사항

- 월이 시작되기 전에 작성된 오래된 스레드에 대상 월 동안 새 답글만 추가된 경우, 월별 `conversations.history` 조회로는 그 스레드를 발견하지 못할 수 있습니다.
- 아카이빙 전에 삭제된 메시지는 복구할 수 없습니다.
- Google Drive 등 외부 서비스의 이미지는 Slack 원본 다운로드 URL이 없어 파일명과 원문 링크만 저장될 수 있습니다.
- 현재 이미지 MIME 타입(`image/*`)만 Notion에 원본 업로드하며 PDF와 일반 파일은 이름만 기록합니다.
- 봇이 참여하지 않은 비공개 채널과 DM은 처리하지 않습니다.
- Slack Block Kit 서식을 Notion에서 완전히 동일하게 재현하지는 않습니다.

감사 또는 법적 보존 수준이 필요하다면 월별 조회 방식 대신 Slack Events API 기반 실시간 수집, 원본 JSON 저장소, 파일 원본 보관과 삭제·수정 이벤트 처리가 추가로 필요합니다.
