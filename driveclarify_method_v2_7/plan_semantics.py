"""Geometry-only evidence that a predicted plan realizes its bound meaning."""

from __future__ import annotations

import math
from typing import Any, Iterable


def _points(rows: Iterable[Any]) -> tuple[tuple[float, float], ...]:
    points = tuple((float(row[0]), float(row[1])) for row in rows)
    if any(not (math.isfinite(x) and math.isfinite(y)) for x, y in points):
        raise ValueError("PLAN_POINT_NOT_FINITE")
    return points


def assess_execution_location_plan_realization(
    *,
    target_points_ego_local_xy_m: Iterable[Any],
    predicted_route_ego_local_xy_m: Iterable[Any],
) -> dict[str, Any]:
    """Require a short-horizon plan to physically reach its own bound event.

    FIRST/SECOND topology labels alone are not plan evidence.  This predicate
    uses only exact, already-produced geometry: the candidate's junction entry
    and exit, and the model's raw local route.  No fitted or scenario-specific
    numeric threshold is introduced.
    """

    try:
        targets = _points(target_points_ego_local_xy_m)
        predicted = _points(predicted_route_ego_local_xy_m)
    except (IndexError, TypeError, ValueError) as error:
        return {
            "realized": False,
            "reason_code": "EL_PLAN_GEOMETRY_INVALID",
            "error_type": type(error).__name__,
            "new_numeric_threshold_count": 0,
        }
    if len(targets) != 2 or len(predicted) < 2:
        return {
            "realized": False,
            "reason_code": "EL_PLAN_OR_BOUND_EVENT_INCOMPLETE",
            "target_point_count": len(targets),
            "predicted_point_count": len(predicted),
            "new_numeric_threshold_count": 0,
        }

    entry, exit_point = targets
    terminal = predicted[-1]
    predicted_arc_length_m = sum(
        math.hypot(right[0] - left[0], right[1] - left[1])
        for left, right in zip(predicted, predicted[1:])
    )
    ego_to_entry_m = math.hypot(entry[0], entry[1])
    ego_to_exit_m = math.hypot(exit_point[0], exit_point[1])
    connector_length_m = math.hypot(
        exit_point[0] - entry[0], exit_point[1] - entry[1]
    )
    terminal_to_exit_m = math.hypot(
        terminal[0] - exit_point[0], terminal[1] - exit_point[1]
    )
    entry_is_ahead = entry[0] > 0.0
    plan_reaches_entry_range = predicted_arc_length_m >= ego_to_entry_m
    plan_lands_within_bound_connector = terminal_to_exit_m <= connector_length_m
    plan_approaches_exit = terminal_to_exit_m < ego_to_exit_m
    target_lateral_m = exit_point[1]
    predicted_terminal_lateral_m = terminal[1]
    lateral_half_plane_matches = bool(
        target_lateral_m == 0.0
        or predicted_terminal_lateral_m * target_lateral_m > 0.0
    )
    realized = bool(
        entry_is_ahead
        and plan_reaches_entry_range
        and plan_lands_within_bound_connector
        and plan_approaches_exit
        and lateral_half_plane_matches
    )
    failed = tuple(
        name
        for name, passed in (
            ("BOUND_ENTRY_NOT_AHEAD", entry_is_ahead),
            ("PREDICTION_HORIZON_DOES_NOT_REACH_BOUND_ENTRY", plan_reaches_entry_range),
            ("PREDICTED_TERMINAL_OUTSIDE_BOUND_CONNECTOR", plan_lands_within_bound_connector),
            ("PREDICTED_PLAN_DOES_NOT_APPROACH_BOUND_EXIT", plan_approaches_exit),
            ("PREDICTED_PLAN_WRONG_TARGET_LATERAL_HALF_PLANE", lateral_half_plane_matches),
        )
        if not passed
    )
    return {
        "realized": realized,
        "reason_code": (
            "EL_PLAN_REACHES_OWN_BOUND_TOPOLOGY_EVENT"
            if realized
            else "EL_PLAN_DOES_NOT_REALIZE_OWN_BOUND_TOPOLOGY_EVENT"
        ),
        "failed_predicates": failed,
        "entry_is_ahead": entry_is_ahead,
        "plan_reaches_entry_range": plan_reaches_entry_range,
        "plan_lands_within_bound_connector": plan_lands_within_bound_connector,
        "plan_approaches_exit": plan_approaches_exit,
        "lateral_half_plane_matches": lateral_half_plane_matches,
        "predicted_arc_length_m": predicted_arc_length_m,
        "ego_to_entry_m": ego_to_entry_m,
        "ego_to_exit_m": ego_to_exit_m,
        "connector_length_m": connector_length_m,
        "terminal_to_exit_m": terminal_to_exit_m,
        "target_terminal_lateral_m": target_lateral_m,
        "predicted_terminal_lateral_m": predicted_terminal_lateral_m,
        "new_numeric_threshold_count": 0,
    }


__all__ = ["assess_execution_location_plan_realization"]
