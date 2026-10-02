"""Pure evaluator for the short-horizon Decision Evidence Contract V2.0."""

from __future__ import annotations

import itertools
import math
from dataclasses import asdict, dataclass
from typing import Mapping, Optional, Sequence, Tuple

from driveclarify_persistent_ambiguity_runtime_v1.contracts import canonical_sha256

from .types import (
    CandidateRecoverabilityV2,
    CandidateRelationshipV2,
    ClarificationUrgencyV2,
    ClarificationWindowEvidenceV2,
    CurrentExecutableCoverageEvidenceV2,
    CurrentExecutableRelationV2,
    DecisionEvidenceBundleV2,
    EvidenceAvailabilityV2,
    FutureObligationEvidenceV2,
    FutureObligationRelationV2,
    FutureObligationRowV2,
    SharedActionLeaseV2,
    SourceBindingV2,
)


@dataclass(frozen=True)
class LocalPlanV2:
    plan_id: str
    candidate_id: Optional[str]
    source_observation_id: str
    source_frame_id: object
    route_points_model_local_m: Tuple[Tuple[float, float], ...]
    control_points_model_local_m: Tuple[Tuple[float, float], ...]
    route_digest: str
    control_digest: str
    coordinate_contract_id: str
    fresh: bool = True


@dataclass(frozen=True)
class TimingCalibrationV2:
    route_seconds_per_meter_lower_bound: Optional[float]
    simulation_to_monotonic_upper_bound: Optional[float]
    source_artifacts: Tuple[str, ...]
    route_version: str
    runtime_config_bound: bool


@dataclass(frozen=True)
class DecisionEvidenceInputV2:
    source: SourceBindingV2
    candidate_bundle_id: str
    active_member_set_digest: str
    candidate_ids: Tuple[str, ...]
    local_plans: Tuple[LocalPlanV2, ...]
    future_obligations: Tuple[FutureObligationRowV2, ...]
    current_progress_m: float
    speed_upper_bound_mps: float
    maximum_normal_planning_interval_s: float
    next_refresh_monotonic_upper_s: float
    plan_checkpoint_tolerance_m: float
    calibrated_uncertainty_m: float
    lane_clearance_m: float
    expected_route_point_count: int
    expected_control_point_count: int
    shared_topology_corridor_end_progress_m: float
    candidate_commitment_progress_m: Mapping[str, float]
    lane_action_compatible: Optional[bool]
    candidate_topology_reachable: Mapping[str, Optional[bool]]
    candidate_lane_reachable: Mapping[str, Optional[bool]]
    candidate_braking_lateral_feasible: Mapping[str, Optional[bool]]
    alignment_verified: bool
    bundle_complete: bool
    bundle_fresh: bool
    dynamic_safety_gate: Optional[bool]
    hard_rule_gate: Optional[bool]
    answer_latency_simulation_s: Optional[float]
    post_answer_latency_monotonic_s: Optional[float]
    timing_calibration: TimingCalibrationV2
    full_plan_coverage_v1: Optional[bool] = None


def _finite(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _arc(points: Sequence[Sequence[float]]) -> float:
    return sum(
        math.hypot(float(right[0]) - float(left[0]), float(right[1]) - float(left[1]))
        for left, right in zip(points, points[1:])
    )


def _valid_points(points: Sequence[Sequence[float]], expected_count: int) -> bool:
    return bool(
        len(points) == expected_count
        and expected_count >= 3
        and all(
            len(row) >= 2 and _finite(row[0]) and _finite(row[1])
            for row in points
        )
    )


def _maximum_pairwise_deviation(
    plans: Sequence[LocalPlanV2], *, control: bool
) -> Optional[float]:
    maximum = 0.0
    for left, right in itertools.combinations(plans, 2):
        left_points = (
            left.control_points_model_local_m
            if control
            else left.route_points_model_local_m
        )
        right_points = (
            right.control_points_model_local_m
            if control
            else right.route_points_model_local_m
        )
        if len(left_points) < 3 or len(right_points) < 3:
            return None
        maximum = max(
            maximum,
            max(
                math.hypot(
                    float(left_points[index][0]) - float(right_points[index][0]),
                    float(left_points[index][1]) - float(right_points[index][1]),
                )
                for index in range(3)
            ),
        )
    return maximum


def _current_executable(request: DecisionEvidenceInputV2) -> CurrentExecutableCoverageEvidenceV2:
    reasons = []
    candidate_ids = tuple(sorted(request.candidate_ids))
    plan_ids = {plan.plan_id for plan in request.local_plans}
    expected_plan_ids = set(candidate_ids) | {"__AUTHORIZED_PLAN__"}
    if len(candidate_ids) < 2 or len(set(candidate_ids)) != len(candidate_ids):
        reasons.append("CURRENT_EXECUTABLE_REQUIRES_DISTINCT_K2")
    if not request.bundle_complete:
        reasons.append("CURRENT_EXECUTABLE_BUNDLE_INCOMPLETE")
    if not request.bundle_fresh:
        reasons.append("CURRENT_EXECUTABLE_BUNDLE_STALE")
    if not request.alignment_verified:
        reasons.append("CURRENT_EXECUTABLE_SOURCE_ALIGNMENT_UNKNOWN")
    if plan_ids != expected_plan_ids:
        reasons.append("CURRENT_EXECUTABLE_PLAN_KEYSET_MISMATCH")
    numeric = (
        request.current_progress_m,
        request.speed_upper_bound_mps,
        request.maximum_normal_planning_interval_s,
        request.next_refresh_monotonic_upper_s,
        request.plan_checkpoint_tolerance_m,
        request.calibrated_uncertainty_m,
        request.lane_clearance_m,
        request.shared_topology_corridor_end_progress_m,
    )
    if not all(_finite(value) for value in numeric) or any(
        float(value) < 0.0 for value in numeric[1:]
    ):
        reasons.append("CURRENT_EXECUTABLE_NUMERIC_INPUT_INVALID")

    support = {}
    valid_representation = True
    for plan in request.local_plans:
        valid = bool(
            _valid_points(
                plan.route_points_model_local_m,
                request.expected_route_point_count,
            )
            and _valid_points(
                plan.control_points_model_local_m,
                request.expected_control_point_count,
            )
            and plan.coordinate_contract_id
            == "SIMLINGO_CONTROL_PID_CHECKPOINTS_1M_EGO_X_FORWARD_Y_RIGHT"
            and math.hypot(*plan.route_points_model_local_m[0])
            <= float(request.plan_checkpoint_tolerance_m)
            and math.hypot(*plan.control_points_model_local_m[0])
            <= float(request.plan_checkpoint_tolerance_m)
            and plan.source_observation_id == request.source.source_observation_id
            and str(plan.source_frame_id) == str(request.source.source_frame_id)
            and plan.fresh
            and bool(plan.route_digest)
            and bool(plan.control_digest)
        )
        if not valid:
            valid_representation = False
            reasons.append("CURRENT_EXECUTABLE_PLAN_INVALID:" + plan.plan_id)
            continue
        support[plan.plan_id] = (
            float(request.current_progress_m)
            + _arc(plan.route_points_model_local_m)
            - float(request.calibrated_uncertainty_m)
            - float(request.plan_checkpoint_tolerance_m)
        )

    geometry_deviation = (
        _maximum_pairwise_deviation(request.local_plans, control=False)
        if valid_representation and len(request.local_plans) >= 3
        else None
    )
    control_deviation = (
        _maximum_pairwise_deviation(request.local_plans, control=True)
        if valid_representation and len(request.local_plans) >= 3
        else None
    )
    compatibility = (
        None
        if geometry_deviation is None or control_deviation is None
        else bool(
            geometry_deviation + float(request.calibrated_uncertainty_m)
            < float(request.lane_clearance_m)
            and control_deviation
            <= float(request.calibrated_uncertainty_m)
        )
    )
    if compatibility is None:
        reasons.append("CURRENT_EXECUTABLE_GEOMETRY_OR_CONTROL_UNKNOWN")

    refresh_end = float(request.current_progress_m) + (
        float(request.speed_upper_bound_mps)
        * float(request.maximum_normal_planning_interval_s)
    )
    local_support_end = min(support.values()) if len(support) == len(expected_plan_ids) else None
    commitment_end = (
        min(float(value) for value in request.candidate_commitment_progress_m.values())
        - float(request.calibrated_uncertainty_m)
        if set(request.candidate_commitment_progress_m) == set(candidate_ids)
        else None
    )
    topology_end = (
        float(request.shared_topology_corridor_end_progress_m)
        - float(request.calibrated_uncertainty_m)
    )
    endpoints = [value for value in (local_support_end, refresh_end, commitment_end, topology_end) if value is not None]
    lease_end = min(endpoints) if len(endpoints) == 4 else None
    local_coverage = bool(
        lease_end is not None
        and lease_end
        > float(request.current_progress_m) + float(request.plan_checkpoint_tolerance_m)
    )
    if not local_coverage:
        reasons.append("CURRENT_EXECUTABLE_POSITIVE_LOCAL_LEASE_UNAVAILABLE")
    if request.lane_action_compatible is None:
        reasons.append("CURRENT_EXECUTABLE_LANE_ACTION_COMPATIBILITY_UNKNOWN")

    relation = None
    availability = EvidenceAvailabilityV2.UNKNOWN
    if (
        valid_representation
        and geometry_deviation is not None
        and control_deviation is not None
        and request.lane_action_compatible is not None
        and request.bundle_complete
        and request.bundle_fresh
        and request.alignment_verified
    ):
        if request.lane_action_compatible is False or compatibility is False:
            relation = CurrentExecutableRelationV2.DIVERGENT
            availability = EvidenceAvailabilityV2.AVAILABLE
        elif local_coverage:
            relation = CurrentExecutableRelationV2.EQUIVALENT
            availability = EvidenceAvailabilityV2.AVAILABLE

    payload = {
        "source": asdict(request.source),
        "candidate_ids": candidate_ids,
        "bundle": request.candidate_bundle_id,
        "members": request.active_member_set_digest,
        "support": support,
        "lease_start": request.current_progress_m,
        "lease_end": lease_end,
        "geometry": geometry_deviation,
        "control": control_deviation,
        "lane_action": request.lane_action_compatible,
        "compatibility": compatibility,
        "availability": availability.value,
        "relation": None if relation is None else relation.value,
    }
    eligible = bool(
        availability is EvidenceAvailabilityV2.AVAILABLE
        and relation is not None
        and request.source.visibility == "RUNTIME_OBSERVABLE"
    )
    return CurrentExecutableCoverageEvidenceV2(
        schema_version="driveclarify.decision_evidence.current_executable_coverage.v2.0",
        contract_version="2.0",
        availability=availability,
        relation=relation,
        source=request.source,
        candidate_ids=candidate_ids,
        candidate_bundle_id=request.candidate_bundle_id,
        active_member_set_digest=request.active_member_set_digest,
        frame="MODEL_LOCAL_METRIC_AND_ROUTE_PROGRESS",
        unit="m",
        lease_start_progress_m=float(request.current_progress_m),
        proposed_lease_end_progress_m=lease_end,
        local_plan_support_end_by_plan_m=support,
        maximum_fixed_time_geometry_deviation_m=geometry_deviation,
        maximum_fixed_time_control_deviation_m=control_deviation,
        lane_action_compatible=request.lane_action_compatible,
        geometry_control_compatible=compatibility,
        local_coverage_available=local_coverage,
        future_target_coverage_required=False,
        authorization_eligible=eligible,
        fresh=bool(request.bundle_fresh),
        reason_codes=tuple(dict.fromkeys(reasons)),
        evidence_digest=canonical_sha256(payload),
    )


def _future_obligation(request: DecisionEvidenceInputV2) -> FutureObligationEvidenceV2:
    reasons = []
    rows = tuple(sorted(request.future_obligations, key=lambda row: row.candidate_id))
    candidate_ids = tuple(sorted(request.candidate_ids))
    if tuple(row.candidate_id for row in rows) != candidate_ids:
        reasons.append("FUTURE_OBLIGATION_CANDIDATE_KEYSET_MISMATCH")
    privileged_reads = sum(1 for row in rows if row.privileged)
    for row in rows:
        if row.route_version != request.source.route_version:
            reasons.append("FUTURE_OBLIGATION_ROUTE_VERSION_MISMATCH:" + row.candidate_id)
        if not row.active_unresolved:
            reasons.append("FUTURE_OBLIGATION_CANDIDATE_NOT_ACTIVE:" + row.candidate_id)
        if not row.semantic_fresh:
            reasons.append("FUTURE_OBLIGATION_STALE:" + row.candidate_id)
        if not row.topology_binding_available:
            reasons.append("FUTURE_OBLIGATION_TOPOLOGY_UNAVAILABLE:" + row.candidate_id)
        if row.visibility != "RUNTIME_OBSERVABLE":
            reasons.append("FUTURE_OBLIGATION_NON_RUNTIME_VISIBILITY:" + row.candidate_id)
        if row.privileged:
            reasons.append("FUTURE_OBLIGATION_PRIVILEGED_SOURCE:" + row.candidate_id)
        if not row.authorization_purpose:
            reasons.append("FUTURE_OBLIGATION_PURPOSE_NOT_AUTHORIZATION:" + row.candidate_id)
        if not {
            "RUNTIME_RGB_0_GROUNDING",
            "LIVE_CARLA_HD_MAP_TOPOLOGY",
        }.issubset(set(row.source_kinds)):
            reasons.append("FUTURE_OBLIGATION_RUNTIME_PROVENANCE_INCOMPLETE:" + row.candidate_id)
        required_strings = (
            row.candidate_id,
            row.interpretation_id,
            row.semantic_sha256,
            row.obligation_digest,
            row.target_id,
            row.junction_id,
            row.branch_id,
            row.source_observation_id,
        )
        if not all(isinstance(value, str) and value for value in required_strings):
            reasons.append("FUTURE_OBLIGATION_IDENTITY_INCOMPLETE:" + row.candidate_id)
    availability = (
        EvidenceAvailabilityV2.AVAILABLE
        if rows and not reasons
        else EvidenceAvailabilityV2.STALE
        if rows and any("STALE" in reason for reason in reasons)
        else EvidenceAvailabilityV2.UNKNOWN
    )
    relation = None
    if availability is EvidenceAvailabilityV2.AVAILABLE:
        relation = (
            FutureObligationRelationV2.DIVERGENT
            if len({row.obligation_digest for row in rows}) >= 2
            else FutureObligationRelationV2.EQUIVALENT
        )
    payload = {
        "source": asdict(request.source),
        "rows": [asdict(row) for row in rows],
        "availability": availability.value,
        "relation": None if relation is None else relation.value,
    }
    eligible = bool(
        availability is EvidenceAvailabilityV2.AVAILABLE
        and relation is not None
        and privileged_reads == 0
    )
    return FutureObligationEvidenceV2(
        schema_version="driveclarify.decision_evidence.future_obligation.v2.0",
        contract_version="2.0",
        availability=availability,
        relation=relation,
        source=request.source,
        rows=rows,
        outside_local_plan_horizon_allowed=True,
        privileged_authorization_reads=privileged_reads,
        authorization_eligible=eligible,
        fresh=bool(rows and all(row.semantic_fresh for row in rows)),
        reason_codes=tuple(dict.fromkeys(reasons)),
        evidence_digest=canonical_sha256(payload),
    )


def _relationship(
    current: CurrentExecutableCoverageEvidenceV2,
    future: FutureObligationEvidenceV2,
) -> Tuple[CandidateRelationshipV2, bool]:
    if not current.authorization_eligible or not future.authorization_eligible:
        return CandidateRelationshipV2.UNKNOWN_OR_INSUFFICIENT_EVIDENCE, False
    if current.relation is CurrentExecutableRelationV2.DIVERGENT:
        return CandidateRelationshipV2.CURRENT_ACTION_DIVERGENT, True
    if (
        current.relation is CurrentExecutableRelationV2.EQUIVALENT
        and future.relation is FutureObligationRelationV2.DIVERGENT
    ):
        return CandidateRelationshipV2.CURRENTLY_EQUIVALENT_FUTURE_DIVERGENT, True
    if (
        current.relation is CurrentExecutableRelationV2.EQUIVALENT
        and future.relation is FutureObligationRelationV2.EQUIVALENT
    ):
        return CandidateRelationshipV2.CURRENT_AND_FUTURE_EQUIVALENT, True
    return CandidateRelationshipV2.UNKNOWN_OR_INSUFFICIENT_EVIDENCE, False


def _shared_lease(
    request: DecisionEvidenceInputV2,
    current: CurrentExecutableCoverageEvidenceV2,
) -> SharedActionLeaseV2:
    reasons = []
    endpoint = current.proposed_lease_end_progress_m
    recoverability = []
    for candidate_id in sorted(request.candidate_ids):
        commitment = request.candidate_commitment_progress_m.get(candidate_id)
        topology = request.candidate_topology_reachable.get(candidate_id)
        lane = request.candidate_lane_reachable.get(candidate_id)
        dynamics = request.candidate_braking_lateral_feasible.get(candidate_id)
        remaining = (
            None
            if endpoint is None or commitment is None
            else endpoint + float(request.calibrated_uncertainty_m) < float(commitment)
        )
        route_matches = request.timing_calibration.route_version == request.source.route_version
        values = (
            topology,
            lane,
            dynamics,
            remaining,
            request.hard_rule_gate,
            request.dynamic_safety_gate,
            route_matches,
            request.bundle_fresh,
        )
        known = all(value is not None for value in values)
        candidate_ok = bool(known and all(values))
        candidate_reasons = []
        if not known:
            candidate_reasons.append("RECOVERABILITY_DEPENDENCY_UNKNOWN")
        elif not candidate_ok:
            candidate_reasons.append("RECOVERABILITY_NOT_PROVEN")
        recoverability.append(
            CandidateRecoverabilityV2(
                candidate_id=candidate_id,
                lease_endpoint_progress_m=(
                    float(endpoint) if endpoint is not None else float(request.current_progress_m)
                ),
                commitment_progress_m=(None if commitment is None else float(commitment)),
                topology_reachable=topology,
                lane_reachable=lane,
                braking_lateral_feasible=dynamics,
                remaining_commitment_margin_positive=remaining,
                rule_valid=request.hard_rule_gate,
                dynamic_safety_valid=request.dynamic_safety_gate,
                route_version_matches=route_matches,
                fresh=request.bundle_fresh,
                recoverable=(candidate_ok if known else None),
                reason_codes=tuple(candidate_reasons),
            )
        )
    all_recoverable = bool(
        recoverability and all(row.recoverable is True for row in recoverability)
    )
    if not all_recoverable:
        reasons.append("SHARED_LEASE_CANDIDATE_WISE_RECOVERABILITY_UNKNOWN")
    if current.relation is not CurrentExecutableRelationV2.EQUIVALENT:
        reasons.append("SHARED_LEASE_CURRENT_ACTION_NOT_EQUIVALENT")
    eligible = bool(
        current.authorization_eligible
        and current.relation is CurrentExecutableRelationV2.EQUIVALENT
        and endpoint is not None
        and endpoint > request.current_progress_m
        and all_recoverable
        and request.dynamic_safety_gate is True
        and request.hard_rule_gate is True
    )
    availability = EvidenceAvailabilityV2.AVAILABLE if eligible else EvidenceAvailabilityV2.UNKNOWN
    valid_until = (
        request.source.observed_monotonic_time
        + float(request.next_refresh_monotonic_upper_s)
        if eligible
        else None
    )
    boundaries = {
        "local_plan_support": (
            min(current.local_plan_support_end_by_plan_m.values())
            if current.local_plan_support_end_by_plan_m
            else None
        ),
        "next_normal_planning_refresh": (
            request.current_progress_m
            + request.speed_upper_bound_mps * request.maximum_normal_planning_interval_s
        ),
        "shared_topology_corridor": (
            request.shared_topology_corridor_end_progress_m
            - request.calibrated_uncertainty_m
        ),
        "precommitment": (
            min(request.candidate_commitment_progress_m.values())
            - request.calibrated_uncertainty_m
            if request.candidate_commitment_progress_m
            else None
        ),
        "recoverability_valid_endpoint": endpoint if all_recoverable else None,
    }
    payload = {
        "source": asdict(request.source),
        "current_digest": current.evidence_digest,
        "endpoint": endpoint,
        "valid_until": valid_until,
        "boundaries": boundaries,
        "recoverability": [asdict(row) for row in recoverability],
    }
    return SharedActionLeaseV2(
        schema_version="driveclarify.decision_evidence.shared_action_lease.v2.0",
        contract_version="2.0",
        availability=availability,
        source=request.source,
        lease_id="shared-lease-v2-" + canonical_sha256(payload)[:24],
        subject_type="SHARED_EQUIVALENCE_CLASS",
        start_progress_m=float(request.current_progress_m),
        end_progress_m=endpoint if eligible else None,
        valid_from_monotonic=float(request.source.observed_monotonic_time),
        valid_until_monotonic=valid_until,
        endpoint_boundaries_m=boundaries,
        binding_boundaries=tuple(
            name for name, value in boundaries.items()
            if endpoint is not None and value is not None and abs(float(value) - float(endpoint)) <= 1e-9
        ),
        candidate_recoverability=tuple(recoverability),
        authorization_eligible=eligible,
        preserve_unresolved_semantics=True,
        reason_codes=tuple(dict.fromkeys(reasons)),
        lease_digest=canonical_sha256(payload),
    )


def _clarification(
    request: DecisionEvidenceInputV2,
    relationship: CandidateRelationshipV2,
    relationship_available: bool,
    lease: SharedActionLeaseV2,
) -> ClarificationWindowEvidenceV2:
    reasons = []
    calibration = request.timing_calibration
    calibration_ok = bool(
        calibration.runtime_config_bound
        and calibration.route_version == request.source.route_version
        and _finite(calibration.route_seconds_per_meter_lower_bound)
        and float(calibration.route_seconds_per_meter_lower_bound) > 0.0
        and _finite(calibration.simulation_to_monotonic_upper_bound)
        and float(calibration.simulation_to_monotonic_upper_bound) > 0.0
    )
    required_numeric = (
        request.answer_latency_simulation_s,
        request.post_answer_latency_monotonic_s,
    )
    if not calibration_ok:
        reasons.append("CLARIFICATION_CLOCK_CONVERSION_UNKNOWN")
    if any(not _finite(value) or float(value) < 0.0 for value in required_numeric):
        reasons.append("CLARIFICATION_LATENCY_BUDGET_UNKNOWN")
    if not relationship_available or relationship not in {
        CandidateRelationshipV2.CURRENTLY_EQUIVALENT_FUTURE_DIVERGENT,
        CandidateRelationshipV2.CURRENT_ACTION_DIVERGENT,
    }:
        reasons.append("CLARIFICATION_FUTURE_DIVERGENCE_NOT_AVAILABLE")
    commitments = request.candidate_commitment_progress_m
    if set(commitments) != set(request.candidate_ids):
        reasons.append("CLARIFICATION_COMMITMENT_KEYSET_MISMATCH")

    commitment_time = None
    start_deadline = None
    answer_deadline = None
    answer_latency_monotonic = None
    post_answer = None
    urgency = ClarificationUrgencyV2.UNKNOWN
    availability = EvidenceAvailabilityV2.UNKNOWN
    if not reasons:
        remaining_distance = (
            min(float(value) for value in commitments.values())
            - float(request.current_progress_m)
            - float(request.calibrated_uncertainty_m)
        )
        commitment_time = (
            float(request.source.observed_monotonic_time)
            + remaining_distance
            * float(calibration.route_seconds_per_meter_lower_bound)
        )
        answer_latency_monotonic = (
            float(request.answer_latency_simulation_s)
            * float(calibration.simulation_to_monotonic_upper_bound)
        )
        post_answer = float(request.post_answer_latency_monotonic_s)
        answer_deadline = commitment_time - post_answer
        start_deadline = answer_deadline - answer_latency_monotonic
        now = float(request.source.observed_monotonic_time)
        if now >= start_deadline:
            urgency = ClarificationUrgencyV2.TOO_LATE
        elif (
            lease.authorization_eligible
            and lease.valid_until_monotonic is not None
            and float(lease.valid_until_monotonic) < start_deadline
        ):
            urgency = ClarificationUrgencyV2.DEFER_CLARIFICATION
        else:
            urgency = ClarificationUrgencyV2.CLARIFY_NOW
        availability = EvidenceAvailabilityV2.AVAILABLE
    payload = {
        "source": asdict(request.source),
        "relationship": relationship.value,
        "commitment_time": commitment_time,
        "start_deadline": start_deadline,
        "answer_deadline": answer_deadline,
        "answer_latency": answer_latency_monotonic,
        "post_answer": post_answer,
        "lease_valid_until": lease.valid_until_monotonic,
        "urgency": urgency.value,
        "calibration": asdict(calibration),
    }
    eligible = bool(
        availability is EvidenceAvailabilityV2.AVAILABLE
        and urgency is not ClarificationUrgencyV2.UNKNOWN
    )
    return ClarificationWindowEvidenceV2(
        schema_version="driveclarify.decision_evidence.clarification_window.v2.0",
        contract_version="2.0",
        availability=availability,
        urgency=urgency,
        source=request.source,
        commitment_time_lower_bound_monotonic=commitment_time,
        clarification_start_deadline_monotonic=start_deadline,
        answer_deadline_monotonic=answer_deadline,
        answer_latency_upper_bound_s=answer_latency_monotonic,
        post_answer_latency_upper_bound_s=post_answer,
        shared_lease_valid_until_monotonic=lease.valid_until_monotonic,
        clock_conversion_verified=calibration_ok,
        authorization_eligible=eligible,
        reason_codes=tuple(dict.fromkeys(reasons)),
        evidence_digest=canonical_sha256(payload),
    )


def evaluate_decision_evidence_v2(request: DecisionEvidenceInputV2) -> DecisionEvidenceBundleV2:
    current = _current_executable(request)
    future = _future_obligation(request)
    relationship, relationship_available = _relationship(current, future)
    lease = _shared_lease(request, current)
    clarification = _clarification(
        request, relationship, relationship_available, lease
    )
    reasons = tuple(
        dict.fromkeys(
            current.reason_codes
            + future.reason_codes
            + lease.reason_codes
            + clarification.reason_codes
        )
    )
    eligible = bool(
        relationship_available
        and current.authorization_eligible
        and future.authorization_eligible
    )
    payload = {
        "source": asdict(request.source),
        "current": current.evidence_digest,
        "future": future.evidence_digest,
        "lease": lease.lease_digest,
        "clarification": clarification.evidence_digest,
        "relationship": relationship.value,
    }
    return DecisionEvidenceBundleV2(
        schema_version="driveclarify.decision_evidence.bundle.v2.0",
        contract_version="2.0",
        source=request.source,
        current_executable=current,
        future_obligation=future,
        shared_action_lease=lease,
        clarification_window=clarification,
        relationship=relationship,
        relationship_available=relationship_available,
        authorization_eligible=eligible,
        reason_codes=reasons,
        decision_evidence_digest=canonical_sha256(payload),
    )


__all__ = [
    "DecisionEvidenceInputV2",
    "LocalPlanV2",
    "TimingCalibrationV2",
    "evaluate_decision_evidence_v2",
]
