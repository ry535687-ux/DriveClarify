#!/usr/bin/env python3
"""STEP1 独立重算：RQ1 条件化补充的关键计数。

只读上一轮逐记录 CSV（sample_results.csv / revision_regression_samples.csv），
不修改任何历史文件、标签或阈值。全部指标从原始 truth / prediction / max_distance_m
字段重算，**不从舍入后的指标反推整数计数**。

均衡正确率定义（沿用上一轮口径，未改动）：
  对真值类别 {TASK_EQUIVALENT, TASK_DIVERGENT} 各算 recall，再取算术平均。
  UNKNOWN 计为错误，**不从分母删除**。
"""
from __future__ import annotations

import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path("/home/buaa/wrh/DriveClarify")
SRC = REPO / "reports/driveclarify_rq1_conditional_supplement_20260911"
SAMPLES = SRC / "sample_results.csv"
REVISION = SRC / "results/revision_regression_samples.csv"

EQ, DV, UNK = "TASK_EQUIVALENT", "TASK_DIVERGENT", "UNKNOWN"
DEFINED = "DEFINED"

# 各来源原先冻结阈值，只核对不调优。
THRESHOLD_M = {
    "B_ABLATION_OVERNIGHT": 0.27119792945561905,
    "C_RQ1_V3_DEV": 0.10,
    "C_RQ1_V3_HIST": 0.10,
}

# 关系分类无意义的策略基线：记 N/A，不填 0。
POLICY_BASELINES = {"M0_ALWAYS_ASK", "M1_NEVER_ASK"}


def method_comparison(rows: list[dict]) -> dict:
    """表 2：同一份 FULL_INPUT 保存输入上的机制对照（原版）。"""
    buckets: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    undefined: dict[tuple[str, str], int] = Counter()
    for row in rows:
        if row["mask_variant"] != "FULL_INPUT":
            continue
        key = (population_of(row), row["method"])
        if row["truth_status"] != DEFINED:
            undefined[key] += 1
            continue
        buckets[key].append((row["truth"], row["prediction"]))

    out: dict[str, dict] = {}
    for (pop, method), pairs in sorted(buckets.items()):
        entry = {"n_label_undefined_excluded": undefined.get((pop, method), 0)}
        if method in POLICY_BASELINES:
            entry["balanced_accuracy"] = "N/A_POLICY_BASELINE_NO_RELATION_OUTPUT"
            entry["n_defined"] = len(pairs)
        else:
            entry.update(balanced_accuracy(pairs))
        out.setdefault(pop, {})[method] = entry
    return out


def missing_evidence(rows: list[dict]) -> dict:
    """表 4：输入缺失压力测试。逐 (population, mask_variant, method) 重算。

    覆盖率 = 确定判断数 / 该条件全部可评价样本数（truth 已定义）。
    「证据不足仍确定」= evidence_condition==EVIDENCE_INSUFFICIENT 或 mask 移除了决定性
    任务结构证据时，仍给出非 UNKNOWN 的判断。此处按 mask 语义判定（NO_TASK_STRUCTURE_*）,
    与标签契约一致，**不以 FULL 自身输出判定证据是否充分**。
    """
    decisive_removed = {"NO_TASK_STRUCTURE_EVIDENCE", "NO_FUTURES_AND_NO_TASK_STRUCTURE"}
    buckets: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for row in rows:
        buckets[(population_of(row), row["mask_variant"], row["method"])].append(row)

    out: dict[str, dict] = {}
    for (pop, mask, method), group in sorted(buckets.items()):
        defined = [r for r in group if r["truth_status"] == DEFINED]
        definite = [r for r in defined if r["prediction"] != UNK]
        out.setdefault(pop, {}).setdefault(mask, {})[method] = {
            "n_evaluable": len(defined),
            "n_label_undefined": len(group) - len(defined),
            "n_definite": len(definite),
            "definite_coverage": (len(definite) / len(defined)) if defined else None,
            "n_definite_but_wrong_class": sum(1 for r in definite if r["prediction"] != r["truth"]),
            "n_definite_without_decisive_evidence": len(definite) if mask in decisive_removed else 0,
            "decisive_task_evidence_removed_by_mask": mask in decisive_removed,
            "n_unknown": sum(1 for r in defined if r["prediction"] == UNK),
        }
    return out


def population_of(row: dict) -> str:
    """统计总体键。来源 B 按来源臂分开，不合并。"""
    if row["source"] == "B_ABLATION_OVERNIGHT":
        return f"B::{row['arm_of_origin']}"
    return f"C::{row['split']}"


def inventory(rows: list[dict], rev_rows: list[dict]) -> dict:
    """总体清单：分母、单位数、标签未定义数，按来源分列，不跨来源合并。"""
    out: dict[str, dict] = {}
    by_pop: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_pop[population_of(row)].append(row)
    for pop, group in sorted(by_pop.items()):
        full = [r for r in group if r["mask_variant"] == "FULL_INPUT"]
        sample_ids = {r["sample_id"] for r in full}
        defined_ids = {r["sample_id"] for r in full if r["truth_status"] == DEFINED}
        out[pop] = {
            "source": group[0]["source"],
            "split": group[0]["split"],
            "n_rows_all_conditions": len(group),
            "n_distinct_samples_full_input": len(sample_ids),
            "n_evaluable_samples_label_defined": len(defined_ids),
            "n_samples_label_undefined": len(sample_ids - defined_ids),
            "n_units_templates_or_layouts": len({r["unit_id"] for r in full}),
            "methods": sorted({r["method"] for r in group}),
            "mask_variants": sorted({r["mask_variant"] for r in group}),
            "truth_distribution_label_defined": dict(Counter(
                r["truth"] for r in full if r["truth_status"] == DEFINED
                and r["method"] == sorted({x["method"] for x in full})[0])),
        }
    return out


M4_NAMES = {"M4_TASK_SIGNATURE_ONLY_GIVEN", "M4_TASK_STATE_TOPOLOGY_ONLY"}
M5_NAMES = {"M5_FULL_B_ARM_RULE", "M5_DRIVECLARIFY_FULL"}


def key_of(row: dict) -> tuple[str, str, str]:
    return (row["source"], row["sample_id"], row["mask_variant"])


def revision_regression(rev_rows: list[dict]) -> dict:
    """表 5：原版 vs 修订版。逐 (sample, condition) 配对，不看已有汇总。"""
    versions = sorted({r["version"] for r in rev_rows})
    by_key: dict[tuple[str, str, str], dict[str, dict]] = defaultdict(dict)
    for row in rev_rows:
        by_key[key_of(row)][row["version"]] = row

    paired = {k: v for k, v in by_key.items() if len(v) == len(versions)}
    original_tag = next((v for v in versions if "ORIG" in v.upper()), versions[0])
    revised_tag = next((v for v in versions if "REVIS" in v.upper()), versions[-1])

    changed, orig_correct_lost, orig_wrong_removed, changed_undefined = [], [], [], []
    for key, pair in paired.items():
        before, after = pair[original_tag], pair[revised_tag]
        if before["prediction"] == after["prediction"]:
            continue
        record = {"source": key[0], "sample_id": key[1], "mask_variant": key[2],
                  "truth": before["truth"], "truth_status": before["truth_status"],
                  "before": before["prediction"], "after": after["prediction"]}
        changed.append(record)
        if before["truth_status"] != DEFINED:
            changed_undefined.append(record)
        elif before["prediction"] == before["truth"]:
            orig_correct_lost.append(record)
        else:
            orig_wrong_removed.append(record)

    per_condition: dict[str, dict] = {}
    buckets: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for row in rev_rows:
        buckets[(population_of(row), row["mask_variant"], row["version"])].append(row)
    for (pop, mask, version), group in sorted(buckets.items()):
        defined = [r for r in group if r["truth_status"] == DEFINED]
        definite = [r for r in defined if r["prediction"] != UNK]
        stats = balanced_accuracy([(r["truth"], r["prediction"]) for r in defined])
        per_condition.setdefault(pop, {}).setdefault(mask, {})[version] = {
            "balanced_accuracy": stats["balanced_accuracy"],
            "n_evaluable": len(defined),
            "n_definite": len(definite),
            "definite_coverage": (len(definite) / len(defined)) if defined else None,
            "n_definite_but_wrong_class": sum(1 for r in definite if r["prediction"] != r["truth"]),
        }

    return {
        "versions": versions,
        "original_tag": original_tag,
        "revised_tag": revised_tag,
        "n_rows": len(rev_rows),
        "n_sample_condition_pairs": len(paired),
        "n_unpaired": len(by_key) - len(paired),
        "n_changed": len(changed),
        "n_changed_originally_correct_definite_lost": len(orig_correct_lost),
        "n_changed_originally_wrong_definite_removed": len(orig_wrong_removed),
        "n_changed_label_undefined": len(changed_undefined),
        "changed_records": sorted(changed, key=lambda r: (r["source"], r["mask_variant"], r["sample_id"])),
        "per_condition": per_condition,
    }


def full_vs_m4(rows: list[dict], rev_rows: list[dict]) -> dict:
    """FULL 与「只用任务结构」是否逐样本同判。

    任务证据可用性用 M4 是否给出非 UNKNOWN 判断作为操作化定义：
    M4 只读任务结构，M4 != UNKNOWN ⟺ 该条件下任务结构证据可读。
    """
    m4: dict[tuple[str, str, str], str] = {}
    m5_original: dict[tuple[str, str, str], str] = {}
    for row in rows:
        if row["method"] in M4_NAMES:
            m4[key_of(row)] = row["prediction"]
        elif row["method"] in M5_NAMES:
            m5_original[key_of(row)] = row["prediction"]

    m5_revised: dict[tuple[str, str, str], str] = {}
    revised_tag = next((v for v in {r["version"] for r in rev_rows} if "REVIS" in v.upper()), None)
    for row in rev_rows:
        if row["version"] == revised_tag:
            m5_revised[key_of(row)] = row["prediction"]

    def compare(label: str, m5: dict[tuple[str, str, str], str]) -> dict:
        shared = sorted(set(m4) & set(m5))
        diffs = [{"source": k[0], "sample_id": k[1], "mask_variant": k[2], "m4": m4[k], "m5": m5[k]}
                 for k in shared if m4[k] != m5[k]]
        evidence_available = [k for k in shared if m4[k] != UNK]
        return {
            "version": label,
            "n_shared_pairs": len(shared),
            "n_m4_only": len(set(m4) - set(m5)),
            "n_m5_only": len(set(m5) - set(m4)),
            "n_disagreements": len(diffs),
            "disagreements": diffs[:60],
            "n_task_evidence_available": len(evidence_available),
            "n_disagreements_where_task_evidence_available":
                sum(1 for k in evidence_available if m4[k] != m5[k]),
        }

    return {"original": compare("ORIGINAL", m5_original), "revised": compare("REVISED", m5_revised)}


def four_cell(rows: list[dict]) -> dict:
    """表 1 四格：轨迹接近/不同 × 真值等价/分歧。

    每个 (population, sample_id) 只算一次，与方法无关；只用 FULL_INPUT 且 truth 已定义。
    """
    out: dict[str, dict] = {}
    by_pop: dict[str, dict[str, dict]] = defaultdict(dict)
    for row in rows:
        if row["mask_variant"] != "FULL_INPUT" or row["truth_status"] != DEFINED:
            continue
        by_pop[population_of(row)][row["sample_id"]] = row

    for pop, samples in sorted(by_pop.items()):
        source = next(iter(samples.values()))["source"]
        threshold = THRESHOLD_M[source]
        cells = {k: {"n": 0, "units": set(), "distances": []} for k in
                 ("close_equivalent", "far_equivalent", "close_divergent", "far_divergent")}
        invalid = 0
        for sid, row in samples.items():
            raw = row["max_distance_m"]
            if raw in ("", None):
                invalid += 1
                continue
            distance = float(raw)
            near = "close" if distance <= threshold else "far"
            side = "equivalent" if row["truth"] == EQ else "divergent"
            cell = cells[f"{near}_{side}"]
            cell["n"] += 1
            cell["units"].add(row["unit_id"])
            cell["distances"].append(distance)
        out[pop] = {
            "threshold_m": threshold,
            "n_samples": len(samples),
            "n_trajectory_invalid": invalid,
            "unit_count": len({r["unit_id"] for r in samples.values()}),
            "cells": {
                key: {
                    "n": cell["n"],
                    "unit_count": len(cell["units"]),
                    "units": sorted(cell["units"]),
                    "min_distance_m": min(cell["distances"]) if cell["distances"] else None,
                    "max_distance_m": max(cell["distances"]) if cell["distances"] else None,
                }
                for key, cell in cells.items()
            },
            "contradictory_cells_total": cells["far_equivalent"]["n"] + cells["close_divergent"]["n"],
        }
    return out


def balanced_accuracy(pairs: list[tuple[str, str]]) -> dict:
    """pairs = [(truth, prediction)]，仅传入 truth 已定义的记录。"""
    per_class = {}
    for cls in (EQ, DV):
        subset = [p for t, p in pairs if t == cls]
        per_class[cls] = {
            "n": len(subset),
            "correct": sum(1 for p in subset if p == cls),
            "unknown": sum(1 for p in subset if p == UNK),
            "recall": (sum(1 for p in subset if p == cls) / len(subset)) if subset else None,
        }
    recalls = [v["recall"] for v in per_class.values() if v["recall"] is not None]
    return {
        "balanced_accuracy": (sum(recalls) / len(recalls)) if recalls else None,
        "classes_present": len(recalls),
        "per_class": per_class,
        "n_defined": len(pairs),
        "confusion": dict(Counter(f"{t}->{p}" for t, p in pairs)),
    }


def main() -> int:
    rows = list(csv.DictReader(SAMPLES.open(encoding="utf-8")))
    rev_rows = list(csv.DictReader(REVISION.open(encoding="utf-8")))
    out = {
        "generated_by": "reports/driveclarify_experiment_restructure_step1_20260912/"
                        "scripts/recompute_rq1_counts.py",
        "inputs": {
            "sample_results_csv": str(SAMPLES.relative_to(REPO)),
            "revision_regression_samples_csv": str(REVISION.relative_to(REPO)),
            "n_sample_rows": len(rows),
            "n_revision_rows": len(rev_rows),
        },
        "balanced_accuracy_definition":
            "mean of per-truth-class recall over {TASK_EQUIVALENT, TASK_DIVERGENT}; "
            "UNKNOWN counted as wrong and NOT removed from the denominator",
        "frozen_thresholds_m_verified_not_retuned": THRESHOLD_M,
        "inventory": inventory(rows, rev_rows),
        "table1_four_cell": four_cell(rows),
        "table2_method_comparison_original": method_comparison(rows),
        "table4_missing_evidence": missing_evidence(rows),
        "table5_revision_regression": revision_regression(rev_rows),
        "full_vs_m4_identity": full_vs_m4(rows, rev_rows),
    }
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1, default=str)
    return 0


if __name__ == "__main__":
    sys.exit(main())
