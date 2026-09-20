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


def test_base_phase_green_day_gets_trainable_plan(tmp_path):
    load_context(tmp_path)
    write_wellness(tmp_path, "2026-04-29", sleepScore=90, hrvStatus="balanced", bodyBattery=80)

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


def _assert_schema_v3_contract(session: dict) -> None:
    assert session["schema_version"] == 3
    assert session["contract_fields"] == SESSION_CONTRACT_FIELDS
    for field in SESSION_CONTRACT_FIELDS:
        assert session.get(field), field
    assert isinstance(session["dose"], dict)
    assert isinstance(session["execution_rules"], list)
    assert isinstance(session["stop_rules"], list)
    assert isinstance(session["post_session_review_fields"], list)


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


def test_base_phase_hard_session_requires_activity_evidence(tmp_path):
    context = load_context(tmp_path)
    context["goal_progression"]["current_phase"] = "base_rebuild"
    save_context(context, tmp_path)
    write_wellness(tmp_path, "2026-05-21", sleepScore=90, hrvStatus="balanced", bodyBattery=80)

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
