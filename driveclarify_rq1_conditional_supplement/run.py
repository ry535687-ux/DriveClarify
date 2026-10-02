"""本轮唯一批处理入口：盘点 → 评分 → 统计 → 绘图 → 写报告。

CPU-only：不创建 CUDA context、不加载 GPU 模型、不新增 VLA forward、不启动 CARLA。
"""

from __future__ import annotations

import json
import platform
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping

from driveclarify_rq1_grounded_relation_v3 import methods as v3_methods
from driveclarify_rq1_grounded_relation_v3 import trajectory as v3_trajectory
from driveclarify_rq1_grounded_relation_v3.contracts import ContractError

from . import CPU_ONLY_CONTRACT, analyses, judges, loaders, masks, paths

STATUS_COMPLETE = "RQ1_CONDITIONAL_SUPPLEMENT_COMPLETE_REVIEW_REQUIRED"
STATUS_PARTIAL = "RQ1_CONDITIONAL_SUPPLEMENT_PARTIAL_REVIEW_REQUIRED"

C_METHODS = ("M0_ALWAYS_ASK", "M1_NEVER_ASK", "M3_TRAJECTORY_ONLY",
             "M4_TASK_STATE_TOPOLOGY_ONLY", "M5_DRIVECLARIFY_FULL", "M_LEX_CONTROL")

LOG: list[str] = []


def log(message: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {message}"
    LOG.append(line)
    print(line, flush=True)


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=1, ensure_ascii=False, sort_keys=False), encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    import csv
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


# ---------------------------------------------------------------- 环境回执

def environment_receipt() -> dict[str, Any]:
    loaded = set(sys.modules)
    disk = subprocess.run(["df", "-h", "/"], capture_output=True, text=True, check=False).stdout
    return {
        "schema": "driveclarify.rq1_conditional_supplement.environment.v1",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "python_version": platform.python_version(),
        "cpu_only_contract": CPU_ONLY_CONTRACT,
        "torch_imported": "torch" in loaded,
        "carla_imported": "carla" in loaded,
        "cuda_context_created": False,
        "disk_root": disk.strip().splitlines()[-1] if disk else None,
    }


# ---------------------------------------------------------------- 来源 C 评分

def c_max_distance(method_input: Mapping[str, Any], config: Mapping[str, Any]) -> tuple[float | None, str | None]:
    rows = method_input.get("candidate_futures") or []
    if len(rows) != 2:
        return None, "CANDIDATE_FUTURES_UNAVAILABLE"
    try:
        _, evidence = v3_trajectory.compare(
            rows[0], rows[1],
            threshold_m=float(config["trajectory_threshold_m"]),
            horizon_s=float(config["trajectory_horizon_s"]),
            step_s=float(config["trajectory_step_s"]))
        return float(evidence["max_l2_m"]), None
    except (ContractError, KeyError, TypeError, ValueError) as error:
        return None, str(error)


def score_c_variant(rows: list[dict[str, Any]], config: Mapping[str, Any], variant: str) -> dict[str, list[dict[str, Any]]]:
    """对来源 C 的一个 mask 变体，用 v3 冻结实现给全部方法打分。"""
    per_method: dict[str, list[dict[str, Any]]] = {name: [] for name in C_METHODS}
    for row in rows:
        masked = masks.apply_mask_c(row["method_input"], variant)
        for name in C_METHODS:
            result = v3_methods.evaluate_method(name, masked, config)
            per_method[name].append({
                "source": row["source"],
                "split": row["split"],
                "sample_id": row["sample_id"],
                "unit_id": row["unit_id"],
                "layout_id": row["layout_id"],
                "method": name,
                "mask_variant": variant,
                "truth": row["truth"],
                "truth_status": row["truth_status"],
                "evidence_condition": row["evidence_condition"],
                "prediction": result.relation.value,
                "ask_recommendation": result.ask.value,
                "reason_codes": list(result.reason_codes),
                "candidate_forward_count_reused_not_new": result.candidate_forward_count,
            })
    return per_method


def verify_c_recomputation(rows: list[dict[str, Any]], config: Mapping[str, Any], split: str) -> dict[str, Any]:
    """核验本轮 FULL_INPUT 重算与原轮可追溯预测逐样本一致。"""
    original = loaders.load_c_traceable_predictions(split)
    source = original.pop(("__source__", "__source__"))["path"]
    scored = score_c_variant(rows, config, masks.MASK_FULL)
    mismatches: list[dict[str, Any]] = []
    compared = 0
    for name, predictions in scored.items():
        for row in predictions:
            key = (name, row["sample_id"])
            if key not in original:
                continue
            compared += 1
            if original[key]["prediction"] != row["prediction"]:
                mismatches.append({"method": name, "sample_id": row["sample_id"],
                                   "original": original[key]["prediction"], "recomputed": row["prediction"]})
    return {"split": split, "traceable_prediction_source": source, "compared_count": compared,
            "mismatch_count": len(mismatches), "mismatches": mismatches[:20],
            "identical": not mismatches}


# ---------------------------------------------------------------- 来源 B 评分

B_METHOD_NAMES = ("M0_ALWAYS_ASK", "M1_NEVER_ASK", "M3_TRAJECTORY_ONLY",
                  "M4_TASK_SIGNATURE_ONLY_GIVEN", "M5_FULL_B_ARM_RULE")


def score_b(records: list[dict[str, Any]], threshold_m: float) -> dict[str, list[dict[str, Any]]]:
    per_method: dict[str, list[dict[str, Any]]] = {name: [] for name in B_METHOD_NAMES}
    for record in records:
        for name in B_METHOD_NAMES:
            result = judges.run_b_method(name, record, threshold_m=threshold_m)
            per_method[name].append({
                "source": record["source"],
                "sample_id": record["sample_id"],
                "unit_id": record["unit_id"],
                "arm_of_origin": record["arm"],
                "seed_tag": record["seed_tag"],
                "method": name,
                "mask_variant": masks.MASK_FULL,
                "truth": record["truth"],
                "truth_status": "DEFINED",
                "prediction": result["relation"],
                "ask_recommendation": result["ask_recommendation"],
                "reason_codes": result["reason_codes"],
                "reads_given_task_signature": result["reads_given_task_signature"],
                "task_signature_evidence_available": record["task_signature_evidence_available"],
            })
    return per_method


# ---------------------------------------------------------------- 数据盘点

def build_inventory(b_records: list[dict[str, Any]], c_rows: dict[str, list[dict[str, Any]]],
                    a_summary: dict[str, Any], a_configs: dict[str, Any],
                    c_config: Mapping[str, Any]) -> dict[str, Any]:
    a_receipts = a_summary["receipts"]
    a_signature_given = sum(1 for entry in a_configs.values() if entry["task_signatures_present"])
    entries: list[dict[str, Any]] = []

    # 来源 A
    entries.append({
        "source_id": "A_RQ1_V4_FORMAL",
        "origin_path": str(paths.SRC_A.relative_to(paths.REPO)),
        "original_run_ids": sorted(row["run_id"] for row in a_receipts),
        "planned_rows": len(a_receipts),
        "decision_evaluable_rows_original_denominator": a_summary["primary_results"]["counts"]["all"],
        "high_original": a_summary["primary_results"]["counts"]["high"],
        "low_original": a_summary["primary_results"]["counts"]["low"],
        "independent_units": len({row["run_id"].rsplit("-S", 1)[0] for row in a_receipts}),
        "independent_unit_kind": "FAMILY_X_CONDITION_TEMPLATE",
        "observation_id": "NOT_SAVED_AS_REUSABLE_FIXED_OBSERVATION",
        "candidate_texts_available": True,
        "candidate_trajectories_available": False,
        "candidate_trajectory_missing_reason":
            "决策收据 raw_local_waypoint_distance_used=false，未保存候选短期轨迹，本轮无法离线重算 M3",
        "task_fields_available": True,
        "task_field_provenance": "PRE_PROVIDED_CERTIFIED_task_signatures_IN_CELL_CONFIG",
        "task_signature_pre_given_config_count": a_signature_given,
        "label_source": "同一 cell 配置内的 certified task signature（与方法输入重叠）",
        "original_method_version": "driveclarify_rq1_v2/simlingo_agent.py + tools/run_rq1_v4_consequence_selectivity.py",
        "frozen_threshold_m": None,
        "threshold_missing_reason": "A 未使用轨迹距离判据，无阈值可核验",
        "exposure_status": "FULLY_EXPOSED_HISTORICAL",
        "usable_for_four_cell": False,
        "usable_for_method_comparison": False,
        "reportable_as": "GIVEN_TASK_STRUCTURE_RULE_VERIFICATION_ONLY",
    })

    # 来源 B
    templates = sorted({row["unit_id"] for row in b_records})
    entries.append({
        "source_id": "B_ABLATION_OVERNIGHT",
        "origin_path": str(paths.SRC_B.relative_to(paths.REPO)),
        "original_run_ids": sorted(row["sample_id"] for row in b_records),
        "planned_rows": 28,
        "saved_first_decision_records": len(b_records),
        "missing_record_reason": "第 25 次 LMK-E-S02-ABL_FULL 在 CARLA Vulkan SIGSEGV 后技术中断，无首决策候选记录",
        "independent_units": len(templates),
        "independent_unit_kind": "BASE_TEMPLATE",
        "templates": templates,
        "seed_tags": sorted({row["seed_tag"] for row in b_records}),
        "arms": sorted({row["arm"] for row in b_records}),
        "observation_id": "PER_RUN_NATIVE_SENSOR_FRAME (每 run 一个首决策观测)",
        "candidate_texts_available": True,
        "candidate_trajectories_available": True,
        "candidate_trajectory_shape": "9 点等时样本 0–2.0 s，步长 0.25 s",
        "task_fields_available": True,
        "task_field_provenance": "PRE_PROVIDED_CERTIFIED_task_signatures_IN_RUNTIME_CONFIG",
        "label_source": str(paths.SRC_B_BINDINGS.relative_to(paths.REPO)) + "/*_TASK_BINDING.json",
        "label_overlaps_method_input": True,
        "label_overlap_note": ("真值由 candidate_region_map 的区数推出；同一作者化绑定亦以 task_signatures "
                               "形式进入方法输入。v3 契约把 task_signatures 列为答案字段。"),
        "original_method_version": "driveclarify.v11.native-runtime-config.v1 / DCAOV_FORMAL_V1",
        "frozen_threshold_m": 0.27119792945561905,
        "threshold_source": "ablation.metric.threshold_m（DEV 两条无标签轨迹距离中位数，事前规则）",
        "coordinate_time_verified": True,
        "exposure_status": "FULLY_EXPOSED_HISTORICAL",
        "usable_for_four_cell": True,
        "usable_for_method_comparison": True,
        "method_comparison_caveat": ("两臂首决策帧不同（FULL 与 TRAJ_ONLY 各自 run 内触发），"
                                     "跨臂不是同输入；同输入对照只在每个 run 自己的保存记录内成立，"
                                     "并按来源臂分别报告。"),
        "reportable_as": "GIVEN_TASK_STRUCTURE_RULE_VERIFICATION + REAL_TRAJECTORY_FOUR_CELL",
    })

    # 来源 C
    for split, rows in c_rows.items():
        defined = [row for row in rows if row["truth_status"] == "DEFINED"]
        entries.append({
            "source_id": f"C_RQ1_V3_{split}",
            "origin_path": str(paths.SRC_C.relative_to(paths.REPO)),
            "planned_rows": len(rows),
            "evaluable_rows": len(defined),
            "label_undefined_rows": len(rows) - len(defined),
            "label_undefined_reason": "roadside_building_appearance 不在允许输入内",
            "independent_units": len({row["unit_id"] for row in rows}),
            "independent_unit_kind": "BASE_LAYOUT_JUNCTION",
            "observation_id": f"{len({row['observation_key'] for row in rows})} 个固定真实观测，每布局 1 个",
            "candidate_texts_available": True,
            "candidate_trajectories_available": True,
            "candidate_trajectory_shape": "11 点等时样本 0–2.5 s，步长 0.25 s",
            "task_fields_available": True,
            "task_field_provenance": "PUBLIC_MAP_ATTRIBUTES (successor_length_m / connector_heading_change_abs_deg)",
            "label_source": str((paths.SRC_C_LABELS / f"{split}_LABELS.jsonl").relative_to(paths.REPO)),
            "label_overlaps_method_input": True,
            "label_overlap_note": ("M4 与标签共同依赖同一 XML 属性 successor_length_m，"
                                   "原轮 INDEPENDENCE_AUDIT.md 已主动披露；真值是允许输入的函数。"),
            "original_method_version": "driveclarify_rq1_grounded_relation_v3",
            "frozen_threshold_m": float(c_config["trajectory_threshold_m"]),
            "threshold_source": "manifests/INFERENCE_CONFIG_DIGEST.json（DEV 校准，事前规则）",
            "frozen_horizon_s": float(c_config["trajectory_horizon_s"]),
            "frozen_step_s": float(c_config["trajectory_step_s"]),
            "exposure_status": ("DEV_EXPOSED_THRESHOLD_CALIBRATION" if split == "DEV"
                                else "HIST_EXPOSED_HISTORICAL_ANALYSIS"),
            "unseen_test_rows": 0,
            "usable_for_four_cell": True,
            "usable_for_method_comparison": True,
            "usable_for_missing_evidence": True,
            "reportable_as": "MAP_ATTRIBUTE_REFERRING_DIAGNOSTIC_SEPARATE_FROM_ORIGINAL_DRIVING_INSTRUCTION_POPULATION",
        })

    # 来源 D
    lexical = loaders.read_json(paths.SRC_D_DIAGNOSIS)
    entries.append({
        "source_id": "D_V2_LEXICAL_DIAGNOSIS",
        "origin_path": str(paths.SRC_D_DIAGNOSIS.relative_to(paths.REPO)),
        "reportable_as": "LEXICAL_SHORTCUT_DIAGNOSIS_ONLY_NOT_PERFORMANCE_EVIDENCE",
        "verdict": lexical.get("verdict") or lexical.get("status"),
        "usable_for_four_cell": False,
        "usable_for_method_comparison": False,
        "retained_note": "保留既有文本捷径诊断，不作主性能证据；v2 目录未修改。",
    })

    return {
        "schema": "driveclarify.rq1_conditional_supplement.data_inventory.v1",
        "selection_criteria": ["输入来源完整", "标签定义清楚", "比较条件可对齐"],
        "selection_criteria_not_used": ["FULL 是否获胜", "是否存在碰撞", "是否满足预期四格"],
        "sources_not_merged_into_single_population": True,
        "entries": entries,
    }


# ---------------------------------------------------------------- 绘图

def select_cjk_font() -> str | None:
    """显式指定系统已装的 CJK 字体，否则 DejaVu Sans 会把汉字画成方框。

    `fc-list` 里有 Noto Sans CJK SC，但 matplotlib 的 ttflist 未必收录，
    因此按候选顺序探测实际可用者并返回其名字，便于回执记录。
    """
    import matplotlib
    from matplotlib import font_manager

    available = {font.name for font in font_manager.fontManager.ttflist}
    for candidate in ("Noto Sans CJK SC", "Noto Sans CJK JP", "WenQuanYi Zen Hei",
                      "WenQuanYi Micro Hei", "Droid Sans Fallback", "AR PL UMing CN"):
        if candidate in available:
            matplotlib.rcParams["font.sans-serif"] = [candidate, "DejaVu Sans"]
            matplotlib.rcParams["axes.unicode_minus"] = False
            return candidate
    return None


def make_figures(b_rows: list[dict[str, Any]], b_threshold: float,
                 c_distance: dict[str, list[dict[str, Any]]], c_threshold: float,
                 four_cells: dict[str, Any], pressure: dict[str, Any]) -> list[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    select_cjk_font()

    written: list[str] = []

    def save(fig: Any, name: str) -> None:
        target = paths.OUT_FIG / name
        fig.savefig(target, dpi=150, bbox_inches="tight")
        plt.close(fig)
        written.append(name)

    # 图 1/2：轨迹距离分布，按真实任务关系分组（真实数据）
    for label, rows, threshold in (("B", b_rows, b_threshold), ("C", c_distance["ALL"], c_threshold)):
        fig, axis = plt.subplots(figsize=(7.2, 4.0))
        groups = [("TASK_EQUIVALENT", "#4C72B0"), ("TASK_DIVERGENT", "#C44E52")]
        plotted = False
        for truth, colour in groups:
            values = [float(row["max_distance_m"]) for row in rows
                      if row["truth"] == truth and row.get("max_distance_m") is not None]
            if values:
                axis.scatter(values, [truth] * len(values), s=44, alpha=0.72, color=colour,
                             edgecolor="white", linewidth=0.6, label=f"{truth} (n={len(values)})")
                plotted = True
        if plotted:
            axis.axvline(threshold, color="#444444", linestyle="--", linewidth=1.2,
                         label=f"冻结阈值 {threshold:.4g} m")
            axis.legend(loc="best", fontsize=8, frameon=False)
        axis.set_xlabel("候选轨迹最大等时 L2 距离 (m)")
        axis.set_title(f"来源 {label}：轨迹距离分布（按真实任务关系分组）", fontsize=10)
        axis.grid(axis="x", alpha=0.25)
        save(fig, f"fig_trajectory_distance_distribution_source_{label}.png")

    # 图 3：四格计数
    fig, axes = plt.subplots(1, len(four_cells), figsize=(5.2 * len(four_cells), 3.6), squeeze=False)
    for index, (name, payload) in enumerate(sorted(four_cells.items())):
        axis = axes[0][index]
        labels = list(analyses.FOUR_CELL_ORDER)
        counts = [payload["cells"][cell]["sample_count"] for cell in labels]
        short = [cell.replace("TRAJECTORY_", "").replace("TASK_", "").replace("__", "\n") for cell in labels]
        bars = axis.bar(short, counts, color=["#4C72B0", "#DD8452", "#C44E52", "#55A868"])
        for bar, count in zip(bars, counts):
            axis.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.3, str(count),
                      ha="center", fontsize=9)
        axis.set_title(f"{name}（阈值 {payload['threshold_m']:.4g} m）", fontsize=9)
        axis.set_ylabel("真实样本数")
        axis.tick_params(axis="x", labelsize=7)
        axis.grid(axis="y", alpha=0.25)
    fig.suptitle("四格：轨迹差异 × 任务差异（真实样本计数，空格记 0）", fontsize=11)
    save(fig, "fig_four_cell_counts.png")

    # 图 4：缺失证据压力测试
    fig, axis = plt.subplots(figsize=(8.4, 4.2))
    variants = list(masks.MASK_ORDER)
    method_names = sorted({name for payload in pressure.values() for name in payload})
    width = 0.8 / max(len(method_names), 1)
    for index, method in enumerate(method_names):
        heights = [pressure.get(variant, {}).get(method, {}).get("determined_coverage") or 0.0
                   for variant in variants]
        offsets = [position + index * width - 0.4 + width / 2 for position in range(len(variants))]
        axis.bar(offsets, heights, width=width, label=method)
    axis.set_xticks(range(len(variants)))
    axis.set_xticklabels([variant.replace("_AND_", "\n+") for variant in variants], fontsize=7)
    axis.set_ylabel("确定判定覆盖率")
    axis.set_ylim(0, 1.05)
    axis.legend(fontsize=7, frameon=False, ncol=2)
    axis.set_title("输入缺失压力测试（来源 C，离线 mask 变体，非真实遮挡）", fontsize=10)
    axis.grid(axis="y", alpha=0.25)
    save(fig, "fig_missing_evidence_coverage.png")

    return written
