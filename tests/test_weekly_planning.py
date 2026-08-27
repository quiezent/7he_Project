from coach_sync.context import load_context, save_context
from coach_sync.io import write_json
from coach_sync.planning import SESSION_CONTRACT_FIELDS
from coach_sync.weekly_planning import _apply_adaptive_programming, build_weekly_plan


def _state(
    day: str = "2026-06-22",
    *,
    readiness_level: str = "yellow",
    readiness_score: int = 55,
    hrv_status: str = "UNBALANCED",
    wake_body_battery: int = 62,
    acwr_ratio: float = 0.9,
    acwr_status: str = "OPTIMAL",
) -> dict:
    return {
        "date": day,
        "athlete": {},
        "phase": {"name": "base_rebuild"},
        "data_freshness": {
            "status": "current",
            "hard_session_confidence": "normal",
            "message": "Garmin readiness inputs are current.",
        },
        "readiness": {
            "readiness_level": readiness_level,
            "readiness_score": readiness_score,
            "hard_session_guidance": "caution" if readiness_level == "yellow" else "allow",
        },
        "wellness_trends": {
            "latest": {
                "date": day,
                "body_battery_wake": wake_body_battery,
                "hrv_status": hrv_status,
            },
        },
        "training_load": {
            "last_7_days": {
                "training_load": 687.1,
                "duration_min": 450.6,
                "sessions": 7,
                "outdoor_mtb_sessions": 4,
            },
            "previous_7_days": {
                "training_load": 599.5,
                "duration_min": 526.8,
                "sessions": 4,
                "outdoor_mtb_sessions": 3,
            },
            "acute_load_spike_ratio": 1.15,
        },
        "training_status_current": {
            "training_status_feedback": "MAINTAINING_3",
            "training_paused": False,
            "acute_chronic": {
                "status": acwr_status,
                "ratio": acwr_ratio,
            },
            "load_focus": {
                "feedback": "BALANCED",
                "low_aerobic": 1047.98,
                "low_aerobic_target_min": 428,
                "low_aerobic_target_max": 1081,
                "high_aerobic": 901.3834,
                "high_aerobic_target_min": 768,
                "high_aerobic_target_max": 1420,
                "anaerobic": 541.70026,
                "anaerobic_target_min": 217,
                "anaerobic_target_max": 652,
            },
        },
    }


def _assert_contract(session: dict) -> None:
    assert session["schema_version"] == 3
    assert session["contract_fields"] == SESSION_CONTRACT_FIELDS
    for field in SESSION_CONTRACT_FIELDS:
        assert session.get(field), field


def _write_race_sabbath_plan(root) -> None:
    write_json(
        root / "input" / "planned_session_2026-09-20.json",
        {
            "date": "2026-09-20",
            "generated_at": "2026-09-19T18:00:00+08:00",
            "status": "active_named_race_exception",
            "sabbath_exception": {
                "date": "2026-09-20",
                "authorized_by": "athlete",
                "authorization_source": "Athlete direct statement on 2026-08-24",
                "explicit_one_off": True,
                "scope": "named_race_event_only",
                "recurring_rule_unchanged": True,
                "replacement_sabbath_date": "2026-09-21",
                "event": {
                    "name": "PDR26",
                    "date": "2026-09-20",
                    "discipline": "downhill_mtb",
                    "venue_key": "denai_peladang",
                },
            },
            "session": {
                "title": "PDR26 downhill race",
                "type": "mtb_downhill_race",
                "modality": "mtb",
                "race_event": True,
                "duration_min": 180,
                "intensity": "race",
                "schema_version": 3,
                "contract_fields": SESSION_CONTRACT_FIELDS,
                "purpose": "Race the named downhill event after Saturday practice.",
                "dose": {"event": "Timed downhill runs plus event warm-up."},
                "adaptation_hypothesis": "Specific race execution converts preparation into performance.",
                "execution_rules": ["Use only the practised line and race setup."],
                "expected_result": {"technical": "Clean race execution."},
                "stop_rules": ["Withdraw for impaired processing or unsafe conditions."],
                "post_session_review_fields": [
                    "stop_rule_outcome",
                    "race_run_time",
                    "technical_quality_notes",
                ],
            },
        },
    )


def test_weekly_plan_writes_artifacts_and_caps_mtb_exposures(tmp_path):
    load_context(tmp_path)

    plan = build_weekly_plan(tmp_path, "2026-06-22", state=_state())

    assert plan["week_key"] == "2026-W26"
    assert plan["week_start"] == "2026-06-22"
    assert plan["week_end"] == "2026-06-28"
    assert plan["status"] == "ready"
    assert plan["targets"]["mtb_exposures"]["protected_mtb_exposures"] == 2
    assert plan["targets"]["mtb_exposures"]["maximum_normal_build_mtb_exposures"] == 3
    assert plan["targets"]["mtb_exposures"]["planned_key_mtb_exposures"] == 2
    assert plan["targets"]["mtb_exposures"]["optional_mtb_exposures"] == 1
    assert plan["targets"]["bike_touches"]["preferred"] == 5
    assert plan["targets"]["bike_touches"]["maximum_normal_build"] == 6
    assert plan["targets"]["bike_touches"]["planned_normal_count"] == 5
    assert plan["targets"]["bike_touches"]["planned_max_count"] == 6
    assert plan["targets"]["bike_touches"]["planned_meaningful_cost_sessions"] == 2
    assert (tmp_path / "snapshots" / "weekly_plan.json").exists()
    assert (tmp_path / "snapshots" / "weekly_plan_2026-W26.json").exists()
    assert (tmp_path / "snapshots" / "weekly_plan.txt").exists()
    text = (tmp_path / "snapshots" / "weekly_plan.txt").read_text(encoding="utf-8")
    assert "Purpose:" in text
    assert "Dose:" in text
    assert "Readiness gate:" in text
    assert "Execution rules:" in text
    assert "Stop rules:" in text
    assert "Review fields:" in text

    trainable = [session for session in plan["sessions"] if session["type"] != "scheduled_rest"]
    assert trainable
    for session in trainable:
        _assert_contract(session)

    sunday = plan["sessions"][-1]
    assert sunday["day_name"] == "Sunday"
    assert sunday["type"] == "scheduled_rest"
    assert sunday["duration_min"] == 0


def test_weekly_surfaces_named_race_exception_and_enforces_replacement_monday(tmp_path):
    load_context(tmp_path)
    _write_race_sabbath_plan(tmp_path)

    race_week = build_weekly_plan(
        tmp_path,
        "2026-09-14",
        state=_state(day="2026-09-14", readiness_level="green", readiness_score=82),
    )

    sunday = next(item for item in race_week["sessions"] if item["day_name"] == "Sunday")
    assert sunday["type"] == "mtb_downhill_race"
    assert sunday["sabbath_exception"]["status"] == (
        "validated_exact_date_race_event_exception"
    )
    assert sunday["weekly_intent_override"]["source"].endswith(
        "planned_session_2026-09-20.json"
    )
    sabbath_gate = next(
        item for item in race_week["daily_gates"] if item["gate"] == "Sabbath"
    )
    assert "authorization is never inferred" in sabbath_gate["rule"].lower()

    replacement_week = build_weekly_plan(
        tmp_path,
        "2026-09-21",
        state=_state(day="2026-09-21", readiness_level="green", readiness_score=82),
    )

    monday = next(
        item for item in replacement_week["sessions"] if item["day_name"] == "Monday"
    )
    assert monday["type"] == "scheduled_rest"
    assert monday["title"] == "Replacement Sabbath rest"
    assert monday["scheduled_rest_rule"]["replacement_for"]["event_name"] == "PDR26"
    assert monday["weekly_intent_override"]["status"] == (
        "replacement_sabbath_enforced"
    )
    assert monday["weekly_intent_override"]["source"].endswith(
        "planned_session_2026-09-20.json"
    )


def test_weekly_plan_rejects_unapproved_sunday_training_override(tmp_path):
    load_context(tmp_path)
    write_json(
        tmp_path / "input" / "planned_session_2026-09-20.json",
        {
            "date": "2026-09-20",
            "status": "missing_athlete_authorization",
            "session": {
                "title": "Unapproved Sunday race-like ride",
                "type": "mtb_downhill_race",
                "modality": "mtb",
                "race_event": True,
                "duration_min": 180,
                "intensity": "race",
            },
        },
    )

    plan = build_weekly_plan(
        tmp_path,
        "2026-09-14",
        state=_state(day="2026-09-14", readiness_level="green", readiness_score=82),
    )

    sunday = next(item for item in plan["sessions"] if item["day_name"] == "Sunday")
    assert sunday["type"] == "scheduled_rest"
    assert sunday["title"] == "Sabbath rest"


def test_weekly_plan_respects_two_mtb_exposure_cap_when_configured(tmp_path):
    context = load_context(tmp_path)
    context.setdefault("training_rules", {}).setdefault("bike_specific_continuity", {})[
        "maximum_mtb_exposures_per_week"
    ] = 2
    save_context(context, tmp_path)

    plan = build_weekly_plan(tmp_path, "2026-06-22", state=_state())

    mtb_exposures = [session for session in plan["sessions"] if session.get("mtb_exposure")]
    assert len(mtb_exposures) == 2
    assert plan["targets"]["mtb_exposures"]["maximum_normal_build_mtb_exposures"] == 2
    assert plan["targets"]["mtb_exposures"]["optional_mtb_exposures"] == 0


def test_weekly_plan_downshifts_when_readiness_is_red(tmp_path):
    load_context(tmp_path)

    plan = build_weekly_plan(
        tmp_path,
        "2026-06-22",
        state=_state(readiness_level="red", readiness_score=35),
    )

    assert plan["status"] == "downshifted_readiness"
    assert plan["targets"]["training_load_range"][1] < 687.1
    assert any(gate["gate"] == "readiness" for gate in plan["daily_gates"])


def test_weekly_plan_labels_prior_day_state_basis_as_provisional(tmp_path):
    load_context(tmp_path)

    plan = build_weekly_plan(
        tmp_path,
        "2026-06-23",
        state=_state(day="2026-06-22"),
    )

    assert plan["status"] == "provisional_prior_day_basis"
    assert plan["planning_basis"]["state_basis_date"] == "2026-06-22"
    assert plan["planning_basis"]["plan_target_date"] == "2026-06-23"


def test_weekly_plan_green_monday_preserves_social_run_and_keeps_bike_optional(tmp_path):
    load_context(tmp_path)

    plan = build_weekly_plan(
        tmp_path,
        "2026-06-22",
        state=_state(
            readiness_level="green",
            readiness_score=82,
            hrv_status="BALANCED",
            wake_body_battery=82,
        ),
    )

    monday = plan["sessions"][0]
    assert monday["date"] == "2026-06-22"
    assert monday["type"] == "social_run_optional_bike"
    assert monday["priority"] == "support"
    assert monday["bike_touch_status"] == "optional"
    assert monday["density_cost"] == "low"


def test_weekly_plan_uses_low_cost_touches_to_reach_frequency_target(tmp_path):
    load_context(tmp_path)

    plan = build_weekly_plan(tmp_path, "2026-06-22", state=_state())

    by_day = {session["day_name"]: session for session in plan["sessions"]}
    assert by_day["Tuesday"]["mtb_exposure"] is True
    assert by_day["Tuesday"]["density_cost"] == "meaningful"
    assert by_day["Wednesday"]["type"] == "indoor_low_aerobic"
    assert by_day["Wednesday"]["density_cost"] == "low"
    assert by_day["Thursday"]["mtb_exposure"] is True
    assert by_day["Thursday"]["density_cost"] == "meaningful"
    assert by_day["Friday"]["type"] == "indoor_low_aerobic"
    assert by_day["Saturday"]["bike_touch_status"] == "conditional"
    assert by_day["Sunday"]["type"] == "scheduled_rest"


def test_weekly_plan_consumes_configured_indoor_continuity_contract(tmp_path):
    context = load_context(tmp_path)
    continuity = context.setdefault("training_rules", {}).setdefault(
        "bike_specific_continuity", {}
    )
    continuity["indoor_endurance_dose_anchors"] = {
        "routine_low_cost_continuity_contract": {
            "total_duration_min": 70,
            "main_power_w_range": [128, 138],
            "global_rpe_range": [2, 4],
        }
    }
    save_context(context, tmp_path)

    plan = build_weekly_plan(tmp_path, "2026-06-22", state=_state())

    by_day = {session["day_name"]: session for session in plan["sessions"]}
    monday = by_day["Monday"]
    assert monday["duration_min"] == 70
    assert "70 min Suito at 128-138 W / RPE 2-4" in monday["dose"]["if_run_skipped"]

    wednesday = by_day["Wednesday"]
    assert wednesday["duration_min"] == 70
    assert wednesday["dose"]["duration_min"] == 70
    assert wednesday["dose"]["power_anchor_w_range"] == [128, 138]
    assert wednesday["dose"]["intensity"] == "RPE 2-4, conversational, seated."

    friday = by_day["Friday"]
    assert friday["duration_min"] == 70
    assert friday["dose"]["green_duration_min"] == 70
    assert friday["dose"]["green_power_w_range"] == [128, 138]
    assert friday["dose"]["green"] == "70 min at 128-138 W / RPE 2-4."


def test_weekly_plan_falls_back_to_standard_continuity_dose_for_invalid_contract(
    tmp_path,
):
    context = load_context(tmp_path)
    continuity = context.setdefault("training_rules", {}).setdefault(
        "bike_specific_continuity", {}
    )
    continuity["indoor_endurance_dose_anchors"] = {
        "routine_low_cost_continuity_contract": {
            "total_duration_min": 0,
            "main_power_w_range": [140, 120],
            "global_rpe_range": [8, 2],
        }
    }
    save_context(context, tmp_path)

    plan = build_weekly_plan(tmp_path, "2026-06-22", state=_state())

    by_day = {session["day_name"]: session for session in plan["sessions"]}
    assert by_day["Wednesday"]["duration_min"] == 60
    assert by_day["Wednesday"]["dose"]["power_anchor_w_range"] == [120, 130]
    assert by_day["Friday"]["dose"]["green"] == "60 min at 120-130 W / RPE 2-3."
    assert "60 min Suito at 120-130 W / RPE 2-3" in by_day["Monday"]["dose"][
        "if_run_skipped"
    ]


def test_weekly_plan_applies_explicit_church_rest_and_recomputes_touch_counts(tmp_path):
    load_context(tmp_path)
    write_json(
        tmp_path / "input" / "planned_session_2026-06-27.json",
        {
            "date": "2026-06-27",
            "status": "active_logistics_rest_override",
            "session": {
                "title": "Church commitment — no planned training",
                "type": "scheduled_rest",
                "modality": "rest",
                "duration_min": 0,
                "intensity": "recovery",
            },
        },
    )

    plan = build_weekly_plan(tmp_path, "2026-06-22", state=_state())

    saturday = next(item for item in plan["sessions"] if item["day_name"] == "Saturday")
    assert saturday["type"] == "scheduled_rest"
    assert saturday["weekly_intent_override"]["source"].endswith(
        "planned_session_2026-06-27.json"
    )
    assert plan["targets"]["mtb_exposures"]["optional_mtb_exposures"] == 0
    assert plan["targets"]["bike_touches"]["planned_normal_count"] == 4
    assert plan["targets"]["bike_touches"]["planned_max_count"] == 5


def test_weekly_plan_counts_mutually_exclusive_fallback_days_as_one_touch(tmp_path):
    load_context(tmp_path)
    exclusive_counting = {
        "mode": "mutually_exclusive",
        "group": "midweek-low-aerobic-touch",
    }
    for day, title in (
        ("2026-06-23", "Primary travel-day spin"),
        ("2026-06-24", "Fallback spin if primary was skipped"),
    ):
        write_json(
            tmp_path / "input" / f"planned_session_{day}.json",
            {
                "date": day,
                "status": "active_conditional_override",
                "session": {
                    "title": title,
                    "type": "conditional_low_aerobic",
                    "modality": "bike_indoor",
                    "duration_min": 40,
                    "intensity": "easy",
                    "optional": True,
                    "bike_touch_status": "conditional",
                    "density_cost": "low",
                    "bike_touch_counting": exclusive_counting,
                },
            },
        )

    plan = build_weekly_plan(tmp_path, "2026-06-22", state=_state())

    touches = plan["targets"]["bike_touches"]
    assert touches["planned_normal_unique_bike_days"] == [
        "2026-06-23",
        "2026-06-24",
        "2026-06-25",
        "2026-06-26",
        "2026-06-27",
    ]
    assert touches["planned_normal_count"] == 4
    assert touches["planned_max_count"] == 5
    assert touches["mutually_exclusive_groups"] == [
        {
            "group": "midweek-low-aerobic-touch",
            "candidate_dates": ["2026-06-23", "2026-06-24"],
            "bike_touch_statuses": ["conditional"],
            "counts_as_at_most": 1,
        }
    ]


def test_adaptive_reflow_changes_only_future_discretionary_cost() -> None:
    state = {
        "date": "2026-08-27",
        "adaptive_training": {
            "date": "2026-08-27",
            "roadmap_block": {"program_mode": "absorption"},
            "target_shape": {"duration_multiplier": 0.85},
            "weekly_budget": {"meaningful_cost_days_remaining": 0},
            "progression_decision": {"active_lever": "bike_specific_continuity"},
        },
    }
    sessions = [
        {
            "date": "2026-08-28",
            "title": "Future hard MTB",
            "type": "mtb_quality_engine",
            "modality": "mtb",
            "duration_min": 90,
            "intensity": "moderate_hard",
            "density_cost": "meaningful",
            "mtb_exposure": True,
            "execution_rules": [],
        }
    ]

    adjusted, conflicts = _apply_adaptive_programming(sessions, state)

    assert conflicts == []
    assert adjusted[0]["density_cost"] == "low"
    assert adjusted[0]["intensity"] == "easy"
    assert adjusted[0]["optional"] is True
    assert adjusted[0]["duration_min"] == 75


def test_adaptive_reflow_preserves_explicit_contract_and_surfaces_conflict() -> None:
    state = {
        "date": "2026-08-27",
        "adaptive_training": {
            "date": "2026-08-27",
            "roadmap_block": {"program_mode": "absorption"},
            "target_shape": {"duration_multiplier": 0.85},
            "weekly_budget": {"meaningful_cost_days_remaining": 0},
            "progression_decision": {"active_lever": "bike_specific_continuity"},
        },
    }
    session = {
        "date": "2026-08-28",
        "title": "Explicit Enduro",
        "type": "mtb_durability_quality",
        "modality": "mtb",
        "duration_min": 120,
        "intensity": "moderate_hard",
        "density_cost": "meaningful",
        "mtb_exposure": True,
        "weekly_intent_override": {"source": "input/planned_session_2026-08-28.json"},
    }

    adjusted, conflicts = _apply_adaptive_programming([session], state)

    assert adjusted[0]["duration_min"] == 120
    assert adjusted[0]["density_cost"] == "meaningful"
    assert adjusted[0]["adaptive_programming"]["budget_disposition"] == "explicit_conflict_preserved"
    assert conflicts[0]["resolution"] == "preserved_for_head_coach_review_not_silently_rewritten"
