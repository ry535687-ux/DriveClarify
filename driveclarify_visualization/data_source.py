"""Read-only data source for the visualization layer.

Two source kinds share one frame schema:
  - OfflineRunSource: reads an existing CP3B run dir (world_state.jsonl + images/), never
    mutating a byte. This is all v0 uses.
  - LatestFrameBuffer: a maxsize=1, non-blocking publish buffer for a FUTURE live observer.
    publish() never blocks the producer; if the consumer is slow the previous frame is
    dropped (counted), never the producer's control loop.

A "VizFrame" is a plain dict pulled from the existing record; we DO NOT recompute anything.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any, Iterator, Optional


class VizFrame(dict):
    """Thin dict wrapper: one visualization frame assembled from an existing record.
    No model output is recomputed; every value is copied read-only from source data."""


class OfflineRunSource:
    """Iterate an existing CP3B run directory, read-only."""

    def __init__(self, run_dir: str) -> None:
        self.run_dir = run_dir
        self.ws_path = os.path.join(run_dir, "world_state.jsonl")
        self.img_dir = os.path.join(run_dir, "images")
        if not os.path.exists(self.ws_path):
            raise FileNotFoundError(f"no world_state.jsonl in {run_dir}")

    def run_id(self) -> str:
        return os.path.basename(os.path.normpath(self.run_dir))

    def __iter__(self) -> Iterator[VizFrame]:
        with open(self.ws_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:  # noqa: BLE001 - a bad line is skipped, never raised up
                    continue
                yield self._assemble(r)

    def _assemble(self, r: dict[str, Any]) -> VizFrame:
        cp3b = r.get("cp3b", {}) or {}
        rgb = cp3b.get("rgb", {}) or {}
        img_rel = rgb.get("image_path")
        img_abs = os.path.join(self.img_dir, img_rel) if img_rel else None
        ego = r.get("ego", {}) or {}
        rot = ego.get("rotation_rpy_deg") or [None, None, None]
        return VizFrame({
            "run_id": r.get("run_id"),
            "observation_id": r.get("observation_id"),
            "record_seq": cp3b.get("record_seq"),
            "carla_frame": r.get("snapshot_frame") or r.get("carla_snapshot_frame"),
            "sensor_frames": r.get("input_sensor_frames", {}),
            "sim_time_s": r.get("snapshot_elapsed_seconds"),
            "gametime_s": r.get("gametime_seconds"),
            "monotonic_s": r.get("probe_read_monotonic_s"),
            "model_start_monotonic_s": r.get("model_start_monotonic_s"),
            "model_end_monotonic_s": r.get("model_end_monotonic_s"),
            "control_ready_monotonic_s": r.get("control_ready_monotonic_s"),
            # Historical CP3B PNG: raw CARLA rgb_0 sensor frame, before SimLingo's
            # JPEG/crop/dynamic-resize/normalize/bfloat16 model-input pipeline.
            "image_abspath": img_abs,
            "image_semantics": "RAW_CAMERA_RGB_0_PREPROCESSING",
            "image_sha256": rgb.get("image_sha256"),
            "image_frame": rgb.get("image_frame"),
            "image_wh": (rgb.get("width"), rgb.get("height")),
            "forward_invocation_counter": cp3b.get("forward_invocation_counter"),
            "ego": ego,
            "ego_yaw_deg": rot[2] if len(rot) == 3 else None,
            "camera": r.get("camera", {}),
            "map_waypoint": r.get("map_waypoint", {}),
            "route_context": r.get("route_context", {}),
            "actors": r.get("actors", {}),
            "traffic_lights": r.get("traffic_lights", {}),
            "pred_route_values": r.get("pred_route_values"),
            "pred_speed_wps_values": r.get("pred_speed_wps_values"),
            "landmarks": cp3b.get("landmarks", {}),
            "route_id": cp3b.get("route_id"),
            "episode_id": cp3b.get("episode_id"),
            "baseline_control": r.get("baseline_control", {}),
        })


class LatestFrameBuffer:
    """maxsize=1 non-blocking publish for a FUTURE live observer.

    publish() NEVER blocks: it replaces the single slot and counts a drop if the previous
    frame was never consumed. The producer (control loop) is therefore never stalled by a
    slow GUI. This class is exercised by unit tests now; it is not wired to any live loop
    this round.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._slot: Optional[VizFrame] = None
        self.published = 0
        self.dropped = 0
        self.consumed = 0

    def publish(self, frame: VizFrame) -> None:
        # non-blocking: acquire without waiting; if busy, drop rather than block producer
        got = self._lock.acquire(blocking=False)
        if not got:
            self.dropped += 1
            return
        try:
            if self._slot is not None:
                self.dropped += 1  # previous frame overwritten before consumption
            self._slot = frame
            self.published += 1
        finally:
            self._lock.release()

    def consume(self) -> Optional[VizFrame]:
        with self._lock:
            f = self._slot
            self._slot = None
            if f is not None:
                self.consumed += 1
            return f

    def stats(self) -> dict[str, int]:
        return {"published": self.published, "dropped": self.dropped, "consumed": self.consumed}
