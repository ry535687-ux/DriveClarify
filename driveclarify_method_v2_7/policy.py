"""Pure ACT/ASK/WAIT/FALLBACK policy for Method V2.7."""

from __future__ import annotations

from .contracts import (
    ContinuationStateV27,
    DecisionContextV27,
    DecisionRecommendationV27,
    ExternalDecisionV27,
)


def _recommend(
    decision,
    lifecycle,
    relation,
    reasons,
    control="BASELINE_SIMLINGO",
    *,
    target_type=None,
    authority_subject_type=None,
):
    return DecisionRecommendationV27(
        decision=decision,
        lifecycle_state=lifecycle,
        control_owner=control,
        ask_grants_vehicle_motion_authority=False,
        candidate_vehicle_control_authority=False,
        relation=relation,
        target_type=(
            "UNIQUE" if decision is ExternalDecisionV27.ACT else None
        ) if target_type is None else target_type,
        authority_subject_type=(
            "SELECTED_NAVIGATION" if decision is ExternalDecisionV27.ACT
            else "EXISTING_LAWFUL_HOLDING" if decision is ExternalDecisionV27.WAIT
            else "CLARIFICATION_QUERY" if decision is ExternalDecisionV27.ASK
            else "BASELINE_CONTROL"
        ) if authority_subject_type is None else authority_subject_type,
        reason_codes=tuple(reasons),
    )


def decide_v27(context: DecisionContextV27) -> DecisionRecommendationV27:
    relation = "%s/%s" % (
        context.current_action_relation, context.future_obligation_relation
    )
    if (
        context.semantic_state == "RESOLVED"
        and context.effective_k == 1
        and context.resolved_answer_exists
        and context.selected_plan_fresh
        and context.selected_motion_safety_valid
        and context.selected_motion_rule_valid
    ):
        return _recommend(
            ExternalDecisionV27.ACT, "RESOLVED_SELECTED_REPLAN", relation,
            ("FRESH_ANSWER_CONDITIONED_SELECTED_REPLAN_AUTHORIZED",),
            control="BASELINE_SIMLINGO_SELECTED_NAVIGATION_INPUT",
        )

    integrity = (
        context.semantic_state == "UNRESOLVED"
        and context.effective_k > 1
        and context.plausible_interpretations >= 2
        and context.future_obligation_evidence_valid
        and context.candidate_identity_keyset_valid
        and context.source_identity_fresh
        and context.topology_valid
        and context.query_transaction_valid
        and context.information_safety_valid
    )
    if not integrity:
        return _recommend(
            ExternalDecisionV27.FALLBACK, "FAIL_CLOSED", relation,
            ("V2_7_QUERY_OR_EVIDENCE_TRANSACTION_INVALID",),
        )
    if not context.candidate_plan_semantics_realized and not context.query_active:
        if context.continuation.baseline_motion_admissible is True:
            return _recommend(
                ExternalDecisionV27.ACT,
                "NO_CURRENT_PLANNING_HORIZON_AMBIGUITY",
                relation,
                (
                    "FUTURE_SEMANTIC_CANDIDATES_NOT_ALL_CURRENTLY_PLAN_RELEVANT",
                    "BASELINE_SIMLINGO_REPLANS_WITHOUT_QUERY_OR_CANDIDATE_AUTHORITY",
                ),
                control="BASELINE_SIMLINGO",
                target_type="BASELINE",
                authority_subject_type="BASELINE_CONTROL",
            )
        return _recommend(
            ExternalDecisionV27.FALLBACK,
            "NO_CURRENT_PLANNING_AMBIGUITY_BASELINE_INADMISSIBLE",
            relation,
            ("CURRENT_PLAN_NOT_AMBIGUOUS_AND_BASELINE_MOTION_NOT_ADMISSIBLE",),
        )
    if context.continuation.state is ContinuationStateV27.COMMITMENT_CROSSED:
        return _recommend(
            ExternalDecisionV27.FALLBACK, "FAIL_CLOSED_COMMITMENT_CROSSED",
            relation, ("UNRESOLVED_SEMANTIC_COMMITMENT_ALREADY_CROSSED",),
        )

    query_needed = bool(
        context.future_obligation_relation == "DIVERGENT"
        and context.answer_can_resolve_or_reduce
        and not context.resolved_answer_exists
    )
    if not query_needed:
        return _recommend(
            ExternalDecisionV27.FALLBACK, "FAIL_CLOSED", relation,
            ("CLARIFICATION_NOT_DECISION_RELEVANT_OR_RESOLVABLE",),
        )

    if (
        not context.query_active
        and not context.ambiguity_evidence_confirmed_across_observations
    ):
        if context.continuation.state is ContinuationStateV27.REVERSIBLE:
            lifecycle = (
                "PROVISIONAL_AMBIGUITY_BASELINE_OBSERVATION"
            )
            return _recommend(
                ExternalDecisionV27.ACT,
                lifecycle,
                relation,
                (
                    "AMBIGUITY_EVIDENCE_AWAITS_FRESH_CONFIRMATION",
                    "BASELINE_SIMLINGO_REPLANS_WITHOUT_QUERY_OR_CANDIDATE_AUTHORITY",
                ),
                control="BASELINE_SIMLINGO",
                target_type="BASELINE",
                authority_subject_type="BASELINE_CONTROL",
            )
        return _recommend(
            ExternalDecisionV27.FALLBACK,
            "PROVISIONAL_AMBIGUITY_PRESERVATION_NOT_PROVEN",
            relation,
            ("AMBIGUITY_NOT_CONFIRMED_BEFORE_REVERSIBILITY_LIMIT",),
        )

    if context.query_active:
        if not context.answer_pending:
            return _recommend(
                ExternalDecisionV27.FALLBACK, "QUERY_STATE_INVALID", relation,
                ("ACTIVE_QUERY_WITHOUT_PENDING_ANSWER",),
            )
        if context.continuation.state is ContinuationStateV27.REVERSIBLE:
            return _recommend(
                ExternalDecisionV27.ASK, "ASK_PENDING_BASELINE_CONTINUATION",
                relation,
                ("QUERY_ACTIVE_BASELINE_CONTINUATION_SEMANTICALLY_REVERSIBLE",),
            )
        if (
            context.continuation.state
            is ContinuationStateV27.COMMITMENT_IMMINENT
            and context.continuation.lawful_holding_available is True
        ):
            return _recommend(
                ExternalDecisionV27.WAIT, "WAIT_BEFORE_UNRESOLVED_COMMITMENT",
                relation,
                ("ACTIVE_QUERY_COMMITMENT_IMMINENT_LAWFUL_HOLD_AVAILABLE",),
                control="EXISTING_LAWFUL_HOLDING",
            )
        return _recommend(
            ExternalDecisionV27.FALLBACK, "FAIL_CLOSED_NO_PRESERVATION_AUTHORITY",
            relation, ("NEITHER_REVERSIBLE_BASELINE_NOR_LAWFUL_WAIT_AVAILABLE",),
        )

    return _recommend(
        ExternalDecisionV27.ASK, "ASK_ISSUED_BASELINE_CONTINUES", relation,
        (
            "VALID_AMBIGUITY_FUTURE_OBLIGATIONS_DIVERGENT_ASK_EARLY",
            "ASK_INDEPENDENT_OF_RECOVERABILITY_SHARED_LEASE_AND_ROUTE_TIMING",
        ),
    )


__all__ = ["decide_v27"]
