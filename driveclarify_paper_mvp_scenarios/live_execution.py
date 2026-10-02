"""Native ScenarioRunner execution of measured Stage 6A live receipts."""

from __future__ import annotations

import hashlib
import math
import os
import sys
import time
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional

from .contracts import canonical_sha256, file_sha256, pretty_json_bytes, require
from .live_contracts import (
    HANDLER_EXECUTION_RECEIPT_SCHEMA_VERSION,
    HANDLER_RECEIPT_HASH_FIELD,
    LIVE_RECEIPT_DIR,
    RECEIPT_HASH_FIELD,
    content_address,
)
from .live_promotion import _atomic_write, _carla_transform
SCENARIO_RUNNER_ROOT_DEFAULT = Path("/home/buaa/wrh/simlingo/scenario_runner")
CARLA_PYTHON_API_DEFAULT = Path("/home/buaa/CARLA_0.9.15/PythonAPI/carla")
GENERATED_ROOT_ENV = "DRIVECLARIFY_STAGE6A_GENERATED_ROOT"
PROMOTION_ROOT_ENV = "DRIVECLARIFY_STAGE6A_PROMOTION_ROOT"
SELECTED_SEED_ENV = "DRIVECLARIFY_STAGE6A_SELECTED_SEED"
AUTHORING_EXECUTION_ENV = "DRIVECLARIFY_STAGE6A_AUTHORING_EXECUTION"
AUTHORING_RECEIPT_ENV = "DRIVECLARIFY_STAGE6A_AUTHORING_RECEIPT"
AUTHORING_EXECUTION_TOKEN = "LIVE_CARLA_SCENARIO_RUNNER_EXECUTION"


@contextmanager
def _temporary_environment(values: Mapping[str, str]) -> Iterator[None]:
    previous = {key: os.environ.get(key) for key in values}
    try:
        os.environ.update(values)
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _route_locations(route_path: Path, carla: Any) -> list[Any]:
    route = ET.parse(route_path).getroot().find("route")
    require(route is not None, "LIVE_EXECUTION_ROUTE_ELEMENT_MISSING")
    result = []
    for position in route.findall("./waypoints/position"):
        result.append(
            carla.Location(
                x=float(position.get("x", "nan")),
                y=float(position.get("y", "nan")),
                z=float(position.get("z", "nan")),
            )
        )
    require(len(result) >= 2, "LIVE_EXECUTION_ROUTE_WAYPOINTS_MISSING")
    return result


def _validation_clearance_route(
    route_locations: list[Any],
    preliminary_receipt: Mapping[str, Any],
    carla: Any,
    *,
    carla_map: Any = None,
) -> tuple[list[Any], dict[str, Any]]:
    """Derive a control-only detour around receipt-bound physical blockers.

    Some controlled fixtures intentionally place a vehicle on the nominal
    centerline.  The formal scenario remains unchanged; only the bounded
    executability driver receives a smooth lateral path around measured actor
    boxes.  The original route endpoint and route hash stay authoritative.
    """

    if len(route_locations) < 3:
        return route_locations, {
            "applied": False,
            "reason": "ROUTE_TOO_SHORT_FOR_CLEARANCE_DERIVATION",
        }
    cumulative = [0.0]
    for first, second in zip(route_locations, route_locations[1:]):
        cumulative.append(cumulative[-1] + float(first.distance(second)))
    total = cumulative[-1]
    start = route_locations[0]
    finish = route_locations[-1]
    dx = float(finish.x) - float(start.x)
    dy = float(finish.y) - float(start.y)
    norm = math.hypot(dx, dy)
    if norm <= 1e-6:
        return route_locations, {
            "applied": False,
            "reason": "ROUTE_ENDPOINT_DIRECTION_DEGENERATE",
        }
    forward_x, forward_y = dx / norm, dy / norm
    left_x, left_y = -forward_y, forward_x
    ego = next(
        (
            item
            for item in preliminary_receipt["actor_bindings"]
            if item["binding_id"] == "ego"
        ),
        None,
    )
    def projected_lateral_half_width(
        transform: Mapping[str, Any],
        extent: Mapping[str, Any],
        bounding_box: Mapping[str, Any],
    ) -> float:
        yaw = math.radians(
            float(transform.get("yaw", 0.0))
            + float(bounding_box.get("rotation", {}).get("yaw", 0.0))
        )
        actor_forward_x, actor_forward_y = math.cos(yaw), math.sin(yaw)
        actor_left_x, actor_left_y = -actor_forward_y, actor_forward_x
        return (
            abs(actor_forward_x * left_x + actor_forward_y * left_y)
            * float(extent.get("x", 0.0))
            + abs(actor_left_x * left_x + actor_left_y * left_y)
            * float(extent.get("y", 0.0))
        )

    ego_probe = (ego or {}).get("spawn_probe")
    ego_box = (
        ego_probe.get("actor_bounding_box")
        if isinstance(ego_probe, Mapping)
        else None
    )
    ego_extent = ego_box.get("extent") if isinstance(ego_box, Mapping) else None
    ego_transform = (ego or {}).get("snap", {}).get("world_transform")
    ego_half_width = (
        projected_lateral_half_width(ego_transform, ego_extent, ego_box)
        if isinstance(ego_transform, Mapping)
        and isinstance(ego_extent, Mapping)
        and isinstance(ego_box, Mapping)
        else 1.0
    )
    obstacles: list[dict[str, Any]] = []
    for binding in preliminary_receipt["actor_bindings"]:
        # Nuisance background vehicles become live TrafficManager actors as
        # soon as the handler initialises.  Treating their initial spawn point
        # as a permanent wall can force the executor off-road and extend a
        # detour to the route endpoint.  Frozen controlled dynamic actors are
        # the stationary blockers that require a validation-only bypass.
        if binding.get("binding_id") == "ego" or binding.get("kind") != "dynamic_actor":
            continue
        transform = binding.get("snap", {}).get("world_transform")
        spawn_probe = binding.get("spawn_probe")
        bounding_box = (
            spawn_probe.get("actor_bounding_box")
            if isinstance(spawn_probe, Mapping)
            else None
        )
        extent = (
            bounding_box.get("extent")
            if isinstance(bounding_box, Mapping)
            else None
        )
        if not isinstance(transform, Mapping) or not isinstance(extent, Mapping):
            continue
        relative_x = float(transform["x"]) - float(start.x)
        relative_y = float(transform["y"]) - float(start.y)
        station = relative_x * forward_x + relative_y * forward_y
        lateral = relative_x * left_x + relative_y * left_y
        half_width = projected_lateral_half_width(transform, extent, bounding_box)
        if (
            0.0 < station < total - 2.0
            and abs(lateral) <= ego_half_width + half_width + 0.75
        ):
            obstacles.append(
                {
                    "binding_id": str(binding["binding_id"]),
                    "station_m": station,
                    "lateral_m": lateral,
                    "conservative_half_width_m": half_width,
                }
            )
    if not obstacles:
        return route_locations, {
            "applied": False,
            "reason": "NO_RECEIPT_BOUND_CENTERLINE_BLOCKER",
            "blocking_binding_ids": [],
        }
    required_offset = max(
        3.5,
        ego_half_width
        + max(item["conservative_half_width_m"] for item in obstacles)
        + 1.5,
    )
    all_actor_geometry: list[dict[str, float | str]] = []
    for binding in preliminary_receipt["actor_bindings"]:
        if binding.get("binding_id") == "ego":
            continue
        transform = binding.get("snap", {}).get("world_transform")
        spawn_probe = binding.get("spawn_probe")
        bounding_box = (
            spawn_probe.get("actor_bounding_box")
            if isinstance(spawn_probe, Mapping)
            else None
        )
        extent = (
            bounding_box.get("extent")
            if isinstance(bounding_box, Mapping)
            else None
        )
        if not isinstance(transform, Mapping) or not isinstance(extent, Mapping):
            continue
        relative_x = float(transform["x"]) - float(start.x)
        relative_y = float(transform["y"]) - float(start.y)
        station = relative_x * forward_x + relative_y * forward_y
        lateral = relative_x * left_x + relative_y * left_y
        if -5.0 <= station <= total + 5.0:
            radius = projected_lateral_half_width(transform, extent, bounding_box)
            all_actor_geometry.append(
                {
                    "binding_id": str(binding["binding_id"]),
                    "x": float(transform["x"]),
                    "y": float(transform["y"]),
                    "station_m": station,
                    "lateral_m": lateral,
                    "conservative_radius_m": radius,
                }
            )
    first_station = min(item["station_m"] for item in obstacles)
    last_station = max(item["station_m"] for item in obstacles)
    # Use all distance available before the first blocker.  The prior 5.5 m
    # ramp was geometrically clear but too sharp for repeatable closed-loop
    # tracking; an 11 m cosine transition preserves the same scene and target
    # clearance while keeping the validation path physically executable.
    ramp_in_start = max(0.0, first_station - 12.5)
    ramp_in_end = max(ramp_in_start + 1.0, first_station - 1.5)
    plateau_end = min(total - 4.0, last_station + 4.0)
    ramp_out_end = min(total - 0.5, plateau_end + 10.0)

    def blend(station: float) -> float:
        if station <= ramp_in_start or station >= ramp_out_end:
            return 0.0
        if station < ramp_in_end:
            ratio = (station - ramp_in_start) / (ramp_in_end - ramp_in_start)
            return 0.5 - 0.5 * math.cos(math.pi * ratio)
        if station <= plateau_end:
            return 1.0
        ratio = (station - plateau_end) / (ramp_out_end - plateau_end)
        return 0.5 + 0.5 * math.cos(math.pi * ratio)

    def derive(sign: float) -> list[Any]:
        result = []
        for index, (location, station) in enumerate(zip(route_locations, cumulative)):
            previous = route_locations[max(0, index - 1)]
            following = route_locations[min(len(route_locations) - 1, index + 1)]
            tangent_x = float(following.x) - float(previous.x)
            tangent_y = float(following.y) - float(previous.y)
            tangent_norm = math.hypot(tangent_x, tangent_y)
            if tangent_norm <= 1e-6:
                local_left_x, local_left_y = left_x, left_y
            else:
                local_left_x = -tangent_y / tangent_norm
                local_left_y = tangent_x / tangent_norm
            offset = sign * required_offset * blend(station)
            result.append(
                carla.Location(
                    x=float(location.x) + local_left_x * offset,
                    y=float(location.y) + local_left_y * offset,
                    z=float(location.z),
                )
            )
        return result

    def point_segment_distance(px: float, py: float, first: Any, second: Any) -> float:
        vx = float(second.x) - float(first.x)
        vy = float(second.y) - float(first.y)
        wx = px - float(first.x)
        wy = py - float(first.y)
        norm_squared = vx * vx + vy * vy
        projection = (
            max(0.0, min(1.0, (wx * vx + wy * vy) / norm_squared))
            if norm_squared > 1e-12
            else 0.0
        )
        return math.hypot(wx - projection * vx, wy - projection * vy)

    def path_clearance(path: list[Any]) -> float:
        return min(
            (
                min(
                    point_segment_distance(
                        float(actor["x"]),
                        float(actor["y"]),
                        first,
                        second,
                    )
                    for first, second in zip(path, path[1:])
                )
                - float(actor["conservative_radius_m"])
                - ego_half_width
                for actor in all_actor_geometry
            ),
            default=required_offset,
        )

    left_path = derive(1.0)
    right_path = derive(-1.0)
    left_clearance = path_clearance(left_path)
    right_clearance = path_clearance(right_path)

    def road_projection_error(path: list[Any]) -> tuple[float | None, int]:
        if carla_map is None:
            return None, 0
        errors = []
        for index, (location, station) in enumerate(zip(path, cumulative)):
            if index % 2 or blend(station) <= 1e-6:
                continue
            waypoint = carla_map.get_waypoint(
                location,
                project_to_road=True,
                lane_type=carla.LaneType.Driving,
            )
            if waypoint is None:
                errors.append(float("inf"))
            else:
                projected = waypoint.transform.location
                errors.append(
                    math.hypot(
                        float(location.x) - float(projected.x),
                        float(location.y) - float(projected.y),
                    )
                )
        return (max(errors) if errors else None), len(errors)

    left_road_error, left_road_queries = road_projection_error(left_path)
    right_road_error, right_road_queries = road_projection_error(right_path)
    if (
        left_road_error is not None
        and right_road_error is not None
        and abs(left_road_error - right_road_error) > 0.25
    ):
        sign = 1.0 if left_road_error < right_road_error else -1.0
        side_selection_basis = "MINIMUM_MAXIMUM_DRIVING_LANE_PROJECTION_ERROR"
    else:
        sign = 1.0 if left_clearance > right_clearance else -1.0
        side_selection_basis = "MAXIMUM_MINIMUM_ACTOR_CLEARANCE"
    derived = left_path if sign > 0.0 else right_path
    evidence = {
        "applied": True,
        "source": "LIVE_RECEIPT_ACTOR_TRANSFORMS_AND_POST_TICK_BOUNDING_BOXES",
        "formal_method_episode": False,
        "original_route_endpoint_preserved": bool(
            derived[-1].distance(route_locations[-1]) <= 1e-9
        ),
        "blocking_binding_ids": sorted(item["binding_id"] for item in obstacles),
        "blocking_geometry_sha256": canonical_sha256(obstacles),
        "lateral_side": "LEFT" if sign > 0.0 else "RIGHT",
        "side_selection_metric": "MAXIMUM_MINIMUM_POLYLINE_CLEARANCE_TO_ALL_RECEIPT_ACTOR_BOXES",
        "side_selection_basis": side_selection_basis,
        "left_minimum_measured_clearance_m": round(left_clearance, 9),
        "right_minimum_measured_clearance_m": round(right_clearance, 9),
        "left_maximum_driving_lane_projection_error_m": (
            None if left_road_error is None else round(left_road_error, 9)
        ),
        "right_maximum_driving_lane_projection_error_m": (
            None if right_road_error is None else round(right_road_error, 9)
        ),
        "driving_lane_projection_query_count": left_road_queries + right_road_queries,
        "maximum_lateral_offset_m": round(required_offset, 9),
        "ramp_in_start_station_m": round(ramp_in_start, 9),
        "ramp_in_end_station_m": round(ramp_in_end, 9),
        "plateau_end_station_m": round(plateau_end, 9),
        "ramp_out_end_station_m": round(ramp_out_end, 9),
        "detour_location_sha256": canonical_sha256(
            [
                [round(float(item.x), 9), round(float(item.y), 9), round(float(item.z), 9)]
                for item in derived
            ]
        ),
        "environment_or_policy_output_used_for_derivation": False,
    }
    return derived, evidence


def _speed(vehicle: Any) -> float:
    velocity = vehicle.get_velocity()
    return math.sqrt(
        float(velocity.x) ** 2 + float(velocity.y) ** 2 + float(velocity.z) ** 2
    )


def _angle_error_degrees(target: Any, transform: Any) -> float:
    desired = math.degrees(
        math.atan2(
            float(target.y) - float(transform.location.y),
            float(target.x) - float(transform.location.x),
        )
    )
    return (desired - float(transform.rotation.yaw) + 180.0) % 360.0 - 180.0


def _build_validation_agent(
    vehicle: Any,
    route_locations: list[Any],
    carla: Any,
    autonomous_agent_class: Any,
    sensor_interface_class: Any,
    pre_trigger_route_locations: Optional[list[Any]] = None,
    display_observer: Any = None,
    display_stride: int = 5,
    candidate_observer: Any = None,
) -> Any:
    class RouteValidationAgent(autonomous_agent_class):
        def setup(self, _path_to_conf_file):
            self.sensor_interface = sensor_interface_class()

        def sensors(self):
            return [
                {
                    "type": "sensor.camera.rgb",
                    "x": 1.5,
                    "y": 0.0,
                    "z": 2.4,
                    "roll": 0.0,
                    "pitch": -8.0,
                    "yaw": 0.0,
                    "width": 640,
                    "height": 360,
                    "fov": 90,
                    "id": "front_rgb",
                }
            ]

        def __call__(self):
            input_data = self.sensor_interface.get_data()
            return self.run_step(input_data, 0.0)

        def run_step(self, input_data, _timestamp):
            self.control_invocation_count += 1
            if self.control_invocation_count > self.maximum_control_invocations:
                raise RuntimeError("LIVE_VALIDATION_ROUTE_CONTROLLER_LIMIT_REACHED")
            frame, array = input_data["front_rgb"]
            captured_semantic_this_tick = False
            self.latest_rgb_frame = int(frame)
            self.latest_rgb_array = array.copy()
            if self.first_rgb_array is None:
                self.first_rgb_frame = int(frame)
                self.first_rgb_array = array.copy()
            if (
                self.semantic_rgb_array is None
                and self.scenario is not None
                and bool(getattr(self.scenario, "_timeline_initialised", False))
            ):
                self.semantic_rgb_frame = int(frame)
                self.semantic_rgb_array = array.copy()
                captured_semantic_this_tick = True
            if (
                callable(self.display_observer)
                and (
                    self.control_invocation_count == 1
                    or self.control_invocation_count % self.display_stride == 0
                )
            ):
                try:
                    self.display_observer(
                        array,
                        int(frame),
                        self.control_invocation_count,
                        self.scenario,
                    )
                    self.display_update_count += 1
                except Exception as exc:
                    self.display_errors.append(
                        type(exc).__name__ + ":" + str(exc)
                    )

            transform = self.vehicle.get_transform()
            location = transform.location
            if (
                bool(getattr(self.scenario, "_timeline_initialised", False))
                and not self.using_post_trigger_route
            ):
                self.route_locations = self.post_trigger_route_locations
                self.route_index = min(
                    range(len(self.route_locations)),
                    key=lambda index: location.distance(self.route_locations[index]),
                )
                self.using_post_trigger_route = True
                self.route_switch_count += 1
                self.route_switch_frame = int(frame)
                self.route_switch_index = int(self.route_index)
            while self.route_index + 1 < len(self.route_locations):
                current_distance = location.distance(
                    self.route_locations[self.route_index]
                )
                next_distance = location.distance(
                    self.route_locations[self.route_index + 1]
                )
                if current_distance < 1.5 or next_distance <= current_distance:
                    self.route_index += 1
                else:
                    break
            lookahead = min(self.route_index + 8, len(self.route_locations) - 1)
            target = self.route_locations[lookahead]
            error = _angle_error_degrees(target, transform)
            control = carla.VehicleControl()
            control.steer = max(-1.0, min(1.0, error / 35.0))
            speed = _speed(self.vehicle)
            if captured_semantic_this_tick and callable(self.candidate_observer):
                try:
                    observed_monotonic_time = time.monotonic()
                    self.semantic_candidate_capture = self.candidate_observer(
                        array,
                        int(frame),
                        {
                            "observed_monotonic_time": observed_monotonic_time,
                            "ego_state": {
                                "position_x_m": float(location.x),
                                "position_y_m": float(location.y),
                                "yaw_degrees": float(transform.rotation.yaw),
                                "speed_mps": float(speed),
                            },
                            "route_context": {
                                "route_command": "LANEFOLLOW",
                                "target_point_x_m": float(target.x),
                                "target_point_y_m": float(target.y),
                                "route_digest": canonical_sha256(
                                    [
                                        [
                                            round(float(item.x), 9),
                                            round(float(item.y), 9),
                                            round(float(item.z), 9),
                                        ]
                                        for item in self.route_locations
                                    ]
                                ),
                            },
                            "control_invocation_count": int(
                                self.control_invocation_count
                            ),
                            "route_index": int(self.route_index),
                            "source": (
                                "SAME_REAL_CARLA_FRONT_RGB_CALLBACK_AND_"
                                "EGO_ROUTE_CONTROL_TICK"
                            ),
                        },
                    )
                    if not isinstance(self.semantic_candidate_capture, Mapping):
                        raise TypeError("LIVE_CANDIDATE_OBSERVER_RESULT_NOT_MAPPING")
                except Exception as exc:
                    self.candidate_capture_errors.append(
                        type(exc).__name__ + ":" + str(exc)
                    )
            endpoint_distance = location.distance(self.route_locations[-1])
            self.minimum_endpoint_distance_m = min(
                self.minimum_endpoint_distance_m,
                float(endpoint_distance),
            )
            self.maximum_route_index = max(self.maximum_route_index, self.route_index)
            self.latest_motion = {
                "frame": int(frame),
                "x": round(float(location.x), 6),
                "y": round(float(location.y), 6),
                "z": round(float(location.z), 6),
                "yaw": round(float(transform.rotation.yaw), 6),
                "speed_mps": round(float(speed), 6),
                "route_index": int(self.route_index),
                "route_phase": (
                    "POST_TRIGGER_CLEARANCE_ROUTE"
                    if self.using_post_trigger_route
                    else "PRE_TRIGGER_ORIGINAL_ROUTE"
                ),
                "endpoint_distance_m": round(float(endpoint_distance), 6),
            }
            if self.control_invocation_count % 100 == 0:
                self.motion_samples.append(dict(self.latest_motion))
            target_speed = 2.5 if abs(error) > 12.0 else 5.0
            if speed < target_speed - 0.5:
                control.throttle = 0.55
                control.brake = 0.0
            elif speed > target_speed + 1.0:
                control.throttle = 0.0
                control.brake = 0.25
            else:
                control.throttle = 0.18
                control.brake = 0.0
            control.hand_brake = False
            control.manual_gear_shift = False
            return control

        def destroy(self):
            return None

    agent = RouteValidationAgent("")
    agent.vehicle = vehicle
    agent.pre_trigger_route_locations = (
        pre_trigger_route_locations
        if pre_trigger_route_locations is not None
        else route_locations
    )
    agent.post_trigger_route_locations = route_locations
    agent.route_locations = agent.pre_trigger_route_locations
    agent.route_index = 0
    agent.using_post_trigger_route = False
    agent.route_switch_count = 0
    agent.route_switch_frame = None
    agent.route_switch_index = None
    agent.maximum_control_invocations = 4000
    agent.control_invocation_count = 0
    agent.first_rgb_frame = None
    agent.first_rgb_array = None
    agent.latest_rgb_frame = None
    agent.latest_rgb_array = None
    agent.semantic_rgb_frame = None
    agent.semantic_rgb_array = None
    agent.scenario = None
    agent.display_observer = display_observer
    agent.display_stride = max(1, int(display_stride))
    agent.display_update_count = 0
    agent.display_errors = []
    agent.candidate_observer = candidate_observer
    agent.semantic_candidate_capture = None
    agent.candidate_capture_errors = []
    agent.minimum_endpoint_distance_m = float("inf")
    agent.maximum_route_index = 0
    agent.latest_motion = None
    agent.motion_samples = []
    return agent


def _write_rgb_evidence(
    output_root: Path,
    runtime_id: str,
    seed: int,
    frame: int,
    array: Any,
) -> dict[str, Any]:
    import cv2  # live-only dependency in the SimLingo environment

    require(array is not None, "LIVE_EXECUTION_FRONT_RGB_MISSING")
    require(len(array.shape) == 3 and array.shape[2] == 4, "LIVE_EXECUTION_FRONT_RGB_SHAPE_INVALID")
    bgr = array[:, :, :3]
    encoded, payload = cv2.imencode(".png", bgr)
    require(bool(encoded), "LIVE_EXECUTION_FRONT_RGB_PNG_ENCODE_FAILED")
    png = bytes(payload)
    png_sha256 = hashlib.sha256(png).hexdigest()
    relative = "rgb/%s.png" % png_sha256
    _atomic_write(output_root / relative, png)
    return {
        "sensor_type": "sensor.camera.rgb",
        "sensor_id": "front_rgb",
        "frame": int(frame),
        "width": int(array.shape[1]),
        "height": int(array.shape[0]),
        "channels": 4,
        "fov_degrees": 90,
        "mount": {"x": 1.5, "y": 0.0, "z": 2.4, "pitch": -8.0, "yaw": 0.0, "roll": 0.0},
        "raw_bgra_sha256": hashlib.sha256(array.tobytes()).hexdigest(),
        "png_path": relative,
        "png_file_sha256": png_sha256,
        "runtime_fixture_id": runtime_id,
        "selected_seed": int(seed),
        "genuine_carla_sensor_callback": True,
        "world_actor_state_projection_used_as_image": False,
    }


def _scenario_configuration(route_path: Path, route_parser: Any) -> Any:
    route_configs = route_parser.parse_routes_file(str(route_path))
    require(len(route_configs) == 1, "LIVE_EXECUTION_ROUTE_CONFIG_COUNT_INVALID")
    scenario_configs = route_configs[0].scenario_configs
    require(len(scenario_configs) == 1, "LIVE_EXECUTION_SCENARIO_CONFIG_COUNT_INVALID")
    config = scenario_configs[0]
    # Execute the real, route-bound scenario handler directly through
    # ScenarioManager.  The derived route is used by the validation driver and
    # remains hash-bound; no RouteScenario filtering/skipping layer is used.
    config.route = None
    config.route_var_name = None
    return config


def execute_preliminary_receipt(
    preliminary_receipt: Mapping[str, Any],
    runtime_input: Mapping[str, Any],
    world: Any,
    client: Any,
    carla: Any,
    output_root: Path,
    *,
    generated_root: Path,
    scenario_runner_root: Path = SCENARIO_RUNNER_ROOT_DEFAULT,
    traffic_manager_port: int = 8020,
    physical_display_verified: bool,
    carla_session_sha256: Optional[str] = None,
    live_display_observer: Any = None,
    live_display_stride: int = 5,
    live_candidate_observer: Any = None,
) -> dict[str, Any]:
    """Run one real handler/ScenarioManager execution and finalize its receipt."""

    require(physical_display_verified, "LIVE_EXECUTION_PHYSICAL_DISPLAY_NOT_VERIFIED")
    require(world.get_settings().no_rendering_mode is False, "LIVE_EXECUTION_RENDERING_DISABLED")
    runtime = runtime_input["runtime"]
    runtime_id = str(runtime["runtime_fixture_id"])
    seed = int(preliminary_receipt["selected_seed"])
    output_root = output_root.resolve()
    generated_root = generated_root.resolve()
    route_path = generated_root / runtime["route_binding"]["derived_route_path"]
    require(route_path.is_file(), "LIVE_EXECUTION_DERIVED_ROUTE_MISSING")

    preliminary_hash = str(preliminary_receipt[RECEIPT_HASH_FIELD])
    preliminary_relative = "preliminary_receipts/%s.json" % preliminary_hash
    _atomic_write(
        output_root / preliminary_relative,
        pretty_json_bytes(preliminary_receipt),
    )

    for path in (scenario_runner_root, CARLA_PYTHON_API_DEFAULT):
        value = str(path.resolve())
        if value not in sys.path:
            sys.path.insert(0, value)

    from srunner.autoagents.autonomous_agent import AutonomousAgent
    from srunner.autoagents.sensor_interface import SensorInterface
    from srunner.scenariomanager.carla_data_provider import CarlaDataProvider
    from srunner.scenariomanager.scenario_manager import ScenarioManager
    from srunner.tools.route_parser import RouteParser

    # Import after the native ScenarioRunner path is available.
    from .scenario_runner_scenario import DriveClarifyPaperMVPScenario

    original_route_locations = _route_locations(route_path, carla)
    route_locations, validation_route_derivation = _validation_clearance_route(
        original_route_locations,
        preliminary_receipt,
        carla,
        carla_map=world.get_map(),
    )
    validation_route_derivation = dict(validation_route_derivation)
    validation_route_derivation["activation_condition"] = (
        "AFTER_REAL_SCENARIO_RUNNER_TRIGGER_AND_HANDLER_TIMELINE_INITIALISE"
    )
    validation_route_derivation["pre_trigger_route"] = (
        "UNCHANGED_HASH_BOUND_DERIVED_ROUTE"
    )
    config = _scenario_configuration(route_path, RouteParser)
    ego_binding = next(
        item for item in preliminary_receipt["actor_bindings"] if item["binding_id"] == "ego"
    )
    blueprint = world.get_blueprint_library().find(
        ego_binding["blueprint_resolution"]["resolved_blueprint_id"]
    )
    if blueprint.has_attribute("role_name"):
        blueprint.set_attribute("role_name", "hero")

    manager: Optional[Any] = None
    scenario: Optional[Any] = None
    ego = None
    agent = None
    original_settings = world.get_settings()
    traffic_manager = client.get_trafficmanager(int(traffic_manager_port))
    starting_frame = int(world.get_snapshot().frame)
    if carla_session_sha256 is None:
        carla_session_sha256 = canonical_sha256(
            {
                "client_version": str(client.get_client_version()),
                "server_version": str(client.get_server_version()),
                "map_name": str(world.get_map().name),
                "starting_frame": starting_frame,
            }
        )
    require(
        len(carla_session_sha256) == 64,
        "LIVE_EXECUTION_CARLA_SESSION_SHA256_INVALID",
    )
    # ScenarioRunner's watchdog interrupts the *main Python thread*.  Keep its
    # deadline above the scenario's own bounded 120 s simulation timeout so a
    # slow physical run cannot bypass this function's cleanup/failure receipt
    # path with an asynchronous KeyboardInterrupt.
    scenario_manager_watchdog_timeout_seconds = 180.0
    error: Optional[BaseException] = None
    try:
        settings = world.get_settings()
        settings.synchronous_mode = True
        settings.fixed_delta_seconds = 0.05
        settings.no_rendering_mode = False
        world.apply_settings(settings)
        traffic_manager.set_synchronous_mode(True)
        world.tick()

        ego = world.try_spawn_actor(
            blueprint,
            _carla_transform(carla, ego_binding["snap"]["world_transform"]),
        )
        require(ego is not None, "LIVE_EXECUTION_EGO_SPAWN_FAILED")
        ego.set_simulate_physics(True)

        CarlaDataProvider.cleanup()
        CarlaDataProvider.set_client(client)
        CarlaDataProvider.set_traffic_manager_port(int(traffic_manager_port))
        CarlaDataProvider.set_world(world)
        CarlaDataProvider.register_actor(ego, ego.get_transform())
        agent = _build_validation_agent(
            ego,
            route_locations,
            carla,
            AutonomousAgent,
            SensorInterface,
            pre_trigger_route_locations=original_route_locations,
            display_observer=live_display_observer,
            display_stride=live_display_stride,
            candidate_observer=live_candidate_observer,
        )

        environment = {
            GENERATED_ROOT_ENV: str(generated_root),
            PROMOTION_ROOT_ENV: str(output_root),
            SELECTED_SEED_ENV: str(seed),
            AUTHORING_EXECUTION_ENV: AUTHORING_EXECUTION_TOKEN,
            AUTHORING_RECEIPT_ENV: preliminary_relative,
        }
        with _temporary_environment(environment):
            scenario = DriveClarifyPaperMVPScenario(
                world,
                [ego],
                config,
                criteria_enable=False,
                timeout=120,
            )
            agent.scenario = scenario
            manager = ScenarioManager(
                debug_mode=False,
                sync_mode=True,
                timeout=scenario_manager_watchdog_timeout_seconds,
            )
            manager.load_scenario(scenario, agent=agent)
            manager.run_scenario()
        world.tick()
        world.tick()
    except BaseException as exc:  # retain cleanup for CARLA/ScenarioRunner failures
        error = exc
    finally:
        if manager is not None and getattr(manager, "scenario", None) is not None:
            try:
                manager.cleanup()
            except Exception:
                pass
        if ego is not None:
            try:
                client.apply_batch_sync(
                    [carla.command.DestroyActor(int(ego.id))], True
                )
            except Exception:
                pass
        try:
            world.tick()
            world.tick()
        except Exception:
            pass
        try:
            traffic_manager.set_synchronous_mode(False)
        except Exception:
            pass
        try:
            world.apply_settings(original_settings)
        except Exception:
            pass
        try:
            CarlaDataProvider.cleanup()
        except Exception:
            pass

    if error is not None:
        raise RuntimeError(
            "LIVE_SCENARIO_RUNNER_EXECUTION_FAILED:%s:%s"
            % (type(error).__name__, error)
        ) from error
    require(scenario is not None and agent is not None and manager is not None, "LIVE_EXECUTION_INTERNAL_STATE_MISSING")
    require(agent.semantic_rgb_array is not None, "LIVE_EXECUTION_SEMANTIC_FRONT_RGB_MISSING")
    rgb = _write_rgb_evidence(
        output_root,
        runtime_id,
        seed,
        int(agent.semantic_rgb_frame),
        agent.semantic_rgb_array,
    )
    rgb["capture_phase"] = "FIRST_FRAME_AFTER_HANDLER_TIMELINE_INITIALISE"
    candidate_capture = agent.semantic_candidate_capture
    candidate_capture_verified = live_candidate_observer is None
    if live_candidate_observer is not None:
        require(not agent.candidate_capture_errors, "LIVE_CANDIDATE_CAPTURE_CALLBACK_FAILED")
        require(
            isinstance(candidate_capture, Mapping),
            "LIVE_CANDIDATE_CAPTURE_RECORD_MISSING",
        )
        candidate_capture_verified = bool(
            candidate_capture.get("status") == "VERIFIED"
            and candidate_capture.get("front_rgb_raw_sha256")
            == rgb["raw_bgra_sha256"]
            and candidate_capture.get("front_rgb_frame") == rgb["frame"]
            and candidate_capture.get("candidate_count") == 2
        )
        require(
            candidate_capture_verified,
            "LIVE_CANDIDATE_CAPTURE_IDENTITY_INVALID",
        )
    ending_frame = int(world.get_snapshot().frame)
    signal_scheduled = any(
        event.get("event_id") == "EV04_SCHEDULED_RUNTIME_SIGNAL"
        for event in runtime["event_timeline"]
    )
    owned_residual = [
        actor
        for actor in world.get_actors()
        if actor.attributes.get("role_name", "").startswith("driveclarify_")
        or actor.attributes.get("role_name") == "hero"
    ]
    status_verified = bool(
        scenario._timeline_initialised
        and scenario._timeline_update_count > 0
        and scenario._termination_reached
        and scenario._termination_reason == "ROUTE_ENDPOINT_REACHED"
        and scenario._cleanup_complete
        and not scenario._owned_actors
        and not owned_residual
        and (not signal_scheduled or scenario._runtime_signal_delivered)
        and ending_frame > starting_frame
        and (
            live_display_observer is None
            or (agent.display_update_count > 0 and not agent.display_errors)
        )
        and candidate_capture_verified
    )
    handler_unsigned = {
        "schema_version": HANDLER_EXECUTION_RECEIPT_SCHEMA_VERSION,
        "status": "VERIFIED" if status_verified else "BLOCKED",
        "source_kind": "LIVE_CARLA_SCENARIO_RUNNER_HANDLER_EXECUTION",
        "runtime_fixture_id": runtime_id,
        "runtime_manifest_sha256": runtime_input["runtime_sha256"],
        "derived_route_sha256": runtime_input["route_sha256"],
        "selected_seed": seed,
        "carla_session_sha256": carla_session_sha256,
        "preliminary_receipt_payload_sha256": preliminary_hash,
        "real_carla_execution": True,
        "injected_client_or_world": False,
        "physical_display_rendered": True,
        "no_rendering_mode": False,
        "scenario_runner": {
            "native_import_root_sha256": canonical_sha256(str(scenario_runner_root.resolve())),
            "scenario_manager_class": "srunner.scenariomanager.scenario_manager.ScenarioManager",
            "handler_class": "DriveClarifyPaperMVPScenario",
            "handler_registered": True,
            "handler_instantiated": True,
            "route_execution_mode": "SCENARIO_MANAGER_DERIVED_ROUTE_VALIDATION_AGENT",
            "route_scenario_skipped": False,
            "watchdog_timeout_seconds": scenario_manager_watchdog_timeout_seconds,
            "scenario_timeout_seconds": 120,
        },
        "timeline": {
            "initialised": bool(scenario._timeline_initialised),
            "update_count": int(scenario._timeline_update_count),
            "runtime_signal_scheduled": signal_scheduled,
            "runtime_signal_delivered": bool(scenario._runtime_signal_delivered),
            "world_start_frame": starting_frame,
            "world_end_frame": ending_frame,
            "world_tick_count_lower_bound": max(0, ending_frame - starting_frame),
            "world_tick_observed": ending_frame > starting_frame,
            "physical_timeline_verified": bool(scenario._timeline_initialised and scenario._timeline_update_count > 0),
        },
        "front_rgb": rgb,
        "termination": {
            "reached": bool(scenario._termination_reached),
            "reason": scenario._termination_reason,
            "scenario_tree_status": str(manager.scenario_tree.status),
            "lost_required_actor_evidence": list(
                getattr(scenario, "_lost_required_actor_evidence", [])
            ),
        },
        "cleanup": {
            "handler_cleanup_complete": bool(scenario._cleanup_complete),
            "handler_owned_actor_count": len(scenario._owned_actors),
            "residual_owned_world_actor_ids": sorted(int(actor.id) for actor in owned_residual),
            "verified": bool(scenario._cleanup_complete and not scenario._owned_actors and not owned_residual),
        },
        "validation_driver": {
            "kind": "BOUNDED_SCENARIO_EXECUTABILITY_ROUTE_FOLLOWER",
            "formal_method_episode": False,
            "simlingo_model_forward_count": 0,
            "existing_simlingo_pid_invocation_count": 0,
            "validation_control_invocation_count": int(agent.control_invocation_count),
            "route_clearance_derivation": validation_route_derivation,
            "trajectory_probe": {
                "maximum_route_index": int(agent.maximum_route_index),
                "pre_trigger_route_location_count": len(
                    agent.pre_trigger_route_locations
                ),
                "post_trigger_route_location_count": len(
                    agent.post_trigger_route_locations
                ),
                "route_switch_count": int(agent.route_switch_count),
                "route_switch_frame": agent.route_switch_frame,
                "route_switch_index": agent.route_switch_index,
                "minimum_endpoint_distance_m": round(
                    float(agent.minimum_endpoint_distance_m), 6
                ),
                "latest_motion": agent.latest_motion,
                "every_100_control_samples": list(agent.motion_samples),
                "world_tick_induced_count": 0,
                "planner_step_induced_count": 0,
                "control_mutation_count": 0,
            },
        },
        "runtime_visualization": {
            "native_window_requested": live_display_observer is not None,
            "display_update_count": int(agent.display_update_count),
            "display_stride": int(agent.display_stride),
            "errors": list(agent.display_errors),
            "source": "SAME_FRONT_RGB_SENSOR_CALLBACK_AS_VALIDATION_AGENT",
            "probe_induced_model_forward_count": 0,
            "probe_induced_pid_invocation_count": 0,
            "probe_induced_planner_step_count": 0,
            "vehicle_control_mutation_count": 0,
            "labels": [
                "RESEARCH DEBUG VIEW",
                "SIMULATION ONLY",
                "NO FORMAL SAFETY GUARANTEE",
            ],
            "verified": bool(
                live_display_observer is not None
                and agent.display_update_count > 0
                and not agent.display_errors
            ),
        },
        "runtime_candidate_generation": {
            "enabled": live_candidate_observer is not None,
            "verified": candidate_capture_verified,
            "capture": (
                None if candidate_capture is None else dict(candidate_capture)
            ),
            "errors": list(agent.candidate_capture_errors),
            "source": "SAME_REAL_CARLA_FRONT_RGB_SENSOR_CALLBACK",
            "world_actor_state_projection_used_as_image": False,
            "model_forward_count": 0,
            "pid_invocation_count": 0,
            "control_write_count": 0,
        },
    }
    handler_receipt, handler_hash = content_address(
        handler_unsigned, HANDLER_RECEIPT_HASH_FIELD
    )
    handler_relative = "handler_receipts/%s.json" % handler_hash
    _atomic_write(output_root / handler_relative, pretty_json_bytes(handler_receipt))
    require(status_verified, "LIVE_SCENARIO_RUNNER_HANDLER_EVIDENCE_INCOMPLETE")

    final_unsigned = dict(preliminary_receipt)
    final_unsigned.pop(RECEIPT_HASH_FIELD, None)
    final_unsigned["handler_execution_receipt"] = {
        "status": "VERIFIED",
        "verified": True,
        "receipt_path": handler_relative,
        "receipt_payload_sha256": handler_hash,
    }
    final_unsigned["gate_receipts"] = dict(final_unsigned["gate_receipts"])
    final_unsigned["gate_receipts"]["scenario_class_registration"] = True
    final_unsigned["gate_receipts"]["handler_spawn_tick_cleanup"] = True
    final_unsigned["promotion_ready"] = True
    final_unsigned["final_status"] = "PASS_LIVE_PROMOTION_READY"
    final_receipt, final_hash = content_address(final_unsigned, RECEIPT_HASH_FIELD)
    final_relative = "%s/%s.json" % (LIVE_RECEIPT_DIR, final_hash)
    _atomic_write(output_root / final_relative, pretty_json_bytes(final_receipt))
    return {
        "runtime_fixture_id": runtime_id,
        "selected_seed": seed,
        "receipt": final_receipt,
        "receipt_path": final_relative,
        "receipt_payload_sha256": final_hash,
        "handler_receipt": handler_receipt,
        "handler_receipt_path": handler_relative,
        "handler_receipt_payload_sha256": handler_hash,
        "rgb": rgb,
        "candidate_capture": candidate_capture,
        "validation_control_invocation_count": int(agent.control_invocation_count),
    }
