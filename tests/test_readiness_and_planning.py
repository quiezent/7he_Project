from coach_sync.context import add_modality_override, load_context, save_context, set_clearance
from coach_sync.io import write_json
from coach_sync.planning import build_today_plan
from coach_sync.readiness import build_readiness


def write_wellness(root, day, **fields):
    write_json(
        root / "snapshots" / f"garmin_wellness_{day}.json",
        {"date": day, "payloads": [{"ok": True, "data": fields}]},
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


def test_stale_checkin_is_ignored(tmp_path):
    load_context(tmp_path)
    write_wellness(tmp_path, "2026-04-29", sleepScore=90, hrvStatus="balanced")
    (tmp_path / "input").mkdir(exist_ok=True)
    (tmp_path / "input" / "daily_checkin.md").write_text(
        "date: 2026-04-28\npain: 5/10\nswelling: yes\n",
        encoding="utf-8",
    )

    readiness = build_readiness(tmp_path, "2026-04-29")

    assert readiness["readiness_level"] != "red"
    assert readiness["symptoms"]["pain"] is None
    assert any(reason["type"] == "stale_checkin" for reason in readiness["reasons"])


def test_red_symptoms_block_hard_session(tmp_path):
    load_context(tmp_path)
    write_wellness(tmp_path, "2026-04-29", sleepScore=90, hrvStatus="balanced")
    (tmp_path / "input").mkdir(exist_ok=True)
    (tmp_path / "input" / "daily_checkin.md").write_text(
        "date: 2026-04-29\npain: 5/10\nswelling: no\ngrip_tolerance: poor\n",
        encoding="utf-8",
    )

    readiness = build_readiness(tmp_path, "2026-04-29")
    plan = build_today_plan(tmp_path, "2026-04-29")

    assert readiness["readiness_level"] == "red"
    assert readiness["hard_session_guidance"] == "avoid"
    assert plan["session"]["type"] == "recovery"


def test_all_cleared_green_reentry_gets_outdoor_mtb_plan(tmp_path):
    load_context(tmp_path)
    write_wellness(tmp_path, "2026-04-29", sleepScore=90, hrvStatus="balanced", bodyBattery=80)

    plan = build_today_plan(tmp_path, "2026-04-29")

    assert plan["decision_inputs"]["clearance_all_cleared"] is True
    assert plan["decision_inputs"]["phase"] == "return_to_outdoor_reentry"
    assert plan["session"]["type"] == "outdoor_mtb"
    assert "daily_protein" in plan["nutrition"]


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


def test_uncleared_trail_gate_blocks_outdoor_plan(tmp_path):
    context = load_context(tmp_path)
    set_clearance(context, "trail", "pending", "2026-04-29", "test")
    save_context(context, tmp_path)
    write_wellness(tmp_path, "2026-04-29", sleepScore=90, hrvStatus="balanced")

    plan = build_today_plan(tmp_path, "2026-04-29")

    assert plan["decision_inputs"]["clearance_all_cleared"] is False
    assert plan["session"]["type"] == "recovery"


def test_modality_override_blocks_trail_plan(tmp_path):
    context = load_context(tmp_path)
    add_modality_override(
        context,
        "blocked",
        ["trail", "outdoor_biking"],
        "doctor wants one more week of indoor-only riding",
        "2026-04-29",
    )
    save_context(context, tmp_path)
    write_wellness(tmp_path, "2026-04-29", sleepScore=90, hrvStatus="balanced", bodyBattery=80)

    plan = build_today_plan(tmp_path, "2026-04-29")

    assert plan["session"]["type"] == "indoor_bike"
    assert plan["gym"]["status"] == "primer"
    assert any("Active modality override" in item for item in plan["guardrails"])


def test_reentry_mtb_cap_blocks_more_mtb_exposure(tmp_path):
    load_context(tmp_path)
    write_wellness(tmp_path, "2026-05-04", sleepScore=90, hrvStatus="balanced", bodyBattery=80)
    for idx, day in enumerate(("2026-05-04", "2026-05-03", "2026-05-02"), start=1):
        write_json(
            tmp_path / "activities" / f"mtb_{idx}.json",
            {
                "activityId": idx,
                "activityName": "MTB ride",
                "activityType": {"typeKey": "mountain_biking"},
                "startTimeLocal": f"{day} 08:00:00",
                "duration": 3600,
            },
        )

    plan = build_today_plan(tmp_path, "2026-05-04")

    assert plan["session"]["type"] == "indoor_bike"
    assert any("exposure cap" in item.lower() for item in plan["guardrails"])


def test_base_phase_hard_session_requires_activity_evidence(tmp_path):
    context = load_context(tmp_path)
    context["goal_progression"]["current_phase"] = "base_rebuild"
    save_context(context, tmp_path)
    write_wellness(tmp_path, "2026-05-21", sleepScore=90, hrvStatus="balanced", bodyBattery=80)

    plan = build_today_plan(tmp_path, "2026-05-21")

    assert plan["decision_inputs"]["phase"] == "base_rebuild"
    assert plan["session"]["intensity"] != "hard"
    assert any("activity data" in item.lower() for item in plan["guardrails"])
