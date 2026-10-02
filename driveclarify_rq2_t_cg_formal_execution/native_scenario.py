"""Non-controlling route-progress lifecycle owner for the frozen formal scenes."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

import carla
import py_trees
from srunner.scenarios.basic_scenario import BasicScenario

from driveclarify_rq2_t.measurement import canonical_sha256

from .routes import SCENARIO_TYPE
from .route_binding_v2 import binding_contract
from .scene_io import execution_scene_by_id


def _atomic_json(path, value):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name("." + target.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(str(temporary), str(target))


def _project(location, points):
    query = (float(location.x), float(location.y), float(location.z))
    best_distance, best_arc, traversed = float("inf"), 0.0, 0.0
    for left, right in zip(points, points[1:]):
        a = (float(left["x"]), float(left["y"]), float(left["z"]))
        b = (float(right["x"]), float(right["y"]), float(right["z"]))
        vector = tuple(b[i] - a[i] for i in range(3))
        length2 = sum(value * value for value in vector)
        if length2 <= 1e-12:
            continue
        fraction = max(0.0, min(1.0, sum((query[i] - a[i]) * vector[i] for i in range(3)) / length2))
        projected = tuple(a[i] + fraction * vector[i] for i in range(3))
        distance = math.dist(query, projected)
        segment = math.sqrt(length2)
        if distance < best_distance:
            best_distance, best_arc = distance, traversed + fraction * segment
        traversed += segment
    return best_arc, best_distance


class _FormalProgressOwner(py_trees.behaviour.Behaviour):
    def __init__(self, scenario):
        super().__init__(name="RQ2-T-CG frozen formal progress owner")
        self.scenario = scenario

    def initialise(self):
        self.scenario._activate(self.scenario._world.get_snapshot())

    def update(self):
        scenario = self.scenario
        snapshot = scenario._world.get_snapshot()
        location = scenario.ego_vehicles[0].get_location()
        progress, distance = _project(location, scenario._scene["route"]["waypoints"])
        previous_progress = scenario._last_progress_m
        relative = float(snapshot.timestamp.elapsed_seconds) - float(scenario._activation_time_s)
        scenario._last_progress_m = progress
        scenario._maximum_route_distance_m = max(scenario._maximum_route_distance_m, distance)
        for event in scenario._scene["events"]:
            event_id = event["event_id"]
            start = float(event["activation"]["start_inclusive_m"])
            end = float(event["activation"]["end_exclusive_m"])
            if event_id not in scenario._event_start_rows and progress >= start:
                scenario._event_start_rows[event_id] = {
                    "event_id": event_id, "event_kind": event["event_kind"], "owner": event["owner"],
                    "start_threshold_m": start, "observed_progress_m": progress,
                    "simulator_frame": int(snapshot.frame), "simulation_time_s": float(snapshot.timestamp.elapsed_seconds),
                    "relative_simulation_time_s": relative,
                    # A native 20 Hz tick may cross a narrow frozen progress
                    # interval in one step.  Segment/window intersection is
                    # the correct continuous-route admission test; requiring
                    # the sampled endpoint itself to remain below `end` would
                    # falsely classify a crossed interval as never entered.
                    "window_entry_observed": previous_progress < end and progress >= start,
                    "reads_view": False, "reads_outcome": False, "reads_passenger_intent": False,
                }
                scenario._write_receipt("EVENT_STARTED")
            if event_id not in scenario._event_end_rows and progress >= end:
                scenario._event_end_rows[event_id] = {
                    "event_id": event_id, "end_threshold_m": end, "observed_progress_m": progress,
                    "simulator_frame": int(snapshot.frame), "simulation_time_s": float(snapshot.timestamp.elapsed_seconds),
                    "relative_simulation_time_s": relative,
                }
                scenario._write_receipt("EVENT_ENDED")
        commitment = float(scenario._scene["commitment"]["threshold_m"])
        if scenario._commitment is None and progress >= commitment:
            scenario._commitment = {
                "state": "COMMITMENT_OBSERVED", "threshold_m": commitment,
                "observed_progress_m": progress, "simulator_frame": int(snapshot.frame),
                "simulation_time_s": float(snapshot.timestamp.elapsed_seconds),
                "relative_simulation_time_s": relative,
                "owner": scenario._scene["commitment"]["owner"],
            }
            scenario._write_commitment()
            scenario._write_receipt("COMMITMENT_OBSERVED")
        if scenario._commitment is not None and float(snapshot.timestamp.elapsed_seconds) - float(scenario._commitment["simulation_time_s"]) >= 1.0:
            scenario._terminal = {
                "state": "NATURAL_HORIZON_OBSERVED", "simulator_frame": int(snapshot.frame),
                "simulation_time_s": float(snapshot.timestamp.elapsed_seconds),
                "relative_simulation_time_s": relative, "progress_m": progress,
            }
            scenario._write_receipt("NATURAL_HORIZON_OBSERVED")
            # The scientific trace ends at the declared natural horizon, but
            # the native evaluator must be allowed to finish the unchanged
            # route and adjudicate its official criteria.  Returning FAILURE
            # here mislabeled a successfully observed horizon as a scenario
            # failure; SUCCESS leaves vehicle control untouched and lets the
            # enclosing RouteScenario continue to route completion.
            return py_trees.common.Status.SUCCESS
        administrative_cap_s = float(
            scenario._scene.get("horizon", {}).get("administrative_cap_simulation_s", 20.0)
        )
        if relative >= administrative_cap_s:
            scenario._terminal = {
                "state": "ADMINISTRATIVE_CAP_COMMITMENT_NOT_OBSERVED", "simulator_frame": int(snapshot.frame),
                "simulation_time_s": float(snapshot.timestamp.elapsed_seconds),
                "relative_simulation_time_s": relative, "progress_m": progress,
                "administrative_cap_simulation_s": administrative_cap_s,
            }
            scenario._write_receipt("ADMINISTRATIVE_CAP")
            return py_trees.common.Status.FAILURE
        return py_trees.common.Status.RUNNING


class DriveClarifyRQ2TCGFormalScenario(BasicScenario):
    def __init__(self, world, ego_vehicles, config, randomize=False, debug_mode=False, criteria_enable=True, timeout=10000):
        del randomize
        parameters = getattr(config, "other_parameters", {}).get("rq2_t_cg_formal")
        if not isinstance(parameters, dict):
            raise RuntimeError("RQ2_T_CG_FORMAL_PARAMETERS_MISSING")
        engineering = os.environ.get("DRIVECLARIFY_RQ2_T_CG_ENGINEERING_QUALIFICATION") == "1"
        scene = dict(execution_scene_by_id(
            str(parameters.get("formal_scene_id")), engineering=engineering,
        ))
        if parameters.get("formal_scene_digest") != scene["formal_scene_digest"]:
            raise RuntimeError("RQ2_T_CG_FORMAL_SCENE_DIGEST_MISMATCH")
        if parameters.get("route_spec_digest") != scene["route"]["route_spec_digest"]:
            raise RuntimeError("RQ2_T_CG_FORMAL_ROUTE_SPEC_DIGEST_MISMATCH")
        route_binding = binding_contract(scene)
        if parameters.get("route_binding_v2_digest") != route_binding["route_binding_v2_digest"]:
            raise RuntimeError("RQ2_T_CG_FORMAL_ROUTE_BINDING_V2_DIGEST_MISMATCH")
        actual_town = world.get_map().name.split("/")[-1]
        expected_town = scene["route"]["town"]
        if not (actual_town == expected_town or (expected_town == "Town10HD" and actual_town == "Town10HD_Opt")):
            raise RuntimeError("RQ2_T_CG_FORMAL_MAP_MISMATCH")
        self.timeout = timeout
        self._world, self._scene = world, scene
        self._route_binding = route_binding
        self._event_start_rows, self._event_end_rows = {}, {}
        self._commitment, self._terminal = None, None
        self._activation_time_s, self._activation_frame = None, None
        self._last_progress_m, self._maximum_route_distance_m = 0.0, 0.0
        self._cleanup_complete = False
        self._engineering_qualification = engineering
        self._formal_scientific_exposure = not self._engineering_qualification
        self._receipt_path = os.environ.get("DRIVECLARIFY_RQ2_T_CG_FORMAL_SCENARIO_RECEIPT")
        self._activation_path = os.environ.get("DRIVECLARIFY_RQ2_T_CG_FORMAL_ACTIVATION_RECEIPT")
        self._commitment_path = os.environ.get("DRIVECLARIFY_RQ2_T_CG_FORMAL_COMMITMENT_RECEIPT")
        super().__init__(SCENARIO_TYPE, ego_vehicles, config, world, debug_mode, criteria_enable=criteria_enable)
        self._write_receipt("INITIALIZED")

    def _initialize_actors(self, config):
        del config
        # Candidate entities/tasks are controlled certificate operands.  Their
        # exact frozen route-progress/lateral layout is recorded, but no actor
        # may perturb the method-neutral native trajectory.
        self.other_actors = []

    def _create_behavior(self):
        return _FormalProgressOwner(self)

    def _create_test_criteria(self):
        return []

    def _activate(self, snapshot):
        if self._activation_time_s is not None:
            raise RuntimeError("RQ2_T_CG_FORMAL_ACTIVATION_REPEATED")
        self._activation_time_s = float(snapshot.timestamp.elapsed_seconds)
        self._activation_frame = int(snapshot.frame)
        value = {
            "schema_version": "driveclarify.rq2_t_cg.formal_activation.v1",
            "cell_id": os.environ.get("DRIVECLARIFY_RQ2_T_CG_FORMAL_CELL_ID"),
            "formal_scene_id": self._scene["formal_scene_id"],
            "formal_scene_digest": self._scene["formal_scene_digest"],
            "route_spec_digest": self._scene["route"]["route_spec_digest"],
            "route_binding_v2_digest": self._route_binding["route_binding_v2_digest"],
            "simulator_frame": self._activation_frame, "simulator_time_s": self._activation_time_s,
            "formal_scientific_exposure": self._formal_scientific_exposure,
            "engineering_qualification": self._engineering_qualification,
            "online_ask_count": 0,
        }
        value["receipt_digest"] = canonical_sha256(value)
        _atomic_json(self._activation_path, value)
        self._write_receipt("ACTIVATED_AND_EXPOSED")

    def _write_commitment(self):
        value = {
            "schema_version": "driveclarify.rq2_t_cg.formal_commitment.v1",
            "cell_id": os.environ.get("DRIVECLARIFY_RQ2_T_CG_FORMAL_CELL_ID"),
            "formal_scene_id": self._scene["formal_scene_id"],
            "formal_scene_digest": self._scene["formal_scene_digest"],
            "commitment": self._commitment, "deadline_reserve_simulation_s": 1.2,
            "scientific_clock": "CARLA_SIMULATION_TIME",
        }
        value["receipt_digest"] = canonical_sha256(value)
        _atomic_json(self._commitment_path, value)

    def _write_receipt(self, status):
        if not self._receipt_path:
            return
        value = {
            "schema_version": "driveclarify.rq2_t_cg.formal_scenario_receipt.v1",
            "status": status, "cell_id": os.environ.get("DRIVECLARIFY_RQ2_T_CG_FORMAL_CELL_ID"),
            "formal_scene_id": self._scene["formal_scene_id"], "scene_code": self._scene["scene_code"],
            "formal_scene_digest": self._scene["formal_scene_digest"],
            "route_spec_digest": self._scene["route"]["route_spec_digest"],
            "route_binding_v2_digest": self._route_binding["route_binding_v2_digest"],
            "activation_frame": self._activation_frame, "activation_simulation_time_s": self._activation_time_s,
            "event_start_rows": [self._event_start_rows[key] for key in sorted(self._event_start_rows)],
            "event_end_rows": [self._event_end_rows[key] for key in sorted(self._event_end_rows)],
            "expected_event_count": len(self._scene["events"]), "commitment": self._commitment,
            "terminal": self._terminal, "last_progress_m": self._last_progress_m,
            "maximum_route_projection_distance_m": self._maximum_route_distance_m,
            "candidate_layout": self._scene["actor_or_task_layout"],
            "candidate_layout_is_noncontrolling_certificate_operand": True,
            "formal_scientific_exposure": (
                self._formal_scientific_exposure and self._activation_time_s is not None
            ),
            "engineering_qualification": self._engineering_qualification,
            "controlled_evidence_built_in_runtime": False, "vehicle_behavior_modified": False,
            "online_ask_count": 0, "cleanup_complete": self._cleanup_complete,
        }
        value["receipt_digest"] = canonical_sha256(value)
        _atomic_json(self._receipt_path, value)

    def remove_all_actors(self):
        self._cleanup_complete = True
        self._write_receipt("CLEANED")


__all__ = ["DriveClarifyRQ2TCGFormalScenario"]
