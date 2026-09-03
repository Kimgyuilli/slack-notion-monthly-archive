from __future__ import annotations

import json
import re
import time
import uuid

from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.models import ArchiveError


class JsonHttpClient:
    def __init__(
            self,
            retries: int = 4,
            timeout: int = 30,
    ):
        self.retries = retries
        self.timeout = timeout

    def request(
            self,
            method: str,
            url: str,
            *,
            headers: dict[str, str] | None = None,
            params: dict[str, Any] | None = None,
            body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:

        if params:
            encoded_params = {
                key: value
                for key, value in params.items()
                if value is not None
            }

            url = f"{url}?{urlencode(encoded_params)}"

        payload = (
            json.dumps(body).encode("utf-8")
            if body is not None
            else None
        )

        request_headers = {
            "Accept": "application/json",
            **(headers or {}),
        }

        if payload is not None:
            request_headers["Content-Type"] = "application/json"

        for attempt in range(self.retries + 1):
            request = Request(
                url,
                data=payload,
                headers=request_headers,
                method=method,
            )

            try:
                with urlopen(
                        request,
                        timeout=self.timeout,
                ) as response:
                    raw = response.read().decode("utf-8")
                    return json.loads(raw) if raw else {}

            except HTTPError as error:
                retryable = (
                        error.code == 429
                        or 500 <= error.code < 600
                )

                if retryable and attempt < self.retries:
                    retry_after = error.headers.get("Retry-After")

                    wait = (
                        float(retry_after)
                        if retry_after
                        else min(2**attempt, 16)
                    )

                    time.sleep(wait)
                    continue

                detail = error.read().decode(
                    "utf-8",
                    errors="replace",
                )

                raise ArchiveError(
                    f"HTTP {error.code} 응답: {detail[:500]}"
                ) from error

            except URLError as error:
                if attempt < self.retries:
                    time.sleep(min(2**attempt, 16))
                    continue

                raise ArchiveError(
                    f"네트워크 요청 실패: {error.reason}"
                ) from error

        raise AssertionError("unreachable")

    def multipart(
            self,
            url: str,
            *,
            headers: dict[str, str],
            content: bytes,
            filename: str,
            content_type: str,
            part_number: int | None = None,
    ) -> dict[str, Any]:

        boundary = f"----slack-notion-{uuid.uuid4().hex}"
        body = bytearray()

        if part_number is not None:
            body.extend(f"--{boundary}\r\n".encode())
            body.extend(
                b'Content-Disposition: form-data; '
                b'name="part_number"\r\n\r\n'
            )
            body.extend(f"{part_number}\r\n".encode())

        ascii_filename = (
                re.sub(
                    r"[^A-Za-z0-9._-]",
                    "_",
                    filename,
                )
                or "upload.bin"
        )

        body.extend(f"--{boundary}\r\n".encode())

        body.extend(
            (
                'Content-Disposition: form-data; '
                f'name="file"; filename="{ascii_filename}"\r\n'
            ).encode()
        )

        body.extend(
            f"Content-Type: {content_type}\r\n\r\n".encode()
        )

        body.extend(content)
        body.extend(f"\r\n--{boundary}--\r\n".encode())

        request_headers = {
            "Accept": "application/json",
            "Content-Type": (
                f"multipart/form-data; boundary={boundary}"
            ),
            **headers,
        }

        for attempt in range(self.retries + 1):
            request = Request(
                url,
                data=bytes(body),
                headers=request_headers,
                method="POST",
            )

            try:
                with urlopen(
                        request,
                        timeout=max(self.timeout, 120),
                ) as response:
                    raw = response.read().decode("utf-8")
                    return json.loads(raw) if raw else {}

            except HTTPError as error:
                retryable = (
                        error.code == 429
                        or 500 <= error.code < 600
                )

                if retryable and attempt < self.retries:
                    retry_after = error.headers.get("Retry-After")

                    wait = (
                        float(retry_after)
                        if retry_after
                        else min(2**attempt, 16)
                    )

                    time.sleep(wait)
                    continue

                detail = error.read().decode(
                    "utf-8",
                    errors="replace",
                )

                raise ArchiveError(
                    f"파일 업로드 HTTP {error.code} 응답: "
                    f"{detail[:500]}"
                ) from error

            except URLError as error:
                if attempt < self.retries:
                    time.sleep(min(2**attempt, 16))
                    continue

                raise ArchiveError(
                    "파일 업로드 네트워크 실패: "
                    f"{error.reason}"
                ) from error

        raise AssertionError("unreachable")