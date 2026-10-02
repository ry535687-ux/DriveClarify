#!/usr/bin/env python3
"""任务 A 审计：来源 B 的**实际闭环 receipt** 与本轮离线判断器逐样本对照。

问题：离线 `judges.b_m5_full`（含「签名不可用时轨迹可升级」条款）是否真的
是该臂闭环里跑过的规则？还是一个只存在于离线的构造？

只读历史 receipt 与保存输入，不改任何实现。
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, "/home/buaa/wrh/DriveClarify")

from driveclarify_rq1_conditional_supplement import judges, judges_revised, loaders, paths  # noqa: E402

# live 关系词表 → 离线关系词表
LIVE_TO_OFFLINE = {
    "TASK_CRITICAL": "TASK_DIVERGENT",
    "TASK_EQUIVALENT": "TASK_EQUIVALENT",
    "UNKNOWN": "UNKNOWN",
}

THRESHOLD_M = 0.27119792945561905


def main() -> int:
    records = loaders.load_b_records()
    rows = []
    for record in records:
        recorded = record.get("recorded_decision") or {}
        live_relation = LIVE_TO_OFFLINE.get(str(recorded.get("relation")), str(recorded.get("relation")))
        m3 = judges.run_b_method("M3_TRAJECTORY_ONLY", record, threshold_m=THRESHOLD_M)
        m4 = judges.run_b_method("M4_TASK_SIGNATURE_ONLY_GIVEN", record, threshold_m=THRESHOLD_M)
        m5 = judges.run_b_method("M5_FULL_B_ARM_RULE", record, threshold_m=THRESHOLD_M)
        m5r = judges_revised.run_b_m5_revised(record, threshold_m=THRESHOLD_M)
        rows.append({
            "sample_id": record["sample_id"],
            "arm": record["arm"],
            "unit_id": record["unit_id"],
            "truth": record["truth"],
            "live_recorded_relation": live_relation,
            "live_reason": recorded.get("trajectory_metric_reason"),
            "offline_M3": m3["relation"],
            "offline_M4": m4["relation"],
            "offline_M5_original": m5["relation"],
            "offline_M5_revised": m5r["relation"],
            "signature_evidence_available": record["task_signature_evidence_available"],
        })

    def agreement(arm: str, column: str) -> dict:
        subset = [r for r in rows if r["arm"] == arm]
        mismatches = [r for r in subset if r[column] != r["live_recorded_relation"]]
        return {
            "n": len(subset),
            "n_agree_with_live": len(subset) - len(mismatches),
            "n_mismatch": len(mismatches),
            "mismatch_examples": [
                {"sample_id": r["sample_id"], "live": r["live_recorded_relation"], column: r[column]}
                for r in mismatches[:6]
            ],
        }

    arms = sorted({r["arm"] for r in rows})
    out = {
        "question": "离线 M5(FULL) 规则是否是该臂闭环实际跑过的规则？",
        "live_code_path": {
            "ABL_FULL": "driveclarify_rq1_v2/simlingo_agent.py:101-113 -> "
                        "driveclarify_rq1_v2.consequence.compare_task_signatures "
                        "(签名比较，无任何轨迹项)",
            "ABL_TRAJ_ONLY": "driveclarify_ablation_overnight/agent.py:24-41 "
                             "(TrajectoryRelationSeam) -> "
                             "driveclarify_task_relation_ablation_dev.relation.compare_trajectories "
                             "(纯轨迹阈值，无签名项)",
            "shared_gate": "driveclarify_rq1_v2.consequence.consequence_gate:157-202 "
                           "(默认出口 UNKNOWN -> fail-closed，非 ASK)",
        },
        "live_relation_distribution_by_arm": {
            arm: dict(Counter(r["live_recorded_relation"] for r in rows if r["arm"] == arm))
            for arm in arms
        },
        "n_live_unknown_ever_recorded": sum(1 for r in rows if r["live_recorded_relation"] == "UNKNOWN"),
        "agreement_vs_live": {
            arm: {col: agreement(arm, col) for col in
                  ("offline_M3", "offline_M4", "offline_M5_original", "offline_M5_revised")}
            for arm in arms
        },
        "rows": rows,
    }
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1, default=str)
    return 0


if __name__ == "__main__":
    sys.exit(main())
