"""修订回归的覆盖率代价图。只画本轮已算出的真实数字，不补造。"""

from __future__ import annotations

import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from . import paths, regression
from .run import select_cjk_font

LABELS = {
    "C_RQ1_V3_DEV::FULL_INPUT": "C DEV\n完整输入",
    "C_RQ1_V3_DEV::NO_CANDIDATE_FUTURES": "C DEV\n无候选轨迹",
    "C_RQ1_V3_DEV::NO_TASK_STRUCTURE_EVIDENCE": "C DEV\n无任务结构*",
    "C_RQ1_V3_DEV::NO_FUTURES_AND_NO_TASK_STRUCTURE": "C DEV\n两者皆无*",
    "C_RQ1_V3_HIST::FULL_INPUT": "C HIST\n完整输入",
    "C_RQ1_V3_HIST::NO_CANDIDATE_FUTURES": "C HIST\n无候选轨迹",
    "C_RQ1_V3_HIST::NO_TASK_STRUCTURE_EVIDENCE": "C HIST\n无任务结构*",
    "C_RQ1_V3_HIST::NO_FUTURES_AND_NO_TASK_STRUCTURE": "C HIST\n两者皆无*",
    "B_ABLATION_OVERNIGHT::ABL_FULL::ARM_SIGNATURE_AVAILABLE": "B::FULL\n签名可读",
    "B_ABLATION_OVERNIGHT::ABL_TRAJ_ONLY::ARM_SIGNATURE_ABLATED_BY_ORIGINAL_DESIGN":
        "B::TRAJ_ONLY\n签名被剥离*",
}


def main() -> int:
    select_cjk_font()
    report = json.loads((paths.OUT_RESULTS / "revision_regression.json").read_text(encoding="utf-8"))

    keys, cov_o, cov_r, wrong_o, wrong_r, decisive = [], [], [], [], [], []
    for population, payload in report["populations"].items():
        for condition, comparison in payload["by_condition"].items():
            key = f"{population}::{condition}"
            original = comparison["by_version"][regression.VERSION_ORIGINAL]
            revised = comparison["by_version"][regression.VERSION_REVISED]
            keys.append(LABELS.get(key, key))
            cov_o.append(original["determined_coverage"])
            cov_r.append(revised["determined_coverage"])
            wrong_o.append(original["evidence_pressure"]["definite_but_wrong_class_count"])
            wrong_r.append(revised["evidence_pressure"]["definite_but_wrong_class_count"])
            decisive.append(comparison["decisive_evidence_removed"])

    positions = range(len(keys))
    width = 0.38
    figure, axes = plt.subplots(2, 1, figsize=(13, 8.4), sharex=True)

    axes[0].bar([p - width / 2 for p in positions], cov_o, width,
                label="原版（冻结，含升级条款）", color="#4477aa")
    axes[0].bar([p + width / 2 for p in positions], cov_r, width,
                label="修订版（取消无任务证据的升级）", color="#cc6677")
    axes[0].set_ylabel("确定判定覆盖率")
    axes[0].set_ylim(0, 1.08)
    axes[0].set_title("M5 融合规则修订前后：确定判定覆盖率与错误确定判断\n"
                      "（* = 该条件下决定性任务证据不可用；离线输入缺失变体，非新场景或真实遮挡）")
    axes[0].legend(loc="upper right", fontsize=9)
    axes[0].grid(axis="y", alpha=0.3)

    axes[1].bar([p - width / 2 for p in positions], wrong_o, width,
                label="原版：确定但类别答错", color="#4477aa")
    axes[1].bar([p + width / 2 for p in positions], wrong_r, width,
                label="修订版：确定但类别答错", color="#cc6677")
    axes[1].set_ylabel("确定但类别答错（例数）")
    axes[1].set_xticks(list(positions))
    axes[1].set_xticklabels(keys, fontsize=8)
    axes[1].legend(loc="upper right", fontsize=9)
    axes[1].grid(axis="y", alpha=0.3)

    for index, (coverage, removed) in enumerate(zip(cov_r, decisive)):
        if removed and coverage == 0:
            axes[0].annotate("0", (index + width / 2, 0.02), ha="center", fontsize=8)

    figure.tight_layout()
    out = paths.OUT_FIG / "fig_revision_coverage_cost.png"
    figure.savefig(out, dpi=170)
    plt.close(figure)
    print(f"已写出 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
