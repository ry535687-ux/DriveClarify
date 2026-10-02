"""One-shot, non-disclosing overlap check against frozen development fixtures.

The checker may read old fixtures only after authoring is complete.  It emits
counts and input hashes, never old records, strings, IDs, costs, or matrices.
It is intentionally disconnected from the challenge generator.
"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from .challenge_contracts import canonical_json, file_sha256, load_json, write_json


REPORT_DIR = Path("reports/m2c_blind_integrated_challenge_v0")
OUTPUT_PATH = REPORT_DIR / "OVERLAP_RESULTS.json"
OLD_JSON_PATHS = (
    Path("tests/query_value_decision_v0/fixtures/runtime_decision_contexts.json"),
    Path("tests/query_value_decision_v0/fixtures/evaluation_only.json"),
    Path("tests/language_interaction_v0/fixtures/runtime_episodes.json"),
    Path("tests/language_interaction_v0/fixtures/evaluation_only.json"),
)
OLD_M1_SOURCE = Path("tests/task_conditioned_pairwise/test_method_v0.py")


def _walk(value: Any) -> Iterable[Any]:
    yield value
    if isinstance(value, Mapping):
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def _records(document: Any) -> list[dict[str, Any]]:
    if isinstance(document, list):
        return [item for item in document if isinstance(item, dict)]
    if not isinstance(document, dict):
        return []
    preferred = ("cases", "episodes", "contexts", "records", "fixtures")
    for key in preferred:
        value = document.get(key)
        if isinstance(value, list) and all(isinstance(item, dict) for item in value):
            return list(value)
    candidates = [
        value for value in document.values()
        if isinstance(value, list) and value and all(isinstance(item, dict) for item in value)
    ]
    return list(max(candidates, key=len)) if candidates else []


def _values_for_key(value: Any, keys: set[str]) -> list[Any]:
    result = []
    for item in _walk(value):
        if isinstance(item, Mapping):
            for key, child in item.items():
                if str(key) in keys:
                    result.append(child)
    return result


def _first_scalar(value: Any, keys: set[str]) -> Any:
    values = _values_for_key(value, keys)
    for item in values:
        if isinstance(item, (str, int, float, bool)) or item is None:
            return item
    return None


def _task_cost_sequence(record: Mapping[str, Any]) -> tuple[Any, ...]:
    cells = _values_for_key(record, {"cells"})
    for value in cells:
        if isinstance(value, list) and all(isinstance(item, Mapping) for item in value):
            return tuple(item.get("task_error_cost") for item in value)
    return ()


def _cost_tuple(record: Mapping[str, Any]) -> tuple[Any, ...] | None:
    query_cost = _first_scalar(record, {"query_cost"})
    if query_cost is None:
        return None
    return (
        query_cost,
        _first_scalar(record, {"delay_cost_per_second"}),
        _first_scalar(record, {"no_answer_penalty"}),
        _first_scalar(record, {"wait_cost"}),
        _first_scalar(record, {"missed_opportunity_cost"}),
        _task_cost_sequence(record),
    )


def _channel_tuple(record: Mapping[str, Any]) -> str | None:
    channels = _values_for_key(record, {"question_channel_contract", "answer_channel"})
    if not channels:
        return None
    channel = channels[0]
    if not isinstance(channel, Mapping):
        return None
    compact = {
        "resolution": _first_scalar(channel, {"answer_resolution_probability"}),
        "no_answer": _first_scalar(channel, {"no_answer_probability"}),
        "confusion": _values_for_key(channel, {"answer_confusion_matrix"}),
        "delay": _values_for_key(channel, {"delay_distribution"}),
    }
    return canonical_json(compact)


def _matrix_tuple(record: Mapping[str, Any]) -> str | None:
    matrices = _values_for_key(record, {"counterfactual_runtime_inputs", "counterfactual_outcome_matrix"})
    if not matrices:
        return None
    matrix = matrices[0]
    if not isinstance(matrix, Mapping):
        return None
    cells = matrix.get("cells")
    if not isinstance(cells, list):
        return None
    compact = [
        (
            cell.get("action_candidate_id"),
            cell.get("hypothesis_candidate_id"),
            cell.get("task_outcome"),
            cell.get("task_error_cost"),
            cell.get("wrong_goal_indicator"),
        )
        for cell in cells
        if isinstance(cell, Mapping)
    ]
    return canonical_json(compact)


def _semantic_tuple(record: Mapping[str, Any]) -> str:
    compact = {
        "ambiguity_type": _first_scalar(record, {"ambiguity_type"}),
        "unresolved_slots": _values_for_key(record, {"unresolved_slots"})[:1],
        "pair_relation": _first_scalar(record, {"pair_relation", "candidate_pair_relation"}),
        "unknown_cause": _first_scalar(record, {"unknown_cause"}),
        "question_status": _first_scalar(record, {"question_status", "proposal_status"}),
        "active_query_status": _first_scalar(record, {"active_query_state", "active_query_id"}),
        "wait_mode": _first_scalar(record, {"wait_mode"}),
        "candidate_count": _first_scalar(record, {"candidate_count"}),
    }
    return canonical_json(compact)


def _intersection_count(left: Iterable[Any], right: Iterable[Any]) -> int:
    return len(set(left).intersection(right))


def run_once() -> dict[str, Any]:
    if OUTPUT_PATH.exists():
        raise RuntimeError("OVERLAP_CHECK_ALREADY_COMPLETED_REFUSING_RERUN")

    challenge = load_json(REPORT_DIR / "runtime_challenge.json")
    new_records = challenge["cases"]
    old_documents = [load_json(path) for path in OLD_JSON_PATHS]
    old_records = [record for document in old_documents for record in _records(document)]

    m1_source = OLD_M1_SOURCE.read_text(encoding="utf-8")
    m1_tree = ast.parse(m1_source, filename=str(OLD_M1_SOURCE))
    m1_string_literals = {
        node.value for node in ast.walk(m1_tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }

    new_full = [canonical_json(record) for record in new_records]
    old_full = [canonical_json(record) for record in old_records]
    new_instructions = [record["raw_instruction"] for record in new_records]
    old_instructions = [
        value for document in old_documents
        for value in _values_for_key(document, {"raw_instruction"})
        if isinstance(value, str)
    ] + [value for value in m1_string_literals if isinstance(value, str)]
    new_costs = [value for record in new_records if (value := _cost_tuple(record)) is not None]
    old_costs = [value for record in old_records if (value := _cost_tuple(record)) is not None]
    new_channels = [value for record in new_records if (value := _channel_tuple(record)) is not None]
    old_channels = [value for record in old_records if (value := _channel_tuple(record)) is not None]
    new_matrices = [value for record in new_records if (value := _matrix_tuple(record)) is not None]
    old_matrices = [value for record in old_records if (value := _matrix_tuple(record)) is not None]
    new_semantics = [_semantic_tuple(record) for record in new_records]
    old_semantics = [_semantic_tuple(record) for record in old_records]

    old_ids = {
        value for document in old_documents
        for value in _values_for_key(document, {"case_id", "challenge_id", "decision_id", "episode_id"})
        if isinstance(value, str)
    }
    old_ids.update(value for value in m1_string_literals if re.fullmatch(r"[A-Za-z]+\d+[A-Za-z0-9_-]*", value))
    new_ids = {record["challenge_id"] for record in new_records}

    counts = {
        "exact_full_case_overlap": _intersection_count(new_full, old_full),
        "raw_instruction_exact_overlap": _intersection_count(new_instructions, old_instructions),
        "full_cost_tuple_overlap": _intersection_count(new_costs, old_costs),
        "full_answer_channel_tuple_overlap": _intersection_count(new_channels, old_channels),
        "full_counterfactual_matrix_overlap": _intersection_count(new_matrices, old_matrices),
        "normalized_semantic_combination_overlap": _intersection_count(new_semantics, old_semantics),
        "old_case_id_overlap": len(new_ids.intersection(old_ids)),
    }
    result = {
        "schema_version": "driveclarify.m2c_blind_overlap_check.v0",
        "disclosure_policy": "COUNTS_AND_HASHES_ONLY",
        "checker_isolated_from_authoring_generator": True,
        "challenge_modified_after_check": False,
        "old_input_hashes": {str(path): file_sha256(path) for path in (*OLD_JSON_PATHS, OLD_M1_SOURCE)},
        "old_document_aggregate_sha256": hashlib.sha256(
            "".join(file_sha256(path) for path in (*OLD_JSON_PATHS, OLD_M1_SOURCE)).encode("ascii")
        ).hexdigest(),
        "new_runtime_sha256": file_sha256(REPORT_DIR / "runtime_challenge.json"),
        "counts": counts,
        "required_zero_checks_pass": counts["exact_full_case_overlap"] == 0 and counts["old_case_id_overlap"] == 0,
        "verdict": "PASS_NO_EXACT_OR_ID_OVERLAP" if counts["exact_full_case_overlap"] == 0 and counts["old_case_id_overlap"] == 0 else "BLOCKED_CHALLENGE_NOT_INDEPENDENT",
    }
    write_json(OUTPUT_PATH, result)
    return result


def main() -> int:
    result = run_once()
    print(
        "OVERLAP_CHECK "
        + " ".join(f"{key}={value}" for key, value in sorted(result["counts"].items()))
        + f" verdict={result['verdict']} aggregate_sha256={result['old_document_aggregate_sha256']}"
    )
    return 0 if result["required_zero_checks_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

