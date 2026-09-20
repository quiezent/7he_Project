from __future__ import annotations

from pathlib import Path
from typing import Any

from .evidence import as_number, load_latest_training_status
from .io import write_json
from .paths import snapshots_dir
from .time_utils import DEFAULT_TIMEZONE, iso_now, parse_date, today_local


def _primary_device_entry(container: dict | None, key: str) -> dict:
    if not isinstance(container, dict):
        return {}
    mapping = container.get(key) or {}
    if not isinstance(mapping, dict):
        return {}
    values = [value for value in mapping.values() if isinstance(value, dict)]
    for value in values:
        if value.get("primaryTrainingDevice"):
            return value
    return values[0] if values else {}


def _first_device_name(container: dict | None) -> str | None:
    if not isinstance(container, dict):
        return None
    devices = container.get("recordedDevices") or []
    if not devices:
        return None
    return devices[0].get("deviceName")


def normalize_training_status_payload(snapshot: dict | None, snapshot_date: str | None) -> dict:
    payload = (snapshot or {}).get("payload")
    data = payload.get("data") if isinstance(payload, dict) and payload.get("ok") else {}
    if not isinstance(data, dict):
        data = {}

    status_container = data.get("mostRecentTrainingStatus") or {}
    balance_container = data.get("mostRecentTrainingLoadBalance") or {}
    status_entry = _primary_device_entry(status_container, "latestTrainingStatusData")
    load_balance = _primary_device_entry(balance_container, "metricsTrainingLoadBalanceDTOMap")
    acute = status_entry.get("acuteTrainingLoadDTO") or {}
    vo2 = data.get("mostRecentVO2Max") or {}
    cycling_vo2 = vo2.get("cycling") or {}
    generic_vo2 = vo2.get("generic") or {}
    acclimation = vo2.get("heatAltitudeAcclimation") or {}

    normalized = {
        "date": snapshot_date,
        "generated_at": iso_now(DEFAULT_TIMEZONE),
        "source_payload_ok": bool(isinstance(payload, dict) and payload.get("ok")),
        "device": _first_device_name(status_container) or _first_device_name(balance_container),
        "sport": status_entry.get("sport"),
        "sub_sport": status_entry.get("subSport"),
        "fitness_trend": status_entry.get("fitnessTrend"),
        "training_status_code": status_entry.get("trainingStatus"),
        "training_status_feedback": status_entry.get("trainingStatusFeedbackPhrase"),
        "training_paused": status_entry.get("trainingPaused"),
        "since_date": status_entry.get("sinceDate"),
        "acute_chronic": {
            "status": acute.get("acwrStatus"),
            "feedback": acute.get("acwrStatusFeedback"),
            "ratio": as_number(acute.get("dailyAcuteChronicWorkloadRatio")),
            "percent": as_number(acute.get("acwrPercent")),
            "acute_load": as_number(acute.get("dailyTrainingLoadAcute")),
            "chronic_load": as_number(acute.get("dailyTrainingLoadChronic")),
            "chronic_min": as_number(acute.get("minTrainingLoadChronic")),
            "chronic_max": as_number(acute.get("maxTrainingLoadChronic")),
        },
        "load_focus": {
            "low_aerobic": as_number(load_balance.get("monthlyLoadAerobicLow")),
            "low_aerobic_target_min": as_number(load_balance.get("monthlyLoadAerobicLowTargetMin")),
            "low_aerobic_target_max": as_number(load_balance.get("monthlyLoadAerobicLowTargetMax")),
            "high_aerobic": as_number(load_balance.get("monthlyLoadAerobicHigh")),
            "high_aerobic_target_min": as_number(load_balance.get("monthlyLoadAerobicHighTargetMin")),
            "high_aerobic_target_max": as_number(load_balance.get("monthlyLoadAerobicHighTargetMax")),
            "anaerobic": as_number(load_balance.get("monthlyLoadAnaerobic")),
            "anaerobic_target_min": as_number(load_balance.get("monthlyLoadAnaerobicTargetMin")),
            "anaerobic_target_max": as_number(load_balance.get("monthlyLoadAnaerobicTargetMax")),
            "feedback": load_balance.get("trainingBalanceFeedbackPhrase"),
        },
        "vo2max": {
            "cycling_value": as_number(cycling_vo2.get("vo2MaxValue")),
            "cycling_precise": as_number(cycling_vo2.get("vo2MaxPreciseValue")),
            "cycling_date": cycling_vo2.get("calendarDate"),
            "generic_value": as_number(generic_vo2.get("vo2MaxValue")),
            "generic_precise": as_number(generic_vo2.get("vo2MaxPreciseValue")),
            "generic_date": generic_vo2.get("calendarDate"),
        },
        "acclimation": {
            "heat_pct": as_number(acclimation.get("heatAcclimationPercentage")),
            "heat_trend": acclimation.get("heatTrend"),
            "heat_date": acclimation.get("heatAcclimationDate"),
            "altitude_pct": as_number(acclimation.get("altitudeAcclimation")),
            "altitude_date": acclimation.get("altitudeAcclimationDate"),
        },
        "flags": [],
    }

    acwr_status = str(normalized["acute_chronic"]["status"] or "").lower()
    if acwr_status and acwr_status not in {"optimal"}:
        normalized["flags"].append(
            {"type": "acwr_status", "message": f"Garmin ACWR status is {normalized['acute_chronic']['status']}."}
        )
    feedback = str(normalized["load_focus"]["feedback"] or "").lower()
    if "shortage" in feedback:
        normalized["flags"].append(
            {"type": "load_focus_gap", "message": f"Garmin load focus feedback: {normalized['load_focus']['feedback']}."}
        )
    return normalized


def build_training_status_current(
    root: str | Path | None = None,
    for_date: str | None = None,
) -> dict:
    target = parse_date(for_date) or today_local(DEFAULT_TIMEZONE)
    snap_date, snapshot = load_latest_training_status(root, target)
    normalized = normalize_training_status_payload(
        snapshot,
        snap_date.isoformat() if snap_date else None,
    )
    write_json(snapshots_dir(root) / "garmin_training_status_current.json", normalized)
    return normalized

