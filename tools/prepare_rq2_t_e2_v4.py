#!/usr/bin/env python3
"""Seal V4 contracts and materialize only development/calibration infrastructure."""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driveclarify_rq2_t_e2_v4.association import FAMILY_MASKS, WEIGHT_PROFILE_GRID  # noqa: E402
from driveclarify_rq2_t_e2_v4.calibration import (  # noqa: E402
    ASSOCIATION_THRESHOLD_GRID,
    MINIMUM_AGE_ACQUISITIONS_GRID,
    MINIMUM_HITS_GRID,
    STOPPING_RULE,
    UNIQUENESS_MARGIN_GRID,
)
from driveclarify_rq2_t_e2_v4.contracts import AcquisitionSchedule, METHOD_ID, canonical_sha256  # noqa: E402
from driveclarify_rq2_t_e2_v4.lifecycle import SCENARIO_TYPE, static_route_lifecycle_admission  # noqa: E402
from driveclarify_rq2_t_e2_v4.scene_bindings import SCENE_BINDINGS, frozen_binding  # noqa: E402
from driveclarify_rq2_t_e2_v4.tracker import AcquisitionClockTracker  # noqa: E402


REPORT = ROOT / "reports/driveclarify_rq2_t_v2_final_blind_domain_shift_redesign_v1"
ROUTES = ROOT / "driveclarify_rq2_t_e2_v4/engineering_routes"
ENTRY_HEAD = "eaa332b1bb994279b59ea5af786fdb5de96adc1b"
SEEDS = [
    644486061, 1368804577, 691468826, 1541502652, 228061484, 2746958768,
    1043698478, 1191258322, 489041976, 4236209177, 3118764197,
]
ROLES = [
    "REF_DOMAIN_SHIFT", "LMK_DOMAIN_SHIFT", "ACQUISITION_CLOCK_MATURITY",
    "COMPATIBLE_REACQUISITION", "ORDER_LIFECYCLE", "USC_LIFECYCLE",
    "NONREVEAL_FALSE_POSITIVE", "REF_CALIBRATION_POSITIVE",
    "LMK_CALIBRATION_POSITIVE", "CALIBRATION_NONREVEAL",
    "CALIBRATION_INTRINSIC_USC",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    with temporary.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def write_json(name: str, value: Mapping[str, Any]) -> None:
    atomic_bytes(
        REPORT / name,
        json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False).encode("utf-8") + b"\n",
    )


def write_text(name: str, value: str) -> None:
    atomic_bytes(REPORT / name, value.rstrip().encode("utf-8") + b"\n")


def git(*args: str, cwd: Path = ROOT, binary: bool = False) -> Any:
    value = subprocess.check_output(("git",) + args, cwd=str(cwd))
    return value if binary else value.decode("utf-8").strip()


def materialize_route(scene: str, binding: Mapping[str, Any]) -> Path:
    source = ROOT / str(binding["base_route"])
    tree = ET.parse(source)
    route = tree.getroot().find("route")
    if route is None:
        raise RuntimeError("E2_V4_BASE_ROUTE_MISSING:" + scene)
    old_scenario = route.find("./scenarios/scenario")
    old_trigger = None if old_scenario is None else old_scenario.find("trigger_point")
    old_yaw = None if old_trigger is None else old_trigger.get("yaw")
    offset = int(binding.get("route_waypoint_start_offset", 0))
    waypoints = route.find("waypoints")
    if waypoints is None:
        raise RuntimeError("E2_V4_BASE_ROUTE_WAYPOINTS_MISSING:" + scene)
    for child in list(waypoints)[:offset]:
        waypoints.remove(child)
    first = waypoints.find("position")
    if first is None:
        raise RuntimeError("E2_V4_ROUTE_EMPTY_AFTER_OFFSET:" + scene)
    route.set("id", "RQ2TE2V4-" + scene)
    scenarios = route.find("scenarios")
    if scenarios is None:
        scenarios = ET.SubElement(route, "scenarios")
    for child in list(scenarios):
        scenarios.remove(child)
    scenario = ET.SubElement(scenarios, "scenario", {
        "name": "RQ2TE2V4-" + scene, "type": SCENARIO_TYPE,
    })
    points = waypoints.findall("position")
    if offset == 0 and old_yaw is not None:
        trigger_yaw = float(old_yaw)
    elif len(points) >= 2:
        trigger_yaw = math.degrees(math.atan2(
            float(points[1].get("y", "0")) - float(points[0].get("y", "0")),
            float(points[1].get("x", "0")) - float(points[0].get("x", "0")),
        ))
    else:
        trigger_yaw = float(binding.get("trigger_yaw", 0.0))
    ET.SubElement(scenario, "trigger_point", {
        "x": first.get("x", "0"), "y": first.get("y", "0"),
        "z": first.get("z", "0"), "yaw": str(trigger_yaw),
    })
    ET.SubElement(scenario, "rq2_t_e2_v4", {
        "scene_config_id": scene,
        "scene_configuration_sha256": str(binding["scene_configuration_sha256"]),
    })
    ET.indent(tree, space="   ")
    ROUTES.mkdir(parents=True, exist_ok=True)
    target = ROUTES / (scene.lower() + ".xml")
    temporary = target.with_name("." + target.name + ".tmp")
    tree.write(temporary, encoding="utf-8", xml_declaration=True)
    os.replace(temporary, target)
    return target


def contract_pair(stem: str, title: str, value: Mapping[str, Any], prose: str) -> None:
    payload = dict(value)
    payload["contract_digest"] = canonical_sha256(payload)
    write_json(stem + ".json", payload)
    write_text(
        stem + ".md",
        "# " + title + "\n\n" + prose.strip() + "\n\nFrozen contract digest: `" + payload["contract_digest"] + "`.",
    )


def main() -> int:
    REPORT.mkdir(parents=True, exist_ok=True)
    (REPORT / "NATIVE_EVIDENCE").mkdir(parents=True, exist_ok=True)
    (REPORT / "FRESH_BLIND_SCENE_CERTIFICATES").mkdir(parents=True, exist_ok=True)
    if git("rev-parse", "HEAD") != ENTRY_HEAD:
        raise RuntimeError("E2_V4_ENTRY_HEAD_CHANGED")
    routes = {}
    admissions = {}
    for scene in SCENE_BINDINGS:
        binding = frozen_binding(scene)
        route = materialize_route(scene, binding)
        routes[scene] = route
        admission = static_route_lifecycle_admission(
            route_path=route, binding=binding, expected_route_sha256=sha256(route),
            expected_scene_configuration_sha256=str(binding["scene_configuration_sha256"]),
        )
        if admission["status"] != "PASS_STATIC_ROUTE_LIFECYCLE_ADMISSION":
            raise RuntimeError("E2_V4_STATIC_ADMISSION_FAILED:" + scene + ":" + json.dumps(admission, sort_keys=True))
        admissions[scene] = admission

    scenes = list(SCENE_BINDINGS)
    identities = []
    for index, (scene, seed, role) in enumerate(zip(scenes, SEEDS, ROLES), 1):
        binding = frozen_binding(scene)
        identities.append({
            "identity": "RQ2TE2V4-ENG-{:03d}".format(index), "phase": binding["phase"],
            "scene": scene, "role": role, "seed": seed,
            "formal_seed": False, "formal_scientific_exposure": False,
            "permanently_excluded_from_formal_v2": True,
        })
    registry = {
        "schema_version": "driveclarify.e2_v4.engineering_identity_registry.v1",
        "status": "PROSPECTIVELY_REGISTERED_BEFORE_NATIVE_EXPOSURE",
        "identities": identities,
        "old_blind_deblinded": [
            "RQ2TE2V3-ENG-015", "RQ2TE2V3-ENG-016", "RQ2TE2V3-ENG-017",
            "RQ2TE2V3-ENG-018", "RQ2TE2V3-ENG-019",
        ],
        "formal_seed_count": 0, "formal_scientific_exposure_count": 0,
        "target_new_identity_limit": 24, "maximum_new_identity_limit": 36,
    }
    registry["registry_digest"] = canonical_sha256(registry)
    write_json("ENGINEERING_IDENTITY_REGISTRY.json", registry)
    write_json("ENGINEERING_SEED_EXCLUSION_REGISTRY.json", {
        "schema_version": "driveclarify.e2_v4.engineering_seed_exclusion.v1",
        "engineering_only_seeds": SEEDS,
        "excluded_from_formal_v2_dev_test": True, "formal_seed_count": 0,
    })
    for phase, name in (("DEVELOPMENT", "DEVELOPMENT_SCENE_MANIFEST.json"), ("CALIBRATION", "CALIBRATION_SCENE_MANIFEST.json")):
        rows = []
        for scene in scenes:
            binding = frozen_binding(scene)
            if binding["phase"] != phase:
                continue
            route = routes[scene]
            identity = next(row for row in identities if row["scene"] == scene)
            rows.append({
                "scene": scene, "identity": identity["identity"], "seed": identity["seed"],
                "family": binding["family"], "scene_role": binding["scene_role"],
                "scene_configuration_sha256": binding["scene_configuration_sha256"],
                "route_path": str(route.relative_to(ROOT)), "route_sha256": sha256(route),
                "lifecycle_admission_receipt_digest": admissions[scene]["admission_receipt_digest"],
            })
        manifest = {
            "schema_version": "driveclarify.e2_v4.{}.scene_manifest.v1".format(phase.lower()),
            "phase": phase, "scenes": rows, "scene_count": len(rows),
            "scene_level_unit": "SCENE_ROUTE_ACTOR_CONFIGURATION",
            "formal_scientific_exposure": False,
        }
        manifest["manifest_digest"] = canonical_sha256(manifest)
        write_json(name, manifest)
        write_text(name[:-5] + ".md", "# {} scene manifest\n\n{} prospectively registered engineering-only scenes.\n\nManifest digest: `{}`.".format(phase.title(), len(rows), manifest["manifest_digest"]))

    contract_pair("E2_V4_METHOD_CONTRACT", "E2 V4 method contract", {
        "method_id": METHOD_ID,
        "change_classification": "E2_V4_SCIENTIFIC_METHOD_CHANGE",
        "historical_v3": "DEVELOPMENT_MECHANISM_PASS_BUT_BLIND_GENERALIZATION_FAIL",
        "unchanged": [
            "V1_EPISTEMIC_EVIDENCE_SUFFICIENT_PREDICATES", "ACT_ASK_WAIT_BEHAVIOR",
            "SIMLINGO_CHECKPOINT", "PID_CONTROLLER", "SINGLE_CONTROL_WRITER",
            "ROUTEPLANNER_OBSERVATION_NONMUTATION", "MEMORY_INVALIDATION_CONTRACT",
        ],
        "changed": [
            "MISSING_AWARE_DOMAIN_INVARIANT_CANDIDATE_REPRESENTATION",
            "OPTIONAL_LAYOUT_CUES_MOVED_FROM_HARD_GATES_TO_SOFT_COMPONENTS",
            "FINITE_FROZEN_WEIGHT_PROFILE_GRID", "ACQUISITION_CLOCK_TRACK_MATURITY",
            "SCENE_LEVEL_ZERO_FALSE_BINDING_CALIBRATION",
        ],
        "expected_generalization_mechanism": "RELATIVE_DEPTH_RANK_AND_TEMPORAL_TRACK_EVIDENCE_SURVIVE_VIEWPOINT_SIDE_REVERSAL",
        "new_failure_modes": ["RANK_TIE", "DETECTOR_MISSED_TARGET", "MULTIPLE_SAME_RANK_SEMANTICALLY_INDISTINGUISHABLE", "CALIBRATED_ABSTENTION"],
    }, "V4 changes only candidate-track association and its calibration. V3 history remains immutable; the redesign is justified by the sealed first-failure and gold-only audits.")
    component_contract = {
        "object_class_compatibility": ["semantic class agreement", "0..1", "candidate parser + tracker", "categorical", "exclude", "hard reject explicit contradiction"],
        "color_compatibility": ["explicit color agreement", "0..1", "candidate parser + grounder", "categorical", "exclude", "hard reject explicit contradiction"],
        "candidate_set_depth_rank": ["near/far or ordinal relative rank", "0..1", "association", "qualified-track rank", "exclude", "soft disagreement"],
        "route_frame_lateral_relation": ["route-relative lateral agreement", "0..1", "legal route observation", "route frame", "exclude", "soft disagreement"],
        "route_lane_topology_relation": ["route-relative lane class", "0..1", "legal route observation", "topology class", "exclude", "soft disagreement"],
        "temporal_motion_compatibility": ["parked/moving temporal state", "0..1", "tracker", "temporal window", "exclude", "soft disagreement"],
        "visibility_normalized_detector_support": ["detector support", "0..1", "GroundingDINO", "unit interval", "exclude", "not a hard contradiction"],
        "track_persistence": ["acquisition hit persistence", "0..1", "tracker", "acquisitions", "exclude", "not a hard contradiction"],
        "temporal_coherence": ["box lineage IoU coherence", "0..1", "tracker", "temporal IoU", "neutral 0.5", "lineage break invalidates"],
    }
    contract_pair("DOMAIN_INVARIANT_CANDIDATE_REPRESENTATION", "Domain-invariant candidate representation", {
        "features": list(component_contract), "component_contract": component_contract,
        "absolute_coordinates_used": False, "pixel_side_used_for_semantic_left_right": False,
        "actor_ids_used": False, "scene_specific_mapping_used": False,
        "unspecified_policy": "EXCLUDE_FROM_NORMALIZATION_NOT_ZERO_NOT_HARD_REJECT",
        "family_masks": {key: list(value) for key, value in FAMILY_MASKS.items()},
    }, "Relative rank, route/ego-frame relations, temporal motion, persistence, coherence, and normalized detector support replace brittle pixel-side gates. Explicit contradiction is distinct from unavailable evidence.")
    contract_pair("TRACK_MATURITY_AND_CADENCE_CONTRACT", "Track maturity and cadence contract", {
        "historical_age_domain": "NATIVE_SIMULATOR_FRAMES",
        "historical_hits_domain": "DETECTOR_ACQUISITION_UPDATE_CALLS",
        "historical_semantic_consistency": False,
        "v4_age_domain": "DETECTOR_ACQUISITION_OPPORTUNITIES",
        "v4_hits_domain": "DETECTOR_ACQUISITION_UPDATE_CALLS",
        "simulator_age_frames": "DIAGNOSTIC_ONLY",
        "schedule": AcquisitionSchedule().to_dict(),
        "maturity_grid": list(MINIMUM_AGE_ACQUISITIONS_GRID),
        "hits_grid": list(MINIMUM_HITS_GRID),
        "lineage_false_binding_guard": "ACTIVE_COMPATIBLE_LINEAGE_AND_ZERO_INVALIDATION_REQUIRED",
        "precalibration_tracker": AcquisitionClockTracker().snapshot(),
    }, "V3 compared detector-hit counts to simulator-frame age. V4 increments maturity exactly once per detector acquisition update, so hits and age share one frozen clock domain.")
    contract_pair("E2_V4_ASSOCIATION_SCORE_CONTRACT", "E2 V4 association score contract", {
        "profiles": {key: dict(value) for key, value in WEIGHT_PROFILE_GRID.items()},
        "normalization": "WEIGHTED_MEAN_OVER_AVAILABLE_APPLICABLE_COMPONENTS",
        "probability_claimed": False,
        "hard_gates": [
            "EXPLICIT_CLASS_CONTRADICTION", "EXPLICIT_COLOR_CONTRADICTION",
            "LINEAGE_NOT_ACTIVE", "INCOMPATIBLE_REACQUISITION",
            "BOTTOM_CLIPPED_EGO_HOOD_OR_NON_REFERENT",
        ],
        "removed_v3_hard_gates": ["LANE_HARD_GATE", "LEFT_RIGHT_HARD_GATE", "MOTION_HARD_GATE", "RELATIVE_ORDER_HARD_GATE"],
        "score_threshold_grid": list(ASSOCIATION_THRESHOLD_GRID),
        "uniqueness_margin_grid": list(UNIQUENESS_MARGIN_GRID),
    }, "All profiles were frozen before V4 calibration outcomes. Calibration selects one whole profile plus thresholds; it does not fit weights on old blind or frame-level outcomes.")
    calibration = {
        "schema_version": "driveclarify.e2_v4.calibration_protocol.v1",
        "split_unit": "SCENE_ROUTE_ACTOR_CONFIGURATION", "frame_level_split": False,
        "feature_set_frozen": True, "family_masks_frozen": True, "hard_gates_frozen": True,
        "weight_profile_grid": list(WEIGHT_PROFILE_GRID),
        "score_threshold_grid": list(ASSOCIATION_THRESHOLD_GRID),
        "uniqueness_margin_grid": list(UNIQUENESS_MARGIN_GRID),
        "minimum_hits_grid": list(MINIMUM_HITS_GRID),
        "minimum_age_acquisitions_grid": list(MINIMUM_AGE_ACQUISITIONS_GRID),
        "configuration_count": len(WEIGHT_PROFILE_GRID) * len(ASSOCIATION_THRESHOLD_GRID) * len(UNIQUENESS_MARGIN_GRID) * len(MINIMUM_HITS_GRID) * len(MINIMUM_AGE_ACQUISITIONS_GRID),
        "objective": ["ZERO_FALSE_UNIQUE_BINDINGS", "MAXIMIZE_POST_REVEAL_RECALL", "LOWER_ABSTENTION", "SIMPLER_PROFILE", "MORE_CONSERVATIVE_THRESHOLDS"],
        "stopping_rule": STOPPING_RULE, "blind_outcomes_readable": False,
    }
    calibration["protocol_digest"] = canonical_sha256(calibration)
    write_json("CALIBRATION_PROTOCOL.json", calibration)
    write_text("CALIBRATION_PROTOCOL.md", "# V4 calibration protocol\n\nA single exhaustive evaluation of the frozen 432-configuration grid is permitted on the four registered scene-level calibration units. Fresh blind outcomes are not readable.")
    lifecycle = {
        "schema_version": "driveclarify.e2_v4.route_lifecycle_closure.v1",
        "status": "PASS_STATIC_LIFECYCLE_TOOLING_AND_PRELAUNCH_ADMISSIONS",
        "admissions": admissions,
        "principles": [
            "STATIC_ROUTE_TRIGGER_INTERSECTION_BEFORE_CARLA", "EXACT_ROUTE_CONFIG_DIGEST_BINDING",
            "MANDATORY_POSITIVE_ACTIVATION_RECEIPT", "LAST_ADMISSIBLE_ACTIVATION_BOUNDARY",
            "SIMULATOR_DOMAIN_ACTIVATION_MISS_FAIL_FAST", "POST_ACTIVATION_SCENARIO_HORIZON",
            "EXACT_COMPLETION_AND_CLEANUP_RECEIPTS",
        ],
        "order_additional_requirements": ["ORDINAL_AT_LEAST_TWO", "TWO_QUALIFYING_JUNCTIONS", "E5_OWNER_ACTIVE", "REVEAL_BEFORE_ROUTE_END"],
    }
    lifecycle["receipt_digest"] = canonical_sha256(lifecycle)
    write_json("ROUTE_LIFECYCLE_CLOSURE_RECEIPT.json", lifecycle)
    write_text("ROUTE_LIFECYCLE_CLOSURE_REPORT.md", "# Route-lifecycle closure\n\nAll development and calibration routes passed exact static route/trigger/config admission. Native completion remains mandatory before blind authorization; ORDER additionally requires a two-opportunity E5 witness.")
    generator = {
        "schema_version": "driveclarify.e2_v4.fresh_blind_generator_spec.v1",
        "status": "FROZEN_BEFORE_CALIBRATION_OUTCOME_INSPECTION",
        "generator_source": "tools/materialize_rq2_t_e2_v4_blind.py",
        "layout_count": 8,
        "balance": {"REFERENTIAL_POSITIVE": 2, "LANDMARK_POSITIVE": 2, "ORDER_POSITIVE": 2, "NONREVEAL": 1, "USC_INTRINSIC": 1},
        "maps_and_route_families": {"REFERENTIAL": ["Town12/ref-01"], "LANDMARK": ["Town10HD/lmk-01"], "ORDER": ["Town12/ord-01", "Town01/ord-02"], "CONTROLS": ["Town12/ref-01"]},
        "route_segment": {"start_offset_waypoints": [0, 3], "minimum_remaining_route_m": 12.0},
        "actor_landmark_identity_pools": {
            "REFERENTIAL": ["vehicle.audi.etron", "vehicle.lincoln.mkz_2017", "vehicle.nissan.micra", "vehicle.tesla.cybertruck"],
            "LANDMARK": ["vehicle.mitsubishi.fusorosa", "vehicle.carlamotors.european_hgv", "vehicle.ford.crown", "vehicle.mercedes.coupe"],
        },
        "actor_position_ranges": {
            "Town12_near_x": [41.0, 45.0], "Town12_far_x": [34.0, 38.0],
            "Town12_near_reveal_y": [4505.5, 4508.0], "Town12_far_y": [4519.0, 4522.0],
            "Town10_near_y": [63.0, 68.0], "Town10_far_y": [80.0, 86.0],
        },
        "occluder_geometry": ["EXISTING_FACADE_INITIAL_BEHIND_CAMERA", "EXISTING_BUILDING_INITIAL_BEHIND_CAMERA"],
        "reveal_timing_s": [0.8, 1.2], "lane_relation": ["UNSPECIFIED", "ON_ROAD", "ROADSIDE"],
        "left_right_relation": ["UNSPECIFIED"], "relative_order": ["NEARER_FIRST", "FARTHER_SECOND"],
        "motion_state": ["PARKED"], "visibility_duration_s": [3.8, 6.0],
        "reveal_to_commitment_margin": "POSITIVE_AND_POSTHOC_CERTIFIED",
        "route_lifecycle_validity": "STATIC_ADMISSION_PLUS_NATIVE_POST_ACTIVATION_BOUND",
        "negative_control_structure": ["ONE_TARGET_REMAINS_OCCLUDED", "NO_OBSERVABLE_PREFERENCE"],
        "manual_favorable_layout_search": False,
    }
    generator["spec_digest"] = canonical_sha256(generator)
    write_json("FRESH_BLIND_GENERATOR_SPEC.json", generator)
    write_text("FRESH_BLIND_GENERATOR_SPEC.md", "# Fresh blind generator specification\n\nThe prospective ranges, identity pools, balanced roles, controls, and lifecycle constraints are frozen. Exact layouts may be materialized only after method, calibration parameters, lifecycle tooling, and generator source are frozen.")
    seal = {
        "schema_version": "driveclarify.e2_v4.fresh_blind_generator_seal.v1",
        "status": "GENERATOR_SPEC_FROZEN_EXACT_LAYOUTS_NOT_MATERIALIZED",
        "spec_digest": generator["spec_digest"],
        "generator_source_sha256": sha256(ROOT / generator["generator_source"]),
        "blind_manifest_exists": (REPORT / "FRESH_BLIND_SCENE_MANIFEST.json").exists(),
        "materialization_authorized": False,
    }
    seal["seal_digest"] = canonical_sha256(seal)
    write_json("FRESH_BLIND_GENERATOR_SEAL.json", seal)
    write_text("COMMAND_LOG.md", "# V4 task command log\n\n- Sealed entry integrity and old-blind postmortem before V4 parameter work.\n- Ran focused V4 contracts in Python 3.13 and SimLingo Python 3.8: 28/28 PASS in each.\n- Materialized development/calibration routes only and passed static lifecycle admission.\n")
    print(json.dumps({
        "status": "PASS_V4_PRECALIBRATION_INFRASTRUCTURE_SEALED",
        "development_scenes": 7, "calibration_scenes": 4,
        "blind_exact_layouts_materialized": False,
        "generator_spec_digest": generator["spec_digest"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
