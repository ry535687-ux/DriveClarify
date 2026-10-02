"""Narrow replacement contract for the non-executable ORD-LATE-REVEAL scene.

The replacement reuses a previously certified Town12 native-GRP approach but
clips the executed diagnostic route to one straight junction traversal.  The
two candidate tasks remain the first eligible turn (z1) and the second eligible
turn after continuing through the first junction (z2).  The passenger's choice
is deliberately absent.
"""

from __future__ import annotations

import copy
import math
from typing import Any, Mapping, Sequence

from driveclarify_rq2_t.measurement import canonical_sha256
from driveclarify_rq2_t_cg.contracts import (
    CertifiedCandidateBinding,
    assert_no_true_intent,
    validate_candidate_bindings,
)
from driveclarify_rq2_t_cg_ord_async_redesign.contract import (
    CALIBRATION_FREEZE_DIGEST,
    J1_ID,
    J1_STRAIGHT_CONNECTOR_ROAD_ID,
    J1_TURN_CONNECTOR_ROAD_ID,
    J2_ID,
    candidate_source_geometry,
    certify_topology,
)
from driveclarify_rq2_t_cg_v2_calibration.scenes import CALIBRATION_PARAMETERS


ORIGINAL_INSTRUCTION = "Take the turn ahead."
Z1_INTERPRETATION = "Turn at the first eligible upcoming junction."
Z2_INTERPRETATION = (
    "Continue through the first eligible upcoming junction, then turn at the "
    "second eligible upcoming junction."
)

# Candidate paths retain the already certified first/second-junction topology.
# The executed route is only the common approach plus a normal straight passage
# through J1.  No second junction or difficult downstream turn is needed by the
# diagnostic episode.
CANDIDATE_START_INDEX = 20
EXECUTION_STOP_INDEX_EXCLUSIVE = 55
# The offset is fixed prospectively from the already valid, unchanged native
# ORD approach trace: it places first possible reveal near 0.6 s TTCmt, safely
# inside (0, 1.20) and the requested ~0.4--1.0 s target band.
EVENT_TTC_DISTANCE_M = 1.8
EVENT_WINDOW_WIDTH_M = 0.4
ADMINISTRATIVE_CAP_SIMULATION_S = 120.0


def _xyz(row: Mapping[str, Any]) -> tuple[float, float, float]:
    return tuple(float(row[key]) for key in ("x", "y", "z"))


def _arc_positions(rows: Sequence[Mapping[str, Any]]) -> list[float]:
    values = [0.0]
    for left, right in zip(rows, rows[1:]):
        values.append(values[-1] + math.dist(_xyz(left), _xyz(right)))
    return values


def _max_gap(rows: Sequence[Mapping[str, Any]]) -> float:
    return max(math.dist(_xyz(left), _xyz(right)) for left, right in zip(rows, rows[1:]))


def candidate_geometry() -> Mapping[str, Any]:
    """Return the short execution route and full certified candidate paths."""
    source = candidate_source_geometry()
    z1 = copy.deepcopy(source["z1"]["route_coordinates"][CANDIDATE_START_INDEX:])
    z2 = copy.deepcopy(source["z2"]["route_coordinates"][CANDIDATE_START_INDEX:])
    executed = copy.deepcopy(
        source["z2"]["route_coordinates"][
            CANDIDATE_START_INDEX:EXECUTION_STOP_INDEX_EXCLUSIVE
        ]
    )
    z1_arc, z2_arc, executed_arc = _arc_positions(z1), _arc_positions(z2), _arc_positions(executed)
    source_z2_arc = _arc_positions(source["z2"]["route_coordinates"])
    offset_m = source_z2_arc[CANDIDATE_START_INDEX]
    commitment_m = float(source["commitment_boundary"]["route_arc_length_m"]) - offset_m
    j1_m = float(source["j1_route_arc_length_m"]) - offset_m
    j2_m = float(source["j2_route_arc_length_m"]) - offset_m
    shared_end_m = float(source["shared_prefix_end"]["route_arc_length_m"]) - offset_m
    value = {
        "source_geometry_digest": source["geometry_digest"],
        "source_start_index": CANDIDATE_START_INDEX,
        "execution_stop_index_exclusive": EXECUTION_STOP_INDEX_EXCLUSIVE,
        "same_ego_state": copy.deepcopy(executed[0]),
        "shared_prefix": {
            "start_route_arc_length_m": 0.0,
            "end_route_arc_length_m": shared_end_m,
            "end_pose": copy.deepcopy(source["shared_prefix_end"]["pose"]),
        },
        "first_candidate_task_divergence": {
            **copy.deepcopy(source["first_candidate_task_divergence"]),
            "route_arc_length_m": j1_m,
        },
        "commitment_boundary": {
            **copy.deepcopy(source["commitment_boundary"]),
            "route_arc_length_m": commitment_m,
        },
        "j1_route_arc_length_m": j1_m,
        "j2_route_arc_length_m": j2_m,
        "executed_route": {
            "route_coordinates": executed,
            "point_count": len(executed),
            "route_length_m": executed_arc[-1],
            "maximum_adjacent_gap_m": _max_gap(executed),
            "maneuver": "STRAIGHT_THROUGH_J1_ON_CONNECTOR_13968",
            "postcommitment_margin_m": executed_arc[-1] - commitment_m,
        },
        "z1": {
            "interpretation": Z1_INTERPRETATION,
            "route_coordinates": z1,
            "point_count": len(z1),
            "route_length_m": z1_arc[-1],
            "maximum_adjacent_gap_m": _max_gap(z1),
            "junction_obligation": {"turn_junction_id": J1_ID},
        },
        "z2": {
            "interpretation": Z2_INTERPRETATION,
            "route_coordinates": z2,
            "point_count": len(z2),
            "route_length_m": z2_arc[-1],
            "maximum_adjacent_gap_m": _max_gap(z2),
            "junction_obligation": {
                "pass_junction_id": J1_ID,
                "turn_junction_id": J2_ID,
            },
        },
    }
    for key in ("executed_route", "z1", "z2"):
        value[key]["geometry_digest"] = canonical_sha256(value[key])
    value["geometry_digest"] = canonical_sha256(value)
    return value


def _event(
    event_id: str, start_m: float, end_m: float,
) -> Mapping[str, Any]:
    value = {
        "event_id": event_id,
        "event_kind": "DECISIVE_LATE_REVEAL",
        "activation": {
            "coordinate": "route_progress_m",
            "start_inclusive_m": start_m,
            "end_exclusive_m": end_m,
        },
        "owner": "CERTIFIED_ORDERED_J1_J2_NATIVE_TOPOLOGY_EVENT_OWNER_V1",
        "payload": {
            "fields": ["E2_GROUNDING", "E5_FUTURE_OBLIGATION"],
            "ordered_task_events": [
                {"order": 1, "junction_id": J1_ID, "task": "TURN_AT_J1"},
                {"order": 2, "junction_id": J2_ID, "task": "PASS_J1_THEN_TURN_AT_J2"},
            ],
            "passenger_selection": "UNKNOWN",
            "gold_reveal": False,
        },
        "reads_view": False,
        "reads_outcome": False,
        "reads_passenger_intent": False,
    }
    value["event_digest"] = canonical_sha256(value)
    return value


def build_replacement_scene(
    *, scene_identity: str, execution_class: str,
    formal_denominator_eligible: bool,
) -> Mapping[str, Any]:
    """Build a fresh identity under the single frozen replacement geometry."""
    if execution_class not in (
        "RQ2_T_CG_V2_ENGINEERING_SEAM_ONLY",
        "RQ2_T_CG_FORMAL_V2_CONFIRMATORY",
    ):
        raise ValueError("ORD_LATE_REVEAL_REPLACEMENT_EXECUTION_CLASS_INVALID")
    geometry = candidate_geometry()
    commitment_m = float(geometry["commitment_boundary"]["route_arc_length_m"])
    event_start_m = commitment_m - EVENT_TTC_DISTANCE_M
    event_end_m = event_start_m + EVENT_WINDOW_WIDTH_M
    bindings = [
        CertifiedCandidateBinding(
            candidate_id=scene_identity + "-C1",
            interpretation_id=scene_identity + "-I1",
            interpretation_text=Z1_INTERPRETATION,
            binding_id=scene_identity + "-B1",
            entity_or_task_role="task:first_eligible_forward_junction_J1_13956",
            obligation_descriptor="turn at first eligible upcoming junction J1=13956",
            binding_kind="CERTIFIED_TASK_TO_NATIVE_TOPOLOGY",
        ).to_dict(),
        CertifiedCandidateBinding(
            candidate_id=scene_identity + "-C2",
            interpretation_id=scene_identity + "-I2",
            interpretation_text=Z2_INTERPRETATION,
            binding_id=scene_identity + "-B2",
            entity_or_task_role="task:pass_J1_then_turn_at_second_eligible_forward_junction_J2_8474",
            obligation_descriptor="continue through J1=13956 and turn at J2=8474",
            binding_kind="CERTIFIED_TASK_TO_NATIVE_TOPOLOGY",
        ).to_dict(),
    ]
    route = {
        "town": "Town12",
        "waypoints": copy.deepcopy(geometry["executed_route"]["route_coordinates"]),
        "route_length_m": float(geometry["executed_route"]["route_length_m"]),
        "mechanism_polyline_source": "reports/driveclarify_a1_integrated_route_switch_smoke_v1/candidates/DC-A1-SMOKE-02-RIGHT/runtime_case.json",
        "source_route_key": "old_full_route",
        "source_slice_half_open": [660 + CANDIDATE_START_INDEX, 660 + EXECUTION_STOP_INDEX_EXCLUSIVE],
        "canonical_executable_route_owner": "BENCH2DRIVE_GLOBAL_ROUTE_PLANNER_TRACE_V2",
        "candidate_path_owner": "CERTIFIED_TOWN12_NATIVE_TOPOLOGY_AND_GRP",
        "native_route_materialized": False,
        "exact_prior_full_route_reused": False,
        "prospective_replacement": True,
        "diagnostic_route_clipped_after_J1": True,
        "dense_native_grp_pair_collapse_authorized": True,
        "calibration_scene_overlap": False,
        "v2_execution_route_instance_id": scene_identity + "-ROUTE",
    }
    route["route_spec_digest"] = canonical_sha256(route)
    scene = {
        "schema_version": "driveclarify.rq2_t_cg.ord_late_reveal_replacement_scene.v1",
        "formal_scene_id": scene_identity,
        "scene_code": "ORD-LATE-REVEAL",
        "scene_family": "ORD",
        "instruction": ORIGINAL_INSTRUCTION,
        "timing_design": "LATE_AFTER_DEADLINE_BEFORE_COMMITMENT",
        "expected_integrity_property": "A topology-owned joint decisive event becomes informative only after the 1.20 s clarification deadline but remains strictly before the first z1 task-recoverability boundary; scientific B1/B2 outcomes are descriptive only.",
        "execution_class": execution_class,
        "calibration_only": False,
        "formal_denominator_eligible": bool(formal_denominator_eligible),
        "future_formal_v2_excluded": not bool(formal_denominator_eligible),
        "engineering_scene_promoted": False,
        "selected_calibration_configuration_id": "RQ2TCG-V2-CAL-EARLY-ASYNC-REVEAL-02",
        "selected_calibration_freeze_digest": CALIBRATION_FREEZE_DIGEST,
        "selected_mechanism_parameters": copy.deepcopy(CALIBRATION_PARAMETERS),
        "prospective_replacement_scope": "ORD_LATE_REVEAL_ONLY",
        "candidate_certification": {
            "minimum_reasonable_candidates": 2,
            "independent_reasonableness_basis": "Native topology exposes a legal J1 turn and a legal J1-straight/J2-turn task from one common ego state.",
            "passenger_intent_identified": False,
            "runtime_correct_candidate_available": False,
            "production_deployable": False,
        },
        "candidate_bindings": bindings,
        "candidate_task_paths": {
            "z1": copy.deepcopy(geometry["z1"]),
            "z2": copy.deepcopy(geometry["z2"]),
            "shared_prefix": copy.deepcopy(geometry["shared_prefix"]),
            "first_candidate_task_divergence": copy.deepcopy(
                geometry["first_candidate_task_divergence"]
            ),
        },
        "route": route,
        "actor_or_task_layout": [
            {
                "layout_id": scene_identity + "-LAYOUT-01",
                "kind": "CERTIFIED_TASK_JUNCTION",
                "junction_id": J1_ID,
                "route_progress_m": geometry["j1_route_arc_length_m"],
                "lateral_offset_m": 0.0,
                "formal_denominator_eligible": bool(formal_denominator_eligible),
            },
            {
                "layout_id": scene_identity + "-LAYOUT-02",
                "kind": "CERTIFIED_TASK_JUNCTION",
                "junction_id": J2_ID,
                "route_progress_m": geometry["j2_route_arc_length_m"],
                "lateral_offset_m": 0.0,
                "formal_denominator_eligible": bool(formal_denominator_eligible),
            },
        ],
        "events": [_event(scene_identity + "-EVENT-01", event_start_m, event_end_m)],
        "commitment": {
            "coordinate": "route_progress_m",
            "threshold_m": commitment_m,
            "owner": "J1_Z1_TASK_RECOVERABILITY_BOUNDARY_OWNER_V2",
            "pose": copy.deepcopy(geometry["commitment_boundary"]["pose"]),
            "road_id": J1_STRAIGHT_CONNECTOR_ROAD_ID,
            "lane_id": 1,
            "junction_id": J1_ID,
            "topological_reason": "The ego has entered J1's straight-only connector 13968; z1's right-turn connector 14005 is no longer reachable without reversing, an illegal maneuver, or discontinuous replanning.",
            "recoverability_proof": {
                "before_boundary": "both J1 connector choices share incoming road 587 lane 1",
                "after_boundary": "connector 13968 has successor road 586, while connector 14005 has successor road 1090; no forward native topology edge joins them inside J1",
                "prohibited_recovery_required": [
                    "reverse", "illegal_lane_or_junction_maneuver", "discontinuous_replan"
                ],
            },
            "reads_view": False,
            "reads_outcome": False,
        },
        "deadline": {
            "definition": "commitment_simulation_time_s - exact frozen DeadlineContract.total_reserved_simulation_s",
            "total_reserved_simulation_s": 1.2,
            "contract_change_authorized": False,
        },
        "horizon": {
            "natural_end": "first source frame at or after commitment plus 1.0 CARLA simulation second",
            "administrative_cap_simulation_s": ADMINISTRATIVE_CAP_SIMULATION_S,
            "administrative_cap": "120.0 CARLA simulation seconds after first eligible source frame",
            "administrative_cap_scientific_event": False,
            "administrative_cap_basis": "Infrastructure-only containment; it is not T-FIXED, TTCmt, the analysis horizon, or an evidence event.",
        },
        "termination": {
            "success": "natural horizon observed with complete paired source trace and native evaluator route completion",
            "censor": "commitment/horizon missing, source identity divergence, certificate breach, or infrastructure termination",
            "zero_imputation_for_censoring": False,
        },
        "prospective_late_event_design": {
            "event_distance_before_commitment_m": EVENT_TTC_DISTANCE_M,
            "target_first_possible_reveal_TTCmt_s": [0.4, 1.0],
            "hard_acceptance_TTCmt_s": {"lower_exclusive": 0.0, "upper_exclusive": 1.2},
            "scientific_outcome_gate": False,
        },
        "formal_seed_values": [],
        "formal_seed_values_generated": 0,
        "formal_episode_count": 0,
        "formal_scientific_exposures": 0,
        "native_materialization_count": 0,
        "online_ask_count": 0,
        "no_ordinal_language_comprehension_claim": True,
    }
    validate_candidate_bindings(scene["candidate_bindings"])
    assert_no_true_intent(scene)
    scene["formal_scene_digest"] = canonical_sha256(scene)
    return scene


def replacement_science_signature(scene: Mapping[str, Any]) -> str:
    """Identity-independent signature used to freeze the replacement science."""
    value = {
        "scene_code": scene["scene_code"],
        "scene_family": scene["scene_family"],
        "instruction": scene["instruction"],
        "timing_design": scene["timing_design"],
        "expected_integrity_property": scene["expected_integrity_property"],
        "candidate_bindings": [
            {
                key: row[key]
                for key in (
                    "interpretation_text", "entity_or_task_role",
                    "obligation_descriptor", "binding_kind",
                    "passenger_intent_identified",
                )
            }
            for row in scene["candidate_bindings"]
        ],
        "candidate_task_paths": scene["candidate_task_paths"],
        "route_waypoints": scene["route"]["waypoints"],
        "route_length_m": scene["route"]["route_length_m"],
        "events": [
            {
                key: row[key]
                for key in (
                    "event_kind", "activation", "owner", "payload", "reads_view",
                    "reads_outcome", "reads_passenger_intent",
                )
            }
            for row in scene["events"]
        ],
        "commitment": scene["commitment"],
        "deadline": scene["deadline"],
        "natural_horizon": scene["horizon"]["natural_end"],
        "late_event_design": scene["prospective_late_event_design"],
        "selected_calibration_freeze_digest": scene["selected_calibration_freeze_digest"],
        "selected_mechanism_parameters": scene["selected_mechanism_parameters"],
    }
    return canonical_sha256(value)


def certify_replacement_topology() -> Mapping[str, Any]:
    """Run the native-map/GRP offline certificate before any seed exposure."""
    topology = certify_topology()
    geometry = candidate_geometry()
    checks = {
        "upstream_native_topology_certificate_pass": topology["status"] == "PASS_ORD_ASYNC_NATIVE_TOPOLOGY_CERTIFICATION",
        "j1_j2_forward_order_certified": geometry["j1_route_arc_length_m"] < geometry["j2_route_arc_length_m"],
        "z1_turns_at_j1": topology["checks"]["z1_turns_at_j1"],
        "z2_continues_at_j1_then_turns_at_j2": topology["checks"]["z2_continues_through_j1"] and topology["checks"]["z2_reaches_then_turns_at_j2"],
        "z1_continuous": geometry["z1"]["maximum_adjacent_gap_m"] < 2.1,
        "z2_continuous": geometry["z2"]["maximum_adjacent_gap_m"] < 2.1,
        "executed_route_continuous": geometry["executed_route"]["maximum_adjacent_gap_m"] < 2.1,
        "single_simple_executed_maneuver": geometry["executed_route"]["maneuver"] == "STRAIGHT_THROUGH_J1_ON_CONNECTOR_13968",
        "commitment_reachable": 0.0 < geometry["commitment_boundary"]["route_arc_length_m"] < geometry["executed_route"]["route_length_m"],
        "natural_horizon_margin": geometry["executed_route"]["postcommitment_margin_m"] > 10.0,
        "candidate_paths_share_exact_ego_state": geometry["z1"]["route_coordinates"][0] == geometry["z2"]["route_coordinates"][0] == geometry["executed_route"]["route_coordinates"][0],
    }
    value = {
        "schema_version": "driveclarify.rq2_t_cg.ord_late_reveal_replacement_topology.v1",
        "status": "PASS_ORD_LATE_REVEAL_REPLACEMENT_TOPOLOGY" if all(checks.values()) else "FAIL_ORD_LATE_REVEAL_REPLACEMENT_TOPOLOGY",
        "map": "Town12",
        "ordering_coordinate": "FORWARD_ROUTE_ARC_LENGTH_FROM_COMMON_EGO_STATE",
        "euclidean_nearest_ordering_used": False,
        "J1": topology["J1"],
        "J2": topology["J2"],
        "topology_connectivity": topology["topology_connectivity"],
        "candidate_geometry": geometry,
        "upstream_topology_receipt_digest": topology["receipt_digest"],
        "checks": checks,
        "failed_checks": [key for key, passed in checks.items() if not passed],
    }
    assert_no_true_intent(value)
    value["receipt_digest"] = canonical_sha256(value)
    return value


__all__ = [
    "ADMINISTRATIVE_CAP_SIMULATION_S",
    "CALIBRATION_FREEZE_DIGEST",
    "EVENT_TTC_DISTANCE_M",
    "ORIGINAL_INSTRUCTION",
    "Z1_INTERPRETATION",
    "Z2_INTERPRETATION",
    "build_replacement_scene",
    "candidate_geometry",
    "certify_replacement_topology",
    "replacement_science_signature",
]
