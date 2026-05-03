import json

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


def test_coach_packet_writes_decision_surface_and_triages_models(tmp_path):
    load_context(tmp_path)
    _write_green_wellness(tmp_path, "2026-04-29")

    packet = build_coach_packet(tmp_path, "2026-04-29")

    assert packet["artifact_type"] == "coach_decision_packet"
    assert packet["stack_path"]["chosen_path"] == "evidence_triage_over_more_models"
    assert packet["today_call"]["session"]["type"] == "outdoor_mtb"
    assert any(item["name"] == "Readiness" for item in packet["evidence"]["trusted"])
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
