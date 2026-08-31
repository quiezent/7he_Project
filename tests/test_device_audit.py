from coach_sync.device_audit import (
    build_device_audit,
    read_standard_fit_device_sources,
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


def test_summarize_activity_devices_surfaces_external_speed_provenance_without_wheel_inference():
    summary = summarize_activity_devices(
        {
            "metadataDTO": {
                "sensors": [
                    {
                        "manufacturer": "DEVELOPMENT",
                        "sourceType": "ANTPLUS",
                        "antplusDeviceType": "BIKE_SPEED",
                        "batteryStatus": "GOOD",
                    }
                ]
            }
        }
    )

    assert summary["external_speed_sensor"] is True
    assert summary["external_speed_sensor_battery_statuses"] == ["GOOD"]
    assert "front" not in str(summary).lower()


def test_speed_sensor_requires_external_standard_source_metadata():
    for source_type in ("LOCAL", "ONBOARD", None):
        sensor = {"antplusDeviceType": "BIKE_SPEED", "batteryStatus": "GOOD"}
        if source_type is not None:
            sensor["sourceType"] = source_type
        summary = summarize_activity_devices(
            {"metadataDTO": {"sensors": [sensor]}}
        )

        assert summary["external_speed_sensor"] is False
        assert summary["external_speed_sensor_battery_statuses"] == []


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


def test_standard_fit_device_summary_surfaces_external_bike_speed_provenance():
    summary = summarize_standard_fit_device_sources(
        [
            {
                "device_index": 5,
                "source_type": "antplus",
                "antplus_device_type": "bike_speed",
                "battery_status": "ok",
                "serial_number": 24994,
                "unknown_24": 24863138,
            },
            {
                "device_index": 6,
                "source_type": "local",
                "antplus_device_type": "bike_speed",
            },
        ]
    )

    assert summary["sensor_types"] == ["BIKE_SPEED"]
    assert summary["external_speed_sensor"] is True
    assert summary["external_speed_sensor_battery_statuses"] == ["OK"]
    assert "calibration" in summary["interpretation_guardrail"]
    assert "wheel circumference" in summary["interpretation_guardrail"]
    assert "24994" not in str(summary)
    assert "24863138" not in str(summary)


def test_standard_fit_reader_includes_antplus_device_type(tmp_path, monkeypatch):
    class Field:
        def __init__(self, name, value):
            self.name = name
            self.value = value

    class Message:
        fields = [
            Field("source_type", "antplus"),
            Field("antplus_device_type", "bike_speed"),
            Field("battery_status", "ok"),
            Field("serial_number", 24994),
        ]

    class FakeFitFile:
        def __init__(self, _source):
            pass

        def parse(self):
            pass

        def get_messages(self, name):
            return [Message()] if name == "device_info" else []

    fit_path = tmp_path / "activity.fit"
    fit_path.write_bytes(b"FIT")
    monkeypatch.setattr("coach_sync.device_audit.FitFile", FakeFitFile)

    summary = read_standard_fit_device_sources(fit_path)

    assert summary["sensor_types"] == ["BIKE_SPEED"]
    assert summary["external_speed_sensor"] is True
    assert summary["external_speed_sensor_battery_statuses"] == ["OK"]
    assert "24994" not in str(summary)


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


def test_device_audit_does_not_reuse_stale_external_speed_boolean_for_local_sensor(
    tmp_path,
):
    write_json(tmp_path / "activities" / "mtb.json", _activity(3, "2026-05-27"))
    write_json(
        tmp_path / "snapshots" / "activity_device_index.json",
        {
            "activities": [
                {
                    "activity_id": "3",
                    "date": "2026-05-27",
                    "name": "Kuala Lumpur Mountain Biking",
                    "type": "mountain_biking",
                    "category": "mtb",
                    "device_fetch_ok": True,
                    "sensors": [
                        {
                            "sensor_type": "BIKE_SPEED",
                            "source_type": "LOCAL",
                            "battery_status": "GOOD",
                        }
                    ],
                    "external_speed_sensor": True,
                    "external_speed_sensor_battery_statuses": ["GOOD"],
                    "external_hr_sensor": False,
                }
            ]
        },
    )

    report = build_device_audit(tmp_path, "2026-05-28")
    device = report["recent_mtb_devices"][0]

    assert device["sensor_types"] == ["BIKE_SPEED"]
    assert device["external_speed_sensor"] is False
    assert device["external_speed_sensor_battery_statuses"] == []


def test_device_audit_uses_bounded_fit_fallback_for_external_speed(
    tmp_path,
    monkeypatch,
):
    class Field:
        def __init__(self, name, value):
            self.name = name
            self.value = value

    class Message:
        fields = [
            Field("source_type", "antplus"),
            Field("antplus_device_type", "bike_speed"),
            Field("battery_status", "ok"),
            Field("serial_number", 24994),
        ]

    class FakeFitFile:
        calls = 0

        def __init__(self, _source):
            type(self).calls += 1

        def parse(self):
            pass

        def get_messages(self, name):
            return [Message()] if name == "device_info" else []

    write_json(tmp_path / "activities" / "mtb.json", _activity(4, "2026-05-27"))
    write_json(
        tmp_path / "snapshots" / "activity_device_index.json",
        {
            "activities": [
                {
                    "activity_id": "4",
                    "date": "2026-05-27",
                    "name": "Kuala Lumpur Mountain Biking",
                    "type": "mountain_biking",
                    "category": "mtb",
                    "device_fetch_ok": True,
                    "sensors": [],
                    "external_hr_sensor": False,
                }
            ]
        },
    )
    fit_path = tmp_path / "activities" / "fit" / "garmin_4_original.fit"
    fit_path.parent.mkdir(parents=True)
    fit_path.write_bytes(b"FIT")
    monkeypatch.setattr("coach_sync.device_audit.FitFile", FakeFitFile)

    build_device_audit(tmp_path, "2026-05-28")
    report = build_device_audit(tmp_path, "2026-05-28")
    device = report["recent_mtb_devices"][0]

    assert FakeFitFile.calls == 1
    assert report["fit_fallback_scope"]["evidence"] == (
        "external_bike_speed_sensor_only"
    )
    assert device["sensor_types"] == ["BIKE_SPEED"]
    assert device["external_speed_sensor"] is True
    assert device["external_speed_sensor_battery_statuses"] == ["OK"]
    assert device["external_speed_sensor_source_surfaces"] == [
        "preserved_standard_fit_device_info"
    ]
    assert device["standard_fit_source"] == (
        "activities/fit/garmin_4_original.fit"
    )
    assert device["standard_fit_device_sources"]["external_speed_sensor"] is True
    assert "24994" not in str(device)
