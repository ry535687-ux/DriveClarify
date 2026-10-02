"""Complete gold-free one-shot driver with durable partial evidence."""

from __future__ import annotations

import json
import os
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable, Mapping

from .contracts import (
    RAW_PREDICTION_FIELDS, file_sha256, policy_input_leak_paths,
    validate_partition_manifest, validate_raw_prediction_record,
    validate_runtime_package,
)
from .staging import (
    append_record_fsync, create_journal_once, read_journal, seal_journal_read_only,
    write_envelope_once,
)


PolicyCallable = Callable[[Mapping[str, Any], str, str], Mapping[str, Any]]
StartMarker = Callable[[], None]

R3_CRASH_REASON_VOCABULARY = frozenset({
    "DUMMY_SIMULATED_INTERRUPT",
    "POLICY_OR_COMPARISON_FAILURE",
    "RAW_RECORD_VALIDATION_FAILURE",
    "RUNTIME_INPUT_VALIDATION_FAILURE",
    "STAGING_IO_FAILURE",
})


def _load_verified(path: Path, expected_sha256: str) -> dict[str, Any]:
    if file_sha256(path) != expected_sha256:
        raise ValueError(f"R2_INPUT_SHA256_MISMATCH:{path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


def attach_control_plane_fields(policy_record: Mapping[str, Any], partition: Mapping[str, Any], *,
                                comparison_id: str, comparison_index: int,
                                hashes: Mapping[str, str]) -> dict[str, Any]:
    values: dict[str, Any] = {
        "case_id": policy_record["case_id"],
        "canonical_case_index": partition["canonical_execution_index"],
        "track": partition["track"], "track_r": partition["track_r"],
        "track_s_full_pool": partition["track_s_full_pool"],
        "track_s_primary_core": partition["track_s_primary_core"],
        "boundary_stress": partition["boundary_stress"],
        "core_nonboundary": partition["core_nonboundary"],
        "matrix_archetype_id": partition["matrix_archetype_id"],
        "profile_id": partition["profile_id"], "comparison_id": comparison_id,
        "canonical_comparison_index": comparison_index,
        "selected_action": policy_record["selected_action"],
        "selected_candidate_id": policy_record.get("selected_candidate_id"),
        "act_subtype": policy_record["act_subtype"],
        "reason_codes": list(policy_record["decision_reason_codes"]),
        "legal_action_mask": dict(policy_record["legal_action_mask"]),
        "R_act_A": policy_record.get("r_act_a"), "R_act_B": policy_record.get("r_act_b"),
        "R_ask": policy_record.get("r_ask"), "R_wait": policy_record.get("r_wait"),
        "V_ask": policy_record.get("v_ask"), "V_wait": policy_record.get("v_wait"),
        "posterior_summary": policy_record.get("posterior_summary", []),
        "query_episode_state": policy_record["query_episode_state"],
        "matrix_sha256": policy_record["matrix_sha256"],
        "profile_sha256": policy_record["profile_sha256"],
        "partition_manifest_sha256": hashes["partition_manifest_sha256"],
        "protocol_sha256": hashes["protocol_sha256"],
        "runtime_package_sha256": hashes["runtime_package_sha256"],
        "comparison_set_sha256": hashes["comparison_set_sha256"],
        "prediction_schema_sha256": hashes["prediction_schema_sha256"],
        "authorization_eligible": False, "used_for_control": False,
        "control_authorized": False, "override_applied": False,
    }
    record = OrderedDict((name, values[name]) for name in RAW_PREDICTION_FIELDS)
    validate_raw_prediction_record(record)
    return dict(record)


def execute_one_shot(*, runtime_path: Path, partition_path: Path, comparison_set_path: Path,
                     hashes: Mapping[str, str], design_id: str, prediction_event_id: str,
                     journal_path: Path, envelope_path: Path, predictor: PolicyCallable) -> dict[str, Any]:
    """Run every case/comparison once; any ordinary failure seals available rows as partial."""
    if journal_path.exists() or envelope_path.exists():
        raise RuntimeError("R2_ONE_SHOT_EVIDENCE_ALREADY_EXISTS")
    runtime = _load_verified(runtime_path, hashes["runtime_package_sha256"])
    partition = _load_verified(partition_path, hashes["partition_manifest_sha256"])
    comparisons = _load_verified(comparison_set_path, hashes["comparison_set_sha256"])
    validate_runtime_package(runtime)
    validate_partition_manifest(partition)
    partition_by_id = {row["case_id"]: row for row in partition["partitions"]}
    if set(partition_by_id) != {case["case_id"] for case in runtime["cases"]}:
        raise ValueError("R2_RUNTIME_PARTITION_CASE_SET_MISMATCH")
    runtime_comparisons = [row["comparison_id"] for row in comparisons["comparisons"]
                           if not row.get("oracle_computed_after_prediction_seal")]
    if len(runtime_comparisons) != 15 or len(set(runtime_comparisons)) != 15:
        raise ValueError("R2_RUNTIME_COMPARISON_SET_INVALID")
    expected_count = len(runtime["cases"]) * len(runtime_comparisons)
    create_journal_once(journal_path)
    failure: Exception | None = None
    for case in runtime["cases"]:
        if policy_input_leak_paths(case):
            failure = ValueError("R2_POLICY_INPUT_CONTAINS_FORBIDDEN_FIELD")
            break
        partition_row = partition_by_id[case["case_id"]]
        for comparison_index, comparison_id in enumerate(runtime_comparisons):
            try:
                policy_record = predictor(dict(case), comparison_id, hashes["protocol_sha256"])
                if policy_record.get("case_id") != case["case_id"]:
                    raise ValueError("R2_POLICY_RECORD_CASE_ID_MISMATCH")
                record = attach_control_plane_fields(
                    policy_record, partition_row, comparison_id=comparison_id,
                    comparison_index=comparison_index, hashes=hashes,
                )
                append_record_fsync(journal_path, record)
            except Exception as exc:  # durable partial evidence is the fail-closed outcome
                failure = exc
                break
        if failure is not None:
            break
    records = read_journal(journal_path)
    seal_journal_read_only(journal_path)
    completeness = "COMPLETE" if failure is None and len(records) == expected_count else "PARTIAL_CONSUMED"
    envelope = OrderedDict((name, value) for name, value in (
        ("schema_version", "driveclarify.m2b_blind_raw_prediction.v2"),
        ("design_id", design_id), ("prediction_event_id", prediction_event_id),
        ("completeness", completeness), ("expected_record_count", expected_count),
        ("record_count", len(records)),
        ("canonical_record_order", ["canonical_case_index", "canonical_comparison_index"]),
        ("records", records),
    ))
    write_envelope_once(envelope_path, envelope)
    return {"envelope": dict(envelope), "failure": None if failure is None else {
        "type": type(failure).__name__, "message": str(failure),
    }}


def _write_r3_envelope_once(path: Path, envelope: Mapping[str, Any]) -> None:
    if path.exists():
        raise RuntimeError("R3_PREDICTION_ENVELOPE_ALREADY_EXISTS")
    records = envelope.get("records")
    if not isinstance(records, list) or envelope.get("record_count") != len(records):
        raise ValueError("R3_PREDICTION_ENVELOPE_COUNT_INVALID")
    if envelope.get("completeness") not in {"COMPLETE", "PARTIAL"}:
        raise ValueError("R3_PREDICTION_ENVELOPE_COMPLETENESS_INVALID")
    if envelope["completeness"] == "COMPLETE" and len(records) != envelope["expected_record_count"]:
        raise ValueError("R3_COMPLETE_PREDICTION_COUNT_INVALID")
    if envelope["completeness"] == "PARTIAL" and len(records) >= envelope["expected_record_count"]:
        raise ValueError("R3_PARTIAL_PREDICTION_COUNT_INVALID")
    for record in records:
        validate_raw_prediction_record(record)
    order = [(row["canonical_case_index"], row["canonical_comparison_index"]) for row in records]
    if order != sorted(order) or len(order) != len(set(order)):
        raise ValueError("R3_PREDICTION_ENVELOPE_ORDER_INVALID")
    payload = (json.dumps(dict(envelope), ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
        try:
            written = 0
            while written < len(payload):
                written += os.write(fd, payload[written:])
            os.fsync(fd)
        finally:
            os.close(fd)
        os.link(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def execute_r3_one_shot(*, runtime_path: Path, partition_path: Path,
                        comparison_set_path: Path, hashes: Mapping[str, str],
                        design_id: str, prediction_event_id: str,
                        journal_path: Path, envelope_path: Path,
                        predictor: PolicyCallable,
                        mark_prediction_started: StartMarker | None = None,
                        prediction_start_precommitted: bool = False,
                        interrupt_after_records: int | None = None) -> dict[str, Any]:
    """R3 complete/partial one-shot execution with first-record consumption gate.

    Production uses ``prediction_start_precommitted=True`` only after the host Event API
    has durably consumed the event. Dummy tests may supply ``mark_prediction_started``.
    No resume is permitted after either journal or envelope exists.
    """
    if journal_path.exists() or envelope_path.exists():
        raise RuntimeError("R3_ONE_SHOT_EVIDENCE_ALREADY_EXISTS_NO_RESUME")
    if (mark_prediction_started is None) == (not prediction_start_precommitted):
        raise ValueError("R3_PREDICTION_START_GATE_EXACTLY_ONE_MODE_REQUIRED")
    if interrupt_after_records is not None and interrupt_after_records <= 0:
        raise ValueError("R3_INTERRUPT_AFTER_RECORDS_INVALID")
    runtime = _load_verified(runtime_path, hashes["runtime_package_sha256"])
    partition = _load_verified(partition_path, hashes["partition_manifest_sha256"])
    comparisons = _load_verified(comparison_set_path, hashes["comparison_set_sha256"])
    validate_runtime_package(runtime)
    validate_partition_manifest(partition)
    partition_by_id = {row["case_id"]: row for row in partition["partitions"]}
    if set(partition_by_id) != {case["case_id"] for case in runtime["cases"]}:
        raise ValueError("R3_RUNTIME_PARTITION_CASE_SET_MISMATCH")
    runtime_comparisons = [row["comparison_id"] for row in comparisons["comparisons"]
                           if not row.get("oracle_computed_after_prediction_seal")]
    if not runtime_comparisons or len(set(runtime_comparisons)) != len(runtime_comparisons):
        raise ValueError("R3_RUNTIME_COMPARISON_SET_INVALID")
    expected_count = len(runtime["cases"]) * len(runtime_comparisons)
    create_journal_once(journal_path)
    failure: Exception | None = None
    crash_reason: str | None = None
    started = prediction_start_precommitted
    record_count = 0
    for case in runtime["cases"]:
        if policy_input_leak_paths(case):
            failure = ValueError("R3_POLICY_INPUT_CONTAINS_FORBIDDEN_FIELD")
            crash_reason = "RUNTIME_INPUT_VALIDATION_FAILURE"
            break
        partition_row = partition_by_id[case["case_id"]]
        for comparison_index, comparison_id in enumerate(runtime_comparisons):
            try:
                if not started:
                    assert mark_prediction_started is not None
                    mark_prediction_started()
                    started = True
                policy_record = predictor(dict(case), comparison_id, hashes["protocol_sha256"])
                if policy_record.get("case_id") != case["case_id"]:
                    raise ValueError("R3_POLICY_RECORD_CASE_ID_MISMATCH")
                record = attach_control_plane_fields(
                    policy_record, partition_row, comparison_id=comparison_id,
                    comparison_index=comparison_index, hashes=hashes,
                )
                append_record_fsync(journal_path, record)
                record_count += 1
                if interrupt_after_records == record_count:
                    raise InterruptedError("NONBLIND_DUMMY_INTERRUPT_AFTER_RECORD")
            except InterruptedError as exc:
                failure = exc
                crash_reason = "DUMMY_SIMULATED_INTERRUPT"
                break
            except (ValueError, TypeError, KeyError) as exc:
                failure = exc
                crash_reason = "RAW_RECORD_VALIDATION_FAILURE"
                break
            except OSError as exc:
                failure = exc
                crash_reason = "STAGING_IO_FAILURE"
                break
            except Exception as exc:  # fail closed and preserve all successful rows
                failure = exc
                crash_reason = "POLICY_OR_COMPARISON_FAILURE"
                break
        if failure is not None:
            break
    records = read_journal(journal_path)
    seal_journal_read_only(journal_path)
    completeness = "COMPLETE" if failure is None and len(records) == expected_count else "PARTIAL"
    if completeness == "PARTIAL" and crash_reason not in R3_CRASH_REASON_VOCABULARY:
        raise RuntimeError("R3_CRASH_REASON_OUTSIDE_VOCABULARY")
    envelope = OrderedDict((name, value) for name, value in (
        ("schema_version", "driveclarify.m2b_blind_raw_prediction.r3"),
        ("design_id", design_id),
        ("prediction_event_id", prediction_event_id),
        ("completeness", completeness),
        ("expected_record_count", expected_count),
        ("record_count", len(records)),
        ("canonical_record_order", ["canonical_case_index", "canonical_comparison_index"]),
        ("config_hashes", dict(sorted(hashes.items()))),
        ("crash_reason", crash_reason),
        ("records", records),
    ))
    _write_r3_envelope_once(envelope_path, envelope)
    return {"envelope": dict(envelope), "failure": None if failure is None else {
        "type": type(failure).__name__, "message": str(failure), "crash_reason": crash_reason,
    }}
