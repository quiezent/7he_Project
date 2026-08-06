from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any

from .io import read_json, write_json
from .paths import activities_dir, snapshots_dir


DEFAULT_GRID_M = 1.0
DEFAULT_SPEED_EPS = 0.5
DEFAULT_DIST_EPS = 1.0
DEFAULT_METRIC_COLUMNS = ("directSpeed",)
DEFAULT_BUCKET_M = 100.0
_SVG_LINE_COLORS = ("#1565c0", "#f57c00", "#2e7d32", "#c62828", "#6d4c41")


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_gmt_millis(value: Any) -> float:
    text = str(value)
    text = text.replace("Z", "+00:00")
    if "." not in text:
        text = f"{text}.000"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp() * 1000.0


def _metric_indexes(descriptors: list[dict[str, Any]]) -> dict[str, int]:
    return {
        str(row.get("key")): int(row.get("metricsIndex"))
        for row in descriptors
        if isinstance(row.get("metricsIndex"), int) and row.get("key")
    }


def _value_at(row_metrics: list[Any], indexes: dict[str, int], key: str) -> float | None:
    index = indexes.get(key)
    if index is None or index < 0 or index >= len(row_metrics):
        return None
    return _as_float(row_metrics[index])


def _svg_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\"", "&quot;")
        .replace("'", "&apos;")
    )


def _find_lap_row(splits: list[dict[str, Any]], lap_number: int) -> dict[str, Any] | None:
    for lap in splits:
        try:
            if int(lap.get("lapIndex")) == lap_number:
                return lap
        except (TypeError, ValueError):
            continue
    return None


def _interpolate(points: list[tuple[float, float]], target: float) -> float | None:
    if not points:
        return None
    if target < points[0][0] or target > points[-1][0]:
        return None
    if target == points[0][0]:
        return points[0][1]
    if target == points[-1][0]:
        return points[-1][1]

    for index in range(1, len(points)):
        left_d, left_v = points[index - 1]
        right_d, right_v = points[index]
        if target < left_d:
            continue
        if target > right_d:
            continue
        if right_d == left_d:
            return (left_v + right_v) / 2.0
        ratio = (target - left_d) / (right_d - left_d)
        return left_v + ratio * (right_v - left_v)
    return None


def _compress_series(distance_m: list[float | None], values: list[float | None]) -> list[tuple[float, float]]:
    pairs = []
    for distance, value in zip(distance_m, values):
        if distance is None or value is None:
            continue
        pairs.append((float(distance), float(value)))
    if not pairs:
        return []
    pairs.sort(key=lambda item: item[0])

    out: list[tuple[float, float]] = []
    for distance, value in pairs:
        if out and distance <= out[-1][0]:
            if abs(distance - out[-1][0]) < 1e-9:
                out[-1] = (distance, value)
            continue
        out.append((distance, value))
    return out


def _metric_path(series: list[tuple[float, float]], x_min: float, x_max: float, y_min: float, y_max: float, width: int, height: int,
                x0: float, y0: float) -> str:
    if not series:
        return ""
    def map_x(distance: float) -> float:
        return x0 + (distance - x_min) / max(1.0, x_max - x_min) * width

    def map_y(value: float) -> float:
        return y0 + (y_max - value) / max(1e-9, y_max - y_min) * height

    points = [
        f"{map_x(distance):.2f},{map_y(value):.2f}" for distance, value in series
    ]
    return " ".join(points)


def _axis_ticks(start: float, end: float, count: int) -> list[float]:
    if start == end:
        if start == 0.0:
            return [0.0]
        return [start]
    interval = (end - start) / max(1, count)
    return [start + interval * i for i in range(count + 1)]


def _build_lap_compare_svg(report: dict[str, Any], output_path: str | Path) -> str:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    series = report.get("series") or []
    if not series:
        write_json(output.with_suffix(".json"), {"status": "missing_series_for_chart"})
        return str(output)

    points = [row for row in series if row.get("lap_a_speed_mps") is not None and row.get("lap_b_speed_mps") is not None]
    if not points:
        return str(output)

    distances = [float(row["distance_m"]) for row in points if row.get("distance_m") is not None]
    if not distances:
        return str(output)
    max_distance = max(distances)
    if max_distance <= 0:
        return str(output)

    a_speed = [float(row["lap_a_speed_mps"]) * 3.6 for row in points]
    b_speed = [float(row["lap_b_speed_mps"]) * 3.6 for row in points]
    deltas = [float(row["delta_mps"]) * 3.6 for row in points if row.get("delta_mps") is not None]

    max_speed = max(a_speed + b_speed)
    min_speed = 0.0
    if max_speed <= 0:
        max_speed = 1.0
    speed_pad = max(1.0, max_speed * 0.08)
    speed_min = min(0.0, min_speed)
    speed_max = max_speed + speed_pad
    speed_range = max_speed if max_speed else 1.0

    delta_abs = max(abs(min(deltas)), abs(max(deltas))) if deltas else 1.0
    delta_abs = max(0.1, delta_abs)
    delta_max = delta_abs * 1.2
    delta_min = -delta_abs * 1.2
    delta_range = delta_max - delta_min

    width = 1400
    height = 900
    margin_left = 70
    margin_right = 40
    margin_top = 40
    margin_bottom = 50
    panel_gap = 90
    panel_height = 280
    speed_top = margin_top + 40
    speed_bottom = speed_top + panel_height
    delta_top = speed_bottom + panel_gap
    delta_bottom = delta_top + panel_height
    inner_width = width - margin_left - margin_right
    speed_height = speed_bottom - speed_top
    delta_height = delta_bottom - delta_top

    def map_x(distance: float) -> float:
        return margin_left + distance / max_distance * inner_width

    def map_speed(y_value: float) -> float:
        return speed_bottom - ((y_value - speed_min) / max(1e-9, speed_max - speed_min)) * speed_height

    def map_delta(y_value: float) -> float:
        return delta_bottom - ((y_value - delta_min) / max(1e-9, delta_range)) * delta_height

    chart: list[str] = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1400 900" '
        'width="1400" height="900" role="img" aria-labelledby="title desc">',
        f'<title id="title">{_svg_escape("Lap " + str(report["lap_number"]) + " distance-sync comparison")}</title>',
        f'<desc id="desc">{_svg_escape("Lap A vs Lap B speed and delta by distance with movement anchoring.")}</desc>',
        '<style>text { font-family: Inter, system-ui, -apple-system, sans-serif; font-size: 12px; } '
        ' .axis{stroke:#444;stroke-width:1;} .grid{stroke:#ddd;stroke-width:1;stroke-dasharray:4 4;}' 
        ' .legend{font-size:12px;} .sub{fill:#555;font-size:11px;}</style>',
    ]

    # background
    chart.extend(
        [
            '<rect x="0" y="0" width="1400" height="900" fill="#fafafa"/>',
            f'<text x="{width // 2}" y="28" text-anchor="middle" font-size="20" font-weight="700">'
            f'{_svg_escape("Lap " + str(report["lap_number"]) + " Distance Sync Comparison")}</text>',
            f'<text x="{width // 2}" y="52" text-anchor="middle" class="sub">'
            f'{_svg_escape("Activities " + str(report["activity_a"]) + " vs " + str(report["activity_b"]))}</text>',
        ]
    )

    # speed panel axes/grid
    chart.append(f'<line x1="{margin_left}" y1="{speed_top}" x2="{margin_left}" y2="{speed_bottom}" class="axis"/>')
    chart.append(f'<line x1="{margin_left}" y1="{speed_bottom}" x2="{width - margin_right}" y2="{speed_bottom}" class="axis"/>')
    for tick in _axis_ticks(speed_min, speed_max, 4):
        y = map_speed(tick)
        chart.append(f'<line x1="{margin_left}" y1="{y:.2f}" x2="{width - margin_right}" y2="{y:.2f}" class="grid"/>')
        chart.append(f'<text x="{margin_left-6}" y="{y+4:.2f}" text-anchor="end">{tick:.1f}</text>')

    # delta zero baseline and axis/grid
    chart.append(f'<line x1="{margin_left}" y1="{delta_top}" x2="{margin_left}" y2="{delta_bottom}" class="axis"/>')
    chart.append(f'<line x1="{margin_left}" y1="{delta_bottom}" x2="{width - margin_right}" y2="{delta_bottom}" class="axis"/>')
    zero_y = map_delta(0.0)
    chart.append(f'<line x1="{margin_left}" y1="{zero_y:.2f}" x2="{width - margin_right}" y2="{zero_y:.2f}" '
                 'stroke="#444" stroke-width="1.2"/>')
    for tick in _axis_ticks(delta_min, delta_max, 4):
        y = map_delta(tick)
        chart.append(f'<line x1="{margin_left}" y1="{y:.2f}" x2="{width - margin_right}" y2="{y:.2f}" class="grid"/>')
        chart.append(f'<text x="{margin_left-6}" y="{y+4:.2f}" text-anchor="end" class="sub">{tick:.1f}</text>')

    # x axis labels (shared)
    for tick in _axis_ticks(0.0, max_distance, 10):
        x = map_x(tick)
        chart.append(f'<line x1="{x:.2f}" y1="{speed_bottom}" x2="{x:.2f}" y2="{speed_bottom + 4}" class="axis" />')
        chart.append(f'<line x1="{x:.2f}" y1="{delta_top}" x2="{x:.2f}" y2="{delta_bottom + 4}" class="axis" />')
        chart.append(f'<text x="{x:.2f}" y="{speed_bottom + 16}" text-anchor="middle" class="sub">{tick:.0f}</text>')
        chart.append(f'<text x="{x:.2f}" y="{delta_bottom + 20}" text-anchor="middle" class="sub">{tick:.0f}</text>')

    # labels
    chart.extend(
        [
            f'<text x="{margin_left}" y="{speed_top - 12}" class="legend" fill="#111" font-weight="600">Speed (km/h)</text>',
            f'<text x="{margin_left}" y="{delta_top - 12}" class="legend" fill="#111" font-weight="600">Delta speed (Lap A - Lap B, km/h)</text>',
            f'<text x="{width - margin_right}" y="{delta_bottom + 36}" text-anchor="end" class="sub">Distance (m)</text>',
        ]
    )

    speed_a_line = [
        (float(row["distance_m"]), float(row.get("lap_a_speed_mps", 0.0) or 0.0) * 3.6)
        for row in series
        if row.get("distance_m") is not None and row.get("lap_a_speed_mps") is not None
    ]
    speed_b_line = [
        (float(row["distance_m"]), float(row.get("lap_b_speed_mps", 0.0) or 0.0) * 3.6)
        for row in series
        if row.get("distance_m") is not None and row.get("lap_b_speed_mps") is not None
    ]
    chart.append(f'<polyline points="{_metric_path(speed_a_line, 0.0, max_distance, speed_min, speed_max, inner_width, speed_height, margin_left, speed_top)}" '
                 f'stroke="{_SVG_LINE_COLORS[0]}" stroke-width="2" fill="none" />')
    chart.append(f'<polyline points="{_metric_path(speed_b_line, 0.0, max_distance, speed_min, speed_max, inner_width, speed_height, margin_left, speed_top)}" '
                 f'stroke="{_SVG_LINE_COLORS[1]}" stroke-width="2" fill="none" />')

    for i in range(1, len(points)):
        row_prev = points[i - 1]
        row = points[i]
        prev_d = row_prev.get("distance_m")
        this_d = row.get("distance_m")
        prev_delta = row_prev.get("delta_mps")
        this_delta = row.get("delta_mps")
        if prev_d is None or this_d is None or prev_delta is None or this_delta is None:
            continue
        x1 = map_x(float(prev_d))
        x2 = map_x(float(this_d))
        y1 = map_delta(float(prev_delta) * 3.6)
        y2 = map_delta(float(this_delta) * 3.6)
        color = _SVG_LINE_COLORS[2] if float(this_delta) >= 0 else _SVG_LINE_COLORS[3]
        chart.append(
            f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" stroke="{color}" stroke-width="2" />'
        )

    faster_pct = report.get("metrics", {}).get("faster_share", {}).get("lap_a_faster_pct")
    chart.append(
        f'<rect x="{width - margin_right - 310}" y="{speed_top + 8}" width="300" height="76" fill="#fff" stroke="#ddd"/>'
    )
    chart.append(
        f'<text x="{width - margin_right - 290}" y="{speed_top + 28}" class="legend" fill="{_SVG_LINE_COLORS[0]}">Lap A</text>'
    )
    chart.append(
        f'<line x1="{width - margin_right - 300}" y1="{speed_top + 32}" x2="{width - margin_right - 278}" y2="{speed_top + 32}" '
        f'stroke="{_SVG_LINE_COLORS[0]}" stroke-width="2"/>'
    )
    chart.append(f'<text x="{width - margin_right - 270}" y="{speed_top + 28}" class="sub">= speed A</text>')
    chart.append(
        f'<text x="{width - margin_right - 290}" y="{speed_top + 46}" class="legend" fill="{_SVG_LINE_COLORS[1]}">Lap B</text>'
    )
    chart.append(
        f'<line x1="{width - margin_right - 300}" y1="{speed_top + 50}" x2="{width - margin_right - 278}" y2="{speed_top + 50}" '
        f'stroke="{_SVG_LINE_COLORS[1]}" stroke-width="2"/>'
    )
    chart.append(f'<text x="{width - margin_right - 270}" y="{speed_top + 46}" class="sub">= speed B</text>')
    chart.append(
        f'<text x="{width - margin_right - 290}" y="{speed_top + 64}" class="legend" fill="{_SVG_LINE_COLORS[2]}">Δ > 0</text>'
    )
    chart.append(
        f'<line x1="{width - margin_right - 300}" y1="{speed_top + 68}" x2="{width - margin_right - 278}" y2="{speed_top + 68}" '
        f'stroke="{_SVG_LINE_COLORS[2]}" stroke-width="2"/>'
    )
    chart.append(f'<text x="{width - margin_right - 270}" y="{speed_top + 64}" class="sub">= Lap A faster</text>')
    if faster_pct is None:
        faster_text = "moving points share unavailable"
    else:
        faster_text = f"Lap A faster on {faster_pct:.1f}% of moving points"
    chart.append(f'<text x="{width - margin_right - 290}" y="{speed_top + 80}" class="sub">{_svg_escape(faster_text)}</text>')

    chart.append(f'<text x="{margin_left + 4}" y="{speed_bottom + 36}" class="sub">Distance (m)</text>')
    chart.append(f'<text x="{margin_left - 55}" y="{speed_top + speed_height / 2:.1f}" transform="rotate(-90 20 {speed_top + speed_height / 2:.1f})" class="sub">km/h</text>')
    chart.append(f'<text x="{margin_left - 55}" y="{delta_top + delta_height / 2:.1f}" transform="rotate(-90 20 {delta_top + delta_height / 2:.1f})" class="sub">km/h</text>')

    chart.append("</svg>")
    output.write_text("\n".join(chart), encoding="utf-8")
    return str(output)

def find_movement_anchor(
    times: list[float],
    dist: list[float | None],
    speed: list[float | None],
    speed_eps: float = DEFAULT_SPEED_EPS,
    dist_eps: float = DEFAULT_DIST_EPS,
) -> int:
    if len(times) < 2:
        return 0
    if speed and speed[0] is not None and speed[0] >= speed_eps:
        return 0
    if dist and dist[0] is not None and dist[0] >= dist_eps:
        return 0
    for index in range(1, len(times)):
        current_dist = dist[index]
        previous_dist = dist[index - 1]
        current_speed = speed[index]
        previous_speed = speed[index - 1]
        if current_dist is not None and previous_dist is not None:
            if (current_dist - previous_dist) >= dist_eps:
                return index
        if (
            current_speed is not None
            and previous_speed is not None
            and current_speed >= speed_eps
            and previous_speed >= speed_eps
        ):
            return index
    return 0


def _derive_speed(time_s: list[float], distance_m: list[float | None]) -> list[float | None]:
    out: list[float | None] = [None]
    for index in range(1, len(distance_m)):
        current = distance_m[index]
        previous = distance_m[index - 1]
        dt = time_s[index] - time_s[index - 1]
        if current is None or previous is None or dt <= 0:
            out.append(None)
            continue
        out.append((current - previous) / dt)
    return out


def _percentile(values: list[float], percent: float) -> float | None:
    if not values:
        return None
    sorted_values = sorted(values)
    if len(sorted_values) == 1:
        return sorted_values[0]
    target = percent / 100.0 * (len(sorted_values) - 1)
    left = int(target)
    right = min(left + 1, len(sorted_values) - 1)
    if left == right:
        return sorted_values[left]
    ratio = target - left
    return sorted_values[left] * (1.0 - ratio) + sorted_values[right] * ratio


def resample_on_distance(
    dist: list[float],
    metric_map: dict[str, list[float | None]],
    times_s: list[float],
    grid_m: float = DEFAULT_GRID_M,
    *,
    max_distance: float | None = None,
) -> list[dict[str, float | None]]:
    if not dist or not times_s or len(dist) != len(times_s):
        return []
    if grid_m <= 0:
        raise ValueError("grid_m must be greater than zero.")

    max_sample = max(dist)
    if max_distance is None:
        max_distance = max_sample
    else:
        max_distance = min(max_sample, max_distance)
    if max_distance < 0:
        return []

    distance_series = _compress_series(dist, times_s)
    metric_series = {key: _compress_series(dist, values) for key, values in metric_map.items()}

    points = [0.0]
    if max_distance > 0:
        steps = int(max_distance // grid_m)
        for step in range(1, steps + 1):
            points.append(round(step * grid_m, 6))
        if abs(points[-1] - max_distance) > 1e-6:
            points.append(round(max_distance, 6))
    rows: list[dict[str, float | None]] = []
    for distance_m in points:
        elapsed_s = _interpolate(distance_series, distance_m)
        if elapsed_s is None:
            continue
        row: dict[str, float | None] = {"distance_m": distance_m, "elapsed_s": elapsed_s}
        for key, points_for_key in metric_series.items():
            row[key] = _interpolate(points_for_key, distance_m)
        rows.append(row)
    return rows


def compute_segment_stats(
    left_samples: list[dict[str, float | None]],
    right_samples: list[dict[str, float | None]],
    *,
    metric_col_left: str = "directSpeed",
    metric_col_right: str = "directSpeed",
    speed_eps: float = DEFAULT_SPEED_EPS,
    bucket_m: float = DEFAULT_BUCKET_M,
    top_windows: int = 10,
) -> dict[str, Any]:
    if len(left_samples) != len(right_samples):
        raise ValueError("Aligned sample lists must have the same length.")

    deltas: list[float] = []
    per_distance_rows: list[dict[str, float]] = []
    buckets: dict[int, list[tuple[float, float]]] = {}
    for left, right in zip(left_samples, right_samples):
        left_speed = left.get(metric_col_left)
        right_speed = right.get(metric_col_right)
        distance = left.get("distance_m")
        if distance is None or left_speed is None or right_speed is None:
            continue
        delta = left_speed - right_speed
        deltas.append(delta)
        per_distance_rows.append(
            {
                "distance_m": distance,
                "left_speed_mps": left_speed,
                "right_speed_mps": right_speed,
                "delta_mps": delta,
            }
        )
        bucket = int(distance // bucket_m)
        buckets.setdefault(bucket, []).append((left_speed, right_speed))

    if not per_distance_rows:
        return {
            "samples": 0,
            "delta_mps": {
                "mean": None,
                "median": None,
                "p5": None,
                "p25": None,
                "p75": None,
                "p95": None,
            },
            "faster_share": {
                "lap_a_faster_pct": None,
                "lap_b_faster_pct": None,
                "moving_points": 0,
                "stationary_points_excluded": 0,
            },
            "windows": [],
            "chunks": [],
        }

    moving_rows = [
        row for row in per_distance_rows if row["left_speed_mps"] >= speed_eps and row["right_speed_mps"] >= speed_eps
    ]
    moving_points = len(moving_rows)
    lap_a_faster = sum(1 for row in moving_rows if row["delta_mps"] > 0)
    lap_b_faster = sum(1 for row in moving_rows if row["delta_mps"] < 0)

    windows: list[dict[str, Any]] = []
    window_start = 0
    while window_start < len(per_distance_rows):
        sign = 0
        if per_distance_rows[window_start]["delta_mps"] > 0:
            sign = 1
        elif per_distance_rows[window_start]["delta_mps"] < 0:
            sign = -1
        else:
            window_start += 1
            continue

        window_end = window_start + 1
        while window_end < len(per_distance_rows):
            next_delta = per_distance_rows[window_end]["delta_mps"]
            if next_delta == 0:
                break
            next_sign = 1 if next_delta > 0 else -1
            if next_sign != sign:
                break
            window_end += 1

        segment = per_distance_rows[window_start:window_end]
        mean_delta = sum(item["delta_mps"] for item in segment) / len(segment)
        windows.append(
            {
                "start_m": segment[0]["distance_m"],
                "end_m": segment[-1]["distance_m"],
                "winner": "lap_a" if mean_delta > 0 else "lap_b",
                "mean_delta_mps": round(mean_delta, 6),
                "abs_mean_delta_mps": round(abs(mean_delta), 6),
            }
        )
        window_start = window_end

    windows.sort(key=lambda row: row["abs_mean_delta_mps"], reverse=True)

    chunk_rows: list[dict[str, Any]] = []
    for bucket in sorted(buckets):
        bucket_rows = buckets[bucket]
        start_m = bucket * bucket_m
        end_m = start_m + bucket_m
        left_mean = sum(left for left, _ in bucket_rows) / len(bucket_rows)
        right_mean = sum(right for _, right in bucket_rows) / len(bucket_rows)
        chunk_rows.append(
            {
                "start_m": start_m,
                "end_m": end_m,
                "left_mean_speed_mps": round(left_mean, 6),
                "right_mean_speed_mps": round(right_mean, 6),
                "delta_mps": round(left_mean - right_mean, 6),
            }
        )

    return {
        "samples": len(per_distance_rows),
        "delta_mps": {
            "mean": round(sum(deltas) / len(deltas), 6),
            "median": round(median(deltas), 6),
            "p5": _percentile(deltas, 5),
            "p25": _percentile(deltas, 25),
            "p75": _percentile(deltas, 75),
            "p95": _percentile(deltas, 95),
        },
        "faster_share": {
            "lap_a_faster_pct": round(lap_a_faster / moving_points * 100, 2) if moving_points else None,
            "lap_b_faster_pct": round(lap_b_faster / moving_points * 100, 2) if moving_points else None,
            "moving_points": moving_points,
            "stationary_points_excluded": len(per_distance_rows) - moving_points,
        },
        "windows": windows[:top_windows],
        "chunks": chunk_rows,
    }


def _read_detail_artifact(root: str | Path | None, activity_id: str) -> dict[str, Any] | None:
    for path in (
        activities_dir(root) / "details" / f"garmin_{activity_id}_detail.json",
        snapshots_dir(root) / f"activity_detail_{activity_id}.json",
    ):
        payload = read_json(path, None)
        if isinstance(payload, dict):
            return payload
    return None


def _extract_lap(payload: dict[str, Any], lap_number: int) -> dict[str, Any]:
    calls = payload.get("calls") or {}
    splits = ((calls.get("splits") or {}).get("data") or {}).get("lapDTOs")
    if not isinstance(splits, list):
        raise ValueError("No lapDTOs found in activity detail artifact.")
    lap = _find_lap_row(splits, lap_number)
    if not lap:
        raise ValueError(f"Lap {lap_number} not found in lapDTOs.")

    details = ((calls.get("details") or {}).get("data") or {})
    descriptors = _metric_indexes(details.get("metricDescriptors") or [])
    metrics_rows = details.get("activityDetailMetrics") or []
    if not isinstance(metrics_rows, list):
        raise ValueError("No activityDetailMetrics found in activity detail artifact.")

    lap_start = _parse_gmt_millis(lap.get("startTimeGMT"))
    duration_seconds = _as_float(lap.get("elapsedDuration")) or _as_float(lap.get("duration")) or 0.0
    lap_end = lap_start + (duration_seconds * 1000.0)

    possible_distance_keys = ("sumDistance", "directDistance")
    distance_key = next((key for key in possible_distance_keys if key in descriptors), None)
    if distance_key is None:
        raise ValueError("No distance metric descriptor (sumDistance/directDistance) found.")
    if "directTimestamp" not in descriptors:
        raise ValueError("No directTimestamp metric descriptor found.")

    candidate_rows: list[dict[str, Any]] = []
    parsed_rows: list[dict[str, Any]] = []
    for row in metrics_rows:
        metrics = row.get("metrics") or []
        timestamp = _value_at(metrics, descriptors, "directTimestamp")
        distance = _value_at(metrics, descriptors, distance_key)
        if timestamp is None or distance is None:
            continue
        parsed_row = {
            "time_ms": timestamp,
            "distance_m": distance,
            "directSpeed": _value_at(metrics, descriptors, "directSpeed"),
            "directLatitude": _value_at(metrics, descriptors, "directLatitude"),
            "directLongitude": _value_at(metrics, descriptors, "directLongitude"),
            "directHeartRate": _value_at(metrics, descriptors, "directHeartRate"),
        }
        candidate_rows.append(parsed_row)
        if lap_start <= timestamp <= lap_end:
            parsed_rows.append(parsed_row)

    if len(parsed_rows) < 2 and candidate_rows:
        candidate_rows.sort(key=lambda item: item["time_ms"])
        # Some cached Garmin detail artifacts expose directTimestamp as elapsed
        # milliseconds rather than absolute UTC milliseconds. In that case the
        # absolute lap window cannot match, but single-lap detail artifacts are
        # still usable by anchoring the stream to its first sample.
        relative_start = candidate_rows[0]["time_ms"]
        relative_end = relative_start + (duration_seconds * 1000.0)
        parsed_rows = [
            item
            for item in candidate_rows
            if relative_start <= item["time_ms"] <= relative_end
        ]

    if len(parsed_rows) < 2:
        raise ValueError(f"Lap {lap_number} has insufficient stream samples.")

    parsed_rows.sort(key=lambda item: item["time_ms"])
    time_ms = [item["time_ms"] for item in parsed_rows]
    distance_m = [item["distance_m"] for item in parsed_rows]
    speed_mps = [item["directSpeed"] for item in parsed_rows]
    if all(speed is None for speed in speed_mps):
        speed_mps = _derive_speed([(value - time_ms[0]) / 1000.0 for value in time_ms], distance_m)

    metric_map: dict[str, list[float | None]] = {"directSpeed": speed_mps}
    if all(item["directLatitude"] is not None for item in parsed_rows):
        metric_map["directLatitude"] = [item["directLatitude"] for item in parsed_rows]
    if all(item["directLongitude"] is not None for item in parsed_rows):
        metric_map["directLongitude"] = [item["directLongitude"] for item in parsed_rows]
    if any(item["directHeartRate"] is not None for item in parsed_rows):
        metric_map["directHeartRate"] = [item["directHeartRate"] for item in parsed_rows]

    return {
        "lap": int(lap_number),
        "raw_distance_m": (_as_float(lap.get("distance")) or distance_m[-1] - distance_m[0]),
        "duration_s": duration_seconds,
        "time_ms": time_ms,
        "distance_m": distance_m,
        "metric_map": metric_map,
    }


def _align_to_motion(
    lap_data: dict[str, Any],
    speed_eps: float,
    dist_eps: float,
) -> dict[str, Any]:
    anchor = find_movement_anchor(
        times=lap_data["time_ms"],
        dist=lap_data["distance_m"],
        speed=lap_data["metric_map"]["directSpeed"],
        speed_eps=speed_eps,
        dist_eps=dist_eps,
    )
    if anchor >= len(lap_data["time_ms"]):
        raise ValueError("Movement anchor is outside lap stream.")

    anchor_time = lap_data["time_ms"][anchor]
    anchor_distance = lap_data["distance_m"][anchor]
    synthetic_anchor = False
    if anchor == 0 and len(lap_data["time_ms"]) > 1:
        dt = lap_data["time_ms"][1] - lap_data["time_ms"][0]
        first_distance = lap_data["distance_m"][0]
        next_distance = lap_data["distance_m"][1]
        if dt > 0 and first_distance is not None and next_distance is not None:
            step_distance = max(0.0, next_distance - first_distance)
            if step_distance > 0:
                anchor_time = lap_data["time_ms"][0] - dt
                anchor_distance = max(0.0, first_distance - step_distance)
                synthetic_anchor = True
    elif anchor > 0:
        current_dist = lap_data["distance_m"][anchor]
        previous_dist = lap_data["distance_m"][anchor - 1]
        if current_dist is not None and previous_dist is not None:
            if current_dist - previous_dist >= dist_eps:
                anchor_time = lap_data["time_ms"][anchor - 1]
                anchor_distance = previous_dist
                synthetic_anchor = True

    anchored = {
        "lap": lap_data["lap"],
        "raw_distance_m": lap_data["raw_distance_m"],
        "duration_s": lap_data["duration_s"],
        "anchor": anchor,
        "anchor_time_ms": anchor_time,
        "anchor_distance_m": anchor_distance,
        "time_ms": [value - anchor_time for value in lap_data["time_ms"][anchor:]],
        "distance_m": [value - anchor_distance for value in lap_data["distance_m"][anchor:]],
        "metric_map": {key: values[anchor:] for key, values in lap_data["metric_map"].items()},
    }
    if synthetic_anchor:
        anchored["time_ms"].insert(0, 0.0)
        anchored["distance_m"].insert(0, 0.0)
        for key, values in anchored["metric_map"].items():
            values.insert(0, values[0] if values else None)
    if not anchored["time_ms"] or not anchored["distance_m"]:
        raise ValueError("No samples remain after motion-anchor trim.")
    if anchored["distance_m"][0] is not None:
        anchored["distance_m"][0] = 0.0
    anchored["time_s"] = [value / 1000.0 for value in anchored["time_ms"]]
    return anchored


def compare_laps_by_distance(
    activity_a: str | int,
    activity_b: str | int,
    lap_number: int,
    metric_cols: list[str] | None = None,
    grid_m: float = DEFAULT_GRID_M,
    speed_eps: float = DEFAULT_SPEED_EPS,
    dist_eps: float = DEFAULT_DIST_EPS,
    include_series: bool = False,
    root: str | Path | None = None,
) -> dict[str, Any]:
    ids = {"a": str(activity_a), "b": str(activity_b)}
    extracted = {}
    for side, activity_id in ids.items():
        payload = _read_detail_artifact(root, activity_id)
        if not payload:
            raise ValueError(f"Missing activity_detail_{activity_id}.json for {side}.")
        extracted[side] = _align_to_motion(_extract_lap(payload, lap_number), speed_eps, dist_eps)

    sample_intervals_ms = []
    for side in ("a", "b"):
        times = extracted[side]["time_ms"]
        sample_intervals_ms.extend(times[index] - times[index - 1] for index in range(1, len(times)))
    sample_interval_ms = median(sample_intervals_ms) if sample_intervals_ms else 0.0

    distance_a = extracted["a"]["distance_m"]
    distance_b = extracted["b"]["distance_m"]
    overlap_m = min(max(distance_a), max(distance_b))
    if overlap_m <= 0:
        raise ValueError("Aligned distance overlap is empty.")
    shorter_raw_distance = min(extracted["a"]["raw_distance_m"], extracted["b"]["raw_distance_m"])
    overlap_ratio = min(1.0, overlap_m / shorter_raw_distance) if shorter_raw_distance > 0 else 0.0

    requested_metrics = list(metric_cols or DEFAULT_METRIC_COLUMNS)
    for metric in requested_metrics:
        if metric not in extracted["a"]["metric_map"]:
            extracted["a"]["metric_map"][metric] = extracted["a"]["metric_map"]["directSpeed"]
        if metric not in extracted["b"]["metric_map"]:
            extracted["b"]["metric_map"][metric] = extracted["b"]["metric_map"]["directSpeed"]

    primary_metric = "directSpeed" if "directSpeed" in requested_metrics else requested_metrics[0]
    metric_map_a = {key: extracted["a"]["metric_map"][key] for key in requested_metrics}
    metric_map_b = {key: extracted["b"]["metric_map"][key] for key in requested_metrics}

    sampled_a = resample_on_distance(
        distance_a,
        metric_map_a,
        extracted["a"]["time_s"],
        grid_m=grid_m,
        max_distance=overlap_m,
    )
    sampled_b = resample_on_distance(
        distance_b,
        metric_map_b,
        extracted["b"]["time_s"],
        grid_m=grid_m,
        max_distance=overlap_m,
    )
    if not sampled_a or not sampled_b:
        raise ValueError("No aligned samples could be created.")
    min_samples = min(len(sampled_a), len(sampled_b))
    sampled_a = sampled_a[:min_samples]
    sampled_b = sampled_b[:min_samples]

    segment_stats = compute_segment_stats(
        sampled_a,
        sampled_b,
        metric_col_left=primary_metric,
        metric_col_right=primary_metric,
        speed_eps=speed_eps,
    )
    if segment_stats["samples"] == 0:
        raise ValueError("No valid overlapping samples after comparison.")

    time_curve_a = _compress_series(extracted["a"]["distance_m"], extracted["a"]["time_s"])
    time_curve_b = _compress_series(extracted["b"]["distance_m"], extracted["b"]["time_s"])
    time_a = _interpolate(time_curve_a, overlap_m)
    time_b = _interpolate(time_curve_b, overlap_m)
    if time_a is None or time_b is None:
        raise ValueError("Could not estimate elapsed time at overlap distance.")

    delta = time_b - time_a
    verdict = "Tie" if abs(delta) < 1e-9 else ("Lap B faster" if delta < 0 else "Lap A faster")
    faster_lap = "lap_a" if delta > 0 else "lap_b" if delta < 0 else "tie"

    quality_flags = []
    if overlap_ratio < 0.7:
        quality_flags.append("insufficient_overlap")
    if sample_interval_ms > 5000:
        quality_flags.append("sparse_sampling")

    report: dict[str, Any] = {
        "activity_a": ids["a"],
        "activity_b": ids["b"],
        "lap_number": lap_number,
        "metric_columns": requested_metrics,
        "grid_m": grid_m,
        "speed_eps_mps": speed_eps,
        "dist_eps_m": dist_eps,
        "overlap_distance_m": round(overlap_m, 3),
        "anchor": {
            "activity_a": {
                "index": extracted["a"]["anchor"],
                "time_ms": extracted["a"]["anchor_time_ms"],
                "distance_m": extracted["a"]["anchor_distance_m"],
                "timestamp_utc": datetime.fromtimestamp(
                    extracted["a"]["anchor_time_ms"] / 1000.0,
                    tz=timezone.utc,
                ).isoformat(),
            },
            "activity_b": {
                "index": extracted["b"]["anchor"],
                "time_ms": extracted["b"]["anchor_time_ms"],
                "distance_m": extracted["b"]["anchor_distance_m"],
                "timestamp_utc": datetime.fromtimestamp(
                    extracted["b"]["anchor_time_ms"] / 1000.0,
                    tz=timezone.utc,
                ).isoformat(),
            },
        },
        "lap_distance_m": {
            "activity_a_raw": extracted["a"]["raw_distance_m"],
            "activity_b_raw": extracted["b"]["raw_distance_m"],
            "matched_distance": round(overlap_m, 3),
        },
        "lap_time_s": {
            "activity_a": round(time_a, 3),
            "activity_b": round(time_b, 3),
            "delta_s": round(delta, 3),
            "faster_lap": faster_lap,
        },
        "verdict": verdict,
        "metrics": {
            "mean_delta_mps": segment_stats["delta_mps"]["mean"],
            "mean_delta_kmh": round(segment_stats["delta_mps"]["mean"] * 3.6, 6),
            "median_delta_mps": segment_stats["delta_mps"]["median"],
            "median_delta_kmh": round(segment_stats["delta_mps"]["median"] * 3.6, 6),
            "percentiles_mps": {
                "p5": segment_stats["delta_mps"]["p5"],
                "p25": segment_stats["delta_mps"]["p25"],
                "p75": segment_stats["delta_mps"]["p75"],
                "p95": segment_stats["delta_mps"]["p95"],
            },
            "faster_share": segment_stats["faster_share"],
            "advantage_windows": segment_stats["windows"],
            "chunked_100m": segment_stats["chunks"][:10],
        },
        "quality": {
            "overlap_ratio_to_shorter": round(overlap_ratio, 3),
            "median_sample_interval_ms": round(sample_interval_ms, 3),
            "flags": quality_flags,
        },
        "samples": {"distance_points": len(sampled_a)},
    }
    if include_series:
        rows: list[dict[str, float | None]] = []
        for left, right in zip(sampled_a, sampled_b):
            left_speed = left.get(primary_metric)
            right_speed = right.get(primary_metric)
            distance_m = left.get("distance_m")
            delta_speed = None
            if left_speed is not None and right_speed is not None:
                delta_speed = left_speed - right_speed
            row: dict[str, float | None] = {
                "distance_m": distance_m if isinstance(distance_m, (int, float)) else None,
                "lap_a_speed_mps": left_speed,
                "lap_b_speed_mps": right_speed,
                "delta_mps": delta_speed,
                "lap_a_moving": left_speed is not None and left_speed >= speed_eps,
                "lap_b_moving": right_speed is not None and right_speed >= speed_eps,
            }
            rows.append(row)
        report["series"] = rows
    return report


def build_lap_distance_comparison(
    activity_a: str | int,
    activity_b: str | int,
    lap_number: int,
    metric_cols: list[str] | None = None,
    grid_m: float = DEFAULT_GRID_M,
    speed_eps: float = DEFAULT_SPEED_EPS,
    dist_eps: float = DEFAULT_DIST_EPS,
    chart_output: str | Path | None = None,
    root: str | Path | None = None,
) -> dict[str, Any]:
    report = compare_laps_by_distance(
        activity_a=activity_a,
        activity_b=activity_b,
        lap_number=lap_number,
        metric_cols=metric_cols,
        grid_m=grid_m,
        speed_eps=speed_eps,
        dist_eps=dist_eps,
        include_series=chart_output is not None,
        root=root,
    )
    output = snapshots_dir(root) / f"lap_comparison_{activity_a}_vs_{activity_b}_lap_{lap_number}.json"
    if chart_output is not None:
        chart_path = Path(chart_output)
        if not chart_path.suffix:
            chart_path = chart_path.with_suffix(".svg")
        elif chart_path.suffix.lower() != ".svg":
            chart_path = chart_path.with_suffix(".svg")
        if not chart_path.is_absolute():
            if str(chart_path.parent) == ".":
                chart_path = snapshots_dir(root) / chart_path
        chart = _build_lap_compare_svg(report, chart_path)
        report["artifacts"] = {
            "chart": str(chart),
        }
        output = snapshots_dir(root) / f"lap_comparison_{activity_a}_vs_{activity_b}_lap_{lap_number}.json"
    write_json(output, report)
    return {
        "output": str(output),
        "report": report,
    }
