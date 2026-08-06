from __future__ import annotations

from datetime import date
from pathlib import Path

from .activity_profile import build_activity_profile
from .device_audit import build_device_audit
from .gear_audit import build_gear_audit
from .io import write_json
from .paths import snapshots_dir
from .self_evaluation import build_self_evaluation_report
from .surface_manifest import build_garmin_surface_manifest
from .time_utils import DEFAULT_TIMEZONE, iso_now, parse_date, today_local
from .training_status import build_training_status_current
from .wearable_coverage import build_wearable_coverage
from .wellness import build_wellness_trends


def build_data_quality_report(
    root: str | Path | None = None,
    for_date: str | date | None = None,
) -> dict:
    target = parse_date(for_date) or today_local(DEFAULT_TIMEZONE)
    wellness = build_wellness_trends(root, target)
    activity = build_activity_profile(root, target)
    training_status = build_training_status_current(root, target.isoformat())
    gear_audit = build_gear_audit(root, target)
    device_audit = build_device_audit(root, target)
    self_evaluation = build_self_evaluation_report(root, target)
    wearable_coverage = build_wearable_coverage(root, target)
    manifest = build_garmin_surface_manifest(root, target)
    raw_sources = manifest.get("raw_sources") or {}
    raw_wellness = raw_sources.get("wellness") or {}
    raw_activity = raw_sources.get("activities") or {}
    metadata = raw_sources.get("activity_metadata") or {}
    status_coverage = raw_sources.get("training_status") or {}
    readiness_coverage = raw_sources.get("training_readiness") or {}
    capability_coverage = raw_sources.get("device_capabilities") or {}
    privacy = manifest.get("privacy_verification") or {}
    endpoint_health = {
        state: sorted(
            endpoint_id
            for endpoint_id, endpoint in (manifest.get("configured_endpoints") or {}).items()
            if endpoint.get("state") == state
        )
        for state in ("success", "success_empty", "failed", "unsupported", "not_attempted")
    }
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
    core_activity_fields = {
        key: (raw_activity.get("top_level_field_coverage") or {}).get(key)
        for key in (
            "activityId",
            "startTimeLocal",
            "duration",
            "movingDuration",
            "activityTrainingLoad",
            "averageHR",
            "maxHR",
            "avgPower",
            "normPower",
            "hrTimeInZone_1",
            "hrTimeInZone_2",
            "hrTimeInZone_3",
            "hrTimeInZone_4",
            "hrTimeInZone_5",
        )
        if key in (raw_activity.get("top_level_field_coverage") or {})
    }
    report = {
        "generated_at": iso_now(DEFAULT_TIMEZONE),
        "basis_date": target.isoformat(),
        "surface_manifest": "snapshots/garmin_surface_manifest.json",
        "source_timestamps": manifest.get("source_timestamps", {}),
        "wellness": {
            "days_available": wellness.get("days_available"),
            "latest_date": latest.get("date"),
            "latest_missing_core_fields": missing_latest,
            "payloads": latest.get("available_payloads", []),
            "data_eras": raw_wellness.get("usable_eras", []),
            "data_gaps": raw_wellness.get("gaps", []),
            "endpoint_coverage": raw_wellness.get("endpoints", {}),
            "latest_source_modified_at": raw_wellness.get("latest_source_modified_at"),
            "latest_fetched_at": raw_wellness.get("latest_fetched_at"),
            "latest_data_cutoff_local": raw_wellness.get("latest_data_cutoff_local"),
        },
        "activities": {
            "count": activity.get("activity_count"),
            "date_span": activity.get("date_span"),
            "categories": activity.get("categories"),
            "power_sessions": activity.get("power_sessions"),
            "raw_file_count": raw_activity.get("raw_file_count"),
            "invalid_file_count": raw_activity.get("invalid_file_count"),
            "top_level_field_count": raw_activity.get("top_level_field_count"),
            "core_field_coverage": core_activity_fields,
            "heart_rate_coverage": raw_activity.get("heart_rate_coverage", {}),
            "training_load_coverage": raw_activity.get("training_load_coverage", {}),
        },
        "gear": {
            "checked_activities": gear_audit.get("checked_activities"),
            "mtb_checked": gear_audit.get("mtb_checked"),
            "coverage": metadata.get("gear", gear_audit.get("coverage", {})),
            "flags": gear_audit.get("flags", []),
        },
        "devices": {
            "checked_activities": device_audit.get("checked_activities"),
            "mtb_checked": device_audit.get("mtb_checked"),
            "coverage": metadata.get("devices", device_audit.get("coverage", {})),
            "flags": device_audit.get("flags", []),
        },
        "self_evaluation": {
            "checked_activities": self_evaluation.get("checked_activities"),
            "evaluated_activities": self_evaluation.get("evaluated_activities"),
            "coverage": metadata.get("self_evaluation", {}),
        },
        "training_status": {
            "date": training_status.get("date"),
            "source_payload_ok": training_status.get("source_payload_ok"),
            "has_acwr": training_status.get("acute_chronic", {}).get("ratio") is not None,
            "has_vo2max": training_status.get("vo2max", {}).get("cycling_value") is not None,
            "coverage": status_coverage,
        },
        "training_readiness": {
            "coverage": readiness_coverage,
            "training_readiness_capable": capability_coverage.get(
                "training_readiness_capable"
            ),
            "decision_use": "context_only_custom_readiness_remains_authoritative",
        },
        "wearable_coverage": {
            "classification": (wearable_coverage.get("classification") or {}).get(
                "label"
            ),
            "status": wearable_coverage.get("status"),
            "stress": ((wearable_coverage.get("observed_coverage") or {}).get("stress") or {}),
            "body_battery": (
                (wearable_coverage.get("observed_coverage") or {}).get("body_battery")
                or {}
            ),
            "optical_heart_rate": (
                (wearable_coverage.get("observed_coverage") or {}).get(
                    "optical_heart_rate"
                )
                or {}
            ),
            "decision_use": wearable_coverage.get("decision_use"),
            "attribution_summary": wearable_coverage.get("attribution_summary"),
            "material_run_attribution": wearable_coverage.get(
                "material_run_attribution"
            ),
            "safety_contract": wearable_coverage.get("safety_contract"),
        },
        "device_capabilities": capability_coverage,
        "privacy": privacy,
        "endpoint_state_counts": manifest.get("endpoint_state_counts", {}),
        "endpoint_health": endpoint_health,
        "flags": [],
    }
    if missing_latest:
        report["flags"].append(
            {"type": "wellness_missing_fields", "message": f"Latest wellness is missing: {', '.join(missing_latest)}"}
        )
    unavailable_minutes = (
        ((wearable_coverage.get("observed_coverage") or {}).get("stress") or {}).get(
            "material_unavailable_minutes"
        )
        or 0
    )
    if unavailable_minutes:
        report["flags"].append(
            {
                "type": "wearable_internal_coverage_partial",
                "severity": "coverage",
                "message": (
                    f"Target-date all-day stress contains {unavailable_minutes:g} minute(s) "
                    "of material internal unavailability; low stress cannot earn positive credit."
                ),
            }
        )
    positive_use_coverage = (
        ((wearable_coverage.get("observed_coverage") or {}).get("stress") or {}).get(
            "positive_use_coverage"
        )
        or {}
    )
    if (
        wearable_coverage.get("status") == "available"
        and positive_use_coverage.get("series_sufficient_for_low_stress_reward")
        is False
    ):
        report["flags"].append(
            {
                "type": "wearable_positive_use_coverage_insufficient",
                "severity": "coverage",
                "message": (
                    "Target-date all-day stress lacks sufficient valid density, cadence, "
                    "or boundary coverage for positive low-stress use; high observed stress "
                    "may still downshift."
                ),
            }
        )
    if not training_status.get("source_payload_ok"):
        report["flags"].append(
            {"type": "training_status_missing", "message": "No usable Garmin training-status payload."}
        )
    if endpoint_health["failed"]:
        report["flags"].append(
            {
                "type": "garmin_surface_endpoint_failed",
                "severity": "coverage",
                "message": "Persisted call evidence shows failed Garmin surface(s): "
                + ", ".join(endpoint_health["failed"]),
            }
        )
    for coverage_name, label in (
        ("gear", "Gear"),
        ("devices", "Devices & Apps"),
        ("self_evaluation", "self-evaluation"),
    ):
        coverage = metadata.get(coverage_name) or {}
        if coverage.get("eligible_activities", 0) and coverage.get("status") != "complete":
            report["flags"].append(
                {
                    "type": f"{coverage_name}_coverage_partial",
                    "severity": "coverage",
                    "message": (
                        f"{label} metadata covers {coverage.get('indexed_activities', 0)} of "
                        f"{coverage.get('eligible_activities', 0)} eligible activities in its "
                        f"{coverage.get('lookback_days')}-day window."
                    ),
                }
            )
    gaps = raw_wellness.get("gaps") or []
    if gaps:
        largest_gap = max(gaps, key=lambda item: item.get("missing_days") or 0)
        report["flags"].append(
            {
                "type": "wellness_history_discontinuous",
                "severity": "coverage",
                "message": (
                    f"Wellness history has {len(gaps)} gap(s); the largest is "
                    f"{largest_gap.get('missing_days')} day(s) between "
                    f"{largest_gap.get('after')} and {largest_gap.get('before')}."
                ),
            }
        )
    if raw_activity.get("invalid_file_count"):
        report["flags"].append(
            {
                "type": "raw_activity_files_invalid",
                "severity": "data_integrity",
                "message": f"{raw_activity.get('invalid_file_count')} raw activity JSON file(s) are unreadable or invalid.",
            }
        )
    if privacy.get("activity_summary_index_exists"):
        if not privacy.get("activity_summary_index_redacts_names"):
            report["flags"].append(
                {
                    "type": "activity_index_name_privacy_failed",
                    "severity": "data_integrity",
                    "message": "The activity summary index contains a name-bearing field.",
                }
            )
        if not privacy.get("activity_summary_index_redacts_source_paths"):
            report["flags"].append(
                {
                    "type": "activity_index_path_privacy_failed",
                    "severity": "data_integrity",
                    "message": "The activity summary index contains a local source path.",
                }
            )
    else:
        report["flags"].append(
            {
                "type": "activity_index_privacy_not_verifiable",
                "severity": "coverage",
                "message": "No activity summary index exists, so its redaction behavior cannot be verified.",
            }
        )
    report["flags"].extend(gear_audit.get("flags") or [])
    report["flags"].extend(device_audit.get("flags") or [])
    write_json(snapshots_dir(root) / "data_quality_report.json", report)
    return report
