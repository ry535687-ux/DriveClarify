"""Independent exact-arithmetic gold solver.

This module intentionally imports no query-value policy, integrated decision
function, baseline implementation, or development prediction artifact.
"""

from __future__ import annotations

from decimal import Decimal, getcontext
from typing import Any, Mapping

import hashlib
import json

REFERENCE_SOLVER_VERSION = "M2B_BLIND_REFERENCE_SOLVER_V1"


def stable_hash(value: Any) -> str:
    payload = (json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
    return hashlib.sha256(payload).hexdigest()

getcontext().prec = 50

ACTIONS = ("ACT", "ASK", "WAIT", "FALLBACK")
REASON_VOCABULARY = frozenset({
    "ACTIVE_QUERY_PENDING_WAIT", "ACTIVE_QUERY_WITHOUT_LEGAL_WAIT",
    "ALL_CANDIDATES_TASK_EQUIVALENT", "ANSWER_CAN_CHANGE_ACTION",
    "ANSWER_CANNOT_CHANGE_ACTION", "ASK_STRICTLY_LOWEST_LOSS",
    "CONTRACT_GATE_FAILED", "COUNTERFACTUAL_MATRIX_UNKNOWN",
    "DECISION_WINDOW_EXPIRED", "EQUIVALENCE_CLASS_ACT",
    "NO_LEGAL_STRICT_DECISION", "NO_UNIQUE_ACT", "QUERY_BUDGET_EXHAUSTED",
    "QUERY_NOT_PASSENGER_RESOLVABLE", "QUERY_VALUE_NOT_POSITIVE",
    "UNIQUE_ACT_STRICTLY_LOWEST_LOSS", "UTILITY_TIE_FAIL_CLOSED",
    "WAIT_INFORMATION_UNAVAILABLE", "WAIT_STRICTLY_LOWEST_LOSS",
    "WAIT_VALUE_NOT_POSITIVE",
})


def D(value: Any) -> Decimal:
    if value is None:
        raise ValueError("DECIMAL_VALUE_MISSING")
    return Decimal(str(value))


def _matrix(case: Mapping[str, Any]) -> dict[tuple[str, str], Mapping[str, Any]]:
    ids = case["candidate_ids"]
    cells = case["counterfactual_matrix"]["cells"]
    result = {(c["action_candidate_id"], c["hypothesis_candidate_id"]): c for c in cells}
    if set(result) != {(a, h) for a in ids for h in ids}:
        raise ValueError("REFERENCE_MATRIX_NOT_COMPLETE_2X2")
    for cell in cells:
        if cell["task_outcome"] == "UNKNOWN":
            if cell.get("task_error_cost") is not None:
                raise ValueError("UNKNOWN_CELL_SCALAR_IMPUTATION_FORBIDDEN")
        elif cell.get("task_error_cost") is None:
            raise ValueError("KNOWN_CELL_COST_MISSING")
    return result


def _act_risks(case: Mapping[str, Any], prior: Mapping[str, Decimal]) -> dict[str, Decimal] | None:
    cells = _matrix(case)
    if any(cell["task_outcome"] == "UNKNOWN" for cell in cells.values()):
        return None
    return {
        action: sum(prior[h] * D(cells[(action, h)]["task_error_cost"]) for h in case["candidate_ids"])
        for action in case["candidate_ids"]
    }


def _unique_minimum(values: Mapping[str, Decimal], epsilon: Decimal) -> str | None:
    minimum = min(values.values())
    winners = [key for key, value in values.items() if abs(value - minimum) <= epsilon]
    return winners[0] if len(winners) == 1 else None


def _posterior(prior: Mapping[str, Decimal], rows: Mapping[str, Mapping[str, Decimal]], label: str) -> dict[str, Decimal]:
    likelihood = sum(prior[h] * rows[h][label] for h in prior)
    if likelihood == 0:
        return dict(prior)
    return {h: prior[h] * rows[h][label] / likelihood for h in prior}


def _query_loss(case: Mapping[str, Any], base: Mapping[str, Decimal], epsilon: Decimal) -> tuple[Decimal, bool, Decimal, list[dict[str, Any]]]:
    q = case["query"]
    channel = case["answer_channel"]
    prior = {k: D(v) for k, v in case["intent_belief"]["candidate_probabilities"]}
    rows = {h: {label: D(p) for label, p in row} for h, row in channel["answer_confusion_matrix"]}
    labels = list(next(iter(rows.values())))
    remaining = D(case["deadline"]["answer_deadline"]) - D(case["deadline"]["now"])
    no_answer = D(channel["no_answer_probability"])
    resolution = D(channel["answer_resolution_probability"])
    base_best = min(base.values())
    before = _unique_minimum(base, epsilon)
    task = Decimal(0)
    delay_cost = Decimal(0)
    late = Decimal(0)
    on_time = Decimal(0)
    can_change = False
    summaries: list[dict[str, Any]] = []
    for delay, delay_probability in channel["delay_distribution"]:
        delay_d, delay_p = D(delay), D(delay_probability)
        event_delay_p = (Decimal(1) - no_answer) * delay_p
        charged = min(delay_d, max(remaining, Decimal(0)))
        delay_cost += event_delay_p * charged * D(case["costs"]["delay_cost_per_second"])
        if delay_d > remaining:
            late += event_delay_p
            continue
        on_time += event_delay_p
        for label in labels:
            predictive = sum(prior[h] * rows[h][label] for h in prior)
            post = _posterior(prior, rows, label)
            post_risks = _act_risks(case, post)
            assert post_risks is not None
            post_best = min(post_risks.values())
            after = _unique_minimum(post_risks, epsilon)
            if before is not None and after is not None and after != before:
                can_change = True
            task += event_delay_p * predictive * (resolution * post_best + (Decimal(1) - resolution) * base_best)
            summaries.append({"answer_label": label, "posterior": {k: str(v) for k, v in sorted(post.items())}})
    effective_no_answer = no_answer + late
    task += effective_no_answer * base_best
    if no_answer > 0 and remaining > 0:
        delay_cost += no_answer * remaining * D(case["costs"]["delay_cost_per_second"])
    total = task + D(case["costs"]["query_cost"]) + delay_cost + effective_no_answer * D(case["costs"]["no_answer_penalty"])
    value = base_best - total
    feasible = (
        q["query_budget"] > 0 and not q["active_query"] and q["proposal_status"] == "QUESTION_PROPOSAL"
        and q["passenger_resolvable"] and remaining > 0 and on_time > 0 and can_change and value > epsilon
    )
    return total, feasible, value, summaries


def _wait_loss(case: Mapping[str, Any], base: Mapping[str, Decimal], epsilon: Decimal, ask_loss: Decimal) -> tuple[Decimal, bool, Decimal]:
    w = case["wait"]
    base_best = min(base.values())
    deadline = D(case["deadline"]["answer_deadline"])
    if w["wait_mode"] == "AWAIT_PENDING_ANSWER":
        task_after = ask_loss - D(case["costs"]["query_cost"])
    else:
        cells = _matrix(case)
        prior = {k: D(v) for k, v in case["intent_belief"]["candidate_probabilities"]}
        oracle = sum(prior[h] * min(D(cells[(a, h)]["task_error_cost"]) for a in case["candidate_ids"]) for h in case["candidate_ids"])
        resolution = D(w["information_resolution_probability"])
        task_after = resolution * oracle + (Decimal(1) - resolution) * base_best
    total = task_after + D(w["wait_cost"]) + D(w["missed_opportunity_cost"])
    value = base_best - total
    arrival = w["expected_information_arrival_time"]
    feasible = (
        w["wait_mode"] != "NOT_AVAILABLE" and w["holding_capability_status"] == "AVAILABLE_CONTRACT_ONLY"
        and w["decision_deadline_status"] == "OPEN" and arrival is not None and D(arrival) <= deadline
        and D(w["information_resolution_probability"]) > 0 and value > epsilon
        and ((w["wait_mode"] == "AWAIT_PENDING_ANSWER" and case["query"]["active_query"])
             or (w["wait_mode"] != "AWAIT_PENDING_ANSWER" and w["future_information_expected"]))
    )
    return total, feasible, value


def solve_case(case: Mapping[str, Any]) -> dict[str, Any]:
    ids = list(case["candidate_ids"])
    if len(ids) != 2 or len(set(ids)) != 2:
        raise ValueError("REFERENCE_SOLVER_REQUIRES_TWO_DISTINCT_CANDIDATES")
    epsilon = D(case["costs"]["strict_value_epsilon"])
    prior = {k: D(v) for k, v in case["intent_belief"]["candidate_probabilities"]}
    if list(prior) != ids or sum(prior.values()) != 1:
        raise ValueError("REFERENCE_PRIOR_INVALID")
    hard = case["contract_state"]
    hard_ok = (
        hard["hard_safety_status"] == "PASS" and hard["hard_rule_status"] == "PASS"
        and hard["evidence_gate_status"] == "PASS" and hard["cache_status"] == "FRESH"
        and hard["candidate_freshness_status"] == "FRESH" and hard["control_authorized"] is False
    )
    risks = _act_risks(case, prior)
    legal = {action: False for action in ACTIONS}
    expected: dict[str, str | None] = {action: None for action in ACTIONS}
    reasons: set[str] = set()
    posterior_summary: list[dict[str, Any]] = []
    selected: str | None = None
    subtype = "NONE"
    q_value: Decimal | None = None
    w_value: Decimal | None = None
    margin: Decimal | None = None
    if not hard_ok:
        action = "FALLBACK"; legal[action] = True; expected[action] = "0"; reasons.add("CONTRACT_GATE_FAILED")
    elif case["deadline"]["decision_window_status"] == "EXPIRED":
        action = "FALLBACK"; legal[action] = True; expected[action] = "0"; reasons.add("DECISION_WINDOW_EXPIRED")
    elif risks is None:
        action = "FALLBACK"; legal[action] = True; expected[action] = "0"; reasons.add("COUNTERFACTUAL_MATRIX_UNKNOWN")
    else:
        unique = _unique_minimum(risks, epsilon)
        all_pass = all(c["task_outcome"] == "PASS" for c in case["counterfactual_matrix"]["cells"])
        if case["matrix_relation"] == "TASK_EQUIVALENT" and all_pass:
            action = "ACT"; subtype = "EQUIVALENCE_CLASS"; legal[action] = True
            expected[action] = str(min(risks.values())); reasons.update(("ALL_CANDIDATES_TASK_EQUIVALENT", "EQUIVALENCE_CLASS_ACT"))
        else:
            ask_loss, ask_ok, q_value, posterior_summary = _query_loss(case, risks, epsilon)
            wait_loss, wait_ok, w_value = _wait_loss(case, risks, epsilon, ask_loss)
            for cid, risk in risks.items(): expected[f"ACT_{cid}"] = str(risk)
            expected["ASK"], expected["WAIT"] = str(ask_loss), str(wait_loss)
            if case["query"]["active_query"]:
                if wait_ok:
                    action = "WAIT"; legal["WAIT"] = True; reasons.add("ACTIVE_QUERY_PENDING_WAIT")
                else:
                    action = "FALLBACK"; legal["FALLBACK"] = True; reasons.add("ACTIVE_QUERY_WITHOUT_LEGAL_WAIT")
            else:
                options: list[tuple[Decimal, str]] = []
                if unique is not None: legal["ACT"] = True; options.append((risks[unique], "ACT"))
                else: reasons.add("NO_UNIQUE_ACT")
                if ask_ok: legal["ASK"] = True; options.append((ask_loss, "ASK")); reasons.add("ANSWER_CAN_CHANGE_ACTION")
                else: reasons.add("QUERY_VALUE_NOT_POSITIVE" if q_value <= epsilon else "ANSWER_CANNOT_CHANGE_ACTION")
                if wait_ok: legal["WAIT"] = True; options.append((wait_loss, "WAIT"))
                else: reasons.add("WAIT_VALUE_NOT_POSITIVE" if w_value <= epsilon else "WAIT_INFORMATION_UNAVAILABLE")
                if not options:
                    action = "FALLBACK"; legal["FALLBACK"] = True; reasons.add("NO_LEGAL_STRICT_DECISION")
                else:
                    best = min(v for v, _ in options)
                    winners = [(v, a) for v, a in options if abs(v - best) <= epsilon]
                    if len(winners) > 1 and any(a == "ACT" for _, a in winners): winners = [(v, a) for v, a in winners if a == "ACT"]
                    if len(winners) != 1:
                        action = "FALLBACK"; legal = {a: False for a in ACTIONS}; legal["FALLBACK"] = True; reasons.add("UTILITY_TIE_FAIL_CLOSED")
                    else:
                        action = winners[0][1]
                        ordered = sorted(v for v, _ in options)
                        margin = ordered[1] - ordered[0] if len(ordered) > 1 else Decimal("999")
                        if action == "ACT": selected = unique; subtype = "UNIQUE_CANDIDATE"; reasons.add("UNIQUE_ACT_STRICTLY_LOWEST_LOSS")
                        elif action == "ASK": reasons.add("ASK_STRICTLY_LOWEST_LOSS")
                        else: reasons.add("WAIT_STRICTLY_LOWEST_LOSS")
    if not reasons.issubset(REASON_VOCABULARY):
        raise AssertionError("REFERENCE_REASON_OUTSIDE_VOCABULARY")
    solver_spec = {"version": REFERENCE_SOLVER_VERSION, "epsilon": str(epsilon), "tie_break": "ACT_IF_LEGAL_TIE_ELSE_FAIL_CLOSED"}
    return {
        "case_id": case["case_id"], "track": case["track"], "gold_action_type": action,
        "gold_candidate_id": selected, "act_subtype": subtype,
        "accepted_reason_code_set": sorted(reasons), "legal_action_set": [a for a in ACTIONS if legal[a]],
        "expected_loss_by_legal_action": expected,
        "query_value": None if q_value is None else str(q_value), "wait_value": None if w_value is None else str(w_value),
        "posterior_summary": posterior_summary,
        "decision_margin": None if margin is None else str(margin),
        "reference_solver_version": REFERENCE_SOLVER_VERSION,
        "reference_solver_hash": stable_hash(solver_spec),
    }
