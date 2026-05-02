from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from .activity_profile import build_activity_profile
from .body_battery_model import build_body_battery_model
from .checkin import load_daily_checkin
from .context import active_modality_overrides, clearance_summary, load_context
from .evidence import load_activities, load_latest_training_status, load_latest_wellness, summarize_recent_training
from .historical_baselines import build_historical_baselines
from .io import write_json
from .load_model import build_modality_load_rollups
from .paths import snapshots_dir
from .readiness import build_readiness
from .training_status import build_training_status_current
from .training_predictor import build_training_predictor
from .time_utils import DEFAULT_TIMEZONE, iso_now, parse_date, today_local
from .wellness import build_wellness_trends


def _clearance_date(clearance: dict) -> date | None:
    dates = []
    for gate in clearance.get("gates", {}).values():
        try:
            if gate.get("status") == "cleared":
                dates.append(parse_date(gate.get("date")))
        except ValueError:
            continue
    dates = [value for value in dates if value is not None]
    return max(dates) if dates else None


def _is_mtb_activity(activity: dict) -> bool:
    return activity.get("category") == "mtb"


def determine_phase(context: dict, clearance: dict, target_date: date) -> dict:
    configured = context.get("goal_progression", {}).get("current_phase", "protected_recovery")
    if not clearance.get("all_cleared"):
        return {
            "name": "protected_recovery",
            "reason": "One or more medical clearance gates are not cleared.",
            "days_since_full_clearance": None,
        }

    full_clearance_date = _clearance_date(clearance)
    days_since = (target_date - full_clearance_date).days if full_clearance_date else None
    reentry_days = (
        context.get("goal_progression", {})
        .get("phase_rules", {})
        .get("return_to_outdoor_reentry", {})
        .get("minimum_days", 14)
    )
    if days_since is not None and days_since < reentry_days:
        return {
            "name": "return_to_outdoor_reentry",
            "reason": "All gates are cleared, but this is still the initial outdoor/gym re-entry window.",
            "days_since_full_clearance": days_since,
        }
    if configured == "return_to_outdoor_reentry":
        return {
            "name": "base_rebuild",
            "reason": "Initial re-entry window is complete; defaulting to base rebuild unless context is updated.",
            "days_since_full_clearance": days_since,
        }
    return {
        "name": configured,
        "reason": "Using configured goal progression phase.",
        "days_since_full_clearance": days_since,
    }


def build_training_load_snapshot(
    root: str | Path | None,
    activities: list[dict],
    target_date: date,
    context: dict,
) -> dict:
    training = summarize_recent_training(activities, target_date)
    threshold = context.get("training_rules", {}).get("acute_chronic_load_spike_ratio", 1.5)
    spike = training.get("acute_load_spike_ratio")
    training["flags"] = []
    if spike is not None and spike > threshold:
        training["flags"].append(
            {
                "type": "load_spike",
                "message": f"Acute load is {spike}x prior 7 days.",
            }
        )
    write_json(snapshots_dir(root) / "training_load.json", training)
    return training


def build_injury_return_snapshot(
    root: str | Path | None,
    context: dict,
    activities: list[dict],
    target_date: date,
    clearance: dict,
) -> dict:
    checkin = load_daily_checkin(root, target_date.isoformat())
    since_date = _clearance_date(clearance) or target_date
    exposures = []
    mtb_last_7 = []
    last_7_start = target_date - timedelta(days=6)
    for activity in activities:
        act_date = parse_date(activity.get("date"))
        if not act_date or act_date < since_date:
            continue
        act_type = (activity.get("type") or "").lower()
        exposure_type = "gym" if "strength" in act_type or "training" in act_type else "ride"
        if _is_mtb_activity(activity) and last_7_start <= act_date <= target_date:
            mtb_last_7.append(activity)
        exposures.append(
            {
                "date": activity.get("date"),
                "type": exposure_type,
                "activity_type": activity.get("type"),
                "duration_min": activity.get("duration_min"),
                "training_load": activity.get("training_load"),
            }
        )
    window_end = since_date + timedelta(days=14)
    artifact = {
        "date": target_date.isoformat(),
        "full_clearance_date": since_date.isoformat(),
        "reentry_window_end": window_end.isoformat(),
        "clearance": clearance,
        "symptoms_today": checkin,
        "post_clearance_exposures": exposures,
        "mtb_exposures_last_7_days": len(mtb_last_7),
        "flags": [],
    }
    if target_date <= window_end and len(exposures) >= 5:
        artifact["flags"].append(
            {
                "type": "dense_reentry_exposure",
                "message": "Five or more ride/gym exposures inside the first 14-day re-entry window.",
            }
        )
    cap = (
        context.get("training_rules", {})
        .get("first_14_day_reentry", {})
        .get("max_outdoor_mtb_days_per_7d", 3)
    )
    if target_date <= window_end and len(mtb_last_7) >= cap:
        artifact["flags"].append(
            {
                "type": "reentry_mtb_cap_reached",
                "message": f"{len(mtb_last_7)} MTB exposures in the last 7 days meets or exceeds the re-entry cap of {cap}.",
            }
        )
    write_json(snapshots_dir(root) / "injury_return.json", artifact)
    return artifact


def build_current_state(root: str | Path | None = None, for_date: str | date | None = None) -> dict:
    context = load_context(root)
    tz = context.get("athlete", {}).get("timezone", DEFAULT_TIMEZONE)
    target_date = parse_date(for_date) or today_local(tz)
    readiness = build_readiness(root, target_date)
    activities = load_activities(root)
    clearance = clearance_summary(context)
    phase = determine_phase(context, clearance, target_date)
    wellness_date, wellness = load_latest_wellness(root, target_date)
    training_status_date, training_status = load_latest_training_status(root, target_date)
    training_load = build_training_load_snapshot(root, activities, target_date, context)
    injury_return = build_injury_return_snapshot(root, context, activities, target_date, clearance)
    wellness_trends = build_wellness_trends(root, target_date)
    activity_profile = build_activity_profile(root, target_date)
    training_status_current = build_training_status_current(root, target_date.isoformat())
    modality_load_rollups = build_modality_load_rollups(root, target_date)
    body_battery_model = build_body_battery_model(root, target_date)
    historical_baselines = build_historical_baselines(root, target_date)
    training_predictor = build_training_predictor(root, target_date)
    overrides = active_modality_overrides(context, target_date.isoformat())

    if wellness_date is None:
        freshness = {"status": "missing", "message": "No Garmin wellness data has been synced."}
    else:
        age = (target_date - wellness_date).days
        freshness = {
            "status": "current" if age <= 0 else "stale",
            "age_days": age,
            "latest_wellness_date": wellness_date.isoformat(),
            "message": "Garmin wellness data is current."
            if age <= 0
            else f"Garmin wellness data is {age} day(s) behind the wall-clock date.",
        }
    latest_activity = training_load.get("latest_activity")
    hard_limiters = []
    if wellness_date is None or freshness.get("status") != "current":
        hard_limiters.append(freshness.get("message"))
    if latest_activity is None:
        activity_freshness = {
            "status": "missing",
            "message": "No Garmin activity data is available to verify recent load.",
        }
        hard_limiters.append(activity_freshness["message"])
    else:
        latest_activity_date = parse_date(latest_activity.get("date"))
        activity_age = (target_date - latest_activity_date).days if latest_activity_date else None
        if activity_age is None:
            activity_freshness = {
                "status": "unknown",
                "message": "Latest activity has no usable date.",
            }
            hard_limiters.append(activity_freshness["message"])
        elif activity_age < 0:
            activity_freshness = {
                "status": "future",
                "age_days": activity_age,
                "message": "Latest activity is dated after the target date and cannot support this plan.",
            }
            hard_limiters.append(activity_freshness["message"])
        elif activity_age > 7:
            activity_freshness = {
                "status": "stale",
                "age_days": activity_age,
                "latest_activity_date": latest_activity_date.isoformat(),
                "message": f"Latest activity is {activity_age} day(s) old.",
            }
            hard_limiters.append(activity_freshness["message"])
        else:
            activity_freshness = {
                "status": "current",
                "age_days": activity_age,
                "latest_activity_date": latest_activity_date.isoformat(),
                "message": "Recent activity data is available.",
            }
    freshness["activity_data"] = activity_freshness
    freshness["hard_session_confidence"] = "normal" if not hard_limiters else "limited"
    freshness["hard_session_limiters"] = [item for item in hard_limiters if item]

    state = {
        "date": target_date.isoformat(),
        "generated_at": iso_now(tz),
        "athlete": context.get("athlete", {}),
        "goal": context.get("athlete", {}).get("goal"),
        "clearance": clearance,
        "phase": phase,
        "readiness": readiness,
        "data_freshness": freshness,
        "training_load": training_load,
        "wellness_trends": wellness_trends,
        "activity_profile": activity_profile,
        "training_status_current": training_status_current,
        "modality_load_rollups": modality_load_rollups,
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
        "injury_return": injury_return,
        "active_modality_overrides": overrides,
        "latest_activity": training_load.get("latest_activity"),
        "evidence_sources": {
            "wellness_date": wellness_date.isoformat() if wellness_date else None,
            "training_status_date": (
                training_status_date.isoformat() if training_status_date else None
            ),
            "wellness_available": wellness is not None,
            "training_status_available": training_status is not None,
            "activity_count": len(activities),
        },
    }
    write_json(snapshots_dir(root) / "current_state.json", state)
    return state
