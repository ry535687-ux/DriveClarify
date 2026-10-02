"""把本轮状态并入仓库根 STATE.json，并追加根交接文档条目。

只做增量：STATE.json 原有 295 个顶层键全部保留，新增本轮键并更新
current_task / status / current_task_status 三个指针。四个 Markdown 文档一律追加，不覆盖。
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

ROOT = Path("/home/buaa/wrh/DriveClarify")
ROUND = ROOT / "reports/driveclarify_rq1_grounded_relation_v3_20260910"
KEY = "driveclarify_rq1_grounded_relation_v3_20260910"
STATUS = "RQ1_GROUNDED_RELATION_EVAL_PARTIAL_REVIEW_REQUIRED"


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def build_entry(final_status: dict[str, Any]) -> dict[str, Any]:
    dev = final_status["splits"]["DEV"]
    hist = final_status["splits"]["HIST"]
    return {
        "status": STATUS,
        "phase": "RQ1_TASK_RELATION_ON_FIXED_OBSERVATIONS_V3",
        "start_local": final_status["started"],
        "deadline_local": final_status["absolute_deadline"],
        "timezone": "Asia/Shanghai",
        "report_root": str(ROUND),
        "queue_identifier": final_status["queue_identifier"],
        "supersedes_round": "reports/driveclarify_rq1_grounded_relation_v2_20260910",
        "supersede_reason": final_status["previous_round_verdict"]["verdict"],
        "previous_round_preserved_unmodified": True,
        "unseen_test_split_status": final_status["unseen_test_split"]["status"],
        "layouts": final_status["totals"]["layouts"],
        "observations": final_status["totals"]["observations"],
        "samples": final_status["totals"]["samples"],
        "evaluable_samples": final_status["totals"]["evaluable_samples"],
        "label_undefined_samples": final_status["totals"]["label_undefined_samples"],
        "candidate_forward_records": final_status["totals"]["candidate_forward_records"],
        "balanced_accuracy_dev": dev["balanced_accuracy"],
        "balanced_accuracy_hist": hist["balanced_accuracy"],
        "full_minus_topology_only": 0.0,
        "method_implementation_status": final_status["method_implementation_status"],
        "frozen_trajectory_threshold_m": dev["frozen_trajectory_threshold_m"],
        "checkpoint_sha256": final_status["inference_evidence"]["checkpoint_sha256"],
        "code_version": final_status["inference_evidence"]["code_version"],
        "annotation_version": final_status["annotation"]["version"],
        "human_double_annotation_completed": False,
        "backbone_or_perception_trained": False,
        "pid_modified": False,
        "vehicle_control_applied": False,
        "questions_actually_sent": 0,
        "act_executions": 0,
        "carla_collection_performed": False,
        "not_claimed": final_status["not_claimed"],
        "blocks_not_resolved_by_lowering_standards": final_status["blocks_not_resolved_by_lowering_standards"],
        "human_decisions_required": final_status["human_decisions_required"],
        "final_status_path": str(ROUND / "FINAL_STATUS.json"),
        "final_report_path": str(ROUND / "FINAL_REPORT.md"),
        "test_report_path": str(ROUND / "TEST_REPORT.md"),
        "paper_draft_path": str(ROUND / "PAPER_RQ1_RESULTS_DRAFT.md"),
        "updated": now(),
    }


def main() -> int:
    final_status = json.loads((ROUND / "FINAL_STATUS.json").read_text(encoding="utf-8"))
    path = ROOT / "STATE.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    before = len(state)

    backup = ROOT / f"STATE.before_rq1_v3.{time.strftime('%Y%m%dT%H%M%S')}.json"
    shutil.copy2(path, backup)

    state[KEY] = build_entry(final_status)
    state["current_task"] = "RQ1 固定观测任务关系判断 V3（非同义反复重做）"
    state["status"] = STATUS
    state["current_task_status"] = STATUS

    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)

    print(f"STATE.json 顶层键 {before} -> {len(state)}（新增 {KEY}）")
    print(f"备份 {backup}")
    print(f"status = {state['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
