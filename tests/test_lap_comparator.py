from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from coach_sync.io import write_json
from coach_sync.lap_comparator import compare_laps_by_distance, find_movement_anchor, resample_on_distance


def _artifact_for_lap(
    tmp_path: Path,
    activity_id: str,
    lap_start_ms: int,
    duration_s: float,
    points: list[tuple[float, float, float]],
) -> None:
    start_time = datetime(2026, 6, 10, 5, 0, 0, tzinfo=timezone.utc) + timedelta(milliseconds=lap_start_ms)
    start_iso = start_time.strftime("%Y-%m-%dT%H:%M:%S.0")
    metric_rows = []
    for timestamp_ms, distance_m, speed_mps in points:
        metric_rows.append(
            {
                "metrics": [
                    timestamp_ms,
                    distance_m,
                    speed_mps,
                    3.14,
                    101.88,
                ]
            }
        )

    write_json(
        tmp_path / "snapshots" / f"activity_detail_{activity_id}.json",
        {
            "activity_id": activity_id,
            "calls": {
                "splits": {
                    "ok": True,
                    "data": {
                        "lapDTOs": [
                            {
                                "lapIndex": 4,
                                "startTimeGMT": start_iso,
                                "duration": duration_s,
                                "distance": points[-1][1] - points[0][1],
                                "movingDuration": duration_s,
                            }
                        ]
                    },
                },
                "details": {
                    "ok": True,
                    "data": {
                        "metricDescriptors": [
                            {"key": "directTimestamp", "metricsIndex": 0},
                            {"key": "sumDistance", "metricsIndex": 1},
                            {"key": "directSpeed", "metricsIndex": 2},
                            {"key": "directLatitude", "metricsIndex": 3},
                            {"key": "directLongitude", "metricsIndex": 4},
                        ],
                        "activityDetailMetrics": metric_rows,
                    },
                },
            },
        },
    )


def _build_track(
    start_ms: int,
    moving_seconds: int,
    speed_mps: float,
    *,
    static_prefix_seconds: int = 0,
) -> list[tuple[float, float, float]]:
    points = []
    t_ms = start_ms
    dist = 0.0
    for _ in range(static_prefix_seconds):
        points.append((float(t_ms), dist, 0.0))
        t_ms += 1000
    for _ in range(moving_seconds):
        dist += speed_mps
        points.append((float(t_ms), dist, speed_mps))
        t_ms += 1000
    return points


def _build_mismatch_speed_track(
    start_ms: int,
    speed_profile: list[float],
    dt: int = 1_000,
) -> list[tuple[float, float, float]]:
    points = []
    t_ms = start_ms
    dist = 0.0
    for speed in speed_profile:
        dist += speed * dt / 1000.0
        points.append((float(t_ms), dist, speed))
        t_ms += dt
    return points


def test_find_movement_anchor_dist_change_threshold():
    times = [0.0, 1000.0, 2000.0]
    dist = [0.0, 0.0, 1.2]
    speed = [0.0, 0.0, 0.0]
    assert find_movement_anchor(times, dist, speed, speed_eps=0.5, dist_eps=1.0) == 2


def test_resample_on_distance_aligns_even_grid(tmp_path):
    points = [(0.0, 0.0, 5.0), (1000.0, 10.0, 5.0), (2000.0, 20.0, 5.0)]
    metric_map = {"directSpeed": [row[2] for row in points]}
    rows = resample_on_distance([row[1] for row in points], metric_map, [row[0] / 1000.0 for row in points], grid_m=5.0)
    assert rows[0]["distance_m"] == 0
    assert rows[-1]["distance_m"] == 20.0
    assert rows[0]["directSpeed"] == 5.0


def test_compare_laps_same_track_speed_gain_5_percent(tmp_path):
    activity_a = "a"
    activity_b = "b"
    points_a = _build_track(0, 120, speed_mps=10.0)
    points_b = _build_track(0, 120, speed_mps=10.5)
    _artifact_for_lap(tmp_path, activity_a, 0, 120.0, points_a)
    _artifact_for_lap(tmp_path, activity_b, 0, 120.0, points_b)
    report = compare_laps_by_distance(activity_a, activity_b, 4, root=tmp_path)
    assert report["verdict"] == "Lap B faster"
    assert report["lap_time_s"]["delta_s"] < 0
    assert report["metrics"]["faster_share"]["lap_b_faster_pct"] > 50


def test_compare_laps_timestamp_shift_removed_by_distance_anchor(tmp_path):
    points_a = _build_track(0, 90, speed_mps=10.0)
    points_b = _build_track(120_000, 90, speed_mps=10.0)
    _artifact_for_lap(tmp_path, "a", 0, 90.0, points_a)
    _artifact_for_lap(tmp_path, "b", 120_000, 90.0, points_b)
    report = compare_laps_by_distance("a", "b", 4, root=tmp_path)
    assert abs(report["lap_time_s"]["delta_s"]) < 1e-6

    assert abs(report["metrics"]["mean_delta_mps"]) < 1e-6


def test_compare_laps_ignore_pre_roll_still(tmp_path):
    points_a = _build_track(0, 90, speed_mps=10.5, static_prefix_seconds=30)
    points_b = _build_track(30_000, 90, speed_mps=10.5)
    _artifact_for_lap(tmp_path, "a", 0, 120.0, points_a)
    _artifact_for_lap(tmp_path, "b", 30_000, 120.0, points_b)
    report = compare_laps_by_distance("a", "b", 4, root=tmp_path)
    assert abs(report["lap_time_s"]["delta_s"]) < 1e-6


def test_compare_laps_unequal_distance_uses_overlap(tmp_path):
    points_a = _build_track(0, 100, speed_mps=10.0)
    points_b = _build_track(0, 120, speed_mps=10.0)
    _artifact_for_lap(tmp_path, "a", 0, 120.0, points_a)
    _artifact_for_lap(tmp_path, "b", 0, 120.0, points_b)
    report = compare_laps_by_distance("a", "b", 4, root=tmp_path)
    assert report["lap_distance_m"]["matched_distance"] == 1000.0
    assert report["quality"]["overlap_ratio_to_shorter"] == 1.0
    assert report["lap_time_s"]["delta_s"] == 0


def test_compare_laps_excludes_stationary_points_from_share(tmp_path):
    profile_a = [4.0, 4.0, 4.0, 0.2, 0.2, 0.2, 4.0]
    profile_b = [4.0, 5.0, 4.0, 0.2, 0.2, 0.2, 5.0]
    points_a = _build_mismatch_speed_track(0, profile_a)
    points_b = _build_mismatch_speed_track(0, profile_b)
    _artifact_for_lap(tmp_path, "a", 0, float(len(profile_a)), points_a)
    _artifact_for_lap(tmp_path, "b", 0, float(len(profile_b)), points_b)
    report = compare_laps_by_distance("a", "b", 4, root=tmp_path, speed_eps=3.0)
    faster_share = report["metrics"]["faster_share"]
    assert faster_share["stationary_points_excluded"] > 0
    assert faster_share["lap_b_faster_pct"] > faster_share["lap_a_faster_pct"]
