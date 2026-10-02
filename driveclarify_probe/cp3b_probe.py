"""CP3B targeted-interface probe extension (READ-ONLY, default-OFF, fail-open).

Additive to the CP1 world-state hook. It closes the ONE gap CP1 left open for the
CP3B interface claims (F6 in particular): CP1 saved no per-frame RGB and recorded no
in-image landmark world coordinates. This module adds, per committed tick:

  1. the SAVED RGB frame (PNG) + its SHA-256 (the model-input camera rgb_0),
  2. a read-only list of landmark world coordinates (traffic lights / actors / actor
     bbox vertices / map landmarks) whose world coords are INDEPENDENT of the model,
  3. identity binding: route_file / config / checkpoint SHA-256 and the per-tick
     forward-invocation counter (MUST equal 1).

Nothing here computes a projection, a metre scale, a left/right sign, or any physical
consequence. All derived diagnostics (per-hypothesis reprojection, scale fit, cross
products) are computed OFFLINE from the raw block per CP3B_LOG_SCHEMA.json.

Hard contract (must hold even if anything here raises):
- Read-only: consumes only values the baseline already produced (the same `input_data`
  dict handed to the P0 hook, and the already-extracted world-state sub-records). Never
  calls model / control_pid / route_planner.run_step / apply_control / world.tick /
  spawn / destroy, never mutates a tensor / dict / control / CARLA actor.
- Fail-open: every entry point is wrapped; an exception is COUNTED (errors) and never
  propagated into the baseline control path or the CP1 world-state record.
- Default-OFF: only active when the hook is given a cp3b image directory; otherwise the
  CP1 record is byte-for-byte what it was (world_state.py path unchanged).

Schema/probe version: driveclarify.cp3b_probe.v0
"""

from __future__ import annotations

import hashlib
import math
import os
from typing import Any

CP3B_PROBE_VERSION = "driveclarify.cp3b_probe.v0"

_OK = "success"
_EMPTY = "empty"
_UNSUPPORTED = "unsupported"
_EXCEPTION = "exception"


def sha256_file(path: str) -> str | None:
    """SHA-256 of a file's bytes. Read-only. None on failure/absence."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:  # noqa: BLE001
        return None


def _vec3(v: Any) -> Any:
    try:
        return [float(v.x), float(v.y), float(v.z)]
    except Exception:  # noqa: BLE001
        return None


def save_rgb_frame(
    input_data: Any,
    out_dir: str,
    obs_id: str,
    record_seq: Any,
    sensor_id: str = "rgb_0",
) -> dict[str, Any]:
    """Save the model-input RGB camera frame as a PNG + record its SHA-256.

    Read-only: the ndarray is the SAME buffer the baseline already received this tick
    (input_data[sensor_id][1]); we copy it, reorder BGRA->RGB, and write a PNG. We do
    NOT touch the baseline's copy or its preprocessing.

    Returns a metadata dict; never raises (returns a status on any failure).
    """
    out: dict[str, Any] = {"status": _OK, "sensor_id": sensor_id}
    try:
        if not isinstance(input_data, dict) or sensor_id not in input_data:
            return {"status": _EMPTY, "reason": "SENSOR_ABSENT", "sensor_id": sensor_id}
        entry = input_data[sensor_id]
        frame_no = None
        arr = None
        if isinstance(entry, (list, tuple)) and len(entry) >= 2:
            try:
                frame_no = int(entry[0])
            except Exception:  # noqa: BLE001
                frame_no = None
            arr = entry[1]
        else:
            arr = entry
        import numpy as _np  # local import: fail-open if unavailable

        a = _np.asarray(arr)
        out["raw_shape"] = list(a.shape)
        out["raw_dtype"] = str(a.dtype)
        out["image_frame"] = frame_no
        # CARLA rgb sensor => HxWx4 BGRA. Take BGR, reorder to RGB for a correct PNG.
        if a.ndim == 3 and a.shape[2] >= 3:
            bgr = a[:, :, :3]
            rgb = bgr[:, :, ::-1]  # BGR -> RGB (view; .copy() below for a contiguous buf)
        elif a.ndim == 3 and a.shape[2] == 1:
            rgb = _np.repeat(a, 3, axis=2)
        else:
            return {"status": _UNSUPPORTED, "reason": f"UNEXPECTED_SHAPE:{a.shape}", "sensor_id": sensor_id}
        rgb = _np.ascontiguousarray(rgb.astype(_np.uint8))
        out["height"], out["width"] = int(rgb.shape[0]), int(rgb.shape[1])
        out["channel_order"] = "RGB"
        out["source_channel_order"] = "BGRA_TAKE_BGR"

        os.makedirs(out_dir, exist_ok=True)
        seq_tag = str(record_seq) if record_seq is not None else "na"
        fname = f"rgb0_{seq_tag}_{frame_no if frame_no is not None else 'nf'}.png"
        fpath = os.path.join(out_dir, fname)

        # Encode PNG. Prefer PIL; fall back to imageio; else record unsupported.
        wrote = False
        try:
            from PIL import Image  # type: ignore

            Image.fromarray(rgb, mode="RGB").save(fpath, format="PNG")
            wrote = True
        except Exception:  # noqa: BLE001
            try:
                import imageio.v2 as _imageio  # type: ignore

                _imageio.imwrite(fpath, rgb)
                wrote = True
            except Exception:  # noqa: BLE001
                out["status"] = _UNSUPPORTED
                out["reason"] = "NO_PNG_ENCODER"
        if wrote:
            out["image_path"] = os.path.relpath(fpath, out_dir)
            out["image_abspath"] = fpath
            out["image_sha256"] = sha256_file(fpath)
            try:
                out["image_bytes"] = os.path.getsize(fpath)
            except Exception:  # noqa: BLE001
                out["image_bytes"] = None
        return out
    except Exception as exc:  # noqa: BLE001
        return {"status": f"{_EXCEPTION}:{type(exc).__name__}", "sensor_id": sensor_id}


def _bbox_world_vertices(actor: Any) -> Any:
    """8 world-space corners of an actor bounding box, read-only. None on failure.

    Uses carla.BoundingBox.get_world_vertices(actor_transform) when available; this is a
    pure read (no mutation). Returns a list of [x,y,z] or None.
    """
    try:
        bb = actor.bounding_box
        tf = actor.get_transform()
        verts = bb.get_world_vertices(tf)
        out = []
        for v in verts:
            xyz = _vec3(v)
            if xyz is not None:
                out.append(xyz)
        return out or None
    except Exception:  # noqa: BLE001
        return None


def extract_landmarks(world: Any, hero_actor: Any, roi_m: float, max_count: int) -> dict[str, Any]:
    """Read-only landmarks with MODEL-INDEPENDENT world coordinates for F6 reprojection.

    Sources (each carries its own `kind`): traffic-light actors, nearby vehicle/walker
    actors (center + bbox vertices), and map landmarks near the ego waypoint. All world
    coordinates come from CARLA transforms, never from the model output. Never mutates
    anything; distinguishes empty from failure.
    """
    out: dict[str, Any] = {"status": _OK, "roi_m": roi_m, "landmarks": []}
    if world is None:
        return {"status": _EMPTY, "reason": "WORLD_NONE", "landmarks": []}
    try:
        ego_loc = hero_actor.get_transform().location if hero_actor is not None else None
    except Exception:  # noqa: BLE001
        ego_loc = None
    collected: list[dict[str, Any]] = []

    def _dist(a: Any) -> Any:
        try:
            return math.sqrt(
                (a.x - ego_loc.x) ** 2 + (a.y - ego_loc.y) ** 2 + (a.z - ego_loc.z) ** 2
            )
        except Exception:  # noqa: BLE001
            return None

    # traffic-light + vehicle/walker actors within ROI
    try:
        actors = world.get_actors()
        out["actor_query_ok"] = True
    except Exception as exc:  # noqa: BLE001
        out["status"] = f"{_EXCEPTION}:{type(exc).__name__}"
        return out
    try:
        for a in actors:
            if len(collected) >= max_count:
                break
            try:
                tid = str(getattr(a, "type_id", ""))
            except Exception:  # noqa: BLE001
                tid = ""
            kind = None
            if "traffic_light" in tid:
                kind = "traffic_light"
            elif tid.startswith("vehicle"):
                kind = "actor_vehicle"
            elif tid.startswith("walker"):
                kind = "actor_walker"
            else:
                continue
            try:
                if getattr(a, "id", None) == getattr(hero_actor, "id", None):
                    continue
            except Exception:  # noqa: BLE001
                pass
            try:
                loc = a.get_transform().location
            except Exception:  # noqa: BLE001
                continue
            dist = _dist(loc) if ego_loc is not None else None
            if dist is not None and dist > roi_m:
                continue
            rec: dict[str, Any] = {"kind": kind, "type_id": tid, "distance_m": dist}
            try:
                rec["actor_id"] = int(a.id)
            except Exception:  # noqa: BLE001
                rec["actor_id"] = None
            rec["world_xyz"] = _vec3(loc)
            rec["bbox_world_vertices_xyz"] = _bbox_world_vertices(a)
            collected.append(rec)
    except Exception as exc:  # noqa: BLE001
        out["iter_status"] = f"{_EXCEPTION}:{type(exc).__name__}"

    # map landmarks near the ego lane (signs etc.), read-only
    try:
        if hero_actor is not None and len(collected) < max_count:
            cmap = world.get_map()
            wp = cmap.get_waypoint(hero_actor.get_transform().location)
            landmarks = wp.get_landmarks(float(roi_m)) if wp is not None else []
            for lm in landmarks:
                if len(collected) >= max_count:
                    break
                rec = {"kind": "map_landmark"}
                try:
                    rec["landmark_id"] = str(lm.id)
                except Exception:  # noqa: BLE001
                    rec["landmark_id"] = None
                try:
                    rec["landmark_name"] = str(lm.name)
                except Exception:  # noqa: BLE001
                    rec["landmark_name"] = None
                try:
                    rec["world_xyz"] = _vec3(lm.transform.location)
                except Exception:  # noqa: BLE001
                    rec["world_xyz"] = None
                collected.append(rec)
    except Exception:  # noqa: BLE001
        # map landmarks are optional; absence is not an error
        pass

    out["landmarks"] = collected
    out["count"] = len(collected)
    if not collected:
        out["status"] = _EMPTY
        out.setdefault("reason", "NO_LANDMARK_IN_ROI")
    return out


def identity_hashes() -> dict[str, Any]:
    """Route/config/checkpoint SHA-256 from env-provided paths, computed at build time.

    The launcher exports DRIVECLARIFY_CP3B_ROUTE_FILE / _CONFIG_FILE / _CHECKPOINT_FILE.
    Hashes are computed once (read-only) so every record can bind identity without a
    per-tick file read. Missing path => null + reason, never a fake value.
    """
    out: dict[str, Any] = {}
    for key, env in (
        ("route_file_hash", "DRIVECLARIFY_CP3B_ROUTE_FILE"),
        ("config_hash", "DRIVECLARIFY_CP3B_CONFIG_FILE"),
        ("checkpoint_hash", "DRIVECLARIFY_CP3B_CHECKPOINT_FILE"),
    ):
        path = os.environ.get(env)
        if not path:
            out[key] = None
            out[key + "_reason"] = "ENV_UNSET"
        elif not os.path.exists(path):
            out[key] = None
            out[key + "_reason"] = "PATH_ABSENT"
        else:
            out[key] = sha256_file(path)
            out[key + "_source"] = path
    return out
