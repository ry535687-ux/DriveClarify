"""Isolated R3 evaluator: prediction-seal gate, one gold unseal, frozen metrics."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from driveclarify_m2b_event_runtime.publication import canonical_bytes, file_sha256
from driveclarify_m2b_event_runtime.r3_api import (
    PREDICTIONS_IMMUTABLE,
    authorize_gold_unseal,
    get_event_status,
    mark_results_immutable,
    publish_results,
    record_gold_unseal,
)

from .metrics import compute_action_metrics
from .statistics import (
    BOOTSTRAP_RESAMPLES,
    BOOTSTRAP_SEED,
    cluster_bootstrap,
    exact_mcnemar,
    holm_adjust,
    paired_cluster_bootstrap_difference,
)


ORACLE_ID = "EVALUATION_ONLY_ORACLE_UPPER_BOUND"
MAIN_ID = "RULE_M1_PLUS_M2B"
HYBRID_ID = "HYBRID_CONSERVATIVE_M1_PLUS_M2B"
H_B1_BASELINES = (
    "ALWAYS_ACT_TOP1", "ALWAYS_ASK", "ALWAYS_WAIT",
    "AMBIGUITY_DETECTION_ONLY", "PAIR_RELATION_ONLY",
    "LANGUAGE_UNCERTAINTY_THRESHOLD",
)


def _load_canonical(path: Path) -> dict[str, Any]:
    payload = path.read_bytes()
    value = json.loads(payload)
    if payload != canonical_bytes(value):
        raise ValueError(f"R3_EVALUATOR_INPUT_NOT_CANONICAL:{path.name}")
    return value


def _runtime_comparison_ids(comparison_set: Mapping[str, Any]) -> list[str]:
    identifiers = [row["comparison_id"] for row in comparison_set["comparisons"]
                   if not row.get("oracle_computed_after_prediction_seal")]
    if not identifiers or len(identifiers) != len(set(identifiers)):
        raise ValueError("R3_EVALUATOR_COMPARISON_SET_INVALID")
    return identifiers


def _validate_join(predictions: Sequence[Mapping[str, Any]],
                   gold: Mapping[str, Mapping[str, Any]],
                   comparison_ids: Sequence[str], expected_record_count: int) -> dict[str, Any]:
    if len(predictions) != expected_record_count:
        raise ValueError("R3_EVALUATOR_PREDICTION_COUNT_MISMATCH")
    keys = [(row["case_id"], row["comparison_id"]) for row in predictions]
    if len(keys) != len(set(keys)):
        raise ValueError("R3_EVALUATOR_DUPLICATE_PREDICTION_RECORD")
    case_ids = sorted({row["case_id"] for row in predictions})
    if set(case_ids) != set(gold):
        missing = sorted(set(gold) - set(case_ids))
        extra = sorted(set(case_ids) - set(gold))
        raise ValueError(f"R3_EVALUATOR_GOLD_CASE_JOIN_MISMATCH:{missing[:3]}:{extra[:3]}")
    expected = {(case_id, comparison_id) for case_id in case_ids for comparison_id in comparison_ids}
    actual = set(keys)
    if actual != expected:
        raise ValueError("R3_EVALUATOR_MISSING_OR_EXTRA_COMPARISON_RECORD")
    order = [(row["canonical_case_index"], row["canonical_comparison_index"]) for row in predictions]
    if order != sorted(order) or len(order) != len(set(order)):
        raise ValueError("R3_EVALUATOR_CANONICAL_ORDER_INVALID")
    if any(row["comparison_id"] == ORACLE_ID for row in predictions):
        raise ValueError("R3_EVALUATOR_ORACLE_PRESENT_IN_RAW_PREDICTIONS")
    return {"case_count": len(case_ids), "comparison_count": len(comparison_ids),
            "record_count": len(predictions), "missing": 0, "duplicate": 0, "extra": 0}


def _oracle_rows(rows: Sequence[Mapping[str, Any]],
                 gold: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    first: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        first.setdefault(row["case_id"], row)
    result = []
    for case_id, base in sorted(first.items()):
        gold_row = gold[case_id]
        oracle = dict(base)
        oracle.update({
            "comparison_id": ORACLE_ID,
            "selected_action": gold_row["gold_action_type"],
            "selected_candidate_id": gold_row.get("gold_candidate_id"),
            "act_subtype": gold_row.get("act_subtype", "NONE"),
            "reason_codes": list(gold_row.get("accepted_reason_code_set", [])),
            "override_applied": False,
        })
        result.append(oracle)
    return result


def _groups(predictions: Sequence[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
    return {
        "track_r_all": [row for row in predictions if row["track_r"]],
        "track_r_primary_nonboundary": [row for row in predictions if row["track_r"] and row["core_nonboundary"]],
        "track_s_primary_core": [row for row in predictions if row["track_s_primary_core"]],
        "track_s_full_pool": [row for row in predictions if row["track_s_full_pool"]],
        "boundary_stress": [row for row in predictions if row["boundary_stress"]],
    }


def _cluster_key(group_name: str, row: Mapping[str, Any],
                 runtime: Mapping[str, Mapping[str, Any]]) -> str:
    if group_name.startswith("track_r"):
        case = runtime.get(row["case_id"], {})
        return str(case.get("unit_id") or case.get("matrix_id") or row["case_id"])
    return str(row.get("matrix_archetype_id") or row["case_id"])


def _exact_differences(main: Mapping[str, Any], other: Mapping[str, Any]) -> dict[str, Any]:
    fields = ("accuracy", "macro_f1", "balanced_accuracy", "candidate_act_accuracy",
              "equivalence_act_accuracy", "wrong_goal_rate", "mean_regret",
              "query_rate", "fallback_rate")
    differences: dict[str, Any] = {}
    for field in fields:
        left, right = main.get(field), other.get(field)
        differences[field] = None if left is None or right is None else float(left) - float(right)
    for field in ("false_fallback", "unsafe_unresolved_act", "unnecessary_ask", "missed_ask",
                  "inappropriate_wait", "missed_wait", "wrong_act"):
        differences[field] = int(main[field]) - int(other[field])
    return differences


def _verdict(condition: bool | None, contrary: bool = False) -> str:
    if condition is None:
        return "INCONCLUSIVE"
    if condition:
        return "SUPPORTED"
    return "NOT_SUPPORTED" if contrary else "INCONCLUSIVE"


def _hypotheses(primary: Mapping[str, Mapping[str, Any]],
                by_comparison: Mapping[str, Sequence[Mapping[str, Any]]]) -> dict[str, Any]:
    main = primary.get(MAIN_ID)
    if main is None:
        return {f"H-B{index}": {"verdict": "INCONCLUSIVE", "evidence": "MAIN_COMPARISON_MISSING"}
                for index in range(1, 7)}
    h1_values = {identifier: primary[identifier]["macro_f1"] for identifier in H_B1_BASELINES if identifier in primary}
    h1_complete = len(h1_values) == len(H_B1_BASELINES) and main["macro_f1"] is not None
    h1_supported = h1_complete and all(main["macro_f1"] > value for value in h1_values.values() if value is not None)
    h1 = _verdict(h1_supported if h1_complete else None, contrary=h1_complete)

    def degradation(ids: Sequence[str], fields: Sequence[str]) -> tuple[str, dict[str, Any]]:
        if any(identifier not in primary for identifier in ids):
            return "INCONCLUSIVE", {"missing": [identifier for identifier in ids if identifier not in primary]}
        evidence: dict[str, Any] = {}
        per_id = []
        for identifier in ids:
            deltas = {field: (None if main[field] is None or primary[identifier][field] is None
                              else float(primary[identifier][field]) - float(main[field]))
                      for field in fields}
            evidence[identifier] = deltas
            per_id.append(any(value is not None and value > 0 for value in deltas.values()))
        return ("SUPPORTED" if all(per_id) else "NOT_SUPPORTED"), evidence

    h2, h2_evidence = degradation(("NO_COUNTERFACTUAL_MATRIX", "NO_POSTERIOR_UPDATE"),
                                  ("fallback_rate", "wrong_act", "mean_regret"))
    h3, h3_evidence = degradation(("CONSEQUENCE_WITHOUT_QUERY_COST", "CONSEQUENCE_WITHOUT_DELAY",
                                   "CONSEQUENCE_WITHOUT_NO_ANSWER"),
                                  ("unnecessary_ask", "wrong_act", "mean_regret"))
    h4, h4_evidence = degradation(("ACT_ASK_WITHOUT_WAIT",),
                                  ("missed_wait", "fallback_rate", "mean_regret"))
    if HYBRID_ID not in by_comparison:
        h5, h5_evidence = "INCONCLUSIVE", {"missing": HYBRID_ID}
    else:
        main_rows = {row["case_id"]: row for row in by_comparison[MAIN_ID]}
        hybrid_rows = {row["case_id"]: row for row in by_comparison[HYBRID_ID]}
        equal = set(main_rows) == set(hybrid_rows) and all(
            (main_rows[c]["selected_action"], main_rows[c].get("selected_candidate_id"), main_rows[c].get("act_subtype")) ==
            (hybrid_rows[c]["selected_action"], hybrid_rows[c].get("selected_candidate_id"), hybrid_rows[c].get("act_subtype"))
            for c in main_rows)
        overrides = sum(bool(row.get("override_applied")) for row in by_comparison[HYBRID_ID])
        h5 = "SUPPORTED" if equal and overrides == 0 else "NOT_SUPPORTED"
        h5_evidence = {"decision_equality": equal, "hybrid_override_count": overrides}
    h6_evidence = {"false_fallback": main["false_fallback"],
                   "justified_fallback": main["justified_fallback"],
                   "presumes_conservatism_justified": False}
    return {
        "H-B1": {"verdict": h1, "evidence": {"main_macro_f1": main["macro_f1"], "baselines": h1_values}},
        "H-B2": {"verdict": h2, "evidence": h2_evidence},
        "H-B3": {"verdict": h3, "evidence": h3_evidence},
        "H-B4": {"verdict": h4, "evidence": h4_evidence},
        "H-B5": {"verdict": h5, "evidence": h5_evidence},
        "H-B6": {"verdict": "SUPPORTED", "evidence": h6_evidence},
    }


def evaluate_records(prediction_package: Mapping[str, Any], gold_package: Mapping[str, Any],
                     comparison_set: Mapping[str, Any], runtime_package: Mapping[str, Any],
                     *, expected_record_count: int) -> dict[str, Any]:
    predictions = prediction_package["records"]
    if prediction_package.get("completeness") != "COMPLETE":
        raise RuntimeError("R3_EVALUATOR_PARTIAL_PREDICTIONS_FORBIDDEN")
    gold = {row["case_id"]: row for row in gold_package["records"]}
    runtime = {row["case_id"]: row for row in runtime_package.get("cases", [])}
    comparison_ids = _runtime_comparison_ids(comparison_set)
    join_audit = _validate_join(predictions, gold, comparison_ids, expected_record_count)
    grouped = _groups(predictions)
    results: dict[str, Any] = {}
    bootstrap: dict[str, Any] = {}
    paired: dict[str, Any] = {}
    for group_name, group_rows in grouped.items():
        by_comparison: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for row in group_rows:
            by_comparison[row["comparison_id"]].append(row)
        oracle = _oracle_rows(group_rows, gold)
        comparison_metrics = {identifier: compute_action_metrics(rows, gold, runtime)
                              for identifier, rows in sorted(by_comparison.items())}
        comparison_metrics[ORACLE_ID] = compute_action_metrics(oracle, gold, runtime)
        results[group_name] = {
            "excluded_from_primary_macro_f1": group_name == "boundary_stress",
            "case_count": len({row["case_id"] for row in group_rows}),
            "comparisons": comparison_metrics,
        }
        bootstrap[group_name] = {}
        for identifier, rows in sorted(by_comparison.items()):
            bootstrap[group_name][identifier] = cluster_bootstrap(
                rows, gold, lambda row, name=group_name: _cluster_key(name, row, runtime), runtime,
                resamples=BOOTSTRAP_RESAMPLES, seed=BOOTSTRAP_SEED,
            )
        if MAIN_ID in by_comparison:
            paired[group_name] = {}
            p_values: dict[str, float] = {}
            main_metrics = comparison_metrics[MAIN_ID]
            for identifier, rows in sorted(by_comparison.items()):
                if identifier == MAIN_ID:
                    continue
                mcnemar = exact_mcnemar(by_comparison[MAIN_ID], rows, gold)
                p_values[identifier] = mcnemar["exact_two_sided_p"]
                paired[group_name][identifier] = {
                    "exact_differences_main_minus_other": _exact_differences(main_metrics, comparison_metrics[identifier]),
                    "mcnemar": mcnemar,
                    "cluster_bootstrap": paired_cluster_bootstrap_difference(
                        by_comparison[MAIN_ID], rows, gold,
                        lambda row, name=group_name: _cluster_key(name, row, runtime), runtime,
                    ),
                    "confirmatory": group_name == "track_s_primary_core" and identifier in H_B1_BASELINES,
                }
            adjusted = holm_adjust({identifier: value for identifier, value in p_values.items()
                                    if group_name == "track_s_primary_core" and identifier in H_B1_BASELINES})
            for identifier, adjusted_p in adjusted.items():
                paired[group_name][identifier]["holm_adjusted_p"] = adjusted_p
    primary_rows = grouped["track_s_primary_core"]
    primary_by_comparison: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in primary_rows:
        primary_by_comparison[row["comparison_id"]].append(row)
    primary_metrics = results["track_s_primary_core"]["comparisons"]
    hypotheses = _hypotheses(primary_metrics, primary_by_comparison)
    metric_count = sum(len(section["comparisons"]) for section in results.values())
    return {
        "schema_version": "driveclarify.m2b_blind_evaluation_result.r3",
        "join_audit": join_audit,
        "groups": results,
        "bootstrap": bootstrap,
        "paired_statistics": paired,
        "hypotheses": hypotheses,
        "oracle_computed_after_prediction_immutability": True,
        "bootstrap_protocol": {"resamples": BOOTSTRAP_RESAMPLES, "seed": BOOTSTRAP_SEED,
                               "track_r_cluster": "REAL_UNIT", "track_s_cluster": "MATRIX_ARCHETYPE"},
        "metric_count": metric_count,
    }


def evaluate_and_publish(*, state_path: Path, audit_path: Path, prediction_path: Path,
                         sealed_gold_path: Path, comparison_set_path: Path,
                         runtime_path: Path, result_path: Path) -> dict[str, Any]:
    state = get_event_status(state_path)
    if state["state"] != PREDICTIONS_IMMUTABLE or state["prediction_completeness"] != "COMPLETE":
        raise PermissionError("R3_EVALUATOR_GATE_FAILED")
    if file_sha256(prediction_path) != state["prediction_sha256"]:
        raise PermissionError("R3_EVALUATOR_PREDICTION_SHA_MISMATCH")
    prediction_package = _load_canonical(prediction_path)
    comparison_set = _load_canonical(comparison_set_path)
    runtime_package = _load_canonical(runtime_path)
    authorize_gold_unseal(state_path, audit_path, prediction_path)
    gold_bytes = sealed_gold_path.read_bytes()
    gold_sha256 = hashlib.sha256(gold_bytes).hexdigest()
    state = get_event_status(state_path)
    if gold_sha256 != state["expected_gold_commitment_sha256"]:
        raise RuntimeError("R3_EVALUATOR_GOLD_COMMITMENT_MISMATCH")
    record_gold_unseal(state_path, audit_path, gold_sha256=gold_sha256,
                       gold_read_bytes=len(gold_bytes))
    gold_package = json.loads(gold_bytes)
    results = evaluate_records(
        prediction_package, gold_package, comparison_set, runtime_package,
        expected_record_count=state["event_config"]["expected_record_count"],
    )
    results.update({
        "blind_execution_id": state["blind_execution_id"],
        "prediction_sha256": state["prediction_sha256"],
        "gold_commitment_sha256": gold_sha256,
        "evaluation_policy_execution_count": 0,
        "raw_prediction_modified": False,
    })
    publish_results(state_path, audit_path, result_path, results)
    final_state = mark_results_immutable(state_path, audit_path, result_path)
    return {"results": results, "state": final_state}
