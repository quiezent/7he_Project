from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from statistics import mean
from typing import Any

from .adaptation_profile import (
    _activity_rows,
    _adaptation_associations,
    _daily,
    _monthly,
    _rolling_highlights,
)
from .io import read_json, write_json, write_text
from .paths import snapshots_dir
from .time_utils import DEFAULT_TIMEZONE, iso_now, parse_date, today_local


def _round(value: float | None, digits: int = 2) -> float | None:
    return round(value, digits) if value is not None else None


def _avg(values: list[float | None]) -> float | None:
    usable = [value for value in values if value is not None]
    return mean(usable) if usable else None


def _ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def _corr(associations: dict, feature: str) -> float | None:
    for item in associations.get("correlations", []):
        if item.get("feature") == feature:
            return item.get("pearson_r")
    return None


def _quartile_delta(associations: dict, feature: str) -> dict | None:
    for item in associations.get("top_vs_bottom_quartile", []):
        if item.get("feature") == feature:
            return item
    return None


def _score_support(score: int) -> str:
    if score >= 4:
        return "strong"
    if score >= 2:
        return "moderate"
    if score >= 1:
        return "weak"
    return "not_supported"


def _make_scope(root: str | Path | None, target: date, days: int | None) -> dict:
    start = date(1900, 1, 1) if days is None or days <= 0 else target - timedelta(days=days - 1)
    rows = _activity_rows(root, start, target)
    if days is None or days <= 0:
        start = parse_date(rows[0]["date"]) if rows else target
    assert start is not None
    daily = _daily(rows, start, target)
    months = _monthly(rows)
    return {
        "start": start,
        "target": target,
        "rows": rows,
        "daily": daily,
        "months": months,
        "associations": _adaptation_associations(rows, daily, start, target),
        "rolling": _rolling_highlights(daily, start, target),
    }


def _months_with_p20(months: list[dict]) -> list[dict]:
    return [month for month in months if month.get("best_p20_w") is not None]


def _month_summary(month: dict) -> dict:
    return {
        "month": month.get("month"),
        "training_load": month.get("training_load"),
        "bike_specific_load": month.get("bike_specific_load"),
        "elliptical_load": month.get("elliptical_load"),
        "mtb_sessions": month.get("mtb_sessions"),
        "hard_sessions": month.get("hard_sessions"),
        "best_p20_w": month.get("best_p20_w"),
        "best_vo2max": month.get("best_vo2max"),
    }


def _condition_stats(months: list[dict], predicate) -> dict:
    selected = [month for month in months if predicate(month)]
    p20_months = _months_with_p20(selected)
    return {
        "months": len(selected),
        "months_with_p20": len(p20_months),
        "avg_best_p20_w": _round(_avg([month.get("best_p20_w") for month in p20_months]), 1),
        "max_best_p20_w": max([month.get("best_p20_w") for month in p20_months], default=None),
        "avg_best_vo2max": _round(_avg([month.get("best_vo2max") for month in selected]), 1),
        "avg_training_load": _round(_avg([month.get("training_load") for month in selected]), 1),
        "avg_bike_specific_load": _round(_avg([month.get("bike_specific_load") for month in selected]), 1),
        "avg_mtb_sessions": _round(_avg([month.get("mtb_sessions") for month in selected]), 1),
        "examples": [_month_summary(month) for month in selected[:8]],
    }


def _best_mtb_load_by_month(rows: list[dict]) -> dict[str, float]:
    out: dict[str, float] = defaultdict(float)
    for row in rows:
        if row.get("category") != "mtb":
            continue
        month = str(row.get("date"))[:7]
        load = row.get("training_load") or 0.0
        if load > out[month]:
            out[month] = load
    return dict(out)


def _training_response_groups(root: str | Path | None) -> dict:
    path = snapshots_dir(root) / "training_response_dataset.json"
    dataset = read_json(path, {})
    rows = dataset.get("rows") or []
    groups = {
        "rest_or_easy": [],
        "moderate_load": [],
        "hard_or_intense": [],
        "very_high_load": [],
    }
    for row in rows:
        features = row.get("features") or {}
        target = row.get("target") or {}
        score = target.get("next_day_response_score")
        if score is None:
            continue
        load = features.get("today_training_load") or 0.0
        high = features.get("today_high_intensity_min") or 0.0
        if load >= 120 or high >= 20:
            groups["very_high_load"].append(float(score))
        elif load >= 75 or high >= 10:
            groups["hard_or_intense"].append(float(score))
        elif load > 0:
            groups["moderate_load"].append(float(score))
        else:
            groups["rest_or_easy"].append(float(score))
    return {
        "source": "snapshots/training_response_dataset.json",
        "target": dataset.get("target"),
        "samples": len(rows),
        "groups": {
            name: {
                "samples": len(values),
                "avg_next_day_response_score": _round(_avg(values), 1),
                "ready_rate": _round(sum(1 for value in values if value >= 70) / len(values), 3)
                if values
                else None,
            }
            for name, values in groups.items()
        },
        "caveat": "Response target is Garmin-derived next-day wellness/readiness, not direct trail performance.",
    }


def _hypothesis_bike_specificity(full: dict, recent: dict) -> dict:
    full_assoc = full["associations"]
    recent_assoc = recent["associations"]
    score = 0
    if (_corr(recent_assoc, "prev_28d_bike_specific_load") or 0) > (_corr(recent_assoc, "prev_28d_training_load") or 0):
        score += 1
    if (_corr(recent_assoc, "prev_28d_indoor_bike_load") or 0) > 0.35:
        score += 1
    if (_corr(recent_assoc, "prev_28d_elliptical_load") or 0) < 0:
        score += 1
    if (_quartile_delta(recent_assoc, "prev_28d_bike_specific_load") or {}).get("difference_top_minus_bottom", 0) > 300:
        score += 1
    return {
        "id": "bike_specificity",
        "hypothesis": "Bike-specific load predicts cycling power better than total/non-bike load.",
        "result": _score_support(score),
        "confidence": "high_recent_year_moderate_full_range",
        "evidence": {
            "recent_365_correlations": {
                "indoor_bike_load": _corr(recent_assoc, "prev_28d_indoor_bike_load"),
                "bike_specific_load": _corr(recent_assoc, "prev_28d_bike_specific_load"),
                "total_training_load": _corr(recent_assoc, "prev_28d_training_load"),
                "elliptical_load": _corr(recent_assoc, "prev_28d_elliptical_load"),
            },
            "full_range_correlations": {
                "indoor_bike_load": _corr(full_assoc, "prev_28d_indoor_bike_load"),
                "bike_specific_load": _corr(full_assoc, "prev_28d_bike_specific_load"),
                "total_training_load": _corr(full_assoc, "prev_28d_training_load"),
                "elliptical_load": _corr(full_assoc, "prev_28d_elliptical_load"),
            },
            "recent_365_top_vs_bottom_p20_quartile": {
                "bike_specific_load": _quartile_delta(recent_assoc, "prev_28d_bike_specific_load"),
                "indoor_bike_load": _quartile_delta(recent_assoc, "prev_28d_indoor_bike_load"),
                "elliptical_load": _quartile_delta(recent_assoc, "prev_28d_elliptical_load"),
            },
        },
        "counterpoints": [
            "Full-range correlations are diluted by different eras, missing power-expression opportunities, and older run-heavy data.",
            "High MTB months can improve durability and VO2 without producing a clean P20 test that month.",
        ],
        "coaching_implication": "Do not let elliptical, run, or gym replace the weekly bike-specific minimum when the goal is enduro performance.",
    }


def _hypothesis_nonbike_substitution(recent: dict) -> dict:
    months = recent["months"]
    high_nonbike = _condition_stats(
        months,
        lambda m: (m.get("training_load") or 0) >= 1500
        and (m.get("bike_specific_load") or 0) < 600,
    )
    high_bike = _condition_stats(
        months,
        lambda m: (m.get("training_load") or 0) >= 1500
        and (m.get("bike_specific_load") or 0) >= 1200,
    )
    score = 0
    if high_nonbike["avg_best_p20_w"] is not None and high_bike["avg_best_p20_w"] is not None:
        if high_bike["avg_best_p20_w"] - high_nonbike["avg_best_p20_w"] >= 20:
            score += 2
    if high_nonbike["avg_best_vo2max"] is not None and high_bike["avg_best_vo2max"] is not None:
        if high_bike["avg_best_vo2max"] > high_nonbike["avg_best_vo2max"]:
            score += 1
    if high_nonbike["months"] >= 2:
        score += 1
    return {
        "id": "nonbike_substitution",
        "hypothesis": "High non-bike aerobic load preserves work capacity but reduces cycling-specific fitness when bike load is low.",
        "result": _score_support(score),
        "confidence": "moderate",
        "evidence": {
            "recent_high_total_low_bike_months": high_nonbike,
            "recent_high_total_high_bike_months": high_bike,
        },
        "counterpoints": [
            "This is strongest during the historical injury/treatment era, so that timeline is a confounder.",
            "Elliptical may have protected general aerobic capacity and recovery even while bike power fell.",
        ],
        "coaching_implication": "Use non-bike modalities as support, not as the backbone of a bike-performance block.",
    }


def _hypothesis_structured_intensity(full: dict, recent: dict, response: dict) -> dict:
    full_months = full["months"]
    recent_months = recent["months"]
    strong_structured = _condition_stats(
        full_months,
        lambda m: (m.get("hard_sessions") or 0) >= 12
        and (m.get("bike_specific_load") or 0) >= 1800,
    )
    low_structure = _condition_stats(
        full_months,
        lambda m: (m.get("hard_sessions") or 0) <= 5
        and (m.get("bike_specific_load") or 0) >= 500,
    )
    recent_high_structure = _condition_stats(
        recent_months,
        lambda m: (m.get("hard_sessions") or 0) >= 10
        and (m.get("bike_specific_load") or 0) >= 1000,
    )
    hard_response = response.get("groups", {}).get("hard_or_intense", {})
    easy_response = response.get("groups", {}).get("rest_or_easy", {})
    score = 0
    if strong_structured["avg_best_p20_w"] and low_structure["avg_best_p20_w"]:
        if strong_structured["avg_best_p20_w"] > low_structure["avg_best_p20_w"]:
            score += 1
    if recent_high_structure["months"] > 0:
        score += 1
    if (_corr(recent["associations"], "prev_28d_hard_sessions") or 0) > 0.3:
        score += 1
    if hard_response.get("avg_next_day_response_score") is not None and easy_response.get("avg_next_day_response_score") is not None:
        if hard_response["avg_next_day_response_score"] < easy_response["avg_next_day_response_score"]:
            score += 1
    return {
        "id": "structured_intensity",
        "hypothesis": "Structured tempo/threshold intensity improves sustained power, but carries next-day recovery cost.",
        "result": _score_support(score),
        "confidence": "moderate",
        "evidence": {
            "full_range_high_structure_months": strong_structured,
            "full_range_low_structure_months": low_structure,
            "recent_365_high_structure_months": recent_high_structure,
            "recent_365_hard_session_correlation_to_next_p20": _corr(recent["associations"], "prev_28d_hard_sessions"),
            "next_day_response_groups": response,
        },
        "counterpoints": [
            "Power targets are only visible when a workout tests power; some MTB fitness months may be under-scored for P20.",
            "Garmin next-day response is a wellness proxy and may miss trail-specific fatigue.",
        ],
        "coaching_implication": "Use structured bike intensity regularly, but place it away from key trail days and progress duration before power.",
    }


def _hypothesis_mtb_durability(full: dict, recent: dict) -> dict:
    full_months = full["months"]
    mtb_best = _best_mtb_load_by_month(full["rows"])
    high_mtb = _condition_stats(full_months, lambda m: (m.get("mtb_sessions") or 0) >= 8)
    low_mtb = _condition_stats(full_months, lambda m: (m.get("mtb_sessions") or 0) <= 2 and (m.get("sessions") or 0) >= 8)
    high_mtb_best_loads = [
        mtb_best.get(month["month"])
        for month in full_months
        if (month.get("mtb_sessions") or 0) >= 8 and mtb_best.get(month["month"]) is not None
    ]
    low_mtb_best_loads = [
        mtb_best.get(month["month"])
        for month in full_months
        if (month.get("mtb_sessions") or 0) <= 2 and mtb_best.get(month["month"]) is not None
    ]
    score = 0
    if high_mtb["avg_best_vo2max"] and low_mtb["avg_best_vo2max"] and high_mtb["avg_best_vo2max"] >= low_mtb["avg_best_vo2max"]:
        score += 1
    if _avg(high_mtb_best_loads) and _avg(low_mtb_best_loads) and _avg(high_mtb_best_loads) > _avg(low_mtb_best_loads):
        score += 2
    if recent["rolling"]["recent_28d"]["category_sessions"].get("mtb", 0) < 8:
        score += 1
    return {
        "id": "mtb_durability",
        "hypothesis": "Enduro durability requires repeated MTB exposure; indoor power alone is insufficient.",
        "result": _score_support(score),
        "confidence": "high_for_durability_moderate_for_power",
        "evidence": {
            "full_range_high_mtb_months": high_mtb,
            "full_range_low_mtb_months": low_mtb,
            "avg_best_mtb_session_load": {
                "high_mtb_months": _round(_avg(high_mtb_best_loads), 1),
                "low_mtb_months_with_mtb": _round(_avg(low_mtb_best_loads), 1),
            },
            "current_recent_28d": recent["rolling"]["recent_28d"],
        },
        "counterpoints": [
            "Indoor training can raise sustained power quickly, but it does not test braking, descending HR cost, grip, or repeat trail handling.",
            "Some high-MTB months have lower P20 because they did not contain a clean indoor test.",
        ],
        "coaching_implication": "For enduro, rebuild toward two reliable MTB exposures per week before chasing old indoor power numbers.",
    }


def _hypothesis_consistency_detraining(full: dict, recent: dict) -> dict:
    full_assoc = full["associations"]
    recent_assoc = recent["associations"]
    low_bike_recent = _condition_stats(recent["months"], lambda m: (m.get("bike_specific_load") or 0) < 700 and (m.get("sessions") or 0) >= 6)
    adequate_bike_recent = _condition_stats(recent["months"], lambda m: (m.get("bike_specific_load") or 0) >= 1200)
    score = 0
    if (_corr(full_assoc, "prev_28d_rest_days") or 0) < 0:
        score += 1
    if (_corr(recent_assoc, "prev_28d_rest_days") or 0) < 0:
        score += 1
    if adequate_bike_recent["avg_best_p20_w"] and low_bike_recent["avg_best_p20_w"]:
        if adequate_bike_recent["avg_best_p20_w"] > low_bike_recent["avg_best_p20_w"]:
            score += 2
    return {
        "id": "consistency_detraining",
        "hypothesis": "Cycling fitness drops when bike-specific frequency/load falls below a maintenance floor.",
        "result": _score_support(score),
        "confidence": "high",
        "evidence": {
            "rest_day_correlation_to_next_p20": {
                "full_range": _corr(full_assoc, "prev_28d_rest_days"),
                "recent_365": _corr(recent_assoc, "prev_28d_rest_days"),
            },
            "recent_low_bike_months": low_bike_recent,
            "recent_adequate_bike_months": adequate_bike_recent,
            "full_range_lowest_active_28d": full["rolling"]["lowest_active_28d_load"],
        },
        "counterpoints": [
            "Rest days are necessary; this does not argue against Sabbath or recovery.",
            "The issue is long gaps or low bike frequency, not a single rest day.",
        ],
        "coaching_implication": "Keep Sabbath and recovery, but avoid multi-week bike droughts; maintenance probably needs about 600-800 bike-load/month, rebuilding more like 1200+.",
    }


def _hypothesis_gym_support(full: dict, recent: dict) -> dict:
    score = 0
    full_corr = _corr(full["associations"], "prev_28d_gym_load")
    recent_corr = _corr(recent["associations"], "prev_28d_gym_load")
    if full_corr and full_corr > 0:
        score += 1
    if recent_corr and recent_corr > 0:
        score += 1
    heavy_gym_recent = _condition_stats(recent["months"], lambda m: (m.get("by_category", {}).get("gym", {}).get("training_load") or 0) >= 40)
    return {
        "id": "gym_support",
        "hypothesis": "Gym work supports cycling performance when it is supportive rather than replacing bike work.",
        "result": _score_support(score),
        "confidence": "weak_to_moderate",
        "evidence": {
            "gym_load_correlation_to_next_p20": {
                "full_range": full_corr,
                "recent_365": recent_corr,
            },
            "recent_months_with_gym_load_40_plus": heavy_gym_recent,
        },
        "counterpoints": [
            "Gym sample is small compared with MTB and indoor bike.",
            "Garmin load does not measure strength quality, soreness, or neuromuscular fatigue well.",
        ],
        "coaching_implication": "Use gym as a small dose for durability, not as a substitute for bike work or before key trail rides.",
    }


def build_training_hypotheses(
    root: str | Path | None = None,
    for_date: str | date | None = None,
) -> dict:
    target = parse_date(for_date) or today_local(DEFAULT_TIMEZONE)
    full = _make_scope(root, target, None)
    recent = _make_scope(root, target, 365)
    response = _training_response_groups(root)
    hypotheses = [
        _hypothesis_bike_specificity(full, recent),
        _hypothesis_nonbike_substitution(recent),
        _hypothesis_structured_intensity(full, recent, response),
        _hypothesis_mtb_durability(full, recent),
        _hypothesis_consistency_detraining(full, recent),
        _hypothesis_gym_support(full, recent),
    ]
    artifact = {
        "date": target.isoformat(),
        "generated_at": iso_now(DEFAULT_TIMEZONE),
        "analysis_type": "garmin_training_hypothesis_tests",
        "scope": {
            "full_range": {
                "start": full["start"].isoformat(),
                "end": target.isoformat(),
                "activity_rows": len(full["rows"]),
                "association_samples": full["associations"].get("samples"),
            },
            "recent_365": {
                "start": recent["start"].isoformat(),
                "end": target.isoformat(),
                "activity_rows": len(recent["rows"]),
                "association_samples": recent["associations"].get("samples"),
            },
        },
        "hypotheses": hypotheses,
        "summary": {
            "strong_or_moderate": [
                item["id"]
                for item in hypotheses
                if item["result"] in {"strong", "moderate"}
            ],
            "weak_or_unproven": [
                item["id"]
                for item in hypotheses
                if item["result"] not in {"strong", "moderate"}
            ],
        },
        "caveats": [
            "All hypotheses are observational and personal to this Garmin record.",
            "Power metrics require activities that actually express power; months without tests can look worse than true fitness.",
            "Full-range associations blend different training eras; recent-year evidence is often more actionable for current coaching.",
            "Wellness response is strongest only where Garmin recovery fields are available and synced.",
        ],
        "artifacts": {
            "json": "snapshots/training_hypothesis_tests.json",
            "text": "snapshots/training_hypothesis_tests.txt",
        },
    }
    write_json(snapshots_dir(root) / "training_hypothesis_tests.json", artifact)
    write_text(snapshots_dir(root) / "training_hypothesis_tests.txt", _text_report(artifact))
    return artifact


def _text_report(artifact: dict) -> str:
    lines = [
        f"Training Hypothesis Tests - {artifact['date']}",
        "",
        f"Full range: {artifact['scope']['full_range']['start']} to {artifact['scope']['full_range']['end']}, "
        f"{artifact['scope']['full_range']['activity_rows']} activity rows, "
        f"{artifact['scope']['full_range']['association_samples']} association samples",
        f"Recent year: {artifact['scope']['recent_365']['start']} to {artifact['scope']['recent_365']['end']}, "
        f"{artifact['scope']['recent_365']['activity_rows']} activity rows, "
        f"{artifact['scope']['recent_365']['association_samples']} association samples",
        "",
    ]
    for item in artifact["hypotheses"]:
        lines.extend(
            [
                f"{item['id']}: {item['result']} ({item['confidence']})",
                f"- Hypothesis: {item['hypothesis']}",
                f"- Coaching implication: {item['coaching_implication']}",
                f"- Counterpoints: {'; '.join(item['counterpoints'])}",
                "",
            ]
        )
    lines.append("Caveats:")
    lines.extend(f"- {caveat}" for caveat in artifact["caveats"])
    return "\n".join(lines) + "\n"
