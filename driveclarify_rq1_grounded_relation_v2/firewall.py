"""方法输入、标签 authority 与预测锁之间的物理边界。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from .contracts import assert_label_firewall
from .lock import verify_lock


class FirewallError(RuntimeError):
    pass


def read_method_inputs(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    for row in rows:
        assert_label_firewall(row)
    return rows


def join_after_lock(predictions_path: Path, lock_path: Path, labels_path: Path) -> list[dict[str, Any]]:
    verify_lock(predictions_path, lock_path)
    predictions = [json.loads(line) for line in predictions_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    labels = [json.loads(line) for line in labels_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    by_id = {row["sample_id"]: row for row in labels}
    if len(by_id) != len(labels) or {row["sample_id"] for row in predictions} != set(by_id):
        raise FirewallError("PREDICTION_LABEL_ID_MISMATCH")
    return [{**row, "truth": by_id[row["sample_id"]]["truth"], "layout_id": by_id[row["sample_id"]]["layout_id"], "family": by_id[row["sample_id"]]["family"]} for row in predictions]
