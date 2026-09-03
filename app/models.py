"""Shared domain types, the KST clock, and the Notion schema both the writer
and the preview renderer describe.
"""

from __future__ import annotations

import contextlib
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo


KST = ZoneInfo("Asia/Seoul")

# The archive database's schema. Kept here, not in app.notion_client, because
# app.renderer prints the same status in its preview: one definition means the
# preview can never claim a status the writer does not actually set.
NOTION_TITLE_PROPERTY = "이름"
NOTION_CHANNEL_PROPERTY = "채널"
NOTION_PERIOD_PROPERTY = "기간"
NOTION_STATUS_PROPERTY = "상태"
NOTION_STATUS_IN_PROGRESS = "진행 중"
NOTION_STATUS_COMPLETE = "완료"
NOTION_STATUS_FAILED = "실패"


class ArchiveError(RuntimeError):
    """An expected integration error with a user-actionable message."""

@dataclass(frozen=True)
class MonthWindow:
    label: str
    start: datetime
    end: datetime

    @property
    def oldest(self) -> str:
        return f"{self.start.timestamp():.6f}"

    @property
    def latest(self) -> str:
        return f"{self.end.timestamp():.6f}"

    def contains(self, timestamp: str) -> bool:
        return self.start.timestamp() <= float(timestamp) < self.end.timestamp()

@dataclass(frozen=True)
class DownloadedFile:
    path: str
    filename: str
    content_type: str
    size: int

    def cleanup(self) -> None:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(self.path)

def month_window(value: str | None, now: datetime | None = None) -> MonthWindow:
    """Return an exact KST calendar-month window, defaulting to last month."""
    current = now.astimezone(KST) if now else datetime.now(KST)
    if value:
        if not re.fullmatch(r"\d{4}-\d{2}", value):
            raise ArchiveError("월은 YYYY-MM 형식이어야 합니다. 예: 2026-08")
        year, month = map(int, value.split("-"))
        if month < 1 or month > 12:
            raise ArchiveError("월은 01부터 12 사이여야 합니다.")
    else:
        first_this_month = current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        previous_day = first_this_month - timedelta(days=1)
        year, month = previous_day.year, previous_day.month

    start = datetime(year, month, 1, tzinfo=KST)
    end = datetime(year + month // 12, month % 12 + 1, 1, tzinfo=KST)
    return MonthWindow(f"{year:04d}-{month:02d}", start, end)
