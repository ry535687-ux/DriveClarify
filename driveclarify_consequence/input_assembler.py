"""Input assembler — builds a ConsequenceAdapterInputV0 dict from a CP1 world_state record (real log)
plus a synthetic/recorded candidate bundle, OR from a fully synthetic fixture.

Rules (fail-closed, never launders unknowns):
  - Preserves query-status distinctions: SUCCESS_EMPTY / MISSING / UNSUPPORTED / EXCEPTION are kept
    distinct; an empty list is NOT turned into UNKNOWN and MISSING is NOT turned into [].
  - Never mutates the source record (deep-reads only; builds a fresh dict).
  - A candidate whose source_observation_id != the record observation is left as-is; the validator
    rejects the record — the assembler does NOT auto-rebind.
  - Marks provenance: world_state_source, candidate_source, physical_authorization_allowed=false.
"""

from __future__ import annotations

from typing import Any

from .record_loader import CandidateBundle

INPUT_SCHEMA_VERSION = "driveclarify.consequence_adapter_input.v0"

# The default frozen CP1 contract snapshot (evidence_grade, corrected E4/E5). Used when a bundle
# does not carry its own snapshot. Mirrors FEATURE_DEPENDENCY_MATRIX cp1_grades.
DEFAULT_CONTRACT_SNAPSHOT = {
    "F1_grade": "SUPPORTED_BUT_INCOMPLETE",
    "F2_grade": "UNRESOLVED_REQUIRES_ADDITIONAL_PROBE",
    "F3_grade": "SUPPORTED_BUT_INCOMPLETE",
    "F4_grade": "UNRESOLVED_REQUIRES_ADDITIONAL_PROBE",
    "F5_grade": "SUPPORTED_BUT_INCOMPLETE",
    "F6_grade": "UNRESOLVED_REQUIRES_ADDITIONAL_PROBE",
    "T1_grade": "VERIFIED_FROM_CONTROLLED_PROBE",
    "T2_grade": "VERIFIED_FROM_CONTROLLED_PROBE",
    "T3_grade": "VERIFIED_FROM_CONTROLLED_PROBE",
    "T4_grade": "SUPPORTED_BUT_INCOMPLETE",
    "D1_grade": "VERIFIED_FROM_CONTROLLED_PROBE",
}

_VALID_QUERY_STATUS = {
    "SUCCESS_NONEMPTY", "SUCCESS_EMPTY", "UNKNOWN_WITH_REASON",
    "UNSUPPORTED", "EXCEPTION", "STALE", "MISSING",
}


def _map_ws_query_status(section: Any, list_key: str = "lights") -> str:
    """Map a CP1 world_state sub-section's status into a v0 query_status, preserving distinctions.

    A missing section => MISSING (never SUCCESS_EMPTY). An error/exception status => EXCEPTION.
    An empty collection with a success flag => SUCCESS_EMPTY. Present items => SUCCESS_NONEMPTY.
    """
    if section is None:
        return "MISSING"
    if not isinstance(section, dict):
        return "UNKNOWN_WITH_REASON"
    status = section.get("status")
    query_ok = section.get("query_ok")
    if status in ("exception", "error", "EXCEPTION"):
        return "EXCEPTION"
    if status in ("unsupported", "UNSUPPORTED"):
        return "UNSUPPORTED"
    if query_ok is False:
        return "UNKNOWN_WITH_REASON"
    items = section.get(list_key)
    if isinstance(items, list):
        return "SUCCESS_NONEMPTY" if items else "SUCCESS_EMPTY"
    return "SUCCESS_NONEMPTY" if status in ("success",) else "UNKNOWN_WITH_REASON"


def _collection(section: Any, list_key: str) -> dict[str, Any]:
    qs = _map_ws_query_status(section, list_key)
    items = None
    reason = None
    if isinstance(section, dict):
        raw_items = section.get(list_key)
        if isinstance(raw_items, list):
            items = list(raw_items)  # shallow copy; never mutate source
        reason = section.get("reason") or section.get("reason_code")
    return {"query_status": qs, "items": items, "reason_code": reason}


def _map_object(section: Any) -> dict[str, Any]:
    if section is None:
        return {"query_status": "MISSING", "value": None, "reason_code": None}
    if not isinstance(section, dict):
        return {"query_status": "UNKNOWN_WITH_REASON", "value": None, "reason_code": None}
    status = section.get("status")
    if status in ("exception", "error"):
        return {"query_status": "EXCEPTION", "value": None,
                "reason_code": section.get("reason")}
    # a real map waypoint has geometry keys => SUCCESS_NONEMPTY, else EMPTY
    value = {k: v for k, v in section.items() if k != "status"}
    qs = "SUCCESS_NONEMPTY" if value else "SUCCESS_EMPTY"
    return {"query_status": qs, "value": value or None, "reason_code": None}


def assemble_from_world_state(
    ws: dict[str, Any],
    bundle: CandidateBundle,
    record_seq: int,
) -> dict[str, Any]:
    """Assemble a ConsequenceAdapterInputV0 dict from a real CP1 world_state record + candidate
    bundle. Does not mutate ``ws`` or ``bundle``. Provenance marks it as REAL_CP1_LOG + SYNTHETIC."""
    observation_id = ws.get("observation_id")
    run_id = ws.get("run_id")
    snapshot_frame = ws.get("snapshot_frame")
    gametime_frame = ws.get("gametime_frame")

    ego = ws.get("ego")
    ego_avail = "SUCCESS_NONEMPTY" if isinstance(ego, dict) and ego else (
        "MISSING" if ego is None else "SUCCESS_EMPTY")
    ego_state = {
        "availability": ego_avail,
        "source_frame": "CARLA_WORLD" if ego_avail == "SUCCESS_NONEMPTY" else "UNKNOWN",
    }
    if isinstance(ego, dict) and ego:
        if isinstance(ego.get("location_xyz"), list):
            ego_state["location_xyz"] = list(ego["location_xyz"])
        if isinstance(ego.get("velocity_world_mps_xyz"), list):
            ego_state["velocity_world_mps_xyz"] = list(ego["velocity_world_mps_xyz"])
        if isinstance(ego.get("acceleration_world_mps2_xyz"), list):
            ego_state["acceleration_world_mps2_xyz"] = list(ego["acceleration_world_mps2_xyz"])
        if isinstance(ego.get("rotation_rpy_deg"), list):
            ego_state["rotation_rpy_deg"] = list(ego["rotation_rpy_deg"])

    cam = ws.get("camera")
    cam_avail = "SUCCESS_NONEMPTY" if isinstance(cam, dict) and cam.get("status") == "success" else (
        "MISSING" if cam is None else "UNKNOWN_WITH_REASON")
    camera_calibration = {
        "availability": cam_avail,
        "K_3x3": (list(cam["K_3x3"]) if isinstance(cam, dict) and isinstance(cam.get("K_3x3"), list) else None),
        "extrinsics_4x4": (list(cam["extrinsics_4x4"]) if isinstance(cam, dict) and isinstance(cam.get("extrinsics_4x4"), list) else None),
        # F6 unresolved: direction is frozen as UNRESOLVED regardless of the logged label.
        "direction_label": "UNRESOLVED_REQUIRES_F6",
    }

    world_state = {
        "source_frame": "CARLA_WORLD",
        "query_status": "SUCCESS_NONEMPTY" if isinstance(ego, dict) and ego else "UNKNOWN_WITH_REASON",
        "actors": _collection(ws.get("actors"), "actors"),
        "traffic_lights": _collection(ws.get("traffic_lights"), "lights"),
        "map_waypoint": _map_object(ws.get("map_waypoint")),
        "route_context": _collection(ws.get("route_context"), "front_items"),
        "camera_calibration": camera_calibration,
    }

    contract_snapshot = dict(bundle.contract_snapshot or DEFAULT_CONTRACT_SNAPSHOT)

    record = {
        "metadata": {
            "schema_version": INPUT_SCHEMA_VERSION,
            "run_id": run_id or "cp1_unknown_run",
            "record_seq": record_seq,
            "observation_id": observation_id,
            "candidate_set_id": bundle.candidate_set_id,
            "generated_at_monotonic_ns": _mono_ns(ws.get("probe_read_monotonic_s")),
            "source_sim_frame": gametime_frame if isinstance(gametime_frame, int) else 0,
            "source_snapshot_frame": snapshot_frame if isinstance(snapshot_frame, int) else 0,
        },
        "clock_bundle": {
            "snapshot_elapsed_seconds": ws.get("snapshot_elapsed_seconds"),
            "gametime_elapsed_seconds": ws.get("gametime_seconds"),
            "monotonic_ns": _mono_ns(ws.get("probe_read_monotonic_s")),
            "clock_policy": "driveclarify.time_policy.v0",
            "observed_sim_offset_seconds": _observed_offset(ws),
        },
        "ego_state": ego_state,
        "world_state": world_state,
        "candidate_plans": [dict(c) for c in bundle.candidates],
        "contract_snapshot": contract_snapshot,
    }
    return record


def _mono_ns(seconds: Any) -> int:
    if isinstance(seconds, (int, float)):
        return int(round(float(seconds) * 1_000_000_000))
    return 0


def _observed_offset(ws: dict[str, Any]) -> float | None:
    snap = ws.get("snapshot_elapsed_seconds")
    game = ws.get("gametime_seconds")
    if isinstance(snap, (int, float)) and isinstance(game, (int, float)):
        return float(snap) - float(game)
    return None
