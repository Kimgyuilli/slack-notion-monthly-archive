#!/usr/bin/env python3
"""Archive one calendar month of Slack messages into a Notion database."""

from __future__ import annotations

import argparse
import sys
import time

from app.constants import (
    DEFAULT_MAX_IMAGE_MB,
    KST,
    MAX_NOTION_BLOCKS_PER_REQUEST,
    MAX_NOTION_TEXT_LENGTH,
    NOTION_API,
    NOTION_CHANNEL_PROPERTY,
    NOTION_PART_BYTES,
    NOTION_PERIOD_PROPERTY,
    NOTION_SINGLE_PART_LIMIT_BYTES,
    NOTION_STATUS_COMPLETE,
    NOTION_STATUS_FAILED,
    NOTION_STATUS_IN_PROGRESS,
    NOTION_STATUS_PROPERTY,
    NOTION_TITLE_PROPERTY,
    NOTION_VERSION,
    SLACK_API,
)
from app.http_client import JsonHttpClient
from app.models import (
    ArchiveError,
    DownloadedFile,
    MonthWindow,
    month_window,
)
from app.notion_client import (
    NotionClient,
    chunked,
)
from app.renderer import (
    archive_blocks,
    image_blocks,
    kst_datetime,
    markdown_preview,
    message_author,
    message_details,
    message_permalink,
    paragraph_block,
    rich_text,
    slack_text,
)
from app.service import (
    all_messages,
    environment_flag,
    is_image_file,
    mock_archive,
    run,
    upload_message_images,
)
from app.slack_client import (
    SlackClient,
    safe_content_type,
    safe_filename,
)


def parse_args(
        argv: list[str] | None = None,
) -> argparse.Namespace:

    parser = argparse.ArgumentParser(
        description=__doc__,
    )

    parser.add_argument(
        "--month",
        help=(
            "아카이빙할 KST 기준 월"
            "(YYYY-MM). 기본값은 지난달"
        ),
    )

    parser.add_argument(
        "--mock",
        action="store_true",
        help=(
            "Slack 없이 샘플 데이터로 "
            "미리보기"
        ),
    )

    parser.add_argument(
        "--publish",
        action="store_true",
        help=(
            "Notion에 실제 페이지 생성"
        ),
    )

    parser.add_argument(
        "--max-image-mb",
        type=int,
        help=(
            "이미지 하나의 최대 다운로드 "
            f"크기(MB). 기본값 "
            f"{DEFAULT_MAX_IMAGE_MB}"
        ),
    )

    return parser.parse_args(argv)


if __name__ == "__main__":
    try:
        raise SystemExit(
            run(
                parse_args()
            )
        )

    except ArchiveError as error:
        print(
            f"오류: {error}",
            file=sys.stderr,
        )

        raise SystemExit(2)