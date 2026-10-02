"""§5 与 §10 要求的分析与图表。

三项必需分析：
  (A) 轨迹差异 × 任务差异 四格交叉。四格由**实际预测轨迹**与独立任务真值描述，
      不由场景构造意图描述。本轮特别协议：真任务不同但预测短轨迹接近的样本必须保留。
  (B) 语义保持与关系翻转的成对分析，统计"两侧都正确"的比例，而非仅输出一致。
  (C) UNKNOWN 与证据不足下的错误确定判断。

图表只画实测数据；FULL 是离散单操作点，不画伪连续 ROC。
"""

from __future__ import annotations

import csv
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .scoring import paired_both_correct

# 四格里"轨迹接近/不同"用与 M3 同一个冻结阈值判定，保持两处口径一致。
CELL_NAMES = (
    "TRAJECTORY_CLOSE__TASK_EQUIVALENT",
    "TRAJECTORY_DIFFERENT__TASK_EQUIVALENT",
    "TRAJECTORY_CLOSE__TASK_DIVERGENT",
    "TRAJECTORY_DIFFERENT__TASK_DIVERGENT",
)
RETAINED_PROTOCOL_NOTE = (
    "本轮特别授权：真任务不同但预测短轨迹相同/接近的样本必须保留计入。早期 RP 中"
    "「轨迹不可区分即不得计为高后果测试」的规则本轮不适用；旧 RP、旧数据与历史结论不修改。"
)


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]], fields: Sequence[str]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(fields), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return {"path": str(path), "row_count": len(rows)}


def _wilson(successes: int, total: int) -> list[float] | None:
    """Wilson 区间。仅在样本层面报告；布局层面另用布局均值区间。"""
    if total <= 0:
        return None
    z = 1.959963984540054
    proportion = successes / total
    denominator = 1 + z * z / total
    centre = (proportion + z * z / (2 * total)) / denominator
    spread = z * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total)) / denominator
    return [round(max(0.0, centre - spread), 4), round(min(1.0, centre + spread), 4)]


def _layout_cluster_interval(values: Sequence[float]) -> dict[str, Any]:
    """按布局聚类的均值与区间：把同布局的关系变体/种子/改写视为相关，不当独立样本。"""
    if not values:
        return {"layout_count": 0, "mean": None, "interval": None}
    mean = statistics.fmean(values)
    if len(values) < 2:
        return {"layout_count": len(values), "mean": round(mean, 4), "interval": None,
                "interval_unavailable_reason": "SINGLE_LAYOUT_NO_BETWEEN_LAYOUT_VARIANCE"}
    error = statistics.stdev(values) / math.sqrt(len(values))
    return {
        "layout_count": len(values), "mean": round(mean, 4),
        "standard_error": round(error, 4),
        # t 分布近似用 1.96；布局数很少时区间只作粗略参考，不用 p 值判定实验是否通过。
        "interval": [round(max(0.0, mean - 1.96 * error), 4), round(min(1.0, mean + 1.96 * error), 4)],
        "cluster_unit": "layout_id",
    }


def trajectory_divergence_by_sample(samples: Iterable[Mapping[str, Any]], threshold_m: float | None) -> dict[str, dict[str, Any]]:
    """从方法输入里取每个样本的实测等时间最大分歧（由 M3 证据侧重算的同一量）。"""
    from .trajectory import compare

    result: dict[str, dict[str, Any]] = {}
    for sample in samples:
        futures = sample.get("candidate_futures")
        sample_id = str(sample["sample_id"])
        if not futures or len(futures) != 2 or not all(row.get("valid") for row in futures):
            result[sample_id] = {"valid": False, "reason_code": "NO_VALID_CANDIDATE_FUTURE_PAIR", "max_l2_m": None}
            continue
        try:
            _relation, evidence = compare(futures[0], futures[1], threshold_m=threshold_m if threshold_m is not None else 0.0, horizon_s=2.5, step_s=0.05)
        except Exception as error:
            result[sample_id] = {"valid": False, "reason_code": type(error).__name__, "max_l2_m": None}
            continue
        result[sample_id] = {"valid": True, "max_l2_m": round(evidence["max_l2_m"], 4)}
    return result


def four_cell(joined: Sequence[Mapping[str, Any]], divergence: Mapping[str, Mapping[str, Any]], threshold_m: float | None) -> dict[str, Any]:
    """(A) 四格交叉：按实际预测轨迹分歧 × 独立任务真值。"""
    cells: dict[str, list[dict[str, Any]]] = {name: [] for name in CELL_NAMES}
    unusable: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in joined:
        sample_id = str(row["sample_id"])
        if sample_id in seen:
            continue
        seen.add(sample_id)
        measurement = divergence.get(sample_id) or {}
        if not measurement.get("valid") or threshold_m is None:
            unusable.append({
                "sample_id": sample_id, "truth": row["truth"],
                "reason_code": measurement.get("reason_code") or "TRAJECTORY_THRESHOLD_UNAVAILABLE",
            })
            continue
        close = float(measurement["max_l2_m"]) <= float(threshold_m)
        name = "TRAJECTORY_{}__{}".format("CLOSE" if close else "DIFFERENT", row["truth"].replace("TASK_", "TASK_"))
        cells[name].append({"sample_id": sample_id, "max_l2_m": measurement["max_l2_m"], "truth": row["truth"]})

    per_cell_errors: dict[str, Any] = {}
    for name, members in cells.items():
        ids = {row["sample_id"] for row in members}
        block: dict[str, Any] = {"sample_count": len(members)}
        for method in sorted({str(row["method"]) for row in joined}):
            subset = [row for row in joined if str(row["method"]) == method and str(row["sample_id"]) in ids]
            if not subset:
                continue
            block[method] = {
                "n": len(subset),
                "correct": sum(row["prediction"] == row["truth"] for row in subset),
                "unknown": sum(row["prediction"] == "UNKNOWN" for row in subset),
                "accuracy": round(sum(row["prediction"] == row["truth"] for row in subset) / len(subset), 4),
            }
        per_cell_errors[name] = block
    return {
        "cells_described_by": "ACTUAL_PREDICTED_TRAJECTORY_AND_INDEPENDENT_TASK_TRUTH",
        "threshold_m": threshold_m,
        "retained_protocol_note": RETAINED_PROTOCOL_NOTE,
        "task_divergent_but_trajectory_close_retained": len(cells["TRAJECTORY_CLOSE__TASK_DIVERGENT"]),
        "cell_counts": {name: len(members) for name, members in cells.items()},
        "cell_members": cells,
        "cell_error_analysis": per_cell_errors,
        "samples_without_valid_trajectory_listed_separately": unusable,
    }


def paired(joined: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """(B) 语义保持与关系翻转的两侧都正确统计。"""
    result = {}
    for kind, field in (("semantics_preserving", "semantic_preservation_pair_id"), ("relation_flip", "relation_flip_pair_id")):
        rows = paired_both_correct(joined, field)
        by_method: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            by_method[str(row["method"])].append(row)
        summary = {}
        for method, group in sorted(by_method.items()):
            complete = [row for row in group if row["member_count"] == 2]
            both = sum(1 for row in complete if row["both_correct"])
            summary[method] = {
                "complete_pair_count": len(complete),
                "both_correct": both,
                "both_correct_rate": round(both / len(complete), 4) if complete else None,
                "incomplete_pairs": len(group) - len(complete),
                "criterion": "BOTH_SIDES_CORRECT_NOT_MERELY_OUTPUTS_AGREE",
                "interval": _wilson(both, len(complete)) if complete else None,
            }
        result[kind] = {"per_method": summary, "pairs": rows}
    return result


def unknown_analysis(joined: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """(C) UNKNOWN 比例、有效决策覆盖率与证据不足下的错误确定判断。"""
    per_method = {}
    for method in sorted({str(row["method"]) for row in joined}):
        subset = [row for row in joined if str(row["method"]) == method]
        unknown = [row for row in subset if row["prediction"] == "UNKNOWN"]
        determined = [row for row in subset if row["prediction"] != "UNKNOWN"]
        insufficient = [row for row in subset if str(row.get("evidence_condition")) != "FULL_EVIDENCE"]
        reasons: dict[str, int] = defaultdict(int)
        for row in unknown:
            for code in row.get("reason_codes") or ["UNSPECIFIED"]:
                reasons[str(code).split(":")[0]] += 1
        per_method[method] = {
            "n": len(subset),
            "unknown_count": len(unknown),
            "unknown_rate": round(len(unknown) / len(subset), 4) if subset else None,
            "valid_decision_coverage": round(len(determined) / len(subset), 4) if subset else None,
            "conditional_accuracy": round(sum(row["prediction"] == row["truth"] for row in determined) / len(determined), 4) if determined else None,
            "conditional_accuracy_reported_with_coverage": True,
            "unknown_reason_codes": dict(sorted(reasons.items())),
            "evidence_insufficient_subset_size": len(insufficient),
            "wrong_definite_under_evidence_insufficiency": sum(
                1 for row in insufficient if row["prediction"] != "UNKNOWN" and row["prediction"] != row["truth"]
            ),
            "ask_counts": {
                value: sum(1 for row in subset if str(row.get("ask_recommendation")) == value)
                for value in ("ASK_RECOMMENDED", "NO_QUERY_NEEDED", "UNRESOLVED")
            },
            "unresolved_never_converted_to_act": True,
            "ask_recommendation_is_not_a_question_actually_sent": True,
        }
    return {"per_method": per_method, "unknown_counted_in_primary_denominator": True}


def layout_clustered(joined: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """按布局聚类的方法结果：布局内的关系变体/种子/改写视为相关。"""
    result = {}
    for method in sorted({str(row["method"]) for row in joined}):
        subset = [row for row in joined if str(row["method"]) == method]
        per_layout = defaultdict(list)
        for row in subset:
            per_layout[str(row["layout_id"])].append(row)
        layout_scores = []
        for rows in per_layout.values():
            classes = []
            for truth in ("TASK_EQUIVALENT", "TASK_DIVERGENT"):
                part = [row for row in rows if row["truth"] == truth]
                if part:
                    classes.append(sum(row["prediction"] == truth for row in part) / len(part))
            if classes:
                layout_scores.append(statistics.fmean(classes))
        result[method] = _layout_cluster_interval(layout_scores)
    return result


def cost_summary(joined: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    result = {}
    for method in sorted({str(row["method"]) for row in joined}):
        subset = [row for row in joined if str(row["method"]) == method]
        seconds = [float(row.get("inference_seconds") or 0.0) for row in subset]
        result[method] = {
            "n": len(subset),
            "inference_seconds_total": round(sum(seconds), 6),
            "inference_seconds_median": round(statistics.median(seconds), 6) if seconds else None,
            "candidate_forward_calls": sum(int(row.get("candidate_forward_count") or 0) for row in subset),
            "grounding_calls": sum(int(row.get("grounding_forward_count") or 0) for row in subset),
            "equal_compute_claimed": False,
        }
    return result


def figures(
    *,
    split: str,
    joined: Sequence[Mapping[str, Any]],
    summary: Mapping[str, Any],
    cells: Mapping[str, Any],
    pairs: Mapping[str, Any],
    unknown: Mapping[str, Any],
    divergence: Mapping[str, Mapping[str, Any]],
    threshold_m: float | None,
    figures_root: Path,
) -> list[dict[str, Any]]:
    """五张必需图。全部用实测数据；无有效轨迹的样本单列，不静默丢弃。"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as error:
        return [{"status": "FIGURES_UNAVAILABLE", "reason": "{}: {}".format(type(error).__name__, error)}]

    figures_root.mkdir(parents=True, exist_ok=True)
    written: list[dict[str, Any]] = []

    def save(figure, name: str, note: str) -> None:
        path = figures_root / "{}_{}.png".format(split, name)
        figure.tight_layout()
        figure.savefig(path, dpi=150)
        plt.close(figure)
        written.append({"figure": name, "path": str(path), "note": note})

    # 图1 方法主结果表（平衡准确率 + 覆盖率）
    methods = [row for row in summary["methods"]]
    figure, axis = plt.subplots(figsize=(9, 4.2))
    names = [row["method"] for row in methods]
    positions = range(len(names))
    axis.bar([p - 0.2 for p in positions], [row["balanced_accuracy"] for row in methods], width=0.4, label="balanced accuracy")
    axis.bar([p + 0.2 for p in positions], [row["coverage"] for row in methods], width=0.4, label="valid-decision coverage")
    axis.axhline(0.5, linestyle="--", linewidth=1, color="grey")
    axis.set_xticks(list(positions))
    axis.set_xticklabels([name.replace("_", "\n", 1) for name in names], fontsize=7)
    axis.set_ylim(0, 1.05)
    axis.set_ylabel("rate")
    axis.set_title("{}: task-relation balanced accuracy (UNKNOWN in denominator) and coverage".format(split), fontsize=9)
    axis.legend(fontsize=7)
    save(figure, "fig1_method_main_results", "UNKNOWN 计入分母；0.5 虚线为随机基准")

    # 图2 按独立任务关系分组的实测轨迹分歧分布
    groups = {"TASK_EQUIVALENT": [], "TASK_DIVERGENT": []}
    seen: set[str] = set()
    for row in joined:
        sample_id = str(row["sample_id"])
        if sample_id in seen:
            continue
        seen.add(sample_id)
        measurement = divergence.get(sample_id) or {}
        if measurement.get("valid"):
            groups.setdefault(str(row["truth"]), []).append(float(measurement["max_l2_m"]))
    figure, axis = plt.subplots(figsize=(7, 4))
    if any(groups.values()):
        axis.hist([groups["TASK_EQUIVALENT"], groups["TASK_DIVERGENT"]], bins=12,
                  label=["TASK_EQUIVALENT (n={})".format(len(groups["TASK_EQUIVALENT"])),
                         "TASK_DIVERGENT (n={})".format(len(groups["TASK_DIVERGENT"]))])
        if threshold_m is not None:
            axis.axvline(float(threshold_m), color="black", linestyle="--", linewidth=1,
                         label="frozen threshold {:.2f} m".format(float(threshold_m)))
        axis.legend(fontsize=7)
    else:
        axis.text(0.5, 0.5, "no valid candidate-future pair", ha="center", va="center")
    axis.set_xlabel("measured equal-time max L2 divergence over 2.5 s (m)")
    axis.set_ylabel("decision samples")
    axis.set_title("{}: trajectory divergence grouped by independent task relation".format(split), fontsize=9)
    save(figure, "fig2_divergence_by_task_relation", "实测等时间分歧；未含无有效轨迹样本（单列于 JSON）")

    # 图3 四格条件误差分析
    figure, axis = plt.subplots(figsize=(9, 4.2))
    cell_names = list(CELL_NAMES)
    focus = [name for name in ("M3_TRAJECTORY_ONLY", "M4_TASK_STATE_TOPOLOGY_ONLY", "M5_DRIVECLARIFY_FULL")]
    width = 0.26
    for offset, method in zip((-width, 0.0, width), focus):
        values = []
        for name in cell_names:
            block = (cells.get("cell_error_analysis") or {}).get(name) or {}
            entry = block.get(method)
            values.append(entry["accuracy"] if entry else 0.0)
        axis.bar([index + offset for index in range(len(cell_names))], values, width=width, label=method)
    axis.set_xticks(range(len(cell_names)))
    axis.set_xticklabels([name.replace("__", "\n") for name in cell_names], fontsize=6)
    axis.set_ylim(0, 1.05)
    axis.set_ylabel("accuracy in cell")
    counts = cells.get("cell_counts") or {}
    axis.set_title("{}: four-cell condition error analysis (n per cell: {})".format(
        split, ", ".join("{}={}".format(name.split("__")[0][11:], counts.get(name, 0)) for name in cell_names)), fontsize=8)
    axis.legend(fontsize=7)
    save(figure, "fig3_four_cell_error_analysis", "四格由实际预测轨迹与独立任务真值描述")

    # 图4 语义保持改写与关系翻转的成对结果
    figure, axis = plt.subplots(figsize=(9, 4.2))
    kinds = ("semantics_preserving", "relation_flip")
    method_names = sorted({name for kind in kinds for name in (pairs.get(kind, {}).get("per_method") or {})})
    for offset, kind in zip((-0.2, 0.2), kinds):
        values = []
        for name in method_names:
            entry = (pairs.get(kind, {}).get("per_method") or {}).get(name) or {}
            values.append(entry.get("both_correct_rate") or 0.0)
        axis.bar([index + offset for index in range(len(method_names))], values, width=0.4, label=kind)
    axis.set_xticks(range(len(method_names)))
    axis.set_xticklabels([name.replace("_", "\n", 1) for name in method_names], fontsize=6)
    axis.set_ylim(0, 1.05)
    axis.set_ylabel("both-sides-correct rate")
    axis.set_title("{}: paired results (criterion = both sides correct, not merely agreeing)".format(split), fontsize=9)
    axis.legend(fontsize=7)
    save(figure, "fig4_paired_preservation_and_flip", "判定标准为两侧都正确")

    # 图5 UNKNOWN 与证据不足下的错误确定判断
    figure, axis = plt.subplots(figsize=(9, 4.2))
    per_method = unknown["per_method"]
    names = sorted(per_method)
    axis.bar([index - 0.2 for index in range(len(names))], [per_method[name]["unknown_rate"] or 0.0 for name in names], width=0.4, label="UNKNOWN rate")
    axis.bar([index + 0.2 for index in range(len(names))], [per_method[name]["wrong_definite_under_evidence_insufficiency"] for name in names], width=0.4, label="wrong definite under evidence insufficiency (count)")
    axis.set_xticks(range(len(names)))
    axis.set_xticklabels([name.replace("_", "\n", 1) for name in names], fontsize=6)
    axis.set_ylabel("rate / count")
    axis.set_title("{}: UNKNOWN and wrong-definite-judgement analysis".format(split), fontsize=9)
    axis.legend(fontsize=7)
    save(figure, "fig5_unknown_and_wrong_definite", "左轴混合比例与计数；证据不足子集大小见 JSON")

    written.append({
        "ask_error_tradeoff_curve": "NOT_DRAWN",
        "reason": "本轮方法输出为离散三态，无真实连续置信分数；FULL 只有一个操作点，不画伪连续曲线。",
    })
    return written


def run(*, split: str, joined: Sequence[Mapping[str, Any]], samples: Sequence[Mapping[str, Any]], frozen: Mapping[str, Any], round_root: Path) -> dict[str, Any]:
    """跑完整分析并写出 CSV、JSON 与图表。"""
    from .scoring import score

    threshold = (frozen.get("frozen_config") or {}).get("trajectory_threshold_m")
    threshold_m = None if threshold is None or (isinstance(threshold, float) and math.isnan(threshold)) else float(threshold)
    divergence = trajectory_divergence_by_sample(samples, threshold_m)
    summary = score(joined)
    cells = four_cell(joined, divergence, threshold_m)
    pairs = paired(joined)
    unknown = unknown_analysis(joined)
    clustered = layout_clustered(joined)
    costs = cost_summary(joined)

    results_root = round_root / "results"
    main_rows = []
    for row in summary["methods"]:
        method = row["method"]
        main_rows.append({
            **row,
            "unknown_rate": unknown["per_method"][method]["unknown_rate"],
            "layout_clustered_mean": clustered[method]["mean"],
            "layout_clustered_interval": json.dumps(clustered[method].get("interval")),
            "layout_count_clustered": clustered[method]["layout_count"],
            "inference_seconds_total": costs[method]["inference_seconds_total"],
            "candidate_forward_calls": costs[method]["candidate_forward_calls"],
            "grounding_calls": costs[method]["grounding_calls"],
        })
    main_csv = _write_csv(results_root / "{}_main_results.csv".format(split), main_rows, [
        "method", "n", "layout_count", "balanced_accuracy", "coverage", "conditional_accuracy",
        "unknown_count", "unknown_rate", "equivalent_to_divergent", "divergent_to_equivalent",
        "layout_clustered_mean", "layout_clustered_interval", "layout_count_clustered",
        "inference_seconds_total", "candidate_forward_calls", "grounding_calls",
    ])

    sample_rows = []
    for row in joined:
        measurement = divergence.get(str(row["sample_id"])) or {}
        sample_rows.append({
            "sample_id": row["sample_id"], "method": row["method"], "layout_id": row["layout_id"],
            "family": row["family"], "truth": row["truth"], "prediction": row["prediction"],
            "ask_recommendation": row.get("ask_recommendation"),
            "correct": int(row["prediction"] == row["truth"]),
            "max_l2_m": measurement.get("max_l2_m"),
            "trajectory_valid": measurement.get("valid"),
            "evidence_condition": row.get("evidence_condition"),
            "reason_codes": ";".join(row.get("reason_codes") or []),
        })
    sample_csv = _write_csv(results_root / "{}_sample_results.csv".format(split), sample_rows, [
        "sample_id", "method", "layout_id", "family", "truth", "prediction", "ask_recommendation",
        "correct", "max_l2_m", "trajectory_valid", "evidence_condition", "reason_codes",
    ])

    paired_rows = []
    for kind in ("semantics_preserving", "relation_flip"):
        for row in pairs[kind]["pairs"]:
            paired_rows.append({"pair_kind": kind, **row})
    paired_csv = _write_csv(results_root / "{}_paired_results.csv".format(split), paired_rows,
                            ["pair_kind", "method", "pair_id", "member_count", "both_correct"])

    figure_rows = figures(
        split=split, joined=joined, summary=summary, cells=cells, pairs=pairs, unknown=unknown,
        divergence=divergence, threshold_m=threshold_m, figures_root=round_root / "figures",
    )

    main_comparison = {}
    by_method = {row["method"]: row for row in summary["methods"]}
    for challenger, baseline in (("M5_DRIVECLARIFY_FULL", "M3_TRAJECTORY_ONLY"), ("M5_DRIVECLARIFY_FULL", "M4_TASK_STATE_TOPOLOGY_ONLY")):
        if challenger in by_method and baseline in by_method:
            difference = by_method[challenger]["balanced_accuracy"] - by_method[baseline]["balanced_accuracy"]
            main_comparison["{}_vs_{}".format(challenger, baseline)] = {
                "challenger_balanced_accuracy": round(by_method[challenger]["balanced_accuracy"], 4),
                "baseline_balanced_accuracy": round(by_method[baseline]["balanced_accuracy"], 4),
                "difference": round(difference, 4),
                "full_required_to_win": False,
                "result_kept_even_if_no_incremental_value": True,
            }

    value = {
        "split": split,
        "threshold_m": threshold_m,
        "summary": summary,
        "prespecified_main_comparisons": main_comparison,
        "four_cell": cells,
        "paired": {kind: pairs[kind]["per_method"] for kind in pairs},
        "unknown_analysis": unknown,
        "layout_clustered": clustered,
        "cost": costs,
        "csv": {"main": main_csv, "sample": sample_csv, "paired": paired_csv},
        "figures": figure_rows,
        "p_value_used_to_decide_pass": False,
    }
    path = round_root / "results/{}_ANALYSIS.json".format(split)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return value
