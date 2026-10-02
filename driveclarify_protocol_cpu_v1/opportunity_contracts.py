"""R4.1 actual-owner decision-opportunity contract builder."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from .contracts import canonical_sha256
from .decision_owner_replay import build_actual_owner_records


CLASSES = ("ACT", "ACT_SHARED", "ASK", "WAIT", "FALLBACK")


def build() -> dict[str, Any]:
    records = build_actual_owner_records()
    coverage = Counter(row["family"] for row in records)
    observed = Counter(row["actual_execution"]["decision"] for row in records)
    by_id = {row["opportunity_id"]: row for row in records}

    def one_variable_pair(row: dict[str, Any]) -> bool:
        positive = by_id.get(row["matched_positive_opportunity_id"])
        if positive is None:
            return False
        left, right = positive["execution_evidence"], row["execution_evidence"]
        if row["family"] == "WAIT":
            left_events, right_events = left.get("lifecycle_events"), right.get("lifecycle_events")
            left_rest = {key: value for key, value in left.items() if key != "lifecycle_events"}
            right_rest = {key: value for key, value in right.items() if key != "lifecycle_events"}
            return (
                left_rest == right_rest
                and isinstance(left_events, list) and isinstance(right_events, list)
                and len(left_events) == len(right_events) + 1
                and all(event in left_events for event in right_events)
            )
        keys = set(left) | set(right)
        return sum(left.get(key) != right.get(key) for key in keys) == 1

    checks = {
        "actual_owner_execution_before_gold_join": all(
            row["offline_audit"]["joined_after_actual_seal"] for row in records
        ),
        "gold_not_visible_to_owner": all(row["gold_visible_to_owner"] is False for row in records),
        "all_observed_match_offline_expected": all(row["offline_audit"]["match"] for row in records),
        "all_five_owner_families_covered": set(coverage) == set(CLASSES),
        "all_five_decisions_observed": set(observed) == set(CLASSES),
        "all_negatives_have_matched_positive": all(
            row["matched_positive_opportunity_id"] is not None
            for row in records
            if "_NEG_" in row["opportunity_id"]
        ),
        "matched_negative_pairs_change_one_variable": all(
            one_variable_pair(row) for row in records if "_NEG_" in row["opportunity_id"]
        ),
        "standalone_fallback_proofs_do_not_claim_false_pairing": all(
            row["matched_positive_opportunity_id"] is None
            for row in records
            if row["family"] == "FALLBACK" and row["opportunity_id"] != "FALLBACK_POSITIVE_K0"
        ),
        "wait_positive_uses_lifecycle": any(
            row["family"] == "WAIT"
            and row["actual_execution"]["decision"] == "WAIT"
            and row["actual_execution"]["gate_trace"]["verified_holding_available"] is True
            and row["actual_execution"]["gate_trace"].get("lifecycle_derived_not_injected") is True
            and len(row["actual_execution"]["gate_trace"].get("lifecycle_transition_trace", [])) == 6
            and row["actual_execution"]["owner_function"] == "evaluate_decision_opportunity_lifecycle"
            for row in records
        ),
        "unknown_never_authorizes": all(
            row["actual_execution"]["authorization_eligible"] is False
            for row in records
            if row["execution_evidence"].get("evidence_fresh", "present") is None
        ),
    }
    value = {
        "schema_version": "driveclarify.decision_opportunity_actual_owner_contracts.r4_1.v1",
        "status": "PASS" if all(checks.values()) else "FAIL",
        "evidence_class": "CPU_ACTUAL_OWNER_REPLAY_NOT_NATIVE_SCIENTIFIC_EVIDENCE",
        "checks": checks,
        "coverage_summary": dict(coverage),
        "observed_decision_summary": dict(observed),
        "records": records,
        "scope": {
            "gpu_forward_count": 0,
            "checkpoint_load_count": 0,
            "cuda_context_creation_count": 0,
            "carla_launch_count": 0,
            "dev_test_access_count": 0,
        },
    }
    value["contract_bundle_sha256"] = canonical_sha256(value)
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    value = build()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": value["status"], "count": len(value["records"]), "sha256": value["contract_bundle_sha256"]}))
    return 0 if value["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
