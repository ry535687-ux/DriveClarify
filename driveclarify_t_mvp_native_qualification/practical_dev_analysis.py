"""Frozen post-batch validator and descriptive analysis for the 72-episode DEV pilot."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
from statistics import mean, median
from typing import Any, Iterable


METHODS = ("T-B1", "T-B2", "T-B3", "T-B4", "T-B5", "T-B6")
BUCKETS = (
    "T1_BEFORE_COMMITMENT",
    "T2_NEAR_COMMITMENT",
    "T3_POST_COMMIT_RECOVERABLE",
    "T4_NO_SAFE_CURRENT_OPPORTUNITY",
)
BINARY_METRICS = (
    "local_semantic_adaptation_success",
    "final_global_task_completion",
    "commitment_violation",
    "recovery_success",
    "rejoin_success",
    "collision",
    "route_deviation",
    "off_road",
)
CONTINUOUS_METRICS = (
    "max_steering_discontinuity",
    "max_heading_discontinuity_deg",
    "max_jerk_mps3",
    "recovery_time_s",
    "recovery_distance_m",
    "global_planner_calls",
    "local_planner_calls",
    "vla_forwards",
    "planning_latency_ms",
)


def _known(value: Any) -> bool:
    return value is not None and value != "UNKNOWN"


def _summary(rows: Iterable[dict[str, Any]], metric: str) -> dict[str, Any]:
    values = [row["metrics"].get(metric) for row in rows]
    known = [value for value in values if _known(value)]
    result: dict[str, Any] = {
        "known_n": len(known),
        "unknown_n": len(values) - len(known),
    }
    if not known:
        result["value"] = "UNKNOWN"
    elif metric in BINARY_METRICS:
        successes = sum(bool(value) for value in known)
        result.update({"true_n": successes, "false_n": len(known) - successes, "rate": successes / len(known)})
    else:
        numeric = [float(value) for value in known]
        if not all(math.isfinite(value) for value in numeric):
            raise ValueError("NONFINITE_METRIC:" + metric)
        result.update({"mean": mean(numeric), "median": median(numeric), "min": min(numeric), "max": max(numeric)})
    return result


def _paired(rows: list[dict[str, Any]], comparator: str) -> list[dict[str, Any]]:
    by_key = {(row["timing_bucket"], int(row["seed"]), row["method_id"]): row for row in rows}
    output = []
    for bucket in BUCKETS:
        seeds = sorted({int(row["seed"]) for row in rows if row["timing_bucket"] == bucket})
        for seed in seeds:
            left = by_key[(bucket, seed, "T-B6")]
            right = by_key[(bucket, seed, comparator)]
            differences = {}
            for metric in (*BINARY_METRICS, *CONTINUOUS_METRICS):
                a, b = left["metrics"].get(metric), right["metrics"].get(metric)
                differences[metric] = "UNKNOWN" if not (_known(a) and _known(b)) else float(a) - float(b)
            output.append({"timing_bucket": bucket, "seed": seed, "comparison": f"T-B6_vs_{comparator}", "paired_differences_b6_minus_comparator": differences})
    return output


def analyze(raw: dict[str, Any]) -> dict[str, Any]:
    rows = raw.get("episodes")
    if not isinstance(rows, list):
        raise ValueError("RAW_EPISODES_NOT_LIST")
    if len(rows) != 72:
        raise ValueError(f"EXPECTED_72_EPISODES_GOT_{len(rows)}")
    identities = [row["episode_id"] for row in rows]
    if len(set(identities)) != 72:
        raise ValueError("DUPLICATE_EPISODE_ID")
    seeds = sorted({int(row["seed"]) for row in rows})
    if len(seeds) != 3:
        raise ValueError("EXPECTED_EXACTLY_THREE_DEV_SEEDS")
    expected = {(method, bucket, seed) for method in METHODS for bucket in BUCKETS for seed in seeds}
    observed = {(row["method_id"], row["timing_bucket"], int(row["seed"])) for row in rows}
    if observed != expected:
        raise ValueError("DEV_FACTORIAL_NOT_COMPLETE")
    if any(row.get("scientific_retry_count") != 0 for row in rows):
        raise ValueError("SCIENTIFIC_RETRY_PRESENT")
    summaries: dict[str, Any] = {}
    for method in METHODS:
        summaries[method] = {}
        for bucket in (*BUCKETS, "ALL"):
            subset = [row for row in rows if row["method_id"] == method and (bucket == "ALL" or row["timing_bucket"] == bucket)]
            summaries[method][bucket] = {metric: _summary(subset, metric) for metric in (*BINARY_METRICS, *CONTINUOUS_METRICS)}
    terminals = defaultdict(int)
    for row in rows:
        terminals[str(row["terminal_status"])] += 1
    return {
        "schema_version": "driveclarify.rq2.t_mvp_dev_analysis.v1",
        "development_pilot_only": True,
        "episode_count": 72,
        "seeds": seeds,
        "terminal_counts": dict(sorted(terminals.items())),
        "summaries": summaries,
        "paired_t_b6_vs_t_b2": _paired(rows, "T-B2"),
        "paired_t_b6_vs_t_b5": _paired(rows, "T-B5"),
        "inferential_claims": "NOT_PERFORMED_THREE_SEED_EXPLORATORY_PILOT",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    raw = json.loads(Path(args.raw).read_text(encoding="utf-8"))
    result = analyze(raw)
    Path(args.output).write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
