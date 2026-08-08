from coach_sync.context import load_context, save_context
from coach_sync.io import write_json
from coach_sync.planning import SESSION_CONTRACT_FIELDS, build_today_plan
from coach_sync.readiness import build_readiness
from coach_sync.wellness import build_wellness_trends


def write_wellness(root, day, **fields):
    write_json(
        root / "snapshots" / f"garmin_wellness_{day}.json",
        {"date": day, "payloads": [{"ok": True, "data": fields}]},
    )


def write_training_status(root, day):
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
                                    "dailyTrainingLoadAcute": 357,
                                    "dailyTrainingLoadChronic": 402,
                                },
                            }
                        }
                    }
                },
            },
        },
    )


def write_activity(root, day, load=55):
    write_json(
        root / "activities" / f"activity_{day}.json",
        {
            "activityId": int(day.replace("-", "")),
            "activityName": "Indoor Cycling",
            "activityType": {"typeKey": "indoor_cycling"},
            "startTimeLocal": f"{day} 10:00:00",
            "duration": 3600,
            "activityTrainingLoad": load,
        },
    )


def write_body_composition(root, day, **fields):
    weight = fields.get("weight")
    sample = {
        "calendarDate": day,
        "sourceType": "INDEX_SCALE",
        "timestampGMT": 1778462398000,
        "weight": weight,
        "bmi": fields.get("bmi"),
        "bodyFat": fields.get("body_fat"),
        "bodyWater": fields.get("body_water"),
        "muscleMass": fields.get("muscle_mass"),
        "boneMass": fields.get("bone_mass"),
    }
    has_sample = any(value is not None for value in sample.values() if value != day)
    write_json(
        root / "snapshots" / f"garmin_wellness_{day}.json",
        {
            "date": day,
            "payloads": [
                {"label": "get_stats", "ok": True, "data": {"calendarDate": day}},
                {
                    "label": "get_body_composition",
                    "ok": True,
                    "data": {
                        "dateWeightList": [sample] if has_sample else [],
                        "totalAverage": {
                            "weight": weight,
                            "bmi": fields.get("bmi"),
                            "bodyFat": fields.get("body_fat"),
                            "bodyWater": fields.get("body_water"),
                            "muscleMass": fields.get("muscle_mass"),
                            "boneMass": fields.get("bone_mass"),
                        },
                    },
                },
            ],
        },
    )


def load_context_with_mtb_heat_targets(root):
    context = load_context(root)
    context.setdefault("nutrition", {})["mtb_heat_fueling_targets"] = {
        "basis": "Garmin scale weight 75.76 kg on 2026-05-23; adjust when newer scale data is available.",
        "ride_90_to_150_min": {
            "carbs_g_per_hour": [45, 75],
            "fluid_ml_per_hour": [500, 900],
            "sodium_mg_per_hour": [600, 1000],
        },
        "over_150_min_or_race_practice": {
            "carbs_g_per_hour": [60, 90],
            "fluid_ml_per_hour": [650, 1000],
            "sodium_mg_per_hour": [800, 1200],
        },
        "rule": "In Kuala Lumpur heat, fuel skill quality before obvious bonking.",
    }
    save_context(context, root)
    return context


def test_stale_wellness_lowers_confidence(tmp_path):
    load_context(tmp_path)
    write_wellness(tmp_path, "2026-04-28", sleepScore=85, hrvStatus="balanced")

    readiness = build_readiness(tmp_path, "2026-04-29")

    assert readiness["confidence"] == "low"
    assert any(reason["type"] == "stale_wellness" for reason in readiness["reasons"])


def test_future_wellness_snapshot_does_not_satisfy_past_date(tmp_path):
    load_context(tmp_path)
    write_wellness(tmp_path, "2026-04-28", sleepScore=85, hrvStatus="balanced")
    write_wellness(tmp_path, "2026-04-30", sleepScore=90, hrvStatus="balanced")

    readiness = build_readiness(tmp_path, "2026-04-29")

    assert readiness["evidence"]["wellness_date"] == "2026-04-28"
    assert any(reason["type"] == "stale_wellness" for reason in readiness["reasons"])


def test_stale_checkin_is_ignored_without_symptom_logic(tmp_path):
    load_context(tmp_path)
    write_wellness(tmp_path, "2026-04-29", sleepScore=90, hrvStatus="balanced")
    (tmp_path / "input").mkdir(exist_ok=True)
    (tmp_path / "input" / "daily_checkin.md").write_text(
        "date: 2026-04-28\nnext_morning_response: poor\n",
        encoding="utf-8",
    )

    readiness = build_readiness(tmp_path, "2026-04-29")

    assert readiness["readiness_level"] != "red"
    assert readiness["subjective"]["next_morning_response"] is None
    assert any(reason["type"] == "stale_checkin" for reason in readiness["reasons"])


def test_target_date_feedback_is_not_masked_by_stale_daily_checkin(tmp_path):
    load_context(tmp_path)
    target = "2026-04-29"
    write_wellness(tmp_path, target, sleepScore=90, hrvStatus="balanced")
    (tmp_path / "input").mkdir(exist_ok=True)
    (tmp_path / "input" / "daily_checkin.md").write_text(
        "date: 2026-04-28\nnext_morning_response: good\n",
        encoding="utf-8",
    )
    write_json(
        tmp_path / "input" / f"feedback_{target}.json",
        {"date": target, "next_morning_response": "poor after late work"},
    )

    readiness = build_readiness(tmp_path, target)

    assert readiness["subjective"]["next_morning_response"] == "poor after late work"
    assert readiness["evidence"]["checkin_date"] == target
    assert any(reason["type"] == "next_morning_response" for reason in readiness["reasons"])
    assert not any(reason["type"] == "stale_checkin" for reason in readiness["reasons"])


def test_current_pain_notes_do_not_block_session(tmp_path):
    load_context(tmp_path)
    write_wellness(tmp_path, "2026-04-29", sleepScore=90, hrvStatus="balanced")
    (tmp_path / "input").mkdir(exist_ok=True)
    (tmp_path / "input" / "daily_checkin.md").write_text(
        "date: 2026-04-29\npain: 5/10\nswelling: yes\ngrip_tolerance: poor\n",
        encoding="utf-8",
    )

    readiness = build_readiness(tmp_path, "2026-04-29")
    plan = build_today_plan(tmp_path, "2026-04-29")

    assert readiness["readiness_level"] != "red"
    assert not any(reason["type"] in {"pain", "grip", "tissue_response"} for reason in readiness["reasons"])
    assert plan["session"]["type"] != "recovery"


def test_garmin_optimal_acwr_overrides_local_week_over_week_jump(tmp_path):
    load_context(tmp_path)
    write_wellness(tmp_path, "2026-06-16", sleepScore=90, hrvStatus="balanced", bodyBattery=82)
    write_training_status(tmp_path, "2026-06-16")
    write_activity(tmp_path, "2026-06-03", load=100)
    write_activity(tmp_path, "2026-06-10", load=200)
    write_activity(tmp_path, "2026-06-12", load=200)

    readiness = build_readiness(tmp_path, "2026-06-16")

    assert readiness["readiness_level"] == "green"
    assert not any(reason["type"] == "load_spike" for reason in readiness["reasons"])


def test_primary_sleep_duration_is_an_independent_readiness_gate(tmp_path):
    load_context(tmp_path)
    target = "2026-07-17"
    write_json(
        tmp_path / "snapshots" / f"garmin_wellness_{target}.json",
        {
            "date": target,
            "payloads": [
                {
                    "label": "get_stats",
                    "ok": True,
                    "data": {
                        "calendarDate": target,
                        "bodyBatteryAtWakeTime": 73,
                        "bodyBatteryMostRecentValue": 67,
                    },
                },
                {
                    "label": "get_sleep_data",
                    "ok": True,
                    "data": {
                        "hrvStatus": "BALANCED",
                        "dailySleepDTO": {
                            "calendarDate": target,
                            "sleepTimeSeconds": 19908,
                            "sleepScores": {"overall": {"value": 73}},
                        },
                    },
                },
            ],
        },
    )
    write_training_status(tmp_path, target)
    (tmp_path / "input").mkdir(exist_ok=True)
    (tmp_path / "input" / "daily_checkin.md").write_text(
        f"date: {target}\nnap_duration_min: 90\n",
        encoding="utf-8",
    )

    readiness = build_readiness(tmp_path, target)

    assert readiness["readiness_level"] == "yellow"
    assert readiness["hard_session_guidance"] == "caution"
    assert readiness["sleep_duration_gate"]["core_sleep_hours"] == 5.53
    assert readiness["sleep_duration_gate"]["hard_session_effect"] == "caution"
    short_sleep = next(item for item in readiness["reasons"] if item["type"] == "primary_short_sleep")
    assert short_sleep["duration_band"] == "5_5_to_under_6_hours"
    assert "nap" in short_sleep["message"].lower()


def test_under_five_hours_primary_sleep_is_a_strong_readiness_limiter(tmp_path):
    load_context(tmp_path)
    target = "2026-07-17"
    write_json(
        tmp_path / "snapshots" / f"garmin_wellness_{target}.json",
        {
            "date": target,
            "payloads": [
                {
                    "label": "get_sleep_data",
                    "ok": True,
                    "data": {
                        "hrvStatus": "BALANCED",
                        "dailySleepDTO": {
                            "calendarDate": target,
                            "sleepTimeSeconds": 17640,
                            "sleepScores": {"overall": {"value": 85}},
                        },
                    },
                }
            ],
        },
    )
    write_training_status(tmp_path, target)

    readiness = build_readiness(tmp_path, target)

    assert readiness["readiness_level"] == "red"
    assert readiness["hard_session_guidance"] == "avoid"
    assert readiness["sleep_duration_gate"] == {
        "core_sleep_hours": 4.9,
        "hard_session_effect": "avoid",
        "nap_rule": (
            "A nap may improve alertness but does not remove a primary short-sleep gate "
            "without independently verified total-sleep and post-nap clarity evidence."
        ),
    }


def test_five_to_under_five_half_hours_is_an_acute_short_sleep_caution(tmp_path):
    load_context(tmp_path)
    target = "2026-07-17"
    write_wellness(
        tmp_path,
        target,
        sleepTimeSeconds=18720,
        sleepScore=73,
        hrvStatus="balanced",
        bodyBattery=80,
    )
    write_training_status(tmp_path, target)

    readiness = build_readiness(tmp_path, target)

    assert readiness["readiness_level"] == "yellow"
    assert readiness["hard_session_guidance"] == "caution"
    assert readiness["readiness_score"] <= 69
    assert readiness["sleep_duration_gate"]["core_sleep_hours"] == 5.2
    reason = next(item for item in readiness["reasons"] if item["type"] == "primary_short_sleep")
    assert reason["duration_band"] == "5_to_under_5_5_hours"
    assert reason["hard_session_effect"] == "caution"
    assert "field gate" in reason["message"].lower()


def test_today_plan_uses_dated_planned_session_input(tmp_path):
    load_context(tmp_path)
    write_wellness(tmp_path, "2026-06-16", sleepScore=90, hrvStatus="balanced", bodyBattery=82)
    write_training_status(tmp_path, "2026-06-16")
    write_activity(tmp_path, "2026-06-16")
    write_json(
        tmp_path / "input" / "planned_session_2026-06-16.json",
        {
            "date": "2026-06-16",
            "session": {
                "title": "Coach-authored primer",
                "type": "recovery",
                "duration_min": 30,
                "intensity": "recovery",
            },
        },
    )

    plan = build_today_plan(tmp_path, "2026-06-16")

    assert plan["session"]["title"] == "Coach-authored primer"
    assert plan["plan_source"] == {
        "type": "input_planned_session",
        "path": "input/planned_session_2026-06-16.json",
    }
    assert any("coach-authored planned session" in item for item in plan["guardrails"])


def test_today_plan_uses_matching_weekly_intent_when_no_explicit_session_exists(tmp_path):
    load_context(tmp_path)
    target = "2026-06-16"
    weekly_session = {
        "date": target,
        "title": "Weekly Kiara quality intent",
        "type": "mtb_quality_skill",
        "duration_min": 75,
        "intensity": "moderate",
        "schema_version": 3,
        "contract_fields": SESSION_CONTRACT_FIELDS,
        "purpose": "Build controlled cornering and climbing transfer.",
        "dose": {"loops": 3},
        "adaptation_hypothesis": "Repeatable loops improve quality without uncontrolled density.",
        "execution_rules": ["Keep each descent technically deliberate."],
        "expected_result": {"technical": "Consistent braking timing."},
        "stop_rules": ["Stop if line choice becomes reactive."],
        "post_session_review_fields": ["loop_count"],
    }
    write_json(
        tmp_path / "snapshots" / "weekly_plan_2026-W25.json",
        {
            "artifact_type": "weekly_training_plan",
            "week_key": "2026-W25",
            "week_start": "2026-06-15",
            "week_end": "2026-06-21",
            "generated_at": "2026-06-15T08:00:00+08:00",
            "status": "ready",
            "sessions": [weekly_session],
        },
    )

    plan = build_today_plan(tmp_path, target, state=_green_state(target))

    assert plan["session"]["title"] == "Weekly Kiara quality intent"
    assert plan["plan_source"]["type"] == "weekly_plan_session"
    assert plan["plan_source"]["path"] == "snapshots/weekly_plan_2026-W25.json"
    assert plan["session"]["schema_version"] == 3
    assert any("week's intent session" in item for item in plan["guardrails"])


def test_explicit_session_overrides_matching_weekly_intent(tmp_path):
    load_context(tmp_path)
    target = "2026-06-16"
    write_json(
        tmp_path / "snapshots" / "weekly_plan_2026-W25.json",
        {
            "artifact_type": "weekly_training_plan",
            "week_start": "2026-06-15",
            "week_end": "2026-06-21",
            "sessions": [
                {
                    "date": target,
                    "title": "Weekly intent",
                    "type": "mtb_quality_skill",
                    "duration_min": 75,
                    "intensity": "moderate",
                }
            ],
        },
    )
    write_json(
        tmp_path / "input" / f"planned_session_{target}.json",
        {
            "date": target,
            "session": {
                "title": "Explicit coach adjustment",
                "type": "outdoor_bike_optional",
                "duration_min": 30,
                "intensity": "easy",
            },
        },
    )

    plan = build_today_plan(tmp_path, target, state=_green_state(target))

    assert plan["session"]["title"] == "Explicit coach adjustment"
    assert plan["plan_source"]["type"] == "input_planned_session"


def test_base_phase_green_day_gets_trainable_plan(tmp_path):
    load_context(tmp_path)
    write_wellness(tmp_path, "2026-04-29", sleepScore=90, hrvStatus="balanced", bodyBattery=80)
    write_training_status(tmp_path, "2026-04-29")
    write_activity(tmp_path, "2026-04-29")

    plan = build_today_plan(tmp_path, "2026-04-29")

    assert plan["decision_inputs"]["phase"] == "base_rebuild"
    assert plan["session"]["type"] in {"endurance_skills", "bike_quality"}
    assert "daily_protein" in plan["nutrition"]


def _green_state(day: str, hard_confidence: str = "normal") -> dict:
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
            "hard_session_confidence": hard_confidence,
            "hard_session_limiters": ["Recent activity data is missing."]
            if hard_confidence == "limited"
            else [],
        },
    }


def _yellow_state(day: str) -> dict:
    state = _green_state(day)
    state["readiness"] = {
        "readiness_level": "yellow",
        "readiness_score": 65,
        "hard_session_guidance": "caution",
    }
    return state


def _with_training_status(
    state: dict,
    *,
    feedback: str = "PRODUCTIVE_2",
    acwr_status: str = "OPTIMAL",
    acwr_ratio: float = 1.0,
    low_aerobic: float = 1230,
    low_aerobic_max: float = 1081,
    high_aerobic: float = 901,
    high_aerobic_min: float = 768,
    high_aerobic_max: float = 1420,
    anaerobic: float = 560,
    anaerobic_max: float = 652,
) -> dict:
    state["training_status_current"] = {
        "training_status_feedback": feedback,
        "training_paused": False,
        "acute_chronic": {
            "status": acwr_status,
            "ratio": acwr_ratio,
        },
        "load_focus": {
            "low_aerobic": low_aerobic,
            "low_aerobic_target_min": 428,
            "low_aerobic_target_max": low_aerobic_max,
            "high_aerobic": high_aerobic,
            "high_aerobic_target_min": high_aerobic_min,
            "high_aerobic_target_max": high_aerobic_max,
            "anaerobic": anaerobic,
            "anaerobic_target_min": 217,
            "anaerobic_target_max": anaerobic_max,
            "feedback": "AEROBIC_LOW_FOCUS",
        },
    }
    return state


def _assert_schema_v3_contract(session: dict) -> None:
    assert session["schema_version"] == 3
    assert session["contract_fields"] == SESSION_CONTRACT_FIELDS
    for field in SESSION_CONTRACT_FIELDS:
        assert session.get(field), field
    assert isinstance(session["dose"], dict)
    assert isinstance(session["execution_rules"], list)
    assert isinstance(session["stop_rules"], list)
    assert isinstance(session["post_session_review_fields"], list)


def _bounded_familiar_skill_session() -> dict:
    return {
        "title": "Kiara acute-short-sleep familiar skill gate",
        "type": "mtb_skill_familiar_capped",
        "modality": "mtb",
        "duration_min": 70,
        "intensity": "skill",
        "schema_version": 3,
        "contract_fields": SESSION_CONTRACT_FIELDS,
        "purpose": "Refresh familiar trail timing with bounded consequence.",
        "dose": {
            "descent": "One familiar Pure Quill descent at 70-75 percent.",
            "hard_cap": "One climb, one descent, and required egress only.",
        },
        "adaptation_hypothesis": "A bounded familiar descent can refresh timing without repeat density.",
        "execution_rules": [
            "Use the familiar line.",
            "No jumps, novelty, speed hunting, setup testing, or second stage.",
        ],
        "expected_result": {"technical": "Early line choice and deliberate brake release."},
        "stop_rules": ["End technical intent if line choice becomes reactive."],
        "post_session_review_fields": ["stop_rule_outcome", "technical_quality_notes"],
    }


def _bounded_controlled_repeatability_session(max_cycles: int = 2) -> dict:
    return {
        "title": "Kiara two-cycle familiar repeatability",
        "type": "mtb_repeatability_controlled",
        "modality": "mtb",
        "duration_min": 135,
        "intensity": "skill",
        "garmin_ceiling_class": "controlled_familiar_repeatability",
        "novelty_allowed": False,
        "open_ended": False,
        "race_simulation": False,
        "setup_changes_allowed": False,
        "setup_test": False,
        "schema_version": 3,
        "contract_fields": SESSION_CONTRACT_FIELDS,
        "purpose": "Restore familiar same-stage repeatability without open-ended extension.",
        "dose": {
            "max_cycles": max_cycles,
            "hard_cap": "Two familiar cycles maximum and no bonus descent.",
        },
        "adaptation_hypothesis": "Two bounded cycles restore repeatability without novelty.",
        "execution_rules": ["Use the same familiar stage and unchanged setup."],
        "expected_result": {"technical": "Final execution matches the first cycle."},
        "stop_rules": ["Stop after any reactive braking or delayed line choice."],
        "post_session_review_fields": ["stop_rule_outcome", "technical_quality_notes"],
    }


def test_trainable_today_plan_sessions_include_schema_v3_contract(tmp_path):
    load_context(tmp_path)

    yellow = build_today_plan(tmp_path, "2026-04-29", state=_yellow_state("2026-04-29"))
    green_skills = build_today_plan(tmp_path, "2026-04-29", state=_green_state("2026-04-29"))
    green_quality = build_today_plan(tmp_path, "2026-04-30", state=_green_state("2026-04-30"))
    data_limited = build_today_plan(
        tmp_path,
        "2026-04-30",
        state=_green_state("2026-04-30", hard_confidence="limited"),
    )

    assert yellow["session"]["type"] == "outdoor_bike_optional"
    assert green_skills["session"]["type"] == "endurance_skills"
    assert green_quality["session"]["type"] == "bike_quality"
    assert data_limited["session"]["type"] == "endurance_data_limited"
    for plan in (yellow, green_skills, green_quality, data_limited):
        _assert_schema_v3_contract(plan["session"])


def test_garmin_productive_status_can_upgrade_yellow_day_to_controlled_repeatability(tmp_path):
    load_context(tmp_path)
    state = _with_training_status(_yellow_state("2026-04-29"))

    plan = build_today_plan(tmp_path, "2026-04-29", state=state)

    arbitration = plan["decision_inputs"]["garmin_arbitration"]
    assert arbitration["recommended_action"] == "controlled_upgrade"
    assert arbitration["ceiling"] == "controlled_mtb_repeatability"
    assert plan["session"]["type"] == "mtb_repeatability_controlled"
    assert plan["session"]["stimulus_intent"] == "controlled_high_aerobic_mtb"
    assert any("anaerobic" in item.lower() for item in arbitration["avoid"])
    assert any("Garmin arbitration" in item for item in plan["guardrails"])
    _assert_schema_v3_contract(plan["session"])


def test_garmin_non_optimal_acwr_downshifts_green_hard_day(tmp_path):
    load_context(tmp_path)
    state = _with_training_status(
        _green_state("2026-04-30"),
        acwr_status="HIGH",
        acwr_ratio=1.6,
    )

    plan = build_today_plan(tmp_path, "2026-04-30", state=state)

    assert plan["decision_inputs"]["garmin_arbitration"]["recommended_action"] == "downshift"
    assert plan["session"]["type"] == "outdoor_bike_optional"
    assert plan["session"]["intensity"] == "easy"
    _assert_schema_v3_contract(plan["session"])


def test_garmin_low_acwr_holds_green_plan_without_treating_underload_as_overload(tmp_path):
    load_context(tmp_path)
    state = _with_training_status(
        _green_state("2026-04-30"),
        feedback="MAINTAINING_2",
        acwr_status="LOW",
        acwr_ratio=0.6,
        low_aerobic=473,
        low_aerobic_max=862,
        high_aerobic=599,
        high_aerobic_min=609,
        high_aerobic_max=1129,
        anaerobic=220,
        anaerobic_max=519,
    )

    plan = build_today_plan(tmp_path, "2026-04-30", state=state)

    arbitration = plan["decision_inputs"]["garmin_arbitration"]
    assert arbitration["recommended_action"] == "hold_plan"
    assert plan["session"]["type"] == "bike_quality"
    assert any("abrupt load spike" in item.lower() for item in arbitration["avoid"])
    _assert_schema_v3_contract(plan["session"])


def test_garmin_recovery_low_acwr_preserves_bounded_familiar_skill_contract(tmp_path):
    load_context(tmp_path)
    target = "2026-07-25"
    state = _with_training_status(
        _yellow_state(target),
        feedback="RECOVERY_2",
        acwr_status="LOW",
        acwr_ratio=0.1,
        low_aerobic=120,
        low_aerobic_max=663,
        high_aerobic=400,
        high_aerobic_min=471,
        high_aerobic_max=872,
        anaerobic=100,
        anaerobic_max=400,
    )
    state["cns_readiness"] = {
        "status": "watch",
        "session_ceiling": {"level": "controlled_skill_only"},
    }
    write_json(
        tmp_path / "input" / f"planned_session_{target}.json",
        {
            "date": target,
            "generated_at": f"{target}T15:16:00+08:00",
            "status": "active",
            "session": _bounded_familiar_skill_session(),
        },
    )

    plan = build_today_plan(tmp_path, target, state=state)

    arbitration = plan["decision_inputs"]["garmin_arbitration"]
    assert arbitration["recommended_action"] == "downshift"
    assert arbitration["ceiling"] == "controlled_familiar_skill_or_aerobic_continuity"
    assert any("bounded familiar technique" in item.lower() for item in arbitration["allowed_stimulus"])
    assert plan["session"]["title"] == "Kiara acute-short-sleep familiar skill gate"
    assert plan["session"]["type"] == "mtb_skill_familiar_capped"
    assert plan["constraint_resolution"]["applied"] == []
    assert plan["coaching_status"] == "proposal_for_llm_coach"
    assert plan["decision_inputs"]["session_lifecycle"]["stance"] == "pre_session"


def test_garmin_recovery_low_acwr_preserves_explicit_green_ready_two_cycle_contract(tmp_path):
    load_context(tmp_path)
    target = "2026-08-08"
    state = _with_training_status(
        _green_state(target),
        feedback="RECOVERY_2",
        acwr_status="LOW",
        acwr_ratio=0.2,
    )
    state["cns_readiness"] = {
        "status": "ready",
        "session_ceiling": {"level": "normal_if_physical_readiness_allows"},
    }
    session = _bounded_controlled_repeatability_session()
    write_json(
        tmp_path / "input" / f"planned_session_{target}.json",
        {
            "date": target,
            "generated_at": f"{target}T14:47:52+08:00",
            "status": "active_same_day_coach_override",
            "session": session,
        },
    )

    plan = build_today_plan(tmp_path, target, state=state)

    assert plan["session"]["title"] == session["title"]
    assert plan["session"]["type"] == "mtb_repeatability_controlled"
    assert plan["session"]["duration_min"] == 135
    assert plan["session"]["dose"] == session["dose"]
    assert plan["session"]["stop_rules"] == session["stop_rules"]
    assert plan["constraint_resolution"]["applied"] == []


def test_garmin_recovery_low_acwr_rejects_more_than_two_familiar_cycles(tmp_path):
    load_context(tmp_path)
    target = "2026-08-08"
    state = _with_training_status(
        _green_state(target),
        feedback="RECOVERY_2",
        acwr_status="LOW",
        acwr_ratio=0.2,
    )
    state["cns_readiness"] = {
        "status": "ready",
        "session_ceiling": {"level": "normal_if_physical_readiness_allows"},
    }
    write_json(
        tmp_path / "input" / f"planned_session_{target}.json",
        {
            "date": target,
            "generated_at": f"{target}T14:47:52+08:00",
            "status": "active_same_day_coach_override",
            "session": _bounded_controlled_repeatability_session(max_cycles=3),
        },
    )

    plan = build_today_plan(tmp_path, target, state=state)

    assert plan["session"]["type"] == "garmin_aerobic_continuity"
    assert plan["constraint_resolution"]["applied"][0]["source"] == (
        "garmin_diagnosis_arbitration"
    )


def test_post_session_rebuild_preserves_executed_coach_authored_contract(tmp_path):
    load_context(tmp_path)
    target = "2026-07-25"
    state = _with_training_status(
        _yellow_state(target),
        feedback="RECOVERY_2",
        acwr_status="HIGH",
        acwr_ratio=1.6,
    )
    state["latest_session_evidence"] = {
        "activity": {
            "activity_id": "23723839107",
            "date": target,
            "category": "mtb",
            "type": "mountain_biking",
            "started_at_local": f"{target} 15:17:24",
        }
    }
    write_json(
        tmp_path / "input" / f"planned_session_{target}.json",
        {
            "date": target,
            "generated_at": f"{target}T15:16:00+08:00",
            "status": "active_same_day_coach_override",
            "session": _bounded_familiar_skill_session(),
        },
    )

    plan = build_today_plan(tmp_path, target, state=state)

    assert plan["decision_inputs"]["garmin_arbitration"]["recommended_action"] == "downshift"
    assert plan["session"]["title"] == "Kiara acute-short-sleep familiar skill gate"
    assert plan["session"]["type"] == "mtb_skill_familiar_capped"
    assert plan["coaching_status"] == "post_session_review"
    assert plan["constraint_resolution"] == {
        "applied": [],
        "effective_session_source": "executed_coach_authored_contract",
    }
    lifecycle = plan["decision_inputs"]["session_lifecycle"]
    assert lifecycle["stance"] == "post_session_review"
    assert lifecycle["source"] == "current_state.latest_session_evidence.activity"
    assert lifecycle["activity_id"] == "23723839107"
    assert any("do not retroactively rewrite" in item.lower() for item in plan["guardrails"])


def test_post_session_red_state_does_not_rewrite_executed_contract(tmp_path):
    load_context(tmp_path)
    target = "2026-07-25"
    state = _with_training_status(
        _yellow_state(target),
        feedback="RECOVERY_2",
        acwr_status="HIGH",
        acwr_ratio=1.6,
    )
    state["readiness"] = {
        "readiness_level": "red",
        "readiness_score": 35,
        "hard_session_guidance": "avoid",
    }
    state["latest_session_evidence"] = {
        "activity": {
            "activity_id": "23723839107",
            "date": target,
            "category": "mtb",
            "type": "mountain_biking",
            "started_at_local": f"{target} 15:17:24",
        }
    }
    write_json(
        tmp_path / "input" / f"planned_session_{target}.json",
        {
            "date": target,
            "generated_at": f"{target}T15:16:00+08:00",
            "status": "active_same_day_coach_override",
            "session": _bounded_familiar_skill_session(),
        },
    )

    plan = build_today_plan(tmp_path, target, state=state)

    assert plan["coaching_status"] == "post_session_review"
    assert plan["session"]["type"] == "mtb_skill_familiar_capped"
    assert (
        plan["constraint_resolution"]["effective_session_source"]
        == "executed_coach_authored_contract"
    )


def test_matching_activity_before_contract_does_not_freeze_later_prescription(tmp_path):
    load_context(tmp_path)
    target = "2026-07-25"
    state = _with_training_status(
        _yellow_state(target),
        feedback="RECOVERY_2",
        acwr_status="HIGH",
        acwr_ratio=1.6,
    )
    state["latest_session_evidence"] = {
        "activity": {
            "activity_id": "earlier-ride",
            "date": target,
            "category": "mtb",
            "type": "mountain_biking",
            "started_at_local": f"{target} 09:00:00",
        }
    }
    write_json(
        tmp_path / "input" / f"planned_session_{target}.json",
        {
            "date": target,
            "generated_at": f"{target}T15:16:00+08:00",
            "status": "active",
            "session": _bounded_familiar_skill_session(),
        },
    )

    plan = build_today_plan(tmp_path, target, state=state)

    assert plan["coaching_status"] == "proposal_for_llm_coach"
    assert plan["session"]["type"] == "garmin_aerobic_continuity"
    assert plan["constraint_resolution"]["applied"][0]["source"] == "garmin_diagnosis_arbitration"


def test_cns_impairment_still_overrides_bounded_familiar_skill_contract(tmp_path):
    load_context(tmp_path)
    target = "2026-07-25"
    state = _with_training_status(
        _yellow_state(target),
        feedback="RECOVERY_2",
        acwr_status="LOW",
        acwr_ratio=0.1,
    )
    state["cns_readiness"] = {
        "status": "compromised",
        "interpretation": "CNS processing is not reliable enough for technical consequence.",
        "session_ceiling": {"level": "low_consequence_repetition_only"},
    }
    write_json(
        tmp_path / "input" / f"planned_session_{target}.json",
        {
            "date": target,
            "generated_at": f"{target}T15:16:00+08:00",
            "status": "active",
            "session": _bounded_familiar_skill_session(),
        },
    )

    plan = build_today_plan(tmp_path, target, state=state)

    assert plan["session"]["type"] == "cns_recovery"
    assert plan["constraint_resolution"]["applied"][0]["source"] == "cns_readiness"


def test_red_readiness_still_overrides_bounded_familiar_skill_contract(tmp_path):
    load_context(tmp_path)
    target = "2026-07-25"
    state = _with_training_status(
        _yellow_state(target),
        feedback="RECOVERY_2",
        acwr_status="LOW",
        acwr_ratio=0.1,
    )
    state["readiness"] = {
        "readiness_level": "red",
        "readiness_score": 35,
        "hard_session_guidance": "avoid",
    }
    write_json(
        tmp_path / "input" / f"planned_session_{target}.json",
        {
            "date": target,
            "generated_at": f"{target}T15:16:00+08:00",
            "status": "active",
            "session": _bounded_familiar_skill_session(),
        },
    )

    plan = build_today_plan(tmp_path, target, state=state)

    assert plan["session"]["type"] == "recovery"
    assert plan["session"]["title"] == "Recovery day"
    assert plan["plan_source"]["type"] == "today_plan"


def test_constraints_override_authored_hard_session_for_cns_impairment(tmp_path):
    load_context(tmp_path)
    target = "2026-04-30"
    state = _green_state(target)
    state["cns_readiness"] = {
        "status": "compromised",
        "interpretation": "CNS processing is not reliable enough for technical consequence.",
        "session_ceiling": {"level": "low_consequence_repetition_only"},
    }
    write_json(
        tmp_path / "input" / f"planned_session_{target}.json",
        {
            "date": target,
            "session": {
                "title": "Authored Kiara repeatability",
                "type": "bike_quality",
                "duration_min": 90,
                "intensity": "hard",
            },
        },
    )

    plan = build_today_plan(tmp_path, target, state=state)

    assert plan["session"]["type"] == "cns_recovery"
    assert plan["constraint_resolution"]["applied"][0]["source"] == "cns_readiness"
    _assert_schema_v3_contract(plan["session"])


def test_constraints_override_authored_hard_session_for_garmin_downshift(tmp_path):
    load_context(tmp_path)
    target = "2026-04-30"
    state = _with_training_status(
        _green_state(target),
        acwr_status="HIGH",
        acwr_ratio=1.6,
    )
    write_json(
        tmp_path / "input" / f"planned_session_{target}.json",
        {
            "date": target,
            "session": {
                "title": "Authored tempo work",
                "type": "bike_quality",
                "duration_min": 90,
                "intensity": "hard",
            },
        },
    )

    plan = build_today_plan(tmp_path, target, state=state)

    assert plan["session"]["type"] == "garmin_aerobic_continuity"
    assert plan["constraint_resolution"]["applied"][0]["source"] == "garmin_diagnosis_arbitration"
    _assert_schema_v3_contract(plan["session"])


def test_sunday_sabbath_blocks_planned_exercise(tmp_path):
    load_context(tmp_path)
    write_wellness(tmp_path, "2026-05-03", sleepScore=90, hrvStatus="balanced", bodyBattery=80)

    plan = build_today_plan(tmp_path, "2026-05-03")

    assert plan["session"]["type"] == "scheduled_rest"
    assert plan["session"]["duration_min"] == 0
    assert plan["gym"]["status"] == "skip"
    assert plan["nutrition"]["during_session_carbs"] == "none"
    assert plan["decision_inputs"]["scheduled_rest"]["label"] == "Sabbath"
    assert any("sabbath" in item.lower() for item in plan["guardrails"])


def test_exact_date_athlete_sabbath_exception_allows_only_low_aerobic_indoor(tmp_path):
    load_context(tmp_path)
    target = "2026-05-03"
    state = _green_state(target)
    session = {
        "title": "One-off low-aerobic Suito",
        "type": "indoor_low_aerobic_sabbath_exception",
        "modality": "bike_indoor",
        "duration_min": 60,
        "intensity": "easy",
        "schema_version": 3,
        "contract_fields": SESSION_CONTRACT_FIELDS,
        "purpose": "Restore low-aerobic bike continuity.",
        "dose": {"main_set": "40-45 min easy."},
        "adaptation_hypothesis": "Low-cost aerobic work is absorbed without next-day debt.",
        "execution_rules": ["Stay conversational."],
        "expected_result": {"training_effect": "low aerobic"},
        "stop_rules": ["Stop if RPE rises above the easy cap."],
        "post_session_review_fields": ["stop_rule_outcome", "actual_rpe"],
    }
    write_json(
        tmp_path / "input" / f"planned_session_{target}.json",
        {
            "date": target,
            "generated_at": f"{target}T08:00:00+08:00",
            "status": "active",
            "sabbath_exception": {
                "date": target,
                "authorized_by": "athlete",
                "explicit_one_off": True,
                "scope": "indoor_low_aerobic_only",
                "recurring_rule_unchanged": True,
            },
            "session": session,
        },
    )

    plan = build_today_plan(tmp_path, target, state=state)

    assert plan["session"]["type"] == "indoor_low_aerobic_sabbath_exception"
    assert plan["plan_source"]["type"] == "input_planned_session"
    assert plan["decision_inputs"]["sabbath_exception"]["status"] == (
        "validated_exact_date_low_consequence_exception"
    )
    assert plan["gym"]["status"] == "skip"
    assert plan["nutrition"]["during_session_carbs"] == (
        "0-20 g/hour (optional if normally fed)"
    )
    assert any("one-off" in item.lower() for item in plan["guardrails"])


def test_sabbath_exception_rejects_hard_or_incomplete_session(tmp_path):
    load_context(tmp_path)
    target = "2026-05-03"
    state = _green_state(target)
    write_json(
        tmp_path / "input" / f"planned_session_{target}.json",
        {
            "date": target,
            "sabbath_exception": {
                "date": target,
                "authorized_by": "athlete",
                "explicit_one_off": True,
                "scope": "indoor_low_aerobic_only",
                "recurring_rule_unchanged": True,
            },
            "session": {
                "title": "Hard trainer",
                "type": "bike_quality",
                "modality": "bike_indoor",
                "duration_min": 60,
                "intensity": "hard",
            },
        },
    )

    plan = build_today_plan(tmp_path, target, state=state)

    assert plan["session"]["type"] == "scheduled_rest"
    assert plan["decision_inputs"]["sabbath_exception"] is None


def test_base_phase_hard_session_requires_activity_evidence(tmp_path):
    context = load_context(tmp_path)
    context["goal_progression"]["current_phase"] = "base_rebuild"
    save_context(context, tmp_path)
    write_wellness(tmp_path, "2026-05-21", sleepScore=90, hrvStatus="balanced", bodyBattery=80)
    write_training_status(tmp_path, "2026-05-21")

    plan = build_today_plan(tmp_path, "2026-05-21")

    assert plan["decision_inputs"]["phase"] == "base_rebuild"
    assert plan["session"]["intensity"] != "hard"
    assert any("activity data" in item.lower() for item in plan["guardrails"])


def test_latest_garmin_scale_weight_feeds_nutrition_when_today_is_empty(tmp_path):
    load_context(tmp_path)
    write_body_composition(
        tmp_path,
        "2026-05-11",
        weight=76080,
        bmi=24.0,
        body_fat=18.2,
        body_water=59.7,
        muscle_mass=35639,
        bone_mass=4469,
    )
    write_body_composition(tmp_path, "2026-05-16")

    trends = build_wellness_trends(tmp_path, "2026-05-16")
    plan = build_today_plan(tmp_path, "2026-05-16")

    composition = trends["latest_body_composition"]
    assert composition["date"] == "2026-05-11"
    assert composition["age_days"] == 5
    assert composition["body_weight_kg"] == 76.08
    assert composition["body_fat_pct"] == 18.2
    assert composition["muscle_mass_kg"] == 35.64
    assert plan["nutrition"]["daily_protein"] == "122-167 g"
    assert plan["nutrition"]["body_weight_basis"] == "76.08 kg from Garmin scale on 2026-05-11"


def test_mtb_heat_fueling_uses_canonical_90_to_150_min_targets_and_context_only_evidence(tmp_path):
    load_context_with_mtb_heat_targets(tmp_path)
    target = "2026-06-19"
    state = _green_state(target)
    state["athlete"] = {
        "body_weight_kg": 76.5,
        "body_weight_source": {"date": "2026-06-18"},
    }
    state["training_status_current"] = {
        "acclimation": {"heat_pct": 82.0, "heat_trend": "ACCLIMATIZED", "heat_date": target}
    }
    state["latest_session_evidence"] = {
        "date": "2026-06-18",
        "temperature": {"device_max_c": 35.0},
        "water_estimated_ml": 780,
        "device": {"recording_device": {"device_id": "PRIVATE-DEVICE"}},
    }
    write_json(
        tmp_path / "input" / f"planned_session_{target}.json",
        {
            "date": target,
            "session": {
                "title": "Kiara controlled durability",
                "type": "mtb_durability_enduro",
                "modality": "mtb",
                "duration_min": 120,
                "intensity": "moderate_hard",
            },
        },
    )

    plan = build_today_plan(tmp_path, target, state=state)
    nutrition = plan["nutrition"]

    assert nutrition["during_session_targets"] == {
        "source": "config/athlete_context.json:nutrition.mtb_heat_fueling_targets",
        "profile": "ride_90_to_150_min",
        "selection_reason": "MTB duration is within the 90-150 minute heat-fueling band.",
        "carbs_g_per_hour": [45, 75],
        "fluid_ml_per_hour": [500, 900],
        "sodium_mg_per_hour": [600, 1000],
    }
    assert nutrition["body_weight_basis"] == "76.5 kg from Garmin scale on 2026-06-18"
    assert nutrition["mtb_heat_target_basis"]
    assert nutrition["heat_context"]["heat_acclimation"]["heat_pct"] == 82.0
    assert nutrition["heat_context"]["latest_session_evidence"]["date"] == "2026-06-18"
    assert "PRIVATE-DEVICE" not in str(nutrition["heat_context"])
    assert "not a forecast" in nutrition["heat_context"]["weather_rule"]
    assert "never reduces" in nutrition["heat_context"]["acclimation_rule"]
    assert "Do not downshift solely" in nutrition["range_selection_rule"]


def test_mtb_race_practice_uses_over_150_profile_even_when_planned_duration_is_shorter(tmp_path):
    load_context_with_mtb_heat_targets(tmp_path)
    target = "2026-06-20"
    write_json(
        tmp_path / "input" / f"planned_session_{target}.json",
        {
            "date": target,
            "session": {
                "title": "Capped race practice",
                "type": "outdoor_mtb",
                "modality": "mtb",
                "race_practice": True,
                "duration_min": 80,
                "intensity": "moderate",
            },
        },
    )

    nutrition = build_today_plan(tmp_path, target, state=_green_state(target))["nutrition"]

    targets = nutrition["during_session_targets"]
    assert targets["profile"] == "over_150_min_or_race_practice"
    assert targets["carbs_g_per_hour"] == [60, 90]
    assert targets["fluid_ml_per_hour"] == [650, 1000]
    assert targets["sodium_mg_per_hour"] == [800, 1200]
    assert "Garmin scale weight" in nutrition["body_weight_basis"]


def test_non_mtb_session_keeps_generic_duration_fueling_targets(tmp_path):
    load_context(tmp_path)
    target = "2026-06-19"
    write_json(
        tmp_path / "input" / f"planned_session_{target}.json",
        {
            "date": target,
            "session": {
                "title": "Indoor aerobic work",
                "type": "bike_quality",
                "modality": "bike_indoor",
                "duration_min": 120,
                "intensity": "moderate",
            },
        },
    )

    nutrition = build_today_plan(tmp_path, target, state=_green_state(target))["nutrition"]

    assert nutrition["during_session_carbs"] == "30-60 g/hour"
    assert "during_session_targets" not in nutrition
