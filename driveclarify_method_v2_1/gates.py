"""Fail-closed Method V2.1 information and motion eligibility gates.

This module owns no model, planner, controller, or VehicleControl writer.  It
only keeps the information-action decision separate from motion authority and
holds an identity snapshot between fresh-plan completion and motion-gated
activation.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Mapping, Optional, Tuple


@dataclass(frozen=True)
class EligibilityResult:
    eligible: bool
    evidence_complete: bool
    reason_codes: Tuple[str, ...]
    available_budget_s: Optional[float] = None
    required_budget_s: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ClarificationEligibilityEvidence:
    ambiguity_unresolved: Optional[bool]
    ambiguity_decision_relevant: Optional[bool]
    effective_k: Optional[int]
    query_changes_future_semantic_action: Optional[bool]
    query_outstanding: Optional[bool]
    query_budget_available: Optional[bool]
    passenger_resolvable: Optional[bool]
    physical_safety_pass: Optional[bool]
    safe_holding_available: Optional[bool]
    clarification_state_timely: Optional[bool]
    t_available_s: Optional[float]
    t_answer_budget_s: Optional[float]
    t_fresh_replan_budget_s: Optional[float]
    t_activation_or_execution_margin_s: Optional[float]
    t_safety_margin_s: Optional[float]


def _finite_nonnegative(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
        and float(value) >= 0.0
    )


def assess_clarification_eligibility(
    evidence: ClarificationEligibilityEvidence,
) -> EligibilityResult:
    """Authorize ASK without consulting motion hard-rule permission.

    Every required fact is positive evidence.  Missing/UNKNOWN facts and
    malformed timing fail closed; the inequality is deliberately strict.
    """

    reasons = []
    boolean_requirements = {
        "AMBIGUITY_NOT_UNRESOLVED": evidence.ambiguity_unresolved,
        "AMBIGUITY_NOT_DECISION_RELEVANT": evidence.ambiguity_decision_relevant,
        "QUERY_CANNOT_CHANGE_FUTURE_ACTION": (
            evidence.query_changes_future_semantic_action
        ),
        "QUERY_ALREADY_OUTSTANDING": (
            None
            if evidence.query_outstanding is None
            else not evidence.query_outstanding
        ),
        "QUERY_BUDGET_UNAVAILABLE": evidence.query_budget_available,
        "PASSENGER_NOT_RESOLVABLE": evidence.passenger_resolvable,
        "PHYSICAL_SAFETY_NOT_PASSED": evidence.physical_safety_pass,
        "SAFE_HOLDING_UNAVAILABLE": evidence.safe_holding_available,
        "CLARIFICATION_NOT_TIMELY": evidence.clarification_state_timely,
    }
    evidence_complete = True
    for reason, value in boolean_requirements.items():
        if value is not True:
            reasons.append(reason if value is False else "UNKNOWN_" + reason)
            if value is None:
                evidence_complete = False
    if (
        not isinstance(evidence.effective_k, int)
        or isinstance(evidence.effective_k, bool)
    ):
        reasons.append("EFFECTIVE_K_UNKNOWN")
        evidence_complete = False
    elif evidence.effective_k < 2:
        reasons.append("EFFECTIVE_K_BELOW_2")

    timing = (
        evidence.t_available_s,
        evidence.t_answer_budget_s,
        evidence.t_fresh_replan_budget_s,
        evidence.t_activation_or_execution_margin_s,
        evidence.t_safety_margin_s,
    )
    if not all(_finite_nonnegative(value) for value in timing):
        reasons.append("TIMING_EVIDENCE_UNKNOWN_OR_INVALID")
        evidence_complete = False
        available = None
        required = None
    else:
        available = float(evidence.t_available_s)
        required = sum(float(value) for value in timing[1:])
        if not available > required:
            reasons.append("REMAINING_BUDGET_NOT_STRICTLY_GREATER_THAN_REQUIRED")
    return EligibilityResult(
        eligible=not reasons,
        evidence_complete=evidence_complete,
        reason_codes=("CLARIFICATION_ELIGIBLE",) if not reasons else tuple(reasons),
        available_budget_s=available,
        required_budget_s=required,
    )


@dataclass(frozen=True)
class MotionEligibilityEvidence:
    physical_safety_pass: Optional[bool]
    hard_rule_pass: Optional[bool]
    motion_valid: Optional[bool]
    selected_navigation_valid: Optional[bool]


@dataclass(frozen=True)
class PlanReadyHoldingEvidence:
    """Live evidence required to retain a selected plan without motion authority."""

    selected_state_valid: Optional[bool]
    physical_safety_pass: Optional[bool]
    hard_rule_evidence_available: Optional[bool]
    safe_holding_valid: Optional[bool]
    physical_progress_evidence_complete: Optional[bool]
    physical_progress_toward_commitment: Optional[bool]
    motion_execution_eligible: Optional[bool]


@dataclass(frozen=True)
class PreActivationFeasibilityEvidence:
    """Existing live evidence that the selected maneuver remains reachable.

    This is a geometric/topological feasibility gate, not a timer.  It reuses
    the selected plan identity snapshot, the existing route commitment
    boundary, and the already-frozen local planning horizon.
    """

    live_plan_identity_valid: Optional[bool]
    physical_progress_evidence_complete: Optional[bool]
    live_route_projection_valid: Optional[bool]
    directed_route_progress_continuous: Optional[bool]
    selected_opportunity_identity_valid: Optional[bool]
    selected_branch_topology_reachable: Optional[bool]
    selected_planning_capability_valid: Optional[bool]
    current_route_progress_m: Optional[float]
    frozen_selected_commitment_boundary_progress_m: Optional[float]
    effective_selected_commitment_boundary_progress_m: Optional[float]
    route_projection_uncertainty_m: Optional[float]


@dataclass(frozen=True)
class PreActivationFeasibilityAssessment:
    feasible: bool
    evidence_complete: bool
    remaining_commitment_slack_m: Optional[float]
    reason_codes: Tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def assess_pre_activation_feasibility(
    evidence: PreActivationFeasibilityEvidence,
) -> PreActivationFeasibilityAssessment:
    """Fail closed if baseline motion has made the selected maneuver infeasible."""

    reasons = []
    complete = True
    for reason, value in (
        ("LIVE_PLAN_IDENTITY_INVALID", evidence.live_plan_identity_valid),
        (
            "PHYSICAL_PROGRESS_EVIDENCE_INCOMPLETE",
            evidence.physical_progress_evidence_complete,
        ),
        ("LIVE_ROUTE_PROJECTION_INVALID", evidence.live_route_projection_valid),
        (
            "DIRECTED_ROUTE_PROGRESS_DISCONTINUOUS",
            evidence.directed_route_progress_continuous,
        ),
        (
            "SELECTED_OPPORTUNITY_IDENTITY_INVALID",
            evidence.selected_opportunity_identity_valid,
        ),
        (
            "SELECTED_BRANCH_TOPOLOGY_UNREACHABLE",
            evidence.selected_branch_topology_reachable,
        ),
        (
            "SELECTED_PLANNING_CAPABILITY_INVALID",
            evidence.selected_planning_capability_valid,
        ),
    ):
        if value is not True:
            reasons.append(reason if value is False else "UNKNOWN_" + reason)
            if value is None:
                complete = False

    numeric = (
        evidence.current_route_progress_m,
        evidence.frozen_selected_commitment_boundary_progress_m,
        evidence.effective_selected_commitment_boundary_progress_m,
        evidence.route_projection_uncertainty_m,
    )
    if not all(_finite_nonnegative(value) for value in numeric):
        reasons.append("PRE_ACTIVATION_FEASIBILITY_NUMERIC_EVIDENCE_UNKNOWN")
        complete = False
        commitment_slack = None
    else:
        current_progress = float(evidence.current_route_progress_m)
        frozen_boundary = float(
            evidence.frozen_selected_commitment_boundary_progress_m
        )
        commitment_boundary = float(
            evidence.effective_selected_commitment_boundary_progress_m
        )
        uncertainty = float(evidence.route_projection_uncertainty_m)
        if commitment_boundary > frozen_boundary:
            reasons.append("EFFECTIVE_COMMITMENT_BOUNDARY_EXPANDED")
        # Reuse the frozen decision-window predicate: progress must remain
        # strictly before boundary - calibrated uncertainty.  Boundary
        # uncertainty is not a grace distance after the maneuver is passed.
        commitment_slack = commitment_boundary - current_progress - uncertainty
        if commitment_slack <= 0.0:
            reasons.append("COMMITMENT_BOUNDARY_IRREVERSIBLY_PASSED")

    return PreActivationFeasibilityAssessment(
        feasible=not reasons,
        evidence_complete=complete,
        remaining_commitment_slack_m=commitment_slack,
        reason_codes=(
            ("PRE_ACTIVATION_SELECTED_MANEUVER_FEASIBLE",)
            if not reasons
            else tuple(reasons)
        ),
    )


def assess_plan_ready_holding(
    evidence: PlanReadyHoldingEvidence,
) -> EligibilityResult:
    """Retain PLAN_READY only under complete state-valid evidence.

    A moving/progressing vehicle is not described as stationary HOLD.  Its
    execution budget must consume even when the hard-rule motion gate is false.
    """

    reasons = []
    complete = True
    for reason, value in (
        ("SELECTED_STATE_INVALID", evidence.selected_state_valid),
        ("PHYSICAL_SAFETY_NOT_PASSED", evidence.physical_safety_pass),
        ("HARD_RULE_EVIDENCE_UNAVAILABLE", evidence.hard_rule_evidence_available),
        (
            "PHYSICAL_PROGRESS_EVIDENCE_INCOMPLETE",
            evidence.physical_progress_evidence_complete,
        ),
    ):
        if value is not True:
            reasons.append(reason if value is False else "UNKNOWN_" + reason)
            if value is None:
                complete = False
    if evidence.physical_progress_toward_commitment is None:
        reasons.append("PHYSICAL_PROGRESS_UNKNOWN")
        complete = False
    if evidence.motion_execution_eligible is None:
        reasons.append("MOTION_ELIGIBILITY_UNKNOWN")
        complete = False
    stationary_blocked = bool(
        evidence.motion_execution_eligible is False
        and evidence.physical_progress_toward_commitment is False
    )
    if stationary_blocked and evidence.safe_holding_valid is not True:
        reasons.append(
            "SAFE_HOLDING_INVALID"
            if evidence.safe_holding_valid is False
            else "UNKNOWN_SAFE_HOLDING_VALIDITY"
        )
        if evidence.safe_holding_valid is None:
            complete = False
    return EligibilityResult(
        eligible=not reasons,
        evidence_complete=complete,
        reason_codes=("PLAN_READY_STATE_VALID",) if not reasons else tuple(reasons),
    )


class ManeuverExecutionBudgetLedger:
    """Opportunity-time ledger; never pauses the clarification wall clock.

    The legacy default preserves V2.1/V2.3 wall-opportunity accounting.  V2.4
    selects ``SIMULATION_EXECUTION_OPPORTUNITY`` on the same ledger so that
    physical maneuver opportunity is derived from the frame-aligned CARLA
    timestamp rather than runtime throughput.
    """

    schema_version = "driveclarify.final_runtime.execution_budget_ledger.v1"
    WALL_CLOCK_DOMAIN = "MONOTONIC_WALL_OPPORTUNITY_TIME"
    SIMULATION_CLOCK_DOMAIN = "SIMULATION_EXECUTION_OPPORTUNITY"

    def __init__(
        self,
        total_budget_s: float,
        *,
        started_monotonic_s: float,
        initial_motion_execution_eligible: bool = False,
        clock_domain: str = WALL_CLOCK_DOMAIN,
        started_simulation_time_s: Optional[float] = None,
        started_frame_id: Optional[int] = None,
    ) -> None:
        if not _finite_nonnegative(total_budget_s) or float(total_budget_s) <= 0.0:
            raise ValueError("EXECUTION_BUDGET_INVALID")
        if not _finite_nonnegative(started_monotonic_s):
            raise ValueError("EXECUTION_BUDGET_CLOCK_INVALID")
        self.total_budget_s = float(total_budget_s)
        self.remaining_budget_s = float(total_budget_s)
        self.started_monotonic_s = float(started_monotonic_s)
        self.last_monotonic_s = float(started_monotonic_s)
        if clock_domain not in {
            self.WALL_CLOCK_DOMAIN,
            self.SIMULATION_CLOCK_DOMAIN,
        }:
            raise ValueError("EXECUTION_BUDGET_CLOCK_DOMAIN_INVALID")
        self.clock_domain = str(clock_domain)
        self.last_simulation_time_s: Optional[float] = None
        self.last_frame_id: Optional[int] = None
        if self.clock_domain == self.SIMULATION_CLOCK_DOMAIN:
            if not _finite_nonnegative(started_simulation_time_s):
                raise ValueError("EXECUTION_BUDGET_SIMULATION_CLOCK_INVALID")
            if type(started_frame_id) is not int or int(started_frame_id) < 0:
                raise ValueError("EXECUTION_BUDGET_FRAME_INVALID")
            self.last_simulation_time_s = float(started_simulation_time_s)
            self.last_frame_id = int(started_frame_id)
        self.consumed_budget_s = 0.0
        if initial_motion_execution_eligible not in (True, False):
            raise ValueError("INITIAL_MOTION_ELIGIBILITY_UNKNOWN")
        self._prior_motion_execution_eligible = bool(
            initial_motion_execution_eligible
        )
        self.history: list[dict[str, Any]] = []

    @property
    def exhausted(self) -> bool:
        return self.remaining_budget_s <= 0.0

    def update(
        self,
        *,
        observed_monotonic_s: float,
        motion_execution_eligible: Optional[bool],
        physical_progress_toward_commitment: Optional[bool],
        observed_simulation_time_s: Optional[float] = None,
        frame_id: Optional[int] = None,
    ) -> dict[str, Any]:
        if motion_execution_eligible not in (True, False):
            raise ValueError("MOTION_ELIGIBILITY_UNKNOWN")
        if physical_progress_toward_commitment not in (True, False):
            raise ValueError("PHYSICAL_PROGRESS_UNKNOWN")
        now = _finite_nonnegative(observed_monotonic_s)
        if not now:
            raise ValueError("EXECUTION_BUDGET_CLOCK_INVALID")
        normalized_now = float(observed_monotonic_s)
        if normalized_now < self.last_monotonic_s:
            raise ValueError("EXECUTION_BUDGET_CLOCK_REGRESSED")
        elapsed_wall = normalized_now - self.last_monotonic_s
        prior_motion_eligible = self._prior_motion_execution_eligible
        simulation_delta: Optional[float] = None
        if self.clock_domain == self.SIMULATION_CLOCK_DOMAIN:
            if not _finite_nonnegative(observed_simulation_time_s):
                raise ValueError("EXECUTION_BUDGET_SIMULATION_CLOCK_INVALID")
            if type(frame_id) is not int or int(frame_id) < 0:
                raise ValueError("EXECUTION_BUDGET_FRAME_INVALID")
            assert self.last_simulation_time_s is not None
            assert self.last_frame_id is not None
            if int(frame_id) <= self.last_frame_id:
                raise ValueError("EXECUTION_BUDGET_FRAME_NOT_ORDERED")
            normalized_simulation = float(observed_simulation_time_s)
            if normalized_simulation <= self.last_simulation_time_s:
                raise ValueError(
                    "EXECUTION_BUDGET_SIMULATION_CLOCK_NOT_ADVANCED"
                )
            simulation_delta = normalized_simulation - self.last_simulation_time_s
            # In V2.4 the current production observation supplies the frozen
            # safety/rule motion-eligibility verdict for this interval.  Pose
            # progress remains diagnostic and can never substitute for time.
            consumes = bool(motion_execution_eligible)
            consumed = simulation_delta if consumes else 0.0
            self.last_simulation_time_s = normalized_simulation
            self.last_frame_id = int(frame_id)
        else:
            # Legacy behavior is intentionally byte-semantic compatible: the
            # prior eligibility owns the interval and observed physical
            # progress proves that the just-finished interval was opportunity.
            consumes = bool(
                prior_motion_eligible or physical_progress_toward_commitment
            )
            consumed = elapsed_wall if consumes else 0.0
        self.consumed_budget_s += consumed
        self.remaining_budget_s = max(0.0, self.total_budget_s - self.consumed_budget_s)
        self.last_monotonic_s = normalized_now
        self._prior_motion_execution_eligible = bool(motion_execution_eligible)
        row = {
            "observed_monotonic_s": normalized_now,
            "elapsed_wall_s": elapsed_wall,
            "clock_domain": self.clock_domain,
            "observed_simulation_time_s": (
                None
                if observed_simulation_time_s is None
                else float(observed_simulation_time_s)
            ),
            "simulation_delta_s": simulation_delta,
            "frame_id": frame_id,
            "motion_execution_eligible": motion_execution_eligible,
            "interval_motion_execution_eligible": prior_motion_eligible,
            "physical_progress_toward_commitment": (
                physical_progress_toward_commitment
            ),
            "execution_budget_consumed_this_update_s": consumed,
            "execution_budget_remaining_s": self.remaining_budget_s,
            "clarification_clock_paused": False,
            "reason_code": (
                "EXECUTION_BUDGET_CONSUMED_SIMULATION_OPPORTUNITY"
                if consumes
                and self.clock_domain == self.SIMULATION_CLOCK_DOMAIN
                else "EXECUTION_BUDGET_CONSUMED_ELIGIBLE_OR_PROGRESS"
                if consumes
                else "EXECUTION_BUDGET_SUSPENDED_MOTION_INELIGIBLE"
                if self.clock_domain == self.SIMULATION_CLOCK_DOMAIN
                else "EXECUTION_BUDGET_SUSPENDED_RULE_FORCED_STATIONARY_HOLD"
            ),
        }
        self.history.append(row)
        return row

    def summary(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "total_budget_s": self.total_budget_s,
            "consumed_budget_s": self.consumed_budget_s,
            "remaining_budget_s": self.remaining_budget_s,
            "exhausted": self.exhausted,
            "clock_domain": self.clock_domain,
            "started_monotonic_s": self.started_monotonic_s,
            "last_monotonic_s": self.last_monotonic_s,
            "last_simulation_time_s": self.last_simulation_time_s,
            "last_frame_id": self.last_frame_id,
            "clarification_clock": "REAL_MONOTONIC_WALL_CLOCK_UNCHANGED",
            "history": list(self.history),
        }


def assess_motion_eligibility(
    evidence: MotionEligibilityEvidence,
) -> EligibilityResult:
    reasons = []
    complete = True
    for reason, value in (
        ("PHYSICAL_SAFETY_NOT_PASSED", evidence.physical_safety_pass),
        ("HARD_RULE_NOT_PASSED", evidence.hard_rule_pass),
        ("MOTION_NOT_VALID", evidence.motion_valid),
        ("SELECTED_NAVIGATION_NOT_VALID", evidence.selected_navigation_valid),
    ):
        if value is not True:
            reasons.append(reason if value is False else "UNKNOWN_" + reason)
            if value is None:
                complete = False
    return EligibilityResult(
        eligible=not reasons,
        evidence_complete=complete,
        reason_codes=("MOTION_EXECUTION_ELIGIBLE",) if not reasons else tuple(reasons),
    )


class PlanActivationState(str, Enum):
    EMPTY = "EMPTY"
    FRESH_REPLAN_REQUIRED = "FRESH_REPLAN_REQUIRED"
    SELECTED_PLAN_READY = "SELECTED_PLAN_READY"
    EXECUTION_ACTIVE = "EXECUTION_ACTIVE"
    INVALIDATED = "INVALIDATED"


@dataclass(frozen=True)
class PlanIdentitySnapshot:
    candidate_id: str
    interpretation_id: str
    obligation_identity: str
    obligation_digest: str
    branch_identity: str
    branch_digest: str
    execution_location_identity: str
    navigation_context_identity: str
    global_destination_identity: str
    mission_context_digest: str
    route_version: str
    environment_digest: str
    scene_compatibility_digest: str
    instruction: str

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if not isinstance(value, str) or not value.strip():
                raise ValueError(name.upper() + "_MISSING")


class PlanActivationGate:
    """Identity-only PLAN_READY gate; never stores or executes control."""

    schema_version = "driveclarify.method_v2_1.plan_activation_gate.v1"

    def __init__(self) -> None:
        self.state = PlanActivationState.EMPTY
        self.snapshot: PlanIdentitySnapshot | None = None
        self.ready_frame_id: int | None = None
        self.activation_frame_id: int | None = None
        self.reason_code = "NOT_STAGED"
        self._last_revalidated_frame_id: int | None = None
        self._last_feasibility_validated_frame_id: int | None = None
        self.transitions: list[dict[str, Any]] = []

    def _transition(self, state: PlanActivationState, reason: str, frame: Any) -> None:
        previous = self.state
        self.state = state
        self.reason_code = str(reason)
        self.transitions.append(
            {"from": previous.value, "to": state.value, "reason_code": reason, "frame_id": frame}
        )

    def stage(self, snapshot: PlanIdentitySnapshot, *, frame_id: int) -> None:
        if self.state not in {PlanActivationState.EMPTY, PlanActivationState.FRESH_REPLAN_REQUIRED}:
            raise RuntimeError("PLAN_READY_ALREADY_STAGED")
        self.snapshot = snapshot
        self._last_revalidated_frame_id = None
        self._last_feasibility_validated_frame_id = None
        self.ready_frame_id = int(frame_id)
        self._transition(
            PlanActivationState.SELECTED_PLAN_READY,
            "FRESH_SELECTED_PLAN_COMPUTED_NO_MOTION_AUTHORITY",
            int(frame_id),
        )

    def revalidate(self, current: PlanIdentitySnapshot, *, frame_id: int) -> EligibilityResult:
        if self.state is not PlanActivationState.SELECTED_PLAN_READY or self.snapshot is None:
            return EligibilityResult(False, False, ("PLAN_NOT_READY",))
        expected = asdict(self.snapshot)
        observed = asdict(current)
        self._last_feasibility_validated_frame_id = None
        changed = tuple(name for name in expected if expected[name] != observed[name])
        if not changed:
            self._last_revalidated_frame_id = int(frame_id)
            return EligibilityResult(True, True, ("STALE_PLAN_REVALIDATION_PASS",))
        self._last_revalidated_frame_id = None
        if "global_destination_identity" in changed or "mission_context_digest" in changed:
            self._transition(
                PlanActivationState.INVALIDATED,
                "GLOBAL_DESTINATION_OR_MISSION_CHANGED",
                int(frame_id),
            )
        else:
            self._transition(
                PlanActivationState.FRESH_REPLAN_REQUIRED,
                "PLAN_CONTEXT_CHANGED:" + ",".join(changed),
                int(frame_id),
            )
        return EligibilityResult(
            False,
            True,
            tuple("STALE_PLAN_" + name.upper() + "_CHANGED" for name in changed),
        )

    def record_pre_activation_feasibility(
        self,
        assessment: PreActivationFeasibilityAssessment,
        *,
        frame_id: int,
    ) -> None:
        if self.state is not PlanActivationState.SELECTED_PLAN_READY:
            raise RuntimeError("PLAN_NOT_READY_FOR_FEASIBILITY_REVALIDATION")
        if not isinstance(assessment, PreActivationFeasibilityAssessment):
            raise TypeError("PRE_ACTIVATION_FEASIBILITY_ASSESSMENT_INVALID")
        if not assessment.feasible or not assessment.evidence_complete:
            self._last_feasibility_validated_frame_id = None
            raise RuntimeError("PRE_ACTIVATION_FEASIBILITY_NOT_PASSED")
        self._last_feasibility_validated_frame_id = int(frame_id)

    def activate(
        self,
        *,
        frame_id: int,
        require_pre_activation_feasibility: bool = False,
    ) -> None:
        if self.state is not PlanActivationState.SELECTED_PLAN_READY:
            raise RuntimeError("PLAN_NOT_READY_FOR_ACTIVATION")
        if self._last_revalidated_frame_id != int(frame_id):
            raise RuntimeError("SAME_FRAME_FULL_REVALIDATION_REQUIRED")
        if (
            require_pre_activation_feasibility
            and self._last_feasibility_validated_frame_id != int(frame_id)
        ):
            raise RuntimeError("SAME_FRAME_PRE_ACTIVATION_FEASIBILITY_REQUIRED")
        self.activation_frame_id = int(frame_id)
        self._transition(
            PlanActivationState.EXECUTION_ACTIVE,
            "MOTION_ELIGIBLE_AND_STALE_REVALIDATION_PASSED",
            int(frame_id),
        )

    def require_replan(self, reason: str, *, frame_id: int) -> None:
        if self.state is not PlanActivationState.SELECTED_PLAN_READY:
            raise RuntimeError("PLAN_NOT_READY_FOR_REPLAN_TRANSITION")
        self._transition(
            PlanActivationState.FRESH_REPLAN_REQUIRED,
            str(reason),
            int(frame_id),
        )

    def invalidate(self, reason: str, *, frame_id: int) -> None:
        if self.state is not PlanActivationState.SELECTED_PLAN_READY:
            raise RuntimeError("PLAN_NOT_READY_FOR_INVALIDATION")
        self._transition(
            PlanActivationState.INVALIDATED,
            str(reason),
            int(frame_id),
        )

    def invalidate_pre_activation(self, reason: str, *, frame_id: int) -> None:
        if self.state not in {
            PlanActivationState.SELECTED_PLAN_READY,
            PlanActivationState.FRESH_REPLAN_REQUIRED,
        }:
            raise RuntimeError("PLAN_NOT_PRE_ACTIVE_FOR_INVALIDATION")
        self._last_feasibility_validated_frame_id = None
        self._transition(
            PlanActivationState.INVALIDATED,
            str(reason),
            int(frame_id),
        )

    def invalidate_active_transition(self, reason: str, *, frame_id: int) -> None:
        """Roll back a synchronous V2.3 activation that failed before publish."""

        if self.state is not PlanActivationState.EXECUTION_ACTIVE:
            raise RuntimeError("PLAN_NOT_IN_ACTIVE_TRANSITION_FOR_INVALIDATION")
        self.activation_frame_id = None
        self._last_feasibility_validated_frame_id = None
        self._transition(
            PlanActivationState.INVALIDATED,
            str(reason),
            int(frame_id),
        )

    def summary(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "state": self.state.value,
            "reason_code": self.reason_code,
            "ready_frame_id": self.ready_frame_id,
            "activation_frame_id": self.activation_frame_id,
            "snapshot": None if self.snapshot is None else asdict(self.snapshot),
            "transitions": list(self.transitions),
            "motion_authority_while_plan_ready": False,
            "last_revalidated_frame_id": self._last_revalidated_frame_id,
            "last_feasibility_validated_frame_id": (
                self._last_feasibility_validated_frame_id
            ),
        }
