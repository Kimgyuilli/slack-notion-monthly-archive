from __future__ import annotations

import contextlib
import math
import time

from typing import Any

from app.http_client import JsonHttpClient
from app.models import ArchiveError, DownloadedFile


NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2026-03-11"

MAX_NOTION_BLOCKS_PER_REQUEST = 100

NOTION_SINGLE_PART_LIMIT_BYTES = 20 * 1024 * 1024
NOTION_PART_BYTES = 10 * 1024 * 1024

NOTION_TITLE_PROPERTY = "이름"
NOTION_CHANNEL_PROPERTY = "채널"
NOTION_PERIOD_PROPERTY = "기간"
NOTION_STATUS_PROPERTY = "상태"

NOTION_STATUS_IN_PROGRESS = "진행 중"
NOTION_STATUS_COMPLETE = "완료"
NOTION_STATUS_FAILED = "실패"


def chunked(
        items: list[Any],
        size: int,
):
    for index in range(0, len(items), size):
        yield items[index:index + size]


class NotionClient:
    def __init__(
            self,
            token: str,
            data_source_id: str,
            http: JsonHttpClient | None = None,
    ):
        self.data_source_id = data_source_id
        self.http = http or JsonHttpClient()

        self.headers = {
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
        }

    def validate_schema(self) -> dict[str, Any]:
        result = self.http.request(
            "GET",
            f"{NOTION_API}/data_sources/{self.data_source_id}",
            headers=self.headers,
        )

        properties = result.get("properties", {})

        expected = {
            NOTION_TITLE_PROPERTY: "title",
            NOTION_CHANNEL_PROPERTY: "select",
            NOTION_PERIOD_PROPERTY: "select",
            NOTION_STATUS_PROPERTY: "status",
        }

        errors = [
            f"{name}({property_type})"
            for name, property_type in expected.items()
            if properties.get(name, {}).get("type") != property_type
        ]

        if errors:
            raise ArchiveError(
                "Notion DB에 다음 속성이 필요합니다: "
                + ", ".join(errors)
            )

        status_options = {
            option.get("name")
            for option in (
                properties[NOTION_STATUS_PROPERTY]
                .get("status", {})
                .get("options", [])
            )
        }

        required_statuses = {
            NOTION_STATUS_IN_PROGRESS,
            NOTION_STATUS_COMPLETE,
            NOTION_STATUS_FAILED,
        }

        missing_statuses = sorted(
            required_statuses - status_options
        )

        if missing_statuses:
            raise ArchiveError(
                "Notion DB의 "
                f"{NOTION_STATUS_PROPERTY} "
                "속성에 다음 옵션을 추가하세요: "
                + ", ".join(missing_statuses)
            )

        return properties

    def ensure_select_options(
            self,
            properties: dict[str, Any],
            channel_labels: set[str],
            period: str,
    ) -> dict[str, list[str]]:

        requested = {
            NOTION_CHANNEL_PROPERTY: channel_labels,
            NOTION_PERIOD_PROPERTY: {period},
        }

        updates: dict[str, Any] = {}
        added: dict[str, list[str]] = {}

        for property_name, values in requested.items():
            existing = (
                properties[property_name]
                .get("select", {})
                .get("options", [])
            )

            existing_names = {
                option.get("name")
                for option in existing
                if option.get("name")
            }

            missing = sorted(values - existing_names)

            if not missing:
                continue

            preserved = [
                (
                    {"id": option["id"]}
                    if option.get("id")
                    else {"name": option["name"]}
                )
                for option in existing
                if option.get("id") or option.get("name")
            ]

            updates[property_name] = {
                "select": {
                    "options": (
                            preserved
                            + [
                                {"name": value}
                                for value in missing
                            ]
                    )
                }
            }

            added[property_name] = missing

        if updates:
            self.http.request(
                "PATCH",
                f"{NOTION_API}/data_sources/{self.data_source_id}",
                headers=self.headers,
                body={"properties": updates},
            )

        return added

    def exact_entry(
            self,
            channel_label: str,
            period: str,
    ) -> dict[str, Any] | None:

        result = self.http.request(
            "POST",
            f"{NOTION_API}/data_sources/{self.data_source_id}/query",
            headers=self.headers,
            body={
                "filter": {
                    "and": [
                        {
                            "property": NOTION_CHANNEL_PROPERTY,
                            "select": {
                                "equals": channel_label
                            },
                        },
                        {
                            "property": NOTION_PERIOD_PROPERTY,
                            "select": {
                                "equals": period
                            },
                        },
                    ]
                },
                "page_size": 1,
            },
        )

        return next(
            (
                page
                for page in result.get("results", [])
                if not page.get("in_trash", False)
            ),
            None,
        )

    def upload_file(
            self,
            downloaded: DownloadedFile,
    ) -> str:

        multi_part = (
                downloaded.size
                > NOTION_SINGLE_PART_LIMIT_BYTES
        )

        number_of_parts = max(
            1,
            math.ceil(
                downloaded.size / NOTION_PART_BYTES
            ),
        )

        create_body: dict[str, Any] = {
            "mode": (
                "multi_part"
                if multi_part
                else "single_part"
            ),
            "filename": downloaded.filename,
            "content_type": downloaded.content_type,
        }

        if multi_part:
            create_body["number_of_parts"] = number_of_parts

        upload = self.http.request(
            "POST",
            f"{NOTION_API}/file_uploads",
            headers=self.headers,
            body=create_body,
        )

        upload_id = upload.get("id")

        if not upload_id:
            raise ArchiveError(
                "Notion이 파일 업로드 ID를 "
                "반환하지 않았습니다."
            )

        upload_url = (
                upload.get("upload_url")
                or f"{NOTION_API}/file_uploads/{upload_id}/send"
        )

        with open(downloaded.path, "rb") as source:
            for part_number in range(
                    1,
                    number_of_parts + 1,
            ):
                content = source.read(
                    NOTION_PART_BYTES
                    if multi_part
                    else downloaded.size + 1
                )

                if not content and downloaded.size:
                    raise ArchiveError(
                        "Notion 업로드 전 이미지 "
                        "파일을 끝까지 읽지 못했습니다."
                    )

                part_result = self.http.multipart(
                    upload_url,
                    headers=self.headers,
                    content=content,
                    filename=downloaded.filename,
                    content_type=downloaded.content_type,
                    part_number=(
                        part_number
                        if multi_part
                        else None
                    ),
                )

                if not multi_part:
                    upload = part_result

                time.sleep(0.35)

        if multi_part:
            upload = self.http.request(
                "POST",
                (
                        upload.get("complete_url")
                        or (
                            f"{NOTION_API}/file_uploads/"
                            f"{upload_id}/complete"
                        )
                ),
                headers=self.headers,
            )

        if upload.get("status") != "uploaded":
            raise ArchiveError(
                "Notion 이미지 업로드 상태가 "
                "예상과 다릅니다: "
                f"{upload.get('status')}"
            )

        return upload_id

    def update_status(
            self,
            page_id: str,
            status: str,
    ) -> None:

        self.http.request(
            "PATCH",
            f"{NOTION_API}/pages/{page_id}",
            headers=self.headers,
            body={
                "properties": {
                    NOTION_STATUS_PROPERTY: {
                        "status": {
                            "name": status
                        }
                    }
                }
            },
        )

    def create_archive_entry(
            self,
            title: str,
            channel_label: str,
            period: str,
            blocks: list[dict[str, Any]],
    ) -> dict[str, Any]:

        existing = self.exact_entry(
            channel_label,
            period,
        )

        if existing:
            return {
                "status": "skipped",
                "url": existing.get("url"),
                "id": existing["id"],
            }

        page = self.http.request(
            "POST",
            f"{NOTION_API}/pages",
            headers=self.headers,
            body={
                "parent": {
                    "type": "data_source_id",
                    "data_source_id": self.data_source_id,
                },
                "properties": {
                    NOTION_TITLE_PROPERTY: {
                        "title": [
                            {
                                "type": "text",
                                "text": {
                                    "content": title
                                },
                            }
                        ]
                    },
                    NOTION_CHANNEL_PROPERTY: {
                        "select": {
                            "name": channel_label
                        }
                    },
                    NOTION_PERIOD_PROPERTY: {
                        "select": {
                            "name": period
                        }
                    },
                    NOTION_STATUS_PROPERTY: {
                        "status": {
                            "name": NOTION_STATUS_IN_PROGRESS
                        }
                    },
                },
            },
        )

        page_id = page.get("id")

        if not page_id:
            raise ArchiveError(
                "Notion이 생성된 DB 페이지 "
                "ID를 반환하지 않았습니다."
            )

        try:
            for batch in chunked(
                    blocks,
                    MAX_NOTION_BLOCKS_PER_REQUEST,
            ):
                self.http.request(
                    "PATCH",
                    (
                        f"{NOTION_API}/blocks/"
                        f"{page_id}/children"
                    ),
                    headers=self.headers,
                    body={"children": batch},
                )

                time.sleep(0.35)

            self.update_status(
                page_id,
                NOTION_STATUS_COMPLETE,
            )

        except Exception:
            with contextlib.suppress(Exception):
                self.update_status(
                    page_id,
                    NOTION_STATUS_FAILED,
                )

            raise

        return {
            "status": "created",
            "url": page.get("url"),
            "id": page_id,
        }