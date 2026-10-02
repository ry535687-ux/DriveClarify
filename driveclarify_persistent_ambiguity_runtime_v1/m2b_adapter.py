"""Lossless two-axis M2B adapter for persistent ambiguity."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple


class AxisValue(str, Enum):
    CURRENT_ACTION_EQUIVALENT = "CURRENT_ACTION_EQUIVALENT"
    CURRENT_ACTION_DIVERGENT = "CURRENT_ACTION_DIVERGENT"
    FUTURE_DIVERGENT = "FUTURE_DIVERGENT"
    NO_MATERIAL_DIVERGENCE = "NO_MATERIAL_DIVERGENCE"
    UNKNOWN = "UNKNOWN"


class LosslessRelation(str, Enum):
    CURRENTLY_EQUIVALENT_FUTURE_DIVERGENT = "CURRENTLY_EQUIVALENT_FUTURE_DIVERGENT"
    CURRENTLY_DIVERGENT = "CURRENTLY_DIVERGENT"
    NO_MATERIAL_DIVERGENCE = "NO_MATERIAL_DIVERGENCE"
    UNKNOWN_OR_INSUFFICIENT_EVIDENCE = "UNKNOWN_OR_INSUFFICIENT_EVIDENCE"


class PersistentDecision(str, Enum):
    ACT = "ACT"
    ACT_SHARED = "ACT_SHARED"
    ACT_UNIQUE = "ACT_UNIQUE"
    ASK = "ASK"
    WAIT = "WAIT"
    FALLBACK = "FALLBACK"


@dataclass(frozen=True)
class PersistentDecisionContext:
    active_candidate_count: int
    semantic_state: str
    current_action_relation: AxisValue
    future_obligation_relation: AxisValue
    evidence_fresh: Optional[bool]
    full_plan_coverage: Optional[bool]
    alignment_verified: Optional[bool]
    shared_action_safe: Optional[bool]
    recoverable: Optional[bool]
    latest_safe_slack_positive: Optional[bool]
    decision_deadline_available: Optional[bool]
    decision_deadline_crossed: Optional[bool]
    hard_safety_gate: Optional[bool]
    hard_rule_gate: Optional[bool]
    active_query: bool
    active_holding_lease: bool
    multiple_plausible_interpretations: bool
    material_consequence_divergence: Optional[bool]
    answer_changes_decision: bool
    positive_query_value: Optional[bool]
    query_budget_available: bool
    answer_likely_before_deadline: Optional[bool]
    passenger_resolvable: bool
    verified_holding_available: bool


@dataclass(frozen=True)
class PersistentDecisionRecommendation:
    decision: PersistentDecision
    relation: LosslessRelation
    target_type: Optional[str]
    authority_subject_type: Optional[str]
    reason_codes: Tuple[str, ...]


@dataclass(frozen=True)
class UniqueDecisionContext:
    semantic_state: str
    active_candidate_count: int
    matched_answer: bool
    old_bundle_invalidated: bool
    latest_observation_replan: bool
    candidate_fresh: bool
    source_identity_aligned: bool
    hard_safety_gate: bool
    hard_rule_gate: bool
    active_query: bool
    active_holding_lease: bool


@dataclass(frozen=True)
class ConvergedUniqueDecisionContext:
    semantic_state: str
    active_candidate_count: int
    convergence_evidence_valid: bool
    old_bundle_invalidated: bool
    latest_observation_replan: bool
    candidate_fresh: bool
    source_identity_aligned: bool
    hard_safety_gate: bool
    hard_rule_gate: bool
    active_query: bool
    active_holding_lease: bool


def classify_lossless_relation(
    current: AxisValue, future: AxisValue
) -> LosslessRelation:
    if current is AxisValue.CURRENT_ACTION_EQUIVALENT and future is AxisValue.FUTURE_DIVERGENT:
        return LosslessRelation.CURRENTLY_EQUIVALENT_FUTURE_DIVERGENT
    if current is AxisValue.CURRENT_ACTION_DIVERGENT and future is AxisValue.FUTURE_DIVERGENT:
        return LosslessRelation.CURRENTLY_DIVERGENT
    if current is AxisValue.CURRENT_ACTION_EQUIVALENT and future is AxisValue.NO_MATERIAL_DIVERGENCE:
        return LosslessRelation.NO_MATERIAL_DIVERGENCE
    return LosslessRelation.UNKNOWN_OR_INSUFFICIENT_EVIDENCE


def decide_persistent(context: PersistentDecisionContext) -> PersistentDecisionRecommendation:
    relation = classify_lossless_relation(
        context.current_action_relation, context.future_obligation_relation
    )
    equivalent_shared_gates = (
        context.active_candidate_count >= 2,
        context.semantic_state == "UNRESOLVED",
        relation is LosslessRelation.NO_MATERIAL_DIVERGENCE,
        context.evidence_fresh is True,
        context.full_plan_coverage is True,
        context.alignment_verified is True,
        context.shared_action_safe is True,
        context.recoverable is True,
        context.hard_safety_gate is True,
        context.hard_rule_gate is True,
        context.active_query is False,
        context.active_holding_lease is False,
        context.multiple_plausible_interpretations,
    )
    if all(equivalent_shared_gates):
        return PersistentDecisionRecommendation(
            PersistentDecision.ACT_SHARED,
            relation,
            "EQUIVALENCE_CLASS",
            "SHARED_EQUIVALENCE_CLASS",
            (
                "CURRENT_AND_FUTURE_EQUIVALENT_SHARED_ACTION_AUTHORIZED",
                "PRESERVE_UNRESOLVED_SEMANTICS",
            ),
        )
    shared_gates = (
        context.active_candidate_count >= 2,
        context.semantic_state == "UNRESOLVED",
        relation is LosslessRelation.CURRENTLY_EQUIVALENT_FUTURE_DIVERGENT,
        context.evidence_fresh is True,
        context.full_plan_coverage is True,
        context.alignment_verified is True,
        context.shared_action_safe is True,
        context.recoverable is True,
        context.latest_safe_slack_positive is True,
        context.decision_deadline_available is True,
        context.decision_deadline_crossed is False,
        context.hard_safety_gate is True,
        context.hard_rule_gate is True,
        context.active_query is False,
        context.active_holding_lease is False,
    )
    if all(shared_gates):
        return PersistentDecisionRecommendation(
            PersistentDecision.ACT_SHARED,
            relation,
            "EQUIVALENCE_CLASS",
            "SHARED_EQUIVALENCE_CLASS",
            ("ALL_ACT_SHARED_GATES_VERIFIED", "PRESERVE_UNRESOLVED_SEMANTICS"),
        )

    ask_gates = (
        context.multiple_plausible_interpretations,
        context.material_consequence_divergence,
        context.answer_changes_decision,
        context.positive_query_value,
        context.query_budget_available,
        not context.active_query,
        context.answer_likely_before_deadline,
        context.passenger_resolvable,
        context.hard_safety_gate is True,
        context.hard_rule_gate is True,
    )
    if all(ask_gates):
        return PersistentDecisionRecommendation(
            PersistentDecision.ASK, relation, None, None, ("ALL_ASK_GATES_VERIFIED",)
        )
    if (
        context.active_query
        and context.verified_holding_available
        and context.hard_safety_gate is True
        and context.hard_rule_gate is True
    ):
        return PersistentDecisionRecommendation(
            PersistentDecision.WAIT,
            relation,
            None,
            None,
            ("ACTIVE_QUERY_WITH_VERIFIED_EXISTING_HOLDING_AUTHORITY",),
        )
    return PersistentDecisionRecommendation(
        PersistentDecision.FALLBACK,
        relation,
        None,
        None,
        ("PERSISTENT_RELATION_GATES_FAIL_CLOSED",),
    )


def decide_unique_after_answer(
    context: UniqueDecisionContext,
) -> PersistentDecisionRecommendation:
    gates = (
        context.semantic_state == "ANSWER_RECEIVED_PENDING_LATEST_REPLAN",
        context.active_candidate_count == 1,
        context.matched_answer,
        context.old_bundle_invalidated,
        context.latest_observation_replan,
        context.candidate_fresh,
        context.source_identity_aligned,
        context.hard_safety_gate,
        context.hard_rule_gate,
        not context.active_query,
        not context.active_holding_lease,
    )
    if all(gates):
        return PersistentDecisionRecommendation(
            PersistentDecision.ACT_UNIQUE,
            LosslessRelation.CURRENTLY_DIVERGENT,
            "UNIQUE_CANDIDATE",
            "UNIQUE_CANDIDATE",
            ("POST_ANSWER_LATEST_REPLAN_ALL_GATES_VERIFIED",),
        )
    return PersistentDecisionRecommendation(
        PersistentDecision.FALLBACK,
        LosslessRelation.UNKNOWN_OR_INSUFFICIENT_EVIDENCE,
        None,
        None,
        ("POST_ANSWER_UNIQUE_GATES_FAIL_CLOSED",),
    )


def decide_unique_after_convergence(
    context: ConvergedUniqueDecisionContext,
) -> PersistentDecisionRecommendation:
    gates = (
        context.semantic_state == "UNRESOLVED",
        context.active_candidate_count == 1,
        context.convergence_evidence_valid,
        context.old_bundle_invalidated,
        context.latest_observation_replan,
        context.candidate_fresh,
        context.source_identity_aligned,
        context.hard_safety_gate,
        context.hard_rule_gate,
        not context.active_query,
        not context.active_holding_lease,
    )
    if all(gates):
        return PersistentDecisionRecommendation(
            PersistentDecision.ACT,
            LosslessRelation.NO_MATERIAL_DIVERGENCE,
            "UNIQUE_CANDIDATE",
            "UNIQUE_CANDIDATE",
            (
                "EVIDENCE_CONVERGENCE_LATEST_REPLAN_ALL_GATES_VERIFIED",
                "OLD_AMBIGUOUS_BUNDLE_INVALIDATED",
            ),
        )
    return PersistentDecisionRecommendation(
        PersistentDecision.FALLBACK,
        LosslessRelation.UNKNOWN_OR_INSUFFICIENT_EVIDENCE,
        None,
        None,
        ("EVIDENCE_CONVERGENCE_UNIQUE_GATES_FAIL_CLOSED",),
    )


__all__ = [
    "AxisValue",
    "ConvergedUniqueDecisionContext",
    "LosslessRelation",
    "PersistentDecision",
    "PersistentDecisionContext",
    "PersistentDecisionRecommendation",
    "UniqueDecisionContext",
    "classify_lossless_relation",
    "decide_persistent",
    "decide_unique_after_convergence",
    "decide_unique_after_answer",
]
