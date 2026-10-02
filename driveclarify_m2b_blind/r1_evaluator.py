"""Evaluator-only R1 gate. This source is never mounted in the policy sandbox."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .r1_lifecycle import assert_evaluator_allowed


def read_evaluator_only_package_after_gate(state_path: Path, evaluator_only_path: Path) -> dict[str, Any]:
    assert_evaluator_allowed(state_path)
    return json.loads(evaluator_only_path.read_text(encoding="utf-8"))
