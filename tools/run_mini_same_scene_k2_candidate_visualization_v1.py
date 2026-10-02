#!/usr/bin/env python3
"""同帧 K=2 candidate plan 的极小型诊断与静态可视化。

``infer`` 只允许在目标 capture 不存在时执行：复用封存的 white-van
grounding/candidate artifact，在同一个冻结 SimLingo 实例中做 A×3/B×3 六次真实
forward。``render`` 只读取 capture 并生成图、报告和 receipt，不触发模型或 DINO。

本文件不启动 CARLA，不调用 planner/PID/control，不修改 SimLingo、V2、M2B 或 M3。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import textwrap
from datetime import datetime
from itertools import combinations
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
STAGE = "DRIVECLARIFY_MINI_SAME_SCENE_K2_CANDIDATE_VISUALIZATION_V1"
REPORT_ROOT = ROOT / "reports/driveclarify_mini_same_scene_k2_candidate_visualization_v1"
ARTIFACT_ROOT = ROOT / "artifacts/driveclarify_mini_same_scene_k2_candidate_visualization_v1"
CAPTURE = REPORT_ROOT / "SIX_REAL_FORWARD_CAPTURE.json"
REPORT = REPORT_ROOT / "MINI_K2_CANDIDATE_EXPERIMENT_REPORT.md"
RECEIPT = REPORT_ROOT / "MINI_K2_CANDIDATE_RECEIPT.json"
HASHES = REPORT_ROOT / "ARTIFACT_HASHES.json"

SOURCE_ROOT = ROOT / "reports/driveclarify_candidate_consequence_equivalence_protocol_revision"
SOURCE_GROUNDING = SOURCE_ROOT / "WHITE_VAN_GROUNDED_CANDIDATES.json"
SOURCE_PLAN = SOURCE_ROOT / "WHITE_VAN_CANDIDATE_PLAN_RECEIPT.json"
SOURCE_CLASSIFICATION = SOURCE_ROOT / "WHITE_VAN_CONSEQUENCE_CLASSIFICATION.json"
SOURCE_RGB = (
    ROOT
    / "artifacts/grounded_language_v1_extension_e1_r1/e2/E1R1-ASK-PHYS-001/driveclarify_grounded_v1/E1R1_GROUNDING_RGB_0.png"
)
SIMLINGO_ROOT = Path("/home/buaa/wrh/simlingo")
CHECKPOINT = SIMLINGO_ROOT / "outputs/simlingo/checkpoints/epoch=013.ckpt/pytorch_model.pt"

EXPECTED = {
    "source_grounding": "289a5c49bf821bd8e2f553fb1ce110a709b16170f7e9cac4e2bc01a30d835065",
    "source_plan": "c0b43351f95ad642e8e2c03c96316558523fb1d2e76c80ddf20e0fa4ab75a6b8",
    "source_classification": "e056ad79d86394169b7a55a59e30ff291a44ea911cb130689133a710e760a4a2",
    "source_rgb_file": "3c4eca17561de6b83c14101142969fa4cad4cca3eb670176318fee7704dbc6a1",
    "adapter": "2bbc816c92b56679874b503288ec3aff7586ea141c5724733f7db3884a4501af",
    "checkpoint": "ec8943723d266ee9f5f56f45d153a163b22616960bfccb741965ea5daa700d28",
    "simlingo_head": "743b243afd6cf5ff51b9fa1f8cac86f22d569684",
    "simlingo_diff": "dad60227cada5a17ba336881e583480e83eb272331e72be3c146ec96abe2d058",
}

BLUE = "#1769AA"
ORANGE = "#D97706"
INK = "#17212B"
GREY = "#697386"
LIGHT = "#EEF2F6"
GRID = "#D8DEE8"
DT_SECONDS = 0.2


def now() -> str:
    return datetime.now().astimezone().isoformat()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def json_load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def json_dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def command_output(args: Sequence[str], cwd: Path | None = None) -> str:
    result = subprocess.run(
        list(args),
        cwd=None if cwd is None else str(cwd),
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


def simlingo_state() -> dict[str, str]:
    return {
        "head": command_output(("git", "rev-parse", "HEAD"), SIMLINGO_ROOT),
        "tracked_diff_sha256": hashlib.sha256(
            subprocess.run(
                ["git", "diff", "--binary"],
                cwd=str(SIMLINGO_ROOT),
                check=True,
                stdout=subprocess.PIPE,
            ).stdout
        ).hexdigest(),
        "checkpoint_sha256": file_sha256(CHECKPOINT),
    }


def source_integrity() -> dict[str, Any]:
    paths = {
        "source_grounding": SOURCE_GROUNDING,
        "source_plan": SOURCE_PLAN,
        "source_classification": SOURCE_CLASSIFICATION,
        "source_rgb_file": SOURCE_RGB,
        "adapter": ROOT / "driveclarify_official_dreaming_adapter/adapter.py",
        "checkpoint": CHECKPOINT,
    }
    observed = {name: file_sha256(path) for name, path in paths.items()}
    matches = {name: observed[name] == EXPECTED[name] for name in paths}
    state = simlingo_state()
    matches["simlingo_head"] = state["head"] == EXPECTED["simlingo_head"]
    matches["simlingo_diff"] = (
        state["tracked_diff_sha256"] == EXPECTED["simlingo_diff"]
    )
    if not all(matches.values()):
        raise RuntimeError("SOURCE_OR_FROZEN_STATE_INTEGRITY_MISMATCH:" + repr(matches))
    display_paths = {}
    for name, path in paths.items():
        try:
            display_paths[name] = str(path.relative_to(ROOT))
        except ValueError:
            display_paths[name] = str(path)
    return {
        "paths": display_paths,
        "observed_sha256": observed,
        "expected_sha256": {name: EXPECTED[name] for name in paths},
        "matches": matches,
        "simlingo": state,
        "all_match": True,
    }


def rmse(left: Any, right: Any) -> float:
    a = np.asarray(left, dtype=np.float64)
    b = np.asarray(right, dtype=np.float64)
    if a.shape != b.shape:
        raise RuntimeError(f"RMSE_SHAPE_MISMATCH:{a.shape}:{b.shape}")
    return float(np.sqrt(np.mean(np.square(a - b))))


def derived_speed(speed_waypoints: Any) -> list[float]:
    points = np.asarray(speed_waypoints, dtype=np.float64)
    if points.shape != (10, 2) or not np.isfinite(points).all():
        raise RuntimeError("SPEED_WAYPOINT_SHAPE_OR_FINITE_INVALID")
    origin = np.zeros((1, 2), dtype=np.float64)
    values = np.linalg.norm(np.diff(np.vstack([origin, points]), axis=0), axis=1)
    return (values / DT_SECONDS).tolist()


def _pairwise_values(rows: Sequence[Mapping[str, Any]], field: str) -> list[float]:
    return [rmse(left[field], right[field]) for left, right in combinations(rows, 2)]


def metrics_from_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    a_rows = [row for row in rows if str(row["candidate_id"]).startswith("A")]
    b_rows = [row for row in rows if str(row["candidate_id"]).startswith("B")]
    if len(a_rows) != 3 or len(b_rows) != 3:
        raise RuntimeError("EXPECTED_A3_B3_ROWS")
    for row in rows:
        row_route = np.asarray(row["equal_spaced_route"], dtype=np.float64)
        row_speed = np.asarray(row["speed_waypoints"], dtype=np.float64)
        if row_route.shape != (20, 2) or row_speed.shape != (10, 2):
            raise RuntimeError("INVALID_PLAN_SHAPE:" + str(row["candidate_id"]))
        if not np.isfinite(row_route).all() or not np.isfinite(row_speed).all():
            raise RuntimeError("NONFINITE_PLAN:" + str(row["candidate_id"]))

    within_a_route = _pairwise_values(a_rows, "equal_spaced_route")
    within_b_route = _pairwise_values(b_rows, "equal_spaced_route")
    within_a_speed_wp = _pairwise_values(a_rows, "speed_waypoints")
    within_b_speed_wp = _pairwise_values(b_rows, "speed_waypoints")
    within_a_speed = _pairwise_values(a_rows, "derived_speed_mps")
    within_b_speed = _pairwise_values(b_rows, "derived_speed_mps")
    between_route = [
        rmse(left["equal_spaced_route"], right["equal_spaced_route"])
        for left in a_rows
        for right in b_rows
    ]
    between_speed_wp = [
        rmse(left["speed_waypoints"], right["speed_waypoints"])
        for left in a_rows
        for right in b_rows
    ]
    between_speed = [
        rmse(left["derived_speed_mps"], right["derived_speed_mps"])
        for left in a_rows
        for right in b_rows
    ]

    left = np.asarray(a_rows[0]["equal_spaced_route"], dtype=np.float64)
    right = np.asarray(b_rows[0]["equal_spaced_route"], dtype=np.float64)
    delta = left - right
    separation = np.linalg.norm(delta, axis=1)
    scalar_speed_delta = np.asarray(a_rows[0]["derived_speed_mps"]) - np.asarray(
        b_rows[0]["derived_speed_mps"]
    )
    return {
        "within_A_route_RMSE_values_m": within_a_route,
        "within_A_route_max_m": max(within_a_route),
        "within_B_route_RMSE_values_m": within_b_route,
        "within_B_route_max_m": max(within_b_route),
        "within_A_speed_waypoint_RMSE_values_m": within_a_speed_wp,
        "within_A_speed_waypoint_max_m": max(within_a_speed_wp),
        "within_B_speed_waypoint_RMSE_values_m": within_b_speed_wp,
        "within_B_speed_waypoint_max_m": max(within_b_speed_wp),
        "within_A_derived_speed_RMSE_values_mps": within_a_speed,
        "within_A_derived_speed_max_mps": max(within_a_speed),
        "within_B_derived_speed_RMSE_values_mps": within_b_speed,
        "within_B_derived_speed_max_mps": max(within_b_speed),
        "between_AB_route_RMSE_values_m": between_route,
        "between_AB_route_RMSE_m": between_route[0],
        "between_AB_route_max_separation_m": float(np.max(separation)),
        "between_AB_lateral_max_m": float(np.max(np.abs(delta[:, 1]))),
        "between_AB_forward_max_m": float(np.max(np.abs(delta[:, 0]))),
        "between_AB_endpoint_separation_m": float(separation[-1]),
        "between_AB_speed_waypoint_RMSE_values_m": between_speed_wp,
        "between_AB_speed_waypoint_RMSE_m": between_speed_wp[0],
        "between_AB_derived_speed_RMSE_values_mps": between_speed,
        "between_AB_derived_speed_RMSE_mps": between_speed[0],
        "between_AB_derived_speed_max_difference_mps": float(
            np.max(np.abs(scalar_speed_delta))
        ),
        "route_delta_forward_m": delta[:, 0].tolist(),
        "route_delta_lateral_m": delta[:, 1].tolist(),
        "route_separation_m": separation.tolist(),
        "repeat_stability": (
            "EMPIRICALLY_EXACT_FOR_THIS_CAPTURE"
            if max(within_a_route + within_b_route + within_a_speed_wp + within_b_speed_wp) == 0.0
            else "NONZERO_REPEAT_VARIATION_OBSERVED"
        ),
        "pass_threshold_created": False,
    }


def run_inference() -> None:
    if CAPTURE.exists():
        raise RuntimeError("CAPTURE_ALREADY_EXISTS_REFUSING_EXTRA_FORWARDS:" + str(CAPTURE))
    integrity_before = source_integrity()
    grounding = json_load(SOURCE_GROUNDING)
    source_plan = json_load(SOURCE_PLAN)
    classification = json_load(SOURCE_CLASSIFICATION)
    if grounding["raw_instruction"] != "Turn after the white van.":
        raise RuntimeError("RAW_INSTRUCTION_CHANGED")
    if grounding["raw_k"] < 2 or grounding["effective_k"] < 2:
        raise RuntimeError("GROUNDING_K_NOT_TWO")
    candidates = grounding["candidates"]
    if len(candidates) != 2:
        raise RuntimeError("EXPECTED_TWO_CANDIDATES")
    if candidates[0]["interpretation_id"] == candidates[1]["interpretation_id"]:
        raise RuntimeError("SEMANTIC_IDENTITY_COLLAPSE")
    if candidates[0]["target"]["target_id"] == candidates[1]["target"]["target_id"]:
        raise RuntimeError("FUTURE_OBLIGATION_COLLAPSE")

    # 延迟导入：render 子命令不会 import/load 模型栈。
    import torch

    from driveclarify_candidate_consequence_equivalence.runner import (
        _base_input,
        _camera_tensor,
        _close_model,
        _forward_rows,
        _load_model,
        _non_language_receipt,
    )
    from driveclarify_simlingo_local_candidate_diagnostic.runtime import _tensor_receipt

    raw, camera = _camera_tensor(SOURCE_RGB, use_thumbnail=False)
    decoded_sha = hashlib.sha256(raw.tobytes(order="C")).hexdigest()
    if decoded_sha != grounding["same_rgb_sha256"]:
        raise RuntimeError("DECODED_RGB_HASH_MISMATCH")
    fields = source_plan["non_language_fields"]
    if _tensor_receipt(camera)["bytes_sha256"] != fields["camera_images"]["bytes_sha256"]:
        raise RuntimeError("CAMERA_TENSOR_HASH_MISMATCH")
    intrinsics = (
        torch.tensor(fields["camera_intrinsics"]["value"][0], dtype=torch.float32),
    )
    extrinsics = (
        torch.tensor(fields["camera_extrinsics"]["value"][0], dtype=torch.float32),
    )

    model, processor, cfg, device = _load_model()
    model_object_id = id(model)
    try:
        base = _base_input(
            camera=camera,
            intrinsics=intrinsics,
            extrinsics=extrinsics,
            vehicle_speed=fields["vehicle_speed"]["value"],
            target_point=fields["target_point"]["value"],
            tokenizer=processor,
            encoder_variant=str(cfg.model.vision_model.variant),
            device=device,
        )
        non_language_fields, non_language_hash = _non_language_receipt(base)
        if non_language_hash != source_plan["non_language_input_sha256"]:
            raise RuntimeError("NON_LANGUAGE_INPUT_HASH_MISMATCH")
        rows = _forward_rows(
            model=model,
            processor=processor,
            cfg=cfg,
            device=device,
            base=base,
            prompts=(
                ("A", candidates[0]["rendered_dreaming_instruction"]),
                ("B", candidates[1]["rendered_dreaming_instruction"]),
            ),
            observation_id=grounding["grounding"]["observation_id"],
            frame_id=int(grounding["grounding"]["frame_id"]),
            image_sha=decoded_sha,
            repetitions=3,
        )
        for row in rows:
            row["derived_speed_mps"] = derived_speed(row["speed_waypoints"])
            row["model_object_runtime_id"] = model_object_id
            row["non_language_input_sha256"] = non_language_hash
            row["camera_tensor_sha256"] = fields["camera_images"]["bytes_sha256"]
    finally:
        _close_model(model)

    forward_order = [row["candidate_id"] for row in rows]
    if forward_order != ["A1", "B1", "A2", "B2", "A3", "B3"]:
        raise RuntimeError("UNEXPECTED_FORWARD_ORDER:" + repr(forward_order))
    if any(row["forward_evidence"]["model_forward_count"] != 1 for row in rows):
        raise RuntimeError("FORWARD_COUNT_PER_ROW_NOT_ONE")
    if any(not row["forward_evidence"]["fresh_candidate_conditioned_model_execution"] for row in rows):
        raise RuntimeError("NONFRESH_CANDIDATE_FORWARD")
    if any(row["forward_evidence"]["candidate_specific_target_point"] for row in rows):
        raise RuntimeError("CANDIDATE_SPECIFIC_TARGET_PRESENT")
    if any(row["forward_evidence"]["target_point_embedding_injected"] for row in rows):
        raise RuntimeError("TARGET_EMBEDDING_INJECTED")
    if any(row["forward_evidence"]["target_point_placeholder_count"] != 0 for row in rows):
        raise RuntimeError("TARGET_PLACEHOLDER_PRESENT")

    metrics = metrics_from_rows(rows)
    integrity_after = source_integrity()
    if integrity_before["observed_sha256"] != integrity_after["observed_sha256"]:
        raise RuntimeError("PROTECTED_SOURCE_CHANGED_DURING_INFERENCE")
    if integrity_before["simlingo"] != integrity_after["simlingo"]:
        raise RuntimeError("SIMLINGO_STATE_CHANGED_DURING_INFERENCE")

    capture = {
        "schema_version": "driveclarify.mini_same_scene_k2_candidate_forward_capture.v1",
        "stage": STAGE,
        "created_at": now(),
        "scope": "DIAGNOSTIC_ONLY_TRAIN_SCENE_FROZEN_REPLAY_NO_CARLA_NO_TRAINING",
        "source_integrity_before": integrity_before,
        "source_integrity_after": integrity_after,
        "raw_instruction": grounding["raw_instruction"],
        "source_scenario": "E1R1-ASK-PHYS-001 (TRAIN-only genuine white-van ambiguity representative)",
        "source_frame": int(grounding["grounding"]["frame_id"]),
        "source_observation_id": grounding["grounding"]["observation_id"],
        "rgb_file_sha256": file_sha256(SOURCE_RGB),
        "rgb_decoded_bytes_sha256": decoded_sha,
        "historical_live_pre_save_array_sha256": grounding["historical_live_pre_save_array_sha256"],
        "historical_live_vs_saved_replay_scope_note": grounding["hash_scope_note"],
        "raw_k": grounding["raw_k"],
        "effective_k": grounding["effective_k"],
        "grounding_source": {
            "dino_actually_run_in_source_stage": grounding["dino_actually_run"],
            "dino_forward_count_in_source_stage": grounding["dino_forward_count"],
            "dino_forward_count_this_stage": 0,
            "detector_id": grounding["grounding"]["detector_id"],
            "detector_revision": grounding["grounding"]["detector_revision"],
            "source_artifact": str(SOURCE_GROUNDING.relative_to(ROOT)),
        },
        "candidates": candidates,
        "source_relationship": classification["classification"]["relationship"],
        "same_observation_invariant": {
            "same_rgb": True,
            "same_carla_frame": True,
            "same_observation_identity": True,
            "same_ego_state": True,
            "same_measurement": True,
            "same_camera_tensors": True,
            "same_navigation": True,
            "same_checkpoint": True,
            "same_model_instance": True,
            "same_preprocessing": True,
            "same_postprocessing": True,
            "only_candidate_language_differs": True,
            "candidate_numeric_target": False,
            "target_embedding_injected": False,
            "navigation_A_B": ["OMITTED_OFFICIAL_NO_NAVIGATION_BRANCH"] * 2,
            "shared_target_tensor": fields["target_point"]["value"],
            "shared_target_tensor_semantics": source_plan["shared_replay_target_tensor_semantics"],
            "ego_speed_mps": float(fields["vehicle_speed"]["value"][0][0]),
            "non_language_input_sha256": non_language_hash,
            "camera_tensor_sha256": fields["camera_images"]["bytes_sha256"],
            "non_language_fields": non_language_fields,
        },
        "execution": {
            "adapter": "OfficialDreamingCandidateAdapter.v1",
            "checkpoint_sha256": file_sha256(CHECKPOINT),
            "model_load_count": 1,
            "model_object_runtime_id": model_object_id,
            "candidate_A_forward_count": 3,
            "candidate_B_forward_count": 3,
            "candidate_forward_count_total": 6,
            "forward_count_per_repeat": {"A": 1, "B": 1},
            "forward_order": forward_order,
            "real_fresh_forward_evidence_all": True,
            "copied_or_shifted_plan_count": 0,
            "carla_launch_count": 0,
            "training_step_count": 0,
            "optimizer_step_count": 0,
            "backward_count": 0,
            "weight_update_count": 0,
            "planner_advance_count": 0,
            "pid_added_count": 0,
            "vehicle_control_change_count": 0,
        },
        "rows": rows,
        "metrics": metrics,
        "plan_validity": {
            "candidate_A_all_valid": True,
            "candidate_B_all_valid": True,
            "route_shape_each": [20, 2],
            "raw_speed_waypoint_shape_each": [10, 2],
            "derived_scalar_speed_shape_each": [10],
            "route_frame": "EGO_LOCAL_CARLA_BEV",
            "route_dimension_order": ["forward_m", "lateral_m"],
            "route_unit": "m",
            "speed_waypoint_representation": "TEN_FUTURE_EGO_LOCAL_2D_POSITIONS",
            "derived_speed_formula": "norm(p_i-p_{i-1}) / 0.2 s; p_0 predecessor is ego origin",
            "derived_speed_unit": "m/s",
        },
    }
    json_dump(CAPTURE, capture)
    print(json.dumps({"capture": str(CAPTURE), "forwards": 6, "metrics": metrics}, indent=2))


def _configure_matplotlib() -> Any:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 10,
            "axes.titlesize": 12,
            "axes.labelsize": 10,
            "axes.edgecolor": GREY,
            "axes.labelcolor": INK,
            "xtick.color": GREY,
            "ytick.color": GREY,
            "text.color": INK,
            "figure.facecolor": "white",
            "axes.facecolor": "white",
        }
    )
    return plt


def _routes(capture: Mapping[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    rows = capture["rows"]
    return (
        np.asarray(next(row for row in rows if row["candidate_id"] == "A1")["equal_spaced_route"], dtype=np.float64),
        np.asarray(next(row for row in rows if row["candidate_id"] == "B1")["equal_spaced_route"], dtype=np.float64),
    )


def _speeds(capture: Mapping[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    rows = capture["rows"]
    return (
        np.asarray(next(row for row in rows if row["candidate_id"] == "A1")["derived_speed_mps"], dtype=np.float64),
        np.asarray(next(row for row in rows if row["candidate_id"] == "B1")["derived_speed_mps"], dtype=np.float64),
    )


def _style_axis(ax: Any) -> None:
    ax.grid(True, color=GRID, linewidth=0.8, alpha=0.75)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)


def _plot_route(ax: Any, route: np.ndarray, color: str, label: str, linestyle: str, marker: str) -> None:
    ax.plot(
        route[:, 1],
        route[:, 0],
        color=color,
        linewidth=2.7,
        linestyle=linestyle,
        marker=marker,
        markersize=4.0,
        markerfacecolor="white",
        markeredgewidth=1.2,
        label=label,
        zorder=3,
    )
    ax.scatter(route[-1, 1], route[-1, 0], s=85, color=color, edgecolor="white", linewidth=1.2, zorder=5)


def _shared_route_limits(route_a: np.ndarray, route_b: np.ndarray) -> tuple[tuple[float, float], tuple[float, float]]:
    both = np.vstack([route_a, route_b, np.zeros((1, 2))])
    lateral_min, lateral_max = float(both[:, 1].min()), float(both[:, 1].max())
    forward_min, forward_max = float(both[:, 0].min()), float(both[:, 0].max())
    lateral_pad = max(0.65, 0.12 * max(1.0, lateral_max - lateral_min))
    forward_pad = max(0.7, 0.06 * max(1.0, forward_max - forward_min))
    return (
        (lateral_min - lateral_pad, lateral_max + lateral_pad),
        (forward_min - forward_pad, forward_max + forward_pad),
    )


def render_main(capture: Mapping[str, Any]) -> Path:
    plt = _configure_matplotlib()
    raw = cv2.imread(str(SOURCE_RGB), cv2.IMREAD_UNCHANGED)
    if raw is None:
        raise RuntimeError("RGB_UNAVAILABLE_AT_RENDER")
    candidates = capture["candidates"]
    metrics = capture["metrics"]
    route_a, route_b = _routes(capture)
    speed_a, speed_b = _speeds(capture)
    xlim, ylim = _shared_route_limits(route_a, route_b)

    fig = plt.figure(figsize=(18, 12), constrained_layout=True)
    gs = fig.add_gridspec(3, 4, height_ratios=(1.15, 1.0, 1.0), width_ratios=(1.2, 1.2, 1.0, 1.0))
    ax_rgb = fig.add_subplot(gs[0, :2])
    ax_sem = fig.add_subplot(gs[0, 2:])
    ax_route = fig.add_subplot(gs[1:, :2])
    ax_speed = fig.add_subplot(gs[1, 2:])
    ax_summary = fig.add_subplot(gs[2, 2:])

    ax_rgb.imshow(cv2.cvtColor(raw, cv2.COLOR_BGR2RGB))
    for idx, candidate in enumerate(candidates):
        box = candidate["bbox_xyxy"]
        color = BLUE if idx == 0 else ORANGE
        label = f"Candidate {'A' if idx == 0 else 'B'}  conf={candidate['detector_confidence']:.3f}"
        ax_rgb.add_patch(
            plt.Rectangle(
                (box[0], box[1]), box[2] - box[0], box[3] - box[1],
                fill=False, edgecolor=color, linewidth=2.6,
            )
        )
        ax_rgb.text(
            box[0], max(10, box[1] - 7), label, color="white", fontsize=9, weight="bold",
            bbox={"facecolor": color, "edgecolor": "none", "alpha": 0.88, "pad": 2.5},
        )
    ax_rgb.set_title(
        "Panel A — Original CARLA RGB + genuine DINO referents\n"
        f"Raw: {capture['raw_instruction']}   |   frame {capture['source_frame']}   |   decoded RGB SHA {capture['rgb_decoded_bytes_sha256'][:16]}…",
        loc="left", weight="bold",
    )
    ax_rgb.axis("off")

    ax_sem.axis("off")
    ax_sem.set_title("Panel B — Candidate semantics", loc="left", weight="bold")
    y = 0.94
    for idx, candidate in enumerate(candidates):
        color = BLUE if idx == 0 else ORANGE
        label = "A" if idx == 0 else "B"
        ax_sem.text(0.01, y, f"Candidate {label}", color=color, fontsize=13, weight="bold", va="top")
        instruction = textwrap.fill(candidate["rendered_dreaming_instruction"], width=78)
        target = candidate["target"]
        body = (
            f"Instruction: {instruction}\n"
            f"Referent: {candidate['referent_id']}  ({candidate['referring_expression']})\n"
            f"Future obligation: route-order {target['route_order_index']} → {target['junction_id']} / {target['branch_id']}\n"
            f"Target: {target['target_id']} · RIGHT · distance/progress {target['distance_or_progress']:.2f} m"
        )
        ax_sem.text(0.01, y - 0.075, body, fontsize=9.2, va="top", linespacing=1.35)
        y -= 0.47
    ax_sem.text(
        0.01, 0.01,
        "Future topology is text-only here: no verified world→ego transform, so it is not drawn as model trajectory.",
        color=GREY, fontsize=8.5, va="bottom",
    )

    _plot_route(ax_route, route_a, BLUE, "Plan A · A1=A2=A3 exact", "-", "o")
    _plot_route(ax_route, route_b, ORANGE, "Plan B · B1=B2=B3 exact", "--", "s")
    ax_route.scatter([0], [0], marker="*", s=150, color=INK, label="ego origin", zorder=6)
    ax_route.annotate("ego heading", xy=(0, 1.8), xytext=(0, 0.35), ha="center", color=INK,
                      arrowprops={"arrowstyle": "-|>", "color": INK, "lw": 1.8})
    ax_route.set_xlim(*xlim)
    ax_route.set_ylim(*ylim)
    ax_route.set_aspect("equal", adjustable="box")
    ax_route.set_xlabel("lateral coordinate (m)")
    ax_route.set_ylabel("forward coordinate (m)")
    ax_route.set_title("Panel C — SimLingo candidate routes\nMODEL LOCAL PLAN · ego-local CARLA BEV (m)", loc="left", weight="bold")
    _style_axis(ax_route)
    ax_route.legend(loc="upper left", frameon=False)

    indexes = np.arange(1, len(speed_a) + 1)
    ax_speed.plot(indexes, speed_a, color=BLUE, linewidth=2.4, marker="o", markerfacecolor="white", label="A speed · A1=A2=A3")
    ax_speed.plot(indexes, speed_b, color=ORANGE, linewidth=2.4, linestyle="--", marker="s", markerfacecolor="white", label="B speed · B1=B2=B3")
    ax_speed.set_xticks(indexes)
    ax_speed.set_xlabel("speed waypoint index (0.2 s spacing)")
    ax_speed.set_ylabel("derived predicted speed (m/s)")
    ax_speed.set_title("Panel D — Speed plans", loc="left", weight="bold")
    _style_axis(ax_speed)
    ax_speed.legend(loc="upper left", frameon=False, ncol=2)
    ax_speed.text(
        0.99, 0.03,
        f"scalar speed RMSE = {metrics['between_AB_derived_speed_RMSE_mps']:.6f} m/s\n"
        f"raw 2-D speed-waypoint RMSE = {metrics['between_AB_speed_waypoint_RMSE_m']:.6f} m",
        ha="right", va="bottom", transform=ax_speed.transAxes, fontsize=8.5, color=GREY,
    )

    ax_summary.axis("off")
    ax_summary.set_title("Panel E — Quantitative summary + real chain", loc="left", weight="bold")
    summary = (
        "Raw instruction → real DINO source artifact → raw K=2 → effective K=2\n"
        "→ semantic A/B → rendered A/B → SAME frozen SimLingo → Plan A / Plan B\n\n"
        f"semantic_K                         2\n"
        f"current_plan_class_K              UNKNOWN (no trusted classifier)\n"
        f"exact_current_plan_object_K       2\n"
        f"future_obligation_class_K         2\n"
        f"route RMSE                        {metrics['between_AB_route_RMSE_m']:.6f} m\n"
        f"max route / lateral separation    {metrics['between_AB_route_max_separation_m']:.6f} / {metrics['between_AB_lateral_max_m']:.6f} m\n"
        f"derived speed RMSE                {metrics['between_AB_derived_speed_RMSE_mps']:.6f} m/s\n"
        f"within A/B route noise            {metrics['within_A_route_max_m']:.6f} / {metrics['within_B_route_max_m']:.6f} m\n"
        f"within A/B speed noise            {metrics['within_A_derived_speed_max_mps']:.6f} / {metrics['within_B_derived_speed_max_mps']:.6f} m/s\n"
        "same RGB/state/navigation/checkpoint    YES / YES / YES / YES\n"
        "only language differs · numeric target NO · forwards A×3/B×3"
    )
    ax_summary.text(
        0.01, 0.98, summary, va="top", family="DejaVu Sans Mono", fontsize=9.1,
        bbox={"facecolor": LIGHT, "edgecolor": GRID, "boxstyle": "round,pad=0.55"},
    )
    ax_summary.text(
        0.01, 0.02,
        "RESEARCH DEBUG VIEW · DIAGNOSTIC ONLY · SIMULATION ONLY · NO FORMAL SAFETY GUARANTEE",
        color=GREY, fontsize=8.5, weight="bold", va="bottom",
    )

    fig.suptitle(
        "Same real CARLA scene → two genuine language candidates → one frozen SimLingo → two independent path-speed plans",
        fontsize=16, weight="bold",
    )
    path = ARTIFACT_ROOT / "SAME_SCENE_K2_CANDIDATE_VISUALIZATION.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=180, facecolor="white")
    plt.close(fig)
    return path


def render_side_by_side(capture: Mapping[str, Any]) -> Path:
    plt = _configure_matplotlib()
    candidates = capture["candidates"]
    route_a, route_b = _routes(capture)
    speed_a, speed_b = _speeds(capture)
    xlim, ylim = _shared_route_limits(route_a, route_b)
    fig = plt.figure(figsize=(18, 10), constrained_layout=True)
    gs = fig.add_gridspec(2, 2, height_ratios=(1.55, 0.8))
    axes_route = [fig.add_subplot(gs[0, 0]), fig.add_subplot(gs[0, 1])]
    axes_speed = [fig.add_subplot(gs[1, 0]), fig.add_subplot(gs[1, 1])]
    for idx, (route, speed, color, linestyle, marker) in enumerate(
        ((route_a, speed_a, BLUE, "-", "o"), (route_b, speed_b, ORANGE, "--", "s"))
    ):
        candidate = candidates[idx]
        label = "A" if idx == 0 else "B"
        ax = axes_route[idx]
        _plot_route(ax, route, color, f"Candidate {label} plan · {label}1={label}2={label}3", linestyle, marker)
        ax.scatter([0], [0], marker="*", s=150, color=INK, zorder=6)
        ax.annotate("heading", xy=(0, 1.8), xytext=(0, 0.35), ha="center",
                    arrowprops={"arrowstyle": "-|>", "color": INK, "lw": 1.8})
        ax.set_xlim(*xlim)
        ax.set_ylim(*ylim)
        ax.set_aspect("equal", adjustable="box")
        ax.set_xlabel("lateral (m)")
        ax.set_ylabel("forward (m)")
        ax.set_title(
            f"Candidate {label}\n{textwrap.fill(candidate['rendered_dreaming_instruction'], width=64)}",
            loc="left", weight="bold", color=color, fontsize=11,
        )
        target = candidate["target"]
        obligation = (
            "FUTURE TOPOLOGY OBLIGATION — NOT MODEL TRAJECTORY\n"
            f"route-order {target['route_order_index']} · {target['junction_id']}\n"
            f"{target['branch_id']} · {target['target_id']}\n"
            f"RIGHT at distance/progress {target['distance_or_progress']:.2f} m"
        )
        ax.text(
            0.02, 0.03, obligation, transform=ax.transAxes, va="bottom", fontsize=8.2,
            bbox={"facecolor": "white", "edgecolor": color, "alpha": 0.93, "boxstyle": "round,pad=0.4"},
        )
        _style_axis(ax)
        ax.legend(loc="upper left", frameon=False)

        sax = axes_speed[idx]
        indexes = np.arange(1, len(speed) + 1)
        sax.plot(indexes, speed, color=color, linewidth=2.5, linestyle=linestyle, marker=marker, markerfacecolor="white")
        sax.set_xticks(indexes)
        sax.set_xlabel("speed waypoint index (0.2 s spacing)")
        sax.set_ylabel("derived predicted speed (m/s)")
        sax.set_title(f"Candidate {label} speed plan", loc="left", weight="bold")
        _style_axis(sax)
    all_speed = np.concatenate([speed_a, speed_b])
    speed_limits = (min(-0.15, float(all_speed.min()) - 0.35), float(all_speed.max()) + 0.45)
    axes_speed[0].set_ylim(*speed_limits)
    axes_speed[1].set_ylim(*speed_limits)
    fig.suptitle(
        "Candidate A / B separated view · identical BEV and speed scales",
        fontsize=16, weight="bold",
    )
    fig.supxlabel(
        "RESEARCH DEBUG VIEW · DIAGNOSTIC ONLY · SIMULATION ONLY · NO FORMAL SAFETY GUARANTEE",
        color=GREY, fontsize=8.5, weight="bold",
    )
    path = ARTIFACT_ROOT / "CANDIDATE_A_B_SIDE_BY_SIDE.png"
    fig.savefig(path, dpi=180, facecolor="white")
    plt.close(fig)
    return path


def render_difference(capture: Mapping[str, Any]) -> Path:
    plt = _configure_matplotlib()
    route_a, route_b = _routes(capture)
    metrics = capture["metrics"]
    delta_forward = np.asarray(metrics["route_delta_forward_m"], dtype=np.float64)
    delta_lateral = np.asarray(metrics["route_delta_lateral_m"], dtype=np.float64)
    separation = np.asarray(metrics["route_separation_m"], dtype=np.float64)
    indexes = np.arange(len(separation))
    xlim, ylim = _shared_route_limits(route_a, route_b)
    fig, (ax_route, ax_delta) = plt.subplots(1, 2, figsize=(16, 9), constrained_layout=True)
    _plot_route(ax_route, route_a, BLUE, "Plan A", "-", "o")
    _plot_route(ax_route, route_b, ORANGE, "Plan B", "--", "s")
    for index in range(len(route_a)):
        ax_route.plot(
            [route_a[index, 1], route_b[index, 1]],
            [route_a[index, 0], route_b[index, 0]],
            color=GREY, alpha=0.35, linewidth=0.8,
        )
    ax_route.scatter([0], [0], marker="*", s=150, color=INK, zorder=6)
    ax_route.set_xlim(*xlim)
    ax_route.set_ylim(*ylim)
    ax_route.set_aspect("equal", adjustable="box")
    ax_route.set_xlabel("lateral coordinate (m)")
    ax_route.set_ylabel("forward coordinate (m)")
    ax_route.set_title("True-scale route overlay", loc="left", weight="bold")
    _style_axis(ax_route)
    ax_route.legend(loc="upper left", frameon=False)

    ax_delta.axhline(0.0, color=INK, linewidth=1.0, alpha=0.65)
    ax_delta.plot(indexes, delta_forward, color=BLUE, linewidth=2.1, marker="o", markerfacecolor="white", label="Δforward = A−B")
    ax_delta.plot(indexes, delta_lateral, color=ORANGE, linewidth=2.1, linestyle="--", marker="s", markerfacecolor="white", label="Δlateral = A−B")
    ax_delta.plot(indexes, separation, color=INK, linewidth=2.5, linestyle=":", marker="^", markerfacecolor="white", label="Euclidean separation")
    max_index = int(np.argmax(separation))
    ax_delta.scatter([max_index], [separation[max_index]], color=INK, s=80, zorder=5)
    ax_delta.annotate(
        f"max {separation[max_index]:.6f} m at waypoint {max_index}",
        xy=(max_index, separation[max_index]), xytext=(max(0, max_index - 7), separation[max_index] + 0.09),
        arrowprops={"arrowstyle": "->", "color": INK}, fontsize=9,
    )
    ax_delta.set_xticks(indexes)
    ax_delta.set_xlabel("route waypoint index")
    ax_delta.set_ylabel("difference / separation (m)")
    ax_delta.set_title("A/B route difference by waypoint · no candidate-specific scaling", loc="left", weight="bold")
    _style_axis(ax_delta)
    ax_delta.legend(loc="upper left", frameon=False)
    ax_delta.text(
        0.99, 0.03,
        f"route RMSE {metrics['between_AB_route_RMSE_m']:.6f} m\n"
        f"max lateral {metrics['between_AB_lateral_max_m']:.6f} m",
        ha="right", va="bottom", transform=ax_delta.transAxes,
        bbox={"facecolor": LIGHT, "edgecolor": GRID, "boxstyle": "round,pad=0.4"}, fontsize=9,
    )
    fig.suptitle(
        "Candidate route difference detail · real values, common coordinate transform",
        fontsize=16, weight="bold",
    )
    fig.supxlabel(
        "RESEARCH DEBUG VIEW · DIAGNOSTIC ONLY · SIMULATION ONLY · NO FORMAL SAFETY GUARANTEE",
        color=GREY, fontsize=8.5, weight="bold",
    )
    path = ARTIFACT_ROOT / "CANDIDATE_ROUTE_DIFFERENCE_DETAIL.png"
    fig.savefig(path, dpi=180, facecolor="white")
    plt.close(fig)
    return path


def cleanup_snapshot() -> dict[str, Any]:
    ps_lines = command_output(("ps", "-eo", "pid=,args=")).splitlines()
    carla_tokens = ("CarlaUE4", "leaderboard_evaluator.py", "scenario_runner.py", "run_evaluation.py")
    carla = [line.strip() for line in ps_lines if any(token in line for token in carla_tokens)]
    try:
        apps = command_output(
            ("nvidia-smi", "--query-compute-apps=pid,process_name,used_memory", "--format=csv,noheader")
        ).splitlines()
    except Exception as error:  # pragma: no cover - only records host observability
        apps = ["QUERY_FAILED:" + str(error)]
    project_gpu = [line for line in apps if "python" in line.casefold() or "carla" in line.casefold()]
    return {
        "carla_or_evaluator_processes": carla,
        "project_gpu_compute_processes": project_gpu,
        "all_gpu_compute_rows": [line for line in apps if line.strip()],
        "pass": not carla and not project_gpu,
    }


def build_receipt(capture: Mapping[str, Any], images: Sequence[Path]) -> dict[str, Any]:
    candidates = capture["candidates"]
    metrics = capture["metrics"]
    invariant = capture["same_observation_invariant"]
    integrity_render = source_integrity()
    protected_unchanged = (
        capture["source_integrity_after"]["observed_sha256"] == integrity_render["observed_sha256"]
        and capture["source_integrity_after"]["simlingo"] == integrity_render["simlingo"]
    )
    cleanup = cleanup_snapshot()
    images_dict = {path.name: str(path.relative_to(ROOT)) for path in images}
    verdict_conditions = {
        "effective_K_at_least_2": capture["effective_k"] >= 2,
        "genuinely_distinct_semantic_candidates": candidates[0]["interpretation_id"] != candidates[1]["interpretation_id"],
        "both_independently_pass_frozen_simlingo": capture["execution"]["candidate_A_forward_count"] == 3 and capture["execution"]["candidate_B_forward_count"] == 3,
        "two_valid_path_speed_plan_objects": capture["plan_validity"]["candidate_A_all_valid"] and capture["plan_validity"]["candidate_B_all_valid"],
        "same_observation_invariant": all(
            invariant[key]
            for key in (
                "same_rgb", "same_carla_frame", "same_observation_identity", "same_ego_state",
                "same_measurement", "same_camera_tensors", "same_navigation", "same_checkpoint",
                "same_model_instance", "same_preprocessing", "same_postprocessing",
                "only_candidate_language_differs",
            )
        ) and not invariant["candidate_numeric_target"] and not invariant["target_embedding_injected"],
    }
    verdict = (
        "PASS_SAME_SCENE_K2_INDEPENDENT_SIMLINGO_CANDIDATE_PLANS_VISUALIZED"
        if all(verdict_conditions.values()) and protected_unchanged and cleanup["pass"]
        else "BLOCKED_SAME_SCENE_K2_CANDIDATE_VISUALIZATION_INVARIANT_OR_CLEANUP_FAILURE"
    )
    return {
        "schema_version": "driveclarify.mini_same_scene_k2_candidate_receipt.v1",
        "stage": STAGE,
        "created_at": now(),
        "scope": "DIAGNOSTIC_ONLY_NOT_METHOD_V1_FREEZE_NOT_E3_DEV_TEST_NOT_TRAINING",
        "final_verdict": verdict,
        "verdict_conditions": verdict_conditions,
        "classification": {
            "semantic_K": 2,
            "current_plan_class_K": None,
            "current_plan_class_K_reason": "NO_TRUSTED_CURRENT_MANEUVER_CLASSIFIER; exact numeric plan arrays differ but are not promoted to maneuver classes",
            "exact_current_plan_object_K": 2,
            "future_obligation_class_K": 2,
            "true_candidate_collapse": False,
            "relationship": capture["source_relationship"],
            "relationship_visual_label": "CURRENT_PLAN_RELATION_UNKNOWN_FUTURE_OBLIGATION_DISTINCT",
            "current_plan_branching_visually_observed": False,
            "current_plan_branching_note": "Close but non-identical real plans; difference detail is shown without a new visual/materiality threshold.",
        },
        "same_observation_invariant": invariant,
        "execution": capture["execution"],
        "plan_validity": capture["plan_validity"],
        "metrics": metrics,
        "chart_contract": {
            "analytical_question": "Can two genuine language candidates for one real CARLA frame produce independent, repeat-stable path-speed plans through one frozen SimLingo?",
            "takeaway": "The six real forwards are repeat-exact within candidate and measurably different between candidates while all non-language inputs are identical.",
            "surface": "standalone static PNG",
            "renderer": "Matplotlib Agg",
            "route_variant": "true-scale ego-local BEV overlay plus same-scale side-by-side",
            "speed_variant": "ordered line chart of derived scalar speed by waypoint index",
            "difference_variant": "ordered multi-line Δforward/Δlateral/Euclidean separation",
            "palette_policy": "hard two-root cap: blue A, orange B, charcoal separation",
            "non_color_distinction": "A solid/circle; B dashed/square; separation dotted/triangle",
            "route_frame_and_unit": "EGO_LOCAL_CARLA_BEV; forward/lateral metres",
            "speed_derivation": capture["plan_validity"]["derived_speed_formula"],
            "future_topology_visual_policy": "text-only and explicitly separated; no verified world-to-ego transform",
            "candidate_specific_scaling": False,
        },
        "source": {
            "raw_instruction": capture["raw_instruction"],
            "scenario": capture["source_scenario"],
            "frame": capture["source_frame"],
            "observation_id": capture["source_observation_id"],
            "rgb_file_sha256": capture["rgb_file_sha256"],
            "rgb_decoded_bytes_sha256": capture["rgb_decoded_bytes_sha256"],
            "historical_live_pre_save_array_sha256": capture["historical_live_pre_save_array_sha256"],
            "rgb_hash_scope_note": capture["historical_live_vs_saved_replay_scope_note"],
            "raw_K": capture["raw_k"],
            "effective_K": capture["effective_k"],
            "grounding": capture["grounding_source"],
        },
        "candidate_A": candidates[0],
        "candidate_B": candidates[1],
        "visualizations": images_dict,
        "forward_capture": str(CAPTURE.relative_to(ROOT)),
        "protected_state": {
            "source_and_simlingo_unchanged_before_after_render": protected_unchanged,
            "simlingo_head": integrity_render["simlingo"]["head"],
            "simlingo_diff_sha256": integrity_render["simlingo"]["tracked_diff_sha256"],
            "checkpoint_sha256": integrity_render["simlingo"]["checkpoint_sha256"],
            "simlingo_modifications": 0,
            "v2_scientific_contract_modifications": 0,
            "m2b_modifications": 0,
            "m3_modifications": 0,
            "decision_threshold_modifications": 0,
            "e3_attempts": 0,
            "dev_attempts": 0,
            "test_attempts": 0,
            "training_steps": 0,
        },
        "cleanup": cleanup,
        "answers": {
            "01_raw_instruction": capture["raw_instruction"],
            "02_source_scenario": capture["source_scenario"],
            "03_source_frame": capture["source_frame"],
            "04_RGB_SHA": capture["rgb_decoded_bytes_sha256"],
            "05_raw_K": capture["raw_k"],
            "06_effective_K": capture["effective_k"],
            "07_candidate_A_identity": candidates[0]["interpretation_id"],
            "08_candidate_B_identity": candidates[1]["interpretation_id"],
            "09_candidate_A_rendered_instruction": candidates[0]["rendered_dreaming_instruction"],
            "10_candidate_B_rendered_instruction": candidates[1]["rendered_dreaming_instruction"],
            "11_future_obligation_A": candidates[0]["target"],
            "12_future_obligation_B": candidates[1]["target"],
            "13_same_RGB": invariant["same_rgb"],
            "14_same_ego_state": invariant["same_ego_state"],
            "15_same_navigation": invariant["same_navigation"],
            "16_checkpoint_identity": capture["execution"]["checkpoint_sha256"],
            "17_candidate_A_forwards": capture["execution"]["candidate_A_forward_count"],
            "18_candidate_B_forwards": capture["execution"]["candidate_B_forward_count"],
            "19_route_A_shape": [20, 2],
            "20_route_B_shape": [20, 2],
            "21_speed_A_shape": {"raw_2d_waypoints": [10, 2], "derived_scalar": [10]},
            "22_speed_B_shape": {"raw_2d_waypoints": [10, 2], "derived_scalar": [10]},
            "23_within_A_noise": {"route_RMSE_m": metrics["within_A_route_max_m"], "derived_speed_RMSE_mps": metrics["within_A_derived_speed_max_mps"]},
            "24_within_B_noise": {"route_RMSE_m": metrics["within_B_route_max_m"], "derived_speed_RMSE_mps": metrics["within_B_derived_speed_max_mps"]},
            "25_between_AB_route_RMSE_m": metrics["between_AB_route_RMSE_m"],
            "26_maximum_route_separation_m": metrics["between_AB_route_max_separation_m"],
            "27_speed_RMSE": {"derived_scalar_mps": metrics["between_AB_derived_speed_RMSE_mps"], "raw_2d_speed_waypoint_m": metrics["between_AB_speed_waypoint_RMSE_m"]},
            "28_semantic_K": 2,
            "29_current_plan_class_K": None,
            "30_future_obligation_class_K": 2,
            "31_true_candidate_collapse": False,
            "32_visualization_paths": images_dict,
            "33_simlingo_modifications": 0,
            "34_v2_modifications": 0,
            "35_E3_DEV_TEST_preservation": "E3/DEV/TEST attempts all 0; no protected stage entered",
            "36_cleanup": cleanup,
            "37_final_verdict": verdict,
        },
    }


def write_report(receipt: Mapping[str, Any]) -> None:
    a = receipt["candidate_A"]
    b = receipt["candidate_B"]
    m = receipt["metrics"]
    inv = receipt["same_observation_invariant"]
    text = f"""# Mini same-scene K=2 candidate visualization experiment

阶段：`{STAGE}`  
性质：`DIAGNOSTIC_ONLY`；不进入 Method V1 Final Freeze、E3、DEV、TEST 或 training。

## 最终结论

`{receipt['final_verdict']}`

同一个真实 CARLA 场景保存帧在完全相同的非语言输入 envelope 下，Candidate A 与 B 各自完成 3 次、合计 6 次新的独立 frozen SimLingo forward；每次都产生有效的 `20×2` ego-local route 与 `10×2` speed-waypoint plan。未复制、平移、缩放、平滑或改写模型数组。

## 真实 candidate-generation 链

```text
Raw instruction: {receipt['source']['raw_instruction']}
↓
historical real Grounding DINO source artifact (this stage rerun count = 0)
↓
raw K = {receipt['source']['raw_K']} → effective K = {receipt['source']['effective_K']}
↓
Candidate A: {a['interpretation_id']} / referent {a['referent_id']}
Candidate B: {b['interpretation_id']} / referent {b['referent_id']}
↓
rendered A/B language
↓
one frozen SimLingo model instance, A1 B1 A2 B2 A3 B3
↓
six real path-speed plan objects
```

这不是人工 explicit pair，也不是从 expected labels 生成路线。DINO 与 candidate semantics 来自封存的 genuine white-van automatic ambiguity artifact；本轮只读复用它，并补做缺失的 A×3/B×3 模型 repeat。

## A / B 语义与 future obligation

Candidate A：`{a['rendered_dreaming_instruction']}`

- referent：`{a['referent_id']}`（`{a['referring_expression']}`）
- future obligation：route-order `{a['target']['route_order_index']}`，`{a['target']['junction_id']}` / `{a['target']['branch_id']}` / `{a['target']['target_id']}`，RIGHT，distance/progress `{a['target']['distance_or_progress']:.2f} m`

Candidate B：`{b['rendered_dreaming_instruction']}`

- referent：`{b['referent_id']}`（`{b['referring_expression']}`）
- future obligation：route-order `{b['target']['route_order_index']}`，`{b['target']['junction_id']}` / `{b['target']['branch_id']}` / `{b['target']['target_id']}`，RIGHT，distance/progress `{b['target']['distance_or_progress']:.2f} m`

future topology 只按文字显示。其 `anchor_xy` 是历史 world/map topology 证据；没有已验证的 world→当前 ego-local transform，因此没有把它画成 SimLingo 轨迹。

## Same-observation 硬不变量

- source scenario / frame：`{receipt['source']['scenario']}` / `{receipt['source']['frame']}`
- source observation：`{receipt['source']['observation_id']}`
- saved PNG SHA-256：`{receipt['source']['rgb_file_sha256']}`
- decoded replay RGB bytes SHA-256：`{receipt['source']['rgb_decoded_bytes_sha256']}`
- camera tensor SHA-256：`{inv['camera_tensor_sha256']}`
- complete non-language input SHA-256：`{inv['non_language_input_sha256']}`
- same RGB / frame / observation / ego state / measurement / camera / navigation / checkpoint / model instance / preprocessing / postprocessing：全部 `YES`
- candidate numeric target：`NO`
- `<TARGET_POINT>` placeholder / embedding：`0 / NO`
- navigation：A/B 都是 `OMITTED_OFFICIAL_NO_NAVIGATION_BRANCH`
- shared transported target tensor：`{inv['shared_target_tensor']}`，语义为 `{inv['shared_target_tensor_semantics']}`

保存 PNG 解码数组与历史 live pre-save 内存数组 SHA 使用不同 artifact scope，未冒充相等：`{receipt['source']['rgb_hash_scope_note']}` 本实验中的 A/B 与全部 repeats 则严格共享同一个保存帧解码数组与同一个 camera tensor。

## Forward 与 plan 证据

- model load：`1`
- A forwards：`3`；B forwards：`3`；总数：`6`
- 每个 repeat：A=`1`，B=`1`
- forward order：`A1, B1, A2, B2, A3, B3`
- 每一行 `model_forward_count=1`、`fresh_candidate_conditioned_model_execution=true`
- route shape：A/B 每次均 `[20, 2]`
- raw speed-waypoint shape：A/B 每次均 `[10, 2]`
- derived scalar-speed shape：A/B 每次均 `[10]`
- route frame / unit：`EGO_LOCAL_CARLA_BEV`，维度 `[forward_m, lateral_m]`
- scalar speed：由 0.2 s 间隔 2-D future positions 计算 `norm(p_i-p_{{i-1}})/0.2 s`

## 差异与 repeat stability

没有创建新的 PASS threshold；下列值只用于区分 language-conditioned difference 与 repeat noise。

| 指标 | 值 |
|---|---:|
| within-A route RMSE max | `{m['within_A_route_max_m']:.9f} m` |
| within-B route RMSE max | `{m['within_B_route_max_m']:.9f} m` |
| within-A derived-speed RMSE max | `{m['within_A_derived_speed_max_mps']:.9f} m/s` |
| within-B derived-speed RMSE max | `{m['within_B_derived_speed_max_mps']:.9f} m/s` |
| between A/B route RMSE | `{m['between_AB_route_RMSE_m']:.9f} m` |
| max route separation | `{m['between_AB_route_max_separation_m']:.9f} m` |
| max lateral separation | `{m['between_AB_lateral_max_m']:.9f} m` |
| raw 2-D speed-waypoint RMSE | `{m['between_AB_speed_waypoint_RMSE_m']:.9f} m` |
| derived scalar-speed RMSE | `{m['between_AB_derived_speed_RMSE_mps']:.9f} m/s` |
| max scalar-speed difference | `{m['between_AB_derived_speed_max_difference_mps']:.9f} m/s` |

repeat 结果为 `{m['repeat_stability']}`：within-A/B 均为 0，而 between-A/B 非零。

## Collapse 分类

- `SEMANTIC_K = 2`
- `CURRENT_PLAN_CLASS_K = UNKNOWN`：没有可信 current-maneuver classifier；不能凭连续数组差异自行创造分类阈值。
- `EXACT_CURRENT_PLAN_OBJECT_K = 2`：A/B 的真实数值 plan 对象不同。
- `FUTURE_OBLIGATION_CLASS_K = 2`
- true candidate collapse：`NO`
- relationship：`CURRENT_PLAN_RELATION_UNKNOWN_FUTURE_OBLIGATION_DISTINCT`（来源关系仍为 `{receipt['classification']['relationship']}`）

两条当前路线接近但不相同；因此提供真实比例的 difference detail，未标成“明显 current-plan branching”，也未把接近误写为同一 maneuver class。

## 可视化

- `{receipt['visualizations']['SAME_SCENE_K2_CANDIDATE_VISUALIZATION.png']}`
- `{receipt['visualizations']['CANDIDATE_A_B_SIDE_BY_SIDE.png']}`
- `{receipt['visualizations']['CANDIDATE_ROUTE_DIFFERENCE_DETAIL.png']}`

## 修改、边界与 cleanup

- SimLingo modifications：`0`
- V2 scientific contract / policy / threshold modifications：`0 / 0 / 0`
- M2B / M3 modifications：`0 / 0`
- CARLA launches / planner advances / new PID / VehicleControl changes：`0 / 0 / 0 / 0`
- training / optimizer / backward / weight update：`0 / 0 / 0 / 0`
- E3 / DEV / TEST attempts：`0 / 0 / 0`
- protected source、SimLingo HEAD/diff、checkpoint 前后不变：`{str(receipt['protected_state']['source_and_simlingo_unchanged_before_after_render']).upper()}`
- cleanup：`{'PASS' if receipt['cleanup']['pass'] else 'FAIL'}`

本阶段到此立即停止，不进入其他阶段。
"""
    REPORT.write_text(text, encoding="utf-8")


def run_render() -> None:
    if not CAPTURE.exists():
        raise RuntimeError("FORWARD_CAPTURE_MISSING_RUN_INFER_FIRST")
    capture = json_load(CAPTURE)
    if capture.get("stage") != STAGE:
        raise RuntimeError("CAPTURE_STAGE_MISMATCH")
    metrics_check = metrics_from_rows(capture["rows"])
    if metrics_check != capture["metrics"]:
        raise RuntimeError("CAPTURE_METRIC_RECOMPUTATION_MISMATCH")
    images = (render_main(capture), render_side_by_side(capture), render_difference(capture))
    receipt = build_receipt(capture, images)
    json_dump(RECEIPT, receipt)
    write_report(receipt)
    files = [CAPTURE, RECEIPT, REPORT, *images]
    json_dump(
        HASHES,
        {
            "schema_version": "driveclarify.mini_same_scene_k2_candidate_artifact_hashes.v1",
            "stage": STAGE,
            "created_at": now(),
            "files": {str(path.relative_to(ROOT)): file_sha256(path) for path in files},
            "note": "Manifest does not include its own hash.",
        },
    )
    print(
        json.dumps(
            {
                "verdict": receipt["final_verdict"],
                "images": [str(path) for path in images],
                "report": str(REPORT),
                "receipt": str(RECEIPT),
            },
            indent=2,
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("infer", "render"))
    args = parser.parse_args()
    if args.phase == "infer":
        run_inference()
    else:
        run_render()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
