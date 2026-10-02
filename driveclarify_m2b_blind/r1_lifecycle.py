"""R1 one-shot lifecycle bound to the complete execution commitment bundle."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .contracts import atomic_replace, atomic_write_once, canonical_bytes, file_sha256
from .r1_contracts import validate_prediction_envelope


DRAFT = "PROTOCOL_DRAFT"
FROZEN = "PROTOCOL_FROZEN_BLIND_NOT_AUTHORIZED"
VERIFIED = "PREEXECUTION_VERIFIED_AWAITING_AUTHORIZATION"
PREDICTED = "BLIND_PREDICTIONS_RECORDED_IMMUTABLE"
PUBLISHED = "BLIND_RESULTS_PUBLISHED_IMMUTABLE"


def load_state(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def initialize_draft(path: Path, *, design_id: str, parent_design_id: str) -> dict[str, Any]:
    state = {
        "schema_version": "driveclarify.m2b_blind_r1_lifecycle.v1",
        "design_id": design_id,
        "parent_design_id": parent_design_id,
        "state": DRAFT,
        "commitment_bundle_sha256": None,
        "prediction_event_consumed": False,
        "prediction_completeness": "NONE",
        "prediction_sha256": None,
        "prediction_record_count": 0,
        "blind_policy_execution_count": 0,
        "blind_prediction_count": 0,
        "blind_metric_count": 0,
        "gold_unseal_count": 0,
        "evaluation_count": 0,
        "publication_count": 0,
    }
    atomic_write_once(path, canonical_bytes(state))
    return state


def freeze_protocol(path: Path, *, commitment_bundle_sha256: str) -> dict[str, Any]:
    state = load_state(path)
    if state["state"] != DRAFT or state["commitment_bundle_sha256"] is not None:
        raise RuntimeError("R1_FREEZE_INVALID_OR_DUPLICATE")
    state["state"] = FROZEN
    state["commitment_bundle_sha256"] = commitment_bundle_sha256
    atomic_replace(path, canonical_bytes(state))
    return state


def mark_preexecution_verified(path: Path, *, bundle_sha256: str,
                               bundle_verification_pass: bool,
                               sandbox_verification_pass: bool,
                               prediction_free: bool) -> dict[str, Any]:
    state = load_state(path)
    if state["state"] != FROZEN:
        raise RuntimeError("R1_PREEXECUTION_VERIFY_INVALID_OR_DUPLICATE")
    if bundle_sha256 != state["commitment_bundle_sha256"]:
        raise RuntimeError("R1_PREEXECUTION_BUNDLE_SHA_MISMATCH")
    if not (bundle_verification_pass and sandbox_verification_pass and prediction_free):
        raise RuntimeError("R1_PREEXECUTION_EVIDENCE_INCOMPLETE")
    state["state"] = VERIFIED
    state["preexecution_evidence"] = {
        "bundle_verification_pass": True,
        "sandbox_verification_pass": True,
        "prediction_free": True,
    }
    atomic_replace(path, canonical_bytes(state))
    return state


def publish_predictions(path: Path, evidence_path: Path, envelope: Mapping[str, Any], *,
                        explicit_authorization: bool, dummy_fixture: bool = False) -> dict[str, Any]:
    state = load_state(path)
    if state["state"] != VERIFIED or state["prediction_event_consumed"] or evidence_path.exists():
        raise RuntimeError("R1_DUPLICATE_OR_INVALID_PREDICTION_EVENT")
    if not explicit_authorization and not dummy_fixture:
        raise PermissionError("R1_BLIND_EVALUATION_NOT_AUTHORIZED")
    validate_prediction_envelope(envelope)
    atomic_write_once(evidence_path, canonical_bytes(envelope))
    state.update({
        "state": PREDICTED,
        "prediction_event_consumed": True,
        "prediction_completeness": envelope["completeness"],
        "prediction_sha256": file_sha256(evidence_path),
        "prediction_record_count": envelope["record_count"],
        "blind_policy_execution_count": 0 if dummy_fixture else 1,
        "blind_prediction_count": 0 if dummy_fixture else envelope["record_count"],
    })
    atomic_replace(path, canonical_bytes(state))
    return state


def assert_evaluator_allowed(path: Path) -> dict[str, Any]:
    state = load_state(path)
    if state["state"] != PREDICTED or not state["prediction_event_consumed"]:
        raise PermissionError("R1_GOLD_READ_BEFORE_PREDICTION_PUBLICATION")
    if state["prediction_completeness"] not in {"COMPLETE", "PARTIAL_CONSUMED"}:
        raise PermissionError("R1_PREDICTION_COMPLETENESS_INVALID")
    return state


def publish_results(path: Path, result_path: Path, results: Mapping[str, Any], *,
                    dummy_fixture: bool = False) -> dict[str, Any]:
    state = assert_evaluator_allowed(path)
    if state["evaluation_count"] or state["publication_count"] or result_path.exists():
        raise RuntimeError("R1_DUPLICATE_EVALUATION_OR_PUBLICATION")
    atomic_write_once(result_path, canonical_bytes(results))
    state.update({
        "state": PUBLISHED,
        "evaluation_count": 1,
        "publication_count": 1,
        "result_sha256": file_sha256(result_path),
        "blind_metric_count": 0 if dummy_fixture else int(results.get("metric_count", 0)),
    })
    atomic_replace(path, canonical_bytes(state))
    return state
