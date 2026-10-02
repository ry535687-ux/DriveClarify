"""候选推理缓存身份；缺任一冻结维度即拒绝构造。"""

from __future__ import annotations

from typing import Any, Mapping

from .contracts import ContractError, digest

REQUIRED_CACHE_FIELDS = (
    "observation_sha256",
    "task_text",
    "candidate_text",
    "route_sha256",
    "history_sha256",
    "checkpoint_sha256",
    "inference_mode",
    "inference_parameters",
    "processing_code_version",
)


def build_cache_key(value: Mapping[str, Any]) -> str:
    missing = [field for field in REQUIRED_CACHE_FIELDS if field not in value]
    if missing:
        raise ContractError("CACHE_KEY_MISSING:" + ",".join(missing))
    payload = {field: value[field] for field in REQUIRED_CACHE_FIELDS}
    if not all(str(payload[field]).strip() for field in REQUIRED_CACHE_FIELDS if field != "inference_parameters"):
        raise ContractError("CACHE_KEY_EMPTY_FIELD")
    if not isinstance(payload["inference_parameters"], Mapping):
        raise ContractError("CACHE_KEY_PARAMETERS_INVALID")
    return digest(payload)
