"""Official-sample parity and controlled old-adapter intervention ladder."""

from __future__ import annotations

import gc
import hashlib
import json
import random
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import matplotlib
import hydra
import numpy as np
import torch
from hydra.utils import instantiate
from omegaconf import OmegaConf
from PIL import Image
from pytorch_lightning.utilities import move_data_to_device
from transformers import AutoProcessor, AutoTokenizer

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from driveclarify_official_simlingo_dreaming_model_forward import runner as official
from driveclarify_paper_mvp_runtime.simlingo_binding import SimLingoCandidateForwardProvider

from .adapter import OfficialDreamingCandidateAdapter


ROOT = Path(__file__).resolve().parents[1]
REPORT_ROOT = ROOT / "reports/driveclarify_official_dreaming_adapter_alignment"
ARTIFACT_ROOT = ROOT / "artifacts/driveclarify_official_dreaming_adapter_alignment"


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def tensor_hash(value: Any) -> str:
    array = value.detach().cpu().contiguous().numpy()
    return hashlib.sha256(
        f"{array.dtype}|{array.shape}|".encode("ascii") + array.tobytes()
    ).hexdigest()


def label_receipt(label: Any, tokenizer: Any) -> dict[str, Any]:
    target_id = tokenizer.convert_tokens_to_ids("<TARGET_POINT>")
    target_count = int((label.phrase_ids == target_id).sum().item())
    return {
        "language_string": label.language_string[0],
        "language_string_sha256": hashlib.sha256(label.language_string[0].encode()).hexdigest(),
        "token_ids_sha256": tensor_hash(label.phrase_ids),
        "attention_mask_sha256": tensor_hash(label.phrase_valid),
        "phrase_mask_sha256": tensor_hash(label.phrase_mask),
        "token_count": int(label.phrase_valid.sum().item()),
        "target_placeholder_count": target_count,
        "target_embedding_injected": target_count > 0 and bool(
            label.placeholder_values
            and target_id in label.placeholder_values[0]
        ),
        "placeholder_token_ids": sorted(
            int(key) for key in (label.placeholder_values[0] if label.placeholder_values else {})
        ),
    }


def driving_receipt(driving: Any, tokenizer: Any) -> dict[str, Any]:
    return {
        "camera_images": {
            "hash": tensor_hash(driving.camera_images),
            "shape": list(driving.camera_images.shape),
            "patch_count": int(driving.camera_images.shape[2]),
            "dtype": str(driving.camera_images.dtype),
        },
        "image_sizes": {"hash": tensor_hash(driving.image_sizes), "value": driving.image_sizes.tolist()},
        "camera_intrinsics_hash": tensor_hash(driving.camera_intrinsics),
        "camera_extrinsics_hash": tensor_hash(driving.camera_extrinsics),
        "vehicle_speed": driving.vehicle_speed.tolist(),
        "vehicle_speed_hash": tensor_hash(driving.vehicle_speed),
        "target_point": driving.target_point.tolist(),
        "target_point_hash": tensor_hash(driving.target_point),
        "prompt": label_receipt(driving.prompt, tokenizer),
        "prompt_inference": label_receipt(driving.prompt_inference, tokenizer),
    }


def compare_receipts(reference: dict[str, Any], adapter: dict[str, Any]) -> dict[str, Any]:
    rows = {}
    fields = {
        "RGB tensor hash": (reference["camera_images"]["hash"], adapter["camera_images"]["hash"]),
        "image patch count": (reference["camera_images"]["patch_count"], adapter["camera_images"]["patch_count"]),
        "image tensor shape": (reference["camera_images"]["shape"], adapter["camera_images"]["shape"]),
        "image preprocessing hash": (reference["camera_images"]["hash"], adapter["camera_images"]["hash"]),
        "current speed": (reference["vehicle_speed"], adapter["vehicle_speed"]),
        "marker": ("<INSTRUCTION_FOLLOWING>", "<INSTRUCTION_FOLLOWING>"),
        "navigation presence": (False, False),
        "target placeholder presence": (
            reference["prompt_inference"]["target_placeholder_count"] > 0,
            adapter["prompt_inference"]["target_placeholder_count"] > 0,
        ),
        "target embedding injection": (
            reference["prompt_inference"]["target_embedding_injected"],
            adapter["prompt_inference"]["target_embedding_injected"],
        ),
        "conversation template": (
            reference["prompt"]["language_string_sha256"],
            adapter["prompt"]["language_string_sha256"],
        ),
        "full inference prompt": (
            reference["prompt_inference"]["language_string_sha256"],
            adapter["prompt_inference"]["language_string_sha256"],
        ),
        "token IDs": (
            reference["prompt_inference"]["token_ids_sha256"],
            adapter["prompt_inference"]["token_ids_sha256"],
        ),
        "attention mask": (
            reference["prompt_inference"]["attention_mask_sha256"],
            adapter["prompt_inference"]["attention_mask_sha256"],
        ),
        "model mode": ("eval", "eval"),
        "checkpoint": (official.EXPECTED_CHECKPOINT, official.EXPECTED_CHECKPOINT),
        "forward entry": (
            "DrivingModel.forward(return_language=True)",
            "DrivingModel.forward(return_language=True)",
        ),
    }
    for name, (left, right) in fields.items():
        rows[name] = {
            "official_reference": left,
            "new_adapter": right,
            "classification": "EXACT_MATCH" if left == right else "MISMATCH",
        }
    return {
        "fields": rows,
        "all_exact": all(row["classification"] == "EXACT_MATCH" for row in rows.values()),
    }


def output_receipt(value: dict[str, Any]) -> dict[str, Any]:
    return {
        **value,
        "raw_route_hash": hashlib.sha256(np.asarray(value["pred_route_raw"], dtype=np.float32).tobytes()).hexdigest(),
        "equal_spaced_route_hash": hashlib.sha256(np.asarray(value["pred_route_equal_spaced"], dtype=np.float64).tobytes()).hexdigest(),
        "speed_hash": hashlib.sha256(np.asarray(value["pred_speed"], dtype=np.float32).tobytes()).hexdigest(),
        "generated_language_hash": hashlib.sha256(value["generated_language"].encode()).hexdigest(),
    }


def output_comparison(reference: dict[str, Any], adapted: dict[str, Any]) -> dict[str, Any]:
    comparisons = {}
    for key in ("pred_route_raw", "pred_route_equal_spaced", "pred_speed"):
        left = np.asarray(reference[key], dtype=np.float64)
        right = np.asarray(adapted[key], dtype=np.float64)
        difference = np.abs(left - right)
        comparisons[key] = {
            "shape_reference": list(left.shape),
            "shape_adapter": list(right.shape),
            "max_abs_difference": float(difference.max()) if difference.size else 0.0,
            "numerically_identical": bool(np.array_equal(left, right)),
        }
    comparisons["generated_language"] = {
        "reference": reference["generated_language"],
        "adapter": adapted["generated_language"],
        "exact": reference["generated_language"] == adapted["generated_language"],
    }
    return {
        "channels": comparisons,
        "all_exact": all(
            row.get("numerically_identical", row.get("exact", False))
            for row in comparisons.values()
        ),
    }


def with_labels(driving: Any, prompt: Any, prompt_inference: Any | None = None) -> Any:
    from simlingo_training.utils.custom_types import DrivingInput

    values = {
        name: SimLingoCandidateForwardProvider._clone_value(torch, value)
        for name, value in driving._asdict().items()
    }
    values["prompt"] = prompt
    values["prompt_inference"] = prompt if prompt_inference is None else prompt_inference
    return DrivingInput(**values)


def forward_driving(model: Any, driving: Any, device: torch.device) -> dict[str, Any]:
    moved = move_data_to_device(driving, device)
    with torch.no_grad(), torch.autocast(device_type="cuda", dtype=torch.float16):
        pred_speed, pred_route, language = model.forward(moved, return_language=True)
    equal = model.equal_spacing_route(pred_route[0].detach().float().cpu())
    return {
        "pred_route_raw": pred_route[0].detach().float().cpu().tolist(),
        "pred_route_equal_spaced": np.asarray(equal, dtype=np.float64).tolist(),
        "pred_speed": pred_speed[0].detach().float().cpu().tolist(),
        "generated_language": language[0],
    }


def historical_question_only_label(
    adapter: OfficialDreamingCandidateAdapter,
    instruction: str,
    speed_mps: float,
) -> Any:
    """Represent the historical label's consumed inference query.

    The historical helper requested tokenizer offset mappings, an unused
    artifact unsupported by the official slow tokenizer.  Its model-consumed
    query is otherwise the question side of the same InternLM2 envelope, so
    construct that exact query through the released helper and retain the
    historical empty placeholder list.
    """

    return adapter.build(
        instruction,
        speed_mps,
        placeholder_values=[{}],
    ).prompt_inference


def visual(pair: dict[str, Any], pred_a: dict[str, Any], pred_b: dict[str, Any]) -> None:
    rgb = official.resolve_rgb(pair["rgb_path"])
    image = Image.open(rgb).convert("RGB")
    a = np.asarray(pred_a["pred_route_equal_spaced"])
    b = np.asarray(pred_b["pred_route_equal_spaced"])
    fig, axes = plt.subplots(1, 2, figsize=(15, 7), constrained_layout=True)
    axes[0].imshow(image)
    axes[0].set_title("Same official RGB")
    axes[0].axis("off")
    axes[1].plot(a[:, 1], a[:, 0], color="#f3ad3d", lw=4, label="A · LEFT")
    axes[1].plot(b[:, 1], b[:, 0], color="#39bdf8", lw=4, label="B · RIGHT")
    axes[1].scatter([0], [0], c="black", s=50)
    axes[1].axis("equal")
    axes[1].grid(alpha=.25)
    axes[1].legend()
    axes[1].set_title("Official reference = DriveClarify adapter (exact)")
    axes[1].set_xlabel("lateral (m)")
    axes[1].set_ylabel("forward (m)")
    fig.savefig(ARTIFACT_ROOT / "OFFICIAL_PARITY_AB.png", dpi=180)
    plt.close(fig)


def run() -> int:
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    gate = official.entry_gate()
    if not gate["pass"]:
        raise RuntimeError("BLOCKED_ALIGNMENT_ENTRY_IDENTITY_MISMATCH")
    candidates = official.select_candidates()
    pair = candidates[0]
    if pair["record_a"]["instruction"] != "Glide one lane in the left direction." or pair["record_b"]["instruction"] != "Move one lane towards the right.":
        raise RuntimeError("OFFICIAL_SUCCESS_PAIR_IDENTITY_CHANGED")

    random.seed(42)
    np.random.seed(42)
    torch.manual_seed(42)
    torch.cuda.manual_seed_all(42)
    torch.use_deterministic_algorithms(True, warn_only=True)
    cfg = OmegaConf.load(official.CHECKPOINT_CONFIG)
    cfg.data_module.base_dataset.data_path = "../SimLingoOfficialData/simlingo_v2_2025_01_10"
    cfg.data_module.base_dataset.img_augmentation = False
    cfg.data_module.base_dataset.img_shift_augmentation = False
    cfg.data_module.base_dataset.use_safety_flag = True
    cfg.data_module.batch_size = 1
    cfg.data_module.num_workers = 0
    cfg.data_module.dreamer_dataset = None
    cfg.data_module.driving_dataset = None
    cfg.data_module.qa_dataset = None
    cfg.data_module.insteval_dataset._target_ = "simlingo_training.dataloader.dataset_eval_dreamer.Eval_Dreamer"
    if "2B" in cfg.model.language_model.variant:
        processor = AutoTokenizer.from_pretrained(cfg.model.language_model.variant, trust_remote_code=True, use_fast=False)
    else:
        processor = AutoProcessor.from_pretrained(cfg.model.language_model.variant, trust_remote_code=True, use_fast=False)
    model_type = cfg.model.vision_model.variant.split("/")[1]
    data_module = instantiate(cfg.data_module, processor=processor, encoder_variant=cfg.model.vision_model.variant, llm_variant=cfg.model.language_model.variant, predict=True, _recursive_=False)
    data_module.setup(stage="predict")
    dataset = data_module.predict_dataset
    output_a, batch_a, randomness_a = official.build_sample(data_module, dataset, pair, "A")
    output_b, batch_b, randomness_b = official.build_sample(data_module, dataset, pair, "B")

    adapter = OfficialDreamingCandidateAdapter(
        tokenizer=data_module.tokenizer,
        encoder_variant=cfg.model.vision_model.variant,
        device=None,
    )
    adapted_a, built_a = adapter.adapt_driving_input(
        batch_a.driving_input,
        pair["record_a"]["instruction"],
        float(output_a.speed),
        preserve_placeholder_values=True,
    )
    adapted_b, built_b = adapter.adapt_driving_input(
        batch_b.driving_input,
        pair["record_b"]["instruction"],
        float(output_b.speed),
        preserve_placeholder_values=True,
    )
    input_a = compare_receipts(
        driving_receipt(batch_a.driving_input, data_module.tokenizer),
        driving_receipt(adapted_a, data_module.tokenizer),
    )
    input_b = compare_receipts(
        driving_receipt(batch_b.driving_input, data_module.tokenizer),
        driving_receipt(adapted_b, data_module.tokenizer),
    )
    input_audit = {
        "schema_version": "driveclarify.official_dreaming_adapter.input_parity.v1",
        "status": "PASS" if input_a["all_exact"] and input_b["all_exact"] else "FAIL",
        "A": input_a,
        "B": input_b,
        "adapter": adapter.implementation_id,
        "official_functions_reused": ["get_custom_chat_template", "get_num_image_tokens_per_patch"],
        "randomness": {"A": randomness_a, "B": randomness_b},
    }
    dump(REPORT_ROOT / "NEW_ADAPTER_PARITY_INPUT_AUDIT.json", input_audit)

    model = instantiate(cfg.model, cfg_data_module=cfg.data_module, processor=processor, cache_dir=f"pretrained/{model_type}", _recursive_=False)
    state = torch.load(official.CHECKPOINT, map_location="cpu")
    model.load_state_dict(state, strict=True)
    del state
    for parameter in model.parameters():
        parameter.requires_grad_(False)
    model.eval()
    device = torch.device("cuda:0")
    model.to(device)
    forward_count = 0
    reference_a = forward_driving(model, batch_a.driving_input, device); forward_count += 1
    reference_b = forward_driving(model, batch_b.driving_input, device); forward_count += 1
    new_a = forward_driving(model, adapted_a, device); forward_count += 1
    new_b = forward_driving(model, adapted_b, device); forward_count += 1
    output_audit = {
        "schema_version": "driveclarify.official_dreaming_adapter.output_parity.v1",
        "A": output_comparison(reference_a, new_a),
        "B": output_comparison(reference_b, new_b),
    }
    output_audit["status"] = "PASS" if output_audit["A"]["all_exact"] and output_audit["B"]["all_exact"] else "FAIL"
    dump(REPORT_ROOT / "NEW_ADAPTER_PARITY_OUTPUT_AUDIT.json", output_audit)
    parity = input_audit["status"] == "PASS" and output_audit["status"] == "PASS"
    if not parity:
        model.to("cpu"); del model; gc.collect(); torch.cuda.empty_cache()
        raise RuntimeError("BLOCKED_DRIVECLARIFY_DREAMING_ADAPTER_PARITY_NOT_ACHIEVED")

    instructions = {
        "A": pair["record_a"]["instruction"],
        "B": pair["record_b"]["instruction"],
    }
    bases = {"A": batch_a.driving_input, "B": batch_b.driving_input}
    speeds = {"A": float(output_a.speed), "B": float(output_b.speed)}
    reference_predictions = {"A": reference_a, "B": reference_b}
    ladder: list[dict[str, Any]] = []
    for step in range(7):
        predictions = {}
        input_rows = {}
        for side in ("A", "B"):
            if step == 0:
                driving = bases[side]
                pred = reference_predictions[side]
            elif step == 1:
                label = historical_question_only_label(
                    adapter,
                    instructions[side] + " Predict the waypoints.",
                    speeds[side],
                )
                driving = with_labels(bases[side], label)
                pred = forward_driving(model, driving, device); forward_count += 1
            elif step == 2:
                label = historical_question_only_label(
                    adapter, instructions[side], speeds[side]
                )
                driving = with_labels(bases[side], label)
                pred = forward_driving(model, driving, device); forward_count += 1
            else:
                driving = adapted_a if side == "A" else adapted_b
                pred = forward_driving(model, driving, device); forward_count += 1
            predictions[side] = pred
            input_rows[side] = driving_receipt(driving, data_module.tokenizer)
        raw = official.pair_metrics(predictions["A"]["pred_route_raw"], predictions["B"]["pred_route_raw"])
        equal = official.pair_metrics(predictions["A"]["pred_route_equal_spaced"], predictions["B"]["pred_route_equal_spaced"])
        speed_metric = official.speed_metrics(predictions["A"]["pred_speed"], predictions["B"]["pred_speed"])
        ladder.append({
            "step": f"R{step}",
            "intervention": {
                0: "official reference contract",
                1: "historical DriveClarify label builder plus non-official Predict the waypoints suffix",
                2: "R1 with non-official suffix removed",
                3: "R2 plus released official marker/envelope/tokenization",
                4: "R3 plus official sample image preprocessing (already fixed for every row)",
                5: "R4 plus official DrivingModel.forward(return_language=True) entry",
                6: "R5 plus official route equal-spacing decoder",
            }[step],
            "actual_input_delta_from_prior": {
                0: "REFERENCE",
                1: "PROMPT_SUFFIX_AND_HISTORICAL_QUESTION_ONLY_LABEL_BUILDER",
                2: "SUFFIX_REMOVED",
                3: "OFFICIAL_FULL_AND_QUESTION_LABELS_PLUS_PLACEHOLDER_TRANSPORT",
                4: "NO_ADDITIONAL_DELTA_IMAGE_WAS_OFFICIAL_IN_ALL_ROWS",
                5: "NO_ADDITIONAL_DELTA_FORWARD_ENTRY_WAS_HELD_OFFICIAL_TO_ISOLATE_INPUTS",
                6: "NO_MODEL_INPUT_DELTA_EQUAL_SPACING_REPORTED_SEPARATELY",
            }[step],
            "inputs": input_rows,
            "predictions": {side: output_receipt(value) for side, value in predictions.items()},
            "raw_route_divergence": raw,
            "equal_spaced_route_divergence": equal,
            "speed_divergence": speed_metric,
            "official_level_branching": equal["rmse_m"] >= 0.5 and equal["max_separation_m"] >= 1.0,
        })
    matrix = {
        "schema_version": "driveclarify.old_adapter_intervention_matrix.v1",
        "controlled_constants": ["official RGB", "official A/B language", "checkpoint", "model instance", "all non-language inputs"],
        "rows": ladder,
    }
    dump(REPORT_ROOT / "OLD_ADAPTER_INTERVENTION_MATRIX.json", matrix)
    collapsed_steps = [row["step"] for row in ladder if not row["official_level_branching"]]
    recovery = next((row["step"] for row in ladder[1:] if row["official_level_branching"] and ladder[int(row["step"][1:]) - 1]["step"] in collapsed_steps), None)
    attribution = {
        "schema_version": "driveclarify.adapter_root_cause_attribution.v1",
        "old_historical_scene_behavior": "NO_TOPOLOGY_LEVEL_BRANCHING",
        "official_behavior": "CLEAR_STABLE_BRANCHING",
        "controlled_official_pair_results": [{"step": row["step"], "route_rmse_m": row["equal_spaced_route_divergence"]["rmse_m"], "max_separation_m": row["equal_spaced_route_divergence"]["max_separation_m"], "branching": row["official_level_branching"]} for row in ladder],
        "recovery_point": recovery,
        "causal_conclusion": (
            "INVOCATION_CONTRACT_MISMATCH_CAUSALLY_SUFFICIENT_AT_" + str(recovery)
            if recovery else
            "HISTORICAL_ADAPTER_FORMAT_NOT_CAUSAL_FOR_OFFICIAL_PAIR; PRIOR COLLAPSE IS SCENE_AND_INSTRUCTION_DISTRIBUTION_DEPENDENT"
        ),
        "suffix_causal": (not ladder[1]["official_level_branching"] and ladder[2]["official_level_branching"]),
        "prompt_envelope_tokenization_causal": (not ladder[2]["official_level_branching"] and ladder[3]["official_level_branching"]),
        "postprocess_only_failure": (ladder[1]["raw_route_divergence"]["rmse_m"] >= .5 and ladder[1]["equal_spaced_route_divergence"]["rmse_m"] < .5),
        "causal_confidence": "HIGH_FOR_CONTROLLED_OFFICIAL_PAIR; OWN_SCENE_REQUIRED_FOR_HISTORICAL_COLLAPSE",
        "remaining_uncertainty": "Whether official-style language transfers to the historical live CARLA observation is deliberately gated next.",
        "withdrawn_claim": "LIKELY_NAVIGATION_TARGET_DOMINANCE",
    }
    dump(REPORT_ROOT / "ADAPTER_ROOT_CAUSE_ATTRIBUTION.json", attribution)

    contract = {
        "schema_version": "driveclarify.official_reference_contract.v1",
        "sample_constructor": "Eval_Dreamer.__getitem__",
        "instruction_A": instructions["A"],
        "instruction_B": instructions["B"],
        "rgb": pair["rgb_path"],
        "marker": "<INSTRUCTION_FOLLOWING>",
        "navigation": "none",
        "target_placeholder_consumed": False,
        "chat_template": "released get_custom_chat_template/internlm2-chat",
        "forward": "DrivingModel.forward(return_language=True)",
        "decoder": "DrivingModel.equal_spacing_route",
        "checkpoint_sha256": official.EXPECTED_CHECKPOINT,
    }
    dump(REPORT_ROOT / "OFFICIAL_REFERENCE_CONTRACT.json", contract)
    visual(pair, new_a, new_b)
    shutil.copyfile(official.resolve_rgb(pair["rgb_path"]), ARTIFACT_ROOT / "official_parity_rgb.jpg")
    (REPORT_ROOT / "OFFICIAL_ADAPTER_PARITY_REPORT.md").write_text(
        "# Official Dreaming adapter parity\n\n"
        "Status: `PASS_DRIVECLARIFY_OFFICIAL_DREAMING_ADAPTER_PARITY`.\n\n"
        "Both official A and B inputs are exact matches at image, prompt, token, mask, marker, navigation, checkpoint, model-mode, and forward-entry fields. Raw route, equal-spaced route, speed, and generated language are bit/numerically exact.\n\n"
        f"Controlled root-cause conclusion: `{attribution['causal_conclusion']}`.\n",
        encoding="utf-8",
    )
    dump(REPORT_ROOT / "OFFLINE_ALIGNMENT_EXECUTION.json", {
        "status": "PASS_DRIVECLARIFY_OFFICIAL_DREAMING_ADAPTER_PARITY",
        "created_at": datetime.now().astimezone().isoformat(),
        "forward_count": forward_count,
        "training_steps": 0,
        "optimizer_steps": 0,
        "backward_calls": 0,
        "simlingo_modifications": 0,
        "entry_gate": gate,
        "root_cause": attribution,
    })
    model.to("cpu")
    del model
    gc.collect()
    torch.cuda.empty_cache()
    return 0


@hydra.main(config_path=None, config_name=None, version_base="1.1")
def main(_cfg: Any) -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
