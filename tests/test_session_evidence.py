from coach_sync.coach_packet import build_coach_packet
from coach_sync.context import load_context
from coach_sync.io import read_json, write_json
from coach_sync.session_evidence import (
    _select_session_duration_seconds,
    build_latest_session_evidence,
)
from coach_sync.state import build_current_state


def _write_activity(root, activity_id, start, activity_type="mountain_biking", **fields):
    payload = {
        "activityId": activity_id,
        "activityName": fields.pop("activityName", "Test session"),
        "activityType": {"typeKey": activity_type},
        "startTimeLocal": start,
        "duration": fields.pop("duration", 5400),
        "elapsedDuration": fields.pop("elapsedDuration", 5400),
        "movingDuration": fields.pop("movingDuration", 4200),
        "activityTrainingLoad": fields.pop("activityTrainingLoad", 150),
        **fields,
    }
    write_json(root / "activities" / f"garmin_{activity_id}.json", payload)


def _write_metadata(root, activity_id, day):
    write_json(
        root / "snapshots" / "activity_gear_index.json",
        {
            "activities": [
                {
                    "activity_id": str(activity_id),
                    "date": day,
                    "gear_fetch_ok": True,
                    "gear": [{"label": "Stumpjumper Expert"}],
                }
            ]
        },
    )
    write_json(
        root / "snapshots" / "activity_device_index.json",
        {
            "activities": [
                {
                    "activity_id": str(activity_id),
                    "date": day,
                    "device_fetch_ok": True,
                    "recording_device": {"manufacturer": "GARMIN"},
                    "sensors": [{"sensor_type": "HEART_RATE"}],
                    "external_hr_sensor": True,
                    "external_hr_battery_statuses": ["GOOD"],
                }
            ]
        },
    )
    write_json(
        root / "snapshots" / "activity_self_evaluation_index.json",
        {
            "activities": [
                {
                    "activity_id": str(activity_id),
                    "date": day,
                    "detail_fetch_ok": True,
                    "has_self_evaluation": True,
                    "feel_score": 75,
                    "feel_label": "strong",
                    "rpe_score": 60,
                    "rpe_label": "hard_plus",
                    "rpe_out_of_10": 6,
                }
            ]
        },
    )


def _write_loop(root, activity_id, day):
    write_json(
        root / "snapshots" / f"activity_loop_load_{day}_{activity_id}.json",
        {
            "artifact_type": "activity_loop_load",
            "activity_id": str(activity_id),
            "date": day,
            "generated_at": f"{day}T18:00:00+08:00",
            "official_activity_training_load": 150,
            "weather": {"temp": 91, "apparentTemp": 99, "relativeHumidity": 53},
            "method": {"primary_estimate": "primary_continuous_hr"},
            "sanity_checks": {"primary_sum_to_official_ratio": 1.0},
            "loops": [
                {
                    "loop": 1,
                    "label": "First descent",
                    "lap_kinds": ["descent"],
                    "elapsed_min": 5,
                    "moving_min": 4.8,
                    "stop_min": 0.2,
                    "average_hr_est": 155,
                    "load_per_elapsed_hour": 180,
                    "estimated_load": {"primary_continuous_hr": 72},
                },
                {
                    "loop": 2,
                    "label": "Final descent",
                    "lap_kinds": ["descent"],
                    "elapsed_min": 5.3,
                    "moving_min": 5.0,
                    "stop_min": 0.3,
                    "average_hr_est": 160,
                    "load_per_elapsed_hour": 188,
                    "estimated_load": {"primary_continuous_hr": 78},
                },
            ],
        },
    )


def test_latest_mtb_session_surfaces_bounded_raw_metadata_weather_and_loop_evidence(tmp_path):
    activity_id = 101
    day = "2026-07-09"
    _write_activity(
        tmp_path,
        activity_id,
        f"{day} 12:00:00",
        activityName="Kiara MTB",
        elevationGain=380,
        elevationLoss=360,
        distance=10500,
        minTemperature=31,
        maxTemperature=36,
        waterEstimated=756,
        activityTrainingLoad=150,
        aerobicTrainingEffect=3.7,
        anaerobicTrainingEffect=2.8,
        trainingEffectLabel="TEMPO",
        trainingStressScore=62,
        averageBikingCadenceInRevPerMinute=54,
        avgPower=85,
        normPower=137,
        maxPower=1352,
        intensityFactor=0.642,
        maxFtp=211,
        max20MinPower=103.8,
        maxAvgPower_1=1352,
        maxAvgPower_5=702,
        maxAvgPower_60=232,
        maxAvgPower_300=165,
        maxAvgPower_1200=104,
        avgFlow=2.59,
        grit=43.44,
    )
    _write_metadata(tmp_path, activity_id, day)
    _write_loop(tmp_path, activity_id, day)
    write_json(
        tmp_path / "snapshots" / f"activity_detail_{activity_id}.json",
        {
            "activity_id": str(activity_id),
            "calls": {
                "weather": {
                    "ok": True,
                    "data": {
                        "temp": 91,
                        "apparentTemp": 99,
                        "relativeHumidity": 53,
                        "issueDate": "2026-07-09T05:00:00Z",
                    },
                }
            },
        },
    )
    write_json(
        tmp_path / "activities" / "details" / f"garmin_{activity_id}_detail.json",
        {
            "artifact_type": "raw_garmin_key_activity_detail",
            "activity_id": str(activity_id),
            "calls": {
                "weather": {
                    "ok": True,
                    "data": {
                        "temp": 91,
                        "apparentTemp": 99,
                        "relativeHumidity": 54,
                        "issueDate": "2026-07-09T05:05:00Z",
                    },
                }
            },
        },
    )

    evidence = build_latest_session_evidence(tmp_path, "2026-07-10")

    assert evidence["activity"]["activity_id"] == "101"
    assert evidence["timing"] == {
        "elapsed_min": 90.0,
        "moving_min": 70.0,
        "stopped_min": 20.0,
        "stopped_derivation": "elapsed_minus_moving",
        "garmin_reported": {
            "elapsed_min": 90.0,
            "moving_min": 70.0,
            "implied_stopped_min": 20.0,
            "source_fields": {
                "elapsed": "elapsedDuration",
                "moving": "movingDuration",
            },
        },
        "plausibility": {"status": "accepted_as_reported"},
    }
    assert evidence["terrain"]["ascent_m"] == 380.0
    assert evidence["environment"]["device_temperature"]["unit"] == "celsius"
    assert evidence["environment"]["weather"]["temperature"]["unit"] == "unknown"
    assert evidence["environment"]["weather"]["relative_humidity"]["value"] == 54.0
    assert evidence["environment"]["garmin_estimated_water_loss"]["value_ml"] == 756.0
    assert evidence["power"]["selected_best_average_w"]["20min"] == 104.0
    assert evidence["power"]["garmin_detected_ftp"]["value_w"] == 211.0
    assert evidence["power"]["garmin_detected_ftp"]["source_field"] == "maxFtp"
    assert "do not independently establish FTP" in evidence["power"]["interpretation_guardrail"]
    assert evidence["technical_context"]["flow"] == 2.59
    assert evidence["gear"]["labels"] == ["Stumpjumper Expert"]
    assert evidence["device"]["hr_confidence"] == "external_hr"
    assert evidence["self_evaluation"]["rpe_out_of_10"] == 6
    assert evidence["loop_analysis"]["manual_loop_groups"] == 2
    assert evidence["loop_analysis"]["first_vs_final"][0]["change_final_minus_first"]["estimated_load"] == 6.0
    assert evidence["loop_analysis"]["source"].endswith(
        "activity_loop_load_2026-07-09_101.json"
    )
    assert evidence["provenance"]["activity_detail"] == (
        "activities/details/garmin_101_detail.json"
    )
    assert evidence["confidence"]["status"] == "partial"
    assert any(
        item["type"] == "latest_session_weather_unit_unverified"
        for item in evidence["cautions"]
    )


def test_latest_session_keeps_recent_loop_analysis_distinct_when_a_later_session_exists(tmp_path):
    _write_activity(tmp_path, 201, "2026-07-09 12:00:00")
    _write_loop(tmp_path, 201, "2026-07-09")
    _write_activity(
        tmp_path,
        202,
        "2026-07-09 17:00:00",
        activity_type="indoor_cycling",
        activityName="Trainer calibration",
        duration=1800,
        elapsedDuration=1800,
        movingDuration=1740,
    )

    evidence = build_latest_session_evidence(tmp_path, "2026-07-10")

    assert evidence["activity"]["activity_id"] == "202"
    assert evidence["loop_analysis"] is None
    assert evidence["recent_loop_analysis"]["activity_id"] == "201"
    assert evidence["recent_loop_analysis"]["matches_latest_session"] is False
    assert evidence["technical_context"] is None


def test_latest_session_surfaces_generic_persisted_detail_trace_without_raw_samples(tmp_path):
    activity_id = 203
    day = "2026-07-26"
    _write_activity(
        tmp_path,
        activity_id,
        f"{day} 18:11:55",
        activity_type="indoor_cycling",
        activityName="Base",
        duration=3600,
        elapsedDuration=3600,
        movingDuration=3600,
        avgPower=125,
        normPower=125,
        averageHR=122,
    )
    write_json(
        tmp_path / "activities" / "details" / f"garmin_{activity_id}_detail.json",
        {
            "artifact_type": "raw_garmin_key_activity_detail",
            "activity_id": str(activity_id),
            "calls": {
                "details": {
                    "ok": True,
                    "status": "success",
                    "data": {
                        "metricDescriptors": [
                            {
                                "metricsIndex": 0,
                                "key": "sumElapsedDuration",
                                "unit": {"key": "second", "factor": 1000.0},
                            },
                            {
                                "metricsIndex": 1,
                                "key": "directHeartRate",
                                "unit": {"key": "bpm", "factor": 1.0},
                            },
                            {
                                "metricsIndex": 2,
                                "key": "directPower",
                                "unit": {"key": "watt", "factor": 1.0},
                            },
                        ],
                        "activityDetailMetrics": [
                            {
                                "metrics": [0.0, 80.0, 987654321.0],
                                "private_marker": "RAW_TRACE_SENTINEL",
                            },
                            {"metrics": [2.0, 81.0, 116.0]},
                        ],
                        "geoPolylineDTO": {
                            "private_marker": "UNRELATED_NESTED_SENTINEL"
                        },
                    },
                }
            },
        },
    )

    evidence = build_latest_session_evidence(tmp_path, day)

    assert evidence["activity"]["category"] == "bike_indoor"
    assert evidence["provenance"]["activity_detail"] == (
        "activities/details/garmin_203_detail.json"
    )
    assert evidence["detail_trace"] == {
        "status": "available",
        "sample_count": 2,
        "metric_descriptors": [
            {"key": "sumElapsedDuration", "unit": "second"},
            {"key": "directHeartRate", "unit": "bpm"},
            {"key": "directPower", "unit": "watt"},
        ],
        "metric_descriptors_truncated": False,
        "source": "activities/details/garmin_203_detail.json",
        "interpretation_guardrail": (
            "This block reports persisted trace availability only. Raw time-series values are "
            "deliberately excluded, and no heart-rate drift or interval physiology is inferred."
        ),
    }
    assert "activityDetailMetrics" not in str(evidence)
    assert "heart_rate_drift" not in str(evidence)
    assert "987654321" not in str(evidence)
    assert "RAW_TRACE_SENTINEL" not in str(evidence)
    assert "UNRELATED_NESTED_SENTINEL" not in str(evidence)


def test_latest_session_detail_trace_bounds_metric_descriptors(tmp_path):
    activity_id = 204
    day = "2026-07-26"
    _write_activity(
        tmp_path,
        activity_id,
        f"{day} 18:11:55",
        activity_type="indoor_cycling",
    )
    write_json(
        tmp_path / "activities" / "details" / f"garmin_{activity_id}_detail.json",
        {
            "activity_id": str(activity_id),
            "calls": {
                "details": {
                    "ok": True,
                    "status": "success",
                    "data": {
                        "metricDescriptors": [
                            {"metricsIndex": index, "unit": {"key": "dimensionless"}}
                            for index in range(12)
                        ]
                        + [
                            {
                                "metricsIndex": index + 12,
                                "key": f"metric_{index}",
                                "unit": {"key": "dimensionless"},
                            }
                            for index in range(40)
                        ],
                        "activityDetailMetrics": [{"metrics": list(range(40))}],
                    },
                }
            },
        },
    )

    evidence = build_latest_session_evidence(tmp_path, day)
    trace = evidence["detail_trace"]

    assert trace["status"] == "available"
    assert trace["sample_count"] == 1
    assert len(trace["metric_descriptors"]) == 32
    assert trace["metric_descriptors_truncated"] is True
    assert trace["metric_descriptors"][-1] == {
        "key": "metric_31",
        "unit": "dimensionless",
    }


def test_latest_session_surfaces_bounded_named_performance_condition_and_speed_provenance(tmp_path):
    activity_id = 205
    day = "2026-08-31"
    _write_activity(
        tmp_path,
        activity_id,
        f"{day} 10:53:32",
        activityName="Kuala Lumpur Mountain Biking",
        avgPower=96,
    )
    _write_metadata(tmp_path, activity_id, day)
    device_index = tmp_path / "snapshots" / "activity_device_index.json"
    device = read_json(device_index, {})
    device["activities"][0]["sensors"].append(
        {
            "sensor_type": "BIKE_SPEED",
            "source_type": "ANTPLUS",
            "battery_status": "GOOD",
        }
    )
    write_json(device_index, device)
    write_json(
        tmp_path / "activities" / "details" / f"garmin_{activity_id}_detail.json",
        {
            "activity_id": str(activity_id),
            "calls": {
                "details": {
                    "ok": True,
                    "status": "success",
                    "data": {
                        "metricDescriptors": [
                            {"metricsIndex": 0, "key": "directTimestamp", "unit": {"key": "gmt"}},
                            {"metricsIndex": 1, "key": "sumElapsedDuration", "unit": {"key": "second"}},
                            {"metricsIndex": 2, "key": "directPerformanceCondition", "unit": {"key": "dimensionless"}},
                            {"metricsIndex": 3, "key": "directPower", "unit": {"key": "watt"}},
                        ],
                        "activityDetailMetrics": [
                            {"metrics": [1_000_000, 0, None, 100]},
                            {"metrics": [1_429_000, 429, 0, 177]},
                            {"metrics": [2_624_000, 1624, -1, 106]},
                            {"metrics": [3_084_000, 2084, -2, 190]},
                            {"metrics": [3_770_000, 2770, -3, 106]},
                            {"metrics": [4_503_000, 3503, -3, 19]},
                        ],
                    },
                }
            },
        },
    )

    evidence = build_latest_session_evidence(tmp_path, day)
    pc = evidence["performance_condition"]

    assert pc["status"] == "available"
    assert pc["context_scope"] == "matched_stumpjumper_fitness_context"
    assert pc["held_trace_observation_count"] == 5
    assert pc["state_point_count"] == 4
    assert pc["change_count"] == 3
    assert pc["timing_basis"] == "sumElapsedDuration_second"
    assert pc["power_context_basis"] == ["garmin_activity_summary.avgPower"]
    assert pc["first_value"] == 0
    assert pc["first_elapsed_min"] == 7.15
    assert pc["final_value"] == -3
    assert pc["change_final_minus_first"] == -3
    assert [item["value"] for item in pc["state_points"]] == [0, -1, -2, -3]
    assert "activityDetailMetrics" not in str(pc)
    assert evidence["device"]["speed_measurement"]["external_speed_sensor"] is True
    assert evidence["device"]["speed_measurement"]["ontology_entity"] == (
        "measurement_provenance"
    )


def test_performance_condition_is_not_applied_to_non_cycling_activity(tmp_path):
    activity_id = 206
    day = "2026-08-31"
    _write_activity(
        tmp_path,
        activity_id,
        f"{day} 08:00:00",
        activity_type="hiking",
        avgPower=100,
    )

    evidence = build_latest_session_evidence(tmp_path, day)

    assert evidence["performance_condition"]["status"] == (
        "not_applicable_non_cycling"
    )


def test_performance_condition_accepts_valid_epoch_seconds_without_elapsed_metric(tmp_path):
    activity_id = 2061
    day = "2026-08-31"
    _write_activity(
        tmp_path,
        activity_id,
        f"{day} 08:00:00",
        elapsedDuration=-1,
        duration=5400,
        beginTimestamp=1_788_000_000_000,
        normPower=140,
    )
    _write_metadata(tmp_path, activity_id, day)
    write_json(
        tmp_path / "activities" / "details" / f"garmin_{activity_id}_detail.json",
        {
            "activity_id": str(activity_id),
            "calls": {
                "details": {
                    "ok": True,
                    "status": "success",
                    "data": {
                        "metricDescriptors": [
                            {
                                "metricsIndex": 0,
                                "key": "directTimestamp",
                                "unit": {"key": "gmt"},
                            },
                            {
                                "metricsIndex": 1,
                                "key": "directPerformanceCondition",
                                "unit": {"key": "dimensionless"},
                            },
                        ],
                        "activityDetailMetrics": [
                            {"metrics": [1_788_000_000, 0]},
                            {"metrics": [1_788_000_060, -1]},
                        ],
                    },
                }
            },
        },
    )

    pc = build_latest_session_evidence(tmp_path, day)["performance_condition"]

    assert pc["status"] == "available"
    assert pc["timing_basis"] == "directTimestamp_gmt_epoch_seconds"
    assert pc["last_elapsed_min"] == 1.0
    assert pc["change_count"] == 1


def test_performance_condition_timestamp_elapsed_requires_activity_begin_alignment(tmp_path):
    activity_id = 20609
    day = "2026-08-31"
    _write_activity(
        tmp_path,
        activity_id,
        f"{day} 08:00:00",
        beginTimestamp=1_700_000_000_000,
        normPower=140,
    )
    _write_metadata(tmp_path, activity_id, day)
    write_json(
        tmp_path / "activities" / "details" / f"garmin_{activity_id}_detail.json",
        {
            "activity_id": str(activity_id),
            "calls": {
                "details": {
                    "ok": True,
                    "data": {
                        "metricDescriptors": [
                            {"metricsIndex": 0, "key": "directTimestamp", "unit": {"key": "gmt"}},
                            {"metricsIndex": 1, "key": "directPerformanceCondition", "unit": {"key": "dimensionless"}},
                        ],
                        "activityDetailMetrics": [
                            {"metrics": [1_788_000_000, 0]},
                            {"metrics": [1_788_000_060, -1]},
                        ],
                    },
                }
            },
        },
    )

    pc = build_latest_session_evidence(tmp_path, day)["performance_condition"]

    assert pc["timing_basis"] == "withheld_timestamp_not_aligned_to_activity_begin"
    assert pc["first_elapsed_min"] is None
    assert pc["last_elapsed_min"] is None


def test_performance_condition_rejects_non_monotonic_elapsed_source_sequence(tmp_path):
    activity_id = 206091
    day = "2026-08-31"
    _write_activity(tmp_path, activity_id, f"{day} 08:00:00", elapsedDuration=180, normPower=140)
    _write_metadata(tmp_path, activity_id, day)
    write_json(
        tmp_path / "activities" / "details" / f"garmin_{activity_id}_detail.json",
        {
            "activity_id": str(activity_id),
            "calls": {
                "details": {
                    "ok": True,
                    "data": {
                        "metricDescriptors": [
                            {"metricsIndex": 0, "key": "sumElapsedDuration", "unit": {"key": "second"}},
                            {"metricsIndex": 1, "key": "directPerformanceCondition", "unit": {"key": "dimensionless"}},
                        ],
                        "activityDetailMetrics": [
                            {"metrics": [0, 0]},
                            {"metrics": [120, -1]},
                            {"metrics": [60, -2]},
                        ],
                    },
                }
            },
        },
    )

    pc = build_latest_session_evidence(tmp_path, day)["performance_condition"]

    assert pc["timing_basis"] == "withheld_missing_or_non_monotonic_elapsed"
    assert [item["value"] for item in pc["state_points"]] == [0, -1, -2]
    assert all(item["elapsed_min"] is None for item in pc["state_points"])


def test_performance_condition_uses_robust_max_valid_session_duration(tmp_path):
    activity_id = 206092
    day = "2026-08-31"
    _write_activity(
        tmp_path,
        activity_id,
        f"{day} 08:00:00",
        elapsedDuration=1,
        duration=5400,
        beginTimestamp=1_788_000_000_000,
        normPower=140,
    )
    _write_metadata(tmp_path, activity_id, day)
    write_json(
        tmp_path / "activities" / "details" / f"garmin_{activity_id}_detail.json",
        {
            "activity_id": str(activity_id),
            "calls": {
                "details": {
                    "ok": True,
                    "data": {
                        "metricDescriptors": [
                            {"metricsIndex": 0, "key": "directTimestamp", "unit": {"key": "gmt"}},
                            {"metricsIndex": 1, "key": "directPerformanceCondition", "unit": {"key": "dimensionless"}},
                        ],
                        "activityDetailMetrics": [
                            {"metrics": [1_788_000_000, 0]},
                            {"metrics": [1_788_000_060, -1]},
                        ],
                    },
                }
            },
        },
    )

    evidence = build_latest_session_evidence(tmp_path, day)
    pc = evidence["performance_condition"]

    assert pc["timing_basis"] == "directTimestamp_gmt_epoch_seconds"
    assert pc["last_elapsed_min"] == 1.0
    assert evidence["timing"]["garmin_reported"]["source_fields"]["elapsed"] == (
        "duration"
    )
    assert _select_session_duration_seconds(
        {"elapsedDuration": 10**12, "duration": 5400}
    ) == 5400


def test_performance_condition_keeps_missing_timestamp_unit_strict(tmp_path):
    activity_id = 20610
    day = "2026-08-31"
    _write_activity(tmp_path, activity_id, f"{day} 08:00:00", normPower=140)
    _write_metadata(tmp_path, activity_id, day)
    write_json(
        tmp_path / "activities" / "details" / f"garmin_{activity_id}_detail.json",
        {
            "activity_id": str(activity_id),
            "calls": {
                "details": {
                    "ok": True,
                    "status": "success",
                    "data": {
                        "metricDescriptors": [
                            {
                                "metricsIndex": 0,
                                "key": "directTimestamp",
                            },
                            {
                                "metricsIndex": 1,
                                "key": "directPerformanceCondition",
                                "unit": {"key": "dimensionless"},
                            },
                        ],
                        "activityDetailMetrics": [
                            {"metrics": [1_788_000_000, 0]},
                            {"metrics": [1_788_000_060, -1]},
                        ],
                    },
                }
            },
        },
    )

    pc = build_latest_session_evidence(tmp_path, day)["performance_condition"]

    assert pc["status"] == "available"
    assert pc["timing_basis"] == "withheld_missing_or_non_monotonic_elapsed"
    assert pc["first_elapsed_min"] is None
    assert pc["last_elapsed_min"] is None


def test_performance_condition_huge_integer_timestamp_fails_closed(tmp_path):
    activity_id = 206101
    day = "2026-08-31"
    _write_activity(tmp_path, activity_id, f"{day} 08:00:00", normPower=140)
    _write_metadata(tmp_path, activity_id, day)
    write_json(
        tmp_path / "activities" / "details" / f"garmin_{activity_id}_detail.json",
        {
            "activity_id": str(activity_id),
            "calls": {
                "details": {
                    "ok": True,
                    "status": "success",
                    "data": {
                        "metricDescriptors": [
                            {
                                "metricsIndex": 0,
                                "key": "directTimestamp",
                                "unit": {"key": "gmt"},
                            },
                            {
                                "metricsIndex": 1,
                                "key": "directPerformanceCondition",
                                "unit": {"key": "dimensionless"},
                            },
                        ],
                        "activityDetailMetrics": [
                            {"metrics": [10**400, 0]},
                            {"metrics": [10**400 + 1, -1]},
                        ],
                    },
                }
            },
        },
    )

    pc = build_latest_session_evidence(tmp_path, day)["performance_condition"]

    assert pc["status"] == "not_available"


def test_performance_condition_prefers_valid_elapsed_order_over_timestamp_order(tmp_path):
    activity_id = 20611
    day = "2026-08-31"
    _write_activity(
        tmp_path,
        activity_id,
        f"{day} 08:00:00",
        elapsedDuration=180,
        duration=180,
        normPower=140,
    )
    _write_metadata(tmp_path, activity_id, day)
    write_json(
        tmp_path / "activities" / "details" / f"garmin_{activity_id}_detail.json",
        {
            "activity_id": str(activity_id),
            "calls": {
                "details": {
                    "ok": True,
                    "status": "success",
                    "data": {
                        "metricDescriptors": [
                            {
                                "metricsIndex": 0,
                                "key": "directTimestamp",
                                "unit": {"key": "gmt"},
                            },
                            {
                                "metricsIndex": 1,
                                "key": "sumElapsedDuration",
                                "unit": {"key": "second"},
                            },
                            {
                                "metricsIndex": 2,
                                "key": "directPerformanceCondition",
                                "unit": {"key": "dimensionless"},
                            },
                        ],
                        "activityDetailMetrics": [
                            {"metrics": [1_788_000_000, 0, None]},
                            {"metrics": [1_788_000_120, 60, 2]},
                            {"metrics": [1_788_000_060, 120, -1]},
                        ],
                    },
                }
            },
        },
    )

    pc = build_latest_session_evidence(tmp_path, day)["performance_condition"]

    assert pc["status"] == "available"
    assert pc["timing_basis"] == "sumElapsedDuration_second"
    assert pc["first_value"] == 2
    assert pc["first_elapsed_min"] == 1.0
    assert pc["final_value"] == -1
    assert pc["last_elapsed_min"] == 2.0


def test_performance_condition_withholds_epoch_elapsed_when_span_exceeds_session(tmp_path):
    activity_id = 20612
    day = "2026-08-31"
    _write_activity(tmp_path, activity_id, f"{day} 08:00:00", normPower=140)
    _write_metadata(tmp_path, activity_id, day)
    write_json(
        tmp_path / "activities" / "details" / f"garmin_{activity_id}_detail.json",
        {
            "activity_id": str(activity_id),
            "calls": {
                "details": {
                    "ok": True,
                    "status": "success",
                    "data": {
                        "metricDescriptors": [
                            {
                                "metricsIndex": 0,
                                "key": "directTimestamp",
                                "unit": {"key": "gmt"},
                            },
                            {
                                "metricsIndex": 1,
                                "key": "directPerformanceCondition",
                                "unit": {"key": "dimensionless"},
                            },
                        ],
                        "activityDetailMetrics": [
                            {"metrics": [1_756_000_000, 0]},
                            {"metrics": [1_788_000_000, -1]},
                        ],
                    },
                }
            },
        },
    )

    pc = build_latest_session_evidence(tmp_path, day)["performance_condition"]

    assert pc["status"] == "available"
    assert pc["timing_basis"] == (
        "withheld_timestamp_span_or_gap_exceeds_session_duration"
    )
    assert pc["first_elapsed_min"] is None
    assert pc["last_elapsed_min"] is None
    assert all(item["elapsed_min"] is None for item in pc["state_points"])


def test_performance_condition_surfaces_cached_trace_refresh_failure(tmp_path):
    activity_id = 2062
    day = "2026-08-31"
    _write_activity(tmp_path, activity_id, f"{day} 08:00:00", avgPower=100)
    _write_metadata(tmp_path, activity_id, day)
    write_json(
        tmp_path / "activities" / "details" / f"garmin_{activity_id}_detail.json",
        {
            "activity_id": str(activity_id),
            "calls": {
                "details": {
                    "ok": True,
                    "status": "success",
                    "last_attempt_ok": False,
                    "last_success_at": f"{day}T09:00:00+08:00",
                    "latest_attempt": {
                        "status": "failed",
                        "attempted_at": f"{day}T10:00:00+08:00",
                        "error": "temporary Garmin failure",
                        "data": "must_not_leak",
                    },
                    "data": {
                        "metricDescriptors": [
                            {
                                "metricsIndex": 0,
                                "key": "sumElapsedDuration",
                                "unit": {"key": "second"},
                            },
                            {
                                "metricsIndex": 1,
                                "key": "directPerformanceCondition",
                                "unit": {"key": "dimensionless"},
                            },
                        ],
                        "activityDetailMetrics": [
                            {"metrics": [0, 0]},
                            {"metrics": [60, -1]},
                        ],
                    },
                }
            },
        },
    )

    pc = build_latest_session_evidence(tmp_path, day)["performance_condition"]

    assert pc["status"] == "available_cached_after_refresh_failure"
    assert pc["provenance"]["last_attempt_ok"] is False
    assert pc["provenance"]["latest_attempt"]["status"] == "failed"
    assert "must_not_leak" not in str(pc)


def test_performance_condition_requires_positive_power_or_external_power_provenance(tmp_path):
    activity_id = 2063
    day = "2026-08-31"
    _write_activity(
        tmp_path,
        activity_id,
        f"{day} 08:00:00",
        avgPower=0,
        normPower="not-a-number",
        maxPower=0,
    )
    _write_metadata(tmp_path, activity_id, day)

    pc = build_latest_session_evidence(tmp_path, day)["performance_condition"]

    assert pc["status"] == "not_available_power_context_missing"


def test_performance_condition_power_gate_rejects_huge_and_junk_scalars(tmp_path):
    activity_id = 20631
    day = "2026-08-31"
    _write_activity(
        tmp_path,
        activity_id,
        f"{day} 08:00:00",
        avgPower=[140],
        normPower="watts 140",
        normalizedPower=10**400,
        maxPower={"watts": 500},
    )
    _write_metadata(tmp_path, activity_id, day)

    pc = build_latest_session_evidence(tmp_path, day)["performance_condition"]

    assert pc["status"] == "not_available_power_context_missing"
    assert pc["power_context_basis"] == []


def test_performance_condition_malformed_detail_lists_fail_closed(tmp_path):
    for index, malformed_field in enumerate(
        ("metricDescriptors", "activityDetailMetrics"), start=1
    ):
        root = tmp_path / malformed_field
        activity_id = 20640 + index
        day = "2026-08-31"
        _write_activity(root, activity_id, f"{day} 08:00:00", avgPower=100)
        _write_metadata(root, activity_id, day)
        data = {
            "metricDescriptors": [],
            "activityDetailMetrics": [],
        }
        data[malformed_field] = 10**400
        write_json(
            root / "activities" / "details" / f"garmin_{activity_id}_detail.json",
            {
                "activity_id": str(activity_id),
                "calls": {"details": {"ok": True, "data": data}},
            },
        )

        pc = build_latest_session_evidence(root, day)["performance_condition"]

        assert pc["status"] == "not_available"


def test_performance_condition_withholds_elapsed_when_units_or_sequence_are_invalid(tmp_path):
    activity_id = 207
    day = "2026-08-31"
    _write_activity(
        tmp_path,
        activity_id,
        f"{day} 08:00:00",
        avgPower=100,
    )
    _write_metadata(tmp_path, activity_id, day)
    write_json(
        tmp_path / "activities" / "details" / f"garmin_{activity_id}_detail.json",
        {
            "activity_id": str(activity_id),
            "calls": {
                "details": {
                    "ok": True,
                    "data": {
                        "metricDescriptors": [
                            {"metricsIndex": 0, "key": "directTimestamp", "unit": {"key": "unknown"}},
                            {"metricsIndex": 1, "key": "sumElapsedDuration", "unit": {"key": "minute"}},
                            {"metricsIndex": 2, "key": "directPerformanceCondition", "unit": {"key": "dimensionless"}},
                        ],
                        "activityDetailMetrics": [
                            {"metrics": [1_000_000, 7, 1]},
                            {"metrics": [1_060_000, 6, 0]},
                        ],
                    },
                }
            },
        },
    )

    pc = build_latest_session_evidence(tmp_path, day)["performance_condition"]

    assert pc["status"] == "available"
    assert pc["timing_basis"] == "withheld_missing_or_non_monotonic_elapsed"
    assert all(item["elapsed_min"] is None for item in pc["state_points"])


def test_self_evaluation_carries_and_validates_activity_identity(tmp_path):
    activity_id = 208
    day = "2026-08-31"
    _write_activity(tmp_path, activity_id, f"{day} 08:00:00")
    _write_metadata(tmp_path, activity_id, day)

    matched = build_latest_session_evidence(tmp_path, day)["self_evaluation"]
    assert matched["activity_id"] == str(activity_id)
    assert matched["date"] == day
    assert matched["identity_status"] == "exact_match"

    index_path = tmp_path / "snapshots" / "activity_self_evaluation_index.json"
    index = read_json(index_path, {})
    conflicting = dict(index["activities"][0])
    conflicting["date"] = "2026-08-30"
    conflicting["feel_score"] = 25
    index["activities"].append(conflicting)
    write_json(index_path, index)

    exact_preferred = build_latest_session_evidence(tmp_path, day)["self_evaluation"]
    assert exact_preferred["identity_status"] == "exact_match"
    assert exact_preferred["feel_score"] == 75

    index["activities"] = [conflicting]
    write_json(index_path, index)

    mismatched = build_latest_session_evidence(tmp_path, day)["self_evaluation"]
    assert mismatched["status"] == "identity_mismatch"
    assert mismatched["identity_status"] == "date_mismatch"
    assert "feel_score" not in mismatched


def test_session_self_evaluation_fails_closed_when_both_categories_are_off_grid(tmp_path):
    activity_id = 2081
    day = "2026-08-31"
    _write_activity(tmp_path, activity_id, f"{day} 08:00:00")
    _write_metadata(tmp_path, activity_id, day)
    path = tmp_path / "snapshots" / "activity_self_evaluation_index.json"
    index = read_json(path, {})
    index["activities"][0].update(
        {
            "has_self_evaluation": True,
            "feel_score": 74,
            "rpe_score": 35,
        }
    )
    write_json(path, index)

    subjective = build_latest_session_evidence(tmp_path, day)["self_evaluation"]

    assert subjective["status"] == "unusable_invalid_categories"
    assert subjective["invalid_category_fields"] == ["feel_score", "rpe_score"]
    assert "feel_out_of_5" not in subjective


def test_session_self_evaluation_surfaces_cached_refresh_failure(tmp_path):
    activity_id = 2082
    day = "2026-08-31"
    _write_activity(tmp_path, activity_id, f"{day} 08:00:00")
    _write_metadata(tmp_path, activity_id, day)
    path = tmp_path / "snapshots" / "activity_self_evaluation_index.json"
    index = read_json(path, {})
    index["activities"][0].update(
        {
            "last_attempt_ok": False,
            "latest_attempt": {
                "status": "failed",
                "fetched_at": f"{day}T10:00:00+08:00",
                "error": "temporary Garmin failure",
            },
        }
    )
    write_json(path, index)

    subjective = build_latest_session_evidence(tmp_path, day)["self_evaluation"]

    assert subjective["status"] == "available_cached_after_refresh_failure"
    assert subjective["latest_attempt"]["status"] == "failed"


def test_session_speed_provenance_does_not_trust_stale_external_boolean(tmp_path):
    activity_id = 209
    day = "2026-08-31"
    _write_activity(tmp_path, activity_id, f"{day} 08:00:00")
    _write_metadata(tmp_path, activity_id, day)
    path = tmp_path / "snapshots" / "activity_device_index.json"
    index = read_json(path, {})
    row = index["activities"][0]
    row["external_speed_sensor"] = True
    row["external_speed_sensor_battery_statuses"] = ["GOOD"]
    row["sensors"].append(
        {
            "sensor_type": "BIKE_SPEED",
            "source_type": "LOCAL",
            "battery_status": "GOOD",
        }
    )
    write_json(path, index)

    speed = build_latest_session_evidence(tmp_path, day)["device"]["speed_measurement"]

    assert speed["external_speed_sensor"] is False
    assert speed["battery_statuses"] == []


def test_session_speed_provenance_can_use_preserved_standard_fit_fallback(
    tmp_path,
    monkeypatch,
):
    activity_id = 210
    day = "2026-08-31"
    _write_activity(tmp_path, activity_id, f"{day} 08:00:00")
    _write_metadata(tmp_path, activity_id, day)
    fit_path = (
        tmp_path
        / "activities"
        / "fit"
        / f"garmin_{activity_id}_original.fit"
    )
    fit_path.parent.mkdir(parents=True, exist_ok=True)
    fit_path.write_bytes(b"FIT")
    monkeypatch.setattr(
        "coach_sync.session_evidence.read_standard_fit_device_sources",
        lambda _path: {
            "status": "available",
            "sensor_types": ["BIKE_SPEED"],
            "external_sensor_types": ["BIKE_SPEED"],
            "external_speed_sensor": True,
            "external_speed_sensor_battery_statuses": ["OK"],
        },
    )

    device = build_latest_session_evidence(tmp_path, day)["device"]

    assert device["speed_measurement"]["status"] == (
        "external_bike_speed_sensor_in_preserved_standard_fit"
    )
    assert device["speed_measurement"]["external_speed_sensor"] is True
    assert device["speed_measurement"]["battery_statuses"] == ["OK"]


def _write_hike_detail_trace(
    root,
    activity_id,
    include_external_hr=False,
    timestamp_unit="gmt",
):
    interval_sec = 176
    rows = []
    distance = 0.0
    for index in range(60):
        if index <= 23:
            elevation = 1880.0 + (343.0 * index / 23.0)
            heart_rate = 110.0 + index
        elif index <= 35:
            elevation = 2221.0
            heart_rate = 95.0
        else:
            elevation = 2221.0 - (331.0 * (index - 35) / 24.0)
            heart_rate = 92.0
        if index and not 24 <= index <= 35:
            distance += 50.0
        rows.append(
            {
                "metrics": [
                    1_800_000_000_000 + index * interval_sec * 1000,
                    distance,
                    0.0,
                    elevation,
                    heart_rate,
                ]
            }
        )
    sensors = (
        [
            {
                "manufacturer": "GARMIN",
                "sourceType": "ANTPLUS",
                "antplusDeviceType": "HEART_RATE",
                "batteryStatus": "GOOD",
            }
        ]
        if include_external_hr
        else None
    )
    timestamp_descriptor = {
        "key": "directTimestamp",
        "metricsIndex": 0,
    }
    if timestamp_unit is not None:
        timestamp_descriptor["unit"] = {"key": timestamp_unit}
    write_json(
        root / "activities" / "details" / f"garmin_{activity_id}_detail.json",
        {
            "artifact_type": "raw_garmin_key_activity_detail",
            "activity_id": str(activity_id),
            "calls": {
                "activity": {
                    "ok": True,
                    "status": "success",
                    "data": {
                        "activityId": activity_id,
                        "metadataDTO": {
                            "manufacturer": "GARMIN",
                            "deviceMetaDataDTO": {
                                "deviceId": "SENSITIVE_DEVICE_IDENTIFIER",
                                "deviceTypePk": 1,
                                "deviceVersionPk": 2,
                            },
                            "sensors": sensors,
                        },
                    },
                },
                "details": {
                    "ok": True,
                    "status": "success",
                    "data": {
                        "metricDescriptors": [
                            timestamp_descriptor,
                            {"key": "sumDistance", "metricsIndex": 1},
                            {"key": "directSpeed", "metricsIndex": 2},
                            {"key": "directElevation", "metricsIndex": 3},
                            {"key": "directHeartRate", "metricsIndex": 4},
                        ],
                        "activityDetailMetrics": rows,
                    },
                },
            },
        },
    )


def test_latest_hike_replaces_implausible_moving_duration_from_trace_and_surfaces_phases(
    tmp_path,
    monkeypatch,
):
    activity_id = 250
    day = "2026-08-04"
    _write_activity(
        tmp_path,
        activity_id,
        f"{day} 09:21:03",
        activity_type="hiking",
        activityName="Steep mountain hike",
        duration=10384,
        elapsedDuration=10384,
        movingDuration=1804,
        distance=3090,
        elevationGain=344,
        elevationLoss=337,
        averageHR=106,
        maxHR=151,
    )
    _write_hike_detail_trace(tmp_path, activity_id)
    _write_loop(tmp_path, activity_id, day)
    fit_path = (
        tmp_path
        / "activities"
        / "fit"
        / f"garmin_{activity_id}_original.zip"
    )
    fit_path.parent.mkdir(parents=True, exist_ok=True)
    fit_path.write_bytes(b"preserved-fit-placeholder")
    monkeypatch.setattr(
        "coach_sync.session_evidence.read_standard_fit_device_sources",
        lambda path: {
            "status": "available",
            "source_types": ["local"],
            "external_device_source_present": False,
            "local_or_onboard_source_present": True,
            "local_or_onboard_only": True,
            "creator": {"manufacturer": "garmin", "source_type": "local"},
        },
    )

    evidence = build_latest_session_evidence(tmp_path, day)

    assert evidence["activity"]["category"] == "hike"
    assert evidence["loop_analysis"] is None
    assert evidence["timing"]["moving_min"] > 120
    assert evidence["timing"]["stopped_min"] < 50
    assert evidence["timing"]["stopped_derivation"] == (
        "derived_detail_trace_movement_timeline"
    )
    assert evidence["timing"]["garmin_reported"]["moving_min"] == 30.1
    assert evidence["timing"]["garmin_reported"]["implied_stopped_min"] == 143.0
    assert evidence["gear"]["status"] == "not_applicable_non_bike"
    assert evidence["device"]["hr_confidence"] == "wrist_or_onboard_likely"
    assert evidence["device"]["standard_fit_device_sources"]["source_types"] == [
        "local"
    ]
    assert "device_id" not in str(evidence["device"]).lower()
    assert "SENSITIVE_DEVICE_IDENTIFIER" not in str(evidence["device"])
    assert evidence["timing"]["nonmoving_or_stopped_estimate_min"] < 50
    assert "not proof" in evidence["timing"]["stopped_interpretation"]
    phases = evidence["hike_phase_summary"]
    assert phases["status"] == "available_derived"
    assert phases["top_band"]["rule"] == "within_8_m_of_trace_max"
    assert phases["phases"]["ascent_to_first_top_band_entry"]["average_hr_bpm"] > 120
    assert phases["phases"]["descent_after_last_top_band_exit"]["max_hr_bpm"] == 95.0
    assert "does not infer SpO2" in phases["interpretation_guardrail"]


def test_latest_hike_accepts_duration_bounded_epoch_trace_with_missing_timestamp_unit(
    tmp_path,
):
    activity_id = 2501
    day = "2026-08-04"
    _write_activity(
        tmp_path,
        activity_id,
        f"{day} 09:21:03",
        activity_type="hiking",
        activityName="Cached steep mountain hike",
        duration=10384,
        elapsedDuration=10384,
        movingDuration=1804,
        distance=3090,
        elevationGain=344,
        elevationLoss=337,
        averageHR=106,
        maxHR=151,
    )
    _write_hike_detail_trace(
        tmp_path,
        activity_id,
        timestamp_unit=None,
    )

    evidence = build_latest_session_evidence(tmp_path, day)

    assert evidence["timing"]["stopped_derivation"] == (
        "derived_detail_trace_movement_timeline"
    )
    assert evidence["timing"]["moving_min"] > 120
    assert evidence["hike_phase_summary"]["status"] == "available_derived"
    assert evidence["hike_phase_summary"]["top_band"][
        "first_entry_offset_min"
    ] > 60


def test_latest_hike_skips_elapsed_only_row_with_missing_timestamp(tmp_path):
    activity_id = 2502
    day = "2026-08-04"
    _write_activity(
        tmp_path,
        activity_id,
        f"{day} 09:21:03",
        activity_type="hiking",
        duration=10384,
        elapsedDuration=10384,
        movingDuration=1804,
        distance=3090,
        elevationGain=344,
    )
    _write_hike_detail_trace(tmp_path, activity_id)
    detail_path = (
        tmp_path / "activities" / "details" / f"garmin_{activity_id}_detail.json"
    )
    payload = read_json(detail_path, {})
    data = payload["calls"]["details"]["data"]
    data["metricDescriptors"].append(
        {
            "key": "sumElapsedDuration",
            "metricsIndex": 5,
            "unit": {"key": "second"},
        }
    )
    for index, sample in enumerate(data["activityDetailMetrics"]):
        sample["metrics"].append(index * 176)
    data["activityDetailMetrics"][0]["metrics"][0] = None
    write_json(detail_path, payload)

    evidence = build_latest_session_evidence(tmp_path, day)

    assert evidence["timing"]["stopped_derivation"] == (
        "derived_detail_trace_movement_timeline"
    )
    assert evidence["hike_phase_summary"]["status"] == "available_derived"
    assert evidence["hike_phase_summary"]["trace_sample_count"] == 59


def test_latest_hike_withholds_implausible_stopped_time_without_credible_trace(tmp_path):
    activity_id = 251
    day = "2026-08-04"
    _write_activity(
        tmp_path,
        activity_id,
        f"{day} 09:21:03",
        activity_type="hiking",
        duration=10380,
        elapsedDuration=10380,
        movingDuration=1800,
        distance=3090,
        elevationGain=344,
    )

    evidence = build_latest_session_evidence(tmp_path, day)

    assert evidence["timing"]["moving_min"] is None
    assert evidence["timing"]["stopped_min"] is None
    assert evidence["timing"]["garmin_reported"]["moving_min"] == 30.0
    assert evidence["timing"]["garmin_reported"]["implied_stopped_min"] == 143.0
    assert evidence["timing"]["plausibility"]["status"] == (
        "withheld_without_credible_trace_support"
    )


def test_latest_hike_consumes_preserved_standard_external_hr_metadata(tmp_path):
    activity_id = 252
    day = "2026-08-04"
    _write_activity(
        tmp_path,
        activity_id,
        f"{day} 09:21:03",
        activity_type="hiking",
        duration=3600,
        elapsedDuration=3600,
        movingDuration=3300,
    )
    _write_hike_detail_trace(tmp_path, activity_id, include_external_hr=True)

    evidence = build_latest_session_evidence(tmp_path, day)

    assert evidence["device"]["status"] == "available_from_preserved_detail"
    assert evidence["device"]["external_hr_sensor"] is True
    assert evidence["device"]["hr_confidence"] == "external_hr"
    assert evidence["provenance"]["activity_detail"] == (
        "activities/details/garmin_252_detail.json"
    )


def test_latest_gym_session_surfaces_exercises_sets_reps_and_normalized_volume(tmp_path):
    _write_activity(
        tmp_path,
        301,
        "2026-07-03 12:30:00",
        activity_type="strength_training",
        activeSets=6,
        totalSets=6,
        totalReps=30,
        summarizedExerciseSets=[
            {
                "category": "SQUAT",
                "sets": 4,
                "reps": 20,
                "maxWeight": 60000,
                "volume": 1200000,
                "duration": 90000,
            },
            {
                "category": "ROW",
                "sets": 2,
                "reps": 10,
                "maxWeight": 35000,
                "volume": 350000,
                "duration": 45000,
            },
        ],
    )
    write_json(
        tmp_path / "activities" / "details" / "garmin_301_detail.json",
        {
            "activity_id": "301",
            "calls": {
                "exercise_sets": {
                    "ok": True,
                    "status": "success",
                    "data": {
                        "activityId": 301,
                        "exerciseSets": [
                            {
                                "setType": "ACTIVE",
                                "duration": 30,
                                "repetitionCount": 5,
                                "weight": 60000,
                                "exercises": [
                                    {"category": "SQUAT", "probability": 80.0}
                                ],
                            },
                            {
                                "setType": "REST",
                                "duration": 60,
                                "repetitionCount": None,
                                "weight": None,
                                "exercises": [],
                            },
                            {
                                "setType": "ACTIVE",
                                "duration": 25,
                                "repetitionCount": 5,
                                "weight": 65000,
                                "exercises": [
                                    {"category": "SQUAT", "probability": 60.0}
                                ],
                            },
                        ],
                    },
                }
            },
        },
    )

    evidence = build_latest_session_evidence(tmp_path, "2026-07-04")

    assert evidence["activity"]["category"] == "gym"
    assert evidence["gym"]["total_sets"] == 6
    assert evidence["gym"]["total_reps"] == 30
    assert evidence["gym"]["total_volume_kg_reps"] == 1550.0
    assert evidence["gym"]["exercises"][0]["max_weight_kg"] == 60.0
    assert evidence["gym"]["exercises"][0]["active_duration_min"] == 1.5
    detailed = evidence["gym"]["detailed_sets"]
    assert detailed["active_sets"] == 2
    assert detailed["rest_intervals"] == 1
    assert detailed["exercise_groups"][0]["exercise"] == "SQUAT"
    assert detailed["exercise_groups"][0]["max_weight_kg"] == 65.0
    assert detailed["exercise_groups"][0]["volume_kg_reps"] == 625.0
    assert detailed["exercise_groups"][0]["mean_detection_probability_pct"] == 70.0
    assert evidence["provenance"]["activity_detail"] == (
        "activities/details/garmin_301_detail.json"
    )


def test_coach_packet_promotes_compact_session_evidence_and_confidence_cautions(tmp_path):
    _write_activity(tmp_path, 401, "2026-07-09 12:00:00")
    evidence = build_latest_session_evidence(tmp_path, "2026-07-10")
    state = {
        "date": "2026-07-10",
        "readiness": {"readiness_level": "yellow", "readiness_score": 65, "reasons": []},
        "data_freshness": {"status": "current", "activity_data": {"status": "current"}},
        "phase": {"name": "base_rebuild"},
        "latest_session_evidence": evidence,
    }
    plan = {
        "date": "2026-07-10",
        "session": {
            "title": "Easy continuity",
            "type": "bike_easy",
            "duration_min": 45,
            "intensity": "easy",
        },
    }

    packet = build_coach_packet(tmp_path, "2026-07-10", state=state, plan=plan)
    signal = next(
        item for item in packet["evidence"]["trusted"] if item["name"] == "Latest session evidence"
    )

    assert signal["status"] == "partial"
    assert signal["value"]["activity"]["activity_id"] == "401"
    assert "heart_rate" in signal["value"]
    assert any(
        item["source"] == "latest_session_evidence"
        for item in packet["evidence"]["cautions"]
    )


def test_current_state_exposes_wellness_intraday_provenance_without_fake_historical_age(tmp_path):
    load_context(tmp_path)
    day = "2026-07-09"
    write_json(
        tmp_path / "snapshots" / f"garmin_wellness_{day}.json",
        {
            "date": day,
            "fetched_at": "2026-07-09T10:00:00+08:00",
            "payloads": [
                {
                    "label": "get_stats",
                    "ok": True,
                    "data": {
                        "calendarDate": day,
                        "wellnessEndTimeLocal": "2026-07-09T09:28:00",
                        "lastSyncTimestampGMT": "2026-07-09T02:00:00Z",
                        "sleepScore": 80,
                    },
                }
            ],
        },
    )
    _write_activity(tmp_path, 501, f"{day} 08:00:00", activity_type="indoor_cycling")

    state = build_current_state(tmp_path, day, refresh_models=False)
    provenance = state["data_freshness"]["wellness_data"]["intraday_provenance"]

    assert provenance["status"] == "available"
    assert provenance["source_fetched_at"] == "2026-07-09T10:00:00+08:00"
    assert provenance["source_data_cutoff_local"] == "2026-07-09T09:28:00+08:00"
    assert provenance["source_data_cutoff_age_min"] is None
    assert provenance["age_basis"] == "not_computed_for_historical_target"
