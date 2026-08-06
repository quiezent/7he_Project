from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .evidence import load_activities
from .io import read_json, write_json, write_text
from .paths import snapshots_dir
from .time_utils import DEFAULT_TIMEZONE, iso_now, parse_date, today_local


BIKE_GEAR_CATEGORIES = {"mtb", "bike_indoor", "bike_outdoor"}
ELITE_SUITO_TERMS = ("elite suito", "suito")


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def gear_label(gear: dict) -> str:
    parts = [
        _text(gear.get("customMakeModel")),
        _text(gear.get("displayName")),
        _text(gear.get("gearMakeName")),
        _text(gear.get("gearModelName")),
    ]
    return " / ".join(part for part in parts if part)


def summarize_gear_items(payload: Any) -> list[dict]:
    if not isinstance(payload, list):
        return []
    rows = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "gear_pk": item.get("gearPk"),
                "uuid": item.get("uuid"),
                "label": gear_label(item),
                "custom_make_model": item.get("customMakeModel"),
                "display_name": item.get("displayName"),
                "gear_type": item.get("gearTypeName"),
                "status": item.get("gearStatusName"),
            }
        )
    return rows


def _gear_text(gear: list[dict]) -> str:
    return " ".join(
        " ".join(
            _text(item.get(key))
            for key in ("label", "custom_make_model", "display_name", "gear_type")
        )
        for item in gear
    ).lower()


def gear_matches(gear: list[dict], terms: tuple[str, ...]) -> bool:
    text = _gear_text(gear)
    return any(term in text for term in terms)


def _activity_lookup(root: str | Path | None) -> dict[str, dict]:
    return {str(activity.get("id")): activity for activity in load_activities(root)}


def _read_activity_gear_index(root: str | Path | None) -> dict:
    payload = read_json(snapshots_dir(root) / "activity_gear_index.json", {})
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, list):
        return {"activities": payload}
    return {"activities": []}


def _text_report(report: dict) -> str:
    lines = [
        f"Gear Audit - {report['date']}",
        "",
        f"Checked activities: {report['checked_activities']}",
        f"MTB checked: {report['mtb_checked']}",
        f"Coverage: {(report.get('coverage') or {}).get('status', 'unknown')}",
        "",
        "Flags:",
    ]
    if report["flags"]:
        lines.extend(f"- {flag['message']}" for flag in report["flags"])
    else:
        lines.append("- None")
    lines.extend(["", "Recent MTB Gear:"])
    for row in report["recent_mtb_gear"]:
        labels = ", ".join(row.get("gear_labels") or []) or "no gear"
        lines.append(f"- {row.get('date')}: {labels}")
    return "\n".join(lines) + "\n"


def build_gear_audit(
    root: str | Path | None = None,
    for_date: str | date | None = None,
    lookback_days: int = 90,
) -> dict:
    target = parse_date(for_date) or today_local(DEFAULT_TIMEZONE)
    start = target - timedelta(days=max(1, lookback_days) - 1)
    index = _read_activity_gear_index(root)
    activities = _activity_lookup(root)
    rows = []
    flags = []
    recent_mtb = []

    for row in index.get("activities") or []:
        if not isinstance(row, dict):
            continue
        activity_id = str(row.get("activity_id") or row.get("id") or "")
        activity = activities.get(activity_id, {})
        act_date = parse_date(row.get("date") or activity.get("date"))
        if not act_date or act_date < start or act_date > target:
            continue
        category = row.get("category") or activity.get("category")
        gear = row.get("gear") if isinstance(row.get("gear"), list) else []
        normalized = {
            "activity_id": activity_id,
            "date": act_date.isoformat(),
            "name": row.get("name") or activity.get("name"),
            "type": row.get("type") or activity.get("type"),
            "category": category,
            "gear_fetch_ok": row.get("gear_fetch_ok"),
            "gear_fetch_error": row.get("gear_fetch_error"),
            "fetch": row.get("fetch"),
            "latest_attempt": row.get("latest_attempt"),
            "last_success_at": row.get("last_success_at"),
            "gear_labels": [item.get("label") for item in gear if item.get("label")],
        }
        rows.append(normalized)
        if category == "mtb":
            recent_mtb.append(normalized)
            latest_attempt = (
                normalized.get("latest_attempt")
                if isinstance(normalized.get("latest_attempt"), dict)
                else {}
            )
            fetch_status = str(
                latest_attempt.get("status")
                or (normalized.get("fetch") or {}).get("status")
                or ("success" if normalized.get("gear_fetch_ok") else "failed")
            )
            if (
                normalized.get("gear_fetch_ok") is True
                and latest_attempt.get("status") in {"failed", "unsupported"}
            ):
                flags.append(
                    {
                        "type": "gear_metadata_refresh_failed_using_cached",
                        "severity": "yellow",
                        "activity_id": activity_id,
                        "date": act_date.isoformat(),
                        "message": (
                            f"{act_date.isoformat()} MTB activity {activity_id} Gear refresh failed; "
                            "using preserved last-known-good bike/source evidence."
                        ),
                    }
                )
            elif normalized.get("gear_fetch_ok") is not True and fetch_status in {
                "failed",
                "unsupported",
            }:
                flags.append(
                    {
                        "type": "mtb_gear_unknown_metadata_unavailable",
                        "severity": "yellow",
                        "activity_id": activity_id,
                        "date": act_date.isoformat(),
                        "message": (
                            f"{act_date.isoformat()} MTB activity {activity_id} Gear metadata "
                            "could not be fetched; bike/source context is unknown."
                        ),
                    }
                )
            elif gear_matches(gear, ELITE_SUITO_TERMS):
                flags.append(
                    {
                        "type": "mtb_activity_gear_elite_suito",
                        "severity": "action_needed",
                        "activity_id": activity_id,
                        "date": act_date.isoformat(),
                        "message": (
                            f"{act_date.isoformat()} MTB activity {activity_id} is tagged with "
                            "Elite Suito gear; update the Garmin Gear field."
                        ),
                    }
                )

    eligible = []
    for activity in activities.values():
        activity_date = parse_date(activity.get("date"))
        if (
            activity_date is not None
            and start <= activity_date <= target
            and activity.get("category") in BIKE_GEAR_CATEGORIES
        ):
            eligible.append(activity)
    eligible_ids = {str(activity.get("id")) for activity in eligible if activity.get("id")}
    covered_ids = {row["activity_id"] for row in rows if row.get("activity_id") in eligible_ids}
    fetched_ok = sum(1 for row in rows if row.get("activity_id") in eligible_ids and row.get("gear_fetch_ok") is True)
    coverage_status = (
        "complete"
        if eligible_ids and covered_ids == eligible_ids and fetched_ok == len(eligible_ids)
        else "partial"
        if covered_ids
        else "missing"
    )
    report = {
        "date": target.isoformat(),
        "generated_at": iso_now(DEFAULT_TIMEZONE),
        "source_index": "snapshots/activity_gear_index.json",
        "lookback_days": lookback_days,
        "checked_activities": len(rows),
        "mtb_checked": sum(1 for row in rows if row.get("category") == "mtb"),
        "coverage": {
            "status": coverage_status,
            "eligible_activities": len(eligible_ids),
            "indexed_activities": len(covered_ids),
            "successful_fetches": fetched_ok,
            "missing_activities": max(0, len(eligible_ids) - len(covered_ids)),
        },
        "recent_mtb_gear": sorted(recent_mtb, key=lambda item: item.get("date") or "", reverse=True)[:10],
        "flags": flags,
    }
    write_json(snapshots_dir(root) / "gear_audit.json", report)
    write_text(snapshots_dir(root) / "gear_audit.txt", _text_report(report))
    return report
