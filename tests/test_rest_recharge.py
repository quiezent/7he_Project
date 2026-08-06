from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from coach_sync.coach_packet import build_coach_packet
from coach_sync.io import write_json
from coach_sync.rest_recharge import analyze_rest_recharge_window, build_rest_recharge_window


KL = ZoneInfo("Asia/Kuala_Lumpur")
DAY = date(2026, 7, 17)


def _ms(hour: int, minute: int) -> int:
    return int(datetime(2026, 7, 17, hour, minute, tzinfo=KL).timestamp() * 1000)


def _rows(values: dict[str, float], body_battery: bool = False) -> list[list]:
    rows = []
    for stamp, value in values.items():
        hour, minute = (int(item) for item in stamp.split(":"))
        rows.append(
            [_ms(hour, minute), "MEASURED", value, 2.0]
            if body_battery
            else [_ms(hour, minute), value]
        )
    return rows


def _three_minute_values(start: str, count: int, value: float) -> dict[str, float]:
    hour, minute = (int(item) for item in start.split(":"))
    current = datetime(2026, 7, 17, hour, minute, tzinfo=KL)
    out = {}
    for _ in range(count):
        out[current.strftime("%H:%M")] = value
        current += timedelta(minutes=3)
    return out


def _snapshot(
    stress_values: dict[str, float] | None = None,
    battery_values: dict[str, float] | None = None,
    nap_seconds: int = 0,
) -> dict:
    all_day = {
        "calendarDate": DAY.isoformat(),
        "endTimestampLocal": "2026-07-17T12:30:00.0",
        "stressValueDescriptorsDTOList": [
            {"index": 0, "key": "timestamp"},
            {"index": 1, "key": "stressLevel"},
        ],
        "stressValuesArray": _rows(stress_values or {}),
        "bodyBatteryValueDescriptorsDTOList": [
            {"bodyBatteryValueDescriptorIndex": 0, "bodyBatteryValueDescriptorKey": "timestamp"},
            {"bodyBatteryValueDescriptorIndex": 1, "bodyBatteryValueDescriptorKey": "bodyBatteryStatus"},
            {"bodyBatteryValueDescriptorIndex": 2, "bodyBatteryValueDescriptorKey": "bodyBatteryLevel"},
            {"bodyBatteryValueDescriptorIndex": 3, "bodyBatteryValueDescriptorKey": "bodyBatteryVersion"},
        ],
        "bodyBatteryValuesArray": _rows(battery_values or {}, body_battery=True),
    }
    return {
        "date": DAY.isoformat(),
        "fetched_at": "2026-07-17T12:31:00+08:00",
        "privacy": "raw_private_local_only",
        "payloads": [
            {
                "label": "get_all_day_stress",
                "ok": True,
                "status": "success",
                "attempted_at": "2026-07-17T12:31:00+08:00",
                "data": all_day,
            },
            {
                "label": "get_sleep_data",
                "ok": True,
                "status": "success",
                "data": {"dailySleepDTO": {"calendarDate": DAY.isoformat(), "napTimeSeconds": nap_seconds}},
            },
            {"label": "get_body_battery_events", "ok": True, "status": "success_empty", "data": []},
        ],
    }


def _feedback(*, clarity=(7, 8), inertia=10) -> dict:
    review = {
        "nap_status": "completed",
        "nap_source": "athlete_report",
        "nap_start_local": "10:20",
        "nap_end_local": "11:50",
        "time_in_bed_minutes": 90,
        "estimated_sleep_minutes": 75,
    }
    if clarity is not None:
        review["post_nap_clarity_10_range"] = list(clarity)
    if inertia is not None:
        review["sleep_inertia_minutes"] = inertia
    return {"date": DAY.isoformat(), "sleep_work_timing_review": review}


def _analyze(snapshot: dict, feedback: dict, activities=None) -> dict:
    return analyze_rest_recharge_window(
        target_date=DAY,
        timezone_name="Asia/Kuala_Lumpur",
        wellness_snapshot=snapshot,
        feedback=feedback,
        normalized_wellness={"primary_sleep_hours": 5.53},
        wellness_history=[{"date": DAY.isoformat(), "primary_sleep_hours": 5.53}],
        activity_rows=activities or [],
    )


def test_descriptor_aware_window_classifies_today_like_nap_as_restorative():
    stress = _three_minute_values("10:00", 47, 19)
    stress["10:06"] = -1  # Garmin sentinel is excluded, not assigned a meaning.
    battery = _three_minute_values("10:00", 47, 65)
    battery.update({stamp: 67 for stamp in _three_minute_values("11:00", 3, 0)})
    battery.update({stamp: 68 for stamp in _three_minute_values("11:09", 4, 0)})
    battery.update({stamp: 69 for stamp in _three_minute_values("11:21", 4, 0)})
    battery.update({stamp: 70 for stamp in _three_minute_values("11:33", 8, 0)})
    battery.update({stamp: 71 for stamp in _three_minute_values("11:57", 12, 0)})
    activities = [
        {
            "id": "mtb-1",
            "date": "2026-07-16",
            "start_time_local": "2026-07-16T11:00:00+08:00",
            "end_time_local": "2026-07-16T12:50:00+08:00",
            "category": "mtb",
            "counts_for_training_load": True,
            "duration_min": 110,
            "training_load": 219.3,
            "hr_zone_min": {"z4": 19.1, "z5": 9.2},
        },
        {
            "id": "future",
            "date": DAY.isoformat(),
            "start_time_local": "2026-07-17T13:00:00+08:00",
            "end_time_local": "2026-07-17T14:00:00+08:00",
            "category": "bike_indoor",
            "counts_for_training_load": True,
            "duration_min": 60,
            "training_load": 100,
        },
    ]

    result = _analyze(_snapshot(stress, battery), _feedback(), activities)

    assert result["classification"]["label"] == "restorative"
    assert result["context"]["sleep_opportunity_debt_proxy"]["observed_days"] == 1
    assert result["context"]["sleep_opportunity_debt_proxy"][
        "cumulative_shortfall_hours"
    ] == 0.97
    assert result["classification"]["confidence"] == "high"
    assert result["objective_response"]["stress"]["during"]["mean"] == 19
    assert result["objective_response"]["stress"]["during"]["rest_range_pct_le_25"] == 100
    assert result["objective_response"]["stress"]["negative_sentinel_samples_excluded"] == 1
    assert result["objective_response"]["body_battery"]["delta_during"] == 5
    assert result["objective_response"]["body_battery"]["recharge_onset"]["latency_minutes"] == 40
    assert result["garmin_nap_evidence"]["nap_time_status"] == "reported_zero_non_exclusionary"
    assert result["context"]["preceding_48h_load"]["training_load"] == 219.3
    assert result["context"]["preceding_48h_load"]["session_count"] == 1
    assert result["safety_contract"]["may_authorize_high_consequence"] is False


def test_body_battery_gain_alone_is_insufficient_and_cannot_promote():
    battery = _three_minute_values("10:00", 47, 60)
    battery.update({stamp: 70 for stamp in _three_minute_values("11:00", 30, 0)})

    result = _analyze(_snapshot({}, battery), _feedback(clarity=None, inertia=None))

    assert result["classification"]["signals"]["body_battery_recharge"] is True
    assert result["classification"]["label"] == "insufficient_evidence"
    assert result["safety_contract"]["body_battery_only_promotion_allowed"] is False


def test_wrong_date_wellness_cannot_satisfy_rest_window_evidence():
    snapshot = _snapshot(
        _three_minute_values("10:20", 30, 18),
        {"10:20": 65, "11:47": 70},
    )
    snapshot["date"] = "2026-07-16"

    result = _analyze(snapshot, _feedback())

    assert result["status"] == "insufficient_evidence"
    assert result["classification"]["label"] == "insufficient_evidence"
    assert result["provenance"]["endpoint_data_status"] == "wrong_date"
    assert result["garmin_nap_evidence"]["nap_time_status"] == "missing_non_exclusionary"
    assert result["safety_contract"]["upward_training_clearance"] == "none"


def test_wrong_nested_endpoint_date_cannot_satisfy_rest_window_evidence():
    snapshot = _snapshot(
        _three_minute_values("10:20", 30, 18),
        {"10:20": 65, "11:47": 70},
    )
    snapshot["payloads"][0]["data"]["calendarDate"] = "2026-07-18"

    result = _analyze(snapshot, _feedback())

    assert result["status"] == "insufficient_evidence"
    assert result["classification"]["label"] == "insufficient_evidence"
    assert result["provenance"]["endpoint_data_status"] == "response_date_mismatch"


def test_retained_last_success_and_latest_failure_are_both_provenanced():
    snapshot = _snapshot(
        _three_minute_values("10:00", 47, 18),
        {
            **_three_minute_values("10:00", 20, 65),
            **_three_minute_values("11:00", 30, 70),
        },
    )
    endpoint = snapshot["payloads"][0]
    endpoint.update(
        {
            "last_attempt_status": "failed",
            "last_success_at": "2026-07-17T12:00:00+08:00",
            "retention_policy": "preserve_last_nonempty_success",
            "latest_attempt": {
                "status": "failed",
                "attempted_at": "2026-07-17T12:30:00+08:00",
                "error": "temporary Garmin failure",
            },
        }
    )

    result = _analyze(snapshot, _feedback())

    assert result["classification"]["label"] == "restorative"
    assert result["provenance"]["endpoint_last_attempt_status"] == "failed"
    assert result["provenance"]["endpoint_last_success_at"] == (
        "2026-07-17T12:00:00+08:00"
    )
    assert result["provenance"]["endpoint_data_retained_after_degraded_attempt"] is True


def test_preceding_load_uses_elapsed_duration_before_active_duration(tmp_path):
    for activity_id, start, training_load in (
        (1, "2026-07-17 07:00:00", 50),
        (2, "2026-07-17 09:00:00", 100),
    ):
        write_json(
            tmp_path / "activities" / f"{activity_id}.json",
            {
                "activityId": activity_id,
                "activityType": {"typeKey": "indoor_cycling"},
                "startTimeLocal": start,
                "duration": 3600,
                "elapsedDuration": 7200,
                "activityTrainingLoad": training_load,
            },
        )

    result = build_rest_recharge_window(
        tmp_path,
        DAY,
        wellness_snapshot=_snapshot(
            _three_minute_values("10:00", 47, 18),
            _three_minute_values("10:00", 47, 65),
        ),
        feedback=_feedback(clarity=(6, 6), inertia=10),
        normalized_wellness={"date": DAY.isoformat(), "primary_sleep_hours": 5.53},
        wellness_history=[],
    )

    load = result["context"]["preceding_48h_load"]
    assert load["session_count"] == 1
    assert load["training_load"] == 50
    assert load["sessions"][0]["end_time_local"] == "2026-07-17T09:00+08:00"
    assert load["sessions"][0]["end_time_derivation"] == "start_plus_elapsed_duration"


def test_wrong_date_feedback_is_ignored_by_artifact_builder(tmp_path):
    feedback = _feedback()
    feedback["date"] = "2026-07-16"

    result = build_rest_recharge_window(
        tmp_path,
        DAY,
        wellness_snapshot=_snapshot(
            _three_minute_values("10:20", 30, 18),
            {"10:20": 65, "11:47": 70},
        ),
        feedback=feedback,
        normalized_wellness={"date": DAY.isoformat(), "primary_sleep_hours": 5.53},
        wellness_history=[],
        activity_rows=[],
    )

    assert result["classification"]["label"] == "insufficient_evidence"
    assert result["window"]["status"] == "missing_completed_manual_window"
    assert (tmp_path / "snapshots" / "rest_recharge_window.json").exists()


def test_wrong_date_embedded_review_is_ignored_even_with_hhmm_timing():
    feedback = _feedback()
    feedback["sleep_work_timing_review"]["date"] = "2026-07-16"

    result = _analyze(
        _snapshot(_three_minute_values("10:20", 30, 18), {"10:20": 65, "11:47": 70}),
        feedback,
    )

    assert result["classification"]["label"] == "insufficient_evidence"
    assert result["window"]["status"] == "missing_completed_manual_window"


def test_untimed_legacy_nap_report_remains_unclassified():
    feedback = {"date": DAY.isoformat(), "notes": "Nap done and felt clearer afterward."}

    result = _analyze(_snapshot({}, {}), feedback)

    assert result["classification"]["label"] == "insufficient_evidence"
    assert result["window"]["legacy_manual_report_present"] is True


def test_missing_garmin_nap_fields_do_not_disprove_valid_manual_nap():
    snapshot = _snapshot(
        _three_minute_values("10:00", 47, 18),
        {
            **_three_minute_values("10:00", 20, 65),
            **_three_minute_values("11:00", 30, 70),
        },
    )
    snapshot["payloads"] = [snapshot["payloads"][0]]

    result = _analyze(snapshot, _feedback())

    assert result["classification"]["label"] == "restorative"
    assert result["garmin_nap_evidence"]["nap_time_status"] == "missing_non_exclusionary"


def test_conflicting_illness_reports_resolve_conservatively():
    feedback = _feedback()
    feedback["sleep_work_timing_review"]["illness_status"] = "active"
    feedback["health_status"] = "healthy"

    result = _analyze(
        _snapshot(_three_minute_values("10:00", 47, 18), _three_minute_values("10:00", 47, 65)),
        feedback,
    )

    illness = result["context"]["illness"]
    assert illness["status"] == "active"
    assert illness["conflict"] is True
    assert illness["resolution_rule"].startswith("conservative_precedence")


def test_quiet_without_recharge_or_subjective_benefit_is_non_restorative():
    stress = _three_minute_values("10:00", 47, 18)
    battery = _three_minute_values("10:00", 47, 65)

    result = _analyze(_snapshot(stress, battery), _feedback(clarity=(6, 6), inertia=10))

    assert result["classification"]["label"] == "quiet_but_non_restorative"


def test_body_battery_points_must_pair_near_window_boundaries():
    stress = _three_minute_values("10:00", 47, 18)
    battery = {"10:50": 65, "11:00": 70}

    result = _analyze(_snapshot(stress, battery), _feedback(clarity=None, inertia=None))

    during = result["objective_response"]["body_battery"]["during"]
    assert during["boundary_pair_valid"] is False
    assert result["classification"]["signals"]["body_battery_recharge"] is False
    assert result["classification"]["label"] == "insufficient_evidence"


def test_objective_recharge_with_poor_clarity_is_discordant():
    stress = _three_minute_values("10:00", 47, 18)
    battery = _three_minute_values("10:00", 47, 65)
    battery.update({stamp: 70 for stamp in _three_minute_values("11:00", 30, 0)})

    result = _analyze(_snapshot(stress, battery), _feedback(clarity=(4, 4), inertia=50))

    assert result["classification"]["label"] == "discordant"


def test_quiet_physiology_with_poor_subjective_response_is_discordant():
    stress = _three_minute_values("10:00", 47, 18)
    battery = _three_minute_values("10:00", 47, 65)

    result = _analyze(_snapshot(stress, battery), _feedback(clarity=(3, 4), inertia=50))

    assert result["classification"]["signals"]["quiet_stress"] is True
    assert result["classification"]["signals"]["subjective_negative"] is True
    assert result["classification"]["label"] == "discordant"


def test_simultaneously_positive_and_negative_subjective_rules_are_discordant():
    feedback = _feedback(clarity=(4, 4), inertia=10)
    feedback["sleep_work_timing_review"]["pre_nap_clarity_10_range"] = [2, 2]

    result = _analyze(
        _snapshot(_three_minute_values("10:00", 47, 18), _three_minute_values("10:00", 47, 65)),
        feedback,
    )

    assert result["classification"]["signals"]["subjective_positive"] is True
    assert result["classification"]["signals"]["subjective_negative"] is True
    assert result["classification"]["label"] == "discordant"


def test_coach_packet_surfaces_window_without_changing_today_call(tmp_path):
    restorative = _analyze(
        _snapshot(
            _three_minute_values("10:00", 47, 18),
            {
                **_three_minute_values("10:00", 20, 65),
                **_three_minute_values("11:00", 30, 70),
            },
        ),
        _feedback(),
    )
    state = {
        "date": DAY.isoformat(),
        "readiness": {"readiness_level": "yellow", "readiness_score": 65, "confidence": "medium"},
        "data_freshness": {"status": "current", "activity_data": {"status": "current"}},
        "cns_readiness": {"status": "watch", "session_ceiling": {"level": "controlled_skill_only"}},
        "phase": {"name": "base_rebuild"},
        "wellness_trends": {"latest": {"primary_sleep_hours": 5.53}, "last_7": {}},
    }
    plan = {
        "date": DAY.isoformat(),
        "session": {"title": "Existing recovery", "type": "scheduled_recovery", "modality": "rest", "intensity": "rest"},
        "decision_inputs": {},
        "constraint_resolution": {"applied": []},
    }

    packet_without = build_coach_packet(tmp_path, DAY, state=deepcopy(state), plan=deepcopy(plan))
    state["rest_recharge_window"] = restorative
    packet_with = build_coach_packet(tmp_path, DAY, state=state, plan=plan)

    assert packet_with["today_call"]["stance"] == packet_without["today_call"]["stance"]
    assert packet_with["today_call"]["session"] == packet_without["today_call"]["session"]
    signal = next(item for item in packet_with["evidence"]["trusted"] if item["name"] == "Rest/recharge window")
    assert signal["status"] == "restorative"
    assert signal["decision_use"] == "intraday_recovery_context_only_never_session_clearance"
    assert signal["value"]["safety_contract"]["may_raise_cns_ceiling"] is False
