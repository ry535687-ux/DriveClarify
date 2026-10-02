"""Read-only, clock-domain-safe native liveness classification.

This module has no CARLA, model, planner, PID, or control dependency.  It only
classifies immutable samples supplied by the native observer.  In particular,
it has no callback through which it could issue a recovery or a control.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math
from typing import Deque, Dict, Optional, Sequence


NATIVE_PROGRESSING = "NATIVE_PROGRESSING"
NATIVE_STATIONARY_COMMANDING_STOP = "NATIVE_STATIONARY_COMMANDING_STOP"
NATIVE_STATIONARY_DESPITE_FORWARD_COMMAND = (
    "NATIVE_STATIONARY_DESPITE_FORWARD_COMMAND"
)
SIMULATION_NOT_ADVANCING = "SIMULATION_NOT_ADVANCING"
SENSOR_OR_MODEL_STALL = "SENSOR_OR_MODEL_STALL"
EVALUATOR_ONLY_WALLCLOCK_TIMEOUT = "EVALUATOR_ONLY_WALLCLOCK_TIMEOUT"
UNKNOWN = "UNKNOWN"

TERMINAL_DIAGNOSTIC_STATES = frozenset(
    {
        NATIVE_STATIONARY_COMMANDING_STOP,
        NATIVE_STATIONARY_DESPITE_FORWARD_COMMAND,
        SIMULATION_NOT_ADVANCING,
        SENSOR_OR_MODEL_STALL,
    }
)


@dataclass(frozen=True)
class LivenessSample:
    """One immutable observation with explicitly separated clock domains."""

    frame_id: int
    simulation_time_s: float
    wall_monotonic_s: float
    xyz: Sequence[float]
    ego_speed_mps: float
    route_completion_percent: Optional[float]
    commanded_target_speed_mps: Optional[float]
    throttle: float
    brake: float
    sensor_heartbeat: int
    model_forward_heartbeat: int
    pid_heartbeat: int
    planner_heartbeat: int
    evaluator_heartbeat: int
    force_move_active: bool = False
    wallclock_timeout_observed: bool = False


class NativeLivenessMonitor:
    """Classify progress over simulation time and stalls over wall time.

    Simulation-time durations are formed only by subtracting simulation
    timestamps.  Wall-time durations are formed only by subtracting monotonic
    timestamps.  Cross-domain subtraction is neither needed nor exposed.
    """

    def __init__(
        self,
        simulation_window_s: float = 4.0,
        wall_stall_s: float = 10.0,
        displacement_epsilon_m: float = 0.25,
        speed_epsilon_mps: float = 0.15,
        route_progress_epsilon_percent: float = 0.01,
        stop_command_threshold_mps: float = 0.4,
        forward_command_threshold_mps: float = 0.8,
    ):
        if simulation_window_s <= 0 or wall_stall_s <= 0:
            raise ValueError("LIVENESS_WINDOWS_MUST_BE_POSITIVE")
        self.simulation_window_s = float(simulation_window_s)
        self.wall_stall_s = float(wall_stall_s)
        self.displacement_epsilon_m = float(displacement_epsilon_m)
        self.speed_epsilon_mps = float(speed_epsilon_mps)
        self.route_progress_epsilon_percent = float(
            route_progress_epsilon_percent
        )
        self.stop_command_threshold_mps = float(stop_command_threshold_mps)
        self.forward_command_threshold_mps = float(forward_command_threshold_mps)
        self._samples: Deque[LivenessSample] = deque()
        self._first_no_progress_frame: Optional[int] = None
        self._first_no_progress_simulation_s: Optional[float] = None
        self._first_no_progress_wall_s: Optional[float] = None

    def observe(self, sample: LivenessSample) -> Dict[str, object]:
        self._samples.append(sample)
        newest_sim = float(sample.simulation_time_s)
        newest_wall = float(sample.wall_monotonic_s)
        # Retain enough history for both independently measured windows.
        while len(self._samples) > 2:
            candidate = self._samples[1]
            sim_old = newest_sim - float(candidate.simulation_time_s)
            wall_old = newest_wall - float(candidate.wall_monotonic_s)
            if not (
                sim_old > self.simulation_window_s
                and wall_old > self.wall_stall_s
            ):
                break
            self._samples.popleft()

        first = self._samples[0]
        sim_delta = max(0.0, newest_sim - float(first.simulation_time_s))
        wall_delta = max(0.0, newest_wall - float(first.wall_monotonic_s))
        displacement = math.dist(first.xyz[:2], sample.xyz[:2])
        route_delta = self._route_delta(
            first.route_completion_percent, sample.route_completion_percent
        )
        heartbeat_deltas = {
            "sensor": sample.sensor_heartbeat - first.sensor_heartbeat,
            "model": sample.model_forward_heartbeat
            - first.model_forward_heartbeat,
            "pid": sample.pid_heartbeat - first.pid_heartbeat,
            "planner": sample.planner_heartbeat - first.planner_heartbeat,
            "evaluator": sample.evaluator_heartbeat - first.evaluator_heartbeat,
        }

        simulation_window_ready = sim_delta >= self.simulation_window_s - 1.0e-9
        wall_window_ready = wall_delta >= self.wall_stall_s - 1.0e-9
        simulation_advancing = sim_delta > 1.0e-6
        heartbeats_advancing = all(value > 0 for value in heartbeat_deltas.values())
        stationary = bool(
            displacement <= self.displacement_epsilon_m
            and max(row.ego_speed_mps for row in self._samples)
            <= self.speed_epsilon_mps
            and (
                route_delta is None
                or route_delta <= self.route_progress_epsilon_percent
            )
        )
        targets = [
            float(row.commanded_target_speed_mps)
            for row in self._samples
            if row.commanded_target_speed_mps is not None
        ]
        all_have_targets = len(targets) == len(self._samples)
        commanding_stop = bool(
            all_have_targets
            and targets
            and max(targets) < self.stop_command_threshold_mps
            and all(row.brake >= 0.99 for row in self._samples)
        )
        commanding_forward = bool(
            all_have_targets
            and targets
            and min(targets) >= self.forward_command_threshold_mps
        )

        if wall_window_ready and not simulation_advancing:
            classification = SIMULATION_NOT_ADVANCING
        elif simulation_window_ready and not heartbeats_advancing:
            classification = SENSOR_OR_MODEL_STALL
        elif simulation_window_ready and stationary and commanding_stop:
            classification = NATIVE_STATIONARY_COMMANDING_STOP
        elif simulation_window_ready and stationary and commanding_forward:
            classification = NATIVE_STATIONARY_DESPITE_FORWARD_COMMAND
        elif sample.wallclock_timeout_observed and simulation_advancing and heartbeats_advancing:
            classification = EVALUATOR_ONLY_WALLCLOCK_TIMEOUT
        elif simulation_window_ready and (
            displacement > self.displacement_epsilon_m
            or (
                route_delta is not None
                and route_delta > self.route_progress_epsilon_percent
            )
        ):
            classification = NATIVE_PROGRESSING
        else:
            classification = UNKNOWN

        no_progress = classification in {
            NATIVE_STATIONARY_COMMANDING_STOP,
            NATIVE_STATIONARY_DESPITE_FORWARD_COMMAND,
        }
        if no_progress and self._first_no_progress_frame is None:
            self._first_no_progress_frame = int(first.frame_id)
            self._first_no_progress_simulation_s = float(first.simulation_time_s)
            self._first_no_progress_wall_s = float(first.wall_monotonic_s)
        elif not no_progress:
            self._first_no_progress_frame = None
            self._first_no_progress_simulation_s = None
            self._first_no_progress_wall_s = None

        no_progress_sim_s = None
        no_progress_wall_s = None
        if self._first_no_progress_simulation_s is not None:
            no_progress_sim_s = max(
                0.0, newest_sim - self._first_no_progress_simulation_s
            )
            no_progress_wall_s = max(
                0.0, newest_wall - float(self._first_no_progress_wall_s)
            )

        return {
            "schema": "driveclarify.rq3-native-liveness-state.v1",
            "classification": classification,
            "diagnostic_only": True,
            "control_authority": "NONE",
            "window": {
                "first_frame_id": int(first.frame_id),
                "last_frame_id": int(sample.frame_id),
                "simulation_duration_s": sim_delta,
                "wall_monotonic_duration_s": wall_delta,
                "xy_displacement_m": displacement,
                "route_completion_delta_percent": route_delta,
            },
            "stationary": stationary,
            "commanding_stop": commanding_stop,
            "commanding_forward": commanding_forward,
            "simulation_advancing": simulation_advancing,
            "heartbeat_deltas": heartbeat_deltas,
            "heartbeats_advancing": heartbeats_advancing,
            "force_move_active": bool(sample.force_move_active),
            "first_no_progress_frame": self._first_no_progress_frame,
            "no_progress_duration_simulation_s": no_progress_sim_s,
            "no_progress_duration_wall_monotonic_s": no_progress_wall_s,
            "clock_domain_rule": (
                "simulation-minus-simulation; monotonic-minus-monotonic only"
            ),
        }

    @staticmethod
    def _route_delta(first: Optional[float], last: Optional[float]) -> Optional[float]:
        if first is None or last is None:
            return None
        return abs(float(last) - float(first))


__all__ = [
    "EVALUATOR_ONLY_WALLCLOCK_TIMEOUT",
    "LivenessSample",
    "NATIVE_PROGRESSING",
    "NATIVE_STATIONARY_COMMANDING_STOP",
    "NATIVE_STATIONARY_DESPITE_FORWARD_COMMAND",
    "NativeLivenessMonitor",
    "SENSOR_OR_MODEL_STALL",
    "SIMULATION_NOT_ADVANCING",
    "TERMINAL_DIAGNOSTIC_STATES",
    "UNKNOWN",
]
