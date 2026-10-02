#!/usr/bin/env python3
"""Append-only finalization of interrupted formal TRAIN slot 39; never reruns it."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driveclarify_method_v1_r3_formal_train_execution import linked_retry  # noqa: E402
from driveclarify_method_v1_r3_formal_train_execution import orchestrator as base  # noqa: E402


ATTEMPT_ID = base.FAMILY_ID + "-0039-A01"
EPISODE_ID = base.FAMILY_ID + "-0039"


def main() -> int:
    ledger = base._load(base.LEDGER_PATH)
    attempt = ledger["attempt_records"][-1]
    if attempt["attempt_id"] != ATTEMPT_ID or base._attempt_state(attempt) != "RUNNING":
        raise SystemExit("SLOT39_NOT_EXACTLY_ONE_RUNNING_ATTEMPT")
    output = ROOT / attempt["artifact_dir"]
    audit_path = output / "stage6b_runtime_audit.json"
    frame_path = output / "stage6b_frame_trace.jsonl"
    probe_path = output / "probe/probe.jsonl"
    audit = base._load(audit_path)
    post = base.resource_snapshot()
    if post["status"] != "PASS":
        raise SystemExit("SLOT39_POST_INTERRUPTION_CLEANUP_NOT_PASS")
    base._atomic_json(output / "ORCHESTRATOR_POST_CLEANUP.json", post)
    frame_count = sum(1 for _ in frame_path.open("rb")) if frame_path.is_file() else 0
    probe_count = sum(1 for _ in probe_path.open("rb")) if probe_path.is_file() else 0
    compute = audit.get("compute", {})
    metric = {
        "schema_version": "driveclarify.method_v1_r3.formal_train.interrupted_metric_receipt.v1",
        "status": "BLOCKED_CONTRACT_DEFECT_REQUIRED_EVIDENCE",
        "episode_identity": {
            "episode_id": EPISODE_ID,
            "split": attempt["split"],
            "scenario_id": attempt["scenario_id"],
            "seed": attempt["seed"],
            "method_id": attempt["method_id"],
            "runtime_fixture_id": attempt["runtime_fixture_id"],
            "slot_index": attempt["global_slot_index"],
            "split_slot_index": attempt["split_slot_index"],
        },
        "failure_class": "EVIDENCE_ACCOUNTING_FAILURE_ORCHESTRATOR_SESSION_INTERRUPTED_AFTER_SCIENTIFIC_EXPOSURE",
        "scientific_exposure": {
            "verified_nonzero": True,
            "serialized_frame_trace_count": frame_count,
            "serialized_probe_trace_count": probe_count,
            "normal_model_forwards_observed_in_runtime_audit": compute.get("normal_model_forwards"),
            "candidate_model_forwards_observed_in_runtime_audit": compute.get("candidate_model_forwards"),
            "existing_pid_invocations_observed_in_runtime_audit": compute.get("existing_pid_invocations"),
            "decision_trace_count": len(audit.get("decision_trace", [])),
            "query_trace_count": len(audit.get("query_trace", [])),
            "wait_trace_count": len(audit.get("wait_trace", [])),
        },
        "evidence_chain": {
            "producer": audit_path.is_file(),
            "serialization": False,
            "ingest": False,
            "aggregation_input_identity_match": "UNKNOWN_NO_EPISODE_RESULT",
            "reducer": "NOT_RUN_MISSING_SERIALIZED_EPISODE_RESULT",
            "receipt": False,
        },
        "required_missing_artifacts": {
            "EPISODE_RESULT.json": not (output / "EPISODE_RESULT.json").is_file(),
            "EPISODE_RECEIPT.json": not (output / "EPISODE_RECEIPT.json").is_file(),
            "FRESH_FORMAL_METRIC_RECEIPT.original": True,
            "CLEANUP_RECEIPT.json": not (output / "CLEANUP_RECEIPT.json").is_file(),
        },
        "unknown_preservation": {
            "scientific_outcome": "UNKNOWN",
            "route_outcome": "UNKNOWN",
            "safety_outcome": "UNKNOWN",
            "unknown_coerced_to_zero_false_safe_or_available": False,
        },
        "runtime_forward_accounting": audit.get("forward_accounting"),
        "runtime_pid_accounting": audit.get("pid_accounting"),
        "runtime_label_firewall": audit.get("label_firewall"),
        "cleanup": post,
        "retry_authorized": False,
        "generated_at_utc": base._now(),
    }
    base._atomic_json(output / "FRESH_FORMAL_METRIC_RECEIPT.json", metric)
    attempt["events"].append(
        {
            "event": "ATTEMPT_TERMINATED_AFTER_ORCHESTRATOR_SESSION_INTERRUPTION",
            "state": "BLOCKED_CONTRACT_DEFECT",
            "timestamp_utc": base._now(),
            "blocker": metric["failure_class"],
            "backend_receipt_status": "MISSING_ORCHESTRATOR_SESSION_INTERRUPTED",
            "backend_evaluator_return_code": None,
            "cleanup_status": post["status"],
            "metric_receipt_path": str((output / "FRESH_FORMAL_METRIC_RECEIPT.json").relative_to(ROOT)),
            "metric_receipt_sha256": base._sha(output / "FRESH_FORMAL_METRIC_RECEIPT.json"),
            "post_cleanup_receipt_sha256": base._sha(output / "ORCHESTRATOR_POST_CLEANUP.json"),
            "episode_receipt_path": None,
            "artifact_hashes": base._artifact_hashes(output),
            "retry_authorized": False,
        }
    )
    linked_retry._save(ledger)
    receipt = {
        "schema_version": "driveclarify.method_v1_r3.formal_train.interrupted_run_finalization.v1",
        "status": "BLOCKED_FORMAL_EVIDENCE_CONTRACT_DEFECT",
        "attempt_id": ATTEMPT_ID,
        "episode_id": EPISODE_ID,
        "split_slot_index": 39,
        "terminal_state": "BLOCKED_CONTRACT_DEFECT",
        "blocker": metric["failure_class"],
        "scientific_exposure": metric["scientific_exposure"],
        "retry_authorized": False,
        "slot40_started": False,
        "ledger_sha256": base._sha(base.LEDGER_PATH),
        "formal_train_counts": base._load(base.LEDGER_PATH)["counts"],
        "dev_attempt_count": 0,
        "test_attempt_count": 0,
        "test_consumed": False,
        "training_jobs": 0,
        "a800_jobs": 0,
        "generated_at_utc": base._now(),
    }
    base._atomic_json(base.REPORT_ROOT / "latest_run_one_receipt.json", receipt)
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
