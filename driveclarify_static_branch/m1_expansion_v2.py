"""CPU/static helpers for the M1 real-dataset Expansion V2 freeze.

Only local XML/JSON evidence and Python's standard library are used.  This
module intentionally has no CARLA runtime, evaluator, SimLingo, model, or
numeric-accelerator dependency.
"""

from __future__ import annotations

import copy
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence
from xml.etree import ElementTree as ET

from .mapper import VERIFIED_PLAN_FRAME, VERIFIED_PLAN_UNIT, StaticBranchPlanMapperV1
from .multi_topology import (
    FROZEN_START_STATION_M,
    MAP_DIRECTORY,
    ROUTE_DIRECTORY,
    _append_distinct,
    _connector_branch,
    _plan,
    _route_rows,
    _semantic_role,
    _turn_angle_degrees,
    mapper_compatibility as base_mapper_compatibility,
)
from .topology import (
    TopologyContractError,
    _lane,
    _lane_section,
    _number,
    _road,
    build_branch_topology_ground_truth,
    deterministic_sha256,
    lane_center_at,
    sample_lane_centerline,
    sha256_file,
    with_sha256,
)


EXPANSION_ID = "DC-M1-DATASET-EXP-V2-20260803T143000Z"
SEARCH_ALGORITHM = "INSTALLED_OPENDRIVE_NEGATIVE_DRIVING_LANE_STRAIGHT_RIGHT_GRAPH_V2"
GENERATION_ALGORITHM = "OPENDRIVE_LANE_CENTERLINE_SCENARIO_FREE_ROUTE_V1"
PILOT_DEVELOPMENT_UNITS = (
    "TOWN04_JUNCTION_53_UNIT01",
    "TOWN04_JUNCTION_278_UNIT01",
    "TOWN04_JUNCTION_1452_UNIT01",
    "TOWN05_JUNCTION_1574_UNIT01",
    "TOWN05_JUNCTION_1722_UNIT01",
)
ENGINEERING_EXCLUSION = "TOWN03_JUNCTION_1221_UNIT01"
EVIDENCE_EXCLUSION = "TOWN03_ROUTE_27515_JUNCTION_238"


def _town_number(town: str) -> int:
    digits = "".join(character for character in town if character.isdigit())
    if not digits:
        raise TopologyContractError("TOWN_NUMBER_MISSING")
    return int(digits)


def generated_route_id(town: str, junction_id: str) -> str:
    value = 90_000_000 + _town_number(town) * 100_000 + int(junction_id)
    return str(value)


def neutral_unit_id(town: str, junction_id: str) -> str:
    return "%s_JUNCTION_%s_UNIT01" % (town.upper(), junction_id)


def _incoming_driving_lane_count(road: ET.Element) -> int:
    length = _number(road, "length")
    section, _ = _lane_section(road, length)
    return sum(
        lane.get("type") in {"driving", "bidirectional"}
        for lane in section.findall("./*/lane")
        if lane.get("id") != "0"
    )


def _reference_geometry_types(road: ET.Element) -> list[str]:
    result = []
    for geometry in road.findall("./planView/geometry"):
        child = next(iter(geometry), None)
        if child is not None:
            result.append(child.tag)
    return sorted(set(result))


def discover_static_graph_candidates() -> dict[str, Any]:
    """Enumerate deterministic straight/right graph pairs before any outputs."""

    map_paths = sorted(
        path.resolve() for path in MAP_DIRECTORY.glob("Town*.xodr") if "_Opt" not in path.stem
    )
    _, route_count, route_path_strings = _route_rows()
    route_paths = [Path(path) for path in route_path_strings]
    raw_pairs: list[dict[str, Any]] = []
    rejection_counts: Counter[str] = Counter()
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
                            roads, incoming_road_id, incoming_lane_id, connection, lane_link
                        )
                        if branch is None:
                            rejection_counts["UNSUPPORTED_LANE_DIRECTION_OR_CONNECTOR"] += 1
                            continue
                        incoming = roads[incoming_road_id]
                        incoming_length = _number(incoming, "length")
                        incoming_tangent = sample_lane_centerline(
                            incoming,
                            incoming_lane_id,
                            max(0.0, incoming_length - 1.0),
                            incoming_length,
                            0.5,
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
                    binding = {
                        "route_id": route_id,
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
                                lane_center_at(incoming, incoming_lane_id, _number(incoming, "length"))
                            ),
                            "straight_turn_angle_degrees": straight["turn_angle_degrees"],
                            "right_turn_angle_degrees": right["turn_angle_degrees"],
                            "incoming_driving_lane_count": _incoming_driving_lane_count(incoming),
                            "incoming_reference_geometry_types": _reference_geometry_types(incoming),
                            "candidate_or_model_output_used": False,
                            "selection_label_fields_read": [],
                        }
                    )
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
    # One independent unit per junction.  Alternative entrances/pairings remain
    # counted in graph_pair_count but cannot become cross-split pseudo-replicates.
    candidates = []
    seen_junctions = set()
    for candidate in raw_pairs:
        identity = (candidate["town"], candidate["junction_id"])
        if identity in seen_junctions:
            rejection_counts["ALTERNATE_SAME_JUNCTION_GROUP"] += 1
            continue
        seen_junctions.add(identity)
        candidates.append(candidate)
    result = {
        "schema_version": "driveclarify.m1_v2_static_graph_search.v1",
        "expansion_id": EXPANSION_ID,
        "algorithm": SEARCH_ALGORITHM,
        "searched_town_count": len(map_paths),
        "searched_towns": [path.stem for path in map_paths],
        "searched_map_paths": [str(path) for path in map_paths],
        "searched_route_file_count": len(route_paths),
        "searched_route_count": route_count,
        "graph_pair_count": len(raw_pairs),
        "unique_junction_candidate_count": len(candidates),
        "candidate_town_distribution": dict(Counter(item["town"] for item in candidates)),
        "pre_graph_rejection_counts": dict(sorted(rejection_counts.items())),
        "selection_inputs": [
            "OpenDRIVE topology",
            "route-generation feasibility",
            "plan-horizon geometry",
            "mapper contract compatibility",
            "Town/junction diversity",
            "split coverage",
        ],
        "candidate_or_model_output_used": False,
        "candidates": candidates,
    }
    result["sha256"] = deterministic_sha256(result)
    return result


def _route_polyline(candidate: Mapping[str, Any]) -> tuple[list[list[float]], dict[str, Any]]:
    root = ET.parse(candidate["map_path"]).getroot()
    binding = candidate["binding"]
    incoming = _road(root, str(binding["incoming_road_id"]))
    incoming_lane = int(binding["incoming_lane_id"])
    incoming_length = _number(incoming, "length")
    start_s = incoming_length + FROZEN_START_STATION_M
    if start_s <= 0.0:
        raise TopologyContractError("GENERATED_ROUTE_START_S_INVALID")
    straight = binding["straight"]
    connector = _road(root, str(straight["connecting_road_id"]))
    connector_lane = int(straight["connecting_lane_id"])
    successor = _road(root, str(straight["successor_road_id"]))
    successor_lane = int(straight["successor_lane_id"])
    successor_length = _number(successor, "length")
    points: list[list[float]] = []
    _append_distinct(points, sample_lane_centerline(incoming, incoming_lane, start_s, incoming_length, 1.0))
    _append_distinct(points, sample_lane_centerline(connector, connector_lane, 0.0, _number(connector, "length"), 1.0))
    if straight["successor_contact_point"] == "start":
        successor_start, successor_end = 0.0, min(25.0, successor_length)
    elif straight["successor_contact_point"] == "end":
        successor_start, successor_end = successor_length, max(0.0, successor_length - 25.0)
    else:
        raise TopologyContractError("GENERATED_ROUTE_SUCCESSOR_CONTACT_INVALID")
    _append_distinct(points, sample_lane_centerline(successor, successor_lane, successor_start, successor_end, 1.0))
    if len(points) < 8:
        raise TopologyContractError("GENERATED_ROUTE_WAYPOINTS_INSUFFICIENT")
    return points, {
        "frame": "CARLA_WORLD",
        "unit": "METRE",
        "world_xyz": points[0],
        "incoming_road_id": str(binding["incoming_road_id"]),
        "incoming_lane_id": incoming_lane,
        "incoming_s_m": start_s,
        "signed_station_from_decision_point_m": FROZEN_START_STATION_M,
        "fixture_waypoint_count": len(points),
        "generation_algorithm": GENERATION_ALGORITHM,
    }


def build_opendrive_generated_fixture(candidate: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    """Build a parser-shaped route whose waypoints are all lane centerline points."""

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
    weather_values = {
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
        ET.SubElement(weathers, "weather", {**weather_values, "route_percentage": percentage})
    ET.indent(routes, space="   ")
    text = ET.tostring(routes, encoding="unicode", short_empty_elements=True) + "\n"
    if any(token in text for token in ("<scenario ", "<actor", "trigger_point", "PedestrianCrossing", "RunningRedLight")):
        raise TopologyContractError("GENERATED_FIXTURE_DYNAMIC_CONTENT_PRESENT")
    repeated_points, repeated_start = _route_polyline(candidate)
    if repeated_points != points or repeated_start != route_start:
        raise TopologyContractError("GENERATED_FIXTURE_NONDETERMINISTIC")
    return text, route_start


def static_route_parser_compatibility(route_path: str | Path) -> dict[str, Any]:
    """Validate the literal XML fields consumed by the frozen RouteParser."""

    path = Path(route_path)
    root = ET.parse(path).getroot()
    routes = root.findall("route")
    if root.tag != "routes" or len(routes) != 1:
        raise TopologyContractError("ROUTE_PARSER_ROOT_OR_COUNT_INVALID")
    route = routes[0]
    if route.get("id") is None or route.get("town") is None:
        raise TopologyContractError("ROUTE_PARSER_IDENTITY_MISSING")
    try:
        int(str(route.get("id")))
    except ValueError as exc:
        raise TopologyContractError("ROUTE_PARSER_NUMERIC_ID_REQUIRED") from exc
    waypoints = route.findall("./waypoints/position")
    if len(waypoints) < 2:
        raise TopologyContractError("ROUTE_PARSER_WAYPOINTS_INSUFFICIENT")
    if not all(
        math.isfinite(float(point.get(axis, "nan")))
        for point in waypoints
        for axis in ("x", "y", "z")
    ):
        raise TopologyContractError("ROUTE_PARSER_WAYPOINT_NONFINITE")
    weathers = route.findall("./weathers/weather")
    if len(weathers) < 1 or any(weather.get("route_percentage") is None for weather in weathers):
        raise TopologyContractError("ROUTE_PARSER_WEATHER_INVALID")
    result = {
        "status": "PASS_STATIC_LITERAL_ROUTE_PARSER_CONTRACT",
        "route_id": route.get("id"),
        "town": route.get("town"),
        "waypoint_count": len(waypoints),
        "weather_count": len(weathers),
        "scenario_count": len(route.findall("./scenarios/scenario")),
        "actor_count": len(route.findall(".//actor")),
        "trigger_count": len(route.findall(".//trigger_point")),
        "source_path": str(path.resolve()),
        "source_sha256": sha256_file(path),
        "runtime_import_used": False,
    }
    result["sha256"] = deterministic_sha256(result)
    return result


def generated_topology(
    candidate: Mapping[str, Any], route_path: str | Path
) -> dict[str, Any]:
    topology = build_branch_topology_ground_truth(
        candidate["map_path"], route_path, candidate["binding"]
    )
    topology = copy.deepcopy(topology)
    topology["route_identity"]["source_type"] = "OPENDRIVE_GENERATED_STATIC"
    topology["route_identity"]["generation_algorithm"] = GENERATION_ALGORITHM
    for source in topology["source_provenance"]:
        if source.get("source_type") == "BENCH2DRIVE_ROUTE_XML":
            source["source_type"] = "GENERATED_STATIC_ROUTE_XML"
            source["generation_algorithm"] = GENERATION_ALGORITHM
    topology.pop("sha256", None)
    topology["sha256"] = deterministic_sha256(topology)
    return topology


def _literal_plan(points: Sequence[Sequence[float]], role: str, topology_hash: str) -> dict[str, Any]:
    plan_points = [[float(point[0]), float(point[1])] for point in points]
    return {
        "candidate_id": "opaque-static-check",
        "plan_frame": VERIFIED_PLAN_FRAME,
        "plan_unit": VERIFIED_PLAN_UNIT,
        "frame_evidence_status": "VERIFIED",
        "unit_evidence_status": "VERIFIED",
        "topology_sha256": topology_hash,
        "plan_points": plan_points,
        "plan_sha256": deterministic_sha256(plan_points),
    }


def extended_mapper_compatibility(
    topology: Mapping[str, Any],
    threshold_contract: Mapping[str, Any],
    map_path: str | Path,
    binding: Mapping[str, Any],
) -> dict[str, Any]:
    """Run required swap, resampling, perturbation, and repeat-hash checks."""

    base_result = base_mapper_compatibility(topology, threshold_contract, map_path, binding)
    mapper = StaticBranchPlanMapperV1(threshold_contract)
    transform = {
        "source_frame": "CARLA_WORLD",
        "target_frame": VERIFIED_PLAN_FRAME,
        "location_xy_world_m": [0.0, 0.0],
        "yaw_degrees": 0.0,
        "evidence_status": "VERIFIED",
    }
    rows = {}
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
        if any(result["mapping_label"] != role for result in (base, resampled, perturbed)):
            raise TopologyContractError("MAPPER_EXTENDED_SYNTHETIC_CHECK_FAILED:%s" % role)
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
        plan = _plan(swapped, role, candidate_id)
        result = mapper.map_plan(swapped, plan, transform)
        if result["mapping_label"] != role:
            raise TopologyContractError("MAPPER_BRANCH_OR_CANDIDATE_SWAP_FAILED")
        swap_results[role] = result["mapping_label"]
    result = {
        "schema_version": "driveclarify.m1_v2_mapper_compatibility.v1",
        "mapper_name": "StaticBranchPlanMapperV1",
        "topology_sha256": topology["sha256"],
        "threshold_contract_sha256": threshold_contract["sha256"],
        "threshold_values": {
            key: threshold_contract[key]
            for key in (
                "distance_threshold_m",
                "alignment_threshold_cosine",
                "branch_score_margin",
                "tail_point_count",
            )
        },
        "base_mapper_compatibility": base_result,
        "synthetic_results": rows,
        "branch_record_swap_results": swap_results,
        "opaque_candidate_id_swap_preserves_semantics": True,
        "centerline_resampling_preserves_semantics": True,
        "small_numerical_perturbation_preserves_semantics": True,
        "topology_hash_repeat": deterministic_sha256({k: v for k, v in topology.items() if k != "sha256"}) == topology["sha256"],
        "candidate_or_model_output_used_to_select_or_tune": False,
        "compatibility_verdict": "PASS_FIXED_STATIC_BRANCH_PLAN_MAPPER_V1",
    }
    result["sha256"] = deterministic_sha256(result)
    return result


def source_provenance(candidate: Mapping[str, Any], fixture_path: str | Path) -> dict[str, Any]:
    result = {
        "schema_version": "driveclarify.m1_v2_source_provenance.v1",
        "source_type": "OPENDRIVE_GENERATED_STATIC",
        "generation_algorithm": GENERATION_ALGORITHM,
        "official_bench2drive_route_claimed": False,
        "opendrive": {
            "path": candidate["map_path"],
            "sha256": candidate["map_sha256"],
        },
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
    "ENGINEERING_EXCLUSION",
    "EVIDENCE_EXCLUSION",
    "EXPANSION_ID",
    "GENERATION_ALGORITHM",
    "PILOT_DEVELOPMENT_UNITS",
    "SEARCH_ALGORITHM",
    "build_opendrive_generated_fixture",
    "discover_static_graph_candidates",
    "extended_mapper_compatibility",
    "generated_topology",
    "neutral_unit_id",
    "source_provenance",
    "static_route_parser_compatibility",
]
