"""Prospectively fixed scene templates for the RQ2 robustness extension.

This module only constructs and statically certifies controlled experimental
operands.  It does not execute episodes, inspect outcomes, or expose a true
passenger intent.  The accepted RQ2 B1/B2 builder remains the sole mechanism
evaluator.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Mapping, Sequence

from driveclarify_rq2_t.measurement import canonical_sha256
from driveclarify_rq2_t_cg.contracts import (
    CertifiedCandidateBinding,
    assert_no_true_intent,
    validate_candidate_bindings,
)


ROOT = Path(__file__).resolve().parents[1]
HISTORICAL_REPORT = (
    ROOT / "reports" / "driveclarify_rq2_t_cg_formal_v3_prospective_evaluability_and_execution_v1"
)
SCENE_ORDER = (
    "REF-ACTIONABLE",
    "REF-TOO-LATE",
    "LMK-ACTIONABLE",
    "LMK-TOO-LATE",
    "ORD-ACTIONABLE",
    "ORD-TOO-LATE",
)
BASE_ROUTES = {
    "REF": ROOT / "driveclarify_rq2_t" / "formal_routes" / "ref-01.xml",
    "LMK": ROOT / "driveclarify_rq2_t" / "formal_routes" / "lmk-01.xml",
}
ACCEPTED_ORD_SCENE = HISTORICAL_REPORT / "FORMAL_V3_SCENES" / "ORD-ASYNC.json"
ACCEPTED_ORD_TOPOLOGY = (
    ROOT / "reports" / "driveclarify_rq2_t_cg_v2_ord_async_redesign_and_formal_v1"
    / "ORD_ASYNC_JUNCTION_ORDER_RECEIPT.json"
)
HISTORICAL_FREEZE_DIGEST = "113843acf014be1db402b78f5618a92a0e5505a35d20079806c879080e4d17d3"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _polyline_length(points: Sequence[Mapping[str, Any]]) -> float:
    return sum(
        math.dist(
            tuple(float(left[axis]) for axis in ("x", "y", "z")),
            tuple(float(right[axis]) for axis in ("x", "y", "z")),
        )
        for left, right in zip(points, points[1:])
    )


def _xml_route(family: str, start: int, stop: int, identity: str) -> Mapping[str, Any]:
    source = BASE_ROUTES[family]
    route = ET.parse(str(source)).getroot().find("route")
    if route is None:
        raise RuntimeError("RQ2_EXTENSION_BASE_ROUTE_MISSING")
    points = [
        {axis: float(node.attrib[axis]) for axis in ("x", "y", "z")}
        for node in route.findall("./waypoints/position")
    ][start:stop]
    value = {
        "town": str(route.attrib["town"]),
        "mechanism_polyline_source": str(source.relative_to(ROOT)),
        "source_slice_half_open": [start, stop],
        "waypoints": points,
        "route_length_m": _polyline_length(points),
        "route_instance_id": identity + "-ROUTE",
        "native_route_materialized": False,
        "exact_historical_full_route_reused": False,
        "historical_rq2_denominator_eligible": False,
    }
    value["route_spec_digest"] = canonical_sha256(value)
    return value


def _ord_route(start: int, stop: int, identity: str) -> tuple[Mapping[str, Any], Mapping[str, float]]:
    accepted = _load(ACCEPTED_ORD_SCENE)
    all_points = accepted["route"]["waypoints"]
    points = copy.deepcopy(all_points[start:stop])
    prefix = _polyline_length(all_points[: start + 1])
    coordinates = {
        "j1_progress_m": 38.19126033756351 - prefix,
        "j2_progress_m": 86.916858119298 - prefix,
        "commitment_progress_m": 39.19176947748733 - prefix,
    }
    route = {
        "town": "Town12",
        "mechanism_polyline_source": accepted["route"]["mechanism_polyline_source"],
        "source_route_key": accepted["route"].get("source_route_key", "old_full_route"),
        "source_slice_half_open": [660 + start, 660 + stop],
        "waypoints": points,
        "route_length_m": _polyline_length(points),
        "route_instance_id": identity + "-ROUTE",
        "candidate_path_owner": "ACCEPTED_CERTIFIED_TOWN12_NATIVE_TOPOLOGY_AND_GRP",
        "canonical_executable_route_owner": "BENCH2DRIVE_GLOBAL_ROUTE_PLANNER_TRACE_V2",
        "native_route_materialized": False,
        "exact_historical_full_route_reused": False,
        "historical_rq2_denominator_eligible": False,
    }
    route["route_spec_digest"] = canonical_sha256(route)
    return route, coordinates


def _event(
    identity: str,
    number: int,
    kind: str,
    start: float,
    stop: float,
    owner: str,
    payload: Mapping[str, Any],
) -> Mapping[str, Any]:
    value = {
        "event_id": f"{identity}-EVENT-{number:02d}",
        "event_kind": kind,
        "activation": {
            "coordinate": "route_progress_m",
            "start_inclusive_m": float(start),
            "end_exclusive_m": float(stop),
        },
        "owner": owner,
        "payload": dict(payload),
        "reads_view": False,
        "reads_outcome": False,
        "reads_passenger_intent": False,
    }
    value["event_digest"] = canonical_sha256(value)
    return value


def _bindings(
    identity: str,
    instruction: str,
    rows: Sequence[tuple[str, str, str, str, str]],
) -> list[Mapping[str, Any]]:
    bindings = []
    for suffix, interpretation, role, obligation, kind in rows:
        bindings.append(
            CertifiedCandidateBinding(
                candidate_id=f"{identity}-C{suffix}",
                interpretation_id=f"{identity}-I{suffix}",
                interpretation_text=f"{instruction} Interpretation {suffix}: {interpretation}",
                binding_id=f"{identity}-B{suffix}",
                entity_or_task_role=role,
                obligation_descriptor=obligation,
                binding_kind=kind,
            ).to_dict()
        )
    validate_candidate_bindings(bindings)
    return bindings


def _scene(
    *,
    code: str,
    identity: str,
    family: str,
    timing: str,
    route: Mapping[str, Any],
    instruction: str,
    candidate_rows: Sequence[tuple[str, str, str, str, str]],
    layout: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
    commitment_m: float,
    evidence_order: Sequence[str],
    rationale: str,
    extra: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    value = {
        "schema_version": (
            "driveclarify.rq2_t_cg.ord_async_prospective_redesign_scene.v1"
            if family == "ORD"
            else "driveclarify.rq2_t_cg.v2_postcalibration_scene.v1"
        ),
        # The accepted loader name is retained solely as an execution-interface
        # compatibility tag.  These rows are extension-only and never enter V2/V3.
        "execution_class": "RQ2_T_CG_FORMAL_V2_CONFIRMATORY",
        "extension_stage": "RQ2_ACTIONABLE_WINDOW_ROBUSTNESS_EXTENSION_V1",
        "extension_denominator_eligible": True,
        "historical_rq2_denominator_eligible": False,
        "calibration_only": False,
        "formal_denominator_eligible": True,
        "future_formal_v2_excluded": False,
        "scene_code": code,
        "formal_scene_id": identity,
        "scene_family": family,
        "timing_design": f"ASYNCHRONOUS_{timing}",
        "timing_stratum": timing,
        "instruction": instruction,
        "candidate_bindings": _bindings(identity, instruction, candidate_rows),
        "candidate_certification": {
            "minimum_reasonable_candidates": 2,
            "independent_reasonableness_basis": (
                "Two controlled interpretations bind to distinct visible entities or certified "
                "ordered route obligations and require different future maneuvers."
            ),
            "passenger_intent_identified": False,
            "runtime_correct_candidate_available": False,
            "production_deployable": False,
        },
        "route": copy.deepcopy(dict(route)),
        "actor_or_task_layout": [copy.deepcopy(dict(row)) for row in layout],
        "events": [copy.deepcopy(dict(row)) for row in events],
        "relevant_controlled_evidence_fields": [
            "E1_INTERPRETATION_VALIDITY",
            "E2_GROUNDING",
            "E4_FUTURE_OBLIGATION_RELATION",
            "E5_ROUTE_LANE_TOPOLOGY_RELATION",
            "E6_CANDIDATE_CONSEQUENCE_DIVERGENCE",
            "E7_SAFETY_RULE_HOLDING",
            "E9_ANSWER_CHANGES_ACTION",
        ],
        "evidence_arrival_mechanism": "route-progress-owned certified events on one native trace",
        "expected_evidence_order_structure": list(evidence_order),
        "commitment": {
            "coordinate": "route_progress_m",
            "threshold_m": float(commitment_m),
            "owner": "FROZEN_ROUTE_TOPOLOGY_CROSSING_OWNER_V1",
            "reads_view": False,
            "reads_outcome": False,
        },
        "deadline": {
            "definition": "commitment_simulation_time_s - frozen total reserve",
            "answer_simulation_s": 0.50,
            "answer_to_action_simulation_s": 0.55,
            "control_reserve_simulation_s": 0.15,
            "total_reserved_simulation_s": 1.20,
            "contract_change_authorized": False,
        },
        "horizon": {
            "natural_end": "first source frame at or after commitment plus 1.0 CARLA simulation second",
            "administrative_cap_simulation_s": 120.0,
            "administrative_cap_scientific_event": False,
        },
        "route_owner_binding": {
            "scientific_progress_owner": "FROZEN_CERTIFIED_SOURCE_POLYLINE_V1_UNCHANGED",
            "runtime_route_owner": "BENCH2DRIVE_GLOBAL_ROUTE_PLANNER_TRACE_V2",
            "candidate_binding_owner": "CERTIFIED_CANDIDATE_EVIDENCE_INTERFACE_V1",
        },
        "prospective_timing_rationale": rationale,
        "stratum_is_prospective_not_outcome_label": True,
        "formal_seed_values": [],
        "formal_seed_values_generated": 0,
        "formal_episode_count": 0,
        "formal_scientific_exposures": 0,
        "online_ask_count": 0,
        "native_materialization_count": 0,
        "engineering_scene_promoted": False,
    }
    if extra:
        value.update(copy.deepcopy(dict(extra)))
    value["formal_scene_digest"] = canonical_sha256(value)
    assert_no_true_intent(value)
    return value


def build_scenes() -> tuple[Mapping[str, Any], ...]:
    scenes: list[Mapping[str, Any]] = []

    identity = "RQ2EXT-V1-REF-ACTIONABLE-RIVERSIDE-01"
    route = _xml_route("REF", 0, 100, identity)
    scenes.append(_scene(
        code="REF-ACTIONABLE", identity=identity, family="REF", timing="ACTIONABLE",
        route=route, instruction="Turn into the lane just beyond the maintenance pickup.",
        candidate_rows=(
            ("1", "the nearer curbside maintenance pickup is the referent", "entity:near_curbside_maintenance_pickup", "turn into the nearer post-pickup lane", "CERTIFIED_ENTITY"),
            ("2", "the farther median-side maintenance pickup is the referent", "entity:far_median_maintenance_pickup", "continue to the farther post-pickup lane", "CERTIFIED_ENTITY"),
        ),
        layout=(
            {"layout_id": identity + "-LAYOUT-01", "kind": "CERTIFIED_ENTITY", "role": "near curbside maintenance pickup", "route_progress_m": 1.25, "lateral_offset_m": 3.6},
            {"layout_id": identity + "-LAYOUT-02", "kind": "CERTIFIED_ENTITY", "role": "far median-side maintenance pickup", "route_progress_m": 5.9, "lateral_offset_m": -3.4},
        ),
        events=(
            _event(identity, 1, "GROUNDING_REVEAL", 1.0, 1.6, "CERTIFIED_NATIVE_VISIBILITY_OWNER_V1", {"field": "E2_GROUNDING", "mechanism": "relative_position_plus_identity"}),
            _event(identity, 2, "OBLIGATION_REVEAL", 2.0, 2.6, "CERTIFIED_ROUTE_OBLIGATION_OWNER_V1", {"fields": ["E4_FUTURE_OBLIGATION_RELATION", "E5_ROUTE_LANE_TOPOLOGY_RELATION", "E6_CANDIDATE_CONSEQUENCE_DIVERGENCE", "E9_ANSWER_CHANGES_ACTION"]}),
        ),
        commitment_m=14.0,
        evidence_order=("referent identity and relative position", "post-referent route obligation"),
        rationale="Disjoint reveals make B1 coincidence unavailable while leaving over 11 m of designed route margin after the decisive event.",
    ))

    identity = "RQ2EXT-V1-REF-TOO-LATE-DEPOT-01"
    route = _xml_route("REF", 100, 190, identity)
    scenes.append(_scene(
        code="REF-TOO-LATE", identity=identity, family="REF", timing="TOO-LATE",
        route=route, instruction="Use the entrance after the road-inspection van.",
        candidate_rows=(
            ("1", "the first shoulder-side inspection van is the referent", "entity:first_shoulder_inspection_van", "take the first entrance beyond the shoulder van", "CERTIFIED_ENTITY"),
            ("2", "the later lane-side inspection van is the referent", "entity:later_lane_inspection_van", "pass the first entrance and use the later one", "CERTIFIED_ENTITY"),
        ),
        layout=(
            {"layout_id": identity + "-LAYOUT-01", "kind": "CERTIFIED_ENTITY", "role": "first shoulder-side inspection van", "route_progress_m": 8.25, "lateral_offset_m": 3.3},
            {"layout_id": identity + "-LAYOUT-02", "kind": "CERTIFIED_ENTITY", "role": "later lane-side inspection van", "route_progress_m": 9.35, "lateral_offset_m": -3.1},
        ),
        events=(
            _event(identity, 1, "OBLIGATION_REVEAL", 8.0, 8.6, "CERTIFIED_ROUTE_OBLIGATION_OWNER_V1", {"fields": ["E4_FUTURE_OBLIGATION_RELATION", "E5_ROUTE_LANE_TOPOLOGY_RELATION", "E6_CANDIDATE_CONSEQUENCE_DIVERGENCE", "E9_ANSWER_CHANGES_ACTION"]}),
            _event(identity, 2, "GROUNDING_REVEAL", 9.1, 9.6, "CERTIFIED_NATIVE_VISIBILITY_OWNER_V1", {"field": "E2_GROUNDING", "mechanism": "late_identity_confirmation"}),
        ),
        commitment_m=10.0,
        evidence_order=("route obligation", "late referent identity confirmation"),
        rationale="The final grounding reveal begins 0.9 m before commitment, prospectively creating a natural late-evidence opportunity without changing TTCmt after exposure.",
    ))

    identity = "RQ2EXT-V1-LMK-ACTIONABLE-CANAL-01"
    route = _xml_route("LMK", 0, 100, identity)
    scenes.append(_scene(
        code="LMK-ACTIONABLE", identity=identity, family="LMK", timing="ACTIONABLE",
        route=route, instruction="Enter the access road after the green canal marker.",
        candidate_rows=(
            ("1", "the low green canal marker identifies the nearer access road", "entity:low_green_canal_marker", "enter the nearer canal-side access", "CERTIFIED_ENTITY"),
            ("2", "the elevated green wayfinding panel identifies the farther access road", "entity:elevated_green_wayfinding_panel", "continue to the farther signed access", "CERTIFIED_ENTITY"),
        ),
        layout=(
            {"layout_id": identity + "-LAYOUT-01", "kind": "CERTIFIED_ENTITY", "role": "low green canal marker", "route_progress_m": 0.15, "lateral_offset_m": 3.1},
            {"layout_id": identity + "-LAYOUT-02", "kind": "CERTIFIED_ENTITY", "role": "elevated green wayfinding panel", "route_progress_m": 3.8, "lateral_offset_m": -3.0},
        ),
        events=(
            _event(identity, 1, "OBLIGATION_REVEAL", 0.0, 0.25, "CERTIFIED_ROUTE_OBLIGATION_OWNER_V1", {"fields": ["E4_FUTURE_OBLIGATION_RELATION", "E5_ROUTE_LANE_TOPOLOGY_RELATION", "E6_CANDIDATE_CONSEQUENCE_DIVERGENCE", "E9_ANSWER_CHANGES_ACTION"]}),
            _event(identity, 2, "GROUNDING_REVEAL", 0.30, 0.70, "CERTIFIED_NATIVE_VISIBILITY_OWNER_V1", {"field": "E2_GROUNDING", "mechanism": "landmark_identity_after_access_geometry"}),
        ),
        commitment_m=8.0,
        evidence_order=("access-road obligation", "landmark identity"),
        rationale="A reversed evidence order and new canal geometry provide a legal B2 opportunity well before the frozen deadline.",
    ))

    identity = "RQ2EXT-V1-LMK-TOO-LATE-TRAM-01"
    route = _xml_route("LMK", 10, 110, identity)
    scenes.append(_scene(
        code="LMK-TOO-LATE", identity=identity, family="LMK", timing="TOO-LATE",
        route=route, instruction="Take the driveway after the amber tram-stop panel.",
        candidate_rows=(
            ("1", "the nearer amber platform panel marks the first driveway", "entity:near_amber_platform_panel", "take the first driveway after the near panel", "CERTIFIED_ENTITY"),
            ("2", "the farther amber transfer panel marks the second driveway", "entity:far_amber_transfer_panel", "continue to the second driveway after the far panel", "CERTIFIED_ENTITY"),
        ),
        layout=(
            {"layout_id": identity + "-LAYOUT-01", "kind": "CERTIFIED_ENTITY", "role": "near amber platform panel", "route_progress_m": 5.55, "lateral_offset_m": 3.4},
            {"layout_id": identity + "-LAYOUT-02", "kind": "CERTIFIED_ENTITY", "role": "far amber transfer panel", "route_progress_m": 6.35, "lateral_offset_m": -3.2},
        ),
        events=(
            _event(identity, 1, "GROUNDING_REVEAL", 5.3, 5.8, "CERTIFIED_NATIVE_VISIBILITY_OWNER_V1", {"field": "E2_GROUNDING", "mechanism": "landmark_panel_identity"}),
            _event(identity, 2, "OBLIGATION_REVEAL", 6.1, 6.5, "CERTIFIED_ROUTE_OBLIGATION_OWNER_V1", {"fields": ["E4_FUTURE_OBLIGATION_RELATION", "E5_ROUTE_LANE_TOPOLOGY_RELATION", "E6_CANDIDATE_CONSEQUENCE_DIVERGENCE", "E9_ANSWER_CHANGES_ACTION"]}),
        ),
        commitment_m=6.9,
        evidence_order=("landmark identity", "late driveway topology and obligation"),
        rationale="The decisive obligation reveal ends only 0.4 m before the fixed commitment crossing, making sufficiency potentially late by natural progress timing.",
    ))

    identity = "RQ2EXT-V1-ORD-ACTIONABLE-TWO-JUNCTION-01"
    route, ord_coordinates = _ord_route(5, 141, identity)
    commitment = ord_coordinates["commitment_progress_m"]
    ord_certification = {
        "ordered_alternative_topology_certification": {
            "source_certificate": str(ACCEPTED_ORD_TOPOLOGY.relative_to(ROOT)),
            "source_certificate_sha256": _sha(ACCEPTED_ORD_TOPOLOGY),
            "source_status": _load(ACCEPTED_ORD_TOPOLOGY)["status"],
            "eligible_opportunities": [
                {"order": 1, "junction_id": 13956, "route_progress_m": ord_coordinates["j1_progress_m"], "forward_connectivity": "incoming road 587 lane 1 to right connector 14005"},
                {"order": 2, "junction_id": 8474, "route_progress_m": ord_coordinates["j2_progress_m"], "forward_connectivity": "continue through J1 connector 13968, then turn at J2"},
            ],
            "intended_ordering": "strictly increasing route arc length: J1 before J2",
            "commitment_point": commitment,
            "task_signature_semantics_changed": False,
            "rq1_consequence_semantics_changed": False,
        }
    }
    scenes.append(_scene(
        code="ORD-ACTIONABLE", identity=identity, family="ORD", timing="ACTIONABLE",
        route=route, instruction="Take the second eligible right, not the first one.",
        candidate_rows=(
            ("1", "second is resolved as the first eligible right after the approach marker", "task:first_eligible_right_J1_13956", "turn at first eligible junction J1=13956", "CERTIFIED_TASK_TO_NATIVE_TOPOLOGY"),
            ("2", "second is resolved as passing the first and turning at the next eligible right", "task:second_eligible_right_J2_8474", "continue through J1=13956 and turn at J2=8474", "CERTIFIED_TASK_TO_NATIVE_TOPOLOGY"),
        ),
        layout=(
            {"layout_id": identity + "-LAYOUT-01", "kind": "CERTIFIED_TASK_JUNCTION", "junction_id": 13956, "route_progress_m": ord_coordinates["j1_progress_m"], "lateral_offset_m": 0.0},
            {"layout_id": identity + "-LAYOUT-02", "kind": "CERTIFIED_TASK_JUNCTION", "junction_id": 8474, "route_progress_m": ord_coordinates["j2_progress_m"], "lateral_offset_m": 0.0},
        ),
        events=(
            _event(identity, 1, "GROUNDING_REVEAL", commitment - 10.0, commitment - 9.4, "CERTIFIED_ROUTE_TOPOLOGY_OWNER_V1", {"field": "E2_GROUNDING", "mechanism": "eligible_junction_identity"}),
            _event(identity, 2, "OBLIGATION_REVEAL", commitment - 8.8, commitment - 8.2, "CERTIFIED_ROUTE_OBLIGATION_OWNER_V1", {"fields": ["E4_FUTURE_OBLIGATION_RELATION", "E5_ROUTE_LANE_TOPOLOGY_RELATION", "E6_CANDIDATE_CONSEQUENCE_DIVERGENCE", "E9_ANSWER_CHANGES_ACTION"]}),
        ),
        commitment_m=commitment,
        evidence_order=("eligible-junction identities", "first-versus-second task divergence"),
        rationale="Certified J1/J2 ordering is revealed asynchronously with more than 8 m of route margin before the unchanged J1 recoverability boundary.",
        extra=ord_certification,
    ))

    identity = "RQ2EXT-V1-ORD-TOO-LATE-TWO-JUNCTION-02"
    route, ord_coordinates = _ord_route(12, 141, identity)
    commitment = ord_coordinates["commitment_progress_m"]
    ord_certification = {
        "ordered_alternative_topology_certification": {
            "source_certificate": str(ACCEPTED_ORD_TOPOLOGY.relative_to(ROOT)),
            "source_certificate_sha256": _sha(ACCEPTED_ORD_TOPOLOGY),
            "source_status": _load(ACCEPTED_ORD_TOPOLOGY)["status"],
            "eligible_opportunities": [
                {"order": 1, "junction_id": 13956, "route_progress_m": ord_coordinates["j1_progress_m"], "forward_connectivity": "incoming road 587 lane 1 to right connector 14005"},
                {"order": 2, "junction_id": 8474, "route_progress_m": ord_coordinates["j2_progress_m"], "forward_connectivity": "continue through J1 connector 13968, then turn at J2"},
            ],
            "intended_ordering": "strictly increasing route arc length: J1 before J2",
            "commitment_point": commitment,
            "task_signature_semantics_changed": False,
            "rq1_consequence_semantics_changed": False,
        }
    }
    scenes.append(_scene(
        code="ORD-TOO-LATE", identity=identity, family="ORD", timing="TOO-LATE",
        route=route, instruction="Use the first of the two eligible right turns ahead.",
        candidate_rows=(
            ("1", "first denotes the immediate eligible right at J1", "task:first_eligible_right_J1_13956", "turn at first eligible junction J1=13956", "CERTIFIED_TASK_TO_NATIVE_TOPOLOGY"),
            ("2", "first is scoped after passing the immediate junction and denotes J2", "task:later_eligible_right_J2_8474", "continue through J1=13956 and turn at J2=8474", "CERTIFIED_TASK_TO_NATIVE_TOPOLOGY"),
        ),
        layout=(
            {"layout_id": identity + "-LAYOUT-01", "kind": "CERTIFIED_TASK_JUNCTION", "junction_id": 13956, "route_progress_m": ord_coordinates["j1_progress_m"], "lateral_offset_m": 0.0},
            {"layout_id": identity + "-LAYOUT-02", "kind": "CERTIFIED_TASK_JUNCTION", "junction_id": 8474, "route_progress_m": ord_coordinates["j2_progress_m"], "lateral_offset_m": 0.0},
        ),
        events=(
            _event(identity, 1, "GROUNDING_REVEAL", commitment - 2.0, commitment - 1.5, "CERTIFIED_ROUTE_TOPOLOGY_OWNER_V1", {"field": "E2_GROUNDING", "mechanism": "eligible_junction_identity"}),
            _event(identity, 2, "OBLIGATION_REVEAL", commitment - 0.9, commitment - 0.4, "CERTIFIED_ROUTE_OBLIGATION_OWNER_V1", {"fields": ["E4_FUTURE_OBLIGATION_RELATION", "E5_ROUTE_LANE_TOPOLOGY_RELATION", "E6_CANDIDATE_CONSEQUENCE_DIVERGENCE", "E9_ANSWER_CHANGES_ACTION"]}),
        ),
        commitment_m=commitment,
        evidence_order=("eligible-junction identities", "late first-versus-second obligation divergence"),
        rationale="The decisive certified ordering event begins 0.9 m before the unchanged recoverability boundary, allowing evidence sufficiency to occur only after actionability may be lost.",
        extra=ord_certification,
    ))
    return tuple(scenes)


def validate_scenes(scenes: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    if len(scenes) != 6 or tuple(scene["scene_code"] for scene in scenes) != SCENE_ORDER:
        raise ValueError("RQ2_EXTENSION_EXACT_SIX_ORDERED_TEMPLATES_REQUIRED")
    historical = [
        _load(path)
        for path in sorted((HISTORICAL_REPORT / "FORMAL_V3_SCENES").glob("*.json"))
    ]
    historical_ids = {row["formal_scene_id"] for row in historical}
    historical_route_digests = {row["route"]["route_spec_digest"] for row in historical}
    historical_instructions = {row["instruction"] for row in historical}
    checks = {
        "scene_ids_unique": len({row["formal_scene_id"] for row in scenes}) == 6,
        "scene_digests_unique": len({row["formal_scene_digest"] for row in scenes}) == 6,
        "route_digests_unique": len({row["route"]["route_spec_digest"] for row in scenes}) == 6,
        "identities_new_vs_historical": not ({row["formal_scene_id"] for row in scenes} & historical_ids),
        "routes_new_vs_historical": not ({row["route"]["route_spec_digest"] for row in scenes} & historical_route_digests),
        "language_new_vs_historical": not ({row["instruction"] for row in scenes} & historical_instructions),
        "three_actionable": sum(row["timing_stratum"] == "ACTIONABLE" for row in scenes) == 3,
        "three_too_late": sum(row["timing_stratum"] == "TOO-LATE" for row in scenes) == 3,
        "families_balanced": all(sum(row["scene_family"] == family for row in scenes) == 2 for family in ("REF", "LMK", "ORD")),
        "asynchronous_nonoverlap": all(
            len(row["events"]) == 2
            and float(row["events"][0]["activation"]["end_exclusive_m"])
            <= float(row["events"][1]["activation"]["start_inclusive_m"])
            for row in scenes
        ),
        "events_precommitment": all(
            0.0 <= float(event["activation"]["start_inclusive_m"])
            < float(event["activation"]["end_exclusive_m"])
            < float(row["commitment"]["threshold_m"])
            for row in scenes for event in row["events"]
        ),
        "route_horizon_sufficient": all(
            float(row["route"]["route_length_m"]) > float(row["commitment"]["threshold_m"]) + 1.0
            for row in scenes
        ),
        "reserve_exact": all(float(row["deadline"]["total_reserved_simulation_s"]) == 1.20 for row in scenes),
        "zero_seeds_and_exposures": all(
            row["formal_seed_values_generated"] == 0 and row["formal_scientific_exposures"] == 0
            for row in scenes
        ),
        "ord_topology_certified": all(
            row.get("ordered_alternative_topology_certification", {}).get("source_status")
            == "PASS_ORD_ASYNC_NATIVE_TOPOLOGY_CERTIFICATION"
            and row["ordered_alternative_topology_certification"]["eligible_opportunities"][0]["route_progress_m"]
            < row["ordered_alternative_topology_certification"]["eligible_opportunities"][1]["route_progress_m"]
            for row in scenes if row["scene_family"] == "ORD"
        ),
    }
    for scene in scenes:
        assert_no_true_intent(scene)
        validate_candidate_bindings(scene["candidate_bindings"])
        expected = canonical_sha256({key: value for key, value in scene.items() if key != "formal_scene_digest"})
        if expected != scene["formal_scene_digest"]:
            raise ValueError("RQ2_EXTENSION_SCENE_DIGEST_MISMATCH:" + scene["scene_code"])
    failures = [key for key, value in checks.items() if not value]
    return {
        "status": "PASS_PROSPECTIVE_SIX_TEMPLATE_CERTIFICATION" if not failures else "FAIL_TEMPLATE_CERTIFICATION",
        "checks": checks,
        "failed_checks": failures,
        "scene_count": len(scenes),
        "historical_rq2_freeze_digest": HISTORICAL_FREEZE_DIGEST,
        "template_set_digest": canonical_sha256([scene["formal_scene_digest"] for scene in scenes]),
    }


__all__ = ["SCENE_ORDER", "build_scenes", "validate_scenes"]
