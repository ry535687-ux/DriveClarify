"""Engineering-only CARLA scene executor; privileged truth never enters runtime."""

from __future__ import annotations

import json
import math
import os
import xml.etree.ElementTree as ET
from pathlib import Path

import carla
import py_trees

from srunner.scenariomanager.carla_data_provider import CarlaDataProvider
from srunner.scenarios.basic_scenario import BasicScenario

from .scene_bindings import frozen_binding
from .usc_admission import canonical_sha256, project_point_to_polyline


EXPECTED_TYPE = "DriveClarifyRQ2TE2V3EngineeringScenario"


def _atomic_json(path, value):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name("." + target.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, target)


def _transform(values):
    x, y, z, yaw = values
    return carla.Transform(carla.Location(x=x, y=y, z=z), carla.Rotation(yaw=yaw))


def _transform_values(value):
    return [
        float(value.location.x), float(value.location.y), float(value.location.z),
        float(value.rotation.yaw),
    ]


class _ProspectiveEventSequence(py_trees.behaviour.Behaviour):
    def __init__(self, scenario):
        super().__init__(name="E2 V3 prospective visibility and invalidation events")
        self.scenario = scenario
        self.start = None

    def initialise(self):
        snapshot = self.scenario._world.get_snapshot()
        self.scenario._activate(snapshot)
        self.start = float(self.scenario._activation_simulation_time_s)

    def update(self):
        snapshot = self.scenario._world.get_snapshot()
        now = float(snapshot.timestamp.elapsed_seconds)
        relative = now - float(self.start)
        for row in self.scenario._event_rows:
            if row.get("first_verified_effective_frame") is not None:
                continue
            if int(snapshot.frame) <= int(row["executed_frame"]):
                continue
            actor = self.scenario._owned_actors[int(row["actor_index"])]
            observed = _transform_values(actor.get_transform())
            authored = row["authored_transform_xyz_yaw"]
            position_ok = all(abs(left - right) <= 0.02 for left, right in zip(observed[:3], authored[:3]))
            yaw_delta = abs(((float(observed[3]) - float(authored[3]) + 180.0) % 360.0) - 180.0)
            if position_ok and yaw_delta <= 0.05:
                row["first_verified_effective_frame"] = int(snapshot.frame)
                row["first_verified_effective_relative_simulation_s"] = relative
                row["first_verified_observed_transform_xyz_yaw"] = observed
        for index, event in enumerate(self.scenario._binding.get("events", ())):
            if index in self.scenario._executed_event_indices:
                continue
            if relative < float(event["at_simulation_s"]):
                continue
            actor = self.scenario._owned_actors[int(event["actor_index"])]
            actor.set_transform(_transform(event["transform"]))
            observed = actor.get_transform()
            row = {
                "event_index": index,
                "event": str(event["event"]),
                "actor_index": int(event["actor_index"]),
                "actor_id": int(actor.id),
                "scheduled_simulation_s": float(event["at_simulation_s"]),
                "executed_relative_simulation_s": relative,
                "executed_frame": int(snapshot.frame),
                "authored_transform_xyz_yaw": list(event["transform"]),
                "observed_transform_xyz_yaw": _transform_values(observed),
                "first_verified_effective_frame": None,
                "first_verified_effective_relative_simulation_s": None,
                "first_verified_observed_transform_xyz_yaw": None,
            }
            self.scenario._event_rows.append(row)
            self.scenario._executed_event_indices.add(index)
            self.scenario._write_receipt("EVENT_EXECUTED")
        if relative >= float(self.scenario._engineering_bound_s):
            self.scenario._engineering_bound_reached = True
            self.scenario._engineering_bound_frame = int(snapshot.frame)
            self.scenario._write_receipt("ENGINEERING_BOUND_REACHED")
            return py_trees.common.Status.FAILURE
        return py_trees.common.Status.RUNNING


class DriveClarifyRQ2TE2V3EngineeringScenario(BasicScenario):
    def __init__(self, world, ego_vehicles, config, randomize=False, debug_mode=False, criteria_enable=True, timeout=10000):
        del randomize
        parameters = getattr(config, "other_parameters", {}).get("rq2_t_e2_v3")
        if not isinstance(parameters, dict):
            raise RuntimeError("E2_V3_ENGINEERING_SCENARIO_BINDING_MISSING")
        scene_config_id = str(parameters.get("scene_config_id"))
        binding = dict(frozen_binding(scene_config_id))
        if parameters.get("scene_configuration_sha256") != binding["scene_configuration_sha256"]:
            raise RuntimeError("E2_V3_ENGINEERING_SCENARIO_HASH_MISMATCH")
        actual_town = world.get_map().name.split("/")[-1]
        expected_town = str(binding["town"])
        if not (actual_town == expected_town or (expected_town == "Town10HD" and actual_town == "Town10HD_Opt")):
            raise RuntimeError("E2_V3_ENGINEERING_SCENARIO_MAP_MISMATCH")
        self.timeout = timeout
        self._world = world
        self._binding = binding
        self._scene_config_id = scene_config_id
        self._owned_actors = []
        self._spawn_rows = []
        self._event_rows = []
        self._executed_event_indices = set()
        self._cleanup_complete = False
        self._activation_only = os.environ.get("DRIVECLARIFY_E2_V3_USC_ACTIVATION_ONLY") == "1"
        self._engineering_bound_s = (
            0.25 if self._activation_only else float(binding.get("engineering_bound_s", 8.0))
        )
        self._engineering_bound_reached = False
        self._engineering_bound_frame = None
        self._activation_status = "CONSTRUCTED_NOT_ACTIVATED"
        self._activation_frame = None
        self._activation_simulation_time_s = None
        self._activation_timer_start_count = 0
        self._scenario_instance_id = "{}:{}:{}".format(EXPECTED_TYPE, scene_config_id, id(self))
        self._initialization_error = None
        self._receipt_path = os.environ.get("DRIVECLARIFY_E2_V3_SCENARIO_RECEIPT")
        self._activation_receipt_path = os.environ.get("DRIVECLARIFY_E2_V3_USC_ACTIVATION_RECEIPT")
        self._admission_path = os.environ.get("DRIVECLARIFY_E2_V3_USC_ADMISSION_RECEIPT")
        super().__init__(EXPECTED_TYPE, ego_vehicles, config, world, debug_mode, criteria_enable=criteria_enable)
        self._verify_spawned_actors()
        self._write_receipt("INITIALIZED")

    def _initialize_actors(self, config):
        del config
        library = self._world.get_blueprint_library()
        try:
            for index, spec in enumerate(self._binding["actors"]):
                blueprint = library.find(spec["blueprint"])
                if spec.get("color") and blueprint.has_attribute("color"):
                    blueprint.set_attribute("color", spec["color"])
                role = "e2v3_{}_{}".format(self._scene_config_id.lower().replace("-", "_"), index)
                exact = _transform(spec["initial"])
                vehicle = str(spec["blueprint"]).startswith("vehicle.")
                spawn = exact
                spawn_mode = "EXACT_POSE_DIRECT"
                if vehicle:
                    elevated = list(spec["initial"])
                    elevated[2] = float(elevated[2]) + 8.0
                    spawn = _transform(elevated)
                    spawn_mode = "ELEVATED_NATIVE_SPAWN_PHYSICS_OFF_EXACT_TELEPORT"
                actor = CarlaDataProvider.request_new_actor(
                    spec["blueprint"], spawn, rolename=role,
                    autopilot=False, random_location=False, color=spec.get("color"), tick=True,
                )
                if actor is None:
                    raise RuntimeError("E2_V3_ENGINEERING_ACTOR_SPAWN_FAILED")
                if hasattr(actor, "set_simulate_physics"):
                    actor.set_simulate_physics(False)
                actor.set_transform(exact)
                self._owned_actors.append(actor)
                self.other_actors.append(actor)
                self._spawn_rows.append({
                    "actor_index": index, "actor_id": int(actor.id),
                    "type_id": str(actor.type_id), "role_name": role,
                    "candidate_id_posthoc_only": spec.get("candidate_id"),
                    "certificate_role": spec.get("certificate_role"),
                    "initial_authored_xyz_yaw": list(spec["initial"]),
                    "initial_observed_xyz_yaw": None, "spawn_mode": spawn_mode,
                })
        except Exception as error:
            self._initialization_error = {"type": type(error).__name__, "message": str(error)}
            self._cleanup_owned_actors()
            self._write_receipt("INITIALIZATION_FAILED")
            raise

    def _verify_spawned_actors(self):
        try:
            for actor, spec, row in zip(self._owned_actors, self._binding["actors"], self._spawn_rows):
                observed_values = _transform_values(actor.get_transform())
                row["initial_observed_xyz_yaw"] = observed_values
                if str(actor.type_id) != str(spec["blueprint"]) or any(
                    abs(left - right) > 0.02
                    for left, right in zip(observed_values[:3], spec["initial"][:3])
                ):
                    raise RuntimeError("E2_V3_ENGINEERING_ACTOR_VERIFICATION_FAILED:" + json.dumps(row, sort_keys=True))
        except Exception as error:
            self._initialization_error = {"type": type(error).__name__, "message": str(error)}
            self._cleanup_owned_actors()
            self._write_receipt("INITIALIZATION_FAILED")
            raise

    def _create_behavior(self):
        return _ProspectiveEventSequence(self)

    def _create_test_criteria(self):
        return []

    def _activate(self, snapshot):
        if self._activation_status == "ACTIVATED_AND_BOUND":
            return
        if self._activation_timer_start_count != 0:
            raise RuntimeError("E2_V3_USC_ACTIVATION_TIMER_ALREADY_STARTED")
        self._activation_status = "ACTIVATED_AND_BOUND"
        self._activation_frame = int(snapshot.frame)
        self._activation_simulation_time_s = float(snapshot.timestamp.elapsed_seconds)
        self._activation_timer_start_count = 1
        # The supplemental activation receipt is a development-USC admission
        # instrument.  It must not alter or inspect the pre-authored blind USC
        # scene, whose execution remains governed by the original native
        # scenario contract.
        if self._scene_config_id == "DEV-V3-USC-CONTROL-A":
            self._write_activation_receipt()
        self._write_receipt("ACTIVATED_AND_BOUND")

    def _write_activation_receipt(self):
        if not self._activation_receipt_path:
            raise RuntimeError("E2_V3_USC_ACTIVATION_RECEIPT_PATH_MISSING")
        if not self._admission_path or not Path(self._admission_path).is_file():
            raise RuntimeError("E2_V3_USC_ADMISSION_RECEIPT_MISSING_AT_ACTIVATION")
        admission = json.loads(Path(self._admission_path).read_text(encoding="utf-8"))
        if admission.get("status") != "PASS_STATIC_ROUTE_TRIGGER_ADMISSION":
            raise RuntimeError("E2_V3_USC_ACTIVATION_WITHOUT_STATIC_ADMISSION")
        ego = self.ego_vehicles[0]
        transform = ego.get_transform()
        ego_xyz = [float(transform.location.x), float(transform.location.y), float(transform.location.z)]
        trigger = [float(value) for value in admission["trigger_transform_xyz_yaw"][:3]]
        waypoint = self._world.get_map().get_waypoint(transform.location, project_to_road=True)
        route = ET.parse(admission["route_path"]).getroot().find("route")
        points = [
            [float(row.attrib[name]) for name in ("x", "y", "z")]
            for row in route.findall("./waypoints/position")
        ]
        progress = dict(project_point_to_polyline(ego_xyz, points))
        receipt = {
            "schema_version": "driveclarify.e2_v3.usc_activation_receipt.v1",
            "engineering_identity": os.environ.get("DRIVECLARIFY_E2_V3_EPISODE_ID"),
            "scene_id": self._scene_config_id,
            "route_id": admission["route_id"],
            "route_digest": admission["route_sha256"],
            "scenario_config_digest": self._binding["scene_configuration_sha256"],
            "scenario_class": EXPECTED_TYPE,
            "scenario_instance_id": self._scenario_instance_id,
            "simulator_frame": self._activation_frame,
            "simulator_time": self._activation_simulation_time_s,
            "ego_transform_xyz_yaw": _transform_values(transform),
            "ego_road_lane": {
                "road_id": int(waypoint.road_id), "lane_id": int(waypoint.lane_id),
                "is_junction": bool(waypoint.is_junction), "waypoint_s": float(waypoint.s),
            },
            "route_progress": progress,
            "trigger_transform_xyz_yaw": admission["trigger_transform_xyz_yaw"],
            "trigger_distance": math.dist(ego_xyz, trigger),
            "owned_actor_count": len(self._owned_actors),
            "expected_semantic_actor_count": int(self._binding.get("expected_semantic_actor_count", len(self._binding["actors"]))),
            "scenario_activation_status": "ACTIVATED_AND_BOUND",
            "activation_timer_start_count": self._activation_timer_start_count,
            "scenario_relative_horizon_s": self._engineering_bound_s,
            "activation_only": self._activation_only,
            "formal_scientific_exposure": False,
        }
        receipt["activation_receipt_digest"] = canonical_sha256(receipt)
        _atomic_json(self._activation_receipt_path, receipt)

    def _write_receipt(self, status):
        if not self._receipt_path:
            return
        expected_events = len(self._binding.get("events", ()))
        _atomic_json(self._receipt_path, {
            "schema_version": "driveclarify.e2_v3.engineering_scenario_receipt.v1",
            "status": status, "scene_config_id": self._scene_config_id,
            "scene_configuration_sha256": self._binding["scene_configuration_sha256"],
            "spawn_rows": list(self._spawn_rows), "expected_spawn_count": len(self._binding["actors"]),
            "event_rows": list(self._event_rows), "expected_event_count": expected_events,
            "all_events_executed": len(self._event_rows) == expected_events,
            "all_event_transforms_verified": bool(
                len(self._event_rows) == expected_events
                and all(row.get("first_verified_effective_frame") is not None for row in self._event_rows)
            ),
            "engineering_bound_s": self._engineering_bound_s,
            "engineering_bound_reached": self._engineering_bound_reached,
            "engineering_bound_frame": self._engineering_bound_frame,
            "scenario_activation_status": self._activation_status,
            "scenario_instance_id": self._scenario_instance_id,
            "activation_frame": self._activation_frame,
            "activation_simulation_time_s": self._activation_simulation_time_s,
            "activation_timer_start_count": self._activation_timer_start_count,
            "scenario_elapsed_simulation_time_s": (
                None if self._activation_simulation_time_s is None
                else max(0.0, float(self._world.get_snapshot().timestamp.elapsed_seconds) - float(self._activation_simulation_time_s))
            ),
            "activation_only": self._activation_only,
            "runtime_candidate_payload_excludes_actor_ids": True,
            "initialization_error": self._initialization_error,
            "cleanup_complete": self._cleanup_complete,
        })

    def _cleanup_owned_actors(self):
        survivors = []
        for actor in reversed(getattr(self, "_owned_actors", [])):
            try:
                if actor.is_alive:
                    if CarlaDataProvider.actor_id_exists(actor.id):
                        CarlaDataProvider.remove_actor_by_id(actor.id)
                    elif actor.destroy() is False:
                        survivors.append(actor)
            except Exception:
                survivors.append(actor)
        self._owned_actors = list(reversed(survivors))
        live_ids = {id(actor) for actor in self._owned_actors}
        self.other_actors = [actor for actor in getattr(self, "other_actors", []) if id(actor) in live_ids]
        self._cleanup_complete = not self._owned_actors

    def remove_all_actors(self):
        self._cleanup_owned_actors()
        self._write_receipt("CLEANED" if self._cleanup_complete else "CLEANUP_FAILED")

    def __del__(self):
        self._cleanup_owned_actors()


__all__ = ["DriveClarifyRQ2TE2V3EngineeringScenario"]
