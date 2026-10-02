# DriveClarify v0.1 Design Repair Gate 报告

审查日期：2026-07-22（Asia/Shanghai）

最终状态：**PASS_READY_FOR_OFFLINE_IMPLEMENTATION**

本结论只表示 v0.1 设计合同已经关闭 Source Delta Review 指出的五项阻断冲突，并具备进入 CPU-only offline evaluator 实现阶段的文档前提。它不表示算法已实现、实时性已验证、安全阈值已确定或 live CARLA 已获授权。

## 1. 技术摘要

修订后的合同把原先单个逐 tick 大循环拆成 fast control 与 slow/event-driven clarification 两套调度：fast loop 每 tick 只执行已经验证且仍有效的 committed/holding control，并输出一个且仅一个控制；slow loop 只在六类合法事件上冻结观测、顺序运行 K 个同实例分支并更新 Candidate Cache。

同时，ASK timing 从 boolean 改为 `FEASIBLE/INFEASIBLE_KNOWN/UNKNOWN` 三值状态。UNKNOWN 保持原值，不改写成 false 或 UNSAFE，不得授权 ACT、ASK 或 HOLDING，并输出具体 reason-coded fallback。COMMITTED 重入也改为四值枚举，只有三种实质原因可以创建新 episode。

## 2. 修改和创建范围

### 2.1 修改文件

1. `design/v0/DriveClarify_v0_Algorithm_Specification.md`
2. `design/v0/ALGORITHM_1_PSEUDOCODE.md`
3. `design/v0/DRIVECLARIFY_STATE_MACHINE.md`
4. `design/v0/NOTATION_AND_DATA_MAPPING.md`
5. `design/v0/CONSEQUENCE_SCHEMA.json`
6. `design/v0/TEST_AND_ABLATION_PLAN.md`
7. `design/v0/NEXT_STAGE_OFFLINE_EVALUATOR_PROMPT.md`

### 2.2 创建文件

1. `design/v0/DESIGN_DELTA_DUAL_RATE.md`
2. `design/v0/CANDIDATE_CACHE_SCHEMA.json`
3. `design/v0/DESIGN_REPAIR_REPORT.md`
4. `configs/offline_v0_synthetic.yaml`

### 2.3 Git 边界文件

- `.gitignore` 和 `GIT_TRACKING_MANIFEST.md` 已在修订前基线中建立；
- 修订前基线提交：`ecf7a20 design: freeze pre-delta DriveClarify v0 contract`；
- `runtime/`、`workspace/`、上传研究包、checkpoint、视频和旧 OpenArm 打包路径均不在跟踪范围；
- `/home/buaa/wrh/simlingo` 是外部同级仓库，不在本 Git 仓库中。

## 3. 冲突修复结果

| 冲突 | 修复结果 | 当前合同证据 |
|---|---|---|
| 每 tick 可能等待 K 次 forward | 已拆成 fast/slow 双速率；fast loop 不运行或等待 K forwards | `DESIGN_DELTA_DUAL_RATE.md`；Algorithm 1 第 2–3 节 |
| 缺少 candidate lifecycle | 创建独立 Candidate Cache Schema，并用 `cache_id` 关联 consequence | `CANDIDATE_CACHE_SCHEMA.json`；Consequence Schema 顶层字段 |
| UNKNOWN timing 可能压成 false 后进入 WAIT | 定义三值 `AskTimingStatus`；UNKNOWN 直接具体 reason fallback | 主规格第 8、10 节；Algorithm 1 第 4 节 |
| COMMITTED 未显式检查 NEW_AMBIGUITY | 定义 `CommitReentryCause` 四值枚举及纯 guard | 状态机第 7 节；Algorithm 1 第 5 节 |
| 缺少 Ambiguity-Only baseline | 新增独立 baseline 及信息防火墙 | `TEST_AND_ABLATION_PLAN.md` 第 4 节 |
| 旧 C1/加权分数/二次提问可能回流 | 保留 Source Delta 的 superseded 判定；新合同继续禁止标量加权和第二个语义问题 | 主规格、状态机、Algorithm 1 不变量 |

## 4. 修订后 16 项审查门

| # | 审查项 | 结果 | 验证说明 |
|---:|---|---:|---|
| 1 | fast loop 不再等待 K forwards | PASS | `FAST_CONTROL_TICK` 只消费结果/控制；slow 未完成时继续有效旧控制或 fallback |
| 2 | slow loop 是 event-driven | PASS | 只接受六种明确 trigger；非法或旧 episode trigger 被拒绝 |
| 3 | Candidate Cache 生命周期字段齐全 | PASS | 11 个指定字段全部为 schema required；另有 `schema_version` |
| 4 | expired/invalid cache 不可执行 | PASS | 执行门为 `now < valid_until` 且 `status=VALID`；等号过期；UNKNOWN 也不可执行 |
| 5 | AskTimingStatus 保持三值 | PASS | schema、notation、主规格、伪代码均使用三值枚举，禁止布尔压缩 |
| 6 | UNKNOWN timing 不进入 ASK 或 WAIT | PASS | 实施了更强合同：UNKNOWN required safety/rule/timing 不得 ACT、ASK 或 HOLDING |
| 7 | equality case 不允许 ASK | PASS | `T_decision == T_clarify` 明确为 `INFEASIBLE_KNOWN` |
| 8 | COMMITTED 三种合法重入原因明确 | PASS | 只有 `NEW_INSTRUCTION/NEW_AMBIGUITY/MATERIAL_INVALIDATION` 可重入 |
| 9 | old/late answer 不触发重入 | PASS | late/old answer、cosmetic paraphrase、ordinary update 均归 `NONE` |
| 10 | Ambiguity-Only baseline 独立存在 | PASS | 与 Language-Only 分开，禁止读取 path/risk/rule/TTD/WAIT |
| 11 | 一次提问限制保持 | PASS | 每 episode budget=1；仍歧义不得第二次语义提问 |
| 12 | 未恢复 scalar weighted score | PASS | consequence 继续分项保存；cascade 继续 hard constraints + lexicographic |
| 13 | 未恢复二次语义追问 | PASS | 唯一问题和 transmission retry 边界保持不变 |
| 14 | synthetic config 标签完整 | PASS | 四个 provenance 标签齐全；固定 `T_clarify=3.50 s` 经求和复核 |
| 15 | 下一阶段 prompt 已同步 | PASS | 加入 cache validator、三值 timing、reentry guard、手写标签和禁实现实时循环 |
| 16 | 未实现代码、未启动 CARLA/GPU、未修改 SimLingo | PASS | 本阶段仅 Markdown/JSON/YAML/Git 元数据；未创建 evaluator package 或 tests 代码 |

## 5. Fixture 合同已经在执行前冻结

`TEST_AND_ABLATION_PLAN.md` 第 9 节已经人工写入 timing 和 cache expected outputs。每条 expected output 均具有：

```text
expected_decision
expected_reason_code
expected_state
label_author
label_basis = HAND_AUTHORED_FROM_FROZEN_RULE_TABLE
reviewed_before_execution = true
```

四个 timing case 为：

| `T_decision` | `T_clarify` | Expected timing status | Expected semantic result |
|---:|---:|---|---|
| 3.60 s | 3.50 s | `FEASIBLE` | 其他必要条件满足时 `ASK` |
| 3.50 s | 3.50 s | `INFEASIBLE_KNOWN` | 已知 holding 可保留选项时 `WAIT`，不得 ASK |
| 3.40 s | 3.50 s | `INFEASIBLE_KNOWN` | 已知 holding 可保留选项时 `WAIT`，不得 ASK |
| UNKNOWN | 3.50 s | `UNKNOWN` | `UNKNOWN_DECISION_DEADLINE` fallback |

本阶段没有 evaluator，因此不存在运行实际输出后反填 expected label 的路径。

## 6. 静态验证

已执行且通过：

```text
jq empty design/v0/CONSEQUENCE_SCHEMA.json
jq empty design/v0/CANDIDATE_CACHE_SCHEMA.json
Python PyYAML 只读解析 configs/offline_v0_synthetic.yaml
求和核对 1.25 + 0.25 + 1.00 + 0.50 + 0.50 = 3.50
git diff --check
关键枚举、字段、reason code、baseline 和 synthetic 标签的跨文件检索
```

验证结果：两个 JSON 文件语法有效；YAML 可解析；query budget 为 1；`T_clarify` 为 3.50 s；补丁无空白错误；全部指定字段与枚举存在。

这些是设计文件静态验证，不是 evaluator 单元测试，也不是 CARLA/GPU 实验。

## 7. 可视化合同

现有 SVG 没有在本阶段修改。`DESIGN_DELTA_DUAL_RATE.md` 第 8 节已明确：旧图不能再被解释成“每 tick 完成 K 次 forward”。未来 live 图必须显示 fast loop、event-driven slow loop、Candidate Cache 边界、有效旧控制/fallback、UNKNOWN timing 禁止流向 ASK/WAIT，以及 `CommitReentryCause=NONE` 不流向新 episode。

因此，当前文字合同已经修复，现有 SVG 仍只适合表示高层模块/七状态，不是双速率实现证据。

## 8. 仍未解决的真实参数

以下参数继续为 `TBD_REQUIRED`，只阻塞 live integration 或真实实验，不阻塞使用显著标记 synthetic records 的 CPU-only evaluator：

1. 真实 `T_WAIT_MAX`、Candidate Cache `valid_until`、holding validity 和 answer expiry；
2. 真实 question、response、replanning latency 与 safety margin；
3. fast/slow wall-clock budgets、调度/并发/原子交换实现；
4. route/speed waypoint 坐标系、单位和 horizon；
5. actor prediction、front/rear TTC、clearance 和 required deceleration；
6. severe-rule taxonomy、地图 decision boundaries 和 comfort thresholds；
7. material scene change / new ambiguity detector 的实时 provenance 和阈值；
8. live safety fallback controller、shared corridor 与 junction blocking 定义。

`configs/offline_v0_synthetic.yaml` 中的所有数字只能用于 synthetic fixtures，不得替代上述真实参数。

## 9. 准入决定

**允许进入 CPU-only offline evaluator 实现阶段。**

下一阶段只能按 `NEXT_STAGE_OFFLINE_EVALUATOR_PROMPT.md` 实现纯数据 validator、三值逻辑、cache lifecycle、cascade、reducer 和手写 fixtures。继续禁止 live SimLingo integration、CARLA、GPU、训练、实时双循环实现和修改 SimLingo。
