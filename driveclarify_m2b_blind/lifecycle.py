"""One-shot evidence publication and gold-unseal lifecycle."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .contracts import atomic_replace, atomic_write_once, canonical_bytes, file_sha256, validate_prediction_record


DRAFT = "PROTOCOL_DRAFT"
FROZEN = "PROTOCOL_FROZEN_BLIND_NOT_AUTHORIZED"
VERIFIED = "PREEXECUTION_VERIFIED_AWAITING_AUTHORIZATION"
PREDICTED = "BLIND_PREDICTIONS_RECORDED_IMMUTABLE"
PUBLISHED = "BLIND_RESULTS_PUBLISHED_IMMUTABLE"


def load_state(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def initialize_frozen(path: Path, *, design_id: str, commitments: Mapping[str, str]) -> dict[str, Any]:
    state = {
        "schema_version": "driveclarify.m2b_blind_lifecycle.v1", "design_id": design_id,
        "state": FROZEN, "commitments": dict(sorted(commitments.items())),
        "prediction_event_consumed": False, "prediction_completeness": "NONE",
        "prediction_sha256": None, "evaluation_count": 0, "publication_count": 0,
        "blind_policy_execution_count": 0, "blind_prediction_count": 0, "blind_metric_count": 0,
    }
    atomic_write_once(path, canonical_bytes(state))
    return state


def mark_preexecution_verified(path: Path, evidence: Mapping[str, Any]) -> dict[str, Any]:
    state = load_state(path)
    if state["state"] != FROZEN:
        raise RuntimeError("PREEXECUTION_VERIFICATION_INVALID_STATE")
    if evidence.get("prediction_free") is not True or evidence.get("gold_solver_verifier_agreement") is not True:
        raise RuntimeError("PREEXECUTION_EVIDENCE_INCOMPLETE")
    state["state"] = VERIFIED
    state["preexecution_evidence"] = dict(evidence)
    atomic_replace(path, canonical_bytes(state))
    return state


def publish_raw_predictions(
    state_path: Path, evidence_path: Path, records: list[Mapping[str, Any]], expected_case_ids: set[Any],
    *, explicit_authorization: bool, dummy_fixture: bool = False,
) -> dict[str, Any]:
    state = load_state(state_path)
    if state["state"] != VERIFIED or state["prediction_event_consumed"] or evidence_path.exists():
        raise RuntimeError("DUPLICATE_OR_INVALID_PREDICTION_EVENT")
    if not explicit_authorization and not dummy_fixture:
        raise PermissionError("BLIND_EVALUATION_NOT_AUTHORIZED")
    for record in records:
        validate_prediction_record(record)
    # Publication happens before completeness assessment: partial evidence consumes the event.
    payload = canonical_bytes(sorted((dict(r) for r in records), key=lambda x: (x["case_id"], x["comparison_id"])))
    atomic_write_once(evidence_path, payload)
    if expected_case_ids and all(isinstance(item, tuple) and len(item) == 2 for item in expected_case_ids):
        observed = {(str(r["case_id"]), str(r["comparison_id"])) for r in records}
    else:
        observed = {str(r["case_id"]) for r in records}
    completeness = "COMPLETE" if observed == expected_case_ids else "PARTIAL_CONSUMED"
    state.update({"state": PREDICTED, "prediction_event_consumed": True,
                  "prediction_completeness": completeness, "prediction_sha256": file_sha256(evidence_path),
                  "prediction_record_count": len(records),
                  "blind_policy_execution_count": 0 if dummy_fixture else 1,
                  "blind_prediction_count": 0 if dummy_fixture else len(records)})
    atomic_replace(state_path, canonical_bytes(state))
    return state


def assert_gold_may_be_read(state_path: Path) -> dict[str, Any]:
    state = load_state(state_path)
    if state["state"] != PREDICTED or not state["prediction_event_consumed"]:
        raise PermissionError("SEALED_GOLD_READ_BEFORE_PREDICTION_PUBLICATION")
    return state


def publish_results(state_path: Path, result_path: Path, results: Mapping[str, Any], *, dummy_fixture: bool = False) -> dict[str, Any]:
    current = load_state(state_path)
    if current["evaluation_count"] != 0 or current["publication_count"] != 0 or current["state"] == PUBLISHED or result_path.exists():
        raise RuntimeError("DUPLICATE_EVALUATION_OR_PUBLICATION")
    state = assert_gold_may_be_read(state_path)
    atomic_write_once(result_path, canonical_bytes(results))
    state.update({"state": PUBLISHED, "evaluation_count": 1, "publication_count": 1,
                  "result_sha256": file_sha256(result_path),
                  "blind_metric_count": 0 if dummy_fixture else int(results.get("metric_count", 0))})
    atomic_replace(state_path, canonical_bytes(state))
    return state
