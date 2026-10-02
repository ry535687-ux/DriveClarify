"""统计与聚合。所有分析均为补充机制分析 / 探索性分析，非预注册。

聚类单位一律为真实基础布局/模板，不把种子、逐帧、候选组合、mask 变体当独立布局。
UNKNOWN 不从关系正确率分母中删除；只有一种真实类别的分组不强算双类别均衡正确率。
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any, Iterable, Mapping, Sequence

from .judges import (ASK_RECOMMENDED, NO_QUERY_NEEDED, RELATION_DIVERGENT,
                     RELATION_EQUIVALENT, RELATION_UNKNOWN, UNRESOLVED)

TRAJ_CLOSE = "TRAJECTORY_CLOSE"
TRAJ_DIFFERENT = "TRAJECTORY_DIFFERENT"

FOUR_CELL_ORDER = (
    f"{TRAJ_CLOSE}__{RELATION_EQUIVALENT}",
    f"{TRAJ_DIFFERENT}__{RELATION_EQUIVALENT}",
    f"{TRAJ_CLOSE}__{RELATION_DIVERGENT}",
    f"{TRAJ_DIFFERENT}__{RELATION_DIVERGENT}",
)

#: 事后阈值敏感性倍数，事前固定，整组展示，不选最好一点替换主结果。
SENSITIVITY_MULTIPLIERS = (0.5, 0.75, 1.0, 1.25, 1.5)


def wilson_interval(successes: float, total: int, z: float = 1.96) -> tuple[float, float] | None:
    """Wilson 区间。total=0 返回 None，不填 0。"""
    if total <= 0:
        return None
    phat = successes / total
    denominator = 1.0 + z * z / total
    center = (phat + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(phat * (1 - phat) / total + z * z / (4 * total * total)) / denominator
    return max(0.0, center - margin), min(1.0, center + margin)


def balanced_accuracy(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """按真实类别取每类正确率的平均。UNKNOWN 计为该类中的错误，保留在分母内。

    只有一种真实类别时不强算双类别均衡正确率，返回 present_classes 供报告说明。
    """
    by_truth: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        by_truth[row["truth"]].append(row)
    per_class: dict[str, Any] = {}
    for truth, subset in sorted(by_truth.items()):
        correct = sum(1 for row in subset if row["prediction"] == truth)
        per_class[truth] = {
            "n": len(subset),
            "correct": correct,
            "recall": correct / len(subset),
            "unknown": sum(1 for row in subset if row["prediction"] == RELATION_UNKNOWN),
            "wilson95": wilson_interval(correct, len(subset)),
        }
    present = sorted(per_class)
    value: float | None = None
    if len(present) >= 2:
        value = sum(per_class[name]["recall"] for name in present) / len(present)
    return {
        "balanced_accuracy": value,
        "balanced_accuracy_applicable": len(present) >= 2,
        "present_classes": present,
        "per_class": per_class,
        "degenerate_single_class_note": None if len(present) >= 2 else
            "只有一种真实类别，未强算双类别均衡正确率",
    }


#: 询问策略参照，本身不输出任务关系；其关系分类指标一律记 N/A，不填 0。
POLICY_BASELINES_WITHOUT_RELATION_OUTPUT = frozenset({"M0_ALWAYS_ASK", "M1_NEVER_ASK"})


def relation_metrics(rows: Sequence[Mapping[str, Any]], *, method: str | None = None) -> dict[str, Any]:
    """关系分类指标。分母为全部有真值样本，UNKNOWN 不被悄悄删除。

    对 Always/Never Ask 这类不产生关系输出的策略参照，关系指标返回 N/A 而非 0，
    以免把「没有这项输出」误读成「这项输出全错」。
    """
    if method in POLICY_BASELINES_WITHOUT_RELATION_OUTPUT:
        return {
            "n": len(rows),
            "layout_or_template_count": len({row["unit_id"] for row in rows}),
            "relation_metrics_applicable": False,
            "relation_metrics_status": "N/A_NO_RELATION_OUTPUT",
            "balanced_accuracy": None,
            "balanced_accuracy_applicable": False,
            "present_classes": sorted({row["truth"] for row in rows}),
            "per_class": {},
            "overall_accuracy_unknown_counted_wrong": None,
            "unknown_count": None,
            "unknown_rate": None,
            "determined_coverage": None,
            "conditional_accuracy_on_determined": None,
            "equivalent_predicted_divergent": None,
            "divergent_predicted_equivalent": None,
            "prediction_distribution": {},
            "note": "询问策略参照，不产生任务关系输出；关系分类指标记 N/A，不填 0。",
        }
    total = len(rows)
    determined = [row for row in rows if row["prediction"] != RELATION_UNKNOWN]
    unknown = total - len(determined)
    equivalent_as_divergent = sum(
        1 for row in rows if row["truth"] == RELATION_EQUIVALENT and row["prediction"] == RELATION_DIVERGENT)
    divergent_as_equivalent = sum(
        1 for row in rows if row["truth"] == RELATION_DIVERGENT and row["prediction"] == RELATION_EQUIVALENT)
    correct = sum(1 for row in rows if row["prediction"] == row["truth"])
    summary = balanced_accuracy(rows)
    return {
        "n": total,
        "layout_or_template_count": len({row["unit_id"] for row in rows}),
        "relation_metrics_applicable": True,
        "relation_metrics_status": "COMPUTED",
        **summary,
        "overall_accuracy_unknown_counted_wrong": correct / total if total else None,
        "unknown_count": unknown,
        "unknown_rate": unknown / total if total else None,
        "determined_coverage": len(determined) / total if total else None,
        "conditional_accuracy_on_determined": (
            sum(1 for row in determined if row["prediction"] == row["truth"]) / len(determined)
            if determined else None),
        "equivalent_predicted_divergent": equivalent_as_divergent,
        "divergent_predicted_equivalent": divergent_as_equivalent,
        "prediction_distribution": dict(Counter(row["prediction"] for row in rows)),
    }


def ask_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """条件化询问建议指标。HIGH = 真分歧（任务关键），LOW = 真等价。

    这些是建议，不是实际发问，也不代表车辆被授权 ACT。
    """
    high = [row for row in rows if row["truth"] == RELATION_DIVERGENT]
    low = [row for row in rows if row["truth"] == RELATION_EQUIVALENT]
    def counts(subset: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        distribution = Counter(row["ask_recommendation"] for row in subset)
        return {
            "n": len(subset),
            ASK_RECOMMENDED: distribution.get(ASK_RECOMMENDED, 0),
            NO_QUERY_NEEDED: distribution.get(NO_QUERY_NEEDED, 0),
            UNRESOLVED: distribution.get(UNRESOLVED, 0),
        }
    high_counts, low_counts = counts(high), counts(low)
    return {
        "high_task_critical": {
            **high_counts,
            "ask_recommended_rate": high_counts[ASK_RECOMMENDED] / high_counts["n"] if high_counts["n"] else None,
            "unresolved_rate": high_counts[UNRESOLVED] / high_counts["n"] if high_counts["n"] else None,
        },
        "low_task_equivalent": {
            **low_counts,
            "unnecessary_ask_recommended_rate": low_counts[ASK_RECOMMENDED] / low_counts["n"] if low_counts["n"] else None,
            "unresolved_rate": low_counts[UNRESOLVED] / low_counts["n"] if low_counts["n"] else None,
        },
        "recommendation_not_actual_question": True,
        "no_query_needed_is_not_act_authorization": True,
    }


def four_cell(rows: Sequence[Mapping[str, Any]], *, threshold_m: float) -> dict[str, Any]:
    """按独立任务标签 × 实测轨迹差异分四格。

    rows 需含 max_distance_m（可为 None）、truth、unit_id、sample_id。
    轨迹无效的样本单列，不塞进任一格。
    """
    cells: dict[str, list[Mapping[str, Any]]] = {name: [] for name in FOUR_CELL_ORDER}
    invalid: list[Mapping[str, Any]] = []
    for row in rows:
        distance = row.get("max_distance_m")
        if distance is None:
            invalid.append(row)
            continue
        side = TRAJ_DIFFERENT if float(distance) > threshold_m else TRAJ_CLOSE
        cells[f"{side}__{row['truth']}"].append(row)
    return {
        "threshold_m": threshold_m,
        "cells": {
            name: {
                "sample_count": len(subset),
                "template_or_layout_count": len({row["unit_id"] for row in subset}),
                "templates_or_layouts": sorted({row["unit_id"] for row in subset}),
                "sample_ids": sorted(row["sample_id"] for row in subset),
                "max_distance_m_range": (
                    [min(float(row["max_distance_m"]) for row in subset),
                     max(float(row["max_distance_m"]) for row in subset)] if subset else None),
            }
            for name, subset in cells.items()
        },
        "empty_cells": [name for name, subset in cells.items() if not subset],
        "trajectory_invalid_samples": sorted(row["sample_id"] for row in invalid),
        "trajectory_invalid_count": len(invalid),
        "note": "未覆盖的格子记为 0 个样本，未人为补造；预测轨迹相同但真实任务不同的样本全部保留。",
    }


def four_cell_errors(rows: Sequence[Mapping[str, Any]], method_rows: Mapping[str, Sequence[Mapping[str, Any]]],
                     *, threshold_m: float) -> dict[str, Any]:
    """逐格给出各方法正确 / 误判 / UNKNOWN 数量。"""
    cell_of: dict[str, str] = {}
    for row in rows:
        distance = row.get("max_distance_m")
        if distance is None:
            continue
        side = TRAJ_DIFFERENT if float(distance) > threshold_m else TRAJ_CLOSE
        cell_of[row["sample_id"]] = f"{side}__{row['truth']}"

    out: dict[str, Any] = {}
    for cell in FOUR_CELL_ORDER:
        per_method: dict[str, Any] = {}
        for method, predictions in method_rows.items():
            subset = [row for row in predictions if cell_of.get(row["sample_id"]) == cell]
            per_method[method] = {
                "n": len(subset),
                "correct": sum(1 for row in subset if row["prediction"] == row["truth"]),
                "wrong_definite": sum(1 for row in subset
                                      if row["prediction"] != RELATION_UNKNOWN and row["prediction"] != row["truth"]),
                "unknown": sum(1 for row in subset if row["prediction"] == RELATION_UNKNOWN),
            }
        out[cell] = per_method
    return out


def threshold_sensitivity(rows: Sequence[Mapping[str, Any]], *, base_threshold_m: float) -> dict[str, Any]:
    """事后阈值敏感性：整组展示，不替换主结果。"""
    entries: list[dict[str, Any]] = []
    for multiplier in SENSITIVITY_MULTIPLIERS:
        threshold = base_threshold_m * multiplier
        scored: list[dict[str, Any]] = []
        for row in rows:
            distance = row.get("max_distance_m")
            if distance is None:
                continue
            prediction = RELATION_DIVERGENT if float(distance) > threshold else RELATION_EQUIVALENT
            scored.append({**row, "prediction": prediction})
        metrics = relation_metrics(scored) if scored else None
        entries.append({
            "multiplier": multiplier,
            "threshold_m": threshold,
            "is_frozen_original": multiplier == 1.0,
            "n_with_valid_trajectory": len(scored),
            "balanced_accuracy": metrics["balanced_accuracy"] if metrics else None,
            "equivalent_predicted_divergent": metrics["equivalent_predicted_divergent"] if metrics else None,
            "divergent_predicted_equivalent": metrics["divergent_predicted_equivalent"] if metrics else None,
        })
    return {
        "label": "POST_HOC_SENSITIVITY_ONLY",
        "base_threshold_m": base_threshold_m,
        "multipliers": list(SENSITIVITY_MULTIPLIERS),
        "entries": entries,
        "does_not_replace_frozen_main_result": True,
        "note": ("仅事后敏感性分析；不得选其中最好一点替换原主结果。"
                 "亦不得由某阈值两侧比例相同推断连续轨迹变量与任务关系总体独立。"),
    }


def per_unit_decomposition(method_rows: Mapping[str, Sequence[Mapping[str, Any]]],
                           first: str, second: str) -> dict[str, Any]:
    """逐模板/布局分解 first − second 的均衡正确率差值。"""
    units = sorted({row["unit_id"] for rows in method_rows.values() for row in rows})
    per_unit: list[dict[str, Any]] = []
    for unit in units:
        entry: dict[str, Any] = {"unit_id": unit}
        for method in (first, second):
            subset = [row for row in method_rows.get(method, []) if row["unit_id"] == unit]
            summary = balanced_accuracy(subset) if subset else None
            entry[method] = {
                "n": len(subset),
                "balanced_accuracy": summary["balanced_accuracy"] if summary else None,
                "balanced_accuracy_applicable": summary["balanced_accuracy_applicable"] if summary else None,
                "correct": sum(1 for row in subset if row["prediction"] == row["truth"]),
                "unknown": sum(1 for row in subset if row["prediction"] == RELATION_UNKNOWN),
            }
        left, right = entry[first]["balanced_accuracy"], entry[second]["balanced_accuracy"]
        entry["difference"] = None if left is None or right is None else left - right
        entry["difference_note"] = (
            None if entry["difference"] is not None else "该单位内真实类别不足两类，未算均衡正确率差")
        per_unit.append(entry)
    return {"comparison": f"{first}_minus_{second}", "per_unit": per_unit}


def paired_difference(method_rows: Mapping[str, Sequence[Mapping[str, Any]]],
                      first: str, second: str) -> dict[str, Any]:
    """配对差值：按 sample_id 对齐，逐样本正确性之差。"""
    left = {row["sample_id"]: row for row in method_rows.get(first, [])}
    right = {row["sample_id"]: row for row in method_rows.get(second, [])}
    shared = sorted(set(left) & set(right))
    both = only_first = only_second = neither = 0
    for sample_id in shared:
        a = left[sample_id]["prediction"] == left[sample_id]["truth"]
        b = right[sample_id]["prediction"] == right[sample_id]["truth"]
        if a and b:
            both += 1
        elif a:
            only_first += 1
        elif b:
            only_second += 1
        else:
            neither += 1
    left_metrics = relation_metrics([left[k] for k in shared]) if shared else None
    right_metrics = relation_metrics([right[k] for k in shared]) if shared else None
    left_ba = left_metrics["balanced_accuracy"] if left_metrics else None
    right_ba = right_metrics["balanced_accuracy"] if right_metrics else None
    return {
        "comparison": f"{first}_minus_{second}",
        "paired_n": len(shared),
        "both_correct": both,
        "only_first_correct": only_first,
        "only_second_correct": only_second,
        "neither_correct": neither,
        "balanced_accuracy_first": left_ba,
        "balanced_accuracy_second": right_ba,
        "balanced_accuracy_difference": None if left_ba is None or right_ba is None else left_ba - right_ba,
        "accuracy_difference_per_sample": (
            (both + only_first) / len(shared) - (both + only_second) / len(shared) if shared else None),
        "degenerate_note": ("全零/全一或模板极少时区间退化，不得把无差异当作等效或非劣效"),
    }


def evidence_pressure_report(rows: Sequence[Mapping[str, Any]], *, decisive_removed: bool) -> dict[str, Any]:
    """缺失证据条件下的报告。区分『证据不足仍确定』与『确定的类别答错』。"""
    total = len(rows)
    determined = [row for row in rows if row["prediction"] != RELATION_UNKNOWN]
    wrong_class = [row for row in determined if row["prediction"] != row["truth"]]
    return {
        "n": total,
        "decisive_evidence_removed": decisive_removed,
        "unknown_count": total - len(determined),
        "determined_count": len(determined),
        "determined_coverage": len(determined) / total if total else None,
        # 只有在独立规定的决定性证据被移除时，确定判断才算『证据不足仍确定』。
        "definite_despite_insufficient_evidence_count": len(determined) if decisive_removed else 0,
        "definite_despite_insufficient_evidence_rate": (
            len(determined) / total if decisive_removed and total else (0.0 if total else None)),
        "definite_but_wrong_class_count": len(wrong_class),
        "definite_but_wrong_class_rate": len(wrong_class) / total if total else None,
        "prediction_distribution": dict(Counter(row["prediction"] for row in rows)),
        "separation_note": "『证据不足仍确定』与『确定的类别答错』分别统计，未合并。",
    }
