"""native.plan acceptance implementation."""

from __future__ import annotations

import math
from typing import Any, Mapping, Tuple

from driveclarify.native.contracts import ModelPlan


MAX_FIRST_POINT_FROM_EGO_M = 3.0
MAX_PLAN_SEGMENT_M = 8.0
MIN_PLAN_ARC_LENGTH_M = 2.0


def accept_simlingo_plan(plan: ModelPlan) -> Tuple[bool, Mapping[str, Any], Tuple[str, ...]]:
    """Check existence, finiteness and gross continuity without editing a plan."""

    if not isinstance(plan, ModelPlan) or plan.source != "SIMLINGO":
        return False, {}, ("PLAN_NOT_FROM_SIMLINGO",)
    if len(plan.xy) < 3:
        return False, {"point_count": len(plan.xy)}, ("PLAN_TOO_SHORT",)
    if not all(
        len(point) == 2 and all(math.isfinite(float(value)) for value in point)
        for point in plan.xy
    ):
        return False, {"point_count": len(plan.xy)}, ("PLAN_NONFINITE_OR_MALFORMED",)

    first_distance = math.hypot(float(plan.xy[0][0]), float(plan.xy[0][1]))
    segments = [
        math.hypot(
            float(current[0]) - float(previous[0]),
            float(current[1]) - float(previous[1]),
        )
        for previous, current in zip(plan.xy, plan.xy[1:])
    ]
    arc = sum(segments)
    maximum_segment = max(segments)
    metrics = {
        "point_count": len(plan.xy),
        "first_point_from_ego_m": first_distance,
        "maximum_segment_m": maximum_segment,
        "arc_length_m": arc,
        "plan_source": plan.source,
    }
    reasons = []
    if first_distance > MAX_FIRST_POINT_FROM_EGO_M:
        reasons.append("PLAN_GROSSLY_DISCONTINUOUS_WITH_EGO")
    if maximum_segment > MAX_PLAN_SEGMENT_M:
        reasons.append("PLAN_HAS_GROSS_SEGMENT_JUMP")
    if arc < MIN_PLAN_ARC_LENGTH_M:
        reasons.append("PLAN_HAS_INSUFFICIENT_FORWARD_EXTENT")
    return not reasons, metrics, tuple(reasons or ("SIMLINGO_PLAN_ACCEPTED",))


__all__ = ["accept_simlingo_plan"]
