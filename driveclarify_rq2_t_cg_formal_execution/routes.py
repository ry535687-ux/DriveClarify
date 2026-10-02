"""Deterministic XML serialization and static admission for frozen scenes."""

from __future__ import annotations

import hashlib
import math
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Mapping, Sequence

from driveclarify_rq2_t.measurement import canonical_sha256
from driveclarify_rq2_t_e2_v3.usc_admission import project_point_to_polyline

from .specs import accepted_scenes, scene_by_code
from .route_binding_v2 import binding_contract, executable_keypoints, source_polyline


SCENARIO_TYPE = "DriveClarifyRQ2TCGFormalScenario"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _yaw(left: Mapping[str, float], right: Mapping[str, float]) -> float:
    return math.degrees(math.atan2(right["y"] - left["y"], right["x"] - left["x"]))


def materialize_route(scene: Mapping[str, Any], output: Path) -> Mapping[str, Any]:
    binding = binding_contract(scene)
    points = executable_keypoints(scene)
    routes = ET.Element("routes")
    route = ET.SubElement(routes, "route", {
        "id": "RQ2TCG-FORMAL-" + scene["scene_code"], "town": scene["route"]["town"],
    })
    waypoints = ET.SubElement(route, "waypoints")
    for point in points:
        ET.SubElement(waypoints, "position", {axis: repr(float(point[axis])) for axis in ("x", "y", "z")})
    scenarios = ET.SubElement(route, "scenarios")
    scenario = ET.SubElement(scenarios, "scenario", {
        "name": scene["formal_scene_id"], "type": SCENARIO_TYPE,
    })
    first = points[0]
    # Scenario admission owns the trigger orientation at the actual route
    # start.  Sparse executable GRP keypoints may place `points[1]` far beyond
    # one or more junctions, so their chord is not a valid launch yaw.
    # Scientific source geometry is unchanged and supplies the exact first
    # native segment for this representation-only value.
    second = source_polyline(scene)[1]
    ET.SubElement(scenario, "trigger_point", {
        "x": repr(float(first["x"])), "y": repr(float(first["y"])),
        "z": repr(float(first["z"])), "yaw": repr(_yaw(first, second)),
    })
    ET.SubElement(scenario, "rq2_t_cg_formal", {
        "formal_scene_id": scene["formal_scene_id"],
        "formal_scene_digest": scene["formal_scene_digest"],
        "route_spec_digest": scene["route"]["route_spec_digest"],
        "route_binding_v2_digest": binding["route_binding_v2_digest"],
    })
    output.parent.mkdir(parents=True, exist_ok=True)
    tree = ET.ElementTree(routes)
    tree.write(str(output), encoding="utf-8", xml_declaration=True)
    receipt = static_route_admission(output, scene)
    if receipt["status"] != "PASS_STATIC_FORMAL_ROUTE_ADMISSION":
        raise RuntimeError("RQ2_T_CG_FORMAL_ROUTE_SERIALIZATION_FAILED:" + scene["scene_code"])
    return receipt


def static_route_admission(path: Path, scene: Mapping[str, Any]) -> Mapping[str, Any]:
    reasons = []
    try:
        route = ET.parse(str(path)).getroot().find("route")
        if route is None:
            raise ValueError("ROUTE_MISSING")
        scenario_rows = route.findall("./scenarios/scenario")
        if len(scenario_rows) != 1:
            raise ValueError("SCENARIO_COUNT_NOT_ONE")
        scenario = scenario_rows[0]
        trigger = scenario.find("trigger_point")
        params = scenario.find("rq2_t_cg_formal")
        if trigger is None or params is None:
            raise ValueError("FORMAL_PARAMETERS_MISSING")
        points = [[float(row.attrib[key]) for key in ("x", "y", "z")] for row in route.findall("./waypoints/position")]
        trigger_point = [float(trigger.attrib[key]) for key in ("x", "y", "z")]
        trigger_yaw = float(trigger.attrib["yaw"])
    except (ET.ParseError, OSError, TypeError, ValueError) as exc:
        return {"status": "FAIL_STATIC_FORMAL_ROUTE_ADMISSION", "reason_codes": [str(exc)]}
    binding = binding_contract(scene)
    expected = [[float(point[key]) for key in ("x", "y", "z")] for point in executable_keypoints(scene)]
    if points != expected:
        reasons.append("WAYPOINT_SERIALIZATION_MISMATCH")
    projection = dict(project_point_to_polyline(trigger_point, points))
    if projection["distance_m"] > 0.05:
        reasons.append("TRIGGER_ROUTE_INTERSECTION_ABSENT")
    source = source_polyline(scene)
    expected_trigger_yaw = _yaw(source[0], source[1])
    yaw_delta = (trigger_yaw - expected_trigger_yaw + 180.0) % 360.0 - 180.0
    if abs(yaw_delta) > 1e-9:
        reasons.append("TRIGGER_INITIAL_SOURCE_HEADING_MISMATCH")
    if route.attrib.get("town") != scene["route"]["town"]:
        reasons.append("TOWN_MISMATCH")
    if scenario.attrib.get("type") != SCENARIO_TYPE:
        reasons.append("SCENARIO_TYPE_MISMATCH")
    if params.attrib.get("formal_scene_id") != scene["formal_scene_id"]:
        reasons.append("SCENE_ID_MISMATCH")
    if params.attrib.get("formal_scene_digest") != scene["formal_scene_digest"]:
        reasons.append("SCENE_DIGEST_MISMATCH")
    if params.attrib.get("route_spec_digest") != scene["route"]["route_spec_digest"]:
        reasons.append("ROUTE_SPEC_DIGEST_MISMATCH")
    if params.attrib.get("route_binding_v2_digest") != binding["route_binding_v2_digest"]:
        reasons.append("ROUTE_BINDING_V2_DIGEST_MISMATCH")
    threshold = float(scene["commitment"]["threshold_m"])
    if float(scene["route"]["route_length_m"]) <= threshold + 1.0:
        reasons.append("COMMITMENT_HORIZON_ROUTE_TOO_SHORT")
    for event in scene["events"]:
        activation = event["activation"]
        if not (0.0 <= float(activation["start_inclusive_m"]) < float(activation["end_exclusive_m"]) < threshold):
            reasons.append("EVENT_OUTSIDE_PRECOMMITMENT_ROUTE:" + event["event_id"])
        if event["reads_view"] or event["reads_outcome"] or event["reads_passenger_intent"]:
            reasons.append("EVENT_OWNER_NOT_INDEPENDENT:" + event["event_id"])
    result = {
        "schema_version": "driveclarify.rq2_t_cg.formal_route_admission.v1",
        "status": "PASS_STATIC_FORMAL_ROUTE_ADMISSION" if not reasons else "FAIL_STATIC_FORMAL_ROUTE_ADMISSION",
        "reason_codes": reasons, "scene_code": scene["scene_code"],
        "formal_scene_id": scene["formal_scene_id"], "formal_scene_digest": scene["formal_scene_digest"],
        "route_spec_digest": scene["route"]["route_spec_digest"],
        "route_binding_v2_digest": binding["route_binding_v2_digest"],
        "route_binding_v2": binding,
        "native_route_path": str(Path(path).resolve()), "native_route_sha256": _sha(path),
        "route_total_arc_length_m": scene["route"]["route_length_m"],
        "certified_source_polyline_coordinates": list(source_polyline(scene)),
        "native_executable_keypoint_coordinates": list(executable_keypoints(scene)),
        "commitment_threshold_m": threshold, "trigger_projection": projection,
        "trigger_yaw_deg": trigger_yaw, "expected_trigger_yaw_deg": expected_trigger_yaw,
        "trigger_yaw_delta_deg": yaw_delta,
        "formal_seed_values_generated": 0, "formal_scientific_exposures": 0,
    }
    result["admission_digest"] = canonical_sha256(result)
    return result


def materialize_all(output_dir: Path) -> Sequence[Mapping[str, Any]]:
    rows = []
    for scene in accepted_scenes():
        rows.append(materialize_route(scene, output_dir / (scene["scene_code"] + ".xml")))
    return rows


__all__ = ["SCENARIO_TYPE", "materialize_all", "materialize_route", "static_route_admission"]
