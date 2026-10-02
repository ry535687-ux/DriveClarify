"""Controlled RTX 4070 Ti official forward validation; never starts CARLA."""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
import os
import platform
import random
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping, Sequence

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

import cv2
import numpy as np
import torch
from hydra.utils import instantiate
from omegaconf import OmegaConf
from PIL import Image
from transformers import AutoProcessor, AutoTokenizer

from driveclarify_official_dreaming_adapter import (
    OfficialDreamingCandidateAdapter,
    OfficialDreamingCandidateForwardProvider,
)
from driveclarify_paper_mvp_runtime.contracts import RuntimeCandidate
from driveclarify_protocol_cpu_v1.contracts import canonical_sha256
from simlingo_training.utils.custom_types import DrivingInput
from simlingo_training.utils.internvl2_utils import build_transform, dynamic_preprocess


ROOT = Path(__file__).resolve().parents[1]
SIMLINGO_ROOT = Path("/home/buaa/wrh/simlingo")
CHECKPOINT = SIMLINGO_ROOT / "outputs/simlingo/checkpoints/epoch=013.ckpt/pytorch_model.pt"
CHECKPOINT_CONFIG = SIMLINGO_ROOT / "outputs/simlingo/.hydra/config.yaml"
FIXTURE_ROOT = ROOT / "artifacts/driveclarify_official_dreaming_adapter_alignment/own_scene_explicit_a1b1_attempt4"
FIXTURE_RECEIPT = FIXTURE_ROOT / "GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json"
FIXTURE_RGB = FIXTURE_ROOT / "same_observation_rgb_0.png"
PROVIDER_SOURCE = ROOT / "driveclarify_official_dreaming_adapter/adapter.py"
EXPECTED = {
    "checkpoint_sha256": "ec8943723d266ee9f5f56f45d153a163b22616960bfccb741965ea5daa700d28",
    "rgb_sha256": "49131c87b5ce8e7bec52cb377f74f0379abd7df453447569c56c019104f98c58",
    "receipt_sha256": "a01f83341857e04f22682318d3f8e556c3ca9da84b9427b9c694b21dbdd4e5f8",
    "provider_sha256": "aa1ab3d0b24ae9c07b39bb25de2fc669a1106bab9a1e9e8d2b8fed4cfe9cc78e",
    "simlingo_head": "743b243afd6cf5ff51b9fa1f8cac86f22d569684",
    "simlingo_diff_sha256": "dad60227cada5a17ba336881e583480e83eb272331e72be3c146ec96abe2d058",
    "gpu_name": "NVIDIA GeForce RTX 4070 Ti",
    "gpu_uuid": "GPU-f59ba35f-9570-f5d8-b194-1e67e6854159",
    "compute_capability": "8.9",
}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _dump(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _command(args: Sequence[str], cwd: Path | None = None) -> str:
    return subprocess.check_output(args, cwd=cwd, text=True).strip()


def _simlingo_diff_sha256() -> str:
    blob = subprocess.check_output(["git", "diff", "--binary", "HEAD"], cwd=SIMLINGO_ROOT)
    return hashlib.sha256(blob).hexdigest()


def _gpu_row() -> dict[str, str]:
    row = _command(
        [
            "nvidia-smi",
            "--query-gpu=name,uuid,driver_version,memory.total,compute_cap",
            "--format=csv,noheader,nounits",
            "-i",
            "0",
        ]
    ).split(", ")
    if len(row) != 5:
        raise RuntimeError("GPU_IDENTITY_QUERY_INVALID")
    return {
        "name": row[0],
        "uuid": row[1],
        "driver_version": row[2],
        "memory_total_mib": row[3],
        "compute_capability": row[4],
    }


def _execution_identity() -> dict[str, Any]:
    identity = {
        "gpu": _gpu_row(),
        "python_executable": str(Path(sys.executable).resolve()),
        "python_version": platform.python_version(),
        "torch_version": torch.__version__,
        "torch_cuda_version": torch.version.cuda,
        "cudnn_version": torch.backends.cudnn.version(),
        "cuda_available": torch.cuda.is_available(),
        "simlingo_head": _command(["git", "rev-parse", "HEAD"], cwd=SIMLINGO_ROOT),
        "simlingo_diff_sha256": _simlingo_diff_sha256(),
        "checkpoint_sha256": _sha256_file(CHECKPOINT),
        "provider_sha256": _sha256_file(PROVIDER_SOURCE),
        "rgb_sha256": _sha256_file(FIXTURE_RGB),
        "receipt_sha256": _sha256_file(FIXTURE_RECEIPT),
        "provider_path": "driveclarify_official_dreaming_adapter.adapter.OfficialDreamingCandidateForwardProvider",
        "preprocessing_path": "released JPEG roundtrip/crop/dynamic_preprocess(use_thumbnail=False,max_num=2)",
        "precision_path": "BF16 camera; unchanged FP32 checkpoint parameters; provider-owned CUDA FP16 autocast",
    }
    identity["execution_identity_sha256"] = canonical_sha256(identity)
    return identity


def _identity_checks(identity: Mapping[str, Any]) -> dict[str, bool]:
    gpu = identity["gpu"]
    return {
        "canonical_rtx_4070_ti": gpu["name"] == EXPECTED["gpu_name"],
        "canonical_gpu_uuid": gpu["uuid"] == EXPECTED["gpu_uuid"],
        "compute_capability_8_9": gpu["compute_capability"] == EXPECTED["compute_capability"],
        "cuda_available": identity["cuda_available"] is True,
        "checkpoint_frozen": identity["checkpoint_sha256"] == EXPECTED["checkpoint_sha256"],
        "provider_frozen": identity["provider_sha256"] == EXPECTED["provider_sha256"],
        "rgb_frozen": identity["rgb_sha256"] == EXPECTED["rgb_sha256"],
        "receipt_frozen": identity["receipt_sha256"] == EXPECTED["receipt_sha256"],
        "simlingo_head_frozen": identity["simlingo_head"] == EXPECTED["simlingo_head"],
        "simlingo_diff_frozen": identity["simlingo_diff_sha256"] == EXPECTED["simlingo_diff_sha256"],
    }


def _validate_train_only_receipt() -> dict[str, Any]:
    receipt = json.loads(FIXTURE_RECEIPT.read_text(encoding="utf-8"))
    if not (
        receipt.get("train_only") is True
        and receipt.get("dev_attempt_count") == 0
        and receipt.get("test_attempt_count") == 0
        and receipt.get("test_consumed") is False
    ):
        raise RuntimeError("STATIC_FIXTURE_NOT_TRAIN_ONLY")
    return receipt


def _camera_tensor(path: Path) -> torch.Tensor:
    raw = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if raw is None or raw.shape != (512, 1024, 3):
        raise RuntimeError("STATIC_TRAIN_RGB_INVALID")
    ok, encoded = cv2.imencode(".jpg", raw)
    if not ok:
        raise RuntimeError("STATIC_TRAIN_RGB_JPEG_FAILED")
    camera = cv2.imdecode(encoded, cv2.IMREAD_UNCHANGED)
    rgb = cv2.cvtColor(camera, cv2.COLOR_BGR2RGB)
    rgb = rgb[: int(rgb.shape[0] - (rgb.shape[0] * 4.8) // 16), :, :]
    patches = dynamic_preprocess(
        Image.fromarray(rgb), image_size=448, use_thumbnail=False, max_num=2
    )
    return torch.stack([build_transform(input_size=448)(item) for item in patches]).view(
        1, 1, len(patches), 3, 448, 448
    ).bfloat16()


def _calibration(receipt: Mapping[str, Any]) -> tuple[Any, Any]:
    fields = receipt["input_conditioning_rows"][0]["before"]["driving_input_fields"]
    intrinsics = torch.tensor(fields["camera_intrinsics"]["value"][0], dtype=torch.float32)
    extrinsics = torch.tensor(fields["camera_extrinsics"]["value"][0], dtype=torch.float32)
    return (intrinsics,), (extrinsics,)


def _base_input(
    *, camera: torch.Tensor, receipt: Mapping[str, Any], tokenizer: Any,
    encoder_variant: str, device: torch.device,
) -> DrivingInput:
    fields = receipt["input_conditioning_rows"][0]["before"]["driving_input_fields"]
    intrinsics, extrinsics = _calibration(receipt)
    adapter = OfficialDreamingCandidateAdapter(
        tokenizer=tokenizer, encoder_variant=encoder_variant, device=device
    )
    vehicle_speed = fields["vehicle_speed"]["value"]
    dummy = adapter.build("Continue driving on your current lane.", float(vehicle_speed[0][0]))
    return DrivingInput(
        camera_images=camera.to(device),
        image_sizes=None,
        camera_intrinsics=tuple(value.to(device) for value in intrinsics),
        camera_extrinsics=tuple(value.to(device) for value in extrinsics),
        vehicle_speed=torch.tensor(vehicle_speed, dtype=torch.float32, device=device),
        target_point=torch.tensor(fields["target_point"]["value"], dtype=torch.float32, device=device),
        prompt=dummy.prompt,
        prompt_inference=dummy.prompt_inference,
    )


def _dtype_histogram(values: Sequence[torch.Tensor]) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        key = str(value.dtype)
        result[key] = result.get(key, 0) + 1
    return dict(sorted(result.items()))


def _string_histogram(values: Sequence[str]) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        result[value] = result.get(value, 0) + 1
    return dict(sorted(result.items()))


def _load_model() -> tuple[Any, Any, Any, torch.device, dict[str, Any]]:
    if _sha256_file(CHECKPOINT) != EXPECTED["checkpoint_sha256"]:
        raise RuntimeError("FROZEN_CHECKPOINT_HASH_MISMATCH")
    random.seed(42)
    np.random.seed(42)
    torch.manual_seed(42)
    torch.cuda.manual_seed_all(42)
    torch.use_deterministic_algorithms(True, warn_only=True)
    cfg = OmegaConf.load(CHECKPOINT_CONFIG)
    if "2B" in cfg.model.language_model.variant:
        processor = AutoTokenizer.from_pretrained(
            cfg.model.language_model.variant, trust_remote_code=True,
            use_fast=False, local_files_only=True,
        )
    else:
        processor = AutoProcessor.from_pretrained(
            cfg.model.language_model.variant, trust_remote_code=True,
            use_fast=False, local_files_only=True,
        )
    tokenizer = processor.tokenizer if hasattr(processor, "tokenizer") else processor
    tokenizer.add_special_tokens(
        {"additional_special_tokens": [
            "<WAYPOINTS>", "<WAYPOINTS_DIFF>", "<ORG_WAYPOINTS_DIFF>",
            "<ORG_WAYPOINTS>", "<WAYPOINT_LAST>", "<ROUTE>",
            "<ROUTE_DIFF>", "<TARGET_POINT>",
        ]}
    )
    tokenizer.padding_side = "left"
    model_type = cfg.model.vision_model.variant.split("/")[1]
    model = instantiate(
        cfg.model, cfg_data_module=cfg.data_module, processor=processor,
        cache_dir=str((ROOT / "pretrained" / model_type).resolve()), _recursive_=False,
    )
    state = torch.load(CHECKPOINT, map_location="cpu")
    checkpoint_parameter_dtypes = {
        name: str(value.dtype) for name, value in state.items()
        if name in dict(model.named_parameters())
    }
    load_result = model.load_state_dict(state, strict=True)
    del state
    gc.collect()
    for parameter in model.parameters():
        parameter.requires_grad_(False)
    model.eval()
    device = torch.device("cuda:0")
    model.to(device)
    runtime_parameter_dtypes = {name: str(value.dtype) for name, value in model.named_parameters()}
    mismatches = sorted(
        name for name, dtype in checkpoint_parameter_dtypes.items()
        if runtime_parameter_dtypes.get(name) != dtype
    )
    transitions = _string_histogram(
        [
            checkpoint_parameter_dtypes[name] + "->" + runtime_parameter_dtypes[name]
            for name in checkpoint_parameter_dtypes
        ]
    )
    metadata = {
        "serialized_checkpoint_parameter_dtype_histogram": _string_histogram(
            list(checkpoint_parameter_dtypes.values())
        ),
        "runtime_parameter_dtype_histogram": _dtype_histogram(
            [value for _, value in model.named_parameters()]
        ),
        "serialized_checkpoint_to_runtime_transition_histogram": transitions,
        "checkpoint_to_runtime_dtype_mismatches": mismatches,
        "load_missing_keys": list(load_result.missing_keys),
        "load_unexpected_keys": list(load_result.unexpected_keys),
        "requires_grad_parameter_count": sum(int(value.requires_grad) for value in model.parameters()),
        "training": bool(model.training),
    }
    return model, tokenizer, cfg, device, metadata


def _close_model(model: Any) -> None:
    model.to("cpu")
    del model
    gc.collect()
    torch.cuda.empty_cache()


def _tensor_meta(value: Any) -> Any:
    if torch.is_tensor(value):
        return {"dtype": str(value.dtype), "device": str(value.device), "shape": list(value.shape)}
    if isinstance(value, tuple) and hasattr(value, "_asdict"):
        return {name: _tensor_meta(item) for name, item in value._asdict().items()}
    if isinstance(value, (tuple, list)):
        return [_tensor_meta(item) for item in value]
    if isinstance(value, dict):
        return {str(name): _tensor_meta(item) for name, item in value.items()}
    return {"python_type": type(value).__name__}


def _autocast_meta() -> dict[str, Any]:
    try:
        dtype = torch.get_autocast_dtype("cuda")
    except (AttributeError, TypeError):
        dtype = torch.get_autocast_gpu_dtype()
    try:
        enabled = torch.is_autocast_enabled("cuda")
    except TypeError:
        enabled = torch.is_autocast_enabled()
    return {"enabled": bool(enabled), "dtype": str(dtype)}


def _first_conv(model: Any) -> tuple[str, Any]:
    for name, module in model.named_modules():
        if isinstance(module, torch.nn.Conv2d):
            return name, module
    raise RuntimeError("NO_CONV2D_FOR_DTYPE_AUDIT")


def _capture_hooks(model: Any) -> tuple[dict[str, Any], list[Any]]:
    capture: dict[str, Any] = {}
    conv_name, conv = _first_conv(model)

    def model_pre_hook(_module: Any, args: Any) -> None:
        if "model_input" not in capture:
            capture["model_input"] = _tensor_meta(args[0])
            capture["model_entry_autocast"] = _autocast_meta()

    def conv_pre_hook(module: Any, args: Any) -> None:
        if "first_conv" not in capture:
            capture["first_conv"] = {
                "module_name": conv_name,
                "input": _tensor_meta(args[0]),
                "weight": _tensor_meta(module.weight),
                "bias": None if module.bias is None else _tensor_meta(module.bias),
                "autocast": _autocast_meta(),
            }

    return capture, [model.register_forward_pre_hook(model_pre_hook), conv.register_forward_pre_hook(conv_pre_hook)]


def _finite(value: Any) -> bool:
    if isinstance(value, (list, tuple)):
        return bool(value) and all(_finite(item) for item in value)
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _candidate(row: Mapping[str, Any]) -> RuntimeCandidate:
    return RuntimeCandidate(
        candidate_id=str(row["candidate_id"]),
        interpretation_id=str(row["interpretation_id"]),
        prompt_text=str(row["prompt_text"]),
        visual_track_id="r9-static-train-rgb",
        visual_anchor_digest=EXPECTED["rgb_sha256"],
        candidate_semantic_digest=str(row["semantic_digest"]),
        candidate_input_digest=str(row["input_bundle_sha256"]),
        source_observation_id="R9-STATIC-TRAIN-OBSERVATION",
        source_frame_id=410,
        generator_rule="R4_PREREGISTERED_GPU_CANDIDATE_CERTIFICATION_FIXTURE",
    )


def _prepare_forward_runtime(prereg: Mapping[str, Any]) -> tuple[Any, Any, Any, Any, dict[str, Any]]:
    receipt = _validate_train_only_receipt()
    model, tokenizer, cfg, device, load_metadata = _load_model()
    camera = _camera_tensor(FIXTURE_RGB)
    base = _base_input(
        camera=camera, receipt=receipt, tokenizer=tokenizer,
        encoder_variant=str(cfg.model.vision_model.variant), device=device,
    )
    agent = SimpleNamespace(
        tokenizer=tokenizer, cfg=cfg, device=device, model=model, DrivingInput=base._asdict()
    )
    provider = OfficialDreamingCandidateForwardProvider(agent)
    provider.begin_event()
    fields = receipt["input_conditioning_rows"][0]["before"]["driving_input_fields"]
    episode = SimpleNamespace(
        ego_state=SimpleNamespace(speed_mps=float(fields["vehicle_speed"]["value"][0][0])),
        vision_observation=SimpleNamespace(
            observation_id="R9-STATIC-TRAIN-OBSERVATION", frame_id=410
        ),
    )
    if len(prereg["schedule"]) != 48:
        raise RuntimeError("PREREGISTERED_SCHEDULE_NOT_48")
    return model, provider, episode, base, load_metadata


def audit(output: Path, prereg_path: Path) -> int:
    prereg = json.loads(prereg_path.read_text(encoding="utf-8"))
    identity = _execution_identity()
    identity_checks = _identity_checks(identity)
    model = None
    try:
        model, _provider, _episode, base, load_metadata = _prepare_forward_runtime(prereg)
        conv_name, conv = _first_conv(model)
        parameter_devices = sorted({str(value.device) for value in model.parameters()})
        audit_checks = {
            **identity_checks,
            "zero_official_forwards": True,
            "all_parameters_on_cuda_0": parameter_devices == ["cuda:0"],
            "runtime_parameter_dtype_is_production_fp32": load_metadata["runtime_parameter_dtype_histogram"] == {"torch.float32": 989},
            "serialized_checkpoint_materialization_is_expected": set(load_metadata["serialized_checkpoint_to_runtime_transition_histogram"]) <= {"torch.float32->torch.float32", "torch.float16->torch.float32"},
            "checkpoint_load_strict": not load_metadata["load_missing_keys"] and not load_metadata["load_unexpected_keys"],
            "parameters_frozen": load_metadata["requires_grad_parameter_count"] == 0,
            "model_eval": load_metadata["training"] is False,
            "camera_input_bfloat16_cuda": base.camera_images.dtype == torch.bfloat16 and str(base.camera_images.device) == "cuda:0",
            "first_conv_weight_fp32_cuda": conv.weight.dtype == torch.float32 and str(conv.weight.device) == "cuda:0",
            "first_conv_bias_fp32_cuda": conv.bias is not None and conv.bias.dtype == torch.float32 and str(conv.bias.device) == "cuda:0",
            "provider_precision_semantics_unchanged": True,
        }
        payload = {
            "schema_version": "driveclarify.method_v1_r4.static_gpu_dtype_device_audit.v1",
            "status": "PASS" if all(audit_checks.values()) else "FAIL",
            "execution_identity": identity,
            "checks": audit_checks,
            "official_model_forward_count": 0,
            "engineering_smoke_forward_count": 0,
            "counted_matrix_forward_count": 0,
            "load_metadata": load_metadata,
            "input_dtype_device": _tensor_meta(base),
            "first_conv": {
                "module_name": conv_name,
                "weight": _tensor_meta(conv.weight),
                "bias": _tensor_meta(conv.bias),
            },
            "parameter_devices": parameter_devices,
            "autocast_contract": {
                "owner": "OfficialDreamingCandidateForwardProvider",
                "condition": "parameter_dtype == torch.float32",
                "device_type": "cuda",
                "dtype": "torch.float16",
                "audit_is_static_no_autocast_scope_entered": True,
            },
            "prohibited_precision_mutations": {
                "model_float_called": False,
            "explicit_global_checkpoint_dtype_conversion_called": False,
                "model_weights_modified": False,
                "cpu_numerical_forward_path_used": False,
            },
        }
        payload["audit_sha256"] = canonical_sha256(payload)
        _dump(output, payload)
        print(json.dumps({"status": payload["status"], "audit_sha256": payload["audit_sha256"]}), flush=True)
        return 0 if payload["status"] == "PASS" else 1
    finally:
        if model is not None:
            _close_model(model)


def _record_forward(
    *, provider: Any, episode: Any, model: Any, row: Mapping[str, Any],
    canonical_input: Mapping[str, Any], expected_count: int,
) -> dict[str, Any]:
    capture, hooks = _capture_hooks(model)
    try:
        before = provider.event_forward_count
        result = provider(episode, _candidate(row))
        after = provider.event_forward_count
    finally:
        for hook in hooks:
            hook.remove()
    if before + 1 != after or after != expected_count:
        raise RuntimeError("OFFICIAL_FORWARD_COUNT_NOT_EXACTLY_ONCE")
    route = [list(point) for point in result.plan.route]
    speed = [list(point) for point in result.plan.speed]
    record = {
        "ordinal": int(row["ordinal"]),
        "fixture_id": str(row["fixture_id"]),
        "repeat_index": int(row["repeat_index"]),
        "candidate_order_index": int(row["candidate_order_index"]),
        "candidate_id": str(row["candidate_id"]),
        "interpretation_id": str(row["interpretation_id"]),
        "prompt_text": str(row["prompt_text"]),
        "prompt_sha256": str(row["prompt_sha256"]),
        "semantic_digest": str(row["semantic_digest"]),
        "input_bundle_sha256": str(row["input_bundle_sha256"]),
        "canonical_input": dict(canonical_input),
        "source_observation": {"observation_id": "R9-STATIC-TRAIN-OBSERVATION", "frame_id": 410},
        "provider_identity": "driveclarify_official_dreaming_adapter.adapter.OfficialDreamingCandidateForwardProvider",
        "checkpoint_sha256": EXPECTED["checkpoint_sha256"],
        "execution_device": "cuda:0",
        "dtype_device_autocast": capture,
        "route": route,
        "speed": speed,
        "finite_value_checks": {"route": _finite(route), "speed": _finite(speed)},
        "route_output_sha256": canonical_sha256(route),
        "speed_output_sha256": canonical_sha256(speed),
        "combined_output_sha256": canonical_sha256({"route": route, "speed": speed}),
        "provider_forward_count_after": after,
        "provider_forward_evidence": dict(result.forward_evidence),
        "preregistered_schedule_row_sha256": str(row["schedule_row_sha256"]),
    }
    record["record_sha256"] = canonical_sha256(record)
    return record


def smoke(output: Path, prereg_path: Path, audit_path: Path) -> int:
    prereg = json.loads(prereg_path.read_text(encoding="utf-8"))
    audit_value = json.loads(audit_path.read_text(encoding="utf-8"))
    identity = _execution_identity()
    identity_checks = _identity_checks(identity)
    if audit_value.get("status") != "PASS" or audit_value["execution_identity"]["execution_identity_sha256"] != identity["execution_identity_sha256"]:
        raise RuntimeError("STATIC_AUDIT_NOT_PASS_OR_IDENTITY_CHANGED")
    model = None
    payload: dict[str, Any] = {
        "schema_version": "driveclarify.method_v1_r4.noncounted_engineering_gpu_smoke.v1",
        "label": "NONCOUNTED_ENGINEERING_GPU_SMOKE_FORWARD",
        "status": "RUNNING",
        "execution_identity": identity,
        "identity_checks": identity_checks,
        "engineering_smoke_forward_count": 0,
        "counted_matrix_forward_count": 0,
        "scientific_retry_count": 0,
        "repair_attempt_count": 0,
        "record": None,
    }
    try:
        model, provider, episode, _base, load_metadata = _prepare_forward_runtime(prereg)
        versions = [parameter._version for parameter in model.parameters()]
        fixture = {row["fixture_id"]: row for row in prereg["fixtures"]}[prereg["schedule"][0]["fixture_id"]]
        record = _record_forward(
            provider=provider, episode=episode, model=model,
            row=prereg["schedule"][0], canonical_input=fixture["canonical_input"], expected_count=1,
        )
        versions_after = [parameter._version for parameter in model.parameters()]
        checks = {
            **identity_checks,
            "exactly_one_engineering_forward": provider.event_forward_count == 1,
            "not_counted_matrix": payload["counted_matrix_forward_count"] == 0,
            "route_finite": record["finite_value_checks"]["route"],
            "speed_finite": record["finite_value_checks"]["speed"],
            "model_entry_autocast_enabled": record["dtype_device_autocast"]["model_entry_autocast"]["enabled"] is True,
            "model_entry_autocast_fp16": record["dtype_device_autocast"]["model_entry_autocast"]["dtype"] == "torch.float16",
            "first_conv_input_cuda": record["dtype_device_autocast"]["first_conv"]["input"]["device"] == "cuda:0",
            "first_conv_weight_fp32": record["dtype_device_autocast"]["first_conv"]["weight"]["dtype"] == "torch.float32",
            "first_conv_bias_fp32": record["dtype_device_autocast"]["first_conv"]["bias"]["dtype"] == "torch.float32",
            "parameter_versions_unchanged": versions == versions_after,
            "runtime_parameter_dtype_is_production_fp32": load_metadata["runtime_parameter_dtype_histogram"] == {"torch.float32": 989},
            "serialized_checkpoint_materialization_is_expected": set(load_metadata["serialized_checkpoint_to_runtime_transition_histogram"]) <= {"torch.float32->torch.float32", "torch.float16->torch.float32"},
        }
        payload.update({
            "status": "PASS" if all(checks.values()) else "FAIL",
            "engineering_smoke_forward_count": 1,
            "record": record,
            "checks": checks,
            "load_metadata": load_metadata,
            "defect_signature": None,
        })
    except Exception as error:
        signature_input = type(error).__name__ + ":" + str(error)
        payload.update({
            "status": "DTYPE_DEVICE_INTEGRATION_DEFECT",
            "engineering_smoke_forward_count": 0,
            "defect_type": type(error).__name__,
            "defect_message": str(error),
            "defect_signature": hashlib.sha256(signature_input.encode("utf-8")).hexdigest(),
        })
    finally:
        if model is not None:
            _close_model(model)
    payload["smoke_sha256"] = canonical_sha256(payload)
    _dump(output, payload)
    print(json.dumps({"status": payload["status"], "smoke_sha256": payload["smoke_sha256"], "defect_signature": payload.get("defect_signature")}), flush=True)
    return 0 if payload["status"] == "PASS" else 1


def freeze_identity(output: Path, prereg_path: Path, audit_path: Path, smoke_path: Path) -> int:
    prereg = json.loads(prereg_path.read_text(encoding="utf-8"))
    audit_value = json.loads(audit_path.read_text(encoding="utf-8"))
    smoke_value = json.loads(smoke_path.read_text(encoding="utf-8"))
    identity = _execution_identity()
    checks = {
        **_identity_checks(identity),
        "preregistration_is_48": len(prereg["schedule"]) == 48,
        "static_audit_pass": audit_value.get("status") == "PASS",
        "smoke_pass": smoke_value.get("status") == "PASS",
        "audit_identity_exact": audit_value["execution_identity"]["execution_identity_sha256"] == identity["execution_identity_sha256"],
        "smoke_identity_exact": smoke_value["execution_identity"]["execution_identity_sha256"] == identity["execution_identity_sha256"],
        "smoke_noncounted": smoke_value["counted_matrix_forward_count"] == 0,
        "no_repair_used": smoke_value["repair_attempt_count"] == 0,
    }
    payload = {
        "schema_version": "driveclarify.method_v1_r4.gpu_execution_identity_freeze.v1",
        "status": "FROZEN_FOR_EXACT_48_COUNTED_MATRIX" if all(checks.values()) else "FAIL",
        "execution_identity": identity,
        "preregistration_sha256": prereg["preregistration_sha256"],
        "audit_sha256": audit_value["audit_sha256"],
        "smoke_sha256": smoke_value["smoke_sha256"],
        "checks": checks,
        "authorized_counted_forward_count": 48,
        "authorized_retry_forward_count": 0,
        "carla_authorized": False,
    }
    payload["freeze_sha256"] = canonical_sha256(payload)
    _dump(output, payload)
    print(json.dumps({"status": payload["status"], "freeze_sha256": payload["freeze_sha256"]}), flush=True)
    return 0 if payload["status"].startswith("FROZEN") else 1


def matrix(output: Path, prereg_path: Path, freeze_path: Path) -> int:
    prereg = json.loads(prereg_path.read_text(encoding="utf-8"))
    frozen = json.loads(freeze_path.read_text(encoding="utf-8"))
    identity = _execution_identity()
    if frozen.get("status") != "FROZEN_FOR_EXACT_48_COUNTED_MATRIX":
        raise RuntimeError("GPU_EXECUTION_IDENTITY_NOT_FROZEN")
    if frozen["execution_identity"]["execution_identity_sha256"] != identity["execution_identity_sha256"]:
        raise RuntimeError("GPU_EXECUTION_IDENTITY_CHANGED_AFTER_FREEZE")
    if frozen["preregistration_sha256"] != prereg["preregistration_sha256"]:
        raise RuntimeError("PREREGISTRATION_CHANGED_AFTER_FREEZE")
    model = None
    records: list[dict[str, Any]] = []
    payload: dict[str, Any] = {
        "schema_version": "driveclarify.method_v1_r4.counted_gpu_forward_matrix.v1",
        "status": "RUNNING",
        "execution_identity": identity,
        "execution_freeze_sha256": frozen["freeze_sha256"],
        "preregistration_sha256": prereg["preregistration_sha256"],
        "authorized_counted_forward_count": 48,
        "actual_counted_forward_count": 0,
        "engineering_smoke_forward_count_in_this_run": 0,
        "retry_forward_count": 0,
        "records": records,
    }
    _dump(output, payload)
    try:
        model, provider, episode, _base, load_metadata = _prepare_forward_runtime(prereg)
        versions = [parameter._version for parameter in model.parameters()]
        fixture_map = {row["fixture_id"]: row for row in prereg["fixtures"]}
        for expected_ordinal, row in enumerate(prereg["schedule"], start=1):
            if int(row["ordinal"]) != expected_ordinal:
                raise RuntimeError("PREREGISTERED_ORDER_INVALID")
            record = _record_forward(
                provider=provider, episode=episode, model=model, row=row,
                canonical_input=fixture_map[row["fixture_id"]]["canonical_input"],
                expected_count=expected_ordinal,
            )
            if not all(record["finite_value_checks"].values()):
                raise RuntimeError("NONFINITE_OFFICIAL_FORWARD_OUTPUT")
            records.append(record)
            payload["actual_counted_forward_count"] = len(records)
            _dump(output, payload)
            print(
                f"R4_COUNTED_GPU_FORWARD {expected_ordinal}/48 {row['fixture_id']} repeat={row['repeat_index']} candidate={row['candidate_label']}",
                flush=True,
            )
        versions_after = [parameter._version for parameter in model.parameters()]
        checkpoint_after = _sha256_file(CHECKPOINT)
        checks = {
            **_identity_checks(identity),
            "exactly_48_counted_forwards": provider.event_forward_count == 48 and len(records) == 48,
            "schedule_order_exact": [row["preregistered_schedule_row_sha256"] for row in records] == [row["schedule_row_sha256"] for row in prereg["schedule"]],
            "all_outputs_finite": all(all(row["finite_value_checks"].values()) for row in records),
            "all_model_entry_autocast_enabled": all(row["dtype_device_autocast"]["model_entry_autocast"]["enabled"] for row in records),
            "all_model_entry_autocast_fp16": all(row["dtype_device_autocast"]["model_entry_autocast"]["dtype"] == "torch.float16" for row in records),
            "all_first_conv_weights_fp32_cuda": all(
                row["dtype_device_autocast"]["first_conv"]["weight"]["dtype"] == "torch.float32"
                and row["dtype_device_autocast"]["first_conv"]["weight"]["device"] == "cuda:0"
                for row in records
            ),
            "all_first_conv_biases_fp32_cuda": all(
                row["dtype_device_autocast"]["first_conv"]["bias"]["dtype"] == "torch.float32"
                and row["dtype_device_autocast"]["first_conv"]["bias"]["device"] == "cuda:0"
                for row in records
            ),
            "parameter_versions_unchanged": versions == versions_after,
            "checkpoint_hash_unchanged_after": checkpoint_after == EXPECTED["checkpoint_sha256"],
            "runtime_parameter_dtype_is_production_fp32": load_metadata["runtime_parameter_dtype_histogram"] == {"torch.float32": 989},
            "serialized_checkpoint_materialization_is_expected": set(load_metadata["serialized_checkpoint_to_runtime_transition_histogram"]) <= {"torch.float32->torch.float32", "torch.float16->torch.float32"},
            "zero_retries": payload["retry_forward_count"] == 0,
        }
        payload.update({
            "status": "PASS" if all(checks.values()) else "FAIL",
            "checks": checks,
            "load_metadata": load_metadata,
            "checkpoint_sha256_after": checkpoint_after,
            "output_hash_sequence_sha256": canonical_sha256([row["combined_output_sha256"] for row in records]),
        })
    except Exception as error:
        payload.update({
            "status": "STOPPED_ON_MATRIX_DEFECT",
            "defect_type": type(error).__name__,
            "defect_message": str(error),
            "defect_signature": hashlib.sha256((type(error).__name__ + ":" + str(error)).encode("utf-8")).hexdigest(),
        })
    finally:
        if model is not None:
            _close_model(model)
    payload["matrix_log_sha256"] = canonical_sha256(payload)
    _dump(output, payload)
    print(json.dumps({"status": payload["status"], "actual_count": payload["actual_counted_forward_count"], "matrix_log_sha256": payload["matrix_log_sha256"]}), flush=True)
    return 0 if payload["status"] == "PASS" else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="mode", required=True)
    for name in ("audit", "smoke", "freeze", "matrix"):
        command = sub.add_parser(name)
        command.add_argument("--output", type=Path, required=True)
        command.add_argument("--preregistration", type=Path, required=True)
        if name in {"smoke", "freeze"}:
            command.add_argument("--audit", type=Path, required=True)
        if name == "freeze":
            command.add_argument("--smoke", type=Path, required=True)
        if name == "matrix":
            command.add_argument("--freeze", type=Path, required=True)
    args = parser.parse_args()
    if args.mode == "audit":
        return audit(args.output, args.preregistration)
    if args.mode == "smoke":
        return smoke(args.output, args.preregistration, args.audit)
    if args.mode == "freeze":
        return freeze_identity(args.output, args.preregistration, args.audit, args.smoke)
    return matrix(args.output, args.preregistration, args.freeze)


if __name__ == "__main__":
    raise SystemExit(main())
