from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_TIMEZONE = "Asia/Kuala_Lumpur"


def get_zoneinfo(tz_name: str | None = None) -> ZoneInfo | None:
    try:
        return ZoneInfo(tz_name or DEFAULT_TIMEZONE)
    except ZoneInfoNotFoundError:
        return None


def now_local(tz_name: str | None = None) -> datetime:
    tz = get_zoneinfo(tz_name)
    if tz is None:
        return datetime.now().astimezone()
    return datetime.now(tz)


def today_local(tz_name: str | None = None) -> date:
    return now_local(tz_name).date()


def iso_now(tz_name: str | None = None) -> str:
    return now_local(tz_name).isoformat(timespec="seconds")


def parse_date(value: str | date | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])

