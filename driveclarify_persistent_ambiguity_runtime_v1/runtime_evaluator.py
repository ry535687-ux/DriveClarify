"""Fresh same-frame decision-window evaluation for the persistent runtime."""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import combinations
from typing import Any, Callable, Mapping, Optional, Sequence, Tuple

from .contracts import canonical_sha256
from .evidence_adapter import DecisionWindowEvidenceAdapter
from .scheduler import CandidateEvidenceRefreshBundle
from .types import EvidenceProvenance, PlanCoverageStatus, Visibility


@dataclass(frozen=True)
class RuntimeWindowObservation:
    source_observation_id: str
    source_frame_id: Any
    observed_monotonic_time: float
    route_version: str
    environment_digest: str
    current_progress_m: float
    current_speed_mps: float
    speed_limit_mps: float
    fixed_delta_seconds: float
    maximum_normal_planning_interval_s: float
    calibrated_uncertainty_m: float
    lane_clearance_m: float
    maneuver_onset_progress_m: float
    candidate_commitment_progress_m: Mapping[str, float]
    runtime_latency_upper_bound_s: float
    dynamic_safety_gate: bool
    hard_rule_gate: bool
    alignment_verified: bool
    route_world_xy_m: Tuple[Tuple[float, float], ...]
    ego_location_xy_m: Tuple[float, float]
    ego_forward_xy: Tuple[float, float]
    ego_right_xy: Tuple[float, float]
    model_coordinate_contract_id: str
    expected_plan_checkpoint_spacing_m: float
    plan_checkpoint_spacing_tolerance_m: float
    expected_plan_point_count: int
    execution_latency_distance_m: float
    coordinate_transform_verified: bool
    # V3 append-only projections.  The V2 fields above remain byte/semantic
    # compatible; these isolate current physical safety and same-frame local
    # transform validity from future semantic connector authorization and a
    # historical cross-route calibration identity.
    current_physical_safety_gate: Optional[bool] = None
    model_local_transform_verified: Optional[bool] = None
    model_local_planar_basis_error: Optional[float] = None
    model_local_planar_basis_error_upper: Optional[float] = None
    route_projection_error_m: Optional[float] = None
    route_projection_segment_index: Optional[int] = None
    route_projection_segment_fraction: Optional[float] = None


@dataclass(frozen=True)
class RuntimeDecisionWindow:
    status: str
    source_observation_id: str
    source_frame_id: Any
    route_version: str
    environment_digest: str
    current_progress_m: float
    shared_action_end_progress_m: Optional[float]
    current_action_relation: str
    future_obligation_relation: str
    candidate_relationship: str
    full_plan_coverage: bool
    recoverability: str
    latest_safe_clarification_monotonic: Optional[float]
    latest_safe_slack_s: Optional[float]
    time_to_divergence_lower_bound_s: Optional[float]
    decision_window_digest: str
    current_action_equivalence_evidence_digest: str
    valid_until_monotonic: Optional[float]
    plan_coverage_evidence: Mapping[str, Any]
    reason_codes: Tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _route_points(result: Any) -> Sequence[Sequence[float]]:
    value = getattr(result, "raw_route", None)
    if value is None:
        value = getattr(result, "route", None)
    if value is None:
        return ()
    try:
        value = value.detach().cpu().tolist()
    except AttributeError:
        if hasattr(value, "tolist"):
            value = value.tolist()
    while isinstance(value, list) and len(value) == 1 and isinstance(value[0], list):
        if value[0] and isinstance(value[0][0], (int, float)):
            break
        value = value[0]
    return value if isinstance(value, (list, tuple)) else ()


def _speed_points(result: Any) -> Sequence[Sequence[float]]:
    value = getattr(result, "raw_speed", None)
    if value is None:
        value = getattr(result, "speed", None)
    if value is None:
        return ()
    try:
        value = value.detach().cpu().tolist()
    except AttributeError:
        if hasattr(value, "tolist"):
            value = value.tolist()
    while isinstance(value, list) and len(value) == 1 and isinstance(value[0], list):
        if value[0] and isinstance(value[0][0], (int, float)):
            break
        value = value[0]
    return value if isinstance(value, (list, tuple)) else ()


def _arc(points: Sequence[Sequence[float]]) -> float:
    try:
        return sum(
            math.hypot(float(right[0]) - float(left[0]), float(right[1]) - float(left[1]))
            for left, right in zip(points, points[1:])
        )
    except (IndexError, TypeError, ValueError):
        return 0.0


def _prefix(points: Sequence[Sequence[float]], distance_m: float) -> list[tuple[float, float]]:
    if not points:
        return []
    rows = [(float(points[0][0]), float(points[0][1]))]
    distance = 0.0
    for left, right in zip(points, points[1:]):
        distance += math.hypot(float(right[0]) - float(left[0]), float(right[1]) - float(left[1]))
        rows.append((float(right[0]), float(right[1])))
        if distance >= distance_m:
            break
    return rows


def _pairwise_prefix_deviation(
    plan_rows: Mapping[str, Sequence[Sequence[float]]], distance_m: float
) -> Optional[float]:
    maximum = 0.0
    for left_id, right_id in combinations(sorted(plan_rows), 2):
        left = _prefix(plan_rows[left_id], distance_m)
        right = _prefix(plan_rows[right_id], distance_m)
        count = min(len(left), len(right))
        if count < 2:
            return None
        maximum = max(
            maximum,
            max(
                math.hypot(left[index][0] - right[index][0], left[index][1] - right[index][1])
                for index in range(count)
            ),
        )
    return maximum


def _pairwise_fixed_time_deviation(
    plan_rows: Mapping[str, Sequence[Sequence[float]]]
) -> Optional[float]:
    """Compare the fixed-time references consumed by the one existing PID.

    SimLingo emits ten positions, while the current PID tick derives desired
    speed from waypoint indices 0 and 2.  The first three points therefore
    define the longitudinal reference for this bounded control action; later
    points remain future-plan evidence and must not manufacture current
    divergence.
    """

    maximum = 0.0
    for left_id, right_id in combinations(sorted(plan_rows), 2):
        left = plan_rows[left_id]
        right = plan_rows[right_id]
        if len(left) != 10 or len(right) != 10:
            return None
        try:
            maximum = max(
                maximum,
                max(
                    math.hypot(
                        float(left[index][0]) - float(right[index][0]),
                        float(left[index][1]) - float(right[index][1]),
                    )
                    for index in range(3)
                ),
            )
        except (IndexError, TypeError, ValueError):
            return None
    return maximum


def _densify_polyline(
    points: Sequence[Sequence[float]], maximum_step_m: float
) -> list[tuple[float, float]]:
    if not points or maximum_step_m <= 0.0:
        return []
    dense = [(float(points[0][0]), float(points[0][1]))]
    for left, right in zip(points, points[1:]):
        dx = float(right[0]) - float(left[0])
        dy = float(right[1]) - float(left[1])
        distance = math.hypot(dx, dy)
        steps = max(1, int(math.ceil(distance / maximum_step_m)))
        dense.extend(
            (
                float(left[0]) + dx * index / steps,
                float(left[1]) + dy * index / steps,
            )
            for index in range(1, steps + 1)
        )
    return dense


def _world_samples_at_route_progress(
    world_points: Sequence[Sequence[float]],
    projections: Sequence[Mapping[str, Any]],
    *,
    current_progress_m: float,
    end_progress_m: float,
    spacing_m: float,
) -> list[tuple[float, float]]:
    if len(world_points) != len(projections) or not world_points or spacing_m <= 0.0:
        return []
    progress = [float(row["progress_m"]) for row in projections]
    targets = [float(current_progress_m)]
    cursor = float(current_progress_m) + spacing_m
    while cursor < float(end_progress_m):
        targets.append(cursor)
        cursor += spacing_m
    if float(end_progress_m) > targets[-1] + 1e-9:
        targets.append(float(end_progress_m))
    return _world_samples_for_route_targets(world_points, projections, targets)


def _world_samples_for_route_targets(
    world_points: Sequence[Sequence[float]],
    projections: Sequence[Mapping[str, Any]],
    targets: Sequence[float],
) -> list[tuple[float, float]]:
    if len(world_points) != len(projections) or not world_points:
        return []
    progress = [float(row["progress_m"]) for row in projections]
    samples = []
    for target in targets:
        target = float(target)
        if target <= progress[0] + 1e-9:
            samples.append((float(world_points[0][0]), float(world_points[0][1])))
            continue
        matched = None
        for index, (left_progress, right_progress) in enumerate(
            zip(progress, progress[1:])
        ):
            if left_progress - 1e-9 <= target <= right_progress + 1e-9:
                span = right_progress - left_progress
                fraction = 0.0 if span <= 1e-9 else (target - left_progress) / span
                matched = (
                    float(world_points[index][0])
                    + fraction
                    * (float(world_points[index + 1][0]) - float(world_points[index][0])),
                    float(world_points[index][1])
                    + fraction
                    * (float(world_points[index + 1][1]) - float(world_points[index][1])),
                )
                break
        if matched is None:
            return []
        samples.append(matched)
    return samples


def _plan_coverage(
    plan_id: str,
    local_points: Sequence[Sequence[float]],
    observation: RuntimeWindowObservation,
    *,
    shared_window_distance_m: float,
    shared_end_progress_m: float,
    source_alignment_verified: bool,
) -> tuple[Any, Sequence[Sequence[float]], Mapping[str, Any]]:
    provenance = EvidenceProvenance(
        producer="PersistentAmbiguityRuntimeV1.RuntimeDecisionWindow",
        source_kind="LIVE_CARLA_SAME_FRAME_PLAN_ROUTE_PROJECTION",
        source_observation_id=observation.source_observation_id,
        source_frame_id=observation.source_frame_id,
        observed_monotonic_time=observation.observed_monotonic_time,
        source_simulation_time=None,
        coordinate_transform_id="live-ego-basis-" + canonical_sha256(
            {
                "observation": observation.source_observation_id,
                "frame": observation.source_frame_id,
                "origin": observation.ego_location_xy_m,
                "forward": observation.ego_forward_xy,
                "right": observation.ego_right_xy,
            }
        )[:24],
        visibility=Visibility.RUNTIME_OBSERVABLE,
        artifact_sha256=None,
        comparison_context_id=observation.source_observation_id,
    )
    try:
        rows = tuple((float(row[0]), float(row[1])) for row in local_points)
        expected = float(observation.expected_plan_checkpoint_spacing_m)
        tolerance = float(observation.plan_checkpoint_spacing_tolerance_m)
        expected_count = int(observation.expected_plan_point_count)
        point_finite_mask = tuple(
            len(row) >= 2 and all(math.isfinite(float(value)) for value in row[:2])
            for row in local_points
        )
        segment_lengths = tuple(
            math.hypot(right[0] - left[0], right[1] - left[1])
            for left, right in zip(rows, rows[1:])
        )
        segment_valid_mask = tuple(
            point_finite_mask[index]
            and point_finite_mask[index + 1]
            and value > 1e-6
            and abs(value - expected) <= tolerance
            for index, value in enumerate(segment_lengths)
        )
        valid_mask = []
        prefix_still_valid = bool(point_finite_mask and point_finite_mask[0])
        for index in range(len(rows)):
            if index:
                prefix_still_valid = bool(
                    prefix_still_valid and segment_valid_mask[index - 1]
                )
            valid_mask.append(prefix_still_valid)
        valid_count = next(
            (index for index, value in enumerate(valid_mask) if not value),
            len(valid_mask),
        )
        valid_rows = rows[:valid_count]
        representation_valid = bool(
            len(rows) == expected_count
            and expected_count >= 3
            and len(valid_rows) >= 3
            and expected > 0.0
            and tolerance > 0.0
            and math.hypot(valid_rows[0][0], valid_rows[0][1]) <= tolerance
            and observation.coordinate_transform_verified
            and observation.model_coordinate_contract_id
            == "SIMLINGO_CONTROL_PID_CHECKPOINTS_1M_EGO_X_FORWARD_Y_RIGHT"
        )
        world = DecisionWindowEvidenceAdapter.model_local_to_world(
            valid_rows,
            ego_location_xy_m=observation.ego_location_xy_m,
            ego_forward_xy=observation.ego_forward_xy,
            ego_right_xy=observation.ego_right_xy,
        )
        interval = DecisionWindowEvidenceAdapter.project_plan_interval(
            world, observation.route_world_xy_m
        )
        dense_local = _densify_polyline(
            valid_rows, tolerance
        )
        dense_world = DecisionWindowEvidenceAdapter.model_local_to_world(
            dense_local,
            ego_location_xy_m=observation.ego_location_xy_m,
            ego_forward_xy=observation.ego_forward_xy,
            ego_right_xy=observation.ego_right_xy,
        )
        dense_interval = DecisionWindowEvidenceAdapter.project_plan_interval(
            dense_world, observation.route_world_xy_m
        )
        projection_error = interval.get("route_projection_error_max_m")
        start = interval.get("route_progress_start_m")
        end = interval.get("route_progress_end_m")
        origin_world_error = (
            math.hypot(
                float(world[0][0]) - float(observation.ego_location_xy_m[0]),
                float(world[0][1]) - float(observation.ego_location_xy_m[1]),
            )
            if world
            else math.inf
        )
        start_aligned = bool(
            start is not None
            and abs(float(start) - float(observation.current_progress_m))
            <= float(observation.calibrated_uncertainty_m) + tolerance
            and origin_world_error <= tolerance
        )
        corridor_required_end = (
            float(shared_end_progress_m)
            + float(observation.calibrated_uncertainty_m)
            + tolerance
            + float(observation.execution_latency_distance_m)
        )
        dense_projections = dense_interval.get("projections", ())
        corridor_end_index = next(
            (
                index
                for index, row in enumerate(dense_projections)
                if float(row["progress_m"]) + 1e-9 >= corridor_required_end
            ),
            None,
        )
        corridor_world = (
            dense_world[: corridor_end_index + 1]
            if corridor_end_index is not None
            else dense_world
        )
        corridor_interval = DecisionWindowEvidenceAdapter.project_plan_interval(
            corridor_world, observation.route_world_xy_m
        )
        corridor_projections = corridor_interval.get("projections", ())
        corridor_progress_deltas = tuple(
            float(right["progress_m"]) - float(left["progress_m"])
            for left, right in zip(corridor_projections, corridor_projections[1:])
        )
        corridor_world_deltas = tuple(
            math.hypot(
                float(right[0]) - float(left[0]),
                float(right[1]) - float(left[1]),
            )
            for left, right in zip(corridor_world, corridor_world[1:])
        )
        route_progress_injective = bool(
            corridor_progress_deltas
            and len(corridor_progress_deltas) == len(corridor_world_deltas)
            and all(
                progress_delta > 1e-6
                for progress_delta, world_delta in zip(
                    corridor_progress_deltas, corridor_world_deltas
                )
                if world_delta > 1e-6
            )
        )
        shared_projection_error = corridor_interval.get(
            "route_projection_error_max_m"
        )
        shared_corridor_verified = bool(
            corridor_end_index is not None
            and len(corridor_world) >= 2
            and dense_interval.get("status") == "AVAILABLE"
            and corridor_interval.get("status") == "AVAILABLE"
            and corridor_interval.get("route_projection_monotonic") is True
            and route_progress_injective
            and shared_projection_error is not None
            and float(shared_projection_error)
            + float(observation.calibrated_uncertainty_m)
            < float(observation.lane_clearance_m)
        )
        comparison_world = _world_samples_at_route_progress(
            corridor_world,
            corridor_interval.get("projections", ()),
            current_progress_m=float(observation.current_progress_m),
            end_progress_m=float(shared_end_progress_m),
            spacing_m=tolerance,
        )
        route_continuity = bool(
            representation_valid
            and interval.get("status") == "AVAILABLE"
            and projection_error is not None
            and interval.get("route_projection_monotonic") is True
            and shared_corridor_verified
            and len(comparison_world) >= 2
        )
        projected_interval = (
            min(float(start), float(observation.current_progress_m)),
            float(end),
        ) if start_aligned and end is not None else (
            float(start or 0.0), float(end or 0.0)
        )
        coverage = DecisionWindowEvidenceAdapter.plan_coverage(
            candidate_id=plan_id,
            route_version_id=observation.route_version,
            current_progress_m=float(observation.current_progress_m),
            required_end_progress_m=float(shared_end_progress_m),
            projected_interval_m=projected_interval,
            plan_arc_length_m=_arc(valid_rows),
            transform_uncertainty_m=(
                float(observation.calibrated_uncertainty_m)
                + float(projection_error or 0.0)
            ),
            discretization_uncertainty_m=tolerance,
            execution_latency_distance_m=float(
                observation.execution_latency_distance_m
            ),
            alignment_verified=bool(
                source_alignment_verified
                and observation.alignment_verified
                and start_aligned
            ),
            route_continuity_verified=route_continuity,
            provenance=provenance,
        )
        diagnostics = {
            "model_coordinate_contract_id": observation.model_coordinate_contract_id,
            "expected_plan_point_count": expected_count,
            "observed_plan_point_count": len(rows),
            "valid_mask": valid_mask,
            "segment_valid_mask": list(segment_valid_mask),
            "valid_prefix_point_count": valid_count,
            "expected_checkpoint_spacing_m": expected,
            "checkpoint_spacing_tolerance_m": tolerance,
            "local_plan_arc_length_m": _arc(valid_rows),
            "carla_world_xy_m": [list(row) for row in world],
            "directed_route_projection": interval,
            "dense_plan_world_xy_m": [list(row) for row in dense_world],
            "dense_directed_route_projection": dense_interval,
            "shared_window_and_margin_world_xy_m": [
                list(row) for row in corridor_world
            ],
            "shared_window_and_margin_directed_route_projection": (
                corridor_interval
            ),
            "shared_window_comparison_world_xy_m": [
                list(row) for row in comparison_world
            ],
            "projection_error_bound_m": projection_error,
            "shared_window_projection_error_bound_m": shared_projection_error,
            "lane_clearance_m": observation.lane_clearance_m,
            "shared_window_corridor_verified": shared_corridor_verified,
            "shared_window_route_progress_injective": (
                route_progress_injective
            ),
            "shared_window_route_progress_deltas_m": list(
                corridor_progress_deltas
            ),
            "local_plan_origin_error_m": math.hypot(
                valid_rows[0][0], valid_rows[0][1]
            ),
            "world_plan_origin_to_ego_error_m": origin_world_error,
            "calibrated_transform_uncertainty_m": (
                observation.calibrated_uncertainty_m
            ),
            "effective_transform_and_projection_uncertainty_m": (
                float(observation.calibrated_uncertainty_m)
                + float(projection_error or 0.0)
            ),
            "discretization_uncertainty_m": tolerance,
            "execution_latency_distance_m": observation.execution_latency_distance_m,
            "required_shared_window_distance_m": shared_window_distance_m,
            "required_shared_end_progress_m": shared_end_progress_m,
            "required_corridor_end_with_margins_progress_m": (
                corridor_required_end
            ),
            "corridor_required_end_reached": corridor_end_index is not None,
            "route_continuity_verified": route_continuity,
            "start_alignment_verified": start_aligned,
            "source_alignment_verified": source_alignment_verified,
        }
        return coverage, world, diagnostics
    except (IndexError, TypeError, ValueError):
        coverage = DecisionWindowEvidenceAdapter.plan_coverage(
            candidate_id=plan_id,
            route_version_id=observation.route_version,
            current_progress_m=float(observation.current_progress_m),
            required_end_progress_m=float(shared_end_progress_m),
            projected_interval_m=(0.0, 0.0),
            plan_arc_length_m=0.0,
            transform_uncertainty_m=float(observation.calibrated_uncertainty_m),
            discretization_uncertainty_m=float(
                observation.plan_checkpoint_spacing_tolerance_m
            ),
            execution_latency_distance_m=float(
                observation.execution_latency_distance_m
            ),
            alignment_verified=False,
            route_continuity_verified=False,
            provenance=provenance,
        )
        return coverage, (), {
            "model_coordinate_contract_id": observation.model_coordinate_contract_id,
            "expected_plan_point_count": observation.expected_plan_point_count,
            "observed_plan_point_count": len(local_points),
            "valid_mask": [],
            "route_continuity_verified": False,
            "start_alignment_verified": False,
            "failure": "PLAN_REPRESENTATION_OR_PROJECTION_INVALID",
        }


def evaluate_runtime_decision_window(
    bundle: CandidateEvidenceRefreshBundle,
    observation: RuntimeWindowObservation,
    *,
    target_obligation_digests: Mapping[str, str],
    authorized_plan_points: Sequence[Sequence[float]],
    authorized_plan_digest: str,
    authorized_speed_points: Sequence[Sequence[float]],
    authorized_speed_digest: str,
    route_points: Callable[[Any], Sequence[Sequence[float]]] = _route_points,
    speed_points: Callable[[Any], Sequence[Sequence[float]]] = _speed_points,
) -> RuntimeDecisionWindow:
    reasons = []
    aligned = bool(
        bundle.source_observation_id == observation.source_observation_id
        and str(bundle.source_frame_id) == str(observation.source_frame_id)
        and observation.alignment_verified
    )
    if not bundle.complete:
        reasons.append("REFRESH_BUNDLE_INCOMPLETE")
    if not aligned:
        reasons.append("SOURCE_IDENTITY_NOT_ALIGNED")
    bundle_fresh = bool(
        math.isfinite(float(bundle.latency_seconds))
        and 0.0 <= float(bundle.latency_seconds)
        <= float(observation.runtime_latency_upper_bound_s)
    )
    if not bundle_fresh:
        reasons.append("REFRESH_BUNDLE_LATENCY_BOUND_EXCEEDED")
    numeric = (
        observation.observed_monotonic_time,
        observation.current_progress_m,
        observation.current_speed_mps,
        observation.speed_limit_mps,
        observation.fixed_delta_seconds,
        observation.maximum_normal_planning_interval_s,
        observation.calibrated_uncertainty_m,
        observation.lane_clearance_m,
        observation.maneuver_onset_progress_m,
        observation.runtime_latency_upper_bound_s,
        observation.expected_plan_checkpoint_spacing_m,
        observation.plan_checkpoint_spacing_tolerance_m,
        observation.execution_latency_distance_m,
    )
    if not all(math.isfinite(float(value)) for value in numeric):
        reasons.append("RUNTIME_WINDOW_NUMERIC_INPUT_INVALID")
    candidate_ids = tuple(sorted(bundle.requested_candidate_ids))
    if set(candidate_ids) != set(target_obligation_digests):
        reasons.append("TARGET_OBLIGATION_KEYSET_MISMATCH")
    if set(candidate_ids) != set(observation.candidate_commitment_progress_m):
        reasons.append("COMMITMENT_KEYSET_MISMATCH")

    speed_upper = max(float(observation.current_speed_mps), float(observation.speed_limit_mps))
    if speed_upper <= 0.0 or observation.fixed_delta_seconds <= 0.0:
        reasons.append("RUNTIME_SPEED_OR_STEP_BOUND_UNAVAILABLE")
    shared_window_distance = (
        speed_upper * float(observation.maximum_normal_planning_interval_s)
        + float(observation.calibrated_uncertainty_m)
    )
    shared_end = float(observation.current_progress_m) + shared_window_distance
    plans = {row.candidate_id: route_points(row.result) for row in bundle.evidence}
    plans["__AUTHORIZED_PLAN__"] = authorized_plan_points
    expected_plan_ids = set(candidate_ids) | {"__AUTHORIZED_PLAN__"}
    coverage_results = {}
    coverage_diagnostics = {}
    if set(plans) == expected_plan_ids:
        for plan_id, points in plans.items():
            coverage, _world_points, diagnostics = _plan_coverage(
                plan_id,
                points,
                observation,
                shared_window_distance_m=shared_window_distance,
                shared_end_progress_m=shared_end,
                source_alignment_verified=bool(aligned and bundle_fresh),
            )
            coverage_results[plan_id] = coverage
            coverage_diagnostics[plan_id] = diagnostics
            coverage_status = (
                coverage.value.get("coverage_status")
                if isinstance(coverage.value, Mapping)
                else None
            )
            if not (
                coverage.is_runtime_authorizable
                and coverage_status == PlanCoverageStatus.COVERED.value
            ):
                safe_plan_id = "".join(
                    value if value.isalnum() else "_" for value in plan_id
                ).strip("_")
                reasons.append(
                    "PLAN_COVERAGE_"
                    + safe_plan_id.upper()
                    + "_"
                    + str(coverage.reason_code or coverage_status or "UNKNOWN")
                )
    full_coverage = bool(
        set(coverage_results) == expected_plan_ids
        and all(
            result.is_runtime_authorizable
            and isinstance(result.value, Mapping)
            and result.value.get("coverage_status")
            == PlanCoverageStatus.COVERED.value
            for result in coverage_results.values()
        )
    )
    comparison_plans = {}
    comparison_route_progress_targets = []
    if full_coverage:
        target_values = {
            float(observation.current_progress_m),
            float(shared_end),
        }
        for diagnostics in coverage_diagnostics.values():
            projection = diagnostics.get(
                "shared_window_and_margin_directed_route_projection", {}
            )
            target_values.update(
                float(row["progress_m"])
                for row in projection.get("projections", ())
                if float(observation.current_progress_m) - 1e-9
                <= float(row["progress_m"])
                <= float(shared_end) + 1e-9
            )
        comparison_route_progress_targets = sorted(target_values)
        for plan_id, diagnostics in coverage_diagnostics.items():
            projection = diagnostics[
                "shared_window_and_margin_directed_route_projection"
            ]
            comparison_plans[plan_id] = _world_samples_for_route_targets(
                diagnostics["shared_window_and_margin_world_xy_m"],
                projection.get("projections", ()),
                comparison_route_progress_targets,
            )
            diagnostics["shared_window_comparison_route_progress_targets_m"] = (
                comparison_route_progress_targets
            )
            diagnostics["shared_window_comparison_world_xy_m"] = [
                list(row) for row in comparison_plans[plan_id]
            ]
    deviation = (
        _pairwise_prefix_deviation(comparison_plans, math.inf)
        if full_coverage
        and set(comparison_plans) == expected_plan_ids
        and all(
            len(points) == len(comparison_route_progress_targets)
            for points in comparison_plans.values()
        )
        else None
    )
    speed_plans = {row.candidate_id: speed_points(row.result) for row in bundle.evidence}
    speed_plans["__AUTHORIZED_PLAN__"] = authorized_speed_points
    speed_deviation = _pairwise_fixed_time_deviation(speed_plans)
    longitudinal_reference_equivalent = bool(
        set(speed_plans) == set(candidate_ids) | {"__AUTHORIZED_PLAN__"}
        and speed_deviation is not None
        and speed_deviation <= float(observation.calibrated_uncertainty_m)
    )
    current_equivalent = bool(
        deviation is not None
        and longitudinal_reference_equivalent
        and deviation + float(observation.calibrated_uncertainty_m)
        < float(observation.lane_clearance_m)
        and shared_end < float(observation.maneuver_onset_progress_m)
    )
    commitments = [float(observation.candidate_commitment_progress_m[row]) for row in candidate_ids]
    future_divergent = bool(
        len(set(target_obligation_digests.values())) >= 2
        and commitments
        and max(commitments) - min(commitments)
        > float(observation.calibrated_uncertainty_m)
        and float(observation.maneuver_onset_progress_m)
        > float(observation.current_progress_m)
    )
    recoverable = bool(
        commitments
        and shared_end + float(observation.calibrated_uncertainty_m) < min(commitments)
        and observation.dynamic_safety_gate
        and observation.hard_rule_gate
    )
    distance_to_commitment = (
        min(commitments)
        - float(observation.current_progress_m)
        - float(observation.calibrated_uncertainty_m)
    ) if commitments else -1.0
    ttd_lower = distance_to_commitment / speed_upper if speed_upper > 0.0 else None
    slack = (
        ttd_lower - float(observation.runtime_latency_upper_bound_s)
        if ttd_lower is not None
        else None
    )
    latest_safe = (
        float(observation.observed_monotonic_time) + slack
        if slack is not None and slack > 0.0
        else None
    )
    if not full_coverage:
        reasons.append("PLAN_COVERAGE_UNKNOWN")
    if not current_equivalent:
        reasons.append("CURRENT_ACTION_EQUIVALENCE_UNKNOWN")
    if not longitudinal_reference_equivalent:
        reasons.append("LONGITUDINAL_REFERENCE_EQUIVALENCE_UNKNOWN")
    if not recoverable:
        reasons.append("RECOVERABILITY_UNKNOWN")
    if latest_safe is None:
        reasons.append("QUERY_DEADLINE_UNKNOWN")

    relationship = (
        "CURRENTLY_EQUIVALENT_FUTURE_DIVERGENT"
        if current_equivalent and future_divergent
        else "CURRENTLY_DIVERGENT"
        if not current_equivalent and future_divergent and full_coverage
        else "NO_MATERIAL_DIVERGENCE"
        if current_equivalent and not future_divergent
        else "UNKNOWN_OR_INSUFFICIENT_EVIDENCE"
    )
    current_relation = (
        "CURRENT_ACTION_EQUIVALENT" if current_equivalent else "UNKNOWN"
    )
    future_relation = "FUTURE_DIVERGENT" if future_divergent else "NO_MATERIAL_DIVERGENCE"
    evidence_payload = {
        "bundle_id": bundle.bundle_id,
        "bundle_generation_latency_seconds": bundle.latency_seconds,
        "bundle_generation_latency_bound_seconds": (
            observation.runtime_latency_upper_bound_s
        ),
        "bundle_source_alignment_verified": bool(aligned and bundle_fresh),
        "source_observation_id": observation.source_observation_id,
        "source_frame_id": observation.source_frame_id,
        "route_version": observation.route_version,
        "current_progress_m": observation.current_progress_m,
        "shared_action_end_progress_m": shared_end,
        "candidate_plan_digests": [row.plan_reference_digest for row in bundle.evidence],
        "authorized_plan_digest": authorized_plan_digest,
        "candidate_speed_digests": [row.speed_digest for row in bundle.evidence],
        "authorized_speed_digest": authorized_speed_digest,
        "maximum_pairwise_prefix_deviation_m": deviation,
        "maximum_fixed_time_speed_reference_deviation_m": speed_deviation,
        "lane_clearance_m": observation.lane_clearance_m,
        "calibrated_uncertainty_m": observation.calibrated_uncertainty_m,
        "plan_coverage_evidence": {
            plan_id: {
                "result": result.to_dict(),
                "projection_and_validity": coverage_diagnostics[plan_id],
            }
            for plan_id, result in sorted(coverage_results.items())
        },
    }
    decision_payload = {
        **evidence_payload,
        "target_obligation_digests": dict(sorted(target_obligation_digests.items())),
        "maneuver_onset_progress_m": observation.maneuver_onset_progress_m,
        "candidate_commitment_progress_m": dict(observation.candidate_commitment_progress_m),
        "recoverability": "RECOVERABLE" if recoverable else "UNKNOWN",
        "latest_safe_clarification_monotonic": latest_safe,
        "relationship": relationship,
    }
    eligible = bool(
        bundle.complete
        and aligned
        and full_coverage
        and current_equivalent
        and future_divergent
        and recoverable
        and latest_safe is not None
        and not reasons
    )
    valid_until = (
        min(
            latest_safe,
            float(observation.observed_monotonic_time)
            + float(observation.maximum_normal_planning_interval_s),
        )
        if eligible and latest_safe is not None
        else None
    )
    return RuntimeDecisionWindow(
        status="AVAILABLE" if eligible else "UNKNOWN",
        source_observation_id=observation.source_observation_id,
        source_frame_id=observation.source_frame_id,
        route_version=observation.route_version,
        environment_digest=observation.environment_digest,
        current_progress_m=float(observation.current_progress_m),
        shared_action_end_progress_m=shared_end if not reasons or full_coverage else None,
        current_action_relation=current_relation,
        future_obligation_relation=future_relation,
        candidate_relationship=relationship,
        full_plan_coverage=full_coverage,
        recoverability="RECOVERABLE" if recoverable else "UNKNOWN",
        latest_safe_clarification_monotonic=latest_safe,
        latest_safe_slack_s=slack,
        time_to_divergence_lower_bound_s=ttd_lower,
        decision_window_digest=canonical_sha256(decision_payload),
        current_action_equivalence_evidence_digest=canonical_sha256(evidence_payload),
        valid_until_monotonic=valid_until,
        plan_coverage_evidence=evidence_payload["plan_coverage_evidence"],
        reason_codes=tuple(dict.fromkeys(reasons)),
    )


__all__ = [
    "RuntimeDecisionWindow",
    "RuntimeWindowObservation",
    "evaluate_runtime_decision_window",
]
