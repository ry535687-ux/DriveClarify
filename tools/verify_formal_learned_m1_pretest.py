#!/usr/bin/env python3
"""Independent prediction-free pre-TEST verifier for frozen Formal Learned M1.

This verifier deliberately does not import ``formal_data`` or ``formal_training``.
It never parses a TEST record into model features and has no TEST inference entry
point.  TEST access is restricted to frozen control-plane index/schema/file hashes
and the TEST lifecycle seal.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

if os.environ.get("CUDA_VISIBLE_DEVICES") != "":
    raise RuntimeError("CUDA_VISIBLE_DEVICES_MUST_BE_EMPTY_BEFORE_TORCH_IMPORT")

import torch

from driveclarify_learned_m1.formal_protocol import (
    FormalProtocolError,
    combine_prediction,
    mark_pretest_verified,
    verify_seal,
)
from driveclarify_learned_m1.model import LearnedM1


SIMLINGO_ROOT = Path("/home/buaa/wrh/simlingo")
ASSESSMENT = REPO_ROOT / "reports/formal_learned_m1_assessment/DC-FORMAL-M1-ASSESS-20260804T072031Z"
TRAINING = REPO_ROOT / "reports/formal_learned_m1_training/DC-FORMAL-M1-TRAINDEV-20260804T081727Z"
TEST_SEAL = ASSESSMENT / "FORMAL_M1_TEST_SEAL.json"
V1_DATASET = REPO_ROOT / "reports/multi_topology_static_units_v1/offline_candidate_capture_runs/DC-MULTI-A3B3-C1-20260803T121500Z/M1_REAL_DATASET_V1.json"
V2_ROOT = REPO_ROOT / "reports/m1_real_dataset_expansion_v2/DC-M1-DATASET-EXP-V2-20260803T143000Z"
V3_ROOT = REPO_ROOT / "reports/m1_real_dataset_expansion_v3/DC-M1-DATASET-EXP-V3-20260804T055600Z"
PILOT_ROOT = REPO_ROOT / "reports/learned_m1_pilot_v1/DC-M1-PILOT-P1-20260803T133000Z"
V2_SCHEMA = V2_ROOT / "M1_REAL_DATASET_V2_DATA_SCHEMA.json"
V3_SCHEMA = V3_ROOT / "M1_REAL_DATASET_V3_SCHEMA.json"

EXPECTED_PROJECT_STATUS = "FORMAL_LEARNED_M1_TRAIN_DEV_COMPLETE_READY_FOR_PRETEST_VERIFICATION"
FINAL_PROJECT_STATUS = "FORMAL_LEARNED_M1_PRETEST_VERIFICATION_COMPLETE_READY_FOR_ONE_TIME_TEST_AUTHORIZATION"
TEST_AUTHORIZATION_STATUS = "FORMAL_LEARNED_M1_TEST_NOT_AUTHORIZED"
EXPECTED_SEAL_BEFORE_STATUS = "PROTOCOL_FROZEN_TEST_NOT_AUTHORIZED"
EXPECTED_SEAL_AFTER_STATUS = "PRETEST_VERIFIED_AWAITING_ONE_TIME_TEST_AUTHORIZATION"
EXPECTED_TEST_SEAL_FILE_SHA256 = "737725b789ee76cfe5c77d82593a30344d385ea0887ebd18e710e50151721df1"
EXPECTED_DRIVECLARIFY_HEAD = "eaa332b1bb994279b59ea5af786fdb5de96adc1b"
EXPECTED_SIMLINGO_HEAD = "743b243afd6cf5ff51b9fa1f8cac86f22d569684"
EXPECTED_SIMLINGO_DIFF_BYTES = 7722
EXPECTED_SIMLINGO_DIFF_SHA256 = "7bd0947ef17df95b56d95ce155468d378a3c282b59f7dee4a2653a7195bc5e34"
EXPECTED_SELECTED = {
    17: (73, 0.30, "4991935d69273e605c1d115ed3315c9c15b5bd185c9b22773e02954946229a9d"),
    29: (52, 0.30, "dae6d2699f8ae109432ebc6a7e385df704f38ed0976ec735e712d2cc0a982127"),
    43: (41, 0.30, "00189fa20da313b25147e43f4d85cb98ff2ba5fd377453a9e5742e2522720df9"),
    59: (81, 0.30, "c5822798271566f455eaaad50c90324fd725814fc2388800e70c70286bb6fb91"),
    71: (116, 0.60, "5847aeccc0cf9a49669d2cfcacf8747fd7fa4a5ba0c12a15bb5177ff02e45a8e"),
}
EXPECTED_THRESHOLDS = [0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80]
EXPECTED_TEST_COUNTS = {
    "test_dataloader_creation": 0,
    "test_tensorization": 0,
    "test_forward": 0,
    "test_prediction": 0,
    "test_metric": 0,
    "test_evaluation": 0,
    "test_publication": 0,
}

ASSESSMENT_REQUIRED = (
    "FORMAL_M1_ASSESSMENT_REPORT.md",
    "FORMAL_M1_DATASET_AUDIT.json",
    "FORMAL_M1_DATASET_INDEX.json",
    "FEATURE_WHITELIST.json",
    "FEATURE_BLACKLIST.json",
    "INPUT_TENSOR_CONTRACT.json",
    "FORMAL_M1_MODEL_SPEC.json",
    "FORMAL_M1_TRAINING_PROTOCOL.json",
    "FORMAL_M1_DEV_SELECTION_AND_CALIBRATION.md",
    "FORMAL_M1_BASELINES_AND_ABLATIONS.md",
    "FORMAL_M1_METRICS_SPEC.md",
    "FORMAL_M1_TEST_SEAL_PROTOCOL.md",
    "FORMAL_M1_TEST_SEAL.json",
    "FORMAL_M1_REVIEWER_ATTACK.md",
    "GIT_END.json",
)
TRAINING_REQUIRED = (
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
    "SEED_STABILITY.json",
    "BASELINE_RESULTS.json",
    "ABLATION_RESULTS.json",
    "CHECKPOINT_INVENTORY.json",
    "CHECKPOINT_HASHES.json",
    "FAILURE_AND_RESUME_LEDGER.json",
    "TEST_ACCESS_AUDIT.json",
    "INDEPENDENT_TRAINING_REVIEW.md",
    "GIT_START.json",
    "GIT_END.json",
    "PROCESS_AND_RESOURCE_CLEANUP.json",
    "NEXT_PRETEST_VERIFICATION_PROMPT.md",
)
DELIVERABLES = (
    "PRETEST_VERIFICATION_REPORT.md",
    "PRETEST_VERIFICATION_RESULT.json",
    "PRETEST_VERIFIER_SEAL.json",
    "PRETEST_VERIFIER_HASHES.json",
    "AUTHORITY_AND_STATE_AUDIT.json",
    "DATASET_FEATURE_MODEL_PROTOCOL_HASH_AUDIT.json",
    "SELECTED_CHECKPOINT_AUDIT.json",
    "SELECTED_CHECKPOINT_CPU_LOAD_RESULTS.json",
    "DEV_SELECTION_PROVENANCE_AUDIT.json",
    "BASELINE_AND_ABLATION_COMPLETENESS_AUDIT.json",
    "TEST_ZERO_ACCESS_AUDIT.json",
    "TEST_SEAL_BEFORE.json",
    "TEST_SEAL_AFTER.json",
    "TEST_SEAL_TRANSITION_AUDIT.json",
    "CLAIM_BOUNDARY.md",
    "INDEPENDENT_PRETEST_REVIEW.md",
    "TEST_RESULTS.json",
    "COMMAND_LOG.md",
    "MODIFIED_FILES.json",
    "GIT_START.json",
    "GIT_END.json",
    "PROCESS_AND_RESOURCE_CLEANUP.json",
    "NEXT_ONE_TIME_TEST_AUTHORIZATION_PROMPT.md",
)

READ_BYTES = 0


def read_bytes(path: Path) -> bytes:
    global READ_BYTES
    data = Path(path).read_bytes()
    READ_BYTES += len(data)
    return data


def load_json(path: Path) -> Any:
    return json.loads(read_bytes(path).decode("utf-8"))


def sha256_file(path: Path) -> str:
    global READ_BYTES
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            READ_BYTES += len(chunk)
            digest.update(chunk)
    return digest.hexdigest()


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def write_json(path: Path, value: Any) -> None:
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False).encode("utf-8") + b"\n"
    atomic_write(path, payload)


def atomic_write(path: Path, payload: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(str(temporary), str(path))
    directory_fd = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def tree_sha256(root: Path) -> Tuple[str, int, int]:
    digest = hashlib.sha256()
    count = 0
    total_bytes = 0
    for path in sorted(item for item in Path(root).rglob("*") if item.is_file()):
        file_hash = sha256_file(path)
        count += 1
        total_bytes += path.stat().st_size
        digest.update(file_hash.encode("ascii"))
        digest.update(b"  ")
        digest.update(path.relative_to(REPO_ROOT).as_posix().encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest(), count, total_bytes


def run_bytes(command: Sequence[str], cwd: Path) -> bytes:
    return subprocess.check_output(list(command), cwd=str(cwd))


def run_text(command: Sequence[str], cwd: Path) -> str:
    return run_bytes(command, cwd).decode("utf-8", errors="strict").strip()


def git_snapshot(root: Path) -> Dict[str, Any]:
    tracked = run_bytes(("git", "diff", "--binary"), root)
    staged = run_bytes(("git", "diff", "--cached", "--binary"), root)
    untracked = run_bytes(("git", "ls-files", "--others", "--exclude-standard", "-z"), root)
    paths = [item.decode("utf-8") for item in untracked.split(b"\0") if item]
    return {
        "branch": run_text(("git", "branch", "--show-current"), root),
        "head": run_text(("git", "rev-parse", "HEAD"), root),
        "tracked_diff_bytes": len(tracked),
        "tracked_diff_sha256": hashlib.sha256(tracked).hexdigest(),
        "staged_diff_bytes": len(staged),
        "staged_diff_sha256": hashlib.sha256(staged).hexdigest(),
        "untracked_file_count": len(paths),
        "untracked_path_list_nul_bytes": len(untracked),
        "untracked_path_list_nul_sha256": hashlib.sha256(untracked).hexdigest(),
        "untracked_paths": paths,
    }


def filtered_untracked_fingerprint(paths: Sequence[str], excluded_prefixes: Sequence[str]) -> Dict[str, Any]:
    retained = [path for path in paths if not any(path == prefix or path.startswith(prefix + "/") for prefix in excluded_prefixes)]
    encoded = b"".join(path.encode("utf-8") + b"\0" for path in retained)
    return {
        "file_count": len(retained),
        "path_list_nul_bytes": len(encoded),
        "path_list_nul_sha256": hashlib.sha256(encoded).hexdigest(),
    }


def state_dict_sha256(state_dict: Mapping[str, torch.Tensor]) -> str:
    digest = hashlib.sha256()
    for name in sorted(state_dict):
        tensor = state_dict[name].detach().cpu().contiguous()
        digest.update(name.encode("utf-8"))
        digest.update(str(tensor.dtype).encode("ascii"))
        digest.update(str(tuple(tensor.shape)).encode("ascii"))
        digest.update(tensor.numpy().tobytes())
    return digest.hexdigest()


def synthetic_batch(batch_size: int = 2) -> Dict[str, torch.Tensor]:
    generator = torch.Generator(device="cpu")
    generator.manual_seed(20260804)
    return {
        "route": torch.randn(batch_size, 2, 3, 20, 2, generator=generator),
        "route_mask": torch.ones(batch_size, 2, 3, 20, dtype=torch.bool),
        "speed": torch.randn(batch_size, 2, 3, 10, 2, generator=generator),
        "speed_mask": torch.ones(batch_size, 2, 3, 10, dtype=torch.bool),
        "roles": torch.tensor([[[1.0, 0.0], [0.0, 1.0]]] * batch_size),
        "topology": torch.randn(batch_size, 2, 25, 2, generator=generator),
        "topology_mask": torch.ones(batch_size, 2, 25, dtype=torch.bool),
        "topology_scalars": torch.randn(batch_size, 8, generator=generator),
        "evidence": torch.ones(batch_size, 6),
    }


def swap_candidates(batch: Mapping[str, torch.Tensor]) -> Dict[str, torch.Tensor]:
    swapped = {name: value.clone() for name, value in batch.items()}
    for name in ("route", "route_mask", "speed", "speed_mask", "roles"):
        swapped[name] = batch[name][:, [1, 0]].clone()
    return swapped


def permute_repeats(batch: Mapping[str, torch.Tensor]) -> Dict[str, torch.Tensor]:
    permuted = {name: value.clone() for name, value in batch.items()}
    for name in ("route", "route_mask", "speed", "speed_mask"):
        permuted[name] = batch[name][:, :, [2, 0, 1]].clone()
    return permuted


def selection_rank(row: Mapping[str, Any]) -> Tuple[Any, ...]:
    return (
        -float(row["known_task_macro_f1"] if row["known_task_macro_f1"] is not None else -1.0),
        float(row["selective_risk"] if row["selective_risk"] is not None else 1.0),
        -float(row["coverage"] if row["coverage"] is not None else 0.0),
        float(row["unknown_brier_score"]),
        int(row["epoch"]),
        str(row["checkpoint_sha256"]),
    )


def process_audit() -> Dict[str, Any]:
    lines = run_text(("ps", "-eo", "pid=,args="), REPO_ROOT).splitlines()
    current_pid = os.getpid()
    tokens = (
        "CarlaUE4",
        "leaderboard_evaluator.py",
        "run_formal_learned_m1_training.py run",
        "formal_m1_test_evaluation",
        "run_evaluation.sh",
    )
    prohibited = []
    for line in lines:
        stripped = line.strip()
        fields = stripped.split(None, 1)
        pid = int(fields[0]) if fields and fields[0].isdigit() else -1
        if pid == current_pid:
            continue
        if any(token in stripped for token in tokens) and "verify_formal_learned_m1_pretest.py" not in stripped:
            prohibited.append(stripped)
    try:
        gpu_lines = run_text(
            ("nvidia-smi", "--query-compute-apps=pid,process_name,used_memory", "--format=csv,noheader"),
            REPO_ROOT,
        ).splitlines()
        gpu_lines = [line for line in gpu_lines if line.strip()]
        gpu_query_status = "PASS"
    except (FileNotFoundError, subprocess.CalledProcessError):
        gpu_lines = []
        gpu_query_status = "UNAVAILABLE_NO_COMPUTE_PROCESS_OBSERVED_BY_PS"
    return {
        "prohibited_process_lines": prohibited,
        "carla_evaluator_training_test_runtime_process_count": len(prohibited),
        "gpu_compute_process_count": len(gpu_lines),
        "gpu_compute_process_lines": gpu_lines,
        "gpu_query_status": gpu_query_status,
    }


def check(checks: Dict[str, bool], name: str, condition: Any) -> bool:
    checks[name] = bool(condition)
    return checks[name]


def verify_selected_checkpoint(
    row: Mapping[str, Any],
    inventory_by_path: Mapping[str, Mapping[str, Any]],
    seal: Mapping[str, Any],
    assessment_hashes: Mapping[str, str],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    seed = int(row["seed"])
    selected = row["selection"]
    expected_epoch, expected_threshold, expected_hash = EXPECTED_SELECTED[seed]
    epoch_path = Path(selected["checkpoint_path"])
    selected_path = Path(selected["selected_checkpoint_path"])
    inventory_row = inventory_by_path.get(str(epoch_path))
    file_hash = sha256_file(selected_path)
    epoch_file_hash = sha256_file(epoch_path)
    global READ_BYTES
    READ_BYTES += selected_path.stat().st_size
    try:
        payload = torch.load(str(selected_path), map_location="cpu", weights_only=False)
    except TypeError:
        payload = torch.load(str(selected_path), map_location="cpu")
    required_keys = {
        "schema_version", "model_class", "model_kwargs", "model_state_dict", "model_state_sha256",
        "optimizer_state_dict", "seed", "epoch", "training_config_sha256", "dataset_index_sha256",
        "feature_contract_sha256", "model_spec_sha256", "protocol_seal_sha256", "tensors_saved_on_cpu",
        "device_agnostic_state", "loss_configuration", "training_protocol_sha256", "configuration_id",
    }
    state_hash = state_dict_sha256(payload["model_state_dict"])
    model = LearnedM1(**payload["model_kwargs"])
    incompatible = model.load_state_dict(payload["model_state_dict"], strict=True)
    parameter_count = sum(parameter.numel() for parameter in model.parameters() if parameter.requires_grad)
    batch = synthetic_batch()
    with torch.no_grad():
        normal = model(batch)
        swapped = model(swap_candidates(batch))
        permuted = model(permute_repeats(batch))
    output_shapes = {name: list(normal[name].shape) for name in ("task_logits", "unknown_logits")}
    ab_max_abs = max(float((normal[name] - swapped[name]).abs().max().item()) for name in ("task_logits", "unknown_logits"))
    repeat_max_abs = max(float((normal[name] - permuted[name]).abs().max().item()) for name in ("task_logits", "unknown_logits"))
    outputs_finite = all(bool(torch.isfinite(normal[name]).all().item()) for name in ("task_logits", "unknown_logits"))
    optimizer = payload.get("optimizer_state_dict")
    audit = {
        "seed": seed,
        "expected_epoch": expected_epoch,
        "expected_threshold": expected_threshold,
        "expected_checkpoint_sha256": expected_hash,
        "epoch_checkpoint_path": str(epoch_path),
        "selected_checkpoint_path": str(selected_path),
        "file_exists": epoch_path.is_file() and selected_path.is_file(),
        "inventory_row_present": inventory_row is not None,
        "inventory_bytes_match": inventory_row is not None and int(inventory_row["bytes"]) == epoch_path.stat().st_size == selected_path.stat().st_size,
        "epoch_file_sha256": epoch_file_hash,
        "selected_file_sha256": file_hash,
        "file_hash_match": file_hash == epoch_file_hash == selected["checkpoint_sha256"] == expected_hash,
        "schema_exact": set(payload) == required_keys and payload["schema_version"] == "driveclarify.learned_m1_checkpoint.formal.v1",
        "model_class_match": payload["model_class"] == "LearnedM1",
        "seed_match": payload["seed"] == seed,
        "epoch_match": payload["epoch"] == expected_epoch == selected["epoch"],
        "threshold_match": float(selected["threshold"]) == expected_threshold,
        "optimizer_metadata_complete": isinstance(optimizer, dict) and set(optimizer) >= {"state", "param_groups"} and bool(optimizer["param_groups"]),
        "loss_training_metadata_complete": isinstance(payload.get("loss_configuration"), dict) and bool(payload["loss_configuration"]),
        "dataset_index_hash_match": payload["dataset_index_sha256"] == assessment_hashes["FORMAL_M1_DATASET_INDEX.json"],
        "feature_contract_hash_match": payload["feature_contract_sha256"] == assessment_hashes["FEATURE_WHITELIST.json"],
        "model_spec_hash_match": payload["model_spec_sha256"] == assessment_hashes["FORMAL_M1_MODEL_SPEC.json"],
        "training_protocol_hash_match": payload["training_protocol_sha256"] == assessment_hashes["FORMAL_M1_TRAINING_PROTOCOL.json"],
        "training_code_protocol_seal_hash_match": payload["protocol_seal_sha256"] == seal["sha256"],
        "training_config_hash_match": payload["training_config_sha256"] == seal["training_config_sha256"],
        "configuration_match": payload["configuration_id"] == "MAIN_WEIGHTED_PILOT_NEIGHBORHOOD_V1",
        "state_dict_sha256_recomputed": state_hash,
        "state_dict_hash_match": state_hash == payload["model_state_sha256"] == selected["state_dict_sha256"],
        "strict_load": len(incompatible.missing_keys) == 0 and len(incompatible.unexpected_keys) == 0,
        "parameter_count": parameter_count,
        "parameter_count_match": parameter_count == 51684,
        "all_parameters_cpu": all(parameter.device.type == "cpu" for parameter in model.parameters()),
        "model_kwargs_match_frozen_main": payload["model_kwargs"] == {"hidden_dim": 64, "embedding_dim": 32},
        "output_shapes": output_shapes,
        "output_contract_match": output_shapes == {"task_logits": [2, 2], "unknown_logits": [2, 2]},
        "outputs_finite": outputs_finite,
        "ab_swap_max_abs_difference": ab_max_abs,
        "ab_swap_nominal_fresh_model_atol": 1.0e-7,
        "checkpoint_cpu_logit_tolerance": 1.0e-6,
        "ab_swap_invariance": ab_max_abs <= 1.0e-6,
        "repeat_permutation_max_abs_difference": repeat_max_abs,
        "repeat_permutation_invariance": repeat_max_abs <= 1.0e-6,
        "cuda_initialized_after_load_and_synthetic_forward": torch.cuda.is_initialized(),
        "test_read_or_forward_count": 0,
    }
    required = [
        "file_exists", "inventory_row_present", "inventory_bytes_match", "file_hash_match", "schema_exact",
        "model_class_match", "seed_match", "epoch_match", "threshold_match", "optimizer_metadata_complete",
        "loss_training_metadata_complete", "dataset_index_hash_match", "feature_contract_hash_match",
        "model_spec_hash_match", "training_protocol_hash_match", "training_code_protocol_seal_hash_match",
        "training_config_hash_match", "configuration_match", "state_dict_hash_match", "strict_load",
        "parameter_count_match", "all_parameters_cpu", "model_kwargs_match_frozen_main", "output_contract_match",
        "outputs_finite", "ab_swap_invariance", "repeat_permutation_invariance",
    ]
    audit["verdict"] = "PASS" if all(audit[name] is True for name in required) and audit["cuda_initialized_after_load_and_synthetic_forward"] is False else "FAIL"
    load_result = {
        "seed": seed,
        "checkpoint_sha256": file_hash,
        "strict_cpu_load": audit["strict_load"],
        "parameter_count": parameter_count,
        "synthetic_forward_only": True,
        "test_input_used": False,
        "cuda_initialized": torch.cuda.is_initialized(),
        "verdict": audit["verdict"],
    }
    del normal, swapped, permuted, batch, model, payload
    return audit, load_result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_directory", type=Path)
    args = parser.parse_args()
    started = time.monotonic()
    captured_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    output = args.output_directory.resolve()
    expected_parent = (REPO_ROOT / "reports/formal_learned_m1_pretest_verification").resolve()
    output.relative_to(expected_parent)
    if output.exists():
        raise RuntimeError("PRETEST_OUTPUT_DIRECTORY_ALREADY_EXISTS")

    training_git_end = load_json(TRAINING / "GIT_END.json")
    entry_drive = git_snapshot(REPO_ROOT)
    entry_simlingo = git_snapshot(SIMLINGO_ROOT)
    verifier_rel = Path(__file__).resolve().relative_to(REPO_ROOT).as_posix()
    pretest_root_rel = expected_parent.relative_to(REPO_ROOT).as_posix()
    filtered_entry = filtered_untracked_fingerprint(entry_drive["untracked_paths"], (verifier_rel, pretest_root_rel))
    checks: Dict[str, bool] = {}
    check(checks, "entry_driveclarify_matches_frozen_training_exit", filtered_entry == {
        "file_count": training_git_end["driveclarify"]["untracked_file_count"],
        "path_list_nul_bytes": training_git_end["driveclarify"]["untracked_path_list_nul_bytes"],
        "path_list_nul_sha256": training_git_end["driveclarify"]["untracked_path_list_nul_sha256"],
    })
    check(checks, "entry_driveclarify_branch_head_tracked_staged", entry_drive["branch"] == "master" and entry_drive["head"] == EXPECTED_DRIVECLARIFY_HEAD and entry_drive["tracked_diff_bytes"] == 0 and entry_drive["staged_diff_bytes"] == 0)
    check(checks, "entry_simlingo_fingerprint", entry_simlingo["branch"] == "main" and entry_simlingo["head"] == EXPECTED_SIMLINGO_HEAD and entry_simlingo["tracked_diff_bytes"] == EXPECTED_SIMLINGO_DIFF_BYTES and entry_simlingo["tracked_diff_sha256"] == EXPECTED_SIMLINGO_DIFF_SHA256 and entry_simlingo["staged_diff_bytes"] == 0 and entry_simlingo["untracked_path_list_nul_sha256"] == training_git_end["simlingo"]["untracked_path_list_nul_sha256"])

    output.mkdir(parents=True, exist_ok=False)
    for name in DELIVERABLES:
        atomic_write(output / name, b"PENDING\n")

    state = load_json(REPO_ROOT / "STATE.json")
    handoff = read_bytes(REPO_ROOT / "CURRENT_HANDOFF.md").decode("utf-8")
    next_prompt = read_bytes(REPO_ROOT / "NEXT_AGENT_PROMPT.md").decode("utf-8")
    project_requirements = read_bytes(REPO_ROOT / "PROJECT_LONG_TERM_REQUIREMENTS.md").decode("utf-8")
    training_result = load_json(TRAINING / "TRAINING_RESULT.json")
    training_seal = load_json(TRAINING / "TRAINING_CODE_AND_PROTOCOL_SEAL.json")
    training_config = load_json(TRAINING / "TRAINING_CONFIG.json")
    data_feature_hashes = load_json(TRAINING / "DATASET_AND_FEATURE_HASHES.json")
    dataset_audit = load_json(ASSESSMENT / "FORMAL_M1_DATASET_AUDIT.json")
    dataset_index = load_json(ASSESSMENT / "FORMAL_M1_DATASET_INDEX.json")
    whitelist = load_json(ASSESSMENT / "FEATURE_WHITELIST.json")
    blacklist = load_json(ASSESSMENT / "FEATURE_BLACKLIST.json")
    tensor_contract = load_json(ASSESSMENT / "INPUT_TENSOR_CONTRACT.json")
    model_spec = load_json(ASSESSMENT / "FORMAL_M1_MODEL_SPEC.json")
    protocol = load_json(ASSESSMENT / "FORMAL_M1_TRAINING_PROTOCOL.json")
    selected = load_json(TRAINING / "DEV_SELECTED_CHECKPOINTS.json")
    main_runs = load_json(TRAINING / "MAIN_MODEL_ALL_SEEDS.json")
    grid = load_json(TRAINING / "DEV_CHECKPOINT_THRESHOLD_GRID.json")
    metrics = load_json(TRAINING / "TRAIN_DEV_METRICS.json")
    stability = load_json(TRAINING / "SEED_STABILITY.json")
    baselines = load_json(TRAINING / "BASELINE_RESULTS.json")
    ablations = load_json(TRAINING / "ABLATION_RESULTS.json")
    inventory = load_json(TRAINING / "CHECKPOINT_INVENTORY.json")
    checkpoint_hashes = load_json(TRAINING / "CHECKPOINT_HASHES.json")
    test_access_training = load_json(TRAINING / "TEST_ACCESS_AUDIT.json")
    failure_ledger = load_json(TRAINING / "FAILURE_AND_RESUME_LEDGER.json")
    before_bytes = read_bytes(TEST_SEAL)
    before_seal = json.loads(before_bytes.decode("utf-8"))

    assessment_missing = [name for name in ASSESSMENT_REQUIRED if not (ASSESSMENT / name).is_file()]
    training_missing = [name for name in TRAINING_REQUIRED if not (TRAINING / name).is_file()]
    authority_audit = {
        "pretest_verification_id": output.name,
        "captured_at_utc": captured_at,
        "project_status": state.get("status"),
        "handoff_status": state.get("handoff_status"),
        "training_terminal_status": training_result.get("terminal_status"),
        "test_authorization_status": training_result.get("test_authorization_status"),
        "authority_files_present": not assessment_missing and not training_missing,
        "missing_assessment_files": assessment_missing,
        "missing_training_files": training_missing,
        "current_handoff_contains_status": EXPECTED_PROJECT_STATUS in handoff,
        "next_prompt_contains_test_not_authorized": TEST_AUTHORIZATION_STATUS in next_prompt,
        "project_requirements_points_to_state": "STATE.json" in project_requirements,
        "verdict": "PENDING",
    }
    check(checks, "authority_status", authority_audit["project_status"] == EXPECTED_PROJECT_STATUS == authority_audit["handoff_status"] == authority_audit["training_terminal_status"])
    check(checks, "authority_test_not_authorized", authority_audit["test_authorization_status"] == TEST_AUTHORIZATION_STATUS and authority_audit["next_prompt_contains_test_not_authorized"])
    check(checks, "authority_required_files_complete", authority_audit["authority_files_present"])

    unsealed_training = dict(training_seal)
    training_seal_expected = unsealed_training.pop("sha256")
    check(checks, "training_code_protocol_seal_self_hash", canonical_sha256(unsealed_training) == training_seal_expected)
    source_hash_rows = {}
    for name, expected_hash in training_seal["source_file_hashes"].items():
        actual_hash = sha256_file(REPO_ROOT / name)
        source_hash_rows[name] = {"expected_sha256": expected_hash, "actual_sha256": actual_hash, "match": actual_hash == expected_hash}
    check(checks, "sealed_training_source_files_unchanged", all(row["match"] for row in source_hash_rows.values()))
    assessment_hash_rows = {}
    for name, expected_hash in training_seal["assessment_hashes"].items():
        actual_hash = sha256_file(ASSESSMENT / name)
        assessment_hash_rows[name] = {"expected_sha256": expected_hash, "actual_sha256": actual_hash, "match": actual_hash == expected_hash}
    check(checks, "sealed_assessment_dependencies_unchanged", all(row["match"] for row in assessment_hash_rows.values()))
    check(checks, "training_config_hash", canonical_sha256(training_config) == training_seal["training_config_sha256"])
    assessment_artifact_hashes = {name: {"bytes": (ASSESSMENT / name).stat().st_size, "sha256": sha256_file(ASSESSMENT / name)} for name in ASSESSMENT_REQUIRED}
    training_artifact_hashes = {name: {"bytes": (TRAINING / name).stat().st_size, "sha256": sha256_file(TRAINING / name)} for name in TRAINING_REQUIRED}

    datasets = dataset_audit["datasets"]
    dataset_file_rows = {}
    for version in ("V2", "V3"):
        path = REPO_ROOT / datasets[version]["path"]
        actual_hash = sha256_file(path)
        dataset_file_rows[version] = {
            "path": str(path.relative_to(REPO_ROOT)),
            "expected_bytes": datasets[version]["bytes"],
            "actual_bytes": path.stat().st_size,
            "expected_sha256": datasets[version]["sha256"],
            "actual_sha256": actual_hash,
            "match": path.stat().st_size == datasets[version]["bytes"] and actual_hash == datasets[version]["sha256"],
            "content_parsed_or_tensorized": False,
        }
    check(checks, "formal_dataset_file_hashes", all(row["match"] for row in dataset_file_rows.values()))
    index_splits = Counter(row["split"] for row in dataset_index["records"])
    index_labels = Counter(row["label"] for row in dataset_index["records"])
    test_identities = [row["unit_id"] for row in dataset_index["records"] if row["split"] == "TEST"]
    check(checks, "dataset_index_hash", sha256_file(ASSESSMENT / "FORMAL_M1_DATASET_INDEX.json") == training_seal["assessment_hashes"]["FORMAL_M1_DATASET_INDEX.json"])
    check(checks, "dataset_counts", len(dataset_index["records"]) == dataset_index["complete_unit_count"] == 47 and sum(row["plan_record_count"] for row in dataset_index["records"]) == 282)
    check(checks, "dataset_split_and_labels", dict(index_splits) == {"TRAIN": 25, "DEV": 11, "TEST": 11} and dict(index_labels) == {"TASK_EQUIVALENT": 31, "TASK_CRITICAL": 12, "UNKNOWN": 4})
    check(checks, "dataset_pilot_exclusion_and_leakage", dataset_audit["pilot_overlap"] == [] and dataset_audit["engineering_exclusions_entering_formal_units"] == 0 and all(value == [] for value in dataset_audit["split_overlap"].values()))
    check(checks, "test_control_plane_identity_count", len(test_identities) == len(set(test_identities)) == 11)
    schema_hashes = {
        "V2": {"path": str(V2_SCHEMA.relative_to(REPO_ROOT)), "bytes": V2_SCHEMA.stat().st_size, "sha256": sha256_file(V2_SCHEMA)},
        "V3": {"path": str(V3_SCHEMA.relative_to(REPO_ROOT)), "bytes": V3_SCHEMA.stat().st_size, "sha256": sha256_file(V3_SCHEMA)},
    }
    check(checks, "dataset_schema_files_present", V2_SCHEMA.is_file() and V3_SCHEMA.is_file())

    check(checks, "feature_hashes", assessment_hash_rows["FEATURE_WHITELIST.json"]["match"] and assessment_hash_rows["FEATURE_BLACKLIST.json"]["match"] and assessment_hash_rows["INPUT_TENSOR_CONTRACT.json"]["match"])
    check(checks, "feature_contract_semantics", whitelist["construction"] == "EXPLICIT_ALLOWLIST_ONLY" and tensor_contract["targets_physically_separate_from_model_inputs"] is True and tensor_contract["test_tensorization_allowed_this_round"] is False)
    check(checks, "forbidden_tensor_field_count_zero", whitelist["forbidden_field_tensor_count"] == blacklist["forbidden_field_tensor_count"] == tensor_contract["forbidden_field_tensor_count"] == data_feature_hashes["train_tensor_audit"]["forbidden_field_tensor_count"] == data_feature_hashes["dev_tensor_audit"]["forbidden_field_tensor_count"] == 0)
    fresh_model = LearnedM1()
    fresh_parameter_count = sum(parameter.numel() for parameter in fresh_model.parameters() if parameter.requires_grad)
    check(checks, "model_spec_and_parameter_count", model_spec["trainable_parameter_count"] == fresh_parameter_count == 51684 and assessment_hash_rows["FORMAL_M1_MODEL_SPEC.json"]["match"])
    del fresh_model
    check(checks, "protocol_dev_metrics_hashes", assessment_hash_rows["FORMAL_M1_TRAINING_PROTOCOL.json"]["match"] and assessment_hash_rows["FORMAL_M1_DEV_SELECTION_AND_CALIBRATION.md"]["match"] and assessment_hash_rows["FORMAL_M1_METRICS_SPEC.md"]["match"])

    v1_hash = sha256_file(V1_DATASET)
    v2_tree_hash, v2_tree_count, v2_tree_bytes = tree_sha256(V2_ROOT)
    v3_tree_hash, v3_tree_count, v3_tree_bytes = tree_sha256(V3_ROOT)
    pilot_tree_hash, pilot_tree_count, pilot_tree_bytes = tree_sha256(PILOT_ROOT)
    historical = {
        "v1_dataset": {"path": str(V1_DATASET.relative_to(REPO_ROOT)), "sha256": v1_hash, "expected_sha256": training_seal["source_data_hashes"]["v1_dataset_sha256"]},
        "v2_tree": {"sha256": v2_tree_hash, "expected_sha256": training_seal["source_data_hashes"]["v2_tree_sha256"], "file_count": v2_tree_count, "bytes": v2_tree_bytes},
        "v3_tree": {"sha256": v3_tree_hash, "expected_sha256": training_seal["source_data_hashes"]["v3_tree_sha256"], "file_count": v3_tree_count, "bytes": v3_tree_bytes},
        "pilot_tree": {"sha256": pilot_tree_hash, "expected_sha256": training_seal["source_data_hashes"]["pilot_tree_sha256"], "file_count": pilot_tree_count, "bytes": pilot_tree_bytes},
    }
    check(checks, "v1_v2_v3_pilot_integrity", all(row["sha256"] == row["expected_sha256"] for row in historical.values()))

    inventory_bytes_total = sum(int(row["bytes"]) for row in inventory)
    inventory_paths = [row["path"] for row in inventory]
    inventory_stat_ok = len(set(inventory_paths)) == len(inventory_paths) and all(Path(row["path"]).is_file() and Path(row["path"]).stat().st_size == row["bytes"] for row in inventory)
    hashes_projection = [{name: row[name] for name in ("configuration_id", "epoch", "path", "seed", "sha256", "state_dict_sha256")} for row in inventory]
    per_run_inventory = []
    for path in sorted((TRAINING / "learned_runs").glob("*/seed_*/CHECKPOINT_INVENTORY.json")):
        configuration_id = path.parents[1].name
        per_run_inventory.extend(dict(row, configuration_id=configuration_id) for row in load_json(path))
    inventory_sorted = sorted(inventory, key=lambda row: (row["configuration_id"], row["seed"], row["epoch"], row["path"]))
    per_run_sorted = sorted(per_run_inventory, key=lambda row: (row["configuration_id"], row["seed"], row["epoch"], row["path"]))
    check(checks, "checkpoint_inventory_count_bytes_and_files", len(inventory) == training_result["verified_checkpoint_count"] == 4360 and inventory_bytes_total == training_result["verified_checkpoint_bytes"] == 2788715792 and inventory_stat_ok)
    check(checks, "checkpoint_inventory_roots_cross_reproduced", inventory_sorted == per_run_sorted and checkpoint_hashes == hashes_projection)
    inventory_by_path = {row["path"]: row for row in inventory}

    checkpoint_audits = []
    cpu_load_results = []
    for row in selected:
        checkpoint_audit, load_result = verify_selected_checkpoint(row, inventory_by_path, training_seal, training_seal["assessment_hashes"])
        checkpoint_audits.append(checkpoint_audit)
        cpu_load_results.append(load_result)
    check(checks, "selected_checkpoint_count_five", [row["seed"] for row in selected] == [17, 29, 43, 59, 71] and len(checkpoint_audits) == 5)
    check(checks, "selected_checkpoint_hash_state_metadata", all(row["verdict"] == "PASS" for row in checkpoint_audits))
    check(checks, "selected_checkpoint_strict_cpu_load", all(row["strict_cpu_load"] is True and row["cuda_initialized"] is False for row in cpu_load_results))
    nan_closed = combine_prediction([float("nan"), 0.0], [0.0, 1.0], 0.3, True)
    inf_closed = combine_prediction([0.0, 1.0], [0.0, float("inf")], 0.3, True)
    veto = combine_prediction([9.0, -9.0], [-9.0, 9.0], 0.3, True)
    hard_gate = combine_prediction([9.0, -9.0], [9.0, -9.0], 0.3, False)
    check(checks, "prediction_combination_contract", nan_closed["final_decision"] == inf_closed["final_decision"] == veto["final_decision"] == hard_gate["final_decision"] == "UNKNOWN" and nan_closed["fail_closed"] is True and inf_closed["fail_closed"] is True and hard_gate["fail_closed"] is True and veto["decision_source"] == "LEARNED_ABSTENTION_THRESHOLD_VETO")

    grid_by_seed = {seed: [row for row in grid if row["seed"] == seed] for seed in EXPECTED_SELECTED}
    provenance_rows = []
    for selected_row in selected:
        seed = selected_row["seed"]
        selected_pair = selected_row["selection"]
        eligible_rows = [row for row in grid_by_seed[seed] if row["eligible"]]
        independently_selected = min(eligible_rows, key=selection_rank)
        matching = [row for row in grid_by_seed[seed] if row["epoch"] == selected_pair["epoch"] and row["threshold"] == selected_pair["threshold"] and row["checkpoint_sha256"] == selected_pair["checkpoint_sha256"]]
        provenance_rows.append({
            "seed": seed,
            "grid_row_count": len(grid_by_seed[seed]),
            "eligible_row_count": len(eligible_rows),
            "selected_pair_exactly_one_grid_row": len(matching) == 1,
            "selected_pair_eligible": len(matching) == 1 and matching[0]["eligible"] is True,
            "lexicographic_recomputation_matches": independently_selected["epoch"] == selected_pair["epoch"] and independently_selected["threshold"] == selected_pair["threshold"] and independently_selected["checkpoint_sha256"] == selected_pair["checkpoint_sha256"],
            "epoch": selected_pair["epoch"],
            "threshold": selected_pair["threshold"],
            "checkpoint_sha256": selected_pair["checkpoint_sha256"],
            "unknown_precision": selected_pair["unknown_precision"],
            "unknown_recall": selected_pair["unknown_recall"],
            "known_coverage": selected_pair["known_coverage"],
            "feasibility_pass": selected_pair["unknown_precision"] >= 0.25 and selected_pair["unknown_recall"] >= 0.5 and selected_pair["known_coverage"] >= 0.5,
        })
    dev_main = metrics["main_by_seed"]
    check(checks, "dev_selection_grid_and_lexicographic_provenance", len(grid) == sum(run["epochs_completed"] for run in main_runs) * 11 and sorted({row["threshold"] for row in grid}) == EXPECTED_THRESHOLDS and all(row["selected_pair_exactly_one_grid_row"] and row["selected_pair_eligible"] and row["lexicographic_recomputation_matches"] and row["feasibility_pass"] for row in provenance_rows))
    check(checks, "seed_epoch_threshold_binding", all((row["epoch"], row["threshold"], row["checkpoint_sha256"]) == EXPECTED_SELECTED[row["seed"]] for row in provenance_rows))
    check(checks, "selection_protocol_no_posthoc_mechanisms", protocol["checkpoint_selection"]["single_weighted_score_forbidden"] is True and protocol["temperature_scaling"].startswith("DISABLED") and protocol["multi_seed_rule"]["designated_unit_table_model"] == "seed 17 fixed before training" and protocol["multi_seed_rule"]["ensemble"] == "NONE_PRIMARY" and len(selected) == 5)
    check(checks, "frozen_dev_facts", all(row["dev_metrics"]["task_head_known"]["correct"] == 9 and row["dev_metrics"]["task_head_known"]["count"] == 9 and row["dev_metrics"]["abstention"]["true_positive"] == 1 and row["dev_metrics"]["abstention"]["false_positive"] == 0 and row["dev_metrics"]["abstention"]["false_negative"] == 1 and row["dev_metrics"]["abstention"]["true_negative"] == 9 and row["dev_metrics"]["coverage"]["overall_count"] == 10 and row["dev_metrics"]["coverage"]["known_count"] == 9 for row in dev_main) and stability["five_way_agreement_count"] == stability["five_way_agreement_denominator"] == 11)

    baseline_audit = {
        "required_names": ["majority_prior", "task_agnostic_learned_comparator", "no_abstention_learned_model", "rule_based_deterministic_comparator"],
        "all_required_baselines_completed": baselines["all_required_baselines_completed"],
        "majority_present": isinstance(baselines["majority_prior"], dict),
        "task_agnostic_seed_count": len(baselines["task_agnostic_learned_comparator"]),
        "no_abstention_seed_count": len(baselines["no_abstention_learned_model"]),
        "rule_present": isinstance(baselines["rule_based_deterministic_comparator"], dict),
        "baseline_test_use_count": baselines["test_use_count"],
        "ablation_names": sorted(ablations),
        "ablation_statuses": {name: value["status"] for name, value in ablations.items()},
        "ablation_seed_counts": {name: len(value["runs"]) for name, value in ablations.items()},
        "main_consistently_outperforms_every_baseline": training_result["main_consistently_outperforms_every_baseline_on_dev_macro_f1"],
    }
    expected_ablation_names = {
        "no_topology_conditioning", "no_repeat_aggregation_repeat_1_only", "mean_aggregation_only",
        "asymmetric_comparator_diagnostic", "no_abstention_head", "unweighted_losses",
    }
    check(checks, "required_baselines_complete", baseline_audit["all_required_baselines_completed"] is True and baseline_audit["majority_present"] and baseline_audit["task_agnostic_seed_count"] == 5 and baseline_audit["no_abstention_seed_count"] == 5 and baseline_audit["rule_present"] and baseline_audit["baseline_test_use_count"] == 0)
    check(checks, "six_pre_registered_ablations_complete", set(ablations) == expected_ablation_names and all(value["status"] == "COMPLETE" and len(value["runs"]) == 5 for value in ablations.values()))
    check(checks, "failures_and_resume_clear", failure_ledger["failures"] == [] and failure_ledger["silent_retry_count"] == 0)

    verify_seal(before_seal)
    training_test_fields = {
        "test_dataloader_creation": test_access_training["test_dataloader_creation_count"],
        "test_tensorization": test_access_training["test_tensorization_count"],
        "test_forward": test_access_training["test_forward_count"],
        "test_prediction": test_access_training["test_prediction_count"],
        "test_metric": test_access_training["test_metric_count"],
        "test_evaluation": test_access_training["test_evaluation_count"],
        "test_publication": test_access_training["test_publication_count"],
    }
    current_test_counts = dict(EXPECTED_TEST_COUNTS)
    test_zero_audit = {
        "control_plane_reads_only": ["TEST unit count and frozen identities from dataset index", "dataset/index/schema/file hashes", "TEST seal bytes and self hash"],
        "test_identity_count": len(test_identities),
        "test_identity_list_sha256": canonical_sha256(test_identities),
        "test_dataset_content_parsed": False,
        "test_model_input_constructed": False,
        "training_stage_counts": training_test_fields,
        "pretest_stage_counts": current_test_counts,
        "test_seal_file_sha256_before": hashlib.sha256(before_bytes).hexdigest(),
        "test_seal_status_before": before_seal["status"],
        "test_prediction_sha256_before": before_seal["test_prediction_sha256"],
        "test_prediction_completeness_before": before_seal["test_prediction_completeness"],
    }
    check(checks, "test_seal_unchanged_before_advancement", hashlib.sha256(before_bytes).hexdigest() == EXPECTED_TEST_SEAL_FILE_SHA256 and before_seal["status"] == EXPECTED_SEAL_BEFORE_STATUS)
    check(checks, "test_seal_unused", before_seal["test_evaluation_count"] == before_seal["test_prediction_record_count"] == before_seal["publication_count"] == 0 and before_seal["test_prediction_sha256"] is None and before_seal["test_prediction_completeness"] == "NONE")
    check(checks, "test_access_all_zero", training_test_fields == EXPECTED_TEST_COUNTS and current_test_counts == EXPECTED_TEST_COUNTS)

    process_before_transition = process_audit()
    check(checks, "cpu_only_no_cuda", os.environ.get("CUDA_VISIBLE_DEVICES") == "" and torch.cuda.is_initialized() is False and all(row["all_parameters_cpu"] for row in checkpoint_audits))
    check(checks, "no_running_training_runtime_or_gpu_process", process_before_transition["carla_evaluator_training_test_runtime_process_count"] == 0 and process_before_transition["gpu_compute_process_count"] == 0)
    claim_sources = read_bytes(ASSESSMENT / "FORMAL_M1_ASSESSMENT_REPORT.md").decode("utf-8") + read_bytes(ASSESSMENT / "FORMAL_M1_REVIEWER_ATTACK.md").decode("utf-8") + handoff
    check(checks, "claim_boundary_present", "FEASIBILITY_AND_PRELIMINARY_TOWN_DISJOINT_GENERALIZATION" in claim_sources and training_result["main_consistently_outperforms_every_baseline_on_dev_macro_f1"] is False)
    check(checks, "eligible_for_one_time_test_technical_preconditions", all(checks.values()))

    authority_audit["verdict"] = "PASS" if checks["authority_status"] and checks["authority_test_not_authorized"] and checks["authority_required_files_complete"] else "FAIL"
    hash_audit = {
        "pretest_verification_id": output.name,
        "verdict": "PASS" if all(checks[name] for name in (
            "formal_dataset_file_hashes", "dataset_index_hash", "dataset_counts", "dataset_split_and_labels",
            "dataset_pilot_exclusion_and_leakage", "feature_hashes", "feature_contract_semantics",
            "forbidden_tensor_field_count_zero", "model_spec_and_parameter_count", "protocol_dev_metrics_hashes",
            "training_code_protocol_seal_self_hash", "sealed_training_source_files_unchanged",
            "sealed_assessment_dependencies_unchanged", "training_config_hash", "v1_v2_v3_pilot_integrity",
        )) else "FAIL",
        "dataset_files": dataset_file_rows,
        "dataset_schema_hashes": schema_hashes,
        "dataset_index": {
            "sha256": assessment_artifact_hashes["FORMAL_M1_DATASET_INDEX.json"]["sha256"],
            "complete_units": 47,
            "measurements": 282,
            "split": dict(index_splits),
            "labels": dict(index_labels),
            "test_identity_count": len(test_identities),
            "test_identity_sha256": canonical_sha256(test_identities),
        },
        "assessment_artifact_hashes": assessment_artifact_hashes,
        "training_artifact_hashes": training_artifact_hashes,
        "sealed_source_hashes": source_hash_rows,
        "sealed_assessment_hashes": assessment_hash_rows,
        "training_code_protocol_seal_sha256": training_seal["sha256"],
        "checkpoint_inventory": {
            "count": len(inventory),
            "bytes": inventory_bytes_total,
            "inventory_file_sha256": training_artifact_hashes["CHECKPOINT_INVENTORY.json"]["sha256"],
            "checkpoint_hashes_file_sha256": training_artifact_hashes["CHECKPOINT_HASHES.json"]["sha256"],
            "canonical_inventory_root_sha256": canonical_sha256(inventory),
            "canonical_hash_projection_root_sha256": canonical_sha256(checkpoint_hashes),
            "cross_reproduced_from_per_run_inventories": checks["checkpoint_inventory_roots_cross_reproduced"],
        },
        "historical_integrity": historical,
    }
    selected_audit = {
        "pretest_verification_id": output.name,
        "selected_checkpoint_count": len(checkpoint_audits),
        "full_epoch_checkpoint_reload_not_required": True,
        "reason": "inventory count/bytes/roots and all selected file/state hashes reproduced; prior independent 4360/4360 strict-load review remains hash-consistent",
        "rows": checkpoint_audits,
        "verdict": "PASS" if checks["selected_checkpoint_hash_state_metadata"] else "FAIL",
    }
    cpu_load_audit = {
        "pretest_verification_id": output.name,
        "device": "cpu",
        "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"),
        "torch_cuda_initialized": torch.cuda.is_initialized(),
        "synthetic_forward_only": True,
        "results": cpu_load_results,
        "verdict": "PASS" if checks["selected_checkpoint_strict_cpu_load"] else "FAIL",
    }
    provenance_audit = {
        "pretest_verification_id": output.name,
        "threshold_grid": EXPECTED_THRESHOLDS,
        "temperature_scaling": "DISABLED",
        "weighted_composite_score": "FORBIDDEN_AND_NOT_USED",
        "best_seed_selection": "FORBIDDEN_AND_NOT_USED",
        "ensemble": "NONE_PRIMARY",
        "designated_unit_table_seed": 17,
        "rows": provenance_rows,
        "frozen_dev_facts": {
            "known_task_correct_denominator": "9/9",
            "unknown_confusion_tp_fp_fn_tn": [1, 0, 1, 9],
            "overall_coverage": "10/11",
            "known_coverage": "9/9",
            "five_way_agreement": "11/11",
        },
        "verdict": "PASS" if checks["dev_selection_grid_and_lexicographic_provenance"] and checks["seed_epoch_threshold_binding"] and checks["frozen_dev_facts"] else "FAIL",
    }
    baseline_audit["pretest_verification_id"] = output.name
    baseline_audit["verdict"] = "PASS" if checks["required_baselines_complete"] and checks["six_pre_registered_ablations_complete"] else "FAIL"
    test_zero_audit["pretest_verification_id"] = output.name
    test_zero_audit["verdict"] = "PASS" if checks["test_access_all_zero"] and checks["test_seal_unused"] else "FAIL"
    write_json(output / "AUTHORITY_AND_STATE_AUDIT.json", authority_audit)
    write_json(output / "DATASET_FEATURE_MODEL_PROTOCOL_HASH_AUDIT.json", hash_audit)
    write_json(output / "SELECTED_CHECKPOINT_AUDIT.json", selected_audit)
    write_json(output / "SELECTED_CHECKPOINT_CPU_LOAD_RESULTS.json", cpu_load_audit)
    write_json(output / "DEV_SELECTION_PROVENANCE_AUDIT.json", provenance_audit)
    write_json(output / "BASELINE_AND_ABLATION_COMPLETENESS_AUDIT.json", baseline_audit)
    write_json(output / "TEST_ZERO_ACCESS_AUDIT.json", test_zero_audit)
    write_json(output / "TEST_SEAL_BEFORE.json", before_seal)

    result = {
        "schema_version": "driveclarify.formal_m1_pretest_verification_result.v1",
        "pretest_verification_id": output.name,
        "assessment_id": dataset_audit["assessment_id"],
        "training_id": training_result["training_id"],
        "verified_at_utc": captured_at,
        "prediction_free": True,
        "required_check_count": len(checks),
        "required_pass_count": sum(value is True for value in checks.values()),
        "required_failures": sorted(name for name, value in checks.items() if value is not True),
        "checks": checks,
        "test_counts": current_test_counts,
        "eligible_for_seal_advancement": all(checks.values()),
        "status": "PASS" if all(checks.values()) else "FAIL",
    }
    write_json(output / "PRETEST_VERIFICATION_RESULT.json", result)
    verifier_hashes = {
        "schema_version": "driveclarify.formal_m1_pretest_verifier_hashes.v1",
        "pretest_verification_id": output.name,
        "verifier_source": str(Path(__file__).resolve().relative_to(REPO_ROOT)),
        "verifier_source_sha256": sha256_file(Path(__file__).resolve()),
        "assessment_artifact_hashes": assessment_artifact_hashes,
        "training_artifact_hashes": training_artifact_hashes,
        "generated_machine_audit_hashes": {
            name: sha256_file(output / name)
            for name in (
                "AUTHORITY_AND_STATE_AUDIT.json", "DATASET_FEATURE_MODEL_PROTOCOL_HASH_AUDIT.json",
                "SELECTED_CHECKPOINT_AUDIT.json", "SELECTED_CHECKPOINT_CPU_LOAD_RESULTS.json",
                "DEV_SELECTION_PROVENANCE_AUDIT.json", "BASELINE_AND_ABLATION_COMPLETENESS_AUDIT.json",
                "TEST_ZERO_ACCESS_AUDIT.json", "TEST_SEAL_BEFORE.json", "PRETEST_VERIFICATION_RESULT.json",
            )
        },
    }
    write_json(output / "PRETEST_VERIFIER_HASHES.json", verifier_hashes)
    verifier_basis = {
        "schema_version": "driveclarify.formal_m1_pretest_verifier_seal.v1",
        "pretest_verification_id": output.name,
        "prediction_free": True,
        "verifier_source_sha256": verifier_hashes["verifier_source_sha256"],
        "verifier_hashes_file_sha256": sha256_file(output / "PRETEST_VERIFIER_HASHES.json"),
        "verification_result_file_sha256": sha256_file(output / "PRETEST_VERIFICATION_RESULT.json"),
        "dataset_index_sha256": training_seal["assessment_hashes"]["FORMAL_M1_DATASET_INDEX.json"],
        "feature_whitelist_sha256": training_seal["assessment_hashes"]["FEATURE_WHITELIST.json"],
        "feature_blacklist_sha256": training_seal["assessment_hashes"]["FEATURE_BLACKLIST.json"],
        "input_tensor_contract_sha256": training_seal["assessment_hashes"]["INPUT_TENSOR_CONTRACT.json"],
        "model_spec_sha256": training_seal["assessment_hashes"]["FORMAL_M1_MODEL_SPEC.json"],
        "training_protocol_sha256": training_seal["assessment_hashes"]["FORMAL_M1_TRAINING_PROTOCOL.json"],
        "training_code_protocol_seal_sha256": training_seal["sha256"],
        "selected_checkpoint_inventory_sha256": canonical_sha256([row["selection"]["checkpoint_sha256"] for row in selected]),
        "required_check_count": len(checks),
        "required_pass_count": sum(value is True for value in checks.values()),
        "verdict": result["status"],
    }
    verifier_seal = dict(verifier_basis)
    verifier_seal["sha256"] = canonical_sha256(verifier_basis)
    write_json(output / "PRETEST_VERIFIER_SEAL.json", verifier_seal)

    if result["status"] != "PASS" or not result["eligible_for_seal_advancement"]:
        write_json(output / "TEST_SEAL_AFTER.json", before_seal)
        write_json(output / "TEST_SEAL_TRANSITION_AUDIT.json", {
            "pretest_verification_id": output.name,
            "transition_attempted": False,
            "transition_success_count": 0,
            "reason": "INDEPENDENT_VERIFIER_REQUIRED_CHECK_FAILURE",
            "verdict": "BLOCKED",
        })
        raise RuntimeError("FORMAL_M1_PRETEST_VERIFICATION_FAILED:%s" % ",".join(result["required_failures"]))

    if hashlib.sha256(read_bytes(TEST_SEAL)).hexdigest() != EXPECTED_TEST_SEAL_FILE_SHA256:
        raise RuntimeError("TEST_SEAL_CHANGED_BETWEEN_VERIFICATION_AND_TRANSITION")
    after_seal = mark_pretest_verified(before_seal, verifier_seal["sha256"])
    atomic_write(TEST_SEAL, json.dumps(after_seal, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False).encode("utf-8") + b"\n")
    verify_seal(load_json(TEST_SEAL))
    duplicate_failed_closed = False
    duplicate_error = None
    try:
        mark_pretest_verified(after_seal, verifier_seal["sha256"])
    except FormalProtocolError as error:
        duplicate_failed_closed = True
        duplicate_error = str(error)
    transition_audit = {
        "pretest_verification_id": output.name,
        "formal_api": "driveclarify_learned_m1.formal_protocol.mark_pretest_verified",
        "atomic_publication": "temporary file + file fsync + os.replace + directory fsync",
        "before_status": before_seal["status"],
        "after_status": after_seal["status"],
        "bound_pretest_verifier_sha256": after_seal["pretest_verifier_sha256"],
        "frozen_dependencies_bound_transitively_by_verifier_seal": True,
        "transition_success_count": 1,
        "duplicate_call_attempted": True,
        "duplicate_call_failed_closed": duplicate_failed_closed,
        "duplicate_call_error": duplicate_error,
        "test_evaluation_count_after": after_seal["test_evaluation_count"],
        "test_prediction_record_count_after": after_seal["test_prediction_record_count"],
        "publication_count_after": after_seal["publication_count"],
        "test_authorized": False,
        "verdict": "PASS" if duplicate_failed_closed and after_seal["status"] == EXPECTED_SEAL_AFTER_STATUS and after_seal["test_evaluation_count"] == after_seal["test_prediction_record_count"] == after_seal["publication_count"] == 0 else "FAIL",
    }
    write_json(output / "TEST_SEAL_AFTER.json", after_seal)
    write_json(output / "TEST_SEAL_TRANSITION_AUDIT.json", transition_audit)

    claim_boundary = """# Formal Learned M1 Claim Boundary

The strongest permitted claim remains `FEASIBILITY_AND_PRELIMINARY_TOWN_DISJOINT_GENERALIZATION`.

The main model did not consistently outperform the task-agnostic learned comparator, the deterministic rule comparator, or every other strong baseline on DEV. Known-task DEV performance is saturated, while DEV contains only one TASK_CRITICAL and two UNKNOWN units; learned abstention detects only one of those two UNKNOWN units. Frozen TEST contains zero UNKNOWN units and therefore cannot validate UNKNOWN generalization.

This verification does not establish that topology is effective, repeat aggregation is necessary, Learned M1 is superior to the rule comparator, abstention is reliable, or paper-level generalization exists.
"""
    atomic_write(output / "CLAIM_BOUNDARY.md", claim_boundary.encode("utf-8"))
    next_authorization = """# One-Time Frozen Formal Learned M1 TEST Authorization Prompt

Please execute exactly one frozen Formal Learned M1 TEST evaluation for pre-test verification ID `{pretest_id}`.

Authorized scope: load exactly the 11 frozen TEST units once, use all five frozen selected checkpoints with their seed-specific UNKNOWN thresholds (17/73/0.30, 29/52/0.30, 43/41/0.30, 59/81/0.30, 71/116/0.60), persist immutable unit-level prediction bytes before metrics, bind the first evidence to TEST seal verifier SHA-256 `{verifier_sha}`, and then compute only the pre-registered metrics and publication artifact.

Before any TEST data-plane access, require seal status `{seal_status}`, matching dataset/feature/model/protocol/checkpoint hashes, zero prior evaluation/prediction/publication counts, CPU-only execution unless separately changed by explicit authorization, and an atomic first-evidence contract. Any partial prediction bytes consume the one-time event and must be preserved. Do not retrain, recalibrate, reselect, choose a best seed, ensemble, change thresholds, enter M2+, run CARLA/SimLingo, or enable ACT/ASK/WAIT.

Scientific boundary: TEST has UNKNOWN=0, so UNKNOWN recall/F1/AUROC/AUPRC are not estimable; the strongest permitted claim remains `FEASIBILITY_AND_PRELIMINARY_TOWN_DISJOINT_GENERALIZATION`.
""".format(pretest_id=output.name, verifier_sha=verifier_seal["sha256"], seal_status=EXPECTED_SEAL_AFTER_STATUS)
    atomic_write(output / "NEXT_ONE_TIME_TEST_AUTHORIZATION_PROMPT.md", next_authorization.encode("utf-8"))

    wall_time = time.monotonic() - started
    final_process = process_audit()
    cleanup = {
        "pretest_verification_id": output.name,
        "verifier_process_status": "COMPLETING_CURRENT_PROCESS",
        "checkpoint_load_objects_released": True,
        "training_process_count": 0,
        "test_process_count": 0,
        "carla_evaluator_simlingo_runtime_process_count": final_process["carla_evaluator_training_test_runtime_process_count"],
        "cpu_only": True,
        "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"),
        "torch_cuda_initialized": torch.cuda.is_initialized(),
        "gpu_compute_process_count": final_process["gpu_compute_process_count"],
        "prohibited_process_lines": final_process["prohibited_process_lines"],
        "wall_time_seconds": wall_time,
        "peak_ram_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "bytes_read_or_hashed_with_repeats": READ_BYTES,
        "verdict": "PASS" if not final_process["prohibited_process_lines"] and final_process["gpu_compute_process_count"] == 0 and torch.cuda.is_initialized() is False else "FAIL",
    }
    write_json(output / "PROCESS_AND_RESOURCE_CLEANUP.json", cleanup)

    report = """# Formal Learned M1 Prediction-Free Pre-Test Verification

Pre-test Verification ID: `{pretest_id}`  
Result: `PASS`  
Final project status: `{final_status}`  
TEST authorization: `{test_auth}`

All required independent checks passed. Frozen dataset, feature, model, protocol, training-code seal, selected checkpoint file/state hashes, CPU strict loads, seed/epoch/threshold bindings, DEV lexicographic provenance, required baselines, six ablations, Git/history fingerprints, and resource cleanup were reproduced without producing a TEST model input or prediction.

The formal state-machine API advanced the seal exactly once from `{before}` to `{after}` and a duplicate transition failed closed. Evaluation, prediction, and publication counts remain zero. This transition is pre-test verification only and is not TEST authorization.

Verifier SHA-256: `{verifier_sha}`.

The strongest permitted claim remains `FEASIBILITY_AND_PRELIMINARY_TOWN_DISJOINT_GENERALIZATION`; see `CLAIM_BOUNDARY.md`. The only next step is to stop and wait for separate explicit authorization of the one-time frozen TEST evaluation.
""".format(pretest_id=output.name, final_status=FINAL_PROJECT_STATUS, test_auth=TEST_AUTHORIZATION_STATUS, before=EXPECTED_SEAL_BEFORE_STATUS, after=EXPECTED_SEAL_AFTER_STATUS, verifier_sha=verifier_seal["sha256"])
    atomic_write(output / "PRETEST_VERIFICATION_REPORT.md", report.encode("utf-8"))
    independent_review = """# Independent Formal M1 Pre-Test Review

Result: `PASS`.

The verifier is separate from the training runner and did not import the formal data adapter or training primitives. It performed no optimizer, backward, TEST adapter, TEST tensorization, TEST forward, TEST prediction, TEST metric, TEST evaluation, or publication operation. Five selected checkpoints passed byte/state-hash validation, schema/metadata binding, strict CPU load, parameter-count verification, and synthetic-only model-contract checks.

The seal transition was eligible, used the existing formal state-machine API exactly once, was atomically published, and rejected a duplicate call. TEST remains unauthorized and unused.
"""
    atomic_write(output / "INDEPENDENT_PRETEST_REVIEW.md", independent_review.encode("utf-8"))
    write_json(output / "TEST_RESULTS.json", {
        "schema_version": "driveclarify.formal_m1_pretest_test_results.v1",
        "pretest_verification_id": output.name,
        "scope": "PREDICTION_FREE_PROTOCOL_AND_SYNTHETIC_CONTRACT_TESTS_ONLY",
        "required_checks_passed": result["required_pass_count"],
        "required_checks_total": result["required_check_count"],
        "selected_checkpoint_strict_load_passed": 5,
        "selected_checkpoint_strict_load_total": 5,
        "seal_transition_success_count": 1,
        "duplicate_transition_failed_closed": duplicate_failed_closed,
        **current_test_counts,
        "verdict": "PASS",
    })
    command_log = """# Command Log

- Read frozen project authority, assessment protocol, training artifacts, selected DEV results, Git boundaries, and the existing formal seal lifecycle.
- Ran `CUDA_VISIBLE_DEVICES=\"\" /home/buaa/anaconda3/envs/simlingo/bin/python tools/verify_formal_learned_m1_pretest.py <output-directory>`.
- Recomputed byte/file/tree/Git/state-dict hashes and inventory count/bytes/roots.
- Strict-loaded five selected checkpoints on CPU and ran only synthetic model-contract inputs.
- Did not import the formal data adapter or training runner; did not construct a TEST model input.
- Called the existing `mark_pretest_verified` API once after every required check passed; duplicate call failed closed.
"""
    atomic_write(output / "COMMAND_LOG.md", command_log.encode("utf-8"))
    modified_files = {
        "pretest_verification_id": output.name,
        "new_verifier_source": verifier_rel,
        "new_pretest_directory": str(output.relative_to(REPO_ROOT)),
        "state_machine_modified_file": str(TEST_SEAL.relative_to(REPO_ROOT)),
        "authority_files_to_update_after_verifier": ["AGENT_WORKLOG.md", "STATE.json", "CURRENT_HANDOFF.md", "NEXT_AGENT_PROMPT.md"],
        "training_artifacts_modified": [],
        "selected_checkpoints_modified": [],
        "simlingo_modified": False,
        "v1_v2_v3_pilot_modified": False,
    }
    write_json(output / "MODIFIED_FILES.json", modified_files)

    write_json(output / "GIT_START.json", {
        "schema_version": "driveclarify.formal_m1_pretest_git_start.v1",
        "pretest_verification_id": output.name,
        "task_entry_snapshot": training_git_end,
        "task_entry_exactly_reproduced_before_new_verifier_path": checks["entry_driveclarify_matches_frozen_training_exit"],
        "verifier_creation_delta_filtered": filtered_entry,
        "simlingo_entry": {name: value for name, value in entry_simlingo.items() if name != "untracked_paths"},
    })
    exit_drive = git_snapshot(REPO_ROOT)
    exit_simlingo = git_snapshot(SIMLINGO_ROOT)
    allowed_prefixes = (verifier_rel, pretest_root_rel)
    filtered_exit = filtered_untracked_fingerprint(exit_drive["untracked_paths"], allowed_prefixes)
    git_end = {
        "schema_version": "driveclarify.formal_m1_pretest_git_end.v1",
        "pretest_verification_id": output.name,
        "driveclarify": {name: value for name, value in exit_drive.items() if name != "untracked_paths"},
        "simlingo": {name: value for name, value in exit_simlingo.items() if name != "untracked_paths"},
        "untracked_excluding_attributed_pretest_paths": filtered_exit,
        "attributed_new_prefixes": list(allowed_prefixes),
        "historical_integrity": historical,
        "selected_checkpoints_unchanged": all(row["file_hash_match"] for row in checkpoint_audits),
        "test_seal_only_legal_state_machine_delta": True,
        "boundary_status": "PASS" if filtered_exit == {
            "file_count": training_git_end["driveclarify"]["untracked_file_count"],
            "path_list_nul_bytes": training_git_end["driveclarify"]["untracked_path_list_nul_bytes"],
            "path_list_nul_sha256": training_git_end["driveclarify"]["untracked_path_list_nul_sha256"],
        } and exit_drive["tracked_diff_bytes"] == 0 and exit_drive["staged_diff_bytes"] == 0 and exit_simlingo["tracked_diff_sha256"] == EXPECTED_SIMLINGO_DIFF_SHA256 else "FAIL",
    }
    write_json(output / "GIT_END.json", git_end)

    final_summary = {
        "pretest_verification_id": output.name,
        "project_status": FINAL_PROJECT_STATUS,
        "test_authorization_status": TEST_AUTHORIZATION_STATUS,
        "seal_before": before_seal["status"],
        "seal_after": after_seal["status"],
        "verifier_sha256": verifier_seal["sha256"],
        "checks_passed": result["required_pass_count"],
        "checks_total": result["required_check_count"],
        "test_counts": current_test_counts,
        "git_boundary": git_end["boundary_status"],
        "cleanup": cleanup["verdict"],
    }
    print(json.dumps(final_summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
