"""Exactly-48 CPU-only official candidate forwards for the six R9 fixtures."""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
import random
from pathlib import Path
from types import SimpleNamespace
from typing import Any

os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")

import cv2
import numpy as np
import torch
from hydra.utils import instantiate
from omegaconf import OmegaConf
from PIL import Image
from transformers import AutoProcessor, AutoTokenizer

from driveclarify_candidate_consequence_equivalence.runner import (
    _base_input,
    _calibration_from_historical_receipt,
)
from driveclarify_official_dreaming_adapter import OfficialDreamingCandidateForwardProvider
from driveclarify_paper_mvp_runtime.contracts import RuntimeCandidate
from simlingo_training.utils.internvl2_utils import build_transform, dynamic_preprocess

from .contracts import canonical_sha256
from .fixtures import CANDIDATE_FIXTURE_IDS, PROMPT_A, PROMPT_B


ROOT = Path(__file__).resolve().parents[1]
SIMLINGO_ROOT = Path("/home/buaa/wrh/simlingo")
CHECKPOINT = SIMLINGO_ROOT / "outputs/simlingo/checkpoints/epoch=013.ckpt/pytorch_model.pt"
CHECKPOINT_CONFIG = SIMLINGO_ROOT / "outputs/simlingo/.hydra/config.yaml"
EXPECTED_CHECKPOINT = "ec8943723d266ee9f5f56f45d153a163b22616960bfccb741965ea5daa700d28"
FIXTURE_RECEIPT = ROOT / "artifacts/driveclarify_official_dreaming_adapter_alignment/own_scene_explicit_a1b1_attempt4/GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json"
FIXTURE_RGB = ROOT / "artifacts/driveclarify_official_dreaming_adapter_alignment/own_scene_explicit_a1b1_attempt4/same_observation_rgb_0.png"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _camera_tensor(path: Path, *, use_thumbnail: bool) -> torch.Tensor:
    raw = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if raw is None or raw.shape != (512, 1024, 3):
        raise RuntimeError("R9_STATIC_TRAIN_RGB_INVALID")
    ok, encoded = cv2.imencode(".jpg", raw)
    if not ok:
        raise RuntimeError("R9_STATIC_TRAIN_RGB_JPEG_FAILED")
    camera = cv2.imdecode(encoded, cv2.IMREAD_UNCHANGED)
    rgb = cv2.cvtColor(camera, cv2.COLOR_BGR2RGB)
    rgb = rgb[: int(rgb.shape[0] - (rgb.shape[0] * 4.8) // 16), :, :]
    patches = dynamic_preprocess(
        Image.fromarray(rgb), image_size=448, use_thumbnail=use_thumbnail, max_num=2
    )
    return torch.stack([build_transform(input_size=448)(item) for item in patches]).view(
        1, 1, len(patches), 3, 448, 448
    ).bfloat16()


def _load_cpu_model() -> tuple[Any, Any, Any, torch.device]:
    if sha256_file(CHECKPOINT) != EXPECTED_CHECKPOINT:
        raise RuntimeError("R9_FROZEN_CHECKPOINT_HASH_MISMATCH")
    if torch.cuda.is_available():
        raise RuntimeError("R9_CPU_PROCESS_EXPOSES_CUDA")
    random.seed(42)
    np.random.seed(42)
    torch.manual_seed(42)
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
        {"additional_special_tokens": ["<WAYPOINTS>", "<WAYPOINTS_DIFF>", "<ORG_WAYPOINTS_DIFF>", "<ORG_WAYPOINTS>", "<WAYPOINT_LAST>", "<ROUTE>", "<ROUTE_DIFF>", "<TARGET_POINT>"]}
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
    gc.collect()
    for parameter in model.parameters():
        parameter.requires_grad_(False)
    model.eval()
    device = torch.device("cpu")
    model.to(device)
    return model, tokenizer, cfg, device


def _candidate(fixture_id: str, label: str, prompt: str, input_sha: str) -> RuntimeCandidate:
    candidate_id = f"{fixture_id}-{label}"
    semantic = canonical_sha256([fixture_id, label, prompt])
    if fixture_id == "NOT_DISTINCT_SEMANTIC_EQUIVALENCE":
        semantic = canonical_sha256([fixture_id, "same-obligation"])
    return RuntimeCandidate(
        candidate_id=candidate_id,
        interpretation_id="interpretation-" + semantic[:20],
        prompt_text=prompt,
        visual_track_id="r9-static-train-rgb",
        visual_anchor_digest=sha256_file(FIXTURE_RGB),
        candidate_semantic_digest=semantic,
        candidate_input_digest=input_sha,
        source_observation_id="R9-STATIC-TRAIN-OBSERVATION",
        source_frame_id=410,
        generator_rule="R9_FIXED_CANDIDATE_CERTIFICATION_FIXTURE",
    )


def run(output: Path) -> None:
    receipt = json.loads(FIXTURE_RECEIPT.read_text(encoding="utf-8"))
    if not (
        receipt.get("train_only") is True
        and receipt.get("dev_attempt_count") == 0
        and receipt.get("test_attempt_count") == 0
        and receipt.get("test_consumed") is False
    ):
        raise RuntimeError("R9_STATIC_FIXTURE_NOT_TRAIN_ONLY")
    model, tokenizer, cfg, device = _load_cpu_model()
    try:
        camera = _camera_tensor(FIXTURE_RGB, use_thumbnail=False)
        intrinsics, extrinsics = _calibration_from_historical_receipt(receipt)
        fields = receipt["input_conditioning_rows"][0]["before"]["driving_input_fields"]
        base = _base_input(
            camera=camera,
            intrinsics=intrinsics,
            extrinsics=extrinsics,
            vehicle_speed=fields["vehicle_speed"]["value"],
            target_point=fields["target_point"]["value"],
            tokenizer=tokenizer,
            encoder_variant=str(cfg.model.vision_model.variant),
            device=device,
        )
        agent = SimpleNamespace(tokenizer=tokenizer, cfg=cfg, device=device, model=model, DrivingInput=base._asdict())
        provider = OfficialDreamingCandidateForwardProvider(agent)
        provider.begin_event()
        episode = SimpleNamespace(
            ego_state=SimpleNamespace(speed_mps=float(fields["vehicle_speed"]["value"][0][0])),
            vision_observation=SimpleNamespace(observation_id="R9-STATIC-TRAIN-OBSERVATION", frame_id=410),
        )
        records = []
        for fixture_id in CANDIDATE_FIXTURE_IDS:
            prompts = [PROMPT_A, PROMPT_B]
            if fixture_id == "NOT_DISTINCT_SEMANTIC_EQUIVALENCE":
                prompts = [PROMPT_A, PROMPT_A]
            bundle = {
                "fixture_id": fixture_id,
                "observation_id": episode.vision_observation.observation_id,
                "frame_id": episode.vision_observation.frame_id,
                "rgb_sha256": sha256_file(FIXTURE_RGB),
                "checkpoint_sha256": EXPECTED_CHECKPOINT,
                "prompts": prompts,
                "device": "CPU",
            }
            input_sha = canonical_sha256(bundle)
            candidates = [
                _candidate(fixture_id, "A", prompts[0], input_sha),
                _candidate(fixture_id, "B", prompts[1], input_sha),
            ]
            for repeat in range(1, 5):
                for order, candidate in enumerate(candidates):
                    before = provider.event_forward_count
                    result = provider(episode, candidate)
                    if provider.event_forward_count != before + 1:
                        raise RuntimeError("R9_OFFICIAL_FORWARD_NOT_COUNTED_EXACTLY_ONCE")
                    evidence = dict(result.forward_evidence)
                    record = {
                        "fixture_id": fixture_id,
                        "repeat_index": repeat,
                        "candidate_order_index": order,
                        "candidate_id": candidate.candidate_id,
                        "interpretation_id": candidate.interpretation_id,
                        "semantic_digest": candidate.candidate_semantic_digest,
                        "input_bundle_sha256": input_sha,
                        "canonical_input": bundle,
                        "provider_class": type(provider).__name__,
                        "execution_device": str(device).upper(),
                        "route": [list(point) for point in result.plan.route],
                        "speed": [list(point) for point in result.plan.speed],
                        "forward_evidence": evidence,
                    }
                    record["record_sha256"] = canonical_sha256(record)
                    records.append(record)
                    print(f"R9_CPU_FORWARD {provider.event_forward_count}/48 {fixture_id} repeat={repeat} candidate={order}", flush=True)
        if provider.event_forward_count != 48 or len(records) != 48:
            raise RuntimeError("R9_EXACT_48_FORWARD_CONTRACT_FAILED")
        payload = {
            "schema_version": "driveclarify.r9.official_candidate_cpu_forward_log.v1",
            "provider_class": "OfficialDreamingCandidateForwardProvider",
            "execution_device": "CPU",
            "checkpoint": {"path": str(CHECKPOINT), "sha256": EXPECTED_CHECKPOINT},
            "fixture_source": {"receipt": str(FIXTURE_RECEIPT.relative_to(ROOT)), "rgb": str(FIXTURE_RGB.relative_to(ROOT)), "train_only": True},
            "authorized_forward_count": 48,
            "actual_forward_count": 48,
            "gpu_or_cuda_forward_count": 0,
            "visualization_forward_count": 0,
            "retry_forward_count": 0,
            "records": records,
        }
        payload["log_sha256"] = canonical_sha256(payload)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    finally:
        model.to("cpu")
        del model
        gc.collect()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
