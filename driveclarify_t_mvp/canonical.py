"""Deterministic canonical JSON used by every T-MVP evidence object."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from enum import Enum
import hashlib
import json
import math
from typing import Any, Iterable, Mapping


class CanonicalizationError(ValueError):
    """Raised when a value has no frozen canonical representation."""


def _normalize(value: Any, *, excluded_keys: frozenset[str]) -> Any:
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, Enum):
        return value.value
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CanonicalizationError("CANONICAL_FLOAT_NONFINITE")
        return value.hex()
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalizationError("CANONICAL_MAPPING_KEY_NOT_STRING")
            if key not in excluded_keys:
                normalized[key] = _normalize(item, excluded_keys=excluded_keys)
        return normalized
    if isinstance(value, (list, tuple)):
        return [_normalize(item, excluded_keys=excluded_keys) for item in value]
    if isinstance(value, (bytes, bytearray, memoryview, set, frozenset)):
        raise CanonicalizationError("CANONICAL_VALUE_TYPE_FORBIDDEN")
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _normalize(value.to_dict(), excluded_keys=excluded_keys)
    raise CanonicalizationError(
        "CANONICAL_VALUE_TYPE_UNSUPPORTED:" + type(value).__qualname__
    )


def canonical_data(
    value: Any, *, exclude: Iterable[str] = ("canonical_sha256",)
) -> Any:
    """Return the JSON-compatible normalized value.

    All floats become their exact IEEE-754 hexadecimal spelling.  Ordered
    sequences remain ordered; mappings are sorted by ``json.dumps``.
    """

    return _normalize(value, excluded_keys=frozenset(exclude))


def canonical_bytes(
    value: Any, *, exclude: Iterable[str] = ("canonical_sha256",)
) -> bytes:
    return json.dumps(
        canonical_data(value, exclude=exclude),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_sha256(
    value: Any, *, exclude: Iterable[str] = ("canonical_sha256",)
) -> str:
    return hashlib.sha256(canonical_bytes(value, exclude=exclude)).hexdigest()


def bytes_sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()
