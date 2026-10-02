"""本轮候选前向的缓存身份与读取：worker 写入侧与 pipeline 读取侧共用同一套键。

键的九个冻结维度由 cache.REQUIRED_CACHE_FIELDS 规定。写入与读取必须走本模块的同一个
函数，任何一侧私自拼键都会导致"相同输入却不复用/不同输入却复用"。

history_sha256 取观测包内容摘要：历史状态被冻结在观测包里，包内容变则历史变。
该选择在 PROTOCOL.md 中如实记录，不声称是独立采样的历史轨迹摘要。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .cache import build_cache_key
from .contracts import ContractError

INFERENCE_MODE = "OFFICIAL_DREAMING_CANDIDATE_FORWARD"
INFERENCE_PARAMETERS = {"do_sample": False, "num_beams": 1, "temperature": 0.0}
PROCESSING_CODE_VERSION = "driveclarify.rq1_v2_futures.v1"


def cache_key_for_candidate(
    sample: Mapping[str, Any],
    candidate_index: int,
    *,
    checkpoint_sha256: str,
) -> str:
    """样本第 candidate_index 个候选文本在本轮的前向缓存键。"""
    candidates = sample["candidates"]
    if candidate_index not in (0, 1):
        raise ContractError("CANDIDATE_INDEX_OUT_OF_RANGE")
    if not str(checkpoint_sha256).strip():
        raise ContractError("CHECKPOINT_SHA256_REQUIRED")
    return build_cache_key({
        "observation_sha256": str(sample["observation"]["sha256"]),
        "task_text": str(sample["raw_instruction"]),
        "candidate_text": str(candidates[candidate_index]["text"]),
        "route_sha256": str(sample["route_context"]["route_id"]),
        "history_sha256": str(sample["observation"]["package_content_sha256"]),
        "checkpoint_sha256": str(checkpoint_sha256),
        "inference_mode": INFERENCE_MODE,
        "inference_parameters": INFERENCE_PARAMETERS,
        "processing_code_version": PROCESSING_CODE_VERSION,
    })


def plan_path(cache_root: Path, unit_id: str, cache_key: str) -> Path:
    return Path(cache_root) / str(unit_id) / (str(cache_key) + "_PLAN.json")


def forward_requests(
    samples,
    *,
    checkpoint_sha256: str,
) -> dict[str, list[dict[str, Any]]]:
    """按 unit 归组，产生去重后的前向请求。

    同一 (观测, 任务文本, 候选文本, 路线, 历史, checkpoint, 模式, 参数, 代码版本) 只请求
    一次；不同输入必须各自请求，绝不为省 GPU 跳过需要重算的候选。
    """
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    for sample in samples:
        unit_id = str(sample["unit_id"])
        for index in (0, 1):
            key = cache_key_for_candidate(sample, index, checkpoint_sha256=checkpoint_sha256)
            grouped.setdefault(unit_id, {})[key] = {
                "cache_key": key,
                "candidate_text": str(sample["candidates"][index]["text"]),
                "observation_key": str(sample["observation_key"]),
                "task_text": str(sample["raw_instruction"]),
            }
    return {unit_id: [rows[key] for key in sorted(rows)] for unit_id, rows in sorted(grouped.items())}


def load_plan(cache_root: Path, unit_id: str, cache_key: str) -> dict[str, Any] | None:
    path = plan_path(cache_root, unit_id, cache_key)
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    # 读取侧再核一次身份：plan 自报的键必须与请求键一致，否则缓存串了。
    recorded = str(value.get("candidate_cache_key") or "")
    if recorded and recorded != str(cache_key):
        raise ContractError("CACHE_KEY_MISMATCH_IN_PLAN:" + recorded)
    return value


def resolve_checkpoint_sha256(cache_root: Path) -> str | None:
    """从已产出的 MODEL_CHECKPOINT_IDENTITY.json 读取本轮实际使用的 checkpoint 摘要。"""
    for path in sorted(Path(cache_root).glob("*/MODEL_CHECKPOINT_IDENTITY.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        for field in ("checkpoint_sha256", "sha256", "checkpoint_digest"):
            if str(value.get(field) or "").strip():
                return str(value[field])
    return None
