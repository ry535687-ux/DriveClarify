"""Serial TRAIN-only native backend for E1-R1 E1/E2 episodes."""

from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
import subprocess
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterator, Mapping

from driveclarify_paper_mvp_stage6b import backend as stage6b
from tools.run_grounded_language_v1_triad import run as run_grounded

from .contracts import (
    ARTIFACT_ROOT,
    FEATURE_FLAG,
    FIXTURE_ROOT,
    METHODS,
    PHYSICAL_FIXTURES,
    SCENARIO_ROOT,
    SCHEMA_PREFIX,
    file_sha256,
    physical_fixture,
)


ROOT = Path(__file__).resolve().parents[1]
METHOD_MAP = {
    "original_simlingo": "original_simlingo",
    "never_ask": "never_ask",
    "always_ask": "always_ask",
    "always_wait": "always_wait",
    "driveclarify_r0": "driveclarify",
    "driveclarify_grounded_v1": "driveclarify_grounded_v1",
}
GPU_LEASE_PATH = ROOT / ARTIFACT_ROOT / ".serial_gpu_lease.lock"


def _utc() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _free_gpu_memory_mb() -> int | None:
    result = subprocess.run(
        [
            "nvidia-smi",
            "--query-gpu=memory.free",
            "--format=csv,noheader,nounits",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    try:
        return int(result.stdout.splitlines()[0].strip())
    except (IndexError, ValueError):
        return None


@contextmanager
def serial_gpu_lease() -> Iterator[dict[str, Any]]:
    GPU_LEASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with GPU_LEASE_PATH.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        free = _free_gpu_memory_mb()
        if free is None or free < 7000:
            raise RuntimeError("E1R1_GPU_FREE_MEMORY_PREFLIGHT_BLOCKED:{}".format(free))
        receipt = {
            "schema_version": SCHEMA_PREFIX + ".gpu_lease.v1",
            "status": "ACQUIRED",
            "acquired_at_utc": _utc(),
            "owner_pid": os.getpid(),
            "free_memory_mb_before_launch": free,
            "lease_kind": "EXCLUSIVE_FLOCK_AUTO_RELEASE",
            "concurrent_grounding_heavy_runs_allowed": 0,
        }
        try:
            yield receipt
        finally:
            receipt["released_at_utc"] = _utc()
            receipt["free_memory_mb_after_cleanup"] = _free_gpu_memory_mb()
            receipt["status"] = "RELEASED"
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def resolve_episode(
    *, fixture_id: str, method_id: str, episode_id: str
) -> stage6b.EpisodeSpec:
    if method_id not in METHODS:
        raise ValueError("E1R1_UNKNOWN_METHOD:" + method_id)
    fixture = physical_fixture(fixture_id)
    route_path = (ROOT / FIXTURE_ROOT / (fixture_id + ".xml")).resolve()
    fixture_index = next(
        index
        for index, row in enumerate(PHYSICAL_FIXTURES, start=1)
        if row["fixture_id"] == fixture_id
    )
    mechanism = str(fixture["mechanism_family"])
    # Baseline smoke reuses only the frozen Stage6B TRAIN passenger-channel
    # identity contract.  Physical route, actors, weather and instruction are
    # replaced by this E1-R1 fixture and remain identical across all methods.
    source_scenario = {"ACT": "DCV0-S001", "ASK": "DCV0-S002", "WAIT": "DCV0-S005"}[mechanism]
    seed = (5300 if mechanism == "WAIT" else 5100) + ((fixture_index - 1) % 3 + 1)
    source_method = METHOD_MAP[method_id] if method_id != "driveclarify_grounded_v1" else "original_simlingo"
    source = stage6b.resolve_train_episode(
        scenario_id=source_scenario,
        seed=seed,
        method_id=source_method,
    )
    return replace(
        source,
        episode_id=episode_id,
        scenario_id=fixture_id,
        split="train",
        seed=seed,
        method_id=METHOD_MAP[method_id],
        town=str(fixture["town"]),
        route_id=str(fixture["route_id"]),
        route_path=route_path,
        raw_instruction=str(fixture["instruction"]),
        information_expected=False,
        schedule_sha256=file_sha256(route_path),
    )


def artifact_directory(*, stage: str, fixture_id: str, method_id: str) -> Path:
    if stage not in {"e1", "e2"}:
        raise ValueError("E1R1_STAGE_NOT_E1_OR_E2")
    return ROOT / ARTIFACT_ROOT / stage / fixture_id / method_id


def _write_lease(output: Path, lease: Mapping[str, Any]) -> None:
    path = output / "GPU_LEASE_RECEIPT.json"
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(dict(lease), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(str(temporary), str(path))


def execute_episode(
    *,
    stage: str,
    fixture_id: str,
    method_id: str,
    episode_id: str,
    visualization: bool = False,
    capture_desktop: bool = False,
    wall_timeout_seconds: float = 240.0,
) -> Mapping[str, Any]:
    spec = resolve_episode(
        fixture_id=fixture_id, method_id=method_id, episode_id=episode_id
    )
    output = artifact_directory(stage=stage, fixture_id=fixture_id, method_id=method_id)
    if output.exists() and any(output.iterdir()):
        raise RuntimeError("E1R1_ARTIFACT_DIRECTORY_NOT_EMPTY:" + str(output))
    fixture = physical_fixture(fixture_id)
    lease_receipt = None
    try:
        with serial_gpu_lease() as lease:
            lease_receipt = lease
            if method_id == "driveclarify_grounded_v1":
                overrides = {
                    FEATURE_FLAG: "1",
                    "SCENARIO_RUNNER_ROOT": str((ROOT / SCENARIO_ROOT).resolve()),
                    "DRIVECLARIFY_E1R1_FIXTURE_ID": fixture_id,
                    "DRIVECLARIFY_E1R1_WAIT_ACTOR_MOTION_SOURCE": fixture["motion_source"],
                }
                if fixture.get("motion"):
                    overrides.update(
                        {
                            "DRIVECLARIFY_E1R1_WAIT_ACTOR_DWELL_SECONDS": str(
                                fixture["motion"]["visible_dwell_seconds"]
                            ),
                            "DRIVECLARIFY_E1R1_WAIT_ACTOR_SPEED_MPS": str(
                                fixture["motion"]["speed_mps"]
                            ),
                            "DRIVECLARIFY_E1R1_WAIT_ACTOR_DISTANCE_M": str(
                                fixture["motion"]["distance_m"]
                            ),
                        }
                    )
                result = run_grounded(
                    output,
                    case=fixture["mechanism_family"].casefold(),
                    seed=spec.seed,
                    method_id=method_id,
                    device="cpu",
                    control=True,
                    answer="The nearer white van.",
                    answer_delay=0.1,
                    timeout_seconds=wall_timeout_seconds,
                    episode_spec=spec,
                    visualization=visualization,
                    post_hoc_world_state=True,
                    terminate_on_runtime_terminal=True,
                    capture_desktop=capture_desktop,
                    environment_overrides=overrides,
                )
            else:
                original_root = stage6b.SCENARIO_DISCOVERY_ROOT
                stage6b.SCENARIO_DISCOVERY_ROOT = (ROOT / SCENARIO_ROOT).resolve()
                try:
                    result = stage6b.UnifiedNativeBackend(
                        wall_timeout_seconds=wall_timeout_seconds,
                        no_progress_timeout_seconds=25.0,
                    ).run(spec, output, visualization=False)
                finally:
                    stage6b.SCENARIO_DISCOVERY_ROOT = original_root
    finally:
        if lease_receipt is not None and output.is_dir():
            _write_lease(output, lease_receipt)
    return result


__all__ = [
    "artifact_directory",
    "execute_episode",
    "resolve_episode",
    "serial_gpu_lease",
]
