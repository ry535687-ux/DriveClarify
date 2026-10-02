"""Prospectively frozen engineering-only controlled-grounding scenes.

These bindings describe candidate alternatives and independently owned native
events.  They deliberately contain no passenger answer, intended route, gold
query label, or method-outcome label.
"""

from __future__ import annotations

import copy
from typing import Any, Mapping

from driveclarify_rq2_t.measurement import canonical_sha256

from .contracts import CertifiedCandidateBinding, assert_no_true_intent, validate_candidate_bindings


ENGINEERING_SEEDS = {
    "CG-REF-ASYNC": 4169024478,
    "CG-LMK-ASYNC": 1987670682,
    "CG-ORD-ASYNC": 990813222,
    "CG-REF-SYNC": 2695171568,
    "CG-LMK-SYNC": 936706656,
    "CG-ORD-LATE-REVEAL": 2929468248,
    "CG-NONREVEAL": 2281622742,
    "CG-USC-INTRINSIC": 1929442806,
    # Fresh replacement registered only after ENG-001's native trajectory
    # failed to reach the unchanged commitment boundary.
    "CG-REF-ASYNC-B": 3195601261,
    "CG-REF-ASYNC-C": 3740192853,
    # Infrastructure-only replacement for ENG-005, whose native child could
    # not allocate CUDA memory while the preceding child was still exiting.
    "CG-LMK-SYNC-B": 1387249651,
}


def _binding(candidate_id: str, interpretation: str, text: str, binding_id: str,
             role: str, obligation: str, kind: str) -> Mapping[str, Any]:
    return CertifiedCandidateBinding(
        candidate_id=candidate_id, interpretation_id=interpretation,
        interpretation_text=text, binding_id=binding_id,
        entity_or_task_role=role, obligation_descriptor=obligation,
        binding_kind=kind,
    ).to_dict()


REF_BINDINGS = [
    _binding("z1", "moving-lane-vehicle", "Turn after the moving white vehicle in the driving lane.",
             "cg-ref-entity-near", "NEARER_LANE_VEHICLE", "turn after candidate entity z1", "SCENE_ENTITY"),
    _binding("z2", "parked-side-vehicle", "Turn after the parked white vehicle in the parking lane.",
             "cg-ref-entity-far", "FARTHER_PARKED_VEHICLE", "turn after candidate entity z2", "SCENE_ENTITY"),
]

LMK_BINDINGS = [
    _binding("z1", "near-roadside-marker", "Use the opening after the nearer roadside vehicle marker.",
             "cg-lmk-entity-near", "NEARER_ROADSIDE_MARKER", "use opening after marker z1", "SCENE_ENTITY"),
    _binding("z2", "far-roadside-marker", "Use the opening after the farther roadside vehicle marker.",
             "cg-lmk-entity-far", "FARTHER_ROADSIDE_MARKER", "use opening after marker z2", "SCENE_ENTITY"),
]

ORD_BINDINGS = [
    _binding("z1", "first-right", "Take the first available right turn.",
             "cg-ord-task-first", "FIRST_RIGHT_ROUTE_OPPORTUNITY", "take first right opportunity", "ROUTE_TASK"),
    _binding("z2", "second-right", "Take the second available right turn.",
             "cg-ord-task-second", "SECOND_RIGHT_ROUTE_OPPORTUNITY", "take second right opportunity", "ROUTE_TASK"),
]

USC_BINDINGS = [
    _binding("z1", "left-convenient-place", "Choose the left available convenient stopping place.",
             "cg-usc-task-left", "LEFT_AVAILABLE_STOPPING_PLACE", "stop at left admissible place", "ROUTE_TASK"),
    _binding("z2", "right-convenient-place", "Choose the right available convenient stopping place.",
             "cg-usc-task-right", "RIGHT_AVAILABLE_STOPPING_PLACE", "stop at right admissible place", "ROUTE_TASK"),
]


def _visual(*, family: str, template: str, town: str, base_route: str,
            instruction: str, candidates: list[Mapping[str, Any]], actors: list[Mapping[str, Any]],
            reveal_at_s: float | None, reveal_transform: list[float] | None,
            synchronous: bool, invalidation: bool = False,
            engineering_bound_s: float = 5.0,
            obligation_progress_after_activation_m: float | None = None,
            invalidation_at_s: float = 2.10) -> dict[str, Any]:
    events = []
    if reveal_at_s is not None and reveal_transform is not None:
        events.append({
            "at_simulation_s": reveal_at_s, "actor_index": 1,
            "event": "CERTIFIED_VISIBILITY_REVEAL", "transform": reveal_transform,
            "event_owner": "NATIVE_ACTOR_TRANSFORM_PLUS_POSTEPISODE_GEOMETRY_CERTIFIER",
        })
    if reveal_at_s is not None and not synchronous:
        hidden = list(actors[1]["initial"])
        events.append({
            "at_simulation_s": reveal_at_s + 0.20, "actor_index": 1,
            "event": "CERTIFIED_VISIBILITY_END", "transform": hidden,
            "event_owner": "NATIVE_ACTOR_TRANSFORM_PLUS_POSTEPISODE_GEOMETRY_CERTIFIER",
        })
    if invalidation:
        conflict = list(actors[1]["initial"])
        conflict[0] = conflict[0] + 15.0
        events.append({
            "at_simulation_s": invalidation_at_s, "actor_index": 1,
            "event": "TRACK_IDENTITY_CONFLICT", "transform": conflict,
            "event_owner": "PROSPECTIVE_NATIVE_BINDING_CONFLICT_EVENT",
        })
    return {
        "phase": "ENGINEERING_ONLY", "family": family, "template": template,
        "town": town, "base_route": base_route, "instruction": instruction,
        "candidate_bindings": copy.deepcopy(candidates), "actors": actors,
        "events": events, "engineering_bound_s": engineering_bound_s,
        "current_event_schedule": {
            "grounding_owner": "CERTIFIED_NATIVE_VISIBILITY_INTERVAL",
            "obligation_owner": "CERTIFIED_ROUTE_PROGRESS_EVENT",
            "holding_owner": "NATIVE_PRECOMMITMENT_SHARED_PREFIX",
            "synchronous": synchronous,
        },
        "obligation_progress_after_activation_m": (
            obligation_progress_after_activation_m
            if obligation_progress_after_activation_m is not None
            else 0.20 if family == "LANDMARK" else 3.50
        ),
        "formal_scientific_exposure": False,
    }


REF_ACTORS = [
    {"blueprint": "vehicle.jeep.wrangler_rubicon", "color": "255,255,255",
     "initial": [36.55, 4520.65, 372.7745, 179.8666], "candidate_id": "z2",
     "certificate_role": "FARTHER_PARKED_TARGET"},
    {"blueprint": "vehicle.nissan.patrol", "color": "255,255,255",
     "initial": [43.35, 4537.0, 372.6740, 89.92], "candidate_id": "z1",
     "certificate_role": "NEARER_REVEAL_TARGET"},
]

LMK_ACTORS = [
    {"blueprint": "vehicle.carlamotors.firetruck", "color": "255,255,255",
     "initial": [101.4, 82.2, 0.35, -90.0], "candidate_id": "z2",
     "certificate_role": "FARTHER_ROADSIDE_TARGET"},
    {"blueprint": "vehicle.ford.ambulance", "color": "255,255,255",
     "initial": [96.4, 25.0, 0.35, 180.0], "candidate_id": "z1",
     "certificate_role": "NEARER_REVEAL_TARGET"},
]


SCENE_BINDINGS: dict[str, dict[str, Any]] = {
    "CG-REF-ASYNC": _visual(
        family="REFERENTIAL", template="REF-ASYNC", town="Town12",
        base_route="driveclarify_rq2_t/formal_routes/ref-01.xml",
        instruction="Turn after the white vehicle.", candidates=REF_BINDINGS, actors=copy.deepcopy(REF_ACTORS),
        reveal_at_s=0.30, reveal_transform=[43.35, 4506.9, 372.6740, 179.90],
        synchronous=False, invalidation=True,
    ),
    "CG-LMK-ASYNC": _visual(
        family="LANDMARK", template="LMK-ASYNC", town="Town10HD",
        base_route="driveclarify_rq2_t/formal_routes/lmk-01.xml",
        instruction="Use the opening after the roadside vehicle marker.",
        candidates=LMK_BINDINGS, actors=copy.deepcopy(LMK_ACTORS),
        reveal_at_s=0.10, reveal_transform=[96.4, 64.4, 0.35, -90.0], synchronous=False,
        engineering_bound_s=7.0, obligation_progress_after_activation_m=0.80,
    ),
    "CG-REF-SYNC": _visual(
        family="REFERENTIAL", template="REF-SYNC", town="Town12",
        base_route="driveclarify_rq2_t/formal_routes/ref-01.xml",
        instruction="Turn after the parked vehicle.", candidates=REF_BINDINGS, actors=copy.deepcopy(REF_ACTORS),
        reveal_at_s=0.30, reveal_transform=[43.35, 4506.9, 372.6740, 179.90], synchronous=True,
        engineering_bound_s=12.0, obligation_progress_after_activation_m=2.0,
    ),
    "CG-LMK-SYNC": _visual(
        family="LANDMARK", template="LMK-SYNC", town="Town10HD",
        base_route="driveclarify_rq2_t/formal_routes/lmk-01.xml",
        instruction="Use the opening after the roadside vehicle marker.",
        candidates=LMK_BINDINGS, actors=copy.deepcopy(LMK_ACTORS),
        reveal_at_s=0.10, reveal_transform=[96.4, 64.4, 0.35, -90.0], synchronous=True,
        engineering_bound_s=7.0, obligation_progress_after_activation_m=0.80,
    ),
    "CG-ORD-ASYNC": {
        "phase": "ENGINEERING_ONLY", "family": "ORDER", "template": "ORD-ASYNC",
        "town": "Town12", "base_route": "driveclarify_rq2_t/formal_routes/ord-01.xml",
        "instruction": "Take the second right turn.", "candidate_bindings": copy.deepcopy(ORD_BINDINGS),
        "actors": [], "events": [], "engineering_bound_s": 13.0,
        "grounding_progress_after_activation_m": 0.50,
        "obligation_progress_after_activation_m": 2.00,
        "current_event_schedule": {
            "grounding_owner": "CERTIFIED_LOCAL_ROUTE_OPPORTUNITY_EVENT",
            "obligation_owner": "CERTIFIED_DISTINCT_ORDINAL_TOPOLOGY_EVENT",
            "holding_owner": "NATIVE_PRECOMMITMENT_SHARED_PREFIX", "synchronous": False,
        },
        "required_topology_ordinal": 2, "formal_scientific_exposure": False,
    },
    "CG-ORD-LATE-REVEAL": {
        "phase": "ENGINEERING_ONLY", "family": "ORDER", "template": "ORD-LATE-REVEAL",
        "town": "Town12", "base_route": "driveclarify_rq2_t/formal_routes/ord-01.xml",
        "instruction": "Take the second right turn.", "candidate_bindings": copy.deepcopy(ORD_BINDINGS),
        "actors": [], "events": [], "engineering_bound_s": 13.0,
        "grounding_progress_after_activation_m": 0.50,
        # Geometric distance to the boundary.  The unchanged V1 owner records
        # commitment at its frozen 0.75-m arrival radius, leaving a 0.50-m
        # evidence-to-owner-commitment interval at this 1.25-m threshold.
        "late_reveal_remaining_to_commitment_m": 1.25,
        "current_event_schedule": {
            "grounding_owner": "CERTIFIED_LOCAL_ROUTE_OPPORTUNITY_STATE",
            "obligation_owner": "PROSPECTIVELY_FROZEN_ROUTE_PROGRESS_TOPOLOGY_BOUNDARY",
            "holding_owner": "NATIVE_PRECOMMITMENT_SHARED_PREFIX", "synchronous": False,
        },
        "required_topology_ordinal": 2, "formal_scientific_exposure": False,
    },
    "CG-NONREVEAL": _visual(
        family="REFERENTIAL", template="NONREVEAL", town="Town12",
        base_route="driveclarify_rq2_t/formal_routes/ref-01.xml",
        instruction="Turn after the parked vehicle.", candidates=REF_BINDINGS, actors=copy.deepcopy(REF_ACTORS),
        reveal_at_s=None, reveal_transform=None, synchronous=False,
        engineering_bound_s=12.0, obligation_progress_after_activation_m=2.0,
    ),
    "CG-USC-INTRINSIC": {
        "phase": "ENGINEERING_ONLY", "family": "UNDERSPECIFIED_CONSTRAINT", "template": "USC-INTRINSIC",
        "town": "Town12", "base_route": "driveclarify_rq2_t/formal_routes/ref-01.xml",
        "instruction": "Pull over when convenient.", "candidate_bindings": copy.deepcopy(USC_BINDINGS),
        "actors": [], "events": [], "engineering_bound_s": 12.0,
        "current_event_schedule": {
            "grounding_owner": "CERTIFIED_LOCAL_STOPPING_PLACE_TOPOLOGY",
            "obligation_owner": "PASSENGER_SEMANTIC_CONSTRAINT_ABSENT",
            "holding_owner": "NATIVE_PRECOMMITMENT_SHARED_PREFIX", "synchronous": False,
        },
        "formal_scientific_exposure": False,
    },
    "CG-REF-ASYNC-B": _visual(
        family="REFERENTIAL", template="REF-ASYNC", town="Town12",
        base_route="driveclarify_rq2_t/formal_routes/ref-01.xml",
        instruction="Turn after the parked vehicle.", candidates=REF_BINDINGS, actors=copy.deepcopy(REF_ACTORS),
        reveal_at_s=0.30, reveal_transform=[43.35, 4506.9, 372.6740, 179.90],
        synchronous=False, invalidation=True, engineering_bound_s=12.0,
        obligation_progress_after_activation_m=2.0,
    ),
    "CG-REF-ASYNC-C": _visual(
        family="REFERENTIAL", template="REF-ASYNC", town="Town12",
        base_route="driveclarify_rq2_t/formal_routes/ref-01.xml",
        instruction="Turn after the parked vehicle.", candidates=REF_BINDINGS, actors=copy.deepcopy(REF_ACTORS),
        reveal_at_s=0.30, reveal_transform=[43.35, 4506.9, 372.6740, 179.90],
        synchronous=False, invalidation=True, engineering_bound_s=12.0,
        obligation_progress_after_activation_m=1.30, invalidation_at_s=3.50,
    ),
    "CG-LMK-SYNC-B": _visual(
        family="LANDMARK", template="LMK-SYNC", town="Town10HD",
        base_route="driveclarify_rq2_t/formal_routes/lmk-01.xml",
        instruction="Use the opening after the roadside vehicle marker.",
        candidates=LMK_BINDINGS, actors=copy.deepcopy(LMK_ACTORS),
        reveal_at_s=0.10, reveal_transform=[96.4, 64.4, 0.35, -90.0], synchronous=True,
        engineering_bound_s=7.0, obligation_progress_after_activation_m=0.80,
    ),
}


def frozen_binding(scene_id: str) -> Mapping[str, Any]:
    if scene_id not in SCENE_BINDINGS:
        raise KeyError("RQ2_T_CG_SCENE_NOT_PROSPECTIVELY_REGISTERED:" + str(scene_id))
    value = copy.deepcopy(SCENE_BINDINGS[scene_id])
    assert_no_true_intent(value)
    certification = validate_candidate_bindings(value["candidate_bindings"])
    result = {**value, "scene_config_id": scene_id, "engineering_seed": ENGINEERING_SEEDS[scene_id],
              "candidate_set_certification": certification}
    result["scene_configuration_sha256"] = canonical_sha256(result)
    return result


for _scene_id in SCENE_BINDINGS:
    frozen_binding(_scene_id)


__all__ = ["ENGINEERING_SEEDS", "SCENE_BINDINGS", "frozen_binding"]
