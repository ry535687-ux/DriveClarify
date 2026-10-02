"""OpenDRIVE/route XML 静态分支拓扑解析。

本模块只依赖 Python 标准库。它不 import CARLA，也不读取模型或 candidate plan。
OpenDRIVE 的平面 Y 坐标在导入 CARLA 时取反；所有输出 polyline 都显式标为
``CARLA_WORLD`` / ``METRE``。
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence
from xml.etree import ElementTree as ET


TOPOLOGY_SCHEMA = "driveclarify.branch_topology_ground_truth.v1"
ROUTE_INSPECTION_SCHEMA = "driveclarify.static_route_topology_inspection.v1"


class TopologyContractError(ValueError):
    """带稳定 reason code 的静态拓扑拒绝。"""


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def deterministic_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def with_sha256(value: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(value))
    result.pop("sha256", None)
    result["sha256"] = deterministic_sha256(result)
    return result


def verify_sha256(value: Mapping[str, Any]) -> bool:
    recorded = value.get("sha256")
    unsigned = copy.deepcopy(dict(value))
    unsigned.pop("sha256", None)
    return isinstance(recorded, str) and recorded == deterministic_sha256(unsigned)


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise TopologyContractError(reason)


def _number(element: ET.Element, name: str) -> float:
    raw = element.get(name)
    _require(raw is not None, f"XML_ATTRIBUTE_MISSING:{element.tag}:{name}")
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise TopologyContractError(f"XML_ATTRIBUTE_INVALID:{element.tag}:{name}") from exc
    _require(math.isfinite(value), f"XML_ATTRIBUTE_NONFINITE:{element.tag}:{name}")
    return value


def _poly(element: ET.Element, delta: float) -> float:
    return sum(_number(element, name) * delta**power for power, name in enumerate(("a", "b", "c", "d")))


def _piecewise(elements: Sequence[ET.Element], coordinate: float, origin_attribute: str) -> tuple[ET.Element, float]:
    eligible = [item for item in elements if _number(item, origin_attribute) <= coordinate + 1e-9]
    _require(bool(eligible), f"PIECEWISE_SEGMENT_MISSING:{origin_attribute}")
    selected = max(eligible, key=lambda item: _number(item, origin_attribute))
    return selected, coordinate - _number(selected, origin_attribute)


def _parse_xml(path: Path, reason: str) -> ET.Element:
    try:
        return ET.parse(path).getroot()
    except (ET.ParseError, OSError) as exc:
        raise TopologyContractError(reason) from exc


def _road(root: ET.Element, road_id: str) -> ET.Element:
    matches = root.findall(f"./road[@id='{road_id}']")
    _require(len(matches) == 1, f"OPENDRIVE_ROAD_NOT_UNIQUE:{road_id}")
    return matches[0]


def _reference_at(road: ET.Element, s: float) -> tuple[float, float, float]:
    length = _number(road, "length")
    _require(-1e-9 <= s <= length + 1e-9, f"ROAD_S_OUT_OF_RANGE:{road.get('id')}")
    s = min(max(0.0, s), length)
    geometries = road.findall("./planView/geometry")
    geometry, ds = _piecewise(geometries, s, "s")
    x0, y0, heading = (_number(geometry, name) for name in ("x", "y", "hdg"))
    if geometry.find("line") is not None:
        x = x0 + ds * math.cos(heading)
        y = y0 + ds * math.sin(heading)
    elif (arc := geometry.find("arc")) is not None:
        curvature = _number(arc, "curvature")
        _require(abs(curvature) > 1e-15, "OPENDRIVE_ZERO_CURVATURE_ARC")
        next_heading = heading + curvature * ds
        x = x0 + (math.sin(next_heading) - math.sin(heading)) / curvature
        y = y0 - (math.cos(next_heading) - math.cos(heading)) / curvature
        heading = next_heading
    else:
        raise TopologyContractError(f"OPENDRIVE_GEOMETRY_UNSUPPORTED:{road.get('id')}")
    return x, y, heading


def _lane_section(road: ET.Element, s: float) -> tuple[ET.Element, float]:
    sections = road.findall("./lanes/laneSection")
    section, _ = _piecewise(sections, s, "s")
    return section, s - _number(section, "s")


def _lane(section: ET.Element, lane_id: int) -> ET.Element:
    matches = section.findall(f"./*/lane[@id='{lane_id}']")
    _require(len(matches) == 1, f"OPENDRIVE_LANE_NOT_UNIQUE:{lane_id}")
    return matches[0]


def _lane_width(lane: ET.Element, section_s: float) -> float:
    widths = lane.findall("width")
    width, delta = _piecewise(widths, section_s, "sOffset")
    value = _poly(width, delta)
    _require(value > 0.0, f"OPENDRIVE_LANE_WIDTH_NONPOSITIVE:{lane.get('id')}")
    return value


def _lane_offset(road: ET.Element, s: float) -> float:
    offsets = road.findall("./lanes/laneOffset")
    if not offsets:
        return 0.0
    offset, delta = _piecewise(offsets, s, "s")
    return _poly(offset, delta)


def _elevation(road: ET.Element, s: float) -> float:
    rows = road.findall("./elevationProfile/elevation")
    if not rows:
        return 0.0
    row, delta = _piecewise(rows, s, "s")
    return _poly(row, delta)


def _assert_flat_superelevation(road: ET.Element) -> None:
    for row in road.findall("./lateralProfile/superelevation"):
        _require(
            all(abs(_number(row, name)) <= 1e-9 for name in ("a", "b", "c", "d")),
            f"NONZERO_SUPERELEVATION_UNSUPPORTED:{road.get('id')}",
        )


def lane_center_at(road: ET.Element, lane_id: int, s: float) -> tuple[float, float, float]:
    """返回一个 lane center 点，坐标已转换为 CARLA_WORLD。"""

    _require(lane_id != 0, "CENTER_LANE_HAS_NO_DRIVING_CENTERLINE")
    _assert_flat_superelevation(road)
    reference_x, reference_y, heading = _reference_at(road, s)
    section, section_s = _lane_section(road, s)
    target = _lane(section, lane_id)
    _require(target.get("type") in {"driving", "bidirectional"}, f"LANE_NOT_DRIVABLE:{road.get('id')}:{lane_id}")
    sign = 1.0 if lane_id > 0 else -1.0
    inner_width = 0.0
    for inner_id in range(1, abs(lane_id)):
        inner_width += _lane_width(_lane(section, int(sign * inner_id)), section_s)
    lateral = _lane_offset(road, s) + sign * (inner_width + 0.5 * _lane_width(target, section_s))
    x_odr = reference_x - math.sin(heading) * lateral
    y_odr = reference_y + math.cos(heading) * lateral
    return x_odr, -y_odr, _elevation(road, s)


def _sample_values(start: float, end: float, step: float) -> list[float]:
    _require(step > 0.0, "SAMPLE_STEP_NONPOSITIVE")
    direction = 1.0 if end >= start else -1.0
    distance = abs(end - start)
    count = int(math.floor(distance / step))
    values = [start + direction * step * index for index in range(count + 1)]
    if not math.isclose(values[-1], end, abs_tol=1e-9):
        values.append(end)
    else:
        values[-1] = end
    return values


def sample_lane_centerline(road: ET.Element, lane_id: int, start_s: float, end_s: float, step: float) -> list[list[float]]:
    return [list(lane_center_at(road, lane_id, s)) for s in _sample_values(start_s, end_s, step)]


def _append_distinct(target: list[list[float]], source: Sequence[Sequence[float]], tolerance: float = 1e-6) -> None:
    for point in source:
        row = [float(value) for value in point]
        if not target or math.dist(target[-1], row) > tolerance:
            target.append(row)


def _cumulative(polyline: Sequence[Sequence[float]]) -> list[float]:
    result = [0.0]
    for left, right in zip(polyline, polyline[1:]):
        result.append(result[-1] + math.dist(left, right))
    return result


def _at_arc(polyline: Sequence[Sequence[float]], distance: float) -> list[float]:
    cumulative = _cumulative(polyline)
    _require(0.0 <= distance <= cumulative[-1] + 1e-9, "POLYLINE_ARC_OUT_OF_RANGE")
    distance = min(distance, cumulative[-1])
    for index, upper in enumerate(cumulative[1:], start=1):
        if distance <= upper + 1e-12:
            lower = cumulative[index - 1]
            span = upper - lower
            ratio = 0.0 if span <= 1e-15 else (distance - lower) / span
            return [
                float(polyline[index - 1][axis]) + ratio * (float(polyline[index][axis]) - float(polyline[index - 1][axis]))
                for axis in range(3)
            ]
    return [float(value) for value in polyline[-1]]


def _slice_arc(polyline: Sequence[Sequence[float]], start: float, end: float, step: float) -> list[list[float]]:
    return [_at_arc(polyline, value) for value in _sample_values(start, end, step)]


def _lane_link(connection: ET.Element, incoming_lane: int, expected_lane: int) -> None:
    matches = [item for item in connection.findall("laneLink") if item.get("from") == str(incoming_lane)]
    _require(len(matches) == 1, f"JUNCTION_LANE_LINK_NOT_UNIQUE:{connection.get('id')}")
    _require(matches[0].get("to") == str(expected_lane), f"JUNCTION_LANE_LINK_TARGET_MISMATCH:{connection.get('id')}")


def _successor_lane(connector: ET.Element, connector_lane: int) -> int:
    section = connector.findall("./lanes/laneSection")[-1]
    lane = _lane(section, connector_lane)
    successor = lane.find("./link/successor")
    _require(successor is not None and successor.get("id") is not None, f"CONNECTOR_SUCCESSOR_LANE_MISSING:{connector.get('id')}")
    return int(str(successor.get("id")))


def _route_record(route_root: ET.Element, route_id: str) -> tuple[ET.Element, dict[str, Any]]:
    matches = [item for item in route_root.iter("route") if item.get("id") == route_id]
    _require(len(matches) == 1, f"ROUTE_ID_NOT_UNIQUE:{route_id}")
    route = matches[0]
    waypoints = route.findall("./waypoints/position")
    _require(len(waypoints) >= 2, "ROUTE_WAYPOINTS_INSUFFICIENT")
    scenarios = route.findall("./scenarios/scenario")
    return route, {
        "route_id": route_id,
        "town": route.get("town"),
        "waypoint_count": len(waypoints),
        "route_start_xyz": [_number(waypoints[0], name) for name in ("x", "y", "z")],
        "route_end_xyz": [_number(waypoints[-1], name) for name in ("x", "y", "z")],
        "scenario_count": len(scenarios),
        "scenario_names": [item.get("name") for item in scenarios],
        "scenario_types": [item.get("type") for item in scenarios],
    }


def _nearest_incoming_s(road: ET.Element, lane_id: int, point: Sequence[float], step: float = 0.02) -> tuple[float, float]:
    length = _number(road, "length")
    best = min(
        ((math.dist(lane_center_at(road, lane_id, s)[:2], point[:2]), s) for s in _sample_values(0.0, length, step)),
        key=lambda item: (item[0], item[1]),
    )
    return best[1], best[0]


def inspect_route_topology(
    xodr_path: str | Path,
    route_xml_path: str | Path,
    binding: Mapping[str, Any],
) -> dict[str, Any]:
    """验证一个 incoming lane 上的 straight/right 静态连接。"""

    xodr = Path(xodr_path).resolve()
    route_xml = Path(route_xml_path).resolve()
    root = _parse_xml(xodr, "OPENDRIVE_PARSE_FAILED")
    route_root = _parse_xml(route_xml, "ROUTE_XML_PARSE_FAILED")
    route_id = str(binding["route_id"])
    route, route_data = _route_record(route_root, route_id)
    town = str(binding["town"])
    _require(route.get("town") == town, "ROUTE_TOWN_MISMATCH")
    junction_id = str(binding["junction_id"])
    incoming_road_id = str(binding["incoming_road_id"])
    incoming_lane_id = int(binding["incoming_lane_id"])
    junctions = root.findall(f"./junction[@id='{junction_id}']")
    _require(len(junctions) == 1, "JUNCTION_NOT_UNIQUE")
    incoming = _road(root, incoming_road_id)
    branches: list[dict[str, Any]] = []
    for role in ("straight", "right"):
        expected = binding[role]
        connector_id = str(expected["connecting_road_id"])
        connector_lane = int(expected["connecting_lane_id"])
        connections = [
            item for item in junctions[0].findall("connection")
            if item.get("incomingRoad") == incoming_road_id and item.get("connectingRoad") == connector_id
        ]
        _require(len(connections) == 1, f"JUNCTION_CONNECTION_NOT_UNIQUE:{role}")
        connection = connections[0]
        _lane_link(connection, incoming_lane_id, connector_lane)
        connector = _road(root, connector_id)
        predecessor = connector.find("./link/predecessor")
        successor = connector.find("./link/successor")
        _require(predecessor is not None and predecessor.get("elementId") == incoming_road_id, f"CONNECTOR_PREDECESSOR_MISMATCH:{role}")
        _require(successor is not None and successor.get("elementId") == str(expected["successor_road_id"]), f"CONNECTOR_SUCCESSOR_MISMATCH:{role}")
        successor_lane = _successor_lane(connector, connector_lane)
        if expected.get("successor_lane_id") is not None:
            _require(successor_lane == int(expected["successor_lane_id"]), f"SUCCESSOR_LANE_MISMATCH:{role}")
        branches.append({
            "semantic_role": "STRAIGHT_BRANCH" if role == "straight" else "RIGHT_TURN_BRANCH",
            "junction_connection_id": connection.get("id"),
            "incoming_road_id": incoming_road_id,
            "incoming_lane_id": incoming_lane_id,
            "connecting_road_id": connector_id,
            "connecting_lane_id": connector_lane,
            "successor_road_id": successor.get("elementId"),
            "successor_contact_point": successor.get("contactPoint"),
            "successor_lane_id": successor_lane,
        })
    match_s, match_error = _nearest_incoming_s(incoming, incoming_lane_id, route_data["route_start_xyz"])
    result = {
        "schema_version": ROUTE_INSPECTION_SCHEMA,
        "route": route_data,
        "town": town,
        "junction_id": junction_id,
        "decision_point": {
            "frame": "CARLA_WORLD",
            "unit": "METRE",
            "xyz": list(lane_center_at(incoming, incoming_lane_id, _number(incoming, "length"))),
            "incoming_road_id": incoming_road_id,
            "incoming_lane_id": incoming_lane_id,
            "incoming_s_m": _number(incoming, "length"),
        },
        "route_start_incoming_lane_match": {
            "incoming_s_m": match_s,
            "centerline_error_m": match_error,
            "static_lead_in_distance_m": _number(incoming, "length") - match_s,
        },
        "straight_branch_available": True,
        "right_branch_available": True,
        "branches": branches,
        "branch_topology_source": "OPENDRIVE_JUNCTION_CONNECTION_LANE_LINK_AND_LANE_CENTERLINE",
        "candidate_or_model_output_used": False,
        "source_provenance": {
            "opendrive_path": str(xodr),
            "opendrive_sha256": sha256_file(xodr),
            "route_xml_path": str(route_xml),
            "route_xml_sha256": sha256_file(route_xml),
        },
    }
    return with_sha256(result)


def build_branch_topology_ground_truth(
    xodr_path: str | Path,
    route_xml_path: str | Path,
    binding: Mapping[str, Any],
    *,
    sample_step_m: float = 0.5,
    incoming_lead_in_m: float = 20.0,
    successor_extension_m: float = 15.0,
    lane_width_reference_m: float = 3.5,
    evaluation_length_m: float = 12.0,
) -> dict[str, Any]:
    """构造输出前冻结的 ``BranchTopologyGroundTruthV1``。"""

    inspection = inspect_route_topology(xodr_path, route_xml_path, binding)
    xodr = Path(xodr_path).resolve()
    root = _parse_xml(xodr, "OPENDRIVE_PARSE_FAILED")
    incoming_id = str(binding["incoming_road_id"])
    incoming_lane = int(binding["incoming_lane_id"])
    incoming = _road(root, incoming_id)
    incoming_length = _number(incoming, "length")
    shared = sample_lane_centerline(
        incoming,
        incoming_lane,
        max(0.0, incoming_length - incoming_lead_in_m),
        incoming_length,
        sample_step_m,
    )
    branch_rows: list[dict[str, Any]] = []
    connector_only: dict[str, list[list[float]]] = {}
    for role_key, semantic_role in (("straight", "STRAIGHT_BRANCH"), ("right", "RIGHT_TURN_BRANCH")):
        expected = binding[role_key]
        connector_id = str(expected["connecting_road_id"])
        connector_lane = int(expected["connecting_lane_id"])
        successor_id = str(expected["successor_road_id"])
        successor_lane = int(expected["successor_lane_id"])
        successor_contact = str(expected["successor_contact_point"])
        connector = _road(root, connector_id)
        successor = _road(root, successor_id)
        connector_length = _number(connector, "length")
        successor_length = _number(successor, "length")
        connector_line = sample_lane_centerline(connector, connector_lane, 0.0, connector_length, sample_step_m)
        if successor_contact == "start":
            successor_start, successor_end = 0.0, min(successor_extension_m, successor_length)
        elif successor_contact == "end":
            successor_start, successor_end = successor_length, max(0.0, successor_length - successor_extension_m)
        else:
            raise TopologyContractError(f"SUCCESSOR_CONTACT_POINT_INVALID:{semantic_role}")
        successor_line = sample_lane_centerline(successor, successor_lane, successor_start, successor_end, sample_step_m)
        branch_polyline: list[list[float]] = []
        _append_distinct(branch_polyline, shared)
        _append_distinct(branch_polyline, connector_line)
        _append_distinct(branch_polyline, successor_line)
        connector_path: list[list[float]] = []
        _append_distinct(connector_path, connector_line)
        _append_distinct(connector_path, successor_line)
        connector_only[semantic_role] = connector_path
        branch_rows.append({
            "semantic_role": semantic_role,
            "lane_ids": [
                {"road_id": connector_id, "lane_id": connector_lane, "role": "JUNCTION_CONNECTOR"},
                {"road_id": successor_id, "lane_id": successor_lane, "role": "SUCCESSOR_EXTENSION"},
            ],
            "polyline_world_xyz": branch_polyline,
            "polyline_frame": "CARLA_WORLD",
            "polyline_unit": "METRE",
        })

    separation_gate = 0.5 * lane_width_reference_m
    maximum_common_arc = min(_cumulative(connector_only["STRAIGHT_BRANCH"])[-1], _cumulative(connector_only["RIGHT_TURN_BRANCH"])[-1])
    divergence_distance: float | None = None
    straight_point: list[float] | None = None
    right_point: list[float] | None = None
    for distance in _sample_values(0.0, maximum_common_arc, min(0.1, sample_step_m)):
        straight_at = _at_arc(connector_only["STRAIGHT_BRANCH"], distance)
        right_at = _at_arc(connector_only["RIGHT_TURN_BRANCH"], distance)
        if math.dist(straight_at, right_at) + 1e-9 >= separation_gate:
            divergence_distance, straight_point, right_point = distance, straight_at, right_at
            break
    _require(divergence_distance is not None and straight_point is not None and right_point is not None, "BRANCH_DIVERGENCE_NOT_FOUND")
    evaluation_end = min(float(divergence_distance) + evaluation_length_m, maximum_common_arc)
    _require(evaluation_end - float(divergence_distance) >= 5.0, "EVALUATION_INTERVAL_TOO_SHORT")
    evaluation_polylines: dict[str, list[list[float]]] = {}
    for semantic_role in ("STRAIGHT_BRANCH", "RIGHT_TURN_BRANCH"):
        evaluation_polylines[semantic_role] = _slice_arc(
            connector_only[semantic_role], float(divergence_distance), evaluation_end, sample_step_m
        )
    for branch in branch_rows:
        branch["evaluation_polyline_world_xyz"] = evaluation_polylines[branch["semantic_role"]]

    midpoint = [(straight_point[index] + right_point[index]) / 2.0 for index in range(3)]
    branch_by_role = {item["semantic_role"]: item for item in branch_rows}
    inspection_branches = {item["semantic_role"]: item for item in inspection["branches"]}
    source = inspection["source_provenance"]
    result = {
        "schema_version": TOPOLOGY_SCHEMA,
        "contract_name": "BranchTopologyGroundTruthV1",
        "town": str(binding["town"]),
        "map_identity": {
            "map_name": str(binding["town"]),
            "coordinate_frame": "CARLA_WORLD",
            "unit": "METRE",
            "opendrive_path": source["opendrive_path"],
            "opendrive_sha256": source["opendrive_sha256"],
            "opendrive_to_carla_transform": "X_CARLA=X_OPENDRIVE;Y_CARLA=-Y_OPENDRIVE;Z_FROM_ELEVATION_PROFILE",
        },
        "route_id": str(binding["route_id"]),
        "route_identity": {
            "route_xml_path": source["route_xml_path"],
            "route_xml_sha256": source["route_xml_sha256"],
            "route_start_xyz": inspection["route"]["route_start_xyz"],
            "route_existing_scenarios": inspection["route"]["scenario_names"],
        },
        "junction_id": str(binding["junction_id"]),
        "decision_point": inspection["decision_point"],
        "straight_branch_lane_ids": branch_by_role["STRAIGHT_BRANCH"]["lane_ids"],
        "right_branch_lane_ids": branch_by_role["RIGHT_TURN_BRANCH"]["lane_ids"],
        "straight_polyline": branch_by_role["STRAIGHT_BRANCH"]["polyline_world_xyz"],
        "right_polyline": branch_by_role["RIGHT_TURN_BRANCH"]["polyline_world_xyz"],
        "branch_divergence_point": {
            "definition": "FIRST_EQUAL_ARC_STATION_WITH_CENTERLINE_SEPARATION_AT_LEAST_HALF_REFERENCE_LANE_WIDTH",
            "arc_length_from_decision_point_m": divergence_distance,
            "topology_separation_threshold_m": separation_gate,
            "actual_centerline_separation_m": math.dist(straight_point, right_point),
            "midpoint_world_xyz": midpoint,
            "straight_point_world_xyz": straight_point,
            "right_point_world_xyz": right_point,
        },
        "evaluation_interval": {
            "frame": "CARLA_WORLD",
            "unit": "METRE",
            "start_arc_length_from_decision_point_m": divergence_distance,
            "end_arc_length_from_decision_point_m": evaluation_end,
            "length_m": evaluation_end - float(divergence_distance),
            "endpoint_inclusive": True,
        },
        "branches": branch_rows,
        "branch_connections": [inspection_branches["STRAIGHT_BRANCH"], inspection_branches["RIGHT_TURN_BRANCH"]],
        "route_start_lead_in": inspection["route_start_incoming_lane_match"],
        "source_provenance": [
            {"source_type": "OPENDRIVE", "path": source["opendrive_path"], "sha256": source["opendrive_sha256"]},
            {"source_type": "BENCH2DRIVE_ROUTE_XML", "path": source["route_xml_path"], "sha256": source["route_xml_sha256"]},
            {"source_type": "DERIVATION", "description": "REFERENCE_LINE_PLUS_LANE_OFFSET_PLUS_LANE_WIDTH_TO_LANE_CENTERLINE"},
        ],
        "sampling_contract": {
            "sample_step_m": sample_step_m,
            "incoming_lead_in_m": incoming_lead_in_m,
            "successor_extension_m": successor_extension_m,
            "lane_width_reference_m": lane_width_reference_m,
            "candidate_or_model_output_used": False,
        },
    }
    return with_sha256(result)
