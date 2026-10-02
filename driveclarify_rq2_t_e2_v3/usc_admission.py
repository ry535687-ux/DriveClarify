"""Pure pre-launch USC route/trigger admission and activation monitoring."""

from __future__ import annotations

import hashlib
import json
import math
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence


USC_SCENE_ID = "DEV-V3-USC-CONTROL-A"
SCENARIO_TYPE = "DriveClarifyRQ2TE2V3EngineeringScenario"
SCENARIO_OWNER = "driveclarify_rq2_t_e2_v3.native_scenario.DriveClarifyRQ2TE2V3EngineeringScenario"
TRIGGER_RADIUS_M = 2.0
ROUTE_INTERSECTION_TOLERANCE_M = 0.25
ROUTE_DIRECTION_MINIMUM_DOT = 0.90


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _point(element: ET.Element) -> tuple[float, float, float]:
    return tuple(float(element.attrib[name]) for name in ("x", "y", "z"))


def _polyline_lengths(points: Sequence[Sequence[float]]) -> list[float]:
    values = [0.0]
    for left, right in zip(points, points[1:]):
        values.append(values[-1] + math.dist(tuple(left), tuple(right)))
    return values


def project_point_to_polyline(
    point: Sequence[float], points: Sequence[Sequence[float]]
) -> Mapping[str, Any]:
    if len(points) < 2:
        raise ValueError("USC_ROUTE_POLYLINE_REQUIRES_TWO_POINTS")
    cumulative = _polyline_lengths(points)
    best: Optional[dict[str, Any]] = None
    for index, (left, right) in enumerate(zip(points, points[1:])):
        delta = tuple(float(right[axis]) - float(left[axis]) for axis in range(3))
        length_sq = sum(value * value for value in delta)
        if length_sq <= 1e-12:
            continue
        projection = sum(
            (float(point[axis]) - float(left[axis])) * delta[axis] for axis in range(3)
        ) / length_sq
        clipped = max(0.0, min(1.0, projection))
        nearest = tuple(float(left[axis]) + clipped * delta[axis] for axis in range(3))
        distance = math.dist(tuple(float(value) for value in point), nearest)
        row = {
            "segment_index": index,
            "segment_fraction": clipped,
            "nearest_xyz": list(nearest),
            "distance_m": distance,
            "route_arc_length_m": cumulative[index] + clipped * math.sqrt(length_sq),
            "segment_direction_xyz": [value / math.sqrt(length_sq) for value in delta],
        }
        if best is None or (row["distance_m"], row["segment_index"]) < (best["distance_m"], best["segment_index"]):
            best = row
    if best is None:
        raise ValueError("USC_ROUTE_POLYLINE_ALL_SEGMENTS_DEGENERATE")
    return best


def _owner_symbol_present(root: Path) -> bool:
    source = root / "driveclarify_rq2_t_e2_v3/native_scenario.py"
    return source.is_file() and ("class " + SCENARIO_TYPE + "(") in source.read_text(encoding="utf-8")


def static_route_trigger_admission(
    *,
    root: Path,
    route_path: Path,
    binding: Mapping[str, Any],
    expected_route_sha256: Optional[str] = None,
    expected_scene_configuration_sha256: Optional[str] = None,
) -> Mapping[str, Any]:
    """Fail-closed route/config/trigger admission without starting CARLA."""

    root = Path(root).resolve()
    route_path = Path(route_path).resolve()
    reasons: list[str] = []
    if not route_path.is_file():
        return {
            "schema_version": "driveclarify.e2_v3.usc_route_trigger_admission.v1",
            "status": "FAIL_STATIC_ROUTE_TRIGGER_ADMISSION",
            "reason_codes": ["ROUTE_FILE_MISSING"],
        }
    route_digest = sha256(route_path)
    scene_digest = str(binding.get("scene_configuration_sha256") or "")
    if expected_route_sha256 is not None and route_digest != str(expected_route_sha256):
        reasons.append("ROUTE_DIGEST_MISMATCH")
    if expected_scene_configuration_sha256 is not None and scene_digest != str(expected_scene_configuration_sha256):
        reasons.append("SCENARIO_CONFIG_DIGEST_MISMATCH")
    try:
        document = ET.parse(str(route_path)).getroot()
        routes = document.findall("route")
        if len(routes) != 1:
            raise ValueError("ROUTE_COUNT_NOT_ONE")
        route = routes[0]
        route_id = str(route.attrib.get("id") or "")
        town = str(route.attrib.get("town") or "")
        points = [_point(row) for row in route.findall("./waypoints/position")]
        scenarios = route.findall("./scenarios/scenario")
        if len(scenarios) != 1:
            raise ValueError("SCENARIO_COUNT_NOT_ONE")
        scenario = scenarios[0]
        trigger_element = scenario.find("trigger_point")
        parameters = scenario.find("rq2_t_e2_v3")
        if trigger_element is None or parameters is None:
            raise ValueError("SCENARIO_TRIGGER_OR_PARAMETERS_MISSING")
        trigger = _point(trigger_element)
        trigger_yaw = float(trigger_element.attrib.get("yaw", "nan"))
    except (ET.ParseError, KeyError, TypeError, ValueError) as error:
        reasons.append("ROUTE_SCENARIO_PARSE_FAILED:" + type(error).__name__ + ":" + str(error))
        points, trigger, trigger_yaw = (), (0.0, 0.0, 0.0), 0.0
        route_id = town = ""
        scenario = parameters = None
    expected_route_id = "RQ2TE2V3-" + str(binding.get("scene_config_id") or USC_SCENE_ID)
    if route_id != expected_route_id:
        reasons.append("ROUTE_ID_MISMATCH")
    if town != str(binding.get("town")):
        reasons.append("MAP_IDENTITY_MISMATCH")
    if scenario is None or str(scenario.attrib.get("type")) != SCENARIO_TYPE:
        reasons.append("SCENARIO_TYPE_MISMATCH")
    if parameters is None or str(parameters.attrib.get("scene_config_id")) != str(binding.get("scene_config_id")):
        reasons.append("SCENARIO_ID_MISMATCH")
    if parameters is None or str(parameters.attrib.get("scene_configuration_sha256")) != scene_digest:
        reasons.append("SCENARIO_ROUTE_EMBEDDED_DIGEST_MISMATCH")
    if not _owner_symbol_present(root):
        reasons.append("SCENARIO_OWNER_NOT_CONSTRUCTABLE_STATIC_SYMBOL_MISSING")
    projection = None
    direction_dot = None
    total_length = None
    if len(points) >= 2:
        projection = dict(project_point_to_polyline(trigger, points))
        total_length = _polyline_lengths(points)[-1]
        if float(projection["distance_m"]) > ROUTE_INTERSECTION_TOLERANCE_M:
            reasons.append("TRIGGER_VOLUME_NOT_INTERSECTED_BY_ROUTE")
        yaw = math.radians(trigger_yaw)
        trigger_direction = (math.cos(yaw), math.sin(yaw), 0.0)
        direction_dot = sum(
            trigger_direction[index] * float(projection["segment_direction_xyz"][index])
            for index in range(3)
        )
        if direction_dot < ROUTE_DIRECTION_MINIMUM_DOT:
            reasons.append("TRIGGER_DIRECTION_OPPOSES_ROUTE")
    else:
        reasons.append("ROUTE_POLYLINE_INSUFFICIENT")
    candidates = tuple(binding.get("runtime_candidates") or ())
    candidate_ids = [str(row.get("candidate_id")) for row in candidates]
    if str(binding.get("family")) != "UNDERSPECIFIED_CONSTRAINT":
        reasons.append("USC_FAMILY_BINDING_MISSING")
    if len(candidates) != 2 or len(set(candidate_ids)) != 2:
        reasons.append("USC_CANDIDATE_BINDING_INVALID")
    actors = tuple(binding.get("actors") or ())
    expected_actor_count = int(binding.get("expected_semantic_actor_count", len(actors)))
    if len(actors) != expected_actor_count:
        reasons.append("EXPECTED_ACTOR_SEMANTIC_BINDING_MISMATCH")
    first_arc = last_arc = None
    if projection is not None and total_length is not None:
        first_arc = max(0.0, float(projection["route_arc_length_m"]) - TRIGGER_RADIUS_M)
        last_arc = min(float(total_length), float(projection["route_arc_length_m"]) + TRIGGER_RADIUS_M)
        if last_arc >= float(total_length) and float(total_length) <= TRIGGER_RADIUS_M:
            reasons.append("LAST_ADMISSIBLE_BOUNDARY_NOT_BEFORE_ROUTE_END")
    admission = {
        "schema_version": "driveclarify.e2_v3.usc_route_trigger_admission.v1",
        "status": "PASS_STATIC_ROUTE_TRIGGER_ADMISSION" if not reasons else "FAIL_STATIC_ROUTE_TRIGGER_ADMISSION",
        "reason_codes": reasons,
        "scene_id": binding.get("scene_config_id"),
        "town": town,
        "route_path": str(route_path),
        "route_id": route_id,
        "route_sha256": route_digest,
        "scenario_configuration_sha256": scene_digest,
        "scenario_type": None if scenario is None else scenario.attrib.get("type"),
        "scenario_owner": SCENARIO_OWNER,
        "scenario_owner_static_symbol_present": _owner_symbol_present(root),
        "trigger_transform_xyz_yaw": list(trigger) + [trigger_yaw],
        "trigger_volume": {"shape": "ROUTE_ARC_AND_EUCLIDEAN_RADIUS", "radius_m": TRIGGER_RADIUS_M},
        "trigger_route_projection": projection,
        "route_total_arc_length_m": total_length,
        "route_direction_dot_trigger_forward": direction_dot,
        "first_admissible_activation_route_arc_length_m": first_arc,
        "last_admissible_activation_route_arc_length_m": last_arc,
        "last_admissible_activation_boundary": {
            "kind": "ROUTE_ARC_LENGTH_M",
            "value": last_arc,
            "crossing_tolerance_m": 0.10,
        },
        "trigger_road_lane_junction_identity": dict(binding.get("trigger_map_certificate") or {}),
        "actor_configuration": list(actors),
        "expected_semantic_actor_count": expected_actor_count,
        "usc_instruction": binding.get("instruction"),
        "usc_candidate_ids": candidate_ids,
        "engineering_only": True,
        "formal_scientific_exposure": False,
    }
    admission["admission_receipt_digest"] = canonical_sha256(admission)
    return admission


def valid_activation_receipt(
    receipt: Mapping[str, Any], *, scene_id: str, scenario_instance_id: Optional[str] = None
) -> bool:
    if receipt.get("scenario_activation_status") != "ACTIVATED_AND_BOUND":
        return False
    if str(receipt.get("scene_id")) != str(scene_id):
        return False
    if scenario_instance_id is not None and str(receipt.get("scenario_instance_id")) != str(scenario_instance_id):
        return False
    claimed = receipt.get("activation_receipt_digest")
    payload = dict(receipt)
    payload.pop("activation_receipt_digest", None)
    return bool(claimed and str(claimed) == canonical_sha256(payload))


def evaluate_activation_boundary(
    admission: Mapping[str, Any], *, ego_xyz: Sequence[float], activation_receipt: Optional[Mapping[str, Any]]
) -> Mapping[str, Any]:
    if admission.get("status") != "PASS_STATIC_ROUTE_TRIGGER_ADMISSION":
        return {"status": "ENGINEERING_PRELAUNCH_ADMISSION_INVALID", "request_stop": True}
    if activation_receipt and valid_activation_receipt(
        activation_receipt, scene_id=str(admission.get("scene_id"))
    ):
        return {"status": "ACTIVATED_AND_BOUND", "request_stop": False}
    route_path = Path(str(admission["route_path"]))
    route = ET.parse(str(route_path)).getroot().find("route")
    if route is None:
        return {"status": "ENGINEERING_ROUTE_MISSING_DURING_MONITOR", "request_stop": True}
    points = [_point(row) for row in route.findall("./waypoints/position")]
    projection = dict(project_point_to_polyline(ego_xyz, points))
    boundary = float(admission["last_admissible_activation_route_arc_length_m"])
    crossed = float(projection["route_arc_length_m"]) > boundary + 0.10
    return {
        "status": "ENGINEERING_SCENARIO_ACTIVATION_MISSED" if crossed else "AWAITING_ACTIVATION_WITHIN_ADMISSIBLE_BOUNDARY",
        "request_stop": crossed,
        "current_route_arc_length_m": projection["route_arc_length_m"],
        "current_route_distance_m": projection["distance_m"],
        "last_admissible_activation_route_arc_length_m": boundary,
    }


__all__ = [
    "SCENARIO_OWNER", "SCENARIO_TYPE", "TRIGGER_RADIUS_M", "USC_SCENE_ID",
    "canonical_sha256", "evaluate_activation_boundary", "project_point_to_polyline",
    "sha256", "static_route_trigger_admission", "valid_activation_receipt",
]
