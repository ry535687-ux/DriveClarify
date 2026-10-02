# 下一阶段任务书——DriveClarify v0.1 CPU-only 离线评估器

继续 DriveClarify 项目，身份是算法合同实现者和证据审计员。

## 1. 唯一目标

只实现并测试一个 **离线、仅 CPU 的 evaluator**，用于验证已经批准的 DriveClarify v0.1 consequence/cache schema、三值 timing、ACT/ASK/WAIT cascade 和 reducer guard。禁止连接 live SimLingo 或 CARLA；禁止实现 fast/slow 实时循环、自由文本 parser、模型分支、车辆 controller、乘客界面、训练或完整系统。

项目根目录：`/home/buaa/wrh/DriveClarify`  
冻结设计目录：`/home/buaa/wrh/DriveClarify/design/v0`

## 2. 开始前必须读取

依次完整读取：

1. `SOURCE_AUDIT.md`
2. `DriveClarify_v0_Algorithm_Specification.md`
3. `NOTATION_AND_DATA_MAPPING.md`
4. `DRIVECLARIFY_STATE_MACHINE.md`
5. `DESIGN_DELTA_DUAL_RATE.md`
6. `CANDIDATE_CACHE_SCHEMA.json`
7. `CONSEQUENCE_SCHEMA.json`
8. `ALGORITHM_1_PSEUDOCODE.md`
9. `TEST_AND_ABLATION_PLAN.md`
10. `DESIGN_REPAIR_REPORT.md`
11. `../../configs/offline_v0_synthetic.yaml`

runtime branch/smoke 证据只能只读。`/home/buaa/wrh/simlingo` 和 `/home/buaa/CARLA_0.9.15` 严格只读且不在实现范围内。

## 3. 开发前置确认

已批准的小型、受版本控制配置为 `configs/offline_v0_synthetic.yaml`。编码前必须验证它明确包含：

- 每个 episode 一个语义问题；
- UNKNOWN 保持 UNKNOWN、按 non-feasible 处理，并禁止 ACT/ASK/HOLDING；
- 离线 fixtures 使用的 `T_WAIT_MAX`、timing 和 cache expiry；
- 状态机重新进入规则。

Fixture 中的阈值必须同时标记 `SYNTHETIC_TEST_ONLY/NOT_CARLA_MEASURED/NOT_HUMAN_RESPONSE_DATA/NOT_A_SAFETY_THRESHOLD`，禁止声称是 CARLA 实测值、人类响应数据或安全阈值。

## 4. 允许实现的内容

在新的 DriveClarify-only 路径（例如 `driveclarify_offline/`）创建小型 package，并在 `tests/offline_v0/` 创建测试。仅实现：

- candidate consequence records 的 JSON Schema 校验；
- Candidate Cache records 的 JSON Schema 校验、ID 关联、age、strict expiry 和 validity 状态；
- 显式三值布尔逻辑；
- consequence equivalence 和 `D_G/D_S/D_R/D_I` 比较；
- 从输入记录计算 `T_clarify` 和三值 `AskTimingStatus`，严格区分 `FEASIBLE/INFEASIBLE_KNOWN/UNKNOWN`；
- 基于 oracle slots 的模板问题相关性、防重复和可回答检查；
- 纯词典序 cascade，输出带 reason code 的 semantic decision；
- 七状态的纯状态转移 reducer、合法 slow trigger 和 `CommitReentryCause` guard；
- answer episode/question/expiry 校验；
- 确定性日志和 decision trace。

禁止实现 model inference、CARLA geometry、TTC prediction、controller/PID、H1–H4 dynamics、sensor ingestion、线程/队列或实时 fast/slow loops。它们由 schema-complete 测试记录显式提供，且必须包含 provenance/status。

## 5. 必需 fixtures

所有 fixture 必须明确标记为 synthetic，并覆盖：

1. 零个有效候选；
2. 一个安全合法候选；
3. route divergence 很大但 consequences 等价；
4. divergence 很小但不可逆边界不同；
5. 已知 unsafe 和 severe-rule 候选；
6. UNKNOWN 硬安全/规则/timing 字段，分别验证不能 ACT、ASK 或 HOLDING；
7. ASK 可行、安全 holding、预算充足；
8. ASK 太晚，但 WAIT 可以延长窗口；
9. 已有问题且无回答 → WAIT；
10. 无安全 holding → robust fallback/safety override；
11. 无回答、延迟、无效、错误、仍歧义和 changed-instruction answer；
12. commit 后 stale answer；
13. wall-clock overrun，以及 branch 默认/在线 latency 来源；
14. question repeat prevention；
15. K=1、K=2、K=3 敏感性，并标明 K=3 超出核心 v0。
16. Candidate Cache 的 VALID/EXPIRED/INVALIDATED/UNKNOWN、`now == valid_until`、ID mismatch、plan age 和 refresh reason；
17. `T_decision=3.60/3.50/3.40/UNKNOWN` 四个预注册 timing cases；
18. `CommitReentryCause` 的三个合法原因与 `NONE`，包括 late answer、cosmetic paraphrase 和 ordinary update；
19. slow refresh 期间存在有效旧 control 与不存在有效旧 control 两种记录。

禁止通过运行 evaluator 生成 synthetic oracle ACT/ASK/WAIT 标签。每条 expected output 必须在执行前人工编写并携带：

```text
expected_decision
expected_reason_code
expected_state
label_author
label_basis = HAND_AUTHORED_FROM_FROZEN_RULE_TABLE
reviewed_before_execution = true
```

## 6. 接受门槛

- 所有 schema 和 unit tests 在 CPU-only 条件下通过。
- 缺失或无效必需字段必须显式失败。
- UNKNOWN 必需安全、规则、timing、holding deadline/validity 或 cache validity 必须保持 UNKNOWN，不得产生 ACT、ASK 或 HOLDING。
- `AskTimingStatus` 不得降级为 boolean；只有 `INFEASIBLE_KNOWN` 可检查 pre-question WAIT。
- `T_decision == T_clarify` 必须是 `INFEASIBLE_KNOWN`，不能 ASK。
- 过期、失效、UNKNOWN 或 ID 不一致的 Candidate Cache 不可执行。
- 不存在标量加权总分。
- ASK 必须要求 holding summary；WAIT 独立且不发送问题。
- route L2/ADE/FDE 不能直接改变 safety class。
- timing 必须计入 `T_branch` 和全部 clarification terms，并测试严格不等式。
- answer 不能绕过 revalidation；stale answer 不能改变 committed state。
- 只有 `NEW_INSTRUCTION/NEW_AMBIGUITY/MATERIAL_INVALIDATION` 可重开 episode；`NONE` 不清 cache、不恢复预算。
- evaluator 不实现实时双循环，只验证其 records、triggers、cache 和 reducer 合同。
- 每个 decision 输出确定性 reason code，以及使用/未知字段。
- 不修改 runtime 证据或 SimLingo。

## 7. 阶段结束时只汇报

1. PASS/BLOCKED；
2. 创建或修改的文件；
3. 测试命令和结果；
4. 规则与 fixture 覆盖；
5. 未解决定义；
6. 明确确认未修改 SimLingo、未启动 CARLA、未运行 GPU、未训练或下载；
7. 如果用户已初始化 Git，则提供准确 git diff；否则提供文件清单和 hashes。

完成离线 evaluator 和测试后立即停止。没有新授权不得进入 live integration。
