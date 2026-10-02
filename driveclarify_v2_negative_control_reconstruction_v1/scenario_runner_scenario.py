"""Physical-only ScenarioRunner implementation for the frozen controls."""

from __future__ import annotations

import carla
from srunner.scenariomanager.carla_data_provider import CarlaDataProvider
from srunner.scenariomanager.scenarioatomics.atomic_behaviors import Idle
from srunner.scenarios.basic_scenario import BasicScenario

from .contracts import physical_scenario


def _scenario_id(config) -> str:
    value = getattr(config, "other_parameters", {}).get("negative_control", {})
    value = value.get("scenario_id") if isinstance(value, dict) else None
    return str(value or getattr(config, "name", ""))


class DriveClarifyV2NegativeControlScenarioV1(BasicScenario):
    timeout = 90

    def __init__(self, world, ego_vehicles, config, randomize=False, debug_mode=False,
                 criteria_enable=True, timeout=90):
        del randomize
        self.timeout = timeout
        self.fixture = physical_scenario(_scenario_id(config))
        super().__init__(
            "DriveClarifyV2NegativeControlScenarioV1", ego_vehicles, config, world,
            debug_mode, criteria_enable=criteria_enable,
        )

    def _initialize_actors(self, config):
        del config
        ego_initial = self.fixture.get("ego_initial_transform")
        if ego_initial is not None:
            x_value, y_value, z_value, yaw = ego_initial
            self.ego_vehicles[0].set_transform(
                carla.Transform(
                    carla.Location(x=float(x_value), y=float(y_value), z=float(z_value)),
                    carla.Rotation(yaw=float(yaw)),
                )
            )
            self.ego_vehicles[0].set_target_velocity(carla.Vector3D(0.0, 0.0, 0.0))
        for index, row in enumerate(self.fixture["actors"], start=1):
            model, x_value, y_value, z_value, yaw = row
            actor = CarlaDataProvider.request_new_actor(
                model,
                carla.Transform(
                    carla.Location(x=float(x_value), y=float(y_value), z=float(z_value)),
                    carla.Rotation(yaw=float(yaw)),
                ),
                rolename="v2_negative_control_{}_{}".format(
                    self.fixture["scenario_id"], index
                ),
                color="255,255,255",
            )
            if actor is None:
                raise RuntimeError("V2_NEGATIVE_CONTROL_ACTOR_SPAWN_FAILED")
            actor.set_simulate_physics(False)
            self.other_actors.append(actor)

    def _create_behavior(self):
        return Idle(60.0)

    def _create_test_criteria(self):
        return []

    def __del__(self):
        self.remove_all_actors()


__all__ = ["DriveClarifyV2NegativeControlScenarioV1"]
