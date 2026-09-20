from coach_sync.device_audit import build_device_audit, summarize_activity_devices
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
