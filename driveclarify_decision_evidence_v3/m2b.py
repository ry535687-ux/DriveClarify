"""Lossless fail-closed M2B policy for Decision Evidence V3.1."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

from .types import (
    ClarificationStateV3,
    CompatibilityRelationshipV3,
    CurrentActionRelationV3,
    DecisionEvidenceBundleV3,
    FutureObligationRelationV3,
    RecoverabilityStatusV3,
    RefreshGuaranteeStateV3,
)
from driveclarify_method_v2_1 import (
    ClarificationEligibilityEvidence,
    assess_clarification_eligibility,
)


class DecisionV3(str, Enum):
    ACT = "ACT"
    ACT_SHARED = "ACT_SHARED"
    ASK = "ASK"
    WAIT = "WAIT"
    FALLBACK = "FALLBACK"


@dataclass(frozen=True)
class DecisionContextV3:
    evidence: DecisionEvidenceBundleV3
    semantic_state: str
    active_candidate_count: int
    multiple_plausible_interpretations: bool
    answer_changes_decision: bool
    query_budget_available: bool
    passenger_resolvable: bool
    active_query: bool
    verified_holding_available: bool
    hard_safety_gate: Optional[bool]
    hard_rule_gate: Optional[bool]
    unique_plan_authorized: bool = False
    rule_gate_decoupled_clarification: bool = False
    safe_holding_available: Optional[bool] = None
    clarification_t_available_s: Optional[float] = None
    clarification_answer_budget_s: Optional[float] = None
    clarification_fresh_replan_budget_s: Optional[float] = None
    clarification_activation_margin_s: Optional[float] = None
    clarification_safety_margin_s: Optional[float] = None


@dataclass(frozen=True)
class DecisionRecommendationV3:
    decision: DecisionV3
    relationship: CompatibilityRelationshipV3
    target_type: Optional[str]
    authority_subject_type: Optional[str]
    reason_codes: Tuple[str, ...]

    @property
    def relation(self) -> CompatibilityRelationshipV3:
        return self.relationship


def decide_v3(context: DecisionContextV3) -> DecisionRecommendationV3:
    evidence = context.evidence
    relationship = evidence.compatibility_relationship
    if (
        context.active_candidate_count == 1
        and context.semantic_state in {"NO_AMBIGUITY", "RESOLVED"}
        and context.unique_plan_authorized
        and context.hard_safety_gate is True and context.hard_rule_gate is True
        and not context.active_query and not context.verified_holding_available
    ):
        return DecisionRecommendationV3(DecisionV3.ACT, relationship, "UNIQUE", "BASELINE_CONTROL", ("V3_K1_UNIQUE_PLAN_AUTHORIZED",))

    wait_motion_rule_satisfied = bool(
        context.rule_gate_decoupled_clarification
        or context.hard_rule_gate is True
    )
    if (
        context.active_query and context.verified_holding_available
        and context.hard_safety_gate is True and wait_motion_rule_satisfied
    ):
        return DecisionRecommendationV3(
            DecisionV3.WAIT,
            relationship,
            None,
            None,
            (
                "ACTIVE_QUERY_WITH_VERIFIED_EXISTING_HOLDING_AUTHORITY",
                "INFORMATION_WAIT_INDEPENDENT_OF_MOTION_RULE_GATE"
                if context.rule_gate_decoupled_clarification
                else "LEGACY_MOTION_RULE_GATE_PASSED",
            ),
        )

    ask_gates = (
        context.semantic_state == "UNRESOLVED",
        context.active_candidate_count >= 2,
        context.multiple_plausible_interpretations,
        evidence.future_obligation.relation is FutureObligationRelationV3.DIVERGENT,
        evidence.future_obligation.authorization_eligible,
        evidence.clarification.state is ClarificationStateV3.CLARIFY_NOW,
        context.answer_changes_decision,
        context.query_budget_available,
        context.passenger_resolvable,
        not context.active_query,
        context.hard_safety_gate is True,
        context.hard_rule_gate is True,
    )
    if context.rule_gate_decoupled_clarification:
        clarification = assess_clarification_eligibility(
            ClarificationEligibilityEvidence(
                ambiguity_unresolved=context.semantic_state == "UNRESOLVED",
                ambiguity_decision_relevant=bool(
                    evidence.future_obligation.relation
                    is FutureObligationRelationV3.DIVERGENT
                    and evidence.future_obligation.authorization_eligible
                ),
                effective_k=context.active_candidate_count,
                query_changes_future_semantic_action=context.answer_changes_decision,
                query_outstanding=context.active_query,
                query_budget_available=context.query_budget_available,
                passenger_resolvable=context.passenger_resolvable,
                physical_safety_pass=context.hard_safety_gate,
                safe_holding_available=context.safe_holding_available,
                clarification_state_timely=bool(
                    evidence.clarification.state
                    is ClarificationStateV3.CLARIFY_NOW
                    and evidence.clarification.authorization_eligible
                ),
                t_available_s=context.clarification_t_available_s,
                t_answer_budget_s=context.clarification_answer_budget_s,
                t_fresh_replan_budget_s=(
                    context.clarification_fresh_replan_budget_s
                ),
                t_activation_or_execution_margin_s=(
                    context.clarification_activation_margin_s
                ),
                t_safety_margin_s=context.clarification_safety_margin_s,
            )
        )
        if clarification.eligible:
            return DecisionRecommendationV3(
                DecisionV3.ASK,
                relationship,
                None,
                None,
                (
                    "ALL_V2_1_CLARIFICATION_GATES_VERIFIED",
                    "INFORMATION_ACTION_INDEPENDENT_OF_MOTION_RULE_GATE",
                ),
            )
    elif all(ask_gates):
        return DecisionRecommendationV3(DecisionV3.ASK, relationship, None, None, ("ALL_V3_ASK_GATES_VERIFIED", "KNOWN_FUTURE_DIVERGENCE_CLARIFY_NOW"))

    clarification_allows_shared = bool(
        evidence.clarification.state is ClarificationStateV3.NOT_NEEDED_YET
        or (
            evidence.clarification.state is ClarificationStateV3.UNKNOWN
            and evidence.clarification.bounded_unknown_safe_until_refresh
        )
    )
    shared_gates = (
        context.semantic_state == "UNRESOLVED",
        context.active_candidate_count >= 2,
        context.multiple_plausible_interpretations,
        evidence.current_action.relation is CurrentActionRelationV3.SHARED,
        evidence.current_action.authorization_eligible,
        evidence.shared_action_lease.valid,
        evidence.shared_action_lease.authorization_eligible,
        evidence.recoverability.status is RecoverabilityStatusV3.RECOVERABLE,
        evidence.recoverability.all_candidates_recoverable,
        evidence.refresh_guarantee.state is RefreshGuaranteeStateV3.GUARANTEED,
        clarification_allows_shared,
        context.hard_safety_gate is True,
        context.hard_rule_gate is True,
        not context.active_query,
        not context.verified_holding_available,
    )
    if all(shared_gates):
        return DecisionRecommendationV3(
            DecisionV3.ACT_SHARED, relationship, "EQUIVALENCE_CLASS",
            "SHARED_EQUIVALENCE_CLASS",
            ("ALL_V3_MINIMUM_SUFFICIENT_ACT_SHARED_GATES_VERIFIED", "BOUNDED_PRECOMMITMENT_REFRESH_GUARANTEED", "PRESERVE_UNRESOLVED_SEMANTICS"),
        )
    return DecisionRecommendationV3(DecisionV3.FALLBACK, relationship, None, None, ("DECISION_EVIDENCE_V3_GATES_FAIL_CLOSED",))


__all__ = ["DecisionContextV3", "DecisionRecommendationV3", "DecisionV3", "decide_v3"]
