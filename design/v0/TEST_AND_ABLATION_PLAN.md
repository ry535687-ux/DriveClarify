# DriveClarify v0.1 测试、基线与消融计划

状态：仅定义评测设计；本阶段不启动 CARLA、不运行 GPU 推理、不训练，也不执行 benchmark。

## 1. 评测问题

1. 规则合同能否始终按照硬约束和 deadline 选择 ACT/ASK/WAIT？
2. 澄清能否减少 wrong goal 和不可逆错误，同时不增加硬安全或严重规则失败？
3. ASK 是否只在包含约 1.25 秒 branch 成本的总 wall-clock 时间早于决策边界时才发挥作用？
4. 独立 WAIT 和 safe holding 是否比立即停车或强制 ACT 更能保留选项？
5. 与复用旧计划相比，回答条件重生成是否必要？

任何 route distance 指标都不能单独回答安全问题。

## 2. 实验控制与公平性

所有方法必须共享：

- 冻结的 SimLingo backbone/checkpoint；
- CARLA 版本、场景、路线起点、交通/天气设置和 seeds；
- 初始仿真状态和传感器调度；
- 同一组人工/oracle candidate interpretations；
- oracle intent 和 oracle answer 内容；
- 回答延迟安排，包括无回答和延迟回答；
- query budget；
- baseline 定义中明确要求相同的 controller/holding；
- wall-clock 测量、deadline 和计算核算。

warm-up 和 steady-state 必须分开报告。branch、evaluator、question、response、replan 和 controller 时间都要计入。规则 MVP 不训练，因此不得写“same training budget”。

Oracle intent/answer 必须在不查看 DriveClarify consequence 输出的情况下固定。场景划分和规则阈值必须在 test 前冻结；只能用 development episodes 调整 `TBD` 阈值。

## 3. 结果和报告方式

主要硬结果：

- predicted collision 和 rollout 后真实 collision 分开报告；
- 各类 severe rule conflict；
- wrong goal 和 missed turn/exit；
- 回答前的 option/recoverability loss；
- deadline miss 和 fallback rate。

交互与流程结果：

- ACT/ASK/WAIT 次数和状态转移；
- 问题有效性、防重复、无回答、延迟/无效/错误回答结果；
- holding 可行性、front/rear TTC、路口阻塞和 `O(h)`；
- time-to-decision、各项 clarification latency、实际 slack 和 wall-clock overrun；
- 回答条件 replan 成功率及过期回答拒绝率。

次要结构诊断：route ADE/FDE、最大横向分歧与起点、speed waypoint 差异，以及转换验证后的 stopping-distance difference。这些值永远不能改名为“安全”。所有 consequence 维度分开报告，不生成加权总分。

## 4. 基线方法

| 基线 | 合同 | 可读取信息 | 必需说明 |
|---|---|---|---|
| Never Ask / Always Act（从不问、总是执行） | 立即选择确定性的有效候选，不提问、不主动等待 | 普通状态和候选校验 | 预先固定 tie-break；仍禁止执行不安全候选 |
| Always Ask + same holding（总是问） | 每次 eligible ambiguity 都问，并使用与 DriveClarify 相同 holding selector | interpretations、template、同一 holding | ASK 时序不可行时记录失败，不能额外延长时间 |
| Always Wait（总是等待） | 永不提问，使用同一 holding 到有界 expiry，然后 fallback | 状态和 holding checks | 是独立 WAIT baseline，不是无限不动 |
| Always Stop（总是停车） | 立即尝试受控停车 | ego/map safety checks | 停车位置仍须合法并考虑 rear risk |
| Language-only threshold（仅语言阈值） | 根据指令/歧义表示决定 ASK/ACT，不使用候选后果 | 指令和允许的非分支特征 | v0 无自由文本 parser，只能用 oracle slot ambiguity features |
| Ambiguity-Only / Multi-Interpretation Trigger（仅多解触发） | 若有效解释数大于 1 且预算大于 0，则尝试 ASK；否则 ACT | original instruction、oracle/manual interpretation count、interpretation validity、query budget | 禁止读取 candidate path-speed、trajectory divergence、TTC/risk、rule outcomes、recoverability、TTD 和独立 WAIT；timing/holding 不可行时记录 protocol failure，不能偷用后果模块或增加时间 |
| KnowNo-style calibrated set（KnowNo 风格校准集合） | 构建校准的可能意图集合，非单例时提问 | oracle candidate scores、development calibration | 只是 inspired baseline；不能声称完整复现；校准在 development 上冻结 |
| Ask-to-Act-inspired ACT/ASK | 只有 ACT/ASK 两动作，无独立 WAIT、无反事实后果 | 当前 observation、ego state、original instruction、ambiguity features、Q/A history、query budget | 禁止读取 branch paths、pairwise TTC/rule/recoverability differences 和独立 WAIT |
| Risk-only（仅风险） | 只使用硬安全/risk consequences，忽略 task/topology/irreversibility | safety fields | 检查通用风险是否已经解释全部结果 |
| Trajectory-only（仅轨迹） | 只使用 route/speed 结构分歧，不用语义后果 | 对齐后的 path/speed diagnostics | 禁止把 divergence 称为安全 |
| Oracle intent（真实意图上界） | 直接提供正确 intent，重生成后在硬可行时 ACT | oracle intent | 歧义消除上界，不是部署方法 |
| Oracle ask（是否提问上界） | 由独立 oracle 判断提问是否有价值，保持相同回答和 holding 约束 | 外部场景标签 + 同一 timing/holding | 标签禁止由 DriveClarify consequences 生成 |

Ask-to-Act-inspired 的信息防火墙：

```text
允许：当前 observation、ego state、original instruction、ambiguity features、
      question/answer history、query budget。
禁止：显式 counterfactual path-speed branches、pairwise TTC differences、
      pairwise rule differences、branch recoverability differences、独立 WAIT 动作。
```

Ambiguity-Only 与 Language-Only 必须分开：前者只读“是否存在多个有效解释”，后者可以读取 language confidence、entropy 或 semantic diversity。Ambiguity-Only 若因 ASK timing 或 holding 不可行而失败，输出 baseline protocol failure；不得读取 DriveClarify 的 consequence/TTD 来修复决策，也不得获得独立 WAIT。

## 5. 消融实验

| 消融 | 改动 | 诊断目的 |
|---|---|---|
| no TTD（去掉 TTD） | 删除 deadline feasibility，其余规则不变 | 衡量忽略时序导致的过晚 ASK 或错失 ASK |
| no speed（去掉 speed） | 删除 speed waypoint 诊断/转换 | 测试 route-only 丢失的时序/动力学信息 |
| route-only（仅 route） | 硬 validator 后只用 route 结构差异 | 检查几何量是否能替代结构化后果；预期不能 |
| no irreversibility（去掉不可逆性） | 删除 branch/reachability 字段 | 衡量 missed exit/turn 和 option loss |
| no WAIT（去掉 WAIT） | 语义动作只剩 ACT/ASK；ASK 仍需 holding | 分离独立 WAIT 的价值 |
| ASK = immediate stop（ASK 等于立即停车） | 把 H1–H4 selector 替换成受控停车尝试 | 与错误地把信息动作等同停车比较 |
| WAIT without ASK（只等不问） | 允许 holding，但永不提问 | 分离等待/保留选项与信息增益 |
| no answer-conditioned replanning（无回答条件重规划） | 回答后复用旧候选 | 测试旧计划风险 |
| K=1 | 单一 oracle 解释 | 歧义覆盖下界，无 pairwise 分歧 |
| K=2 | v0 主条件 | 参考条件 |
| K=3 | 三个 oracle 解释，并完整计入顺序前向成本 | 仅做扩展敏感性；不属于核心 v0 |

除非另行批准专门安全分析，否则所有 ablation 都必须保留硬安全和严重规则检查，防止通过执行违规控制获得虚假提升。

## 6. 测试层级

### 6.1 静态合同测试（不使用 CARLA/GPU）

- JSON Schema 可解析且要求全部 consequence 字段。
- Candidate Cache Schema 可解析，字段齐全，且 consequence 通过 `cache_id` 正确关联。
- 七个状态、语义动作和转移全部覆盖。
- `UNKNOWN` 必需安全、规则或 timing 不能产生 ACT、ASK 或 HOLDING，且必须保留具体 reason code。
- `AskTimingStatus` 必须区分 `FEASIBLE/INFEASIBLE_KNOWN/UNKNOWN`，禁止用 boolean false 代替 UNKNOWN。
- 只有 `INFEASIBLE_KNOWN` 可以检查 pre-question WAIT；holding deadline/validity UNKNOWN 必须 fallback。
- ASK 始终返回 question dispatch + holding control。
- WAIT 不发送问题，也不等同 `brake=1`。
- 单问题预算和 question signature 能阻止重复。
- 迟到、过期、错误、无效回答不能绕过 validator。
- 修改指令时旧候选失效并调用重规划接口。
- `Critical` 不把 route L2 当作独立安全 predicate。
- `T_clarify` 包含 branch 和全部必需 latency。
- 严格边界分别覆盖 `T_decision > T_clarify`、`==`、`<`；等号不得 ASK。
- Fast loop 不等待 K forwards；slow loop 只能由合法 trigger 运行。
- cache 在 `now == valid_until` 时已经过期；`EXPIRED/INVALIDATED/UNKNOWN` 均不可执行。
- `CommitReentryCause=NONE` 不创建 episode；NEW_AMBIGUITY 必须有独立 provenance/reason。

### 6.2 离线记录回放（下一阶段，不需要 live CARLA）

使用明确标记为 synthetic 的 schema-complete 记录，不能从当前不完整日志中编造 projection。至少测试：

- route divergence 很大但 consequences 等价；
- path divergence 很小但跨不可逆边界；
- front-safe 但 rear-unsafe 的立即停车；
- 有任务收益但冲突红灯；
- 无 map boundary 但 actor risk 更早；
- 速度接近零且 TTD 未知；
- 一个候选无效、零候选、NaN plan；
- 无回答、过期前后回答、wrong option、changed instruction；
- shared corridor 为空和 safety override；
- branch latency 或 wall-clock overrun；
- Candidate Cache 的 VALID/EXPIRED/INVALIDATED/UNKNOWN、ID mismatch、age 和 refresh reason；
- COMMITTED 的三种合法 re-entry cause 及 `NONE`；
- slow refresh 进行中仍有有效旧控制，以及不存在有效旧控制时的 reason-coded fallback。

### 6.3 第一个 oracle CARLA pilot（未来，需单独授权）

使用主规格中的左转/直行场景。启动前必须具备：精确 route/location 与 decision boundary 标注；两个合法 HLC mapping；同步 actor/map/traffic-rule logger；冻结阈值和 response-latency schedule；包括 rear risk 与 junction blocking 的 holding validator；停止条件与资源安全计划；无训练声明和 SimLingo 前后完整性 hash。

## 7. 场景矩阵

每种 ambiguity family 至少交叉：oracle intent 1/2、回答类型（正确/错误/无效/无回答）、response（立即/在 slack 内/超出 slack/过期后）、dynamic risk（无/front/rear/crossing actor）、rule state、decision distance、shared corridor 是否为空、seed 和 traffic density。

第一轮 pilot 不必覆盖全部组合，但遗漏必须明确。后续 family：当前/下一出口、第一/第二辆白车、障碍物左绕/右绕、两个停车目标。

## 8. 接受与证伪

不使用单一总分。只有满足以下条件才能从 pilot 进入更大规模评测：

- 对相关 baseline 不出现硬安全或严重规则退化；
- 所有 episode 满足状态机不变量和单问题预算；
- 每个 ASK 都有正的实测 slack 和硬可行 holding；
- 过期回答从不执行，回答后计划确实重新生成；
- 缺失数据保持 unknown，不填 0；
- branch 成本和 wall-clock overrun 完整报告。

如果出现主规格中的 pilot falsification 条件，必须阻断或重设计。统计样本量、置信区间和提升阈值在明确 pilot prevalence 和 development/test split 前保持 `TBD`。

## 9. Frozen fixture design table（本阶段只设计，不执行）

以下 expected outputs 在 evaluator 实现前，依据冻结规则人工写入。所有数值只来自 `configs/offline_v0_synthetic.yaml`，必须带四个 synthetic provenance 标签。

固定标签字段：

```text
label_author = DRIVECLARIFY_V0_1_DESIGN_CONTRACT
label_basis = HAND_AUTHORED_FROM_FROZEN_RULE_TABLE
reviewed_before_execution = true
```

### 9.1 严格 timing 边界

`T_clarify = 1.25 + 0.25 + 1.00 + 0.50 + 0.50 = 3.50 s`。

| fixture_id | 输入差异 | expected_decision | expected_reason_code | expected_state | label_author | label_basis | reviewed_before_execution |
|---|---|---|---|---|---|---|---:|
| `TIMING_GT_3_60` | `T_decision=3.60`，critical=true，holding 已知可行，预算=1 | `ASK` | `CRITICAL_ASK_TIMELY` | `QUESTION_SENT`（发送成功后） | `DRIVECLARIFY_V0_1_DESIGN_CONTRACT` | `HAND_AUTHORED_FROM_FROZEN_RULE_TABLE` | true |
| `TIMING_EQ_3_50` | `T_decision=3.50`，holding 已知且可保留选项 | `WAIT` | `ASK_TOO_LATE_HOLD_CAN_PRESERVE` | `HOLDING` | `DRIVECLARIFY_V0_1_DESIGN_CONTRACT` | `HAND_AUTHORED_FROM_FROZEN_RULE_TABLE` | true |
| `TIMING_LT_3_40` | `T_decision=3.40`，holding 已知且可保留选项 | `WAIT` | `ASK_TOO_LATE_HOLD_CAN_PRESERVE` | `HOLDING` | `DRIVECLARIFY_V0_1_DESIGN_CONTRACT` | `HAND_AUTHORED_FROM_FROZEN_RULE_TABLE` | true |
| `TIMING_UNKNOWN` | `T_decision=UNKNOWN`，其他字段即使可行 | `FALLBACK` | `UNKNOWN_DECISION_DEADLINE` | `FALLBACK` | `DRIVECLARIFY_V0_1_DESIGN_CONTRACT` | `HAND_AUTHORED_FROM_FROZEN_RULE_TABLE` | true |

对应 `AskTimingStatus` 必须分别为 `FEASIBLE/INFEASIBLE_KNOWN/INFEASIBLE_KNOWN/UNKNOWN`。如果 equality 或 UNKNOWN 输出 ASK，属于阻断失败。

### 9.2 Candidate Cache 执行门

| fixture_id | 输入差异 | expected_decision | expected_reason_code | expected_state | label_author | label_basis | reviewed_before_execution |
|---|---|---|---|---|---|---|---:|
| `CACHE_VALID_BEFORE_EXPIRY` | `status=VALID` 且 `now < valid_until`，其余 ACT 条件满足 | `ACT` | `ROBUST_OR_EQUIVALENT_ACT` | `COMMITTED` | `DRIVECLARIFY_V0_1_DESIGN_CONTRACT` | `HAND_AUTHORED_FROM_FROZEN_RULE_TABLE` | true |
| `CACHE_EQUAL_EXPIRY` | `status=VALID` 且 `now == valid_until` | `FALLBACK` | `CACHE_EXPIRED` | `FALLBACK` | `DRIVECLARIFY_V0_1_DESIGN_CONTRACT` | `HAND_AUTHORED_FROM_FROZEN_RULE_TABLE` | true |
| `CACHE_INVALIDATED` | `status=INVALIDATED` | `FALLBACK` | `CACHE_INVALIDATED` | `FALLBACK` | `DRIVECLARIFY_V0_1_DESIGN_CONTRACT` | `HAND_AUTHORED_FROM_FROZEN_RULE_TABLE` | true |
| `CACHE_UNKNOWN` | `status=UNKNOWN` | `FALLBACK` | `UNKNOWN_CACHE_VALIDITY` | `FALLBACK` | `DRIVECLARIFY_V0_1_DESIGN_CONTRACT` | `HAND_AUTHORED_FROM_FROZEN_RULE_TABLE` | true |
| `CACHE_LINK_MISMATCH` | consequence 的 cache/episode/observation/candidate ID 任一不一致 | `FALLBACK` | `CACHE_CONSEQUENCE_ID_MISMATCH` | `FALLBACK` | `DRIVECLARIFY_V0_1_DESIGN_CONTRACT` | `HAND_AUTHORED_FROM_FROZEN_RULE_TABLE` | true |

### 9.3 标签生成禁令

- evaluator 不得生成、改写或建议 expected labels；
- 测试失败后不得把实际输出复制为 expected；
- 若冻结规则改变，必须先进行独立设计修订、更新 label basis 的规则版本并审查，然后才能更改 expected；
- fixture 文件实现时必须逐条携带上述三个标签字段，不能只依赖文件级注释。
