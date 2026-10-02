"""Gold-unseal evaluator; inaccessible until raw predictions are immutable."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median
from typing import Any, Mapping

from .lifecycle import assert_gold_may_be_read


ACTIONS = ("ACT", "ASK", "WAIT", "FALLBACK")


def _f1(confusion: Mapping[str, Mapping[str, int]], label: str) -> float:
    tp = confusion[label][label]
    fp = sum(confusion[g][label] for g in ACTIONS if g != label)
    fn = sum(confusion[label][p] for p in ACTIONS if p != label)
    return 0.0 if 2 * tp + fp + fn == 0 else 2 * tp / (2 * tp + fp + fn)


def _metrics(rows: list[Mapping[str, Any]], gold: Mapping[str, Mapping[str, Any]], runtime: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    confusion = {g: {p: 0 for p in ACTIONS} for g in ACTIONS}
    regrets: list[float] = []
    candidate_num = candidate_den = equivalence_num = equivalence_den = 0
    reason_exact = accepted_match = unsupported = 0
    wrong_goal_num = wrong_goal_den = 0
    fallback_reasons = Counter()
    for p in rows:
        g = gold[p["case_id"]]
        confusion[g["gold_action_type"]][p["selected_action"]] += 1
        if g["act_subtype"] == "UNIQUE_CANDIDATE":
            candidate_den += 1; candidate_num += p["selected_candidate_id"] == g["gold_candidate_id"]
        if g["act_subtype"] == "EQUIVALENCE_CLASS":
            equivalence_den += 1; equivalence_num += p["selected_action"] == "ACT" and p["act_subtype"] == "EQUIVALENCE_CLASS"
        accepted = set(g["accepted_reason_code_set"]); predicted = set(p["decision_reason_codes"])
        reason_exact += predicted == accepted; accepted_match += bool(predicted.intersection(accepted)); unsupported += len(predicted - accepted)
        chosen_key = f"ACT_{p['selected_candidate_id']}" if p["selected_action"] == "ACT" and p["selected_candidate_id"] else p["selected_action"]
        losses = [float(v) for k, v in g["expected_loss_by_legal_action"].items() if v is not None and (k in ACTIONS or k.startswith("ACT_"))]
        chosen = g["expected_loss_by_legal_action"].get(chosen_key)
        regrets.append(0.0 if chosen is None or not losses else max(0.0, float(chosen) - min(losses)))
        if p["selected_action"] == "ACT" and p["selected_candidate_id"] and p["case_id"] in runtime:
            case = runtime[p["case_id"]]; latent = g["latent_true_intent"]
            cell = next((c for c in case["counterfactual_matrix"]["cells"] if c["action_candidate_id"] == p["selected_candidate_id"] and c["hypothesis_candidate_id"] == latent), None)
            if cell is not None and cell["task_error_cost"] is not None:
                wrong_goal_den += 1; wrong_goal_num += float(cell["task_error_cost"]) > 0
        if p["selected_action"] == "FALLBACK":
            if g["gold_action_type"] != "FALLBACK": fallback_reasons["false_fallback"] += 1
            else:
                fallback_reasons["justified_fallback"] += 1
                reasons = set(g["accepted_reason_code_set"])
                if "COUNTERFACTUAL_MATRIX_UNKNOWN" in reasons: fallback_reasons["matrix_unknown_fallback"] += 1
                if "CONTRACT_GATE_FAILED" in reasons: fallback_reasons["contract_fallback"] += 1
                if "DECISION_WINDOW_EXPIRED" in reasons: fallback_reasons["expired_deadline_fallback"] += 1
                if reasons.intersection({"NO_LEGAL_STRICT_DECISION", "ACTIVE_QUERY_WITHOUT_LEGAL_WAIT"}): fallback_reasons["unresolvable_information_fallback"] += 1
    n = len(rows); action_counts = Counter(p["selected_action"] for p in rows)
    correct = sum(confusion[a][a] for a in ACTIONS)
    per_recall = [confusion[a][a] / sum(confusion[a].values()) if sum(confusion[a].values()) else 0.0 for a in ACTIONS]
    return {
        "numerator_denominator": {"action_correct": [correct, n], "candidate_selection": [candidate_num, candidate_den],
                                  "equivalence_act": [equivalence_num, equivalence_den], "wrong_goal": [wrong_goal_num, wrong_goal_den]},
        "macro_f1": sum(_f1(confusion, a) for a in ACTIONS) / 4,
        "balanced_accuracy": sum(per_recall) / 4, "confusion_matrix": confusion,
        "exact_action_counts": dict(action_counts),
        "false_fallback": sum(confusion[g]["FALLBACK"] for g in ACTIONS if g != "FALLBACK"),
        "unsafe_unresolved_act": sum(confusion[g]["ACT"] for g in ("ASK", "WAIT", "FALLBACK")),
        "unnecessary_ask": sum(confusion[g]["ASK"] for g in ACTIONS if g != "ASK"),
        "missed_ask": sum(confusion["ASK"][p] for p in ACTIONS if p != "ASK"),
        "inappropriate_wait": sum(confusion[g]["WAIT"] for g in ACTIONS if g != "WAIT"),
        "missed_wait": sum(confusion["WAIT"][p] for p in ACTIONS if p != "WAIT"),
        "wrong_goal_rate": wrong_goal_num / wrong_goal_den if wrong_goal_den else None,
        "mean_regret": mean(regrets) if regrets else None, "median_regret": median(regrets) if regrets else None,
        "query_rate": action_counts["ASK"] / n if n else None, "fallback_rate": action_counts["FALLBACK"] / n if n else None,
        "fallback_decomposition": dict(fallback_reasons),
        "reason_codes": {"exact_match": [reason_exact, n], "accepted_set_match": [accepted_match, n],
                         "unsupported_reason_count": unsupported},
    }


def evaluate_after_unseal(state_path: Path, prediction_path: Path, sealed_gold_path: Path, runtime_path: Path | None = None) -> dict[str, Any]:
    state = assert_gold_may_be_read(state_path)
    if state["prediction_completeness"] != "COMPLETE":
        raise RuntimeError("PARTIAL_PREDICTION_EVENT_CANNOT_PRODUCE_RESULTS")
    predictions = json.loads(prediction_path.read_text(encoding="utf-8"))
    gold_package = json.loads(sealed_gold_path.read_text(encoding="utf-8"))
    gold = {row["case_id"]: row for row in gold_package["records"]}
    runtime_package = {"cases": []} if runtime_path is None else json.loads(runtime_path.read_text(encoding="utf-8"))
    runtime = {row["case_id"]: row for row in runtime_package["cases"]}
    results: dict[str, Any] = {"tracks": {}, "metric_count": 0,
                              "oracle_computed_only_after_prediction_publication": True}
    for track in ("R", "S"):
        track_rows = [p for p in predictions if p["track"] == track]
        if track == "S": track_rows = [p for p in track_rows if gold[p["case_id"]]["primary_core_member"]]
        grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for row in track_rows: grouped[row["comparison_id"]].append(row)
        comparison_results = {comparison_id: _metrics(rows, gold, runtime) for comparison_id, rows in sorted(grouped.items())}
        oracle_rows = [{"case_id": g["case_id"], "selected_action": g["gold_action_type"],
                        "selected_candidate_id": g["gold_candidate_id"], "act_subtype": g["act_subtype"],
                        "decision_reason_codes": g["accepted_reason_code_set"]}
                       for g in gold.values() if g["track"] == track and (track != "S" or g["primary_core_member"])]
        comparison_results["EVALUATION_ONLY_ORACLE_UPPER_BOUND"] = _metrics(oracle_rows, gold, runtime)
        results["tracks"][track] = {"comparisons": comparison_results}
        results["metric_count"] += sum(len(row) for row in comparison_results.values())
    return results
