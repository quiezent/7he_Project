from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .evidence import load_activities
from .io import read_json, write_json, write_text
from .paths import snapshots_dir
from .time_utils import DEFAULT_TIMEZONE, iso_now, parse_date, today_local


BIKE_DEVICE_CATEGORIES = {"mtb", "bike_indoor", "bike_outdoor"}
EXTERNAL_HR_TYPES = {"HEART_RATE"}


def _metadata(payload: dict) -> dict:
    metadata = payload.get("metadataDTO")
    return metadata if isinstance(metadata, dict) else {}


def _sensor_type(sensor: dict) -> str:
    return str(sensor.get("antplusDeviceType") or sensor.get("sensorType") or "").upper()


def summarize_activity_devices(payload: dict) -> dict:
    metadata = _metadata(payload)
    sensors = metadata.get("sensors") if isinstance(metadata.get("sensors"), list) else []
    sensor_rows = []
    for sensor in sensors:
        if not isinstance(sensor, dict):
            continue
        sensor_rows.append(
            {
                "manufacturer": sensor.get("manufacturer"),
                "source_type": sensor.get("sourceType"),
                "sensor_type": _sensor_type(sensor),
                "sku": sensor.get("sku"),
                "fit_product_number": sensor.get("fitProductNumber"),
                "software_version": sensor.get("softwareVersion"),
                "battery_status": sensor.get("batteryStatus"),
            }
        )
    device_meta = metadata.get("deviceMetaDataDTO")
    if not isinstance(device_meta, dict):
        device_meta = {}
    file_format = metadata.get("fileFormat")
    if not isinstance(file_format, dict):
        file_format = {}
    return {
        "recording_device": {
            "manufacturer": metadata.get("manufacturer"),
            "device_id": device_meta.get("deviceId"),
            "device_type_pk": device_meta.get("deviceTypePk"),
            "device_version_pk": device_meta.get("deviceVersionPk"),
        },
        "apps": {
            "device_application_installation_id": metadata.get("deviceApplicationInstallationId"),
            "agent_application_installation_id": metadata.get("agentApplicationInstallationId"),
            "agent_string": metadata.get("agentString"),
            "file_format": file_format.get("formatKey"),
        },
        "sensors": sensor_rows,
        "external_hr_sensor": any(row.get("sensor_type") in EXTERNAL_HR_TYPES for row in sensor_rows),
        "external_hr_battery_statuses": [
            row.get("battery_status")
            for row in sensor_rows
            if row.get("sensor_type") in EXTERNAL_HR_TYPES and row.get("battery_status")
        ],
    }


def _activity_lookup(root: str | Path | None) -> dict[str, dict]:
    return {str(activity.get("id")): activity for activity in load_activities(root)}


def _read_activity_device_index(root: str | Path | None) -> dict:
    payload = read_json(snapshots_dir(root) / "activity_device_index.json", {})
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, list):
        return {"activities": payload}
    return {"activities": []}


def _hr_confidence(row: dict) -> str:
    if row.get("external_hr_sensor"):
        statuses = [str(item).upper() for item in row.get("external_hr_battery_statuses") or []]
        if "LOW" in statuses:
            return "external_hr_low_battery"
        return "external_hr"
    return "wrist_hr_likely"


def _text_report(report: dict) -> str:
    lines = [
        f"Device Audit - {report['date']}",
        "",
        f"Checked activities: {report['checked_activities']}",
        f"MTB checked: {report['mtb_checked']}",
        "",
        "Flags:",
    ]
    if report["flags"]:
        lines.extend(f"- {flag['message']}" for flag in report["flags"])
    else:
        lines.append("- None")
    lines.extend(["", "Recent MTB Devices:"])
    for row in report["recent_mtb_devices"]:
        sensors = ", ".join(row.get("sensor_types") or []) or "no external sensors"
        lines.append(
            f"- {row.get('date')}: HR {row.get('hr_confidence')}; sensors: {sensors}"
        )
    return "\n".join(lines) + "\n"


def build_device_audit(
    root: str | Path | None = None,
    for_date: str | date | None = None,
    lookback_days: int = 90,
) -> dict:
    target = parse_date(for_date) or today_local(DEFAULT_TIMEZONE)
    start = target - timedelta(days=max(1, lookback_days) - 1)
    index = _read_activity_device_index(root)
    activities = _activity_lookup(root)
    rows = []
    flags = []
    recent_mtb = []

    for row in index.get("activities") or []:
        if not isinstance(row, dict):
            continue
        activity_id = str(row.get("activity_id") or row.get("id") or "")
        activity = activities.get(activity_id, {})
        act_date = parse_date(row.get("date") or activity.get("date"))
        if not act_date or act_date < start or act_date > target:
            continue
        category = row.get("category") or activity.get("category")
        sensors = row.get("sensors") if isinstance(row.get("sensors"), list) else []
        normalized = {
            "activity_id": activity_id,
            "date": act_date.isoformat(),
            "name": row.get("name") or activity.get("name"),
            "type": row.get("type") or activity.get("type"),
            "category": category,
            "device_fetch_ok": row.get("device_fetch_ok"),
            "recording_device": row.get("recording_device"),
            "apps": row.get("apps"),
            "sensor_types": sorted({item.get("sensor_type") for item in sensors if item.get("sensor_type")}),
            "external_hr_sensor": bool(row.get("external_hr_sensor")),
            "external_hr_battery_statuses": row.get("external_hr_battery_statuses") or [],
        }
        normalized["hr_confidence"] = _hr_confidence(normalized)
        rows.append(normalized)

        if category == "mtb":
            recent_mtb.append(normalized)
            if not normalized["external_hr_sensor"]:
                flags.append(
                    {
                        "type": "mtb_wrist_hr_likely",
                        "severity": "yellow",
                        "activity_id": activity_id,
                        "date": act_date.isoformat(),
                        "message": (
                            f"{act_date.isoformat()} MTB activity {activity_id} has no external "
                            "heart-rate sensor in Devices & Apps; treat HR and Garmin load as lower confidence."
                        ),
                    }
                )
            elif "LOW" in [str(item).upper() for item in normalized["external_hr_battery_statuses"]]:
                flags.append(
                    {
                        "type": "external_hr_battery_low",
                        "severity": "yellow",
                        "activity_id": activity_id,
                        "date": act_date.isoformat(),
                        "message": (
                            f"{act_date.isoformat()} MTB activity {activity_id} used an external HR sensor "
                            "with LOW battery; replace/charge before key rides."
                        ),
                    }
                )

    report = {
        "date": target.isoformat(),
        "generated_at": iso_now(DEFAULT_TIMEZONE),
        "source_index": "snapshots/activity_device_index.json",
        "lookback_days": lookback_days,
        "checked_activities": len(rows),
        "mtb_checked": sum(1 for row in rows if row.get("category") == "mtb"),
        "recent_mtb_devices": sorted(recent_mtb, key=lambda item: item.get("date") or "", reverse=True)[:10],
        "flags": flags,
    }
    write_json(snapshots_dir(root) / "device_audit.json", report)
    write_text(snapshots_dir(root) / "device_audit.txt", _text_report(report))
    return report
