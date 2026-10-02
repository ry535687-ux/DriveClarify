"""Append-only JSONL shadow logger; logging has no execution authority."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping


class ShadowLogger:
    @staticmethod
    def entry(
        *,
        context: Mapping[str, Any],
        candidates: Any,
        stability: Mapping[str, Any],
        consequence: Mapping[str, Any],
        decision: Mapping[str, Any],
    ) -> dict:
        return {
            "result_type": "DRIVECLARIFY_OFFLINE_SHADOW_LOG_ENTRY_V1",
            "offline_only": True,
            "context": dict(context),
            "candidates": candidates,
            "stability": dict(stability),
            "consequence": dict(consequence),
            "decision": dict(decision),
            "model_forward_count_added": 0,
            "pid_count_added": 0,
            "control_count_added": 0,
            "control_changed": False,
        }

    @staticmethod
    def append_jsonl(path: Path, entry: Mapping[str, Any]) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = (
            json.dumps(
                entry,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )
        with path.open("a", encoding="utf-8") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
