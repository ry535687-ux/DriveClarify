"""V4 native ScenarioRunner owner with mandatory lifecycle receipts."""

from __future__ import annotations

import json
import math
import os
import xml.etree.ElementTree as ET
from pathlib import Path

from driveclarify_rq2_t_e2_v3.native_scenario import (
    DriveClarifyRQ2TE2V3EngineeringScenario,
    _ProspectiveEventSequence,
    _atomic_json,
    _transform_values,
)
from driveclarify_rq2_t_e2_v3.usc_admission import project_point_to_polyline
from srunner.scenarios.basic_scenario import BasicScenario

from .contracts import canonical_sha256
from .lifecycle import SCENARIO_TYPE
from .scene_bindings import frozen_binding


class DriveClarifyRQ2TE2V4EngineeringScenario(DriveClarifyRQ2TE2V3EngineeringScenario):
    def __init__(self, world, ego_vehicles, config, randomize=False, debug_mode=False, criteria_enable=True, timeout=10000):
        del randomize
        parameters = getattr(config, "other_parameters", {}).get("rq2_t_e2_v4")
        if not isinstance(parameters, dict):
            raise RuntimeError("E2_V4_ENGINEERING_SCENARIO_BINDING_MISSING")
        scene_config_id = str(parameters.get("scene_config_id"))
        binding = dict(frozen_binding(scene_config_id))
        if parameters.get("scene_configuration_sha256") != binding["scene_configuration_sha256"]:
            raise RuntimeError("E2_V4_ENGINEERING_SCENARIO_HASH_MISMATCH")
        actual_town = world.get_map().name.split("/")[-1]
        expected_town = str(binding["town"])
        if not (actual_town == expected_town or (expected_town == "Town10HD" and actual_town == "Town10HD_Opt")):
            raise RuntimeError("E2_V4_ENGINEERING_SCENARIO_MAP_MISMATCH")
        self.timeout = timeout
        self._world = world
        self._binding = binding
        self._scene_config_id = scene_config_id
        self._owned_actors = []
        self._spawn_rows = []
        self._event_rows = []
        self._executed_event_indices = set()
        self._cleanup_complete = False
        self._activation_only = False
        self._engineering_bound_s = float(binding.get("engineering_bound_s", 5.0))
        self._engineering_bound_reached = False
        self._engineering_bound_frame = None
        self._activation_status = "CONSTRUCTED_NOT_ACTIVATED"
        self._activation_frame = None
        self._activation_simulation_time_s = None
        self._activation_timer_start_count = 0
        self._scenario_instance_id = "{}:{}:{}".format(SCENARIO_TYPE, scene_config_id, id(self))
        self._initialization_error = None
        self._receipt_path = os.environ.get("DRIVECLARIFY_E2_V4_SCENARIO_RECEIPT")
        self._activation_receipt_path = os.environ.get("DRIVECLARIFY_E2_V4_ACTIVATION_RECEIPT")
        self._admission_path = os.environ.get("DRIVECLARIFY_E2_V4_LIFECYCLE_ADMISSION_RECEIPT")
        BasicScenario.__init__(self, SCENARIO_TYPE, ego_vehicles, config, world, debug_mode, criteria_enable=criteria_enable)
        self._verify_spawned_actors()
        self._write_receipt("INITIALIZED")

    def _create_behavior(self):
        return _ProspectiveEventSequence(self)

    def _activate(self, snapshot):
        if self._activation_status == "ACTIVATED_AND_BOUND":
            return
        if self._activation_timer_start_count != 0:
            raise RuntimeError("E2_V4_ACTIVATION_TIMER_ALREADY_STARTED")
        self._activation_status = "ACTIVATED_AND_BOUND"
        self._activation_frame = int(snapshot.frame)
        self._activation_simulation_time_s = float(snapshot.timestamp.elapsed_seconds)
        self._activation_timer_start_count = 1
        self._write_activation_receipt()
        self._write_receipt("ACTIVATED_AND_BOUND")

    def _write_activation_receipt(self):
        if not self._activation_receipt_path:
            raise RuntimeError("E2_V4_ACTIVATION_RECEIPT_PATH_MISSING")
        if not self._admission_path or not Path(self._admission_path).is_file():
            raise RuntimeError("E2_V4_LIFECYCLE_ADMISSION_MISSING_AT_ACTIVATION")
        admission = json.loads(Path(self._admission_path).read_text(encoding="utf-8"))
        if admission.get("status") != "PASS_STATIC_ROUTE_LIFECYCLE_ADMISSION":
            raise RuntimeError("E2_V4_ACTIVATION_WITHOUT_STATIC_ADMISSION")
        ego = self.ego_vehicles[0]
        transform = ego.get_transform()
        ego_xyz = [float(transform.location.x), float(transform.location.y), float(transform.location.z)]
        trigger = [float(value) for value in admission["trigger_transform_xyz"]]
        waypoint = self._world.get_map().get_waypoint(transform.location, project_to_road=True)
        route = ET.parse(admission["route_path"]).getroot().find("route")
        points = [
            [float(row.attrib[name]) for name in ("x", "y", "z")]
            for row in route.findall("./waypoints/position")
        ]
        receipt = {
            "schema_version": "driveclarify.e2_v4.activation_receipt.v1",
            "engineering_identity": os.environ.get("DRIVECLARIFY_E2_V4_EPISODE_ID"),
            "scene_id": self._scene_config_id,
            "route_id": admission["route_id"], "route_digest": admission["route_sha256"],
            "scenario_config_digest": self._binding["scene_configuration_sha256"],
            "scenario_class": SCENARIO_TYPE, "scenario_instance_id": self._scenario_instance_id,
            "simulator_frame": self._activation_frame, "simulator_time": self._activation_simulation_time_s,
            "ego_transform_xyz_yaw": _transform_values(transform),
            "ego_road_lane": {
                "road_id": int(waypoint.road_id), "lane_id": int(waypoint.lane_id),
                "is_junction": bool(waypoint.is_junction), "waypoint_s": float(waypoint.s),
            },
            "route_progress": dict(project_point_to_polyline(ego_xyz, points)),
            "trigger_transform_xyz": trigger, "trigger_distance": math.dist(ego_xyz, trigger),
            "owned_actor_count": len(self._owned_actors),
            "expected_semantic_actor_count": int(self._binding.get("expected_semantic_actor_count", len(self._binding["actors"]))),
            "scenario_activation_status": "ACTIVATED_AND_BOUND",
            "activation_timer_start_count": 1,
            "scenario_relative_horizon_s": self._engineering_bound_s,
            "formal_scientific_exposure": False,
        }
        receipt["activation_receipt_digest"] = canonical_sha256(receipt)
        _atomic_json(self._activation_receipt_path, receipt)

    def _write_receipt(self, status):
        if not self._receipt_path:
            return
        expected_events = len(self._binding.get("events", ()))
        elapsed = None if self._activation_simulation_time_s is None else max(
            0.0, float(self._world.get_snapshot().timestamp.elapsed_seconds) - float(self._activation_simulation_time_s)
        )
        _atomic_json(self._receipt_path, {
            "schema_version": "driveclarify.e2_v4.engineering_scenario_receipt.v1",
            "status": status, "scene_config_id": self._scene_config_id,
            "scene_configuration_sha256": self._binding["scene_configuration_sha256"],
            "family": self._binding["family"],
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
            "scenario_elapsed_simulation_time_s": elapsed,
            "scenario_relative_horizon_started_after_activation": self._activation_timer_start_count == 1,
            "runtime_candidate_payload_excludes_actor_ids": True,
            "initialization_error": self._initialization_error, "cleanup_complete": self._cleanup_complete,
        })


__all__ = ["DriveClarifyRQ2TE2V4EngineeringScenario"]
