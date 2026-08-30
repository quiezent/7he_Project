from __future__ import annotations

from coach_sync.context import load_context
from coach_sync.io import read_json, write_json
from coach_sync.planning import SESSION_CONTRACT_FIELDS, build_today_plan


DAY = "2026-04-29"


def _state(day: str = DAY) -> dict:
    return {
        "date": day,
        "athlete": {},
        "phase": {"name": "base_rebuild"},
        "readiness": {
            "readiness_level": "green",
            "readiness_score": 85,
            "hard_session_guidance": "allow",
        },
        "data_freshness": {
            "status": "current",
            "hard_session_confidence": "normal",
            "hard_session_limiters": [],
        },
        "cns_readiness": {
            "status": "ready",
            "session_ceiling": {"level": "normal_training"},
        },
    }


def _planned_session(root, day: str = DAY, *, venue: str = "Bukit Kiara", modality: str = "mtb") -> None:
    write_json(
        root / "input" / f"planned_session_{day}.json",
        {
            "artifact_type": "coach_authored_planned_session",
            "date": day,
            "generated_at": f"{day}T06:00:00+08:00",
            "status": "active",
            "session": {
                "title": "Explicit outdoor quality",
                "type": "mtb_quality_skill" if modality == "mtb" else "outdoor_endurance",
                "modality": modality,
                "venue": venue,
                "action_identity": {
                    "venue_key": "bukit_kiara" if venue == "Bukit Kiara" else "other_venue"
                },
                "duration_min": 120,
                "intensity": "moderate_technical",
                "schema_version": 3,
                "contract_fields": SESSION_CONTRACT_FIELDS,
                "purpose": "Train a prolonged outdoor quality exposure.",
                "dose": {"duration_min": 120, "cap": "Two hours maximum."},
                "adaptation_hypothesis": "A bounded outdoor dose develops repeatability.",
                "execution_rules": ["Keep execution controlled."],
                "expected_result": {"technical": "Repeatable quality."},
                "stop_rules": ["Stop if execution degrades."],
                "post_session_review_fields": ["stop_rule_outcome"],
            },
        },
    )


def _environment(
    *,
    pm2_5: float,
    gate: str,
    freshness: str = "current",
    forecast_high: float | None = None,
    forecast_confidence: str = "low",
) -> dict:
    reason = f"Bukit Kiara PM2.5 is {pm2_5:.1f} ug/m3; environment gate is {gate}."
    return {
        "artifact_type": "environment_evidence_current",
        "date": DAY,
        "status": freshness,
        "source": {
            "endpoint": "http://192.168.80.147:8765/api/v1/mtb/environment-evidence",
            "fallback": "none",
        },
        "latest_attempt": {
            "attempted_at": f"{DAY}T08:00:00+08:00",
            "status": "success",
            "error": None,
            "evidence_id": "airgradient-86311-test-v1",
        },
        "last_known_good": {
            "identity": {
                "evidence_id": "airgradient-86311-test-v1",
                "schema_version": "1.0.0",
            },
            "observation": {
                "observed_at_utc": "2026-04-29T00:00:00Z",
                "pm2_5_ug_m3": pm2_5,
            },
            "provenance": {
                "sensor": {
                    "provider": "AirGradient",
                    "location_id": 86311,
                    "stale_after_seconds": 420,
                    "expired_after_seconds": 900,
                }
            },
            "scope": {
                "role": "direct_venue_environment_evidence",
                "venue_keys": ["bukit_kiara"],
                "location": {
                    "name": "Bukit Kiara / Taman Tun Dr Ismail",
                    "timezone": "Asia/Kuala_Lumpur",
                },
                "transfer_to_unlisted_venues": False,
            },
        },
        "freshness": {
            "state": freshness,
            "age_seconds": 120,
            "stale_after_seconds": 420,
            "expired_after_seconds": 900,
        },
        "decision": {
            "gate": gate,
            "severity": "red" if "close" in gate or "closure" in gate else "yellow",
            "reason": reason,
            "reason_codes": [gate],
            "current_pm2_5_ug_m3": pm2_5,
            "forecast_upper_pm2_5_ug_m3": forecast_high,
            "forecast_confidence": {
                "arrival": forecast_confidence,
                "on_trail": forecast_confidence,
            },
            "recheck_minutes": 15,
            "decision_role": "outdoor_downshift_or_hold_only_never_training_promotion",
            "can_promote_training": False,
            "venue_keys": ["bukit_kiara"],
            "venue_aliases": ["Bukit Kiara", "Kiara", "TTDI"],
        },
        "guardrail": "Environment can hold or downshift only.",
    }


def _forecast_window(
    name: str,
    target_date: str,
    *,
    thunderstorm: str = "none",
    rain_probability: int = 10,
    rain_mm: float = 0.0,
) -> dict:
    start_hour = 1 if name == "morning" else 6
    local_window = "09:00–13:00" if name == "morning" else "14:00–18:00"
    modeled = "09:00–11:00" if name == "morning" else "14:00–16:00"
    return {
        "target_day": "Tomorrow",
        "target_date": target_date,
        "ride_window": local_window,
        "modeled_session": modeled,
        "active": False,
        "current_conditions_applicable": False,
        "recheck": f"Recheck {target_date} closer to departure",
        "confidence": "Low · limited local history",
        "particle_forecast": {
            "available": True,
            "validation_state": "experimental_not_validated",
            "mean_range_pm2_5_ug_m3": {"low": 20.0, "high": 95.0},
            "upper_peak_pm2_5_ug_m3": 110.0,
        },
        "weather_forecast": {
            "available": True,
            "target_date": target_date,
            "start_at_utc": f"{target_date}T{start_hour:02d}:00:00Z",
            "end_at_utc": f"{target_date}T{start_hour + 2:02d}:00:00Z",
            "modeled_session": modeled,
            "precipitation_probability_max_pct": rain_probability,
            "precipitation_mm": rain_mm,
            "rain_used_for_comparison": False,
            "rain_signal": "Rain likely · context only" if rain_probability >= 70 else "Low rain context",
            "thunderstorm": {
                "level": thunderstorm,
                "rank": 2 if thunderstorm == "likely" else 0,
                "label": "Thunderstorm signal elevated" if thunderstorm == "likely" else "No thunderstorm signal",
                "basis": "Structured model evidence.",
                "source": "model",
                "used_for_decision": thunderstorm in {"likely", "severe"},
            },
        },
    }


def _with_forecast(
    environment: dict,
    target_date: str,
    *,
    morning_thunder: str = "none",
    afternoon_thunder: str = "none",
    preferred_window: str | None = "afternoon",
) -> dict:
    environment["last_known_good"]["ride_windows"] = {
        "comparison": {
            "status": "ready",
            "preferred_window": preferred_window,
            "relative_only": True,
            "ride_approval": False,
            "reason": "Relative comparison only.",
        },
        "morning": _forecast_window(
            "morning", target_date, thunderstorm=morning_thunder
        ),
        "afternoon": _forecast_window(
            "afternoon", target_date, thunderstorm=afternoon_thunder
        ),
    }
    return environment


def _assert_schema_v3(session: dict) -> None:
    assert session["schema_version"] == 3
    assert session["contract_fields"] == SESSION_CONTRACT_FIELDS
    for field in SESSION_CONTRACT_FIELDS:
        assert session.get(field), field


def test_fresh_moderate_alias_is_caution_only_and_never_promotes(tmp_path):
    load_context(tmp_path)
    _planned_session(tmp_path, venue="Kiara")
    state = _state()
    state["environment_evidence"] = _environment(
        pm2_5=38.0,
        gate="moderate_caution",
    )

    plan = build_today_plan(tmp_path, DAY, state=state)

    assert plan["session"]["title"] == "Explicit outdoor quality"
    assert not any(
        item.get("source") == "environment_evidence"
        for item in plan["constraint_resolution"]["applied"]
    )
    decision = plan["decision_inputs"]["environment_evidence"]["decision"]
    assert decision["gate"] == "moderate_caution"
    assert decision["can_promote_training"] is False


def test_moderate_or_low_confidence_forecast_crossing_only_holds_for_recheck(tmp_path):
    load_context(tmp_path)
    _planned_session(tmp_path)
    state = _state()
    state["environment_evidence"] = _environment(
        pm2_5=38.0,
        gate="hold_and_recheck",
        forecast_high=84.5,
        forecast_confidence="low",
    )

    plan = build_today_plan(tmp_path, DAY, state=state)

    assert plan["session"]["title"] == "Explicit outdoor quality"
    assert not any(
        item.get("source") == "environment_evidence"
        for item in plan["constraint_resolution"]["applied"]
    )
    environment = plan["decision_inputs"]["environment_evidence"]
    assert environment["decision"]["gate"] == "hold_and_recheck"
    assert environment["decision"]["can_promote_training"] is False
    assert environment["evidence"]["evidence_id"] == "airgradient-86311-test-v1"
    assert any("hold_and_recheck" in item for item in plan["guardrails"])


def test_fresh_poor_pm_replaces_explicit_kiara_mtb_with_established_indoor_fallback(tmp_path):
    load_context(tmp_path)
    _planned_session(tmp_path)
    state = _state()
    state["environment_evidence"] = _environment(
        pm2_5=51.0,
        gate="close_mtb_prolonged_endurance_high_ventilation",
    )

    plan = build_today_plan(tmp_path, DAY, state=state)

    assert plan["session"]["type"] == "environment_indoor_continuity"
    assert plan["session"]["modality"] == "bike_indoor"
    assert plan["session"]["duration_min"] == 60
    assert plan["session"]["dose"]["main"] == "40 minutes at 120-130 W / global RPE 2-3."
    _assert_schema_v3(plan["session"])
    applied = plan["constraint_resolution"]["applied"]
    environment = next(item for item in applied if item["source"] == "environment_evidence")
    assert environment["evidence_id"] == "airgradient-86311-test-v1"
    assert environment["freshness"]["state"] == "current"
    assert environment["current_pm2_5_ug_m3"] == 51.0


def test_hazardous_pm_closes_any_explicit_kiara_outdoor_session(tmp_path):
    load_context(tmp_path)
    _planned_session(tmp_path, modality="outdoor_hike")
    state = _state()
    state["environment_evidence"] = _environment(
        pm2_5=151.0,
        gate="close_all_planned_outdoor_exercise",
    )

    plan = build_today_plan(tmp_path, DAY, state=state)

    assert plan["session"]["type"] == "environment_indoor_continuity"
    assert any(
        item.get("source") == "environment_evidence"
        for item in plan["constraint_resolution"]["applied"]
    )


def test_environment_replacement_never_increases_the_written_duration(tmp_path):
    load_context(tmp_path)
    _planned_session(tmp_path)
    planned_path = tmp_path / "input" / f"planned_session_{DAY}.json"
    planned = read_json(planned_path)
    planned["session"]["duration_min"] = 30
    write_json(planned_path, planned)
    state = _state()
    state["environment_evidence"] = _environment(
        pm2_5=70.0,
        gate="close_mtb_prolonged_endurance_high_ventilation",
    )

    plan = build_today_plan(tmp_path, DAY, state=state)

    assert plan["session"]["type"] == "environment_indoor_continuity"
    assert plan["session"]["duration_min"] == 30
    assert plan["session"]["dose"]["main"].startswith("20 minutes")


def test_environment_proxy_is_venue_scoped_and_does_not_touch_another_venue(tmp_path):
    load_context(tmp_path)
    _planned_session(tmp_path, venue="Denai Peladang")
    state = _state()
    state["environment_evidence"] = _environment(
        pm2_5=151.0,
        gate="close_all_planned_outdoor_exercise",
    )

    plan = build_today_plan(tmp_path, DAY, state=state)

    assert plan["session"]["title"] == "Explicit outdoor quality"
    assert not any(
        item.get("source") == "environment_evidence"
        for item in plan["constraint_resolution"]["applied"]
    )


def test_stale_poor_retains_restriction_but_stale_low_only_holds_without_clearance(tmp_path):
    load_context(tmp_path)
    _planned_session(tmp_path)
    poor_state = _state()
    poor_state["environment_evidence"] = _environment(
        pm2_5=70.0,
        gate="retained_high_ventilation_closure_pending_refresh",
        freshness="stale",
    )

    poor_plan = build_today_plan(tmp_path, DAY, state=poor_state)
    assert poor_plan["session"]["type"] == "environment_indoor_continuity"

    low_state = _state()
    low_state["environment_evidence"] = _environment(
        pm2_5=20.0,
        gate="hold_pending_refresh",
        freshness="stale",
    )
    low_plan = build_today_plan(tmp_path, DAY, state=low_state)
    assert low_plan["session"]["title"] == "Explicit outdoor quality"
    decision = low_plan["decision_inputs"]["environment_evidence"]["decision"]
    assert decision["can_promote_training"] is False
    assert any("hold_pending_refresh" in item for item in low_plan["guardrails"])

    expired_state = _state()
    expired_state["environment_evidence"] = _environment(
        pm2_5=70.0,
        gate="environment_unknown_hold",
        freshness="expired",
    )
    expired_plan = build_today_plan(tmp_path, DAY, state=expired_state)
    assert expired_plan["session"]["title"] == "Explicit outdoor quality"
    assert not any(
        item.get("source") == "environment_evidence"
        for item in expired_plan["constraint_resolution"]["applied"]
    )


def test_environment_does_not_replace_stricter_cns_or_sabbath_resolution(tmp_path):
    load_context(tmp_path)
    _planned_session(tmp_path)
    cns_state = _state()
    cns_state["cns_readiness"] = {
        "status": "impaired",
        "session_ceiling": {"level": "recovery_only"},
        "interpretation": "CNS impaired.",
    }
    cns_state["environment_evidence"] = _environment(
        pm2_5=151.0,
        gate="close_all_planned_outdoor_exercise",
    )

    cns_plan = build_today_plan(tmp_path, DAY, state=cns_state)
    assert cns_plan["session"]["type"] == "cns_recovery"
    assert not any(
        item.get("source") == "environment_evidence"
        for item in cns_plan["constraint_resolution"]["applied"]
    )

    sunday = "2026-05-03"
    _planned_session(tmp_path, sunday)
    sabbath_state = _state(sunday)
    sabbath_state["environment_evidence"] = _environment(
        pm2_5=151.0,
        gate="close_all_planned_outdoor_exercise",
    )
    sabbath_plan = build_today_plan(tmp_path, sunday, state=sabbath_state)
    assert sabbath_plan["session"]["type"] == "scheduled_rest"
    assert sabbath_plan["session"]["duration_min"] == 0
    assert not any(
        item.get("source") == "environment_evidence"
        for item in sabbath_plan["constraint_resolution"]["applied"]
    )


def test_future_plan_uses_exact_dated_windows_and_never_current_pm_as_clearance_or_closure(
    tmp_path,
):
    load_context(tmp_path)
    tomorrow = "2026-04-30"
    _planned_session(tmp_path, tomorrow)
    state = _state(tomorrow)
    environment = _environment(
        pm2_5=151.0,
        gate="close_all_planned_outdoor_exercise",
    )
    state["environment_evidence"] = _with_forecast(environment, tomorrow)

    plan = build_today_plan(tmp_path, tomorrow, state=state)

    assert plan["session"]["title"] == "Explicit outdoor quality"
    applicability = plan["decision_inputs"]["environment_evidence"][
        "plan_applicability"
    ]
    assert applicability["status"] == "forecast_windows_time_unspecified"
    assert applicability["current_pm_used_for_clearance"] is False
    assert [window["name"] for window in applicability["windows"]] == [
        "morning",
        "afternoon",
    ]
    assert applicability["preferred_window"] == "afternoon"
    assert applicability["preferred_window_relative_only"] is True
    assert applicability["ride_approval"] is False
    assert applicability["preferred_window_used_for_selection"] is False


def test_textual_tomorrow_without_exact_structured_dates_is_never_rolled_forward(
    tmp_path,
):
    load_context(tmp_path)
    tomorrow = "2026-04-30"
    _planned_session(tmp_path, tomorrow)
    state = _state(tomorrow)
    environment = _environment(
        pm2_5=20.0,
        gate="no_environment_downshift_from_current_point",
    )
    environment["last_known_good"]["ride_windows"] = {
        "comparison": {"preferred_window": "morning", "relative_only": True, "ride_approval": False},
        "morning": {
            "target_day": "Tomorrow",
            "current_conditions_applicable": False,
            "weather_forecast": {"available": True},
        },
    }
    state["environment_evidence"] = environment

    plan = build_today_plan(tmp_path, tomorrow, state=state)

    applicability = plan["decision_inputs"]["environment_evidence"][
        "plan_applicability"
    ]
    assert applicability["status"] == "forecast_unavailable_recheck"
    assert applicability["windows"] == []
    assert plan["session"]["title"] == "Explicit outdoor quality"


def test_exact_afternoon_likely_thunderstorm_downshifts_high_consequence_kiara(
    tmp_path,
):
    load_context(tmp_path)
    tomorrow = "2026-04-30"
    _planned_session(tmp_path, tomorrow)
    path = tmp_path / "input" / f"planned_session_{tomorrow}.json"
    planned = read_json(path)
    planned["session"]["time_of_day"] = "afternoon"
    write_json(path, planned)
    state = _state(tomorrow)
    state["environment_evidence"] = _with_forecast(
        _environment(pm2_5=35.0, gate="moderate_caution"),
        tomorrow,
        afternoon_thunder="likely",
    )

    plan = build_today_plan(tmp_path, tomorrow, state=state)

    assert plan["session"]["type"] == "environment_indoor_continuity"
    constraint = next(
        item
        for item in plan["constraint_resolution"]["applied"]
        if item.get("source") == "environment_evidence"
    )
    assert constraint["gate"] == "structured_thunderstorm_hold"
    assert constraint["forecast_window_names"] == ["afternoon"]
    applicability = plan["decision_inputs"]["environment_evidence"][
        "plan_applicability"
    ]
    assert applicability["status"] == "forecast_named_window_planning_context"
    assert applicability["candidate_window_names"] == ["afternoon"]
    assert applicability["applicable_window_names"] == []


def test_explicit_1400_matches_exact_afternoon_interval_and_applies_thunder_hold(
    tmp_path,
):
    load_context(tmp_path)
    tomorrow = "2026-04-30"
    _planned_session(tmp_path, tomorrow)
    path = tmp_path / "input" / f"planned_session_{tomorrow}.json"
    planned = read_json(path)
    planned["session"]["planned_start_time_local"] = "14:00"
    write_json(path, planned)
    state = _state(tomorrow)
    environment = _with_forecast(
        _environment(pm2_5=35.0, gate="moderate_caution"),
        tomorrow,
        afternoon_thunder="likely",
    )
    environment["generated_at"] = f"{tomorrow}T13:00:00+08:00"
    environment["latest_attempt"]["attempted_at"] = (
        f"{tomorrow}T13:00:00+08:00"
    )
    environment["last_known_good"]["ride_windows"]["afternoon"][
        "recheck_at_local"
    ] = f"{tomorrow}T12:30:00+08:00"
    state["environment_evidence"] = environment

    plan = build_today_plan(tmp_path, tomorrow, state=state)

    assert plan["session"]["type"] == "environment_indoor_continuity"
    applicability = plan["decision_inputs"]["environment_evidence"][
        "plan_applicability"
    ]
    assert applicability["status"] == "forecast_window_applicable"
    assert applicability["time_basis"] == "explicit_local_clock"
    assert applicability["recheck_state"] == "recheck_satisfied"
    assert applicability["applicable_window_names"] == ["afternoon"]


def test_same_local_date_future_window_takes_forecast_precedence_over_low_current_pm(
    tmp_path,
):
    load_context(tmp_path)
    tomorrow = "2026-04-30"
    _planned_session(tmp_path, tomorrow)
    path = tmp_path / "input" / f"planned_session_{tomorrow}.json"
    planned = read_json(path)
    planned["session"]["planned_start_time_local"] = "14:00"
    write_json(path, planned)
    state = _state(tomorrow)
    environment = _with_forecast(
        _environment(
            pm2_5=20.0,
            gate="no_environment_downshift_from_current_point",
        ),
        tomorrow,
        afternoon_thunder="likely",
    )
    environment["date"] = tomorrow
    environment["last_known_good"]["observation"]["observed_at_utc"] = (
        "2026-04-29T16:05:00Z"
    )
    state["environment_evidence"] = environment

    plan = build_today_plan(tmp_path, tomorrow, state=state)

    applicability = plan["decision_inputs"]["environment_evidence"][
        "plan_applicability"
    ]
    assert applicability["status"] == "forecast_window_applicable"
    assert applicability["current_pm_used_for_clearance"] is False
    assert applicability["applicable_window_names"] == ["afternoon"]
    assert plan["session"]["type"] == "environment_indoor_continuity"


def test_explicit_time_outside_modeled_windows_requires_recheck_without_selection(
    tmp_path,
):
    load_context(tmp_path)
    tomorrow = "2026-04-30"
    _planned_session(tmp_path, tomorrow)
    path = tmp_path / "input" / f"planned_session_{tomorrow}.json"
    planned = read_json(path)
    planned["session"]["planned_start_time_local"] = "13:30"
    write_json(path, planned)
    state = _state(tomorrow)
    state["environment_evidence"] = _with_forecast(
        _environment(pm2_5=35.0, gate="moderate_caution"),
        tomorrow,
        afternoon_thunder="likely",
    )

    plan = build_today_plan(tmp_path, tomorrow, state=state)

    assert plan["session"]["title"] == "Explicit outdoor quality"
    applicability = plan["decision_inputs"]["environment_evidence"][
        "plan_applicability"
    ]
    assert applicability["status"] == (
        "planned_interval_outside_published_window_recheck"
    )
    assert applicability["applicable_window_names"] == []
    assert not any(
        item.get("source") == "environment_evidence"
        for item in plan["constraint_resolution"]["applied"]
    )


def test_wrong_date_explicit_datetime_is_rejected_even_when_clock_matches(tmp_path):
    load_context(tmp_path)
    tomorrow = "2026-04-30"
    _planned_session(tmp_path, tomorrow)
    path = tmp_path / "input" / f"planned_session_{tomorrow}.json"
    planned = read_json(path)
    planned["session"]["planned_start_at_local"] = "2026-04-29T14:00:00+08:00"
    write_json(path, planned)
    state = _state(tomorrow)
    state["environment_evidence"] = _with_forecast(
        _environment(pm2_5=35.0, gate="moderate_caution"),
        tomorrow,
        afternoon_thunder="likely",
    )

    plan = build_today_plan(tmp_path, tomorrow, state=state)

    applicability = plan["decision_inputs"]["environment_evidence"][
        "plan_applicability"
    ]
    assert applicability["time_basis"] == "explicit_datetime_wrong_target_date"
    assert applicability["applicable_window_names"] == []
    assert plan["session"]["title"] == "Explicit outdoor quality"


def test_iso_local_and_aware_starts_use_exact_afternoon_containment(tmp_path):
    load_context(tmp_path)
    tomorrow = "2026-04-30"
    environment = _with_forecast(
        _environment(pm2_5=35.0, gate="moderate_caution"),
        tomorrow,
    )
    for index, planned_start in enumerate(
        ("2026-04-30T14:30:00", "2026-04-30T06:30:00Z")
    ):
        root = tmp_path / f"case_{index}"
        load_context(root)
        _planned_session(root, tomorrow)
        path = root / "input" / f"planned_session_{tomorrow}.json"
        planned = read_json(path)
        planned["session"]["planned_start_at_local"] = planned_start
        planned["session"]["duration_min"] = 90
        planned["session"]["dose"]["duration_min"] = 90
        write_json(path, planned)
        state = _state(tomorrow)
        state["environment_evidence"] = environment

        plan = build_today_plan(root, tomorrow, state=state)

        applicability = plan["decision_inputs"]["environment_evidence"][
            "plan_applicability"
        ]
        assert applicability["applicable_window_names"] == ["afternoon"]


def test_1530_two_hour_ride_cannot_match_1400_to_1600_modeled_window(tmp_path):
    load_context(tmp_path)
    tomorrow = "2026-04-30"
    _planned_session(tmp_path, tomorrow)
    path = tmp_path / "input" / f"planned_session_{tomorrow}.json"
    planned = read_json(path)
    planned["session"]["planned_start_time_local"] = "15:30"
    write_json(path, planned)
    state = _state(tomorrow)
    state["environment_evidence"] = _with_forecast(
        _environment(pm2_5=35.0, gate="moderate_caution"),
        tomorrow,
        afternoon_thunder="likely",
    )

    plan = build_today_plan(tmp_path, tomorrow, state=state)

    applicability = plan["decision_inputs"]["environment_evidence"][
        "plan_applicability"
    ]
    assert applicability["action_interval_state"] == (
        "exact_interval_outside_modeled_window"
    )
    assert applicability["candidate_window_names"] == []
    assert applicability["applicable_window_names"] == []
    assert plan["session"]["title"] == "Explicit outdoor quality"


def test_exact_start_without_explicit_duration_is_planning_only_not_applicable(
    tmp_path,
):
    load_context(tmp_path)
    tomorrow = "2026-04-30"
    _planned_session(tmp_path, tomorrow)
    path = tmp_path / "input" / f"planned_session_{tomorrow}.json"
    planned = read_json(path)
    planned["session"]["planned_start_time_local"] = "14:00"
    planned["session"].pop("duration_min")
    planned["session"]["dose"].pop("duration_min")
    write_json(path, planned)
    state = _state(tomorrow)
    state["environment_evidence"] = _with_forecast(
        _environment(pm2_5=35.0, gate="moderate_caution"),
        tomorrow,
    )

    plan = build_today_plan(tmp_path, tomorrow, state=state)

    applicability = plan["decision_inputs"]["environment_evidence"][
        "plan_applicability"
    ]
    assert applicability["status"] == "forecast_exact_interval_duration_missing"
    assert applicability["action_interval_state"] == (
        "exact_interval_duration_missing"
    )
    assert applicability["candidate_window_names"] == ["afternoon"]
    assert applicability["applicable_window_names"] == []
    assert applicability["can_promote_training"] is False


def test_v16_exact_window_without_parseable_recheck_cannot_be_normally_applicable(
    tmp_path,
):
    load_context(tmp_path)
    tomorrow = "2026-04-30"
    _planned_session(tmp_path, tomorrow)
    path = tmp_path / "input" / f"planned_session_{tomorrow}.json"
    planned = read_json(path)
    planned["session"]["planned_start_time_local"] = "14:00"
    write_json(path, planned)
    state = _state(tomorrow)
    environment = _with_forecast(
        _environment(pm2_5=35.0, gate="moderate_caution"),
        tomorrow,
    )
    environment["last_known_good"]["identity"]["schema_version"] = "1.6.0"
    environment["last_known_good"]["ride_windows"]["afternoon"][
        "recheck_at_local"
    ] = "not-a-time"
    state["environment_evidence"] = environment

    plan = build_today_plan(tmp_path, tomorrow, state=state)

    applicability = plan["decision_inputs"]["environment_evidence"][
        "plan_applicability"
    ]
    assert applicability["status"] == "retained_recheck_required"
    assert applicability["recheck_state"] == "retained_recheck_required"
    assert applicability["action_interval_state"] == "exact_interval_contained"
    assert applicability["can_promote_training"] is False


def test_before_recheck_is_planning_only_even_with_exact_time(tmp_path):
    load_context(tmp_path)
    tomorrow = "2026-04-30"
    _planned_session(tmp_path, tomorrow)
    path = tmp_path / "input" / f"planned_session_{tomorrow}.json"
    planned = read_json(path)
    planned["session"]["planned_start_time_local"] = "14:00"
    write_json(path, planned)
    state = _state(tomorrow)
    environment = _with_forecast(
        _environment(pm2_5=35.0, gate="moderate_caution"),
        tomorrow,
    )
    environment["generated_at"] = f"{tomorrow}T11:00:00+08:00"
    environment["latest_attempt"]["attempted_at"] = (
        f"{tomorrow}T11:00:00+08:00"
    )
    environment["last_known_good"]["ride_windows"]["afternoon"][
        "recheck_at_local"
    ] = f"{tomorrow}T12:30:00+08:00"
    state["environment_evidence"] = environment

    plan = build_today_plan(tmp_path, tomorrow, state=state)

    applicability = plan["decision_inputs"]["environment_evidence"][
        "plan_applicability"
    ]
    assert applicability["status"] == "forecast_planning_only_recheck_pending"
    assert applicability["recheck_state"] == "planning_only_recheck_pending"
    assert applicability["can_promote_training"] is False


def test_failed_refresh_after_recheck_retains_thunder_restriction_and_requires_recheck(
    tmp_path,
):
    load_context(tmp_path)
    tomorrow = "2026-04-30"
    _planned_session(tmp_path, tomorrow)
    path = tmp_path / "input" / f"planned_session_{tomorrow}.json"
    planned = read_json(path)
    planned["session"]["planned_start_time_local"] = "14:00"
    write_json(path, planned)
    state = _state(tomorrow)
    environment = _with_forecast(
        _environment(pm2_5=35.0, gate="moderate_caution"),
        tomorrow,
        afternoon_thunder="likely",
    )
    environment["generated_at"] = f"{tomorrow}T13:00:00+08:00"
    environment["latest_attempt"].update(
        {
            "attempted_at": f"{tomorrow}T13:00:00+08:00",
            "status": "failed",
            "error": "timeout",
        }
    )
    environment["last_known_good"]["ride_windows"]["afternoon"][
        "recheck_at_local"
    ] = f"{tomorrow}T12:30:00+08:00"
    state["environment_evidence"] = environment

    plan = build_today_plan(tmp_path, tomorrow, state=state)

    applicability = plan["decision_inputs"]["environment_evidence"][
        "plan_applicability"
    ]
    assert applicability["status"] == "retained_recheck_required"
    assert applicability["recheck_state"] == "retained_recheck_required"
    assert plan["session"]["type"] == "environment_indoor_continuity"
    constraint = next(
        item
        for item in plan["constraint_resolution"]["applied"]
        if item.get("source") == "environment_evidence"
    )
    assert constraint["forecast_recheck_state"] == "retained_recheck_required"


def test_failed_refresh_after_recheck_never_turns_low_forecast_into_clearance(tmp_path):
    load_context(tmp_path)
    tomorrow = "2026-04-30"
    _planned_session(tmp_path, tomorrow)
    path = tmp_path / "input" / f"planned_session_{tomorrow}.json"
    planned = read_json(path)
    planned["session"]["planned_start_time_local"] = "14:00"
    write_json(path, planned)
    state = _state(tomorrow)
    environment = _with_forecast(
        _environment(
            pm2_5=20.0,
            gate="no_environment_downshift_from_current_point",
        ),
        tomorrow,
    )
    environment["generated_at"] = f"{tomorrow}T13:00:00+08:00"
    environment["latest_attempt"].update(
        {
            "attempted_at": f"{tomorrow}T13:00:00+08:00",
            "status": "failed",
            "error": "timeout",
        }
    )
    environment["last_known_good"]["ride_windows"]["afternoon"][
        "recheck_at_local"
    ] = f"{tomorrow}T12:30:00+08:00"
    state["environment_evidence"] = environment

    plan = build_today_plan(tmp_path, tomorrow, state=state)

    applicability = plan["decision_inputs"]["environment_evidence"][
        "plan_applicability"
    ]
    assert applicability["status"] == "retained_recheck_required"
    assert applicability["can_promote_training"] is False
    assert any("without a successful refresh" in item for item in plan["guardrails"])


def test_unavailable_or_time_inconsistent_weather_window_is_rejected(tmp_path):
    load_context(tmp_path)
    tomorrow = "2026-04-30"
    for index, defect in enumerate(("unavailable", "time_mismatch")):
        root = tmp_path / f"defect_{index}"
        load_context(root)
        _planned_session(root, tomorrow)
        path = root / "input" / f"planned_session_{tomorrow}.json"
        planned = read_json(path)
        planned["session"]["planned_start_time_local"] = "14:00"
        write_json(path, planned)
        state = _state(tomorrow)
        environment = _with_forecast(
            _environment(pm2_5=35.0, gate="moderate_caution"),
            tomorrow,
            afternoon_thunder="likely",
        )
        afternoon = environment["last_known_good"]["ride_windows"]["afternoon"]
        if defect == "unavailable":
            afternoon["weather_forecast"]["available"] = False
        else:
            afternoon["weather_forecast"]["start_at_utc"] = (
                f"{tomorrow}T05:00:00Z"
            )
        state["environment_evidence"] = environment

        plan = build_today_plan(root, tomorrow, state=state)

        applicability = plan["decision_inputs"]["environment_evidence"][
            "plan_applicability"
        ]
        assert applicability["applicable_window_names"] == []
        assert plan["session"]["title"] == "Explicit outdoor quality"


def test_ordinary_rain_context_alone_does_not_close_future_kiara_session(tmp_path):
    load_context(tmp_path)
    tomorrow = "2026-04-30"
    _planned_session(tmp_path, tomorrow)
    path = tmp_path / "input" / f"planned_session_{tomorrow}.json"
    planned = read_json(path)
    planned["session"]["time_of_day"] = "afternoon"
    write_json(path, planned)
    state = _state(tomorrow)
    environment = _with_forecast(
        _environment(pm2_5=35.0, gate="moderate_caution"),
        tomorrow,
    )
    afternoon = environment["last_known_good"]["ride_windows"]["afternoon"]
    afternoon["weather_forecast"]["precipitation_probability_max_pct"] = 100
    afternoon["weather_forecast"]["precipitation_mm"] = 12.0
    afternoon["weather_forecast"]["rain_signal"] = "Rain likely · context only"
    afternoon["weather_forecast"]["rain_used_for_comparison"] = False
    state["environment_evidence"] = environment

    plan = build_today_plan(tmp_path, tomorrow, state=state)

    assert plan["session"]["title"] == "Explicit outdoor quality"
    assert not any(
        item.get("source") == "environment_evidence"
        for item in plan["constraint_resolution"]["applied"]
    )


def test_no_planned_time_surfaces_both_windows_and_does_not_choose_safe_or_preferred(
    tmp_path,
):
    load_context(tmp_path)
    tomorrow = "2026-04-30"
    _planned_session(tmp_path, tomorrow)
    state = _state(tomorrow)
    state["environment_evidence"] = _with_forecast(
        _environment(pm2_5=35.0, gate="moderate_caution"),
        tomorrow,
        morning_thunder="likely",
        afternoon_thunder="none",
        preferred_window="afternoon",
    )

    plan = build_today_plan(tmp_path, tomorrow, state=state)

    assert plan["session"]["title"] == "Explicit outdoor quality"
    applicability = plan["decision_inputs"]["environment_evidence"][
        "plan_applicability"
    ]
    assert applicability["status"] == "forecast_windows_time_unspecified"
    assert applicability["candidate_window_names"] == ["morning", "afternoon"]
    assert applicability["applicable_window_names"] == []
    assert applicability["preferred_window_used_for_selection"] is False


def test_no_planned_time_holds_when_both_exact_windows_have_likely_thunder(tmp_path):
    load_context(tmp_path)
    tomorrow = "2026-04-30"
    _planned_session(tmp_path, tomorrow)
    state = _state(tomorrow)
    state["environment_evidence"] = _with_forecast(
        _environment(pm2_5=35.0, gate="moderate_caution"),
        tomorrow,
        morning_thunder="likely",
        afternoon_thunder="likely",
        preferred_window=None,
    )

    plan = build_today_plan(tmp_path, tomorrow, state=state)

    assert plan["session"]["type"] == "environment_indoor_continuity"
    constraint = next(
        item
        for item in plan["constraint_resolution"]["applied"]
        if item.get("source") == "environment_evidence"
    )
    assert constraint["forecast_window_names"] == ["morning", "afternoon"]


def test_beyond_published_horizon_is_unavailable_not_training_promotion(tmp_path):
    load_context(tmp_path)
    tomorrow = "2026-04-30"
    beyond = "2026-05-01"
    _planned_session(tmp_path, beyond)
    state = _state(beyond)
    state["environment_evidence"] = _with_forecast(
        _environment(
            pm2_5=12.0,
            gate="no_environment_downshift_from_current_point",
        ),
        tomorrow,
    )

    plan = build_today_plan(tmp_path, beyond, state=state)

    applicability = plan["decision_inputs"]["environment_evidence"][
        "plan_applicability"
    ]
    assert applicability["status"] == "forecast_unavailable_recheck"
    assert applicability["can_promote_training"] is False
    assert plan["session"]["title"] == "Explicit outdoor quality"


def test_canonical_action_venue_overrides_conflicting_text_and_mutable_aliases(tmp_path):
    load_context(tmp_path)
    _planned_session(tmp_path, venue="Bukit Kiara")
    path = tmp_path / "input" / f"planned_session_{DAY}.json"
    planned = read_json(path)
    planned["session"]["action_identity"]["venue_key"] = "denai_peladang"
    planned["session"]["venue"] = "Bukit Kiara"
    write_json(path, planned)
    state = _state()
    environment = _environment(
        pm2_5=151.0,
        gate="close_all_planned_outdoor_exercise",
    )
    environment["decision"]["venue_keys"] = ["bukit_kiara", "denai_peladang"]
    environment["decision"]["venue_aliases"] = ["Bukit Kiara", "Denai Peladang", "DP"]
    state["environment_evidence"] = environment

    plan = build_today_plan(tmp_path, DAY, state=state)

    assert plan["session"]["title"] == "Explicit outdoor quality"
    applicability = plan["decision_inputs"]["environment_evidence"][
        "plan_applicability"
    ]
    assert applicability["status"] == "venue_specific_evidence_unavailable"


def test_unresolved_kiara_or_dp_option_keeps_separate_venue_branches(tmp_path):
    load_context(tmp_path)
    _planned_session(tmp_path, venue="Bukit Kiara or Denai Peladang (DP)")
    path = tmp_path / "input" / f"planned_session_{DAY}.json"
    planned = read_json(path)
    planned["session"]["action_identity"].pop("venue_key")
    write_json(path, planned)
    state = _state()
    state["environment_evidence"] = _environment(
        pm2_5=151.0,
        gate="close_all_planned_outdoor_exercise",
    )

    plan = build_today_plan(tmp_path, DAY, state=state)

    assert plan["session"]["title"] == "Explicit outdoor quality"
    applicability = plan["decision_inputs"]["environment_evidence"][
        "plan_applicability"
    ]
    assert applicability["status"] == "multi_venue_choice_unresolved"
    assert applicability["venue_applicability"]["denai_peladang"] == (
        "venue_specific_evidence_unavailable"
    )


def test_stale_hazardous_current_evidence_retains_all_outdoor_closure(tmp_path):
    load_context(tmp_path)
    _planned_session(tmp_path, modality="outdoor_hike")
    state = _state()
    state["environment_evidence"] = _environment(
        pm2_5=151.0,
        gate="retained_high_ventilation_closure_pending_refresh",
        freshness="stale",
    )

    plan = build_today_plan(tmp_path, DAY, state=state)

    assert plan["session"]["type"] == "environment_indoor_continuity"
