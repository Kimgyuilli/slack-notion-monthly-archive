"""Dependency-free HTTP with the retry policy every API call shares."""

from __future__ import annotations

import contextlib
import json
import re
import time
import uuid
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.models import ArchiveError


def retry_delay(retry_after: str | None, attempt: int) -> float:
    """Honour a numeric Retry-After header, else back off exponentially to 16s.

    The header is clamped to the same ceiling: a server asking for an hour (or
    for a negative wait, which time.sleep rejects outright) must not stall or
    crash a run that is already bounded by the workflow timeout.
    """
    if retry_after:
        with contextlib.suppress(ValueError):
            return min(max(0.0, float(retry_after)), 16)
    return min(2**attempt, 16)

class JsonHttpClient:
    def __init__(self, retries: int = 4, timeout: int = 30):
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
            url = f"{url}?{urlencode({k: v for k, v in params.items() if v is not None})}"
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        request_headers = {"Accept": "application/json", **(headers or {})}
        if payload is not None:
            request_headers["Content-Type"] = "application/json"

        return self._send(
            lambda: Request(url, data=payload, headers=request_headers, method=method),
            timeout=self.timeout,
        )

    def _send(
        self,
        build_request: Callable[[], Request],
        *,
        timeout: int,
        error_prefix: str = "",
    ) -> dict[str, Any]:
        """Send one request, retrying 429/5xx and network failures."""
        for attempt in range(self.retries + 1):
            try:
                with urlopen(build_request(), timeout=timeout) as response:
                    raw = response.read().decode("utf-8")
                    return json.loads(raw) if raw else {}
            except HTTPError as error:
                retryable = error.code == 429 or 500 <= error.code < 600
                if retryable and attempt < self.retries:
                    time.sleep(retry_delay(error.headers.get("Retry-After"), attempt))
                    continue
                detail = error.read().decode("utf-8", errors="replace")
                raise ArchiveError(
                    f"{error_prefix}HTTP {error.code} 응답: {detail[:500]}"
                ) from error
            except URLError as error:
                if attempt < self.retries:
                    time.sleep(retry_delay(None, attempt))
                    continue
                raise ArchiveError(f"{error_prefix}네트워크 요청 실패: {error.reason}") from error
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
        """POST one multipart file body, optionally as a numbered Notion part."""
        boundary = f"----slack-notion-{uuid.uuid4().hex}"
        body = bytearray()
        if part_number is not None:
            body.extend(f"--{boundary}\r\n".encode())
            body.extend(b'Content-Disposition: form-data; name="part_number"\r\n\r\n')
            body.extend(f"{part_number}\r\n".encode())
        ascii_filename = re.sub(r"[^A-Za-z0-9._-]", "_", filename) or "upload.bin"
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(
            f'Content-Disposition: form-data; name="file"; filename="{ascii_filename}"\r\n'.encode()
        )
        body.extend(f"Content-Type: {content_type}\r\n\r\n".encode())
        body.extend(content)
        body.extend(f"\r\n--{boundary}--\r\n".encode())
        request_headers = {
            "Accept": "application/json",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            **headers,
        }

        return self._send(
            lambda: Request(url, data=bytes(body), headers=request_headers, method="POST"),
            timeout=max(self.timeout, 120),
            error_prefix="파일 업로드 ",
        )
