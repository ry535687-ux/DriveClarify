"""确定性 JSON 行序列化，剔除权重/密钥/凭据类字段。"""

from __future__ import annotations

import json
from typing import Any

# 明确禁止落盘的敏感键（子串匹配，大小写不敏感）。
_FORBIDDEN_SUBSTRINGS = (
    "state_dict",
    "weight",
    "checkpoint_blob",
    "password",
    "secret",
    "token",
    "credential",
    "api_key",
    "private_key",
)


class SerializationError(RuntimeError):
    """序列化失败；调用方必须 fail-open 并累加 serialization error 计数。"""


def _is_forbidden_key(key: str) -> bool:
    low = key.lower()
    return any(sub in low for sub in _FORBIDDEN_SUBSTRINGS)


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: _sanitize(v)
            for k, v in value.items()
            if not _is_forbidden_key(str(k))
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize(v) for v in value]
    return value


def serialize_record(record: dict[str, Any]) -> str:
    """把一条记录序列化为单行 JSON（append-only 友好）。"""

    try:
        sanitized = _sanitize(record)
        # allow_nan=False：NaN/Inf 必须已在 finite 标记里，不允许写非法 JSON。
        return json.dumps(
            sanitized,
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise SerializationError(str(exc)) from exc
