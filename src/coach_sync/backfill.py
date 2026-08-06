from __future__ import annotations

import os
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

from .evidence import wellness_snapshot_is_usable
from .garmin_sync import _method_call, _safe_call
from .io import read_json, write_json
from .paths import activities_dir, snapshots_dir
from .time_utils import DEFAULT_TIMEZONE, iso_now, parse_date, today_local


def _login() -> tuple[Any | None, dict]:
    email = os.getenv("GARMIN_EMAIL")
    password = os.getenv("GARMIN_PASSWORD")
    tokenstore = os.getenv("GARMINTOKENS")
    default_tokenstore = Path.home() / ".garminconnect"
    if not tokenstore and default_tokenstore.exists():
        tokenstore = str(default_tokenstore)
    if not email or not password:
        return None, {"status": "failed", "reason": "GARMIN_EMAIL and GARMIN_PASSWORD are required."}
    try:
        from garminconnect import Garmin
    except Exception as exc:
        return None, {"status": "failed", "reason": f"garminconnect import failed: {exc}"}
    try:
        client = Garmin(email, password)
        if tokenstore:
            client.login(tokenstore=tokenstore)
            return client, {"status": "ok", "auth_method": "tokenstore"}
        client.login()
        return client, {"status": "ok", "auth_method": "credentials"}
    except Exception as exc:
        return None, {"status": "failed", "reason": f"Garmin login failed: {exc}"}


def _call(label: str, func: Callable[..., Any], *args: Any) -> dict:
    result = _safe_call(label, func, *args)
    if not result.get("ok"):
        return result
    return result


def _wellness_payload(client: Any, day: str) -> list[dict]:
    return [
        _method_call(client, "get_stats", day),
        _method_call(client, "get_user_summary", day),
        _method_call(client, "get_body_battery", day, day),
        _method_call(client, "get_body_battery_events", day),
        _method_call(client, "get_sleep_data", day),
        _method_call(client, "get_hrv_data", day),
        _method_call(client, "get_body_composition", day),
    ]


def _daterange(start: date, end: date) -> list[date]:
    days = []
    current = start
    while current <= end:
        days.append(current)
        current += timedelta(days=1)
    return days


def _existing_activity_ids(root: str | Path | None) -> set[str]:
    ids = set()
    for path in activities_dir(root).glob("garmin_*.json"):
        stem = path.stem.removeprefix("garmin_")
        if stem:
            ids.add(stem)
    return ids


def backfill_activities(
    client: Any,
    root: str | Path | None,
    page_size: int,
    delay_seconds: float,
    max_pages: int | None,
) -> dict:
    total = None
    count_result = _call("count_activities", client.count_activities)
    if count_result.get("ok"):
        total = count_result.get("data")
    existing = _existing_activity_ids(root)
    offset = 0
    pages = 0
    written = 0
    seen = 0
    failures = []
    while True:
        if max_pages is not None and pages >= max_pages:
            break
        result = _call("get_activities", client.get_activities, offset, page_size)
        if not result.get("ok"):
            failures.append({"offset": offset, "error": result.get("error")})
            break
        rows = result.get("data") or []
        if not isinstance(rows, list) or not rows:
            break
        for activity in rows:
            activity_id = str(activity.get("activityId") or activity.get("id") or "")
            if not activity_id:
                continue
            seen += 1
            if activity_id in existing:
                continue
            write_json(activities_dir(root) / f"garmin_{activity_id}.json", activity)
            existing.add(activity_id)
            written += 1
        pages += 1
        offset += page_size
        if total is not None and offset >= int(total):
            break
        if len(rows) < page_size:
            break
        if delay_seconds:
            time.sleep(delay_seconds)
    return {
        "total_reported": total,
        "pages": pages,
        "seen": seen,
        "written": written,
        "failures": failures,
    }


def backfill_wellness(
    client: Any,
    root: str | Path | None,
    start: date,
    end: date,
    max_days: int,
    delay_seconds: float,
    skip_existing: bool,
) -> dict:
    written = []
    skipped = []
    failures = []
    days = _daterange(start, end)
    for day in days[:max_days]:
        day_text = day.isoformat()
        path = snapshots_dir(root) / f"garmin_wellness_{day_text}.json"
        existing = read_json(path, {}) if path.exists() else {}
        if skip_existing and wellness_snapshot_is_usable(existing):
            skipped.append(day_text)
            continue
        payloads = _wellness_payload(client, day_text)
        snapshot = {
            "date": day_text,
            "fetched_at": iso_now(DEFAULT_TIMEZONE),
            "source": "garminconnect_backfill",
            "payloads": payloads,
        }
        if not wellness_snapshot_is_usable(snapshot):
            failures.append({"date": day_text, "payloads": payloads})
            continue
        write_json(
            path,
            snapshot,
        )
        written.append(day_text)
        if delay_seconds:
            time.sleep(delay_seconds)
    remaining = max(0, len(days) - max_days)
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "max_days": max_days,
        "written": written,
        "skipped": skipped,
        "failures": failures,
        "remaining_after_this_run": remaining,
    }


def historical_backfill(
    root: str | Path | None = None,
    start_date: str = "2021-07-01",
    end_date: str | None = None,
    activities: bool = True,
    wellness: bool = True,
    wellness_max_days: int = 120,
    page_size: int = 100,
    max_pages: int | None = None,
    delay_seconds: float = 0.4,
    skip_existing: bool = True,
) -> dict:
    client, login = _login()
    if client is None:
        status = {"generated_at": iso_now(DEFAULT_TIMEZONE), "login": login}
        write_json(snapshots_dir(root) / "historical_backfill_status.json", status)
        return status
    start = parse_date(start_date)
    end = parse_date(end_date) or today_local(DEFAULT_TIMEZONE)
    assert start is not None and end is not None
    result = {
        "generated_at": iso_now(DEFAULT_TIMEZONE),
        "login": login,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "delay_seconds": delay_seconds,
        "activities": None,
        "wellness": None,
    }
    if activities:
        result["activities"] = backfill_activities(client, root, page_size, delay_seconds, max_pages)
    if wellness:
        result["wellness"] = backfill_wellness(
            client,
            root,
            start,
            end,
            wellness_max_days,
            delay_seconds,
            skip_existing,
        )
    write_json(snapshots_dir(root) / "historical_backfill_status.json", result)
    return result
