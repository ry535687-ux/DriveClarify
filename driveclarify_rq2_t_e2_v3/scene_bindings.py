"""Prospectively registered engineering scenes for E2_TRACKED_ASSOCIATION_V3.

This module is imported by the ScenarioRunner process and by offline report
builders.  The runtime receives only ``runtime_candidates``; actor identities,
events, visibility roles, and expected scoring stay outside the runtime.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


NEAR_FAR_CANDIDATES = [
    {
        "candidate_id": "z1",
        "interpretation_id": "nearer-first",
        "text": "Turn after the nearer first parked white car in the ego lane.",
        "overrides": {"road_relation": "ON_ROAD"},
    },
    {
        "candidate_id": "z2",
        "interpretation_id": "farther-second",
        "text": "Turn after the farther second parked white car in the ego lane.",
        "overrides": {"road_relation": "ON_ROAD"},
    },
]

COLORED_NEAR_FAR_CANDIDATES = [
    {
        "candidate_id": "z1",
        "interpretation_id": "nearer-blue-right",
        "text": "Turn after the nearer parked blue car on the right side.",
        "overrides": {"road_relation": "ON_ROAD"},
    },
    {
        "candidate_id": "z2",
        "interpretation_id": "farther-yellow-left",
        "text": "Turn after the farther parked yellow car on the left side.",
        "overrides": {"road_relation": "ON_ROAD"},
    },
]

NEAR_FAR_VAN_CANDIDATES = [
    {
        "candidate_id": "z1",
        "interpretation_id": "nearer-roadside-marker",
        "text": "Use the opening after the nearer first parked white van on the left side.",
        "overrides": {"lane_relation": "LEFT_LANE", "road_relation": "ROADSIDE"},
    },
    {
        "candidate_id": "z2",
        "interpretation_id": "farther-roadside-marker",
        "text": "Use the opening after the farther second parked white van on the right side.",
        "overrides": {"lane_relation": "RIGHT_LANE", "road_relation": "ROADSIDE"},
    },
]

# Development/calibration-only landmark interpretations.  The first native
# landmark witness showed that the authored left/right labels were reversed by
# the Town10 viewpoint and unstable under ego motion.  Near/far plus local
# ordinal remains a distinct, renderer-certified language condition.  The
# separately sealed blind candidate set above is intentionally unchanged.
TOWN10_LMK_NEAR_FAR_CANDIDATES = [
    {
        "candidate_id": "z1",
        "interpretation_id": "nearer-first-roadside-marker",
        "text": "Use the opening after the nearer first parked white van roadside marker.",
        "overrides": {"road_relation": "ROADSIDE"},
    },
    {
        "candidate_id": "z2",
        "interpretation_id": "farther-second-roadside-marker",
        "text": "Use the opening after the farther second parked white van roadside marker.",
        "overrides": {"road_relation": "ROADSIDE"},
    },
]

ORDER_CANDIDATES = [
    {"candidate_id": "z1", "interpretation_id": "first-right", "text": "Take the first right turn."},
    {"candidate_id": "z2", "interpretation_id": "second-right", "text": "Take the second right turn."},
]

USC_CANDIDATES = [
    {"candidate_id": "z1", "interpretation_id": "left-branch", "text": "Choose the branch on the left."},
    {"candidate_id": "z2", "interpretation_id": "right-branch", "text": "Choose the branch on the right."},
]


def _town12_visual(
    phase: str,
    family: str,
    *,
    near_x: float = 33.185,
    far_x: float = 18.8954,
    near_reveal_y: float = 4512.20,
    far_y: float = 4512.14,
    reveal: bool = True,
    event_variant: str = "LATERAL_FACADE_TO_FORWARD_LANE",
    extra_events: Any = None,
    extra_actors: Any = None,
    expected_scoring: str = "POSITIVE_REVEAL",
    engineering_bound_s: float = 8.0,
    runtime_candidates: Any = None,
    near_color: str = "255,255,255",
    far_color: str = "255,255,255",
) -> dict[str, Any]:
    events = []
    if reveal:
        events.append({
            "at_simulation_s": 1.0,
            "actor_index": 1,
            "event": "VISIBILITY_REVEAL",
            "transform": [near_x, near_reveal_y, 372.6740, 179.90],
        })
    events.extend(list(extra_events or ()))
    actors = [
        {
            "blueprint": "vehicle.audi.a2", "color": far_color,
            "initial": [far_x, far_y, 372.7745, 179.8666],
            "candidate_id": "z2", "certificate_role": "FARTHER_TARGET",
        },
        {
            "blueprint": "vehicle.lincoln.mkz_2020", "color": near_color,
            "initial": [near_x, 4536.24, 372.6740, 89.92],
            "candidate_id": "z1", "certificate_role": "NEARER_REVEAL_TARGET",
        },
    ]
    actors.extend(list(extra_actors or ()))
    return {
        "phase": phase, "family": family, "town": "Town12",
        "base_route": "driveclarify_rq2_t/formal_routes/ref-01.xml",
        "instruction": "Turn after the white car.",
        "runtime_candidates": runtime_candidates or NEAR_FAR_CANDIDATES,
        "actors": actors, "events": events,
        "engineering_bound_s": engineering_bound_s,
        "reveal_contract": event_variant,
        "expected_scoring": expected_scoring,
    }


def _town10_landmark(
    phase: str,
    *,
    near_y: float = 66.0,
    far_y: float = 79.0,
    reveal: bool = True,
    expected_scoring: str = "POSITIVE_REVEAL",
    engineering_bound_s: float = 8.0,
) -> dict[str, Any]:
    events = [] if not reveal else [{
        "at_simulation_s": 1.0, "actor_index": 1,
        "event": "VISIBILITY_REVEAL", "transform": [96.4, near_y, 0.35, -90.0],
    }]
    return {
        "phase": phase, "family": "LANDMARK", "town": "Town10HD",
        "base_route": "driveclarify_rq2_t/formal_routes/lmk-01.xml",
        "instruction": "Use the opening after the white roadside marker.",
        "runtime_candidates": TOWN10_LMK_NEAR_FAR_CANDIDATES,
        "actors": [
            {
                "blueprint": "vehicle.mercedes.sprinter", "color": "255,255,255",
                "initial": [101.4, far_y, 0.35, -90.0],
                "candidate_id": "z2", "certificate_role": "FARTHER_LANDMARK_TARGET",
            },
            {
                "blueprint": "vehicle.volkswagen.t2", "color": "255,255,255",
                # Keep the reveal target behind the initial forward camera
                # half-space.  An earlier engineering identity proved the old
                # facade coordinate could be fully renderer-visible before
                # the authored event under a different traffic seed.
                "initial": [96.4, 25.0, 0.35, 180.0],
                "candidate_id": "z1", "certificate_role": "NEARER_LANDMARK_REVEAL_TARGET",
            },
        ],
        "events": events, "engineering_bound_s": engineering_bound_s,
        "reveal_contract": "BUILDING_OCCLUDED_VAN_TO_TWO_DISCRIMINABLE_ROADSIDE_MARKERS",
        "expected_scoring": expected_scoring,
    }


SCENE_BINDINGS: dict[str, dict[str, Any]] = {
    # Development identities are inspectable and repairable only through fresh identities.
    "DEV-V3-REF-REVEAL-A": _town12_visual(
        "DEVELOPMENT", "REFERENTIAL", near_x=44.0, far_x=38.0,
        near_reveal_y=4507.0, far_y=4518.5, engineering_bound_s=3.0,
        event_variant="FACADE_TO_LATERALLY_SEPARATED_FORWARD_TARGETS",
        runtime_candidates=COLORED_NEAR_FAR_CANDIDATES,
        near_color="0,0,255", far_color="255,255,0",
    ),
    "DEV-V3-LMK-REVEAL-A": _town10_landmark("DEVELOPMENT", engineering_bound_s=5.0),
    "DEV-V3-ORD-TOPOLOGY-A": {
        "phase": "DEVELOPMENT", "family": "ORDER", "town": "Town12",
        "base_route": "driveclarify_rq2_t/formal_routes/ord-01.xml",
        "instruction": "Take the second right turn.", "runtime_candidates": ORDER_CANDIDATES,
        "actors": [], "events": [], "engineering_bound_s": 5.0,
        "reveal_contract": "LOCAL_ROUTE_PREFIX_TOPOLOGY_ONLY", "expected_scoring": "E5_POSITIVE",
    },
    "DEV-V3-E7-INVALIDATION-A": _town12_visual(
        "DEVELOPMENT", "REFERENTIAL",
        extra_actors=[{
            "blueprint": "vehicle.tesla.model3", "color": "0,0,0",
            "initial": [47.4, 4503.8, 372.2, 90.0],
            "candidate_id": None, "certificate_role": "CROSS_TRAFFIC_INVALIDATOR",
        }],
        extra_events=[{
            "at_simulation_s": 1.5, "actor_index": 2,
            "event": "SAFETY_INVALIDATION", "transform": [47.4, 4512.1, 372.2, 90.0],
        }],
        event_variant="VISUAL_REVEAL_THEN_CROSS_TRAFFIC_SAFETY_INVALIDATION",
        engineering_bound_s=3.0,
    ),
    "DEV-V3-ASYNC-MEMORY-A": _town12_visual(
        "DEVELOPMENT", "REFERENTIAL", near_x=44.0, far_x=38.0,
        near_reveal_y=4507.0, far_y=4518.5,
        extra_events=[
            {"at_simulation_s": 2.5, "actor_index": 1, "event": "TEMPORARY_ASYNC_CUE_LOSS", "transform": [44.0, 4536.24, 372.6740, 89.92]},
            {"at_simulation_s": 3.5, "actor_index": 1, "event": "COMPATIBLE_ASYNC_CUE_RETURN", "transform": [44.0, 4507.0, 372.6740, 179.90]},
        ],
        event_variant="E2_CUE_PRECEDES_ROUTE_AND_HOLDING_EVIDENCE_WITHIN_FROZEN_TTLS",
        engineering_bound_s=4.0,
        runtime_candidates=COLORED_NEAR_FAR_CANDIDATES,
        near_color="0,0,255", far_color="255,255,0",
    ),
    "DEV-V3-NONREVEAL-A": _town12_visual(
        "DEVELOPMENT", "REFERENTIAL", reveal=False,
        event_variant="SECOND_CANDIDATE_REMAINS_FACADE_OCCLUDED",
        expected_scoring="NEGATIVE_NONREVEAL",
        engineering_bound_s=5.0,
    ),
    "DEV-V3-USC-CONTROL-A": {
        "phase": "DEVELOPMENT", "family": "UNDERSPECIFIED_CONSTRAINT", "town": "Town12",
        # The original Town06 route never entered the engineering scenario
        # trigger and therefore ran until wall containment.  Use the already
        # validated short route binding; USC semantics come from the language
        # condition and absence of an observable preference, not the town.
        "base_route": "driveclarify_rq2_t/formal_routes/ref-01.xml",
        "instruction": "Pull over when convenient.", "runtime_candidates": USC_CANDIDATES,
        "actors": [], "events": [], "engineering_bound_s": 5.0,
        "expected_semantic_actor_count": 0,
        "trigger_map_certificate": {
            "source": "VALID_NATIVE_IDENTITY_RQ2TE2V3_ENG_036_POSTHOC_FRAME_2596",
            "road_id": 525, "lane_id": 1, "is_junction": False,
            "lane_type": "Driving", "waypoint_s": 12.03650768179054,
        },
        "reveal_contract": "PASSIVE_ENVIRONMENT_CANNOT_SUPPLY_MISSING_PREFERENCE",
        "expected_scoring": "NEGATIVE_INTRINSIC_USC",
    },
    "DEV-V3-TRACK-REACQUISITION-A": _town12_visual(
        "DEVELOPMENT", "REFERENTIAL", near_x=34.5, far_x=20.0,
        extra_events=[
            {"at_simulation_s": 3.5, "actor_index": 0, "event": "TEMPORARY_TRACK_LOSS", "transform": [20.0, 4538.0, 372.7745, 90.0]},
            {"at_simulation_s": 5.0, "actor_index": 0, "event": "COMPATIBLE_REACQUISITION", "transform": [20.2, 4512.15, 372.7745, 179.8666]},
        ],
        event_variant="VISUAL_REVEAL_THEN_LOSS_GRACE_AND_COMPATIBLE_REACQUISITION",
        engineering_bound_s=7.0,
    ),

    # Calibration scenes are scene/route/actor-configuration disjoint from development and blind.
    "CAL-V3-REF-POS-A": _town12_visual(
        "CALIBRATION", "REFERENTIAL", near_x=43.6, far_x=37.5,
        near_reveal_y=4506.5, far_y=4519.3, engineering_bound_s=5.0,
        runtime_candidates=COLORED_NEAR_FAR_CANDIDATES,
        near_color="0,0,255", far_color="255,255,0",
    ),
    "CAL-V3-REF-POS-B": _town12_visual(
        "CALIBRATION", "REFERENTIAL", near_x=45.0, far_x=39.2,
        near_reveal_y=4508.0, far_y=4521.0, engineering_bound_s=5.0,
        runtime_candidates=COLORED_NEAR_FAR_CANDIDATES,
        near_color="0,0,255", far_color="255,255,0",
    ),
    "CAL-V3-LMK-POS-A": _town10_landmark("CALIBRATION", near_y=65.2, far_y=80.4, engineering_bound_s=5.0),
    "CAL-V3-PRE-REVEAL-NEG": _town12_visual(
        "CALIBRATION", "REFERENTIAL", near_x=42.8, far_x=36.8,
        near_reveal_y=4506.0, far_y=4520.1,
        event_variant="PRE_REVEAL_ROWS_NEGATIVE_POST_REVEAL_ROWS_POSITIVE",
        engineering_bound_s=5.0,
        runtime_candidates=COLORED_NEAR_FAR_CANDIDATES,
        near_color="0,0,255", far_color="255,255,0",
    ),
    "CAL-V3-NONREVEAL-NEG": _town10_landmark(
        "CALIBRATION", near_y=67.2, far_y=81.1, reveal=False,
        expected_scoring="NEGATIVE_NONREVEAL",
        engineering_bound_s=5.0,
    ),
    "CAL-V3-WRONG-PAIR-NEG": _town12_visual(
        "CALIBRATION", "REFERENTIAL", near_x=46.0, far_x=40.5,
        near_reveal_y=4507.5, far_y=4521.7,
        event_variant="POSTHOC_WRONG_CANDIDATE_TRACK_PAIRS_ARE_NEGATIVE",
        expected_scoring="MIXED_POSITIVE_AND_WRONG_PAIR_NEGATIVE",
        engineering_bound_s=5.0,
        runtime_candidates=COLORED_NEAR_FAR_CANDIDATES,
        near_color="0,0,255", far_color="255,255,0",
    ),

    # Blind scenes are sealed before any V3 development native outcome is inspected.
    "BLIND-V3-E2-REF": {
        "phase": "BLIND", "family": "REFERENTIAL", "town": "Town03",
        "base_route": "driveclarify_paper_mvp_scenarios/generated/routes/dc-runtime-0bd82fb9b9bda9e4ca49040e.xml",
        "instruction": "Turn after the white van.", "runtime_candidates": NEAR_FAR_VAN_CANDIDATES,
        "actors": [
            {"blueprint": "vehicle.volkswagen.t2", "color": "255,255,255", "initial": [169.2,-192.4,0.35,0.0], "candidate_id": "z2", "certificate_role": "FARTHER_TARGET"},
            {"blueprint": "vehicle.mercedes.sprinter", "color": "255,255,255", "initial": [151.2,-171.0,2.75,91.0], "candidate_id": "z1", "certificate_role": "NEARER_REVEAL_TARGET"},
        ],
        "events": [{"at_simulation_s": 1.0, "actor_index": 1, "event": "VISIBILITY_REVEAL", "transform": [153.8,-188.1,0.35,0.0]}],
        "engineering_bound_s": 8.0, "reveal_contract": "TOWN03_BUILDING_OCCLUSION_TO_LATERALLY_SEPARATED_VANS",
        "expected_scoring": "BLIND_POSITIVE_REVEAL",
    },
    "BLIND-V3-E2-LMK": {
        "phase": "BLIND", "family": "LANDMARK", "town": "Town02",
        "base_route": "driveclarify_paper_mvp_scenarios/generated/routes/dc-runtime-ed0e96eb054a43cd26e73b95.xml",
        "instruction": "Use the opening after the white roadside marker.", "runtime_candidates": NEAR_FAR_VAN_CANDIDATES,
        "actors": [
            {"blueprint": "vehicle.volkswagen.t2", "color": "255,255,255", "initial": [43.7,222.0,0.35,90.0], "candidate_id": "z2", "certificate_role": "FARTHER_LANDMARK_TARGET"},
            {"blueprint": "vehicle.mercedes.sprinter", "color": "255,255,255", "initial": [57.0,244.0,0.35,0.0], "candidate_id": "z1", "certificate_role": "NEARER_LANDMARK_REVEAL_TARGET"},
        ],
        "events": [{"at_simulation_s": 1.0, "actor_index": 1, "event": "VISIBILITY_REVEAL", "transform": [48.2,239.0,0.35,-90.0]}],
        "engineering_bound_s": 8.0, "reveal_contract": "TOWN02_CORNER_OCCLUSION_TO_LATERALLY_SEPARATED_VANS",
        "expected_scoring": "BLIND_POSITIVE_REVEAL",
    },
    "BLIND-V3-E5-ORD": {
        "phase": "BLIND", "family": "ORDER", "town": "Town01",
        "base_route": "driveclarify_rq2_t/formal_routes/ord-02.xml",
        "instruction": "Take the second right turn.", "runtime_candidates": ORDER_CANDIDATES,
        "actors": [], "events": [], "engineering_bound_s": 8.0,
        "reveal_contract": "HELD_OUT_TOWN01_LOCAL_ROUTE_PREFIX_TOPOLOGY",
        "expected_scoring": "BLIND_E5_POSITIVE",
    },
    "BLIND-V3-USC": {
        "phase": "BLIND", "family": "UNDERSPECIFIED_CONSTRAINT", "town": "Town10HD",
        "base_route": "driveclarify_paper_mvp_scenarios/generated/routes/dc-runtime-06cccfa4723c09ca09a9bc2b.xml",
        "instruction": "Choose the branch beyond the plaza.", "runtime_candidates": USC_CANDIDATES,
        "actors": [], "events": [], "engineering_bound_s": 8.0,
        "reveal_contract": "HELD_OUT_INTRINSIC_LANGUAGE_CONSTRAINT_UNOBSERVABLE",
        "expected_scoring": "BLIND_NEGATIVE_INTRINSIC_USC",
    },
    "BLIND-V3-NONREVEAL": {
        **_town12_visual(
            "BLIND", "REFERENTIAL", near_x=37.2, far_x=23.5, reveal=False,
            event_variant="HELD_OUT_SECOND_TARGET_REMAINS_DIFFERENT_FACADE_OCCLUDED",
            expected_scoring="BLIND_NEGATIVE_NONREVEAL",
        ),
        "town": "Town12",
    },
}


def frozen_binding(scene_config_id: str) -> Mapping[str, Any]:
    value = SCENE_BINDINGS[scene_config_id]
    return {
        **value,
        "scene_config_id": scene_config_id,
        "scene_configuration_sha256": canonical_sha256(value),
    }


__all__ = ["SCENE_BINDINGS", "canonical_sha256", "frozen_binding"]
