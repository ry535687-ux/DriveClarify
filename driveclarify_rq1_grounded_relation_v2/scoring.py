"""在预测哈希锁定后执行布局聚类的 RQ1 评分。"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Iterable, Mapping

VALID_TRUTH = {"TASK_EQUIVALENT", "TASK_DIVERGENT"}
VALID_PRED = VALID_TRUTH | {"UNKNOWN"}


def score(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    data = list(rows)
    if not data:
        raise ValueError("NO_SCORED_ROWS")
    for row in data:
        if row["truth"] not in VALID_TRUTH or row["prediction"] not in VALID_PRED:
            raise ValueError("INVALID_RELATION_VALUE")
    methods = sorted({str(row["method"]) for row in data})
    results = []
    for method in methods:
        subset = [row for row in data if row["method"] == method]
        classes = {}
        for truth in sorted(VALID_TRUTH):
            part = [row for row in subset if row["truth"] == truth]
            classes[truth] = sum(row["prediction"] == truth for row in part) / len(part) if part else None
        observed = [value for value in classes.values() if value is not None]
        determined = [row for row in subset if row["prediction"] != "UNKNOWN"]
        e_to_d = sum(row["truth"] == "TASK_EQUIVALENT" and row["prediction"] == "TASK_DIVERGENT" for row in subset)
        d_to_e = sum(row["truth"] == "TASK_DIVERGENT" and row["prediction"] == "TASK_EQUIVALENT" for row in subset)
        results.append({"method": method, "n": len(subset), "layout_count": len({row["layout_id"] for row in subset}), "balanced_accuracy": sum(observed) / len(observed) if observed else 0.0, "coverage": len(determined) / len(subset), "conditional_accuracy": sum(row["prediction"] == row["truth"] for row in determined) / len(determined) if determined else None, "unknown_count": len(subset) - len(determined), "equivalent_to_divergent": e_to_d, "divergent_to_equivalent": d_to_e})
    return {"row_count": len(data), "layout_count": len({row["layout_id"] for row in data}), "methods": results, "cluster_unit": "layout_id", "unknown_counted_in_primary_denominator": True}


def paired_both_correct(rows: Iterable[Mapping[str, Any]], pair_field: str) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(str(row["method"]), str(row[pair_field]))].append(row)
    return [{"method": key[0], "pair_id": key[1], "member_count": len(group), "both_correct": len(group) == 2 and all(row["prediction"] == row["truth"] for row in group)} for key, group in sorted(groups.items())]
