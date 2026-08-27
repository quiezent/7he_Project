from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from .evidence import as_number
from .garmin_arbitration import build_garmin_arbitration
from .io import read_json, write_json, write_text
from .paths import snapshots_dir
from .planning import build_today_plan
from .state import build_current_state
from .time_utils import DEFAULT_TIMEZONE, iso_now, parse_date, today_local


def _value(value: Any, fallback: str = "unknown") -> Any:
    return fallback if value is None else value


def _signal(
    name: str,
    status: str,
    value: Any,
    decision_use: str,
    message: str,
) -> dict:
    return {
        "name": name,
        "status": status,
        "value": value,
        "decision_use": decision_use,
        "message": message,
    }


def _compact_modalities(windows: dict) -> dict:
    compact = {}
    for window_name in ("last_7_days", "last_28_days"):
        window = windows.get(window_name) or {}
        compact[window_name] = {
            modality: {
                "sessions": row.get("sessions"),
                "duration_min": row.get("duration_min"),
                "training_load": row.get("training_load"),
            }
            for modality, row in sorted(window.items())
        }
    return compact


def _continuity_policy_audit(state: dict, plan: dict) -> dict:
    accountability = state.get("bike_continuity_accountability") or {}
    contract = accountability.get("routine_low_cost_continuity_contract") or {}
    anchor_duration = int(contract.get("total_duration_min") or 60)
    session = plan.get("session") or {}
    modality = str(session.get("modality") or "").lower()
    intensity = str(session.get("intensity") or "").lower()
    density_cost = str(session.get("density_cost") or "").lower()
    is_low_cost_indoor_bike = modality in {"bike_indoor", "indoor_cycling"} and (
        density_cost == "low" or intensity in {"easy", "recovery"}
    )
    planned_duration = as_number(session.get("duration_min"))
    if planned_duration is None:
        duration_range = session.get("duration_range_min") or []
        if isinstance(duration_range, (list, tuple)) and duration_range:
            planned_duration = as_number(max(duration_range))

    applied = (plan.get("constraint_resolution") or {}).get("applied") or []
    constraint_sources = sorted(
        {
            str(item.get("source"))
            for item in applied
            if isinstance(item, dict) and item.get("source")
        }
    )
    named_exception = session.get("dose_exception_reason") or session.get(
        "policy_exception"
    )
    if not is_low_cost_indoor_bike:
        status = "not_applicable"
    elif planned_duration is None:
        status = "unknown_planned_duration"
    elif planned_duration >= anchor_duration:
        status = "aligned_with_established_anchor"
    elif constraint_sources or named_exception:
        status = "below_anchor_with_named_constraint"
    else:
        status = "below_anchor_without_named_constraint"
    return {
        "status": status,
        "routine_anchor_duration_min": anchor_duration,
        "planned_duration_min": planned_duration,
        "planned_session_title": session.get("title"),
        "plan_source": plan.get("plan_source"),
        "named_constraint_sources": constraint_sources,
        "named_exception": named_exception,
        "rule": (
            "A routine low-cost indoor bike dose below the established 60-minute anchor needs a "
            "specific readiness, CNS, symptom, environmental or calendar reason. Protecting a "
            "hypothetical later session is not sufficient by itself."
        ),
    }


def _compact_loop_comparison(comparison: dict) -> dict:
    fields = ("loop", "moving_min", "rest_min", "average_hr", "estimated_load")
    return {
        "kind": comparison.get("kind"),
        "samples": comparison.get("samples"),
        "first": {key: (comparison.get("first") or {}).get(key) for key in fields},
        "final": {key: (comparison.get("final") or {}).get(key) for key in fields},
        "change_final_minus_first": {
            key: (comparison.get("change_final_minus_first") or {}).get(key)
            for key in fields
            if key != "loop"
        },
    }


def _compact_loop_evidence(loop: dict | None) -> dict | None:
    if not isinstance(loop, dict) or not loop:
        return None
    return {
        "activity_id": loop.get("activity_id"),
        "date": loop.get("date"),
        "age_days_at_target": loop.get("age_days_at_target"),
        "matches_latest_session": loop.get("matches_latest_session"),
        "manual_loop_groups": loop.get("manual_loop_groups"),
        "official_activity_training_load": loop.get("official_activity_training_load"),
        "moving_min": loop.get("moving_min"),
        "rest_min": loop.get("rest_min"),
        "first_vs_final": [
            _compact_loop_comparison(item)
            for item in loop.get("first_vs_final") or []
            if isinstance(item, dict)
        ],
        "load_method": loop.get("load_method"),
        "interpretation_guardrail": loop.get("interpretation_guardrail"),
        "weather": loop.get("weather"),
        "source": loop.get("source"),
    }


def _compact_trace_timing(trace: dict | None) -> dict | None:
    if not isinstance(trace, dict):
        return None
    return {
        key: trace.get(key)
        for key in (
            "moving_min",
            "stopped_min",
            "nonmoving_or_stopped_estimate_min",
            "unknown_min",
            "sample_count",
            "coverage_ratio",
            "max_sample_gap_sec",
            "method",
            "interpretation_guardrail",
        )
    }


def _compact_session_timing(timing: dict | None) -> dict | None:
    if not isinstance(timing, dict):
        return None
    reported = timing.get("garmin_reported") or {}
    plausibility = timing.get("plausibility") or {}
    source_fields = reported.get("source_fields") or {}
    return {
        "elapsed_min": timing.get("elapsed_min"),
        "moving_min": timing.get("moving_min"),
        "stopped_min": timing.get("stopped_min"),
        "nonmoving_or_stopped_estimate_min": timing.get(
            "nonmoving_or_stopped_estimate_min"
        ),
        "stopped_derivation": timing.get("stopped_derivation"),
        "stopped_interpretation": timing.get("stopped_interpretation"),
        "garmin_reported": {
            "elapsed_min": reported.get("elapsed_min"),
            "moving_min": reported.get("moving_min"),
            "implied_stopped_min": reported.get("implied_stopped_min"),
            "source_fields": {
                "elapsed": source_fields.get("elapsed"),
                "moving": source_fields.get("moving"),
            },
        },
        "plausibility": {
            "status": plausibility.get("status"),
            "reason": plausibility.get("reason"),
            "trace": _compact_trace_timing(plausibility.get("trace")),
        },
    }


def _compact_hike_phase(phase: dict | None) -> dict | None:
    if not isinstance(phase, dict):
        return None
    return {
        key: phase.get(key)
        for key in (
            "duration_min",
            "sample_count",
            "start_elevation_m",
            "end_elevation_m",
            "min_elevation_m",
            "max_elevation_m",
            "average_hr_bpm",
            "max_hr_bpm",
        )
    }


def _compact_hike_phase_summary(summary: dict | None) -> dict | None:
    if not isinstance(summary, dict) or not summary:
        return None
    top_band = summary.get("top_band") or {}
    phases = summary.get("phases") or {}
    return {
        "status": summary.get("status"),
        "trace_sample_count": summary.get("trace_sample_count"),
        "sample_count": summary.get("sample_count"),
        "valid_hr_elevation_sample_ratio": summary.get(
            "valid_hr_elevation_sample_ratio"
        ),
        "max_valid_sample_gap_sec": summary.get("max_valid_sample_gap_sec"),
        "trace_min_elevation_m": summary.get("trace_min_elevation_m"),
        "trace_max_elevation_m": summary.get("trace_max_elevation_m"),
        "top_band": {
            key: top_band.get(key)
            for key in (
                "floor_elevation_m",
                "rule",
                "first_entry_offset_min",
                "last_exit_offset_min",
            )
        },
        "phases": {
            key: _compact_hike_phase(phases.get(key))
            for key in (
                "ascent_to_first_top_band_entry",
                "top_band_dwell",
                "descent_after_last_top_band_exit",
            )
        },
        "derivation": summary.get("derivation"),
        "interpretation_guardrail": summary.get("interpretation_guardrail"),
    }


def _compact_standard_fit_sources(sources: dict | None) -> dict | None:
    if not isinstance(sources, dict) or not sources:
        return None
    creator = sources.get("creator") or {}
    return {
        "status": sources.get("status"),
        "device_info_rows": sources.get("device_info_rows"),
        "source_types": sources.get("source_types"),
        "external_device_source_present": sources.get(
            "external_device_source_present"
        ),
        "local_or_onboard_source_present": sources.get(
            "local_or_onboard_source_present"
        ),
        "local_or_onboard_only": sources.get("local_or_onboard_only"),
        "creator": {
            "manufacturer": creator.get("manufacturer"),
            "product": creator.get("product"),
            "software_version": creator.get("software_version"),
            "source_type": creator.get("source_type"),
        },
        "interpretation_guardrail": sources.get("interpretation_guardrail"),
    }


def _compact_latest_session(evidence: dict) -> dict:
    power = evidence.get("power") or {}
    environment = evidence.get("environment") or {}
    device = evidence.get("device") or {}
    return {
        "activity": evidence.get("activity"),
        "timing": _compact_session_timing(evidence.get("timing")),
        "terrain": evidence.get("terrain"),
        "workload": evidence.get("workload"),
        "heart_rate": evidence.get("heart_rate"),
        "cadence": evidence.get("cadence"),
        "power": {
            key: power.get(key)
            for key in (
                "average_w",
                "normalized_w",
                "max_w",
                "intensity_factor",
                "max_20_min_w",
                "selected_best_average_w",
                "interpretation_guardrail",
            )
        }
        if power
        else None,
        "environment": {
            "device_temperature": environment.get("device_temperature"),
            "weather": environment.get("weather"),
            "garmin_estimated_water_loss": environment.get("garmin_estimated_water_loss"),
        },
        "technical_context": evidence.get("technical_context"),
        "gear": evidence.get("gear"),
        "hr_source": {
            "status": device.get("status"),
            "hr_confidence": device.get("hr_confidence"),
            "external_hr_sensor": device.get("external_hr_sensor"),
            "local_or_onboard_hr_sensor": device.get("local_or_onboard_hr_sensor"),
            "hr_source_classification": device.get("hr_source_classification"),
            "sensor_types": device.get("sensor_types"),
            "standard_fit_device_sources": _compact_standard_fit_sources(
                device.get("standard_fit_device_sources")
            ),
            "interpretation_guardrail": device.get("interpretation_guardrail"),
        },
        "self_evaluation": evidence.get("self_evaluation"),
        "gym": evidence.get("gym"),
        "matching_loop_analysis": _compact_loop_evidence(evidence.get("loop_analysis")),
        "recent_loop_analysis": _compact_loop_evidence(evidence.get("recent_loop_analysis")),
        "hike_phase_summary": _compact_hike_phase_summary(
            evidence.get("hike_phase_summary")
        ),
        "confidence": evidence.get("confidence"),
        "provenance": evidence.get("provenance"),
    }


def _coach_confidence(state: dict) -> str:
    readiness = state.get("readiness", {})
    freshness = state.get("data_freshness", {})
    cns = state.get("cns_readiness") or {}
    if readiness.get("readiness_level") == "red":
        return "high_for_downshift"
    if cns.get("status") in {"impaired", "compromised"}:
        return "high_for_downshift"
    if freshness.get("status") != "current":
        return "limited"
    if freshness.get("activity_data", {}).get("status") not in {"current", None}:
        return "limited"
    return readiness.get("confidence") or "medium"


def _training_predictor_use(training_predictor: dict) -> tuple[str, str]:
    if (training_predictor.get("wearable_coverage_cache_guard") or {}).get("applied"):
        return (
            "ignored_coverage_superseded",
            "Fresh wear-state coverage superseded the cached same-day low-stress prediction; rebuild before use.",
        )
    validation = training_predictor.get("validation") or {}
    utility = validation.get("utility")
    status = validation.get("status")
    if utility == "useful":
        return "supporting_signal", "Validation beats the majority baseline enough to support coaching judgment."
    if status == "insufficient_samples":
        return "ignored_until_validated", "Insufficient samples for a useful holdout validation."
    return (
        "experimental_caution_only",
        "Validation does not beat a simple baseline, so this cannot steer the plan.",
    )


def _body_battery_model_use(model: dict) -> tuple[str, str]:
    if (model.get("wearable_coverage_cache_guard") or {}).get("applied"):
        return (
            "ignored_coverage_superseded",
            "Fresh wear-state coverage superseded the cached same-day low-stress prediction; rebuild before use.",
        )
    samples = model.get("samples") or 0
    accuracy = model.get("leave_one_out_accuracy")
    if samples >= 30 and accuracy is not None and accuracy >= 0.7:
        return "interpretable_support", "Enough recent samples to explain likely wake Body Battery patterns."
    return "exploratory_only", "Useful for inspection, but not enough validated signal to steer training."


def _sleep_opportunity_signal(state: dict) -> dict:
    trends = state.get("wellness_trends") or {}
    latest = trends.get("latest") or {}
    last_7 = trends.get("last_7") or {}
    primary_sleep = as_number(
        latest.get("primary_sleep_hours")
        if latest.get("primary_sleep_hours") is not None
        else latest.get("sleep_hours")
    )
    avg_7d = as_number(last_7.get("avg_primary_sleep_hours"))
    if avg_7d is None:
        avg_7d = as_number(last_7.get("avg_sleep_hours"))
    if primary_sleep is None:
        status = "unknown"
    elif primary_sleep < 5:
        status = "critical_short_primary_sleep"
    elif primary_sleep < 6 or (avg_7d is not None and avg_7d < 6):
        status = "sleep_incomplete"
    elif primary_sleep < 6.5:
        status = "watch"
    else:
        status = "adequate_primary_sleep"
    return _signal(
        "Sleep opportunity",
        status,
        {
            "sleep_start_local": latest.get("sleep_start_local"),
            "sleep_end_local": latest.get("sleep_end_local"),
            "sleep_window_hours": latest.get("sleep_window_hours"),
            "primary_sleep_hours": primary_sleep,
            "nap_hours_reported": latest.get("nap_hours_reported"),
            "total_sleep_hours_reported": latest.get("total_sleep_hours_reported"),
            "avg_primary_sleep_hours_7d": avg_7d,
            "nap_capture_limit": (
                "Garmin-reported zero is not proof that no nap occurred; use structured completed-nap input."
            ),
        },
        "independent_sleep_and_cognitive_ceiling",
        (
            "Evaluate primary sleep duration independently from Garmin sleep score, HRV, and Body Battery. "
            "A completed nap is separate intraday evidence and does not rewrite the morning anchor."
        ),
    )


def _rest_recharge_signal(state: dict) -> dict:
    artifact = state.get("rest_recharge_window") or {}
    classification = artifact.get("classification") or {}
    objective = artifact.get("objective_response") or {}
    stress = (objective.get("stress") or {}).get("during") or {}
    body_battery = objective.get("body_battery") or {}
    window = artifact.get("window") or {}
    safety = artifact.get("safety_contract") or {}
    label = classification.get("label") or "insufficient_evidence"
    return _signal(
        "Rest/recharge window",
        label,
        {
            "artifact_status": artifact.get("status"),
            "classification": label,
            "confidence": classification.get("confidence"),
            "classification_signals": classification.get("signals"),
            "window": {
                "type": window.get("type"),
                "occurrence_source": window.get("occurrence_source"),
                "start_local": window.get("start_local"),
                "end_local": window.get("end_local"),
                "time_in_bed_minutes": window.get("time_in_bed_minutes"),
                "estimated_sleep_minutes": window.get("estimated_sleep_minutes"),
                "sleep_inertia_minutes": window.get("sleep_inertia_minutes"),
                "post_clarity_low_10": window.get("post_clarity_low_10"),
                "post_clarity_high_10": window.get("post_clarity_high_10"),
            },
            "autonomic_response": {
                "during_mean_stress": stress.get("mean"),
                "during_rest_range_pct_le_25": stress.get("rest_range_pct_le_25"),
                "during_stress_coverage_pct": stress.get("coverage_pct"),
                "body_battery_delta_during": body_battery.get("delta_during"),
                "recharge_onset": body_battery.get("recharge_onset"),
            },
            "primary_sleep_context": (artifact.get("context") or {}).get(
                "shortfall_to_architecture_threshold"
            ),
            "rolling_sleep_shortfall_proxy": (artifact.get("context") or {}).get(
                "sleep_opportunity_debt_proxy"
            ),
            "illness": (artifact.get("context") or {}).get("illness"),
            "preceding_48h_load": (artifact.get("context") or {}).get(
                "preceding_48h_load"
            ),
            "garmin_nap_evidence": artifact.get("garmin_nap_evidence"),
            "safety_contract": safety,
            "provenance": artifact.get("provenance"),
        },
        "intraday_recovery_context_only_never_session_clearance",
        (
            "This describes whether a reported rest window worked. It cannot raise physical, CNS, "
            "or technical ceilings; erase primary-sleep or illness constraints; change the written "
            "session; or authorize high-consequence work."
        ),
    )


def _wearable_coverage_signal(state: dict) -> dict:
    artifact = state.get("wearable_coverage") or {}
    classification = artifact.get("classification") or {}
    observed = artifact.get("observed_coverage") or {}
    stress = observed.get("stress") or {}
    battery = observed.get("body_battery") or {}
    optical_hr = observed.get("optical_heart_rate") or {}
    decision = artifact.get("decision_use") or {}
    return _signal(
        "Wear-state coverage",
        classification.get("label") or "insufficient_evidence",
        {
            "classification": classification.get("label"),
            "confidence": classification.get("confidence"),
            "stress_series": {
                "sample_count": stress.get("sample_count"),
                "valid_sample_count": stress.get("valid_sample_count"),
                "sentinel_sample_count": stress.get("sentinel_sample_count"),
                "malformed_sample_count": stress.get("malformed_sample_count"),
                "discarded_outside_target_date_count": stress.get(
                    "discarded_outside_target_date_count"
                ),
                "discarded_after_cutoff_count": stress.get(
                    "discarded_after_cutoff_count"
                ),
                "cadence_seconds": stress.get("cadence_seconds"),
                "first_sample_local": stress.get("first_sample_local"),
                "last_sample_local": stress.get("last_sample_local"),
                "material_unavailable_run_count": stress.get(
                    "material_unavailable_run_count"
                ),
                "material_unavailable_minutes": stress.get(
                    "material_unavailable_minutes"
                ),
                "material_unavailable_runs": stress.get("material_unavailable_runs"),
                "positive_use_coverage": stress.get("positive_use_coverage"),
            },
            "body_battery_series": {
                "valid_level_sample_count": battery.get("valid_level_sample_count"),
                "missing_level_sample_count": battery.get("missing_level_sample_count"),
                "material_unavailable_minutes": battery.get(
                    "material_unavailable_minutes"
                ),
            },
            "optical_heart_rate_series": {
                "endpoint_status": optical_hr.get("endpoint_status"),
                "endpoint_usable": optical_hr.get("endpoint_usable"),
                "sample_count": optical_hr.get("sample_count"),
                "measured_sample_count": optical_hr.get("measured_sample_count"),
                "unavailable_sample_count": optical_hr.get(
                    "unavailable_sample_count"
                ),
                "cadence_seconds": optical_hr.get("cadence_seconds"),
                "first_sample_local": optical_hr.get("first_sample_local"),
                "last_sample_local": optical_hr.get("last_sample_local"),
                "endpoint_start_local": optical_hr.get("endpoint_start_local"),
                "endpoint_cutoff_local": optical_hr.get("endpoint_cutoff_local"),
                "material_unavailable_run_count": optical_hr.get(
                    "material_unavailable_run_count"
                ),
                "material_unavailable_minutes": optical_hr.get(
                    "material_unavailable_minutes"
                ),
                "material_unavailable_runs": optical_hr.get(
                    "material_unavailable_runs"
                ),
            },
            "athlete_reported_windows": artifact.get("athlete_reported_windows"),
            "recurring_context": artifact.get("recurring_context"),
            "sensor_alignment": artifact.get("sensor_alignment"),
            "material_run_attribution": artifact.get("material_run_attribution"),
            "attribution_summary": artifact.get("attribution_summary"),
            "low_stress_positive_reward_eligible": decision.get(
                "low_stress_positive_reward_eligible"
            ),
            "safety_contract": artifact.get("safety_contract"),
            "provenance": artifact.get("provenance"),
        },
        "coverage_interpretation_only_never_readiness_clearance",
        (
            "Wear-state evidence explains data availability only. Direct wrist-HR gaps may confirm "
            "optical-HR measurement unavailability, but do not prove physical watch removal or its "
            "cause. Missing physiology is never rest, is never imputed, and cannot raise physical "
            "readiness, CNS readiness, technical consequence, or the written session."
        ),
    )


def _oxygenation_respiration_signal(state: dict) -> dict:
    trends = state.get("wellness_trends") or {}
    latest = trends.get("latest") or {}
    last_7 = trends.get("last_7") or {}
    spo2_status = latest.get("spo2_endpoint_status") or "not_attempted"
    respiration_status = latest.get("respiration_endpoint_status") or "not_attempted"
    spo2_available = (
        spo2_status == "success" and latest.get("spo2_freshness") == "target_date"
    )
    respiration_available = (
        respiration_status == "success"
        and latest.get("respiration_freshness") == "target_date"
    )
    if spo2_available and respiration_available:
        status = "available"
    elif spo2_available or respiration_available:
        status = "partial"
    elif {spo2_status, respiration_status} == {"not_attempted"}:
        status = "not_attempted"
    else:
        status = "unavailable"
    return _signal(
        "Oxygenation and respiration context",
        status,
        {
            "date": latest.get("date"),
            "monitoring_altitude": {
                "value_m": latest.get("monitoring_altitude_m"),
                "source": latest.get("monitoring_altitude_source"),
                "seven_day_average_m": last_7.get("avg_monitoring_altitude_m"),
            },
            "spo2": {
                "endpoint_status": spo2_status,
                "latest_attempt_status": latest.get("spo2_latest_attempt_status"),
                "freshness": latest.get("spo2_freshness"),
                "data_cutoff_local": latest.get("spo2_data_cutoff_local"),
                "daily_average_pct": latest.get("spo2_daily_average_pct"),
                "sleep_average_pct": latest.get("spo2_sleep_average_pct"),
                "lowest_pct": latest.get("spo2_lowest_pct"),
                "hourly_lowest_pct": latest.get("spo2_hourly_lowest_pct"),
                "hourly_highest_pct": latest.get("spo2_hourly_highest_pct"),
                "hourly_aggregate_count": latest.get(
                    "spo2_hourly_aggregate_count"
                ),
                "direct_single_reading_count": latest.get(
                    "spo2_single_reading_count"
                ),
                "continuous_reading_count": latest.get(
                    "spo2_continuous_reading_count"
                ),
                "seven_day_daily_average_pct": last_7.get("avg_spo2_daily_pct"),
                "seven_day_sleep_average_pct": last_7.get("avg_spo2_sleep_pct"),
                "in_activity_interpretation": latest.get(
                    "spo2_in_activity_interpretation"
                ),
                "provenance": latest.get("spo2_provenance"),
            },
            "respiration": {
                "endpoint_status": respiration_status,
                "latest_attempt_status": latest.get(
                    "respiration_latest_attempt_status"
                ),
                "freshness": latest.get("respiration_freshness"),
                "data_cutoff_local": latest.get("respiration_data_cutoff_local"),
                "waking_average_brpm": latest.get(
                    "respiration_waking_average_brpm"
                ),
                "sleep_average_brpm": latest.get(
                    "respiration_sleep_average_brpm"
                ),
                "lowest_brpm": latest.get("respiration_lowest_brpm"),
                "highest_brpm": latest.get("respiration_highest_brpm"),
                "two_min_valid_count": latest.get(
                    "respiration_two_min_valid_count"
                ),
                "two_min_sentinel_count": latest.get(
                    "respiration_two_min_sentinel_count"
                ),
                "exercise_aligned_unavailable_count": latest.get(
                    "respiration_two_min_activity_sentinel_count"
                ),
                "two_min_series_coverage_ratio": latest.get(
                    "respiration_two_min_series_coverage_ratio"
                ),
                "two_min_valid_measurement_ratio": latest.get(
                    "respiration_two_min_valid_measurement_ratio"
                ),
                "seven_day_waking_average_brpm": last_7.get(
                    "avg_respiration_waking_brpm"
                ),
                "seven_day_sleep_average_brpm": last_7.get(
                    "avg_respiration_sleep_brpm"
                ),
                "exercise_interpretation": latest.get(
                    "respiration_exercise_aligned_unavailable_interpretation"
                ),
                "provenance": latest.get("respiration_provenance"),
            },
        },
        "context_only_downshift_or_verify_never_readiness_promotion",
        (
            "Use these wearable summaries to corroborate altitude, symptoms, illness, and trends. "
            "A low value can trigger caution or measurement verification; a normal, high, absent, "
            "or sparse value never raises readiness or the technical-consequence ceiling, and no "
            "daily endpoint value is inferred to have occurred during an activity."
        ),
    )


def _build_trusted_evidence(state: dict, plan: dict, root: str | Path | None = None) -> list[dict]:
    freshness = state.get("data_freshness") or {}
    activity_freshness = freshness.get("activity_data") or {}
    readiness = state.get("readiness") or {}
    cns = state.get("cns_readiness") or {}
    phase = state.get("phase") or {}
    training_status = state.get("training_status_current") or {}
    training_readiness = state.get("training_readiness_current") or {}
    cycling_ftp = state.get("cycling_ftp_current") or {}
    garmin_arbitration = (plan.get("decision_inputs") or {}).get("garmin_arbitration")
    if not isinstance(garmin_arbitration, dict) or not garmin_arbitration:
        garmin_arbitration = build_garmin_arbitration(state)
    rollups = state.get("modality_load_rollups") or {}
    continuity = state.get("bike_continuity_accountability") or {}
    adaptive = state.get("adaptive_training") or {}
    baselines = state.get("historical_baselines") or {}
    gear_audit = state.get("gear_audit") or {}
    device_audit = state.get("device_audit") or {}
    self_evaluation = state.get("self_evaluation") or {}
    latest_session = state.get("latest_session_evidence") or {}
    latest_response = state.get("latest_session_response") or {}
    gear_coverage = (gear_audit.get("coverage") or {}).get("status")
    device_coverage = (device_audit.get("coverage") or {}).get("status")
    gear_status = "flagged" if gear_audit.get("flags") else "clear" if gear_coverage == "complete" else gear_coverage or "unknown"
    device_status = "flagged" if device_audit.get("flags") else "clear" if device_coverage == "complete" else device_coverage or "unknown"
    raw_predictive = read_json(snapshots_dir(root) / "predictive_training.json", {})
    target = parse_date(state.get("date"))
    predictive = (
        raw_predictive
        if target is not None
        and isinstance(raw_predictive, dict)
        and parse_date(raw_predictive.get("date")) == target
        else {}
    )
    predictive_status = (
        "stale"
        if isinstance(raw_predictive, dict) and raw_predictive and not predictive
        else "missing"
    )
    scheduled_rest = (plan.get("decision_inputs") or {}).get("scheduled_rest")
    sabbath_exception = (plan.get("decision_inputs") or {}).get("sabbath_exception")

    trusted = []
    if scheduled_rest and not sabbath_exception:
        trusted.append(
            _signal(
                "Scheduled rest",
                scheduled_rest.get("status") or "active",
                scheduled_rest,
                "hard_daily_constraint",
                scheduled_rest.get("reason") or "Scheduled rest day is active.",
            )
        )
    elif sabbath_exception:
        if sabbath_exception.get("exception_type") == "athlete_authorized_race_event":
            event = sabbath_exception.get("event") or {}
            replacement = sabbath_exception.get("replacement_sabbath") or {}
            exception_message = (
                f"Athlete authorization applies only to the named race {event.get('name') or 'event'} "
                f"on this exact date; {replacement.get('date')} is the hard replacement Sabbath. "
                "The recurring Sunday rule is unchanged."
            )
        else:
            exception_message = (
                "Athlete authorization applies only to this date and indoor low-aerobic scope; "
                "the recurring Sunday rule is unchanged."
            )
        trusted.append(
            _signal(
                "One-off Sabbath exception",
                sabbath_exception.get("status") or "validated",
                sabbath_exception,
                "exact_date_scope_constraint",
                exception_message,
            )
        )

    trusted.extend([
        _sleep_opportunity_signal(state),
        _rest_recharge_signal(state),
        _wearable_coverage_signal(state),
        _oxygenation_respiration_signal(state),
        _signal(
            "Garmin wellness freshness",
            freshness.get("status") or "unknown",
            {
                "latest_wellness_date": freshness.get("latest_wellness_date"),
                "age_days": freshness.get("age_days"),
                "intraday_provenance": (freshness.get("wellness_data") or {}).get(
                    "intraday_provenance"
                ),
            },
            "freshness_gate",
            freshness.get("message") or "No Garmin wellness freshness message is available.",
        ),
        _signal(
            "Garmin activity freshness",
            activity_freshness.get("status") or "unknown",
            {
                "latest_activity_date": activity_freshness.get("latest_activity_date"),
                "age_days": activity_freshness.get("age_days"),
            },
            "load_confidence_gate",
            activity_freshness.get("message") or "No Garmin activity freshness message is available.",
        ),
        _signal(
            "Readiness",
            readiness.get("readiness_level") or "unknown",
            {
                "score": readiness.get("readiness_score"),
                "confidence": readiness.get("confidence"),
                "hard_session_guidance": readiness.get("hard_session_guidance"),
            },
            "daily_intensity_ceiling",
            "Use readiness to cap session ambition before adding MTB specificity.",
        ),
        _signal(
            "CNS readiness",
            cns.get("status") or "unknown",
            {
                "score": cns.get("score"),
                "confidence": cns.get("confidence"),
                "session_ceiling": cns.get("session_ceiling"),
                "signals": cns.get("signals"),
            },
            "technical_consequence_ceiling",
            cns.get("interpretation")
            or "Use CNS readiness to cap speed, jumps, enduro simulation, novelty, and technical consequence.",
        ),
        _signal(
            "Current phase",
            phase.get("name") or "unknown",
            {
                "reason": phase.get("reason"),
            },
            "progression_stage",
            "Use the current phase to scale bike specificity, intensity, and durability work.",
        ),
        _signal(
            "Training status",
            training_status.get("training_status_feedback") or "unknown",
            {
                "acwr": (training_status.get("acute_chronic") or {}).get("ratio"),
                "acwr_status": (training_status.get("acute_chronic") or {}).get("status"),
                "load_focus": (training_status.get("load_focus") or {}).get("feedback"),
                "cycling_vo2max": (training_status.get("vo2max") or {}).get("cycling_precise"),
            },
            "load_context_not_daily_command",
            "Use Garmin training status as context, not as an automatic workout prescription.",
        ),
        _signal(
            "Garmin Training Readiness",
            training_readiness.get("status") or "missing",
            {
                "target_date": training_readiness.get("target_date"),
                "source_snapshot_date": training_readiness.get("source_snapshot_date"),
                "freshness": training_readiness.get("freshness"),
                "source_availability_status": training_readiness.get(
                    "source_availability_status"
                ),
                "score": training_readiness.get("score"),
                "level": training_readiness.get("level"),
                "feedback": training_readiness.get("feedback"),
                "factors": training_readiness.get("factors"),
                "source_endpoint": training_readiness.get("source_endpoint"),
                "device_capability": training_readiness.get("device_capability"),
            },
            "context_only_custom_readiness_remains_authoritative",
            (
                "Garmin Training Readiness is a separate contextual feed; it does not replace the "
                "stack's physical-readiness gate or CNS technical-consequence ceiling."
            ),
        ),
        _signal(
            "Garmin cycling FTP",
            cycling_ftp.get("status") or "missing",
            {
                "ftp_w": cycling_ftp.get("ftp_w"),
                "effective_date": cycling_ftp.get("effective_date"),
                "effective_at": cycling_ftp.get("effective_at"),
                "age_days": cycling_ftp.get("age_days"),
                "sport": cycling_ftp.get("sport"),
                "biometric_source_type": cycling_ftp.get("biometric_source_type"),
                "detection_source": cycling_ftp.get("detection_source"),
                "confidence": cycling_ftp.get("confidence"),
                "source_endpoint": cycling_ftp.get("source_endpoint"),
                "latest_attempt": cycling_ftp.get("latest_attempt"),
            },
            "ftp_relative_power_anchor_with_rpe_hr_validation",
            cycling_ftp.get("decision_use")
            or "No current Garmin operational FTP is available.",
        ),
        _signal(
            "Garmin diagnosis arbitration",
            garmin_arbitration.get("recommended_action") or "unknown",
            {
                "ceiling": garmin_arbitration.get("ceiling"),
                "stimulus": garmin_arbitration.get("stimulus"),
                "confidence": garmin_arbitration.get("confidence"),
                "acwr": garmin_arbitration.get("acwr"),
                "load_focus": garmin_arbitration.get("load_focus"),
                "allowed_stimulus": garmin_arbitration.get("allowed_stimulus"),
                "avoid": garmin_arbitration.get("avoid"),
                "reasons": garmin_arbitration.get("reasons"),
            },
            "session_ceiling_arbitration",
            garmin_arbitration.get("summary")
            or "Use Garmin diagnosis as a bounded session-ceiling signal.",
        ),
        _signal(
            "Modality load rollups",
            "available" if rollups.get("windows") else "missing",
            _compact_modalities(rollups.get("windows") or {}),
            "specificity_context",
            "Recent load distribution shows what the current fitness is actually built from.",
        ),
        _signal(
            "Build accountability",
            continuity.get("status") or "missing",
            {
                "targets": continuity.get("targets"),
                "current_calendar_week": continuity.get("current_calendar_week"),
                "rolling_last_7_days": continuity.get("rolling_last_7_days"),
                "previous_7_days": continuity.get("previous_7_days"),
                "preferred_gap_unique_days": continuity.get(
                    "preferred_gap_unique_days"
                ),
                "remaining_non_rest_calendar_dates": continuity.get(
                    "remaining_non_rest_calendar_dates"
                ),
                "routine_low_cost_continuity_contract": continuity.get(
                    "routine_low_cost_continuity_contract"
                ),
                "progressive_overload_guardrail": continuity.get(
                    "progressive_overload_guardrail"
                ),
                "weekly_frequency_audit_rule": continuity.get(
                    "weekly_frequency_audit_rule"
                ),
                "policy_alignment": _continuity_policy_audit(state, plan),
                "provenance": continuity.get("provenance"),
            },
            "weekly_frequency_and_dose_drift_audit_not_training_clearance",
            continuity.get("decision_use")
            or "No live bike-continuity accountability evidence is available.",
        ),
        _signal(
            "Adaptive training controller",
            adaptive.get("status") or "missing",
            {
                "roadmap_block": adaptive.get("roadmap_block"),
                "progression_decision": adaptive.get("progression_decision"),
                "weekly_budget": adaptive.get("weekly_budget"),
                "progression_tracks": adaptive.get("progression_tracks"),
                "trainable_limiter_ranking": adaptive.get("trainable_limiter_ranking"),
                "recommended_week_roles": adaptive.get("recommended_week_roles"),
                "programming_audit": adaptive.get("programming_audit"),
            }
            if adaptive
            else None,
            "persistent_adaptation_programming_not_same_day_clearance",
            (
                "Use this surface to select the adaptation, progression rung and remaining weekly role budget. "
                "The same-day safety resolver and head-coach judgment still determine whether the candidate is executable."
                if adaptive
                else "No adaptive programming state is available; hold progression and use the canonical template."
            ),
        ),
        _signal(
            "Historical MTB baseline",
            "available" if baselines.get("pre_injury_mtb_baseline") else "missing",
            baselines.get("pre_injury_mtb_baseline"),
            "long_range_target_context",
            "Historical MTB volume is useful for direction, not an immediate target.",
        ),
        _signal(
            "Activity gear audit",
            gear_status,
            {
                "checked_activities": gear_audit.get("checked_activities"),
                "mtb_checked": gear_audit.get("mtb_checked"),
                "coverage": gear_audit.get("coverage"),
                "recent_mtb_gear": gear_audit.get("recent_mtb_gear"),
            },
            "power_source_and_bike_context",
            "Use Garmin Gear to distinguish Stumpjumper, Enduro, hardtail, and trainer-related activity context.",
        ),
        _signal(
            "Activity device audit",
            device_status,
            {
                "checked_activities": device_audit.get("checked_activities"),
                "mtb_checked": device_audit.get("mtb_checked"),
                "coverage": device_audit.get("coverage"),
                "recent_mtb_devices": device_audit.get("recent_mtb_devices"),
            },
            "heart_rate_source_confidence",
            "Use Devices & Apps to distinguish chest-strap HR from wrist optical HR before trusting trail HR/load.",
        ),
        _signal(
            "Post-activity self evaluation",
            "available" if self_evaluation.get("evaluated_activities") else "missing",
            {
                "checked_activities": self_evaluation.get("checked_activities"),
                "evaluated_activities": self_evaluation.get("evaluated_activities"),
                "recent_self_evaluations": self_evaluation.get("recent_self_evaluations"),
            },
            "subjective_session_response",
            "Use Garmin self-evaluation feel and RPE to interpret whether load was costly, sustainable, or misleading.",
        ),
        _signal(
            "Latest session evidence",
            (latest_session.get("confidence") or {}).get("status")
            or latest_session.get("status")
            or "missing",
            _compact_latest_session(latest_session) if latest_session.get("status") == "available" else None,
            "post_session_coaching_context",
            (
                "Use the bounded raw-session, metadata, environment, gym, and loop evidence to interpret "
                "what the latest load actually represented; respect each field's confidence and guardrails."
                if latest_session.get("status") == "available"
                else "No bounded raw latest-session evidence is available."
            ),
        ),
        _signal(
            "Structured latest-session response",
            latest_response.get("status") or "missing",
            latest_response,
            "session_response_interpretation_not_automatic_clearance",
            ((latest_response.get("decision_use") or {}).get("guardrail"))
            or "No structured target-date session response is available.",
        ),
        _signal(
            "Predictive training twin",
            (
                predictive.get("today_prescription", {})
                .get("model_confidence", {})
                .get("status")
                or predictive_status
            ),
            {
                "today_prediction": (
                    predictive.get("today_prescription", {}).get("prediction") if predictive else None
                ),
                "latest_review": (
                    predictive.get("latest_review", {}).get("comparison") if predictive else None
                ),
                "latest_learning_disposition": (
                    (
                        predictive.get("latest_review", {}).get("comparison")
                        or {}
                    ).get("learning_disposition")
                    if predictive
                    else None
                ),
            },
            "pre_session_expectation_and_post_session_multi_channel_learning",
            (
                "Store an expected response before training, then separate nominal-contract validation, "
                "delivered-action response, execution-boundary learning, and safety-adherence learning. "
                "A stop-rule override can inform the latter lanes but can never validate the nominal dose."
            ),
        ),
    ])
    return trusted


def _build_cautions(state: dict, plan: dict | None = None) -> list[dict]:
    cautions = []
    adaptive = state.get("adaptive_training") or {}
    adaptive_audit = adaptive.get("programming_audit") or {}
    adaptive_items = adaptive_audit.get("items") or []
    if adaptive_items:
        cautions.append(
            {
                "source": "adaptive_training",
                "type": "adaptive_programming_attention",
                "severity": "yellow",
                "message": (
                    "Adaptive programming needs attention: "
                    + "; ".join(str(item.get("type") or "unknown") for item in adaptive_items)
                    + ". Preserve justified constraints, but do not let them erase an underdose or density breach."
                ),
            }
        )
    torque = ((adaptive.get("progression_tracks") or {}).get("engine") or {}).get("torque") or {}
    if torque.get("decision") == "hold_no_promotion":
        cautions.append(
            {
                "source": "adaptive_training",
                "type": "engine_progression_blocked_by_stop_override",
                "severity": "yellow",
                "message": torque.get("block_reason")
                or "The latest structured-engine outcome blocks progression.",
            }
        )
    if plan is not None:
        adaptive_plan = plan.get("adaptive_programming") or {}
        conflicts = adaptive_plan.get("explicit_contract_conflicts") or []
        if conflicts:
            cautions.append(
                {
                    "source": "adaptive_training",
                    "type": "explicit_contract_budget_conflict",
                    "severity": "yellow",
                    "message": (
                        f"{len(conflicts)} explicit future contract(s) exceed the remaining adaptive cost budget. "
                        "They were preserved for head-coach resolution, not silently rewritten."
                    ),
                }
            )
        policy_audit = _continuity_policy_audit(state, plan)
        if policy_audit.get("status") == "below_anchor_without_named_constraint":
            cautions.append(
                {
                    "source": "build_accountability",
                    "type": "low_cost_dose_below_established_anchor_without_named_constraint",
                    "severity": "yellow",
                    "message": (
                        f"The written low-cost indoor dose is {policy_audit.get('planned_duration_min'):g} "
                        f"minutes versus the established {policy_audit.get('routine_anchor_duration_min')} "
                        "minute anchor, with no named hard constraint. Treat this as coaching-policy "
                        "drift; preserve explicit-plan provenance but do not hide the divergence."
                    ),
                }
            )
    readiness = state.get("readiness") or {}
    for reason in readiness.get("reasons") or []:
        cautions.append(
            {
                "source": "readiness",
                "type": reason.get("type"),
                "severity": reason.get("severity"),
                "message": reason.get("message"),
            }
        )
    for limiter in (state.get("data_freshness") or {}).get("hard_session_limiters") or []:
        cautions.append(
            {
                "source": "data_freshness",
                "type": "hard_session_limiter",
                "severity": "yellow",
                "message": limiter,
            }
        )
    for flag in (state.get("cns_readiness") or {}).get("flags") or []:
        cautions.append(
            {
                "source": "cns_readiness",
                "type": flag.get("type"),
                "severity": flag.get("severity") or "yellow",
                "message": flag.get("message"),
            }
        )
    for flag in (state.get("training_status_current") or {}).get("flags") or []:
        cautions.append(
            {
                "source": "training_status",
                "type": flag.get("type"),
                "severity": "yellow",
                "message": flag.get("message"),
            }
        )
    for flag in (state.get("training_readiness_current") or {}).get("flags") or []:
        cautions.append(
            {
                "source": "training_readiness",
                "type": flag.get("type"),
                "severity": flag.get("severity") or "yellow",
                "message": flag.get("message"),
            }
        )
    for flag in (state.get("gear_audit") or {}).get("flags") or []:
        cautions.append(
            {
                "source": "gear_audit",
                "type": flag.get("type"),
                "severity": flag.get("severity") or "yellow",
                "message": flag.get("message"),
            }
        )
    for flag in (state.get("device_audit") or {}).get("flags") or []:
        cautions.append(
            {
                "source": "device_audit",
                "type": flag.get("type"),
                "severity": flag.get("severity") or "yellow",
                "message": flag.get("message"),
            }
        )
    for source_name, report in (
        ("gear_audit", state.get("gear_audit") or {}),
        ("device_audit", state.get("device_audit") or {}),
        ("self_evaluation", state.get("self_evaluation") or {}),
    ):
        coverage = report.get("coverage") if isinstance(report.get("coverage"), dict) else {}
        if coverage.get("status") in {"partial", "missing"}:
            cautions.append(
                {
                    "source": source_name,
                    "type": f"{source_name}_coverage_{coverage.get('status')}",
                    "severity": "yellow",
                    "message": (
                        f"{source_name.replace('_', ' ').title()} coverage is {coverage.get('status')}: "
                        f"{coverage.get('indexed_activities', 0)}/{coverage.get('eligible_activities', 0)} "
                        "eligible recent activities are indexed. Treat uncovered sessions as unknown."
                    ),
                }
            )
    for flag in (state.get("latest_session_evidence") or {}).get("cautions") or []:
        cautions.append(
            {
                "source": "latest_session_evidence",
                "type": flag.get("type"),
                "severity": flag.get("severity") or "yellow",
                "message": flag.get("message"),
            }
        )
    latest_response = state.get("latest_session_response") or {}
    response_decision = latest_response.get("decision_use") or {}
    response_classification = response_decision.get("classification")
    stop_outcome = latest_response.get("stop_rule_outcome")
    if response_classification == "stop_or_downshift_signal":
        cautions.append(
            {
                "source": "latest_session_response",
                "type": "structured_symptom_stop_or_downshift_signal",
                "severity": "yellow",
                "message": (
                    "Structured post-session feedback contains a sharp/focal, asymmetric, "
                    "mechanically altering, or persistent symptom signal. Review it before the "
                    "next dose; do not promote training from Garmin recovery alone."
                ),
            }
        )
    if stop_outcome == "triggered_but_continued":
        cautions.append(
            {
                "source": "latest_session_response",
                "type": "explicit_stop_rule_override",
                "severity": "yellow",
                "message": (
                    "The latest structured review explicitly records triggered_but_continued. "
                    "Nominal-contract validation is rejected and the next call must preserve "
                    "execution-boundary and safety-adherence learning."
                ),
            }
        )
    rest_recharge = state.get("rest_recharge_window") or {}
    rest_classification = (rest_recharge.get("classification") or {}).get("label")
    if rest_classification in {"discordant", "quiet_but_non_restorative"}:
        cautions.append(
            {
                "source": "rest_recharge_window",
                "type": rest_classification,
                "severity": "yellow",
                "message": (
                    "The reported rest window did not produce a fully concordant recovery response. "
                    "Use it only to hold or downshift the existing plan; never as upward clearance."
                ),
            }
        )
    rest_signals = (rest_recharge.get("classification") or {}).get("signals") or {}
    if (
        rest_signals.get("subjective_negative") is True
        and rest_classification not in {"discordant", "quiet_but_non_restorative"}
    ):
        cautions.append(
            {
                "source": "rest_recharge_window",
                "type": "poor_post_rest_cognition",
                "severity": "yellow",
                "message": (
                    "Athlete-reported post-rest clarity or inertia remains impaired even though "
                    "objective coverage is incomplete. Treat this as a downward CNS gate, never as "
                    "missing evidence that permits hard or high-consequence work."
                ),
            }
        )
    illness_status = ((rest_recharge.get("context") or {}).get("illness") or {}).get(
        "status"
    )
    if illness_status in {"suspected", "active", "recovering"}:
        cautions.append(
            {
                "source": "rest_recharge_window",
                "type": f"illness_{illness_status}",
                "severity": "red" if illness_status == "active" else "yellow",
                "message": (
                    f"Structured rest-window context reports illness status '{illness_status}'. "
                    "A restorative autonomic response cannot erase illness or authorize hard or "
                    "high-consequence work; the head coach must reassess the written call."
                ),
            }
        )
    wearable = state.get("wearable_coverage") or {}
    wearable_label = (wearable.get("classification") or {}).get("label")
    stress_coverage = ((wearable.get("observed_coverage") or {}).get("stress") or {})
    unavailable_minutes = stress_coverage.get("material_unavailable_minutes")
    if unavailable_minutes:
        contact_corroborated = wearable_label in {
            "confirmed_optical_hr_measurement_unavailability",
            "partially_confirmed_optical_hr_with_unexplained_internal_unavailability",
        }
        partially_corroborated = wearable_label == (
            "partially_confirmed_optical_hr_with_unexplained_internal_unavailability"
        )
        cautions.append(
            {
                "source": "wearable_coverage",
                "type": wearable_label or "material_internal_unavailability",
                "severity": "yellow",
                "message": (
                    f"Garmin all-day stress contains {unavailable_minutes:g} minute(s) of material "
                    "internal unavailability. Low average stress receives no positive credit; "
                    "high observed stress may still downshift. "
                    + (
                        (
                            "Direct wrist-HR transitions corroborate portions of the stress "
                            "gaps, while wrist HR continued through the remaining portions or "
                            "runs. Do not classify those unmatched periods as optical-contact "
                            "loss."
                            if partially_corroborated
                            else "Direct wrist-HR transitions corroborate measurement unavailability, but "
                            "physical watch removal and the specific cause remain unassigned."
                        )
                        if contact_corroborated
                        else "Missing samples do not prove optical-contact loss or its cause."
                    )
                ),
            }
        )
    elif wearable_label in {"insufficient_series_coverage", "endpoint_unavailable"}:
        positive_use = stress_coverage.get("positive_use_coverage") or {}
        cautions.append(
            {
                "source": "wearable_coverage",
                "type": wearable_label,
                "severity": "yellow",
                "message": (
                    "Garmin all-day stress lacks a usable dense target-date series through the "
                    "decision-day cutoff. Low average stress receives no positive credit; high "
                    "observed stress may still downshift."
                ),
                "coverage": positive_use,
            }
        )
    if wearable_label == "discordant":
        cautions.append(
            {
                "source": "wearable_coverage",
                "type": "reported_window_sensor_discordance",
                "severity": "yellow",
                "message": (
                    "Valid Garmin samples overlap an athlete-reported off-wrist window. Preserve "
                    "both sources and verify timing; do not delete samples or infer recovery."
                ),
            }
        )
    return cautions


def _build_experimental_evidence(state: dict) -> tuple[list[dict], list[dict]]:
    experimental = []
    ignored = []

    body_model = state.get("body_battery_model") or {}
    body_use, body_message = _body_battery_model_use(body_model)
    latest = body_model.get("latest_prediction") or {}
    experimental.append(
        {
            "name": "Wake Body Battery decision tree",
            "decision_use": body_use,
            "samples": body_model.get("samples"),
            "validation": {
                "leave_one_out_accuracy": body_model.get("leave_one_out_accuracy"),
            },
            "latest_prediction": {
                "path": latest.get("path"),
                "prob_good_wake_body_battery": (latest.get("leaf") or {}).get(
                    "prob_good_wake_body_battery"
                ),
                "leaf_samples": (latest.get("leaf") or {}).get("samples"),
            },
            "message": body_message,
        }
    )
    if body_use == "exploratory_only":
        ignored.append(
            {
                "name": "Wake Body Battery decision tree",
                "reason": "Not enough validated signal yet for training prescription.",
            }
        )

    predictor = state.get("training_predictor") or {}
    predictor_use, predictor_message = _training_predictor_use(predictor)
    prediction = (predictor.get("today_prediction") or {}).get("prediction") or {}
    validation = predictor.get("validation") or {}
    experimental.append(
        {
            "name": "Next-day training response tree",
            "decision_use": predictor_use,
            "samples": predictor.get("samples"),
            "validation": {
                "utility": validation.get("utility"),
                "status": validation.get("status"),
                "accuracy": validation.get("accuracy"),
                "baseline_majority_accuracy": validation.get("baseline_majority_accuracy"),
                "accuracy_lift_vs_baseline": validation.get("accuracy_lift_vs_baseline"),
            },
            "latest_prediction": {
                "predicted_next_day_readiness_level": prediction.get(
                    "predicted_next_day_readiness_level"
                ),
                "expected_next_day_response_score": prediction.get(
                    "expected_next_day_response_score"
                ),
                "prob_next_day_ready": prediction.get("prob_next_day_ready"),
                "leaf_samples": prediction.get("leaf_samples"),
            },
            "message": predictor_message,
        }
    )
    if predictor_use != "supporting_signal":
        ignored.append(
            {
                "name": "Next-day training response tree",
                "reason": predictor_message,
            }
        )

    return experimental, ignored


def _today_decision(state: dict, plan: dict, cautions: list[dict]) -> dict:
    session = plan.get("session") or {}
    decision_inputs = plan.get("decision_inputs") or {}
    garmin_arbitration = decision_inputs.get("garmin_arbitration") or {}
    session_lifecycle = decision_inputs.get("session_lifecycle") or {}
    constraints = (plan.get("constraint_resolution") or {}).get("applied") or []
    constraint_sources = {item.get("source") for item in constraints if isinstance(item, dict)}
    phase = (state.get("phase") or {}).get("name")
    readiness = state.get("readiness") or {}
    cns = state.get("cns_readiness") or {}
    cns_status = cns.get("status")
    confidence = _coach_confidence(state)
    continuity = state.get("bike_continuity_accountability") or {}
    adaptive = state.get("adaptive_training") or {}
    policy_audit = _continuity_policy_audit(state, plan)
    latest_response = state.get("latest_session_response") or {}
    response_classification = (latest_response.get("decision_use") or {}).get(
        "classification"
    )
    if (
        plan.get("coaching_status") == "post_session_review"
        or session_lifecycle.get("stance") == "post_session_review"
    ):
        stance = "post_session_review"
    elif (
        session.get("type") == "scheduled_rest"
        and decision_inputs.get("scheduled_rest")
        and not decision_inputs.get("sabbath_exception")
    ):
        stance = "sabbath_rest"
    elif (
        session.get("type") in {"scheduled_rest", "scheduled_recovery"}
        or session.get("modality") == "rest"
        or session.get("intensity") == "rest"
    ):
        stance = "recovery"
    elif readiness.get("readiness_level") == "red":
        stance = "downshift"
    elif "cns_readiness" in constraint_sources or (
        cns_status in {"impaired", "compromised"}
        and session.get("intensity") not in {"recovery", "easy"}
    ):
        stance = "cns_downshift"
    elif "garmin_diagnosis_arbitration" in constraint_sources:
        stance = "garmin_downshift"
    elif "data_freshness" in constraint_sources:
        stance = "data_limited"
    elif session.get("adaptive_upgrade_option"):
        stance = "controlled_upgrade_option"
    elif session.get("type") == "mtb_repeatability_controlled":
        stance = "controlled_upgrade"
    elif session.get("intensity") == "skill" or str(session.get("type") or "").startswith(
        "mtb_skill"
    ):
        stance = "controlled_skill"
    elif session.get("intensity") == "hard":
        stance = "quality_allowed"
    else:
        stance = "aerobic_continuity"
    return {
        "stance": stance,
        "coach_confidence": confidence,
        "session": session,
        "plan_source": plan.get("plan_source"),
        "constraint_resolution": plan.get("constraint_resolution"),
        "gym": plan.get("gym"),
        "nutrition": plan.get("nutrition"),
        "guardrails": plan.get("guardrails", []),
        "build_accountability": {
            "status": continuity.get("status"),
            "current_week_unique_bike_days": (
                continuity.get("current_calendar_week") or {}
            ).get("unique_bike_days"),
            "preferred_unique_bike_days": (continuity.get("targets") or {}).get(
                "preferred_unique_bike_days"
            ),
            "preferred_gap_unique_days": continuity.get(
                "preferred_gap_unique_days"
            ),
            "policy_alignment": policy_audit,
        },
        "adaptive_programming": {
            "roadmap_block": adaptive.get("roadmap_block"),
            "progression_decision": adaptive.get("progression_decision"),
            "weekly_budget": adaptive.get("weekly_budget"),
            "programming_audit": adaptive.get("programming_audit"),
            "guardrail": "Programming direction is not same-day clearance; the resolved session and lowest safety ceiling still win.",
        },
        "latest_session_response": {
            "status": latest_response.get("status"),
            "classification": response_classification,
            "global_rpe_0_to_10": latest_response.get("global_rpe_0_to_10"),
            "local_rpe_0_to_10": latest_response.get("local_rpe_0_to_10"),
            "stop_rule_outcome": latest_response.get("stop_rule_outcome"),
            "stop_rule_outcome_explicit": latest_response.get(
                "stop_rule_outcome_explicit"
            ),
            "symptom": latest_response.get("symptom"),
        },
        "why": [
            f"Readiness is {_value(readiness.get('readiness_level'))} at {_value(readiness.get('readiness_score'))}/100.",
            f"Phase is {_value(phase)}.",
            f"Planned session is {session.get('title', 'unknown session')} at {session.get('intensity', 'unknown')} intensity.",
            f"Garmin arbitration recommends {_value(garmin_arbitration.get('recommended_action'))}.",
            f"CNS readiness is {_value(cns_status)} with ceiling {_value((cns.get('session_ceiling') or {}).get('level'))}.",
            f"{len(constraints)} session constraint(s) were applied.",
            f"{len(cautions)} caution item(s) are active.",
            (
                f"Bike continuity is {(continuity.get('current_calendar_week') or {}).get('unique_bike_days', 'unknown')} "
                f"unique day(s) this calendar week against a preferred {(continuity.get('targets') or {}).get('preferred_unique_bike_days', 'unknown')}; "
                f"written-dose policy is {policy_audit.get('status')}."
            ),
            f"Latest structured session response is {_value(response_classification)} with explicit stop outcome {_value(latest_response.get('stop_rule_outcome'))}.",
        ],
    }


def _next_data_needed(state: dict) -> list[str]:
    readiness_accuracy = (state.get("readiness") or {}).get("readiness_accuracy") or {}
    needed = []
    recommended_next_step = (
        (readiness_accuracy.get("recommended_next_step") or {}) if isinstance(readiness_accuracy, dict) else {}
    )
    readiness_commands = recommended_next_step.get("commands")
    if readiness_commands:
        needed.append("Recommended readiness fix sequence (in order):")
        for command in readiness_commands:
            needed.append(command)
        blockers = recommended_next_step.get("label") or "No explicit blocker label."
        if blockers:
            needed.append(f"Readiness blocker context: {blockers}")
    needed.extend(
        [
            "Keep live Garmin wellness and activity sync current before hard-session decisions.",
            "Label what the Fenix cannot see: ride purpose, trail condition, confidence, braking comfort, skill quality, fueling, and heat feel.",
            "Label CNS/technical sharpness: brain fog, vision, braking timing, line choice, unclipping delay, confidence, and late-ride decision speed.",
        ]
    )
    if not (state.get("body_battery_model") or {}).get("samples"):
        needed.append("Collect more modern wellness rows before trusting Body Battery modeling.")
    if (state.get("training_predictor") or {}).get("validation", {}).get("utility") != "useful":
        needed.append("Treat the training response model as experimental until validation beats a simple baseline.")
    if ((state.get("gear_audit") or {}).get("coverage") or {}).get("status") != "complete":
        needed.append("Complete recent activity Gear metadata coverage so bike/source mismatches are not inferred from a partial index.")
    if ((state.get("device_audit") or {}).get("coverage") or {}).get("status") != "complete":
        needed.append("Complete recent Devices & Apps coverage so HR-source confidence is known for key rides.")
    if ((state.get("self_evaluation") or {}).get("coverage") or {}).get("status") != "complete":
        needed.append("Complete recent self-evaluation metadata coverage; log feel/RPE after key sessions when Garmin has none.")
    wearable = state.get("wearable_coverage") or {}
    unexplained_runs = [
        row
        for row in wearable.get("material_run_attribution") or []
        if (row.get("unexplained_minutes") or 0) > 0.1
    ]
    unknown_hr_unexplained = any(
        ((row.get("optical_hr_measurement") or {}).get("state") or "unknown")
        == "unknown"
        for row in unexplained_runs
    )
    if unknown_hr_unexplained:
        needed.append(
            "If exact wear-state attribution matters, report the completed Fenix-off start and end times; the recurring Sunday habit alone does not time today's gap."
        )
    elif (wearable.get("classification") or {}).get("label") in {
        "insufficient_series_coverage",
        "endpoint_unavailable",
    }:
        needed.append(
            "Refresh the target-date Garmin all-day stress surface; positive low-stress use requires dense valid samples through a declared evening cutoff."
        )
    return needed


def _same_date_artifact(root: str | Path | None, filename: str, target: date) -> dict | None:
    payload = read_json(snapshots_dir(root) / filename, {})
    if isinstance(payload, dict) and parse_date(payload.get("date")) == target:
        return payload
    return None


def _packet_text(packet: dict) -> str:
    def bullets(items: list[str]) -> str:
        return "\n".join(f"- {item}" for item in items) if items else "- None"

    trusted = [
        f"{item['name']}: {item['status']} - {item['message']}"
        for item in packet["evidence"]["trusted"]
    ]
    cautions = [
        f"{item.get('source')}/{item.get('type')}: {item.get('message')}"
        for item in packet["evidence"]["cautions"]
    ]
    experimental = [
        f"{item['name']}: {item['decision_use']} - {item['message']}"
        for item in packet["evidence"]["experimental"]
    ]
    ignored = [
        f"{item['name']}: {item['reason']}"
        for item in packet["evidence"]["ignored_for_decision"]
    ]
    today = packet["today_call"]
    source = today.get("plan_source") or {}
    source_text = source.get("type") or "unknown"
    if source.get("path"):
        source_text = f"{source_text} ({source['path']})"
    return "\n".join(
        [
            f"Coach Packet - {packet['date']}",
            "",
            f"Stack path: {packet['stack_path']['chosen_path']}",
            packet["stack_path"]["why"],
            "",
            f"Today: {today['session'].get('title')} ({today['stance']}, confidence {today['coach_confidence']})",
            f"Plan source: {source_text}",
            bullets(today.get("why") or []),
            "",
            "Trusted evidence:",
            bullets(trusted),
            "",
            "Cautions:",
            bullets(cautions),
            "",
            "Experimental evidence:",
            bullets(experimental),
            "",
            "Ignored for today's decision:",
            bullets(ignored),
            "",
            "Next data needed:",
            bullets(packet.get("next_data_needed") or []),
            "",
        ]
    )


def build_coach_packet(
    root: str | Path | None = None,
    for_date: str | date | None = None,
    state: dict | None = None,
    plan: dict | None = None,
) -> dict:
    target = parse_date(for_date) or parse_date((state or {}).get("date")) or today_local(DEFAULT_TIMEZONE)
    if state is None:
        state = _same_date_artifact(root, "current_state.json", target) or build_current_state(root, target)
    if plan is None:
        plan = _same_date_artifact(root, "today_plan.json", target) or build_today_plan(
            root, target, state=state
        )

    cautions = _build_cautions(state, plan)
    experimental, ignored = _build_experimental_evidence(state)
    packet = {
        "date": target.isoformat(),
        "generated_at": iso_now(DEFAULT_TIMEZONE),
        "artifact_type": "coach_decision_packet",
        "stack_path": {
            "chosen_path": "evidence_triage_over_more_models",
            "why": "More artifacts are only useful when they improve the coaching call; this packet promotes current, validated, inspectable evidence and demotes weak model output.",
            "promotion_rule": "A model can influence training only when its validation beats a simple baseline and its target matches the coaching decision.",
        },
        "today_call": _today_decision(state, plan, cautions),
        "evidence": {
            "trusted": _build_trusted_evidence(state, plan, root),
            "cautions": cautions,
            "experimental": experimental,
            "ignored_for_decision": ignored,
        },
        "next_data_needed": _next_data_needed(state),
        "artifacts": {
            "json": "snapshots/coach_packet.json",
            "text": "snapshots/coach_packet.txt",
            "source_state": "snapshots/current_state.json",
            "source_plan": "snapshots/today_plan.json",
            "source_cycling_ftp": "snapshots/garmin_cycling_ftp_current.json",
        },
    }
    write_json(snapshots_dir(root) / "coach_packet.json", packet)
    write_text(snapshots_dir(root) / "coach_packet.txt", _packet_text(packet))
    return packet
