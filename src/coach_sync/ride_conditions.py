from __future__ import annotations

from datetime import date, datetime, timezone
import json
import math
import os
from pathlib import Path
from typing import Any, Callable
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from .context import load_context
from .io import read_json, write_json
from .paths import snapshots_dir
from .time_utils import DEFAULT_TIMEZONE, now_local, parse_date


ENVIRONMENT_EVIDENCE_VERSION = "mtb_environment_evidence_adapter_v1"
ARTIFACT_NAME = "environment_evidence.json"
DEFAULT_TIMEOUT_SECONDS = 5.0
SUPPORTED_SCHEMA_MAJOR = "1"
DEFAULT_STALE_AFTER_SECONDS = 420.0
DEFAULT_EXPIRED_AFTER_SECONDS = 900.0
DEFAULT_FUTURE_TOLERANCE_SECONDS = 120.0
DEFAULT_SPORT_BANDS = {
    "normal_below": 25.0,
    "poor_from": 51.0,
    "hazardous_above": 150.0,
}


def _environment_config(context: dict[str, Any]) -> dict[str, Any]:
    value = (
        (((context.get("athlete") or {}).get("venue_profiles") or {}).get("bukit_kiara") or {}).get(
            "preferred_environment_report"
        )
        or {}
    )
    return value if isinstance(value, dict) else {}


def _endpoint(config: dict[str, Any], override: str | None = None) -> str | None:
    if override:
        return override
    if os.getenv("COACH_RIDE_CONDITIONS_URL"):
        return os.environ["COACH_RIDE_CONDITIONS_URL"]
    endpoint = config.get("endpoint")
    if endpoint:
        return str(endpoint)
    base = config.get("base_url") or config.get("url")
    if not base:
        return None
    path = ((config.get("endpoints") or {}).get("environment_evidence")) or (
        "/api/v1/mtb/environment-evidence"
    )
    return f"{str(base).rstrip('/')}/{str(path).lstrip('/')}"


def _http_json(url: str, timeout: float) -> dict[str, Any]:
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "Cache-Control": "no-cache",
            "User-Agent": "ClaytonMTBCoachStack/1.0",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object from {url}")
    return payload


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _nested(record: Any, *keys: str) -> dict[str, Any]:
    current = record
    for key in keys:
        if not isinstance(current, dict):
            return {}
        current = current.get(key)
    return current if isinstance(current, dict) else {}


def _compact_support(value: Any) -> dict[str, float | None]:
    support = value if isinstance(value, dict) else {}
    return {
        "origin_count": _number(support.get("originCount")),
        "matched_count": _number(support.get("matchedCount")),
        "minimum_required": _number(support.get("minimumRequired")),
    }


def _compact_clearance_event(value: Any) -> dict[str, Any]:
    event = value if isinstance(value, dict) else {}
    return {
        "detected": event.get("detected"),
        "state": event.get("state"),
        "kind": event.get("kind"),
        "label": event.get("label"),
        "age_minutes": _number(event.get("ageMinutes")),
        "rain_support": event.get("rainSupport"),
        "dry_air_mass_support": event.get("dryAirMassSupport"),
    }


def _compact_ride_window(value: Any) -> dict[str, Any]:
    window = value if isinstance(value, dict) else {}
    forecast = window.get("weatherForecast") if isinstance(window.get("weatherForecast"), dict) else {}
    return {
        "target_day": window.get("targetDay"),
        "ride_window": window.get("rideWindow"),
        "lead_hours": _number(window.get("leadHours")),
        "active": window.get("active"),
        "current_conditions_applicable": window.get("currentConditionsApplicable"),
        "recheck": window.get("recheck"),
        "confidence": window.get("confidence"),
        "weather_forecast": {
            "available": forecast.get("available"),
            "apparent_temperature_max_c": _number(forecast.get("apparentTemperatureMaxC")),
            "precipitation_probability_max_pct": _number(
                forecast.get("precipitationProbabilityMaxPct")
            ),
            "precipitation_mm": _number(forecast.get("precipitationMm")),
            "rain_signal": forecast.get("rainSignal"),
        },
    }


def _compact_ride_windows(value: Any) -> dict[str, Any]:
    windows = value if isinstance(value, dict) else {}
    comparison = windows.get("comparison") if isinstance(windows.get("comparison"), dict) else {}
    return {
        "comparison": {
            "status": comparison.get("status"),
            "confidence": comparison.get("confidence"),
            "paired_days": _number(comparison.get("pairedDays")),
            "required_days": _number(comparison.get("requiredDays")),
        },
        "morning": _compact_ride_window(windows.get("morning")),
        "afternoon": _compact_ride_window(windows.get("afternoon")),
    }


def _compact_limitations(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [
        {"code": item.get("code"), "message": item.get("message")}
        for item in value
        if isinstance(item, dict)
    ]


def _validate_contract(
    payload: dict[str, Any],
    config: dict[str, Any],
    attempted_at: datetime,
) -> list[str]:
    issues: list[str] = []
    schema_version = str(payload.get("schemaVersion") or "")
    if schema_version.split(".", 1)[0] != SUPPORTED_SCHEMA_MAJOR:
        issues.append("unsupported_schema_major")
    if payload.get("kind") != "mtb_environment_evidence":
        issues.append("unexpected_kind")

    boundary = payload.get("boundary") if isinstance(payload.get("boundary"), dict) else {}
    if boundary.get("role") != "environmental_evidence":
        issues.append("unexpected_boundary_role")
    if boundary.get("trainingPrescriptionIncluded") is not False:
        issues.append("training_prescription_boundary_violation")
    if boundary.get("historicalSeriesIncluded") is not False:
        issues.append("historical_series_boundary_violation")

    server_status = payload.get("status") if isinstance(payload.get("status"), dict) else {}
    if server_status.get("usable") is not True:
        issues.append("server_status_not_usable")

    location = payload.get("location") if isinstance(payload.get("location"), dict) else {}
    expected_timezone = config.get("timezone") or config.get("station_timezone")
    if expected_timezone and location.get("timezone") != expected_timezone:
        issues.append("location_timezone_mismatch")
    expected_name = config.get("location_name")
    if expected_name and str(expected_name).lower() not in str(location.get("name") or "").lower():
        issues.append("location_name_mismatch")

    expected_location_id = config.get("location_id")
    actual_location_id = _nested(payload, "provenance", "sensor").get("locationId")
    if expected_location_id is not None and str(actual_location_id) != str(expected_location_id):
        issues.append("sensor_location_id_mismatch")

    generated_at = _timestamp(payload.get("generatedAt"))
    observed_at = _timestamp(_nested(payload, "observation").get("timestamp"))
    if generated_at is None:
        issues.append("generated_at_missing_or_not_timezone_aware")
    if observed_at is None:
        issues.append("observation_timestamp_missing_or_not_timezone_aware")
    elif (
        observed_at.astimezone(timezone.utc) - attempted_at.astimezone(timezone.utc)
    ).total_seconds() > DEFAULT_FUTURE_TOLERANCE_SECONDS:
        issues.append("observation_timestamp_in_future")

    pm2_5 = _number(_nested(payload, "observation", "particles").get("pm25UgM3"))
    if pm2_5 is None or pm2_5 < 0:
        issues.append("pm2_5_missing_or_invalid")
    return issues


def _normalize(payload: dict[str, Any]) -> dict[str, Any]:
    observation = _nested(payload, "observation")
    particles = _nested(observation, "particles")
    heat = _nested(observation, "heat")
    nowcast = _nested(payload, "particleNowcast")
    mix = _nested(nowcast, "particleMixSignal")
    arrival = _nested(payload, "exposureOutlook", "arrival")
    arrival_range = _nested(arrival, "likelyRangePm25UgM3")
    trail = _nested(payload, "exposureOutlook", "onTrail")
    trail_range = _nested(trail, "likelyMeanRangePm25UgM3")
    trail_peak = _nested(trail, "upperPeakPm25UgM3")
    weather = _nested(payload, "weather")
    trail_weather = _nested(weather, "trailPeriod")
    airflow = _nested(trail_weather, "airflow")
    quality = _nested(payload, "evidenceQuality")
    provenance = _nested(payload, "provenance")
    sensor = _nested(provenance, "sensor")
    forecast = _nested(provenance, "weatherForecast")
    weather_reference = _nested(provenance, "weatherReference")
    logistics = _nested(payload, "logistics")
    typical_duration = _nested(logistics, "typicalTrailDurationMinutes")
    exposure_window = _nested(logistics, "modeledExposureWindowMinutes")

    return {
        "identity": {
            "schema_version": payload.get("schemaVersion"),
            "kind": payload.get("kind"),
            "evidence_id": payload.get("evidenceId"),
            "server_generated_at": payload.get("generatedAt"),
        },
        "location": {
            "name": _nested(payload, "location").get("name"),
            "timezone": _nested(payload, "location").get("timezone"),
            "sensor_location_id": sensor.get("locationId"),
        },
        "boundary": {
            "role": _nested(payload, "boundary").get("role"),
            "training_prescription_included": _nested(payload, "boundary").get(
                "trainingPrescriptionIncluded"
            ),
            "historical_series_included": _nested(payload, "boundary").get(
                "historicalSeriesIncluded"
            ),
        },
        "server_status": {
            "state": _nested(payload, "status").get("state"),
            "usable": _nested(payload, "status").get("usable"),
            "fresh": _nested(payload, "status").get("fresh"),
            "message": _nested(payload, "status").get("message"),
            "issues": list(_nested(payload, "status").get("issues") or []),
        },
        "logistics": {
            "decision_to_trail_min": _number(logistics.get("decisionToTrailMinutes")),
            "typical_trail_duration_min": {
                "minimum": _number(typical_duration.get("minimum")),
                "maximum": _number(typical_duration.get("maximum")),
            },
            "modeled_trail_duration_min": _number(logistics.get("modeledTrailDurationMinutes")),
            "modeled_exposure_window_min": {
                "start": _number(exposure_window.get("start")),
                "end": _number(exposure_window.get("end")),
            },
        },
        "observation": {
            "id": observation.get("id"),
            "observed_at_utc": observation.get("timestamp"),
            "server_age_seconds": _number(observation.get("ageSeconds")),
            "pm2_5_ug_m3": _number(particles.get("pm25UgM3")),
            "pm10_ug_m3": _number(particles.get("pm10UgM3")),
            "temperature_c": _number(heat.get("temperatureC")),
            "relative_humidity_pct": _number(heat.get("relativeHumidityPct")),
            "heat_index_c": _number(heat.get("heatIndexC")),
        },
        "particle_nowcast": {
            "available": nowcast.get("available"),
            "state": nowcast.get("state"),
            "label": nowcast.get("label"),
            "change_30_min_ug_m3": _number(nowcast.get("change30MinutesUgM3")),
            "change_60_min_ug_m3": _number(nowcast.get("change60MinutesUgM3")),
            "fast_rise": nowcast.get("fastRise"),
            "minimum_since_event_ug_m3": _number(nowcast.get("minimumSinceEventUgM3")),
            "recheck_minutes": _number(nowcast.get("recheckMinutes")),
            "particle_mix": {
                "state": mix.get("state"),
                "label": mix.get("label"),
                "fine_share_pct": _number(mix.get("fineSharePct")),
                "coarse_particles_ug_m3": _number(mix.get("coarseParticlesUgM3")),
            },
        },
        "exposure_outlook": {
            "arrival": {
                "available": arrival.get("available"),
                "offset_min": _number(arrival.get("offsetMinutes")),
                "expected_at_utc": arrival.get("expectedAt"),
                "headline": arrival.get("headline"),
                "baseline_pm2_5_ug_m3": _number(arrival.get("baselinePm25UgM3")),
                "likely_range_pm2_5_ug_m3": {
                    "low": _number(arrival_range.get("low")),
                    "high": _number(arrival_range.get("high")),
                    "calibrated": arrival_range.get("calibrated"),
                },
                "confidence": arrival.get("confidence"),
                "confidence_detail": arrival.get("confidenceDetail"),
                "method_id": arrival.get("methodId"),
                "support": _compact_support(arrival.get("support")),
            },
            "on_trail": {
                "available": trail.get("available"),
                "start_offset_min": _number(trail.get("startOffsetMinutes")),
                "end_offset_min": _number(trail.get("endOffsetMinutes")),
                "start_at_utc": trail.get("startAt"),
                "end_at_utc": trail.get("endAt"),
                "headline": trail.get("headline"),
                "baseline_mean_pm2_5_ug_m3": _number(trail.get("baselineMeanPm25UgM3")),
                "likely_mean_range_pm2_5_ug_m3": {
                    "low": _number(trail_range.get("low")),
                    "high": _number(trail_range.get("high")),
                    "calibrated": trail_range.get("calibrated"),
                },
                "upper_peak_pm2_5_ug_m3": {
                    "value": _number(trail_peak.get("value")),
                    "calibrated": trail_peak.get("calibrated"),
                },
                "confidence": trail.get("confidence"),
                "confidence_detail": trail.get("confidenceDetail"),
                "method_id": trail.get("methodId"),
                "support": _compact_support(trail.get("support")),
            },
        },
        "weather": {
            "available": weather.get("available"),
            "forecast_source": weather.get("forecastSource"),
            "forecast_fetched_at_utc": weather.get("forecastFetchedAt"),
            "forecast_age_min": _number(weather.get("forecastAgeMinutes")),
            "trail_period": {
                "available": trail_weather.get("available"),
                "start_at_utc": trail_weather.get("startAt"),
                "end_at_utc": trail_weather.get("endAt"),
                "apparent_temperature_max_c": _number(
                    trail_weather.get("apparentTemperatureMaxC")
                ),
                "precipitation_probability_max_pct": _number(
                    trail_weather.get("precipitationProbabilityMaxPct")
                ),
                "precipitation_mm": _number(trail_weather.get("precipitationMm")),
                "rain_signal": trail_weather.get("rainSignal"),
                "airflow": {
                    "context": airflow.get("context"),
                    "modeled_wind_10m_mean_kmh": _number(airflow.get("modeledWind10mMeanKmh")),
                    "modeled_wind_180m_mean_kmh": _number(
                        airflow.get("modeledWind180mMeanKmh")
                    ),
                    "modeled_direction_180m": airflow.get("modeledDirection180m"),
                    "used_for_particle_forecast": airflow.get("usedForParticleForecast"),
                },
            },
            "particle_relationship_validated": weather.get("particleRelationshipValidated"),
        },
        "clearance_event": _compact_clearance_event(payload.get("clearanceEvent")),
        "ride_windows": _compact_ride_windows(payload.get("rideWindows")),
        "evidence_quality": {
            "state": quality.get("state"),
            "limitations": _compact_limitations(quality.get("limitations")),
        },
        "provenance": {
            "sensor": {
                "provider": sensor.get("provider"),
                "location_id": sensor.get("locationId"),
                "measurement": sensor.get("measurement"),
                "observed_at_utc": sensor.get("observedAt"),
                "age_seconds": _number(sensor.get("ageSeconds")),
                "poll_seconds": _number(sensor.get("pollSeconds")),
                "stale_after_seconds": _number(sensor.get("staleAfterSeconds")),
                "expired_after_seconds": _number(sensor.get("expiredAfterSeconds")),
            },
            "weather_forecast": {
                "provider": forecast.get("provider"),
                "fetched_at_utc": forecast.get("fetchedAt"),
                "age_minutes": _number(forecast.get("ageMinutes")),
                "validation": forecast.get("validation"),
            },
            "weather_reference": {
                "provider": weather_reference.get("provider"),
                "station": weather_reference.get("station"),
                "wigos_id": weather_reference.get("wigosId"),
            },
            "analysis_days_requested": _number(provenance.get("analysisDaysRequested")),
            "history_hours_available": _number(provenance.get("historyHoursAvailable")),
            "sample_count": _number(provenance.get("sampleCount")),
        },
    }


def _sport_bands(config: dict[str, Any]) -> dict[str, float]:
    configured = config.get("sports_exercise_bands_ug_m3")
    configured = configured if isinstance(configured, dict) else {}
    bands = {
        key: _number(configured.get(key, default))
        for key, default in DEFAULT_SPORT_BANDS.items()
    }
    if (
        any(value is None for value in bands.values())
        or not (bands["normal_below"] < bands["poor_from"] <= bands["hazardous_above"])
    ):
        return dict(DEFAULT_SPORT_BANDS)
    return {key: float(value) for key, value in bands.items()}


def _projection_status(
    evidence: dict[str, Any] | None,
    latest_attempt: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    if not evidence:
        return {
            "state": "unknown",
            "current": False,
            "age_seconds": None,
            "stale_after_seconds": None,
            "expired_after_seconds": None,
            "latest_attempt_status": latest_attempt.get("status"),
        }
    observed = _timestamp(_nested(evidence, "observation").get("observed_at_utc"))
    age_seconds = (
        (now.astimezone(timezone.utc) - observed.astimezone(timezone.utc)).total_seconds()
        if observed is not None
        else None
    )
    stale_after = _number(_nested(evidence, "provenance", "sensor").get("stale_after_seconds"))
    expired_after = _number(
        _nested(evidence, "provenance", "sensor").get("expired_after_seconds")
    )
    stale_after = stale_after or DEFAULT_STALE_AFTER_SECONDS
    expired_after = expired_after or DEFAULT_EXPIRED_AFTER_SECONDS
    if age_seconds is None or age_seconds < -DEFAULT_FUTURE_TOLERANCE_SECONDS:
        state = "unknown"
    elif age_seconds > expired_after:
        state = "expired"
    elif age_seconds > stale_after or _nested(evidence, "server_status").get("fresh") is not True:
        state = "stale"
    elif latest_attempt.get("status") == "success":
        state = "current"
    else:
        state = "retained_current"
    return {
        "state": state,
        "current": state == "current",
        "usable_for_downshift": state in {"current", "retained_current", "stale"},
        "age_seconds": round(max(0.0, age_seconds), 1) if age_seconds is not None else None,
        "stale_after_seconds": stale_after,
        "expired_after_seconds": expired_after,
        "latest_attempt_status": latest_attempt.get("status"),
    }


def _confidence_supports_forecast_closure(outlook: dict[str, Any]) -> bool:
    confidence = str(outlook.get("confidence") or "").lower()
    likely_range = outlook.get("likely_range_pm2_5_ug_m3") or {}
    return confidence in {"medium", "high"} and likely_range.get("calibrated") is True


def _supported_forecast_lower_bound_is_poor(
    arrival: dict[str, Any],
    trail: dict[str, Any],
    poor_from: float,
) -> bool:
    for outlook in (arrival, trail):
        likely_range = outlook.get("likely_range_pm2_5_ug_m3") or {}
        lower = _number(likely_range.get("low"))
        if (
            lower is not None
            and lower >= poor_from
            and _confidence_supports_forecast_closure(outlook)
        ):
            return True
    return False


def _environment_decision(
    evidence: dict[str, Any] | None,
    freshness: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    bands = _sport_bands(config)
    common = {
        "decision_role": "outdoor_downshift_or_hold_only_never_training_promotion",
        "can_promote_training": False,
        "sports_exercise_bands_ug_m3": {
            **bands,
            "basis": "AIS 2023 exercise-in-bushfire-smoke atmospheric PM2.5 bands; not AQI.",
        },
        "venue_keys": list(config.get("automatic_gate_venue_keys") or ["bukit_kiara"]),
        "venue_aliases": list(
            config.get("automatic_gate_venue_aliases")
            or ["Bukit Kiara", "Kiara", "Taman Tun Dr. Ismail", "TTDI"]
        ),
        "indoor_guardrail": (
            "This outdoor source does not establish indoor air quality; use Clayton's indoor monitor "
            "and current symptoms separately."
        ),
        "forecast_guardrail": (
            "Low-confidence or uncalibrated range crossings create a hold/recheck, not a fabricated forecast certainty."
        ),
    }
    state = freshness.get("state")
    if not evidence or state in {"unknown", "expired"}:
        return {
            **common,
            "gate": "environment_unknown_hold",
            "severity": "yellow",
            "reason_codes": ["environment_evidence_unavailable_or_expired"],
            "reason": "No current usable Bukit Kiara environment evidence is available; refresh before committing to planned outdoor training.",
            "default_if_refresh_unavailable": "indoor_or_rest",
        }

    observation = evidence.get("observation") or {}
    current_pm = _number(observation.get("pm2_5_ug_m3"))
    arrival = _nested(evidence, "exposure_outlook", "arrival")
    trail = _nested(evidence, "exposure_outlook", "on_trail")
    arrival_range = arrival.get("likely_range_pm2_5_ug_m3") or {}
    trail_range = trail.get("likely_mean_range_pm2_5_ug_m3") or {}
    forecast_lower_values = [
        _number(arrival_range.get("low")),
        _number(trail_range.get("low")),
    ]
    forecast_upper_values = [
        _number(arrival_range.get("high")),
        _number(trail_range.get("high")),
        _number(_nested(trail, "upper_peak_pm2_5_ug_m3").get("value")),
    ]
    forecast_lower = max((value for value in forecast_lower_values if value is not None), default=None)
    forecast_upper = max((value for value in forecast_upper_values if value is not None), default=None)
    nowcast = evidence.get("particle_nowcast") or {}
    weather = _nested(evidence, "weather", "trail_period")
    heat_max = max(
        (
            value
            for value in (
                _number(observation.get("heat_index_c")),
                _number(weather.get("apparent_temperature_max_c")),
            )
            if value is not None
        ),
        default=None,
    )
    rain_probability = _number(weather.get("precipitation_probability_max_pct"))
    rain_mm = _number(weather.get("precipitation_mm"))
    rain_signal = str(weather.get("rain_signal") or "")
    reason_codes: list[str] = []
    modifiers: list[str] = []
    recheck_minutes = _number(nowcast.get("recheck_minutes"))

    if heat_max is not None and heat_max >= 40:
        modifiers.append("strong_heat_modifier")
    elif heat_max is not None and heat_max >= 38:
        modifiers.append("heat_load_modifier")
    if (
        (rain_probability is not None and rain_probability >= 70)
        or (rain_mm is not None and rain_mm >= 1)
        or rain_signal.lower() == "rain may disrupt the ride"
    ):
        modifiers.append("rain_disruption")
    elif (
        (rain_probability is not None and rain_probability >= 40)
        or "shower" in rain_signal.lower()
    ):
        modifiers.append("rain_watch")
    if _nested(evidence, "evidence_quality").get("state") not in {None, "good", "ready"}:
        modifiers.append("evidence_quality_limited")

    if state in {"stale", "retained_current"}:
        if current_pm is not None and current_pm >= bands["poor_from"]:
            gate = "retained_high_ventilation_closure_pending_refresh"
            severity = "red"
            reason_codes.append("retained_poor_value_preserves_restriction")
        else:
            gate = "hold_pending_refresh"
            severity = "yellow"
            reason_codes.append("retained_value_cannot_clear_outdoor_training")
    elif current_pm is not None and current_pm > bands["hazardous_above"]:
        gate = "close_all_planned_outdoor_exercise"
        severity = "red"
        reason_codes.append("current_pm2_5_likely_hazardous")
    elif current_pm is not None and current_pm >= bands["poor_from"]:
        gate = "close_mtb_prolonged_endurance_high_ventilation"
        severity = "red"
        reason_codes.append("current_pm2_5_poor_exercise_conditions")
    elif (
        _supported_forecast_lower_bound_is_poor(arrival, trail, bands["poor_from"])
    ):
        gate = "close_mtb_prolonged_endurance_high_ventilation"
        severity = "red"
        reason_codes.append("calibrated_supported_forecast_lower_bound_poor")
    elif current_pm is not None and current_pm >= bands["normal_below"]:
        upper_crosses_poor = forecast_upper is not None and forecast_upper >= bands["poor_from"]
        rebound = str(nowcast.get("state") or "").lower() in {"rebound", "rising"}
        fast_rise = nowcast.get("fast_rise") is True
        if upper_crosses_poor or rebound or fast_rise:
            gate = "hold_and_recheck"
            severity = "yellow"
            reason_codes.append("current_pm2_5_moderate_caution")
            if upper_crosses_poor:
                reason_codes.append("uncertain_exposure_range_crosses_poor")
            if rebound:
                reason_codes.append("particle_rebound_or_rising")
            if fast_rise:
                reason_codes.append("fast_particle_rise")
        else:
            gate = "moderate_caution"
            severity = "yellow"
            reason_codes.append("current_pm2_5_moderate_caution")
    else:
        gate = "no_environment_downshift_from_current_point"
        severity = "info"
        reason_codes.append("current_pm2_5_normal_exercise_band")

    if modifiers:
        reason_codes.extend(modifiers)
    current_text = f"{current_pm:.1f} ug/m3" if current_pm is not None else "unknown"
    reason = (
        f"Bukit Kiara PM2.5 is {current_text}; environment gate is {gate}. "
        "Physical readiness, CNS, current symptoms and actual trail conditions remain separate ceilings."
    )
    return {
        **common,
        "gate": gate,
        "severity": severity,
        "reason_codes": reason_codes,
        "reason": reason,
        "current_pm2_5_ug_m3": current_pm,
        "forecast_lower_pm2_5_ug_m3": forecast_lower,
        "forecast_upper_pm2_5_ug_m3": forecast_upper,
        "forecast_confidence": {
            "arrival": arrival.get("confidence"),
            "on_trail": trail.get("confidence"),
        },
        "recheck_minutes": recheck_minutes,
        "default_if_recheck_unavailable": (
            "indoor_or_rest" if gate in {"hold_and_recheck", "hold_pending_refresh"} else None
        ),
        "modifiers": modifiers,
        "heat_max_c": heat_max,
        "rain_probability_max_pct": rain_probability,
        "rain_mm": rain_mm,
        "weather_particle_relationship_validated": _nested(evidence, "weather").get(
            "particle_relationship_validated"
        ),
    }


def _project(
    stored: dict[str, Any],
    config: dict[str, Any],
    target_date: date,
    now: datetime,
) -> dict[str, Any]:
    latest_attempt = stored.get("latest_attempt") if isinstance(stored, dict) else None
    latest_attempt = latest_attempt if isinstance(latest_attempt, dict) else {}
    last_known_good = stored.get("last_known_good") if isinstance(stored, dict) else None
    last_known_good = last_known_good if isinstance(last_known_good, dict) else None
    freshness = _projection_status(last_known_good, latest_attempt, now)
    decision = _environment_decision(last_known_good, freshness, config)
    return {
        "artifact_type": "environment_evidence_current",
        "version": ENVIRONMENT_EVIDENCE_VERSION,
        "date": target_date.isoformat(),
        "generated_at": now.isoformat(timespec="seconds"),
        "status": freshness.get("state"),
        "source": {
            "name": config.get("name") or "Clayton local Bukit Kiara environment evidence",
            "endpoint": _endpoint(config),
            "access": "athlete_managed_local_http_json",
            "fallback": "none",
        },
        "latest_attempt": latest_attempt or None,
        "last_known_good": last_known_good,
        "freshness": freshness,
        "decision": decision,
        "persistence": {
            "current_snapshot": f"snapshots/{ARTIFACT_NAME}",
            "dated_snapshot": f"snapshots/environment_evidence_{target_date.isoformat()}.json",
            "raw_payload_stored": False,
            "history_series_duplicated": False,
        },
        "guardrail": (
            "This endpoint supplies environmental evidence, not a prescription. The stack may only hold, "
            "downshift or replace a venue-matched outdoor session; it cannot promote readiness, CNS consequence "
            "or indoor-air clearance."
        ),
    }


def build_environment_evidence(
    root: str | Path | None = None,
    for_date: str | date | None = None,
    *,
    endpoint: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    fetch_json: Callable[[str, float], dict[str, Any]] | None = None,
    now: datetime | None = None,
    refresh: bool = True,
) -> dict[str, Any]:
    context = load_context(root)
    config = _environment_config(context)
    timezone_name = (
        config.get("timezone")
        or config.get("station_timezone")
        or (context.get("athlete") or {}).get("timezone")
        or DEFAULT_TIMEZONE
    )
    generated = now or now_local(str(timezone_name))
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=ZoneInfo(str(timezone_name)))
    else:
        generated = generated.astimezone(ZoneInfo(str(timezone_name)))
    target = parse_date(for_date) or generated.date()
    current_path = snapshots_dir(root) / ARTIFACT_NAME
    dated_path = snapshots_dir(root) / f"environment_evidence_{target.isoformat()}.json"

    if target != generated.date():
        historical = read_json(dated_path, {})
        if isinstance(historical, dict) and historical.get("date") == target.isoformat():
            return {**historical, "historical_projection": True}
        return {
            "artifact_type": "environment_evidence_current",
            "version": ENVIRONMENT_EVIDENCE_VERSION,
            "date": target.isoformat(),
            "generated_at": generated.isoformat(timespec="seconds"),
            "status": "historical_unavailable",
            "source": {"endpoint": _endpoint(config, endpoint), "fallback": "none"},
            "latest_attempt": None,
            "last_known_good": None,
            "freshness": {"state": "historical_unavailable", "current": False},
            "decision": {
                "gate": "historical_environment_unavailable",
                "decision_role": "not_projected_from_current_date",
                "can_promote_training": False,
            },
            "guardrail": "Current environment evidence is never projected backward into another date.",
        }

    previous = read_json(current_path, {})
    previous_good = previous.get("last_known_good") if isinstance(previous, dict) else None
    resolved_endpoint = _endpoint(config, endpoint)
    if not refresh:
        return _project(previous if isinstance(previous, dict) else {}, config, target, generated)

    latest_attempt: dict[str, Any] = {
        "attempted_at": generated.isoformat(timespec="seconds"),
        "endpoint": resolved_endpoint,
        "status": "failed",
        "error": None,
        "semantic_issues": [],
        "evidence_id": None,
    }
    last_known_good = previous_good if isinstance(previous_good, dict) else None
    if not resolved_endpoint:
        latest_attempt.update({"status": "unconfigured", "error": "No environment endpoint is configured."})
    else:
        try:
            payload = (fetch_json or _http_json)(resolved_endpoint, timeout)
            issues = _validate_contract(payload, config, generated)
            latest_attempt["semantic_issues"] = issues
            latest_attempt["evidence_id"] = payload.get("evidenceId")
            if issues:
                latest_attempt["status"] = "semantic_invalid"
            else:
                latest_attempt["status"] = "success"
                last_known_good = _normalize(payload)
        except Exception as exc:  # fail-soft inside the same-day stack rebuild
            latest_attempt.update(
                {
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    artifact = _project(
        {
            "latest_attempt": latest_attempt,
            "last_known_good": last_known_good,
        },
        config,
        target,
        generated,
    )
    write_json(current_path, artifact)
    write_json(dated_path, artifact)
    return artifact


def fetch_ride_conditions(
    root: str | Path | None = None,
    *,
    base_url: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    fetch_json: Callable[[str, float], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compatibility command: refresh and persist the bounded v1 MTB environment evidence."""
    return build_environment_evidence(
        root,
        endpoint=base_url,
        timeout=timeout,
        fetch_json=fetch_json,
        refresh=True,
    )
