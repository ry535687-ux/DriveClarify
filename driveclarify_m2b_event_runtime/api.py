"""Frozen formal Blind Event API with fail-closed crash states."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .publication import canonical_bytes, file_sha256, replace_control_state, write_once


VERIFIED = "PREEXECUTION_VERIFIED_AWAITING_AUTHORIZATION"
EVENT_CREATED = "BLIND_EVENT_CREATED_PREDICTION_PENDING"
PREDICTED = "BLIND_PREDICTIONS_RECORDED_IMMUTABLE"
PUBLISHED = "BLIND_RESULTS_PUBLISHED_IMMUTABLE"


def load_state(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def initialize_resealed_state_once(path: Path, *, design_id: str, parent_design_id: str,
                                   r1_design_id: str, commitment_bundle_sha256: str) -> dict[str, Any]:
    state = {
        "schema_version": "driveclarify.m2b_blind_r2_lifecycle.v1",
        "design_id": design_id, "parent_design_id": parent_design_id,
        "r1_design_id": r1_design_id, "state": VERIFIED,
        "commitment_bundle_sha256": commitment_bundle_sha256,
        "blind_execution_id": None, "prediction_event_id": None,
        "formal_blind_event_created": False, "prediction_event_consumed": False,
        "prediction_completeness": "NONE", "prediction_sha256": None,
        "prediction_record_count": 0, "prediction_bytes": 0,
        "blind_policy_execution_count": 0, "comparison_execution_count": 0,
        "blind_prediction_count": 0, "blind_metric_count": 0,
        "gold_open_count": 0, "gold_read_bytes": 0,
        "gold_semantic_access_count": 0, "gold_unseal_count": 0,
        "evaluation_count": 0, "publication_count": 0,
    }
    write_once(path, canonical_bytes(state), mode=0o600)
    return state


def create_blind_event(state_path: Path, receipt_path: Path, *, blind_execution_id: str,
                       prediction_event_id: str, authorization_receipt_sha256: str,
                       explicit_authorization: bool, dummy_fixture: bool = False) -> dict[str, Any]:
    state = load_state(state_path)
    if state["state"] != VERIFIED or state["formal_blind_event_created"]:
        raise RuntimeError("R2_BLIND_EVENT_CREATE_INVALID_OR_DUPLICATE")
    if state["prediction_event_consumed"] or state["blind_execution_id"] is not None or receipt_path.exists():
        raise RuntimeError("R2_BLIND_EVENT_ALREADY_CONSUMED_OR_CRASH_RESIDUE")
    if not explicit_authorization and not dummy_fixture:
        raise PermissionError("R2_FORMAL_BLIND_EVENT_NOT_AUTHORIZED")
    if not blind_execution_id or not prediction_event_id or not authorization_receipt_sha256:
        raise ValueError("R2_BLIND_EVENT_IDENTITY_INCOMPLETE")
    receipt = {
        "schema_version": "driveclarify.m2b_blind_event_receipt.v1",
        "design_id": state["design_id"], "blind_execution_id": blind_execution_id,
        "prediction_event_id": prediction_event_id,
        "authorization_receipt_sha256": authorization_receipt_sha256,
        "dummy_fixture": dummy_fixture,
    }
    write_once(receipt_path, canonical_bytes(receipt))
    state.update({
        "state": EVENT_CREATED, "blind_execution_id": blind_execution_id,
        "prediction_event_id": prediction_event_id, "formal_blind_event_created": True,
        "event_receipt_sha256": file_sha256(receipt_path),
    })
    replace_control_state(state_path, state)
    return state


def publish_first_prediction_evidence(state_path: Path, staged_envelope_path: Path,
                                      immutable_evidence_path: Path, *,
                                      dummy_fixture: bool = False) -> dict[str, Any]:
    """First valid complete or partial bytes consume the one-shot event permanently."""
    state = load_state(state_path)
    if state["state"] != EVENT_CREATED or not state["formal_blind_event_created"]:
        raise RuntimeError("R2_PREDICTION_PUBLICATION_EVENT_NOT_CREATED")
    if state["prediction_event_consumed"] or immutable_evidence_path.exists():
        raise RuntimeError("R2_DUPLICATE_PREDICTION_EVIDENCE_FORBIDDEN")
    envelope = json.loads(staged_envelope_path.read_text(encoding="utf-8"))
    # Lazy import is control-plane validation only; this package is absent from prediction mounts.
    from driveclarify_m2b_prediction_runtime.contracts import validate_prediction_envelope
    validate_prediction_envelope(envelope)
    if envelope["design_id"] != state["design_id"] or envelope["prediction_event_id"] != state["prediction_event_id"]:
        raise ValueError("R2_PREDICTION_EVIDENCE_EVENT_IDENTITY_MISMATCH")
    payload = staged_envelope_path.read_bytes()
    if payload != canonical_bytes(envelope):
        raise ValueError("R2_PREDICTION_EVIDENCE_NOT_CANONICAL")
    write_once(immutable_evidence_path, payload)
    record_count = int(envelope["record_count"])
    state.update({
        "state": PREDICTED, "prediction_event_consumed": True,
        "prediction_completeness": envelope["completeness"],
        "prediction_sha256": file_sha256(immutable_evidence_path),
        "prediction_record_count": record_count,
        "prediction_bytes": immutable_evidence_path.stat().st_size,
        "blind_policy_execution_count": 0 if dummy_fixture else 1,
        "comparison_execution_count": 0 if dummy_fixture else record_count,
        "blind_prediction_count": 0 if dummy_fixture else record_count,
    })
    replace_control_state(state_path, state)
    return state


def assert_evaluator_allowed(state_path: Path, immutable_evidence_path: Path) -> dict[str, Any]:
    state = load_state(state_path)
    if state["state"] != PREDICTED or not state["prediction_event_consumed"]:
        raise PermissionError("R2_EVALUATOR_BEFORE_FIRST_PREDICTION_EVIDENCE")
    if state["prediction_completeness"] not in {"COMPLETE", "PARTIAL_CONSUMED"}:
        raise PermissionError("R2_EVALUATOR_PREDICTION_COMPLETENESS_INVALID")
    if not immutable_evidence_path.is_file() or file_sha256(immutable_evidence_path) != state["prediction_sha256"]:
        raise PermissionError("R2_EVALUATOR_PREDICTION_EVIDENCE_MISSING_OR_CHANGED")
    return state


def publish_results_once(state_path: Path, immutable_evidence_path: Path, result_path: Path,
                         results: Mapping[str, Any], *, dummy_fixture: bool = False) -> dict[str, Any]:
    current = load_state(state_path)
    if current["evaluation_count"] or current["publication_count"] or result_path.exists():
        raise RuntimeError("R2_DUPLICATE_EVALUATION_OR_PUBLICATION")
    state = assert_evaluator_allowed(state_path, immutable_evidence_path)
    if state["prediction_completeness"] != "COMPLETE":
        raise RuntimeError("R2_PARTIAL_EVENT_CANNOT_PUBLISH_METRICS")
    write_once(result_path, canonical_bytes(results))
    state.update({
        "state": PUBLISHED, "evaluation_count": 1, "publication_count": 1,
        "result_sha256": file_sha256(result_path),
        "blind_metric_count": 0 if dummy_fixture else int(results.get("metric_count", 0)),
    })
    replace_control_state(state_path, state)
    return state
