"""显式三值逻辑；UNKNOWN 在任何操作中都不会偷换成 False。"""

from __future__ import annotations

from collections.abc import Iterable

from .models import TriValue


def tri_from_optional_bool(value: bool | None | str) -> TriValue:
    if value is True:
        return TriValue.TRUE
    if value is False:
        return TriValue.FALSE
    if value is None or value == "UNKNOWN":
        return TriValue.UNKNOWN
    raise TypeError(f"not a three-valued boolean: {value!r}")


def tri_equal(left: object, right: object, *, unknown_tokens: tuple[object, ...] = (None, "UNKNOWN")) -> TriValue:
    if left in unknown_tokens or right in unknown_tokens:
        return TriValue.UNKNOWN
    return TriValue.TRUE if left == right else TriValue.FALSE


def tri_or(values: Iterable[TriValue]) -> TriValue:
    # Kleene OR：存在 TRUE 即为 TRUE；否则只要存在 UNKNOWN 就保留 UNKNOWN。
    values = tuple(values)
    if any(value is TriValue.TRUE for value in values):
        return TriValue.TRUE
    if any(value is TriValue.UNKNOWN for value in values):
        return TriValue.UNKNOWN
    return TriValue.FALSE


def tri_and(values: Iterable[TriValue]) -> TriValue:
    # Kleene AND：存在 FALSE 即为 FALSE；否则只要存在 UNKNOWN 就保留 UNKNOWN。
    values = tuple(values)
    if any(value is TriValue.FALSE for value in values):
        return TriValue.FALSE
    if any(value is TriValue.UNKNOWN for value in values):
        return TriValue.UNKNOWN
    return TriValue.TRUE


def tri_not(value: TriValue) -> TriValue:
    if value is TriValue.TRUE:
        return TriValue.FALSE
    if value is TriValue.FALSE:
        return TriValue.TRUE
    return TriValue.UNKNOWN
