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


def _thresholds(config: dict) -> dict:
    configured = config.get("coaching_thresholds_ug_m3")
    configured = configured if isinstance(configured, dict) else {}
    return {
        "elevated_from": float(configured.get("elevated_from", 9.1)),
        "outdoor_hard_closed_from": float(
            configured.get("outdoor_hard_closed_from", 35.5)
        ),
        "all_outdoor_closed_from": float(
            configured.get("all_outdoor_closed_from", 55.5)
        ),
        "basis": configured.get("basis")
        or (
            "coach exposure thresholds anchored to current EPA PM2.5 concentration intervals; "
            "not an AQI calculation"
        ),
    }


def _decision(
    pm25: float | None,
    freshness_status: str,
    config: dict,
    *,
    latest_attempt_status: str | None,
) -> dict:
    limits = _thresholds(config)
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
            "This is a raw current PM2.5 observation, not AQI or a 24-hour average. A high fresh "
            "sample may close training; one low or falling sample cannot promote training."
        ),
        "measurement_guardrail": (
            "This public AirGradient raw sensor observation may differ from corrected dashboard "
            "or regulatory-reference data. Use it conservatively for exposure downshift, never "
            "as a precise medical measurement or low-value clearance."
        ),
        "can_promote_training": False,
        "thresholds": limits,
    }
    if freshness_status == "stale" and pm25 is not None:
        if pm25 >= limits["all_outdoor_closed_from"]:
            return {
                **common,
                "gate": "retained_outdoor_training_closed_pending_refresh",
                "severity": "red",
                "reason": (
                    f"The most recent TTDI raw PM2.5 observation is stale but still within the "
                    f"usable caution window at {pm25:.1f} ug/m3. Keep outdoor training closed "
                    "for TTDI/Bukit Kiara until a fresh venue-relevant source is checked."
                ),
            }
        if pm25 >= limits["outdoor_hard_closed_from"]:
            return {
                **common,
                "gate": "retained_outdoor_hard_training_closed_pending_refresh",
                "severity": "red",
                "reason": (
                    f"The most recent TTDI raw PM2.5 observation is stale but still within the "
                    f"usable caution window at {pm25:.1f} ug/m3. Keep high-ventilation and "
                    "consequence-bearing TTDI/Bukit Kiara training closed until refreshed."
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

    if pm25 >= limits["all_outdoor_closed_from"]:
        gate = "outdoor_training_closed"
        severity = "red"
        reason = (
            f"Fresh TTDI raw PM2.5 is {pm25:.1f} ug/m3, at or above the stack's "
            f"{limits['all_outdoor_closed_from']:.1f} ug/m3 threshold; planned outdoor training "
            "at TTDI/Bukit Kiara is closed."
        )
    elif pm25 >= limits["outdoor_hard_closed_from"]:
        gate = "outdoor_hard_training_closed"
        severity = "red"
        reason = (
            f"Fresh TTDI raw PM2.5 is {pm25:.1f} ug/m3, closing high-ventilation and "
            "consequence-bearing outdoor training at TTDI/Bukit Kiara."
        )
    elif pm25 >= limits["elevated_from"]:
        gate = "outdoor_caution"
        severity = "yellow"
        reason = (
            f"Fresh TTDI raw PM2.5 is {pm25:.1f} ug/m3. Use fresh explicitly current symptoms, "
            "a corroborating trend and venue-specific exposure before choosing TTDI/Bukit "
            "Kiara training."
        )
    else:
        gate = "no_pm25_downshift_from_current_sample"
        severity = "info"
        reason = (
            f"Fresh TTDI raw PM2.5 is {pm25:.1f} ug/m3, below the stack's current PM2.5 "
            "downshift thresholds. Other readiness, symptom, weather and consequence gates remain."
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
        "decision": _decision(
            pm25,
            freshness.get("status"),
            config,
            latest_attempt_status=latest_status,
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
    existing = read_json(path, {})
    entries = existing.get("entries") if isinstance(existing, dict) else None
    entries = list(entries) if isinstance(entries, list) else []
    attempt = artifact.get("latest_attempt") or {}
    current = artifact.get("last_known_good") or {}
    entry = {
        "attempted_at": attempt.get("attempted_at"),
        "attempt_status": attempt.get("status"),
        "http_status": attempt.get("http_status"),
        "error": attempt.get("error"),
        "observed_at_utc": current.get("observed_at_utc") if attempt.get("status") == "success" else None,
        "pm2_5_ug_m3": (
            (current.get("pm2_5") or {}).get("value")
            if attempt.get("status") == "success"
            else None
        ),
        "location_id": (current.get("location") or {}).get("id"),
    }
    if entries and all(
        entries[-1].get(key) == entry.get(key)
        for key in ("attempt_status", "observed_at_utc", "pm2_5_ug_m3", "error")
    ):
        entries[-1] = entry
    else:
        entries.append(entry)
    write_json(
        path,
        {
            "artifact_type": "external_air_quality_fetch_ledger",
            "generated_at": artifact.get("generated_at"),
            "retention": LEDGER_RETENTION,
            "entries": entries[-LEDGER_RETENTION:],
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

    artifact = _project(
        {"latest_attempt": latest_attempt, "last_known_good": last_known_good},
        config,
        generated.date(),
        generated,
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
    return _project(stored if isinstance(stored, dict) else {}, config, target, generated)
