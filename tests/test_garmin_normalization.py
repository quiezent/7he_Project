import json
from datetime import datetime, timedelta, timezone

from coach_sync.activity_profile import build_activity_profile
from coach_sync.evidence import load_latest_training_status, load_latest_wellness
from coach_sync.io import write_json
from coach_sync.load_model import build_activity_summary_index, build_modality_load_rollups
from coach_sync.readiness import build_readiness
from coach_sync.training_status import build_training_status_current
from coach_sync.wellness import build_wellness_trends, normalize_wellness_payload


def _write_training_status(root, day: str) -> None:
    write_json(
        root / "snapshots" / f"garmin_training_status_{day}.json",
        {
            "date": day,
            "payload": {
                "ok": True,
                "data": {
                    "mostRecentTrainingStatus": {
                        "latestTrainingStatusData": {
                            "dev": {
                                "primaryTrainingDevice": True,
                                "trainingStatusFeedbackPhrase": "MAINTAINING_2",
                                "acuteTrainingLoadDTO": {
                                    "acwrStatus": "OPTIMAL",
                                    "dailyAcuteChronicWorkloadRatio": 0.8,
                                },
                            }
                        }
                    }
                },
            },
        },
    )


def test_failed_dated_payloads_do_not_satisfy_wellness_or_training_status_freshness(tmp_path):
    write_json(
        tmp_path / "snapshots" / "garmin_wellness_2026-04-29.json",
        {
            "date": "2026-04-29",
            "payloads": [{"label": "get_stats", "ok": True, "data": {"restingHeartRate": 48}}],
        },
    )
    write_json(
        tmp_path / "snapshots" / "garmin_wellness_2026-04-30.json",
        {
            "date": "2026-04-30",
            "payloads": [{"label": "get_stats", "ok": False, "error": "timeout"}],
        },
    )
    _write_training_status(tmp_path, "2026-04-29")
    write_json(
        tmp_path / "snapshots" / "garmin_training_status_2026-04-30.json",
        {
            "date": "2026-04-30",
            "payload": {"ok": False, "error": "timeout"},
        },
    )

    wellness_date, _ = load_latest_wellness(tmp_path, "2026-04-30")
    status_date, _ = load_latest_training_status(tmp_path, "2026-04-30")

    assert wellness_date is not None and wellness_date.isoformat() == "2026-04-29"
    assert status_date is not None and status_date.isoformat() == "2026-04-29"


def test_wellness_normalizes_nested_sleep_body_battery_and_hrv(tmp_path):
    write_json(
        tmp_path / "snapshots" / "garmin_wellness_2026-04-30.json",
        {
            "date": "2026-04-30",
            "payloads": [
                {
                    "label": "get_stats",
                    "ok": True,
                    "data": {
                        "calendarDate": "2026-04-30",
                        "bodyBatteryMostRecentValue": 33,
                        "bodyBatteryAtWakeTime": 65,
                        "averageStressLevel": 33,
                        "restingHeartRate": 46,
                    },
                },
                {
                    "label": "get_sleep_data",
                    "ok": True,
                    "data": {
                        "avgOvernightHrv": 51,
                        "hrvStatus": "BALANCED",
                        "dailySleepDTO": {
                            "calendarDate": "2026-04-30",
                            "sleepTimeSeconds": 18000,
                            "awakeSleepSeconds": 1800,
                            "sleepScores": {"overall": {"value": 64, "qualifierKey": "FAIR"}},
                        },
                    },
                },
            ],
        },
    )
    _write_training_status(tmp_path, "2026-04-30")

    trends = build_wellness_trends(tmp_path, "2026-04-30")
    readiness = build_readiness(tmp_path, "2026-04-30")

    assert trends["latest"]["sleep_score"] == 64
    assert trends["latest"]["body_battery_current"] == 33
    assert trends["latest"]["overnight_hrv"] == 51
    assert readiness["readiness_score"] == 58.0
    assert any(reason["type"] == "primary_short_sleep" for reason in readiness["reasons"])
    assert (tmp_path / "snapshots" / "readiness_features_2026-04-30.json").exists()


def test_wellness_normalizes_local_epoch_sleep_without_double_timezone_offset(tmp_path):
    def local_wall_epoch_ms(value: str) -> int:
        # Garmin's *Local epoch fields encode the local wall clock as if it
        # were UTC; they are not instants that need another +08 conversion.
        wall_clock = datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
        return int(wall_clock.timestamp() * 1000)

    write_json(
        tmp_path / "snapshots" / "garmin_wellness_2026-05-29.json",
        {
            "date": "2026-05-29",
            "payloads": [
                {
                    "label": "get_sleep_data",
                    "ok": True,
                    "data": {
                        "dailySleepDTO": {
                            "calendarDate": "2026-05-29",
                            "sleepStartTimestampLocal": local_wall_epoch_ms(
                                "2026-05-29T01:10:00"
                            ),
                            "sleepEndTimestampLocal": local_wall_epoch_ms(
                                "2026-05-29T06:40:00"
                            ),
                            "sleepTimeSeconds": 18000,
                            "awakeSleepSeconds": 1800,
                            "napTimeSeconds": 0,
                        }
                    },
                }
            ],
        },
    )

    latest = build_wellness_trends(tmp_path, "2026-05-29")["latest"]

    assert latest["sleep_start_local"] == "2026-05-29T01:10+08:00"
    assert latest["sleep_end_local"] == "2026-05-29T06:40+08:00"
    assert latest["sleep_window_hours"] == 5.5
    assert latest["primary_sleep_hours"] == 5.0
    assert latest["nap_hours_reported"] == 0.0
    assert latest["total_sleep_hours_reported"] == 5.0
    assert latest["nap_reporting_status"] == "reported_zero_not_proof_of_no_nap"
    assert latest["total_sleep_reporting_status"] == "primary_plus_garmin_reported_nap"
    assert "does not prove" in latest["sleep_duration_provenance"]["nap_zero_caveat"]


def test_wellness_prefers_dedicated_body_battery_endpoint(tmp_path):
    write_json(
        tmp_path / "snapshots" / "garmin_wellness_2026-05-06.json",
        {
            "date": "2026-05-06",
            "payloads": [
                {
                    "label": "get_stats",
                    "ok": True,
                    "data": {
                        "calendarDate": "2026-05-06",
                        "bodyBatteryMostRecentValue": 33,
                        "bodyBatteryChargedValue": 28,
                        "bodyBatteryDrainedValue": 0,
                        "averageStressLevel": 11,
                    },
                },
                {
                    "label": "get_body_battery",
                    "ok": True,
                    "data": [
                        {
                            "date": "2026-05-06",
                            "charged": 63,
                            "drained": 18,
                            "startTimestampLocal": "2026-05-06T00:00:00.0",
                            "endTimestampLocal": "2026-05-06T10:05:00.0",
                            "bodyBatteryValuesArray": [
                                [1778019840000, 68],
                                [1778021280000, 67],
                                [1778031360000, 50],
                            ],
                        }
                    ],
                },
            ],
        },
    )

    trends = build_wellness_trends(tmp_path, "2026-05-06")

    assert trends["latest"]["body_battery_current"] == 50
    assert trends["latest"]["body_battery_charge"] == 63
    assert trends["latest"]["body_battery_drain"] == 18
    assert trends["latest"]["body_battery_source"] == "get_body_battery"
    assert trends["latest"]["body_battery_end_time_local"] == "2026-05-06T10:05:00.0"


def test_wellness_surfaces_all_day_series_without_replacing_readiness_body_battery(tmp_path):
    kl = timezone(timedelta(hours=8))

    def stamp(hour: int, minute: int) -> int:
        return int(datetime(2026, 5, 6, hour, minute, tzinfo=kl).timestamp() * 1000)

    write_json(
        tmp_path / "snapshots" / "garmin_wellness_2026-05-06.json",
        {
            "date": "2026-05-06",
            "payloads": [
                {
                    "label": "get_stats",
                    "ok": True,
                    "data": {
                        "calendarDate": "2026-05-06",
                        "bodyBatteryMostRecentValue": 33,
                        "averageStressLevel": 11,
                    },
                },
                {
                    "label": "get_body_battery",
                    "ok": True,
                    "data": [
                        {
                            "date": "2026-05-06",
                            "bodyBatteryValuesArray": [[stamp(10, 0), 50]],
                        }
                    ],
                },
                {
                    "label": "get_all_day_stress",
                    "ok": True,
                    "status": "success",
                    "last_attempt_status": "success",
                    "last_success_at": "2026-05-06T12:02:00+08:00",
                    "data": {
                        "calendarDate": "2026-05-06",
                        "endTimestampLocal": "2026-05-06T12:02:00+08:00",
                        "stressValueDescriptorsDTOList": [
                            {"index": 0, "key": "timestamp"},
                            {"index": 1, "key": "stressLevel"},
                        ],
                        "stressValuesArray": [
                            [stamp(10, 0), -1],
                            [stamp(10, 3), 18],
                            [stamp(10, 6), 22],
                        ],
                        "bodyBatteryValueDescriptorsDTOList": [
                            {
                                "bodyBatteryValueDescriptorIndex": 0,
                                "bodyBatteryValueDescriptorKey": "timestamp",
                            },
                            {
                                "bodyBatteryValueDescriptorIndex": 2,
                                "bodyBatteryValueDescriptorKey": "bodyBatteryLevel",
                            },
                        ],
                        "bodyBatteryValuesArray": [
                            [stamp(10, 0), "MEASURED", 80, 2.0],
                            [stamp(10, 6), "MEASURED", 82, 2.0],
                        ],
                    },
                },
            ],
        },
    )

    latest = build_wellness_trends(tmp_path, "2026-05-06")["latest"]

    assert latest["body_battery_current"] == 50
    assert latest["body_battery_source"] == "get_body_battery"
    assert latest["all_day_stress_sample_count"] == 3
    assert latest["all_day_stress_valid_sample_count"] == 2
    assert latest["all_day_stress_sentinel_sample_count"] == 1
    assert latest["all_day_stress_start_time_local"] == "2026-05-06T10:03:00+08:00"
    assert latest["all_day_stress_end_time_local"] == "2026-05-06T10:06:00+08:00"
    assert latest["all_day_stress_material_unavailable_run_count"] == 0
    assert latest["all_day_stress_material_unavailable_minutes"] == 0.0
    assert latest["all_day_stress_low_positive_reward_eligible"] is False
    assert latest["all_day_stress_coverage_sufficient"] is False
    assert "sample_count_too_low" in latest["all_day_stress_sufficiency_issues"]
    assert "series_does_not_start_near_midnight" in (
        latest["all_day_stress_sufficiency_issues"]
    )
    assert latest["all_day_body_battery_sample_count"] == 2
    assert latest["all_day_body_battery_latest_value"] == 82
    assert latest["all_day_body_battery_latest_timestamp_local"] == (
        "2026-05-06T10:06:00+08:00"
    )
    assert latest["all_day_stress_latest_attempt_status"] == "success"
    assert latest["all_day_stress_endpoint_status"] == "success"
    assert latest["all_day_stress_last_success_at"] == "2026-05-06T12:02:00+08:00"


def test_wellness_marks_material_internal_stress_unavailability_as_no_positive_credit(tmp_path):
    kl = timezone(timedelta(hours=8))
    start = datetime(2026, 5, 6, 8, 0, tzinfo=kl)
    rows = []
    for index in range(20):
        stamp = int((start + timedelta(minutes=index * 3)).timestamp() * 1000)
        rows.append([stamp, -1 if 4 <= index < 14 else 18])
    write_json(
        tmp_path / "snapshots" / "garmin_wellness_2026-05-06.json",
        {
            "date": "2026-05-06",
            "payloads": [
                {
                    "label": "get_stats",
                    "ok": True,
                    "data": {
                        "calendarDate": "2026-05-06",
                        "averageStressLevel": 18,
                    },
                },
                {
                    "label": "get_all_day_stress",
                    "ok": True,
                    "status": "success",
                    "data": {
                        "calendarDate": "2026-05-06",
                        "stressValuesArray": rows,
                    },
                },
            ],
        },
    )

    latest = build_wellness_trends(tmp_path, "2026-05-06")["latest"]

    assert latest["all_day_stress_material_unavailable_run_count"] == 1
    assert latest["all_day_stress_material_unavailable_minutes"] == 30.0
    assert latest["all_day_stress_longest_material_unavailable_minutes"] == 30.0
    assert latest["all_day_stress_low_positive_reward_eligible"] is False


def _dense_stress_rows(day: str, end_hour: int = 20, end_minute: int = 0) -> list[list[int]]:
    kl = timezone(timedelta(hours=8))
    start = datetime.fromisoformat(day).replace(tzinfo=kl)
    end_minutes = end_hour * 60 + end_minute
    return [
        [int((start + timedelta(minutes=minute)).timestamp() * 1000), 18]
        for minute in range(0, end_minutes + 1, 3)
    ]


def _normalized_all_day_stress(
    *,
    outer_date: str = "2026-05-06",
    nested_date: str | None = "2026-05-06",
    rows: object | None = None,
    status: str = "success",
    ok: bool = True,
    cutoff_local: str | None = "2026-05-06T20:00:00+08:00",
    as_of: str = "2026-05-06T20:30:00+08:00",
    average_stress: int = 18,
) -> dict:
    data: dict = {
        "stressValuesArray": _dense_stress_rows("2026-05-06") if rows is None else rows,
    }
    if nested_date is not None:
        data["calendarDate"] = nested_date
    if cutoff_local is not None:
        data["endTimestampLocal"] = cutoff_local
    snapshot = {
        "date": outer_date,
        "payloads": [
            {
                "label": "get_stats",
                "ok": True,
                "data": {
                    "calendarDate": outer_date,
                    "averageStressLevel": average_stress,
                },
            },
            {
                "label": "get_all_day_stress",
                "ok": ok,
                "status": status,
                "data": data,
            },
        ],
    }
    return normalize_wellness_payload(snapshot, as_of=as_of)


def test_wellness_all_day_low_stress_credit_requires_dense_target_day_coverage():
    latest = _normalized_all_day_stress()

    assert latest["all_day_stress_low_positive_reward_eligible"] is True
    assert latest["all_day_stress_coverage_sufficient"] is True
    assert latest["all_day_stress_sufficiency_issues"] == []
    assert latest["all_day_stress_sample_count"] == 401
    assert latest["all_day_stress_parsed_sample_count"] == 401
    assert latest["all_day_stress_target_date_sample_count"] == 401
    assert latest["all_day_stress_valid_sample_count"] == 401
    assert latest["all_day_stress_cadence_seconds"] == 180.0
    assert latest["all_day_stress_density_ratio"] == 1.0
    assert latest["all_day_stress_start_lag_minutes"] == 0.0
    assert latest["all_day_stress_tail_lag_minutes"] == 0.0
    assert latest["all_day_stress_effective_cutoff_local"] == (
        "2026-05-06T20:00:00+08:00"
    )


def test_wellness_accepts_exact_next_midnight_as_completed_day_cutoff():
    latest = _normalized_all_day_stress(
        rows=_dense_stress_rows("2026-05-06", 23, 57),
        cutoff_local="2026-05-07T00:00:00+08:00",
        as_of="2026-05-07T08:00:00+08:00",
    )

    assert "declared_cutoff_date_mismatch" not in latest[
        "all_day_stress_sufficiency_issues"
    ]
    assert latest["all_day_stress_tail_lag_minutes"] == 3.0
    assert latest["all_day_stress_low_positive_reward_eligible"] is True


def test_wellness_normalizes_spo2_respiration_series_provenance_and_sentinels():
    day = "2026-08-04"
    kl = timezone(timedelta(hours=8))
    start = datetime.fromisoformat(day).replace(tzinfo=kl)
    respiration_rows = [
        [
            int((start + timedelta(minutes=2 * index)).timestamp() * 1000),
            -1 if index == 10 else -2 if index == 11 else 14.0,
        ]
        for index in range(1, 151)
    ]
    hourly_stamps = [
        int((start + timedelta(hours=index)).timestamp() * 1000)
        for index in range(5)
    ]
    row = normalize_wellness_payload(
        {
            "date": day,
            "payloads": [
                {
                    "label": "get_stats",
                    "ok": True,
                    "status": "success",
                    "data": {
                        "calendarDate": day,
                        "averageMonitoringEnvironmentAltitude": 1862,
                    },
                },
                {
                    "label": "get_spo2_data",
                    "ok": True,
                    "status": "success",
                    "attempted_at": f"{day}T20:00:00+08:00",
                    "last_success_at": f"{day}T19:00:00+08:00",
                    "latest_attempt": {
                        "status": "failed",
                        "attempted_at": f"{day}T20:00:00+08:00",
                    },
                    "retention_policy": "preserve_last_nonempty_success",
                    "data": {
                        "calendarDate": day,
                        "endTimestampLocal": f"{day}T05:00:00+08:00",
                        "averageSpO2": 92,
                        "avgSleepSpO2": 91,
                        "lowestSpO2": 84,
                        "latestSpO2": 95,
                        "lastSevenDaysAvgSpO2": 93,
                        "spO2HourlyAverages": [
                            [stamp, value]
                            for stamp, value in zip(
                                hourly_stamps,
                                (90, 91, 92, 95, 97),
                            )
                        ],
                    },
                },
                {
                    "label": "get_respiration_data",
                    "ok": True,
                    "status": "success",
                    "attempted_at": f"{day}T05:01:00+08:00",
                    "data": {
                        "calendarDate": day,
                        "endTimestampLocal": f"{day}T05:00:00+08:00",
                        "avgWakingRespirationValue": 14,
                        "avgSleepRespirationValue": 13,
                        "lowestRespirationValue": 9,
                        "highestRespirationValue": 18,
                        "respirationValuesArray": respiration_rows,
                        "respirationAveragesValuesArray": [
                            [stamp, 14.0, 15.0, 12.0] for stamp in hourly_stamps
                        ],
                    },
                },
            ],
        },
        as_of=f"{day}T05:05:00+08:00",
    )

    assert row["monitoring_altitude_m"] == 1862
    assert row["avg_spo2"] == 92
    assert row["sleep_spo2"] == 91
    assert row["spo2_lowest_pct"] == 84
    assert row["spo2_hourly_highest_pct"] == 97
    assert row["spo2_hourly_aggregate_count"] == 5
    assert row["spo2_data_retained_after_degraded_attempt"] is True
    assert row["spo2_latest_attempt_status"] == "failed"
    assert "does not establish activity-time" in row[
        "spo2_in_activity_interpretation"
    ]
    assert row["avg_respiration"] == 14
    assert row["respiration_two_min_reported_count"] == 150
    assert row["respiration_two_min_valid_count"] == 148
    assert row["respiration_two_min_sentinel_count"] == 2
    assert row["respiration_two_min_activity_sentinel_count"] == 1
    assert row["respiration_two_min_expected_count_through_cutoff"] == 150
    assert row["respiration_two_min_series_coverage_ratio"] == 1.0
    assert row["respiration_two_min_valid_measurement_ratio"] == 0.987
    assert "no in-activity value is imputed" in row[
        "respiration_exercise_aligned_unavailable_interpretation"
    ]


def test_wrong_date_oxygenation_endpoints_do_not_leak_values_into_target_day():
    row = normalize_wellness_payload(
        {
            "date": "2026-08-04",
            "payloads": [
                {
                    "label": "get_spo2_data",
                    "ok": True,
                    "status": "success",
                    "data": {
                        "calendarDate": "2026-08-05",
                        "averageSpO2": 99,
                        "spO2HourlyAverages": [[1785859200000, 99]],
                    },
                },
                {
                    "label": "get_respiration_data",
                    "ok": True,
                    "status": "success",
                    "data": {
                        "calendarDate": "2026-08-05",
                        "avgWakingRespirationValue": 10,
                        "respirationValuesArray": [[1785859320000, 10]],
                    },
                },
            ],
        }
    )

    assert row["spo2_response_date_matches_target"] is False
    assert row["spo2_daily_average_pct"] is None
    assert row["respiration_response_date_matches_target"] is False
    assert row["respiration_waking_average_brpm"] is None


def test_wellness_failed_status_cannot_gain_low_stress_credit_even_when_ok_true():
    latest = _normalized_all_day_stress(status="failed", ok=True, average_stress=45)

    assert latest["avg_stress"] == 45
    assert latest["all_day_stress_low_positive_reward_eligible"] is False
    assert "endpoint_not_success" in latest["all_day_stress_sufficiency_issues"]

    noncanonical_ok = _normalized_all_day_stress(status="ok", ok=True)
    assert noncanonical_ok["all_day_stress_low_positive_reward_eligible"] is False
    assert "endpoint_not_success" in noncanonical_ok[
        "all_day_stress_sufficiency_issues"
    ]


def test_wellness_redacts_arbitrary_all_day_endpoint_provenance_scalars():
    secret_path = r"C:\Users\private\garmin\profile-SECRET_PROFILE_78413.json"
    secret_url = "https://connector.invalid/user/SECRET_PROFILE_78413"
    snapshot = {
        "date": "2026-05-06",
        "payloads": [
            {
                "label": "get_stats",
                "ok": True,
                "data": {
                    "calendarDate": "2026-05-06",
                    "averageStressLevel": 45,
                },
            },
            {
                "label": "get_all_day_stress",
                "ok": True,
                "status": secret_path,
                "last_attempt_status": secret_url,
                "last_success_at": secret_path,
                "latest_attempt": {
                    "status": "SECRET_PROFILE_78413",
                    "profile": "SECRET_PROFILE_78413",
                    "path": secret_path,
                    "url": secret_url,
                },
                "data": {
                    "calendarDate": "2026-05-06",
                    "endTimestampLocal": "2026-05-06T20:00:00+08:00",
                    "userProfilePK": "SECRET_PROFILE_78413",
                    "stressValuesArray": _dense_stress_rows("2026-05-06"),
                },
            },
        ],
    }

    latest = normalize_wellness_payload(
        snapshot,
        as_of="2026-05-06T20:30:00+08:00",
    )
    serialized = json.dumps(latest, sort_keys=True)

    assert latest["avg_stress"] == 45
    assert latest["all_day_stress_endpoint_status"] == "unknown"
    assert latest["all_day_stress_latest_attempt_status"] == "unknown"
    assert latest["all_day_stress_last_success_at"] is None
    assert latest["all_day_stress_low_positive_reward_eligible"] is False
    assert "SECRET_PROFILE_78413" not in serialized
    assert "connector.invalid" not in serialized
    assert "C:\\\\Users" not in serialized


def test_wellness_all_day_credit_requires_outer_and_nested_target_date_agreement():
    missing_nested = _normalized_all_day_stress(nested_date=None)
    wrong_nested = _normalized_all_day_stress(nested_date="2026-05-07")
    wrong_outer = _normalized_all_day_stress(outer_date="2026-05-07")

    assert missing_nested["all_day_stress_low_positive_reward_eligible"] is False
    assert "nested_target_date_missing_or_invalid" in (
        missing_nested["all_day_stress_sufficiency_issues"]
    )
    assert wrong_nested["all_day_stress_low_positive_reward_eligible"] is False
    assert "nested_target_date_mismatch" in wrong_nested[
        "all_day_stress_sufficiency_issues"
    ]
    assert wrong_outer["all_day_stress_low_positive_reward_eligible"] is False
    assert "nested_target_date_mismatch" in wrong_outer[
        "all_day_stress_sufficiency_issues"
    ]

    malformed_outer = _normalized_all_day_stress(outer_date="not-a-date")
    malformed_nested = _normalized_all_day_stress(nested_date="not-a-date")
    assert malformed_outer["all_day_stress_low_positive_reward_eligible"] is False
    assert "outer_target_date_missing_or_invalid" in malformed_outer[
        "all_day_stress_sufficiency_issues"
    ]
    assert malformed_nested["all_day_stress_low_positive_reward_eligible"] is False
    assert "nested_target_date_missing_or_invalid" in malformed_nested[
        "all_day_stress_sufficiency_issues"
    ]


def test_wellness_all_day_credit_rejects_malformed_or_sparse_series():
    malformed = _normalized_all_day_stress(rows=[["bad"], {"timestamp": 1}, None])
    one_row = _normalized_all_day_stress(rows=_dense_stress_rows("2026-05-06")[:1])
    two_rows = _normalized_all_day_stress(rows=_dense_stress_rows("2026-05-06")[:2])

    assert malformed["all_day_stress_low_positive_reward_eligible"] is False
    assert malformed["all_day_stress_malformed_sample_count"] == 3
    assert malformed["all_day_stress_target_date_sample_count"] == 0
    assert one_row["all_day_stress_low_positive_reward_eligible"] is False
    assert two_rows["all_day_stress_low_positive_reward_eligible"] is False
    assert "sample_count_too_low" in one_row["all_day_stress_sufficiency_issues"]
    assert "sample_count_too_low" in two_rows["all_day_stress_sufficiency_issues"]


def test_wellness_all_day_density_uses_fixed_three_minute_valid_slots():
    kl = timezone(timedelta(hours=8))
    start = datetime(2026, 5, 6, tzinfo=kl)
    five_minute_rows = [
        [int((start + timedelta(minutes=minute)).timestamp() * 1000), 18]
        for minute in range(0, 20 * 60 + 1, 5)
    ]
    alternating_sentinel_rows = _dense_stress_rows("2026-05-06")
    for index, row in enumerate(alternating_sentinel_rows):
        if index % 2:
            row[1] = -1

    thinned = _normalized_all_day_stress(rows=five_minute_rows)
    sentinel_thinned = _normalized_all_day_stress(rows=alternating_sentinel_rows)

    assert thinned["all_day_stress_cadence_seconds"] == 300.0
    assert thinned["all_day_stress_density_ratio"] == 0.601
    assert thinned["all_day_stress_low_positive_reward_eligible"] is False
    assert "series_density_insufficient" in thinned[
        "all_day_stress_sufficiency_issues"
    ]
    assert sentinel_thinned["all_day_stress_parsed_density_ratio"] == 1.0
    assert sentinel_thinned["all_day_stress_density_ratio"] == 0.501
    assert sentinel_thinned["all_day_stress_low_positive_reward_eligible"] is False


def test_wellness_discards_non_target_and_post_cutoff_samples_and_withholds_credit():
    kl = timezone(timedelta(hours=8))

    def stamp(day: str, hour: int, minute: int) -> int:
        return int(
            datetime.fromisoformat(f"{day}T{hour:02d}:{minute:02d}:00")
            .replace(tzinfo=kl)
            .timestamp()
            * 1000
        )

    rows = _dense_stress_rows("2026-05-06")
    rows.extend(
        [
            [stamp("2026-05-05", 23, 57), 8],
            [stamp("2026-05-06", 20, 3), 8],
            [stamp("2026-05-07", 0, 0), 8],
        ]
    )
    latest = _normalized_all_day_stress(rows=rows)

    assert latest["all_day_stress_low_positive_reward_eligible"] is False
    assert latest["all_day_stress_other_date_sample_count"] == 2
    assert latest["all_day_stress_after_cutoff_sample_count"] == 1
    assert latest["all_day_stress_target_date_sample_count"] == 401
    assert latest["all_day_stress_end_time_local"] == "2026-05-06T20:00:00+08:00"
    assert "samples_outside_target_date" in latest[
        "all_day_stress_sufficiency_issues"
    ]
    assert "samples_after_effective_cutoff" in latest[
        "all_day_stress_sufficiency_issues"
    ]


def test_wellness_all_day_credit_requires_tail_to_reach_declared_cutoff():
    latest = _normalized_all_day_stress(rows=_dense_stress_rows("2026-05-06", 16))

    assert latest["all_day_stress_low_positive_reward_eligible"] is False
    assert latest["all_day_stress_tail_lag_minutes"] == 240.0
    assert "series_tail_does_not_reach_cutoff" in latest[
        "all_day_stress_sufficiency_issues"
    ]


def test_wellness_boundary_tolerance_is_not_looser_than_exact_artifact():
    dense = _dense_stress_rows("2026-05-06")
    late_start = _normalized_all_day_stress(rows=dense[2:])
    early_tail = _normalized_all_day_stress(rows=dense[:-2])

    assert late_start["all_day_stress_start_lag_minutes"] == 6.0
    assert late_start["all_day_stress_low_positive_reward_eligible"] is False
    assert "series_does_not_start_near_midnight" in late_start[
        "all_day_stress_sufficiency_issues"
    ]
    assert early_tail["all_day_stress_tail_lag_minutes"] == 6.0
    assert early_tail["all_day_stress_low_positive_reward_eligible"] is False
    assert "series_tail_does_not_reach_cutoff" in early_tail[
        "all_day_stress_sufficiency_issues"
    ]


def test_wellness_all_day_credit_requires_effective_cutoff_at_or_after_18_local():
    latest = _normalized_all_day_stress(
        rows=_dense_stress_rows("2026-05-06", 17, 30),
        cutoff_local="2026-05-06T17:30:00+08:00",
    )

    assert latest["all_day_stress_low_positive_reward_eligible"] is False
    assert "effective_cutoff_before_18_local" in latest[
        "all_day_stress_sufficiency_issues"
    ]

    missing_source_cutoff = _normalized_all_day_stress(cutoff_local=None)
    assert missing_source_cutoff["all_day_stress_effective_cutoff_local"] is None
    assert missing_source_cutoff["all_day_stress_sample_cutoff_local"] == (
        "2026-05-06T20:30:00+08:00"
    )
    assert missing_source_cutoff[
        "all_day_stress_low_positive_reward_eligible"
    ] is False
    assert "declared_cutoff_unavailable" in missing_source_cutoff[
        "all_day_stress_sufficiency_issues"
    ]


def test_wellness_wall_as_of_clamps_a_later_declared_endpoint_cutoff():
    rows = _dense_stress_rows("2026-05-06")
    rows.append(_dense_stress_rows("2026-05-06", 20, 3)[-1])
    latest = _normalized_all_day_stress(
        rows=rows,
        cutoff_local="2026-05-06T21:00:00+08:00",
        as_of="2026-05-06T20:00:00+08:00",
    )

    assert latest["all_day_stress_effective_cutoff_local"] == (
        "2026-05-06T20:00:00+08:00"
    )
    assert latest["all_day_stress_after_cutoff_sample_count"] == 1
    assert latest["all_day_stress_low_positive_reward_eligible"] is False


def test_wellness_future_target_date_is_clipped_by_wall_as_of_and_cannot_qualify():
    latest = _normalized_all_day_stress(
        outer_date="2026-05-07",
        nested_date="2026-05-07",
        rows=_dense_stress_rows("2026-05-07"),
        cutoff_local="2026-05-07T20:00:00+08:00",
        as_of="2026-05-06T20:00:00+08:00",
    )

    assert latest["all_day_stress_effective_cutoff_local"] == (
        "2026-05-06T20:00:00+08:00"
    )
    assert latest["all_day_stress_target_date_sample_count"] == 0
    assert latest["all_day_stress_after_cutoff_sample_count"] == 401
    assert latest["all_day_stress_low_positive_reward_eligible"] is False


def test_activity_load_uses_activity_training_load_and_excludes_motorsport(tmp_path):
    write_json(
        tmp_path / "activities" / "ride.json",
        {
            "activityId": 1,
            "activityName": "Base",
            "activityType": {"typeKey": "indoor_cycling"},
            "startTimeLocal": "2026-04-30 08:00:00",
            "duration": 3600,
            "distance": 20000,
            "activityTrainingLoad": 75.5,
            "averageHR": 130,
        },
    )
    write_json(
        tmp_path / "activities" / "car.json",
        {
            "activityId": 2,
            "activityName": "Race",
            "activityType": {"typeKey": "auto_racing"},
            "startTimeLocal": "2026-04-30 10:00:00",
            "duration": 3600,
            "activityTrainingLoad": 999,
            "averageHR": 130,
        },
    )

    profile = build_activity_profile(tmp_path, "2026-04-30")
    rollups = build_modality_load_rollups(tmp_path, "2026-04-30")

    assert profile["categories"]["bike_indoor"] == 1
    assert profile["categories"]["motorsport"] == 1
    assert rollups["windows"]["last_7_days"]["bike_indoor"]["training_load"] == 75.5
    assert rollups["windows"]["last_7_days"]["motorsport"]["excluded_sessions"] == 1


def test_activity_summary_index_surfaces_redacted_local_timing_without_gmt_guessing(tmp_path):
    write_json(
        tmp_path / "activities" / "explicit_end.json",
        {
            "activityId": 11,
            "activityName": "Private trail name",
            "activityType": {"typeKey": "mountain_biking"},
            "startTimeLocal": "2026-04-30 16:59:59",
            "endTimeLocal": "2026-04-30 18:05:00",
            "duration": 3600,
        },
    )
    write_json(
        tmp_path / "activities" / "derived_end.json",
        {
            "activityId": 12,
            "activityName": "Late session",
            "activityType": {"typeKey": "indoor_cycling"},
            "startTimeLocal": "2026-04-30 23:50:00",
            "elapsedDuration": 1800,
            "duration": 1200,
        },
    )
    write_json(
        tmp_path / "activities" / "gmt_only.json",
        {
            "activityId": 13,
            "activityName": "No local timestamp",
            "activityType": {"typeKey": "running"},
            "startTimeGMT": "2026-04-30 03:00:00",
            "duration": 1200,
        },
    )

    rows = build_activity_summary_index(tmp_path, "2026-04-30")
    by_category = {row["category"]: row for row in rows}

    mtb = by_category["mtb"]
    assert mtb["date"] == "2026-04-30"
    assert mtb["start_time_local"] == "2026-04-30T16:59:59"
    assert mtb["end_time_local"] == "2026-04-30T18:05:00"
    assert mtb["start_time_bucket"] == "afternoon"

    indoor = by_category["bike_indoor"]
    assert indoor["start_time_local"] == "2026-04-30T23:50:00"
    assert indoor["end_time_local"] == "2026-05-01T00:20:00"
    assert indoor["start_time_bucket"] == "late_evening"

    run = by_category["run"]
    assert run["date"] == "2026-04-30"
    assert run["start_time_local"] is None
    assert run["end_time_local"] is None
    assert run["start_time_bucket"] is None

    for row in rows:
        assert "name" not in row
        assert "source_file" not in row
        assert "activityId" not in row


def test_training_status_normalizes_acwr_vo2_and_load_focus(tmp_path):
    write_json(
        tmp_path / "snapshots" / "garmin_training_status_2026-04-30.json",
        {
            "date": "2026-04-30",
            "payload": {
                "ok": True,
                "data": {
                    "mostRecentTrainingStatus": {
                        "latestTrainingStatusData": {
                            "dev": {
                                "primaryTrainingDevice": True,
                                "trainingStatusFeedbackPhrase": "MAINTAINING_2",
                                "acuteTrainingLoadDTO": {
                                    "acwrStatus": "OPTIMAL",
                                    "dailyAcuteChronicWorkloadRatio": 0.8,
                                    "dailyTrainingLoadAcute": 357,
                                    "dailyTrainingLoadChronic": 402,
                                },
                            }
                        }
                    },
                    "mostRecentTrainingLoadBalance": {
                        "metricsTrainingLoadBalanceDTOMap": {
                            "dev": {
                                "primaryTrainingDevice": True,
                                "monthlyLoadAnaerobic": 98,
                                "monthlyLoadAnaerobicTargetMin": 133,
                                "trainingBalanceFeedbackPhrase": "ANAEROBIC_SHORTAGE",
                            }
                        }
                    },
                    "mostRecentVO2Max": {"cycling": {"vo2MaxValue": 45}},
                },
            },
        },
    )

    status = build_training_status_current(tmp_path, "2026-04-30")

    assert status["acute_chronic"]["ratio"] == 0.8
    assert status["vo2max"]["cycling_value"] == 45.0
    assert status["flags"][0]["type"] == "load_focus_gap"


def test_training_status_keeps_altitude_acclimation_separate_from_percentage(tmp_path):
    write_json(
        tmp_path / "snapshots" / "garmin_training_status_2026-08-06.json",
        {
            "date": "2026-08-06",
            "payload": {
                "ok": True,
                "data": {
                    "mostRecentVO2Max": {
                        "heatAltitudeAcclimation": {
                            "altitudeAcclimation": 1000,
                            "acclimationPercentage": 0,
                            "previousAltitudeAcclimation": 900,
                            "previousAcclimationPercentage": 9,
                            "currentAltitude": 69,
                            "previousAltitude": 70,
                            "altitudeTrend": "ACCLIMATIZING",
                            "altitudeAcclimationDate": "2026-08-05",
                            "altitudeAcclimationLocalTimestamp": "2026-08-06T00:05:59.0",
                        }
                    }
                },
            },
        },
    )

    status = build_training_status_current(tmp_path, "2026-08-06")
    altitude = status["acclimation"]

    assert "altitude_pct" not in altitude
    assert altitude["altitude_acclimation"] == 1000.0
    assert altitude["acclimation_percentage"] == 0.0
    assert altitude["previous_altitude_acclimation"] == 900.0
    assert altitude["previous_acclimation_percentage"] == 9.0
    assert altitude["current_altitude"] == 69.0
    assert altitude["previous_altitude"] == 70.0
    assert altitude["altitude_trend"] == "ACCLIMATIZING"
    assert altitude["altitude_date"] == "2026-08-05"
    assert altitude["altitude_local_timestamp"] == "2026-08-06T00:05:59.0"
    assert altitude["units"]["altitude_acclimation"] == (
        "garmin_native_unit_not_declared"
    )
    assert altitude["units"]["acclimation_percentage"] == "percent"
    assert "not converted to or labeled as percent" in altitude["provenance"][
        "interpretation_guardrail"
    ]
