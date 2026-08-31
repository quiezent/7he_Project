from __future__ import annotations

from datetime import date, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any
from zipfile import ZipFile

from fitparse import FitFile

from .evidence import load_activities
from .io import read_json, write_json, write_text
from .paths import repo_root, snapshots_dir
from .time_utils import DEFAULT_TIMEZONE, iso_now, parse_date, today_local


BIKE_DEVICE_CATEGORIES = {"mtb", "bike_indoor", "bike_outdoor"}
EXTERNAL_HR_TYPES = {"HEART_RATE"}
EXTERNAL_STANDARD_SOURCE_TYPES = {
    "ANT",
    "ANTPLUS",
    "BLE",
    "BLUETOOTH",
    "BLUETOOTHLOWENERGY",
}
LOCAL_STANDARD_SOURCE_TYPES = {"INTERNAL", "LOCAL", "ONBOARD"}


def _normalized_source_type(value: Any) -> str:
    return "".join(character for character in str(value or "").upper() if character.isalnum())


def is_external_standard_source(value: Any) -> bool:
    return _normalized_source_type(value) in EXTERNAL_STANDARD_SOURCE_TYPES


def _normalized_sensor_type(value: Any) -> str:
    parts = []
    current = []
    for character in str(value or "").upper():
        if character.isalnum():
            current.append(character)
        elif current:
            parts.append("".join(current))
            current = []
    if current:
        parts.append("".join(current))
    return "_".join(parts)


def _standard_hr_source_classification(sensor_rows: list[dict]) -> str:
    hr_rows = [row for row in sensor_rows if row.get("sensor_type") in EXTERNAL_HR_TYPES]
    if not hr_rows:
        return "not_reported_by_standard_metadata"
    sources = {_normalized_source_type(row.get("source_type")) for row in hr_rows}
    if sources & EXTERNAL_STANDARD_SOURCE_TYPES:
        return "external_standard_metadata"
    if sources and sources <= LOCAL_STANDARD_SOURCE_TYPES:
        return "local_or_onboard_standard_metadata"
    return "unknown_standard_metadata_source"


def public_recording_device(value: Any) -> dict | None:
    """Return bounded recording-device context without identifiers or serial-like keys."""
    row = value if isinstance(value, dict) else {}
    manufacturer = row.get("manufacturer")
    return {"manufacturer": manufacturer} if manufacturer is not None else None


def _public_app_context(value: Any) -> dict | None:
    row = value if isinstance(value, dict) else {}
    public = {
        key: row.get(key)
        for key in ("agent_string", "file_format")
        if row.get(key) is not None
    }
    return public or None


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
    hr_source_classification = _standard_hr_source_classification(sensor_rows)
    external_hr_sensor = hr_source_classification == "external_standard_metadata"
    local_or_onboard_hr_sensor = (
        hr_source_classification == "local_or_onboard_standard_metadata"
    )
    external_speed_rows = [
        row
        for row in sensor_rows
        if row.get("sensor_type") == "BIKE_SPEED"
        and is_external_standard_source(row.get("source_type"))
    ]
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
        "external_hr_sensor": external_hr_sensor,
        "local_or_onboard_hr_sensor": local_or_onboard_hr_sensor,
        "hr_source_classification": hr_source_classification,
        "external_speed_sensor": bool(external_speed_rows),
        "external_speed_sensor_battery_statuses": sorted(
            {
                str(row.get("battery_status")).upper()
                for row in external_speed_rows
                if row.get("battery_status")
            }
        ),
        "external_hr_battery_statuses": [
            row.get("battery_status")
            for row in sensor_rows
            if row.get("sensor_type") in EXTERNAL_HR_TYPES
            and _normalized_source_type(row.get("source_type"))
            in EXTERNAL_STANDARD_SOURCE_TYPES
            and row.get("battery_status")
        ],
    }


def summarize_standard_fit_device_sources(rows: list[dict]) -> dict:
    """Summarize only public FIT device-info fields; ignore proprietary fields."""
    public_rows = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        source_type = _normalized_source_type(row.get("source_type"))
        if not source_type:
            continue
        public_rows.append(
            {
                "source_type": source_type.lower(),
                "manufacturer": row.get("manufacturer"),
                "device_type": row.get("device_type"),
                "sensor_type": _normalized_sensor_type(
                    row.get("antplus_device_type")
                ),
                "device_index": row.get("device_index"),
                "product": row.get("garmin_product") or row.get("product"),
                "software_version": row.get("software_version"),
                "battery_status": row.get("battery_status"),
            }
        )

    source_types = sorted({row["source_type"] for row in public_rows})
    normalized_sources = {_normalized_source_type(value) for value in source_types}
    external_present = bool(normalized_sources & EXTERNAL_STANDARD_SOURCE_TYPES)
    local_present = bool(normalized_sources & LOCAL_STANDARD_SOURCE_TYPES)
    sensor_types = sorted(
        {row["sensor_type"] for row in public_rows if row.get("sensor_type")}
    )
    external_speed_rows = [
        row
        for row in public_rows
        if row.get("sensor_type") == "BIKE_SPEED"
        and is_external_standard_source(row.get("source_type"))
    ]
    external_sensor_types = sorted(
        {
            row["sensor_type"]
            for row in public_rows
            if row.get("sensor_type")
            and is_external_standard_source(row.get("source_type"))
        }
    )
    creator_row = next(
        (row for row in public_rows if str(row.get("device_index") or "").lower() == "creator"),
        None,
    )
    return {
        "status": "available" if public_rows else "no_standard_device_sources",
        "device_info_rows": len(public_rows),
        "source_types": source_types,
        "sensor_types": sensor_types,
        "external_sensor_types": external_sensor_types,
        "external_device_source_present": external_present,
        "local_or_onboard_source_present": local_present,
        "local_or_onboard_only": local_present and not external_present,
        "external_speed_sensor": bool(external_speed_rows),
        "external_speed_sensor_battery_statuses": sorted(
            {
                str(row.get("battery_status")).upper()
                for row in external_speed_rows
                if row.get("battery_status")
            }
        ),
        "creator": (
            {
                "manufacturer": creator_row.get("manufacturer"),
                "product": creator_row.get("product"),
                "software_version": creator_row.get("software_version"),
                "source_type": creator_row.get("source_type"),
            }
            if creator_row
            else None
        ),
        "interpretation_guardrail": (
            "Standard FIT source_type distinguishes local/onboard from ANT/BLE device records, "
            "and antplus_device_type can identify bounded sensor classes such as BIKE_SPEED. "
            "Sensor presence supports measurement provenance but does not prove wheel location, "
            "calibration, wheel circumference, or per-sample accuracy. It also does not by itself "
            "prove which device supplied heart rate. Proprietary or unknown FIT fields are excluded."
        ),
    }


def read_standard_fit_device_sources(path: str | Path) -> dict:
    """Read bounded standard device-info provenance from a preserved FIT or ZIP."""
    source_path = Path(path)
    try:
        if source_path.stat().st_size > 50_000_000:
            return {"status": "fit_source_too_large"}
        raw = source_path.read_bytes()
        if raw[:2] == b"PK":
            with ZipFile(BytesIO(raw)) as archive:
                members = [
                    item
                    for item in archive.infolist()
                    if not item.is_dir() and item.filename.lower().endswith(".fit")
                ]
                if not members:
                    return {"status": "fit_member_missing"}
                member = min(members, key=lambda item: item.file_size)
                if member.file_size > 50_000_000:
                    return {"status": "fit_member_too_large"}
                raw = archive.read(member)
        fit = FitFile(BytesIO(raw))
        fit.parse()
        rows = []
        for message in fit.get_messages("device_info"):
            rows.append(
                {
                    field.name: field.value
                    for field in message.fields
                    if field.name
                    in {
                        "source_type",
                        "manufacturer",
                        "device_type",
                        "antplus_device_type",
                        "device_index",
                        "garmin_product",
                        "product",
                        "software_version",
                        "battery_status",
                    }
                }
            )
        return summarize_standard_fit_device_sources(rows)
    except Exception as exc:
        return {
            "status": "unreadable",
            "error_type": type(exc).__name__,
        }


def _original_fit_path(root: str | Path | None, activity_id: str) -> Path | None:
    base = repo_root(root) / "activities" / "fit"
    for suffix in (".fit", ".zip"):
        candidate = base / f"garmin_{activity_id}_original{suffix}"
        if candidate.is_file():
            return candidate
    return None


def _relative_source_path(root: str | Path | None, path: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root(root)).as_posix()
    except ValueError:
        return path.name


def _fit_source_signature(path: Path) -> dict:
    stat = path.stat()
    return {
        "size_bytes": stat.st_size,
        "modified_time_ns": stat.st_mtime_ns,
    }


def _fit_speed_scan(
    summary: dict,
    *,
    source: str,
    source_signature: dict,
) -> dict:
    available = summary.get("status") == "available"
    return {
        "schema_version": 1,
        "status": summary.get("status") or "unknown",
        "external_speed_sensor": bool(
            available and summary.get("external_speed_sensor") is True
        ),
        "external_speed_sensor_battery_statuses": (
            sorted(
                {
                    str(item).upper()
                    for item in summary.get(
                        "external_speed_sensor_battery_statuses"
                    )
                    or []
                    if item
                }
            )
            if available
            else []
        ),
        "source": source,
        "source_signature": source_signature,
    }


def _recent_mtb_activity_ids(
    index_rows: list,
    activities: dict[str, dict],
    *,
    start: date,
    target: date,
    limit: int = 10,
) -> set[str]:
    candidates = []
    for row in index_rows:
        if not isinstance(row, dict):
            continue
        activity_id = str(row.get("activity_id") or row.get("id") or "")
        activity = activities.get(activity_id, {})
        activity_date = parse_date(row.get("date") or activity.get("date"))
        category = row.get("category") or activity.get("category")
        if (
            activity_id
            and activity_date is not None
            and start <= activity_date <= target
            and category == "mtb"
        ):
            candidates.append((activity_date, activity_id))
    candidates.sort(key=lambda item: item[0], reverse=True)
    return {activity_id for _, activity_id in candidates[:limit]}


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
    latest_attempt = row.get("latest_attempt") if isinstance(row.get("latest_attempt"), dict) else {}
    fetch_status = str(
        latest_attempt.get("status")
        or (row.get("fetch") or {}).get("status")
        or ("success" if row.get("device_fetch_ok") else "failed")
    )
    if row.get("device_fetch_ok") is not True and fetch_status in {
        "failed",
        "unsupported",
        "success_empty",
    }:
        return "unknown_metadata_unavailable"
    if row.get("external_hr_sensor"):
        statuses = [str(item).upper() for item in row.get("external_hr_battery_statuses") or []]
        if "LOW" in statuses:
            return "external_hr_low_battery"
        return "external_hr"
    if row.get("local_or_onboard_hr_sensor") is True or row.get(
        "hr_source_classification"
    ) == "local_or_onboard_standard_metadata":
        return "local_or_onboard_hr"
    if row.get("hr_source_classification") == "unknown_standard_metadata_source":
        return "unknown_standard_metadata_source"
    return "wrist_hr_likely"


def _text_report(report: dict) -> str:
    lines = [
        f"Device Audit - {report['date']}",
        "",
        f"Checked activities: {report['checked_activities']}",
        f"MTB checked: {report['mtb_checked']}",
        f"Coverage: {(report.get('coverage') or {}).get('status', 'unknown')}",
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
    index_rows = index.get("activities") or []
    fit_fallback_activity_ids = _recent_mtb_activity_ids(
        index_rows,
        activities,
        start=start,
        target=target,
    )
    previous_report = read_json(snapshots_dir(root) / "device_audit.json", {})
    previous_mtb_rows = {
        str(row.get("activity_id")): row
        for row in (
            previous_report.get("recent_mtb_devices")
            if isinstance(previous_report, dict)
            else []
        )
        or []
        if isinstance(row, dict) and row.get("activity_id") is not None
    }
    rows = []
    flags = []
    recent_mtb = []

    for row in index_rows:
        if not isinstance(row, dict):
            continue
        activity_id = str(row.get("activity_id") or row.get("id") or "")
        activity = activities.get(activity_id, {})
        act_date = parse_date(row.get("date") or activity.get("date"))
        if not act_date or act_date < start or act_date > target:
            continue
        category = row.get("category") or activity.get("category")
        sensors = row.get("sensors") if isinstance(row.get("sensors"), list) else []
        external_speed_rows = [
            item
            for item in sensors
            if isinstance(item, dict)
            and _normalized_sensor_type(item.get("sensor_type")) == "BIKE_SPEED"
            and is_external_standard_source(item.get("source_type"))
        ]
        fit_path = None
        fit_sources = None
        fit_speed_scan = None
        if activity_id in fit_fallback_activity_ids and not external_speed_rows:
            fit_path = _original_fit_path(root, activity_id)
            if fit_path:
                fit_source = _relative_source_path(root, fit_path)
                fit_signature = _fit_source_signature(fit_path)
                previous_row = previous_mtb_rows.get(activity_id) or {}
                previous_scan = previous_row.get("standard_fit_speed_scan")
                if (
                    isinstance(previous_scan, dict)
                    and previous_scan.get("schema_version") == 1
                    and previous_scan.get("source") == fit_source
                    and previous_scan.get("source_signature") == fit_signature
                ):
                    fit_speed_scan = previous_scan
                    previous_sources = previous_row.get(
                        "standard_fit_device_sources"
                    )
                    if (
                        fit_speed_scan.get("external_speed_sensor") is True
                        and isinstance(previous_sources, dict)
                        and previous_sources.get("status") == "available"
                    ):
                        fit_sources = previous_sources
                else:
                    fit_summary = read_standard_fit_device_sources(fit_path)
                    fit_speed_scan = _fit_speed_scan(
                        fit_summary,
                        source=fit_source,
                        source_signature=fit_signature,
                    )
                    if fit_speed_scan.get("external_speed_sensor") is True:
                        fit_sources = fit_summary
        fit_external_speed = bool(
            fit_speed_scan
            and fit_speed_scan.get("external_speed_sensor") is True
        )
        speed_source_surfaces = []
        if external_speed_rows:
            speed_source_surfaces.append(
                "garmin_devices_and_apps_standard_metadata"
            )
        if fit_external_speed:
            speed_source_surfaces.append("preserved_standard_fit_device_info")
        sensor_types = {
            _normalized_sensor_type(item.get("sensor_type"))
            for item in sensors
            if isinstance(item, dict) and item.get("sensor_type")
        }
        if fit_external_speed:
            sensor_types.add("BIKE_SPEED")
        speed_battery_statuses = {
            str(item.get("battery_status")).upper()
            for item in external_speed_rows
            if item.get("battery_status")
        }
        speed_battery_statuses.update(
            str(item).upper()
            for item in (fit_speed_scan or {}).get(
                "external_speed_sensor_battery_statuses"
            )
            or []
            if item
        )
        normalized = {
            "activity_id": activity_id,
            "date": act_date.isoformat(),
            "name": row.get("name") or activity.get("name"),
            "type": row.get("type") or activity.get("type"),
            "category": category,
            "device_fetch_ok": row.get("device_fetch_ok"),
            "device_fetch_error": row.get("device_fetch_error"),
            "fetch": row.get("fetch"),
            "latest_attempt": row.get("latest_attempt"),
            "last_success_at": row.get("last_success_at"),
            "recording_device": public_recording_device(row.get("recording_device")),
            "apps": _public_app_context(row.get("apps")),
            "sensor_types": sorted(sensor_types),
            "external_hr_sensor": bool(row.get("external_hr_sensor")),
            "local_or_onboard_hr_sensor": row.get("local_or_onboard_hr_sensor"),
            "hr_source_classification": row.get("hr_source_classification"),
            "external_hr_battery_statuses": row.get("external_hr_battery_statuses") or [],
            "external_speed_sensor": bool(
                external_speed_rows or fit_external_speed
            ),
            "external_speed_sensor_battery_statuses": sorted(
                speed_battery_statuses
            ),
            "external_speed_sensor_source_surfaces": speed_source_surfaces,
            "standard_fit_device_sources": fit_sources,
            "standard_fit_speed_scan": fit_speed_scan,
            "standard_fit_source": (
                _relative_source_path(root, fit_path)
                if fit_speed_scan is not None and fit_path is not None
                else None
            ),
        }
        normalized["hr_confidence"] = _hr_confidence(normalized)
        rows.append(normalized)

        if category == "mtb":
            recent_mtb.append(normalized)
            latest_attempt = (
                normalized.get("latest_attempt")
                if isinstance(normalized.get("latest_attempt"), dict)
                else {}
            )
            if (
                normalized.get("device_fetch_ok") is True
                and latest_attempt.get("status") in {"failed", "unsupported"}
            ):
                flags.append(
                    {
                        "type": "device_metadata_refresh_failed_using_cached",
                        "severity": "yellow",
                        "activity_id": activity_id,
                        "date": act_date.isoformat(),
                        "message": (
                            f"{act_date.isoformat()} MTB activity {activity_id} device metadata refresh "
                            "failed; using preserved last-known-good sensor evidence."
                        ),
                    }
                )
            if normalized["hr_confidence"] == "unknown_metadata_unavailable":
                flags.append(
                    {
                        "type": "mtb_hr_source_unknown_metadata_unavailable",
                        "severity": "yellow",
                        "activity_id": activity_id,
                        "date": act_date.isoformat(),
                        "message": (
                            f"{act_date.isoformat()} MTB activity {activity_id} device metadata "
                            "could not be fetched; HR source is unknown, not evidence of wrist HR."
                        ),
                    }
                )
            elif not normalized["external_hr_sensor"]:
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

    eligible = []
    for activity in activities.values():
        activity_date = parse_date(activity.get("date"))
        if (
            activity_date is not None
            and start <= activity_date <= target
            and activity.get("category") in BIKE_DEVICE_CATEGORIES
        ):
            eligible.append(activity)
    eligible_ids = {str(activity.get("id")) for activity in eligible if activity.get("id")}
    covered_ids = {row["activity_id"] for row in rows if row.get("activity_id") in eligible_ids}
    fetched_ok = sum(1 for row in rows if row.get("activity_id") in eligible_ids and row.get("device_fetch_ok") is True)
    coverage_status = (
        "complete"
        if eligible_ids and covered_ids == eligible_ids and fetched_ok == len(eligible_ids)
        else "partial"
        if covered_ids
        else "missing"
    )
    report = {
        "date": target.isoformat(),
        "generated_at": iso_now(DEFAULT_TIMEZONE),
        "source_index": "snapshots/activity_device_index.json",
        "source_surfaces": [
            "snapshots/activity_device_index.json",
            "activities/fit/garmin_<activity_id>_original.{fit,zip} "
            "(recent-MTB speed-only bounded fallback)",
        ],
        "fit_fallback_scope": {
            "category": "mtb",
            "recent_activity_limit": 10,
            "evidence": "external_bike_speed_sensor_only",
            "cache_validation": "source_path_size_and_modified_time_ns",
        },
        "lookback_days": lookback_days,
        "checked_activities": len(rows),
        "mtb_checked": sum(1 for row in rows if row.get("category") == "mtb"),
        "coverage": {
            "status": coverage_status,
            "eligible_activities": len(eligible_ids),
            "indexed_activities": len(covered_ids),
            "successful_fetches": fetched_ok,
            "missing_activities": max(0, len(eligible_ids) - len(covered_ids)),
        },
        "recent_mtb_devices": sorted(recent_mtb, key=lambda item: item.get("date") or "", reverse=True)[:10],
        "flags": flags,
    }
    write_json(snapshots_dir(root) / "device_audit.json", report)
    write_text(snapshots_dir(root) / "device_audit.txt", _text_report(report))
    return report
