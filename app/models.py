from __future__ import annotations

import contextlib
import os
import re

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.constants import KST


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


@dataclass(frozen=True)
class DownloadedFile:
    path: str
    filename: str
    content_type: str
    size: int

    def cleanup(self) -> None:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(self.path)


def month_window(
        value: str | None,
        now: datetime | None = None,
) -> MonthWindow:
    """Return an exact KST calendar-month window, defaulting to last month."""

    current = now.astimezone(KST) if now else datetime.now(KST)

    if value:
        if not re.fullmatch(r"\d{4}-\d{2}", value):
            raise ArchiveError(
                "월은 YYYY-MM 형식이어야 합니다. 예: 2026-08"
            )

        year, month = map(int, value.split("-"))

        if month < 1 or month > 12:
            raise ArchiveError("월은 01부터 12 사이여야 합니다.")

    else:
        first_this_month = current.replace(
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )

        previous_day = first_this_month - timedelta(days=1)

        year = previous_day.year
        month = previous_day.month

    start = datetime(
        year,
        month,
        1,
        tzinfo=KST,
    )

    if month == 12:
        end = datetime(
            year + 1,
            1,
            1,
            tzinfo=KST,
            )
    else:
        end = datetime(
            year,
            month + 1,
            1,
            tzinfo=KST,
            )

    return MonthWindow(
        label=f"{year:04d}-{month:02d}",
        start=start,
        end=end,
    )