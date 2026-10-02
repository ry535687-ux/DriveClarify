"""Prospective V4 development/calibration bindings and post-freeze blind loader.

Only ``runtime_candidates`` is exported to the live association runtime.  Actor
identity, authored events, expected role, and reveal semantics remain in the
ScenarioRunner/post-episode grading side of the boundary.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .contracts import canonical_sha256


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports/driveclarify_rq2_t_v2_final_blind_domain_shift_redesign_v1"
BLIND_MANIFEST = REPORT / "FRESH_BLIND_SCENE_MANIFEST.json"


NEAR_FAR_CARS = [
    {
        "candidate_id": "z1", "interpretation_id": "nearer-first-vehicle",
        "text": "Turn after the nearer first parked vehicle.",
        "overrides": {"road_relation": "ON_ROAD"},
    },
    {
        "candidate_id": "z2", "interpretation_id": "farther-second-vehicle",
        "text": "Turn after the farther second parked vehicle.",
        "overrides": {"road_relation": "ON_ROAD"},
    },
]

NEAR_FAR_MARKERS = [
    {
        "candidate_id": "z1", "interpretation_id": "nearer-first-roadside-marker",
        "text": "Use the opening after the nearer first parked vehicle roadside marker.",
        "overrides": {"road_relation": "ROADSIDE"},
    },
    {
        "candidate_id": "z2", "interpretation_id": "farther-second-roadside-marker",
        "text": "Use the opening after the farther second parked vehicle roadside marker.",
        "overrides": {"road_relation": "ROADSIDE"},
    },
]

ORDER_CANDIDATES = [
    {"candidate_id": "z1", "interpretation_id": "first-right", "text": "Take the first right turn."},
    {"candidate_id": "z2", "interpretation_id": "second-right", "text": "Take the second right turn."},
]

USC_CANDIDATES = [
    {"candidate_id": "z1", "interpretation_id": "left-preference", "text": "Choose the left available branch."},
    {"candidate_id": "z2", "interpretation_id": "right-preference", "text": "Choose the right available branch."},
]


def _town12_visual(
    phase: str,
    *,
    scene_role: str,
    near_x: float,
    far_x: float,
    near_reveal_y: float,
    far_y: float,
    reveal: bool = True,
    bound_s: float = 5.0,
    near_blueprint: str = "vehicle.nissan.patrol",
    far_blueprint: str = "vehicle.jeep.wrangler_rubicon",
    extra_events: Any = None,
) -> dict[str, Any]:
    events = []
    if reveal:
        events.append({
            "at_simulation_s": 1.0, "actor_index": 1,
            "event": "VISIBILITY_REVEAL",
            "transform": [near_x, near_reveal_y, 372.6740, 179.90],
        })
    events.extend(list(extra_events or ()))
    return {
        "phase": phase, "family": "REFERENTIAL", "town": "Town12",
        "base_route": "driveclarify_rq2_t/formal_routes/ref-01.xml",
        "instruction": "Turn after the parked vehicle.",
        "runtime_candidates": NEAR_FAR_CARS,
        "actors": [
            {
                "blueprint": far_blueprint, "color": "255,255,255",
                "initial": [far_x, far_y, 372.7745, 179.8666],
                "candidate_id": "z2", "certificate_role": "FARTHER_TARGET",
            },
            {
                "blueprint": near_blueprint, "color": "255,255,255",
                "initial": [near_x, 4537.0, 372.6740, 89.92],
                "candidate_id": "z1", "certificate_role": "NEARER_REVEAL_TARGET",
            },
        ],
        "events": events, "engineering_bound_s": bound_s,
        "reveal_contract": "FACADE_OCCLUSION_TO_DEPTH_RANK_DISCRIMINABLE_TARGETS",
        "expected_scoring": "POSITIVE_REVEAL" if reveal else "NEGATIVE_NONREVEAL",
        "scene_role": scene_role,
    }


def _town10_landmark(
    phase: str,
    *,
    scene_role: str,
    near_y: float,
    far_y: float,
    reveal: bool = True,
    bound_s: float = 5.0,
    near_blueprint: str = "vehicle.ford.ambulance",
    far_blueprint: str = "vehicle.carlamotors.firetruck",
) -> dict[str, Any]:
    events = [] if not reveal else [{
        "at_simulation_s": 1.0, "actor_index": 1,
        "event": "VISIBILITY_REVEAL", "transform": [96.4, near_y, 0.35, -90.0],
    }]
    return {
        "phase": phase, "family": "LANDMARK", "town": "Town10HD",
        "base_route": "driveclarify_rq2_t/formal_routes/lmk-01.xml",
        "instruction": "Use the opening after the parked roadside marker.",
        "runtime_candidates": NEAR_FAR_MARKERS,
        "actors": [
            {
                "blueprint": far_blueprint, "color": "255,255,255",
                "initial": [101.4, far_y, 0.35, -90.0],
                "candidate_id": "z2", "certificate_role": "FARTHER_LANDMARK_TARGET",
            },
            {
                "blueprint": near_blueprint, "color": "255,255,255",
                "initial": [96.4, 25.0, 0.35, 180.0],
                "candidate_id": "z1", "certificate_role": "NEARER_LANDMARK_REVEAL_TARGET",
            },
        ],
        "events": events, "engineering_bound_s": bound_s,
        "reveal_contract": "BUILDING_OCCLUSION_TO_DEPTH_RANK_DISCRIMINABLE_ROADSIDE_MARKERS",
        "expected_scoring": "POSITIVE_REVEAL" if reveal else "NEGATIVE_NONREVEAL",
        "scene_role": scene_role,
    }


def _order(phase: str, *, scene_role: str, base_route: str, town: str, bound_s: float) -> dict[str, Any]:
    return {
        "phase": phase, "family": "ORDER", "town": town,
        "base_route": base_route, "instruction": "Take the second right turn.",
        "runtime_candidates": ORDER_CANDIDATES, "actors": [], "events": [],
        "engineering_bound_s": bound_s, "required_topology_ordinal": 2,
        "reveal_contract": "TWO_LOCAL_ROUTE_JUNCTION_OPPORTUNITIES",
        "expected_scoring": "E5_POSITIVE", "scene_role": scene_role,
    }


def _usc(phase: str, *, scene_role: str, bound_s: float) -> dict[str, Any]:
    return {
        "phase": phase, "family": "UNDERSPECIFIED_CONSTRAINT", "town": "Town12",
        "base_route": "driveclarify_rq2_t/formal_routes/ref-01.xml",
        "instruction": "Pull over when convenient.", "runtime_candidates": USC_CANDIDATES,
        "actors": [], "events": [], "engineering_bound_s": bound_s,
        "expected_semantic_actor_count": 0,
        "reveal_contract": "PASSIVE_ENVIRONMENT_CANNOT_SUPPLY_MISSING_PREFERENCE",
        "expected_scoring": "NEGATIVE_INTRINSIC_USC", "scene_role": scene_role,
    }


SCENE_BINDINGS: dict[str, dict[str, Any]] = {
    "DEV-V4-REF-DOMAIN-A": _town12_visual(
        "DEVELOPMENT", scene_role="REF_DOMAIN_SHIFT", near_x=43.1, far_x=36.3,
        near_reveal_y=4506.7, far_y=4520.4, bound_s=5.0,
    ),
    "DEV-V4-LMK-DOMAIN-A": _town10_landmark(
        "DEVELOPMENT", scene_role="LMK_DOMAIN_SHIFT", near_y=64.4, far_y=82.2, bound_s=5.0,
    ),
    "DEV-V4-MATURITY-A": _town12_visual(
        "DEVELOPMENT", scene_role="ACQUISITION_CLOCK_MATURITY", near_x=42.4, far_x=35.7,
        near_reveal_y=4505.8, far_y=4519.6, bound_s=5.0,
        near_blueprint="vehicle.chevrolet.impala", far_blueprint="vehicle.citroen.c3",
    ),
    "DEV-V4-REACQUISITION-A": _town12_visual(
        "DEVELOPMENT", scene_role="COMPATIBLE_REACQUISITION", near_x=41.6, far_x=34.9,
        near_reveal_y=4506.2, far_y=4518.9, bound_s=7.0,
        near_blueprint="vehicle.ford.mustang", far_blueprint="vehicle.mini.cooper_s",
        extra_events=[
            {"at_simulation_s": 3.0, "actor_index": 0, "event": "TEMPORARY_TRACK_LOSS", "transform": [34.9, 4540.0, 372.7745, 89.9]},
            {"at_simulation_s": 4.5, "actor_index": 0, "event": "COMPATIBLE_REACQUISITION", "transform": [35.1, 4518.9, 372.7745, 179.8]},
        ],
    ),
    "DEV-V4-ORDER-LIFECYCLE-A": _order(
        "DEVELOPMENT", scene_role="ORDER_LIFECYCLE", base_route="driveclarify_rq2_t/formal_routes/ord-01.xml",
        town="Town12", bound_s=5.0,
    ),
    "DEV-V4-USC-LIFECYCLE-A": _usc("DEVELOPMENT", scene_role="USC_LIFECYCLE", bound_s=5.0),
    "DEV-V4-NONREVEAL-A": _town12_visual(
        "DEVELOPMENT", scene_role="NONREVEAL_FALSE_POSITIVE", near_x=40.8, far_x=33.8,
        near_reveal_y=4505.4, far_y=4518.1, reveal=False, bound_s=5.0,
        near_blueprint="vehicle.dodge.charger_2020", far_blueprint="vehicle.toyota.prius",
    ),
    "CAL-V4-REF-POS-A": _town12_visual(
        "CALIBRATION", scene_role="REF_CALIBRATION_POSITIVE", near_x=44.2, far_x=37.1,
        near_reveal_y=4507.1, far_y=4521.2, bound_s=5.0,
        near_blueprint="vehicle.audi.tt", far_blueprint="vehicle.seat.leon",
    ),
    "CAL-V4-LMK-POS-A": _town10_landmark(
        "CALIBRATION", scene_role="LMK_CALIBRATION_POSITIVE", near_y=65.7, far_y=83.4,
        bound_s=5.0, near_blueprint="vehicle.volkswagen.t2_2021",
        far_blueprint="vehicle.nissan.patrol_2021",
    ),
    "CAL-V4-NONREVEAL-A": _town10_landmark(
        "CALIBRATION", scene_role="CALIBRATION_NONREVEAL", near_y=66.8, far_y=84.6,
        reveal=False, bound_s=5.0, near_blueprint="vehicle.mercedes.coupe_2020",
        far_blueprint="vehicle.bmw.grandtourer",
    ),
    "CAL-V4-USC-A": _usc("CALIBRATION", scene_role="CALIBRATION_INTRINSIC_USC", bound_s=5.0),
    # Fresh identity after ENG-001 exposed a route-trigger yaw plumbing defect.
    # The A identity and its exact route remain immutable failed evidence.
    "DEV-V4-REF-DOMAIN-B": _town12_visual(
        "DEVELOPMENT", scene_role="REF_DOMAIN_SHIFT_ROUTE_YAW_REPAIR", near_x=43.35, far_x=36.55,
        near_reveal_y=4506.9, far_y=4520.65, bound_s=5.0,
        near_blueprint="vehicle.nissan.patrol", far_blueprint="vehicle.jeep.wrangler_rubicon",
    ),
}


def _load_materialized_blind() -> Mapping[str, Mapping[str, Any]]:
    if not BLIND_MANIFEST.is_file():
        return {}
    manifest = json.loads(BLIND_MANIFEST.read_text(encoding="utf-8"))
    rows = manifest.get("scenes") or ()
    return {str(row["scene"]): dict(row["binding"]) for row in rows}


def frozen_binding(scene_config_id: str) -> Mapping[str, Any]:
    if scene_config_id in SCENE_BINDINGS:
        value = SCENE_BINDINGS[scene_config_id]
    else:
        blind = _load_materialized_blind()
        if scene_config_id not in blind:
            raise KeyError("E2_V4_SCENE_NOT_PROSPECTIVELY_REGISTERED:" + scene_config_id)
        value = blind[scene_config_id]
    return {
        **value,
        "scene_config_id": scene_config_id,
        "scene_configuration_sha256": canonical_sha256(value),
    }


__all__ = [
    "BLIND_MANIFEST", "NEAR_FAR_CARS", "NEAR_FAR_MARKERS", "ORDER_CANDIDATES",
    "SCENE_BINDINGS", "USC_CANDIDATES", "frozen_binding",
]
