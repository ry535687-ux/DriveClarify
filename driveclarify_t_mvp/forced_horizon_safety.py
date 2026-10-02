"""Read-only forced-horizon snapshots of the official RQ2 safety criteria.

The snapshot is an evaluator-side evidence projection.  It never calls a
criterion lifecycle method and has no reference to an agent, model, planner,
controller, PID, CARLA world, or termination owner.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from enum import Enum
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence, Tuple

from .canonical import canonical_sha256


SAFETY_SNAPSHOT_SCHEMA = "driveclarify.rq2.safety_endpoint_snapshot.v2"
CRITERION_SNAPSHOT_SCHEMA = "driveclarify.rq2.safety_endpoint_criterion_snapshot.v2"
RECEIPT_KIND = "RQ2_T_SAFETY_CRITERIA_ENDPOINT_SNAPSHOT"
PRODUCTION_PHASE = "EVALUATOR_POST_CRITERIA_TICK_READ_ONLY"
SOURCE_CLOCK = "CARLA_SIMULATION_TIME"
RQ2_T_FORCED_HORIZON_SECONDS = 35.0
SNAPSHOT_TRIGGER_REASON = "RQ2_T_35_SECOND_FORCED_HORIZON_REACHED"
NATURAL_TERMINAL_TRIGGER_REASON = "RQ2_T_AUTHORITATIVE_NATURAL_TERMINAL_REACHED"
FORCED_HORIZON_TERMINAL_CLASS = "MAXIMUM_35S_HORIZON_REACHED"
ALLOWED_NATURAL_TERMINAL_CLASSES = frozenset(
    {
        "AUTHORITATIVE_ROUTE_TREE_SUCCESS",
        "AUTHORITATIVE_ROUTE_TREE_FAILURE",
    }
)


class SafetyTruthState(str, Enum):
    KNOWN_TRUE = "KNOWN_TRUE"
    KNOWN_FALSE = "KNOWN_FALSE"
    UNKNOWN = "UNKNOWN"


class SafetyEndpointType(str, Enum):
    FORCED_35S = "FORCED_35S"
    AUTHORITATIVE_NATURAL_TERMINAL = "AUTHORITATIVE_NATURAL_TERMINAL"


@dataclass(frozen=True)
class CriterionOwnerSpec:
    endpoint_name: str
    class_name: str
    owner: str
    source_reference: str
    source_sha256: str
    event_types: Tuple[str, ...]
    collision_sensor_required: bool = False


OFFICIAL_ATOMIC_CRITERIA_SHA256 = (
    "c2b76391ddf2d324c7370c65cab571b1706aad0f80dd539f04514abff6468c92"
)
OFFICIAL_ROUTE_SCENARIO_SHA256 = (
    "aface4bb3d07c22f2fd3cef1934fe3d287781b4103596ecdd8630d1c4b4d078f"
)
OFFICIAL_SCENARIO_MANAGER_SHA256 = (
    "d001c5e4454f1c90e1b086eaf274749c85a479e2eaa00228148ced8154418844"
)

CRITERION_OWNER_SPECS: Tuple[CriterionOwnerSpec, ...] = (
    CriterionOwnerSpec(
        endpoint_name="collision",
        class_name="CollisionTest",
        owner="leaderboard_autopilot.RouteScenario.criteria_node",
        source_reference=(
            "/home/buaa/wrh/simlingo/scenario_runner_autopilot/srunner/"
            "scenariomanager/scenarioatomics/atomic_criteria.py:282"
        ),
        source_sha256=OFFICIAL_ATOMIC_CRITERIA_SHA256,
        event_types=(
            "COLLISION_STATIC",
            "COLLISION_VEHICLE",
            "COLLISION_PEDESTRIAN",
        ),
        collision_sensor_required=True,
    ),
    CriterionOwnerSpec(
        endpoint_name="route_deviation",
        class_name="InRouteTest",
        owner="leaderboard_autopilot.RouteScenario.criteria_node",
        source_reference=(
            "/home/buaa/wrh/simlingo/scenario_runner_autopilot/srunner/"
            "scenariomanager/scenarioatomics/atomic_criteria.py:1388"
        ),
        source_sha256=OFFICIAL_ATOMIC_CRITERIA_SHA256,
        event_types=("ROUTE_DEVIATION",),
    ),
    CriterionOwnerSpec(
        endpoint_name="outside_route_lanes",
        class_name="OutsideRouteLanesTest",
        owner="leaderboard_autopilot.RouteScenario.criteria_node",
        source_reference=(
            "/home/buaa/wrh/simlingo/scenario_runner_autopilot/srunner/"
            "scenariomanager/scenarioatomics/atomic_criteria.py:985"
        ),
        source_sha256=OFFICIAL_ATOMIC_CRITERIA_SHA256,
        event_types=("OUTSIDE_ROUTE_LANES_INFRACTION",),
    ),
)


@dataclass(frozen=True)
class EventEvidenceReference:
    event_type: str
    frame: Optional[int]
    message: Optional[str]
    payload: Any


@dataclass(frozen=True)
class CriterionLifetimeEvidence:
    criterion_name: str
    tracking_started_before_first_tree_tick: bool
    route_criterion_always_active_by_owner: bool
    observation_count: int
    first_observed_frame: Optional[int]
    first_observed_time_s: Optional[float]
    last_observed_frame: Optional[int]
    last_observed_time_s: Optional[float]
    present_every_observed_tick: bool
    same_instance_every_observed_tick: bool
    active_after_every_observed_tick: bool
    collision_sensor_live_after_every_observed_tick: bool
    instance_identity: Optional[str]
    evaluator_tick_owner: str
    evaluator_tick_owner_sha256: str
    terminal_observation_seen: bool = False
    terminal_invalidation_observed: bool = False
    collision_sensor_stopped_at_terminal: bool = False

    @property
    def active_window_proven(self) -> bool:
        base = (
            self.tracking_started_before_first_tree_tick
            and self.route_criterion_always_active_by_owner
            and self.observation_count > 0
            and self.present_every_observed_tick
            and self.same_instance_every_observed_tick
            and self.active_after_every_observed_tick
            and self.first_observed_frame is not None
            and self.last_observed_frame is not None
            and self.first_observed_time_s is not None
            and self.last_observed_time_s is not None
            and self.instance_identity is not None
        )
        spec = _spec_by_endpoint(self.criterion_name)
        return base and (
            self.collision_sensor_live_after_every_observed_tick
            if spec.collision_sensor_required
            else True
        )


@dataclass(frozen=True)
class ForcedHorizonCriterionSnapshot:
    schema_version: str
    criterion_name: str
    criterion_class: str
    criterion_owner: str
    criterion_source_reference: str
    criterion_source_sha256: str
    criterion_instance_identity: Optional[str]
    criterion_present: bool
    active_evaluated_proof: Mapping[str, Any]
    source_state: Mapping[str, Any]
    truth_state: str
    value: Optional[bool]
    event_evidence_references: Tuple[EventEvidenceReference, ...]
    reason_code: Optional[str]
    source_digest: str


@dataclass(frozen=True)
class RQ2TForcedHorizonSafetySnapshotReceipt:
    schema_version: str
    receipt_kind: str
    production_phase: str
    runtime_policy_access: bool
    case_id: str
    episode_id: str
    seed: int
    method: str
    timing_bucket: str
    cell_admissibility_reference: Optional[str]
    cell_admissibility_digest: Optional[str]
    cell_admissibility_binding_state: str
    scientific_window_contract_reference: str
    scientific_window_contract_digest: str
    endpoint_seconds: float
    endpoint_type: str
    endpoint_identity: str
    realized_observation_duration_s: float
    terminal_class: str
    simulation_frame: int
    simulation_time_s: float
    snapshot_trigger_reason: str
    source_clock: str
    source_evaluator_identity: str
    evaluator_instance_identity: str
    criteria: Tuple[ForcedHorizonCriterionSnapshot, ...]
    required_criteria_present: bool
    snapshot_complete: bool
    distinct_off_road_produced: bool
    distinct_wrong_lane_produced: bool
    canonical_digest: str


def _spec_by_endpoint(endpoint_name: str) -> CriterionOwnerSpec:
    for spec in CRITERION_OWNER_SPECS:
        if spec.endpoint_name == endpoint_name:
            return spec
    raise ValueError("SAFETY_ENDPOINT_NOT_AUTHORIZED:" + endpoint_name)


def _status_name(value: Any) -> str:
    name = getattr(value, "name", None)
    if isinstance(name, str):
        return name
    text = str(value)
    return text.rsplit(".", 1)[-1].upper()


def _json_projection(value: Any, depth: int = 0) -> Any:
    """Read a bounded, JSON-safe projection without invoking domain methods."""

    if depth > 5:
        return {"python_type": type(value).__module__ + "." + type(value).__name__}
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, Enum):
        return value.name
    if isinstance(value, Mapping):
        return {
            str(key): _json_projection(item, depth + 1)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_json_projection(item, depth + 1) for item in value]
    xyz = tuple(getattr(value, axis, None) for axis in ("x", "y", "z"))
    if all(isinstance(item, (int, float)) for item in xyz):
        return {"x": xyz[0], "y": xyz[1], "z": xyz[2]}
    result = {"python_type": type(value).__module__ + "." + type(value).__name__}
    for attribute in ("id", "type_id"):
        item = getattr(value, attribute, None)
        if isinstance(item, (str, bool, int, float)):
            result[attribute] = item
    return result


def _event_reference(event: Any) -> EventEvidenceReference:
    event_type = getattr(event, "_type", None)
    event_name = getattr(event_type, "name", None)
    return EventEvidenceReference(
        event_type=(event_name if isinstance(event_name, str) else str(event_type)),
        frame=(
            int(getattr(event, "_frame"))
            if isinstance(getattr(event, "_frame", None), int)
            else None
        ),
        message=(
            str(getattr(event, "_message"))
            if getattr(event, "_message", None) is not None
            else None
        ),
        payload=_json_projection(getattr(event, "_dict", None)),
    )


def _sensor_live(criterion: Any) -> bool:
    sensor = getattr(criterion, "_collision_sensor", None)
    if sensor is None:
        return False
    listening = getattr(sensor, "is_listening", None)
    if isinstance(listening, bool):
        return listening
    return True


def _criterion_source_state(criterion: Any, events: Sequence[EventEvidenceReference]) -> Mapping[str, Any]:
    state = {
        "pytrees_status": _status_name(getattr(criterion, "status", "UNKNOWN")),
        "test_status": str(getattr(criterion, "test_status", "MISSING")),
        "actual_value": _json_projection(getattr(criterion, "actual_value", None)),
        "event_count": len(events),
        "event_types": [event.event_type for event in events],
    }
    for attribute in (
        "_current_index",
        "_out_route_distance",
        "_in_safe_route",
        "_total_distance",
        "_wrong_distance",
        "_outside_lane_active",
        "_wrong_lane_active",
        "_wrong_direction_active",
    ):
        if hasattr(criterion, attribute):
            state[attribute] = _json_projection(getattr(criterion, attribute))
    if type(criterion).__name__ == "CollisionTest":
        state["collision_sensor_present"] = getattr(criterion, "_collision_sensor", None) is not None
        state["collision_sensor_live"] = _sensor_live(criterion)
    return state


def _active_proof_mapping(lifetime: CriterionLifetimeEvidence) -> Mapping[str, Any]:
    return {
        "tracking_started_before_first_tree_tick": lifetime.tracking_started_before_first_tree_tick,
        "route_criterion_always_active_by_owner": lifetime.route_criterion_always_active_by_owner,
        "observation_count": lifetime.observation_count,
        "first_observed_frame": lifetime.first_observed_frame,
        "first_observed_time_s": lifetime.first_observed_time_s,
        "last_observed_frame": lifetime.last_observed_frame,
        "last_observed_time_s": lifetime.last_observed_time_s,
        "present_every_observed_tick": lifetime.present_every_observed_tick,
        "same_instance_every_observed_tick": lifetime.same_instance_every_observed_tick,
        "active_after_every_observed_tick": lifetime.active_after_every_observed_tick,
        "collision_sensor_live_after_every_observed_tick": lifetime.collision_sensor_live_after_every_observed_tick,
        "terminal_observation_seen": lifetime.terminal_observation_seen,
        "terminal_invalidation_observed": lifetime.terminal_invalidation_observed,
        "collision_sensor_stopped_at_terminal": lifetime.collision_sensor_stopped_at_terminal,
        "evaluator_tick_owner": lifetime.evaluator_tick_owner,
        "evaluator_tick_owner_sha256": lifetime.evaluator_tick_owner_sha256,
        "active_window_proven": lifetime.active_window_proven,
    }


def _truth_from_official_state(
    criterion: Any,
    spec: CriterionOwnerSpec,
    lifetime: CriterionLifetimeEvidence,
    events: Sequence[EventEvidenceReference],
) -> Tuple[SafetyTruthState, Optional[bool], Optional[str]]:
    test_status = str(getattr(criterion, "test_status", "MISSING")).upper()
    actual_value = getattr(criterion, "actual_value", None)
    relevant_events = [event for event in events if event.event_type in spec.event_types]
    if test_status == "FAILURE" or (
        isinstance(actual_value, (int, float)) and actual_value > 0
    ) or relevant_events:
        return SafetyTruthState.KNOWN_TRUE, True, None
    if not lifetime.active_window_proven:
        return SafetyTruthState.UNKNOWN, None, "CRITERION_ACTIVE_WINDOW_NOT_PROVEN"
    if test_status not in {"INIT", "RUNNING", "SUCCESS", "ACCEPTABLE"}:
        return SafetyTruthState.UNKNOWN, None, "OFFICIAL_TEST_STATUS_UNRESOLVED"
    if not isinstance(actual_value, (int, float)) or actual_value != 0:
        return SafetyTruthState.UNKNOWN, None, "OFFICIAL_ACCUMULATED_VALUE_UNRESOLVED"
    if events:
        return SafetyTruthState.UNKNOWN, None, "OFFICIAL_EVENT_TYPE_UNRECOGNIZED"
    return SafetyTruthState.KNOWN_FALSE, False, None


def locate_route_owned_criteria(scenario: Any) -> Mapping[str, Any]:
    """Locate only the direct, always-active RouteScenario criterion children."""

    criteria_node = getattr(scenario, "criteria_node", None)
    direct_children = tuple(getattr(criteria_node, "children", ()) or ())
    output = {}
    for spec in CRITERION_OWNER_SPECS:
        matches = [child for child in direct_children if type(child).__name__ == spec.class_name]
        output[spec.endpoint_name] = matches[0] if len(matches) == 1 else None
    return output


def _snapshot_safety_endpoint(
    *,
    case_id: str,
    episode_id: str,
    seed: int,
    method: str,
    timing_bucket: str,
    simulation_frame: int,
    simulation_time_s: float,
    criteria: Mapping[str, Any],
    lifetime_evidence: Mapping[str, CriterionLifetimeEvidence],
    scientific_window_contract_reference: str,
    scientific_window_contract_digest: str,
    source_evaluator_identity: str,
    evaluator_instance_identity: str,
    endpoint_type: SafetyEndpointType | str,
    terminal_class: str,
    cell_admissibility_reference: Optional[str] = None,
    cell_admissibility_digest: Optional[str] = None,
) -> RQ2TForcedHorizonSafetySnapshotReceipt:
    """Project already-accumulated official state into a canonical receipt."""

    if simulation_frame < 0 or simulation_time_s < 0:
        raise ValueError("SAFETY_ENDPOINT_TIME_INVALID")
    endpoint = SafetyEndpointType(endpoint_type)
    if endpoint is SafetyEndpointType.FORCED_35S:
        if simulation_time_s < RQ2_T_FORCED_HORIZON_SECONDS:
            raise ValueError("FORCED_HORIZON_ENDPOINT_NOT_REACHED")
        if terminal_class != FORCED_HORIZON_TERMINAL_CLASS:
            raise ValueError("FORCED_HORIZON_TERMINAL_CLASS_INVALID")
        endpoint_identity = "RQ2_T_35_SECOND_FIRST_POST_CRITERIA_TICK_AT_OR_AFTER_CAP"
        trigger_reason = SNAPSHOT_TRIGGER_REASON
    else:
        if simulation_time_s >= RQ2_T_FORCED_HORIZON_SECONDS:
            raise ValueError("NATURAL_TERMINAL_MUST_PRECEDE_FORCED_HORIZON")
        if terminal_class not in ALLOWED_NATURAL_TERMINAL_CLASSES:
            raise ValueError("NATURAL_TERMINAL_CLASS_NOT_AUTHORIZED")
        endpoint_identity = "RQ2_T_FIRST_AUTHORITATIVE_ROUTE_TREE_TERMINAL_BEFORE_CAP"
        trigger_reason = NATURAL_TERMINAL_TRIGGER_REASON
    if len(scientific_window_contract_digest) != 64:
        raise ValueError("SCIENTIFIC_WINDOW_CONTRACT_DIGEST_INVALID")
    if cell_admissibility_digest is not None and len(cell_admissibility_digest) != 64:
        raise ValueError("CELL_ADMISSIBILITY_DIGEST_INVALID")
    rows = []
    for spec in CRITERION_OWNER_SPECS:
        criterion = criteria.get(spec.endpoint_name)
        lifetime = lifetime_evidence.get(spec.endpoint_name)
        if criterion is None or lifetime is None:
            proof = (
                _active_proof_mapping(lifetime)
                if lifetime is not None
                else {"active_window_proven": False}
            )
            state = {"criterion_located": False}
            source_digest = canonical_sha256(
                {"criterion": spec.endpoint_name, "proof": proof, "source_state": state}
            )
            rows.append(
                ForcedHorizonCriterionSnapshot(
                    schema_version=CRITERION_SNAPSHOT_SCHEMA,
                    criterion_name=spec.endpoint_name,
                    criterion_class=spec.class_name,
                    criterion_owner=spec.owner,
                    criterion_source_reference=spec.source_reference,
                    criterion_source_sha256=spec.source_sha256,
                    criterion_instance_identity=None,
                    criterion_present=False,
                    active_evaluated_proof=proof,
                    source_state=state,
                    truth_state=SafetyTruthState.UNKNOWN.value,
                    value=None,
                    event_evidence_references=(),
                    reason_code="OFFICIAL_ROUTE_CRITERION_NOT_LOCATED",
                    source_digest=source_digest,
                )
            )
            continue
        events = tuple(_event_reference(event) for event in tuple(getattr(criterion, "events", ()) or ()))
        source_state = _criterion_source_state(criterion, events)
        proof = _active_proof_mapping(lifetime)
        truth, value, reason = _truth_from_official_state(criterion, spec, lifetime, events)
        source_digest = canonical_sha256(
            {
                "criterion": spec.endpoint_name,
                "instance_identity": lifetime.instance_identity,
                "proof": proof,
                "source_state": source_state,
                "events": events,
            }
        )
        rows.append(
            ForcedHorizonCriterionSnapshot(
                schema_version=CRITERION_SNAPSHOT_SCHEMA,
                criterion_name=spec.endpoint_name,
                criterion_class=spec.class_name,
                criterion_owner=spec.owner,
                criterion_source_reference=spec.source_reference,
                criterion_source_sha256=spec.source_sha256,
                criterion_instance_identity=lifetime.instance_identity,
                criterion_present=True,
                active_evaluated_proof=proof,
                source_state=source_state,
                truth_state=truth.value,
                value=value,
                event_evidence_references=events,
                reason_code=reason,
                source_digest=source_digest,
            )
        )
    required_present = all(row.criterion_present for row in rows)
    complete = required_present and all(
        row.truth_state != SafetyTruthState.UNKNOWN.value for row in rows
    )
    value = RQ2TForcedHorizonSafetySnapshotReceipt(
        schema_version=SAFETY_SNAPSHOT_SCHEMA,
        receipt_kind=RECEIPT_KIND,
        production_phase=PRODUCTION_PHASE,
        runtime_policy_access=False,
        case_id=case_id,
        episode_id=episode_id,
        seed=int(seed),
        method=method,
        timing_bucket=timing_bucket,
        cell_admissibility_reference=cell_admissibility_reference,
        cell_admissibility_digest=cell_admissibility_digest,
        cell_admissibility_binding_state=(
            "BOUND" if cell_admissibility_digest else "POSTEXECUTION_JOIN_PENDING"
        ),
        scientific_window_contract_reference=scientific_window_contract_reference,
        scientific_window_contract_digest=scientific_window_contract_digest,
        endpoint_seconds=RQ2_T_FORCED_HORIZON_SECONDS,
        endpoint_type=endpoint.value,
        endpoint_identity=endpoint_identity,
        realized_observation_duration_s=float(simulation_time_s),
        terminal_class=terminal_class,
        simulation_frame=int(simulation_frame),
        simulation_time_s=float(simulation_time_s),
        snapshot_trigger_reason=trigger_reason,
        source_clock=SOURCE_CLOCK,
        source_evaluator_identity=source_evaluator_identity,
        evaluator_instance_identity=evaluator_instance_identity,
        criteria=tuple(rows),
        required_criteria_present=required_present,
        snapshot_complete=complete,
        distinct_off_road_produced=False,
        distinct_wrong_lane_produced=False,
        canonical_digest="",
    )
    return replace(
        value,
        canonical_digest=canonical_sha256(value, exclude=("canonical_digest",)),
    )


def snapshot_forced_horizon_safety(
    *,
    case_id: str,
    episode_id: str,
    seed: int,
    method: str,
    timing_bucket: str,
    simulation_frame: int,
    simulation_time_s: float,
    criteria: Mapping[str, Any],
    lifetime_evidence: Mapping[str, CriterionLifetimeEvidence],
    scientific_window_contract_reference: str,
    scientific_window_contract_digest: str,
    source_evaluator_identity: str,
    evaluator_instance_identity: str,
    cell_admissibility_reference: Optional[str] = None,
    cell_admissibility_digest: Optional[str] = None,
) -> RQ2TForcedHorizonSafetySnapshotReceipt:
    """Seal the first existing post-criteria tick at or after the 35 s cap."""

    return _snapshot_safety_endpoint(
        case_id=case_id,
        episode_id=episode_id,
        seed=seed,
        method=method,
        timing_bucket=timing_bucket,
        simulation_frame=simulation_frame,
        simulation_time_s=simulation_time_s,
        criteria=criteria,
        lifetime_evidence=lifetime_evidence,
        scientific_window_contract_reference=scientific_window_contract_reference,
        scientific_window_contract_digest=scientific_window_contract_digest,
        source_evaluator_identity=source_evaluator_identity,
        evaluator_instance_identity=evaluator_instance_identity,
        endpoint_type=SafetyEndpointType.FORCED_35S,
        terminal_class=FORCED_HORIZON_TERMINAL_CLASS,
        cell_admissibility_reference=cell_admissibility_reference,
        cell_admissibility_digest=cell_admissibility_digest,
    )


def snapshot_natural_terminal_safety(
    *,
    case_id: str,
    episode_id: str,
    seed: int,
    method: str,
    timing_bucket: str,
    simulation_frame: int,
    simulation_time_s: float,
    terminal_class: str,
    criteria: Mapping[str, Any],
    lifetime_evidence: Mapping[str, CriterionLifetimeEvidence],
    scientific_window_contract_reference: str,
    scientific_window_contract_digest: str,
    source_evaluator_identity: str,
    evaluator_instance_identity: str,
    cell_admissibility_reference: Optional[str] = None,
    cell_admissibility_digest: Optional[str] = None,
) -> RQ2TForcedHorizonSafetySnapshotReceipt:
    """Seal an allowed absorbing official tree terminal before the 35 s cap."""

    return _snapshot_safety_endpoint(
        case_id=case_id,
        episode_id=episode_id,
        seed=seed,
        method=method,
        timing_bucket=timing_bucket,
        simulation_frame=simulation_frame,
        simulation_time_s=simulation_time_s,
        criteria=criteria,
        lifetime_evidence=lifetime_evidence,
        scientific_window_contract_reference=scientific_window_contract_reference,
        scientific_window_contract_digest=scientific_window_contract_digest,
        source_evaluator_identity=source_evaluator_identity,
        evaluator_instance_identity=evaluator_instance_identity,
        endpoint_type=SafetyEndpointType.AUTHORITATIVE_NATURAL_TERMINAL,
        terminal_class=terminal_class,
        cell_admissibility_reference=cell_admissibility_reference,
        cell_admissibility_digest=cell_admissibility_digest,
    )


def receipt_to_mapping(
    receipt: RQ2TForcedHorizonSafetySnapshotReceipt,
) -> Mapping[str, Any]:
    return asdict(receipt)


def validate_forced_horizon_safety_receipt(
    receipt: Any,
) -> Mapping[str, Any]:
    value = asdict(receipt) if isinstance(receipt, RQ2TForcedHorizonSafetySnapshotReceipt) else dict(receipt)
    if value.get("schema_version") != SAFETY_SNAPSHOT_SCHEMA:
        raise ValueError("SAFETY_SNAPSHOT_SCHEMA_INVALID")
    if value.get("receipt_kind") != RECEIPT_KIND:
        raise ValueError("SAFETY_ENDPOINT_RECEIPT_KIND_INVALID")
    if value.get("production_phase") != PRODUCTION_PHASE:
        raise ValueError("SAFETY_SNAPSHOT_PHASE_INVALID")
    if value.get("runtime_policy_access") is not False:
        raise ValueError("SAFETY_SNAPSHOT_RUNTIME_ACCESS_FORBIDDEN")
    if value.get("endpoint_seconds") != RQ2_T_FORCED_HORIZON_SECONDS:
        raise ValueError("SAFETY_SNAPSHOT_HORIZON_CHANGED")
    endpoint = SafetyEndpointType(value.get("endpoint_type"))
    simulation_time_s = value.get("simulation_time_s")
    if not isinstance(simulation_time_s, (int, float)) or simulation_time_s < 0:
        raise ValueError("SAFETY_ENDPOINT_TIME_INVALID")
    if value.get("realized_observation_duration_s") != simulation_time_s:
        raise ValueError("SAFETY_EXPOSURE_DURATION_MISMATCH")
    terminal_class = value.get("terminal_class")
    if endpoint is SafetyEndpointType.FORCED_35S:
        if simulation_time_s < RQ2_T_FORCED_HORIZON_SECONDS:
            raise ValueError("FORCED_HORIZON_ENDPOINT_NOT_REACHED")
        if terminal_class != FORCED_HORIZON_TERMINAL_CLASS:
            raise ValueError("FORCED_HORIZON_TERMINAL_CLASS_INVALID")
        if value.get("snapshot_trigger_reason") != SNAPSHOT_TRIGGER_REASON:
            raise ValueError("FORCED_HORIZON_TRIGGER_INVALID")
    else:
        if simulation_time_s >= RQ2_T_FORCED_HORIZON_SECONDS:
            raise ValueError("NATURAL_TERMINAL_MUST_PRECEDE_FORCED_HORIZON")
        if terminal_class not in ALLOWED_NATURAL_TERMINAL_CLASSES:
            raise ValueError("NATURAL_TERMINAL_CLASS_NOT_AUTHORIZED")
        if value.get("snapshot_trigger_reason") != NATURAL_TERMINAL_TRIGGER_REASON:
            raise ValueError("NATURAL_TERMINAL_TRIGGER_INVALID")
    if value.get("source_clock") != SOURCE_CLOCK:
        raise ValueError("SAFETY_SNAPSHOT_CLOCK_INVALID")
    if value.get("distinct_off_road_produced") is not False:
        raise ValueError("DISTINCT_OFF_ROAD_FABRICATION_FORBIDDEN")
    if value.get("distinct_wrong_lane_produced") is not False:
        raise ValueError("DISTINCT_WRONG_LANE_FABRICATION_FORBIDDEN")
    rows = value.get("criteria")
    if not isinstance(rows, (list, tuple)):
        raise ValueError("SAFETY_SNAPSHOT_CRITERIA_INVALID")
    names = tuple(row.get("criterion_name") for row in rows)
    expected = tuple(spec.endpoint_name for spec in CRITERION_OWNER_SPECS)
    if names != expected:
        raise ValueError("SAFETY_SNAPSHOT_REQUIRED_CRITERIA_INVALID")
    for row in rows:
        state = SafetyTruthState(row.get("truth_state"))
        if state is SafetyTruthState.UNKNOWN:
            if row.get("value") is not None or not row.get("reason_code"):
                raise ValueError("SAFETY_SNAPSHOT_UNKNOWN_INVARIANT_FAILED")
        elif row.get("value") is not (state is SafetyTruthState.KNOWN_TRUE):
            raise ValueError("SAFETY_SNAPSHOT_KNOWN_VALUE_INVALID")
    required_present = all(row.get("criterion_present") is True for row in rows)
    complete = required_present and all(
        row.get("truth_state") != SafetyTruthState.UNKNOWN.value for row in rows
    )
    if value.get("required_criteria_present") is not required_present:
        raise ValueError("SAFETY_SNAPSHOT_PRESENCE_FLAG_INVALID")
    if value.get("snapshot_complete") is not complete:
        raise ValueError("SAFETY_SNAPSHOT_COMPLETENESS_FLAG_INVALID")
    if value.get("canonical_digest") != canonical_sha256(value, exclude=("canonical_digest",)):
        raise ValueError("SAFETY_SNAPSHOT_CANONICAL_DIGEST_MISMATCH")
    return value


def load_forced_horizon_safety_receipt(path: Path) -> Mapping[str, Any]:
    return validate_forced_horizon_safety_receipt(
        json.loads(path.read_text(encoding="utf-8"))
    )


def write_forced_horizon_safety_receipt_exclusive(
    path: Path,
    receipt: RQ2TForcedHorizonSafetySnapshotReceipt,
) -> None:
    value = validate_forced_horizon_safety_receipt(receipt)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
