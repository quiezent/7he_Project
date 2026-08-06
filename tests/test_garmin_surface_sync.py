from __future__ import annotations

import sys
from types import SimpleNamespace

from coach_sync.cleanup import cleanup_derived
from coach_sync.device_audit import build_device_audit
from coach_sync.evidence import load_activities
from coach_sync.garmin_sync import (
    _activity_success_empty_gap,
    _cycling_ftp_summary,
    _fetch_live,
    _fetch_activity_detail_results,
    _record_sync_run,
    _safe_call,
    _training_readiness_capability,
    _training_readiness_sync_failure,
    _unit_system_sync_warning,
    _select_key_activities,
    _write_cycling_ftp_current,
    _write_wellness_payload,
    _write_activity_gear_index,
    _write_activity_device_index,
    _write_key_activity_details,
    _write_merged_activity_index,
)
from coach_sync.io import read_json, write_json
from coach_sync.training_readiness import (
    build_training_readiness_current,
    normalize_training_readiness_payload,
)
from coach_sync.wellness import normalize_wellness_payload


def _activity(activity_id: int = 1, category: str = "mountain_biking") -> dict:
    return {
        "activityId": activity_id,
        "activityName": "Test ride",
        "activityType": {"typeKey": category},
        "startTimeLocal": "2026-07-10 08:00:00",
        "duration": 3600,
        "activityTrainingLoad": 80,
        "averageHR": 130,
    }


def test_safe_call_distinguishes_success_empty_failure_and_success():
    empty = _safe_call("empty", lambda: [])
    full = _safe_call("full", lambda: {"value": 1})
    failed = _safe_call("failed", lambda: (_ for _ in ()).throw(RuntimeError("boom")))

    assert empty["status"] == "success_empty"
    assert full["status"] == "success"
    assert failed["status"] == "failed"
    assert failed["ok"] is False
    assert all(row.get("attempted_at") for row in (empty, full, failed))


def test_cycling_ftp_summary_extracts_endpoint_value_date_and_source():
    summary = _cycling_ftp_summary(
        {
            "latestFunctionalThresholdPower": {
                "calendarDate": "2026-07-25T16:48:44.0",
                "sport": "cycling",
                "functionalThresholdPower": 211,
                "functionalThresholdPowerSource": "AUTO_DETECTED",
                "biometricSourceType": "CHANGE_LOG",
            }
        }
    )

    assert summary == {
        "ftp_w": 211.0,
        "effective_date": "2026-07-25",
        "effective_at": "2026-07-25T16:48:44.0",
        "detection_source": "AUTO_DETECTED",
        "biometric_source_type": "CHANGE_LOG",
        "sport": "CYCLING",
    }
    assert _cycling_ftp_summary({"calendarDate": "2026-07-25"}) is None


def test_cycling_ftp_refresh_preserves_last_known_good_and_latest_attempt(tmp_path):
    first = _write_cycling_ftp_current(
        tmp_path,
        {
            "label": "get_cycling_ftp",
            "ok": True,
            "status": "success",
            "attempted_at": "2026-07-25T20:40:00+08:00",
            "data": {
                "calendarDate": "2026-07-25",
                "sport": "CYCLING",
                "functionalThresholdPower": 211,
                "functionalThresholdPowerSource": "AUTO_DETECTED",
            },
        },
    )
    retained = _write_cycling_ftp_current(
        tmp_path,
        {
            "label": "get_cycling_ftp",
            "ok": False,
            "status": "failed",
            "attempted_at": "2026-07-26T07:30:00+08:00",
            "error": "temporary Garmin failure",
        },
    )

    assert first["status"] == "available_current"
    assert retained["status"] == "available_retained"
    assert retained["ftp_w"] == 211.0
    assert retained["effective_date"] == "2026-07-25"
    assert retained["last_success_at"] == "2026-07-25T20:40:00+08:00"
    assert retained["last_known_good"]["raw_payload"]["functionalThresholdPower"] == 211
    assert retained["latest_attempt"]["status"] == "failed"
    assert retained["latest_attempt"]["error"] == "temporary Garmin failure"
    assert retained["last_attempt_ok"] is False
    assert retained["retention_policy"] == "preserve_last_nonempty_success"


def test_cycling_ftp_semantic_empty_does_not_replace_last_known_good(tmp_path):
    _write_cycling_ftp_current(
        tmp_path,
        {
            "label": "get_cycling_ftp",
            "ok": True,
            "status": "success",
            "attempted_at": "2026-07-25T20:40:00+08:00",
            "data": {
                "calendarDate": "2026-07-25",
                "functionalThresholdPower": 211,
            },
        },
    )
    retained = _write_cycling_ftp_current(
        tmp_path,
        {
            "label": "get_cycling_ftp",
            "ok": True,
            "status": "success",
            "attempted_at": "2026-07-26T07:30:00+08:00",
            "data": {"calendarDate": "2026-07-26"},
        },
    )

    assert retained["status"] == "available_retained"
    assert retained["ftp_w"] == 211.0
    assert retained["latest_attempt"]["status"] == "success_empty"
    assert (
        retained["latest_attempt"]["semantic_error"]
        == "functional_threshold_power_missing_or_invalid"
    )
    assert retained["latest_attempt"]["raw_payload"] == {
        "calendarDate": "2026-07-26"
    }


def test_live_wellness_sync_collects_named_intraday_series_payloads(tmp_path, monkeypatch):
    calls = []

    class Garmin:
        def __init__(self, email, password):
            self.email = email
            self.password = password

        def login(self, tokenstore=None):
            return None

        def get_stats(self, day):
            return {"calendarDate": day, "totalSteps": 100}

        def get_user_summary(self, day):
            return {"calendarDate": day, "averageStressLevel": 20}

        def get_all_day_stress(self, day):
            calls.append(("get_all_day_stress", day))
            return {
                "calendarDate": day,
                "stressValuesArray": [[1, 18], [2, 21]],
                "bodyBatteryValuesArray": [[1, 65], [2, 66]],
            }

        def get_heart_rates(self, day):
            calls.append(("get_heart_rates", day))
            return {
                "calendarDate": day,
                "heartRateValueDescriptors": [
                    {"index": 0, "key": "timestamp"},
                    {"index": 1, "key": "heartrate"},
                ],
                "heartRateValues": [[1, 52], [2, 54]],
            }

        def get_spo2_data(self, day):
            calls.append(("get_spo2_data", day))
            return {
                "calendarDate": day,
                "averageSpO2": 96,
                "avgSleepSpO2": 95,
                "spO2HourlyAverages": [[1, 95], [2, 97]],
            }

        def get_respiration_data(self, day):
            calls.append(("get_respiration_data", day))
            return {
                "calendarDate": day,
                "avgWakingRespirationValue": 14,
                "respirationValuesArray": [[1, 14], [2, -1]],
                "respirationAveragesValuesArray": [[1, 14, 15, 13]],
            }

        def get_body_battery(self, start, end):
            return []

        def get_body_battery_events(self, day):
            return []

        def get_sleep_data(self, day):
            return {}

        def get_hrv_data(self, day):
            return {}

        def get_body_composition(self, day):
            return {}

        def get_training_status(self, day):
            return {"calendarDate": day}

        def get_cycling_ftp(self):
            return {
                "calendarDate": "2026-07-25T16:48:44.0",
                "sport": "CYCLING",
                "functionalThresholdPower": 211,
                "biometricSourceType": "CHANGE_LOG",
            }

        def get_training_readiness(self, day):
            return []

        def get_morning_training_readiness(self, day):
            return []

        def get_devices(self):
            return [{"trainingReadinessCapable": False}]

        def get_unit_system(self):
            return {"unitSystem": "metric"}

    monkeypatch.setenv("GARMIN_EMAIL", "athlete@example.test")
    monkeypatch.setenv("GARMIN_PASSWORD", "secret")
    monkeypatch.setitem(sys.modules, "garminconnect", SimpleNamespace(Garmin=Garmin))

    result = _fetch_live(tmp_path, wellness_days=1, activity_limit=0)
    day = result["wellness_written"][0]
    snapshot = read_json(tmp_path / "snapshots" / f"garmin_wellness_{day}.json", {})
    payload = next(
        item for item in snapshot["payloads"] if item["label"] == "get_all_day_stress"
    )
    heart_rates = next(
        item for item in snapshot["payloads"] if item["label"] == "get_heart_rates"
    )

    assert calls == [
        ("get_all_day_stress", day),
        ("get_heart_rates", day),
        ("get_spo2_data", day),
        ("get_respiration_data", day),
    ]
    assert payload["status"] == "success"
    assert payload["data"]["stressValuesArray"] == [[1, 18], [2, 21]]
    assert payload["data"]["bodyBatteryValuesArray"] == [[1, 65], [2, 66]]
    assert payload["last_success_at"] == payload["attempted_at"]
    assert heart_rates["status"] == "success"
    assert heart_rates["data"]["heartRateValues"] == [[1, 52], [2, 54]]
    assert heart_rates["retention_policy"] == "preserve_last_nonempty_success"
    spo2 = next(item for item in snapshot["payloads"] if item["label"] == "get_spo2_data")
    respiration = next(
        item for item in snapshot["payloads"] if item["label"] == "get_respiration_data"
    )
    assert spo2["data"]["spO2HourlyAverages"] == [[1, 95], [2, 97]]
    assert spo2["series_retention"]["series"][0]["nominal_cadence"] == "hourly"
    assert respiration["data"]["respirationValuesArray"] == [[1, 14], [2, -1]]
    assert respiration["series_retention"]["series"][0]["nominal_cadence"] == (
        "two_minutes"
    )
    assert snapshot["privacy"] == "raw_private_local_only"
    cycling_ftp = read_json(
        tmp_path / "snapshots" / "garmin_cycling_ftp_current.json",
        {},
    )
    assert result["cycling_ftp_available"] is True
    assert cycling_ftp["status"] == "available_current"
    assert cycling_ftp["ftp_w"] == 211.0
    assert cycling_ftp["effective_date"] == "2026-07-25"
    assert cycling_ftp["effective_at"] == "2026-07-25T16:48:44.0"
    assert cycling_ftp["detection_source"] is None
    assert cycling_ftp["biometric_source_type"] == "CHANGE_LOG"


def test_heart_rate_refresh_requires_target_date_series_and_preserves_last_good(tmp_path):
    day = "2026-07-19"
    success = {
        "label": "get_heart_rates",
        "ok": True,
        "status": "success",
        "attempted_at": f"{day}T16:45:00+08:00",
        "data": {
            "calendarDate": day,
            "heartRateValues": [[1784390400000, 52], [1784390520000, 54]],
        },
    }
    _write_wellness_payload(tmp_path, day, [success])

    wrong_date = _write_wellness_payload(
        tmp_path,
        day,
        [
            {
                "label": "get_heart_rates",
                "ok": True,
                "status": "success",
                "attempted_at": f"{day}T17:00:00+08:00",
                "data": {
                    "calendarDate": "2026-07-20",
                    "heartRateValues": [[1784476800000, 60]],
                },
            }
        ],
    )["payloads"][0]

    assert wrong_date["status"] == "success"
    assert wrong_date["data"] == success["data"]
    assert wrong_date["last_attempt_status"] == "failed"
    assert wrong_date["last_attempt_error"] == "response_date_mismatch"
    assert wrong_date["latest_attempt"]["response_date"] == "2026-07-20"

    missing_series = _write_wellness_payload(
        tmp_path,
        day,
        [
            {
                "label": "get_heart_rates",
                "ok": True,
                "status": "success",
                "attempted_at": f"{day}T17:15:00+08:00",
                "data": {"calendarDate": day, "heartRateValues": []},
            }
        ],
    )["payloads"][0]

    assert missing_series["status"] == "success"
    assert missing_series["data"] == success["data"]
    assert missing_series["last_attempt_status"] == "success_empty"
    assert missing_series["latest_attempt"]["data"]["heartRateValues"] == []


def test_all_day_stress_success_empty_is_distinct_from_failure(tmp_path):
    empty = _write_wellness_payload(
        tmp_path,
        "2026-07-17",
        [
            {
                "label": "get_all_day_stress",
                "ok": True,
                "status": "success_empty",
                "attempted_at": "2026-07-17T10:00:00+08:00",
                "data": [],
            }
        ],
    )["payloads"][0]
    failed = _write_wellness_payload(
        tmp_path,
        "2026-07-18",
        [
            {
                "label": "get_all_day_stress",
                "ok": False,
                "status": "failed",
                "attempted_at": "2026-07-18T10:00:00+08:00",
                "error": "timeout",
            }
        ],
    )["payloads"][0]

    assert empty["status"] == "success_empty"
    assert empty["data"] == []
    assert empty["last_attempt_ok"] is True
    assert failed["status"] == "failed"
    assert failed["last_attempt_ok"] is False
    assert failed["last_attempt_error"] == "timeout"


def test_all_day_stress_requires_the_nested_stress_series(tmp_path):
    result = _write_wellness_payload(
        tmp_path,
        "2026-07-17",
        [
            {
                "label": "get_all_day_stress",
                "ok": True,
                "status": "success",
                "attempted_at": "2026-07-17T10:00:00+08:00",
                "data": {
                    "calendarDate": "2026-07-17",
                    "stressValuesArray": [],
                    "bodyBatteryValuesArray": [[1, 65]],
                },
            }
        ],
    )["payloads"][0]

    assert result["status"] == "success_empty"
    assert result["last_attempt_status"] == "success_empty"
    assert result["data"]["bodyBatteryValuesArray"] == [[1, 65]]


def test_all_day_stress_rejects_a_wrong_nested_response_date(tmp_path):
    result = _write_wellness_payload(
        tmp_path,
        "2026-07-17",
        [
            {
                "label": "get_all_day_stress",
                "ok": True,
                "status": "success",
                "attempted_at": "2026-07-17T10:00:00+08:00",
                "data": {
                    "calendarDate": "2026-07-18",
                    "stressValuesArray": [[1, 18]],
                    "bodyBatteryValuesArray": [[1, 65]],
                },
            }
        ],
    )["payloads"][0]

    assert result["status"] == "failed"
    assert result["last_attempt_status"] == "failed"
    assert result["last_attempt_error"] == "response_date_mismatch"
    assert result["data"]["calendarDate"] == "2026-07-18"


def test_wrong_nested_date_does_not_overwrite_retained_target_date_series(tmp_path):
    _write_wellness_payload(
        tmp_path,
        "2026-07-17",
        [
            {
                "label": "get_all_day_stress",
                "ok": True,
                "status": "success",
                "attempted_at": "2026-07-17T10:00:00+08:00",
                "data": {
                    "calendarDate": "2026-07-17",
                    "stressValuesArray": [[1, 18]],
                    "bodyBatteryValuesArray": [[1, 65]],
                },
            }
        ],
    )
    result = _write_wellness_payload(
        tmp_path,
        "2026-07-17",
        [
            {
                "label": "get_all_day_stress",
                "ok": True,
                "status": "success",
                "attempted_at": "2026-07-17T11:00:00+08:00",
                "data": {
                    "calendarDate": "2026-07-18",
                    "stressValuesArray": [[2, 30]],
                    "bodyBatteryValuesArray": [[2, 60]],
                },
            }
        ],
    )["payloads"][0]

    assert result["status"] == "success"
    assert result["data"]["calendarDate"] == "2026-07-17"
    assert result["last_attempt_status"] == "failed"
    assert result["last_attempt_error"] == "response_date_mismatch"
    assert result["latest_attempt"]["data"]["calendarDate"] == "2026-07-18"


def test_all_day_stress_refresh_preserves_last_nonempty_raw_response(tmp_path):
    success = {
        "label": "get_all_day_stress",
        "ok": True,
        "status": "success",
        "attempted_at": "2026-07-17T10:00:00+08:00",
        "data": {"stressValuesArray": [[1, 18]], "bodyBatteryValuesArray": [[1, 65]]},
    }
    _write_wellness_payload(tmp_path, "2026-07-17", [success])
    after_empty = _write_wellness_payload(
        tmp_path,
        "2026-07-17",
        [
            {
                "label": "get_all_day_stress",
                "ok": True,
                "status": "success_empty",
                "attempted_at": "2026-07-17T11:00:00+08:00",
                "data": [],
            }
        ],
    )["payloads"][0]
    after_failure = _write_wellness_payload(
        tmp_path,
        "2026-07-17",
        [
            {
                "label": "get_all_day_stress",
                "ok": False,
                "status": "failed",
                "attempted_at": "2026-07-17T12:00:00+08:00",
                "error": "temporary Garmin failure",
            }
        ],
    )["payloads"][0]

    assert after_empty["status"] == "success"
    assert after_empty["data"] == success["data"]
    assert after_empty["latest_attempt"]["status"] == "success_empty"
    assert after_empty["latest_attempt"]["data"] == []
    assert after_empty["last_attempt_ok"] is True
    assert after_failure["status"] == "success"
    assert after_failure["data"] == success["data"]
    assert after_failure["latest_attempt"]["status"] == "failed"
    assert after_failure["latest_attempt"]["error"] == "temporary Garmin failure"
    assert after_failure["last_attempt_ok"] is False
    assert after_failure["last_success_at"] == "2026-07-17T10:00:00+08:00"


def test_spo2_refresh_preserves_target_day_hourly_evidence_and_attempt_provenance(
    tmp_path,
):
    day = "2026-08-04"
    success = {
        "label": "get_spo2_data",
        "ok": True,
        "status": "success",
        "attempted_at": f"{day}T20:00:00+08:00",
        "data": {
            "calendarDate": day,
            "averageSpO2": 86,
            "spO2HourlyAverages": [[1785772800000, 83], [1785776400000, 84]],
        },
    }
    first = _write_wellness_payload(tmp_path, day, [success])["payloads"][0]
    retained = _write_wellness_payload(
        tmp_path,
        day,
        [
            {
                "label": "get_spo2_data",
                "ok": False,
                "status": "failed",
                "attempted_at": f"{day}T21:00:00+08:00",
                "error": "temporary Garmin failure",
            }
        ],
    )["payloads"][0]

    assert first["series_retention"]["scope"] == (
        "single Garmin calendar-date endpoint response"
    )
    assert first["series_retention"]["series"][0]["retained_count"] == 2
    assert retained["status"] == "success"
    assert retained["data"] == success["data"]
    assert retained["last_attempt_status"] == "failed"
    assert retained["latest_attempt"]["attempted_at"] == f"{day}T21:00:00+08:00"


def test_key_detail_selector_keeps_latest_hike_then_maximizes_modality_diversity():
    def session(activity_id, day, category):
        row = _activity(activity_id, category)
        row["startTimeLocal"] = f"{day} 08:00:00"
        return row

    selected = _select_key_activities(
        [
            session(1, "2026-08-01", "strength_training"),
            session(2, "2026-08-03", "mountain_biking"),
            session(3, "2026-08-04", "hiking"),
            session(4, "2026-08-02", "indoor_cycling"),
            session(5, "2026-07-31", "mountain_biking"),
        ],
        limit=3,
    )

    assert [summary["id"] for _, summary in selected] == ["3", "2", "4"]
    assert [summary["category"] for _, summary in selected] == [
        "hike",
        "mtb",
        "bike_indoor",
    ]


def test_device_index_includes_latest_non_bike_training_but_gear_remains_bike_only(
    tmp_path,
):
    class Client:
        def get_activity(self, activity_id):
            return {"metadataDTO": {"sensors": []}, "summaryDTO": {}}

        def get_activity_gear(self, activity_id):
            return []

    def session(activity_id, day, category):
        row = _activity(activity_id, category)
        row["startTimeLocal"] = f"{day} 08:00:00"
        return row

    activities = [
        session(1, "2026-08-01", "running"),
        session(2, "2026-08-03", "mountain_biking"),
        session(3, "2026-08-04", "hiking"),
    ]
    devices = _write_activity_device_index(
        tmp_path,
        Client(),
        activities,
        max_recent_non_bike=1,
    )
    gear = _write_activity_gear_index(tmp_path, Client(), activities)

    assert {row["activity_id"] for row in devices["activities"]} == {"2", "3"}
    assert {row["activity_id"] for row in gear["activities"]} == {"2"}


def test_merged_index_preserves_last_known_good_when_refresh_fails(tmp_path):
    _write_merged_activity_index(
        tmp_path,
        "activity_gear_index.json",
        "test",
        [
            {
                "activity_id": "1",
                "date": "2026-07-10",
                "gear_fetch_ok": True,
                "fetch": {"status": "success", "fetched_at": "2026-07-10T08:00:00+08:00"},
                "gear": [{"label": "Stumpjumper"}],
            }
        ],
    )
    report = _write_merged_activity_index(
        tmp_path,
        "activity_gear_index.json",
        "test",
        [
            {
                "activity_id": "1",
                "date": "2026-07-10",
                "gear_fetch_ok": False,
                "gear_fetch_error": "temporary",
                "fetch": {
                    "status": "failed",
                    "fetched_at": "2026-07-10T09:00:00+08:00",
                    "error": "temporary",
                },
                "gear": [],
            }
        ],
    )

    row = report["activities"][0]
    assert row["gear_fetch_ok"] is True
    assert row["gear"][0]["label"] == "Stumpjumper"
    assert row["latest_attempt"]["status"] == "failed"
    assert row["last_attempt_ok"] is False
    assert report["last_sync_summary"]["failed"] == 1


def test_activity_detail_fetch_is_shared_for_device_and_self_evaluation_consumers():
    class Client:
        def __init__(self):
            self.calls = []

        def get_activity(self, activity_id):
            self.calls.append(str(activity_id))
            return {"summaryDTO": {"directWorkoutRpe": 40}}

    client = Client()
    rows = [_activity(1), _activity(2, "indoor_cycling")]
    results = _fetch_activity_detail_results(client, rows, max_self_evaluation_fetches=2)

    assert sorted(results) == ["1", "2"]
    assert client.calls == ["1", "2"]


def test_key_activity_detail_and_original_are_preserved_under_activities(tmp_path):
    class Client:
        def get_activity_details(self, activity_id):
            return {"activityId": activity_id, "metricDescriptors": []}

        def get_activity_splits(self, activity_id):
            return {"activityId": activity_id, "lapDTOs": []}

        def get_activity_hr_in_timezones(self, activity_id):
            return []

        def get_activity_power_in_timezones(self, activity_id):
            return []

        def get_activity_weather(self, activity_id):
            return {"temp": 91}

        def download_activity(self, activity_id, download_format):
            return b"PK\x03\x04raw-original"

    activity = _activity(77)
    detail = {
        "77": {
            "label": "get_activity",
            "ok": True,
            "status": "success",
            "attempted_at": "2026-07-10T08:00:00+08:00",
            "data": {"summaryDTO": {"activityTrainingLoad": 80}},
        }
    }
    report = _write_key_activity_details(
        tmp_path,
        Client(),
        [activity],
        detail,
        limit=1,
        original_download_format="original",
    )

    detail_path = tmp_path / "activities" / "details" / "garmin_77_detail.json"
    original_path = tmp_path / "activities" / "fit" / "garmin_77_original.zip"
    assert report["attempted"] == 1
    assert detail_path.exists()
    assert original_path.exists()
    cleanup_derived(tmp_path, apply=True)
    assert detail_path.exists()
    assert original_path.exists()
    assert len(load_activities(tmp_path)) == 0


def test_failed_key_detail_refresh_preserves_last_known_good_raw_evidence(tmp_path):
    class Client:
        def __init__(self):
            self.fail = False

        def _result(self, activity_id):
            if self.fail:
                raise RuntimeError("temporary Garmin failure")
            return {"activityId": activity_id, "samples": [1, 2, 3]}

        get_activity_details = _result
        get_activity_splits = _result
        get_activity_hr_in_timezones = _result
        get_activity_power_in_timezones = _result
        get_activity_weather = _result

        def download_activity(self, activity_id, download_format):
            if self.fail:
                raise RuntimeError("temporary download failure")
            return b"FITRAW"

    activity = _activity(78)
    activity_call = {
        "78": {
            "label": "get_activity",
            "ok": True,
            "status": "success",
            "attempted_at": "2026-07-10T08:00:00+08:00",
            "data": {"metadataDTO": {"sensors": []}},
        }
    }
    client = Client()
    _write_key_activity_details(
        tmp_path,
        client,
        [activity],
        activity_call,
        limit=1,
        original_download_format="original",
    )
    client.fail = True
    _write_key_activity_details(
        tmp_path,
        client,
        [activity],
        activity_call,
        limit=1,
        original_download_format="original",
    )

    detail = read_json(
        tmp_path / "activities" / "details" / "garmin_78_detail.json",
        {},
    )
    assert detail["calls"]["details"]["status"] == "success"
    assert detail["calls"]["details"]["data"]["samples"] == [1, 2, 3]
    assert detail["calls"]["details"]["latest_attempt"]["status"] == "failed"
    assert detail["calls"]["original_download"]["stored_path"].endswith("garmin_78_original.fit")
    assert detail["calls"]["original_download"]["latest_attempt"]["status"] == "failed"


def test_failed_device_fetch_is_unknown_not_wrist_hr(tmp_path):
    write_json(tmp_path / "activities" / "garmin_1.json", _activity(1))
    write_json(
        tmp_path / "snapshots" / "activity_device_index.json",
        {
            "activities": [
                {
                    "activity_id": "1",
                    "date": "2026-07-10",
                    "category": "mtb",
                    "device_fetch_ok": False,
                    "device_fetch_error": "timeout",
                    "fetch": {"status": "failed", "error": "timeout"},
                    "sensors": [],
                    "external_hr_sensor": False,
                }
            ]
        },
    )

    report = build_device_audit(tmp_path, "2026-07-10")
    flag_types = {flag["type"] for flag in report["flags"]}
    assert "mtb_hr_source_unknown_metadata_unavailable" in flag_types
    assert "mtb_wrist_hr_likely" not in flag_types
    assert report["recent_mtb_devices"][0]["hr_confidence"] == "unknown_metadata_unavailable"


def test_nonempty_activity_without_sensor_metadata_is_unknown_not_wrist_hr(tmp_path):
    class Client:
        def get_activity(self, activity_id):
            return {"summaryDTO": {"activityId": int(activity_id)}}

    activity = _activity(1)
    write_json(tmp_path / "activities" / "garmin_1.json", activity)
    index = _write_activity_device_index(tmp_path, Client(), [activity])
    row = index["activities"][0]
    report = build_device_audit(tmp_path, "2026-07-10")

    assert row["fetch"]["endpoint_status"] == "success"
    assert row["fetch"]["status"] == "success_empty"
    assert row["fetch"]["semantic_error"] == "metadataDTO.sensors_missing"
    assert row["device_fetch_ok"] is False
    assert report["recent_mtb_devices"][0]["hr_confidence"] == "unknown_metadata_unavailable"
    assert {flag["type"] for flag in report["flags"]} == {
        "mtb_hr_source_unknown_metadata_unavailable"
    }


def test_training_readiness_device_capability_is_tri_state():
    assert _training_readiness_capability({"status": "failed"}) == (None, None)
    assert _training_readiness_capability(
        {"status": "success_empty", "data": []}
    ) == (None, None)
    assert _training_readiness_capability(
        {"status": "success", "data": [{"displayName": "Fenix"}]}
    ) == (None, 1)
    assert _training_readiness_capability(
        {"status": "success", "data": [{"trainingReadinessCapable": False}]}
    ) == (False, 1)
    assert _training_readiness_capability(
        {
            "status": "success",
            "data": [
                {"trainingReadinessCapable": False},
                {"trainingReadinessCapable": True},
            ],
        }
    ) == (True, 2)


def test_training_readiness_has_separate_schema_and_endpoint_health():
    snapshot = {
        "date": "2026-07-10",
        "fetched_at": "2026-07-10T08:00:00+08:00",
        "payloads": [
            {
                "label": "get_training_readiness",
                "ok": True,
                "status": "success",
                "data": [
                    {
                        "calendarDate": "2026-07-10",
                        "trainingReadinessScore": 72,
                        "readinessLevel": "HIGH",
                        "hrvFactor": 80,
                    }
                ],
            },
            {
                "label": "get_morning_training_readiness",
                "ok": True,
                "status": "success_empty",
                "data": None,
            },
        ],
    }

    normalized = normalize_training_readiness_payload(snapshot)
    assert normalized["status"] == "available"
    assert normalized["score"] == 72
    assert normalized["level"] == "HIGH"
    assert normalized["factors"]["hrvFactor"] == 80
    assert normalized["decision_use"] == "context_only_custom_readiness_remains_authoritative"


def test_legacy_training_readiness_failure_without_explicit_status_stays_failed():
    normalized = normalize_training_readiness_payload(
        {
            "date": "2026-07-10",
            "payloads": [
                {
                    "label": "get_training_readiness",
                    "ok": False,
                    "error": "timeout",
                }
            ],
        }
    )

    assert normalized["status"] == "failed"
    assert normalized["endpoint_health"][0]["status"] == "failed"


def test_training_readiness_rejects_wrong_date_and_unrecognized_content():
    wrong_date = normalize_training_readiness_payload(
        {
            "date": "2026-07-10",
            "payloads": [
                {
                    "label": "get_training_readiness",
                    "ok": True,
                    "status": "success",
                    "data": [
                        {
                            "calendarDate": "2026-07-11",
                            "trainingReadinessScore": 99,
                        }
                    ],
                }
            ],
        }
    )
    unrecognized = normalize_training_readiness_payload(
        {
            "date": "2026-07-10",
            "payloads": [
                {
                    "label": "get_training_readiness",
                    "ok": True,
                    "status": "success",
                    "data": {"trainingReadiness": []},
                }
            ],
        }
    )

    assert wrong_date["status"] == "unrecognized_or_wrong_date"
    assert wrong_date["score"] is None
    assert unrecognized["status"] == "unrecognized_or_wrong_date"
    assert unrecognized["source_endpoint"] is None


def test_training_readiness_preserves_valid_zero_score():
    normalized = normalize_training_readiness_payload(
        {
            "date": "2026-07-10",
            "payloads": [
                {
                    "label": "get_training_readiness",
                    "ok": True,
                    "status": "success",
                    "data": {
                        "calendarDate": "2026-07-10",
                        "trainingReadinessScore": 0,
                    },
                }
            ],
        }
    )

    assert normalized["status"] == "available"
    assert normalized["score"] == 0


def test_training_readiness_surfaces_stale_source_snapshot(tmp_path):
    write_json(
        tmp_path / "snapshots" / "garmin_training_readiness_2026-07-09.json",
        {
            "date": "2026-07-09",
            "payloads": [
                {
                    "label": "get_training_readiness",
                    "ok": True,
                    "status": "success",
                    "data": {
                        "calendarDate": "2026-07-09",
                        "trainingReadinessScore": 72,
                    },
                }
            ],
        },
    )

    result = build_training_readiness_current(tmp_path, "2026-07-10")

    assert result["status"] == "stale"
    assert result["source_availability_status"] == "available"
    assert result["target_date"] == "2026-07-10"
    assert result["source_snapshot_date"] == "2026-07-09"
    assert result["freshness"] == {"status": "stale", "age_days": 1}
    assert {flag["type"] for flag in result["flags"]} == {
        "training_readiness_stale"
    }


def test_training_readiness_empty_is_classified_as_device_unsupported(tmp_path):
    write_json(
        tmp_path / "snapshots" / "garmin_training_readiness_2026-07-10.json",
        {
            "date": "2026-07-10",
            "payloads": [
                {
                    "label": "get_training_readiness",
                    "ok": True,
                    "status": "success_empty",
                    "data": [],
                }
            ],
        },
    )
    write_json(
        tmp_path / "snapshots" / "garmin_device_capabilities_2026-07-10.json",
        {"date": "2026-07-10", "training_readiness_capable": False},
    )

    result = build_training_readiness_current(tmp_path, "2026-07-10")
    assert result["status"] == "unsupported_by_registered_devices"
    assert result["flags"] == []


def test_live_sync_health_helpers_surface_capable_readiness_failure_activity_gap_and_unit_warning(
    tmp_path,
):
    readiness_payloads = [
        {"label": "get_training_readiness", "status": "success_empty", "ok": True},
        {"label": "get_morning_training_readiness", "status": "failed", "ok": False},
    ]
    readiness_failure = _training_readiness_sync_failure(True, readiness_payloads)
    assert readiness_failure["source"] == "training_readiness"
    assert _training_readiness_sync_failure(False, readiness_payloads) is None
    assert _training_readiness_sync_failure(
        True,
        [{"label": "get_training_readiness", "status": "success", "ok": True}],
    ) is None

    write_json(tmp_path / "activities" / "garmin_1.json", _activity(1))
    activity_gap = _activity_success_empty_gap(
        tmp_path,
        5,
        {"label": "get_activities", "status": "success_empty", "ok": True, "data": []},
    )
    assert activity_gap["source"] == "activities"
    assert _activity_success_empty_gap(
        tmp_path,
        0,
        {"label": "get_activities", "status": "success_empty", "ok": True, "data": []},
    ) is None

    warning = _unit_system_sync_warning(
        {"label": "get_unit_system", "status": "failed", "ok": False}
    )
    assert warning["source"] == "unit_system"
    assert _unit_system_sync_warning(
        {"label": "get_unit_system", "status": "success", "ok": True}
    ) is None


def test_rebuild_run_does_not_erase_last_live_sync_status(tmp_path):
    live = {"status": "ok", "auth_method": "tokenstore", "fetched_at": "2026-07-10T08:00:00+08:00"}
    _record_sync_run(tmp_path, live, rebuild_only=False, decision_only=True)
    before = read_json(tmp_path / "snapshots" / "last_live_sync_status.json", {})

    _record_sync_run(
        tmp_path,
        {"status": "skipped", "reason": "rebuild_only"},
        rebuild_only=True,
        decision_only=True,
    )
    after = read_json(tmp_path / "snapshots" / "last_live_sync_status.json", {})

    assert before == after
    ledger = read_json(tmp_path / "snapshots" / "sync_run_ledger.json", {})
    assert [row["run_type"] for row in ledger["entries"]] == ["live_sync", "rebuild"]


def test_wellness_daily_summaries_merge_complementary_non_null_fields():
    row = normalize_wellness_payload(
        {
            "date": "2026-07-10",
            "fetched_at": "2026-07-10T08:00:00+08:00",
            "payloads": [
                {
                    "label": "get_stats",
                    "ok": True,
                    "data": {
                        "calendarDate": "2026-07-10",
                        "totalSteps": 1234,
                        "restingHeartRate": None,
                        "wellnessEndTimeLocal": "2026-07-10T07:59:00.0",
                    },
                },
                {
                    "label": "get_user_summary",
                    "ok": True,
                    "data": {"restingHeartRate": 49, "averageStressLevel": 22},
                },
            ],
        }
    )

    assert row["steps"] == 1234
    assert row["resting_hr"] == 49
    assert row["avg_stress"] == 22
    assert row["source_fetched_at"] == "2026-07-10T08:00:00+08:00"
    assert row["source_data_cutoff_local"] == "2026-07-10T07:59:00+08:00"
    assert row["source_data_cutoff_source"] == "daily_summary.wellnessEndTimeLocal"


def test_wellness_cutoff_normalizes_mixed_iso_and_local_epoch_milliseconds():
    # 2026-07-10 08:30 encoded as Garmin's local-wall-clock epoch convention.
    sleep_end_local_ms = 1783672200000
    row = normalize_wellness_payload(
        {
            "date": "2026-07-10",
            "payloads": [
                {
                    "label": "get_stats",
                    "ok": True,
                    "data": {
                        "calendarDate": "2026-07-10",
                        "wellnessEndTimeLocal": "2026-07-10T07:59:00",
                    },
                },
                {
                    "label": "get_sleep_data",
                    "ok": True,
                    "data": {
                        "dailySleepDTO": {
                            "calendarDate": "2026-07-10",
                            "sleepEndTimestampLocal": sleep_end_local_ms,
                        }
                    },
                },
            ],
        }
    )

    assert row["source_data_cutoff_local"] == "2026-07-10T08:30:00+08:00"
    assert row["source_data_cutoff_source"] == "sleep.dailySleepDTO.sleepEndTimestampLocal"
    cutoffs = {item["source"]: item for item in row["source_data_cutoffs"]}
    assert cutoffs["sleep.dailySleepDTO.sleepEndTimestampLocal"]["encoding"] == "local_epoch_ms"
