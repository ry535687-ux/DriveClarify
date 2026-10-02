#!/usr/bin/env python3
"""Execute the single hash-sealed Formal Learned M1 TEST event on CPU.

This runner deliberately lives outside the frozen training source set.  It
checks every frozen dependency before parsing TEST data, freezes the comparison
set and event manifest, performs one forward per unit/checkpoint, atomically
publishes canonical prediction bytes before any metric, and advances the
existing formal lifecycle API without editing seal fields directly.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import resource
import statistics
import subprocess
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import torch

from driveclarify_learned_m1.checkpoint import state_dict_sha256
from driveclarify_learned_m1.formal_data import (
    MODEL_INPUT_KEYS,
    MODEL_INPUT_SOURCE_PATHS,
    REPO_ROOT,
    V2_ROOT,
    V3_ROOT,
    _validated_unit_parts,
    load_formal_records,
    model_input_sha256,
    tree_sha256,
)
from driveclarify_learned_m1.formal_protocol import (
    FORMAL_SEEDS,
    UNKNOWN_THRESHOLD_GRID,
    FormalProtocolError,
    assert_cpu_only,
    combine_prediction,
    publish_test_result,
    record_engineering_interruption,
    record_test_predictions,
    verify_seal,
)
from driveclarify_learned_m1.formal_training import (
    calibration_metrics,
    classification_metrics,
    load_formal_checkpoint,
    metrics_from_probabilities,
    sha256_file,
)
from driveclarify_learned_m1.model import trainable_parameter_count


ASSESSMENT_ID = "DC-FORMAL-M1-ASSESS-20260804T072031Z"
TRAINING_ID = "DC-FORMAL-M1-TRAINDEV-20260804T081727Z"
PRETEST_ID = "DC-FORMAL-M1-PRETEST-20260804T085627Z"
ASSESSMENT = REPO_ROOT / "reports/formal_learned_m1_assessment" / ASSESSMENT_ID
TRAINING = REPO_ROOT / "reports/formal_learned_m1_training" / TRAINING_ID
PRETEST = REPO_ROOT / "reports/formal_learned_m1_pretest_verification" / PRETEST_ID
OUTPUT_ROOT = REPO_ROOT / "reports/formal_learned_m1_test"
TEST_SEAL = ASSESSMENT / "FORMAL_M1_TEST_SEAL.json"

EXPECTED_VERIFIER_SHA256 = "b0964285931040b15269218faee20a024b172e9233c95a84a244697162f99315"
EXPECTED_DATASET_INDEX_SHA256 = "dd36cee891b1081e251857dcebb71820ebfb3e167f0e80c42f0f9d5b36defd25"
EXPECTED_PROTOCOL_SHA256 = "4aa4b0ef6f35f81402f0633f1c400c592c0bb7de755192d92229d45bd5edcdaf"
EXPECTED_TRAINING_CODE_SEAL_SHA256 = "c124431e79e72a7af45137a2468fa7a0df525e5aec41a9b842c7c63c814e3d3f"
EXPECTED_V2_TREE_SHA256 = "31dc8aaf2774f46fa47dd2dfe05832ca85c6797947efddbef418dbab446f5843"
EXPECTED_V3_TREE_SHA256 = "7d95d1fd76fbbfdcd58885a819f9b12739da0e627817a1a7c6b3c743d9bf460f"
EXPECTED_DRIVECLARIFY_HEAD = "eaa332b1bb994279b59ea5af786fdb5de96adc1b"
EXPECTED_SIMLINGO_HEAD = "743b243afd6cf5ff51b9fa1f8cac86f22d569684"
EXPECTED_SIMLINGO_DIFF_SHA256 = "7bd0947ef17df95b56d95ce155468d378a3c282b59f7dee4a2653a7195bc5e34"

FROZEN_HASHES = {
    "dataset_index_sha256": EXPECTED_DATASET_INDEX_SHA256,
    "feature_whitelist_sha256": "bec9f34157b8191b86fc198a785620ab73bb6125423639b782c5977be31f3b85",
    "feature_blacklist_sha256": "8f97e804df7f154d67ab685332b9238e29132d9607fe17ba7cc4a10281ec503c",
    "input_tensor_contract_sha256": "a5199e6b6602307738b44a51b9cdc47e9f475f4f361fe1c5abaa19c1bec7472b",
    "model_spec_sha256": "f5e5de1563023170fd2994df89f1f04ff9fa639695e43df5ee7a551e73213e27",
    "training_protocol_sha256": EXPECTED_PROTOCOL_SHA256,
    "metrics_spec_sha256": "54f1f307467818e8c1775d2d70afd8bc9226452c30d47268ff85bcc8726841ae",
    "training_code_protocol_seal_sha256": EXPECTED_TRAINING_CODE_SEAL_SHA256,
    "pretest_verifier_sha256": EXPECTED_VERIFIER_SHA256,
}

CHECKPOINTS = (
    {"seed": 17, "epoch": 73, "threshold": 0.30, "sha256": "4991935d69273e605c1d115ed3315c9c15b5bd185c9b22773e02954946229a9d"},
    {"seed": 29, "epoch": 52, "threshold": 0.30, "sha256": "dae6d2699f8ae109432ebc6a7e385df704f38ed0976ec735e712d2cc0a982127"},
    {"seed": 43, "epoch": 41, "threshold": 0.30, "sha256": "00189fa20da313b25147e43f4d85cb98ff2ba5fd377453a9e5742e2522720df9"},
    {"seed": 59, "epoch": 81, "threshold": 0.30, "sha256": "c5822798271566f455eaaad50c90324fd725814fc2388800e70c70286bb6fb91"},
    {"seed": 71, "epoch": 116, "threshold": 0.60, "sha256": "5847aeccc0cf9a49669d2cfcacf8747fd7fa4a5ba0c12a15bb5177ff02e45a8e"},
)

SOURCE_HASHES = {
    "driveclarify_learned_m1/checkpoint.py": "ae7243be8b6f906f89d8f883d7a895104a82e9986148c16116aab003aa3c144b",
    "driveclarify_learned_m1/formal_data.py": "748dec207ebc7e3fa16969e0e39386aa75d6e0fc21aaf7b73572330192ab5453",
    "driveclarify_learned_m1/formal_protocol.py": "669d6cb6ceb629d76a5fd4305387833311d9699218e5fb5d401f60ba6b99d6ef",
    "driveclarify_learned_m1/formal_training.py": "76f1d57348dc92943120d7521fe7e2fb5b2bb514fd33287036c2c80afc7a67ab",
    "driveclarify_learned_m1/model.py": "9d455e2b5068a961589e0b901b142e992a7616f904cff2585f6f4060f588200a",
    "tools/run_formal_learned_m1_training.py": "99ae745e7791fe1372a1b37a51d9325bf1808fe08b47c8e2d0ff30f504db8a1c",
    "tools/verify_formal_learned_m1_training.py": "b8c7e7c8d9955574ea3526871afc1df9fe1cc1978886a0f132a33b27dc4bc18a",
}

ARTIFACT_HASH_PATHS = {
    "dataset_index_sha256": ASSESSMENT / "FORMAL_M1_DATASET_INDEX.json",
    "feature_whitelist_sha256": ASSESSMENT / "FEATURE_WHITELIST.json",
    "feature_blacklist_sha256": ASSESSMENT / "FEATURE_BLACKLIST.json",
    "input_tensor_contract_sha256": ASSESSMENT / "INPUT_TENSOR_CONTRACT.json",
    "model_spec_sha256": ASSESSMENT / "FORMAL_M1_MODEL_SPEC.json",
    "training_protocol_sha256": ASSESSMENT / "FORMAL_M1_TRAINING_PROTOCOL.json",
    "metrics_spec_sha256": ASSESSMENT / "FORMAL_M1_METRICS_SPEC.md",
}


class TestEventError(RuntimeError):
    pass


class PartialInferenceError(TestEventError):
    def __init__(self, reason: str, records: Sequence[Mapping[str, Any]], counters: Mapping[str, int]):
        super().__init__(reason)
        self.records = list(records)
        self.counters = dict(counters)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    fd, temporary = tempfile.mkstemp(prefix=".%s." % path.name, dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, str(path))
        directory_fd = os.open(str(path.parent), os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def publish_immutable(path: Path, data: bytes) -> None:
    """Atomically install first bytes without any overwrite path."""

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".%s." % path.name, dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, str(path))
        except FileExistsError:
            raise TestEventError("IMMUTABLE_FIRST_EVIDENCE_ALREADY_EXISTS")
        os.chmod(path, 0o444)
        directory_fd = os.open(str(path.parent), os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_write_seal(value: Mapping[str, Any]) -> None:
    write_json(TEST_SEAL, value)


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


def simlingo_snapshot() -> Dict[str, Any]:
    root = Path("/home/buaa/wrh/simlingo")
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


def process_audit() -> Dict[str, Any]:
    current = os.getpid()
    ancestors = {current}
    probe = current
    for _ in range(10):
        status = Path("/proc/%d/status" % probe)
        if not status.exists():
            break
        ppid = 0
        for line in status.read_text(errors="replace").splitlines():
            if line.startswith("PPid:"):
                ppid = int(line.split()[1])
                break
        if ppid <= 1:
            break
        ancestors.add(ppid)
        probe = ppid
    forbidden_tokens = (
        "run_formal_learned_m1_training.py",
        "CarlaUE4",
        "leaderboard_evaluator.py",
        "simlingo/team_code",
        "run_evaluation",
    )
    relevant = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) in ancestors:
            continue
        try:
            command = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", errors="replace")
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        if any(token.lower() in command.lower() for token in forbidden_tokens):
            relevant.append({"pid": int(entry.name), "command": command})
    gpu_rows: List[str] = []
    try:
        output = subprocess.check_output(
            ["nvidia-smi", "--query-compute-apps=pid,process_name,used_gpu_memory", "--format=csv,noheader"],
            stderr=subprocess.STDOUT,
        ).decode("utf-8", errors="replace").strip()
        gpu_rows = [row for row in output.splitlines() if row.strip()]
    except (FileNotFoundError, subprocess.CalledProcessError):
        gpu_rows = []
    return {
        "relevant_forbidden_processes": relevant,
        "relevant_forbidden_process_count": len(relevant),
        "gpu_compute_process_rows": gpu_rows,
        "gpu_compute_process_count": len(gpu_rows),
    }


def checkpoint_path(seed: int) -> Path:
    return TRAINING / "learned_runs/MAIN_WEIGHTED_PILOT_NEIGHBORHOOD_V1" / ("seed_%d" % seed) / "selected_checkpoint.pt"


def preflight() -> Tuple[Dict[str, Any], List[Tuple[torch.nn.Module, Dict[str, Any], Dict[str, Any]]]]:
    """Prediction-free final gate.  No TEST data source is opened here."""

    assert_cpu_only("cpu")
    checks: Dict[str, bool] = {}
    details: Dict[str, Any] = {}
    seal = load_json(TEST_SEAL)
    verify_seal(seal)
    checks["test_seal_status"] = seal.get("status") == "PRETEST_VERIFIED_AWAITING_ONE_TIME_TEST_AUTHORIZATION"
    checks["verifier_sha"] = seal.get("pretest_verifier_sha256") == EXPECTED_VERIFIER_SHA256
    checks["zero_prior_counts"] = (
        seal.get("test_evaluation_count") == 0
        and seal.get("test_prediction_record_count") == 0
        and seal.get("publication_count") == 0
        and seal.get("test_prediction_sha256") is None
        and seal.get("test_prediction_completeness") == "NONE"
    )
    checks["dataset_and_protocol_seal_binding"] = (
        seal.get("dataset_index_sha256") == EXPECTED_DATASET_INDEX_SHA256
        and seal.get("protocol_sha256") == EXPECTED_PROTOCOL_SHA256
    )

    verifier = load_json(PRETEST / "PRETEST_VERIFIER_SEAL.json")
    verifier_unsealed = dict(verifier)
    stored_verifier_sha = verifier_unsealed.pop("sha256")
    checks["pretest_verifier_self_hash"] = canonical_sha256(verifier_unsealed) == stored_verifier_sha == EXPECTED_VERIFIER_SHA256
    result_hash = sha256_file(PRETEST / "PRETEST_VERIFICATION_RESULT.json")
    hashes_hash = sha256_file(PRETEST / "PRETEST_VERIFIER_HASHES.json")
    checks["pretest_verifier_bound_files"] = (
        result_hash == verifier["verification_result_file_sha256"]
        and hashes_hash == verifier["verifier_hashes_file_sha256"]
        and load_json(PRETEST / "PRETEST_VERIFICATION_RESULT.json")["status"] == "PASS"
    )

    for name, path in ARTIFACT_HASH_PATHS.items():
        checks[name] = sha256_file(path) == FROZEN_HASHES[name]
    training_seal = load_json(TRAINING / "TRAINING_CODE_AND_PROTOCOL_SEAL.json")
    unsealed_training = dict(training_seal)
    stored_training_sha = unsealed_training.pop("sha256")
    checks["training_code_seal_self_hash"] = canonical_sha256(unsealed_training) == stored_training_sha == EXPECTED_TRAINING_CODE_SEAL_SHA256
    checks["sealed_source_hashes"] = all(sha256_file(REPO_ROOT / path) == expected for path, expected in SOURCE_HASHES.items())

    git = git_snapshot(REPO_ROOT)
    simlingo = simlingo_snapshot()
    checks["driveclarify_git_boundary"] = (
        git["branch"] == "master"
        and git["head"] == EXPECTED_DRIVECLARIFY_HEAD
        and git["tracked_diff_bytes"] == 0
        and git["staged_diff_bytes"] == 0
    )
    checks["simlingo_fingerprint"] = (
        simlingo["branch"] == "main"
        and simlingo["head"] == EXPECTED_SIMLINGO_HEAD
        and simlingo["tracked_diff_sha256"] == EXPECTED_SIMLINGO_DIFF_SHA256
        and simlingo["tracked_diff_bytes"] == 7722
        and simlingo["staged_diff_bytes"] == 0
    )
    details["git"] = git
    details["simlingo"] = simlingo
    process = process_audit()
    checks["no_running_forbidden_process"] = process["relevant_forbidden_process_count"] == 0
    checks["gpu_compute_zero"] = process["gpu_compute_process_count"] == 0
    checks["cpu_only_no_cuda"] = os.environ.get("CUDA_VISIBLE_DEVICES") == "" and not torch.cuda.is_initialized()
    details["process"] = process

    checks["v2_tree_sha256"] = tree_sha256(V2_ROOT) == EXPECTED_V2_TREE_SHA256
    checks["v3_tree_sha256"] = tree_sha256(V3_ROOT) == EXPECTED_V3_TREE_SHA256

    selected = load_json(TRAINING / "DEV_SELECTED_CHECKPOINTS.json")
    selected_by_seed = {int(row["seed"]): row["selection"] for row in selected}
    models = []
    checkpoint_rows = []
    for frozen in CHECKPOINTS:
        seed = int(frozen["seed"])
        path = checkpoint_path(seed)
        row = selected_by_seed.get(seed, {})
        file_hash = sha256_file(path) if path.is_file() else None
        binding_ok = (
            row.get("epoch") == frozen["epoch"]
            and float(row.get("threshold", -1)) == float(frozen["threshold"])
            and row.get("selected_checkpoint_sha256") == frozen["sha256"]
            and file_hash == frozen["sha256"]
        )
        model, payload = load_formal_checkpoint(path)
        model.eval()
        strict_ok = (
            payload["seed"] == seed
            and payload["epoch"] == frozen["epoch"]
            and payload["configuration_id"] == "MAIN_WEIGHTED_PILOT_NEIGHBORHOOD_V1"
            and payload["dataset_index_sha256"] == EXPECTED_DATASET_INDEX_SHA256
            and payload["feature_contract_sha256"] == FROZEN_HASHES["feature_whitelist_sha256"]
            and payload["model_spec_sha256"] == FROZEN_HASHES["model_spec_sha256"]
            and payload["training_protocol_sha256"] == EXPECTED_PROTOCOL_SHA256
            and state_dict_sha256(model.state_dict()) == payload["model_state_sha256"]
            and trainable_parameter_count(model) == 51684
            and all(parameter.device.type == "cpu" for parameter in model.parameters())
        )
        checkpoint_rows.append({**frozen, "path": str(path), "file_sha256": file_hash, "binding_ok": binding_ok, "strict_cpu_load": strict_ok})
        models.append((model, payload, dict(frozen)))
    checks["selected_checkpoint_count_5"] = len(models) == 5 and tuple(row[2]["seed"] for row in models) == FORMAL_SEEDS
    checks["selected_checkpoint_hash_epoch_threshold_binding"] = all(row["binding_ok"] for row in checkpoint_rows)
    checks["selected_checkpoint_strict_cpu_load_5_of_5"] = all(row["strict_cpu_load"] for row in checkpoint_rows)
    checks["forbidden_tensor_fields_zero"] = (
        load_json(ASSESSMENT / "FEATURE_WHITELIST.json")["forbidden_field_tensor_count"] == 0
        and load_json(ASSESSMENT / "FEATURE_BLACKLIST.json")["forbidden_field_tensor_count"] == 0
        and load_json(ASSESSMENT / "INPUT_TENSOR_CONTRACT.json")["forbidden_field_tensor_count"] == 0
    )
    checks["no_existing_test_event"] = not OUTPUT_ROOT.exists() or not any(OUTPUT_ROOT.iterdir())
    checks["cuda_context_after_checkpoint_load"] = not torch.cuda.is_initialized()
    details["checkpoint_rows"] = checkpoint_rows
    failures = [name for name, passed in checks.items() if not passed]
    audit = {
        "schema_version": "driveclarify.formal_m1_test_final_gate.v1",
        "prediction_free": True,
        "checks": checks,
        "check_count": len(checks),
        "failures": failures,
        "details": details,
        "verdict": "PASS" if not failures else "FAIL",
    }
    if failures:
        raise TestEventError("FINAL_GATE_FAILED:" + ",".join(failures))
    return audit, models


def tensorize_authorized_test(records: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """TEST-only tensorization using the unchanged frozen validation helper."""

    if len(records) != 11 or any(row.get("split") != "TEST" for row in records):
        raise TestEventError("AUTHORIZED_TEST_RECORD_SET_INVALID")
    parts = [_validated_unit_parts(record) for record in records]
    samples = [part[0] for part in parts]
    targets = [part[1] for part in parts]
    routes = []
    route_masks = []
    speeds = []
    speed_masks = []
    roles = []
    for sample in samples:
        routes.append([[plan["route_plan"] for plan in sample["candidates"][group]["plans"]] for group in ("A", "B")])
        route_masks.append([[plan["route_valid_mask"] for plan in sample["candidates"][group]["plans"]] for group in ("A", "B")])
        speeds.append([[plan["speed_plan"] for plan in sample["candidates"][group]["plans"]] for group in ("A", "B")])
        speed_masks.append([[plan["speed_valid_mask"] for plan in sample["candidates"][group]["plans"]] for group in ("A", "B")])
        roles.append([sample["candidates"][group]["semantic_role_one_hot"] for group in ("A", "B")])
    inputs = {
        "route": torch.tensor(routes, dtype=torch.float32),
        "route_mask": torch.tensor(route_masks, dtype=torch.bool),
        "speed": torch.tensor(speeds, dtype=torch.float32),
        "speed_mask": torch.tensor(speed_masks, dtype=torch.bool),
        "roles": torch.tensor(roles, dtype=torch.float32),
        "topology": torch.tensor([sample["topology"] for sample in samples], dtype=torch.float32),
        "topology_mask": torch.tensor([sample["topology_mask"] for sample in samples], dtype=torch.bool),
        "topology_scalars": torch.tensor([sample["topology_scalars"] for sample in samples], dtype=torch.float32),
        "evidence": torch.tensor([sample["evidence"] for sample in samples], dtype=torch.float32),
    }
    target_tensors = {
        "task_target": torch.tensor([target["task_target"] for target in targets], dtype=torch.long),
        "task_known_mask": torch.tensor([target["task_known_mask"] for target in targets], dtype=torch.bool),
        "unknown_target": torch.tensor([target["unknown_target"] for target in targets], dtype=torch.long),
    }
    expected = {
        "route": (11, 2, 3, 20, 2), "route_mask": (11, 2, 3, 20),
        "speed": (11, 2, 3, 10, 2), "speed_mask": (11, 2, 3, 10),
        "roles": (11, 2, 2), "topology": (11, 2, 25, 2),
        "topology_mask": (11, 2, 25), "topology_scalars": (11, 8), "evidence": (11, 6),
    }
    if tuple(inputs) != MODEL_INPUT_KEYS:
        raise TestEventError("TEST_MODEL_INPUT_KEY_DRIFT")
    for name, shape in expected.items():
        if tuple(inputs[name].shape) != shape:
            raise TestEventError("TEST_TENSOR_SHAPE_MISMATCH:" + name)
        if inputs[name].dtype != torch.bool and not bool(torch.isfinite(inputs[name]).all().item()):
            raise TestEventError("TEST_NONFINITE_TENSOR:" + name)
    return {
        "model_inputs": inputs,
        "targets": target_tensors,
        "audit": {
            "unit_count": 11,
            "model_input_keys": list(inputs),
            "target_keys_physically_separate": list(target_tensors),
            "model_input_source_paths": list(MODEL_INPUT_SOURCE_PATHS),
            "forbidden_field_tensor_count": 0,
            "test_tensorized": True,
            "input_sha256": model_input_sha256(inputs),
        },
    }


def _slice_inputs(inputs: Mapping[str, torch.Tensor], index: int) -> Dict[str, torch.Tensor]:
    return {name: inputs[name][index : index + 1] for name in MODEL_INPUT_KEYS}


def infer_once(
    records: Sequence[Mapping[str, Any]],
    bundle: Mapping[str, Any],
    models: Sequence[Tuple[torch.nn.Module, Dict[str, Any], Dict[str, Any]]],
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    raw: List[Dict[str, Any]] = []
    counters = {"test_unit_count": 11, "model_forward_count": 0, "prediction_record_count": 0, "checkpoint_load_count": 5}
    try:
        with torch.inference_mode():
            for unit_index, record in enumerate(records):
                single = _slice_inputs(bundle["model_inputs"], unit_index)
                hard_gate = bool(torch.all(single["evidence"] > 0.5).item())
                for model, payload, frozen in models:
                    outputs = model(single)
                    counters["model_forward_count"] += 1
                    task_logits = [float(value) for value in outputs["task_logits"][0].detach().cpu().tolist()]
                    unknown_logits = [float(value) for value in outputs["unknown_logits"][0].detach().cpu().tolist()]
                    if not all(math.isfinite(value) for value in task_logits + unknown_logits):
                        raise TestEventError("NONFINITE_TEST_LOGIT")
                    task_probs = [float(value) for value in torch.softmax(outputs["task_logits"], dim=-1)[0].detach().cpu().tolist()]
                    unknown_probs = [float(value) for value in torch.softmax(outputs["unknown_logits"], dim=-1)[0].detach().cpu().tolist()]
                    decision = combine_prediction(task_logits, unknown_logits, float(frozen["threshold"]), hard_gate)
                    raw.append(
                        {
                        "record_order": len(raw),
                        "test_unit_identity": str(record["unit_id"]),
                        "seed": int(frozen["seed"]),
                        "selected_epoch": int(frozen["epoch"]),
                        "frozen_unknown_threshold": float(frozen["threshold"]),
                        "checkpoint_sha256": str(frozen["sha256"]),
                        "model_output": {
                            "task_logits": task_logits,
                            "task_probabilities": task_probs,
                            "unknown_logits": unknown_logits,
                            "unknown_probabilities_known_unknown": unknown_probs,
                            "unknown_probability": unknown_probs[1],
                            "hard_evidence_gate_status": "PASS" if hard_gate else "FAIL",
                            "task_head_prediction": decision.get("task_prediction"),
                            "final_prediction": decision["final_decision"],
                            "decision_source": decision["decision_source"],
                        },
                        "target": {"ground_truth_label": str(record["pair_task_label"])},
                        "frozen_dependency_hashes": dict(FROZEN_HASHES),
                        "model_input_sha256": model_input_sha256(single),
                        "model_parameter_count": trainable_parameter_count(model),
                        "targets_passed_to_forward": False,
                        }
                    )
                    counters["prediction_record_count"] += 1
    except Exception as error:
        raise PartialInferenceError(str(error), raw, counters) from error
    if counters != {"test_unit_count": 11, "model_forward_count": 55, "prediction_record_count": 55, "checkpoint_load_count": 5}:
        raise TestEventError("TEST_RUNTIME_COUNT_MISMATCH")
    if torch.cuda.is_initialized():
        raise TestEventError("CUDA_CONTEXT_CREATED_DURING_TEST")
    return raw, counters


def _metrics_for_seed(rows: Sequence[Mapping[str, Any]], threshold: float) -> Dict[str, Any]:
    ordered = list(rows)
    task_prob = torch.tensor([row["model_output"]["task_probabilities"] for row in ordered], dtype=torch.float64)
    unknown_prob = torch.tensor([row["model_output"]["unknown_probability"] for row in ordered], dtype=torch.float64)
    task_index = {"TASK_EQUIVALENT": 0, "TASK_CRITICAL": 1}
    labels = [row["target"]["ground_truth_label"] for row in ordered]
    targets = {
        "task_target": torch.tensor([task_index.get(label, -1) for label in labels], dtype=torch.long),
        "task_known_mask": torch.tensor([label in task_index for label in labels], dtype=torch.bool),
        "unknown_target": torch.tensor([int(label == "UNKNOWN") for label in labels], dtype=torch.long),
    }
    metrics = metrics_from_probabilities(task_prob, unknown_prob, targets, threshold, abstention_enabled=True)
    metrics["abstention"]["recall"] = "NOT_ESTIMABLE_NO_POSITIVE_UNKNOWN"
    metrics["abstention"]["f1"] = "NOT_ESTIMABLE_NO_POSITIVE_UNKNOWN"
    metrics["abstention"]["auroc"] = "NOT_ESTIMABLE_NO_POSITIVE_UNKNOWN"
    metrics["abstention"]["auprc"] = "NOT_ESTIMABLE_NO_POSITIVE_UNKNOWN"
    metrics["abstention"]["unknown_precision_denominator"] = metrics["abstention"]["predicted_positive"]
    metrics["abstention"]["false_unknown_count"] = metrics["abstention"]["false_positive"]
    return metrics


def risk_coverage_rows(rows: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    task_index = {"TASK_EQUIVALENT": 0, "TASK_CRITICAL": 1}
    result = []
    for threshold in UNKNOWN_THRESHOLD_GRID:
        retained = [row for row in rows if float(row["model_output"]["unknown_probability"]) < float(threshold)]
        correct = sum(
            int(task_index[row["target"]["ground_truth_label"]] == max(range(2), key=lambda i: row["model_output"]["task_probabilities"][i]))
            for row in retained
        )
        count = len(retained)
        accuracy = float(correct) / count if count else None
        result.append(
            {
                "threshold": float(threshold),
                "retained_count": count,
                "denominator": len(rows),
                "coverage": float(count) / len(rows),
                "correct_count": correct,
                "selective_accuracy": accuracy,
                "selective_risk": None if accuracy is None else 1.0 - accuracy,
            }
        )
    return result


def mean_std(values: Sequence[float]) -> Dict[str, Any]:
    return {
        "count": len(values),
        "mean": statistics.fmean(values) if values else None,
        "std_population": statistics.pstdev(values) if len(values) > 1 else (0.0 if values else None),
    }


def stability(seed_rows: Mapping[int, Sequence[Mapping[str, Any]]], seed_metrics: Mapping[int, Mapping[str, Any]]) -> Dict[str, Any]:
    seeds = list(FORMAL_SEEDS)
    prediction_vectors = {seed: [row["model_output"]["final_prediction"] for row in seed_rows[seed]] for seed in seeds}
    pairwise = []
    for left_index, left in enumerate(seeds):
        for right in seeds[left_index + 1 :]:
            agreement = sum(a == b for a, b in zip(prediction_vectors[left], prediction_vectors[right]))
            pairwise.append({"seed_a": left, "seed_b": right, "agreement_count": agreement, "denominator": 11, "agreement": agreement / 11.0})
    five_way_count = sum(len({prediction_vectors[seed][index] for seed in seeds}) == 1 for index in range(11))
    per_unit = []
    for index in range(11):
        unit_id = seed_rows[17][index]["test_unit_identity"]
        predictions = {str(seed): prediction_vectors[seed][index] for seed in seeds}
        per_unit.append({"test_unit_identity": unit_id, "predictions": predictions, "five_way_agreement": len(set(predictions.values())) == 1})
    fields = {
        "task_head_accuracy": [float(seed_metrics[seed]["task_head_known"]["accuracy"]) for seed in seeds],
        "task_head_balanced_accuracy": [float(seed_metrics[seed]["task_head_known"]["balanced_accuracy"]) for seed in seeds],
        "task_head_macro_f1": [float(seed_metrics[seed]["task_head_known"]["macro_f1"]) for seed in seeds],
        "selective_accuracy": [float(seed_metrics[seed]["selective_known"]["accuracy"]) for seed in seeds if seed_metrics[seed]["selective_known"]["accuracy"] is not None],
        "coverage": [float(seed_metrics[seed]["coverage"]["overall"]) for seed in seeds],
        "unknown_brier_score": [float(seed_metrics[seed]["calibration"]["unknown_probability"]["brier_score"]) for seed in seeds],
        "task_brier_score": [float(seed_metrics[seed]["calibration"]["task_confidence"]["brier_score"]) for seed in seeds],
    }
    return {
        "seed_aggregates": {name: mean_std(values) for name, values in fields.items()},
        "pairwise_prediction_agreement": pairwise,
        "five_way_agreement_count": five_way_count,
        "five_way_agreement_denominator": 11,
        "five_way_agreement": five_way_count / 11.0,
        "per_unit_five_seed_predictions": per_unit,
        "no_best_seed_selected": True,
        "ensemble_used": False,
    }


def percentile(values: Sequence[float], probability: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * probability
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def bootstrap_intervals(seed_rows: Mapping[int, Sequence[Mapping[str, Any]]], evaluation_seed: int) -> Dict[str, Any]:
    output = {}
    label_index = {"TASK_EQUIVALENT": 0, "TASK_CRITICAL": 1}
    for seed in FORMAL_SEEDS:
        rows = list(seed_rows[seed])
        rng = random.Random((evaluation_seed + seed) & 0xFFFFFFFF)
        samples: Dict[str, List[float]] = {"task_head_accuracy": [], "task_head_balanced_accuracy": [], "task_head_macro_f1": [], "coverage": [], "selective_accuracy": [], "unknown_brier_score": [], "task_brier_score": []}
        for _ in range(10000):
            chosen = [rows[rng.randrange(11)] for _ in range(11)]
            truth = [label_index[row["target"]["ground_truth_label"]] for row in chosen]
            predicted = [max(range(2), key=lambda i: row["model_output"]["task_probabilities"][i]) for row in chosen]
            task = classification_metrics(truth, predicted)
            samples["task_head_accuracy"].append(float(task["accuracy"]))
            if task["balanced_accuracy"] is not None:
                samples["task_head_balanced_accuracy"].append(float(task["balanced_accuracy"]))
            if task["macro_f1"] is not None:
                samples["task_head_macro_f1"].append(float(task["macro_f1"]))
            threshold = next(float(item["threshold"]) for item in CHECKPOINTS if item["seed"] == seed)
            retained = [row for row in chosen if float(row["model_output"]["unknown_probability"]) < threshold]
            samples["coverage"].append(len(retained) / 11.0)
            if retained:
                retained_correct = sum(
                    label_index[row["target"]["ground_truth_label"]] == max(range(2), key=lambda i: row["model_output"]["task_probabilities"][i])
                    for row in retained
                )
                samples["selective_accuracy"].append(retained_correct / float(len(retained)))
            samples["unknown_brier_score"].append(sum(float(row["model_output"]["unknown_probability"]) ** 2 for row in chosen) / 11.0)
            samples["task_brier_score"].append(
                sum((max(row["model_output"]["task_probabilities"]) - int(label_index[row["target"]["ground_truth_label"]] == max(range(2), key=lambda i: row["model_output"]["task_probabilities"][i]))) ** 2 for row in chosen) / 11.0
            )
        output[str(seed)] = {
            name: {
                "lower_2_5_percentile": percentile(values, 0.025),
                "upper_97_5_percentile": percentile(values, 0.975),
                "defined_resample_count": len(values),
                "requested_resample_count": 10000,
            }
            for name, values in samples.items()
        }
    return {
        "schema_version": "driveclarify.formal_m1_test_bootstrap.v1",
        "method": "DETERMINISTIC_NONPARAMETRIC_UNIT_BOOTSTRAP_PERCENTILE_95_CI",
        "evaluation_seed": evaluation_seed,
        "evaluation_seed_derivation": "first 8 hex digits of frozen pretest verifier SHA-256",
        "resamples": 10000,
        "independent_test_units": 11,
        "held_out_town_groups": 2,
        "town_caveat": "Unit bootstrap does not create additional independent Town groups.",
        "intervals_by_seed": output,
        "undefined_unknown_intervals": "NOT_ESTIMABLE_NO_POSITIVE_UNKNOWN",
    }


def make_report(event_id: str, event_result: Mapping[str, Any], stability_result: Mapping[str, Any], seed_metrics: Mapping[int, Mapping[str, Any]]) -> str:
    lines = [
        "# Formal Learned M1 One-Time Frozen TEST Report",
        "",
        "TEST Event ID: `%s`" % event_id,
        "",
        "Final project status: `FORMAL_LEARNED_M1_ONE_TIME_TEST_COMPLETE`.",
        "Scientific verdict: `%s`." % event_result["scientific_verdict"],
        "",
        "The unique sealed event evaluated exactly 11 TEST units with all five frozen selected main checkpoints (55 unit forwards). Canonical unit-level prediction bytes were atomically published and seal-bound before any summary metric was computed. No baseline or ablation had frozen TEST authorization; their frozen DEV evidence remains the comparison boundary.",
        "",
        "## Exact seed results",
        "",
        "| Seed | Epoch | Threshold | Task accuracy | Balanced accuracy | Macro F1 | Coverage | Selective accuracy | Predicted UNKNOWN |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for frozen in CHECKPOINTS:
        seed = int(frozen["seed"])
        metric = seed_metrics[seed]
        lines.append(
            "| %d | %d | %.2f | %d/%d (%.6f) | %.6f | %.6f | %d/%d (%.6f) | %s | %d/11 |"
            % (
                seed, frozen["epoch"], frozen["threshold"], metric["task_head_known"]["correct"], metric["task_head_known"]["count"], metric["task_head_known"]["accuracy"],
                metric["task_head_known"]["balanced_accuracy"], metric["task_head_known"]["macro_f1"], metric["coverage"]["overall_count"], metric["coverage"]["overall_denominator"], metric["coverage"]["overall"],
                "undefined" if metric["selective_known"]["accuracy"] is None else "%.6f" % metric["selective_known"]["accuracy"], metric["abstention"]["predicted_positive"],
            )
        )
    lines.extend(
        [
            "",
            "Five-way final-prediction agreement: `%d/11` (%.6f). No best seed was selected and no ensemble was used." % (stability_result["five_way_agreement_count"], stability_result["five_way_agreement"]),
            "",
            "## Scientific boundary",
            "",
            "TEST contains 11 units (6 TASK_EQUIVALENT, 5 TASK_CRITICAL) from only two held-out Town groups and no scientific UNKNOWN. UNKNOWN recall, F1, known-vs-unknown AUROC, and positive-class AUPRC are `NOT_ESTIMABLE_NO_POSITIVE_UNKNOWN`. The unit bootstrap uses 10,000 deterministic resamples but cannot create new independent Towns.",
            "",
            "The result can support only preliminary Town-disjoint known-task feasibility. It does not establish population-level generalization, reliable abstention, a topology or repeat-aggregation advantage, or learned-method superiority. Frozen DEV already showed that the main method did not consistently outperform task-agnostic, rule-based, and no-abstention comparators.",
        ]
    )
    return "\n".join(lines) + "\n"


def execute(event_id: str) -> Path:
    started_wall = time.monotonic()
    start_utc = utc_now()
    gate, models = preflight()
    event_root = OUTPUT_ROOT / event_id
    event_root.mkdir(parents=True, exist_ok=False)
    write_json(event_root / "GIT_START.json", {"driveclarify": gate["details"]["git"], "simlingo": gate["details"]["simlingo"]})
    seal_before = load_json(TEST_SEAL)
    write_json(event_root / "TEST_SEAL_BEFORE.json", seal_before)
    write_json(event_root / "TEST_INPUT_INTEGRITY_AUDIT.json", gate)

    comparison = {
        "schema_version": "driveclarify.formal_m1_test_comparison_set.v1",
        "event_id": event_id,
        "frozen_before_test_data_plane": True,
        "comparison_scope": "MAIN_MODEL_FIVE_SEED_SPECIFIC_SELECTED_CHECKPOINTS_ONLY",
        "comparison_ids": ["MAIN_WEIGHTED_PILOT_NEIGHBORHOOD_V1_SEED_%d" % row["seed"] for row in CHECKPOINTS],
        "checkpoints": list(CHECKPOINTS),
        "test_baselines_authorized": False,
        "test_ablations_authorized": False,
        "reason": "Frozen one-time TEST seal protocol explicitly binds all five preselected main checkpoints; baseline and ablation documents require TRAIN/DEV comparisons but do not authorize TEST inference.",
    }
    comparison_hash = canonical_sha256(comparison)
    comparison["comparison_set_sha256"] = comparison_hash
    write_json(event_root / "FORMAL_TEST_COMPARISON_SET.json", comparison)
    evaluation_seed = int(EXPECTED_VERIFIER_SHA256[:8], 16)
    manifest = {
        "schema_version": "driveclarify.formal_m1_test_event_manifest.v1",
        "event_id": event_id,
        "created_at_utc": utc_now(),
        "pretest_verification_id": PRETEST_ID,
        "pretest_verifier_sha256": EXPECTED_VERIFIER_SHA256,
        "comparison_set_sha256": comparison_hash,
        "comparison_ids": comparison["comparison_ids"],
        "test_unit_count": 11,
        "seed_order": list(FORMAL_SEEDS),
        "record_order": "assessment dataset index TEST unit order, then seed order [17,29,43,59,71]",
        "bootstrap_evaluation_seed": evaluation_seed,
        "bootstrap_evaluation_seed_derivation": "first 8 hex digits of frozen pretest verifier SHA-256",
        "expected_main_forwards": 55,
        "expected_prediction_records": 55,
        "prediction_serialization": "UTF-8 canonical JSON sort_keys/separators plus one LF",
        "first_evidence_overwrite_allowed": False,
        "frozen_dependency_hashes": dict(FROZEN_HASHES),
    }
    write_json(event_root / "TEST_EVENT_MANIFEST.json", manifest)

    # TEST data-plane begins only after every gate and both event control-plane files exist.
    all_records = load_formal_records()
    by_id = {str(record["unit_id"]): record for record in all_records if record["split"] == "TEST"}
    index = load_json(ASSESSMENT / "FORMAL_M1_DATASET_INDEX.json")
    ordered_ids = [str(row["unit_id"]) for row in index["records"] if row["split"] == "TEST"]
    if len(ordered_ids) != 11 or set(ordered_ids) != set(by_id):
        raise TestEventError("TEST_IDENTITY_SET_MISMATCH")
    records = [by_id[unit_id] for unit_id in ordered_ids]
    if Counter(record["pair_task_label"] for record in records) != Counter({"TASK_EQUIVALENT": 6, "TASK_CRITICAL": 5}):
        raise TestEventError("TEST_LABEL_INTEGRITY_COUNT_MISMATCH")
    bundle = tensorize_authorized_test(records)
    try:
        raw, counters = infer_once(records, bundle, models)
    except PartialInferenceError as error:
        partial_payload = {
            "schema_version": "driveclarify.formal_m1_test_unit_predictions.v1",
            "event_id": event_id,
            "deterministic_record_order": manifest["record_order"],
            "record_count": len(error.records),
            "completeness": "PARTIAL_PRESERVED",
            "failure_reason": str(error),
            "records": error.records,
        }
        if error.records:
            partial_bytes = canonical_bytes(partial_payload)
            publish_immutable(event_root / "TEST_UNIT_LEVEL_PREDICTIONS.json", partial_bytes)
            partial_seal = record_test_predictions(seal_before, partial_bytes, len(error.records), complete=False)
            atomic_write_seal(partial_seal)
            write_json(event_root / "TEST_SEAL_AFTER.json", partial_seal)
            (event_root / "TEST_RAW_PREDICTION_BYTES.sha256").write_text(hashlib.sha256(partial_bytes).hexdigest() + "  TEST_UNIT_LEVEL_PREDICTIONS.json\n", encoding="ascii")
        else:
            partial_seal = record_engineering_interruption(seal_before)
            atomic_write_seal(partial_seal)
            write_json(event_root / "TEST_SEAL_AFTER.json", partial_seal)
        write_json(event_root / "TEST_ACCESS_AND_FORWARD_AUDIT.json", {**error.counters, "failure_reason": str(error), "prediction_completeness": "PARTIAL_PRESERVED" if error.records else "NONE"})
        raise
    evidence_payload = {
        "schema_version": "driveclarify.formal_m1_test_unit_predictions.v1",
        "event_id": event_id,
        "deterministic_record_order": manifest["record_order"],
        "record_count": len(raw),
        "records": raw,
    }
    evidence_bytes = canonical_bytes(evidence_payload)
    evidence_path = event_root / "TEST_UNIT_LEVEL_PREDICTIONS.json"
    evidence_publication_ns = time.monotonic_ns()
    publish_immutable(evidence_path, evidence_bytes)
    evidence_sha = hashlib.sha256(evidence_bytes).hexdigest()
    (event_root / "TEST_RAW_PREDICTION_BYTES.sha256").write_text(evidence_sha + "  TEST_UNIT_LEVEL_PREDICTIONS.json\n", encoding="ascii")
    seal_predictions = record_test_predictions(seal_before, evidence_bytes, len(raw), complete=True)
    atomic_write_seal(seal_predictions)
    write_json(
        event_root / "FIRST_EVIDENCE_PUBLICATION.json",
        {
            "event_id": event_id,
            "published_at_utc": utc_now(),
            "publication_monotonic_ns": evidence_publication_ns,
            "path": str(evidence_path),
            "bytes": len(evidence_bytes),
            "sha256": evidence_sha,
            "record_count": len(raw),
            "completeness": "COMPLETE",
            "atomic_no_overwrite_method": "fsync temporary + hard-link create final + chmod 0444 + directory fsync",
            "summary_metrics_computed_before_publication": 0,
            "seal_status_after_first_evidence": seal_predictions["status"],
        },
    )

    # Metrics may only start after immutable evidence and seal transition.
    metrics_started_ns = time.monotonic_ns()
    seed_rows = {seed: [row for row in raw if row["seed"] == seed] for seed in FORMAL_SEEDS}
    seed_metrics = {
        seed: _metrics_for_seed(seed_rows[seed], next(row["threshold"] for row in CHECKPOINTS if row["seed"] == seed))
        for seed in FORMAL_SEEDS
    }
    risk_rows = {str(seed): risk_coverage_rows(seed_rows[seed]) for seed in FORMAL_SEEDS}
    stability_result = stability(seed_rows, seed_metrics)
    bootstrap = bootstrap_intervals(seed_rows, evaluation_seed)
    seed17_rows = seed_rows[17]
    main_metrics = {
        "schema_version": "driveclarify.formal_m1_test_main_metrics.v1",
        "designated_exact_main_table_seed": 17,
        "seed_17": seed_metrics[17],
        "seed_17_exact_unit_table": seed17_rows,
        "all_seed_aggregates": stability_result["seed_aggregates"],
        "unit_level_denominator": 11,
        "repeats_are_measurements_not_samples": True,
    }
    calibration = {str(seed): seed_metrics[seed]["calibration"] for seed in FORMAL_SEEDS}
    confusions = {
        str(seed): {
            "task_head": seed_metrics[seed]["task_head_known"]["confusion_matrix_truth_rows_prediction_columns"],
            "selective": seed_metrics[seed]["selective_known"]["confusion_matrix_truth_rows_prediction_columns"],
            "unknown_truth_rows_prediction_columns": seed_metrics[seed]["abstention"]["confusion_matrix_truth_rows_prediction_columns"],
        }
        for seed in FORMAL_SEEDS
    }
    undefined = {
        "ground_truth_unknown_count": 0,
        "status": "NOT_ESTIMABLE_NO_POSITIVE_UNKNOWN",
        "metrics": ["UNKNOWN recall", "UNKNOWN F1", "known-vs-unknown AUROC", "positive-class UNKNOWN AUPRC"],
        "unknown_precision_rule": "computed only when predicted UNKNOWN denominator > 0; otherwise denominator=0 and precision=null",
        "per_seed_predicted_unknown_count": {str(seed): seed_metrics[seed]["abstention"]["predicted_positive"] for seed in FORMAL_SEEDS},
    }
    baseline = {
        "test_baseline_evaluation_performed": False,
        "test_baseline_authorized_by_frozen_protocol": False,
        "formal_test_comparison_set": comparison["comparison_ids"],
        "frozen_dev_comparison": {
            "main_macro_f1_all_seeds": 1.0,
            "task_agnostic_macro_f1_all_seeds": 1.0,
            "no_abstention_macro_f1_all_seeds": 1.0,
            "rule_based_macro_f1": 1.0,
            "majority_macro_f1": 0.47058823529411764,
        },
        "main_model_consistently_outperforms_every_baseline": False,
        "interpretation": "NO_COMPONENT_ADVANTAGE_ESTABLISHED; no post-hoc TEST baseline was run.",
    }
    write_json(event_root / "TEST_MAIN_MODEL_METRICS.json", main_metrics)
    write_json(event_root / "TEST_SEED_METRICS.json", {str(seed): seed_metrics[seed] for seed in FORMAL_SEEDS})
    write_json(event_root / "TEST_SEED_STABILITY.json", stability_result)
    write_json(event_root / "TEST_CONFUSION_MATRICES.json", confusions)
    write_json(event_root / "TEST_CALIBRATION.json", calibration)
    write_json(event_root / "TEST_RISK_COVERAGE.json", risk_rows)
    write_json(event_root / "TEST_BOOTSTRAP_INTERVALS.json", bootstrap)
    write_json(event_root / "TEST_BASELINE_COMPARISON.json", baseline)
    write_json(event_root / "TEST_UNDEFINED_METRICS.json", undefined)

    all_accuracies = [float(seed_metrics[seed]["task_head_known"]["accuracy"]) for seed in FORMAL_SEEDS]
    if all(value >= 0.5 for value in all_accuracies):
        verdict = "FEASIBILITY_SUPPORTED_PRELIMINARY_TOWN_DISJOINT_KNOWN_TASK_GENERALIZATION_NO_COMPONENT_ADVANTAGE"
    else:
        verdict = "FORMAL_TEST_INCONCLUSIVE_SMALL_SAMPLE_NO_COMPONENT_ADVANTAGE"
    metric_hashes = {
        name: sha256_file(event_root / name)
        for name in (
            "TEST_MAIN_MODEL_METRICS.json", "TEST_SEED_METRICS.json", "TEST_SEED_STABILITY.json",
            "TEST_CONFUSION_MATRICES.json", "TEST_CALIBRATION.json", "TEST_RISK_COVERAGE.json",
            "TEST_BOOTSTRAP_INTERVALS.json", "TEST_BASELINE_COMPARISON.json", "TEST_UNDEFINED_METRICS.json",
        )
    }
    publication_payload = {
        "schema_version": "driveclarify.formal_m1_test_publication.v1",
        "event_id": event_id,
        "project_status": "FORMAL_LEARNED_M1_ONE_TIME_TEST_COMPLETE",
        "scientific_verdict": verdict,
        "comparison_set_sha256": comparison_hash,
        "prediction_sha256": evidence_sha,
        "prediction_record_count": 55,
        "prediction_completeness": "COMPLETE",
        "metrics_started_after_first_evidence": metrics_started_ns > evidence_publication_ns,
        "metric_artifact_hashes": metric_hashes,
        "main_metric_artifact_sha256": metric_hashes["TEST_MAIN_MODEL_METRICS.json"],
        "claim_boundary": "FEASIBILITY_AND_PRELIMINARY_TOWN_DISJOINT_GENERALIZATION",
    }
    publication_bytes = canonical_bytes(publication_payload)
    publish_immutable(event_root / "TEST_RESULTS.json", publication_bytes)
    publication_sha = hashlib.sha256(publication_bytes).hexdigest()
    seal_after = publish_test_result(seal_predictions, publication_sha)
    atomic_write_seal(seal_after)
    write_json(event_root / "TEST_SEAL_AFTER.json", seal_after)

    duplicate_evaluation_error = None
    duplicate_publication_error = None
    try:
        record_test_predictions(seal_after, evidence_bytes, len(raw), complete=True)
    except FormalProtocolError as error:
        duplicate_evaluation_error = str(error)
    try:
        publish_test_result(seal_after, publication_sha)
    except FormalProtocolError as error:
        duplicate_publication_error = str(error)
    transition = {
        "before_status": seal_before["status"],
        "prediction_status": seal_predictions["status"],
        "after_status": seal_after["status"],
        "formal_apis": ["record_test_predictions", "publish_test_result"],
        "prediction_sha256": evidence_sha,
        "publication_sha256": publication_sha,
        "test_evaluation_count": seal_after["test_evaluation_count"],
        "publication_count": seal_after["publication_count"],
        "duplicate_evaluation_error": duplicate_evaluation_error,
        "duplicate_evaluation_failed_closed": duplicate_evaluation_error is not None,
        "duplicate_publication_error": duplicate_publication_error,
        "duplicate_publication_failed_closed": duplicate_publication_error is not None,
        "transitive_bindings": {"event_id": event_id, "comparison_set_sha256": comparison_hash, "main_metric_artifact_sha256": metric_hashes["TEST_MAIN_MODEL_METRICS.json"]},
    }
    write_json(event_root / "TEST_SEAL_TRANSITION_AUDIT.json", transition)
    write_json(
        event_root / "TEST_ACCESS_AND_FORWARD_AUDIT.json",
        {
            **counters,
            "test_dataloader_creation_count": 1,
            "test_tensorization_count": 11,
            "test_evaluation_count": 1,
            "test_publication_count": 1,
            "optimizer_step_count": 0,
            "backward_count": 0,
            "best_seed_selection_count": 0,
            "ensemble_count": 0,
            "forbidden_field_tensor_count": 0,
            "target_passed_to_forward_count": 0,
            "unit_forward_contract": "one forward for each of 11 unit x 5 seed records",
        },
    )
    claim_md = """# Formal Learned M1 TEST Claim Boundary

Strongest permitted claim: `FEASIBILITY_AND_PRELIMINARY_TOWN_DISJOINT_GENERALIZATION`.

The formal TEST has only 11 units from two held-out Town groups, with six TASK_EQUIVALENT, five TASK_CRITICAL, and zero scientific UNKNOWN. It may support only preliminary known-task Town-disjoint feasibility. It cannot validate UNKNOWN generalization or reliable abstention. A 10,000-resample unit bootstrap does not create new independent Towns.

Frozen DEV evidence showed no consistent advantage over task-agnostic learned, rule-based, and no-abstention comparators. This TEST does not establish population generalization, paper-level claims, topology effectiveness, repeat aggregation necessity, learned-method superiority, or independence from mapper-derived target-definition correlation.
"""
    (event_root / "TEST_CLAIM_BOUNDARY.md").write_text(claim_md, encoding="utf-8")
    event_result = {
        **publication_payload,
        "published_at_utc": utc_now(),
        "publication_artifact_sha256": publication_sha,
        "seal_before_status": seal_before["status"],
        "seal_after_status": seal_after["status"],
        "runtime_counts": counters,
    }
    write_json(event_root / "TEST_EVENT_RESULT.json", event_result)
    report = make_report(event_id, event_result, stability_result, seed_metrics)
    (event_root / "FORMAL_M1_TEST_REPORT.md").write_text(report, encoding="utf-8")
    (event_root / "NEXT_PROJECT_STAGE_RECOMMENDATION.md").write_text(
        "# Next Project Stage Recommendation\n\nSTOP. The one-time Formal Learned M1 TEST event is consumed and immutable. The only next step is user review of the sealed result; do not rerun TEST, retrain, or enter M2 automatically.\n",
        encoding="utf-8",
    )
    command_log = """# Command Log

The formal event was executed once with `CUDA_VISIBLE_DEVICES=\"\"` using this runner. The runner performed prediction-free gates, froze the event manifest/comparison set, parsed and tensorized 11 TEST units, loaded five selected CPU checkpoints, executed 55 one-unit forwards, atomically published 55 canonical records, then computed only frozen metrics and advanced the existing state machine. No training, backward, optimizer, CARLA, evaluator, SimLingo, GPU, ensemble, reselection, or recalibration command ran.
"""
    (event_root / "COMMAND_LOG.md").write_text(command_log, encoding="utf-8")
    cleanup = process_audit()
    wall = time.monotonic() - started_wall
    cleanup.update(
        {
            "wall_time_seconds": wall,
            "peak_ram_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "test_unit_count": 11,
            "model_forward_count": 55,
            "prediction_record_count": 55,
            "checkpoint_load_count": 5,
            "prediction_output_bytes": len(evidence_bytes),
            "oom": False,
            "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"),
            "torch_cuda_initialized": torch.cuda.is_initialized(),
            "cleanup_status": "PASS" if cleanup["relevant_forbidden_process_count"] == cleanup["gpu_compute_process_count"] == 0 and not torch.cuda.is_initialized() else "FAIL",
        }
    )
    write_json(event_root / "PROCESS_AND_RESOURCE_CLEANUP.json", cleanup)
    git_end = {"driveclarify": git_snapshot(REPO_ROOT), "simlingo": simlingo_snapshot(), "historical_v2_sha256": tree_sha256(V2_ROOT), "historical_v3_sha256": tree_sha256(V3_ROOT)}
    write_json(event_root / "GIT_END.json", git_end)
    required = [
        "FORMAL_M1_TEST_REPORT.md", "TEST_EVENT_RESULT.json", "TEST_EVENT_MANIFEST.json", "FORMAL_TEST_COMPARISON_SET.json",
        "TEST_INPUT_INTEGRITY_AUDIT.json", "TEST_UNIT_LEVEL_PREDICTIONS.json", "TEST_RAW_PREDICTION_BYTES.sha256",
        "TEST_MAIN_MODEL_METRICS.json", "TEST_SEED_METRICS.json", "TEST_SEED_STABILITY.json", "TEST_CONFUSION_MATRICES.json",
        "TEST_CALIBRATION.json", "TEST_RISK_COVERAGE.json", "TEST_BOOTSTRAP_INTERVALS.json", "TEST_BASELINE_COMPARISON.json",
        "TEST_UNDEFINED_METRICS.json", "TEST_CLAIM_BOUNDARY.md", "TEST_SEAL_BEFORE.json", "TEST_SEAL_AFTER.json",
        "TEST_SEAL_TRANSITION_AUDIT.json", "FIRST_EVIDENCE_PUBLICATION.json", "TEST_ACCESS_AND_FORWARD_AUDIT.json",
        "COMMAND_LOG.md", "TEST_RESULTS.json", "GIT_START.json", "GIT_END.json", "PROCESS_AND_RESOURCE_CLEANUP.json",
        "NEXT_PROJECT_STAGE_RECOMMENDATION.md",
    ]
    modified = {
        "event_id": event_id,
        "created_files": sorted(required + ["INDEPENDENT_TEST_REVIEW.md", "MODIFIED_FILES.json"]),
        "updated_files": [str(TEST_SEAL.relative_to(REPO_ROOT)), "AGENT_WORKLOG.md", "STATE.json", "CURRENT_HANDOFF.md", "NEXT_AGENT_PROMPT.md"],
        "historical_dataset_or_checkpoint_modified": False,
        "simlingo_modified": False,
    }
    write_json(event_root / "MODIFIED_FILES.json", modified)
    missing = [name for name in required if not (event_root / name).is_file()]
    if missing:
        raise TestEventError("MISSING_REQUIRED_DELIVERABLES:" + ",".join(missing))
    return event_root


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    if args.preflight_only:
        audit, _ = preflight()
        print(json.dumps({"verdict": audit["verdict"], "check_count": audit["check_count"]}, sort_keys=True))
        return 0
    output = execute(args.event_id)
    print(str(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
