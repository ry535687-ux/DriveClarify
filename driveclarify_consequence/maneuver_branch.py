"""CPU-only MANEUVER_BRANCH plan-to-Task mapping.

The mapper deliberately treats SimLingo route values as model-native numbers.  It uses only
their direction after a validated ego/world rotation; it never promotes those numbers to
metres.  Metric positions and target regions belong exclusively to frozen CARLA map evidence.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from .serialization import canonical_json, write_json
from .task_atoms import PairClass, TaskState


FROZEN_SCHEMA = "driveclarify.maneuver_branch.frozen_observation.v2"
SCENARIO_RUNTIME_EVIDENCE_SCHEMA = "driveclarify.maneuver_branch.scenario_runtime_evidence.v1"
SCENARIO_RUNTIME_FAIRNESS_PASS = "PASS_SCENARIO_RUNTIME_FAIRNESS"
CANDIDATE_SCHEMA = "driveclarify.maneuver_branch.candidate_record.v1"
PREDICTOR_SCHEMA = "driveclarify.maneuver_branch.task_evaluation_p.v1"
GROUND_TRUTH_SCHEMA = "driveclarify.maneuver_branch.task_ground_truth_h.v1"
PAIR_SCHEMA = "driveclarify.maneuver_branch.pair_equivalence.v1"
REPEATABILITY_SCHEMA = "driveclarify.maneuver_branch.repeatability.v1"

RAW_ROUTE_FRAME = "MODEL_LOCAL_RAW_X_FORWARD_Y_RIGHT"
RAW_ROUTE_UNIT = "MODEL_NATIVE_UNCALIBRATED"
MAPPING_MODE = "UNIT_FREE_DIRECTIONAL_BRANCH_APPROACH_V1"


def deterministic_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def with_hash(value: Mapping[str, Any], field: str = "evidence_sha256") -> dict[str, Any]:
    result = copy.deepcopy(dict(value))
    result.pop(field, None)
    result[field] = deterministic_hash(result)
    return result


def verify_hash(value: Mapping[str, Any], field: str = "evidence_sha256") -> bool:
    recorded = value.get(field)
    if not isinstance(recorded, str) or len(recorded) != 64:
        return False
    unsigned = copy.deepcopy(dict(value))
    unsigned.pop(field, None)
    return recorded == deterministic_hash(unsigned)


def planar_transforms(origin_world_xy: Sequence[float], yaw_rad: float) -> dict[str, Any]:
    """Return explicit homogeneous CARLA-world/ego planar transforms.

    CARLA world and this ego frame are left-handed.  Ego +x is forward and ego +y is right.
    ``yaw_rad`` is the actual navigation yaw used to construct SimLingo's target point.
    """

    if len(origin_world_xy) != 2:
        raise ValueError("TRANSFORM_ORIGIN_REQUIRES_XY")
    ox, oy = (float(origin_world_xy[0]), float(origin_world_xy[1]))
    yaw = float(yaw_rad)
    if not all(math.isfinite(item) for item in (ox, oy, yaw)):
        raise ValueError("TRANSFORM_VALUE_NONFINITE")
    c, s = math.cos(yaw), math.sin(yaw)
    world_to_ego = [
        [c, s, -(c * ox + s * oy)],
        [-s, c, s * ox - c * oy],
        [0.0, 0.0, 1.0],
    ]
    ego_to_world = [
        [c, -s, ox],
        [s, c, oy],
        [0.0, 0.0, 1.0],
    ]
    return {
        "world_to_ego": world_to_ego,
        "ego_to_world": ego_to_world,
        "world_to_ego_direction": "CARLA_WORLD_XY_TO_MODEL_EGO_X_FORWARD_Y_RIGHT",
        "ego_to_world_direction": "MODEL_EGO_X_FORWARD_Y_RIGHT_TO_CARLA_WORLD_XY",
        "coordinate_handedness": "CARLA_LEFT_HANDED_Z_UP",
        "world_frame": "CARLA_WORLD",
        "world_unit": "METRE",
        "model_frame": RAW_ROUTE_FRAME,
        "model_raw_unit": RAW_ROUTE_UNIT,
        "model_raw_is_not_declared_metre": True,
        "mapping_mode": MAPPING_MODE,
    }


def apply_planar(matrix: Sequence[Sequence[float]], point: Sequence[float]) -> tuple[float, float]:
    if len(matrix) != 3 or any(len(row) != 3 for row in matrix) or len(point) != 2:
        raise ValueError("PLANAR_TRANSFORM_SHAPE")
    x, y = float(point[0]), float(point[1])
    px = float(matrix[0][0]) * x + float(matrix[0][1]) * y + float(matrix[0][2])
    py = float(matrix[1][0]) * x + float(matrix[1][1]) * y + float(matrix[1][2])
    pw = float(matrix[2][0]) * x + float(matrix[2][1]) * y + float(matrix[2][2])
    if not math.isfinite(pw) or abs(pw) < 1e-12:
        raise ValueError("PLANAR_TRANSFORM_HOMOGENEOUS_W")
    return px / pw, py / pw


def transform_validation(contract: Mapping[str, Any]) -> dict[str, Any]:
    reasons: list[str] = []
    world_to_ego = contract.get("world_to_ego")
    ego_to_world = contract.get("ego_to_world")
    probes = ((0.0, 0.0), (1.25, -3.5), (-7.0, 4.0))
    max_error: float | None = None
    try:
        errors = []
        for probe in probes:
            world = apply_planar(ego_to_world, probe)  # type: ignore[arg-type]
            restored = apply_planar(world_to_ego, world)  # type: ignore[arg-type]
            errors.extend(abs(restored[index] - probe[index]) for index in (0, 1))
        max_error = max(errors)
        if max_error > 1e-9:
            reasons.append("TRANSFORM_ROUND_TRIP_FAILED")
        origin = apply_planar(ego_to_world, (0.0, 0.0))  # type: ignore[arg-type]
        forward = apply_planar(ego_to_world, (1.0, 0.0))  # type: ignore[arg-type]
        right = apply_planar(ego_to_world, (0.0, 1.0))  # type: ignore[arg-type]
        forward_vector = (forward[0] - origin[0], forward[1] - origin[1])
        right_vector = (right[0] - origin[0], right[1] - origin[1])
        dot = forward_vector[0] * right_vector[0] + forward_vector[1] * right_vector[1]
        cross_z = forward_vector[0] * right_vector[1] - forward_vector[1] * right_vector[0]
        if abs(dot) > 1e-9 or cross_z <= 0.0:
            reasons.append("TRANSFORM_DIRECTION_OR_HANDEDNESS_FAILED")
    except (TypeError, ValueError, IndexError, ZeroDivisionError):
        reasons.append("TRANSFORM_MISSING_OR_INVALID")
        origin = forward = right = None
        forward_vector = right_vector = None
        dot = cross_z = None
    return {
        "status": "PASS" if not reasons else "FAIL",
        "reason_codes": reasons,
        "round_trip_max_abs_error": max_error,
        "direction_probe": {
            "ego_origin_world": origin,
            "ego_forward_endpoint_world": forward,
            "ego_right_endpoint_world": right,
            "forward_world_vector": forward_vector,
            "right_world_vector": right_vector,
            "dot": dot,
            "cross_z_in_numeric_xy": cross_z,
        },
    }


def _finite_pair(value: Any) -> tuple[float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in value):
        return None
    result = float(value[0]), float(value[1])
    return result if all(math.isfinite(item) for item in result) else None


def _route_points(value: Any) -> list[tuple[float, float]] | None:
    route = value
    if isinstance(route, list) and len(route) == 1 and isinstance(route[0], list):
        route = route[0]
    if not isinstance(route, list) or len(route) < 2:
        return None
    points = [_finite_pair(item) for item in route]
    return None if any(item is None for item in points) else [item for item in points if item]


def _unit(vector: Sequence[float]) -> tuple[float, float] | None:
    x, y = float(vector[0]), float(vector[1])
    norm = math.hypot(x, y)
    if not math.isfinite(norm) or norm <= 1e-12:
        return None
    return x / norm, y / norm


def _angle_degrees(left: Sequence[float], right: Sequence[float]) -> float:
    dot = max(-1.0, min(1.0, float(left[0]) * float(right[0]) + float(left[1]) * float(right[1])))
    return math.degrees(math.acos(dot))


def validate_frozen_package(package: Mapping[str, Any]) -> tuple[str, ...]:
    errors: list[str] = []
    if package.get("schema_version") != FROZEN_SCHEMA:
        errors.append("FROZEN_SCHEMA_MISMATCH")
    if not verify_hash(package, "package_sha256"):
        errors.append("FROZEN_PACKAGE_HASH_MISMATCH")
    freeze = package.get("freeze")
    if not isinstance(freeze, Mapping) or freeze.get("persisted_before_first_inference") is not True:
        errors.append("CANDIDATE_TARGET_NOT_FROZEN_BEFORE_OUTPUT")
    observation = package.get("observation")
    if not isinstance(observation, Mapping):
        errors.append("OBSERVATION_BINDING_MISSING")
    else:
        for field in ("observation_id", "carla_frame", "simulation_timestamp_seconds", "input_identity_sha256"):
            if observation.get(field) is None:
                errors.append("OBSERVATION_FIELD_MISSING:" + field)
    map_binding = package.get("map_binding")
    if not isinstance(map_binding, Mapping):
        errors.append("MAP_BINDING_MISSING")
    else:
        for field in ("town", "map_name", "opendrive_sha256", "route_id", "route_xml_sha256"):
            if not map_binding.get(field):
                errors.append("MAP_BINDING_FIELD_MISSING:" + field)
    scenario_binding = package.get("scenario_binding")
    if not isinstance(scenario_binding, Mapping):
        errors.append("SCENARIO_BINDING_MISSING")
    else:
        for field in ("name", "type"):
            if not scenario_binding.get(field):
                errors.append("SCENARIO_BINDING_FIELD_MISSING:" + field)
    contract = package.get("coordinate_contract")
    if not isinstance(contract, Mapping):
        errors.append("TRANSFORM_MISSING")
    else:
        validation = transform_validation(contract)
        if validation["status"] != "PASS":
            errors.extend(validation["reason_codes"])
        recorded_validation = contract.get("runtime_validation")
        if not isinstance(recorded_validation, Mapping) or recorded_validation.get("status") != "PASS":
            errors.append("RUNTIME_COORDINATE_VALIDATION_NOT_PASS")
        if contract.get("mapping_mode") != MAPPING_MODE:
            errors.append("MAPPING_MODE_MISMATCH")
        if contract.get("model_raw_unit") != RAW_ROUTE_UNIT:
            errors.append("RAW_ROUTE_UNIT_CONTRACT_MISMATCH")
        if contract.get("model_raw_is_not_declared_metre") is not True:
            errors.append("RAW_ROUTE_PHYSICAL_UNIT_LEAK")
    evidence = package.get("branch_evidence")
    if not isinstance(evidence, Mapping):
        errors.append("BRANCH_EVIDENCE_MISSING")
    else:
        if not verify_hash(evidence):
            errors.append("BRANCH_EVIDENCE_HASH_MISMATCH")
        if evidence.get("frame") != "CARLA_WORLD" or evidence.get("unit") != "METRE":
            errors.append("BRANCH_EVIDENCE_FRAME_UNIT_MISMATCH")
        branches = evidence.get("branches")
        if not isinstance(branches, list) or len(branches) != 2:
            errors.append("BRANCH_COUNT_NOT_TWO")
        else:
            ids = []
            directions = []
            for branch in branches:
                if not isinstance(branch, Mapping):
                    errors.append("BRANCH_RECORD_INVALID")
                    continue
                branch_id = branch.get("branch_evidence_id")
                if not isinstance(branch_id, str) or not branch_id:
                    errors.append("BRANCH_ID_MISSING")
                else:
                    ids.append(branch_id)
                waypoint_ids = branch.get("map_waypoint_ids")
                if not isinstance(waypoint_ids, list) or len(waypoint_ids) < 2:
                    errors.append("BRANCH_WAYPOINT_IDS_MISSING")
                centerline = branch.get("centerline_world_xyz")
                if not isinstance(centerline, list) or len(centerline) < 2:
                    errors.append("BRANCH_CENTERLINE_MISSING")
                direction = _finite_pair(branch.get("approach_direction_world_unit"))
                if direction is None or _unit(direction) is None:
                    errors.append("BRANCH_APPROACH_DIRECTION_INVALID")
                else:
                    directions.append(_unit(direction))
                region = branch.get("target_region")
                if not isinstance(region, Mapping) or region.get("frame") != "CARLA_WORLD" or region.get("unit") != "METRE":
                    errors.append("BRANCH_TARGET_REGION_INVALID")
            if len(ids) != len(set(ids)):
                errors.append("BRANCH_ID_COLLISION")
            if len(directions) == 2 and _angle_degrees(directions[0], directions[1]) < 30.0:  # type: ignore[arg-type]
                errors.append("BRANCH_DIRECTIONS_NOT_SEPARATED")
    definitions = package.get("candidate_definitions")
    if not isinstance(definitions, list) or len(definitions) != 2:
        errors.append("CANDIDATE_DEFINITION_COUNT_NOT_TWO")
    else:
        definition_hashes = []
        branch_ids = {
            item.get("branch_evidence_id")
            for item in package.get("branch_evidence", {}).get("branches", [])
            if isinstance(item, Mapping)
        }
        for definition in definitions:
            if not isinstance(definition, Mapping) or not verify_hash(definition, "definition_sha256"):
                errors.append("CANDIDATE_DEFINITION_HASH_MISMATCH")
                continue
            definition_hashes.append(definition.get("definition_sha256"))
            if definition.get("defined_before_output") is not True:
                errors.append("CANDIDATE_TARGET_NOT_FROZEN_BEFORE_OUTPUT")
            if definition.get("intended_target_branch_evidence_id") not in branch_ids:
                errors.append("CANDIDATE_TARGET_BRANCH_UNKNOWN")
            if _finite_pair(definition.get("target_point_ego_xy")) is None:
                errors.append("CANDIDATE_TARGET_POINT_INVALID")
        if len(definition_hashes) != len(set(definition_hashes)):
            errors.append("CANDIDATE_DEFINITION_HASH_COLLISION")
    errors.extend(validate_scenario_runtime_evidence(package))
    return tuple(sorted(set(errors)))


def validate_scenario_runtime_evidence(package: Mapping[str, Any]) -> tuple[str, ...]:
    """Validate the V3 pilot fairness evidence before any plan-to-Task mapping."""

    errors: list[str] = []
    evidence = package.get("scenario_runtime_evidence")
    if not isinstance(evidence, Mapping):
        return ("SCENARIO_RUNTIME_EVIDENCE_MISSING",)
    if evidence.get("schema_version") != SCENARIO_RUNTIME_EVIDENCE_SCHEMA:
        errors.append("SCENARIO_RUNTIME_EVIDENCE_SCHEMA_MISMATCH")
    if not verify_hash(evidence):
        errors.append("SCENARIO_RUNTIME_EVIDENCE_HASH_MISMATCH")
    if evidence.get("captured_before_first_candidate_output") is not True:
        errors.append("SCENARIO_EVIDENCE_NOT_CAPTURED_BEFORE_OUTPUT")
    if evidence.get("persisted_before_first_candidate_output") is not True:
        errors.append("SCENARIO_EVIDENCE_NOT_PERSISTED_BEFORE_OUTPUT")
    if evidence.get("fairness_verdict") != SCENARIO_RUNTIME_FAIRNESS_PASS:
        errors.append("SCENARIO_RUNTIME_FAIRNESS_NOT_PASS")
    if evidence.get("fairness_reason_code") != SCENARIO_RUNTIME_FAIRNESS_PASS:
        errors.append("SCENARIO_RUNTIME_FAIRNESS_REASON_NOT_PASS")

    observation = package.get("observation")
    map_binding = package.get("map_binding")
    scenario_binding = package.get("scenario_binding")
    if not isinstance(observation, Mapping):
        errors.append("SCENARIO_EVIDENCE_OBSERVATION_BINDING_UNAVAILABLE")
    else:
        if evidence.get("observation_id") != observation.get("observation_id"):
            errors.append("SCENARIO_EVIDENCE_OBSERVATION_ID_MISMATCH")
        if evidence.get("carla_frame") != observation.get("carla_frame"):
            errors.append("SCENARIO_EVIDENCE_FRAME_MISMATCH")
        if evidence.get("simulation_timestamp") != observation.get("simulation_timestamp_seconds"):
            errors.append("SCENARIO_EVIDENCE_TIMESTAMP_MISMATCH")
    if evidence.get("run_id") != package.get("run_id"):
        errors.append("SCENARIO_EVIDENCE_RUN_ID_MISMATCH")
    if isinstance(map_binding, Mapping):
        evidence_map = evidence.get("map_binding")
        if not isinstance(evidence_map, Mapping):
            errors.append("SCENARIO_EVIDENCE_MAP_BINDING_MISSING")
        else:
            for field in ("town", "map_name", "opendrive_sha256", "route_id", "route_xml_sha256"):
                if evidence_map.get(field) != map_binding.get(field):
                    errors.append("SCENARIO_EVIDENCE_MAP_PIN_MISMATCH:" + field)
    if isinstance(scenario_binding, Mapping):
        if evidence.get("scenario_config_name") != scenario_binding.get("name"):
            errors.append("SCENARIO_EVIDENCE_CONFIG_NAME_MISMATCH")
        if evidence.get("scenario_class_name") != scenario_binding.get("type"):
            errors.append("SCENARIO_EVIDENCE_CLASS_NAME_MISMATCH")
        trigger_source = evidence.get("trigger_contract_source")
        if not isinstance(trigger_source, Mapping):
            errors.append("SCENARIO_TRIGGER_CONTRACT_SOURCE_MISSING")
        elif trigger_source.get("scenario_source_sha256") != scenario_binding.get("scenario_source_sha256"):
            errors.append("SCENARIO_TRIGGER_CONTRACT_SOURCE_PIN_MISMATCH")
        elif trigger_source.get("route_blackboard_value") is not False:
            errors.append("SCENARIO_ROUTE_BLACKBOARD_TRIGGER_NOT_FALSE")
    if evidence.get("trigger_state") != "PRE_TRIGGER":
        errors.append("SCENARIO_TRIGGER_STATE_NOT_PRETRIGGER")
    package_branch_evidence = package.get("branch_evidence")
    package_branch_hash = (
        package_branch_evidence.get("evidence_sha256")
        if isinstance(package_branch_evidence, Mapping)
        else None
    )
    if evidence.get("branch_evidence_sha256") != package_branch_hash:
        errors.append("SCENARIO_BRANCH_EVIDENCE_BINDING_MISMATCH")

    identity = evidence.get("scenario_instance_identity")
    if not isinstance(identity, Mapping) or identity.get("exact_config_object_identity") is not True:
        errors.append("SCENARIO_INSTANCE_IDENTITY_UNVERIFIED")
    snapshots = evidence.get("scenario_actor_snapshots")
    if not isinstance(snapshots, list) or not snapshots:
        errors.append("SCENARIO_ACTOR_SNAPSHOTS_EMPTY")
    else:
        for snapshot in snapshots:
            if not isinstance(snapshot, Mapping):
                errors.append("SCENARIO_ACTOR_SNAPSHOT_INVALID")
                continue
            actor_identity = snapshot.get("source_identity")
            if not isinstance(actor_identity, Mapping) or actor_identity.get("exact_other_actors_member") is not True:
                errors.append("SCENARIO_ACTOR_IDENTITY_UNVERIFIED")
            for field in ("actor_id", "type_id", "role_name", "alive", "transform_world", "velocity_world"):
                if snapshot.get(field) is None:
                    errors.append("SCENARIO_ACTOR_FIELD_MISSING:" + field)
            physics = snapshot.get("physics_state_equivalent")
            if not isinstance(physics, Mapping) or physics.get("runtime_inactive_contract_satisfied") is not True:
                errors.append("SCENARIO_ACTOR_INACTIVE_CONTRACT_NOT_PASS")
            if isinstance(observation, Mapping) and snapshot.get("captured_frame") != observation.get("carla_frame"):
                errors.append("SCENARIO_ACTOR_FRAME_MISMATCH")
    return tuple(sorted(set(errors)))


def _unknown_mapping(record: Mapping[str, Any], reason: str, details: Sequence[str] = ()) -> dict[str, Any]:
    return {
        "candidate_id": record.get("candidate_id"),
        "candidate_definition_sha256": record.get("candidate_definition_sha256"),
        "status": TaskState.UNKNOWN.value,
        "reason_code": reason,
        "reason_details": list(details),
        "mapped_branch_evidence_id": None,
        "mapping_kind": "BRANCH_APPROACH",
        "mapping_mode": MAPPING_MODE,
        "raw_route_frame": record.get("raw_route_frame"),
        "raw_route_unit": record.get("raw_route_unit"),
        "raw_values_interpreted_as_metres": False,
        "raw_l2_used": False,
        "candidate_name_used_for_mapping": False,
        "expected_pair_class_read": False,
        "authorization_eligible": False,
        "safety_critical_eligible": False,
    }


def map_candidate_plan(
    package: Mapping[str, Any],
    record: Mapping[str, Any],
    *,
    maximum_branch_angle_deg: float = 35.0,
    minimum_separation_margin_deg: float = 10.0,
) -> dict[str, Any]:
    """Map one raw route to one frozen branch approach without using its A/B name."""

    package_errors = validate_frozen_package(package)
    if package_errors:
        return _unknown_mapping(record, "FROZEN_EVIDENCE_INVALID", package_errors)
    if record.get("schema_version") != CANDIDATE_SCHEMA:
        return _unknown_mapping(record, "CANDIDATE_SCHEMA_MISMATCH")
    if record.get("completion_status") != "COMPLETE":
        return _unknown_mapping(record, "CANDIDATE_OUTPUT_INCOMPLETE")
    observation = package["observation"]
    if record.get("source_observation_id") != observation.get("observation_id"):
        return _unknown_mapping(record, "CANDIDATE_OBSERVATION_MISMATCH")
    if record.get("input_identity_sha256") != observation.get("input_identity_sha256"):
        return _unknown_mapping(record, "CANDIDATE_INPUT_IDENTITY_MISMATCH")
    model_binding = package.get("model_binding", {})
    if record.get("model_binding_sha256") != model_binding.get("binding_sha256"):
        return _unknown_mapping(record, "CANDIDATE_MODEL_BINDING_MISMATCH")
    definitions = {
        item.get("definition_sha256"): item
        for item in package["candidate_definitions"]
        if isinstance(item, Mapping)
    }
    if record.get("candidate_definition_sha256") not in definitions:
        return _unknown_mapping(record, "CANDIDATE_DEFINITION_NOT_FROZEN")
    if record.get("raw_route_frame") != RAW_ROUTE_FRAME:
        return _unknown_mapping(record, "RAW_ROUTE_FRAME_UNVALIDATED")
    if record.get("raw_route_unit") != RAW_ROUTE_UNIT:
        return _unknown_mapping(record, "RAW_ROUTE_UNIT_MUST_REMAIN_MODEL_NATIVE")
    route = record.get("raw_route")
    if record.get("raw_route_sha256") != deterministic_hash(route):
        return _unknown_mapping(record, "RAW_ROUTE_HASH_MISMATCH")
    points = _route_points(route)
    if points is None:
        return _unknown_mapping(record, "RAW_ROUTE_INVALID")
    endpoint = points[-1]
    raw_direction = _unit(endpoint)
    if raw_direction is None or math.hypot(endpoint[0], endpoint[1]) < 0.25:
        return _unknown_mapping(record, "RAW_ROUTE_APPROACH_DIRECTION_UNAVAILABLE")

    matrix = package["coordinate_contract"]["ego_to_world"]
    try:
        world_x = float(matrix[0][0]) * raw_direction[0] + float(matrix[0][1]) * raw_direction[1]
        world_y = float(matrix[1][0]) * raw_direction[0] + float(matrix[1][1]) * raw_direction[1]
    except (TypeError, ValueError, IndexError):
        return _unknown_mapping(record, "TRANSFORM_MISSING_OR_INVALID")
    world_direction = _unit((world_x, world_y))
    if world_direction is None:
        return _unknown_mapping(record, "TRANSFORMED_DIRECTION_INVALID")

    scores = []
    for branch in package["branch_evidence"]["branches"]:
        direction = _unit(branch["approach_direction_world_unit"])
        if direction is None:
            return _unknown_mapping(record, "BRANCH_APPROACH_DIRECTION_INVALID")
        scores.append(
            {
                "branch_evidence_id": branch["branch_evidence_id"],
                "angle_deg": _angle_degrees(world_direction, direction),
            }
        )
    scores.sort(key=lambda item: (item["angle_deg"], item["branch_evidence_id"]))
    best, second = scores
    margin = second["angle_deg"] - best["angle_deg"]
    if best["angle_deg"] > maximum_branch_angle_deg:
        return dict(
            _unknown_mapping(record, "NO_BRANCH_APPROACH_WITHIN_ANGULAR_GATE"),
            raw_approach_direction_ego_unit=list(raw_direction),
            transformed_approach_direction_world_unit=list(world_direction),
            branch_angle_scores=scores,
            best_to_second_margin_deg=margin,
        )
    if margin < minimum_separation_margin_deg:
        return dict(
            _unknown_mapping(record, "BRANCH_APPROACH_AMBIGUOUS"),
            raw_approach_direction_ego_unit=list(raw_direction),
            transformed_approach_direction_world_unit=list(world_direction),
            branch_angle_scores=scores,
            best_to_second_margin_deg=margin,
        )
    return {
        "candidate_id": record.get("candidate_id"),
        "candidate_definition_sha256": record.get("candidate_definition_sha256"),
        "status": "AVAILABLE",
        "reason_code": "UNIQUE_FROZEN_BRANCH_APPROACH",
        "reason_details": [],
        "mapped_branch_evidence_id": best["branch_evidence_id"],
        "mapping_kind": "BRANCH_APPROACH",
        "mapping_mode": MAPPING_MODE,
        "raw_route_frame": RAW_ROUTE_FRAME,
        "raw_route_unit": RAW_ROUTE_UNIT,
        "raw_values_interpreted_as_metres": False,
        "raw_approach_direction_ego_unit": list(raw_direction),
        "transformed_approach_direction_world_unit": list(world_direction),
        "branch_angle_scores": scores,
        "best_to_second_margin_deg": margin,
        "maximum_branch_angle_deg": maximum_branch_angle_deg,
        "minimum_separation_margin_deg": minimum_separation_margin_deg,
        "raw_l2_used": False,
        "candidate_name_used_for_mapping": False,
        "expected_pair_class_read": False,
        "authorization_eligible": False,
        "safety_critical_eligible": False,
    }


def _ground_truth(package: Mapping[str, Any]) -> dict[str, Any]:
    errors = validate_frozen_package(package)
    if errors:
        pair_class = PairClass.UNKNOWN.value
        reason = "FROZEN_GROUND_TRUTH_INVALID"
        targets = []
    else:
        targets = [
            {
                "candidate_definition_sha256": item["definition_sha256"],
                "target_branch_evidence_id": item["intended_target_branch_evidence_id"],
                "semantic_definition": item["semantic_definition"],
            }
            for item in package["candidate_definitions"]
        ]
        unique = {item["target_branch_evidence_id"] for item in targets}
        pair_class = (
            PairClass.TASK_EQUIVALENT.value
            if len(unique) == 1
            else PairClass.TASK_CRITICAL.value
        )
        reason = "FROZEN_TARGET_BRANCHES_EQUAL" if len(unique) == 1 else "FROZEN_TARGET_BRANCHES_DISTINCT"
    return with_hash(
        {
            "schema_version": GROUND_TRUTH_SCHEMA,
            "status": "AVAILABLE" if not errors else "UNKNOWN",
            "reason_code": reason,
            "reason_details": list(errors),
            "targets": targets,
            "pair_class": pair_class,
            "source": "PRE_OUTPUT_FROZEN_CARLA_MAP_BRANCH_DEFINITION",
            "derived_from_model_outputs": False,
            "runtime_predictor_p_can_read_pair_class": False,
            "authorization_eligible": False,
            "safety_critical_eligible": False,
        }
    )


def evaluate_capture(package: Mapping[str, Any], records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Evaluate six records and keep predictor P separate from ground truth H."""

    mappings = [map_candidate_plan(package, record) for record in records]
    definitions = {
        item.get("definition_sha256"): item
        for item in package.get("candidate_definitions", [])
        if isinstance(item, Mapping)
    }
    enriched = []
    for record, mapping in zip(records, mappings):
        definition = definitions.get(record.get("candidate_definition_sha256"))
        target = definition.get("intended_target_branch_evidence_id") if definition else None
        if mapping["status"] != "AVAILABLE" or target is None:
            task_status = TaskState.UNKNOWN.value
            task_reason = mapping["reason_code"] if target is not None else "CANDIDATE_DEFINITION_NOT_FROZEN"
        elif mapping["mapped_branch_evidence_id"] == target:
            task_status = TaskState.PASS.value
            task_reason = "PREDICTED_BRANCH_MATCHES_FROZEN_TARGET"
        else:
            task_status = TaskState.FAIL.value
            task_reason = "PREDICTED_BRANCH_DIFFERS_FROM_FROZEN_TARGET"
        enriched.append(
            dict(
                mapping,
                target_branch_evidence_id=target,
                task_status=task_status,
                task_reason_code=task_reason,
                raw_route_sha256=record.get("raw_route_sha256"),
                raw_speed_sha256=record.get("raw_speed_sha256"),
                sequence_position=record.get("sequence_position"),
            )
        )

    groups: dict[str, list[dict[str, Any]]] = {}
    record_by_id = {str(record.get("candidate_id")): record for record in records}
    for item in enriched:
        groups.setdefault(str(item.get("candidate_definition_sha256")), []).append(item)
    group_results = []
    aggregate_mapped: dict[str, str] = {}
    schedule = package.get("schedule_binding", {}).get("candidate_order", [])
    schedule_valid = [record.get("candidate_id") for record in records] == list(schedule)
    for definition_hash in sorted(groups):
        items = sorted(groups[definition_hash], key=lambda item: int(item.get("sequence_position") or 0))
        mapped = {item.get("mapped_branch_evidence_id") for item in items if item.get("mapped_branch_evidence_id")}
        statuses = {item["task_status"] for item in items}
        route_hashes = [item.get("raw_route_sha256") for item in items]
        speed_hashes = [item.get("raw_speed_sha256") for item in items]
        if len(items) != 3:
            aggregate_status, reason = TaskState.UNKNOWN.value, "REPEAT_COUNT_NOT_THREE"
        elif TaskState.UNKNOWN.value in statuses:
            aggregate_status, reason = TaskState.UNKNOWN.value, "REPEAT_MAPPING_UNKNOWN"
        elif len(mapped) != 1:
            aggregate_status, reason = TaskState.UNKNOWN.value, "REPEAT_BRANCH_VARIATION"
        elif statuses == {TaskState.PASS.value}:
            aggregate_status, reason = TaskState.PASS.value, "ALL_REPEATS_MATCH_FROZEN_TARGET"
        elif statuses == {TaskState.FAIL.value}:
            aggregate_status, reason = TaskState.FAIL.value, "ALL_REPEATS_DIFFER_FROM_FROZEN_TARGET"
        else:
            aggregate_status, reason = TaskState.UNKNOWN.value, "REPEAT_TASK_STATUS_VARIATION"
        mapped_one = next(iter(mapped)) if len(mapped) == 1 else None
        if mapped_one is not None and aggregate_status != TaskState.UNKNOWN.value:
            aggregate_mapped[definition_hash] = mapped_one
        group_results.append(
            {
                "candidate_definition_sha256": definition_hash,
                "candidate_ids": [item.get("candidate_id") for item in items],
                "repeat_count": len(items),
                "raw_route_all_equal": len(set(route_hashes)) == 1,
                "raw_speed_all_equal": len(set(speed_hashes)) == 1,
                "mapped_branch_all_equal": len(mapped) == 1 and len(items) == 3,
                "mapped_branch_evidence_id": mapped_one,
                "status": aggregate_status,
                "reason_code": reason,
            }
        )

    if not schedule_valid:
        predicted_pair, predicted_reason = PairClass.UNKNOWN.value, "CANDIDATE_SCHEDULE_MISMATCH"
    elif len(groups) != 2 or any(len(items) != 3 for items in groups.values()):
        predicted_pair, predicted_reason = PairClass.UNKNOWN.value, "A3_B3_RECORD_SET_INCOMPLETE"
    elif len(aggregate_mapped) != 2:
        predicted_pair, predicted_reason = PairClass.UNKNOWN.value, "PAIR_BRANCH_MAPPING_UNKNOWN"
    elif len(set(aggregate_mapped.values())) == 1:
        predicted_pair, predicted_reason = PairClass.TASK_EQUIVALENT.value, "PREDICTED_BRANCH_APPROACHES_EQUAL"
    else:
        predicted_pair, predicted_reason = PairClass.TASK_CRITICAL.value, "PREDICTED_BRANCH_APPROACHES_DISTINCT"

    predictor = with_hash(
        {
            "schema_version": PREDICTOR_SCHEMA,
            "mapping_mode": MAPPING_MODE,
            "records": enriched,
            "candidate_aggregates": group_results,
            "pair_class": predicted_pair,
            "reason_code": predicted_reason,
            "candidate_schedule_valid": schedule_valid,
            "expected_pair_class_read": False,
            "raw_l2_used": False,
            "candidate_name_used_for_mapping": False,
            "raw_values_interpreted_as_metres": False,
            "authorization_eligible": False,
            "safety_critical_eligible": False,
        }
    )
    ground_truth = _ground_truth(package)
    final_pair = predicted_pair if ground_truth["status"] == "AVAILABLE" else PairClass.UNKNOWN.value
    pair = with_hash(
        {
            "schema_version": PAIR_SCHEMA,
            "pair_class": final_pair,
            "predictor_p_pair_class": predicted_pair,
            "ground_truth_h_pair_class": ground_truth["pair_class"],
            "predictor_ground_truth_pair_class_agree": (
                predicted_pair == ground_truth["pair_class"] and predicted_pair != PairClass.UNKNOWN.value
            ),
            "candidate_task_results": [
                {
                    "candidate_definition_sha256": item["candidate_definition_sha256"],
                    "status": item["status"],
                    "reason_code": item["reason_code"],
                    "mapped_branch_evidence_id": item["mapped_branch_evidence_id"],
                }
                for item in group_results
            ],
            "reason_code": predicted_reason if final_pair != PairClass.UNKNOWN.value else "PAIR_EVIDENCE_UNKNOWN",
            "recommendation": "FALLBACK_RECOMMENDED",
            "recommendation_reason": "QUERY_DEADLINE_HOLDING_AND_RECOVERABILITY_NOT_VALIDATED",
            "ask_recommended": False,
            "wait_recommended": False,
            "act_ask_wait_validated": False,
            "authorization_eligible": False,
            "safety_critical_eligible": False,
            "control_authorized": False,
        }
    )
    repeatability = with_hash(
        {
            "schema_version": REPEATABILITY_SCHEMA,
            "candidate_schedule_valid": schedule_valid,
            "candidate_order": [record.get("candidate_id") for record in records],
            "groups": group_results,
            "same_condition_equality_required_for_branch_mapping": False,
            "same_condition_variation_recorded": True,
            "branch_consistency_required": True,
        }
    )
    return {
        "repeatability_results": repeatability,
        "task_evaluation_p": predictor,
        "task_ground_truth_h": ground_truth,
        "pair_equivalence": pair,
    }


def write_evaluation_outputs(
    frozen_package_path: str | Path,
    candidate_paths: Sequence[str | Path],
    output_dir: str | Path,
) -> dict[str, Any]:
    package = json.loads(Path(frozen_package_path).read_text(encoding="utf-8"))
    records = [json.loads(Path(path).read_text(encoding="utf-8")) for path in candidate_paths]
    outputs = evaluate_capture(package, records)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    names = {
        "repeatability_results": "REPEATABILITY_RESULTS.json",
        "task_evaluation_p": "TASK_EVALUATION_P.json",
        "task_ground_truth_h": "TASK_GROUND_TRUTH_H.json",
        "pair_equivalence": "PAIR_EQUIVALENCE.json",
    }
    for key, filename in names.items():
        write_json(destination / filename, outputs[key])
    return outputs
