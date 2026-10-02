"""TRAIN-only execution backend for Grounded Language V1 Extension E1.

The policy side reads only the frozen runtime projection.  Evaluator labels are
deliberately absent from this module and are joined by ``evaluation.py`` only
after the native process has terminated.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from driveclarify_paper_mvp_stage6b import backend as stage6b
from tools.run_grounded_language_v1_triad import run as run_grounded

from .contracts import (
    ARTIFACT_ROOT,
    BASELINE_METHOD_MAP,
    EXTENSION_METHODS,
    FREEZE_PATH,
    REPORT_ROOT,
    ExtensionContractError,
    assert_runtime_projection_clean,
    assert_train_only,
    file_sha256,
)


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_MANIFEST = REPORT_ROOT / "EXTENSION_RUNTIME_TRAIN_MANIFEST.json"


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _frozen_runtime() -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    freeze = _load_json(ROOT / FREEZE_PATH)
    manifest_path = ROOT / RUNTIME_MANIFEST
    expected = freeze["frozen_file_sha256"][str(RUNTIME_MANIFEST)]
    if file_sha256(manifest_path) != expected:
        raise ExtensionContractError("EXTENSION_RUNTIME_MANIFEST_HASH_MISMATCH")
    manifest = _load_json(manifest_path)
    assert_runtime_projection_clean(manifest)
    assert_train_only(str(manifest.get("split")))
    if int(manifest.get("record_count", -1)) != 12:
        raise ExtensionContractError("EXTENSION_RUNTIME_TRAIN_COUNT_NOT_12")
    return freeze, manifest


def runtime_record(scenario_id: str) -> Mapping[str, Any]:
    _, manifest = _frozen_runtime()
    matches = [
        row for row in manifest["records"] if row["scenario_id"] == scenario_id
    ]
    if len(matches) != 1:
        raise ExtensionContractError("EXTENSION_TRAIN_SCENARIO_BINDING_COUNT_NOT_ONE")
    row = matches[0]
    assert_train_only(str(row["split"]))
    return row


def resolve_episode(
    *, scenario_id: str, seed_index: int, method_id: str, episode_id: str
) -> stage6b.EpisodeSpec:
    """Resolve an extension slot from its no-gold runtime projection."""

    if method_id not in EXTENSION_METHODS:
        raise ExtensionContractError("EXTENSION_UNKNOWN_METHOD:" + method_id)
    row = runtime_record(scenario_id)
    seeds = tuple(int(value) for value in row["carla_seeds"])
    if seed_index not in (1, 2, 3):
        raise ExtensionContractError("EXTENSION_SEED_INDEX_OUT_OF_RANGE")
    carla_seed = seeds[seed_index - 1]
    source_method = BASELINE_METHOD_MAP.get(method_id, "original_simlingo")
    source = stage6b.resolve_train_episode(
        scenario_id=str(row["source_train_scenario_id"]),
        seed=carla_seed,
        method_id=source_method,
    )
    freeze = _load_json(ROOT / FREEZE_PATH)
    spec = replace(
        source,
        episode_id=str(episode_id),
        runtime_config_id=(
            "DC-GLV1-E1-{}-SEED-{}".format(scenario_id, seed_index)
        ),
        scenario_id=str(scenario_id),
        split="train",
        method_id=(method_id if method_id == "driveclarify_grounded_v1" else source_method),
        raw_instruction=str(row["raw_instruction"]),
        information_expected=False,
        schedule_sha256=str(freeze["freeze_payload_sha256"]),
    )
    assert_train_only(spec.split)
    return spec


def artifact_directory(
    *, scenario_id: str, seed_index: int, method_id: str
) -> Path:
    assert_train_only("train")
    return (
        ROOT
        / ARTIFACT_ROOT
        / "train"
        / scenario_id
        / ("seed_{}".format(seed_index))
        / method_id
    )


def execute_episode(
    *,
    scenario_id: str,
    seed_index: int,
    method_id: str,
    episode_id: str,
    visualization: bool = False,
    capture_desktop: bool = False,
    wall_timeout_seconds: float = 180.0,
    no_progress_timeout_seconds: float = 30.0,
) -> Mapping[str, Any]:
    """Execute exactly one frozen TRAIN slot through the appropriate backend."""

    spec = resolve_episode(
        scenario_id=scenario_id,
        seed_index=seed_index,
        method_id=method_id,
        episode_id=episode_id,
    )
    output = artifact_directory(
        scenario_id=scenario_id, seed_index=seed_index, method_id=method_id
    )
    if output.exists() and any(output.iterdir()):
        raise ExtensionContractError("EXTENSION_ARTIFACT_DIRECTORY_NOT_EMPTY:" + str(output))
    if method_id == "driveclarify_grounded_v1":
        return run_grounded(
            output,
            case="extension",
            seed=spec.seed,
            method_id=method_id,
            # SimLingo already occupies most of the 12 GiB device. Grounding
            # DINO runs online on CPU to avoid an invalid concurrent CUDA OOM.
            device="cpu",
            control=True,
            answer="The nearer white van.",
            answer_delay=0.1,
            timeout_seconds=wall_timeout_seconds,
            episode_spec=spec,
            visualization=visualization,
            post_hoc_world_state=True,
            # The E1 unit is the completed grounded decision lifecycle.  This
            # is the same bounded mode used by the prior controlled integration.
            terminate_on_runtime_terminal=True,
            capture_desktop=capture_desktop,
        )
    backend = stage6b.UnifiedNativeBackend(
        wall_timeout_seconds=wall_timeout_seconds,
        no_progress_timeout_seconds=no_progress_timeout_seconds,
    )
    return backend.run(spec, output, visualization=False)


__all__ = [
    "artifact_directory",
    "execute_episode",
    "resolve_episode",
    "runtime_record",
]
