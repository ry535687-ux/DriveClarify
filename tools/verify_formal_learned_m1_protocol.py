#!/usr/bin/env python3
"""Independent, prediction-free verifier for a Formal Learned M1 assessment."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from driveclarify_learned_m1.formal_data import (
    EXPECTED_V2_TREE_SHA256,
    REPO_ROOT,
    V2_ROOT,
    audit_formal_dataset,
    sha256_file,
    tree_sha256,
)
from driveclarify_learned_m1.formal_protocol import assert_cpu_only, verify_seal


REQUIRED = {
    "FORMAL_M1_ASSESSMENT_REPORT.md",
    "FORMAL_M1_DATASET_AUDIT.json",
    "FORMAL_M1_DATASET_INDEX.json",
    "FEATURE_WHITELIST.json",
    "FEATURE_BLACKLIST.json",
    "INPUT_TENSOR_CONTRACT.json",
    "FORMAL_M1_MODEL_SPEC.md",
    "FORMAL_M1_MODEL_SPEC.json",
    "FORMAL_M1_TRAINING_PROTOCOL.md",
    "FORMAL_M1_TRAINING_PROTOCOL.json",
    "FORMAL_M1_BASELINES_AND_ABLATIONS.md",
    "FORMAL_M1_METRICS_SPEC.md",
    "FORMAL_M1_DEV_SELECTION_AND_CALIBRATION.md",
    "FORMAL_M1_TEST_SEAL_PROTOCOL.md",
    "FORMAL_M1_TEST_SEAL.json",
    "FORMAL_M1_REVIEWER_ATTACK.md",
    "FORMAL_M1_RESOURCE_ESTIMATE.md",
    "TEST_RESULTS.json",
    "COMMAND_LOG.md",
    "MODIFIED_FILES.json",
    "GIT_START.json",
    "GIT_END.json",
    "NEXT_FORMAL_M1_TRAINING_PROMPT.md",
}


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("assessment_directory", type=Path)
    args = parser.parse_args()
    root = args.assessment_directory.resolve()
    root.relative_to(REPO_ROOT.resolve())
    assert_cpu_only("cpu")
    missing = sorted(REQUIRED - {item.name for item in root.iterdir() if item.is_file()})
    if missing:
        raise RuntimeError("ASSESSMENT_DELIVERABLES_MISSING:%s" % ",".join(missing))
    audit = audit_formal_dataset()
    stored_audit = load_json(root / "FORMAL_M1_DATASET_AUDIT.json")
    index = load_json(root / "FORMAL_M1_DATASET_INDEX.json")
    whitelist = load_json(root / "FEATURE_WHITELIST.json")
    blacklist = load_json(root / "FEATURE_BLACKLIST.json")
    tensor_contract = load_json(root / "INPUT_TENSOR_CONTRACT.json")
    model_spec = load_json(root / "FORMAL_M1_MODEL_SPEC.json")
    protocol = load_json(root / "FORMAL_M1_TRAINING_PROTOCOL.json")
    test_seal = load_json(root / "FORMAL_M1_TEST_SEAL.json")
    tests = load_json(root / "TEST_RESULTS.json")
    verify_seal(test_seal)

    checks = {
        "live_audit_pass": audit["status"] == "PASS",
        "stored_audit_pass": stored_audit["status"] == "PASS",
        "unit_count_47": audit["complete_unit_count"] == stored_audit["complete_unit_count"] == index["complete_unit_count"] == 47,
        "plan_count_282": audit["plan_record_count"] == stored_audit["plan_record_count"] == index["plan_record_count"] == 282,
        "split_exact": audit["split_distribution"] == {"TRAIN": 25, "DEV": 11, "TEST": 11},
        "labels_exact": audit["label_distribution"] == {"TASK_EQUIVALENT": 31, "TASK_CRITICAL": 12, "UNKNOWN": 4},
        "pilot_overlap_zero": audit["pilot_overlap"] == [],
        "split_overlap_zero": all(not value for value in audit["split_overlap"].values()),
        "forbidden_tensor_count_zero": blacklist["forbidden_field_tensor_count"] == tensor_contract["forbidden_field_tensor_count"] == 0,
        "whitelist_allowlist_only": whitelist["construction"] == "EXPLICIT_ALLOWLIST_ONLY",
        "targets_separate": tensor_contract["targets_physically_separate_from_model_inputs"] is True,
        "parameter_count": model_spec["trainable_parameter_count"] == 51684,
        "training_not_authorized": protocol["formal_training_authorized"] is False,
        "test_not_authorized": test_seal["status"] == "PROTOCOL_FROZEN_TEST_NOT_AUTHORIZED",
        "test_prediction_zero": test_seal["test_evaluation_count"] == tests["runtime_counts"]["test_prediction"] == 0,
        "test_metric_zero": tests["runtime_counts"]["test_performance_metric"] == 0,
        "training_zero": tests["runtime_counts"]["optimizer_step"] == tests["runtime_counts"]["backward"] == 0,
        "cuda_zero": tests["runtime_counts"]["cuda_context"] == 0,
        "v2_tree_unchanged": tree_sha256(V2_ROOT) == EXPECTED_V2_TREE_SHA256,
    }
    if not all(checks.values()):
        raise RuntimeError("FORMAL_PROTOCOL_VERIFICATION_FAILED:%s" % json.dumps(checks, sort_keys=True))
    inventory = {
        item.name: {"bytes": item.stat().st_size, "sha256": sha256_file(item)}
        for item in sorted(root.iterdir())
        if item.is_file()
    }
    result = {
        "schema_version": "driveclarify.formal_m1_independent_verifier.v1",
        "status": "PASS",
        "checks": checks,
        "assessment_directory": str(root.relative_to(REPO_ROOT)),
        "artifact_count": len(inventory),
        "artifact_inventory_sha256": hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "test_predictions_computed": 0,
        "test_performance_metrics_computed": 0,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

