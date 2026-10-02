"""Side-path recorder for the SimLingo VLA research-debug visualization.

The recorder is instantiated only when ``DRIVECLARIFY_VLA_DEBUG_ENABLED=1``.  The
SimLingo hook gives it references to values that the baseline already produced.  It
materializes read-only copies *after* the one model forward and one PID call, then a
background CPU writer creates PNG/JSON artifacts.  It never invokes the model, PID,
route planner, world tick, sensor creation, or vehicle control.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from . import vla_debug_renderer as renderer


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe_scalar(value: Any) -> Any:
    try:
        if value is None:
            return None
        if hasattr(value, "item"):
            return float(value.item())
        if isinstance(value, (list, tuple)) and value:
            return _safe_scalar(value[0])
        return float(value)
    except Exception:  # noqa: BLE001
        return None


def _control_values(control: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in (
        "steer",
        "throttle",
        "brake",
        "hand_brake",
        "reverse",
        "manual_gear_shift",
        "gear",
    ):
        try:
            if hasattr(control, key):
                out[key] = getattr(control, key)
        except Exception:  # noqa: BLE001
            pass
    return out


def _tensor_cpu_float(value: Any) -> np.ndarray | None:
    """Lossless numeric copy for bfloat16/float tensors, after baseline use."""
    try:
        if value is None:
            return None
        if hasattr(value, "detach"):
            tensor = value.detach()
            try:
                tensor = tensor.float()
            except Exception:  # noqa: BLE001
                pass
            return np.asarray(tensor.cpu().contiguous().numpy(), dtype=np.float32).copy()
        return np.asarray(value, dtype=np.float32).copy()
    except Exception:  # noqa: BLE001
        return None


def _tensor_list(value: Any) -> Any:
    arr = _tensor_cpu_float(value)
    return arr.tolist() if arr is not None else None


def _raw_camera_rgb(input_data: Any, sensor_id: str = "rgb_0") -> tuple[np.ndarray | None, Any]:
    try:
        entry = input_data[sensor_id]
        sensor_frame = int(entry[0]) if isinstance(entry, (tuple, list)) else None
        value = entry[1] if isinstance(entry, (tuple, list)) else entry
        arr = np.asarray(value)
        if arr.ndim != 3 or arr.shape[2] < 3:
            return None, sensor_frame
        # CARLA Python sensor buffer exposed by leaderboard is BGRA/BGR.
        return np.ascontiguousarray(arr[:, :, :3][:, :, ::-1], dtype=np.uint8), sensor_frame
    except Exception:  # noqa: BLE001
        return None, None


def _prompt_snapshot(agent: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "instruction_text": getattr(agent, "prompt", None),
        "prompt_tp": getattr(agent, "prompt_tp", None),
        "command_history": None,
        "previous_command": getattr(agent, "last_command", None),
        "prompt_token_ids": None,
        "prompt_token_sha256": None,
        "model_prompt_text": None,
    }
    try:
        result["command_history"] = list(getattr(agent, "commands"))
    except Exception:  # noqa: BLE001
        pass
    try:
        prompt_obj = getattr(agent, "DrivingInput", {}).get("prompt")
        language = getattr(prompt_obj, "language_string", None)
        if language:
            result["model_prompt_text"] = str(language[0])
        ids = getattr(prompt_obj, "phrase_ids", None)
        ids_arr = _tensor_cpu_float(ids)
        if ids_arr is not None:
            ids_arr = ids_arr.astype(np.int64)
            result["prompt_token_ids"] = ids_arr.tolist()
            result["prompt_token_sha256"] = _sha_bytes(ids_arr.tobytes(order="C"))
    except Exception:  # noqa: BLE001
        pass
    return result


@dataclass(frozen=True)
class VLADebugConfig:
    output_dir: Path
    mode: str = "live"
    stride: int = 2
    queue_size: int = 16
    use_ego_view: bool = True
    use_bev_debug: bool = True

    @classmethod
    def from_env(cls) -> "VLADebugConfig":
        output = Path(
            os.environ.get(
                "DRIVECLARIFY_VLA_DEBUG_OUTPUT",
                "/home/buaa/wrh/DriveClarify/runtime/debug_visualization",
            )
        )
        mode = os.environ.get("DRIVECLARIFY_VLA_DEBUG_MODE", "live").strip().lower()
        if mode not in {"live", "evidence"}:
            mode = "live"
        return cls(
            output_dir=output,
            mode=mode,
            stride=max(1, int(os.environ.get("DRIVECLARIFY_VLA_DEBUG_STRIDE", "2"))),
            queue_size=max(
                1,
                int(
                    os.environ.get(
                        "DRIVECLARIFY_VLA_DEBUG_QUEUE",
                        "128" if mode == "evidence" else "16",
                    )
                ),
            ),
            use_ego_view=_truthy(os.environ.get("DRIVECLARIFY_VLA_DEBUG_USE_EGO_VIEW", "1")),
            use_bev_debug=_truthy(os.environ.get("DRIVECLARIFY_VLA_DEBUG_USE_BEV", "1")),
        )


class VLADebugRecorder:
    """Read-only per-tick capture plus asynchronous CPU rendering."""

    def __init__(self, agent: Any, config: VLADebugConfig) -> None:
        self._agent = agent
        self.config = config
        self._pending: dict[str, Any] | None = None
        self._sample_seq = 0
        self._ticks_seen = 0
        self._enqueued = 0
        self._written = 0
        self._dropped = 0
        self._errors = 0
        self._control_identity_preserved = 0
        self._control_values_preserved = 0
        self._queue: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=config.queue_size)
        self._stop = threading.Event()
        self._worker = threading.Thread(target=self._writer_loop, name="dc-vla-debug-writer", daemon=True)
        self._prepare_dirs()
        self._worker.start()

    def _prepare_dirs(self) -> None:
        self.config.output_dir.mkdir(parents=True, exist_ok=True)
        for name in (
            "raw_camera",
            "model_input_tensor",
            "input_rgb",
            "ego_view",
            "bev_debug",
            "overlay",
            "frames",
        ):
            (self.config.output_dir / name).mkdir(parents=True, exist_ok=True)

    def on_tick(
        self,
        input_data: Any,
        tick_data: Any,
        timestamp: Any,
        frame_id: Any,
        observation_id: Any,
    ) -> None:
        self._ticks_seen += 1
        should_sample = ((self._ticks_seen - 1) % self.config.stride) == 0
        if not should_sample:
            self._pending = None
            return
        driving_input = getattr(self._agent, "DrivingInput", {}) or {}
        self._pending = {
            "_input_data_ref": input_data,
            "_model_input_ref": driving_input.get("camera_images"),
            "frame_id": frame_id,
            "observation_id": observation_id,
            "timestamp_s": _safe_scalar(timestamp),
            "timestamp_domain": "GameTime/SIM",
            "route_id": os.environ.get("DRIVECLARIFY_CP3B_ROUTE_ID")
            or os.environ.get("DRIVECLARIFY_VLA_DEBUG_ROUTE_ID"),
            "town": os.environ.get("DRIVECLARIFY_VLA_DEBUG_TOWN"),
            "speed_mps": _safe_scalar((tick_data or {}).get("speed") if isinstance(tick_data, dict) else None),
            "model_input_shape": list(getattr(driving_input.get("camera_images"), "shape", [])),
            "model_input_source_dtype": str(getattr(driving_input.get("camera_images"), "dtype", "UNKNOWN")),
            "recording_mode": self.config.mode,
            "generated_from_same_forward": False,
            "pred_route": None,
            "pred_speed_wps": None,
            "_control_ref": None,
            "_control_values_before": None,
            **_prompt_snapshot(self._agent),
        }

    def on_model_output(
        self,
        pred_route: Any,
        pred_speed_wps: Any,
        model_start_monotonic_s: float,
        model_end_monotonic_s: float,
    ) -> None:
        if self._pending is None:
            return
        self._pending.update(
            {
                "_pred_route_ref": pred_route,
                "_pred_speed_ref": pred_speed_wps,
                "generated_from_same_forward": True,
                "model_start_monotonic_s": float(model_start_monotonic_s),
                "model_end_monotonic_s": float(model_end_monotonic_s),
            }
        )

    def on_control(self, control: Any, speed: Any, control_ready_monotonic_s: float) -> None:
        if self._pending is None:
            return
        values = _control_values(control)
        self._pending["_control_ref"] = control
        self._pending["_control_values_before"] = dict(values)
        self._pending["control"] = dict(values)
        self._pending["speed_mps"] = _safe_scalar(speed) or self._pending.get("speed_mps")
        self._pending["control_ready_monotonic_s"] = float(control_ready_monotonic_s)

    def commit(self) -> None:
        pending, self._pending = self._pending, None
        if pending is None:
            return
        try:
            control = pending.get("_control_ref")
            control_id_before = id(control) if control is not None else None
            control_values_before = pending.get("_control_values_before")
            snapshot = self._materialize(pending)
            if control is not None:
                if id(control) == control_id_before:
                    self._control_identity_preserved += 1
                if _control_values(control) == control_values_before:
                    self._control_values_preserved += 1
            snapshot["dropped_before_enqueue"] = self._dropped
            try:
                self._queue.put_nowait(snapshot)
                self._enqueued += 1
            except queue.Full:
                self._dropped += 1
        except Exception:  # noqa: BLE001 - visualization must never break control
            self._errors += 1

    def _materialize(self, pending: dict[str, Any]) -> dict[str, Any]:
        # Materialization is deliberately after the baseline forward and PID.
        raw_rgb, sensor_frame = _raw_camera_rgb(pending.pop("_input_data_ref", None))
        model_input = _tensor_cpu_float(pending.pop("_model_input_ref", None))
        pred_route = _tensor_list(pending.pop("_pred_route_ref", None))
        pred_speed = _tensor_list(pending.pop("_pred_speed_ref", None))
        pending.pop("_control_ref", None)
        pending.pop("_control_values_before", None)
        if model_input is None:
            raise ValueError("actual DrivingInput.camera_images tensor unavailable")
        source_bytes = np.ascontiguousarray(model_input).tobytes(order="C")
        pending.update(
            {
                "_raw_camera_rgb": raw_rgb,
                "_model_input_values": model_input,
                "sensor_frame": sensor_frame,
                "pred_route": pred_route,
                "pred_speed_wps": pred_speed,
                "pred_route_count": _point_count(pred_route),
                "pred_speed_wps_count": _point_count(pred_speed),
                "model_input_value_sha256": _sha_bytes(source_bytes),
                "model_input_stored_dtype": "float32_lossless_values_from_source_tensor",
                "materialized_after_forward_and_pid": True,
                "sample_seq": self._sample_seq,
                "capture_monotonic_s": time.monotonic(),
            }
        )
        self._sample_seq += 1
        return pending

    def close(self) -> None:
        try:
            self._queue.put(None, timeout=2.0)
        except queue.Full:
            self._stop.set()
        self._worker.join(timeout=30.0)
        try:
            self._write_summary()
        except Exception:  # noqa: BLE001
            self._errors += 1

    def summary(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "mode": self.config.mode,
            "stride": self.config.stride,
            "ticks_seen": self._ticks_seen,
            "samples_enqueued": self._enqueued,
            "samples_written": self._written,
            "samples_dropped": self._dropped,
            "errors": self._errors,
            "control_identity_preserved_samples": self._control_identity_preserved,
            "control_values_preserved_samples": self._control_values_preserved,
            "probe_induced_model_calls": 0,
            "probe_induced_pid_calls": 0,
            "probe_induced_route_planner_steps": 0,
            "sensor_parameters_changed": False,
            "model_input_mutated": False,
        }

    def _writer_loop(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=0.25)
            except queue.Empty:
                continue
            if item is None:
                self._queue.task_done()
                break
            try:
                self._write_snapshot(item)
                self._written += 1
            except Exception:  # noqa: BLE001 - writer failure is isolated
                self._errors += 1
            finally:
                self._queue.task_done()
        self._write_summary()

    def _write_snapshot(self, snapshot: dict[str, Any]) -> None:
        out = self.config.output_dir
        frame = snapshot.get("frame_id")
        tag = f"frame_{int(frame):06d}" if frame is not None else f"sample_{snapshot['sample_seq']:06d}"
        raw_arr = snapshot.pop("_raw_camera_rgb", None)
        model_values = snapshot.pop("_model_input_values")

        tensor_path = out / "model_input_tensor" / f"{tag}.npy"
        np.save(tensor_path, model_values, allow_pickle=False)
        snapshot["model_input_tensor_file"] = str(tensor_path.relative_to(out))
        snapshot["model_input_tensor_file_sha256"] = _sha_file(tensor_path)

        patches, input_meta = renderer.model_tensor_to_rgb_patches(model_values)
        model_img = renderer.patch_mosaic(patches)
        input_path = out / "input_rgb" / f"{tag}.png"
        model_img.save(input_path, format="PNG")
        snapshot["model_input_png"] = str(input_path.relative_to(out))
        snapshot["model_input_png_sha256"] = _sha_file(input_path)
        snapshot["model_input_display"] = input_meta

        if raw_arr is None:
            raw_img = Image.new("RGB", (1024, 512), (0, 0, 0))
            snapshot["raw_camera_status"] = "NOT_AVAILABLE"
        else:
            raw_img = Image.fromarray(raw_arr, "RGB")
            snapshot["raw_camera_status"] = "AVAILABLE"
        raw_path = out / "raw_camera" / f"{tag}.png"
        raw_img.save(raw_path, format="PNG")
        snapshot["raw_camera_png"] = str(raw_path.relative_to(out))
        snapshot["raw_camera_png_sha256"] = _sha_file(raw_path)

        if self.config.use_ego_view:
            ego_img, enhancement = renderer.enhance_for_researcher(np.asarray(raw_img))
        else:
            ego_img, enhancement = raw_img.copy(), {
                "display_only": True,
                "used_by_model": False,
                "enhancement_disabled": True,
                "sensor_parameters_changed": False,
            }
        ego_path = out / "ego_view" / f"{tag}.png"
        ego_img.save(ego_path, format="PNG")
        snapshot["ego_view_png"] = str(ego_path.relative_to(out))
        snapshot["ego_view_display_transform"] = enhancement

        bev = (
            renderer.render_model_local_bev(snapshot)
            if self.config.use_bev_debug
            else Image.new("RGB", (580, 270), (15, 22, 24))
        )
        bev_path = out / "bev_debug" / f"{tag}.png"
        bev.save(bev_path, format="PNG")
        snapshot["bev_debug_png"] = str(bev_path.relative_to(out))
        snapshot["bev_semantics"] = "MODEL_LOCAL_RAW_DEBUG_ONLY_NO_METRIC_OR_CAMERA_PROJECTION"

        overlay = renderer.render_dashboard(snapshot, raw_img, model_img, ego_img, bev)
        overlay_path = out / "overlay" / f"{tag}.png"
        overlay.save(overlay_path, format="PNG")
        snapshot["overlay_png"] = str(overlay_path.relative_to(out))
        snapshot["overlay_png_sha256"] = _sha_file(overlay_path)

        frame_meta = out / "frames" / f"{tag}.json"
        _write_json(frame_meta, snapshot)
        with (out / "records.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(snapshot, ensure_ascii=False, allow_nan=False, sort_keys=True) + "\n")

        latest_tmp = out / ".latest.png.tmp"
        overlay.save(latest_tmp, format="PNG")
        os.replace(latest_tmp, out / "latest.png")
        _write_json(out / "latest.json", snapshot)

    def _write_summary(self) -> None:
        _write_json(self.config.output_dir / "SUMMARY.json", self.summary())


def _point_count(value: Any) -> int:
    try:
        arr = np.asarray(value)
        return int(arr.shape[-2]) if arr.ndim >= 2 else 0
    except Exception:  # noqa: BLE001
        return 0


def _sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False, sort_keys=True)
    os.replace(tmp, path)


def build_vla_debug_recorder(agent: Any = None) -> VLADebugRecorder | None:
    """Default-OFF, fail-open factory used by the SimLingo side hook."""
    if not _truthy(os.environ.get("DRIVECLARIFY_VLA_DEBUG_ENABLED")):
        return None
    try:
        return VLADebugRecorder(agent=agent, config=VLADebugConfig.from_env())
    except Exception:  # noqa: BLE001 - visualization setup must never break baseline
        return None
