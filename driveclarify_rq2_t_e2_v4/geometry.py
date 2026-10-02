"""Layout-invariant ego/route-frame and candidate-set rank primitives."""

from __future__ import annotations

import math
from typing import Mapping, Sequence


def ego_frame_relation(
    point_xy: Sequence[float], ego_xy: Sequence[float], ego_yaw_degrees: float
) -> Mapping[str, float]:
    dx = float(point_xy[0]) - float(ego_xy[0])
    dy = float(point_xy[1]) - float(ego_xy[1])
    yaw = math.radians(float(ego_yaw_degrees))
    forward = (math.cos(yaw), math.sin(yaw))
    right = (-math.sin(yaw), math.cos(yaw))
    return {
        "longitudinal_m": dx * forward[0] + dy * forward[1],
        "lateral_m": dx * right[0] + dy * right[1],
    }


def route_frame_relation(
    point_xy: Sequence[float], route_anchor_xy: Sequence[float], route_tangent_xy: Sequence[float]
) -> Mapping[str, float | str]:
    norm = math.hypot(float(route_tangent_xy[0]), float(route_tangent_xy[1]))
    if norm <= 1e-12:
        raise ValueError("E2_V4_ROUTE_TANGENT_DEGENERATE")
    tangent = (float(route_tangent_xy[0]) / norm, float(route_tangent_xy[1]) / norm)
    right = (-tangent[1], tangent[0])
    dx = float(point_xy[0]) - float(route_anchor_xy[0])
    dy = float(point_xy[1]) - float(route_anchor_xy[1])
    longitudinal = dx * tangent[0] + dy * tangent[1]
    lateral = dx * right[0] + dy * right[1]
    return {
        "route_longitudinal_m": longitudinal,
        "route_lateral_m": lateral,
        "route_left_right": "LEFT" if lateral < -0.25 else "RIGHT" if lateral > 0.25 else "CENTER",
    }


def deterministic_relative_ranks(values: Mapping[str, float], *, descending: bool) -> Mapping[str, int]:
    ordered = sorted(values.items(), key=lambda row: ((-row[1]) if descending else row[1], row[0]))
    return {key: index for index, (key, _value) in enumerate(ordered, 1)}


def normalized_candidate_relation(value: float, values: Sequence[float]) -> float:
    if not values:
        raise ValueError("E2_V4_NORMALIZATION_REQUIRES_CANDIDATE_SET")
    low, high = min(float(row) for row in values), max(float(row) for row in values)
    if abs(high - low) <= 1e-12:
        return 0.5
    return (float(value) - low) / (high - low)


__all__ = [
    "deterministic_relative_ranks",
    "ego_frame_relation",
    "normalized_candidate_relation",
    "route_frame_relation",
]
