from coach_sync.io import write_json
from coach_sync.loop_load import build_loop_load, parse_lap_groups


def test_parse_lap_groups():
    assert parse_lap_groups(["1,2", "3, 4", "5"]) == [[1, 2], [3, 4], [5]]


def test_build_loop_load_scales_laps_to_official_activity_load(tmp_path):
    activity_id = "1"
    start_ms = 1780617600000.0
    write_json(
        tmp_path / "activities" / f"garmin_{activity_id}.json",
        {
            "activityId": 1,
            "activityName": "Kuala Lumpur Mountain Biking",
            "activityType": {"typeKey": "mountain_biking"},
            "startTimeLocal": "2026-06-05 08:00:00",
            "duration": 900,
            "movingDuration": 900,
            "distance": 3000,
            "elevationGain": 200,
            "averageHR": 145,
            "maxHR": 170,
            "activityTrainingLoad": 100.0,
            "calories": 300,
        },
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
                                "lapIndex": 1,
                                "startTimeGMT": "2026-06-05T00:00:00.0",
                                "duration": 600,
                                "elapsedDuration": 600,
                                "movingDuration": 600,
                                "distance": 2000,
                                "elevationGain": 200,
                                "elevationLoss": 0,
                                "averageHR": 135,
                                "maxHR": 145,
                                "calories": 200,
                            },
                            {
                                "lapIndex": 2,
                                "startTimeGMT": "2026-06-05T00:10:00.0",
                                "duration": 300,
                                "elapsedDuration": 300,
                                "movingDuration": 300,
                                "distance": 1000,
                                "elevationGain": 0,
                                "elevationLoss": 200,
                                "averageHR": 165,
                                "maxHR": 170,
                                "calories": 100,
                            },
                        ]
                    },
                },
                "details": {
                    "ok": True,
                    "data": {
                        "metricDescriptors": [
                            {"key": "directHeartRate", "metricsIndex": 0},
                            {"key": "directTimestamp", "metricsIndex": 1},
                        ],
                        "activityDetailMetrics": [
                            {"metrics": [130, start_ms]},
                            {"metrics": [140, start_ms + 300_000]},
                            {"metrics": [160, start_ms + 600_000]},
                            {"metrics": [170, start_ms + 750_000]},
                            {"metrics": [165, start_ms + 900_000]},
                        ],
                    },
                },
                "hr_zones": {
                    "ok": True,
                    "data": [
                        {"zoneNumber": 1, "zoneLowBoundary": 118},
                        {"zoneNumber": 2, "zoneLowBoundary": 132},
                        {"zoneNumber": 3, "zoneLowBoundary": 146},
                        {"zoneNumber": 4, "zoneLowBoundary": 160},
                        {"zoneNumber": 5, "zoneLowBoundary": 174},
                    ],
                },
            },
        },
    )

    report = build_loop_load(tmp_path, activity_id, [[1, 2]], date="2026-06-05", fetch_live=False)

    assert report["official_activity_training_load"] == 100.0
    assert report["sanity_checks"]["lap_primary_load_sum"] == 100.0
    assert report["loops"][0]["estimated_load"]["primary_continuous_hr"] == 100.0
    assert report["laps"][1]["estimated_load"]["primary_continuous_hr"] > report["laps"][0]["estimated_load"][
        "primary_continuous_hr"
    ] / 2


def test_build_loop_load_flags_boundary_hr_carryover_after_long_stop(tmp_path):
    activity_id = "2"
    start_ms = 1780617600000.0
    write_json(
        tmp_path / "activities" / f"garmin_{activity_id}.json",
        {
            "activityId": 2,
            "activityName": "Kuala Lumpur Mountain Biking",
            "activityType": {"typeKey": "mountain_biking"},
            "startTimeLocal": "2026-06-05 08:00:00",
            "duration": 600,
            "movingDuration": 280,
            "distance": 500,
            "elevationGain": 80,
            "averageHR": 135,
            "maxHR": 181,
            "activityTrainingLoad": 50.0,
            "calories": 150,
        },
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
                                "lapIndex": 1,
                                "startTimeGMT": "2026-06-05T00:00:00.0",
                                "duration": 600,
                                "elapsedDuration": 600,
                                "movingDuration": 280,
                                "distance": 500,
                                "elevationGain": 80,
                                "elevationLoss": 0,
                                "averageHR": 135,
                                "maxHR": 181,
                                "calories": 150,
                            }
                        ]
                    },
                },
                "details": {
                    "ok": True,
                    "data": {
                        "metricDescriptors": [
                            {"key": "directHeartRate", "metricsIndex": 0},
                            {"key": "directTimestamp", "metricsIndex": 1},
                            {"key": "directSpeed", "metricsIndex": 2},
                            {"key": "sumDistance", "metricsIndex": 3},
                            {"key": "directPower", "metricsIndex": 4},
                            {"key": "directElevation", "metricsIndex": 5},
                        ],
                        "activityDetailMetrics": [
                            {"metrics": [181, start_ms, 2.0, 0.0, 0, 100.0]},
                            {"metrics": [176, start_ms + 20_000, 0.0, 40.0, 0, 100.0]},
                            {"metrics": [140, start_ms + 120_000, 0.0, 40.0, 0, 100.0]},
                            {"metrics": [115, start_ms + 300_000, 0.0, 40.0, 0, 100.0]},
                            {"metrics": [115, start_ms + 320_000, 0.0, 40.0, 0, 100.0]},
                            {"metrics": [118, start_ms + 330_000, 1.5, 41.0, 120, 101.0]},
                            {"metrics": [150, start_ms + 590_000, 1.5, 500.0, 140, 120.0]},
                            {"metrics": [150, start_ms + 600_000, 0.0, 500.0, 0, 120.0]},
                        ],
                    },
                },
                "hr_zones": {
                    "ok": True,
                    "data": [
                        {"zoneNumber": 1, "zoneLowBoundary": 118},
                        {"zoneNumber": 2, "zoneLowBoundary": 132},
                        {"zoneNumber": 3, "zoneLowBoundary": 146},
                        {"zoneNumber": 4, "zoneLowBoundary": 160},
                        {"zoneNumber": 5, "zoneLowBoundary": 174},
                    ],
                },
            },
        },
    )

    report = build_loop_load(tmp_path, activity_id, [[1]], date="2026-06-05", fetch_live=False)
    timeline = report["laps"][0]["timeline"]

    assert timeline["longest_stop"]["duration_s"] == 300.0
    assert timeline["first_moving_after_longest_stop"]["start_hr"] == 115
    assert "max_hr_likely_boundary_carryover" in timeline["flags"]
    action_summary = timeline["action_terrain_summary"]
    assert action_summary["dominant_active_category"] == "punchy_climb_pedaling"
    assert action_summary["sections"]["punchy_climb_pedaling"]["duration_s"] == 260.0
    assert action_summary["sections"]["punchy_climb_pedaling"]["avg_grade_pct"] == 4.1
