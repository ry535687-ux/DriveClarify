"""Pure runtime-signal adapter for frozen decision-window evidence.

The adapter converts already-owned runtime observations into typed evaluator
inputs.  It deliberately has no CARLA, SimLingo, planner, PID, authority, or
control dependency.  Live objects must be detached into ordinary mappings and
numeric sequences before crossing this boundary.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence, Tuple

from .evaluators import PlanCoverageInput, evaluate_plan_coverage
from .types import (
    EvidenceProvenance,
    EvidenceResult,
    EvidenceStatus,
    UsagePurpose,
    available_result,
    unknown_result,
)


Point2D = Tuple[float, float]


def _finite(*values: float) -> bool:
    try:
        return all(math.isfinite(float(value)) for value in values)
    except (TypeError, ValueError):
        return False


def _point(value: Sequence[Any]) -> Point2D:
    if len(value) < 2 or not _finite(value[0], value[1]):
        raise ValueError("NON_FINITE_OR_SHORT_POINT")
    return float(value[0]), float(value[1])


def _distance(left: Point2D, right: Point2D) -> float:
    return math.hypot(right[0] - left[0], right[1] - left[1])


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class RouteProjection:
    progress_m: float
    projection_error_m: float
    segment_index: int
    segment_fraction: float
    projected_xy_m: Point2D

    def to_dict(self) -> dict:
        return {
            "status": "AVAILABLE",
            "progress_m": self.progress_m,
            "projection_error_m": self.projection_error_m,
            "segment_index": self.segment_index,
            "segment_fraction": self.segment_fraction,
            "projected_xy_m": list(self.projected_xy_m),
        }


@dataclass(frozen=True)
class RecedingHorizonCalibrationSample:
    """One normal-P1 plan/control pair and its next physical state."""

    frame_id: int
    plan_local_xy_m: Tuple[Point2D, ...]
    current_world_xy_m: Point2D
    next_world_xy_m: Point2D
    ego_forward_xy: Point2D
    ego_right_xy: Point2D
    control_steer: float


@dataclass(frozen=True)
class CoordinateCalibrationInput:
    route_world_xy_m: Tuple[Point2D, ...]
    ego_location_xy_m: Point2D
    map_waypoint_xy_m: Point2D
    ego_forward_xy: Point2D
    ego_right_xy: Point2D
    lane_width_m: float
    vehicle_half_width_m: float
    baseline_plan_local_xy_m: Tuple[Point2D, ...]
    observed_baseline_world_xy_m: Tuple[Point2D, ...]
    route_source: str
    source_frame_id: int
    source_observation_id: str
    model_coordinate_contract_id: Optional[str] = None
    expected_checkpoint_spacing_m: Optional[float] = None
    checkpoint_spacing_tolerance_m: Optional[float] = None
    receding_horizon_samples: Tuple[RecedingHorizonCalibrationSample, ...] = ()


@dataclass(frozen=True)
class CandidateCorridorInput:
    candidate_id: str
    route_version_id: str
    source_candidate_version: str
    start_progress_m: float
    common_prefix_end_progress_m: float
    lane_sequence: Tuple[str, ...]
    executable_reference_verified: bool
    coverage_status: str


@dataclass(frozen=True)
class CommitmentBoundaryInput:
    candidate_id: str
    route_version_id: Optional[str]
    maneuver_onset_progress_m: Optional[float]
    speed_upper_bound_mps: Optional[float]
    turn_speed_upper_bound_mps: Optional[float]
    acceleration_upper_bound_mps2: Optional[float]
    braking_deceleration_lower_bound_mps2: Optional[float]
    planning_model_latency_upper_bound_s: Optional[float]
    m2b_m3_authority_latency_upper_bound_s: Optional[float]
    control_response_latency_upper_bound_s: Optional[float]
    lateral_response_time_upper_bound_s: Optional[float]
    safety_margin_distance_m: Optional[float]
    legal_lane_connectivity: Optional[bool]
    rule_valid: Optional[bool]
    dynamic_path_feasible: Optional[bool]
    source_assumptions: Tuple[str, ...] = ()


class DecisionWindowEvidenceAdapter:
    """Deterministic conversion of detached signals into frozen evidence."""

    implementation_id = "DecisionWindowEvidenceAdapter.v1"

    @staticmethod
    def normalize_route(route_world_xy_m: Sequence[Sequence[Any]]) -> Tuple[Point2D, ...]:
        points = tuple(_point(value) for value in route_world_xy_m)
        if len(points) < 2:
            raise ValueError("ROUTE_POLYLINE_TOO_SHORT")
        if sum(_distance(a, b) for a, b in zip(points, points[1:])) <= 1e-6:
            raise ValueError("ROUTE_POLYLINE_ZERO_LENGTH")
        return points

    @staticmethod
    def route_version_id(route_world_xy_m: Sequence[Sequence[Any]], *, source: str) -> str:
        points = DecisionWindowEvidenceAdapter.normalize_route(route_world_xy_m)
        canonical = {
            "source": source,
            "frame": "CARLA_WORLD",
            "unit": "m",
            "points": [[round(x, 6), round(y, 6)] for x, y in points],
        }
        return "route-world-" + _canonical_sha256(canonical)

    @staticmethod
    def project_to_route(
        point_xy_m: Sequence[Any], route_world_xy_m: Sequence[Sequence[Any]]
    ) -> RouteProjection:
        point = _point(point_xy_m)
        route = DecisionWindowEvidenceAdapter.normalize_route(route_world_xy_m)
        cumulative = 0.0
        best: Optional[Tuple[float, int, RouteProjection]] = None
        for index, (left, right) in enumerate(zip(route, route[1:])):
            dx, dy = right[0] - left[0], right[1] - left[1]
            length_sq = dx * dx + dy * dy
            if length_sq <= 1e-12:
                continue
            fraction = max(
                0.0,
                min(
                    1.0,
                    ((point[0] - left[0]) * dx + (point[1] - left[1]) * dy)
                    / length_sq,
                ),
            )
            projected = (left[0] + fraction * dx, left[1] + fraction * dy)
            error = _distance(point, projected)
            segment_length = math.sqrt(length_sq)
            result = RouteProjection(
                progress_m=cumulative + fraction * segment_length,
                projection_error_m=error,
                segment_index=index,
                segment_fraction=fraction,
                projected_xy_m=projected,
            )
            key = (error, index)
            if best is None or key < (best[0], best[1]):
                best = (error, index, result)
            cumulative += segment_length
        if best is None:
            raise ValueError("ROUTE_PROJECTION_FAILED")
        return best[2]

    @staticmethod
    def model_local_to_world(
        route_local_forward_right_m: Sequence[Sequence[Any]],
        *,
        ego_location_xy_m: Sequence[Any],
        ego_forward_xy: Sequence[Any],
        ego_right_xy: Sequence[Any],
    ) -> Tuple[Point2D, ...]:
        origin = _point(ego_location_xy_m)
        forward = _point(ego_forward_xy)
        right = _point(ego_right_xy)
        if not _finite(*forward, *right):
            raise ValueError("EGO_BASIS_NON_FINITE")
        return tuple(
            (
                origin[0] + forward[0] * _point(value)[0] + right[0] * _point(value)[1],
                origin[1] + forward[1] * _point(value)[0] + right[1] * _point(value)[1],
            )
            for value in route_local_forward_right_m
        )

    @staticmethod
    def project_plan_interval(
        plan_world_xy_m: Sequence[Sequence[Any]],
        route_world_xy_m: Sequence[Sequence[Any]],
    ) -> Mapping[str, Any]:
        projections = tuple(
            DecisionWindowEvidenceAdapter.project_to_route(point, route_world_xy_m)
            for point in plan_world_xy_m
        )
        progress = tuple(value.progress_m for value in projections)
        errors = tuple(value.projection_error_m for value in projections)
        monotonic = all(b + 1e-6 >= a for a, b in zip(progress, progress[1:]))
        return {
            "status": "AVAILABLE" if projections and monotonic else "INVALID",
            "route_progress_start_m": progress[0] if progress else None,
            "route_progress_end_m": progress[-1] if progress else None,
            "route_projection_error_mean_m": (
                sum(errors) / len(errors) if errors else None
            ),
            "route_projection_error_max_m": max(errors) if errors else None,
            "route_projection_monotonic": monotonic,
            "projections": [value.to_dict() for value in projections],
        }

    @staticmethod
    def coordinate_calibration(
        request: CoordinateCalibrationInput,
        *,
        provenance: EvidenceProvenance,
    ) -> EvidenceResult:
        try:
            route = DecisionWindowEvidenceAdapter.normalize_route(request.route_world_xy_m)
            ego_projection = DecisionWindowEvidenceAdapter.project_to_route(
                request.ego_location_xy_m, route
            )
            map_projection = DecisionWindowEvidenceAdapter.project_to_route(
                request.map_waypoint_xy_m, route
            )
            baseline_world = DecisionWindowEvidenceAdapter.model_local_to_world(
                request.baseline_plan_local_xy_m,
                ego_location_xy_m=request.ego_location_xy_m,
                ego_forward_xy=request.ego_forward_xy,
                ego_right_xy=request.ego_right_xy,
            )
        except (TypeError, ValueError) as error:
            return unknown_result(
                "COORDINATE_CALIBRATION_INPUT_INVALID",
                dependencies=(type(error).__name__, str(error)),
                unit="m",
                frame="CARLA_WORLD_TO_ROUTE_DIRECTED_PROGRESS",
                clock_domain="MONOTONIC",
                purpose=UsagePurpose.AUTHORIZATION,
                provenance=provenance,
                status=EvidenceStatus.INVALID,
            )

        numeric = (
            request.lane_width_m,
            request.vehicle_half_width_m,
            *request.ego_forward_xy,
            *request.ego_right_xy,
        )
        if not _finite(*numeric) or request.lane_width_m <= 0 or request.vehicle_half_width_m <= 0:
            return unknown_result(
                "COORDINATE_CALIBRATION_GEOMETRY_INVALID",
                unit="m",
                frame="CARLA_WORLD_TO_ROUTE_DIRECTED_PROGRESS",
                clock_domain="MONOTONIC",
                purpose=UsagePurpose.AUTHORIZATION,
                provenance=provenance,
                status=EvidenceStatus.INVALID,
            )
        forward_norm = math.hypot(*request.ego_forward_xy)
        right_norm = math.hypot(*request.ego_right_xy)
        basis_dot = (
            request.ego_forward_xy[0] * request.ego_right_xy[0]
            + request.ego_forward_xy[1] * request.ego_right_xy[1]
        )
        basis_error = max(abs(forward_norm - 1.0), abs(right_norm - 1.0), abs(basis_dot))
        map_waypoint_error = _distance(request.ego_location_xy_m, request.map_waypoint_xy_m)
        corridor_clearance = request.lane_width_m / 2.0 - request.vehicle_half_width_m
        if corridor_clearance <= 0:
            return unknown_result(
                "COORDINATE_CALIBRATION_NO_POSITIVE_LANE_CLEARANCE",
                unit="m",
                frame="CARLA_WORLD_TO_ROUTE_DIRECTED_PROGRESS",
                clock_domain="MONOTONIC",
                purpose=UsagePurpose.AUTHORIZATION,
                provenance=provenance,
            )
        validation_mode = "LEGACY_FIXED_PLAN_PHYSICAL_RESPONSE"
        receding_diagnostics: Mapping[str, Any] = {}
        if request.receding_horizon_samples:
            validation_mode = "PER_TICK_RECEDING_HORIZON_PHYSICAL_RESPONSE"
            spacing = request.expected_checkpoint_spacing_m
            spacing_tolerance = request.checkpoint_spacing_tolerance_m
            contract_ok = request.model_coordinate_contract_id == (
                "SIMLINGO_CONTROL_PID_CHECKPOINTS_1M_EGO_X_FORWARD_Y_RIGHT"
            )
            receding_input_valid = (
                contract_ok
                and spacing is not None
                and spacing_tolerance is not None
                and _finite(spacing, spacing_tolerance)
                and float(spacing) > 0.0
                and float(spacing_tolerance) >= 0.0
                and len(request.receding_horizon_samples) >= 3
            )
            route_errors = []
            progress_deltas = []
            checkpoint_errors = []
            steering_lateral_products = []
            if receding_input_valid:
                for sample in request.receding_horizon_samples:
                    try:
                        current_projection = DecisionWindowEvidenceAdapter.project_to_route(
                            sample.current_world_xy_m, route
                        )
                        next_projection = DecisionWindowEvidenceAdapter.project_to_route(
                            sample.next_world_xy_m, route
                        )
                        current = _point(sample.current_world_xy_m)
                        following = _point(sample.next_world_xy_m)
                        right = _point(sample.ego_right_xy)
                        _point(sample.ego_forward_xy)
                        plan = tuple(_point(value) for value in sample.plan_local_xy_m)
                        if len(plan) < 3 or not _finite(sample.control_steer):
                            raise ValueError("RECEDING_HORIZON_SAMPLE_INCOMPLETE")
                    except (TypeError, ValueError):
                        receding_input_valid = False
                        break
                    route_errors.extend(
                        (
                            current_projection.projection_error_m,
                            next_projection.projection_error_m,
                        )
                    )
                    progress_deltas.append(
                        next_projection.progress_m - current_projection.progress_m
                    )
                    for left, right_plan in zip(plan[:4], plan[1:4]):
                        checkpoint_errors.append(
                            abs(_distance(left, right_plan) - float(spacing))
                        )
                    dx = following[0] - current[0]
                    dy = following[1] - current[1]
                    lateral_displacement = dx * right[0] + dy * right[1]
                    if abs(float(sample.control_steer)) > 1e-6 and abs(lateral_displacement) > 1e-6:
                        steering_lateral_products.append(
                            float(sample.control_steer) * lateral_displacement
                        )
            tracking_max = max(route_errors) if route_errors else None
            receding_diagnostics = {
                "receding_horizon_pair_count": len(request.receding_horizon_samples),
                "checkpoint_spacing_error_max_m": (
                    max(checkpoint_errors) if checkpoint_errors else None
                ),
                "physical_route_projection_error_max_m": tracking_max,
                "physical_route_progress_delta_total_m": sum(progress_deltas),
                "physical_route_progress_delta_min_m": (
                    min(progress_deltas) if progress_deltas else None
                ),
                "steering_lateral_response_pair_count": len(steering_lateral_products),
                "steering_lateral_response_correlation_sum": sum(
                    steering_lateral_products
                ),
                "model_coordinate_contract_id": request.model_coordinate_contract_id,
                "expected_checkpoint_spacing_m": spacing,
                "checkpoint_spacing_tolerance_m": spacing_tolerance,
                "frozen_plan_future_tracking_comparison_used": False,
            }
            receding_valid = (
                receding_input_valid
                and tracking_max is not None
                and tracking_max < corridor_clearance
                and bool(progress_deltas)
                and min(progress_deltas) >= -corridor_clearance
                and sum(progress_deltas) > 0.0
                and bool(checkpoint_errors)
                and max(checkpoint_errors) <= float(spacing_tolerance)
                and len(steering_lateral_products) >= 3
                and sum(steering_lateral_products) > 0.0
            )
        else:
            tracking_errors = tuple(
                min(_distance(_point(actual), predicted) for predicted in baseline_world)
                for actual in request.observed_baseline_world_xy_m
            ) if baseline_world and request.observed_baseline_world_xy_m else ()
            tracking_max = max(tracking_errors) if tracking_errors else None
            receding_valid = False
        reasons = []
        if request.route_source != "AGENT_OWNED_DENSE_CARLA_WORLD_ROUTE":
            reasons.append("ROUTE_SOURCE_NOT_DENSE_CARLA_WORLD")
        if ego_projection.projection_error_m >= corridor_clearance:
            reasons.append("ROUTE_REFERENCE_NOT_EGO_LANE_CONSISTENT")
        if map_projection.projection_error_m >= corridor_clearance:
            reasons.append("ROUTE_REFERENCE_NOT_MAP_WAYPOINT_LANE_CONSISTENT")
        if map_waypoint_error >= max(0.25, corridor_clearance):
            reasons.append("EGO_MAP_WAYPOINT_BINDING_ERROR_EXCESSIVE")
        if basis_error > 1e-3:
            reasons.append("EGO_BASIS_NOT_ORTHONORMAL")
        if request.receding_horizon_samples:
            if not receding_valid:
                reasons.append("RECEDING_HORIZON_SIGN_OR_SCALE_NOT_PHYSICALLY_VALIDATED")
        elif tracking_max is None:
            reasons.append("BASELINE_PHYSICAL_RESPONSE_NOT_CAPTURED")
        elif tracking_max >= corridor_clearance:
            reasons.append("MODEL_LOCAL_SIGN_OR_SCALE_NOT_PHYSICALLY_VALIDATED")
        if reasons:
            return unknown_result(
                reasons[0],
                dependencies=tuple(reasons),
                unit="m",
                frame="CARLA_WORLD_TO_ROUTE_DIRECTED_PROGRESS",
                clock_domain="MONOTONIC",
                purpose=UsagePurpose.AUTHORIZATION,
                provenance=provenance,
            )
        uncertainty = max(
            ego_projection.projection_error_m,
            map_projection.projection_error_m,
            map_waypoint_error,
            float(tracking_max),
            float(receding_diagnostics.get("checkpoint_spacing_error_max_m") or 0.0),
        )
        route_version = DecisionWindowEvidenceAdapter.route_version_id(
            route, source=request.route_source
        )
        return available_result(
            {
                "route_version_id": route_version,
                "route_source": request.route_source,
                "ego_route_progress_m": ego_projection.progress_m,
                "ego_route_projection_error_m": ego_projection.projection_error_m,
                "map_waypoint_route_projection_error_m": map_projection.projection_error_m,
                "ego_map_waypoint_error_m": map_waypoint_error,
                "basis_orthonormal_error": basis_error,
                "baseline_tracking_error_max_m": tracking_max,
                "calibrated_transform_uncertainty_m": uncertainty,
                "lane_corridor_clearance_m": corridor_clearance,
                "source_frame_id": request.source_frame_id,
                "source_observation_id": request.source_observation_id,
                "validation_mode": validation_mode,
                **receding_diagnostics,
            },
            unit="m",
            frame="CARLA_WORLD_TO_ROUTE_DIRECTED_PROGRESS",
            clock_domain="MONOTONIC",
            purpose=UsagePurpose.AUTHORIZATION,
            provenance=provenance,
            dependencies=(
                "agent_owned_dense_world_route",
                "same_frame_ego_map_waypoint",
                "orthonormal_ego_basis",
                "per_tick_physical_response" if request.receding_horizon_samples else "baseline_physical_response",
            ),
        )

    @staticmethod
    def shared_corridor(
        rows: Sequence[CandidateCorridorInput],
        *,
        provenance: EvidenceProvenance,
    ) -> EvidenceResult:
        if len(rows) < 2:
            return unknown_result(
                "SHARED_CORRIDOR_REQUIRES_MULTIPLE_CANDIDATES",
                unit="m_route", frame="ROUTE_DIRECTED_PROGRESS",
                clock_domain="MONOTONIC", purpose=UsagePurpose.AUTHORIZATION,
                provenance=provenance,
            )
        route_versions = {row.route_version_id for row in rows}
        starts = {round(row.start_progress_m, 6) for row in rows}
        if len(route_versions) != 1 or len(starts) != 1:
            return unknown_result(
                "SHARED_CORRIDOR_ROUTE_OR_ORIGIN_MISMATCH",
                unit="m_route", frame="ROUTE_DIRECTED_PROGRESS",
                clock_domain="MONOTONIC", purpose=UsagePurpose.AUTHORIZATION,
                provenance=provenance,
            )
        if any(
            not row.executable_reference_verified
            or row.coverage_status != "COVERED"
            or not row.lane_sequence
            for row in rows
        ):
            return unknown_result(
                "SHARED_CORRIDOR_EXECUTABLE_REFERENCE_OR_COVERAGE_UNKNOWN",
                unit="m_route", frame="ROUTE_DIRECTED_PROGRESS",
                clock_domain="MONOTONIC", purpose=UsagePurpose.AUTHORIZATION,
                provenance=provenance,
            )
        common_lane_prefix = []
        for lane_rows in zip(*(row.lane_sequence for row in rows)):
            if len(set(lane_rows)) != 1:
                break
            common_lane_prefix.append(lane_rows[0])
        start = float(rows[0].start_progress_m)
        end = min(float(row.common_prefix_end_progress_m) for row in rows)
        if not common_lane_prefix or not _finite(start, end) or end <= start:
            return unknown_result(
                "SHARED_CORRIDOR_NO_POSITIVE_COMMON_EXECUTABLE_INTERVAL",
                unit="m_route", frame="ROUTE_DIRECTED_PROGRESS",
                clock_domain="MONOTONIC", purpose=UsagePurpose.AUTHORIZATION,
                provenance=provenance,
            )
        return available_result(
            {
                "start_progress_m": start,
                "end_progress_m": end,
                "coverage_status": "COVERED",
                "common_lane_sequence": common_lane_prefix,
                "source_candidate_versions": {
                    row.candidate_id: row.source_candidate_version for row in rows
                },
                "route_version_id": rows[0].route_version_id,
            },
            unit="m_route", frame="ROUTE_DIRECTED_PROGRESS",
            clock_domain="MONOTONIC", purpose=UsagePurpose.AUTHORIZATION,
            provenance=provenance,
            dependencies=("candidate_executable_references", "lane_topology_prefix"),
        )

    @staticmethod
    def commitment_boundary(
        request: CommitmentBoundaryInput,
        *,
        provenance: EvidenceProvenance,
    ) -> EvidenceResult:
        required = {
            "route_version": request.route_version_id,
            "maneuver_onset": request.maneuver_onset_progress_m,
            "speed_upper": request.speed_upper_bound_mps,
            "turn_speed_upper": request.turn_speed_upper_bound_mps,
            "acceleration_upper": request.acceleration_upper_bound_mps2,
            "planning_model_latency_upper": request.planning_model_latency_upper_bound_s,
            "m2b_m3_authority_latency_upper": request.m2b_m3_authority_latency_upper_bound_s,
            "control_response_latency_upper": request.control_response_latency_upper_bound_s,
            "lateral_response_time_upper": request.lateral_response_time_upper_bound_s,
            "safety_margin_distance": request.safety_margin_distance_m,
            "legal_lane_connectivity": request.legal_lane_connectivity,
            "rule_valid": request.rule_valid,
            "dynamic_path_feasible": request.dynamic_path_feasible,
        }
        missing = tuple(key for key, value in required.items() if value is None)
        if missing:
            return unknown_result(
                "COMMITMENT_BOUNDARY_DEPENDENCY_UNKNOWN",
                dependencies=missing,
                unit="m_route", frame="ROUTE_DIRECTED_PROGRESS",
                clock_domain="MONOTONIC", purpose=UsagePurpose.AUTHORIZATION,
                provenance=provenance,
            )
        numeric = tuple(
            float(value) for key, value in required.items()
            if key not in {"route_version", "legal_lane_connectivity", "rule_valid", "dynamic_path_feasible"}
        )
        if not _finite(*numeric) or any(value < 0 for value in numeric):
            return unknown_result(
                "COMMITMENT_BOUNDARY_NUMERIC_INPUT_INVALID",
                unit="m_route", frame="ROUTE_DIRECTED_PROGRESS",
                clock_domain="MONOTONIC", purpose=UsagePurpose.AUTHORIZATION,
                provenance=provenance, status=EvidenceStatus.INVALID,
            )
        if not (
            request.legal_lane_connectivity
            and request.rule_valid
            and request.dynamic_path_feasible
        ):
            return unknown_result(
                "COMMITMENT_BOUNDARY_LEGAL_DYNAMIC_PATH_NOT_PROVEN",
                dependencies=("legal_lane_connectivity", "rule_valid", "dynamic_path_feasible"),
                unit="m_route", frame="ROUTE_DIRECTED_PROGRESS",
                clock_domain="MONOTONIC", purpose=UsagePurpose.AUTHORIZATION,
                provenance=provenance,
            )
        speed = float(request.speed_upper_bound_mps)
        turn_speed = float(request.turn_speed_upper_bound_mps)
        braking_required = speed > turn_speed
        if braking_required:
            if request.braking_deceleration_lower_bound_mps2 is None:
                return unknown_result(
                    "COMMITMENT_BOUNDARY_BRAKING_ENVELOPE_UNKNOWN",
                    unit="m_route", frame="ROUTE_DIRECTED_PROGRESS",
                    clock_domain="MONOTONIC", purpose=UsagePurpose.AUTHORIZATION,
                    provenance=provenance,
                )
            deceleration = float(request.braking_deceleration_lower_bound_mps2)
            if not _finite(deceleration) or deceleration <= 0:
                return unknown_result(
                    "COMMITMENT_BOUNDARY_BRAKING_ENVELOPE_INVALID",
                    unit="m_route", frame="ROUTE_DIRECTED_PROGRESS",
                    clock_domain="MONOTONIC", purpose=UsagePurpose.AUTHORIZATION,
                    provenance=provenance, status=EvidenceStatus.INVALID,
                )
        else:
            deceleration = None
        latency = sum(
            (
                float(request.planning_model_latency_upper_bound_s),
                float(request.m2b_m3_authority_latency_upper_bound_s),
                float(request.control_response_latency_upper_bound_s),
            )
        )
        acceleration = float(request.acceleration_upper_bound_mps2)
        response_distance = speed * latency + 0.5 * acceleration * latency * latency
        braking_distance = (
            (speed * speed - turn_speed * turn_speed) / (2.0 * deceleration)
            if braking_required and deceleration is not None
            else 0.0
        )
        lateral_distance = speed * float(request.lateral_response_time_upper_bound_s)
        required_distance = (
            response_distance
            + braking_distance
            + lateral_distance
            + float(request.safety_margin_distance_m)
        )
        boundary = float(request.maneuver_onset_progress_m) - required_distance
        return available_result(
            {
                "candidate_id": request.candidate_id,
                "route_version_id": request.route_version_id,
                "progress_m": boundary,
                "uncertainty_m": float(request.safety_margin_distance_m),
                "maneuver_onset_progress_m": float(request.maneuver_onset_progress_m),
                "required_switch_distance_m": required_distance,
                "response_distance_m": response_distance,
                "braking_distance_m": braking_distance,
                "braking_required_by_speed_envelope": braking_required,
                "lateral_response_distance_m": lateral_distance,
                "latency_upper_bound_s": latency,
                "bound_type": "VERIFIED_CONSERVATIVE_BOUND",
                "source_assumptions": list(request.source_assumptions),
            },
            unit="m_route", frame="ROUTE_DIRECTED_PROGRESS",
            clock_domain="MONOTONIC", purpose=UsagePurpose.AUTHORIZATION,
            provenance=provenance,
            dependencies=(
                "candidate_topology", "maneuver_onset", "speed_envelope",
                "braking_envelope", "lateral_response", "runtime_latency",
                "control_response", "safety_margin",
            ),
        )

    @staticmethod
    def plan_coverage(
        *,
        candidate_id: str,
        route_version_id: str,
        current_progress_m: float,
        required_end_progress_m: float,
        projected_interval_m: Tuple[float, float],
        plan_arc_length_m: float,
        transform_uncertainty_m: float,
        discretization_uncertainty_m: float,
        execution_latency_distance_m: float,
        alignment_verified: bool,
        route_continuity_verified: bool,
        provenance: EvidenceProvenance,
    ) -> EvidenceResult:
        return evaluate_plan_coverage(
            PlanCoverageInput(
                claim_id="shared-window-coverage-" + candidate_id,
                required_endpoint_id="shared-action-window-end",
                required_endpoint_type="SHARED_ACTION_WINDOW_END",
                route_version_id=route_version_id,
                plan_route_version_id=route_version_id,
                plan_frame="ROUTE_DIRECTED_PROGRESS",
                plan_unit="m_route",
                current_progress_m=current_progress_m,
                required_end_progress_m=required_end_progress_m,
                valid_intervals_m=(projected_interval_m,),
                plan_arc_length_m=plan_arc_length_m,
                transform_uncertainty_m=transform_uncertainty_m,
                discretization_uncertainty_m=discretization_uncertainty_m,
                execution_latency_distance_m=execution_latency_distance_m,
                alignment_verified=alignment_verified,
                route_continuity_verified=route_continuity_verified,
                provenance=provenance,
            )
        )

    @staticmethod
    def replay_b0_r1(live: Mapping[str, Any], *, provenance: EvidenceProvenance) -> Mapping[str, Any]:
        """Parse B0-R1 deterministically while preserving its invalid binding."""

        samples = tuple(
            row for row in live.get("physical_runtime_samples", ())
            if row.get("status") == "AVAILABLE"
        )
        source_frame = live.get("plan_source_frame")
        exact = tuple(row for row in samples if row.get("hook_frame") == source_frame)
        route = tuple(
            _point(value)
            for value in live.get("topology_context", {}).get("route_polyline_world", ())
        )
        if len(exact) != 1 or len(route) < 2:
            result = unknown_result(
                "B0_R1_REPLAY_SOURCE_OR_ROUTE_MISSING",
                unit="m", frame="CARLA_WORLD_TO_ROUTE_DIRECTED_PROGRESS",
                clock_domain="MONOTONIC", purpose=UsagePurpose.AUTHORIZATION,
                provenance=provenance,
            )
            return {"status": result.status.value, "coordinate_calibration": result.to_dict()}
        source = exact[0]
        ego = source.get("ego", {})
        waypoint = source.get("map_waypoint", {})
        baseline_actual = tuple(
            _point(row["ego"]["location_xyz"])
            for row in samples
            if row.get("hook_frame", -1) >= source_frame
            and isinstance(row.get("ego", {}).get("location_xyz"), list)
        )
        request = CoordinateCalibrationInput(
            route_world_xy_m=route,
            ego_location_xy_m=_point(ego.get("location_xyz", ())),
            map_waypoint_xy_m=_point(waypoint.get("waypoint_location_xyz", ())),
            ego_forward_xy=_point(ego.get("forward_vector_xyz", ())),
            ego_right_xy=_point(ego.get("right_vector_xyz", ())),
            lane_width_m=float(waypoint.get("lane_width")),
            vehicle_half_width_m=float(ego.get("bbox", {}).get("extent_xyz", (None, None))[1]),
            baseline_plan_local_xy_m=tuple(
                _point(value) for value in live.get("baseline_route_at_source", ())
            ),
            observed_baseline_world_xy_m=baseline_actual,
            route_source="GPS_CONVERTED_ROUTE_DEQUE_B0_R1",
            source_frame_id=int(source_frame),
            source_observation_id=str(live.get("plan_source_observation_id")),
        )
        result = DecisionWindowEvidenceAdapter.coordinate_calibration(
            request, provenance=provenance
        )
        return {
            "status": "PASS_DETERMINISTIC_REPLAY_PARSE_FAIL_CLOSED",
            "coordinate_calibration": result.to_dict(),
            "old_route_source_promoted": False,
            "authorization_eligible": False,
        }


__all__ = [
    "CandidateCorridorInput",
    "CommitmentBoundaryInput",
    "CoordinateCalibrationInput",
    "DecisionWindowEvidenceAdapter",
    "RecedingHorizonCalibrationSample",
    "RouteProjection",
]
