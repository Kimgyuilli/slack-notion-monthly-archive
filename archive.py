#!/usr/bin/env python3
"""Archive one calendar month of Slack messages into a Notion database.

This is deliberately dependency-free so it can run on GitHub Actions without a
package installation step. By default it prints a preview. Passing --publish is
required before the script writes anything to Notion.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from typing import Any, Iterable

from app.models import KST, ArchiveError, MonthWindow, month_window
from app.notion_client import NotionClient
from app.renderer import archive_blocks, channel_label, entry_title, markdown_preview
from app.slack_client import SlackClient


DEFAULT_MAX_IMAGE_MB = 200


def is_image_file(file: dict[str, Any]) -> bool:
    return str(file.get("mimetype") or "").lower().startswith("image/")

def all_messages(messages: list[dict[str, Any]]) -> Iterable[dict[str, Any]]:
    for message in messages:
        yield message
        yield from message.get("_replies", [])

def upload_message_images(
    messages: list[dict[str, Any]],
    slack: SlackClient,
    notion: NotionClient,
    max_bytes: int,
) -> tuple[int, int]:
    """Add private Notion upload IDs to Slack image objects in-place."""
    uploaded = failed = 0
    for message in all_messages(messages):
        for file in message.get("files", []):
            if not is_image_file(file):
                continue
            downloaded: DownloadedFile | None = None
            try:
                downloaded = slack.download_file(file, max_bytes)
                file["_notion_upload_id"] = notion.upload_file(downloaded)
                uploaded += 1
            except ArchiveError as error:
                file["_archive_error"] = str(error)
                failed += 1
                filename = file.get("name") or file.get("title") or file.get("id") or "이미지"
                print(f"경고: {filename} 원본을 업로드하지 못했습니다: {error}", file=sys.stderr)
            finally:
                if downloaded:
                    downloaded.cleanup()
    return uploaded, failed

def mock_archive(window: MonthWindow) -> tuple[list[dict[str, Any]], dict[str, str]]:
    def ts(day: int, hour: int, minute: int) -> str:
        return f"{datetime(window.start.year, window.start.month, day, hour, minute, tzinfo=KST).timestamp():.6f}"

    channels = [
        {
            "id": "C01PRODUCT",
            "name": "product",
            "is_private": False,
            "messages": [
                {
                    "ts": ts(3, 9, 14),
                    "user": "U01MIN",
                    "text": "이번 배포는 목요일 오전으로 변경하겠습니다. <@U02SEO> QA 일정 확인 부탁드려요.",
                    "reactions": [{"name": "white_check_mark", "count": 3}],
                    "_replies": [
                        {
                            "ts": ts(3, 9, 20),
                            "user": "U02SEO",
                            "text": "확인했습니다. QA 일정도 하루 미루겠습니다.",
                        },
                        {
                            "ts": ts(3, 9, 31),
                            "user": "U01MIN",
                            "text": "감사합니다! 변경된 일정은 <https://example.com/release|릴리스 문서>에 반영했어요.",
                        },
                    ],
                },
                {
                    "ts": ts(18, 14, 5),
                    "user": "U03KIM",
                    "text": "8월 고객 인터뷰 결과 화면과 메모를 공유합니다.",
                    "files": [
                        {
                            "id": "F01IMAGE",
                            "name": "interview-result.png",
                            "title": "고객 인터뷰 결과",
                            "mimetype": "image/png",
                            "size": 184320,
                            "url_private_download": "https://files.slack.com/demo/interview-result.png",
                        },
                        {
                            "id": "F02PDF",
                            "name": "interview-notes.pdf",
                            "title": "고객 인터뷰 메모",
                            "mimetype": "application/pdf",
                        },
                    ],
                    "_replies": [],
                },
            ],
        }
    ]
    return channels, {"U01MIN": "민수", "U02SEO": "서연", "U03KIM": "김PM"}

def environment_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ArchiveError(f"{name}은 true 또는 false여야 합니다.")

def run(args: argparse.Namespace) -> int:
    if args.mock and args.publish:
        raise ArchiveError("--mock과 --publish는 함께 사용할 수 없습니다.")
    window = month_window(args.month or os.getenv("ARCHIVE_MONTH"))
    workspace_url = os.getenv("SLACK_WORKSPACE_URL", "")
    slack: SlackClient | None = None

    if args.mock:
        channels, users = mock_archive(window)
        workspace_url = workspace_url or "https://demo-workspace.slack.com"
    else:
        slack_token = os.getenv("SLACK_BOT_TOKEN")
        if not slack_token:
            raise ArchiveError("SLACK_BOT_TOKEN 환경 변수가 필요합니다. 데모만 보려면 --mock을 사용하세요.")
        slack = SlackClient(slack_token)
        identity = slack.auth_test()
        if not workspace_url and identity.get("url"):
            workspace_url = identity["url"]
        users = slack.users()
        auto_join_public = environment_flag("AUTO_JOIN_PUBLIC_CHANNELS")
        channels = []
        for channel in slack.member_channels(auto_join_public=auto_join_public):
            channel = dict(channel)
            channel["messages"] = slack.channel_messages(channel["id"], window)
            channels.append(channel)

    if not args.publish:
        previews = [
            markdown_preview(channel, channel["messages"], users, window, workspace_url)
            for channel in channels
        ]
        print("\n---\n\n".join(previews))
        print(f"\n[미리보기] {len(channels)}개 채널. Notion에는 쓰지 않았습니다.", file=sys.stderr)
        return 0

    notion_token = os.getenv("NOTION_TOKEN")
    notion_data_source = os.getenv("NOTION_DATA_SOURCE_ID")
    if not notion_token or not notion_data_source:
        raise ArchiveError("게시하려면 NOTION_TOKEN과 NOTION_DATA_SOURCE_ID가 필요합니다.")

    notion = NotionClient(notion_token, notion_data_source)
    properties = notion.validate_schema()
    if channels:
        added_labels = notion.ensure_select_options(
            properties,
            {channel_label(channel) for channel in channels},
            window.label,
        )
        for property_name, values in added_labels.items():
            print(f"labels   {property_name}: {', '.join(values)}")
    try:
        configured_max = (
            args.max_image_mb
            if args.max_image_mb is not None
            else os.getenv("MAX_IMAGE_MB") or DEFAULT_MAX_IMAGE_MB
        )
        max_image_mb = int(configured_max)
    except ValueError as error:
        raise ArchiveError("MAX_IMAGE_MB는 양의 정수여야 합니다.") from error
    if max_image_mb <= 0 or max_image_mb > 5120:
        raise ArchiveError("MAX_IMAGE_MB는 1부터 5120 사이여야 합니다.")
    max_image_bytes = max_image_mb * 1024 * 1024
    created = skipped = 0
    for channel in channels:
        label = channel_label(channel)
        title = entry_title(window, label)
        existing = notion.exact_entry(label, window.label)
        if existing:
            skipped += 1
            print(f"skipped  {title}  {existing.get('url') or existing['id']}")
            continue
        assert slack is not None
        uploaded, failed = upload_message_images(
            channel["messages"], slack, notion, max_image_bytes
        )
        if uploaded or failed:
            print(f"images   {title}  업로드 {uploaded}개, 실패 {failed}개")
        blocks = archive_blocks(
            channel, channel["messages"], users, window, workspace_url
        )
        result = notion.create_archive_entry(title, label, window.label, blocks)
        created += 1
        print(f"created  {title}  {result.get('url') or result['id']}")
    print(f"완료: 생성 {created}개, 기존 페이지 건너뜀 {skipped}개")
    return 0

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--month", help="아카이빙할 KST 기준 월(YYYY-MM). 기본값은 지난달")
    parser.add_argument("--mock", action="store_true", help="Slack 없이 샘플 데이터로 미리보기")
    parser.add_argument("--publish", action="store_true", help="Notion에 실제 페이지 생성")
    parser.add_argument(
        "--max-image-mb",
        type=int,
        help=f"이미지 하나의 최대 다운로드 크기(MB). 기본값 {DEFAULT_MAX_IMAGE_MB}",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    try:
        raise SystemExit(run(parse_args()))
    except ArchiveError as error:
        print(f"오류: {error}", file=sys.stderr)
        raise SystemExit(2)
