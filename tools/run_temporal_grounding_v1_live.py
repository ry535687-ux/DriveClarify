#!/usr/bin/env python3
"""Run one isolated real-CARLA TRAIN Temporal Grounding V1 diagnostic.

The diagnostic fixture is deliberately outside the frozen paper population.
This runner reuses the already-audited native launcher and exact-owned-process
cleanup, while removing every Stage6A/Stage6B/Language-V1 activation variable.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping

# Support direct ``python tools/run_temporal_grounding_v1_live.py`` execution.
_BOOTSTRAP_ROOT = Path(__file__).resolve().parents[1]
if str(_BOOTSTRAP_ROOT) not in sys.path:
    sys.path.insert(0, str(_BOOTSTRAP_ROOT))

from driveclarify_temporal_grounding_v1.contracts import FEATURE_FLAG
from driveclarify_temporal_grounding_v1.runtime import (
    CONTROL_ENV,
    DEVICE_ENV,
    OUTPUT_ENV,
    RAW_INSTRUCTION_ENV,
    RECEIPT_FILENAME,
)
from driveclarify_paper_mvp_evaluation.display_preflight import (
    collect_native_display_facts,
    evaluate_native_display_preflight,
)
from driveclarify_paper_mvp_stage6b import backend as native


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_JSON = (
    REPOSITORY_ROOT
    / "driveclarify_temporal_grounding_v1/fixtures/DC-TGV1-TRAIN-DIAGNOSTIC-001.json"
)
ROUTE_XML = (
    REPOSITORY_ROOT
    / "driveclarify_temporal_grounding_v1/fixtures/DC-TGV1-TRAIN-DIAGNOSTIC-001.xml"
)
GROUNDING_DINO_CHECKPOINT = REPOSITORY_ROOT / "pretrained/grounding-dino-tiny/model.safetensors"

PASS_SHADOW = "SHADOW_FRESH_TEMPORAL_REPLAN_PASS"
PASS_CONTROL = "BOUNDED_WAIT_TO_REPLAN_CLOSED_LOOP_PASS"
BLOCKED_PREFIX = "BLOCKED_"


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


def diagnostic_episode(*, seed: int, method_id: str) -> native.EpisodeSpec:
    fixture = json.loads(FIXTURE_JSON.read_text(encoding="utf-8"))
    fixture_sha = _file_sha256(FIXTURE_JSON)
    route_sha = _file_sha256(ROUTE_XML)
    return native.EpisodeSpec(
        episode_id="DC-TGV1-DIAG-001-{}-{}".format(seed, method_id),
        runtime_config_id="DC-TGV1-TRAIN-DIAGNOSTIC-001",
        scenario_id=str(fixture["scenario_id"]),
        runtime_fixture_id=str(fixture["fixture_id"]),
        split="train",
        seed=int(seed),
        method_id=str(method_id),
        town=str(fixture["town"]),
        route_id=str(fixture["route_id"]),
        route_path=ROUTE_XML.resolve(),
        runtime_manifest_path=FIXTURE_JSON.resolve(),
        raw_instruction=str(fixture["instruction"]),
        information_expected=True,
        schedule_sha256=route_sha,
        runtime_manifest_sha256=fixture_sha,
        promotion_receipt_path=FIXTURE_JSON.resolve(),
        promotion_receipt_payload_sha256=fixture_sha,
    )


def build_environment(
    spec: native.EpisodeSpec,
    output_dir: Path,
    *,
    detector_device: str,
    control: bool,
    post_hoc_world_state: bool,
) -> dict[str, str]:
    environment = native.build_environment(spec, output_dir, visualization=False)
    forbidden_prefixes = (
        "DRIVECLARIFY_PAPER_MVP_STAGE6A_",
        "DRIVECLARIFY_PAPER_MVP_STAGE6B_",
    )
    for key in tuple(environment):
        if key.startswith(forbidden_prefixes):
            environment.pop(key, None)
    for key in (
        "DRIVECLARIFY_LANGUAGE_GROUNDING_V1",
        "DRIVECLARIFY_LANGUAGE_GROUNDING_V1_OUTPUT_DIR",
        "DRIVECLARIFY_LANGUAGE_GROUNDING_V1_RAW_INSTRUCTION",
        "DRIVECLARIFY_LANGUAGE_GROUNDING_V1_TRIGGER_FRAME",
    ):
        environment.pop(key, None)
    environment.update(
        {
            FEATURE_FLAG: "1",
            OUTPUT_ENV: str(output_dir),
            RAW_INSTRUCTION_ENV: spec.raw_instruction,
            DEVICE_ENV: detector_device,
            CONTROL_ENV: "1" if control else "0",
            "DRIVECLARIFY_SHADOW_V0": "1",
            "DRIVECLARIFY_SHADOW_OUTPUT_DIR": str(output_dir),
            "DRIVECLARIFY_PROBE_RUN_ID": spec.episode_id,
        }
    )
    if post_hoc_world_state:
        environment["DRIVECLARIFY_WORLDSTATE_OUTPUT"] = str(
            output_dir / "post_hoc_world_state.jsonl"
        )
    else:
        environment.pop("DRIVECLARIFY_WORLDSTATE_OUTPUT", None)
    return environment


def preflight(
    spec: native.EpisodeSpec,
    output_dir: Path,
    environment: Mapping[str, str],
    *,
    control: bool,
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
    if spec.split != "train":
        blockers.append("TEMPORAL_DIAGNOSTIC_NOT_TRAIN")
    if not FIXTURE_JSON.is_file() or not ROUTE_XML.is_file():
        blockers.append("TEMPORAL_DIAGNOSTIC_FIXTURE_MISSING")
    if not GROUNDING_DINO_CHECKPOINT.is_file():
        blockers.append("GROUNDING_DINO_CHECKPOINT_MISSING")
    if native._file_sha256(native.CHECKPOINT) != native.CHECKPOINT_SHA256:
        blockers.append("SIMLINGO_CHECKPOINT_HASH_MISMATCH")
    if native._git_head(native.SIMLINGO_ROOT) != native.SIMLINGO_PROTECTED_HEAD:
        blockers.append("SIMLINGO_HEAD_MISMATCH")
    if native._git_diff_sha256(native.SIMLINGO_ROOT) != native.SIMLINGO_PROTECTED_DIFF_SHA256:
        blockers.append("SIMLINGO_PROTECTED_DIFF_MISMATCH")
    conflicts = {
        key: environment.get(key)
        for key in (
            "DRIVECLARIFY_PAPER_MVP_STAGE6A_LIVE",
            "DRIVECLARIFY_PAPER_MVP_STAGE6B_LIVE",
            "DRIVECLARIFY_LANGUAGE_GROUNDING_V1",
        )
        if environment.get(key) is not None
    }
    if conflicts:
        blockers.append("TEMPORAL_RUNTIME_COACTIVATION")
    receipt = {
        "schema_version": "driveclarify.temporal_grounding_v1.native_preflight.v1",
        "status": "PASS" if not blockers else "BLOCKED",
        "observed_at_utc": _utc_now(),
        "episode": spec.to_dict(),
        "train_only": True,
        "population_status": "NOT_PART_OF_FROZEN_PAPER_POPULATION",
        "native_display": dict(evaluated.evidence),
        "local_session": session,
        "blockers": list(dict.fromkeys(blockers)),
        "feature_flags": {
            FEATURE_FLAG: environment.get(FEATURE_FLAG),
            CONTROL_ENV: environment.get(CONTROL_ENV),
            "coactivated": conflicts,
        },
        "fixture_sha256": _file_sha256(FIXTURE_JSON),
        "route_sha256": _file_sha256(ROUTE_XML),
        "simlingo": {
            "head": native._git_head(native.SIMLINGO_ROOT),
            "protected_diff_sha256": native._git_diff_sha256(native.SIMLINGO_ROOT),
            "checkpoint_sha256": native._file_sha256(native.CHECKPOINT),
        },
        "control_mode": "BOUNDED_CLOSED_LOOP" if control else "NO_CONTROL_SHADOW",
        "dev_attempt_count": 0,
        "test_attempt_count": 0,
        "test_consumed": False,
    }
    _atomic_json(output_dir / "TEMPORAL_GROUNDING_V1_NATIVE_PREFLIGHT.json", receipt)
    return receipt


def _terminal(status: Any, *, control: bool) -> bool:
    text = str(status or "")
    return text == (PASS_CONTROL if control else PASS_SHADOW) or text.startswith(BLOCKED_PREFIX)


def run(
    output_dir: Path,
    *,
    seed: int,
    method_id: str,
    detector_device: str,
    control: bool,
    post_hoc_world_state: bool,
    timeout_seconds: float,
) -> Mapping[str, Any]:
    output_dir = output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise RuntimeError("TEMPORAL_GROUNDING_V1_OUTPUT_DIR_NOT_EMPTY:" + str(output_dir))
    output_dir.mkdir(parents=True, exist_ok=True)
    spec = diagnostic_episode(seed=seed, method_id=method_id)
    environment = build_environment(
        spec,
        output_dir,
        detector_device=detector_device,
        control=control,
        post_hoc_world_state=post_hoc_world_state,
    )
    checked = preflight(spec, output_dir, environment, control=control)
    if checked["status"] != "PASS":
        raise RuntimeError("BLOCKED_NATIVE_PREFLIGHT:" + ",".join(checked["blockers"]))
    command = native.build_command(spec, output_dir)
    launch = {
        "schema_version": "driveclarify.temporal_grounding_v1.launch.v1",
        "episode": spec.to_dict(),
        "command": list(command),
        "cwd": str(native.SIMLINGO_ROOT),
        "population_status": "NOT_PART_OF_FROZEN_PAPER_POPULATION",
        "feature_flag_default": "OFF",
        "feature_flag": FEATURE_FLAG,
        "stage6a_runtime_enabled": False,
        "stage6b_runtime_enabled": False,
        "language_grounding_v1_enabled": False,
        "control_mode": "BOUNDED_CLOSED_LOOP" if control else "NO_CONTROL_SHADOW",
        "candidate_direct_vehicle_control_writes_allowed": 0,
        "m3_direct_vehicle_control_writes_allowed": 0,
        "new_pid_allowed": 0,
        "existing_pid_window_allowed": 1 if control else 0,
        "post_hoc_world_state": {
            "enabled": post_hoc_world_state,
            "output": (
                str(output_dir / "post_hoc_world_state.jsonl")
                if post_hoc_world_state
                else None
            ),
            "policy_input": False,
            "purpose": "POST_HOC_SAFETY_AND_EVENT_TIMELINE_AUDIT_ONLY",
        },
        "diagnostic_timeout_seconds": timeout_seconds,
        "dev_attempt_count": 0,
        "test_attempt_count": 0,
        "test_consumed": False,
    }
    _atomic_json(output_dir / "TEMPORAL_GROUNDING_V1_LAUNCH_CONTRACT.json", launch)
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
                    if _terminal(live.get("status"), control=control):
                        termination_reason = "TEMPORAL_RECEIPT_CAPTURED"
                        os.kill(evaluator_pid, signal.SIGINT)
                        timeout_signal = "SIGINT"
                        break
                except (json.JSONDecodeError, OSError):
                    pass
            if time.monotonic() - started >= timeout_seconds:
                termination_reason = "TEMPORAL_WALL_TIMEOUT"
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
    _atomic_json(output_dir / "TEMPORAL_GROUNDING_V1_CLEANUP_RECEIPT.json", cleanup)
    live_path = output_dir / RECEIPT_FILENAME
    live = json.loads(live_path.read_text(encoding="utf-8")) if live_path.is_file() else None
    leaderboard_path = output_dir / "leaderboard_results.json"
    leaderboard = (
        json.loads(leaderboard_path.read_text(encoding="utf-8"))
        if leaderboard_path.is_file()
        else {}
    )
    records = leaderboard.get("_checkpoint", {}).get("records", [])
    vehicle_collisions = [
        item
        for record in records
        for item in record.get("infractions", {}).get("collisions_vehicle", [])
    ]
    expected_status = PASS_CONTROL if control else PASS_SHADOW
    passed = (
        live is not None
        and live.get("status") == expected_status
        and cleanup.get("status") == "PASS"
        and not vehicle_collisions
    )
    result = {
        "schema_version": "driveclarify.temporal_grounding_v1.native_run_receipt.v1",
        "status": "PASS_NATIVE_TEMPORAL_CAPTURE_AND_CLEANUP" if passed else "BLOCKED_NATIVE_TEMPORAL_DIAGNOSTIC",
        "start_utc": started_utc,
        "end_utc": _utc_now(),
        "duration_wall_seconds": time.monotonic() - started,
        "termination_reason": termination_reason,
        "evaluator_return_code": return_code,
        "expected_live_status": expected_status,
        "live_status": None if live is None else live.get("status"),
        "live_level": None if live is None else live.get("level"),
        "real_native_carla": True,
        "physical_display": True,
        "headless": False,
        "population_status": "NOT_PART_OF_FROZEN_PAPER_POPULATION",
        "control_mode": "BOUNDED_CLOSED_LOOP" if control else "NO_CONTROL_SHADOW",
        "candidate_direct_vehicle_control_writes": 0 if live is None else live.get("candidate_direct_vehicle_control_write_count"),
        "m3_direct_vehicle_control_writes": 0 if live is None else live.get("m3_direct_vehicle_control_write_count"),
        "new_pid_count": 0 if live is None else live.get("new_pid_count"),
        "cleanup_status": cleanup.get("status"),
        "post_hoc_safety": {
            "vehicle_collision_count": len(vehicle_collisions),
            "vehicle_collisions": vehicle_collisions,
            "required_for_pass": True,
            "policy_input": False,
        },
        "artifact_sha256": {
            RECEIPT_FILENAME: _file_sha256(live_path) if live_path.is_file() else None,
            "evaluator_stdout.log": _file_sha256(stdout_path),
        },
        "dev_attempt_count": 0,
        "test_attempt_count": 0,
        "test_consumed": False,
    }
    _atomic_json(output_dir / "TEMPORAL_GROUNDING_V1_NATIVE_RUN_RECEIPT.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--seed", type=int, default=5301)
    parser.add_argument("--method-id", default="driveclarify")
    parser.add_argument("--detector-device", choices=("cpu", "cuda"), default="cuda")
    parser.add_argument("--control", action="store_true")
    parser.add_argument("--post-hoc-world-state", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    args = parser.parse_args()
    print(
        json.dumps(
            run(
                args.output_dir,
                seed=args.seed,
                method_id=args.method_id,
                detector_device=args.detector_device,
                control=args.control,
                post_hoc_world_state=args.post_hoc_world_state,
                timeout_seconds=args.timeout_seconds,
            ),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
