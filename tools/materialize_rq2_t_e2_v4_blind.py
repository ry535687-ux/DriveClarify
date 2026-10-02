#!/usr/bin/env python3
"""One-shot exact fresh-blind materializer; callable only after V4 freeze."""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driveclarify_rq2_t_e2_v4.contracts import canonical_sha256  # noqa: E402
from driveclarify_rq2_t_e2_v4.scene_bindings import (  # noqa: E402
    NEAR_FAR_CARS, NEAR_FAR_MARKERS, ORDER_CANDIDATES, USC_CANDIDATES,
)


REPORT = ROOT / "reports/driveclarify_rq2_t_v2_final_blind_domain_shift_redesign_v1"
SEEDS = [107339907, 3803933653, 611118815, 4109659386, 3913138873, 3157249188, 3510869268, 2802864520]
SCENES = [
    "BLIND-V4-REF-01", "BLIND-V4-REF-02", "BLIND-V4-LMK-01", "BLIND-V4-LMK-02",
    "BLIND-V4-ORD-01", "BLIND-V4-ORD-02", "BLIND-V4-NONREVEAL-01", "BLIND-V4-USC-01",
]


def uniform(rng, lo, hi):
    return round(rng.uniform(lo, hi), 4)


def visual(rng, role, near_bp, far_bp, reveal=True):
    near_x, far_x = uniform(rng, 41.0, 45.0), uniform(rng, 34.0, 38.0)
    near_y, far_y = uniform(rng, 4505.5, 4508.0), uniform(rng, 4519.0, 4522.0)
    reveal_s = uniform(rng, 0.8, 1.2)
    events = [] if not reveal else [{
        "at_simulation_s": reveal_s, "actor_index": 1, "event": "VISIBILITY_REVEAL",
        "transform": [near_x, near_y, 372.6740, 179.90],
    }]
    return {
        "phase": "BLIND", "family": "REFERENTIAL", "town": "Town12",
        "base_route": "driveclarify_rq2_t/formal_routes/ref-01.xml",
        "instruction": "Turn after the parked vehicle.", "runtime_candidates": NEAR_FAR_CARS,
        "actors": [
            {"blueprint": far_bp, "color": "255,255,255", "initial": [far_x, far_y, 372.7745, 179.8666], "candidate_id": "z2", "certificate_role": "FARTHER_TARGET"},
            {"blueprint": near_bp, "color": "255,255,255", "initial": [near_x, uniform(rng, 4534.0, 4538.0), 372.6740, 89.92], "candidate_id": "z1", "certificate_role": "NEARER_REVEAL_TARGET"},
        ],
        "events": events, "engineering_bound_s": uniform(rng, 5.0, 6.0),
        "reveal_contract": "GENERATED_FACADE_OCCLUSION_TO_DEPTH_RANK_TARGETS" if reveal else "GENERATED_PERMANENT_NONREVEAL",
        "expected_scoring": "BLIND_POSITIVE_REVEAL" if reveal else "BLIND_NEGATIVE_NONREVEAL",
        "scene_role": role,
    }


def landmark(rng, role, near_bp, far_bp):
    near_y, far_y = uniform(rng, 63.0, 68.0), uniform(rng, 80.0, 86.0)
    return {
        "phase": "BLIND", "family": "LANDMARK", "town": "Town10HD",
        "base_route": "driveclarify_rq2_t/formal_routes/lmk-01.xml",
        "instruction": "Use the opening after the parked roadside marker.", "runtime_candidates": NEAR_FAR_MARKERS,
        "actors": [
            {"blueprint": far_bp, "color": "255,255,255", "initial": [101.4, far_y, 0.35, -90.0], "candidate_id": "z2", "certificate_role": "FARTHER_LANDMARK_TARGET"},
            {"blueprint": near_bp, "color": "255,255,255", "initial": [96.4, uniform(rng, 20.0, 30.0), 0.35, 180.0], "candidate_id": "z1", "certificate_role": "NEARER_LANDMARK_REVEAL_TARGET"},
        ],
        "events": [{"at_simulation_s": uniform(rng, 0.8, 1.2), "actor_index": 1, "event": "VISIBILITY_REVEAL", "transform": [96.4, near_y, 0.35, -90.0]}],
        "engineering_bound_s": uniform(rng, 5.0, 6.0),
        "reveal_contract": "GENERATED_BUILDING_OCCLUSION_TO_DEPTH_RANK_MARKERS",
        "expected_scoring": "BLIND_POSITIVE_REVEAL", "scene_role": role,
    }


def main():
    target = REPORT / "FRESH_BLIND_SCENE_MANIFEST.json"
    if target.exists():
        raise RuntimeError("E2_V4_BLIND_MATERIALIZATION_ONE_SHOT_ALREADY_CONSUMED")
    freeze = json.loads((REPORT / "SOURCE_FREEZE_RECEIPT.json").read_text(encoding="utf-8"))
    parameter = json.loads((REPORT / "FINAL_PARAMETER_FREEZE.json").read_text(encoding="utf-8"))
    seal = json.loads((REPORT / "FRESH_BLIND_GENERATOR_SEAL.json").read_text(encoding="utf-8"))
    if freeze.get("pre_materialization_freeze_authorized") is not True or parameter.get("status") != "FROZEN_BEFORE_BLIND_MATERIALIZATION":
        raise RuntimeError("E2_V4_BLIND_MATERIALIZATION_BEFORE_METHOD_PARAMETER_FREEZE")
    if seal.get("status") != "GENERATOR_SPEC_FROZEN_EXACT_LAYOUTS_NOT_MATERIALIZED":
        raise RuntimeError("E2_V4_BLIND_GENERATOR_NOT_FROZEN")
    pools = [
        ("vehicle.audi.etron", "vehicle.lincoln.mkz_2017"),
        ("vehicle.nissan.micra", "vehicle.tesla.cybertruck"),
        ("vehicle.mitsubishi.fusorosa", "vehicle.carlamotors.european_hgv"),
        ("vehicle.ford.crown", "vehicle.mercedes.coupe"),
    ]
    bindings = []
    for index, (scene, seed) in enumerate(zip(SCENES, SEEDS)):
        rng = random.Random(seed)
        if index < 2:
            binding = visual(rng, "FRESH_BLIND_REFERENTIAL_POSITIVE", *pools[index])
        elif index < 4:
            binding = landmark(rng, "FRESH_BLIND_LANDMARK_POSITIVE", *pools[index])
        elif index == 4:
            binding = {
                "phase": "BLIND", "family": "ORDER", "town": "Town12",
                "base_route": "driveclarify_rq2_t/formal_routes/ord-01.xml", "route_waypoint_start_offset": 1,
                "instruction": "Take the second right turn.", "runtime_candidates": ORDER_CANDIDATES,
                "actors": [], "events": [], "engineering_bound_s": 5.0, "required_topology_ordinal": 2,
                "reveal_contract": "GENERATED_SHORT_ROUTE_TWO_LOCAL_JUNCTION_OPPORTUNITIES",
                "expected_scoring": "BLIND_E5_POSITIVE", "scene_role": "FRESH_BLIND_ORDER_POSITIVE",
            }
        elif index == 5:
            binding = {
                "phase": "BLIND", "family": "ORDER", "town": "Town01",
                "base_route": "driveclarify_rq2_t/formal_routes/ord-02.xml", "route_waypoint_start_offset": 2,
                "instruction": "Take the second right turn.", "runtime_candidates": ORDER_CANDIDATES,
                "actors": [], "events": [], "engineering_bound_s": 3.0, "required_topology_ordinal": 2,
                "reveal_contract": "GENERATED_SHORT_ROUTE_TWO_LOCAL_JUNCTION_OPPORTUNITIES",
                "expected_scoring": "BLIND_E5_POSITIVE", "scene_role": "FRESH_BLIND_ORDER_POSITIVE",
            }
        elif index == 6:
            binding = visual(rng, "FRESH_BLIND_NONREVEAL_CONTROL", "vehicle.dodge.charger_police_2020", "vehicle.toyota.prius", reveal=False)
        else:
            binding = {
                "phase": "BLIND", "family": "UNDERSPECIFIED_CONSTRAINT", "town": "Town12",
                "base_route": "driveclarify_rq2_t/formal_routes/ref-01.xml",
                "instruction": "Pull over when convenient.", "runtime_candidates": USC_CANDIDATES,
                "actors": [], "events": [], "engineering_bound_s": 5.0, "expected_semantic_actor_count": 0,
                "reveal_contract": "PASSIVE_ENVIRONMENT_CANNOT_SUPPLY_MISSING_PREFERENCE",
                "expected_scoring": "BLIND_NEGATIVE_INTRINSIC_USC", "scene_role": "FRESH_BLIND_USC_INTRINSIC_CONTROL",
            }
        bindings.append({
            "scene": scene, "identity": "RQ2TE2V4-ENG-{:03d}".format(index + 13),
            "seed": seed, "binding": binding, "scene_configuration_sha256": canonical_sha256(binding),
            "formal_seed": False, "formal_scientific_exposure": False,
        })
    manifest = {
        "schema_version": "driveclarify.e2_v4.fresh_blind_scene_manifest.v1",
        "status": "EXACT_LAYOUTS_MATERIALIZED_ONCE_AFTER_COMPLETE_FREEZE",
        "scenes": bindings, "scene_count": len(bindings),
        "generator_spec_digest": seal["spec_digest"], "generator_seal_digest": seal["seal_digest"],
        "materialization_count": 1, "outcomes_inspected": False,
        "all_permanently_excluded_from_formal_v2": True,
    }
    manifest["manifest_digest"] = canonical_sha256(manifest)
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (REPORT / "FRESH_BLIND_SCENE_MANIFEST.md").write_text(
        "# Fresh blind scene manifest\n\nEight exact layouts were materialized once after the complete pre-materialization freeze. No mechanism outcome had been inspected.\n\nManifest digest: `{}`.\n".format(manifest["manifest_digest"]), encoding="utf-8",
    )
    print(json.dumps({"status": manifest["status"], "scene_count": 8, "manifest_digest": manifest["manifest_digest"]}, indent=2))


if __name__ == "__main__":
    main()
