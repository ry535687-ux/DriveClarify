"""R4.1 pure-CPU, evidence-derived candidate validity certification.

Fixture identifiers are report metadata only. Every verdict is derived from
immutable parent records plus replayable semantic/topology/kinematic evidence.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Mapping, Sequence

from .contracts import canonical_sha256


UNCERTAINTY_UPPER_M = 0.3969352485356619
EXPECTED_POINT_COUNT = 20
NONTERMINAL_SEGMENT_MIN_M = 0.8
SEGMENT_MAX_M = 1.2
BACKWARD_PROGRESS_TOLERANCE_M = 0.05
CURVATURE_MAX_INVERSE_M = 0.5
SPEED_MAX_MPS = 15.0
ACCELERATION_MAX_MPS2 = 8.0
PASS, FAIL, UNKNOWN = "PASS", "FAIL", "UNKNOWN"

CLASSIFICATIONS = {
    "CERTIFIED_DISTINCT_CURRENT_PLANS",
    "CERTIFIED_CURRENT_SHARED_FUTURE_DIVERGENT",
    "COLLAPSED_DUPLICATE",
    "INVALID_CANDIDATE",
    "UNKNOWN_INSUFFICIENT_EVIDENCE",
}


def _canonical_sha_or_none(value: Any) -> str | None:
    """Hash external nested evidence, returning None only for data errors."""
    try:
        return canonical_sha256(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _finite_points(value: Any) -> list[tuple[float, float]] | None:
    if not isinstance(value, (list, tuple)):
        return None
    rows: list[tuple[float, float]] = []
    try:
        for row in value:
            if not isinstance(row, (list, tuple)) or len(row) < 2:
                return None
            point = (float(row[0]), float(row[1]))
            if not all(math.isfinite(item) for item in point):
                return None
            rows.append(point)
    except (TypeError, ValueError, OverflowError):
        return None
    return rows


def _rmse(left: Sequence[Sequence[float]], right: Sequence[Sequence[float]]) -> float:
    if len(left) != len(right) or not left:
        return math.inf
    return math.sqrt(sum(
        (float(a[0]) - float(b[0])) ** 2 + (float(a[1]) - float(b[1])) ** 2
        for a, b in zip(left, right)
    ) / (2.0 * len(left)))


def _max_separation(left: Sequence[Sequence[float]], right: Sequence[Sequence[float]], indices: range) -> float:
    values = [
        math.hypot(float(left[i][0]) - float(right[i][0]), float(left[i][1]) - float(right[i][1]))
        for i in indices if i < len(left) and i < len(right)
    ]
    return max(values) if values else math.inf


def _curvatures(points: Sequence[tuple[float, float]]) -> list[float]:
    values: list[float] = []
    for first, middle, last in zip(points, points[1:], points[2:]):
        a = math.hypot(middle[0] - first[0], middle[1] - first[1])
        b = math.hypot(last[0] - middle[0], last[1] - middle[1])
        c = math.hypot(last[0] - first[0], last[1] - first[1])
        denominator = a * b * c
        if denominator <= 1e-12:
            values.append(0.0)
            continue
        twice_area = abs(
            (middle[0] - first[0]) * (last[1] - first[1])
            - (middle[1] - first[1]) * (last[0] - first[0])
        )
        values.append(2.0 * twice_area / denominator)
    return values


def _effective_value(record: Mapping[str, Any], evidence: Mapping[str, Any], field: str) -> Any:
    overrides = evidence.get("derived_overrides", {})
    row = overrides.get(str(record.get("ordinal")), {}) if isinstance(overrides, Mapping) else {}
    return row.get(field, record.get(field)) if isinstance(row, Mapping) else record.get(field)


def _kinematic_validity(route: Any, speed: Any, *, sample_interval_s: Any = None) -> dict[str, Any]:
    if not isinstance(route, (list, tuple)) or not isinstance(speed, (list, tuple)):
        return {"status": UNKNOWN, "reason_codes": ["ROUTE_OR_SPEED_EVIDENCE_MISSING_OR_WRONG_TYPE"], "acceleration_status": UNKNOWN}
    if any(not isinstance(row, (list, tuple)) or len(row) < 2 for row in (*route, *speed)):
        return {"status": UNKNOWN, "reason_codes": ["ROUTE_OR_SPEED_NESTED_SHAPE_MALFORMED"], "acceleration_status": UNKNOWN}
    points, speeds = _finite_points(route), _finite_points(speed)
    if points is None or speeds is None:
        numeric_values = [item for row in (*route, *speed) for item in row[:2]]
        try:
            explicitly_nonfinite = any(not math.isfinite(float(item)) for item in numeric_values)
        except (TypeError, ValueError, OverflowError):
            explicitly_nonfinite = False
        return {
            "status": FAIL if explicitly_nonfinite else UNKNOWN,
            "reason_codes": [
                "NON_FINITE_ROUTE_OR_SPEED_VALUE"
                if explicitly_nonfinite else "ROUTE_OR_SPEED_NUMERIC_EVIDENCE_MALFORMED"
            ],
            "acceleration_status": UNKNOWN,
        }
    lengths = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(points, points[1:])]
    spacing_valid = bool(
        len(points) == EXPECTED_POINT_COUNT
        and all(NONTERMINAL_SEGMENT_MIN_M <= value <= SEGMENT_MAX_M for value in lengths[:-1])
        and lengths and 0.0 <= lengths[-1] <= SEGMENT_MAX_M
    )
    progress_valid = all(b[0] + BACKWARD_PROGRESS_TOLERANCE_M >= a[0] for a, b in zip(points, points[1:]))
    curvature = _curvatures(points)
    curvature_valid = bool(curvature and max(curvature) <= CURVATURE_MAX_INVERSE_M)
    magnitudes = [math.hypot(*row) for row in speeds]
    speed_valid = bool(magnitudes and max(magnitudes) <= SPEED_MAX_MPS)
    acceleration_status, acceleration_max = UNKNOWN, None
    if sample_interval_s is not None:
        try:
            interval = float(sample_interval_s)
            if not math.isfinite(interval) or interval <= 0.0:
                raise ValueError
            acceleration_max = max((abs(b - a) / interval for a, b in zip(magnitudes, magnitudes[1:])), default=0.0)
            acceleration_status = PASS if acceleration_max <= ACCELERATION_MAX_MPS2 else FAIL
        except (TypeError, ValueError, OverflowError):
            acceleration_status = UNKNOWN
    checks = {
        "route_point_count": len(points) == EXPECTED_POINT_COUNT,
        "full_horizon_segment_spacing": spacing_valid,
        "full_horizon_curvature": curvature_valid,
        "full_speed_plan_bound": speed_valid,
        "no_reverse_or_jump": progress_valid and bool(lengths) and max(lengths) <= SEGMENT_MAX_M,
    }
    return {
        "status": PASS if all(checks.values()) and acceleration_status != FAIL else FAIL,
        "checks": checks,
        "segment_count_checked": len(lengths),
        "segment_length_min_m": min(lengths) if lengths else None,
        "segment_length_max_m": max(lengths) if lengths else None,
        "curvature_max_inverse_m": max(curvature) if curvature else None,
        "speed_max_mps": max(magnitudes) if magnitudes else None,
        "acceleration_status": acceleration_status,
        "acceleration_max_mps2": acceleration_max,
    }


def _topology_validity(record: Mapping[str, Any], route: Any, evidence: Mapping[str, Any]) -> dict[str, Any]:
    topology_all = evidence.get("topology")
    if not isinstance(topology_all, Mapping):
        return {"status": UNKNOWN, "reason_codes": ["TOPOLOGY_EVIDENCE_MISSING"]}
    topology_overrides = evidence.get("topology_overrides", {})
    topology = (
        topology_overrides.get(str(record.get("ordinal")))
        if isinstance(topology_overrides, Mapping)
        else None
    ) or topology_all.get(str(record.get("candidate_order_index")))
    if not isinstance(topology, Mapping):
        return {"status": UNKNOWN, "reason_codes": ["CANDIDATE_TOPOLOGY_EVIDENCE_MISSING"]}
    points = _finite_points(route)
    projections, path = topology.get("point_projections"), topology.get("lane_path")
    successors, legal_lanes = topology.get("lane_successor_edges"), topology.get("legal_lane_ids")
    required = {
        "map_evidence_id", "map_evidence_sha256", "map_evidence_class",
        "map_evidence_provider", "source_observation_id", "road_id",
        "corridor_half_width_m", "obligation_branch",
        "obligation_target_lane_id", "observed_branch",
    }
    if (
        points is None
        or not required.issubset(topology)
        or not isinstance(projections, list)
        or not isinstance(path, list)
        or not isinstance(successors, list)
        or not isinstance(legal_lanes, list)
        or not isinstance(topology.get("map_evidence_id"), str)
        or not isinstance(topology.get("map_evidence_sha256"), str)
        or len(topology.get("map_evidence_sha256", "")) != 64
        or topology.get("map_evidence_class") not in {
            "PROVIDED_MAP_LANE_CORRIDOR",
            "SYNTHETIC_CERTIFIER_CAPABILITY_FIXTURE",
        }
        or not isinstance(topology.get("map_evidence_provider"), str)
        or not topology.get("map_evidence_provider")
        or not isinstance(topology.get("source_observation_id"), str)
        or not isinstance(topology.get("road_id"), int)
        or isinstance(topology.get("road_id"), bool)
        or not isinstance(topology.get("corridor_half_width_m"), (int, float))
        or isinstance(topology.get("corridor_half_width_m"), bool)
    ):
        return {"status": UNKNOWN, "reason_codes": ["TOPOLOGY_EVIDENCE_MALFORMED"]}
    corridor_half_width = float(topology["corridor_half_width_m"])
    if not math.isfinite(corridor_half_width) or corridor_half_width < 0.0:
        return {"status": UNKNOWN, "reason_codes": ["TOPOLOGY_EVIDENCE_MALFORMED"]}
    map_payload = topology.get("map_payload")
    if (
        not isinstance(map_payload, Mapping)
        or _canonical_sha_or_none(map_payload) != topology["map_evidence_sha256"]
        or map_payload.get("road_id") != topology.get("road_id")
        or map_payload.get("legal_lane_ids") != legal_lanes
        or map_payload.get("corridor_half_width_m") != topology.get("corridor_half_width_m")
        or map_payload.get("lane_successor_edges") != successors
    ):
        return {"status": UNKNOWN, "reason_codes": ["TOPOLOGY_MAP_PAYLOAD_OR_HASH_UNVERIFIABLE"]}
    if topology["map_evidence_class"] == "SYNTHETIC_CERTIFIER_CAPABILITY_FIXTURE":
        if (
            topology.get("synthetic_projection_derived_from_candidate_output") is not True
            or topology.get("official_model_performance_result") is not False
        ):
            return {"status": UNKNOWN, "reason_codes": ["SYNTHETIC_TOPOLOGY_PROVENANCE_MALFORMED"]}
    else:
        receipt = topology.get("provided_evidence_receipt")
        if not isinstance(receipt, Mapping):
            return {"status": UNKNOWN, "reason_codes": ["PROVIDED_TOPOLOGY_RECEIPT_MISSING"]}
        receipt_payload = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
        if (
            receipt.get("receipt_sha256") != _canonical_sha_or_none(receipt_payload)
            or receipt.get("map_evidence_sha256") != topology["map_evidence_sha256"]
            or receipt.get("map_evidence_provider") != topology["map_evidence_provider"]
            or receipt.get("source_observation_id") != topology["source_observation_id"]
            or receipt.get("route_derived") is not False
            or receipt.get("evidence_class") != "PROVIDED_MAP_LANE_CORRIDOR"
        ):
            return {"status": UNKNOWN, "reason_codes": ["PROVIDED_TOPOLOGY_RECEIPT_UNVERIFIABLE"]}
    source = record.get("source_observation")
    if not isinstance(source, Mapping) or topology["source_observation_id"] != source.get("observation_id"):
        return {"status": FAIL, "reason_codes": ["TOPOLOGY_SOURCE_OBSERVATION_MISMATCH"]}
    projection_shape_valid = all(
        isinstance(row, Mapping)
        and isinstance(row.get("route_index"), int)
        and not isinstance(row.get("route_index"), bool)
        and isinstance(row.get("projection_distance_m"), (int, float))
        and not isinstance(row.get("projection_distance_m"), bool)
        and isinstance(row.get("lane_center_y_m"), (int, float))
        and not isinstance(row.get("lane_center_y_m"), bool)
        and isinstance(row.get("travel_direction_dot"), (int, float))
        and not isinstance(row.get("travel_direction_dot"), bool)
        and "road_id" in row and "lane_id" in row
        and isinstance(row.get("finite_projection"), bool)
        and isinstance(row.get("opposing_or_illegal"), bool)
        for row in projections
    )
    successor_shape_valid = all(
        isinstance(row, (list, tuple)) and len(row) == 2 for row in successors
    )
    if not projection_shape_valid or not successor_shape_valid:
        return {"status": UNKNOWN, "reason_codes": ["TOPOLOGY_EVIDENCE_MALFORMED"]}
    if len(projections) != len(points) or len(points) != EXPECTED_POINT_COUNT:
        return {"status": UNKNOWN, "reason_codes": ["TOPOLOGY_HORIZON_INCOMPLETE"]}
    all_points_checked = len(projections) == len(points) == EXPECTED_POINT_COUNT
    projection_valid = all(
        row.get("route_index") == index
        and row.get("finite_projection") is True
        and math.isfinite(float(row["projection_distance_m"]))
        and float(row["projection_distance_m"]) <= corridor_half_width
        and math.isfinite(float(row["lane_center_y_m"]))
        and math.isclose(
            float(row["projection_distance_m"]),
            abs(points[index][1] - float(row["lane_center_y_m"])),
            rel_tol=0.0,
            abs_tol=1e-9,
        )
        and row.get("road_id") == topology.get("road_id")
        and row.get("lane_id") in legal_lanes
        and math.isfinite(float(row["travel_direction_dot"]))
        and float(row["travel_direction_dot"]) > 0.0
        and row.get("opposing_or_illegal") is False
        for index, row in enumerate(projections)
    )
    successor_set = {(row[0], row[1]) for row in successors}
    connectivity = all(left == right or (left, right) in successor_set for left, right in zip(path, path[1:]))
    no_jump = all(math.hypot(b[0] - a[0], b[1] - a[1]) <= SEGMENT_MAX_M for a, b in zip(points, points[1:]))
    terminal_matches = bool(path and path[-1] == topology.get("obligation_target_lane_id"))
    branch_matches = topology.get("observed_branch") == topology.get("obligation_branch")
    checks = {
        "all_route_points_projected": all_points_checked and projection_valid,
        "road_lane_identity_legal": projection_valid,
        "lane_successor_connected": connectivity,
        "junction_branch_matches_obligation": branch_matches,
        "travel_direction_consistent": projection_valid,
        "no_disconnected_jump": no_jump,
        "terminal_lane_matches_obligation": terminal_matches,
        "no_opposing_or_illegal_lane": projection_valid,
    }
    return {"status": PASS if all(checks.values()) else FAIL, "checks": checks, "route_point_count_checked": len(projections), "lane_path": path}


def _source_status(record: Mapping[str, Any]) -> str:
    try:
        canonical_input, source, provider = record["canonical_input"], record["source_observation"], record["provider_forward_evidence"]
        exact = (
            isinstance(canonical_input, Mapping) and isinstance(source, Mapping) and isinstance(provider, Mapping)
            and source["observation_id"] == canonical_input["observation_id"]
            and source["frame_id"] == canonical_input["frame_id"]
            and provider["source_observation_id"] == source["observation_id"]
            and provider["source_frame_id"] == source["frame_id"]
            and provider["candidate_id"] == record["candidate_id"]
            and record["combined_output_sha256"] == canonical_sha256({"route": record["route"], "speed": record["speed"]})
        )
    except (KeyError, TypeError):
        return UNKNOWN
    return PASS if exact else FAIL


def _semantic_status(record: Mapping[str, Any], evidence: Mapping[str, Any]) -> str:
    bindings, topology_all = evidence.get("semantic_bindings"), evidence.get("topology")
    if not isinstance(bindings, Mapping) or not isinstance(topology_all, Mapping):
        return UNKNOWN
    binding = bindings.get(str(record.get("candidate_order_index")))
    topology_overrides = evidence.get("topology_overrides", {})
    topology = (
        topology_overrides.get(str(record.get("ordinal")))
        if isinstance(topology_overrides, Mapping)
        else None
    ) or topology_all.get(str(record.get("candidate_order_index")))
    if not isinstance(binding, Mapping) or not isinstance(topology, Mapping):
        return UNKNOWN
    required = ("interpretation_id", "semantic_digest", "obligation", "target_lane_id")
    if not all(key in binding for key in required):
        return UNKNOWN
    return PASS if (
        binding["interpretation_id"] == record.get("interpretation_id")
        and binding["semantic_digest"] == record.get("semantic_digest")
        and binding["target_lane_id"] == topology.get("obligation_target_lane_id")
        and binding["obligation"] == topology.get("obligation_branch")
    ) else FAIL


def certify_candidate_pair(fixture_id: str, records: Sequence[Mapping[str, Any]], *, evidence: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return a tri-state R4.1 certificate; ``fixture_id`` never affects it."""
    evidence = evidence or {}
    by_repeat: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    malformed = False
    for record in records:
        try:
            by_repeat[int(record["repeat_index"])].append(record)
        except (KeyError, TypeError, ValueError):
            malformed = True
    repeats = sorted(by_repeat)
    complete_shape = not malformed and repeats == [1, 2, 3, 4] and all(len(by_repeat[i]) == 2 for i in repeats)
    ordered = {i: sorted(by_repeat[i], key=lambda row: int(row.get("candidate_order_index", -1))) for i in repeats}
    rows = [row for i in repeats for row in ordered[i]]
    input_hashes = {str(row.get("input_bundle_sha256")) for row in rows}
    orders = [tuple(str(row.get("candidate_id")) for row in ordered[i]) for i in repeats]
    semantic_orders = [tuple(str(row.get("semantic_digest")) for row in ordered[i]) for i in repeats]
    interpretation_orders = [tuple(str(row.get("interpretation_id")) for row in ordered[i]) for i in repeats]
    validity: list[dict[str, Any]] = []
    metrics: list[dict[str, Any]] = []
    effective_hash_pairs: list[tuple[str | None, str | None]] = []
    source_states: list[str] = []
    semantic_states: list[str] = []
    topology_states: list[str] = []
    kinematic_states: list[str] = []
    for index in repeats:
        if len(ordered[index]) != 2:
            continue
        effective_routes, effective_speeds = [], []
        for record in ordered[index]:
            route = _effective_value(record, evidence, "route")
            speed = _effective_value(record, evidence, "speed")
            effective_routes.append(route)
            effective_speeds.append(speed)
            topology = _topology_validity(record, route, evidence)
            kinematics = _kinematic_validity(route, speed, sample_interval_s=evidence.get("sample_interval_s"))
            source_status, semantic_status = _source_status(record), _semantic_status(record, evidence)
            source_states.append(source_status); semantic_states.append(semantic_status)
            topology_states.append(topology["status"]); kinematic_states.append(kinematics["status"])
            validity.append({
                "repeat_index": index, "candidate_order_index": record.get("candidate_order_index"),
                "parent_record_id": record.get("ordinal"), "parent_output_hash": record.get("combined_output_sha256"),
                "source_identity": source_status, "semantic_binding": semantic_status,
                "topology": topology, "kinematics": kinematics,
            })
        left = _finite_points(effective_routes[0]) or []
        right = _finite_points(effective_routes[1]) or []
        metrics.append({
            "repeat_index": index, "full_route_rmse_m": _rmse(left, right),
            "current_prefix_max_separation_m": _max_separation(left, right, range(0, 3)),
            "future_max_separation_m": _max_separation(left, right, range(3, EXPECTED_POINT_COUNT)),
        })
        effective_hash_pairs.append(tuple(
            _canonical_sha_or_none({"route": route, "speed": speed})
            for route, speed in zip(effective_routes, effective_speeds)
        ))
    identity_stable = bool(
        complete_shape and len(input_hashes) == 1 and orders and all(row == orders[0] for row in orders)
        and semantic_orders and all(row == semantic_orders[0] for row in semantic_orders)
        and interpretation_orders and all(row == interpretation_orders[0] for row in interpretation_orders)
    )
    exact_output_stable = bool(
        effective_hash_pairs
        and all(all(item is not None for item in row) for row in effective_hash_pairs)
        and all(row == effective_hash_pairs[0] for row in effective_hash_pairs)
    )
    repeat_deviation = 0.0
    repeat_evidence_comparable = complete_shape
    if complete_shape:
        for candidate_index in (0, 1):
            reference = _finite_points(_effective_value(ordered[1][candidate_index], evidence, "route"))
            if reference is None:
                repeat_evidence_comparable = False
                continue
            for repeat_index in repeats[1:]:
                current = _finite_points(_effective_value(ordered[repeat_index][candidate_index], evidence, "route"))
                if current is None or len(current) != len(reference):
                    repeat_evidence_comparable = False
                    continue
                repeat_deviation = max(repeat_deviation, _max_separation(reference, current, range(0, EXPECTED_POINT_COUNT)))
    repeat_status = (
        UNKNOWN
        if not repeat_evidence_comparable
        else PASS
        if identity_stable and repeat_deviation <= UNCERTAINTY_UPPER_M
        else FAIL
    )
    bindings = evidence.get("semantic_bindings")
    obligation_values = (
        {str(row.get("obligation")) for row in bindings.values() if isinstance(row, Mapping)}
        if isinstance(bindings, Mapping)
        else set()
    )
    semantic_distinct = len(obligation_values) == 2 and bool(semantic_orders and len(set(semantic_orders[0])) == 2)
    semantic_equivalent = len(obligation_values) == 1 or bool(semantic_orders and len(set(semantic_orders[0])) == 1)
    dimensions = {
        "source_identity": FAIL if FAIL in source_states else UNKNOWN if UNKNOWN in source_states or not source_states else PASS,
        "semantic_binding": FAIL if FAIL in semantic_states else UNKNOWN if UNKNOWN in semantic_states or not semantic_states else PASS,
        "topology": FAIL if FAIL in topology_states else UNKNOWN if UNKNOWN in topology_states or not topology_states else PASS,
        "kinematics": FAIL if FAIL in kinematic_states else UNKNOWN if UNKNOWN in kinematic_states or not kinematic_states else PASS,
        "repeat_stability": repeat_status,
    }
    if FAIL in dimensions.values():
        classification = "INVALID_CANDIDATE"
    elif UNKNOWN in dimensions.values():
        classification = "UNKNOWN_INSUFFICIENT_EVIDENCE"
    elif semantic_equivalent:
        classification = "COLLAPSED_DUPLICATE"
    else:
        current = max((row["current_prefix_max_separation_m"] for row in metrics), default=math.inf)
        future = max((row["future_max_separation_m"] for row in metrics), default=math.inf)
        if not semantic_distinct or max(current, future) <= max(UNCERTAINTY_UPPER_M, repeat_deviation):
            classification = "COLLAPSED_DUPLICATE"
        elif current > UNCERTAINTY_UPPER_M:
            classification = "CERTIFIED_DISTINCT_CURRENT_PLANS"
        elif future > UNCERTAINTY_UPPER_M:
            classification = "CERTIFIED_CURRENT_SHARED_FUTURE_DIVERGENT"
        else:
            classification = "COLLAPSED_DUPLICATE"
    result = {
        "schema_version": "driveclarify.candidate_certification.r4_1.v1",
        "fixture_id_metadata_only": fixture_id, "classification": classification,
        "classification_allowed": classification in CLASSIFICATIONS, "dimension_states": dimensions,
        "checks": {
            "fixture_id_used_for_verdict": False, "complete_four_by_two_record_shape": complete_shape,
            "single_canonical_input_bundle": len(input_hashes) == 1, "pair_order_stable": identity_stable,
            "semantic_obligations_distinct": semantic_distinct, "repeat_stability": repeat_status,
            "repeat_evidence_comparable": repeat_evidence_comparable,
            "exact_effective_output_hash_stability": exact_output_stable,
        },
        "tolerance_registry": {
            "contract": "CANDIDATE_CERTIFICATION_MINIMUM_SUFFICIENT_CONTRACT_R4_1",
            "repeat_and_current_plan_separation_m": UNCERTAINTY_UPPER_M,
            "expected_plan_point_count": EXPECTED_POINT_COUNT, "nonterminal_segment_min_m": NONTERMINAL_SEGMENT_MIN_M,
            "segment_max_m": SEGMENT_MAX_M, "curvature_max_inverse_m": CURVATURE_MAX_INVERSE_M,
            "speed_max_mps": SPEED_MAX_MPS, "acceleration_max_mps2": ACCELERATION_MAX_MPS2,
        },
        "pair_metrics": metrics, "candidate_validity": validity, "repeat_max_deviation_m": repeat_deviation,
        "record_count": len(rows), "parent_gpu_record_ids": [row.get("ordinal") for row in rows],
        "parent_output_hashes": [row.get("combined_output_sha256") for row in rows],
        "unsupported_safety_dimensions": {
            "collision_prediction": UNKNOWN, "TTC": UNKNOWN, "PID_tracking": UNKNOWN,
            "formal_safety": UNKNOWN, "complete_traffic_law_validation": UNKNOWN,
        },
    }
    result["certificate_sha256"] = canonical_sha256(result)
    return result
