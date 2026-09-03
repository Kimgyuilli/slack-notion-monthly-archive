from __future__ import annotations

import contextlib
import os
import tempfile
import time

from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.constants import SLACK_API
from app.http_client import JsonHttpClient
from app.models import (
    ArchiveError,
    DownloadedFile,
    MonthWindow,
)


def safe_filename(value: str) -> str:
    name = (
            os.path.basename(
                value.replace("\\", "/")
            ).strip()
            or "slack-image"
    )

    encoded = name.encode("utf-8")

    if len(encoded) <= 800:
        return name

    root, extension = os.path.splitext(name)

    allowed = max(
        1,
        780 - len(extension.encode("utf-8")),
        )

    shortened = (
        root.encode("utf-8")[:allowed]
        .decode(
            "utf-8",
            errors="ignore",
        )
    )

    return shortened + extension


def safe_content_type(value: Any) -> str:
    import re

    content_type = str(
        value or "application/octet-stream"
    ).lower()

    if re.fullmatch(
            r"[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+",
            content_type,
    ):
        return content_type

    return "application/octet-stream"


class SlackClient:
    def __init__(
            self,
            token: str,
            http: JsonHttpClient | None = None,
    ):
        self.token = token
        self.http = http or JsonHttpClient()

    def call(
            self,
            method: str,
            *,
            http_method: str = "GET",
            **params: Any,
    ) -> dict[str, Any]:

        result = self.http.request(
            http_method,
            f"{SLACK_API}/{method}",
            headers={
                "Authorization": f"Bearer {self.token}"
            },
            params=params if http_method == "GET" else None,
            body=params if http_method != "GET" else None,
        )

        if not result.get("ok"):
            raise ArchiveError(
                f"Slack API {method} 실패: "
                f"{result.get('error', 'unknown_error')}"
            )

        return result

    def auth_test(self) -> dict[str, Any]:
        return self.call("auth.test")

    def download_file(
            self,
            file: dict[str, Any],
            max_bytes: int,
    ) -> DownloadedFile:

        url = (
                file.get("url_private_download")
                or file.get("url_private")
        )

        if not url:
            raise ArchiveError(
                "Slack 파일에 다운로드 URL이 없습니다."
            )

        expected_size = int(
            file.get("size") or 0
        )

        if (
                expected_size
                and expected_size > max_bytes
        ):
            raise ArchiveError(
                "설정된 이미지 한도"
                f"({max_bytes // 1024 // 1024}MB)"
                "를 초과합니다."
            )

        filename = safe_filename(
            file.get("name")
            or file.get("title")
            or (
                f"slack-image-"
                f"{file.get('id', 'unknown')}"
            )
        )

        content_type = safe_content_type(
            file.get("mimetype")
        )

        for attempt in range(
                self.http.retries + 1
        ):
            temp = tempfile.NamedTemporaryFile(
                prefix="slack-image-",
                delete=False,
            )

            temp_path = temp.name

            try:
                request = Request(
                    url,
                    headers={
                        "Authorization": (
                            f"Bearer {self.token}"
                        )
                    },
                )

                with (
                    urlopen(
                        request,
                        timeout=120,
                    ) as response,
                    temp,
                ):
                    response_type = safe_content_type(
                        response.headers.get_content_type()
                    )

                    if response_type.startswith(
                            "image/"
                    ):
                        content_type = response_type

                    total = 0

                    while True:
                        chunk = response.read(
                            1024 * 1024
                        )

                        if not chunk:
                            break

                        total += len(chunk)

                        if total > max_bytes:
                            raise ArchiveError(
                                "다운로드 중 설정된 "
                                "이미지 한도"
                                f"({max_bytes // 1024 // 1024}MB)"
                                "를 초과했습니다."
                            )

                        temp.write(chunk)

                return DownloadedFile(
                    temp_path,
                    filename,
                    content_type,
                    total,
                )

            except (HTTPError, URLError) as error:

                with contextlib.suppress(Exception):
                    temp.close()

                with contextlib.suppress(
                        FileNotFoundError
                ):
                    os.unlink(temp_path)

                retryable = (
                        not isinstance(error, HTTPError)
                        or error.code == 429
                        or 500 <= error.code < 600
                )

                if (
                        retryable
                        and attempt < self.http.retries
                ):
                    retry_after = (
                        error.headers.get(
                            "Retry-After"
                        )
                        if isinstance(
                            error,
                            HTTPError,
                        )
                        else None
                    )

                    time.sleep(
                        float(retry_after)
                        if retry_after
                        else min(2**attempt, 16)
                    )

                    continue

                if isinstance(error, HTTPError):
                    raise ArchiveError(
                        "Slack 이미지 다운로드 실패: "
                        f"HTTP {error.code}"
                    ) from error

                raise ArchiveError(
                    "Slack 이미지 다운로드 실패: "
                    f"{error.reason}"
                ) from error

            except Exception:

                with contextlib.suppress(Exception):
                    temp.close()

                with contextlib.suppress(
                        FileNotFoundError
                ):
                    os.unlink(temp_path)

                raise

        raise AssertionError("unreachable")

    def users(self) -> dict[str, str]:
        cursor = ""
        names: dict[str, str] = {}

        while True:
            result = self.call(
                "users.list",
                limit=200,
                cursor=cursor,
            )

            for user in result.get(
                    "members",
                    [],
            ):
                profile = user.get(
                    "profile",
                    {},
                )

                names[user["id"]] = (
                        profile.get("display_name")
                        or profile.get("real_name")
                        or user.get("real_name")
                        or user.get("name")
                        or user["id"]
                )

            cursor = (
                result.get(
                    "response_metadata",
                    {},
                )
                .get(
                    "next_cursor",
                    "",
                )
            )

            if not cursor:
                return names

    def member_channels(
            self,
            *,
            auto_join_public: bool = False,
    ) -> list[dict[str, Any]]:

        cursor = ""
        channels: list[dict[str, Any]] = []

        while True:
            result = self.call(
                "conversations.list",
                types=(
                    "public_channel,"
                    "private_channel"
                ),
                exclude_archived="true",
                limit=200,
                cursor=cursor,
            )

            for channel in result.get(
                    "channels",
                    [],
            ):
                if not channel.get(
                        "is_member"
                ):
                    if (
                            not auto_join_public
                            or channel.get(
                        "is_private"
                    )
                    ):
                        continue

                    joined = self.call(
                        "conversations.join",
                        http_method="POST",
                        channel=channel["id"],
                    )

                    channel = (
                            joined.get("channel")
                            or {
                                **channel,
                                "is_member": True,
                            }
                    )

                channels.append(channel)

            cursor = (
                result.get(
                    "response_metadata",
                    {},
                )
                .get(
                    "next_cursor",
                    "",
                )
            )

            if not cursor:
                return sorted(
                    channels,
                    key=lambda item: item.get(
                        "name",
                        item["id"],
                    ),
                )

    def channel_messages(
            self,
            channel_id: str,
            window: MonthWindow,
    ) -> list[dict[str, Any]]:

        cursor = ""
        roots: list[dict[str, Any]] = []

        while True:
            result = self.call(
                "conversations.history",
                channel=channel_id,
                oldest=window.oldest,
                latest=window.latest,
                inclusive="true",
                limit=200,
                cursor=cursor,
            )

            roots.extend(
                message
                for message in result.get(
                    "messages",
                    [],
                )
                if (
                        window.start.timestamp()
                        <= float(message["ts"])
                        < window.end.timestamp()
                )
            )

            cursor = (
                result.get(
                    "response_metadata",
                    {},
                )
                .get(
                    "next_cursor",
                    "",
                )
            )

            if not cursor:
                break

        output: list[dict[str, Any]] = []

        for root in sorted(
                roots,
                key=lambda item: float(
                    item["ts"]
                ),
        ):
            root = dict(root)
            root["_replies"] = []

            if root.get(
                    "reply_count",
                    0,
            ):
                root["_replies"] = (
                    self.thread_replies(
                        channel_id,
                        root["ts"],
                        window,
                    )
                )

            output.append(root)

        return output

    def thread_replies(
            self,
            channel_id: str,
            root_ts: str,
            window: MonthWindow,
    ) -> list[dict[str, Any]]:

        cursor = ""
        replies: list[dict[str, Any]] = []

        while True:
            result = self.call(
                "conversations.replies",
                channel=channel_id,
                ts=root_ts,
                oldest=window.oldest,
                latest=window.latest,
                inclusive="true",
                limit=200,
                cursor=cursor,
            )

            for message in result.get(
                    "messages",
                    [],
            ):
                timestamp = float(
                    message["ts"]
                )

                if (
                        message["ts"] != root_ts
                        and window.start.timestamp()
                        <= timestamp
                        < window.end.timestamp()
                ):
                    replies.append(message)

            cursor = (
                result.get(
                    "response_metadata",
                    {},
                )
                .get(
                    "next_cursor",
                    "",
                )
            )

            if not cursor:
                return sorted(
                    replies,
                    key=lambda item: float(
                        item["ts"]
                    ),
                )