from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from .evidence import as_number, dated_snapshot_files, find_value
from .io import read_json, write_json
from .paths import snapshots_dir
from .time_utils import DEFAULT_TIMEZONE, iso_now, parse_date, today_local


FACTOR_KEYS = (
    "sleepScore",
    "sleepHistoryFactor",
    "hrvFactor",
    "acuteLoadFactor",
    "recoveryTimeFactor",
    "stressHistoryFactor",
)


def _latest_snapshot(
    root: str | Path | None,
    target: date,
) -> tuple[date | None, dict | None]:
    files = [
        item
        for item in dated_snapshot_files(root, "garmin_training_readiness")
        if item[0] <= target
    ]
    if not files:
        return None, None
    snapshot_date, path = files[-1]
    payload = read_json(path, {})
    return snapshot_date, payload if isinstance(payload, dict) else None


def _latest_device_capability(
    root: str | Path | None,
    target: date,
) -> dict | None:
    files = [
        item
        for item in dated_snapshot_files(root, "garmin_device_capabilities")
        if item[0] <= target
    ]
    if not files:
        return None
    return read_json(files[-1][1], None)


def _candidate(data: Any, target: date) -> dict:
    if isinstance(data, dict):
        for key in ("trainingReadiness", "morningTrainingReadiness", "latest"):
            if key in data:
                return _candidate(data.get(key), target)
        raw_date = data.get("calendarDate") or data.get("date")
        if raw_date and parse_date(raw_date) != target:
            return {}
        return data
    if isinstance(data, list):
        rows = [row for row in data if isinstance(row, dict)]
        dated_rows = []
        for row in reversed(rows):
            raw_date = row.get("calendarDate") or row.get("date")
            if raw_date:
                dated_rows.append(row)
                if parse_date(raw_date) == target:
                    return row
        if dated_rows:
            return {}
        return rows[-1] if rows else {}
    return {}


def _first_present(row: dict, keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in row and row.get(key) is not None:
            return row.get(key)
    return None


def _recognized_readiness(row: dict) -> bool:
    direct_keys = (
        "score",
        "trainingReadinessScore",
        "readinessScore",
        "level",
        "readinessLevel",
        "scoreState",
        "feedbackShort",
        "feedback",
        "feedbackPhrase",
    )
    if any(key in row and row.get(key) is not None for key in direct_keys):
        return True
    return any(find_value(row, (key,)) is not None for key in FACTOR_KEYS)


def normalize_training_readiness_payload(
    snapshot: dict | None,
    snapshot_date: str | date | None = None,
) -> dict:
    target = parse_date(snapshot_date) or parse_date((snapshot or {}).get("date"))
    payloads = (snapshot or {}).get("payloads") or []
    endpoint_rows = []
    candidates: list[tuple[str, dict]] = []
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        label = str(payload.get("label") or "unknown")
        status = str(
            payload.get("status")
            or (
                "success"
                if payload.get("ok") is True and payload.get("data")
                else "success_empty"
                if payload.get("ok") is True
                else "failed"
            )
        )
        endpoint_row = {
            "label": label,
            "status": status,
            "attempted_at": payload.get("attempted_at"),
            "error": payload.get("error"),
        }
        if status == "success" and target is not None:
            row = _candidate(payload.get("data"), target)
            if row and _recognized_readiness(row):
                candidates.append((label, row))
                endpoint_row["content_status"] = "recognized"
            else:
                endpoint_row["content_status"] = "unrecognized_or_wrong_date"
        endpoint_rows.append(endpoint_row)

    preferred = next(
        (item for item in candidates if item[0] == "get_morning_training_readiness"),
        candidates[0] if candidates else (None, {}),
    )
    source_label, row = preferred
    score = as_number(
        _first_present(
            row,
            ("score", "trainingReadinessScore", "readinessScore"),
        )
    )
    level = _first_present(row, ("level", "readinessLevel", "scoreState"))
    feedback = _first_present(
        row,
        ("feedbackShort", "feedback", "feedbackPhrase"),
    )
    factors = {
        key: as_number(find_value(row, (key,)))
        for key in FACTOR_KEYS
        if find_value(row, (key,)) is not None
    }
    statuses = {item.get("status") for item in endpoint_rows}
    if row:
        status = "available"
    elif "failed" in statuses:
        status = "failed"
    elif "unsupported" in statuses:
        status = "unsupported"
    elif any(item.get("content_status") == "unrecognized_or_wrong_date" for item in endpoint_rows):
        status = "unrecognized_or_wrong_date"
    elif endpoint_rows:
        status = "empty"
    else:
        status = "missing"
    return {
        "date": target.isoformat() if target else None,
        "generated_at": iso_now(DEFAULT_TIMEZONE),
        "status": status,
        "decision_use": "context_only_custom_readiness_remains_authoritative",
        "source_endpoint": source_label,
        "score": score,
        "level": level,
        "feedback": feedback,
        "factors": factors,
        "endpoint_health": endpoint_rows,
        "source_fetched_at": (snapshot or {}).get("fetched_at"),
        "flags": (
            [
                {
                    "type": "training_readiness_fetch_failed",
                    "severity": "yellow",
                    "message": "Garmin Training Readiness could not be read.",
                }
            ]
            if status == "failed"
            else [
                {
                    "type": "training_readiness_unrecognized_or_wrong_date",
                    "severity": "yellow",
                    "message": (
                        "Garmin Training Readiness returned nonempty data without recognized "
                        "same-date readiness content."
                    ),
                }
            ]
            if status == "unrecognized_or_wrong_date"
            else []
        ),
    }


def build_training_readiness_current(
    root: str | Path | None = None,
    for_date: str | date | None = None,
) -> dict:
    target = parse_date(for_date) or today_local(DEFAULT_TIMEZONE)
    snapshot_date, snapshot = _latest_snapshot(root, target)
    normalized = normalize_training_readiness_payload(snapshot, snapshot_date or target)
    capabilities = _latest_device_capability(root, target)
    capability_payload = capabilities if isinstance(capabilities, dict) else {}
    capable = capability_payload.get("training_readiness_capable")
    capability_date = parse_date(capability_payload.get("date"))
    normalized["device_capability"] = {
        "status": (
            "supported"
            if capable is True
            else "unsupported"
            if capable is False
            else "unknown"
        ),
        "training_readiness_capable": capable,
        "registered_device_count": capability_payload.get("registered_device_count"),
        "source_date": capability_date.isoformat() if capability_date else None,
        "age_days": (target - capability_date).days if capability_date else None,
        "source": (
            "snapshots/garmin_device_capabilities_<date>.json"
            if capabilities is not None
            else None
        ),
    }
    if capable is False and normalized.get("status") in {"empty", "missing"}:
        normalized["status"] = "unsupported_by_registered_devices"
        normalized["flags"] = []
    age_days = (target - snapshot_date).days if snapshot_date is not None else None
    normalized["target_date"] = target.isoformat()
    normalized["source_snapshot_date"] = snapshot_date.isoformat() if snapshot_date else None
    normalized["freshness"] = {
        "status": (
            "missing"
            if snapshot_date is None
            else "current"
            if age_days == 0
            else "stale"
        ),
        "age_days": age_days,
    }
    if age_days is not None and age_days > 0 and normalized.get("status") == "available":
        normalized["source_availability_status"] = "available"
        normalized["status"] = "stale"
        normalized.setdefault("flags", []).append(
            {
                "type": "training_readiness_stale",
                "severity": "yellow",
                "message": f"Garmin Training Readiness is {age_days} day(s) old.",
            }
        )
    if snapshot_date is None:
        normalized["date"] = target.isoformat()
    write_json(snapshots_dir(root) / "garmin_training_readiness_current.json", normalized)
    return normalized
