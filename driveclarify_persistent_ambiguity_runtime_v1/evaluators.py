"""Pure decision-window evaluators frozen for G0.

All functions are deterministic and fail closed.  They consume typed evidence
and return typed evidence; none can advance a route planner or issue control.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from typing import Dict, Iterable, Mapping, Optional, Sequence, Tuple

from .contracts import ContractViolation, canonical_pair_key, validate_dependency_completeness
from .types import (
    CandidateRelationship,
    CurrentActionRelation,
    EvidenceGrade,
    EvidenceProvenance,
    EvidenceResult,
    EvidenceStatus,
    PlanCoverageStatus,
    Recoverability,
    TimeToDivergenceSemantics,
    UsagePurpose,
    available_result,
    unknown_result,
)


def _finite(*values: float) -> bool:
    return all(math.isfinite(float(value)) for value in values)


@dataclass(frozen=True)
class PlanCoverageInput:
    claim_id: str
    required_endpoint_id: str
    required_endpoint_type: str
    route_version_id: str
    plan_route_version_id: Optional[str]
    plan_frame: Optional[str]
    plan_unit: Optional[str]
    current_progress_m: Optional[float]
    required_end_progress_m: Optional[float]
    valid_intervals_m: Tuple[Tuple[float, float], ...]
    plan_arc_length_m: Optional[float]
    transform_uncertainty_m: Optional[float]
    discretization_uncertainty_m: Optional[float]
    execution_latency_distance_m: Optional[float]
    alignment_verified: bool
    route_continuity_verified: bool
    provenance: EvidenceProvenance


def _continuous_interval_end(
    intervals: Sequence[Tuple[float, float]], start_progress: float
) -> Optional[float]:
    normalized = sorted((float(left), float(right)) for left, right in intervals)
    if any(left > right for left, right in normalized):
        return None
    containing = [right for left, right in normalized if left <= start_progress <= right]
    if not containing:
        return None
    end = max(containing)
    for left, right in normalized:
        if left <= end and right > end:
            end = right
    return end


def evaluate_plan_coverage(request: PlanCoverageInput) -> EvidenceResult:
    required = (
        request.plan_route_version_id,
        request.plan_frame,
        request.plan_unit,
        request.current_progress_m,
        request.required_end_progress_m,
        request.plan_arc_length_m,
        request.transform_uncertainty_m,
        request.discretization_uncertainty_m,
        request.execution_latency_distance_m,
    )
    if any(value is None for value in required):
        return unknown_result(
            "PLAN_COVERAGE_DEPENDENCY_MISSING",
            dependencies=("plan_route_binding", "required_endpoint", "uncertainty_bounds"),
            unit="m_route",
            frame="ROUTE_DIRECTED_PROGRESS",
            clock_domain="MONOTONIC",
            purpose=UsagePurpose.AUTHORIZATION,
            provenance=request.provenance,
        )
    if not request.alignment_verified:
        return unknown_result(
            "PLAN_COVERAGE_ALIGNMENT_UNKNOWN",
            dependencies=("coordinate_alignment",),
            unit="m_route",
            frame="ROUTE_DIRECTED_PROGRESS",
            clock_domain="MONOTONIC",
            purpose=UsagePurpose.AUTHORIZATION,
            provenance=request.provenance,
        )
    if request.plan_route_version_id != request.route_version_id:
        return unknown_result(
            "PLAN_COVERAGE_ROUTE_VERSION_MISMATCH",
            dependencies=("route_version",),
            unit="m_route",
            frame="ROUTE_DIRECTED_PROGRESS",
            clock_domain="MONOTONIC",
            purpose=UsagePurpose.AUTHORIZATION,
            provenance=request.provenance,
            status=EvidenceStatus.STALE,
            grade=EvidenceGrade.INVALIDATED,
        )
    if request.plan_frame != "ROUTE_DIRECTED_PROGRESS" or request.plan_unit != "m_route":
        return unknown_result(
            "PLAN_COVERAGE_FRAME_OR_UNIT_MISMATCH",
            dependencies=("coordinate_frame", "unit"),
            unit="m_route",
            frame="ROUTE_DIRECTED_PROGRESS",
            clock_domain="MONOTONIC",
            purpose=UsagePurpose.AUTHORIZATION,
            provenance=request.provenance,
        )
    numeric = tuple(float(value) for value in required[3:])
    if not _finite(*numeric) or any(value < 0 for value in numeric[3:]):
        return unknown_result(
            "PLAN_COVERAGE_NUMERIC_INPUT_INVALID",
            unit="m_route",
            frame="ROUTE_DIRECTED_PROGRESS",
            clock_domain="MONOTONIC",
            purpose=UsagePurpose.AUTHORIZATION,
            provenance=request.provenance,
            status=EvidenceStatus.INVALID,
            grade=EvidenceGrade.INVALIDATED,
        )
    current = float(request.current_progress_m)
    endpoint = float(request.required_end_progress_m)
    if request.required_endpoint_type == "SHARED_ACTION_WINDOW_END" and endpoint <= current:
        return unknown_result(
            "SHARED_ACTION_WINDOW_MUST_HAVE_POSITIVE_LENGTH",
            unit="m_route",
            frame="ROUTE_DIRECTED_PROGRESS",
            clock_domain="MONOTONIC",
            purpose=UsagePurpose.AUTHORIZATION,
            provenance=request.provenance,
            status=EvidenceStatus.INVALID,
            grade=EvidenceGrade.INVALIDATED,
        )
    if not request.route_continuity_verified:
        return unknown_result(
            "PLAN_ROUTE_CONTINUITY_UNKNOWN",
            unit="m_route",
            frame="ROUTE_DIRECTED_PROGRESS",
            clock_domain="MONOTONIC",
            purpose=UsagePurpose.AUTHORIZATION,
            provenance=request.provenance,
        )
    coverage_end = _continuous_interval_end(request.valid_intervals_m, current)
    if coverage_end is None:
        status = PlanCoverageStatus.NOT_COVERED
        coverage_end = current
    else:
        uncertainty = (
            float(request.transform_uncertainty_m)
            + float(request.discretization_uncertainty_m)
            + float(request.execution_latency_distance_m)
        )
        margin = coverage_end - endpoint - uncertainty
        if margin > 0:
            status = PlanCoverageStatus.COVERED
        elif coverage_end > current:
            status = PlanCoverageStatus.PARTIALLY_COVERED
        else:
            status = PlanCoverageStatus.NOT_COVERED
    margin = (
        coverage_end
        - endpoint
        - float(request.transform_uncertainty_m)
        - float(request.discretization_uncertainty_m)
        - float(request.execution_latency_distance_m)
    )
    value = {
        "coverage_status": status.value,
        "claim_id": request.claim_id,
        "required_endpoint_id": request.required_endpoint_id,
        "required_endpoint_type": request.required_endpoint_type,
        "distance_to_required_endpoint_m": max(0.0, endpoint - current),
        "plan_arc_length_m": float(request.plan_arc_length_m),
        "coverage_start_progress_m": current,
        "coverage_end_progress_m": coverage_end,
        "required_end_progress_m": endpoint,
        "coverage_margin_m": margin,
        "route_version_id": request.route_version_id,
    }
    return available_result(
        value,
        unit="m_route",
        frame="ROUTE_DIRECTED_PROGRESS",
        clock_domain="MONOTONIC",
        purpose=UsagePurpose.AUTHORIZATION,
        provenance=request.provenance,
        dependencies=("plan_route_alignment", "route_continuity", "coverage_endpoint"),
    )


@dataclass(frozen=True)
class CandidateReachability:
    candidate_id: str
    endpoint_defined: Optional[bool]
    legal_lane_connectivity: Optional[bool]
    branch_accessible: Optional[bool]
    braking_margin_sufficient: Optional[bool]
    lateral_feasible: Optional[bool]
    rule_margin_sufficient: Optional[bool]
    remaining_distance_sufficient: Optional[bool]
    latency_margin_sufficient: Optional[bool]
    dynamic_safety_guard_pass: Optional[bool]
    route_version_matches: Optional[bool]
    fresh: Optional[bool]
    irreversible_proven: bool = False
    no_collision_observed: Optional[bool] = None


def evaluate_recoverability(
    rows: Sequence[CandidateReachability], provenance: EvidenceProvenance
) -> EvidenceResult:
    if len(rows) < 2:
        return unknown_result(
            "RECOVERABILITY_REQUIRES_MULTIPLE_CANDIDATES",
            unit="ENUM", frame="SHARED_ACTION_ENDPOINT", clock_domain="MONOTONIC",
            purpose=UsagePurpose.SAFETY_CRITICAL, provenance=provenance,
        )
    if any(row.irreversible_proven for row in rows):
        return available_result(
            Recoverability.IRREVERSIBLE.value,
            unit="ENUM", frame="SHARED_ACTION_ENDPOINT", clock_domain="MONOTONIC",
            purpose=UsagePurpose.SAFETY_CRITICAL, provenance=provenance,
            dependencies=("candidate_wise_irreversibility_proof",),
        )
    fields = (
        "endpoint_defined", "legal_lane_connectivity", "branch_accessible",
        "braking_margin_sufficient", "lateral_feasible", "rule_margin_sufficient",
        "remaining_distance_sufficient", "latency_margin_sufficient",
        "dynamic_safety_guard_pass", "route_version_matches", "fresh",
    )
    values = [getattr(row, field) for row in rows for field in fields]
    if any(value is None for value in values):
        return unknown_result(
            "RECOVERABILITY_DEPENDENCY_UNKNOWN",
            dependencies=fields,
            unit="ENUM", frame="SHARED_ACTION_ENDPOINT", clock_domain="MONOTONIC",
            purpose=UsagePurpose.SAFETY_CRITICAL, provenance=provenance,
        )
    if not all(values):
        return unknown_result(
            "RECOVERABILITY_NOT_PROVEN",
            dependencies=fields,
            unit="ENUM", frame="SHARED_ACTION_ENDPOINT", clock_domain="MONOTONIC",
            purpose=UsagePurpose.SAFETY_CRITICAL, provenance=provenance,
        )
    return available_result(
        Recoverability.RECOVERABLE.value,
        unit="ENUM", frame="SHARED_ACTION_ENDPOINT", clock_domain="MONOTONIC",
        purpose=UsagePurpose.SAFETY_CRITICAL, provenance=provenance,
        dependencies=fields,
    )


@dataclass(frozen=True)
class CurrentActionEquivalenceInput:
    candidate_ids: Tuple[str, ...]
    semantic_candidates_unresolved: bool
    comparison_context_aligned: bool
    plan_coverage: Mapping[str, EvidenceResult]
    action_primitives: Mapping[str, str]
    pairwise_geometry_separation_m: Mapping[str, float]
    geometry_threshold_m: Optional[float]
    geometry_uncertainty_m: Optional[float]
    commitment_passed: Mapping[str, Optional[bool]]
    recoverability: EvidenceResult
    shared_action_safe: EvidenceResult
    provenance: EvidenceProvenance


def evaluate_current_action_equivalence(
    request: CurrentActionEquivalenceInput,
) -> EvidenceResult:
    ids = tuple(sorted(request.candidate_ids))
    if len(ids) < 2 or not request.semantic_candidates_unresolved:
        return unknown_result(
            "CURRENT_ACTION_REQUIRES_ACTIVE_UNRESOLVED_K2",
            unit="ENUM", frame="DECISION_WINDOW", clock_domain="MONOTONIC",
            purpose=UsagePurpose.AUTHORIZATION, provenance=request.provenance,
        )
    if not request.comparison_context_aligned:
        return unknown_result(
            "CURRENT_ACTION_COMPARISON_CONTEXT_UNKNOWN",
            unit="ENUM", frame="DECISION_WINDOW", clock_domain="MONOTONIC",
            purpose=UsagePurpose.AUTHORIZATION, provenance=request.provenance,
        )
    if set(request.plan_coverage) != set(ids) or set(request.action_primitives) != set(ids):
        return unknown_result(
            "CURRENT_ACTION_CANDIDATE_KEYSET_MISMATCH",
            unit="ENUM", frame="DECISION_WINDOW", clock_domain="MONOTONIC",
            purpose=UsagePurpose.AUTHORIZATION, provenance=request.provenance,
            status=EvidenceStatus.INVALID, grade=EvidenceGrade.INVALIDATED,
        )
    coverage_ok, coverage_reasons = validate_dependency_completeness(
        list(request.plan_coverage.values())
    )
    if not coverage_ok or any(
        result.value.get("coverage_status") != PlanCoverageStatus.COVERED.value
        for result in request.plan_coverage.values() if result.value is not None
    ):
        return unknown_result(
            "CURRENT_ACTION_PLAN_COVERAGE_UNKNOWN",
            dependencies=coverage_reasons,
            unit="ENUM", frame="DECISION_WINDOW", clock_domain="MONOTONIC",
            purpose=UsagePurpose.AUTHORIZATION, provenance=request.provenance,
        )
    commitment_values = [request.commitment_passed.get(candidate_id) for candidate_id in ids]
    if any(value is None for value in commitment_values):
        return unknown_result(
            "CURRENT_ACTION_COMMITMENT_STATE_UNKNOWN",
            unit="ENUM", frame="DECISION_WINDOW", clock_domain="MONOTONIC",
            purpose=UsagePurpose.AUTHORIZATION, provenance=request.provenance,
        )
    primitives = {request.action_primitives[candidate_id] for candidate_id in ids}
    if len(primitives) > 1 or any(commitment_values):
        return available_result(
            CurrentActionRelation.CURRENT_ACTION_DIVERGENT.value,
            unit="ENUM", frame="DECISION_WINDOW", clock_domain="MONOTONIC",
            purpose=UsagePurpose.AUTHORIZATION, provenance=request.provenance,
            dependencies=("plan_coverage", "action_primitive", "commitment_state"),
        )
    if request.geometry_threshold_m is None or request.geometry_uncertainty_m is None:
        return unknown_result(
            "CURRENT_ACTION_GEOMETRY_THRESHOLD_UNKNOWN",
            unit="ENUM", frame="DECISION_WINDOW", clock_domain="MONOTONIC",
            purpose=UsagePurpose.AUTHORIZATION, provenance=request.provenance,
        )
    if not _finite(request.geometry_threshold_m, request.geometry_uncertainty_m) or request.geometry_threshold_m < 0 or request.geometry_uncertainty_m < 0:
        return unknown_result(
            "CURRENT_ACTION_GEOMETRY_THRESHOLD_INVALID",
            unit="ENUM", frame="DECISION_WINDOW", clock_domain="MONOTONIC",
            purpose=UsagePurpose.AUTHORIZATION, provenance=request.provenance,
            status=EvidenceStatus.INVALID, grade=EvidenceGrade.INVALIDATED,
        )
    expected_pairs = {
        canonical_pair_key(left, right)
        for left, right in itertools.combinations(ids, 2)
    }
    if set(request.pairwise_geometry_separation_m) != expected_pairs:
        return unknown_result(
            "CURRENT_ACTION_PAIRWISE_GEOMETRY_INCOMPLETE",
            unit="ENUM", frame="DECISION_WINDOW", clock_domain="MONOTONIC",
            purpose=UsagePurpose.AUTHORIZATION, provenance=request.provenance,
        )
    limit = float(request.geometry_threshold_m) + float(request.geometry_uncertainty_m)
    if any(float(value) > limit for value in request.pairwise_geometry_separation_m.values()):
        return available_result(
            CurrentActionRelation.CURRENT_ACTION_DIVERGENT.value,
            unit="ENUM", frame="DECISION_WINDOW", clock_domain="MONOTONIC",
            purpose=UsagePurpose.AUTHORIZATION, provenance=request.provenance,
            dependencies=("plan_coverage", "pairwise_geometry", "calibrated_threshold"),
        )
    recoverable_ok = (
        request.recoverability.is_safety_authorizable
        and request.recoverability.value == Recoverability.RECOVERABLE.value
    )
    safety_ok = request.shared_action_safe.is_safety_authorizable and request.shared_action_safe.value is True
    if not (recoverable_ok and safety_ok):
        return unknown_result(
            "CURRENT_ACTION_RECOVERABILITY_OR_SAFETY_UNKNOWN",
            dependencies=("recoverability", "shared_action_safety"),
            unit="ENUM", frame="DECISION_WINDOW", clock_domain="MONOTONIC",
            purpose=UsagePurpose.AUTHORIZATION, provenance=request.provenance,
        )
    return available_result(
        CurrentActionRelation.CURRENT_ACTION_EQUIVALENT.value,
        unit="ENUM", frame="DECISION_WINDOW", clock_domain="MONOTONIC",
        purpose=UsagePurpose.AUTHORIZATION, provenance=request.provenance,
        dependencies=("full_shared_window_coverage", "pairwise_geometry", "recoverability", "safety"),
    )


@dataclass(frozen=True)
class ManeuverOnsetInput:
    pair_candidate_ids: Tuple[str, str]
    route_version_id: str
    progress_samples_m: Tuple[float, ...]
    separation_samples_m: Tuple[float, ...]
    threshold_m: Optional[float]
    uncertainty_m: Optional[float]
    hysteresis_samples: int
    plan_coverage_verified: bool
    comparison_context_aligned: bool
    provenance: EvidenceProvenance


def evaluate_maneuver_onset(request: ManeuverOnsetInput) -> EvidenceResult:
    if not request.plan_coverage_verified or not request.comparison_context_aligned:
        return unknown_result(
            "MANEUVER_ONSET_COVERAGE_OR_ALIGNMENT_UNKNOWN",
            unit="m_route", frame="ROUTE_DIRECTED_PROGRESS", clock_domain="MONOTONIC",
            purpose=UsagePurpose.AUTHORIZATION, provenance=request.provenance,
        )
    if request.threshold_m is None or request.uncertainty_m is None:
        return unknown_result(
            "MANEUVER_ONSET_THRESHOLD_UNKNOWN",
            unit="m_route", frame="ROUTE_DIRECTED_PROGRESS", clock_domain="MONOTONIC",
            purpose=UsagePurpose.AUTHORIZATION, provenance=request.provenance,
        )
    if (
        len(request.progress_samples_m) != len(request.separation_samples_m)
        or not request.progress_samples_m
        or request.hysteresis_samples < 1
        or not _finite(*(request.progress_samples_m + request.separation_samples_m))
    ):
        return unknown_result(
            "MANEUVER_ONSET_SERIES_INVALID",
            unit="m_route", frame="ROUTE_DIRECTED_PROGRESS", clock_domain="MONOTONIC",
            purpose=UsagePurpose.AUTHORIZATION, provenance=request.provenance,
            status=EvidenceStatus.INVALID, grade=EvidenceGrade.INVALIDATED,
        )
    limit = float(request.threshold_m) + float(request.uncertainty_m)
    above = [value > limit for value in request.separation_samples_m]
    for start in range(0, len(above) - request.hysteresis_samples + 1):
        if all(above[start:start + request.hysteresis_samples]):
            value = {
                "boundary_id": "onset-" + canonical_pair_key(*request.pair_candidate_ids),
                "route_version_id": request.route_version_id,
                "progress_m": float(request.progress_samples_m[start]),
                "uncertainty_m": float(request.uncertainty_m),
            }
            return available_result(
                value,
                unit="m_route", frame="ROUTE_DIRECTED_PROGRESS", clock_domain="MONOTONIC",
                purpose=UsagePurpose.AUTHORIZATION, provenance=request.provenance,
                dependencies=("candidate_corridor", "hysteresis", "plan_coverage"),
            )
    return unknown_result(
        "MANEUVER_ONSET_NOT_OBSERVED_IN_COVERED_PLAN",
        unit="m_route", frame="ROUTE_DIRECTED_PROGRESS", clock_domain="MONOTONIC",
        purpose=UsagePurpose.AUTHORIZATION, provenance=request.provenance,
        status=EvidenceStatus.NOT_AVAILABLE,
    )


def evaluate_commitment_boundary_state(
    *,
    ego_progress: EvidenceResult,
    commitment_boundary: EvidenceResult,
    provenance: EvidenceProvenance,
) -> EvidenceResult:
    eligible, reasons = validate_dependency_completeness(
        (ego_progress, commitment_boundary)
    )
    if not eligible:
        return unknown_result(
            "COMMITMENT_BOUNDARY_DEPENDENCY_UNKNOWN",
            dependencies=reasons,
            unit="ENUM", frame="DECISION_WINDOW", clock_domain="MONOTONIC",
            purpose=UsagePurpose.AUTHORIZATION, provenance=provenance,
        )
    boundary = commitment_boundary.value
    if not isinstance(boundary, Mapping) or not {
        "progress_m", "uncertainty_m", "route_version_id"
    }.issubset(boundary):
        return unknown_result(
            "COMMITMENT_BOUNDARY_VALUE_INVALID",
            unit="ENUM", frame="DECISION_WINDOW", clock_domain="MONOTONIC",
            purpose=UsagePurpose.AUTHORIZATION, provenance=provenance,
            status=EvidenceStatus.INVALID, grade=EvidenceGrade.INVALIDATED,
        )
    progress = float(ego_progress.value)
    commitment = float(boundary["progress_m"])
    uncertainty = float(boundary["uncertainty_m"])
    if not _finite(progress, commitment, uncertainty) or uncertainty < 0:
        return unknown_result(
            "COMMITMENT_BOUNDARY_VALUE_INVALID",
            unit="ENUM", frame="DECISION_WINDOW", clock_domain="MONOTONIC",
            purpose=UsagePurpose.AUTHORIZATION, provenance=provenance,
            status=EvidenceStatus.INVALID, grade=EvidenceGrade.INVALIDATED,
        )
    if progress < commitment - uncertainty:
        return available_result(
            "BEFORE_COMMITMENT_RECOVERABILITY_BOUNDARY",
            unit="ENUM", frame="DECISION_WINDOW", clock_domain="MONOTONIC",
            purpose=UsagePurpose.AUTHORIZATION, provenance=provenance,
            dependencies=("ego_progress", "commitment_boundary"),
        )
    if progress <= commitment + uncertainty:
        return unknown_result(
            "ON_COMMITMENT_BOUNDARY",
            unit="ENUM", frame="DECISION_WINDOW", clock_domain="MONOTONIC",
            purpose=UsagePurpose.AUTHORIZATION, provenance=provenance,
            status=EvidenceStatus.NOT_AVAILABLE,
        )
    return unknown_result(
        "PASSED_DECISION_POINT",
        unit="ENUM", frame="DECISION_WINDOW", clock_domain="MONOTONIC",
        purpose=UsagePurpose.AUTHORIZATION, provenance=provenance,
        status=EvidenceStatus.NOT_AVAILABLE,
    )


def evaluate_decision_point(
    *,
    candidate_ids: Sequence[str],
    pairwise_maneuver_onsets: Mapping[str, EvidenceResult],
    commitment_points: Mapping[str, EvidenceResult],
    provenance: EvidenceProvenance,
) -> EvidenceResult:
    ids = tuple(sorted(candidate_ids))
    expected_pairs = {
        canonical_pair_key(left, right)
        for left, right in itertools.combinations(ids, 2)
    }
    if set(pairwise_maneuver_onsets) != expected_pairs or set(commitment_points) != set(ids):
        return unknown_result(
            "DECISION_POINT_CANDIDATE_OR_PAIRSET_INCOMPLETE",
            unit="m_route", frame="ROUTE_DIRECTED_PROGRESS", clock_domain="MONOTONIC",
            purpose=UsagePurpose.AUTHORIZATION, provenance=provenance,
        )
    dependencies = tuple(pairwise_maneuver_onsets.values()) + tuple(commitment_points.values())
    eligible, reasons = validate_dependency_completeness(dependencies)
    if not eligible:
        return unknown_result(
            "DECISION_POINT_DEPENDENCY_UNKNOWN",
            dependencies=reasons,
            unit="m_route", frame="ROUTE_DIRECTED_PROGRESS", clock_domain="MONOTONIC",
            purpose=UsagePurpose.AUTHORIZATION, provenance=provenance,
        )
    onset_progress = {
        pair: float(result.value["progress_m"])
        for pair, result in pairwise_maneuver_onsets.items()
    }
    commitment_progress = {
        candidate_id: float(result.value["progress_m"])
        for candidate_id, result in commitment_points.items()
    }
    return available_result(
        {
            "pairwise_maneuver_onset_progress_m": onset_progress,
            "candidate_commitment_progress_m": commitment_progress,
            "earliest_maneuver_onset_progress_m": min(onset_progress.values()),
            "earliest_commitment_progress_m": min(commitment_progress.values()),
        },
        unit="m_route", frame="ROUTE_DIRECTED_PROGRESS", clock_domain="MONOTONIC",
        purpose=UsagePurpose.AUTHORIZATION, provenance=provenance,
        dependencies=("pairwise_maneuver_onsets", "candidate_commitment_points"),
    )


def evaluate_time_to_divergence(
    semantics: TimeToDivergenceSemantics,
    *,
    lower_bound_s: Optional[float],
    upper_bound_s: Optional[float],
    provenance: EvidenceProvenance,
) -> EvidenceResult:
    if semantics is TimeToDivergenceSemantics.NO_MATERIAL_DIVERGENCE:
        return available_result(
            {"semantics": semantics.value, "lower_bound_s": None, "upper_bound_s": None},
            unit="s", frame="TIME_FROM_COMPARISON_CONTEXT", clock_domain="MONOTONIC",
            purpose=UsagePurpose.AUTHORIZATION, provenance=provenance,
        )
    if semantics in (
        TimeToDivergenceSemantics.NOT_YET_OBSERVABLE,
        TimeToDivergenceSemantics.UNKNOWN,
        TimeToDivergenceSemantics.ALREADY_DIVERGENT,
        TimeToDivergenceSemantics.PASSED_DECISION_POINT,
    ):
        return unknown_result(
            semantics.value,
            unit="s", frame="TIME_FROM_COMPARISON_CONTEXT", clock_domain="MONOTONIC",
            purpose=UsagePurpose.AUTHORIZATION, provenance=provenance,
            status=EvidenceStatus.NOT_AVAILABLE if semantics in (
                TimeToDivergenceSemantics.ALREADY_DIVERGENT,
                TimeToDivergenceSemantics.PASSED_DECISION_POINT,
            ) else EvidenceStatus.UNKNOWN,
        )
    if lower_bound_s is None or upper_bound_s is None:
        return unknown_result(
            "TIME_TO_DIVERGENCE_BOUND_MISSING",
            unit="s", frame="TIME_FROM_COMPARISON_CONTEXT", clock_domain="MONOTONIC",
            purpose=UsagePurpose.AUTHORIZATION, provenance=provenance,
        )
    if not _finite(lower_bound_s, upper_bound_s) or lower_bound_s < 0 or upper_bound_s < lower_bound_s:
        return unknown_result(
            "TIME_TO_DIVERGENCE_BOUND_INVALID",
            unit="s", frame="TIME_FROM_COMPARISON_CONTEXT", clock_domain="MONOTONIC",
            purpose=UsagePurpose.AUTHORIZATION, provenance=provenance,
            status=EvidenceStatus.INVALID, grade=EvidenceGrade.INVALIDATED,
        )
    return available_result(
        {
            "semantics": semantics.value,
            "lower_bound_s": float(lower_bound_s),
            "upper_bound_s": float(upper_bound_s),
        },
        unit="s", frame="TIME_FROM_COMPARISON_CONTEXT", clock_domain="MONOTONIC",
        purpose=UsagePurpose.AUTHORIZATION, provenance=provenance,
    )


def evaluate_route_distance_timing(
    *,
    route_distance_m: Optional[float],
    current_speed_mps: Optional[float],
    future_arrival_lower_s: Optional[float],
    future_arrival_upper_s: Optional[float],
    provenance: EvidenceProvenance,
) -> EvidenceResult:
    del route_distance_m, current_speed_mps  # A constant-speed shortcut is forbidden.
    if future_arrival_lower_s is None or future_arrival_upper_s is None:
        return evaluate_time_to_divergence(
            TimeToDivergenceSemantics.NOT_YET_OBSERVABLE,
            lower_bound_s=None, upper_bound_s=None, provenance=provenance,
        )
    return evaluate_time_to_divergence(
        TimeToDivergenceSemantics.FUTURE_DIVERGENCE_BOUND_FROM_TOPOLOGY,
        lower_bound_s=future_arrival_lower_s,
        upper_bound_s=future_arrival_upper_s,
        provenance=provenance,
    )


def evaluate_latest_safe_clarification(
    *,
    commitment_time_lower_bound: EvidenceResult,
    answer_latency_upper_bound: EvidenceResult,
    candidate_refresh_replan_latency_upper_bound: EvidenceResult,
    m2b_m3_authority_latency_upper_bound: EvidenceResult,
    control_response_latency_upper_bound: EvidenceResult,
    safety_margin: EvidenceResult,
    provenance: EvidenceProvenance,
) -> EvidenceResult:
    dependencies = (
        commitment_time_lower_bound,
        answer_latency_upper_bound,
        candidate_refresh_replan_latency_upper_bound,
        m2b_m3_authority_latency_upper_bound,
        control_response_latency_upper_bound,
        safety_margin,
    )
    eligible, reasons = validate_dependency_completeness(dependencies)
    if not eligible:
        return unknown_result(
            "LATEST_SAFE_CLARIFICATION_DEPENDENCY_UNKNOWN",
            dependencies=reasons,
            unit="s", frame="ABSOLUTE_MONOTONIC_TIME", clock_domain="MONOTONIC",
            purpose=UsagePurpose.AUTHORIZATION, provenance=provenance,
        )
    values = [float(result.value) for result in dependencies]
    if not _finite(*values) or any(value < 0 for value in values[1:]):
        return unknown_result(
            "LATEST_SAFE_CLARIFICATION_TIMING_INVALID",
            unit="s", frame="ABSOLUTE_MONOTONIC_TIME", clock_domain="MONOTONIC",
            purpose=UsagePurpose.AUTHORIZATION, provenance=provenance,
            status=EvidenceStatus.INVALID, grade=EvidenceGrade.INVALIDATED,
        )
    latest = values[0] - sum(values[1:])
    return available_result(
        latest,
        unit="s", frame="ABSOLUTE_MONOTONIC_TIME", clock_domain="MONOTONIC",
        purpose=UsagePurpose.AUTHORIZATION, provenance=provenance,
        dependencies=(
            "commitment_lower_bound", "answer_latency_upper", "candidate_refresh_replan_upper",
            "m2b_m3_authority_upper", "control_response_upper", "safety_margin",
        ),
    )


def classify_candidate_relationship(
    current_action: EvidenceResult,
    future_divergence: EvidenceResult,
    *,
    provenance: EvidenceProvenance,
) -> EvidenceResult:
    if not current_action.is_runtime_authorizable or not future_divergence.is_runtime_authorizable:
        return unknown_result(
            "CANDIDATE_RELATIONSHIP_DEPENDENCY_UNKNOWN",
            unit="ENUM", frame="DECISION_WINDOW", clock_domain="MONOTONIC",
            purpose=UsagePurpose.AUTHORIZATION, provenance=provenance,
        )
    if current_action.value == CurrentActionRelation.CURRENT_ACTION_DIVERGENT.value:
        relationship = CandidateRelationship.MATERIAL_DIVERGENCE_NOW
    elif bool(future_divergence.value):
        relationship = CandidateRelationship.CURRENTLY_EQUIVALENT_FUTURE_DIVERGENT
    else:
        relationship = CandidateRelationship.CONSEQUENCE_EQUIVALENT
    return available_result(
        relationship.value,
        unit="ENUM", frame="DECISION_WINDOW", clock_domain="MONOTONIC",
        purpose=UsagePurpose.AUTHORIZATION, provenance=provenance,
        dependencies=("current_action_relation", "future_obligation_relation"),
    )


@dataclass(frozen=True)
class ActionEligibility:
    decision: str
    eligible: bool
    preserve_unresolved_semantics: bool
    reason_code: str


def evaluate_action_eligibility(
    relationship: EvidenceResult,
    *,
    latest_safe_clarification: EvidenceResult,
    recoverability: EvidenceResult,
    hard_safety_rule_gate: EvidenceResult,
    now_monotonic: float,
) -> ActionEligibility:
    if not relationship.is_runtime_authorizable:
        return ActionEligibility("UNKNOWN", False, False, "CANDIDATE_RELATIONSHIP_UNKNOWN_FAIL_CLOSED")
    if not (hard_safety_rule_gate.is_safety_authorizable and hard_safety_rule_gate.value is True):
        return ActionEligibility("UNKNOWN", False, False, "HARD_SAFETY_RULE_GATE_UNKNOWN")
    if relationship.value == CandidateRelationship.CONSEQUENCE_EQUIVALENT.value:
        return ActionEligibility("ACT", True, False, "CONSEQUENCE_EQUIVALENT_ACT_ELIGIBLE")
    if relationship.value == CandidateRelationship.CURRENTLY_EQUIVALENT_FUTURE_DIVERGENT.value:
        if not (
            latest_safe_clarification.is_runtime_authorizable
            and float(latest_safe_clarification.value) > float(now_monotonic)
            and recoverability.is_safety_authorizable
            and recoverability.value == Recoverability.RECOVERABLE.value
        ):
            return ActionEligibility("UNKNOWN", False, True, "ACT_SHARED_WINDOW_OR_RECOVERABILITY_UNKNOWN")
        return ActionEligibility("ACT_SHARED", True, True, "CURRENT_ACTION_SHARED_FUTURE_DIVERGENCE_PENDING")
    return ActionEligibility("UNKNOWN", False, True, "CURRENT_ACTION_DIVERGENT_NO_OPTIMISTIC_ACT")
