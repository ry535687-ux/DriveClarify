"""Exactly eight fresh, prospective formal-scene specifications.

The specifications reuse frozen simulator mechanisms and map polylines, but
not any prior exact scene identity.  They cannot execute an episode.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from driveclarify_rq2_t.measurement import canonical_sha256
from driveclarify_rq2_t_cg.contracts import CertifiedCandidateBinding, validate_candidate_bindings


ROOT = Path(__file__).resolve().parents[1]
BASE_ROUTES = {
    "REF": ROOT / "driveclarify_rq2_t" / "formal_routes" / "ref-01.xml",
    "LMK": ROOT / "driveclarify_rq2_t" / "formal_routes" / "lmk-01.xml",
    "ORD": ROOT / "driveclarify_rq2_t" / "formal_routes" / "ord-01.xml",
}

SCENE_ORDER = (
    "REF-ASYNC", "LMK-ASYNC", "ORD-ASYNC", "REF-SYNC", "LMK-SYNC",
    "ORD-LATE-REVEAL", "NONREVEAL", "USC-INTRINSIC",
)


def _base_waypoints(family: str) -> Tuple[str, List[Mapping[str, float]]]:
    route = ET.parse(str(BASE_ROUTES[family])).getroot().find("route")
    assert route is not None
    points = [
        {axis: float(node.attrib[axis]) for axis in ("x", "y", "z")}
        for node in route.findall("./waypoints/position")
    ]
    return str(route.attrib["town"]), points


def _route(family: str, start: int, stop: int) -> Mapping[str, Any]:
    town, points = _base_waypoints(family)
    selected = points[start:stop]
    if len(selected) < 20:
        raise ValueError("RQ2_T_CG_FORMAL_ROUTE_TOO_SHORT")
    distance = sum(math.dist(
        (left["x"], left["y"], left["z"]),
        (right["x"], right["y"], right["z"]),
    ) for left, right in zip(selected, selected[1:]))
    value = {
        "town": town,
        "mechanism_polyline_source": str(BASE_ROUTES[family].relative_to(ROOT)),
        "source_slice_half_open": [start, stop],
        "waypoints": selected,
        "route_length_m": distance,
        "exact_prior_full_route_reused": False,
        "native_route_materialized": False,
    }
    value["route_spec_digest"] = canonical_sha256(value)
    return value


def _bindings(prefix: str, instruction: str, rows: Sequence[Tuple[str, str, str, str]]) -> List[Mapping[str, Any]]:
    result = []
    for suffix, interpretation, role, obligation in rows:
        result.append(CertifiedCandidateBinding(
            candidate_id=prefix + "-C" + suffix,
            interpretation_id=prefix + "-I" + suffix,
            interpretation_text=instruction + " Interpretation " + suffix + ": " + interpretation,
            binding_id=prefix + "-B" + suffix,
            entity_or_task_role=role,
            obligation_descriptor=obligation,
            binding_kind="CERTIFIED_ENTITY" if "entity" in role else "CERTIFIED_TASK",
        ).to_dict())
    validate_candidate_bindings(result)
    return result


def _event(event_id: str, kind: str, start_m: float, end_m: float, owner: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
    value = {
        "event_id": event_id, "event_kind": kind,
        "activation": {"coordinate": "route_progress_m", "start_inclusive_m": start_m, "end_exclusive_m": end_m},
        "owner": owner, "payload": dict(payload),
        "reads_view": False, "reads_outcome": False, "reads_passenger_intent": False,
    }
    value["event_digest"] = canonical_sha256(value)
    return value


def _scene(
    code: str, scene_id: str, family: str, slice_pair: Tuple[int, int], instruction: str,
    candidates: Sequence[Tuple[str, str, str, str]], actors: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]], commitment_m: float, design: str,
    expected_integrity: str,
) -> Mapping[str, Any]:
    route = _route(family, slice_pair[0], slice_pair[1])
    value = {
        "schema_version": "driveclarify.rq2_t_cg.formal_scene.v1",
        "scene_code": code, "formal_scene_id": scene_id,
        "scene_family": family, "timing_design": design,
        "instruction": instruction,
        "candidate_bindings": _bindings(scene_id, instruction, candidates),
        "candidate_certification": {
            "minimum_reasonable_candidates": 2,
            "independent_reasonableness_basis": "Each reading denotes a distinct visible entity or distinct eligible task obligation and changes the required future maneuver.",
            "passenger_intent_identified": False,
            "runtime_correct_candidate_available": False,
            "production_deployable": False,
        },
        "route": route, "actor_or_task_layout": list(actors), "events": list(events),
        "commitment": {
            "coordinate": "route_progress_m", "threshold_m": commitment_m,
            "owner": "FROZEN_ROUTE_TOPOLOGY_CROSSING_OWNER_V1",
            "reads_view": False, "reads_outcome": False,
        },
        "deadline": {
            "definition": "commitment_simulation_time_s - exact frozen DeadlineContract.total_reserved_simulation_s",
            "total_reserved_simulation_s": 1.2,
            "contract_change_authorized": False,
        },
        "horizon": {
            "natural_end": "first source frame at or after commitment plus 1.0 CARLA simulation second",
            "administrative_cap": "20.0 CARLA simulation seconds after first eligible source frame",
            "administrative_cap_scientific_event": False,
        },
        "termination": {
            "success": "natural horizon observed with complete paired source trace",
            "censor": "commitment/horizon missing, source identity divergence, certificate breach, or infrastructure termination",
            "zero_imputation_for_censoring": False,
        },
        "expected_integrity_property": expected_integrity,
        "formal_seed_values": [], "formal_seed_values_generated": 0,
        "formal_episode_count": 0, "formal_scientific_exposures": 0,
        "online_ask_count": 0, "native_materialization_count": 0,
        "engineering_scene_promoted": False,
    }
    value["formal_scene_digest"] = canonical_sha256(value)
    return value


_REF_ASYNC_EVENTS = [
    _event("FREF-A-E1", "GROUNDING_REVEAL", 1.5, 1.9, "CERTIFIED_NATIVE_VISIBILITY_OWNER_V1", {"field": "E2_GROUNDING", "candidate": "C1"}),
    _event("FREF-A-E2", "OBLIGATION_REVEAL", 2.8, 3.2, "CERTIFIED_ROUTE_OBLIGATION_OWNER_V1", {"field": "E5_FUTURE_OBLIGATION", "candidate": "C1"}),
    _event("FREF-A-E3", "BINDING_INVALIDATION", 4.6, 4.8, "AUTHORED_BINDING_INVALIDATION_OWNER_V1", {"invalidation": "REFERENT_BINDING_CHANGED"}),
]
_LMK_ASYNC_EVENTS = [
    _event("FLMK-A-E1", "GROUNDING_REVEAL", 1.0, 1.4, "CERTIFIED_NATIVE_VISIBILITY_OWNER_V1", {"field": "E2_GROUNDING", "candidate": "C2"}),
    _event("FLMK-A-E2", "OBLIGATION_REVEAL", 2.2, 2.6, "CERTIFIED_ROUTE_OBLIGATION_OWNER_V1", {"field": "E5_FUTURE_OBLIGATION", "candidate": "C2"}),
    _event("FLMK-A-E3", "TOPOLOGY_INVALIDATION", 4.4, 4.6, "AUTHORED_TOPOLOGY_INVALIDATION_OWNER_V1", {"invalidation": "ROUTE_TOPOLOGY_CHANGED"}),
]
_ORD_ASYNC_EVENTS = [
    _event("FORD-A-E1", "GROUNDING_REVEAL", 1.5, 1.9, "CERTIFIED_ROUTE_TOPOLOGY_OWNER_V1", {"field": "E2_GROUNDING", "candidate": "C1"}),
    _event("FORD-A-E2", "OBLIGATION_REVEAL", 2.6, 3.0, "CERTIFIED_ROUTE_OBLIGATION_OWNER_V1", {"field": "E5_FUTURE_OBLIGATION", "candidate": "C1"}),
    _event("FORD-A-E3", "HOLDING_INVALIDATION", 5.2, 5.4, "AUTHORED_HOLDING_INVALIDATION_OWNER_V1", {"invalidation": "HOLDING_FEASIBILITY_CHANGED"}),
]

FORMAL_SCENES = (
    _scene("REF-ASYNC", "RQ2TCG-FRZ-REF-ASYNC-01", "REF", (30, 100), "Turn after the service van.",
           (("1", "the nearer stationary curbside service van is the landmark", "entity:near_stationary_service_van", "turn at the first eligible branch after the near van"),
            ("2", "the farther slowly moving lane service van is the landmark", "entity:far_moving_service_van", "continue past the first branch and turn after the far van")),
           ({"layout_id": "FREF-ACTOR-NEAR", "role": "stationary curbside service van", "route_progress_m": 3.1, "lateral_offset_m": 3.3},
            {"layout_id": "FREF-ACTOR-FAR", "role": "slow lane service van", "route_progress_m": 5.7, "lateral_offset_m": -3.6}),
           _REF_ASYNC_EVENTS, 8.0, "ASYNCHRONOUS_REVEAL",
           "Distinct legal reveal events can combine within exact frozen field TTLs; B2 advantage is possible but not guaranteed."),
    _scene("LMK-ASYNC", "RQ2TCG-FRZ-LMK-ASYNC-01", "LMK", (20, 90), "Use the opening after the striped roadside marker.",
           (("1", "the nearer striped work-zone marker defines the opening", "entity:near_workzone_marker", "enter the nearer service opening"),
            ("2", "the farther striped transit marker defines the opening", "entity:far_transit_marker", "enter the farther transit opening")),
           ({"layout_id": "FLMK-ACTOR-NEAR", "role": "striped work-zone marker", "route_progress_m": 2.7, "lateral_offset_m": 3.5},
            {"layout_id": "FLMK-ACTOR-FAR", "role": "striped transit marker", "route_progress_m": 5.2, "lateral_offset_m": -3.4}),
           _LMK_ASYNC_EVENTS, 6.5, "ASYNCHRONOUS_REVEAL",
           "Distinct legal reveal events can combine within exact frozen field TTLs; B2 advantage is possible but not guaranteed."),
    _scene("ORD-ASYNC", "RQ2TCG-FRZ-ORD-ASYNC-01", "ORD", (10, 110), "Take the next right after the plaza.",
           (("1", "next modifies the first eligible right beyond the plaza boundary", "task:first_post_plaza_right", "take the first eligible right after the plaza"),
            ("2", "next refers to the second right after passing the plaza frontage", "task:second_post_plaza_right", "pass the first eligible right and take the second")),
           ({"layout_id": "FORD-TASK-FIRST", "role": "first eligible right marker", "route_progress_m": 5.4, "lateral_offset_m": -2.8},
            {"layout_id": "FORD-TASK-SECOND", "role": "second eligible right marker", "route_progress_m": 8.2, "lateral_offset_m": -2.8}),
           _ORD_ASYNC_EVENTS, 10.0, "ASYNCHRONOUS_REVEAL",
           "Distinct legal reveal events can combine within exact frozen field TTLs; B2 advantage is possible but not guaranteed."),
    _scene("REF-SYNC", "RQ2TCG-FRZ-REF-SYNC-01", "REF", (50, 120), "Turn after the delivery vehicle.",
           (("1", "the near loading-bay delivery vehicle is the landmark", "entity:near_loading_vehicle", "turn at the branch after the loading-bay vehicle"),
            ("2", "the far travel-lane delivery vehicle is the landmark", "entity:far_lane_vehicle", "continue and turn after the travel-lane vehicle")),
           ({"layout_id": "FREF-S-ACTOR-NEAR", "role": "loading-bay delivery vehicle", "route_progress_m": 3.9, "lateral_offset_m": 3.8},
            {"layout_id": "FREF-S-ACTOR-FAR", "role": "travel-lane delivery vehicle", "route_progress_m": 6.1, "lateral_offset_m": -2.9}),
           (_event("FREF-S-E1", "SYNCHRONOUS_GROUNDING_AND_OBLIGATION", 2.0, 2.5, "CERTIFIED_COMMON_FRAME_EVENT_OWNER_V1", {"fields": ["E2_GROUNDING", "E5_FUTURE_OBLIGATION"]}),),
           8.5, "SYNCHRONOUS_COMMON_CURRENT_FRAME",
           "Both required fields share a common current source frame; memory must not manufacture a B2-only witness and ties are not guaranteed."),
    _scene("LMK-SYNC", "RQ2TCG-FRZ-LMK-SYNC-01", "LMK", (40, 110), "Enter after the blue roadside sign.",
           (("1", "the near blue access sign marks the first entry", "entity:near_access_sign", "enter at the first signed access"),
            ("2", "the far blue transit sign marks the second entry", "entity:far_transit_sign", "continue to the second signed access")),
           ({"layout_id": "FLMK-S-ACTOR-NEAR", "role": "blue access sign", "route_progress_m": 3.4, "lateral_offset_m": 3.2},
            {"layout_id": "FLMK-S-ACTOR-FAR", "role": "blue transit sign", "route_progress_m": 5.8, "lateral_offset_m": -3.1}),
           (_event("FLMK-S-E1", "SYNCHRONOUS_GROUNDING_AND_OBLIGATION", 1.5, 2.0, "CERTIFIED_COMMON_FRAME_EVENT_OWNER_V1", {"fields": ["E2_GROUNDING", "E5_FUTURE_OBLIGATION"]}),),
           7.0, "SYNCHRONOUS_COMMON_CURRENT_FRAME",
           "Both required fields share a common current source frame; memory must not manufacture a B2-only witness and ties are not guaranteed."),
    _scene("ORD-LATE-REVEAL", "RQ2TCG-FRZ-ORD-LATE-REVEAL-01", "ORD", (20, 120), "Use the right exit after the arcade.",
           (("1", "the right exit immediately beyond the arcade is intended by the instruction reading", "task:immediate_arcade_exit", "take the immediate post-arcade right exit"),
            ("2", "the later service right beyond the arcade block is intended by the instruction reading", "task:later_arcade_service_exit", "continue past the immediate exit and take the later service right")),
           ({"layout_id": "FORD-LATE-FIRST", "role": "immediate post-arcade exit", "route_progress_m": 9.1, "lateral_offset_m": -3.0},
            {"layout_id": "FORD-LATE-SECOND", "role": "later service right", "route_progress_m": 11.3, "lateral_offset_m": -3.0}),
           (_event("FORD-L-E1", "DECISIVE_LATE_REVEAL", 9.25, 9.45, "CERTIFIED_ROUTE_TOPOLOGY_OWNER_V1", {"fields": ["E2_GROUNDING", "E5_FUTURE_OBLIGATION"], "gold_reveal": False}),),
           10.0, "LATE_AFTER_DEADLINE_BEFORE_COMMITMENT",
           "Decisive evidence is progress-owned after the 1.2-s deadline but before commitment under the frozen 0.8–2.0 m/s approach envelope; runtime gold reveal is forbidden."),
    _scene("NONREVEAL", "RQ2TCG-FRZ-NONREVEAL-01", "REF", (70, 140), "Turn after the white utility vehicle.",
           (("1", "the nearer parked white utility vehicle is the possible landmark", "entity:near_white_utility", "take the nearer post-vehicle branch"),
            ("2", "the farther moving white utility vehicle is the possible landmark", "entity:far_white_utility", "continue to the farther post-vehicle branch")),
           ({"layout_id": "FNR-ACTOR-NEAR", "role": "occluded parked utility vehicle", "route_progress_m": 4.1, "lateral_offset_m": 3.7},
            {"layout_id": "FNR-ACTOR-FAR", "role": "occluded moving utility vehicle", "route_progress_m": 6.9, "lateral_offset_m": -3.2}),
           (_event("FNR-E1", "NONDECISIVE_CONTEXT_ONLY", 2.0, 2.4, "CERTIFIED_NATIVE_VISIBILITY_OWNER_V1", {"decisive_grounding_reveal": False}),),
           9.0, "NEGATIVE_NONREVEAL",
           "No decisive grounding or obligation reveal occurs; B1/B2 sufficiency and fabricated semantic resolution must remain absent."),
    _scene("USC-INTRINSIC", "RQ2TCG-FRZ-USC-INTRINSIC-01", "REF", (90, 160), "Pull over at a convenient place.",
           (("1", "the left permissible bay is one reasonable convenience interpretation", "task:left_permissible_bay", "pull into the left permissible bay when feasible"),
            ("2", "the right permissible lay-by is another reasonable convenience interpretation", "task:right_permissible_layby", "pull into the right permissible lay-by when feasible")),
           ({"layout_id": "FUSC-TASK-LEFT", "role": "left permissible stopping bay", "route_progress_m": 5.0, "lateral_offset_m": 3.6},
            {"layout_id": "FUSC-TASK-RIGHT", "role": "right permissible lay-by", "route_progress_m": 6.2, "lateral_offset_m": -3.6}),
           (_event("FUSC-E1", "STOPPING_FEASIBILITY_CONTEXT", 2.4, 3.0, "CERTIFIED_HOLDING_FEASIBILITY_OWNER_V1", {"field": "E7_HOLDING_FEASIBILITY", "passenger_preference_available": False}),),
           9.5, "NEGATIVE_INTRINSIC_SEMANTIC_UNCERTAINTY",
           "Environmental feasibility may be observed, but the absent passenger convenience constraint remains UNKNOWN and cannot be fabricated by memory."),
)


def validate_formal_scenes(scenes: Sequence[Mapping[str, Any]] = FORMAL_SCENES) -> Mapping[str, Any]:
    if len(scenes) != 8 or tuple(scene["scene_code"] for scene in scenes) != SCENE_ORDER:
        raise ValueError("RQ2_T_CG_EXACT_EIGHT_SCENE_TEMPLATES_REQUIRED")
    ids = [scene["formal_scene_id"] for scene in scenes]
    digests = [scene["formal_scene_digest"] for scene in scenes]
    routes = [scene["route"]["route_spec_digest"] for scene in scenes]
    if len(set(ids)) != 8 or len(set(digests)) != 8 or len(set(routes)) != 8:
        raise ValueError("RQ2_T_CG_FORMAL_IDENTITIES_NOT_UNIQUE")
    for scene in scenes:
        validate_candidate_bindings(scene["candidate_bindings"])
        if scene["route"]["route_length_m"] <= scene["commitment"]["threshold_m"] + 1.0:
            raise ValueError("RQ2_T_CG_ROUTE_HORIZON_INSUFFICIENT:" + scene["scene_code"])
        if scene["formal_seed_values"] or scene["formal_episode_count"] or scene["formal_scientific_exposures"]:
            raise PermissionError("RQ2_T_CG_FORMAL_EXPOSURE_OR_SEED_PRESENT")
        if any(event["reads_view"] or event["reads_outcome"] or event["reads_passenger_intent"] for event in scene["events"]):
            raise PermissionError("RQ2_T_CG_EVENT_OWNER_NOT_INDEPENDENT")
    late = next(scene for scene in scenes if scene["scene_code"] == "ORD-LATE-REVEAL")
    reveal_m = late["events"][0]["activation"]["start_inclusive_m"]
    distance_m = late["commitment"]["threshold_m"] - reveal_m
    ttc_interval = [distance_m / 2.0, distance_m / 0.8]
    if not (0.0 < ttc_interval[0] <= ttc_interval[1] < 1.2):
        raise ValueError("RQ2_T_CG_LATE_REVEAL_TTC_CONTRACT_FAILED")
    return {
        "scene_count": 8, "scene_ids_unique": True, "scene_digests_unique": True,
        "route_specs_unique": True, "candidate_certificates_valid": True,
        "event_owners_independent": True, "formal_seed_values_generated": 0,
        "formal_scientific_exposures": 0, "late_reveal_TTCmt_interval_s": ttc_interval,
        "validation_digest": canonical_sha256(digests),
    }


validate_formal_scenes()

__all__ = ["BASE_ROUTES", "FORMAL_SCENES", "SCENE_ORDER", "validate_formal_scenes"]
