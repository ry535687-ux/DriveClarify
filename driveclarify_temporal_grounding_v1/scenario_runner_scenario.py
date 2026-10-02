"""Independent TRAIN diagnostic bus-clear fixture (not in frozen population)."""

from __future__ import annotations

import carla
import py_trees

from srunner.scenariomanager.carla_data_provider import CarlaDataProvider
from srunner.scenariomanager.scenarioatomics.atomic_behaviors import (
    ActorDestroy,
    ActorTransformSetter,
    Idle,
    KeepVelocity,
)
from srunner.scenariomanager.scenarioatomics.atomic_trigger_conditions import DriveDistance
from srunner.scenarios.basic_scenario import BasicScenario


class DriveClarifyTemporalGroundingV1Scenario(BasicScenario):
    """A city bus visibly leaves the ego-forward junction conflict corridor."""

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
        self.timeout = timeout
        self._world = world
        self._visible_transform = carla.Transform(
            carla.Location(x=107.084907532, y=-2.079092503, z=0.316792309),
            carla.Rotation(yaw=178.687240601),
        )
        super().__init__(
            "DriveClarifyTemporalGroundingV1Scenario",
            ego_vehicles,
            config,
            world,
            debug_mode,
            criteria_enable=criteria_enable,
        )

    def _initialize_actors(self, config):
        del config
        hidden = carla.Transform(
            carla.Location(
                x=self._visible_transform.location.x,
                y=self._visible_transform.location.y,
                z=self._visible_transform.location.z - 500.0,
            ),
            self._visible_transform.rotation,
        )
        actor = CarlaDataProvider.request_new_actor(
            "vehicle.mitsubishi.fusorosa", hidden, rolename="tgv1_bus"
        )
        if actor is None:
            raise RuntimeError("TEMPORAL_GROUNDING_BUS_SPAWN_FAILED")
        actor.set_simulate_physics(False)
        self.other_actors.append(actor)

    def _create_behavior(self):
        sequence = py_trees.composites.Sequence(name="TemporalGroundingBusClears")
        sequence.add_child(
            ActorTransformSetter(self.other_actors[0], self._visible_transform, True)
        )
        # The detector runs synchronously on one simulation frame. A short
        # simulation dwell preserves initial visibility without making the
        # slow-starting bus a collision obstacle for the baseline holding plan.
        sequence.add_child(Idle(0.25))
        moving = py_trees.composites.Parallel(
            name="BusClearsConflictRegion",
            policy=py_trees.common.ParallelPolicy.SUCCESS_ON_ONE,
        )
        moving.add_child(KeepVelocity(self.other_actors[0], 8.0, force_speed=True))
        moving.add_child(DriveDistance(self.other_actors[0], 35.0))
        sequence.add_child(moving)
        # Retain actor identity after it has cleared; disappearance is never the
        # evidence used for CLEARED.
        sequence.add_child(Idle(30.0))
        sequence.add_child(ActorDestroy(self.other_actors[0]))
        return sequence

    def _create_test_criteria(self):
        return []

    def __del__(self):
        self.remove_all_actors()


__all__ = ["DriveClarifyTemporalGroundingV1Scenario"]
