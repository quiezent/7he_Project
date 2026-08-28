from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from .context import load_context
from .evidence import as_number
from .time_utils import DEFAULT_TIMEZONE, iso_now


RIDE_CONDITIONS_VERSION = "local_ride_conditions_v1"
DEFAULT_TIMEOUT_SECONDS = 5.0
FRESH_MAX_AGE_SECONDS = 720


def _environment_config(context: dict[str, Any]) -> dict[str, Any]:
    return (
        (((context.get("athlete") or {}).get("venue_profiles") or {}).get("bukit_kiara") or {}).get(
            "preferred_environment_report"
        )
        or {}
    )


def _http_json(url: str, timeout: float) -> dict[str, Any]:
    request = Request(
        url,
        headers={"Accept": "application/json", "Cache-Control": "no-cache"},
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object from {url}")
    return payload


def _nested(record: Any, *keys: str) -> dict[str, Any]:
    current = record
    for key in keys:
        if not isinstance(current, dict):
            return {}
        current = current.get(key)
    return current if isinstance(current, dict) else {}


def fetch_ride_conditions(
    root: str | Path | None = None,
    *,
    base_url: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    fetch_json: Callable[[str, float], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Read Clayton's local ride-conditions service without persisting or governing a session."""
    context = load_context(root)
    config = _environment_config(context)
    resolved_base = (
        base_url
        or os.getenv("COACH_RIDE_CONDITIONS_URL")
        or config.get("base_url")
        or config.get("url")
    )
    source = {
        "name": config.get("name") or "Clayton local Bukit Kiara ride-conditions service",
        "base_url": resolved_base,
        "access": "local_http_json",
        "operator": "athlete_managed_local_service",
    }
    guardrail = (
        "Transient environmental evidence may hold or downshift outdoor exposure but cannot promote physical or CNS "
        "readiness, establish indoor air quality, or automatically replace the written session."
    )
    if not resolved_base:
        return {
            "artifact_type": "transient_ride_conditions",
            "version": RIDE_CONDITIONS_VERSION,
            "generated_at": iso_now(DEFAULT_TIMEZONE),
            "status": "unavailable",
            "source": source,
            "error": "No local ride-conditions base URL is configured.",
            "persistence": "none",
            "guardrail": guardrail,
        }

    endpoints = config.get("endpoints") if isinstance(config.get("endpoints"), dict) else {}
    current_path = endpoints.get("current") or "/api/current"
    analysis_path = endpoints.get("analysis") or "/api/analysis?days=28"
    current_url = urljoin(str(resolved_base).rstrip("/") + "/", str(current_path).lstrip("/"))
    analysis_url = urljoin(str(resolved_base).rstrip("/") + "/", str(analysis_path).lstrip("/"))
    getter = fetch_json or _http_json
    source["endpoints"] = {"current": current_url, "analysis": analysis_url}
    try:
        current_payload = getter(current_url, timeout)
        analysis_payload = getter(analysis_url, timeout)
    except Exception as exc:  # noqa: BLE001 - surface endpoint failure without hiding it
        return {
            "artifact_type": "transient_ride_conditions",
            "version": RIDE_CONDITIONS_VERSION,
            "generated_at": iso_now(DEFAULT_TIMEZONE),
            "status": "unavailable",
            "source": source,
            "error": f"{type(exc).__name__}: {exc}",
            "persistence": "none",
            "guardrail": guardrail,
        }

    reading = current_payload.get("reading") if isinstance(current_payload, dict) else None
    if not isinstance(reading, dict):
        return {
            "artifact_type": "transient_ride_conditions",
            "version": RIDE_CONDITIONS_VERSION,
            "generated_at": iso_now(DEFAULT_TIMEZONE),
            "status": "unavailable",
            "source": source,
            "error": "The current endpoint returned no reading object.",
            "persistence": "none",
            "guardrail": guardrail,
        }

    age_seconds = as_number(reading.get("ageSeconds"))
    pm2_5 = as_number(reading.get("pm02"))
    if pm2_5 is None or pm2_5 < 0:
        return {
            "artifact_type": "transient_ride_conditions",
            "version": RIDE_CONDITIONS_VERSION,
            "generated_at": iso_now(DEFAULT_TIMEZONE),
            "status": "unavailable",
            "source": source,
            "error": "The current endpoint returned no usable non-negative PM2.5 concentration.",
            "persistence": "none",
            "guardrail": guardrail,
        }

    collector = (
        current_payload.get("collector")
        if isinstance(current_payload.get("collector"), dict)
        else {}
    )
    analysis_available = analysis_payload.get("available") is not False
    stale = age_seconds is None or age_seconds < 0 or age_seconds > FRESH_MAX_AGE_SECONDS
    if stale:
        status = "stale"
    elif collector.get("error") or not analysis_available:
        status = "degraded"
    else:
        status = "ready"

    air_window = _nested(analysis_payload, "airWindow")
    arrival = _nested(air_window, "arrival")
    trail = _nested(air_window, "trail")
    outlook = _nested(analysis_payload, "outlook")
    weather = _nested(analysis_payload, "weather")
    trail_weather = _nested(weather, "trail")
    preference = _nested(weather, "preference")
    return {
        "artifact_type": "transient_ride_conditions",
        "version": RIDE_CONDITIONS_VERSION,
        "generated_at": iso_now(DEFAULT_TIMEZONE),
        "status": status,
        "source": source,
        "freshness": {
            "observed_at_utc": reading.get("timestamp"),
            "age_seconds": age_seconds,
            "fresh_max_age_seconds": FRESH_MAX_AGE_SECONDS,
            "collector_last_success_utc": collector.get("last_success"),
            "collector_error": collector.get("error"),
        },
        "analysis_availability": {
            "available": analysis_available,
            "reason": analysis_payload.get("reason"),
            "sample_count": as_number(analysis_payload.get("sampleCount")),
        },
        "current": {
            "pm2_5": {"value": pm2_5, "unit": "ug/m3"},
            "pm10": {"value": as_number(reading.get("pm10")), "unit": "ug/m3"},
            "temperature": {"value": as_number(reading.get("atmp")), "unit": "celsius"},
            "relative_humidity": {"value": as_number(reading.get("rhum")), "unit": "percent"},
            "heat_index": {"value": as_number(reading.get("heatindex")), "unit": "celsius"},
        },
        "particle_movement": {
            "direction": outlook.get("momentumDirection"),
            "change_30_min_ug_m3": as_number(air_window.get("change30")),
            "change_60_min_ug_m3": as_number(air_window.get("change60")),
            "state": air_window.get("state"),
            "label": air_window.get("label"),
            "recheck_minutes": as_number(air_window.get("recheckMinutes")),
        },
        "arrival_90_min": {
            "point_ug_m3": as_number(arrival.get("point")),
            "range_low_ug_m3": as_number(arrival.get("rangeLow")),
            "range_high_ug_m3": as_number(arrival.get("rangeHigh")),
            "confidence": arrival.get("confidence"),
            "headline": arrival.get("headline"),
        },
        "on_trail_90_to_210_min": {
            "point_ug_m3": as_number(trail.get("point")),
            "range_low_ug_m3": as_number(trail.get("rangeLow")),
            "range_high_ug_m3": as_number(trail.get("rangeHigh")),
            "peak_upper_ug_m3": as_number(trail.get("peakUpper")),
            "confidence": trail.get("confidence"),
            "headline": trail.get("headline"),
        },
        "trail_weather": {
            "apparent_temperature_max_c": as_number(trail_weather.get("apparentTemperatureMax")),
            "precipitation_probability_max_pct": as_number(
                trail_weather.get("precipitationProbabilityMax")
            ),
            "precipitation_mm": as_number(trail_weather.get("precipitationMm")),
            "ventilation": trail_weather.get("ventilationLabel"),
            "wind_speed_10m_mean_kmh": as_number(trail_weather.get("windSpeed10mMean")),
            "window_preference": preference.get("label"),
            "window_preference_reason": preference.get("reason"),
        },
        "decision_use": (
            "Use these fresh raw concentrations, movement, arrival/on-trail uncertainty and weather alongside symptoms, "
            "session duration, trail consequence and same-day readiness. The server's outlook informs exposure; the head "
            "coach still makes the decision."
        ),
        "persistence": "none",
        "guardrail": guardrail,
    }
