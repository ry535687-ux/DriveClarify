"""Independent brute-force authoring oracle for M2C hidden-gold consistency.

The implementation enumerates the legal symbolic actions directly from declared
challenge fields.  It neither imports nor calls any M2B policy or helper.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping


@dataclass(frozen=True)
class OracleResult:
    runtime_action: str
    decision: str
    act_target_type: str
    selected_candidate_id: str | None
    wait_mode: str
    expected_loss: float | None
    act_losses: tuple[tuple[str, float], ...]
    ask_loss: float | None
    wait_loss: float | None
    reason: str


def _belief(case: Mapping[str, Any]) -> dict[str, float]:
    return {
        row["candidate_id"]: float(row["probability"])
        for row in case["intent_belief"]["candidate_probabilities"]
    }


def _cells(case: Mapping[str, Any]) -> dict[tuple[str, str], Mapping[str, Any]]:
    return {
        (cell["action_candidate_id"], cell["hypothesis_candidate_id"]): cell
        for cell in case["counterfactual_runtime_inputs"]["cells"]
    }


def _global_gate_passes(case: Mapping[str, Any]) -> bool:
    gate = case["hard_gate_envelope"]
    required = {
        "hard_safety_status": "AVAILABLE_CONTRACT_ONLY",
        "hard_rule_status": "PASS",
        "evidence_gate_status": "PASS",
        "cache_status": "FRESH",
        "candidate_freshness_status": "FRESH",
        "candidate_set_status": "COMPLETE",
        "belief_status": "AVAILABLE_NORMALIZED",
    }
    return all(gate.get(key) == value for key, value in required.items())


def _act_losses(case: Mapping[str, Any]) -> dict[str, float] | None:
    ids = case["counterfactual_runtime_inputs"]["candidate_ids"]
    belief = _belief(case)
    cells = _cells(case)
    losses: dict[str, float] = {}
    for action_id in ids:
        total = 0.0
        for hypothesis_id in ids:
            cell = cells[(action_id, hypothesis_id)]
            if cell["task_outcome"] == "UNKNOWN" or cell["task_error_cost"] is None:
                return None
            total += belief[hypothesis_id] * float(cell["task_error_cost"])
        losses[action_id] = total
    return losses


def _perfect_information_loss(case: Mapping[str, Any]) -> float | None:
    ids = case["counterfactual_runtime_inputs"]["candidate_ids"]
    belief = _belief(case)
    cells = _cells(case)
    result = 0.0
    for hypothesis_id in ids:
        costs: list[float] = []
        for action_id in ids:
            cell = cells[(action_id, hypothesis_id)]
            if cell["task_outcome"] == "UNKNOWN" or cell["task_error_cost"] is None:
                return None
            costs.append(float(cell["task_error_cost"]))
        result += belief[hypothesis_id] * min(costs)
    return result


def _ask_loss(case: Mapping[str, Any], act_losses: Mapping[str, float]) -> tuple[float, set[str]] | None:
    ids = case["counterfactual_runtime_inputs"]["candidate_ids"]
    if len(ids) != 2:
        return None
    meta = case["ambiguity_metadata_without_gold"]
    channel = case["question_channel_contract"]
    active = case["active_query_state"]
    if meta["unknown_cause_class"] != "PASSENGER_RESOLVABLE":
        return None
    if channel["question_status"] != "QUESTION_PROPOSAL" or channel["channel_status"] != "VALID":
        return None
    if case["query_budget"] <= 0 or active["status"] != "NONE":
        return None
    deadline = case["answer_deadline_monotonic"]
    if deadline is None or deadline <= case["monotonic_now"]:
        return None

    remaining = float(deadline) - float(case["monotonic_now"])
    on_time_delay_probability = sum(
        float(item["probability"])
        for item in channel["delay_distribution"]
        if float(item["seconds"]) <= remaining
    )
    if on_time_delay_probability <= 0.0:
        return None
    expected_delay = sum(
        float(item["seconds"]) * float(item["probability"])
        for item in channel["delay_distribution"]
    )
    base_resolution = (
        (1.0 - float(channel["no_answer_probability"]))
        * float(channel["answer_resolution_probability"])
        * on_time_delay_probability
    )
    if base_resolution <= 0.0:
        return None

    belief = _belief(case)
    cells = _cells(case)
    rows = {
        row["hypothesis_candidate_id"]: {
            entry["answer_label"]: float(entry["probability"])
            for entry in row["answer_probabilities"]
        }
        for row in channel["answer_confusion_matrix"]
    }
    labels = [item["answer_label"] for item in channel["candidate_partition"]]
    resolved_joint_probability = 0.0
    resolved_task_loss = 0.0
    posterior_best_actions: set[str] = set()
    for label in labels:
        joint_by_hypothesis = {
            hypothesis_id: belief[hypothesis_id] * base_resolution * rows[hypothesis_id][label]
            for hypothesis_id in ids
        }
        label_probability = sum(joint_by_hypothesis.values())
        resolved_joint_probability += label_probability
        if label_probability <= 0.0:
            continue
        action_joint_losses: dict[str, float] = {}
        for action_id in ids:
            action_joint_losses[action_id] = sum(
                joint_by_hypothesis[hypothesis_id]
                * float(cells[(action_id, hypothesis_id)]["task_error_cost"])
                for hypothesis_id in ids
            )
        best_action = min(action_joint_losses, key=action_joint_losses.get)
        posterior_best_actions.add(best_action)
        resolved_task_loss += action_joint_losses[best_action]
    if len(posterior_best_actions) < 2:
        return None

    unresolved_probability = max(0.0, 1.0 - resolved_joint_probability)
    current_best = min(act_losses.values())
    costs = case["declared_cost_parameters"]
    total = (
        float(costs["query_cost"])
        + expected_delay * float(costs["delay_cost_per_second"])
        + resolved_task_loss
        + unresolved_probability * current_best
        + unresolved_probability * float(costs["no_answer_penalty"])
    )
    return total, posterior_best_actions


def _wait_loss(case: Mapping[str, Any], act_losses: Mapping[str, float]) -> float | None:
    wait = case["wait_opportunity"]
    mode = wait["wait_mode"]
    if mode not in {
        "AWAIT_PENDING_ANSWER",
        "AWAIT_EXPECTED_OBSERVATION",
        "DEFER_BEFORE_DECISION_WINDOW",
    }:
        return None
    if not wait["future_information_expected"]:
        return None
    if wait["holding_capability_status"] != "AVAILABLE_CONTRACT_ONLY":
        return None
    arrival = wait["expected_information_arrival_monotonic"]
    deadline = case["answer_deadline_monotonic"]
    if arrival is None or deadline is None or float(arrival) > float(deadline):
        return None
    if mode == "AWAIT_PENDING_ANSWER" and case["active_query_state"]["status"] != "ACTIVE":
        return None
    if mode == "DEFER_BEFORE_DECISION_WINDOW" and wait["decision_window_status"] != "OPEN":
        return None
    probability = float(wait["information_resolution_probability"])
    if probability <= 0.0:
        return None
    perfect = _perfect_information_loss(case)
    if perfect is None:
        return None
    current_best = min(act_losses.values())
    return (
        float(wait["wait_cost"])
        + float(wait["missed_opportunity_cost"])
        + probability * perfect
        + (1.0 - probability) * current_best
    )


def evaluate_case(case: Mapping[str, Any]) -> OracleResult:
    ids = case["counterfactual_runtime_inputs"]["candidate_ids"]
    if len(ids) != 2:
        return OracleResult(
            "FALLBACK_RECOMMENDED", "FALLBACK_RECOMMENDED", "NONE", None,
            "NOT_AVAILABLE", None, (), None, None, "CANDIDATE_COUNT_OUTSIDE_BINARY_CONTRACT"
        )
    if not _global_gate_passes(case):
        return OracleResult(
            "FALLBACK_RECOMMENDED", "FALLBACK_RECOMMENDED", "NONE", None,
            "NOT_AVAILABLE", None, (), None, None, "HARD_GATE_NOT_PASSED"
        )

    act_losses = _act_losses(case)
    if act_losses is None:
        cause = case["ambiguity_metadata_without_gold"]["unknown_cause_class"]
        reason = (
            "PASSENGER_CAUSE_WITH_UNKNOWN_MATRIX_HAS_NO_SCALAR_ORACLE"
            if cause == "PASSENGER_RESOLVABLE"
            else "NONPASSENGER_UNKNOWN_FALLBACK_CEILING"
        )
        return OracleResult(
            "FALLBACK_RECOMMENDED", "FALLBACK_RECOMMENDED", "NONE", None,
            "NOT_AVAILABLE", None, (), None, None, reason
        )

    loss_items = tuple((candidate_id, act_losses[candidate_id]) for candidate_id in ids)
    cells = _cells(case)
    if case["counterfactual_runtime_inputs"]["pair_relation"] == "TASK_EQUIVALENT" and all(
        cell["task_outcome"] == "PASS" for cell in cells.values()
    ):
        return OracleResult(
            "ACT_EQUIVALENCE_CLASS", "ACT", "EQUIVALENCE_CLASS", None,
            "NOT_AVAILABLE", 0.0, loss_items, None, None, "ALL_HYPOTHESES_TASK_EQUIVALENT"
        )

    first, second = ids
    exact_tie = math.isclose(act_losses[first], act_losses[second], rel_tol=0.0, abs_tol=1e-12)
    current_best = min(act_losses.values())
    unique_act_id = None if exact_tie else min(ids, key=lambda item: act_losses[item])

    ask_result = _ask_loss(case, act_losses)
    ask_loss = None if ask_result is None else ask_result[0]
    wait_loss = _wait_loss(case, act_losses)
    epsilon = float(case["declared_cost_parameters"]["strict_value_epsilon"])
    legal: list[tuple[str, float]] = []
    if unique_act_id is not None:
        legal.append(("ACT", act_losses[unique_act_id]))
    if ask_loss is not None and ask_loss < current_best - epsilon:
        legal.append(("ASK", ask_loss))
    if wait_loss is not None and wait_loss < current_best - epsilon:
        legal.append(("WAIT", wait_loss))
    if not legal:
        return OracleResult(
            "FALLBACK_RECOMMENDED", "FALLBACK_RECOMMENDED", "NONE", None,
            "NOT_AVAILABLE", None, loss_items, ask_loss, wait_loss,
            "EXACT_ACT_TIE_WITHOUT_POSITIVE_INFORMATION_VALUE" if exact_tie else "NO_LEGAL_STRICTLY_PREFERRED_ACTION"
        )

    legal.sort(key=lambda item: item[1])
    if len(legal) > 1 and math.isclose(legal[0][1], legal[1][1], rel_tol=0.0, abs_tol=1e-12):
        return OracleResult(
            "FALLBACK_RECOMMENDED", "FALLBACK_RECOMMENDED", "NONE", None,
            "NOT_AVAILABLE", None, loss_items, ask_loss, wait_loss, "BEST_ACTION_EXACT_TIE"
        )
    action, risk = legal[0]
    if action == "ACT":
        position = ids.index(unique_act_id)
        return OracleResult(
            "ACT_A" if position == 0 else "ACT_B", "ACT", "UNIQUE_CANDIDATE",
            unique_act_id, "NOT_AVAILABLE", risk, loss_items, ask_loss, wait_loss,
            "UNIQUE_MINIMUM_EXPECTED_TASK_LOSS"
        )
    if action == "ASK":
        return OracleResult(
            "ASK", "ASK", "NONE", None, "NOT_AVAILABLE", risk, loss_items,
            ask_loss, wait_loss, "PASSENGER_QUERY_HAS_STRICT_POSITIVE_VALUE"
        )
    return OracleResult(
        "WAIT", "WAIT", "NONE", None, case["wait_opportunity"]["wait_mode"],
        risk, loss_items, ask_loss, wait_loss, "FUTURE_INFORMATION_HAS_STRICT_POSITIVE_VALUE"
    )

