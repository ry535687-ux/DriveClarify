"""Contracts and static validation for the sealed M2C blind challenge.

Only Python's standard library is used.  This module does not import any M1,
M2A, or M2B inference component.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping


SCHEMA_VERSION = "driveclarify.m2c_blind_integrated_challenge.v0"
CHALLENGE_NAME = "DriveClarify Method M2C-A Blind Offline Integrated Challenge v0"

M1_ARTIFACT_INVENTORY_SHA256 = (
    "7bb83bb80534938b839cc5d2cf84c0b3d59d9e22b1cdf8e9f6c282e1065b749f"
)
M2A_ARTIFACT_INVENTORY_SHA256 = (
    "84cd256b3056da05e25ce52b968676b3e67640c2afd5a865363bd536b8d6ff98"
)
M2B_QUERY_VALUE_POLICY_SHA256 = (
    "15ea1c881865b67f5cdf81333ce5c05d8419cd899dd4835a3efe26daa549fb0e"
)
REAL_S1_SHA256 = "2e580c4b182eb211bc866ceb41c09d594fa43b0915d83a83160704512da75a82"

SEED_SOURCE = (
    "DRIVECLARIFY_M2C_V0"
    + M1_ARTIFACT_INVENTORY_SHA256
    + M2A_ARTIFACT_INVENTORY_SHA256
    + M2B_QUERY_VALUE_POLICY_SHA256
    + REAL_S1_SHA256
)
CHALLENGE_SEED_SHA256 = hashlib.sha256(SEED_SOURCE.encode("utf-8")).hexdigest()

RUNTIME_REQUIRED_FIELDS = frozenset(
    {
        "challenge_id",
        "group_id",
        "case_type",
        "tags",
        "raw_instruction",
        "symbolic_scene_table",
        "allowed_task_vocabulary",
        "ambiguity_metadata_without_gold",
        "candidate_input_contract",
        "candidate_plan_records",
        "candidate_specific_task_bindings",
        "grounding_evidence",
        "intent_belief",
        "question_channel_contract",
        "query_budget",
        "active_query_state",
        "monotonic_now",
        "answer_deadline_monotonic",
        "wait_opportunity",
        "hard_gate_envelope",
        "counterfactual_runtime_inputs",
        "declared_cost_parameters",
        "provenance",
        "schema_version",
    }
)

HIDDEN_REQUIRED_FIELDS = frozenset(
    {
        "challenge_id",
        "group_id",
        "latent_true_intent",
        "expected_language_status",
        "expected_ambiguity_type",
        "expected_unresolved_slots",
        "expected_candidate_set",
        "expected_question_status",
        "expected_answer_status",
        "expected_resolved_candidate_id",
        "answer_arrival_status",
        "replan_required",
        "expected_pair_relation",
        "expected_decision",
        "expected_act_target_type",
        "expected_selected_candidate_id",
        "expected_wait_mode",
        "expected_unknown_routing",
        "realized_answer_event",
        "realized_task_loss",
        "oracle_best_runtime_action",
        "oracle_expected_loss",
        "gold_reason_family",
        "provenance",
        "schema_version",
    }
)

FORBIDDEN_RUNTIME_KEYS = frozenset(
    {
        "true_intent",
        "latent_true_intent",
        "gold_intent",
        "gold_candidate_id",
        "gold_answer",
        "gold_decision",
        "expected_decision",
        "necessary_query",
        "oracle_selected_candidate",
        "oracle_action",
        "oracle_loss",
        "expected_selected_candidate",
        "expected_answer_status",
        "paper_label",
    }
)

FORBIDDEN_CONTROL_KEYS = frozenset(
    {
        "steering",
        "steer",
        "throttle",
        "brake",
        "vehicle_control",
        "control_command",
    }
)

FORBIDDEN_RUNTIME_PHRASES = (
    "正确选项",
    "应该 ask",
    "应该选择 a",
    "应该选择 b",
    "correct option",
    "should ask",
    "should choose",
)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stable_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _walk(value: Any, path: str = "$") -> Iterable[tuple[str, Any]]:
    yield path, value
    if isinstance(value, Mapping):
        for key, item in value.items():
            yield from _walk(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk(item, f"{path}[{index}]")


def _require_finite_nonnegative(value: Any, path: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"NON_NUMERIC_COST:{path}")
    if not math.isfinite(float(value)) or float(value) < 0.0:
        raise ValueError(f"INVALID_COST:{path}")


def _validate_probability(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"NON_NUMERIC_PROBABILITY:{path}")
    result = float(value)
    if not math.isfinite(result) or result < 0.0 or result > 1.0:
        raise ValueError(f"INVALID_PROBABILITY:{path}")
    return result


def assert_runtime_has_no_gold_leakage(value: Any) -> None:
    """Reject evaluation labels, aliases, control fields, and textual hints."""

    for path, item in _walk(value):
        if isinstance(item, Mapping):
            for key in item:
                normalized = str(key).lower()
                if normalized == "ambiguity_metadata_without_gold":
                    continue
                if normalized in FORBIDDEN_RUNTIME_KEYS:
                    raise ValueError(f"FORBIDDEN_RUNTIME_KEY:{path}.{key}")
                if normalized in FORBIDDEN_CONTROL_KEYS:
                    raise ValueError(f"FORBIDDEN_CONTROL_KEY:{path}.{key}")
                if (
                    normalized.startswith(("gold_", "oracle_"))
                    or normalized.endswith(("_gold", "_oracle"))
                    or "latent_true" in normalized
                ):
                    raise ValueError(f"RENAMED_RUNTIME_GOLD_KEY:{path}.{key}")
        elif isinstance(item, str):
            lowered = item.lower()
            for phrase in FORBIDDEN_RUNTIME_PHRASES:
                if phrase in lowered:
                    raise ValueError(f"NATURAL_LANGUAGE_GOLD_LEAK:{path}")


def validate_runtime_case(case: Mapping[str, Any]) -> None:
    missing = RUNTIME_REQUIRED_FIELDS.difference(case)
    if missing:
        raise ValueError(f"RUNTIME_FIELDS_MISSING:{','.join(sorted(missing))}")
    assert_runtime_has_no_gold_leakage(case)

    challenge_id = str(case["challenge_id"])
    if not challenge_id.startswith("M2C-"):
        raise ValueError(f"INVALID_CHALLENGE_ID:{challenge_id}")
    if case["schema_version"] != SCHEMA_VERSION:
        raise ValueError(f"RUNTIME_SCHEMA_VERSION:{challenge_id}")
    if case["case_type"] not in {"BASE", "SWAP_COMPANION", "MONOTONIC_COMPANION"}:
        raise ValueError(f"INVALID_CASE_TYPE:{challenge_id}")

    records = case["candidate_plan_records"]
    ids = [record["candidate_id"] for record in records]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError(f"CANDIDATE_IDS_NOT_UNIQUE:{challenge_id}")

    belief = case["intent_belief"]
    belief_rows = belief["candidate_probabilities"]
    belief_ids = [row["candidate_id"] for row in belief_rows]
    if belief_ids != ids:
        raise ValueError(f"BELIEF_CANDIDATE_ORDER:{challenge_id}")
    belief_total = sum(
        _validate_probability(row["probability"], f"{challenge_id}.intent_belief")
        for row in belief_rows
    )
    if not math.isclose(belief_total, 1.0, abs_tol=1e-9):
        raise ValueError(f"BELIEF_NOT_NORMALIZED:{challenge_id}")

    channel = case["question_channel_contract"]
    for row in channel["answer_confusion_matrix"]:
        if row["hypothesis_candidate_id"] not in ids:
            raise ValueError(f"CHANNEL_UNKNOWN_CANDIDATE:{challenge_id}")
        total = sum(
            _validate_probability(entry["probability"], f"{challenge_id}.channel")
            for entry in row["answer_probabilities"]
        )
        if not math.isclose(total, 1.0, abs_tol=1e-9):
            raise ValueError(f"CHANNEL_ROW_NOT_NORMALIZED:{challenge_id}")
    _validate_probability(channel["answer_resolution_probability"], f"{challenge_id}.resolution")
    _validate_probability(channel["no_answer_probability"], f"{challenge_id}.no_answer")
    delay_total = sum(
        _validate_probability(item["probability"], f"{challenge_id}.delay")
        for item in channel["delay_distribution"]
    )
    if not math.isclose(delay_total, 1.0, abs_tol=1e-9):
        raise ValueError(f"DELAY_NOT_NORMALIZED:{challenge_id}")

    partition = channel["candidate_partition"]
    flattened = [candidate for item in partition for candidate in item["candidate_ids"]]
    if channel["question_status"] == "QUESTION_PROPOSAL":
        if sorted(flattened) != sorted(ids) or len(flattened) != len(set(flattened)):
            raise ValueError(f"QUESTION_PARTITION_INVALID:{challenge_id}")
    elif flattened:
        raise ValueError(f"NONPROPOSAL_PARTITION_MUST_BE_EMPTY:{challenge_id}")

    now = case["monotonic_now"]
    deadline = case["answer_deadline_monotonic"]
    if not isinstance(now, (int, float)) or not math.isfinite(float(now)):
        raise ValueError(f"MONOTONIC_NOW_INVALID:{challenge_id}")
    if deadline is not None and (
        not isinstance(deadline, (int, float)) or not math.isfinite(float(deadline))
    ):
        raise ValueError(f"MONOTONIC_DEADLINE_INVALID:{challenge_id}")
    if case["candidate_input_contract"]["time_domain"] != "MONOTONIC_SECONDS":
        raise ValueError(f"NON_MONOTONIC_TIME_DOMAIN:{challenge_id}")

    for path, item in _walk(case):
        leaf = path.rsplit(".", 1)[-1].lower()
        if item is not None and (
            leaf.endswith("_cost")
            or leaf.endswith("_loss")
            or leaf in {"query_cost", "no_answer_penalty", "strict_value_epsilon"}
        ):
            _require_finite_nonnegative(item, path)

    matrix = case["counterfactual_runtime_inputs"]
    matrix_ids = matrix["candidate_ids"]
    if matrix_ids != ids:
        raise ValueError(f"MATRIX_CANDIDATE_ORDER:{challenge_id}")
    identities: set[tuple[str, str]] = set()
    for cell in matrix["cells"]:
        identity = (cell["action_candidate_id"], cell["hypothesis_candidate_id"])
        if identity in identities or not set(identity).issubset(ids):
            raise ValueError(f"MATRIX_CELL_IDENTITY:{challenge_id}")
        identities.add(identity)
        if cell["task_outcome"] == "UNKNOWN":
            if cell["task_error_cost"] is not None or cell["wrong_goal_indicator"] is not None:
                raise ValueError(f"UNKNOWN_CELL_HAS_SCALAR:{challenge_id}")
        else:
            _require_finite_nonnegative(cell["task_error_cost"], f"{challenge_id}.matrix")
    expected_cells = {(a, h) for a in ids for h in ids}
    if identities != expected_cells:
        raise ValueError(f"MATRIX_INCOMPLETE:{challenge_id}")

    if case["hard_gate_envelope"]["control_authorized"] is not False:
        raise ValueError(f"CONTROL_AUTHORIZATION_PRESENT:{challenge_id}")


def validate_hidden_case(hidden: Mapping[str, Any], runtime: Mapping[str, Any]) -> None:
    missing = HIDDEN_REQUIRED_FIELDS.difference(hidden)
    if missing:
        raise ValueError(f"HIDDEN_FIELDS_MISSING:{','.join(sorted(missing))}")
    if hidden["challenge_id"] != runtime["challenge_id"]:
        raise ValueError("HIDDEN_RUNTIME_ID_MISMATCH")
    if hidden["group_id"] != runtime["group_id"]:
        raise ValueError("HIDDEN_RUNTIME_GROUP_MISMATCH")
    if hidden["schema_version"] != SCHEMA_VERSION:
        raise ValueError("HIDDEN_SCHEMA_VERSION_MISMATCH")
    selected = hidden["expected_selected_candidate_id"]
    runtime_ids = [record["candidate_id"] for record in runtime["candidate_plan_records"]]
    if selected is not None and selected not in runtime_ids:
        raise ValueError("HIDDEN_SELECTED_CANDIDATE_UNKNOWN")
    if hidden["expected_act_target_type"] == "EQUIVALENCE_CLASS" and selected is not None:
        raise ValueError("HIDDEN_EQUIVALENCE_DEFAULT_CANDIDATE")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path.write_text(payload, encoding="utf-8")
