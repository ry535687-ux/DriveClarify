"""独立 finalizer：核对本轮实际产出，写出状态与 FINAL_REPORT.md。

状态由**实际文件与实际数字**决定，不由叙述决定：
  RQ1_GROUNDED_RELATION_EVAL_COMPLETE_REVIEW_REQUIRED   开发集与测试集都跑完
  RQ1_GROUNDED_RELATION_EVAL_PARTIAL_REVIEW_REQUIRED    部分完成
  CORE_NONORACLE_IMPLEMENTATION_UNAVAILABLE             非 oracle 主方法无法运行

COMPLETE 只表示冻结任务跑完，不表示 FULL 获胜、标注获得人类认可或论文结论独立通过。
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path("/home/buaa/wrh/DriveClarify")
ROUND = ROOT / "reports/driveclarify_rq1_grounded_relation_v2_20260910"


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def collect() -> dict[str, Any]:
    stages = read_json(ROUND / "queue/STAGE_STATE.json") or {"stages": {}}
    dev_analysis = read_json(ROUND / "results/DEV_ANALYSIS.json")
    test_analysis = read_json(ROUND / "results/TEST_ANALYSIS.json")
    assemble_dev = read_json(ROUND / "queue/S2_ASSEMBLE_DEV.json")
    precheck = read_json(ROUND / "queue/S5_TEST_PRECHECK.json")
    forwards_stage = read_json(ROUND / "queue/S1_DEV_FORWARDS.json")
    freeze = read_json(ROUND / "queue/S4_FREEZE.json")
    calibration = read_json(ROUND / "queue/S3_DEV_CALIBRATE.json")
    dependence = read_json(ROUND / "results/OBSERVATION_DEPENDENCE_DIAGNOSTIC.json")

    # 非 oracle 主方法是否真的跑起来了：M5 必须有非 UNKNOWN 的判断。
    nonoracle_ran = False
    if dev_analysis:
        for row in dev_analysis["summary"]["methods"]:
            if row["method"] == "M5_DRIVECLARIFY_FULL" and row["coverage"] > 0:
                nonoracle_ran = True
    if not nonoracle_ran:
        status = "CORE_NONORACLE_IMPLEMENTATION_UNAVAILABLE"
    elif dev_analysis and test_analysis:
        status = "RQ1_GROUNDED_RELATION_EVAL_COMPLETE_REVIEW_REQUIRED"
    else:
        status = "RQ1_GROUNDED_RELATION_EVAL_PARTIAL_REVIEW_REQUIRED"

    figures = sorted(path.name for path in (ROUND / "figures").glob("*.png")) if (ROUND / "figures").is_dir() else []
    plans = len(list((ROUND / "cache/candidate_forwards").glob("*/*_PLAN.json"))) if (ROUND / "cache/candidate_forwards").is_dir() else 0

    return {
        "schema_version": "driveclarify.rq1_v2_finalizer.v1",
        "finalized_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "status": status,
        "status_meaning": "COMPLETE/PARTIAL 只表示冻结任务的运行状态，不表示 FULL 获胜、标注获人类认可或结论独立通过。",
        "deadline_local": "2026-09-10T20:20:40+08:00",
        "stages": stages.get("stages", {}),
        "dev": {
            "ran": bool(dev_analysis),
            "independent_layout_count": assemble_dev and assemble_dev["layouts_with_real_observations"],
            "decision_sample_count": assemble_dev and assemble_dev["decision_sample_count"],
            "observation_count": assemble_dev and assemble_dev["observation_count"],
            "language_variant_count": assemble_dev and assemble_dev["language_variant_count"],
            "annotation_disputes": assemble_dev and len(assemble_dev["annotation_disputes"]),
            "real_gpu_candidate_forwards": plans,
            "selected_trajectory_threshold_m": calibration and calibration.get("selected_threshold_m"),
            "methods": dev_analysis and {row["method"]: {"balanced_accuracy": row["balanced_accuracy"], "coverage": row["coverage"]} for row in dev_analysis["summary"]["methods"]},
            "prespecified_main_comparisons": dev_analysis and dev_analysis["prespecified_main_comparisons"],
            "four_cell_counts": dev_analysis and dev_analysis["four_cell"]["cell_counts"],
        },
        "test": {
            "ran": bool(test_analysis),
            "planned_layout_count": precheck and precheck["planned_test_layout_count"],
            "layouts_with_real_observations": precheck and precheck["test_layouts_with_real_observations"],
            "status": precheck and precheck["status"],
            "blocked_reason": precheck and precheck.get("blocked_reason"),
            "historical_analyzed_layouts_reused_as_test": False,
            "duplicated_layouts_to_pad_scale": False,
        },
        "observation_dependence": dependence and {
            method: block["depends_on_observation_content"] for method, block in dependence["methods"].items()
        },
        "freeze": {
            "code_version": freeze and freeze.get("code_version"),
            "checkpoint_sha256": freeze and freeze.get("checkpoint_sha256"),
            "frozen_config": freeze and freeze.get("frozen_config"),
        },
        "forwards": {
            "unit_count": forwards_stage and forwards_stage.get("unit_count"),
            "checkpoint_matches_orientation_clue": False,
        },
        "figures": figures,
        "not_claimed": [
            "actual questions sent to a passenger",
            "actual ACT execution",
            "real dynamic safety window",
            "closed-loop success rate",
            "parking benefit",
            "active-waiting benefit",
            "human consistency check completed",
            "independent review completed",
            "visual scene grounding capability demonstrated",
            "unseen-test-set generalization",
        ],
        "human_decisions_required": [
            "是否删除 /home/buaa/wrh/simlingo/reports/driveclarify_rq1_grounded_relation_v2_20260910/（2.5M，因路径缺陷写入只读仓库，数据已按字节相同复制到本轮目录；删除类操作需人工确认）",
            "是否授权新一轮 CARLA 采集以运行 24 个测试布局（需原生 Ubuntu + 本地物理显示器；磁盘仅剩约 13G）",
            "是否接受 M4/M5 满分不依赖观测这一构造局限，或要求补充候选文本无法单独决定分支的场景",
            "是否安排真人双标注以解除 AUTOMATED_LABEL_V1_PENDING_HUMAN_REVIEW",
        ],
    }


def main(argv=()) -> int:
    value = collect()
    write_json(ROUND / "FINAL_STATUS.json", value)
    print(json.dumps({
        "status": value["status"],
        "dev_samples": value["dev"]["decision_sample_count"],
        "dev_layouts": value["dev"]["independent_layout_count"],
        "forwards": value["dev"]["real_gpu_candidate_forwards"],
        "test_status": value["test"]["status"],
        "figures": len(value["figures"]),
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
