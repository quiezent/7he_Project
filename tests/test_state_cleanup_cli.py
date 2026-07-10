import json
from datetime import date

from coach_sync.cleanup import cleanup_derived
from coach_sync.cli import main
from coach_sync.context import load_context
from coach_sync.io import write_json
from coach_sync.state import _cached_or_build_report, build_current_state


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


def test_decision_only_cache_rebuilds_when_target_date_changes(tmp_path):
    write_json(
        tmp_path / "snapshots" / "model.json",
        {"date": "2026-04-28", "value": "stale"},
    )
    calls = []

    def builder(root, target):
        calls.append(target)
        return {"date": target.isoformat(), "value": "fresh"}

    report = _cached_or_build_report(
        tmp_path,
        "model.json",
        builder,
        date(2026, 4, 29),
        refresh_models=False,
    )

    assert report["value"] == "fresh"
    assert calls == [date(2026, 4, 29)]


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


def test_cleanup_removes_only_transient_snapshot_json(tmp_path):
    load_context(tmp_path)
    snapshots = tmp_path / "snapshots"
    transient_detail = snapshots / "activity_detail_123.json"
    transient_current = snapshots / "activity_loop_load_current.json"
    dated_loop = snapshots / "activity_loop_load_2026-06-10_123.json"
    current_state = snapshots / "current_state.json"
    transient_detail.write_text("{}", encoding="utf-8")
    transient_current.write_text("{}", encoding="utf-8")
    dated_loop.write_text("{}", encoding="utf-8")
    current_state.write_text("{}", encoding="utf-8")

    result = cleanup_derived(tmp_path, apply=True)

    assert not transient_detail.exists()
    assert not transient_current.exists()
    assert dated_loop.exists()
    assert current_state.exists()
    assert str(transient_detail.resolve()) in result["removed_or_would_remove"]
    assert str(transient_current.resolve()) in result["removed_or_would_remove"]
    assert str(dated_loop.resolve()) not in result["removed_or_would_remove"]


def test_cli_rebuild_creates_valid_json(tmp_path, capsys):
    code = main(["rebuild", "--root", str(tmp_path)])
    captured = capsys.readouterr()

    assert code == 0
    payload = json.loads(captured.out)
    assert payload["live_sync"]["reason"] == "rebuild_only"
    assert (tmp_path / "snapshots" / "current_state.json").exists()
    assert (tmp_path / "snapshots" / "daily_brief.txt").exists()
    assert (tmp_path / "snapshots" / "coach_packet.txt").exists()
