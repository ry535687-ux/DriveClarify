"""Offline/replay evidence runner for the consequence-equivalence revision.

No CARLA process is started.  Saved raw BGR frames are passed through the exact
released live JPEG/crop/dynamic-preprocess path, then through the frozen shared
checkpoint and OfficialDreamingCandidateAdapter.v1.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
import random
from dataclasses import asdict
from datetime import datetime
from itertools import combinations
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable, Mapping, Sequence

import cv2
import hydra
import numpy as np
import torch
from hydra.utils import instantiate
from omegaconf import OmegaConf
from PIL import Image
from transformers import AutoProcessor, AutoTokenizer

from driveclarify_grounded_language_v1_extension_e1_r1.runtime import _bind_candidate
from driveclarify_grounded_language_v1_extension_e1_r1.topology import ManeuverOpportunity
from driveclarify_language_grounding_v1.candidate_pipeline import GroundedCandidatePipeline
from driveclarify_language_grounding_v1.slot_parser import SemanticSlotParser
from driveclarify_language_grounding_v1.visual_grounder import GroundingDinoVisualGrounder
from driveclarify_official_dreaming_adapter.adapter import (
    OfficialDreamingCandidateAdapter,
    OfficialDreamingCandidateForwardProvider,
)
from driveclarify_paper_mvp_runtime.contracts import RuntimeCandidate, canonical_sha256
from driveclarify_simlingo_local_candidate_diagnostic.runtime import _tensor_receipt
from simlingo_training.utils.custom_types import DrivingInput
from simlingo_training.utils.internvl2_utils import build_transform, dynamic_preprocess

from .protocol import (
    CandidateRelationship,
    ClassificationEvidence,
    DecisionEvidence,
    TriState,
    classify_candidate_relationship,
    evaluate_decision_contract,
)
from .renderer import (
    ConsequenceAwareGroundedSemantic,
    ConsequenceAwareOfficialDreamingRenderer,
)


ROOT = Path(__file__).resolve().parents[1]
REPORT_ROOT = ROOT / "reports/driveclarify_candidate_consequence_equivalence_protocol_revision"
ARTIFACT_ROOT = ROOT / "artifacts/driveclarify_candidate_consequence_equivalence_protocol_revision"
SIMLINGO_ROOT = Path("/home/buaa/wrh/simlingo")
CHECKPOINT = SIMLINGO_ROOT / "outputs/simlingo/checkpoints/epoch=013.ckpt/pytorch_model.pt"
CHECKPOINT_CONFIG = SIMLINGO_ROOT / "outputs/simlingo/.hydra/config.yaml"
EXPECTED_CHECKPOINT = "ec8943723d266ee9f5f56f45d153a163b22616960bfccb741965ea5daa700d28"
OWN_ROOT = ROOT / "artifacts/driveclarify_official_dreaming_adapter_alignment/own_scene_explicit_a1b1_attempt4"
OWN_RECEIPT = OWN_ROOT / "GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json"
OWN_RGB = OWN_ROOT / "same_observation_rgb_0.png"
WHITE_ROOT = ROOT / "artifacts/grounded_language_v1_extension_e1_r1/e2/E1R1-ASK-PHYS-001/driveclarify_grounded_v1"
WHITE_RECEIPT = WHITE_ROOT / "GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json"
WHITE_RGB = WHITE_ROOT / "E1R1_GROUNDING_RGB_0.png"
EXPECTED_WHITE_PNG = "3c4eca17561de6b83c14101142969fa4cad4cca3eb670176318fee7704dbc6a1"
INSTRUCTION_A = "Move one lane towards the right."
INSTRUCTION_B = "Continue driving on your current lane."


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _camera_tensor(path: Path, *, use_thumbnail: bool) -> tuple[np.ndarray, torch.Tensor]:
    raw = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if raw is None or raw.shape != (512, 1024, 3):
        raise RuntimeError("FROZEN_RGB_UNAVAILABLE_OR_SHAPE_CHANGED:" + str(path))
    encoded_ok, encoded = cv2.imencode(".jpg", raw)
    if not encoded_ok:
        raise RuntimeError("FROZEN_RGB_JPEG_ROUNDTRIP_FAILED")
    camera = cv2.imdecode(encoded, cv2.IMREAD_UNCHANGED)
    rgb = cv2.cvtColor(camera, cv2.COLOR_BGR2RGB)
    rgb = rgb[: int(rgb.shape[0] - (rgb.shape[0] * 4.8) // 16), :, :]
    image = Image.fromarray(rgb)
    patches = dynamic_preprocess(
        image,
        image_size=448,
        use_thumbnail=bool(use_thumbnail),
        max_num=2,
    )
    values = torch.stack([build_transform(input_size=448)(item) for item in patches])
    tensor = values.view(1, 1, len(patches), 3, 448, 448).bfloat16()
    return raw, tensor


def _calibration_from_historical_receipt(receipt: Mapping[str, Any]) -> tuple[Any, Any]:
    fields = receipt["input_conditioning_rows"][0]["before"]["driving_input_fields"]
    intrinsics = torch.tensor(fields["camera_intrinsics"]["value"][0], dtype=torch.float32)
    extrinsics = torch.tensor(fields["camera_extrinsics"]["value"][0], dtype=torch.float32)
    return (intrinsics,), (extrinsics,)


def _base_input(
    *,
    camera: torch.Tensor,
    intrinsics: Any,
    extrinsics: Any,
    vehicle_speed: Sequence[Sequence[float]],
    target_point: Sequence[Sequence[float]],
    tokenizer: Any,
    encoder_variant: str,
    device: torch.device,
) -> DrivingInput:
    adapter = OfficialDreamingCandidateAdapter(
        tokenizer=tokenizer,
        encoder_variant=encoder_variant,
        device=device,
    )
    dummy = adapter.build("Continue driving on your current lane.", float(vehicle_speed[0][0]))
    move = lambda value: value.to(device) if hasattr(value, "to") else value
    intrinsics_device = tuple(move(value) for value in intrinsics)
    extrinsics_device = tuple(move(value) for value in extrinsics)
    return DrivingInput(
        camera_images=camera.to(device),
        image_sizes=None,
        camera_intrinsics=intrinsics_device,
        camera_extrinsics=extrinsics_device,
        vehicle_speed=torch.tensor(vehicle_speed, dtype=torch.float32, device=device),
        target_point=torch.tensor(target_point, dtype=torch.float32, device=device),
        prompt=dummy.prompt,
        prompt_inference=dummy.prompt_inference,
    )


def _non_language_receipt(driving: DrivingInput) -> tuple[dict[str, Any], str]:
    values = driving._asdict()
    fields = {
        name: _tensor_receipt(value)
        for name, value in values.items()
        if name not in {"prompt", "prompt_inference"}
    }
    return fields, canonical_sha256(fields)


def _load_model() -> tuple[Any, Any, Any, torch.device]:
    if file_sha256(CHECKPOINT) != EXPECTED_CHECKPOINT:
        raise RuntimeError("FROZEN_CHECKPOINT_HASH_MISMATCH")
    random.seed(42)
    np.random.seed(42)
    torch.manual_seed(42)
    torch.cuda.manual_seed_all(42)
    torch.use_deterministic_algorithms(True, warn_only=True)
    cfg = OmegaConf.load(CHECKPOINT_CONFIG)
    if "2B" in cfg.model.language_model.variant:
        processor = AutoTokenizer.from_pretrained(
            cfg.model.language_model.variant,
            trust_remote_code=True,
            use_fast=False,
            local_files_only=True,
        )
    else:
        processor = AutoProcessor.from_pretrained(
            cfg.model.language_model.variant,
            trust_remote_code=True,
            use_fast=False,
            local_files_only=True,
        )
    tokenizer = processor.tokenizer if hasattr(processor, "tokenizer") else processor
    tokenizer.add_special_tokens(
        {
            "additional_special_tokens": [
                "<WAYPOINTS>",
                "<WAYPOINTS_DIFF>",
                "<ORG_WAYPOINTS_DIFF>",
                "<ORG_WAYPOINTS>",
                "<WAYPOINT_LAST>",
                "<ROUTE>",
                "<ROUTE_DIFF>",
                "<TARGET_POINT>",
            ]
        }
    )
    tokenizer.padding_side = "left"
    model_type = cfg.model.vision_model.variant.split("/")[1]
    model = instantiate(
        cfg.model,
        cfg_data_module=cfg.data_module,
        processor=processor,
        cache_dir=str((ROOT / "pretrained" / model_type).resolve()),
        _recursive_=False,
    )
    state = torch.load(CHECKPOINT, map_location="cpu")
    model.load_state_dict(state, strict=True)
    del state
    for parameter in model.parameters():
        parameter.requires_grad_(False)
    model.eval()
    device = torch.device("cuda:0")
    model.to(device)
    return model, tokenizer, cfg, device


def _close_model(model: Any) -> None:
    model.to("cpu")
    del model
    gc.collect()
    torch.cuda.empty_cache()


def _candidate(prompt: str, label: str, observation_id: str, frame_id: int, image_sha: str) -> RuntimeCandidate:
    return RuntimeCandidate(
        candidate_id="cce-" + label.casefold(),
        interpretation_id="cce-meaning-" + label.casefold(),
        prompt_text=prompt,
        visual_track_id="frozen-rgb-replay",
        visual_anchor_digest=image_sha,
        candidate_semantic_digest=canonical_sha256({"label": label, "prompt": prompt}),
        candidate_input_digest=canonical_sha256(
            {"observation_id": observation_id, "frame_id": frame_id, "image": image_sha, "prompt": prompt}
        ),
        source_observation_id=observation_id,
        source_frame_id=frame_id,
        generator_rule="consequence-aware-grounded-renderer-v1",
    )


def _forward_rows(
    *,
    model: Any,
    processor: Any,
    cfg: Any,
    device: torch.device,
    base: DrivingInput,
    prompts: Sequence[tuple[str, str]],
    observation_id: str,
    frame_id: int,
    image_sha: str,
    repetitions: int,
) -> list[dict[str, Any]]:
    agent = SimpleNamespace(
        tokenizer=processor,
        cfg=cfg,
        device=device,
        model=model,
        DrivingInput=base._asdict(),
    )
    provider = OfficialDreamingCandidateForwardProvider(agent)
    episode = SimpleNamespace(
        ego_state=SimpleNamespace(speed_mps=float(base.vehicle_speed.detach().cpu()[0, 0])),
        vision_observation=SimpleNamespace(
            observation_id=observation_id,
            frame_id=frame_id,
        ),
    )
    candidates = [
        (label, _candidate(prompt, label, observation_id, frame_id, image_sha))
        for label, prompt in prompts
    ]
    rows = []
    for repetition in range(1, repetitions + 1):
        for label, candidate in candidates:
            result = provider(episode, candidate)
            evidence = dict(result.forward_evidence)
            rows.append(
                {
                    "candidate_id": label + str(repetition),
                    "instruction": candidate.prompt_text,
                    "raw_pred_route": evidence["raw_route"],
                    "equal_spaced_route": evidence["equal_spaced_route"],
                    "speed_waypoints": result.plan.speed,
                    "endpoint": list(result.plan.route[-1]),
                    "raw_route_hash": evidence["raw_route_sha256"],
                    "equal_spaced_route_hash": evidence["equal_spaced_route_sha256"],
                    "speed_output_hash": evidence["speed_tensor_sha256"],
                    "generated_language": evidence["generated_language"],
                    "forward_evidence": evidence,
                }
            )
    return rows


def _rmse(left: Sequence[Sequence[float]], right: Sequence[Sequence[float]]) -> float:
    a = np.asarray(left, dtype=np.float64)
    b = np.asarray(right, dtype=np.float64)
    return float(np.sqrt(np.mean(np.square(a - b))))


def _pair_metrics(left: Sequence[Sequence[float]], right: Sequence[Sequence[float]]) -> dict[str, Any]:
    a = np.asarray(left, dtype=np.float64)
    b = np.asarray(right, dtype=np.float64)
    delta = a - b
    separation = np.linalg.norm(delta, axis=1)
    return {
        "route_rmse_m": float(np.sqrt(np.mean(np.square(delta)))),
        "max_separation_m": float(np.max(separation)),
        "lateral_rmse_m": float(np.sqrt(np.mean(np.square(delta[:, 1])))),
        "max_lateral_separation_m": float(np.max(np.abs(delta[:, 1]))),
        "endpoint_separation_m": float(np.linalg.norm(delta[-1])),
    }


def _pair_values(rows_a: Sequence[Mapping[str, Any]], rows_b: Sequence[Mapping[str, Any]], field: str) -> list[float]:
    return [_rmse(left[field], right[field]) for left in rows_a for right in rows_b]


def _repeat_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    a = [row for row in rows if str(row["candidate_id"]).startswith("A")]
    b = [row for row in rows if str(row["candidate_id"]).startswith("B")]
    within_a = [_rmse(left["equal_spaced_route"], right["equal_spaced_route"]) for left, right in combinations(a, 2)]
    within_b = [_rmse(left["equal_spaced_route"], right["equal_spaced_route"]) for left, right in combinations(b, 2)]
    between = _pair_values(a, b, "equal_spaced_route")
    within_speed_a = [_rmse(left["speed_waypoints"], right["speed_waypoints"]) for left, right in combinations(a, 2)]
    within_speed_b = [_rmse(left["speed_waypoints"], right["speed_waypoints"]) for left, right in combinations(b, 2)]
    between_speed = _pair_values(a, b, "speed_waypoints")
    endpoints_a = np.asarray([row["endpoint"] for row in a], dtype=np.float64)
    endpoints_b = np.asarray([row["endpoint"] for row in b], dtype=np.float64)
    within_max = max(within_a + within_b) if within_a or within_b else 0.0
    between_min = min(between)
    scale = max(abs(float(value)) for row in rows for point in row["equal_spaced_route"] for value in point)
    numerical_tolerance = max(1e-6, 10.0 * float(np.finfo(np.float32).eps) * max(1.0, scale))
    margin = between_min - within_max
    ratio = None if within_max == 0.0 else between_min / within_max
    return {
        "within_A_route_rmse_values_m": within_a,
        "within_A_route_rmse_max_m": max(within_a) if within_a else 0.0,
        "within_B_route_rmse_values_m": within_b,
        "within_B_route_rmse_max_m": max(within_b) if within_b else 0.0,
        "between_AB_all_pair_route_rmse_values_m": between,
        "between_AB_route_rmse_min_m": between_min,
        "between_AB_route_rmse_mean_m": float(np.mean(between)),
        "between_vs_within_margin_m": margin,
        "between_vs_within_ratio": ratio,
        "ratio_reason": "DENOMINATOR_ZERO_RATIO_NULL" if ratio is None else None,
        "serialization_numerical_tolerance_m": numerical_tolerance,
        "within_A_speed_rmse_values": within_speed_a,
        "within_A_speed_rmse_max": max(within_speed_a) if within_speed_a else 0.0,
        "within_B_speed_rmse_values": within_speed_b,
        "within_B_speed_rmse_max": max(within_speed_b) if within_speed_b else 0.0,
        "between_AB_speed_rmse_values": between_speed,
        "between_AB_speed_rmse_min": min(between_speed),
        "endpoint_variance_A": np.var(endpoints_a, axis=0).tolist(),
        "endpoint_variance_B": np.var(endpoints_b, axis=0).tolist(),
        "pass": between_min > within_max and margin > numerical_tolerance,
        "pass_rule": "BETWEEN_MIN_GT_WITHIN_MAX_AND_MARGIN_GT_FLOAT32_SCALE_TOLERANCE",
    }


def _own_visual(raw: np.ndarray, rows: Sequence[Mapping[str, Any]], metrics: Mapping[str, Any]) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    path = ARTIFACT_ROOT / "OWN_SCENE_LANGUAGE_SENSITIVITY_REPEAT_PANEL.png"
    fig, axes = plt.subplots(1, 2, figsize=(15, 7), constrained_layout=True)
    axes[0].imshow(cv2.cvtColor(raw, cv2.COLOR_BGR2RGB))
    axes[0].set_title("SAME FROZEN RGB\nLANGUAGE SENSITIVITY DIAGNOSTIC")
    axes[0].axis("off")
    for row in rows:
        route = np.asarray(row["equal_spaced_route"], dtype=np.float64)
        color = "#1769aa" if str(row["candidate_id"]).startswith("A") else "#d1495b"
        axes[1].plot(route[:, 1], route[:, 0], color=color, alpha=0.75, linewidth=2)
    axes[1].set_title(
        "A×3 / B×3 REAL SIMLINGO ROUTES\nNOT A JUNCTION RIGHT/STRAIGHT CLAIM"
    )
    axes[1].set_xlabel("lateral coordinate (m)")
    axes[1].set_ylabel("forward coordinate (m)")
    axes[1].grid(alpha=0.25)
    axes[1].axis("equal")
    fig.suptitle(
        "between min {:.6f} m · within max {:.6f} m".format(
            metrics["between_AB_route_rmse_min_m"],
            max(metrics["within_A_route_rmse_max_m"], metrics["within_B_route_rmse_max_m"]),
        ),
        fontsize=14,
        fontweight="bold",
    )
    fig.savefig(path, dpi=180)
    plt.close(fig)
    return path


def run_own() -> int:
    historical = json.loads(OWN_RECEIPT.read_text(encoding="utf-8"))
    raw, camera = _camera_tensor(OWN_RGB, use_thumbnail=False)
    if hashlib.sha256(raw.tobytes(order="C")).hexdigest() != historical["same_observation"]["rgb_0_sha256"]:
        raise RuntimeError("BLOCKED_REQUIRED_FROZEN_OWN_SCENE_INPUT_NOT_AVAILABLE:RGB_HASH")
    expected_camera_hash = historical["input_conditioning_rows"][0]["before"]["driving_input_fields"]["camera_images"]["bytes_sha256"]
    if _tensor_receipt(camera)["bytes_sha256"] != expected_camera_hash:
        raise RuntimeError("BLOCKED_REQUIRED_FROZEN_OWN_SCENE_INPUT_NOT_AVAILABLE:CAMERA_TENSOR_HASH")

    model, processor, cfg, device = _load_model()
    try:
        intrinsics, extrinsics = _calibration_from_historical_receipt(historical)
        source_fields = historical["input_conditioning_rows"][0]["before"]["driving_input_fields"]
        base = _base_input(
            camera=camera,
            intrinsics=intrinsics,
            extrinsics=extrinsics,
            vehicle_speed=source_fields["vehicle_speed"]["value"],
            target_point=source_fields["target_point"]["value"],
            tokenizer=processor,
            encoder_variant=str(cfg.model.vision_model.variant),
            device=device,
        )
        non_language_fields, non_language_hash = _non_language_receipt(base)
        expected_non_language = historical["non_language_input_hash_a"]
        if non_language_hash != expected_non_language:
            raise RuntimeError(
                "BLOCKED_REQUIRED_FROZEN_OWN_SCENE_INPUT_NOT_AVAILABLE:NON_LANGUAGE_HASH:{}:{}".format(
                    non_language_hash, expected_non_language
                )
            )
        rows = _forward_rows(
            model=model,
            processor=processor,
            cfg=cfg,
            device=device,
            base=base,
            prompts=(("A", INSTRUCTION_A), ("B", INSTRUCTION_B)),
            observation_id=historical["same_observation"]["source_observation_id"],
            frame_id=int(historical["same_observation"]["frame_id"]),
            image_sha=historical["same_observation"]["rgb_0_sha256"],
            repetitions=3,
        )
        metrics = _repeat_metrics(rows)
    finally:
        _close_model(model)

    panel = _own_visual(raw, rows, metrics)
    status = (
        "PASS_OWN_SCENE_LANGUAGE_CONDITIONED_TRAJECTORY_SENSITIVITY_STABLE"
        if metrics["pass"]
        else "BLOCKED_OWN_SCENE_LANGUAGE_CONDITIONED_TRAJECTORY_SENSITIVITY_NOT_STABLE"
    )
    receipt = {
        "schema_version": "driveclarify.own_scene_language_sensitivity_repeat_receipt.v1",
        "created_at": datetime.now().astimezone().isoformat(),
        "status": status,
        "scientific_role": "OWN_SCENE_LANGUAGE_CONDITIONED_TRAJECTORY_SENSITIVITY_POSITIVE_CONTROL",
        "semantic_caveat": "A is lane-change-like and B is lane-keeping/non-straight historical evidence; not a junction RIGHT/STRAIGHT claim.",
        "adapter": "OfficialDreamingCandidateAdapter.v1",
        "checkpoint_sha256": file_sha256(CHECKPOINT),
        "same_frozen_rgb_sha256": historical["same_observation"]["rgb_0_sha256"],
        "saved_png_sha256": file_sha256(OWN_RGB),
        "reconstructed_camera_tensor_hash": _tensor_receipt(camera)["bytes_sha256"],
        "historical_camera_tensor_hash": expected_camera_hash,
        "camera_tensor_exact_match": True,
        "reconstructed_non_language_input_hash": non_language_hash,
        "historical_non_language_input_hash": expected_non_language,
        "non_language_input_exact_match": True,
        "non_language_fields": non_language_fields,
        "instruction_A": INSTRUCTION_A,
        "instruction_B": INSTRUCTION_B,
        "A_repetitions": 3,
        "B_repetitions": 3,
        "rows": rows,
        "metrics": metrics,
        "navigation_A_B": ["OMITTED_OFFICIAL_NO_NAVIGATION_BRANCH"] * 2,
        "target_placeholder_count_A_B": [0, 0],
        "target_embedding_injected_A_B": [False, False],
        "candidate_specific_numeric_target": False,
        "shared_baseline_target_tensor_transported_but_unconsumed": True,
        "simlingo_forward_count": 6,
        "carla_launch_count": 0,
        "planner_advances_added": 0,
        "pid_added": 0,
        "control_mutations": 0,
        "visualization_induced": {
            "dino_forward": 0,
            "simlingo_forward": 0,
            "planner_advance": 0,
            "pid": 0,
            "control_mutation": 0,
        },
        "panel": str(panel.relative_to(ROOT)),
    }
    dump(REPORT_ROOT / "OWN_SCENE_LANGUAGE_SENSITIVITY_REPEAT_RECEIPT.json", receipt)
    dump(ARTIFACT_ROOT / "own_scene_repeat_rows.json", rows)
    report = """# Own-scene language sensitivity repeat report

Status: `{status}`.

The saved raw RGB reconstructs the historical official live camera tensor and the complete non-language `DrivingInput` exactly. A×3 and B×3 are six new real frozen-checkpoint SimLingo forwards. This is a language-sensitivity positive control, not a junction RIGHT/STRAIGHT claim.

- within-A route RMSE max: `{within_a:.9f} m`
- within-B route RMSE max: `{within_b:.9f} m`
- between A/B route RMSE min: `{between:.9f} m`
- between-vs-within margin: `{margin:.9f} m`
- ratio: `{ratio}`
- between speed RMSE min: `{speed:.9f}`
""".format(
        status=status,
        within_a=metrics["within_A_route_rmse_max_m"],
        within_b=metrics["within_B_route_rmse_max_m"],
        between=metrics["between_AB_route_rmse_min_m"],
        margin=metrics["between_vs_within_margin_m"],
        ratio="null (within denominator is zero)" if metrics["between_vs_within_ratio"] is None else metrics["between_vs_within_ratio"],
        speed=metrics["between_AB_speed_rmse_min"],
    )
    (REPORT_ROOT / "OWN_SCENE_LANGUAGE_SENSITIVITY_REPORT.md").write_text(report, encoding="utf-8")
    print(json.dumps({"status": status, "metrics": metrics}, indent=2))
    return 0 if metrics["pass"] else 3


def _white_semantics(bound: Sequence[Any], opportunities: Sequence[ManeuverOpportunity]) -> tuple[list[dict[str, Any]], list[str]]:
    renderer = ConsequenceAwareOfficialDreamingRenderer()
    rows = []
    prompts = []
    for index, (candidate, opportunity) in enumerate(zip(bound, opportunities), start=1):
        current = "TURN_AT_UPCOMING_OPPORTUNITY" if index == 1 else "CONTINUE_TO_LATER_OPPORTUNITY"
        semantic = ConsequenceAwareGroundedSemantic(
            relation="AFTER",
            referring_expression=candidate.referring_expression,
            maneuver_direction=opportunity.maneuver_direction,
            route_order_index=opportunity.route_order_index,
            current_behavior=current,
            persistent_target_id=opportunity.target_id,
            persistent_branch_id=opportunity.branch_id,
        )
        rendered = renderer.render(semantic)
        rows.append(
            {
                "candidate_label": chr(ord("A") + index - 1),
                "candidate_id": candidate.candidate_id,
                "interpretation_id": candidate.interpretation_id,
                "referent_id": candidate.grounded_referent_id,
                "referring_expression": candidate.referring_expression,
                "semantic": asdict(semantic),
                "target": opportunity.to_dict(),
                "rendered_dreaming_instruction": rendered,
                "renderer": renderer.implementation_id,
            }
        )
        prompts.append(rendered)
    return rows, prompts


def _white_visual(raw: np.ndarray, candidates: Sequence[Mapping[str, Any]], plans: Sequence[Mapping[str, Any]], relationship: str, decision: str) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    path = ARTIFACT_ROOT / "WHITE_VAN_CONSEQUENCE_PANEL.png"
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    fig = plt.figure(figsize=(16, 9), constrained_layout=True)
    grid = fig.add_gridspec(2, 2, width_ratios=[1.1, 1.0])
    ax_img = fig.add_subplot(grid[0, 0])
    ax_img.imshow(cv2.cvtColor(raw, cv2.COLOR_BGR2RGB))
    for index, candidate in enumerate(candidates):
        box = candidate["bbox_xyxy"]
        color = "#1769aa" if index == 0 else "#d1495b"
        ax_img.add_patch(plt.Rectangle((box[0], box[1]), box[2]-box[0], box[3]-box[1], fill=False, edgecolor=color, linewidth=2.5))
        ax_img.text(box[0], box[1]-5, "Candidate " + chr(ord("A")+index), color=color, weight="bold")
    ax_img.set_title("Raw: Turn after the white van.\nSAME FROZEN RGB · REAL DINO")
    ax_img.axis("off")
    ax_route = fig.add_subplot(grid[:, 1])
    for index, plan in enumerate(plans):
        route = np.asarray(plan["equal_spaced_route"], dtype=np.float64)
        color = "#1769aa" if index == 0 else "#d1495b"
        ax_route.plot(route[:, 1], route[:, 0], color=color, linewidth=3, label="REAL Plan " + chr(ord("A")+index))
    ax_route.set_title("CURRENT PLAN (no visual exaggeration)")
    ax_route.set_xlabel("lateral coordinate (m)")
    ax_route.set_ylabel("forward coordinate (m)")
    ax_route.grid(alpha=0.25)
    ax_route.axis("equal")
    ax_route.legend()
    ax_text = fig.add_subplot(grid[1, 0])
    ax_text.axis("off")
    lines = [
        "PERSISTENT / FUTURE SEMANTIC TARGETS",
        "A: {} / {}".format(candidates[0]["target"]["junction_id"], candidates[0]["target"]["target_id"]),
        "B: {} / {}".format(candidates[1]["target"]["junction_id"], candidates[1]["target"]["target_id"]),
        "",
        "Candidate relation: " + relationship,
        "Natural decision: " + decision,
    ]
    ax_text.text(0.01, 0.96, "\n".join(lines), va="top", family="monospace", fontsize=10)
    fig.savefig(path, dpi=180)
    plt.close(fig)
    return path


def run_white_van() -> int:
    own = json.loads((REPORT_ROOT / "OWN_SCENE_LANGUAGE_SENSITIVITY_REPEAT_RECEIPT.json").read_text(encoding="utf-8"))
    if own.get("status") != "PASS_OWN_SCENE_LANGUAGE_CONDITIONED_TRAJECTORY_SENSITIVITY_STABLE":
        raise RuntimeError("WHITE_VAN_FORBIDDEN_OWN_SCENE_SENSITIVITY_GATE_NOT_PASS")
    historical = json.loads(WHITE_RECEIPT.read_text(encoding="utf-8"))
    if historical.get("raw_instruction") != "Turn after the white van.":
        raise RuntimeError("WHITE_VAN_HISTORICAL_RAW_INSTRUCTION_CHANGED")
    raw = cv2.imread(str(WHITE_RGB), cv2.IMREAD_UNCHANGED)
    if raw is None or raw.shape != (512, 1024, 3):
        raise RuntimeError("WHITE_VAN_FROZEN_RGB_UNAVAILABLE_OR_SHAPE_CHANGED")
    png_sha = file_sha256(WHITE_RGB)
    if png_sha != EXPECTED_WHITE_PNG:
        raise RuntimeError("WHITE_VAN_FROZEN_PNG_LEDGER_HASH_MISMATCH")
    image_sha = hashlib.sha256(raw.tobytes(order="C")).hexdigest()
    historical_live_image_sha = historical["grounding"]["image_sha256"]

    parser = SemanticSlotParser()
    parsed = parser.parse("Turn after the white van.")
    detector = GroundingDinoVisualGrounder(device="cpu")
    grounding = detector.ground(
        raw,
        parsed.referent_phrase,
        frame_id=int(historical["grounding"]["frame_id"]),
        observation_id=historical["grounding"]["observation_id"],
        captured_monotonic=historical["grounding"]["captured_monotonic"],
    )
    constructed = GroundedCandidatePipeline().construct(parsed, grounding)
    if constructed.effective_k != 2:
        raise RuntimeError("BLOCKED_WHITE_VAN_EFFECTIVE_K_NOT_TWO")
    opportunities = tuple(ManeuverOpportunity(**item) for item in historical["maneuver_opportunities"][:2])
    bound = tuple(
        _bind_candidate(candidate, opportunities[index], order=index + 1)
        for index, candidate in enumerate(constructed.candidates[:2])
    )
    candidate_rows, prompts = _white_semantics(bound, opportunities)
    by_id = {item.local_object_id: item for item in grounding.selected_referents}
    for row in candidate_rows:
        referent = by_id[row["referent_id"]]
        row.update(
            {
                "bbox_xyxy": list(referent.bbox_xyxy),
                "detector_confidence": referent.detector_confidence,
                "linguistic_plausibility": "TRUE",
                "semantic_duplicate": False,
                "grounding_source": "NEW_REAL_GROUNDING_DINO_FORWARD_ON_FROZEN_RGB",
                "target_binding_source": "REPLAY_OF_HASH_BOUND_LIVE_ROUTE_MAP_TOPOLOGY_RECEIPT",
            }
        )
    grounded_receipt = {
        "schema_version": "driveclarify.white_van_grounded_candidates.v1",
        "raw_instruction": "Turn after the white van.",
        "fixture": "E1R1-ASK-PHYS-001",
        "fixture_split": "TRAIN",
        "same_rgb_sha256": image_sha,
        "frozen_png_sha256": png_sha,
        "frozen_png_hash_ledger_match": True,
        "historical_live_pre_save_array_sha256": historical_live_image_sha,
        "saved_png_decoded_array_differs_from_historical_live_array": image_sha != historical_live_image_sha,
        "hash_scope_note": "The exact ledger-bound saved PNG is the available frozen replay input; the historical live in-memory array hash is retained separately and is not falsely asserted equal after PNG persistence.",
        "dino_actually_run": True,
        "dino_forward_count": detector.forward_count,
        "grounding": grounding.to_dict(),
        "raw_k": grounding.raw_grounding_k,
        "effective_k": constructed.effective_k,
        "semantic_duplicate": constructed.semantic_duplicate,
        "grounding_duplicate": constructed.grounding_duplicate,
        "target_duplicate": opportunities[0].target_id == opportunities[1].target_id,
        "historical_binding_receipt": str(WHITE_RECEIPT.relative_to(ROOT)),
        "historical_binding_receipt_sha256": file_sha256(WHITE_RECEIPT),
        "binding_replay_contract": "DINO_REFERENTS_ORDERED_BY_APPARENT_NEAR_TO_FAR_JOIN_ROUTE_OPPORTUNITIES_ORDERED_FIRST_TO_SECOND",
        "runtime_label_join": False,
        "scenario_id_lookup": False,
        "candidates": candidate_rows,
    }
    dump(REPORT_ROOT / "WHITE_VAN_GROUNDED_CANDIDATES.json", grounded_receipt)

    _, camera = _camera_tensor(WHITE_RGB, use_thumbnail=False)
    own_historical = json.loads(OWN_RECEIPT.read_text(encoding="utf-8"))
    intrinsics, extrinsics = _calibration_from_historical_receipt(own_historical)
    model, processor, cfg, device = _load_model()
    try:
        base = _base_input(
            camera=camera,
            intrinsics=intrinsics,
            extrinsics=extrinsics,
            vehicle_speed=[[0.0]],
            target_point=[[0.0, 0.0]],
            tokenizer=processor,
            encoder_variant=str(cfg.model.vision_model.variant),
            device=device,
        )
        non_language_fields, non_language_hash = _non_language_receipt(base)
        plan_rows = _forward_rows(
            model=model,
            processor=processor,
            cfg=cfg,
            device=device,
            base=base,
            prompts=(("A", prompts[0]), ("B", prompts[1])),
            observation_id=historical["grounding"]["observation_id"],
            frame_id=int(historical["grounding"]["frame_id"]),
            image_sha=image_sha,
            repetitions=1,
        )
    finally:
        _close_model(model)
    metrics = _pair_metrics(plan_rows[0]["equal_spaced_route"], plan_rows[1]["equal_spaced_route"])
    metrics["speed_divergence_rmse"] = _rmse(plan_rows[0]["speed_waypoints"], plan_rows[1]["speed_waypoints"])
    metrics["raw_route_rmse_m"] = _rmse(plan_rows[0]["raw_pred_route"], plan_rows[1]["raw_pred_route"])
    first_distance = float(opportunities[0].distance_or_progress)
    route_arcs = []
    for row in plan_rows:
        route = np.asarray(row["equal_spaced_route"], dtype=np.float64)
        route_arcs.append(float(np.linalg.norm(np.diff(route, axis=0), axis=1).sum()))
    # Distinct route-order targets establish future divergence, but the close
    # finite-horizon plans do not by themselves establish either current
    # maneuver equivalence or material divergence.  No trusted maneuver/window
    # classifier exists in this replay, so those predicates remain UNKNOWN.
    immediate = TriState.UNKNOWN
    classification_evidence = ClassificationEvidence(
        raw_k=grounding.raw_grounding_k,
        effective_k=constructed.effective_k,
        semantic_distinct=TriState.TRUE,
        semantic_duplicate=TriState.FALSE,
        target_duplicate=TriState.FALSE,
        current_executable_maneuver_equivalent=TriState.UNKNOWN,
        immediate_material_divergence=immediate,
        future_target_or_topology_divergent=TriState.TRUE,
        future_maneuver_divergent=TriState.TRUE,
        answer_changes_action=TriState.TRUE,
        relevant_decision_window_covered=TriState.UNKNOWN,
    )
    classification = classify_candidate_relationship(classification_evidence)
    decision_evidence = DecisionEvidence(
        shared_current_action_safe=TriState.UNKNOWN,
        hard_rule_pass=TriState.TRUE,
        answer_changes_action=TriState.TRUE,
        query_budget_available=TriState.TRUE,
        no_active_query=TriState.TRUE,
        timing_allows_answer=TriState.UNKNOWN,
        decision_deadline_near=TriState.UNKNOWN,
        recoverability=TriState.UNKNOWN,
        persistent_ambiguity_runtime_supported=False,
    )
    logical_decision = evaluate_decision_contract(classification.relationship, decision_evidence)
    engine_dict = {
        "status": "NOT_EVALUATED_RELATIONSHIP_UNKNOWN",
        "reason": "UnifiedTriadDecisionEngine requires a declared TASK_EQUIVALENT or TASK_CRITICAL relation; supplying TASK_CRITICAL here would manufacture the unresolved current-maneuver and timing predicates.",
        "frozen_logical_contract_used": "evaluate_decision_contract",
    }

    plan_receipt = {
        "schema_version": "driveclarify.white_van_candidate_plan_receipt.v1",
        "status": "PASS_REAL_FROZEN_SIMLINGO_PLANS_CAPTURED",
        "adapter": "OfficialDreamingCandidateAdapter.v1",
        "checkpoint_sha256": file_sha256(CHECKPOINT),
        "same_rgb_sha256": image_sha,
        "frozen_png_sha256": png_sha,
        "frozen_png_hash_ledger_match": True,
        "historical_live_pre_save_array_sha256": historical_live_image_sha,
        "same_non_language_input_A_B": True,
        "non_language_input_sha256": non_language_hash,
        "non_language_fields": non_language_fields,
        "navigation_A_B": ["OMITTED_OFFICIAL_NO_NAVIGATION_BRANCH"] * 2,
        "shared_replay_target_tensor": [[0.0, 0.0]],
        "shared_replay_target_tensor_semantics": "TRANSPORTED_BUT_UNCONSUMED_NEUTRAL_REPLAY_VALUE_NOT_A_CANDIDATE_TARGET",
        "candidate_specific_numeric_target": False,
        "target_placeholder_count_A_B": [0, 0],
        "target_embedding_injected_A_B": [False, False],
        "plans": plan_rows,
        "metrics": metrics,
        "plan_A_source_real_simlingo": True,
        "plan_B_source_real_simlingo": True,
        "simlingo_forward_count": 2,
        "carla_launch_count": 0,
        "planner_advances_added": 0,
        "pid_added": 0,
        "control_mutations": 0,
    }
    dump(REPORT_ROOT / "WHITE_VAN_CANDIDATE_PLAN_RECEIPT.json", plan_receipt)
    consequence = {
        "schema_version": "driveclarify.white_van_consequence_classification.v1",
        "classification_evidence": {
            key: value.value if isinstance(value, TriState) else value
            for key, value in asdict(classification_evidence).items()
        },
        "classification": classification.to_dict(),
        "current_plan_equivalent": None,
        "current_plan_equivalence_unknown_reason": "REAL_FINITE_HORIZON_PLANS_ARE_CLOSE_BUT_NO_TRUSTED_CURRENT_MANEUVER_EQUIVALENCE_CLASSIFIER_IS_AVAILABLE",
        "current_plan_measurement_basis": "Route RMSE and maximum separation are descriptive only and cannot establish maneuver equality or immediate material divergence.",
        "future_target_topology_divergent": True,
        "future_divergence_basis": "distinct hash-bound junction/branch/target identities and route-order indices 1 versus 2",
        "distance_to_first_divergence_m": first_distance,
        "time_to_divergence_s": None,
        "time_to_divergence_unknown_reason": "FROZEN_EGO_SPEED_IS_ZERO_AND_NO_VALID_FUTURE_SPEED_TO_TIME_CALIBRATION",
        "decision_point": "FIRST_ROUTE_ORDERED_RIGHT_TURN_OPPORTUNITY",
        "decision_window_evidence": {
            "first_target_distance_or_progress_m": first_distance,
            "real_plan_arc_lengths_m": route_arcs,
            "historical_same_fixture_query_to_answer_to_replan": historical.get("status"),
            "historical_answer_delay_simulation_seconds": historical.get("answer_delay_simulation_seconds"),
            "coverage": None,
            "basis": "The first target lies slightly beyond both measured plan arcs; historical lifecycle success is offline diagnostic context and does not prove the current decision deadline.",
        },
        "latest_safe_clarification_time_s": None,
        "latest_safe_clarification_unknown_reason": "NO_CALIBRATED_LATEST_SAFE_TIME_ESTIMATOR_IN_CURRENT_RUNTIME",
        "answer_changes_action": True,
        "answer_changes_action_basis": "The answer selects distinct route-order junction/branch/target obligations, changing the future turn location; it does not prove an immediate maneuver change.",
        "recoverability": None,
        "recoverability_unknown_reason": "NO_RELIABLE_RECOVERABILITY_ESTIMATOR_FOR_THIS_FROZEN_REPLAY",
        "logical_decision": logical_decision.to_dict(),
        "existing_m2b_decision_engine": engine_dict,
        "natural_decision": logical_decision.decision,
        "forced_decision_count": 0,
        "persistent_ambiguity_lifecycle_supported": False,
        "persistent_ambiguity_limitation": "ReferentialGroundedRuntime terminates after ACT and has no persistent future-divergent ambiguity obligation state; no manager was added in this stage.",
    }
    dump(REPORT_ROOT / "WHITE_VAN_CONSEQUENCE_CLASSIFICATION.json", consequence)
    panel = _white_visual(raw, candidate_rows, plan_rows, classification.relationship.value, logical_decision.decision)
    report = """# White-van reintegration report

Status: `PASS_WHITE_VAN_REAL_GROUNDING_AND_FROZEN_SIMLINGO_REINTEGRATED_RELATIONSHIP_UNKNOWN`.

The exact TRAIN-only E1R1 representative RGB was replayed through one new real Grounding DINO forward. Two plausible white-van referents were joined by apparent near-to-far order to the two hash-bound live route/map topology opportunities preserved by the historical receipt. Both grounded semantics were rendered without numeric target/navigation injection and run through the same frozen checkpoint with `OfficialDreamingCandidateAdapter.v1`.

- raw/effective K: `{raw_k}/{effective_k}`
- route RMSE / max separation: `{rmse:.6f}/{maximum:.6f} m`
- speed divergence RMSE: `{speed:.6f}`
- relationship: `{relationship}`
- natural decision: `{decision}` (forced=0)

Current-plan evidence and persistent/future target evidence are reported separately. The close real plans do not prove current maneuver equivalence or immediate divergence, and no trusted decision-window classifier is available. Those fields, time-to-divergence, latest-safe-time, and recoverability remain null/UNKNOWN with reasons; the natural decision is therefore UNKNOWN rather than a manufactured ASK.
""".format(
        raw_k=grounding.raw_grounding_k,
        effective_k=constructed.effective_k,
        rmse=metrics["route_rmse_m"],
        maximum=metrics["max_separation_m"],
        speed=metrics["speed_divergence_rmse"],
        relationship=classification.relationship.value,
        decision=logical_decision.decision,
    )
    (REPORT_ROOT / "WHITE_VAN_REINTEGRATION_REPORT.md").write_text(report, encoding="utf-8")
    print(json.dumps({"status": "PASS_WHITE_VAN_REAL_GROUNDING_AND_FROZEN_SIMLINGO_REINTEGRATED_RELATIONSHIP_UNKNOWN", "relationship": classification.relationship.value, "decision": logical_decision.decision, "metrics": metrics, "panel": str(panel)}, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("own", "white-van"))
    args = parser.parse_args()
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    return run_own() if args.phase == "own" else run_white_van()


if __name__ == "__main__":
    raise SystemExit(main())
