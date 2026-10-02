"""Generic, answer-blind material-consequence comparison for V10 routes."""

from __future__ import annotations

import math
from typing import Sequence

from .contracts import CandidateRoute, ConsequenceDecision, PolicyAction


MATERIAL_ROUTE_SEPARATION_M = 1.5
COMPARISON_HORIZON_POINTS = 80


def _xy_distance(first, second):
    return math.hypot(float(first.x) - float(second.x), float(first.y) - float(second.y))


class MaterialRouteConsequenceEvaluator:
    """ASK only when candidate routes have materially different execution.

    This evaluator does not decide which candidate is correct.  Equivalent
    candidate consequences are safe to ACT using the deterministic rank one.
    """

    def __init__(self, separation_m=MATERIAL_ROUTE_SEPARATION_M):
        self.separation_m = float(separation_m)
        if not math.isfinite(self.separation_m) or self.separation_m <= 0.0:
            raise ValueError("MATERIAL_ROUTE_SEPARATION_INVALID")

    def evaluate(self, routes: Sequence[CandidateRoute]) -> ConsequenceDecision:
        routes = tuple(routes)
        if len(routes) < 2:
            return ConsequenceDecision(
                action=PolicyAction.WAIT,
                selected_candidate_id=None,
                question=None,
                reason_codes=("INSUFFICIENT_CANDIDATE_ROUTES",),
            )
        first, second = routes[0], routes[1]
        if first.route.destination_xyz != second.route.destination_xyz:
            return self._ask("CANDIDATE_DESTINATIONS_DIFFER")
        count = min(
            len(first.route.points),
            len(second.route.points),
            COMPARISON_HORIZON_POINTS,
        )
        if count < 2:
            return ConsequenceDecision(
                action=PolicyAction.WAIT,
                selected_candidate_id=None,
                question=None,
                reason_codes=("CANDIDATE_ROUTE_HORIZON_INSUFFICIENT",),
            )
        separations = tuple(
            _xy_distance(first.route.points[index], second.route.points[index])
            for index in range(count)
        )
        option_pairs = tuple(
            (
                first.route.points[index].road_option,
                second.route.points[index].road_option,
            )
            for index in range(count)
        )
        materially_separate = max(separations) > self.separation_m
        maneuver_differs = any(a != b for a, b in option_pairs)
        connector_differs = first.route.connector_id != second.route.connector_id
        if materially_separate or maneuver_differs or connector_differs:
            reasons = []
            if materially_separate:
                reasons.append("CANDIDATE_ROUTE_GEOMETRY_MATERIALLY_DIFFERS")
            if maneuver_differs:
                reasons.append("CANDIDATE_ROADOPTION_SEQUENCE_DIFFERS")
            if connector_differs:
                reasons.append("CANDIDATE_CONNECTOR_DIFFERS")
            return self._ask(*reasons)
        return ConsequenceDecision(
            action=PolicyAction.ACT,
            selected_candidate_id=first.candidate.candidate_id,
            question=None,
            reason_codes=("CANDIDATE_DRIVING_CONSEQUENCES_EQUIVALENT",),
        )

    @staticmethod
    def _ask(*reasons):
        return ConsequenceDecision(
            action=PolicyAction.ASK,
            selected_candidate_id=None,
            question="Which of the currently grounded alternatives did you mean?",
            reason_codes=tuple(reasons),
        )


__all__ = [
    "COMPARISON_HORIZON_POINTS",
    "MATERIAL_ROUTE_SEPARATION_M",
    "MaterialRouteConsequenceEvaluator",
]
