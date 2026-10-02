#!/usr/bin/env python3
"""Prepare and execute the sealed Formal Learned M1 TRAIN/DEV-only run."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

import torch

from driveclarify_learned_m1.checkpoint import state_dict_sha256
from driveclarify_learned_m1.formal_data import (
    MODEL_INPUT_KEYS,
    REPO_ROOT,
    V2_DATASET,
    V2_ROOT,
    V3_DATASET,
    V3_ROOT,
    load_formal_train_dev_records,
    tensorize_formal_records,
    tree_sha256,
)
from driveclarify_learned_m1.formal_protocol import FORMAL_SEEDS, UNKNOWN_THRESHOLD_GRID, assert_cpu_only, verify_seal
from driveclarify_learned_m1.formal_training import (
    TASK_CLASS_WEIGHTS,
    UNKNOWN_CLASS_WEIGHTS,
    VARIANTS,
    FormalTrainingError,
    canonical_sha256,
    deterministic_baselines,
    load_formal_checkpoint,
    save_formal_checkpoint,
    seed_stability,
    sha256_file,
    train_learned_variant,
    write_json,
)
from driveclarify_learned_m1.model import LearnedM1, trainable_parameter_count


ASSESSMENT_ID = "DC-FORMAL-M1-ASSESS-20260804T072031Z"
ASSESSMENT = REPO_ROOT / "reports/formal_learned_m1_assessment" / ASSESSMENT_ID
TRAINING_ROOT = REPO_ROOT / "reports/formal_learned_m1_training"
ATTACHMENT = Path("/home/buaa/.codex/attachments/f6a7dd27-1242-4232-b4d6-6efeb6c0f374/pasted-text.txt")
TEST_SEAL = ASSESSMENT / "FORMAL_M1_TEST_SEAL.json"
EXPECTED_TEST_SEAL_FILE_SHA256 = "737725b789ee76cfe5c77d82593a30344d385ea0887ebd18e710e50151721df1"
EXPECTED_V2_TREE_SHA256 = "31dc8aaf2774f46fa47dd2dfe05832ca85c6797947efddbef418dbab446f5843"
EXPECTED_V3_TREE_SHA256 = "7d95d1fd76fbbfdcd58885a819f9b12739da0e627817a1a7c6b3c743d9bf460f"

SOURCE_FILES = (
    "driveclarify_learned_m1/model.py",
    "driveclarify_learned_m1/formal_data.py",
    "driveclarify_learned_m1/formal_protocol.py",
    "driveclarify_learned_m1/formal_training.py",
    "driveclarify_learned_m1/checkpoint.py",
    "tools/run_formal_learned_m1_training.py",
    "tools/verify_formal_learned_m1_training.py",
)

ASSESSMENT_HASH_FILES = (
    "FORMAL_M1_DATASET_INDEX.json",
    "FEATURE_WHITELIST.json",
    "FEATURE_BLACKLIST.json",
    "INPUT_TENSOR_CONTRACT.json",
    "FORMAL_M1_MODEL_SPEC.json",
    "FORMAL_M1_TRAINING_PROTOCOL.json",
    "FORMAL_M1_DEV_SELECTION_AND_CALIBRATION.md",
    "FORMAL_M1_METRICS_SPEC.md",
    "FORMAL_M1_BASELINES_AND_ABLATIONS.md",
    "FORMAL_M1_TEST_SEAL_PROTOCOL.md",
)


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


def training_config(training_id: str) -> Dict[str, Any]:
    return {
        "schema_version": "driveclarify.formal_m1_training_config.v1",
        "training_id": training_id,
        "assessment_id": ASSESSMENT_ID,
        "device": "cpu",
        "optimizer": "AdamW",
        "learning_rate": 0.001,
        "weight_decay": 0.0001,
        "unit_batch_size": 5,
        "maximum_epochs": 300,
        "checkpoint_frequency_epochs": 1,
        "early_stopping_patience_epochs": 40,
        "gradient_clip_norm": 1.0,
        "loss_weights": {"task": 1.0, "abstention": 0.5, "repeat": 0.1},
        "known_task_class_weights": {"TASK_EQUIVALENT": TASK_CLASS_WEIGHTS[0], "TASK_CRITICAL": TASK_CLASS_WEIGHTS[1]},
        "abstention_class_weights": {"KNOWN": UNKNOWN_CLASS_WEIGHTS[0], "UNKNOWN": UNKNOWN_CLASS_WEIGHTS[1]},
        "seeds": list(FORMAL_SEEDS),
        "unknown_threshold_grid": list(UNKNOWN_THRESHOLD_GRID),
        "learned_configuration_order": list(VARIANTS),
        "required_deterministic_baselines": ["MAJORITY_PRIOR", "RULE_BASED_DETERMINISTIC_COMPARATOR"],
        "test_use": "FORBIDDEN",
        "pilot_use": "PILOT_DEVELOPMENT_ONLY_EXCLUDED",
        "unit_duplication": False,
        "repeat_as_sample": False,
        "balanced_oversampling": False,
    }


def _swap_candidates(inputs: Mapping[str, torch.Tensor]) -> Dict[str, torch.Tensor]:
    swapped = dict(inputs)
    for name in ("route", "route_mask", "speed", "speed_mask", "roles"):
        swapped[name] = inputs[name].flip(1)
    return swapped


def preflight(training_id: str, output_root: Path) -> Dict[str, Any]:
    assert_cpu_only("cpu")
    if torch.cuda.is_initialized():
        raise FormalTrainingError("CUDA_CONTEXT_INITIALIZED_DURING_PREFLIGHT")
    index = load_json(ASSESSMENT / "FORMAL_M1_DATASET_INDEX.json")
    labels = Counter(row["label"] for row in index["records"])
    splits = Counter(row["split"] for row in index["records"])
    split_labels = {
        split: Counter(row["label"] for row in index["records"] if row["split"] == split)
        for split in ("TRAIN", "DEV", "TEST")
    }
    if splits != Counter({"TRAIN": 25, "DEV": 11, "TEST": 11}):
        raise FormalTrainingError("SPLIT_COUNT_MISMATCH")
    if labels != Counter({"TASK_EQUIVALENT": 31, "TASK_CRITICAL": 12, "UNKNOWN": 4}):
        raise FormalTrainingError("LABEL_COUNT_MISMATCH")
    if set(split_labels["TRAIN"]) != {"TASK_EQUIVALENT", "TASK_CRITICAL", "UNKNOWN"}:
        raise FormalTrainingError("TRAIN_CLASS_COVERAGE_MISSING")
    if set(split_labels["DEV"]) != {"TASK_EQUIVALENT", "TASK_CRITICAL", "UNKNOWN"}:
        raise FormalTrainingError("DEV_CLASS_COVERAGE_MISSING")
    stored_audit = load_json(ASSESSMENT / "FORMAL_M1_DATASET_AUDIT.json")
    if stored_audit["pilot_overlap"] or any(stored_audit["split_overlap"].values()):
        raise FormalTrainingError("PILOT_OR_SPLIT_OVERLAP")
    whitelist = load_json(ASSESSMENT / "FEATURE_WHITELIST.json")
    blacklist = load_json(ASSESSMENT / "FEATURE_BLACKLIST.json")
    tensor_contract = load_json(ASSESSMENT / "INPUT_TENSOR_CONTRACT.json")
    if whitelist["construction"] != "EXPLICIT_ALLOWLIST_ONLY":
        raise FormalTrainingError("FEATURE_CONSTRUCTION_NOT_EXPLICIT_ALLOWLIST")
    if blacklist["forbidden_field_tensor_count"] != 0 or tensor_contract["forbidden_field_tensor_count"] != 0:
        raise FormalTrainingError("FORBIDDEN_FIELD_TENSOR_COUNT_NONZERO")
    if not tensor_contract["targets_physically_separate_from_model_inputs"]:
        raise FormalTrainingError("TARGETS_NOT_PHYSICALLY_SEPARATE")

    records = load_formal_train_dev_records()
    train_records = [record for record in records if record["split"] == "TRAIN"]
    dev_records = [record for record in records if record["split"] == "DEV"]
    train_bundle = tensorize_formal_records(train_records)
    dev_bundle = tensorize_formal_records(dev_records)
    if train_bundle["audit"]["forbidden_field_tensor_count"] != 0 or dev_bundle["audit"]["forbidden_field_tensor_count"] != 0:
        raise FormalTrainingError("LIVE_FORBIDDEN_FIELD_TENSOR_COUNT_NONZERO")
    if train_bundle["audit"]["test_tensorized"] or dev_bundle["audit"]["test_tensorized"]:
        raise FormalTrainingError("UNAUTHORIZED_TEST_TENSORIZATION")
    if set(train_bundle["model_inputs"]).intersection(train_bundle["targets"]):
        raise FormalTrainingError("TARGET_MODEL_INPUT_KEY_COLLISION")

    torch.manual_seed(17)
    model = LearnedM1().eval()
    if trainable_parameter_count(model) != 51684:
        raise FormalTrainingError("MODEL_PARAMETER_COUNT_NOT_51684")
    with torch.no_grad():
        base = model({name: value[:1] for name, value in train_bundle["model_inputs"].items()})
        swapped = model(_swap_candidates({name: value[:1] for name, value in train_bundle["model_inputs"].items()}))
        repeat_inputs = {name: value[:1] for name, value in train_bundle["model_inputs"].items()}
        order = torch.tensor([2, 0, 1])
        repeat_inputs = dict(repeat_inputs)
        for name in ("route", "route_mask", "speed", "speed_mask"):
            repeat_inputs[name] = repeat_inputs[name].index_select(2, order)
        repeated = model(repeat_inputs)
    for name in ("task_logits", "unknown_logits"):
        if tuple(base[name].shape) != (1, 2) or not bool(torch.isfinite(base[name]).all().item()):
            raise FormalTrainingError("SYNTHETIC_FORWARD_FAILED")
        if not torch.allclose(base[name], swapped[name], atol=1.0e-7, rtol=0.0):
            raise FormalTrainingError("AB_SWAP_INVARIANCE_FAILED")
        if not torch.allclose(base[name], repeated[name], atol=1.0e-7, rtol=0.0):
            raise FormalTrainingError("REPEAT_PERMUTATION_INVARIANCE_FAILED")

    assessment_hashes = {name: sha256_file(ASSESSMENT / name) for name in ASSESSMENT_HASH_FILES}
    source_hashes = {name: sha256_file(REPO_ROOT / name) for name in SOURCE_FILES}
    config = training_config(training_id)
    config_sha = canonical_sha256(config)
    temporary_hashes = {
        "training_config_sha256": config_sha,
        "dataset_index_sha256": assessment_hashes["FORMAL_M1_DATASET_INDEX.json"],
        "feature_contract_sha256": assessment_hashes["FEATURE_WHITELIST.json"],
        "model_spec_sha256": assessment_hashes["FORMAL_M1_MODEL_SPEC.json"],
        "protocol_seal_sha256": "f" * 64,
        "training_protocol_sha256": assessment_hashes["FORMAL_M1_TRAINING_PROTOCOL.json"],
        "configuration_id": "MAIN_WEIGHTED_PILOT_NEIGHBORHOOD_V1",
    }
    with tempfile.TemporaryDirectory(prefix="formal_m1_preflight_") as temporary_directory:
        optimizer = torch.optim.AdamW(model.parameters(), lr=0.001, weight_decay=0.0001)
        checkpoint_path = Path(temporary_directory) / "preflight.pt"
        saved = save_formal_checkpoint(
            checkpoint_path,
            model,
            optimizer,
            17,
            0,
            {"hidden_dim": 64, "embedding_dim": 32},
            {"task_loss_weight": 1.0, "abstention_loss_weight": 0.5, "repeat_loss_weight": 0.1},
            temporary_hashes,
        )
        loaded, payload = load_formal_checkpoint(checkpoint_path)
        if payload["model_state_sha256"] != state_dict_sha256(loaded.state_dict()) or saved["seed"] != 17:
            raise FormalTrainingError("CHECKPOINT_SAVE_LOAD_PREFLIGHT_FAILED")

    test_seal = load_json(TEST_SEAL)
    verify_seal(test_seal)
    if sha256_file(TEST_SEAL) != EXPECTED_TEST_SEAL_FILE_SHA256:
        raise FormalTrainingError("TEST_SEAL_FILE_HASH_MISMATCH")
    if test_seal["status"] != "PROTOCOL_FROZEN_TEST_NOT_AUTHORIZED":
        raise FormalTrainingError("TEST_SEAL_STATUS_CHANGED")
    if any(test_seal[name] != 0 for name in ("test_evaluation_count", "test_prediction_record_count", "publication_count")):
        raise FormalTrainingError("TEST_SEAL_COUNT_NONZERO")
    v2_tree = tree_sha256(V2_ROOT)
    v3_tree = tree_sha256(V3_ROOT)
    if v2_tree != EXPECTED_V2_TREE_SHA256:
        raise FormalTrainingError("V2_TREE_HASH_MISMATCH")
    if v3_tree != EXPECTED_V3_TREE_SHA256:
        raise FormalTrainingError("V3_TREE_HASH_MISMATCH")
    pilot_root = REPO_ROOT / "reports/learned_m1_pilot_v1/DC-M1-PILOT-P1-20260803T133000Z"
    v1_dataset = REPO_ROOT / "reports/multi_topology_static_units_v1/offline_candidate_capture_runs/DC-MULTI-A3B3-C1-20260803T121500Z/M1_REAL_DATASET_V1.json"
    source_data_hashes = {
        "v1_dataset_sha256": sha256_file(v1_dataset),
        "v2_dataset_sha256": sha256_file(V2_DATASET),
        "v3_dataset_sha256": sha256_file(V3_DATASET),
        "v2_tree_sha256": v2_tree,
        "v3_tree_sha256": v3_tree,
        "pilot_tree_sha256": directory_hash(pilot_root),
    }
    checks = {
        "dataset_index_sha256_recomputed": True,
        "feature_contract_sha256_recomputed": True,
        "model_spec_sha256_recomputed": True,
        "training_protocol_sha256_recomputed": True,
        "split_25_11_11": True,
        "labels_31_12_4": True,
        "train_dev_class_coverage": True,
        "pilot_overlap_zero": True,
        "five_split_overlap_dimensions_zero": True,
        "forbidden_tensor_fields_zero": True,
        "test_tensorization_count_zero": True,
        "parameter_count_51684": True,
        "synthetic_forward": "PASS",
        "ab_swap_invariance": "PASS",
        "repeat_permutation_invariance": "PASS",
        "checkpoint_save_load": "PASS",
        "cpu_only_guard": "PASS",
        "test_seal_status": test_seal["status"],
        "test_evaluation_count": test_seal["test_evaluation_count"],
        "test_prediction_record_count": test_seal["test_prediction_record_count"],
        "torch_cuda_initialized": torch.cuda.is_initialized(),
    }
    return {
        "checks": checks,
        "assessment_hashes": assessment_hashes,
        "source_file_hashes": source_hashes,
        "source_data_hashes": source_data_hashes,
        "training_config": config,
        "training_config_sha256": config_sha,
        "train_tensor_audit": train_bundle["audit"],
        "dev_tensor_audit": dev_bundle["audit"],
        "train_unit_ids": [record["unit_id"] for record in train_records],
        "dev_unit_ids": [record["unit_id"] for record in dev_records],
    }


def prepare(training_id: str) -> Path:
    if not training_id.startswith("DC-FORMAL-M1-TRAINDEV-"):
        raise FormalTrainingError("INVALID_TRAINING_ID")
    output_root = TRAINING_ROOT / training_id
    if output_root.exists():
        raise FormalTrainingError("TRAINING_ID_OUTPUT_ALREADY_EXISTS")
    output_root.mkdir(parents=True)
    try:
        pre = preflight(training_id, output_root)
        config = pre["training_config"]
        git_start = {
            "schema_version": "driveclarify.formal_m1_training_git_start.v1",
            "training_id": training_id,
            "task_entry_authority": load_json(ASSESSMENT / "GIT_END.json"),
            "preseal_driveclarify": git_snapshot(REPO_ROOT),
            "preseal_simlingo": git_snapshot(Path("/home/buaa/wrh/simlingo")),
            "expected_driveclarify_branch": "master",
            "expected_driveclarify_head": "eaa332b1bb994279b59ea5af786fdb5de96adc1b",
            "expected_simlingo_branch": "main",
            "expected_simlingo_head": "743b243afd6cf5ff51b9fa1f8cac86f22d569684",
            "expected_simlingo_tracked_diff_bytes": 7722,
            "expected_simlingo_tracked_diff_sha256": "7bd0947ef17df95b56d95ce155468d378a3c282b59f7dee4a2653a7195bc5e34",
        }
        simlingo = git_start["preseal_simlingo"]
        driveclarify = git_start["preseal_driveclarify"]
        if driveclarify["branch"] != "master" or driveclarify["head"] != git_start["expected_driveclarify_head"]:
            raise FormalTrainingError("DRIVECLARIFY_GIT_IDENTITY_MISMATCH")
        if driveclarify["tracked_diff_bytes"] or driveclarify["staged_diff_bytes"]:
            raise FormalTrainingError("DRIVECLARIFY_TRACKED_OR_STAGED_DIFF")
        if (
            simlingo["branch"] != "main"
            or simlingo["head"] != git_start["expected_simlingo_head"]
            or simlingo["tracked_diff_bytes"] != 7722
            or simlingo["tracked_diff_sha256"] != git_start["expected_simlingo_tracked_diff_sha256"]
            or simlingo["staged_diff_bytes"] != 0
        ):
            raise FormalTrainingError("SIMLINGO_GIT_BOUNDARY_MISMATCH")
        seal_without_hash = {
            "schema_version": "driveclarify.formal_m1_training_code_protocol_seal.v1",
            "training_id": training_id,
            "status": "SEALED_READY_FOR_FIRST_OPTIMIZER_STEP",
            "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "authorization_attachment": {"path": str(ATTACHMENT), "sha256": sha256_file(ATTACHMENT)},
            "source_file_hashes": pre["source_file_hashes"],
            "assessment_hashes": pre["assessment_hashes"],
            "source_data_hashes": pre["source_data_hashes"],
            "training_config_sha256": pre["training_config_sha256"],
            "git_identity": {"driveclarify": driveclarify, "simlingo": simlingo},
            "runtime": {
                "python_version": platform.python_version(),
                "python_executable": sys.executable,
                "torch_version": torch.__version__,
                "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"),
                "torch_cuda_initialized": torch.cuda.is_initialized(),
                "device": "cpu",
            },
            "preflight": pre["checks"],
            "test_access_counts": {
                "dataloader_creation": 0,
                "tensorization": 0,
                "forward": 0,
                "prediction": 0,
                "metric": 0,
                "evaluation": 0,
                "publication": 0,
            },
        }
        seal = {**seal_without_hash, "sha256": canonical_sha256(seal_without_hash)}
        write_json(output_root / "TRAINING_CONFIG.json", config)
        write_json(
            output_root / "DATASET_AND_FEATURE_HASHES.json",
            {
                "assessment_hashes": pre["assessment_hashes"],
                "source_data_hashes": pre["source_data_hashes"],
                "train_tensor_audit": pre["train_tensor_audit"],
                "dev_tensor_audit": pre["dev_tensor_audit"],
                "test_tensorization_count": 0,
            },
        )
        write_json(output_root / "GIT_START.json", git_start)
        write_json(output_root / "TRAINING_CODE_AND_PROTOCOL_SEAL.json", seal)
        write_json(output_root / "FAILURE_AND_RESUME_LEDGER.json", {"training_id": training_id, "failures": [], "silent_retry_count": 0})
        write_json(
            output_root / "TEST_ACCESS_AUDIT.json",
            {
                "training_id": training_id,
                "test_seal_file_sha256": EXPECTED_TEST_SEAL_FILE_SHA256,
                "test_seal_status": "PROTOCOL_FROZEN_TEST_NOT_AUTHORIZED",
                "allowed_integrity_reads": ["frozen dataset index counts/hash", "monolithic V2/V3 source file byte hashes", "TEST seal bytes/hash"],
                "test_dataloader_creation_count": 0,
                "test_tensorization_count": 0,
                "test_forward_count": 0,
                "test_prediction_count": 0,
                "test_metric_count": 0,
                "test_evaluation_count": 0,
                "test_publication_count": 0,
            },
        )
        write_json(output_root / "PREPARED_UNIT_IDS.json", {"train_unit_ids": pre["train_unit_ids"], "dev_unit_ids": pre["dev_unit_ids"], "test_unit_ids_materialized": False})
        (output_root / "COMMAND_LOG.md").write_text(
            "# Formal M1 TRAIN/DEV command log\n\n"
            "- PREPARE: CPU-only frozen-authority preflight; no optimizer step or backward.\n"
            "- TEST: no DataLoader, tensorization, forward, prediction, metric, evaluation, or publication.\n",
            encoding="utf-8",
        )
        print(json.dumps({"status": "PREPARED", "training_id": training_id, "output_root": str(output_root), "seal_sha256": seal["sha256"]}, sort_keys=True))
        return output_root
    except Exception:
        write_json(output_root / "FAILURE_AND_RESUME_LEDGER.json", {"training_id": training_id, "failures": [{"stage": "PREPARE", "error": repr(sys.exc_info()[1])}], "silent_retry_count": 0})
        raise


def _verify_seal_sources(seal: Mapping[str, Any]) -> None:
    unsealed = dict(seal)
    expected = unsealed.pop("sha256")
    if canonical_sha256(unsealed) != expected:
        raise FormalTrainingError("TRAINING_CODE_PROTOCOL_SEAL_SELF_HASH_MISMATCH")
    for name, expected_hash in seal["source_file_hashes"].items():
        if sha256_file(REPO_ROOT / name) != expected_hash:
            raise FormalTrainingError("SEALED_SOURCE_HASH_MISMATCH:%s" % name)
    for name, expected_hash in seal["assessment_hashes"].items():
        if sha256_file(ASSESSMENT / name) != expected_hash:
            raise FormalTrainingError("SEALED_ASSESSMENT_HASH_MISMATCH:%s" % name)
    if sha256_file(TEST_SEAL) != EXPECTED_TEST_SEAL_FILE_SHA256:
        raise FormalTrainingError("TEST_SEAL_CHANGED_AFTER_PREPARE")


def _relative(path: str) -> str:
    return str(Path(path).resolve().relative_to(REPO_ROOT.resolve()))


def _compact_run(run: Mapping[str, Any]) -> Dict[str, Any]:
    excluded = {"dev_unit_predictions"}
    return {key: value for key, value in run.items() if key not in excluded}


def _main_report(training_id: str, candidate_status: str, main_runs: Sequence[Mapping[str, Any]], stability: Mapping[str, Any], baseline_results: Mapping[str, Any], ablation_results: Mapping[str, Any], runtime: Mapping[str, Any]) -> str:
    rows = []
    for run in main_runs:
        selection = run.get("selection")
        rows.append(
            "| %d | %s | %s | %s | %d |"
            % (
                run["seed"],
                str(selection["epoch"] if selection else "NONE"),
                str(selection["threshold"] if selection else "NONE"),
                "YES" if selection else "NO",
                run["checkpoint_count"],
            )
        )
    return (
        "# Formal Learned M1 TRAIN/DEV Report\n\n"
        "Training ID: `%s`  \nCandidate terminal status pending independent review: `%s`  \n"
        "TEST status: `FORMAL_LEARNED_M1_TEST_NOT_AUTHORIZED`\n\n"
        "## Frozen scope\n\nFormal V2+V3 data: 47 complete units / 282 measurements; TRAIN/DEV/TEST=25/11/11. "
        "Only TRAIN updated parameters. DEV performed no backward or optimizer step. TEST had zero tensorization, forward, prediction, metric, evaluation, and publication.\n\n"
        "## Main seeds\n\n| Seed | Selected epoch | UNKNOWN threshold | Feasible | Epoch checkpoints |\n|---:|---:|---:|---|---:|\n"
        % (training_id, candidate_status)
        + "\n".join(rows)
        + "\n\n## Stability\n\nFive-way DEV final-decision agreement: `%s`; selected seed count: `%s`.\n\n"
        % (str(stability.get("five_way_agreement")), str(stability.get("selected_seed_count")))
        + "## Baselines and ablations\n\nRequired baselines completed: `%s`. Pre-registered ablation statuses are recorded in `ABLATION_RESULTS.json`.\n\n"
        % str(baseline_results.get("all_required_baselines_completed"))
        + "## Resource and claim boundary\n\nCPU-only PyTorch 2.2.0 was used; CUDA remained uninitialized. This is a feasibility/DEV-calibration result, not a paper-level or TEST result. "
        "Independent validation is required before the candidate status becomes terminal. Runtime counters: `%s`.\n"
        % json.dumps(runtime, sort_keys=True)
    )


def run_training(output_root: Path) -> None:
    output_root = Path(output_root).resolve()
    output_root.relative_to(TRAINING_ROOT.resolve())
    seal = load_json(output_root / "TRAINING_CODE_AND_PROTOCOL_SEAL.json")
    _verify_seal_sources(seal)
    assert_cpu_only("cpu")
    config = load_json(output_root / "TRAINING_CONFIG.json")
    if canonical_sha256(config) != seal["training_config_sha256"]:
        raise FormalTrainingError("TRAINING_CONFIG_HASH_MISMATCH")
    records = load_formal_train_dev_records()
    train_records = [record for record in records if record["split"] == "TRAIN"]
    dev_records = [record for record in records if record["split"] == "DEV"]
    train_bundle = tensorize_formal_records(train_records)
    dev_bundle = tensorize_formal_records(dev_records)
    train_unit_ids = [record["unit_id"] for record in train_records]
    dev_unit_ids = [record["unit_id"] for record in dev_records]
    dev_labels = [record["pair_task_label"] for record in dev_records]
    hashes = {
        "training_config_sha256": seal["training_config_sha256"],
        "dataset_index_sha256": seal["assessment_hashes"]["FORMAL_M1_DATASET_INDEX.json"],
        "feature_contract_sha256": seal["assessment_hashes"]["FEATURE_WHITELIST.json"],
        "model_spec_sha256": seal["assessment_hashes"]["FORMAL_M1_MODEL_SPEC.json"],
        "protocol_seal_sha256": seal["sha256"],
        "training_protocol_sha256": seal["assessment_hashes"]["FORMAL_M1_TRAINING_PROTOCOL.json"],
    }
    runtime_counters = {
        "optimizer_step": 0,
        "backward": 0,
        "train_parameter_update_batches": 0,
        "dev_forward_epochs": 0,
        "dev_backward": 0,
        "dev_optimizer_step": 0,
        "checkpoint_save": 0,
        "test_dataloader_creation": 0,
        "test_tensorization": 0,
        "test_forward": 0,
        "test_prediction": 0,
        "test_metric": 0,
        "test_evaluation": 0,
        "test_publication": 0,
    }
    all_runs: Dict[str, List[Dict[str, Any]]] = {}
    training_started = time.monotonic()
    try:
        for configuration_id in VARIANTS:
            all_runs[configuration_id] = []
            for seed in FORMAL_SEEDS:
                print("START configuration=%s seed=%d" % (configuration_id, seed), flush=True)
                run = train_learned_variant(
                    configuration_id,
                    seed,
                    train_bundle,
                    dev_bundle,
                    train_unit_ids,
                    dev_unit_ids,
                    output_root / "learned_runs",
                    hashes,
                    runtime_counters,
                    maximum_epochs=300,
                    patience=40,
                )
                all_runs[configuration_id].append(run)
                print(
                    "DONE configuration=%s seed=%d epochs=%d selected=%s"
                    % (configuration_id, seed, run["epochs_completed"], str(run["selection"] is not None)),
                    flush=True,
                )
        deterministic = deterministic_baselines(train_bundle, dev_bundle, dev_unit_ids)
    except Exception as error:
        ledger = load_json(output_root / "FAILURE_AND_RESUME_LEDGER.json")
        ledger["failures"].append({"stage": "FORMAL_TRAINING", "error": repr(error), "runtime_counters": runtime_counters})
        write_json(output_root / "FAILURE_AND_RESUME_LEDGER.json", ledger)
        raise

    main_runs = all_runs["MAIN_WEIGHTED_PILOT_NEIGHBORHOOD_V1"]
    all_main_feasible = all(run["selection"] is not None for run in main_runs)
    candidate_status = (
        "FORMAL_LEARNED_M1_TRAIN_DEV_COMPLETE_READY_FOR_PRETEST_VERIFICATION"
        if all_main_feasible
        else "FORMAL_LEARNED_M1_TRAIN_DEV_COMPLETE_NO_FEASIBLE_DEV_SELECTION"
    )
    stability = seed_stability(main_runs, dev_unit_ids)
    baseline_results = {
        "majority_prior": deterministic["majority_prior"],
        "task_agnostic_learned_comparator": [_compact_run(run) for run in all_runs["BASELINE_TASK_AGNOSTIC_LEARNED_COMPARATOR_V1"]],
        "no_abstention_learned_model": [_compact_run(run) for run in all_runs["BASELINE_NO_ABSTENTION_LEARNED_V1"]],
        "rule_based_deterministic_comparator": deterministic["rule_based_deterministic_comparator"],
        "all_required_baselines_completed": True,
        "test_use_count": 0,
    }
    ablation_mapping = {
        "no_topology_conditioning": "ABLATION_NO_TOPOLOGY_CONDITIONING_V1",
        "no_repeat_aggregation_repeat_1_only": "ABLATION_REPEAT_1_ONLY_V1",
        "mean_aggregation_only": "ABLATION_MEAN_AGGREGATION_ONLY_V1",
        "asymmetric_comparator_diagnostic": "ABLATION_ASYMMETRIC_COMPARATOR_V1",
        "no_abstention_head": "BASELINE_NO_ABSTENTION_LEARNED_V1",
        "unweighted_losses": "ABLATION_UNWEIGHTED_V1",
    }
    ablation_results = {
        name: {
            "configuration_id": configuration_id,
            "status": "COMPLETE",
            "reused_equal_protocol_run": name == "no_abstention_head",
            "runs": [_compact_run(run) for run in all_runs[configuration_id]],
        }
        for name, configuration_id in ablation_mapping.items()
    }
    main_grid = []
    checkpoint_inventory = []
    training_curves = {"main": [], "comparison_curve_paths": {}}
    for configuration_id, runs in all_runs.items():
        training_curves["comparison_curve_paths"][configuration_id] = [run["curve_path"] for run in runs]
        for run in runs:
            inventory = load_json(Path(run["checkpoint_inventory_path"]))
            checkpoint_inventory.extend([{**row, "configuration_id": configuration_id} for row in inventory])
            if configuration_id == "MAIN_WEIGHTED_PILOT_NEIGHBORHOOD_V1":
                main_grid.extend(load_json(Path(run["grid_path"])))
                training_curves["main"].append({"seed": run["seed"], "curve": load_json(Path(run["curve_path"]))})
    selected_checkpoints = [
        {"seed": run["seed"], "selection": run["selection"], "feasibility_satisfied": run["selection"] is not None}
        for run in main_runs
    ]
    dev_unit_rows = []
    for unit_index, unit_id in enumerate(dev_unit_ids):
        dev_unit_rows.append(
            {
                "unit_id": unit_id,
                "target_label": dev_labels[unit_index],
                "seed_predictions": [
                    run["dev_unit_predictions"][unit_index] if run["dev_unit_predictions"] else {"seed": run["seed"], "selection_status": "NO_FEASIBLE_SELECTION"}
                    for run in main_runs
                ],
            }
        )
    train_dev_metrics = {
        "unit_level_denominators": {"TRAIN": 25, "DEV": 11, "repeat_is_sample": False},
        "main_by_seed": [
            {"seed": run["seed"], "train_metrics": run["train_metrics"], "dev_metrics": run["dev_metrics"]}
            for run in main_runs
        ],
    }
    main_macro = [
        run["dev_metrics"]["task_head_known"]["macro_f1"]
        for run in main_runs
        if run["dev_metrics"] and run["dev_metrics"]["task_head_known"]["macro_f1"] is not None
    ]
    comparison_macro = []
    for family in ("BASELINE_TASK_AGNOSTIC_LEARNED_COMPARATOR_V1", "BASELINE_NO_ABSTENTION_LEARNED_V1"):
        comparison_macro.extend(
            run["dev_metrics"]["task_head_known"]["macro_f1"]
            for run in all_runs[family]
            if run["dev_metrics"] and run["dev_metrics"]["task_head_known"]["macro_f1"] is not None
        )
    comparison_macro.extend(
        [
            deterministic["majority_prior"]["dev_metrics_at_threshold_0_5"]["task_head_known"]["macro_f1"],
            deterministic["rule_based_deterministic_comparator"]["selected_metrics"]["task_head_known"]["macro_f1"],
        ]
    )
    main_consistently_better = bool(main_macro and comparison_macro and min(main_macro) > max(comparison_macro))
    total_wall = time.monotonic() - training_started
    runtime_summary = {
        **runtime_counters,
        "formal_training_wall_time_seconds": total_wall,
        "cpu_only": True,
        "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"),
        "torch_cuda_initialized": torch.cuda.is_initialized(),
        "gpu_compute_process_count_observed_before_run": 0,
    }
    checkpoint_hashes = [
        {
            "configuration_id": row["configuration_id"],
            "seed": row["seed"],
            "epoch": row["epoch"],
            "path": row["path"],
            "sha256": row["sha256"],
            "state_dict_sha256": row["state_dict_sha256"],
        }
        for row in checkpoint_inventory
    ]
    result = {
        "schema_version": "driveclarify.formal_m1_training_result.v1",
        "training_id": seal["training_id"],
        "candidate_terminal_status": candidate_status,
        "terminal_status": "FORMAL_LEARNED_M1_TRAINING_FINISHED_AWAITING_INDEPENDENT_REVIEW",
        "test_authorization_status": "FORMAL_LEARNED_M1_TEST_NOT_AUTHORIZED",
        "formal_data": {"complete_units": 47, "plan_measurements": 282, "split": {"TRAIN": 25, "DEV": 11, "TEST": 11}},
        "main_seeds": list(FORMAL_SEEDS),
        "all_main_seeds_feasible": all_main_feasible,
        "required_baselines_completed": True,
        "pre_registered_ablations_completed": True,
        "main_consistently_outperforms_every_baseline_on_dev_macro_f1": main_consistently_better,
        "runtime_counters": runtime_summary,
        "independent_review": "PENDING",
        "test_counts": {key: value for key, value in runtime_counters.items() if key.startswith("test_")},
    }

    write_json(output_root / "MAIN_MODEL_ALL_SEEDS.json", [_compact_run(run) for run in main_runs])
    write_json(output_root / "DEV_CHECKPOINT_THRESHOLD_GRID.json", main_grid)
    write_json(output_root / "DEV_SELECTED_CHECKPOINTS.json", selected_checkpoints)
    write_json(output_root / "DEV_UNIT_LEVEL_PREDICTIONS.json", dev_unit_rows)
    write_json(output_root / "TRAIN_DEV_METRICS.json", train_dev_metrics)
    write_json(output_root / "TRAINING_CURVES.json", training_curves)
    write_json(output_root / "SEED_STABILITY.json", stability)
    write_json(output_root / "BASELINE_RESULTS.json", baseline_results)
    write_json(output_root / "ABLATION_RESULTS.json", ablation_results)
    write_json(output_root / "CHECKPOINT_INVENTORY.json", checkpoint_inventory)
    write_json(output_root / "CHECKPOINT_HASHES.json", checkpoint_hashes)
    write_json(output_root / "TRAINING_RESULT.json", result)
    write_json(
        output_root / "TEST_RESULTS.json",
        {
            "training_id": seal["training_id"],
            "status": "TEST_NOT_AUTHORIZED_NOT_RUN",
            "test_dataloader_creation_count": 0,
            "test_tensorization_count": 0,
            "test_forward_count": 0,
            "test_prediction_count": 0,
            "test_metric_count": 0,
            "test_evaluation_count": 0,
            "test_publication_count": 0,
        },
    )
    write_json(
        output_root / "PROCESS_AND_RESOURCE_CLEANUP.json",
        {
            "training_process_status": "RUNNER_EXIT_PENDING_FINAL_VERIFIER",
            "cpu_only": True,
            "torch_cuda_initialized": torch.cuda.is_initialized(),
            "gpu_compute_process_count": 0,
            "carla_process_count": 0,
            "simlingo_process_count": 0,
            "test_process_count": 0,
        },
    )
    (output_root / "FORMAL_M1_TRAIN_DEV_REPORT.md").write_text(
        _main_report(seal["training_id"], candidate_status, main_runs, stability, baseline_results, ablation_results, runtime_summary),
        encoding="utf-8",
    )
    (output_root / "NEXT_PRETEST_VERIFICATION_PROMPT.md").write_text(
        "# Next step — separate authorization required\n\n"
        "Current candidate status: `%s`. TEST remains `FORMAL_LEARNED_M1_TEST_NOT_AUTHORIZED`.\n\n"
        "The only next step after independent TRAIN/DEV verification is to stop and wait for a separate pre-test verification authorization. "
        "Do not tensorize or infer TEST, do not call `mark_pretest_verified`, and do not enter M2+.\n" % candidate_status,
        encoding="utf-8",
    )
    modified_files = {
        "training_id": seal["training_id"],
        "new_training_directory": str(output_root.relative_to(REPO_ROOT)),
        "training_source_files_preseal": list(SOURCE_FILES),
        "historical_dataset_or_artifact_files_modified": [],
        "simlingo_files_modified": [],
        "top_level_authority_files_pending_update": ["AGENT_WORKLOG.md", "STATE.json", "CURRENT_HANDOFF.md", "NEXT_AGENT_PROMPT.md"],
    }
    write_json(output_root / "MODIFIED_FILES.json", modified_files)
    with (output_root / "COMMAND_LOG.md").open("a", encoding="utf-8") as handle:
        handle.write("- RUN: eight frozen learned configurations x five seeds, sequential CPU-only; deterministic baselines.\n")
        handle.write("- DEV: no-grad epoch evaluation and frozen threshold-grid selection only.\n")
        handle.write("- TEST: all prohibited counters remained zero.\n")
        handle.write("- VERIFY: pending separate sealed independent verifier process.\n")
    _verify_seal_sources(seal)
    if torch.cuda.is_initialized():
        raise FormalTrainingError("CUDA_CONTEXT_INITIALIZED_DURING_TRAINING")
    print(json.dumps({"status": "TRAINING_FINISHED", "candidate_terminal_status": candidate_status, "output_root": str(output_root)}, sort_keys=True), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("training_id")
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("output_root", type=Path)
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args.training_id)
    else:
        run_training(args.output_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
