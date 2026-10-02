"""Demo-only static visual referents for one TRAIN diagnostic route."""

from __future__ import annotations

import carla
from srunner.scenariomanager.carla_data_provider import CarlaDataProvider
from srunner.scenariomanager.scenarioatomics.atomic_behaviors import Idle
from srunner.scenarios.basic_scenario import BasicScenario

from .contracts import ACTORS, FIXTURE_ID


class DriveClarifyGroundedV1DistinctTrajectoryDemoScenario(BasicScenario):
    timeout = 90

    def __init__(
        self,
        world,
        ego_vehicles,
        config,
        randomize=False,
        debug_mode=False,
        criteria_enable=True,
        timeout=90,
    ):
        del randomize
        if str(getattr(config, "name", "")) != FIXTURE_ID:
            raise RuntimeError("DEMO_FIXTURE_ID_MISMATCH")
        self.timeout = timeout
        super().__init__(
            "DriveClarifyGroundedV1DistinctTrajectoryDemoScenario",
            ego_vehicles,
            config,
            world,
            debug_mode,
            criteria_enable=criteria_enable,
        )

    def _initialize_actors(self, config):
        del config
        for index, (model, x_value, y_value, z_value, yaw) in enumerate(
            ACTORS, start=1
        ):
            actor = CarlaDataProvider.request_new_actor(
                model,
                carla.Transform(
                    carla.Location(x=x_value, y=y_value, z=z_value),
                    carla.Rotation(yaw=yaw),
                ),
                rolename="grounded_v1_demo_white_van_{}".format(index),
                color="255,255,255",
            )
            if actor is None:
                raise RuntimeError("DEMO_WHITE_VAN_SPAWN_FAILED:{}".format(index))
            actor.set_simulate_physics(False)
            self.other_actors.append(actor)

    def _create_behavior(self):
        return Idle(60.0)

    def _create_test_criteria(self):
        return []

    def __del__(self):
        self.remove_all_actors()


__all__ = ["DriveClarifyGroundedV1DistinctTrajectoryDemoScenario"]
