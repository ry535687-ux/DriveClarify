"""Runtime-neutral helpers used by the one-shot MANEUVER_BRANCH agent.

This module imports neither CARLA nor torch.  Live objects are accepted through duck-typed
interfaces so the branch selection and freeze protocol can be tested on CPU-only fixtures.
"""

from __future__ import annotations

import hashlib
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .maneuver_branch import (
    FROZEN_SCHEMA,
    MAPPING_MODE,
    RAW_ROUTE_FRAME,
    RAW_ROUTE_UNIT,
    SCENARIO_RUNTIME_EVIDENCE_SCHEMA,
    SCENARIO_RUNTIME_FAIRNESS_PASS,
    apply_planar,
    deterministic_hash,
    planar_transforms,
    transform_validation,
    with_hash,
)


CAPTURE_PROTOCOL = "DRIVECLARIFY_REAL_MANEUVER_BRANCH_MAPPING_PILOT_V1"
BRANCH_SELECTION_ALGORITHM = "FIRST_FORWARD_REACHABLE_STRAIGHT_AND_RIGHT_PATH_V1"
SUPPORTED_SCENARIO_CLASS = "OppositeVehicleRunningRedLight"
SCENARIO_ALREADY_ACTIVE = "BLOCKED_SCENARIO_ALREADY_ACTIVE"
SCENARIO_ACTOR_IDENTITY_UNAVAILABLE = "BLOCKED_SCENARIO_ACTOR_IDENTITY_UNAVAILABLE"
SCENARIO_RUNTIME_STATE_UNKNOWN = "BLOCKED_SCENARIO_RUNTIME_STATE_UNKNOWN"
SCENARIO_BRANCH_INTERFERENCE = "BLOCKED_SCENARIO_BRANCH_INTERFERENCE"
SCENARIO_EVIDENCE_INTEGRITY = "BLOCKED_SCENARIO_EVIDENCE_INTEGRITY"


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _location_xyz(location: Any) -> list[float]:
    return [float(location.x), float(location.y), float(location.z)]


def _rotation_rpy(rotation: Any) -> list[float]:
    return [float(rotation.roll), float(rotation.pitch), float(rotation.yaw)]


def _status_name(node: Any) -> str | None:
    status = getattr(node, "status", None)
    if status is None:
        return None
    name = getattr(status, "name", None)
    if isinstance(name, str) and name:
        return name
    text = str(status)
    return text.rsplit(".", 1)[-1] if text else None


def _transform_world(transform: Any) -> dict[str, Any] | None:
    if transform is None or getattr(transform, "location", None) is None:
        return None
    try:
        value = {
            "location_xyz": _location_xyz(transform.location),
            "rotation_roll_pitch_yaw_degrees": _rotation_rpy(transform.rotation),
            "frame": "CARLA_WORLD",
            "translation_unit": "METRE",
            "rotation_unit": "DEGREE",
        }
    except (AttributeError, TypeError, ValueError):
        return None
    flattened = value["location_xyz"] + value["rotation_roll_pitch_yaw_degrees"]
    return value if all(math.isfinite(item) for item in flattened) else None


def _vector_world(vector: Any) -> dict[str, Any] | None:
    if vector is None:
        return None
    try:
        xyz = _location_xyz(vector)
    except (AttributeError, TypeError, ValueError):
        return None
    if not all(math.isfinite(item) for item in xyz):
        return None
    return {
        "xyz": xyz,
        "magnitude_metres_per_second": math.sqrt(sum(item * item for item in xyz)),
        "frame": "CARLA_WORLD",
        "unit": "METRE_PER_SECOND",
    }


def _point_segment_distance_3d(point: Sequence[float], start: Sequence[float], end: Sequence[float]) -> float:
    delta = [float(end[index]) - float(start[index]) for index in range(3)]
    relative = [float(point[index]) - float(start[index]) for index in range(3)]
    squared = sum(item * item for item in delta)
    ratio = 0.0 if squared <= 1e-12 else max(0.0, min(1.0, sum(relative[i] * delta[i] for i in range(3)) / squared))
    closest = [float(start[index]) + ratio * delta[index] for index in range(3)]
    return math.sqrt(sum((float(point[index]) - closest[index]) ** 2 for index in range(3)))


def _branch_interference(location_xyz: Sequence[float], branch_evidence: Mapping[str, Any]) -> dict[str, Any]:
    comparisons = []
    for branch in branch_evidence.get("branches", []):
        centerline = branch.get("centerline_world_xyz", []) if isinstance(branch, Mapping) else []
        distances = [
            _point_segment_distance_3d(location_xyz, left, right)
            for left, right in zip(centerline, centerline[1:])
            if len(left) == 3 and len(right) == 3
        ]
        target = branch.get("target_region", {}) if isinstance(branch, Mapping) else {}
        target_center = target.get("center_world_xyz") if isinstance(target, Mapping) else None
        target_distance = (
            math.sqrt(sum((float(location_xyz[index]) - float(target_center[index])) ** 2 for index in range(3)))
            if isinstance(target_center, Sequence) and len(target_center) == 3
            else None
        )
        radius = float(target.get("radius", 0.0)) if isinstance(target, Mapping) else 0.0
        minimum_centerline = min(distances) if distances else None
        interferes = (
            (minimum_centerline is not None and minimum_centerline <= max(3.5, radius))
            or (target_distance is not None and target_distance <= radius)
        )
        comparisons.append(
            {
                "branch_evidence_id": branch.get("branch_evidence_id") if isinstance(branch, Mapping) else None,
                "minimum_centerline_distance_metres_3d": minimum_centerline,
                "target_center_distance_metres_3d": target_distance,
                "target_radius_metres": radius,
                "interferes": interferes,
            }
        )
    return {
        "comparison_frame": "CARLA_WORLD_3D",
        "comparisons": comparisons,
        "interferes_with_frozen_branches": any(item["interferes"] for item in comparisons),
    }


def build_scenario_runtime_evidence(
    *,
    run_id: str,
    captured_at_utc: str,
    observation: Mapping[str, Any],
    map_binding: Mapping[str, Any],
    scenario_binding: Mapping[str, Any],
    route_scenario: Any,
    route_blackboard_value: Any,
    branch_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """Capture and adjudicate one exact OppositeVehicleRunningRedLight snapshot.

    The function is CARLA/torch import-free.  Live objects are read once through their public
    scenario/actor attributes; no control, planner, PID, tick, wait, or actor mutation occurs.
    """

    expected_name = scenario_binding.get("name")
    expected_class = scenario_binding.get("type")
    config = None
    instance = None
    config_index = None
    instance_index = None
    configs = list(getattr(route_scenario, "scenario_configurations", ())) if route_scenario is not None else []
    matching_configs = [
        (index, item)
        for index, item in enumerate(configs)
        if getattr(item, "name", None) == expected_name and getattr(item, "type", None) == expected_class
    ]
    if len(matching_configs) == 1:
        config_index, config = matching_configs[0]
    scenarios = list(getattr(route_scenario, "list_scenarios", ())) if route_scenario is not None else []
    matching_instances = [
        (index, item)
        for index, item in enumerate(scenarios)
        if config is not None
        and getattr(item, "config", None) is config
        and type(item).__name__ == expected_class
    ]
    if len(matching_instances) == 1:
        instance_index, instance = matching_instances[0]

    route_tree_status = _status_name(getattr(route_scenario, "scenario_tree", None))
    route_triggerer_status = _status_name(getattr(route_scenario, "scenario_triggerer", None))
    scenario_tree_status = _status_name(getattr(instance, "scenario_tree", None))
    behavior_tree = getattr(instance, "behavior_tree", None)
    behavior_tree_status = _status_name(behavior_tree)
    behavior_children = list(getattr(behavior_tree, "children", ())) if behavior_tree is not None else []
    route_gate = behavior_children[0] if behavior_children else None
    route_gate_status = _status_name(route_gate)
    status_values = (
        route_tree_status,
        route_triggerer_status,
        scenario_tree_status,
        behavior_tree_status,
        route_gate_status,
    )
    statuses_known = all(value is not None for value in status_values)
    statuses_pretrigger = (
        statuses_known
        and all(value == "INVALID" for value in status_values)
        and route_blackboard_value is False
    )

    trigger_point = None
    trigger_points = list(getattr(config, "trigger_points", ())) if config is not None else []
    if trigger_points:
        trigger_point = _transform_world(trigger_points[0])
    collision_location = getattr(instance, "_collision_location", None)
    collision_location_xyz = None
    if collision_location is not None:
        try:
            collision_location_xyz = _location_xyz(collision_location)
        except (AttributeError, TypeError, ValueError):
            collision_location_xyz = None

    actors = list(getattr(instance, "other_actors", ())) if instance is not None else []
    snapshots = []
    for index, actor in enumerate(actors):
        try:
            transform = actor.get_transform()
        except Exception:
            transform = None
        try:
            velocity = actor.get_velocity()
        except Exception:
            velocity = None
        transform_record = _transform_world(transform)
        velocity_record = _vector_world(velocity)
        active_spawn = _transform_world(getattr(instance, "_spawn_location", None))
        vertical_offset = None
        if transform_record is not None and active_spawn is not None:
            vertical_offset = (
                transform_record["location_xyz"][2] - active_spawn["location_xyz"][2]
            )
        branch_comparison = (
            _branch_interference(transform_record["location_xyz"], branch_evidence)
            if transform_record is not None
            else None
        )
        attributes = getattr(actor, "attributes", {})
        if not isinstance(attributes, Mapping):
            attributes = {}
        alive = getattr(actor, "is_alive", None)
        if callable(alive):
            alive = alive()
        is_active = getattr(actor, "is_active", None)
        if callable(is_active):
            is_active = is_active()
        is_dormant = getattr(actor, "is_dormant", None)
        if callable(is_dormant):
            is_dormant = is_dormant()
        inactive_contract_satisfied = (
            alive is True
            and vertical_offset is not None
            and vertical_offset <= -499.0
            and velocity_record is not None
            and velocity_record["magnitude_metres_per_second"] <= 0.05
        )
        snapshots.append(
            {
                "actor_id": getattr(actor, "id", None),
                "type_id": getattr(actor, "type_id", None),
                "role_name": attributes.get("role_name"),
                "alive": alive,
                "transform_world": transform_record,
                "velocity_world": velocity_record,
                "physics_enabled": None,
                "physics_state_equivalent": {
                    "public_runtime_getter_available": False,
                    "source_contract_set_simulate_physics_false_before_tree_tick": True,
                    "source_contract_initial_vertical_offset_metres": -500.0,
                    "runtime_vertical_offset_from_active_spawn_metres": vertical_offset,
                    "runtime_inactive_contract_satisfied": inactive_contract_satisfied,
                },
                "visibility_inactive_evidence": {
                    "is_active_if_exposed": is_active,
                    "is_dormant_if_exposed": is_dormant,
                    "below_ground_and_stationary": inactive_contract_satisfied,
                },
                "branch_interference": branch_comparison,
                "source_identity": {
                    "scenario_instance_object_id": id(instance),
                    "scenario_other_actors_index": index,
                    "actor_object_id": id(actor),
                    "exact_other_actors_member": instance is not None and instance.other_actors[index] is actor,
                },
                "captured_frame": observation.get("carla_frame"),
            }
        )

    identity = {
        "route_scenario_object_id": id(route_scenario) if route_scenario is not None else None,
        "scenario_configuration_index": config_index,
        "scenario_configuration_object_id": id(config) if config is not None else None,
        "scenario_instance_index": instance_index,
        "scenario_instance_object_id": id(instance) if instance is not None else None,
        "exact_config_object_identity": instance is not None and getattr(instance, "config", None) is config,
        "binding_method": "EXACT_ROUTE_SCENARIO_LIST_MEMBER_AND_CONFIG_OBJECT_IDENTITY",
    }
    actor_identity_known = bool(snapshots) and all(
        item["actor_id"] is not None
        and item["type_id"]
        and item["role_name"]
        and item["source_identity"]["exact_other_actors_member"] is True
        for item in snapshots
    )
    branch_interference = any(
        item.get("branch_interference", {}).get("interferes_with_frozen_branches") is True
        for item in snapshots
        if isinstance(item.get("branch_interference"), Mapping)
    )
    actors_inactive = bool(snapshots) and all(
        item["physics_state_equivalent"]["runtime_inactive_contract_satisfied"] is True
        for item in snapshots
    )
    trigger_contract_known = (
        scenario_binding.get("scenario_source_path")
        and scenario_binding.get("scenario_source_sha256")
        and getattr(config, "route_var_name", None)
        and route_blackboard_value is not None
        and route_gate is not None
        and getattr(instance, "_sync_time", None) is not None
        and getattr(instance, "_min_trigger_dist", None) is not None
        and trigger_point is not None
        and collision_location_xyz is not None
    )

    if expected_class != SUPPORTED_SCENARIO_CLASS:
        verdict = reason = SCENARIO_EVIDENCE_INTEGRITY
    elif observation.get("carla_frame") is None or observation.get("observation_id") is None:
        verdict = reason = SCENARIO_EVIDENCE_INTEGRITY
    elif not identity["exact_config_object_identity"] or not actor_identity_known:
        verdict = reason = SCENARIO_ACTOR_IDENTITY_UNAVAILABLE
    elif not statuses_known or not trigger_contract_known:
        verdict = reason = SCENARIO_RUNTIME_STATE_UNKNOWN
    elif not statuses_pretrigger:
        verdict = reason = SCENARIO_ALREADY_ACTIVE
    elif branch_interference:
        verdict = reason = SCENARIO_BRANCH_INTERFERENCE
    elif not actors_inactive:
        verdict = reason = SCENARIO_ALREADY_ACTIVE
    else:
        verdict = reason = SCENARIO_RUNTIME_FAIRNESS_PASS

    evidence = {
        "schema_version": SCENARIO_RUNTIME_EVIDENCE_SCHEMA,
        "run_id": run_id,
        "observation_id": observation.get("observation_id"),
        "carla_frame": observation.get("carla_frame"),
        "simulation_timestamp": observation.get("simulation_timestamp_seconds"),
        "captured_at_utc": captured_at_utc,
        "captured_before_first_candidate_output": True,
        "persisted_before_first_candidate_output": True,
        "atomic_frozen_package_persistence_required": True,
        "scenario_config_name": getattr(config, "name", None),
        "scenario_class_name": type(instance).__name__ if instance is not None else None,
        "scenario_instance_identity": identity,
        "scenario_tree_status": scenario_tree_status,
        "scenario_behavior_tree_status": behavior_tree_status,
        "route_scenario_tree_status": route_tree_status,
        "route_scenario_triggerer_status": route_triggerer_status,
        "trigger_contract_source": {
            "scenario_class": SUPPORTED_SCENARIO_CLASS,
            "scenario_source_path": scenario_binding.get("scenario_source_path"),
            "scenario_source_sha256": scenario_binding.get("scenario_source_sha256"),
            "route_gate": type(route_gate).__name__ if route_gate is not None else None,
            "route_gate_name": getattr(route_gate, "name", None),
            "route_gate_status": route_gate_status,
            "route_blackboard_variable": getattr(config, "route_var_name", None),
            "route_blackboard_value": route_blackboard_value,
            "activation_order": "ROUTE_BLACKBOARD_GATE_THEN_ACTOR_TRANSFORM_SETTER_THEN_INTERNAL_ADVERSARY_TRIGGER",
        },
        "trigger_location_world": trigger_point,
        "trigger_threshold": {
            "internal_time_to_arrival_seconds": getattr(instance, "_sync_time", None),
            "internal_minimum_distance_metres": getattr(instance, "_min_trigger_dist", None),
            "collision_location_world_xyz": collision_location_xyz,
        },
        "trigger_state": "PRE_TRIGGER" if statuses_pretrigger else ("ACTIVE_OR_COMPLETED" if statuses_known else "UNKNOWN"),
        "scenario_actor_snapshots": snapshots,
        "branch_evidence_sha256": branch_evidence.get("evidence_sha256"),
        "map_binding": dict(map_binding),
        "fairness_verdict": verdict,
        "fairness_reason_code": reason,
        "fairness_scope": "FIRST_FROZEN_OBSERVATION_CANDIDATE_COMPARISON_ONLY_NOT_EPISODE_SAFETY",
    }
    return with_hash(evidence)


def _waypoint_sort_key(waypoint: Any) -> tuple[Any, ...]:
    return (
        int(getattr(waypoint, "road_id", 0)),
        int(getattr(waypoint, "section_id", 0)),
        int(getattr(waypoint, "lane_id", 0)),
        round(float(getattr(waypoint, "s", 0.0)), 6),
        str(getattr(waypoint, "id", "")),
    )


def _waypoint_identity(waypoint: Any) -> dict[str, Any]:
    return {
        "carla_waypoint_id": str(getattr(waypoint, "id", "")),
        "road_id": int(getattr(waypoint, "road_id", 0)),
        "section_id": int(getattr(waypoint, "section_id", 0)),
        "lane_id": int(getattr(waypoint, "lane_id", 0)),
        "s": float(getattr(waypoint, "s", 0.0)),
        "is_junction": bool(getattr(waypoint, "is_junction", False)),
    }


def enumerate_forward_paths(
    start_waypoint: Any,
    *,
    step_metres: float = 2.0,
    horizon_metres: float = 36.0,
    maximum_paths: int = 64,
) -> list[list[Any]]:
    """Enumerate deterministic forward topology paths without ticking the world."""

    if step_metres <= 0.0 or horizon_metres <= step_metres:
        raise ValueError("BRANCH_PATH_DISTANCE_CONTRACT")
    steps = int(math.ceil(horizon_metres / step_metres))
    active: list[list[Any]] = [[start_waypoint]]
    completed: list[list[Any]] = []
    for _ in range(steps):
        next_active: list[list[Any]] = []
        for path in active:
            successors = sorted(list(path[-1].next(step_metres)), key=_waypoint_sort_key)
            current_ids = {str(getattr(item, "id", "")) for item in path[-4:]}
            successors = [item for item in successors if str(getattr(item, "id", "")) not in current_ids]
            if not successors:
                completed.append(path)
                continue
            for successor in successors:
                next_active.append(path + [successor])
        deduplicated: dict[tuple[str, ...], list[Any]] = {}
        for path in next_active:
            key = tuple(str(getattr(item, "id", "")) for item in path)
            deduplicated[key] = path
        active = [deduplicated[key] for key in sorted(deduplicated)]
        if len(active) > maximum_paths:
            raise RuntimeError("BRANCH_PATH_ENUMERATION_LIMIT")
        if not active:
            break
    completed.extend(active)
    return completed


def _path_descriptor(
    path: Sequence[Any],
    *,
    origin_world_xy: Sequence[float],
    world_to_ego: Sequence[Sequence[float]],
) -> dict[str, Any]:
    centerline = [_location_xyz(item.transform.location) for item in path]
    endpoint_ego = apply_planar(world_to_ego, centerline[-1][:2])
    angle_deg = math.degrees(math.atan2(endpoint_ego[1], endpoint_ego[0]))
    distance = math.hypot(endpoint_ego[0], endpoint_ego[1])
    world_vector = (
        centerline[-1][0] - float(origin_world_xy[0]),
        centerline[-1][1] - float(origin_world_xy[1]),
    )
    norm = math.hypot(*world_vector)
    direction = [world_vector[0] / norm, world_vector[1] / norm] if norm > 1e-9 else [0.0, 0.0]
    return {
        "path": list(path),
        "centerline_world_xyz": centerline,
        "endpoint_ego_xy": list(endpoint_ego),
        "endpoint_distance_metres": distance,
        "signed_endpoint_angle_deg_ego_y_right": angle_deg,
        "approach_direction_world_unit": direction,
    }


def discover_straight_right_branches(
    carla_map: Any,
    ego_location: Any,
    *,
    origin_world_xy: Sequence[float],
    yaw_rad: float,
    opendrive_sha256: str,
    step_metres: float = 2.0,
    horizon_metres: float = 36.0,
) -> dict[str, Any]:
    """Freeze one straight and one right branch selected only from CARLA topology."""

    contract = planar_transforms(origin_world_xy, yaw_rad)
    start = carla_map.get_waypoint(ego_location, project_to_road=True)
    if start is None:
        raise RuntimeError("EGO_MAP_WAYPOINT_UNAVAILABLE")
    paths = enumerate_forward_paths(
        start,
        step_metres=step_metres,
        horizon_metres=horizon_metres,
    )
    descriptors = [
        _path_descriptor(
            path,
            origin_world_xy=origin_world_xy,
            world_to_ego=contract["world_to_ego"],
        )
        for path in paths
    ]
    descriptors = [item for item in descriptors if item["endpoint_distance_metres"] >= 15.0]
    straight = [item for item in descriptors if abs(item["signed_endpoint_angle_deg_ego_y_right"]) <= 25.0]
    right = [item for item in descriptors if 30.0 <= item["signed_endpoint_angle_deg_ego_y_right"] <= 120.0]
    if not straight:
        raise RuntimeError("STRAIGHT_BRANCH_NOT_FOUND_AT_FROZEN_OBSERVATION")
    if not right:
        raise RuntimeError("RIGHT_BRANCH_NOT_FOUND_AT_FROZEN_OBSERVATION")
    straight.sort(
        key=lambda item: (
            abs(item["signed_endpoint_angle_deg_ego_y_right"]),
            tuple(_waypoint_sort_key(wp) for wp in item["path"]),
        )
    )
    right.sort(
        key=lambda item: (
            abs(item["signed_endpoint_angle_deg_ego_y_right"] - 75.0),
            tuple(_waypoint_sort_key(wp) for wp in item["path"]),
        )
    )
    selected = (("STRAIGHT_LANE_FOLLOW", straight[0]), ("RIGHT_TURN_BRANCH", right[0]))
    branches = []
    for semantic_role, descriptor in selected:
        path = descriptor["path"]
        waypoint_ids = [_waypoint_identity(item) for item in path]
        identity_payload = {
            "opendrive_sha256": opendrive_sha256,
            "map_waypoint_ids": waypoint_ids,
            "centerline_world_xyz": descriptor["centerline_world_xyz"],
        }
        branch_id = "branch:" + deterministic_hash(identity_payload)
        lane_width = float(getattr(path[-1], "lane_width", 3.5))
        target = descriptor["centerline_world_xyz"][-1]
        branches.append(
            {
                "branch_evidence_id": branch_id,
                "semantic_role": semantic_role,
                "map_waypoint_ids": waypoint_ids,
                "centerline_world_xyz": descriptor["centerline_world_xyz"],
                "approach_direction_world_unit": descriptor["approach_direction_world_unit"],
                "signed_endpoint_angle_deg_ego_y_right": descriptor[
                    "signed_endpoint_angle_deg_ego_y_right"
                ],
                "target_region": {
                    "kind": "CIRCLE_ON_LANE_CENTER",
                    "center_world_xyz": target,
                    "radius": max(1.0, lane_width * 0.75),
                    "frame": "CARLA_WORLD",
                    "unit": "METRE",
                },
            }
        )
    evidence = {
        "selection_algorithm": BRANCH_SELECTION_ALGORITHM,
        "selection_parameters": {
            "step_metres": step_metres,
            "horizon_metres": horizon_metres,
            "straight_angle_gate_deg": [-25.0, 25.0],
            "right_angle_gate_deg": [30.0, 120.0],
            "minimum_endpoint_distance_metres": 15.0,
        },
        "frame": "CARLA_WORLD",
        "unit": "METRE",
        "origin_world_xy": [float(origin_world_xy[0]), float(origin_world_xy[1])],
        "branches": branches,
        "candidate_outputs_read_during_selection": False,
    }
    return with_hash(evidence)


def _definition(
    *,
    condition_id: str,
    semantic_definition: str,
    hlc: str,
    target_branch: Mapping[str, Any],
    world_to_ego: Sequence[Sequence[float]],
    speed_text: str,
    frozen_at_utc: str,
) -> dict[str, Any]:
    center = target_branch["target_region"]["center_world_xyz"]
    target_ego = apply_planar(world_to_ego, center[:2])
    if hlc == "LANEFOLLOW_STRAIGHT":
        command = "follow the lane straight through the next junction"
    elif hlc == "RIGHT_TURN":
        command = "take the right branch at the next junction"
    else:
        raise ValueError("CANDIDATE_HLC_UNSUPPORTED")
    prompt = (
        f"Current speed: {speed_text} m/s. Command: {command}. "
        "What should the ego do next?"
    )
    value = {
        "condition_id": condition_id,
        "semantic_definition": semantic_definition,
        "hlc": hlc,
        "candidate_dependent_fields": ["HLC", "target_point", "prompt"],
        "intended_target_branch_evidence_id": target_branch["branch_evidence_id"],
        "target_point_ego_xy": list(target_ego),
        "target_point_frame": RAW_ROUTE_FRAME,
        "target_point_unit": "METRE_FROM_VALIDATED_CARLA_TRANSFORM",
        "prompt": prompt,
        "defined_at_utc": frozen_at_utc,
        "defined_before_output": True,
        "derived_from_candidate_output": False,
    }
    return with_hash(value, "definition_sha256")


def build_frozen_package(
    *,
    run_id: str,
    frozen_at_utc: str,
    observation: Mapping[str, Any],
    map_binding: Mapping[str, Any],
    origin_world_xy: Sequence[float],
    yaw_rad: float,
    runtime_coordinate_validation: Mapping[str, Any],
    branch_evidence: Mapping[str, Any],
    scenario_binding: Mapping[str, Any],
    scenario_runtime_evidence: Mapping[str, Any],
    model_binding: Mapping[str, Any],
    schedule_binding: Mapping[str, Any],
    speed_metres_per_second: float,
    capture_protocol: str = CAPTURE_PROTOCOL,
) -> dict[str, Any]:
    contract = planar_transforms(origin_world_xy, yaw_rad)
    contract["static_validation"] = transform_validation(contract)
    contract["runtime_validation"] = dict(runtime_coordinate_validation)
    by_role = {
        item["semantic_role"]: item for item in branch_evidence.get("branches", [])
    }
    timestamp = frozen_at_utc or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    definitions = [
        _definition(
            condition_id="condition:straight",
            semantic_definition="straight / lane-follow branch",
            hlc="LANEFOLLOW_STRAIGHT",
            target_branch=by_role["STRAIGHT_LANE_FOLLOW"],
            world_to_ego=contract["world_to_ego"],
            speed_text=str(round(float(speed_metres_per_second), 1)),
            frozen_at_utc=timestamp,
        ),
        _definition(
            condition_id="condition:right",
            semantic_definition="right-turn / take-branch condition",
            hlc="RIGHT_TURN",
            target_branch=by_role["RIGHT_TURN_BRANCH"],
            world_to_ego=contract["world_to_ego"],
            speed_text=str(round(float(speed_metres_per_second), 1)),
            frozen_at_utc=timestamp,
        ),
    ]
    package = {
        "schema_version": FROZEN_SCHEMA,
        "capture_protocol": capture_protocol,
        "run_id": run_id,
        "freeze": {
            "frozen_at_utc": timestamp,
            "persisted_before_first_inference": True,
            "first_inference_sequence_position": None,
            "observation_selection_rule": "FIRST_MODEL_READY_OBSERVATION_ONLY",
            "outputs_read_before_freeze": False,
        },
        "observation": dict(observation),
        "map_binding": dict(map_binding),
        "scenario_binding": dict(scenario_binding),
        "coordinate_contract": contract,
        "branch_evidence": dict(branch_evidence),
        "scenario_runtime_evidence": dict(scenario_runtime_evidence),
        "candidate_definitions": definitions,
        "model_binding": dict(model_binding),
        "schedule_binding": dict(schedule_binding),
        "mapping_contract": {
            "mode": MAPPING_MODE,
            "raw_route_frame": RAW_ROUTE_FRAME,
            "raw_route_unit": RAW_ROUTE_UNIT,
            "model_raw_values_are_not_metres": True,
            "uses_only_normalized_route_direction": True,
            "expected_pair_class_available_to_runtime_predictor": False,
            "candidate_name_can_determine_branch": False,
        },
    }
    return with_hash(package, "package_sha256")


def tensor_like_identity(value: Any) -> dict[str, Any]:
    """Return a bit-preserving identity for a tensor/array-like value.

    Torch intentionally is not imported here.  In particular, converting a
    ``torch.bfloat16`` tensor through ``Tensor.numpy()`` is unsupported in the
    SimLingo environment.  A torch-like tensor is therefore canonicalized to
    a contiguous CPU clone and read through its untyped byte storage.  The
    dtype is never converted, so the digest binds the tensor's original bit
    representation rather than a float32 approximation.
    """

    if value is None:
        return {"kind": "none", "sha256": deterministic_hash({"kind": "none"})}

    source_shape = list(getattr(value, "shape", ())) if hasattr(value, "shape") else None
    source_dtype = str(getattr(value, "dtype", type(value).__name__))
    source_device = str(getattr(value, "device", "cpu"))
    source_requires_grad = bool(getattr(value, "requires_grad", False))
    stride_member = getattr(value, "stride", None)
    if callable(stride_member):
        source_stride = list(stride_member())
    else:
        raw_strides = getattr(value, "strides", None)
        source_stride = list(raw_strides) if raw_strides is not None else None
    contiguous_member = getattr(value, "is_contiguous", None)
    source_contiguous = bool(contiguous_member()) if callable(contiguous_member) else None

    detached = value.detach() if callable(getattr(value, "detach", None)) else value
    cpu_value = detached.cpu() if callable(getattr(detached, "cpu", None)) else detached
    contiguous = cpu_value.contiguous() if callable(getattr(cpu_value, "contiguous", None)) else cpu_value

    # A clone gives canonical storage_offset=0 and prevents a contiguous view
    # from exposing unrelated bytes that happen to share its backing storage.
    canonical = contiguous.clone() if callable(getattr(contiguous, "clone", None)) else contiguous
    untyped_storage = getattr(canonical, "untyped_storage", None)
    if callable(untyped_storage):
        storage_bytes = bytes(untyped_storage())
        storage_offset_member = getattr(canonical, "storage_offset", None)
        element_size_member = getattr(canonical, "element_size", None)
        numel_member = getattr(canonical, "numel", None)
        storage_offset = int(storage_offset_member()) if callable(storage_offset_member) else 0
        element_size = int(element_size_member()) if callable(element_size_member) else 1
        element_count = int(numel_member()) if callable(numel_member) else None
        byte_offset = storage_offset * element_size
        expected_bytes = element_count * element_size if element_count is not None else None
        raw = (
            storage_bytes[byte_offset : byte_offset + expected_bytes]
            if expected_bytes is not None
            else storage_bytes[byte_offset:]
        )
        if expected_bytes is not None and len(raw) != expected_bytes:
            raise RuntimeError("TENSOR_IDENTITY_RAW_BYTE_LENGTH_MISMATCH")
        canonical_stride_member = getattr(canonical, "stride", None)
        canonical_stride = (
            list(canonical_stride_member()) if callable(canonical_stride_member) else None
        )
        canonical_contiguous_member = getattr(canonical, "is_contiguous", None)
        canonical_contiguous = (
            bool(canonical_contiguous_member())
            if callable(canonical_contiguous_member)
            else True
        )
    elif callable(getattr(canonical, "numpy", None)):
        array = canonical.numpy()
        raw = array.tobytes(order="C")
        canonical_stride = list(getattr(array, "strides", ()))
        flags = getattr(array, "flags", None)
        canonical_contiguous = bool(getattr(flags, "c_contiguous", True))
    elif callable(getattr(canonical, "tobytes", None)):
        raw = canonical.tobytes()
        canonical_stride = list(getattr(canonical, "strides", ()))
        canonical_contiguous = True
    else:
        raw = repr(canonical).encode("utf-8")
        canonical_stride = None
        canonical_contiguous = None
    raw_sha256 = hashlib.sha256(raw).hexdigest()
    return {
        "kind": "tensor_or_array",
        "shape": source_shape,
        "dtype": source_dtype,
        "source_stride": source_stride,
        "source_contiguous": source_contiguous,
        "canonical_stride": canonical_stride,
        "canonical_contiguous": canonical_contiguous,
        "device_before_cpu_copy": source_device,
        "requires_grad": source_requires_grad,
        "canonical_raw_bytes_sha256": raw_sha256,
        "sha256": raw_sha256,
        "bytes": len(raw),
    }
