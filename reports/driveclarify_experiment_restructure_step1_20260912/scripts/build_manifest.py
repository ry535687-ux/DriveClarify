#!/usr/bin/env python3
"""生成本轮 MANIFEST.json：输入/输出路径、关键小文件 SHA-256、Git 状态、核验状态。

大型数据不整目录复制；已有可信摘要直接引用并注明。
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path("/home/buaa/wrh/DriveClarify")
OUT = REPO / "reports/driveclarify_experiment_restructure_step1_20260912"
SUPPLEMENT = REPO / "reports/driveclarify_rq1_conditional_supplement_20260911"

# 只对小文件算 SHA-256（阈值 4 MiB），大文件只记大小与路径。
SHA_SIZE_LIMIT = 4 * 1024 * 1024


def sha256_of(path: Path) -> dict:
    size = path.stat().st_size
    entry = {"path": str(path.relative_to(REPO)), "size_bytes": size}
    if size <= SHA_SIZE_LIMIT:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        entry["sha256"] = digest
    else:
        entry["sha256"] = None
        entry["sha256_skipped_reason"] = f"LARGER_THAN_{SHA_SIZE_LIMIT}_BYTES"
    return entry


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True,
                          text=True, check=False).stdout.strip()


def main() -> int:
    outputs = sorted(p for p in OUT.rglob("*")
                     if p.is_file() and "__pycache__" not in p.parts)
    inputs = [
        SUPPLEMENT / "sample_results.csv",
        SUPPLEMENT / "results/revision_regression_samples.csv",
        SUPPLEMENT / "RQ1_REPLACEMENT.md",
        SUPPLEMENT / "FINAL_STATUS.json",
        REPO / "driveclarify_rq1_conditional_supplement/judges.py",
        REPO / "driveclarify_rq1_conditional_supplement/judges_revised.py",
        REPO / "driveclarify_rq1_grounded_relation_v3/methods.py",
        REPO / "driveclarify_rq1_v2/consequence.py",
        REPO / "driveclarify_rq1_v2/simlingo_agent.py",
        REPO / "driveclarify_ablation_overnight/agent.py",
        REPO / "driveclarify_task_relation_ablation_dev/relation.py",
        REPO / "reports/driveclarify_rq1_v4_ord_critical_stability_and_formal_v1/RQ1_V4_PRIMARY_RESULTS.json",
        REPO / "reports/driveclarify_transparent_bypass_full_bench2drive_v2/PAIRED_ROUTE_LEDGER.json",
        REPO / "reports/driveclarify_full_bench2drive_standard_benchmark_v1/ALL_PAIRED_ROUTE_RESULTS.csv",
    ]

    docx = Path("/tmp/fuse/论文20260909_去附录版_220条结果.docx")
    manifest = {
        "schema": "driveclarify.experiment_restructure_step1.manifest.v1",
        "round_id": "DRIVECLARIFY_EXPERIMENT_RESTRUCTURE_STEP1_20260912",
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "final_status": "STEP1_PARTIAL_REVIEW_REQUIRED",
        "status_meaning": "可独立核查部分已完成；存在资料缺口（用户指定批注稿不在本机、"
                          "B2D 逐路线记录不足）与科学定义待决项（Q1/Q2）。"
                          "不代表方法有效、实验成功或已授权下一阶段。",
        "git": {
            "repository": str(REPO),
            "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
            "entry_head": "eaa332b1bb994279b59ea5af786fdb5de96adc1b",
            "exit_head": git("rev-parse", "HEAD"),
            "head_changed": git("rev-parse", "HEAD") != "eaa332b1bb994279b59ea5af786fdb5de96adc1b",
            "tracked_diff_empty": git("diff", "--stat") == "",
            "staged_diff_empty": git("diff", "--cached", "--stat") == "",
            "untracked_count_entry": 700,
            "untracked_count_exit": len([l for l in git("status", "--porcelain=v1").splitlines()]),
            "commits_made": 0,
            "destructive_git_commands_used": [],
        },
        "change_scope": {
            "production_code_modified": False,
            "live_decision_implementation_modified": False,
            "frozen_labels_or_historical_inputs_modified": False,
            "thresholds_modified": False,
            "new_files_only_in": str(OUT.relative_to(REPO)),
            "handoff_files_updated": [
                "AGENT_WORKLOG.md (append-only)",
                "COMMAND_LOG.md (append-only)",
                "CURRENT_HANDOFF.md (prepend-only)",
                "NEXT_AGENT_PROMPT.md (prepend-only)",
                "STATE.json (incremental merge, backup kept)",
            ],
            "live_migration_performed": False,
            "new_experiments_started": 0,
            "carla_launches": 0,
            "vla_forwards": 0,
            "models_loaded": 0,
            "cuda_contexts_created": 0,
        },
        "verification_status": {
            "rq1_counts_recomputed_from_per_record_data": True,
            "rq1_four_cell_reproduced": True,
            "rq1_method_comparison_reproduced": True,
            "rq1_table4_trajectory_only_misjudgement_resolved": {
                "C_HIST": 35, "C_DEV": 15,
                "cross_check_equals_table1_cells_2_plus_3": True,
            },
            "rq1_revision_regression_reproduced": {
                "sample_condition_pairs": 731,
                "changed_label_defined": 31,
                "wrong_definite_removed": 19,
                "correct_definite_lost": 12,
                "changed_label_undefined_additional": 18,
                "changed_total": 49,
            },
            "full_vs_task_structure_only": {
                "original_disagreements": 49,
                "original_disagreements_when_task_evidence_available": 0,
                "revised_disagreements": 0,
                "trajectory_increment_observed_in_any_population": False,
            },
            "live_vs_offline_judge_audit": {
                "live_full_arm_equals_signature_only_M4": "13/13",
                "live_traj_arm_equals_trajectory_only_M3": "14/14",
                "live_unknown_ever_recorded": 0,
                "offline_M5_on_traj_arm_ever_ran_in_closed_loop": False,
            },
            "entity_id_semantics": {
                "divergence_explained_only_by_entity_id_C_DEV": 0,
                "divergence_explained_only_by_entity_id_C_HIST": 0,
                "reference_entity_differs_but_same_target_B_configs": 20,
                "conflict_with_intro_example": False,
                "classification": "WORDING_CLARIFICATION_NOT_SCIENTIFIC_DEFINITION_CHANGE",
            },
            "bench2drive_pairing_check": {
                "claimed_in_paper_and_comment": "220 paired routes / 440 runs",
                "repo_paired_route_ledger_total": 220,
                "repo_complete_pairs": 101,
                "repo_A0_authoritative_completed": 101,
                "repo_A1_authoritative_completed": 101,
                "standard_benchmark_dir_rows_not_attempted": 220,
                "standard_benchmark_analysis_fields_null": True,
                "reproducible_from_repo_records": False,
                "errata_ref": "PAPER_ERRATA.md#A2",
            },
            "rq2_recomputed": False,
            "rq3_recomputed": False,
            "rq2_rq3_status": "HISTORICAL_REPORT_ONLY_SOURCE_AND_SCOPE_CHECK_ONLY",
            "c_test_split_exists": False,
            "unseen_test_set_count": 0,
        },
        "paper_input": {
            "user_designated_file": "《论文20260909改2.docx》",
            "user_designated_file_found": False,
            "search_performed": ["find / -iname '*改2*'", "find / -iname '*20260909*'"],
            "candidate_file_examined": str(docx),
            "candidate_file_is_user_designated": False,
            "candidate_comment_count": 1,
            "candidate_comment_topic": "Bench2Drive 220-route counts (author=Codex, 2026-09-08)",
            "method_or_section_3_2_comments_found": False,
            "rq_table_numeric_cells_empty": True,
            "field_codes_present": {"fldSimple": 0, "instrText": 0, "sdt": 0},
            "original_docx_modified": False,
            "comments_deleted": False,
            "method_refinement_docx_found": False,
        },
        "unit_tests": {
            "existing_test_method_scope_semantics": 15,
            "existing_test_revised_no_unsupported_escalation": 11,
            "new_test_step1_version_and_semantics": 21,
            "total_passed": 47,
            "pytest_invocation_note": "env -u PYTHONPATH required (ROS 2 Foxy on PYTHONPATH "
                                     "breaks collection under Python 3.13)",
            "counted_as_experiment_samples": False,
            "closed_loop_validation_implied": False,
            "pending_decision_tests": [
                "test_missing_relevant_field_outranks_valid_divergence_on_another_field",
            ],
        },
        "pending_user_decisions": {
            "Q1": "任一相关字段缺失 vs 已有有效分歧证据，谁优先？（现行=缺失优先；本轮数据未触发）",
            "Q2": "正文式 (7) 与 driveclarify_rq1_grounded_relation_v3/methods.py:205-211 谁让步？",
            "Q3": "来源 B 的 ABL_TRAJ_ONLY 臂定位与 PAPER_ERRATA §A1 的更正方式",
            "Q4": "Bench2Drive 220/440 如何写入（仓库仅 101 完整配对）",
            "Q5": "consequence_gate 的 QueryOK 缺位与 UNKNOWN 默认出口，改正文还是改实现",
            "task_relation_definition_frozen": False,
        },
        "recommended_next_rule_version": {
            "id": "DRIVECLARIFY_TASK_RELATION_CONTRACT_CANDIDATE_STEP1_20260912",
            "based_on": "driveclarify_rq1_conditional_supplement/judges_revised.py semantics",
            "document": "METHOD_CONTRACT_CANDIDATE.md",
            "status": "PROPOSED_FOR_REVIEW / NOT_AUTHORIZED_FOR_LIVE",
            "matches_current_live": False,
            "minimum_migration_items": 6,
            "migration_items_executed": 0,
        },
        "inputs": [sha256_of(p) for p in inputs if p.is_file()],
        "inputs_missing": [str(p) for p in inputs if not p.is_file()],
        "external_input_docx": {
            "path": str(docx),
            "exists": docx.is_file(),
            **({"size_bytes": docx.stat().st_size,
                "sha256": hashlib.sha256(docx.read_bytes()).hexdigest()}
               if docx.is_file() else {}),
            "access": "READ_ONLY_NOT_MODIFIED",
        },
        "reused_trusted_summaries_not_recopied": [
            "reports/driveclarify_rq1_conditional_supplement_20260911/evidence/"
            "B_TRAJECTORY_FIDELITY.json (27/27 exact, from prior round)",
            "reports/driveclarify_rq1_conditional_supplement_20260911/evidence/"
            "C_MASK_LEAK_AUDIT.json (0 leaks, from prior round)",
            "reports/driveclarify_rq1_conditional_supplement_20260911/METHOD_SCOPE_ERRATA.md "
            "(equation locator corrections, from prior round)",
        ],
        "outputs": [sha256_of(p) for p in outputs],
    }
    (OUT / "MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"MANIFEST.json written: {len(manifest['outputs'])} outputs, "
          f"{len(manifest['inputs'])} inputs hashed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
