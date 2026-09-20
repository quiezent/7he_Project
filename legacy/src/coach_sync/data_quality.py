from __future__ import annotations

from pathlib import Path

from .activity_profile import build_activity_profile
from .device_audit import build_device_audit
from .gear_audit import build_gear_audit
from .io import write_json
from .paths import snapshots_dir
from .self_evaluation import build_self_evaluation_report
from .training_status import build_training_status_current
from .wellness import build_wellness_trends


def build_data_quality_report(root: str | Path | None = None) -> dict:
    wellness = build_wellness_trends(root)
    activity = build_activity_profile(root)
    training_status = build_training_status_current(root)
    gear_audit = build_gear_audit(root)
    device_audit = build_device_audit(root)
    self_evaluation = build_self_evaluation_report(root)
    latest = wellness.get("latest") or {}
    required_wellness = [
        "sleep_score",
        "sleep_hours",
        "overnight_hrv",
        "hrv_status",
        "resting_hr",
        "body_battery_current",
        "avg_stress",
    ]
    missing_latest = [key for key in required_wellness if latest.get(key) is None]
    report = {
        "wellness": {
            "days_available": wellness.get("days_available"),
            "latest_date": latest.get("date"),
            "latest_missing_core_fields": missing_latest,
            "payloads": latest.get("available_payloads", []),
        },
        "activities": {
            "count": activity.get("activity_count"),
            "date_span": activity.get("date_span"),
            "categories": activity.get("categories"),
            "power_sessions": activity.get("power_sessions"),
        },
        "gear": {
            "checked_activities": gear_audit.get("checked_activities"),
            "mtb_checked": gear_audit.get("mtb_checked"),
            "flags": gear_audit.get("flags", []),
        },
        "devices": {
            "checked_activities": device_audit.get("checked_activities"),
            "mtb_checked": device_audit.get("mtb_checked"),
            "flags": device_audit.get("flags", []),
        },
        "self_evaluation": {
            "checked_activities": self_evaluation.get("checked_activities"),
            "evaluated_activities": self_evaluation.get("evaluated_activities"),
        },
        "training_status": {
            "date": training_status.get("date"),
            "source_payload_ok": training_status.get("source_payload_ok"),
            "has_acwr": training_status.get("acute_chronic", {}).get("ratio") is not None,
            "has_vo2max": training_status.get("vo2max", {}).get("cycling_value") is not None,
        },
        "privacy": {
            "activity_summary_index_redacts_names": True,
            "activity_summary_index_redacts_source_paths": True,
            "raw_activity_files_preserved": True,
        },
        "flags": [],
    }
    if missing_latest:
        report["flags"].append(
            {"type": "wellness_missing_fields", "message": f"Latest wellness is missing: {', '.join(missing_latest)}"}
        )
    if not training_status.get("source_payload_ok"):
        report["flags"].append(
            {"type": "training_status_missing", "message": "No usable Garmin training-status payload."}
        )
    report["flags"].extend(gear_audit.get("flags") or [])
    report["flags"].extend(device_audit.get("flags") or [])
    write_json(snapshots_dir(root) / "data_quality_report.json", report)
    return report
