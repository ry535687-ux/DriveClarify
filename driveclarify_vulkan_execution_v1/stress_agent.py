"""Record-only agent for the non-scientific native Vulkan lifecycle stress.

It deliberately loads no model or checkpoint, makes no DriveClarify decision,
and uses no scientific PID/planner.  The sensor surface matches the frozen
SimLingo evaluation sensor suite so the visible CARLA/Vulkan path is real.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
import signal
import tempfile
import threading

import carla
from leaderboard.autoagents.autonomous_agent import AutonomousAgent, Track


def get_entry_point():
    return "DriveClarifyVulkanStressAgent"


def _utc_now():
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class DriveClarifyVulkanStressAgent(AutonomousAgent):
    """A fixed-brake, record-only agent that requests clean evaluator stop."""

    def setup(self, path_to_conf_file):
        self.track = Track.SENSORS
        self._cycle_id = os.environ["DRIVECLARIFY_VULKAN_CYCLE_ID"]
        self._receipt_path = Path(os.environ["DRIVECLARIFY_VULKAN_AGENT_RECEIPT"])
        self._ticks = 0
        self._first_timestamp = None
        self._last_timestamp = None
        self._first_observation_at = None
        self._stop_requested = False
        self._write("INITIALIZED")

    def sensors(self):
        return [
            {
                "type": "sensor.camera.rgb", "x": -1.5, "y": 0.0, "z": 2.0,
                "roll": 0.0, "pitch": 0.0, "yaw": 0.0,
                "width": 1024, "height": 512, "fov": 110, "id": "rgb_0",
            },
            {
                "type": "sensor.other.imu", "x": 0.0, "y": 0.0, "z": 0.0,
                "roll": 0.0, "pitch": 0.0, "yaw": 0.0,
                "sensor_tick": 0.05, "id": "imu",
            },
            {
                "type": "sensor.other.gnss", "x": 0.0, "y": 0.0, "z": 0.0,
                "roll": 0.0, "pitch": 0.0, "yaw": 0.0,
                "sensor_tick": 0.01, "id": "gps",
            },
            {"type": "sensor.speedometer", "reading_frequency": 20, "id": "speed"},
        ]

    def _payload(self, phase):
        elapsed = None
        if self._first_timestamp is not None and self._last_timestamp is not None:
            elapsed = max(0.0, float(self._last_timestamp - self._first_timestamp))
        return {
            "schema_version": "driveclarify.r4_2.vulkan_stress_agent_receipt.v1",
            "cycle_id": self._cycle_id,
            "phase": phase,
            "observed_at_utc": _utc_now(),
            "first_durable_observation": self._first_observation_at is not None,
            "first_observation_at_utc": self._first_observation_at,
            "ticks_after_first_observation": max(0, self._ticks - 1),
            "simulation_seconds_after_first_observation": elapsed,
            "required_duration_reached": bool(
                self._ticks - 1 >= 50 and elapsed is not None and elapsed >= 5.0
            ),
            "official_model_forward_count": 0,
            "candidate_forward_count": 0,
            "driveclarify_decision_count": 0,
            "scientific_pid_count": 0,
            "paper_metric_count": 0,
            "checkpoint_load_count": 0,
            "cuda_context_count": 0,
        }

    def _write(self, phase):
        _atomic_json(self._receipt_path, self._payload(phase))

    def run_step(self, input_data, timestamp):
        self._ticks += 1
        self._last_timestamp = float(timestamp)
        if self._first_timestamp is None:
            self._first_timestamp = float(timestamp)
            self._first_observation_at = _utc_now()
            self._write("FIRST_DURABLE_OBSERVATION")

        elapsed = float(timestamp) - self._first_timestamp
        if self._ticks - 1 >= 50 and elapsed >= 5.0 and not self._stop_requested:
            self._stop_requested = True
            self._write("REQUIRED_DURATION_REACHED")
            timer = threading.Timer(0.05, os.kill, args=(os.getpid(), signal.SIGINT))
            timer.daemon = True
            timer.start()

        control = carla.VehicleControl()
        control.throttle = 0.0
        control.steer = 0.0
        control.brake = 1.0
        control.hand_brake = True
        return control

    def destroy(self):
        self._write("DESTROYED")
