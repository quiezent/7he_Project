from coach_sync.gear_audit import build_gear_audit
from coach_sync.io import write_json


def _activity(activity_id, day, type_key="mountain_biking"):
    return {
        "activityId": activity_id,
        "activityName": "Kuala Lumpur Mountain Biking",
        "activityType": {"typeKey": type_key},
        "startTimeLocal": f"{day} 08:00:00",
        "duration": 3600,
    }


def test_gear_audit_flags_mtb_with_elite_suito(tmp_path):
    write_json(tmp_path / "activities" / "mtb.json", _activity(1, "2026-05-22"))
    write_json(
        tmp_path / "snapshots" / "activity_gear_index.json",
        {
            "activities": [
                {
                    "activity_id": "1",
                    "date": "2026-05-22",
                    "name": "Kuala Lumpur Mountain Biking",
                    "type": "mountain_biking",
                    "category": "mtb",
                    "gear_fetch_ok": True,
                    "gear": [{"label": "Elite Suito", "custom_make_model": "Elite Suito"}],
                }
            ]
        },
    )

    report = build_gear_audit(tmp_path, "2026-05-26")

    assert report["mtb_checked"] == 1
    assert report["flags"][0]["type"] == "mtb_activity_gear_elite_suito"


def test_gear_audit_accepts_stumpjumper_mtb(tmp_path):
    write_json(tmp_path / "activities" / "mtb.json", _activity(2, "2026-05-22"))
    write_json(
        tmp_path / "snapshots" / "activity_gear_index.json",
        {
            "activities": [
                {
                    "activity_id": "2",
                    "date": "2026-05-22",
                    "name": "Kuala Lumpur Mountain Biking",
                    "type": "mountain_biking",
                    "category": "mtb",
                    "gear_fetch_ok": True,
                    "gear": [
                        {
                            "label": "Stumpjumper Expert MY25",
                            "custom_make_model": "Stumpjumper Expert MY25",
                        }
                    ],
                }
            ]
        },
    )

    report = build_gear_audit(tmp_path, "2026-05-26")

    assert report["mtb_checked"] == 1
    assert report["flags"] == []
