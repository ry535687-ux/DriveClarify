"""Strict deterministic JSON boundary for the offline reference package."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import fields, is_dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping


class PublicInputError(ValueError):
    """Expected rejection raised only while validating untrusted input."""

    def __init__(self, processing_result: str, audit_reason: str, detail: str):
        super().__init__(detail)
        self.processing_result = processing_result
        self.audit_reason = audit_reason
        self.detail = detail


def _reject_nonstandard_json_constant(value: str) -> None:
    raise ValueError("non-standard JSON numeric constant rejected: " + value)


def strict_json_loads(raw_text: str) -> Any:
    if not isinstance(raw_text, str):
        raise TypeError("strict_json_loads requires str input")
    return json.loads(raw_text, parse_constant=_reject_nonstandard_json_constant)


def to_json_compatible(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, MappingProxyType):
        return {str(key): to_json_compatible(item) for key, item in value.items()}
    if isinstance(value, Mapping):
        return {str(key): to_json_compatible(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [to_json_compatible(item) for item in value]
    if isinstance(value, list):
        return [to_json_compatible(item) for item in value]
    if is_dataclass(value) and not isinstance(value, type):
        custom = getattr(value, "to_dict", None)
        if callable(custom):
            return custom()
        return {field.name: to_json_compatible(getattr(value, field.name))
                for field in fields(value)}
    return value


def strict_json_dumps(value: Any, *, ensure_ascii: bool = False) -> str:
    return json.dumps(
        to_json_compatible(value),
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=ensure_ascii,
    )


def canonical_json_bytes(value: Any) -> bytes:
    return strict_json_dumps(value).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def validate_finite_json_numbers(value: Any) -> None:
    """Reject booleans only when used as numbers; reject all nonfinite floats.

    Boolean JSON values remain legal payload data. Numeric-domain call sites use
    ``is_finite_real`` when a value is a temporal operand.
    """
    _validate_json_value(value, set())


def _validate_json_value(value: Any, active: set[int]) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise PublicInputError(
                "REJECTED_INVALID_TEMPORAL_NUMERIC_DOMAIN",
                "INVALID_NONFINITE_MONOTONIC_TIME",
                "nonfinite JSON number rejected",
            )
        return
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in active:
            raise PublicInputError(
                "REJECTED_INVALID_PAYLOAD_TYPE", "INVALID_PAYLOAD_TYPE",
                "cyclic mapping is not JSON-compatible")
        active.add(identity)
        for key, item in value.items():
            if not isinstance(key, str):
                raise PublicInputError(
                    "REJECTED_INVALID_PAYLOAD_TYPE", "INVALID_PAYLOAD_TYPE",
                    "JSON object keys must be strings")
            _validate_json_value(item, active)
        active.remove(identity)
    elif isinstance(value, (list, tuple)):
        identity = id(value)
        if identity in active:
            raise PublicInputError(
                "REJECTED_INVALID_PAYLOAD_TYPE", "INVALID_PAYLOAD_TYPE",
                "cyclic sequence is not JSON-compatible")
        active.add(identity)
        for item in value:
            _validate_json_value(item, active)
        active.remove(identity)
    else:
        raise PublicInputError(
            "REJECTED_INVALID_PAYLOAD_TYPE", "INVALID_PAYLOAD_TYPE",
            "unsupported JSON payload value: " + type(value).__name__)
