"""本轮候选前向 worker：同一冻结观测上，只改解释文本，跑真实 SimLingo forward。

复用既有冻结后端 `SimLingoSensitivityPilotBackend` 与状态恢复纪律；本模块不训练、
不改权重、不接车辆控制，也不读取任何 oracle 字段（无 TASK_BINDING、无正确绑定）。

每个候选文本只前向一次并按缓存键落盘；相同输入复用，不同输入必须重算。
必须在 SimLingo 的 Python 3.8 环境中运行。
"""

from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path("/home/buaa/wrh/DriveClarify")
SIMLINGO = Path("/home/buaa/wrh/simlingo")

# 与历史冻结模板一致的提示词包装；只有 Command 段落随候选解释文本变化。
PROMPT_TEMPLATE = "Current speed: {speed} m/s. Command: {command} What should the ego do next?"
INFERENCE_MODE = "OFFICIAL_DREAMING_CANDIDATE_FORWARD"
# 冻结后端契约：candidate_id 形如 A<digits>/B<digits>，且 interpretation_id 必须等于
# candidate_id[0]，而 source.interpretations 以该组字母为键。因此单个后端实例恰好承载
# 两个不同解释文本（A 与 B）。故按 2 个候选文本一批切分，不修改冻结后端。
CANDIDATES_PER_BACKEND = 2
_SLOT_LABELS = ("A1", "B1")


class WorkerError(RuntimeError):
    pass


def build_prompt(candidate_text: str, speed_mps: float) -> str:
    return PROMPT_TEMPLATE.format(speed=round(float(speed_mps), 1), command=str(candidate_text).strip())


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def run_forwards(
    *,
    package_directory: Path,
    manifest: Mapping[str, Any],
    unit_id: str,
    requests: Sequence[Mapping[str, Any]],
    output_directory: Path,
    run_id: str,
) -> dict[str, Any]:
    """对一个观测包运行 N 个候选文本的前向。

    `requests` 每项形如 {"cache_key": str, "candidate_text": str}。已存在同名
    plan 文件的请求直接跳过（缓存命中），不重复消耗 GPU。
    """
    # 必须在 chdir 之前把路径解析成绝对路径：`_load_model` 依赖 SimLingo 仓库作为工作
    # 目录，若此处仍持有相对路径，产物会落到 SimLingo 仓库内（该仓库须保持只读）。
    package_directory = Path(package_directory).resolve()
    output_directory = Path(output_directory).resolve()
    if str(output_directory) == str(SIMLINGO) or str(output_directory).startswith(str(SIMLINGO) + os.sep):
        raise WorkerError("OUTPUT_DIRECTORY_INSIDE_READ_ONLY_SIMLINGO_REPO:" + str(output_directory))
    output_directory.mkdir(parents=True, exist_ok=True)
    os.chdir(str(SIMLINGO))
    for path in (str(ROOT), str(SIMLINGO), str(SIMLINGO / "team_code")):
        if path not in sys.path:
            sys.path.insert(0, path)

    torch = importlib.import_module("torch")
    from simlingo_training.utils.custom_types import DrivingInput
    from driveclarify_candidate_stability.simlingo_live_adapter import (
        SimLingoCandidateSource,
        SimLingoTensorInputIsolation,
        _input_fingerprint,
    )
    from driveclarify_candidate_stability.simlingo_sensitivity_pilot import (
        SimLingoSensitivityPilotBackend,
        explicit_sensitivity_pilot_config,
    )
    from driveclarify_static_branch.offline_candidate_worker import (
        _buffer_summary,
        _load_model,
        _logical_state,
        _move,
        _parameter_summary,
        _plan_record,
        _restore_buffers,
        _snapshot_buffers,
    )

    if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
        raise WorkerError("CUDA_SINGLE_DEVICE_REQUIRED")

    pending = [row for row in requests if not (output_directory / (str(row["cache_key"]) + "_PLAN.json")).is_file()]
    if not pending:
        return {"status": "ALL_CACHED", "forward_count": 0, "cached_count": len(requests)}

    names = ("camera_images", "image_sizes", "camera_intrinsics", "camera_extrinsics", "vehicle_speed", "target_point", "prompt", "prompt_inference")
    loaded = {name: torch.load(str(package_directory / "model_ready" / (name + ".pt")), map_location="cpu") for name in names}
    common_names = ("camera_images", "image_sizes", "camera_intrinsics", "camera_extrinsics", "vehicle_speed", "target_point")
    device = torch.device("cuda:0")
    common = {name: _move(torch, loaded[name], device) for name in common_names}
    speed_scalar = float(common["vehicle_speed"].detach().float().cpu().reshape(-1)[0].item())
    common_fingerprints = {name: _input_fingerprint(torch, common[name]) for name in common}
    del loaded

    torch.cuda.reset_peak_memory_stats()
    progress_path = output_directory / "WORKER_PROGRESS.json"
    write_json(progress_path, {"schema_version": "driveclarify.rq1_v2_worker_progress.v1", "run_id": run_id, "model_forward_call_count": 0, "status": "MODEL_LOADED_NO_FORWARD"})
    agent, model_identity = _load_model(torch, progress_path, run_id)
    model = agent.model.model
    write_json(output_directory / "MODEL_CHECKPOINT_IDENTITY.json", dict(model_identity, all_modules_eval=all(not module.training for module in model.modules()), model_training_flag=bool(model.training)))

    parameter_baseline = _parameter_summary(torch, model)
    buffer_values = _snapshot_buffers(model)
    buffer_baseline_digest = _buffer_summary(torch, model)["digest"]
    navigation = json.loads((package_directory / "metadata/navigation_state.json").read_text(encoding="utf-8"))
    isolation = SimLingoTensorInputIsolation(torch)
    linkage = {
        "observation_id": "{}:{}:frame:{}".format(unit_id, manifest["run_id"], manifest["source_frame"]),
        "observation_digest": manifest["observation_hash"],
        "source_frame": manifest["source_frame"],
        "freshness_token": manifest["package_content_sha256"],
        "state_digest": navigation["digest"],
    }
    # 按 CANDIDATES_PER_BACKEND=2 切分。槽位标签 A1/B1 只是冻结契约要求的位置标签，
    # 不表示任何语义分组；候选身份始终由 cache_key 承载。不修改冻结后端。
    batches = [pending[index:index + CANDIDATES_PER_BACKEND] for index in range(0, len(pending), CANDIDATES_PER_BACKEND)]

    fairness_rows, forward_count, sequence = [], 0, 0
    baseline_digests = set()
    for batch_index, batch in enumerate(batches, start=1):
        slots = {str(row["cache_key"]): _SLOT_LABELS[position] for position, row in enumerate(batch)}
        # 冻结隔离层要求 source.interpretations 的键恰为 {"A","B"}。候选文本总数为奇数时，
        # 末批只有一个真实候选：此时 B 位用同一文本占位以满足该契约，但**从不前向**
        # （expected_order 只含真实候选），因此不产生任何未计数的 forward。
        prompts = {"A": build_prompt(batch[0]["candidate_text"], speed_scalar)}
        prompts["B"] = build_prompt(batch[1]["candidate_text"], speed_scalar) if len(batch) > 1 else prompts["A"]
        unused_slot_filled = len(batch) == 1
        evidence_root = output_directory / "backend_batches" / "batch{:02d}".format(batch_index)
        evidence_root.mkdir(parents=True, exist_ok=True)
        backend = SimLingoSensitivityPilotBackend(
            agent=agent, driving_input_factory=DrivingInput, label_builder=agent.build_language_label,
            generation_config=explicit_sensitivity_pilot_config(),
            candidates_path=evidence_root / "BACKEND_CANDIDATES.json",
            runtime_audit_path=evidence_root / "BACKEND_RUNTIME_AUDIT.json",
            rng_evidence_path=evidence_root / "BACKEND_RNG_EVIDENCE.json",
            isolation_evidence_path=evidence_root / "BACKEND_INPUT_ISOLATION.json",
            failure_evidence_path=evidence_root / "BACKEND_FAILURE_EVIDENCE.json",
            run_id="{}-B{:02d}".format(run_id, batch_index),
            expected_order=tuple(slots[str(row["cache_key"])] for row in batch),
        )
        if backend.run_read_only_runtime_audit()["status"] != "PASS":
            raise WorkerError("RUNTIME_AUDIT_FAILED")
        baseline_state = _logical_state(torch, model, backend, parameter_baseline, navigation)
        if baseline_state["model_training"]:
            raise WorkerError("MODEL_NOT_IN_EVAL")
        baseline_digests.add(baseline_state["logical_state_digest"])
        if batch_index == 1:
            write_json(output_directory / "BASELINE_STATE.json", {
                "schema_version": "driveclarify.rq1_v2_baseline_state.v1", "run_id": run_id, "unit_id": unit_id,
                "established_once_before_first_forward": True, "state": baseline_state,
                "parameters": parameter_baseline, "buffers": _buffer_summary(torch, model),
                "candidate_independent_common_input_fingerprints": common_fingerprints,
            })
        source = SimLingoCandidateSource(common_inputs=common, interpretations=prompts)
        for row in batch:
            cache_key = str(row["cache_key"])
            slot = slots[cache_key]
            sequence += 1
            # 每个候选前先恢复缓冲区与模型输出，并核验逻辑状态摘要回到基线，
            # 防止运行顺序通过共享状态污染后续候选。
            _restore_buffers(torch, model, buffer_values)
            backend._reset_model_outputs()
            state_before = _logical_state(torch, model, backend, parameter_baseline, navigation)
            if state_before["logical_state_digest"] != baseline_state["logical_state_digest"]:
                raise WorkerError("STATE_RESTORE_VERIFICATION_FAILED:" + cache_key)
            gpu_before = {"allocated_bytes": torch.cuda.memory_allocated(), "reserved_bytes": torch.cuda.memory_reserved(), "max_allocated_bytes": torch.cuda.max_memory_allocated()}
            semantic_payload = {
                "candidate_cache_key": cache_key,
                "candidate_text": str(row["candidate_text"]),
                "prompt": prompts[slot[0]],
                "backend_slot_label": slot,
                "backend_slot_label_is_position_only": True,
                "batch_unused_slot_filled_never_forwarded": unused_slot_filled,
                "injection_positions": ["DrivingInput.prompt", "DrivingInput.prompt_inference"],
                "target_point_policy": "UNCHANGED_FROM_FROZEN_NORMAL_SIMLINGO_INPUT",
                "required_branch_supplied": False,
            }
            record = backend.run_candidate(
                candidate_id=slot, interpretation_id=slot[0], interpretation_text=prompts[slot[0]],
                source=source, input_isolation=isolation, linkage=linkage,
                candidate_metadata={"semantic_payload": semantic_payload, "schedule_position": sequence},
            )
            state_after = _logical_state(torch, model, backend, parameter_baseline, navigation)
            gpu_after = {"allocated_bytes": torch.cuda.memory_allocated(), "reserved_bytes": torch.cuda.memory_reserved(), "max_allocated_bytes": torch.cuda.max_memory_allocated()}
            plan = _plan_record(
                run_id=run_id, unit_id=unit_id, candidate_id=slot, sequence=sequence,
                payload={"semantic_payload": semantic_payload, "sha256": cache_key},
                manifest=manifest, backend=record, state_before=state_before, state_after=state_after,
                gpu_before=gpu_before, gpu_after=gpu_after, topology_sha256=None,
            )
            plan["inference_mode"] = INFERENCE_MODE
            plan["candidate_text"] = str(row["candidate_text"])
            plan["candidate_cache_key"] = cache_key
            plan["not_an_execution_authorization"] = True
            write_json(output_directory / (cache_key + "_PLAN.json"), plan)
            forward_count += 1
            fairness_rows.append({
                "cache_key": cache_key,
                "backend_slot_label": slot,
                "batch_index": batch_index,
                "batch_unused_slot_filled_never_forwarded": unused_slot_filled,
                "state_before_matches_baseline": state_before["logical_state_digest"] == baseline_state["logical_state_digest"],
                "state_after_matches_baseline": state_after["logical_state_digest"] == baseline_state["logical_state_digest"],
                "input_isolation": record["input_isolation_status"],
                "cache_cleared": record["cache_cleared"],
                "target_point_fingerprint": common_fingerprints["target_point"],
            })
        backend.finalize_evidence()
    fairness_pass = all(
        row["state_before_matches_baseline"] and row["state_after_matches_baseline"]
        and row["input_isolation"] == "PASS" and row["cache_cleared"]
        for row in fairness_rows
    )
    result = {
        "schema_version": "driveclarify.rq1_v2_worker_result.v1",
        "status": "COMPLETE" if fairness_pass else "FAIRNESS_FAILED",
        "run_id": run_id, "unit_id": unit_id,
        "forward_count": forward_count, "cached_count": len(requests) - len(pending),
        "fairness": "PASS" if fairness_pass else "FAIL",
        "fairness_records": fairness_rows,
        "same_observation_all_candidates": True,
        "only_candidate_text_changes": True,
        "batch_count": len(batches),
        "candidates_per_backend_limit": CANDIDATES_PER_BACKEND,
        # 全部批次必须落在同一个逻辑基线摘要上，否则批间状态已漂移。
        "single_baseline_digest_across_batches": len(baseline_digests) == 1,
        "model_parameter_digest_unchanged": _parameter_summary(torch, model)["digest"] == parameter_baseline["digest"],
        "model_buffer_digest_unchanged": _buffer_summary(torch, model)["digest"] == buffer_baseline_digest,
        "pid_control_planner_vehicle_control": 0,
        "training": 0,
        "peak_allocated_bytes": torch.cuda.max_memory_allocated(),
    }
    write_json(output_directory / "WORKER_RESULT.json", result)
    if not fairness_pass:
        raise WorkerError("CANDIDATE_FAIRNESS_FAILED")
    return result


def main(argv: Sequence[str] = ()) -> int:
    """入口：worker_spec JSON 路径。"""
    arguments = list(argv or sys.argv[1:])
    if len(arguments) != 1:
        print("usage: gpu_worker.py <WORKER_SPEC.json>", file=sys.stderr)
        return 2
    spec = json.loads(Path(arguments[0]).read_text(encoding="utf-8"))
    result = run_forwards(
        package_directory=Path(spec["package_directory"]),
        manifest=spec["manifest"],
        unit_id=str(spec["unit_id"]),
        requests=spec["requests"],
        output_directory=Path(spec["output_directory"]),
        run_id=str(spec["run_id"]),
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
