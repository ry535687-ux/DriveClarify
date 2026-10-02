#!/usr/bin/env python3
"""Qualification-only read observer around the frozen RQ3 native agent.

The inherited implementation remains the only model, planner, PID, and
VehicleControl owner.  Every override calls its inherited counterpart exactly
once and returns the inherited value unchanged.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import random
import time
from typing import Any, Dict, Optional

import numpy as np
import torch
from srunner.scenariomanager.carla_data_provider import CarlaDataProvider

from driveclarify_clear_passthrough_v11.simlingo_agent import _atomic_json, _input_frame
from driveclarify_rq3.simlingo_agent import DriveClarifyRQ3SimLingoAgent
from driveclarify_rq3_native_qualification.liveness import (
    LivenessSample,
    NativeLivenessMonitor,
)
from driveclarify_rq3_native_qualification.trace import (
    JsonlTraceWriter,
    array_signature,
    canonical_sha256,
    jsonable,
    sensor_signatures,
)


TRACE_SCHEMA = "driveclarify.rq3-native-terminal-trace.v1"
RUNTIME_MODES = frozenset({"NATIVE_DEFAULT", "DETERMINISTIC_ENGINEERING"})


def get_entry_point():
    return "DriveClarifyNativeQualificationAgent"


def _controller_window(controller: Any) -> Any:
    for name in ("_window", "window"):
        value = getattr(controller, name, None)
        if value is not None:
            return [float(item) for item in value]
    return None


def _safe_scalar(value: Any) -> Optional[float]:
    try:
        if hasattr(value, "detach"):
            value = value.detach().float().cpu().numpy()
        array = np.asarray(value).reshape(-1)
        return None if not len(array) else float(array[0])
    except Exception:
        return None


class DriveClarifyNativeQualificationAgent(DriveClarifyRQ3SimLingoAgent):
    """Read-only native observer used only by the engineering qualification."""

    def _apply_runtime_mode(self) -> Dict[str, Any]:
        mode = os.environ.get(
            "DRIVECLARIFY_ENGINEERING_RUNTIME_MODE", "NATIVE_DEFAULT"
        )
        if mode not in RUNTIME_MODES:
            raise RuntimeError("RQ3_NATIVE_QUALIFICATION_RUNTIME_MODE_INVALID")
        seed_text = os.environ.get("DRIVECLARIFY_ENGINEERING_SEED")
        seed = None if seed_text is None else int(seed_text)
        changed = []
        if mode == "DETERMINISTIC_ENGINEERING":
            if seed is None:
                raise RuntimeError("DETERMINISTIC_ENGINEERING_SEED_REQUIRED")
            random.seed(seed)
            np.random.seed(seed % (2**32))
            torch.manual_seed(seed)
            torch.cuda.manual_seed_all(seed)
            torch.backends.cudnn.benchmark = False
            torch.backends.cudnn.deterministic = True
            if hasattr(torch.backends.cudnn, "allow_tf32"):
                torch.backends.cudnn.allow_tf32 = False
            if hasattr(torch.backends.cuda.matmul, "allow_tf32"):
                torch.backends.cuda.matmul.allow_tf32 = False
            torch.use_deterministic_algorithms(True, warn_only=True)
            changed = [
                "python_random_seed",
                "numpy_random_seed",
                "torch_cpu_rng_seed",
                "torch_cuda_rng_seed_all",
                "cudnn_benchmark=False",
                "cudnn_deterministic=True",
                "cudnn_allow_tf32=False",
                "cuda_matmul_allow_tf32=False",
                "torch_deterministic_algorithms_warn_only=True",
            ]
        return {
            "schema": "driveclarify.rq3-native-runtime-mode.v1",
            "mode": mode,
            "engineering_seed": seed,
            "non_formal_diagnostic_only": True,
            "changed_runtime_flags": changed,
            "torch_version": str(torch.__version__),
            "cuda_version": str(torch.version.cuda),
            "cudnn_version": (
                None if not torch.backends.cudnn.is_available() else torch.backends.cudnn.version()
            ),
            "cudnn_benchmark": bool(torch.backends.cudnn.benchmark),
            "cudnn_deterministic": bool(torch.backends.cudnn.deterministic),
            "cudnn_allow_tf32": bool(getattr(torch.backends.cudnn, "allow_tf32", False)),
            "cuda_matmul_allow_tf32": bool(
                getattr(torch.backends.cuda.matmul, "allow_tf32", False)
            ),
            "deterministic_algorithms_enabled": bool(
                torch.are_deterministic_algorithms_enabled()
            ),
            "cublas_workspace_config": os.environ.get("CUBLAS_WORKSPACE_CONFIG"),
        }

    def setup(self, path_to_conf_file, route_index=None, traffic_manager=None):
        self._qualification_trace_writer = None
        self._qualification_trace_errors = 0
        self._qualification_sensor_heartbeat = 0
        self._qualification_pid_heartbeat = 0
        self._qualification_planner_heartbeat = 0
        self._qualification_evaluator_heartbeat = 0
        self._qualification_pending_sensor = None
        self._qualification_pending_processed_input = None
        self._qualification_pending_pid = None
        self._qualification_last_liveness = None
        self._qualification_force_move_events = []
        self._qualification_started_monotonic_s = time.monotonic()
        self._qualification_runtime_mode = self._apply_runtime_mode()
        super().setup(
            path_to_conf_file,
            route_index=route_index,
            traffic_manager=traffic_manager,
        )
        self._qualification_liveness = NativeLivenessMonitor(
            simulation_window_s=float(
                self._v11_config.get("qualification_liveness_simulation_window_s", 4.0)
            ),
            wall_stall_s=float(
                self._v11_config.get("qualification_liveness_wall_stall_s", 10.0)
            ),
        )
        self._qualification_trace_writer = JsonlTraceWriter(
            self._output / "NATIVE_TERMINAL_TRACE.jsonl",
            fsync_interval=int(
                self._v11_config.get("qualification_trace_fsync_interval", 20)
            ),
        )
        self._qualification_runtime_mode["receipt_digest"] = canonical_sha256(
            self._qualification_runtime_mode
        )
        _atomic_json(
            self._output / "ENGINEERING_RUNTIME_MODE.json",
            self._qualification_runtime_mode,
        )

    def tick(self, input_data):
        try:
            self._qualification_pending_sensor = sensor_signatures(input_data)
        except Exception:
            self._qualification_trace_errors += 1
            self._qualification_pending_sensor = None
        result = super().tick(input_data)
        self._qualification_sensor_heartbeat += 1
        if self.initialized:
            self._qualification_planner_heartbeat += 1
        try:
            prompt = self.DrivingInput.get("prompt")
            self._qualification_pending_processed_input = {
                "camera_images": array_signature(
                    self.DrivingInput.get("camera_images"), include_values=False
                ),
                "image_sizes": jsonable(self.DrivingInput.get("image_sizes")),
                "vehicle_speed": jsonable(self.DrivingInput.get("vehicle_speed")),
                "target_point": jsonable(self.DrivingInput.get("target_point")),
                "route_switch_active": jsonable(
                    self.DrivingInput.get("route_switch_active")
                ),
                "language_string": jsonable(getattr(prompt, "language_string", None)),
                "language_token_ids": array_signature(
                    getattr(prompt, "phrase_ids", None), include_values=False
                ),
                "language_valid_mask": array_signature(
                    getattr(prompt, "phrase_valid", None), include_values=False
                ),
                "preprocessing": {
                    "raw_color_order": "CARLA_BGRA_FIRST_THREE_CHANNELS_BGR",
                    "jpeg_round_trip": True,
                    "jpeg_encoder_parameters": "OpenCV_defaults",
                    "post_jpeg_color_order": "RGB",
                    "vertical_crop_removed_fraction": 0.3,
                    "dynamic_preprocess_image_size": 448,
                    "dynamic_preprocess_max_num": 2,
                    "dynamic_preprocess_use_thumbnail": bool(
                        self.cfg.model.vision_model.use_global_img
                    ),
                    "model_image_dtype": "torch.bfloat16",
                },
            }
        except Exception:
            self._qualification_trace_errors += 1
            self._qualification_pending_processed_input = None
        return result

    def _derive_pid_operands(self, route_waypoints, velocity, speed_waypoints):
        route_np = route_waypoints[0].detach().float().cpu().numpy()
        speed_np = speed_waypoints[0].detach().float().cpu().numpy()
        current_speed = float(velocity[0].detach().float().cpu().numpy())
        one_second = int(
            self.config.carla_fps
            // (self.config.wp_dilation * self.config.data_save_freq)
        )
        half_second = one_second // 2
        speed_indices = [half_second - 2, one_second - 2]
        speed_points = [speed_np[index].tolist() for index in speed_indices]
        target_speed = float(
            np.linalg.norm(speed_np[speed_indices[0]] - speed_np[speed_indices[1]])
            * 2.0
        )
        longitudinal_error_unclipped = target_speed - current_speed
        longitudinal_error_clipped = float(
            np.clip(longitudinal_error_unclipped, 0.0, self.config.clip_delta)
        )
        expected_brake = bool(
            target_speed < self.config.brake_speed
            or np.divide(current_speed, target_speed) > self.config.brake_ratio
        )

        interpolated = self.interpolate_waypoints(route_np.squeeze())
        lateral_speed_kph = current_speed * 3.6
        lookahead = int(
            min(
                np.clip(
                    self.turn_controller.speed_scale * lateral_speed_kph
                    + self.turn_controller.speed_offset,
                    24,
                    105,
                ),
                interpolated.shape[0] - 1,
            )
        )
        lookahead = min(lookahead, len(interpolated) - 1)
        target_point = interpolated[lookahead]
        yaw_path = float(np.arctan2(target_point[1], target_point[0]))
        heading_error = yaw_path % (2.0 * np.pi)
        if heading_error >= np.pi:
            heading_error -= 2.0 * np.pi
        heading_error = float(heading_error * 180.0 / np.pi / 90.0)
        return {
            "controller_source": "agent_simlingo.LingoAgent.control_pid",
            "route_tensor_indices_consumed": list(range(int(route_np.shape[0]))),
            "route_tensor_values": jsonable(route_np),
            "route_tensor_signature": array_signature(route_waypoints, False),
            "interpolated_path_point_count": int(interpolated.shape[0]),
            "interpolated_path_index_consumed": int(lookahead),
            "target_point_consumed": jsonable(target_point),
            "speed_waypoint_indices_consumed": speed_indices,
            "speed_waypoint_values_consumed": speed_points,
            "speed_waypoint_tensor_values": jsonable(speed_np),
            "speed_waypoint_tensor_signature": array_signature(speed_waypoints, False),
            "target_speed_mps": target_speed,
            "current_speed_mps": current_speed,
            "longitudinal_error_unclipped_mps": longitudinal_error_unclipped,
            "longitudinal_error_clipped_mps": longitudinal_error_clipped,
            "native_brake_predicate": expected_brake,
            "brake_speed_threshold_mps": float(self.config.brake_speed),
            "brake_ratio": float(self.config.brake_ratio),
            "lateral_heading_error": heading_error,
            "longitudinal_pid_window_before": _controller_window(
                self.speed_controller
            ),
            "lateral_pid_window_before": _controller_window(self.turn_controller),
        }

    def control_pid(self, route_waypoints, velocity, speed_waypoints):
        started = time.monotonic()
        try:
            if self._qualification_pending_processed_input is not None:
                self._qualification_pending_processed_input["route_switch_active"] = jsonable(
                    self.DrivingInput.get("route_switch_active")
                )
            derived = self._derive_pid_operands(
                route_waypoints, velocity, speed_waypoints
            )
            model_output = {
                "pred_route": array_signature(route_waypoints, include_values=True),
                "pred_speed_wps": array_signature(
                    speed_waypoints, include_values=True
                ),
                "exact_objects_passed_to_native_pid": True,
                "source_frame_id": getattr(
                    self, "_qualification_current_frame", None
                ),
            }
            probe_pending = getattr(getattr(self, "_dc_probe", None), "_ws_pending", None)
            if isinstance(probe_pending, dict):
                model_output["model_start_monotonic_s"] = probe_pending.get(
                    "model_start_monotonic_s"
                )
                model_output["model_end_monotonic_s"] = probe_pending.get(
                    "model_end_monotonic_s"
                )
        except Exception as error:
            self._qualification_trace_errors += 1
            derived = {"derivation_error": repr(error)}
            model_output = {"capture_error": repr(error)}

        # Sole inherited/native PID invocation.  Its return is not changed.
        control = super().control_pid(route_waypoints, velocity, speed_waypoints)
        self._qualification_pid_heartbeat += 1
        derived.update(
            {
                "longitudinal_pid_window_after": _controller_window(
                    self.speed_controller
                ),
                "lateral_pid_window_after": _controller_window(
                    self.turn_controller
                ),
                "native_pid_return": {
                    "steer": float(control[0]),
                    "throttle": float(control[1]),
                    "brake": float(control[2]),
                },
                "observer_elapsed_wall_monotonic_s": time.monotonic() - started,
            }
        )
        self._qualification_pending_pid = {
            "model_output": model_output,
            "pid": derived,
        }
        return control

    def _world_state(self, frame: int) -> Dict[str, Any]:
        world = CarlaDataProvider.get_world()
        hero = CarlaDataProvider.get_hero_actor()
        snapshot = world.get_snapshot()
        transform = hero.get_transform()
        velocity = hero.get_velocity()
        acceleration = hero.get_acceleration()
        traffic_light = hero.get_traffic_light()
        traffic_light_state = {
            "affected": bool(hero.is_at_traffic_light()),
            "actor_id": None if traffic_light is None else int(traffic_light.id),
            "state": (
                None
                if traffic_light is None
                else str(traffic_light.get_state())
            ),
        }
        actor_rows = None
        actor_sampled = int(frame) % int(
            self._v11_config.get("qualification_actor_sample_interval_frames", 5)
        ) == 0
        if actor_sampled:
            actor_rows = []
            ego_location = transform.location
            for actor in world.get_actors():
                try:
                    if actor.id == hero.id or not actor.is_alive:
                        continue
                    if not actor.type_id.startswith(
                        ("vehicle.", "walker.", "traffic.traffic_light", "traffic.stop")
                    ):
                        continue
                    other_transform = actor.get_transform()
                    distance = ego_location.distance(other_transform.location)
                    if distance > 100.0:
                        continue
                    other_velocity = actor.get_velocity()
                    actor_rows.append(
                        {
                            "actor_id": int(actor.id),
                            "type_id": str(actor.type_id),
                            "role_name": str(actor.attributes.get("role_name", "")),
                            "distance_to_ego_m": float(distance),
                            "transform": {
                                "location_xyz": [
                                    float(other_transform.location.x),
                                    float(other_transform.location.y),
                                    float(other_transform.location.z),
                                ],
                                "rotation_rpy_deg": [
                                    float(other_transform.rotation.roll),
                                    float(other_transform.rotation.pitch),
                                    float(other_transform.rotation.yaw),
                                ],
                            },
                            "velocity_xyz_mps": [
                                float(other_velocity.x),
                                float(other_velocity.y),
                                float(other_velocity.z),
                            ],
                        }
                    )
                except Exception:
                    self._qualification_trace_errors += 1
        return {
            "world_frame_id": int(snapshot.frame),
            "simulation_time_s": float(snapshot.timestamp.elapsed_seconds),
            "simulation_delta_seconds": float(snapshot.timestamp.delta_seconds),
            "platform_timestamp_s": float(snapshot.timestamp.platform_timestamp),
            "ego_transform": {
                "location_xyz": [
                    float(transform.location.x),
                    float(transform.location.y),
                    float(transform.location.z),
                ],
                "rotation_rpy_deg": [
                    float(transform.rotation.roll),
                    float(transform.rotation.pitch),
                    float(transform.rotation.yaw),
                ],
            },
            "ego_velocity_xyz_mps": [
                float(velocity.x),
                float(velocity.y),
                float(velocity.z),
            ],
            "ego_acceleration_xyz_mps2": [
                float(acceleration.x),
                float(acceleration.y),
                float(acceleration.z),
            ],
            "ego_speed_mps": float(
                math.sqrt(velocity.x**2 + velocity.y**2 + velocity.z**2)
            ),
            "traffic_light": traffic_light_state,
            "nearby_relevant_actors_sampled": actor_sampled,
            "nearby_relevant_actors": actor_rows,
        }

    def _planner_state(self) -> Dict[str, Any]:
        planner = getattr(self, "_route_planner", None)
        route = list(getattr(planner, "route", ()))
        route_front = []
        for row in route[:3]:
            try:
                route_front.append(
                    {
                        "xyz": jsonable(row[0]),
                        "road_option": getattr(row[1], "name", str(row[1])),
                    }
                )
            except Exception:
                route_front.append({"unreadable": True})
        return {
            "planner_type": None if planner is None else type(planner).__name__,
            "route_length": len(route),
            "route_frontier": route_front,
            "route_frontier_digest": canonical_sha256(route_front),
            "is_last": getattr(planner, "is_last", None),
            "active_route_identity": getattr(planner, "active_route_identity", None),
            "last_consumed_route_identity": getattr(
                planner, "last_consumed_route_identity", None
            ),
            "online_update_generation": getattr(
                planner, "online_update_generation", None
            ),
        }

    def _official_state(self, frame: int) -> Dict[str, Any]:
        trajectory = getattr(self, "_trajectory", [])
        latest = trajectory[-1] if trajectory and trajectory[-1].get("frame") == frame else {}
        route_completion = None
        for criterion in latest.get("official_criteria", []):
            if criterion.get("criterion") == "RouteCompletionTest":
                route_completion = criterion.get("actual_value")
                break
        return {
            "agent_status": getattr(self, "_status", None),
            "route_completion_percent": route_completion,
            "criteria": latest.get("official_criteria"),
            "safety": latest.get("safety"),
            "evaluator_heartbeat": self._qualification_evaluator_heartbeat,
        }

    def _persist_qualification_row(
        self,
        frame: int,
        evaluator_timestamp: Any,
        control: Any,
        force_move_before: int,
    ) -> None:
        if self._qualification_trace_writer is None:
            return
        wall_now = time.monotonic()
        world = self._world_state(frame)
        planner = self._planner_state()
        official = self._official_state(frame)
        pending = self._qualification_pending_pid or {}
        pid = pending.get("pid") or {}
        force_move_after = int(getattr(self, "force_move", 0))
        force_move_event = None
        if force_move_before != force_move_after or force_move_after > 0:
            force_move_event = {
                "frame_id": int(frame),
                "before": int(force_move_before),
                "after": force_move_after,
                "native_event": True,
                "observer_issued_recovery": False,
            }
            self._qualification_force_move_events.append(force_move_event)
        target_speed = pid.get("target_speed_mps")
        liveness_sample = LivenessSample(
            frame_id=int(frame),
            simulation_time_s=float(world["simulation_time_s"]),
            wall_monotonic_s=wall_now,
            xyz=world["ego_transform"]["location_xyz"],
            ego_speed_mps=float(world["ego_speed_mps"]),
            route_completion_percent=official["route_completion_percent"],
            commanded_target_speed_mps=target_speed,
            throttle=float(control.throttle),
            brake=float(control.brake),
            sensor_heartbeat=self._qualification_sensor_heartbeat,
            model_forward_heartbeat=int(
                getattr(self, "_model_forward_return_count", 0)
            ),
            pid_heartbeat=self._qualification_pid_heartbeat,
            planner_heartbeat=self._qualification_planner_heartbeat,
            evaluator_heartbeat=self._qualification_evaluator_heartbeat,
            force_move_active=force_move_after > 0,
        )
        liveness = self._qualification_liveness.observe(liveness_sample)
        self._qualification_last_liveness = liveness
        sensor_rows = self._qualification_pending_sensor
        sensor_frames = sorted(
            {
                row.get("frame_id")
                for row in (sensor_rows or {}).values()
                if row.get("frame_id") is not None
            }
        )
        row = {
            "schema": TRACE_SCHEMA,
            "run_id": self._v11_config["run_id"],
            "qualification_scope": "NON_FORMAL_DIAGNOSTIC_ONLY",
            "source_frame_id": int(frame),
            "input_identity": {
                "sensor_frames": sensor_frames,
                "sensor_frames_synchronized": sensor_frames == [int(frame)],
                "sensors": sensor_rows,
                "processed_model_input": self._qualification_pending_processed_input,
                "ego_state_input": {
                    "gps": jsonable(
                        None
                        if sensor_rows is None
                        else sensor_rows.get("gps", {}).get("values")
                    ),
                    "imu": jsonable(
                        None
                        if sensor_rows is None
                        else sensor_rows.get("imu", {}).get("values")
                    ),
                    "speed": jsonable(
                        None
                        if sensor_rows is None
                        else sensor_rows.get("speed", {}).get("values")
                    ),
                },
                "navigation_input": {
                    "target_point": jsonable(self.DrivingInput.get("target_point")),
                    "road_option": getattr(
                        self.last_command_tmp, "name", str(self.last_command_tmp)
                    ),
                    "active_route_identity": planner["active_route_identity"],
                },
                "language_hlc_input": {
                    "instruction": self._method_input.get("instruction"),
                    "prompt": getattr(self, "prompt", None),
                    "road_option": getattr(
                        self.last_command_tmp, "name", str(self.last_command_tmp)
                    ),
                },
            },
            "simlingo_output": pending.get("model_output"),
            "native_pid": pid,
            "controls": {
                "native_pid_return": pid.get("native_pid_return"),
                "final_vehicle_control": {
                    "steer": float(control.steer),
                    "throttle": float(control.throttle),
                    "brake": float(control.brake),
                },
                "control_frame_id": int(frame),
                "control_simulation_time_s": world["simulation_time_s"],
                "force_move_event": force_move_event,
            },
            "world_native_execution": {
                **world,
                "planner": planner,
                "official_evaluator": official,
            },
            "clocks": {
                "simulation_time_s": world["simulation_time_s"],
                "simulation_delta_seconds": world["simulation_delta_seconds"],
                "wall_monotonic_s": wall_now,
                "evaluator_supplied_timestamp": jsonable(evaluator_timestamp),
                "clock_domains_must_not_be_subtracted": True,
            },
            "heartbeats": {
                "sensor": self._qualification_sensor_heartbeat,
                "model_forward": int(
                    getattr(self, "_model_forward_return_count", 0)
                ),
                "pid": self._qualification_pid_heartbeat,
                "planner": self._qualification_planner_heartbeat,
                "evaluator": self._qualification_evaluator_heartbeat,
            },
            "liveness": liveness,
            "observer": {
                "trace_errors": self._qualification_trace_errors,
                "added_vla_forwards": 0,
                "added_pid_calls": 0,
                "added_planner_advancements": 0,
                "added_control_writers": 0,
                "control_mutations": 0,
                "scientific_state_mutations": 0,
            },
        }
        persisted = self._qualification_trace_writer.append(row)
        heartbeat = {
            "schema": "driveclarify.rq3-native-liveness-heartbeat.v1",
            "run_id": self._v11_config["run_id"],
            "source_frame_id": int(frame),
            "trace_sequence": persisted["trace_sequence"],
            "simulation_time_s": world["simulation_time_s"],
            "wall_monotonic_s": wall_now,
            "liveness": liveness,
            "heartbeats": row["heartbeats"],
            "diagnostic_only": True,
        }
        heartbeat["receipt_digest"] = canonical_sha256(heartbeat)
        _atomic_json(self._output / "NATIVE_LIVENESS_STATUS.json", heartbeat)

    def run_step(self, input_data, timestamp, sensors=None):
        frame = _input_frame(input_data)
        self._qualification_current_frame = int(frame)
        force_move_before = int(getattr(self, "force_move", 0))
        try:
            # Sole inherited/native run_step; returned VehicleControl is unchanged.
            control = super().run_step(input_data, timestamp, sensors=sensors)
            self._qualification_evaluator_heartbeat += 1
            if self.initialized:
                self._persist_qualification_row(
                    frame, timestamp, control, force_move_before
                )
            return control
        except Exception as error:
            self._qualification_trace_errors += 1
            if self._qualification_trace_writer is not None:
                try:
                    self._qualification_trace_writer.append(
                        {
                            "schema": TRACE_SCHEMA,
                            "run_id": self._v11_config["run_id"],
                            "qualification_scope": "NON_FORMAL_DIAGNOSTIC_ONLY",
                            "source_frame_id": int(frame),
                            "terminal_exception": repr(error),
                            "clocks": {"wall_monotonic_s": time.monotonic()},
                            "observer": {
                                "added_vla_forwards": 0,
                                "added_pid_calls": 0,
                                "added_planner_advancements": 0,
                                "added_control_writers": 0,
                                "control_mutations": 0,
                                "scientific_state_mutations": 0,
                            },
                        }
                    )
                except Exception:
                    pass
            raise

    def _write_terminal_classification(self, results=None) -> None:
        if not hasattr(self, "_output"):
            return
        liveness = self._qualification_last_liveness or {
            "classification": "UNKNOWN"
        }
        value = {
            "schema": "driveclarify.rq3-native-termination.v1",
            "run_id": self._v11_config["run_id"],
            "qualification_scope": "NON_FORMAL_DIAGNOSTIC_ONLY",
            "reason_code": liveness.get("classification", "UNKNOWN"),
            "first_no_progress_frame": liveness.get("first_no_progress_frame"),
            "no_progress_duration_simulation_s": liveness.get(
                "no_progress_duration_simulation_s"
            ),
            "no_progress_duration_wall_monotonic_s": liveness.get(
                "no_progress_duration_wall_monotonic_s"
            ),
            "model_alive": bool(
                (liveness.get("heartbeat_deltas") or {}).get("model", 0) > 0
            ),
            "pid_alive": bool(
                (liveness.get("heartbeat_deltas") or {}).get("pid", 0) > 0
            ),
            "sensor_alive": bool(
                (liveness.get("heartbeat_deltas") or {}).get("sensor", 0) > 0
            ),
            "planner_alive": bool(
                (liveness.get("heartbeat_deltas") or {}).get("planner", 0) > 0
            ),
            "evaluator_alive": bool(
                (liveness.get("heartbeat_deltas") or {}).get("evaluator", 0) > 0
            ),
            "force_move_events": self._qualification_force_move_events,
            "trace_rows": (
                0
                if self._qualification_trace_writer is None
                else self._qualification_trace_writer.sequence
            ),
            "observer_trace_errors": self._qualification_trace_errors,
            "evaluator_destroy_results_present": results is not None,
            "diagnostic_only": True,
            "issued_vehicle_recovery": False,
            "issued_control": False,
        }
        value["receipt_digest"] = canonical_sha256(value)
        _atomic_json(self._output / "TERMINATION_CLASSIFICATION.json", value)

    def destroy(self, results=None):
        try:
            self._write_terminal_classification(results)
        finally:
            if self._qualification_trace_writer is not None:
                self._qualification_trace_writer.close()
            super().destroy(results)


__all__ = ["DriveClarifyNativeQualificationAgent", "get_entry_point"]
