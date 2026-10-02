"""Static route admission and native lifecycle validation for RQ2-T-CG."""

from __future__ import annotations

import hashlib
import math
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Mapping, Sequence

from driveclarify_rq2_t.measurement import canonical_sha256
from driveclarify_rq2_t_e2_v3.usc_admission import project_point_to_polyline


SCENARIO_TYPE = "DriveClarifyRQ2TCGEngineeringScenario"


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _point(element: ET.Element) -> list[float]:
    return [float(element.attrib[key]) for key in ("x", "y", "z")]


def _length(points: Sequence[Sequence[float]]) -> float:
    return sum(math.dist(left, right) for left, right in zip(points, points[1:]))


def static_route_admission(route_path: Path, binding: Mapping[str, Any]) -> Mapping[str, Any]:
    path = Path(route_path).resolve()
    reasons: list[str] = []
    try:
        route = ET.parse(path).getroot().find("route")
        if route is None:
            raise ValueError("ROUTE_MISSING")
        points = [_point(row) for row in route.findall("./waypoints/position")]
        scenarios = route.findall("./scenarios/scenario")
        if len(scenarios) != 1:
            raise ValueError("SCENARIO_COUNT_NOT_ONE")
        scenario = scenarios[0]
        trigger_element = scenario.find("trigger_point")
        parameters = scenario.find("rq2_t_cg")
        if trigger_element is None or parameters is None:
            raise ValueError("CG_TRIGGER_OR_PARAMETERS_MISSING")
        trigger = _point(trigger_element)
    except (OSError, ET.ParseError, TypeError, ValueError) as error:
        return {"status": "FAIL_STATIC_ROUTE_ADMISSION", "reason_codes": [str(error)]}
    projection = dict(project_point_to_polyline(trigger, points))
    if projection["distance_m"] > 0.25:
        reasons.append("ROUTE_TRIGGER_INTERSECTION_ABSENT")
    if _length(points) - float(projection["route_arc_length_m"]) < 12.0:
        reasons.append("INSUFFICIENT_REMAINING_ROUTE")
    if scenario.attrib.get("type") != SCENARIO_TYPE:
        reasons.append("SCENARIO_TYPE_MISMATCH")
    if parameters.attrib.get("scene_config_id") != binding["scene_config_id"]:
        reasons.append("SCENE_ID_MISMATCH")
    if parameters.attrib.get("scene_configuration_sha256") != binding["scene_configuration_sha256"]:
        reasons.append("SCENE_DIGEST_MISMATCH")
    if route.attrib.get("town") != binding["town"]:
        reasons.append("TOWN_MISMATCH")
    receipt = {
        "schema_version": "driveclarify.rq2_t_cg.static_route_admission.v1",
        "status": "PASS_STATIC_ROUTE_ADMISSION" if not reasons else "FAIL_STATIC_ROUTE_ADMISSION",
        "reason_codes": reasons, "route_path": str(path), "route_sha256": sha256(path),
        "route_id": route.attrib.get("id"), "town": route.attrib.get("town"),
        "scene_id": binding["scene_config_id"],
        "scenario_configuration_sha256": binding["scene_configuration_sha256"],
        "scenario_type": scenario.attrib.get("type"), "trigger_transform_xyz": trigger,
        "trigger_route_projection": projection, "route_total_arc_length_m": _length(points),
        "remaining_route_after_trigger_m": _length(points) - float(projection["route_arc_length_m"]),
        "formal_scientific_exposure": False,
    }
    receipt["admission_digest"] = canonical_sha256(receipt)
    return receipt


def validate_activation(receipt: Mapping[str, Any], admission: Mapping[str, Any]) -> bool:
    payload = dict(receipt)
    claimed = payload.pop("activation_receipt_digest", None)
    return bool(
        receipt.get("scenario_activation_status") == "ACTIVATED_AND_BOUND"
        and receipt.get("scene_id") == admission.get("scene_id")
        and receipt.get("route_digest") == admission.get("route_sha256")
        and receipt.get("scenario_config_digest") == admission.get("scenario_configuration_sha256")
        and claimed == canonical_sha256(payload)
    )


__all__ = ["SCENARIO_TYPE", "sha256", "static_route_admission", "validate_activation"]
