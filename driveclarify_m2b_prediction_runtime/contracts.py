"""Prediction-only contracts with no evaluator, reference, or gold semantics."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping


RAW_PREDICTION_SCHEMA_VERSION = "driveclarify.m2b_blind_raw_prediction.v2"
PARTITION_SCHEMA_VERSION = "driveclarify.m2b_blind_evaluation_partition.v1"
CANONICAL_SERIALIZATION = "UTF8_JSON_SORT_KEYS_COMPACT_NO_NAN_SINGLE_LF"

RUNTIME_FORBIDDEN_FRAGMENTS = (
    "gold", "latent_true", "expected_decision", "expected_action", "oracle",
    "wrong_goal", "regret", "baseline_answer", "evaluation_split",
)
EVALUATION_ONLY_FRAGMENTS = (
    "gold", "latent_true", "expected_loss", "oracle", "wrong_goal", "regret",
    "accepted_reason", "primary_core_member", "boundary_classification",
)
POLICY_INPUT_FORBIDDEN_FIELDS = frozenset({
    "track_r", "track_s_full_pool", "track_s_primary_core", "boundary_stress",
    "core_nonboundary", "canonical_execution_index", "canonical_case_index",
    "partition_generation_version", "partition_manifest_sha256",
})

RAW_PREDICTION_SEMANTIC_FIELDS = (
    "case_id", "canonical_case_index", "track", "track_r", "track_s_full_pool",
    "track_s_primary_core", "boundary_stress", "core_nonboundary",
    "matrix_archetype_id", "profile_id", "comparison_id",
    "canonical_comparison_index", "selected_action", "selected_candidate_id",
    "act_subtype", "reason_codes", "legal_action_mask", "R_act_A", "R_act_B",
    "R_ask", "R_wait", "V_ask", "V_wait", "posterior_summary",
    "query_episode_state", "matrix_sha256", "profile_sha256",
    "partition_manifest_sha256", "protocol_sha256", "runtime_package_sha256",
    "comparison_set_sha256", "prediction_schema_sha256", "authorization_eligible",
    "used_for_control", "control_authorized", "override_applied",
)
RAW_PREDICTION_FIELDS = tuple(sorted(RAW_PREDICTION_SEMANTIC_FIELDS))


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                       allow_nan=False) + "\n").encode("utf-8")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _key_paths(value: Any, prefix: str = "$") -> Iterable[tuple[str, str]]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            path = f"{prefix}.{key}"
            yield path, str(key)
            yield from _key_paths(item, path)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _key_paths(item, f"{prefix}[{index}]")


def runtime_leak_paths(value: Any) -> list[str]:
    return sorted(path for path, key in _key_paths(value)
                  if any(fragment in key.lower() for fragment in RUNTIME_FORBIDDEN_FRAGMENTS))


def policy_input_leak_paths(value: Any) -> list[str]:
    return sorted({path for path, key in _key_paths(value)
                   if key in POLICY_INPUT_FORBIDDEN_FIELDS
                   or any(fragment in key.lower() for fragment in EVALUATION_ONLY_FRAGMENTS)}
                  | set(runtime_leak_paths(value)))


def validate_runtime_package(package: Mapping[str, Any]) -> None:
    leaks = runtime_leak_paths(package)
    if leaks:
        raise ValueError("R2_RUNTIME_EVALUATION_FIELD_LEAK:" + ",".join(leaks[:10]))
    cases = package.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("R2_RUNTIME_CASES_MISSING")
    ids = [case.get("case_id") for case in cases if isinstance(case, Mapping)]
    if len(ids) != len(cases) or len(set(ids)) != len(ids) or ids != sorted(ids):
        raise ValueError("R2_RUNTIME_CANONICAL_CASE_ORDER_INVALID")


def validate_partition_manifest(manifest: Mapping[str, Any]) -> None:
    if manifest.get("schema_version") != PARTITION_SCHEMA_VERSION:
        raise ValueError("R2_PARTITION_SCHEMA_INVALID")
    rows = manifest.get("partitions")
    if not isinstance(rows, list) or not rows:
        raise ValueError("R2_PARTITION_ROWS_MISSING")
    ids: list[str] = []
    for index, row in enumerate(rows):
        if row.get("canonical_execution_index") != index:
            raise ValueError("R2_PARTITION_INDEX_INVALID")
        ids.append(str(row.get("case_id")))
        if bool(row.get("track_r")) == bool(row.get("track_s_full_pool")):
            raise ValueError("R2_PARTITION_TRACK_MEMBERSHIP_INVALID")
        if bool(row.get("boundary_stress")) == bool(row.get("core_nonboundary")):
            raise ValueError("R2_PARTITION_BOUNDARY_COMPLEMENT_INVALID")
    if ids != sorted(ids) or len(ids) != len(set(ids)):
        raise ValueError("R2_PARTITION_CANONICAL_CASE_ORDER_INVALID")


def validate_raw_prediction_record(record: Mapping[str, Any]) -> None:
    if tuple(record.keys()) != RAW_PREDICTION_FIELDS:
        raise ValueError("R2_RAW_PREDICTION_EXACT_FIELD_ORDER_INVALID")
    if runtime_leak_paths(record):
        raise ValueError("R2_RAW_PREDICTION_EVALUATION_FIELD_FORBIDDEN")
    if record["selected_action"] not in {"ACT", "ASK", "WAIT", "FALLBACK"}:
        raise ValueError("R2_RAW_PREDICTION_ACTION_INVALID")
    if record["authorization_eligible"] or record["used_for_control"] or record["control_authorized"]:
        raise ValueError("R2_RAW_PREDICTION_CONTROL_AUTHORIZATION_FORBIDDEN")
    if record["override_applied"]:
        raise ValueError("R2_RAW_PREDICTION_OVERRIDE_FORBIDDEN")
    if bool(record["boundary_stress"]) == bool(record["core_nonboundary"]):
        raise ValueError("R2_RAW_PREDICTION_BOUNDARY_COMPLEMENT_INVALID")
    for name in ("R_act_A", "R_act_B", "R_ask", "R_wait", "V_ask", "V_wait"):
        value = record[name]
        if value is not None and (not isinstance(value, (int, float)) or isinstance(value, bool)):
            raise ValueError(f"R2_RAW_PREDICTION_NUMERIC_INVALID:{name}")


def validate_prediction_envelope(envelope: Mapping[str, Any]) -> None:
    fields = ("schema_version", "design_id", "prediction_event_id", "completeness",
              "expected_record_count", "record_count", "canonical_record_order", "records")
    if set(envelope) != set(fields):
        raise ValueError("R2_PREDICTION_ENVELOPE_FIELDS_INVALID")
    if envelope["schema_version"] != RAW_PREDICTION_SCHEMA_VERSION:
        raise ValueError("R2_PREDICTION_ENVELOPE_SCHEMA_INVALID")
    if envelope["completeness"] not in {"COMPLETE", "PARTIAL_CONSUMED"}:
        raise ValueError("R2_PREDICTION_COMPLETENESS_INVALID")
    records = envelope["records"]
    if envelope["record_count"] != len(records):
        raise ValueError("R2_PREDICTION_RECORD_COUNT_INVALID")
    if envelope["completeness"] == "COMPLETE" and len(records) != envelope["expected_record_count"]:
        raise ValueError("R2_COMPLETE_PREDICTION_COUNT_INVALID")
    if envelope["completeness"] == "PARTIAL_CONSUMED" and len(records) >= envelope["expected_record_count"]:
        raise ValueError("R2_PARTIAL_PREDICTION_COUNT_INVALID")
    for record in records:
        validate_raw_prediction_record(record)
    order = [(r["canonical_case_index"], r["canonical_comparison_index"]) for r in records]
    if order != sorted(order) or len(order) != len(set(order)):
        raise ValueError("R2_PREDICTION_RECORD_ORDER_INVALID")
