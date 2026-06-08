import json

from coach_sync.cleanup import cleanup_derived
from coach_sync.cli import main
from coach_sync.context import load_context
from coach_sync.io import write_json
from coach_sync.state import build_current_state


def test_current_state_writes_training_snapshots(tmp_path):
    load_context(tmp_path)
    write_json(
        tmp_path / "snapshots" / "garmin_wellness_2026-04-29.json",
        {"date": "2026-04-29", "payloads": [{"data": {"sleepScore": 80}}]},
    )

    state = build_current_state(tmp_path, "2026-04-29")

    assert state["phase"]["name"] == "base_rebuild"
    assert (tmp_path / "snapshots" / "training_load.json").exists()
    assert (tmp_path / "snapshots" / "current_state.json").exists()


def test_cleanup_does_not_touch_activities(tmp_path):
    load_context(tmp_path)
    protected = tmp_path / "activities" / "__pycache__"
    removable = tmp_path / "src" / "__pycache__"
    protected.mkdir(parents=True)
    removable.mkdir(parents=True)

    result = cleanup_derived(tmp_path, apply=True)

    assert protected.exists()
    assert not removable.exists()
    assert str(protected) not in result["removed_or_would_remove"]


def test_cli_rebuild_creates_valid_json(tmp_path, capsys):
    code = main(["rebuild", "--root", str(tmp_path)])
    captured = capsys.readouterr()

    assert code == 0
    payload = json.loads(captured.out)
    assert payload["live_sync"]["reason"] == "rebuild_only"
    assert (tmp_path / "snapshots" / "current_state.json").exists()
    assert (tmp_path / "snapshots" / "daily_brief.txt").exists()
    assert (tmp_path / "snapshots" / "coach_packet.txt").exists()
