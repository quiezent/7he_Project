from coach_sync import reports
from coach_sync.context import load_context
from coach_sync.io import write_json


def test_microcycle_forecast_enforces_sabbath_rest(tmp_path, monkeypatch):
    monkeypatch.setattr(reports, "build_current_state", lambda root: {"date": "2026-06-22"})

    forecast = reports.microcycle_forecast(tmp_path, days=7)
    sunday = next(item for item in forecast["entries"] if item["date"] == "2026-06-28")

    assert sunday["focus"] == "Sabbath rest: no planned exercise"


def test_weekly_report_uses_requested_window_for_displayed_totals(tmp_path, monkeypatch):
    load_context(tmp_path)
    monkeypatch.setattr(reports, "today_local", lambda tz_name=None: reports.date(2026, 6, 22))
    for activity_id, day in ((1, "2026-06-10"), (2, "2026-06-20")):
        write_json(
            tmp_path / "activities" / f"activity_{activity_id}.json",
            {
                "activityId": activity_id,
                "activityName": "Indoor Cycling",
                "activityType": {"typeKey": "indoor_cycling"},
                "startTimeLocal": f"{day} 08:00:00",
                "duration": 3600,
                "activityTrainingLoad": 50,
            },
        )

    report = reports.weekly_report(tmp_path, days=14)

    assert report["requested_window_training"]["sessions"] == 2
    assert report["requested_window_training"]["training_load"] == 100.0
