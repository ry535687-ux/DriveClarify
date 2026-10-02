"""Replayable R4.1 evidence construction over immutable GPU parent records."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence

from .contracts import canonical_sha256


def _compressed(values: Sequence[int]) -> list[int]:
    result: list[int] = []
    for value in values:
        if not result or result[-1] != value:
            result.append(value)
    return result


def _topology_for_route(
    route: Sequence[Sequence[float]], *, obligation: str, target_lane_id: int,
    source_observation_id: str,
) -> dict[str, Any]:
    projections = []
    lanes = []
    for index, point in enumerate(route):
        x, y = float(point[0]), float(point[1])
        lane_id = -2 if y >= 2.5 else -1
        lane_center = 3.5 if lane_id == -2 else 0.0
        lanes.append(lane_id)
        projections.append({
            "route_index": index,
            "finite_projection": True,
            "road_id": 1,
            "lane_id": lane_id,
            "lane_center_y_m": lane_center,
            "projection_distance_m": abs(y - lane_center),
            "projected_longitudinal_m": x,
            "travel_direction_dot": 1.0,
            "opposing_or_illegal": False,
        })
    path = _compressed(lanes)
    observed = "LANE_CHANGE_RIGHT" if path and path[-1] == -2 else "KEEP_CURRENT_LANE"
    map_payload = {
        "road_id": 1,
        "legal_lane_ids": [-1, -2],
        "lane_centers_y_m": {"-1": 0.0, "-2": 3.5},
        "corridor_half_width_m": 2.2,
        "lane_successor_edges": [[-1, -2]],
    }
    return {
        "map_evidence_id": "R4_1_SYNTHETIC_CORRIDOR_CAPABILITY_FIXTURE_V1",
        "map_evidence_sha256": canonical_sha256(map_payload),
        "map_evidence_class": "SYNTHETIC_CERTIFIER_CAPABILITY_FIXTURE",
        "map_evidence_provider": "driveclarify_protocol_cpu_v1.candidate_evidence",
        "source_observation_id": source_observation_id,
        "synthetic_projection_derived_from_candidate_output": True,
        "official_model_performance_result": False,
        "map_payload": map_payload,
        "road_id": 1,
        "legal_lane_ids": [-1, -2],
        "corridor_half_width_m": 2.2,
        "lane_successor_edges": [[-1, -2]],
        "lane_path": path,
        "point_projections": projections,
        "obligation_branch": obligation,
        "obligation_target_lane_id": target_lane_id,
        "observed_branch": observed,
    }


def build_base_evidence(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    first = {}
    for record in records:
        first.setdefault(int(record["candidate_order_index"]), record)
    if set(first) != {0, 1}:
        return {"evidence_version": "R4_1_V1"}
    prompts = [str(first[index].get("prompt_text", "")) for index in (0, 1)]
    duplicate_obligation = prompts[0] == prompts[1]
    obligations = ["LANE_CHANGE_RIGHT", "LANE_CHANGE_RIGHT" if duplicate_obligation else "KEEP_CURRENT_LANE"]
    target_lanes = [-2, -2 if duplicate_obligation else -1]
    semantic = {}
    for index in (0, 1):
        record = first[index]
        semantic[str(index)] = {
            "interpretation_id": record["interpretation_id"],
            "semantic_digest": record["semantic_digest"],
            "obligation": obligations[index],
            "target_lane_id": target_lanes[index],
        }
    value = {
        "evidence_version": "R4_1_V1",
        "semantic_bindings": semantic,
        "topology_evidence_status": "UNKNOWN_MISSING_PROVIDED_MAP_LANE_CORRIDOR_EVIDENCE",
        "topology": {},
        "sample_interval_s": None,
        "derived_overrides": {},
        "topology_overrides": {},
    }
    value["evidence_sha256"] = canonical_sha256(value)
    return value


def build_synthetic_corridor_evidence(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Build an explicitly synthetic certifier-capability fixture.

    The immutable GPU observations contain no independently supplied map/lane
    corridor.  This helper is therefore forbidden as an official model result:
    it only exercises PASS/FAIL branches of the certifier in CPU tests.
    """
    value = build_base_evidence(records)
    first: dict[int, Mapping[str, Any]] = {}
    for record in records:
        first.setdefault(int(record["candidate_order_index"]), record)
    if set(first) != {0, 1}:
        return value
    topology = {}
    for index in (0, 1):
        binding = value["semantic_bindings"][str(index)]
        source = first[index].get("source_observation", {})
        topology[str(index)] = _topology_for_route(
            first[index]["route"],
            obligation=str(binding["obligation"]),
            target_lane_id=int(binding["target_lane_id"]),
            source_observation_id=str(source.get("observation_id", "MISSING")),
        )
    value["topology"] = topology
    value["topology_evidence_status"] = "SYNTHETIC_CERTIFIER_CAPABILITY_ONLY"
    value["synthetic_certifier_test"] = True
    value["official_model_performance_result"] = False
    value.pop("evidence_sha256", None)
    value["evidence_sha256"] = canonical_sha256(value)
    return value


def _parent_chain(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "parent_gpu_record_ids": [int(row["ordinal"]) for row in records],
        "parent_output_hashes": [str(row["combined_output_sha256"]) for row in records],
    }


def derive_semantic_cross_swap(records: Sequence[Mapping[str, Any]], evidence: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    before = deepcopy(dict(evidence))
    after = deepcopy(before)
    after["semantic_bindings"] = {
        "0": deepcopy(before["semantic_bindings"]["1"]),
        "1": deepcopy(before["semantic_bindings"]["0"]),
    }
    after.pop("evidence_sha256", None)
    after["evidence_sha256"] = canonical_sha256(after)
    registry = {
        "negative_id": "SEMANTIC_CROSS_SWAP_DERIVED_R4_1",
        **_parent_chain(records),
        "transformation_name": "SWAP_CANDIDATE_TO_INTERPRETATION_ASSOCIATION",
        "transformation_version": "R4_1_V1",
        "transformation_parameters": {"association_keys": ["0", "1"], "model_output_tensor_modified": False},
        "before_evidence_sha": canonical_sha256(before),
        "after_evidence_sha": canonical_sha256(after),
        "target_dimension": "SEMANTIC_BINDING",
        "expected_contract_violation": "BOUND_INTERPRETATION_AND_ROUTE_OBLIGATION_DISAGREE",
        "synthetic_certifier_test": True,
        "official_model_performance_result": False,
    }
    return after, registry


def derive_disconnected_successor(records: Sequence[Mapping[str, Any]], evidence: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    before = deepcopy(dict(evidence))
    after = deepcopy(before)
    after["topology"]["0"]["lane_successor_edges"] = [[-1, 99]]
    after["topology"]["0"]["map_payload"]["lane_successor_edges"] = [[-1, 99]]
    after["topology"]["0"]["map_evidence_sha256"] = canonical_sha256(
        after["topology"]["0"]["map_payload"]
    )
    after.pop("evidence_sha256", None)
    after["evidence_sha256"] = canonical_sha256(after)
    registry = {
        "negative_id": "TOPOLOGY_DISCONNECTED_SUCCESSOR_DERIVED_R4_1",
        **_parent_chain(records),
        "transformation_name": "REPLACE_LANE_SUCCESSOR_WITH_DISCONNECTED_EDGE",
        "transformation_version": "R4_1_V1",
        "transformation_parameters": {"candidate_order_index": 0, "removed_edge": [-1, -2], "inserted_edge": [-1, 99]},
        "before_evidence_sha": canonical_sha256(before),
        "after_evidence_sha": canonical_sha256(after),
        "target_dimension": "TOPOLOGY",
        "expected_contract_violation": "LANE_SUCCESSOR_NOT_CONNECTED",
        "synthetic_certifier_test": True,
        "official_model_performance_result": False,
    }
    return after, registry


def derive_speed_spike(records: Sequence[Mapping[str, Any]], evidence: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    before = deepcopy(dict(evidence))
    after = deepcopy(before)
    transformed = []
    for record in records:
        if int(record["candidate_order_index"]) != 0:
            continue
        speed = deepcopy(record["speed"])
        speed[5] = [30.0, 0.0]
        after["derived_overrides"][str(record["ordinal"])] = {"speed": speed}
        transformed.append(int(record["ordinal"]))
    after.pop("evidence_sha256", None)
    after["evidence_sha256"] = canonical_sha256(after)
    registry = {
        "negative_id": "KINEMATIC_SPEED_SPIKE_DERIVED_R4_1",
        **_parent_chain(records),
        "transformation_name": "SET_DERIVED_SPEED_VECTOR_ABOVE_FROZEN_BOUND",
        "transformation_version": "R4_1_V1",
        "transformation_parameters": {"record_ids": transformed, "speed_index": 5, "new_value": [30.0, 0.0], "frozen_speed_bound_mps": 15.0},
        "before_evidence_sha": canonical_sha256(before),
        "after_evidence_sha": canonical_sha256(after),
        "target_dimension": "KINEMATICS",
        "expected_contract_violation": "SPEED_BOUND_EXCEEDED",
        "synthetic_certifier_test": True,
        "official_model_performance_result": False,
    }
    return after, registry


def derive_repeat_instability(records: Sequence[Mapping[str, Any]], evidence: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    before = deepcopy(dict(evidence))
    after = deepcopy(before)
    target = next(row for row in records if int(row["repeat_index"]) == 4 and int(row["candidate_order_index"]) == 0)
    route = deepcopy(target["route"])
    for index in range(10, len(route)):
        route[index][1] = float(route[index][1]) + 0.5
    after["derived_overrides"][str(target["ordinal"])] = {"route": route}
    base_topology = after["topology"]["0"]
    after["topology_overrides"][str(target["ordinal"])] = _topology_for_route(
        route,
        obligation=str(base_topology["obligation_branch"]),
        target_lane_id=int(base_topology["obligation_target_lane_id"]),
        source_observation_id=str(base_topology["source_observation_id"]),
    )
    after.pop("evidence_sha256", None)
    after["evidence_sha256"] = canonical_sha256(after)
    registry = {
        "negative_id": "SYNTHETIC_REPEAT_INSTABILITY_CERTIFIER_NEGATIVE",
        **_parent_chain(records),
        "transformation_name": "TRANSLATE_ONE_DERIVED_REPEAT_ROUTE_TAIL_Y",
        "transformation_version": "R4_1_V1",
        "transformation_parameters": {"record_id": int(target["ordinal"]), "repeat_index": 4, "candidate_order_index": 0, "start_waypoint_index": 10, "delta_y_m": 0.5, "repeat_tolerance_m": 0.3969352485356619},
        "before_evidence_sha": canonical_sha256(before),
        "after_evidence_sha": canonical_sha256(after),
        "target_dimension": "REPEAT_STABILITY",
        "expected_contract_violation": "DERIVED_REPEAT_DEVIATION_EXCEEDS_TOLERANCE",
        "synthetic_certifier_test": True,
        "official_model_performance_result": False,
        "official_model_instability": False,
    }
    return after, registry


def evidence_hash_without_embedded_hash(evidence: Mapping[str, Any]) -> str:
    return canonical_sha256({key: value for key, value in evidence.items() if key != "evidence_sha256"})


__all__ = [
    "build_base_evidence", "build_synthetic_corridor_evidence",
    "derive_semantic_cross_swap", "derive_disconnected_successor",
    "derive_speed_spike", "derive_repeat_instability", "evidence_hash_without_embedded_hash",
]
