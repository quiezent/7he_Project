from __future__ import annotations

from datetime import date, datetime, timezone
import json
import math
import os
from pathlib import Path
import re
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


def _version_tuple(value: Any) -> tuple[int, int, int] | None:
    if not isinstance(value, str):
        return None
    parts = value.strip().split(".")
    if not parts or any(not part.isdigit() for part in parts[:3]):
        return None
    values = [int(part) for part in parts[:3]]
    values.extend([0] * (3 - len(values)))
    return tuple(values)  # type: ignore[return-value]


def _requires_contract(schema_version: Any) -> bool:
    parsed = _version_tuple(schema_version)
    return parsed is not None and parsed >= (1, 6, 0)


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
        "independent_origin_count": _number(support.get("independentOriginCount")),
        "matched_count": _number(support.get("matchedCount")),
        "matched_independent_origin_count": _number(
            support.get("matchedIndependentOriginCount")
        ),
        "matched_distinct_days": _number(support.get("matchedDistinctDays")),
        "matched_event_count": _number(support.get("matchedEventCount")),
        "distinct_days": _number(support.get("distinctDays")),
        "minimum_required": _number(support.get("minimumRequired")),
    }


def _compact_range(value: Any) -> dict[str, Any]:
    item = value if isinstance(value, dict) else {}
    return {
        "low": _number(item.get("low")),
        "high": _number(item.get("high")),
        "calibrated": item.get("calibrated"),
        "role": item.get("role"),
        "contains_baseline": item.get("containsBaseline"),
        "contains_experimental_projection": item.get("containsExperimentalProjection"),
    }


def _compact_upper(value: Any) -> dict[str, Any]:
    item = value if isinstance(value, dict) else {}
    return {
        "value": _number(item.get("value")),
        "calibrated": item.get("calibrated"),
    }


def _compact_thunderstorm(value: Any) -> dict[str, Any]:
    item = value if isinstance(value, dict) else {}
    metrics = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
    weather_codes = metrics.get("weatherCodes")
    return {
        "level": item.get("level"),
        "rank": _number(item.get("rank")),
        "label": item.get("label"),
        "basis": item.get("basis"),
        "source": item.get("source"),
        "used_for_decision": item.get("usedForDecision"),
        "metrics": {
            "weather_codes": list(weather_codes) if isinstance(weather_codes, list) else [],
            "cape_max_j_kg": _number(metrics.get("capeMaxJkg")),
            "lifted_index_min": _number(metrics.get("liftedIndexMin")),
            "convective_inhibition_min_j_kg": _number(
                metrics.get("convectiveInhibitionMinJkg")
            ),
            "modeled_gust_max_kmh": _number(metrics.get("modeledGustMaxKmh")),
            "modeled_showers_mm": _number(metrics.get("modeledShowersMm")),
            "precipitation_probability_max_pct": _number(
                metrics.get("precipitationProbabilityMaxPct")
            ),
        },
    }


def _target_date_from_utc(value: Any, timezone_name: str | None) -> str | None:
    parsed = _timestamp(value)
    if parsed is None:
        return None
    try:
        zone = ZoneInfo(timezone_name or DEFAULT_TIMEZONE)
    except Exception:
        zone = ZoneInfo(DEFAULT_TIMEZONE)
    return parsed.astimezone(zone).date().isoformat()


def _recheck_at_local(
    value: Any,
    target_date: str | None,
    timezone_name: str | None,
) -> str | None:
    if not isinstance(value, str) or not target_date:
        return None
    match = re.search(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)", value)
    if not match:
        return None
    parsed_date = parse_date(target_date)
    if parsed_date is None:
        return None
    try:
        zone = ZoneInfo(timezone_name or DEFAULT_TIMEZONE)
    except Exception:
        zone = ZoneInfo(DEFAULT_TIMEZONE)
    return datetime(
        parsed_date.year,
        parsed_date.month,
        parsed_date.day,
        int(match.group(1)),
        int(match.group(2)),
        tzinfo=zone,
    ).isoformat(timespec="minutes")


def _compact_clearance_event(value: Any) -> dict[str, Any]:
    event = value if isinstance(value, dict) else {}
    conditioned = (
        event.get("eventConditionedForecast")
        if isinstance(event.get("eventConditionedForecast"), dict)
        else {}
    )
    return {
        "detected": event.get("detected"),
        "state": event.get("state"),
        "kind": event.get("kind"),
        "label": event.get("label"),
        "age_minutes": _number(event.get("ageMinutes")),
        "last_evidence_at_utc": event.get("lastEvidenceAt"),
        "detection_mode": event.get("detectionMode"),
        "rain_support": event.get("rainSupport"),
        "dry_air_mass_support": event.get("dryAirMassSupport"),
        "event_conditioned_forecast": {
            "available": conditioned.get("available"),
            "status": conditioned.get("status"),
            "completed_prior_events": _number(conditioned.get("completedPriorEvents")),
            "completed_prior_distinct_days": _number(
                conditioned.get("completedPriorDistinctDays")
            ),
            "minimum_prior_events": _number(conditioned.get("minimumPriorEvents")),
            "minimum_prior_distinct_days": _number(
                conditioned.get("minimumPriorDistinctDays")
            ),
            "event_at_utc": conditioned.get("eventAt"),
            "event_age_minutes": _number(conditioned.get("eventAgeMinutes")),
            "experimental": conditioned.get("experimental"),
        },
    }


def _compact_particle_forecast(value: Any) -> dict[str, Any]:
    forecast = value if isinstance(value, dict) else {}
    return {
        "available": forecast.get("available"),
        "used_learned_model_for_decision": forecast.get("usedLearnedModelForDecision"),
        "forecast_state": forecast.get("forecastState"),
        "point_role": forecast.get("pointRole"),
        "method": forecast.get("method"),
        "confidence": forecast.get("confidence"),
        "confidence_detail": forecast.get("confidenceDetail"),
        "baseline_mean_pm2_5_ug_m3": _number(forecast.get("baselineMeanPm25UgM3")),
        "persistence_anchor_role": forecast.get("persistenceAnchorRole"),
        "projected_mean_pm2_5_ug_m3": _number(forecast.get("projectedMeanPm25UgM3")),
        "projected_peak_pm2_5_ug_m3": _number(forecast.get("projectedPeakPm25UgM3")),
        "approximate": forecast.get("approximate"),
        "validation_state": forecast.get("validationState"),
        "used_for_validated_decision": forecast.get("usedForValidatedDecision"),
        "mean_range_pm2_5_ug_m3": _compact_range(forecast.get("meanRangePm25UgM3")),
        "upper_peak_pm2_5_ug_m3": _number(forecast.get("upperPeakPm25UgM3")),
        "uncertainty_method": forecast.get("uncertaintyMethod"),
        "lead_hours": _number(forecast.get("leadHours")),
        "duration_hours": _number(forecast.get("durationHours")),
        "support": _compact_support(forecast.get("support")),
    }


def _compact_airflow(value: Any) -> dict[str, Any]:
    airflow = value if isinstance(value, dict) else {}
    return {
        "context": airflow.get("context"),
        "modeled_wind_10m_mean_kmh": _number(airflow.get("modeledWind10mMeanKmh")),
        "modeled_wind_gust_10m_max_kmh": _number(
            airflow.get("modeledWindGust10mMaxKmh")
        ),
        "modeled_wind_180m_mean_kmh": _number(airflow.get("modeledWind180mMeanKmh")),
        "modeled_direction_180m": airflow.get("modeledDirection180m"),
        "used_for_particle_forecast": airflow.get("usedForParticleForecast"),
    }


def _compact_weather_period(value: Any, timezone_name: str | None) -> dict[str, Any]:
    forecast = value if isinstance(value, dict) else {}
    start_at = forecast.get("startAt")
    return {
        "available": forecast.get("available"),
        "start_at_utc": start_at,
        "end_at_utc": forecast.get("endAt"),
        "target_date": _target_date_from_utc(start_at, timezone_name),
        "modeled_session": forecast.get("modeledSession"),
        "source_point_count": _number(forecast.get("sourcePointCount")),
        "precipitation_source_point_count": _number(
            forecast.get("precipitationSourcePointCount")
        ),
        "precipitation_equivalent_hours": _number(
            forecast.get("precipitationEquivalentHours")
        ),
        "apparent_temperature_max_c": _number(forecast.get("apparentTemperatureMaxC")),
        "temperature_max_c": _number(forecast.get("temperatureMaxC")),
        "precipitation_probability_max_pct": _number(
            forecast.get("precipitationProbabilityMaxPct")
        ),
        "precipitation_mm": _number(forecast.get("precipitationMm")),
        "rain_used_for_comparison": forecast.get("rainUsedForComparison"),
        "rain_signal": forecast.get("rainSignal"),
        "thunderstorm": _compact_thunderstorm(forecast.get("thunderstorm")),
        "airflow": _compact_airflow(forecast.get("airflow")),
    }


def _compact_ride_window(value: Any, timezone_name: str | None) -> dict[str, Any]:
    window = value if isinstance(value, dict) else {}
    forecast = window.get("weatherForecast") if isinstance(window.get("weatherForecast"), dict) else {}
    weather_forecast = _compact_weather_period(forecast, timezone_name)
    target_date = weather_forecast.get("target_date")
    recheck = window.get("recheck")
    return {
        "target_day": window.get("targetDay"),
        "target_date": target_date,
        "ride_window": window.get("rideWindow"),
        "modeled_session": window.get("modeledSession"),
        "lead_hours": _number(window.get("leadHours")),
        "active": window.get("active"),
        "current_conditions_applicable": window.get("currentConditionsApplicable"),
        "recheck": recheck,
        "recheck_at_local": _recheck_at_local(recheck, target_date, timezone_name),
        "confidence": window.get("confidence"),
        "particle_forecast": _compact_particle_forecast(window.get("particleForecast")),
        "weather_forecast": weather_forecast,
    }


def _compact_ride_windows(value: Any, timezone_name: str | None) -> dict[str, Any]:
    windows = value if isinstance(value, dict) else {}
    comparison = windows.get("comparison") if isinstance(windows.get("comparison"), dict) else {}
    model = comparison.get("particleModel") if isinstance(comparison.get("particleModel"), dict) else {}
    return {
        "comparison": {
            "status": comparison.get("status"),
            "preferred_window": comparison.get("preferredWindow"),
            "historical_lower_exposure_window": comparison.get(
                "historicalLowerExposureWindow"
            ),
            "relative_only": comparison.get("relativeOnly"),
            "ride_approval": comparison.get("rideApproval"),
            "policy_id": comparison.get("policyId"),
            "aggressive_experimental": comparison.get("aggressiveExperimental"),
            "verdict": comparison.get("verdict"),
            "reason": comparison.get("reason"),
            "confidence": comparison.get("confidence"),
            "particle_model": {
                "status": model.get("status"),
                "application_status": model.get("applicationStatus"),
                "used_for_decision": model.get("usedForDecision"),
                "used_for_comparison": model.get("usedForComparison"),
                "model_version": model.get("modelVersion"),
                "aggressive_mode": model.get("aggressiveMode"),
                "validation": {
                    "scored_window_count": _number(model.get("scoredWindowCount")),
                    "minimum_scored_windows": _number(model.get("minimumScoredWindows")),
                    "distinct_days": _number(model.get("distinctDays")),
                    "minimum_distinct_days": _number(model.get("minimumDistinctDays")),
                },
                "note": model.get("note"),
            },
        },
        "morning": _compact_ride_window(windows.get("morning"), timezone_name),
        "afternoon": _compact_ride_window(windows.get("afternoon"), timezone_name),
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
    previous_good: dict[str, Any] | None = None,
) -> list[str]:
    issues: list[str] = []
    schema_version = str(payload.get("schemaVersion") or "")
    parsed_schema = _version_tuple(schema_version)
    if parsed_schema is None:
        issues.append("schema_version_invalid")
    elif str(parsed_schema[0]) != SUPPORTED_SCHEMA_MAJOR:
        issues.append("unsupported_schema_major")

    contract = payload.get("contract") if isinstance(payload.get("contract"), dict) else {}
    if contract and str(contract.get("schemaVersion") or "") != schema_version:
        issues.append("contract_schema_mismatch")
    if _requires_contract(schema_version):
        if not contract:
            issues.append("contract_required")
        else:
            if not str(contract.get("revision") or "").strip():
                issues.append("contract_revision_missing")
            revision_role = str(contract.get("revisionRole") or "").strip()
            if not revision_role:
                issues.append("contract_revision_role_missing")
            elif revision_role != "contract_and_documentation_only":
                issues.append("contract_revision_role_unsupported")
            if (_number(contract.get("evidencePollSeconds")) or 0) <= 0:
                issues.append("contract_poll_seconds_invalid")
            resources = [
                contract.get("openapi"),
                contract.get("jsonSchema"),
                contract.get("documentation"),
            ]
            refresh_policy = (
                contract.get("refreshPolicy")
                if isinstance(contract.get("refreshPolicy"), dict)
                else {}
            )
            policy_resources = refresh_policy.get("resources")
            if any(not isinstance(value, str) or not value for value in resources):
                issues.append("contract_resources_invalid")
            elif not isinstance(policy_resources, list) or any(
                resource not in policy_resources for resource in resources
            ):
                issues.append("contract_refresh_resources_mismatch")
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
    else:
        previous_observed_at = _timestamp(
            _nested(previous_good or {}, "observation").get("observed_at_utc")
        )
        if (
            previous_observed_at is not None
            and observed_at.astimezone(timezone.utc)
            < previous_observed_at.astimezone(timezone.utc)
        ):
            issues.append("observation_timestamp_regression")

    pm2_5 = _number(_nested(payload, "observation", "particles").get("pm25UgM3"))
    if pm2_5 is None or pm2_5 < 0:
        issues.append("pm2_5_missing_or_invalid")
    return issues


def _normalize(payload: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    contract = _nested(payload, "contract")
    refresh_policy = _nested(contract, "refreshPolicy")
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
    particle_forecast_provenance = _nested(provenance, "particleForecast")
    weather_reference = _nested(provenance, "weatherReference")
    logistics = _nested(payload, "logistics")
    typical_duration = _nested(logistics, "typicalTrailDurationMinutes")
    exposure_window = _nested(logistics, "modeledExposureWindowMinutes")
    location = _nested(payload, "location")
    timezone_name = str(location.get("timezone") or config.get("timezone") or DEFAULT_TIMEZONE)
    nearby_storm = _nested(weather, "nearbyStorm")
    decision_policy = _nested(weather, "decisionPolicy")

    return {
        "contract": {
            "schema_version": contract.get("schemaVersion"),
            "revision": contract.get("revision"),
            "revision_role": contract.get("revisionRole"),
            "evidence_poll_seconds": _number(contract.get("evidencePollSeconds")),
            "resources": {
                "openapi": contract.get("openapi"),
                "json_schema": contract.get("jsonSchema"),
                "documentation": contract.get("documentation"),
            },
            "refresh_policy": {
                "mode": refresh_policy.get("mode"),
                "use_if_none_match": refresh_policy.get("useIfNoneMatch"),
                "reload_when": list(refresh_policy.get("reloadWhen") or []),
                "resources": list(refresh_policy.get("resources") or []),
            },
        },
        "identity": {
            "schema_version": payload.get("schemaVersion"),
            "kind": payload.get("kind"),
            "evidence_id": payload.get("evidenceId"),
            "server_generated_at": payload.get("generatedAt"),
        },
        "location": {
            "name": location.get("name"),
            "latitude": _number(location.get("latitude")),
            "longitude": _number(location.get("longitude")),
            "timezone": location.get("timezone"),
            "sensor_location_id": sensor.get("locationId"),
        },
        "scope": {
            "role": "direct_venue_environment_evidence",
            "venue_keys": list(config.get("automatic_gate_venue_keys") or ["bukit_kiara"]),
            "location": {
                "name": location.get("name"),
                "latitude": _number(location.get("latitude")),
                "longitude": _number(location.get("longitude")),
                "timezone": location.get("timezone"),
            },
            "transfer_to_unlisted_venues": False,
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
            "sustained_improvement": nowcast.get("sustainedImprovement"),
            "minimum_since_event_ug_m3": _number(nowcast.get("minimumSinceEventUgM3")),
            "recheck_minutes": _number(nowcast.get("recheckMinutes")),
            "analysis_bucket_end_at_utc": nowcast.get("analysisBucketEndAt"),
            "analysis_bucket_minutes": _number(nowcast.get("analysisBucketMinutes")),
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
                "based_on_observed_at_utc": arrival.get("basedOnObservedAt"),
                "headline": arrival.get("headline"),
                "persistence_anchor_pm2_5_ug_m3": _number(
                    arrival.get("persistenceAnchorPm25UgM3")
                    if arrival.get("persistenceAnchorPm25UgM3") is not None
                    else arrival.get("baselinePm25UgM3")
                ),
                "persistence_anchor_role": arrival.get("persistenceAnchorRole"),
                "model_feature_anchor_pm2_5_ug_m3": _number(
                    arrival.get("modelFeatureAnchorPm25UgM3")
                ),
                "projected_pm2_5_ug_m3": _number(arrival.get("projectedPm25UgM3")),
                "baseline_pm2_5_ug_m3": _number(arrival.get("baselinePm25UgM3")),
                "point_role": arrival.get("pointRole"),
                "forecast_state": arrival.get("forecastState"),
                "approximate": arrival.get("approximate"),
                "validation_state": arrival.get("validationState"),
                "likely_range_pm2_5_ug_m3": _compact_range(arrival_range),
                "decision_envelope_pm2_5_ug_m3": _compact_range(
                    arrival.get("decisionEnvelopePm25UgM3")
                ),
                "upper_pm2_5_ug_m3": _compact_upper(arrival.get("upperPm25UgM3")),
                "decision_upper_pm2_5_ug_m3": _number(
                    arrival.get("decisionUpperPm25UgM3")
                ),
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
                "based_on_observed_at_utc": trail.get("basedOnObservedAt"),
                "headline": trail.get("headline"),
                "persistence_anchor_pm2_5_ug_m3": _number(
                    trail.get("persistenceAnchorPm25UgM3")
                    if trail.get("persistenceAnchorPm25UgM3") is not None
                    else trail.get("baselineMeanPm25UgM3")
                ),
                "persistence_anchor_role": trail.get("persistenceAnchorRole"),
                "model_feature_anchor_pm2_5_ug_m3": _number(
                    trail.get("modelFeatureAnchorPm25UgM3")
                ),
                "projected_mean_pm2_5_ug_m3": _number(
                    trail.get("projectedMeanPm25UgM3")
                ),
                "projected_peak_pm2_5_ug_m3": _number(
                    trail.get("projectedPeakPm25UgM3")
                ),
                "baseline_mean_pm2_5_ug_m3": _number(trail.get("baselineMeanPm25UgM3")),
                "point_role": trail.get("pointRole"),
                "forecast_state": trail.get("forecastState"),
                "approximate": trail.get("approximate"),
                "validation_state": trail.get("validationState"),
                "likely_mean_range_pm2_5_ug_m3": _compact_range(trail_range),
                "decision_mean_envelope_pm2_5_ug_m3": _compact_range(
                    trail.get("decisionMeanEnvelopePm25UgM3")
                ),
                "upper_mean_pm2_5_ug_m3": _compact_upper(
                    trail.get("upperMeanPm25UgM3")
                ),
                "upper_peak_pm2_5_ug_m3": _compact_upper(trail_peak),
                "decision_peak_risk_marker_pm2_5_ug_m3": _number(
                    trail.get("decisionPeakRiskMarkerPm25UgM3")
                ),
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
            "trail_period": _compact_weather_period(trail_weather, timezone_name),
            "nearby_storm": {
                "available": nearby_storm.get("available"),
                "fresh": nearby_storm.get("fresh"),
                "age_minutes": _number(nearby_storm.get("ageMinutes")),
                "present_weather": nearby_storm.get("presentWeather"),
                "level": nearby_storm.get("level"),
                "rank": _number(nearby_storm.get("rank")),
                "label": nearby_storm.get("label"),
                "basis": nearby_storm.get("basis"),
                "source": nearby_storm.get("source"),
                "used_for_decision": nearby_storm.get("usedForDecision"),
            },
            "decision_policy": {
                "ordinary_rain_used_for_comparison": decision_policy.get(
                    "ordinaryRainUsedForComparison"
                ),
                "thunderstorm_used_for_comparison": decision_policy.get(
                    "thunderstormUsedForComparison"
                ),
                "heat_used_as_tie_breaker": decision_policy.get("heatUsedAsTieBreaker"),
            },
            "particle_relationship_validated": weather.get("particleRelationshipValidated"),
        },
        "clearance_event": _compact_clearance_event(payload.get("clearanceEvent")),
        "ride_windows": _compact_ride_windows(payload.get("rideWindows"), timezone_name),
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
                "validation": {
                    "supported": _nested(forecast, "validation").get("supported"),
                    "origin_count": _number(
                        _nested(forecast, "validation").get("originCount")
                    ),
                    "minimum_origins": _number(
                        _nested(forecast, "validation").get("minimumOrigins")
                    ),
                },
            },
            "particle_forecast": {
                "provider": particle_forecast_provenance.get("provider"),
                "model_version": particle_forecast_provenance.get("modelVersion"),
                "used_for_decision": particle_forecast_provenance.get("usedForDecision"),
                "used_for_comparison": particle_forecast_provenance.get(
                    "usedForComparison"
                ),
                "fetched_at_utc": particle_forecast_provenance.get("fetchedAt"),
                "error": particle_forecast_provenance.get("error"),
                "validation": {
                    "mode": _nested(particle_forecast_provenance, "validation").get("mode"),
                    "supported": _nested(particle_forecast_provenance, "validation").get(
                        "supported"
                    ),
                    "scored_window_count": _number(
                        _nested(particle_forecast_provenance, "validation").get(
                            "scoredWindowCount"
                        )
                    ),
                    "minimum_scored_windows": _number(
                        _nested(particle_forecast_provenance, "validation").get(
                            "minimumScoredWindows"
                        )
                    ),
                    "distinct_days": _number(
                        _nested(particle_forecast_provenance, "validation").get(
                            "distinctDays"
                        )
                    ),
                    "minimum_distinct_days": _number(
                        _nested(particle_forecast_provenance, "validation").get(
                            "minimumDistinctDays"
                        )
                    ),
                },
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
        _number(_nested(arrival, "decision_envelope_pm2_5_ug_m3").get("high")),
        _number(_nested(trail, "decision_mean_envelope_pm2_5_ug_m3").get("high")),
        _number(arrival.get("decision_upper_pm2_5_ug_m3")),
        _number(_nested(trail, "upper_peak_pm2_5_ug_m3").get("value")),
        _number(trail.get("decision_peak_risk_marker_pm2_5_ug_m3")),
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
    rain_used_for_comparison = weather.get("rain_used_for_comparison") is True
    thunderstorm = _nested(weather, "thunderstorm")
    nearby_storm = _nested(evidence, "weather", "nearby_storm")
    thunderstorm_level = str(thunderstorm.get("level") or "none").lower()
    nearby_storm_level = str(nearby_storm.get("level") or "none").lower()
    structured_thunderstorm_hold = (
        (
            thunderstorm_level in {"likely", "severe"}
            and thunderstorm.get("used_for_decision") is True
        )
        or (
            nearby_storm_level in {"likely", "severe"}
            and nearby_storm.get("used_for_decision") is True
            and nearby_storm.get("fresh") is True
        )
    )
    reason_codes: list[str] = []
    modifiers: list[str] = []
    recheck_minutes = _number(nowcast.get("recheck_minutes"))

    if heat_max is not None and heat_max >= 40:
        modifiers.append("strong_heat_modifier")
    elif heat_max is not None and heat_max >= 38:
        modifiers.append("heat_load_modifier")
    rain_is_material = (
        (rain_probability is not None and rain_probability >= 70)
        or (rain_mm is not None and rain_mm >= 1)
        or rain_signal.lower() == "rain may disrupt the ride"
    )
    if rain_is_material and not rain_used_for_comparison:
        modifiers.append("rain_context_only")
    elif rain_is_material:
        modifiers.append("rain_disruption_modifier")
    elif (
        (rain_probability is not None and rain_probability >= 40)
        or "shower" in rain_signal.lower()
    ):
        modifiers.append("rain_watch")
    if _nested(evidence, "evidence_quality").get("state") not in {None, "good", "ready"}:
        modifiers.append("evidence_quality_limited")

    if state in {"stale", "retained_current"}:
        if current_pm is not None and current_pm > bands["hazardous_above"]:
            gate = "retained_all_outdoor_closure_pending_refresh"
            severity = "red"
            reason_codes.append("retained_hazardous_value_preserves_all_outdoor_restriction")
        elif current_pm is not None and current_pm >= bands["poor_from"]:
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
    elif structured_thunderstorm_hold:
        gate = "hold_and_recheck"
        severity = "yellow"
        reason_codes.append("structured_thunderstorm_likely_or_severe")
    elif forecast_upper is not None and forecast_upper >= bands["poor_from"]:
        gate = "hold_and_recheck"
        severity = "yellow"
        reason_codes.append("conservative_uncertain_exposure_envelope_crosses_poor")
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
        "experimental_points_used_for_deterministic_closure": False,
        "thunderstorm_level": thunderstorm_level,
        "nearby_storm_level": nearby_storm_level,
        "ordinary_rain_used_for_comparison": rain_used_for_comparison,
    }


def _contract_state(
    evidence: dict[str, Any] | None,
    latest_attempt: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    contract = _nested(evidence or {}, "contract")
    configured_contract = _nested(config, "schema_contract")
    accepted_schema = contract.get("schema_version") or _nested(
        evidence or {}, "identity"
    ).get("schema_version")
    accepted_revision = contract.get("revision")
    configured_schema = config.get("schema_version") or configured_contract.get(
        "schema_version"
    )
    configured_revision = config.get("contract_revision") or configured_contract.get(
        "last_verified_revision"
    )
    drift = list(latest_attempt.get("contract_drift") or [])
    semantic_issues = list(latest_attempt.get("semantic_issues") or [])
    contract_issue = any(
        issue.startswith("contract_") or issue in {"unsupported_schema_major", "schema_version_invalid"}
        for issue in semantic_issues
    )
    revalidation_needed = bool(
        latest_attempt.get("contract_revalidation_required") is True or contract_issue
    )
    if contract_issue:
        state = "invalid"
    elif revalidation_needed:
        state = "changed_revalidation_needed"
    elif drift:
        state = "accepted_after_configured_revalidation"
    elif accepted_revision:
        state = "accepted"
    elif accepted_schema:
        state = "legacy_same_major"
    else:
        state = "unavailable"
    return {
        "state": state,
        "schema_version": accepted_schema,
        "revision": accepted_revision,
        "revision_role": contract.get("revision_role"),
        "evidence_poll_seconds": contract.get("evidence_poll_seconds"),
        "resources": contract.get("resources") or {},
        "configured_schema_version": configured_schema,
        "configured_revision": configured_revision,
        "drift": drift,
        "revalidation_needed": revalidation_needed,
        "freshness_independent": True,
    }


def _contract_acceptance(
    payload: dict[str, Any],
    previous_good: dict[str, Any] | None,
    config: dict[str, Any],
) -> dict[str, Any]:
    schema_version = str(payload.get("schemaVersion") or "")
    if not _requires_contract(schema_version):
        return {
            "drift": [],
            "revalidation_required": False,
            "configured_contract_matches_live": None,
        }
    contract = _nested(payload, "contract")
    live_revision = contract.get("revision")
    configured_contract = _nested(config, "schema_contract")
    configured_schema = config.get("schema_version") or configured_contract.get(
        "schema_version"
    )
    configured_revision = config.get("contract_revision") or configured_contract.get(
        "last_verified_revision"
    )
    previous_schema = _nested(previous_good or {}, "contract").get(
        "schema_version"
    ) or _nested(previous_good or {}, "identity").get("schema_version")
    previous_revision = _nested(previous_good or {}, "contract").get("revision")
    drift: list[str] = []
    if configured_schema and schema_version != configured_schema:
        drift.append("live_schema_differs_from_configured")
    if configured_revision and live_revision != configured_revision:
        drift.append("live_revision_differs_from_configured")
    if previous_schema and schema_version != previous_schema:
        drift.append("live_schema_differs_from_last_accepted")
    if previous_revision and live_revision != previous_revision:
        drift.append("live_revision_differs_from_last_accepted")
    configured_matches_live = bool(
        configured_schema == schema_version
        and configured_revision
        and configured_revision == live_revision
    )
    if not configured_schema or not configured_revision:
        drift.append("live_contract_not_verified_in_config")
    revalidation_required = bool(
        not configured_matches_live
        or (
            previous_good
            and (previous_schema != schema_version or previous_revision != live_revision)
            and not configured_matches_live
        )
    )
    return {
        "drift": list(dict.fromkeys(drift)),
        "revalidation_required": revalidation_required,
        "configured_contract_matches_live": configured_matches_live,
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
    contract_state = _contract_state(last_known_good, latest_attempt, config)
    source_endpoint = latest_attempt.get("endpoint") or _endpoint(config)
    return {
        "artifact_type": "environment_evidence_current",
        "version": ENVIRONMENT_EVIDENCE_VERSION,
        "date": target_date.isoformat(),
        "generated_at": now.isoformat(timespec="seconds"),
        "status": freshness.get("state"),
        "source": {
            "name": config.get("name") or "Clayton local Bukit Kiara environment evidence",
            "endpoint": source_endpoint,
            "endpoint_source": latest_attempt.get("endpoint_source") or "config",
            "access": "athlete_managed_local_http_json",
            "fallback": "none",
        },
        "latest_attempt": latest_attempt or None,
        "contract_state": contract_state,
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


def _future_projection(
    stored: dict[str, Any],
    config: dict[str, Any],
    target_date: date,
    observation_date: date,
    now: datetime,
) -> dict[str, Any]:
    artifact = _project(stored, config, target_date, now)
    latest = artifact.get("last_known_good") or {}
    windows = latest.get("ride_windows") if isinstance(latest, dict) else {}
    windows = windows if isinstance(windows, dict) else {}
    matching_windows = [
        name
        for name in ("morning", "afternoon")
        if isinstance(windows.get(name), dict)
        and windows[name].get("target_date") == target_date.isoformat()
    ]
    artifact["decision"] = {
        "gate": (
            "future_exact_window_evidence_only"
            if matching_windows
            else "future_environment_recheck"
        ),
        "severity": "yellow",
        "reason_codes": [
            (
                "exact_target_dated_ride_windows_available"
                if matching_windows
                else "no_exact_target_dated_ride_window"
            )
        ],
        "reason": (
            "Use only the exact target-dated ride-window evidence and its recheck; the current "
            "observation is context and cannot clear or close this future session."
        ),
        "decision_role": "future_exact_window_only_no_current_observation_projection",
        "can_promote_training": False,
        "current_observation_applicable_to_target": False,
        "current_pm_used_for_clearance_or_closure": False,
        "matching_window_names": matching_windows,
    }
    artifact["future_target"] = {
        "target_date": target_date.isoformat(),
        "source_observation_date": observation_date.isoformat(),
        "exact_target_window_names": matching_windows,
        "current_observation_projected": False,
        "future_observation_artifact_written": False,
    }
    artifact["persistence"] = {
        **(artifact.get("persistence") or {}),
        "dated_snapshot": f"snapshots/environment_evidence_{observation_date.isoformat()}.json",
        "future_dated_snapshot_written": False,
    }
    return artifact


def _within_advertised_poll_interval(
    previous: dict[str, Any],
    resolved_endpoint: str | None,
    now: datetime,
    config: dict[str, Any],
) -> bool:
    attempt = previous.get("latest_attempt") if isinstance(previous, dict) else {}
    attempt = attempt if isinstance(attempt, dict) else {}
    evidence = previous.get("last_known_good") if isinstance(previous, dict) else {}
    evidence = evidence if isinstance(evidence, dict) else {}
    if attempt.get("endpoint") != resolved_endpoint:
        return False
    attempted_at = _timestamp(attempt.get("attempted_at"))
    if attempted_at is None:
        return False
    poll_seconds = _number(_nested(evidence, "contract").get("evidence_poll_seconds"))
    poll_seconds = poll_seconds or _number(
        _nested(evidence, "provenance", "sensor").get("poll_seconds")
    )
    poll_seconds = poll_seconds or _number(
        _nested(config, "contract_discovery").get("evidence_poll_seconds")
    )
    poll_seconds = poll_seconds or _number(config.get("evidence_poll_seconds"))
    if poll_seconds is None or poll_seconds <= 0:
        return False
    age = (now.astimezone(timezone.utc) - attempted_at.astimezone(timezone.utc)).total_seconds()
    return 0 <= age < poll_seconds


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
    observation_date = generated.date()
    dated_path = snapshots_dir(root) / f"environment_evidence_{target.isoformat()}.json"
    current_dated_path = (
        snapshots_dir(root) / f"environment_evidence_{observation_date.isoformat()}.json"
    )

    if target < observation_date:
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

    future_target = target > observation_date
    previous = read_json(current_path, {})
    previous_good = previous.get("last_known_good") if isinstance(previous, dict) else None
    resolved_endpoint = _endpoint(config, endpoint)
    if not refresh:
        stored = previous if isinstance(previous, dict) else {}
        return (
            _future_projection(stored, config, target, observation_date, generated)
            if future_target
            else _project(stored, config, target, generated)
        )
    if (
        endpoint is None
        and fetch_json is None
        and _within_advertised_poll_interval(
            previous if isinstance(previous, dict) else {},
            resolved_endpoint,
            generated,
            config,
        )
    ):
        stored = previous if isinstance(previous, dict) else {}
        current_projection = _project(
            stored,
            config,
            observation_date,
            generated,
        )
        write_json(current_path, current_projection)
        write_json(current_dated_path, current_projection)
        return (
            _future_projection(stored, config, target, observation_date, generated)
            if future_target
            else current_projection
        )

    latest_attempt: dict[str, Any] = {
        "attempted_at": generated.isoformat(timespec="seconds"),
        "endpoint": resolved_endpoint,
        "endpoint_source": (
            "explicit_override"
            if endpoint
            else "environment_override"
            if os.getenv("COACH_RIDE_CONDITIONS_URL")
            else "config"
        ),
        "status": "failed",
        "error": None,
        "semantic_issues": [],
        "evidence_id": None,
        "schema_version": None,
        "contract_schema_version": None,
        "contract_revision": None,
        "contract_drift": [],
        "contract_revalidation_required": False,
        "configured_contract_matches_live": None,
        "previous_accepted_schema_version": (
            _nested(previous_good or {}, "contract").get("schema_version")
            or _nested(previous_good or {}, "identity").get("schema_version")
        ),
        "previous_accepted_revision": _nested(previous_good or {}, "contract").get(
            "revision"
        ),
    }
    last_known_good = previous_good if isinstance(previous_good, dict) else None
    if not resolved_endpoint:
        latest_attempt.update({"status": "unconfigured", "error": "No environment endpoint is configured."})
    else:
        try:
            payload = (fetch_json or _http_json)(resolved_endpoint, timeout)
            payload_contract = _nested(payload, "contract")
            latest_attempt["schema_version"] = payload.get("schemaVersion")
            latest_attempt["contract_schema_version"] = payload_contract.get("schemaVersion")
            latest_attempt["contract_revision"] = payload_contract.get("revision")
            issues = _validate_contract(payload, config, generated, last_known_good)
            acceptance = _contract_acceptance(payload, last_known_good, config)
            latest_attempt["contract_drift"] = acceptance["drift"]
            latest_attempt["contract_revalidation_required"] = acceptance[
                "revalidation_required"
            ]
            latest_attempt["configured_contract_matches_live"] = acceptance[
                "configured_contract_matches_live"
            ]
            latest_attempt["semantic_issues"] = issues
            latest_attempt["evidence_id"] = payload.get("evidenceId")
            if issues:
                latest_attempt["status"] = "semantic_invalid"
            elif acceptance["revalidation_required"]:
                latest_attempt["status"] = "contract_revalidation_required"
                latest_attempt["error"] = (
                    "Live contract schema or revision changed; revalidate the advertised "
                    "contract resources before accepting new evidence."
                )
            else:
                latest_attempt["status"] = "success"
                last_known_good = _normalize(payload, config)
        except Exception as exc:  # fail-soft inside the same-day stack rebuild
            latest_attempt.update(
                {
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    stored = {
        "latest_attempt": latest_attempt,
        "last_known_good": last_known_good,
    }
    current_artifact = _project(
        stored,
        config,
        observation_date,
        generated,
    )
    write_json(current_path, current_artifact)
    write_json(current_dated_path, current_artifact)
    if future_target:
        return _future_projection(stored, config, target, observation_date, generated)
    return current_artifact


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
