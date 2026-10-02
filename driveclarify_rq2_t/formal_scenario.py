"""Native RouteScenario wrapper for the recovered RQ2-T 2A scene assets."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import carla

from srunner.scenariomanager.carla_data_provider import CarlaDataProvider
from srunner.scenariomanager.scenarioatomics.atomic_behaviors import WaitForever
from srunner.scenarios.basic_scenario import BasicScenario

from driveclarify_rq2_t.experiment_2a import load_owner_bindings


EXPECTED_TYPE = "DriveClarifyRQ2T2AFormalScenario"
_ACTORS = {
    "REF-01": (
        (
            "vehicle.audi.a2",
            (18.895418167, 4512.141113281, 372.774536133, 179.866592407),
            "255,255,255",
        ),
        (
            "vehicle.lincoln.mkz_2020",
            (35.809803009, 4533.235351563, 372.673980713, 89.920394897),
            "255,255,255",
        ),
    ),
    "LMK-01": (
        (
            "static.prop.kiosk_01",
            (95.880250931, 71.221124506, 0.20, 90.390748182),
            None,
        ),
        (
            "static.prop.kiosk_01",
            (82.930492902, 63.354590344, 0.20, 179.926740697),
            None,
        ),
    ),
    "ORD-01": (),
    "ORD-02": (),
}


def _canonical_sha256(value):
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _atomic_json(path, value):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name("." + target.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, target)


class DriveClarifyRQ2T2AFormalScenario(BasicScenario):
    """Spawn only the semantic entities frozen by the accepted certificates."""

    def __init__(
        self,
        world,
        ego_vehicles,
        config,
        randomize=False,
        debug_mode=False,
        criteria_enable=True,
        timeout=10000,
    ):
        del randomize
        parameters = getattr(config, "other_parameters", {}).get("rq2_t")
        if not isinstance(parameters, dict):
            raise RuntimeError("RQ2_T_2A_SCENARIO_BINDING_MISSING")
        scene_key = parameters.get("scene_key")
        if scene_key not in _ACTORS:
            raise RuntimeError("RQ2_T_2A_SCENARIO_KEY_INVALID")
        binding = load_owner_bindings()["bindings"][scene_key]
        if (
            parameters.get("scenario_configuration_sha256")
            != binding["scenario_configuration_sha256"]
        ):
            raise RuntimeError("RQ2_T_2A_SCENARIO_CONFIGURATION_MISMATCH")
        expected_town = str(binding["map"])
        actual_town = world.get_map().name.split("/")[-1]
        if not (
            actual_town == expected_town
            or (expected_town == "Town10HD" and actual_town == "Town10HD_Opt")
        ):
            raise RuntimeError("RQ2_T_2A_SCENARIO_MAP_MISMATCH")
        self.timeout = timeout
        self._scene_key = scene_key
        self._world = world
        self._owned_actors = []
        self._spawn_rows = []
        self._cleanup_complete = False
        self._receipt_path = os.environ.get("DRIVECLARIFY_RQ2_T_2A_SCENARIO_RECEIPT")
        super().__init__(
            EXPECTED_TYPE,
            ego_vehicles,
            config,
            world,
            debug_mode,
            criteria_enable=criteria_enable,
        )
        self._verify_spawned_actors()
        self._write_receipt("INITIALIZED")

    def _initialize_actors(self, config):
        del config
        library = self._world.get_blueprint_library()
        try:
            for index, (blueprint_id, values, color) in enumerate(
                _ACTORS[self._scene_key], 1
            ):
                blueprint = library.find(blueprint_id)
                if color is not None and blueprint.has_attribute("color"):
                    blueprint.set_attribute("color", color)
                if blueprint.has_attribute("role_name"):
                    blueprint.set_attribute(
                        "role_name",
                        "rq2t_2a_{}_{}".format(
                            self._scene_key.lower().replace("-", "_"), index
                        ),
                    )
                x, y, z, yaw = values
                exact = carla.Transform(
                    carla.Location(x=x, y=y, z=z), carla.Rotation(yaw=yaw)
                )
                vehicle = blueprint_id.startswith("vehicle.")
                if vehicle:
                    initial = carla.Transform(
                        carla.Location(x=x, y=y, z=z + 8.0),
                        carla.Rotation(yaw=yaw),
                    )
                    mode = "ELEVATED_NATIVE_SPAWN_PHYSICS_OFF_EXACT_TELEPORT"
                else:
                    initial = exact
                    mode = "EXACT_POSE_DIRECT"
                role_name = "rq2t_2a_{}_{}".format(
                    self._scene_key.lower().replace("-", "_"), index
                )
                actor = CarlaDataProvider.request_new_actor(
                    blueprint_id,
                    initial,
                    rolename=role_name,
                    autopilot=False,
                    random_location=False,
                    color=color,
                    tick=True,
                )
                if actor is None and not vehicle:
                    actor = CarlaDataProvider.request_new_actor(
                        blueprint_id,
                        carla.Transform(
                            carla.Location(x=x, y=y, z=z + 8.0),
                            carla.Rotation(yaw=yaw),
                        ),
                        rolename=role_name,
                        autopilot=False,
                        random_location=False,
                        color=color,
                        tick=True,
                    )
                    mode = "ELEVATED_NATIVE_SPAWN_EXACT_TELEPORT"
                if actor is None:
                    raise RuntimeError("RQ2_T_2A_SEMANTIC_ACTOR_SPAWN_FAILED")
                self._owned_actors.append(actor)
                self.other_actors.append(actor)
                if hasattr(actor, "set_simulate_physics"):
                    actor.set_simulate_physics(False)
                actor.set_transform(exact)
                self._spawn_rows.append(
                    {
                        "actor_id": int(actor.id),
                        "actor_type_id": str(actor.type_id),
                        "requested_xyz_yaw": list(values),
                        "observed_xyz_yaw": None,
                        "spawn_mode": mode,
                    }
                )
        except Exception:
            self._cleanup_owned_actors()
            raise

    def _verify_spawned_actors(self):
        try:
            for actor, row in zip(self._owned_actors, self._spawn_rows):
                actual = actor.get_transform()
                observed = (
                    float(actual.location.x),
                    float(actual.location.y),
                    float(actual.location.z),
                    float(actual.rotation.yaw),
                )
                expected = row["requested_xyz_yaw"]
                row["observed_xyz_yaw"] = list(observed)
                if actor.type_id != row["actor_type_id"] or any(
                    abs(left - right) > 0.02
                    for left, right in zip(observed[:3], expected[:3])
                ):
                    raise RuntimeError(
                        "RQ2_T_2A_SEMANTIC_ACTOR_VERIFICATION_FAILED:"
                        + json.dumps(
                            {
                                "type": actor.type_id,
                                "expected": expected,
                                "observed": observed,
                            },
                            sort_keys=True,
                        )
                    )
        except Exception:
            self._cleanup_owned_actors()
            raise

    def _create_behavior(self):
        return WaitForever(name="RQ2-T 2A semantic scene remains active")

    def _create_test_criteria(self):
        return []

    def _write_receipt(self, status):
        if not self._receipt_path:
            return
        value = {
            "schema_version": "driveclarify.rq2_t.formal_scenario_receipt.v1",
            "status": status,
            "scene_key": self._scene_key,
            "spawn_rows": list(self._spawn_rows),
            "expected_spawn_count": len(_ACTORS[self._scene_key]),
            "cleanup_complete": self._cleanup_complete,
        }
        value["receipt_digest"] = _canonical_sha256(value)
        _atomic_json(self._receipt_path, value)

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
        survivors.reverse()
        survivor_ids = {id(actor) for actor in survivors}
        self._owned_actors = survivors
        self.other_actors = [
            actor
            for actor in getattr(self, "other_actors", [])
            if id(actor) in survivor_ids
        ]
        self._cleanup_complete = not survivors

    def remove_all_actors(self):
        self._cleanup_owned_actors()
        self._write_receipt("CLEANED" if self._cleanup_complete else "CLEANUP_FAILED")

    def __del__(self):
        self._cleanup_owned_actors()


__all__ = ["DriveClarifyRQ2T2AFormalScenario"]
