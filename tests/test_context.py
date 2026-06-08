from coach_sync.context import load_context, save_context, set_event_date


def test_default_context_keeps_finger_history_as_context_only(tmp_path):
    context = load_context(tmp_path)

    history = context["medical"]["history"]
    assert history == [
        {
            "date": "2025-08",
            "label": "left_pinky_fracture",
            "note": "Historical context only; not used as a current training gate.",
        }
    ]
    assert "clearance_gates" not in context["medical"]


def test_event_date_can_update_historical_context(tmp_path):
    context = load_context(tmp_path)
    set_event_date(context, "sepang_logistics", "2026-05-13", "logistics")
    save_context(context, tmp_path)

    reloaded = load_context(tmp_path)
    assert any(item["label"] == "sepang_logistics" for item in reloaded["medical"]["history"])
