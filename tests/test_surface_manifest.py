import json

from coach_sync.data_inventory import build_data_inventory
from coach_sync.data_quality import build_data_quality_report
from coach_sync.io import write_json
from coach_sync.surface_manifest import (
    build_garmin_surface_manifest,
    verify_activity_summary_privacy,
)


def _wellness(day, extra_payloads=None):
    return {
        "date": day,
        "source": "garminconnect",
        "payloads": [
            {
                "label": "get_stats",
                "ok": True,
                "data": {
                    "calendarDate": day,
                    "restingHeartRate": 50,
                    "averageStressLevel": 25,
                    "bodyBatteryMostRecentValue": 60,
                    "wellnessEndTimeLocal": f"{day}T09:00:00.0",
                },
            },
            *(extra_payloads or []),
        ],
    }


def _activity(activity_id, day, average_hr):
    return {
        "activityId": activity_id,
        "activityType": {"typeKey": "mountain_biking"},
        "startTimeLocal": f"{day} 08:00:00",
        "duration": 3600,
        "movingDuration": 3000,
        "activityTrainingLoad": 80,
        "averageHR": average_hr,
        "maxHR": 175,
        **(
            {f"hrTimeInZone_{zone}": zone * 60 for zone in range(1, 6)}
            if activity_id == 1
            else {}
        ),
    }


def _seed_surface(root):
    write_json(
        root / "snapshots" / "garmin_wellness_2026-01-01.json",
        _wellness(
            "2026-01-01",
            [
                {"label": "get_user_summary", "ok": True, "data": {}},
                {"label": "get_body_battery_events", "ok": True, "data": []},
                {
                    "label": "get_sleep_data",
                    "ok": False,
                    "attempted_at": "2026-01-01T09:00:00+08:00",
                    "error": "timeout https://private.example/profile/123 C:\\private\\token",
                },
                {"label": "get_hrv_data", "ok": False, "error": "missing_method"},
            ],
        ),
    )
    write_json(
        root / "snapshots" / "garmin_wellness_2026-01-02.json",
        _wellness("2026-01-02"),
    )
    write_json(
        root / "snapshots" / "garmin_wellness_2026-01-05.json",
        _wellness(
            "2026-01-05",
            [
                {
                    "label": "get_all_day_stress",
                    "ok": True,
                    "status": "success",
                    "attempted_at": "2026-01-05T09:05:00+08:00",
                    "last_attempt_status": "failed",
                    "last_success_at": "2026-01-05T09:05:00+08:00",
                    "data": {
                        "stressValuesArray": [[1767574800000, 18]],
                        "bodyBatteryValuesArray": [[1767574800000, 65]],
                    },
                    "latest_attempt": {
                        "label": "get_all_day_stress",
                        "ok": False,
                        "status": "failed",
                        "attempted_at": "2026-01-05T09:10:00+08:00",
                        "error": "temporary Garmin failure",
                    },
                    "retention_policy": "preserve_last_nonempty_success",
                },
                {
                    "label": "get_heart_rates",
                    "ok": True,
                    "status": "success",
                    "attempted_at": "2026-01-05T09:05:00+08:00",
                    "data": {
                        "calendarDate": "2026-01-05",
                        "heartRateValues": [[1767574800000, 55]],
                    },
                },
                {
                    "label": "get_spo2_data",
                    "ok": True,
                    "status": "success",
                    "attempted_at": "2026-01-05T09:05:00+08:00",
                    "data": {
                        "calendarDate": "2026-01-05",
                        "endTimestampLocal": "2026-01-05T09:00:00+08:00",
                        "averageSpO2": 96,
                        "avgSleepSpO2": 95,
                        "spO2HourlyAverages": [[1767574800000, 96]],
                    },
                },
                {
                    "label": "get_respiration_data",
                    "ok": True,
                    "status": "success",
                    "attempted_at": "2026-01-05T09:05:00+08:00",
                    "data": {
                        "calendarDate": "2026-01-05",
                        "endTimestampLocal": "2026-01-05T09:00:00+08:00",
                        "avgWakingRespirationValue": 14,
                        "respirationValuesArray": [[1767574800000, 14]],
                        "respirationAveragesValuesArray": [
                            [1767574800000, 14, 15, 13]
                        ],
                    },
                },
            ],
        ),
    )
    write_json(root / "activities" / "one.json", _activity(1, "2026-01-04", 140))
    write_json(root / "activities" / "two.json", _activity(2, "2026-01-05", None))
    write_json(
        root / "snapshots" / "garmin_training_status_2026-01-05.json",
        {
            "date": "2026-01-05",
            "payload": {
                "label": "get_training_status",
                "ok": True,
                "data": {
                    "mostRecentTrainingStatus": {
                        "latestTrainingStatusData": {
                            "device": {
                                "primaryTrainingDevice": True,
                                "trainingStatusFeedbackPhrase": "MAINTAINING_2",
                            }
                        }
                    }
                },
            },
        },
    )
    write_json(
        root / "snapshots" / "activity_gear_index.json",
        {
            "generated_at": "2026-01-05T10:00:00+08:00",
            "activities": [
                {
                    "activity_id": "1",
                    "date": "2026-01-04",
                    "category": "mtb",
                    "gear_fetch_ok": True,
                    "gear": [{"label": "Stumpjumper"}],
                }
            ],
        },
    )
    write_json(
        root / "snapshots" / "activity_self_evaluation_index.json",
        {
            "generated_at": "2026-01-05T10:01:00+08:00",
            "activities": [
                {
                    "activity_id": "1",
                    "date": "2026-01-04",
                    "category": "mtb",
                    "detail_fetch_ok": True,
                    "has_self_evaluation": True,
                    "feel_score": 50,
                    "rpe_score": 40,
                }
            ],
        },
    )
    write_json(
        root / "snapshots" / "activity_detail_1.json",
        {
            "generated_at": "2026-01-05T10:02:00+08:00",
            "calls": {
                "splits": {"label": "get_activity_splits", "ok": False, "error": "timeout"},
                "details": {"label": "get_activity_details", "ok": True, "data": {}},
                "weather": {
                    "label": "get_activity_weather",
                    "ok": False,
                    "error": "missing_method",
                },
                "activity": {
                    "label": "get_activity",
                    "ok": True,
                    "data": {"summaryDTO": {"activityId": 1}},
                },
            },
        },
    )
    write_json(
        root / "activities" / "details" / "garmin_2_detail.json",
        {
            "generated_at": "2026-01-05T10:03:00+08:00",
            "calls": {
                "hr_zones": {
                    "label": "get_activity_hr_in_timezones",
                    "ok": True,
                    "data": [{"zoneNumber": 1, "secsInZone": 600}],
                }
            },
        },
    )
    fit_path = root / "activities" / "fit" / "garmin_2.fit"
    fit_path.parent.mkdir(parents=True, exist_ok=True)
    fit_path.write_bytes(b"FIT")
    write_json(
        root / "snapshots" / "activity_summary_index.json",
        [
            {"activity_ref": "redacted-one", "date": "2026-01-04", "category": "mtb"},
            {"activity_ref": "redacted-two", "date": "2026-01-05", "category": "mtb"},
        ],
    )


def test_surface_manifest_tracks_endpoint_states_eras_fields_and_lineage(tmp_path):
    _seed_surface(tmp_path)

    manifest = build_garmin_surface_manifest(tmp_path, "2026-01-05")

    assert manifest["basis_date"] == "2026-01-05"
    assert (tmp_path / "snapshots" / "garmin_surface_manifest.json").exists()
    endpoints = manifest["configured_endpoints"]
    assert endpoints["wellness.get_stats"]["state"] == "success"
    assert endpoints["wellness.get_user_summary"]["state"] == "success_empty"
    assert endpoints["wellness.get_sleep_data"]["state"] == "failed"
    assert endpoints["wellness.get_sleep_data"]["latest_attempt_error"] == "timeout"
    assert "private.example" not in json.dumps(manifest)
    assert "private\\token" not in json.dumps(manifest)
    assert endpoints["wellness.get_hrv_data"]["state"] == "unsupported"
    assert endpoints["wellness.get_body_composition"]["state"] == "not_attempted"
    assert endpoints["wellness.get_all_day_stress"]["state"] == "success"
    assert endpoints["wellness.get_heart_rates"]["state"] == "success"
    assert endpoints["wellness.get_spo2_data"]["state"] == "success"
    assert endpoints["wellness.get_respiration_data"]["state"] == "success"
    assert endpoints["wellness.get_spo2_data"]["decision_use"] == (
        "context_only_downshift_or_verify_never_readiness_promotion"
    )
    assert endpoints["wellness.get_respiration_data"]["downstream_consumers"] == [
        "wellness_daily",
        "wellness_trends",
        "readiness_features_context_only",
        "current_state",
        "coach_packet",
    ]
    assert endpoints["wellness.get_heart_rates"]["downstream_consumers"] == [
        "wearable_coverage",
        "current_state",
        "coach_packet",
    ]
    assert endpoints["wellness.get_heart_rates"]["decision_use"] == (
        "wear_state_contact_provenance_only_never_physiology_or_session_clearance"
    )
    assert endpoints["wellness.get_all_day_stress"]["latest_attempt_state"] == "failed"
    assert endpoints["wellness.get_all_day_stress"]["decision_use"] == (
        "intraday_coverage_and_recovery_context_never_session_clearance"
    )
    assert "rest_recharge_window" in endpoints["wellness.get_all_day_stress"][
        "downstream_consumers"
    ]
    assert "wearable_coverage" in endpoints["wellness.get_all_day_stress"][
        "downstream_consumers"
    ]
    assert "rest_recharge_window" not in endpoints["wellness.get_all_day_stress"][
        "normalized_outputs"
    ]
    assert "readiness" not in endpoints["wellness.get_all_day_stress"][
        "downstream_consumers"
    ]
    assert "cns_readiness" not in endpoints["wellness.get_all_day_stress"][
        "downstream_consumers"
    ]
    assert endpoints["detail.splits"]["state"] == "failed"
    assert endpoints["detail.details"]["state"] == "success_empty"
    assert endpoints["detail.hr_zones"]["state"] == "success"
    assert endpoints["detail.weather"]["state"] == "unsupported"
    assert endpoints["training.get_training_status"]["state"] == "success"
    assert endpoints["training.get_training_readiness"]["state"] == "not_attempted"

    wellness = manifest["raw_sources"]["wellness"]
    assert len(wellness["usable_eras"]) == 2
    assert wellness["gaps"][0]["missing_days"] == 2
    assert wellness["latest_data_cutoff_local"] == "2026-01-05T09:00:00+08:00"

    activities = manifest["raw_sources"]["activities"]
    assert activities["valid_activity_count"] == 2
    assert activities["top_level_field_coverage"]["averageHR"]["present_count"] == 2
    assert activities["top_level_field_coverage"]["averageHR"]["non_null_count"] == 1
    assert activities["heart_rate_coverage"]["complete_five_hr_zones"]["count"] == 1
    assert manifest["raw_sources"]["activity_detail"]["artifact_count"] == 2
    assert manifest["raw_sources"]["activity_detail"]["retained_detail_count"] == 1
    assert manifest["raw_sources"]["activity_detail"]["legacy_snapshot_detail_count"] == 1
    assert manifest["raw_sources"]["activity_detail"]["fit_file_count"] == 1
    assert manifest["raw_sources"]["activity_metadata"]["gear"]["status"] == "partial"
    assert manifest["privacy_verification"]["activity_summary_index_redacts_names"] is True
    assert endpoints["activities.get_activities"]["downstream_consumers"]
    wellness_fields = manifest["normalized_surfaces"]["wellness_daily"]["fields"]
    assert "sleep_start_local" in wellness_fields
    assert "nap_hours_reported" in wellness_fields
    assert "sleep_duration_provenance" in wellness_fields
    assert "all_day_stress_valid_sample_count" in wellness_fields
    assert "all_day_stress_material_unavailable_minutes" in wellness_fields
    assert "all_day_stress_coverage_sufficient" in wellness_fields
    assert "all_day_stress_sufficiency_issues" in wellness_fields
    assert "all_day_stress_sample_cutoff_local" in wellness_fields
    assert "all_day_body_battery_latest_value" in wellness_fields
    assert "spo2_daily_average_pct" in wellness_fields
    assert "spo2_sleep_average_pct" in wellness_fields
    assert "spo2_hourly_highest_pct" in wellness_fields
    assert "respiration_two_min_activity_sentinel_count" in wellness_fields
    assert "respiration_two_min_valid_measurement_ratio" in wellness_fields
    assert "monitoring_altitude_m" in wellness_fields
    rest_surface = manifest["normalized_surfaces"]["rest_recharge_window"]
    assert rest_surface["source_path"] == "snapshots/rest_recharge_window.json"
    assert rest_surface["decision_use"] == (
        "intraday_recovery_context_only_never_session_clearance"
    )
    assert "safety_contract" in rest_surface["fields"]
    assert rest_surface["downstream_consumers"] == [
        "current_state",
        "coach_packet",
        "cns_readiness_negative_only",
    ]
    wearable_surface = manifest["normalized_surfaces"]["wearable_coverage"]
    assert wearable_surface["source_path"] == "snapshots/wearable_coverage.json"
    assert wearable_surface["decision_use"] == (
        "coverage_interpretation_only_never_readiness_clearance"
    )
    assert "safety_contract" in wearable_surface["fields"]
    assert "material_run_attribution" in wearable_surface["fields"]
    assert "attribution_summary" in wearable_surface["fields"]
    assert (
        "observed_coverage.optical_heart_rate.material_unavailable_runs"
        in wearable_surface["fields"]
    )
    assert "provenance.optical_hr_endpoint" in wearable_surface["fields"]
    assert (
        "observed_coverage.stress.positive_use_coverage.series_sufficient_for_low_stress_reward"
        in wearable_surface["fields"]
    )
    assert "provenance.wellness_snapshot_date_matches_target" in wearable_surface[
        "fields"
    ]
    activity_fields = manifest["normalized_surfaces"]["activity_summary"]["fields"]
    assert "start_time_local" in activity_fields
    assert "end_time_local" in activity_fields
    assert "start_time_bucket" in activity_fields
    assert manifest["units_and_provenance"]["temperature"].startswith("Do not merge")
    assert "never establish in-activity" in manifest["units_and_provenance"]["spo2"]
    assert "-2 activity sentinels" in manifest["units_and_provenance"]["respiration"]


def test_inventory_and_quality_surface_gaps_coverage_and_verified_privacy(tmp_path):
    _seed_surface(tmp_path)

    inventory = build_data_inventory(tmp_path, "2026-01-05")
    quality = build_data_quality_report(tmp_path, "2026-01-05")

    assert inventory["generated_at"]
    assert inventory["source_timestamps"]["wellness_latest_data_cutoff_local"]
    assert inventory["wellness_data_gaps"][0]["missing_days"] == 2
    assert inventory["activity_field_coverage"]["activityTrainingLoad"]["non_null_count"] == 2
    assert inventory["activity_heart_rate_coverage"]["average_hr"]["non_null_pct"] == 50.0
    assert inventory["metadata_coverage"]["devices"]["status"] == "missing"

    flag_types = {flag["type"] for flag in quality["flags"]}
    assert "gear_coverage_partial" in flag_types
    assert "devices_coverage_partial" in flag_types
    assert "self_evaluation_coverage_partial" in flag_types
    assert "wellness_history_discontinuous" in flag_types
    assert quality["privacy"]["activity_summary_index_redacts_names"] is True
    assert quality["activities"]["heart_rate_coverage"]["average_hr"]["non_null_count"] == 1


def test_privacy_verification_detects_name_and_local_path_fields(tmp_path):
    write_json(tmp_path / "activities" / "one.json", _activity(1, "2026-01-05", 140))
    write_json(
        tmp_path / "snapshots" / "activity_summary_index.json",
        [
            {
                "activity_ref": "redacted-one",
                "name": "Private ride name",
                "source_file": r"C:\\private\\activities\\one.json",
            }
        ],
    )

    verification = verify_activity_summary_privacy(tmp_path)

    assert verification["activity_summary_index_redacts_names"] is False
    assert verification["activity_summary_index_redacts_source_paths"] is False
    assert verification["forbidden_name_fields_found"] == ["name"]
    assert verification["raw_activity_files_preserved"] is True


def test_manifest_surfaces_separate_training_readiness_and_device_capability_health(tmp_path):
    write_json(
        tmp_path / "snapshots" / "garmin_training_readiness_2026-01-05.json",
        {
            "date": "2026-01-05",
            "fetched_at": "2026-01-05T09:00:00+08:00",
            "payloads": [
                {
                    "label": "get_training_readiness",
                    "ok": True,
                    "status": "success_empty",
                    "attempted_at": "2026-01-05T09:00:00+08:00",
                    "data": [],
                },
                {
                    "label": "get_morning_training_readiness",
                    "ok": False,
                    "status": "unsupported",
                    "attempted_at": "2026-01-05T09:00:01+08:00",
                    "error": "client_method_unavailable",
                },
            ],
        },
    )
    write_json(
        tmp_path / "snapshots" / "garmin_device_capabilities_2026-01-05.json",
        {
            "date": "2026-01-05",
            "fetched_at": "2026-01-05T09:00:02+08:00",
            "training_readiness_capable": False,
            "calls": {
                "devices": {
                    "label": "get_devices",
                    "ok": True,
                    "status": "success",
                    "data": [
                        {
                            "deviceId": "private-device-id",
                            "trainingReadinessCapable": False,
                        }
                    ],
                },
                "unit_system": {
                    "label": "get_unit_system",
                    "ok": True,
                    "status": "success",
                    "data": {"unitSystem": "metric"},
                },
            },
        },
    )

    manifest = build_garmin_surface_manifest(tmp_path, "2026-01-05")

    endpoints = manifest["configured_endpoints"]
    assert endpoints["training.get_training_readiness"]["state"] == "success_empty"
    assert endpoints["training.get_morning_training_readiness"]["state"] == "unsupported"
    assert endpoints["capabilities.get_devices"]["state"] == "success"
    capabilities = manifest["raw_sources"]["device_capabilities"]
    assert capabilities["training_readiness_capable"] is False
    assert capabilities["registered_device_count"] == 1
    assert capabilities["unit_system"] == {"unitSystem": "metric"}
    assert "private-device-id" not in json.dumps(manifest)


def test_manifest_surfaces_cycling_ftp_endpoint_and_consumers(tmp_path):
    _seed_surface(tmp_path)
    write_json(
        tmp_path / "snapshots" / "garmin_cycling_ftp_current.json",
        {
            "artifact_type": "garmin_cycling_ftp_current",
            "status": "available_current",
            "ftp_w": 211.0,
            "effective_date": "2026-01-05",
            "effective_at": "2026-01-05T16:48:44.0",
            "sport": "CYCLING",
            "biometric_source_type": "CHANGE_LOG",
            "source": "garminconnect.get_cycling_ftp",
            "last_success_at": "2026-01-05T17:00:00+08:00",
            "latest_attempt": {
                "label": "get_cycling_ftp",
                "status": "success",
                "attempted_at": "2026-01-05T17:00:00+08:00",
                "ftp_w": 211.0,
            },
            "decision_use": "current_garmin_operational_ftp_context",
        },
    )

    manifest = build_garmin_surface_manifest(tmp_path, "2026-01-05")

    endpoint = manifest["configured_endpoints"]["training.get_cycling_ftp"]
    assert endpoint["state"] == "success"
    assert "current_state" in endpoint["downstream_consumers"]
    assert manifest["raw_sources"]["cycling_ftp"]["ftp_w"] == 211.0
    surface = manifest["normalized_surfaces"]["cycling_ftp_current"]
    assert surface["source_path"] == "snapshots/garmin_cycling_ftp_current.json"
    assert "ftp_relative_prescription" in surface["downstream_consumers"]
    assert "garmin_detected_ftp" in manifest["normalized_surfaces"]["activity_summary"]["fields"]
