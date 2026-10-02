"""固定的完整／缺失条件回归：原版 vs 修订版，同一份保存输入。

一次固定运行，不重复调规则追求正结果。修订只取消「没有任务证据支持的轨迹升级条款」，
其余一切（阈值、投影字段、标签、ASK 映射、代价计数）不动。

条件对（不新增数据、不新增推理、不训练、不驾驶）：
- 来源 C：沿用本轮已保存的 mask 协议四变体（完整 / 无候选轨迹 / 无任务结构 / 两者皆无）。
- 来源 B：条件对来自该批消融**原本的设计**——`ABL_FULL` 臂签名可读（完整），
  `ABL_TRAJ_ONLY` 臂签名被刻意剥掉（缺失）。两臂仍分别报告，不合并。

输出并列三列：原版、修订版、差值。覆盖率代价单独成列。
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from driveclarify_rq1_grounded_relation_v3 import methods as v3_methods

from . import analyses, judges, judges_revised, loaders, masks, paths

VERSION_ORIGINAL = "ORIGINAL_FROZEN"
VERSION_REVISED = "REVISED_NO_UNSUPPORTED_ESCALATION"
VERSIONS = (VERSION_ORIGINAL, VERSION_REVISED)


def _row(base: Mapping[str, Any], *, version: str, relation: str, ask: str,
         reason_codes: Sequence[str]) -> dict[str, Any]:
    return {**base, "version": version, "prediction": relation,
            "ask_recommendation": ask, "reason_codes": list(reason_codes)}


def score_c_both_versions(
    rows: list[dict[str, Any]], config: Mapping[str, Any], variant: str
) -> list[dict[str, Any]]:
    """来源 C 的一个 mask 变体：M5 两版并列打分。M3/M4 不受修订影响，另行标注。"""
    out: list[dict[str, Any]] = []
    for row in rows:
        masked = masks.apply_mask_c(row["method_input"], variant)
        base = {
            "source": row["source"], "split": row["split"], "sample_id": row["sample_id"],
            "unit_id": row["unit_id"], "layout_id": row["layout_id"],
            "method": "M5_DRIVECLARIFY_FULL", "mask_variant": variant,
            "truth": row["truth"], "truth_status": row["truth_status"],
            "evidence_condition": row["evidence_condition"],
        }
        original = v3_methods.evaluate_method("M5_DRIVECLARIFY_FULL", masked, config)
        out.append(_row(base, version=VERSION_ORIGINAL, relation=original.relation.value,
                        ask=original.ask.value, reason_codes=original.reason_codes))
        revised = judges_revised.evaluate_c_m5_revised(masked, config)
        out.append(_row(base, version=VERSION_REVISED, relation=revised["relation"],
                        ask=revised["ask_recommendation"], reason_codes=revised["reason_codes"]))
    return out


def score_b_both_versions(records: list[dict[str, Any]], threshold_m: float) -> list[dict[str, Any]]:
    """来源 B：M5 两版并列打分，按来源臂保留标注，不合并。"""
    out: list[dict[str, Any]] = []
    for record in records:
        base = {
            "source": record["source"], "sample_id": record["sample_id"],
            "unit_id": record["unit_id"], "arm_of_origin": record["arm"],
            "method": "M5_FULL_B_ARM_RULE", "mask_variant": masks.MASK_FULL,
            "truth": record["truth"], "truth_status": "DEFINED",
            "task_signature_evidence_available": record["task_signature_evidence_available"],
        }
        original = judges.run_b_method("M5_FULL_B_ARM_RULE", record, threshold_m=threshold_m)
        out.append(_row(base, version=VERSION_ORIGINAL, relation=original["relation"],
                        ask=original["ask_recommendation"], reason_codes=original["reason_codes"]))
        revised = judges_revised.run_b_m5_revised(record, threshold_m=threshold_m)
        out.append(_row(base, version=VERSION_REVISED, relation=revised["relation"],
                        ask=revised["ask_recommendation"], reason_codes=revised["reason_codes"]))
    return out


def _evaluable(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [row for row in rows if row.get("truth_status", "DEFINED") == "DEFINED"]


def compare_versions(
    rows: Sequence[Mapping[str, Any]], *, decisive_removed: bool
) -> dict[str, Any]:
    """同一组样本上比较两版。返回两版指标、差值与覆盖率代价。"""
    evaluable = _evaluable(rows)
    by_version = {
        version: [row for row in evaluable if row["version"] == version] for version in VERSIONS
    }
    metrics = {
        version: {
            **analyses.relation_metrics(subset, method="M5"),
            **{"evidence_pressure": analyses.evidence_pressure_report(
                subset, decisive_removed=decisive_removed)},
        }
        for version, subset in by_version.items()
    }

    # 逐样本配对，统计修订把哪些确定判断换成了 UNKNOWN。
    keyed = {
        version: {row["sample_id"]: row for row in subset} for version, subset in by_version.items()
    }
    shared = sorted(set(keyed[VERSION_ORIGINAL]) & set(keyed[VERSION_REVISED]))
    changed: list[dict[str, Any]] = []
    lost_correct = lost_wrong = unchanged = 0
    for sample_id in shared:
        before, after = keyed[VERSION_ORIGINAL][sample_id], keyed[VERSION_REVISED][sample_id]
        if before["prediction"] == after["prediction"]:
            unchanged += 1
            continue
        was_correct = before["prediction"] == before["truth"]
        lost_correct += int(was_correct)
        lost_wrong += int(not was_correct)
        changed.append({
            "sample_id": sample_id, "unit_id": before["unit_id"], "truth": before["truth"],
            "original_prediction": before["prediction"], "revised_prediction": after["prediction"],
            "original_was_correct": was_correct,
        })

    original_metrics, revised_metrics = metrics[VERSION_ORIGINAL], metrics[VERSION_REVISED]

    def _delta(key: str) -> float | None:
        before, after = original_metrics.get(key), revised_metrics.get(key)
        if before is None or after is None:
            return None
        return after - before

    return {
        "n_evaluable": len(shared),
        "layout_or_template_count": len({row["unit_id"] for row in evaluable}),
        "decisive_evidence_removed": decisive_removed,
        "by_version": metrics,
        "delta_revised_minus_original": {
            "balanced_accuracy": _delta("balanced_accuracy"),
            "determined_coverage": _delta("determined_coverage"),
            "unknown_count": (revised_metrics.get("unknown_count", 0)
                              - original_metrics.get("unknown_count", 0)),
            "equivalent_predicted_divergent": (revised_metrics.get("equivalent_predicted_divergent", 0)
                                               - original_metrics.get("equivalent_predicted_divergent", 0)),
            "divergent_predicted_equivalent": (revised_metrics.get("divergent_predicted_equivalent", 0)
                                               - original_metrics.get("divergent_predicted_equivalent", 0)),
        },
        "coverage_cost": {
            "predictions_changed": len(changed),
            "predictions_unchanged": unchanged,
            "definite_to_unknown_that_was_correct": lost_correct,
            "definite_to_unknown_that_was_wrong": lost_wrong,
            "net_wrong_definite_removed": lost_wrong,
            "cost_note": ("修订以放弃确定判断换取消除错误确定判断；"
                          "两者都必须报告，不得只报被消除的错误。"),
        },
        "changed_samples": changed,
    }


# ------------------------------------------------------------------ 固定回归

#: 本轮回归的固定条件表。执行前写定，执行后不调整。
REGRESSION_CONDITIONS = {
    "C_RQ1_V3_DEV": tuple(masks.MASK_ORDER),
    "C_RQ1_V3_HIST": tuple(masks.MASK_ORDER),
    "B_ABLATION_OVERNIGHT::ABL_FULL": ("ARM_SIGNATURE_AVAILABLE",),
    "B_ABLATION_OVERNIGHT::ABL_TRAJ_ONLY": ("ARM_SIGNATURE_ABLATED_BY_ORIGINAL_DESIGN",),
}


def run_regression() -> dict[str, Any]:
    """一次固定的完整／缺失条件回归。不新增数据、不新增推理、不训练、不驾驶。"""
    b_records = loaders.load_b_records()
    b_threshold = float(b_records[0]["metric_config"]["threshold_m"])
    c_config = loaders.load_c_frozen_config()
    c_rows = {split: loaders.load_c_split(split) for split in ("DEV", "HIST")}

    report: dict[str, Any] = {
        "schema": "driveclarify.rq1_conditional_supplement.revision_regression.v1",
        "revision_spec": judges_revised.REVISION_SPEC,
        "regression_conditions_fixed_before_execution": {
            key: list(value) for key, value in REGRESSION_CONDITIONS.items()
        },
        "methods_affected_by_revision": list(judges_revised.METHODS_AFFECTED_BY_REVISION),
        "methods_identical_across_versions": list(judges_revised.METHODS_IDENTICAL_ACROSS_VERSIONS),
        "frozen_thresholds_unchanged": {
            "B_threshold_m": b_threshold,
            "C_trajectory_threshold_m": float(c_config["trajectory_threshold_m"]),
        },
        "populations": {},
        "all_rows": [],
    }

    # 来源 C：四个 mask 变体，两版并列。
    for split in ("DEV", "HIST"):
        population = f"C_RQ1_V3_{split}"
        per_variant: dict[str, Any] = {}
        for variant in masks.MASK_ORDER:
            scored = score_c_both_versions(c_rows[split], c_config, variant)
            report["all_rows"].extend(scored)
            per_variant[variant] = compare_versions(
                scored, decisive_removed=masks.DECISIVE_EVIDENCE_REMOVED[variant])
        report["populations"][population] = {
            "condition_kind": "OFFLINE_INPUT_MASK_VARIANTS",
            "decisive_evidence_group": masks.DECISIVE_EVIDENCE_GROUP_FOR_C,
            "decisive_group_determined_by_label_contract_not_by_full_output": True,
            "by_condition": per_variant,
        }

    # 来源 B：条件对来自该批消融原本的设计，两臂分别报告，不合并。
    b_scored = score_b_both_versions(b_records, b_threshold)
    report["all_rows"].extend(b_scored)
    for arm, condition in (("ABL_FULL", "ARM_SIGNATURE_AVAILABLE"),
                           ("ABL_TRAJ_ONLY", "ARM_SIGNATURE_ABLATED_BY_ORIGINAL_DESIGN")):
        subset = [row for row in b_scored if row["arm_of_origin"] == arm]
        signature_available = arm == "ABL_FULL"
        report["populations"][f"B_ABLATION_OVERNIGHT::{arm}"] = {
            "condition_kind": "ARM_OF_ORIGIN_NOT_A_MASK",
            "arms_pooled": False,
            "pool_prohibition_reason": (
                "ABL_TRAJ_ONLY 臂配置刻意剥掉 certified 签名，该剥离即该臂消融本身；"
                "两臂首决策帧亦不同。合并会把输入可用性差异报成机制效应。"),
            "task_signature_evidence_available": signature_available,
            "by_condition": {
                condition: compare_versions(subset, decisive_removed=not signature_available)
            },
        }
    return report


def _population_csv_rows(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for population, payload in report["populations"].items():
        for condition, comparison in payload["by_condition"].items():
            for version in VERSIONS:
                metrics = comparison["by_version"][version]
                pressure = metrics["evidence_pressure"]
                rows.append({
                    "population": population,
                    "condition": condition,
                    "decisive_evidence_removed": comparison["decisive_evidence_removed"],
                    "version": version,
                    "n": metrics["n"],
                    "layout_or_template_count": metrics["layout_or_template_count"],
                    "balanced_accuracy": metrics["balanced_accuracy"],
                    "balanced_accuracy_applicable": metrics["balanced_accuracy_applicable"],
                    "determined_coverage": metrics["determined_coverage"],
                    "unknown_count": metrics["unknown_count"],
                    "equivalent_predicted_divergent": metrics["equivalent_predicted_divergent"],
                    "divergent_predicted_equivalent": metrics["divergent_predicted_equivalent"],
                    "definite_despite_insufficient_evidence": pressure[
                        "definite_despite_insufficient_evidence_count"],
                    "definite_but_wrong_class": pressure["definite_but_wrong_class_count"],
                    "definite_to_unknown_that_was_correct": (
                        comparison["coverage_cost"]["definite_to_unknown_that_was_correct"]
                        if version == VERSION_REVISED else ""),
                    "definite_to_unknown_that_was_wrong": (
                        comparison["coverage_cost"]["definite_to_unknown_that_was_wrong"]
                        if version == VERSION_REVISED else ""),
                })
    return rows


REGRESSION_CSV_COLUMNS = [
    "population", "condition", "decisive_evidence_removed", "version", "n",
    "layout_or_template_count", "balanced_accuracy", "balanced_accuracy_applicable",
    "determined_coverage", "unknown_count", "equivalent_predicted_divergent",
    "divergent_predicted_equivalent", "definite_despite_insufficient_evidence",
    "definite_but_wrong_class", "definite_to_unknown_that_was_correct",
    "definite_to_unknown_that_was_wrong",
]


def main() -> int:
    from .run import write_csv  # 复用本轮的 CSV 写出器

    paths.ensure_out()
    report = run_regression()
    rows = report.pop("all_rows")

    out_json = paths.OUT_RESULTS / "revision_regression.json"
    out_json.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    write_csv(paths.OUT / "revision_regression.csv", _population_csv_rows(report),
              REGRESSION_CSV_COLUMNS)
    sample_rows = [
        {key: ("" if row.get(key) is None else row.get(key)) for key in (
            "source", "split", "sample_id", "unit_id", "arm_of_origin", "method", "version",
            "mask_variant", "truth", "truth_status", "prediction", "ask_recommendation")}
        for row in rows
    ]
    write_csv(paths.OUT_RESULTS / "revision_regression_samples.csv", sample_rows,
              ["source", "split", "sample_id", "unit_id", "arm_of_origin", "method", "version",
               "mask_variant", "truth", "truth_status", "prediction", "ask_recommendation"])

    print(f"回归条件组 {len(report['populations'])} 个总体，逐样本 {len(rows)} 行")
    for population, payload in report["populations"].items():
        for condition, comparison in payload["by_condition"].items():
            before = comparison["by_version"][VERSION_ORIGINAL]
            after = comparison["by_version"][VERSION_REVISED]
            cost = comparison["coverage_cost"]
            print(f"  {population} / {condition}: "
                  f"BA {before['balanced_accuracy']} -> {after['balanced_accuracy']}, "
                  f"覆盖 {before['determined_coverage']} -> {after['determined_coverage']}, "
                  f"改判 {cost['predictions_changed']} "
                  f"(原对 {cost['definite_to_unknown_that_was_correct']} / "
                  f"原错 {cost['definite_to_unknown_that_was_wrong']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
