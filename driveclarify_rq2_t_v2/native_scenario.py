"""Engineering-only native reveal scenario; gold stays in the scenario process."""

from __future__ import annotations

import json
import os
from pathlib import Path

import carla
import py_trees

from srunner.scenariomanager.carla_data_provider import CarlaDataProvider
from srunner.scenarios.basic_scenario import BasicScenario

from .scene_bindings import frozen_binding


EXPECTED_TYPE = "DriveClarifyRQ2TV2EngineeringScenario"


def _atomic_json(path, value):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name("." + target.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, target)


def _transform(values):
    x, y, z, yaw = values
    return carla.Transform(carla.Location(x=x, y=y, z=z), carla.Rotation(yaw=yaw))


class _ProspectiveReveal(py_trees.behaviour.Behaviour):
    def __init__(self, scenario):
        super().__init__(name="RQ2-T V2 prospectively authored physical reveal")
        self.scenario = scenario
        self.start = None

    def initialise(self):
        snapshot = self.scenario._world.get_snapshot()
        self.start = float(snapshot.timestamp.elapsed_seconds)

    def update(self):
        now = float(self.scenario._world.get_snapshot().timestamp.elapsed_seconds)
        delay = self.scenario._binding.get("reveal_after_simulation_s")
        if delay is not None and not self.scenario._reveal_executed:
            if now - float(self.start) >= float(delay):
                for actor, spec in zip(self.scenario._owned_actors, self.scenario._binding["actors"]):
                    if spec.get("revealed") is not None:
                        actor.set_transform(_transform(spec["revealed"]))
                self.scenario._reveal_executed = True
                self.scenario._reveal_frame = int(self.scenario._world.get_snapshot().frame)
                self.scenario._write_receipt("REVEAL_EXECUTED")
        # This is an engineering-only simulator-domain containment boundary.
        # Returning FAILURE terminates the RouteScenario without supplying any
        # policy/control/evidence value and without using wall time as a scene
        # outcome.  It keeps the bounded witness suite practical and gives the
        # normal ScenarioRunner cleanup path a deterministic endpoint.
        if now - float(self.start) >= float(self.scenario._engineering_bound_s):
            self.scenario._engineering_bound_reached = True
            self.scenario._engineering_bound_frame = int(
                self.scenario._world.get_snapshot().frame
            )
            self.scenario._write_receipt("ENGINEERING_BOUND_REACHED")
            return py_trees.common.Status.FAILURE
        return py_trees.common.Status.RUNNING


class DriveClarifyRQ2TV2EngineeringScenario(BasicScenario):
    def __init__(self, world, ego_vehicles, config, randomize=False, debug_mode=False, criteria_enable=True, timeout=10000):
        del randomize
        parameters = getattr(config, "other_parameters", {}).get("rq2_t_v2")
        if not isinstance(parameters, dict):
            raise RuntimeError("RQ2_T_V2_ENGINEERING_SCENARIO_BINDING_MISSING")
        scene_config_id = parameters.get("scene_config_id")
        binding = dict(frozen_binding(str(scene_config_id)))
        if parameters.get("scene_configuration_sha256") != binding["scene_configuration_sha256"]:
            raise RuntimeError("RQ2_T_V2_ENGINEERING_SCENARIO_HASH_MISMATCH")
        actual_town = world.get_map().name.split("/")[-1]
        expected_town = str(binding["town"])
        if not (actual_town == expected_town or (expected_town == "Town10HD" and actual_town == "Town10HD_Opt")):
            raise RuntimeError("RQ2_T_V2_ENGINEERING_SCENARIO_MAP_MISMATCH")
        self.timeout = timeout
        self._world = world
        self._binding = binding
        self._scene_config_id = str(scene_config_id)
        self._owned_actors = []
        self._spawn_rows = []
        self._cleanup_complete = False
        self._reveal_executed = False
        self._reveal_frame = None
        self._engineering_bound_s = 10.0
        self._engineering_bound_reached = False
        self._engineering_bound_frame = None
        self._initialization_error = None
        self._receipt_path = os.environ.get("DRIVECLARIFY_RQ2_T_V2_SCENARIO_RECEIPT")
        super().__init__(EXPECTED_TYPE, ego_vehicles, config, world, debug_mode, criteria_enable=criteria_enable)
        self._verify_spawned_actors()
        self._write_receipt("INITIALIZED")

    def _initialize_actors(self, config):
        del config
        library = self._world.get_blueprint_library()
        try:
            for index, spec in enumerate(self._binding["actors"], 1):
                blueprint = library.find(spec["blueprint"])
                if spec.get("color") and blueprint.has_attribute("color"):
                    blueprint.set_attribute("color", spec["color"])
                role = "rq2tv2_{}_{}".format(self._scene_config_id.lower().replace("-", "_"), index)
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
                if actor is None and not vehicle:
                    elevated = list(spec["initial"])
                    elevated[2] = float(elevated[2]) + 8.0
                    actor = CarlaDataProvider.request_new_actor(
                        spec["blueprint"], _transform(elevated), rolename=role,
                        autopilot=False, random_location=False, color=spec.get("color"), tick=True,
                    )
                    spawn_mode = "ELEVATED_NATIVE_SPAWN_EXACT_TELEPORT"
                if actor is None:
                    raise RuntimeError("RQ2_T_V2_ENGINEERING_ACTOR_SPAWN_FAILED")
                if hasattr(actor, "set_simulate_physics"):
                    actor.set_simulate_physics(False)
                actor.set_transform(exact)
                self._owned_actors.append(actor)
                self.other_actors.append(actor)
                self._spawn_rows.append({
                    "actor_id": int(actor.id), "type_id": str(actor.type_id),
                    "initial": list(spec["initial"]), "observed": None,
                    "spawn_mode": spawn_mode,
                })
        except Exception as error:
            self._initialization_error = {
                "type": type(error).__name__, "message": str(error)
            }
            self._cleanup_owned_actors()
            self._write_receipt("INITIALIZATION_FAILED")
            raise

    def _verify_spawned_actors(self):
        try:
            for actor, spec, row in zip(
                self._owned_actors, self._binding["actors"], self._spawn_rows
            ):
                observed = actor.get_transform()
                observed_values = [
                    float(observed.location.x), float(observed.location.y),
                    float(observed.location.z), float(observed.rotation.yaw),
                ]
                row["observed"] = observed_values
                if str(actor.type_id) != str(spec["blueprint"]) or any(
                    abs(left - right) > 0.02
                    for left, right in zip(observed_values[:3], spec["initial"][:3])
                ):
                    details = {
                        "actor_type_id": str(actor.type_id),
                        "expected_type_id": str(spec["blueprint"]),
                        "expected_xyz_yaw": list(spec["initial"]),
                        "observed_xyz_yaw": observed_values,
                    }
                    raise RuntimeError(
                        "RQ2_T_V2_ENGINEERING_ACTOR_VERIFICATION_FAILED:"
                        + json.dumps(details, sort_keys=True)
                    )
        except Exception as error:
            self._initialization_error = {
                "type": type(error).__name__, "message": str(error)
            }
            self._cleanup_owned_actors()
            self._write_receipt("INITIALIZATION_FAILED")
            raise

    def _create_behavior(self):
        return _ProspectiveReveal(self)

    def _create_test_criteria(self):
        return []

    def _write_receipt(self, status):
        if self._receipt_path:
            _atomic_json(self._receipt_path, {
                "schema_version": "driveclarify.rq2_t_v2.engineering_scenario_receipt.v1",
                "status": status, "scene_config_id": self._scene_config_id,
                "scene_configuration_sha256": self._binding["scene_configuration_sha256"],
                "spawn_rows": list(self._spawn_rows), "expected_spawn_count": len(self._binding["actors"]),
                "reveal_executed": self._reveal_executed, "reveal_frame": self._reveal_frame,
                "engineering_bound_s": self._engineering_bound_s,
                "engineering_bound_reached": self._engineering_bound_reached,
                "engineering_bound_frame": self._engineering_bound_frame,
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


__all__ = ["DriveClarifyRQ2TV2EngineeringScenario"]
