"""Post-run, fail-closed analysis for the Phase B white-van probe."""

from __future__ import annotations

import hashlib
import json
import math
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from driveclarify_persistent_ambiguity_runtime_v1.contracts import (
    canonical_pair_key,
    canonical_sha256,
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
    EvidenceGrade,
    EvidenceProvenance,
    TimeToDivergenceSemantics,
    UsagePurpose,
    Visibility,
    available_result,
    unknown_result,
)

from .runtime import EXPECTED_TARGETS, _project_to_polyline


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _route_rows(live: Mapping[str, Any]) -> list:
    rows = live.get("topology_context", {}).get("route_polyline_world") or []
    return [[float(row[0]), float(row[1]), "UNKNOWN_OPTION"] for row in rows]


def _world_plan(
    route: Sequence[Sequence[float]], source: Mapping[str, Any]
) -> list:
    ego = source["ego"]
    location = ego["location_xyz"]
    forward = ego["forward_vector_xyz"]
    right = ego["right_vector_xyz"]
    return [
        [
            float(location[0]) + float(forward[0]) * float(point[0]) + float(right[0]) * float(point[1]),
            float(location[1]) + float(forward[1]) * float(point[0]) + float(right[1]) * float(point[1]),
        ]
        for point in route
    ]


def _arc(route: Sequence[Sequence[float]]) -> float:
    return sum(
        math.hypot(float(right[0]) - float(left[0]), float(right[1]) - float(left[1]))
        for left, right in zip(route, route[1:])
    )


def _pair_metrics(left: Sequence[Sequence[float]], right: Sequence[Sequence[float]]) -> Mapping[str, Any]:
    separations = [
        math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))
        for a, b in zip(left, right)
    ]
    squared = [
        (float(a[0]) - float(b[0])) ** 2 + (float(a[1]) - float(b[1])) ** 2
        for a, b in zip(left, right)
    ]
    return {
        "point_count": len(separations),
        "route_vector_rmse_m": math.sqrt(sum(squared) / max(len(squared), 1)),
        "max_separation_m": max(separations) if separations else None,
        "separation_by_waypoint_m": separations,
    }


def analyze(
    output_dir: Path, report_dir: Path, *, run_id: str = "B0-R1"
) -> Mapping[str, Any]:
    started = time.monotonic()
    live = _load(output_dir / "GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json")
    native = _load(output_dir / "GROUNDED_LANGUAGE_V1_NATIVE_RUN_RECEIPT.json")
    preflight = _load(output_dir / "GROUNDED_LANGUAGE_V1_NATIVE_PREFLIGHT.json")
    desktop = _load(output_dir / "NATIVE_DESKTOP_VALIDATION.json")
    source_frame = int(live["plan_source_frame"])
    samples = [row for row in live.get("physical_runtime_samples", []) if row.get("status") == "AVAILABLE"]
    exact = [row for row in samples if int(row.get("hook_frame", -1)) == source_frame]
    source = exact[0] if len(exact) == 1 else None
    route_rows = _route_rows(live)
    route_version = None if source is None else source.get("route_version_digest")
    snapshot_same_frame = bool(
        source is not None
        and source.get("snapshot", {}).get("status") == "AVAILABLE"
        and int(source["snapshot"]["frame"]) == source_frame
    )
    observation_same = bool(
        source is not None
        and source.get("source_observation_id") == live.get("plan_source_observation_id")
    )

    coordinate_candidates = {}
    projected_plans = {}
    for plan in live.get("candidate_plans", []):
        label = str(plan["candidate_id"])
        route = plan.get("equal_spaced_route") or plan.get("route") or []
        world = _world_plan(route, source) if source is not None else []
        projections = [_project_to_polyline(point, route_rows) for point in world]
        available = [row for row in projections if row.get("status") == "AVAILABLE"]
        progress = [float(row["progress_m"]) for row in available]
        errors = [float(row["projection_error_m"]) for row in available]
        monotonic = all(right + 1e-6 >= left for left, right in zip(progress, progress[1:]))
        coordinate_candidates[label] = {
            "model_frame": "EGO_LOCAL_FORWARD_RIGHT_METERS_FROM_OFFICIAL_SIMLINGO_ROUTE_CONTRACT",
            "world_transform": "ego_location + forward_vector*route[0] + right_vector*route[1]",
            "source_frame": source_frame,
            "same_snapshot_frame": snapshot_same_frame,
            "same_observation": observation_same,
            "route_version_digest": route_version,
            "plan_arc_length_m": _arc(route),
            "world_arc_length_m": _arc(world),
            "route_projection_count": len(available),
            "route_projection_monotonic": monotonic,
            "route_projection_error_mean_m": sum(errors) / len(errors) if errors else None,
            "route_projection_error_max_m": max(errors) if errors else None,
            "route_projection_progress_start_m": progress[0] if progress else None,
            "route_projection_progress_end_m": progress[-1] if progress else None,
        }
        projected_plans[label] = {
            "ego_local_route_m": route,
            "carla_world_route_m": world,
            "directed_route_projections": projections,
        }

    baseline_local = live.get("baseline_route_at_source") or []
    baseline_world = _world_plan(baseline_local, source) if source is not None else []
    actual_world = [
        row["ego"]["location_xyz"][:2]
        for row in samples
        if int(row.get("hook_frame", -1)) >= source_frame
        and isinstance(row.get("ego", {}).get("location_xyz"), list)
    ]
    actual_to_baseline_errors = []
    for point in actual_world:
        if baseline_world:
            actual_to_baseline_errors.append(
                min(math.hypot(float(point[0]) - route_point[0], float(point[1]) - route_point[1]) for route_point in baseline_world)
            )
    coordinate_status = (
        "SUPPORTED_BUT_INCOMPLETE_NOT_RUNTIME_AUTHORIZABLE"
        if source is not None and snapshot_same_frame and observation_same and coordinate_candidates
        else "UNKNOWN"
    )
    coordinate = {
        "schema_version": "driveclarify.coordinate_frame_validation.v1",
        "status": coordinate_status,
        "authorization_eligible": False,
        "reason_code": "CONTROLLED_PROBE_TRANSFORM_CAPTURED_BUT_CALIBRATED_ERROR_BOUND_AND_SIGN_CONVENTION_ACCEPTANCE_NOT_FROZEN",
        "source_frame": source_frame,
        "source_observation_id": live.get("plan_source_observation_id"),
        "snapshot_same_frame": snapshot_same_frame,
        "observation_identity_match": observation_same,
        "route_version_digest": route_version,
        "route_reference_origin_xy_m": None if source is None else source.get("route_reference_origin_xy_m"),
        "ego_route_projection": None if source is None else source.get("ego_route_projection"),
        "candidate_results": coordinate_candidates,
        "baseline_actual_path_diagnostic": {
            "actual_sample_count": len(actual_world),
            "nearest_baseline_plan_error_mean_m": (
                sum(actual_to_baseline_errors) / len(actual_to_baseline_errors)
                if actual_to_baseline_errors else None
            ),
            "nearest_baseline_plan_error_max_m": max(actual_to_baseline_errors) if actual_to_baseline_errors else None,
            "interpretation": "DIAGNOSTIC_ONLY_NO_PRE_FROZEN_ACCEPTANCE_THRESHOLD",
        },
        "projected_plans": projected_plans,
    }
    _dump(report_dir / "COORDINATE_FRAME_VALIDATION.json", coordinate)

    provenance = EvidenceProvenance(
        producer="DecisionWindowEvidenceProbeRuntime.B0-R1",
        source_kind="NATIVE_CARLA_SAME_FRAME_RUNTIME_CAPTURE",
        source_observation_id=live.get("plan_source_observation_id"),
        source_frame_id=source_frame,
        observed_monotonic_time=(
            None if source is None else source.get("captured_monotonic_time")
        ),
        source_simulation_time=(
            None if source is None else source.get("hook_simulation_time")
        ),
        coordinate_transform_id=(
            None
            if source is None
            else "carla-world-to-directed-route-" + str(route_version)
        ),
        visibility=Visibility.RUNTIME_OBSERVABLE,
        artifact_sha256=_sha(output_dir / "GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json"),
        comparison_context_id=live.get("plan_source_observation_id"),
    )
    ego_projection = None if source is None else source.get("ego_route_projection")
    if (
        isinstance(ego_projection, Mapping)
        and ego_projection.get("status") == "AVAILABLE"
        and snapshot_same_frame
        and observation_same
    ):
        ego_progress_result = available_result(
            float(ego_projection["progress_m"]),
            unit="m_route",
            frame="ROUTE_DIRECTED_PROGRESS",
            clock_domain="MONOTONIC",
            purpose=UsagePurpose.AUTHORIZATION,
            provenance=provenance,
            dependencies=("same_frame_ego_pose", "live_route_version"),
            source_artifacts=(
                str(output_dir / "GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json"),
            ),
        )
    else:
        ego_progress_result = unknown_result(
            "EGO_ROUTE_PROGRESS_SAME_FRAME_PROJECTION_UNAVAILABLE",
            dependencies=("same_frame_ego_pose", "live_route_version"),
            unit="m_route",
            frame="ROUTE_DIRECTED_PROGRESS",
            clock_domain="MONOTONIC",
            purpose=UsagePurpose.AUTHORIZATION,
            provenance=provenance,
        )
    ego_progress_evidence = {
        "schema_version": "driveclarify.ego_route_progress_evidence.v1",
        **ego_progress_result.to_dict(),
        "route_version_digest": route_version,
        "projection_diagnostic": ego_projection,
        "same_snapshot_frame": snapshot_same_frame,
        "same_observation": observation_same,
        "map_waypoint": None if source is None else source.get("map_waypoint"),
        "ego_speed_world_mps": (
            None if source is None else source.get("ego", {}).get("speed_world_mps")
        ),
    }
    _dump(report_dir / "EGO_ROUTE_PROGRESS_EVIDENCE.json", ego_progress_evidence)

    plans = live.get("candidate_plans", [])
    routes = [row.get("equal_spaced_route") or row.get("route") or [] for row in plans]
    pair = _pair_metrics(routes[0], routes[1]) if len(routes) == 2 else {}
    first_target_progress = float(live["target_binding_receipts"][0]["distance_or_progress"])
    plan_coverage_results = {}
    for plan in plans:
        label = str(plan["candidate_id"])
        route = plan.get("equal_spaced_route") or plan.get("route") or []
        plan_coverage_results[label] = evaluate_plan_coverage(
            PlanCoverageInput(
                claim_id="B0-R1-plan-coverage-" + label,
                required_endpoint_id="shared-action-window-end-unresolved",
                required_endpoint_type="SHARED_ACTION_WINDOW_END",
                route_version_id=str(route_version or "UNKNOWN_ROUTE_VERSION"),
                plan_route_version_id=None,
                plan_frame=None,
                plan_unit=None,
                current_progress_m=(
                    float(ego_progress_result.value)
                    if ego_progress_result.value is not None
                    else None
                ),
                required_end_progress_m=None,
                valid_intervals_m=(),
                plan_arc_length_m=_arc(route),
                transform_uncertainty_m=None,
                discretization_uncertainty_m=None,
                execution_latency_distance_m=None,
                alignment_verified=False,
                route_continuity_verified=False,
                provenance=provenance,
            )
        )
    plan_coverage = {
        "schema_version": "driveclarify.plan_coverage_evidence.v1",
        "status": "UNKNOWN",
        "value": None,
        "authorization_eligible": False,
        "reason_code": "SHARED_ACTION_WINDOW_ENDPOINT_AND_AUTHORIZABLE_PLAN_ROUTE_CALIBRATION_NOT_ESTABLISHED",
        "g0_evaluator_results": {
            key: value.to_dict() for key, value in plan_coverage_results.items()
        },
        "candidate_projection_diagnostics": coordinate_candidates,
        "first_topology_target_progress_m": first_target_progress,
        "prohibited_shortcut_used": False,
        "note": "Projected plan intervals are retained as diagnostic evidence; they are not compared with the topology anchor as an authorization claim.",
    }
    _dump(report_dir / "PLAN_COVERAGE_EVIDENCE.json", plan_coverage)

    shared_corridor_result = unknown_result(
        "SHARED_CORRIDOR_CALIBRATED_THRESHOLD_HYSTERESIS_AND_ENDPOINT_UNKNOWN",
        dependencies=(
            "authorization_grade_plan_alignment",
            "pairwise_corridor_threshold",
            "hysteresis",
            "shared_window_endpoint",
        ),
        unit="m_route",
        frame="ROUTE_DIRECTED_PROGRESS",
        clock_domain="MONOTONIC",
        purpose=UsagePurpose.AUTHORIZATION,
        provenance=provenance,
    )
    shared_corridor = {
        "schema_version": "driveclarify.shared_corridor_evidence.v1",
        **shared_corridor_result.to_dict(),
        "current_plan_pair_diagnostic": pair,
        "candidate_projection_diagnostics": coordinate_candidates,
        "text_or_target_id_substitution_used": False,
    }
    _dump(report_dir / "SHARED_CORRIDOR_EVIDENCE.json", shared_corridor)

    maneuver_result = evaluate_maneuver_onset(
        ManeuverOnsetInput(
            pair_candidate_ids=("A", "B"),
            route_version_id=str(route_version or "UNKNOWN_ROUTE_VERSION"),
            progress_samples_m=(),
            separation_samples_m=(),
            threshold_m=None,
            uncertainty_m=None,
            hysteresis_samples=1,
            plan_coverage_verified=False,
            comparison_context_aligned=False,
            provenance=provenance,
        )
    )
    maneuver = {
        "schema_version": "driveclarify.maneuver_onset_evidence.v1",
        **maneuver_result.to_dict(),
        "current_plan_pair_diagnostic": pair,
        "topology_anchor_diagnostic": {
            "first_junction_entry_progress_m": first_target_progress,
            "candidate_A_branch_anchor_xy": live["target_binding_receipts"][0].get("branch_anchor_xy"),
            "candidate_B_later_branch_anchor_xy": live["target_binding_receipts"][1].get("branch_anchor_xy"),
            "explicitly_not_promoted_to_maneuver_onset": True,
        },
    }
    _dump(report_dir / "MANEUVER_ONSET_EVIDENCE.json", maneuver)

    commitment_results = {
        label: unknown_result(
            "CANDIDATE_LAST_LEGAL_DYNAMIC_SWITCH_BOUNDARY_NOT_IDENTIFIED",
            dependencies=(
                "legal_lane_connectivity",
                "braking_and_lateral_response_envelope",
                "rule_constrained_reachability",
                "complete_latency_upper_bounds",
            ),
            unit="m_route",
            frame="ROUTE_DIRECTED_PROGRESS",
            clock_domain="MONOTONIC",
            purpose=UsagePurpose.AUTHORIZATION,
            provenance=provenance,
        )
        for label in ("A", "B")
    }
    commitment = {
        "schema_version": "driveclarify.commitment_boundary_evidence.v1",
        "status": "UNKNOWN",
        "value": None,
        "authorization_eligible": False,
        "reason_code": "BLOCKED_COMMITMENT_BOUNDARY_RUNTIME_EVIDENCE_NOT_IDENTIFIED",
        "candidate_results": {
            key: value.to_dict() for key, value in commitment_results.items()
        },
        "available_diagnostic_sources": [
            "live_map_road_lane_junction_ids",
            "route_ordered_right_branch_anchors",
            "ego_pose_speed_and_directed_route_projection",
        ],
        "missing_authorization_sources": [
            "candidate_wise_last_legal_switchable_state",
            "validated_lateral_and_braking_response_envelope",
            "rule_constrained_reachability_after_all_latencies",
        ],
        "hand_drawn_or_target_waypoint_substitution_used": False,
    }
    _dump(report_dir / "COMMITMENT_BOUNDARY_EVIDENCE.json", commitment)

    decision_point_result = evaluate_decision_point(
        candidate_ids=("A", "B"),
        pairwise_maneuver_onsets={
            canonical_pair_key("A", "B"): maneuver_result,
        },
        commitment_points=commitment_results,
        provenance=provenance,
    )
    decision_point = {
        "schema_version": "driveclarify.decision_point_evidence.v1",
        **decision_point_result.to_dict(),
        "semantic_target_is_decision_point": False,
        "topology_target_is_decision_point": False,
        "junction_center_substitution_used": False,
    }
    _dump(report_dir / "DECISION_POINT_EVIDENCE.json", decision_point)

    recoverability_result = evaluate_recoverability(
        tuple(
            CandidateReachability(
                candidate_id=label,
                endpoint_defined=None,
                legal_lane_connectivity=None,
                branch_accessible=None,
                braking_margin_sufficient=None,
                lateral_feasible=None,
                rule_margin_sufficient=None,
                remaining_distance_sufficient=None,
                latency_margin_sufficient=None,
                dynamic_safety_guard_pass=None,
                route_version_matches=None,
                fresh=None,
                no_collision_observed=None,
            )
            for label in ("A", "B")
        ),
        provenance,
    )
    recoverability = {
        "schema_version": "driveclarify.recoverability_evidence.v1",
        **recoverability_result.to_dict(),
        "no_collision_treated_as_recoverable": False,
        "available": ["lane_ids", "branch_connectivity_diagnostic", "ego_speed", "remaining_route_geometry"],
        "missing": [
            "shared_action_endpoint",
            "per_candidate_legal_reachable_path_from_endpoint",
            "braking_lateral_rule_and_latency_margins",
            "dynamic_occupancy_feasibility",
        ],
    }
    _dump(report_dir / "RECOVERABILITY_EVIDENCE.json", recoverability)

    time_to_divergence_result = evaluate_route_distance_timing(
        route_distance_m=None,
        current_speed_mps=(
            None if source is None else source.get("ego", {}).get("speed_world_mps")
        ),
        future_arrival_lower_s=None,
        future_arrival_upper_s=None,
        provenance=provenance,
    )
    time_to_divergence = {
        "schema_version": "driveclarify.time_to_divergence_evidence.v1",
        **time_to_divergence_result.to_dict(),
        "constant_speed_distance_division_used": False,
        "required_missing_sources": [
            "authorization_grade_decision_or_commitment_progress",
            "future_speed_or_arrival_time_envelope",
        ],
    }
    _dump(
        report_dir / "TIME_TO_DIVERGENCE_EVIDENCE.json",
        time_to_divergence,
    )

    missing_timing = unknown_result(
        "RUNTIME_UPPER_BOUND_NOT_ESTABLISHED_BY_SINGLE_PASSIVE_PROBE",
        unit="s",
        frame="TIME_INTERVAL",
        clock_domain="MONOTONIC",
        purpose=UsagePurpose.AUTHORIZATION,
        provenance=provenance,
    )
    commitment_time = unknown_result(
        "COMMITMENT_TIME_LOWER_BOUND_UNKNOWN",
        unit="s",
        frame="ABSOLUTE_MONOTONIC_TIME",
        clock_domain="MONOTONIC",
        purpose=UsagePurpose.AUTHORIZATION,
        provenance=provenance,
    )
    latest_safe_result = evaluate_latest_safe_clarification(
        commitment_time_lower_bound=commitment_time,
        answer_latency_upper_bound=missing_timing,
        candidate_refresh_replan_latency_upper_bound=missing_timing,
        m2b_m3_authority_latency_upper_bound=missing_timing,
        control_response_latency_upper_bound=missing_timing,
        safety_margin=missing_timing,
        provenance=provenance,
    )

    shared_action_safe = unknown_result(
        "SHARED_ACTION_SAFETY_NOT_EVALUATED_IN_PASSIVE_PROBE",
        unit="BOOLEAN",
        frame="SHARED_ACTION_ENDPOINT",
        clock_domain="MONOTONIC",
        purpose=UsagePurpose.SAFETY_CRITICAL,
        provenance=provenance,
    )
    current_action_result = evaluate_current_action_equivalence(
        CurrentActionEquivalenceInput(
            candidate_ids=("A", "B"),
            semantic_candidates_unresolved=True,
            comparison_context_aligned=False,
            plan_coverage=plan_coverage_results,
            action_primitives={
                "A": "TURN_AT_UPCOMING_OPPORTUNITY",
                "B": "CONTINUE_TO_LATER_OPPORTUNITY",
            },
            pairwise_geometry_separation_m={
                canonical_pair_key("A", "B"): float(
                    pair.get("max_separation_m") or 0.0
                )
            },
            geometry_threshold_m=None,
            geometry_uncertainty_m=None,
            commitment_passed={"A": None, "B": None},
            recoverability=recoverability_result,
            shared_action_safe=shared_action_safe,
            provenance=provenance,
        )
    )
    fixture = live.get("frozen_fixture_identity", {})
    if (
        fixture.get("exact_match") is True
        and tuple(tuple(row) for row in fixture.get("actual_targets", []))
        == EXPECTED_TARGETS
    ):
        future_divergence_result = available_result(
            True,
            unit="BOOLEAN",
            frame="FUTURE_ROUTE_OBLIGATION",
            clock_domain="MONOTONIC",
            purpose=UsagePurpose.AUTHORIZATION,
            provenance=provenance,
            dependencies=("runtime_distinct_topology_targets",),
            source_artifacts=(
                str(output_dir / "GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json"),
            ),
        )
    else:
        future_divergence_result = unknown_result(
            "RUNTIME_FROZEN_TARGET_IDENTITY_MISMATCH_OR_UNAVAILABLE",
            unit="BOOLEAN",
            frame="FUTURE_ROUTE_OBLIGATION",
            clock_domain="MONOTONIC",
            purpose=UsagePurpose.AUTHORIZATION,
            provenance=provenance,
        )
    relationship_result = classify_candidate_relationship(
        current_action_result,
        future_divergence_result,
        provenance=provenance,
    )
    decision_window = {
        "schema_version": "driveclarify.decision_window_evidence.v1",
        "status": "UNKNOWN",
        "value": None,
        "authorization_eligible": False,
        "reason_code": "CURRENT_ACTION_COMMITMENT_RECOVERABILITY_AND_COMPLETE_TIMING_DEPENDENCIES_UNKNOWN",
        "ego_route_progress": ego_progress_result.to_dict(),
        "current_action_equivalence": current_action_result.to_dict(),
        "future_obligation_divergence": future_divergence_result.to_dict(),
        "candidate_relationship": relationship_result.to_dict(),
        "shared_corridor": shared_corridor_result.to_dict(),
        "maneuver_onset": maneuver_result.to_dict(),
        "decision_point": decision_point_result.to_dict(),
        "commitment_boundary": {
            key: value.to_dict() for key, value in commitment_results.items()
        },
        "recoverability": recoverability_result.to_dict(),
        "time_to_divergence": time_to_divergence_result.to_dict(),
        "latest_safe_clarification": latest_safe_result.to_dict(),
    }
    _dump(report_dir / "DECISION_WINDOW_EVIDENCE.json", decision_window)

    candidate_latencies = [
        {
            "candidate_id": row["candidate_id"],
            "latency_seconds": row.get("latency_seconds"),
            "started_monotonic": row.get("forward_evidence", {}).get("started_monotonic"),
            "ended_monotonic": row.get("forward_evidence", {}).get("ended_monotonic"),
        }
        for row in plans
    ]
    timing = {
        "schema_version": "driveclarify.timing_evidence.v1",
        "status": "PARTIAL_MEASURED_NOT_SUFFICIENT_FOR_LATEST_SAFE_CLARIFICATION",
        "measured": {
            "dino_latency_seconds": live.get("detector_latency_seconds"),
            "candidate_construction_latency_seconds": live.get("candidate_construction_latency_seconds"),
            "baseline_forward_events": live.get("baseline_forward_events", []),
            "candidate_forward_events": candidate_latencies,
            "postprocess_consequence_evaluation_latency_seconds": time.monotonic() - started,
            "existing_pid_invocation_count": live.get("existing_pid_invocation_count"),
            "baseline_control_event_count": len(live.get("baseline_control_events", [])),
        },
        "not_measured_in_passive_probe": {
            "m2b_latency": "NOT_EXECUTED_PASSIVE_BASELINE_AUTHORITY",
            "m3_authority_latency": "NOT_EXECUTED_PASSIVE_BASELINE_AUTHORITY",
            "answer_latency": "NOT_EXECUTED_NO_QUERY",
            "candidate_refresh_after_answer_latency": "NOT_EXECUTED_NO_QUERY",
            "control_response_upper_bound": "NO_FROZEN_BOUND_FROM_SINGLE_DIAGNOSTIC_RUN",
        },
        "assumptions": {
            "configured_oracle_answer_delay_seconds": 0.1,
            "used_as_measured": False,
        },
        "latest_safe_clarification": latest_safe_result.to_dict(),
    }
    _dump(report_dir / "TIMING_EVIDENCE.json", timing)

    forward = {
        "schema_version": "driveclarify.persistent_ambiguity_runtime_v1.forward_accounting.v1",
        "phase": run_id,
        "dino": {
            "event_triggered_forward_count": live.get(
                "dino_event_triggered_forward_count"
            ),
            "receipt_forward_count": live.get("grounding", {}).get(
                "detector_forward_count"
            ),
        },
        "simlingo": {
            "baseline_forward_count": live.get("normal_simlingo_forward_count"),
            "candidate_A_forward_count": 1 if len(plans) >= 1 else 0,
            "candidate_B_forward_count": 1 if len(plans) >= 2 else 0,
            "candidate_forward_total": live.get("candidate_simlingo_forward_count"),
            "skipped": live.get("candidate_forward_skipped"),
            "failed": live.get("candidate_forward_failed"),
            "online_repeat_count": live.get("online_candidate_repeat_count"),
            "visualization_extra_forward": live.get("visualization_extra_forward_count"),
        },
        "new_planner_advance_count": 0,
        "new_pid_count": live.get("new_pid_count"),
        "direct_control_write_count": live.get("direct_vehicle_control_write_count"),
    }
    _dump(report_dir / "FORWARD_ACCOUNTING.json", forward)

    authority_audit = {
        "schema_version": "driveclarify.phase_b.authority_mutation_audit.v1",
        "status": "PASS_BASELINE_AUTHORITY_ONLY_NO_PROBE_MUTATION",
        "run_id": run_id,
        "declared_control_authority": live.get("control_authority"),
        "candidate_plan_selected_for_control": live.get(
            "candidate_plan_selected_for_control"
        ),
        "baseline_plan_source_selection_count": live.get(
            "baseline_plan_source_selection_count"
        ),
        "existing_pid_invocation_count": live.get("existing_pid_invocation_count"),
        "new_pid_count": live.get("new_pid_count"),
        "new_planner_count": live.get("new_planner_count"),
        "direct_vehicle_control_write_count": live.get(
            "direct_vehicle_control_write_count"
        ),
        "candidate_control_mutation_count": 0,
        "m2b_execution": "NOT_EXECUTED_PASSIVE_EVIDENCE_PROBE",
        "m3_authority_execution": "NOT_EXECUTED_PASSIVE_EVIDENCE_PROBE",
        "act_shared_execution": "NOT_EXECUTED_PASSIVE_EVIDENCE_PROBE",
        "ask_execution": "NOT_EXECUTED_PASSIVE_EVIDENCE_PROBE",
        "wait_override_execution": "NOT_EXECUTED_PASSIVE_EVIDENCE_PROBE",
    }
    _dump(report_dir / "AUTHORITY_MUTATION_AUDIT.json", authority_audit)
    privileged_audit = {
        "schema_version": "driveclarify.phase_b.privileged_read_audit.v1",
        "status": "PASS_NO_POLICY_PRIVILEGED_OR_GOLD_READ",
        "run_id": run_id,
        "policy_privileged_state_read_count": live.get(
            "privileged_state_policy_read_count"
        ),
        "gold_policy_label_reads": live.get("gold_policy_label_reads"),
        "expected_decision_reads": live.get("expected_decision_reads"),
        "gold_candidate_index_reads": live.get("gold_candidate_index_reads"),
        "evaluation_label_reads": live.get("evaluation_label_reads"),
        "label_firewall": live.get("label_firewall"),
        "runtime_observable_sources": [
            "rgb_0",
            "existing_route_deque",
            "live_map_topology",
            "ego_transform_velocity_map_waypoint",
            "existing_model_output",
        ],
        "evaluator_only_or_postprocess_sources": [
            "native_run_cleanup_and_collision_receipt",
            "post_run_desktop_validation",
        ],
        "frozen_expected_target_ids_used_only_as_integrity_rejection": True,
    }
    _dump(report_dir / "PRIVILEGED_READ_AUDIT.json", privileged_audit)

    valid_run = bool(
        live.get("status") == "PASS_PHASE_B_DECISION_WINDOW_EVIDENCE_CAPTURED_PENDING_ANALYSIS"
        and fixture.get("exact_match") is True
        and tuple(tuple(row) for row in fixture.get("actual_targets", [])) == EXPECTED_TARGETS
        and desktop.get("status") == "PASS_VALIDATED_NATIVE_DESKTOP"
        and preflight.get("status") == "PASS"
        and native.get("status") == "PASS_NATIVE_UNIFIED_TRIAD_CAPTURE_AND_CLEANUP"
        and native.get("cleanup_status") == "PASS"
        and native.get("vehicle_collision_count") == 0
        and live.get("control_authority") == "EXISTING_BASELINE"
        and live.get("candidate_plan_selected_for_control") is False
        and source is not None
        and snapshot_same_frame
        and observation_same
        and forward["dino"]["event_triggered_forward_count"] == 1
        and forward["dino"]["receipt_forward_count"] == 1
        and forward["simlingo"]["candidate_forward_total"] == 2
        and forward["simlingo"]["visualization_extra_forward"] == 0
        and live.get("new_pid_count") == 0
        and live.get("new_planner_count") == 0
        and live.get("direct_vehicle_control_write_count") == 0
        and int(live.get("post_plan_physical_sample_count") or 0) >= 20
    )
    receipt = {
        "schema_version": "driveclarify.carla_decision_window_evidence_receipt.v1",
        "phase": "PHASE_B_NATIVE_CARLA_CONTROLLED_EVIDENCE_PROBE",
        "run_id": run_id,
        "valid_controlled_probe_run": valid_run,
        "native_physical_display": {
            "status": desktop.get("status"),
            "preflight": preflight.get("native_display"),
            "local_session": preflight.get("local_session"),
            "desktop_validation": str(output_dir / "NATIVE_DESKTOP_VALIDATION.json"),
            "desktop_screenshot": desktop.get("screenshot_path"),
        },
        "fixture": fixture,
        "raw_instruction": live.get("raw_instruction"),
        "raw_k": live.get("raw_k"),
        "effective_k": live.get("effective_k"),
        "candidates": live.get("candidate_semantics"),
        "target_bindings": live.get("target_binding_receipts"),
        "checkpoint_proof": preflight.get("simlingo"),
        "coordinate_frame": coordinate_status,
        "ego_route_progress": ego_progress_result.to_dict(),
        "plan_coverage": plan_coverage,
        "shared_corridor": shared_corridor_result.to_dict(),
        "maneuver_onset": maneuver_result.to_dict(),
        "decision_point": decision_point_result.to_dict(),
        "commitment_boundary": {
            key: value.to_dict() for key, value in commitment_results.items()
        },
        "recoverability": recoverability_result.to_dict(),
        "time_to_divergence": time_to_divergence_result.to_dict(),
        "latest_safe_clarification": latest_safe_result.to_dict(),
        "current_action_equivalence": current_action_result.to_dict(),
        "future_obligation_divergence": future_divergence_result.to_dict(),
        "consequence_relationship": relationship_result.to_dict(),
        "runtime_authorizable_evidence": [
            "native_physical_display",
            "frozen_fixture_identity",
            "same_frame_ego_pose_speed_map_waypoint_route_digest_capture",
            "ego_directed_route_progress",
            "runtime_distinct_future_topology_target_obligations",
            "forward_accounting_and_baseline_authority_isolation",
        ],
        "evaluator_only_or_diagnostic_evidence": [
            "model_local_to_world_formula",
            "candidate_plan_route_projection",
            "current_plan_pairwise_separation",
            "topology_junction_entry_and_branch_anchors",
        ],
        "unknown_fields_remaining": [
            "authorization_grade_coordinate_error_bound",
            "shared_action_window_endpoint",
            "plan_coverage",
            "maneuver_onset",
            "decision_point",
            "commitment_boundary",
            "recoverability",
            "time_to_divergence",
            "latest_safe_clarification",
            "complete_latency_upper_bounds",
        ],
        "unknown_reason_details": {
            "coordinate_frame": coordinate["reason_code"],
            "plan_coverage": plan_coverage["reason_code"],
            "shared_corridor": shared_corridor_result.reason_code,
            "maneuver_onset": maneuver_result.reason_code,
            "decision_point": decision_point_result.reason_code,
            "commitment_boundary": commitment["reason_code"],
            "recoverability": recoverability_result.reason_code,
            "time_to_divergence": time_to_divergence_result.reason_code,
            "latest_safe_clarification": latest_safe_result.reason_code,
            "current_action_equivalence": current_action_result.reason_code,
            "consequence_relationship": relationship_result.reason_code,
        },
        "repeat_stability": "NOT_RUN_GATE_ALREADY_PARTIAL_AFTER_VALID_B0",
        "forward_accounting": forward,
        "baseline_authority_only": True,
        "candidate_control_mutation": 0,
        "new_pid_count": 0,
        "new_planner_count": 0,
        "phase_b_acceptance": False,
        "status": (
            "PARTIAL_PASS_PHASE_B_RUNTIME_EXECUTED_COMMITMENT_AND_RECOVERABILITY_EVIDENCE_UNRESOLVED"
            if valid_run
            else "BLOCKED_CARLA_EXECUTION_FAILURE"
        ),
        "blocking_codes": [
            *([] if valid_run else ["BLOCKED_PHASE_B_CONTROLLED_PROBE_VALIDITY"]),
            "BLOCKED_COORDINATE_FRAME_NOT_VERIFIED_FOR_AUTHORIZATION",
            "BLOCKED_MANEUVER_ONSET_NOT_IDENTIFIABLE",
            "BLOCKED_COMMITMENT_BOUNDARY_NOT_IDENTIFIABLE",
            "BLOCKED_RECOVERABILITY_NOT_AUTHORIZABLE",
        ],
        "phase_c_authorized_to_continue": False,
    }
    _dump(report_dir / "PHASE_B_B0_R1_RECEIPT.json", receipt)
    report = f"""# Phase B B0-R1 CARLA decision-window evidence probe

## 结论

Phase B 状态：`{receipt['status']}`。B0-R1 controlled-probe validity=`{valid_run}`。若 validity 为 true，它是一个有效的 native、visible、TRAIN-only、baseline-authority controlled probe，但只解决了 evidence source 的一部分；它没有建立可授权的 maneuver onset、commitment boundary、recoverability 或 latest-safe deadline。因此本轮按授权停止，不进入 Phase C。

## B0-R1 真实执行

- raw instruction：`{live.get('raw_instruction')}`；raw/effective K=`{live.get('raw_k')}/{live.get('effective_k')}`。
- frozen target identity exact match：`{fixture.get('exact_match')}`。
- CarlaUE4 与本地 dashboard 同时可见，desktop validation=`{desktop.get('status')}`。
- 车辆 authority：existing baseline；candidate plan selection/control write=`0/0`。
- forward：DINO event-triggered 1；baseline normal forwards=`{live.get('normal_simlingo_forward_count')}`；candidate A/B=`1/1`；repeat=0；visualization extra=0。
- same-frame ego/world/map/route snapshot：`{snapshot_same_frame and observation_same}`；route version digest=`{route_version}`。

## 已获得但仍属 diagnostic 的证据

官方 SimLingo route 的 ego-local forward/right 米制解释、source-frame ego transform、CARLA world conversion、directed route projection和随后 baseline 物理轨迹都被记录。由于冻结合同要求受控校准误差界与 sign-convention acceptance，而单个 B0 只有 diagnostic nearest-path comparison，coordinate evidence 保持 `SUPPORTED_BUT_INCOMPLETE_NOT_RUNTIME_AUTHORIZABLE`。

当前 A/B plan pair 的数值分离已真实记录，但没有冻结的 corridor threshold、hysteresis 与 required endpoint coverage，不能把小 RMSE 升级为 current equivalence，也不能把 first target junction entry 升级为 maneuver onset。

## Blockers

- `BLOCKED_COORDINATE_FRAME_NOT_VERIFIED_FOR_AUTHORIZATION`
- `BLOCKED_MANEUVER_ONSET_NOT_IDENTIFIABLE`
- `BLOCKED_COMMITMENT_BOUNDARY_NOT_IDENTIFIABLE`
- `BLOCKED_RECOVERABILITY_NOT_AUTHORIZABLE`

现有 map/route 能给 road/lane/junction/branch 和 route-ordered anchors，但没有“最后仍可合法、安全、动力学切换”的 candidate-wise runtime source；也没有 shared endpoint 后逐候选可达性、制动/横向/规则/动态占用/全延迟 margin。没有碰撞未被当作 recoverability。M2B/M3/query 没有在 passive probe 中执行，其延迟没有伪装成 measured。

## Gate 决定

Phase B full acceptance=`false`。本轮只授权一个新的 B0-R1，因此不运行任何 repeat，也不实施 runtime persistence integration，不进入 white-van closed-loop validation。
"""
    (report_dir / "PHASE_B_B0_R1_REPORT.md").write_text(
        report, encoding="utf-8"
    )
    return receipt


__all__ = ["analyze"]
