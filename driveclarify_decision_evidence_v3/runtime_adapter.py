"""Production adapter for Decision Evidence Architecture V3.1.

Consumes only the existing normal-planning baseline, same-frame candidate
bundle, persistent semantic identities and live CARLA observation.  It has no
model, planner, PID or control-write API.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence, Tuple

from driveclarify_decision_evidence_v2.runtime_adapter import (
    load_phase_b_timing_calibration_v2,
)
from driveclarify_persistent_ambiguity_runtime_v1.runtime_evaluator import (
    RuntimeWindowObservation,
    _route_points,
    _speed_points,
)

from .evaluator import (
    DecisionEvidenceInputV3,
    LocalPlanV3,
    TimingCalibrationV3,
    evaluate_decision_evidence_v3,
)
from .types import (
    ClarificationStateV3,
    CurrentActionRelationV3,
    DecisionEvidenceBundleV3,
    FutureObligationRelationV3,
    FutureObligationRowV3,
    RecoverabilityStatusV3,
    SourceBindingV3,
)


def _points_tuple(value: Optional[Sequence[Sequence[float]]]) -> Tuple[Tuple[float, float], ...]:
    if value is None:
        return ()
    try:
        return tuple((float(row[0]), float(row[1])) for row in value)
    except (IndexError, TypeError, ValueError):
        return ()


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _future_rows(
    *,
    episode: Any,
    connectors: Sequence[Mapping[str, Any]],
    source: SourceBindingV3,
    qualified_obligations: Optional[Mapping[str, Any]] = None,
) -> Tuple[FutureObligationRowV3, ...]:
    connector_by_id = {
        str(row.get("candidate_id")): row for row in connectors
        if row.get("candidate_id") is not None
    }
    rows = []
    for candidate in episode.candidates:
        if candidate.candidate_id not in episode.active_candidate_ids:
            continue
        connector = connector_by_id.get(candidate.candidate_id, {})
        obligation = (
            None
            if qualified_obligations is None
            else qualified_obligations.get(candidate.candidate_id)
        )
        if obligation is not None:
            qualification = _enum_value(
                getattr(obligation, "qualification_status", "UNKNOWN")
            )
            recoverability = _enum_value(
                getattr(obligation, "global_goal_recoverability", "UNKNOWN")
            )
            segment = tuple(getattr(obligation, "local_route_segment", ()) or ())
            physical = bool(
                qualification == "QUALIFIED"
                and recoverability == "RECOVERABLE_TO_GLOBAL_GOAL"
                and len(segment) >= 2
            )
            semantic_binding = bool(
                physical
                and str(getattr(obligation, "candidate_id", ""))
                == candidate.candidate_id
                and str(getattr(obligation, "interpretation_id", ""))
                == candidate.interpretation_id
                and str(getattr(obligation, "source_observation_id", ""))
                == candidate.source_observation_id
                and bool(str(getattr(obligation, "local_branch_identity", "")))
                and bool(str(getattr(obligation, "obligation_digest", "")))
            )
            obligation_digest = str(
                getattr(obligation, "obligation_digest", "")
            )
            obligation_type = str(
                getattr(obligation, "maneuver_family", candidate.obligation_type)
            )
            maneuver = str(
                getattr(obligation, "maneuver_direction", candidate.maneuver)
            )
            target_id = str(
                getattr(obligation, "target_digest", candidate.topology_target_id)
            )
            branch_id = str(
                getattr(
                    obligation,
                    "local_branch_identity",
                    candidate.topology_branch_id,
                )
            )
        else:
            physical = bool(
                connector.get("exit_reached") is True
                and connector.get("live_map_pair_match") is True
            )
            semantic_binding = bool(
                connector.get("status") == "AVAILABLE"
                and physical
                and str(connector.get("branch_id")) == candidate.topology_branch_id
                and str(connector.get("junction_id")) == candidate.topology_junction_id
            )
            obligation_digest = candidate.target_obligation_digest
            obligation_type = candidate.obligation_type
            maneuver = candidate.maneuver
            target_id = candidate.topology_target_id
            branch_id = candidate.topology_branch_id
        rows.append(FutureObligationRowV3(
            candidate_id=candidate.candidate_id,
            interpretation_id=candidate.interpretation_id,
            semantic_sha256=candidate.semantic_sha256,
            obligation_digest=obligation_digest,
            obligation_type=obligation_type,
            maneuver=maneuver,
            event_relation=candidate.event_relation,
            referent_lineage_id=candidate.referent_lineage_id,
            referent_description=candidate.referent_description,
            target_id=target_id,
            junction_id=candidate.topology_junction_id,
            branch_id=branch_id,
            route_order_index=int(candidate.topology_route_order),
            route_version=source.route_version,
            source_observation_id=candidate.source_observation_id,
            source_frame_id=candidate.source_frame_id,
            source_kinds=tuple(candidate.provenance),
            active_unresolved=True,
            semantic_fresh=True,
            topology_binding_available=semantic_binding,
            physical_connector_available=physical,
            visibility="RUNTIME_OBSERVABLE",
            privileged=False,
            authorization_purpose=True,
        ))
    return tuple(rows)


@dataclass(frozen=True)
class RuntimeDecisionWindowV3:
    status: str
    source_observation_id: str
    source_frame_id: Any
    route_version: str
    environment_digest: str
    current_progress_m: float
    shared_action_end_progress_m: Optional[float]
    current_action_relation: str
    future_obligation_relation: str
    clarification_state: str
    precommitment_refresh_guarantee: str
    candidate_relationship: str
    full_plan_coverage: bool
    current_executable_coverage: bool
    recoverability: str
    recoverability_by_candidate: Mapping[str, str]
    latest_safe_clarification_monotonic: Optional[float]
    answer_deadline_monotonic: Optional[float]
    latest_safe_slack_s: Optional[float]
    time_to_divergence_lower_bound_s: Optional[float]
    decision_window_digest: str
    current_action_equivalence_evidence_digest: str
    future_obligation_evidence_digest: str
    refresh_guarantee_evidence_digest: str
    recoverability_evidence_digest: str
    valid_until_monotonic: Optional[float]
    plan_coverage_evidence: Mapping[str, Any]
    reason_codes: Tuple[str, ...]
    v3_bundle: DecisionEvidenceBundleV3

    def to_dict(self) -> dict[str, Any]:
        value = dict(self.__dict__)
        value["v3_bundle"] = self.v3_bundle.to_dict()
        return value


def evaluate_runtime_decision_evidence_v3(
    *, bundle: Any, observation: RuntimeWindowObservation, episode: Any,
    connectors: Sequence[Mapping[str, Any]],
    qualified_obligations: Optional[Mapping[str, Any]] = None,
    authorized_plan_points: Sequence[Sequence[float]], authorized_plan_digest: str,
    authorized_speed_points: Sequence[Sequence[float]], authorized_speed_digest: str,
    answer_latency_simulation_s: float, full_plan_coverage_v1: bool,
) -> RuntimeDecisionWindowV3:
    source = SourceBindingV3(
        source_observation_id=observation.source_observation_id,
        source_frame_id=observation.source_frame_id,
        route_version=observation.route_version,
        environment_digest=observation.environment_digest,
        observed_monotonic_time=observation.observed_monotonic_time,
    )
    ids = tuple(sorted(str(value) for value in bundle.requested_candidate_ids))
    plans = [
        LocalPlanV3(
            plan_id=str(row.candidate_id), candidate_id=str(row.candidate_id),
            source_observation_id=str(row.source_observation_id),
            source_frame_id=row.source_frame_id,
            route_points_model_local_m=_points_tuple(_route_points(row.result)),
            control_points_model_local_m=_points_tuple(_speed_points(row.result)),
            route_digest=str(row.route_digest), control_digest=str(row.speed_digest),
            coordinate_contract_id=observation.model_coordinate_contract_id, fresh=True,
        )
        for row in bundle.evidence
    ]
    plans.append(LocalPlanV3(
        plan_id="__AUTHORIZED_PLAN__", candidate_id=None,
        source_observation_id=observation.source_observation_id,
        source_frame_id=observation.source_frame_id,
        route_points_model_local_m=_points_tuple(authorized_plan_points),
        control_points_model_local_m=_points_tuple(authorized_speed_points),
        route_digest=authorized_plan_digest, control_digest=authorized_speed_digest,
        coordinate_contract_id=observation.model_coordinate_contract_id, fresh=True,
    ))
    future_rows = _future_rows(
        episode=episode,
        connectors=connectors,
        source=source,
        qualified_obligations=qualified_obligations,
    )
    physical = {
        row.candidate_id: row.physical_connector_available for row in future_rows
    }
    lane_local = bool(
        plans and all(
            len(plan.route_points_model_local_m) >= 3
            and all(
                abs(float(point[1])) + observation.calibrated_uncertainty_m
                < observation.lane_clearance_m
                for point in plan.route_points_model_local_m[:3]
            )
            for plan in plans
        )
        and observation.current_progress_m + observation.calibrated_uncertainty_m
        < observation.maneuver_onset_progress_m
    )
    # V3.1 source identity is the identity of plans already represented in the
    # same model-local contract.  The CARLA world-basis projection is retained
    # below as a diagnostic, but it is not a transformation used by this
    # pairwise comparison and therefore cannot establish or revoke identity.
    source_aligned = bool(
        bundle.source_observation_id == observation.source_observation_id
        and str(bundle.source_frame_id) == str(observation.source_frame_id)
        and observation.route_version and observation.environment_digest
    )
    bundle_fresh = bool(
        bundle.complete
        and bundle.latency_seconds <= observation.runtime_latency_upper_bound_s
    )
    old_timing = load_phase_b_timing_calibration_v2(route_version=observation.route_version)
    timing = TimingCalibrationV3(
        route_seconds_per_meter_lower_bound=old_timing.route_seconds_per_meter_lower_bound,
        simulation_to_monotonic_upper_bound=old_timing.simulation_to_monotonic_upper_bound,
        source_artifacts=old_timing.source_artifacts,
        route_version=old_timing.route_version,
        runtime_config_bound=old_timing.runtime_config_bound,
    )
    physical_safety = getattr(observation, "current_physical_safety_gate", None)
    request = DecisionEvidenceInputV3(
        source=source, source_planning_event=str(bundle.normal_planning_event_id),
        candidate_bundle_id=bundle.bundle_id,
        active_member_set_digest=bundle.candidate_set_digest, candidate_ids=ids,
        local_plans=tuple(plans), future_obligations=future_rows,
        current_progress_m=observation.current_progress_m,
        speed_upper_bound_mps=max(observation.current_speed_mps, observation.speed_limit_mps),
        maximum_normal_planning_interval_s=observation.maximum_normal_planning_interval_s,
        next_refresh_monotonic_upper_s=observation.runtime_latency_upper_bound_s,
        plan_checkpoint_tolerance_m=observation.plan_checkpoint_spacing_tolerance_m,
        calibrated_uncertainty_m=observation.calibrated_uncertainty_m,
        lane_clearance_m=observation.lane_clearance_m,
        expected_route_point_count=observation.expected_plan_point_count,
        expected_control_point_count=10,
        shared_executable_corridor_end_progress_m=observation.maneuver_onset_progress_m,
        candidate_commitment_progress_m=dict(observation.candidate_commitment_progress_m),
        lane_action_compatible=lane_local, branch_compatible_within_lease=lane_local,
        candidate_physical_topology_reachable=physical,
        candidate_lane_reachable=dict(physical),
        candidate_braking_lateral_feasible={
            candidate_id: bool(
                physical.get(candidate_id) is True
                and physical_safety is True and observation.hard_rule_gate
            )
            for candidate_id in ids
        },
        source_identity_aligned=source_aligned,
        runtime_route_identity_verified=bool(observation.route_version and observation.environment_digest),
        bundle_complete=bool(bundle.complete), bundle_fresh=bundle_fresh,
        current_physical_safety_gate=physical_safety,
        hard_rule_gate=observation.hard_rule_gate,
        answer_latency_simulation_s=answer_latency_simulation_s,
        post_answer_latency_monotonic_s=observation.runtime_latency_upper_bound_s,
        timing_calibration=timing,
        non_authoritative_transform_diagnostics={
            "planar_basis_error": getattr(
                observation, "model_local_planar_basis_error", None
            ),
            "historical_planar_basis_error_upper": getattr(
                observation, "model_local_planar_basis_error_upper", None
            ),
            "route_projection_error_m": getattr(
                observation, "route_projection_error_m", None
            ),
            "legacy_model_local_transform_verified": getattr(
                observation, "model_local_transform_verified", None
            ),
        },
    )
    evidence = evaluate_decision_evidence_v3(request)
    clarification = evidence.clarification
    now = observation.observed_monotonic_time
    commitment_time = clarification.commitment_time_lower_bound_monotonic
    return RuntimeDecisionWindowV3(
        status="AVAILABLE" if evidence.authorization_eligible else "UNKNOWN",
        source_observation_id=observation.source_observation_id,
        source_frame_id=observation.source_frame_id,
        route_version=observation.route_version,
        environment_digest=observation.environment_digest,
        current_progress_m=observation.current_progress_m,
        shared_action_end_progress_m=evidence.shared_action_lease.lease_end_progress_m,
        current_action_relation=evidence.current_action.relation.value,
        future_obligation_relation=evidence.future_obligation.relation.value,
        clarification_state=evidence.clarification.state.value,
        precommitment_refresh_guarantee=evidence.refresh_guarantee.state.value,
        candidate_relationship=evidence.compatibility_relationship.value,
        full_plan_coverage=bool(full_plan_coverage_v1),
        current_executable_coverage=evidence.current_action.local_coverage_available,
        recoverability=evidence.recoverability.status.value,
        recoverability_by_candidate={row.candidate_id: row.status.value for row in evidence.recoverability.candidate_rows},
        latest_safe_clarification_monotonic=clarification.clarification_start_deadline_monotonic,
        answer_deadline_monotonic=clarification.answer_deadline_monotonic,
        latest_safe_slack_s=None if clarification.clarification_start_deadline_monotonic is None else clarification.clarification_start_deadline_monotonic - now,
        time_to_divergence_lower_bound_s=None if commitment_time is None else commitment_time - now,
        decision_window_digest=evidence.decision_evidence_digest,
        current_action_equivalence_evidence_digest=evidence.current_action.evidence_digest,
        future_obligation_evidence_digest=evidence.future_obligation.evidence_digest,
        refresh_guarantee_evidence_digest=evidence.refresh_guarantee.evidence_digest,
        recoverability_evidence_digest=evidence.recoverability.evidence_digest,
        valid_until_monotonic=evidence.shared_action_lease.lease_expiry_monotonic,
        plan_coverage_evidence={"V3_CURRENT_ACTION": {
            "availability": evidence.current_action.availability.value,
            "relation": evidence.current_action.relation.value,
            "local_coverage_available": evidence.current_action.local_coverage_available,
            "endpoint_boundaries_m": dict(evidence.current_action.endpoint_boundaries_m),
        }},
        reason_codes=evidence.reason_codes, v3_bundle=evidence,
    )


__all__ = ["RuntimeDecisionWindowV3", "evaluate_runtime_decision_evidence_v3"]
