"""把公式编号更正与融合规则修订回归追加进交接记录。

`AGENT_WORKLOG.md` 与根 `COMMAND_LOG.md` 只追加；
`CURRENT_HANDOFF.md` / `NEXT_AGENT_PROMPT.md` 只前置；
`STATE.json` 先备份再增量并入。既有行一律不重写。
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from . import paths

STAMP = "2026-09-11T02:35:00+08:00"
STATUS = "RQ1_CONDITIONAL_SUPPLEMENT_COMPLETE_REVIEW_REQUIRED"

WORKLOG = f"""
## {STAMP} — RQ1 条件化补充 追加轮：公式定位更正 + 融合规则修订回归

承接同日 01:52 的记录，同一交付目录 `reports/driveclarify_rq1_conditional_supplement_20260911`。
本轮仍为 CPU-only 离线：未新增数据、未新增模型推理、未训练、未驾驶、未启动 CARLA，
新增 VLA forward **0**。未反复调规则追求正结果——修订后只做**一次**固定回归。

**一、改正公式与版本定位（影响 11 处引用，不影响任何实测数字）。**
证据不足闭合条款的真实编号是 **式 (7)**（标签 `eq:mask-unknown-cn`，
`method_cn.tex` 第 171–179 行）。此前误记为「式 (10)」——式 (10) 实为
`eq:no-future-truth-leakage-cn`（第 209–219 行：分歧时间真值与真实任务关系
只用于评价、不得进入运行时输入），与证据不足条款无关。
编号依据编译产物 `preview_cn.aux` 的 `\\newlabel` 记录：`method_cn.tex` 前导区
**没有** `numberwithin` 或 `setcounter{{equation}}`，公式按全文顺序累加，
**行号不能当公式号**。另查实 `preview_cn.tex` 设 `\\setcounter{{section}}{{5}}`，
故该 PDF 中方法章为 **§6**（`候选解释与任务后果构造` = §6.3、
`同观测候选未来` = §6.3.2）；用户与历史交接称其为「§3.2」，指同一段内容，
docx 真实章号仍无法核实（文件不在主机）。11 处已全部改正，
只追加的交接文件以追加更正段处理，未重写既有行。

**二、在独立离线副本中取消没有任务证据支持的轨迹升级条款。**
实现 `driveclarify_rq1_conditional_supplement/judges_revised.py`，修订 ID
`RQ1_CONDITIONAL_SUPPLEMENT_20260911_NO_UNSUPPORTED_TRAJECTORY_ESCALATION_V1`。
只删两处分支：C 的 `methods.py:209-211`（`future_escalation_to_divergent_without_topology`）
与 B 的 `judges.py:131-134`（`trajectory_escalation_without_signature`）。
**不改**阈值（C 0.10 m / B 0.27119792945561905 m）、投影字段、标签、
任务等价定义、ASK 映射、候选未来无条件先算并计入代价的规定。
判据：候选轨迹按式 (6) 属 `C_VLA`（短期行为后果），不是任务义务 `C_obl`，故不算任务证据。
**公共 live 实现 `driveclarify_rq1_grounded_relation_v3/methods.py` 与 `judges.b_m5_full`
原样保留并列评分，未改一行。** 按授权，修订版数字只能称为
**已暴露数据上的修订分析**，不得追认为原冻结测试结果。

**三、一次固定的完整／缺失条件回归（10 个总体-条件，原版 vs 修订版并列）。**
条件表在执行前写定。来源 C 沿用已保存的 mask 协议四变体；
来源 B 的完整/缺失条件对来自该批消融**原本的设计**（`ABL_FULL` 签名可读 /
`ABL_TRAJ_ONLY` 签名被刻意剥掉），不是本轮新造 mask，两臂仍分别报告不合并。
结果（均衡正确率 原→修 / 确定覆盖率 原→修 / 错误确定 原→修）：
C DEV 完整 1.000→1.000 / 1.00→1.00 / 0→0；C DEV 无候选轨迹 同上；
C DEV 无任务结构 0.063→**0.000** / 0.06→**0.00** / 1→**0**（改判 3，原对 2 原错 1）；
C HIST 完整与无候选轨迹 均 1.000→1.000 / 1.00→1.00 / 0→0；
C HIST 无任务结构 0.125→**0.000** / 0.25→**0.00** / 14→**0**（改判 21，原对 7 原错 14）；
B::ABL_FULL 1.000→1.000 / 1.00→1.00 / 0→0；
B::ABL_TRAJ_ONLY 0.188→**0.000** / 0.50→**0.00** / 4→**0**（改判 7，原对 3 原错 4）。
两组「两者皆无」条件两版同为 0.000 / 0.00 / 0。

**合计：放弃 12 个原本正确的确定判断，消除 19 个错误的确定判断。**
**修订不是改进，是取舍——没有任何条件的均衡正确率因此上升**，
三个任务证据不可用的条件全部降到 0.000（UNKNOWN 按真值类别计为错误）。
修订消除的是「证据不足仍确定」这一错误类别（修订版 **0/454**），代价是这些条件下完全不作答。

**四、三项逐样本核验。**
(1) 任务证据**实际可用**的 **277** 个配对中两版不一致 **0** 个——判定依据是实现自身的
`_topology` / certified 签名，**不看 mask 标签**；故完整输入条件下全部主结果两版相同。
(2) 任务证据不可用的 **454** 个配对中修订版确定判断 **0** 个。
(3) **修订版 M5 与 M4 在全部 731 个样本-条件配对上逐样本同判（0 处不同）**——
取消升级条款后完整方法在本轮全部条件下退化为与只用任务结构不可区分。
这比原版「FULL−M4 = 0.000（均衡正确率相等）」是更强的零增量陈述。

**五、保留的不利结果。**
修订使 `ABL_TRAJ_ONLY` 臂上 FULL 与 M3 的差距从 **−0.167 扩大到 −0.354**
（M3 未受修订影响，仍 0.354）。未借修订改善该差值。

**六、自查纠正的一处断言错误（我自己的核验逻辑，非修订缺陷）。**
初版断言把「mask 变体 = FULL_INPUT」当成「任务证据可用」，并误把 `ABL_TRAJ_ONLY` 臂
（该臂签名被原设计剥离，正是修订应当改变的一臂）纳入「不应改变」的集合，
得到 16 处假不一致。改为按实现自身判定任务证据可用性后，不一致 **0**。
原回执以 `supersedes` 字段说明错误原因，未删除。
该次排查另**查实一处真实现象**：原版在 **9 个标签未定义样本**上，
即使在 `FULL_INPUT` 条件下也依升级条款给出确定 `TASK_DIVERGENT`
（DEV 4 / HIST 5，配置 `CFG_EVIDENCE_INSUFFICIENT`，缺 `roadside_building_appearance`
故任务证据本就不可用）；修订版在其上确定判断为 0。该组样本仍**不**用于正确率，
**也不**当作「应输出 UNKNOWN」的金标准，只作单列观察。

**七、重构后的回归自证。** 为让两张图共用一套 CJK 字体选择逻辑，
把 `run.py` 内联的字体探测提取为 `select_cjk_font()`。因该文件已被先前核验依赖，
重跑整条流水线并与备份逐文件比对：**21 个产物中 20 个逐字节相同**，
唯一差异是 `FINAL_STATUS.json` 的时间戳与 `elapsed_seconds`（去掉这两项后完全一致），
确认重构无行为改变。`FINAL_STATUS.json` 被流水线重写时丢掉的手工字段已由
`patch_final_status.py` 补回。

**八、交付新增。** `RQ1_REPLACEMENT.md` 改为三段结构
（一、比较问题含评价条件 → 二、结果 6 张表 + 5 条图注 → 三、反例与限制），
修订前版本保留为 `RQ1_REPLACEMENT.superseded_before_revision_regression.md`。
新增 `results/revision_regression.json`、`revision_regression.csv`、
`results/revision_regression_samples.csv`、
`figures/fig_revision_coverage_cost.png`、
`evidence/REVISION_REGRESSION_VERIFICATION.json`。
单元测试 **26/26** 通过（15 项固定原版行为 + 11 项固定修订副本行为，
均为手写用例，明确不计入实验样本）。

**正文侧仍未改动**：`method_cn.tex` 与 docx 均未修改，方向 A（改正文）未实施。
式 (7) 与融合规则常量的最终取舍仍待人工决定。
未 reset/clean/覆盖式 restore/checkout/commit/push，未执行任何删除或清除类命令，
simlingo 完全未改动。Bench2Drive 持续暂停。
"""

HANDOFF = f"""## {STAMP} — RQ1 条件化补充 追加轮：公式定位更正 + 融合规则修订回归

承接同日 01:52 记录，同一交付目录。入口仍为
`reports/driveclarify_rq1_conditional_supplement_20260911/FINAL_REPORT.md`；
正文改用三段结构的 `RQ1_REPLACEMENT.md`（旧版保留为 `...superseded_before_revision_regression.md`）。

**本轮三件事**：

一、**改正公式与版本定位**。证据不足闭合条款是 **式 (7)**（`eq:mask-unknown-cn`，
第 171–179 行），此前误记为式 (10)；式 (10) 实为 `eq:no-future-truth-leakage-cn`
（分歧时间真值不得进入运行时输入）。依据编译产物 `preview_cn.aux`——
`method_cn.tex` 无 `numberwithin`，**行号不能当公式号**。
`preview_cn.tex` 设 `setcounter{{section}}{{5}}`，故该 PDF 中方法章为 §6
（§6.3 / §6.3.2）；「§3.2」是用户与历史交接的习惯称法，指同一段内容，
docx 真实章号仍无法核实。11 处引用已改正，**不影响任何实测数字**。

二、**在独立离线副本中取消没有任务证据支持的轨迹升级条款**
（`judges_revised.py`；只删 C `methods.py:209-211` 与 B `judges.py:131-134` 两处分支，
不改阈值/投影/标签/ASK 映射/代价计数）。
**公共 live 实现原样保留并列评分，未改一行。**
按授权，修订版数字只能称为**已暴露数据上的修订分析**，不得追认为原冻结测试结果。

三、**一次固定的完整／缺失条件回归**（10 个总体-条件，条件表执行前写定，未反复调规则）。
关键数字：任务证据可用的 6 个条件两版**完全相同**；任务证据不可用的 3 个条件
（C DEV 无任务结构、C HIST 无任务结构、B::ABL_TRAJ_ONLY）覆盖率一律降为 **0.00**，
错误确定判断同时降为 **0**。**合计放弃 12 个原本正确的确定判断，消除 19 个错误确定判断。**

**下一代理必须知道的三条**（在 01:52 那三条之外新增）：

四、**修订不是改进，是取舍。** 没有任何条件的均衡正确率因修订上升；
三个任务证据不可用的条件全部降到 0.000。引用时必须同时写出放弃的 12 个正确判断。

五、**修订版 M5 与 M4 在全部 731 个样本-条件配对上逐样本同判（0 处不同）。**
取消升级条款后完整方法在本轮全部条件下与只用任务结构不可区分——
这比原版「FULL−M4 = 0.000」是更强的零增量陈述，对「完整方法必要性」的主张更不利。

六、**修订使 `ABL_TRAJ_ONLY` 臂上 FULL 与 M3 的差距从 −0.167 扩大到 −0.354。**
更不利的结果保留，未借修订改善差值。

**待人工决策**（在 01:52 那 7 项之外）：
(8) 式 (7) 与融合规则常量的最终取舍——改正文（方向 A，需承认无任务结构时
14/84 的错误确定判断）还是改实现（方向 B，即本轮离线修订，需接受这些条件下完全不作答）。
本轮只提供两侧实测依据，**未代为决定**，正文与 live 实现均未改。

**停止在此**：只复核交付，不继续训练/扩样/新实验，不自动恢复 Bench2Drive。
两仓库 HEAD 未变，simlingo 完全未改动，未执行任何删除类命令。旧历史交接内容保留如下。

---
"""

PROMPT = f"""
## {STAMP} — 追加轮完成：公式定位已更正，融合规则修订已在离线副本回归

同一阶段 `reports/driveclarify_rq1_conditional_supplement_20260911`，状态 `{STATUS}`。

必读顺序不变，但 `RQ1_REPLACEMENT.md` 已改为三段结构
（一、比较问题 → 二、结果 6 表 5 图注 → 三、反例与限制），
并新增 `results/revision_regression.json` 与 `METHOD_SCOPE_ERRATA.md` §0（版本定位）与
§1 检查 3（修订回归结果表）。

**引用前必须知道**：

- 证据不足闭合条款是 **式 (7)**（`eq:mask-unknown-cn`），不是式 (10)。
  编号依据 `preview_cn.aux`；`method_cn.tex` 无 `numberwithin`，行号≠公式号。
  编译入口设 `setcounter{{section}}{{5}}`，故 PDF 中方法章为 §6；「§3.2」是习惯称法。
- 修订只在 `judges_revised.py`（离线副本），**公共 live 实现未改一行**，原版并列保留。
  修订版数字只能称**已暴露数据上的修订分析**。
- **修订不是改进**：0 个条件的均衡正确率上升；放弃 12 个正确确定判断换消除 19 个错误确定判断。
- **修订版 M5 == M4，731/731 逐样本同判**；`ABL_TRAJ_ONLY` 臂 FULL−M3 由 −0.167 变 −0.354。

**未做、需明确授权才能做的事**（在前一段那几项之外）：
实施方向 A（改正文式 (7)）或把方向 B 落到公共 live 实现——两者都需人工决定，
本轮只提供两侧实测依据。若落到 live 实现，其后结果仍只能称为已暴露数据上的修订分析。

**当前动作**：`STOP_AND_WAIT_FOR_EXPLICIT_USER_AUTHORIZATION`。
不自动恢复 Bench2Drive，不进入视觉采集、新消融、训练或停车修复。旧历史指针保留如下。

---
"""

COMMANDS = f"""
## {STAMP} — 追加轮：公式定位更正 + 融合规则修订回归（CPU-only 离线）

- `cat preview_cn.tex`、`grep numberwithin/setcounter method_cn.tex`：查实无 `numberwithin`，
  公式全文顺序累加，行号不能当公式号；编译入口设 `setcounter{{section}}{{5}}`。
- `grep -o '\\\\newlabel{{eq:...}}' preview_cn.aux`：取全部 30 个公式的**真实渲染编号**，
  确认 `eq:mask-unknown-cn` = **式 (7)**、`eq:no-future-truth-leakage-cn` = 式 (10)。
  据此改正 11 处引用（此前把式 (7) 误记为式 (10)）。
- `grep -o '\\\\newlabel{{sec:...}}' preview_cn.aux`：取小节真实编号，
  确认 `候选解释与任务后果构造` = §6.3、`同观测候选未来` = §6.3.2。
- `sed -n '160,200p' method_cn.tex` 与 `sed -n '205,222p'`：逐字核对两个条款原文。
- 新建 `judges_revised.py`：取消 C `methods.py:209-211` 与 B `judges.py:131-134`
  两处无任务证据支持的升级分支；复用冻结私有函数 `_topology` / `_trajectory` / `_grounding`，
  不改阈值、投影字段、标签、ASK 映射与代价计数。公共 live 实现未改。
- 新建 `regression.py` 并执行 `python3 -m ...regression`（**一次固定运行**）：
  10 个总体-条件，原版 vs 修订版并列，逐样本 1462 行。
- 逐样本核验三条断言：任务证据实际可用 277 配对不一致 **0**；
  不可用 454 配对修订版确定判断 **0**；修订版 M5 与 M4 **731/731 同判**。
- 首版断言把「mask 标签 FULL_INPUT」当成「任务证据可用」，又误纳 `ABL_TRAJ_ONLY` 臂，
  得 16 处假不一致；改为按实现自身判定后为 0。原回执以 `supersedes` 字段保留说明，未删除。
  该次排查另查实：原版在 9 个标签未定义样本上于 FULL_INPUT 条件即给确定 `TASK_DIVERGENT`。
- 把 `run.py` 内联字体探测提取为 `select_cjk_font()`，供两张图共用。
- 重跑 `python3 -m ...pipeline` 并与备份逐文件 `diff`：**21 个产物 20 个逐字节相同**，
  唯一差异为 `FINAL_STATUS.json` 的时间戳与 `elapsed_seconds` → 确认重构无行为改变。
- `python3 -m ...patch_final_status`：补回流水线重写时丢掉的手工字段
  （交接回执、修订回归摘要、第 5 张图、单元测试计数、编号更正记录）。
- `python3 -m ...figure_regression`：出图 `fig_revision_coverage_cost.png`，0 条缺字警告。
- `cp -p RQ1_REPLACEMENT.md RQ1_REPLACEMENT.superseded_before_revision_regression.md`
  后改写为三段结构（未删除旧版）。
- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/rq1_conditional_supplement/ -q`：
  **26/26 通过**（15 项固定原版 + 11 项固定修订副本）。
  修订副本测试首轮 3 项失败，原因是我手写的 B 夹具字段名猜错
  （实际为 `trajectories` / `given_task_signatures`，`relevant_components` 是组件名列表）；
  按 `loaders.load_b_records()` 的真实记录结构改写夹具后通过。
- 全程未执行任何删除或清除类命令，未 reset/clean/覆盖式 restore/checkout/commit/push。
  未新增数据、未新增模型推理、未训练、未驾驶、未启动 CARLA，新增 VLA forward 0。

Primary status: `{STATUS}`（修订未提高任何条件的均衡正确率、修订版 M5 与 M4 逐样本同判、
`ABL_TRAJ_ONLY` 臂上差距扩大到 −0.354，均按授权保留并如实报告）。
"""


def _append(path: Path, block: str) -> None:
    text = path.read_text(encoding="utf-8")
    path.write_text(text + ("" if text.endswith("\n") else "\n") + block, encoding="utf-8")


def _prepend(path: Path, block: str) -> None:
    path.write_text(block + "\n" + path.read_text(encoding="utf-8"), encoding="utf-8")


def main() -> int:
    status = json.loads((paths.OUT / "FINAL_STATUS.json").read_text(encoding="utf-8"))

    state_path = paths.REPO / "STATE.json"
    backup = paths.REPO / f"STATE.before_rq1_revision_regression.{time.strftime('%Y%m%dT%H%M%S')}.json"
    shutil.copy2(state_path, backup)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    before = len(state)

    entry = state["driveclarify_rq1_conditional_supplement_20260911"]
    entry["equation_locator_correction"] = status["equation_locator_correction"]
    entry["revision_regression"] = status["revision_regression"]
    entry["unit_tests"] = status["unit_tests"]
    entry["method_semantics_check"]["conflict_found"] = (
        "式 (7) eq:mask-unknown-cn 的 Insufficient⇒UNKNOWN vs FULL_FUSION_RULE 的 "
        "FUTURES_MAY_ONLY_ESCALATE_TO_DIVERGENT_WHEN_TOPOLOGY_UNAVAILABLE")
    entry["method_semantics_check"]["equation_number_previously_misreported_as_10"] = True
    entry["method_semantics_check"]["conflict_resolved_this_round"] = False
    entry["method_semantics_check"]["direction_b_executed_in_offline_copy_only"] = True
    entry["deliverables"] = list(entry["deliverables"]) + [
        "revision_regression.csv", "results/revision_regression.json",
        "results/revision_regression_samples.csv",
        "figures/fig_revision_coverage_cost.png",
        "evidence/REVISION_REGRESSION_VERIFICATION.json",
        "RQ1_REPLACEMENT.superseded_before_revision_regression.md",
    ]
    entry["pending_human_decisions"] = list(entry["pending_human_decisions"]) + [
        "式 (7) 与融合规则升级条款的最终取舍：改正文（方向 A）还是把离线修订落到 live 实现（方向 B）",
    ]
    state["step"] = ("公式定位已改正（式 (7) 非式 (10)，依据 preview_cn.aux）；"
                     "无任务证据的轨迹升级条款已在离线副本取消并做一次固定回归；"
                     "任务证据可用的 6 条件两版相同，不可用的 3 条件覆盖率降为 0、错误确定降为 0；"
                     "放弃 12 个正确确定判断换消除 19 个错误确定判断，无条件均衡正确率上升；"
                     "修订版 M5 与 M4 731/731 同判；ABL_TRAJ_ONLY 臂 FULL−M3 由 −0.167 变 −0.354；"
                     "26/26 单元测试；公共 live 实现与正文均未改")
    state_path.write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding="utf-8")

    _append(paths.REPO / "AGENT_WORKLOG.md", WORKLOG)
    _append(paths.REPO / "COMMAND_LOG.md", COMMANDS)
    _append(paths.OUT / "COMMAND_LOG.md", COMMANDS)
    _prepend(paths.REPO / "CURRENT_HANDOFF.md", HANDOFF)
    _prepend(paths.REPO / "NEXT_AGENT_PROMPT.md", PROMPT)

    print(f"STATE.json 键 {before} -> {len(state)}（备份 {backup.name}）")
    print("AGENT_WORKLOG.md / COMMAND_LOG.md 追加；CURRENT_HANDOFF.md / NEXT_AGENT_PROMPT.md 前置")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
