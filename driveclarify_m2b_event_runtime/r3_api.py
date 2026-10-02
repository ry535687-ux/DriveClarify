"""R3 formal blind-event state machine and immutable publication API.

This control-plane module is never mounted in the prediction namespace.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .publication import canonical_bytes, file_sha256, replace_control_state, write_once


SCHEMA_VERSION = "driveclarify.m2b_blind_event.r3"
PREEXECUTION = "PREEXECUTION_VERIFIED_AWAITING_AUTHORIZATION"
EVENT_CREATED = "BLIND_EVENT_CREATED_PREDICTION_PENDING"
PREDICTION_STARTED = "BLIND_PREDICTION_STARTED_EVENT_CONSUMED"
PREDICTIONS_STAGED = "BLIND_PREDICTIONS_PUBLISHED_PENDING_IMMUTABILITY"
PREDICTIONS_IMMUTABLE = "BLIND_PREDICTIONS_RECORDED_IMMUTABLE"
RESULTS_STAGED = "BLIND_RESULTS_PUBLISHED_PENDING_IMMUTABILITY"
RESULTS_IMMUTABLE = "BLIND_RESULTS_PUBLISHED_IMMUTABLE"

HASH_FIELDS = (
    "commitment_bundle_sha256",
    "prediction_runtime_tree_sha256",
    "prediction_import_graph_sha256",
    "runtime_package_sha256",
    "partition_manifest_sha256",
    "case_order_sha256",
    "gold_commitment_sha256",
    "comparison_set_sha256",
    "prediction_schema_sha256",
    "production_mount_manifest_sha256",
    "git_identity_sha256",
    "authorization_receipt_sha256",
)
COUNT_FIELDS = (
    "expected_case_count",
    "expected_runtime_comparison_count",
    "expected_record_count",
)
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_config(config: Mapping[str, Any]) -> dict[str, Any]:
    expected = {"design_id", "parent_r2_id", *HASH_FIELDS, *COUNT_FIELDS}
    if set(config) != expected:
        raise ValueError("R3_EVENT_CONFIG_FIELDS_INVALID")
    normalized = dict(config)
    if not normalized["design_id"] or not normalized["parent_r2_id"]:
        raise ValueError("R3_EVENT_CONFIG_DESIGN_ID_INVALID")
    for field in HASH_FIELDS:
        if not isinstance(normalized[field], str) or not _SHA_RE.fullmatch(normalized[field]):
            raise ValueError(f"R3_EVENT_CONFIG_HASH_INVALID:{field}")
    for field in COUNT_FIELDS:
        if not isinstance(normalized[field], int) or isinstance(normalized[field], bool) or normalized[field] <= 0:
            raise ValueError(f"R3_EVENT_CONFIG_COUNT_INVALID:{field}")
    if normalized["expected_case_count"] * normalized["expected_runtime_comparison_count"] != normalized["expected_record_count"]:
        raise ValueError("R3_EVENT_CONFIG_EXPECTED_COUNT_PRODUCT_INVALID")
    return normalized


def _append_audit(path: Path, action: str, before: str, after: str, detail: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "schema_version": "driveclarify.m2b_blind_event_audit.r3",
        "sequence": 1,
        "timestamp_utc": _utc_now(),
        "action": action,
        "state_before": before,
        "state_after": after,
        "detail": dict(detail),
    }
    if path.exists():
        rows = read_audit(path)
        row["sequence"] = len(rows) + 1
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        payload = canonical_bytes(row)
        written = 0
        while written < len(payload):
            written += os.write(fd, payload[written:])
        os.fsync(fd)
    finally:
        os.close(fd)
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def initialize_state_once(state_path: Path, *, design_id: str, parent_r2_id: str,
                          blocked_r2_preflight_id: str, commitment_bundle_sha256: str) -> dict[str, Any]:
    if not _SHA_RE.fullmatch(commitment_bundle_sha256):
        raise ValueError("R3_COMMITMENT_SHA256_INVALID")
    state = {
        "schema_version": SCHEMA_VERSION,
        "design_id": design_id,
        "parent_r2_id": parent_r2_id,
        "blocked_r2_preflight_id": blocked_r2_preflight_id,
        "state": PREEXECUTION,
        "commitment_bundle_sha256": commitment_bundle_sha256,
        "blind_execution_id": None,
        "prediction_event_id": None,
        "formal_event_created": False,
        "event_create_count": 0,
        "event_consumed": False,
        "prediction_started": False,
        "prediction_start_count": 0,
        "prediction_completeness": "NONE",
        "prediction_record_count": 0,
        "prediction_bytes": 0,
        "prediction_sha256": None,
        "prediction_publication_count": 0,
        "predictions_immutable_count": 0,
        "gold_unseal_authorized": False,
        "gold_authorization_count": 0,
        "gold_open_count": 0,
        "gold_read_bytes": 0,
        "gold_semantic_access_count": 0,
        "gold_unseal_count": 0,
        "evaluator_start_count": 0,
        "metric_count": 0,
        "result_bytes": 0,
        "result_sha256": None,
        "result_publication_count": 0,
        "results_immutable_count": 0,
        "formal_m1_test_access_count": 0,
        "live_control_count": 0,
        "m3_operation_count": 0,
        "gpu_compute_count": 0,
        "cuda_context_count": 0,
    }
    write_once(state_path, canonical_bytes(state), mode=0o600)
    return state


def create_event(state_path: Path, event_manifest_path: Path, tombstone_path: Path,
                 audit_path: Path, *, blind_execution_id: str, prediction_event_id: str,
                 config: Mapping[str, Any], explicit_authorization: bool,
                 dummy_fixture: bool = False) -> dict[str, Any]:
    state = _load(state_path)
    if state["state"] != PREEXECUTION or state["formal_event_created"] or state["event_create_count"]:
        raise RuntimeError("R3_EVENT_CREATE_INVALID_OR_DUPLICATE")
    if event_manifest_path.exists() or tombstone_path.exists():
        raise RuntimeError("R3_EVENT_TOMBSTONE_OR_MANIFEST_ALREADY_EXISTS")
    if not explicit_authorization and not dummy_fixture:
        raise PermissionError("R3_FORMAL_EVENT_NOT_AUTHORIZED")
    if not blind_execution_id or not prediction_event_id:
        raise ValueError("R3_EVENT_IDENTITY_INCOMPLETE")
    normalized = _validate_config(config)
    if normalized["design_id"] != state["design_id"] or normalized["parent_r2_id"] != state["parent_r2_id"]:
        raise ValueError("R3_EVENT_CONFIG_DESIGN_MISMATCH")
    if normalized["commitment_bundle_sha256"] != state["commitment_bundle_sha256"]:
        raise ValueError("R3_EVENT_COMMITMENT_MISMATCH")
    manifest = {
        "schema_version": "driveclarify.m2b_blind_event_manifest.r3",
        "blind_execution_id": blind_execution_id,
        "prediction_event_id": prediction_event_id,
        "created_at_utc": _utc_now(),
        "dummy_fixture": dummy_fixture,
        "config": normalized,
        "config_sha256": _sha256(normalized),
    }
    write_once(event_manifest_path, canonical_bytes(manifest))
    write_once(tombstone_path, canonical_bytes({
        "schema_version": "driveclarify.m2b_blind_event_tombstone.r3",
        "blind_execution_id": blind_execution_id,
        "event_manifest_sha256": file_sha256(event_manifest_path),
    }))
    before = state["state"]
    state.update({
        "state": EVENT_CREATED,
        "blind_execution_id": blind_execution_id,
        "prediction_event_id": prediction_event_id,
        "formal_event_created": True,
        "event_create_count": 1,
        "event_manifest_sha256": file_sha256(event_manifest_path),
        "event_config_sha256": manifest["config_sha256"],
        "event_config": normalized,
        "expected_gold_commitment_sha256": normalized["gold_commitment_sha256"],
    })
    replace_control_state(state_path, state)
    _append_audit(audit_path, "create_event", before, state["state"], {
        "blind_execution_id": blind_execution_id,
        "event_manifest_sha256": state["event_manifest_sha256"],
    })
    return state


def mark_prediction_started(state_path: Path, event_manifest_path: Path, audit_path: Path,
                            *, config: Mapping[str, Any]) -> dict[str, Any]:
    state = _load(state_path)
    if state["state"] != EVENT_CREATED or not state["formal_event_created"]:
        raise RuntimeError("R3_PREDICTION_START_INVALID_OR_DUPLICATE")
    if state["event_consumed"] or state["prediction_started"] or state["prediction_start_count"]:
        raise RuntimeError("R3_PREDICTION_ALREADY_STARTED_CONSUMED")
    manifest = _load(event_manifest_path)
    normalized = _validate_config(config)
    if _sha256(normalized) != manifest["config_sha256"] or normalized != manifest["config"]:
        raise RuntimeError("R3_CHANGED_CONFIG_RESUME_FORBIDDEN")
    before = state["state"]
    state.update({
        "state": PREDICTION_STARTED,
        "event_consumed": True,
        "prediction_started": True,
        "prediction_start_count": 1,
        "prediction_started_at_utc": _utc_now(),
        "started_config_sha256": manifest["config_sha256"],
    })
    replace_control_state(state_path, state)
    _append_audit(audit_path, "mark_prediction_started", before, state["state"], {
        "event_consumed": True,
        "config_sha256": manifest["config_sha256"],
    })
    return state


def publish_predictions(state_path: Path, audit_path: Path, immutable_prediction_path: Path,
                        payload: bytes, *, completeness: str, record_count: int,
                        expected_record_count: int) -> dict[str, Any]:
    state = _load(state_path)
    if state["state"] != PREDICTION_STARTED or not state["event_consumed"]:
        raise RuntimeError("R3_PREDICTION_PUBLICATION_START_GATE_FAILED")
    if state["prediction_publication_count"] or immutable_prediction_path.exists():
        raise RuntimeError("R3_DUPLICATE_PREDICTION_PUBLICATION_FORBIDDEN")
    if completeness not in {"COMPLETE", "PARTIAL"}:
        raise ValueError("R3_PREDICTION_COMPLETENESS_INVALID")
    if not isinstance(record_count, int) or record_count < 0:
        raise ValueError("R3_PREDICTION_RECORD_COUNT_INVALID")
    if completeness == "COMPLETE" and record_count != expected_record_count:
        raise ValueError("R3_COMPLETE_RECORD_COUNT_MISMATCH")
    if completeness == "PARTIAL" and record_count >= expected_record_count:
        raise ValueError("R3_PARTIAL_RECORD_COUNT_INVALID")
    write_once(immutable_prediction_path, payload)
    before = state["state"]
    state.update({
        "state": PREDICTIONS_STAGED,
        "prediction_completeness": completeness,
        "prediction_record_count": record_count,
        "prediction_bytes": immutable_prediction_path.stat().st_size,
        "prediction_sha256": file_sha256(immutable_prediction_path),
        "prediction_publication_count": 1,
        "prediction_published_at_utc": _utc_now(),
    })
    replace_control_state(state_path, state)
    _append_audit(audit_path, "publish_predictions", before, state["state"], {
        "completeness": completeness,
        "record_count": record_count,
        "prediction_sha256": state["prediction_sha256"],
        "prediction_bytes": state["prediction_bytes"],
    })
    return state


def mark_predictions_immutable(state_path: Path, audit_path: Path,
                               immutable_prediction_path: Path) -> dict[str, Any]:
    state = _load(state_path)
    if state["state"] != PREDICTIONS_STAGED or state["predictions_immutable_count"]:
        raise RuntimeError("R3_PREDICTION_IMMUTABILITY_TRANSITION_INVALID")
    if not immutable_prediction_path.is_file() or file_sha256(immutable_prediction_path) != state["prediction_sha256"]:
        raise RuntimeError("R3_PREDICTION_EVIDENCE_MISSING_OR_CHANGED")
    before = state["state"]
    state.update({"state": PREDICTIONS_IMMUTABLE, "predictions_immutable_count": 1,
                  "predictions_immutable_at_utc": _utc_now()})
    replace_control_state(state_path, state)
    _append_audit(audit_path, "mark_predictions_immutable", before, state["state"], {
        "prediction_sha256": state["prediction_sha256"],
    })
    return state


def authorize_gold_unseal(state_path: Path, audit_path: Path,
                          immutable_prediction_path: Path) -> dict[str, Any]:
    state = _load(state_path)
    if state["state"] != PREDICTIONS_IMMUTABLE or state["prediction_completeness"] != "COMPLETE":
        raise PermissionError("R3_GOLD_UNSEAL_PREDICTION_GATE_FAILED")
    if state["gold_unseal_authorized"] or state["gold_authorization_count"]:
        raise RuntimeError("R3_DUPLICATE_GOLD_AUTHORIZATION")
    if file_sha256(immutable_prediction_path) != state["prediction_sha256"]:
        raise PermissionError("R3_GOLD_UNSEAL_PREDICTION_SHA_MISMATCH")
    state.update({"gold_unseal_authorized": True, "gold_authorization_count": 1,
                  "evaluator_start_count": 1, "gold_authorized_at_utc": _utc_now()})
    replace_control_state(state_path, state)
    _append_audit(audit_path, "authorize_gold_unseal", state["state"], state["state"], {
        "prediction_sha256": state["prediction_sha256"],
    })
    return state


def record_gold_unseal(state_path: Path, audit_path: Path, *, gold_sha256: str,
                       gold_read_bytes: int) -> dict[str, Any]:
    state = _load(state_path)
    if state["state"] != PREDICTIONS_IMMUTABLE or not state["gold_unseal_authorized"]:
        raise PermissionError("R3_GOLD_UNSEAL_NOT_AUTHORIZED")
    if state["gold_unseal_count"] or state["gold_semantic_access_count"]:
        raise RuntimeError("R3_DUPLICATE_GOLD_UNSEAL_FORBIDDEN")
    event_manifest_gold = state.get("expected_gold_commitment_sha256")
    if event_manifest_gold is None:
        event_manifest_gold = state.get("gold_commitment_sha256")
    if event_manifest_gold is not None and gold_sha256 != event_manifest_gold:
        raise RuntimeError("R3_GOLD_COMMITMENT_MISMATCH")
    if not _SHA_RE.fullmatch(gold_sha256) or gold_read_bytes <= 0:
        raise ValueError("R3_GOLD_UNSEAL_EVIDENCE_INVALID")
    state.update({
        "gold_open_count": 1,
        "gold_read_bytes": gold_read_bytes,
        "gold_semantic_access_count": 1,
        "gold_unseal_count": 1,
        "gold_unsealed_sha256": gold_sha256,
        "gold_unsealed_at_utc": _utc_now(),
    })
    replace_control_state(state_path, state)
    _append_audit(audit_path, "record_gold_unseal", state["state"], state["state"], {
        "gold_sha256": gold_sha256,
        "gold_read_bytes": gold_read_bytes,
    })
    return state


def publish_results(state_path: Path, audit_path: Path, immutable_result_path: Path,
                    results: Mapping[str, Any]) -> dict[str, Any]:
    state = _load(state_path)
    if state["state"] != PREDICTIONS_IMMUTABLE or state["gold_unseal_count"] != 1:
        raise RuntimeError("R3_RESULT_PUBLICATION_EVALUATOR_GATE_FAILED")
    if state["result_publication_count"] or immutable_result_path.exists():
        raise RuntimeError("R3_DUPLICATE_RESULT_PUBLICATION_FORBIDDEN")
    payload = canonical_bytes(results)
    write_once(immutable_result_path, payload)
    before = state["state"]
    state.update({
        "state": RESULTS_STAGED,
        "result_publication_count": 1,
        "result_bytes": immutable_result_path.stat().st_size,
        "result_sha256": file_sha256(immutable_result_path),
        "metric_count": int(results.get("metric_count", 0)),
        "result_published_at_utc": _utc_now(),
    })
    replace_control_state(state_path, state)
    _append_audit(audit_path, "publish_results", before, state["state"], {
        "result_sha256": state["result_sha256"],
        "result_bytes": state["result_bytes"],
        "metric_count": state["metric_count"],
    })
    return state


def mark_results_immutable(state_path: Path, audit_path: Path,
                           immutable_result_path: Path) -> dict[str, Any]:
    state = _load(state_path)
    if state["state"] != RESULTS_STAGED or state["results_immutable_count"]:
        raise RuntimeError("R3_RESULT_IMMUTABILITY_TRANSITION_INVALID")
    if file_sha256(immutable_result_path) != state["result_sha256"]:
        raise RuntimeError("R3_RESULT_MISSING_OR_CHANGED")
    before = state["state"]
    state.update({"state": RESULTS_IMMUTABLE, "results_immutable_count": 1,
                  "results_immutable_at_utc": _utc_now()})
    replace_control_state(state_path, state)
    _append_audit(audit_path, "mark_results_immutable", before, state["state"], {
        "result_sha256": state["result_sha256"],
    })
    return state


def bind_expected_gold_commitment(state_path: Path, event_manifest_path: Path) -> dict[str, Any]:
    """Internal create-time binding helper used before prediction starts."""
    state = _load(state_path)
    if state["state"] != EVENT_CREATED or state["event_consumed"]:
        raise RuntimeError("R3_GOLD_COMMITMENT_BINDING_STATE_INVALID")
    manifest = _load(event_manifest_path)
    state["expected_gold_commitment_sha256"] = manifest["config"]["gold_commitment_sha256"]
    replace_control_state(state_path, state)
    return state


def get_event_status(state_path: Path) -> dict[str, Any]:
    return _load(state_path)


def read_audit(audit_path: Path) -> list[dict[str, Any]]:
    if not audit_path.exists():
        return []
    rows = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines() if line]
    if [row.get("sequence") for row in rows] != list(range(1, len(rows) + 1)):
        raise RuntimeError("R3_EVENT_AUDIT_SEQUENCE_INVALID")
    return rows
