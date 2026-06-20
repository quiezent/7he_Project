import json

import coach_sync.coach_packet as coach_packet_module
from coach_sync.cli import main
from coach_sync.coach_packet import build_coach_packet
from coach_sync.context import load_context
from coach_sync.io import write_json


def _write_green_wellness(root, day: str) -> None:
    write_json(
        root / "snapshots" / f"garmin_wellness_{day}.json",
        {
            "date": day,
            "payloads": [
                {
                    "ok": True,
                    "data": {
                        "calendarDate": day,
                        "sleepScore": 90,
                        "hrvStatus": "balanced",
                        "bodyBattery": 82,
                        "bodyBatteryAtWakeTime": 86,
                    },
                }
            ],
        },
    )


def _write_training_status(root, day: str) -> None:
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
                                },
                            }
                        }
                    }
                },
            },
        },
    )


def _write_activity(root, day: str) -> None:
    write_json(
        root / "activities" / f"activity_{day}.json",
        {
            "activityId": int(day.replace("-", "")),
            "activityName": "Indoor Cycling",
            "activityType": {"typeKey": "indoor_cycling"},
            "startTimeLocal": f"{day} 10:00:00",
            "duration": 3600,
            "activityTrainingLoad": 55,
        },
    )


def test_coach_packet_writes_decision_surface_and_triages_models(tmp_path):
    load_context(tmp_path)
    _write_green_wellness(tmp_path, "2026-04-29")
    _write_training_status(tmp_path, "2026-04-29")
    _write_activity(tmp_path, "2026-04-29")

    packet = build_coach_packet(tmp_path, "2026-04-29")

    assert packet["artifact_type"] == "coach_decision_packet"
    assert packet["stack_path"]["chosen_path"] == "evidence_triage_over_more_models"
    assert packet["today_call"]["session"]["type"] == "endurance_skills"
    assert any(item["name"] == "Readiness" for item in packet["evidence"]["trusted"])
    assert any(
        item["name"] == "Garmin diagnosis arbitration"
        for item in packet["evidence"]["trusted"]
    )
    assert any(
        item["name"] == "Next-day training response tree"
        for item in packet["evidence"]["ignored_for_decision"]
    )
    assert (tmp_path / "snapshots" / "coach_packet.json").exists()
    assert (tmp_path / "snapshots" / "coach_packet.txt").exists()


def test_coach_packet_cli_command(tmp_path, capsys):
    load_context(tmp_path)
    _write_green_wellness(tmp_path, "2026-04-29")

    code = main(["coach-packet", "--root", str(tmp_path), "--date", "2026-04-29"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert code == 0
    assert payload["artifacts"]["json"] == "snapshots/coach_packet.json"


def test_coach_packet_promotes_sabbath_constraint(tmp_path):
    load_context(tmp_path)
    _write_green_wellness(tmp_path, "2026-05-03")

    packet = build_coach_packet(tmp_path, "2026-05-03")

    assert packet["today_call"]["stance"] == "sabbath_rest"
    assert packet["today_call"]["session"]["type"] == "scheduled_rest"
    assert packet["evidence"]["trusted"][0]["name"] == "Scheduled rest"


def test_coach_packet_reuses_same_date_state_and_plan_artifacts(tmp_path, monkeypatch):
    load_context(tmp_path)
    write_json(
        tmp_path / "snapshots" / "current_state.json",
        {
            "date": "2026-06-08",
            "readiness": {
                "readiness_level": "yellow",
                "readiness_score": 65,
                "confidence": "medium",
                "hard_session_guidance": "caution",
                "reasons": [],
            },
            "data_freshness": {
                "status": "current",
                "message": "Garmin wellness data is current.",
                "activity_data": {
                    "status": "current",
                    "message": "Recent activity data is available.",
                },
            },
            "phase": {"name": "base_rebuild", "reason": "test"},
        },
    )
    write_json(
        tmp_path / "snapshots" / "today_plan.json",
        {
            "date": "2026-06-08",
            "session": {
                "title": "Easy bike continuity",
                "type": "outdoor_bike_optional",
                "duration_min": 45,
                "intensity": "easy",
            },
        },
    )

    def fail_rebuild(*_args, **_kwargs):
        raise AssertionError("coach packet should reuse same-date artifacts")

    monkeypatch.setattr(coach_packet_module, "build_current_state", fail_rebuild)

    packet = build_coach_packet(tmp_path, "2026-06-08")

    assert packet["date"] == "2026-06-08"
    assert packet["today_call"]["session"]["title"] == "Easy bike continuity"


def test_coach_packet_stance_shows_adaptive_upgrade_option(tmp_path):
    load_context(tmp_path)
    state = {
        "date": "2026-06-08",
        "readiness": {
            "readiness_level": "yellow",
            "readiness_score": 68,
            "confidence": "medium",
            "hard_session_guidance": "caution",
            "reasons": [],
        },
        "data_freshness": {
            "status": "current",
            "activity_data": {"status": "current"},
        },
        "phase": {"name": "base_rebuild"},
        "training_status_current": {},
    }
    plan = {
        "date": "2026-06-08",
        "session": {
            "title": "Easy planned trail check",
            "type": "outdoor_mtb",
            "duration_min": 45,
            "intensity": "recovery_skill",
            "adaptive_upgrade_option": {
                "source": "garmin_diagnosis_arbitration",
                "ceiling": "controlled_mtb_repeatability",
            },
        },
        "decision_inputs": {
            "garmin_arbitration": {
                "recommended_action": "controlled_upgrade",
                "summary": "Garmin diagnosis permits a controlled upgrade, not an open-ended hard day.",
            }
        },
    }

    packet = build_coach_packet(tmp_path, "2026-06-08", state=state, plan=plan)

    assert packet["today_call"]["stance"] == "controlled_upgrade_option"
