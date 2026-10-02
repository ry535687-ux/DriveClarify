"""Frozen deterministic cluster-bootstrap and paired statistics for R3."""

from __future__ import annotations

import math
import random
from collections import defaultdict
from typing import Any, Callable, Mapping, Sequence

from .metrics import compute_action_metrics


BOOTSTRAP_RESAMPLES = 10_000
BOOTSTRAP_SEED = 20_260_804


def _percentile(values: Sequence[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * probability
    low = int(math.floor(index)); high = int(math.ceil(index))
    if low == high:
        return ordered[low]
    weight = index - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


def cluster_bootstrap(rows: Sequence[Mapping[str, Any]],
                      gold: Mapping[str, Mapping[str, Any]],
                      cluster_key: Callable[[Mapping[str, Any]], str],
                      runtime: Mapping[str, Mapping[str, Any]] | None = None,
                      *, resamples: int = BOOTSTRAP_RESAMPLES,
                      seed: int = BOOTSTRAP_SEED) -> dict[str, Any]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[cluster_key(row)].append(row)
    clusters = sorted(grouped)
    if not clusters:
        return {"cluster_count": 0, "resamples": resamples, "seed": seed, "intervals": {}}
    rng = random.Random(seed)
    sampled: dict[str, list[float]] = defaultdict(list)
    fields = ("accuracy", "macro_f1", "balanced_accuracy", "query_rate", "fallback_rate", "wrong_goal_rate", "mean_regret")
    for _ in range(resamples):
        draw: list[Mapping[str, Any]] = []
        for _index in clusters:
            draw.extend(grouped[rng.choice(clusters)])
        metrics = compute_action_metrics(draw, gold, runtime)
        for field in fields:
            if metrics[field] is not None:
                sampled[field].append(float(metrics[field]))
    intervals = {field: {"lower": _percentile(values, 0.025), "upper": _percentile(values, 0.975)}
                 for field, values in sorted(sampled.items())}
    return {"cluster_count": len(clusters), "resamples": resamples, "seed": seed,
            "interval": "95_PERCENTILE", "intervals": intervals}


def exact_mcnemar(main_rows: Sequence[Mapping[str, Any]], other_rows: Sequence[Mapping[str, Any]],
                  gold: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    main = {row["case_id"]: row for row in main_rows}
    other = {row["case_id"]: row for row in other_rows}
    if set(main) != set(other):
        raise ValueError("R3_PAIRED_CASE_SET_MISMATCH")
    main_only = other_only = 0
    for case_id in sorted(main):
        expected = gold[case_id]["gold_action_type"]
        m_ok = main[case_id]["selected_action"] == expected
        o_ok = other[case_id]["selected_action"] == expected
        main_only += m_ok and not o_ok
        other_only += o_ok and not m_ok
    discordant = main_only + other_only
    if discordant == 0:
        p_value = 1.0
    else:
        tail = sum(math.comb(discordant, k) for k in range(0, min(main_only, other_only) + 1)) / (2 ** discordant)
        p_value = min(1.0, 2 * tail)
    return {"main_correct_other_wrong": main_only, "main_wrong_other_correct": other_only,
            "discordant": discordant, "exact_two_sided_p": p_value}


def paired_cluster_bootstrap_difference(
        main_rows: Sequence[Mapping[str, Any]], other_rows: Sequence[Mapping[str, Any]],
        gold: Mapping[str, Mapping[str, Any]],
        cluster_key: Callable[[Mapping[str, Any]], str],
        runtime: Mapping[str, Mapping[str, Any]] | None = None,
        *, resamples: int = BOOTSTRAP_RESAMPLES, seed: int = BOOTSTRAP_SEED) -> dict[str, Any]:
    main = {row["case_id"]: row for row in main_rows}
    other = {row["case_id"]: row for row in other_rows}
    if set(main) != set(other):
        raise ValueError("R3_PAIRED_BOOTSTRAP_CASE_SET_MISMATCH")
    clusters: dict[str, list[str]] = defaultdict(list)
    for case_id, row in main.items():
        clusters[cluster_key(row)].append(case_id)
    names = sorted(clusters)
    if not names:
        return {"cluster_count": 0, "resamples": resamples, "seed": seed, "intervals": {}}
    rng = random.Random(seed)
    sampled: dict[str, list[float]] = defaultdict(list)
    fields = ("accuracy", "macro_f1", "balanced_accuracy", "fallback_rate",
              "wrong_goal_rate", "mean_regret")
    for _ in range(resamples):
        case_draw: list[str] = []
        for _index in names:
            case_draw.extend(clusters[rng.choice(names)])
        main_metrics = compute_action_metrics([main[case_id] for case_id in case_draw], gold, runtime)
        other_metrics = compute_action_metrics([other[case_id] for case_id in case_draw], gold, runtime)
        for field in fields:
            left, right = main_metrics[field], other_metrics[field]
            if left is not None and right is not None:
                sampled[field].append(float(left) - float(right))
    return {
        "cluster_count": len(names), "resamples": resamples, "seed": seed,
        "interval": "95_PERCENTILE_PAIRED_DIFFERENCE_MAIN_MINUS_OTHER",
        "intervals": {field: {"lower": _percentile(values, 0.025),
                               "upper": _percentile(values, 0.975)}
                      for field, values in sorted(sampled.items())},
    }


def holm_adjust(p_values: Mapping[str, float]) -> dict[str, float]:
    ordered = sorted(p_values.items(), key=lambda item: (item[1], item[0]))
    adjusted: dict[str, float] = {}
    running = 0.0
    total = len(ordered)
    for index, (name, value) in enumerate(ordered):
        running = max(running, min(1.0, (total - index) * value))
        adjusted[name] = running
    return dict(sorted(adjusted.items()))
