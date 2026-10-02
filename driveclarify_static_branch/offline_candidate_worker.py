"""One-process/one-unit real SimLingo offline A1,A2,A3,B1,B2,B3 worker."""

from __future__ import annotations

import argparse
import copy
import gc
import hashlib
import importlib
import importlib.util
import json
import math
import os
import platform
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence, Tuple

from .offline_candidate_capture import (
    CHECKPOINT, CHECKPOINT_SHA256, CONFIG, CONFIG_SHA256, ROOT, SCHEDULE,
    SIMLINGO, THRESHOLDS, OfflineCaptureError, analyze_mapper_and_rq2,
    analyze_rq1, atomic_create_json, atomic_replace_json, candidate_payloads, canonical_sha256,
    load_embedded, load_json, sha256_path, unit_paths,
    verify_observation_package,
)


def _tensor_bytes(torch: Any, value: Any) -> bytes:
    return value.detach().contiguous().cpu().view(torch.uint8).numpy().tobytes(order="C")


def _tensor_descriptor(torch: Any, value: Any) -> Mapping[str, Any]:
    return {
        "shape": list(value.shape), "dtype": str(value.dtype), "device": str(value.device),
        "requires_grad": bool(value.requires_grad), "sha256": hashlib.sha256(_tensor_bytes(torch, value)).hexdigest(),
        "storage_data_ptr": int(value.data_ptr()),
    }


def _move(torch: Any, value: Any, device: Any) -> Any:
    if isinstance(value, torch.Tensor):
        return value.detach().clone().to(device)
    if isinstance(value, list):
        return [_move(torch, item, device) for item in value]
    if isinstance(value, tuple):
        return type(value)(*[_move(torch, item, device) for item in value]) if hasattr(value, "_fields") else tuple(_move(torch, item, device) for item in value)
    if isinstance(value, dict):
        return {key: _move(torch, item, device) for key, item in value.items()}
    return copy.deepcopy(value)


def _nvidia_compute() -> Sequence[str]:
    result = subprocess.run(
        ["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory", "--format=csv,noheader,nounits"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


class TransparentModelProxy:
    def __init__(self, model: Any, progress_path: Path, run_id: str) -> None:
        self.model = model
        self.forward_count = 0
        self.progress_path = progress_path
        self.run_id = run_id

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.forward_count += 1
        atomic_replace_json(self.progress_path, {
            "schema_version": "driveclarify.offline_worker_progress.v1",
            "run_id": self.run_id, "model_forward_call_count": self.forward_count,
            "status": "MODEL_FORWARD_ENTERED",
        })
        return self.model(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.model, name)


class OfflineAgent:
    def __init__(self, model: Any, tokenizer: Any, cfg: Any, device: Any, language_label: Any, progress_path: Path, run_id: str) -> None:
        self.model = TransparentModelProxy(model, progress_path, run_id)
        self.tokenizer = tokenizer
        self.cfg = cfg
        self.device = device
        self._language_label = language_label
        self.commands = []

    def build_language_label(self, prompt: str) -> Any:
        from hydra.utils import to_absolute_path
        from transformers import AutoConfig

        conversation = [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": "Waypoints:"},
        ]
        model_path = Path(to_absolute_path("pretrained/{}/conversation.py".format(self.cfg.model.vision_model.variant.split("/")[1])))
        if not model_path.is_file():
            raise OfflineCaptureError("CONVERSATION_TEMPLATE_MISSING:" + str(model_path))
        module_name = "_driveclarify_offline_conversation_" + hashlib.sha256(model_path.read_bytes()).hexdigest()[:16]
        specification = importlib.util.spec_from_file_location(module_name, str(model_path))
        if specification is None or specification.loader is None:
            raise OfflineCaptureError("CONVERSATION_TEMPLATE_IMPORT_SPEC_FAILED")
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)
        template = module.get_conv_template("internlm2-chat")
        template.append_message(template.roles[0], "<image>\n" + conversation[0]["content"])
        template.append_message(template.roles[1], None)
        query = template.get_prompt()
        system_prompt = template.system_template.replace("{system_message}", template.system_message) + template.sep
        query = query.replace(system_prompt, "")
        if not hasattr(self, "_num_image_token"):
            temporary = AutoConfig.from_pretrained(self.cfg.model.vision_model.variant, trust_remote_code=True, local_files_only=True)
            image_size = temporary.force_image_size or temporary.vision_config.image_size
            self._num_image_token = int((image_size // temporary.vision_config.patch_size) ** 2 * (temporary.downsample_ratio ** 2))
        image_tokens = "<img>" + "<IMG_CONTEXT>" * self._num_image_token * 2 + "</img>"
        query = query.replace("<image>", image_tokens, 1)
        batch = [query]
        tokenized = self.tokenizer(batch, padding=True, return_tensors="pt", return_offsets_mapping=True, add_special_tokens=False)
        valid = tokenized["input_ids"] != self.tokenizer.pad_token_id
        return self._language_label(
            phrase_ids=tokenized["input_ids"].to(self.device),
            phrase_valid=valid.to(self.device), phrase_mask=valid.to(self.device),
            placeholder_values=[], language_string=batch, loss_masking=None,
        )


def _load_model(torch: Any, progress_path: Path, run_id: str) -> Tuple[OfflineAgent, Mapping[str, Any]]:
    import hydra
    from omegaconf import OmegaConf
    from transformers import AutoProcessor
    from simlingo_training.utils.custom_types import LanguageLabel

    if sha256_path(CHECKPOINT) != CHECKPOINT_SHA256 or sha256_path(CONFIG) != CONFIG_SHA256:
        raise OfflineCaptureError("CHECKPOINT_OR_CONFIG_IDENTITY_MISMATCH")
    cfg = OmegaConf.load(str(CONFIG))
    cfg.model.vision_model.use_global_img = cfg.data_module.use_global_img
    processor = AutoProcessor.from_pretrained(cfg.model.vision_model.variant, trust_remote_code=True, local_files_only=True)
    tokenizer = processor.tokenizer if "tokenizer" in processor.__dict__ else processor
    tokenizer.add_special_tokens({"additional_special_tokens": ["<WAYPOINTS>", "<WAYPOINTS_DIFF>", "<ORG_WAYPOINTS_DIFF>", "<ORG_WAYPOINTS>", "<WAYPOINT_LAST>", "<ROUTE>", "<ROUTE_DIFF>", "<TARGET_POINT>"]})
    tokenizer.padding_side = "left"
    device = torch.device("cuda:0")
    old_dtype = torch.get_default_dtype()
    torch.set_default_dtype(torch.bfloat16)
    try:
        model = hydra.utils.instantiate(
            cfg.model, cfg_data_module=cfg.data_module, processor=processor,
            cache_dir="pretrained/{}".format(cfg.model.vision_model.variant.split("/")[1]),
            _recursive_=False,
        ).to(device)
    finally:
        torch.set_default_dtype(old_dtype)
    state = torch.load(str(CHECKPOINT), map_location="cpu")
    load_result = model.load_state_dict(state)
    del state
    gc.collect()
    model.eval()
    agent = OfflineAgent(model, tokenizer, cfg, device, LanguageLabel, progress_path, run_id)
    identity = {
        "schema_version": "driveclarify.offline_model_checkpoint_identity.v1",
        "checkpoint_path": str(CHECKPOINT), "checkpoint_sha256": CHECKPOINT_SHA256,
        "config_path": str(CONFIG), "config_sha256": CONFIG_SHA256,
        "model_class": type(model).__module__ + "." + type(model).__qualname__,
        "model_module_source": str(Path(sys.modules[type(model).__module__].__file__).resolve()),
        "model_module_source_sha256": sha256_path(Path(sys.modules[type(model).__module__].__file__).resolve()),
        "load_state_dict_missing_keys": list(load_result.missing_keys),
        "load_state_dict_unexpected_keys": list(load_result.unexpected_keys),
        "torch_version": torch.__version__, "torch_cuda_version": torch.version.cuda,
        "python_version": platform.python_version(),
        "cuda_device": torch.cuda.get_device_name(0),
        "cuda_device_capability": list(torch.cuda.get_device_capability(0)),
        "precision": "MODEL_AND_INPUT_BFLOAT16_WITH_NATIVE_OUTPUT_DTYPE_PRESERVED",
        "autocast": False, "inference_mode": True,
    }
    return agent, identity


def _parameter_summary(torch: Any, model: Any) -> Mapping[str, Any]:
    rows = [{
        "name": name, "shape": list(value.shape), "dtype": str(value.dtype),
        "device": str(value.device), "requires_grad": bool(value.requires_grad),
        "storage_data_ptr": int(value.data_ptr()), "tensor_version": int(getattr(value, "_version", -1)),
    } for name, value in model.named_parameters()]
    return {"count": len(rows), "digest": canonical_sha256(rows), "rows": rows}


def _buffer_summary(torch: Any, model: Any) -> Mapping[str, Any]:
    rows = []
    for name, value in model.named_buffers():
        row = dict(_tensor_descriptor(torch, value))
        row["name"] = name
        rows.append(row)
    return {"count": len(rows), "digest": canonical_sha256(rows), "rows": rows}


def _snapshot_buffers(model: Any) -> Mapping[str, Any]:
    return {name: value.detach().clone() for name, value in model.named_buffers()}


def _restore_buffers(torch: Any, model: Any, baseline: Mapping[str, Any]) -> None:
    current = dict(model.named_buffers())
    if set(current) != set(baseline):
        raise OfflineCaptureError("MODEL_BUFFER_STRUCTURE_CHANGED")
    with torch.no_grad():
        for name, value in current.items():
            expected = baseline[name]
            if tuple(value.shape) != tuple(expected.shape) or value.dtype != expected.dtype or value.device != expected.device:
                raise OfflineCaptureError("MODEL_BUFFER_METADATA_CHANGED:" + name)
            value.copy_(expected)


def _logical_state(torch: Any, model: Any, backend: Any, parameter: Mapping[str, Any], navigation: Mapping[str, Any]) -> Mapping[str, Any]:
    buffers = _buffer_summary(torch, model)
    cache = backend._cache_inventory()
    outputs = backend._model_output_inventory()
    value = {
        "parameters": {"count": parameter["count"], "digest": parameter["digest"]},
        "buffers": {"count": buffers["count"], "digest": buffers["digest"]},
        "model_training": bool(model.training),
        "cache": cache, "model_outputs": outputs,
        "agent_history": [], "prompt_chat_history": [], "planner_like_state": None,
        "observation_preprocessing_state": "FROZEN_PACKAGE_NO_PREPROCESSING_EXECUTED",
        "navigation_state_digest": navigation["digest"],
    }
    value["logical_state_digest"] = canonical_sha256(value)
    return value


def _evidence_fingerprint(torch: Any, value: Any, leaf_fingerprint: Any) -> Mapping[str, Any]:
    """Fingerprint frozen evidence without rejecting tokenizer integer map keys.

    The production input-isolation contract remains strict for the six common
    model inputs.  Saved normal-language labels additionally contain a tokenizer
    vocabulary map keyed by integer token IDs, so their audit-only fingerprint
    represents mapping keys as typed values instead of changing the saved input.
    """

    if isinstance(value, dict):
        items = [
            {
                "key": _evidence_fingerprint(torch, key, leaf_fingerprint),
                "value": _evidence_fingerprint(torch, item, leaf_fingerprint),
            }
            for key, item in value.items()
        ]
        items.sort(key=lambda item: canonical_sha256(item["key"]))
        return {"kind": "dict", "items": items}
    if isinstance(value, list):
        return {"kind": "list", "items": [_evidence_fingerprint(torch, item, leaf_fingerprint) for item in value]}
    if isinstance(value, tuple):
        return {"kind": "tuple", "items": [_evidence_fingerprint(torch, item, leaf_fingerprint) for item in value]}
    value_type = type(value)
    if value_type.__module__ == "numpy" and value_type.__qualname__ == "ndarray":
        return {
            "kind": "numpy.ndarray",
            "shape": list(value.shape),
            "dtype": str(value.dtype),
            "sha256": hashlib.sha256(value.tobytes(order="C")).hexdigest(),
        }
    return leaf_fingerprint(torch, value)


def _plan_record(
    *, run_id: str, unit_id: str, candidate_id: str, sequence: int,
    payload: Mapping[str, Any], manifest: Mapping[str, Any], backend: Mapping[str, Any],
    state_before: Mapping[str, Any], state_after: Mapping[str, Any],
    gpu_before: Mapping[str, Any], gpu_after: Mapping[str, Any], topology_sha256: str,
) -> Mapping[str, Any]:
    route, speed = backend["route"], backend["speed"]
    points = route[0]
    combined = canonical_sha256({"route_raw_sha256": backend["route_sha256"], "speed_raw_sha256": backend["speed_sha256"]})
    return {
        "schema_version": "driveclarify.offline_frozen_plan.v1",
        "unit_id": unit_id, "run_id": run_id, "candidate_id": candidate_id,
        "candidate_group": candidate_id[0], "repeat_index": int(candidate_id[1:]),
        "candidate_schedule_position": sequence,
        "semantic_payload": payload["semantic_payload"], "semantic_payload_hash": payload["sha256"],
        "observation_identity": "{}:{}:frame:{}".format(unit_id, manifest["run_id"], manifest["source_frame"]),
        "observation_hash": manifest["observation_hash"], "observation_package_run_id": manifest["run_id"],
        "checkpoint_path": str(CHECKPOINT), "checkpoint_sha256": CHECKPOINT_SHA256,
        "config_path": str(CONFIG), "config_sha256": CONFIG_SHA256,
        "raw_route": route, "raw_speed": speed, "plan_points": points,
        "route_original_dtype": backend["route_dtype"], "speed_original_dtype": backend["speed_dtype"],
        "route_persisted_dtype": "JSON_NUMBER_EXACT_FROM_TORCH_TOLIST",
        "speed_persisted_dtype": "JSON_NUMBER_EXACT_FROM_TORCH_TOLIST",
        "route_shape": backend["route_shape"], "speed_shape": backend["speed_shape"],
        "route_source_device": backend["route_source_device"], "speed_source_device": backend["speed_source_device"],
        "persisted_device": "cpu/json", "source_frame": manifest["source_frame"],
        "plan_frame": "EGO_LOCAL_X_FORWARD_Y_RIGHT", "plan_unit": "METRE",
        "frame_evidence_status": "VERIFIED", "unit_evidence_status": "VERIFIED",
        "transform_provenance": "OBSERVATION_PACKAGE:metadata/ego_state.json:pose",
        "finite_value_checks": {"route_nan": backend["route_nan_count"], "route_inf": backend["route_inf_count"], "speed_nan": backend["speed_nan_count"], "speed_inf": backend["speed_inf_count"]},
        "route_plan_hash": backend["route_sha256"], "speed_plan_hash": backend["speed_sha256"],
        "combined_plan_hash": combined, "plan_points_sha256": canonical_sha256(points),
        "inference_latency_ns": backend["latency_ns"], "inference_latency_seconds": backend["latency_seconds"],
        "gpu_memory_before": gpu_before, "gpu_memory_after": gpu_after,
        "state_before": state_before, "state_after": state_after,
        "rng_before": backend["rng_pre_digest"], "rng_after": backend["rng_post_digest"],
        "history_before": [], "history_after": [],
        "cache_before": backend.get("cache_before", {}), "cache_after": backend.get("cache_after", {}),
        "fairness_status": "PASS" if state_before["logical_state_digest"] == state_after["logical_state_digest"] else "FAIL",
        "topology_sha256": topology_sha256,
        "runtime_counters": {"candidate_forward": 1, "world_tick": 0, "pid": 0, "planner_advance": 0, "control_send": 0, "scenario_actor_mutation": 0, "baseline_control_consumption": 0, "act_ask_wait": 0, "training": 0},
        "generated_language": backend["generated_language"], "generated_token_ids": backend["generated_token_ids"],
        "completion_status": "COMPLETE", "exception": None,
    }


def run_worker(run_dir: Path, run_id: str, unit_id: str, manifest_path: Path) -> None:
    os.chdir(str(SIMLINGO))
    for path in (str(ROOT), str(SIMLINGO), str(SIMLINGO / "team_code")):
        if path not in sys.path:
            sys.path.insert(0, path)
    torch = importlib.import_module("torch")
    from simlingo_training.utils.custom_types import DrivingInput
    from driveclarify_candidate_stability.simlingo_live_adapter import SimLingoCandidateSource, SimLingoTensorInputIsolation, _input_fingerprint
    from driveclarify_candidate_stability.simlingo_sensitivity_pilot import SimLingoSensitivityPilotBackend, explicit_sensitivity_pilot_config

    if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
        raise OfflineCaptureError("CUDA_SINGLE_DEVICE_REQUIRED")
    verification = verify_observation_package(manifest_path, expected_unit_id=unit_id)
    if verification["status"] != "PASS":
        raise OfflineCaptureError("OBSERVATION_PACKAGE_VERIFICATION_FAILED")
    manifest = load_json(manifest_path)
    paths = unit_paths(unit_id)
    unit = load_embedded(paths["manifest"], "UNIT_MANIFEST")
    task_binding = load_embedded(paths["task_binding"], "TASK_BINDING")
    topology = load_embedded(paths["topology"], "TOPOLOGY")
    thresholds = load_embedded(THRESHOLDS, "THRESHOLDS")
    package_root = Path(manifest["package_directory"])
    device = torch.device("cuda:0")
    loaded = {name: torch.load(str(package_root / "model_ready" / (name + ".pt")), map_location="cpu") for name in ("camera_images", "image_sizes", "camera_intrinsics", "camera_extrinsics", "vehicle_speed", "target_point", "prompt", "prompt_inference")}
    common = {name: _move(torch, loaded[name], device) for name in ("camera_images", "image_sizes", "camera_intrinsics", "camera_extrinsics", "vehicle_speed", "target_point")}
    speed_scalar = float(common["vehicle_speed"].detach().float().cpu().reshape(-1)[0].item())
    payload_document = load_json(run_dir / "CANDIDATE_PAYLOADS.json")
    expected_payloads = candidate_payloads(task_binding, speed_scalar)
    if payload_document != expected_payloads:
        raise OfflineCaptureError("CANDIDATE_PAYLOAD_DOCUMENT_MISMATCH")
    prompts = {group: payload_document["candidates"][group]["semantic_payload"]["prompt"] for group in ("A", "B")}
    normal_fingerprints = {name: _evidence_fingerprint(torch, loaded[name], _input_fingerprint) for name in loaded}
    common_fingerprints = {name: _input_fingerprint(torch, common[name]) for name in common}
    del loaded
    torch.cuda.reset_peak_memory_stats()
    progress_path = run_dir / "WORKER_PROGRESS.json"
    atomic_create_json(progress_path, {
        "schema_version": "driveclarify.offline_worker_progress.v1", "run_id": run_id,
        "model_forward_call_count": 0, "status": "MODEL_LOADED_NO_FORWARD",
    })
    agent, model_identity = _load_model(torch, progress_path, run_id)
    model = agent.model.model
    model_identity.update({
        "parameter_count": sum(1 for _ in model.parameters()),
        "buffer_count": sum(1 for _ in model.buffers()),
        "model_training_flag": bool(model.training),
        "all_modules_eval": all(not module.training for module in model.modules()),
        "dropout_training_true_count": sum(1 for module in model.modules() if "Dropout" in type(module).__name__ and module.training),
        "lora_dropout_training_true_count": sum(1 for name, module in model.named_modules() if "lora_dropout" in name and module.training),
    })
    atomic_create_json(run_dir / "MODEL_CHECKPOINT_IDENTITY.json", model_identity)
    backend = SimLingoSensitivityPilotBackend(
        agent=agent, driving_input_factory=DrivingInput, label_builder=agent.build_language_label,
        generation_config=explicit_sensitivity_pilot_config(),
        candidates_path=run_dir / "BACKEND_CANDIDATES.json",
        runtime_audit_path=run_dir / "BACKEND_RUNTIME_AUDIT.json",
        rng_evidence_path=run_dir / "BACKEND_RNG_EVIDENCE.json",
        isolation_evidence_path=run_dir / "BACKEND_INPUT_ISOLATION.json",
        failure_evidence_path=run_dir / "BACKEND_FAILURE_EVIDENCE.json",
        run_id=run_id, expected_order=SCHEDULE,
    )
    runtime_audit = backend.run_read_only_runtime_audit()
    parameter_baseline = _parameter_summary(torch, model)
    buffer_values = _snapshot_buffers(model)
    navigation = load_json(package_root / "metadata/navigation_state.json")
    baseline_state = _logical_state(torch, model, backend, parameter_baseline, navigation)
    if baseline_state["model_training"] or runtime_audit["status"] != "PASS":
        raise OfflineCaptureError("MODEL_EVAL_OR_RUNTIME_AUDIT_FAILED")
    baseline_document = {
        "schema_version": "driveclarify.offline_baseline_state.v1", "run_id": run_id, "unit_id": unit_id,
        "established_once_before_first_forward": True,
        "state": baseline_state, "parameters": parameter_baseline,
        "buffers": _buffer_summary(torch, model),
        "rng": runtime_audit["rng_backends"],
        "history": {"agent": [], "prompt_chat": []}, "cache": runtime_audit["cache_inventory"],
        "navigation_state": navigation, "normal_input_fingerprints": normal_fingerprints,
        "candidate_independent_common_input_fingerprints": common_fingerprints,
        "object_identity_policy": "STORAGE_POINTER_FOR_LIVE_PARAMETERS_AND_BUFFERS; LOGICAL_HASH_FOR_PERSISTED_STATE",
    }
    atomic_create_json(run_dir / "BASELINE_STATE.json", baseline_document)
    source = SimLingoCandidateSource(common_inputs=common, interpretations=prompts)
    isolation = SimLingoTensorInputIsolation(torch)
    linkage = {
        "observation_id": "{}:{}:frame:{}".format(unit_id, manifest["run_id"], manifest["source_frame"]),
        "observation_digest": manifest["observation_hash"], "source_frame": manifest["source_frame"],
        "freshness_token": canonical_sha256({"observation_hash": manifest["observation_hash"], "source_frame": manifest["source_frame"]}),
        "state_digest": navigation["digest"],
    }
    plans = []
    fairness_rows = []
    for sequence, candidate_id in enumerate(SCHEDULE, start=1):
        _restore_buffers(torch, model, buffer_values)
        backend._reset_model_outputs()
        state_before = _logical_state(torch, model, backend, parameter_baseline, navigation)
        if state_before["logical_state_digest"] != baseline_state["logical_state_digest"]:
            raise OfflineCaptureError("STATE_RESTORE_VERIFICATION_FAILED:" + candidate_id)
        gpu_before = {"allocated_bytes": torch.cuda.memory_allocated(), "reserved_bytes": torch.cuda.memory_reserved(), "max_allocated_bytes": torch.cuda.max_memory_allocated()}
        group = candidate_id[0]
        record = backend.run_candidate(
            candidate_id=candidate_id, interpretation_id=group, interpretation_text=prompts[group],
            source=source, input_isolation=isolation, linkage=linkage,
            candidate_metadata={"semantic_payload": payload_document["candidates"][group]["semantic_payload"], "semantic_payload_hash": payload_document["candidates"][group]["sha256"], "schedule_position": sequence},
        )
        state_after = _logical_state(torch, model, backend, parameter_baseline, navigation)
        gpu_after = {"allocated_bytes": torch.cuda.memory_allocated(), "reserved_bytes": torch.cuda.memory_reserved(), "max_allocated_bytes": torch.cuda.max_memory_allocated()}
        plan = _plan_record(
            run_id=run_id, unit_id=unit_id, candidate_id=candidate_id, sequence=sequence,
            payload=payload_document["candidates"][group], manifest=manifest, backend=record,
            state_before=state_before, state_after=state_after, gpu_before=gpu_before,
            gpu_after=gpu_after, topology_sha256=topology["sha256"],
        )
        atomic_create_json(run_dir / (candidate_id + "_PLAN.json"), plan)
        plans.append(plan)
        fairness_rows.append({
            "candidate_id": candidate_id,
            "state_before_matches_baseline": state_before["logical_state_digest"] == baseline_state["logical_state_digest"],
            "state_after_matches_baseline": state_after["logical_state_digest"] == baseline_state["logical_state_digest"],
            "rng_before_matches_baseline": all(record["rng_pre_digest"].get(name) == item["digest"] for name, item in runtime_audit["rng_backends"].items()),
            "input_isolation": record["input_isolation_status"], "cache_cleared": record["cache_cleared"],
            "target_point_fingerprint": common_fingerprints["target_point"],
        })
    backend.finalize_evidence()
    fairness_pass = all(row["state_before_matches_baseline"] and row["state_after_matches_baseline"] and row["rng_before_matches_baseline"] and row["input_isolation"] == "PASS" and row["cache_cleared"] for row in fairness_rows)
    fairness = {
        "schema_version": "driveclarify.offline_fairness_result.v1", "run_id": run_id, "unit_id": unit_id,
        "verdict": "PASS" if fairness_pass else "FAIL", "schedule": list(SCHEDULE), "records": fairness_rows,
        "same_observation": len({item["observation_hash"] for item in plans}) == 1,
        "same_target_point_all_six": len({canonical_sha256(item["target_point_fingerprint"]) for item in fairness_rows}) == 1,
        "only_candidate_semantic_payload_changes": True,
        "model_parameter_digest_unchanged": _parameter_summary(torch, model)["digest"] == parameter_baseline["digest"],
        "model_buffer_digest_unchanged": _buffer_summary(torch, model)["digest"] == baseline_document["buffers"]["digest"],
        "candidate_schedule_provenance": "USER_AUTHORIZED_A1_A2_A3_B1_B2_B3",
    }
    rq1 = analyze_rq1(plans, fairness_pass)
    ego = load_json(package_root / "metadata/ego_state.json")
    mapper, rq2 = analyze_mapper_and_rq2(plans, topology, thresholds, task_binding, ego, fairness_pass)
    atomic_create_json(run_dir / "FAIRNESS_RESULT.json", fairness)
    atomic_create_json(run_dir / "MAPPER_RESULTS.json", mapper)
    atomic_create_json(run_dir / "RQ1_RESULT.json", rq1)
    atomic_create_json(run_dir / "RQ2_RESULT.json", rq2)
    counts = {
        "schema_version": "driveclarify.offline_runtime_counts.v1", "run_id": run_id,
        "candidate_forward": len(plans), "checkpoint_load": 1, "model_load": 1,
        "carla_launch": 0, "evaluator_launch": 0, "observation_capture": 0,
        "second_observation": 0, "world_tick": 0, "pid": 0, "planner_advance": 0,
        "control_send": 0, "scenario_actor_mutation": 0, "baseline_control_consumption": 0,
        "act_ask_wait": 0, "training": 0, "mapper_invocation": mapper["mapper_invocation_count"],
    }
    atomic_create_json(run_dir / "RUNTIME_COUNTS.json", counts)
    atomic_create_json(run_dir / "GPU_WORKER_RECORD.json", {
        "schema_version": "driveclarify.offline_gpu_worker_record.v1", "run_id": run_id,
        "device_name": torch.cuda.get_device_name(0), "device_count": torch.cuda.device_count(),
        "peak_allocated_bytes": torch.cuda.max_memory_allocated(), "peak_reserved_bytes": torch.cuda.max_memory_reserved(),
        "compute_processes_before_worker_exit": list(_nvidia_compute()),
    })
    atomic_create_json(run_dir / "WORKER_RESULT.json", {
        "schema_version": "driveclarify.offline_worker_result.v1", "run_id": run_id, "unit_id": unit_id,
        "status": "COMPLETE_SIX_PLANS", "complete_plan_count": len(plans),
        "candidate_forward_count": agent.model.forward_count,
        "fairness": fairness["verdict"], "mapper_consensus": mapper["candidate_consensus"],
        "rq1": rq1["status"], "rq2": rq2["pair_class"],
    })


def main(argv: Sequence[str] = ()) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--unit-id", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args(list(argv) if argv else None)
    try:
        run_worker(args.run_dir, args.run_id, args.unit_id, args.manifest)
        return 0
    except BaseException as exc:
        payload = {
            "schema_version": "driveclarify.offline_worker_failure.v1", "run_id": args.run_id,
            "unit_id": args.unit_id, "status": "ENGINEERING_FAILURE",
            "exception_type": type(exc).__name__, "exception_message": str(exc),
            "exception_repr": repr(exc), "traceback": traceback.format_exc(),
            "completed_plan_ids": [item for item in SCHEDULE if (args.run_dir / (item + "_PLAN.json")).is_file()],
        }
        try:
            atomic_create_json(args.run_dir / "WORKER_FAILURE.json", payload)
        except FileExistsError:
            pass
        return 70
    finally:
        try:
            torch = importlib.import_module("torch")
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except BaseException:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
