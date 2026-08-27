from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from coach_sync.adaptive_training import (
    _parse_roadmap_block,
    build_adaptive_training_state,
)


ROADMAP = """# Test roadmap

| Dates | Block intent | What this block must buy | Constraints and review |
| --- | --- | --- | --- |
| Wed 2026-08-26 to Sun 2026-08-30 | Absorption and conference entry | Absorb torque | Do not chase missed volume. |
| Mon 2026-08-31 to Sun 2026-09-06 | Re-entry and course preparation | Restore rhythm | Two MTB exposures. |
| Mon 2026-09-07 to Sun 2026-09-13 | Race-specific build and Denai recce window | Learn course | Recce consumes Enduro. |
| Mon 2026-09-14 to Fri 2026-09-18 | Taper and sharpening | Reduce fatigue | Reduce volume. |
| Sat 2026-09-19 | PDR26 official practice | Confirm lines | Event exposure. |
| Sun 2026-09-20 | PDR26 race | Execute | Named race. |
| Mon 2026-09-21 | Replacement Sabbath | Absorb | Hard rest. |

| Week | Dates | Phase | Primary intent and promotion evidence |
| --- | --- | --- | --- |
| 1 | Mon 2026-09-28 to Sun 2026-10-04 | Foundation 1 | Re-establish five bike touches. |
| 4 | Mon 2026-10-19 to Sun 2026-10-25 | Consolidation 1 | Reduce duration. |
"""


def _root(tmp_path: Path) -> Path:
    (tmp_path / "input").mkdir()
    (tmp_path / "snapshots").mkdir()
    (tmp_path / "activities").mkdir()
    (tmp_path / "input" / "expert_enduro_roadmap.md").write_text(ROADMAP, encoding="utf-8")
    return tmp_path


def _context() -> dict:
    return {
        "athlete": {
            "timezone": "Asia/Kuala_Lumpur",
            "event_focus": {
                "current_milestone": {
                    "name": "four_twin_peaks_enduro_reacquisition",
                    "status": "active",
                    "latest_progression_evidence": {
                        "verified_level": "Four short descents, not four complete cycles.",
                        "next_gate": "Test three complete Twin Peaks cycles.",
                    },
                }
            },
        }
    }


def _feedback(root: Path, day: str, review: dict, *, text: str = "") -> None:
    payload = {
        "date": day,
        "entries": [
            {
                "activity_id": day,
                "reported_context": {"session_intent": text},
                "session_contract_review": review,
                "objective_session_evidence": {
                    "main_block": {"power_hr_decoupling_pct": 3.0}
                },
            }
        ],
    }
    (root / "input" / f"feedback_{day}.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def _planned(
    root: Path,
    day: str,
    session_type: str,
    title: str,
    *,
    modality: str | None = None,
    equipment_key: str | None = None,
) -> None:
    session = {"type": session_type, "title": title}
    if modality is not None:
        session["modality"] = modality
    if equipment_key is not None:
        session["equipment_key"] = equipment_key
    (root / "input" / f"planned_session_{day}.json").write_text(
        json.dumps({"date": day, "session": session}),
        encoding="utf-8",
    )


def _bike(day: str, *, category: str = "bike_indoor", load: float = 45, duration: float = 60) -> dict:
    return {
        "date": day,
        "category": category,
        "counts_for_training_load": True,
        "duration_min": duration,
        "training_load": load,
        "aerobic_te": 2.5,
        "anaerobic_te": 0,
        "intensity_factor": 0.57,
    }


def test_triggered_but_continued_blocks_torque_promotion(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _planned(root, "2026-08-25", "indoor_tempo_torque", "3x8 tempo torque")
    _feedback(
        root,
        "2026-08-25",
        {"stop_rule_outcome": "triggered_but_continued", "local_leg_rpe_0_to_10": 8},
        text="3x8 tempo torque",
    )

    artifact = build_adaptive_training_state(
        root,
        "2026-08-27",
        context=_context(),
        activities=[],
        readiness={"readiness_level": "green"},
        cns_readiness={"status": "ready"},
        training_status={},
    )

    torque = artifact["progression_tracks"]["engine"]["torque"]
    assert torque["current_rung"] == "3x8_min"
    assert torque["decision"] == "hold_no_promotion"
    assert artifact["progression_decision"]["program_action"] == "absorb_and_hold"
    assert artifact["feedback_ledger_summary"]["out_of_policy_sessions"] == [
        {
            "date": "2026-08-25",
            "family": "engine_torque",
            "stop_rule_outcome": "triggered_but_continued",
        }
    ]


def test_two_absorbed_standard_doses_open_duration_only(tmp_path: Path) -> None:
    root = _root(tmp_path)
    for day in ("2026-08-26", "2026-08-27"):
        _planned(root, day, "indoor_low_aerobic", "Indoor low-aerobic continuity")
        _feedback(
            root,
            day,
            {
                "stop_rule_outcome": "not_triggered",
                "global_session_rpe_0_to_10": 3,
                "next_morning_response": "normal legs and function",
            },
            text="low-aerobic steady endurance",
        )

    artifact = build_adaptive_training_state(
        root,
        "2026-08-27",
        context=_context(),
        activities=[_bike("2026-08-26"), _bike("2026-08-27")],
        readiness={"readiness_level": "green"},
        cns_readiness={"status": "ready"},
        training_status={},
    )

    endurance = artifact["progression_tracks"]["endurance"]
    assert endurance["decision"] == "promote_duration"
    assert endurance["gate_progress"]["clean_with_explicit_next_day_response"] == 2
    assert len(
        {
            artifact["progression_decision"]["active_lever"]
        }
    ) == 1


def test_current_calendar_week_drives_budget_and_density_breach(tmp_path: Path) -> None:
    root = _root(tmp_path)
    activities = [
        _bike("2026-10-19", load=80),
        _bike("2026-10-20", load=80),
        _bike("2026-10-21", load=80),
        _bike("2026-10-22", load=80),
        _bike("2026-10-18"),
    ]
    artifact = build_adaptive_training_state(
        root,
        "2026-10-22",
        context=_context(),
        activities=activities,
        readiness={"readiness_level": "green"},
        cns_readiness={"status": "ready"},
        training_status={},
    )

    current = artifact["weekly_budget"]["current_calendar_week"]
    assert current["unique_bike_days"] == 4
    assert current["meaningful_cost_days"] == 4
    assert artifact["target_shape"]["meaningful_cost_days_max"] == 2
    assert any(
        item["type"] == "meaningful_cost_budget_breached"
        for item in artifact["programming_audit"]["items"]
    )


def test_future_feedback_cannot_affect_historical_target(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _planned(root, "2026-08-28", "indoor_tempo_torque", "3x8 tempo torque")
    _feedback(
        root,
        "2026-08-28",
        {"stop_rule_outcome": "triggered_but_continued"},
        text="3x8 tempo torque",
    )

    artifact = build_adaptive_training_state(
        root,
        "2026-08-27",
        context=_context(),
        activities=[],
        readiness={"readiness_level": "green"},
        cns_readiness={"status": "ready"},
        training_status={},
    )

    assert artifact["progression_tracks"]["engine"]["torque"]["latest_evidence"] is None
    assert artifact["feedback_ledger_summary"]["out_of_policy_sessions"] == []


def test_roadmap_resolves_event_replacement_and_build_modes(tmp_path: Path) -> None:
    root = _root(tmp_path)
    assert _parse_roadmap_block(root, date(2026, 8, 27))["program_mode"] == "absorption"
    assert _parse_roadmap_block(root, date(2026, 9, 20))["program_mode"] == "event_race"
    assert _parse_roadmap_block(root, date(2026, 9, 21))["program_mode"] == "replacement_sabbath"
    assert _parse_roadmap_block(root, date(2026, 9, 28))["program_mode"] == "build"
    assert _parse_roadmap_block(root, date(2026, 10, 20))["program_mode"] == "consolidation"


def test_short_descents_never_satisfy_complete_cycle_proof(tmp_path: Path) -> None:
    root = _root(tmp_path)
    artifact = build_adaptive_training_state(
        root,
        "2026-08-27",
        context=_context(),
        activities=[],
        readiness={"readiness_level": "green"},
        cns_readiness={"status": "ready"},
        training_status={},
    )
    technical = artifact["progression_tracks"]["technical"]
    assert technical["verified_rung"] == "four_short_descents_quality_density_not_complete_cycles"
    assert "three complete Twin Peaks" in technical["next_proof"]


def test_demo_mtb_consumes_cost_but_not_protected_role(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _feedback(
        root,
        "2026-08-24",
        {
            "stop_rule_outcome": "not_triggered",
            "technical_quality_notes": "Levo SL test ride on 2K.",
        },
        text="Short test ride of a Specialized Levo SL rather than the planned workout.",
    )
    payload = json.loads((root / "input" / "feedback_2026-08-24.json").read_text(encoding="utf-8"))
    payload["entries"][0]["objective_ebike_evidence"] = {"assist_mode": "observed"}
    (root / "input" / "feedback_2026-08-24.json").write_text(json.dumps(payload), encoding="utf-8")

    artifact = build_adaptive_training_state(
        root,
        "2026-08-27",
        context=_context(),
        activities=[_bike("2026-08-24", category="mtb", load=85, duration=25)],
        readiness={"readiness_level": "green"},
        cns_readiness={"status": "ready"},
        training_status={},
    )
    budget = artifact["weekly_budget"]
    assert budget["current_calendar_week"]["unique_mtb_days"] == 1
    assert budget["current_calendar_week"]["meaningful_cost_days"] == 0
    assert budget["current_calendar_week"]["possible_meaningful_cost_days"] == 1
    assert budget["protected_mtb_role_days_completed"] == 0
    assert budget["mtb_days_remaining_to_protect"] == 1


def test_explicit_enduro_contract_sets_remaining_protected_role(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _planned(
        root,
        "2026-08-28",
        "mtb_race_bike_skill_transfer",
        "Enduro two-cycle race-bike transfer",
        modality="mtb",
        equipment_key="specialized_enduro",
    )
    artifact = build_adaptive_training_state(
        root,
        "2026-08-27",
        context=_context(),
        activities=[_bike("2026-08-26"), _bike("2026-08-27")],
        readiness={"readiness_level": "green"},
        cns_readiness={"status": "ready"},
        training_status={},
    )

    selection = artifact["protected_mtb_role_selection"]
    role = next(
        item
        for item in artifact["recommended_week_roles"]
        if item["role"] == "protected_enduro_durability_or_race_transfer"
    )
    assert selection["selection_basis"] == "explicit_schema_v3_contract"
    assert selection["date"] == "2026-08-28"
    assert role["planned_date"] == "2026-08-28"
    assert role["density_fit"] == "uses_remaining_meaningful_slot"


def test_missing_next_day_response_is_pending_not_absorbed(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _planned(root, "2026-08-27", "indoor_low_aerobic", "Indoor low-aerobic continuity")
    _feedback(
        root,
        "2026-08-27",
        {"stop_rule_outcome": "not_triggered", "global_session_rpe_0_to_10": 3},
        text="Indoor low-aerobic continuity",
    )
    artifact = build_adaptive_training_state(
        root,
        "2026-08-27",
        context=_context(),
        activities=[_bike("2026-08-27")],
        readiness={"readiness_level": "green"},
        cns_readiness={"status": "ready"},
        training_status={},
    )
    assert artifact["absorption_state"]["status"] == "pending"
    assert artifact["progression_tracks"]["endurance"]["decision"] == "hold_and_collect_absorption"


def test_complete_cycle_proof_requires_every_explicit_gate(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _feedback(
        root,
        "2026-09-10",
        {
            "stop_rule_outcome": "not_triggered",
            "complete_twin_peaks_cycles": 3,
            "final_climb_rpe_0_to_10": 6,
            "first_vs_final_technical_delta_max_0_to_10": 1,
            "braking_fatigue_0_to_10": 2,
            "upper_body_fatigue_0_to_10": 3,
            "reactive_braking": "none",
            "rescue": "none",
            "near_miss": "none",
            "late_session_skill_fade": "none",
            "fueling_carbs_g_per_hour": 60,
            "fluid_ml_per_hour": 750,
            "sodium_mg_per_hour": 800,
            "next_morning_response": "clean",
            "technical_quality_notes": "Twin Peaks Pure Quill trail condition dry.",
        },
        text="Three complete Twin Peaks Pure Quill cycles; trail condition dry.",
    )
    artifact = build_adaptive_training_state(
        root,
        "2026-09-10",
        context=_context(),
        activities=[],
        readiness={"readiness_level": "green"},
        cns_readiness={"status": "ready"},
        training_status={},
    )

    proof = artifact["progression_tracks"]["technical"]["complete_cycle_proof_evaluation"]
    assert proof["decision"] == "eligible_for_head_coach_promotion_review"
    assert all(value is True for value in proof["gates"].values())
