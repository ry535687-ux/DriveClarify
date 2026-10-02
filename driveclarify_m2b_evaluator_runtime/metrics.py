"""Frozen M2B action-metric implementation for the isolated R3 evaluator."""

from __future__ import annotations

from collections import Counter
from statistics import mean, median
from typing import Any, Mapping, Sequence


ACTIONS = ("ACT", "ASK", "WAIT", "FALLBACK")


def _safe_rate(numerator: int | float, denominator: int) -> float | None:
    return None if denominator == 0 else float(numerator) / denominator


def confusion_matrix(rows: Sequence[Mapping[str, Any]],
                     gold: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, int]]:
    confusion = {actual: {predicted: 0 for predicted in ACTIONS} for actual in ACTIONS}
    for row in rows:
        confusion[gold[row["case_id"]]["gold_action_type"]][row["selected_action"]] += 1
    return confusion


def _class_metrics(confusion: Mapping[str, Mapping[str, int]], label: str) -> dict[str, Any]:
    tp = confusion[label][label]
    fp = sum(confusion[g][label] for g in ACTIONS if g != label)
    fn = sum(confusion[label][p] for p in ACTIONS if p != label)
    support = sum(confusion[label].values())
    precision = _safe_rate(tp, tp + fp)
    recall = _safe_rate(tp, tp + fn)
    f1 = None if precision is None or recall is None or precision + recall == 0 else 2 * precision * recall / (precision + recall)
    return {"precision": precision, "recall": recall, "f1": f1, "support": support,
            "tp": tp, "fp": fp, "fn": fn}


def _chosen_loss(row: Mapping[str, Any], gold_row: Mapping[str, Any]) -> tuple[float | None, float | None]:
    losses = gold_row.get("expected_loss_by_legal_action", {})
    available = [float(value) for value in losses.values() if value is not None]
    if not available:
        return None, None
    if row["selected_action"] == "ACT":
        key = f"ACT_{row.get('selected_candidate_id')}"
    else:
        key = row["selected_action"]
    chosen = losses.get(key)
    return (None if chosen is None else float(chosen)), min(available)


def _wrong_goal(row: Mapping[str, Any], gold_row: Mapping[str, Any],
                runtime_case: Mapping[str, Any] | None) -> bool | None:
    if row["selected_action"] != "ACT" or row.get("selected_candidate_id") is None:
        return None
    direct = gold_row.get("wrong_goal_by_candidate")
    if isinstance(direct, Mapping) and row["selected_candidate_id"] in direct:
        value = direct[row["selected_candidate_id"]]
        return None if value is None else bool(value)
    if runtime_case is None or gold_row.get("latent_true_intent") is None:
        return None
    cell = next((cell for cell in runtime_case["counterfactual_matrix"]["cells"]
                 if cell["action_candidate_id"] == row["selected_candidate_id"]
                 and cell["hypothesis_candidate_id"] == gold_row["latent_true_intent"]), None)
    if cell is None or cell.get("task_error_cost") is None:
        return None
    return float(cell["task_error_cost"]) > 0


def compute_action_metrics(rows: Sequence[Mapping[str, Any]],
                           gold: Mapping[str, Mapping[str, Any]],
                           runtime: Mapping[str, Mapping[str, Any]] | None = None) -> dict[str, Any]:
    runtime = runtime or {}
    confusion = confusion_matrix(rows, gold)
    per_class = {label: _class_metrics(confusion, label) for label in ACTIONS}
    n = len(rows)
    correct = sum(confusion[label][label] for label in ACTIONS)
    recalls = [metric["recall"] for metric in per_class.values() if metric["recall"] is not None]
    f1s = [metric["f1"] if metric["f1"] is not None else 0.0 for metric in per_class.values()]
    candidate_num = candidate_den = equivalence_num = equivalence_den = 0
    wrong_goal_num = wrong_goal_den = 0
    reason_exact = accepted_match = unsupported_reasons = 0
    regrets: list[float] = []
    fallback = Counter()
    for row in rows:
        gold_row = gold[row["case_id"]]
        if gold_row.get("act_subtype") == "UNIQUE_CANDIDATE":
            candidate_den += 1
            candidate_num += (row["selected_action"] == "ACT"
                              and row.get("selected_candidate_id") == gold_row.get("gold_candidate_id"))
        if gold_row.get("act_subtype") == "EQUIVALENCE_CLASS":
            equivalence_den += 1
            equivalence_num += row["selected_action"] == "ACT" and row.get("act_subtype") == "EQUIVALENCE_CLASS"
        accepted = set(gold_row.get("accepted_reason_code_set", []))
        predicted = set(row.get("reason_codes", []))
        reason_exact += predicted == accepted
        accepted_match += bool(predicted.intersection(accepted))
        unsupported_reasons += len(predicted - accepted)
        wrong = _wrong_goal(row, gold_row, runtime.get(row["case_id"]))
        if wrong is not None:
            wrong_goal_den += 1
            wrong_goal_num += wrong
        chosen, best = _chosen_loss(row, gold_row)
        if chosen is not None and best is not None:
            regrets.append(max(0.0, chosen - best))
        if row["selected_action"] == "FALLBACK":
            if gold_row["gold_action_type"] == "FALLBACK":
                fallback["justified_fallback"] += 1
                reasons = set(gold_row.get("accepted_reason_code_set", []))
                if "COUNTERFACTUAL_MATRIX_UNKNOWN" in reasons:
                    fallback["matrix_unknown_fallback"] += 1
                if "CONTRACT_GATE_FAILED" in reasons:
                    fallback["contract_fallback"] += 1
                if "DECISION_WINDOW_EXPIRED" in reasons:
                    fallback["expired_deadline_fallback"] += 1
                if reasons.intersection({"NO_LEGAL_STRICT_DECISION", "ACTIVE_QUERY_WITHOUT_LEGAL_WAIT"}):
                    fallback["unresolvable_information_fallback"] += 1
            else:
                fallback["false_fallback"] += 1
    actions = Counter(row["selected_action"] for row in rows)
    wrong_act = sum(confusion[g]["ACT"] for g in ("ASK", "WAIT", "FALLBACK"))
    return {
        "case_prediction_count": n,
        "numerator_denominator": {
            "action_correct": [correct, n],
            "candidate_act_correct": [candidate_num, candidate_den],
            "equivalence_act_correct": [equivalence_num, equivalence_den],
            "wrong_goal": [wrong_goal_num, wrong_goal_den],
        },
        "accuracy": _safe_rate(correct, n),
        "macro_f1": None if not f1s else sum(f1s) / len(ACTIONS),
        "balanced_accuracy": None if not recalls else sum(recalls) / len(ACTIONS),
        "confusion_matrix": confusion,
        "per_class": per_class,
        "exact_action_counts": {label: actions[label] for label in ACTIONS},
        "candidate_act_accuracy": _safe_rate(candidate_num, candidate_den),
        "equivalence_act_accuracy": _safe_rate(equivalence_num, equivalence_den),
        "false_fallback": fallback["false_fallback"],
        "justified_fallback": fallback["justified_fallback"],
        "fallback_decomposition": dict(sorted(fallback.items())),
        "unsafe_unresolved_act": wrong_act,
        "wrong_act": wrong_act,
        "unnecessary_ask": sum(confusion[g]["ASK"] for g in ACTIONS if g != "ASK"),
        "missed_ask": sum(confusion["ASK"][p] for p in ACTIONS if p != "ASK"),
        "inappropriate_wait": sum(confusion[g]["WAIT"] for g in ACTIONS if g != "WAIT"),
        "missed_wait": sum(confusion["WAIT"][p] for p in ACTIONS if p != "WAIT"),
        "wrong_goal_rate": _safe_rate(wrong_goal_num, wrong_goal_den),
        "mean_regret": mean(regrets) if regrets else None,
        "median_regret": median(regrets) if regrets else None,
        "query_rate": _safe_rate(actions["ASK"], n),
        "fallback_rate": _safe_rate(actions["FALLBACK"], n),
        "reason_code_correctness": {
            "exact_match": [reason_exact, n],
            "accepted_set_match": [accepted_match, n],
            "unsupported_reason_count": unsupported_reasons,
        },
    }
