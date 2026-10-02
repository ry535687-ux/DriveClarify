"""Strict deterministic serialization for offline M3 replay artifacts."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import fields, is_dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping


def _reject_constant(value: str) -> None:
    raise ValueError("non-standard JSON constant rejected: " + value)


def strict_json_loads(raw: str) -> Any:
    if not isinstance(raw, str):
        raise TypeError("strict_json_loads requires str")
    return json.loads(raw, parse_constant=_reject_constant,
                      object_pairs_hook=_reject_duplicate_keys)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key rejected: " + key)
        result[key] = value
    return result


def to_json_compatible(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (Mapping, MappingProxyType)):
        return {str(key): to_json_compatible(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [to_json_compatible(item) for item in value]
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: to_json_compatible(getattr(value, item.name))
                for item in fields(value)}
    return value


def validate_finite_json(value: Any, active: set[int] | None = None) -> None:
    if active is None:
        active = set()
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("nonfinite JSON number rejected")
        return
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in active:
            raise ValueError("cyclic JSON mapping rejected")
        active.add(identity)
        for key, item in value.items():
            if type(key) is not str:
                raise TypeError("JSON object keys must be strings")
            validate_finite_json(item, active)
        active.remove(identity)
        return
    if isinstance(value, (tuple, list)):
        identity = id(value)
        if identity in active:
            raise ValueError("cyclic JSON sequence rejected")
        active.add(identity)
        for item in value:
            validate_finite_json(item, active)
        active.remove(identity)
        return
    raise TypeError("unsupported JSON value: " + type(value).__name__)


def strict_json_dumps(value: Any) -> str:
    compatible = to_json_compatible(value)
    validate_finite_json(compatible)
    return json.dumps(compatible, allow_nan=False, sort_keys=True,
                      separators=(",", ":"), ensure_ascii=False)


def canonical_json_bytes(value: Any) -> bytes:
    return strict_json_dumps(value).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def freeze_json(value: Any) -> Any:
    validate_finite_json(value)
    if isinstance(value, Mapping):
        return MappingProxyType({key: freeze_json(item)
                                 for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(freeze_json(item) for item in value)
    return value


def require_sha256(value: Any, label: str) -> str:
    if (type(value) is not str or len(value) != 64 or
            any(char not in "0123456789abcdef" for char in value)):
        raise ValueError(label + " must be 64 lowercase hexadecimal characters")
    return value


def is_finite_real(value: Any) -> bool:
    return (not isinstance(value, bool) and isinstance(value, (int, float)) and
            (not isinstance(value, float) or math.isfinite(value)))
