from coach_sync.context import clearance_summary, load_context, save_context, set_clearance


def test_default_context_records_dr_teh_clearance(tmp_path):
    context = load_context(tmp_path)
    summary = clearance_summary(context)

    assert summary["all_cleared"] is True
    for gate in ("cardio", "grip", "loading", "trail"):
        assert summary["gates"][gate]["status"] == "cleared"
        assert summary["gates"][gate]["date"] == "2026-04-29"
        assert summary["gates"][gate]["source"] == "Dr. Teh"


def test_clearance_gate_can_be_downshifted(tmp_path):
    context = load_context(tmp_path)
    set_clearance(context, "trail", "pending", "2026-04-29", "test", "not yet")
    save_context(context, tmp_path)

    reloaded = load_context(tmp_path)
    summary = clearance_summary(reloaded)
    assert summary["all_cleared"] is False
    assert summary["gates"]["trail"]["status"] == "pending"

