from __future__ import annotations

import calendar
import re

from datetime import datetime, timezone
from typing import Any

from app.models import KST, MonthWindow


MAX_NOTION_TEXT_LENGTH = 1900
NOTION_STATUS_COMPLETE = "완료"


def slack_text(
        text: str,
        users: dict[str, str],
) -> str:

    text = re.sub(
        r"<@([A-Z0-9]+)>",
        lambda match: (
            f"@{users.get(match.group(1), match.group(1))}"
        ),
        text,
    )

    text = re.sub(
        r"<([^>|]+)\|([^>]+)>",
        lambda match: (
            f"{match.group(2)} ({match.group(1)})"
        ),
        text,
    )

    text = re.sub(
        r"<((?:https?|mailto):[^>]+)>",
        r"\1",
        text,
    )

    return text


def message_permalink(
        workspace_url: str,
        channel_id: str,
        timestamp: str,
) -> str | None:

    if not workspace_url:
        return None

    return (
        f"{workspace_url.rstrip('/')}"
        f"/archives/{channel_id}/p"
        f"{timestamp.replace('.', '')}"
    )


def message_author(
        message: dict[str, Any],
        users: dict[str, str],
) -> str:

    user_id = message.get("user")

    if user_id:
        return users.get(
            user_id,
            user_id,
        )

    return (
            message.get("username")
            or message.get("bot_profile", {}).get("name")
            or "Slack Bot"
    )


def message_details(
        message: dict[str, Any],
) -> str:

    extras: list[str] = []

    reactions = message.get("reactions", [])

    if reactions:
        extras.append(
            " ".join(
                (
                    f":{item['name']}:"
                    f"×{item.get('count', 0)}"
                )
                for item in reactions
            )
        )

    files = message.get("files", [])

    if files:
        extras.append(
            "파일: "
            + ", ".join(
                item.get("title")
                or item.get("name")
                or "첨부파일"
                for item in files
            )
        )

    return " · ".join(extras)


def kst_datetime(
        timestamp: str,
) -> datetime:

    return datetime.fromtimestamp(
        float(timestamp),
        tz=timezone.utc,
    ).astimezone(KST)


def rich_text(
        content: str,
        *,
        bold: bool = False,
        link: str | None = None,
) -> list[dict[str, Any]]:

    if not content:
        return []

    pieces: list[dict[str, Any]] = []

    for index in range(
            0,
            len(content),
            MAX_NOTION_TEXT_LENGTH,
    ):
        text: dict[str, Any] = {
            "content": content[
                index:index + MAX_NOTION_TEXT_LENGTH
            ]
        }

        if link:
            text["link"] = {"url": link}

        pieces.append(
            {
                "type": "text",
                "text": text,
                "annotations": {
                    "bold": bold
                },
            }
        )

    return pieces


def paragraph_block(
        message: dict[str, Any],
        users: dict[str, str],
        channel_id: str,
        workspace_url: str,
        *,
        reply: bool = False,
) -> dict[str, Any]:

    sent_at = kst_datetime(message["ts"])

    prefix = "↳ " if reply else ""

    display_time = (
        f"{sent_at:%m-%d %H:%M}"
        if reply
        else f"{sent_at:%H:%M}"
    )

    header = (
        f"{prefix}"
        f"{display_time} "
        f"{message_author(message, users)}  "
    )

    body = slack_text(
        message.get("text") or "(본문 없음)",
        users,
        )

    details = message_details(message)

    if details:
        body += f"\n{details}"

    content = (
            rich_text(
                header,
                bold=True,
            )
            + rich_text(body)
    )

    permalink = message_permalink(
        workspace_url,
        channel_id,
        message["ts"],
    )

    if permalink:
        content += rich_text(
            "  원문",
            link=permalink,
        )

    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {
            "rich_text": content
        },
    }


def image_blocks(
        message: dict[str, Any],
) -> list[dict[str, Any]]:

    blocks: list[dict[str, Any]] = []

    for file in message.get("files", []):
        upload_id = file.get("_notion_upload_id")

        if not upload_id:
            continue

        filename = (
                file.get("title")
                or file.get("name")
                or "Slack 이미지"
        )

        blocks.append(
            {
                "object": "block",
                "type": "image",
                "image": {
                    "type": "file_upload",
                    "file_upload": {
                        "id": upload_id
                    },
                    "caption": rich_text(filename),
                },
            }
        )

    return blocks


def archive_blocks(
        channel: dict[str, Any],
        messages: list[dict[str, Any]],
        users: dict[str, str],
        window: MonthWindow,
        workspace_url: str,
) -> list[dict[str, Any]]:

    message_count = sum(
        1 + len(message.get("_replies", []))
        for message in messages
    )

    last_day = calendar.monthrange(
        window.start.year,
        window.start.month,
    )[1]

    blocks: list[dict[str, Any]] = [
        {
            "object": "block",
            "type": "callout",
            "callout": {
                "icon": {
                    "type": "emoji",
                    "emoji": "📦",
                },
                "rich_text": rich_text(
                    f"기간: {window.label}-01 "
                    f"~ {window.label}-{last_day:02d}"
                    f" · 채널 ID: {channel['id']}"
                    f" · 메시지/답글: {message_count}개"
                ),
            },
        }
    ]

    current_date = None

    for message in messages:
        date = kst_datetime(
            message["ts"]
        ).date()

        if date != current_date:
            current_date = date

            blocks.append(
                {
                    "object": "block",
                    "type": "heading_2",
                    "heading_2": {
                        "rich_text": rich_text(
                            f"{date:%Y-%m-%d}"
                        )
                    },
                }
            )

        blocks.append(
            paragraph_block(
                message,
                users,
                channel["id"],
                workspace_url,
            )
        )

        blocks.extend(image_blocks(message))

        for reply_message in message.get(
                "_replies",
                [],
        ):
            blocks.append(
                paragraph_block(
                    reply_message,
                    users,
                    channel["id"],
                    workspace_url,
                    reply=True,
                )
            )

            blocks.extend(
                image_blocks(reply_message)
            )

    if not messages:
        blocks.append(
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": rich_text(
                        "이 기간에 메시지가 없습니다."
                    )
                },
            }
        )

    return blocks


def markdown_preview(
        channel: dict[str, Any],
        messages: list[dict[str, Any]],
        users: dict[str, str],
        window: MonthWindow,
        workspace_url: str,
) -> str:

    channel_label = (
        f"#{channel.get('name', channel['id'])}"
    )

    title = (
        f"Slack · "
        f"{window.label} · "
        f"{channel_label}"
    )

    lines = [
        (
            f"[Notion DB] 이름={title}"
            f" · 채널={channel_label}"
            f" · 기간={window.label}"
            f" · 상태={NOTION_STATUS_COMPLETE}"
        ),
        "",
        f"# {title}",
        "",
    ]

    current_date = None

    for message in messages:
        sent_at = kst_datetime(
            message["ts"]
        )

        if sent_at.date() != current_date:
            current_date = sent_at.date()

            lines.extend(
                [
                    f"## {current_date:%Y-%m-%d}",
                    "",
                ]
            )

        items = [
            (message, ""),
            *[
                (reply, "  ↳ ")
                for reply in message.get(
                    "_replies",
                    [],
                )
            ],
        ]

        for item, prefix in items:
            item_time = kst_datetime(
                item["ts"]
            )

            display_time = (
                f"{item_time:%m-%d %H:%M}"
                if prefix
                else f"{item_time:%H:%M}"
            )

            text = slack_text(
                item.get("text") or "(본문 없음)",
                users,
                ).replace("\n", " ")

            line = (
                f"- {prefix}"
                f"**{display_time} "
                f"{message_author(item, users)}**"
                f" — {text}"
            )

            details = message_details(item)

            if details:
                line += f" · {details}"

            permalink = message_permalink(
                workspace_url,
                channel["id"],
                item["ts"],
            )

            if permalink:
                line += (
                    f" · [원문]"
                    f"({permalink})"
                )

            lines.append(line)

        lines.append("")

    if not messages:
        lines.append(
            "이 기간에 메시지가 없습니다."
        )

    return "\n".join(lines).rstrip() + "\n"