"""Pure minimum-sufficient-evidence evaluator for V3.1."""

from __future__ import annotations

import itertools
import math
from dataclasses import asdict, dataclass, field
from typing import Mapping, Optional, Sequence, Tuple

from driveclarify_persistent_ambiguity_runtime_v1.contracts import canonical_sha256

from .types import (
    CandidateRecoverabilityV3,
    ClarificationEvidenceV3,
    ClarificationStateV3,
    CompatibilityRelationshipV3,
    CurrentActionEvidenceV3,
    CurrentActionRelationV3,
    DecisionEvidenceBundleV3,
    EvidenceAvailabilityV3,
    FutureObligationEvidenceV3,
    FutureObligationRelationV3,
    FutureObligationRowV3,
    PreCommitmentRefreshGuaranteeV3,
    RecoverabilityEvidenceV3,
    RecoverabilityStatusV3,
    RefreshGuaranteeStateV3,
    SharedActionLeaseV3,
    SourceBindingV3,
    CONTRACT_VERSION,
)


COORDINATE_CONTRACT = "SIMLINGO_CONTROL_PID_CHECKPOINTS_1M_EGO_X_FORWARD_Y_RIGHT"


@dataclass(frozen=True)
class LocalPlanV3:
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
class TimingCalibrationV3:
    route_seconds_per_meter_lower_bound: Optional[float]
    simulation_to_monotonic_upper_bound: Optional[float]
    source_artifacts: Tuple[str, ...]
    route_version: str
    runtime_config_bound: bool


@dataclass(frozen=True)
class DecisionEvidenceInputV3:
    source: SourceBindingV3
    source_planning_event: str
    candidate_bundle_id: str
    active_member_set_digest: str
    candidate_ids: Tuple[str, ...]
    local_plans: Tuple[LocalPlanV3, ...]
    future_obligations: Tuple[FutureObligationRowV3, ...]
    current_progress_m: float
    speed_upper_bound_mps: float
    maximum_normal_planning_interval_s: float
    next_refresh_monotonic_upper_s: float
    plan_checkpoint_tolerance_m: float
    calibrated_uncertainty_m: float
    lane_clearance_m: float
    expected_route_point_count: int
    expected_control_point_count: int
    shared_executable_corridor_end_progress_m: float
    candidate_commitment_progress_m: Mapping[str, float]
    lane_action_compatible: Optional[bool]
    branch_compatible_within_lease: Optional[bool]
    candidate_physical_topology_reachable: Mapping[str, Optional[bool]]
    candidate_lane_reachable: Mapping[str, Optional[bool]]
    candidate_braking_lateral_feasible: Mapping[str, Optional[bool]]
    source_identity_aligned: bool
    runtime_route_identity_verified: bool
    bundle_complete: bool
    bundle_fresh: bool
    current_physical_safety_gate: Optional[bool]
    hard_rule_gate: Optional[bool]
    answer_latency_simulation_s: Optional[float]
    post_answer_latency_monotonic_s: Optional[float]
    timing_calibration: TimingCalibrationV3
    # V3.1 keeps live world-transform quality visible for audit, but it is not
    # an identity input to comparisons performed wholly in one model-local
    # coordinate contract.
    non_authoritative_transform_diagnostics: Mapping[str, Optional[float]] = field(
        default_factory=dict
    )


def _finite(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _arc(points: Sequence[Sequence[float]]) -> float:
    return sum(
        math.hypot(float(b[0]) - float(a[0]), float(b[1]) - float(a[1]))
        for a, b in zip(points, points[1:])
    )


def _valid_points(points: Sequence[Sequence[float]], count: int) -> bool:
    return bool(count >= 3 and len(points) == count and all(
        len(row) >= 2 and _finite(row[0]) and _finite(row[1]) for row in points
    ))


def _maximum_pairwise_deviation(plans: Sequence[LocalPlanV3], *, control: bool) -> Optional[float]:
    maximum = 0.0
    for left, right in itertools.combinations(plans, 2):
        a = left.control_points_model_local_m if control else left.route_points_model_local_m
        b = right.control_points_model_local_m if control else right.route_points_model_local_m
        if len(a) < 3 or len(b) < 3:
            return None
        maximum = max(maximum, max(
            math.hypot(float(a[i][0]) - float(b[i][0]), float(a[i][1]) - float(b[i][1]))
            for i in range(3)
        ))
    return maximum


def _current_action(request: DecisionEvidenceInputV3) -> CurrentActionEvidenceV3:
    reasons = []
    ids = tuple(sorted(request.candidate_ids))
    expected = set(ids) | {"__AUTHORIZED_PLAN__"}
    if len(ids) < 2 or len(set(ids)) != len(ids):
        reasons.append("CURRENT_ACTION_REQUIRES_DISTINCT_K2")
    if not request.bundle_complete:
        reasons.append("CURRENT_ACTION_BUNDLE_INCOMPLETE")
    if not request.bundle_fresh:
        reasons.append("CURRENT_ACTION_BUNDLE_STALE")
    plan_observation_aligned = bool(request.local_plans) and all(
        plan.source_observation_id == request.source.source_observation_id
        for plan in request.local_plans
    )
    plan_frame_aligned = bool(request.local_plans) and all(
        str(plan.source_frame_id) == str(request.source.source_frame_id)
        for plan in request.local_plans
    )
    coordinate_contract_aligned = bool(request.local_plans) and all(
        plan.coordinate_contract_id == COORDINATE_CONTRACT
        for plan in request.local_plans
    )
    source_identity_checks = {
        "adapter_bundle_source_aligned": request.source_identity_aligned,
        "source_observation_present": bool(request.source.source_observation_id),
        "source_frame_present": request.source.source_frame_id is not None,
        "route_version_present": bool(request.source.route_version),
        "environment_digest_present": bool(request.source.environment_digest),
        "runtime_observable": request.source.visibility == "RUNTIME_OBSERVABLE",
        "all_plan_observations_aligned": plan_observation_aligned,
        "all_plan_frames_aligned": plan_frame_aligned,
        "all_plan_coordinate_contracts_aligned": coordinate_contract_aligned,
    }
    if not all(value is True for value in source_identity_checks.values()):
        reasons.append("CURRENT_ACTION_SOURCE_IDENTITY_UNKNOWN")
    if {row.plan_id for row in request.local_plans} != expected:
        reasons.append("CURRENT_ACTION_PLAN_KEYSET_MISMATCH")

    numeric = (
        request.current_progress_m, request.speed_upper_bound_mps,
        request.maximum_normal_planning_interval_s, request.plan_checkpoint_tolerance_m,
        request.calibrated_uncertainty_m, request.lane_clearance_m,
        request.shared_executable_corridor_end_progress_m,
    )
    numeric_valid = all(_finite(v) for v in numeric) and all(float(v) >= 0.0 for v in numeric[1:])
    if not numeric_valid:
        reasons.append("CURRENT_ACTION_NUMERIC_INPUT_INVALID")

    support = {}
    representations_valid = numeric_valid
    for plan in request.local_plans:
        valid = bool(
            _valid_points(plan.route_points_model_local_m, request.expected_route_point_count)
            and _valid_points(plan.control_points_model_local_m, request.expected_control_point_count)
            and plan.coordinate_contract_id == COORDINATE_CONTRACT
            and math.hypot(*plan.route_points_model_local_m[0]) <= request.plan_checkpoint_tolerance_m
            and math.hypot(*plan.control_points_model_local_m[0]) <= request.plan_checkpoint_tolerance_m
            and plan.source_observation_id == request.source.source_observation_id
            and str(plan.source_frame_id) == str(request.source.source_frame_id)
            and plan.fresh and bool(plan.route_digest) and bool(plan.control_digest)
        )
        if not valid:
            representations_valid = False
            reasons.append("CURRENT_ACTION_PLAN_INVALID:" + plan.plan_id)
            continue
        support[plan.plan_id] = (
            request.current_progress_m + _arc(plan.route_points_model_local_m)
            - request.calibrated_uncertainty_m - request.plan_checkpoint_tolerance_m
        )

    geometry = _maximum_pairwise_deviation(request.local_plans, control=False) if representations_valid and len(request.local_plans) >= 3 else None
    control = _maximum_pairwise_deviation(request.local_plans, control=True) if representations_valid and len(request.local_plans) >= 3 else None
    compatible = None if geometry is None or control is None else bool(
        geometry + request.calibrated_uncertainty_m < request.lane_clearance_m
        and control <= request.calibrated_uncertainty_m
    )
    if compatible is None:
        reasons.append("CURRENT_ACTION_GEOMETRY_OR_CONTROL_UNKNOWN")

    commitment_lower = None
    if set(request.candidate_commitment_progress_m) == set(ids) and all(
        _finite(value) for value in request.candidate_commitment_progress_m.values()
    ):
        commitment_lower = min(float(value) for value in request.candidate_commitment_progress_m.values()) - request.calibrated_uncertainty_m
    else:
        reasons.append("CURRENT_ACTION_COMMITMENT_KEYSET_OR_VALUE_UNKNOWN")
    boundaries = {
        "local_plan_support": min(support.values()) if len(support) == len(expected) else None,
        "shared_executable_corridor": request.shared_executable_corridor_end_progress_m - request.calibrated_uncertainty_m if numeric_valid else None,
        "next_mandatory_normal_refresh": request.current_progress_m + request.speed_upper_bound_mps * request.maximum_normal_planning_interval_s + request.calibrated_uncertainty_m if numeric_valid else None,
        "earliest_candidate_commitment_lower": commitment_lower,
    }
    endpoint = min(boundaries.values()) if all(value is not None for value in boundaries.values()) else None
    local_coverage = bool(endpoint is not None and endpoint > request.current_progress_m + request.plan_checkpoint_tolerance_m)
    if not local_coverage:
        reasons.append("CURRENT_ACTION_NONEMPTY_BOUNDED_LEASE_UNAVAILABLE")
    if request.lane_action_compatible is None:
        reasons.append("CURRENT_ACTION_LANE_ACTION_UNKNOWN")
    if request.branch_compatible_within_lease is None:
        reasons.append("CURRENT_ACTION_BRANCH_COMPATIBILITY_UNKNOWN")

    available_inputs = bool(
        representations_valid and geometry is not None and control is not None
        and request.lane_action_compatible is not None
        and request.branch_compatible_within_lease is not None
        and request.bundle_complete and request.bundle_fresh
        and all(value is True for value in source_identity_checks.values())
    )
    relation = CurrentActionRelationV3.UNKNOWN
    availability = EvidenceAvailabilityV3.UNKNOWN
    if available_inputs:
        availability = EvidenceAvailabilityV3.AVAILABLE
        if request.lane_action_compatible is False or request.branch_compatible_within_lease is False or compatible is False:
            relation = CurrentActionRelationV3.DIVERGENT
        elif local_coverage:
            relation = CurrentActionRelationV3.SHARED
        else:
            availability = EvidenceAvailabilityV3.UNKNOWN

    eligible = bool(availability is EvidenceAvailabilityV3.AVAILABLE and relation is not CurrentActionRelationV3.UNKNOWN)
    payload = {
        "source": asdict(request.source), "ids": ids, "bundle": request.candidate_bundle_id,
        "support": support, "boundaries": boundaries, "geometry": geometry,
        "control": control, "lane": request.lane_action_compatible,
        "branch": request.branch_compatible_within_lease, "relation": relation.value,
        "source_identity_checks": source_identity_checks,
        "non_authoritative_transform_diagnostics": dict(
            request.non_authoritative_transform_diagnostics
        ),
    }
    return CurrentActionEvidenceV3(
        availability=availability, relation=relation, source=request.source,
        candidate_ids=ids, candidate_bundle_id=request.candidate_bundle_id,
        active_member_set_digest=request.active_member_set_digest,
        lease_start_progress_m=float(request.current_progress_m),
        proposed_lease_end_progress_m=endpoint,
        local_plan_support_end_by_plan_m=support, endpoint_boundaries_m=boundaries,
        maximum_fixed_time_geometry_deviation_m=geometry,
        maximum_fixed_time_control_deviation_m=control,
        lane_action_compatible=request.lane_action_compatible,
        branch_compatible_within_lease=request.branch_compatible_within_lease,
        geometry_control_compatible=compatible, local_coverage_available=local_coverage,
        source_identity_checks=source_identity_checks,
        non_authoritative_transform_diagnostics=dict(
            request.non_authoritative_transform_diagnostics
        ),
        authorization_eligible=eligible, reason_codes=tuple(dict.fromkeys(reasons)),
        evidence_digest=canonical_sha256(payload),
    )


def _common_continuation(rows: Sequence[FutureObligationRowV3], ids: Sequence[str], source: SourceBindingV3) -> bool:
    if not rows or {row.candidate_id for row in rows} != set(ids):
        return False
    signatures = {
        (row.obligation_digest, row.obligation_type, row.maneuver, row.event_relation,
         row.target_id, row.junction_id, row.branch_id, row.route_order_index)
        for row in rows
    }
    return bool(len(signatures) == 1 and all(
        row.active_unresolved and row.semantic_fresh and not row.privileged
        and row.authorization_purpose and row.visibility == "RUNTIME_OBSERVABLE"
        and row.route_version == source.route_version
        for row in rows
    ))


def _future_obligation(request: DecisionEvidenceInputV3) -> FutureObligationEvidenceV3:
    rows = tuple(sorted(request.future_obligations, key=lambda row: row.candidate_id))
    ids = tuple(sorted(request.candidate_ids))
    reasons = []
    if tuple(row.candidate_id for row in rows) != ids:
        reasons.append("FUTURE_OBLIGATION_CANDIDATE_KEYSET_MISMATCH")
    privileged = sum(1 for row in rows if row.privileged)
    required_provenance = {"RUNTIME_RGB_0_GROUNDING", "LIVE_CARLA_HD_MAP_TOPOLOGY"}
    for row in rows:
        if row.route_version != request.source.route_version:
            reasons.append("FUTURE_OBLIGATION_ROUTE_VERSION_MISMATCH:" + row.candidate_id)
        if not row.active_unresolved or not row.semantic_fresh:
            reasons.append("FUTURE_OBLIGATION_INACTIVE_OR_STALE:" + row.candidate_id)
        if not row.topology_binding_available:
            reasons.append("FUTURE_OBLIGATION_TOPOLOGY_BINDING_UNKNOWN:" + row.candidate_id)
        if row.visibility != "RUNTIME_OBSERVABLE" or row.privileged or not row.authorization_purpose:
            reasons.append("FUTURE_OBLIGATION_AUTHORIZATION_SOURCE_INVALID:" + row.candidate_id)
        if not required_provenance.issubset(set(row.source_kinds)):
            reasons.append("FUTURE_OBLIGATION_RUNTIME_PROVENANCE_INCOMPLETE:" + row.candidate_id)
    full_binding = bool(rows and not reasons)
    relation = FutureObligationRelationV3.UNKNOWN
    availability = EvidenceAvailabilityV3.UNKNOWN
    if full_binding:
        availability = EvidenceAvailabilityV3.AVAILABLE
        relation = FutureObligationRelationV3.DIVERGENT if len({row.obligation_digest for row in rows}) >= 2 else FutureObligationRelationV3.EQUIVALENT
    common = _common_continuation(rows, ids, request.source)
    payload = {"source": asdict(request.source), "rows": [asdict(row) for row in rows], "relation": relation.value, "common": common}
    return FutureObligationEvidenceV3(
        availability=availability, relation=relation, source=request.source, rows=rows,
        common_continuation_authorized=common,
        privileged_authorization_reads=privileged,
        authorization_eligible=full_binding and privileged == 0,
        reason_codes=tuple(dict.fromkeys(reasons)), evidence_digest=canonical_sha256(payload),
    )


def _refresh_guarantee(request: DecisionEvidenceInputV3) -> PreCommitmentRefreshGuaranteeV3:
    reasons = []
    ids = set(request.candidate_ids)
    inputs_valid = bool(
        _finite(request.current_progress_m) and _finite(request.speed_upper_bound_mps)
        and request.speed_upper_bound_mps >= 0.0
        and _finite(request.maximum_normal_planning_interval_s)
        and request.maximum_normal_planning_interval_s > 0.0
        and _finite(request.calibrated_uncertainty_m)
        and request.calibrated_uncertainty_m >= 0.0
        and set(request.candidate_commitment_progress_m) == ids
        and all(_finite(value) for value in request.candidate_commitment_progress_m.values())
        and request.bundle_fresh and request.runtime_route_identity_verified
    )
    if not inputs_valid:
        reasons.append("PRECOMMITMENT_REFRESH_DEPENDENCY_UNKNOWN")
    next_progress = None
    commitment_lower = None
    strict = None
    state = RefreshGuaranteeStateV3.UNKNOWN
    if inputs_valid:
        next_progress = request.current_progress_m + request.speed_upper_bound_mps * request.maximum_normal_planning_interval_s + request.calibrated_uncertainty_m
        commitment_lower = min(float(value) for value in request.candidate_commitment_progress_m.values()) - request.calibrated_uncertainty_m
        strict = next_progress < commitment_lower
        state = RefreshGuaranteeStateV3.GUARANTEED if strict else RefreshGuaranteeStateV3.NOT_GUARANTEED
        if not strict:
            reasons.append("NEXT_REFRESH_NOT_STRICTLY_BEFORE_EARLIEST_COMMITMENT")
    payload = {"p": request.current_progress_m, "v": request.speed_upper_bound_mps, "t": request.maximum_normal_planning_interval_s, "u": request.calibrated_uncertainty_m, "next": next_progress, "commitment": commitment_lower, "state": state.value}
    return PreCommitmentRefreshGuaranteeV3(
        state=state, current_progress_m=float(request.current_progress_m),
        next_refresh_progress_upper_m=next_progress,
        earliest_commitment_lower_m=commitment_lower,
        maximum_normal_planning_interval_s=request.maximum_normal_planning_interval_s,
        speed_upper_bound_mps=request.speed_upper_bound_mps,
        calibrated_uncertainty_m=request.calibrated_uncertainty_m,
        strict_before_commitment=strict, source_runtime_config_bound=inputs_valid,
        authorization_eligible=state is RefreshGuaranteeStateV3.GUARANTEED,
        reason_codes=tuple(reasons), evidence_digest=canonical_sha256(payload),
    )


def _recoverability(request: DecisionEvidenceInputV3, current: CurrentActionEvidenceV3, future: FutureObligationEvidenceV3, refresh: PreCommitmentRefreshGuaranteeV3) -> RecoverabilityEvidenceV3:
    ids = tuple(sorted(request.candidate_ids))
    rows_by_id = {row.candidate_id: row for row in future.rows}
    map_keysets = (
        set(request.candidate_physical_topology_reachable), set(request.candidate_lane_reachable),
        set(request.candidate_braking_lateral_feasible), set(request.candidate_commitment_progress_m),
    )
    keyset_complete = all(value == set(ids) for value in map_keysets) and set(rows_by_id) == set(ids)
    result = []
    for candidate_id in ids:
        obligation = rows_by_id.get(candidate_id)
        semantic_route = None if obligation is None else bool(
            obligation.topology_binding_available or future.common_continuation_authorized
        )
        topology = request.candidate_physical_topology_reachable.get(candidate_id) if semantic_route else None
        lane = request.candidate_lane_reachable.get(candidate_id) if semantic_route else None
        dynamics = request.candidate_braking_lateral_feasible.get(candidate_id) if semantic_route else None
        commitment = request.candidate_commitment_progress_m.get(candidate_id)
        endpoint = current.proposed_lease_end_progress_m
        remaining = None if endpoint is None or commitment is None or not _finite(commitment) else endpoint + request.calibrated_uncertainty_m < float(commitment)
        values = (
            topology, lane, dynamics, remaining, request.hard_rule_gate,
            request.current_physical_safety_gate,
            refresh.state is RefreshGuaranteeStateV3.GUARANTEED,
            request.runtime_route_identity_verified, semantic_route,
            request.bundle_fresh,
        )
        known = keyset_complete and all(value is not None for value in values)
        ok = bool(known and all(values))
        status = RecoverabilityStatusV3.RECOVERABLE if ok else RecoverabilityStatusV3.NOT_RECOVERABLE if known else RecoverabilityStatusV3.UNKNOWN
        reasons = () if ok else ("RECOVERABILITY_NOT_PROVEN",) if known else ("RECOVERABILITY_DEPENDENCY_UNKNOWN",)
        result.append(CandidateRecoverabilityV3(
            candidate_id=candidate_id, lease_endpoint_progress_m=endpoint,
            commitment_progress_m=None if commitment is None else float(commitment),
            topology_reachable=topology, lane_reachable=lane,
            braking_lateral_feasible=dynamics,
            remaining_commitment_margin_positive=remaining,
            rule_valid=request.hard_rule_gate,
            current_physical_safety_valid=request.current_physical_safety_gate,
            latency_budget_valid=refresh.state is RefreshGuaranteeStateV3.GUARANTEED,
            route_identity_verified=request.runtime_route_identity_verified,
            semantic_route_binding_sufficient=semantic_route, fresh=request.bundle_fresh,
            status=status, reason_codes=reasons,
        ))
    all_ok = bool(result and all(row.status is RecoverabilityStatusV3.RECOVERABLE for row in result))
    aggregate = RecoverabilityStatusV3.RECOVERABLE if all_ok else RecoverabilityStatusV3.NOT_RECOVERABLE if result and all(row.status is not RecoverabilityStatusV3.UNKNOWN for row in result) else RecoverabilityStatusV3.UNKNOWN
    reasons = () if all_ok else ("ALL_CANDIDATE_RECOVERABILITY_NOT_PROVEN",)
    payload = {"rows": [asdict(row) for row in result], "keyset": keyset_complete, "status": aggregate.value}
    return RecoverabilityEvidenceV3(
        status=aggregate, candidate_rows=tuple(result),
        active_candidate_keyset_complete=keyset_complete,
        all_candidates_recoverable=all_ok, authorization_eligible=all_ok,
        reason_codes=reasons, evidence_digest=canonical_sha256(payload),
    )


def _lease(request: DecisionEvidenceInputV3, current: CurrentActionEvidenceV3, recoverability: RecoverabilityEvidenceV3, refresh: PreCommitmentRefreshGuaranteeV3) -> SharedActionLeaseV3:
    reasons = []
    boundaries = dict(current.endpoint_boundaries_m)
    boundaries["recoverability_valid_endpoint"] = current.proposed_lease_end_progress_m if recoverability.all_candidates_recoverable else None
    endpoint = min(boundaries.values()) if all(value is not None for value in boundaries.values()) else None
    expiry = request.source.observed_monotonic_time + request.next_refresh_monotonic_upper_s if _finite(request.next_refresh_monotonic_upper_s) and request.next_refresh_monotonic_upper_s > 0.0 else None
    valid = bool(
        current.relation is CurrentActionRelationV3.SHARED and current.authorization_eligible
        and endpoint is not None and endpoint > request.current_progress_m + request.plan_checkpoint_tolerance_m
        and recoverability.all_candidates_recoverable
        and refresh.state is RefreshGuaranteeStateV3.GUARANTEED
        and request.current_physical_safety_gate is True and request.hard_rule_gate is True
        and expiry is not None
    )
    if current.relation is not CurrentActionRelationV3.SHARED:
        reasons.append("LEASE_CURRENT_ACTION_NOT_SHARED")
    if not recoverability.all_candidates_recoverable:
        reasons.append("LEASE_ALL_CANDIDATE_RECOVERABILITY_NOT_PROVEN")
    if refresh.state is not RefreshGuaranteeStateV3.GUARANTEED:
        reasons.append("LEASE_PRECOMMITMENT_REFRESH_NOT_GUARANTEED")
    if endpoint is None or endpoint <= request.current_progress_m + request.plan_checkpoint_tolerance_m:
        reasons.append("LEASE_NONEMPTY_BOUND_UNKNOWN")
    if expiry is None:
        reasons.append("LEASE_MONOTONIC_EXPIRY_UNKNOWN")
    if request.current_physical_safety_gate is not True or request.hard_rule_gate is not True:
        reasons.append("LEASE_HARD_GATE_NOT_PASSED")
    binding = tuple(name for name, value in boundaries.items() if endpoint is not None and value is not None and abs(float(value) - float(endpoint)) <= 1e-9)
    payload = {"source": asdict(request.source), "boundaries": boundaries, "endpoint": endpoint, "expiry": expiry, "valid": valid}
    digest = canonical_sha256(payload)
    return SharedActionLeaseV3(
        valid=valid, source=request.source, lease_id="shared-lease-v3-" + digest[:24],
        subject_type="SHARED_EQUIVALENCE_CLASS",
        lease_start_progress_m=float(request.current_progress_m),
        lease_end_progress_m=endpoint if valid else None,
        lease_expiry_monotonic=expiry if valid else None,
        source_planning_event=request.source_planning_event,
        candidate_bundle_id=request.candidate_bundle_id,
        active_member_set_digest=request.active_member_set_digest,
        endpoint_boundaries_m=boundaries, binding_boundaries=binding,
        lease_reason=binding[0] if valid and binding else "LEASE_INVALID",
        preserve_unresolved_semantics=True, authorization_eligible=valid,
        reason_codes=tuple(dict.fromkeys(reasons)), lease_digest=digest,
    )


def _clarification(request: DecisionEvidenceInputV3, future: FutureObligationEvidenceV3, lease: SharedActionLeaseV3, refresh: PreCommitmentRefreshGuaranteeV3) -> ClarificationEvidenceV3:
    reasons = []
    state = ClarificationStateV3.UNKNOWN
    commitment_time = start_deadline = answer_deadline = answer_mono = post_answer = None
    bounded_unknown = bool(
        lease.valid and refresh.state is RefreshGuaranteeStateV3.GUARANTEED
        and lease.lease_end_progress_m is not None
        and refresh.earliest_commitment_lower_m is not None
        and lease.lease_end_progress_m < refresh.earliest_commitment_lower_m
    )
    if future.relation is FutureObligationRelationV3.EQUIVALENT:
        state = ClarificationStateV3.NOT_NEEDED_YET
    elif future.relation is FutureObligationRelationV3.UNKNOWN:
        if bounded_unknown:
            state = ClarificationStateV3.NOT_NEEDED_YET
        else:
            reasons.append("CLARIFICATION_FUTURE_AND_TIMING_UNKNOWN")
    else:
        calibration = request.timing_calibration
        calibration_ok = bool(
            calibration.runtime_config_bound and calibration.route_version == request.source.route_version
            and _finite(calibration.route_seconds_per_meter_lower_bound)
            and calibration.route_seconds_per_meter_lower_bound > 0.0
            and _finite(calibration.simulation_to_monotonic_upper_bound)
            and calibration.simulation_to_monotonic_upper_bound > 0.0
            and _finite(request.answer_latency_simulation_s)
            and request.answer_latency_simulation_s >= 0.0
            and _finite(request.post_answer_latency_monotonic_s)
            and request.post_answer_latency_monotonic_s >= 0.0
            and refresh.earliest_commitment_lower_m is not None
        )
        if calibration_ok:
            remaining = refresh.earliest_commitment_lower_m - request.current_progress_m
            commitment_time = request.source.observed_monotonic_time + remaining * float(calibration.route_seconds_per_meter_lower_bound)
            answer_mono = float(request.answer_latency_simulation_s) * float(calibration.simulation_to_monotonic_upper_bound)
            post_answer = float(request.post_answer_latency_monotonic_s)
            answer_deadline = commitment_time - post_answer
            start_deadline = answer_deadline - answer_mono
            now = request.source.observed_monotonic_time
            if now >= start_deadline:
                state = ClarificationStateV3.TOO_LATE
            elif lease.valid and lease.lease_expiry_monotonic is not None and lease.lease_expiry_monotonic < start_deadline:
                state = ClarificationStateV3.NOT_NEEDED_YET
            else:
                state = ClarificationStateV3.CLARIFY_NOW
        else:
            reasons.append("CLARIFICATION_CLOCK_OR_LATENCY_EVIDENCE_UNKNOWN")
    payload = {"future": future.relation.value, "state": state.value, "commitment": commitment_time, "start": start_deadline, "answer": answer_deadline, "bounded_unknown": bounded_unknown}
    return ClarificationEvidenceV3(
        state=state, source=request.source,
        commitment_time_lower_bound_monotonic=commitment_time,
        clarification_start_deadline_monotonic=start_deadline,
        answer_deadline_monotonic=answer_deadline,
        answer_latency_upper_bound_s=answer_mono,
        post_answer_latency_upper_bound_s=post_answer,
        bounded_unknown_safe_until_refresh=bounded_unknown,
        authorization_eligible=state is not ClarificationStateV3.UNKNOWN or bounded_unknown,
        reason_codes=tuple(reasons), evidence_digest=canonical_sha256(payload),
    )


def _compatibility(current: CurrentActionRelationV3, future: FutureObligationRelationV3) -> CompatibilityRelationshipV3:
    if current is CurrentActionRelationV3.DIVERGENT:
        return CompatibilityRelationshipV3.CURRENT_ACTION_DIVERGENT
    if current is CurrentActionRelationV3.SHARED and future is FutureObligationRelationV3.DIVERGENT:
        return CompatibilityRelationshipV3.CURRENTLY_EQUIVALENT_FUTURE_DIVERGENT
    if current is CurrentActionRelationV3.SHARED and future is FutureObligationRelationV3.EQUIVALENT:
        return CompatibilityRelationshipV3.CURRENT_AND_FUTURE_EQUIVALENT
    if current is CurrentActionRelationV3.SHARED:
        return CompatibilityRelationshipV3.CURRENT_ACTION_SHARED_FUTURE_UNKNOWN
    return CompatibilityRelationshipV3.UNKNOWN_OR_INSUFFICIENT_EVIDENCE


def evaluate_decision_evidence_v3(request: DecisionEvidenceInputV3) -> DecisionEvidenceBundleV3:
    current = _current_action(request)
    future = _future_obligation(request)
    refresh = _refresh_guarantee(request)
    recoverability = _recoverability(request, current, future, refresh)
    lease = _lease(request, current, recoverability, refresh)
    clarification = _clarification(request, future, lease, refresh)
    relationship = _compatibility(current.relation, future.relation)
    reasons = tuple(dict.fromkeys(
        current.reason_codes + future.reason_codes + refresh.reason_codes
        + recoverability.reason_codes + lease.reason_codes + clarification.reason_codes
    ))
    payload = {
        "source": asdict(request.source), "current": current.evidence_digest,
        "future": future.evidence_digest, "refresh": refresh.evidence_digest,
        "recoverability": recoverability.evidence_digest, "lease": lease.lease_digest,
        "clarification": clarification.evidence_digest, "compatibility": relationship.value,
    }
    return DecisionEvidenceBundleV3(
        schema_version="driveclarify.decision_evidence.bundle.v3.1",
        contract_version=CONTRACT_VERSION, source=request.source, current_action=current,
        future_obligation=future, clarification=clarification,
        shared_action_lease=lease, refresh_guarantee=refresh,
        recoverability=recoverability, compatibility_relationship=relationship,
        authorization_eligible=bool(request.source.visibility == "RUNTIME_OBSERVABLE"),
        reason_codes=reasons, decision_evidence_digest=canonical_sha256(payload),
    )


__all__ = ["DecisionEvidenceInputV3", "LocalPlanV3", "TimingCalibrationV3", "evaluate_decision_evidence_v3"]
