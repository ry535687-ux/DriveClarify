"""Fail-closed M2B policy for Decision Evidence Contract V2.0."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

from .types import (
    CandidateRelationshipV2,
    ClarificationUrgencyV2,
    DecisionEvidenceBundleV2,
)


class DecisionV2(str, Enum):
    ACT = "ACT"
    ACT_SHARED = "ACT_SHARED"
    ASK = "ASK"
    WAIT = "WAIT"
    FALLBACK = "FALLBACK"


@dataclass(frozen=True)
class DecisionContextV2:
    evidence: DecisionEvidenceBundleV2
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


@dataclass(frozen=True)
class DecisionRecommendationV2:
    decision: DecisionV2
    relationship: CandidateRelationshipV2
    target_type: Optional[str]
    authority_subject_type: Optional[str]
    reason_codes: Tuple[str, ...]

    @property
    def relation(self) -> CandidateRelationshipV2:
        """Compatibility name used by the append-only V1 audit projector."""

        return self.relationship


def decide_v2(context: DecisionContextV2) -> DecisionRecommendationV2:
    """Map independently verified V2 evidence to one bounded runtime decision."""

    evidence = context.evidence
    relationship = evidence.relationship

    # An in-flight passenger transaction owns the next decision.  A valid
    # holding authority produces WAIT; otherwise the policy fails closed.
    if (
        context.active_query
        and context.verified_holding_available
        and context.hard_safety_gate is True
        and context.hard_rule_gate is True
    ):
        return DecisionRecommendationV2(
            decision=DecisionV2.WAIT,
            relationship=relationship,
            target_type=None,
            authority_subject_type=None,
            reason_codes=(
                "ACTIVE_QUERY_WITH_VERIFIED_EXISTING_HOLDING_AUTHORITY",
            ),
        )

    equivalent_shared_gates = (
        context.semantic_state == "UNRESOLVED",
        context.active_candidate_count >= 2,
        context.multiple_plausible_interpretations,
        evidence.authorization_eligible,
        relationship
        is CandidateRelationshipV2.CURRENT_AND_FUTURE_EQUIVALENT,
        evidence.current_executable.authorization_eligible,
        evidence.future_obligation.authorization_eligible,
        evidence.shared_action_lease.authorization_eligible,
        context.hard_safety_gate is True,
        context.hard_rule_gate is True,
        not context.active_query,
        not context.verified_holding_available,
    )
    if all(equivalent_shared_gates):
        return DecisionRecommendationV2(
            decision=DecisionV2.ACT_SHARED,
            relationship=relationship,
            target_type="EQUIVALENCE_CLASS",
            authority_subject_type="SHARED_EQUIVALENCE_CLASS",
            reason_codes=(
                "CURRENT_AND_FUTURE_EQUIVALENT_SHARED_ACTION_AUTHORIZED",
                "BOUNDED_LOCAL_EXECUTABLE_LEASE",
                "PRESERVE_UNRESOLVED_SEMANTICS",
            ),
        )

    future_divergent_shared_gates = (
        context.semantic_state == "UNRESOLVED",
        context.active_candidate_count >= 2,
        context.multiple_plausible_interpretations,
        evidence.authorization_eligible,
        relationship
        is CandidateRelationshipV2.CURRENTLY_EQUIVALENT_FUTURE_DIVERGENT,
        evidence.shared_action_lease.authorization_eligible,
        evidence.clarification_window.authorization_eligible,
        evidence.clarification_window.urgency
        is ClarificationUrgencyV2.DEFER_CLARIFICATION,
        context.hard_safety_gate is True,
        context.hard_rule_gate is True,
        not context.active_query,
        not context.verified_holding_available,
    )
    if all(future_divergent_shared_gates):
        return DecisionRecommendationV2(
            decision=DecisionV2.ACT_SHARED,
            relationship=relationship,
            target_type="EQUIVALENCE_CLASS",
            authority_subject_type="SHARED_EQUIVALENCE_CLASS",
            reason_codes=(
                "ALL_V2_ACT_SHARED_GATES_VERIFIED",
                "BOUNDED_LOCAL_EXECUTABLE_LEASE",
                "PRESERVE_UNRESOLVED_SEMANTICS",
            ),
        )

    ask_relationship = relationship in {
        CandidateRelationshipV2.CURRENTLY_EQUIVALENT_FUTURE_DIVERGENT,
        CandidateRelationshipV2.CURRENT_ACTION_DIVERGENT,
    }
    ask_gates = (
        context.semantic_state == "UNRESOLVED",
        context.active_candidate_count >= 2,
        context.multiple_plausible_interpretations,
        evidence.authorization_eligible,
        ask_relationship,
        evidence.future_obligation.authorization_eligible,
        evidence.clarification_window.authorization_eligible,
        evidence.clarification_window.urgency
        is ClarificationUrgencyV2.CLARIFY_NOW,
        context.answer_changes_decision,
        context.query_budget_available,
        context.passenger_resolvable,
        not context.active_query,
        context.hard_safety_gate is True,
        context.hard_rule_gate is True,
    )
    if all(ask_gates):
        return DecisionRecommendationV2(
            decision=DecisionV2.ASK,
            relationship=relationship,
            target_type=None,
            authority_subject_type=None,
            reason_codes=(
                "ALL_V2_ASK_GATES_VERIFIED",
                "CLARIFICATION_START_BOUNDARY_REACHED",
            ),
        )

    return DecisionRecommendationV2(
        decision=DecisionV2.FALLBACK,
        relationship=relationship,
        target_type=None,
        authority_subject_type=None,
        reason_codes=("DECISION_EVIDENCE_V2_GATES_FAIL_CLOSED",),
    )


__all__ = [
    "DecisionContextV2",
    "DecisionRecommendationV2",
    "DecisionV2",
    "decide_v2",
]
