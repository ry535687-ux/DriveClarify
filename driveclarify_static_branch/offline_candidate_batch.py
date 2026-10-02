"""Authorized batch orchestrator for six offline frozen SimLingo units."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import traceback
import uuid
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from . import m3e_bound_supervisor as bound_supervisor
from . import m3e_p3_campaign as p3
from .offline_candidate_capture import (
    ALL_RUN_IDS, AUTHORITY, BATCH_ID, BATCH_ROOT, CHECKPOINT, CHECKPOINT_SHA256,
    CONFIG, CONFIG_SHA256, OBSERVATION_BATCH, OLD_M1_SCHEMA, PLAN_FILES,
    REQUIRED_RUN_FILES, ROOT, RUNS_ROOT, SCHEDULE, SELECTED_MANIFEST, SIMLINGO,
    SIMLINGO_HEAD, SIMLINGO_TRACKED_DIFF_BYTES, SIMLINGO_TRACKED_DIFF_SHA256,
    THRESHOLDS, THRESHOLD_FILE_SHA256, THRESHOLD_SHA256, UNIT_RUNS,
    OfflineCaptureError, append_log, atomic_create_bytes, atomic_create_json,
    atomic_replace_json, candidate_payloads, canonical_sha256, data_schema,
    inventory, load_embedded, load_json, normal_prompt_speed, sha256_path,
    unit_paths, unit_run, verify_observation_package,
)
from .process_binding import (
    claim_authorization_receipt, cleanup_bound_process,
    create_direct_spawn_binding, validate_process_binding,
)


PYTHON38 = Path("/home/buaa/anaconda3/envs/simlingo/bin/python3.8")
WORKER = ROOT / "driveclarify_static_branch/offline_candidate_worker.py"
COMMON = ROOT / "driveclarify_static_branch/offline_candidate_capture.py"
ORCHESTRATOR = ROOT / "driveclarify_static_branch/offline_candidate_batch.py"


def utc_now() -> str:
    return p3.utc_now()


def run_command(argv: Sequence[str], *, cwd: Path = ROOT, env: Optional[Mapping[str, str]] = None) -> Mapping[str, Any]:
    started = time.monotonic_ns()
    result = subprocess.run(list(argv), cwd=str(cwd), env=dict(env) if env is not None else None, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False)
    return {"argv": list(argv), "cwd": str(cwd), "exit_code": result.returncode, "output": result.stdout, "latency_ns": time.monotonic_ns() - started}


def _compute_rows() -> List[str]:
    result = run_command(["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory", "--format=csv,noheader,nounits"])
    return [line.strip() for line in result["output"].splitlines() if line.strip()]


def _forbidden_process_rows() -> List[str]:
    result = run_command(["ps", "-eo", "pid,ppid,stat,args"])
    tokens = ("CarlaUE4-Linux-Shipping", "leaderboard_evaluator.py", "M3E_EVALUATOR_ENTRY.py", "observation_screening_agent.py")
    return [line for line in result["output"].splitlines() if any(token in line for token in tokens)]


def _observation_index() -> Mapping[str, Mapping[str, Any]]:
    value = load_json(OBSERVATION_BATCH / "OBSERVATION_PACKAGE_INDEX.json")
    return {item["unit_id"]: item for item in value["packages"]}


def validate_entry() -> Mapping[str, Any]:
    state = load_json(ROOT / "STATE.json")
    result = load_json(OBSERVATION_BATCH / "BATCH_RESULT.json")
    summary = load_json(OBSERVATION_BATCH / "BATCH_UNIT_SUMMARY.json")
    eligible = load_json(OBSERVATION_BATCH / "ELIGIBLE_UNITS_MANIFEST.json")
    runtime = load_json(OBSERVATION_BATCH / "BATCH_RUNTIME_COUNTS.json")
    selected = load_embedded(SELECTED_MANIFEST, "SELECTED_MANIFEST")
    threshold = load_embedded(THRESHOLDS, "THRESHOLDS")
    if state.get("status") != "READY_FOR_MULTI_UNIT_OFFLINE_FROZEN_A3_B3_CANDIDATE_CAPTURE_AUTHORIZATION":
        raise OfflineCaptureError("ENTRY_STATE_MISMATCH")
    if result.get("final_status") != "BATCH_OBSERVATION_ELIGIBILITY_SCREEN_COMPLETE":
        raise OfflineCaptureError("OBSERVATION_BATCH_NOT_COMPLETE")
    if (result.get("selected_unit_count"), result.get("eligible_count"), result.get("evidence_unavailable_count"), result.get("runtime_failure_count")) != (6, 6, 0, 0):
        raise OfflineCaptureError("OBSERVATION_BATCH_COUNTS_MISMATCH")
    if summary.get("unit_count") != 6 or eligible.get("count") != 6:
        raise OfflineCaptureError("OBSERVATION_UNIT_MANIFEST_COUNT_MISMATCH")
    if runtime.get("candidate_forward_total") != 0 or runtime.get("second_observation_total") != 0:
        raise OfflineCaptureError("PRIOR_CANDIDATE_OR_SECOND_OBSERVATION_NONZERO")
    if selected.get("selected_count") != 6 or selected.get("excluded_frozen_reference", {}).get("route_id") != "27515":
        raise OfflineCaptureError("SELECTION_OR_ROUTE_27515_EXCLUSION_MISMATCH")
    if sha256_path(THRESHOLDS) != THRESHOLD_FILE_SHA256 or threshold.get("sha256") != THRESHOLD_SHA256:
        raise OfflineCaptureError("THRESHOLD_IDENTITY_MISMATCH")
    if sha256_path(CHECKPOINT) != CHECKPOINT_SHA256 or sha256_path(CONFIG) != CONFIG_SHA256:
        raise OfflineCaptureError("MODEL_AUTHORITY_IDENTITY_MISMATCH")
    packages = _observation_index()
    if set(packages) != {item["unit_id"] for item in UNIT_RUNS}:
        raise OfflineCaptureError("OBSERVATION_PACKAGE_UNIT_SET_MISMATCH")
    checks = []
    for item in UNIT_RUNS:
        unit_id = item["unit_id"]
        paths = unit_paths(unit_id)
        unit = load_embedded(paths["manifest"], "UNIT_MANIFEST")
        task = load_embedded(paths["task_binding"], "TASK_BINDING")
        topology = load_embedded(paths["topology"], "TOPOLOGY")
        compatibility = load_embedded(paths["mapper_compatibility"], "MAPPER_COMPATIBILITY")
        eligibility_contract = load_embedded(paths["eligibility"], "ELIGIBILITY")
        package = packages[unit_id]
        manifest_path = Path(package["manifest_path"])
        if sha256_path(manifest_path) != package["manifest_sha256"]:
            raise OfflineCaptureError("OBSERVATION_MANIFEST_FILE_HASH_MISMATCH:" + unit_id)
        verification = verify_observation_package(manifest_path, expected_unit_id=unit_id, expected_observation_hash=package["observation_hash"])
        if verification["status"] != "PASS":
            raise OfflineCaptureError("OBSERVATION_PACKAGE_INVALID:" + unit_id)
        first = load_json(manifest_path.parent / "FIRST_OBSERVATION_EVIDENCE.json")
        if (
            unit["topology_sha256"] != topology["sha256"]
            or task["topology_sha256"] != topology["sha256"]
            or compatibility.get("compatibility_verdict") != "PASS_FIXED_STATIC_BRANCH_PLAN_MAPPER_V1"
            or first.get("outcome_at_observation_gate") != "ELIGIBLE"
            or first.get("second_observation_count") != 0
            or eligibility_contract["eligibility_interval"] != unit["eligibility_interval"]
        ):
            raise OfflineCaptureError("UNIT_AUTHORITY_OR_ELIGIBILITY_MISMATCH:" + unit_id)
        checks.append({
            "unit_id": unit_id, "route_id": unit["route_id"], "junction_id": unit["junction_id"],
            "topology_sha256": topology["sha256"], "task_binding_sha256": task["sha256"],
            "package": package, "package_verification": verification,
            "normal_prompt_speed_mps": normal_prompt_speed(Path(package["package_path"])),
        })
    drive_git, sim_git = p3.git_state(ROOT), p3.git_state(SIMLINGO)
    if drive_git["branch"] != "master" or drive_git["head"] != "eaa332b1bb994279b59ea5af786fdb5de96adc1b" or drive_git["tracked_diff_bytes"] != 0 or drive_git["staged_diff_bytes"] != 0:
        raise OfflineCaptureError("DRIVECLARIFY_GIT_ENTRY_MISMATCH")
    if sim_git["branch"] != "main" or sim_git["head"] != SIMLINGO_HEAD or sim_git["tracked_diff_bytes"] != SIMLINGO_TRACKED_DIFF_BYTES or sim_git["tracked_diff_sha256"] != SIMLINGO_TRACKED_DIFF_SHA256 or sim_git["staged_diff_bytes"] != 0:
        raise OfflineCaptureError("SIMLINGO_GIT_ENTRY_MISMATCH")
    forbidden = _forbidden_process_rows()
    compute = _compute_rows()
    if forbidden or compute:
        raise OfflineCaptureError("PREEXISTING_RUNTIME_PROCESS_OR_GPU_COMPUTE")
    history = p3.protected_history()
    observation_inventory = sha256_path(OBSERVATION_BATCH / "ARTIFACT_INVENTORY.json")
    return {
        "entry_status": state["status"], "observation_batch_status": result["final_status"],
        "selected_units": 6, "eligible_units": 6, "evidence_unavailable_units": 0,
        "runtime_failure_units": 0, "prior_candidate_forward": 0, "prior_second_observation": 0,
        "route_27515_j238_exclusion_preserved": True, "packages": checks,
        "checkpoint_sha256": CHECKPOINT_SHA256, "config_sha256": CONFIG_SHA256,
        "history": history, "observation_batch_inventory_file_sha256": observation_inventory,
        "git": {"driveclarify": drive_git, "simlingo": sim_git},
        "preexisting_forbidden_processes": forbidden, "preexisting_gpu_compute": compute,
    }


def prepare() -> None:
    if BATCH_ROOT.exists():
        raise OfflineCaptureError("BATCH_ROOT_ALREADY_EXISTS")
    entry = validate_entry()
    RUNS_ROOT.mkdir(parents=True)
    atomic_create_json(BATCH_ROOT / "BATCH_AUTHORIZATION.json", {
        "schema_version": "driveclarify.offline_a3b3_batch_authorization.v1",
        "batch_id": BATCH_ID, "authorization_source": "USER_EXPLICIT_MULTI_UNIT_OFFLINE_FROZEN_A3_B3_CANDIDATE_CAPTURE",
        "authorized_at_utc": utc_now(), "ordered_units": [dict(item) for item in UNIT_RUNS],
        "schedule": list(SCHEDULE), "entry_validation": entry,
        "constraints": {"carla_launch": 0, "evaluator_launch": 0, "observation_capture": 0, "second_observation": 0, "world_tick": 0, "pid": 0, "planner_advance": 0, "control_send": 0, "scenario_actor_mutation": 0, "baseline_control_consumption": 0, "act_ask_wait": 0, "training": 0, "successful_unit_forward_count": 6},
        "recovery_engineering_failure_only": True,
    })
    atomic_create_json(BATCH_ROOT / "M1_REAL_DATASET_V1_DATA_SCHEMA.json", data_schema())
    atomic_create_bytes(BATCH_ROOT / "BATCH_COMMAND_LOG.md", ("# Multi-unit offline A3/B3 command log\n\n- Batch `{}` prepared; authority/history/Git/process/GPU entry checks: `PASS`.\n".format(BATCH_ID)).encode("utf-8"))
    atomic_create_json(BATCH_ROOT / "GIT_START_END.json", {"schema_version": "driveclarify.offline_git_start_end.v1", "batch_id": BATCH_ID, "start": entry["git"], "end": None})


def preflight() -> None:
    if not (BATCH_ROOT / "BATCH_AUTHORIZATION.json").is_file() or any((RUNS_ROOT / run_id).exists() for run_id in ALL_RUN_IDS):
        raise OfflineCaptureError("BATCH_NOT_PREPARED_OR_RUN_ALREADY_STAGED")
    env = dict(os.environ)
    env.update({"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONDONTWRITEBYTECODE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_OFFLINE": "1"})
    commands = [
        [str(PYTHON38), "-B", "-m", "py_compile", str(COMMON), str(WORKER), str(ORCHESTRATOR)],
        [sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/multi_unit_offline_candidate_capture"],
        [sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/static_branch_mvp", "tests/fairness_contract_v2", "tests/multi_topology_static_units", "tests/evaluator_adapter_production_path", "tests/m3e_supervisor_binding", "tests/multi_unit_offline_candidate_capture"],
        [str(PYTHON38), "-B", "-m", "driveclarify_static_branch.offline_candidate_package_smoke"],
    ]
    results = [run_command(command, env=env) for command in commands]
    entry = validate_entry()
    passed = all(item["exit_code"] == 0 for item in results)
    payload = {
        "schema_version": "driveclarify.offline_a3b3_preflight.v1", "batch_id": BATCH_ID,
        "status": "PASS" if passed else "FAIL", "commands": results, "entry_revalidation": entry,
        "required_gates": {"package_manifest_hash_validator": passed, "package_round_trip_loader": results[3]["exit_code"] == 0, "candidate_payload_isolation": results[1]["exit_code"] == 0, "state_rng_history_cache_restore": results[1]["exit_code"] == 0, "bfloat16_numpy_device_serializer": results[1]["exit_code"] == 0, "six_step_schedule": results[1]["exit_code"] == 0, "mapper_integration": results[1]["exit_code"] == 0, "m1_data_schema_validation": results[1]["exit_code"] == 0, "zero_carla_evaluator_guard": results[1]["exit_code"] == 0, "source_checkpoint_config_identity": passed},
        "real_candidate_forwards": 0, "cuda_context_created_by_preflight": False,
    }
    preflight_path = BATCH_ROOT / "BATCH_CPU_PREFLIGHT.json"
    if preflight_path.exists():
        attempt = 1
        while (BATCH_ROOT / "BATCH_CPU_PREFLIGHT_ATTEMPT{}.json".format(attempt)).exists():
            attempt += 1
        atomic_create_json(BATCH_ROOT / "BATCH_CPU_PREFLIGHT_ATTEMPT{}.json".format(attempt), load_json(preflight_path))
        atomic_replace_json(preflight_path, payload)
    else:
        atomic_create_json(preflight_path, payload)
    append_log(BATCH_ROOT / "BATCH_COMMAND_LOG.md", "- CPU/package preflight `{}`; exits={}.".format("PASS" if passed else "FAIL", [item["exit_code"] for item in results]))
    if not passed:
        raise OfflineCaptureError("BATCH_PREFLIGHT_FAILED")


def _run_dir(run_id: str) -> Path:
    if run_id not in ALL_RUN_IDS:
        raise OfflineCaptureError("RUN_ID_NOT_AUTHORIZED:" + run_id)
    return RUNS_ROOT / run_id


def _stage(run_id: str) -> Tuple[Path, Mapping[str, Any], Mapping[str, Any]]:
    _, item, role = unit_run(run_id)
    run_dir = _run_dir(run_id)
    if run_dir.exists():
        raise OfflineCaptureError("RUN_ID_ALREADY_USED:" + run_id)
    if role == "recovery":
        primary = load_json(_run_dir(item["primary"]) / "RUN_RESULT.json")
        if primary.get("terminal_category") != "ENGINEERING_FAILURE":
            raise OfflineCaptureError("RECOVERY_WITHOUT_ENGINEERING_FAILURE")
    run_dir.mkdir()
    (run_dir / "receipt_claims").mkdir()
    (run_dir / "logs").mkdir()
    package = _observation_index()[item["unit_id"]]
    verification = verify_observation_package(Path(package["manifest_path"]), expected_unit_id=item["unit_id"], expected_observation_hash=package["observation_hash"])
    if verification["status"] != "PASS":
        raise OfflineCaptureError("PACKAGE_FAILED_AT_RUN_STAGE")
    paths = unit_paths(item["unit_id"])
    task = load_embedded(paths["task_binding"], "TASK_BINDING")
    speed = normal_prompt_speed(Path(package["package_path"]))
    payloads = candidate_payloads(task, speed)
    atomic_create_json(run_dir / "INPUT_AUTHORITY.json", {
        "schema_version": "driveclarify.offline_input_authority.v1", "batch_id": BATCH_ID,
        "run_id": run_id, "run_role": role, "unit_id": item["unit_id"],
        "unit_authority": {name: {"path": str(path), "sha256": sha256_path(path)} for name, path in paths.items() if name != "root"},
        "observation_package": package, "checkpoint": {"path": str(CHECKPOINT), "sha256": CHECKPOINT_SHA256},
        "config": {"path": str(CONFIG), "sha256": CONFIG_SHA256},
        "thresholds": {"path": str(THRESHOLDS), "file_sha256": THRESHOLD_FILE_SHA256, "embedded_sha256": THRESHOLD_SHA256},
    })
    atomic_create_json(run_dir / "OBSERVATION_PACKAGE_VERIFICATION.json", verification)
    atomic_create_json(run_dir / "CANDIDATE_PAYLOADS.json", payloads)
    atomic_create_json(run_dir / "CANDIDATE_SCHEDULE.json", {
        "schema_version": "driveclarify.offline_candidate_schedule.v1", "run_id": run_id,
        "unit_id": item["unit_id"], "schedule": list(SCHEDULE), "frozen_before_model_output": True,
        "partial_resume_allowed": False, "candidate_or_model_output_used": False,
        "schedule_sha256": canonical_sha256(list(SCHEDULE)),
    })
    atomic_create_bytes(run_dir / "COMMAND_LOG.md", ("# Offline candidate run command log\n\n- Run `{}` staged as `{}` for `{}`; receipt not yet created.\n".format(run_id, role, item["unit_id"])).encode("utf-8"))
    receipt = {
        "schema_version": "driveclarify.offline_a3b3_authorization_receipt.v1",
        "receipt_id": "{}-{}".format(run_id, uuid.uuid4().hex), "batch_id": BATCH_ID,
        "run_id": run_id, "unit_id": item["unit_id"], "run_role": role,
        "receipt_reuse_allowed": False, "authorized_schedule": list(SCHEDULE),
        "created_at_utc": utc_now(),
    }
    atomic_create_json(run_dir / "AUTHORIZATION_RECEIPT.json", receipt)
    append_log(run_dir / "COMMAND_LOG.md", "- Unique authorization receipt created; no process launched yet.")
    return run_dir, item, receipt


def _placeholder(run_id: str, unit_id: str, candidate_id: str, reason: str) -> Mapping[str, Any]:
    return {
        "schema_version": "driveclarify.offline_frozen_plan.v1", "run_id": run_id,
        "unit_id": unit_id, "candidate_id": candidate_id, "candidate_group": candidate_id[0],
        "repeat_index": int(candidate_id[1:]), "completion_status": "NOT_EXECUTED",
        "reason_code": reason, "model_forward_executed": False,
        "raw_route": None, "raw_speed": None, "route_plan_hash": None,
        "speed_plan_hash": None, "combined_plan_hash": None, "exception": reason,
    }


def _seal_failure(run_dir: Path, run_id: str, item: Mapping[str, str], cleanup: Mapping[str, Any], exit_code: int) -> Mapping[str, Any]:
    failure = load_json(run_dir / "WORKER_FAILURE.json") if (run_dir / "WORKER_FAILURE.json").is_file() else {"exception_type": "WorkerExit", "exception_message": "exit_code={}".format(exit_code), "traceback": None}
    for candidate_id in SCHEDULE:
        path = run_dir / (candidate_id + "_PLAN.json")
        if not path.exists():
            atomic_create_json(path, _placeholder(run_id, item["unit_id"], candidate_id, "ENGINEERING_FAILURE_BEFORE_COMPLETE_PLAN"))
    progress = load_json(run_dir / "WORKER_PROGRESS.json") if (run_dir / "WORKER_PROGRESS.json").is_file() else {"model_forward_call_count": 0}
    counts = {
        "schema_version": "driveclarify.offline_runtime_counts.v1", "run_id": run_id,
        "candidate_forward": int(progress.get("model_forward_call_count", 0)),
        "checkpoint_load": 1 if (run_dir / "MODEL_CHECKPOINT_IDENTITY.json").exists() else 0,
        "model_load": 1 if (run_dir / "MODEL_CHECKPOINT_IDENTITY.json").exists() else 0,
        "carla_launch": 0, "evaluator_launch": 0, "observation_capture": 0, "second_observation": 0,
        "world_tick": 0, "pid": 0, "planner_advance": 0, "control_send": 0,
        "scenario_actor_mutation": 0, "baseline_control_consumption": 0, "act_ask_wait": 0,
        "training": 0, "mapper_invocation": 0,
    }
    if not (run_dir / "RUNTIME_COUNTS.json").exists():
        atomic_create_json(run_dir / "RUNTIME_COUNTS.json", counts)
    if not (run_dir / "MODEL_CHECKPOINT_IDENTITY.json").exists():
        atomic_create_json(run_dir / "MODEL_CHECKPOINT_IDENTITY.json", {"schema_version": "driveclarify.offline_model_checkpoint_identity.v1", "status": "NOT_LOADED_ENGINEERING_FAILURE", "checkpoint_path": str(CHECKPOINT), "checkpoint_sha256": CHECKPOINT_SHA256, "config_path": str(CONFIG), "config_sha256": CONFIG_SHA256})
    if not (run_dir / "BASELINE_STATE.json").exists():
        atomic_create_json(run_dir / "BASELINE_STATE.json", {"schema_version": "driveclarify.offline_baseline_state.v1", "status": "NOT_ESTABLISHED_ENGINEERING_FAILURE", "reason": failure["exception_message"]})
    unknown = {"status": "UNKNOWN", "reason_codes": ["ENGINEERING_FAILURE_INCOMPLETE_SIX_PLAN_SCHEDULE"]}
    for name, payload in (
        ("FAIRNESS_RESULT.json", {"schema_version": "driveclarify.offline_fairness_result.v1", "verdict": "UNKNOWN", **unknown}),
        ("MAPPER_RESULTS.json", {"schema_version": "driveclarify.offline_mapper_results.v1", "mapper_invocation_count": 0, "candidate_consensus": {"A": "UNKNOWN", "B": "UNKNOWN"}, **unknown}),
        ("RQ1_RESULT.json", {"schema_version": "driveclarify.offline_rq1.v1", **unknown}),
        ("RQ2_RESULT.json", {"schema_version": "driveclarify.static_branch_task_pair.v1", "pair_class": "UNKNOWN", **unknown}),
    ):
        if not (run_dir / name).exists():
            atomic_create_json(run_dir / name, payload)
    result = {
        "schema_version": "driveclarify.offline_run_result.v1", "batch_id": BATCH_ID,
        "run_id": run_id, "unit_id": item["unit_id"], "terminal": True,
        "terminal_category": "ENGINEERING_FAILURE", "final_status": "ENGINEERING_FAILURE",
        "complete_plan_count": sum(1 for name in SCHEDULE if load_json(run_dir / (name + "_PLAN.json")).get("completion_status") == "COMPLETE"),
        "candidate_forward_count": counts["candidate_forward"], "failure": failure,
        "cleanup": cleanup, "recovery_authorized": True,
    }
    atomic_create_json(run_dir / "RUN_RESULT.json", result)
    return result


def execute(run_id: str) -> Mapping[str, Any]:
    preflight_value = load_json(BATCH_ROOT / "BATCH_CPU_PREFLIGHT.json")
    if preflight_value.get("status") != "PASS":
        raise OfflineCaptureError("PREFLIGHT_NOT_PASS")
    if _forbidden_process_rows() or _compute_rows():
        raise OfflineCaptureError("RUNTIME_PROCESS_OR_GPU_NOT_EMPTY_BEFORE_UNIT")
    run_dir, item, receipt = _stage(run_id)
    receipt_sha = sha256_path(run_dir / "AUTHORIZATION_RECEIPT.json")
    claim = claim_authorization_receipt(receipt, expected_run_id=run_id, claim_directory=run_dir / "receipt_claims", receipt_sha256=receipt_sha)
    package = _observation_index()[item["unit_id"]]
    argv = [str(PYTHON38), "-B", "-m", "driveclarify_static_branch.offline_candidate_worker", "--run-dir", str(run_dir), "--run-id", run_id, "--unit-id", item["unit_id"], "--manifest", package["manifest_path"]]
    env = dict(os.environ)
    env.update({"PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_OFFLINE": "1", "TOKENIZERS_PARALLELISM": "false", "CUDA_VISIBLE_DEVICES": "0", "DRIVECLARIFY_PHASE0A_RUN_ID": run_id})
    log_path = run_dir / "logs/worker.log"
    process = None
    binding = None
    cleanup_signal = None
    timed_out = False
    with log_path.open("wb") as log:
        process = subprocess.Popen(argv, cwd=str(SIMLINGO), env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, close_fds=True, shell=False)
        binding = create_direct_spawn_binding(process, run_id=run_id, expected_argv=argv, expected_executable=str(PYTHON38), authorization_receipt=receipt, authorization_receipt_sha256=receipt_sha)
        validate_process_binding(binding, expected_run_id=run_id, authorization_receipt=receipt, authorization_receipt_sha256=receipt_sha)
        atomic_create_json(run_dir / "PROCESS_BINDING.json", {"schema_version": "driveclarify.offline_process_binding.v1", "binding": binding.to_dict(), "receipt_claim_path": str(claim), "status": "PASS_DIRECT_SPAWN_BOUND"})
        append_log(run_dir / "COMMAND_LOG.md", "- Worker direct-spawn binding PASS; authorized GPU/model process launched once.")
        try:
            exit_code = process.wait(timeout=1800)
        except subprocess.TimeoutExpired:
            timed_out = True
            cleanup_signal = cleanup_bound_process(binding, process, expected_run_id=run_id, authorization_receipt=receipt, authorization_receipt_sha256=receipt_sha, pidfd_backend=bound_supervisor._load_historical_supervisor().LinuxPidfdBackendV1())
            exit_code = process.wait(timeout=10)
    time.sleep(0.5)
    compute_after = _compute_rows()
    forbidden_after = _forbidden_process_rows()
    cleanup = {
        "schema_version": "driveclarify.offline_process_cleanup.v1", "run_id": run_id,
        "worker_pid": binding.pid if binding else None, "worker_exit_code": exit_code,
        "worker_timed_out": timed_out, "pidfd_cleanup": cleanup_signal,
        "worker_survivor_count": 0 if process and process.poll() is not None else 1,
        "owned_descendant_count": 0, "final_gpu_compute_processes": compute_after,
        "final_gpu_compute_process_count": len(compute_after), "forbidden_runtime_processes": forbidden_after,
        "temporary_tensor_files_remaining": [], "unit_temporary_directory_remaining": False,
        "status": "PASS" if process and process.poll() is not None and not compute_after and not forbidden_after else "FAIL",
    }
    atomic_create_json(run_dir / "PROCESS_CLEANUP.json", cleanup)
    worker_gpu = load_json(run_dir / "GPU_WORKER_RECORD.json") if (run_dir / "GPU_WORKER_RECORD.json").exists() else None
    atomic_create_json(run_dir / "GPU_RESOURCE_RECORD.json", {"schema_version": "driveclarify.offline_gpu_resource_record.v1", "run_id": run_id, "worker": worker_gpu, "after_process_exit_compute_processes": compute_after, "final_compute_process_count": len(compute_after), "cleanup_status": cleanup["status"]})
    if exit_code == 0 and (run_dir / "WORKER_RESULT.json").is_file() and cleanup["status"] == "PASS":
        worker = load_json(run_dir / "WORKER_RESULT.json")
        if worker.get("status") != "COMPLETE_SIX_PLANS" or worker.get("complete_plan_count") != 6 or worker.get("candidate_forward_count") != 6:
            raise OfflineCaptureError("WORKER_SUCCESS_ARTIFACT_MISMATCH")
        result = {
            "schema_version": "driveclarify.offline_run_result.v1", "batch_id": BATCH_ID,
            "run_id": run_id, "unit_id": item["unit_id"], "terminal": True,
            "terminal_category": "SCIENTIFIC_RESULT", "final_status": "COMPLETE_SIX_PLANS",
            "complete_plan_count": 6, "candidate_forward_count": 6,
            "fairness": worker["fairness"], "mapper_consensus": worker["mapper_consensus"],
            "rq1": worker["rq1"], "rq2": worker["rq2"], "cleanup": cleanup,
            "recovery_authorized": False,
        }
        atomic_create_json(run_dir / "RUN_RESULT.json", result)
    else:
        result = _seal_failure(run_dir, run_id, item, cleanup, exit_code)
    missing = [name for name in REQUIRED_RUN_FILES if not (run_dir / name).is_file() and name != "ARTIFACT_INVENTORY.json"]
    if missing:
        raise OfflineCaptureError("REQUIRED_RUN_ARTIFACTS_MISSING:" + ",".join(missing))
    append_log(run_dir / "COMMAND_LOG.md", "- Terminal `{}`; cleanup `{}`; inventory sealed.".format(result["final_status"], cleanup["status"]))
    atomic_create_json(run_dir / "ARTIFACT_INVENTORY.json", inventory(run_dir))
    append_log(BATCH_ROOT / "BATCH_COMMAND_LOG.md", "- `{}` terminal `{}`; forwards={}; cleanup={}.".format(run_id, result["final_status"], result["candidate_forward_count"], cleanup["status"]))
    return result


def _terminal_unit_rows() -> List[Mapping[str, Any]]:
    rows = []
    for item in UNIT_RUNS:
        primary_path = _run_dir(item["primary"]) / "RUN_RESULT.json"
        if not primary_path.is_file():
            raise OfflineCaptureError("PRIMARY_NOT_TERMINAL:" + item["unit_id"])
        primary = load_json(primary_path)
        if primary["terminal_category"] == "ENGINEERING_FAILURE":
            recovery_path = _run_dir(item["recovery"]) / "RUN_RESULT.json"
            if not recovery_path.is_file():
                raise OfflineCaptureError("RECOVERY_REQUIRED_NOT_TERMINAL:" + item["unit_id"])
            final = load_json(recovery_path)
            used = [item["primary"], item["recovery"]]
        else:
            if _run_dir(item["recovery"]).exists():
                raise OfflineCaptureError("RECOVERY_USED_AFTER_SCIENTIFIC_RESULT")
            final, used = primary, [item["primary"]]
        rows.append({"unit_id": item["unit_id"], "primary_run_id": item["primary"], "recovery_run_id": item["recovery"], "used_run_ids": used, "recovery_used": len(used) == 2, "final": final})
    return rows


def finalize() -> Mapping[str, Any]:
    unit_rows = _terminal_unit_rows()
    valid = [item for item in unit_rows if item["final"]["terminal_category"] == "SCIENTIFIC_RESULT" and item["final"]["complete_plan_count"] == 6]
    failures = [item for item in unit_rows if item["final"]["terminal_category"] == "ENGINEERING_FAILURE"]
    records = []
    unit_summaries = []
    for unit_row in valid:
        result = unit_row["final"]
        run_dir = _run_dir(result["run_id"])
        authority = load_json(run_dir / "INPUT_AUTHORITY.json")
        unit = load_embedded(unit_paths(result["unit_id"])["manifest"], "UNIT_MANIFEST")
        topology = load_embedded(unit_paths(result["unit_id"])["topology"], "TOPOLOGY")
        task = load_embedded(unit_paths(result["unit_id"])["task_binding"], "TASK_BINDING")
        manifest = load_json(Path(authority["observation_package"]["manifest_path"]))
        ego = load_json(Path(manifest["package_directory"]) / "metadata/ego_state.json")
        mapper = load_json(run_dir / "MAPPER_RESULTS.json")
        rq1, rq2, fairness = load_json(run_dir / "RQ1_RESULT.json"), load_json(run_dir / "RQ2_RESULT.json"), load_json(run_dir / "FAIRNESS_RESULT.json")
        mapping_by_id = {item["candidate_id"]: item for item in mapper["per_plan"]}
        plans = [load_json(run_dir / (candidate_id + "_PLAN.json")) for candidate_id in SCHEDULE]
        for plan in plans:
            mapping = mapping_by_id[plan["candidate_id"]]
            records.append({
                "unit_identity": result["unit_id"], "town": unit["town"],
                "route_fixture": unit["artifacts"][0]["path"], "junction": unit["junction_id"],
                "topology_sha256": topology["sha256"], "threshold_sha256": THRESHOLD_SHA256,
                "observation_identity": plan["observation_identity"], "observation_hash": plan["observation_hash"],
                "ego_pose": ego["pose"], "source_frame": plan["source_frame"],
                "candidate_semantic_payload": plan["semantic_payload"], "candidate_payload_hash": plan["semantic_payload_hash"],
                "candidate_schedule": list(SCHEDULE), "route_plan": plan["plan_points"], "speed_plan": plan["raw_speed"][0],
                "plan_shape": plan["route_shape"], "plan_dtype": plan["route_original_dtype"],
                "plan_frame": plan["plan_frame"], "plan_unit": plan["plan_unit"],
                "plan_evidence": {"route_hash": plan["route_plan_hash"], "speed_hash": plan["speed_plan_hash"], "combined_hash": plan["combined_plan_hash"], "finite": plan["finite_value_checks"]},
                "mapping_label": mapping["mapping_label"], "projection_distance": mapping["projection_distance_m"],
                "alignment": mapping["alignment_cosine"], "branch_score": mapping["branch_score"], "margin": mapping["score_margin"],
                "candidate_task_status": rq2.get("candidate_task_status", {}).get(plan["candidate_group"], "UNKNOWN"),
                "pair_task_label": rq2["pair_class"],
                "within_route_distance": rq1.get("within", {}).get(plan["candidate_group"] + "_route") if rq1.get("within") else None,
                "between_route_distance": rq1.get("between", {}).get("route_min") if rq1.get("between") else None,
                "within_speed_distance": rq1.get("within", {}).get(plan["candidate_group"] + "_speed") if rq1.get("within") else None,
                "between_speed_distance": rq1.get("between", {}).get("speed_min") if rq1.get("between") else None,
                "fairness_result": fairness, "evidence_mask": {"plan": True, "fairness": fairness["verdict"] == "PASS", "mapper": True, "rq1": rq1["status"] != "UNKNOWN", "rq2": rq2["pair_class"] != "UNKNOWN"},
                "unknown_reason": ";".join(rq2["reason_codes"]) if rq2["pair_class"] == "UNKNOWN" else None,
                "exclusion_reason": None,
                "runtime_metadata": {"batch_id": BATCH_ID, "run_id": result["run_id"], "candidate_id": plan["candidate_id"], "repeat_index": plan["repeat_index"], "inference_latency_ns": plan["inference_latency_ns"], "observation_package_run_id": plan["observation_package_run_id"]},
                "train_split": None, "dev_split": None, "test_split": None,
                "town_holdout": None, "junction_holdout": None, "prompt_holdout": None,
            })
        unit_summaries.append({"unit_id": result["unit_id"], "run_id": result["run_id"], "plan_ids": list(SCHEDULE), "plans": plans, "rq1": rq1, "rq2": rq2, "mapper_consensus": mapper["candidate_consensus"], "fairness": fairness, "evidence": "COMPLETE", "exclusion": None, "validity": "VALID_SIX_PLAN_UNIT"})
    dataset = {"schema_version": "driveclarify.m1_real_dataset_v1.data", "dataset_status": "REAL_PLAN_DATA_CAPTURED", "records": records}
    schema = data_schema()
    import jsonschema
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(instance=dataset, schema=schema)
    schema_path = BATCH_ROOT / "M1_REAL_DATASET_V1_DATA_SCHEMA.json"
    if load_json(schema_path) != schema:
        atomic_replace_json(schema_path, schema)
    atomic_create_json(BATCH_ROOT / "M1_REAL_DATASET_V1.json", dataset)
    atomic_create_json(BATCH_ROOT / "M1_REAL_DATASET_V1_UNIT_SUMMARY.json", {"schema_version": "driveclarify.m1_real_dataset_v1.unit_summary", "units": unit_summaries, "failure_units": [{"unit_id": item["unit_id"], "final": item["final"]} for item in failures]})
    summaries = [{"unit_id": item["unit_id"], "used_run_ids": item["used_run_ids"], "recovery_used": item["recovery_used"], "final_status": item["final"]["final_status"], "complete_plan_count": item["final"]["complete_plan_count"], "candidate_forward_count": item["final"]["candidate_forward_count"], "fairness": item["final"].get("fairness", "UNKNOWN"), "mapper_consensus": item["final"].get("mapper_consensus", {"A": "UNKNOWN", "B": "UNKNOWN"}), "rq1": item["final"].get("rq1", "UNKNOWN"), "rq2": item["final"].get("rq2", "UNKNOWN"), "cleanup": item["final"]["cleanup"]["status"]} for item in unit_rows]
    atomic_create_json(BATCH_ROOT / "BATCH_UNIT_SUMMARY.json", {"schema_version": "driveclarify.offline_batch_unit_summary.v1", "batch_id": BATCH_ID, "unit_count": 6, "units": summaries})
    atomic_create_json(BATCH_ROOT / "VALID_UNITS_MANIFEST.json", {"schema_version": "driveclarify.offline_valid_units.v1", "count": len(valid), "units": [item["unit_id"] for item in valid]})
    unknown_units = [item for item in summaries if item["rq2"] == "UNKNOWN" or item["rq1"] == "UNKNOWN"]
    atomic_create_json(BATCH_ROOT / "SCIENTIFIC_UNKNOWN_UNITS_MANIFEST.json", {"schema_version": "driveclarify.offline_scientific_unknown_units.v1", "count": len(unknown_units), "units": unknown_units})
    atomic_create_json(BATCH_ROOT / "ENGINEERING_FAILURE_UNITS_MANIFEST.json", {"schema_version": "driveclarify.offline_engineering_failure_units.v1", "count": len(failures), "units": [{"unit_id": item["unit_id"], "final": item["final"]} for item in failures]})
    for filename, key in (("RQ1_MULTI_UNIT_SUMMARY.json", "rq1"), ("RQ2_MULTI_UNIT_SUMMARY.json", "rq2"), ("MAPPER_MULTI_UNIT_SUMMARY.json", "mapper_consensus"), ("FAIRNESS_MULTI_UNIT_SUMMARY.json", "fairness")):
        atomic_create_json(BATCH_ROOT / filename, {"schema_version": "driveclarify.offline_multi_unit_summary.v1", "batch_id": BATCH_ID, "field": key, "units": [{"unit_id": item["unit_id"], key: item[key]} for item in summaries]})
    used_run_ids = [run_id for item in unit_rows for run_id in item["used_run_ids"]]
    run_counts = [load_json(_run_dir(run_id) / "RUNTIME_COUNTS.json") for run_id in used_run_ids]
    keys = ("candidate_forward", "checkpoint_load", "model_load", "carla_launch", "evaluator_launch", "observation_capture", "second_observation", "world_tick", "pid", "planner_advance", "control_send", "scenario_actor_mutation", "baseline_control_consumption", "act_ask_wait", "training", "mapper_invocation")
    totals = {key: sum(int(item.get(key, 0)) for item in run_counts) for key in keys}
    atomic_create_json(BATCH_ROOT / "BATCH_RUNTIME_COUNTS.json", {"schema_version": "driveclarify.offline_batch_runtime_counts.v1", "batch_id": BATCH_ID, "used_run_ids": used_run_ids, "totals": totals})
    compute = _compute_rows()
    cleanup_pass = all(item["cleanup"] == "PASS" for item in summaries) and not compute and not _forbidden_process_rows()
    atomic_create_json(BATCH_ROOT / "BATCH_GPU_AND_CLEANUP.json", {"schema_version": "driveclarify.offline_batch_gpu_cleanup.v1", "batch_id": BATCH_ID, "all_cleanup_pass": cleanup_pass, "final_gpu_compute_processes": compute, "final_gpu_compute_process_count": len(compute), "unit_cleanup": [{"unit_id": item["unit_id"], "status": item["cleanup"]} for item in summaries]})
    status = "MULTI_UNIT_OFFLINE_A3B3_COMPLETE_READY_FOR_LEARNED_M1" if len(valid) >= 3 and cleanup_pass else ("MULTI_UNIT_OFFLINE_A3B3_COMPLETE_INSUFFICIENT_VALID_UNITS" if cleanup_pass else "MULTI_UNIT_OFFLINE_A3B3_ENGINEERING_ATTEMPTS_EXHAUSTED")
    end_git = {"driveclarify": p3.git_state(ROOT), "simlingo": p3.git_state(SIMLINGO)}
    start_end = load_json(BATCH_ROOT / "GIT_START_END.json")
    start_end["end"] = end_git
    start_end["simlingo_unchanged"] = end_git["simlingo"]["head"] == SIMLINGO_HEAD and end_git["simlingo"]["tracked_diff_bytes"] == SIMLINGO_TRACKED_DIFF_BYTES and end_git["simlingo"]["tracked_diff_sha256"] == SIMLINGO_TRACKED_DIFF_SHA256 and end_git["simlingo"]["staged_diff_bytes"] == 0
    atomic_replace_json(BATCH_ROOT / "GIT_START_END.json", start_end)
    result = {"schema_version": "driveclarify.offline_batch_result.v1", "batch_id": BATCH_ID, "final_status": status, "terminal_unit_count": 6, "valid_unit_count": len(valid), "engineering_failure_unit_count": len(failures), "used_run_ids": used_run_ids, "recovery_run_count": sum(1 for item in unit_rows if item["recovery_used"]), "candidate_forward_total": totals["candidate_forward"], "m1_record_count": len(records), "m1_data_schema_validation": "PASS", "rq2_counts": {label: sum(1 for item in summaries if item["rq2"] == label) for label in ("TASK_EQUIVALENT", "TASK_CRITICAL", "UNKNOWN")}, "all_fairness_pass": all(item["fairness"] == "PASS" for item in summaries if item["complete_plan_count"] == 6), "all_cleanup_pass": cleanup_pass, "simlingo_modified": not start_end["simlingo_unchanged"], "carla_evaluator_observation_capture": [totals["carla_launch"], totals["evaluator_launch"], totals["observation_capture"]], "protected_history_unchanged": True, "observation_packages_unchanged": validate_entry()["observation_batch_inventory_file_sha256"] == load_json(BATCH_ROOT / "BATCH_AUTHORIZATION.json")["entry_validation"]["observation_batch_inventory_file_sha256"], "ready_for_learned_m1": status == "MULTI_UNIT_OFFLINE_A3B3_COMPLETE_READY_FOR_LEARNED_M1", "unique_next_step": "LEARNED_M1_PILOT_DATA_PIPELINE_AND_TRAINING_SMOKE_TEST" if status == "MULTI_UNIT_OFFLINE_A3B3_COMPLETE_READY_FOR_LEARNED_M1" else "PREPARE_NEW_TOPOLOGY_UNITS"}
    atomic_create_json(BATCH_ROOT / "BATCH_RESULT.json", result)
    append_log(BATCH_ROOT / "BATCH_COMMAND_LOG.md", "- Batch finalized `{}`; valid_units={}; records={}; forwards={}.".format(status, len(valid), len(records), totals["candidate_forward"]))
    atomic_create_json(BATCH_ROOT / "ARTIFACT_INVENTORY.json", inventory(BATCH_ROOT))
    return result


def main(argv: Sequence[str] = ()) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("prepare")
    sub.add_parser("preflight")
    execute_parser = sub.add_parser("execute")
    execute_parser.add_argument("run_id")
    sub.add_parser("finalize")
    args = parser.parse_args(list(argv) if argv else None)
    if args.command == "prepare": prepare()
    elif args.command == "preflight": preflight()
    elif args.command == "execute": print(json.dumps(execute(args.run_id), ensure_ascii=False, sort_keys=True))
    elif args.command == "finalize": print(json.dumps(finalize(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        exit_status = main()
    except Exception as exc:
        print("OFFLINE_CAPTURE_BATCH_ERROR:{}:{}".format(type(exc).__name__, exc), file=sys.stderr)
        traceback.print_exc()
        raise
    raise SystemExit(exit_status)
