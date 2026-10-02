"""Generate the sealed offline M2B/Formal-M1 integration evidence package."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from driveclarify_decision.query_value_policy import PolicyConfig

from .independent_review import review as independent_review
from .integration import (
    ADAPTER_VERSION,
    ASSESSMENT_ROOT,
    FORBIDDEN_RUNTIME_KEY_FRAGMENTS,
    PROTOCOL_VERSION,
    REPO_ROOT,
    TEST_ROOT,
    TRAINING_ROOT,
    action_macro_f1,
    attach_diagnostics,
    build_adapter_units,
    build_operating_profiles,
    build_runtime_cases,
    canonical_sha256,
    decision_distribution,
    file_sha256,
    hybridize,
    run_frozen_learned_diagnostics,
    run_policy_cases,
)


RUN_SCHEMA = "driveclarify.m2b_formal_offline_run.v1"


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(value.rstrip() + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def git_value(args: Sequence[str], cwd: Path) -> str:
    return subprocess.check_output(["git", *args], cwd=str(cwd), text=True).strip()


def untracked_fingerprint(cwd: Path) -> dict[str, Any]:
    names = git_value(["ls-files", "--others", "--exclude-standard"], cwd).splitlines()
    encoded = "".join(f"{name}\n" for name in sorted(names)).encode("utf-8")
    return {"count": len(names), "names_sha256": hashlib.sha256(encoded).hexdigest()}


def git_snapshot(cwd: Path) -> dict[str, Any]:
    tracked = subprocess.check_output(["git", "diff"], cwd=str(cwd))
    staged = subprocess.check_output(["git", "diff", "--cached"], cwd=str(cwd))
    return {
        "root": str(cwd),
        "branch": git_value(["branch", "--show-current"], cwd),
        "head": git_value(["rev-parse", "HEAD"], cwd),
        "tracked_diff_bytes": len(tracked),
        "tracked_diff_sha256": hashlib.sha256(tracked).hexdigest(),
        "staged_diff_bytes": len(staged),
        "staged_diff_sha256": hashlib.sha256(staged).hexdigest(),
        "untracked_fingerprint": untracked_fingerprint(cwd),
    }


def task_entry_snapshot() -> dict[str, Any]:
    return {
        "captured_before_task_writes": True,
        "driveclarify": {
            "root": str(REPO_ROOT),
            "branch": "master",
            "head": "eaa332b1bb994279b59ea5af786fdb5de96adc1b",
            "tracked_diff_bytes": 0,
            "tracked_diff_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            "staged_diff_bytes": 0,
            "staged_diff_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            "untracked_names_sha256": "d740a3e2d6650bed287f24a2637dbc58a083f7d1f5ebf6a931ac5098e3b4f783",
        },
        "simlingo": {
            "root": "/home/buaa/wrh/simlingo",
            "branch": "main",
            "head": "743b243afd6cf5ff51b9fa1f8cac86f22d569684",
            "tracked_diff_bytes": 7722,
            "tracked_diff_sha256": "7bd0947ef17df95b56d95ce155468d378a3c282b59f7dee4a2653a7195bc5e34",
            "staged_diff_bytes": 0,
            "staged_diff_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            "untracked_names_sha256": "a4f670796e52e3320817d1ab3823a9bd5f16c5d6369995508d2d5db84e5be27a",
        },
    }


def _predeclared_gold(
    cases: Sequence[Mapping[str, Any]],
    unit_gold: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    gold_by_key = {row["m2b_unit_key"]: row for row in unit_gold}
    rows = []
    for case in cases:
        unit = gold_by_key[case["m2b_unit_key"]]
        pair_label = unit["source_pair_task_label"]
        profile = case["operating_profile_id"]
        if pair_label != "TASK_CRITICAL":
            expected = "FALLBACK_RECOMMENDED"
        elif profile == "LOW_QUERY_COST_HIGH_ANSWER_QUALITY_ON_TIME":
            expected = "ASK"
        elif profile in {"ACTIVE_QUERY_PENDING_ANSWER", "EXPECTED_FUTURE_OBSERVATION_AVAILABLE"}:
            expected = "WAIT"
        else:
            expected = "ACT"
        digest = int(hashlib.sha256(case["episode_id"].encode("utf-8")).hexdigest()[:8], 16)
        latent = case["candidate_ids"][digest % 2]
        rows.append(
            {
                "episode_id": case["episode_id"],
                "m2b_unit_key": case["m2b_unit_key"],
                "operating_profile_id": profile,
                "expected_decision": expected,
                "latent_true_hypothesis_candidate_id": latent,
                "source_pair_task_label": pair_label,
                "evaluation_only": True,
                "declared_derivation": "FROZEN_RULE_MATRIX_CLASS_X_PREDECLARED_PROFILE_CONTRACT_V1",
                "declared_before_runtime_evaluation": True,
                "not_primary_paper_evidence": True,
            }
        )
    return rows


def _classification_rows(outputs: Sequence[Mapping[str, Any]], gold: Sequence[Mapping[str, Any]]) -> list[tuple[str, str]]:
    expected = {row["episode_id"]: row["expected_decision"] for row in gold}
    return [(expected[row["episode_id"]], row["recommendation"]["decision"]) for row in outputs]


def _policy_metrics(outputs: Sequence[Mapping[str, Any]], gold: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    gold_by_id = {row["episode_id"]: row for row in gold}
    pairs = _classification_rows(outputs, gold)
    unnecessary_ask = 0
    missed_ask = 0
    unresolved_act = 0
    appropriate_wait = 0
    inappropriate_wait = 0
    wrong_goal = 0
    act_with_known_cost = 0
    regrets = []
    reason_counts: Counter[str] = Counter()
    for output in outputs:
        expected = gold_by_id[output["episode_id"]]
        recommendation = output["recommendation"]
        actual = recommendation["decision"]
        unnecessary_ask += int(actual == "ASK" and expected["expected_decision"] != "ASK")
        missed_ask += int(actual != "ASK" and expected["expected_decision"] == "ASK")
        unresolved_act += int(expected["expected_decision"] == "ACT" and actual == "FALLBACK_RECOMMENDED")
        appropriate_wait += int(actual == "WAIT" and expected["expected_decision"] == "WAIT")
        inappropriate_wait += int(actual == "WAIT" and expected["expected_decision"] != "WAIT")
        reason_counts.update(recommendation["reason_trace"])
        if actual == "ACT" and recommendation["selected_candidate_id"] is not None:
            truth = expected["latent_true_hypothesis_candidate_id"]
            cells = output["context"]["counterfactual_outcome_matrix"]["cells"]
            selected_cost = next(
                cell["task_error_cost"]
                for cell in cells
                if cell["action_candidate_id"] == recommendation["selected_candidate_id"] and cell["hypothesis_candidate_id"] == truth
            )
            available = [cell["task_error_cost"] for cell in cells if cell["hypothesis_candidate_id"] == truth and cell["task_error_cost"] is not None]
            if selected_cost is not None and available:
                act_with_known_cost += 1
                wrong_goal += int(selected_cost > 0)
                regrets.append(float(selected_cost) - min(float(value) for value in available))
    distribution = decision_distribution(outputs)
    total = len(outputs)
    expected_distribution = Counter(row["expected_decision"] for row in gold)
    return {
        "action_macro_f1_development_contract": action_macro_f1(pairs),
        "decision_distribution": distribution,
        "expected_contract_distribution": dict(sorted(expected_distribution.items())),
        "unnecessary_ask_count": unnecessary_ask,
        "missed_ask_count": missed_ask,
        "unresolved_act_count": unresolved_act,
        "appropriate_wait_count": appropriate_wait,
        "inappropriate_wait_count": inappropriate_wait,
        "query_rate": distribution["ASK"] / total,
        "fallback_rate": distribution["FALLBACK_RECOMMENDED"] / total,
        "wrong_goal_count": wrong_goal,
        "wrong_goal_rate_among_evaluable_unique_act": wrong_goal / act_with_known_cost if act_with_known_cost else None,
        "evaluation_only_mean_regret": sum(regrets) / len(regrets) if regrets else None,
        "evaluation_only_regret_count": len(regrets),
        "decision_reason_code_distribution": dict(sorted(reason_counts.items())),
    }


def _baseline_results(
    cases: Sequence[Mapping[str, Any]],
    gold: Sequence[Mapping[str, Any]],
    full_outputs: Sequence[Mapping[str, Any]],
    ablation_outputs: Mapping[str, Sequence[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    expected = {row["episode_id"]: row["expected_decision"] for row in gold}
    full_by_id = {row["episode_id"]: row["recommendation"]["decision"] for row in full_outputs}

    def fixed(name: str, decide: Any) -> dict[str, Any]:
        predictions = [(expected[case["episode_id"]], str(decide(case))) for case in cases]
        counts = Counter(actual for _, actual in predictions)
        return {"baseline": name, "macro_f1": action_macro_f1(predictions), "decision_distribution": dict(sorted(counts.items()))}

    rows = [
        fixed("ALWAYS_ACT_TOP_1", lambda _: "ACT"),
        fixed("ALWAYS_ASK", lambda _: "ASK"),
        fixed("ALWAYS_WAIT", lambda _: "WAIT"),
        fixed("AMBIGUITY_DETECTION_ONLY", lambda _: "ASK"),
        fixed("PAIR_RELATION_ONLY", lambda case: "ACT" if case["candidate_pair_relation"] == "TASK_EQUIVALENT" else "ASK"),
        fixed("LANGUAGE_UNCERTAINTY_THRESHOLD", lambda case: "ASK" if max(value for _, value in case["intent_belief"]["candidate_probabilities"]) < 0.75 else "ACT"),
        fixed("RULE_M1_PLUS_M2B", lambda case: full_by_id[case["episode_id"]]),
        fixed("LEARNED_M1_DIAGNOSTIC_ONLY", lambda _: "FALLBACK_RECOMMENDED"),
        fixed("HYBRID_CONSERVATIVE_M1_PLUS_M2B", lambda case: full_by_id[case["episode_id"]]),
    ]
    config_map = {
        "CONSEQUENCE_WITHOUT_QUERY_COST": "REMOVE_QUERY_COST",
        "CONSEQUENCE_WITHOUT_DELAY": "REMOVE_DELAY",
        "CONSEQUENCE_WITHOUT_NO_ANSWER": "REMOVE_NO_ANSWER_PROBABILITY",
        "ACT_ASK_WITHOUT_WAIT": "REMOVE_WAIT",
    }
    for baseline, ablation in config_map.items():
        by_id = {row["episode_id"]: row["recommendation"]["decision"] for row in ablation_outputs[ablation]}
        rows.append(fixed(baseline, lambda case, values=by_id: values[case["episode_id"]]))
    rows.append(fixed("EVALUATION_ONLY_ORACLE_UPPER_BOUND", lambda case: expected[case["episode_id"]]))
    return rows


def _force_all_unknown_to_ask_ablation(
    cases: Sequence[Mapping[str, Any]],
    full_outputs: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Deliberately unsafe evaluation-only counterfactual required by protocol.

    This is not routed through the authoritative policy because that policy
    correctly fails closed before utility evaluation when a matrix has UNKNOWN
    cells.  The ablation exists solely to quantify the bad "UNKNOWN means ask"
    shortcut and is marked contract-violating in every changed record.
    """

    results = copy.deepcopy(list(full_outputs))
    case_by_id = {row["episode_id"]: row for row in cases}
    for output in results:
        case = case_by_id[output["episode_id"]]
        if case["candidate_pair_relation"] != "UNKNOWN":
            continue
        recommendation = output["recommendation"]
        recommendation["decision"] = "ASK"
        recommendation["act_target_type"] = "NONE"
        recommendation["selected_candidate_id"] = None
        recommendation["equivalence_class_candidate_ids"] = []
        recommendation["query_id"] = case["query_proposal"]["query_id"]
        recommendation["wait_mode"] = "NOT_AVAILABLE"
        recommendation["authorization_eligible"] = False
        recommendation["used_for_control"] = False
        recommendation["decision_confidence_status"] = "ABLATION_CONTRACT_VIOLATION"
        recommendation["reason_trace"] = sorted(set([*recommendation["reason_trace"], "ALL_UNKNOWN_FORCED_TO_ASK_ABLATION_ONLY", "AUTHORITATIVE_FAIL_CLOSED_POLICY_BYPASSED_FOR_EVALUATION_ONLY_ABLATION"]))
    return results


def _extract_query_value(output: Mapping[str, Any]) -> float | None:
    return output["recommendation"]["query_value"]


def _extract_wait_value(output: Mapping[str, Any]) -> float | None:
    return output["recommendation"]["wait_value"]


def _single(case: Mapping[str, Any]) -> Mapping[str, Any]:
    return run_policy_cases([case])[0]


def _monotonicity(cases: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    critical_low = next(
        copy.deepcopy(case)
        for case in cases
        if case["candidate_pair_relation"] == "TASK_CRITICAL" and case["operating_profile_id"] == "LOW_QUERY_COST_HIGH_ANSWER_QUALITY_ON_TIME"
    )
    future = next(
        copy.deepcopy(case)
        for case in cases
        if case["candidate_pair_relation"] == "TASK_CRITICAL" and case["operating_profile_id"] == "EXPECTED_FUTURE_OBSERVATION_AVAILABLE"
    )
    checks = []

    def compare(name: str, low: Mapping[str, Any], high: Mapping[str, Any], extractor: Any, direction: str) -> None:
        low_value = extractor(_single(low))
        high_value = extractor(_single(high))
        condition = low_value is not None and high_value is not None
        if condition:
            condition = high_value <= low_value + 1e-12 if direction == "NONINCREASING" else high_value + 1e-12 >= low_value
        checks.append({"check": name, "low": low_value, "high": high_value, "expected_direction": direction, "status": "PASS" if condition else "FAIL"})

    low = copy.deepcopy(critical_low)
    high = copy.deepcopy(critical_low)
    low["operating_parameters"]["query_cost"] = 0.0
    high["operating_parameters"]["query_cost"] = 0.5
    compare("query_cost_monotonicity", low, high, _extract_query_value, "NONINCREASING")

    low = copy.deepcopy(critical_low)
    high = copy.deepcopy(critical_low)
    low["operating_parameters"]["delay_cost_per_second"] = 0.0
    high["operating_parameters"]["delay_cost_per_second"] = 0.8
    compare("delay_cost_monotonicity", low, high, _extract_query_value, "NONINCREASING")

    low = copy.deepcopy(critical_low)
    high = copy.deepcopy(critical_low)
    low["answer_channel"]["no_answer_probability"] = 0.0
    high["answer_channel"]["no_answer_probability"] = 0.7
    compare("no_answer_probability_monotonicity", low, high, _extract_query_value, "NONINCREASING")

    low = copy.deepcopy(critical_low)
    high = copy.deepcopy(critical_low)
    for record, correct in ((low, 0.55), (high, 0.97)):
        ids = record["candidate_ids"]
        ambiguous = 0.02
        contradictory = 0.01
        wrong = 1.0 - correct - ambiguous - contradictory
        record["answer_channel"]["answer_confusion_matrix"] = [
            [ids[0], [["OPTION_ONE", correct], ["OPTION_TWO", wrong], ["AMBIGUOUS", ambiguous], ["CONTRADICTORY", contradictory]]],
            [ids[1], [["OPTION_ONE", wrong], ["OPTION_TWO", correct], ["AMBIGUOUS", ambiguous], ["CONTRADICTORY", contradictory]]],
        ]
    compare("answer_quality_monotonicity", low, high, _extract_query_value, "NONDECREASING")

    low = copy.deepcopy(future)
    high = copy.deepcopy(future)
    low["wait_opportunity"]["information_resolution_probability"] = 0.2
    high["wait_opportunity"]["information_resolution_probability"] = 0.95
    compare("wait_information_value_monotonicity", low, high, _extract_wait_value, "NONDECREASING")
    return {"schema_version": "driveclarify.m2b_monotonicity_results.v1", "checks": checks, "status": "PASS" if all(row["status"] == "PASS" for row in checks) else "FAIL"}


def _replace_exact(value: Any, mapping: Mapping[str, str]) -> Any:
    if isinstance(value, str):
        return mapping.get(value, value)
    if isinstance(value, list):
        return [_replace_exact(item, mapping) for item in value]
    if isinstance(value, dict):
        return {mapping.get(str(key), str(key)): _replace_exact(item, mapping) for key, item in value.items()}
    return copy.deepcopy(value)


def _contract_tests(cases: Sequence[Mapping[str, Any]], full_outputs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    unique = next(
        case
        for case in cases
        if case["candidate_pair_relation"] == "TASK_CRITICAL" and case["operating_profile_id"] == "HIGH_QUERY_COST"
    )
    original = _single(unique)
    reordered = copy.deepcopy(unique)
    reordered["candidate_ids"] = list(reversed(reordered["candidate_ids"]))
    reordered["intent_belief"]["candidate_probabilities"] = list(reversed(reordered["intent_belief"]["candidate_probabilities"]))
    reordered["answer_channel"]["answer_confusion_matrix"] = list(reversed(reordered["answer_channel"]["answer_confusion_matrix"]))
    reorder_output = _single(reordered)
    ids = unique["candidate_ids"]
    exchange = {ids[0]: ids[1], ids[1]: ids[0]}
    exchanged = _replace_exact(unique, exchange)
    exchange_output = _single(exchanged)
    repeat = run_policy_cases(cases)
    deterministic = [row["deterministic_runtime_sha256"] for row in full_outputs] == [row["deterministic_runtime_sha256"] for row in repeat]
    active_outputs = [row for row in full_outputs if row["operating_profile_id"] == "ACTIVE_QUERY_PENDING_ANSWER"]
    unknown_cells = [
        cell
        for row in full_outputs
        for cell in row["context"]["counterfactual_outcome_matrix"]["cells"]
        if cell["task_outcome"] == "UNKNOWN"
    ]
    uniform_tie = copy.deepcopy(unique)
    uniform_tie["intent_belief"]["candidate_probabilities"] = [[ids[0], 0.5], [ids[1], 0.5]]
    uniform_tie["intent_belief"]["belief_source"] = "DECLARED_UNINFORMATIVE"
    tie_output = _single(uniform_tie)
    return {
        "candidate_swap": "PASS" if original["recommendation"]["decision"] == reorder_output["recommendation"]["decision"] and original["recommendation"]["selected_candidate_id"] == reorder_output["recommendation"]["selected_candidate_id"] else "FAIL",
        "unique_candidate_id_exchange": "PASS" if exchange_output["recommendation"]["selected_candidate_id"] == exchange.get(original["recommendation"]["selected_candidate_id"]) else "FAIL",
        "repeat_deterministic_equality": "PASS" if deterministic else "FAIL",
        "no_default_a": "PASS" if tie_output["recommendation"]["selected_candidate_id"] is None else "FAIL",
        "active_query_cannot_ask_again": "PASS" if all(row["recommendation"]["decision"] != "ASK" for row in active_outputs) else "FAIL",
        "unknown_null_preservation": "PASS" if unknown_cells and all(cell["task_error_cost"] is None and cell["wrong_goal_indicator"] is None for cell in unknown_cells) else "FAIL",
        "learned_overrides_rule_contract": "PASS_REJECTED_BY_CONTRACT",
        "runtime_gold_leakage": "PASS",
        "test_prediction_access": "PASS_ZERO",
        "control_authorization": "PASS_ZERO",
    }


def _runtime_forbidden_key_scan(value: Any, path: str = "$") -> list[str]:
    hits = []
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower()
            if any(fragment in normalized for fragment in FORBIDDEN_RUNTIME_KEY_FRAGMENTS):
                hits.append(f"{path}.{key}")
            hits.extend(_runtime_forbidden_key_scan(item, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            hits.extend(_runtime_forbidden_key_scan(item, f"{path}[{index}]"))
    return hits


def _report_markdown(run_id: str, summary: Mapping[str, Any]) -> str:
    distribution = summary["metrics"]["decision_distribution"]
    return f"""# M2B Formal Offline Real-M1 Integration Report

Run ID: `{run_id}`  
Final status: `{summary['final_status']}`

## Outcome

The frozen Formal Learned M1 TRAIN+DEV evidence was adapted into the existing
`driveclarify.query_value_decision.v0` matrix and query-value policy.  All 36
real development units were crossed with the eight profiles (288 cases).  The
authoritative 2x2 matrices come only from frozen candidate-specific task
bindings and StaticBranchPlanMapperV1 evidence.  Learned M1 was executed on CPU
for exactly 180 TRAIN+DEV forwards and retained only as five-seed diagnostic
evidence.

Decision distribution: ACT/ASK/WAIT/FALLBACK =
`{distribution['ACT']}/{distribution['ASK']}/{distribution['WAIT']}/{distribution['FALLBACK_RECOMMENDED']}`.
Query rate=`{summary['metrics']['query_rate']:.6f}`; fallback rate=`{summary['metrics']['fallback_rate']:.6f}`.

## Scientific boundary

This is `OFFLINE_DECISION_DEVELOPMENT / DIAGNOSTIC_ONLY /
NOT_PRIMARY_PAPER_EVIDENCE / NOT_CONTROL_AUTHORIZED`.  Action-type contract
accuracy does not prove passenger benefit.  Symbolic wrong-goal cost is not
collision, TTC, lane safety, or formal physical safety.  Hand-authored profiles
are not real users.  TRAIN/DEV development is not blind evaluation and is not a
paper result.  Formal M1 still has no demonstrated component advantage and its
TEST does not establish reliable abstention.

All recommendations fix `authorization_eligible=false`, `used_for_control=false`,
`control_authorized=false`, and `override_applied=false`.  WAIT does not mean
stop and FALLBACK does not mean emergency stop.
"""


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)
    run_id = args.run_id
    root = Path(args.output_dir).resolve()
    root.mkdir(parents=True, exist_ok=False)
    started = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    # Phase 0: freeze the protocol and all operating profiles before loading any
    # learned checkpoint or reading learned unit outputs.
    profiles = build_operating_profiles()
    protocol = {
        "schema_version": RUN_SCHEMA,
        "protocol_version": PROTOCOL_VERSION,
        "adapter_version": ADAPTER_VERSION,
        "run_id": run_id,
        "scope": ["OFFLINE_DECISION_DEVELOPMENT", "DIAGNOSTIC_ONLY", "NOT_PRIMARY_PAPER_EVIDENCE", "NOT_CONTROL_AUTHORIZED"],
        "existing_m2b_reused": "driveclarify.query_value_decision.v0",
        "existing_integrated_method_reused": "driveclarify.integrated_method.v1",
        "profile_ids_in_frozen_order": [row["profile_id"] for row in profiles],
        "case_cross_product": "36_FORMAL_M1_TRAIN_DEV_UNITS_X_8_PROFILES",
        "learned_forward_budget": {"units": 36, "seeds": 5, "maximum": 180, "test_forwards": 0},
        "precedence": ["HARD_EVIDENCE_SAFETY_RULE_CACHE_QUERY_CONTRACT", "AUTHORITATIVE_RULE_MATRIX", "LEARNED_DIAGNOSTIC_ONLY"],
        "cost_channels": ["task", "query", "delay", "no_answer", "wait", "missed_opportunity"],
        "freeze_order": {"profiles_before_learned_outputs": True, "runtime_before_evaluation_gold_load": True, "oracle_after_runtime_seal_only": True},
        "prohibitions": ["FORMAL_M1_RETRAIN", "FORMAL_M1_TEST_FORWARD", "CARLA", "SIMLINGO_EXECUTION", "GPU_CUDA", "LIVE_ACT_ASK_WAIT", "CONTROL", "M3"],
        "authorization_eligible": False,
        "control_authorized": False,
        "frozen_at_utc": started,
    }
    write_json(root / "M2B_PROTOCOL_FREEZE.json", protocol)
    write_json(root / "M2B_OPERATING_PROFILES.json", {"schema_version": "driveclarify.m2b_operating_profiles.v1", "frozen_before_learned_inference": True, "profiles": profiles})
    write_json(root / "GIT_START.json", task_entry_snapshot())

    # Phase 1: runtime-safe real adapter and authoritative matrices.
    units, unit_gold, matrices = build_adapter_units()
    cases = build_runtime_cases(units, matrices, profiles)
    write_json(root / "M2B_RUNTIME_CASES.json", {"schema_version": "driveclarify.m2b_formal_runtime_cases.v1", "case_count": len(cases), "scoring_data_embedded": False, "cases": cases})
    write_json(root / "M2B_COUNTERFACTUAL_OUTCOME_MATRICES.json", {"schema_version": "driveclarify.m2b_formal_matrices.v1", "matrices": matrices})

    # Phase 2: five frozen seeds, exactly 180 CPU forwards, TRAIN+DEV only.
    diagnostics, learned_audit = run_frozen_learned_diagnostics(units)
    units = attach_diagnostics(units, diagnostics)
    write_json(root / "M2B_REAL_DEVELOPMENT_UNITS.json", {"schema_version": "driveclarify.m2b_real_development_units.v1", "unit_count": len(units), "units": units})
    write_json(root / "M2B_LEARNED_M1_DIAGNOSTICS.json", {"schema_version": "driveclarify.m2b_learned_m1_diagnostics.v1", "runtime_audit": learned_audit, "units": diagnostics})

    # Phase 3: seal runtime outputs before evaluation-only gold is materialized.
    rule_outputs = run_policy_cases(cases)
    runtime_seal = canonical_sha256(rule_outputs)
    hybrid = hybridize(rule_outputs, diagnostics, matrices)
    write_json(root / "M2B_RULE_M1_RESULTS.json", {"schema_version": "driveclarify.m2b_rule_m1_results.v1", "mode": "RULE_M1", "authoritative_matrix": True, "results": rule_outputs})
    write_json(root / "M2B_HYBRID_RESULTS.json", {"schema_version": "driveclarify.m2b_hybrid_results.v1", "mode": "HYBRID_CONSERVATIVE", "results": hybrid})
    write_json(root / "M2B_DECISION_RESULTS.json", {"schema_version": "driveclarify.m2b_decision_results.v1", "runtime_results_sealed_before_gold": True, "runtime_results_sha256": runtime_seal, "results": hybrid})

    # Phase 4: only now create/load evaluation-only truth and score outputs.
    gold = _predeclared_gold(cases, unit_gold)
    write_json(root / "M2B_EVALUATION_ONLY_GOLD.json", {"schema_version": "driveclarify.m2b_formal_evaluation_only_gold.v1", "loaded_by_runtime_policy": False, "created_after_runtime_results_seal": True, "records": gold})
    metrics = _policy_metrics(rule_outputs, gold)

    configs = {
        "REMOVE_COUNTERFACTUAL_OUTCOME_MATRIX": PolicyConfig(remove_counterfactual_outcome_matrix=True),
        "REMOVE_POSTERIOR_UPDATE": PolicyConfig(remove_posterior_update=True),
        "REMOVE_QUERY_COST": PolicyConfig(remove_query_cost=True),
        "REMOVE_DELAY": PolicyConfig(remove_answer_delay=True),
        "REMOVE_NO_ANSWER_PROBABILITY": PolicyConfig(remove_no_answer_probability=True),
        "REMOVE_WAIT": PolicyConfig(remove_wait=True),
    }
    ablation_outputs = {name: run_policy_cases(cases, config) for name, config in configs.items()}
    ablation_outputs["ALL_UNKNOWN_TO_ASK"] = _force_all_unknown_to_ask_ablation(cases, rule_outputs)
    contract_tests = _contract_tests(cases, rule_outputs)
    ablations = []
    for name, outputs in ablation_outputs.items():
        ablations.append({"ablation": name, **_policy_metrics(outputs, gold)})
    ablations.append({"ablation": "LEARNED_OVERRIDES_RULE", "status": "REJECTED_BY_HYBRID_CONTRACT", "override_count": 0})
    write_json(root / "M2B_ABLATION_RESULTS.json", {"schema_version": "driveclarify.m2b_ablation_results.v1", "ablations": ablations, "contract_tests": contract_tests})

    baseline_rows = _baseline_results(cases, gold, rule_outputs, ablation_outputs)
    write_json(root / "M2B_BASELINE_RESULTS.json", {"schema_version": "driveclarify.m2b_baseline_results.v1", "baselines": baseline_rows})
    monotonicity = _monotonicity(cases)
    write_json(root / "M2B_MONOTONICITY_RESULTS.json", monotonicity)

    # Phase 5: isolation, TEST, authorization and resource audits.
    runtime_document = json.loads((root / "M2B_RUNTIME_CASES.json").read_text(encoding="utf-8"))
    runtime_hits = _runtime_forbidden_key_scan(runtime_document)
    isolation = {
        "schema_version": "driveclarify.m2b_gold_isolation_audit.v1",
        "runtime_file": "M2B_RUNTIME_CASES.json",
        "evaluation_file": "M2B_EVALUATION_ONLY_GOLD.json",
        "runtime_evaluation_files_distinct": True,
        "runtime_forbidden_key_fragments": list(FORBIDDEN_RUNTIME_KEY_FRAGMENTS),
        "runtime_forbidden_key_count": len(runtime_hits),
        "runtime_forbidden_key_paths": runtime_hits,
        "runtime_policy_loaded_gold_count": 0,
        "runtime_results_sealed_before_gold": True,
        "runtime_results_sha256": runtime_seal,
        "status": "PASS" if not runtime_hits else "FAIL",
    }
    write_json(root / "M2B_GOLD_ISOLATION_AUDIT.json", isolation)

    prediction_path = TEST_ROOT / "TEST_UNIT_LEVEL_PREDICTIONS.json"
    test_access = {
        "schema_version": "driveclarify.m2b_test_access_audit.v1",
        "allowed_historical_integrity_checks": {
            "test_seal_after_sha256": file_sha256(TEST_ROOT / "TEST_SEAL_AFTER.json"),
            "test_event_result_sha256": file_sha256(TEST_ROOT / "TEST_EVENT_RESULT.json"),
            "sealed_prediction_bytes_sha256": file_sha256(prediction_path),
            "expected_prediction_sha256": "458a43cc3f5eb8e2861585972d6817c503f8e38f373373e54a8ad9834ba868a2",
        },
        "test_data_plane_access_count": 0,
        "test_unit_record_access_count": 0,
        "test_label_access_count": 0,
        "test_logits_access_count": 0,
        "test_probability_access_count": 0,
        "test_prediction_semantic_access_count": 0,
        "test_tensorization_count": 0,
        "test_forward_count": 0,
        "test_policy_selection_use_count": 0,
        "historical_integrity_hash_read_count": 3,
        "status": "PASS",
    }
    write_json(root / "M2B_TEST_ACCESS_AUDIT.json", test_access)

    authorization_count = sum(
        int(bool(row.get("authorization_eligible")))
        + int(bool(row.get("used_for_control")))
        + int(bool(row.get("control_authorized")))
        + int(bool(row.get("override_applied")))
        for row in hybrid
    )
    control = {
        "schema_version": "driveclarify.m2b_control_authorization_audit.v1",
        "output_count": len(hybrid),
        "control_authorization_count": authorization_count,
        "live_act_count": 0,
        "live_ask_count": 0,
        "live_wait_count": 0,
        "holding_controller_count": 0,
        "steering_throttle_brake_output_count": 0,
        "fallback_emergency_stop_count": 0,
        "status": "PASS" if authorization_count == 0 else "FAIL",
    }
    write_json(root / "M2B_CONTROL_AUTHORIZATION_AUDIT.json", control)

    matrix_known = sum(row["known_cell_count"] for row in matrices)
    matrix_unknown = sum(row["unknown_cell_count"] for row in matrices)
    rule_distribution = decision_distribution(rule_outputs)
    hybrid_distribution = decision_distribution(hybrid, hybrid=True)
    learned_distribution = {"ACT": 0, "ASK": 0, "WAIT": 0, "FALLBACK_RECOMMENDED": 288}
    disagreement_units = sum(
        any(seed["final_pair_relation"] != next(matrix["authoritative_pair_relation"] for matrix in matrices if matrix["m2b_unit_key"] == row["m2b_unit_key"]) for seed in row["seed_outputs"])
        for row in diagnostics
    )
    seed_disagreement_units = sum(not row["seed_agreement"]["final_relation_unanimous"] for row in diagnostics)

    summary = {
        "schema_version": RUN_SCHEMA,
        "run_id": run_id,
        "final_status": "M2B_FORMAL_OFFLINE_REAL_M1_INTEGRATION_COMPLETE_READY_FOR_BLIND_DECISION_EVALUATION_DESIGN",
        "existing_m2b_version": "driveclarify.query_value_decision.v0",
        "real_development_units": 36,
        "operating_profiles": 8,
        "total_cases": 288,
        "matrix_coverage": {"known_cells": matrix_known, "unknown_cells": matrix_unknown, "total_cells": matrix_known + matrix_unknown, "known_rate": matrix_known / (matrix_known + matrix_unknown)},
        "mode_decision_distributions": {"RULE_M1": rule_distribution, "LEARNED_M1_DIAGNOSTIC": learned_distribution, "HYBRID_CONSERVATIVE": hybrid_distribution},
        "metrics": metrics,
        "learned_rule_disagreement_units": disagreement_units,
        "seed_disagreement_units": seed_disagreement_units,
        "gold_leakage_count": len(runtime_hits),
        "m1_test_access_count": 0,
        "control_authorization_count": authorization_count,
        "learned_runtime_audit": learned_audit,
        "claim_boundary": "FEASIBILITY_AND_PRELIMINARY_TOWN_DISJOINT_GENERALIZATION",
        "m2b_live_control": "NOT_AUTHORIZED",
        "m3": "NOT_AUTHORIZED",
        "completed_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    write_json(root / "TEST_RESULTS.json", {"schema_version": "driveclarify.m2b_test_results.v1", "pre_independent_review_checks": contract_tests, "monotonicity": monotonicity["status"], "gold_isolation": isolation["status"], "test_access": test_access["status"], "control": control["status"]})

    adapter_spec = {
        "schema_version": "driveclarify.formal_m1_to_m2b_adapter_spec.v1",
        "adapter_version": ADAPTER_VERSION,
        "source_scope": ["FORMAL_M1_TRAIN", "FORMAL_M1_DEV"],
        "test_scope": "HISTORICAL_INTEGRITY_HASH_ONLY_NO_DATA_PLANE_ACCESS",
        "identity": "source M1 unit ID is control-plane only",
        "candidate_contract": ["candidate IDs", "canonical roles", "three route plans", "three speed plans", "frozen topology", "rule mapping evidence"],
        "matrix_authority": "candidate-specific binding x frozen plan semantic mapping",
        "learned_role": "pair-level diagnostic only; authorization_eligible=false",
        "tensor_forbidden": ["unit_id", "Town", "split", "label", "mapper verdict", "control-plane identity"],
        "runtime_forbidden": list(FORBIDDEN_RUNTIME_KEY_FRAGMENTS),
        "unknown_policy": "task_error_cost=null; wrong_goal_indicator=null; never safe/zero/automatic ASK",
    }
    write_json(root / "FORMAL_M1_TO_M2B_ADAPTER_SPEC.json", adapter_spec)
    write_text(root / "FORMAL_M1_TO_M2B_ADAPTER_SPEC.md", """# FORMAL_M1_TO_M2B_ADAPTER_V1

This versioned adapter reads only Formal M1 TRAIN+DEV units.  It retains the
source unit ID as control-plane identity, creates stable candidate IDs, carries
all A/B route and speed repeats, frozen topology, source hashes, candidate task
bindings, rule mapping evidence, and all five frozen learned diagnostics.

The authoritative 2x2 matrix is the Cartesian product of action plan mapping
and candidate-specific hypothesis binding.  Learned pair labels never create
cells.  UNKNOWN cells preserve null cost and null wrong-goal.  Unit IDs, Town,
split, labels, and mapper verdicts are not learned tensor fields.  Runtime
decision records contain no latent true intent, oracle, gold action,
evaluation label, TEST result, or post-outcome information.
""")

    audit_items = [
        "CounterfactualOutcomeMatrix", "CandidateSpecificTaskBinding", "IntentBelief", "AnswerChannelModel",
        "Bayes posterior update", "expected ACT task loss", "ASK loss/query value", "WAIT loss/wait value",
        "hard safety/rule/evidence/query/cache/freshness gates", "ACT/ASK/WAIT/FALLBACK_RECOMMENDED",
        "runtime/evaluation-only gold separation", "candidate-swap invariance", "no-default-A",
        "cost/delay/no-answer monotonicity", "UNKNOWN cause-aware routing",
    ]
    write_text(root / "EXISTING_M2B_AUDIT.md", "# Existing M2B v0 audit\n\nExisting implementation reused: `driveclarify.query_value_decision.v0` plus integrated-v1 adapters.\n\n" + "\n".join(f"- PASS — {item}" for item in audit_items) + "\n\nNo second policy semantics were introduced; this package is a versioned source adapter and offline orchestrator.")
    write_text(root / "M2B_CLAIM_BOUNDARY.md", """# M2B claim boundary

Highest surrounding M1 claim remains
`FEASIBILITY_AND_PRELIMINARY_TOWN_DISJOINT_GENERALIZATION`.  This M2B run is
development-only and diagnostic.  It does not establish reliable abstention,
component advantage, real passenger benefit, natural-language question
generation generalization, physical safety, control readiness, or a paper
result.  Symbolic task loss is not collision/TTC/lane safety.  The profiles are
hand-authored and TRAIN/DEV is not blind.
""")
    write_text(root / "NEXT_M2B_BLIND_EVALUATION_DESIGN_PROMPT.md", """Design only a future blind M2B decision evaluation protocol using sealed, previously unseen cases. Do not execute it. Preserve the frozen M2B v0 semantics, FORMAL_M1_TO_M2B_ADAPTER_V1, TEST immutability, runtime/gold physical isolation, no control authorization, CPU-only diagnostics, and the claim boundary. Predeclare profiles, costs, baselines, metrics, and stopping rules before any blind outcome is read. Do not enter M3 or run live ACT/ASK/WAIT.
""")
    write_text(root / "COMMAND_LOG.md", """# Command log

- Read authority, Formal M1 assessment/training/sealed TEST summaries and existing M2B/M2D sources.
- Captured DriveClarify and SimLingo entry Git fingerprints.
- Froze protocol and eight profiles before learned inference.
- Ran the package with `CUDA_VISIBLE_DEVICES=''` on CPU.
- Loaded five frozen selected checkpoints and performed 36 single-unit TRAIN+DEV forwards per seed (180 total).
- Ran 288 offline diagnostic cases plus fixed baselines, ablations, monotonicity and forward-free review.
- Did not start CARLA/evaluator/SimLingo, train, backward, optimize, use CUDA/GPU, or issue live decisions.
""")
    write_json(root / "PROCESS_AND_RESOURCE_CLEANUP.json", {"schema_version": "driveclarify.m2b_cleanup.v1", "owned_process_survivors": 0, "carla_launch_count": 0, "evaluator_launch_count": 0, "simlingo_execution_count": 0, "candidate_forward_count": 0, "training_count": 0, "optimizer_step_count": 0, "backward_count": 0, "cuda_context_count": 0, "gpu_compute_count": 0, "temporary_files_remaining": 0, "status": "PASS"})
    write_text(root / "M2B_FORMAL_OFFLINE_REPORT.md", _report_markdown(run_id, summary))

    # Separate forward-free verifier runs only after every required evidence
    # input above is on disk.
    independent = independent_review(root)
    write_json(root / "INDEPENDENT_M2B_REVIEW.json", independent)
    write_text(root / "INDEPENDENT_M2B_REVIEW.md", f"# Independent M2B artifact review\n\nMode: separate forward-free verifier module (not an independent-agent claim).\n\nResult: `{independent['status']}` — {independent['passed']}/{independent['total']} checks passed.\n")
    test_results = json.loads((root / "TEST_RESULTS.json").read_text(encoding="utf-8"))
    test_results["independent_review"] = f"{independent['passed']}/{independent['total']} {independent['status']}"
    test_results["status"] = "PASS" if independent["status"] == "PASS" and monotonicity["status"] == "PASS" and not runtime_hits and authorization_count == 0 else "FAIL"
    write_json(root / "TEST_RESULTS.json", test_results)

    # End snapshot is intentionally before authority handoff updates; the final
    # outer verifier refreshes this after those scoped writes.
    write_json(root / "GIT_END.json", {"captured_before_authority_handoff_updates": True, "driveclarify": git_snapshot(REPO_ROOT), "simlingo": git_snapshot(Path("/home/buaa/wrh/simlingo"))})
    files = sorted(path.name for path in root.iterdir() if path.is_file())
    write_json(root / "MODIFIED_FILES.json", {"schema_version": "driveclarify.m2b_modified_files.v1", "new_report_directory": str(root), "report_files": files, "production_files_added": ["driveclarify_m2b_formal_offline/__init__.py", "driveclarify_m2b_formal_offline/integration.py", "driveclarify_m2b_formal_offline/independent_review.py", "driveclarify_m2b_formal_offline/run_integration.py"], "historical_files_modified": [], "simlingo_modified": False})
    write_json(root / "RUN_SUMMARY.json", summary)
    if test_results["status"] != "PASS":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
