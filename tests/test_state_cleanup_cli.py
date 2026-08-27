import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from coach_sync.cleanup import cleanup_derived
from coach_sync.cli import main
from coach_sync.context import load_context
from coach_sync.io import write_json
from coach_sync.state import (
    _cached_or_build_report,
    _guard_current_model_predictions,
    build_current_state,
)


def test_current_state_writes_training_snapshots(tmp_path):
    load_context(tmp_path)
    write_json(
        tmp_path / "snapshots" / "garmin_wellness_2026-04-29.json",
        {"date": "2026-04-29", "payloads": [{"data": {"sleepScore": 80}}]},
    )

    state = build_current_state(tmp_path, "2026-04-29")

    assert state["phase"]["name"] == "base_rebuild"
    assert state["bike_continuity_accountability"]["targets"][
        "preferred_unique_bike_days"
    ] == 5
    assert state["latest_session_response"]["status"] == "feedback_missing"
    assert state["adaptive_training"]["artifact_type"] == "adaptive_training_programming_state"
    assert (tmp_path / "snapshots" / "training_load.json").exists()
    assert (tmp_path / "snapshots" / "current_state.json").exists()
    assert (tmp_path / "snapshots" / "adaptive_training.json").exists()


def test_exact_date_all_day_rest_surfaces_without_refreshing_core_readiness(tmp_path):
    load_context(tmp_path)
    write_json(
        tmp_path / "snapshots" / "garmin_wellness_2026-07-16.json",
        {
            "date": "2026-07-16",
            "payloads": [
                {
                    "label": "get_stats",
                    "ok": True,
                    "data": {
                        "calendarDate": "2026-07-16",
                        "restingHeartRate": 50,
                        "bodyBatteryMostRecentValue": 50,
                    },
                }
            ],
        },
    )
    baseline = build_current_state(tmp_path, "2026-07-17", refresh_models=False)

    tz = ZoneInfo("Asia/Kuala_Lumpur")
    current = datetime(2026, 7, 17, 10, 0, tzinfo=tz)
    stress_rows = []
    battery_rows = []
    for _ in range(47):
        stamp = int(current.timestamp() * 1000)
        stress_rows.append([stamp, 18])
        battery_rows.append([stamp, "MEASURED", 65 if current.hour < 11 else 70, 2.0])
        current += timedelta(minutes=3)
    write_json(
        tmp_path / "snapshots" / "garmin_wellness_2026-07-17.json",
        {
            "date": "2026-07-17",
            "payloads": [
                {
                    "label": "get_all_day_stress",
                    "ok": True,
                    "status": "success",
                    "attempted_at": "2026-07-17T12:30:00+08:00",
                    "data": {
                        "calendarDate": "2026-07-17",
                        "stressValuesArray": stress_rows,
                        "bodyBatteryValuesArray": battery_rows,
                    },
                }
            ],
        },
    )
    write_json(
        tmp_path / "input" / "feedback_2026-07-17.json",
        {
            "date": "2026-07-17",
            "sleep_work_timing_review": {
                "nap_status": "completed",
                "nap_start_local": "10:20",
                "nap_end_local": "11:50",
                "sleep_inertia_minutes": 10,
                "post_nap_clarity_10_range": [7, 8],
            },
        },
    )

    state = build_current_state(tmp_path, "2026-07-17", refresh_models=False)

    assert state["data_freshness"]["latest_wellness_date"] == "2026-07-16"
    assert state["readiness"]["readiness_score"] == baseline["readiness"]["readiness_score"]
    assert state["readiness"]["readiness_level"] == baseline["readiness"]["readiness_level"]
    assert state["cns_readiness"]["score"] == baseline["cns_readiness"]["score"]
    assert state["cns_readiness"]["session_ceiling"] == baseline["cns_readiness"][
        "session_ceiling"
    ]
    assert state["rest_recharge_window"]["classification"]["label"] == "restorative"
    assert state["wearable_coverage"]["classification"]["label"] == (
        "insufficient_series_coverage"
    )
    assert state["wearable_coverage"]["decision_use"][
        "low_stress_positive_reward_eligible"
    ] is False
    assert (tmp_path / "snapshots" / "wearable_coverage.json").exists()


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


def test_fresh_wear_gap_masks_same_day_cached_low_stress_predictions():
    target = date(2026, 7, 19)
    body, predictor = _guard_current_model_predictions(
        {
            "date": target.isoformat(),
            "latest_row": {
                "date": target.isoformat(),
                "features": {"avg_stress": 16},
            },
            "latest_prediction": {"leaf": {"prediction": True}},
        },
        {
            "date": target.isoformat(),
            "validation": {"utility": "useful"},
            "today_prediction": {
                "date": target.isoformat(),
                "basis_date": target.isoformat(),
                "features": {"today_avg_stress": 16},
                "prediction": {"prediction_next_day_ready": True},
                "prediction_path": [{"feature": "today_avg_stress"}],
                "warnings": [],
            },
        },
        {
            "date": target.isoformat(),
            "decision_use": {"low_stress_positive_reward_eligible": False},
        },
        target,
    )

    assert body["latest_prediction"] is None
    assert body["wearable_coverage_cache_guard"]["applied"] is True
    assert predictor["today_prediction"]["prediction"] is None
    assert predictor["today_prediction"]["status"] == (
        "unavailable_coverage_superseded"
    )
    assert predictor["wearable_coverage_cache_guard"]["applied"] is True


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


def test_cleanup_preserves_raw_detail_and_removes_only_transient_current_alias(tmp_path):
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

    assert transient_detail.exists()
    assert not transient_current.exists()
    assert dated_loop.exists()
    assert current_state.exists()
    assert str(transient_detail.resolve()) not in result["removed_or_would_remove"]
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
    assert (tmp_path / "snapshots" / "adaptive_training.txt").exists()
