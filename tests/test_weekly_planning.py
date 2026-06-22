from coach_sync.context import load_context, save_context
from coach_sync.planning import SESSION_CONTRACT_FIELDS
from coach_sync.weekly_planning import build_weekly_plan


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
    assert (tmp_path / "snapshots" / "weekly_plan.json").exists()
    assert (tmp_path / "snapshots" / "weekly_plan_2026-W26.json").exists()
    assert (tmp_path / "snapshots" / "weekly_plan.txt").exists()

    trainable = [session for session in plan["sessions"] if session["type"] != "scheduled_rest"]
    assert trainable
    for session in trainable:
        _assert_contract(session)

    sunday = plan["sessions"][-1]
    assert sunday["day_name"] == "Sunday"
    assert sunday["type"] == "scheduled_rest"
    assert sunday["duration_min"] == 0


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


def test_weekly_plan_green_monday_can_start_with_tempo(tmp_path):
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
    assert monday["type"] == "indoor_tempo_torque"
    assert monday["priority"] == "key_engine"
