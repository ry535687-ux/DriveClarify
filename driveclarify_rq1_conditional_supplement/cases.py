"""每种实际存在的关键错误类型，按固定 ID 顺序选一个真实案例。

案例全部来自真实记录，不制作「期望结果」示意数据。
同一观测的多个候选组合不当作多条独立道路：同一 (unit_id, observation) 下
只取排序最小的一个 sample_id 作为代表，并记录该组其余样本数。
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

#: 关键错误类型，固定顺序。
ERROR_TYPES = (
    ("EQUIVALENT_PREDICTED_DIVERGENT", "TASK_EQUIVALENT", "TASK_DIVERGENT"),
    ("DIVERGENT_PREDICTED_EQUIVALENT", "TASK_DIVERGENT", "TASK_EQUIVALENT"),
    ("DIVERGENT_LEFT_UNKNOWN", "TASK_DIVERGENT", "UNKNOWN"),
    ("EQUIVALENT_LEFT_UNKNOWN", "TASK_EQUIVALENT", "UNKNOWN"),
)


def _observation_group(sample_id: str) -> str:
    """同一观测下的候选组合归为一组（C 的 sample_id 含 config/phrasing 段）。"""
    parts = sample_id.split("|")
    if len(parts) >= 3:
        return f"{parts[0]}|{parts[2]}"
    return sample_id


def select_cases(scored: Mapping[str, Sequence[Mapping[str, Any]]],
                 distances: Mapping[str, float | None] | None = None) -> list[dict[str, Any]]:
    """对每个方法 × 每种真实存在的错误类型，取一个代表案例。"""
    cases: list[dict[str, Any]] = []
    for method in sorted(scored):
        rows = scored[method]
        for type_id, truth, prediction in ERROR_TYPES:
            matches = sorted((row for row in rows
                              if row["truth"] == truth and row["prediction"] == prediction),
                             key=lambda row: row["sample_id"])
            if not matches:
                cases.append({
                    "method": method, "error_type": type_id,
                    "occurs_in_this_population": False, "sample_count": 0,
                    "note": "该错误类型在本总体不存在，记 0，不人为补造案例",
                })
                continue
            groups: dict[str, list[Mapping[str, Any]]] = {}
            for row in matches:
                groups.setdefault(_observation_group(row["sample_id"]), []).append(row)
            representative_group = sorted(groups)[0]
            chosen = groups[representative_group][0]
            cases.append({
                "method": method,
                "error_type": type_id,
                "occurs_in_this_population": True,
                "sample_count": len(matches),
                "distinct_observation_group_count": len(groups),
                "representative_sample_id": chosen["sample_id"],
                "representative_unit_id": chosen["unit_id"],
                "truth": truth,
                "prediction": prediction,
                "ask_recommendation": chosen["ask_recommendation"],
                "reason_codes": list(chosen["reason_codes"]),
                "max_distance_m": (distances or {}).get(chosen["sample_id"]),
                "same_observation_siblings_not_counted_as_independent":
                    len(groups[representative_group]) - 1,
                "provenance": "REAL_RECORD_NOT_ILLUSTRATIVE",
            })
    return cases
