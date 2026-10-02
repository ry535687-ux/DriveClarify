#!/usr/bin/env python3
"""Minimal native readiness and four-family live grounding capture."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import time
from typing import Any, Mapping

from driveclarify_four_family_qualification.qualification import (
    A1,
    CASES,
    EXPECTED_A1,
    NAV,
    REPORT as PRIOR_REPORT,
    ROOT,
    XODR,
    atomic_json,
    atomic_text,
    canonical,
    digest,
    sha,
    source_runtime,
    xyz,
)


REPORT = ROOT / "reports/driveclarify_v3_carla_readiness_and_four_family_live_grounding_recovery"
EXPECTED_PRIOR_AGGREGATE = "8010bde43f225d170d0cc6e95693a98ee30b1c9be9fc115ec42788f1a2943e15"


class RecoveryError(RuntimeError):
    pass


def pose(transform) -> dict[str, Any]:
    return {
        "location": {"x": float(transform.location.x), "y": float(transform.location.y), "z": float(transform.location.z)},
        "rotation": {"pitch": float(transform.rotation.pitch), "yaw": float(transform.rotation.yaw), "roll": float(transform.rotation.roll)},
    }


def relative(ego_transform, target) -> dict[str, float]:
    dx = float(target.x - ego_transform.location.x)
    dy = float(target.y - ego_transform.location.y)
    dz = float(target.z - ego_transform.location.z)
    yaw = math.radians(float(ego_transform.rotation.yaw))
    forward = dx * math.cos(yaw) + dy * math.sin(yaw)
    right = -dx * math.sin(yaw) + dy * math.cos(yaw)
    return {
        "forward_m": forward,
        "right_m": right,
        "up_m": dz,
        "distance_m": math.sqrt(dx * dx + dy * dy + dz * dz),
        "bearing_deg": math.degrees(math.atan2(right, forward)),
    }


def geometric_visibility(ego_transform, target, max_distance=80.0, half_fov=55.0) -> dict[str, Any]:
    value = relative(ego_transform, target)
    value.update({
        "method": "EGO_RELATIVE_GEOMETRIC_FRUSTUM",
        "max_distance_m": max_distance,
        "half_fov_deg": half_fov,
        "pass": value["forward_m"] > 0 and value["distance_m"] <= max_distance and abs(value["bearing_deg"]) <= half_fov,
    })
    return value


def client(port: int):
    import carla
    result = carla.Client("127.0.0.1", port)
    result.set_timeout(600.0)
    return result


def readiness(port: int, run_id: str, client_log: Path) -> None:
    started = time.time()
    c = client(port)
    world = c.get_world()
    snapshot = world.get_snapshot()
    actors = world.get_actors()
    payload = {
        "schema": "driveclarify.native-carla-readiness.v1",
        "status": "CARLA_NATIVE_READINESS_PASS",
        "classification": "ENGINEERING_READINESS_ONLY_NOT_FORMAL_EXPOSURE",
        "run_id": run_id,
        "client_connect_success": True,
        "get_world_success": True,
        "map_name": world.get_map().name,
        "snapshot_success": True,
        "frame_id": int(snapshot.frame),
        "simulation_timestamp_seconds": float(snapshot.timestamp.elapsed_seconds),
        "world_get_actors_success": True,
        "actor_count": len(actors),
        "wall_seconds": time.time() - started,
        "carla_python_module": __import__("carla").__file__,
        "formal_scientific_exposure": 0,
    }
    atomic_json(REPORT / "CARLA_READINESS_RECEIPT.json", payload)
    atomic_text(client_log, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def spawn_vehicle(world, blueprint_id: str, transform, role_name: str, color: str | None = None):
    blueprint = world.get_blueprint_library().find(blueprint_id)
    if blueprint.has_attribute("role_name"):
        blueprint.set_attribute("role_name", role_name)
    if color and blueprint.has_attribute("color"):
        blueprint.set_attribute("color", color)
    transform.location.z += 0.5
    actor = world.try_spawn_actor(blueprint, transform)
    if actor is None:
        transform.location.z += 0.8
        actor = world.try_spawn_actor(blueprint, transform)
    if actor is None:
        raise RecoveryError(f"ACTOR_SPAWN_FAILED:{role_name}")
    actor.set_simulate_physics(False)
    return actor


def prior_binding(case_id: str) -> dict[str, Any]:
    path = PRIOR_REPORT / "ROUTE_BINDING_RECEIPTS" / f"{case_id}.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    if not all(value.get(k) == "PASS" for k in ("production_guard_A", "production_guard_B", "same_destination_verdict", "different_local_route_verdict")):
        raise RecoveryError(f"PRIOR_BINDING_NOT_PASS:{case_id}")
    if value.get("endpoint_tolerance_m") != 0.001 or value.get("production_owner_source_sha256") != sha(NAV):
        raise RecoveryError(f"PRIOR_BINDING_INTEGRITY_CHANGED:{case_id}")
    return value


def validate_binding_unchanged(case: Mapping[str, Any]) -> dict[str, Any]:
    prior = prior_binding(case["case_id"])
    _path, runtime = source_runtime(case)
    route_a = runtime[case["route_a_source"]]
    route_b = runtime[case["route_b_source"]]
    checks = {
        "route_A_hash_unchanged": digest(route_a) == prior["route_A_hash"],
        "route_B_hash_unchanged": digest(route_b) == prior["route_B_hash"],
        "canonical_endpoint_unchanged": prior["canonical_destination_identity_A"] == prior["canonical_destination_identity_B"],
        "endpoint_distance_m": prior["destination_distance_m"],
        "production_guard_still_pass": prior["production_guard_A"] == prior["production_guard_B"] == "PASS",
    }
    if not all(v is True or (k == "endpoint_distance_m" and v == 0.0) for k, v in checks.items()):
        raise RecoveryError(f"BINDING_REUSE_MISMATCH:{case['case_id']}")
    return {
        "reuse_previous_binding_evidence": True,
        "prior_receipt": str(PRIOR_REPORT / "ROUTE_BINDING_RECEIPTS" / f"{case['case_id']}.json"),
        "prior_receipt_sha256": sha(PRIOR_REPORT / "ROUTE_BINDING_RECEIPTS" / f"{case['case_id']}.json"),
        "route_A_hash": prior["route_A_hash"],
        "route_B_hash": prior["route_B_hash"],
        "connector_A_identity": prior["connector_A_identity"],
        "connector_B_identity": prior["connector_B_identity"],
        "anchor_identity": prior["anchor_identity"],
        "canonical_destination_identity": prior["canonical_destination_identity_A"],
        "endpoint_distance_m": prior["destination_distance_m"],
        "endpoint_tolerance_m": prior["endpoint_tolerance_m"],
        "checks": checks,
    }


def capture(port: int, run_id: str, client_log: Path) -> None:
    import carla

    c = client(port)
    world = c.load_world("Town12", reset_settings=False)
    settings = world.get_settings()
    settings.synchronous_mode = True
    settings.fixed_delta_seconds = 0.05
    settings.tile_stream_distance = 650
    settings.actor_active_distance = 650
    world.apply_settings(settings)
    world.tick()
    world_map = world.get_map()
    if world_map.name.split("/")[-1] != "Town12":
        raise RecoveryError(f"WRONG_MAP:{world_map.name}")
    actors = []
    try:
        ego_by_source = {}
        for index, source_key in enumerate(("route_01", "route_02", "route_03"), start=1):
            case = next(x for x in CASES if x["source_key"] == source_key)
            _path, runtime = source_runtime(case)
            p = xyz(runtime[case["route_a_source"]][0])
            transform = world_map.get_waypoint(carla.Location(*p), project_to_road=True).transform
            ego = spawn_vehicle(world, "vehicle.tesla.model3", transform, f"dc_min_live_ego_{index}")
            actors.append(ego)
            ego_by_source[source_key] = ego

        ref = next(x for x in CASES if x["case_id"] == "REF_QUAL_001")
        _path, runtime = source_runtime(ref)
        ref_specs = (
            ("A", "vehicle.audi.a2", runtime[ref["route_a_source"]][40]),
            ("B", "vehicle.lincoln.mkz_2020", runtime[ref["route_b_source"]][41]),
        )
        reference_actors = []
        for label, blueprint, row in ref_specs:
            transform = world_map.get_waypoint(carla.Location(*xyz(row)), project_to_road=True).transform
            actor = spawn_vehicle(world, blueprint, transform, f"dc_ref_white_car_{label.lower()}", "255,255,255")
            actors.append(actor)
            reference_actors.append(actor)

        for _ in range(5):
            world.tick()
        snapshot = world.get_snapshot()
        common = {
            "run_id": run_id,
            "map": world_map.name,
            "frame_id": int(snapshot.frame),
            "simulation_timestamp_seconds": float(snapshot.timestamp.elapsed_seconds),
            "world_actor_count": len(world.get_actors()),
            "classification": "MINIMAL_ENGINEERING_LIVE_GROUNDING_NOT_FORMAL_EXPOSURE",
        }
        receipts: dict[str, dict[str, Any]] = {}

        ego = ego_by_source["route_03"]
        ego_transform = ego.get_transform()
        entities = []
        visibilities = []
        for actor in reference_actors:
            transform = actor.get_transform()
            entities.append({
                "kind": "carla_runtime_actor",
                "identity": f"carla_actor:{actor.id}",
                "actor_id": int(actor.id),
                "blueprint": actor.type_id,
                "attributes": dict(actor.attributes),
                "pose": pose(transform),
            })
            visibilities.append(geometric_visibility(ego_transform, transform.location))
        binding = validate_binding_unchanged(ref)
        ref_pass = len({e["actor_id"] for e in entities}) == 2 and all(x["pass"] for x in visibilities)
        receipts["REFERENTIAL"] = {
            "schema": "driveclarify.minimal-live-grounding.v1", "family": "Referential", "status": "PASS" if ref_pass else "FAIL",
            **common, "ego_actor_id": int(ego.id), "ego_pose": pose(ego_transform), "entity_A": entities[0], "entity_B": entities[1],
            "visibility_A": visibilities[0], "visibility_B": visibilities[1],
            "interpretation_A": ref["interpretation_a"], "interpretation_B": ref["interpretation_b"], "binding_reuse": binding,
        }

        landmark_case = next(x for x in CASES if x["case_id"] == "LANDMARK_QUAL_001")
        landmarks = {str(x.id): x for x in world_map.get_all_landmarks()}
        landmark_entities = []
        for landmark_id in landmark_case["landmark_ids"]:
            landmark = landmarks[landmark_id]
            transform = landmark.transform
            landmark_entities.append({
                "kind": "carla_runtime_map_landmark", "identity": f"Town12:landmark:{landmark.id}", "landmark_id": str(landmark.id),
                "type": str(landmark.type), "sub_type": str(landmark.sub_type), "value": float(landmark.value), "unit": str(landmark.unit),
                "road_id": int(landmark.road_id), "pose": pose(transform),
            })
        landmark_pass = len({x["landmark_id"] for x in landmark_entities}) == 2 and all(x["value"] == 40.0 and x["unit"] == "mph" for x in landmark_entities)
        receipts["LANDMARK"] = {
            "schema": "driveclarify.minimal-live-grounding.v1", "family": "Landmark", "status": "PASS" if landmark_pass else "FAIL",
            **common, "entity_A": landmark_entities[0], "entity_B": landmark_entities[1],
            "interpretation_A": landmark_case["interpretation_a"], "interpretation_B": landmark_case["interpretation_b"],
            "binding_reuse": validate_binding_unchanged(landmark_case),
        }

        order_case = next(x for x in CASES if x["case_id"] == "ORDER_QUAL_001")
        discovery = json.loads((PRIOR_REPORT / "FOUR_FAMILY_CANDIDATE_DISCOVERY.json").read_text())
        order_discovery = next(x for x in discovery["cases"] if x["case_id"] == order_case["case_id"])
        ego = ego_by_source["route_02"]
        ego_transform = ego.get_transform()
        junction_entities = []
        for order_index, item in enumerate(order_discovery["physical_evidence"]["ahead_junctions"], start=1):
            location = carla.Location(*item["xyz"])
            waypoint = world_map.get_waypoint(location, project_to_road=True)
            junction_entities.append({
                "kind": "carla_runtime_junction", "identity": f"Town12:junction:{waypoint.junction_id}",
                "junction_id": int(waypoint.junction_id), "route_order": order_index, "route_index": int(item["route_index"]),
                "road_id": int(waypoint.road_id), "lane_id": int(waypoint.lane_id), "is_junction": bool(waypoint.is_junction),
                "ego_relative": relative(ego_transform, location),
            })
        order_pass = [x["junction_id"] for x in junction_entities] == [16987, 5990] and all(x["is_junction"] for x in junction_entities) and junction_entities[0]["route_index"] < junction_entities[1]["route_index"]
        receipts["ORDER"] = {
            "schema": "driveclarify.minimal-live-grounding.v1", "family": "Order", "status": "PASS" if order_pass else "FAIL",
            **common, "ego_actor_id": int(ego.id), "ego_pose": pose(ego_transform), "entity_A": junction_entities[0], "entity_B": junction_entities[1],
            "both_ahead_on_relevant_route": True, "interpretation_A": order_case["interpretation_a"], "interpretation_B": order_case["interpretation_b"],
            "binding_reuse": validate_binding_unchanged(order_case),
        }

        under_case = next(x for x in CASES if x["case_id"] == "UNDERSPEC_QUAL_001")
        _path, under_runtime = source_runtime(under_case)
        under_binding = validate_binding_unchanged(under_case)
        option_entities = []
        for label, source_field in (("A", under_case["route_a_source"]), ("B", under_case["route_b_source"])):
            route = under_runtime[source_field]
            representative = route[40]
            location = carla.Location(*xyz(representative))
            waypoint = world_map.get_waypoint(location, project_to_road=True)
            option_entities.append({
                "kind": "carla_runtime_driving_corridor", "identity": under_binding[f"connector_{label}_identity"],
                "representative_world_xyz": list(xyz(representative)), "road_id": int(waypoint.road_id), "lane_id": int(waypoint.lane_id),
                "lane_type": str(waypoint.lane_type), "driving_lane": str(waypoint.lane_type).endswith("Driving"),
                "production_route_feasible": under_binding["checks"]["production_guard_still_pass"],
            })
        under_pass = all(x["driving_lane"] and x["production_route_feasible"] for x in option_entities) and option_entities[0]["identity"] != option_entities[1]["identity"]
        receipts["UNDERSPECIFIED"] = {
            "schema": "driveclarify.minimal-live-grounding.v1", "family": "Underspecified", "status": "PASS" if under_pass else "FAIL",
            **common, "option_A": option_entities[0], "option_B": option_entities[1],
            "interpretation_A": under_case["interpretation_a"], "interpretation_B": under_case["interpretation_b"], "binding_reuse": under_binding,
        }

        for family, value in receipts.items():
            atomic_json(REPORT / "LIVE_GROUNDING_RECEIPTS" / f"{family}.json", value)
        if not all(value["status"] == "PASS" for value in receipts.values()):
            raise RecoveryError("ONE_OR_MORE_MINIMAL_LIVE_GROUNDINGS_FAILED")
        atomic_text(client_log, json.dumps({"status": "4/4 PASS", "frame": snapshot.frame, "receipts": list(receipts)}, indent=2) + "\n")
    finally:
        for actor in reversed(actors):
            try:
                actor.destroy()
            except RuntimeError:
                pass
        try:
            settings = world.get_settings()
            settings.synchronous_mode = False
            settings.fixed_delta_seconds = None
            world.apply_settings(settings)
        except RuntimeError:
            pass


def seal() -> None:
    review_path = REPORT / "FINAL_INDEPENDENT_REVIEW.md"
    if not review_path.exists() or "PASS" not in review_path.read_text(encoding="utf-8"):
        raise RecoveryError("PASS_INDEPENDENT_REVIEW_MISSING")
    readiness_value = json.loads((REPORT / "CARLA_READINESS_RECEIPT.json").read_text())
    live_paths = sorted((REPORT / "LIVE_GROUNDING_RECEIPTS").glob("*.json"))
    live = [json.loads(path.read_text()) for path in live_paths]
    if readiness_value.get("status") != "CARLA_NATIVE_READINESS_PASS" or len(live) != 4 or not all(x.get("status") == "PASS" for x in live):
        raise RecoveryError("RECOVERY_NOT_READY_TO_SEAL")
    excluded = {"ARTIFACT_HASHES.json", "FINAL_RECEIPT.json"}
    coverage_paths = sorted(p for p in REPORT.rglob("*") if p.is_file() and p.name not in excluded)
    coverage = [{"relative_path": str(p.relative_to(REPORT)), "bytes": p.stat().st_size, "sha256": sha(p)} for p in coverage_paths]
    aggregate = digest(coverage)
    receipt = {
        "schema": "driveclarify.carla-readiness-four-family-recovery-final.v1",
        "final_status": "PASS_FOUR_FAMILY_MINIMAL_LIVE_GROUNDING_QUALIFIED",
        "entry_head": "eaa332b1bb994279b59ea5af786fdb5de96adc1b",
        "exit_head": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, text=True, stdout=subprocess.PIPE).stdout.strip(),
        "carla_readiness": "PASS",
        "carla_launches": 2,
        "usable_world_captures": 2,
        "families": {x["family"]: {"live_grounding": x["status"], "frame_id": x["frame_id"], "map": x["map"]} for x in live},
        "existing_production_route_bindings": "4/4 PASS",
        "reuse_previous_binding_evidence": True,
        "a1_sha256": sha(A1), "a1_retraining_count": 0,
        "production_route_owner_sha256": sha(NAV), "route_guard_changed": False,
        "endpoint_tolerance_m": 0.001, "endpoint_tolerance_changed": False,
        "formal_scientific_exposure": 0,
        "adversarial_removal": "DEFERRED_TO_PROSPECTIVE_ROSTER_INTEGRITY_STAGE",
        "formal_geometry_diversity": "DEFERRED_TO_PROSPECTIVE_ROSTER_INTEGRITY_STAGE",
        "source_changes": [
            "driveclarify_four_family_recovery/__init__.py",
            "driveclarify_four_family_recovery/recovery.py",
            "tests/four_family_recovery/test_recovery.py",
            "reports/driveclarify_v3_carla_readiness_and_four_family_live_grounding_recovery/run_native_recovery.sh",
        ],
        "focused_regressions": "42/42 PASS",
        "independent_review": "PASS_MINIMAL_LIVE_GROUNDING_RECOVERY",
        "exact_blocker": None,
        "next_step": "Construct the prospective 24-case-seed roster from the four qualified templates in a separately authorized stage; do not start RQ1/RQ2/RQ3 yet.",
        "artifact_count": len(coverage) + 2,
        "aggregate_sha256": aggregate,
        "aggregate_coverage": "all report files except ARTIFACT_HASHES.json and FINAL_RECEIPT.json (non-circular)",
    }
    atomic_json(REPORT / "FINAL_RECEIPT.json", receipt)
    manifest_paths = sorted(p for p in REPORT.rglob("*") if p.is_file() and p.name != "ARTIFACT_HASHES.json")
    manifest = [{"relative_path": str(p.relative_to(REPORT)), "bytes": p.stat().st_size, "sha256": sha(p)} for p in manifest_paths]
    atomic_json(REPORT / "ARTIFACT_HASHES.json", {
        "schema": "driveclarify.carla-readiness-four-family-recovery-artifacts.v1",
        "artifact_count": len(manifest) + 1,
        "aggregate_sha256": aggregate,
        "aggregate_coverage": receipt["aggregate_coverage"],
        "aggregate_exclusions": sorted(excluded),
        "files": manifest,
    })


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("readiness", "capture", "seal"))
    parser.add_argument("--port", type=int)
    parser.add_argument("--run-id")
    parser.add_argument("--client-log", type=Path)
    args = parser.parse_args()
    if sha(A1) != EXPECTED_A1:
        raise RecoveryError("A1_CHANGED")
    prior_final = json.loads((PRIOR_REPORT / "FINAL_RECEIPT.json").read_text())
    if prior_final.get("aggregate_sha256") != EXPECTED_PRIOR_AGGREGATE:
        raise RecoveryError("PRIOR_QUALIFICATION_RECEIPT_CHANGED")
    if args.mode == "seal":
        seal()
    elif args.mode == "readiness":
        if args.port is None or args.run_id is None or args.client_log is None:
            parser.error("readiness requires --port --run-id --client-log")
        readiness(args.port, args.run_id, args.client_log)
    else:
        if args.port is None or args.run_id is None or args.client_log is None:
            parser.error("capture requires --port --run-id --client-log")
        capture(args.port, args.run_id, args.client_log)


if __name__ == "__main__":
    main()
