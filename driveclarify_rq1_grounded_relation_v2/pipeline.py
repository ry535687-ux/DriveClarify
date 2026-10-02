"""把真实资产装配成方法输入、标签与预测，并做开发集阈值校准。

装配顺序严格是：真实观测 → 公开地图任务结构 → 候选未来（冻结前向）→ 方法输入
→ 预测落盘 → 锁定 → 才关联标签。本模块不做任何反向流动。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from . import assets, dataset, labels
from .cache import build_cache_key
from .contracts import ContractError, digest
from .futures import candidate_future_from_plan, geometric_path_from_plan
from .methods import METHODS, evaluate_method

# 开发集阈值搜索范围与并列规则事先固定，不看结果再改。
TRAJECTORY_THRESHOLD_GRID_M = tuple(round(0.05 * step, 4) for step in range(1, 41))
TRAJECTORY_THRESHOLD_TIE_RULE = "PREFER_SMALLEST_THRESHOLD_AMONG_MAXIMUM_DEV_BALANCED_ACCURACY"
GROUNDING_THRESHOLD_DEFAULT = 0.0


def historical_plan_paths(unit_id: str) -> list[Path]:
    """定位该单元历史 stage_b 的冻结候选前向记录（真实 GPU forward）。"""
    for run_result in sorted(assets.V3_CAMPAIGN.glob("stage_b/runs/*/RUN_RESULT.json")):
        value = assets.read_json(run_result)
        if str(value.get("unit_id")) == unit_id and value.get("final_status") == "COMPLETE_SIX_PLANS":
            return [run_result.parent / name for name in ("A1_PLAN.json", "B1_PLAN.json")]
    return []


def candidate_futures_for_sample(
    sample: Mapping[str, Any],
    plan_paths: Sequence[Path],
    *,
    reuse_note: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """为样本装配两个候选未来。

    `reuse_note` 必须说明这些前向的真实来源。当候选文本与产生前向时的注入文本不同，
    该事实必须写进证据，不能声称是本轮候选文本的前向。
    """
    if len(plan_paths) != 2:
        return [], {"available": False, "reason_code": "CANDIDATE_FORWARD_RECORDS_UNAVAILABLE"}
    context = str(sample["observation"]["sha256"])
    plans = [assets.read_json(path) for path in plan_paths]
    futures, geometry, cache_keys = [], [], []
    for plan, path in zip(plans, plan_paths):
        future = candidate_future_from_plan(plan, nonlanguage_context_sha256=context)
        future["forward_record_path"] = str(path)
        futures.append(future)
        geometry.append(geometric_path_from_plan(plan))
        cache_keys.append(build_cache_key({
            "observation_sha256": context,
            "task_text": str(sample["raw_instruction"]),
            "candidate_text": str(plan.get("semantic_payload", {}).get("prompt") or path.stem),
            "route_sha256": str(sample["route_context"]["route_id"]),
            "history_sha256": digest(plan.get("history_before") or []),
            "checkpoint_sha256": str(plan.get("checkpoint_sha256")),
            "inference_mode": "OFFICIAL_DREAMING_CANDIDATE_FORWARD",
            "inference_parameters": {"do_sample": False, "num_beams": 1, "temperature": 0.0},
            "processing_code_version": "driveclarify.rq1_v2_futures.v1",
        }))
    evidence = {
        "available": all(row.get("valid") for row in futures),
        "reuse_note": reuse_note,
        "candidate_text_matches_forward_injection_text": False,
        "forward_injection_texts": [str(plan.get("semantic_payload", {}).get("prompt")) for plan in plans],
        "geometric_paths": geometry,
        "cache_keys": cache_keys,
        "checkpoint_sha256": str(plans[0].get("checkpoint_sha256")),
        "candidate_forward_count": 2,
    }
    return futures, evidence


def build_unit_rows(
    unit_id: str,
    *,
    layout_index: int,
    split: str,
    observations: Sequence[Mapping[str, Any]],
    reuse_plan_paths: Sequence[Path] = (),
    reuse_note: str = "",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """产生该布局的方法输入与标签记录（两者物理分离返回）。"""
    topology = assets.public_topology(unit_id)
    task_binding = assets.privileged_task_binding(unit_id)
    ego_states, navigation_states, rows = [], [], []
    for observation in observations:
        ego, navigation = assets.observation_states(observation["package_directory"])
        ego_states.append(ego)
        navigation_states.append(navigation)
        rows.append(dict(observation, speed_mps=assets.observation_speed(observation["package_directory"])))
    family = dataset.family_for_layout(layout_index)
    samples = dataset.build_layout_samples(
        unit_id=unit_id,
        layout_id=f"{topology['town']}:junction:{topology['junction_id']}",
        family=family,
        observations=rows,
        topology=topology,
        ego_states=ego_states,
        navigation_states=navigation_states,
    )
    label_rows = []
    for sample in samples:
        sample["split"] = split
        futures, evidence = candidate_futures_for_sample(sample, list(reuse_plan_paths), reuse_note=reuse_note)
        if futures:
            sample["candidate_futures"] = futures
        sample["candidate_future_evidence"] = evidence
        label_rows.append(labels.label_row(sample, task_binding=task_binding, topology=topology))
    return samples, label_rows


def predict(
    samples: Iterable[Mapping[str, Any]],
    config: Mapping[str, Any],
    *,
    privileged_by_sample: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """对每个样本运行全部方法，产生无标签预测行。"""
    rows = []
    for sample in samples:
        privileged = (privileged_by_sample or {}).get(str(sample["sample_id"]))
        for method in METHODS:
            result = evaluate_method(method, sample, config, privileged_input=privileged)
            rows.append({
                "sample_id": str(sample["sample_id"]),
                "method": method,
                "prediction": result.relation.value,
                "ask_recommendation": result.ask.value,
                "reason_codes": list(result.reason_codes),
                "evidence": result.evidence,
                "inference_seconds": result.inference_seconds,
                "candidate_forward_count": result.candidate_forward_count,
                "grounding_forward_count": result.grounding_forward_count,
            })
    return rows


def calibrate_trajectory_threshold(
    samples: Sequence[Mapping[str, Any]],
    truth_by_sample: Mapping[str, str],
) -> dict[str, Any]:
    """在开发集上按事先固定的网格与并列规则选轨迹阈值。"""
    scored = []
    for threshold in TRAJECTORY_THRESHOLD_GRID_M:
        config = {"trajectory_threshold_m": threshold, "grounding_threshold": GROUNDING_THRESHOLD_DEFAULT}
        per_class: dict[str, list[int]] = {"TASK_EQUIVALENT": [], "TASK_DIVERGENT": []}
        for sample in samples:
            truth = truth_by_sample.get(str(sample["sample_id"]))
            if truth not in per_class:
                continue
            result = evaluate_method("M3_TRAJECTORY_ONLY", sample, config)
            per_class[truth].append(int(result.relation.value == truth))
        observed = [sum(values) / len(values) for values in per_class.values() if values]
        scored.append({
            "threshold_m": threshold,
            "balanced_accuracy": sum(observed) / len(observed) if observed else 0.0,
            "class_counts": {key: len(values) for key, values in per_class.items()},
        })
    best = max(row["balanced_accuracy"] for row in scored) if scored else 0.0
    chosen = min((row for row in scored if row["balanced_accuracy"] == best), key=lambda row: row["threshold_m"], default=None)
    return {
        "objective": "DEV_BALANCED_ACCURACY_OF_M3_TRAJECTORY_ONLY",
        "search_grid_m": list(TRAJECTORY_THRESHOLD_GRID_M),
        "tie_rule": TRAJECTORY_THRESHOLD_TIE_RULE,
        "scored": scored,
        "selected_threshold_m": None if chosen is None else chosen["threshold_m"],
        "selected_balanced_accuracy": None if chosen is None else chosen["balanced_accuracy"],
        "calibrated_on": "DEV_ONLY",
        "backbone_or_perception_trained": False,
    }


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n")
            count += 1
    return {"path": str(path), "row_count": count, "sha256": assets.sha256_file(path)}
