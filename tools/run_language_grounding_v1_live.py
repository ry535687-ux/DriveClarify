#!/usr/bin/env python3
"""Run one isolated TRAIN-only Language Grounding V1 native diagnostic.

This runner reuses the frozen native launch/process-cleanup primitives without
enabling or editing the Stage6A/Stage6B runtime.  It stops the evaluator after
the V1 receipt reaches a terminal diagnostic state; candidate plans are never
returned to the PID by the V1 runtime.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any, Mapping, Optional

from driveclarify_language_grounding_v1.contracts import FEATURE_FLAG
from driveclarify_language_grounding_v1.runtime import (
    DEVICE_ENV,
    OUTPUT_ENV,
    RAW_INSTRUCTION_ENV,
    RECEIPT_FILENAME,
    TRIGGER_FRAME_ENV,
)
from driveclarify_paper_mvp_evaluation.display_preflight import (
    collect_native_display_facts,
    evaluate_native_display_preflight,
)
from driveclarify_paper_mvp_stage6b import backend as native


TERMINAL_STATUSES = {
    "REAL_PLAN_DIVERGENCE_OVER_REPEAT_NOISE_PASS",
    "BLOCKED_VISUAL_REFERENT_GROUNDING",
    "BLOCKED_CANDIDATE_TO_SIMLINGO_BINDING_COLLAPSE",
    "BLOCKED_SIMLINGO_GROUNDED_INTERPRETATION_SENSITIVITY",
    "UNKNOWN",
}


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(str(temporary), str(path))


def build_v1_environment(
    spec: native.EpisodeSpec,
    output_dir: Path,
    *,
    device: str,
    trigger_frame: Optional[int],
) -> dict[str, str]:
    environment = native.build_environment(spec, output_dir, visualization=False)
    for key in tuple(environment):
        if key.startswith("DRIVECLARIFY_PAPER_MVP_STAGE6B_"):
            environment.pop(key, None)
    for key in (
        "DRIVECLARIFY_PAPER_MVP_STAGE6A_LIVE",
        "DRIVECLARIFY_PAPER_MVP_STAGE6A_OUTPUT_DIR",
        "DRIVECLARIFY_PAPER_MVP_STAGE6A_VISUALIZATION",
    ):
        environment.pop(key, None)
    environment.update(
        {
            FEATURE_FLAG: "1",
            OUTPUT_ENV: str(output_dir),
            RAW_INSTRUCTION_ENV: spec.raw_instruction,
            DEVICE_ENV: device,
            "DRIVECLARIFY_SHADOW_V0": "1",
            "DRIVECLARIFY_SHADOW_OUTPUT_DIR": str(output_dir),
        }
    )
    if trigger_frame is not None:
        environment[TRIGGER_FRAME_ENV] = str(trigger_frame)
    else:
        environment.pop(TRIGGER_FRAME_ENV, None)
    return environment


def v1_preflight(
    spec: native.EpisodeSpec,
    output_dir: Path,
    environment: Mapping[str, str],
) -> Mapping[str, Any]:
    command = native.build_command(spec, output_dir)
    session = native._local_x11_session()
    facts = collect_native_display_facts(
        launch_arguments=command,
        launch_environment=environment,
        carla_executable=native.CARLA_ROOT / "CarlaUE4.sh",
        leaderboard_evaluator=native.EVALUATOR,
        checkpoint=native.CHECKPOINT,
        carla_port=native.RPC_PORT,
        traffic_manager_port=native.TRAFFIC_MANAGER_PORT,
    )
    evaluated = evaluate_native_display_preflight(facts)
    blockers = list(evaluated.blocker_codes)
    if session is None:
        blockers.append("LOCAL_SEAT0_X11_SESSION_NOT_VERIFIED")
    if native._file_sha256(native.CHECKPOINT) != native.CHECKPOINT_SHA256:
        blockers.append("SIMLINGO_CHECKPOINT_HASH_MISMATCH")
    if native._git_head(native.SIMLINGO_ROOT) != native.SIMLINGO_PROTECTED_HEAD:
        blockers.append("SIMLINGO_HEAD_MISMATCH")
    if native._git_diff_sha256(native.SIMLINGO_ROOT) != native.SIMLINGO_PROTECTED_DIFF_SHA256:
        blockers.append("SIMLINGO_PROTECTED_DIFF_MISMATCH")
    if environment.get("DRIVECLARIFY_PAPER_MVP_STAGE6B_LIVE") is not None:
        blockers.append("FROZEN_STAGE6B_RUNTIME_COACTIVATION")
    if environment.get("DRIVECLARIFY_PAPER_MVP_STAGE6A_LIVE") is not None:
        blockers.append("FROZEN_STAGE6A_RUNTIME_COACTIVATION")
    receipt = {
        "schema_version": "driveclarify.language_grounding_v1.native_preflight.v1",
        "status": "PASS" if not blockers else "BLOCKED",
        "observed_at_utc": _utc_now(),
        "episode": spec.to_dict(),
        "train_only": spec.split == "train",
        "native_display": dict(evaluated.evidence),
        "local_session": session,
        "blockers": list(dict.fromkeys(blockers)),
        "feature_flags": {
            FEATURE_FLAG: environment.get(FEATURE_FLAG),
            "DRIVECLARIFY_PAPER_MVP_STAGE6A_LIVE": environment.get(
                "DRIVECLARIFY_PAPER_MVP_STAGE6A_LIVE"
            ),
            "DRIVECLARIFY_PAPER_MVP_STAGE6B_LIVE": environment.get(
                "DRIVECLARIFY_PAPER_MVP_STAGE6B_LIVE"
            ),
        },
        "simlingo": {
            "head": native._git_head(native.SIMLINGO_ROOT),
            "protected_diff_sha256": native._git_diff_sha256(native.SIMLINGO_ROOT),
            "checkpoint_sha256": native._file_sha256(native.CHECKPOINT),
        },
        "dev_attempt_count": 0,
        "test_attempt_count": 0,
        "test_consumed": False,
    }
    _atomic_json(output_dir / "LANGUAGE_GROUNDING_V1_NATIVE_PREFLIGHT.json", receipt)
    return receipt


def run(
    output_dir: Path,
    *,
    scenario_id: str,
    seed: int,
    method_id: str,
    detector_device: str,
    trigger_frame: Optional[int],
    timeout_seconds: float,
) -> Mapping[str, Any]:
    output_dir = output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise RuntimeError("LANGUAGE_GROUNDING_V1_OUTPUT_DIR_NOT_EMPTY:" + str(output_dir))
    output_dir.mkdir(parents=True, exist_ok=True)
    spec = native.resolve_train_episode(
        scenario_id=scenario_id, seed=seed, method_id=method_id
    )
    environment = build_v1_environment(
        spec,
        output_dir,
        device=detector_device,
        trigger_frame=trigger_frame,
    )
    preflight = v1_preflight(spec, output_dir, environment)
    if preflight["status"] != "PASS":
        raise RuntimeError("BLOCKED_NATIVE_PREFLIGHT:" + ",".join(preflight["blockers"]))
    command = native.build_command(spec, output_dir)
    launch = {
        "schema_version": "driveclarify.language_grounding_v1.launch.v1",
        "episode": spec.to_dict(),
        "command": list(command),
        "cwd": str(native.SIMLINGO_ROOT),
        "parallel_research_branch": True,
        "stage6a_runtime_enabled": False,
        "stage6b_runtime_enabled": False,
        "feature_flag": FEATURE_FLAG,
        "detector_device": detector_device,
        "configured_trigger_frame": trigger_frame,
        "normal_pid_owner": "EXISTING_SIMLINGO_PID",
        "candidate_plan_commit_allowed": False,
        "candidate_control_writes_allowed": 0,
        "diagnostic_timeout_seconds": timeout_seconds,
        "dev_attempt_count": 0,
        "test_attempt_count": 0,
        "test_consumed": False,
    }
    _atomic_json(output_dir / "LANGUAGE_GROUNDING_V1_LAUNCH_CONTRACT.json", launch)
    stdout_path = output_dir / "evaluator_stdout.log"
    started_utc = _utc_now()
    started = time.monotonic()
    observed_pids: set[int] = set()
    observed_pgids: set[int] = set()
    termination_reason = "UNKNOWN"
    timeout_signal = None
    with stdout_path.open("w", encoding="utf-8") as stdout:
        process = subprocess.Popen(
            list(command),
            cwd=str(native.SIMLINGO_ROOT),
            env=environment,
            stdout=stdout,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        evaluator_pid = int(process.pid)
        observed_pids.add(evaluator_pid)
        observed_pgids.add(evaluator_pid)
        while process.poll() is None:
            descendants = native._descendants(evaluator_pid, native._process_rows())
            observed_pids.update(row.pid for row in descendants)
            observed_pgids.update(row.pgid for row in descendants if row.pgid > 1)
            receipt_path = output_dir / RECEIPT_FILENAME
            if receipt_path.is_file():
                try:
                    live = json.loads(receipt_path.read_text(encoding="utf-8"))
                    if live.get("status") in TERMINAL_STATUSES:
                        termination_reason = "DIAGNOSTIC_RECEIPT_CAPTURED"
                        os.kill(evaluator_pid, signal.SIGINT)
                        timeout_signal = "SIGINT"
                        break
                except (json.JSONDecodeError, OSError):
                    pass
            if time.monotonic() - started >= timeout_seconds:
                termination_reason = "DIAGNOSTIC_WALL_TIMEOUT"
                os.kill(evaluator_pid, signal.SIGINT)
                timeout_signal = "SIGINT"
                break
            time.sleep(1.0)
        if process.poll() is None:
            try:
                process.wait(timeout=90.0)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(evaluator_pid, signal.SIGTERM)
                    timeout_signal = "SIGINT_THEN_SIGTERM"
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=20.0)
                except subprocess.TimeoutExpired:
                    os.killpg(evaluator_pid, signal.SIGKILL)
                    timeout_signal = "SIGINT_THEN_SIGTERM_THEN_SIGKILL"
                    process.wait(timeout=10.0)
        return_code = int(process.wait())
    cleanup = native._cleanup_receipt(
        spec=spec,
        evaluator_pid=evaluator_pid,
        observed_pids=observed_pids,
        observed_pgids=observed_pgids,
        timeout_signal=timeout_signal,
    )
    _atomic_json(output_dir / "LANGUAGE_GROUNDING_V1_CLEANUP_RECEIPT.json", cleanup)
    live_path = output_dir / RECEIPT_FILENAME
    live = json.loads(live_path.read_text(encoding="utf-8")) if live_path.is_file() else None
    result = {
        "schema_version": "driveclarify.language_grounding_v1.native_run_receipt.v1",
        "status": (
            "PASS_DIAGNOSTIC_CAPTURE_AND_CLEANUP"
            if live is not None
            and live.get("status") in TERMINAL_STATUSES
            and cleanup.get("status") == "PASS"
            else "BLOCKED_NATIVE_DIAGNOSTIC"
        ),
        "start_utc": started_utc,
        "end_utc": _utc_now(),
        "duration_wall_seconds": time.monotonic() - started,
        "termination_reason": termination_reason,
        "evaluator_return_code": return_code,
        "live_status": None if live is None else live.get("status"),
        "live_level": None if live is None else live.get("level"),
        "real_native_carla": True,
        "physical_display": True,
        "headless": False,
        "candidate_control_writes": 0 if live is None else live.get("candidate_control_write_count"),
        "m3_control_writes": 0 if live is None else live.get("m3_control_write_count"),
        "cleanup_status": cleanup.get("status"),
        "artifact_sha256": {
            RECEIPT_FILENAME: _file_sha256(live_path) if live_path.is_file() else None,
            "evaluator_stdout.log": _file_sha256(stdout_path),
        },
        "dev_attempt_count": 0,
        "test_attempt_count": 0,
        "test_consumed": False,
    }
    _atomic_json(output_dir / "LANGUAGE_GROUNDING_V1_NATIVE_RUN_RECEIPT.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--scenario-id", default="DCV0-S002")
    parser.add_argument("--seed", type=int, default=5103)
    parser.add_argument("--method-id", default="driveclarify")
    parser.add_argument("--detector-device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--trigger-frame", type=int, default=2561)
    parser.add_argument("--timeout-seconds", type=float, default=240.0)
    args = parser.parse_args()
    print(
        json.dumps(
            run(
                args.output_dir,
                scenario_id=args.scenario_id,
                seed=args.seed,
                method_id=args.method_id,
                detector_device=args.detector_device,
                trigger_frame=args.trigger_frame,
                timeout_seconds=args.timeout_seconds,
            ),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
