"""Deterministic external R9 contracts; no simulator or production binding."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Mapping, Optional

import numpy as np

from driveclarify_persistent_ambiguity_runtime_v1.m2b_adapter import (
    AxisValue,
    PersistentDecisionContext,
    decide_persistent,
)
from driveclarify_persistent_ambiguity_runtime_v1.runtime import (
    HardGateCertificate,
    HardGateCertificateStatus,
    HardGateEvidenceEnvelope,
    PersistentAmbiguityReferentialRuntime,
)


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _lane(row: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(row, Mapping):
        raise TypeError("LANE_RECORD_WRONG_TYPE")
    direction = row.get("direction", [1.0, 0.0])
    if not isinstance(direction, (list, tuple)) or len(direction) != 2:
        raise ValueError("LANE_DIRECTION_MUST_HAVE_EXACTLY_TWO_COMPONENTS")
    direction_xy = [float(direction[0]), float(direction[1])]
    if not all(math.isfinite(value) for value in direction_xy):
        raise ValueError("LANE_DIRECTION_MUST_BE_FINITE")
    return {
        "road_id": int(row["road_id"]),
        "section_id": int(row.get("section_id", 0)),
        "lane_id": int(row["lane_id"]),
        "junction_id": row.get("junction_id"),
        "travel_direction_unit_xy": direction_xy,
    }


def _transform(x: float, y: float, yaw: float = 0.0) -> dict[str, Any]:
    return {
        "location_xyz_m": [float(x), float(y), 0.0],
        "rotation_ypr_deg": [float(yaw), 0.0, 0.0],
    }


def _corners(x: float, y: float, half_x: float = 1.0, half_y: float = 0.5):
    return [
        [x - half_x, y - half_y],
        [x + half_x, y - half_y],
        [x + half_x, y + half_y],
        [x - half_x, y + half_y],
    ]


def _evaluate_route_fixture_unchecked(fixture: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate a complete control census with route/topology/sweep joins."""

    frame = int(fixture["source_frame_id"])
    ego_lane = _lane(fixture["ego_lane"])
    route_lanes = tuple(_lane(row) for row in fixture["route_lanes"])
    route_keys = {
        (row["road_id"], row["section_id"], row["lane_id"], row["junction_id"])
        for row in route_lanes
    }
    horizon = float(fixture.get("forward_horizon_m", 30.0))
    evaluated = []
    restrictive, clear, not_applicable, unknown = [], [], [], []
    for source in fixture.get("controls", ()):
        actor_id = source["actor_id"]
        state = str(source.get("state", "UNKNOWN"))
        lane = _lane(source.get("lane", fixture["ego_lane"]))
        lane_key = (
            lane["road_id"], lane["section_id"], lane["lane_id"], lane["junction_id"]
        )
        distance = source.get("forward_distance_m")
        state_source_frame = source.get("state_source_frame_id", frame)
        same_frame = state_source_frame == frame
        route_join = source.get("route_join_proven", True) and lane_key in route_keys
        direction_dot = sum(
            left * right
            for left, right in zip(
                ego_lane["travel_direction_unit_xy"], lane["travel_direction_unit_xy"]
            )
        )
        same_direction = direction_dot > 0.9
        forward = distance is not None and float(distance) >= 0.0
        within = forward and float(distance) <= horizon
        intersection = bool(source.get("sweep_intersection", True))
        actor_proximity_only = bool(source.get("actor_proximity_only", False))
        missing = bool(source.get("missing_required_field", False))
        if actor_proximity_only or missing or not same_frame:
            applicability = "UNKNOWN"
            reason = (
                "ACTOR_PROXIMITY_WITHOUT_ROUTE_JOIN"
                if actor_proximity_only
                else "SOURCE_FRAME_MISMATCH"
                if not same_frame
                else "MISSING_REQUIRED_FIELD"
            )
        elif route_join and same_direction and forward and within and intersection:
            applicability = "APPLICABLE"
            reason = "ROUTE_LOCAL_ORIENTED_SWEEP"
        else:
            applicability = "NOT_APPLICABLE"
            reason = str(source["negative_reason"])
        restriction = (
            "RESTRICTIVE"
            if state in {"RED", "YELLOW", "STOP"}
            else "CLEAR"
            if state == "GREEN"
            else "UNKNOWN"
        )
        if applicability == "UNKNOWN" or (applicability == "APPLICABLE" and restriction == "UNKNOWN"):
            unknown.append(actor_id)
        elif applicability == "NOT_APPLICABLE":
            not_applicable.append(actor_id)
        elif restriction == "RESTRICTIVE":
            restrictive.append(actor_id)
        else:
            clear.append(actor_id)
        x = float(distance or 1.0)
        evaluated.append(
            {
                "actor_id": actor_id,
                "control_type": source.get("control_type", "TRAFFIC_LIGHT"),
                "state": state,
                "restriction_status": restriction,
                "state_source_frame_id": state_source_frame,
                "same_frame": same_frame,
                "actor_transform": _transform(x, float(source.get("lateral_m", 0.0))),
                "trigger_world_transform": _transform(
                    x, float(source.get("lateral_m", 0.0)), float(source.get("yaw_deg", 0.0))
                ),
                "trigger_extent_xyz_m": [1.0, 0.5, 1.0],
                "trigger_oriented_corners_world_xy_m": _corners(
                    x, float(source.get("lateral_m", 0.0))
                ),
                "affected_waypoints": [
                    {
                        "waypoint_id": f"wp-{actor_id}",
                        "lane": lane,
                        "world_xy_m": [x, float(source.get("lateral_m", 0.0))],
                        "kind": "STOP_WAYPOINT" if source.get("control_type") == "STOP_SIGN" else "AFFECTED_LANE",
                    }
                ],
                "ego_to_route": "PASS" if route_join else "UNKNOWN" if applicability == "UNKNOWN" else "FAIL",
                "affected_waypoint_to_route": "PASS" if route_join else "UNKNOWN" if applicability == "UNKNOWN" else "FAIL",
                "same_travel_direction": "PASS" if same_direction else "FAIL",
                "forward_of_ego": "PASS" if forward else "FAIL",
                "control_forward_distance_m": (
                    float(distance)
                    if distance is not None and float(distance) >= 0.0
                    else None
                ),
                "within_horizon": "PASS" if within else "FAIL",
                "oriented_sweep_intersection": "PASS" if intersection else "FAIL",
                "applicability_status": applicability,
                "reason_code": reason,
            }
        )
    complete = bool(fixture.get("census_complete", True))
    all_ids = {row["actor_id"] for row in evaluated}
    partition = set(restrictive) | set(clear) | set(not_applicable) | set(unknown)
    all_accounted = complete and partition == all_ids
    if restrictive:
        status = "BLOCKED"
        reason_code = "VERIFIED_ROUTE_LOCAL_RESTRICTIVE_CONTROL"
    elif complete and all_accounted and not unknown:
        status = "PASS"
        reason_code = "COMPLETE_CENSUS_NO_APPLICABLE_RESTRICTIVE_CONTROL"
    else:
        status = "UNKNOWN"
        reason_code = "INCOMPLETE_CONTROL_CENSUS" if not complete else "GEOMETRY_OR_TOPOLOGY_UNPROVABLE"
    route_polyline = [[0.0, 0.0], [horizon, 0.0]]
    source_identity = {
        "episode_id": str(fixture["fixture_id"]),
        "source_frame_id": frame,
        "simulation_time_s": float(fixture.get("simulation_time_s", 1.0)),
        "world_snapshot_id": f"snapshot-{frame}",
        "map_id": "R9-STATIC-TRAIN-MAP",
        "route_version": "R9-STATIC-ROUTE-V1",
    }
    certificate = {
        "schema_version": "driveclarify.route_local_traffic_control_evidence.r9.v2",
        "certificate_id": "route-certificate-" + str(fixture["fixture_id"]).lower(),
        "source_identity": source_identity,
        "ego_geometry": {
            "source_frame_id": frame,
            "same_frame": True,
            "lane": ego_lane,
            "world_transform": _transform(0.0, 0.0),
            "bounding_box_extent_xyz_m": [2.2, 1.0, 0.8],
            "footprint_corners_world_xy_m": _corners(0.0, 0.0, 2.2, 1.0),
            "velocity_world_xyz_mps": [5.0, 0.0, 0.0],
        },
        "selected_route_corridor": {
            "corridor_id": "r9-static-corridor",
            "source_frame_id": frame,
            "same_frame": True,
            "ordered_lane_segments": list(route_lanes),
            "polyline_world_xy_m": route_polyline,
            "corridor_sha256": canonical_sha256(route_polyline),
        },
        "sweep_geometry": {
            "construction_id": "EGO_ORIENTED_FOOTPRINT_FORWARD_SWEEP_V1",
            "source_frame_id": frame,
            "same_frame": True,
            "start_transform": _transform(0.0, 0.0),
            "end_transform": _transform(horizon, 0.0),
            "forward_horizon_m": horizon,
            "time_horizon_s": 3.0,
            "sample_spacing_m": 1.0,
            "start_footprint_world_xy_m": _corners(0.0, 0.0, 2.2, 1.0),
            "end_footprint_world_xy_m": _corners(horizon, 0.0, 2.2, 1.0),
            "swept_polygon_world_xy_m": [[-2.2, -1.0], [horizon + 2.2, -1.0], [horizon + 2.2, 1.0], [-2.2, 1.0]],
            "sweep_sha256": canonical_sha256([frame, horizon, "oriented"]),
        },
        "control_census": {
            "source_frame_id": frame,
            "same_frame": True,
            "census_complete": complete,
            "actor_count": len(evaluated),
            "actor_ids": [row["actor_id"] for row in evaluated],
            "world_control_set_sha256": canonical_sha256([row["actor_id"] for row in evaluated]),
            "evaluated_controls": evaluated,
        },
        "aggregate": {
            "aggregation_rule": "BLOCK_IF_ANY_VERIFIED_APPLICABLE_RESTRICTIVE;_PASS_ONLY_IF_COMPLETE_CENSUS_AND_NO_RESTRICTIVE_OR_UNKNOWN;OTHERWISE_UNKNOWN",
            "applicable_restrictive_control_ids": restrictive,
            "applicable_clear_control_ids": clear,
            "not_applicable_control_ids": not_applicable,
            "unknown_control_ids": unknown,
            "all_census_controls_accounted": all_accounted,
        },
        "hard_rule_status": status,
        "reason_code": reason_code,
        "canonical_input_sha256": canonical_sha256(fixture),
    }
    certificate["certificate_sha256"] = canonical_sha256(certificate)
    return certificate


def evaluate_route_fixture(fixture: Any) -> dict[str, Any]:
    """Normalize/validate nested route-control evidence and fail closed."""
    try:
        if not isinstance(fixture, Mapping):
            raise TypeError("ROUTE_CONTROL_RECORD_WRONG_TYPE")
        return _evaluate_route_fixture_unchecked(fixture)
    except (IndexError, KeyError, TypeError, ValueError, OverflowError) as error:
        value = {
            "schema_version": "driveclarify.route_control.pre_entry_rejection.r4_1.v1",
            "status": "PRE_ENTRY_REJECTED",
            "hard_rule_status": "UNKNOWN",
            "authorization_eligible": False,
            "decision": "FALLBACK",
            "reason_code": "MALFORMED_NESTED_ROUTE_OR_TRAFFIC_CONTROL_EVIDENCE",
            "error_type": type(error).__name__,
            "error_detail": str(error),
        }
        value["certificate_sha256"] = canonical_sha256(value)
        return value


@dataclass(frozen=True)
class StaticHardGateEvidenceProvider:
    physical_status: str
    route_status: str
    frame_offset: int = 0
    observation_id_override: Optional[str] = None
    candidate_bundle_id: Optional[str] = None

    def resolve_hard_gate_evidence(
        self,
        *,
        source_observation_id: str,
        source_frame_id: Any,
        route_version: Optional[str],
        environment_digest: Optional[str],
    ) -> HardGateEvidenceEnvelope:
        del route_version, environment_digest
        frame = int(source_frame_id) + self.frame_offset
        physical = {
            "PASS": HardGateCertificateStatus.VERIFIED_PHYSICAL_SAFETY_PASS,
            "BLOCKED": HardGateCertificateStatus.VERIFIED_PHYSICAL_SAFETY_BLOCKED,
            "UNKNOWN": HardGateCertificateStatus.UNKNOWN,
        }[self.physical_status]
        route = {
            "PASS": HardGateCertificateStatus.VERIFIED_ROUTE_LOCAL_PASS,
            "BLOCKED": HardGateCertificateStatus.VERIFIED_ROUTE_LOCAL_BLOCKED,
            "UNKNOWN": HardGateCertificateStatus.UNKNOWN,
        }[self.route_status]
        def cert(kind: str, status: HardGateCertificateStatus) -> HardGateCertificate:
            verified = status is not HardGateCertificateStatus.UNKNOWN
            return HardGateCertificate(
                f"{kind}-{source_observation_id}-{frame}" if verified else None,
                canonical_sha256([kind, source_observation_id, frame, status.value]) if verified else None,
                frame,
                status,
            )
        current_frame = int(source_frame_id)
        return HardGateEvidenceEnvelope(
            str(self.observation_id_override or source_observation_id),
            frame,
            cert("physical", physical),
            cert("route", route),
            source_timestamp=float(frame),
            current_frame_id=current_frame,
            current_timestamp=float(current_frame),
            producer_id="StaticHardGateEvidenceProvider.R4_1",
            candidate_bundle_id=self.candidate_bundle_id,
        )


class _Tensor:
    def __init__(self, value: Any) -> None:
        self.value = np.ascontiguousarray(value, dtype=np.float32)
    def detach(self): return self
    def cpu(self): return self
    def contiguous(self): return self
    def numpy(self): return self.value


def _k1_decision(physical: str, route: str) -> str:
    runtime = PersistentAmbiguityReferentialRuntime.__new__(PersistentAmbiguityReferentialRuntime)
    runtime._initial_k1_pending = True
    runtime._initial_k1_emitted = False
    runtime._latest_observation_id = "r9-k1-observation"
    runtime._latest_frame = 901
    runtime._runtime_route_version = "R9-STATIC-ROUTE-V1"
    runtime._runtime_environment_digest = canonical_sha256("R9-STATIC-TRAIN-MAP")
    runtime._hard_gate_evidence_provider = StaticHardGateEvidenceProvider(physical, route)
    runtime._method_decision_envelope = None
    runtime._method_decision_history = []
    runtime._method_m3_transactions = []
    runtime._receipt = {"control_source_receipts": [], "hard_gate_evidence_history": []}
    runtime._episode_id = None
    runtime._normal_forwards = 1
    runtime._candidate_forwards = 0
    runtime._latest_image = None
    runtime._emit_initial_k1_decision(
        _Tensor([[float(i), 0.0] for i in range(20)]),
        _Tensor([[float(i), 0.0] for i in range(10)]),
    )
    return runtime._decision.recommendation.decision.value


def _persistent_context(decision: str, physical: str, route: str) -> PersistentDecisionContext:
    values = dict(
        active_candidate_count=2, semantic_state="UNRESOLVED",
        current_action_relation=AxisValue.CURRENT_ACTION_EQUIVALENT,
        future_obligation_relation=AxisValue.FUTURE_DIVERGENT,
        evidence_fresh=True, full_plan_coverage=True, alignment_verified=True,
        shared_action_safe=True, recoverable=True, latest_safe_slack_positive=True,
        decision_deadline_available=True, decision_deadline_crossed=False,
        hard_safety_gate=physical == "PASS", hard_rule_gate=route == "PASS",
        active_query=False, active_holding_lease=False,
        multiple_plausible_interpretations=True,
        material_consequence_divergence=False, answer_changes_decision=True,
        positive_query_value=True, query_budget_available=True,
        answer_likely_before_deadline=True, passenger_resolvable=True,
        verified_holding_available=False,
    )
    if decision == "ACT_SHARED":
        pass
    elif decision == "ASK":
        values.update(current_action_relation=AxisValue.CURRENT_ACTION_DIVERGENT, shared_action_safe=False, material_consequence_divergence=True)
    elif decision == "WAIT":
        values.update(current_action_relation=AxisValue.UNKNOWN, active_query=True, active_holding_lease=True, verified_holding_available=True, query_budget_available=False)
    elif decision == "FALLBACK":
        values.update(current_action_relation=AxisValue.UNKNOWN, evidence_fresh=False)
    return PersistentDecisionContext(**values)


def exercise_decision_path(decision: str, *, physical: str = "PASS", route: str = "PASS") -> str:
    """Exercise the revised K1 owner or its imported production reducer."""
    if decision == "ACT":
        return _k1_decision(physical, route)
    recommendation = decide_persistent(_persistent_context(decision, physical, route))
    return recommendation.decision.value
