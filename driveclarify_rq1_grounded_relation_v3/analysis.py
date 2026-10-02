"""RQ1 V3 分析与图表：四格、成对、UNKNOWN、证据不足、成本与同义反复检测。

所有结果程序化输出，包括零效应、弱结果与 UNKNOWN 较多的情形。
不因结果不理想增加样本、调阈值或换布局。
"""

from __future__ import annotations

import json
import math
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

MAIN_METHODS = ("M0_ALWAYS_ASK", "M1_NEVER_ASK", "M2_GROUNDING_CONFIDENCE", "M3_TRAJECTORY_ONLY",
                "M4_TASK_STATE_TOPOLOGY_ONLY", "M5_DRIVECLARIFY_FULL", "M_LEX_CONTROL")
PRESPECIFIED_COMPARISONS = (
    ("M5_DRIVECLARIFY_FULL", "M3_TRAJECTORY_ONLY"),
    ("M5_DRIVECLARIFY_FULL", "M4_TASK_STATE_TOPOLOGY_ONLY"),
)
TRAJECTORY_CLOSE = "TRAJECTORY_CLOSE"
TRAJECTORY_DIFFERENT = "TRAJECTORY_DIFFERENT"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _write_csv(path: Path, header: Sequence[str], rows: Sequence[Sequence[Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [",".join(str(name) for name in header)]
    for row in rows:
        lines.append(",".join("" if value is None else str(value) for value in row))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _cluster_bootstrap_ci(
    per_layout: Mapping[str, list[tuple[str, str]]],
    *,
    iterations: int = 2000,
    seed: int = 20260910,
) -> tuple[float | None, float | None]:
    """按布局聚类的 bootstrap 置信区间；关系变体/seed/改写作为相关样本整体重抽。"""
    layouts = sorted(per_layout)
    if len(layouts) < 2:
        return None, None
    # 用 Mersenne Twister 而不是自写 LCG。自写 LCG 取模小基数只用到最低几位，
    # 低位周期极短（模 2**31 时 state%8 的周期 <= 8），会让每次重抽恰好各布局各取一次，
    # 于是全部 bootstrap 估计等于全样本估计、CI 退化成一个点。已在 DEV 上实测到该症状。
    sampler = random.Random(seed)

    def _next(bound: int) -> int:
        return sampler.randrange(bound)

    def _balanced(rows: Sequence[tuple[str, str]]) -> float | None:
        per: dict[str, list[int]] = {"TASK_EQUIVALENT": [], "TASK_DIVERGENT": []}
        for truth, prediction in rows:
            if truth in per:
                per[truth].append(int(prediction == truth))
        values = [sum(item) / len(item) for item in per.values() if item]
        return sum(values) / len(values) if len(values) == 2 else None

    estimates = []
    for _ in range(iterations):
        rows: list[tuple[str, str]] = []
        for _ in layouts:
            rows.extend(per_layout[layouts[_next(len(layouts))]])
        value = _balanced(rows)
        if value is not None:
            estimates.append(value)
    if len(estimates) < 100:
        return None, None
    estimates.sort()
    return (round(estimates[int(0.025 * len(estimates))], 6), round(estimates[min(int(0.975 * len(estimates)), len(estimates) - 1)], 6))


def _trajectory_cell(row: Mapping[str, Any], threshold: float) -> str | None:
    """按 M3 的实测最大等时距离给出轨迹接近/不同；不依据预期强行归格。"""
    evidence = row.get("evidence") or {}
    value = evidence.get("max_l2_m")
    if value is None:
        return None
    return TRAJECTORY_CLOSE if float(value) <= threshold else TRAJECTORY_DIFFERENT


def run(round_root: Path, split: str) -> dict[str, Any]:
    prefix = "DEV" if split == "DEV" else "HIST"
    joined = _read_jsonl(round_root / f"results/{prefix}_joined_rows.jsonl")
    if not joined:
        return {"status": "BLOCKED_NO_JOINED_ROWS", "split": split}
    freeze = json.loads((round_root / "queue/S4_FREEZE.json").read_text(encoding="utf-8"))
    threshold = float(freeze["frozen_config"]["trajectory_threshold_m"])
    score_value = json.loads((round_root / f"results/{prefix}_SCORE.json").read_text(encoding="utf-8"))

    # 候选文本来自方法输入侧（非标签侧），仅用于跨布局翻转配对的分组键。
    # 它本来就是方法可见输入，读它不构成标签泄漏。
    texts_by_sample = {
        str(row["sample_id"]): [str(item["text"]) for item in row["candidates"]]
        for row in _read_jsonl(round_root / f"method_inputs/{prefix}_METHOD_INPUTS.jsonl")
    }
    for row in joined:
        row["candidate_texts"] = texts_by_sample.get(str(row["sample_id"]))

    evaluable = [row for row in joined if row.get("truth") in ("TASK_EQUIVALENT", "TASK_DIVERGENT")]
    undefined = [row for row in joined if row.get("truth") not in ("TASK_EQUIVALENT", "TASK_DIVERGENT")]

    # ---- 主结果表（含 M_LEX 同义反复检测）
    by_method = {row["method"]: row for row in score_value["main_results"]["methods"]}
    main_rows = []
    for method in MAIN_METHODS:
        stats = by_method.get(method)
        if stats is None:
            continue
        per_layout: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for row in evaluable:
            if row["method"] == method:
                per_layout[str(row["layout_id"])].append((str(row["truth"]), str(row["prediction"])))
        low, high = _cluster_bootstrap_ci(per_layout)
        cost = [row for row in joined if row["method"] == method]
        main_rows.append({
            **stats,
            "balanced_accuracy_ci95_low": low,
            "balanced_accuracy_ci95_high": high,
            "total_inference_seconds": round(sum(float(row.get("inference_seconds") or 0.0) for row in cost), 6),
            "candidate_forward_calls": sum(int(row.get("candidate_forward_count") or 0) for row in cost),
            "grounding_calls": sum(int(row.get("grounding_forward_count") or 0) for row in cost),
        })

    lex = next((row for row in main_rows if row["method"] == "M_LEX_CONTROL"), None)
    tautology = {
        "criterion": "M_LEX_CONTROL_MATCHES_A_MAIN_METHOD_SCORE",
        "m_lex_balanced_accuracy": None if lex is None else lex["balanced_accuracy"],
        "m_lex_coverage": None if lex is None else lex["coverage"],
        "matches": {} if lex is None else {
            row["method"]: abs(row["balanced_accuracy"] - lex["balanced_accuracy"]) < 1e-9
            for row in main_rows if row["method"] in ("M3_TRAJECTORY_ONLY", "M4_TASK_STATE_TOPOLOGY_ONLY", "M5_DRIVECLARIFY_FULL")
        },
    }
    tautology["lexically_tautological"] = bool(
        lex is not None and lex["coverage"] > 0.0 and any(tautology["matches"].values()))

    # ---- 预先指定的主比较
    comparisons = {}
    for challenger, baseline in PRESPECIFIED_COMPARISONS:
        a, b = by_method.get(challenger), by_method.get(baseline)
        if a is None or b is None:
            continue
        comparisons[f"{challenger}_vs_{baseline}"] = {
            "challenger_balanced_accuracy": a["balanced_accuracy"],
            "baseline_balanced_accuracy": b["balanced_accuracy"],
            "difference": round(a["balanced_accuracy"] - b["balanced_accuracy"], 6),
            "full_required_to_win": False,
            "result_kept_even_if_no_incremental_value": True,
        }

    # ---- 四格：轨迹接近/不同 × 任务等价/分歧
    m3_cell = {str(row["sample_id"]): _trajectory_cell(row, threshold)
               for row in joined if row["method"] == "M3_TRAJECTORY_ONLY"}
    four_cell: dict[str, int] = Counter()
    for row in evaluable:
        if row["method"] != "M3_TRAJECTORY_ONLY":
            continue
        cell = m3_cell.get(str(row["sample_id"]))
        if cell is None:
            four_cell["NO_VALID_TRAJECTORY"] += 1
            continue
        four_cell[f"{cell}__{row['truth']}"] += 1
    cell_errors: dict[str, dict[str, int]] = defaultdict(Counter)
    for row in evaluable:
        cell = m3_cell.get(str(row["sample_id"]))
        key = f"{cell}__{row['truth']}" if cell else "NO_VALID_TRAJECTORY"
        if row["prediction"] != row["truth"]:
            cell_errors[str(row["method"])][key] += 1

    # ---- 成对：语义保持
    # 语义保持配对键 unit|config|observation 恰含 P1/P2 两种表面形式 → 组大小恒为 2。
    def _preservation_pairs() -> dict[str, Any]:
        groups: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
        for row in evaluable:
            groups[(str(row["method"]), str(row.get("semantic_preservation_pair_id")))].append(row)
        per_method: dict[str, dict[str, Any]] = {}
        for (method, _), group in sorted(groups.items()):
            bucket = per_method.setdefault(method, {"pairs": 0, "both_correct": 0, "outputs_identical": 0,
                                                    "truth_differs_within_pair": 0, "groups_wrong_size": 0})
            if len(group) != 2:
                bucket["groups_wrong_size"] += 1
                continue
            bucket["pairs"] += 1
            bucket["both_correct"] += int(all(row["prediction"] == row["truth"] for row in group))
            bucket["outputs_identical"] += int(group[0]["prediction"] == group[1]["prediction"])
            bucket["truth_differs_within_pair"] += int(group[0]["truth"] != group[1]["truth"])
        for bucket in per_method.values():
            bucket["both_correct_rate"] = (bucket["both_correct"] / bucket["pairs"]) if bucket["pairs"] else None
        return per_method

    # ---- 成对：任务关系翻转
    # 按用户定义"原始语言不变，改变参照物与路口/目标的对应关系，使独立定义的关系发生变化"，
    # 翻转的正确实现是**候选文本完全相同、布局不同、真值相反**的跨布局配对。
    # 早先按 unit|observation|phrasing 分组是错的：那产生 3 个配置的同布局对照组，
    # 既不满足"语言不变"，也被"恰好 2 个成员"的过滤器整类跳过（实测 DEV 128 / HIST 224 组全被丢弃）。
    def _flip_pairs() -> dict[str, Any]:
        by_method_text: dict[str, dict[tuple[str, str], list[Mapping[str, Any]]]] = defaultdict(lambda: defaultdict(list))
        for row in evaluable:
            texts = row.get("candidate_texts")
            if not texts:
                continue
            by_method_text[str(row["method"])][(str(texts[0]), str(texts[1]))].append(row)
        per_method: dict[str, dict[str, Any]] = {}
        for method, buckets in sorted(by_method_text.items()):
            pairs = both = 0
            outputs_changed = 0
            for _, rows in sorted(buckets.items()):
                equivalent = [row for row in rows if row["truth"] == "TASK_EQUIVALENT"]
                divergent = [row for row in rows if row["truth"] == "TASK_DIVERGENT"]
                # 按布局顺序两两配对，配到较短一侧用尽为止；同一行不复用。
                for left, right in zip(sorted(equivalent, key=lambda row: str(row["layout_id"])),
                                       sorted(divergent, key=lambda row: str(row["layout_id"]))):
                    pairs += 1
                    both += int(left["prediction"] == left["truth"] and right["prediction"] == right["truth"])
                    outputs_changed += int(left["prediction"] != right["prediction"])
            per_method[method] = {
                "pairs": pairs, "both_correct": both,
                "both_correct_rate": (both / pairs) if pairs else None,
                "outputs_changed": outputs_changed,
                "outputs_changed_rate": (outputs_changed / pairs) if pairs else None,
                "pairing_rule": "IDENTICAL_CANDIDATE_TEXT_PAIR__DIFFERENT_LAYOUT__OPPOSITE_TRUTH",
                "note": "both_correct 才是主指标；outputs_changed 只说明输出变了，不说明变对了。",
            }
        return per_method

    preservation = _preservation_pairs()
    flip = _flip_pairs()

    # ---- 条件化 ASK 建议
    ask = {}
    for method in MAIN_METHODS:
        subset = [row for row in joined if row["method"] == method]
        if subset:
            ask[method] = dict(Counter(str(row.get("ask_recommendation")) for row in subset))

    # ---- 无有效轨迹的样本单列
    invalid_trajectory = sorted({str(row["sample_id"]) for row in joined
                                 if row["method"] == "M3_TRAJECTORY_ONLY" and _trajectory_cell(row, threshold) is None})

    result = {
        "schema_version": "driveclarify.rq1_v3_analysis.v1",
        "split": split,
        "status": "COMPLETE",
        "planned_population": len(joined) // max(len({row["method"] for row in joined}), 1),
        "evaluable_sample_count": len({row["sample_id"] for row in evaluable}),
        "label_undefined_sample_count": len({row["sample_id"] for row in undefined}),
        "layout_count": len({row["layout_id"] for row in joined}),
        "observation_count": len({row["observation_key"] for row in joined if row.get("observation_key")}),
        "frozen_trajectory_threshold_m": threshold,
        "main_results": main_rows,
        "tautology_check": tautology,
        "prespecified_comparisons": comparisons,
        "four_cell_counts": dict(four_cell),
        "four_cell_errors_by_method": {key: dict(value) for key, value in cell_errors.items()},
        "semantic_preservation": preservation,
        "relation_flip": flip,
        "ask_recommendation_distribution": ask,
        "evidence_insufficient": score_value["evidence_insufficient"],
        "samples_without_valid_trajectory": invalid_trajectory,
        "unknown_is_not_silently_converted_to_act": True,
        "ask_counts_are_recommendations_not_sent_questions": True,
    }
    _write_json(round_root / f"results/{prefix}_ANALYSIS.json", result)

    # ---- CSV 交付
    _write_csv(round_root / f"results/{prefix}_main_results.csv",
               ["method", "n", "layout_count", "balanced_accuracy", "ci95_low", "ci95_high", "coverage",
                "conditional_accuracy", "unknown_count", "equivalent_to_divergent", "divergent_to_equivalent",
                "total_inference_seconds", "candidate_forward_calls", "grounding_calls"],
               [[row["method"], row["n"], row["layout_count"], row["balanced_accuracy"],
                 row["balanced_accuracy_ci95_low"], row["balanced_accuracy_ci95_high"], row["coverage"],
                 row["conditional_accuracy"], row["unknown_count"], row["equivalent_to_divergent"],
                 row["divergent_to_equivalent"], row["total_inference_seconds"],
                 row["candidate_forward_calls"], row["grounding_calls"]] for row in main_rows])

    _write_csv(round_root / f"results/{prefix}_sample_results.csv",
               ["sample_id", "layout_id", "family", "relation_config_id", "phrasing_id", "observation_key",
                "truth", "truth_status", "evidence_condition", "method", "prediction", "ask_recommendation",
                "trajectory_cell", "reason_codes"],
               [[row["sample_id"], row["layout_id"], row["family"], row.get("relation_config_id"),
                 row.get("phrasing_id"), row.get("observation_key"), row.get("truth"), row.get("truth_status"),
                 row.get("evidence_condition"), row["method"], row["prediction"], row.get("ask_recommendation"),
                 m3_cell.get(str(row["sample_id"])), ";".join(row.get("reason_codes") or [])] for row in joined])

    paired_rows = []
    for method, bucket in sorted(preservation.items()):
        paired_rows.append(["SEMANTIC_PRESERVATION", method, bucket["pairs"], bucket["both_correct"],
                            bucket["both_correct_rate"], bucket["outputs_identical"],
                            bucket["truth_differs_within_pair"], None])
    for method, bucket in sorted(flip.items()):
        paired_rows.append(["RELATION_FLIP", method, bucket["pairs"], bucket["both_correct"],
                            bucket["both_correct_rate"], None, bucket["pairs"],
                            bucket["outputs_changed"]])
    _write_csv(round_root / f"results/{prefix}_paired_results.csv",
               ["pair_kind", "method", "pairs", "both_correct", "both_correct_rate",
                "outputs_identical", "truth_differs_within_pair", "outputs_changed"], paired_rows)

    _figures(round_root, prefix, main_rows, evaluable, m3_cell, four_cell, cell_errors,
             preservation, flip, score_value, threshold)
    return result


def _figures(round_root: Path, prefix: str, main_rows, evaluable, m3_cell, four_cell,
             cell_errors, preservation, flip, score_value, threshold: float) -> None:
    figures = round_root / "figures"
    figures.mkdir(parents=True, exist_ok=True)

    # 图1 方法主结果
    fig, axis = plt.subplots(figsize=(10, 5))
    names = [row["method"] for row in main_rows]
    values = [row["balanced_accuracy"] for row in main_rows]
    coverage = [row["coverage"] for row in main_rows]
    positions = range(len(names))
    axis.bar([p - 0.2 for p in positions], values, width=0.4, label="balanced accuracy")
    axis.bar([p + 0.2 for p in positions], coverage, width=0.4, label="coverage (determined rate)")
    for index, row in enumerate(main_rows):
        if row["balanced_accuracy_ci95_low"] is not None:
            axis.plot([index - 0.2, index - 0.2],
                      [row["balanced_accuracy_ci95_low"], row["balanced_accuracy_ci95_high"]],
                      color="black", linewidth=1.2)
    axis.axhline(0.5, linestyle="--", linewidth=0.8, color="grey")
    axis.set_xticks(list(positions))
    axis.set_xticklabels([name.replace("_", "\n") for name in names], fontsize=7)
    axis.set_ylim(0, 1.05)
    axis.set_ylabel("value")
    axis.set_title(f"{prefix}: task-relation balanced accuracy and coverage (UNKNOWN kept in denominator)")
    axis.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(figures / f"{prefix}_fig1_method_main_results.png", dpi=150)
    plt.close(fig)

    # 图2 真实轨迹分歧分布，按独立任务关系分组
    fig, axis = plt.subplots(figsize=(9, 5))
    buckets: dict[str, list[float]] = defaultdict(list)
    for row in evaluable:
        if row["method"] != "M3_TRAJECTORY_ONLY":
            continue
        value = (row.get("evidence") or {}).get("max_l2_m")
        if value is not None:
            buckets[str(row["truth"])].append(float(value))
    if buckets:
        axis.boxplot([buckets.get("TASK_EQUIVALENT", []), buckets.get("TASK_DIVERGENT", [])],
                     labels=["TASK_EQUIVALENT", "TASK_DIVERGENT"])
        for index, key in enumerate(("TASK_EQUIVALENT", "TASK_DIVERGENT"), start=1):
            for value in buckets.get(key, []):
                axis.plot(index, value, "o", alpha=0.5, markersize=4)
    axis.axhline(threshold, linestyle="--", color="red", linewidth=0.9,
                 label=f"frozen threshold {threshold} m")
    axis.set_ylabel("max equal-time L2 distance between candidate futures (m)")
    axis.set_title(f"{prefix}: measured trajectory divergence grouped by independent task relation")
    axis.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(figures / f"{prefix}_fig2_divergence_by_task_relation.png", dpi=150)
    plt.close(fig)

    # 图3 四格错误分析
    fig, axis = plt.subplots(figsize=(10, 5))
    cells = [f"{a}__{b}" for a in (TRAJECTORY_CLOSE, TRAJECTORY_DIFFERENT)
             for b in ("TASK_EQUIVALENT", "TASK_DIVERGENT")]
    methods = [row["method"] for row in main_rows if row["method"] in
               ("M3_TRAJECTORY_ONLY", "M4_TASK_STATE_TOPOLOGY_ONLY", "M5_DRIVECLARIFY_FULL", "M_LEX_CONTROL")]
    width = 0.8 / max(len(methods), 1)
    for offset, method in enumerate(methods):
        axis.bar([index + offset * width for index in range(len(cells))],
                 [cell_errors.get(method, {}).get(cell, 0) for cell in cells],
                 width=width, label=method)
    axis.set_xticks([index + 0.4 - width / 2 for index in range(len(cells))])
    axis.set_xticklabels([cell.replace("__", "\n") for cell in cells], fontsize=7)
    axis.set_ylabel("error count")
    axis.set_title(f"{prefix}: errors by four-cell condition (cells from measured trajectories)\n"
                   f"cell totals: " + ", ".join(f"{cell}={four_cell.get(cell, 0)}" for cell in cells), fontsize=8)
    axis.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(figures / f"{prefix}_fig3_four_cell_error_analysis.png", dpi=150)
    plt.close(fig)

    # 图4 改写保持与关系翻转配对
    fig, axis = plt.subplots(figsize=(9, 5))
    names = sorted(set(preservation) | set(flip))
    axis.bar([index - 0.2 for index in range(len(names))],
             [(preservation.get(name, {}).get("both_correct_rate") or 0) for name in names],
             width=0.4, label="semantic preservation: both correct")
    axis.bar([index + 0.2 for index in range(len(names))],
             [(flip.get(name, {}).get("both_correct_rate") or 0) for name in names],
             width=0.4, label="relation flip: both correct")
    axis.set_xticks(list(range(len(names))))
    axis.set_xticklabels([name.replace("_", "\n") for name in names], fontsize=7)
    axis.set_ylim(0, 1.05)
    axis.set_ylabel("both-sides-correct rate")
    axis.set_title(f"{prefix}: paired results (both sides correct, not merely identical output)")
    axis.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(figures / f"{prefix}_fig4_paired_preservation_and_flip.png", dpi=150)
    plt.close(fig)

    # 图5 UNKNOWN 与证据不足条件下的错误确定判断
    fig, axis = plt.subplots(figsize=(9, 5))
    insufficient = {row["method"]: row for row in score_value["evidence_insufficient"]["methods"]}
    names = [row["method"] for row in main_rows]
    axis.bar([index - 0.2 for index in range(len(names))],
             [row["unknown_count"] / row["n"] if row["n"] else 0 for row in main_rows],
             width=0.4, label="UNKNOWN rate (label-valid population)")
    axis.bar([index + 0.2 for index in range(len(names))],
             [(insufficient.get(name, {}).get("wrong_definite_rate") or 0) for name in names],
             width=0.4, label="wrong definite rate (evidence-insufficient subset)")
    axis.set_xticks(list(range(len(names))))
    axis.set_xticklabels([name.replace("_", "\n") for name in names], fontsize=7)
    axis.set_ylim(0, 1.05)
    axis.set_ylabel("rate")
    axis.set_title(f"{prefix}: UNKNOWN and wrong-definite analysis")
    axis.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(figures / f"{prefix}_fig5_unknown_and_wrong_definite.png", dpi=150)
    plt.close(fig)
