from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable

from .context import load_context
from .evidence import as_number, load_activities
from .io import read_json, write_json, write_text
from .paths import config_dir, input_dir, repo_root, snapshots_dir
from .time_utils import DEFAULT_TIMEZONE, iso_now, parse_date, today_local


ADAPTIVE_TRAINING_VERSION = "adaptive_training_v1"


def _flatten_text(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(_flatten_text(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return " ".join(_flatten_text(item) for item in value)
    return str(value or "")


def _relative_path(root: str | Path | None, path: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root(root)).as_posix()
    except (OSError, ValueError):
        return path.name


def _roadmap_mode(label: str, intent: str) -> str:
    text = f"{label} {intent}".lower()
    if "replacement sabbath" in text:
        return "replacement_sabbath"
    if "official practice" in text:
        return "event_practice"
    if "pdr26 race" in text or ("race" in text and "race-specific" not in text):
        return "event_race"
    if "absorption" in text or "conference entry" in text:
        return "absorption"
    if "re-entry" in text or "reentry" in text:
        return "reentry"
    if "race-specific" in text or "recce" in text:
        return "race_specific"
    if "taper" in text or "sharpening" in text:
        return "taper"
    if "consolidation" in text:
        return "consolidation"
    if "benchmark" in text or "absorption" in text:
        return "benchmark"
    if any(token in text for token in ("foundation", "performance build", "expert integration")):
        return "build"
    return "build"


def _parse_roadmap_block(root: str | Path | None, target: date) -> dict[str, Any]:
    path = input_dir(root) / "expert_enduro_roadmap.md"
    architecture_path = config_dir(root) / "coaching_architecture.json"
    architecture = read_json(architecture_path, {})
    configured_blocks = (
        (architecture.get("adaptive_programming") or {}).get("block_calendar")
        if isinstance(architecture, dict)
        else None
    )
    if isinstance(configured_blocks, list):
        for block in configured_blocks:
            if not isinstance(block, dict):
                continue
            start = parse_date(block.get("start"))
            end = parse_date(block.get("end"))
            if start is None or end is None or not start <= target <= end:
                continue
            return {
                "status": "matched",
                "program_mode": block.get("mode") or "build",
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "label": block.get("label") or block.get("mode") or "Configured block",
                "intent": block.get("intent") or block.get("label"),
                "constraints": block.get("constraints")
                or "Use input/expert_enduro_roadmap.md for the detailed human-readable block intent and gates.",
                "source": _relative_path(root, architecture_path),
                "human_roadmap": _relative_path(root, path),
            }
    if target == date(2026, 9, 21):
        return {
            "status": "matched",
            "program_mode": "replacement_sabbath",
            "start_date": target.isoformat(),
            "end_date": target.isoformat(),
            "label": "PDR26 replacement Sabbath",
            "intent": "Full physical and mental absorption; hard no planned exercise.",
            "constraints": "This exact replacement Sabbath overrides readiness.",
            "source": _relative_path(root, path),
        }
    if date(2026, 9, 22) <= target <= date(2026, 9, 27):
        return {
            "status": "matched",
            "program_mode": "race_recovery_transition",
            "start_date": "2026-09-22",
            "end_date": "2026-09-27",
            "label": "PDR26 race-recovery transition",
            "intent": "Evidence-led re-entry without compensation training.",
            "constraints": "No maximal FTP test, four-cycle proof, or high-volume jump/landing work.",
            "source": _relative_path(root, path),
        }
    if not path.exists():
        return {
            "status": "missing",
            "program_mode": "build",
            "start_date": None,
            "end_date": None,
            "label": "Canonical phase fallback",
            "intent": "Use canonical strategy because the dated roadmap is unavailable.",
            "constraints": "Missing roadmap blocks calendar-specific upgrades.",
            "source": _relative_path(root, path),
        }

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line.lstrip().startswith("|"):
            continue
        cells = [cell.strip() for cell in raw_line.strip().strip("|").split("|")]
        if not cells or all(set(cell) <= {"-", ":", " "} for cell in cells):
            continue
        if cells[0].isdigit() and len(cells) >= 4:
            date_cell, label, intent = cells[1], cells[2], cells[3]
            constraints = intent
        elif len(cells) >= 3:
            date_cell, label, constraints = cells[0], cells[1], cells[2]
            intent = label
        else:
            continue
        matches = re.findall(r"\d{4}-\d{2}-\d{2}", date_cell)
        if not matches:
            continue
        start = parse_date(matches[0])
        end = parse_date(matches[1]) if len(matches) > 1 else start
        if start is None or end is None or not start <= target <= end:
            continue
        return {
            "status": "matched",
            "program_mode": _roadmap_mode(label, intent),
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "label": label,
            "intent": intent,
            "constraints": constraints,
            "source": _relative_path(root, path),
        }

    return {
        "status": "outside_dated_roadmap",
        "program_mode": "build",
        "start_date": None,
        "end_date": None,
        "label": "Canonical phase fallback",
        "intent": "Use the canonical build strategy outside the dated roadmap horizon.",
        "constraints": "No calendar-specific promotion may be inferred.",
        "source": _relative_path(root, path),
    }


def _target_shape(mode: str) -> dict[str, Any]:
    shapes = {
        "absorption": (4, 5, 2, 1, 0.85),
        "reentry": (4, 5, 3, 2, 1.0),
        "race_specific": (4, 5, 3, 2, 1.0),
        "taper": (3, 4, 2, 1, 0.60),
        "event_practice": (2, 3, 2, 1, 0.50),
        "event_race": (1, 2, 1, 1, 0.35),
        "replacement_sabbath": (0, 0, 0, 0, 0.0),
        "race_recovery_transition": (2, 4, 1, 1, 0.55),
        "consolidation": (4, 5, 2, 1, 0.72),
        "benchmark": (4, 5, 2, 1, 0.75),
        "build": (5, 6, 3, 2, 1.0),
    }
    preferred, maximum, meaningful, mtb, multiplier = shapes.get(mode, shapes["build"])
    return {
        "preferred_unique_bike_days": preferred,
        "maximum_unique_bike_days": maximum,
        "meaningful_cost_days_max": meaningful,
        "protected_mtb_days": mtb,
        "duration_multiplier": multiplier,
        "counting_rule": "Count unique days; split files and unnecessary doubles do not create extra touches.",
    }


def _is_bike(activity: dict[str, Any]) -> bool:
    return str(activity.get("category") or "") in {"mtb", "bike_indoor", "bike_outdoor"}


def _is_mtb(activity: dict[str, Any]) -> bool:
    return str(activity.get("category") or "") == "mtb"


def _meaningful_reasons(activity: dict[str, Any]) -> list[str]:
    category = str(activity.get("category") or "")
    duration = as_number(activity.get("duration_min")) or 0.0
    load = as_number(activity.get("training_load")) or 0.0
    aerobic = as_number(activity.get("aerobic_te")) or 0.0
    anaerobic = as_number(activity.get("anaerobic_te")) or 0.0
    intensity_factor = as_number(activity.get("intensity_factor")) or 0.0
    reasons: list[str] = []
    if category == "mtb":
        if duration >= 90:
            reasons.append("mtb_duration_at_least_90_min")
        if load >= 80:
            reasons.append("mtb_load_at_least_80")
        if aerobic >= 3.0:
            reasons.append("mtb_aerobic_te_at_least_3")
        if anaerobic >= 1.0:
            reasons.append("mtb_anaerobic_te_at_least_1")
    elif category in {"bike_indoor", "bike_outdoor"}:
        if load >= 70:
            reasons.append("bike_load_at_least_70")
        if intensity_factor >= 0.68:
            reasons.append("bike_if_at_least_0_68")
        if aerobic >= 3.0:
            reasons.append("bike_aerobic_te_at_least_3")
        if anaerobic >= 1.0:
            reasons.append("bike_anaerobic_te_at_least_1")
        if duration >= 75:
            reasons.append("duration_development_at_least_75_min")
    elif category == "run":
        if load >= 70 or aerobic >= 3.0 or anaerobic >= 1.0:
            reasons.append("hard_run_counts_toward_cost_cap")
    return reasons


def _window_summary(activities: Iterable[dict[str, Any]], start: date, end: date) -> dict[str, Any]:
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for activity in activities:
        activity_date = parse_date(activity.get("date"))
        if activity_date is None or not start <= activity_date <= end:
            continue
        if _is_bike(activity) or str(activity.get("category") or "") == "run":
            by_day[activity_date.isoformat()].append(activity)

    bike_days: list[str] = []
    mtb_days: list[str] = []
    meaningful_days: list[dict[str, Any]] = []
    bike_duration = 0.0
    bike_load = 0.0
    for day, rows in sorted(by_day.items()):
        bike_rows = [row for row in rows if _is_bike(row)]
        if bike_rows:
            bike_days.append(day)
            bike_duration += sum(as_number(row.get("duration_min")) or 0.0 for row in bike_rows)
            bike_load += sum(as_number(row.get("training_load")) or 0.0 for row in bike_rows)
        if any(_is_mtb(row) for row in rows):
            mtb_days.append(day)
        reasons = sorted({reason for row in rows for reason in _meaningful_reasons(row)})
        if reasons:
            meaningful_days.append({"date": day, "reasons": reasons})
    return {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "unique_bike_days": len(bike_days),
        "bike_dates": bike_days,
        "unique_mtb_days": len(mtb_days),
        "mtb_dates": mtb_days,
        "meaningful_cost_days": len(meaningful_days),
        "meaningful_day_evidence": meaningful_days,
        "bike_duration_min": round(bike_duration, 1),
        "bike_training_load": round(bike_load, 1),
    }


def _weekly_budget(activities: list[dict[str, Any]], target: date, shape: dict[str, Any]) -> dict[str, Any]:
    week_start = target - timedelta(days=target.weekday())
    current = _window_summary(activities, week_start, target)
    completed_weeks = []
    for offset in (1, 2, 3):
        end = week_start - timedelta(days=7 * offset - 6)
        start = end - timedelta(days=6)
        completed_weeks.append(_window_summary(activities, start, end))
    three_week_average = round(
        sum(item["unique_bike_days"] for item in completed_weeks) / len(completed_weeks), 2
    )
    meaningful_remaining = max(
        0, shape["meaningful_cost_days_max"] - current["meaningful_cost_days"]
    )
    return {
        "current_calendar_week": current,
        "previous_three_complete_weeks": completed_weeks,
        "previous_three_week_bike_day_average": three_week_average,
        "preferred_bike_days_remaining": max(
            0, shape["preferred_unique_bike_days"] - current["unique_bike_days"]
        ),
        "mtb_days_remaining_to_protect": max(
            0, shape["protected_mtb_days"] - current["unique_mtb_days"]
        ),
        "meaningful_cost_days_remaining": meaningful_remaining,
        "meaningful_cost_budget_status": (
            "available" if meaningful_remaining > 0 else "spent"
        ),
        "classification_guardrail": (
            "The cost classifier is a transparent density audit, not a Garmin-load truth. "
            "Head-coach judgment may reclassify a day when route consequence, execution, or sensor quality warrants it."
        ),
    }


def _planned_session(root: str | Path | None, day: date) -> dict[str, Any]:
    path = input_dir(root) / f"planned_session_{day.isoformat()}.json"
    payload = read_json(path, {})
    if not isinstance(payload, dict):
        return {}
    session = payload.get("session")
    return session if isinstance(session, dict) else {}


def _preferred_protected_mtb_role(
    root: str | Path | None,
    target: date,
    roadmap: dict[str, Any],
) -> dict[str, Any]:
    """Resolve the protected MTB role without manufacturing a session contract.

    An explicit dated contract is authoritative for role identity, while the
    adaptive controller remains responsible for exposing its density fit.  In
    the absence of a contract, race-specific/taper blocks prefer race-bike
    transfer and other blocks remain bike-agnostic until the head coach writes
    the schema-v3 dose.
    """
    week_end = target + timedelta(days=6 - target.weekday())
    for offset in range((week_end - target).days + 1):
        day = target + timedelta(days=offset)
        session = _planned_session(root, day)
        if str(session.get("modality") or "").lower() != "mtb":
            continue
        text = _flatten_text(
            {
                "title": session.get("title"),
                "type": session.get("type"),
                "equipment_key": session.get("equipment_key"),
                "bike_key": (session.get("action_identity") or {}).get("bike_key")
                if isinstance(session.get("action_identity"), dict)
                else None,
            }
        ).lower()
        role = (
            "protected_enduro_durability_or_race_transfer"
            if "enduro" in text
            else "protected_stumpjumper_quality_fitness"
            if "stumpjumper" in text
            else "protected_mtb_quality_or_race_transfer"
        )
        return {
            "role": role,
            "date": day.isoformat(),
            "title": session.get("title"),
            "source": f"input/planned_session_{day.isoformat()}.json",
            "selection_basis": "explicit_schema_v3_contract",
        }

    if roadmap.get("program_mode") in {"race_specific", "taper"}:
        role = "protected_enduro_durability_or_race_transfer"
        basis = "dated_race_specific_block"
    else:
        role = "protected_mtb_quality_or_race_transfer"
        basis = "head_coach_contract_required"
    return {
        "role": role,
        "date": None,
        "title": None,
        "source": roadmap.get("source"),
        "selection_basis": basis,
    }


def _session_family(root: str | Path | None, day: date, entry: dict[str, Any]) -> str:
    planned = _planned_session(root, day)
    delivered_text = _flatten_text(entry).lower()
    planned_text = f"{planned.get('type', '')} {planned.get('title', '')}".lower()
    if isinstance(entry.get("objective_ebike_evidence"), dict):
        return "mtb_technical"
    if isinstance(entry.get("objective_interval_evidence"), dict):
        return "engine_vo2" if "vo2" in planned_text else "engine_torque"
    if isinstance(entry.get("objective_session_evidence"), dict) and "indoor" in delivered_text:
        return "steady_endurance"
    if any(token in delivered_text for token in ("3x8", "3x10", "3x12", "tempo_torque", "tempo/torque")):
        return "engine_torque"
    if "vo2" in delivered_text or re.search(r"\b[45]x3\b", delivered_text):
        return "engine_vo2"
    if any(
        token in delivered_text
        for token in (
            "indoor low-aerobic",
            "low-aerobic equivalent",
            "low aerobic",
            "zone 2",
            "steady endurance",
            "indoor continuity",
            "suito session",
            "main block averaged",
            "low-aerobic",
        )
    ):
        return "steady_endurance"
    if any(
        token in delivered_text
        for token in ("pure quill", "twin peak", "flintstone", "2k+", "dh line", "trail condition")
    ):
        return "mtb_technical"
    if any(token in planned_text for token in ("3x8", "3x10", "3x12", "tempo_torque", "tempo/torque")):
        return "engine_torque"
    if "vo2" in planned_text or re.search(r"\b[45]x3\b", planned_text):
        return "engine_vo2"
    planned_modality = str(planned.get("modality") or "").lower()
    if "indoor" in planned_modality or any(
        token in planned_text for token in ("low_aerobic", "low-aerobic", "steady_endurance", "continuity", "recovery_primer")
    ):
        return "steady_endurance"
    if planned_modality == "mtb" or str(planned.get("type") or "").lower().startswith("mtb"):
        return "mtb_technical"
    return "other"


def _feedback_ledger(root: str | Path | None, target: date, lookback_days: int = 84) -> list[dict[str, Any]]:
    start = target - timedelta(days=lookback_days - 1)
    rows: list[dict[str, Any]] = []
    for path in sorted(input_dir(root).glob("feedback_*.json")):
        day_match = re.search(r"(\d{4}-\d{2}-\d{2})", path.name)
        day = parse_date(day_match.group(1)) if day_match else None
        if day is None or not start <= day <= target:
            continue
        payload = read_json(path, {})
        entries = payload.get("entries") if isinstance(payload, dict) else None
        if not isinstance(entries, list):
            entries = [payload] if isinstance(payload, dict) else []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            review = entry.get("session_contract_review")
            review = review if isinstance(review, dict) else {}
            stop = review.get("stop_rule_outcome")
            stop = str(stop).strip().lower() if stop not in (None, "") else None
            text = _flatten_text(entry).lower()
            if stop == "triggered_but_continued":
                outcome = "out_of_policy"
            elif stop in {"triggered_and_stopped", "triggered_and_downshifted"}:
                outcome = "absorbed_boundary_pending_response"
            elif stop == "not_triggered":
                outcome = "completed_pending_absorption"
            else:
                outcome = "insufficient_stop_evidence"
            next_day_explicit = any(
                key in review and review.get(key) not in (None, "")
                for key in ("next_morning_response", "next_day_response")
            ) or any(
                entry.get(key) not in (None, "")
                for key in ("next_morning_response", "next_day_response")
            )
            family = _session_family(root, day, entry)
            role_credit = None
            if family == "mtb_technical":
                role_credit = (
                    "mtb_exposure_no_protected_role"
                    if any(token in text for token in ("test ride", "bike test", "demo ride", "levo sl"))
                    else "protected_mtb_role"
                )
            rows.append(
                {
                    "date": day.isoformat(),
                    "activity_id": entry.get("activity_id"),
                    "family": family,
                    "role_credit": role_credit,
                    "stop_rule_outcome": stop,
                    "stop_rule_outcome_explicit": stop is not None,
                    "outcome_class": outcome,
                    "next_day_response_explicit": next_day_explicit,
                    "global_rpe_0_to_10": as_number(
                        review.get("global_session_rpe_0_to_10")
                        or review.get("overall_rpe_0_to_10")
                    ),
                    "local_rpe_0_to_10": as_number(review.get("local_leg_rpe_0_to_10")),
                    "technical_review_present": any(
                        review.get(key) not in (None, "")
                        for key in (
                            "technical_quality_notes",
                            "late_session_skill_fade",
                            "braking_fatigue",
                            "upper_body_fatigue",
                        )
                    ),
                    "source": _relative_path(root, path),
                    "review": review,
                    "entry": entry,
                }
            )
    return sorted(rows, key=lambda item: item["date"])


def _last(items: list[dict[str, Any]], family: str) -> dict[str, Any] | None:
    return next((item for item in reversed(items) if item.get("family") == family), None)


def _first_number(record: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        number = as_number(record.get(key))
        if number is not None:
            return number
    return None


def _explicit_none(value: Any) -> bool | None:
    if isinstance(value, bool):
        return not value
    if value in (None, ""):
        return None
    normalized = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in {"none", "no", "false", "not_present", "not_triggered", "0"}:
        return True
    if normalized in {"yes", "true", "present", "occurred", "1"}:
        return False
    return None


def _apply_delivered_role_credit(
    budget: dict[str, Any],
    ledger: list[dict[str, Any]],
    target: date,
    shape: dict[str, Any],
) -> None:
    week_start = target - timedelta(days=target.weekday())
    protected_dates = sorted(
        {
            item["date"]
            for item in ledger
            if item.get("role_credit") == "protected_mtb_role"
            and (day := parse_date(item.get("date"))) is not None
            and week_start <= day <= target
        }
    )
    noncredit_dates = sorted(
        {
            item["date"]
            for item in ledger
            if item.get("role_credit") == "mtb_exposure_no_protected_role"
            and (day := parse_date(item.get("date"))) is not None
            and week_start <= day <= target
        }
    )
    current = budget.get("current_calendar_week") or {}
    confirmed_cost_evidence = []
    possible_cost_evidence = []
    for item in current.get("meaningful_day_evidence") or []:
        reasons = item.get("reasons") or []
        demo_only_mtb_cost = bool(
            item.get("date") in noncredit_dates
            and reasons
            and all(str(reason).startswith("mtb_") for reason in reasons)
        )
        if demo_only_mtb_cost:
            possible_cost_evidence.append(
                {
                    **item,
                    "classification": "possible_meaningful_cost",
                    "reason": (
                        "Objective Garmin load/Training Effect was meaningful-looking, but structured feedback identifies "
                        "a short demo/exploratory MTB exposure without protected-role credit. Preserve the cost as uncertainty "
                        "instead of treating it as either free or a confirmed costly workout."
                    ),
                }
            )
        else:
            confirmed_cost_evidence.append(item)
    current["raw_meaningful_cost_days_before_feedback"] = current.get("meaningful_cost_days", 0)
    current["meaningful_day_evidence"] = confirmed_cost_evidence
    current["meaningful_cost_days"] = len(confirmed_cost_evidence)
    current["possible_meaningful_cost_days"] = len(possible_cost_evidence)
    current["possible_meaningful_day_evidence"] = possible_cost_evidence
    budget["protected_mtb_role_days_completed"] = len(protected_dates)
    budget["protected_mtb_role_dates"] = protected_dates
    budget["mtb_exposure_dates_without_protected_role_credit"] = noncredit_dates
    budget["mtb_days_remaining_to_protect"] = max(
        0, shape["protected_mtb_days"] - len(protected_dates)
    )
    budget["meaningful_cost_days_remaining"] = max(
        0, shape["meaningful_cost_days_max"] - current["meaningful_cost_days"]
    )
    budget["meaningful_cost_budget_status"] = (
        "available" if budget["meaningful_cost_days_remaining"] > 0 else "spent"
    )
    budget["possible_cost_guardrail"] = (
        "Possible cost is not assumed free and is not silently promoted to confirmed cost. The head coach resolves it from "
        "sensor confidence, delivered action, subjective response, next-day absorption, and the consequence of the next role."
    )
    current_week_rows = [
        item
        for item in ledger
        if (day := parse_date(item.get("date"))) is not None and week_start <= day <= target
    ]
    role_dates = {
        "low_cost_endurance": sorted(
            {item["date"] for item in current_week_rows if item.get("family") == "steady_endurance"}
        ),
        "structured_engine": sorted(
            {
                item["date"]
                for item in current_week_rows
                if item.get("family") in {"engine_torque", "engine_vo2"}
            }
        ),
        "protected_mtb": protected_dates,
        "exploratory_mtb_no_role_credit": noncredit_dates,
    }
    budget["delivered_role_dates"] = role_dates
    budget["delivered_role_counts"] = {
        role: len(dates) for role, dates in role_dates.items()
    }
    budget["mtb_role_credit_rule"] = (
        "An activity classified as MTB is an exposure, but a demo/setup/exploratory ride does not silently satisfy "
        "the protected quality or durability role. Role credit comes from delivered-action feedback."
    )


def _endurance_track(ledger: list[dict[str, Any]]) -> dict[str, Any]:
    sessions = [item for item in ledger if item.get("family") == "steady_endurance"]
    evidence = []
    for item in sessions[-4:]:
        objective = item.get("entry", {}).get("objective_session_evidence") or {}
        main = objective.get("main_block") if isinstance(objective, dict) else {}
        response = item.get("entry", {}).get("matched_comparison_to_2026_08_26") or {}
        evidence.append(
            {
                "date": item.get("date"),
                "stop_rule_outcome": item.get("stop_rule_outcome"),
                "global_rpe_0_to_10": item.get("global_rpe_0_to_10"),
                "local_rpe_0_to_10": item.get("local_rpe_0_to_10"),
                "decoupling_pct": as_number((main or {}).get("power_hr_decoupling_pct")),
                "next_day_response_explicit": item.get("next_day_response_explicit"),
                "response_note": response.get("symptom_timing") if isinstance(response, dict) else None,
            }
        )
    clean = [
        item
        for item in evidence
        if item.get("stop_rule_outcome") == "not_triggered"
        and (item.get("global_rpe_0_to_10") is None or item["global_rpe_0_to_10"] <= 4)
        and (item.get("decoupling_pct") is None or item["decoupling_pct"] <= 5)
    ]
    absorbed = [item for item in clean if item.get("next_day_response_explicit")]
    promote = len(absorbed) >= 2
    return {
        "current_rung": "60_min_120_130_w",
        "rung_sequence": ["60_min_120_130_w", "75_min_near_125_w", "90_min_near_125_w"],
        "decision": "promote_duration" if promote else "hold_and_collect_absorption",
        "promotion_gate": (
            "Two absorbed standard doses or one clean duration-development confirmation; global RPE <=4, "
            "stable mechanics, approximately <=5% decoupling when trustworthy, and normal next-day function."
        ),
        "gate_progress": {
            "clean_recent_standard_doses": len(clean),
            "clean_with_explicit_next_day_response": len(absorbed),
            "required_clean_absorbed_standard_doses": 2,
        },
        "recent_evidence": evidence,
    }


def _engine_track(ledger: list[dict[str, Any]], roadmap_mode: str) -> dict[str, Any]:
    torque = _last(ledger, "engine_torque")
    torque_stop = torque.get("stop_rule_outcome") if torque else None
    torque_review = torque.get("review") if torque else {}
    torque_entry = torque.get("entry") if torque else {}
    torque_review = torque_review if isinstance(torque_review, dict) else {}
    torque_entry = torque_entry if isinstance(torque_entry, dict) else {}
    objective = torque_entry.get("objective_interval_evidence") or {}
    repetitions = objective.get("repetitions") if isinstance(objective, dict) else None
    repetitions = repetitions if isinstance(repetitions, list) else []
    rep_rpe = torque_review.get("repetition_reported_rpe_0_to_10")
    rep_rpe = [as_number(value) for value in rep_rpe] if isinstance(rep_rpe, list) else []
    rep_rpe = [value for value in rep_rpe if value is not None]
    powers = [as_number(rep.get("average_power_w")) for rep in repetitions if isinstance(rep, dict)]
    powers = [value for value in powers if value is not None]
    cadences = [as_number(rep.get("average_cadence_rpm")) for rep in repetitions if isinstance(rep, dict)]
    cadences = [value for value in cadences if value is not None]
    power_spread_pct = (
        round((max(powers) - min(powers)) / (sum(powers) / len(powers)) * 100, 1)
        if len(powers) >= 2 and sum(powers) > 0
        else None
    )
    cadence_spread_rpm = round(max(cadences) - min(cadences), 1) if len(cadences) >= 2 else None
    torque_decision = "repeat_3x8_within_ceiling"
    block_reason = None
    if torque_stop == "triggered_but_continued":
        torque_decision = "hold_no_promotion"
        block_reason = "Latest torque attempt crossed its stop rule and continued; nominal progression is rejected."
    elif (
        torque
        and torque_stop == "not_triggered"
        and torque.get("next_day_response_explicit")
        and rep_rpe
        and max(rep_rpe) <= 6
        and (power_spread_pct is None or power_spread_pct <= 5)
    ):
        torque_decision = "eligible_to_review_3x10"
    vo2_allowed = roadmap_mode in {"build", "race_specific"} and torque_decision == "eligible_to_review_3x10"
    return {
        "torque": {
            "current_rung": "3x8_min",
            "rung_sequence": ["3x8_min", "3x10_min", "3x12_min", "raise_watts"],
            "decision": torque_decision,
            "block_reason": block_reason,
            "latest_evidence": (
                {
                    "date": torque.get("date"),
                    "stop_rule_outcome": torque_stop,
                    "global_rpe_0_to_10": torque.get("global_rpe_0_to_10"),
                    "local_rpe_0_to_10": torque.get("local_rpe_0_to_10"),
                    "rep_rpe_0_to_10": rep_rpe or None,
                    "rep_power_spread_pct": power_spread_pct,
                    "rep_cadence_spread_rpm": cadence_spread_rpm,
                    "next_day_response_explicit": torque.get("next_day_response_explicit"),
                }
                if torque
                else None
            ),
            "promotion_gate": "Every repetition within the written RPE ceiling, stable cadence/form/power, no trigger, and clean next-day response.",
        },
        "vo2": {
            "current_rung": "not_admitted" if not vo2_allowed else "4x3_min",
            "rung_sequence": ["4x3_min", "5x3_min"],
            "decision": "not_admitted" if not vo2_allowed else "eligible_for_4x3_candidate",
            "admission_gate": "Roadmap permits it, torque is absorbed, power source is trustworthy, density budget is available, and fresh readiness/CNS are green.",
        },
    }


def _technical_track(context: dict[str, Any], ledger: list[dict[str, Any]]) -> dict[str, Any]:
    milestone = (
        ((context.get("athlete") or {}).get("event_focus") or {}).get("current_milestone")
        or {}
    )
    latest_mtb_exposure = _last(ledger, "mtb_technical")
    latest_mtb = next(
        (
            item
            for item in reversed(ledger)
            if item.get("family") == "mtb_technical"
            and item.get("role_credit") == "protected_mtb_role"
        ),
        None,
    )
    latest = milestone.get("latest_progression_evidence") or {}
    proof_review = latest_mtb.get("review") if latest_mtb else {}
    proof_review = proof_review if isinstance(proof_review, dict) else {}
    complete_cycles = _first_number(
        proof_review,
        "complete_twin_peaks_cycles",
        "complete_climb_stage_cycles",
        "complete_cycle_count",
    )
    final_climb_rpe = _first_number(
        proof_review,
        "final_climb_rpe_0_to_10",
        "final_climb_rpe",
    )
    technical_delta = _first_number(
        proof_review,
        "first_vs_final_technical_delta_max_0_to_10",
        "final_vs_first_technical_delta_max_0_to_10",
    )
    braking_fatigue = _first_number(proof_review, "braking_fatigue_0_to_10", "braking_fatigue")
    upper_fatigue = _first_number(
        proof_review,
        "upper_body_fatigue_0_to_10",
        "upper_body_fatigue",
    )
    incident_values = {
        key: _explicit_none(proof_review.get(key))
        for key in ("reactive_braking", "rescue", "near_miss", "late_session_skill_fade")
    }
    fuel_complete = all(
        proof_review.get(key) not in (None, "")
        for key in ("fueling_carbs_g_per_hour", "fluid_ml_per_hour", "sodium_mg_per_hour")
    )
    proof_gates = {
        "at_least_three_complete_cycles": None if complete_cycles is None else complete_cycles >= 3,
        "final_climb_rpe_at_most_6": None if final_climb_rpe is None else final_climb_rpe <= 6,
        "final_technical_dimensions_within_1": None if technical_delta is None else technical_delta <= 1,
        "braking_fatigue_at_most_3": None if braking_fatigue is None else braking_fatigue <= 3,
        "upper_body_fatigue_at_most_3": None if upper_fatigue is None else upper_fatigue <= 3,
        "no_reactive_braking_rescue_near_miss_or_fade": (
            None
            if any(value is None for value in incident_values.values())
            else all(incident_values.values())
        ),
        "explicit_not_triggered": (
            None if latest_mtb is None else latest_mtb.get("stop_rule_outcome") == "not_triggered"
        ),
        "race_relevant_fueling_recorded": fuel_complete,
        "clean_next_morning_explicit": (
            None if latest_mtb is None else bool(latest_mtb.get("next_day_response_explicit"))
        ),
    }
    proof_passed = bool(proof_gates) and all(value is True for value in proof_gates.values())
    mtb_rows = [item for item in ledger if item.get("family") == "mtb_technical"]
    skill_definitions = {
        "corner_entry_brake_release_exit": ("corner", "brake release", "turn-in", "exit"),
        "front_tracking_dynamic_centre": ("front tracking", "front wheel", "dynamic centre", "centered", "centred"),
        "jump_squash_scrub_landing": ("jump", "scrub", "squash", "landing"),
        "controlled_sliding": ("drift", "slide", "rear yaw", "micro-slip", "micro slip"),
        "rough_technical_terrain": ("rock garden", "roots", "rough", "technical terrain"),
    }
    skill_tracks: dict[str, Any] = {}
    for name, tokens in skill_definitions.items():
        matched = next(
            (
                item
                for item in reversed(mtb_rows)
                if any(token in _flatten_text(item.get("entry") or {}).lower() for token in tokens)
            ),
            None,
        )
        skill_tracks[name] = {
            "status": "evidence_present_hold_for_structured_gate" if matched else "unassessed",
            "latest_evidence_date": matched.get("date") if matched else None,
            "stop_rule_outcome": matched.get("stop_rule_outcome") if matched else None,
            "promotion_rule": "Require the named clean-repetition gate; narrative evidence alone does not promote the skill rung.",
        }
    return {
        "milestone": milestone.get("name") or "expert_enduro_repeatability",
        "status": milestone.get("status") or "active",
        "verified_rung": "four_short_descents_quality_density_not_complete_cycles",
        "verified_evidence": latest.get("verified_level"),
        "next_proof": latest.get("next_gate") or "Three complete Twin Peaks climb-stage cycles.",
        "latest_feedback": (
            {
                "date": latest_mtb.get("date"),
                "stop_rule_outcome": latest_mtb.get("stop_rule_outcome"),
                "technical_review_present": latest_mtb.get("technical_review_present"),
            }
            if latest_mtb
            else None
        ),
        "latest_mtb_exposure": (
            {
                "date": latest_mtb_exposure.get("date"),
                "role_credit": latest_mtb_exposure.get("role_credit"),
                "stop_rule_outcome": latest_mtb_exposure.get("stop_rule_outcome"),
            }
            if latest_mtb_exposure
            else None
        ),
        "promotion_gate": (
            "Final climb RPE <=6; every final-run technical dimension within one point of the first; "
            "braking and upper-body fatigue <=3; no reactive braking, rescue, near miss, or fade; "
            "race-relevant fueling; explicit not_triggered; clean next morning."
        ),
        "one_variable_rule": "Progress only one of speed, roughness, fatigue, novelty, setup, or cycle count at a time.",
        "complete_cycle_proof_evaluation": {
            "decision": "eligible_for_head_coach_promotion_review" if proof_passed else "hold_incomplete_or_failed_gate",
            "reported_complete_cycles": complete_cycles,
            "gates": proof_gates,
            "rule": "Only explicit structured fields count; missing values remain unknown and narrative capacity does not become an executed proof.",
        },
        "skill_tracks": skill_tracks,
    }


def _absorption_state(ledger: list[dict[str, Any]]) -> dict[str, Any]:
    if not ledger:
        return {
            "status": "unknown",
            "latest_review_date": None,
            "reason": "No structured session review is available in the lookback window.",
        }
    latest = ledger[-1]
    if latest.get("outcome_class") == "out_of_policy":
        status = "out_of_policy"
        reason = "The stop rule triggered and the written boundary was continued."
    elif latest.get("stop_rule_outcome") in {"triggered_and_stopped", "triggered_and_downshifted"}:
        status = "absorbed_boundary" if latest.get("next_day_response_explicit") else "pending"
        reason = "The trigger was obeyed; next-day evidence determines absorption."
    elif latest.get("stop_rule_outcome") == "not_triggered" and latest.get("next_day_response_explicit"):
        status = "absorbed_clean"
        reason = "The session stayed inside the written stop rule and has explicit next-day response evidence."
    elif latest.get("stop_rule_outcome") == "not_triggered":
        status = "pending"
        reason = "Execution was inside the stop rule, but next-day absorption is not explicit yet."
    else:
        status = "unknown"
        reason = "The latest review lacks an explicit stop-rule outcome."
    return {
        "status": status,
        "latest_review_date": latest.get("date"),
        "family": latest.get("family"),
        "stop_rule_outcome": latest.get("stop_rule_outcome"),
        "next_day_response_explicit": latest.get("next_day_response_explicit"),
        "reason": reason,
        "guardrail": "Absorption status can hold or downshift progression; it is not same-day training clearance.",
    }


def _fitness_gap(training_status: dict[str, Any]) -> dict[str, Any]:
    vo2 = training_status.get("vo2max") or {}
    load = training_status.get("acute_chronic") or {}
    current_vo2 = as_number(vo2.get("cycling_precise"))
    chronic = as_number(load.get("chronic_load"))
    reference_vo2 = 51.1
    reference_chronic = 561.0
    return {
        "cycling_vo2max": {
            "current": current_vo2,
            "reference": reference_vo2,
            "gap": round(reference_vo2 - current_vo2, 1) if current_vo2 is not None else None,
        },
        "chronic_load": {
            "current": chronic,
            "reference": reference_chronic,
            "gap": round(reference_chronic - chronic, 1) if chronic is not None else None,
        },
        "interpretation": "These markers expose the fitness gap; neither is the objective function or permission to add cost.",
    }


def _limiter_ranking(
    budget: dict[str, Any],
    fitness_gap: dict[str, Any],
    engine: dict[str, Any],
    technical: dict[str, Any],
    decision: dict[str, Any],
) -> list[dict[str, Any]]:
    three_week_average = as_number(budget.get("previous_three_week_bike_day_average"))
    vo2_gap = as_number((fitness_gap.get("cycling_vo2max") or {}).get("gap"))
    chronic_gap = as_number((fitness_gap.get("chronic_load") or {}).get("gap"))
    ranking = [
        {
            "limiter": "bike_specific_continuity_and_seated_endurance",
            "priority": 1,
            "evidence": {
                "previous_three_week_bike_day_average": three_week_average,
                "normal_build_target": 5,
                "cycling_vo2max_gap": vo2_gap,
                "chronic_load_gap": chronic_gap,
            },
            "training_response": "Restore low-cost bike frequency and progress 60->75->90-minute endurance one duration lever at a time.",
        },
        {
            "limiter": "complete_climb_stage_enduro_repeatability",
            "priority": 2,
            "evidence": {
                "verified_rung": technical.get("verified_rung"),
                "next_proof": technical.get("next_proof"),
            },
            "training_response": "Protect the next complete-cycle proof; short-descents and reported reserve do not substitute for it.",
        },
        {
            "limiter": "structured_engine_force_and_repeat_power",
            "priority": 3,
            "evidence": {
                "torque_decision": (engine.get("torque") or {}).get("decision"),
                "vo2_decision": (engine.get("vo2") or {}).get("decision"),
            },
            "training_response": "Own 3x8 inside its ceiling before 3x10/3x12; admit 4x3 VO2 only in a permitted absorbed block.",
        },
        {
            "limiter": "expert_skill_execution_under_fatigue",
            "priority": 4,
            "evidence": {
                "skill_tracks": technical.get("skill_tracks"),
                "complete_cycle_proof": technical.get("complete_cycle_proof_evaluation"),
            },
            "training_response": "Use isolated clean-repetition gates for corner exit, front authority, scrub/landing, sliding and rough terrain; add one consequence variable only.",
        },
        {
            "limiter": "upper_body_and_braking_durability_monitor",
            "priority": 5,
            "evidence": "Recent clean short-run reports reduce confidence that this remains the primary limiter; re-rank only from long-stage or race-practice evidence.",
            "training_response": "Monitor on complete-cycle and race-practice work rather than prescribing extra fatigue from stale assumptions.",
        },
    ]
    active = decision.get("active_lever")
    for item in ranking:
        item["active_this_block"] = bool(
            (active in {"bike_specific_continuity", "frequency", "endurance_duration"} and item["priority"] == 1)
            or (active in {"complete_cycle_count", "technical_consequence"} and item["priority"] in {2, 4})
            or (active in {"engine_dose", "tempo_torque", "vo2"} and item["priority"] == 3)
        )
    return ranking


def _recommended_roles(
    mode: str,
    shape: dict[str, Any],
    budget: dict[str, Any],
    preferred_mtb_role: dict[str, Any],
) -> list[dict[str, Any]]:
    if mode == "replacement_sabbath":
        return [{"role": "hard_rest", "count": 1, "cost": "none", "status": "required"}]
    if mode == "event_race":
        return [{"role": "race_execution", "count": 1, "cost": "meaningful", "status": "event"}]
    if mode == "event_practice":
        return [{"role": "official_practice", "count": 1, "cost": "meaningful", "status": "event"}]

    roles: list[dict[str, Any]] = []
    delivered = budget.get("delivered_role_counts") or {}
    low_cost_count = max(1, shape["preferred_unique_bike_days"] - shape["meaningful_cost_days_max"])
    roles.append(
        {
            "role": "low_cost_bike_continuity",
            "count": low_cost_count,
            "completed": delivered.get("low_cost_endurance", 0),
            "cost": "low",
            "status": (
                "fulfilled"
                if delivered.get("low_cost_endurance", 0) >= low_cost_count
                else "remaining"
            ),
        }
    )
    if shape["protected_mtb_days"]:
        if shape["protected_mtb_days"] == 1:
            first_mtb_role = preferred_mtb_role.get("role") or "protected_mtb_quality_or_race_transfer"
        else:
            first_mtb_role = "protected_stumpjumper_quality_fitness"
        possible_costs = (budget.get("current_calendar_week") or {}).get(
            "possible_meaningful_cost_days", 0
        )
        remaining_costs = budget.get("meaningful_cost_days_remaining", 0)
        if remaining_costs > 0 and possible_costs:
            density_fit = "uses_remaining_confirmed_slot_with_prior_possible_cost_requiring_same_day_resolution"
        elif remaining_costs > 0:
            density_fit = "uses_remaining_meaningful_slot"
        else:
            density_fit = "confirmed_meaningful_budget_spent"
        roles.append(
            {
                "role": first_mtb_role,
                "count": 1,
                "cost": "meaningful",
                "completed": delivered.get("protected_mtb", 0),
                "density_fit": density_fit,
                "selection_basis": preferred_mtb_role.get("selection_basis"),
                "planned_date": preferred_mtb_role.get("date"),
                "source": preferred_mtb_role.get("source"),
                "status": (
                    "fulfilled"
                    if delivered.get("protected_mtb", 0) >= 1
                    else "conditional_same_day_gate" if mode == "absorption" else "remaining"
                ),
            }
        )
    if shape["protected_mtb_days"] >= 2:
        roles.append(
            {
                "role": "protected_enduro_durability_or_race_transfer",
                "count": 1,
                "cost": "meaningful",
                "status": "recce_consumes_role" if mode == "race_specific" else "programmed",
            }
        )
    if mode not in {"absorption", "taper", "race_recovery_transition", "consolidation"}:
        roles.append(
            {
                "role": "structured_engine_or_costly_third_mtb",
                "count": 1,
                "cost": "meaningful",
                "status": "mutually_exclusive",
            }
        )
    elif mode == "absorption":
        roles.append(
            {
                "role": "structured_engine",
                "count": 0,
                "completed": delivered.get("structured_engine", 0),
                "cost": "meaningful",
                "status": "already_spent_or_not_added_during_absorption",
            }
        )
    if shape["maximum_unique_bike_days"] > shape["preferred_unique_bike_days"]:
        roles.append({"role": "optional_sixth_recovery_touch", "count": 1, "cost": "low", "status": "optional_after_absorption"})
    return roles


def _progression_decision(
    target: date,
    mode: str,
    readiness: dict[str, Any],
    cns: dict[str, Any],
    budget: dict[str, Any],
    engine: dict[str, Any],
) -> dict[str, Any]:
    if mode == "replacement_sabbath" or (target.weekday() == 6 and mode != "event_race"):
        return {
            "program_action": "rest",
            "active_lever": "none",
            "primary_adaptation_target": "absorption",
            "reason": "Sunday or exact replacement Sabbath is a hard no-exercise constraint.",
            "next_constraint": "Resume only after the protected rest day.",
        }
    if readiness.get("readiness_level") == "red" or cns.get("status") in {"impaired", "compromised"}:
        return {
            "program_action": "downshift",
            "active_lever": "none",
            "primary_adaptation_target": "restore_physical_and_cns_readiness",
            "reason": "The lowest current safety ceiling overrides progression.",
            "next_constraint": "Require fresh non-red physical readiness and a non-impaired CNS ceiling.",
        }
    if mode in {"taper", "consolidation", "race_recovery_transition", "benchmark"}:
        return {
            "program_action": "consolidate",
            "active_lever": "frequency_without_added_cost",
            "primary_adaptation_target": "absorb_prior_work_while_preserving_bike_specificity",
            "reason": f"The active roadmap mode is {mode}.",
            "next_constraint": "Do not add volume or consequence until the dated block and absorption gates permit it.",
        }
    if mode == "absorption" or (engine.get("torque") or {}).get("decision") == "hold_no_promotion":
        return {
            "program_action": "absorb_and_hold",
            "active_lever": "bike_specific_continuity",
            "primary_adaptation_target": "absorb_torque_boundary_and_preserve_bike_rhythm",
            "reason": "A recent torque stop-rule override blocks engine progression, while low-cost continuity remains trainable.",
            "next_constraint": "No torque/VO2 promotion until a clean within-ceiling repeat and next-day response exist.",
        }
    if budget.get("meaningful_cost_days_remaining", 0) <= 0:
        return {
            "program_action": "hold_cost_add_low_only",
            "active_lever": "frequency",
            "primary_adaptation_target": "bike_specific_continuity_without_more_meaningful_cost",
            "reason": "The weekly meaningful-cost budget is spent.",
            "next_constraint": "Only low-cost frequency may be added; do not hide another costly day as continuity.",
        }
    return {
        "program_action": "build_one_lever",
        "active_lever": "endurance_duration",
        "primary_adaptation_target": "bike_specific_aerobic_and_muscular_durability",
        "reason": "Continuity and chronic-load gaps are larger than the cardiovascular cost at the current easy power anchor.",
        "next_constraint": "Progress duration, engine dose, technical consequence, or cycle count—never more than one at once.",
    }


def _programming_audit(shape: dict[str, Any], budget: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    current = budget.get("current_calendar_week") or {}
    misses: list[dict[str, Any]] = []
    if current.get("unique_bike_days", 0) < shape["preferred_unique_bike_days"]:
        misses.append(
            {
                "type": "bike_frequency_gap",
                "actual": current.get("unique_bike_days"),
                "target": shape["preferred_unique_bike_days"],
                "remaining": budget.get("preferred_bike_days_remaining"),
            }
        )
    protected_completed = budget.get("protected_mtb_role_days_completed", 0)
    if protected_completed < shape["protected_mtb_days"]:
        misses.append(
            {
                "type": "protected_mtb_gap",
                "actual": protected_completed,
                "raw_mtb_exposure_days": current.get("unique_mtb_days"),
                "target": shape["protected_mtb_days"],
                "remaining": budget.get("mtb_days_remaining_to_protect"),
            }
        )
    if current.get("meaningful_cost_days", 0) > shape["meaningful_cost_days_max"]:
        misses.append(
            {
                "type": "meaningful_cost_budget_breached",
                "actual": current.get("meaningful_cost_days"),
                "maximum": shape["meaningful_cost_days_max"],
            }
        )
    if current.get("possible_meaningful_cost_days", 0) > 0:
        misses.append(
            {
                "type": "possible_meaningful_cost_requires_head_coach_resolution",
                "count": current.get("possible_meaningful_cost_days"),
                "effect": "Does not consume a confirmed slot automatically, but must inform the consequence and recovery gate for the next meaningful role.",
            }
        )
    return {
        "status": "attention" if misses else "on_track",
        "items": misses,
        "program_action": decision.get("program_action"),
        "interpretation": (
            "A safety downshift can explain an underdose, but it must not erase it. "
            "Programming misses and justified constraints remain separate."
        ),
    }


def _text_report(artifact: dict[str, Any]) -> str:
    block = artifact["roadmap_block"]
    budget = artifact["weekly_budget"]
    current = budget["current_calendar_week"]
    decision = artifact["progression_decision"]
    endurance = artifact["progression_tracks"]["endurance"]
    torque = artifact["progression_tracks"]["engine"]["torque"]
    technical = artifact["progression_tracks"]["technical"]
    lines = [
        f"Adaptive training programming state - {artifact['date']}",
        f"Roadmap: {block['label']} ({block['program_mode']})",
        f"Program action: {decision['program_action']}",
        f"Active lever: {decision['active_lever']}",
        f"Primary target: {decision['primary_adaptation_target']}",
        "",
        "Weekly execution:",
        f"- Bike days: {current['unique_bike_days']} / preferred {artifact['target_shape']['preferred_unique_bike_days']}",
        f"- MTB days: {current['unique_mtb_days']} / protect {artifact['target_shape']['protected_mtb_days']}",
        f"- Confirmed meaningful-cost days: {current['meaningful_cost_days']} / max {artifact['target_shape']['meaningful_cost_days_max']}",
        f"- Possible meaningful-cost days needing coach resolution: {current.get('possible_meaningful_cost_days', 0)}",
        "",
        "Progression tracks:",
        f"- Endurance: {endurance['current_rung']} -> {endurance['decision']}",
        f"- Torque: {torque['current_rung']} -> {torque['decision']}",
        f"- Technical: {technical['verified_rung']}",
        f"- Next proof: {technical['next_proof']}",
        "",
        f"Next constraint: {decision['next_constraint']}",
    ]
    return "\n".join(lines) + "\n"


def build_adaptive_training_state(
    root: str | Path | None = None,
    for_date: str | date | None = None,
    *,
    context: dict[str, Any] | None = None,
    activities: list[dict[str, Any]] | None = None,
    readiness: dict[str, Any] | None = None,
    cns_readiness: dict[str, Any] | None = None,
    training_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the persistent programming layer above Garmin evidence.

    The controller selects direction, progression rungs and weekly role budgets. It
    cannot authorize a same-day session over Sabbath, physical readiness, CNS,
    symptoms, environment or consequence. Garmin MCP remains direct model
    perception and is deliberately not ingested here.
    """

    context = context if isinstance(context, dict) else load_context(root)
    timezone = (context.get("athlete") or {}).get("timezone", DEFAULT_TIMEZONE)
    target = parse_date(for_date) or today_local(timezone)
    activities = activities if activities is not None else load_activities(root)
    readiness = readiness if isinstance(readiness, dict) else {}
    cns_readiness = cns_readiness if isinstance(cns_readiness, dict) else {}
    training_status = training_status if isinstance(training_status, dict) else {}

    roadmap = _parse_roadmap_block(root, target)
    shape = _target_shape(roadmap["program_mode"])
    budget = _weekly_budget(activities, target, shape)
    ledger = _feedback_ledger(root, target)
    _apply_delivered_role_credit(budget, ledger, target, shape)
    endurance = _endurance_track(ledger)
    engine = _engine_track(ledger, roadmap["program_mode"])
    technical = _technical_track(context, ledger)
    absorption = _absorption_state(ledger)
    decision = _progression_decision(
        target,
        roadmap["program_mode"],
        readiness,
        cns_readiness,
        budget,
        engine,
    )
    preferred_mtb_role = _preferred_protected_mtb_role(root, target, roadmap)
    roles = _recommended_roles(roadmap["program_mode"], shape, budget, preferred_mtb_role)
    fitness_gap = _fitness_gap(training_status)
    artifact = {
        "artifact_type": "adaptive_training_programming_state",
        "version": ADAPTIVE_TRAINING_VERSION,
        "date": target.isoformat(),
        "generated_at": iso_now(timezone),
        "status": "ready" if roadmap.get("status") == "matched" else "provisional",
        "objective": (
            "Improve Clayton's bike-specific fitness and expert-enduro execution by programming "
            "the next useful adaptation, then requiring explicit absorption and technical evidence before promotion."
        ),
        "architecture_boundary": {
            "garmin_mcp": "Direct live perception for head-coach cognition; no generic MCP-to-stack ingestion bridge.",
            "stack": "Persistent, deterministic and testable programming state, progression gates, budgets, contracts and audit.",
            "head_coach": "Interprets MCP plus stack evidence and owns the final same-day schema-v3 prescription.",
        },
        "roadmap_block": roadmap,
        "target_shape": shape,
        "weekly_budget": budget,
        "fitness_gap": fitness_gap,
        "trainable_limiter_ranking": _limiter_ranking(
            budget,
            fitness_gap,
            engine,
            technical,
            decision,
        ),
        "absorption_state": absorption,
        "progression_tracks": {
            "endurance": endurance,
            "engine": engine,
            "technical": technical,
        },
        "progression_decision": decision,
        "protected_mtb_role_selection": preferred_mtb_role,
        "recommended_week_roles": roles,
        "programming_audit": _programming_audit(shape, budget, decision),
        "feedback_ledger_summary": {
            "lookback_days": 84,
            "session_reviews": len(ledger),
            "explicit_stop_outcomes": sum(1 for item in ledger if item["stop_rule_outcome_explicit"]),
            "out_of_policy_sessions": [
                {"date": item["date"], "family": item["family"], "stop_rule_outcome": item["stop_rule_outcome"]}
                for item in ledger
                if item["outcome_class"] == "out_of_policy"
            ],
        },
        "guardrails": [
            "The controller cannot override Sabbath, red physical readiness, the CNS ceiling, symptoms, environment, or technical consequence.",
            "A triggered_but_continued outcome never promotes the nominal progression rung.",
            "Garmin load is an audit range, not the objective function.",
            "Missing review evidence holds promotion; it does not prove failure or absence.",
            "One progressed lever at a time: frequency, duration, engine dose, cycle count, technical consequence, or strength.",
        ],
        "provenance": {
            "canonical_strategy": "config/athlete_context.json",
            "canonical_architecture": "config/coaching_architecture.json",
            "dated_macrocycle": roadmap.get("source"),
            "objective_training": "normalized local Garmin activity evidence",
            "subjective_outcomes": "input/feedback_YYYY-MM-DD.json",
            "mcp_copy_performed": False,
        },
    }
    write_json(snapshots_dir(root) / "adaptive_training.json", artifact)
    write_text(snapshots_dir(root) / "adaptive_training.txt", _text_report(artifact))
    return artifact
