import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from coach_sync.context import load_context
from coach_sync.cli import main
from coach_sync.io import write_json
from coach_sync.wearable_coverage import (
    analyze_wearable_coverage,
    build_wearable_coverage,
)


TARGET = date(2026, 7, 19)
TZ = ZoneInfo("Asia/Kuala_Lumpur")


def _context(*, recurring=False):
    wearable = {
        "primary_device": "Garmin Fenix 6",
        "reported_recurring_exceptions": [],
    }
    if recurring:
        wearable["reported_recurring_exceptions"].append(
            {
                "label": "Private raw description must not surface",
                "weekday": 6,
                "frequency": "usually",
                "reason_category": "church_dress_watch",
                "replacement_device_category": "dress_watch",
            }
        )
    return {
        "athlete": {
            "timezone": "Asia/Kuala_Lumpur",
            "wearable_context": wearable,
        }
    }


def _snapshot(
    *,
    start="06:00",
    sentinel_start="08:00",
    sentinel_end="10:00",
    sentinel_windows=None,
    end="20:00",
):
    start_dt = datetime.fromisoformat(f"2026-07-19T{start}:00+08:00")
    end_dt = datetime.fromisoformat(f"2026-07-19T{end}:00+08:00")
    windows = sentinel_windows or [(sentinel_start, sentinel_end)]
    parsed_windows = [
        (
            datetime.fromisoformat(f"2026-07-19T{window_start}:00+08:00"),
            datetime.fromisoformat(f"2026-07-19T{window_end}:00+08:00"),
        )
        for window_start, window_end in windows
    ]
    stress = []
    battery = []
    current = start_dt
    while current <= end_dt:
        stamp = int(current.timestamp() * 1000)
        unavailable = any(
            window_start <= current < window_end
            for window_start, window_end in parsed_windows
        )
        stress.append([stamp, -1 if unavailable else 18])
        battery.append(
            [
                stamp,
                None if unavailable else "MEASURED",
                None if unavailable else 80,
                2.0,
            ]
        )
        current += timedelta(minutes=3)
    return {
        "date": TARGET.isoformat(),
        "payloads": [
            {
                "label": "get_all_day_stress",
                "ok": True,
                "status": "success",
                "attempted_at": "2026-07-19T20:01:00+08:00",
                "last_success_at": "2026-07-19T20:01:00+08:00",
                "latest_attempt": {
                    "status": "success",
                    "attempted_at": "2026-07-19T20:01:00+08:00",
                },
                "data": {
                    "calendarDate": TARGET.isoformat(),
                    "endTimestampLocal": "2026-07-19T20:00:00+08:00",
                    "stressValueDescriptorsDTOList": [
                        {"index": 0, "key": "timestamp"},
                        {"index": 1, "key": "stressLevel"},
                    ],
                    "bodyBatteryValueDescriptorsDTOList": [
                        {
                            "bodyBatteryValueDescriptorIndex": 0,
                            "bodyBatteryValueDescriptorKey": "timestamp",
                        },
                        {
                            "bodyBatteryValueDescriptorIndex": 1,
                            "bodyBatteryValueDescriptorKey": "bodyBatteryStatus",
                        },
                        {
                            "bodyBatteryValueDescriptorIndex": 2,
                            "bodyBatteryValueDescriptorKey": "bodyBatteryLevel",
                        },
                    ],
                    "stressValuesArray": stress,
                    "bodyBatteryValuesArray": battery,
                },
            }
        ],
    }


def _with_heart_rate(
    snapshot,
    *,
    unavailable_windows=(),
    start="06:00",
    end="20:00",
    payload_date=TARGET.isoformat(),
):
    start_dt = datetime.fromisoformat(f"2026-07-19T{start}:00+08:00")
    end_dt = datetime.fromisoformat(f"2026-07-19T{end}:00+08:00")
    windows = [
        (
            datetime.fromisoformat(f"2026-07-19T{window_start}:00+08:00"),
            datetime.fromisoformat(f"2026-07-19T{window_end}:00+08:00"),
        )
        for window_start, window_end in unavailable_windows
    ]
    values = []
    current = start_dt
    while current <= end_dt:
        containing = next(
            (
                (window_start, window_end)
                for window_start, window_end in windows
                if window_start <= current < window_end
            ),
            None,
        )
        if containing is None:
            values.append([int(current.timestamp() * 1000), 58])
        elif current == containing[0]:
            values.append([int(current.timestamp() * 1000), None])
        current += timedelta(minutes=2)
    end_gmt = end_dt.astimezone(ZoneInfo("UTC")).replace(tzinfo=None).isoformat()
    snapshot["payloads"].append(
        {
            "label": "get_heart_rates",
            "ok": True,
            "status": "success",
            "attempted_at": "2026-07-19T20:01:00+08:00",
            "last_success_at": "2026-07-19T20:01:00+08:00",
            "latest_attempt": {
                "status": "success",
                "attempted_at": "2026-07-19T20:01:00+08:00",
            },
            "retention_policy": "preserve_last_nonempty_success",
            "data": {
                "calendarDate": payload_date,
                "startTimestampLocal": "2026-07-19T00:00:00.0",
                # Garmin describes the nominal day here; GMT is the actual cutoff.
                "endTimestampLocal": "2026-07-20T00:00:00.0",
                "endTimestampGMT": end_gmt,
                "heartRateValueDescriptors": [
                    {"index": 0, "key": "timestamp"},
                    {"index": 1, "key": "heartrate"},
                ],
                "heartRateValues": values,
                "userProfilePK": "must_not_surface",
            },
        }
    )
    return snapshot


def _analyze(*, snapshot=None, feedback=None, context=None, as_of=None):
    return analyze_wearable_coverage(
        target_date=TARGET,
        wellness_snapshot=snapshot if snapshot is not None else _snapshot(),
        feedback=feedback or {},
        context=context or _context(),
        as_of=as_of or datetime(2026, 7, 19, 20, 30, tzinfo=TZ),
    )


def test_material_sentinel_run_stays_unexplained_without_athlete_timing():
    result = _analyze()

    assert result["classification"]["label"] == "unexplained_internal_unavailability"
    stress = result["observed_coverage"]["stress"]
    assert stress["material_unavailable_run_count"] == 1
    assert stress["material_unavailable_minutes"] == 120.0
    assert result["decision_use"]["low_stress_positive_reward_eligible"] is False
    assert result["safety_contract"]["may_treat_missing_as_rest"] is False
    assert result["safety_contract"]["may_impute_stress"] is False
    assert result["safety_contract"]["may_raise_cns_ceiling"] is False


def test_direct_optical_hr_gap_confirms_measurement_not_physical_watch_removal():
    result = _analyze(
        snapshot=_with_heart_rate(
            _snapshot(),
            unavailable_windows=[("08:00", "10:00")],
        )
    )

    assert result["classification"]["label"] == (
        "confirmed_optical_hr_measurement_unavailability"
    )
    run = result["material_run_attribution"][0]
    assert run["measurement_availability"]["confirmation"] == (
        "direct_garmin_heart_rate_series"
    )
    assert run["device_wear_state"] == {
        "state": "unknown",
        "confirmation": "unconfirmed",
    }
    assert run["cause_attribution"] == {"status": "none", "category": "unknown"}
    summary = result["attribution_summary"]
    assert summary["confirmed_measurement_unavailable_minutes"] == 120.0
    assert summary["confirmed_off_wrist_minutes"] == 0.0
    assert summary["unknown_physical_wear_state_minutes"] == 120.0
    assert summary["cause_unattributed_minutes"] == 120.0
    assert summary["unexplained_material_minutes"] == 0.0
    assert result["safety_contract"][
        "may_infer_physical_watch_removal_from_hr_gap"
    ] is False
    assert result["decision_use"]["low_stress_positive_reward_eligible"] is False


def test_hr_endpoint_without_matching_gap_does_not_explain_stress_sentinels():
    result = _analyze(snapshot=_with_heart_rate(_snapshot()))

    assert result["classification"]["label"] == "unexplained_internal_unavailability"
    assert result["attribution_summary"]["unexplained_material_minutes"] == 120.0
    assert result["attribution_summary"]["optical_hr_corroborated_run_count"] == 0
    run = result["material_run_attribution"][0]
    assert run["measurement_availability"]["signal"] == "garmin_all_day_stress"
    assert run["measurement_availability"]["confirmation"] == (
        "direct_garmin_stress_series"
    )
    assert run["optical_hr_measurement"]["state"] == "available"
    assert run["optical_hr_measurement"][
        "measured_sample_count_inside_stress_gap"
    ] > 0


def test_matching_hr_gap_and_later_stress_only_gap_remain_separate():
    result = _analyze(
        snapshot=_with_heart_rate(
            _snapshot(
                sentinel_windows=[("08:00", "10:00"), ("16:00", "16:30")]
            ),
            unavailable_windows=[("08:00", "10:00")],
        )
    )

    assert result["classification"]["label"] == (
        "partially_confirmed_optical_hr_with_unexplained_internal_unavailability"
    )
    assert result["attribution_summary"]["confirmed_measurement_unavailable_minutes"] == 120.0
    assert result["attribution_summary"]["unexplained_material_minutes"] == 30.0
    assert result["attribution_summary"]["stress_only_run_count_with_hr_available"] == 1
    assert result["material_run_attribution"][0]["optical_hr_measurement"][
        "state"
    ] == "unavailable"
    assert result["material_run_attribution"][1]["optical_hr_measurement"][
        "state"
    ] == "available"


def test_partial_hr_overlap_cannot_override_valid_hr_inside_stress_gap():
    result = _analyze(
        snapshot=_with_heart_rate(
            _snapshot(),
            unavailable_windows=[("08:04", "10:00")],
        )
    )

    assert result["classification"]["label"] == (
        "partially_confirmed_optical_hr_with_unexplained_internal_unavailability"
    )
    run = result["material_run_attribution"][0]
    assert run["attribution"] == (
        "partially_corroborated_optical_hr_with_unexplained_remainder"
    )
    assert run["measurement_availability"]["state"] == "mixed"
    assert run["optical_hr_measurement"]["state"] == "mixed"
    assert run["optical_hr_measurement"][
        "measured_sample_count_inside_stress_gap"
    ] == 2
    assert run["measurement_confirmed_minutes"] == 116.0
    assert run["unexplained_minutes"] == 4.0
    assert result["attribution_summary"]["mixed_optical_hr_run_count"] == 1


def test_wrong_date_hr_payload_cannot_confirm_measurement_unavailability():
    result = _analyze(
        snapshot=_with_heart_rate(
            _snapshot(),
            unavailable_windows=[("08:00", "10:00")],
            payload_date="2026-07-18",
        )
    )

    assert result["classification"]["label"] == "unexplained_internal_unavailability"
    heart_rate = result["observed_coverage"]["optical_heart_rate"]
    assert heart_rate["endpoint_usable"] is False
    assert heart_rate["sample_count"] == 0


def test_optical_hr_surface_redacts_profile_identifier_and_uses_gmt_cutoff():
    result = _analyze(
        snapshot=_with_heart_rate(
            _snapshot(),
            unavailable_windows=[("08:00", "10:00")],
        )
    )
    heart_rate = result["observed_coverage"]["optical_heart_rate"]

    assert heart_rate["endpoint_cutoff_local"] == "2026-07-19T20:00+08:00"
    assert "must_not_surface" not in json.dumps(result)


def test_declared_hr_prefix_gap_is_detected_without_claiming_physical_removal():
    result = _analyze(
        snapshot=_with_heart_rate(
            _snapshot(
                start="00:00",
                sentinel_start="00:00",
                sentinel_end="06:00",
            ),
            start="06:00",
        )
    )

    assert result["classification"]["label"] == (
        "confirmed_optical_hr_measurement_unavailability"
    )
    hr_run = result["observed_coverage"]["optical_heart_rate"][
        "material_unavailable_runs"
    ][0]
    assert hr_run["start_local"] == "2026-07-19T00:00+08:00"
    assert hr_run["start_boundary_observed"] is False
    assert hr_run["end_boundary_observed"] is True
    attribution = result["material_run_attribution"][0]
    assert attribution["measurement_availability"]["boundary_basis"] == (
        "open_endpoint_prefix_until_heart_rate_start"
    )
    assert attribution["device_wear_state"]["state"] == "unknown"


def test_complete_dense_target_date_series_can_earn_low_stress_coverage_credit():
    result = _analyze(
        snapshot=_snapshot(
            start="00:00",
            end="20:00",
            sentinel_start="23:00",
            sentinel_end="23:30",
        )
    )

    coverage = result["observed_coverage"]["stress"]["positive_use_coverage"]
    assert result["classification"]["label"] == "no_material_internal_unavailability"
    assert coverage["series_sufficient_for_low_stress_reward"] is True
    assert coverage["valid_density_pct"] == 100.0
    assert result["decision_use"]["low_stress_positive_reward_eligible"] is True


def test_sparse_or_truncated_series_cannot_earn_low_stress_credit():
    sparse = _analyze(
        snapshot=_snapshot(
            start="20:00",
            end="20:03",
            sentinel_start="23:00",
            sentinel_end="23:30",
        )
    )
    trailing_snapshot = _snapshot(
        start="00:00",
        end="16:00",
        sentinel_start="23:00",
        sentinel_end="23:30",
    )
    trailing_snapshot["payloads"][0]["data"]["endTimestampLocal"] = (
        "2026-07-19T20:00:00+08:00"
    )
    trailing = _analyze(snapshot=trailing_snapshot)

    assert sparse["classification"]["label"] == "insufficient_series_coverage"
    assert sparse["decision_use"]["low_stress_positive_reward_eligible"] is False
    trailing_coverage = trailing["observed_coverage"]["stress"][
        "positive_use_coverage"
    ]
    assert trailing["classification"]["label"] == "insufficient_series_coverage"
    assert trailing_coverage["end_boundary_complete"] is False
    assert trailing_coverage["tail_gap_minutes"] >= 230


def test_alternating_sentinels_fail_valid_density_without_inventing_material_run():
    snapshot = _snapshot(
        start="00:00",
        end="20:00",
        sentinel_start="23:00",
        sentinel_end="23:30",
    )
    stress = snapshot["payloads"][0]["data"]["stressValuesArray"]
    for index, row in enumerate(stress):
        if index % 2:
            row[1] = -1

    result = _analyze(snapshot=snapshot)
    stress_coverage = result["observed_coverage"]["stress"]

    assert stress_coverage["material_unavailable_run_count"] == 0
    assert stress_coverage["positive_use_coverage"]["valid_density_pct"] < 60
    assert result["classification"]["label"] == "insufficient_series_coverage"
    assert result["decision_use"]["low_stress_positive_reward_eligible"] is False


def test_wrong_date_future_and_malformed_samples_fail_closed():
    wrong_date = _snapshot(
        start="00:00",
        end="20:00",
        sentinel_start="23:00",
        sentinel_end="23:30",
    )
    for row in wrong_date["payloads"][0]["data"]["stressValuesArray"]:
        row[0] += 24 * 60 * 60 * 1000
    wrong_result = _analyze(snapshot=wrong_date)

    future = _snapshot(
        start="19:00",
        end="20:00",
        sentinel_start="23:00",
        sentinel_end="23:30",
    )
    future_result = _analyze(
        snapshot=future,
        as_of=datetime(2026, 7, 19, 18, 0, tzinfo=TZ),
    )

    malformed = _snapshot()
    malformed["payloads"][0]["data"]["stressValuesArray"] = [["bad"]]
    malformed_result = _analyze(snapshot=malformed)

    assert wrong_result["classification"]["label"] == "endpoint_unavailable"
    assert wrong_result["observed_coverage"]["stress"][
        "discarded_outside_target_date_count"
    ] > 0
    assert future_result["classification"]["label"] == "endpoint_unavailable"
    assert future_result["observed_coverage"]["stress"][
        "discarded_after_cutoff_count"
    ] > 0
    assert malformed_result["classification"]["label"] == "endpoint_unavailable"
    assert malformed_result["observed_coverage"]["stress"]["malformed_sample_count"] == 1


def test_recurring_sunday_pattern_is_context_not_dated_confirmation():
    result = _analyze(context=_context(recurring=True))

    assert (
        result["classification"]["label"]
        == "unexplained_internal_unavailability_with_recurring_context"
    )
    assert result["athlete_reported_windows"] == []
    assert result["recurring_context"][0]["occurrence_confirmed_for_date"] is False
    assert result["recurring_context"][0]["exact_timing_known"] is False
    assert result["attribution_summary"]["unattributed_run_count"] == 1
    serialized = json.dumps(result)
    assert "Private raw description" not in serialized


def test_exact_confirmed_window_explains_only_its_interval_without_imputation():
    result = _analyze(
        feedback={
            "date": TARGET.isoformat(),
            "wearable_context": {
                "off_wrist_windows": [
                    {
                        "status": "confirmed",
                        "start_local": "08:00",
                        "end_local": "10:00",
                        "reason": "Private church location and personal note",
                        "replacement_device": "dress_watch",
                    }
                ]
            },
        }
    )

    assert result["classification"]["label"] == "confirmed_intentional_off_wrist"
    assert result["sensor_alignment"][0]["sensor_agreement"] == (
        "consistent_with_unavailable_series"
    )
    assert result["athlete_reported_windows"][0]["reason_category"] == "other_reported"
    assert result["observed_coverage"]["stress"]["sample_count"] == 281
    assert result["attribution_summary"]["unexplained_material_minutes"] == 0.0
    assert "Private church location" not in json.dumps(result)


def test_valid_samples_inside_reported_off_wrist_window_are_preserved_and_discordant():
    baseline = _analyze(snapshot=_snapshot(sentinel_start="11:00", sentinel_end="12:00"))
    result = _analyze(
        snapshot=_snapshot(sentinel_start="11:00", sentinel_end="12:00"),
        feedback={
            "date": TARGET.isoformat(),
            "wearable_off_window": {
                "status": "completed",
                "start_local": "08:00",
                "end_local": "09:00",
                "reason_category": "dress_watch",
            },
        },
    )

    assert result["classification"]["label"] == "discordant"
    assert result["sensor_alignment"][0]["valid_stress_samples_inside"] == 20
    assert result["observed_coverage"]["stress"]["sample_count"] == baseline[
        "observed_coverage"
    ]["stress"]["sample_count"]


def test_wrong_date_missing_end_and_future_window_cannot_explain_gap():
    result = _analyze(
        feedback={
            "date": TARGET.isoformat(),
            "wearable_off_windows": [
                {
                    "date": "2026-07-18",
                    "status": "confirmed",
                    "start_local": "08:00",
                    "end_local": "10:00",
                },
                {
                    "status": "confirmed",
                    "start_local": "08:00",
                },
                {
                    "status": "confirmed",
                    "start_local": "21:00",
                    "end_local": "22:00",
                },
            ],
        }
    )

    statuses = [item["status"] for item in result["athlete_reported_windows"]]
    assert statuses == [
        "ignored_wrong_date",
        "reported_without_complete_timing",
        "future_relative_to_wall_clock",
    ]
    assert result["classification"]["label"] == "unexplained_internal_unavailability"


def test_malformed_outer_feedback_date_cannot_create_target_date_confirmation():
    result = _analyze(
        feedback={
            "date": "not-a-date",
            "wearable_off_window": {
                "status": "completed",
                "start_local": "08:00",
                "end_local": "10:00",
            },
        }
    )

    assert result["athlete_reported_windows"] == []
    assert result["classification"]["label"] == "unexplained_internal_unavailability"


def test_exact_window_cannot_attribute_a_second_unmatched_material_run():
    result = _analyze(
        snapshot=_snapshot(
            sentinel_windows=[("08:00", "10:00"), ("16:00", "16:30")]
        ),
        feedback={
            "date": TARGET.isoformat(),
            "wearable_off_window": {
                "status": "completed",
                "start_local": "08:00",
                "end_local": "10:00",
                "reason_category": "church_dress_watch",
            },
        },
    )

    assert result["classification"]["label"] == (
        "partially_confirmed_with_unexplained_internal_unavailability"
    )
    assert result["attribution_summary"]["fully_attributed_run_count"] == 1
    assert result["attribution_summary"]["unattributed_run_count"] == 1
    assert result["attribution_summary"]["unexplained_material_minutes"] == 30.0


def test_recurring_context_does_not_attribute_either_of_two_material_runs():
    result = _analyze(
        snapshot=_snapshot(
            sentinel_windows=[("08:00", "10:00"), ("16:00", "16:30")]
        ),
        context=_context(recurring=True),
    )

    assert result["classification"]["label"] == (
        "unexplained_internal_unavailability_with_recurring_context"
    )
    assert result["attribution_summary"]["unattributed_run_count"] == 2
    assert result["attribution_summary"]["confirmed_overlap_minutes"] == 0.0


def test_confirmed_boundary_window_with_no_internal_gap_withholds_low_stress_reward():
    result = _analyze(
        snapshot=_snapshot(sentinel_start="05:00", sentinel_end="05:30"),
        feedback={
            "date": TARGET.isoformat(),
            "wearable_off_window": {
                "status": "completed",
                "start_local": "05:00",
                "end_local": "06:00",
            },
        },
    )

    assert result["classification"]["label"] == "confirmed_intentional_off_wrist"
    assert result["observed_coverage"]["stress"]["material_unavailable_run_count"] == 0
    assert result["decision_use"]["low_stress_positive_reward_eligible"] is False


def test_discordant_window_without_internal_gap_withholds_low_stress_reward():
    result = _analyze(
        snapshot=_snapshot(sentinel_start="05:00", sentinel_end="05:30"),
        feedback={
            "date": TARGET.isoformat(),
            "wearable_off_window": {
                "status": "completed",
                "start_local": "08:00",
                "end_local": "09:00",
            },
        },
    )

    assert result["classification"]["label"] == "discordant"
    assert result["decision_use"]["low_stress_positive_reward_eligible"] is False


def test_adjacent_short_confirmed_windows_form_material_union():
    result = _analyze(
        snapshot=_snapshot(sentinel_start="05:00", sentinel_end="05:30"),
        feedback={
            "date": TARGET.isoformat(),
            "off_wrist_windows": [
                {"status": "completed", "start_local": "08:00", "end_local": "08:10"},
                {"status": "completed", "start_local": "08:10", "end_local": "08:20"},
            ],
        },
    )

    assert result["athlete_reported_window_summary"]["union_duration_minutes"] == 20.0
    assert result["decision_use"]["low_stress_positive_reward_eligible"] is False


def test_completed_window_with_future_end_is_not_accepted():
    result = _analyze(
        feedback={
            "date": TARGET.isoformat(),
            "wearable_off_window": {
                "status": "completed",
                "start_local": "16:00",
                "end_local": "23:00",
            },
        },
        as_of=datetime(2026, 7, 19, 17, 0, tzinfo=TZ),
    )

    assert result["athlete_reported_windows"][0]["status"] == (
        "future_relative_to_wall_clock"
    )
    assert result["classification"]["label"] == "unexplained_internal_unavailability"


def test_confirmed_window_on_future_target_date_is_not_accepted():
    future = date(2026, 7, 20)
    result = analyze_wearable_coverage(
        target_date=future,
        wellness_snapshot={},
        feedback={
            "date": future.isoformat(),
            "wearable_off_window": {
                "status": "completed",
                "start_local": "09:00",
                "end_local": "12:00",
            },
        },
        context=_context(),
        as_of=datetime(2026, 7, 19, 20, 30, tzinfo=TZ),
    )

    assert result["athlete_reported_windows"][0]["status"] == (
        "future_relative_to_wall_clock"
    )
    assert result["athlete_reported_window_summary"]["confirmed_valid_window_count"] == 0


def test_overlapping_confirmed_windows_are_deduplicated_by_union():
    result = _analyze(
        feedback={
            "date": TARGET.isoformat(),
            "off_wrist_windows": [
                {
                    "status": "confirmed",
                    "start_local": "08:00",
                    "end_local": "09:30",
                },
                {
                    "status": "confirmed",
                    "start_local": "09:00",
                    "end_local": "10:00",
                },
            ],
        }
    )

    summary = result["athlete_reported_window_summary"]
    assert summary["confirmed_valid_window_count"] == 2
    assert summary["raw_duration_minutes"] == 150.0
    assert summary["union_duration_minutes"] == 120.0
    assert summary["overlap_deduplicated_minutes"] == 30.0


def test_endpoint_failure_remains_failure_and_manual_context_does_not_create_data():
    result = _analyze(
        snapshot={
            "date": TARGET.isoformat(),
            "payloads": [
                {
                    "label": "get_all_day_stress",
                    "ok": False,
                    "status": "failed",
                    "error": "temporary",
                }
            ],
        },
        feedback={
            "date": TARGET.isoformat(),
            "wearable_off_window": {
                "status": "confirmed",
                "start_local": "08:00",
                "end_local": "10:00",
            },
        },
    )

    assert result["status"] == "insufficient_evidence"
    assert result["classification"]["label"] == "endpoint_unavailable"
    assert result["provenance"]["endpoint_status"] == "failed"
    assert result["decision_use"]["low_stress_positive_reward_eligible"] is False
    assert result["safety_contract"]["endpoint_failure_remains_endpoint_failure"] is True


def test_failed_record_with_residual_nonempty_data_remains_unavailable():
    snapshot = _snapshot()
    snapshot["payloads"][0]["ok"] = False
    snapshot["payloads"][0]["status"] = "failed"

    result = _analyze(snapshot=snapshot)

    assert result["status"] == "insufficient_evidence"
    assert result["classification"]["label"] == "endpoint_unavailable"
    assert result["decision_use"]["low_stress_positive_reward_eligible"] is False


def test_wrong_outer_snapshot_date_rejects_target_dated_nested_payload():
    snapshot = _snapshot()
    snapshot["date"] = "2026-07-18"

    result = _analyze(snapshot=snapshot)

    assert result["status"] == "insufficient_evidence"
    assert result["classification"]["label"] == "endpoint_unavailable"
    assert result["provenance"]["wellness_snapshot_date_matches_target"] is False


def test_retained_success_exposes_failed_attempt_without_copying_raw_attempt_data():
    snapshot = _snapshot()
    record = snapshot["payloads"][0]
    record["retention_policy"] = "preserve_last_nonempty_success"
    record["latest_attempt"] = {
        "label": "get_all_day_stress",
        "status": "failed",
        "ok": False,
        "attempted_at": "2026-07-19T20:05:00+08:00",
        "error": "C:/private/profile-991?url=https://secret.example response_date_mismatch",
        "expected_date": "2026-07-19",
        "response_date": "2026-07-18",
        "data": {
            "userProfilePK": "private-profile-id",
            "stressValuesArray": [[123, 45]],
        },
    }

    result = _analyze(snapshot=snapshot)

    assert result["status"] == "available"
    assert result["provenance"]["latest_attempt"]["status"] == "failed"
    assert result["provenance"]["retained_after_degraded_attempt"] is True
    assert "data" not in result["provenance"]["latest_attempt"]
    serialized = json.dumps(result)
    assert "private-profile-id" not in serialized
    assert "stressValuesArray" not in serialized
    assert "profile-991" not in serialized
    assert "secret.example" not in serialized
    assert result["provenance"]["latest_attempt"]["error_category"] == (
        "connector_error_redacted"
    )


def test_top_level_endpoint_provenance_scalars_are_bounded():
    snapshot = _snapshot()
    record = snapshot["payloads"][0]
    record["status"] = "C:/private/profile-772"
    record["last_success_at"] = "https://secret.example/profile-772"
    record["retention_policy"] = "profile-772-local-path"

    result = _analyze(snapshot=snapshot)
    serialized = json.dumps(result)

    assert result["classification"]["label"] == "endpoint_unavailable"
    assert result["provenance"]["endpoint_status"] == "unknown"
    assert result["provenance"]["last_success_at"] is None
    assert result["provenance"]["retention_policy"] == "unknown"
    assert "profile-772" not in serialized
    assert "secret.example" not in serialized


def test_builder_writes_current_and_dated_artifacts(tmp_path):
    context = load_context(tmp_path)
    context["athlete"]["wearable_context"] = _context(recurring=True)["athlete"][
        "wearable_context"
    ]
    write_json(tmp_path / "config" / "athlete_context.json", context)
    write_json(
        tmp_path / "snapshots" / f"garmin_wellness_{TARGET.isoformat()}.json",
        _snapshot(),
    )

    result = build_wearable_coverage(tmp_path, TARGET)

    assert result["date"] == TARGET.isoformat()
    assert (tmp_path / "snapshots" / "wearable_coverage.json").exists()
    assert (
        tmp_path / "snapshots" / f"wearable_coverage_{TARGET.isoformat()}.json"
    ).exists()


def test_wearable_coverage_cli(tmp_path, capsys):
    load_context(tmp_path)
    write_json(
        tmp_path / "snapshots" / f"garmin_wellness_{TARGET.isoformat()}.json",
        _snapshot(),
    )

    code = main(
        [
            "wearable-coverage",
            "--root",
            str(tmp_path),
            "--date",
            TARGET.isoformat(),
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["artifact_type"] == "wearable_coverage"
