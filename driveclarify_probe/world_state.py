"""CP1 world-state extraction + append-only writer (READ-ONLY, fail-open).

Separate from the frozen CP0 harness (record.py / verifier.py): CP1 writes a parallel
`world_state.jsonl`, one line per committed tick, keyed by the SAME observation_id as the
CP0 probe.jsonl. This keeps the CP0 path byte-identical (frozen verifier stays valid) while
adding the ego/camera/actor/light/map/route fields CP1 needs.

Contract:
- Every extractor is duck-typed and wrapped so a missing/raising getter degrades to a
  null value + an explicit per-field status ("success"/"empty"/"unsupported"/"exception"/
  "stale"), NEVER 0/false/SAFE. No mutation of any CARLA object. No world.tick/apply_control/
  route_planner.run_step/spawn/destroy is ever called here.
- The writer is append-only, one JSON object per line, allow_nan=False, fail-open (errors
  counted, never raised into the baseline path).

Analysis/schema version: driveclarify.world_state.v1
"""

from __future__ import annotations

import json
import math
import time
from typing import Any

WORLD_STATE_SCHEMA_VERSION = "driveclarify.world_state.v1"

_FIELD_OK = "success"
_FIELD_EMPTY = "empty"
_FIELD_UNSUPPORTED = "unsupported"
_FIELD_EXCEPTION = "exception"


def _vec3(v: Any) -> Any:
    """Read x/y/z off a carla Vector3D/Location-like object. Read-only."""
    try:
        return [float(v.x), float(v.y), float(v.z)]
    except Exception:  # noqa: BLE001
        return None


def _rot(r: Any) -> Any:
    try:
        return [float(r.roll), float(r.pitch), float(r.yaw)]
    except Exception:  # noqa: BLE001
        return None


def _matrix(transform: Any) -> Any:
    """carla.Transform.get_matrix() -> 4x4 list. Read-only."""
    try:
        m = transform.get_matrix()
        out = [[float(m[i][j]) for j in range(4)] for i in range(4)]
        return out
    except Exception:  # noqa: BLE001
        return None


def _finite_list(xs: Any) -> bool:
    try:
        return all(math.isfinite(float(x)) for x in xs)
    except Exception:  # noqa: BLE001
        return False


def extract_ego(hero_actor: Any) -> dict[str, Any]:
    """Read-only ego kinematics/transform. status per sub-read."""
    out: dict[str, Any] = {"status": _FIELD_OK}
    if hero_actor is None:
        return {"status": _FIELD_EMPTY, "reason": "HERO_ACTOR_NONE"}
    try:
        out["actor_id"] = int(getattr(hero_actor, "id", -1))
    except Exception:  # noqa: BLE001
        out["actor_id"] = None
    try:
        tf = hero_actor.get_transform()
        out["location_xyz"] = _vec3(tf.location)
        out["rotation_rpy_deg"] = _rot(tf.rotation)
        out["forward_vector_xyz"] = _vec3(tf.get_forward_vector())
        out["right_vector_xyz"] = _vec3(tf.get_right_vector())
        out["matrix_4x4"] = _matrix(tf)
    except Exception as exc:  # noqa: BLE001
        out["transform_status"] = f"{_FIELD_EXCEPTION}:{type(exc).__name__}"
    for name, getter in (
        ("velocity_world_mps_xyz", "get_velocity"),
        ("acceleration_world_mps2_xyz", "get_acceleration"),
        ("angular_velocity_degps_xyz", "get_angular_velocity"),
    ):
        try:
            out[name] = _vec3(getattr(hero_actor, getter)())
        except Exception as exc:  # noqa: BLE001
            out[name] = None
            out[name + "_status"] = f"{_FIELD_EXCEPTION}:{type(exc).__name__}"
    # forward speed scalar (norm of velocity) — diagnostic only
    v = out.get("velocity_world_mps_xyz")
    if v and _finite_list(v):
        out["speed_world_mps"] = math.sqrt(sum(c * c for c in v))
    try:
        bb = hero_actor.bounding_box
        out["bbox"] = {
            "location_xyz": _vec3(bb.location),
            "extent_xyz": _vec3(bb.extent),
            "rotation_rpy_deg": _rot(bb.rotation),
        }
    except Exception:  # noqa: BLE001
        out["bbox"] = None
    return out


def extract_camera(intrinsics_fn: Any, extrinsics_fn: Any, width: int, height: int, fov: float) -> dict[str, Any]:
    """Read camera K (3x3) and extrinsics (4x4) from the agent's own pure functions.

    intrinsics_fn/extrinsics_fn are team_code.simlingo_utils.get_camera_intrinsics /
    get_camera_extrinsics (or None). We do NOT modify baseline preprocessing; this only
    records the same calibration the model input already uses.
    """
    out: dict[str, Any] = {"status": _FIELD_OK, "width": width, "height": height, "fov_deg": fov}
    try:
        if intrinsics_fn is None:
            out["K_status"] = _FIELD_UNSUPPORTED
        else:
            k = intrinsics_fn(width, height, fov)
            out["K_3x3"] = [[float(k[i][j]) for j in range(3)] for i in range(3)]
    except Exception as exc:  # noqa: BLE001
        out["K_status"] = f"{_FIELD_EXCEPTION}:{type(exc).__name__}"
    try:
        if extrinsics_fn is None:
            out["extrinsics_status"] = _FIELD_UNSUPPORTED
        else:
            e = extrinsics_fn()
            out["extrinsics_4x4"] = [[float(e[i][j]) for j in range(4)] for i in range(4)]
            out["extrinsics_direction_label"] = "UNRESOLVED_REQUIRES_F6"
    except Exception as exc:  # noqa: BLE001
        out["extrinsics_status"] = f"{_FIELD_EXCEPTION}:{type(exc).__name__}"
    return out


def _distance(a: Any, b: Any) -> Any:
    try:
        return math.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2)
    except Exception:  # noqa: BLE001
        return None


def extract_actors(world: Any, hero_actor: Any, roi_m: float, max_count: int) -> dict[str, Any]:
    """Read-only bounded-ROI actor snapshot. Distinguishes query-empty from query-failure."""
    out: dict[str, Any] = {"actor_query_ok": False, "roi_m": roi_m, "max_count": max_count, "actors": []}
    if world is None:
        out["status"] = _FIELD_EMPTY
        out["reason"] = "WORLD_NONE"
        return out
    try:
        all_actors = world.get_actors()
        out["actor_query_ok"] = True
    except Exception as exc:  # noqa: BLE001
        out["status"] = f"{_FIELD_EXCEPTION}:{type(exc).__name__}"
        return out
    try:
        ego_loc = hero_actor.get_transform().location if hero_actor is not None else None
    except Exception:  # noqa: BLE001
        ego_loc = None
    vehicles = pedestrians = 0
    collected = []
    try:
        for a in all_actors:
            try:
                tid = str(getattr(a, "type_id", ""))
            except Exception:  # noqa: BLE001
                tid = ""
            if not (tid.startswith("vehicle") or tid.startswith("walker")):
                continue
            try:
                if getattr(a, "id", None) == getattr(hero_actor, "id", None):
                    continue
            except Exception:  # noqa: BLE001
                pass
            dist = None
            if ego_loc is not None:
                try:
                    dist = _distance(a.get_transform().location, ego_loc)
                except Exception:  # noqa: BLE001
                    dist = None
            if dist is not None and dist > roi_m:
                continue
            if tid.startswith("vehicle"):
                vehicles += 1
            else:
                pedestrians += 1
            if len(collected) < max_count:
                rec: dict[str, Any] = {"type_id": tid, "distance_m": dist}
                try:
                    rec["id"] = int(a.id)
                except Exception:  # noqa: BLE001
                    rec["id"] = None
                try:
                    tf = a.get_transform()
                    rec["location_xyz"] = _vec3(tf.location)
                    rec["rotation_rpy_deg"] = _rot(tf.rotation)
                except Exception:  # noqa: BLE001
                    rec["location_xyz"] = None
                try:
                    rec["velocity_world_mps_xyz"] = _vec3(a.get_velocity())
                except Exception:  # noqa: BLE001
                    rec["velocity_world_mps_xyz"] = None
                try:
                    bb = a.bounding_box
                    rec["bbox"] = {"location_xyz": _vec3(bb.location), "extent_xyz": _vec3(bb.extent)}
                except Exception:  # noqa: BLE001
                    rec["bbox"] = None
                collected.append(rec)
    except Exception as exc:  # noqa: BLE001
        out["iter_status"] = f"{_FIELD_EXCEPTION}:{type(exc).__name__}"
    out["actors"] = collected
    out["counts"] = {"vehicles_in_roi": vehicles, "pedestrians_in_roi": pedestrians, "logged": len(collected)}
    out["status"] = _FIELD_OK if collected else _FIELD_EMPTY
    return out


def extract_traffic_lights(world: Any, hero_actor: Any, roi_m: float, max_count: int) -> dict[str, Any]:
    """Read-only traffic-light state + trigger volume. Never defaults to Green."""
    out: dict[str, Any] = {"query_ok": False, "lights": []}
    if world is None:
        out["status"] = _FIELD_EMPTY
        out["reason"] = "WORLD_NONE"
        return out
    try:
        lights = world.get_actors().filter("*traffic_light*")
        out["query_ok"] = True
    except Exception as exc:  # noqa: BLE001
        out["status"] = f"{_FIELD_EXCEPTION}:{type(exc).__name__}"
        return out
    try:
        ego_loc = hero_actor.get_transform().location if hero_actor is not None else None
    except Exception:  # noqa: BLE001
        ego_loc = None
    collected = []
    try:
        for lt in lights:
            dist = None
            if ego_loc is not None:
                try:
                    dist = _distance(lt.get_transform().location, ego_loc)
                except Exception:  # noqa: BLE001
                    dist = None
            if dist is not None and dist > roi_m:
                continue
            if len(collected) >= max_count:
                break
            rec: dict[str, Any] = {"distance_m": dist}
            try:
                rec["id"] = int(lt.id)
            except Exception:  # noqa: BLE001
                rec["id"] = None
            try:
                rec["state"] = str(lt.state)  # enum -> str; never coerced to Green
            except Exception:  # noqa: BLE001
                rec["state"] = None
                rec["state_status"] = _FIELD_UNSUPPORTED
            try:
                tf = lt.get_transform()
                rec["location_xyz"] = _vec3(tf.location)
            except Exception:  # noqa: BLE001
                rec["location_xyz"] = None
            try:
                tv = lt.trigger_volume
                rec["trigger_volume"] = {"location_xyz": _vec3(tv.location), "extent_xyz": _vec3(tv.extent)}
            except Exception:  # noqa: BLE001
                rec["trigger_volume"] = None
            collected.append(rec)
    except Exception as exc:  # noqa: BLE001
        out["iter_status"] = f"{_FIELD_EXCEPTION}:{type(exc).__name__}"
    out["lights"] = collected
    out["status"] = _FIELD_OK if collected else _FIELD_EMPTY
    return out


def extract_map_waypoint(world: Any, hero_actor: Any) -> dict[str, Any]:
    """Read-only current-lane waypoint + markings. No mutation of map/route."""
    out: dict[str, Any] = {"status": _FIELD_OK}
    if world is None or hero_actor is None:
        return {"status": _FIELD_EMPTY, "reason": "WORLD_OR_HERO_NONE"}
    try:
        carla_map = world.get_map()
    except Exception as exc:  # noqa: BLE001
        return {"status": f"{_FIELD_EXCEPTION}:{type(exc).__name__}", "reason": "GET_MAP_FAILED"}
    try:
        loc = hero_actor.get_transform().location
        wp = carla_map.get_waypoint(loc)
    except Exception as exc:  # noqa: BLE001
        return {"status": f"{_FIELD_EXCEPTION}:{type(exc).__name__}", "reason": "GET_WAYPOINT_FAILED"}
    if wp is None:
        return {"status": _FIELD_EMPTY, "reason": "NO_WAYPOINT"}
    for name, attr in (
        ("road_id", "road_id"),
        ("lane_id", "lane_id"),
        ("s", "s"),
        ("lane_width", "lane_width"),
        ("is_junction", "is_junction"),
    ):
        try:
            out[name] = getattr(wp, attr)
        except Exception:  # noqa: BLE001
            out[name] = None
    try:
        out["lane_type"] = str(wp.lane_type)
    except Exception:  # noqa: BLE001
        out["lane_type"] = None
    try:
        out["waypoint_location_xyz"] = _vec3(wp.transform.location)
        out["waypoint_rotation_rpy_deg"] = _rot(wp.transform.rotation)
    except Exception:  # noqa: BLE001
        out["waypoint_location_xyz"] = None
    for side in ("left_lane_marking", "right_lane_marking"):
        try:
            lm = getattr(wp, side)
            out[side] = {"type": str(lm.type), "color": str(lm.color)}
        except Exception:  # noqa: BLE001
            out[side] = None
    return out


class WorldStateWriter:
    """Append-only, one JSON object per line, allow_nan=False, fail-open."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._fh = open(path, "a", encoding="utf-8")
        self.written = 0
        self.errors = 0

    def write(self, record: dict[str, Any]) -> None:
        try:
            line = json.dumps(record, ensure_ascii=False, allow_nan=False, sort_keys=True)
            self._fh.write(line + "\n")
            self._fh.flush()
            self.written += 1
        except Exception:  # noqa: BLE001
            self.errors += 1

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:  # noqa: BLE001
            self.errors += 1
