"""Training Monitor interface (contract + reader). No training is started this round.

Reads (future) training/validation/closed-loop metric streams and emits JSONL/CSV/PNG
summaries. The three metric families are kept STRICTLY separate: training loss is never
presented as a closed-loop safety improvement. ACT/ASK/WAIT/FALLBACK and UNKNOWN-rate are
reported as distributions; a falling training loss says nothing about closed-loop safety.

This module does NOT assume training authorization and does NOT launch anything. It defines
the metric schema, a reader for an existing metrics.jsonl, and pure summarizers/plotters.
"""

from __future__ import annotations

import csv
import json
import os
from typing import Any, Optional

# Three DISTINCT metric families (must never be merged into one "score").
TRAINING_METRICS = ("step", "epoch", "train_loss", "learning_rate", "grad_norm",
                    "gpu_memory_mib", "samples_per_sec", "checkpoint")
VALIDATION_METRICS = ("epoch", "val_loss", "val_task_completion", "val_unknown_rate")
CLOSED_LOOP_METRICS = ("episode_id", "task_completion", "collision", "rule_violations",
                       "query_rate", "unnecessary_query", "act", "ask", "wait", "fallback",
                       "unknown_rate", "source_run_id", "source_hash")

METRIC_FAMILIES = {
    "training": TRAINING_METRICS,
    "validation": VALIDATION_METRICS,
    "closed_loop": CLOSED_LOOP_METRICS,
}


class TrainingMonitorContract:
    """Static description of what the monitor reads/emits; consumed by tests + reports."""

    families = METRIC_FAMILIES
    separation_rule = ("training loss, validation metrics, and closed-loop rollout metrics "
                       "are separate families; a training-loss decrease is NEVER rendered as "
                       "a closed-loop safety improvement")
    outputs = ("metrics.jsonl", "summary.csv", "epoch_curves.png",
               "sample_episode.mp4", "sample_bev_decision.png", "tensorboard(optional)")
    authorization = "TRAINING NOT AUTHORIZED THIS ROUND; monitor is read-only interface only"

    def to_dict(self) -> dict[str, Any]:
        return {"families": {k: list(v) for k, v in self.families.items()},
                "separation_rule": self.separation_rule,
                "outputs": list(self.outputs),
                "authorization": self.authorization}


def read_metrics_jsonl(path: str) -> list[dict[str, Any]]:
    """Read an existing metrics.jsonl (if any). Never creates training data."""
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:  # noqa: BLE001
                continue
    return rows


def summarize_family(rows: list[dict[str, Any]], family: str) -> dict[str, Any]:
    """Summaries stay within one family; no cross-family blending."""
    keys = METRIC_FAMILIES[family]
    present = [r for r in rows if any(k in r for k in keys)]
    return {"family": family, "rows": len(present), "keys": list(keys)}


def write_csv_summary(rows: list[dict[str, Any]], out_csv: str, family: str) -> str:
    keys = METRIC_FAMILIES[family]
    with open(out_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(keys)
        for r in rows:
            w.writerow([r.get(k) for k in keys])
    return out_csv


def decision_distribution(closed_loop_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """ACT/ASK/WAIT/FALLBACK + UNKNOWN-rate distribution from closed-loop rows (honest)."""
    dist = {"act": 0, "ask": 0, "wait": 0, "fallback": 0}
    ur = []
    for r in closed_loop_rows:
        for k in ("act", "ask", "wait", "fallback"):
            dist[k] += int(r.get(k, 0) or 0)
        if r.get("unknown_rate") is not None:
            ur.append(r["unknown_rate"])
    dist["unknown_rate_mean"] = round(sum(ur) / len(ur), 4) if ur else None
    dist["note"] = "distribution only; training-loss trends are NOT closed-loop safety"
    return dist
