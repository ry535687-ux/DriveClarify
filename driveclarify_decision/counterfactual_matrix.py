"""Candidate-action by candidate-hypothesis symbolic consequence evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from driveclarify_language.interaction_contracts import (
    BindingStatus,
    CandidateSpecificTaskBinding,
    LongitudinalTaskTargetType,
)

from .decision_contracts import (
    CounterfactualOutcomeCell,
    CounterfactualOutcomeMatrix,
    LongitudinalTaskEvidence,
    LongitudinalTaskOutcome,
    LongitudinalTaskOutcomeEvidence,
    SerializableContract,
    TaskOutcome,
)


_LEGAL_PLAN_PROVENANCE = frozenset(
    {
        "HAND_AUTHORED_DECISION_FIXTURE",
        "SYMBOLIC_COUNTERFACTUAL_OUTCOME",
        "SYNTHETIC_QUERY_CHANNEL_TEST_ONLY",
        "REAL_RECORDED_UNLABELED",
        "SYNTHETIC_PLAN_TEST_ONLY",
        "HAND_AUTHORED_ORACLE_FIXTURE",
        # Additive non-blind diagnostic producer provenance.  This authorizes
        # symbolic matrix consumption only; it does not authorize control or a
        # physical-safety claim.
        "SHADOW_STATIC_BRANCH_MAPPED_CANDIDATE_DIAGNOSTIC_V0",
    }
)

CONJUNCTIVE_REQUIRED_TASK_COMPONENT_COMPOSITION_V0 = (
    "CONJUNCTIVE_REQUIRED_TASK_COMPONENT_COMPOSITION_V0"
)


@dataclass(frozen=True)
class CounterfactualPlanEvidence(SerializableContract):
    action_candidate_id: str
    source_observation_id: str | None
    mapped_symbolic_target_type: str | None
    mapped_symbolic_target_id: str | None
    plan_frame: str | None
    plan_unit: str | None
    provenance: tuple[str, ...]
    source_artifacts: tuple[str, ...]
    reason_codes: tuple[str, ...] = ()
    longitudinal_task_evidence: LongitudinalTaskEvidence | None = None
    schema_version: str = "driveclarify.counterfactual_plan_evidence.v0"

    def __post_init__(self) -> None:
        if (
            self.longitudinal_task_evidence is not None
            and self.longitudinal_task_evidence.action_candidate_id
            != self.action_candidate_id
        ):
            raise ValueError("COUNTERFACTUAL_PLAN_LONGITUDINAL_EVIDENCE_ID_MISMATCH")


def _unknown_cell(
    action_id: str,
    hypothesis_id: str,
    reasons: Sequence[str],
    provenance: Sequence[str],
) -> CounterfactualOutcomeCell:
    return CounterfactualOutcomeCell(
        action_candidate_id=action_id,
        hypothesis_candidate_id=hypothesis_id,
        task_outcome=TaskOutcome.UNKNOWN,
        evidence_status="INSUFFICIENT",
        task_error_cost=None,
        wrong_goal_indicator=None,
        provenance=tuple((*provenance, "SYMBOLIC_COUNTERFACTUAL_OUTCOME")),
        reason_codes=tuple(sorted(set(reasons))),
    )


def _evaluate_route_counterfactual_cell(
    plan: CounterfactualPlanEvidence | None,
    hypothesis_binding: CandidateSpecificTaskBinding | None,
    declared_wrong_goal_cost: float | None,
    *,
    action_candidate_id: str,
    hypothesis_candidate_id: str,
) -> CounterfactualOutcomeCell:
    """Evaluate only the existing symbolic route component."""

    reasons: list[str] = []
    provenance: list[str] = []
    if plan is None:
        reasons.append("COUNTERFACTUAL_ACTION_PLAN_MISSING")
    else:
        provenance.extend(plan.provenance)
        if plan.action_candidate_id != action_candidate_id:
            reasons.append("COUNTERFACTUAL_ACTION_ID_MISMATCH")
        if not plan.mapped_symbolic_target_type or not plan.mapped_symbolic_target_id:
            reasons.append("COUNTERFACTUAL_PLAN_MAPPING_MISSING")
        if plan.plan_frame != "SYMBOLIC_PLAN_TARGET" or plan.plan_unit != "IDENTIFIER":
            reasons.append("COUNTERFACTUAL_PLAN_SEMANTIC_DOMAIN_NOT_ESTABLISHED")
        if not _LEGAL_PLAN_PROVENANCE.intersection(plan.provenance):
            reasons.append("COUNTERFACTUAL_PLAN_PROVENANCE_INVALID_OR_MISSING")
    if hypothesis_binding is None:
        reasons.append("COUNTERFACTUAL_HYPOTHESIS_BINDING_MISSING")
    else:
        provenance.extend(hypothesis_binding.provenance)
        if hypothesis_binding.source_candidate_id != hypothesis_candidate_id:
            reasons.append("COUNTERFACTUAL_HYPOTHESIS_ID_MISMATCH")
        if hypothesis_binding.binding_status is not BindingStatus.BOUND:
            reasons.append("COUNTERFACTUAL_HYPOTHESIS_BINDING_NOT_AVAILABLE")
        if not hypothesis_binding.symbolic_target_type or not hypothesis_binding.symbolic_target_id:
            reasons.append("COUNTERFACTUAL_HYPOTHESIS_TARGET_MISSING")
    if declared_wrong_goal_cost is None or declared_wrong_goal_cost < 0.0:
        reasons.append("DECLARED_TASK_ERROR_COST_MISSING_OR_INVALID")
    if reasons:
        return _unknown_cell(action_candidate_id, hypothesis_candidate_id, reasons, provenance)

    assert plan is not None and hypothesis_binding is not None and declared_wrong_goal_cost is not None
    matches = (
        plan.mapped_symbolic_target_type,
        plan.mapped_symbolic_target_id,
    ) == (
        hypothesis_binding.symbolic_target_type,
        hypothesis_binding.symbolic_target_id,
    )
    return CounterfactualOutcomeCell(
        action_candidate_id=action_candidate_id,
        hypothesis_candidate_id=hypothesis_candidate_id,
        task_outcome=TaskOutcome.PASS if matches else TaskOutcome.FAIL,
        evidence_status="SUFFICIENT_SYMBOLIC_TASK_EVIDENCE",
        task_error_cost=0.0 if matches else float(declared_wrong_goal_cost),
        wrong_goal_indicator=not matches,
        provenance=tuple((*provenance, "SYMBOLIC_COUNTERFACTUAL_OUTCOME")),
        reason_codes=(
            "SYMBOLIC_TASK_TARGET_MATCH" if matches else "SYMBOLIC_WRONG_GOAL_TASK_TARGET_MISMATCH",
            "NO_PHYSICAL_SAFETY_INFERENCE",
            "RAW_UNIT_NOT_INTERPRETED_AS_METRE",
        ),
    )


def _validated_longitudinal_component(
    evidence: LongitudinalTaskOutcomeEvidence | None,
    *,
    action_candidate_id: str,
    hypothesis_candidate_id: str,
    target_type: str,
) -> tuple[LongitudinalTaskOutcome, tuple[str, ...]]:
    if evidence is None:
        return (
            LongitudinalTaskOutcome.UNKNOWN,
            ("LONGITUDINAL_TASK_OUTCOME_EVIDENCE_MISSING",),
        )
    if (
        evidence.action_candidate_id != action_candidate_id
        or evidence.hypothesis_candidate_id != hypothesis_candidate_id
    ):
        return (
            LongitudinalTaskOutcome.UNKNOWN,
            ("LONGITUDINAL_TASK_OUTCOME_EVIDENCE_IDENTITY_MISMATCH",),
        )
    if evidence.target_type != target_type:
        return (
            LongitudinalTaskOutcome.UNKNOWN,
            ("LONGITUDINAL_TASK_OUTCOME_EVIDENCE_TARGET_MISMATCH",),
        )
    if target_type == LongitudinalTaskTargetType.STOP.value:
        if evidence.outcome not in {
            LongitudinalTaskOutcome.SATISFIED,
            LongitudinalTaskOutcome.CONTRADICTED,
            LongitudinalTaskOutcome.UNKNOWN,
        }:
            return (
                LongitudinalTaskOutcome.UNKNOWN,
                ("STOP_TASK_OUTCOME_EVIDENCE_INVALID",),
            )
    elif target_type == LongitudinalTaskTargetType.CONTINUE.value:
        if evidence.outcome is not LongitudinalTaskOutcome.UNKNOWN:
            return (
                LongitudinalTaskOutcome.UNKNOWN,
                ("CONTINUE_TASK_SATISFACTION_UNRESOLVED",),
            )
    else:
        return (
            LongitudinalTaskOutcome.UNKNOWN,
            ("LONGITUDINAL_TASK_TARGET_UNSUPPORTED",),
        )
    return evidence.outcome, evidence.reason_codes


def _compose_required_task_components(
    route_outcome: TaskOutcome,
    longitudinal_outcome: LongitudinalTaskOutcome,
) -> TaskOutcome:
    if route_outcome is TaskOutcome.FAIL:
        return TaskOutcome.FAIL
    if longitudinal_outcome is LongitudinalTaskOutcome.CONTRADICTED:
        return TaskOutcome.FAIL
    if (
        route_outcome is TaskOutcome.PASS
        and longitudinal_outcome is LongitudinalTaskOutcome.SATISFIED
    ):
        return TaskOutcome.PASS
    return TaskOutcome.UNKNOWN


def evaluate_counterfactual_cell(
    plan: CounterfactualPlanEvidence | None,
    hypothesis_binding: CandidateSpecificTaskBinding | None,
    declared_wrong_goal_cost: float | None,
    *,
    action_candidate_id: str,
    hypothesis_candidate_id: str,
    longitudinal_outcome: LongitudinalTaskOutcomeEvidence | None = None,
) -> CounterfactualOutcomeCell:
    """Evaluate action against every required component of one runtime hypothesis.

    This is deliberately separate from M2A's own-binding bridge.  The old bridge continues to
    require plan candidate ID == binding candidate ID; this evaluator keeps both identities and
    makes the cross-hypothesis comparison explicit.
    """

    route_cell = _evaluate_route_counterfactual_cell(
        plan,
        hypothesis_binding,
        declared_wrong_goal_cost,
        action_candidate_id=action_candidate_id,
        hypothesis_candidate_id=hypothesis_candidate_id,
    )
    target = (
        hypothesis_binding.longitudinal_task_target
        if hypothesis_binding is not None
        else None
    )
    if target is None:
        return route_cell

    raw_target_type = getattr(target, "target_type", None)
    target_type = (
        raw_target_type.value
        if isinstance(raw_target_type, LongitudinalTaskTargetType)
        else str(raw_target_type)
    )
    longitudinal_component, longitudinal_reasons = _validated_longitudinal_component(
        longitudinal_outcome,
        action_candidate_id=action_candidate_id,
        hypothesis_candidate_id=hypothesis_candidate_id,
        target_type=target_type,
    )
    final_outcome = _compose_required_task_components(
        route_cell.task_outcome,
        longitudinal_component,
    )
    outcome_provenance = (
        longitudinal_outcome.provenance if longitudinal_outcome is not None else ()
    )
    provenance = tuple(
        dict.fromkeys(
            (
                *route_cell.provenance,
                *outcome_provenance,
                CONJUNCTIVE_REQUIRED_TASK_COMPONENT_COMPOSITION_V0,
            )
        )
    )
    reasons = tuple(
        sorted(
            set(
                (
                    *route_cell.reason_codes,
                    *longitudinal_reasons,
                    f"ROUTE_COMPONENT_{route_cell.task_outcome.value}",
                    f"LONGITUDINAL_COMPONENT_{longitudinal_component.value}",
                    f"COMPOSED_TASK_{final_outcome.value}",
                    "DECLARED_ROUTE_TASK_ERROR_COST_NOT_LONGITUDINAL_PENALTY",
                    "NO_PHYSICAL_SAFETY_INFERENCE",
                )
            )
        )
    )
    return CounterfactualOutcomeCell(
        action_candidate_id=action_candidate_id,
        hypothesis_candidate_id=hypothesis_candidate_id,
        task_outcome=final_outcome,
        evidence_status=(
            "INSUFFICIENT_COMPOSED_TASK_EVIDENCE"
            if final_outcome is TaskOutcome.UNKNOWN
            else "SUFFICIENT_CONJUNCTIVE_TASK_COMPONENT_EVIDENCE"
        ),
        task_error_cost=(
            None if final_outcome is TaskOutcome.UNKNOWN else route_cell.task_error_cost
        ),
        wrong_goal_indicator=(
            None
            if final_outcome is TaskOutcome.UNKNOWN
            else route_cell.wrong_goal_indicator
        ),
        provenance=provenance,
        reason_codes=reasons,
        route_task_outcome=route_cell.task_outcome,
        longitudinal_task_outcome=longitudinal_component,
    )


def build_counterfactual_outcome_matrix(
    candidate_ids: Sequence[str],
    plans: Mapping[str, CounterfactualPlanEvidence],
    hypothesis_bindings: Mapping[str, CandidateSpecificTaskBinding],
    declared_wrong_goal_costs: Mapping[str, float],
    *,
    longitudinal_task_outcomes: Mapping[
        tuple[str, str], LongitudinalTaskOutcomeEvidence
    ] | None = None,
    provenance: Sequence[str] = (),
) -> CounterfactualOutcomeMatrix:
    """Build every action×hypothesis cell; missing inputs remain explicit UNKNOWN."""

    ids = tuple(candidate_ids)
    component_outcomes = longitudinal_task_outcomes or {}
    expected_pairs = {(action_id, hypothesis_id) for action_id in ids for hypothesis_id in ids}
    if not set(component_outcomes).issubset(expected_pairs):
        raise ValueError("COUNTERFACTUAL_LONGITUDINAL_OUTCOME_IDENTITIES_INVALID")
    cells = tuple(
        evaluate_counterfactual_cell(
            plans.get(action_id),
            hypothesis_bindings.get(hypothesis_id),
            declared_wrong_goal_costs.get(hypothesis_id),
            action_candidate_id=action_id,
            hypothesis_candidate_id=hypothesis_id,
            longitudinal_outcome=component_outcomes.get((action_id, hypothesis_id)),
        )
        for action_id in ids
        for hypothesis_id in ids
    )
    unknown_count = sum(cell.task_outcome is TaskOutcome.UNKNOWN for cell in cells)
    return CounterfactualOutcomeMatrix(
        candidate_ids=ids,
        cells=cells,
        matrix_status="COMPLETE_KNOWN" if unknown_count == 0 else "INCOMPLETE_OR_UNKNOWN",
        provenance=tuple((*provenance, "SYMBOLIC_COUNTERFACTUAL_OUTCOME")),
        reason_codes=(
            "COUNTERFACTUAL_MATRIX_COMPLETE"
            if unknown_count == 0
            else f"COUNTERFACTUAL_MATRIX_UNKNOWN_CELL_COUNT_{unknown_count}",
            "HYPOTHESES_ARE_RUNTIME_CANDIDATES_NOT_GOLD_INTENT",
        ),
        longitudinal_task_evidence=tuple(
            plans[action_id].longitudinal_task_evidence
            for action_id in ids
            if action_id in plans
            and plans[action_id].longitudinal_task_evidence is not None
        ),
        longitudinal_task_outcomes=tuple(
            component_outcomes[(action_id, hypothesis_id)]
            for action_id in ids
            for hypothesis_id in ids
            if (action_id, hypothesis_id) in component_outcomes
        ),
    )
