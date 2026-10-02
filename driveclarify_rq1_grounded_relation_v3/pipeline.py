"""把真实资产装配成方法输入、标签与预测，并做开发集阈值校准（v3）。

装配顺序严格是：真实观测 → 公开地图任务结构 → 候选未来（本轮冻结前向）→ 方法输入
→ 预测落盘 → 锁定 → 才关联标签。本模块不做任何反向流动。

与 v2 的差别：候选未来必须来自**本轮候选文本**的真实前向（缓存键含候选文本），
不再复用历史 A1/B1 plan。v2 那种复用会让"候选文本"与"实际注入文本"不一致。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from . import assets, dataset, labels
from .contracts import ContractError
from .forwards import cache_key_for_candidate, load_plan
from .futures import candidate_future_from_plan, geometric_path_from_plan
from .methods import METHODS, evaluate_method

# 开发集阈值搜索范围与并列规则事先固定，不看结果再改。
TRAJECTORY_THRESHOLD_GRID_M = tuple(round(0.05 * step, 4) for step in range(1, 41))
TRAJECTORY_THRESHOLD_TIE_RULE = "PREFER_SMALLEST_THRESHOLD_AMONG_MAXIMUM_DEV_BALANCED_ACCURACY"
GROUNDING_THRESHOLD_DEFAULT = 0.0


def candidate_futures_for_sample(
    sample: Mapping[str, Any],
    cache_root: Path,
    *,
    checkpoint_sha256: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """为样本装配两个候选未来，来自本轮候选文本的真实前向缓存。

    两个候选共享同一 RGB、自车状态、路线、历史、checkpoint、模式与解码协议；
    只有解释文本不同。缓存缺失时 available=False，由方法侧闭合为 UNKNOWN。
    """
    unit_id = str(sample["unit_id"])
    context = str(sample["observation"]["sha256"])
    keys = [cache_key_for_candidate(sample, index, checkpoint_sha256=checkpoint_sha256) for index in (0, 1)]
    plans = [load_plan(cache_root, unit_id, key) for key in keys]
    if any(plan is None for plan in plans):
        return [], {
            "available": False,
            "reason_code": "CANDIDATE_FORWARD_NOT_YET_COMPUTED",
            "cache_keys": keys,
            "missing_cache_keys": [key for key, plan in zip(keys, plans) if plan is None],
            "candidate_forward_count": 0,
        }
    futures, geometry = [], []
    for plan, key in zip(plans, keys):
        future = candidate_future_from_plan(plan, nonlanguage_context_sha256=context)
        future["candidate_cache_key"] = key
        futures.append(future)
        geometry.append(geometric_path_from_plan(plan))
    injected = [str((plan.get("semantic_payload") or {}).get("prompt") or "") for plan in plans]
    texts = [str(row["text"]) for row in sample["candidates"]]
    evidence = {
        "available": all(row.get("valid") for row in futures),
        "reuse_note": "FRESH_FORWARD_WITH_THIS_ROUND_CANDIDATE_TEXT",
        "candidate_text_matches_forward_injection_text": all(text in prompt for text, prompt in zip(texts, injected)),
        "forward_injection_prompts": injected,
        "geometric_paths": geometry,
        "cache_keys": keys,
        "checkpoint_sha256": str(plans[0].get("checkpoint_sha256")),
        "candidate_forward_count": 2,
        "same_observation_hash": len({str(plan.get("observation_hash")) for plan in plans}) == 1,
        "distinct_speed_plan_hash": len({str(plan.get("speed_plan_hash")) for plan in plans}),
    }
    return futures, evidence


def build_unit_rows(
    unit_id: str,
    *,
    layout_index: int,
    split: str,
    observations: Sequence[Mapping[str, Any]],
    cache_root: Path,
    checkpoint_sha256: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """产生该布局的方法输入与标签记录（两者物理分离返回）。"""
    topology = assets.public_topology(unit_id)
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
        futures, evidence = candidate_futures_for_sample(sample, cache_root, checkpoint_sha256=checkpoint_sha256)
        if futures:
            sample["candidate_futures"] = futures
        sample["candidate_future_evidence"] = evidence
        label_rows.append(labels.label_row(sample, topology=topology))
    return samples, label_rows


def privileged_by_sample(samples: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """为 M6 准备特权签名。仅本函数触碰标注侧解析，M0–M5 输入不含其结果。"""
    value: dict[str, dict[str, Any]] = {}
    topologies: dict[str, Any] = {}
    for sample in samples:
        unit_id = str(sample["unit_id"])
        if unit_id not in topologies:
            topologies[unit_id] = assets.public_topology(unit_id)
        texts = [str(row["text"]) for row in sample["candidates"]]
        try:
            value[str(sample["sample_id"])] = labels.privileged_task_signatures(texts, topology=topologies[unit_id])
        except labels.LabelError:
            continue
    return value


def predict(
    samples: Iterable[Mapping[str, Any]],
    config: Mapping[str, Any],
    *,
    privileged: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """对每个样本运行全部方法，产生无标签预测行。"""
    rows = []
    for sample in samples:
        signature = (privileged or {}).get(str(sample["sample_id"]))
        for method in METHODS:
            result = evaluate_method(method, sample, config, privileged_input=signature)
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
        config = {"trajectory_threshold_m": threshold, "grounding_threshold": GROUNDING_THRESHOLD_DEFAULT,
                  "trajectory_horizon_s": 2.5, "trajectory_step_s": 0.05}
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
    best = max((row["balanced_accuracy"] for row in scored), default=0.0)
    chosen = min((row for row in scored if row["balanced_accuracy"] == best),
                 key=lambda row: row["threshold_m"], default=None)
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
