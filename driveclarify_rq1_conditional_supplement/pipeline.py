"""主流程：盘点 → 保存 mask 协议 → 评分 → 三项分析 → CSV/图/状态。"""

from __future__ import annotations

import time
from typing import Any

from . import analyses, cases, judges, loaders, masks, paths
from .run import (B_METHOD_NAMES, build_inventory, c_max_distance, environment_receipt,
                  log, LOG, make_figures, score_b, score_c_variant, verify_c_recomputation,
                  write_csv, write_json)

STATUS_COMPLETE = "RQ1_CONDITIONAL_SUPPLEMENT_COMPLETE_REVIEW_REQUIRED"
STATUS_PARTIAL = "RQ1_CONDITIONAL_SUPPLEMENT_PARTIAL_REVIEW_REQUIRED"


def _load_all() -> dict[str, Any]:
    log("读取来源 A / B / C / D")
    return {
        "a_summary": loaders.load_a_summary(),
        "a_configs": loaders.load_a_config_task_signatures(),
        "b_records": loaders.load_b_records(),
        "c_config": loaders.load_c_frozen_config(),
        "c_rows": {split: loaders.load_c_split(split) for split in ("DEV", "HIST")},
    }


def _score_b_stage(b_records: list[dict[str, Any]]) -> dict[str, Any]:
    log("来源 B：轨迹指标保真核验")
    threshold = float(b_records[0]["metric_config"]["threshold_m"])
    fidelity = [{"sample_id": row["sample_id"], **judges.fidelity_check(row)} for row in b_records]
    summary = {
        "n": len(fidelity),
        "comparable": sum(1 for row in fidelity if row["comparable"]),
        "exact_match_within_1e_9": sum(1 for row in fidelity if row.get("matches_within_1e_9")),
        "mismatches": [row for row in fidelity if row["comparable"] and not row.get("matches_within_1e_9")],
        "frozen_threshold_m": threshold,
        "recomputation_reproduces_recorded_metric": True,
        "rows": fidelity,
    }
    summary["recomputation_reproduces_recorded_metric"] = not summary["mismatches"]
    write_json(paths.OUT_EVIDENCE / "B_TRAJECTORY_FIDELITY.json", summary)
    log(f"  B 保真：{summary['exact_match_within_1e_9']}/{summary['n']} 精确一致")

    log("来源 B：评分 M0/M1/M3/M4/M5")
    scored = score_b(b_records, threshold)
    distances = [{
        "source": "B_ABLATION_OVERNIGHT",
        "sample_id": row["sample_id"],
        "unit_id": row["unit_id"],
        "arm_of_origin": row["arm"],
        "truth": row["truth"],
        "max_distance_m": judges.b_trajectory_metric(row["trajectories"]).get("max_aligned_distance_m"),
    } for row in b_records]
    return {"threshold": threshold, "fidelity": summary, "scored": scored, "distances": distances}


def _score_c_stage(c_rows: dict[str, list[dict[str, Any]]], c_config: dict[str, Any]) -> dict[str, Any]:
    log("来源 C：FULL_INPUT 重算与原轮可追溯预测一致性核验")
    verify = {split: verify_c_recomputation(rows, c_config, split) for split, rows in c_rows.items()}
    write_json(paths.OUT_EVIDENCE / "C_RECOMPUTATION_VERIFICATION.json", verify)
    for split, payload in verify.items():
        log(f"  C {split}: 比较 {payload['compared_count']} 项，不一致 {payload['mismatch_count']}")

    log("来源 C：mask 泄漏核验")
    leaks: list[dict[str, Any]] = []
    for split, rows in c_rows.items():
        for variant in masks.MASK_ORDER:
            for row in rows:
                masked = masks.apply_mask_c(row["method_input"], variant)
                finding = masks.verify_mask_removed_information(row["method_input"], masked, variant)
                if not finding["clean"]:
                    leaks.append({"split": split, "sample_id": row["sample_id"], **finding})
    write_json(paths.OUT_EVIDENCE / "C_MASK_LEAK_AUDIT.json",
               {"leak_count": len(leaks), "clean": not leaks, "leaks": leaks[:40],
                "checked_variants": list(masks.MASK_ORDER)})
    log(f"  mask 泄漏项：{len(leaks)}")

    log("来源 C：按 mask 变体评分")
    scored = {split: {variant: score_c_variant(rows, c_config, variant) for variant in masks.MASK_ORDER}
              for split, rows in c_rows.items()}

    distances: dict[str, list[dict[str, Any]]] = {"ALL": []}
    for split, rows in c_rows.items():
        collected: list[dict[str, Any]] = []
        for row in rows:
            distance, reason = c_max_distance(row["method_input"], c_config)
            collected.append({
                "source": row["source"], "split": split, "sample_id": row["sample_id"],
                "unit_id": row["unit_id"], "truth": row["truth"], "truth_status": row["truth_status"],
                "max_distance_m": distance, "trajectory_unavailable_reason": reason,
            })
        distances[split] = collected
        distances["ALL"].extend(row for row in collected if row["truth_status"] == "DEFINED")
    return {"verify": verify, "leaks": leaks, "scored": scored, "distances": distances}


def _analysis_one(b_stage: dict[str, Any], b_scored: dict[str, Any],
                  c_stage: dict[str, Any], c_threshold: float) -> dict[str, Any]:
    """分析 1：四格 + 事后阈值敏感性。"""
    log("分析 1：四格 + 阈值敏感性")
    four_cells: dict[str, Any] = {}
    errors: dict[str, Any] = {}
    sensitivity: dict[str, Any] = {}

    b_rows = b_stage["distances"]
    four_cells["B_ABLATION_OVERNIGHT"] = analyses.four_cell(b_rows, threshold_m=b_stage["threshold"])
    errors["B_ABLATION_OVERNIGHT"] = analyses.four_cell_errors(b_rows, b_scored, threshold_m=b_stage["threshold"])
    sensitivity["B_ABLATION_OVERNIGHT"] = analyses.threshold_sensitivity(
        b_rows, base_threshold_m=b_stage["threshold"])

    for split in ("DEV", "HIST"):
        key = f"C_RQ1_V3_{split}"
        defined = [row for row in c_stage["distances"][split] if row["truth_status"] == "DEFINED"]
        method_full = {name: [row for row in rows if row["truth_status"] == "DEFINED"]
                       for name, rows in c_stage["scored"][split][masks.MASK_FULL].items()}
        four_cells[key] = analyses.four_cell(defined, threshold_m=c_threshold)
        errors[key] = analyses.four_cell_errors(defined, method_full, threshold_m=c_threshold)
        sensitivity[key] = analyses.threshold_sensitivity(defined, base_threshold_m=c_threshold)

    write_json(paths.OUT_RESULTS / "four_cell_analysis.json",
               {"four_cells": four_cells, "errors_by_method": errors,
                "threshold_sensitivity": sensitivity,
                "labelling": "补充机制分析 / 探索性分析，非预注册"})
    return {"four_cells": four_cells, "errors": errors, "sensitivity": sensitivity}


def _analysis_two(b_records: list[dict[str, Any]], b_scored: dict[str, Any],
                  c_stage: dict[str, Any], a_summary: dict[str, Any]) -> dict[str, Any]:
    """分析 2：三个机制对照。"""
    log("分析 2：机制对照")
    comparison: dict[str, Any] = {}

    # B 的主报告单位是「来源臂」。ABL_TRAJ_ONLY 的配置刻意剥掉 certified 任务签名，
    # 该剥离本身就是消融；把两臂合并会把输入可用性差异报成机制差异，故不设合并主结果。
    arms = sorted({row["arm"] for row in b_records})
    by_arm: dict[str, Any] = {}
    for arm in arms:
        arm_scored = {name: [row for row in rows if row["arm_of_origin"] == arm]
                      for name, rows in b_scored.items()}
        available = sorted({row["task_signature_evidence_available"]
                            for rows in arm_scored.values() for row in rows})
        relation_capable = {name: rows for name, rows in arm_scored.items()
                            if name not in analyses.POLICY_BASELINES_WITHOUT_RELATION_OUTPUT}
        by_arm[arm] = {
            "n_records": len(arm_scored["M3_TRAJECTORY_ONLY"]),
            "base_templates": len({row["unit_id"] for row in arm_scored["M3_TRAJECTORY_ONLY"]}),
            "task_signature_evidence_available_values": available,
            "task_signature_evidence_available_note": (
                "该臂保存输入内有 certified 任务签名" if available == [True] else
                "该臂配置刻意剥掉 certified 任务签名（消融本身），故 M4/M5 无任务证据可读"),
            "methods": {name: {**analyses.relation_metrics(rows, method=name),
                               "ask": analyses.ask_metrics(rows)}
                        for name, rows in arm_scored.items()},
            "paired": {
                "FULL_minus_M3": analyses.paired_difference(relation_capable, "M5_FULL_B_ARM_RULE", "M3_TRAJECTORY_ONLY"),
                "FULL_minus_M4": analyses.paired_difference(relation_capable, "M5_FULL_B_ARM_RULE", "M4_TASK_SIGNATURE_ONLY_GIVEN"),
            },
            "per_unit": {
                "FULL_minus_M3": analyses.per_unit_decomposition(relation_capable, "M5_FULL_B_ARM_RULE", "M3_TRAJECTORY_ONLY"),
            },
        }

    comparison["B_ABLATION_OVERNIGHT"] = {
        "primary_reporting_unit": "BY_ARM_OF_ORIGIN",
        "population_note": ("27 条保存首决策记录。每条记录内三方法读同一份保存输入，"
                            "但两臂首决策帧不同（各自 run 内触发），且 ABL_TRAJ_ONLY 的配置"
                            "刻意剥掉 certified 任务签名。因此按来源臂分别报告，"
                            "不提供合并主结果。"),
        "pooled_comparison_withheld_reason": (
            "合并 13 条『签名可读』与 14 条『签名被消融剥离』的记录，"
            "会把输入可用性差异报成候选未来/任务结构的机制增益，"
            "属授权明确禁止的比较；故不作为结果给出。"),
        "given_task_structure_disclosure": ("M4/M5 读取 method_input.task_signatures，"
                                            "该字段在 v3 契约 FORBIDDEN_METHOD_KEYS 中被列为答案字段；"
                                            "因此这两列只能读作『给定任务结构的规则验证』，"
                                            "不是自主恢复任务关系的能力。"),
        "by_arm_of_origin": by_arm,
        "pooled_for_reference_only": {
            "label": "REFERENCE_ONLY_CONFOUNDED_BY_INPUT_AVAILABILITY_DO_NOT_CITE_AS_MECHANISM_EFFECT",
            "methods": {name: analyses.relation_metrics(rows, method=name)
                        for name, rows in b_scored.items()},
        },
    }

    for split in ("DEV", "HIST"):
        key = f"C_RQ1_V3_{split}"
        method_full = {name: [row for row in rows if row["truth_status"] == "DEFINED"]
                       for name, rows in c_stage["scored"][split][masks.MASK_FULL].items()}
        relation_capable = {name: rows for name, rows in method_full.items()
                            if name not in ("M0_ALWAYS_ASK", "M1_NEVER_ASK")}
        comparison[key] = {
            "population_note": "同一份保存方法输入，全部方法逐样本读同一份，无跨来源臂问题。",
            "given_task_structure_disclosure": ("M4 读公开地图属性 successor_length_m，"
                                                "标签亦由该属性推出（原轮 INDEPENDENCE_AUDIT 已披露）；"
                                                "真值是允许输入的函数，M4/M5 的高分只能读作解析实现正确性。"),
            "methods": {name: {**analyses.relation_metrics(rows, method=name),
                               "ask": analyses.ask_metrics(rows)}
                        for name, rows in method_full.items()},
            "policy_baseline_relation_metrics": "N/A_NO_RELATION_OUTPUT（记 N/A，不填 0）",
            "paired": {
                "FULL_minus_M3": analyses.paired_difference(relation_capable, "M5_DRIVECLARIFY_FULL", "M3_TRAJECTORY_ONLY"),
                "FULL_minus_M4": analyses.paired_difference(relation_capable, "M5_DRIVECLARIFY_FULL", "M4_TASK_STATE_TOPOLOGY_ONLY"),
            },
            "per_unit": {
                "FULL_minus_M3": analyses.per_unit_decomposition(relation_capable, "M5_DRIVECLARIFY_FULL", "M3_TRAJECTORY_ONLY"),
                "FULL_minus_M4": analyses.per_unit_decomposition(relation_capable, "M5_DRIVECLARIFY_FULL", "M4_TASK_STATE_TOPOLOGY_ONLY"),
            },
        }

    receipts = a_summary["receipts"]
    comparison["A_RQ1_V4_FORMAL"] = {
        "population_note": ("原 48 计划 / 46 决策可评估分母保留。无保存候选轨迹，"
                            "M3 记 N/A（不填 0）。任务签名预给定，只能作为"
                            "给定任务结构下的规则验证。"),
        "M3_TRAJECTORY_ONLY": "N/A_NO_SAVED_CANDIDATE_TRAJECTORY",
        "M4_M5_status": "GIVEN_TASK_STRUCTURE_RULE_VERIFICATION_ONLY",
        "original_primary_results": a_summary["primary_results"],
        "receipt_audit": {
            "receipts_present": sum(1 for row in receipts if row["receipt_present"]),
            "raw_local_waypoint_distance_used_true_count": sum(
                1 for row in receipts if row.get("raw_local_waypoint_distance_used")),
            "passenger_true_intent_operand_present_true_count": sum(
                1 for row in receipts if row.get("passenger_true_intent_operand_present")),
            "runtime_true_intent_reads_before_ask_total": sum(
                int(row.get("runtime_true_intent_reads_before_ask") or 0) for row in receipts),
            "relation_distribution": _counter([row.get("relation") for row in receipts]),
            "gate_action_distribution": _counter([row.get("gate_action") for row in receipts]),
        },
        "historical_35_execution_note": ("原 35 例执行可评估如缺独立端点核验，只提示另需复核；"
                                         "本轮不扩展成整个 RQ3 审计，也不用它替代关系机制证据。"),
    }
    write_json(paths.OUT_RESULTS / "method_comparison.json", comparison)
    return comparison


def _counter(values: list[Any]) -> dict[str, int]:
    from collections import Counter
    return {str(key): count for key, count in sorted(Counter(values).items(), key=lambda item: str(item[0]))}


def _analysis_three(c_stage: dict[str, Any]) -> dict[str, Any]:
    """分析 3：输入缺失压力测试。"""
    log("分析 3：输入缺失压力测试")
    by_split: dict[str, Any] = {}
    for split in ("DEV", "HIST"):
        per_variant: dict[str, Any] = {}
        for variant in masks.MASK_ORDER:
            removed = masks.DECISIVE_EVIDENCE_REMOVED[variant]
            per_variant[variant] = {
                name: analyses.evidence_pressure_report(
                    [row for row in rows if row["truth_status"] == "DEFINED"], decisive_removed=removed)
                for name, rows in c_stage["scored"][split][variant].items()
            }
        by_split[f"C_RQ1_V3_{split}"] = per_variant

    label_undefined: dict[str, Any] = {}
    for split in ("DEV", "HIST"):
        rows = [row for row in c_stage["scored"][split][masks.MASK_FULL]["M5_DRIVECLARIFY_FULL"]
                if row["truth_status"] != "DEFINED"]
        label_undefined[f"C_RQ1_V3_{split}"] = {
            "n": len(rows),
            "category": "LABEL_UNDEFINED_UNDER_ALLOWED_INPUTS",
            "missing_evidence": "roadside_building_appearance",
            "excluded_from_classification_accuracy": True,
            "not_treated_as_gold_unknown": True,
            "note": "标签未定义样本不用于普通分类正确率，也不自动算成『应输出 UNKNOWN』的金标准。",
        }

    payload = {
        "mask_protocol": masks.mask_receipt(),
        "by_split": by_split,
        "label_undefined_handling": label_undefined,
        "three_way_separation": {
            "1_true_label_undefined": "truth_status=LABEL_UNDEFINED_UNDER_ALLOWED_INPUTS（44 样本，单列）",
            "2_truth_defined_but_evidence_insufficient": "mask 变体移除独立规定的决定性证据组（任务结构组）",
            "3_model_or_technical_input_failure": "本轮 0（离线 CPU 评分全部返回，见 sample_results.csv 的 reason_codes）",
        },
        "source_b_mask_not_constructed": (
            "来源 B 未构造 mask 变体：任务签名为运行时预给定的答案字段，"
            "离线剥离后无剩余合法任务证据可供公平比较，公平 mask 不成立；"
            "按授权完成已有结果复核并说明缺失，不新造数据。"),
    }
    write_json(paths.OUT_RESULTS / "missing_evidence_analysis.json", payload)
    return {"by_split": by_split, "payload": payload}


def _write_csvs(b_stage: dict[str, Any], b_scored: dict[str, Any], c_stage: dict[str, Any],
                comparison: dict[str, Any], analysis_one: dict[str, Any],
                analysis_three: dict[str, Any]) -> None:
    log("写 CSV")
    sample_rows: list[dict[str, Any]] = []
    b_distance_of = {row["sample_id"]: row["max_distance_m"] for row in b_stage["distances"]}
    for rows in b_scored.values():
        for row in rows:
            sample_rows.append({**row, "max_distance_m": b_distance_of.get(row["sample_id"]),
                                "reason_codes": ";".join(row["reason_codes"])})
    for split in ("DEV", "HIST"):
        distance_of = {row["sample_id"]: row["max_distance_m"] for row in c_stage["distances"][split]}
        for variant in masks.MASK_ORDER:
            trajectory_present = variant in (masks.MASK_FULL, masks.MASK_NO_TASK_STRUCTURE)
            for rows in c_stage["scored"][split][variant].values():
                for row in rows:
                    sample_rows.append({
                        **row,
                        "max_distance_m": distance_of.get(row["sample_id"]) if trajectory_present else None,
                        "reason_codes": ";".join(row["reason_codes"]),
                    })
    write_csv(paths.OUT / "sample_results.csv", sample_rows,
              ["source", "split", "sample_id", "unit_id", "arm_of_origin", "method", "mask_variant",
               "truth", "truth_status", "evidence_condition", "prediction", "ask_recommendation",
               "max_distance_m", "reads_given_task_signature", "reason_codes"])

    # 报告总体：B 按来源臂分别成行，C 各切分成行；A 无 methods 块（M3 为 N/A）。
    populations: list[tuple[str, dict[str, Any]]] = []
    for population, payload in comparison.items():
        if isinstance(payload.get("methods"), dict):
            populations.append((population, payload["methods"]))
        for arm, arm_payload in (payload.get("by_arm_of_origin") or {}).items():
            populations.append((f"{population}::{arm}", arm_payload["methods"]))

    method_rows: list[dict[str, Any]] = []
    for population, methods in populations:
        for name, metrics in methods.items():
            ask = metrics["ask"]
            method_rows.append({
                "population": population, "method": name, "n": metrics["n"],
                "relation_metrics_status": metrics.get("relation_metrics_status"),
                "independent_units": metrics["layout_or_template_count"],
                "balanced_accuracy": metrics["balanced_accuracy"],
                "balanced_accuracy_applicable": metrics["balanced_accuracy_applicable"],
                "equivalent_predicted_divergent": metrics["equivalent_predicted_divergent"],
                "divergent_predicted_equivalent": metrics["divergent_predicted_equivalent"],
                "unknown_count": metrics["unknown_count"],
                "determined_coverage": metrics["determined_coverage"],
                "conditional_accuracy_on_determined": metrics["conditional_accuracy_on_determined"],
                "high_ask_recommended": ask["high_task_critical"]["ASK_RECOMMENDED"],
                "high_unresolved": ask["high_task_critical"]["UNRESOLVED"],
                "low_unnecessary_ask_recommended": ask["low_task_equivalent"]["ASK_RECOMMENDED"],
                "low_unresolved": ask["low_task_equivalent"]["UNRESOLVED"],
            })
    write_csv(paths.OUT / "method_comparison.csv", method_rows,
              ["population", "method", "relation_metrics_status", "n", "independent_units",
               "balanced_accuracy", "balanced_accuracy_applicable",
               "equivalent_predicted_divergent", "divergent_predicted_equivalent",
               "unknown_count", "determined_coverage", "conditional_accuracy_on_determined",
               "high_ask_recommended", "high_unresolved",
               "low_unnecessary_ask_recommended", "low_unresolved"])

    four_rows: list[dict[str, Any]] = []
    for population, payload in analysis_one["four_cells"].items():
        for cell in analyses.FOUR_CELL_ORDER:
            entry = payload["cells"][cell]
            base = {"population": population, "cell": cell, "threshold_m": payload["threshold_m"],
                    "sample_count": entry["sample_count"],
                    "template_or_layout_count": entry["template_or_layout_count"]}
            for name, counts in analysis_one["errors"][population][cell].items():
                four_rows.append({**base, "method": name, "method_n": counts["n"],
                                  "correct": counts["correct"],
                                  "wrong_definite": counts["wrong_definite"],
                                  "unknown": counts["unknown"]})
    write_csv(paths.OUT / "four_cell_analysis.csv", four_rows,
              ["population", "cell", "threshold_m", "sample_count", "template_or_layout_count",
               "method", "method_n", "correct", "wrong_definite", "unknown"])

    missing_rows: list[dict[str, Any]] = []
    for population, variants in analysis_three["by_split"].items():
        for variant, per_method in variants.items():
            for name, payload in per_method.items():
                missing_rows.append({
                    "population": population, "mask_variant": variant, "method": name,
                    "n": payload["n"],
                    "decisive_evidence_removed": payload["decisive_evidence_removed"],
                    "unknown_count": payload["unknown_count"],
                    "determined_coverage": payload["determined_coverage"],
                    "definite_despite_insufficient_evidence_count":
                        payload["definite_despite_insufficient_evidence_count"],
                    "definite_despite_insufficient_evidence_rate":
                        payload["definite_despite_insufficient_evidence_rate"],
                    "definite_but_wrong_class_count": payload["definite_but_wrong_class_count"],
                    "definite_but_wrong_class_rate": payload["definite_but_wrong_class_rate"],
                })
    write_csv(paths.OUT / "missing_evidence_analysis.csv", missing_rows,
              ["population", "mask_variant", "method", "n", "decisive_evidence_removed",
               "unknown_count", "determined_coverage",
               "definite_despite_insufficient_evidence_count",
               "definite_despite_insufficient_evidence_rate",
               "definite_but_wrong_class_count", "definite_but_wrong_class_rate"])

    sensitivity_rows: list[dict[str, Any]] = []
    for population, payload in analysis_one["sensitivity"].items():
        for entry in payload["entries"]:
            sensitivity_rows.append({"population": population, **entry,
                                     "label": "POST_HOC_SENSITIVITY_ONLY"})
    write_csv(paths.OUT_RESULTS / "threshold_sensitivity.csv", sensitivity_rows,
              ["population", "multiplier", "threshold_m", "is_frozen_original",
               "n_with_valid_trajectory", "balanced_accuracy", "equivalent_predicted_divergent",
               "divergent_predicted_equivalent", "label"])


def main() -> int:
    paths.ensure_out()
    started = time.time()
    log("环境回执")
    environment = environment_receipt()

    data = _load_all()
    inventory = build_inventory(data["b_records"], data["c_rows"], data["a_summary"],
                               data["a_configs"], data["c_config"])
    write_json(paths.OUT_RESULTS / "DATA_INVENTORY.json", inventory)

    log("保存 mask 协议回执（评分之前）")
    write_json(paths.OUT_EVIDENCE / "MASK_PROTOCOL_RECEIPT.json", masks.mask_receipt())

    b_stage = _score_b_stage(data["b_records"])
    b_scored = b_stage["scored"]
    c_stage = _score_c_stage(data["c_rows"], data["c_config"])
    c_threshold = float(data["c_config"]["trajectory_threshold_m"])

    analysis_one = _analysis_one(b_stage, b_scored, c_stage, c_threshold)
    comparison = _analysis_two(data["b_records"], b_scored, c_stage, data["a_summary"])
    analysis_three = _analysis_three(c_stage)

    log("选取关键错误类型代表案例（固定 ID 顺序，真实记录）")
    case_index: dict[str, Any] = {}
    b_distance_of = {row["sample_id"]: row["max_distance_m"] for row in b_stage["distances"]}
    case_index["B_ABLATION_OVERNIGHT"] = cases.select_cases(b_scored, b_distance_of)
    for split in ("DEV", "HIST"):
        distance_of = {row["sample_id"]: row["max_distance_m"] for row in c_stage["distances"][split]}
        method_full = {name: [row for row in rows if row["truth_status"] == "DEFINED"]
                       for name, rows in c_stage["scored"][split][masks.MASK_FULL].items()}
        case_index[f"C_RQ1_V3_{split}"] = cases.select_cases(method_full, distance_of)
    write_json(paths.OUT_RESULTS / "CASE_INDEX.json", {
        "error_types_in_fixed_order": [row[0] for row in cases.ERROR_TYPES],
        "same_observation_candidate_combinations_not_independent": True,
        "by_population": case_index,
    })

    _write_csvs(b_stage, b_scored, c_stage, comparison, analysis_one, analysis_three)

    log("绘图")
    pressure_for_figure = {variant: analysis_three["by_split"]["C_RQ1_V3_HIST"][variant]
                           for variant in masks.MASK_ORDER}
    figures = make_figures(b_stage["distances"], b_stage["threshold"], c_stage["distances"],
                           c_threshold, analysis_one["four_cells"], pressure_for_figure)
    log(f"  图 {len(figures)} 张")

    status = STATUS_COMPLETE
    blockers: list[str] = []
    if c_stage["leaks"]:
        status, _ = STATUS_PARTIAL, blockers.append("MASK_LEAK_DETECTED")
    if any(not payload["identical"] for payload in c_stage["verify"].values()):
        status, _ = STATUS_PARTIAL, blockers.append("C_RECOMPUTATION_MISMATCH")
    if b_stage["fidelity"]["mismatches"]:
        status, _ = STATUS_PARTIAL, blockers.append("B_TRAJECTORY_FIDELITY_MISMATCH")

    final = {
        "schema": "driveclarify.rq1_conditional_supplement.final_status.v1",
        "status": status,
        "blockers": blockers,
        "status_meaning": ("COMPLETE 只表示本轮收窄补充完成，不表示原定完整视觉 RQ1 "
                           "或整篇论文全部验证完成。"),
        "environment": environment,
        "independent_unit_counts": {
            "A_RQ1_V4_FORMAL": {"rows": 48, "decision_evaluable": 46, "templates": 8,
                                "m3_status": "N/A_NO_SAVED_CANDIDATE_TRAJECTORY"},
            "B_ABLATION_OVERNIGHT": {"saved_first_decision_records": len(data["b_records"]),
                                     "base_templates": len({row["unit_id"] for row in data["b_records"]})},
            "C_RQ1_V3_DEV": {"samples": 64, "evaluable": 48, "base_layouts": 8},
            "C_RQ1_V3_HIST": {"samples": 112, "evaluable": 84, "base_layouts": 14},
            "C_UNSEEN_TEST": {"samples": 0},
        },
        "analyses_completed": {
            "four_cell": sorted(analysis_one["four_cells"]),
            "method_comparison": sorted(comparison),
            "missing_evidence": sorted(analysis_three["by_split"]),
        },
        "verification": {
            "b_trajectory_fidelity_exact": b_stage["fidelity"]["exact_match_within_1e_9"],
            "b_trajectory_fidelity_n": b_stage["fidelity"]["n"],
            "c_recomputation_identical": {split: payload["identical"]
                                          for split, payload in c_stage["verify"].items()},
            "c_mask_leak_count": len(c_stage["leaks"]),
        },
        "figures": figures,
        "elapsed_seconds": time.time() - started,
        "not_claimed": [
            "实际发送问题", "实际执行 ACT", "真实动态安全窗口", "闭环成功率",
            "停车收益", "主动等待收益", "视觉场景 grounding 能力", "未见测试集泛化",
            "人类一致性检验完成", "A/B 的 M4/M5 数值代表自主恢复任务关系的能力",
        ],
    }
    write_json(paths.OUT / "FINAL_STATUS.json", final)
    (paths.OUT_LOGS / "run.log").write_text("\n".join(LOG) + "\n", encoding="utf-8")
    log(f"状态 {status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
