from coach_sync.device_audit import (
    build_device_audit,
    summarize_activity_devices,
    summarize_standard_fit_device_sources,
)
from coach_sync.io import write_json


def _activity(activity_id, day):
    return {
        "activityId": activity_id,
        "activityName": "Kuala Lumpur Mountain Biking",
        "activityType": {"typeKey": "mountain_biking"},
        "startTimeLocal": f"{day} 08:00:00",
        "duration": 3600,
    }


def test_summarize_activity_devices_detects_external_hr():
    payload = {
        "metadataDTO": {
            "manufacturer": "GARMIN",
            "deviceMetaDataDTO": {"deviceId": "123"},
            "fileFormat": {"formatKey": "fit"},
            "sensors": [
                {
                    "manufacturer": "GARMIN",
                    "sourceType": "ANTPLUS",
                    "antplusDeviceType": "HEART_RATE",
                    "batteryStatus": "OK",
                }
            ],
        }
    }

    summary = summarize_activity_devices(payload)

    assert summary["external_hr_sensor"] is True
    assert summary["external_hr_battery_statuses"] == ["OK"]
    assert summary["hr_source_classification"] == "external_standard_metadata"


def test_summarize_activity_devices_distinguishes_local_hr_from_external_hr():
    summary = summarize_activity_devices(
        {
            "metadataDTO": {
                "sensors": [
                    {
                        "manufacturer": "GARMIN",
                        "sourceType": "LOCAL",
                        "antplusDeviceType": "HEART_RATE",
                    }
                ]
            }
        }
    )

    assert summary["external_hr_sensor"] is False
    assert summary["local_or_onboard_hr_sensor"] is True
    assert summary["hr_source_classification"] == (
        "local_or_onboard_standard_metadata"
    )


def test_standard_fit_device_summary_is_bounded_and_ignores_proprietary_fields():
    summary = summarize_standard_fit_device_sources(
        [
            {
                "device_index": "creator",
                "manufacturer": "garmin",
                "garmin_product": 3290,
                "software_version": 28.02,
                "source_type": "local",
                "serial_number": 123456,
                "unknown_147": "HRM200:proprietary-paired-name",
            },
            {
                "device_index": 2,
                "source_type": "local",
                "device_type": 0,
            },
        ]
    )

    assert summary["source_types"] == ["local"]
    assert summary["local_or_onboard_only"] is True
    assert summary["external_device_source_present"] is False
    assert summary["creator"] == {
        "manufacturer": "garmin",
        "product": 3290,
        "software_version": 28.02,
        "source_type": "local",
    }
    assert "123456" not in str(summary)
    assert "HRM200" not in str(summary)


def test_device_audit_flags_mtb_without_external_hr(tmp_path):
    write_json(tmp_path / "activities" / "mtb.json", _activity(1, "2026-05-27"))
    write_json(
        tmp_path / "snapshots" / "activity_device_index.json",
        {
            "activities": [
                {
                    "activity_id": "1",
                    "date": "2026-05-27",
                    "name": "Kuala Lumpur Mountain Biking",
                    "type": "mountain_biking",
                    "category": "mtb",
                    "device_fetch_ok": True,
                    "recording_device": {
                        "manufacturer": "GARMIN",
                        "device_id": "SENSITIVE_DEVICE_IDENTIFIER",
                    },
                    "apps": {
                        "device_application_installation_id": "SENSITIVE_APP_IDENTIFIER",
                        "file_format": "fit",
                    },
                    "sensors": [{"sensor_type": "BIKE_POWER"}],
                    "external_hr_sensor": False,
                    "external_hr_battery_statuses": [],
                }
            ]
        },
    )

    report = build_device_audit(tmp_path, "2026-05-28")

    assert report["mtb_checked"] == 1
    assert report["flags"][0]["type"] == "mtb_wrist_hr_likely"
    assert report["recent_mtb_devices"][0]["recording_device"] == {
        "manufacturer": "GARMIN"
    }
    assert "SENSITIVE_DEVICE_IDENTIFIER" not in str(report)
    assert "SENSITIVE_APP_IDENTIFIER" not in str(report)


def test_device_audit_flags_low_external_hr_battery(tmp_path):
    write_json(tmp_path / "activities" / "mtb.json", _activity(2, "2026-05-27"))
    write_json(
        tmp_path / "snapshots" / "activity_device_index.json",
        {
            "activities": [
                {
                    "activity_id": "2",
                    "date": "2026-05-27",
                    "name": "Kuala Lumpur Mountain Biking",
                    "type": "mountain_biking",
                    "category": "mtb",
                    "device_fetch_ok": True,
                    "sensors": [{"sensor_type": "HEART_RATE", "battery_status": "LOW"}],
                    "external_hr_sensor": True,
                    "external_hr_battery_statuses": ["LOW"],
                }
            ]
        },
    )

    report = build_device_audit(tmp_path, "2026-05-28")

    assert report["mtb_checked"] == 1
    assert report["flags"][0]["type"] == "external_hr_battery_low"
