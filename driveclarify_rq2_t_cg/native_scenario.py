"""Native engineering scene owner; controlled evidence is built only later."""

from __future__ import annotations

import json
import os
from pathlib import Path

import carla
import py_trees
from srunner.scenariomanager.carla_data_provider import CarlaDataProvider
from srunner.scenarios.basic_scenario import BasicScenario

from driveclarify_rq2_t.measurement import canonical_sha256

from .lifecycle import SCENARIO_TYPE
from .scene_bindings import frozen_binding


def _atomic_json(path: str, value) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name("." + target.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, target)


def _transform(values):
    x, y, z, yaw = values
    return carla.Transform(carla.Location(x=x, y=y, z=z), carla.Rotation(yaw=yaw))


def _values(value):
    return [float(value.location.x), float(value.location.y), float(value.location.z), float(value.rotation.yaw)]


class _ControlledEventSequence(py_trees.behaviour.Behaviour):
    def __init__(self, scenario):
        super().__init__(name="RQ2-T-CG prospective native event sequence")
        self.scenario = scenario
        self.start = None

    def initialise(self):
        snapshot = self.scenario._world.get_snapshot()
        self.scenario._activate(snapshot)
        self.start = float(snapshot.timestamp.elapsed_seconds)

    def update(self):
        snapshot = self.scenario._world.get_snapshot()
        relative = float(snapshot.timestamp.elapsed_seconds) - float(self.start)
        for row in self.scenario._event_rows:
            if row["first_verified_effective_frame"] is not None or int(snapshot.frame) <= row["executed_frame"]:
                continue
            actor = self.scenario._owned_actors[row["actor_index"]]
            observed, authored = _values(actor.get_transform()), row["authored_transform_xyz_yaw"]
            yaw_delta = abs(((observed[3] - authored[3] + 180.0) % 360.0) - 180.0)
            if all(abs(left - right) <= 0.02 for left, right in zip(observed[:3], authored[:3])) and yaw_delta <= 0.05:
                row["first_verified_effective_frame"] = int(snapshot.frame)
                row["first_verified_effective_relative_simulation_s"] = relative
                row["first_verified_observed_transform_xyz_yaw"] = observed
                self.scenario._write_receipt("EVENT_EFFECT_VERIFIED")
        for index, event in enumerate(self.scenario._binding.get("events", ())):
            if index in self.scenario._executed_event_indices or relative < float(event["at_simulation_s"]):
                continue
            actor = self.scenario._owned_actors[int(event["actor_index"])]
            actor.set_transform(_transform(event["transform"]))
            self.scenario._event_rows.append({
                "event_index": index, "event": event["event"], "event_owner": event["event_owner"],
                "actor_index": int(event["actor_index"]), "actor_id_postepisode_only": int(actor.id),
                "scheduled_relative_simulation_s": float(event["at_simulation_s"]),
                "executed_relative_simulation_s": relative, "executed_frame": int(snapshot.frame),
                "authored_transform_xyz_yaw": list(event["transform"]),
                "observed_transform_xyz_yaw": _values(actor.get_transform()),
                "first_verified_effective_frame": None,
                "first_verified_effective_relative_simulation_s": None,
                "first_verified_observed_transform_xyz_yaw": None,
            })
            self.scenario._executed_event_indices.add(index)
            self.scenario._write_receipt("EVENT_EXECUTED")
        if relative >= self.scenario._engineering_bound_s:
            self.scenario._engineering_bound_reached = True
            self.scenario._engineering_bound_frame = int(snapshot.frame)
            self.scenario._write_receipt("ENGINEERING_BOUND_REACHED")
            return py_trees.common.Status.FAILURE
        return py_trees.common.Status.RUNNING


class DriveClarifyRQ2TCGEngineeringScenario(BasicScenario):
    def __init__(self, world, ego_vehicles, config, randomize=False, debug_mode=False, criteria_enable=True, timeout=10000):
        del randomize
        parameters = getattr(config, "other_parameters", {}).get("rq2_t_cg")
        if not isinstance(parameters, dict):
            raise RuntimeError("RQ2_T_CG_SCENARIO_BINDING_MISSING")
        scene_id = str(parameters.get("scene_config_id"))
        binding = dict(frozen_binding(scene_id))
        if parameters.get("scene_configuration_sha256") != binding["scene_configuration_sha256"]:
            raise RuntimeError("RQ2_T_CG_SCENARIO_HASH_MISMATCH")
        actual_town = world.get_map().name.split("/")[-1]
        if not (actual_town == binding["town"] or (binding["town"] == "Town10HD" and actual_town == "Town10HD_Opt")):
            raise RuntimeError("RQ2_T_CG_SCENARIO_MAP_MISMATCH")
        self.timeout = timeout
        self._world, self._binding, self._scene_id = world, binding, scene_id
        self._owned_actors, self._spawn_rows, self._event_rows = [], [], []
        self._executed_event_indices = set()
        self._cleanup_complete = False
        self._engineering_bound_s = float(binding["engineering_bound_s"])
        self._engineering_bound_reached, self._engineering_bound_frame = False, None
        self._activation_status, self._activation_frame, self._activation_time_s = "CONSTRUCTED_NOT_ACTIVATED", None, None
        self._activation_timer_start_count = 0
        self._receipt_path = os.environ.get("DRIVECLARIFY_RQ2_T_CG_SCENARIO_RECEIPT")
        self._activation_path = os.environ.get("DRIVECLARIFY_RQ2_T_CG_ACTIVATION_RECEIPT")
        super().__init__(SCENARIO_TYPE, ego_vehicles, config, world, debug_mode, criteria_enable=criteria_enable)
        self._verify_actors()
        self._write_receipt("INITIALIZED")

    def _initialize_actors(self, config):
        del config
        try:
            for index, spec in enumerate(self._binding["actors"]):
                elevated = list(spec["initial"])
                elevated[2] += 8.0
                actor = CarlaDataProvider.request_new_actor(
                    spec["blueprint"], _transform(elevated),
                    rolename="rq2_t_cg_{}_{}".format(self._scene_id.lower().replace("-", "_"), index),
                    autopilot=False, random_location=False, color=spec.get("color"), tick=True,
                )
                if actor is None:
                    raise RuntimeError("RQ2_T_CG_ACTOR_SPAWN_FAILED")
                if hasattr(actor, "set_simulate_physics"):
                    actor.set_simulate_physics(False)
                actor.set_transform(_transform(spec["initial"]))
                self._owned_actors.append(actor)
                self.other_actors.append(actor)
                self._spawn_rows.append({
                    "actor_index": index, "actor_id_postepisode_only": int(actor.id), "type_id": actor.type_id,
                    "candidate_id_postepisode_only": spec["candidate_id"], "certificate_role": spec["certificate_role"],
                    "initial_authored_xyz_yaw": list(spec["initial"]), "initial_observed_xyz_yaw": None,
                    "runtime_candidate_payload_excludes_actor_id": True,
                })
        except Exception:
            self._cleanup_owned_actors()
            raise

    def _verify_actors(self):
        for actor, spec, row in zip(self._owned_actors, self._binding["actors"], self._spawn_rows):
            observed = _values(actor.get_transform())
            row["initial_observed_xyz_yaw"] = observed
            if actor.type_id != spec["blueprint"] or any(abs(a - b) > 0.02 for a, b in zip(observed[:3], spec["initial"][:3])):
                raise RuntimeError("RQ2_T_CG_ACTOR_VERIFICATION_FAILED")

    def _create_behavior(self):
        return _ControlledEventSequence(self)

    def _create_test_criteria(self):
        return []

    def _activate(self, snapshot):
        if self._activation_status == "ACTIVATED_AND_BOUND":
            return
        if self._activation_timer_start_count:
            raise RuntimeError("RQ2_T_CG_ACTIVATION_TIMER_ALREADY_STARTED")
        self._activation_status = "ACTIVATED_AND_BOUND"
        self._activation_frame = int(snapshot.frame)
        self._activation_time_s = float(snapshot.timestamp.elapsed_seconds)
        self._activation_timer_start_count = 1
        admission = json.loads(Path(os.environ["DRIVECLARIFY_RQ2_T_CG_ROUTE_ADMISSION"]).read_text(encoding="utf-8"))
        receipt = {
            "schema_version": "driveclarify.rq2_t_cg.activation_receipt.v1",
            "engineering_identity": os.environ.get("DRIVECLARIFY_RQ2_T_CG_EPISODE_ID"),
            "scene_id": self._scene_id, "route_id": admission["route_id"], "route_digest": admission["route_sha256"],
            "scenario_config_digest": self._binding["scene_configuration_sha256"],
            "scenario_class": SCENARIO_TYPE, "simulator_frame": self._activation_frame,
            "simulator_time_s": self._activation_time_s,
            "scenario_activation_status": self._activation_status, "activation_timer_start_count": 1,
            "owned_actor_count": len(self._owned_actors), "formal_scientific_exposure": False,
        }
        receipt["activation_receipt_digest"] = canonical_sha256(receipt)
        if not self._activation_path:
            raise RuntimeError("RQ2_T_CG_ACTIVATION_RECEIPT_PATH_MISSING")
        _atomic_json(self._activation_path, receipt)
        self._write_receipt("ACTIVATED_AND_BOUND")

    def _write_receipt(self, status):
        if not self._receipt_path:
            return
        elapsed = None if self._activation_time_s is None else max(
            0.0, float(self._world.get_snapshot().timestamp.elapsed_seconds) - self._activation_time_s
        )
        _atomic_json(self._receipt_path, {
            "schema_version": "driveclarify.rq2_t_cg.engineering_scenario_receipt.v1",
            "status": status, "scene_config_id": self._scene_id,
            "scene_configuration_sha256": self._binding["scene_configuration_sha256"],
            "template": self._binding["template"], "family": self._binding["family"],
            "spawn_rows": self._spawn_rows, "expected_spawn_count": len(self._binding["actors"]),
            "event_rows": self._event_rows, "expected_event_count": len(self._binding["events"]),
            "all_events_executed": len(self._event_rows) == len(self._binding["events"]),
            "all_event_transforms_verified": len(self._event_rows) == len(self._binding["events"]) and all(
                row["first_verified_effective_frame"] is not None for row in self._event_rows
            ),
            "engineering_bound_s": self._engineering_bound_s,
            "engineering_bound_reached": self._engineering_bound_reached,
            "engineering_bound_frame": self._engineering_bound_frame,
            "scenario_activation_status": self._activation_status,
            "activation_frame": self._activation_frame, "activation_simulation_time_s": self._activation_time_s,
            "activation_timer_start_count": self._activation_timer_start_count,
            "scenario_elapsed_simulation_time_s": elapsed, "cleanup_complete": self._cleanup_complete,
            "controlled_evidence_built_in_runtime": False, "vehicle_behavior_modified": False,
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
        self._cleanup_complete = not self._owned_actors

    def remove_all_actors(self):
        self._cleanup_owned_actors()
        self._write_receipt("CLEANED" if self._cleanup_complete else "CLEANUP_FAILED")

    def __del__(self):
        self._cleanup_owned_actors()


__all__ = ["DriveClarifyRQ2TCGEngineeringScenario"]
