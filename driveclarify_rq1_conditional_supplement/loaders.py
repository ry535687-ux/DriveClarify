"""按来源读取既有记录。只读，不改写原产物。"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Iterator

from . import paths

# ---------------------------------------------------------------- 公共

def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 来源 B

#: B 的模板族与真值：`-C` 两候选绑定不同停车区 → 分歧；`-E` 绑定同一区 → 等价。
#: 真值取自 scenarios/evaluation_only/*_TASK_BINDING.json，与方法输入是同一份
#: 作者化场景绑定；这一重叠在 INDEPENDENCE 段落必须披露。
def load_b_bindings() -> dict[str, dict[str, Any]]:
    bindings: dict[str, dict[str, Any]] = {}
    for path in sorted(paths.SRC_B_BINDINGS.glob("*_TASK_BINDING.json")):
        template = path.name.replace("_TASK_BINDING.json", "")
        payload = read_json(path)
        region_map = payload["candidate_region_map"]
        regions = sorted(set(region_map.values()))
        bindings[template] = {
            "template_key": template,
            "candidate_region_map": region_map,
            "distinct_region_count": len(regions),
            "truth": "TASK_DIVERGENT" if len(regions) == 2 else "TASK_EQUIVALENT",
            "truth_source": str(path.relative_to(paths.REPO)),
            "evaluation_version": payload.get("evaluation_version"),
        }
    return bindings


def _parse_b_run_id(run_id: str) -> dict[str, str]:
    # DCAOV-FV1-REF-C-S01-ABL_FULL
    parts = run_id.split("-")
    family, variant, seed_tag = parts[2], parts[3], parts[4]
    arm = run_id.split("-", 5)[5]
    return {
        "run_id": run_id,
        "family": family,
        "variant": variant,
        "template_key": f"{family}-{variant}",
        "seed_tag": seed_tag,
        "arm": arm,
    }


def load_b_records() -> list[dict[str, Any]]:
    """每个 native run 的首决策：两候选轨迹 + 该 run 自己的保存输入。"""
    bindings = load_b_bindings()
    records: list[dict[str, Any]] = []
    for run_dir in sorted(paths.SRC_B_NATIVE.iterdir()):
        timeline = run_dir / "owner_evidence/ABL_CANDIDATE_TIMELINE.jsonl"
        if not timeline.is_file():
            continue
        meta = _parse_b_run_id(run_dir.name)
        binding = bindings[meta["template_key"]]
        candidates = read_jsonl(timeline)
        config_path = paths.SRC_B_CONFIGS / f"{run_dir.name}.json"
        config = read_json(config_path)
        method_input = config["method_input"]
        decision_path = run_dir / "owner_evidence/ABL_DECISION_TIMELINE.jsonl"
        decisions = read_jsonl(decision_path) if decision_path.is_file() else []
        records.append({
            "source": "B_ABLATION_OVERNIGHT",
            "sample_id": run_dir.name,
            "unit_id": meta["template_key"],
            "layout_id": config.get("ablation", {}).get("protocol_id", "") + ":" + meta["template_key"],
            **meta,
            "truth": binding["truth"],
            "truth_status": "DEFINED",
            "truth_source": binding["truth_source"],
            "candidate_region_map": binding["candidate_region_map"],
            "instruction": method_input.get("instruction"),
            "candidate_texts": [row.get("description") for row in method_input.get("alternatives", [])],
            # 预给定的任务签名：v3 契约把 task_signatures 列为答案字段。
            "given_task_signatures": method_input.get("task_signatures", []),
            # TRAJ_ONLY 臂的配置刻意剥掉 certified / relevant_components —— 这正是该臂的消融本身。
            # 因此该臂保存输入内根本没有任务签名证据，M4/M5 的 UNKNOWN 是输入可用性差异，
            # 不是方法能力差异；两臂不可合并成一个同输入总体。
            "task_signature_evidence_available": bool(
                len(method_input.get("task_signatures", [])) == 2
                and all(row.get("certified") is True for row in method_input.get("task_signatures", []))),
            "trajectories": [row["timed_trajectory"] for row in candidates],
            "candidate_prompt_sha256": [row["forward_evidence"].get("candidate_prompt_sha256") for row in candidates],
            "observation_context_sha256": sorted({row["timed_trajectory"].get("nonlanguage_context_sha256") for row in candidates}),
            "source_frames": sorted({row["timed_trajectory"].get("source_frame") for row in candidates}),
            "metric_config": config.get("ablation", {}).get("metric", {}),
            "recorded_decision": decisions[0] if decisions else None,
            "config_path": str(config_path.relative_to(paths.REPO)),
            "timeline_path": str(timeline.relative_to(paths.REPO)),
        })
    return records


# ---------------------------------------------------------------- 来源 C

def load_c_split(split: str) -> list[dict[str, Any]]:
    inputs = {row["sample_id"]: row for row in read_jsonl(paths.SRC_C_INPUTS / f"{split}_METHOD_INPUTS.jsonl")}
    labels = {row["sample_id"]: row for row in read_jsonl(paths.SRC_C_LABELS / f"{split}_LABELS.jsonl")}
    missing = set(inputs) ^ set(labels)
    if missing:
        raise ValueError(f"C_{split}_SAMPLE_ID_MISMATCH:{sorted(missing)[:3]}")
    rows: list[dict[str, Any]] = []
    for sample_id in sorted(inputs):
        label = labels[sample_id]
        rows.append({
            "source": f"C_RQ1_V3_{split}",
            "split": split,
            "sample_id": sample_id,
            "unit_id": label["unit_id"],
            "layout_id": label["layout_id"],
            "method_input": inputs[sample_id],
            "truth": label["truth"],
            "truth_status": label["truth_status"],
            "evidence_condition": label["evidence_condition"],
            "expected_decidability": label["expected_decidability"],
            "missing_evidence": label["missing_evidence"],
            "relation_config_id": label["relation_config_id"],
            "phrasing_id": label["phrasing_id"],
            "observation_key": label["observation_key"],
            "candidate_texts": [row["text"] for row in inputs[sample_id]["candidates"]],
        })
    return rows


def load_c_frozen_config() -> dict[str, Any]:
    digest = read_json(paths.SRC_C_MANIFESTS / "INFERENCE_CONFIG_DIGEST.json")
    for key in ("config", "inference_config", "method_config"):
        if isinstance(digest.get(key), dict) and "trajectory_threshold_m" in digest[key]:
            return digest[key]
    for value in digest.values():
        if isinstance(value, dict) and "trajectory_threshold_m" in value:
            return value
    raise ValueError("C_FROZEN_CONFIG_NOT_FOUND")


def load_c_traceable_predictions(split: str) -> dict[tuple[str, str], dict[str, Any]]:
    """原轮可追溯预测（attempt 最大者），键为 (method, sample_id)。"""
    candidates = sorted(paths.SRC_C_RESULTS.glob(f"{split}_predictions.attempt*.jsonl"))
    if not candidates:
        candidates = [paths.SRC_C_RESULTS / f"{split}_predictions.jsonl"]
    chosen = candidates[-1]
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for row in read_jsonl(chosen):
        out[(row["method"], row["sample_id"])] = row
    out[("__source__", "__source__")] = {"path": str(chosen.relative_to(paths.REPO))}
    return out


# ---------------------------------------------------------------- 来源 A

def load_a_summary() -> dict[str, Any]:
    primary = read_json(paths.SRC_A_PRIMARY)
    receipts: list[dict[str, Any]] = []
    for run_dir in sorted(paths.SRC_A_RUNS.iterdir()):
        receipt = run_dir / "attempt_01/owner_evidence/RQ1_V2_CONSEQUENCE_DECISION_RECEIPT.json"
        if not receipt.is_file():
            receipts.append({"run_id": run_dir.name, "receipt_present": False})
            continue
        payload = read_json(receipt)
        receipts.append({
            "run_id": run_dir.name,
            "receipt_present": True,
            "ambiguity_status": payload.get("ambiguity_status"),
            "relation": payload.get("comparison", {}).get("relation"),
            "gate_action": payload.get("gate", {}).get("action"),
            "reasonable_interpretation_count": payload.get("reasonable_interpretation_count"),
            # A 的决策明确未使用原始局部路点距离 → 无候选轨迹证据
            "raw_local_waypoint_distance_used": payload.get("raw_local_waypoint_distance_used"),
            "passenger_true_intent_operand_present": payload.get("passenger_true_intent_operand_present"),
            "runtime_true_intent_reads_before_ask": payload.get("runtime_true_intent_reads_before_ask"),
        })
    return {"primary_results": primary, "receipts": receipts}


def load_a_config_task_signatures() -> dict[str, Any]:
    """检查 A 的 cell 配置是否同样预给定 task_signatures。"""
    out: dict[str, Any] = {}
    config_dir = paths.SRC_A / "formal_run_configs"
    for path in sorted(config_dir.glob("*.json")):
        payload = read_json(path)
        method_input = payload.get("method_input", {})
        out[path.stem] = {
            "task_signatures_present": "task_signatures" in method_input,
            "task_signature_count": len(method_input.get("task_signatures", [])),
            "alternatives_count": len(method_input.get("alternatives", [])),
        }
    return out


def load_b_episode_rows() -> list[dict[str, str]]:
    with paths.SRC_B_EPISODES.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))
