# Slack → Notion 월별 아카이빙 데모

Slack의 한 달 메시지를 채널별 Notion DB 행으로 만드는, 외부 Python 패키지가 필요 없는 데모입니다. 각 행은 상세 페이지이기도 하므로 메시지와 이미지는 행의 본문에 저장됩니다. 기본 실행은 Markdown 미리보기이며 `--publish`를 명시해야만 Notion에 쓰기 작업을 합니다.

## 1분 데모

실제 계정이나 토큰 없이 샘플 결과를 확인할 수 있습니다.

```bash
python3 archive.py --mock --month 2026-08
python3 -m unittest discover -s tests -v
```

샘플에는 공개 채널 메시지, 스레드 답글, 사용자 멘션, 링크, 리액션, 이미지와 첨부파일 메타데이터가 포함되어 있습니다. Mock 모드는 외부 다운로드를 하지 않으므로 실제 이미지 블록 생성은 `--publish` 실행에서 이루어집니다.

## 실제 연결

### 1. Slack 앱

Slack API의 **Your Apps → Create New App → From scratch**에서 워크스페이스 내부용 앱을 만듭니다.

Bot Token Scopes:

```text
channels:read
channels:history
users:read
files:read
```

모든 공개 채널에 자동으로 참여하게 하려면 다음 권한도 추가합니다.

```text
channels:join
```

비공개 채널도 포함하려면 다음 두 권한을 추가합니다.

```text
groups:read
groups:history
```

앱을 워크스페이스에 설치한 뒤 아카이빙할 각 채널에서 `/invite @앱이름`으로 초대합니다. 기본 설정에서는 봇이 실제 멤버인 채널만 읽습니다. `AUTO_JOIN_PUBLIC_CHANNELS=true`로 설정하면 봇이 모든 공개 채널에 자동 참여한 뒤 함께 아카이빙합니다.

기존 앱에 `files:read`를 나중에 추가했다면 변경된 권한이 토큰에 반영되도록 앱을 워크스페이스에 다시 설치합니다.

### 2. Notion 연결

1. Notion `Settings → Connections`에서 내부 연결을 만듭니다.
2. 아카이브용 데이터베이스를 만들고 아래 속성을 정확한 이름과 타입으로 추가합니다.
3. 데이터베이스의 `Connections`에 내부 연결을 추가합니다.
4. 데이터베이스 메뉴의 `Manage data sources → Copy data source ID`에서 데이터 소스 ID를 복사합니다.
5. 연결 토큰과 데이터 소스 ID를 준비합니다.

필수 DB 속성:

| 속성 | 타입 | 값 예시 |
|---|---|---|
| `이름` | Title | `Slack · 2026-08 · #general` |
| `채널` | Select | `#general` |
| `기간` | Select | `2026-08` |
| `상태` | Status | `진행 중`, `완료`, `실패` |

스크립트는 채널마다 다음과 같은 DB 행을 만들고, 해당 행의 상세 페이지 본문에 메시지와 이미지를 저장합니다.

```text
이름                         채널       기간       상태
Slack · 2026-08 · #general  #general   2026-08    완료
Slack · 2026-08 · #product  #product   2026-08    완료
```

저장을 시작할 때 상태는 `진행 중`, 본문 저장까지 끝나면 `완료`, 본문 저장 중 오류가 발생하면 `실패`가 됩니다. 실행할 때 봇이 접근할 수 있는 Slack 채널과 대상 월을 Notion DB의 `채널`·`기간` Select 옵션과 먼저 대조하며, 없는 라벨만 자동으로 추가합니다. 기존 Select 옵션은 그대로 보존합니다. 그다음 동일한 `채널 + 기간` 행이 이미 있으면 안전하게 건너뛰며 기존 행을 삭제하거나 덮어쓰지 않습니다.

### 3. 로컬 미리보기

```bash
export SLACK_BOT_TOKEN="xoxb-..."
export SLACK_WORKSPACE_URL="https://your-workspace.slack.com"

python3 archive.py --month 2026-08
```

이 단계에서는 Slack을 읽지만 Notion에는 아무것도 쓰지 않습니다.

### 4. Notion에 게시

```bash
export NOTION_TOKEN="ntn_..."
export NOTION_DATA_SOURCE_ID="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"

python3 archive.py --month 2026-08 --publish
```

## GitHub Actions 설정

저장소의 **Settings → Secrets and variables → Actions**에서 아래 값을 등록합니다. 토큰은 코드나 Repository variables가 아니라 반드시 Repository secrets에 저장합니다.

Repository secrets:

- `SLACK_BOT_TOKEN`
- `NOTION_TOKEN`
- `AUTO_JOIN_PUBLIC_CHANNELS`: 선택 사항. 기본값은 `false`. 아래의 채널 접근 로직을 제어

Repository variables:

- `SLACK_WORKSPACE_URL`: `https://your-workspace.slack.com`
- `MAX_IMAGE_MB`: 선택 사항. 이미지 하나의 최대 다운로드 크기이며 기본값은 `200`, 최대 `5120`
- `NOTION_DATA_SOURCE_ID`: 아카이빙 DB의 데이터 소스 ID

채널 접근 로직은 다음 두 가지뿐입니다.

- `false`: 봇이 초대되어 현재 멤버인 공개·비공개 채널만 조회
- `true`: 위 채널에 더해 워크스페이스의 모든 공개 채널에 자동 참여하고 조회

`true`를 사용하려면 Slack 앱에 `channels:join` 권한을 추가하고 앱을 워크스페이스에 다시 설치해야 합니다. 비공개 채널은 `true`여도 자동 참여할 수 없으므로 봇을 직접 초대해야 합니다.

`AUTO_JOIN_PUBLIC_CHANNELS=false`는 **새 채널 자동 참여만 중단**합니다. 봇이 이미 참여한 채널의 읽기 권한까지 제거하려면 해당 Slack 채널에서 앱을 별도로 제거해야 합니다.

포함된 워크플로는 매월 1일 00:17 KST에 지난달을 자동으로 아카이빙합니다.

## 수동 트리거

예약 시간을 기다리지 않고 GitHub 웹 화면이나 로컬 터미널에서 즉시 실행할 수 있습니다.

### GitHub Actions 화면에서 실행

1. 저장소의 **Actions** 탭을 엽니다.
2. 왼쪽에서 **Archive Slack to Notion** 워크플로를 선택합니다.
3. **Run workflow** 버튼을 누릅니다.
4. `month`에 아카이빙할 월을 `YYYY-MM` 형식으로 입력합니다. 예: `2026-08`
5. **Run workflow**를 눌러 실행하고 같은 화면에서 실행 로그와 결과를 확인합니다.

`month`를 비우면 KST 기준 지난달을 아카이빙합니다. 이미 같은 `채널 + 기간` 행이 Notion DB에 있으면 덮어쓰지 않고 건너뛰므로 동일한 월을 다시 실행해도 기존 페이지는 보존됩니다.

GitHub CLI를 사용한다면 다음과 같이 실행할 수도 있습니다.

```bash
gh workflow run archive.yml --repo Kimgyuilli/slack-notion-monthly-archive -f month=2026-08
```

지난달을 실행할 때는 `month` 입력을 생략합니다.

```bash
gh workflow run archive.yml --repo Kimgyuilli/slack-notion-monthly-archive
```

### 로컬 터미널에서 실행

앞에서 설명한 Slack 및 Notion 환경 변수를 먼저 설정한 다음 실행합니다.

```bash
# KST 기준 지난달
python3 archive.py --publish

# 특정 월
python3 archive.py --month 2026-08 --publish

```

`--publish`를 빼면 Slack 데이터는 읽지만 Notion에는 쓰지 않는 미리보기로 실행됩니다.

## 포함 범위

- 봇이 참여한 공개·비공개 채널
- 월 안에 작성된 최상위 메시지
- 위 메시지에 달린 같은 달의 스레드 답글
- 사용자 표시 이름과 멘션
- 리액션 수
- Slack에 직접 업로드된 이미지 원본을 Notion 이미지 블록으로 복사
- 20MB 초과 이미지는 10MB 단위 multipart 업로드
- 이미지가 아닌 첨부파일의 이름
- Slack 원문 링크
- Slack·Notion API 일시 오류 및 `429` 재시도
- Notion API의 요청당 100블록 제한 처리
- 채널·기간 Select 라벨과 진행 상태 관리
- 봇이 접근 가능한 채널 탐색 및 누락된 Select 라벨 자동 생성

## 데모의 의도적인 한계

- **다른 달에 시작된 오래된 스레드에 이번 달 새로 달린 답글**은 월말 `conversations.history` 조회만으로 발견할 수 없습니다. 완전한 보존이 필요하면 Slack Events API로 메시지 이벤트를 실시간 수집해 중간 DB에 저장해야 합니다.
- 사용자가 월말 전에 삭제한 메시지는 이 방식으로 복구할 수 없습니다.
- Google Drive 등 외부 서비스에서 공유된 이미지는 Slack 원본 다운로드 URL이 없을 수 있어 파일명과 Slack 원문 링크만 남습니다.
- 이미지 업로드가 실패하거나 `MAX_IMAGE_MB`를 넘으면 해당 이미지 이름과 Slack 원문 링크를 남기고 나머지 아카이빙은 계속합니다.
- 현재는 이미지 MIME 타입(`image/*`)만 원본을 복사하며 PDF와 일반 파일은 이름만 남깁니다.
- 앱이 참여하지 않은 비공개 채널과 구성원 간 DM은 읽을 수 없습니다.
- Slack 특유의 모든 Block Kit 서식을 1:1로 재현하지는 않습니다.
- `실패` 상태의 행도 중복으로 판단합니다. 해당 채널·월을 다시 아카이빙하려면 실패한 행을 휴지통으로 옮긴 뒤 재실행합니다.

즉, 이 데모는 **공개 업무 채널의 월별 지식 아카이브 MVP**입니다. 감사·법적 보존 수준이 필요하면 Events API 수집기, 원본 JSON 저장소, 파일 원본 저장 및 삭제/수정 이벤트 처리를 추가해야 합니다.
