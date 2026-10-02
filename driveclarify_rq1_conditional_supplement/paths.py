"""本轮所有输入输出路径集中在此，便于审计。"""

from __future__ import annotations

from pathlib import Path

REPO = Path("/home/buaa/wrh/DriveClarify")

# 来源 A：原 RQ1 正式批（48 计划 / 46 决策可评估），无保存候选轨迹
SRC_A = REPO / "reports/driveclarify_rq1_v4_ord_critical_stability_and_formal_v1"
SRC_A_RUNS = SRC_A / "formal_runs"
SRC_A_PRIMARY = SRC_A / "RQ1_V4_PRIMARY_RESULTS.json"
SRC_A_ROSTER = SRC_A / "FORMAL_ROSTER.json"

# 来源 B：闭环消融，27 次 native run 各保存首决策两候选轨迹
SRC_B = REPO / "reports/driveclarify_ablation_overnight_20260909_v1"
SRC_B_NATIVE = SRC_B / "formal/native"
SRC_B_CONFIGS = SRC_B / "formal_preparation/configs"
SRC_B_BINDINGS = SRC_B / "scenarios/evaluation_only"
SRC_B_EPISODES = SRC_B / "episode_results.csv"

# 来源 C：RQ1 V3 固定观测地图属性指代
SRC_C = REPO / "reports/driveclarify_rq1_grounded_relation_v3_20260910"
SRC_C_INPUTS = SRC_C / "method_inputs"
SRC_C_LABELS = SRC_C / "label_authority"
SRC_C_RESULTS = SRC_C / "results"
SRC_C_MANIFESTS = SRC_C / "manifests"

# 来源 D：V2 词法捷径诊断（只引用，不作性能证据）
SRC_D_DIAGNOSIS = SRC_C / "diagnosis/LEXICAL_SUFFICIENCY.json"

# 本轮交付目录
OUT = REPO / "reports/driveclarify_rq1_conditional_supplement_20260911"
OUT_FIG = OUT / "figures"
OUT_RESULTS = OUT / "results"
OUT_EVIDENCE = OUT / "evidence"
OUT_LOGS = OUT / "logs"


def ensure_out() -> None:
    for path in (OUT, OUT_FIG, OUT_RESULTS, OUT_EVIDENCE, OUT_LOGS):
        path.mkdir(parents=True, exist_ok=True)
