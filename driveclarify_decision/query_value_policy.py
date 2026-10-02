"""Counterfactual query-value and wait-value policy for offline diagnostics only."""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence

from .decision_contracts import (
    ActTargetType,
    AnswerChannelModel,
    BeliefSource,
    BeliefStatus,
    Decision,
    DecisionContext,
    DecisionRecommendation,
    EVIDENCE_DESIGNATION,
    ExpectedLossChannels,
    IntentBelief,
    PosteriorBeliefUpdate,
    TaskOutcome,
    WaitMode,
    assert_no_forbidden_runtime_keys,
)


_NONSELECTING_ANSWERS = frozenset(
    {"NO_ANSWER", "LATE", "AMBIGUOUS", "UNINFORMATIVE", "CONTRADICTORY"}
)
_UNRESOLVABLE_UNKNOWN_CAUSES = frozenset(
    {
        "PLAN_TO_TASK_MAPPING_MISSING",
        "RUNTIME_FAIRNESS_MISSING",
        "WORLD_COORDINATE_MISSING",
        "PHYSICAL_EVIDENCE_MISSING",
        "ARTIFACT_PROVENANCE_INSUFFICIENT",
    }
)


@dataclass(frozen=True)
class PolicyConfig:
    remove_query_cost: bool = False
    remove_answer_delay: bool = False
    remove_no_answer_probability: bool = False
    remove_counterfactual_outcome_matrix: bool = False
    remove_wait: bool = False
    treat_all_unknown_as_ask: bool = False
    force_shared_uniform_task_loss: bool = False
    remove_posterior_update: bool = False


@dataclass(frozen=True)
class QueryEvaluation:
    feasible: bool
    risk: float | None
    channels: ExpectedLossChannels
    value: float | None
    answer_can_change_action: bool
    on_time_answer_probability: float
    expected_delay_seconds: float
    posterior_updates: tuple[PosteriorBeliefUpdate, ...]
    valid_interpretations: bool
    consequence_difference: bool
    post_answer_possible_actions: tuple[str, ...]
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class WaitEvaluation:
    feasible: bool
    risk: float | None
    channels: ExpectedLossChannels
    value: float | None
    reason_codes: tuple[str, ...]


def _channel_rows(channel: AnswerChannelModel) -> dict[str, dict[str, float]]:
    return {
        candidate_id: {label: float(value) for label, value in row}
        for candidate_id, row in channel.answer_confusion_matrix
    }


def posterior_update(
    prior: IntentBelief,
    channel: AnswerChannelModel,
    answer_label: str,
    candidate_ids: Sequence[str],
    *,
    answer_status: str = "RECEIVED_ON_TIME",
) -> PosteriorBeliefUpdate:
    """Bayes update over runtime candidate hypotheses; no option is intrinsically correct."""

    ids = tuple(candidate_ids)
    prior.validate_for(ids)
    channel.validate_for(ids)
    normalized_label = str(answer_label).strip().upper()
    if answer_status == "LATE" or normalized_label == "LATE":
        status, reason = "LATE_IGNORED", "LATE_ANSWER_DOES_NOT_MODIFY_DECISION"
    elif normalized_label == "NO_ANSWER":
        status, reason = "NO_ANSWER_UNCHANGED", "NO_ANSWER_DOES_NOT_SELECT_CANDIDATE"
    elif normalized_label in {"AMBIGUOUS", "UNINFORMATIVE"}:
        status, reason = "UNINFORMATIVE_UNCHANGED", "AMBIGUOUS_ANSWER_DOES_NOT_SELECT_CANDIDATE"
    elif normalized_label == "CONTRADICTORY":
        status, reason = "CONTRADICTORY_UNCHANGED", "CONTRADICTORY_ANSWER_DOES_NOT_SELECT_CANDIDATE"
    else:
        status = ""
        reason = ""
    if status:
        return PosteriorBeliefUpdate(
            answer_label=normalized_label,
            posterior_belief=prior,
            update_status=status,
            selected_candidate_id=None,
            likelihood=1.0,
            reason_codes=(reason,),
        )

    rows = _channel_rows(channel)
    if any(normalized_label not in rows[candidate_id] for candidate_id in ids):
        return PosteriorBeliefUpdate(
            answer_label=normalized_label,
            posterior_belief=prior,
            update_status="CONTRADICTORY_OR_OUT_OF_CHANNEL",
            selected_candidate_id=None,
            likelihood=0.0,
            reason_codes=("ANSWER_LABEL_NOT_IN_DECLARED_CHANNEL",),
        )
    prior_values = prior.as_dict()
    numerators = {
        candidate_id: prior_values[candidate_id] * rows[candidate_id][normalized_label]
        for candidate_id in ids
    }
    likelihood = sum(numerators.values())
    if likelihood <= 0.0:
        return PosteriorBeliefUpdate(
            answer_label=normalized_label,
            posterior_belief=prior,
            update_status="ZERO_LIKELIHOOD_CONTRADICTION",
            selected_candidate_id=None,
            likelihood=0.0,
            reason_codes=("ANSWER_HAS_ZERO_PRIOR_PREDICTIVE_PROBABILITY",),
        )
    probabilities = tuple((candidate_id, numerators[candidate_id] / likelihood) for candidate_id in ids)
    posterior = IntentBelief(
        candidate_probabilities=probabilities,
        belief_status=BeliefStatus.AVAILABLE,
        belief_source=BeliefSource.RULE_DERIVED,
        normalization_status="NORMALIZED_BAYES_UPDATE",
        provenance=tuple((*prior.provenance, *channel.provenance, "DETERMINISTIC_BAYES_UPDATE")),
        reason_codes=("ANSWER_CHANNEL_LIKELIHOOD_APPLIED",),
    )
    values = [value for _, value in probabilities]
    maximum = max(values)
    winners = [candidate_id for candidate_id, value in probabilities if math.isclose(value, maximum, abs_tol=1e-12)]
    changed = any(
        not math.isclose(prior_values[candidate_id], dict(probabilities)[candidate_id], abs_tol=1e-12)
        for candidate_id in ids
    )
    selected = winners[0] if changed and len(winners) == 1 else None
    return PosteriorBeliefUpdate(
        answer_label=normalized_label,
        posterior_belief=posterior,
        update_status="INFORMATIVE_UPDATED" if changed else "UNINFORMATIVE_UNCHANGED",
        selected_candidate_id=selected,
        likelihood=likelihood,
        reason_codes=(
            "POSTERIOR_UPDATED_WITHOUT_DEFAULT_OPTION_ORDER"
            if changed
            else "EQUAL_LIKELIHOODS_PRESERVE_PRIOR",
        ),
    )


def _loss_channels(
    *,
    task: float | None,
    query: float = 0.0,
    delay: float = 0.0,
    no_answer: float = 0.0,
    wait: float = 0.0,
    missed: float = 0.0,
    reasons: Sequence[str] = (),
) -> ExpectedLossChannels:
    total = None if task is None else task + query + delay + no_answer + wait + missed
    return ExpectedLossChannels(
        task_cost=task,
        query_cost=query,
        delay_cost=delay,
        no_answer_cost=no_answer,
        wait_cost=wait,
        missed_opportunity_cost=missed,
        total=total,
        reason_codes=tuple(reasons),
    )


class OfflineQueryValuePolicy:
    """Auditable v0 policy: hard contracts first, utility comparison second."""

    def __init__(self, config: PolicyConfig | None = None) -> None:
        self.config = config or PolicyConfig()

    def _act_risks(
        self,
        context: DecisionContext,
        belief: IntentBelief | None = None,
    ) -> dict[str, float] | None:
        if self.config.remove_counterfactual_outcome_matrix:
            return None
        matrix = context.counterfactual_outcome_matrix
        if not matrix.complete_and_known:
            return None
        current = belief or context.intent_belief
        current.validate_for(context.candidate_ids)
        probabilities = current.as_dict()
        known_fail_costs = [
            float(cell.task_error_cost)
            for cell in matrix.cells
            if cell.task_outcome is TaskOutcome.FAIL and cell.task_error_cost is not None
        ]
        shared_cost = sum(known_fail_costs) / len(known_fail_costs) if known_fail_costs else 0.0
        risks: dict[str, float] = {}
        for action_id in context.candidate_ids:
            risk = 0.0
            for hypothesis_id in context.candidate_ids:
                cell = matrix.cell(action_id, hypothesis_id)
                if cell is None or cell.task_error_cost is None:
                    return None
                cost = shared_cost if self.config.force_shared_uniform_task_loss and cell.task_outcome is TaskOutcome.FAIL else float(cell.task_error_cost)
                risk += probabilities[hypothesis_id] * cost
            risks[action_id] = risk
        return risks

    @staticmethod
    def _unique_minimum(risks: Mapping[str, float], epsilon: float) -> str | None:
        if not risks:
            return None
        minimum = min(risks.values())
        winners = [candidate_id for candidate_id, value in risks.items() if abs(value - minimum) <= epsilon]
        return winners[0] if len(winners) == 1 else None

    @staticmethod
    def _oracle_hypothesis_risk(context: DecisionContext) -> float | None:
        probabilities = context.intent_belief.as_dict()
        total = 0.0
        for hypothesis_id in context.candidate_ids:
            costs = []
            for action_id in context.candidate_ids:
                cell = context.counterfactual_outcome_matrix.cell(action_id, hypothesis_id)
                if cell is None or cell.task_error_cost is None:
                    return None
                costs.append(float(cell.task_error_cost))
            total += probabilities[hypothesis_id] * min(costs)
        return total

    def _query_evaluation(
        self,
        context: DecisionContext,
        base_risks: Mapping[str, float],
        *,
        query_already_active: bool = False,
    ) -> QueryEvaluation:
        reasons: list[str] = []
        epsilon = context.operating_parameters.strict_value_epsilon
        base_best = min(base_risks.values())
        valid_interpretations = (
            len(context.candidate_ids) == 2
            and all(
                (
                    context.counterfactual_outcome_matrix.cell(candidate_id, candidate_id)
                    is not None
                    and context.counterfactual_outcome_matrix.cell(
                        candidate_id, candidate_id
                    ).task_outcome
                    is TaskOutcome.PASS
                )
                for candidate_id in context.candidate_ids
            )
        )
        if not valid_interpretations:
            reasons.append("ASK_REQUIRES_TWO_VALID_INTERPRETATIONS")
        consequence_signatures = {
            (
                cell.action_candidate_id,
                cell.task_outcome.value,
                cell.task_error_cost,
            )
            for cell in context.counterfactual_outcome_matrix.cells
        }
        consequence_difference = any(
            len(
                {
                    (cell.task_outcome.value, cell.task_error_cost)
                    for cell in context.counterfactual_outcome_matrix.cells
                    if cell.action_candidate_id == action_id
                }
            )
            > 1
            for action_id in context.candidate_ids
        ) and len(consequence_signatures) > 1
        if not consequence_difference:
            reasons.append("ASK_REQUIRES_CONSEQUENCE_DIFFERENCE")
        proposal = context.query_proposal if isinstance(context.query_proposal, Mapping) else {}
        if not query_already_active:
            if context.query_budget <= 0:
                reasons.append("QUERY_BUDGET_EXHAUSTED")
            if context.active_query_id is not None:
                reasons.append("ACTIVE_QUERY_PREVENTS_SECOND_ASK")
            if proposal.get("proposal_status") != "QUESTION_PROPOSAL" or not proposal.get("query_id"):
                reasons.append("QUESTION_NOT_REALIZABLE")
            partition = proposal.get("candidate_partition")
            if not isinstance(partition, (list, tuple)):
                reasons.append("QUESTION_PARTITION_MISSING")
            else:
                partition_labels: list[str] = []
                partition_ids: list[str] = []
                for item in partition:
                    if not isinstance(item, (list, tuple)) or len(item) != 2:
                        reasons.append("QUESTION_PARTITION_INVALID")
                        continue
                    partition_labels.append(str(item[0]).upper())
                    if isinstance(item[1], (list, tuple)):
                        partition_ids.extend(str(candidate_id) for candidate_id in item[1])
                    else:
                        reasons.append("QUESTION_PARTITION_INVALID")
                if (
                    sorted(partition_ids) != sorted(context.candidate_ids)
                    or len(partition_ids) != len(set(partition_ids))
                ):
                    reasons.append("QUESTION_PARTITION_DOES_NOT_COVER_CANDIDATES_ONCE")
                channel_labels = set(next(iter(_channel_rows(context.answer_channel).values())).keys())
                if not set(partition_labels).issubset(channel_labels):
                    reasons.append("QUESTION_PARTITION_LABELS_NOT_IN_ANSWER_CHANNEL")
        if context.answer_deadline_monotonic is None:
            reasons.append("ANSWER_DEADLINE_NOT_AVAILABLE")
            remaining = 0.0
        else:
            remaining = context.answer_deadline_monotonic - context.monotonic_now
            if remaining <= 0.0 or context.time_to_decision_status == "EXPIRED":
                reasons.append("ANSWER_DEADLINE_EXPIRED")
        evidence = context.evidence_dict()
        causes = set(str(item) for item in evidence.get("unknown_causes", ()))
        passenger_resolvable = evidence.get("passenger_resolvable_unknown") is True
        ambiguity_present = evidence.get("ambiguity_present") is True
        if causes.intersection(_UNRESOLVABLE_UNKNOWN_CAUSES) and not self.config.treat_all_unknown_as_ask:
            reasons.append("UNKNOWN_CAUSE_NOT_PASSENGER_RESOLVABLE")
        if not (passenger_resolvable or ambiguity_present or self.config.treat_all_unknown_as_ask):
            reasons.append("QUERY_TARGET_NOT_PASSENGER_RESOLVABLE")

        no_answer_probability = 0.0 if self.config.remove_no_answer_probability else context.answer_channel.no_answer_probability
        rows = _channel_rows(context.answer_channel)
        answer_labels = tuple(next(iter(rows.values())).keys())
        prior = context.intent_belief.as_dict()
        posterior_updates: list[PosteriorBeliefUpdate] = []
        post_answer_possible_actions: set[str] = set()
        answer_can_change = False
        on_time_answer_probability = 0.0
        task_cost = 0.0
        delay_cost = 0.0
        expected_delay_seconds = 0.0
        late_answer_probability = 0.0
        resolution_probability = context.answer_channel.answer_resolution_probability
        if self.config.remove_posterior_update:
            resolution_probability = 0.0

        for delay, delay_probability in context.answer_channel.delay_distribution:
            effective_delay = 0.0 if self.config.remove_answer_delay else float(delay)
            probability_at_delay = (1.0 - no_answer_probability) * float(delay_probability)
            charged_delay = min(effective_delay, max(remaining, 0.0))
            delay_cost += probability_at_delay * charged_delay * context.operating_parameters.delay_cost_per_second
            expected_delay_seconds += probability_at_delay * charged_delay
            if effective_delay > remaining:
                late_answer_probability += probability_at_delay
                continue
            on_time_answer_probability += probability_at_delay
            for answer_label in answer_labels:
                predictive = sum(prior[candidate_id] * rows[candidate_id][answer_label] for candidate_id in context.candidate_ids)
                event_probability = probability_at_delay * predictive
                update = posterior_update(
                    context.intent_belief,
                    context.answer_channel,
                    answer_label,
                    context.candidate_ids,
                )
                if not posterior_updates:
                    posterior_updates.append(update)
                elif all(existing.answer_label != update.answer_label for existing in posterior_updates):
                    posterior_updates.append(update)
                posterior_risks = self._act_risks(context, update.posterior_belief)
                post_best = base_best if posterior_risks is None else min(posterior_risks.values())
                if posterior_risks is not None:
                    before_action = self._unique_minimum(base_risks, epsilon)
                    after_action = self._unique_minimum(posterior_risks, epsilon)
                    if update.update_status == "INFORMATIVE_UPDATED" and after_action is not None and after_action != before_action:
                        answer_can_change = True
                    if update.update_status == "INFORMATIVE_UPDATED" and after_action is not None:
                        post_answer_possible_actions.add(after_action)
                effective_post_best = resolution_probability * post_best + (1.0 - resolution_probability) * base_best
                task_cost += event_probability * effective_post_best

        effective_no_answer = no_answer_probability + late_answer_probability
        task_cost += effective_no_answer * base_best
        if no_answer_probability > 0.0 and remaining > 0.0:
            charged = 0.0 if self.config.remove_answer_delay else remaining
            delay_cost += no_answer_probability * charged * context.operating_parameters.delay_cost_per_second
            expected_delay_seconds += no_answer_probability * charged
        no_answer_cost = effective_no_answer * context.operating_parameters.no_answer_penalty
        query_cost = 0.0 if query_already_active or self.config.remove_query_cost else context.operating_parameters.query_cost
        channels = _loss_channels(
            task=task_cost,
            query=query_cost,
            delay=delay_cost,
            no_answer=no_answer_cost,
            reasons=("TASK_QUERY_DELAY_NO_ANSWER_CHANNELS_REPORTED_SEPARATELY",),
        )
        risk = channels.total
        value = None if risk is None else base_best - risk
        if on_time_answer_probability <= 0.0:
            reasons.append("NO_ANSWER_PROBABILITY_BEFORE_DEADLINE")
        if not answer_can_change:
            reasons.append("ANSWER_CANNOT_CHANGE_UNIQUE_ACTION")
        if not query_already_active and (value is None or value <= epsilon):
            reasons.append("QUERY_VALUE_NOT_STRICTLY_POSITIVE")
        feasible = not reasons
        if query_already_active:
            ignored = {
                "ACTIVE_QUERY_PREVENTS_SECOND_ASK",
                "QUERY_BUDGET_EXHAUSTED",
                "QUESTION_NOT_REALIZABLE",
                "QUERY_VALUE_NOT_STRICTLY_POSITIVE",
            }
            feasible = not [reason for reason in reasons if reason not in ignored]
        return QueryEvaluation(
            feasible=feasible,
            risk=risk,
            channels=channels,
            value=value,
            answer_can_change_action=answer_can_change,
            on_time_answer_probability=on_time_answer_probability,
            expected_delay_seconds=expected_delay_seconds,
            posterior_updates=tuple(posterior_updates),
            valid_interpretations=valid_interpretations,
            consequence_difference=consequence_difference,
            post_answer_possible_actions=tuple(sorted(post_answer_possible_actions)),
            reason_codes=tuple(sorted(set(reasons or ("QUERY_VALUE_FEASIBLE",)))),
        )

    def _wait_evaluation(
        self,
        context: DecisionContext,
        base_risks: Mapping[str, float],
        query_evaluation: QueryEvaluation,
    ) -> WaitEvaluation:
        wait = context.wait_opportunity
        base_best = min(base_risks.values())
        reasons: list[str] = []
        if self.config.remove_wait or wait.wait_mode is WaitMode.NOT_AVAILABLE:
            reasons.append("WAIT_NOT_AVAILABLE")
        if wait.holding_capability_status != "AVAILABLE_CONTRACT_ONLY":
            reasons.append("HOLDING_CAPABILITY_CONTRACT_NOT_AVAILABLE")
        if wait.decision_deadline_status == "EXPIRED" or context.time_to_decision_status == "EXPIRED":
            reasons.append("WAIT_DECISION_DEADLINE_EXPIRED")
        if wait.expected_information_arrival_time is None:
            reasons.append("WAIT_INFORMATION_ARRIVAL_TIME_MISSING")
        elif context.answer_deadline_monotonic is not None and wait.expected_information_arrival_time > context.answer_deadline_monotonic:
            reasons.append("WAIT_INFORMATION_EXPECTED_AFTER_DEADLINE")
        if wait.information_resolution_probability <= 0.0:
            reasons.append("WAIT_HAS_NO_INFORMATION_RESOLUTION_PROBABILITY")
        if "RUNTIME_DECISION_AUTHORITY_EVIDENCE_V1" in wait.provenance:
            if not wait.wait_reason:
                reasons.append("WAIT_REASON_MISSING")
            if wait.closed_loop_behavior != "MAINTAIN_CURRENT_VALID_CLOSED_LOOP_BEHAVIOR":
                reasons.append("WAIT_SAFE_CLOSED_LOOP_BEHAVIOR_NOT_DECLARED")
            if wait.low_level_controller_owner != "EXISTING_BASELINE_PID":
                reasons.append("WAIT_EXISTING_PID_OWNERSHIP_NOT_DECLARED")
            if wait.emergency_stop_semantics:
                reasons.append("WAIT_MUST_NOT_MEAN_EMERGENCY_STOP")

        if wait.wait_mode is WaitMode.AWAIT_PENDING_ANSWER:
            if not wait.active_query_pending or context.active_query_id is None:
                reasons.append("WAIT_PENDING_ANSWER_REQUIRES_ACTIVE_QUERY")
            task_after = query_evaluation.channels.task_cost
            delay_after = query_evaluation.channels.delay_cost
            no_answer_after = query_evaluation.channels.no_answer_cost
        else:
            if not wait.future_information_expected:
                reasons.append("WAIT_REQUIRES_EXPLICIT_FUTURE_INFORMATION")
            oracle_risk = self._oracle_hypothesis_risk(context)
            task_after = None if oracle_risk is None else (
                wait.information_resolution_probability * oracle_risk
                + (1.0 - wait.information_resolution_probability) * base_best
            )
            delay_after = 0.0
            no_answer_after = 0.0
        channels = _loss_channels(
            task=task_after,
            delay=delay_after,
            no_answer=no_answer_after,
            wait=wait.wait_cost,
            missed=wait.missed_opportunity_cost,
            reasons=("WAIT_DOES_NOT_IMPLY_STOP_BRAKE_OR_HOLDING_CONTROL",),
        )
        risk = channels.total
        value = None if risk is None else base_best - risk
        if value is None or value <= context.operating_parameters.strict_value_epsilon:
            reasons.append("WAIT_VALUE_NOT_STRICTLY_POSITIVE")
        return WaitEvaluation(
            feasible=not reasons,
            risk=risk,
            channels=channels,
            value=value,
            reason_codes=tuple(sorted(set(reasons or ("WAIT_VALUE_FEASIBLE",)))),
        )

    @staticmethod
    def _decision_authority_gate_reasons(
        context: DecisionContext,
    ) -> tuple[str, ...]:
        """Gates shared by ACT, ASK, and WAIT decision authority.

        UNKNOWN physical safety is deliberately not a shared blocker because
        ASK and WAIT generate no low-level control.  An explicit negative or
        invalid safety status remains fail-closed for every decision.
        """

        gate = context.hard_gate_envelope
        reasons: list[str] = []
        if gate.hard_safety_status not in {"PASS", "CLEAR", "UNKNOWN"}:
            reasons.append("HARD_SAFETY_GATE_NOT_PASSED")
        if gate.hard_rule_status not in {"PASS", "CLEAR"}:
            reasons.append("HARD_RULE_GATE_NOT_PASSED")
        if gate.evidence_gate_status != "PASS":
            reasons.append("EVIDENCE_GATE_NOT_PASSED")
        if gate.cache_status != "FRESH":
            reasons.append("CACHE_NOT_FRESH")
        if gate.candidate_freshness_status != "FRESH":
            reasons.append("CANDIDATE_NOT_FRESH")
        active = context.active_query_id is not None
        if active != (gate.query_episode_status == "ACTIVE_QUERY_PENDING"):
            reasons.append("ACTIVE_QUERY_STATE_CONFLICT")
        if gate.control_authorized:
            reasons.append("M2B_CONTROL_AUTHORIZATION_INVALID")
        return tuple(sorted(set(reasons)))

    @staticmethod
    def _act_safety_reasons(context: DecisionContext) -> tuple[str, ...]:
        """Physical-control eligibility used only by ACT."""

        gate = context.hard_gate_envelope
        if gate.hard_safety_status not in {"PASS", "CLEAR"}:
            return ("HARD_SAFETY_GATE_NOT_PASSED",)
        evidence = gate.physical_safety_evidence
        # Backward-compatible offline M2B fixtures predate the V1 evidence
        # envelope.  Online V1 always supplies one, including UNKNOWN.
        if evidence is None:
            return ()
        verified = (
            evidence.safety_status in {"PASS", "CLEAR"}
            and evidence.availability == "AVAILABLE_VERIFIED"
            and evidence.evidence_grade == "VERIFIED_FROM_CONTROLLED_PROBE"
            and evidence.usage_purpose == "PHYSICAL_CONTROL_AUTHORIZATION"
            and evidence.safety_critical_eligible is True
        )
        return () if verified else ("VERIFIED_PHYSICAL_SAFETY_EVIDENCE_NOT_AVAILABLE",)

    @staticmethod
    def _recommendation(
        decision: Decision,
        *,
        selected: str | None = None,
        equivalence: Sequence[str] = (),
        query_id: str | None = None,
        wait_mode: WaitMode = WaitMode.NOT_AVAILABLE,
        channels: Mapping[str, ExpectedLossChannels] | None = None,
        query_value: float | None = None,
        wait_value: float | None = None,
        confidence: str,
        reasons: Sequence[str],
        predicates: Mapping[str, Any] | None = None,
    ) -> DecisionRecommendation:
        if decision is Decision.ACT:
            target_type = ActTargetType.EQUIVALENCE_CLASS if equivalence else ActTargetType.UNIQUE_CANDIDATE
        else:
            target_type = ActTargetType.NONE
        return DecisionRecommendation(
            decision=decision,
            act_target_type=target_type,
            selected_candidate_id=selected,
            equivalence_class_candidate_ids=tuple(equivalence),
            query_id=query_id if decision is Decision.ASK else None,
            wait_mode=wait_mode if decision is Decision.WAIT else WaitMode.NOT_AVAILABLE,
            expected_loss_channels=tuple(sorted((channels or {}).items())),
            query_value=query_value,
            wait_value=wait_value,
            decision_confidence_status=confidence,
            authorization_eligible=False,
            used_for_control=False,
            reason_trace=tuple(
                sorted(
                    set(
                        (
                            *reasons,
                            "DIAGNOSTIC_RECOMMENDATION_ONLY",
                            "FALLBACK_IS_NOT_EMERGENCY_STOP",
                            "WAIT_GENERATES_NO_CONTROL_QUANTITIES",
                        )
                    )
                )
            ),
            provenance=("FULL_M2B_QUERY_VALUE_POLICY", *EVIDENCE_DESIGNATION),
            decision_predicates=tuple(sorted((predicates or {}).items())),
        )

    def recommend(self, context: DecisionContext) -> DecisionRecommendation:
        """Return ACT/ASK/WAIT/FALLBACK with decision-type-specific authority."""

        original = copy.deepcopy(context.to_dict())
        assert_no_forbidden_runtime_keys(original)
        gate_reasons = self._decision_authority_gate_reasons(context)
        act_safety_reasons = self._act_safety_reasons(context)
        gate_predicates = {
            "physical_safety_status": context.hard_gate_envelope.hard_safety_status,
            "physical_control_authorization_eligible": not act_safety_reasons,
            "clarification_decision_authority_gate_passed": not gate_reasons,
            "wait_decision_authority_gate_passed": not gate_reasons,
        }
        if gate_reasons:
            result = self._recommendation(
                Decision.FALLBACK_RECOMMENDED,
                confidence="FAIL_CLOSED_HARD_CONTRACT",
                reasons=gate_reasons,
                predicates=gate_predicates,
            )
            if context.to_dict() != original:
                raise RuntimeError("DECISION_CONTEXT_MUTATED")
            return result

        act_risks = self._act_risks(context)
        if act_risks is None:
            return self._recommendation(
                Decision.FALLBACK_RECOMMENDED,
                confidence="FAIL_CLOSED_UNKNOWN_TASK_LOSS",
                reasons=("COUNTERFACTUAL_MATRIX_MISSING_INCOMPLETE_OR_UNKNOWN",),
                predicates=gate_predicates,
            )
        act_channels = {
            f"ACT:{candidate_id}": _loss_channels(
                task=risk,
                reasons=("EXPECTED_TASK_LOSS_OVER_RUNTIME_HYPOTHESES",),
            )
            for candidate_id, risk in act_risks.items()
        }
        epsilon = context.operating_parameters.strict_value_epsilon
        unique = self._unique_minimum(act_risks, epsilon)
        query_eval = self._query_evaluation(
            context,
            act_risks,
            query_already_active=context.active_query_id is not None,
        )
        wait_eval = self._wait_evaluation(context, act_risks, query_eval)
        channels = {
            **act_channels,
            "ASK": query_eval.channels,
            "WAIT": wait_eval.channels,
        }
        current_preferred_action = unique or "UNRESOLVED_TIE"
        predicates = {
            **gate_predicates,
            "two_valid_interpretations": query_eval.valid_interpretations,
            "consequence_difference": query_eval.consequence_difference,
            "current_preferred_action": current_preferred_action,
            "post_answer_possible_actions": query_eval.post_answer_possible_actions,
            "answer_can_change_selected_action": query_eval.answer_can_change_action,
            "query_value_positive": (
                query_eval.value is not None and query_eval.value > epsilon
            ),
            "query_value": query_eval.value,
            "no_active_query": context.active_query_id is None,
            "time_window_valid": (
                context.answer_deadline_monotonic is not None
                and context.answer_deadline_monotonic > context.monotonic_now
                and context.time_to_decision_status != "EXPIRED"
            ),
            "holding_capability_declared": (
                context.wait_opportunity.holding_capability_status
                == "AVAILABLE_CONTRACT_ONLY"
            ),
            "future_information_arrival_declared": (
                context.wait_opportunity.expected_information_arrival_time
                is not None
            ),
            "wait_reason": context.wait_opportunity.wait_reason,
            "wait_cost": context.wait_opportunity.wait_cost,
            "wait_value_positive": (
                wait_eval.value is not None and wait_eval.value > epsilon
            ),
            "wait_maintains_safe_closed_loop_behavior": (
                context.wait_opportunity.closed_loop_behavior
                == "MAINTAIN_CURRENT_VALID_CLOSED_LOOP_BEHAVIOR"
                and context.wait_opportunity.emergency_stop_semantics is False
            ),
        }

        if context.active_query_id is not None:
            if wait_eval.feasible:
                return self._recommendation(
                    Decision.WAIT,
                    wait_mode=context.wait_opportunity.wait_mode,
                    channels=channels,
                    query_value=query_eval.value,
                    wait_value=wait_eval.value,
                    confidence="EXPLICIT_PENDING_INFORMATION_VALUE",
                    reasons=("ACTIVE_QUERY_ALREADY_PENDING_NO_SECOND_ASK", *wait_eval.reason_codes),
                    predicates=predicates,
                )
            return self._recommendation(
                Decision.FALLBACK_RECOMMENDED,
                channels=channels,
                query_value=query_eval.value,
                wait_value=wait_eval.value,
                confidence="FAIL_CLOSED_ACTIVE_QUERY_WITHOUT_WAIT_VALUE",
                reasons=(
                    "ACTIVE_QUERY_CANNOT_BE_REASKED",
                    *act_safety_reasons,
                    *wait_eval.reason_codes,
                ),
                predicates=predicates,
            )

        if len(context.candidate_ids) == 1:
            candidate_id = context.candidate_ids[0]
            diagonal = context.counterfactual_outcome_matrix.cell(candidate_id, candidate_id)
            if (
                not act_safety_reasons
                and diagonal is not None
                and diagonal.task_outcome is TaskOutcome.PASS
            ):
                return self._recommendation(
                    Decision.ACT,
                    selected=candidate_id,
                    channels=channels,
                    query_value=query_eval.value,
                    wait_value=wait_eval.value,
                    confidence="SINGLE_VALID_EVIDENCE_SUFFICIENT_CANDIDATE",
                    reasons=("ONE_VALID_CANDIDATE_WITH_PASS_OUTCOME",),
                    predicates=predicates,
                )

        all_cross_pass = all(
            cell.task_outcome is TaskOutcome.PASS
            for cell in context.counterfactual_outcome_matrix.cells
        )
        if (
            not act_safety_reasons
            and context.candidate_pair_relation == "TASK_EQUIVALENT"
            and all_cross_pass
        ):
            return self._recommendation(
                Decision.ACT,
                equivalence=context.candidate_ids,
                channels=channels,
                query_value=query_eval.value,
                wait_value=wait_eval.value,
                confidence="EXPLICIT_TASK_EQUIVALENCE_CLASS",
                reasons=("EQUIVALENT_CANDIDATES_ACT_WITHOUT_DEFAULT_ID",),
                predicates=predicates,
            )

        options: list[tuple[float, Decision]] = []
        if unique is not None and not act_safety_reasons:
            options.append((act_risks[unique], Decision.ACT))
        if query_eval.feasible and query_eval.risk is not None:
            options.append((query_eval.risk, Decision.ASK))
        if wait_eval.feasible and wait_eval.risk is not None:
            options.append((wait_eval.risk, Decision.WAIT))
        if not options:
            return self._recommendation(
                Decision.FALLBACK_RECOMMENDED,
                channels=channels,
                query_value=query_eval.value,
                wait_value=wait_eval.value,
                confidence="FAIL_CLOSED_NO_FEASIBLE_UNIQUE_OPTION",
                reasons=(
                    "NO_UNIQUE_ACT_AND_NO_POSITIVE_QUERY_OR_WAIT_VALUE",
                    *act_safety_reasons,
                    *query_eval.reason_codes,
                    *wait_eval.reason_codes,
                ),
                predicates=predicates,
            )
        minimum = min(value for value, _ in options)
        winners = [decision for value, decision in options if abs(value - minimum) <= epsilon]
        if len(winners) != 1:
            if unique is not None and Decision.ACT in winners:
                winners = [Decision.ACT]
            else:
                return self._recommendation(
                    Decision.FALLBACK_RECOMMENDED,
                    channels=channels,
                    query_value=query_eval.value,
                    wait_value=wait_eval.value,
                    confidence="FAIL_CLOSED_UTILITY_TIE",
                    reasons=("NO_STRICTLY_DOMINANT_FEASIBLE_DECISION",),
                    predicates=predicates,
                )
        decision = winners[0]
        if decision is Decision.ASK:
            return self._recommendation(
                Decision.ASK,
                query_id=str(context.query_proposal.get("query_id")),
                channels=channels,
                query_value=query_eval.value,
                wait_value=wait_eval.value,
                confidence="STRICT_POSITIVE_QUERY_VALUE",
                reasons=(
                    *query_eval.reason_codes,
                    *(
                        ("PHYSICAL_SAFETY_UNKNOWN_NON_CONTROL_ASK_ONLY",)
                        if context.hard_gate_envelope.hard_safety_status == "UNKNOWN"
                        else ()
                    ),
                ),
                predicates=predicates,
            )
        if decision is Decision.WAIT:
            return self._recommendation(
                Decision.WAIT,
                wait_mode=context.wait_opportunity.wait_mode,
                channels=channels,
                query_value=query_eval.value,
                wait_value=wait_eval.value,
                confidence="STRICT_POSITIVE_FUTURE_INFORMATION_VALUE",
                reasons=(
                    *wait_eval.reason_codes,
                    *(
                        ("PHYSICAL_SAFETY_UNKNOWN_NON_CONTROL_WAIT_ONLY",)
                        if context.hard_gate_envelope.hard_safety_status == "UNKNOWN"
                        else ()
                    ),
                ),
                predicates=predicates,
            )
        assert unique is not None
        return self._recommendation(
            Decision.ACT,
            selected=unique,
            channels=channels,
            query_value=query_eval.value,
            wait_value=wait_eval.value,
            confidence="UNIQUE_EXPECTED_TASK_LOSS_DOMINANT",
            reasons=("ASK_AND_WAIT_DO_NOT_HAVE_HIGHER_VALUE",),
            predicates=predicates,
        )


def bridge_authoritative_rule_output(
    authoritative_rule_output: Mapping[str, Any],
    recommendation: DecisionRecommendation,
) -> dict[str, Any]:
    """Preserve the authoritative rule/state-machine result beside the M2B diagnostic."""

    return {
        "schema_version": "driveclarify.m2b_rule_bridge.v0",
        "authoritative_output": copy.deepcopy(dict(authoritative_rule_output)),
        "m2b_diagnostic_suggestion": recommendation.to_dict(),
        "override_applied": False,
        "used_for_control": False,
        "authorization_eligible": False,
        "control_authorized": False,
        "reason_trace": [
            "AUTHORITATIVE_RULE_OUTPUT_PRESERVED",
            "HARD_SAFETY_GATE_NOT_OVERRIDDEN",
            "HARD_RULE_GATE_NOT_OVERRIDDEN",
            "FROZEN_UNKNOWN_NOT_OVERRIDDEN",
            "CACHE_QUERY_TIMEOUT_FALLBACK_CONTRACTS_NOT_OVERRIDDEN",
        ],
    }
