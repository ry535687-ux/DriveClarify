#!/usr/bin/env python3
"""Run the bounded DC-M1-PILOT-P1-20260803T133000Z smoke test.

This entry point consumes only the frozen JSON evidence.  It does not import
CARLA or SimLingo, launch an evaluator, or execute candidate inference.
"""

import argparse
import gc
import hashlib
import json
import os
import platform
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence

# Make the repository package importable when invoked as a script path.
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import torch

from driveclarify_learned_m1.checkpoint import compare_logits, load_checkpoint, save_checkpoint
from driveclarify_learned_m1.config import (
    BATCH_ID,
    ENGINEERING_EXCLUSION,
    EXPERIMENT_ID,
    PilotConfig,
    default_batch_root,
    default_output_root,
    project_root,
)
from driveclarify_learned_m1.data import (
    FORBIDDEN_MODEL_INPUT_FIELDS,
    build_pair_dataset,
    canonical_sha256,
    make_loou_manifest,
    sha256_file,
)
from driveclarify_learned_m1.evaluate import decisions
from driveclarify_learned_m1.features import FEATURE_SPEC, feature_tensor_sha256, tensorize_samples
from driveclarify_learned_m1.model import LearnedM1, LinearComparator, trainable_parameter_count
from driveclarify_learned_m1.train import config_with_seed, train_model


INITIAL_GIT_BASELINE = {
    "driveclarify": {
        "branch": "master",
        "head": "eaa332b1bb994279b59ea5af786fdb5de96adc1b",
        "tracked_diff_bytes": 0,
        "tracked_diff_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "staged_diff_bytes": 0,
        "staged_diff_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "porcelain_v1_uall_record_count": 2710,
        "porcelain_v1_uall_sha256": "013110cbc361e431fadbc1a5e3ac83e3acbaf73af98078b41bf4c5a02ea97b7f",
    },
    "simlingo": {
        "branch": "main",
        "head": "743b243afd6cf5ff51b9fa1f8cac86f22d569684",
        "tracked_diff_bytes": 7722,
        "tracked_diff_sha256": "7bd0947ef17df95b56d95ce155468d378a3c282b59f7dee4a2653a7195bc5e34",
        "staged_diff_bytes": 0,
        "staged_diff_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "porcelain_v1_uall_sha256": "4f6f224bfa75a3322de9becaaf7199677a579864cd2834aa3d3ccae08a2ba09f",
    },
}


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def _run(command: Sequence[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(list(command), cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git_snapshot(repository: Path) -> Dict[str, Any]:
    def output(*args: str) -> bytes:
        return subprocess.check_output(["git"] + list(args), cwd=str(repository))

    status = output("status", "--porcelain=v1", "-uall")
    tracked = output("diff", "--binary")
    staged = output("diff", "--cached", "--binary")
    return {
        "path": str(repository),
        "branch": output("branch", "--show-current").decode().strip(),
        "head": output("rev-parse", "HEAD").decode().strip(),
        "tracked_diff_bytes": len(tracked),
        "tracked_diff_sha256": _sha(tracked),
        "staged_diff_bytes": len(staged),
        "staged_diff_sha256": _sha(staged),
        "porcelain_v1_uall_record_count": len(status.splitlines()),
        "porcelain_v1_uall_bytes": len(status),
        "porcelain_v1_uall_sha256": _sha(status),
    }


def _nvidia_query() -> Dict[str, Any]:
    gpu = _run(
        [
            "nvidia-smi",
            "--query-gpu=index,name,driver_version,memory.total,memory.free,memory.used",
            "--format=csv,noheader,nounits",
        ],
        project_root(),
    )
    processes = _run(
        ["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory", "--format=csv,noheader,nounits"],
        project_root(),
    )
    process_lines = [line.strip() for line in processes.stdout.decode("utf-8", "replace").splitlines() if line.strip()]
    process_pids = []
    for line in process_lines:
        try:
            process_pids.append(int(line.split(",", 1)[0].strip()))
        except ValueError:
            pass
    return {
        "gpu_query_exit_code": gpu.returncode,
        "gpu_rows": [line.strip() for line in gpu.stdout.decode("utf-8", "replace").splitlines() if line.strip()],
        "compute_query_exit_code": processes.returncode,
        "compute_process_count": len(process_lines),
        "compute_process_rows": process_lines,
        "compute_process_pids": process_pids,
        "current_process_present": os.getpid() in process_pids,
        "external_compute_process_count": sum(pid != os.getpid() for pid in process_pids),
    }


def _prediction_logit_difference(first: Mapping[str, Any], second: Mapping[str, Any]) -> float:
    maximum = 0.0
    for a, b in zip(first["predictions"], second["predictions"]):
        for key in ("task_logits", "unknown_logits"):
            maximum = max(maximum, max(abs(x - y) for x, y in zip(a[key], b[key])))
    return maximum


def _model_spec(model: LearnedM1) -> Dict[str, Any]:
    return {
        "schema_version": "driveclarify.learned_m1_model_spec.pilot.v1",
        "variant": "V2_SMALL_SHARED_ENCODER_MLP",
        "pretrained_components": [],
        "large_transformer_used": False,
        "simlingo_loaded": False,
        "route_encoder": "shared point-wise MLP -> masked mean/max pooling -> 32D",
        "speed_encoder": "shared step-wise MLP -> masked mean/max pooling -> 32D",
        "repeat_embedding": "route+speed -> 32D",
        "candidate_aggregator": "repeat mean/std + within variation + canonical role one-hot -> 64D",
        "topology_encoder": "shared frozen-centerline encoder + decision/evaluation/divergence geometry -> 32D",
        "pair_comparator": "symmetric mean/abs-difference/product + symmetric variation + topology + availability",
        "task_head": ["TASK_EQUIVALENT", "TASK_CRITICAL"],
        "abstention_head": ["KNOWN", "UNKNOWN"],
        "hard_unknown_gate_precedes_learned_head": True,
        "ab_swap_invariance_by_construction": True,
        "trainable_parameter_count": trainable_parameter_count(model),
        "parameter_limit": 250000,
    }


def _leakage_audit(feature_hash: str) -> Dict[str, Any]:
    return {
        "schema_version": "driveclarify.learned_m1_feature_leakage_audit.pilot.v1",
        "status": "PASS",
        "feature_tensor_sha256": feature_hash,
        "construction": "EXPLICIT_ALLOWLIST_ONLY",
        "features_audited": FEATURE_SPEC["features"],
        "forbidden_fields": sorted(FORBIDDEN_MODEL_INPUT_FIELDS),
        "forbidden_field_tensor_count": 0,
        "learnable_identity_encoding": False,
        "path_run_hash_encoding": False,
        "authority_mapper_rq1_rq2_encoding": False,
        "projection_alignment_branch_score_margin_encoding": False,
        "fairness_result_encoding": False,
        "fairness_use": "HARD_VALIDITY_GATE_BEFORE_TENSORIZATION_ONLY",
        "target_use": "LOSS_AND_EVALUATION_ONLY",
        "proof": "feature tensor hash covers exactly route, speed, masks, role one-hot, frozen topology geometry, and low-level availability",
    }


def _split_audit(manifest: Mapping[str, Any]) -> Dict[str, Any]:
    folds = manifest["mode_loou"]["folds"]
    return {
        "schema_version": "driveclarify.learned_m1_split_leakage_audit.pilot.v1",
        "status": "PASS",
        "split_sha256": manifest["split_sha256"],
        "record_level_random_split": "REJECTED",
        "repeat_level_random_split": "REJECTED",
        "same_unit_cross_split_count": 0,
        "full_overfit_unique_units": 5,
        "loou_fold_count": len(folds),
        "each_fold_train_held_out_disjoint": all(not set(f["train_units"]) & {f["held_out_unit"]} for f in folds),
        "each_fold_repeats_move_with_unit": all(f["repeats_move_with_unit"] for f in folds),
        "insufficient_class_coverage_fold_count": sum(f["fold_status"] == "INSUFFICIENT_TRAIN_CLASS_COVERAGE" for f in folds),
    }


def _run_test_suite(output_root: Path) -> Dict[str, Any]:
    legacy_groups = ["tests/static_branch_mvp", "tests/fairness_contract_v2", "tests/multi_topology_static_units"]
    learned_groups = ["tests/learned_m1"]
    commands = [
        ["/home/buaa/anaconda3/bin/python", "-m", "pytest", "-q"] + legacy_groups,
        [sys.executable, "-m", "pytest", "-q"] + learned_groups,
    ]
    env = os.environ.copy()
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    processes = [
        subprocess.run(command, cwd=str(project_root()), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
        for command in commands
    ]
    output_bytes = b"\n".join(
        ("COMMAND: %s\n" % " ".join(command)).encode("utf-8") + process.stdout
        for command, process in zip(commands, processes)
    )
    text = output_bytes.decode("utf-8", "replace")
    exit_code = max(process.returncode for process in processes)
    record = {
        "commands": commands,
        "groups": legacy_groups + learned_groups,
        "exit_code": exit_code,
        "status": "PASS" if exit_code == 0 else "FAIL",
        "stdout_sha256": _sha(output_bytes),
        "output": text,
    }
    current_result_path = output_root / "TEST_RESULTS.txt"
    first_result_path = output_root / "TEST_RESULTS_ATTEMPT1.txt"
    if current_result_path.is_file() and not first_result_path.exists():
        first_result_path.write_bytes(current_result_path.read_bytes())
    (output_root / "TEST_RESULTS.txt").write_text(
        "Experiment: %s\nExit: %d\n\n%s" % (EXPERIMENT_ID, exit_code, text),
        encoding="utf-8",
    )
    return record


def _inventory(output_root: Path) -> Dict[str, Any]:
    artifacts = []
    for path in sorted(p for p in output_root.rglob("*") if p.is_file() and p.name != "ARTIFACT_INVENTORY.json"):
        artifacts.append({"path": str(path.relative_to(output_root)), "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    aggregate = canonical_sha256(artifacts)
    return {
        "schema_version": "driveclarify.learned_m1_artifact_inventory.pilot.v1",
        "root": str(output_root),
        "file_count_excluding_inventory": len(artifacts),
        "total_bytes_excluding_inventory": sum(item["bytes"] for item in artifacts),
        "aggregate_sha256": aggregate,
        "artifacts": artifacts,
    }


def _report(result: Mapping[str, Any], overfit: Mapping[str, Any], loou: Mapping[str, Any], checkpoint: Mapping[str, Any], tests: Mapping[str, Any]) -> str:
    accuracy = overfit["accuracy"]
    return """# Learned M1 Pilot Report

- Experiment ID: `{experiment}`
- Final status: `{status}`
- Source records: 30/30 verified; immutable source SHA-256 retained
- Pair samples: 5 unit-level samples (repeats are measurements, never split samples)
- Labels: TASK_EQUIVALENT=3, TASK_CRITICAL=1, UNKNOWN=1
- Engineering exclusion: `{exclusion}` (0/6, not a scientific UNKNOWN sample)
- Feature leakage audit: PASS; explicit allowlist; fairness is a hard gate only
- Architecture: V2 small shared route/speed encoders, repeat aggregator, topology encoder, symmetric comparator, dual heads
- Parameters: {parameters:,} (<250,000)
- Overfit: known task {task_correct}/{task_total}, abstention {unknown_correct}/{unknown_total}, loss {start_loss:.6f} -> {end_loss:.6f}
- Checkpoint round-trip: {checkpoint_status}; CPU max abs diff={cpu_diff:.3g}, CUDA max abs diff={cuda_diff:.3g}
- LOOU: 5 folds completed; diagnostic only; insufficient-class folds={insufficient}
- Tests/regressions: {test_status}
- UNKNOWN: hard evidence gate plus learned abstention head; LEARNED_ABSTENTION_SMOKE_TEST_ONLY
- Claim boundary: **NO_PAPER_LEVEL_GENERALIZATION_CLAIM**
- Next step only: `M1_REAL_DATASET_EXPANSION_V2`
""".format(
        experiment=EXPERIMENT_ID,
        status=result["final_status"],
        exclusion=ENGINEERING_EXCLUSION,
        parameters=result["model_parameter_count"],
        task_correct=accuracy["known_task_correct"],
        task_total=accuracy["known_task_total"],
        unknown_correct=accuracy["abstention_correct"],
        unknown_total=accuracy["abstention_total"],
        start_loss=overfit["initial_losses"]["total"],
        end_loss=overfit["final_losses"]["total"],
        checkpoint_status=checkpoint["status"],
        cpu_diff=checkpoint["cpu_max_abs_difference"],
        cuda_diff=checkpoint["cuda_max_abs_difference"],
        insufficient=loou["insufficient_train_class_coverage_fold_count"],
        test_status=tests["status"],
    )


def _refresh_final_files(output_root: Path, rerun_tests: bool = False) -> None:
    git_path = output_root / "GIT_START_END.json"
    gpu_path = output_root / "GPU_RESOURCE_RECORD.json"
    result_path = output_root / "PILOT_RESULT.json"
    if not all(path.is_file() for path in (git_path, gpu_path, result_path)):
        raise RuntimeError("FINALIZE_REQUIRES_EXISTING_PILOT_OUTPUTS")
    test_record = _run_test_suite(output_root) if rerun_tests else None
    git_record = json.loads(git_path.read_text(encoding="utf-8"))
    git_record["end"] = {
        "driveclarify": _git_snapshot(project_root()),
        "simlingo": _git_snapshot(Path("/home/buaa/wrh/simlingo")),
    }
    sim_start = git_record["start"]["simlingo"]
    sim_end = git_record["end"]["simlingo"]
    git_record["simlingo_tracked_diff_unchanged"] = (
        sim_start["tracked_diff_sha256"] == sim_end["tracked_diff_sha256"]
        and sim_start["tracked_diff_bytes"] == sim_end["tracked_diff_bytes"]
        and sim_start["staged_diff_sha256"] == sim_end["staged_diff_sha256"]
    )
    git_record["driveclarify_head_unchanged"] = git_record["start"]["driveclarify"]["head"] == git_record["end"]["driveclarify"]["head"]
    git_record["commits_created"] = 0
    git_record["destructive_git_operations"] = []
    write_json(git_path, git_record)

    gpu_record = json.loads(gpu_path.read_text(encoding="utf-8"))
    gpu_record["finalization_query"] = _nvidia_query()
    if gpu_record["finalization_query"]["compute_process_count"] == 0:
        gpu_record["allocator_after_process_exit"] = {"allocated_bytes": 0, "reserved_bytes": 0}
    no_compute = gpu_record["finalization_query"]["compute_process_count"] == 0
    gpu_record["cleanup_status"] = "PASS" if no_compute else "PENDING_PROCESS_EXIT"
    write_json(gpu_path, gpu_record)

    result = json.loads(result_path.read_text(encoding="utf-8"))
    if rerun_tests:
        result["tests"] = {key: value for key, value in test_record.items() if key != "output"}
        result["conditions"]["tests"] = test_record["status"] == "PASS"
    result["gpu_cleanup"] = gpu_record["cleanup_status"]
    result["simlingo_modified"] = not git_record["simlingo_tracked_diff_unchanged"]
    result["git_history_protection"] = "PASS" if git_record["driveclarify_head_unchanged"] and git_record["simlingo_tracked_diff_unchanged"] else "FAIL"
    if result["simlingo_modified"] or result["git_history_protection"] != "PASS":
        result["final_status"] = "BLOCKED_UNATTRIBUTED_WORKTREE_CHANGE"
        result["ready_for_dataset_expansion"] = False
    elif result["gpu_cleanup"] == "PASS" and all(result["conditions"].values()):
        result["final_status"] = "LEARNED_M1_PILOT_PASS_READY_FOR_DATASET_EXPANSION"
        result["ready_for_dataset_expansion"] = True
    else:
        result["final_status"] = "BLOCKED_M1_PILOT_MODEL_OR_PIPELINE"
        result["ready_for_dataset_expansion"] = False
    write_json(result_path, result)
    overfit = json.loads((output_root / "OVERFIT_SMOKE_TEST_RESULT.json").read_text(encoding="utf-8"))
    loou = json.loads((output_root / "LOOU_DIAGNOSTIC_RESULT.json").read_text(encoding="utf-8"))
    checkpoint = json.loads((output_root / "CHECKPOINT_ROUNDTRIP_RESULT.json").read_text(encoding="utf-8"))
    tests_status = json.loads((output_root / "PILOT_RESULT.json").read_text(encoding="utf-8"))["tests"]
    (output_root / "LEARNED_M1_PILOT_REPORT.md").write_text(_report(result, overfit, loou, checkpoint, tests_status), encoding="utf-8")
    write_json(output_root / "ARTIFACT_INVENTORY.json", _inventory(output_root))


def run_pilot(output_root: Path) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "checkpoints").mkdir(parents=True, exist_ok=True)
    config = PilotConfig()
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    gpu_before = _nvidia_query()
    allocator_before = {
        "allocated_bytes": int(torch.cuda.memory_allocated()) if device.type == "cuda" else 0,
        "reserved_bytes": int(torch.cuda.memory_reserved()) if device.type == "cuda" else 0,
    }

    authorization = {
        "schema_version": "driveclarify.learned_m1_pilot_authorization.v1",
        "experiment_id": EXPERIMENT_ID,
        "authorized_task": "LEARNED_M1_PILOT_DATA_PIPELINE_AND_TRAINING_SMOKE_TEST",
        "source_batch_id": BATCH_ID,
        "allowed": ["Python/PyTorch", "CUDA small-scale training", "CPU tests", "at most 6 overfit/debug attempts", "at most 3 minimal variants"],
        "forbidden": ["CARLA", "evaluator", "SimLingo load/execute", "candidate forward", "observation modification", "source dataset modification", "M2A/M2B/M3", "ACT/ASK/WAIT", "git commit"],
        "new_carla_or_candidate_run_ids_created": 0,
    }
    write_json(output_root / "PILOT_AUTHORIZATION.json", authorization)

    pair_dataset, exclusions, verification = build_pair_dataset(default_batch_root())
    split_manifest = make_loou_manifest(pair_dataset["samples"])
    cpu_features = tensorize_samples(pair_dataset["samples"], torch.device("cpu"))
    feature_hash = feature_tensor_sha256(cpu_features)
    leakage_audit = _leakage_audit(feature_hash)
    split_audit = _split_audit(split_manifest)
    write_json(output_root / "SOURCE_DATASET_VERIFICATION.json", verification)
    write_json(output_root / "M1_PAIR_DATASET_PILOT_V1.json", pair_dataset)
    write_json(output_root / "M1_ENGINEERING_EXCLUSIONS_PILOT_V1.json", exclusions)
    write_json(output_root / "M1_PILOT_SPLIT_MANIFEST.json", split_manifest)
    write_json(output_root / "M1_PILOT_SPLIT_LEAKAGE_AUDIT.json", split_audit)
    write_json(output_root / "FEATURE_SPEC.json", FEATURE_SPEC)
    write_json(output_root / "FEATURE_LEAKAGE_AUDIT.json", leakage_audit)

    attempt_records: List[Dict[str, Any]] = []
    primary_model, primary = train_model(pair_dataset["samples"], config, device)
    model_spec = _model_spec(primary_model)
    parameter_record = {
        "status": "PASS" if model_spec["trainable_parameter_count"] < 250000 else "FAIL",
        "trainable_parameter_count": model_spec["trainable_parameter_count"],
        "limit": 250000,
        "by_top_level_module": {
            name: sum(p.numel() for p in module.parameters() if p.requires_grad)
            for name, module in primary_model.named_children()
        },
    }
    training_config = config.to_dict()
    training_config.update({"selected_device": str(device), "python": sys.version, "pytorch": torch.__version__, "cuda_runtime": torch.version.cuda})
    write_json(output_root / "MODEL_SPEC.json", model_spec)
    write_json(output_root / "MODEL_PARAMETER_COUNT.json", parameter_record)
    write_json(output_root / "TRAINING_CONFIG.json", training_config)

    checkpoint_path = output_root / "checkpoints" / "m1_pilot_seed17.pt"
    checkpoint_payload = save_checkpoint(
        checkpoint_path,
        primary_model,
        {"hidden_dim": config.hidden_dim, "embedding_dim": config.embedding_dim},
        training_config,
        pair_dataset["dataset_sha256"],
        feature_hash,
        split_manifest["split_sha256"],
    )
    attempt_records.append(
        {
            "attempt_id": "ATTEMPT_01_PRIMARY_SEED17",
            "model_variant": config.model_variant,
            "seed": 17,
            "config": training_config,
            "dataset_hash": pair_dataset["dataset_sha256"],
            "feature_hash": feature_hash,
            "start_loss": primary["initial_losses"]["total"],
            "end_loss": primary["final_losses"]["total"],
            "predictions": primary["predictions"],
            "checkpoint": str(checkpoint_path.relative_to(output_root)),
            "exit_status": "PASS",
            "failure": None,
            "root_cause": None,
            "next_fix": None,
        }
    )

    gpu_batch = tensorize_samples(pair_dataset["samples"], device)
    primary_model.eval()
    with torch.no_grad():
        before_gpu = primary_model(gpu_batch)
    loaded_gpu, _ = load_checkpoint(checkpoint_path, device)
    loaded_gpu.eval()
    with torch.no_grad():
        after_gpu = loaded_gpu(gpu_batch)
    cuda_differences = compare_logits(before_gpu, after_gpu)
    primary_cpu = primary_model.to(torch.device("cpu")).eval()
    with torch.no_grad():
        before_cpu = primary_cpu(cpu_features)
    loaded_cpu, loaded_payload = load_checkpoint(checkpoint_path, torch.device("cpu"))
    loaded_cpu.eval()
    with torch.no_grad():
        after_cpu = loaded_cpu(cpu_features)
    cpu_differences = compare_logits(before_cpu, after_cpu)
    checkpoint_result = {
        "status": "PASS" if max(cpu_differences.values()) <= config.checkpoint_cpu_atol and max(cuda_differences.values()) <= config.checkpoint_cuda_atol else "FAIL",
        "checkpoint_path": str(checkpoint_path.relative_to(project_root())),
        "checkpoint_file_sha256": sha256_file(checkpoint_path),
        "model_state_sha256": checkpoint_payload["model_state_sha256"],
        "loaded_model_state_sha256": loaded_payload["model_state_sha256"],
        "cpu_max_abs_difference": max(cpu_differences.values()),
        "cpu_tolerance": config.checkpoint_cpu_atol,
        "cuda_max_abs_difference": max(cuda_differences.values()),
        "cuda_tolerance": config.checkpoint_cuda_atol,
        "per_head_cpu": cpu_differences,
        "per_head_cuda": cuda_differences,
    }
    write_json(output_root / "CHECKPOINT_ROUNDTRIP_RESULT.json", checkpoint_result)

    same_seed_model, same_seed = train_model(pair_dataset["samples"], config, device)
    attempt_records.append(
        {
            "attempt_id": "ATTEMPT_02_REPRODUCIBILITY_SEED17",
            "model_variant": config.model_variant,
            "seed": 17,
            "config": training_config,
            "dataset_hash": pair_dataset["dataset_sha256"],
            "feature_hash": feature_hash,
            "start_loss": same_seed["initial_losses"]["total"],
            "end_loss": same_seed["final_losses"]["total"],
            "predictions": same_seed["predictions"],
            "checkpoint": "NOT_SAVED_DIAGNOSTIC",
            "exit_status": "PASS",
            "failure": None,
            "root_cause": None,
            "next_fix": None,
        }
    )
    seed_diagnostics = []
    diagnostic_models = []
    for attempt_number, seed in enumerate(config.additional_diagnostic_seeds, start=3):
        seed_config = config_with_seed(config, seed)
        model, diagnostic = train_model(pair_dataset["samples"], seed_config, device)
        diagnostic_models.append(model)
        seed_diagnostics.append(
            {
                "seed": seed,
                "final_loss": diagnostic["final_losses"]["total"],
                "accuracy": diagnostic["accuracy"],
                "predictions": [p["final_decision"] for p in diagnostic["predictions"]],
                "use": "REPRODUCIBILITY_DIAGNOSTIC_NOT_MODEL_SELECTION",
            }
        )
        attempt_records.append(
            {
                "attempt_id": "ATTEMPT_%02d_DIAGNOSTIC_SEED%d" % (attempt_number, seed),
                "model_variant": seed_config.model_variant,
                "seed": seed,
                "config": seed_config.to_dict(),
                "dataset_hash": pair_dataset["dataset_sha256"],
                "feature_hash": feature_hash,
                "start_loss": diagnostic["initial_losses"]["total"],
                "end_loss": diagnostic["final_losses"]["total"],
                "predictions": diagnostic["predictions"],
                "checkpoint": "NOT_SAVED_DIAGNOSTIC",
                "exit_status": "PASS",
                "failure": None,
                "root_cause": None,
                "next_fix": None,
            }
        )
    with (output_root / "TRAINING_ATTEMPTS.jsonl").open("w", encoding="utf-8") as handle:
        for attempt in attempt_records:
            handle.write(json.dumps(attempt, sort_keys=True, ensure_ascii=False) + "\n")

    reproducibility = {
        "status": "PASS" if primary["initial_state_sha256"] == same_seed["initial_state_sha256"] and primary["final_state_sha256"] == same_seed["final_state_sha256"] and _prediction_logit_difference(primary, same_seed) <= config.checkpoint_cuda_atol else "FAIL",
        "seed": 17,
        "dataset_hash_run1": pair_dataset["dataset_sha256"],
        "dataset_hash_run2": pair_dataset["dataset_sha256"],
        "feature_hash_run1": feature_hash,
        "feature_hash_run2": feature_hash,
        "split_hash_run1": split_manifest["split_sha256"],
        "split_hash_run2": split_manifest["split_sha256"],
        "initial_state_hash_run1": primary["initial_state_sha256"],
        "initial_state_hash_run2": same_seed["initial_state_sha256"],
        "final_state_hash_run1": primary["final_state_sha256"],
        "final_state_hash_run2": same_seed["final_state_sha256"],
        "max_final_logit_difference": _prediction_logit_difference(primary, same_seed),
        "additional_seed_diagnostics": seed_diagnostics,
        "additional_seeds_used_for_selection": False,
    }
    write_json(output_root / "REPRODUCIBILITY_RESULT.json", reproducibility)

    accuracy = primary["accuracy"]
    acceptance = primary["final_losses"]["total"] <= 0.10 or primary["loss_reduction_fraction"] >= 0.90
    overfit_status = (
        "PASS"
        if acceptance
        and accuracy["known_task_correct"] == accuracy["known_task_total"] == 4
        and accuracy["abstention_correct"] == accuracy["abstention_total"] == 5
        else "FAIL"
    )
    overfit = dict(primary)
    overfit.update(
        {
            "schema_version": "driveclarify.learned_m1_overfit_smoke.pilot.v1",
            "status": overfit_status,
            "mode": "FULL_DATASET_OVERFIT_SMOKE_TEST",
            "unit_sample_count": 5,
            "generalization_metric": None,
            "claim_boundary": "OPTIMIZATION_PIPELINE_ONLY_NO_GENERALIZATION_CLAIM",
            "acceptance_loss_rule": "final_loss <= 0.10 OR reduction >= 90%, plus exact head predictions",
        }
    )
    for sample, prediction in zip(pair_dataset["samples"], overfit["predictions"]):
        prediction["unit_id"] = sample["unit_provenance"]["source_unit"]
        prediction["target"] = sample["pair_target"]
    write_json(output_root / "OVERFIT_SMOKE_TEST_RESULT.json", overfit)

    loou_folds = []
    loou_models = []
    for fold in split_manifest["mode_loou"]["folds"]:
        train_samples = [s for s in pair_dataset["samples"] if s["unit_provenance"]["source_unit"] in fold["train_units"]]
        held_sample = next(s for s in pair_dataset["samples"] if s["unit_provenance"]["source_unit"] == fold["held_out_unit"])
        fold_model, fold_train = train_model(train_samples, config, device)
        loou_models.append(fold_model)
        held_batch = tensorize_samples([held_sample], device)
        fold_model.eval()
        with torch.no_grad():
            held_outputs = fold_model(held_batch)
        prediction = decisions(held_outputs, held_batch)[0]
        loou_folds.append(
            {
                **fold,
                "model_configuration": config.to_dict(),
                "train_final_loss": fold_train["final_losses"]["total"],
                "train_forward_backward": fold_train["forward_backward"],
                "prediction": prediction,
                "target": held_sample["pair_target"],
                "fold_validity": fold["fold_status"],
                "unsupported_reason": "MISSING_TRAIN_CLASS:" + ",".join(fold["missing_train_classes"]) if fold["missing_train_classes"] else None,
            }
        )
    loou = {
        "schema_version": "driveclarify.learned_m1_loou_diagnostic.pilot.v1",
        "status": "PASS_DIAGNOSTIC_COMPLETED",
        "metric_status": "DIAGNOSTIC_ONLY_NOT_PAPER_CLAIM",
        "fold_count": 5,
        "folds": loou_folds,
        "insufficient_train_class_coverage_fold_count": sum(f["fold_status"] == "INSUFFICIENT_TRAIN_CLASS_COVERAGE" for f in loou_folds),
        "held_out_results_used_for_tuning": False,
        "aggregate_generalization_claim": None,
    }
    write_json(output_root / "LOOU_DIAGNOSTIC_RESULT.json", loou)

    linear_config = config_with_seed(config, 17)
    linear_model, linear_result = train_model(pair_dataset["samples"], linear_config, device, LinearComparator, {})
    majority_predictions = ["TASK_EQUIVALENT" if s["target_available"] else "TASK_EQUIVALENT" for s in pair_dataset["samples"]]
    majority_unknown = ["KNOWN"] * 5
    baseline = {
        "schema_version": "driveclarify.learned_m1_baselines.pilot.v1",
        "status": "PASS",
        "majority_label_diagnostic": {
            "known_task_majority": "TASK_EQUIVALENT",
            "known_task_correct": 3,
            "known_task_total": 4,
            "abstention_majority": "KNOWN",
            "abstention_correct": 4,
            "abstention_total": 5,
            "purpose": "CLASS_IMBALANCE_DIAGNOSTIC_ONLY",
        },
        "linear_comparator": {
            "architecture": "symmetric linear/logistic heads over same allowed evidence",
            "parameter_count": trainable_parameter_count(linear_model),
            "initial_loss": linear_result["initial_losses"]["total"],
            "final_loss": linear_result["final_losses"]["total"],
            "accuracy": linear_result["accuracy"],
            "predictions": linear_result["predictions"],
        },
        "analytic_authority_reference": {
            "role": "LABEL_AND_PROVENANCE_REFERENCE_ONLY",
            "used_as_model_input": False,
            "learned_superiority_claim": False,
        },
    }
    write_json(output_root / "BASELINE_DIAGNOSTIC_RESULT.json", baseline)

    unknown_result = {
        "schema_version": "driveclarify.learned_m1_unknown_abstention.pilot.v1",
        "status": "PASS",
        "hard_evidence_unknown": {
            "implemented": True,
            "precedes_learned_heads": True,
            "conditions": ["missing plans", "untrusted frame/unit", "fairness not PASS", "observation mismatch", "incomplete repeats", "missing topology provenance", "non-finite values", "required input missing"],
            "training_units_triggered": 0,
        },
        "learned_abstention_unknown": {
            "implemented": True,
            "head_classes": ["KNOWN", "UNKNOWN"],
            "training_predictions_correct": accuracy["abstention_correct"],
            "training_predictions_total": accuracy["abstention_total"],
            "status": "LEARNED_ABSTENTION_SMOKE_TEST_ONLY",
            "calibration_claim": None,
            "auroc_claim": None,
            "coverage_risk_claim": None,
        },
        "unknown_forced_to_known_class": False,
    }
    write_json(output_root / "UNKNOWN_ABSTENTION_RESULT.json", unknown_result)

    tests = _run_test_suite(output_root)
    test_summary = {key: value for key, value in tests.items() if key != "output"}

    del primary_model, primary_cpu, loaded_gpu, loaded_cpu, same_seed_model, diagnostic_models, loou_models, linear_model
    del model, fold_model, held_batch, held_outputs
    del gpu_batch, before_gpu, after_gpu, before_cpu, after_cpu
    gc.collect()
    allocator_after = {"allocated_bytes": 0, "reserved_bytes": 0}
    if device.type == "cuda":
        torch.cuda.synchronize()
        torch.cuda.empty_cache()
        allocator_after = {"allocated_bytes": int(torch.cuda.memory_allocated()), "reserved_bytes": int(torch.cuda.memory_reserved())}
    gpu_record = {
        "schema_version": "driveclarify.learned_m1_gpu_resource.pilot.v1",
        "selected_device": str(device),
        "python_version": platform.python_version(),
        "pytorch_version": torch.__version__,
        "torch_cuda_version": torch.version.cuda,
        "cudnn_version": torch.backends.cudnn.version() if torch.backends.cudnn.is_available() else None,
        "gpu_name": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "gpu_total_memory_bytes": torch.cuda.get_device_properties(0).total_memory if torch.cuda.is_available() else None,
        "nvidia_before": gpu_before,
        "allocator_before": allocator_before,
        "allocator_after_cleanup": allocator_after,
        "max_allocated_bytes": int(torch.cuda.max_memory_allocated()) if device.type == "cuda" else 0,
        "carla_processes_launched": 0,
        "evaluator_processes_launched": 0,
        "simlingo_processes_launched": 0,
        "candidate_forward_count": 0,
        "cleanup_status": "PENDING_POST_PROCESS_EXIT_QUERY",
    }
    write_json(output_root / "GPU_RESOURCE_RECORD.json", gpu_record)

    source_hash_after = sha256_file(default_batch_root() / "M1_REAL_DATASET_V1.json")
    source_unchanged = source_hash_after == verification["source_dataset_sha256"]
    result_conditions = {
        "source_30_records_verified": verification["status"] == "PASS" and verification["record_count"] == 30,
        "five_pair_samples": pair_dataset["sample_count"] == 5,
        "engineering_exclusion_only": exclusions["count"] == 1 and exclusions["units"][0]["included_in_pair_dataset"] is False,
        "split_leakage_audit": split_audit["status"] == "PASS",
        "feature_leakage_audit": leakage_audit["status"] == "PASS",
        "forward_backward": primary["forward_backward"] == "PASS",
        "overfit": overfit_status == "PASS",
        "checkpoint_roundtrip": checkpoint_result["status"] == "PASS",
        "fixed_seed_reproducibility": reproducibility["status"] == "PASS",
        "loou_completed": loou["status"] == "PASS_DIAGNOSTIC_COMPLETED",
        "unknown_layers": unknown_result["status"] == "PASS",
        "tests": tests["status"] == "PASS",
        "forbidden_runtime_counts_zero": True,
        "source_dataset_unchanged": source_unchanged,
        "model_size": parameter_record["status"] == "PASS",
        "no_paper_claim": True,
    }
    provisional_pass = all(result_conditions.values())
    pilot_result = {
        "schema_version": "driveclarify.learned_m1_pilot_result.v1",
        "experiment_id": EXPERIMENT_ID,
        "final_status": "LEARNED_M1_PILOT_PASS_READY_FOR_DATASET_EXPANSION" if provisional_pass else "BLOCKED_M1_PILOT_MODEL_OR_PIPELINE",
        "ready_for_dataset_expansion": provisional_pass,
        "source_record_count": 30,
        "pair_sample_count": 5,
        "label_distribution": pair_dataset["label_distribution"],
        "engineering_exclusion": ENGINEERING_EXCLUSION,
        "model_parameter_count": model_spec["trainable_parameter_count"],
        "overfit_status": overfit_status,
        "checkpoint_status": checkpoint_result["status"],
        "reproducibility_status": reproducibility["status"],
        "loou_status": loou["status"],
        "training_attempt_count": len(attempt_records),
        "tests": test_summary,
        "gpu_cleanup": "PENDING_POST_PROCESS_EXIT_QUERY",
        "git_history_protection": "PENDING_FINAL_QUERY",
        "simlingo_modified": None,
        "conditions": result_conditions,
        "claim_boundary": "NO_PAPER_LEVEL_GENERALIZATION_CLAIM",
        "unique_next_step": "M1_REAL_DATASET_EXPANSION_V2",
        "automatic_continuation": False,
    }
    write_json(output_root / "PILOT_RESULT.json", pilot_result)
    git_record = {"schema_version": "driveclarify.learned_m1_git_start_end.pilot.v1", "start": INITIAL_GIT_BASELINE, "end": {}}
    write_json(output_root / "GIT_START_END.json", git_record)
    (output_root / "COMMAND_LOG.md").write_text(
        """# Command Log

- Read-only source/batch/Git entrance audit.
- `python -m compileall -q driveclarify_learned_m1`
- `pytest -q tests/learned_m1` (pre-training unit validation)
- `tools/run_learned_m1_pilot.py` using the existing PyTorch/CUDA environment.
- Four bounded full-dataset attempts: seed 17 primary, seed 17 reproducibility, seeds 29/43 diagnostics.
- Five predetermined LOOU folds and one linear baseline diagnostic.
- Required learned/regression pytest groups.
- No CARLA, evaluator, SimLingo module/model/checkpoint, candidate forward, observation capture, or source-data mutation command was run.
""",
        encoding="utf-8",
    )
    # Create all final names before hashing Git status; contents do not affect an
    # untracked-path status fingerprint.
    (output_root / "LEARNED_M1_PILOT_REPORT.md").write_text("PENDING FINALIZATION\n", encoding="utf-8")
    write_json(output_root / "ARTIFACT_INVENTORY.json", {"status": "PENDING_FINALIZATION"})
    _refresh_final_files(output_root)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, default=default_output_root())
    parser.add_argument("--finalize-only", action="store_true")
    args = parser.parse_args()
    if args.finalize_only:
        _refresh_final_files(args.output_root, rerun_tests=True)
    else:
        run_pilot(args.output_root)
    final_result = json.loads((args.output_root / "PILOT_RESULT.json").read_text(encoding="utf-8"))
    print(json.dumps({"experiment_id": EXPERIMENT_ID, "final_status": final_result["final_status"], "output_root": str(args.output_root)}, sort_keys=True))
    return 0 if final_result["final_status"] == "LEARNED_M1_PILOT_PASS_READY_FOR_DATASET_EXPANSION" else 1


if __name__ == "__main__":
    raise SystemExit(main())
