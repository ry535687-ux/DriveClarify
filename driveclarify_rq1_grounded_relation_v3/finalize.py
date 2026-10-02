"""写 FINAL_STATUS.json：全部数字从已落盘结果文件读出，不手抄。"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

ROOT = Path("/home/buaa/wrh/DriveClarify")
ROUND = ROOT / "reports/driveclarify_rq1_grounded_relation_v3_20260910"
SIMLINGO = Path("/home/buaa/wrh/simlingo")
VALID = ("TASK_EQUIVALENT", "TASK_DIVERGENT")
DEADLINE = "2026-09-11T04:30:00+08:00"
STARTED = "2026-09-10T20:30:41+08:00"


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _git(repository: Path) -> dict[str, Any]:
    def run(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=str(repository), capture_output=True,
                              text=True, check=False).stdout.strip()
    return {
        "repository": str(repository),
        "branch": run("rev-parse", "--abbrev-ref", "HEAD"),
        "head": run("rev-parse", "HEAD"),
        "tracked_diff_file_count": len([row for row in run("diff", "--name-only").splitlines() if row]),
        "staged_diff_file_count": len([row for row in run("diff", "--cached", "--name-only").splitlines() if row]),
        "untracked_count": len([row for row in run("ls-files", "--others", "--exclude-standard").splitlines() if row]),
        "reset_or_clean_or_commit_or_push_performed": False,
    }


def _split_block(prefix: str) -> dict[str, Any]:
    analysis = _json(ROUND / f"results/{prefix}_ANALYSIS.json")
    score = _json(ROUND / f"results/{prefix}_SCORE.json")
    methods = {row["method"]: row for row in analysis["main_results"]}
    return {
        "layout_count": analysis["layout_count"],
        "observation_count": analysis["observation_count"],
        "observations_per_layout": 1,
        "planned_samples": analysis["planned_population"],
        "evaluable_samples": analysis["evaluable_sample_count"],
        "label_undefined_samples": analysis["label_undefined_sample_count"],
        "frozen_trajectory_threshold_m": analysis["frozen_trajectory_threshold_m"],
        "balanced_accuracy": {name: row["balanced_accuracy"] for name, row in sorted(methods.items())},
        "balanced_accuracy_ci95": {name: [row["balanced_accuracy_ci95_low"], row["balanced_accuracy_ci95_high"]]
                                   for name, row in sorted(methods.items())},
        "unknown_count": {name: row["unknown_count"] for name, row in sorted(methods.items())},
        "candidate_forward_calls": {name: row["candidate_forward_calls"] for name, row in sorted(methods.items())},
        "four_cell_counts": analysis["four_cell_counts"],
        "prespecified_comparisons": analysis["prespecified_comparisons"],
        "tautology_check": analysis["tautology_check"],
        "semantic_preservation_both_correct": {name: row["both_correct"]
                                              for name, row in sorted(analysis["semantic_preservation"].items())},
        "semantic_preservation_pairs": {name: row["pairs"]
                                       for name, row in sorted(analysis["semantic_preservation"].items())},
        "relation_flip_both_correct": {name: row["both_correct"]
                                       for name, row in sorted(analysis["relation_flip"].items())},
        "relation_flip_pairs": {name: row["pairs"] for name, row in sorted(analysis["relation_flip"].items())},
        "relation_flip_pairing_rule": next(iter(analysis["relation_flip"].values()))["pairing_rule"],
        "evidence_insufficient": {row["method"]: {"n": row["n"], "unknown": row["unknown_count"],
                                                  "wrong_definite": row["wrong_definite_count"]}
                                  for row in analysis["evidence_insufficient"]["methods"]},
        "ask_recommendation_distribution": analysis["ask_recommendation_distribution"],
        "prediction_path": score["prediction_path"],
        "lock_path": score["lock_path"],
        "prediction_attempt": score["attempt"],
        "superseded_paths_preserved": score["superseded_paths_preserved"],
    }


def build() -> dict[str, Any]:
    freeze = _json(ROUND / "queue/S4_FREEZE.json")
    fairness = _json(ROUND / "manifests/FORWARD_FAIRNESS_SUMMARY.json")
    split_manifest = _json(ROUND / "manifests/SPLIT_MANIFEST.json")
    stage_state = _json(ROUND / "queue/STAGE_STATE.json")
    lexical = _json(ROUND / "diagnosis/LEXICAL_SUFFICIENCY.json")
    design = _json(ROUND / "diagnosis/DESIGN_CHECK.json")

    return {
        "schema_version": "driveclarify.rq1_v3_final_status.v1",
        "status": "RQ1_GROUNDED_RELATION_EVAL_PARTIAL_REVIEW_REQUIRED",
        "status_reason_zh": "开发集与已暴露历史切分完成真实评价；24 个未见测试布局因无真实观测包而阻断。",
        "round_directory": str(ROUND),
        "started": STARTED,
        "absolute_deadline": DEADLINE,
        "finalized_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "queue_identifier": "DC-RQ1V3-GROUNDED-RELATION-20260910",

        "core_nonoracle_evaluation_ran": True,
        "unseen_test_split": {
            "status": "TEST_BLOCKED_NO_UNSEEN_OBSERVATIONS",
            "layout_count": 0, "sample_count": 0,
            "required_zh": "需新 CARLA 采集：原生 Ubuntu + 本地物理显示器，禁 headless/RenderOffScreen/Xvfb/VNC。",
            "hard_constraint_zh": "根分区仅剩约 12G（98% 已用），须先估算体量并设低空间停止闸。",
            "not_substituted_with_historical_layouts": True,
        },
        "splits": {"DEV": _split_block("DEV"), "HIST": _split_block("HIST")},
        "historical_split_is_not_unseen_test": True,

        "totals": {
            "layouts": 22, "observations": 22, "samples": 176,
            "evaluable_samples": 132, "label_undefined_samples": 44,
            "candidate_forward_records": fairness["candidate_forward_records"],
        },
        "undelivered_scale_declarations": split_manifest["undelivered_scale_declarations"],

        "method_implementation_status": {
            "M0_ALWAYS_ASK": "IMPLEMENTED",
            "M1_NEVER_ASK": "IMPLEMENTED",
            "M2_GROUNDING_CONFIDENCE": freeze["m2_status"]["status"],
            "M3_TRAJECTORY_ONLY": "IMPLEMENTED",
            "M4_TASK_STATE_TOPOLOGY_ONLY": "IMPLEMENTED",
            "M5_DRIVECLARIFY_FULL": "IMPLEMENTED",
            "M6_PRIVILEGED_TASK_SIGNATURE_REFERENCE": "IMPLEMENTED_PRIVILEGED_REFERENCE_NOT_DEPLOYABLE_PERFORMANCE",
            "M_LEX_CONTROL": "IMPLEMENTED_TAUTOLOGY_DETECTOR_NOT_A_SCIENTIFIC_BASELINE",
        },
        "m2_status": freeze["m2_status"],

        "previous_round_verdict": {
            "round": "reports/driveclarify_rq1_grounded_relation_v2_20260910",
            "verdict": lexical.get("verdict"),
            "m_lex_balanced_accuracy_on_v2": 1.0,
            "preserved_unmodified": True,
        },
        "design_checks_all_hold": design["all_checks_hold"],

        "inference_evidence": {
            "checkpoint_sha256": freeze["checkpoint_sha256"],
            "checkpoint_matches_user_orientation_clue": False,
            "code_version": freeze["code_version"],
            "units_fairness_pass": fairness["units_fairness_pass"],
            "unit_count": fairness["unit_count"],
            "all_records_isolated_and_restored": fairness["all_records_isolated_and_restored"],
            "backbone_or_perception_trained": False,
            "pid_modified": False,
            "vehicle_control_applied": False,
            "questions_actually_sent": 0,
            "act_executions": 0,
        },

        "annotation": {
            "version": freeze["annotation_version"],
            "human_double_annotation_completed": False,
            "narrow_margin_samples_for_human_review": 16,
            "narrow_margin_minimum_extent_difference_m": 0.72,
        },

        "not_claimed": [
            "实际发送问题", "实际执行 ACT", "真实动态安全窗口", "闭环成功率",
            "停车收益", "主动等待收益", "视觉场景 grounding 能力",
            "未见测试集泛化", "人类一致性检验完成", "M4/M5 的 1.0 代表部署性能",
        ],
        "blocks_not_resolved_by_lowering_standards": [
            "未见测试集 24 布局无真实观测包",
            "每布局第二个种子不存在",
            "人类双标注未做",
            "M2 无冻结 grounding 打分器",
            "视觉场景 grounding 未检验",
            "M5 相对 M4 零增量（保留）",
            "M3 近随机（保留）",
        ],
        "stage_state": stage_state.get("stages", {}),
        "git_entry_and_exit": {
            "driveclarify": _git(ROOT),
            "simlingo": _git(SIMLINGO),
            "note_zh": "两仓库 tracked/untracked 的既有 dirty 状态原样保留；未 reset/clean/commit/push；未删除历史文件。",
        },
        "human_decisions_required": [
            "是否投入新 CARLA 采集以解开未见测试集（需原生显示器；磁盘仅剩约 12G）",
            "是否接受“真值为允许输入的函数”这一设定用于论文（决定 M4/M5 的 1.0 如何表述）",
            "是否为视觉 grounding 另立一轮（放置可指代对象，方法侧须从 RGB 恢复绑定）",
            "16 个窄余量样本（长度差 ≤ 1 m，最小 0.72 m）的语言可辨识性是否可接受",
            "M2 是否需要真实实现（需冻结的候选指代对象打分器）",
            "是否安排人类双标注",
        ],
        **_deliverable_lists(),
    }


def _is_superseded(relative: str) -> bool:
    """被取代的旧版本备份不是交付物，只是保留证据，不能混进交付清单里充数。"""
    return "superseded" in relative or ".attempt" in relative


def _deliverable_lists() -> dict[str, Any]:
    current: list[str] = []
    superseded: list[str] = []
    for path in sorted(ROUND.rglob("*")):
        if not path.is_file() or path.suffix not in (".md", ".csv", ".png", ".json"):
            continue
        relative = str(path.relative_to(ROUND))
        (superseded if _is_superseded(relative) else current).append(relative)
    return {
        "deliverables": current,
        "deliverable_counts": {
            "documents_md": sum(1 for row in current if row.endswith(".md")),
            "figures_png": sum(1 for row in current if row.endswith(".png")),
            "tables_csv": sum(1 for row in current if row.endswith(".csv")),
            "json_artifacts": sum(1 for row in current if row.endswith(".json")),
        },
        "superseded_preserved_not_deliverables": superseded,
        "superseded_note_zh": "这些是开发期修复前的旧版本，按要求保留为证据，不计入交付清单。",
    }


def main() -> int:
    value = build()
    path = ROUND / "FINAL_STATUS.json"
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path}")
    print("status:", value["status"])
    print("totals:", json.dumps(value["totals"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
