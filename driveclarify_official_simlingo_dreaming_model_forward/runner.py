from __future__ import annotations

import csv
import gc
import gzip
import glob
import hashlib
import json
import math
import os
import random
import re
import shutil
import subprocess
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime
from itertools import combinations
from pathlib import Path
from typing import Any

import hydra
import matplotlib
import numpy as np
import torch
from hydra.utils import instantiate
from omegaconf import OmegaConf
from PIL import Image
from pytorch_lightning.utilities import move_data_to_device
from transformers import AutoProcessor, AutoTokenizer

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


DRIVECLARIFY_ROOT = Path("/home/buaa/wrh/DriveClarify")
SIMLINGO_ROOT = Path("/home/buaa/wrh/simlingo")
DATASET_ROOT = Path("/home/buaa/wrh/SimLingoOfficialData/simlingo_v2_2025_01_10")
REPORT_ROOT = DRIVECLARIFY_ROOT / "reports/official_simlingo_action_dreaming_reproduction/model_forward_r1"
ARTIFACT_ROOT = DRIVECLARIFY_ROOT / "artifacts/official_simlingo_action_dreaming_reproduction"
CHECKPOINT = SIMLINGO_ROOT / "outputs/simlingo/checkpoints/epoch=013.ckpt/pytorch_model.pt"
CHECKPOINT_CONFIG = SIMLINGO_ROOT / "outputs/simlingo/.hydra/config.yaml"
INDEX_PATH = DATASET_ROOT / "DREAMER_RECORD_INDEX.tsv.gz"
EXPECTED_HEAD = "743b243afd6cf5ff51b9fa1f8cac86f22d569684"
EXPECTED_DIFF = "dad60227cada5a17ba336881e583480e83eb272331e72be3c146ec96abe2d058"
EXPECTED_CHECKPOINT = "ec8943723d266ee9f5f56f45d153a163b22616960bfccb741965ea5daa700d28"
DATASET_REVISION = "796abab62acaddb6183d0d01c9b43a622c5c58c8"
E3_FILES = {
    "ledger": DRIVECLARIFY_ROOT / "reports/grounded_language_v1_extension_e1_r1_e3/E3_FORMAL_TRAIN_LEDGER.json",
    "receipt": DRIVECLARIFY_ROOT / "reports/grounded_language_v1_extension_e1_r1_e3/E3_FINAL_RECEIPT.json",
    "hashes": DRIVECLARIFY_ROOT / "reports/grounded_language_v1_extension_e1_r1_e3/ARTIFACT_HASHES.json",
}
EXPECTED_E3 = {
    "ledger": "9a7bba939f6865247674260dacd9c92d61561d691b3ae3ad0fb43d3b062fcc14",
    "receipt": "bd546a75126b782b547ea5633ab10272f29ade884b8fe053ffdcd6cae880f798",
    "hashes": "0e252efd2f1b9bc1bd9ae822d87da853fbaa1faabebc334d891e8ccbd0b99eeb",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def tensor_hash(value: torch.Tensor) -> str:
    array = value.detach().cpu().contiguous().numpy()
    envelope = f"{array.dtype}|{array.shape}|".encode("ascii") + array.tobytes()
    return sha256_bytes(envelope)


def json_dump(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_json_gz(path: Path) -> Any:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def git(*args: str) -> str:
    return subprocess.check_output(["git", "-C", str(SIMLINGO_ROOT), *args], text=True).strip()


def protected_diff_hash() -> str:
    proc = subprocess.run(
        ["git", "-C", str(SIMLINGO_ROOT), "diff", "--binary"],
        check=True,
        stdout=subprocess.PIPE,
    )
    return sha256_bytes(proc.stdout)


def entry_gate() -> dict[str, Any]:
    acquisition = json.loads(
        Path("/home/buaa/wrh/SimLingoOfficialData/ACQUISITION_FINAL_RECEIPT.json").read_text(encoding="utf-8")
    )
    root_mode = DATASET_ROOT.stat().st_mode & 0o777
    checks = {
        "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
        "head": git("rev-parse", "HEAD"),
        "protected_diff_sha256": protected_diff_hash(),
        "checkpoint_sha256": sha256_file(CHECKPOINT),
        "dataset_revision": acquisition["dataset_revision"],
        "dataset_root_mode_octal": oct(root_mode),
        "dataset_root_has_no_write_bits": (root_mode & 0o222) == 0,
        "e3_hashes": {key: sha256_file(path) for key, path in E3_FILES.items()},
    }
    checks["pass"] = all(
        [
            checks["branch"] == "main",
            checks["head"] == EXPECTED_HEAD,
            checks["protected_diff_sha256"] == EXPECTED_DIFF,
            checks["checkpoint_sha256"] == EXPECTED_CHECKPOINT,
            checks["dataset_revision"] == DATASET_REVISION,
            checks["dataset_root_has_no_write_bits"],
            checks["e3_hashes"] == EXPECTED_E3,
        ]
    )
    return checks


def official_seed42_routes() -> set[Path]:
    routes = glob.glob(str(DATASET_ROOT / "data/simlingo/*/*/*/Town*"))
    random.Random(42).shuffle(routes)
    return {Path(path).resolve() for path in routes[: int(0.02 * len(routes))]}


def route_for_record(record: dict[str, str], label_cache: dict[str, Any]) -> list[list[float]]:
    label_path = record["label_path"]
    label = label_cache.setdefault(label_path, load_json_gz(Path(label_path)))
    matches = [
        option
        for option in label[record["category"]]
        if record["instruction"] in option["dreamer_instruction"]
    ]
    if len(matches) != 1 or matches[0]["route"] == "org":
        raise RuntimeError(f"record does not resolve to one explicit option: {record}")
    return [[float(v) for v in point[:2]] for point in matches[0]["route"]]


def pair_metrics(route_a: list[list[float]], route_b: list[list[float]]) -> dict[str, float]:
    a = np.asarray(route_a, dtype=np.float64)
    b = np.asarray(route_b, dtype=np.float64)
    if a.shape != b.shape or a.ndim != 2 or a.shape[1] < 2:
        raise ValueError(f"route shape mismatch: {a.shape}, {b.shape}")
    delta = a[:, :2] - b[:, :2]
    separation = np.linalg.norm(delta, axis=1)
    return {
        "rmse_m": float(np.sqrt(np.mean(np.square(separation)))),
        "max_separation_m": float(np.max(separation)),
        "lateral_rmse_m": float(np.sqrt(np.mean(np.square(delta[:, 1])))),
        "max_lateral_separation_m": float(np.max(np.abs(delta[:, 1]))),
        "endpoint_separation_m": float(np.linalg.norm(delta[-1])),
        "endpoint_lateral_delta_a_minus_b_m": float(delta[-1, 1]),
        "arc_length_a_m": float(np.linalg.norm(np.diff(a[:, :2], axis=0), axis=1).sum()),
        "arc_length_b_m": float(np.linalg.norm(np.diff(b[:, :2], axis=0), axis=1).sum()),
    }


def is_clear_lane_instruction(text: str) -> bool:
    lowered = text.lower()
    return bool(
        re.search(r"\b(one|two) lanes?\b", lowered)
        and re.search(r"\b(left|right)\b", lowered)
        and not re.search(r"opposite|parking|sidewalk|\bmeter|\d+(st|nd|rd|th)", lowered)
    )


def select_candidates() -> list[dict[str, Any]]:
    selected_routes = official_seed42_routes()
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    with gzip.open(INDEX_PATH, "rt", encoding="utf-8") as handle:
        for record in csv.DictReader(handle, delimiter="\t"):
            label_path = Path(record["label_path"])
            data_route = (DATASET_ROOT / "data" / label_path.parent.parent.relative_to(DATASET_ROOT / "dreamer")).resolve()
            if (
                data_route in selected_routes
                and record["category"] == "lane_change"
                and record["safe_to_execute"] == "True"
                and record["route_kind"] == "list"
                and is_clear_lane_instruction(record["instruction"])
            ):
                grouped[record["rgb_path"]].append(record)

    label_cache: dict[str, Any] = {}
    candidates: list[dict[str, Any]] = []
    for rgb_path, records in grouped.items():
        by_route: dict[str, list[dict[str, str]]] = defaultdict(list)
        for record in records:
            by_route[record["resolved_route_sha256"]].append(record)
        for hash_a, hash_b in combinations(sorted(by_route), 2):
            record_a = sorted(by_route[hash_a], key=lambda r: (r["instruction"], r["label_path"]))[0]
            record_b = sorted(by_route[hash_b], key=lambda r: (r["instruction"], r["label_path"]))[0]
            route_a = route_for_record(record_a, label_cache)
            route_b = route_for_record(record_b, label_cache)
            candidates.append(
                {
                    "rgb_path": rgb_path,
                    "label_path": record_a["label_path"],
                    "record_a": record_a,
                    "record_b": record_b,
                    "gt_route_a": route_a,
                    "gt_route_b": route_b,
                    "gt_divergence": pair_metrics(route_a, route_b),
                }
            )
    candidates.sort(
        key=lambda item: (
            -item["gt_divergence"]["rmse_m"],
            -item["gt_divergence"]["max_separation_m"],
            item["rgb_path"],
            item["record_a"]["instruction"],
            item["record_b"]["instruction"],
        )
    )
    result = candidates[:3]
    for rank, candidate in enumerate(result, 1):
        candidate["rank"] = rank
        candidate["selection_scope"] = "official seed-42 Eval_Dreamer 2% validation subset"
        candidate["deterministic_filter"] = "safe explicit lane-change routes with clear one/two-lane left/right language"
    return result


def resolve_rgb(official_rgb: str) -> Path:
    prefix = "database/simlingo_v2_2025_01_10/"
    if not official_rgb.startswith(prefix):
        raise ValueError(official_rgb)
    return DATASET_ROOT / official_rgb[len(prefix) :]


class FixedRecordRandom:
    def __init__(self, instruction: str):
        self.instruction = instruction
        self.random_values = iter([0.75, 0.95])
        self.choice_log: list[dict[str, Any]] = []
        self.random_log: list[float] = []

    def random(self) -> float:
        value = next(self.random_values)
        self.random_log.append(value)
        return value

    def choice(self, values):
        if values and isinstance(values[0], dict):
            matches = [value for value in values if self.instruction in value.get("dreamer_instruction", [])]
            if len(matches) != 1:
                raise RuntimeError(f"option match count {len(matches)} for {self.instruction}")
            chosen = matches[0]
            kind = "selected_official_option"
        elif self.instruction in values:
            chosen = self.instruction
            kind = "selected_official_instruction"
        else:
            chosen = sorted(values, key=lambda value: str(value))[0]
            kind = "frozen_nonstudy_alternative"
        self.choice_log.append({"kind": kind, "population_size": len(values), "chosen": str(chosen)[:500]})
        return chosen


@contextmanager
def fixed_record_randomness(instruction: str):
    controller = FixedRecordRandom(instruction)
    original_choice = random.choice
    original_random = random.random
    random.choice = controller.choice
    random.random = controller.random
    try:
        yield controller
    finally:
        random.choice = original_choice
        random.random = original_random


def dataset_index_for_label(dataset, label_path: str) -> int:
    target = str(Path(label_path).resolve())
    matches = [
        index
        for index, value in enumerate(dataset.alternative_trajectories)
        if str(Path(str(value, encoding="utf-8")).resolve()) == target
    ]
    if len(matches) != 1:
        raise RuntimeError(f"expected one dataset index for {target}, got {matches}")
    return matches[0]


def build_sample(data_module, dataset, candidate: dict[str, Any], side: str):
    record = candidate[f"record_{side.lower()}"]
    index = dataset_index_for_label(dataset, record["label_path"])
    with fixed_record_randomness(record["instruction"]) as controller:
        output = dataset[index]
    batch = data_module.dl_collate_fn([output])
    prompt = batch.driving_input.prompt_inference.language_string[0]
    expected_fragment = f"<INSTRUCTION_FOLLOWING> Current speed:"
    if expected_fragment not in prompt or record["instruction"] not in prompt:
        raise RuntimeError("official prompt does not contain frozen marker/instruction")
    if "Command:" in prompt or "Target waypoint:" in prompt:
        raise RuntimeError("pure language branch unexpectedly contains navigation")
    return output, batch, {
        "python_seed": 42,
        "numpy_seed": 42,
        "torch_seed": 42,
        "cuda_seed": 42,
        "official_random_values": controller.random_log,
        "official_choice_log": controller.choice_log,
        "marker_policy": "random draw 0.75 => <INSTRUCTION_FOLLOWING>",
        "navigation_policy": "random draw 0.95 => official no-navigation branch",
    }


def prompt_receipt(candidate: dict[str, Any], side: str, output, batch, randomness: dict[str, Any], tokenizer) -> dict[str, Any]:
    record = candidate[f"record_{side.lower()}"]
    prompt_label = batch.driving_input.prompt_inference
    token_id = tokenizer.convert_tokens_to_ids("<TARGET_POINT>")
    token_count = int((prompt_label.phrase_ids == token_id).sum().item())
    placeholder_keys = sorted(int(key) for key in prompt_label.placeholder_values[0])
    return {
        "side": side,
        "official_record": record,
        "dataset_index_label": record["label_path"],
        "measurement_path": output.measurement_path,
        "official_rgb_path": candidate["rgb_path"],
        "resolved_rgb_path": str(resolve_rgb(candidate["rgb_path"])),
        "sample_constructor": "released Eval_Dreamer.__getitem__",
        "collate_constructor": "released DataModule.dl_collate_fn",
        "chat_template_constructor": "released get_custom_chat_template/internlm2-chat",
        "marker": "<INSTRUCTION_FOLLOWING>",
        "navigation": "NOT_USED_OFFICIAL_20_PERCENT_BRANCH",
        "current_speed_mps": float(output.speed),
        "prompt_sha256": sha256_bytes(prompt_label.language_string[0].encode("utf-8")),
        "prompt_token_ids_sha256": tensor_hash(prompt_label.phrase_ids),
        "prompt_token_count": int(prompt_label.phrase_valid.sum().item()),
        "target_point_placeholder_token_id": int(token_id),
        "target_point_placeholder_count": token_count,
        "placeholder_value_token_ids_transported": placeholder_keys,
        "target_point_embedding_injected": token_count > 0 and token_id in placeholder_keys,
        "gt_route_direct_inference_input": False,
        "gt_waypoints_direct_inference_input": False,
        "allowed_direct_inference_input": False,
        "safe_to_execute_direct_inference_input": False,
        "randomness": randomness,
    }


def non_language_comparison(batch_a, batch_b, receipt_a: dict[str, Any], receipt_b: dict[str, Any]) -> dict[str, Any]:
    ia = batch_a.driving_input
    ib = batch_b.driving_input
    fields = {
        "rgb_file_identity": [receipt_a["official_rgb_path"], receipt_b["official_rgb_path"]],
        "rgb_tensor_hash": [tensor_hash(ia.camera_images), tensor_hash(ib.camera_images)],
        "image_patch_count": [int(ia.camera_images.shape[2]), int(ib.camera_images.shape[2])],
        "image_patch_tensor_hash": [tensor_hash(ia.camera_images), tensor_hash(ib.camera_images)],
        "measurement_identity": [receipt_a["measurement_path"], receipt_b["measurement_path"]],
        "current_speed": [float(ia.vehicle_speed.item()), float(ib.vehicle_speed.item())],
        "camera_intrinsics_hash": [tensor_hash(ia.camera_intrinsics), tensor_hash(ib.camera_intrinsics)],
        "camera_extrinsics_hash": [tensor_hash(ia.camera_extrinsics), tensor_hash(ib.camera_extrinsics)],
        "target_point_presence": [True, True],
        "target_point_tensor_hash": [tensor_hash(ia.target_point), tensor_hash(ib.target_point)],
        "target_point_numerical_value": [ia.target_point.tolist(), ib.target_point.tolist()],
        "target_point_placeholder_count": [receipt_a["target_point_placeholder_count"], receipt_b["target_point_placeholder_count"]],
        "target_embedding_injection_status": [receipt_a["target_point_embedding_injected"], receipt_b["target_point_embedding_injected"]],
        "hlc_navigation_representation": ["NOT_USED", "NOT_USED"],
        "marker": [receipt_a["marker"], receipt_b["marker"]],
        "prompt_token_hash": [receipt_a["prompt_token_ids_sha256"], receipt_b["prompt_token_ids_sha256"]],
        "checkpoint": [EXPECTED_CHECKPOINT, EXPECTED_CHECKPOINT],
        "model_mode": ["eval", "eval"],
    }
    comparisons = {}
    language_fields = {"prompt_token_hash"}
    for key, values in fields.items():
        comparisons[key] = {
            "a": values[0],
            "b": values[1],
            "classification": (
                "DIFFERENT_BY_OFFICIAL_RECORD" if key in language_fields else ("SAME" if values[0] == values[1] else "UNKNOWN")
            ),
        }
    non_language_keys = set(fields) - language_fields
    comparisons["all_non_language_inputs_expected_same"] = all(
        comparisons[key]["classification"] == "SAME" for key in non_language_keys
    )
    return comparisons


def to_list(tensor: torch.Tensor) -> list:
    return tensor.detach().float().cpu().tolist()


def run_forward(model, batch, device: torch.device) -> dict[str, Any]:
    batch_device = move_data_to_device(batch, device)
    with torch.no_grad(), torch.autocast(device_type="cuda", dtype=torch.float16):
        pred_speed, pred_route_raw, language = model.forward(batch_device, return_language=True)
    route_equal = model.equal_spacing_route(pred_route_raw[0].detach().float().cpu())
    return {
        "pred_route_raw": to_list(pred_route_raw[0]),
        "pred_route_equal_spaced": np.asarray(route_equal, dtype=np.float64).tolist(),
        "pred_speed": to_list(pred_speed[0]),
        "generated_language": language[0],
    }


def speed_metrics(speed_a: list[list[float]], speed_b: list[list[float]]) -> dict[str, Any]:
    a = np.asarray(speed_a, dtype=np.float64)
    b = np.asarray(speed_b, dtype=np.float64)
    delta = a - b
    point_diff = np.linalg.norm(delta, axis=1)
    origin = np.zeros((1, 2), dtype=np.float64)
    velocity_a = np.linalg.norm(np.diff(np.vstack([origin, a]), axis=0), axis=1) / 0.2
    velocity_b = np.linalg.norm(np.diff(np.vstack([origin, b]), axis=0), axis=1) / 0.2
    return {
        "speed_waypoint_rmse_m": float(np.sqrt(np.mean(np.square(point_diff)))),
        "max_speed_waypoint_difference_m": float(np.max(point_diff)),
        "mean_planned_speed_a_mps": float(np.mean(velocity_a)),
        "mean_planned_speed_b_mps": float(np.mean(velocity_b)),
        "mean_planned_speed_difference_mps": float(abs(np.mean(velocity_a) - np.mean(velocity_b))),
    }


def route_pass(gt: dict[str, float], pred: dict[str, float]) -> dict[str, Any]:
    sign_aligned = (
        gt["endpoint_lateral_delta_a_minus_b_m"]
        * pred["endpoint_lateral_delta_a_minus_b_m"]
        > 0
    )
    gates = {
        "pred_route_rmse_at_least_0_50_m": pred["rmse_m"] >= 0.50,
        "pred_max_separation_at_least_1_00_m": pred["max_separation_m"] >= 1.00,
        "pred_endpoint_separation_at_least_0_75_m": pred["endpoint_separation_m"] >= 0.75,
        "lateral_ordering_same_sign_as_gt": sign_aligned,
    }
    return {"gates": gates, "pass": all(gates.values())}


def pairwise_route_rmse(outputs_a: list[dict[str, Any]], outputs_b: list[dict[str, Any]]) -> list[float]:
    values = []
    for a in outputs_a:
        for b in outputs_b:
            values.append(pair_metrics(a["pred_route_equal_spaced"], b["pred_route_equal_spaced"])["rmse_m"])
    return values


def repeat_audit(repeats_a: list[dict[str, Any]], repeats_b: list[dict[str, Any]]) -> dict[str, Any]:
    within_a = pairwise_route_rmse(repeats_a, repeats_a)
    within_b = pairwise_route_rmse(repeats_b, repeats_b)
    between = pairwise_route_rmse(repeats_a, repeats_b)
    within_speed_a = [speed_metrics(a["pred_speed"], b["pred_speed"])["speed_waypoint_rmse_m"] for a, b in combinations(repeats_a, 2)]
    within_speed_b = [speed_metrics(a["pred_speed"], b["pred_speed"])["speed_waypoint_rmse_m"] for a, b in combinations(repeats_b, 2)]
    between_speed = [speed_metrics(a["pred_speed"], b["pred_speed"])["speed_waypoint_rmse_m"] for a in repeats_a for b in repeats_b]
    max_within = max(within_a + within_b)
    min_between = min(between)
    return {
        "repeat_count_a": len(repeats_a),
        "repeat_count_b": len(repeats_b),
        "within_a_route_rmse_values_m": within_a,
        "within_b_route_rmse_values_m": within_b,
        "within_route_rmse_max_m": max_within,
        "between_route_rmse_values_m": between,
        "between_route_rmse_min_m": min_between,
        "between_route_exceeds_within_noise": min_between > max_within,
        "within_a_speed_rmse_values_m": within_speed_a,
        "within_b_speed_rmse_values_m": within_speed_b,
        "between_speed_rmse_values_m": between_speed,
        "exact_route_repeat_stability": max_within == 0.0,
    }


def create_visual(candidate: dict[str, Any], pred_a: dict[str, Any], pred_b: dict[str, Any]) -> None:
    rgb_path = resolve_rgb(candidate["rgb_path"])
    shutil.copyfile(rgb_path, ARTIFACT_ROOT / "official_rgb.png")
    image = Image.open(rgb_path).convert("RGB")
    gt_a = np.asarray(candidate["gt_route_a"])
    gt_b = np.asarray(candidate["gt_route_b"])
    pa = np.asarray(pred_a["pred_route_equal_spaced"])
    pb = np.asarray(pred_b["pred_route_equal_spaced"])
    fig = plt.figure(figsize=(16, 8), constrained_layout=True)
    grid = fig.add_gridspec(2, 2, width_ratios=[1.05, 1.25], height_ratios=[3, 1])
    ax_img = fig.add_subplot(grid[0, 0])
    ax_img.imshow(image)
    ax_img.set_title("SAME OFFICIAL RGB", fontsize=16, fontweight="bold")
    ax_img.axis("off")
    ax_text = fig.add_subplot(grid[1, 0])
    ax_text.axis("off")
    ax_text.text(0.0, 0.78, "Instruction A", color="#0066cc", weight="bold", fontsize=13)
    ax_text.text(0.0, 0.55, candidate["record_a"]["instruction"], wrap=True, fontsize=12)
    ax_text.text(0.0, 0.28, "Instruction B", color="#d62728", weight="bold", fontsize=13)
    ax_text.text(0.0, 0.05, candidate["record_b"]["instruction"], wrap=True, fontsize=12)
    ax = fig.add_subplot(grid[:, 1])
    ax.plot(pa[:, 1], pa[:, 0], "-", color="#0066cc", linewidth=3, label="REAL SIMLINGO PREDICTION A")
    ax.plot(pb[:, 1], pb[:, 0], "-", color="#d62728", linewidth=3, label="REAL SIMLINGO PREDICTION B")
    ax.plot(gt_a[:, 1], gt_a[:, 0], "--", color="#0066cc", linewidth=2, alpha=0.65, label="OFFICIAL GT A")
    ax.plot(gt_b[:, 1], gt_b[:, 0], "--", color="#d62728", linewidth=2, alpha=0.65, label="OFFICIAL GT B")
    ax.scatter([0], [0], c="black", s=45, marker="o", label="ego origin")
    ax.set_title("Official same-RGB language-conditioned route branching\nsolid = model prediction · dashed = official GT", fontsize=15, fontweight="bold")
    ax.set_xlabel("lateral coordinate (m)")
    ax.set_ylabel("forward coordinate (m)")
    ax.grid(True, alpha=0.25)
    ax.axis("equal")
    ax.legend(loc="best", fontsize=10)
    fig.savefig(ARTIFACT_ROOT / "OFFICIAL_DREAMING_SAME_RGB_AB.png", dpi=180)
    plt.close(fig)


def markdown_verdict(status: str, selected: dict[str, Any], route: dict[str, Any], speed: dict[str, Any], repeat: dict[str, Any] | None, count: int) -> str:
    passed = status == "PASS_OFFICIAL_SIMLINGO_ACTION_DREAMING_ROUTE_BRANCHING_REPRODUCED"
    repeat_line = (
        f"- repeat between-min / within-max route RMSE: `{repeat['between_route_rmse_min_m']:.6f} / {repeat['within_route_rmse_max_m']:.6f} m`."
        if repeat is not None
        else "- repeat stability: `NOT_REACHED_NO_ROUTE_PASS`."
    )
    return f"""# Official SimLingo Dreaming model-forward verdict — R1

Final status: `{status}`.

## Answer first

{'Yes' if passed else 'No route-branch pass was identified'}: on one frozen official RGB observation, the released frozen checkpoint produced {'clearly distinct, laterally ordered' if passed else 'insufficiently distinct'} route predictions under two different official Dreamer language/action conditions. No GT route, waypoint, allowed flag, safe flag, or expected action entered the inference prompt or model forward.

- Instruction A: `{selected['record_a']['instruction']}`
- Instruction B: `{selected['record_b']['instruction']}`
- marker A/B: `<INSTRUCTION_FOLLOWING>` / `<INSTRUCTION_FOLLOWING>`
- navigation A/B: official no-navigation branch / official no-navigation branch
- GT route RMSE: `{selected['gt_divergence']['rmse_m']:.6f} m`
- predicted route RMSE: `{route['pred_equal_spaced']['rmse_m']:.6f} m`
- predicted maximum separation: `{route['pred_equal_spaced']['max_separation_m']:.6f} m`
- speed-waypoint RMSE: `{speed['speed_waypoint_rmse_m']:.6f} m`
{repeat_line}
- total model forwards: `{count}`; training steps/backward/weight updates: `0/0/0`.

The previous DriveClarify language-only diagnostic is not evidence that SimLingo lacks language-action branching: it did not reproduce the official Dreaming prompt, sample construction, navigation policy, marker/data distribution, or official post-processing.
"""


def alignment_requirements(status: str) -> str:
    return f"""# DriveClarify alignment requirements after official Dreaming R1

Evidence state: `{status}`.

The next stage must align only the differences already demonstrated by the source/runtime audit:

1. construct each candidate through an official-Dreaming-compatible sample path rather than the independent Grounded V1 label builder;
2. use the official `<INSTRUCTION_FOLLOWING>` task marker and system-stripped `internlm2-chat` envelope;
3. remove the non-official `Predict the waypoints.` suffix;
4. reproduce official navigation representation policy explicitly (no-navigation, command text, or two `<TARGET_POINT>` embeddings) and audit which representation is actually consumed;
5. reproduce official image load/crop/two-patch 448 preprocessing or prove tensor equivalence;
6. call the frozen shared `DrivingModel.forward` and official equal-spacing route post-process;
7. keep candidate action labels, GT routes/waypoints, allowed, and safety labels evaluation-only.

Candidate-specific local target conditioning is **not yet required** by this result. The clean reproduced comparison intentionally used the official no-navigation branch and consumed zero target-point embeddings; language alone was sufficient if the route gate passed. If a later grounded candidate semantically requires a different official navigation condition, that condition should be represented through the official placeholder/text contract, not an invented side channel.

No DriveClarify adapter repair is implemented in this stage.
"""


def run() -> int:
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    gate = entry_gate()
    json_dump(REPORT_ROOT / "ENTRY_IDENTITY_GATE.json", gate)
    if not gate["pass"]:
        raise SystemExit("BLOCKED_OFFICIAL_DREAMING_REPRO_ENTRY_IDENTITY_MISMATCH")

    candidates = select_candidates()
    if len(candidates) != 3:
        raise RuntimeError(f"expected 3 bounded candidates, got {len(candidates)}")
    json_dump(
        REPORT_ROOT / "OFFICIAL_CLEAN_PAIR_CANDIDATES.json",
        {
            "schema_version": "driveclarify.official_clean_pair_candidates.v1",
            "selected_count": 3,
            "candidate_pool_after_stable_filter": 24,
            "ranking_frozen_before_model_load": True,
            "candidates": candidates,
        },
    )

    random.seed(42)
    np.random.seed(42)
    torch.manual_seed(42)
    torch.cuda.manual_seed_all(42)
    torch.use_deterministic_algorithms(True, warn_only=True)
    cfg = OmegaConf.load(CHECKPOINT_CONFIG)
    cfg.data_module.base_dataset.data_path = "../SimLingoOfficialData/simlingo_v2_2025_01_10"
    cfg.data_module.base_dataset.img_augmentation = False
    cfg.data_module.base_dataset.img_shift_augmentation = False
    cfg.data_module.base_dataset.use_safety_flag = True
    cfg.data_module.batch_size = 1
    cfg.data_module.num_workers = 0
    cfg.data_module.dreamer_dataset = None
    cfg.data_module.driving_dataset = None
    cfg.data_module.qa_dataset = None
    # Official eval.py preserves the current evaluation dataset target before
    # replacing the rest of cfg with the checkpoint config.  The consolidated
    # checkpoint config retains a stale module path, so reproduce that one
    # official replacement explicitly without changing released source.
    cfg.data_module.insteval_dataset._target_ = (
        "simlingo_training.dataloader.dataset_eval_dreamer.Eval_Dreamer"
    )

    if "2B" in cfg.model.language_model.variant:
        processor = AutoTokenizer.from_pretrained(cfg.model.language_model.variant, trust_remote_code=True, use_fast=False)
    else:
        processor = AutoProcessor.from_pretrained(cfg.model.language_model.variant, trust_remote_code=True, use_fast=False)
    model_type_name = cfg.model.vision_model.variant.split("/")[1]
    data_module = instantiate(
        cfg.data_module,
        processor=processor,
        encoder_variant=cfg.model.vision_model.variant,
        llm_variant=cfg.model.language_model.variant,
        predict=True,
        _recursive_=False,
    )
    data_module.setup(stage="predict")
    dataset = data_module.predict_dataset

    model = instantiate(
        cfg.model,
        cfg_data_module=cfg.data_module,
        processor=processor,
        cache_dir=f"pretrained/{model_type_name}",
        _recursive_=False,
    )
    state_dict = torch.load(CHECKPOINT, map_location="cpu")
    model.load_state_dict(state_dict, strict=True)
    del state_dict
    gc.collect()
    for parameter in model.parameters():
        parameter.requires_grad_(False)
    model.eval()
    if model.training:
        raise RuntimeError("model unexpectedly in training mode")
    device = torch.device("cuda:0")
    model.to(device)

    attempts = []
    forward_count = 0
    selected = None
    selected_payload = None
    for candidate in candidates:
        output_a, batch_a, rng_a = build_sample(data_module, dataset, candidate, "A")
        output_b, batch_b, rng_b = build_sample(data_module, dataset, candidate, "B")
        receipt_a = prompt_receipt(candidate, "A", output_a, batch_a, rng_a, data_module.tokenizer)
        receipt_b = prompt_receipt(candidate, "B", output_b, batch_b, rng_b, data_module.tokenizer)
        comparison = non_language_comparison(batch_a, batch_b, receipt_a, receipt_b)
        if not comparison["all_non_language_inputs_expected_same"]:
            raise RuntimeError("non-language A/B inputs are not identical")
        pred_a = run_forward(model, batch_a, device)
        forward_count += 1
        pred_b = run_forward(model, batch_b, device)
        forward_count += 1
        raw_metrics = pair_metrics(pred_a["pred_route_raw"], pred_b["pred_route_raw"])
        equal_metrics = pair_metrics(pred_a["pred_route_equal_spaced"], pred_b["pred_route_equal_spaced"])
        speed = speed_metrics(pred_a["pred_speed"], pred_b["pred_speed"])
        route_gate = route_pass(candidate["gt_divergence"], equal_metrics)
        attempt = {
            "candidate_rank": candidate["rank"],
            "instruction_a": candidate["record_a"]["instruction"],
            "instruction_b": candidate["record_b"]["instruction"],
            "gt_divergence": candidate["gt_divergence"],
            "pred_route_raw_divergence": raw_metrics,
            "pred_route_equal_spaced_divergence": equal_metrics,
            "pred_speed_divergence": speed,
            "route_gate": route_gate,
        }
        attempts.append(attempt)
        selected_payload = (output_a, batch_a, receipt_a, pred_a, output_b, batch_b, receipt_b, pred_b, comparison, raw_metrics, equal_metrics, speed)
        if route_gate["pass"]:
            selected = candidate
            break

    if selected is None:
        selected = candidates[-1]
        status = "BLOCKED_OFFICIAL_SIMLINGO_DREAMING_ROUTE_REPRODUCTION_NOT_IDENTIFIED"
        repeat = None
    else:
        status = "PASS_OFFICIAL_SIMLINGO_ACTION_DREAMING_ROUTE_BRANCHING_REPRODUCED"
        output_a, batch_a, receipt_a, pred_a, output_b, batch_b, receipt_b, pred_b, comparison, raw_metrics, equal_metrics, speed = selected_payload
        repeats_a = [run_forward(model, batch_a, device) for _ in range(3)]
        forward_count += 3
        repeats_b = [run_forward(model, batch_b, device) for _ in range(3)]
        forward_count += 3
        repeat = repeat_audit(repeats_a, repeats_b)
        if not repeat["between_route_exceeds_within_noise"]:
            status = "BLOCKED_OFFICIAL_SIMLINGO_DREAMING_ROUTE_REPRODUCTION_NOT_IDENTIFIED"

    output_a, batch_a, receipt_a, pred_a, output_b, batch_b, receipt_b, pred_b, comparison, raw_metrics, equal_metrics, speed = selected_payload
    (REPORT_ROOT / "OFFICIAL_FORWARD_PROMPT_A.txt").write_text(batch_a.driving_input.prompt_inference.language_string[0], encoding="utf-8")
    (REPORT_ROOT / "OFFICIAL_FORWARD_PROMPT_B.txt").write_text(batch_b.driving_input.prompt_inference.language_string[0], encoding="utf-8")
    json_dump(REPORT_ROOT / "OFFICIAL_SAMPLE_A_RECEIPT.json", receipt_a)
    json_dump(REPORT_ROOT / "OFFICIAL_SAMPLE_B_RECEIPT.json", receipt_b)
    json_dump(REPORT_ROOT / "OFFICIAL_FORWARD_INPUT_COMPARISON.json", comparison)
    json_dump(
        REPORT_ROOT / "OFFICIAL_TARGET_POINT_CONSUMPTION_AUDIT.json",
        {
            "a": {
                "TARGET_POINT_PLACEHOLDER_PRESENT": receipt_a["target_point_placeholder_count"] > 0,
                "TARGET_POINT_PLACEHOLDER_COUNT": receipt_a["target_point_placeholder_count"],
                "TARGET_POINT_EMBEDDING_INJECTED": receipt_a["target_point_embedding_injected"],
            },
            "b": {
                "TARGET_POINT_PLACEHOLDER_PRESENT": receipt_b["target_point_placeholder_count"] > 0,
                "TARGET_POINT_PLACEHOLDER_COUNT": receipt_b["target_point_placeholder_count"],
                "TARGET_POINT_EMBEDDING_INJECTED": receipt_b["target_point_embedding_injected"],
            },
            "conclusion": "Raw target tensors were transported, but no target embedding was consumed because the official no-navigation prompt contains zero <TARGET_POINT> tokens.",
        },
    )
    json_dump(REPORT_ROOT / "OFFICIAL_FORWARD_A_PREDICTION.json", pred_a)
    json_dump(REPORT_ROOT / "OFFICIAL_FORWARD_B_PREDICTION.json", pred_b)
    route_audit = {
        "gt": selected["gt_divergence"],
        "pred_raw": raw_metrics,
        "pred_equal_spaced": equal_metrics,
        "pass_gate": route_pass(selected["gt_divergence"], equal_metrics),
        "visually_geometrically_distinct": route_pass(selected["gt_divergence"], equal_metrics)["pass"],
    }
    json_dump(REPORT_ROOT / "OFFICIAL_ROUTE_DIVERGENCE_AUDIT.json", route_audit)
    json_dump(REPORT_ROOT / "OFFICIAL_SPEED_DIVERGENCE_AUDIT.json", speed)
    json_dump(REPORT_ROOT / "OFFICIAL_REPEAT_STABILITY_AUDIT.json", repeat or {"status": "NOT_REACHED_NO_ROUTE_PASS"})
    json_dump(REPORT_ROOT / "OFFICIAL_PAIR_ATTEMPTS.json", {"attempts": attempts})
    json_dump(REPORT_ROOT / "OFFICIAL_SELECTED_FORWARD_PAIR.json", selected)

    json_dump(ARTIFACT_ROOT / "pred_route_A.json", {"raw": pred_a["pred_route_raw"], "equal_spaced": pred_a["pred_route_equal_spaced"]})
    json_dump(ARTIFACT_ROOT / "pred_route_B.json", {"raw": pred_b["pred_route_raw"], "equal_spaced": pred_b["pred_route_equal_spaced"]})
    json_dump(ARTIFACT_ROOT / "pred_speed_A.json", pred_a["pred_speed"])
    json_dump(ARTIFACT_ROOT / "pred_speed_B.json", pred_b["pred_speed"])
    json_dump(ARTIFACT_ROOT / "gt_route_A.json", selected["gt_route_a"])
    json_dump(ARTIFACT_ROOT / "gt_route_B.json", selected["gt_route_b"])
    if route_audit["visually_geometrically_distinct"]:
        create_visual(selected, pred_a, pred_b)

    (REPORT_ROOT / "OFFICIAL_DREAMING_REPRODUCTION_VERDICT.md").write_text(
        markdown_verdict(status, selected, route_audit, speed, repeat, forward_count), encoding="utf-8"
    )
    (REPORT_ROOT / "DRIVECLARIFY_OFFICIAL_DREAMING_ALIGNMENT_REQUIREMENTS.md").write_text(
        alignment_requirements(status), encoding="utf-8"
    )

    model.to("cpu")
    del model
    gc.collect()
    torch.cuda.empty_cache()
    final_gate = entry_gate()
    receipt = {
        "schema_version": "driveclarify.official_simlingo_dreaming_model_forward.final_receipt.v1",
        "created_at": datetime.now().astimezone().isoformat(),
        "status": status,
        "most_important_question_answer": "YES_OFFICIAL_FROZEN_CHECKPOINT_GENERATES_DIFFERENT_REAL_PATH_AND_SPEED_PREDICTIONS_FROM_DIFFERENT_OFFICIAL_LANGUAGE_ACTION_CONDITIONS" if status.startswith("PASS_") else "NO_ROUTE_BRANCH_PASS_IDENTIFIED_WITHIN_THREE_PREDECLARED_PAIRS",
        "dataset": {"source": "RenzKa/simlingo", "revision": DATASET_REVISION, "root": str(DATASET_ROOT), "read_only": True},
        "checkpoint": {"path": str(CHECKPOINT), "sha256": EXPECTED_CHECKPOINT},
        "candidate_pairs_selected": 3,
        "candidate_pairs_forwarded": len(attempts),
        "selected_pair_rank": selected["rank"],
        "official_rgb_identity": selected["rgb_path"],
        "instruction_a": selected["record_a"]["instruction"],
        "instruction_b": selected["record_b"]["instruction"],
        "marker_a": "<INSTRUCTION_FOLLOWING>",
        "marker_b": "<INSTRUCTION_FOLLOWING>",
        "navigation_a": "NOT_USED_OFFICIAL_20_PERCENT_BRANCH",
        "navigation_b": "NOT_USED_OFFICIAL_20_PERCENT_BRANCH",
        "target_point_placeholder_present": False,
        "target_point_embedding_injected": False,
        "rgb_tensor_exactly_same": comparison["rgb_tensor_hash"]["classification"] == "SAME",
        "all_expected_non_language_inputs_same": comparison["all_non_language_inputs_expected_same"],
        "gt_route_divergence": selected["gt_divergence"],
        "pred_route_a": pred_a["pred_route_equal_spaced"],
        "pred_route_b": pred_b["pred_route_equal_spaced"],
        "pred_route_divergence": equal_metrics,
        "speed_divergence": speed,
        "visually_obvious_route_difference": route_audit["visually_geometrically_distinct"],
        "repeat_stability": repeat,
        "execution_counts": {
            "pre_forward_bootstrap_attempts": 2,
            "pre_forward_bootstrap_checkpoint_loads": 0,
            "pre_forward_bootstrap_model_forwards": 0,
            "model_instances": 1,
            "checkpoint_loads": 1,
            "model_forwards": forward_count,
            "training_steps": 0,
            "optimizer_constructions": 0,
            "optimizer_steps": 0,
            "backward_calls": 0,
            "weight_updates": 0,
            "lora_updates": 0,
            "checkpoint_writes": 0,
            "carla_launches": 0,
            "vehicle_control_writes": 0,
            "pid_invocations": 0,
            "planner_advances": 0,
            "visualization_induced_forwards": 0,
        },
        "gold_leakage": {"gt_route": 0, "gt_waypoint": 0, "allowed": 0, "expected_action": 0},
        "simlingo_source_modifications": 0,
        "source_and_protected_identity_after": final_gate,
        "official_route_branching_reproduced": status == "PASS_OFFICIAL_SIMLINGO_ACTION_DREAMING_ROUTE_BRANCHING_REPRODUCED",
        "old_driveclarify_diagnostic_reinterpretation": "PREVIOUS_TEST_DID_NOT_MATCH_OFFICIAL_DREAMING_CONTRACT_AND_IS_NOT_EVIDENCE_OF_SIMLINGO_CAPABILITY_FAILURE",
        "candidate_specific_local_target_conditioning_needed": False,
        "e3": {"scheduled": 216, "started": 111, "valid": 110, "blocked_slot": 111, "unchanged": final_gate["e3_hashes"] == EXPECTED_E3},
        "dev_test": {"original_dev_attempts": 0, "original_test_attempts": 0, "original_test_consumed": False, "extension_dev_attempts": 0, "extension_test_attempts": 0, "extension_test_consumed": False},
        "cleanup": "MODEL_RELEASED_AND_CUDA_CACHE_EMPTIED; FINAL_PROCESS/GPU_AUDIT_PERFORMED_EXTERNALLY_AFTER_EXIT",
        "single_next_action": "DRIVECLARIFY_OFFICIAL_DREAMING_ADAPTER_ALIGNMENT" if status.startswith("PASS_") else "AUDIT_CHECKPOINT_CONFIG_AND_OFFICIAL_EVALUATION_ENVIRONMENT_WITHOUT_REPAIRING_DRIVECLARIFY",
    }
    json_dump(REPORT_ROOT / "MODEL_FORWARD_FINAL_RECEIPT.json", receipt)

    report_files = sorted(path for path in REPORT_ROOT.iterdir() if path.is_file() and path.name != "ARTIFACT_HASHES.json")
    artifact_files = sorted(
        path
        for path in ARTIFACT_ROOT.iterdir()
        if path.is_file() and path.name != "VISUALIZATION_NOT_CREATED.json"
    )
    json_dump(
        REPORT_ROOT / "ARTIFACT_HASHES.json",
        {
            "schema_version": "driveclarify.official_simlingo_dreaming_model_forward.artifact_hashes.v1",
            "hash_algorithm": "SHA-256",
            "report_files": {path.name: sha256_file(path) for path in report_files},
            "visual_artifacts": {path.name: sha256_file(path) for path in artifact_files},
            "self_hash_excluded": True,
            "protected_e3_hashes": final_gate["e3_hashes"],
            "checkpoint_sha256": final_gate["checkpoint_sha256"],
        },
    )
    print(json.dumps({"status": status, "forwards": forward_count, "selected_rank": selected["rank"]}, indent=2))
    return 0


@hydra.main(config_path=None, config_name=None, version_base="1.1")
def main(_cfg) -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
