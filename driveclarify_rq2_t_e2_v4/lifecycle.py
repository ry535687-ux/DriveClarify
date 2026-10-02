"""Fail-closed static admission and scenario-relative lifecycle closure."""

from __future__ import annotations

import hashlib
import json
import math
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from driveclarify_rq2_t_e2_v3.usc_admission import project_point_to_polyline


SCENARIO_TYPE = "DriveClarifyRQ2TE2V4EngineeringScenario"
SCENARIO_OWNER = "driveclarify_rq2_t_e2_v4.native_scenario.DriveClarifyRQ2TE2V4EngineeringScenario"
TRIGGER_INTERSECTION_TOLERANCE_M = 0.25
TRIGGER_RADIUS_M = 2.0


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def _point(element: ET.Element) -> tuple[float, float, float]:
    return tuple(float(element.attrib[name]) for name in ("x", "y", "z"))


def _polyline_length(points: Sequence[Sequence[float]]) -> float:
    return sum(math.dist(tuple(left), tuple(right)) for left, right in zip(points, points[1:]))


def static_route_lifecycle_admission(
    *,
    route_path: Path,
    binding: Mapping[str, Any],
    expected_route_sha256: str,
    expected_scene_configuration_sha256: str,
    minimum_remaining_route_m: float = 12.0,
) -> Mapping[str, Any]:
    route_path = Path(route_path).resolve()
    reasons = []
    if not route_path.is_file():
        return {"status": "FAIL_STATIC_ROUTE_LIFECYCLE_ADMISSION", "reason_codes": ["ROUTE_FILE_MISSING"]}
    route_digest = sha256(route_path)
    scene_digest = str(binding.get("scene_configuration_sha256") or "")
    if route_digest != str(expected_route_sha256):
        reasons.append("ROUTE_DIGEST_MISMATCH")
    if scene_digest != str(expected_scene_configuration_sha256):
        reasons.append("SCENARIO_CONFIG_DIGEST_MISMATCH")
    try:
        root = ET.parse(str(route_path)).getroot()
        routes = root.findall("route")
        if len(routes) != 1:
            raise ValueError("ROUTE_COUNT_NOT_ONE")
        route = routes[0]
        points = [_point(row) for row in route.findall("./waypoints/position")]
        scenarios = route.findall("./scenarios/scenario")
        if len(scenarios) != 1:
            raise ValueError("SCENARIO_COUNT_NOT_ONE")
        scenario = scenarios[0]
        trigger_element = scenario.find("trigger_point")
        parameters = scenario.find("rq2_t_e2_v4")
        if trigger_element is None or parameters is None:
            raise ValueError("TRIGGER_OR_V4_PARAMETERS_MISSING")
        trigger = _point(trigger_element)
    except (ET.ParseError, TypeError, ValueError) as error:
        return {
            "status": "FAIL_STATIC_ROUTE_LIFECYCLE_ADMISSION",
            "reason_codes": ["ROUTE_SCENARIO_PARSE_FAILED:" + str(error)],
            "route_sha256": route_digest,
        }
    if len(points) < 2:
        reasons.append("ROUTE_POLYLINE_INSUFFICIENT")
        projection = None
        remaining = 0.0
    else:
        projection = dict(project_point_to_polyline(trigger, points))
        remaining = _polyline_length(points) - float(projection["route_arc_length_m"])
        if float(projection["distance_m"]) > TRIGGER_INTERSECTION_TOLERANCE_M:
            reasons.append("ROUTE_TRIGGER_INTERSECTION_ABSENT")
        if remaining < float(minimum_remaining_route_m):
            reasons.append("ROUTE_REMAINING_DISTANCE_BELOW_SCENARIO_BOUND_CERTIFICATE")
    if str(scenario.attrib.get("type")) != SCENARIO_TYPE:
        reasons.append("SCENARIO_TYPE_MISMATCH")
    if str(parameters.attrib.get("scene_config_id")) != str(binding.get("scene_config_id")):
        reasons.append("SCENE_ID_MISMATCH")
    if str(parameters.attrib.get("scene_configuration_sha256")) != scene_digest:
        reasons.append("ROUTE_EMBEDDED_SCENE_DIGEST_MISMATCH")
    family = str(binding.get("family"))
    ordinal = int(binding.get("required_topology_ordinal", 0) or 0)
    if family == "ORDER" and ordinal < 2:
        reasons.append("ORDER_TWO_QUALIFYING_OPPORTUNITIES_NOT_PROSPECTIVELY_REQUIRED")
    receipt = {
        "schema_version": "driveclarify.e2_v4.static_route_lifecycle_admission.v1",
        "status": "PASS_STATIC_ROUTE_LIFECYCLE_ADMISSION" if not reasons else "FAIL_STATIC_ROUTE_LIFECYCLE_ADMISSION",
        "reason_codes": reasons,
        "scene_id": binding.get("scene_config_id"),
        "family": family,
        "route_id": route.attrib.get("id"),
        "town": route.attrib.get("town"),
        "route_path": str(route_path),
        "route_sha256": route_digest,
        "scenario_configuration_sha256": scene_digest,
        "scenario_type": scenario.attrib.get("type"),
        "scenario_owner": SCENARIO_OWNER,
        "trigger_transform_xyz": list(trigger),
        "trigger_route_projection": projection,
        "route_total_arc_length_m": _polyline_length(points),
        "remaining_route_after_trigger_m": remaining,
        "minimum_remaining_route_m": float(minimum_remaining_route_m),
        "first_admissible_activation_route_arc_length_m": None if projection is None else max(0.0, float(projection["route_arc_length_m"]) - TRIGGER_RADIUS_M),
        "last_admissible_activation_route_arc_length_m": None if projection is None else float(projection["route_arc_length_m"]) + TRIGGER_RADIUS_M,
        "activation_receipt_mandatory": True,
        "scenario_relative_horizon_starts_after_activation": True,
        "route_end_before_bound_is_engineering_invalid": True,
        "required_topology_ordinal": ordinal,
        "formal_scientific_exposure": False,
    }
    receipt["admission_receipt_digest"] = canonical(receipt)
    return receipt


def validate_activation_receipt(
    receipt: Mapping[str, Any], *, admission: Mapping[str, Any]
) -> bool:
    if receipt.get("scenario_activation_status") != "ACTIVATED_AND_BOUND":
        return False
    if str(receipt.get("scene_id")) != str(admission.get("scene_id")):
        return False
    if str(receipt.get("route_digest")) != str(admission.get("route_sha256")):
        return False
    if str(receipt.get("scenario_config_digest")) != str(admission.get("scenario_configuration_sha256")):
        return False
    claimed = receipt.get("activation_receipt_digest")
    payload = dict(receipt)
    payload.pop("activation_receipt_digest", None)
    return bool(claimed and str(claimed) == canonical(payload))


def evaluate_activation_boundary(
    admission: Mapping[str, Any], *, ego_xyz: Sequence[float], activation_receipt: Optional[Mapping[str, Any]]
) -> Mapping[str, Any]:
    if admission.get("status") != "PASS_STATIC_ROUTE_LIFECYCLE_ADMISSION":
        return {"status": "ENGINEERING_PRELAUNCH_ADMISSION_INVALID", "request_stop": True}
    if activation_receipt and validate_activation_receipt(activation_receipt, admission=admission):
        return {"status": "ACTIVATED_AND_BOUND", "request_stop": False}
    route = ET.parse(str(admission["route_path"])).getroot().find("route")
    points = [_point(row) for row in route.findall("./waypoints/position")]
    progress = dict(project_point_to_polyline(ego_xyz, points))
    boundary = float(admission["last_admissible_activation_route_arc_length_m"])
    crossed = float(progress["route_arc_length_m"]) > boundary + 0.10
    return {
        "status": "ENGINEERING_SCENARIO_ACTIVATION_MISSED" if crossed else "AWAITING_ACTIVATION_WITHIN_ADMISSIBLE_BOUNDARY",
        "request_stop": crossed,
        "current_route_arc_length_m": progress["route_arc_length_m"],
        "last_admissible_activation_route_arc_length_m": boundary,
    }


def validate_scenario_completion(
    *,
    admission: Mapping[str, Any],
    activation_receipt: Optional[Mapping[str, Any]],
    scenario_receipt: Mapping[str, Any],
    cleanup_receipt: Mapping[str, Any],
    route_ended: bool,
) -> Mapping[str, Any]:
    reasons = []
    if not activation_receipt or not validate_activation_receipt(activation_receipt, admission=admission):
        reasons.append("ACTIVATION_RECEIPT_MISSING_OR_INVALID")
    bound = float(scenario_receipt.get("engineering_bound_s", 0.0))
    elapsed = float(scenario_receipt.get("scenario_elapsed_simulation_time_s", 0.0) or 0.0)
    if scenario_receipt.get("engineering_bound_reached") is not True or elapsed + 1e-6 < bound:
        reasons.append("SCENARIO_RELATIVE_BOUND_NOT_COMPLETED")
        if route_ended:
            reasons.append("ROUTE_END_BEFORE_BOUND_ENGINEERING_INVALID")
    if scenario_receipt.get("scenario_activation_status") != "ACTIVATED_AND_BOUND":
        reasons.append("SCENARIO_NOT_ACTIVATED")
    if cleanup_receipt.get("cleanup_pass") is not True:
        reasons.append("CLEANUP_RECEIPT_INVALID")
    if str(admission.get("family")) == "ORDER":
        if scenario_receipt.get("order_e5_owner_active") is not True:
            reasons.append("ORDER_E5_OWNER_NOT_ACTIVE")
        if int(scenario_receipt.get("qualifying_junction_opportunity_count", 0)) < 2:
            reasons.append("ORDER_TWO_QUALIFYING_JUNCTIONS_NOT_OBSERVED")
        if scenario_receipt.get("local_topology_reveal_before_route_end") is not True:
            reasons.append("ORDER_REVEAL_NOT_BEFORE_ROUTE_END")
    return {
        "status": "PASS_SCENARIO_RELATIVE_LIFECYCLE" if not reasons else "ENGINEERING_INVALID_ROUTE_LIFECYCLE",
        "reason_codes": reasons,
        "scenario_relative_horizon_started_after_activation": activation_receipt is not None,
        "scenario_relative_elapsed_s": elapsed,
        "scenario_relative_bound_s": bound,
    }


__all__ = [
    "SCENARIO_OWNER", "SCENARIO_TYPE", "evaluate_activation_boundary", "sha256",
    "static_route_lifecycle_admission", "validate_activation_receipt",
    "validate_scenario_completion",
]
