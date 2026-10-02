"""CPU-only, label-blind static helpers for M1 Dataset Expansion V3.

V2 intentionally searched only negative-id driving lanes whose vehicle travel
direction follows increasing OpenDRIVE ``s``.  V3 keeps the same straight/right
task family and frozen mapper authority, but handles both legal lane travel
directions.  Selection inputs remain limited to local OpenDRIVE topology,
generated route geometry, and static mapper compatibility evidence.
"""

from __future__ import annotations

import copy
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from xml.etree import ElementTree as ET

from .m1_expansion_v2 import (
    _incoming_driving_lane_count,
    _literal_plan,
    _reference_geometry_types,
    generated_route_id,
    neutral_unit_id,
    static_route_parser_compatibility,
)
from .mapper import VERIFIED_PLAN_FRAME, VERIFIED_PLAN_UNIT, StaticBranchPlanMapperV1
from .multi_topology import (
    FROZEN_START_STATION_M,
    FROZEN_THRESHOLD_PATH,
    MAP_DIRECTORY,
    PLAN_TAIL_OFFSETS_M,
    _plan,
    _semantic_role,
    _turn_angle_degrees,
)
from .topology import (
    TOPOLOGY_SCHEMA,
    TopologyContractError,
    _append_distinct,
    _at_arc,
    _cumulative,
    _lane,
    _lane_section,
    _lane_width,
    _number,
    _road,
    _sample_values,
    _slice_arc,
    deterministic_sha256,
    lane_center_at,
    sample_lane_centerline,
    sha256_file,
    with_sha256,
)


EXPANSION_ID = "DC-M1-DATASET-EXP-V3-20260804T055600Z"
SEARCH_ALGORITHM = "INSTALLED_OPENDRIVE_BIDIRECTIONAL_DRIVING_LANE_STRAIGHT_RIGHT_GRAPH_V3"
GENERATION_ALGORITHM = "OPENDRIVE_DIRECTION_AWARE_LANE_CENTERLINE_SCENARIO_FREE_ROUTE_V1"


def _town_number(town: str) -> int:
    digits = "".join(character for character in town if character.isdigit())
    if not digits:
        raise TopologyContractError("TOWN_NUMBER_MISSING")
    return int(digits)


def _vehicle_direction(lane_id: int) -> str:
    if lane_id == 0:
        raise TopologyContractError("CENTER_LANE_HAS_NO_VEHICLE_DIRECTION")
    return "INCREASING_S" if lane_id < 0 else "DECREASING_S"


def _decision_s(road: ET.Element, lane_id: int) -> float:
    return _number(road, "length") if lane_id < 0 else 0.0


def _sample_vehicle_lane(
    road: ET.Element, lane_id: int, start_s: float, end_s: float, step: float
) -> list[list[float]]:
    if lane_id < 0 and end_s + 1e-9 < start_s:
        raise TopologyContractError("NEGATIVE_LANE_REVERSED_AGAINST_VEHICLE_DIRECTION")
    if lane_id > 0 and end_s - 1e-9 > start_s:
        raise TopologyContractError("POSITIVE_LANE_REVERSED_AGAINST_VEHICLE_DIRECTION")
    return sample_lane_centerline(road, lane_id, start_s, end_s, step)


def _lane_link_at(connector: ET.Element, lane_id: int, relation: str) -> int:
    if relation not in {"predecessor", "successor"}:
        raise TopologyContractError("LANE_LINK_RELATION_INVALID")
    sections = connector.findall("./lanes/laneSection")
    if not sections:
        raise TopologyContractError("CONNECTOR_LANE_SECTION_MISSING")
    section = sections[-1] if relation == "successor" else sections[0]
    lane = _lane(section, lane_id)
    link = lane.find("./link/" + relation)
    if link is None or link.get("id") is None:
        raise TopologyContractError("CONNECTOR_{}_LANE_MISSING".format(relation.upper()))
    return int(str(link.get("id")))


def _connector_branch(
    roads: Mapping[str, ET.Element],
    junction_id: str,
    incoming_road_id: str,
    incoming_lane_id: int,
    connection: ET.Element,
    lane_link: ET.Element,
) -> dict[str, Any] | None:
    connector_id = str(connection.get("connectingRoad"))
    if connector_id not in roads or incoming_lane_id == 0:
        return None
    connector_lane_id = int(str(lane_link.get("to")))
    if connector_lane_id == 0:
        return None

    connector_entry = "start" if connector_lane_id < 0 else "end"
    if connection.get("contactPoint") != connector_entry:
        return None

    incoming = roads[incoming_road_id]
    incoming_relation = "successor" if incoming_lane_id < 0 else "predecessor"
    incoming_link = incoming.find("./link/" + incoming_relation)
    if (
        incoming_link is None
        or incoming_link.get("elementType") != "junction"
        or incoming_link.get("elementId") != junction_id
    ):
        return None

    connector = roads[connector_id]
    exit_relation = "successor" if connector_lane_id < 0 else "predecessor"
    exit_link = connector.find("./link/" + exit_relation)
    if (
        exit_link is None
        or exit_link.get("elementType") != "road"
        or str(exit_link.get("elementId")) not in roads
    ):
        return None
    successor_road_id = str(exit_link.get("elementId"))
    successor_contact_point = str(exit_link.get("contactPoint"))
    if successor_contact_point not in {"start", "end"}:
        return None
    successor_lane_id = _lane_link_at(connector, connector_lane_id, exit_relation)

    connector_length = _number(connector, "length")
    connector_line = _sample_vehicle_lane(
        connector,
        connector_lane_id,
        0.0 if connector_lane_id < 0 else connector_length,
        connector_length if connector_lane_id < 0 else 0.0,
        0.5,
    )
    successor = roads[successor_road_id]
    successor_length = _number(successor, "length")
    if successor_contact_point == "start":
        extension = _sample_vehicle_lane(
            successor, successor_lane_id, 0.0, min(20.0, successor_length), 0.5
        )
    else:
        extension = _sample_vehicle_lane(
            successor,
            successor_lane_id,
            successor_length,
            max(0.0, successor_length - 20.0),
            0.5,
        )
    branch_line: list[list[float]] = []
    _append_distinct(branch_line, connector_line)
    _append_distinct(branch_line, extension)
    return {
        "junction_connection_id": str(connection.get("id")),
        "incoming_road_id": incoming_road_id,
        "incoming_lane_id": incoming_lane_id,
        "incoming_vehicle_direction": _vehicle_direction(incoming_lane_id),
        "connecting_road_id": connector_id,
        "connecting_lane_id": connector_lane_id,
        "connector_entry_contact_point": connector_entry,
        "connector_vehicle_direction": _vehicle_direction(connector_lane_id),
        "successor_road_id": successor_road_id,
        "successor_lane_id": successor_lane_id,
        "successor_contact_point": successor_contact_point,
        "branch_polyline": branch_line,
    }


def discover_static_graph_candidates() -> dict[str, Any]:
    """Enumerate every direction-aware graph pairing; do not inspect labels."""

    map_paths = sorted(
        path.resolve() for path in MAP_DIRECTORY.glob("Town*.xodr") if "_Opt" not in path.stem
    )
    raw_pairs: list[dict[str, Any]] = []
    rejection_counts: Counter[str] = Counter()
    junctions_with_pairs: set[tuple[str, str]] = set()
    for map_path in map_paths:
        town = map_path.stem
        root = ET.parse(map_path).getroot()
        roads = {str(road.get("id")): road for road in root.findall("road")}
        groups: dict[tuple[str, str, int], list[dict[str, Any]]] = defaultdict(list)
        for junction in root.findall("junction"):
            junction_id = str(junction.get("id"))
            for connection in junction.findall("connection"):
                incoming_road_id = str(connection.get("incomingRoad"))
                if incoming_road_id not in roads:
                    rejection_counts["INCOMING_ROAD_MISSING"] += 1
                    continue
                for lane_link in connection.findall("laneLink"):
                    try:
                        incoming_lane_id = int(str(lane_link.get("from")))
                        branch = _connector_branch(
                            roads,
                            junction_id,
                            incoming_road_id,
                            incoming_lane_id,
                            connection,
                            lane_link,
                        )
                        if branch is None:
                            rejection_counts["UNSUPPORTED_DIRECTION_OR_CONNECTOR"] += 1
                            continue
                        incoming = roads[incoming_road_id]
                        decision_s = _decision_s(incoming, incoming_lane_id)
                        tangent_start = (
                            max(0.0, decision_s - 1.0)
                            if incoming_lane_id < 0
                            else min(_number(incoming, "length"), decision_s + 1.0)
                        )
                        incoming_tangent = _sample_vehicle_lane(
                            incoming, incoming_lane_id, tangent_start, decision_s, 0.5
                        )
                        angle = _turn_angle_degrees(incoming_tangent, branch["branch_polyline"])
                        semantic_role = _semantic_role(angle)
                        if semantic_role is None:
                            rejection_counts["OUTSIDE_STRAIGHT_RIGHT_TASK_FAMILY"] += 1
                            continue
                        branch["semantic_role"] = semantic_role
                        branch["turn_angle_degrees"] = angle
                        groups[(junction_id, incoming_road_id, incoming_lane_id)].append(branch)
                    except (TopologyContractError, TypeError, ValueError) as exc:
                        rejection_counts[str(exc).split(":", 1)[0]] += 1

        for (junction_id, incoming_road_id, incoming_lane_id), branches in sorted(
            groups.items(), key=lambda item: (int(item[0][0]), int(item[0][1]), item[0][2])
        ):
            straights = sorted(
                (item for item in branches if item["semantic_role"] == "STRAIGHT_BRANCH"),
                key=lambda item: (int(item["connecting_road_id"]), item["connecting_lane_id"]),
            )
            rights = sorted(
                (item for item in branches if item["semantic_role"] == "RIGHT_TURN_BRANCH"),
                key=lambda item: (int(item["connecting_road_id"]), item["connecting_lane_id"]),
            )
            for straight in straights:
                for right in rights:
                    if straight["connecting_road_id"] == right["connecting_road_id"]:
                        rejection_counts["SHARED_CONNECTING_ROAD"] += 1
                        continue
                    if straight["successor_road_id"] == right["successor_road_id"]:
                        rejection_counts["SHARED_SUCCESSOR_ROAD"] += 1
                        continue
                    incoming = roads[incoming_road_id]
                    route_id = generated_route_id(town, junction_id)
                    branch_fields = (
                        "connecting_road_id",
                        "connecting_lane_id",
                        "connector_entry_contact_point",
                        "connector_vehicle_direction",
                        "successor_road_id",
                        "successor_lane_id",
                        "successor_contact_point",
                    )
                    binding = {
                        "route_id": route_id,
                        "town": town,
                        "junction_id": junction_id,
                        "incoming_road_id": incoming_road_id,
                        "incoming_lane_id": incoming_lane_id,
                        "incoming_vehicle_direction": _vehicle_direction(incoming_lane_id),
                        "incoming_decision_contact_point": "end" if incoming_lane_id < 0 else "start",
                        "straight": {key: straight[key] for key in branch_fields},
                        "right": {key: right[key] for key in branch_fields},
                    }
                    raw_pairs.append(
                        {
                            "town": town,
                            "junction_id": junction_id,
                            "junction_group": "%s_JUNCTION_%s" % (town.upper(), junction_id),
                            "route_family": "%s_GENERATED_JUNCTION_%s" % (town.upper(), junction_id),
                            "route_id": route_id,
                            "unit_id": neutral_unit_id(town, junction_id),
                            "source_type": "OPENDRIVE_GENERATED_STATIC",
                            "map_path": str(map_path),
                            "map_sha256": sha256_file(map_path),
                            "binding": binding,
                            "decision_point_xyz": list(
                                lane_center_at(incoming, incoming_lane_id, _decision_s(incoming, incoming_lane_id))
                            ),
                            "straight_turn_angle_degrees": straight["turn_angle_degrees"],
                            "right_turn_angle_degrees": right["turn_angle_degrees"],
                            "incoming_driving_lane_count": _incoming_driving_lane_count(incoming),
                            "incoming_reference_geometry_types": _reference_geometry_types(incoming),
                            "candidate_or_model_output_used": False,
                            "selection_label_fields_read": [],
                        }
                    )
                    junctions_with_pairs.add((town, junction_id))
    raw_pairs.sort(
        key=lambda item: (
            _town_number(item["town"]),
            int(item["junction_id"]),
            int(item["binding"]["incoming_road_id"]),
            item["binding"]["incoming_lane_id"],
            int(item["binding"]["straight"]["connecting_road_id"]),
            int(item["binding"]["right"]["connecting_road_id"]),
        )
    )
    for index, candidate in enumerate(raw_pairs):
        candidate["graph_pair_index"] = index
    result = {
        "schema_version": "driveclarify.m1_v3_static_graph_search.v1",
        "expansion_id": EXPANSION_ID,
        "algorithm": SEARCH_ALGORITHM,
        "searched_town_count": len(map_paths),
        "searched_towns": [path.stem for path in map_paths],
        "searched_map_paths": [str(path) for path in map_paths],
        "graph_pair_count": len(raw_pairs),
        "unique_junction_candidate_count": len(junctions_with_pairs),
        "candidate_town_distribution": dict(Counter(item[0] for item in junctions_with_pairs)),
        "pre_graph_rejection_counts": dict(sorted(rejection_counts.items())),
        "selection_inputs": [
            "OpenDRIVE topology",
            "vehicle-direction/contactPoint/laneLink consistency",
            "route-generation feasibility",
            "plan-horizon geometry",
            "mapper contract compatibility",
            "Town/junction/route-family coverage",
        ],
        "candidate_or_model_output_used": False,
        "candidates": raw_pairs,
    }
    result["sha256"] = deterministic_sha256(result)
    return result


def _branch_line(
    root: ET.Element, binding: Mapping[str, Any], role: str, extension_m: float
) -> tuple[list[list[float]], list[list[float]]]:
    branch = binding[role]
    connector = _road(root, str(branch["connecting_road_id"]))
    connector_lane = int(branch["connecting_lane_id"])
    connector_length = _number(connector, "length")
    connector_line = _sample_vehicle_lane(
        connector,
        connector_lane,
        0.0 if connector_lane < 0 else connector_length,
        connector_length if connector_lane < 0 else 0.0,
        0.5,
    )
    successor = _road(root, str(branch["successor_road_id"]))
    successor_lane = int(branch["successor_lane_id"])
    successor_length = _number(successor, "length")
    if branch["successor_contact_point"] == "start":
        successor_line = _sample_vehicle_lane(
            successor, successor_lane, 0.0, min(extension_m, successor_length), 0.5
        )
    elif branch["successor_contact_point"] == "end":
        successor_line = _sample_vehicle_lane(
            successor,
            successor_lane,
            successor_length,
            max(0.0, successor_length - extension_m),
            0.5,
        )
    else:
        raise TopologyContractError("SUCCESSOR_CONTACT_POINT_INVALID")
    combined: list[list[float]] = []
    _append_distinct(combined, connector_line)
    _append_distinct(combined, successor_line)
    return connector_line, combined


def _route_polyline(candidate: Mapping[str, Any]) -> tuple[list[list[float]], dict[str, Any]]:
    root = ET.parse(candidate["map_path"]).getroot()
    binding = candidate["binding"]
    incoming = _road(root, str(binding["incoming_road_id"]))
    incoming_lane = int(binding["incoming_lane_id"])
    decision_s = _decision_s(incoming, incoming_lane)
    start_s = decision_s + (FROZEN_START_STATION_M if incoming_lane < 0 else -FROZEN_START_STATION_M)
    if not (0.0 <= start_s <= _number(incoming, "length")):
        raise TopologyContractError("GENERATED_ROUTE_START_S_INVALID")
    incoming_line = _sample_vehicle_lane(incoming, incoming_lane, start_s, decision_s, 1.0)
    _, straight_line = _branch_line(root, binding, "straight", 25.0)
    points: list[list[float]] = []
    _append_distinct(points, incoming_line)
    _append_distinct(points, straight_line)
    if len(points) < 8:
        raise TopologyContractError("GENERATED_ROUTE_WAYPOINTS_INSUFFICIENT")
    station_direction_sign = 1 if incoming_lane < 0 else -1
    return points, {
        "frame": "CARLA_WORLD",
        "unit": "METRE",
        "world_xyz": points[0],
        "incoming_road_id": str(binding["incoming_road_id"]),
        "incoming_lane_id": incoming_lane,
        "incoming_s_m": start_s,
        "station_direction_sign": station_direction_sign,
        "signed_station_from_decision_point_m": FROZEN_START_STATION_M,
        "fixture_waypoint_count": len(points),
        "generation_algorithm": GENERATION_ALGORITHM,
    }


def build_opendrive_generated_fixture(candidate: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    points, route_start = _route_polyline(candidate)
    routes = ET.Element("routes")
    route = ET.SubElement(
        routes,
        "route",
        {
            "id": str(candidate["route_id"]),
            "road_id": str(candidate["binding"]["straight"]["successor_road_id"]),
            "town": str(candidate["town"]),
        },
    )
    waypoints = ET.SubElement(route, "waypoints")
    for point in points:
        ET.SubElement(
            waypoints,
            "position",
            {axis: format(float(value), ".15g") for axis, value in zip(("x", "y", "z"), point)},
        )
    ET.SubElement(route, "scenarios")
    weathers = ET.SubElement(route, "weathers")
    weather = {
        "cloudiness": "0.0",
        "fog_density": "0.0",
        "precipitation": "0.0",
        "precipitation_deposits": "0.0",
        "sun_altitude_angle": "70.0",
        "sun_azimuth_angle": "0.0",
        "wetness": "0.0",
        "wind_intensity": "0.0",
    }
    for percentage in ("0", "100"):
        ET.SubElement(weathers, "weather", {**weather, "route_percentage": percentage})
    ET.indent(routes, space="   ")
    text = ET.tostring(routes, encoding="unicode", short_empty_elements=True) + "\n"
    if any(token in text for token in ("<scenario ", "<actor", "trigger_point")):
        raise TopologyContractError("GENERATED_FIXTURE_DYNAMIC_CONTENT_PRESENT")
    repeated_points, repeated_start = _route_polyline(candidate)
    if repeated_points != points or repeated_start != route_start:
        raise TopologyContractError("GENERATED_FIXTURE_NONDETERMINISTIC")
    return text, route_start


def _nearest_incoming_s(
    incoming: ET.Element, lane_id: int, point: Sequence[float], step: float = 0.02
) -> tuple[float, float]:
    length = _number(incoming, "length")
    values = (
        (math.dist(lane_center_at(incoming, lane_id, station)[:2], point[:2]), station)
        for station in _sample_values(0.0, length, step)
    )
    error, station = min(values, key=lambda item: (item[0], item[1]))
    return station, error


def generated_topology(candidate: Mapping[str, Any], route_path: str | Path) -> dict[str, Any]:
    root = ET.parse(candidate["map_path"]).getroot()
    binding = candidate["binding"]
    incoming = _road(root, str(binding["incoming_road_id"]))
    incoming_lane = int(binding["incoming_lane_id"])
    incoming_length = _number(incoming, "length")
    decision_s = _decision_s(incoming, incoming_lane)
    shared_start = max(0.0, decision_s - 20.0) if incoming_lane < 0 else min(incoming_length, decision_s + 20.0)
    shared = _sample_vehicle_lane(incoming, incoming_lane, shared_start, decision_s, 0.5)

    branch_rows: list[dict[str, Any]] = []
    connector_only: dict[str, list[list[float]]] = {}
    connections: list[dict[str, Any]] = []
    junction = root.find("./junction[@id='{}']".format(binding["junction_id"]))
    if junction is None:
        raise TopologyContractError("JUNCTION_NOT_UNIQUE")
    for role_key, semantic_role in (("straight", "STRAIGHT_BRANCH"), ("right", "RIGHT_TURN_BRANCH")):
        expected = binding[role_key]
        candidates = [
            item
            for item in junction.findall("connection")
            if item.get("incomingRoad") == str(binding["incoming_road_id"])
            and item.get("connectingRoad") == str(expected["connecting_road_id"])
        ]
        if len(candidates) != 1:
            raise TopologyContractError("JUNCTION_CONNECTION_NOT_UNIQUE:" + role_key)
        connection = candidates[0]
        matches = [
            item
            for item in connection.findall("laneLink")
            if item.get("from") == str(incoming_lane)
            and item.get("to") == str(expected["connecting_lane_id"])
        ]
        if len(matches) != 1:
            raise TopologyContractError("JUNCTION_LANE_LINK_NOT_UNIQUE:" + role_key)
        connector_line, branch_line = _branch_line(root, binding, role_key, 15.0)
        polyline: list[list[float]] = []
        _append_distinct(polyline, shared)
        _append_distinct(polyline, branch_line)
        connector_only[semantic_role] = branch_line
        branch_rows.append(
            {
                "semantic_role": semantic_role,
                "lane_ids": [
                    {
                        "road_id": str(expected["connecting_road_id"]),
                        "lane_id": int(expected["connecting_lane_id"]),
                        "role": "JUNCTION_CONNECTOR",
                    },
                    {
                        "road_id": str(expected["successor_road_id"]),
                        "lane_id": int(expected["successor_lane_id"]),
                        "role": "SUCCESSOR_EXTENSION",
                    },
                ],
                "polyline_world_xyz": polyline,
                "polyline_frame": "CARLA_WORLD",
                "polyline_unit": "METRE",
            }
        )
        connections.append(
            {
                "semantic_role": semantic_role,
                "junction_connection_id": connection.get("id"),
                "incoming_road_id": str(binding["incoming_road_id"]),
                "incoming_lane_id": incoming_lane,
                "incoming_vehicle_direction": binding["incoming_vehicle_direction"],
                "connecting_road_id": str(expected["connecting_road_id"]),
                "connecting_lane_id": int(expected["connecting_lane_id"]),
                "connector_vehicle_direction": expected["connector_vehicle_direction"],
                "successor_road_id": str(expected["successor_road_id"]),
                "successor_contact_point": expected["successor_contact_point"],
                "successor_lane_id": int(expected["successor_lane_id"]),
            }
        )

    separation_gate = 1.75
    maximum_common_arc = min(
        _cumulative(connector_only["STRAIGHT_BRANCH"])[-1],
        _cumulative(connector_only["RIGHT_TURN_BRANCH"])[-1],
    )
    divergence_distance = None
    straight_point = None
    right_point = None
    for distance in _sample_values(0.0, maximum_common_arc, 0.1):
        left = _at_arc(connector_only["STRAIGHT_BRANCH"], distance)
        right = _at_arc(connector_only["RIGHT_TURN_BRANCH"], distance)
        if math.dist(left, right) + 1e-9 >= separation_gate:
            divergence_distance, straight_point, right_point = distance, left, right
            break
    if divergence_distance is None or straight_point is None or right_point is None:
        raise TopologyContractError("BRANCH_DIVERGENCE_NOT_FOUND")
    evaluation_end = min(divergence_distance + 12.0, maximum_common_arc)
    if evaluation_end - divergence_distance < 5.0:
        raise TopologyContractError("EVALUATION_INTERVAL_TOO_SHORT")
    for branch in branch_rows:
        branch["evaluation_polyline_world_xyz"] = _slice_arc(
            connector_only[branch["semantic_role"]], divergence_distance, evaluation_end, 0.5
        )

    fixture_root = ET.parse(route_path).getroot()
    route = fixture_root.find("./route")
    if route is None:
        raise TopologyContractError("ROUTE_ID_NOT_UNIQUE")
    route_start_xyz = [float(route.find("./waypoints/position").get(axis)) for axis in ("x", "y", "z")]
    match_s, match_error = _nearest_incoming_s(incoming, incoming_lane, route_start_xyz)
    midpoint = [(straight_point[index] + right_point[index]) / 2.0 for index in range(3)]
    result = {
        "schema_version": TOPOLOGY_SCHEMA,
        "contract_name": "BranchTopologyGroundTruthV1",
        "town": str(binding["town"]),
        "map_identity": {
            "map_name": str(binding["town"]),
            "coordinate_frame": "CARLA_WORLD",
            "unit": "METRE",
            "opendrive_path": str(Path(candidate["map_path"]).resolve()),
            "opendrive_sha256": candidate["map_sha256"],
            "opendrive_to_carla_transform": "X_CARLA=X_OPENDRIVE;Y_CARLA=-Y_OPENDRIVE;Z_FROM_ELEVATION_PROFILE",
        },
        "route_id": str(binding["route_id"]),
        "route_identity": {
            "route_xml_path": str(Path(route_path).resolve()),
            "route_xml_sha256": sha256_file(route_path),
            "route_start_xyz": route_start_xyz,
            "route_existing_scenarios": [],
            "source_type": "OPENDRIVE_GENERATED_STATIC",
            "generation_algorithm": GENERATION_ALGORITHM,
        },
        "junction_id": str(binding["junction_id"]),
        "decision_point": {
            "frame": "CARLA_WORLD",
            "unit": "METRE",
            "xyz": list(lane_center_at(incoming, incoming_lane, decision_s)),
            "incoming_road_id": str(binding["incoming_road_id"]),
            "incoming_lane_id": incoming_lane,
            "incoming_s_m": decision_s,
            "station_direction_sign": 1 if incoming_lane < 0 else -1,
            "vehicle_direction": binding["incoming_vehicle_direction"],
        },
        "straight_branch_lane_ids": branch_rows[0]["lane_ids"],
        "right_branch_lane_ids": branch_rows[1]["lane_ids"],
        "straight_polyline": branch_rows[0]["polyline_world_xyz"],
        "right_polyline": branch_rows[1]["polyline_world_xyz"],
        "branch_divergence_point": {
            "definition": "FIRST_EQUAL_ARC_STATION_WITH_CENTERLINE_SEPARATION_AT_LEAST_FROZEN_DISTANCE_THRESHOLD",
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
            "length_m": evaluation_end - divergence_distance,
            "endpoint_inclusive": True,
        },
        "branches": branch_rows,
        "branch_connections": connections,
        "route_start_lead_in": {
            "incoming_s_m": match_s,
            "centerline_error_m": match_error,
            "static_lead_in_distance_m": abs(decision_s - match_s),
        },
        "source_provenance": [
            {"source_type": "OPENDRIVE", "path": str(Path(candidate["map_path"]).resolve()), "sha256": candidate["map_sha256"]},
            {"source_type": "GENERATED_STATIC_ROUTE_XML", "path": str(Path(route_path).resolve()), "sha256": sha256_file(route_path), "generation_algorithm": GENERATION_ALGORITHM},
            {"source_type": "DERIVATION", "description": "DIRECTION_AWARE_REFERENCE_LINE_PLUS_LANE_OFFSET_PLUS_LANE_WIDTH_TO_LANE_CENTERLINE"},
        ],
        "sampling_contract": {
            "sample_step_m": 0.5,
            "incoming_lead_in_m": 20.0,
            "successor_extension_m": 15.0,
            "lane_width_reference_m": 3.5,
            "candidate_or_model_output_used": False,
        },
    }
    return with_sha256(result)


def eligibility_contract(topology: Mapping[str, Any], route_start: Mapping[str, Any]) -> dict[str, Any]:
    start = float(topology["evaluation_interval"]["start_arc_length_from_decision_point_m"])
    end = float(topology["evaluation_interval"]["end_arc_length_from_decision_point_m"])
    lower = start - PLAN_TAIL_OFFSETS_M[0]
    upper = min(0.0, end - PLAN_TAIL_OFFSETS_M[-1])
    route_station = float(route_start["signed_station_from_decision_point_m"])
    if not (lower <= route_station < upper):
        raise TopologyContractError("FROZEN_ROUTE_START_NOT_ELIGIBLE")
    result = {
        "schema_version": "driveclarify.m1_v3_observation_eligibility_contract.v1",
        "contract_name": "FirstModelReadyObservationUnitEligibilityV1",
        "topology_sha256": topology["sha256"],
        "route_start": copy.deepcopy(dict(route_start)),
        "decision_point": copy.deepcopy(topology["decision_point"]),
        "divergence_station_m": topology["branch_divergence_point"]["arc_length_from_decision_point_m"],
        "evaluation_interval_m": [start, end],
        "expected_plan_horizon_requirement": {
            "route_output_point_count": 20,
            "nominal_equal_spacing_stations_m": [0.0, 19.0],
            "mapper_tail_point_count": 3,
            "nominal_tail_offsets_from_observation_m": list(PLAN_TAIL_OFFSETS_M),
            "actual_output_extent_statically_guaranteed": False,
        },
        "eligibility_interval": {
            "lower_inclusive_m": lower,
            "upper_exclusive_m": upper,
            "signed_station_origin": "DECISION_POINT_NEGATIVE_IS_INCOMING_PREDECISION",
            "station_direction_sign": topology["decision_point"]["station_direction_sign"],
        },
        "route_start_in_eligibility_interval": True,
        "first_observation_policy": {
            "required_observation_index": 0,
            "second_observation_allowed": False,
            "rerun_same_unit_after_noneligible": False,
            "candidate_or_model_output_read_before_gate": False,
            "on_eligible": "ELIGIBLE_FOR_LATER_A3_B3_BATCH_ONLY",
            "on_noneligible": "EVIDENCE_UNAVAILABLE_KEEP_UNKNOWN_EXCLUSION",
        },
        "candidate_or_model_output_used": False,
    }
    result["sha256"] = deterministic_sha256(result)
    return result


def _endpoint_tangent(polyline: Sequence[Sequence[float]]) -> tuple[float, float]:
    dx = float(polyline[-1][0]) - float(polyline[-2][0])
    dy = float(polyline[-1][1]) - float(polyline[-2][1])
    norm = math.hypot(dx, dy)
    if norm <= 1e-12:
        raise TopologyContractError("ZERO_LENGTH_TANGENT")
    return dx / norm, dy / norm


def extended_mapper_compatibility(
    topology: Mapping[str, Any],
    threshold_contract: Mapping[str, Any],
    map_path: str | Path,
    binding: Mapping[str, Any],
) -> dict[str, Any]:
    mapper = StaticBranchPlanMapperV1(threshold_contract)
    transform = {
        "source_frame": "CARLA_WORLD",
        "target_frame": VERIFIED_PLAN_FRAME,
        "location_xy_world_m": [0.0, 0.0],
        "yaw_degrees": 0.0,
        "evidence_status": "VERIFIED",
    }
    rows: dict[str, Any] = {}
    for role in ("STRAIGHT_BRANCH", "RIGHT_TURN_BRANCH"):
        branch = next(item for item in topology["branches"] if item["semantic_role"] == role)
        points = branch["evaluation_polyline_world_xyz"]
        base = mapper.map_plan(topology, _literal_plan(points, role, topology["sha256"]), transform)
        indices = list(range(0, len(points), 2))
        if indices[-1] != len(points) - 1:
            indices.append(len(points) - 1)
        resampled_points = [points[index] for index in indices]
        resampled = mapper.map_plan(
            topology, _literal_plan(resampled_points, role, topology["sha256"]), transform
        )
        perturbed_points = [
            [float(point[0]) + (1.0e-4 if index % 2 == 0 else -1.0e-4), float(point[1])]
            for index, point in enumerate(points)
        ]
        perturbed = mapper.map_plan(
            topology, _literal_plan(perturbed_points, role, topology["sha256"]), transform
        )
        if any(item["mapping_label"] != role for item in (base, resampled, perturbed)):
            raise TopologyContractError("MAPPER_EXTENDED_SYNTHETIC_CHECK_FAILED:" + role)
        rows[role] = {
            "base_mapping": base["mapping_label"],
            "resampled_mapping": resampled["mapping_label"],
            "perturbed_mapping": perturbed["mapping_label"],
            "resampled_point_count": len(resampled_points),
            "perturbation_m": 1.0e-4,
        }

    swapped = copy.deepcopy(dict(topology))
    swapped["branches"] = list(reversed(swapped["branches"]))
    swapped.pop("sha256", None)
    swapped["sha256"] = deterministic_sha256(swapped)
    swap_results = {}
    for candidate_id, role in (("opaque-B", "STRAIGHT_BRANCH"), ("opaque-A", "RIGHT_TURN_BRANCH")):
        result = mapper.map_plan(swapped, _plan(swapped, role, candidate_id), transform)
        if result["mapping_label"] != role:
            raise TopologyContractError("MAPPER_BRANCH_OR_CANDIDATE_SWAP_FAILED")
        swap_results[role] = result["mapping_label"]

    by_role = {item["semantic_role"]: item for item in topology["branches"]}
    left = _endpoint_tangent(by_role["STRAIGHT_BRANCH"]["evaluation_polyline_world_xyz"])
    right = _endpoint_tangent(by_role["RIGHT_TURN_BRANCH"]["evaluation_polyline_world_xyz"])
    dot = max(-1.0, min(1.0, left[0] * right[0] + left[1] * right[1]))
    tangent_separation = math.degrees(math.acos(dot))
    alignment_gate = math.degrees(math.acos(float(threshold_contract["alignment_threshold_cosine"])))
    if tangent_separation < alignment_gate - 1e-9:
        raise TopologyContractError("BRANCH_TANGENT_SEPARATION_BELOW_ALIGNMENT_GATE")

    root = ET.parse(map_path).getroot()
    lane_widths = []
    width_specs = [
        ("INCOMING", str(binding["incoming_road_id"]), int(binding["incoming_lane_id"]), binding["incoming_decision_contact_point"]),
        ("STRAIGHT_CONNECTOR", str(binding["straight"]["connecting_road_id"]), int(binding["straight"]["connecting_lane_id"]), binding["straight"]["connector_entry_contact_point"]),
        ("RIGHT_CONNECTOR", str(binding["right"]["connecting_road_id"]), int(binding["right"]["connecting_lane_id"]), binding["right"]["connector_entry_contact_point"]),
    ]
    for role, road_id, lane_id, contact in width_specs:
        road = _road(root, road_id)
        station = 0.0 if contact == "start" else _number(road, "length")
        section, section_s = _lane_section(road, station)
        lane_widths.append(
            {"role": role, "road_id": road_id, "lane_id": lane_id, "width_m": _lane_width(_lane(section, lane_id), section_s)}
        )
    distance_threshold = float(threshold_contract["distance_threshold_m"])
    # The mapper threshold is a maximum projection distance.  Wider lanes are
    # monotonically safer for branch separation; narrower lanes are rejected.
    # This keeps the frozen 1.75 m authority unchanged while generalizing the
    # V2 exact-3.5 m fixture heuristic to a conservative minimum-width gate.
    if not all(item["width_m"] / 2.0 + 1e-9 >= distance_threshold for item in lane_widths):
        raise TopologyContractError("LANE_WIDTH_INCOMPATIBLE_WITH_FROZEN_DISTANCE_THRESHOLD")
    result = {
        "schema_version": "driveclarify.m1_v3_mapper_compatibility.v1",
        "mapper_name": "StaticBranchPlanMapperV1",
        "topology_sha256": topology["sha256"],
        "threshold_contract_sha256": threshold_contract["sha256"],
        "threshold_file_path": str(FROZEN_THRESHOLD_PATH),
        "threshold_file_sha256": sha256_file(FROZEN_THRESHOLD_PATH),
        "threshold_values": {
            key: threshold_contract[key]
            for key in ("distance_threshold_m", "alignment_threshold_cosine", "branch_score_margin", "tail_point_count")
        },
        "branch_endpoint_tangent_separation_degrees": tangent_separation,
        "alignment_gate_degrees": alignment_gate,
        "lane_width_checks": lane_widths,
        "lane_width_gate": "HALF_WIDTH_GREATER_THAN_OR_EQUAL_TO_FROZEN_DISTANCE_THRESHOLD",
        "lane_width_compatible": True,
        "synthetic_results": rows,
        "branch_record_swap_results": swap_results,
        "opaque_candidate_id_swap_preserves_semantics": True,
        "centerline_resampling_preserves_semantics": True,
        "small_numerical_perturbation_preserves_semantics": True,
        "topology_hash_repeat": deterministic_sha256({key: value for key, value in topology.items() if key != "sha256"}) == topology["sha256"],
        "candidate_or_model_output_used_to_select_or_tune": False,
        "compatibility_verdict": "PASS_FIXED_STATIC_BRANCH_PLAN_MAPPER_V1",
    }
    result["sha256"] = deterministic_sha256(result)
    return result


def source_provenance(candidate: Mapping[str, Any], fixture_path: str | Path) -> dict[str, Any]:
    result = {
        "schema_version": "driveclarify.m1_v3_source_provenance.v1",
        "source_type": "OPENDRIVE_GENERATED_STATIC",
        "generation_algorithm": GENERATION_ALGORITHM,
        "official_bench2drive_route_claimed": False,
        "opendrive": {"path": candidate["map_path"], "sha256": candidate["map_sha256"]},
        "generated_route": {
            "path": str(Path(fixture_path).resolve()),
            "sha256": sha256_file(fixture_path),
            "waypoints_from_verified_lane_centerlines": True,
        },
        "binding": copy.deepcopy(candidate["binding"]),
        "candidate_plan_used": False,
        "mapper_runtime_output_used": False,
        "model_output_used": False,
        "dynamic_actor_or_trigger_used": False,
    }
    result["sha256"] = deterministic_sha256(result)
    return result


__all__ = [
    "EXPANSION_ID",
    "GENERATION_ALGORITHM",
    "SEARCH_ALGORITHM",
    "build_opendrive_generated_fixture",
    "discover_static_graph_candidates",
    "eligibility_contract",
    "extended_mapper_compatibility",
    "generated_topology",
    "source_provenance",
    "static_route_parser_compatibility",
]
