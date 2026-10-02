"""ScenarioRunner implementation for TRAIN-only decision activation."""

from __future__ import annotations

import carla
from srunner.scenariomanager.carla_data_provider import CarlaDataProvider
from srunner.scenariomanager.scenarioatomics.atomic_behaviors import Idle
from srunner.scenarios.basic_scenario import BasicScenario

from .contracts import scenario


def _scenario_id(config) -> str:
    value = getattr(config, "other_parameters", {}).get("activation", {})
    value = value.get("scenario_id") if isinstance(value, dict) else None
    return str(value or getattr(config, "name", ""))


class DriveClarifyDecisionActivationScenario(BasicScenario):
    timeout = 90

    def __init__(self, world, ego_vehicles, config, randomize=False, debug_mode=False,
                 criteria_enable=True, timeout=90):
        del randomize
        self.timeout = timeout
        self.fixture = scenario(_scenario_id(config))
        super().__init__("DriveClarifyDecisionActivationScenario", ego_vehicles,
                         config, world, debug_mode, criteria_enable=criteria_enable)

    def _initialize_actors(self, config):
        del config
        for index, row in enumerate(self.fixture["actors"], start=1):
            model, x, y, z, yaw = row
            actor = CarlaDataProvider.request_new_actor(
                model,
                carla.Transform(carla.Location(x=x, y=y, z=z), carla.Rotation(yaw=yaw)),
                rolename="decision_activation_{}_{}".format(self.fixture["scenario_id"], index),
                color="255,255,255",
            )
            if actor is None:
                raise RuntimeError("DECISION_ACTIVATION_ACTOR_SPAWN_FAILED")
            actor.set_simulate_physics(False)
            self.other_actors.append(actor)

    def _create_behavior(self):
        return Idle(60.0)

    def _create_test_criteria(self):
        return []

    def __del__(self):
        self.remove_all_actors()


__all__ = ["DriveClarifyDecisionActivationScenario"]
