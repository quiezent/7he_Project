from __future__ import annotations

from collections import defaultdict
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
    "sleep_score",
    "sleep_hours",
    "overnight_hrv",
    "hrv_vs_low_baseline",
    "resting_hr",
    "avg_stress",
    "sleep_stress",
    "prev_day_training_load",
    "prev_day_duration_min",
    "prev_day_weighted_intensity_min",
    "prev_day_body_battery_drain",
]


def _coverage_safe_avg_stress(row: dict) -> float | None:
    value = as_number(row.get("avg_stress"))
    if value is None:
        return None
    if value > 30 or row.get("all_day_stress_low_positive_reward_eligible") is True:
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


def _activity_by_date(activity_rows: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = defaultdict(lambda: {"training_load": 0.0, "duration_min": 0.0})
    for row in activity_rows:
        if not row.get("counts_for_training_load"):
            continue
        day = row.get("date")
        if not day:
            continue
        out[day]["training_load"] += row.get("training_load") or 0
        out[day]["duration_min"] += row.get("duration_min") or 0
    for value in out.values():
        value["training_load"] = round(value["training_load"], 1)
        value["duration_min"] = round(value["duration_min"], 1)
    return out


def build_body_battery_dataset(
    root: str | Path | None = None,
    for_date: str | date | None = None,
) -> list[dict]:
    target = parse_date(for_date) or today_local(DEFAULT_TIMEZONE)
    wellness_rows = _apply_wearable_coverage_guard([
        row
        for row in build_wellness_daily(root)
        if parse_date(row.get("date")) and parse_date(row.get("date")) <= target
    ], root)
    activity_by_day = _activity_by_date(build_activity_summary_index(root, target))
    by_date = {row["date"]: row for row in wellness_rows if row.get("date")}
    dataset = []
    for row in wellness_rows:
        row_date = parse_date(row.get("date"))
        if row_date is None:
            continue
        previous_date = (row_date - timedelta(days=1)).isoformat()
        previous_wellness = by_date.get(previous_date, {})
        previous_activity = activity_by_day.get(previous_date, {})
        target_value = as_number(row.get("body_battery_wake"))
        if target_value is None:
            continue
        features = {
            "sleep_score": as_number(row.get("sleep_score")),
            "sleep_hours": as_number(row.get("sleep_hours")),
            "overnight_hrv": as_number(row.get("overnight_hrv")),
            "hrv_vs_low_baseline": (
                as_number(row.get("overnight_hrv")) - as_number(row.get("hrv_balanced_low"))
                if as_number(row.get("overnight_hrv")) is not None
                and as_number(row.get("hrv_balanced_low")) is not None
                else None
            ),
            "resting_hr": as_number(row.get("resting_hr")),
            "avg_stress": _coverage_safe_avg_stress(row),
            "sleep_stress": as_number(row.get("sleep_stress")),
            "prev_day_training_load": as_number(previous_activity.get("training_load")) or 0.0,
            "prev_day_duration_min": as_number(previous_activity.get("duration_min")) or 0.0,
            "prev_day_weighted_intensity_min": as_number(
                previous_wellness.get("weighted_intensity_min")
            )
            or 0.0,
            "prev_day_body_battery_drain": as_number(previous_wellness.get("body_battery_drain"))
            or 0.0,
        }
        if all(features[feature] is not None for feature in FEATURES):
            dataset.append(
                {
                    "date": row["date"],
                    "target_body_battery_wake": target_value,
                    "target_good_wake_body_battery": target_value >= 70,
                    "features": features,
                }
            )
    write_json(snapshots_dir(root) / "body_battery_dataset.json", dataset)
    return dataset


def _gini(rows: list[dict]) -> float:
    if not rows:
        return 0.0
    positives = sum(1 for row in rows if row["target_good_wake_body_battery"])
    p = positives / len(rows)
    return 1 - p * p - (1 - p) * (1 - p)


def _leaf(rows: list[dict]) -> dict:
    positives = sum(1 for row in rows if row["target_good_wake_body_battery"])
    probability = positives / len(rows) if rows else 0.0
    avg_target = mean(row["target_body_battery_wake"] for row in rows) if rows else None
    return {
        "type": "leaf",
        "samples": len(rows),
        "prob_good_wake_body_battery": round(probability, 3),
        "prediction": probability >= 0.5,
        "avg_body_battery_wake": round(avg_target, 1) if avg_target is not None else None,
    }


def _candidate_thresholds(rows: list[dict], feature: str) -> list[float]:
    values = sorted({row["features"][feature] for row in rows})
    return [round((left + right) / 2, 4) for left, right in zip(values, values[1:])]


def _best_split(rows: list[dict], min_leaf: int) -> tuple[str, float, float] | None:
    base = _gini(rows)
    best: tuple[str, float, float] | None = None
    for feature in FEATURES:
        for threshold in _candidate_thresholds(rows, feature):
            left = [row for row in rows if row["features"][feature] <= threshold]
            right = [row for row in rows if row["features"][feature] > threshold]
            if len(left) < min_leaf or len(right) < min_leaf:
                continue
            weighted = (len(left) / len(rows)) * _gini(left) + (len(right) / len(rows)) * _gini(right)
            gain = base - weighted
            if best is None or gain > best[2]:
                best = (feature, threshold, gain)
    return best


def _train_tree(rows: list[dict], depth: int, max_depth: int, min_leaf: int) -> dict:
    if depth >= max_depth or len(rows) < min_leaf * 2 or _gini(rows) == 0:
        return _leaf(rows)
    split = _best_split(rows, min_leaf)
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
        "left": _train_tree(left, depth + 1, max_depth, min_leaf),
        "right": _train_tree(right, depth + 1, max_depth, min_leaf),
    }


def _predict(tree: dict, features: dict) -> dict:
    node = tree
    path = []
    while node.get("type") == "node":
        feature = node["feature"]
        threshold = node["threshold"]
        value = features[feature]
        go_left = value <= threshold
        path.append(node["left_rule"] if go_left else node["right_rule"])
        node = node["left"] if go_left else node["right"]
    return {"leaf": node, "path": path}


def _leave_one_out_accuracy(rows: list[dict], max_depth: int, min_leaf: int) -> float | None:
    if len(rows) < min_leaf * 3:
        return None
    correct = 0
    tested = 0
    for index, row in enumerate(rows):
        train = rows[:index] + rows[index + 1 :]
        tree = _train_tree(train, 0, max_depth, min_leaf)
        pred = _predict(tree, row["features"])["leaf"]["prediction"]
        correct += int(pred == row["target_good_wake_body_battery"])
        tested += 1
    return round(correct / tested, 3) if tested else None


def build_body_battery_model(
    root: str | Path | None = None,
    for_date: str | date | None = None,
    max_depth: int = 3,
    min_leaf: int = 4,
) -> dict:
    target = parse_date(for_date) or today_local(DEFAULT_TIMEZONE)
    rows = build_body_battery_dataset(root, target)
    tree = _train_tree(rows, 0, max_depth, min_leaf) if rows else _leaf([])
    latest = rows[-1] if rows else None
    latest_prediction = _predict(tree, latest["features"]) if latest else None
    positives = sum(1 for row in rows if row["target_good_wake_body_battery"])
    report = {
        "date": target.isoformat(),
        "generated_at": iso_now(DEFAULT_TIMEZONE),
        "model_type": "pure_python_decision_tree_classifier",
        "target": "body_battery_wake >= 70",
        "samples": len(rows),
        "positive_samples": positives,
        "negative_samples": len(rows) - positives,
        "features": FEATURES,
        "max_depth": max_depth,
        "min_leaf": min_leaf,
        "leave_one_out_accuracy": _leave_one_out_accuracy(rows, max_depth, min_leaf),
        "caveats": [
            "Small personal dataset; use as explainable signal, not authority.",
            "Garmin Body Battery is itself a modeled metric, so this is a model of a model.",
            "The tree explains patterns in this synced window and should be retrained after more outdoor/gym data.",
        ],
        "latest_row": latest,
        "latest_prediction": latest_prediction,
        "tree": tree,
    }
    write_json(snapshots_dir(root) / "body_battery_decision_tree.json", tree)
    write_json(snapshots_dir(root) / "body_battery_model_report.json", report)
    write_json(
        snapshots_dir(root) / "body_battery_today.json",
        {
            "date": target.isoformat(),
            "latest_row": latest,
            "latest_prediction": latest_prediction,
        },
    )
    return report
