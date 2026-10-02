"""DriveClarify CP0 runtime probe hook (SimLingo side, minimal + default-OFF).

This is the ONLY new SimLingo file for CP0 runtime smoke. It keeps `agent_simlingo.py`
edits to a handful of one-line call sites; all extraction / fail-open / equivalence
bookkeeping lives here so the baseline inference/PID/route-planner path is untouched.

Contract (must hold even if anything here breaks):
- Default OFF. `DRIVECLARIFY_PROBE_ENABLED` must be truthy ("1"/"true") to enable.
  When OFF, `build_runtime_probe` returns an inert object whose every method is a
  no-op; no logger thread, no file, no tensor CPU sync, no extra getters.
- The ordinary probe is read-only.  A separate DRIVECLARIFY_SHADOW_V0 opt-in may
  execute bounded candidate-only model forwards, but still never calls control_pid,
  _route_planner.run_step, or apply_control and has no control-returning API.
- Fail-open. Every method is wrapped so an exception is counted, never propagated
  into the baseline control path. Import failures degrade to the inert probe.

Enable example (set before launching the leaderboard evaluator):
    export DRIVECLARIFY_PROBE_ENABLED=1
    export DRIVECLARIFY_PROBE_OUTPUT=/path/to/probe.jsonl
    export DRIVECLARIFY_PROBE_RUN_ID=cp0_enabled
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

_DEFAULT_DC_REPO = "/home/buaa/wrh/DriveClarify"
_PREPROCESS_REVISION = "simlingo_agent_v0"

# CP1 world-state logging (opt-in, read-only, separate file from CP0 probe.jsonl).
# Enabled only when DRIVECLARIFY_WORLDSTATE_OUTPUT is set AND the probe is enabled.
# All CARLA world reads happen at P0 (after baseline tick()), never mutate anything,
# never call world.tick/apply_control/route_planner.run_step/spawn/destroy.


def _truthy(val: str | None) -> bool:
    return str(val).strip().lower() in ("1", "true", "yes", "on") if val is not None else False


class _InertRuntimeProbe:
    """No-op probe used when disabled or when the harness cannot be imported."""

    enabled = False

    def __init__(self, reason: str = "DISABLED") -> None:
        self.reason = reason
        self.harness_errors = 0

    def on_tick(self, *a: Any, **k: Any) -> None:
        return None

    def on_model_output(self, *a: Any, **k: Any) -> None:
        return None

    def prepare_model_input(self, model_input: Any) -> Any:
        return model_input

    def select_connector_target_window(
        self,
        active_route: Any,
        ego_xyz_m: Any,
        baseline_targets: tuple[Any, Any],
        *a: Any,
        **k: Any,
    ) -> tuple[Any, Any]:
        del active_route, ego_xyz_m, a, k
        return baseline_targets

    def select_plan_source(self, baseline_route: Any, baseline_speed: Any, *a: Any, **k: Any) -> tuple[Any, Any]:
        return baseline_route, baseline_speed

    def on_pid_invocation(self, *a: Any, **k: Any) -> None:
        return None

    def on_candidate_set(self, *a: Any, **k: Any) -> None:
        return None

    def on_baseline_control(self, *a: Any, **k: Any) -> None:
        return None

    def on_m2b_decision(self, *a: Any, **k: Any) -> None:
        return None

    def commit(self, *a: Any, **k: Any) -> None:
        return None

    def close(self, *a: Any, **k: Any) -> None:
        return None

    def audit_summary(self) -> dict[str, Any]:
        return {"enabled": False, "reason": self.reason}

    def should_end_scientific_horizon(self, simulation_elapsed_s: float) -> bool:
        del simulation_elapsed_s
        return False

    def record_execution_terminal(self, state: str, simulation_elapsed_s: float) -> None:
        del state, simulation_elapsed_s
        return None

    def scientific_execution_terminal(self) -> None:
        return None


class _RuntimeProbe:
    """Enabled probe: bridges baseline P0/P1/P2 observations to the DriveClarify harness.

    Also accumulates a per-tick baseline-equivalence audit (control object identity and
    values before/after the read-only observation, plus probe-induced call counts, which
    are structurally zero) that is written to a sidecar JSON at close(). The JSONL schema
    itself is unchanged so the frozen offline verifier stays valid.
    """

    enabled = True

    def __init__(
        self,
        harness_probe: Any,
        run_id: str,
        equivalence_path: str | None,
        agent: Any = None,
        world_state_writer: Any = None,
        world_state_mod: Any = None,
        roi_m: float = 50.0,
        actor_max: int = 32,
        cp3b_mod: Any = None,
        cp3b_img_dir: str | None = None,
        cp3b_identity: dict[str, Any] | None = None,
        vla_debug_recorder: Any = None,
        live_shadow_runtime: Any = None,
        scientific_owner_runtime: Any = None,
        live_runtime_fail_closed: bool = False,
    ) -> None:
        self._probe = harness_probe
        self._run_id = run_id
        self._equivalence_path = equivalence_path
        self._route_binding_runtime_path = os.environ.get(
            "DRIVECLARIFY_ROUTE_BINDING_RUNTIME_RECEIPT"
        )
        self._route_binding_runtime_written = False
        self.harness_errors = 0
        # CP1 world-state (read-only, optional; separate file)
        self._agent = agent
        self._ws_writer = world_state_writer
        self._ws = world_state_mod
        self._roi_m = roi_m
        self._actor_max = actor_max
        self._ws_pending: dict[str, Any] | None = None
        self._ws_written = 0
        self.world_state_errors = 0
        # CP3B interface probe (read-only, default-OFF; additive to CP1 record).
        # Active only when cp3b_mod AND cp3b_img_dir are provided.
        self._cp3b = cp3b_mod
        self._cp3b_img_dir = cp3b_img_dir
        self._cp3b_identity = cp3b_identity or {}
        self._cp3b_input_data: Any = None  # stashed at P0, RGB saved at commit()
        self._cp3b_seq = 0
        self._cp3b_images_written = 0
        self.cp3b_errors = 0
        # VLA research-debug recorder (optional/default-OFF).  It only receives the
        # exact objects already produced by this tick and materializes copies after
        # the baseline forward + PID.  No second forward/PID/planner call exists.
        self._vla_debug = vla_debug_recorder
        self.vla_debug_errors = 0
        # Bounded live candidate shadow (optional/default-OFF).  It owns no control
        # object and all of its methods are fail-open with respect to baseline driving.
        self._live_shadow = live_shadow_runtime
        # RQ2-T 2A 独立科学控制 owner。_live_shadow 继续只产出证据；
        # owner 启用时，其任何 plan 选择都不进入控制路径。
        self._scientific_owner = scientific_owner_runtime
        self._live_runtime_fail_closed = bool(live_runtime_fail_closed)
        self.live_shadow_errors = 0
        # per-tick state
        self._frame: Any = None
        self._candidate_set: dict[str, Any] | None = None
        self._m2b_decision: dict[str, Any] | None = None
        self._obs_id: str | None = None
        self._fwd_count = 0
        self._pid_count = 0
        # accumulated audit
        self._ticks_committed = 0
        self._control_identity_preserved_ticks = 0
        self._control_values_preserved_ticks = 0
        self._identity_violations: list[dict[str, Any]] = []
        self._forward_counts: list[int] = []
        self._pid_counts: list[int] = []
        # probe-induced (structural): the hook calls none of these
        self._probe_induced_model_calls = 0
        self._probe_induced_pid_calls = 0
        self._probe_induced_route_planner_steps = 0

    # ---- P0 ----
    def on_tick(self, input_data: Any, tick_data: Any, timestamp: Any) -> None:
        try:
            self._fwd_count = 0
            self._pid_count = 0
            self._candidate_set = None
            self._m2b_decision = None
            frame = self._extract_frame(input_data)
            self._frame = frame
            self._obs_id = f"{self._run_id}:{frame}:{_PREPROCESS_REVISION}"
            sensor_frames = self._extract_sensor_frames(input_data)
            p0 = {
                "observation_id": self._obs_id,
                "carla_snapshot_frame": frame,
                "game_time_frame": frame,
                "sensor_frames": sensor_frames,
                # simulation clock: agent `timestamp` is GameTime seconds (sim domain).
                "sim_elapsed_s": _as_float(timestamp),
                "game_time_s": _as_float(timestamp),
                "observation": {"speed": _read_scalar(tick_data, "speed")},
            }
            self._probe.observe_tick(p0)
        except Exception:  # noqa: BLE001 - never propagate to baseline
            self.harness_errors += 1
        if self._vla_debug is not None:
            try:
                self._vla_debug.on_tick(
                    input_data, tick_data, timestamp, self._frame, self._obs_id
                )
            except Exception:  # noqa: BLE001 - visualization never affects baseline
                self.vla_debug_errors += 1
        if self._live_shadow is not None:
            try:
                self._live_shadow.on_tick(
                    input_data,
                    tick_data,
                    timestamp,
                    self._frame,
                    self._obs_id,
                )
            except Exception:  # noqa: BLE001 - shadow never breaks baseline
                self.live_shadow_errors += 1
                if self._live_runtime_fail_closed:
                    raise
        if self._scientific_owner is not None:
            self._scientific_owner.on_tick(
                input_data,
                tick_data,
                timestamp,
                self._frame,
                self._obs_id,
            )
        # CP1 world-state capture (read-only). Fully isolated fail-open; a failure here
        # increments world_state_errors and NEVER affects the baseline or the CP0 record.
        if self._ws_writer is not None and self._ws is not None:
            try:
                self._capture_world_state(input_data, timestamp)
            except Exception:  # noqa: BLE001
                self.world_state_errors += 1

    def _capture_world_state(self, input_data: Any, timestamp: Any) -> None:
        rec: dict[str, Any] = {
            "schema_version": self._ws.WORLD_STATE_SCHEMA_VERSION,
            "run_id": self._run_id,
            "observation_id": self._obs_id,
            "carla_snapshot_frame": self._frame,
        }
        hero = None
        world = None
        try:
            from srunner.scenariomanager.carla_data_provider import CarlaDataProvider
            hero = CarlaDataProvider.get_hero_actor()
            world = CarlaDataProvider.get_world()
        except Exception as exc:  # noqa: BLE001
            rec["world_access_status"] = f"exception:{type(exc).__name__}"
        # independent frame + clock sources (T1/T2): snapshot frame/elapsed/delta and
        # GameTime.get_frame()/get_time(), captured separately from agent `timestamp`.
        try:
            from srunner.scenariomanager.timer import GameTime
            rec["gametime_frame"] = int(GameTime.get_frame())
            rec["gametime_seconds"] = float(GameTime.get_time())
        except Exception as exc:  # noqa: BLE001
            rec["gametime_status"] = f"exception:{type(exc).__name__}"
        try:
            if world is not None:
                snap = world.get_snapshot()
                ts = snap.timestamp
                rec["snapshot_frame"] = int(ts.frame)
                rec["snapshot_elapsed_seconds"] = float(ts.elapsed_seconds)
                rec["snapshot_delta_seconds"] = float(ts.delta_seconds)
        except Exception as exc:  # noqa: BLE001
            rec["snapshot_status"] = f"exception:{type(exc).__name__}"
        rec["agent_timestamp_seconds"] = _as_float(timestamp)
        rec["probe_read_monotonic_s"] = time.monotonic()
        # ego / camera / actors / lights / map (all read-only)
        rec["ego"] = self._ws.extract_ego(hero)
        rec["camera"] = self._capture_camera()
        rec["actors"] = self._ws.extract_actors(world, hero, self._roi_m, self._actor_max)
        rec["traffic_lights"] = self._ws.extract_traffic_lights(world, hero, self._roi_m, self._actor_max)
        rec["map_waypoint"] = self._ws.extract_map_waypoint(world, hero)
        rec["route_context"] = self._capture_route_context()
        rec["input_sensor_frames"] = self._extract_sensor_frames(input_data)
        # CP3B (read-only, additive): landmarks (world coords) + identity binding now;
        # RGB frame is saved at commit() to keep the pre-forward path untouched.
        if self._cp3b is not None and self._cp3b_img_dir is not None:
            try:
                cp3b: dict[str, Any] = {
                    "probe_version": self._cp3b.CP3B_PROBE_VERSION,
                    "record_seq": self._cp3b_seq,
                    "route_id": os.environ.get("DRIVECLARIFY_CP3B_ROUTE_ID"),
                    "episode_id": os.environ.get("DRIVECLARIFY_CP3B_EPISODE_ID"),
                    "identity_hashes": self._cp3b_identity,
                    "landmarks": self._cp3b.extract_landmarks(
                        world, hero, self._roi_m, self._actor_max
                    ),
                }
                rec["cp3b"] = cp3b
                self._cp3b_input_data = input_data  # read-only reference; RGB saved later
            except Exception:  # noqa: BLE001
                self.cp3b_errors += 1
                self._cp3b_input_data = None
        self._ws_pending = rec

    def _capture_camera(self) -> dict[str, Any]:
        try:
            from team_code.simlingo_utils import get_camera_intrinsics, get_camera_extrinsics
        except Exception:  # noqa: BLE001
            try:
                from simlingo_utils import get_camera_intrinsics, get_camera_extrinsics  # type: ignore
            except Exception as exc:  # noqa: BLE001
                return {"status": f"exception:{type(exc).__name__}", "reason": "CAMERA_UTILS_IMPORT_FAILED"}
        w, h, fov = 1024, 512, 110.0
        try:
            cfg = getattr(self._agent, "config", None)
            if cfg is not None:
                w = int(getattr(cfg, "camera_width", w))
                h = int(getattr(cfg, "camera_height", h))
        except Exception:  # noqa: BLE001
            pass
        try:
            k = get_camera_intrinsics(w, h, fov)
            import numpy as _np
            k = _np.asarray(k)
            e = _np.asarray(get_camera_extrinsics())
            return {
                "status": "success",
                "width": w, "height": h, "fov_deg": fov,
                "K_3x3": k.reshape(3, 3).astype(float).tolist(),
                "extrinsics_4x4": e.reshape(4, 4).astype(float).tolist(),
                "extrinsics_direction_label": "UNRESOLVED_REQUIRES_F6",
            }
        except Exception as exc:  # noqa: BLE001
            return {"status": f"exception:{type(exc).__name__}", "reason": "CAMERA_CALC_FAILED"}

    def _capture_route_context(self) -> dict[str, Any]:
        out: dict[str, Any] = {"status": "success"}
        try:
            rp = getattr(self._agent, "_route_planner", None)
            if rp is None:
                return {"status": "empty", "reason": "NO_ROUTE_PLANNER"}
            route = getattr(rp, "route", None)
            if route is not None:
                # READ-ONLY copy of up to first 5 front items; do NOT call run_step (mutates deque).
                front = []
                for i, item in enumerate(route):
                    if i >= 5:
                        break
                    try:
                        loc = item[0]
                        xyz = self._route_xyz(loc)
                        front.append({
                            "planner_xyz": xyz,
                            "world_xyz": self._planner_to_world_xyz(xyz),
                            "road_option": self._road_option(item[1]),
                        })
                    except Exception:  # noqa: BLE001
                        pass
                out["front_items"] = front
                try:
                    out["route_len_remaining"] = len(route)
                except Exception:  # noqa: BLE001
                    out["route_len_remaining"] = None
                if len(front) > 1:
                    out["navigation_target_consumed"] = front[1]
                elif front:
                    out["navigation_target_consumed"] = front[0]
            if (
                self._route_binding_runtime_path
                and not self._route_binding_runtime_written
            ):
                self._write_route_binding_runtime_receipt(rp, out)
        except Exception as exc:  # noqa: BLE001
            return {"status": f"exception:{type(exc).__name__}"}
        return out

    @staticmethod
    def _road_option(command: Any) -> dict[str, Any]:
        return {
            "name": None if getattr(command, "name", None) is None else str(command.name),
            "value": getattr(command, "value", None),
        }

    @staticmethod
    def _route_xyz(location: Any) -> list[float]:
        if hasattr(location, "location"):
            location = location.location
        if all(hasattr(location, axis) for axis in ("x", "y", "z")):
            return [float(location.x), float(location.y), float(location.z)]
        return [float(location[index]) for index in range(3)]

    def _planner_to_world_xyz(self, xyz: list[float]) -> list[float] | None:
        translation = getattr(
            self._agent, "_world_to_route_planner_translation_xyz_m", None
        )
        if translation is None:
            return None
        try:
            return [float(xyz[index] - translation[index]) for index in range(3)]
        except Exception:  # noqa: BLE001
            return None

    def _serialize_route(self, route: Any, planner_coordinates: bool = False) -> list[dict[str, Any]]:
        rows = []
        if route is None:
            return rows
        for index, item in enumerate(route):
            location, command = item
            xyz = self._route_xyz(location)
            row = {
                "index": index,
                "road_option": self._road_option(command),
            }
            if planner_coordinates:
                row["planner_xyz"] = xyz
                row["world_xyz"] = self._planner_to_world_xyz(xyz)
            else:
                row["world_xyz"] = xyz
            rows.append(row)
        return rows

    def _serialize_gps_route(self, route: Any) -> list[dict[str, Any]]:
        rows = []
        if route is None:
            return rows
        for index, item in enumerate(route):
            position, command = item
            rows.append({
                "index": index,
                "gps": {
                    "lat": float(position["lat"]),
                    "lon": float(position["lon"]),
                    "z": float(position["z"]),
                },
                "road_option": self._road_option(command),
            })
        return rows

    def _write_route_binding_runtime_receipt(self, route_planner: Any, route_context: dict[str, Any]) -> None:
        path = str(self._route_binding_runtime_path)
        translation = getattr(
            self._agent, "_world_to_route_planner_translation_xyz_m", None
        )
        value = {
            "schema_version": "driveclarify.agent_route_binding_runtime.v2",
            "run_id": self._run_id,
            "source_observation_id": self._obs_id,
            "source_frame_id": self._frame,
            "agent_dense_global_plan_world": self._serialize_route(
                getattr(self._agent, "org_dense_route_world_coord", None)
            ),
            "agent_downsampled_global_plan_world": self._serialize_route(
                getattr(self._agent, "_global_plan_world_coord", None)
            ),
            "agent_downsampled_global_plan_gps": self._serialize_gps_route(
                getattr(self._agent, "_global_plan", None)
            ),
            "agent_route_planner_installed_route": self._serialize_route(
                getattr(route_planner, "route", None), planner_coordinates=True
            ),
            "world_to_route_planner_translation_xyz_m": (
                None if translation is None
                else [float(translation[index]) for index in range(3)]
            ),
            "actual_navigation_target_consumed": route_context.get(
                "navigation_target_consumed"
            ),
            "read_only_capture": True,
            "probe_induced_route_planner_steps": 0,
            "probe_induced_model_calls": 0,
            "probe_induced_pid_calls": 0,
        }
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        temporary = os.path.join(directory, "." + os.path.basename(path) + ".tmp")
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
        self._route_binding_runtime_written = True

    # ---- P1 ----
    def prepare_model_input(self, model_input: Any) -> Any:
        """Optionally bind V2 navigation to the one existing normal forward."""

        if self._live_shadow is None:
            return model_input
        try:
            prepared = self._live_shadow.prepare_model_input(model_input)
            if prepared is None:
                raise RuntimeError("METHOD_V2_PREPARED_MODEL_INPUT_MISSING")
            return prepared
        except Exception:  # noqa: BLE001 - normal baseline input remains fail-open
            self.live_shadow_errors += 1
            if self._live_runtime_fail_closed:
                raise
            return model_input

    def select_connector_target_window(
        self,
        active_route: Any,
        ego_xyz_m: Any,
        baseline_targets: tuple[Any, Any],
        *,
        input_data: Any = None,
    ) -> tuple[Any, Any]:
        """Apply an enabled frozen target owner; contract failures propagate."""

        if self._live_shadow is None:
            return baseline_targets
        if self._scientific_owner is not None:
            return baseline_targets
        selector = getattr(
            self._live_shadow, "select_connector_target_window", None
        )
        if not callable(selector):
            return baseline_targets
        frame = self._extract_frame(input_data)
        selected = selector(
            active_route,
            ego_xyz_m,
            baseline_targets,
            frame_id=frame,
        )
        if not isinstance(selected, tuple) or len(selected) != 2:
            raise RuntimeError("METHOD_V3_CONNECTOR_TARGET_WINDOW_RESULT_INVALID")
        return selected

    def on_model_output(self, pred_route: Any, pred_speed_wps: Any, t0: float, t1: float) -> None:
        try:
            self._fwd_count += 1
            p1 = {
                "pred_route": pred_route,           # tensor object; harness does read-only summary
                "pred_speed_wps": pred_speed_wps,
                "source_observation_id": self._obs_id,
                "generated_from_same_forward": True,
                "used_by_baseline_pid": True,
                "model_start_monotonic_s": float(t0),
                "model_end_monotonic_s": float(t1),
            }
            self._probe.observe_model_output(p1)
        except Exception:  # noqa: BLE001
            self.harness_errors += 1
        # CP1: record the SAME forward's route/speed values into the world-state record too,
        # so per-tick ego displacement (F3/F5 unit) can be joined offline. Read-only copy.
        if self._ws_pending is not None:
            try:
                self._ws_pending["model_start_monotonic_s"] = float(t0)
                self._ws_pending["model_end_monotonic_s"] = float(t1)
                self._ws_pending["pred_route_values"] = _tensor_to_list(pred_route)
                self._ws_pending["pred_speed_wps_values"] = _tensor_to_list(pred_speed_wps)
            except Exception:  # noqa: BLE001
                self.world_state_errors += 1
            # CP3B: bind per-tick forward counter + raw tensor shapes (MUST be 1 forward).
            if isinstance(self._ws_pending.get("cp3b"), dict):
                try:
                    c = self._ws_pending["cp3b"]
                    c["forward_invocation_counter"] = self._fwd_count
                    pr = self._ws_pending.get("pred_route_values")
                    ps = self._ws_pending.get("pred_speed_wps_values")
                    c["tensor_shape_route"] = _shape_of(pr)
                    c["tensor_shape_speed"] = _shape_of(ps)
                    c["source_observation_id"] = self._obs_id
                except Exception:  # noqa: BLE001
                    self.cp3b_errors += 1
        if self._vla_debug is not None:
            try:
                self._vla_debug.on_model_output(
                    pred_route, pred_speed_wps, float(t0), float(t1)
                )
            except Exception:  # noqa: BLE001
                self.vla_debug_errors += 1
        if self._live_shadow is not None:
            try:
                self._live_shadow.on_model_output(
                    pred_route, pred_speed_wps, float(t0), float(t1)
                )
            except Exception:  # noqa: BLE001 - shadow never breaks baseline
                self.live_shadow_errors += 1
                if self._live_runtime_fail_closed:
                    raise
        if self._scientific_owner is not None:
            self._scientific_owner.on_model_output(
                pred_route, pred_speed_wps, float(t0), float(t1)
            )

    def select_plan_source(
        self,
        baseline_route: Any,
        baseline_speed: Any,
        current_monotonic: float,
    ) -> tuple[Any, Any]:
        """Fail-open, pre-PID selection of exactly one already-computed plan."""

        if self._scientific_owner is not None:
            selected = self._scientific_owner.select_plan_source(
                baseline_route,
                baseline_speed,
                float(current_monotonic),
            )
            if not isinstance(selected, tuple) or len(selected) != 2:
                raise RuntimeError("RQ2_T_2A_OWNER_PLAN_SELECTION_RETURN_INVALID")
            return selected
        if self._live_shadow is None:
            return baseline_route, baseline_speed
        try:
            selected = self._live_shadow.select_plan_source(
                baseline_route,
                baseline_speed,
                float(current_monotonic),
            )
            if not isinstance(selected, tuple) or len(selected) != 2:
                raise RuntimeError("LIMITED_ACT_PLAN_SELECTION_RETURN_INVALID")
            return selected
        except Exception:  # noqa: BLE001 - baseline plan remains the fail-open owner
            self.live_shadow_errors += 1
            if self._live_runtime_fail_closed:
                raise
            return baseline_route, baseline_speed

    def on_pid_invocation(self, current_monotonic: float) -> None:
        """Observe the one existing PID call site; never invokes a controller."""

        if self._live_shadow is not None:
            try:
                self._live_shadow.on_pid_invocation(float(current_monotonic))
            except Exception:  # noqa: BLE001 - observation never blocks baseline PID
                self.live_shadow_errors += 1
                if self._live_runtime_fail_closed:
                    raise
        if self._scientific_owner is not None:
            self._scientific_owner.on_pid_invocation(float(current_monotonic))

    # ---- P1.5 ----
    def on_candidate_set(self, candidates: Any, t: float | None = None) -> None:
        try:
            payload: dict[str, Any] = {
                "source_observation_id": self._obs_id,
                "source": "route_planner",
                "timestamp_monotonic_s": float(t if t is not None else time.monotonic()),
                "candidates": _candidate_set_summary(candidates),
            }
            self._candidate_set = payload
            self._probe.observe_candidate_set(payload)
            if self._ws_pending is not None:
                self._ws_pending["candidate_set"] = payload
        except Exception:  # noqa: BLE001
            self.harness_errors += 1

    def on_m2b_decision(self, control: Any, gt_velocity: Any, t: float | None = None) -> None:
        try:
            decision: dict[str, Any] = {
                "source_observation_id": self._obs_id,
                "decision_source": "baseline_pid",
                "decision": "selected_baseline_control",
                "timestamp_monotonic_s": float(t if t is not None else time.monotonic()),
                "control": _control_values(control),
            }
            gtv = _first_scalar(gt_velocity)
            if gtv is not None:
                decision["gt_velocity"] = gtv
            if self._candidate_set is not None:
                candidates = self._candidate_set.get("candidates")
                if isinstance(candidates, dict):
                    decision["candidate_count"] = candidates.get("count")
                    items = candidates.get("items")
                    if isinstance(items, list) and items:
                        decision["candidate_id"] = 0
                        decision["selected_candidate"] = items[0]
            self._m2b_decision = decision
            self._probe.observe_final_m2b_decision(decision)
            if self._ws_pending is not None:
                self._ws_pending["m2b_decision"] = decision
        except Exception:  # noqa: BLE001
            self.harness_errors += 1

    # ---- P2 ----
    def on_baseline_control(self, control: Any, gt_velocity: Any, t2: float) -> None:
        try:
            self._pid_count += 1
            id_before = id(control)
            values_before = _control_values(control)
            if self._ws_pending is not None:
                try:
                    self._ws_pending["baseline_control"] = dict(values_before)
                    self._ws_pending["baseline_control"]["gt_velocity"] = _first_scalar(gt_velocity)
                    self._ws_pending["control_ready_monotonic_s"] = float(t2)
                except Exception:  # noqa: BLE001
                    self.world_state_errors += 1
            # ---- read-only observe ----
            p2 = dict(values_before)
            p2["control_ready_monotonic_s"] = float(t2)
            gtv = _first_scalar(gt_velocity)
            if gtv is not None:
                p2["gt_velocity"] = gtv
            self._probe.observe_baseline_control(p2)
            # ---- post-observation snapshot for equivalence ----
            id_after = id(control)
            values_after = _control_values(control)
            if id_before == id_after:
                self._control_identity_preserved_ticks += 1
            else:
                self._identity_violations.append(
                    {"frame": self._frame, "id_before": id_before, "id_after": id_after}
                )
            if values_before == values_after:
                self._control_values_preserved_ticks += 1
        except Exception:  # noqa: BLE001
            self.harness_errors += 1
        if self._vla_debug is not None:
            try:
                self._vla_debug.on_control(control, gt_velocity, float(t2))
            except Exception:  # noqa: BLE001
                self.vla_debug_errors += 1
        if self._live_shadow is not None:
            try:
                self._live_shadow.on_control(control, gt_velocity, float(t2))
            except Exception:  # noqa: BLE001 - baseline control is still returned
                self.live_shadow_errors += 1
                if self._live_runtime_fail_closed:
                    raise
        if self._scientific_owner is not None:
            self._scientific_owner.on_control(control, gt_velocity, float(t2))

    def commit(self) -> None:
        try:
            self._probe.commit_tick(clock_now_monotonic=time.monotonic())
            self._ticks_committed += 1
            self._forward_counts.append(self._fwd_count)
            self._pid_counts.append(self._pid_count)
        except Exception:  # noqa: BLE001
            self.harness_errors += 1
        # CP3B: save the RGB frame now (after control is produced) + attach metadata.
        if (
            self._cp3b is not None
            and self._cp3b_img_dir is not None
            and isinstance(self._ws_pending, dict)
            and isinstance(self._ws_pending.get("cp3b"), dict)
            and self._cp3b_input_data is not None
        ):
            try:
                img_meta = self._cp3b.save_rgb_frame(
                    self._cp3b_input_data,
                    self._cp3b_img_dir,
                    self._obs_id,
                    self._cp3b_seq,
                    sensor_id="rgb_0",
                )
                self._ws_pending["cp3b"]["rgb"] = img_meta
                if isinstance(img_meta, dict) and img_meta.get("image_sha256"):
                    self._cp3b_images_written += 1
            except Exception:  # noqa: BLE001
                self.cp3b_errors += 1
            finally:
                self._cp3b_input_data = None
        # CP1: flush the world-state record for this tick (separate file, fail-open).
        if self._ws_writer is not None and self._ws_pending is not None:
            try:
                self._ws_writer.write(self._ws_pending)
                self._ws_written += 1
            except Exception:  # noqa: BLE001
                self.world_state_errors += 1
            finally:
                self._ws_pending = None
        if self._cp3b is not None:
            self._cp3b_seq += 1
        # This call is intentionally last: the baseline model forward and PID have
        # already completed.  The recorder only copies their existing values and
        # publishes to a bounded background writer.
        if self._vla_debug is not None:
            try:
                self._vla_debug.commit()
            except Exception:  # noqa: BLE001
                self.vla_debug_errors += 1
        if self._live_shadow is not None:
            try:
                self._live_shadow.commit()
            except Exception:  # noqa: BLE001
                self.live_shadow_errors += 1
                if self._live_runtime_fail_closed:
                    raise
        if self._scientific_owner is not None:
            self._scientific_owner.commit()

    def close(self) -> None:
        try:
            self._probe.flush()
            self._probe.close()
        except Exception:  # noqa: BLE001
            self.harness_errors += 1
        if self._ws_writer is not None:
            try:
                self._ws_writer.close()
            except Exception:  # noqa: BLE001
                self.world_state_errors += 1
        if self._vla_debug is not None:
            try:
                self._vla_debug.close()
            except Exception:  # noqa: BLE001
                self.vla_debug_errors += 1
        if self._live_shadow is not None:
            try:
                self._live_shadow.close()
            except Exception:  # noqa: BLE001
                self.live_shadow_errors += 1
                if self._live_runtime_fail_closed:
                    raise
        if self._scientific_owner is not None:
            self._scientific_owner.close()
        try:
            if self._equivalence_path:
                with open(self._equivalence_path, "w", encoding="utf-8") as fh:
                    json.dump(self.audit_summary(), fh, ensure_ascii=False, indent=2, sort_keys=True)
        except Exception:  # noqa: BLE001
            self.harness_errors += 1

    def audit_summary(self) -> dict[str, Any]:
        counters = {}
        vla_debug_summary = None
        live_shadow_summary = None
        scientific_owner_summary = None
        try:
            counters = self._probe.counters()
        except Exception:  # noqa: BLE001
            self.harness_errors += 1
        if self._vla_debug is not None:
            try:
                vla_debug_summary = self._vla_debug.summary()
            except Exception:  # noqa: BLE001
                self.vla_debug_errors += 1
        if self._live_shadow is not None:
            try:
                live_shadow_summary = self._live_shadow.summary()
            except Exception:  # noqa: BLE001
                self.live_shadow_errors += 1
        if self._scientific_owner is not None:
            scientific_owner_summary = self._scientific_owner.summary()
        return {
            "enabled": True,
            "run_id": self._run_id,
            "ticks_committed": self._ticks_committed,
            "control_identity_preserved_ticks": self._control_identity_preserved_ticks,
            "control_values_preserved_ticks": self._control_values_preserved_ticks,
            "identity_violations": self._identity_violations,
            "forward_call_count_per_tick": self._forward_counts,
            "pid_call_count_per_tick": self._pid_counts,
            "probe_induced_model_calls": self._probe_induced_model_calls,
            "probe_induced_pid_calls": self._probe_induced_pid_calls,
            "probe_induced_route_planner_steps": self._probe_induced_route_planner_steps,
            "harness_counters": counters,
            "harness_errors": self.harness_errors,
            "world_state_written": self._ws_written,
            "world_state_errors": self.world_state_errors,
            "cp3b_enabled": self._cp3b is not None and self._cp3b_img_dir is not None,
            "cp3b_images_written": self._cp3b_images_written,
            "cp3b_errors": self.cp3b_errors,
            "vla_debug_enabled": self._vla_debug is not None,
            "vla_debug_errors": self.vla_debug_errors,
            "vla_debug_summary": vla_debug_summary,
            "live_shadow_enabled": bool(
                self._live_shadow is not None
                and getattr(self._live_shadow, "enabled", False)
            ),
            "live_shadow_errors": self.live_shadow_errors,
            "live_runtime_fail_closed": self._live_runtime_fail_closed,
            "live_shadow_summary": live_shadow_summary,
            "scientific_owner_enabled": self._scientific_owner is not None,
            "scientific_owner_plan_authority_exclusive": (
                self._scientific_owner is not None
            ),
            "evidence_runtime_plan_selection_bypassed": (
                self._scientific_owner is not None and self._live_shadow is not None
            ),
            "scientific_owner_summary": scientific_owner_summary,
            "notes": (
                "forward/pid counts are observed at the single existing call sites; "
                "probe_induced_* are structurally zero (hook re-executes nothing). "
                "route_planner steps are performed by baseline tick(), not observable here; "
                "probe adds none."
            ),
        }

    def should_end_scientific_horizon(self, simulation_elapsed_s: float) -> bool:
        if self._scientific_owner is None:
            return False
        callback = getattr(self._scientific_owner, "should_end_scientific_horizon", None)
        if not callable(callback):
            return False
        return bool(callback(float(simulation_elapsed_s)))

    def record_execution_terminal(self, state: str, simulation_elapsed_s: float) -> None:
        if self._scientific_owner is None:
            return
        callback = getattr(self._scientific_owner, "record_execution_terminal", None)
        if callable(callback):
            callback(state=state, simulation_elapsed_s=float(simulation_elapsed_s))

    def scientific_execution_terminal(self) -> Any:
        """Expose the owner's simulator-domain terminal event to the observer."""

        if self._scientific_owner is None:
            return None
        summary = self._scientific_owner.summary()
        terminal = summary.get("terminal_event")
        return dict(terminal) if isinstance(terminal, dict) else None

    # ---- read-only extraction helpers ----
    @staticmethod
    def _extract_frame(input_data: Any) -> Any:
        try:
            for key in ("rgb_0", "rgb", "imu", "gps", "speed"):
                if isinstance(input_data, dict) and key in input_data:
                    entry = input_data[key]
                    if isinstance(entry, (list, tuple)) and len(entry) >= 1:
                        return int(entry[0])
        except Exception:  # noqa: BLE001
            return None
        return None

    @staticmethod
    def _extract_sensor_frames(input_data: Any) -> dict[str, Any]:
        frames: dict[str, Any] = {}
        try:
            if isinstance(input_data, dict):
                for key, entry in input_data.items():
                    if isinstance(entry, (list, tuple)) and len(entry) >= 1:
                        try:
                            frames[str(key)] = int(entry[0])
                        except (TypeError, ValueError):
                            pass
        except Exception:  # noqa: BLE001
            return frames
        return frames


def _as_float(v: Any) -> Any:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _first_scalar(v: Any) -> Any:
    # gt_velocity may be a 1-element array/tensor; read-only, no mutation.
    try:
        if v is None:
            return None
        if hasattr(v, "item"):
            try:
                return float(v.item())
            except Exception:  # noqa: BLE001
                pass
        if isinstance(v, (list, tuple)) and v:
            return _as_float(v[0])
        return _as_float(v)
    except Exception:  # noqa: BLE001
        return None


def _read_scalar(tick_data: Any, key: str) -> Any:
    try:
        if isinstance(tick_data, dict) and key in tick_data:
            return _first_scalar(tick_data[key])
    except Exception:  # noqa: BLE001
        return None
    return None


def _candidate_set_summary(candidates: Any, max_items: int = 8) -> dict[str, Any]:
    """Read-only bounded candidate-set summary for JSON serialization."""
    out: dict[str, Any] = {"present": False}
    if candidates is None:
        return out
    try:
        seq = list(candidates)
    except Exception:  # noqa: BLE001
        return {"present": False, "reason": "CANDIDATE_SET_NOT_ITERABLE", "type": str(type(candidates).__name__)}

    out["present"] = True
    out["count"] = len(seq)
    out["max_observed"] = min(len(seq), max_items)
    out["items"] = []
    for idx, item in enumerate(seq[:max_items]):
        out["items"].append(_candidate_item_summary(item, idx))
    return out


def _candidate_item_summary(item: Any, index: int) -> dict[str, Any]:
    out: dict[str, Any] = {"index": index}
    try:
        loc = item[0]
        cmd = item[1] if len(item) > 1 else None
    except Exception:  # noqa: BLE001
        return {"index": index, "type": str(type(item).__name__), "raw": str(item)}

    if hasattr(loc, "x") and hasattr(loc, "y"):
        try:
            out["loc_x"] = float(loc.x)
            out["loc_y"] = float(loc.y)
            out["loc_z"] = float(loc.z)
        except Exception:  # noqa: BLE001
            pass
    elif isinstance(loc, (list, tuple)) and len(loc) >= 2:
        out["loc_x"] = _as_float(loc[0])
        out["loc_y"] = _as_float(loc[1])
        if len(loc) > 2:
            out["loc_z"] = _as_float(loc[2])

    if cmd is not None:
        out["command"] = getattr(cmd, "name", str(cmd))
    return out


def _shape_of(nested: Any) -> Any:
    """Shape of a nested list (already CPU-side), read-only. None on failure."""
    try:
        if nested is None:
            return None
        shape = []
        cur = nested
        while isinstance(cur, (list, tuple)):
            shape.append(len(cur))
            cur = cur[0] if cur else None
        return shape or None
    except Exception:  # noqa: BLE001
        return None


def _tensor_to_list(t: Any) -> Any:
    """Read-only tensor -> nested list. Never mutates. Returns None on failure/absence."""
    try:
        if t is None:
            return None
        if hasattr(t, "detach"):
            return t.detach().cpu().numpy().tolist()
        if hasattr(t, "tolist"):
            return t.tolist()
        return list(t)
    except Exception:  # noqa: BLE001
        return None


def _control_values(control: Any) -> dict[str, Any]:
    """Read-only snapshot of a carla.VehicleControl-like object."""
    out: dict[str, Any] = {}
    for field in ("steer", "throttle", "brake", "hand_brake", "reverse", "manual_gear_shift"):
        try:
            if hasattr(control, field):
                out[field] = getattr(control, field)
        except Exception:  # noqa: BLE001
            pass
    return out


def build_runtime_probe(agent: Any = None) -> Any:
    """Factory. Returns inert probe unless DRIVECLARIFY_PROBE_ENABLED is truthy.

    Never raises: any import/config error degrades to the inert probe so the baseline
    control path is never affected by probe setup.
    """
    owner_requested = _truthy(
        os.environ.get("DRIVECLARIFY_RQ2_T_2A_OWNER_ENABLED")
    )
    if owner_requested and not _truthy(os.environ.get("DRIVECLARIFY_PROBE_ENABLED")):
        raise RuntimeError("RQ2_T_2A_OWNER_REQUIRES_OBSERVER_PROBE")
    if not _truthy(os.environ.get("DRIVECLARIFY_PROBE_ENABLED")):
        return _InertRuntimeProbe("DISABLED_BY_ENV")
    try:
        dc_repo = os.environ.get("DRIVECLARIFY_REPO", _DEFAULT_DC_REPO)
        if dc_repo not in sys.path:
            sys.path.insert(0, dc_repo)
        from driveclarify_probe.config import ProbeConfig
        from driveclarify_probe.hooks import build_probe

        output = os.environ.get("DRIVECLARIFY_PROBE_OUTPUT")
        if not output:
            if owner_requested:
                raise RuntimeError("RQ2_T_2A_OWNER_REQUIRES_PROBE_OUTPUT")
            return _InertRuntimeProbe("ENABLED_BUT_NO_OUTPUT_PATH")
        run_id = os.environ.get("DRIVECLARIFY_PROBE_RUN_ID", "cp0_run")
        cfg = ProbeConfig(
            enabled=True,
            output_path=output,
            run_id=run_id,
            camera_logging_mode="NONE",     # metadata-only; no pixels
            actor_roi_m=float(os.environ.get("DRIVECLARIFY_PROBE_ACTOR_ROI_M", "50")),
            actor_max_count=int(os.environ.get("DRIVECLARIFY_PROBE_ACTOR_MAX", "32")),
            static_map_once=True,
            flush_policy="PER_RECORD",
            queue_maxsize=int(os.environ.get("DRIVECLARIFY_PROBE_QUEUE_MAX", "256")),
        )
        provenance = {
            "code_commit": os.environ.get("DRIVECLARIFY_COMMIT", "eaa332b"),
            "config_hash": os.environ.get("DRIVECLARIFY_CONFIG_HASH", "UNKNOWN"),
            "checkpoint_hash": os.environ.get("DRIVECLARIFY_CKPT_HASH", "UNKNOWN"),
            "worktree_dirty_summary": os.environ.get("DRIVECLARIFY_DIRTY", "UNKNOWN"),
        }
        harness_probe = build_probe(cfg, provenance=provenance)
        equivalence_path = os.environ.get("DRIVECLARIFY_PROBE_EQUIVALENCE")
        # CP1 world-state writer (opt-in). Absent -> _RuntimeProbe behaves exactly like CP0.
        ws_writer = None
        ws_mod = None
        ws_output = os.environ.get("DRIVECLARIFY_WORLDSTATE_OUTPUT")
        if ws_output:
            try:
                from driveclarify_probe import world_state as ws_mod  # type: ignore
                ws_writer = ws_mod.WorldStateWriter(ws_output)
            except Exception:  # noqa: BLE001 - world-state failure must not disable CP0 probe
                ws_writer = None
                ws_mod = None
        # CP3B interface probe (opt-in, read-only, default-OFF). Active only when
        # DRIVECLARIFY_CP3B_IMAGE_DIR is set AND the module imports. Absent -> pure CP1.
        cp3b_mod = None
        cp3b_img_dir = None
        cp3b_identity = None
        cp3b_img_dir_env = os.environ.get("DRIVECLARIFY_CP3B_IMAGE_DIR")
        if cp3b_img_dir_env:
            try:
                from driveclarify_probe import cp3b_probe as cp3b_mod  # type: ignore

                cp3b_img_dir = cp3b_img_dir_env
                cp3b_identity = cp3b_mod.identity_hashes()
            except Exception:  # noqa: BLE001 - CP3B failure must not disable CP1/CP0
                cp3b_mod = None
                cp3b_img_dir = None
                cp3b_identity = None
        # VLA research-debug visualization.  The recorder lives in DriveClarify;
        # this SimLingo-side change is only the minimal default-OFF plumbing needed
        # to see the exact DrivingInput.camera_images object.
        vla_debug_recorder = None
        if _truthy(os.environ.get("DRIVECLARIFY_VLA_DEBUG_ENABLED")):
            try:
                from driveclarify_visualization.vla_debug_recorder import (
                    build_vla_debug_recorder,
                )

                vla_debug_recorder = build_vla_debug_recorder(agent)
            except Exception:  # noqa: BLE001 - never disable the baseline probe
                vla_debug_recorder = None
        # The evidence runtime and the 2A scientific owner are separate channels.
        # The former may observe/prepare the one normal model input; only the latter
        # has plan-source authority when enabled.
        live_shadow_runtime = None
        if _truthy(os.environ.get("DRIVECLARIFY_SHADOW_V0")):
            try:
                from driveclarify_m3_runtime_shadow.live_shadow_runtime import (
                    build_live_shadow_runtime,
                )

                live_shadow_runtime = build_live_shadow_runtime(agent)
            except Exception:  # noqa: BLE001 - shadow setup never disables baseline
                live_shadow_runtime = None
        scientific_owner_runtime = None
        if owner_requested:
            from driveclarify_rq2_t.experiment_2a import build_shared_prefix_owner

            scientific_owner_runtime = build_shared_prefix_owner(agent)
        return _RuntimeProbe(
            harness_probe, run_id, equivalence_path,
            agent=agent,
            world_state_writer=ws_writer,
            world_state_mod=ws_mod,
            roi_m=float(os.environ.get("DRIVECLARIFY_PROBE_ACTOR_ROI_M", "50")),
            actor_max=int(os.environ.get("DRIVECLARIFY_PROBE_ACTOR_MAX", "32")),
            cp3b_mod=cp3b_mod,
            cp3b_img_dir=cp3b_img_dir,
            cp3b_identity=cp3b_identity,
            vla_debug_recorder=vla_debug_recorder,
            live_shadow_runtime=live_shadow_runtime,
            scientific_owner_runtime=scientific_owner_runtime,
            live_runtime_fail_closed=False,
        )
    except Exception as exc:  # noqa: BLE001 - degrade to inert, never break baseline
        if owner_requested:
            raise
        return _InertRuntimeProbe(f"IMPORT_OR_CONFIG_FAILED:{type(exc).__name__}")
