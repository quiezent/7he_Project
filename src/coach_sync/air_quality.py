from __future__ import annotations

from datetime import date, datetime, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from .context import load_context
from .io import read_json, write_json
from .paths import snapshots_dir
from .time_utils import DEFAULT_TIMEZONE, now_local, parse_date


ARTIFACT_NAME = "air_quality_current.json"
LEDGER_NAME = "air_quality_ledger.json"
LEDGER_RETENTION = 200
DEFAULT_TIMEOUT_SECONDS = 8.0
DEFAULT_FRESH_MAX_MIN = 10.0
DEFAULT_USABLE_MAX_MIN = 60.0
DEFAULT_FUTURE_TOLERANCE_MIN = 2.0
DEFAULT_TREND_WINDOW_MINUTES = 60.0
DEFAULT_TREND_MINIMUM_SAMPLES = 6
DEFAULT_TREND_MINIMUM_SPAN_MINUTES = 25.0
DEFAULT_TREND_MAXIMUM_GAP_MINUTES = 10.0


class AirQualityFetchError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        http_status: int | None = None,
        retry_after: str | None = None,
    ) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.retry_after = retry_after


def _config(context: dict) -> dict:
    value = (
        context.get("athlete", {})
        .get("venue_profiles", {})
        .get("bukit_kiara", {})
        .get("air_quality_proxy", {})
    )
    return value if isinstance(value, dict) else {}


def _config_fingerprint(config: dict) -> str | None:
    if not config.get("endpoint") or config.get("location_id") is None:
        return None
    selected = {
        "endpoint": str(config.get("endpoint")),
        "location_id": str(config.get("location_id")),
        "station_timezone": str(
            config.get("station_timezone") or DEFAULT_TIMEZONE
        ),
    }
    return sha256(
        json.dumps(selected, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _aware_now(now: datetime | None, timezone_name: str) -> datetime:
    if now is None:
        return now_local(timezone_name)
    if now.tzinfo is None:
        return now.replace(tzinfo=ZoneInfo(timezone_name))
    return now.astimezone(ZoneInfo(timezone_name))


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _http_fetch(url: str, timeout_seconds: float) -> tuple[int, dict, dict]:
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "ClaytonMTBCoachStack/0.1",
        },
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            status = int(getattr(response, "status", 200))
            body = response.read().decode("utf-8")
            headers = dict(response.headers.items())
    except HTTPError as exc:
        raise AirQualityFetchError(
            f"AirGradient returned HTTP {exc.code}.",
            http_status=exc.code,
            retry_after=exc.headers.get("Retry-After") if exc.headers else None,
        ) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise AirQualityFetchError("AirGradient request failed.") from exc
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise AirQualityFetchError("AirGradient returned malformed JSON.", http_status=status) from exc
    if not isinstance(payload, dict):
        raise AirQualityFetchError(
            "AirGradient response was not a JSON object.",
            http_status=status,
        )
    return status, payload, headers


def _call_fetcher(
    fetcher: Callable[[str, float], Any],
    url: str,
    timeout_seconds: float,
) -> tuple[int, dict, dict]:
    result = fetcher(url, timeout_seconds)
    if isinstance(result, tuple):
        if len(result) == 3:
            status, payload, headers = result
        elif len(result) == 2:
            status, payload = result
            headers = {}
        else:
            raise AirQualityFetchError("Air-quality fetcher returned an invalid tuple.")
    else:
        status, payload, headers = 200, result, {}
    if not isinstance(payload, dict):
        raise AirQualityFetchError(
            "Air-quality fetcher returned a non-object payload.",
            http_status=int(status) if status is not None else None,
        )
    return int(status), payload, dict(headers or {})


def _selected_measurement(
    payload: dict,
    config: dict,
    fetched_at: datetime,
) -> tuple[dict | None, str | None]:
    expected_location_id = config.get("location_id")
    location_id = payload.get("locationId")
    if str(location_id) != str(expected_location_id):
        return None, "location_id_mismatch"

    expected_timezone = str(config.get("station_timezone") or DEFAULT_TIMEZONE)
    payload_timezone = payload.get("timezone")
    if payload_timezone and str(payload_timezone) != expected_timezone:
        return None, "station_timezone_mismatch"

    if payload.get("offline") is not False:
        return None, "station_offline_or_state_missing"

    pm25 = _number(payload.get("pm02"))
    if pm25 is None or pm25 < 0:
        return None, "pm02_missing_or_invalid"

    observed = _parse_timestamp(payload.get("timestamp"))
    if observed is None:
        return None, "timestamp_missing_or_not_timezone_aware"
    age_min = (fetched_at.astimezone(timezone.utc) - observed.astimezone(timezone.utc)).total_seconds() / 60
    future_tolerance = float(
        config.get("future_timestamp_tolerance_minutes", DEFAULT_FUTURE_TOLERANCE_MIN)
    )
    if age_min < -future_tolerance:
        return None, "measurement_timestamp_is_in_the_future"
    usable_max = float(config.get("usable_max_age_minutes", DEFAULT_USABLE_MAX_MIN))
    if age_min > usable_max:
        return None, "measurement_older_than_usable_limit"

    timezone_name = str(config.get("station_timezone") or DEFAULT_TIMEZONE)
    selected = {
        "location": {
            "id": int(location_id),
            "name": payload.get("publicLocationName") or payload.get("locationName"),
            "timezone": timezone_name,
        },
        "observed_at_utc": observed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "observed_at_local": observed.astimezone(ZoneInfo(timezone_name)).isoformat(),
        "age_min_at_fetch": round(max(0.0, age_min), 1),
        "offline": False,
        "pm2_5": {
            "value": round(pm25, 1),
            "unit": "ug/m3",
            "source_field": "pm02",
            "value_kind": "raw_unadjusted_mass_concentration",
            "corrected_value_used": False,
        },
        "station_config_sha256": _config_fingerprint(config),
    }
    selected_hash = sha256(
        json.dumps(selected, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    selected["selected_payload_sha256"] = selected_hash
    return selected, None


def _sports_exercise_bands(config: dict) -> dict:
    """Return direct-concentration sport bands, never AQI breakpoints.

    AIS 2023 treats atmospheric PM2.5 below 25 ug/m3 as normal exercise
    conditions, 25-50 as moderate caution, 51-150 as poor exercise
    conditions, and above 150 as likely hazardous for outdoor exercise.  A
    configured 101 marker may describe the upper part of the AIS poor band,
    but it must not be presented as another AIS band.
    """
    configured = config.get("sports_exercise_bands_ug_m3")
    configured = configured if isinstance(configured, dict) else {}
    defaults = {
        "good_below": 25.0,
        "moderate_from": 25.0,
        "poor_from": 51.0,
        "very_poor_from": 101.0,
        "hazardous_above": 150.0,
    }
    values = {
        key: _number(configured.get(key, default))
        for key, default in defaults.items()
    }
    if (
        values["good_below"] is None
        or values["moderate_from"] is None
        or values["poor_from"] is None
        or values["very_poor_from"] is None
        or values["hazardous_above"] is None
        or values["good_below"] < 0
        or values["moderate_from"] != values["good_below"]
        or not (
            values["moderate_from"]
            < values["poor_from"]
            < values["very_poor_from"]
            <= values["hazardous_above"]
        )
    ):
        values = defaults
        configuration_status = "invalid_configuration_defaults_used"
    else:
        configuration_status = "configured_or_default_valid"
    return {
        **values,
        "configuration_status": configuration_status,
        "standard": "AIS_2023_exercise_in_bushfire_smoke",
        "bands": [
            {
                "key": "normal_exercise_conditions",
                "min_inclusive": 0.0,
                "max_exclusive": values["good_below"],
            },
            {
                "key": "moderate_caution",
                "min_inclusive": values["moderate_from"],
                "max_exclusive": values["poor_from"],
            },
            {
                "key": "poor_conditions_for_exercise",
                "min_inclusive": values["poor_from"],
                "max_inclusive": values["hazardous_above"],
            },
            {
                "key": "likely_hazardous_for_outdoor_exercise",
                "min_exclusive": values["hazardous_above"],
                "max_inclusive": None,
            },
        ],
        "upper_poor_marker_rule": (
            f"The configured {values['very_poor_from']:.1f} ug/m3 marker only distinguishes "
            "the upper portion of the AIS 51-150 poor-conditions band; it is not a separate "
            "AIS category."
        ),
        "basis": (
            "AIS 2023 sports-specific recommendations applied directly to atmospheric PM2.5 "
            "mass concentration. These are not EPA AQI breakpoints, an AQI calculation, a "
            "24-hour regulatory average, or a medical diagnosis."
        ),
    }


def _point_band(pm25: float | None, bands: dict) -> dict | None:
    if pm25 is None:
        return None
    if pm25 < bands["good_below"]:
        key = "normal_exercise_conditions"
        severity = "normal"
    elif pm25 < bands["poor_from"]:
        key = "moderate_caution"
        severity = "moderate"
    elif pm25 <= bands["hazardous_above"]:
        key = "poor_conditions_for_exercise"
        severity = (
            "upper_poor"
            if pm25 >= bands["very_poor_from"]
            else "poor"
        )
    else:
        key = "likely_hazardous_for_outdoor_exercise"
        severity = "hazardous"
    return {
        "key": key,
        "severity": severity,
        "pm2_5_ug_m3": round(pm25, 1),
        "classification_input": "direct_current_pm2_5_mass_concentration",
        "is_aqi": False,
    }


def _trend_requirements(config: dict) -> dict:
    configured = config.get("trend_requirements")
    configured = configured if isinstance(configured, dict) else {}

    def positive_number(key: str, default: float) -> float:
        value = _number(configured.get(key, default))
        return value if value is not None and value > 0 else default

    minimum_samples_raw = _number(
        configured.get("minimum_samples", DEFAULT_TREND_MINIMUM_SAMPLES)
    )
    minimum_samples = (
        int(minimum_samples_raw)
        if minimum_samples_raw is not None and minimum_samples_raw >= 2
        else DEFAULT_TREND_MINIMUM_SAMPLES
    )
    return {
        "window_minutes": positive_number(
            "window_minutes", DEFAULT_TREND_WINDOW_MINUTES
        ),
        "minimum_samples": minimum_samples,
        "minimum_span_minutes": positive_number(
            "minimum_span_minutes", DEFAULT_TREND_MINIMUM_SPAN_MINUTES
        ),
        "maximum_gap_minutes": positive_number(
            "maximum_gap_minutes", DEFAULT_TREND_MAXIMUM_GAP_MINUTES
        ),
    }


def _read_ledger_entries(root: str | Path | None) -> list[dict]:
    ledger = read_json(snapshots_dir(root) / LEDGER_NAME, {})
    entries = ledger.get("entries") if isinstance(ledger, dict) else None
    return [dict(item) for item in entries or [] if isinstance(item, dict)]


def _ledger_entry(attempt: dict, current: dict) -> dict:
    success = attempt.get("status") == "success"
    return {
        "attempted_at": attempt.get("attempted_at"),
        "attempt_status": attempt.get("status"),
        "http_status": attempt.get("http_status"),
        "error": attempt.get("error"),
        "observed_at_utc": current.get("observed_at_utc") if success else None,
        "pm2_5_ug_m3": (
            (current.get("pm2_5") or {}).get("value") if success else None
        ),
        "location_id": (current.get("location") or {}).get("id"),
        "station_config_sha256": current.get("station_config_sha256"),
    }


def _with_candidate_ledger_entry(
    entries: list[dict],
    attempt: dict,
    current: dict,
) -> list[dict]:
    candidate = _ledger_entry(attempt, current)
    result = list(entries)
    comparison_keys = (
        "attempt_status",
        "observed_at_utc",
        "pm2_5_ug_m3",
        "error",
    )
    if result and all(
        result[-1].get(key) == candidate.get(key) for key in comparison_keys
    ):
        result[-1] = candidate
    else:
        result.append(candidate)
    return result[-LEDGER_RETENTION:]


def _exposure_window(
    entries: list[dict],
    config: dict,
    now: datetime,
    *,
    available_for_target_date: bool,
) -> dict:
    requirements = _trend_requirements(config)
    common = {
        "status": "insufficient",
        "sufficient": False,
        "source": f"snapshots/{LEDGER_NAME}",
        "window_minutes": requirements["window_minutes"],
        "requirements": requirements,
        "sample_count": 0,
        "span_minutes": None,
        "maximum_observed_gap_minutes": None,
        "insufficiency_reasons": [],
        "sample_mean_pm2_5_ug_m3": None,
        "minimum_pm2_5_ug_m3": None,
        "maximum_pm2_5_ug_m3": None,
        "first_pm2_5_ug_m3": None,
        "latest_pm2_5_ug_m3": None,
        "net_change_pm2_5_ug_m3": None,
        "slope_ug_m3_per_hour": None,
        "trend": None,
        "interpretation": (
            "This bounded station window is not an EPA NowCast, AQI, 24-hour regulatory "
            "average, athlete inhaled dose, or medical measurement."
        ),
    }
    if not available_for_target_date:
        return {
            **common,
            "status": "historical_unavailable",
            "insufficiency_reasons": ["live_ledger_not_projected_into_historical_date"],
        }

    expected_location_id = config.get("location_id")
    expected_fingerprint = _config_fingerprint(config)
    cutoff = now.astimezone(timezone.utc).timestamp() - (
        requirements["window_minutes"] * 60.0
    )
    future_tolerance_seconds = float(
        config.get(
            "future_timestamp_tolerance_minutes",
            DEFAULT_FUTURE_TOLERANCE_MIN,
        )
    ) * 60.0
    selected_by_timestamp: dict[str, tuple[datetime, float]] = {}
    for entry in entries:
        if entry.get("attempt_status") != "success":
            continue
        if str(entry.get("location_id")) != str(expected_location_id):
            continue
        fingerprint = entry.get("station_config_sha256")
        if fingerprint and fingerprint != expected_fingerprint:
            continue
        observed = _parse_timestamp(entry.get("observed_at_utc"))
        value = _number(entry.get("pm2_5_ug_m3"))
        if observed is None or value is None or value < 0:
            continue
        observed_utc = observed.astimezone(timezone.utc)
        observed_epoch = observed_utc.timestamp()
        if observed_epoch < cutoff:
            continue
        if observed_epoch > now.astimezone(timezone.utc).timestamp() + future_tolerance_seconds:
            continue
        selected_by_timestamp[observed_utc.isoformat()] = (observed_utc, value)

    selected = sorted(selected_by_timestamp.values(), key=lambda item: item[0])
    count = len(selected)
    span_minutes = (
        (selected[-1][0] - selected[0][0]).total_seconds() / 60.0
        if count >= 2
        else None
    )
    gaps = [
        (selected[index][0] - selected[index - 1][0]).total_seconds() / 60.0
        for index in range(1, count)
    ]
    maximum_gap = max(gaps) if gaps else None
    reasons = []
    if count < requirements["minimum_samples"]:
        reasons.append("minimum_sample_count_not_met")
    if span_minutes is None or span_minutes < requirements["minimum_span_minutes"]:
        reasons.append("minimum_observation_span_not_met")
    if maximum_gap is None or maximum_gap > requirements["maximum_gap_minutes"]:
        reasons.append("maximum_gap_requirement_not_met")
    summary = {
        **common,
        "sample_count": count,
        "span_minutes": round(span_minutes, 1) if span_minutes is not None else None,
        "maximum_observed_gap_minutes": (
            round(maximum_gap, 1) if maximum_gap is not None else None
        ),
        "insufficiency_reasons": reasons,
    }
    if reasons:
        return summary

    values = [item[1] for item in selected]
    first = values[0]
    latest = values[-1]
    mean = sum(values) / count
    x_values = [
        (item[0] - selected[0][0]).total_seconds() / 60.0 for item in selected
    ]
    x_mean = sum(x_values) / count
    denominator = sum((value - x_mean) ** 2 for value in x_values)
    slope_per_minute = (
        sum(
            (x_value - x_mean) * (value - mean)
            for x_value, value in zip(x_values, values)
        )
        / denominator
        if denominator > 0
        else 0.0
    )
    net_change = latest - first
    meaningful_change = max(3.0, mean * 0.1)
    if net_change >= meaningful_change and slope_per_minute > 0:
        trend = "rising"
    elif net_change <= -meaningful_change and slope_per_minute < 0:
        trend = "falling"
    else:
        trend = "stable_or_mixed"
    return {
        **summary,
        "status": "sufficient",
        "sufficient": True,
        "insufficiency_reasons": [],
        "sample_mean_pm2_5_ug_m3": round(mean, 1),
        "minimum_pm2_5_ug_m3": round(min(values), 1),
        "maximum_pm2_5_ug_m3": round(max(values), 1),
        "first_pm2_5_ug_m3": round(first, 1),
        "latest_pm2_5_ug_m3": round(latest, 1),
        "net_change_pm2_5_ug_m3": round(net_change, 1),
        "slope_ug_m3_per_hour": round(slope_per_minute * 60.0, 1),
        "trend": trend,
    }


def _decision(
    pm25: float | None,
    freshness_status: str,
    config: dict,
    *,
    latest_attempt_status: str | None,
    exposure_window: dict,
) -> dict:
    bands = _sports_exercise_bands(config)
    point_classification = _point_band(pm25, bands)
    window_mean = (
        _number(exposure_window.get("sample_mean_pm2_5_ug_m3"))
        if exposure_window.get("sufficient") is True
        else None
    )
    decision_reference = max(
        value for value in (pm25, window_mean) if value is not None
    ) if pm25 is not None or window_mean is not None else None
    reference_classification = _point_band(decision_reference, bands)
    reference_basis = (
        "worse_of_current_point_and_sufficient_bounded_window_sample_mean"
        if window_mean is not None
        else "current_point_only_exposure_window_insufficient"
    )
    common = {
        "decision_role": "outdoor_exposure_downshift_or_closure_only_never_training_promotion",
        "spatial_scope": config.get("spatial_scope"),
        "spatial_guardrail": config.get("spatial_guardrail"),
        "automatic_gate_venue_keys": list(
            config.get("automatic_gate_venue_keys") or []
        ),
        "automatic_gate_venue_aliases": list(
            config.get("automatic_gate_venue_aliases") or []
        ),
        "indoor_guardrail": (
            "This outdoor station does not establish indoor air quality; use the athlete's indoor "
            "monitor and symptoms as a separate evidence source."
        ),
        "symptom_override": (
            "Fresh explicitly current airway or smoke-exposure symptoms are a separate head-coach "
            "gate that can close outdoor training despite a lower station value. This PM2.5 "
            "surface does not infer current symptoms from historical or free-text notes."
        ),
        "single_sample_guardrail": (
            "This is a raw current PM2.5 observation, not AQI or a 24-hour average. A single "
            "25-50 ug/m3 point is moderate caution, not an automatic hard closure; one low or "
            "falling sample cannot promote training after a higher exposure window."
        ),
        "measurement_guardrail": (
            "This public AirGradient raw sensor observation may differ from corrected dashboard "
            "or regulatory-reference data. Use it conservatively for exposure downshift, never "
            "as a precise medical measurement or low-value clearance."
        ),
        "can_promote_training": False,
        "sports_exercise_bands": bands,
        "point_classification": point_classification,
        "decision_reference": {
            "pm2_5_ug_m3": (
                round(decision_reference, 1)
                if decision_reference is not None
                else None
            ),
            "basis": reference_basis,
            "classification": reference_classification,
        },
        "exposure_window_status": exposure_window.get("status"),
    }
    if freshness_status == "stale" and pm25 is not None:
        if decision_reference is not None and decision_reference > bands["hazardous_above"]:
            return {
                **common,
                "gate": "retained_outdoor_training_closed_pending_refresh",
                "severity": "red",
                "reason": (
                    f"The most recent TTDI raw PM2.5 observation is stale but still within the "
                    f"usable caution window at {pm25:.1f} ug/m3, and the decision reference is "
                    f"{decision_reference:.1f} ug/m3. Keep outdoor exercise closed for TTDI/"
                    "Bukit Kiara until a fresh venue-relevant source is checked."
                ),
            }
        if decision_reference is not None and decision_reference >= bands["poor_from"]:
            return {
                **common,
                "gate": "retained_outdoor_mtb_endurance_high_ventilation_closed_pending_refresh",
                "severity": "red",
                "reason": (
                    f"The most recent TTDI raw PM2.5 observation is stale but still within the "
                    f"usable caution window at {pm25:.1f} ug/m3. Keep planned MTB, prolonged "
                    "endurance and high-ventilation TTDI/Bukit Kiara training closed until "
                    "refreshed; this does not classify all incidental low-intensity movement."
                ),
            }
        if decision_reference is not None and decision_reference >= bands["moderate_from"]:
            return {
                **common,
                "gate": "retained_outdoor_moderate_caution_pending_refresh",
                "severity": "yellow",
                "reason": (
                    f"The retained TTDI PM2.5 observation is {pm25:.1f} ug/m3 in the AIS "
                    "moderate-caution range. Refresh it and check current symptoms before "
                    "choosing prolonged or high-ventilation outdoor training."
                ),
            }
    if freshness_status != "current" or pm25 is None:
        return {
            **common,
            "gate": "unknown_downshift_only",
            "severity": "yellow",
            "reason": (
                "No fresh usable TTDI PM2.5 observation is available; this source cannot open "
                "outdoor training."
            ),
        }

    if decision_reference is not None and decision_reference > bands["hazardous_above"]:
        gate = "outdoor_training_closed"
        severity = "red"
        reason = (
            f"The TTDI PM2.5 decision reference is {decision_reference:.1f} ug/m3, above the "
            f"AIS {bands['hazardous_above']:.1f} ug/m3 hazardous boundary. Planned outdoor "
            "exercise at TTDI/Bukit Kiara is closed."
        )
    elif decision_reference is not None and decision_reference >= bands["poor_from"]:
        gate = "outdoor_mtb_endurance_high_ventilation_closed"
        severity = "red"
        reason = (
            f"The TTDI PM2.5 decision reference is {decision_reference:.1f} ug/m3 in the AIS "
            "poor-conditions range. Planned MTB, prolonged endurance and high-ventilation "
            "training should move to cleaner air; this is not a blanket closure of incidental "
            "or short low-intensity outdoor movement for an asymptomatic athlete."
        )
    elif decision_reference is not None and decision_reference >= bands["moderate_from"]:
        gate = "outdoor_moderate_caution"
        severity = "yellow"
        reason = (
            f"Fresh TTDI raw PM2.5 is {pm25:.1f} ug/m3 in the AIS moderate-caution range. "
            "This point alone does not close high-ventilation training; use the bounded exposure "
            "window, current symptoms and planned ventilatory duration before the coaching call."
        )
    else:
        gate = "no_pm25_downshift_from_current_sample"
        severity = "info"
        reason = (
            f"Fresh TTDI raw PM2.5 is {pm25:.1f} ug/m3 in the AIS normal-exercise range. "
            "Other readiness, symptom, weather and consequence gates remain."
        )
    if latest_attempt_status not in {None, "success"} and gate == "no_pm25_downshift_from_current_sample":
        gate = "verify_before_outdoor_upgrade"
        severity = "yellow"
        reason = (
            "A retained low observation remains fresh, but the latest refresh failed or was "
            "unusable; verify another current source before outdoor training."
        )
    return {**common, "gate": gate, "severity": severity, "reason": reason}


def _project(
    artifact: dict,
    config: dict,
    target_date: date,
    now: datetime,
    *,
    ledger_entries: list[dict] | None = None,
) -> dict:
    latest_attempt = artifact.get("latest_attempt") if isinstance(artifact, dict) else None
    latest_attempt = latest_attempt if isinstance(latest_attempt, dict) else {}
    last_known_good = artifact.get("last_known_good") if isinstance(artifact, dict) else None
    last_known_good = last_known_good if isinstance(last_known_good, dict) else None
    expected_config_fingerprint = _config_fingerprint(config)
    retained_config_fingerprint = (last_known_good or {}).get(
        "station_config_sha256"
    )
    if last_known_good is None:
        retention_validation = "no_retained_measurement"
    elif (
        expected_config_fingerprint is None
        or retained_config_fingerprint != expected_config_fingerprint
    ):
        retention_validation = "station_config_fingerprint_mismatch_or_missing"
        last_known_good = None
    else:
        retention_validation = "station_config_fingerprint_match"
    timezone_name = str(config.get("station_timezone") or DEFAULT_TIMEZONE)
    current_local_date = now.astimezone(ZoneInfo(timezone_name)).date()
    if target_date != current_local_date:
        freshness = {
            "status": "historical_unavailable",
            "age_min": None,
            "fresh_max_age_min": float(
                config.get("fresh_max_age_minutes", DEFAULT_FRESH_MAX_MIN)
            ),
            "usable_max_age_min": float(
                config.get("usable_max_age_minutes", DEFAULT_USABLE_MAX_MIN)
            ),
        }
        current = None
        visible_latest_attempt = None
        visible_last_known_good = None
    else:
        observed = _parse_timestamp((last_known_good or {}).get("observed_at_utc"))
        age_min = (
            (now.astimezone(timezone.utc) - observed.astimezone(timezone.utc)).total_seconds() / 60
            if observed is not None
            else None
        )
        fresh_max = float(config.get("fresh_max_age_minutes", DEFAULT_FRESH_MAX_MIN))
        usable_max = float(config.get("usable_max_age_minutes", DEFAULT_USABLE_MAX_MIN))
        future_tolerance = float(
            config.get("future_timestamp_tolerance_minutes", DEFAULT_FUTURE_TOLERANCE_MIN)
        )
        if age_min is None:
            freshness_status = "missing"
        elif age_min < -future_tolerance:
            freshness_status = "future_invalid"
        elif age_min <= fresh_max:
            freshness_status = "current"
        elif age_min <= usable_max:
            freshness_status = "stale"
        else:
            freshness_status = "unusable"
        freshness = {
            "status": freshness_status,
            "age_min": round(max(0.0, age_min), 1) if age_min is not None else None,
            "fresh_max_age_min": fresh_max,
            "usable_max_age_min": usable_max,
            "age_basis": "measurement_timestamp_not_fetch_time",
        }
        current = last_known_good if freshness_status in {"current", "stale"} else None
        visible_latest_attempt = latest_attempt or None
        visible_last_known_good = last_known_good

    pm25 = _number(((current or {}).get("pm2_5") or {}).get("value"))
    latest_status = latest_attempt.get("status")
    exposure_window = _exposure_window(
        ledger_entries or [],
        config,
        now,
        available_for_target_date=target_date == current_local_date,
    )
    if not config:
        status = "unconfigured"
    elif current is None:
        status = "unavailable"
    elif freshness.get("status") == "current" and latest_status == "success":
        status = "available_current"
    elif freshness.get("status") == "current":
        status = "available_retained_current"
    else:
        status = "available_stale"
    return {
        "artifact_type": "external_air_quality_current",
        "schema_version": 1,
        "date": target_date.isoformat(),
        "generated_at": now.isoformat(timespec="seconds"),
        "provider": "AirGradient",
        "status": status,
        "location": {
            "id": config.get("location_id"),
            "name": config.get("location_name"),
            "timezone": timezone_name,
        },
        "source": {
            "endpoint": config.get("endpoint"),
            "endpoint_type": "public_current_location_measure",
            "authentication": "none",
            "selected_pm25_field": "pm02",
            "selected_value_kind": "raw_unadjusted_mass_concentration",
            "unit": "ug/m3",
        },
        "current": current,
        "freshness": freshness,
        "exposure_window": exposure_window,
        "decision": _decision(
            pm25,
            freshness.get("status"),
            config,
            latest_attempt_status=latest_status,
            exposure_window=exposure_window,
        ),
        "latest_attempt": visible_latest_attempt,
        "last_known_good": visible_last_known_good,
        "retention_validation": {
            "status": retention_validation,
            "expected_station_config_sha256": expected_config_fingerprint,
            "retained_station_config_sha256": retained_config_fingerprint,
            "historical_projection_hides_current_measurements": (
                target_date != current_local_date
            ),
        },
        "retention_policy": "preserve_last_semantically_valid_nonoffline_measurement",
        "privacy": {
            "classification": "public_station_selected_fields_only",
            "raw_payload_stored": False,
            "excluded_fields": [
                "latitude",
                "longitude",
                "publicContributorName",
                "wifi",
                "serialno",
                "firmwareVersion",
            ],
        },
    }


def _append_ledger(root: str | Path | None, artifact: dict) -> None:
    path = snapshots_dir(root) / LEDGER_NAME
    entries = _read_ledger_entries(root)
    attempt = artifact.get("latest_attempt") or {}
    current = artifact.get("last_known_good") or {}
    entries = _with_candidate_ledger_entry(entries, attempt, current)
    write_json(
        path,
        {
            "artifact_type": "external_air_quality_fetch_ledger",
            "generated_at": artifact.get("generated_at"),
            "retention": LEDGER_RETENTION,
            "entries": entries,
        },
    )


def refresh_air_quality(
    root: str | Path | None = None,
    *,
    now: datetime | None = None,
    fetcher: Callable[[str, float], Any] | None = None,
) -> dict:
    context = load_context(root)
    config = _config(context)
    timezone_name = str(
        config.get("station_timezone")
        or context.get("athlete", {}).get("timezone")
        or DEFAULT_TIMEZONE
    )
    generated = _aware_now(now, timezone_name)
    path = snapshots_dir(root) / ARTIFACT_NAME
    previous = read_json(path, {})
    previous_lkg = previous.get("last_known_good") if isinstance(previous, dict) else None
    if not config.get("endpoint") or config.get("location_id") is None:
        artifact = _project(
            {
                "latest_attempt": {
                    "status": "unconfigured",
                    "attempted_at": generated.isoformat(timespec="seconds"),
                    "error": "No Bukit Kiara air_quality_proxy endpoint is configured.",
                },
                "last_known_good": previous_lkg,
            },
            config,
            generated.date(),
            generated,
            ledger_entries=_read_ledger_entries(root),
        )
        write_json(path, artifact)
        return artifact

    endpoint = str(config["endpoint"])
    timeout_seconds = float(config.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
    latest_attempt: dict[str, Any] = {
        "status": "failed",
        "attempted_at": generated.isoformat(timespec="seconds"),
        "http_status": None,
        "error": None,
        "retry_after": None,
    }
    last_known_good = previous_lkg if isinstance(previous_lkg, dict) else None
    try:
        status, payload, headers = _call_fetcher(fetcher or _http_fetch, endpoint, timeout_seconds)
        latest_attempt["http_status"] = status
        if status != 200:
            raise AirQualityFetchError(
                f"AirGradient returned HTTP {status}.",
                http_status=status,
                retry_after=headers.get("Retry-After"),
            )
        selected, semantic_error = _selected_measurement(payload, config, generated)
        if selected is None:
            latest_attempt.update(
                {
                    "status": "success_unusable",
                    "semantic_error": semantic_error,
                    "error": None,
                }
            )
        else:
            latest_attempt.update(
                {
                    "status": "success",
                    "measurement_timestamp": selected.get("observed_at_utc"),
                }
            )
            last_known_good = {
                **selected,
                "fetched_at": generated.isoformat(timespec="seconds"),
            }
    except AirQualityFetchError as exc:
        latest_attempt.update(
            {
                "status": "failed",
                "http_status": exc.http_status,
                "error": str(exc),
                "retry_after": exc.retry_after,
            }
        )
    except Exception:  # Keep this public context surface fail-soft inside sync.
        latest_attempt.update(
            {
                "status": "failed",
                "error": "Unexpected air-quality fetch failure.",
            }
        )

    prospective_entries = _with_candidate_ledger_entry(
        _read_ledger_entries(root),
        latest_attempt,
        last_known_good or {},
    )
    artifact = _project(
        {"latest_attempt": latest_attempt, "last_known_good": last_known_good},
        config,
        generated.date(),
        generated,
        ledger_entries=prospective_entries,
    )
    write_json(path, artifact)
    _append_ledger(root, artifact)
    return artifact


def load_air_quality_context(
    root: str | Path | None = None,
    for_date: str | date | None = None,
    *,
    now: datetime | None = None,
) -> dict:
    context = load_context(root)
    config = _config(context)
    timezone_name = str(
        config.get("station_timezone")
        or context.get("athlete", {}).get("timezone")
        or DEFAULT_TIMEZONE
    )
    generated = _aware_now(now, timezone_name)
    target = parse_date(for_date) or generated.date()
    stored = read_json(snapshots_dir(root) / ARTIFACT_NAME, {})
    return _project(
        stored if isinstance(stored, dict) else {},
        config,
        target,
        generated,
        ledger_entries=_read_ledger_entries(root),
    )
