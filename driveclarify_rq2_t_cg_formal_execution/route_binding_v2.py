"""Prospective V2 binding from frozen scientific polylines to native GRP inputs.

The frozen scene polyline remains the sole scientific progress coordinate.  This
module changes only the XML keypoint representation consumed by Bench2Drive's
pairwise GlobalRoutePlanner interpolation.  Town10HD LMK polylines are dense
samples through a junction; submitting every sample as a new GRP origin/destination
pair can select unrelated topology.  V2 therefore supplies one canonical GRP pair
and a small terminal-only padding point from the same already-certified source
route.  The padding is after every event and commitment threshold and never enters
the scientific progress calculation.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Mapping, Sequence

from driveclarify_rq2_t.measurement import canonical_sha256


ROOT = Path(__file__).resolve().parents[1]
LMK_TERMINAL_PADDING_SOURCE_POINTS = 4
CANONICAL_EXECUTABLE_ROUTE_OWNER = "BENCH2DRIVE_GLOBAL_ROUTE_PLANNER_TRACE_V2"


def _xyz(row: Mapping[str, Any]) -> Mapping[str, float]:
    return {axis: float(row[axis]) for axis in ("x", "y", "z")}


def polyline_length(points: Sequence[Mapping[str, Any]]) -> float:
    return sum(
        math.dist(
            tuple(float(left[axis]) for axis in ("x", "y", "z")),
            tuple(float(right[axis]) for axis in ("x", "y", "z")),
        )
        for left, right in zip(points, points[1:])
    )


def source_polyline(scene: Mapping[str, Any]) -> tuple[Mapping[str, float], ...]:
    return tuple(_xyz(row) for row in scene["route"]["waypoints"])


def _source_route_points(scene: Mapping[str, Any]) -> tuple[Mapping[str, float], ...]:
    path = ROOT / str(scene["route"]["mechanism_polyline_source"])
    rows = ET.parse(str(path)).getroot().findall("./route/waypoints/position")
    return tuple(
        {axis: float(row.attrib[axis]) for axis in ("x", "y", "z")}
        for row in rows
    )


def executable_keypoints(scene: Mapping[str, Any]) -> tuple[Mapping[str, float], ...]:
    source = source_polyline(scene)
    # The prospective ORD redesign carries an exact dense Town12 GRP trace.
    # Feeding every ~1 m sample back to Bench2Drive as an independent
    # origin/destination pair can make boundary samples snap to a different
    # connector and inflate the native evaluator route.  The offline topology
    # certificate proves that one start/end GRP pair follows the complete
    # J1-straight/J2-turn route, so use that representation only for the new
    # redesign schema.  The dense source remains the unchanged scientific
    # progress coordinate.
    if scene.get("schema_version") in (
        "driveclarify.rq2_t_cg.ord_async_prospective_redesign_scene.v1",
        "driveclarify.rq2_t_cg.ord_late_reveal_replacement_scene.v1",
    ):
        return (source[0], source[-1])
    if not str(scene["scene_code"]).startswith("LMK-"):
        return source
    source_slice = scene["route"].get("source_slice_half_open")
    if not isinstance(source_slice, list) or len(source_slice) != 2:
        raise RuntimeError("RQ2_T_CG_V2_LMK_SOURCE_SLICE_MISSING")
    full = _source_route_points(scene)
    terminal_index = int(source_slice[1]) - 1 + LMK_TERMINAL_PADDING_SOURCE_POINTS
    if terminal_index >= len(full):
        raise RuntimeError("RQ2_T_CG_V2_LMK_TERMINAL_PADDING_UNAVAILABLE")
    if source[0] != full[int(source_slice[0])] or source[-1] != full[int(source_slice[1]) - 1]:
        raise RuntimeError("RQ2_T_CG_V2_LMK_FROZEN_SOURCE_BINDING_MISMATCH")
    return (source[0], full[terminal_index])


def binding_contract(scene: Mapping[str, Any]) -> Mapping[str, Any]:
    source = source_polyline(scene)
    executable = executable_keypoints(scene)
    stable = {
        "schema_version": "driveclarify.rq2_t_cg.route_binding.v2",
        "scene_code": scene["scene_code"],
        "formal_scene_id": scene["formal_scene_id"],
        "formal_scene_digest": scene["formal_scene_digest"],
        "frozen_route_spec_digest": scene["route"]["route_spec_digest"],
        "scientific_progress_owner": "FROZEN_CERTIFIED_SOURCE_POLYLINE_V1_UNCHANGED",
        "canonical_executable_route_owner": CANONICAL_EXECUTABLE_ROUTE_OWNER,
        "source_polyline_coordinates": list(source),
        "source_polyline_point_count": len(source),
        "executable_keypoint_coordinates": list(executable),
        "executable_keypoint_count": len(executable),
        "terminal_padding_source_points": (
            LMK_TERMINAL_PADDING_SOURCE_POINTS
            if str(scene["scene_code"]).startswith("LMK-") else 0
        ),
        "dense_native_grp_pair_collapse": (
            scene.get("schema_version") in (
                "driveclarify.rq2_t_cg.ord_async_prospective_redesign_scene.v1",
                "driveclarify.rq2_t_cg.ord_late_reveal_replacement_scene.v1",
            )
        ),
        "terminal_padding_is_after_all_events_and_commitment": all(
            float(event["activation"]["end_exclusive_m"])
            < float(scene["commitment"]["threshold_m"])
            < float(scene["route"]["route_length_m"])
            for event in scene["events"]
        ),
        "scientific_progress_geometry_changed": False,
        "event_timing_changed": False,
        "commitment_changed": False,
        "route_planner_behavior_changed": False,
    }
    value = dict(stable)
    # The accepted manifest owns the scientific route-length float.  Do not
    # make a cross-Python libm last-bit difference part of the binding digest.
    value["source_polyline_length_m"] = float(scene["route"]["route_length_m"])
    value["source_polyline_recomputed_length_m"] = polyline_length(source)
    value["executable_keypoint_polyline_length_m"] = polyline_length(executable)
    value["route_binding_v2_digest"] = canonical_sha256(stable)
    return value


__all__ = [
    "CANONICAL_EXECUTABLE_ROUTE_OWNER",
    "LMK_TERMINAL_PADDING_SOURCE_POINTS",
    "binding_contract",
    "executable_keypoints",
    "polyline_length",
    "source_polyline",
]
