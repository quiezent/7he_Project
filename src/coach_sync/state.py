from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .activity_profile import build_activity_profile
from .adaptive_training import build_adaptive_training_state
from .body_battery_model import build_body_battery_model
from .context import load_context
from .cns_readiness import build_cns_readiness
from .device_audit import build_device_audit
from .evidence import as_number, load_activities, load_latest_training_status, load_latest_wellness, summarize_recent_training
from .gear_audit import build_gear_audit
from .historical_baselines import build_historical_baselines
from .io import read_json, write_json
from .load_model import (
    build_bike_continuity_accountability,
    build_modality_load_rollups,
)
from .paths import snapshots_dir
from .readiness import build_readiness
from .rest_recharge import build_rest_recharge_window
from .ride_conditions import build_environment_evidence
from .self_evaluation import build_self_evaluation_report
from .session_evidence import build_latest_session_evidence
from .session_response import build_latest_session_response
from .training_status import build_training_status_current
from .training_predictor import build_training_predictor
from .training_readiness import build_training_readiness_current
from .time_utils import DEFAULT_TIMEZONE, iso_now, parse_date, today_local
from .wearable_coverage import build_wearable_coverage
from .wellness import build_wellness_trends
from .wellness_verification import build_wellness_verification


def determine_phase(context: dict, target_date: date) -> dict:
    configured = context.get("goal_progression", {}).get("current_phase", "base_rebuild")
    return {
        "name": configured,
        "reason": "Using configured goal progression phase.",
    }


def _cycling_ftp_current(
    root: str | Path | None,
    target_date: date,
) -> dict:
    artifact = read_json(snapshots_dir(root) / "garmin_cycling_ftp_current.json", {})
    if not isinstance(artifact, dict) or not artifact:
        return {
            "status": "missing",
            "ftp_w": None,
            "effective_date": None,
            "source": "snapshots/garmin_cycling_ftp_current.json",
        }
    effective = parse_date(artifact.get("effective_date"))
    if effective is not None and effective > target_date:
        return {
            "status": "future_excluded",
            "ftp_w": None,
            "effective_date": effective.isoformat(),
            "age_days": None,
            "source": "snapshots/garmin_cycling_ftp_current.json",
            "latest_attempt": artifact.get("latest_attempt"),
            "interpretation": "A future-dated Garmin FTP cannot represent the historical target date.",
        }
    ftp_w = as_number(artifact.get("ftp_w"))
    available = ftp_w is not None and ftp_w > 0
    age_days = (target_date - effective).days if effective is not None else None
    return {
        "status": artifact.get("status") if available else "unavailable",
        "ftp_w": round(ftp_w, 1) if available else None,
        "effective_date": effective.isoformat() if effective else None,
        "effective_at": artifact.get("effective_at"),
        "age_days": age_days,
        "sport": artifact.get("sport"),
        "biometric_source_type": artifact.get("biometric_source_type"),
        "detection_source": artifact.get("detection_source"),
        "last_success_at": artifact.get("last_success_at"),
        "latest_attempt": artifact.get("latest_attempt"),
        "source": "snapshots/garmin_cycling_ftp_current.json",
        "source_endpoint": artifact.get("source"),
        "decision_use": (
            "Current Garmin operational FTP for FTP-relative prescription with RPE/HR validation; "
            "not equivalent to a clean steady-state or laboratory test."
            if available
            else "No current Garmin operational FTP is available."
        ),
        "confidence": {
            "garmin_operational_value": "high" if available else "missing",
            "clean_test_physiological_validity": "moderate" if available else "missing",
        },
    }


def build_training_load_snapshot(
    root: str | Path | None,
    activities: list[dict],
    target_date: date,
    context: dict,
) -> dict:
    training = summarize_recent_training(activities, target_date)
    technical_start = target_date - timedelta(days=2)
    technical_rows = []
    for activity in activities:
        activity_date = parse_date(activity.get("date"))
        if (
            activity_date is None
            or activity_date < technical_start
            or activity_date > target_date
            or activity.get("category") != "mtb"
        ):
            continue
        technical_rows.append(
            {
                "date": activity_date.isoformat(),
                "category": activity.get("category"),
                "duration_min": activity.get("duration_min"),
                "training_load": activity.get("training_load"),
                "hr_zone_min": activity.get("hr_zone_min"),
            }
        )
    training["recent_technical_activities"] = sorted(
        technical_rows,
        key=lambda item: item.get("date") or "",
        reverse=True,
    )
    threshold = context.get("training_rules", {}).get("acute_chronic_load_spike_ratio", 1.5)
    spike = training.get("acute_load_spike_ratio")
    training["flags"] = []
    if spike is not None and spike > threshold:
        training["flags"].append(
            {
                "type": "week_over_week_load_jump",
                "message": f"Local 7-day load is {spike}x the prior 7 days; compare with Garmin ACWR before using as a readiness limiter.",
            }
        )
    write_json(snapshots_dir(root) / "training_load.json", training)
    return training


def _cached_or_build_report(
    root: str | Path | None,
    filename: str,
    builder,
    target_date: date,
    refresh_models: bool,
) -> dict:
    if refresh_models:
        return builder(root, target_date)
    cached = read_json(snapshots_dir(root) / filename, {})
    cached_date = parse_date(cached.get("date")) if isinstance(cached, dict) else None
    if isinstance(cached, dict) and cached and cached_date == target_date:
        return cached
    return builder(root, target_date)


def _guard_current_model_predictions(
    body_model: dict,
    training_predictor: dict,
    wearable_coverage: dict,
    target_date: date,
) -> tuple[dict, dict]:
    """Mask same-day cached optimistic predictions after coverage deteriorates."""
    decision = (wearable_coverage.get("decision_use") or {}).get(
        "low_stress_positive_reward_eligible"
    )
    if decision is not False:
        return body_model, training_predictor
    guard = {
        "applied": True,
        "date": target_date.isoformat(),
        "reason": (
            "Fresh wearable coverage no longer permits positive low-stress use; "
            "a same-day cached low-stress prediction is excluded until rebuilt."
        ),
    }

    guarded_body = dict(body_model)
    latest_row = body_model.get("latest_row") or {}
    latest_features = latest_row.get("features") or {}
    latest_stress = as_number(latest_features.get("avg_stress"))
    if (
        latest_row.get("date") == target_date.isoformat()
        and (latest_stress is None or latest_stress <= 30)
    ):
        guarded_body["latest_prediction"] = None
        guarded_body["wearable_coverage_cache_guard"] = guard
        guarded_body["latest_row"] = {
            **latest_row,
            "decision_use": "excluded_by_fresh_wearable_coverage",
        }

    guarded_predictor = dict(training_predictor)
    today_prediction = training_predictor.get("today_prediction") or {}
    today_features = today_prediction.get("features") or {}
    today_stress = as_number(today_features.get("today_avg_stress"))
    if (
        today_prediction.get("basis_date") == target_date.isoformat()
        and today_prediction.get("prediction")
        and (today_stress is None or today_stress <= 30)
    ):
        warnings = list(today_prediction.get("warnings") or [])
        warnings.append(guard["reason"])
        guarded_predictor["today_prediction"] = {
            **today_prediction,
            "status": "unavailable_coverage_superseded",
            "prediction": None,
            "prediction_path": [],
            "coverage_guard": guard,
            "warnings": warnings,
        }
        guarded_predictor["wearable_coverage_cache_guard"] = guard
    return guarded_body, guarded_predictor


def _source_timestamp(value, tz: str, assume_utc: bool = False) -> datetime | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 10_000_000_000:
            seconds /= 1000
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc if assume_utc else ZoneInfo(tz))
    return parsed


def _source_age_min(
    value,
    target_date: date,
    tz: str,
    assume_utc: bool = False,
) -> float | None:
    now = datetime.now(ZoneInfo(tz))
    if target_date != now.date():
        return None
    parsed = _source_timestamp(value, tz, assume_utc=assume_utc)
    if parsed is None:
        return None
    return round((now - parsed.astimezone(ZoneInfo(tz))).total_seconds() / 60, 1)


def _wellness_intraday_provenance(wellness_trends: dict, target_date: date, tz: str) -> dict:
    latest = wellness_trends.get("latest") if isinstance(wellness_trends, dict) else None
    latest = latest if isinstance(latest, dict) else {}
    fetched_at = latest.get("source_fetched_at")
    last_sync = latest.get("source_last_sync_timestamp_gmt")
    data_cutoff = latest.get("source_data_cutoff_local")
    available = any(value is not None for value in (fetched_at, last_sync, data_cutoff))
    return {
        "status": "available" if available else "unavailable",
        "source_fetched_at": fetched_at,
        "source_fetched_at_age_min": _source_age_min(fetched_at, target_date, tz),
        "source_last_sync_timestamp_gmt": last_sync,
        "source_last_sync_age_min": _source_age_min(
            last_sync,
            target_date,
            tz,
            assume_utc=True,
        ),
        "source_data_cutoff_local": data_cutoff,
        "source_data_cutoff_age_min": _source_age_min(data_cutoff, target_date, tz),
        "age_basis": (
            "wall_clock_at_state_generation"
            if target_date == datetime.now(ZoneInfo(tz)).date()
            else "not_computed_for_historical_target"
        ),
        "interpretation": (
            "Calendar-date freshness does not prove intraday completeness; use the data cutoff age "
            "before a same-day hard-session upgrade."
        ),
    }


def build_current_state(
    root: str | Path | None = None,
    for_date: str | date | None = None,
    refresh_models: bool = True,
) -> dict:
    context = load_context(root)
    tz = context.get("athlete", {}).get("timezone", DEFAULT_TIMEZONE)
    target_date = parse_date(for_date) or today_local(tz)
    environment_evidence = build_environment_evidence(root, target_date)
    readiness = build_readiness(root, target_date)
    activities = load_activities(root)
    latest_session_evidence = build_latest_session_evidence(
        root,
        target_date,
        activities=activities,
    )
    latest_session_activity = latest_session_evidence.get("activity") or {}
    latest_session_response = build_latest_session_response(
        root,
        parse_date(latest_session_activity.get("date")) or target_date,
        activity_id=latest_session_activity.get("activity_id"),
        self_evaluation=latest_session_evidence.get("self_evaluation"),
        subjective_review_policy=(
            (context.get("coaching_evidence_ontology") or {}).get(
                "garmin_subjective_review_policy"
            )
        ),
    )
    phase = determine_phase(context, target_date)
    wellness_date, wellness = load_latest_wellness(root, target_date)
    training_status_date, training_status = load_latest_training_status(root, target_date)
    training_load = build_training_load_snapshot(root, activities, target_date, context)
    wellness_trends = build_wellness_trends(root, target_date)
    wellness_verification = build_wellness_verification(root, target_date)
    exact_date_wellness = read_json(
        snapshots_dir(root) / f"garmin_wellness_{target_date.isoformat()}.json",
        {},
    )
    wearable_coverage = build_wearable_coverage(
        root,
        target_date,
        wellness_snapshot=exact_date_wellness,
    )
    rest_recharge_window = build_rest_recharge_window(
        root,
        target_date,
        wellness_snapshot=exact_date_wellness,
        normalized_wellness=wellness_trends.get("latest") or {},
    )
    activity_profile = build_activity_profile(root, target_date)
    training_status_current = build_training_status_current(root, target_date.isoformat())
    training_readiness_current = build_training_readiness_current(root, target_date)
    cycling_ftp_current = _cycling_ftp_current(root, target_date)
    modality_load_rollups = build_modality_load_rollups(root, target_date)
    bike_continuity_accountability = build_bike_continuity_accountability(
        root,
        target_date,
        activities=activities,
        context=context,
    )
    body_battery_model = _cached_or_build_report(
        root,
        "body_battery_model_report.json",
        build_body_battery_model,
        target_date,
        refresh_models,
    )
    historical_baselines = _cached_or_build_report(
        root,
        "historical_activity_baselines.json",
        build_historical_baselines,
        target_date,
        refresh_models,
    )
    training_predictor = _cached_or_build_report(
        root,
        "training_response_model_report.json",
        build_training_predictor,
        target_date,
        refresh_models,
    )
    body_battery_model, training_predictor = _guard_current_model_predictions(
        body_battery_model,
        training_predictor,
        wearable_coverage,
        target_date,
    )
    gear_audit = build_gear_audit(root, target_date)
    device_audit = build_device_audit(root, target_date)
    self_evaluation = build_self_evaluation_report(root, target_date)
    cns_readiness = build_cns_readiness(
        root,
        target_date,
        wellness_trends=wellness_trends,
        training_status_current=training_status_current,
        training_load=training_load,
        self_evaluation=self_evaluation,
        rest_recharge_window=rest_recharge_window,
        wearable_coverage=wearable_coverage,
    )
    athlete = dict(context.get("athlete", {}))
    latest_body_composition = wellness_trends.get("latest_body_composition") or {}
    if athlete.get("body_weight_kg") is None and latest_body_composition.get("body_weight_kg") is not None:
        athlete["body_weight_kg"] = latest_body_composition.get("body_weight_kg")
        athlete["body_weight_source"] = {
            "source": "garmin_body_composition",
            "date": latest_body_composition.get("date"),
            "age_days": latest_body_composition.get("age_days"),
        }

    hard_limiters = []

    if wellness_date is None:
        wellness_freshness = {
            "status": "missing",
            "message": "No Garmin wellness data has been synced.",
        }
        hard_limiters.append(wellness_freshness["message"])
    else:
        wellness_age = (target_date - wellness_date).days
        if wellness_age < 0:
            wellness_freshness = {
                "status": "future",
                "age_days": wellness_age,
                "message": "Latest wellness snapshot is dated after the target date.",
            }
            hard_limiters.append(wellness_freshness["message"])
        elif wellness_age == 0:
            wellness_freshness = {
                "status": "current",
                "age_days": wellness_age,
                "message": "Garmin wellness data is current.",
            }
        else:
            wellness_freshness = {
                "status": "stale",
                "age_days": wellness_age,
                "message": f"Garmin wellness data is {wellness_age} day(s) behind the wall-clock date.",
            }
            hard_limiters.append(wellness_freshness["message"])

    wellness_freshness["intraday_provenance"] = _wellness_intraday_provenance(
        wellness_trends,
        target_date,
        tz,
    )

    if training_status_date is None:
        training_status_freshness = {
            "status": "missing",
            "message": "No Garmin training status snapshot is available.",
        }
        hard_limiters.append(training_status_freshness["message"])
    else:
        status_age = (target_date - training_status_date).days
        if status_age < 0:
            training_status_freshness = {
                "status": "future",
                "age_days": status_age,
                "message": "Latest training status is dated after the target date.",
            }
            hard_limiters.append(training_status_freshness["message"])
        elif status_age == 0:
            training_status_freshness = {
                "status": "current",
                "age_days": status_age,
                "message": "Garmin training status is current.",
            }
        else:
            training_status_freshness = {
                "status": "stale",
                "age_days": status_age,
                "message": f"Latest training status is {status_age} day(s) old.",
            }
            hard_limiters.append(training_status_freshness["message"])

    latest_training_activity = training_load.get("latest_training_activity") or training_load.get(
        "latest_activity"
    )
    if latest_training_activity is None:
        activity_freshness = {
            "status": "missing",
            "message": "No Garmin training activity data is available to verify recent load.",
        }
        hard_limiters.append(activity_freshness["message"])
    else:
        latest_activity_date = parse_date(latest_training_activity.get("date"))
        activity_age = (target_date - latest_activity_date).days if latest_activity_date else None
        if activity_age is None:
            activity_freshness = {
                "status": "unknown",
                "message": "Latest training activity has no usable date.",
            }
            hard_limiters.append(activity_freshness["message"])
        elif activity_age < 0:
            activity_freshness = {
                "status": "future",
                "age_days": activity_age,
                "message": "Latest training activity is dated after the target date and cannot support this plan.",
            }
            hard_limiters.append(activity_freshness["message"])
        elif activity_age > 7:
            activity_freshness = {
                "status": "stale",
                "age_days": activity_age,
                "latest_activity_date": latest_activity_date.isoformat(),
                "message": f"Latest training activity is {activity_age} day(s) old.",
            }
            hard_limiters.append(activity_freshness["message"])
        else:
            activity_freshness = {
                "status": "current",
                "age_days": activity_age,
                "latest_activity_date": latest_activity_date.isoformat(),
                "message": "Recent training activity is available.",
            }

    freshness_statuses = (
        wellness_freshness.get("status"),
        training_status_freshness.get("status"),
        activity_freshness.get("status"),
    )
    if "missing" in freshness_statuses:
        overall_status = "missing"
    elif "stale" in freshness_statuses or "future" in freshness_statuses or "unknown" in freshness_statuses:
        overall_status = "stale"
    else:
        overall_status = "current"
    freshness = {
        "status": overall_status,
        "age_days": wellness_freshness.get("age_days"),
        "latest_wellness_date": wellness_date.isoformat() if wellness_date else None,
        "message": (
            "Garmin readiness inputs are current."
            if overall_status == "current"
            else next((item for item in hard_limiters if item), "Readiness inputs are not current; limit hard-session confidence.")
        ),
        "wellness_data": wellness_freshness,
        "training_status_data": training_status_freshness,
        "activity_data": activity_freshness,
        "hard_session_confidence": "normal" if not hard_limiters else "limited",
        "hard_session_limiters": [item for item in hard_limiters if item],
    }

    adaptive_training = build_adaptive_training_state(
        root,
        target_date,
        context=context,
        activities=activities,
        readiness=readiness,
        cns_readiness=cns_readiness,
        training_status=training_status_current,
    )

    state = {
        "date": target_date.isoformat(),
        "generated_at": iso_now(tz),
        "build_mode": "full" if refresh_models else "decision_quick",
        "athlete": athlete,
        "goal": context.get("athlete", {}).get("goal"),
        "phase": phase,
        "readiness": readiness,
        "readiness_accuracy": readiness.get("readiness_accuracy"),
        "data_freshness": freshness,
        "environment_evidence": environment_evidence,
        "training_load": training_load,
        "wellness_trends": wellness_trends,
        "wellness_verification": wellness_verification,
        "rest_recharge_window": rest_recharge_window,
        "wearable_coverage": wearable_coverage,
        "body_composition": latest_body_composition or None,
        "activity_profile": activity_profile,
        "training_status_current": training_status_current,
        "training_readiness_current": training_readiness_current,
        "cycling_ftp_current": cycling_ftp_current,
        "cns_readiness": cns_readiness,
        "modality_load_rollups": modality_load_rollups,
        "bike_continuity_accountability": bike_continuity_accountability,
        "adaptive_training": adaptive_training,
        "body_battery_model": {
            "samples": body_battery_model.get("samples"),
            "leave_one_out_accuracy": body_battery_model.get("leave_one_out_accuracy"),
            "latest_prediction": body_battery_model.get("latest_prediction"),
            "caveats": body_battery_model.get("caveats"),
        },
        "historical_baselines": {
            "activity_count": historical_baselines.get("activity_count"),
            "date_span": historical_baselines.get("date_span"),
            "category_counts": historical_baselines.get("category_counts"),
            "peak_windows": historical_baselines.get("peak_windows"),
            "pre_injury_mtb_baseline": historical_baselines.get("pre_injury_mtb_baseline"),
        },
        "training_predictor": {
            "samples": training_predictor.get("samples"),
            "validation": training_predictor.get("validation"),
            "feature_usage": training_predictor.get("feature_usage"),
            "today_prediction": training_predictor.get("today_prediction"),
            "caveats": training_predictor.get("caveats"),
        },
        "gear_audit": gear_audit,
        "device_audit": device_audit,
        "self_evaluation": self_evaluation,
        "latest_session_evidence": latest_session_evidence,
        "latest_session_response": latest_session_response,
        "latest_activity": training_load.get("latest_activity"),
        "latest_training_activity": training_load.get("latest_training_activity"),
        "evidence_sources": {
            "wellness_date": wellness_date.isoformat() if wellness_date else None,
            "training_status_date": (
                training_status_date.isoformat() if training_status_date else None
            ),
            "wellness_available": wellness is not None,
            "training_status_available": training_status is not None,
            "activity_count": len(activities),
            "environment_evidence_date": environment_evidence.get("date"),
            "environment_evidence_status": environment_evidence.get("status"),
            "environment_evidence_source": (
                (environment_evidence.get("persistence") or {}).get(
                    "current_snapshot"
                    if target_date == today_local(tz)
                    else "dated_snapshot"
                )
                or (
                    "snapshots/environment_evidence.json"
                    if target_date == today_local(tz)
                    else f"snapshots/environment_evidence_{target_date.isoformat()}.json"
                )
            ),
        },
    }
    write_json(snapshots_dir(root) / "current_state.json", state)
    return state
