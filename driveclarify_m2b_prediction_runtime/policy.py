"""Frozen comparison dispatch for prediction execution; no evaluation imports."""

from __future__ import annotations

from typing import Any, Mapping

from driveclarify_decision.decision_contracts import (
    AnswerChannelModel, BeliefSource, BeliefStatus, CandidateOutcomeEvidence,
    CounterfactualOutcomeCell, CounterfactualOutcomeMatrix, DecisionContext,
    HardGateEnvelope, IntentBelief, OperatingParameters, TaskOutcome,
    WaitMode, WaitOpportunityModel,
)
from driveclarify_decision.query_value_policy import OfflineQueryValuePolicy, PolicyConfig

from .contracts import policy_input_leak_paths


def _context(case: Mapping[str, Any]) -> DecisionContext:
    ids = tuple(case["candidate_ids"])
    cells = tuple(CounterfactualOutcomeCell(
        cell["action_candidate_id"], cell["hypothesis_candidate_id"],
        TaskOutcome(cell["task_outcome"]), cell["evidence_status"],
        cell["task_error_cost"],
        None if cell["task_outcome"] == "UNKNOWN" else bool(cell["task_error_cost"] > 0),
        ("FROZEN_BLIND_RUNTIME_MATRIX",), ("SYMBOLIC_NOT_PHYSICAL_SAFETY",),
    ) for cell in case["counterfactual_matrix"]["cells"])
    matrix = CounterfactualOutcomeMatrix(
        ids, cells, case["counterfactual_matrix"]["matrix_status"],
        ("FROZEN_BLIND_RUNTIME_MATRIX",), ("CANDIDATE_SPECIFIC_TASK_BINDING",),
    )
    prior = tuple((str(key), float(value)) for key, value in case["intent_belief"]["candidate_probabilities"])
    answer = case["answer_channel"]
    wait = case["wait"]
    gate = case["contract_state"]
    costs = case["costs"]
    return DecisionContext(
        decision_id=case["case_id"], episode_id=case["case_id"], source_observation_id=None,
        candidate_ids=ids, candidate_pair_relation=case["matrix_relation"],
        candidate_outcomes=tuple(CandidateOutcomeEvidence(
            candidate_id, matrix.cell(candidate_id, candidate_id).task_outcome,
            matrix.cell(candidate_id, candidate_id).evidence_status,
            ("FROZEN_BLIND_RUNTIME_MATRIX",), (),
        ) for candidate_id in ids),
        counterfactual_outcome_matrix=matrix,
        intent_belief=IntentBelief(
            prior, BeliefStatus.AVAILABLE,
            BeliefSource.DECLARED_UNINFORMATIVE
            if all(abs(value - 0.5) < 1e-12 for _, value in prior)
            else BeliefSource.EXTERNAL_CALIBRATED,
            "NORMALIZED_PREDECLARED_PROFILE", ("FROZEN_BLIND_PROFILE",), (),
        ),
        query_proposal={
            "proposal_status": case["query"]["proposal_status"],
            "query_id": f"Q-{case['case_id']}",
            "candidate_partition": [["OPTION_ONE", [ids[0]]], ["OPTION_TWO", [ids[1]]]],
        },
        answer_channel=AnswerChannelModel(
            float(answer["answer_resolution_probability"]), float(answer["no_answer_probability"]),
            tuple((str(hypothesis), tuple((str(label), float(probability)) for label, probability in row))
                  for hypothesis, row in answer["answer_confusion_matrix"]),
            tuple((float(delay), float(probability)) for delay, probability in answer["delay_distribution"]),
            "AVAILABLE", "FROZEN_BLIND_PROFILE", ("FROZEN_BLIND_PROFILE",), (),
        ),
        query_budget=int(case["query"]["query_budget"]),
        active_query_id=f"AQ-{case['case_id']}" if case["query"]["active_query"] else None,
        monotonic_now=float(case["deadline"]["now"]),
        answer_deadline_monotonic=float(case["deadline"]["answer_deadline"]),
        time_to_decision_status=case["deadline"]["decision_window_status"],
        wait_opportunity=WaitOpportunityModel(
            WaitMode(wait["wait_mode"]), bool(wait["future_information_expected"]),
            wait["expected_information_arrival_time"], bool(case["query"]["active_query"]),
            wait["holding_capability_status"], wait["decision_deadline_status"],
            float(wait["missed_opportunity_cost"]), float(wait["wait_cost"]),
            float(wait["information_resolution_probability"]),
            ("FROZEN_BLIND_PROFILE",), (),
        ),
        hard_gate_envelope=HardGateEnvelope(
            gate["hard_safety_status"], gate["hard_rule_status"], gate["evidence_gate_status"],
            "ACTIVE_QUERY_PENDING" if case["query"]["active_query"] else "NO_ACTIVE_QUERY",
            gate["cache_status"], gate["candidate_freshness_status"], False, (),
        ),
        evidence_masks=(("ambiguity_present", True),
                        ("passenger_resolvable_unknown", bool(case["query"]["passenger_resolvable"])),
                        ("unknown_causes", ("PASSENGER_INTENT_AMBIGUITY",))),
        operating_parameters=OperatingParameters(
            float(costs["query_cost"]), float(costs["delay_cost_per_second"]),
            float(costs["no_answer_penalty"]), costs["unknown_task_loss_policy"],
            float(costs["strict_value_epsilon"]), ("FROZEN_BLIND_PROFILE",),
        ),
        provenance=("M2B_SEALED_BLIND_RUNTIME_INPUT", "DIAGNOSTIC_ONLY"),
    )


def predict_runtime_case(case: Mapping[str, Any], comparison_id: str,
                         protocol_sha256: str) -> dict[str, Any]:
    leaks = policy_input_leak_paths(case)
    if leaks:
        raise ValueError("R2_POLICY_INPUT_MEMBERSHIP_OR_EVALUATION_LEAK:" + ",".join(leaks[:10]))
    if comparison_id == "EVALUATION_ONLY_ORACLE_UPPER_BOUND":
        raise PermissionError("R2_ORACLE_COMPARISON_EVALUATOR_ONLY")
    configs = {
        "CONSEQUENCE_WITHOUT_QUERY_COST": PolicyConfig(remove_query_cost=True),
        "CONSEQUENCE_WITHOUT_DELAY": PolicyConfig(remove_answer_delay=True),
        "CONSEQUENCE_WITHOUT_NO_ANSWER": PolicyConfig(remove_no_answer_probability=True),
        "ACT_ASK_WITHOUT_WAIT": PolicyConfig(remove_wait=True),
        "NO_COUNTERFACTUAL_MATRIX": PolicyConfig(remove_counterfactual_outcome_matrix=True),
        "NO_POSTERIOR_UPDATE": PolicyConfig(remove_posterior_update=True),
    }
    recommendation = OfflineQueryValuePolicy(configs.get(comparison_id)).recommend(_context(case))
    channels = dict(recommendation.expected_loss_channels)

    def total(name: str) -> float | None:
        item = channels.get(name)
        return None if item is None else item.total

    action = "FALLBACK" if recommendation.decision.value == "FALLBACK_RECOMMENDED" else recommendation.decision.value
    selected = recommendation.selected_candidate_id
    subtype = recommendation.act_target_type.value
    baseline_reason: str | None = None
    if comparison_id == "ALWAYS_ACT_TOP1":
        action, selected, subtype, baseline_reason = "ACT", case["candidate_ids"][0], "UNIQUE_CANDIDATE", "BASELINE_ALWAYS_ACT_TOP1"
    elif comparison_id == "ALWAYS_ASK":
        action, selected, subtype, baseline_reason = "ASK", None, "NONE", "BASELINE_ALWAYS_ASK"
    elif comparison_id == "ALWAYS_WAIT":
        action, selected, subtype, baseline_reason = "WAIT", None, "NONE", "BASELINE_ALWAYS_WAIT"
    elif comparison_id == "AMBIGUITY_DETECTION_ONLY":
        action, selected, subtype, baseline_reason = "ASK", None, "NONE", "BASELINE_AMBIGUITY_ONLY"
    elif comparison_id == "PAIR_RELATION_ONLY":
        equivalent = case["matrix_relation"] == "TASK_EQUIVALENT"
        action, selected, subtype, baseline_reason = (
            ("ACT", None, "EQUIVALENCE_CLASS", "BASELINE_PAIR_EQUIVALENT") if equivalent
            else ("FALLBACK", None, "NONE", "BASELINE_PAIR_NON_EQUIVALENT")
        )
    elif comparison_id == "LANGUAGE_UNCERTAINTY_THRESHOLD":
        top = max(case["intent_belief"]["candidate_probabilities"], key=lambda item: item[1])
        action, selected, subtype, baseline_reason = (
            ("ASK", None, "NONE", "BASELINE_LANGUAGE_UNCERTAIN") if float(top[1]) < 0.7
            else ("ACT", top[0], "UNIQUE_CANDIDATE", "BASELINE_LANGUAGE_CONFIDENT")
        )
    elif comparison_id == "LEARNED_M1_DIAGNOSTIC_ONLY":
        action, selected, subtype, baseline_reason = (
            "FALLBACK", None, "NONE", "LEARNED_DIAGNOSTIC_HAS_NO_DECISION_AUTHORITY"
        )
    return {
        "case_id": case["case_id"], "selected_action": action,
        "selected_candidate_id": selected, "act_subtype": subtype,
        "decision_reason_codes": list(recommendation.reason_trace)
        + ([] if baseline_reason is None else [baseline_reason]),
        "legal_action_mask": {
            "ACT": total(f"ACT:{case['candidate_ids'][0]}") is not None,
            "ASK": total("ASK") is not None, "WAIT": total("WAIT") is not None,
            "FALLBACK": True,
        },
        "r_act_a": total(f"ACT:{case['candidate_ids'][0]}"),
        "r_act_b": total(f"ACT:{case['candidate_ids'][1]}"),
        "r_ask": total("ASK"), "r_wait": total("WAIT"),
        "v_ask": recommendation.query_value, "v_wait": recommendation.wait_value,
        "posterior_summary": [],
        "query_episode_state": "ACTIVE_QUERY_PENDING" if case["query"]["active_query"] else "NO_ACTIVE_QUERY",
        "matrix_sha256": case["provenance_hashes"]["matrix_sha256"],
        "profile_sha256": case["provenance_hashes"]["profile_sha256"],
        "protocol_sha256": protocol_sha256,
    }

