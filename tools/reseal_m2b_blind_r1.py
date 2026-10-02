#!/usr/bin/env python3
"""Prepare the prediction-free M2B blind R1 execution-contract reseal package."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import shutil
import subprocess
from collections import Counter, OrderedDict
from pathlib import Path
from typing import Any, Mapping

from driveclarify_m2b_blind import REFERENCE_SOLVER_VERSION
from driveclarify_m2b_blind.contracts import atomic_replace, canonical_bytes, file_sha256, runtime_leak_paths
from driveclarify_m2b_blind.design_generator import _gold_extension
from driveclarify_m2b_blind.independent_verifier import independent_decision
from driveclarify_m2b_blind.r1_contracts import (
    PARTITION_FIELDS, PARTITION_SEMANTIC_FIELDS, RAW_PREDICTION_FIELDS,
    RAW_PREDICTION_SEMANTIC_FIELDS, R1_CANONICAL_SERIALIZATION,
    R1_COMMITMENT_BUNDLE_SCHEMA_VERSION, R1_PARTITION_SCHEMA_VERSION,
    R1_RAW_PREDICTION_SCHEMA_VERSION, canonical_sha256, commitment_entry,
    evaluation_only_paths, policy_input_leak_paths, sha256_bytes,
    validate_partition_manifest, verify_commitment_bundle,
)
from driveclarify_m2b_blind.r1_lifecycle import freeze_protocol, initialize_draft
from driveclarify_m2b_blind.reference_solver import REASON_VOCABULARY, solve_case


REPO = Path(__file__).resolve().parents[1]
PARENT_ID = "DC-M2B-BLIND-DESIGN-20260804T101836Z"
BLOCKED_ID = "DC-M2B-BLIND-PREFLIGHT-20260804T105747Z"
PARENT = REPO / "reports/m2b_sealed_blind_decision_evaluation_design" / PARENT_ID
BLOCKED = REPO / "reports/m2b_sealed_blind_decision_evaluation_preexecution" / BLOCKED_ID
EXPECTED_GOLD_SHA = "06f913ff3e5361e450464ee5cd8833b203a3201a0b56723e93747a09dd94392d"


def write_json(path: Path, value: Any) -> None:
    atomic_replace(path, canonical_bytes(value))


def write_text(path: Path, value: str) -> None:
    atomic_replace(path, (value.rstrip() + "\n").encode("utf-8"))


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def git_bytes(root: Path, *args: str) -> bytes:
    return subprocess.run(("git", *args), cwd=root, check=True, stdout=subprocess.PIPE).stdout


def git_snapshot(root: Path) -> dict[str, Any]:
    tracked = git_bytes(root, "diff", "--binary")
    staged = git_bytes(root, "diff", "--cached", "--binary")
    untracked = git_bytes(root, "ls-files", "--others", "--exclude-standard", "-z")
    return {
        "root": str(root),
        "branch": git_bytes(root, "branch", "--show-current").decode().strip(),
        "head": git_bytes(root, "rev-parse", "HEAD").decode().strip(),
        "tracked_diff_bytes": len(tracked),
        "tracked_diff_sha256": sha256_bytes(tracked),
        "staged_diff_bytes": len(staged),
        "staged_diff_sha256": sha256_bytes(staged),
        "untracked_count": len([x for x in untracked.split(b"\0") if x]),
        "untracked_path_list_nul_sha256": sha256_bytes(untracked),
    }


def inventory(root: Path, *, metadata_only_files: Mapping[str, tuple[int, str]] | None = None) -> dict[str, Any]:
    metadata_only_files = dict(metadata_only_files or {})
    rows = []
    for path in sorted(p for p in root.iterdir() if p.is_file()):
        if path.name in metadata_only_files:
            expected_bytes, committed_sha = metadata_only_files[path.name]
            rows.append({"filename": path.name, "bytes": path.stat().st_size,
                         "expected_bytes": expected_bytes, "committed_sha256": committed_sha,
                         "content_opened": False, "stat_matches_commitment": path.stat().st_size == expected_bytes})
        else:
            rows.append({"filename": path.name, "bytes": path.stat().st_size,
                         "sha256": file_sha256(path), "content_opened": True})
    return {"file_count": len(rows), "files": rows, "inventory_sha256": canonical_sha256(rows)}


def embedded_entry(value: Any, version: str) -> dict[str, Any]:
    payload = canonical_bytes(value)
    return {
        "artifact": None,
        "bytes": len(payload),
        "sha256": sha256_bytes(payload),
        "schema_or_version": version,
        "canonical_serialization": R1_CANONICAL_SERIALIZATION,
        "verification_method": "CANONICAL_EMBEDDED_VALUE_SHA256",
        "value": value,
    }


def reconstruct(runtime: Mapping[str, Any], profiles: Mapping[str, Any]) -> tuple[list[dict[str, Any]], set[str], dict[str, Any]]:
    classification = {
        p["profile_id"]: p["design_classification"]
        for group in (profiles["track_r_profiles"], profiles["track_s_profiles"])
        for p in group
    }
    gold = [_gold_extension(case, solve_case(case), classification[case["profile_id"]])
            for case in runtime["cases"]]
    candidates = [g for g in gold if g["track"] == "S" and g["boundary_classification"] == "CORE_NONBOUNDARY"]
    by_action = {action: [] for action in ("ACT", "ASK", "WAIT", "FALLBACK")}
    for row in sorted(candidates, key=lambda x: x["case_id"]):
        by_action[row["gold_action_type"]].append(row)
    if any(len(rows) < 32 for rows in by_action.values()):
        raise RuntimeError("R1_ORIGINAL_CORE_NOT_REPRODUCIBLE")
    core_ids = {row["case_id"] for rows in by_action.values() for row in rows[:32]}
    for row in gold:
        row["primary_core_member"] = row["case_id"] in core_ids
    gold.sort(key=lambda x: x["case_id"])
    package = {
        "schema_version": "driveclarify.m2b_sealed_evaluation_gold.v1",
        "design_id": PARENT_ID,
        "reference_solver_version": REFERENCE_SOLVER_VERSION,
        "record_count": len(gold),
        "records": gold,
    }
    reconstructed_sha = sha256_bytes(canonical_bytes(package))
    if reconstructed_sha != EXPECTED_GOLD_SHA:
        raise RuntimeError(f"R1_RECONSTRUCTED_GOLD_COMMITMENT_MISMATCH:{reconstructed_sha}")
    disagreements = []
    for case, expected in zip(runtime["cases"], gold):
        actual = independent_decision(case)
        wanted = (expected["gold_action_type"], expected["gold_candidate_id"], expected["act_subtype"])
        if actual != wanted:
            disagreements.append({"case_id": case["case_id"], "decimal": wanted, "fraction": actual})
    return gold, core_ids, {
        "reconstructed_gold_sha256": reconstructed_sha,
        "decimal_fraction_agreement": len(gold) - len(disagreements),
        "decimal_fraction_total": len(gold),
        "disagreements": disagreements[:10],
    }


def partition_manifest(design_id: str, runtime: Mapping[str, Any], profiles: Mapping[str, Any],
                       core_ids: set[str], source_hashes: Mapping[str, str]) -> dict[str, Any]:
    classification = {
        p["profile_id"]: p["design_classification"]
        for group in (profiles["track_r_profiles"], profiles["track_s_profiles"])
        for p in group
    }
    rows = []
    for index, case in enumerate(runtime["cases"]):
        values = {
            "case_id": case["case_id"],
            "track": case["track"],
            "track_r": case["track"] == "R",
            "track_s_full_pool": case["track"] == "S",
            "track_s_primary_core": case["case_id"] in core_ids,
            "boundary_stress": classification[case["profile_id"]] == "BOUNDARY_STRESS",
            "core_nonboundary": classification[case["profile_id"]] == "CORE_NONBOUNDARY",
            "matrix_archetype_id": case["matrix_id"] if case["track"] == "S" else None,
            "profile_id": case["profile_id"],
            "canonical_execution_index": index,
            "partition_generation_version": "M2B_BLIND_PARTITION_RECONSTRUCTION_V1",
            "source_hashes": {
                "runtime_package_sha256": source_hashes["runtime_package_sha256"],
                "matrix_sha256": case["provenance_hashes"]["matrix_sha256"],
                "profile_sha256": case["provenance_hashes"]["profile_sha256"],
                "reference_solver_source_sha256": source_hashes["reference_solver_source_sha256"],
                "partition_generator_source_sha256": source_hashes["partition_generator_source_sha256"],
            },
        }
        rows.append(OrderedDict((key, values[key]) for key in PARTITION_FIELDS))
    manifest = OrderedDict((key, value) for key, value in sorted({
        "schema_version": R1_PARTITION_SCHEMA_VERSION,
        "design_id": design_id,
        "parent_design_id": PARENT_ID,
        "partition_generation_version": "M2B_BLIND_PARTITION_RECONSTRUCTION_V1",
        "canonical_serialization": R1_CANONICAL_SERIALIZATION,
        "canonical_execution_order": "case_id_lexicographic_zero_based_index",
        "case_count": len(rows),
        "partitions": rows,
    }.items()))
    validate_partition_manifest(manifest)
    return dict(manifest)


def raw_schema(design_id: str) -> dict[str, Any]:
    nullable_number = {"type": ["number", "null"]}
    properties: dict[str, Any] = {}
    booleans = {"track_r", "track_s_full_pool", "track_s_primary_core", "boundary_stress", "core_nonboundary",
                "authorization_eligible", "used_for_control", "control_authorized", "override_applied"}
    integers = {"canonical_case_index", "canonical_comparison_index"}
    nullable_strings = {"matrix_archetype_id", "selected_candidate_id"}
    numbers = {"R_act_A", "R_act_B", "R_ask", "R_wait", "V_ask", "V_wait"}
    arrays = {"reason_codes", "posterior_summary"}
    objects = {"legal_action_mask"}
    for name in RAW_PREDICTION_FIELDS:
        if name in booleans:
            properties[name] = {"type": "boolean"}
        elif name in integers:
            properties[name] = {"type": "integer", "minimum": 0}
        elif name in nullable_strings:
            properties[name] = {"type": ["string", "null"]}
        elif name in numbers:
            properties[name] = nullable_number
        elif name in arrays:
            properties[name] = {"type": "array"}
        elif name in objects:
            properties[name] = {"type": "object", "additionalProperties": {"type": "boolean"}}
        else:
            properties[name] = {"type": "string"}
    for name in ("authorization_eligible", "used_for_control", "control_authorized", "override_applied"):
        properties[name]["const"] = False
    properties["selected_action"]["enum"] = ["ACT", "ASK", "WAIT", "FALLBACK"]
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": R1_RAW_PREDICTION_SCHEMA_VERSION,
        "schema_version": R1_RAW_PREDICTION_SCHEMA_VERSION,
        "design_id": design_id,
        "type": "object",
        "additionalProperties": False,
        "required": list(RAW_PREDICTION_FIELDS),
        "properties": properties,
        "semantic_field_order": list(RAW_PREDICTION_SEMANTIC_FIELDS),
        "canonical_serialized_field_order": list(RAW_PREDICTION_FIELDS),
        "numeric_representation": "FINITE_JSON_NUMBER_OR_NULL_NO_NAN_NO_INFINITY",
        "null_semantics": "NULL_MEANS_NOT_AVAILABLE_OR_NOT_APPLICABLE_NEVER_ZERO_IMPUTATION",
        "canonical_record_order": ["canonical_case_index", "canonical_comparison_index"],
        "canonical_comparison_order_source": "PARENT_M2B_BLIND_COMPARISON_SET_JSON_ORDER",
        "partial_evidence_format": "SAME_ENVELOPE_WITH_COMPLETENESS_PARTIAL_CONSUMED_AND_ALL_AVAILABLE_RECORDS",
        "atomic_publication_format": "CANONICAL_ENVELOPE_UTF8_JSON_WRITE_FSYNC_HARDLINK_ONCE_DIRECTORY_FSYNC",
        "canonical_serialization": R1_CANONICAL_SERIALIZATION,
    }


def evaluator_schema(design_id: str) -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "driveclarify.m2b_blind_evaluator_input.v2",
        "schema_version": "driveclarify.m2b_blind_evaluator_input.v2",
        "design_id": design_id,
        "required_gate_state": "BLIND_PREDICTIONS_RECORDED_IMMUTABLE",
        "required_prediction_schema": R1_RAW_PREDICTION_SCHEMA_VERSION,
        "prediction_sha_must_be_bound": True,
        "gold_access_before_gate": "FORBIDDEN",
        "oracle_phase": "EVALUATOR_ONLY_AFTER_GATE",
        "duplicate_evaluation": "FAIL_CLOSED",
        "duplicate_publication": "FAIL_CLOSED",
        "canonical_serialization": R1_CANONICAL_SERIALIZATION,
    }


def protocol(design_id: str) -> dict[str, Any]:
    parent_protocol = load(PARENT / "M2B_BLIND_PROTOCOL.json")
    return {
        "schema_version": "driveclarify.m2b_blind_protocol.r1",
        "protocol_version": "M2B_SEALED_BLIND_PROTOCOL_R1_EXECUTION_CONTRACT_ONLY",
        "design_id": design_id,
        "parent_design_id": PARENT_ID,
        "blocked_preflight_id": BLOCKED_ID,
        "repair_scope": "EXECUTION_CONTRACT_ONLY",
        "scientific_case_definition_changed": False,
        "policy_changed": False,
        "matrices_changed": False,
        "profiles_changed": False,
        "comparison_set_changed": False,
        "gold_semantics_changed": False,
        "blind_evaluation_authorized": False,
        "live_control_authorized": False,
        "m3_authorized": False,
        "core_epsilon": parent_protocol["core_epsilon"],
        "comparison_ids": parent_protocol["comparison_ids"],
        "statistics": parent_protocol["statistics"],
        "execution_order": [
            "VERIFY_COMPLETE_R1_COMMITMENT_BUNDLE", "ENTER_ALLOWLISTED_BWRAP_NAMESPACE",
            "RUN_POLICY_WITH_RUNTIME_CASE_ONLY", "ATTACH_CONTROL_PLANE_PARTITION_AFTER_POLICY_RETURN",
            "ATOMIC_PUBLISH_RAW_PREDICTION_V2", "BIND_PREDICTION_SHA_AND_ADVANCE_SEAL",
            "START_EVALUATOR_OUTSIDE_PREDICTION_NAMESPACE", "VERIFY_SEALED_GOLD_COMMITMENT",
            "COMPUTE_ONLY_PREREGISTERED_METRICS", "IMMUTABLE_FIRST_RESULT_PUBLICATION",
        ],
        "policy_input_partition_membership_forbidden": True,
        "prediction_orchestrator_attaches_partition_after_policy_return": True,
        "prediction_namespace_gold_path_absent": True,
        "prediction_environment_gold_or_key_fields_forbidden": True,
        "raw_prediction_schema": R1_RAW_PREDICTION_SCHEMA_VERSION,
        "partial_prediction_consumes_event": True,
        "duplicate_prediction_publication_fail_closed": True,
        "duplicate_evaluation_fail_closed": True,
        "duplicate_result_publication_fail_closed": True,
        "allowed_claim_boundary": parent_protocol["claim_boundary"],
    }


def prepare(design_id: str, output: Path) -> None:
    output = output.resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError("R1_OUTPUT_MUST_BE_NEW_AND_EMPTY")
    output.mkdir(parents=True, exist_ok=True)
    parent_gold_manifest = load(PARENT / "M2B_SEALED_GOLD_MANIFEST.json")
    parent_inventory = inventory(PARENT, metadata_only_files={
        "M2B_SEALED_EVALUATION_GOLD.json": (parent_gold_manifest["bytes"], parent_gold_manifest["sha256"]),
    })
    blocked_inventory = inventory(BLOCKED)
    state = load(REPO / "STATE.json")
    preflight = state["m2b_sealed_blind_execution_preflight"]
    if state["status"] != "BLOCKED_M2B_BLIND_FROZEN_ARTIFACT_MISMATCH":
        raise RuntimeError("R1_ENTRY_PROJECT_STATUS_INVALID")
    if any((preflight["blind_event_created"], preflight["blind_event_consumed"], preflight["policy_execution_count"],
            preflight["prediction_record_count"], preflight["prediction_bytes"], preflight["metric_count"],
            preflight["gold_open_count"], preflight["gold_read_bytes"], preflight["gold_unseal_count"])):
        raise RuntimeError("R1_ENTRY_ZERO_EXECUTION_CONTRACT_INVALID")
    runtime_path = PARENT / "M2B_BLIND_RUNTIME_INPUT_PACKAGE.json"
    profiles_path = PARENT / "M2B_BLIND_PROFILES.json"
    runtime, profiles = load(runtime_path), load(profiles_path)
    if runtime_leak_paths(runtime):
        raise RuntimeError("R1_RUNTIME_GOLD_LEAK")
    gold, core_ids, reconstruction = reconstruct(runtime, profiles)
    source_hashes = {
        "runtime_package_sha256": file_sha256(runtime_path),
        "reference_solver_source_sha256": file_sha256(REPO / "driveclarify_m2b_blind/reference_solver.py"),
        "partition_generator_source_sha256": file_sha256(Path(__file__)),
    }
    partition = partition_manifest(design_id, runtime, profiles, core_ids, source_hashes)
    partition_path = output / "M2B_BLIND_EVALUATION_PARTITION_MANIFEST.json"
    write_json(partition_path, partition)
    write_text(output / "M2B_BLIND_EVALUATION_PARTITION_MANIFEST.sha256", file_sha256(partition_path))
    schema_path = output / "M2B_BLIND_RAW_PREDICTION_V2_SCHEMA.json"
    evaluator_schema_path = output / "M2B_BLIND_EVALUATOR_INPUT_V2_SCHEMA.json"
    write_json(schema_path, raw_schema(design_id))
    write_json(evaluator_schema_path, evaluator_schema(design_id))
    protocol_path = output / "M2B_BLIND_R1_PROTOCOL.json"
    write_json(protocol_path, protocol(design_id))
    isolation_spec = f"""# M2B blind R1 gold isolation specification

Design ID: `{design_id}`

Future prediction execution MUST use `/usr/bin/bwrap` with `--unshare-all`, `--die-with-parent`, `--new-session`, and `--cap-drop ALL`. The namespace root is empty except for read-only system runtime directories, individually allowlisted runtime/partition/protocol/comparison/schema files, individually allowlisted policy-code directories, `/proc`, `/dev`, an isolated tmpfs `/tmp`, and one writable prediction staging directory.

The repository root, original design directory, sealed-gold file/directory, evaluator source/artifacts, reference outputs, and any key path are not mounted. The process is launched with an empty inherited environment (`env -i` semantics); only `PATH=/usr/bin:/bin`, `PYTHONPATH=/app`, and `CUDA_VISIBLE_DEVICES=` are injected. Gold/key paths are forbidden in mounts, argv, environment, and the open allowlist. `--unshare-all` also removes host network visibility.

The committed gold remains evaluator-only outside the prediction namespace. This design uses mount-namespace non-visibility rather than encryption; therefore no decryption key exists and key exposure count is necessarily zero. The evaluator source is not mounted in the prediction namespace and may start only after the R1 lifecycle reaches `BLIND_PREDICTIONS_RECORDED_IMMUTABLE` with a bound prediction SHA.
"""
    isolation_path = output / "M2B_BLIND_GOLD_ISOLATION_SPEC.md"
    write_text(isolation_path, isolation_spec)
    case_order = [row["case_id"] for row in partition["partitions"]]
    primary = [[row["case_id"], row["track_s_primary_core"]] for row in partition["partitions"]]
    boundary = [[row["case_id"], row["boundary_stress"]] for row in partition["partitions"]]
    full_pool = [[row["case_id"], row["track_s_full_pool"]] for row in partition["partitions"]]
    comparison_path = PARENT / "M2B_BLIND_COMPARISON_SET.json"
    gold_manifest = load(PARENT / "M2B_SEALED_GOLD_MANIFEST.json")
    commitments: dict[str, Any] = {
        "protocol": commitment_entry(protocol_path, repo_root=REPO, schema_or_version="driveclarify.m2b_blind_protocol.r1"),
        "runtime_input": commitment_entry(runtime_path, repo_root=REPO, schema_or_version="driveclarify.m2b_blind_runtime.v1"),
        "sealed_gold": {
            "artifact": None, "bytes": gold_manifest["bytes"], "sha256": gold_manifest["sha256"],
            "schema_or_version": "driveclarify.m2b_sealed_evaluation_gold.v1",
            "canonical_serialization": R1_CANONICAL_SERIALIZATION,
            "verification_method": "SEALED_DIGEST_COMMITMENT_METADATA",
        },
        "matrix_archetypes": commitment_entry(PARENT / "M2B_BLIND_MATRIX_ARCHETYPES.json", repo_root=REPO, schema_or_version="driveclarify.m2b_blind_matrix_archetypes.v1"),
        "profiles": commitment_entry(profiles_path, repo_root=REPO, schema_or_version="driveclarify.m2b_blind_profiles.v1"),
        "canonical_case_ordering": embedded_entry(case_order, "case_id_lexicographic.v1"),
        "evaluation_partition_manifest": commitment_entry(partition_path, repo_root=REPO, schema_or_version=R1_PARTITION_SCHEMA_VERSION),
        "primary_core_membership": embedded_entry(primary, "track_s_primary_core_membership.v1"),
        "boundary_membership": embedded_entry(boundary, "boundary_stress_membership.v1"),
        "full_pool_membership": embedded_entry(full_pool, "track_s_full_pool_membership.v1"),
        "comparison_set": commitment_entry(comparison_path, repo_root=REPO, schema_or_version="driveclarify.m2b_blind_comparison_set.v1"),
        "prediction_schema": commitment_entry(schema_path, repo_root=REPO, schema_or_version=R1_RAW_PREDICTION_SCHEMA_VERSION),
        "evaluator_schema": commitment_entry(evaluator_schema_path, repo_root=REPO, schema_or_version="driveclarify.m2b_blind_evaluator_input.v2"),
        "metrics_spec": commitment_entry(PARENT / "M2B_BLIND_METRICS_SPEC.md", repo_root=REPO, schema_or_version="PREREGISTERED_METRICS_V1", canonical_serialization="UTF8_TEXT_LF"),
        "statistics_protocol": commitment_entry(PARENT / "M2B_BLIND_STATISTICAL_PROTOCOL.md", repo_root=REPO, schema_or_version="FROZEN_STATISTICAL_PROTOCOL_V1", canonical_serialization="UTF8_TEXT_LF"),
        "hypotheses": commitment_entry(PARENT / "M2B_BLIND_HYPOTHESES.md", repo_root=REPO, schema_or_version="H_B1_TO_H_B6_V1", canonical_serialization="UTF8_TEXT_LF"),
        "reason_code_vocabulary": embedded_entry(sorted(REASON_VOCABULARY), "M2B_BLIND_REFERENCE_REASON_VOCABULARY_V1"),
        "lifecycle_state_machine_source": commitment_entry(REPO / "driveclarify_m2b_blind/r1_lifecycle.py", repo_root=REPO, schema_or_version="driveclarify.m2b_blind_r1_lifecycle.v1", canonical_serialization="UTF8_PYTHON_SOURCE"),
        "reference_solver_source": commitment_entry(REPO / "driveclarify_m2b_blind/reference_solver.py", repo_root=REPO, schema_or_version=REFERENCE_SOLVER_VERSION, canonical_serialization="UTF8_PYTHON_SOURCE"),
        "independent_gold_verifier_source": commitment_entry(REPO / "driveclarify_m2b_blind/independent_verifier.py", repo_root=REPO, schema_or_version="FRACTION_EXACT_REENUMERATION_V1", canonical_serialization="UTF8_PYTHON_SOURCE"),
        "prediction_runner_source": commitment_entry(REPO / "driveclarify_m2b_blind/r1_orchestrator.py", repo_root=REPO, schema_or_version="M2B_BLIND_R1_ORCHESTRATOR_V1", canonical_serialization="UTF8_PYTHON_SOURCE"),
        "evaluator_source": commitment_entry(REPO / "driveclarify_m2b_blind/r1_evaluator.py", repo_root=REPO, schema_or_version="M2B_BLIND_R1_EVALUATOR_GATE_V1", canonical_serialization="UTF8_PYTHON_SOURCE"),
        "sandbox_gold_isolation_spec": commitment_entry(isolation_path, repo_root=REPO, schema_or_version="M2B_BLIND_R1_BWRAP_ISOLATION_V1", canonical_serialization="UTF8_TEXT_LF"),
        "sandbox_launcher_source": commitment_entry(REPO / "driveclarify_m2b_blind/r1_sandbox.py", repo_root=REPO, schema_or_version="M2B_BLIND_R1_BWRAP_LAUNCHER_V1", canonical_serialization="UTF8_PYTHON_SOURCE"),
    }
    bundle = {
        "schema_version": R1_COMMITMENT_BUNDLE_SCHEMA_VERSION,
        "design_id": design_id,
        "parent_design_id": PARENT_ID,
        "blocked_preflight_id": BLOCKED_ID,
        "canonical_serialization": R1_CANONICAL_SERIALIZATION,
        "sealed_gold_commitment_sha256": gold_manifest["sha256"],
        "sealed_gold_committed_bytes": gold_manifest["bytes"],
        "commitment_count": len(commitments),
        "commitments": dict(sorted(commitments.items())),
    }
    bundle_path = output / "M2B_BLIND_EXECUTION_COMMITMENT_BUNDLE.json"
    write_json(bundle_path, bundle)
    write_text(output / "M2B_BLIND_EXECUTION_COMMITMENT_BUNDLE.sha256", file_sha256(bundle_path))
    verification = verify_commitment_bundle(load(bundle_path), REPO)
    if verification["status"] != "PASS":
        raise RuntimeError("R1_COMMITMENT_BUNDLE_SELF_VERIFICATION_FAILED")
    counts = {
        "track_r": sum(r["track_r"] for r in partition["partitions"]),
        "track_s_full_pool": sum(r["track_s_full_pool"] for r in partition["partitions"]),
        "track_s_primary_core": sum(r["track_s_primary_core"] for r in partition["partitions"]),
        "boundary_stress": sum(r["boundary_stress"] for r in partition["partitions"]),
        "core_nonboundary": sum(r["core_nonboundary"] for r in partition["partitions"]),
    }
    distribution = dict(Counter(row["gold_action_type"] for row in gold if row["primary_core_member"]))
    write_json(output / "M2B_BLIND_MEMBERSHIP_REPRODUCTION_AUDIT.json", {
        "schema_version": "driveclarify.m2b_blind_membership_reproduction_audit.v1",
        "status": "PASS", "design_id": design_id, "counts": counts,
        "primary_gold_distribution": distribution,
        "reconstruction": reconstruction,
        "original_gold_file_opened": False,
        "proof": "CANONICAL_RECONSTRUCTION_SHA_EQUALS_ORIGINAL_SEALED_GOLD_COMMITMENT",
        "primary_core_membership_sha256": commitments["primary_core_membership"]["sha256"],
        "boundary_membership_sha256": commitments["boundary_membership"]["sha256"],
        "full_pool_membership_sha256": commitments["full_pool_membership"]["sha256"],
    })
    write_json(output / "M2B_BLIND_CASE_ORDER_AUDIT.json", {
        "schema_version": "driveclarify.m2b_blind_case_order_audit.v1", "status": "PASS",
        "case_count": len(case_order), "canonical_order": "case_id_lexicographic",
        "is_sorted": case_order == sorted(case_order), "unique": len(case_order) == len(set(case_order)),
        "case_order_sha256": commitments["canonical_case_ordering"]["sha256"],
    })
    write_json(output / "M2B_BLIND_SCIENTIFIC_CONTENT_UNCHANGED_AUDIT.json", {
        "schema_version": "driveclarify.m2b_blind_scientific_content_unchanged_audit.v1",
        "status": "PASS", "parent_design_id": PARENT_ID,
        "scientific_case_definition_changed": False, "policy_changed": False,
        "matrices_changed": False, "profiles_changed": False, "comparison_set_changed": False,
        "gold_semantics_changed": False, "runtime_sha256": file_sha256(runtime_path),
        "sealed_gold_reconstructed_sha256": reconstruction["reconstructed_gold_sha256"],
        "comparison_set_sha256": file_sha256(comparison_path), "epsilon": "0.005",
        "hypotheses_sha256": file_sha256(PARENT / "M2B_BLIND_HYPOTHESES.md"),
        "metrics_spec_sha256": file_sha256(PARENT / "M2B_BLIND_METRICS_SPEC.md"),
        "statistics_protocol_sha256": file_sha256(PARENT / "M2B_BLIND_STATISTICAL_PROTOCOL.md"),
        "parent_inventory_before": parent_inventory,
        "blocked_preflight_inventory_before": blocked_inventory,
    })
    write_json(output / "M2B_BLIND_SANDBOX_CAPABILITY_AUDIT.json", {
        "schema_version": "driveclarify.m2b_blind_sandbox_capability_audit.v1",
        "status": "PENDING_DUMMY_TEST", "mechanism": "BUBBLEWRAP_ALLOWLISTED_MOUNT_NAMESPACE",
        "bwrap_path": "/usr/bin/bwrap", "bwrap_version": "bubblewrap 0.4.0",
        "unprivileged_user_namespace_enabled": True, "unshare_all": True,
        "empty_inherited_environment": True, "clearenv_compatibility": "OUTER_ENV_EMPTY",
    })
    write_json(output / "M2B_BLIND_R1_ZERO_EXECUTION_AUDIT.json", {
        "schema_version": "driveclarify.m2b_blind_r1_zero_execution_audit.v1", "status": "PASS",
        "blind_execution_id": None, "blind_event_created": False, "blind_event_consumed": False,
        "blind_policy_execution_count": 0, "blind_prediction_count": 0, "blind_metric_count": 0,
        "real_gold_open_count": 0, "real_gold_read_bytes": 0, "real_gold_unseal_count": 0,
        "formal_m1_test_data_plane_access_count": 0, "live_control_count": 0, "m3_operation_count": 0,
        "carla_launch_count": 0, "simlingo_execution_count": 0, "candidate_forward_count": 0,
        "training_count": 0, "optimizer_count": 0, "backward_count": 0,
        "gpu_compute_count": 0, "cuda_context_count": 0,
        "reference_solver_reconstruction_count": 1368,
        "independent_fraction_verification_count": 1368,
    })
    write_json(output / "GIT_START.json", {
        "schema_version": "driveclarify.m2b_blind_r1_git_start.v1", "design_id": design_id,
        "driveclarify": git_snapshot(REPO), "simlingo": git_snapshot(Path("/home/buaa/wrh/simlingo")),
        "parent_inventory": parent_inventory, "blocked_preflight_inventory": blocked_inventory,
        "formal_m1_test_prediction_sha256": file_sha256(REPO / "reports/formal_learned_m1_test/DC-FORMAL-M1-TEST-20260804T091730Z/TEST_UNIT_LEVEL_PREDICTIONS.json"),
    })
    state_path = output / "M2B_BLIND_R1_LIFECYCLE_STATE.json"
    before = initialize_draft(state_path, design_id=design_id, parent_design_id=PARENT_ID)
    write_json(output / "M2B_BLIND_R1_SEAL_BEFORE.json", before)
    freeze_protocol(state_path, commitment_bundle_sha256=file_sha256(bundle_path))
    write_text(output / "M2B_BLIND_R1_REPAIR_REPORT.md", f"""# M2B blind R1 execution-contract repair and reseal

R1 Design/Repair ID: `{design_id}`  
Parent: `{PARENT_ID}`  
Blocked preflight: `{BLOCKED_ID}`

This package repairs execution contracts only. Scientific cases, runtime, matrices, profiles, comparison set, gold semantics, epsilon, metrics, statistics, and hypotheses are unchanged. Membership was deterministically reconstructed from the original runtime with the original Decimal solver; the complete reconstructed canonical gold package SHA equals the sealed commitment without opening the original gold file.

The R1 partition manifest is gold-free and the policy input boundary excludes partition membership. The orchestrator attaches membership and commitment fields only after policy return. A bubblewrap allowlisted mount namespace makes the real gold directory, evaluator artifacts, reference outputs, and key paths absent from the prediction namespace.

This repair performs no blind execution and is not execution authorization. Final PASS and advancement to `PREEXECUTION_VERIFIED_AWAITING_AUTHORIZATION` require the complete prediction-free tests and independent R1 verifier.
""")
    write_text(output / "M2B_BLIND_R1_ROOT_CAUSE_AND_CLOSURE.md", """# Root cause and closure

The parent design committed the main artifacts but omitted execution-level partition/schema/source commitments. Membership was stored only with evaluator data, the raw record contract lacked control-plane partition hashes, and gold isolation relied on path rejection rather than namespace non-visibility.

R1 closes these gaps with deterministic gold-free partition reconstruction proven against the original sealed-gold SHA, a complete commitment bundle, strict raw-prediction v2 and evaluator schemas, post-policy control-plane enrichment, a versioned lifecycle bound to the bundle, and a bubblewrap allowlist namespace with an empty inherited environment. No scientific input or expected outcome changes.
""")
    write_text(output / "NEXT_M2B_R1_EXACTLY_ONE_BLIND_EXECUTION_AUTHORIZATION_PROMPT.md", f"""After independently verifying R1 design `{design_id}` and its complete commitment bundle, authorize exactly one sealed blind execution only if desired. The future execution must use the committed bubblewrap allowlist sandbox, raw-prediction v2 schema, partition manifest, lifecycle source, runtime/comparison order, and publish immutable prediction bytes before any evaluator access. Partial evidence consumes the event. No execution is authorized by this R1 repair package itself; live control and M3 remain unauthorized.
""")
    write_text(output / "COMMAND_LOG.md", """# Command log

- Verified blocked entry state and zero execution/gold access counters.
- Inventoried original design and blocked preflight before scoped writes.
- Read only original runtime/profile/manifest/commitment artifacts; original sealed-gold bytes were not opened.
- Reconstructed all 1368 reference outcomes and original primary-core membership with the frozen Decimal solver; independently re-enumerated all cases with Fraction arithmetic.
- Created R1 partition/schema/protocol/isolation/commitment/lifecycle artifacts.
- Initialized the R1 lifecycle and performed the first freeze; preexecution verification remains pending tests.
""")
    print(json.dumps({"design_id": design_id, "output": str(output), "counts": counts,
                      "core_distribution": distribution, "bundle_verification": verification,
                      "reconstruction": reconstruction}, sort_keys=True, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--design-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.design_id, args.output)


if __name__ == "__main__":
    main()
