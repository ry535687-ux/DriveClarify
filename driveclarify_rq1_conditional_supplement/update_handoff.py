"""把本轮结果并入根交接文件。

规则：
- `AGENT_WORKLOG.md` 只在末尾追加；
- `CURRENT_HANDOFF.md` / `NEXT_AGENT_PROMPT.md` 在开头前置新段，历史内容原样保留；
- `STATE.json` 增量并入，先备份，原有键全部保留；
- 根 `COMMAND_LOG.md` 末尾追加。

不删除、不重写既有记录。
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from . import paths

STAMP = "2026-09-11T01:52:00+08:00"
STATUS = "RQ1_CONDITIONAL_SUPPLEMENT_COMPLETE_REVIEW_REQUIRED"
STAGE_KEY = "driveclarify_rq1_conditional_supplement_20260911"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _prepend(path: Path, block: str) -> None:
    original = _read(path)
    path.write_text(block + "\n" + original, encoding="utf-8")


def _append(path: Path, block: str) -> None:
    original = _read(path)
    separator = "" if original.endswith("\n") else "\n"
    path.write_text(original + separator + block, encoding="utf-8")


WORKLOG_BLOCK = f"""
## {STAMP} — {STATUS}

新阶段 `reports/driveclarify_rq1_conditional_supplement_20260911`。用户为截稿收窄 RQ1，本轮唯一目标是
利用已有记录完成 RQ1 的离线补充比较、错误分析与正文收尾。**不是**完整视觉 grounding 实验，
**不是**旧 27 条闭环消融的重跑，**不是**把 v3 的 DEV/HIST 改名充未见测试集。
CPU-only：`torch_imported=false`、`carla_imported=false`、`cuda_context_created=false`、
新增 VLA forward **0**、未启动 CARLA、未训练、未改 PID/适配器/执行栈。唯一占 GPU 的是 root 的 ToDesk 远程桌面（363 MiB），与本轮无关。

**按来源分列，未合并成一个新测试集。** A `rq1_v4`：48 行 / 46 决策可评估 / 8 模板，
**46/46 收据 `raw_local_waypoint_distance_used=false`，从未保存候选轨迹**，故 M3 记 `N/A`（不填 0），
A 不参与四格与机制对照。B `ablation_overnight`：27 条首决策记录 / 7 基础模板（第 28 条 `LMK-E-S02-ABL_FULL`
因 CARLA Vulkan SIGSEGV 技术中断），冻结阈值 0.27119792945561905 m。C `rq1_v3`：DEV 64/48/8 布局、
HIST 112/84/14 布局、**TEST 仍为 0**，冻结阈值 0.10 m。D v2 词法诊断只引用，不作性能证据。
合计参与分析 B 27 条 / C 132 可评价，另 C 44 个标签未定义样本单列。种子、逐帧、候选组合、mask 变体均未当作独立单位。

**最重要的独立性披露：A 与 B 的方法输入内含答案字段。** v3 自身
`driveclarify_rq1_grounded_relation_v3/contracts.py:33` 的 `FORBIDDEN_METHOD_KEYS` 明确列入 `task_signatures`；
而 B 的 `method_input.task_signatures` 直接给出每候选 `task_completion_region`（如 `REF-C-BAY-1` vs `BAY-2`），
独立真值正是由同一份作者化绑定的 `candidate_region_map` 区数推出。故 **A 与 B 的 M4/M5 读的就是答案**，
只能写成「给定任务结构的规则验证」，不得包装成自主恢复任务关系的能力。C 的真值是允许输入的函数
（M4 与标签共依 `successor_length_m`，原轮已披露），M4/M5 = 1.0 只表示解析实现正确性，M4 完全不读 RGB。
常设检测器 M_LEX 在 C 上 DEV/HIST 均 **0.000**，与 M4/M5 的 1.0 不同，证明本轮真值**不可**由候选文本词法读出。

**查实并纠正了一处会构成不当比较的合并口径。** B 的 `ABL_TRAJ_ONLY` 臂配置**刻意剥掉** `certified` 与
`relevant_components`（该臂 `task_signatures` 仅剩 `{{candidate_id, binding_id}}`），这一剥离**就是该臂的消融本身**；
两臂首决策帧也不同（REF-C-S01 的 FULL 在 frame 741、TRAJ_ONLY 在 687）。若把 13 条「签名可读」与
14 条「签名被剥离」的记录合并成一个同输入总体，就会把输入可用性差异报成候选未来/任务结构的机制增益。
本轮改为**按来源臂分别报告，不提供合并主结果**，合并数值仅以
`REFERENCE_ONLY_CONFOUNDED_BY_INPUT_AVAILABILITY_DO_NOT_CITE_AS_MECHANISM_EFFECT` 标注保存。

**分析 1（四格，阈值取各来源原冻结值，未重新寻优）。** 无空格，轨迹无效样本 0。
接近×等价 / **不同×等价** / **接近×分歧** / 不同×分歧：B 4(1模板)/**7(2)**/**11(3)**/5(2)；
C DEV 31(8布局)/**1(1)**/**14(8)**/2(2)；C HIST 42(12)/**14(8)**/**21(12)**/7(5)。
第 3 格（轨迹接近但任务分歧）M3 **全部给出错误确定判断且 UNKNOWN 为 0**（B 11/11、C DEV 14/14、C HIST 21/21）——
只看轨迹结构上无法表达「我不知道」。第 2 格（轨迹不同但任务等价）B 7、C HIST 14，M3 全部建议不必要询问。
两格合计占 B 18/27、C DEV 15/48、C HIST 35/84。事后阈值敏感性 ×{{0.5,0.75,1.0,1.25,1.5}} 整组展示，
M3 均衡正确率全部落在 0.34–0.55，说明结论不来自某个特定阈值；但**不得**据此推断连续轨迹变量与任务关系总体独立。

**分析 2（机制对照，同一份保存输入）。** B::ABL_FULL(13/7) M3 0.325 / M4 1.000 / M5 1.000；
B::ABL_TRAJ_ONLY(14/7) M3 0.354 / M4 0.000（签名已被消融剥离）/ M5 0.188；
C DEV(48/8) 0.547 / 1.000 / 1.000；C HIST(84/14) 0.500 / 1.000 / 1.000。
**FULL − M4 = 0.000**（B::ABL_FULL、C DEV、C HIST 三总体一致）：任务结构可读时候选未来无可观察增量。
**在 ABL_TRAJ_ONLY 臂上 FULL 比 M3 更差（−0.167）**：该臂无签名可读，融合规则的升级条款使 FULL 继承 M3 的
4 例「等价误判为分歧」，另 7 例闭合 UNKNOWN。零增量与负增量均按授权保留，未改规则、未删对照。
Always/Never Ask 的关系分类指标记 **N/A**，不填 0。C HIST 逐模板 14/14 布局 FULL−M3 为正（0.375–0.625）。

**分析 3（输入缺失压力测试，C HIST 84 样本；mask 协议评分前已保存）。** 决定性证据由标签契约独立规定
（C 真值是公开地图属性的函数 → 决定性组=任务结构组），**不以 FULL 自身输出判定证据是否充分**。
移除**非**决定性的候选轨迹时 M4/M5 覆盖率仍 1.00 且 0 错——剩余合法证据足以确定关系，确定判断在此合理，
本轮**未**规定「任何字段缺失都必须 UNKNOWN」。移除决定性的任务结构证据后 M4 覆盖率 0.00 全部 UNKNOWN，
而 **M5 仍在 21/84 上作确定判断、其中 14 例类别答错**——融合规则升级条款的实测代价。
M3 在该条件下覆盖率仍 1.00。「证据不足仍确定」与「确定的类别答错」分别统计。
三类严格分开：标签未定义 44（单列，不入正确率，不当金标准）、真值已定义但证据不足、模型/技术失败 **0**。
来源 B 未构造 mask：其任务证据为运行时预给定答案字段，剥离后无剩余合法任务证据，公平 mask 不成立。

**§3.2 定向语义检查（15/15 手写单元测试，明确不计入实验样本）。** 实现侧不把「参照物不同」当「任务不同」：
v3 投影字段不含 `referring_expression_id`，B 判断器只比较 `relevant_components`。但正文
（`deliverables/driveclarify_method_latex_v1/method_cn.tex` 第 194 行）把分歧条件写成「不同的转向、道路分支
或指令执行位置」，未区分「作为任务目标的对象」与「仅用于定位动作的参照物」，按字面读与实现不一致，已给出补句建议。
**另查实一处明确语义不一致**：正文式 (10)「可比较证据不足 ⟹ UNKNOWN」与实现融合规则
`FUTURES_MAY_ONLY_ESCALATE_TO_DIVERGENT_WHEN_TOPOLOGY_UNAVAILABLE` 冲突。本轮**只报告冲突并用单元测试
固定住当前行为**，给出改正文/改实现两个方向但**不选择**，未改公共 live 实现，未改 docx。

**《论文20260909_方法正文精修.docx》不在主机**（已检索 `*.docx`、`*方法*`、`*正文*`、`*精修*`），
按授权改用已核验文本副本 `method_cn.tex`（2026-09-05），未猜测任何公式，缺失如实记录。

**7 项修复全部为本轮离线脚本自身缺陷，未改公共 live 实现。** 其中两项是我自己审计逻辑的假阳性：
mask 泄漏审计原用数值子串匹配，而 `STRAIGHT_BRANCH` 的弯度属性值恰为 `0.0`，命中每个轨迹坐标与时间戳，
产生 120 项假阳性；另把 `field_sources` 的取值 `public_map` 当泄漏 token，子串命中无关键名
`route_context.public_map_sha256`，再产生 40 项。改为结构化审计（字段名递归消失 + 组内独有 token 按 JSON
字面量整体匹配 + 排除来源分类标签），最终泄漏 **0** 项；错误审计原件留存为
`.superseded_substring_false_positive.json`，未删除。其余修复：合并口径改按臂、Always/Never Ask 关系指标改 N/A、
图注 CJK 字体（改用 Noto Sans CJK JP，重渲染 0 glyph 警告）、pytest 绕开 `PYTHONPATH` 上 ROS Foxy 的
`launch_testing` 插件崩溃（未改系统环境）、四格 CSV 按 `population::arm` 展开。

**三项核验回执全部通过。** B 轨迹指标保真：本轮重算最大等时 L2 与 B 记录的 `trajectory_metric_m`
**27/27 精确一致（≤1e-9）**；C 重算一致性：本轮 FULL_INPUT 与原轮可追溯预测逐样本一致，
DEV **384/384**、HIST **672/672** 无差异；C mask 泄漏 **0**。单元测试 15/15。

**不声称**：实际发送问题、实际执行 ACT、真实动态安全窗口、闭环成功率、停车收益、主动等待收益、
视觉场景 grounding 能力、未见测试集泛化、人类一致性检验完成，以及 A/B 的 M4/M5 数值代表自主恢复任务关系的能力。
所有新增分析标为补充机制分析 / 探索性分析，非预注册。两仓库均未 reset/clean/覆盖式 restore/checkout/commit/push，
未执行任何删除或清除类命令，未删除任何历史结果、预测、标签、reducer、fixtures 或 checkpoint；simlingo 完全未改动。
本轮仅新增 3 个未跟踪路径，交付 1.8M，根分区仍 12G 可用，未删旧文件腾位置。Bench2Drive 持续暂停，未自动恢复。
"""

HANDOFF_BLOCK = f"""## {STAMP} — {STATUS}

阶段入口：`reports/driveclarify_rq1_conditional_supplement_20260911/FINAL_REPORT.md`。
本轮按用户收窄授权，仅用**既有记录**完成 RQ1 的离线补充比较、错误分析与正文收尾；CPU-only，
新增 VLA forward 0，未启动 CARLA、未驾驶、未训练、未采集、未补 24 个未见布局、未实现新视觉/grounding 评分器。

**数据（按来源分列，未合并）**：A `rq1_v4` 48 行/46 决策可评估/8 模板，**无保存候选轨迹**（46/46 收据
`raw_local_waypoint_distance_used=false`）→ M3 记 N/A，不参与四格与机制对照；B `ablation_overnight`
27 条首决策/7 模板，阈值 0.27119792945561905 m；C `rq1_v3` DEV 64/48/8 布局、HIST 112/84/14 布局、**TEST 仍 0**，
阈值 0.10 m；D v2 词法诊断只引用。C 另有 44 个标签未定义样本单列。

**必须随数字一同引用的条件**：A 与 B 的 `method_input.task_signatures` 直接给出任务完成区域，
而该字段在 v3 自身 `contracts.py:33` 的 `FORBIDDEN_METHOD_KEYS` 中被列为答案字段，独立真值又由同一份
作者化绑定推出 → **A/B 的 M4/M5 只能写成「给定任务结构的规则验证」**；C 的真值是允许输入的函数，
M4/M5 = 1.0 只表示解析实现正确性，M4 完全不读 RGB。M_LEX 在 C 上 0.000，证明真值不可由候选文本词法读出。

**结果**：四格中「轨迹不同×任务等价」与「轨迹接近×任务分歧」两格合计占 B 18/27、C DEV 15/48、C HIST 35/84；
M3 在「接近×分歧」格全错且 UNKNOWN 为 0。**FULL − M4 = 0.000**（三总体一致）；
**B 的 ABL_TRAJ_ONLY 臂上 FULL 比 M3 更差 −0.167**。缺失证据下 M4 全闭合 UNKNOWN，
而 M5 仍在 21/84 上确定判断、14 例答错。零增量与负增量均保留。

**B 两臂不可合并**：`ABL_TRAJ_ONLY` 的配置刻意剥掉 certified 签名（该剥离即消融本身），两臂首决策帧也不同；
合并会把输入可用性差异报成机制增益，故不提供合并主结果。

**待人工决策**：(1) 是否接受「真值为允许输入的函数」用于论文；(2) §3.2 式 (10) 与实现融合规则升级条款的
冲突走改正文还是改实现（本轮只报告，未选）；(3) 是否为视觉 grounding 另立一轮（需可指代对象场景，
方法侧必须从 RGB 恢复绑定）；(4) 是否投入新 CARLA 采集解未见测试集（需原生 Ubuntu + 本地物理显示器，
根分区仅剩 12G）；(5) A 的 35 例执行端点是否安排独立复核；(6) 人类双标注是否安排；(7) M2 是否真实实现。

**停止在此**：只复核交付，不继续训练/扩样/新实验，不自动恢复 Bench2Drive，不进入视觉采集或停车修复。
两仓库 HEAD 未变，simlingo 完全未改动，未执行任何删除类命令。旧历史交接内容保留如下。

---
"""

PROMPT_BLOCK = f"""
## {STAMP} — RQ1 条件化补充已完成，等待人工决策

阶段：`reports/driveclarify_rq1_conditional_supplement_20260911`，状态 `{STATUS}`。
COMPLETE 只表示本轮收窄补充完成，**不**表示原定完整视觉 RQ1 或整篇论文全部验证完成。

必读顺序：
1. `reports/driveclarify_rq1_conditional_supplement_20260911/FINAL_REPORT.md`
2. `PROTOCOL_AND_DATA_INVENTORY.md`（§5 独立性披露是理解全部数字的前提）
3. `RQ1_REPLACEMENT.md`（可替换正文 §4.1–4.7、4 张表、图注）
4. `METHOD_SCOPE_ERRATA.md`（§3.2 语义检查与两处冲突）
5. `COMMAND_AND_SOURCE_INDEX.md`、`FINAL_STATUS.json`

**下一代理必须知道的三条硬约束**：

一、A 与 B 的 M4/M5 数字**不是**自主恢复任务关系的能力证据。其方法输入含 `task_signatures`，
该字段在 v3 自身契约中被列为答案字段，独立真值又由同一份作者化绑定推出。任何引用都必须带
「给定任务结构的规则验证」限定。C 的 M4/M5 = 1.0 同样只表示解析实现正确性，M4 完全不读 RGB。

二、B 的两个来源臂**不可合并**。`ABL_TRAJ_ONLY` 的配置刻意剥掉 certified 签名，该剥离即该臂的消融本身；
两臂首决策帧也不同。合并会把输入可用性差异报成机制增益。合并值已标注
`REFERENCE_ONLY_CONFOUNDED_BY_INPUT_AVAILABILITY_DO_NOT_CITE_AS_MECHANISM_EFFECT`，不得引用为机制效应。

三、未见测试集仍为 **0**。C 的 DEV/HIST 均已暴露，**重新分析后仍不得称为未见测试**。

**本轮未做、需明确授权才能做的事**：新 CARLA 采集与未见测试集、视觉 grounding 轮次（需可指代对象场景）、
M2 的真实实现（需冻结的候选指代对象打分器）、人类双标注、A 的 35 例执行端点独立复核、
§3.2 式 (10) 与融合规则升级条款的取舍（改正文或改实现，本轮只给方向未选）。
若选择改实现，按授权其结果只能称为**已暴露数据上的修订分析**，不得追认为原冻结测试结果。

**当前动作**：`STOP_AND_WAIT_FOR_EXPLICIT_USER_AUTHORIZATION`。不自动恢复 Bench2Drive，
不进入视觉采集、新消融、训练或停车修复。旧历史指针保留如下。

---
"""

COMMAND_BLOCK = f"""
## {STAMP} — RQ1 条件化补充（CPU-only 离线）

- 定向盘点 A/B/C/D 四来源结构与字段，20 分钟内完成后进入分析；未做全仓审计、未扩展文献检索。
- 遍历 27 个 `formal/native/*/owner_evidence/ABL_CANDIDATE_TIMELINE.jsonl`，核验来源 B 每条记录两候选
  共享同一观测（`nonlanguage_context_sha256` 与 `source_frame` 27/27 相同），故每条记录内同输入对照成立。
- 读 `formal_preparation/configs/*.json` 查实 `method_input.task_signatures` 预给定任务完成区域，
  且 `ABL_TRAJ_ONLY` 臂刻意剥掉 `certified`/`relevant_components`（该剥离即该臂消融本身）；
  据此取消两臂合并主结果，改按来源臂分别报告。
- 读 A 的 46 份 `RQ1_V2_CONSEQUENCE_DECISION_RECEIPT.json`，`raw_local_waypoint_distance_used` 全为 false，
  确认 A 无候选轨迹，M3 记 `N/A_NO_SAVED_CANDIDATE_TRAJECTORY`，不填 0。
- 新建 `driveclarify_rq1_conditional_supplement/`（10 模块），来源 C 直接复用 v3 冻结实现
  `driveclarify_rq1_grounded_relation_v3.methods`；来源 B 的 live 判断器在 native runtime 内无法离线调用，
  按其 `COORDINATE_TIME_CONTRACT` 与实际 decision receipt 重写等价 CPU 版本。
- 先写 `evidence/MASK_PROTOCOL_RECEIPT.json`（评分之前），再执行
  `python3 -m driveclarify_rq1_conditional_supplement.pipeline`（共 5 次，含 4 次修复后重跑）。
- 保真核验：本轮重算的最大等时 L2 与 B 记录的 `trajectory_metric_m` **27/27 精确一致（≤1e-9）**。
- 重算一致性：本轮 FULL_INPUT 与原轮可追溯预测（`DEV_predictions.attempt03.jsonl`、
  `HIST_predictions.attempt02.jsonl`）逐样本一致，DEV **384/384**、HIST **672/672** 无差异。
- 修复 mask 泄漏审计的两类假阳性（均为我自己审计逻辑的缺陷，非 mask 缺陷）：数值子串匹配下
  `STRAIGHT_BRANCH` 弯度值恰为 `0.0`，命中每个轨迹坐标与时间戳（120 项）；`field_sources` 取值
  `public_map` 子串命中无关键名 `route_context.public_map_sha256`（40 项）。改为结构化审计
  （字段名递归消失 + 组内独有 token 按 JSON 字面量整体匹配 + 排除来源分类标签），最终泄漏 **0**。
  修复前原件以 `cp` 另存 `evidence/C_MASK_LEAK_AUDIT.superseded_substring_false_positive.json`，未删除。
- 修复 Always/Never Ask 的关系分类指标被算成 0.000 → 改 `N/A_NO_RELATION_OUTPUT`，新增 `relation_metrics_status`。
- 修复图注中文渲染为方框：`fc-list :lang=zh` 与 `font_manager` 查询后选用 Noto Sans CJK JP，重渲染 0 glyph 警告。
- 运行 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/rq1_conditional_supplement/ -q`：**15/15 通过**。
  该环境变量用于绕开 `PYTHONPATH` 上 ROS Foxy 的 `launch_testing` 插件在本 Python 上的
  `asyncio.coroutine` 崩溃（环境问题，非测试失败）；未改系统环境。
- `nvidia-smi --query-compute-apps` 与 `ps -p 3822455`：唯一占 GPU 的是 root 的 ToDesk 远程桌面会话
  （363 MiB，7.5 小时前启动），与本轮无关；CPU-only 契约成立。
- 全程未执行任何删除或清除类命令（`rm`/`rmdir`/`unlink`/`git rm`/`git clean`/`find -delete`/`truncate`/`dd`/`mkfs`），
  未 reset、clean、覆盖式 restore/checkout、commit 或 push。两仓库 HEAD 未变，simlingo 完全未改动。
- 未新采集 CARLA，未启动常驻服务或待命队列，未启动子 agent 轮询。交付 1.8M，根分区仍 12G 可用，未删旧文件。

Primary status: `{STATUS}`（FULL 相对 M4 零增量、B 一臂上 FULL 更差、未见测试集仍为 0、
A 无候选轨迹故 M3 为 N/A，均按授权保留并如实报告）。
"""


def _stage_entry() -> dict:
    final = json.loads((paths.OUT / "FINAL_STATUS.json").read_text(encoding="utf-8"))
    comparison = json.loads((paths.OUT_RESULTS / "method_comparison.json").read_text(encoding="utf-8"))
    four = json.loads((paths.OUT_RESULTS / "four_cell_analysis.json").read_text(encoding="utf-8"))

    def balanced(population: str) -> dict:
        methods = comparison[population]["methods"]
        return {name: metrics["balanced_accuracy"] for name, metrics in methods.items()}

    arms = comparison["B_ABLATION_OVERNIGHT"]["by_arm_of_origin"]
    return {
        "status": final["status"],
        "phase": "RQ1_CONDITIONAL_SUPPLEMENT_OFFLINE_CPU_ONLY",
        "start_local": "2026-09-11T01:06:30+08:00",
        "timezone": "Asia/Shanghai",
        "report_root": str(paths.OUT),
        "narrowed_by_user_for_submission_deadline": True,
        "is_full_visual_grounding_experiment": False,
        "cpu_only_contract": final["environment"]["cpu_only_contract"],
        "new_vla_forward_count": 0,
        "carla_started": False,
        "sources_used": {
            "A_RQ1_V4_FORMAL": {
                "rows": 48, "decision_evaluable": 46, "independent_templates": 8,
                "candidate_trajectories_saved": False,
                "m3_status": "N/A_NO_SAVED_CANDIDATE_TRAJECTORY",
                "task_field_provenance": "PRE_PROVIDED_CERTIFIED_task_signatures",
                "reportable_as": "GIVEN_TASK_STRUCTURE_RULE_VERIFICATION_ONLY",
            },
            "B_ABLATION_OVERNIGHT": {
                "saved_first_decision_records": 27, "planned": 28,
                "independent_base_templates": 7,
                "missing_record": "LMK-E-S02-ABL_FULL (CARLA Vulkan SIGSEGV)",
                "frozen_threshold_m": 0.27119792945561905,
                "arms_may_be_pooled": False,
                "pool_prohibition_reason":
                    "ABL_TRAJ_ONLY config deliberately strips certified task signatures; "
                    "pooling would report input-availability difference as mechanism gain",
                "task_field_provenance": "PRE_PROVIDED_CERTIFIED_task_signatures",
                "reportable_as": "GIVEN_TASK_STRUCTURE_RULE_VERIFICATION + REAL_TRAJECTORY_FOUR_CELL",
            },
            "C_RQ1_V3_DEV": {"samples": 64, "evaluable": 48, "base_layouts": 8},
            "C_RQ1_V3_HIST": {"samples": 112, "evaluable": 84, "base_layouts": 14},
            "C_UNSEEN_TEST": {"samples": 0, "status": "STILL_ZERO_NOT_COLLECTED_THIS_ROUND"},
            "D_V2_LEXICAL_DIAGNOSIS": {"reportable_as": "DIAGNOSIS_ONLY_NOT_PERFORMANCE_EVIDENCE"},
        },
        "label_leak_disclosure": {
            "task_signatures_is_forbidden_answer_key_in_v3_contract": True,
            "contract_location": "driveclarify_rq1_grounded_relation_v3/contracts.py:33",
            "affects": ["A_RQ1_V4_FORMAL", "B_ABLATION_OVERNIGHT"],
            "c_truth_is_function_of_allowed_inputs": True,
            "m_lex_control_balanced_accuracy_on_c": {"DEV": 0.0, "HIST": 0.0},
            "m_lex_interpretation": "C 的真值不可由候选文本词法读出；v2 的同义反复缺陷未在 C 复现",
        },
        "four_cell_counts": {
            population: {cell: payload["cells"][cell]["sample_count"]
                         for cell in payload["cells"]}
            for population, payload in four["four_cells"].items()
        },
        "balanced_accuracy_by_population": {
            "B_ABLATION_OVERNIGHT::ABL_FULL": {
                name: metrics["balanced_accuracy"]
                for name, metrics in arms["ABL_FULL"]["methods"].items()},
            "B_ABLATION_OVERNIGHT::ABL_TRAJ_ONLY": {
                name: metrics["balanced_accuracy"]
                for name, metrics in arms["ABL_TRAJ_ONLY"]["methods"].items()},
            "C_RQ1_V3_DEV": balanced("C_RQ1_V3_DEV"),
            "C_RQ1_V3_HIST": balanced("C_RQ1_V3_HIST"),
        },
        "full_minus_topology_only": {
            "B_ABLATION_OVERNIGHT::ABL_FULL": 0.0,
            "C_RQ1_V3_DEV": 0.0,
            "C_RQ1_V3_HIST": 0.0,
            "note": "任务结构可读时候选未来无可观察增量；零增量结果保留",
        },
        "full_worse_than_trajectory_only": {
            "B_ABLATION_OVERNIGHT::ABL_TRAJ_ONLY": -0.16666666666666663,
            "cause": "融合规则升级条款使 FULL 继承 M3 的 4 例等价误判为分歧；负增量结果保留",
        },
        "missing_evidence_pressure_test": {
            "population": "C_RQ1_V3_HIST",
            "variants": 4,
            "mask_protocol_saved_before_scoring": True,
            "decisive_evidence_group": "TASK_STRUCTURE_AND_PUBLIC_TOPOLOGY",
            "decisive_group_not_determined_by_full_output": True,
            "no_task_structure_M4_determined_coverage": 0.0,
            "no_task_structure_M5_determined_coverage": 0.25,
            "no_task_structure_M5_definite_despite_insufficient_evidence": 21,
            "no_task_structure_M5_definite_but_wrong_class": 14,
            "source_b_mask_not_constructed_reason":
                "任务证据为运行时预给定答案字段，剥离后无剩余合法任务证据，公平 mask 不成立",
        },
        "label_undefined_samples": {"DEV": 16, "HIST": 28, "total": 44,
                                    "excluded_from_accuracy": True,
                                    "not_treated_as_gold_unknown": True},
        "method_semantics_check": {
            "target": "deliverables/driveclarify_method_latex_v1/method_cn.tex lines 107-221",
            "paper_docx_present_on_host": False,
            "paper_docx_name": "论文20260909_方法正文精修.docx",
            "unit_tests": "tests/rq1_conditional_supplement/test_method_scope_semantics.py 15/15 PASS",
            "unit_tests_counted_as_experiment_samples": False,
            "referring_expression_not_treated_as_task_difference": True,
            "conflict_found": "式 (10) Insufficient⇒UNKNOWN vs FULL_FUSION_RULE 的 "
                              "FUTURES_MAY_ONLY_ESCALATE_TO_DIVERGENT_WHEN_TOPOLOGY_UNAVAILABLE",
            "conflict_resolved_this_round": False,
            "live_implementation_modified": False,
            "docx_modified": False,
        },
        "verification": final["verification"],
        "repairs": 7,
        "repairs_all_in_this_round_offline_scripts": True,
        "false_positive_audits_fixed": 2,
        "analyses_are_exploratory_not_preregistered": True,
        "deliverables": [
            "PROTOCOL_AND_DATA_INVENTORY.md", "RQ1_REPLACEMENT.md", "METHOD_SCOPE_ERRATA.md",
            "FINAL_REPORT.md", "COMMAND_AND_SOURCE_INDEX.md", "FINAL_STATUS.json",
            "sample_results.csv", "method_comparison.csv", "four_cell_analysis.csv",
            "missing_evidence_analysis.csv", "figures/ (4)", "results/", "evidence/", "logs/",
        ],
        "not_claimed": final["not_claimed"],
        "pending_human_decisions": [
            "是否接受「真值为允许输入的函数」用于论文",
            "§3.2 式 (10) 与融合规则升级条款的冲突走改正文还是改实现",
            "是否为视觉 grounding 另立一轮",
            "是否投入新 CARLA 采集解未见测试集（根分区仅剩 12G）",
            "A 的 35 例执行端点是否安排独立复核",
            "人类双标注是否安排",
            "M2 是否真实实现",
        ],
        "next_action": "STOP_AND_WAIT_FOR_EXPLICIT_USER_AUTHORIZATION",
        "bench2drive": "REMAINS_PAUSED_NOT_AUTO_RESUMED",
    }


def main() -> int:
    entry = _stage_entry()

    state_path = paths.REPO / "STATE.json"
    backup = paths.REPO / f"STATE.before_rq1_conditional_supplement.{time.strftime('%Y%m%dT%H%M%S')}.json"
    shutil.copy2(state_path, backup)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    before_keys = len(state)

    state["_prev_status_before_rq1_conditional_supplement"] = state.get("status")
    state["_prev_current_task_before_rq1_conditional_supplement"] = state.get("current_task")
    state["status"] = STATUS
    state["current_task"] = "RQ1 条件化补充（离线机制分析与正文收尾）"
    state["current_task_status"] = STATUS
    state["stage"] = "RQ1_CONDITIONAL_SUPPLEMENT"
    state["step"] = ("A:48 rows/46 decision-evaluable, M3=N/A no saved trajectory; "
                     "B:27 saved first-decision records/7 templates, arms not pooled; "
                     "C:132 evaluable/22 layouts, TEST still 0; "
                     "FULL-M4=0.000 three populations; FULL<M3 on ABL_TRAJ_ONLY -0.167; "
                     "15/15 semantic unit tests; no automatic continuation")
    state[STAGE_KEY] = entry
    state_path.write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding="utf-8")

    _append(paths.REPO / "AGENT_WORKLOG.md", WORKLOG_BLOCK)
    _prepend(paths.REPO / "CURRENT_HANDOFF.md", HANDOFF_BLOCK)
    _prepend(paths.REPO / "NEXT_AGENT_PROMPT.md", PROMPT_BLOCK)
    _append(paths.REPO / "COMMAND_LOG.md", COMMAND_BLOCK)
    (paths.OUT / "COMMAND_LOG.md").write_text(COMMAND_BLOCK.lstrip("\n"), encoding="utf-8")

    print(f"STATE.json keys {before_keys} -> {len(state)}; backup {backup.name}")
    print("appended AGENT_WORKLOG.md, COMMAND_LOG.md; prepended CURRENT_HANDOFF.md, NEXT_AGENT_PROMPT.md")
    print(f"stage COMMAND_LOG.md written to {paths.OUT.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
