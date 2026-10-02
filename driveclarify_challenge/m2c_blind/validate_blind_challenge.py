"""Static, non-policy validation for the authored M2C blind challenge."""

from __future__ import annotations

import ast
import copy
import json
import math
from pathlib import Path
import sys
from typing import Any, Mapping

from .author_blind_challenge import build_challenge
from .challenge_contracts import (
    CHALLENGE_SEED_SHA256,
    SCHEMA_VERSION,
    assert_runtime_has_no_gold_leakage,
    canonical_json,
    file_sha256,
    load_json,
    stable_sha256,
    validate_hidden_case,
    validate_runtime_case,
)
from .independent_gold_oracle import evaluate_case


REPORT_DIR = Path("reports/m2c_blind_integrated_challenge_v0")
RUNTIME_PATH = REPORT_DIR / "runtime_challenge.json"
HIDDEN_PATH = REPORT_DIR / "evaluation_only_hidden.json"
COVERAGE_PATH = REPORT_DIR / "COVERAGE_MATRIX.json"

FORBIDDEN_AUTHORING_REFERENCES = (
    "driveclarify_decision.query_value_policy",
    "driveclarify_decision.offline_decision_evaluation",
    "driveclarify_decision.decision_cli",
    "tests/query_value_decision_v0/fixtures/runtime_decision_contexts.json",
    "tests/query_value_decision_v0/fixtures/evaluation_only.json",
    "reports/query_value_decision_method_v0/OFFLINE_POLICY_RESULTS.json",
    "reports/query_value_decision_method_v0/BASELINE_ABLATION_RESULTS.json",
    "tests/language_interaction_v0/fixtures/runtime_episodes.json",
    "tests/language_interaction_v0/fixtures/evaluation_only.json",
)


def _case_maps() -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    runtime_doc = load_json(RUNTIME_PATH)
    hidden_doc = load_json(HIDDEN_PATH)
    runtime = {case["challenge_id"]: case for case in runtime_doc["cases"]}
    hidden = {case["challenge_id"]: case for case in hidden_doc["cases"]}
    if len(runtime) != len(runtime_doc["cases"]) or len(hidden) != len(hidden_doc["cases"]):
        raise AssertionError("DUPLICATE_CHALLENGE_ID")
    if runtime.keys() != hidden.keys():
        raise AssertionError("RUNTIME_HIDDEN_CASE_SET_MISMATCH")
    return runtime, hidden


def _base_id(companion: Mapping[str, Any]) -> str:
    return str(companion["metamorphic_link"]["source_challenge_id"])


def _mapped(candidate_id: str | None) -> str | None:
    return {"CAND_A": "CAND_B", "CAND_B": "CAND_A"}.get(candidate_id, candidate_id)


def _strip_candidate_identity(record: Mapping[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(dict(record))
    value.pop("candidate_id", None)
    value.pop("source_candidate_id", None)
    return value


def _assert_swap(base: Mapping[str, Any], swap: Mapping[str, Any], base_gold: Mapping[str, Any], swap_gold: Mapping[str, Any]) -> None:
    if base["group_id"] != swap["group_id"]:
        raise AssertionError("SWAP_GROUP_MISMATCH")
    for key in ("candidate_plan_records", "candidate_specific_task_bindings", "grounding_evidence"):
        base_by_id = {item["candidate_id"]: item for item in base[key]}
        swap_by_id = {item["candidate_id"]: item for item in swap[key]}
        for candidate_id in ("CAND_A", "CAND_B"):
            if _strip_candidate_identity(base_by_id[candidate_id]) != _strip_candidate_identity(
                swap_by_id[_mapped(candidate_id)]
            ):
                raise AssertionError(f"SWAP_RECORD_TRANSFORM:{swap['challenge_id']}:{key}")

    base_belief = {item["candidate_id"]: item["probability"] for item in base["intent_belief"]["candidate_probabilities"]}
    swap_belief = {item["candidate_id"]: item["probability"] for item in swap["intent_belief"]["candidate_probabilities"]}
    for candidate_id in ("CAND_A", "CAND_B"):
        if not math.isclose(base_belief[candidate_id], swap_belief[_mapped(candidate_id)], abs_tol=1e-12):
            raise AssertionError("SWAP_BELIEF_TRANSFORM")

    base_cells = {
        (cell["action_candidate_id"], cell["hypothesis_candidate_id"]): cell
        for cell in base["counterfactual_runtime_inputs"]["cells"]
    }
    swap_cells = {
        (cell["action_candidate_id"], cell["hypothesis_candidate_id"]): cell
        for cell in swap["counterfactual_runtime_inputs"]["cells"]
    }
    for (action_id, hypothesis_id), base_cell in base_cells.items():
        swap_cell = swap_cells[(_mapped(action_id), _mapped(hypothesis_id))]
        for field in ("task_outcome", "evidence_status", "task_error_cost", "wrong_goal_indicator"):
            if base_cell[field] != swap_cell[field]:
                raise AssertionError(f"SWAP_MATRIX_TRANSFORM:{swap['challenge_id']}:{field}")

    base_channel = base["question_channel_contract"]
    swap_channel = swap["question_channel_contract"]
    base_rows = {
        row["hypothesis_candidate_id"]: {
            item["answer_label"]: item["probability"] for item in row["answer_probabilities"]
        }
        for row in base_channel["answer_confusion_matrix"]
    }
    swap_rows = {
        row["hypothesis_candidate_id"]: {
            item["answer_label"]: item["probability"] for item in row["answer_probabilities"]
        }
        for row in swap_channel["answer_confusion_matrix"]
    }
    label_map = {"OPTION_ONE": "OPTION_TWO", "OPTION_TWO": "OPTION_ONE"}
    for candidate_id in ("CAND_A", "CAND_B"):
        for label in ("OPTION_ONE", "OPTION_TWO"):
            if base_rows[candidate_id][label] != swap_rows[_mapped(candidate_id)][label_map[label]]:
                raise AssertionError("SWAP_ANSWER_CHANNEL_TRANSFORM")
    if base_channel["question_status"] == "QUESTION_PROPOSAL":
        if swap_channel["option_descriptions"] != list(reversed(base_channel["option_descriptions"])):
            raise AssertionError("SWAP_OPTION_DESCRIPTION_ORDER")
        expected_partition = [
            {"answer_label": "OPTION_ONE", "candidate_ids": ["CAND_A"]},
            {"answer_label": "OPTION_TWO", "candidate_ids": ["CAND_B"]},
        ]
        if swap_channel["candidate_partition"] != expected_partition:
            raise AssertionError("SWAP_QUESTION_PARTITION")

    if base_gold["expected_decision"] != swap_gold["expected_decision"]:
        raise AssertionError("SWAP_DECISION_NOT_INVARIANT")
    if base_gold["expected_act_target_type"] != swap_gold["expected_act_target_type"]:
        raise AssertionError("SWAP_ACT_TARGET_TYPE")
    selected = base_gold["expected_selected_candidate_id"]
    if swap_gold["expected_selected_candidate_id"] != _mapped(selected):
        raise AssertionError("SWAP_SELECTED_ID_NOT_EXCHANGED")
    if swap_gold["latent_true_intent"] != _mapped(base_gold["latent_true_intent"]):
        raise AssertionError("SWAP_LATENT_INTENT_NOT_EXCHANGED")
    for field in ("oracle_expected_loss", "realized_task_loss"):
        left, right = base_gold[field], swap_gold[field]
        if left is None or right is None:
            if left != right:
                raise AssertionError(f"SWAP_{field.upper()}")
        elif not math.isclose(float(left), float(right), abs_tol=1e-12):
            raise AssertionError(f"SWAP_{field.upper()}")


def _diff_paths(left: Any, right: Any, path: str = "$") -> set[str]:
    if type(left) is not type(right):
        return {path}
    if isinstance(left, dict):
        result: set[str] = set()
        for key in left.keys() | right.keys():
            if key not in left or key not in right:
                result.add(f"{path}.{key}")
            else:
                result.update(_diff_paths(left[key], right[key], f"{path}.{key}"))
        return result
    if isinstance(left, list):
        if len(left) != len(right):
            return {path}
        result: set[str] = set()
        for index, (a, b) in enumerate(zip(left, right)):
            result.update(_diff_paths(a, b, f"{path}[{index}]"))
        return result
    return set() if left == right else {path}


def _semantic_projection(case: Mapping[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(dict(case))
    for key in ("challenge_id", "case_type", "tags", "metamorphic_link", "provenance"):
        value.pop(key, None)
    return value


def _assert_monotonic(base: Mapping[str, Any], companion: Mapping[str, Any], base_gold: Mapping[str, Any], companion_gold: Mapping[str, Any]) -> None:
    factor = companion["metamorphic_link"]["changed_factor"]
    expected_paths = {
        "query_cost": {"$.declared_cost_parameters.query_cost"},
        "answer_delay": {"$.question_channel_contract.delay_distribution[0].seconds"},
        "no_answer_probability": {"$.question_channel_contract.no_answer_probability"},
        "answer_informativeness": {
            "$.question_channel_contract.answer_confusion_matrix[0].answer_probabilities[0].probability",
            "$.question_channel_contract.answer_confusion_matrix[0].answer_probabilities[1].probability",
            "$.question_channel_contract.answer_confusion_matrix[1].answer_probabilities[0].probability",
            "$.question_channel_contract.answer_confusion_matrix[1].answer_probabilities[1].probability",
        },
        "deadline": {"$.answer_deadline_monotonic"},
    }[factor]
    actual = _diff_paths(_semantic_projection(base), _semantic_projection(companion))
    if actual != expected_paths:
        raise AssertionError(f"MONOTONIC_NOT_SINGLE_FACTOR:{companion['challenge_id']}:{sorted(actual)}")
    if base["group_id"] != companion["group_id"]:
        raise AssertionError("MONOTONIC_GROUP_MISMATCH")
    if companion_gold.get("expected_monotonic_relation") != companion_gold["metamorphic_relation"]:
        raise AssertionError("MONOTONIC_RELATION_NOT_FROZEN")
    base_oracle = evaluate_case(base)
    companion_oracle = evaluate_case(companion)
    base_best = min(value for _, value in base_oracle.act_losses)
    base_ask_value = base_best - base_oracle.ask_loss
    companion_ask_value = -math.inf
    if companion_oracle.ask_loss is not None:
        companion_best = min(value for _, value in companion_oracle.act_losses)
        companion_ask_value = companion_best - companion_oracle.ask_loss
    if companion_ask_value > base_ask_value + 1e-12:
        raise AssertionError("MONOTONIC_ASK_VALUE_INCREASED")
    if base_gold["expected_decision"] != "ASK":
        raise AssertionError("MONOTONIC_BASE_NOT_ASK")


def _assert_source_isolation() -> None:
    author_path = Path("driveclarify_challenge/m2c_blind/author_blind_challenge.py")
    oracle_path = Path("driveclarify_challenge/m2c_blind/independent_gold_oracle.py")
    author_text = author_path.read_text(encoding="utf-8")
    oracle_text = oracle_path.read_text(encoding="utf-8")
    for forbidden in FORBIDDEN_AUTHORING_REFERENCES:
        if forbidden in author_text or forbidden in oracle_text:
            raise AssertionError(f"FORBIDDEN_AUTHORING_REFERENCE:{forbidden}")
    for path in (author_path, oracle_path):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            module = None
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(("driveclarify_decision", "driveclarify_language", "driveclarify_consequence", "torch")):
                        raise AssertionError(f"FORBIDDEN_IMPORT:{path}:{alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module.startswith(("driveclarify_decision", "driveclarify_language", "driveclarify_consequence", "torch")):
                    raise AssertionError(f"FORBIDDEN_IMPORT:{path}:{module}")

    hidden_literal = "evaluation_only_hidden.json"
    for root in (Path("driveclarify_consequence"), Path("driveclarify_language"), Path("driveclarify_decision")):
        for path in root.rglob("*.py"):
            if hidden_literal in path.read_text(encoding="utf-8"):
                raise AssertionError(f"RUNTIME_IMPORTS_M2C_HIDDEN:{path}")


def validate_all(*, verify_seal: bool = False) -> dict[str, Any]:
    runtime_doc = load_json(RUNTIME_PATH)
    hidden_doc = load_json(HIDDEN_PATH)
    coverage = load_json(COVERAGE_PATH)
    if runtime_doc["schema_version"] != SCHEMA_VERSION or hidden_doc["schema_version"] != SCHEMA_VERSION:
        raise AssertionError("DOCUMENT_SCHEMA_VERSION")
    if runtime_doc["challenge_seed_sha256"] != CHALLENGE_SEED_SHA256:
        raise AssertionError("CHALLENGE_SEED_MISMATCH")
    assert_runtime_has_no_gold_leakage(runtime_doc)
    runtime, hidden = _case_maps()
    for challenge_id, case in runtime.items():
        validate_runtime_case(case)
        validate_hidden_case(hidden[challenge_id], case)
        oracle = evaluate_case(case)
        gold = hidden[challenge_id]
        if (
            oracle.runtime_action != gold["oracle_best_runtime_action"]
            or oracle.decision != gold["expected_decision"]
            or oracle.act_target_type != gold["expected_act_target_type"]
            or oracle.selected_candidate_id != gold["expected_selected_candidate_id"]
            or oracle.wait_mode != gold["expected_wait_mode"]
        ):
            raise AssertionError(f"ORACLE_GOLD_MISMATCH:{challenge_id}")
        if gold["expected_decision"] == "WAIT":
            wait = case["wait_opportunity"]
            if not wait["future_information_expected"] or wait["information_resolution_probability"] <= 0.0:
                raise AssertionError(f"WAIT_WITHOUT_FUTURE_INFORMATION:{challenge_id}")
        cause = case["ambiguity_metadata_without_gold"]["unknown_cause_class"]
        if cause == "PASSENGER_UNRESOLVABLE" and gold["expected_decision"] == "ASK":
            raise AssertionError(f"NONPASSENGER_UNKNOWN_ASK:{challenge_id}")
        if gold["expected_act_target_type"] == "EQUIVALENCE_CLASS" and gold["expected_selected_candidate_id"] is not None:
            raise AssertionError(f"DEFAULT_A_EQUIVALENCE:{challenge_id}")

    case_type_counts = {
        kind: sum(case["case_type"] == kind for case in runtime.values())
        for kind in ("BASE", "SWAP_COMPANION", "MONOTONIC_COMPANION")
    }
    if len(runtime) < 112 or case_type_counts != {"BASE": 72, "SWAP_COMPANION": 24, "MONOTONIC_COMPANION": 16}:
        raise AssertionError(f"CASE_COUNTS:{case_type_counts}")
    if len({case["group_id"] for case in runtime.values()}) != 72:
        raise AssertionError("GROUP_COUNT")

    def count_tag(tag: str, *, base_only: bool = False) -> int:
        return sum(tag in case["tags"] and (not base_only or case["case_type"] == "BASE") for case in runtime.values())

    thresholds = {
        "consequence_over_ambiguity": (16, True),
        "asymmetric_wrong_goal": (16, True),
        "strong_asymmetric_loss": (12, True),
        "query_cost_delay_answer_quality": (16, True),
        "wait_stress": (20, False),
        "unknown_passenger_resolvable": (10, False),
        "unknown_passenger_unresolvable": (10, False),
        "language_compositional_holdout": (20, False),
        "out_of_domain_fail_closed": (12, False),
        "answer_interaction_lifecycle": (16, False),
        "hard_gate_boundary": (16, False),
        "candidate_swap_metamorphic": (24, False),
        "cost_channel_monotonic_companion": (16, False),
    }
    for tag, (minimum, base_only) in thresholds.items():
        if count_tag(tag, base_only=base_only) < minimum:
            raise AssertionError(f"COVERAGE_MINIMUM:{tag}")

    for case in runtime.values():
        if case["case_type"] == "SWAP_COMPANION":
            base_id = _base_id(case)
            _assert_swap(runtime[base_id], case, hidden[base_id], hidden[case["challenge_id"]])
        elif case["case_type"] == "MONOTONIC_COMPANION":
            base_id = _base_id(case)
            _assert_monotonic(runtime[base_id], case, hidden[base_id], hidden[case["challenge_id"]])

    generated_first = build_challenge()
    generated_second = build_challenge()
    expected_documents = (runtime_doc, hidden_doc, coverage, load_json(REPORT_DIR / "AUTHORING_PROVENANCE.json"))
    if tuple(stable_sha256(item) for item in generated_first) != tuple(stable_sha256(item) for item in generated_second):
        raise AssertionError("SAME_SEED_REGENERATION_MISMATCH")
    if tuple(stable_sha256(item) for item in generated_first) != tuple(stable_sha256(item) for item in expected_documents):
        raise AssertionError("ON_DISK_GENERATION_MISMATCH")

    _assert_source_isolation()
    if "torch" in sys.modules:
        raise AssertionError("TORCH_LOADED_DURING_AUTHORING_VALIDATION")

    if verify_seal:
        seal = load_json(REPORT_DIR / "SEAL.json")
        expected_hashes = {
            "runtime_challenge_sha256": RUNTIME_PATH,
            "hidden_gold_sha256": HIDDEN_PATH,
            "challenge_spec_sha256": REPORT_DIR / "CHALLENGE_SPEC.md",
            "predeclared_metrics_sha256": REPORT_DIR / "PREDECLARED_METRICS.json",
            "coverage_matrix_sha256": COVERAGE_PATH,
        }
        for field, path in expected_hashes.items():
            if seal[field] != file_sha256(path):
                raise AssertionError(f"SEALED_HASH_MISMATCH:{field}")
        if seal["seal_status"] != "SEALED" or seal["post_seal_mutation_allowed"] is not False:
            raise AssertionError("SEAL_STATUS_INVALID")

    return {
        "schema_validation": "PASS",
        "runtime_gold_leakage": "PASS",
        "oracle_consistency": "PASS",
        "swap_validation": "PASS",
        "monotonic_validation": "PASS",
        "coverage_validation": "PASS",
        "deterministic_regeneration": "PASS",
        "source_isolation": "PASS",
        "torch_loaded": False,
        "case_count": len(runtime),
        "case_type_counts": case_type_counts,
        "group_count": len({case["group_id"] for case in runtime.values()}),
        "runtime_sha256": file_sha256(RUNTIME_PATH),
        "hidden_sha256": file_sha256(HIDDEN_PATH),
    }


def main() -> int:
    result = validate_all(verify_seal=(REPORT_DIR / "SEAL.json").exists())
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
