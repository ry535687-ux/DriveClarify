"""CPU-only multi-topology discovery and preparation helpers.

The module reads local OpenDRIVE and Bench2Drive XML only.  It deliberately
does not import CARLA, SimLingo, torch, or any model/runtime package.
"""

from __future__ import annotations

import copy
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from xml.etree import ElementTree as ET

from .mapper import VERIFIED_PLAN_FRAME, VERIFIED_PLAN_UNIT, StaticBranchPlanMapperV1
from .topology import (
    TopologyContractError,
    _lane,
    _lane_section,
    _lane_width,
    _number,
    _road,
    _sample_values,
    _successor_lane,
    build_branch_topology_ground_truth,
    deterministic_sha256,
    lane_center_at,
    sample_lane_centerline,
    sha256_file,
)


MAP_DIRECTORY = Path("/home/buaa/CARLA_0.9.15/CarlaUE4/Content/Carla/Maps/OpenDrive")
ROUTE_DIRECTORY = Path("/home/buaa/wrh/simlingo/leaderboard/data/bench2drive_split")
FROZEN_THRESHOLD_PATH = Path(
    "/home/buaa/wrh/DriveClarify/reports/static_maneuver_branch_primary_mvp_v1/"
    "MAPPING_THRESHOLD_PROVENANCE.json"
)
EXCLUDED_ROUTE_ID = "27515"
EXCLUDED_JUNCTION_ID = "238"
TARGET_START_INTERVAL_M = (-7.5, -3.0)
FROZEN_START_STATION_M = -5.5
PLAN_TAIL_OFFSETS_M = (17.0, 18.0, 19.0)
SEARCH_SCHEMA = "driveclarify.multi_topology_route_backed_search.v1"


def _distance_2d(left: Sequence[float], right: Sequence[float]) -> float:
    return math.dist((float(left[0]), float(left[1])), (float(right[0]), float(right[1])))


def _minimum_distance(point: Sequence[float], polyline: Sequence[Sequence[float]]) -> float:
    return min(_distance_2d(point, candidate) for candidate in polyline)


def _append_distinct(target: list[list[float]], source: Iterable[Sequence[float]]) -> None:
    for point in source:
        row = [float(value) for value in point]
        if not target or math.dist(target[-1], row) > 1e-6:
            target.append(row)


def _unit(vector: Sequence[float]) -> tuple[float, float]:
    norm = math.hypot(float(vector[0]), float(vector[1]))
    if norm <= 1e-12:
        raise TopologyContractError("ZERO_LENGTH_TANGENT")
    return float(vector[0]) / norm, float(vector[1]) / norm


def _turn_angle_degrees(incoming: Sequence[Sequence[float]], outgoing: Sequence[Sequence[float]]) -> float:
    left = _unit((incoming[-1][0] - incoming[-2][0], incoming[-1][1] - incoming[-2][1]))
    right = _unit((outgoing[-1][0] - outgoing[0][0], outgoing[-1][1] - outgoing[0][1]))
    cross = left[0] * right[1] - left[1] * right[0]
    dot = left[0] * right[0] + left[1] * right[1]
    return math.degrees(math.atan2(cross, dot))


def _semantic_role(angle_degrees: float) -> str | None:
    if abs(angle_degrees) <= 35.0:
        return "STRAIGHT_BRANCH"
    if 35.0 < angle_degrees < 145.0:
        return "RIGHT_TURN_BRANCH"
    return None


def _route_rows() -> tuple[dict[str, list[dict[str, Any]]], int, list[str]]:
    rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    searched_files: list[str] = []
    route_count = 0
    for path in sorted(ROUTE_DIRECTORY.glob("*.xml")):
        root = ET.parse(path).getroot()
        for route in root.iter("route"):
            town = str(route.get("town"))
            if not (MAP_DIRECTORY / f"{town}.xodr").is_file():
                continue
            waypoints = [
                [float(position.get(axis, "nan")) for axis in ("x", "y", "z")]
                for position in route.findall("./waypoints/position")
            ]
            if len(waypoints) < 2 or not all(math.isfinite(value) for point in waypoints for value in point):
                continue
            rows[town].append({
                "path": path.resolve(),
                "route_id": str(route.get("id")),
                "route_road_id": route.get("road_id"),
                "waypoints": waypoints,
                "scenario_names": [item.get("name") for item in route.findall("./scenarios/scenario")],
                "scenario_types": [item.get("type") for item in route.findall("./scenarios/scenario")],
            })
            route_count += 1
            searched_files.append(str(path.resolve()))
    for town in rows:
        rows[town].sort(key=lambda item: (int(item["route_id"]), str(item["path"])))
    return rows, route_count, sorted(set(searched_files))


def _connector_branch(
    roads: Mapping[str, ET.Element],
    incoming_road_id: str,
    incoming_lane_id: int,
    connection: ET.Element,
    lane_link: ET.Element,
) -> dict[str, Any] | None:
    connector_id = str(connection.get("connectingRoad"))
    if connector_id not in roads:
        return None
    connector_lane_id = int(str(lane_link.get("to")))
    if incoming_lane_id >= 0 or connector_lane_id >= 0:
        return None
    connector = roads[connector_id]
    predecessor = connector.find("./link/predecessor")
    successor = connector.find("./link/successor")
    if (
        predecessor is None
        or successor is None
        or predecessor.get("elementType") != "road"
        or successor.get("elementType") != "road"
        or predecessor.get("elementId") != incoming_road_id
        or str(successor.get("elementId")) not in roads
    ):
        return None
    successor_road_id = str(successor.get("elementId"))
    successor_contact_point = str(successor.get("contactPoint"))
    if successor_contact_point not in {"start", "end"}:
        return None
    successor_lane_id = _successor_lane(connector, connector_lane_id)
    connector_length = _number(connector, "length")
    connector_line = sample_lane_centerline(connector, connector_lane_id, 0.0, connector_length, 0.5)
    successor_road = roads[successor_road_id]
    successor_length = _number(successor_road, "length")
    if successor_contact_point == "start":
        extension = sample_lane_centerline(
            successor_road, successor_lane_id, 0.0, min(20.0, successor_length), 0.5
        )
    else:
        extension = sample_lane_centerline(
            successor_road, successor_lane_id, successor_length, max(0.0, successor_length - 20.0), 0.5
        )
    branch_line: list[list[float]] = []
    _append_distinct(branch_line, connector_line)
    _append_distinct(branch_line, extension)
    return {
        "junction_connection_id": str(connection.get("id")),
        "incoming_road_id": incoming_road_id,
        "incoming_lane_id": incoming_lane_id,
        "connecting_road_id": connector_id,
        "connecting_lane_id": connector_lane_id,
        "successor_road_id": successor_road_id,
        "successor_lane_id": successor_lane_id,
        "successor_contact_point": successor_contact_point,
        "branch_polyline": branch_line,
    }


def _route_match(
    route: Mapping[str, Any],
    incoming_line: Sequence[Sequence[float]],
    decision_point: Sequence[float],
    straight_line: Sequence[Sequence[float]],
    right_line: Sequence[Sequence[float]],
) -> dict[str, Any] | None:
    incoming_rows = [
        (index, _minimum_distance(point, incoming_line), _distance_2d(point, decision_point))
        for index, point in enumerate(route["waypoints"])
    ]
    matched = [item for item in incoming_rows if item[1] <= 0.8]
    if not matched:
        return None
    incoming_index, incoming_error, lead_in = min(matched, key=lambda item: (item[2], item[0]))
    if lead_in > 15.0:
        return None
    later = route["waypoints"][incoming_index + 1 :]
    if not later:
        return None
    straight_distance = min(_minimum_distance(point, straight_line) for point in later)
    right_distance = min(_minimum_distance(point, right_line) for point in later)
    branch = "STRAIGHT_BRANCH" if straight_distance < right_distance else "RIGHT_TURN_BRANCH"
    branch_distance = min(straight_distance, right_distance)
    if branch_distance > 1.0:
        return None
    return {
        "nearest_incoming_waypoint_index": incoming_index,
        "incoming_centerline_error_m": incoming_error,
        "nearest_incoming_waypoint_lead_in_m": lead_in,
        "route_outgoing_branch": branch,
        "outgoing_branch_centerline_error_m": branch_distance,
    }


def discover_route_backed_candidates() -> dict[str, Any]:
    """Deterministically find supported straight/right route-backed units."""

    routes_by_town, route_count, route_files = _route_rows()
    map_files = sorted(
        path.resolve()
        for path in MAP_DIRECTORY.glob("Town*.xodr")
        if "_Opt" not in path.stem and path.stem in routes_by_town
    )
    candidates: list[dict[str, Any]] = []
    graph_pair_count = 0
    for map_path in map_files:
        town = map_path.stem
        root = ET.parse(map_path).getroot()
        roads = {str(item.get("id")): item for item in root.findall("road")}
        groups: dict[tuple[str, str, int], list[dict[str, Any]]] = defaultdict(list)
        for junction in root.findall("junction"):
            junction_id = str(junction.get("id"))
            for connection in junction.findall("connection"):
                incoming_road_id = str(connection.get("incomingRoad"))
                if incoming_road_id not in roads:
                    continue
                for lane_link in connection.findall("laneLink"):
                    try:
                        incoming_lane_id = int(str(lane_link.get("from")))
                        branch = _connector_branch(
                            roads, incoming_road_id, incoming_lane_id, connection, lane_link
                        )
                        if branch is None:
                            continue
                        incoming = roads[incoming_road_id]
                        incoming_length = _number(incoming, "length")
                        incoming_tangent = sample_lane_centerline(
                            incoming, incoming_lane_id, max(0.0, incoming_length - 1.0), incoming_length, 0.5
                        )
                        angle = _turn_angle_degrees(incoming_tangent, branch["branch_polyline"])
                        semantic_role = _semantic_role(angle)
                        if semantic_role is None:
                            continue
                        branch["semantic_role"] = semantic_role
                        branch["turn_angle_degrees"] = angle
                        groups[(junction_id, incoming_road_id, incoming_lane_id)].append(branch)
                    except (TopologyContractError, TypeError, ValueError):
                        continue
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
                    if (
                        straight["connecting_road_id"] == right["connecting_road_id"]
                        or straight["successor_road_id"] == right["successor_road_id"]
                    ):
                        continue
                    graph_pair_count += 1
                    incoming = roads[incoming_road_id]
                    incoming_length = _number(incoming, "length")
                    incoming_line = sample_lane_centerline(
                        incoming,
                        incoming_lane_id,
                        max(0.0, incoming_length - 20.0),
                        incoming_length,
                        0.5,
                    )
                    decision_point = incoming_line[-1]
                    for route in routes_by_town[town]:
                        match = _route_match(
                            route,
                            incoming_line,
                            decision_point,
                            straight["branch_polyline"],
                            right["branch_polyline"],
                        )
                        if match is None:
                            continue
                        binding = {
                            "route_id": route["route_id"],
                            "town": town,
                            "junction_id": junction_id,
                            "incoming_road_id": incoming_road_id,
                            "incoming_lane_id": incoming_lane_id,
                            "straight": {
                                key: straight[key]
                                for key in (
                                    "connecting_road_id",
                                    "connecting_lane_id",
                                    "successor_road_id",
                                    "successor_lane_id",
                                    "successor_contact_point",
                                )
                            },
                            "right": {
                                key: right[key]
                                for key in (
                                    "connecting_road_id",
                                    "connecting_lane_id",
                                    "successor_road_id",
                                    "successor_lane_id",
                                    "successor_contact_point",
                                )
                            },
                        }
                        candidates.append({
                            "town": town,
                            "route_id": route["route_id"],
                            "route_source_path": str(route["path"]),
                            "route_source_sha256": sha256_file(route["path"]),
                            "map_path": str(map_path),
                            "map_sha256": sha256_file(map_path),
                            "junction_id": junction_id,
                            "binding": binding,
                            "decision_point_xyz": [float(value) for value in decision_point],
                            "branch_semantics": ["STRAIGHT_BRANCH", "RIGHT_TURN_BRANCH"],
                            "straight_turn_angle_degrees": straight["turn_angle_degrees"],
                            "right_turn_angle_degrees": right["turn_angle_degrees"],
                            "source_route_outgoing_branch": match["route_outgoing_branch"],
                            "source_route_match": match,
                            "source_scenario_names": route["scenario_names"],
                            "source_scenario_types": route["scenario_types"],
                            "candidate_or_model_output_used": False,
                        })
    candidates.sort(
        key=lambda item: (
            item["town"],
            int(item["junction_id"]),
            int(item["route_id"]),
            item["binding"]["incoming_lane_id"],
        )
    )
    deduplicated: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, int]] = set()
    for candidate in candidates:
        identity = (
            candidate["town"],
            candidate["junction_id"],
            candidate["binding"]["incoming_road_id"],
            candidate["binding"]["incoming_lane_id"],
        )
        if identity not in seen:
            seen.add(identity)
            deduplicated.append(candidate)
    result = {
        "schema_version": SEARCH_SCHEMA,
        "algorithm": "INSTALLED_MAP_NEGATIVE_DRIVING_LANE_STRAIGHT_RIGHT_ROUTE_BACKED_V1",
        "deterministic_sort": "TOWN_NUMERIC_JUNCTION_NUMERIC_ROUTE_INCOMING_LANE",
        "candidate_or_model_output_used": False,
        "searched_map_count": len(map_files),
        "searched_map_paths": [str(path) for path in map_files],
        "searched_route_count": route_count,
        "searched_route_file_count": len(route_files),
        "searched_route_paths": route_files,
        "supported_graph_pair_count_before_route_match": graph_pair_count,
        "route_backed_candidate_count": len(deduplicated),
        "candidates": deduplicated,
    }
    result["sha256"] = deterministic_sha256(result)
    return result


def unit_id(candidate: Mapping[str, Any]) -> str:
    town = str(candidate["town"]).upper()
    return f"{town}_JUNCTION_{candidate['junction_id']}_UNIT01"


def _nearest_on_incoming(
    incoming: ET.Element, lane_id: int, point: Sequence[float], start_s: float
) -> tuple[float, float]:
    length = _number(incoming, "length")
    return min(
        (
            (_distance_2d(point, lane_center_at(incoming, lane_id, s)), s)
            for s in _sample_values(max(0.0, start_s), length, 0.02)
        ),
        key=lambda item: (item[0], item[1]),
    )[::-1]


def build_scenario_free_fixture(
    source_route_path: str | Path,
    xodr_path: str | Path,
    binding: Mapping[str, Any],
    *,
    signed_start_station_m: float = FROZEN_START_STATION_M,
) -> tuple[str, dict[str, Any]]:
    """Return deterministic route XML text and its static start metadata."""

    if not (TARGET_START_INTERVAL_M[0] <= signed_start_station_m <= TARGET_START_INTERVAL_M[1]):
        raise TopologyContractError("ROUTE_START_OUTSIDE_FROZEN_TARGET_INTERVAL")
    source_root = ET.parse(source_route_path).getroot()
    source_matches = [item for item in source_root.iter("route") if item.get("id") == str(binding["route_id"])]
    if len(source_matches) != 1:
        raise TopologyContractError("SOURCE_ROUTE_NOT_UNIQUE")
    source_route = source_matches[0]
    route = copy.deepcopy(source_route)
    xodr_root = ET.parse(xodr_path).getroot()
    incoming = _road(xodr_root, str(binding["incoming_road_id"]))
    incoming_lane_id = int(binding["incoming_lane_id"])
    incoming_length = _number(incoming, "length")
    start_s = incoming_length + float(signed_start_station_m)
    if start_s <= 0.0 or start_s >= incoming_length:
        raise TopologyContractError("ROUTE_START_S_INVALID")
    start_xyz = list(lane_center_at(incoming, incoming_lane_id, start_s))
    old_waypoints = source_route.findall("./waypoints/position")
    incoming_matches: list[tuple[int, float, float]] = []
    for index, waypoint in enumerate(old_waypoints):
        point = [float(waypoint.get(axis, "nan")) for axis in ("x", "y", "z")]
        matched_s, error = _nearest_on_incoming(
            incoming, incoming_lane_id, point, max(0.0, incoming_length - 20.0)
        )
        if error <= 0.8:
            incoming_matches.append((index, matched_s, error))
    if not incoming_matches:
        raise TopologyContractError("SOURCE_ROUTE_INCOMING_WAYPOINT_MISSING")
    after_start = [item for item in incoming_matches if item[1] >= start_s - 1e-6]
    retain_index = min((item[0] for item in after_start), default=max(item[0] for item in incoming_matches) + 1)
    waypoints = route.find("./waypoints")
    if waypoints is None:
        raise TopologyContractError("SOURCE_ROUTE_WAYPOINT_CONTAINER_MISSING")
    for child in list(waypoints):
        waypoints.remove(child)
    ET.SubElement(
        waypoints,
        "position",
        {axis: format(float(value), ".15g") for axis, value in zip(("x", "y", "z"), start_xyz)},
    )
    for waypoint in old_waypoints[retain_index:]:
        point = [float(waypoint.get(axis, "nan")) for axis in ("x", "y", "z")]
        if _distance_2d(point, start_xyz) > 0.2:
            waypoints.append(copy.deepcopy(waypoint))
    if len(waypoints.findall("position")) < 2:
        raise TopologyContractError("FIXTURE_WAYPOINTS_INSUFFICIENT")
    scenarios = route.find("./scenarios")
    if scenarios is None:
        scenarios = ET.Element("scenarios")
        weather = route.find("./weathers")
        route.insert(list(route).index(weather) if weather is not None else len(route), scenarios)
    for child in list(scenarios):
        scenarios.remove(child)
    scenarios.text = None
    output_root = ET.Element("routes")
    output_root.append(route)
    ET.indent(output_root, space="   ")
    text = ET.tostring(output_root, encoding="unicode", short_empty_elements=True) + "\n"
    forbidden = ("<scenario ", "<actor", "trigger_point", "PedestrianCrossing", "RunningRedLight")
    if any(token in text for token in forbidden):
        raise TopologyContractError("FIXTURE_DYNAMIC_CONTENT_PRESENT")
    return text, {
        "frame": "CARLA_WORLD",
        "unit": "METRE",
        "world_xyz": start_xyz,
        "incoming_road_id": str(binding["incoming_road_id"]),
        "incoming_lane_id": incoming_lane_id,
        "incoming_s_m": start_s,
        "signed_station_from_decision_point_m": float(signed_start_station_m),
        "retained_source_waypoint_index": retain_index,
        "source_waypoint_count": len(old_waypoints),
        "fixture_waypoint_count": len(waypoints.findall("position")),
    }


def eligibility_contract(topology: Mapping[str, Any], route_start: Mapping[str, Any]) -> dict[str, Any]:
    start = float(topology["evaluation_interval"]["start_arc_length_from_decision_point_m"])
    end = float(topology["evaluation_interval"]["end_arc_length_from_decision_point_m"])
    lower = start - PLAN_TAIL_OFFSETS_M[0]
    upper = min(0.0, end - PLAN_TAIL_OFFSETS_M[-1])
    route_station = float(route_start["signed_station_from_decision_point_m"])
    if not (lower <= route_station < upper):
        raise TopologyContractError("FROZEN_ROUTE_START_NOT_ELIGIBLE")
    result = {
        "schema_version": "driveclarify.multi_topology_observation_eligibility_contract.v1",
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
        "static_justification": (
            "The -5.5 m start is frozen before output, lies inside the unit-specific nominal tail-coverage "
            "interval, remains predecision, and retains 3.0 m minimum decision clearance. P3B is used only "
            "as engineering evidence that first readiness can occur near the start, not as a universal timing law."
        ),
        "uncertainty": [
            "REAL_FIRST_MODEL_READY_STATION_REQUIRES_FUTURE_OBSERVATION_ONLY_SCREEN",
            "ACTUAL_PLAN_SPACING_AND_EXTENT_REMAIN_MAPPER_VALIDATED_PER_OUTPUT",
        ],
        "candidate_or_model_output_used": False,
    }
    result["sha256"] = deterministic_sha256(result)
    return result


def _plan(topology: Mapping[str, Any], role: str, candidate_id: str) -> dict[str, Any]:
    branch = next(item for item in topology["branches"] if item["semantic_role"] == role)
    points = [point[:2] for point in branch["evaluation_polyline_world_xyz"]]
    result = {
        "candidate_id": candidate_id,
        "plan_frame": VERIFIED_PLAN_FRAME,
        "plan_unit": VERIFIED_PLAN_UNIT,
        "frame_evidence_status": "VERIFIED",
        "unit_evidence_status": "VERIFIED",
        "topology_sha256": topology["sha256"],
        "plan_points": points,
    }
    result["plan_sha256"] = deterministic_sha256(points)
    return result


def _endpoint_tangent(polyline: Sequence[Sequence[float]]) -> tuple[float, float]:
    return _unit((polyline[-1][0] - polyline[-2][0], polyline[-1][1] - polyline[-2][1]))


def mapper_compatibility(
    topology: Mapping[str, Any], threshold_contract: Mapping[str, Any], xodr_path: str | Path, binding: Mapping[str, Any]
) -> dict[str, Any]:
    mapper = StaticBranchPlanMapperV1(threshold_contract)
    transform = {
        "source_frame": "CARLA_WORLD",
        "target_frame": VERIFIED_PLAN_FRAME,
        "location_xy_world_m": [0.0, 0.0],
        "yaw_degrees": 0.0,
        "evidence_status": "VERIFIED",
    }
    straight = mapper.map_plan(topology, _plan(topology, "STRAIGHT_BRANCH", "opaque-1"), transform)
    right = mapper.map_plan(topology, _plan(topology, "RIGHT_TURN_BRANCH", "opaque-2"), transform)
    if straight["mapping_label"] != "STRAIGHT_BRANCH" or right["mapping_label"] != "RIGHT_TURN_BRANCH":
        raise TopologyContractError("FIXED_MAPPER_SYNTHETIC_DISTINCTION_FAILED")
    swapped = copy.deepcopy(dict(topology))
    swapped["branches"].reverse()
    swapped.pop("sha256", None)
    swapped["sha256"] = deterministic_sha256(swapped)
    swapped_straight = mapper.map_plan(swapped, _plan(swapped, "STRAIGHT_BRANCH", "opaque-2"), transform)
    swapped_right = mapper.map_plan(swapped, _plan(swapped, "RIGHT_TURN_BRANCH", "opaque-1"), transform)
    if swapped_straight["mapping_label"] != "STRAIGHT_BRANCH" or swapped_right["mapping_label"] != "RIGHT_TURN_BRANCH":
        raise TopologyContractError("BRANCH_OR_CANDIDATE_SWAP_CHANGED_SEMANTICS")
    by_role = {item["semantic_role"]: item for item in topology["branches"]}
    left_tangent = _endpoint_tangent(by_role["STRAIGHT_BRANCH"]["evaluation_polyline_world_xyz"])
    right_tangent = _endpoint_tangent(by_role["RIGHT_TURN_BRANCH"]["evaluation_polyline_world_xyz"])
    dot = max(-1.0, min(1.0, left_tangent[0] * right_tangent[0] + left_tangent[1] * right_tangent[1]))
    tangent_separation = math.degrees(math.acos(dot))
    root = ET.parse(xodr_path).getroot()
    lane_widths: list[dict[str, Any]] = []
    for role, road_id, lane_id, sample_at_end in (
        ("INCOMING", str(binding["incoming_road_id"]), int(binding["incoming_lane_id"]), True),
        ("STRAIGHT_CONNECTOR", str(binding["straight"]["connecting_road_id"]), int(binding["straight"]["connecting_lane_id"]), False),
        ("RIGHT_CONNECTOR", str(binding["right"]["connecting_road_id"]), int(binding["right"]["connecting_lane_id"]), False),
    ):
        road = _road(root, road_id)
        s = _number(road, "length") if sample_at_end else 0.0
        section, section_s = _lane_section(road, s)
        lane_widths.append({"role": role, "road_id": road_id, "lane_id": lane_id, "width_m": _lane_width(_lane(section, lane_id), section_s)})
    distance_threshold = float(threshold_contract["distance_threshold_m"])
    lane_width_compatible = all(abs(item["width_m"] / 2.0 - distance_threshold) <= 1e-9 for item in lane_widths)
    if tangent_separation < math.degrees(math.acos(float(threshold_contract["alignment_threshold_cosine"]))) - 1e-9:
        raise TopologyContractError("BRANCH_TANGENT_SEPARATION_BELOW_ALIGNMENT_GATE")
    if not lane_width_compatible:
        raise TopologyContractError("LANE_WIDTH_INCOMPATIBLE_WITH_FROZEN_DISTANCE_THRESHOLD")
    result = {
        "schema_version": "driveclarify.multi_topology_mapper_compatibility.v1",
        "mapper_name": "StaticBranchPlanMapperV1",
        "topology_sha256": topology["sha256"],
        "threshold_contract_sha256": threshold_contract["sha256"],
        "threshold_file_path": str(FROZEN_THRESHOLD_PATH),
        "threshold_file_sha256": sha256_file(FROZEN_THRESHOLD_PATH),
        "threshold_values": {
            key: threshold_contract[key]
            for key in (
                "distance_threshold_m",
                "alignment_threshold_cosine",
                "branch_score_margin",
                "tail_point_count",
            )
        },
        "branch_endpoint_tangent_separation_degrees": tangent_separation,
        "alignment_gate_degrees": math.degrees(math.acos(float(threshold_contract["alignment_threshold_cosine"]))),
        "lane_width_checks": lane_widths,
        "lane_width_compatible": lane_width_compatible,
        "evaluation_centerline_sample_counts": {
            role: len(branch["evaluation_polyline_world_xyz"]) for role, branch in by_role.items()
        },
        "synthetic_branch_results": {
            "straight": straight,
            "right": right,
        },
        "branch_record_swap_preserves_semantics": True,
        "opaque_candidate_id_swap_preserves_semantics": True,
        "candidate_or_model_output_used_to_select_or_tune": False,
        "compatibility_verdict": "PASS_FIXED_STATIC_BRANCH_PLAN_MAPPER_V1",
    }
    result["sha256"] = deterministic_sha256(result)
    return result


def task_binding(topology: Mapping[str, Any]) -> dict[str, Any]:
    result = {
        "schema_version": "driveclarify.multi_topology_task_binding.v1",
        "topology_sha256": topology["sha256"],
        "candidate_bindings": {
            "A": {"semantic_payload": "straight/lane-follow", "required_branch": "STRAIGHT_BRANCH"},
            "B": {"semantic_payload": "right-turn/branch-taking", "required_branch": "RIGHT_TURN_BRANCH"},
        },
        "branch_task_equivalence_classes": {
            "STRAIGHT_BRANCH": "STRAIGHT_TASK_CLASS",
            "RIGHT_TURN_BRANCH": "RIGHT_TASK_CLASS",
        },
        "known_mapping_labels": ["STRAIGHT_BRANCH", "RIGHT_TURN_BRANCH"],
        "unknown_mapping_labels": ["UNKNOWN", "AMBIGUOUS", "NO_MATCH"],
        "pair_label_rule": "DERIVE_ONLY_FROM_ACTUAL_MAPPING_X_BINDING_X_PREDECLARED_EQUIVALENCE_CLASS",
        "expected_pair_label": None,
        "candidate_or_model_output_used": False,
    }
    result["sha256"] = deterministic_sha256(result)
    return result


__all__ = [
    "EXCLUDED_JUNCTION_ID",
    "EXCLUDED_ROUTE_ID",
    "FROZEN_START_STATION_M",
    "FROZEN_THRESHOLD_PATH",
    "MAP_DIRECTORY",
    "PLAN_TAIL_OFFSETS_M",
    "ROUTE_DIRECTORY",
    "TARGET_START_INTERVAL_M",
    "build_scenario_free_fixture",
    "discover_route_backed_candidates",
    "eligibility_contract",
    "mapper_compatibility",
    "task_binding",
    "unit_id",
]
