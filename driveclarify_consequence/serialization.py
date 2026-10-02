"""Canonical serialization — deterministic JSON for byte-identical replay outputs.

Guarantees: stable key ordering (sort_keys), UTF-8, explicit float formatting, NaN/Infinity REJECTED
(allow_nan=False), no trailing whitespace drift. Used for every output file so identical semantic
input yields identical bytes. Pure.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any


def _check_finite(obj: Any) -> None:
    """Recursively reject NaN / Infinity before serialization (fail-closed)."""
    if isinstance(obj, float):
        if not math.isfinite(obj):
            raise ValueError("SERIALIZATION_REJECTS_NON_FINITE_FLOAT")
    elif isinstance(obj, dict):
        for v in obj.values():
            _check_finite(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _check_finite(v)


def canonical_json(obj: Any) -> str:
    """Return canonical JSON: sorted keys, compact separators, no NaN/Inf, ensure_ascii=False."""
    _check_finite(obj)
    return json.dumps(obj, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False)


def write_json(path: str | Path, obj: Any) -> None:
    """Write a single object as canonical pretty JSON (sorted keys) with trailing newline."""
    _check_finite(obj)
    text = json.dumps(obj, sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False)
    Path(path).write_text(text + "\n", encoding="utf-8")


def write_jsonl(path: str | Path, rows: list[Any]) -> None:
    """Write one canonical JSON object per line (stable ordering)."""
    lines = [canonical_json(r) for r in rows]
    Path(path).write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
