from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
from statistics import mean
from typing import Any

from .evidence import as_number
from .io import read_json, write_json
from .load_model import build_activity_summary_index
from .paths import snapshots_dir
from .time_utils import DEFAULT_TIMEZONE, iso_now, parse_date, today_local
from .wellness import build_wellness_daily


FEATURES = [
    "today_sleep_score_proxy",
    "today_sleep_hours",
    "today_body_battery_anchor",
    "today_resting_hr",
    "today_resting_hr_delta_14d",
    "today_avg_stress",
    "today_sleep_stress_proxy",
    "today_body_battery_drain",
    "today_weighted_intensity_min",
    "today_training_load",
    "today_duration_min",
    "today_high_intensity_min",
    "today_training_load_per_hour",
    "rolling_7d_training_load",
    "rolling_7d_duration_min",
    "rolling_7d_sessions",
    "acute_to_chronic_load_ratio",
    "consecutive_training_days",
    "mtb_sessions_today",
    "gym_sessions_today",
]

TARGET_DESCRIPTION = (
    "next-day response/readiness score derived from following-day Garmin wellness: "
    "sleep, Body Battery, HRV, resting HR versus baseline, and stress where available"
)
DEFAULT_MAX_ROWS = 730
DEFAULT_MAX_THRESHOLDS_PER_FEATURE = 32


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _round(value: float | None, digits: int = 2) -> float | None:
    return round(value, digits) if value is not None else None


def _avg(values: list[float | None]) -> float | None:
    usable = [value for value in values if value is not None]
    return mean(usable) if usable else None


def _number(value: Any, default: float = 0.0) -> float:
    parsed = as_number(value)
    return parsed if parsed is not None else default


def _readiness_level(score: float) -> str:
    if score < 45:
        return "red"
    if score < 70:
        return "yellow"
    return "green"


def _response_class(score: float) -> str:
    if score >= 75:
        return "strong"
    if score >= 65:
        return "steady"
    if score >= 50:
        return "strained"
    return "poor"


def _sleep_score_proxy(row: dict) -> float:
    sleep_score = as_number(row.get("sleep_score"))
    if sleep_score is not None:
        return sleep_score
    sleep_hours = as_number(row.get("sleep_hours"))
    if sleep_hours is None:
        return 65.0
    return round(_clamp(40 + (sleep_hours - 4.0) * 10.0, 35.0, 90.0), 1)


def _body_battery_anchor(row: dict) -> float:
    verified = as_number(row.get("body_battery_verified_morning_anchor"))
    wake = as_number(row.get("body_battery_wake"))
    if verified is not None and (wake is None or verified >= wake):
        return verified
    if wake is not None:
        return wake
    current = as_number(row.get("body_battery_current"))
    if current is not None:
        return current
    return 50.0


def _sleep_stress_proxy(row: dict) -> float | None:
    """Return observed sleep stress only; do not manufacture a proxy."""
    return as_number(row.get("sleep_stress"))


def _coverage_safe_avg_stress(row: dict) -> float | None:
    """Keep high observed stress, but require explicit coverage for low stress."""
    value = as_number(row.get("avg_stress"))
    if value is None:
        return None
    if value > 30:
        return value
    if row.get("all_day_stress_low_positive_reward_eligible") is True:
        return value
    return None


def _wearable_coverage_decision(
    root: str | Path | None,
    day: date,
) -> bool | None:
    """Load an exact-date wear decision, preferring the immutable dated artifact."""
    directory = snapshots_dir(root)
    dated_path = directory / f"wearable_coverage_{day.isoformat()}.json"
    artifact: Any = None
    try:
        if dated_path.exists():
            artifact = read_json(dated_path, {})
        else:
            alias = read_json(directory / "wearable_coverage.json", {})
            if isinstance(alias, dict) and alias.get("date") == day.isoformat():
                artifact = alias
    except (OSError, ValueError):
        return None
    if not isinstance(artifact, dict) or artifact.get("date") != day.isoformat():
        return None
    decision = (artifact.get("decision_use") or {}).get(
        "low_stress_positive_reward_eligible"
    )
    return decision if isinstance(decision, bool) else None


def _apply_wearable_coverage_guard(
    rows: list[dict],
    root: str | Path | None,
) -> list[dict]:
    """Join wear evidence downward-only without mutating normalized wellness."""
    guarded: list[dict] = []
    for source_row in rows:
        row = dict(source_row)
        try:
            row_date = parse_date(row.get("date"))
        except (TypeError, ValueError):
            row_date = None
        if row_date is not None:
            decision = _wearable_coverage_decision(root, row_date)
            raw = row.get("all_day_stress_low_positive_reward_eligible")
            if raw is True and decision is False:
                row["all_day_stress_low_positive_reward_eligible"] = False
        guarded.append(row)
    return guarded


def _wellness_dates(wellness_rows: list[dict]) -> dict[str, date]:
    out = {}
    for row in wellness_rows:
        try:
            parsed = parse_date(row.get("date"))
        except (TypeError, ValueError):
            parsed = None
        if parsed is not None and row.get("date"):
            out[str(row["date"])] = parsed
    return out


def _prior_wellness_rows(wellness_rows: list[dict], day: date, days: int) -> list[dict]:
    start = day - timedelta(days=days)
    out = []
    for row in wellness_rows:
        try:
            row_date = parse_date(row.get("date"))
        except (TypeError, ValueError):
            row_date = None
        if row_date and start <= row_date < day:
            out.append(row)
    return out


def _activity_empty() -> dict:
    return {
        "sessions": 0,
        "duration_min": 0.0,
        "training_load": 0.0,
        "distance_km": 0.0,
        "high_intensity_min": 0.0,
        "mtb_sessions": 0,
        "gym_sessions": 0,
        "categories": {},
    }


def _activity_by_date(activity_rows: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = defaultdict(_activity_empty)
    for row in activity_rows:
        day = row.get("date")
        if not day or not row.get("counts_for_training_load"):
            continue
        bucket = out[day]
        category = row.get("category") or "other"
        bucket["sessions"] += 1
        bucket["duration_min"] += _number(row.get("duration_min"))
        bucket["training_load"] += _number(row.get("training_load"))
        bucket["distance_km"] += _number(row.get("distance_km"))
        zones = row.get("hr_zone_min") or {}
        bucket["high_intensity_min"] += _number(zones.get("z4")) + _number(zones.get("z5"))
        bucket["mtb_sessions"] += int(category == "mtb")
        bucket["gym_sessions"] += int(category == "gym")
        bucket["categories"][category] = bucket["categories"].get(category, 0) + 1
    for bucket in out.values():
        bucket["duration_min"] = round(bucket["duration_min"], 1)
        bucket["training_load"] = round(bucket["training_load"], 1)
        bucket["distance_km"] = round(bucket["distance_km"], 1)
        bucket["high_intensity_min"] = round(bucket["high_intensity_min"], 1)
    return out


def _activity_window(activity_by_day: dict[str, dict], day: date, days: int) -> dict:
    totals = _activity_empty()
    for offset in range(days):
        row = activity_by_day.get((day - timedelta(days=offset)).isoformat(), _activity_empty())
        totals["sessions"] += row["sessions"]
        totals["duration_min"] += row["duration_min"]
        totals["training_load"] += row["training_load"]
        totals["distance_km"] += row["distance_km"]
        totals["high_intensity_min"] += row["high_intensity_min"]
        totals["mtb_sessions"] += row["mtb_sessions"]
        totals["gym_sessions"] += row["gym_sessions"]
    totals["duration_min"] = round(totals["duration_min"], 1)
    totals["training_load"] = round(totals["training_load"], 1)
    totals["distance_km"] = round(totals["distance_km"], 1)
    totals["high_intensity_min"] = round(totals["high_intensity_min"], 1)
    return totals


def _consecutive_training_days(activity_by_day: dict[str, dict], day: date) -> int:
    count = 0
    for offset in range(14):
        row = activity_by_day.get((day - timedelta(days=offset)).isoformat(), _activity_empty())
        if row["duration_min"] <= 0 and row["training_load"] <= 0:
            break
        count += 1
    return count


def _features_for_day(
    day: date,
    wellness_by_date: dict[str, dict],
    wellness_rows: list[dict],
    activity_by_day: dict[str, dict],
) -> dict[str, float] | None:
    row = wellness_by_date.get(day.isoformat())
    if row is None:
        return None
    activity_today = activity_by_day.get(day.isoformat(), _activity_empty())
    prior_14 = _prior_wellness_rows(wellness_rows, day, 14)
    resting_hr_baseline = _avg([as_number(item.get("resting_hr")) for item in prior_14])
    resting_hr = _number(row.get("resting_hr"), resting_hr_baseline or 0.0)
    resting_delta = resting_hr - resting_hr_baseline if resting_hr_baseline is not None else 0.0
    rolling_7 = _activity_window(activity_by_day, day, 7)
    rolling_28 = _activity_window(activity_by_day, day, 28)
    chronic_week = rolling_28["training_load"] / 4 if rolling_28["training_load"] > 0 else 0.0
    acute_chronic = rolling_7["training_load"] / chronic_week if chronic_week > 0 else 0.0
    duration = activity_today["duration_min"]
    load_per_hour = activity_today["training_load"] / (duration / 60) if duration > 0 else 0.0

    avg_stress = _coverage_safe_avg_stress(row)
    sleep_stress = _sleep_stress_proxy(row)
    if avg_stress is None or sleep_stress is None:
        return None

    features = {
        "today_sleep_score_proxy": _sleep_score_proxy(row),
        "today_sleep_hours": _number(row.get("sleep_hours"), 6.5),
        "today_body_battery_anchor": _body_battery_anchor(row),
        "today_resting_hr": resting_hr,
        "today_resting_hr_delta_14d": resting_delta,
        "today_avg_stress": avg_stress,
        "today_sleep_stress_proxy": sleep_stress,
        "today_body_battery_drain": _number(row.get("body_battery_drain"), 40.0),
        "today_weighted_intensity_min": _number(row.get("weighted_intensity_min")),
        "today_training_load": activity_today["training_load"],
        "today_duration_min": activity_today["duration_min"],
        "today_high_intensity_min": activity_today["high_intensity_min"],
        "today_training_load_per_hour": load_per_hour,
        "rolling_7d_training_load": rolling_7["training_load"],
        "rolling_7d_duration_min": rolling_7["duration_min"],
        "rolling_7d_sessions": float(rolling_7["sessions"]),
        "acute_to_chronic_load_ratio": acute_chronic,
        "consecutive_training_days": float(_consecutive_training_days(activity_by_day, day)),
        "mtb_sessions_today": float(activity_today["mtb_sessions"]),
        "gym_sessions_today": float(activity_today["gym_sessions"]),
    }
    return {feature: round(float(features[feature]), 3) for feature in FEATURES}


def _target_score(row: dict, wellness_rows: list[dict], target_day: date) -> dict:
    score = 70.0
    signals: list[str] = []
    audit_markers: list[str] = []
    missing: list[str] = []

    sleep_score = as_number(row.get("sleep_score"))
    sleep_hours = as_number(row.get("sleep_hours"))
    if sleep_score is not None:
        signals.append("sleep_score")
        if sleep_score < 60:
            score -= 18
        elif sleep_score < 70:
            score -= 8
        elif sleep_score >= 85:
            score += 6
        elif sleep_score >= 80:
            score += 4
    elif sleep_hours is not None:
        signals.append("sleep_hours_proxy")
        if sleep_hours < 5.5:
            score -= 18
        elif sleep_hours < 6.5:
            score -= 8
        elif sleep_hours >= 7.5:
            score += 4
    else:
        missing.append("sleep")

    body_battery_wake = as_number(row.get("body_battery_wake"))
    body_battery_current = as_number(row.get("body_battery_current"))
    if body_battery_wake is not None:
        signals.append("body_battery_wake")
        if body_battery_wake < 55:
            score -= 15
        elif body_battery_wake < 65:
            score -= 8
        elif body_battery_wake >= 75:
            score += 5
    elif body_battery_current is not None:
        signals.append("body_battery_current_proxy")
        if body_battery_current < 25:
            score -= 7
        elif body_battery_current < 40:
            score -= 4
        elif body_battery_current >= 70:
            score += 2
    else:
        missing.append("body_battery")

    hrv_status = str(row.get("hrv_status") or "").lower()
    overnight_hrv = as_number(row.get("overnight_hrv"))
    if hrv_status:
        signals.append("hrv_status")
        if any(term in hrv_status for term in ("low", "poor", "unbalanced")):
            score -= 10
        elif "balanced" in hrv_status:
            score += 3
    elif overnight_hrv is not None:
        prior_14 = _prior_wellness_rows(wellness_rows, target_day, 14)
        hrv_baseline = _avg([as_number(item.get("overnight_hrv")) for item in prior_14])
        if hrv_baseline is not None:
            signals.append("overnight_hrv_vs_14d")
            delta = overnight_hrv - hrv_baseline
            if delta <= -8:
                score -= 8
            elif delta >= 5:
                score += 3
        else:
            missing.append("hrv")
    else:
        missing.append("hrv")

    resting_hr = as_number(row.get("resting_hr"))
    prior_14 = _prior_wellness_rows(wellness_rows, target_day, 14)
    resting_baseline = _avg([as_number(item.get("resting_hr")) for item in prior_14])
    if resting_hr is not None and resting_baseline is not None:
        signals.append("resting_hr_vs_14d")
        delta = resting_hr - resting_baseline
        if delta >= 6:
            score -= 9
        elif delta >= 4:
            score -= 5
        elif delta <= -4:
            score += 3
    elif resting_hr is None:
        missing.append("resting_hr")

    avg_stress = as_number(row.get("avg_stress"))
    if avg_stress is not None:
        if avg_stress > 45:
            signals.append("avg_stress")
            score -= 12
        elif avg_stress > 35:
            signals.append("avg_stress")
            score -= 7
        elif avg_stress > 30:
            signals.append("avg_stress")
        elif row.get("all_day_stress_low_positive_reward_eligible") is True:
            signals.append("avg_stress")
            if avg_stress <= 20:
                score += 3
        else:
            audit_markers.append(
                "avg_stress_low_positive_withheld_for_partial_wear"
            )
    else:
        missing.append("avg_stress")

    sleep_stress = as_number(row.get("sleep_stress"))
    if sleep_stress is not None:
        signals.append("sleep_stress")
        if sleep_stress > 25:
            score -= 8
        elif sleep_stress > 18:
            score -= 4

    score = round(_clamp(score, 0.0, 100.0), 1)
    return {
        "next_day_response_score": score,
        "next_day_readiness_level": _readiness_level(score),
        "next_day_response_class": _response_class(score),
        "next_day_ready": score >= 70,
        "signal_count": len(signals),
        "signals": signals,
        "audit_markers": audit_markers,
        "missing_signals": missing,
        "usable": len(signals) >= 3,
    }


def build_training_response_dataset(
    root: str | Path | None = None,
    for_date: str | date | None = None,
    max_rows: int = DEFAULT_MAX_ROWS,
) -> dict:
    target = parse_date(for_date) or today_local(DEFAULT_TIMEZONE)
    wellness_rows = _apply_wearable_coverage_guard([
        row
        for row in build_wellness_daily(root)
        if parse_date(row.get("date")) and parse_date(row.get("date")) <= target
    ], root)
    wellness_by_date = {row["date"]: row for row in wellness_rows if row.get("date")}
    activity_by_day = _activity_by_date(build_activity_summary_index(root, target))
    available_dates = _wellness_dates(wellness_rows)
    rows = []
    skipped = Counter()
    candidate_days = 0

    for row in wellness_rows:
        row_date = available_dates.get(str(row.get("date")))
        if row_date is None:
            skipped["missing_row_date"] += 1
            continue
        target_day = row_date + timedelta(days=1)
        if target_day > target:
            skipped["future_target"] += 1
            continue
        next_row = wellness_by_date.get(target_day.isoformat())
        if next_row is None:
            skipped["missing_next_day_wellness"] += 1
            continue
        candidate_days += 1
        features = _features_for_day(row_date, wellness_by_date, wellness_rows, activity_by_day)
        if features is None:
            skipped["missing_feature_wellness"] += 1
            continue
        target_payload = _target_score(next_row, wellness_rows, target_day)
        if not target_payload["usable"]:
            skipped["insufficient_target_signals"] += 1
            continue
        activity_today = activity_by_day.get(row_date.isoformat(), _activity_empty())
        rows.append(
            {
                "date": row_date.isoformat(),
                "target_date": target_day.isoformat(),
                "features": features,
                "target": {
                    key: value
                    for key, value in target_payload.items()
                    if key != "usable"
                },
                "source_summary": {
                    "activity_today": {
                        "sessions": activity_today["sessions"],
                        "duration_min": activity_today["duration_min"],
                        "training_load": activity_today["training_load"],
                        "high_intensity_min": activity_today["high_intensity_min"],
                        "mtb_sessions": activity_today["mtb_sessions"],
                        "gym_sessions": activity_today["gym_sessions"],
                        "categories": activity_today["categories"],
                    },
                    "wellness_today_available": sorted(
                        key
                        for key in (
                            "sleep_score",
                            "sleep_hours",
                            "body_battery_wake",
                            "body_battery_current",
                            "overnight_hrv",
                            "hrv_status",
                            "resting_hr",
                            "avg_stress",
                            "sleep_stress",
                        )
                        if row.get(key) is not None
                    ),
                    "target_signals": target_payload["signals"],
                    "target_audit_markers": target_payload["audit_markers"],
                },
            }
        )

    if max_rows > 0:
        rows = rows[-max_rows:]
    artifact = {
        "date": target.isoformat(),
        "generated_at": iso_now(DEFAULT_TIMEZONE),
        "dataset_type": "next_day_training_response",
        "target": TARGET_DESCRIPTION,
        "features": FEATURES,
        "max_rows": max_rows,
        "candidate_days": candidate_days,
        "samples": len(rows),
        "date_span": {
            "start": rows[0]["date"] if rows else None,
            "end": rows[-1]["date"] if rows else None,
        },
        "skipped": dict(sorted(skipped.items())),
        "rows": rows,
    }
    write_json(snapshots_dir(root) / "training_response_dataset.json", artifact)
    return artifact


def _gini(rows: list[dict]) -> float:
    if not rows:
        return 0.0
    positives = sum(1 for row in rows if row["target"]["next_day_ready"])
    p = positives / len(rows)
    return 1 - p * p - (1 - p) * (1 - p)


def _leaf(rows: list[dict]) -> dict:
    positives = sum(1 for row in rows if row["target"]["next_day_ready"])
    probability = positives / len(rows) if rows else 0.0
    scores = [row["target"]["next_day_response_score"] for row in rows]
    avg_score = mean(scores) if scores else None
    levels = Counter(row["target"]["next_day_readiness_level"] for row in rows)
    classes = Counter(row["target"]["next_day_response_class"] for row in rows)
    return {
        "type": "leaf",
        "samples": len(rows),
        "prob_next_day_ready": round(probability, 3),
        "prediction_next_day_ready": probability >= 0.5,
        "avg_next_day_response_score": _round(avg_score, 1),
        "predicted_next_day_readiness_level": _readiness_level(avg_score)
        if avg_score is not None
        else None,
        "readiness_level_distribution": dict(sorted(levels.items())),
        "response_class_distribution": dict(sorted(classes.items())),
        "date_span": {
            "start": rows[0]["date"] if rows else None,
            "end": rows[-1]["date"] if rows else None,
        },
    }


def _candidate_thresholds(
    rows: list[dict],
    feature: str,
    max_thresholds: int = DEFAULT_MAX_THRESHOLDS_PER_FEATURE,
) -> list[float]:
    values = sorted({row["features"][feature] for row in rows})
    thresholds = [round((left + right) / 2, 4) for left, right in zip(values, values[1:])]
    if len(thresholds) <= max_thresholds:
        return thresholds
    selected = []
    for index in range(1, max_thresholds + 1):
        raw = round(index * (len(thresholds) - 1) / (max_thresholds + 1))
        selected.append(thresholds[int(raw)])
    return sorted(set(selected))


def _best_split(
    rows: list[dict],
    min_leaf: int,
    max_thresholds: int,
) -> tuple[str, float, float] | None:
    base = _gini(rows)
    best: tuple[str, float, float] | None = None
    for feature in FEATURES:
        for threshold in _candidate_thresholds(rows, feature, max_thresholds):
            left = [row for row in rows if row["features"][feature] <= threshold]
            right = [row for row in rows if row["features"][feature] > threshold]
            if len(left) < min_leaf or len(right) < min_leaf:
                continue
            weighted = (len(left) / len(rows)) * _gini(left) + (len(right) / len(rows)) * _gini(right)
            gain = base - weighted
            if best is None or gain > best[2]:
                best = (feature, threshold, gain)
    return best


def _train_tree(
    rows: list[dict],
    depth: int,
    max_depth: int,
    min_leaf: int,
    max_thresholds: int,
) -> dict:
    if depth >= max_depth or len(rows) < min_leaf * 2 or _gini(rows) == 0:
        return _leaf(rows)
    split = _best_split(rows, min_leaf, max_thresholds)
    if split is None or split[2] <= 0:
        return _leaf(rows)
    feature, threshold, gain = split
    left = [row for row in rows if row["features"][feature] <= threshold]
    right = [row for row in rows if row["features"][feature] > threshold]
    return {
        "type": "node",
        "feature": feature,
        "threshold": threshold,
        "gini_gain": round(gain, 4),
        "samples": len(rows),
        "left_rule": f"{feature} <= {threshold}",
        "right_rule": f"{feature} > {threshold}",
        "left": _train_tree(left, depth + 1, max_depth, min_leaf, max_thresholds),
        "right": _train_tree(right, depth + 1, max_depth, min_leaf, max_thresholds),
    }


def _predict(tree: dict, features: dict) -> dict:
    node = tree
    path = []
    while node.get("type") == "node":
        feature = node["feature"]
        threshold = node["threshold"]
        value = features[feature]
        go_left = value <= threshold
        path.append(
            {
                "feature": feature,
                "value": value,
                "threshold": threshold,
                "rule": node["left_rule"] if go_left else node["right_rule"],
            }
        )
        node = node["left"] if go_left else node["right"]
    return {"leaf": node, "path": path}


def _chronological_validation(
    rows: list[dict],
    max_depth: int,
    min_leaf: int,
    max_thresholds: int,
) -> dict:
    minimum = max(12, min_leaf * 4)
    if len(rows) < minimum:
        return {
            "method": "chronological_holdout",
            "status": "insufficient_samples",
            "samples": len(rows),
            "minimum_samples": minimum,
        }
    split_index = max(int(len(rows) * 0.75), min_leaf * 2)
    split_index = min(split_index, len(rows) - 1)
    train = rows[:split_index]
    test = rows[split_index:]
    tree = _train_tree(train, 0, max_depth, min_leaf, max_thresholds)
    train_positive_rate = sum(1 for row in train if row["target"]["next_day_ready"]) / len(train)
    baseline_prediction = train_positive_rate >= 0.5
    correct = 0
    baseline_correct = 0
    abs_errors = []
    confusion = {"tp": 0, "tn": 0, "fp": 0, "fn": 0}
    for row in test:
        prediction = _predict(tree, row["features"])["leaf"]
        predicted_ready = prediction["prediction_next_day_ready"]
        actual_ready = row["target"]["next_day_ready"]
        correct += int(predicted_ready == actual_ready)
        baseline_correct += int(baseline_prediction == actual_ready)
        predicted_score = prediction.get("avg_next_day_response_score")
        if predicted_score is not None:
            abs_errors.append(abs(predicted_score - row["target"]["next_day_response_score"]))
        if predicted_ready and actual_ready:
            confusion["tp"] += 1
        elif not predicted_ready and not actual_ready:
            confusion["tn"] += 1
        elif predicted_ready and not actual_ready:
            confusion["fp"] += 1
        else:
            confusion["fn"] += 1
    return {
        "method": "chronological_holdout",
        "status": "ok",
        "train_samples": len(train),
        "test_samples": len(test),
        "split_after_date": train[-1]["date"] if train else None,
        "accuracy": round(correct / len(test), 3) if test else None,
        "baseline_majority_accuracy": round(baseline_correct / len(test), 3) if test else None,
        "mean_absolute_score_error": _round(mean(abs_errors), 1) if abs_errors else None,
        "confusion": confusion,
    }


def _tree_splits(tree: dict) -> list[dict]:
    counter: dict[str, dict] = {}

    def visit(node: dict) -> None:
        if node.get("type") != "node":
            return
        feature = node["feature"]
        item = counter.setdefault(feature, {"feature": feature, "uses": 0, "total_gini_gain": 0.0})
        item["uses"] += 1
        item["total_gini_gain"] += node.get("gini_gain") or 0.0
        visit(node["left"])
        visit(node["right"])

    visit(tree)
    out = []
    for item in counter.values():
        item["total_gini_gain"] = round(item["total_gini_gain"], 4)
        out.append(item)
    return sorted(out, key=lambda item: (-item["total_gini_gain"], -item["uses"], item["feature"]))


def _prediction_for_date(
    tree: dict,
    root: str | Path | None,
    target: date,
    dataset_rows: list[dict],
) -> dict:
    wellness_rows = _apply_wearable_coverage_guard([
        row
        for row in build_wellness_daily(root)
        if parse_date(row.get("date")) and parse_date(row.get("date")) <= target
    ], root)
    wellness_by_date = {row["date"]: row for row in wellness_rows if row.get("date")}
    activity_by_day = _activity_by_date(build_activity_summary_index(root, target))
    candidate_dates = [
        parse_date(row.get("date"))
        for row in wellness_rows
        if row.get("date") and parse_date(row.get("date")) and parse_date(row.get("date")) <= target
    ]
    candidate_dates = [item for item in candidate_dates if item is not None]
    basis_date = max(candidate_dates) if candidate_dates else None
    warnings = []
    if basis_date is None:
        return {
            "date": target.isoformat(),
            "status": "unavailable",
            "warnings": ["No wellness row is available on or before the prediction date."],
        }
    if basis_date != target:
        warnings.append(
            f"Prediction is based on {basis_date.isoformat()} because no wellness row exists for {target.isoformat()}."
        )
    if not dataset_rows:
        warnings.append("Model has no historical training rows; prediction is a zero-sample leaf.")
    features = _features_for_day(basis_date, wellness_by_date, wellness_rows, activity_by_day)
    if features is None:
        return {
            "date": target.isoformat(),
            "status": "unavailable",
            "basis_date": basis_date.isoformat(),
            "warnings": ["Could not build feature vector for the selected basis date."],
        }
    prediction = _predict(tree, features)
    leaf = prediction["leaf"]
    predicted_score = leaf.get("avg_next_day_response_score")
    predicted_level = (
        _readiness_level(predicted_score)
        if predicted_score is not None
        else leaf.get("predicted_next_day_readiness_level")
    )
    return {
        "date": target.isoformat(),
        "status": "ok" if not warnings else "caution",
        "basis_date": basis_date.isoformat(),
        "predicts_date": (basis_date + timedelta(days=1)).isoformat(),
        "features": features,
        "prediction_path": prediction["path"],
        "prediction": {
            "prob_next_day_ready": leaf.get("prob_next_day_ready"),
            "prediction_next_day_ready": leaf.get("prediction_next_day_ready"),
            "expected_next_day_response_score": predicted_score,
            "predicted_next_day_readiness_level": predicted_level,
            "predicted_next_day_response_class": _response_class(predicted_score)
            if predicted_score is not None
            else None,
            "leaf_samples": leaf.get("samples"),
            "leaf_date_span": leaf.get("date_span"),
            "leaf_readiness_level_distribution": leaf.get("readiness_level_distribution"),
        },
        "warnings": warnings,
    }


def build_training_predictor(
    root: str | Path | None = None,
    for_date: str | date | None = None,
    max_depth: int = 3,
    min_leaf: int = 6,
    max_rows: int = DEFAULT_MAX_ROWS,
    max_thresholds: int = DEFAULT_MAX_THRESHOLDS_PER_FEATURE,
) -> dict:
    target = parse_date(for_date) or today_local(DEFAULT_TIMEZONE)
    dataset = build_training_response_dataset(root, target, max_rows=max_rows)
    rows = dataset["rows"]
    tree = _train_tree(rows, 0, max_depth, min_leaf, max_thresholds) if rows else _leaf([])
    positives = sum(1 for row in rows if row["target"]["next_day_ready"])
    validation = _chronological_validation(rows, max_depth, min_leaf, max_thresholds)
    if validation.get("status") == "ok":
        accuracy = validation.get("accuracy")
        baseline = validation.get("baseline_majority_accuracy")
        lift = (
            round(accuracy - baseline, 3)
            if accuracy is not None and baseline is not None
            else None
        )
        validation["accuracy_lift_vs_baseline"] = lift
        validation["utility"] = (
            "useful"
            if lift is not None and lift >= 0.05
            else "experimental_no_baseline_lift"
        )
    today_prediction = _prediction_for_date(tree, root, target, rows)
    caveats = [
        "This is a small personal model trained only on local Garmin-derived artifacts.",
        "Targets are derived from next-day wellness, not direct MTB performance or technical skill quality.",
        "A same-day prediction can change after the day is complete and Garmin sync catches later training or recovery data.",
        "The tree is intentionally shallow and bounded for inspectability; use it as one coaching signal, not an authority.",
    ]
    report = {
        "date": target.isoformat(),
        "generated_at": iso_now(DEFAULT_TIMEZONE),
        "model_type": "pure_python_bounded_decision_tree_classifier",
        "target": TARGET_DESCRIPTION,
        "samples": len(rows),
        "positive_samples": positives,
        "negative_samples": len(rows) - positives,
        "features": FEATURES,
        "bounds": {
            "max_depth": max_depth,
            "min_leaf": min_leaf,
            "max_rows": max_rows,
            "max_thresholds_per_feature": max_thresholds,
        },
        "validation": validation,
        "feature_usage": _tree_splits(tree),
        "dataset_summary": {
            "candidate_days": dataset["candidate_days"],
            "date_span": dataset["date_span"],
            "skipped": dataset["skipped"],
        },
        "caveats": caveats,
        "today_prediction": today_prediction,
        "tree": tree,
        "artifacts": {
            "dataset": "snapshots/training_response_dataset.json",
            "model_report": "snapshots/training_response_model_report.json",
            "today_prediction": "snapshots/training_prediction_today.json",
        },
    }
    write_json(snapshots_dir(root) / "training_response_model_report.json", report)
    write_json(snapshots_dir(root) / "training_prediction_today.json", today_prediction)
    return report
