"""Deterministic CARLA-world projection over frozen V3 branch polylines."""

from __future__ import annotations

import math
from typing import Any, Iterable, Mapping, Sequence, Tuple

from .contracts import require


# This alias is evaluated at import time even with postponed annotations.
# SimLingo's frozen Python 3.8 runtime cannot subscript the built-in ``tuple``.
Point = Tuple[float, float, float]


def _point(value: Sequence[Any]) -> Point:
    require(len(value) == 3, "WORLD_POINT_DIMENSION_INVALID")
    result = tuple(float(item) for item in value)
    require(all(math.isfinite(item) for item in result), "WORLD_POINT_NONFINITE")
    return result  # type: ignore[return-value]


def distance(first: Point, second: Point) -> float:
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(first, second)))


def _closest_index(points: Sequence[Point], target: Point) -> int:
    require(bool(points), "POLYLINE_EMPTY")
    return min(range(len(points)), key=lambda index: distance(points[index], target))


def _ordered_vehicle_path(
    topology: Mapping[str, Any],
    eligibility: Mapping[str, Any],
    branch_role: str,
) -> tuple[list[Point], int]:
    branch = next(
        (
            item
            for item in topology.get("branches", [])
            if item.get("semantic_role") == branch_role
        ),
        None,
    )
    require(isinstance(branch, Mapping), f"BRANCH_ROLE_MISSING:{branch_role}")
    points = [_point(item) for item in branch.get("polyline_world_xyz", [])]
    require(len(points) >= 3, f"BRANCH_POLYLINE_TOO_SHORT:{branch_role}")
    decision = _point(topology["decision_point"]["xyz"])
    route_start = _point(eligibility["route_start"]["world_xyz"])
    decision_index = _closest_index(points, decision)
    start_index = _closest_index(points, route_start)
    require(start_index != decision_index, f"ROUTE_START_EQUALS_DECISION:{branch_role}")
    if start_index < decision_index:
        ordered = points
        ordered_decision_index = decision_index
    else:
        ordered = list(reversed(points))
        ordered_decision_index = len(points) - 1 - decision_index
    require(ordered_decision_index > 0, f"BRANCH_HAS_NO_PREDECISION_EXTENT:{branch_role}")
    require(
        ordered_decision_index < len(ordered) - 1,
        f"BRANCH_HAS_NO_POSTDECISION_EXTENT:{branch_role}",
    )
    # Use the exact frozen decision point as the signed-station origin.
    ordered[ordered_decision_index] = decision
    return ordered, ordered_decision_index


def _sample_segment(first: Point, second: Point, fraction: float) -> Point:
    return tuple(a + fraction * (b - a) for a, b in zip(first, second))  # type: ignore[return-value]


def _yaw(first: Point, second: Point) -> float:
    dx = second[0] - first[0]
    dy = second[1] - first[1]
    require(math.hypot(dx, dy) > 1e-8, "POLYLINE_ZERO_LENGTH_SEGMENT")
    return math.degrees(math.atan2(dy, dx))


def sample_signed_station(
    topology: Mapping[str, Any],
    eligibility: Mapping[str, Any],
    branch_role: str,
    signed_station_m: float,
    *,
    lateral_offset_m: float = 0.0,
    z_offset_m: float = 0.0,
) -> dict[str, float]:
    """Project a signed decision-point station to an exact world transform.

    Negative station is before the frozen decision point; positive station is
    after it along ``branch_role``.  Lateral displacement uses the local left
    normal.  This is a deterministic geometric projection, not evidence that a
    catalog semantic class exists at that location in CARLA.  When a frozen
    catalog station extends beyond the finite topology polyline, its terminal
    tangent is continued deterministically.  Such numeric extrapolation remains
    subject to the fixture's mandatory live map/occupancy receipt.
    """

    station = float(signed_station_m)
    require(math.isfinite(station), "SIGNED_STATION_NONFINITE")
    require(math.isfinite(lateral_offset_m), "LATERAL_OFFSET_NONFINITE")
    require(math.isfinite(z_offset_m), "Z_OFFSET_NONFINITE")
    points, origin = _ordered_vehicle_path(topology, eligibility, branch_role)
    direction = 1 if station >= 0.0 else -1
    remaining = abs(station)
    index = origin
    if remaining <= 1e-12:
        next_index = origin + 1
        location = points[origin]
        yaw = _yaw(points[origin], points[next_index])
    else:
        location = points[origin]
        yaw = 0.0
        while remaining > 1e-12:
            next_index = index + direction
            if not 0 <= next_index < len(points):
                if direction > 0:
                    tangent_start, tangent_end = points[index - 1], points[index]
                    tangent_length = distance(tangent_start, tangent_end)
                    require(tangent_length > 1e-10, "POLYLINE_TERMINAL_TANGENT_ZERO_LENGTH")
                    scale = remaining / tangent_length
                    location = tuple(
                        tangent_end[axis]
                        + scale * (tangent_end[axis] - tangent_start[axis])
                        for axis in range(3)
                    )
                    yaw = _yaw(tangent_start, tangent_end)
                else:
                    tangent_start, tangent_end = points[index], points[index + 1]
                    tangent_length = distance(tangent_start, tangent_end)
                    require(tangent_length > 1e-10, "POLYLINE_TERMINAL_TANGENT_ZERO_LENGTH")
                    scale = remaining / tangent_length
                    location = tuple(
                        tangent_start[axis]
                        + scale * (tangent_start[axis] - tangent_end[axis])
                        for axis in range(3)
                    )
                    yaw = _yaw(tangent_start, tangent_end)
                remaining = 0.0
                continue
            segment = distance(points[index], points[next_index])
            if segment <= 1e-10:
                index = next_index
                continue
            if remaining <= segment:
                fraction = remaining / segment
                location = _sample_segment(points[index], points[next_index], fraction)
                yaw = (
                    _yaw(points[index], points[next_index])
                    if direction > 0
                    else _yaw(points[next_index], points[index])
                )
                remaining = 0.0
            else:
                remaining -= segment
                index = next_index
    yaw_radians = math.radians(yaw)
    x = location[0] - math.sin(yaw_radians) * lateral_offset_m
    y = location[1] + math.cos(yaw_radians) * lateral_offset_m
    return {
        "x": round(x, 9),
        "y": round(y, 9),
        "z": round(location[2] + z_offset_m, 9),
        "yaw": round(yaw, 9),
        "pitch": 0.0,
        "roll": 0.0,
    }


def route_spawn_transform(route_points: Iterable[Sequence[Any]]) -> dict[str, float]:
    points = [_point(item) for item in route_points]
    require(len(points) >= 2, "ROUTE_SPAWN_WAYPOINTS_INSUFFICIENT")
    return {
        "x": round(points[0][0], 9),
        "y": round(points[0][1], 9),
        "z": round(points[0][2] + 0.5, 9),
        "yaw": round(_yaw(points[0], points[1]), 9),
        "pitch": 0.0,
        "roll": 0.0,
    }


def relation_branch(lateral_relation: str) -> tuple[str, str]:
    mapping = {
        "candidate_A_region": "STRAIGHT_BRANCH",
        "candidate_B_region": "RIGHT_TURN_BRANCH",
        "shared_context_region": "STRAIGHT_BRANCH",
    }
    require(lateral_relation in mapping, f"LATERAL_RELATION_UNSUPPORTED:{lateral_relation}")
    return (
        mapping[lateral_relation],
        "DETERMINISTIC_STRAIGHT_RIGHT_PROXY_REQUIRES_LIVE_SEMANTIC_VALIDATION",
    )
