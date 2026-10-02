"""Versioned R1 execution-contract primitives for the sealed M2B evaluation.

This module contains no tested-policy invocation and no gold reader.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .contracts import canonical_bytes, file_sha256, runtime_leak_paths


R1_RAW_PREDICTION_SCHEMA_VERSION = "driveclarify.m2b_blind_raw_prediction.v2"
R1_PARTITION_SCHEMA_VERSION = "driveclarify.m2b_blind_evaluation_partition.v1"
R1_COMMITMENT_BUNDLE_SCHEMA_VERSION = "driveclarify.m2b_blind_execution_commitment_bundle.v1"
R1_CANONICAL_SERIALIZATION = "UTF8_JSON_SORT_KEYS_COMPACT_NO_NAN_SINGLE_LF"

PARTITION_SEMANTIC_FIELDS = (
    "case_id", "track", "track_r", "track_s_full_pool", "track_s_primary_core",
    "boundary_stress", "core_nonboundary", "matrix_archetype_id", "profile_id",
    "canonical_execution_index", "partition_generation_version", "source_hashes",
)

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

# canonical_bytes uses sort_keys=True; these are therefore the exact serialized
# object-key orders, independent of construction order.
PARTITION_FIELDS = tuple(sorted(PARTITION_SEMANTIC_FIELDS))
RAW_PREDICTION_FIELDS = tuple(sorted(RAW_PREDICTION_SEMANTIC_FIELDS))

POLICY_INPUT_FORBIDDEN_FIELDS = frozenset({
    "track_r", "track_s_full_pool", "track_s_primary_core", "boundary_stress",
    "core_nonboundary", "canonical_execution_index", "canonical_case_index",
    "partition_generation_version", "partition_manifest_sha256",
})

EVALUATION_ONLY_FRAGMENTS = (
    "gold", "latent_true", "expected_loss", "oracle", "wrong_goal", "regret",
    "accepted_reason", "primary_core_member", "boundary_classification",
)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def canonical_sha256(value: Any) -> str:
    return sha256_bytes(canonical_bytes(value))


def _key_paths(value: Any, prefix: str = "$") -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            path = f"{prefix}.{key}"
            result.append((path, str(key)))
            result.extend(_key_paths(item, path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            result.extend(_key_paths(item, f"{prefix}[{index}]"))
    return result


def evaluation_only_paths(value: Any) -> list[str]:
    return sorted(path for path, key in _key_paths(value)
                  if any(fragment in key.lower() for fragment in EVALUATION_ONLY_FRAGMENTS))


def policy_input_leak_paths(case: Mapping[str, Any]) -> list[str]:
    membership = [path for path, key in _key_paths(case) if key in POLICY_INPUT_FORBIDDEN_FIELDS]
    return sorted(set(membership + runtime_leak_paths(case) + evaluation_only_paths(case)))


def validate_partition_manifest(manifest: Mapping[str, Any]) -> None:
    if manifest.get("schema_version") != R1_PARTITION_SCHEMA_VERSION:
        raise ValueError("R1_PARTITION_SCHEMA_VERSION_INVALID")
    rows = manifest.get("partitions")
    if not isinstance(rows, list) or not rows:
        raise ValueError("R1_PARTITION_ROWS_MISSING")
    if evaluation_only_paths(manifest):
        raise ValueError("R1_PARTITION_EVALUATION_FIELD_LEAK")
    ids: list[str] = []
    for index, row in enumerate(rows):
        if tuple(row.keys()) != PARTITION_FIELDS:
            raise ValueError("R1_PARTITION_FIELD_ORDER_INVALID")
        if row["canonical_execution_index"] != index:
            raise ValueError("R1_PARTITION_INDEX_INVALID")
        ids.append(str(row["case_id"]))
        flags = [bool(row["track_r"]), bool(row["track_s_full_pool"])]
        if sum(flags) != 1 or row["track"] not in {"R", "S"}:
            raise ValueError("R1_PARTITION_TRACK_MEMBERSHIP_INVALID")
        if row["track_s_primary_core"] and (not row["track_s_full_pool"] or row["boundary_stress"]):
            raise ValueError("R1_PARTITION_PRIMARY_CORE_INVALID")
        if bool(row["boundary_stress"]) == bool(row["core_nonboundary"]):
            raise ValueError("R1_PARTITION_BOUNDARY_COMPLEMENT_INVALID")
    if ids != sorted(ids) or len(ids) != len(set(ids)):
        raise ValueError("R1_PARTITION_CANONICAL_CASE_ORDER_INVALID")


def validate_raw_prediction_record(record: Mapping[str, Any]) -> None:
    if tuple(record.keys()) != RAW_PREDICTION_FIELDS:
        raise ValueError("R1_RAW_PREDICTION_EXACT_FIELD_ORDER_INVALID")
    if evaluation_only_paths(record) or runtime_leak_paths(record):
        raise ValueError("R1_RAW_PREDICTION_EVALUATION_FIELD_FORBIDDEN")
    if record["selected_action"] not in {"ACT", "ASK", "WAIT", "FALLBACK"}:
        raise ValueError("R1_RAW_PREDICTION_ACTION_INVALID")
    if record["authorization_eligible"] or record["used_for_control"] or record["control_authorized"]:
        raise ValueError("R1_RAW_PREDICTION_CONTROL_AUTHORIZATION_FORBIDDEN")
    if record["override_applied"]:
        raise ValueError("R1_RAW_PREDICTION_OVERRIDE_FORBIDDEN")
    if bool(record["boundary_stress"]) == bool(record["core_nonboundary"]):
        raise ValueError("R1_RAW_PREDICTION_BOUNDARY_COMPLEMENT_INVALID")
    for name in ("R_act_A", "R_act_B", "R_ask", "R_wait", "V_ask", "V_wait"):
        if record[name] is not None and (not isinstance(record[name], (int, float)) or isinstance(record[name], bool)):
            raise ValueError(f"R1_RAW_PREDICTION_NUMERIC_INVALID:{name}")


def validate_prediction_envelope(envelope: Mapping[str, Any]) -> None:
    required = (
        "schema_version", "design_id", "prediction_event_id", "completeness",
        "expected_record_count", "record_count", "canonical_record_order", "records",
    )
    if tuple(envelope.keys()) != required:
        raise ValueError("R1_PREDICTION_ENVELOPE_FIELDS_INVALID")
    if envelope["schema_version"] != R1_RAW_PREDICTION_SCHEMA_VERSION:
        raise ValueError("R1_PREDICTION_ENVELOPE_SCHEMA_INVALID")
    if envelope["completeness"] not in {"COMPLETE", "PARTIAL_CONSUMED"}:
        raise ValueError("R1_PREDICTION_COMPLETENESS_INVALID")
    records = envelope["records"]
    if envelope["record_count"] != len(records):
        raise ValueError("R1_PREDICTION_RECORD_COUNT_INVALID")
    for record in records:
        validate_raw_prediction_record(record)
    order = [(r["canonical_case_index"], r["canonical_comparison_index"]) for r in records]
    if order != sorted(order) or len(order) != len(set(order)):
        raise ValueError("R1_PREDICTION_RECORD_ORDER_INVALID")


def commitment_entry(path: Path, *, repo_root: Path, schema_or_version: str,
                     canonical_serialization: str = R1_CANONICAL_SERIALIZATION) -> dict[str, Any]:
    return {
        "artifact": str(path.relative_to(repo_root)),
        "bytes": path.stat().st_size,
        "sha256": file_sha256(path),
        "schema_or_version": schema_or_version,
        "canonical_serialization": canonical_serialization,
        "verification_method": "FILE_BYTES_SHA256",
    }


def verify_commitment_bundle(bundle: Mapping[str, Any], repo_root: Path,
                             *, exclude_names: Sequence[str] = ("sealed_gold",)) -> dict[str, Any]:
    if bundle.get("schema_version") != R1_COMMITMENT_BUNDLE_SCHEMA_VERSION:
        raise ValueError("R1_COMMITMENT_BUNDLE_SCHEMA_INVALID")
    checks: dict[str, bool] = {}
    for name, entry in bundle["commitments"].items():
        method = entry.get("verification_method", "FILE_BYTES_SHA256")
        if name in exclude_names or method == "SEALED_DIGEST_COMMITMENT_METADATA":
            checks[name] = (
                entry["sha256"] == bundle["sealed_gold_commitment_sha256"]
                and entry["bytes"] == bundle["sealed_gold_committed_bytes"]
            )
            continue
        if method == "CANONICAL_EMBEDDED_VALUE_SHA256":
            payload = canonical_bytes(entry["value"])
            checks[name] = len(payload) == entry["bytes"] and sha256_bytes(payload) == entry["sha256"]
            continue
        path = repo_root / entry["artifact"]
        checks[name] = path.is_file() and path.stat().st_size == entry["bytes"] and file_sha256(path) == entry["sha256"]
    return {"status": "PASS" if all(checks.values()) else "FAIL", "checks": checks}
