"""core.trajectory implementation."""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from driveclarify.core.contracts import ContractError, TaskRelation, require_finite_number


def _validate(row: Mapping[str, Any]) -> tuple[list[float], list[tuple[float, float]]]:
    if row.get("valid") is not True:
        raise ContractError("TRAJECTORY_INVALID")
    times = [require_finite_number(x, "relative_time_s") for x in row.get("relative_times_s", [])]
    points = [tuple(require_finite_number(v, "xy_m") for v in p) for p in row.get("xy_m", [])]
    if len(times) != len(points) or len(times) < 2 or any(len(p) != 2 for p in points):
        raise ContractError("TRAJECTORY_SHAPE_INVALID")
    if times[0] > 1e-9 or any(b <= a for a, b in zip(times, times[1:])):
        raise ContractError("TRAJECTORY_TIME_INVALID")
    return times, points


def _at(times: Sequence[float], points: Sequence[tuple[float, float]], target: float) -> tuple[float, float]:
    if target < times[0] - 1e-9 or target > times[-1] + 1e-9:
        raise ContractError("TRAJECTORY_HORIZON_INSUFFICIENT")
    for index in range(1, len(times)):
        if target <= times[index] + 1e-12:
            span = times[index] - times[index - 1]
            weight = (target - times[index - 1]) / span
            return tuple(points[index - 1][axis] + weight * (points[index][axis] - points[index - 1][axis]) for axis in (0, 1))
    return points[-1]


def compare(first: Mapping[str, Any], second: Mapping[str, Any], *, threshold_m: float, horizon_s: float = 2.0, step_s: float = 0.1) -> tuple[TaskRelation, dict[str, Any]]:
    """返回 max_t ||p1(t)-p2(t)||_2；轨迹无效时由调用方转 UNKNOWN。"""
    for key in ("source_frame", "source_time_s", "nonlanguage_context_sha256", "coordinate_frame"):
        if first.get(key) != second.get(key):
            raise ContractError("TRAJECTORY_CONTEXT_MISMATCH:" + key)
    if first.get("coordinate_frame") != "CARLA_EGO_X_FORWARD_Y_RIGHT_METRES":
        raise ContractError("TRAJECTORY_COORDINATE_FRAME_INVALID")
    t1, p1 = _validate(first)
    t2, p2 = _validate(second)
    threshold = require_finite_number(threshold_m, "threshold_m")
    count = int(round(horizon_s / step_s))
    grid = [round(index * step_s, 10) for index in range(count + 1)]
    distances = [math.dist(_at(t1, p1, t), _at(t2, p2, t)) for t in grid]
    maximum = max(distances)
    relation = TaskRelation.EQUIVALENT if maximum <= threshold else TaskRelation.DIVERGENT
    return relation, {"max_l2_m": maximum, "threshold_m": threshold, "horizon_s": horizon_s, "step_s": step_s, "grid_count": len(grid)}
