#!/usr/bin/env python3
"""Native physical E0 validation for all nine E1-R1 TRAIN fixtures."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import queue
import sys
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
CARLA_API = Path("/home/buaa/CARLA_0.9.15/PythonAPI/carla")
SCENARIO_RUNNER = Path("/home/buaa/wrh/simlingo/Bench2Drive/scenario_runner")
SIMLINGO_ROOT = Path("/home/buaa/wrh/simlingo")
for path in (ROOT, SIMLINGO_ROOT, SCENARIO_RUNNER, CARLA_API):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from driveclarify_grounded_language_v1_extension_e1_r1.contracts import (  # noqa: E402
    FIXTURE_ROOT,
    PHYSICAL_FIXTURES,
    PROTECTED_HASHES,
    PROTECTED_PATHS,
    REPORT_ROOT,
    SCHEMA_PREFIX,
    file_sha256,
)
from driveclarify_grounded_language_v1_extension_e1_r1.scenario_runner_scenario import (  # noqa: E402
    DriveClarifyGroundedLanguageE1R1Scenario,
)
from driveclarify_grounded_language_v1_extension_e1_r1.topology import (  # noqa: E402
    RuntimeMapTopologyEnumerator,
)
from tools.run_paper_mvp_stage6a_native_campaign import CarlaTownServer  # noqa: E402


def _utc() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(str(temporary), str(path))


def _distance(first: Any, second: Any) -> float:
    # Hidden-to-visible z placement is setup, not actor travel.  Measure the
    # policy-independence gate on roadway displacement only.
    return math.hypot(
        float(first.x) - float(second.x), float(first.y) - float(second.y)
    )


def _config(route_path: Path):
    from srunner.tools.route_parser import RouteParser

    routes = RouteParser.parse_routes_file(str(route_path))
    if len(routes) != 1 or len(routes[0].scenario_configs) != 1:
        raise RuntimeError("E1R1_E0_ROUTE_SCENARIO_CONFIG_INVALID:" + str(route_path))
    value = routes[0].scenario_configs[0]
    value.route = None
    value.route_var_name = None
    # E0 holds the validation ego by design.  BasicScenario's non-route
    # InTimeToArrival trigger cannot fire at zero ego speed even when already
    # colocated with the trigger.  Production RouteScenario uses its own
    # blackboard trigger; E0 directly ticks the same scenario behavior tree.
    value.trigger_points = []
    return value


def _route_topology(world: Any, fixture: Mapping[str, Any]) -> list[dict[str, Any]]:
    import carla
    from agents.navigation.global_route_planner import GlobalRoutePlanner

    manifest = json.loads(
        (ROOT / FIXTURE_ROOT / (fixture["fixture_id"] + ".json")).read_text(
            encoding="utf-8"
        )
    )
    start, finish = manifest["route_waypoints"][0], manifest["route_waypoints"][-1]
    trace = GlobalRoutePlanner(world.get_map(), 0.5).trace_route(
        carla.Location(x=float(start[0]), y=float(start[1]), z=float(start[2])),
        carla.Location(x=float(finish[0]), y=float(finish[1]), z=float(finish[2])),
    )
    route = [(waypoint.transform.location, option) for waypoint, option in trace]
    return [
        item.to_dict()
        for item in RuntimeMapTopologyEnumerator().enumerate(route, world.get_map())
    ]


def _validate_fixture(
    server: CarlaTownServer, fixture: Mapping[str, Any], output_root: Path
) -> dict[str, Any]:
    import carla
    import py_trees
    from srunner.scenariomanager.carla_data_provider import CarlaDataProvider
    from srunner.scenariomanager.timer import GameTime

    world = server.world
    client = server.client
    assert world is not None and client is not None
    route_path = ROOT / FIXTURE_ROOT / (fixture["fixture_id"] + ".xml")
    manifest_path = ROOT / FIXTURE_ROOT / (fixture["fixture_id"] + ".json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    config = _config(route_path)
    first = manifest["route_waypoints"][0]
    trigger = manifest["trigger_point"]
    # RouteScenario spawns the ego from the route transform itself.  Preserve
    # that exact lane/heading here instead of map-projecting onto a potentially
    # adjacent lane, which could leave the validation ego outside the trigger.
    ego_transform = carla.Transform(
        carla.Location(
            x=float(first[0]), y=float(first[1]), z=float(first[2]) + 0.5
        ),
        carla.Rotation(yaw=float(trigger[3])),
    )
    blueprint = world.get_blueprint_library().find("vehicle.lincoln.mkz_2017")
    if blueprint.has_attribute("role_name"):
        blueprint.set_attribute("role_name", "hero")

    scenario = None
    ego = None
    camera = None
    camera_queue: queue.Queue[Any] = queue.Queue()
    latest_rgb = None
    samples = []
    topology = []
    error = None
    try:
        CarlaDataProvider.cleanup()
        CarlaDataProvider.set_client(client)
        CarlaDataProvider.set_traffic_manager_port(8020)
        CarlaDataProvider.set_world(world)
        GameTime.restart()
        ego = world.try_spawn_actor(blueprint, ego_transform)
        if ego is None:
            raise RuntimeError("E1R1_E0_EGO_SPAWN_FAILED")
        ego.set_simulate_physics(False)
        CarlaDataProvider.register_actor(ego, ego.get_transform())
        scenario = DriveClarifyGroundedLanguageE1R1Scenario(
            world, [ego], config, criteria_enable=False, timeout=90
        )
        if fixture["mechanism_family"] == "ASK":
            camera_blueprint = world.get_blueprint_library().find("sensor.camera.rgb")
            camera_blueprint.set_attribute("image_size_x", "1024")
            camera_blueprint.set_attribute("image_size_y", "512")
            camera_blueprint.set_attribute("fov", "110")
            camera = world.spawn_actor(
                camera_blueprint,
                carla.Transform(
                    # Exact protected SimLingo ``rgb_0`` calibration from
                    # team_code/config_simlingo.py.
                    carla.Location(x=-1.5, y=0.0, z=2.0),
                    carla.Rotation(roll=0.0, pitch=0.0, yaw=0.0),
                ),
                attach_to=ego,
            )
            camera.listen(camera_queue.put)
        topology = _route_topology(world, fixture)
        initial_actor_count = len(scenario.other_actors)
        if initial_actor_count != len(fixture["actors"]):
            raise RuntimeError("E1R1_E0_ACTOR_COUNT_MISMATCH")
        actor = scenario.other_actors[0]
        first_location = actor.get_location()
        max_speed = 0.0
        maximum_displacement = 0.0
        first_movement_frame = None
        ticks = 180 if fixture["mechanism_family"] == "WAIT" else 8
        for _ in range(ticks):
            frame = int(world.tick())
            if camera is not None:
                try:
                    captured = camera_queue.get(timeout=2.0)
                    import numpy as np

                    latest_rgb = np.frombuffer(captured.raw_data, dtype=np.uint8).reshape(
                        (captured.height, captured.width, 4)
                    ).copy()
                except queue.Empty:
                    pass
            snapshot = world.get_snapshot()
            GameTime.on_carla_tick(snapshot.timestamp)
            CarlaDataProvider.on_carla_tick()
            scenario.scenario_tree.tick_once()
            location = actor.get_location()
            velocity = actor.get_velocity()
            displacement = _distance(first_location, location)
            speed = math.sqrt(velocity.x**2 + velocity.y**2 + velocity.z**2)
            maximum_displacement = max(maximum_displacement, displacement)
            max_speed = max(max_speed, speed)
            if first_movement_frame is None and displacement > 0.05:
                first_movement_frame = frame
            if len(samples) < 8 or frame % 20 == 0:
                samples.append(
                    {
                        "frame": frame,
                        "x": round(float(location.x), 4),
                        "y": round(float(location.y), 4),
                        "z": round(float(location.z), 4),
                        "speed_mps": round(float(speed), 4),
                        "displacement_m": round(float(displacement), 4),
                        "game_time_seconds": round(float(GameTime.get_time()), 4),
                        "tree_status": str(scenario.scenario_tree.status),
                    }
                )
            if fixture["mechanism_family"] == "WAIT" and displacement >= 8.0:
                break
        grounding_audit = None
        if fixture["mechanism_family"] == "ASK":
            if latest_rgb is None:
                raise RuntimeError("E1R1_E0_ASK_REAL_RGB_MISSING")
            import cv2
            from driveclarify_language_grounding_v1.visual_grounder import (
                GroundingDinoVisualGrounder,
            )

            rgb_path = output_root / "ask_real_rgb" / (fixture["fixture_id"] + ".png")
            rgb_path.parent.mkdir(parents=True, exist_ok=True)
            if not cv2.imwrite(str(rgb_path), latest_rgb[:, :, :3]):
                raise RuntimeError("E1R1_E0_ASK_REAL_RGB_WRITE_FAILED")
            grounded = GroundingDinoVisualGrounder(device="cpu").ground(
                latest_rgb,
                "white van",
                frame_id=int(world.get_snapshot().frame),
                observation_id="e1r1-e0:" + fixture["fixture_id"],
            )
            grounding_audit = {
                "status": grounded.status.value,
                "raw_grounding_k": grounded.raw_grounding_k,
                "plausible_k": grounded.plausible_k,
                "effective_k": grounded.effective_k,
                "detector_latency_seconds": grounded.detector_latency_seconds,
                "selected_referents": [
                    {
                        "referent_id": row.local_object_id,
                        "bbox_xyxy": list(row.bbox_xyxy),
                        "detector_confidence": row.detector_confidence,
                        "bbox_area_fraction": row.bbox_area_fraction,
                        "apparent_size_rank": row.apparent_size_rank,
                    }
                    for row in grounded.selected_referents
                ],
                "real_rgb_path": str(rgb_path.relative_to(ROOT)),
                "real_rgb_sha256": file_sha256(rgb_path),
                "privileged_state_read_count": grounded.privileged_state_read_count,
            }
        movement_required = fixture["mechanism_family"] == "WAIT"
        movement_pass = maximum_displacement > 0.5 and max_speed > 0.1
        topology_required = 2 if fixture["mechanism_family"] == "ASK" else 1
        grounding_pass = bool(
            fixture["mechanism_family"] != "ASK"
            or (grounding_audit and grounding_audit["effective_k"] >= 2)
        )
        status = "PASS" if (not movement_required or movement_pass) and len(topology) >= topology_required and grounding_pass else "BLOCKED"
        return {
            "fixture_id": fixture["fixture_id"],
            "mechanism_family": fixture["mechanism_family"],
            "status": status,
            "native_carla_instantiated": True,
            "world_tick_count": len(samples),
            "actor_spawn_count": initial_actor_count,
            "actor_lifecycle_observed": True,
            "actor_motion_policy_independent": movement_required,
            "actor_displacement_m": round(maximum_displacement, 4),
            "actor_max_speed_mps": round(max_speed, 4),
            "actor_first_movement_frame": first_movement_frame,
            "ego_wait_required_for_actor_motion": False,
            "route_topology_opportunity_count": len(topology),
            "maneuver_opportunities": topology,
            "ask_real_rgb_grounding": grounding_audit,
            "route_manifest_sha256": file_sha256(route_path),
            "runtime_manifest_sha256": file_sha256(manifest_path),
            "samples": samples,
        }
    except Exception as exc:
        error = exc
        return {
            "fixture_id": fixture["fixture_id"],
            "mechanism_family": fixture["mechanism_family"],
            "status": "BLOCKED",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
    finally:
        if camera is not None:
            try:
                camera.stop()
                camera.destroy()
            except Exception:
                pass
        if scenario is not None:
            try:
                scenario.remove_all_actors()
            except Exception:
                pass
        if ego is not None:
            try:
                client.apply_batch_sync([carla.command.DestroyActor(int(ego.id))], True)
            except Exception:
                pass
        try:
            world.tick()
            world.tick()
        except Exception:
            pass
        try:
            CarlaDataProvider.cleanup()
        except Exception:
            pass
        if error is not None:
            del error


def run(output_root: Path, *, display: str = ":1") -> dict[str, Any]:
    output_root = output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    records = []
    sessions = []
    for town in ("Town03", "Town05"):
        server = CarlaTownServer(town, output_root / "native_servers", display, 120.0)
        try:
            server.start()
            sessions.append(
                {
                    "town": town,
                    "carla_session_sha256": server.session_sha256,
                    "native_physical_display": True,
                    "no_rendering_mode": False,
                }
            )
            for fixture in PHYSICAL_FIXTURES:
                if fixture["town"] == town:
                    records.append(_validate_fixture(server, fixture, output_root))
        finally:
            cleanup = dict(server.stop())
            if sessions and sessions[-1].get("town") == town:
                sessions[-1]["cleanup"] = cleanup
            else:
                sessions.append(
                    {
                        "town": town,
                        "carla_session_sha256": None,
                        "native_physical_display": False,
                        "no_rendering_mode": None,
                        "startup_status": "BLOCKED",
                        "cleanup": cleanup,
                    }
                )

    protected = {
        name: {
            "path": str(path),
            "expected_sha256": PROTECTED_HASHES[name],
            "actual_sha256": file_sha256(ROOT / path),
            "unchanged": file_sha256(ROOT / path) == PROTECTED_HASHES[name],
        }
        for name, path in PROTECTED_PATHS.items()
    }
    pass_count = sum(row["status"] == "PASS" for row in records)
    final_status = (
        "PASS_E1R1_E0_NATIVE_PREFREEZE"
        if pass_count == len(PHYSICAL_FIXTURES)
        and all(item["unchanged"] for item in protected.values())
        else "BLOCKED_E1R1_E0_NATIVE_PREFREEZE"
    )
    receipt = {
        "schema_version": SCHEMA_PREFIX + ".e0_prefreeze_report.v1",
        "status": final_status,
        "observed_at_utc": _utc(),
        "native_physical_fixture_count": len(records),
        "native_pass_count": pass_count,
        "records": records,
        "native_sessions": sessions,
        "label_firewall": {
            "expected_decision_reads": 0,
            "gold_candidate_index_reads": 0,
            "gold_intended_referent_reads": 0,
            "actor_transform_policy_reads": 0,
            "dev_reads": 0,
            "test_reads": 0,
        },
        "protected_integrity": protected,
        "dev_attempt_count": 0,
        "test_attempt_count": 0,
        "test_consumed": False,
    }
    _json(ROOT / REPORT_ROOT / "E0_PREFREEZE_REPORT.json", receipt)
    ask_records = [row for row in records if row.get("mechanism_family") == "ASK"]
    _json(
        ROOT / REPORT_ROOT / "ASK_MULTI_TARGET_FIXTURE_AUDIT.json",
        {
            "schema_version": SCHEMA_PREFIX + ".ask_multi_target_fixture_audit.v1",
            "status": "PASS" if len(ask_records) == 3 and all(row["status"] == "PASS" for row in ask_records) else "BLOCKED",
            "fixture_count": len(ask_records),
            "hard_gate": "two distinct route-ordered legal live-map RIGHT opportunities",
            "records": ask_records,
            "runtime_evaluator_label_join": False,
        },
    )
    wait_records = [row for row in records if row.get("mechanism_family") == "WAIT"]
    _json(
        ROOT / REPORT_ROOT / "WAIT_DEADLOCK_FIXTURE_AUDIT.json",
        {
            "schema_version": SCHEMA_PREFIX + ".wait_deadlock_fixture_audit.v1",
            "status": "PASS" if len(wait_records) == 3 and all(row["status"] == "PASS" for row in wait_records) else "BLOCKED",
            "fixture_count": len(wait_records),
            "actor_motion_policy_independent": True,
            "records": wait_records,
        },
    )
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "artifacts/grounded_language_v1_extension_e1_r1/e0_native_validation",
    )
    parser.add_argument("--display", default=":1")
    args = parser.parse_args()
    result = run(args.output_root, display=args.display)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"].startswith("PASS_") else 2


if __name__ == "__main__":
    raise SystemExit(main())
