"""Production-runtime adapter for Decision Evidence Contract V2.0.

The adapter consumes the existing same-frame SimLingo refresh bundle, the
existing baseline plan, persistent semantic identities, and live CARLA
topology.  It performs no model forward, planner advance, PID call, or control
write.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Tuple

from driveclarify_persistent_ambiguity_runtime_v1.contracts import canonical_sha256
from driveclarify_persistent_ambiguity_runtime_v1.runtime_evaluator import (
    RuntimeWindowObservation,
    _route_points,
    _speed_points,
)

from .evaluator import (
    DecisionEvidenceInputV2,
    LocalPlanV2,
    TimingCalibrationV2,
    evaluate_decision_evidence_v2,
)
from .types import (
    CandidateRelationshipV2,
    CurrentExecutableRelationV2,
    DecisionEvidenceBundleV2,
    FutureObligationRelationV2,
    FutureObligationRowV2,
    SourceBindingV2,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PHASE_B_ROOT = (
    REPOSITORY_ROOT
    / "reports/driveclarify_persistent_ambiguity_runtime_v1_continuous_completion"
)


def runtime_referent_route_order_authorization(
    referents: Sequence[Any],
) -> Tuple[bool, str]:
    """Check whether image-only ordering can authorize a route-order join.

    The frozen positive white-van contract joins an apparent near/far pair on
    the same forward approach to route-ordered topology.  A lateral LEFT/RIGHT
    pair is still a real K=2 ambiguity, but image-only evidence cannot say
    which object precedes which future route opportunity.  This function uses
    only the already-frozen categorical image locations and apparent ranks; it
    has no scenario identity, actor truth, label, or new numeric threshold.
    """

    rows = tuple(referents)
    if len(rows) < 2:
        return False, "FUTURE_OBLIGATION_REFERENT_K2_UNAVAILABLE"
    locations = tuple(str(getattr(row, "relative_image_location", "")) for row in rows[:2])
    ranks = tuple(getattr(row, "apparent_size_rank", None) for row in rows[:2])
    if locations == ("CENTER", "CENTER") and ranks == (1, 2):
        return True, "APPARENT_FORWARD_NEAR_FAR_ROUTE_ORDER_AUTHORIZATION_ELIGIBLE"
    return False, "FUTURE_OBLIGATION_ROUTE_ORDER_NOT_AUTHORIZATION_GRADE"


@dataclass(frozen=True)
class RuntimeDecisionWindowV2:
    status: str
    source_observation_id: str
    source_frame_id: Any
    route_version: str
    environment_digest: str
    current_progress_m: float
    shared_action_end_progress_m: Optional[float]
    current_action_relation: str
    future_obligation_relation: str
    candidate_relationship: str
    full_plan_coverage: bool
    current_executable_coverage: bool
    recoverability: str
    latest_safe_clarification_monotonic: Optional[float]
    answer_deadline_monotonic: Optional[float]
    latest_safe_slack_s: Optional[float]
    time_to_divergence_lower_bound_s: Optional[float]
    decision_window_digest: str
    current_action_equivalence_evidence_digest: str
    future_obligation_evidence_digest: str
    valid_until_monotonic: Optional[float]
    plan_coverage_evidence: Mapping[str, Any]
    reason_codes: Tuple[str, ...]
    v2_bundle: DecisionEvidenceBundleV2

    def to_dict(self) -> dict[str, Any]:
        value = dict(self.__dict__)
        value["v2_bundle"] = self.v2_bundle.to_dict()
        return value


def load_phase_b_timing_calibration_v2(
    *, route_version: str
) -> TimingCalibrationV2:
    """Derive clock/route bounds only from the two accepted Phase-B runs."""

    final = json.loads(
        (PHASE_B_ROOT / "PHASE_B_FINAL_EVIDENCE_RECEIPT.json").read_text(
            encoding="utf-8"
        )
    )
    accepted = tuple(str(value) for value in final.get("accepted_run_ids", ()))
    if (
        final.get("status")
        != "PASS_PHASE_B_DECISION_WINDOW_PHYSICAL_EVIDENCE_COMPLETE"
        or accepted != ("B1-R4", "B2")
        or set(final.get("stability", {}).get("route_versions", {}).values())
        != {route_version}
    ):
        return TimingCalibrationV2(None, None, (), route_version, False)

    route_rates = []
    clock_rates = []
    sources = []
    for run_id in accepted:
        root = PHASE_B_ROOT / run_id
        timing_path = root / "TIMING_EVIDENCE.json"
        progress_path = root / "EGO_ROUTE_PROGRESS_EVIDENCE.json"
        timing = json.loads(timing_path.read_text(encoding="utf-8"))
        progress = json.loads(progress_path.read_text(encoding="utf-8"))
        crossing = timing.get("commitment_crossing_interval", {})
        provenance = timing.get("latest_safe_clarification", {}).get(
            "provenance", {}
        )
        try:
            source_mono = float(provenance["observed_monotonic_time"])
            source_sim = float(provenance["source_simulation_time"])
            source_progress = float(progress["value"])
            lower_mono = float(crossing["lower_monotonic_s"])
            lower_sim = float(crossing["lower_simulation_s"])
            lower_progress = float(crossing["lower_progress_m"])
            upper_mono = float(crossing["upper_monotonic_s"])
            upper_sim = float(crossing["upper_simulation_s"])
        except (KeyError, TypeError, ValueError):
            return TimingCalibrationV2(None, None, (), route_version, False)
        denominators = (
            lower_progress - source_progress,
            lower_sim - source_sim,
            upper_sim - source_sim,
        )
        if any(not math.isfinite(value) or value <= 0.0 for value in denominators):
            return TimingCalibrationV2(None, None, (), route_version, False)
        route_rates.append((lower_mono - source_mono) / denominators[0])
        clock_rates.extend(
            (
                (lower_mono - source_mono) / denominators[1],
                (upper_mono - source_mono) / denominators[2],
            )
        )
        sources.extend((str(timing_path.relative_to(REPOSITORY_ROOT)), str(progress_path.relative_to(REPOSITORY_ROOT))))

    if not route_rates or not clock_rates or any(
        not math.isfinite(value) or value <= 0.0
        for value in route_rates + clock_rates
    ):
        return TimingCalibrationV2(None, None, tuple(sources), route_version, False)
    return TimingCalibrationV2(
        # Fastest observed arrival is the conservative commitment-time lower
        # bound; slowest wall/simulation conversion bounds answer waiting.
        route_seconds_per_meter_lower_bound=min(route_rates),
        simulation_to_monotonic_upper_bound=max(clock_rates),
        source_artifacts=tuple(sources),
        route_version=route_version,
        runtime_config_bound=True,
    )


def _points_tuple(value: Optional[Sequence[Sequence[float]]]) -> Tuple[Tuple[float, float], ...]:
    if value is None:
        return ()
    try:
        return tuple((float(row[0]), float(row[1])) for row in value)
    except (IndexError, TypeError, ValueError):
        return ()


def _future_rows(
    *, episode: Any, connectors: Sequence[Mapping[str, Any]], source: SourceBindingV2
) -> Tuple[FutureObligationRowV2, ...]:
    connector_by_id = {
        str(row.get("candidate_id")): row
        for row in connectors
        if row.get("candidate_id") is not None
    }
    rows = []
    for candidate in episode.candidates:
        if candidate.candidate_id not in episode.active_candidate_ids:
            continue
        connector = connector_by_id.get(candidate.candidate_id, {})
        topology_available = bool(
            connector.get("status") == "AVAILABLE"
            and connector.get("exit_reached") is True
            and connector.get("live_map_pair_match") is True
            and str(connector.get("branch_id")) == candidate.topology_branch_id
            and str(connector.get("junction_id")) == candidate.topology_junction_id
        )
        rows.append(
            FutureObligationRowV2(
                candidate_id=candidate.candidate_id,
                interpretation_id=candidate.interpretation_id,
                semantic_sha256=candidate.semantic_sha256,
                obligation_digest=candidate.target_obligation_digest,
                obligation_type=candidate.obligation_type,
                maneuver=candidate.maneuver,
                event_relation=candidate.event_relation,
                referent_lineage_id=candidate.referent_lineage_id,
                referent_description=candidate.referent_description,
                target_id=candidate.topology_target_id,
                junction_id=candidate.topology_junction_id,
                branch_id=candidate.topology_branch_id,
                route_order_index=int(candidate.topology_route_order),
                route_version=source.route_version,
                source_observation_id=candidate.source_observation_id,
                source_frame_id=candidate.source_frame_id,
                source_kinds=tuple(candidate.provenance),
                active_unresolved=True,
                semantic_fresh=True,
                topology_binding_available=topology_available,
                visibility="RUNTIME_OBSERVABLE",
                privileged=False,
                authorization_purpose=True,
            )
        )
    return tuple(rows)


def evaluate_runtime_decision_evidence_v2(
    *,
    bundle: Any,
    observation: RuntimeWindowObservation,
    episode: Any,
    connectors: Sequence[Mapping[str, Any]],
    authorized_plan_points: Sequence[Sequence[float]],
    authorized_plan_digest: str,
    authorized_speed_points: Sequence[Sequence[float]],
    authorized_speed_digest: str,
    answer_latency_simulation_s: float,
    full_plan_coverage_v1: bool,
) -> RuntimeDecisionWindowV2:
    source = SourceBindingV2(
        source_observation_id=observation.source_observation_id,
        source_frame_id=observation.source_frame_id,
        route_version=observation.route_version,
        environment_digest=observation.environment_digest,
        observed_monotonic_time=observation.observed_monotonic_time,
    )
    candidate_ids = tuple(sorted(str(value) for value in bundle.requested_candidate_ids))
    local_plans = []
    for row in bundle.evidence:
        local_plans.append(
            LocalPlanV2(
                plan_id=str(row.candidate_id),
                candidate_id=str(row.candidate_id),
                source_observation_id=str(row.source_observation_id),
                source_frame_id=row.source_frame_id,
                route_points_model_local_m=_points_tuple(_route_points(row.result)),
                control_points_model_local_m=_points_tuple(_speed_points(row.result)),
                route_digest=str(row.route_digest),
                control_digest=str(row.speed_digest),
                coordinate_contract_id=observation.model_coordinate_contract_id,
                fresh=True,
            )
        )
    local_plans.append(
        LocalPlanV2(
            plan_id="__AUTHORIZED_PLAN__",
            candidate_id=None,
            source_observation_id=observation.source_observation_id,
            source_frame_id=observation.source_frame_id,
            route_points_model_local_m=_points_tuple(authorized_plan_points),
            control_points_model_local_m=_points_tuple(authorized_speed_points),
            route_digest=authorized_plan_digest,
            control_digest=authorized_speed_digest,
            coordinate_contract_id=observation.model_coordinate_contract_id,
            fresh=True,
        )
    )
    connector_by_id = {
        str(row.get("candidate_id")): row
        for row in connectors
        if row.get("candidate_id") is not None
    }
    topology_reachable = {
        candidate_id: bool(
            connector_by_id.get(candidate_id, {}).get("status") == "AVAILABLE"
            and connector_by_id.get(candidate_id, {}).get("exit_reached") is True
            and connector_by_id.get(candidate_id, {}).get("live_map_pair_match") is True
        )
        for candidate_id in candidate_ids
    }
    lane_local = bool(
        local_plans
        and all(
            len(plan.route_points_model_local_m) >= 3
            and all(
                abs(float(point[1])) + observation.calibrated_uncertainty_m
                < observation.lane_clearance_m
                for point in plan.route_points_model_local_m[:3]
            )
            for plan in local_plans
        )
        and observation.current_progress_m + observation.calibrated_uncertainty_m
        < observation.maneuver_onset_progress_m
    )
    timing = load_phase_b_timing_calibration_v2(
        route_version=observation.route_version
    )
    aligned = bool(
        observation.alignment_verified
        and observation.coordinate_transform_verified
        and bundle.source_observation_id == observation.source_observation_id
        and str(bundle.source_frame_id) == str(observation.source_frame_id)
    )
    bundle_fresh = bool(
        bundle.complete
        and bundle.latency_seconds <= observation.runtime_latency_upper_bound_s
    )
    request = DecisionEvidenceInputV2(
        source=source,
        candidate_bundle_id=bundle.bundle_id,
        active_member_set_digest=bundle.candidate_set_digest,
        candidate_ids=candidate_ids,
        local_plans=tuple(local_plans),
        future_obligations=_future_rows(
            episode=episode, connectors=connectors, source=source
        ),
        current_progress_m=observation.current_progress_m,
        speed_upper_bound_mps=max(
            observation.current_speed_mps, observation.speed_limit_mps
        ),
        maximum_normal_planning_interval_s=(
            observation.maximum_normal_planning_interval_s
        ),
        next_refresh_monotonic_upper_s=(
            observation.maximum_normal_planning_interval_s
            * float(timing.simulation_to_monotonic_upper_bound or 0.0)
        ),
        plan_checkpoint_tolerance_m=(
            observation.plan_checkpoint_spacing_tolerance_m
        ),
        calibrated_uncertainty_m=observation.calibrated_uncertainty_m,
        lane_clearance_m=observation.lane_clearance_m,
        expected_route_point_count=observation.expected_plan_point_count,
        expected_control_point_count=10,
        shared_topology_corridor_end_progress_m=(
            observation.maneuver_onset_progress_m
        ),
        candidate_commitment_progress_m=dict(
            observation.candidate_commitment_progress_m
        ),
        lane_action_compatible=lane_local,
        candidate_topology_reachable=topology_reachable,
        candidate_lane_reachable=dict(topology_reachable),
        candidate_braking_lateral_feasible={
            candidate_id: bool(
                topology_reachable[candidate_id]
                and observation.dynamic_safety_gate
                and observation.hard_rule_gate
            )
            for candidate_id in candidate_ids
        },
        alignment_verified=aligned,
        bundle_complete=bool(bundle.complete),
        bundle_fresh=bundle_fresh,
        dynamic_safety_gate=observation.dynamic_safety_gate,
        hard_rule_gate=observation.hard_rule_gate,
        answer_latency_simulation_s=answer_latency_simulation_s,
        post_answer_latency_monotonic_s=observation.runtime_latency_upper_bound_s,
        timing_calibration=timing,
        full_plan_coverage_v1=full_plan_coverage_v1,
    )
    evidence = evaluate_decision_evidence_v2(request)
    clarification = evidence.clarification_window
    commitments = tuple(observation.candidate_commitment_progress_m.values())
    commitment_time = clarification.commitment_time_lower_bound_monotonic
    now = observation.observed_monotonic_time
    all_recoverable = bool(
        evidence.shared_action_lease.candidate_recoverability
        and all(
            row.recoverable is True
            for row in evidence.shared_action_lease.candidate_recoverability
        )
    )
    current_relation = (
        "CURRENT_ACTION_EQUIVALENT"
        if evidence.current_executable.relation
        is CurrentExecutableRelationV2.EQUIVALENT
        else "CURRENT_ACTION_DIVERGENT"
        if evidence.current_executable.relation
        is CurrentExecutableRelationV2.DIVERGENT
        else "UNKNOWN"
    )
    future_relation = (
        "FUTURE_DIVERGENT"
        if evidence.future_obligation.relation
        is FutureObligationRelationV2.DIVERGENT
        else "NO_MATERIAL_DIVERGENCE"
        if evidence.future_obligation.relation
        is FutureObligationRelationV2.EQUIVALENT
        else "UNKNOWN"
    )
    relationship = (
        "CURRENTLY_DIVERGENT"
        if evidence.relationship is CandidateRelationshipV2.CURRENT_ACTION_DIVERGENT
        else evidence.relationship.value
    )
    return RuntimeDecisionWindowV2(
        status="AVAILABLE" if evidence.authorization_eligible else "UNKNOWN",
        source_observation_id=observation.source_observation_id,
        source_frame_id=observation.source_frame_id,
        route_version=observation.route_version,
        environment_digest=observation.environment_digest,
        current_progress_m=observation.current_progress_m,
        shared_action_end_progress_m=evidence.shared_action_lease.end_progress_m,
        current_action_relation=current_relation,
        future_obligation_relation=future_relation,
        candidate_relationship=relationship,
        full_plan_coverage=bool(full_plan_coverage_v1),
        current_executable_coverage=(
            evidence.current_executable.local_coverage_available
        ),
        recoverability="RECOVERABLE" if all_recoverable else "UNKNOWN",
        latest_safe_clarification_monotonic=(
            clarification.clarification_start_deadline_monotonic
        ),
        answer_deadline_monotonic=clarification.answer_deadline_monotonic,
        latest_safe_slack_s=(
            None
            if clarification.clarification_start_deadline_monotonic is None
            else clarification.clarification_start_deadline_monotonic - now
        ),
        time_to_divergence_lower_bound_s=(
            None if commitment_time is None else commitment_time - now
        ),
        decision_window_digest=evidence.decision_evidence_digest,
        current_action_equivalence_evidence_digest=(
            evidence.current_executable.evidence_digest
        ),
        future_obligation_evidence_digest=(
            evidence.future_obligation.evidence_digest
        ),
        valid_until_monotonic=(
            evidence.shared_action_lease.valid_until_monotonic
        ),
        plan_coverage_evidence={
            "V2_CURRENT_EXECUTABLE": {
                "result": evidence.current_executable.to_dict()
                if hasattr(evidence.current_executable, "to_dict")
                else {
                    "availability": evidence.current_executable.availability.value,
                    "relation": current_relation,
                    "local_coverage_available": evidence.current_executable.local_coverage_available,
                }
            }
        },
        reason_codes=evidence.reason_codes,
        v2_bundle=evidence,
    )


__all__ = [
    "RuntimeDecisionWindowV2",
    "evaluate_runtime_decision_evidence_v2",
    "load_phase_b_timing_calibration_v2",
    "runtime_referent_route_order_authorization",
]
