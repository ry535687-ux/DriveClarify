"""native.replan implementation."""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple

from driveclarify.native.contracts import AdmissibilityResult, AuthoritativeRoute, EgoState, ModelPlan, RoutePoint, RouteTransactionReceipt, TransitionDisposition, canonical_sha256
from driveclarify.native.plan_acceptance import accept_simlingo_plan


def _distance(a: Sequence[float], b: Sequence[float]) -> float:
    return math.sqrt(sum((float(x) - float(y)) ** 2 for x, y in zip(a, b)))


def _distance_xy(a: Sequence[float], b: Sequence[float]) -> float:
    return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))


def _wrap_degrees(value: float) -> float:
    return (float(value) + 180.0) % 360.0 - 180.0


def _heading(a: RoutePoint, b: RoutePoint) -> float:
    return math.degrees(math.atan2(float(b.y) - float(a.y), float(b.x) - float(a.x)))


def _nearest_point_index(route: AuthoritativeRoute, ego: EgoState) -> Tuple[int, float]:
    values = [_distance_xy(point.xyz, ego.xyz) for point in route.points]
    index = min(range(len(values)), key=values.__getitem__)
    return index, float(values[index])


def _arc_lengths(points: Sequence[RoutePoint]) -> Tuple[float, ...]:
    values = [0.0]
    for first, second in zip(points, points[1:]):
        values.append(values[-1] + _distance_xy(first.xyz, second.xyz))
    return tuple(values)


def _maximum_heading_change_per_m(points: Sequence[RoutePoint], start: int, horizon_m: float) -> float:
    if len(points) < 3:
        return 0.0
    arcs = _arc_lengths(points)
    limit = arcs[start] + float(horizon_m)
    maximum = 0.0
    for index in range(max(1, start), len(points) - 1):
        if arcs[index] > limit:
            break
        before = _heading(points[index - 1], points[index])
        after = _heading(points[index], points[index + 1])
        span = max(
            1.0e-6,
            0.5
            * (
                _distance_xy(points[index - 1].xyz, points[index].xyz)
                + _distance_xy(points[index].xyz, points[index + 1].xyz)
            ),
        )
        maximum = max(maximum, abs(_wrap_degrees(after - before)) * math.pi / 180.0 / span)
    return maximum


@dataclass(frozen=True)
class ReplanThresholds:
    maximum_route_age_frames: int = 120
    maximum_destination_delta_m: float = 0.001
    maximum_join_distance_m: float = 3.0
    maximum_join_heading_delta_degrees: float = 45.0
    maximum_route_segment_gap_m: float = 8.0
    maximum_local_curvature_per_m: float = 0.35
    local_curvature_horizon_m: float = 35.0
    maximum_lateral_acceleration_mps2: float = 2.5
    comfortable_deceleration_mps2: float = 2.5
    reaction_time_s: float = 0.5
    minimum_commitment_margin_m: float = 3.0


class ReplanAdmissibility:
    """Assess only transition geometry; never choose passenger intent."""

    def __init__(self, thresholds: ReplanThresholds = ReplanThresholds()):
        self.thresholds = thresholds

    def assess(
        self,
        ego: EgoState,
        current: AuthoritativeRoute,
        resolved: AuthoritativeRoute,
        preview_plan: Optional[ModelPlan] = None,
    ) -> AdmissibilityResult:
        threshold = self.thresholds
        reasons = []
        reject = []
        defer = []

        route_age = int(ego.frame) - int(resolved.source_frame)
        destination_delta = _distance(current.destination_xyz, resolved.destination_xyz)
        index, join_distance = _nearest_point_index(resolved, ego)
        next_index = min(index + 1, len(resolved.points) - 1)
        prior_index = max(0, index - 1)
        if next_index == index:
            route_heading = _heading(resolved.points[prior_index], resolved.points[index])
        else:
            route_heading = _heading(resolved.points[index], resolved.points[next_index])
        heading_delta = abs(_wrap_degrees(route_heading - ego.yaw_degrees))

        segments = [
            _distance(first.xyz, second.xyz)
            for first, second in zip(resolved.points, resolved.points[1:])
        ]
        max_segment = max(segments)
        curvature = _maximum_heading_change_per_m(
            resolved.points, index, threshold.local_curvature_horizon_m
        )
        safe_curve_speed = (
            math.sqrt(threshold.maximum_lateral_acceleration_mps2 / curvature)
            if curvature > 1.0e-9
            else float("inf")
        )

        arcs = _arc_lengths(resolved.points)
        commitment_index = (
            resolved.commitment_point_index
            if resolved.commitment_point_index is not None
            else len(resolved.points) - 1
        )
        remaining_commitment = arcs[commitment_index] - arcs[index]
        excess_speed = max(0.0, ego.speed_mps - safe_curve_speed)
        deceleration_distance = (
            ego.speed_mps * threshold.reaction_time_s
            + (excess_speed * excess_speed)
            / (2.0 * threshold.comfortable_deceleration_mps2)
        )

        if route_age < 0 or route_age > threshold.maximum_route_age_frames:
            reject.append("RESOLVED_ROUTE_STALE")
        if destination_delta > threshold.maximum_destination_delta_m:
            reject.append("SAME_DESTINATION_CONTRACT_FAILED")
        if max_segment > threshold.maximum_route_segment_gap_m:
            reject.append("RESOLVED_ROUTE_DISCONNECTED")
        if not resolved.full_route_owner or not resolved.active_suffix_owner:
            reject.append("ROUTE_OWNERSHIP_MISSING")
        if resolved.connector_id is not None and not resolved.connector_id.strip():
            reject.append("SELECTED_CONNECTOR_ID_INVALID")
        if join_distance > threshold.maximum_join_distance_m:
            defer.append("EGO_TO_RESOLVED_ROUTE_JOIN_TOO_FAR")
        if heading_delta > threshold.maximum_join_heading_delta_degrees:
            defer.append("ROUTE_JOIN_HEADING_DISCONTINUOUS")
        if curvature > threshold.maximum_local_curvature_per_m:
            reject.append("RESOLVED_ROUTE_LOCAL_CURVATURE_INFEASIBLE")

        missed = False
        if resolved.commitment_point_index is not None:
            if index > resolved.commitment_point_index:
                missed = True
                reject.append("MISSED_REPLAN_OPPORTUNITY")
            elif (
                excess_speed > 0.0
                and remaining_commitment
                < deceleration_distance + threshold.minimum_commitment_margin_m
            ):
                missed = True
                reject.append("MISSED_REPLAN_OPPORTUNITY_INSUFFICIENT_BRAKING_MARGIN")

        plan_metrics: Mapping[str, Any] = {}
        if preview_plan is not None:
            accepted, plan_metrics, plan_reasons = accept_simlingo_plan(preview_plan)
            if not accepted:
                defer.extend(plan_reasons)

        metrics: Dict[str, Any] = {
            "route_age_frames": route_age,
            "destination_delta_m": destination_delta,
            "nearest_resolved_route_index": index,
            "join_distance_m": join_distance,
            "join_heading_delta_degrees": heading_delta,
            "maximum_route_segment_gap_m": max_segment,
            "maximum_local_curvature_per_m": curvature,
            "safe_curve_speed_mps": None if math.isinf(safe_curve_speed) else safe_curve_speed,
            "ego_speed_mps": ego.speed_mps,
            "remaining_commitment_distance_m": remaining_commitment,
            "deceleration_distance_m": deceleration_distance,
            "target_point": list(resolved.target_point),
            "road_option": resolved.road_option,
            "full_route_owner": resolved.full_route_owner,
            "active_suffix_owner": resolved.active_suffix_owner,
            "connector_id": resolved.connector_id,
            "simlingo_plan_acceptance": dict(plan_metrics),
        }
        if reject:
            disposition = TransitionDisposition.REJECT_STALE_OR_INFEASIBLE
            reasons = reject + defer
        elif defer:
            disposition = TransitionDisposition.DEFER_COMMIT
            reasons = defer
        else:
            disposition = TransitionDisposition.COMMIT_NOW
            reasons = ["ROUTE_TRANSITION_ADMISSIBLE"]
        return AdmissibilityResult(
            disposition=disposition,
            reason_codes=tuple(dict.fromkeys(reasons)),
            metrics=metrics,
            route_id=resolved.route_id,
            missed_replan_opportunity=missed,
        )


class RouteTransitionManager:
    """Exactly-once authority transaction wrapper with deferred reevaluation."""

    def __init__(
        self,
        commit_route: Callable[[AuthoritativeRoute], Mapping[str, Any]],
        admissibility: Optional[ReplanAdmissibility] = None,
    ):
        if not callable(commit_route):
            raise TypeError("ROUTE_COMMIT_CALLBACK_REQUIRED")
        self._commit_route = commit_route
        self._admissibility = admissibility or ReplanAdmissibility()
        self._pending = None
        self._committed_route_ids = set()
        self._transaction_count = 0

    @property
    def transaction_count(self) -> int:
        return self._transaction_count

    @property
    def pending_route_id(self) -> Optional[str]:
        return None if self._pending is None else self._pending[1].route_id

    def propose(
        self,
        ego: EgoState,
        current: AuthoritativeRoute,
        resolved: AuthoritativeRoute,
        preview_plan: Optional[ModelPlan] = None,
    ) -> Tuple[AdmissibilityResult, Optional[RouteTransactionReceipt]]:
        if resolved.route_id in self._committed_route_ids:
            raise RuntimeError("ROUTE_TRANSACTION_ALREADY_COMMITTED")
        assessment = self._admissibility.assess(
            ego, current, resolved, preview_plan=preview_plan
        )
        if assessment.disposition is TransitionDisposition.DEFER_COMMIT:
            self._pending = (current, resolved, preview_plan)
            return assessment, None
        self._pending = None
        if assessment.disposition is not TransitionDisposition.COMMIT_NOW:
            return assessment, None
        return assessment, self._commit(current, resolved)

    def reevaluate(
        self, ego: EgoState
    ) -> Tuple[Optional[AdmissibilityResult], Optional[RouteTransactionReceipt]]:
        if self._pending is None:
            return None, None
        current, resolved, preview_plan = self._pending
        assessment = self._admissibility.assess(
            ego, current, resolved, preview_plan=preview_plan
        )
        if assessment.disposition is TransitionDisposition.DEFER_COMMIT:
            return assessment, None
        self._pending = None
        if assessment.disposition is not TransitionDisposition.COMMIT_NOW:
            return assessment, None

        # A deferred candidate was constructed at an earlier ego pose.  Once it
        # becomes admissible, reconnect its active suffix at the current nearest
        # point before handing it to the native online route owner.  Otherwise the
        # stale first point can violate the owner's origin-continuity contract even
        # though the current generic join assessment correctly says COMMIT_NOW.
        nearest, _ = _nearest_point_index(resolved, ego)
        start = max(0, nearest - 1)
        points = tuple(resolved.points[start:])
        if len(points) < 2:
            return assessment, None
        commitment = resolved.commitment_point_index
        if commitment is not None:
            commitment = max(0, min(commitment - start, len(points) - 1))
        target_index = min(2, len(points) - 1)
        reconnected = replace(
            resolved,
            points=points,
            target_point=(points[target_index].x, points[target_index].y),
            road_option=points[target_index].road_option,
            commitment_point_index=commitment,
        )
        return assessment, self._commit(current, reconnected)

    def _commit(
        self, current: AuthoritativeRoute, resolved: AuthoritativeRoute
    ) -> RouteTransactionReceipt:
        owner_receipt = dict(self._commit_route(resolved))
        if owner_receipt.get("committed") is not True:
            raise RuntimeError("ROUTE_OWNER_DID_NOT_CONFIRM_COMMIT")
        self._transaction_count += 1
        self._committed_route_ids.add(resolved.route_id)
        return RouteTransactionReceipt(
            transaction_id="v11-route-" + canonical_sha256(
                {
                    "count": self._transaction_count,
                    "before": current.route_id,
                    "after": resolved.route_id,
                    "owner": owner_receipt,
                }
            )[:24],
            route_id_before=current.route_id,
            route_id_after=resolved.route_id,
            transaction_count=self._transaction_count,
            committed=True,
            a1_route_switch_active=True,
            owner_receipt=owner_receipt,
        )


__all__ = [
    "ReplanAdmissibility",
    "ReplanThresholds",
    "RouteTransitionManager",
]
