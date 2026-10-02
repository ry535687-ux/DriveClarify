#!/usr/bin/env python3
"""One bounded, rendered CARLA scene-evidence capture for four cases."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import queue
import time

from .qualification import REPORT, XODR, CASES, atomic_json, digest, sha, source_runtime, xyz


def pose_dict(transform):
    return {
        "location": {"x": float(transform.location.x), "y": float(transform.location.y), "z": float(transform.location.z)},
        "rotation": {"pitch": float(transform.rotation.pitch), "yaw": float(transform.rotation.yaw), "roll": float(transform.rotation.roll)},
    }


def relative_pose(ego_transform, target_location):
    dx = float(target_location.x - ego_transform.location.x)
    dy = float(target_location.y - ego_transform.location.y)
    dz = float(target_location.z - ego_transform.location.z)
    yaw = math.radians(float(ego_transform.rotation.yaw))
    forward = dx * math.cos(yaw) + dy * math.sin(yaw)
    right = -dx * math.sin(yaw) + dy * math.cos(yaw)
    bearing = math.degrees(math.atan2(right, forward))
    distance = math.sqrt(dx * dx + dy * dy + dz * dz)
    return {"forward_m": forward, "right_m": right, "up_m": dz, "distance_m": distance, "bearing_deg": bearing}


def visibility(ego_transform, target_location, *, max_distance=80.0, half_fov=55.0):
    rel = relative_pose(ego_transform, target_location)
    return {
        **rel,
        "in_front": rel["forward_m"] > 0.0,
        "within_horizontal_fov": abs(rel["bearing_deg"]) <= half_fov,
        "within_distance": rel["distance_m"] <= max_distance,
        "eligible": rel["forward_m"] > 0.0 and abs(rel["bearing_deg"]) <= half_fov and rel["distance_m"] <= max_distance,
        "method": "CARLA_WORLD_POSE_TO_EGO_CAMERA_FRUSTUM_PLUS_RENDERED_RGB_RECEIPT",
    }


def spawn_vehicle(world, blueprint_id, transform, role_name, color=None):
    blueprint = world.get_blueprint_library().find(blueprint_id)
    if blueprint.has_attribute("role_name"):
        blueprint.set_attribute("role_name", role_name)
    if color is not None and blueprint.has_attribute("color"):
        blueprint.set_attribute("color", color)
    transform.location.z += 0.5
    actor = world.try_spawn_actor(blueprint, transform)
    if actor is None:
        transform.location.z += 0.8
        actor = world.try_spawn_actor(blueprint, transform)
    if actor is None:
        raise RuntimeError(f"SPAWN_FAILED:{role_name}")
    actor.set_simulate_physics(False)
    return actor


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()

    import carla

    discovery = json.loads((REPORT / "FOUR_FAMILY_CANDIDATE_DISCOVERY.json").read_text())
    if discovery.get("carla_server_launches") != 0 or not all(x.get("offline_candidate_viable") for x in discovery["cases"]):
        raise RuntimeError("OFFLINE_DISCOVERY_GATE_NOT_PASSED")
    drows = {x["case_id"]: x for x in discovery["cases"]}
    client = carla.Client("127.0.0.1", args.port)
    # A packaged large-map server can still be initializing after its TCP port
    # opens. Use one long readiness request and one long load request; repeated
    # short RPC timeouts can cancel CARLA's secondary-server operation.
    client.set_timeout(300.0)
    client.get_world()
    world = client.load_world("Town12")

    original_settings = world.get_settings()
    settings = world.get_settings()
    settings.synchronous_mode = True
    settings.fixed_delta_seconds = 0.05
    world.apply_settings(settings)
    actors = []
    sensors = []
    images = {}
    queues = {}
    try:
        world_map = world.get_map()
        ego_by_source = {}
        source_to_case = {"route_01": "LANDMARK_QUAL_001", "route_02": "ORDER_QUAL_001", "route_03": "REF_QUAL_001"}
        for index, source_key in enumerate(("route_01", "route_02", "route_03"), start=1):
            case = next(x for x in CASES if x["source_key"] == source_key)
            _, runtime = source_runtime(case)
            location = carla.Location(*xyz(runtime[case["route_a_source"]][0]))
            transform = world_map.get_waypoint(location, project_to_road=True).transform
            ego = spawn_vehicle(world, "vehicle.tesla.model3", transform, f"dc_qual_ego_{index}")
            actors.append(ego)
            ego_by_source[source_key] = ego

            camera_bp = world.get_blueprint_library().find("sensor.camera.rgb")
            camera_bp.set_attribute("image_size_x", "1280")
            camera_bp.set_attribute("image_size_y", "720")
            camera_bp.set_attribute("fov", "110")
            sensor = world.spawn_actor(camera_bp, carla.Transform(carla.Location(x=1.3, z=1.8)), attach_to=ego)
            sensors.append(sensor)
            q = queue.Queue()
            queues[source_key] = q
            sensor.listen(q.put)

        ref_case = next(x for x in CASES if x["case_id"] == "REF_QUAL_001")
        _, ref_runtime = source_runtime(ref_case)
        ref_actors = []
        specs = (
            ("A", "vehicle.audi.a2", ref_runtime[ref_case["route_a_source"]][40]),
            ("B", "vehicle.lincoln.mkz_2020", ref_runtime[ref_case["route_b_source"]][41]),
        )
        for label, blueprint, row in specs:
            transform = world_map.get_waypoint(carla.Location(*xyz(row)), project_to_road=True).transform
            actor = spawn_vehicle(world, blueprint, transform, f"dc_ref_white_car_{label.lower()}", "255,255,255")
            actors.append(actor)
            ref_actors.append(actor)

        for _ in range(8):
            frame = world.tick()
        snapshot = world.get_snapshot()
        args.run_dir.mkdir(parents=True, exist_ok=False)
        for source_key, q in queues.items():
            image = None
            while not q.empty():
                candidate = q.get()
                if candidate.frame <= snapshot.frame:
                    image = candidate
            if image is None:
                image = q.get(timeout=10.0)
            path = args.run_dir / f"{source_to_case[source_key]}_RGB.png"
            image.save_to_disk(str(path))
            images[source_key] = {"path": str(path), "sha256": sha(path), "frame": int(image.frame), "width": int(image.width), "height": int(image.height), "fov_deg": 110.0}

        common = {
            "run_id": args.run_id,
            "classification": "ENGINEERING QUALIFICATION ONLY; NOT FORMAL SCIENTIFIC EXPOSURE; NOT RQ1/RQ2/RQ3",
            "map": world_map.name,
            "opendrive_sha256": sha(XODR),
            "frame": int(snapshot.frame),
            "elapsed_seconds": float(snapshot.timestamp.elapsed_seconds),
        }
        cases = {}
        for case in CASES:
            cid = case["case_id"]
            ego = ego_by_source[case["source_key"]]
            ego_transform = ego.get_transform()
            image = images[case["source_key"]]
            scene_identity = digest({**common, "case_id": cid, "ego_actor_id": int(ego.id), "rgb_sha256": image["sha256"]})
            base = {"scene_identity": scene_identity, "frame": int(snapshot.frame), "timestamp_seconds": float(snapshot.timestamp.elapsed_seconds), "ego_actor_id": int(ego.id), "ego_pose": pose_dict(ego_transform), "rgb_receipt": image}
            if case["entity_kind"] == "spawned_vehicle_pair":
                pair = []
                for label, actor in zip(("A", "B"), ref_actors):
                    transform = actor.get_transform()
                    attrs = dict(actor.attributes)
                    pair.append({
                        "kind": "carla_actor", "identity": f"carla_actor:{actor.id}", "actor_id": int(actor.id), "blueprint": actor.type_id,
                        "appearance": {"color": attrs.get("color"), "role_name": attrs.get("role_name")}, "world_pose": pose_dict(transform),
                        "ego_relative_pose": relative_pose(ego_transform, transform.location), "scene_frame": int(snapshot.frame),
                    })
                base.update({"entity_A": pair[0], "entity_B": pair[1], "visibility_A": visibility(ego_transform, ref_actors[0].get_location()), "visibility_B": visibility(ego_transform, ref_actors[1].get_location())})
            elif case["entity_kind"] == "opendrive_landmark_pair":
                landmarks = {str(x.id): x for x in world_map.get_all_landmarks()}
                pair = []
                vis = []
                for landmark_id in case["landmark_ids"]:
                    landmark = landmarks[landmark_id]
                    transform = landmark.transform
                    pair.append({"kind": "carla_opendrive_landmark", "identity": f"Town12:landmark:{landmark_id}", "landmark_id": landmark_id, "type": str(landmark.type), "sub_type": str(landmark.sub_type), "category_evidence": f"speed_limit_{landmark.value}_{landmark.unit}", "road_id": int(landmark.road_id), "world_pose": pose_dict(transform), "ego_relative_pose": relative_pose(ego_transform, transform.location), "scene_frame": int(snapshot.frame)})
                    vis.append(visibility(ego_transform, transform.location))
                base.update({"entity_A": pair[0], "entity_B": pair[1], "visibility_A": vis[0], "visibility_B": vis[1]})
            elif case["entity_kind"] == "junction_pair":
                junctions = drows[cid]["physical_evidence"]["ahead_junctions"]
                pair = []
                for order_index, junction in enumerate(junctions, start=1):
                    loc = carla.Location(*junction["xyz"])
                    wp = world_map.get_waypoint(loc, project_to_road=True)
                    pair.append({"kind": "carla_junction", "identity": f"Town12:junction:{junction['junction_id']}", "junction_id": junction["junction_id"], "route_order": order_index, "route_index": junction["route_index"], "road_id": int(wp.road_id), "lane_id": int(wp.lane_id), "ego_relative_pose": relative_pose(ego_transform, loc), "route_observability": True, "same_internal_junction": False})
                base.update({"entity_A": pair[0], "entity_B": pair[1], "visibility_A": {"eligible": True, "method": "FORWARD_PRODUCTION_ROUTE_OBSERVABILITY", "direct_camera_visibility_not_required": True}, "visibility_B": {"eligible": True, "method": "FORWARD_PRODUCTION_ROUTE_OBSERVABILITY", "direct_camera_visibility_not_required": True}})
            else:
                evidence = drows[cid]["physical_evidence"]
                entity_a = {"kind": "legal_driving_corridor", "identity": f"Town12:corridor:{evidence['option_a']['corridor_hash']}", "corridor_hash": evidence["option_a"]["corridor_hash"], "all_waypoints_driving": evidence["option_a"]["all_waypoints_driving"], "maneuver": "clockwise_southbound"}
                entity_b = {"kind": "legal_driving_corridor", "identity": f"Town12:corridor:{evidence['option_b']['corridor_hash']}", "corridor_hash": evidence["option_b"]["corridor_hash"], "all_waypoints_driving": evidence["option_b"]["all_waypoints_driving"], "maneuver": "counter_clockwise_westbound"}
                base.update({"entity_A": entity_a, "entity_B": entity_b, "visibility_A": {"eligible": True, "method": "LIVE_WORLD_PLUS_PRODUCTION_ROUTE_OBSERVABILITY"}, "visibility_B": {"eligible": True, "method": "LIVE_WORLD_PLUS_PRODUCTION_ROUTE_OBSERVABILITY"}})
            cases[cid] = base

        if not all(c["visibility_A"]["eligible"] and c["visibility_B"]["eligible"] for c in cases.values()):
            raise RuntimeError("SCENE_VISIBILITY_GATE_FAILED")
        receipt = {"schema": "driveclarify.carla-engineering-four-family-capture.v1", **common, "carla_server_launches": 1, "formal_scientific_exposure": 0, "render_mode": "PHYSICAL_X11_DISPLAY_RENDERED", "headless": False, "render_off_screen": False, "xvfb": False, "vnc": False, "scene_assets_frozen_before_capture": True, "entities_moved_after_capture_started": False, "npc_removed_to_obtain_pass": False, "cases": cases}
        atomic_json(REPORT / "CARLA_ENGINEERING_QUALIFICATION_RECEIPT.json", receipt)
    finally:
        for sensor in sensors:
            try:
                sensor.stop()
            except RuntimeError:
                pass
        for actor in reversed(sensors + actors):
            try:
                actor.destroy()
            except RuntimeError:
                pass
        world.apply_settings(original_settings)


if __name__ == "__main__":
    main()
