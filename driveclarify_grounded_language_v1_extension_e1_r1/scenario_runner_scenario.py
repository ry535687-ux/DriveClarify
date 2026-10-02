"""Nine append-only TRAIN physical fixtures for Grounded Language E1-R1."""

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

from .contracts import physical_fixture


def _fixture_id(config) -> str:
    fixture = getattr(config, "other_parameters", {}).get("fixture", {})
    value = fixture.get("fixture_id") if isinstance(fixture, dict) else None
    if not value:
        value = getattr(config, "name", None)
    if not value:
        raise RuntimeError("E1R1_SCENARIO_FIXTURE_ID_MISSING")
    return str(value)


def _transform(actor_row) -> carla.Transform:
    _, x_value, y_value, z_value, yaw = actor_row
    return carla.Transform(
        carla.Location(x=float(x_value), y=float(y_value), z=float(z_value)),
        carla.Rotation(yaw=float(yaw)),
    )


class DriveClarifyGroundedLanguageE1R1Scenario(BasicScenario):
    """Static referents or a scenario-owned independently moving bus."""

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
        self.fixture = physical_fixture(_fixture_id(config))
        self._visible_transforms = tuple(_transform(row) for row in self.fixture["actors"])
        super().__init__(
            "DriveClarifyGroundedLanguageE1R1Scenario",
            ego_vehicles,
            config,
            world,
            debug_mode,
            criteria_enable=criteria_enable,
        )

    def _initialize_actors(self, config):
        del config
        mechanism = self.fixture["mechanism_family"]
        for index, (row, visible) in enumerate(
            zip(self.fixture["actors"], self._visible_transforms), start=1
        ):
            model = str(row[0])
            transform = visible
            if mechanism == "WAIT":
                transform = carla.Transform(
                    carla.Location(
                        x=visible.location.x,
                        y=visible.location.y,
                        z=visible.location.z - 500.0,
                    ),
                    visible.rotation,
                )
            actor = CarlaDataProvider.request_new_actor(
                model,
                transform,
                rolename="e1r1_{}_{}".format(self.fixture["fixture_id"], index),
                color="255,255,255",
            )
            if actor is None:
                raise RuntimeError(
                    "E1R1_ACTOR_SPAWN_FAILED:{}:{}".format(
                        self.fixture["fixture_id"], index
                    )
                )
            actor.set_simulate_physics(False)
            self.other_actors.append(actor)

    def _create_behavior(self):
        if self.fixture["mechanism_family"] != "WAIT":
            return Idle(60.0)
        motion = self.fixture["motion"]
        sequence = py_trees.composites.Sequence(name="E1R1IndependentBusClears")
        sequence.add_child(
            ActorTransformSetter(self.other_actors[0], self._visible_transforms[0], True)
        )
        sequence.add_child(Idle(float(motion["visible_dwell_seconds"])))
        moving = py_trees.composites.Parallel(
            name="E1R1BusMotionIndependentOfEgoWait",
            policy=py_trees.common.ParallelPolicy.SUCCESS_ON_ONE,
        )
        moving.add_child(
            KeepVelocity(
                self.other_actors[0], float(motion["speed_mps"]), force_speed=True
            )
        )
        moving.add_child(
            DriveDistance(self.other_actors[0], float(motion["distance_m"]))
        )
        sequence.add_child(moving)
        # Preserve actor identity after region exit; the event estimator must
        # see tracked motion and cannot infer CLEARED from actor destruction.
        sequence.add_child(Idle(30.0))
        sequence.add_child(ActorDestroy(self.other_actors[0]))
        return sequence

    def _create_test_criteria(self):
        return []

    def __del__(self):
        self.remove_all_actors()


__all__ = ["DriveClarifyGroundedLanguageE1R1Scenario"]
