"""把交接回执与修订回归并入 `FINAL_STATUS.json`。

`pipeline.py` 每次重跑都会重写 `FINAL_STATUS.json`，手工追加的字段会被覆盖。
本脚本在重跑后补回这些字段，使回执与实际交付一致。
"""

from __future__ import annotations

import json

from . import judges_revised, paths, regression

HANDOFF = {
    "timestamp": "2026-09-11T01:52:00+08:00",
    "AGENT_WORKLOG.md": "APPENDED_ONLY_9720_TO_9807_LINES",
    "COMMAND_LOG.md": "APPENDED_ONLY_2454_TO_2493_LINES",
    "CURRENT_HANDOFF.md": "PREPENDED_ONLY_8207_TO_8241_LINES",
    "NEXT_AGENT_PROMPT.md": "PREPENDED_ONLY_418_TO_453_LINES",
    "STATE.json": "MERGED_INCREMENTALLY_296_TO_299_KEYS_ZERO_EXISTING_KEYS_LOST",
    "STATE_backup": "STATE.before_rq1_conditional_supplement.20260911T015327.json",
    "stage_command_log_copy": "COMMAND_LOG.md",
    "change_manifest": "evidence/THIS_ROUND_CHANGE_MANIFEST.md",
    "rewrote_or_deleted_existing_records": False,
}


def main() -> int:
    status_path = paths.OUT / "FINAL_STATUS.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    report = json.loads((paths.OUT_RESULTS / "revision_regression.json").read_text(encoding="utf-8"))

    coverage_correct = coverage_wrong = 0
    per_condition: dict[str, dict[str, object]] = {}
    for population, payload in report["populations"].items():
        for condition, comparison in payload["by_condition"].items():
            cost = comparison["coverage_cost"]
            coverage_correct += cost["definite_to_unknown_that_was_correct"]
            coverage_wrong += cost["definite_to_unknown_that_was_wrong"]
            original = comparison["by_version"][regression.VERSION_ORIGINAL]
            revised = comparison["by_version"][regression.VERSION_REVISED]
            per_condition[f"{population}::{condition}"] = {
                "decisive_evidence_removed": comparison["decisive_evidence_removed"],
                "balanced_accuracy_original": original["balanced_accuracy"],
                "balanced_accuracy_revised": revised["balanced_accuracy"],
                "determined_coverage_original": original["determined_coverage"],
                "determined_coverage_revised": revised["determined_coverage"],
                "definite_but_wrong_class_original": original["evidence_pressure"][
                    "definite_but_wrong_class_count"],
                "definite_but_wrong_class_revised": revised["evidence_pressure"][
                    "definite_but_wrong_class_count"],
                "predictions_changed": cost["predictions_changed"],
            }

    status["equation_locator_correction"] = {
        "insufficient_evidence_clause": {
            "label": "eq:mask-unknown-cn",
            "correct_equation_number": 7,
            "previously_written_as": 10,
            "tex_lines": "171-179",
            "subsection_in_compiled_preview": "6.3.2",
        },
        "equation_10_actually_is": {
            "label": "eq:no-future-truth-leakage-cn",
            "tex_lines": "209-219",
            "content": "分歧时间真值与真实任务关系只用于评价，不得进入运行时输入",
        },
        "authority": "deliverables/driveclarify_method_latex_v1/preview_cn.aux (\\newlabel records)",
        "section_numbering_note": (
            "preview_cn.tex 设 setcounter{section}{5}，故编译后方法章为 §6；"
            "用户与历史交接称其为 §3.2，指同一段内容。docx 章号未能核实（文件不在主机）。"),
        "affects_measured_numbers": False,
        "locations_corrected": 11,
    }
    status["revision_regression"] = {
        "revision_id": judges_revised.REVISION_SPEC["revision_id"],
        "scope": "OFFLINE_INDEPENDENT_COPY_ONLY",
        "public_live_implementation_modified": False,
        "original_kept_side_by_side": True,
        "results_may_only_be_called": "REVISED_ANALYSIS_ON_EXPOSED_DATA",
        "may_be_called_frozen_test_results": False,
        "single_fixed_regression": True,
        "rule_not_retuned_for_favorable_outcome": True,
        "identical_where_task_evidence_available": {"pairs": 277, "mismatches": 0},
        "definite_judgments_without_task_evidence": {"pairs": 454, "definite": 0},
        "revised_m5_equals_m4_on_all_pairs": {"pairs": 731, "mismatches": 0},
        "aggregate_coverage_cost": {
            "correct_definite_given_up": coverage_correct,
            "wrong_definite_removed": coverage_wrong,
            "balanced_accuracy_improved_in_any_condition": False,
            "note": "修订不是改进，是取舍：没有任何条件的均衡正确率因此上升。",
        },
        "full_minus_trajectory_only_on_ablated_arm": {
            "original": -0.16666666666666663,
            "revised": -0.35416666666666663,
            "note": "修订使该臂上 FULL 与 M3 的差距进一步扩大；更不利的结果保留。",
        },
        "by_condition": per_condition,
        "verification_receipt": "evidence/REVISION_REGRESSION_VERIFICATION.json",
    }
    status["figures"] = list(status.get("figures", [])) + ["fig_revision_coverage_cost.png"]
    status["unit_tests"] = {
        "test_method_scope_semantics.py": 15,
        "test_revised_no_unsupported_escalation.py": 11,
        "total_passed": 26,
        "counted_as_experiment_samples": False,
    }
    status["handoff_updated"] = HANDOFF
    status_path.write_text(json.dumps(status, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"FINAL_STATUS.json 已补回 {len(status)} 个顶层键")
    print(f"  放弃正确确定判断 {coverage_correct} / 消除错误确定判断 {coverage_wrong}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
