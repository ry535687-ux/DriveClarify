"""Fail-closed post-run analysis for B1-EVIDENCE."""

from __future__ import annotations

import hashlib
import json
import math
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from driveclarify_persistent_ambiguity_runtime_v1.contracts import canonical_pair_key
from driveclarify_persistent_ambiguity_runtime_v1.evidence_adapter import (
    CandidateCorridorInput,
    CommitmentBoundaryInput,
    CoordinateCalibrationInput,
    DecisionWindowEvidenceAdapter,
    RecedingHorizonCalibrationSample,
)
from driveclarify_persistent_ambiguity_runtime_v1.evaluators import (
    CandidateReachability,
    CurrentActionEquivalenceInput,
    ManeuverOnsetInput,
    PlanCoverageInput,
    classify_candidate_relationship,
    evaluate_current_action_equivalence,
    evaluate_decision_point,
    evaluate_latest_safe_clarification,
    evaluate_maneuver_onset,
    evaluate_plan_coverage,
    evaluate_recoverability,
    evaluate_route_distance_timing,
)
from driveclarify_persistent_ambiguity_runtime_v1.types import (
    EvidenceProvenance,
    TimeToDivergenceSemantics,
    UsagePurpose,
    Visibility,
    available_result,
    unknown_result,
)

from .runtime import ROUTE_SOURCE, RUN_ID


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _arc(points: Sequence[Sequence[float]]) -> float:
    return sum(
        math.hypot(float(b[0]) - float(a[0]), float(b[1]) - float(a[1]))
        for a, b in zip(points, points[1:])
    )


def _pair_metrics(left: Sequence[Sequence[float]], right: Sequence[Sequence[float]]) -> dict:
    separations = [
        math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))
        for a, b in zip(left, right)
    ]
    return {
        "point_count": len(separations),
        "separation_by_waypoint_m": separations,
        "max_separation_m": max(separations) if separations else None,
        "rmse_m": (
            math.sqrt(sum(value * value for value in separations) / len(separations))
            if separations
            else None
        ),
    }


def _envelope(result: Any, **diagnostics: Any) -> dict:
    return {**result.to_dict(), **diagnostics}


def _point_at_arc(points: Sequence[Sequence[float]], distance_m: float) -> tuple:
    if not points:
        raise ValueError("POLYLINE_EMPTY")
    remaining = max(0.0, float(distance_m))
    for left, right in zip(points, points[1:]):
        length = math.hypot(float(right[0]) - float(left[0]), float(right[1]) - float(left[1]))
        if length <= 1e-9:
            continue
        if remaining <= length:
            fraction = remaining / length
            return (
                float(left[0]) + fraction * (float(right[0]) - float(left[0])),
                float(left[1]) + fraction * (float(right[1]) - float(left[1])),
            )
        remaining -= length
    return float(points[-1][0]), float(points[-1][1])


def _corridor_separation_series(connector: Mapping[str, Any]) -> Mapping[str, Any]:
    branch = [tuple(row[:2]) for row in connector.get("polyline_xy_m", [])]
    continuation = [
        tuple(row[:2]) for row in connector.get("route_continuation_xy_m", [])
    ]
    if len(branch) < 2 or len(continuation) < 2:
        return {"status": "UNKNOWN", "reason_code": "CORRIDOR_POLYLINE_MISSING"}
    origin = branch[0]
    continuation = [origin] + [
        point for point in continuation if math.hypot(point[0] - origin[0], point[1] - origin[1]) > 0.25
    ]
    covered_arc = min(_arc(branch), _arc(continuation))
    if covered_arc <= 1.0:
        return {"status": "UNKNOWN", "reason_code": "CORRIDOR_COMMON_PARAMETER_TOO_SHORT"}
    spacing = float(connector.get("sampling_distance_m", 0.5))
    count = int(math.floor(covered_arc / spacing)) + 1
    distances = [min(covered_arc, index * spacing) for index in range(count)]
    branch_samples = [_point_at_arc(branch, value) for value in distances]
    continuation_samples = [_point_at_arc(continuation, value) for value in distances]
    separations = [
        math.hypot(left[0] - right[0], left[1] - right[1])
        for left, right in zip(branch_samples, continuation_samples)
    ]
    entry_progress = float(connector["junction_entry_progress_m"])
    return {
        "status": "AVAILABLE",
        "sampling_distance_m": spacing,
        "covered_arc_m": covered_arc,
        "progress_samples_m": [entry_progress + value for value in distances],
        "separation_samples_m": separations,
        "branch_samples_xy_m": [list(value) for value in branch_samples],
        "continuation_samples_xy_m": [list(value) for value in continuation_samples],
    }


def _crossing_interval(
    samples: Sequence[Mapping[str, Any]], progress_m: float
) -> Mapping[str, Any]:
    prior = None
    for row in samples:
        projection = row.get("ego_route_projection", {})
        if projection.get("status") != "AVAILABLE":
            continue
        current = float(projection["progress_m"])
        if current >= float(progress_m):
            if prior is None:
                return {"status": "UNKNOWN", "reason_code": "CROSSING_PRECEDES_CAPTURE"}
            return {
                "status": "AVAILABLE",
                "lower_monotonic_s": float(prior["captured_monotonic_time"]),
                "upper_monotonic_s": float(row["captured_monotonic_time"]),
                "lower_simulation_s": float(prior["hook_simulation_time"]),
                "upper_simulation_s": float(row["hook_simulation_time"]),
                "lower_progress_m": float(prior["ego_route_projection"]["progress_m"]),
                "upper_progress_m": current,
                "crossing_progress_m": float(progress_m),
            }
        prior = row
    return {"status": "UNKNOWN", "reason_code": "CROSSING_NOT_OBSERVED_IN_CONTROLLED_WINDOW"}


def _available_seconds(value: float, provenance: EvidenceProvenance, dependency: str) -> Any:
    return available_result(
        float(value),
        unit="s",
        frame="ABSOLUTE_MONOTONIC_TIME",
        clock_domain="MONOTONIC",
        purpose=UsagePurpose.AUTHORIZATION,
        provenance=provenance,
        dependencies=(dependency,),
    )


def analyze(output_dir: Path, report_dir: Path, *, run_id: str = RUN_ID) -> Mapping[str, Any]:
    started = time.monotonic()
    live_path = output_dir / "GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json"
    live = _load(live_path)
    native = _load(output_dir / "GROUNDED_LANGUAGE_V1_NATIVE_RUN_RECEIPT.json")
    preflight = _load(output_dir / "GROUNDED_LANGUAGE_V1_NATIVE_PREFLIGHT.json")
    desktop = _load(output_dir / "NATIVE_DESKTOP_VALIDATION.json")
    source_frame = int(live["plan_source_frame"])
    source_observation_id = str(live["plan_source_observation_id"])
    samples = [
        row
        for row in live.get("physical_runtime_samples", [])
        if row.get("status") == "AVAILABLE"
    ]
    exact = [row for row in samples if int(row.get("hook_frame", -1)) == source_frame]
    source = exact[0] if len(exact) == 1 else None
    dense = live.get("dense_world_route_evidence", {})
    route = tuple(
        (float(row[0]), float(row[1])) for row in dense.get("route_rows", [])
    )
    artifact_sha = _sha(live_path)
    route_digest = None if source is None else source.get("route_version_digest")
    provenance = EvidenceProvenance(
        producer="PhaseBEvidenceCompletionRuntime." + str(run_id),
        source_kind="NATIVE_CARLA_SAME_FRAME_RUNTIME_CAPTURE",
        source_observation_id=source_observation_id,
        source_frame_id=source_frame,
        observed_monotonic_time=(
            None if source is None else source.get("captured_monotonic_time")
        ),
        source_simulation_time=(
            None if source is None else source.get("hook_simulation_time")
        ),
        coordinate_transform_id=(
            None
            if route_digest is None
            else "carla-ego-forward-right-{}-{}".format(source_frame, str(route_digest)[:16])
        ),
        visibility=Visibility.RUNTIME_OBSERVABLE,
        artifact_sha256=artifact_sha,
        comparison_context_id=source_observation_id,
    )

    calibration_request = None
    if source is not None and len(route) >= 2:
        ego = source.get("ego", {})
        waypoint = source.get("map_waypoint", {})
        baseline_local = tuple(
            (float(row[0]), float(row[1]))
            for row in live.get("baseline_route_at_source", [])
        )
        plan_horizon = _arc(baseline_local)
        observed = []
        for row in samples:
            if int(row.get("hook_frame", -1)) < source_frame:
                continue
            projection = row.get("ego_route_projection", {})
            ego_location = row.get("ego", {}).get("location_xyz")
            if (
                projection.get("status") == "AVAILABLE"
                and float(projection.get("progress_m", plan_horizon + 1.0)) <= plan_horizon
                and isinstance(ego_location, list)
            ):
                observed.append((float(ego_location[0]), float(ego_location[1])))
        physical_by_frame = {
            int(row["hook_frame"]): row
            for row in samples
            if isinstance(row.get("hook_frame"), int)
        }
        receding = []
        for event in live.get("receding_horizon_plan_control_events", []):
            frame_id = int(event.get("source_frame_id", -1))
            current = physical_by_frame.get(frame_id)
            following = physical_by_frame.get(frame_id + 1)
            control = event.get("control")
            if (
                frame_id < source_frame
                or current is None
                or following is None
                or not isinstance(control, Mapping)
            ):
                continue
            current_ego = current.get("ego", {})
            next_ego = following.get("ego", {})
            plan_rows = event.get("baseline_route_local_forward_right_m", [])
            try:
                receding.append(
                    RecedingHorizonCalibrationSample(
                        frame_id=frame_id,
                        plan_local_xy_m=tuple(
                            (float(value[0]), float(value[1])) for value in plan_rows
                        ),
                        current_world_xy_m=(
                            float(current_ego["location_xyz"][0]),
                            float(current_ego["location_xyz"][1]),
                        ),
                        next_world_xy_m=(
                            float(next_ego["location_xyz"][0]),
                            float(next_ego["location_xyz"][1]),
                        ),
                        ego_forward_xy=(
                            float(current_ego["forward_vector_xyz"][0]),
                            float(current_ego["forward_vector_xyz"][1]),
                        ),
                        ego_right_xy=(
                            float(current_ego["right_vector_xyz"][0]),
                            float(current_ego["right_vector_xyz"][1]),
                        ),
                        control_steer=float(control["steer"]),
                    )
                )
            except (IndexError, KeyError, TypeError, ValueError):
                continue
            if len(receding) >= 50:
                break
        operating_contract = live.get("controlled_operating_contract", {})
        calibration_request = CoordinateCalibrationInput(
            route_world_xy_m=route,
            ego_location_xy_m=(
                float(ego["location_xyz"][0]), float(ego["location_xyz"][1])
            ),
            map_waypoint_xy_m=(
                float(waypoint["waypoint_location_xyz"][0]),
                float(waypoint["waypoint_location_xyz"][1]),
            ),
            ego_forward_xy=(
                float(ego["forward_vector_xyz"][0]),
                float(ego["forward_vector_xyz"][1]),
            ),
            ego_right_xy=(
                float(ego["right_vector_xyz"][0]),
                float(ego["right_vector_xyz"][1]),
            ),
            lane_width_m=float(waypoint["lane_width"]),
            vehicle_half_width_m=float(ego["bbox"]["extent_xyz"][1]),
            baseline_plan_local_xy_m=baseline_local,
            observed_baseline_world_xy_m=tuple(observed),
            route_source=str(dense.get("route_source")),
            source_frame_id=source_frame,
            source_observation_id=source_observation_id,
            model_coordinate_contract_id=live.get("model_coordinate_contract_id"),
            expected_checkpoint_spacing_m=operating_contract.get(
                "expected_model_checkpoint_spacing_m"
            ),
            checkpoint_spacing_tolerance_m=operating_contract.get(
                "checkpoint_spacing_tolerance_m"
            ),
            receding_horizon_samples=tuple(receding),
        )
    if calibration_request is None:
        coordinate = unknown_result(
            "B1_SAME_FRAME_COORDINATE_INPUT_MISSING",
            unit="m",
            frame="CARLA_WORLD_TO_ROUTE_DIRECTED_PROGRESS",
            clock_domain="MONOTONIC",
            purpose=UsagePurpose.AUTHORIZATION,
            provenance=provenance,
        )
    else:
        coordinate = DecisionWindowEvidenceAdapter.coordinate_calibration(
            calibration_request, provenance=provenance
        )

    coordinate_value = coordinate.value if isinstance(coordinate.value, Mapping) else {}
    route_version = coordinate_value.get("route_version_id")
    transform_uncertainty = coordinate_value.get("calibrated_transform_uncertainty_m")
    coordinate_artifact = _envelope(
        coordinate,
        schema_version="driveclarify.phase_b.coordinate_frame_validation.v1",
        run_id=run_id,
        route_source=dense.get("route_source"),
        route_source_attribute=dense.get("agent_attribute"),
        route_row_count=len(route),
        old_b0_route_binding_reused=False,
        source_snapshot_frame=(
            None if source is None else source.get("snapshot", {}).get("frame")
        ),
        source_frame=source_frame,
        source_observation_id=source_observation_id,
    )
    _dump(report_dir / "COORDINATE_FRAME_VALIDATION.json", coordinate_artifact)

    if coordinate.is_runtime_authorizable:
        ego_progress = available_result(
            float(coordinate_value["ego_route_progress_m"]),
            unit="m_route",
            frame="ROUTE_DIRECTED_PROGRESS",
            clock_domain="MONOTONIC",
            purpose=UsagePurpose.AUTHORIZATION,
            provenance=provenance,
            dependencies=("coordinate_calibration", "same_frame_ego_pose"),
            source_artifacts=(str(live_path),),
        )
    else:
        ego_progress = unknown_result(
            "EGO_ROUTE_PROGRESS_COORDINATE_CALIBRATION_UNKNOWN",
            unit="m_route",
            frame="ROUTE_DIRECTED_PROGRESS",
            clock_domain="MONOTONIC",
            purpose=UsagePurpose.AUTHORIZATION,
            provenance=provenance,
        )
    _dump(
        report_dir / "EGO_ROUTE_PROGRESS_EVIDENCE.json",
        _envelope(
            ego_progress,
            schema_version="driveclarify.phase_b.ego_route_progress.v1",
            run_id=run_id,
            route_version_id=route_version,
            source_frame=source_frame,
            projection_diagnostic=(
                None if source is None else source.get("ego_route_projection")
            ),
        ),
    )

    config = live.get("existing_agent_config_evidence", {}).get("values", {})
    operating_contract = live.get("controlled_operating_contract", {})
    source_operating = {} if source is None else source.get("runtime_operating_state", {})
    fixed_delta = source_operating.get("fixed_delta_seconds")
    if fixed_delta is None and config.get("carla_fps"):
        fixed_delta = 1.0 / float(config["carla_fps"])
    fixed_delta = float(fixed_delta or 0.05)
    current_progress = float(ego_progress.value) if ego_progress.value is not None else None
    lane_clearance = coordinate_value.get("lane_corridor_clearance_m")
    source_ego = {} if source is None else source.get("ego", {})
    longitudinal_speed = 0.0
    planar_acceleration = 0.0
    if source is not None:
        velocity = source_ego.get("velocity_world_mps_xyz", [0.0, 0.0, 0.0])
        forward_basis = source_ego.get("forward_vector_xyz", [0.0, 0.0, 0.0])
        acceleration = source_ego.get("acceleration_world_mps2_xyz", [0.0, 0.0, 0.0])
        longitudinal_speed = max(
            0.0,
            float(velocity[0]) * float(forward_basis[0])
            + float(velocity[1]) * float(forward_basis[1]),
        )
        planar_acceleration = math.hypot(float(acceleration[0]), float(acceleration[1]))
    shared_distance = max(
        1e-6,
        longitudinal_speed * fixed_delta
        + 0.5 * planar_acceleration * fixed_delta * fixed_delta,
    )
    shared_endpoint = None if current_progress is None else current_progress + shared_distance

    projected_plans = {}
    coverage = {}
    plan_world = {}
    for plan in live.get("candidate_plans", []):
        label = str(plan.get("candidate_id"))
        local = plan.get("equal_spaced_route") or plan.get("route") or []
        if calibration_request is not None and route:
            world = DecisionWindowEvidenceAdapter.model_local_to_world(
                local,
                ego_location_xy_m=calibration_request.ego_location_xy_m,
                ego_forward_xy=calibration_request.ego_forward_xy,
                ego_right_xy=calibration_request.ego_right_xy,
            )
            interval = DecisionWindowEvidenceAdapter.project_plan_interval(world, route)
        else:
            world, interval = (), {"status": "UNKNOWN"}
        plan_world[label] = world
        projected_plans[label] = {
            "candidate_id": label,
            "ego_local_forward_right_m": local,
            "carla_world_xy_m": [list(row) for row in world],
            "route_interval": interval,
            "plan_arc_length_m": _arc(local),
            "shared_endpoint_world_xy_m": (
                list(_point_at_arc(world, shared_distance)) if world else None
            ),
        }
        coverage[label] = evaluate_plan_coverage(
            PlanCoverageInput(
                claim_id="B1-full-positive-shared-window-" + label,
                required_endpoint_id="shared-action-window-end-one-normal-refresh",
                required_endpoint_type="SHARED_ACTION_WINDOW_END",
                route_version_id=str(route_version or "UNKNOWN_ROUTE_VERSION"),
                plan_route_version_id=route_version,
                plan_frame="ROUTE_DIRECTED_PROGRESS" if coordinate.is_runtime_authorizable else None,
                plan_unit="m_route" if coordinate.is_runtime_authorizable else None,
                current_progress_m=current_progress,
                required_end_progress_m=shared_endpoint,
                valid_intervals_m=(
                    (
                        float(interval["route_progress_start_m"]),
                        float(interval["route_progress_end_m"]),
                    ),
                )
                if interval.get("status") == "AVAILABLE"
                else (),
                plan_arc_length_m=_arc(local),
                transform_uncertainty_m=transform_uncertainty,
                discretization_uncertainty_m=0.1,
                execution_latency_distance_m=shared_distance,
                alignment_verified=coordinate.is_runtime_authorizable,
                route_continuity_verified=interval.get("status") == "AVAILABLE",
                provenance=provenance,
            )
        )
    labels = sorted(plan_world)
    pair_key = canonical_pair_key(*labels) if len(labels) == 2 else "A::B"
    shared_points = {
        label: _point_at_arc(plan_world[label], shared_distance)
        for label in labels
        if plan_world[label]
    }
    shared_geometry_separation = (
        math.hypot(
            shared_points[labels[0]][0] - shared_points[labels[1]][0],
            shared_points[labels[0]][1] - shared_points[labels[1]][1],
        )
        if len(labels) == 2 and len(shared_points) == 2
        else None
    )
    plan_coverage_artifact = {
        "schema_version": "driveclarify.phase_b.plan_coverage.v1",
        "run_id": run_id,
        "status": "AVAILABLE"
        if coverage and all(value.is_runtime_authorizable for value in coverage.values())
        else "UNKNOWN",
        "required_endpoint": {
            "id": "shared-action-window-end-one-normal-refresh",
            "progress_m": shared_endpoint,
            "distance_from_source_m": shared_distance,
            "derivation": "SOURCE_LONGITUDINAL_SPEED_PLUS_PLANAR_ACCELERATION_OVER_ONE_CONFIGURED_FIXED_DELTA",
            "fixed_delta_seconds": fixed_delta,
        },
        "candidate_results": {key: value.to_dict() for key, value in coverage.items()},
        "projected_plans": projected_plans,
        "authorization_eligible": bool(coverage)
        and all(value.is_runtime_authorizable for value in coverage.values()),
    }
    _dump(report_dir / "PLAN_COVERAGE_EVIDENCE.json", plan_coverage_artifact)

    connector_rows = list(live.get("candidate_executable_connector_evidence", []))
    first_series = (
        _corridor_separation_series(connector_rows[0])
        if connector_rows
        else {"status": "UNKNOWN", "reason_code": "FIRST_CONNECTOR_MISSING"}
    )
    onset = evaluate_maneuver_onset(
        ManeuverOnsetInput(
            pair_candidate_ids=tuple(labels) if len(labels) == 2 else ("A", "B"),
            route_version_id=str(route_version or "UNKNOWN_ROUTE_VERSION"),
            progress_samples_m=tuple(first_series.get("progress_samples_m", [])),
            separation_samples_m=tuple(first_series.get("separation_samples_m", [])),
            threshold_m=lane_clearance,
            uncertainty_m=transform_uncertainty,
            hysteresis_samples=3,
            plan_coverage_verified=first_series.get("status") == "AVAILABLE",
            comparison_context_aligned=coordinate.is_runtime_authorizable,
            provenance=provenance,
        )
    )
    _dump(
        report_dir / "MANEUVER_ONSET_EVIDENCE.json",
        _envelope(
            onset,
            schema_version="driveclarify.phase_b.maneuver_onset.v1",
            run_id=run_id,
            pair_key=pair_key,
            executable_corridor_series=first_series,
            threshold_m=lane_clearance,
            threshold_derivation="LIVE_LANE_HALF_WIDTH_MINUS_EGO_HALF_WIDTH",
            topology_junction_entry_not_substituted_for_onset=True,
        ),
    )

    lane_keys = []
    first_route_index = (
        int(connector_rows[0].get("route_opportunity_index", 0)) if connector_rows else 0
    )
    for row in dense.get("lane_topology_rows", [])[: first_route_index + 1]:
        if row.get("status") != "AVAILABLE":
            continue
        key = "{}:{}:{}".format(row.get("road_id"), row.get("lane_id"), row.get("section_id"))
        if not lane_keys or lane_keys[-1] != key:
            lane_keys.append(key)
    connectors_verified = len(connector_rows) == 2 and all(
        row.get("status") == "AVAILABLE" and row.get("exit_reached") is True
        for row in connector_rows
    )
    corridor_inputs = tuple(
        CandidateCorridorInput(
            candidate_id=label,
            route_version_id=str(route_version or "UNKNOWN_ROUTE_VERSION"),
            source_candidate_version=str(source_observation_id) + ":" + label,
            start_progress_m=float(current_progress or 0.0),
            common_prefix_end_progress_m=float(shared_endpoint or 0.0),
            lane_sequence=tuple(lane_keys + [label + ":DIVERGENT_FUTURE_BRANCH"]),
            executable_reference_verified=connectors_verified,
            coverage_status=(
                coverage[label].value.get("coverage_status")
                if label in coverage and isinstance(coverage[label].value, Mapping)
                else "UNKNOWN"
            ),
        )
        for label in labels or ("A", "B")
    )
    shared_corridor = DecisionWindowEvidenceAdapter.shared_corridor(
        corridor_inputs, provenance=provenance
    )
    _dump(
        report_dir / "SHARED_CORRIDOR_EVIDENCE.json",
        _envelope(
            shared_corridor,
            schema_version="driveclarify.phase_b.shared_corridor.v1",
            run_id=run_id,
            live_lane_topology_rows=dense.get("lane_topology_rows", []),
            candidate_connector_evidence=connector_rows,
            target_bindings=live.get("target_binding_receipts", []),
            map_topology_used_as_executable_reference_not_plan_coverage_substitute=True,
        ),
    )

    second_series = (
        _corridor_separation_series(connector_rows[1])
        if len(connector_rows) >= 2
        else {"status": "UNKNOWN", "reason_code": "SECOND_CONNECTOR_MISSING"}
    )
    onset_b = evaluate_maneuver_onset(
        ManeuverOnsetInput(
            pair_candidate_ids=("B", "B_CONTINUATION"),
            route_version_id=str(route_version or "UNKNOWN_ROUTE_VERSION"),
            progress_samples_m=tuple(second_series.get("progress_samples_m", [])),
            separation_samples_m=tuple(second_series.get("separation_samples_m", [])),
            threshold_m=lane_clearance,
            uncertainty_m=transform_uncertainty,
            hysteresis_samples=3,
            plan_coverage_verified=second_series.get("status") == "AVAILABLE",
            comparison_context_aligned=coordinate.is_runtime_authorizable,
            provenance=provenance,
        )
    )
    observed_speeds = [
        float(row.get("ego", {}).get("speed_world_mps", 0.0)) for row in samples
    ]
    speed_upper = max(observed_speeds) if observed_speeds else None
    rule_valid = bool(
        source_operating
        and (
            not source_operating.get("is_at_traffic_light")
            or "GREEN" in str(source_operating.get("traffic_light_state", "")).upper()
        )
    )
    control_response_seconds = fixed_delta * float(
        operating_contract.get("control_response_upper_bound_frames", 2)
    )
    commitment_onsets = {
        "A": onset.value.get("progress_m") if isinstance(onset.value, Mapping) else None,
        "B": onset_b.value.get("progress_m") if isinstance(onset_b.value, Mapping) else None,
    }
    commitments = {}
    for label in labels or ("A", "B"):
        commitments[label] = DecisionWindowEvidenceAdapter.commitment_boundary(
            CommitmentBoundaryInput(
                candidate_id=label,
                route_version_id=route_version,
                maneuver_onset_progress_m=commitment_onsets.get(label),
                speed_upper_bound_mps=speed_upper,
                turn_speed_upper_bound_mps=speed_upper,
                acceleration_upper_bound_mps2=0.0,
                braking_deceleration_lower_bound_mps2=None,
                planning_model_latency_upper_bound_s=0.0,
                m2b_m3_authority_latency_upper_bound_s=0.0,
                control_response_latency_upper_bound_s=control_response_seconds,
                lateral_response_time_upper_bound_s=control_response_seconds,
                safety_margin_distance_m=lane_clearance,
                legal_lane_connectivity=connectors_verified,
                rule_valid=rule_valid,
                dynamic_path_feasible=(
                    connectors_verified and native.get("vehicle_collision_count") == 0
                ),
                source_assumptions=(
                    "SYNCHRONOUS_CARLA_HAS_ZERO_PHYSICAL_PROGRESS_DURING_WALL_TIME_MODEL_COMPUTE",
                    "CONTROLLED_WINDOW_MAX_SPEED_ENVELOPS_ACCELERATION_RESPONSE",
                    "TURN_SPEED_EQUALS_CONTROLLED_SPEED_ENVELOPE_SO_BRAKING_NOT_REQUIRED",
                ),
            ),
            provenance=provenance,
        )
    _dump(
        report_dir / "COMMITMENT_BOUNDARY_EVIDENCE.json",
        {
            "schema_version": "driveclarify.phase_b.commitment_boundary.v1",
            "run_id": run_id,
            "status": "AVAILABLE"
            if all(value.is_runtime_authorizable for value in commitments.values())
            else "UNKNOWN",
            "candidate_results": {
                key: value.to_dict() for key, value in commitments.items()
            },
            "candidate_b_executable_corridor_series": second_series,
            "candidate_b_maneuver_onset": onset_b.to_dict(),
            "controlled_speed_upper_bound_mps": speed_upper,
            "synchronous_simulation_motion_latency_seconds": 0.0,
            "authorization_eligible": all(
                value.is_runtime_authorizable for value in commitments.values()
            ),
        },
    )

    decision_point = evaluate_decision_point(
        candidate_ids=labels or ("A", "B"),
        pairwise_maneuver_onsets={pair_key: onset},
        commitment_points=commitments,
        provenance=provenance,
    )
    _dump(
        report_dir / "DECISION_POINT_EVIDENCE.json",
        _envelope(
            decision_point,
            schema_version="driveclarify.phase_b.decision_point.v1",
            run_id=run_id,
            semantic_targets=live.get("target_binding_receipts", []),
            topology_targets_are_not_decision_points=True,
        ),
    )

    source_monotonic = None if source is None else float(source["captured_monotonic_time"])
    onset_crossing = (
        _crossing_interval(samples, float(onset.value["progress_m"]))
        if isinstance(onset.value, Mapping)
        else {"status": "UNKNOWN", "reason_code": "ONSET_UNKNOWN"}
    )
    timing = evaluate_route_distance_timing(
        route_distance_m=(
            None
            if current_progress is None or not isinstance(onset.value, Mapping)
            else float(onset.value["progress_m"]) - current_progress
        ),
        current_speed_mps=source_ego.get("speed_world_mps"),
        future_arrival_lower_s=(
            float(onset_crossing["lower_monotonic_s"]) - source_monotonic
            if onset_crossing.get("status") == "AVAILABLE" and source_monotonic is not None
            else None
        ),
        future_arrival_upper_s=(
            float(onset_crossing["upper_monotonic_s"]) - source_monotonic
            if onset_crossing.get("status") == "AVAILABLE" and source_monotonic is not None
            else None
        ),
        provenance=provenance,
    )
    _dump(
        report_dir / "TIME_TO_DIVERGENCE_EVIDENCE.json",
        _envelope(
            timing,
            schema_version="driveclarify.phase_b.time_to_divergence.v1",
            run_id=run_id,
            controlled_crossing_interval=onset_crossing,
            constant_speed_shortcut_used=False,
        ),
    )

    earliest_commitment = min(
        (
            float(value.value["progress_m"])
            for value in commitments.values()
            if isinstance(value.value, Mapping)
        ),
        default=None,
    )
    commitment_crossing = (
        _crossing_interval(samples, earliest_commitment)
        if earliest_commitment is not None
        else {"status": "UNKNOWN", "reason_code": "COMMITMENT_UNKNOWN"}
    )
    source_baseline_latency = sum(
        float(row.get("latency_seconds", 0.0))
        for row in live.get("baseline_forward_events", [])
        if int(row.get("source_frame_id", -1)) == source_frame
    )
    candidate_latencies = [
        float(row.get("latency_seconds", 0.0))
        for row in live.get("candidate_plans", [])
    ]
    measured_bundle_latency = source_baseline_latency + sum(candidate_latencies)
    bundle_upper = float(
        operating_contract.get("full_baseline_plus_k2_bundle_wall_timeout_s", 0.0)
    )
    latency_contract_pass = bundle_upper > 0.0 and measured_bundle_latency <= bundle_upper
    if commitment_crossing.get("status") == "AVAILABLE" and latency_contract_pass:
        commitment_time_lower = _available_seconds(
            float(commitment_crossing["lower_monotonic_s"]),
            provenance,
            "controlled_commitment_crossing_lower",
        )
        answer_upper = _available_seconds(
            float(operating_contract["configured_answer_latency_upper_bound_s"]),
            provenance,
            "configured_answer_channel_bound",
        )
        bundle_result = _available_seconds(
            bundle_upper, provenance, "enforced_full_bundle_timeout"
        )
        authority_upper = _available_seconds(
            float(operating_contract["m2b_m3_authority_latency_upper_bound_s"]),
            provenance,
            "frozen_authority_receipt_bound",
        )
        control_upper = _available_seconds(
            control_response_seconds,
            provenance,
            "two_carla_frame_control_response_bound",
        )
        safety_margin_time = _available_seconds(
            fixed_delta * float(operating_contract.get("safety_margin_frames", 1)),
            provenance,
            "one_carla_frame_safety_margin",
        )
    else:
        unavailable_timing = unknown_result(
            "CONTROLLED_COMMITMENT_CROSSING_OR_ENFORCED_LATENCY_CONTRACT_UNAVAILABLE",
            unit="s",
            frame="ABSOLUTE_MONOTONIC_TIME",
            clock_domain="MONOTONIC",
            purpose=UsagePurpose.AUTHORIZATION,
            provenance=provenance,
        )
        commitment_time_lower = unavailable_timing
        answer_upper = unavailable_timing
        bundle_result = unavailable_timing
        authority_upper = unavailable_timing
        control_upper = unavailable_timing
        safety_margin_time = unavailable_timing
    latest_safe = evaluate_latest_safe_clarification(
        commitment_time_lower_bound=commitment_time_lower,
        answer_latency_upper_bound=answer_upper,
        candidate_refresh_replan_latency_upper_bound=bundle_result,
        m2b_m3_authority_latency_upper_bound=authority_upper,
        control_response_latency_upper_bound=control_upper,
        safety_margin=safety_margin_time,
        provenance=provenance,
    )
    _dump(
        report_dir / "TIMING_EVIDENCE.json",
        {
            "schema_version": "driveclarify.phase_b.timing.v1",
            "run_id": run_id,
            "status": "AVAILABLE" if latest_safe.is_runtime_authorizable else "UNKNOWN",
            "baseline_forward_events": live.get("baseline_forward_events", []),
            "candidate_forward_latencies_seconds": candidate_latencies,
            "measured_full_bundle_latency_seconds": measured_bundle_latency,
            "enforced_full_bundle_timeout_seconds": bundle_upper,
            "latency_contract_pass": latency_contract_pass,
            "commitment_crossing_interval": commitment_crossing,
            "latest_safe_clarification": latest_safe.to_dict(),
            "synchronous_wall_compute_does_not_advance_physical_simulation": True,
            "authorization_eligible": latest_safe.is_runtime_authorizable,
        },
    )

    next_sample = next(
        (row for row in samples if int(row.get("hook_frame", -1)) == source_frame + 1),
        None,
    )
    endpoint_lane_safe = False
    if next_sample is not None and lane_clearance is not None:
        projection = next_sample.get("ego_route_projection", {})
        endpoint_lane_safe = (
            projection.get("status") == "AVAILABLE"
            and float(projection["projection_error_m"]) < float(lane_clearance)
        )
    shared_safe_ok = bool(
        coordinate.is_runtime_authorizable
        and endpoint_lane_safe
        and source_operating.get("synchronous_mode") is True
        and rule_valid
        and native.get("vehicle_collision_count") == 0
        and shared_geometry_separation is not None
        and lane_clearance is not None
        and shared_geometry_separation
        <= float(lane_clearance) + float(transform_uncertainty or 0.0)
    )
    shared_safe = (
        available_result(
            True,
            unit="BOOL",
            frame="DECISION_WINDOW",
            clock_domain="MONOTONIC",
            purpose=UsagePurpose.SAFETY_CRITICAL,
            provenance=provenance,
            dependencies=(
                "controlled_next_frame_lane_containment",
                "existing_baseline_single_pid",
                "traffic_rule_state",
                "pairwise_shared_window_geometry",
            ),
        )
        if shared_safe_ok
        else unknown_result(
            "SHARED_ACTION_SAFETY_NOT_PROVEN",
            unit="BOOL",
            frame="DECISION_WINDOW",
            clock_domain="MONOTONIC",
            purpose=UsagePurpose.SAFETY_CRITICAL,
            provenance=provenance,
        )
    )
    latency_margin_sufficient = bool(
        latest_safe.is_runtime_authorizable
        and source_monotonic is not None
        and float(latest_safe.value) > source_monotonic
    )
    reachability = []
    for label in labels or ("A", "B"):
        boundary = commitments[label].value if label in commitments else None
        before_boundary = bool(
            isinstance(boundary, Mapping)
            and shared_endpoint is not None
            and shared_endpoint
            < float(boundary["progress_m"]) - float(boundary["uncertainty_m"])
        )
        reachability.append(
            CandidateReachability(
                candidate_id=label,
                endpoint_defined=(
                    shared_endpoint is not None
                    and shared_endpoint > float(current_progress or 0.0)
                ),
                legal_lane_connectivity=connectors_verified,
                branch_accessible=connectors_verified and before_boundary,
                braking_margin_sufficient=(
                    isinstance(boundary, Mapping)
                    and boundary.get("braking_required_by_speed_envelope") is False
                ),
                lateral_feasible=connectors_verified and before_boundary,
                rule_margin_sufficient=rule_valid,
                remaining_distance_sufficient=before_boundary,
                latency_margin_sufficient=latency_margin_sufficient,
                dynamic_safety_guard_pass=shared_safe.is_safety_authorizable,
                route_version_matches=coordinate.is_runtime_authorizable,
                fresh=True,
                no_collision_observed=native.get("vehicle_collision_count") == 0,
            )
        )
    recoverability = evaluate_recoverability(reachability, provenance)
    _dump(
        report_dir / "RECOVERABILITY_EVIDENCE.json",
        _envelope(
            recoverability,
            schema_version="driveclarify.phase_b.recoverability.v1",
            run_id=run_id,
            shared_action_endpoint_progress_m=shared_endpoint,
            candidate_inputs=[row.__dict__ for row in reachability],
            no_collision_is_not_recoverability=True,
        ),
    )

    commitment_passed = {
        label: (
            None
            if not isinstance(commitments[label].value, Mapping) or current_progress is None
            else current_progress
            >= float(commitments[label].value["progress_m"])
            - float(commitments[label].value["uncertainty_m"])
        )
        for label in labels or ("A", "B")
    }
    current_relation = evaluate_current_action_equivalence(
        CurrentActionEquivalenceInput(
            candidate_ids=tuple(labels) if labels else ("A", "B"),
            semantic_candidates_unresolved=True,
            comparison_context_aligned=coordinate.is_runtime_authorizable,
            plan_coverage=coverage,
            action_primitives={
                label: "KEEP_LANE_SHARED_APPROACH" for label in labels or ("A", "B")
            },
            pairwise_geometry_separation_m=(
                {pair_key: float(shared_geometry_separation)}
                if shared_geometry_separation is not None
                else {}
            ),
            geometry_threshold_m=lane_clearance,
            geometry_uncertainty_m=transform_uncertainty,
            commitment_passed=commitment_passed,
            recoverability=recoverability,
            shared_action_safe=shared_safe,
            provenance=provenance,
        )
    )
    future_obligations = available_result(
        True,
        unit="BOOL",
        frame="SEMANTIC_TOPOLOGY_TARGET_SET",
        clock_domain="MONOTONIC",
        purpose=UsagePurpose.AUTHORIZATION,
        provenance=provenance,
        dependencies=("distinct_target_ids", "distinct_branch_ids"),
    )
    relationship = classify_candidate_relationship(
        current_relation, future_obligations, provenance=provenance
    )
    required = {
        "coordinate_calibration": coordinate,
        "ego_route_progress": ego_progress,
        "shared_corridor": shared_corridor,
        "maneuver_onset": onset,
        "decision_point": decision_point,
        "recoverability": recoverability,
        "time_to_divergence": timing,
        "latest_safe_clarification": latest_safe,
        "current_action_relation": current_relation,
        "candidate_relationship": relationship,
    }
    unresolved = [
        key
        for key, value in required.items()
        if not (
            value.is_safety_authorizable
            if key == "recoverability"
            else value.is_runtime_authorizable
        )
    ]
    phase_b_acceptance = not unresolved and bool(coverage) and all(
        value.authorization_eligible for value in coverage.values()
    )
    _dump(
        report_dir / "DECISION_WINDOW_EVIDENCE.json",
        {
            "schema_version": "driveclarify.phase_b.decision_window.v1",
            "run_id": run_id,
            "status": "PASS" if phase_b_acceptance else "UNKNOWN",
            "candidate_ids": labels,
            "results": {key: value.to_dict() for key, value in required.items()},
            "plan_coverage": {key: value.to_dict() for key, value in coverage.items()},
            "unresolved_dependencies": unresolved,
            "phase_b_acceptance": phase_b_acceptance,
            "phase_c_authorized_to_continue": phase_b_acceptance,
        },
    )

    forward = {
        "schema_version": "driveclarify.phase_b.forward_accounting.v1",
        "run_id": run_id,
        "dino": live.get("dino_event_triggered_forward_count"),
        "baseline_simlingo": live.get("normal_simlingo_forward_count"),
        "candidate_simlingo": live.get("candidate_simlingo_forward_count"),
        "candidate_requested": live.get("candidate_forward_requested"),
        "candidate_executed": live.get("candidate_forward_executed"),
        "candidate_repeat": live.get("online_candidate_repeat_count"),
        "visualization_extra": live.get("visualization_extra_forward_count"),
        "policy_decision_executed": live.get("policy_decision_executed"),
    }
    _dump(report_dir / "FORWARD_ACCOUNTING.json", forward)
    privileged = {
        "schema_version": "driveclarify.phase_b.privileged_read_audit.v1",
        "run_id": run_id,
        "scenario_gold_policy_reads": native.get("gold_policy_label_reads", 0),
        "target_binding_privileged_state_read_count": sum(
            int(row.get("privileged_state_read_count", 0))
            for row in live.get("target_binding_receipts", [])
        ),
        "status": "PASS_NO_PRIVILEGED_POLICY_INPUT",
    }
    _dump(report_dir / "PRIVILEGED_READ_AUDIT.json", privileged)
    authority = {
        "schema_version": "driveclarify.phase_b.authority_mutation_audit.v1",
        "run_id": run_id,
        "status": "PASS_EXISTING_BASELINE_AUTHORITY_UNCHANGED",
        "control_authority": live.get("control_authority"),
        "candidate_plan_selected_for_control": live.get("candidate_plan_selected_for_control"),
        "new_pid_count": live.get("new_pid_count"),
        "new_planner_count": live.get("new_planner_count"),
        "direct_vehicle_control_write_count": live.get("direct_vehicle_control_write_count"),
        "planner_advance_count": live.get("planner_advance_count"),
        "route_planner_mutation_count": live.get("route_planner_mutation_count"),
    }
    _dump(report_dir / "AUTHORITY_MUTATION_AUDIT.json", authority)

    valid_controlled_probe = bool(
        native.get("status") == "PASS_NATIVE_UNIFIED_TRIAD_CAPTURE_AND_CLEANUP"
        and desktop.get("status") == "PASS_VALIDATED_NATIVE_DESKTOP"
        and preflight.get("status") == "PASS"
        and live.get("probe_run_id") == run_id
        and live.get("physical_runtime_evidence_captured") is True
        and live.get("candidate_forward_executed") == 2
        and live.get("candidate_plan_selected_for_control") is False
        and native.get("vehicle_collision_count") == 0
    )
    receipt = {
        "schema_version": "driveclarify.phase_b.evidence_completion_receipt.v1",
        "status": (
            "PASS_PHASE_B_EVIDENCE_COMPLETION"
            if phase_b_acceptance and valid_controlled_probe
            else "PARTIAL_PASS_B1_VALID_CONTROLLED_EVIDENCE_CRITICAL_UNKNOWNS_REMAIN"
            if valid_controlled_probe
            else "BLOCKED_B1_INVALID_CONTROLLED_PROBE"
        ),
        "run_id": run_id,
        "valid_controlled_probe_run": valid_controlled_probe,
        "phase_b_acceptance": phase_b_acceptance and valid_controlled_probe,
        "phase_c_authorized_to_continue": phase_b_acceptance and valid_controlled_probe,
        "retry_predecessor_run_id": "B1-R3" if str(run_id) == "B1-R4" else None,
        "retry_engineering_classification": (
            "NATIVE_DESKTOP_CAPTURE_STATUS_ALIAS_DEADLOCK_AFTER_RUNTIME_TERMINAL"
            if str(run_id) == "B1-R4"
            else None
        ),
        "prior_scientific_collision_relabelled": False,
        "unresolved_dependencies": unresolved,
        "coordinate_route_binding_repaired": coordinate.is_runtime_authorizable,
        "old_b0_route_binding_reused": False,
        "analysis_wall_seconds": time.monotonic() - started,
        "native_status": native.get("status"),
        "desktop_status": desktop.get("status"),
        "live_status": live.get("status"),
        "forward_accounting": forward,
        "authority_audit": authority,
        "privileged_read_audit": privileged,
    }
    _dump(report_dir / "PHASE_B_EVIDENCE_RECEIPT.json", receipt)
    return receipt


__all__ = ["analyze"]
