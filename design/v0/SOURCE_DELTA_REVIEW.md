# DriveClarify v0 Source Delta Review

审查日期：2026-07-22（Asia/Shanghai）  
审查模式：只读来源差异审查；除本文件外未修改任何项目文件  
结论：**NEEDS_REVISION**

这里的 `NEEDS_REVISION` 表示：最新用户确认和当前交接材料中存在尚未写入 v0 合同的明确约束，不能把现有 `design/v0` 原样当成离线 evaluator 的最终冻结输入。它不否定现有工程 gate，也不要求修改 SimLingo。

## 1. 审查结论先行

| 检查项 | 结论 | 摘要 |
|---|---:|---|
| Research question | 一致 | 最新 RP、规范研究思路、当前交接与 v0 都以“多解释 → 同一 VLA 候选计划 → 多维后果 → ACT/ASK/WAIT”为主线。 |
| Novelty claims | 一致，但有历史冲突 | 当前材料均避免“首次主动澄清”，把潜在增量限定为动态闭环、多解释 path–speed 后果、time-to-decision、非暂停 holding 和回答后重规划。旧筛选报告曾放弃 C7，已被最新 RP 明确取代。 |
| ACT/ASK/WAIT 定义 | 主定义一致 | ACT 执行已校验候选；ASK 是一次问题发送并同时执行 holding；WAIT 不发新问题但执行 holding，绝不等于暂停仿真或默认急停。 |
| Consequence vector | 无 RP 关键变量遗漏 | 当前六维 `task/safety/rule/irreversibility/comfort/timing` 覆盖 RP 的目标、风险、规则/路线、进度、舒适和时间变量；结构轨迹分歧保留为诊断，query budget/cost 属交互协议而非候选安全标量。 |
| Baseline / ablation | 部分不一致 | 当前 v0 缺少规范研究思路明确要求的独立 `ambiguity-only` baseline；完整 RP 的若干全实验 baseline/ablation 也未建立“本阶段/后续阶段”映射。 |
| UNKNOWN 规则 | 存在阻断差异 | schema 保留 UNKNOWN，且多数路径保守处理；但逐 tick 伪代码把“已知不够时间”和“时间 UNKNOWN”都折叠为 `AskFeasible=false`，可能进入 pre-ASK WAIT，没有把 UNKNOWN 必须 reason-coded fallback 的规则写死。 |
| COMMITTED 重新进入 | 状态机一致，伪代码不完整 | 状态机写明三种合法原因；伪代码的 COMMITTED 快速返回没有显式检查 `newly_detected_ambiguity`，reducer 合同仍需收紧。 |
| 双速率结构 | 明确冲突 | 当前 Algorithm 1 在每个 `DRIVECLARIFY_TICK` 中顺序运行 K 次 VLA forward；最新确认要求 fast control loop 不等待 K 次 forward，分支只能在 slow/event-driven loop 运行。 |
| CPU-only offline evaluator | **有条件允许** | 完成第 7 节的离线阻断项后可以继续；当前不得直接按未修订合同编码。live CARLA 继续禁止。 |

## 2. 来源优先级与审查口径

本次采用以下优先级：

1. 本轮用户明确确认的协议和范围；
2. 已验证工程报告；
3. 当前交接 `CURRENT_HANDOFF.md`、`STATE.json`；
4. 当前 `design/v0` 算法合同；
5. 最新 RP、`RESEARCH_IDEA_CANONICAL.md`；
6. Algorithm Reading Guide；
7. 历史研究筛选、候选方向和早期原始思路。

低优先级材料中的候选公式、旧方向选择或完整实验愿望不能覆盖当前已批准的 v0 规则。尤其：

- RP/原始思路中的加权关键性分数或 VoC 只能视为待检验 proposal，不得覆盖 hard constraints + lexicographic cascade；
- Ask-to-Act-inspired 的 `same training budget` 不适用于不训练的规则 MVP；当前应比较相同 backbone、场景、seed、候选解释、回答协议、query budget 和 wall-clock accounting；
- Reading Guide 的“可允许一次 follow-up”是候选设计，已被“每 episode 最多成功发送一个语义问题”覆盖。

## 3. 新读取文件

本次完整清点并读取上传包中的 21 个文件；PDF/DOCX 通过只读文本抽取检查，Markdown/JSON 直接读取。

### 3.1 当前状态与工作流

1. `DriveClarify_host_upload_pack/CURRENT_HANDOFF.md`
2. `DriveClarify_host_upload_pack/NEXT_CHAT_PROMPT.md`
3. `DriveClarify_host_upload_pack/PACKAGE_MANIFEST.json`
4. `DriveClarify_host_upload_pack/README_FIRST.md`
5. `DriveClarify_host_upload_pack/RESEARCH_EVIDENCE_INDEX.md`
6. `DriveClarify_host_upload_pack/SOURCE_DELTA_REVIEW_PROMPT.md`
7. `DriveClarify_host_upload_pack/STATE.json`
8. `DriveClarify_host_upload_pack/UPLOAD_INSTRUCTIONS.md`

### 3.2 RP、研究思路与历史研究材料

9. `DriveClarify_host_upload_pack/docs/research_evidence/DriveClarify_RP_revised.pdf`
10. `DriveClarify_host_upload_pack/docs/research_evidence/DriveClarify_research_idea.docx`
11. `DriveClarify_host_upload_pack/docs/research_evidence/RESEARCH_IDEA_CANONICAL.md`
12. `DriveClarify_host_upload_pack/docs/research_evidence/VLA_research_direction_review.pdf`
13. `DriveClarify_host_upload_pack/docs/research_evidence/combined_research_report.md`
14. `DriveClarify_host_upload_pack/docs/research_evidence/final_recommendation.md`
15. `DriveClarify_host_upload_pack/docs/research_evidence/gap_matrix.md`
16. `DriveClarify_host_upload_pack/docs/research_evidence/innovation_candidates.md`
17. `DriveClarify_host_upload_pack/docs/research_evidence/literature_map.md`
18. `DriveClarify_host_upload_pack/docs/research_evidence/reviewer_attack.md`

### 3.3 教材与综述原文

19. `DriveClarify_host_upload_pack/reading_algorithm_design/DriveClarify_Algorithm_Reading_Guide.pdf`
20. `DriveClarify_host_upload_pack/references/surveys/Hu_VLA4AD_Survey_2026.pdf`
21. `DriveClarify_host_upload_pack/references/surveys/Jiang_VLA4AD_Survey_ICCVW_2025.pdf`

同时重新读取了 `design/v0` 的 11 个既有文件，并对既有 runtime gate 报告、JSON/JSONL 进行了只读存在性和关键结论复核。未启动任何新运行。

## 4. 一致内容

### 4.1 Research question 与三个递进问题

最新来源和当前 v0 一致地回答三个递进问题：

1. 多个语言解释是否会传播成不同的任务、安全、规则或不可逆后果；
2. 后果发生分歧时应 ACT、ASK 还是 WAIT；
3. ASK/WAIT 期间如何在世界持续演化时安全控制并保留选择。

RP 的 RQ1–RQ3 进一步要求最终闭环实验检验安全收益、language-only 对照和 WAIT 的独立价值。v0 是规则合同与 MVP 上界，不包含跨 checkpoint/config 的实证结论；这是阶段范围差异，不是科学问题冲突。

### 4.2 Novelty boundary

当前 v0 与最新 RP/Reading Guide 一致承认：

- 主动澄清、ambiguity detection、问题生成、Ask-or-Act、conformal abstention、VoI 和 query burden 均有前作；
- URS/Talk2Car 类工作已覆盖驾驶语境的静态 grounding/澄清，不能声称“首次让自动驾驶系统提问”；
- SimLingo/LMDrive 已把语言条件接入驾驶计划或闭环控制，不能把“语言影响动作”本身作为新颖性；
- 可检验的窄增量是同一动态观测下的解释 → path–speed candidates → 交通后果 → time-aware ACT/ASK/WAIT，以及 ASK/WAIT 期间不暂停控制和回答后基于当前观测重规划。

当前规格没有把外接 LLM、oracle answer、route L2 或单一风险代理包装成创新，符合 RP 的限制性表述。

### 4.3 ACT / ASK / WAIT 与 episode 协议

以下合同已一致：

- `ACT(z_k)` 只能执行当前重新校验且硬可行的候选；
- `ASK(q)` 是信息动作，必须同时给出独立的 holding control；
- `WAIT` 不发送新问题，但必须执行经验证的 holding；
- 每个 clarification episode 最多成功发送一次语义问题；
- 无效回答不恢复预算；迟到/旧 episode 回答不能改变 committed state；
- 回答不能给旧计划换标签，必须从当前观测重新生成计划；
- `COMMITTED` 只有新指令、新检测歧义或计划/解释实质失效才能开启新 episode。

### 4.4 Consequence vector

RP 的早期四组记号 `g/r/v/q` 是高层 proposal；Reading Guide 和规范研究思路把它细化为六个独立维度。当前 schema 已覆盖：

| RP/教材变量 | 当前 v0 对应位置 | 结论 |
|---|---|---|
| 目标、wrong goal、route progress、missed turn/exit | `task` | 已覆盖 |
| collision、front/rear TTC、clearance、required deceleration、unsafe speed | `safety` | 已覆盖 |
| red light、stop sign、lane/solid line、off-road、wrong-way、speed limit | `rule` | 已覆盖 |
| branch boundary、exit gore、remaining reachability、recoverability | `irreversibility` | 已覆盖 |
| acceleration、jerk、hard brake、steering rate、control switching | `comfort` | 已覆盖 |
| branch/question/response/replan/margin、TTD、slack | `timing` | 已覆盖 |
| path/speed divergence | 独立结构诊断 | 正确地未冒充 safety |
| query count/burden | episode budget、dialogue log 和评测指标 | 合理地未合并进候选安全分数 |

因此，没有发现 RP 中影响当前规则 MVP 的关键 consequence 变量被遗漏。尚未可计算的字段仍必须保持 UNKNOWN/TBD，不能由 synthetic fixture 冒充真实 CARLA 测量。

### 4.5 Oracle 独立性与时间边界

- RP、当前规格和离线任务书均要求 oracle interpretation、answer、expected decision 在查看 evaluator 输出前独立冻结；禁止先运行 evaluator 再复制输出为 expected label。
- `T_clarify` 均包含 branch、question、response、replan 和 margin。
- 当前保留的约 `1.25 s` 只支持受测双顺序前向的工程默认值，不支持真实人类响应或完整实时阈值。
- ASK 必须使用严格不等式 `T_decision > T_clarify`；相等不能算可行。

## 5. 明确冲突与未并入的最新约束

### C1：逐 tick K 次前向与双速率实时结构冲突

当前 `DriveClarify_v0_Algorithm_Specification.md` 第 5 节写“每 tick”共享预处理并顺序生成两个候选；`ALGORITHM_1_PSEUDOCODE.md` 又在单个 `DRIVECLARIFY_TICK` 的 F 段执行 K 次 forward。最新交接和用户确认要求：

- fast control loop 每个 CARLA tick 立即执行 committed plan 或 holding，并更新风险、回答、超时和有效性；
- slow/event-driven clarification loop 只在 episode open、candidate expiry、material scene change、timely answer 或 candidate invalidation 时运行共享预处理和 K 次顺序 forward；
- fast loop 不得等待 K 次 VLA forward。

这是明确结构冲突。它直接阻塞 live CARLA；其中候选缓存/有效性记录的合同也阻塞离线 reducer 定型。

### C2：候选 freshness/refresh 字段缺失

现有状态包含 `observation_id` 和候选 ID，但没有最新确认要求的：

```text
generated_at
valid_until
plan_age_s
refresh_reason
```

当前 `policy_state`、候选记录和离线 evaluator 任务书都未完整定义这些字段及其过期/刷新转移。这不是 consequence 六维遗漏，而是双速率状态合同遗漏。

### C3：UNKNOWN 对 HOLDING 的禁用尚未写成不可绕过规则

一致部分：当前 schema 保留 `UNKNOWN`，并未把它重写为 `UNSAFE`；硬安全/规则 UNKNOWN 通常按 non-feasible 处理；ASK timing 要求输入已知。

冲突部分：Algorithm 1 先令 `AskFeasible=false`，再以“`AskFeasible=false` 且存在 h_star”作为 pre-ASK WAIT 的入口。这里没有区分：

- 已知 `T_decision <= T_clarify`；
- 必需 timing 为 `UNKNOWN`。

最新确认明确规定 required safety/rule/timing UNKNOWN 不得授权 ACT、ASK 或 HOLDING，必须保持 UNKNOWN 并输出确定性 reason-coded fallback。当前静态测试条目也只明确写了“不产生 ACT 或 ASK”，没有把 HOLDING 一并列出。因此当前文本不能证明 UNKNOWN timing 不会被折叠为普通“太晚”并进入 WAIT。

### C4：COMMITTED 伪代码未显式检查 newly detected ambiguity

状态机和主规格写对了三种 re-entry 原因，但 Algorithm 1 的 COMMITTED 快速路径只显式检查“计划未实质失效且没有新指令”后就直接跟踪已提交计划；它没有显式布尔量/事件检查 `newly_detected_ambiguity`。离线 reducer 若机械照抄伪代码，可能漏掉合法新 episode，或让模糊的“根据当前原因开启”接收未授权原因。

### C5：核心 baseline 清单缺少独立 ambiguity-only

`RESEARCH_IDEA_CANONICAL.md` 把 `language-only` 与 `ambiguity-only` 分列为必须击败或解释的基线。当前 `TEST_AND_ABLATION_PLAN.md` 只有 `Language-only threshold`，未定义只判断“是否多解”、不读取轨迹/风险的独立 `Ambiguity Detection Only`。这是明确的核心 baseline 缺项。

完整 RP 的 14 项 baseline 还包括 Original Driving VLA、Random Ask、Fixed Confidence Threshold、CLAM-style、Oracle Consequence Upper Bound；当前 v0 没有逐项对应。部分项目因 v0 不支持任意模糊文本直送 SimLingo、不训练、只做 synthetic rule evaluator 而合理延期，但现有文档没有明确的阶段映射。该差异不阻塞离线规则 evaluator，但在未来完整实验前必须关闭。

当前 v0 ablation 与规范研究思路的核心项基本一致；完整 RP 额外列出的 no-risk、no-query-cost、生成解释/问题、视觉时序、回答噪声、checkpoint 和缓存/并行推理等属于后续闭环/完整实验范围。它们不应在本阶段实现，但应在未来实验计划中标注“deferred”，避免声称与完整 RP 已完全对齐。

## 6. 已解决的历史冲突（不应覆盖当前规格）

| 历史来源 | 历史内容 | 当前处理 |
|---|---|---|
| `final_recommendation.md`、`combined_research_report.md`、`VLA_research_direction_review.pdf` | 当时选择 C1 条件化服从，并明确放弃 C7 主动澄清 | 这是前期筛选结论；最新 RP、规范研究思路和当前交接已明确选择 DriveClarify，故标记为 superseded，不改回 C1。 |
| 原始 `DriveClarify_research_idea.docx` | 用加权 `K_t` 和加权 `J_wait` 决策 | 当前 v0 的 hard feasibility + lexicographic cascade 优先；旧公式只可作为敏感性候选，不得进入冻结规则标签。 |
| 最新 RP 的候选 VoC 公式 | 以 expected loss、delay、query cost 描述 Ask 倾向 | RP 自身称其为待检验设计假设；不能覆盖当前非加权规则 cascade。 |
| RP 的 Ask-to-Act “same training budget” | 面向未来学习式比较 | 规则 MVP 不训练，改用相同输入、场景、回答、query 和 wall-clock accounting。 |
| Reading Guide fallback 候选 | still-ambiguous 时允许一次 follow-up | 当前已批准预算为每 episode 一次；仍歧义时不得再问，只能有界 holding、robust fallback 或 safety override。 |

这些冲突已由来源优先级和用户确认解决，不构成修改当前研究问题或恢复旧公式的理由。

## 7. 建议修改项与阻断范围

本轮不执行以下修改；它们是下一阶段编码前的必要动作。

### 7.1 阻塞 CPU-only offline evaluator 的修改

1. 按用户要求，在确认项目仍非 Git 仓库后，只在 `/home/buaa/wrh/DriveClarify` 初始化 Git，添加适当 `.gitignore`，并把当前 `design/v0` 提交为冻结设计基线；不得嵌套或改动 `/home/buaa/wrh/simlingo`。
2. 创建 `design/v0/DESIGN_DELTA_DUAL_RATE.md`，把 C1/C2 的双速率结构、触发事件、缓存字段和 reducer 转移写成对现有 v0 的显式增量；本阶段不实现实时循环。
3. 在 offline state/candidate records 与 reducer 合同中预留 `observation_id/generated_at/valid_until/plan_age_s/refresh_reason`，并定义 expired/invalidated/material-change 的 reason code。
4. 收紧三值逻辑：required safety/rule/timing 为 UNKNOWN 时保持 UNKNOWN，直接产生相应 reason-coded fallback；不得输出 ACT、ASK 或 HOLDING；严格区分 `TIMING_UNKNOWN` 与已知的 `ASK_TOO_LATE`。
5. 给 COMMITTED reducer 增加枚举式 re-entry cause，只允许 `NEW_INSTRUCTION`、`NEW_AMBIGUITY`、`MATERIAL_PLAN_OR_INTERPRETATION_INVALIDATION`；其他事件和旧回答不得开新 episode。
6. 创建只用于 synthetic fixture 的配置，明确标记 `SYNTHETIC_TEST_ONLY`：

   ```yaml
   protocol:
     query_budget_per_episode: 1
     unknown_hard_field_policy: non_feasible
     t_wait_max_s: 3.0

   timing_fixture_default:
     branch_latency_s: 1.25
     question_latency_s: 0.25
     response_latency_s: 1.0
     replanning_latency_s: 0.50
     safety_margin_s: 0.50
   ```

   这些数值不得称为 CARLA 实测、人类响应或真实驾驶阈值。
7. 人工写入并独立审查 expected decisions；至少显式覆盖 `T_decision > T_clarify`、`==`、`<` 三个边界，其中只有严格大于可使 ASK timing feasible。

### 7.2 不阻塞 offline evaluator、但阻塞后续实验或 live CARLA 的修改

1. 在未来修订 `TEST_AND_ABLATION_PLAN.md` 时新增独立 `Ambiguity Detection Only` baseline。
2. 增加 RP 14 baselines / full ablations 到 v0、offline、pilot、full experiment 的阶段映射；本阶段不实现 Random Ask、CLAM、学习式 Ask-to-Act 或模型/问题生成消融。
3. 在历史研究材料入口处记录“由最新 DriveClarify RP supersede”，防止旧 C1 决策覆盖当前项目；无需改写历史文件正文。
4. live CARLA 只有在双速率实现以及坐标系/horizon、actor prediction、TTC、地图/规则边界、holding 风险和真实 timing 阈值全部验证后，才可另行申请授权。

## 8. 证据类型分离

| 类型 | 本次可用结论 | 不允许外推 |
|---|---|---|
| 已验证工程事实 | 原 SimLingo 单路线 smoke 完成；同一观测/模型实例可顺序生成两组 `[1,20,2]` route 与 `[1,10,2]` speed waypoints；single/branch baseline exact equal；10/10 probes 有非零条件效应；两次 forward 约 `0.624+0.625 s`；alternative 未进入 PID且未修改 SimLingo | 不证明自由文本、解释正确、安全收益、实时性、ACT/ASK/WAIT 或泛化 |
| RP proposal | 多解释 consequence-aware ACT/ASK/WAIT、A/B/C/D 场景、14 baselines、候选 VoC 与完整统计计划 | 不是实现事实；候选加权公式不是冻结规则 |
| 文献事实 | 前作已覆盖驾驶语言条件、静态驾驶澄清、Ask-or-Act/求助、不确定性校准、闭环评测或规则约束的不同子问题 | 不能据此声称某模型有缺陷，也不能声称 DriveClarify 是首次主动澄清 |
| 当前算法设计 | K=2 oracle 候选、六维 consequence、硬约束、词典序 cascade、一次提问、ASK/WAIT+holding、严格 timing、回答后重规划 | 规范要求不等于算法已实现或已验证安全 |
| 仍未验证假设 | consequence 信息优于 language-only；WAIT 有独立价值；holding 可安全保留选项；真实 TTD/TTC/规则/舒适阈值；跨场景或跨模型收益 | 不得写成实验结果、安全保证或真实驾驶阈值 |

## 9. 是否允许继续 CPU-only offline evaluator

**结论：有条件允许（ALLOW AFTER REVISION）。**

必须先完成第 7.1 节的冻结基线、双速率设计增量、候选 freshness 字段、UNKNOWN reason-coded fallback、COMMITTED reducer、synthetic-only 配置和三类严格时间边界 fixture。完成后可以继续 CPU-only offline evaluator。

即使上述修改完成，仍然不授权 live SimLingo integration、CARLA、GPU、训练、自由文本 parser 或对 `/home/buaa/wrh/simlingo` 的任何修改。
