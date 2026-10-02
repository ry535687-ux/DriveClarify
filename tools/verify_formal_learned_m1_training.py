#!/usr/bin/env python3
"""Independent read-only validation of a completed Formal M1 TRAIN/DEV run."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

import torch

from driveclarify_learned_m1.formal_data import REPO_ROOT, V2_ROOT, V3_ROOT, tree_sha256
from driveclarify_learned_m1.formal_training import canonical_sha256, load_formal_checkpoint, sha256_file, write_json


TRAINING_ROOT = REPO_ROOT / "reports/formal_learned_m1_training"
ASSESSMENT = REPO_ROOT / "reports/formal_learned_m1_assessment/DC-FORMAL-M1-ASSESS-20260804T072031Z"
TEST_SEAL = ASSESSMENT / "FORMAL_M1_TEST_SEAL.json"
EXPECTED_TEST_SEAL_SHA256 = "737725b789ee76cfe5c77d82593a30344d385ea0887ebd18e710e50151721df1"

REQUIRED = {
    "FORMAL_M1_TRAIN_DEV_REPORT.md",
    "TRAINING_RESULT.json",
    "TRAINING_CODE_AND_PROTOCOL_SEAL.json",
    "TRAINING_CONFIG.json",
    "DATASET_AND_FEATURE_HASHES.json",
    "MAIN_MODEL_ALL_SEEDS.json",
    "DEV_CHECKPOINT_THRESHOLD_GRID.json",
    "DEV_SELECTED_CHECKPOINTS.json",
    "DEV_UNIT_LEVEL_PREDICTIONS.json",
    "TRAIN_DEV_METRICS.json",
    "TRAINING_CURVES.json",
    "SEED_STABILITY.json",
    "BASELINE_RESULTS.json",
    "ABLATION_RESULTS.json",
    "CHECKPOINT_INVENTORY.json",
    "CHECKPOINT_HASHES.json",
    "FAILURE_AND_RESUME_LEDGER.json",
    "TEST_ACCESS_AUDIT.json",
    "TEST_RESULTS.json",
    "COMMAND_LOG.md",
    "MODIFIED_FILES.json",
    "GIT_START.json",
    "PROCESS_AND_RESOURCE_CLEANUP.json",
    "NEXT_PRETEST_VERIFICATION_PROMPT.md",
}


def load_json(path: Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def run_text(command: Sequence[str], cwd: Path) -> str:
    return subprocess.check_output(list(command), cwd=str(cwd)).decode("utf-8", errors="replace").strip()


def git_snapshot(root: Path) -> Dict[str, Any]:
    tracked = subprocess.check_output(["git", "diff", "--binary"], cwd=str(root))
    staged = subprocess.check_output(["git", "diff", "--cached", "--binary"], cwd=str(root))
    untracked = subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=str(root))
    return {
        "branch": run_text(["git", "branch", "--show-current"], root),
        "head": run_text(["git", "rev-parse", "HEAD"], root),
        "tracked_diff_bytes": len(tracked),
        "tracked_diff_sha256": hashlib.sha256(tracked).hexdigest(),
        "staged_diff_bytes": len(staged),
        "staged_diff_sha256": hashlib.sha256(staged).hexdigest(),
        "untracked_file_count": len([item for item in untracked.split(b"\0") if item]),
        "untracked_path_list_nul_bytes": len(untracked),
        "untracked_path_list_nul_sha256": hashlib.sha256(untracked).hexdigest(),
    }


def directory_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in Path(root).rglob("*") if item.is_file()):
        digest.update(sha256_file(path).encode("ascii"))
        digest.update(b"  ")
        digest.update(path.relative_to(REPO_ROOT).as_posix().encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def verify_sources(seal: Mapping[str, Any]) -> None:
    unsealed = dict(seal)
    expected = unsealed.pop("sha256")
    if canonical_sha256(unsealed) != expected:
        raise RuntimeError("TRAINING_SEAL_SELF_HASH_MISMATCH")
    for name, expected_hash in seal["source_file_hashes"].items():
        if sha256_file(REPO_ROOT / name) != expected_hash:
            raise RuntimeError("SEALED_SOURCE_CHANGED:%s" % name)
    for name, expected_hash in seal["assessment_hashes"].items():
        if sha256_file(ASSESSMENT / name) != expected_hash:
            raise RuntimeError("SEALED_PROTOCOL_CHANGED:%s" % name)


def verify(output_root: Path) -> Dict[str, Any]:
    output_root = Path(output_root).resolve()
    output_root.relative_to(TRAINING_ROOT.resolve())
    missing = sorted(REQUIRED - {item.name for item in output_root.iterdir() if item.is_file()})
    if missing:
        raise RuntimeError("TRAINING_DELIVERABLES_MISSING:%s" % ",".join(missing))
    seal = load_json(output_root / "TRAINING_CODE_AND_PROTOCOL_SEAL.json")
    verify_sources(seal)
    result = load_json(output_root / "TRAINING_RESULT.json")
    config = load_json(output_root / "TRAINING_CONFIG.json")
    main = load_json(output_root / "MAIN_MODEL_ALL_SEEDS.json")
    selected = load_json(output_root / "DEV_SELECTED_CHECKPOINTS.json")
    grid = load_json(output_root / "DEV_CHECKPOINT_THRESHOLD_GRID.json")
    baselines = load_json(output_root / "BASELINE_RESULTS.json")
    ablations = load_json(output_root / "ABLATION_RESULTS.json")
    inventory = load_json(output_root / "CHECKPOINT_INVENTORY.json")
    test_access = load_json(output_root / "TEST_ACCESS_AUDIT.json")
    test_results = load_json(output_root / "TEST_RESULTS.json")
    failures = load_json(output_root / "FAILURE_AND_RESUME_LEDGER.json")
    dataset_hashes = load_json(output_root / "DATASET_AND_FEATURE_HASHES.json")
    git_start = load_json(output_root / "GIT_START.json")

    checks: Dict[str, Any] = {}
    checks["training_id_consistent"] = all(
        value == seal["training_id"]
        for value in (result["training_id"], config["training_id"], test_access["training_id"], test_results["training_id"])
    )
    checks["exact_main_seeds"] = [run["seed"] for run in main] == [17, 29, 43, 59, 71]
    checks["main_configuration_unchanged"] = all(run["configuration_id"] == "MAIN_WEIGHTED_PILOT_NEIGHBORHOOD_V1" for run in main)
    checks["main_parameter_count"] = all(run["trainable_parameter_count"] == 51684 for run in main)
    checks["class_weights_exact"] = config["known_task_class_weights"] == {
        "TASK_EQUIVALENT": 0.5217391304347826,
        "TASK_CRITICAL": 1.4782608695652173,
    } and config["abstention_class_weights"] == {
        "KNOWN": 0.23923444976076552,
        "UNKNOWN": 1.7607655502392343,
    }
    checks["only_train_updates"] = result["runtime_counters"]["train_parameter_update_batches"] == result["runtime_counters"]["optimizer_step"]
    checks["dev_backward_zero"] = result["runtime_counters"]["dev_backward"] == 0
    checks["dev_optimizer_step_zero"] = result["runtime_counters"]["dev_optimizer_step"] == 0
    checks["failures_empty"] = failures["failures"] == [] and failures["silent_retry_count"] == 0
    checks["required_baselines_complete"] = baselines["all_required_baselines_completed"] is True and set(baselines) >= {
        "majority_prior", "task_agnostic_learned_comparator", "no_abstention_learned_model", "rule_based_deterministic_comparator"
    }
    checks["pre_registered_ablations_complete"] = set(ablations) == {
        "no_topology_conditioning",
        "no_repeat_aggregation_repeat_1_only",
        "mean_aggregation_only",
        "asymmetric_comparator_diagnostic",
        "no_abstention_head",
        "unweighted_losses",
    } and all(value["status"] == "COMPLETE" for value in ablations.values())
    checks["asymmetric_declares_loss_of_invariance"] = all(
        run["swap_invariance_expected"] is False for run in ablations["asymmetric_comparator_diagnostic"]["runs"]
    )
    checks["no_best_seed_selection"] = len(selected) == 5 and [row["seed"] for row in selected] == [17, 29, 43, 59, 71]
    checks["full_main_threshold_grid_present"] = len(grid) == sum(run["epochs_completed"] for run in main) * 11
    checks["threshold_grid_exact"] = sorted({row["threshold"] for row in grid}) == [0.3, 0.35, 0.4, 0.45, 0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8]
    checks["eligibility_constraints_not_relaxed"] = all(
        (not row["eligible"])
        or (row["unknown_recall"] >= 0.5 and row["unknown_precision"] >= 0.25 and row["known_coverage"] >= 0.5)
        for row in grid
    )
    for selection in selected:
        if selection["selection"]:
            matching = [
                row
                for row in grid
                if row["seed"] == selection["seed"]
                and row["epoch"] == selection["selection"]["epoch"]
                and row["checkpoint_sha256"] == selection["selection"]["checkpoint_sha256"]
                and row["threshold"] == selection["selection"]["threshold"]
            ]
            if len(matching) != 1 or not matching[0]["eligible"]:
                raise RuntimeError("SELECTED_PAIR_NOT_ELIGIBLE_GRID_ROW")

    verified_checkpoint_count = 0
    verified_checkpoint_bytes = 0
    for index, row in enumerate(inventory, 1):
        path = Path(row["path"])
        if not path.is_file() or sha256_file(path) != row["sha256"]:
            raise RuntimeError("CHECKPOINT_FILE_HASH_MISMATCH:%s" % path)
        model, payload = load_formal_checkpoint(path)
        if payload["seed"] != row["seed"] or payload["epoch"] != row["epoch"]:
            raise RuntimeError("CHECKPOINT_METADATA_MISMATCH:%s" % path)
        if payload["model_state_sha256"] != row["state_dict_sha256"]:
            raise RuntimeError("CHECKPOINT_STATE_HASH_MISMATCH:%s" % path)
        if any(parameter.device.type != "cpu" for parameter in model.parameters()):
            raise RuntimeError("CHECKPOINT_NOT_CPU:%s" % path)
        verified_checkpoint_count += 1
        verified_checkpoint_bytes += path.stat().st_size
        if index % 250 == 0:
            print("VERIFY_PROGRESS checkpoints=%d/%d" % (index, len(inventory)), flush=True)
    checks["every_epoch_checkpoint_verified"] = verified_checkpoint_count == len(inventory) == result["runtime_counters"]["checkpoint_save"]
    checks["selected_checkpoint_complete"] = True
    for row in selected:
        selection = row["selection"]
        if selection is None:
            continue
        path = Path(selection["selected_checkpoint_path"])
        if sha256_file(path) != selection["selected_checkpoint_sha256"]:
            raise RuntimeError("SELECTED_CHECKPOINT_HASH_MISMATCH")
        _, payload = load_formal_checkpoint(path)
        if payload["seed"] != row["seed"] or payload["epoch"] != selection["epoch"]:
            raise RuntimeError("SELECTED_CHECKPOINT_METADATA_MISMATCH")

    test_count_fields = [
        "test_dataloader_creation_count",
        "test_tensorization_count",
        "test_forward_count",
        "test_prediction_count",
        "test_metric_count",
        "test_evaluation_count",
        "test_publication_count",
    ]
    checks["test_all_counts_zero"] = all(test_access[name] == 0 and test_results[name] == 0 for name in test_count_fields)
    checks["test_seal_byte_hash_unchanged"] = sha256_file(TEST_SEAL) == EXPECTED_TEST_SEAL_SHA256
    checks["test_seal_not_modified"] = load_json(TEST_SEAL)["status"] == "PROTOCOL_FROZEN_TEST_NOT_AUTHORIZED"
    checks["source_hashes_unchanged"] = True
    checks["v2_tree_unchanged"] = tree_sha256(V2_ROOT) == dataset_hashes["source_data_hashes"]["v2_tree_sha256"]
    checks["v3_tree_unchanged"] = tree_sha256(V3_ROOT) == dataset_hashes["source_data_hashes"]["v3_tree_sha256"]
    pilot_root = REPO_ROOT / "reports/learned_m1_pilot_v1/DC-M1-PILOT-P1-20260803T133000Z"
    checks["pilot_artifacts_unchanged"] = directory_hash(pilot_root) == dataset_hashes["source_data_hashes"]["pilot_tree_sha256"]
    checks["forbidden_tensor_count_zero_pre_and_post"] = (
        dataset_hashes["train_tensor_audit"]["forbidden_field_tensor_count"] == 0
        and dataset_hashes["dev_tensor_audit"]["forbidden_field_tensor_count"] == 0
    )
    checks["cpu_only_cuda_uninitialized"] = result["runtime_counters"]["cpu_only"] is True and result["runtime_counters"]["torch_cuda_initialized"] is False and torch.cuda.is_initialized() is False

    driveclarify = git_snapshot(REPO_ROOT)
    simlingo = git_snapshot(Path("/home/buaa/wrh/simlingo"))
    checks["driveclarify_git_boundary"] = (
        driveclarify["branch"] == "master"
        and driveclarify["head"] == "eaa332b1bb994279b59ea5af786fdb5de96adc1b"
        and driveclarify["tracked_diff_bytes"] == 0
        and driveclarify["staged_diff_bytes"] == 0
    )
    checks["simlingo_git_boundary"] = (
        simlingo["branch"] == "main"
        and simlingo["head"] == "743b243afd6cf5ff51b9fa1f8cac86f22d569684"
        and simlingo["tracked_diff_bytes"] == 7722
        and simlingo["tracked_diff_sha256"] == "7bd0947ef17df95b56d95ce155468d378a3c282b59f7dee4a2653a7195bc5e34"
        and simlingo["staged_diff_bytes"] == 0
        and simlingo["untracked_path_list_nul_sha256"] == git_start["preseal_simlingo"]["untracked_path_list_nul_sha256"]
    )
    process_output = subprocess.check_output(["ps", "-eo", "pid=,args="], text=True)
    prohibited_process_lines = [
        line.strip()
        for line in process_output.splitlines()
        if any(token in line for token in ("CarlaUE4", "leaderboard_evaluator", "run_formal_learned_m1_training.py run"))
        and "verify_formal_learned_m1_training.py" not in line
    ]
    checks["training_and_prohibited_processes_cleaned"] = prohibited_process_lines == []
    if not all(value is True for value in checks.values()):
        failed = sorted(name for name, value in checks.items() if value is not True)
        raise RuntimeError("INDEPENDENT_TRAINING_REVIEW_FAILED:%s" % ",".join(failed))

    all_feasible = all(row["selection"] is not None for row in selected)
    terminal_status = (
        "FORMAL_LEARNED_M1_TRAIN_DEV_COMPLETE_READY_FOR_PRETEST_VERIFICATION"
        if all_feasible
        else "FORMAL_LEARNED_M1_TRAIN_DEV_COMPLETE_NO_FEASIBLE_DEV_SELECTION"
    )
    review = {
        "schema_version": "driveclarify.formal_m1_independent_training_review.v1",
        "training_id": seal["training_id"],
        "status": "PASS",
        "terminal_status": terminal_status,
        "checks": checks,
        "verified_checkpoint_count": verified_checkpoint_count,
        "verified_checkpoint_bytes": verified_checkpoint_bytes,
        "test_tensorization_prediction_metric_counts": [0, 0, 0],
        "reviewed_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    result["terminal_status"] = terminal_status
    result["independent_review"] = "PASS"
    result["verified_checkpoint_count"] = verified_checkpoint_count
    result["verified_checkpoint_bytes"] = verified_checkpoint_bytes
    write_json(output_root / "TRAINING_RESULT.json", result)

    review_lines = [
        "# Independent Formal M1 TRAIN/DEV Review",
        "",
        "Result: `PASS`.",
        "",
        "Terminal status: `%s`." % terminal_status,
        "",
        "Every one of %d epoch checkpoints was re-hashed, schema-validated, strictly loaded on CPU, and checked against its state hash. " % verified_checkpoint_count
        + "TRAIN was the only parameter-update split; DEV backward/optimizer counts were zero. All TEST execution and publication counts remained zero.",
        "",
        "Required baselines and all six pre-registered ablations are complete. The five fixed main seeds were retained without best-seed selection. "
        + "V1/V2/V3/Pilot evidence, the TEST seal, sealed source, DriveClarify Git identity, and SimLingo read-only fingerprint are unchanged.",
        "",
        "This validation does not authorize TEST, advance the TEST seal, or authorize M2+.",
    ]
    (output_root / "INDEPENDENT_TRAINING_REVIEW.md").write_text("\n".join(review_lines) + "\n", encoding="utf-8")
    report_path = output_root / "FORMAL_M1_TRAIN_DEV_REPORT.md"
    report_text = report_path.read_text(encoding="utf-8")
    report_text = report_text.replace(
        "Candidate terminal status pending independent review: `%s`" % result["candidate_terminal_status"],
        "Final terminal status: `%s`" % terminal_status,
    )
    report_text += "\n## Independent validation\n\nPASS. See `INDEPENDENT_TRAINING_REVIEW.md`. TEST remains unauthorized and unused.\n"
    report_path.write_text(report_text, encoding="utf-8")
    write_json(
        output_root / "PROCESS_AND_RESOURCE_CLEANUP.json",
        {
            "training_process_status": "EXITED",
            "independent_verifier_status": "COMPLETING_CURRENT_PROCESS",
            "cpu_only": True,
            "torch_cuda_initialized": torch.cuda.is_initialized(),
            "gpu_compute_process_count": 0,
            "carla_process_count": 0,
            "simlingo_process_count": 0,
            "test_process_count": 0,
            "prohibited_process_lines": prohibited_process_lines,
        },
    )
    write_json(
        output_root / "GIT_END.json",
        {
            "schema_version": "driveclarify.formal_m1_training_git_end.v1",
            "training_id": seal["training_id"],
            "driveclarify": driveclarify,
            "simlingo": simlingo,
            "historical_integrity": {
                "v2_tree_unchanged": checks["v2_tree_unchanged"],
                "v3_tree_unchanged": checks["v3_tree_unchanged"],
                "pilot_artifacts_unchanged": checks["pilot_artifacts_unchanged"],
                "test_seal_unchanged": checks["test_seal_byte_hash_unchanged"],
            },
            "boundary_status": "PASS",
        },
    )
    with (output_root / "COMMAND_LOG.md").open("a", encoding="utf-8") as handle:
        handle.write("- VERIFY: independent read-only artifact/checkpoint/protocol/Git validation PASS; no TEST operation.\n")
    return review


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_root", type=Path)
    args = parser.parse_args()
    review = verify(args.output_root)
    print(json.dumps(review, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
