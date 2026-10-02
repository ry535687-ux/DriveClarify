#!/usr/bin/env python3
"""按项目既有要求更新四个交接文件 + COMMAND_LOG.md。

规则（沿用上一轮）：
- AGENT_WORKLOG.md / COMMAND_LOG.md：**只追加**；
- CURRENT_HANDOFF.md / NEXT_AGENT_PROMPT.md：**只前置**，旧历史指针原样保留；
- STATE.json：**增量合并**，先备份，零既有键丢失。
"""
from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

REPO = Path("/home/buaa/wrh/DriveClarify")
STAMP = "2026-09-12T01:35:00+08:00"
BACKUP = REPO / "STATE.before_experiment_restructure_step1.20260912T013500.json"
ROUND_DIR = "reports/driveclarify_experiment_restructure_step1_20260912"

WORKLOG = f"""

## {STAMP} — STEP1：方法规则 / 任务定义 / 现有证据对齐（核对轮，非新实验）

交付目录 `{ROUND_DIR}`，状态 **`STEP1_PARTIAL_REVIEW_REQUIRED`**。
本轮不是新实验执行、不是代码重构、不是重做 RQ1。
入口/出口 HEAD 均为 `eaa332b1bb994279b59ea5af786fdb5de96adc1b`，未 commit、未 push。
生产代码 0 改动；live 迁移 0 项；CARLA 0 次；VLA forward 0；模型加载 0；CUDA context 0。

**一、最重要发现：来源 B 两臂的「融合规则」在闭环中不存在。**
用 27 条 live receipt 逐样本反查：
`ABL_FULL` 首决策与只读签名的 M4 **13/13 一致**；
`ABL_TRAJ_ONLY` 与纯轨迹的 M3 **14/14 一致**；live receipt 中 **UNKNOWN 0/27**。
代码结构印证：`agent.py:44-49` 只把 `TrajectoryRelationSeam` 混入 `TrajectoryBase`，
`FullBase` 原样继承 RQ3 agent → FULL 臂走
`driveclarify_rq1_v2/simlingo_agent.py:101-113` → `compare_task_signatures`
（**无任何轨迹项**，docstring 明写 "no trajectory-distance threshold"）；
TRAJ_ONLY 臂走 `compare_trajectories`（**无签名项**）。
两臂都没有「签名不可用 → 轨迹升级」分支。
因此离线 `judges.b_m5_full` 在 `ABL_TRAJ_ONLY` 臂上的 0.188 与 FULL−M3 = −0.167、
以及修订版的 −0.354，**都是离线构造，未进入闭环**。
`judges.py:30-32` 注释称该规则「取自 B 实际 receipt」，该说法在 FULL 臂成立、
在 TRAJ_ONLY 臂**不成立**。已记为 `PAPER_ERRATA.md` §A1。

**二、实体 ID 语义：现有实现已经正确，无需科学定义变更。**
C 的 `_topology` 只比较 `(maneuver, public_topology_target, ordering, constraint,
completion_predicate)`，`referring_expression_id` 只存于 `binding_evidence`，
**不参与比较**；live `TASK_COMPONENTS` 六分量中无任何参照实体分量。
实测「仅因实体 ID 而判分歧」= **0**（C DEV 0 / C HIST 0 / B 0）；
反向证据充足：C DEV 16 / C HIST 28 个样本参照表达式不同但任务投影全等 → 真值等价；
B 有 20 个配置参照描述不同但 `task_completion_region` 相同
（`-E-` 模板同 bay → 等价；`-C-` 模板异 bay → 分歧）。
与引言「不同白车可对应同一转弯任务」一致。所需修正是**表述澄清**
（在 §3.2 写明参照实体 vs 任务目标实体的不同角色），非定义变更，故**不重标历史样本**。
同时警告：**不要**删除所有 entity ID —— `task_completion_region` 本身就是任务要求。

**三、三值逻辑优先级：现行合同是「缺失优先」，但正文未规定 → 待决。**
`compare_task_signatures` 先调 `validation_errors()`，任一已声明相关分量
`RELEVANT_VALUE_MISSING` 即返回 UNKNOWN 且清空 `differing_components`，
**即使另一分量已有有效确定分歧**。C 的 `_topology` 同样是缺失优先（直接 raise）。
正文式 (7) 没有规定这一优先级，两种解释都能读通 → 记为 **Q1 待用户裁定**，
已固定为 PENDING_DECISION 测试，**本轮数据未触发该组合**，不改变任何已报数字。

**四、联合决策表默认出口发现两处不自洽（只记录，不重写状态机）。**
`consequence_gate:157-202` 的 `TASK_CRITICAL → ASK` **不检查** QueryOK 与时机条件，
而正文 P0097 把两者设为 Ask 的必要条件；UNKNOWN 的默认出口是
`UNKNOWN_FAIL_CLOSED_PRESERVE_EXISTING_AUTHORITY`（保持既有权限），正文未描述。
B 的 receipt 另有 `requested_action_after_joint_timing`，提示时机判断在 gate 之外
另有一层——**本轮未追溯该层**，标为未核验。

**五、RQ1 全部关键计数已从逐记录数据独立重算，与上一轮报告一致。**
四格：C DEV 31/1/14/2、C HIST 42/14/21/7、B 合计 4/7/11/5（逐臂 2/3/6/2 与 2/4/5/3）。
机制对照全部复现（M3 0.325/0.354/0.547/0.500；M4 与 M5 原版 1.000；
`ABL_TRAJ_ONLY` M4 0.000、M5 0.188；M_LEX 0.000）。
**优先项已核清**：仅轨迹对照「确定但类别答错」= **C HIST 35 / C DEV 15**，
并通过两个独立一致性检查（35 = 表 1 第 2+3 格 14+21；M3 在完整输入与
无任务结构下误判数相同，符合 M3 不读任务结构）。
修订回归：**731 配对（0 未配对）、19 撤回错误确定、12 放弃正确确定**。
**新发现口径差异**：实际改判 **49** 个 = 31（标签已定义）+ **18（标签未定义）**，
表 5「改判」列只计前者而未说明 → `PAPER_ERRATA.md` §A4。
全部 49 个方向均为 `TASK_DIVERGENT → UNKNOWN`，无反向。
**零增量如实保留**：原版 FULL−M4 = 0.000；修订版 M5 与 M4 **731/731 逐样本同判**。
明确记录「本轮全部总体均未显示候选轨迹增量」，未重新分组制造贡献。

**六、Bench2Drive 220 条 / 440 次运行在仓库内不可复算。**
`PAIRED_ROUTE_LEDGER.json`：`total=220`、**`complete_pairs=101`**；
两臂 `authoritative_completed=101`。
`driveclarify_full_bench2drive_standard_benchmark_v1/ALL_PAIRED_ROUTE_RESULTS.csv`
的 220 行**全部 `NOT_ATTEMPTED`**，`FULL_B2D_PAIRED_ROUTE_ANALYSIS.json`
成功计数与均值**全为 null**（空脚手架，不可引用）。
批注 id=32 的成功率算术自洽（169/220、171/220、差 0.91 pp），
但分母 220 与仓库的 101 完整配对不一致 → `PAPER_ERRATA.md` §A2。

**七、论文与批注核对存在明确缺口（不假装已读）。**
用户指定的《论文20260909改2.docx》**不在本机**（`find / -iname "*改2*"` 无结果）。
唯一同日候选 `/tmp/fuse/论文20260909_去附录版_220条结果.docx` 只有 **1 条批注**
（id=32，Bench2Drive 计数），**没有**方法 / §3.2 批注；
其 RQ1/RQ2/RQ3 表格数值单元格**全为空**
（`fldSimple=0`、`instrText=0`、`sdt=0` → 确实无内容，非域未更新）。
原 docx 只读，未修改、未删批注。`《论文20260909_方法正文精修.docx》`亦不在本机。

**八、C 无未见测试集。** `method_inputs/` 与 `label_authority/` 只有 `DEV_*` / `HIST_*`，
**TEST split 在磁盘上不存在** → 未见测试集计数 = **0**；DEV/HIST 均已暴露，
重新分析后仍不得称未见测试。44 个标签未定义样本（DEV 16 / HIST 28）单列，
不进分类正确率，也不算「应输出 UNKNOWN」的金标准。

**九、测试 47/47 通过**（26 既有 + **21 本轮新增**）。
新增覆盖：参照实体 vs 任务目标语义（含 `TASK_COMPONENTS` 词表守卫）、
缺失字段 vs 有效分歧优先级（PENDING_DECISION）、UNKNOWN/等价与控制授权分离、
live 与离线判断器版本对应、标签未定义不进分类分母、全 UNKNOWN 的覆盖率与准确率口径、
B 分臂配对键、修订回归 731/31/19/12、「无任何条件因修订提高均衡正确率」、
修订版与 M4 逐样本同判。expected 全部来自契约或独立逻辑案例，
**未从被测实现输出反向生成**；逻辑案例不计入论文样本；
测试通过**不表示**新规则已通过闭环验证。
注：pytest 需 `env -u PYTHONPATH`（ROS 2 Foxy 在 `PYTHONPATH` 上，
`asyncio.coroutine` 在 Python 3.13 已移除，否则 collection 失败）。

**十、后续推荐版本已提出但未获授权。**
`METHOD_CONTRACT_CANDIDATE.md`（`DRIVECLARIFY_TASK_RELATION_CONTRACT_CANDIDATE_STEP1_20260912`），
状态 **PROPOSED_FOR_REVIEW / NOT_AUTHORIZED_FOR_LIVE**，以 `judges_revised.py` 语义为基础，
**与当前 live 不一致**，最小迁移清单 6 项、**执行 0 项**。
必须同时接受：采用该候选后，完整方法在本轮全部条件下与只用任务结构**不可区分**；
若要主张候选未来有增量，需设计**新的**「任务结构不可读但仍有合法任务证据」条件。

**待用户裁定的最小问题**：Q1 缺失字段 vs 有效分歧优先级；Q2 式 (7) 与
`methods.py:205-211` 谁让步；Q3 `ABL_TRAJ_ONLY` 臂定位与 §A1 更正方式；
Q4 B2D 220/440 如何写入；Q5 gate 的 QueryOK 缺位与 UNKNOWN 默认出口。
**在 Q1–Q2 答复前，不得声称任务关系定义已冻结。**
未 reset/clean/覆盖式 restore/checkout/commit/push，未执行任何删除或清除类命令，
simlingo 完全未改动，Bench2Drive 持续暂停。
"""

COMMAND_LOG = f"""

## {STAMP} — STEP1 核对轮命令摘要（只追加）

阶段命令全表见 `{ROUND_DIR}/COMMAND_LOG.md`（40 条，含退出码与输出去向）。要点：

- 定位：`git rev-parse`（入口=出口 `eaa332b1bb…`）、`git status --porcelain=v1`
  （入口 700 / 出口 701 untracked，唯一新增为本轮目录）、
  `git diff` 与 `git diff --cached` **均空**（无 tracked 修改）。
- 论文：`find / -iname "*改2*"` **无结果**；只读抽取
  `/tmp/fuse/论文20260909_去附录版_220条结果.docx`（1 条批注、表格数值为空）。
  原文件未修改、未删批注。
- 重算：`scripts/recompute_rq1_counts.py`（exit 0）→ `results/RECOMPUTED_RQ1.json`；
  `scripts/entity_semantics_audit.py`（首次 exit 1，tuple 作 dict 键，修复后 exit 0）；
  `scripts/live_vs_offline_judge_audit.py`（exit 0）。
- 测试：`python3 -m pytest`（**collection error**，ROS 2 Foxy 在 `PYTHONPATH`）→
  改用 `env -u PYTHONPATH … -p no:cacheprovider`（exit 0）：
  既有 **26 passed**、新增 **21 passed**、合计 **47 passed**。
- 交付：`scripts/build_manifest.py`（exit 0，24 输出 / 14 输入 SHA-256）。
- **未执行**：任何删除或清除类命令；CARLA / evaluator / live shadow；VLA forward；
  模型加载；CUDA context；训练；下载；reset/clean/restore/checkout/commit/push。

Primary status: `STEP1_PARTIAL_REVIEW_REQUIRED`（关键口径已核清并复现，
但用户指定批注稿不在本机、B2D 逐路线记录仅 101/220 完整配对、Q1/Q2 科学定义待决）。
"""

HANDOFF = f"""## {STAMP} — STEP1：方法规则 / 任务定义 / 现有证据对齐（核对轮）

交付入口 `{ROUND_DIR}/FINAL_REPORT.md`，
状态 **`STEP1_PARTIAL_REVIEW_REQUIRED`**。
本轮**不是**新实验、**不是**代码重构、**不是**重做 RQ1。
入口/出口 HEAD 同为 `eaa332b1bb994279b59ea5af786fdb5de96adc1b`；生产代码 0 改动；
live 迁移 **0 项**；CARLA 0 次；VLA forward 0。

**引用本轮结论前必须知道的四件事**：

一、**来源 B 两臂的「融合规则」从未在闭环中运行。** 逐样本反查 27 条 live receipt：
`ABL_FULL` ≡ 只读签名的 M4（13/13）；`ABL_TRAJ_ONLY` ≡ 纯轨迹的 M3（14/14）；
live UNKNOWN **0/27**。代码上 FULL 臂走 `compare_task_signatures`（无轨迹项）、
TRAJ_ONLY 臂走 `compare_trajectories`（无签名项），两臂均无「签名不可用→轨迹升级」分支。
故 `RQ1_REPLACEMENT.md` 中该臂的 M5 = 0.188、FULL−M3 = −0.167、修订后 −0.354
**都是离线构造**，不得写成该臂闭环表现。→ `PAPER_ERRATA.md` §A1（本轮最重要更正）。

二、**实体 ID 没有被混同，无需科学定义变更。** 「仅因参照实体 ID 而判分歧」实测 **0**；
反向证据充足（C DEV 16 / C HIST 28 个样本参照表达式不同但任务投影全等 → 真值等价；
B 20 个配置参照描述不同但同一 bay）。与引言例子一致。
需要的只是**表述澄清**（§3.2 写明参照实体 vs 任务目标实体的不同角色）。
**不重标历史样本；也不要删除所有 entity ID**——`task_completion_region` 本身是任务要求。

三、**RQ1 全部关键计数已独立重算并复现**，包括此前待核对的
仅轨迹对照误判计数 = **C HIST 35 / C DEV 15**（两个独立一致性检查通过），
以及修订回归的 **731 配对 / 19 撤回错误确定 / 12 放弃正确确定**。
**新发现**：实际改判 **49** = 31（标签已定义）+ 18（标签未定义），
表 5「改判」列只计前者未说明 → §A4。
**零增量如实保留**：修订版 M5 与 M4 **731/731 逐样本同判**；本轮无任何总体显示轨迹增量。

四、**Bench2Drive 220/440 在仓库内不可复算**：账本 `complete_pairs=101`；
standard_benchmark 目录 220 行全 `NOT_ATTEMPTED`、分析字段全 null（空脚手架，不可引用）。
→ §A2。

**明确的资料缺口（不假装已读）**：用户指定《论文20260909改2.docx》**不在本机**；
唯一同日候选只有 1 条 Bench2Drive 批注、**无方法/§3.2 批注**、RQ 表格数值单元格全空。
C 的 **TEST split 不存在**，未见测试集计数 = 0。

**待用户裁定（Q1–Q2 未答前不得声称任务关系定义已冻结）**：
Q1 任一相关字段缺失 vs 已有有效分歧证据谁优先（现行=缺失优先，本轮数据未触发）；
Q2 正文式 (7) 与 `methods.py:205-211` 谁让步；
Q3 `ABL_TRAJ_ONLY` 臂定位与 §A1 更正方式；Q4 B2D 220/440 如何写入；
Q5 `consequence_gate` 的 QueryOK 缺位与 UNKNOWN 默认出口（fail-closed 保持既有权限）。

后续推荐版本见 `METHOD_CONTRACT_CANDIDATE.md`
（**PROPOSED_FOR_REVIEW / NOT_AUTHORIZED_FOR_LIVE**，与当前 live 不一致，
最小迁移 6 项、执行 0 项）。测试 **47/47** 通过（26 既有 + 21 新增，
需 `env -u PYTHONPATH`）。旧历史指针原样保留如下。

"""

PROMPT = f"""
## {STAMP} — STEP1 完成（核对轮）：等待 Q1–Q2 裁定，不得进入下一阶段

交付目录 `{ROUND_DIR}`，状态 **`STEP1_PARTIAL_REVIEW_REQUIRED`**。
必读顺序：`FINAL_REPORT.md` → `PAPER_ERRATA.md` → `METHOD_CONTRACT_CANDIDATE.md`
→ `EVIDENCE_LEDGER.csv` → `MANIFEST.json`。

**引用前必须知道**：

- **来源 B 两臂的融合规则从未进入闭环**：live `ABL_FULL` ≡ M4（13/13）、
  `ABL_TRAJ_ONLY` ≡ M3（14/14）、live UNKNOWN 0/27。
  该臂的 M5 = 0.188 / FULL−M3 = −0.167 / 修订后 −0.354 **均为离线构造**。
  → `PAPER_ERRATA.md` §A1。**不要**再把这些差值当作「完整方法更差」的闭环证据。
- **实体 ID 未被混同**（实测 0 反例），所需修正是表述澄清而非定义变更；
  **不要**重标历史样本，**不要**删除所有 entity ID。
- **零增量结论未变且更强**：修订版 M5 与 M4 **731/731 逐样本同判**；
  本轮**无任何总体**显示候选轨迹增量。不得改写措辞或重新分组制造贡献。
- **Bench2Drive 仓库内仅 101/220 完整配对**；standard_benchmark 目录是空脚手架，
  **不可引用**。无澄清活动的标准驾驶差异不得归因于主动澄清。
- **C 无 TEST split**，未见测试集 = 0；DEV/HIST 均已暴露，不得改称未见测试。
- 用户指定《论文20260909改2.docx》**不在本机**，方法/§3.2 批注**未能核对**。

**下一步唯一任务**：
**回答 Q1（任一相关字段缺失 vs 已有有效分歧证据谁优先）与
Q2（正文式 (7) 与 `driveclarify_rq1_grounded_relation_v3/methods.py:205-211` 谁让步），
并裁定 Q3（`ABL_TRAJ_ONLY` 臂定位与 §A1 的更正方式）。**
若能提供《论文20260909改2.docx》，一并补做批注核对。

**未做、需明确授权才能做的事**：
实施 `METHOD_CONTRACT_CANDIDATE.md` §9 的 6 项迁移（本轮执行 0 项）；
修改任何 live 决策实现或冻结离线实现；重跑 Bench2Drive；
开始后续主实验、消融或完整 B2D 统计阶段。

**当前动作**：`STOP_AND_WAIT_FOR_EXPLICIT_USER_AUTHORIZATION`。
不自动迁移 live，不启动主实验，不重跑 Bench2Drive。旧历史指针保留如下。
"""

STATE_PATCH = {
    "experiment_restructure_step1": {
        "round_id": "DRIVECLARIFY_EXPERIMENT_RESTRUCTURE_STEP1_20260912",
        "timestamp": STAMP,
        "status": "STEP1_PARTIAL_REVIEW_REQUIRED",
        "status_meaning": "可独立核查部分已完成；资料缺口（用户指定批注稿不在本机、"
                          "B2D 逐路线记录仅 101/220）与科学定义待决项（Q1/Q2）并存。"
                          "不代表方法有效、实验成功或已授权下一阶段。",
        "report_directory": ROUND_DIR,
        "entry_head": "eaa332b1bb994279b59ea5af786fdb5de96adc1b",
        "exit_head": "eaa332b1bb994279b59ea5af786fdb5de96adc1b",
        "production_code_modified": False,
        "live_decision_implementation_modified": False,
        "live_migration_performed": False,
        "frozen_labels_or_thresholds_modified": False,
        "carla_launches": 0,
        "new_vla_forwards": 0,
        "models_loaded": 0,
        "cuda_contexts_created": 0,
        "commits_made": 0,
        "unit_tests": {"existing": 26, "new": 21, "total_passed": 47,
                       "pytest_requires_env_unset_pythonpath": True,
                       "counted_as_experiment_samples": False,
                       "closed_loop_validation_implied": False},
        "key_findings": {
            "live_full_arm_equals_signature_only_M4": "13/13",
            "live_traj_arm_equals_trajectory_only_M3": "14/14",
            "live_unknown_ever_recorded": 0,
            "offline_fusion_rule_ever_ran_in_closed_loop": False,
            "entity_id_only_divergences": 0,
            "entity_semantics_classification":
                "WORDING_CLARIFICATION_NOT_SCIENTIFIC_DEFINITION_CHANGE",
            "trajectory_only_misjudgements_C_HIST": 35,
            "trajectory_only_misjudgements_C_DEV": 15,
            "revision_pairs": 731,
            "revision_changed_label_defined": 31,
            "revision_wrong_definite_removed": 19,
            "revision_correct_definite_lost": 12,
            "revision_changed_label_undefined_additional": 18,
            "revision_changed_total": 49,
            "revised_M5_vs_M4_disagreements": 0,
            "trajectory_increment_observed": False,
            "bench2drive_complete_pairs_in_repo": 101,
            "bench2drive_claimed": 220,
            "c_test_split_exists": False,
            "unseen_test_set_count": 0,
            "missing_field_priority_current_contract": "MISSING_OUTRANKS_DIVERGENCE",
            "gate_default_exit": "UNKNOWN_FAIL_CLOSED_PRESERVE_EXISTING_AUTHORITY",
        },
        "paper_annotation_gap": {
            "user_designated_file": "《论文20260909改2.docx》",
            "found_on_host": False,
            "candidate_examined": "/tmp/fuse/论文20260909_去附录版_220条结果.docx",
            "candidate_comment_count": 1,
            "method_section_comments_found": False,
            "rq_table_numeric_cells_empty": True,
            "original_docx_modified": False,
        },
        "recommended_next_rule_version": {
            "id": "DRIVECLARIFY_TASK_RELATION_CONTRACT_CANDIDATE_STEP1_20260912",
            "document": f"{ROUND_DIR}/METHOD_CONTRACT_CANDIDATE.md",
            "status": "PROPOSED_FOR_REVIEW / NOT_AUTHORIZED_FOR_LIVE",
            "matches_current_live": False,
            "minimum_migration_items": 6,
            "migration_items_executed": 0,
        },
        "pending_user_decisions": ["Q1", "Q2", "Q3", "Q4", "Q5"],
        "task_relation_definition_frozen": False,
        "next_action": "STOP_AND_WAIT_FOR_EXPLICIT_USER_AUTHORIZATION",
        "next_single_task": "回答 Q1（缺失字段 vs 有效分歧优先级）与 Q2（式 (7) 与 "
                           "methods.py:205-211 谁让步），并裁定 Q3（ABL_TRAJ_ONLY 臂定位）",
        "deliverables": [
            f"{ROUND_DIR}/FINAL_REPORT.md",
            f"{ROUND_DIR}/METHOD_CONTRACT_CANDIDATE.md",
            f"{ROUND_DIR}/EVIDENCE_LEDGER.csv",
            f"{ROUND_DIR}/PAPER_ERRATA.md",
            f"{ROUND_DIR}/MANIFEST.json",
            f"{ROUND_DIR}/COMMAND_LOG.md",
        ],
        "state_backup": BACKUP.name,
    },
}


def append(path: Path, text: str) -> str:
    before = len(path.read_text(encoding="utf-8").splitlines())
    with path.open("a", encoding="utf-8") as handle:
        handle.write(text)
    after = len(path.read_text(encoding="utf-8").splitlines())
    return f"APPENDED_ONLY_{before}_TO_{after}_LINES"


def prepend(path: Path, text: str) -> str:
    original = path.read_text(encoding="utf-8")
    before = len(original.splitlines())
    path.write_text(text + original, encoding="utf-8")
    after = len(path.read_text(encoding="utf-8").splitlines())
    return f"PREPENDED_ONLY_{before}_TO_{after}_LINES"


def main() -> int:
    results = {}
    results["AGENT_WORKLOG.md"] = append(REPO / "AGENT_WORKLOG.md", WORKLOG)
    results["COMMAND_LOG.md"] = append(REPO / "COMMAND_LOG.md", COMMAND_LOG)
    results["CURRENT_HANDOFF.md"] = prepend(REPO / "CURRENT_HANDOFF.md", HANDOFF)
    results["NEXT_AGENT_PROMPT.md"] = prepend(REPO / "NEXT_AGENT_PROMPT.md", PROMPT)

    state_path = REPO / "STATE.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    keys_before = set(state)
    if BACKUP.exists():
        print(f"REFUSING: backup already exists {BACKUP}", file=sys.stderr)
        return 1
    shutil.copy2(state_path, BACKUP)

    state.update(STATE_PATCH)
    state["_prev_step_experiment_restructure_step1"] = "STEP1_PARTIAL_REVIEW_REQUIRED"
    lost = keys_before - set(state)
    if lost:
        print(f"REFUSING: would lose keys {sorted(lost)}", file=sys.stderr)
        return 1
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                          encoding="utf-8")
    results["STATE.json"] = (f"MERGED_INCREMENTALLY_{len(keys_before)}_TO_{len(set(state))}"
                             f"_KEYS_ZERO_EXISTING_KEYS_LOST")
    results["STATE_backup"] = BACKUP.name

    print(json.dumps(results, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
