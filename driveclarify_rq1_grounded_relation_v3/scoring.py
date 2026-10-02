"""在预测哈希锁定后执行布局聚类的 RQ1 评分（v3）。

与 v2 的差别：v3 的标注 authority 允许 truth=None
（`LABEL_UNDEFINED_UNDER_ALLOWED_INPUTS`，即证据不足子集）。这些行**不进入**主评价
总体的分母，但必须单独报告，绝不静默消失。技术缺失与模型 UNKNOWN 分开计。
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Iterable, Mapping

VALID_TRUTH = {"TASK_EQUIVALENT", "TASK_DIVERGENT"}
VALID_PRED = VALID_TRUTH | {"UNKNOWN"}


def partition_by_label_status(rows: Iterable[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """把行分成"标签有效的主评价总体"与"标签未定义的证据不足子集"。"""
    evaluable, undefined = [], []
    for row in rows:
        (evaluable if row.get("truth") in VALID_TRUTH else undefined).append(dict(row))
    return evaluable, undefined


def evidence_insufficient_report(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """证据不足子集：检验方法是否如实报告不可判定，而不是给出错误的确定判断。

    这里的"错误确定判断"指：真值在允许输入下未定义，方法却输出了 EQUIVALENT/DIVERGENT。
    """
    data = list(rows)
    methods = sorted({str(row["method"]) for row in data})
    per_method = []
    for method in methods:
        subset = [row for row in data if row["method"] == method]
        definite = [row for row in subset if row["prediction"] != "UNKNOWN"]
        per_method.append({
            "method": method,
            "n": len(subset),
            "unknown_count": len(subset) - len(definite),
            "wrong_definite_count": len(definite),
            "wrong_definite_rate": (len(definite) / len(subset)) if subset else None,
            "prediction_distribution": dict(Counter(str(row["prediction"]) for row in subset)),
        })
    return {
        "row_count": len(data),
        "layout_count": len({row["layout_id"] for row in data}),
        "criterion": "TRUTH_UNDEFINED_UNDER_ALLOWED_INPUTS_SO_ANY_DEFINITE_PREDICTION_IS_WRONG",
        "methods": per_method,
    }


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
