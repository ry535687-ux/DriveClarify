# DriveClarify v0.1 算法规格说明

项目：**DriveClarify：闭环驾驶视觉—语言—动作模型中面向歧义指令的后果感知主动澄清**  
版本：v0.1 设计修订合同，2026-07-22
实现状态：**尚未实现**  
证据索引：`SOURCE_AUDIT.md`
调度增量：`DESIGN_DELTA_DUAL_RATE.md`

本文中的“必须”“禁止”“应该”“可以”都是未来实现的规范要求，不代表当前仓库已经具备相应功能。

## 1. 范围与非声明

### 1.1 v0 包含

- 校验前固定 `K=2` 的人工/oracle 解释；
- 在相同观测、同一 SimLingo 模型实例上，以受约束 HLC 条件顺序生成候选；
- 分项保存、不聚合的后果记录；
- 基于规则和词典序优先级的 ACT/ASK/WAIT cascade；
- `AskTimingStatus` 三值时序规则，不把 UNKNOWN 压成布尔 false；
- 每个 episode 最多一个固定模板问题；
- 仅用于 MVP 上界评估的 oracle 乘客回答；
- 规则化 safe holding，其中 ASK 永远等于“发送问题 + holding control”；
- 从当前观测重新生成计划的回答条件重规划接口。
- fast control 与 slow/event-driven clarification 的双速率合同及 Candidate Cache 生命周期。

### 1.2 v0 不包含且禁止声称

v0 不包含任意自由文本能力、学习式 parser 或解释生成器、学习式 ACT/ASK/WAIT、开放式问题生成器、形式化安全保证、真实道路验证、人因/HRI 结论、跨模型泛化、训练、微调、LoRA、RL 或完整 benchmark。Oracle 解释和回答只是 MVP 上界，不是部署方案。

现有 branch gate 只证明接口可行和条件效应存在，不证明语义正确、安全收益、实时性，也不证明 route L2、ADE 或 FDE 是安全指标。

## 2. 形式化输入与输出

### 2.1 状态

\[
s_t=(o_t,e_t,u,Z_t,H_t,B_t,d_t,C_t,x_t)
\]

| 分量 | 类型或形状 | 单位 | 来源 | 是否 oracle | 更新频率 |
|---|---|---|---|---:|---:|
| `o_t`，观测 | 模型就绪记录；相机张量形状随配置变化，含车速、目标点 `[2]`、时间戳和 frame ID | 混合 | 一次 SimLingo `tick()` 的现有基础 | 否 | 每 tick |
| `e_t`，ego 状态 | 位姿 `[6]`、速度 `[3]`、可选加速度/偏航角速度和有效位 | m、rad、m/s、m/s² | CARLA GNSS/IMU/speedometer 和未来 logger | 否 | 每 tick |
| `u`，原始指令 | 字符串、ID 和接收时间 | 无、s | oracle pilot 注入 | v0 是 | 事件触发 |
| `Z_t`，解释集合 | 0–2 个六槽位记录 | 混合 | 人工/oracle generator 和 validators | v0 是 | 指令、回答、失效时 |
| `H_t`，交互历史 | 只追加事件记录 | 混合 | 未来 clarification logger | 回答内容是 | 事件或每 tick |
| `B_t`，询问预算 | 整数 | 个问题 | 状态机 | 协议项 | 发送问题或新 episode 时 |
| `d_t`，当前截止时间 | `{type,time_s,validity,computed_at}` | s | 未来 deadline estimator | 否 | 每 tick |
| `C_t`，候选缓存 | `CANDIDATE_CACHE_SCHEMA.json` 记录 | 混合 | 未来 slow/event-driven loop | 否 | 合法 refresh 事件 |
| `x_t`，策略状态 | 状态枚举、持久 ID 和计时器 | 混合 | 未来 policy | 否 | 事件或每 tick |

完整缺失值规则见 `NOTATION_AND_DATA_MAPPING.md`。必需观测或 ego 字段缺失时进入 `FALLBACK`；未知的硬安全、规则或时序字段必须保持 UNKNOWN，不得授权 ACT、ASK 或 HOLDING，并输出保留未知来源的 reason-coded fallback。

### 2.2 语义输出与物理控制

\[
m_t \in \{ACT(z_k), ASK(q), WAIT\},\qquad
a_t=(steer,throttle,brake)
\]

- `ACT(z_k)`：选择并跟踪刚刚重新校验的候选计划；只有被选分支允许进入普通 PID/controller。
- `ASK(q)`：发送一次模板问题，并在同一 tick 执行独立选择的 holding 策略。
- `WAIT`：不发送新问题，同时执行 holding 策略；不等于冻结或必然停车。
- `a_t`：CARLA 归一化控制。语义动作和车辆控制必须同时记录；单独的 ASK 不是完整控制命令。

## 3. 交互状态机

必需状态为 `NORMAL`（正常）、`AMBIGUITY_ACTIVE`（歧义活动）、`QUESTION_SENT`（问题已发送）、`HOLDING`（保持中）、`ANSWER_RECEIVED`（收到回答）、`COMMITTED`（已提交）和 `FALLBACK`（回退）。完整进入条件、动作、每 tick 更新、退出和超时合同见 `DRIVECLARIFY_STATE_MACHINE.md`。

固定设计决定：

- 每个 clarification episode 最多成功发送一个语义问题；
- 同一规范化问题签名不能重复发送；
- WAIT 最大持续时间为 `min(T_WAIT_MAX, 回答过期时间, holding 有效期, decision deadline)`；`T_WAIT_MAX=TBD_REQUIRED`，未配置时禁止交互执行；
- 迟到回答只有在 episode/question 仍匹配、未过期且当前重新校验通过时才可使用；
- 回答指向的解释若已不可行，不能强制执行；
- `COMMITTED` 后只有新指令、新歧义或实质失效才能在新 episode ID 下重新进入澄清；旧回答一律忽略。
- `CommitReentryCause` 只能是 `NEW_INSTRUCTION/NEW_AMBIGUITY/MATERIAL_INVALIDATION/NONE`；只有前三者可清除 cache、创建新 episode 和恢复该新 episode 的预算。
- late answer、cosmetic paraphrase 和 ordinary scene update 的重入原因为 `NONE`。

## 4. 解释表示

### 4.1 本体

每个 `z_k` 必须含：

```text
{maneuver, lane, target_object, timing, distance, speed}
```

未使用槽位显式写 `null`。允许词表、单位和 validator 见符号映射文档。`PromptAdapter` 必须把有效解释映射到已支持的受约束 HLC；禁止把任意乘客文本直接传给 SimLingo，并声称当前接口支持自由文本。

### 4.2 Oracle 构造与校验

每个 pilot episode 必须在查看 DriveClarify 后果输出前，由标注者建立 `z_1,z_2`，并记录场景 ID、原指令、槽位、预期对比、oracle intent、地图 anchor 和标注来源。

校验顺序：

1. **Schema 校验**：六个槽位齐全，类型和词表正确。
2. **非同义校验**：规范化槽位必须不同；若允许计划集合、目标、拓扑和后果均相同，则合并为一个。
3. **对象落地**：引用对象存在、唯一，且 snapshot 年龄在 `TBD` 有效期内。
4. **地图落地**：road/lane/exit 存在并从当前拓扑可达。
5. **HLC 接受性**：能够映射到已审计的受约束命令接口，或以后明确验证的 adapter 合同。
6. **删除无效候选**：记录 reason code；禁止静默修复槽位。

删除后：0 个候选 → `FALLBACK`；1 个候选 → 仅在计划硬安全合法时 ACT；2 个候选 → 分支评估；超过 2 个不属于 v0。

## 5. 双速率共享候选计划与缓存接口

\[
p_t^k=f_\theta(o_t,z_k)=(\tau_t^k,v_t^k),\quad
\tau_t^k\in\mathbb{R}^{20\times 2},\quad
v_t^k\in\mathbb{R}^{10\times 2}.
\]

未来 live 调度必须遵守 `DESIGN_DELTA_DUAL_RATE.md`。Fast control loop 每个 CARLA tick 运行，但只执行已经验证且未过期的 committed plan 或 holding；它不等待 K 次 VLA forward。以下接口只在 slow/event-driven clarification loop 的合法 refresh trigger 上运行：

1. 每次 slow-loop refresh 的 temporal、route、UKF 预处理只执行一次；禁止每 tick 无条件触发 branch planner。
2. 冻结一份模型就绪观测，记录 `observation_id`、frame、仿真时间和单调时间。
3. 两个候选使用同一个已加载 SimLingo 模型对象。
4. 只替换已验证的受约束 HLC 条件；`k=1,2` 在 inference mode 下顺序执行。
5. 下一次前向前先 detach/copy 当前候选；记录模型实例 ID、HLC/prompt ID、开始/结束时间、shape、dtype、有限值 mask 和异常。
6. alternative 分支不得进入 PID。未来完整 v0 只能让 policy 最终选中的一个分支进入 PID。
7. 记录总 branch 时间；有在线实测时用实测，否则使用当前保守默认值 `1.25 s`。
8. 异常、shape 错误、NaN/Inf、输出缺失、观测过期、模型实例不一致或 wall-clock 超限都会使候选无效；禁止补齐或修复张量。
9. 剩一个候选时走单候选 cascade；无候选时 fallback。
10. 分支结果写入独立 Candidate Cache，至少含 `cache_id/episode_id/source_observation_id/generated_at_monotonic_s/generated_at_sim_time_s/valid_until_monotonic_s/plan_age_s/refresh_reason/refresh_requested/candidate_ids/validity_status`。
11. 执行前必须满足 `now < valid_until_monotonic_s` 且 `validity_status=VALID`；等号边界已过期，`EXPIRED/INVALIDATED/UNKNOWN` 均不可执行。
12. 每条 consequence record 通过 `cache_id` 关联 cache，且 episode、observation、candidate ID 必须一致。

Slow loop 运行期间，fast loop 继续执行上一个仍有效的 committed plan 或 holding。如果不存在仍有效控制，则立即输出 reason-coded safety fallback；不得等待 slow loop 完成。

当前证据只证明上述机制对已审计 HLC pair 可行且状态隔离，不证明语义 adapter 正确。

## 6. 后果数据结构

对候选 `k` 分项保存：

\[
c_t^k=[task,safety,rule,irreversibility,comfort,timing].
\]

v0 禁止把它合成为 `w1*safety + w2*task - w3*query`。硬安全和严重规则优先于任务、舒适性、progress 和询问次数。

以下表中“当前可算”指现有保留证据是否已经足够；“新增日志”表示未来是否需要同步 query、预测或记录。硬字段为 `UNKNOWN` 时按不可行处理。

### 6.1 任务维度 `task`

| 字段 | 定义与单位 | CARLA/SimLingo 来源 | 当前可算与新增日志 | 缺失处理 | 风险代理 | 最可能的审稿质疑 |
|---|---|---|---|---|---:|---|
| `target_road_id` | 目标终点的 CARLA road ID；标识符 | oracle 槽位 + CARLA map | 否；新增 map grounding | null → 目标比较未知 | 否 | road ID 不一定有语义且跨地图不稳定 |
| `target_lane_id` | 目标 lane ID；标识符 | oracle 槽位 + CARLA waypoint | 否；新增日志 | null → lane 目标未知 | 否 | lane ID 正负号和可达性约定 |
| `target_exit_id` | 标注的出口决策 ID；标识符 | oracle 场景标注 + map topology | 否；新增日志 | 不适用可为 null；需要时未知 | 否 | CARLA 没有统一语义 exit ID |
| `goal_region_match` | 候选终点是否落入标注 goal region；布尔 | 候选到世界坐标变换 + oracle region | 否；新增变换/日志 | unknown 阻止后果等价 | 是 | 对 region 构造和 horizon 敏感 |
| `wrong_goal` | 候选拓扑是否违背选定目标；布尔 | oracle goal + map matcher | 否；新增日志 | unknown，禁止默认 false | 否 | 若标签来自自身 evaluator 会形成循环 |
| `route_progress` | 沿声明 route 中心线的有符号进度；m 和比例 | map projection | 当前无在线值；新增日志 | null，禁止代入 0 | 是 | 可能奖励不安全或错误路线 |
| `missed_turn_or_exit` | 是否不可逆地错过目标转弯/出口；布尔 | topology + boundary crossing | 否；新增日志 | unknown 使 recoverability 未知 | 否 | decision boundary 标注具有主观性 |

### 6.2 安全维度 `safety`

| 字段 | 定义与单位 | 来源 | 当前可算与新增日志 | 缺失处理 | 风险代理 | 最可能的审稿质疑 |
|---|---|---|---|---|---:|---|
| `predicted_collision` | 声明 horizon 和预测模型下是否碰撞；布尔 | world actors + 候选投影 | 否；新增 predictor | unknown → 硬不可行 | 是 | actor 未来假设与漏检 |
| `min_front_ttc_s` | horizon 内最小正向 front TTC；s | actor 状态 + lane association | 否；新增日志 | 无相关 actor 可为 null，但感知缺失为 unknown | 是 | 匀速 TTC 和 actor 匹配 |
| `min_rear_ttc_s` | 计划或 holding 引起的最小 rear TTC；s | rear actor + 预测 | 否；新增日志 | unknown → holding 不可行 | 是 | 后向风险易被忽略且预测不确定 |
| `min_clearance_m` | 车辆 footprint 间最小间距；m | actor geometry + ego footprint | 否；新增日志 | unknown → 硬不可行 | 是 | 离散采样可能漏过碰撞 |
| `required_deceleration_mps2` | 避免冲突所需最小减速度幅值；m/s² | 相对运动学 + 动力学假设 | 否；新增日志 | 相关冲突下 unknown → 硬不可行 | 是 | 依赖路面摩擦、延迟和预测模型 |
| `unsafe_speed` | 派生速度是否超过物理/场景界限；布尔 | speed waypoint 转换 + map/weather/risk | 否；新增转换 | unknown → 硬不可行 | 是 | 当前 speed 输出不是标量速度 |
| `safety_class` | `SAFE/CAUTION/UNSAFE/UNKNOWN` | 上述安全字段和冻结规则表 | 否；新增日志 | 任一必需输入缺失 → `UNKNOWN` | 是 | 校准不能提供形式化安全保证 |

### 6.3 规则维度 `rule`

| 字段 | 定义与单位 | 来源 | 当前可算与新增日志 | 缺失处理 | 风险代理 | 最可能的审稿质疑 |
|---|---|---|---|---|---:|---|
| `red_light_conflict` | 是否越过当前红灯对应停止线；布尔 | 交通灯状态 + stop-line geometry | 否；新增日志 | unknown → 硬不可行 | 否/场景模型 | 灯与 lane 关联及相位时间 |
| `stop_sign_conflict` | 是否未满足停车标志要求；布尔 | sign trigger + trajectory timing | 否；新增日志 | 适用时 unknown → 硬不可行 | 否/场景模型 | “完全停车”的时间定义 |
| `lane_invasion` | footprint 是否越出允许 lane marking；布尔 | map marking + footprint sweep | 否；新增日志 | unknown | 是 | 合法变道与偶然压线的区分 |
| `solid_line_crossing` | footprint 是否跨越实线；布尔 | marking geometry + sweep | 否；新增日志 | unknown → severe 状态未知 | 否/场景模型 | CARLA marking 精度 |
| `sidewalk_or_offroad` | 是否进入 sidewalk 或不可行驶区域；布尔 | drivable polygon + footprint | 否；新增日志 | unknown → 硬不可行 | 否/场景模型 | 地图精度与紧急例外 |
| `wrong_way` | heading/topology 是否逆向；布尔 | waypoint direction + heading | 否；新增日志 | unknown → 硬不可行 | 否/场景模型 | 路口 lane topology 歧义 |
| `speed_limit_violation` | 派生速度是否超过限速与冻结容差；布尔 | speed 转换 + waypoint speed limit | 否；新增日志 | unknown | 是 | 容差及 speed head 解释 |
| `severe_rule_conflict` | 预先定义的严重违规 OR；布尔/unknown | 上述 rule 字段 | 否；新增日志 | 任何必需项未知 → unknown | 否 | severity taxonomy 必须预注册 |

### 6.4 不可逆性维度 `irreversibility`

| 字段 | 定义与单位 | 来源 | 当前可算与新增日志 | 缺失处理 | 风险代理 | 最可能的审稿质疑 |
|---|---|---|---|---|---:|---|
| `distance_to_branch_m` | 到最早标注 branch boundary 的 route 距离；m | map topology + ego projection | 否；新增日志 | unknown | 是 | 不同候选可能定义不同距离 |
| `time_to_branch_s` | 按保守运动预测到 boundary 的时间；s | distance + ego/holding dynamics | 否；新增日志 | unknown；近零速度需显式 rollout | 是 | 对未来速度假设敏感 |
| `crossed_branch_boundary` | ego footprint 是否已越过 branch event；布尔 | boundary geometry + pose history | 否；新增日志 | unknown | 否 | boundary 选择可能主观 |
| `crossed_solid_line` | ego 是否已跨过相关实线；布尔 | marking geometry + pose sweep | 否；新增日志 | unknown | 否 | 几何和采样精度 |
| `crossed_exit_gore` | ego 是否越过出口导流区决策边界；布尔 | oracle/map boundary + pose | 否；新增日志 | 不适用为 null；需要时 unknown | 否 | 并非所有地图都有 gore 标注 |
| `other_interpretations_still_reachable` | 其他解释是否仍在拓扑和硬约束上可达；布尔 | map search + validators | 否；新增日志 | unknown 不计为可达 | 是 | 搜索 horizon 和动力学不完整 |
| `recoverability_class` | `FULL/PARTIAL/NONE/UNKNOWN` | 上述边界与可达性字段 | 否；新增日志 | `UNKNOWN` | 是 | 依赖 controller 和 horizon |

### 6.5 舒适性维度 `comfort`

| 字段 | 定义与单位 | 来源 | 当前可算与新增日志 | 缺失处理 | 风险代理 | 最可能的审稿质疑 |
|---|---|---|---|---|---:|---|
| `longitudinal_acceleration_mps2` | 声明 horizon 内纵向加速度；m/s² | ego dynamics 或 plan rollout | 否；新增日志 | null，禁用该 tie-break | 是 | 预测与实际执行加速度不同 |
| `lateral_acceleration_mps2` | 横向加速度峰值；m/s² | curvature-speed 或 IMU | 否；新增日志 | null | 是 | 坐标系、噪声和滤波假设 |
| `jerk_mps3` | 加速度导数峰值；m/s³ | 同步 acceleration series | 否；新增日志 | null | 是 | 对采样率和滤波高度敏感 |
| `hard_brake` | 减速度是否超过冻结阈值；布尔 | acceleration/control log | 否；新增日志 | unknown，禁止默认 false | 是 | 阈值和控制命令/实际减速度差异 |
| `steering_rate` | 转向命令变化率峰值；归一化值/s | control sequence + 时间 | 否；新增日志 | null | 是 | 归一化 steer 依车型变化 |

### 6.6 时序维度 `timing`

| 字段 | 定义与单位 | 来源 | 当前可算与新增日志 | 缺失处理 | 风险代理 | 最可能的审稿质疑 |
|---|---|---|---|---|---:|---|
| `branch_forward_latency_s` | 顺序分支前向 wall time 总和；s | wrapper 单调时钟 | 有证据；未来新增在线日志 | 在线缺失时可用 1.25 s，但必须记录来源 | 否 | 硬件、负载和场景依赖 |
| `question_latency_s` | 构造并发送固定模板问题的 wall time；s | 未来 clarification channel | 否；新增日志 | unknown → `AskTimingStatus=UNKNOWN` | 否 | 通道和系统负载依赖 |
| `consequence_latency_s` | evaluator wall time；s | 未来 evaluator 时间戳 | 否；新增日志 | 保持 unknown；由 slow wall-clock budget 单独处理，不计入本阶段固定 `T_clarify` | 否 | map/actor 查询耗时可能无界 |
| `estimated_response_latency_s` | 预设或测得的乘客回答延迟；s | pilot 协议或未来通道 | 否；新增日志 | unknown → `AskTimingStatus=UNKNOWN` | 是 | oracle 延迟不等于真人延迟 |
| `replanning_latency_s` | 收到回答到新计划校验完成；s | 未来 replanner 时间戳 | 否；新增日志 | unknown → `AskTimingStatus=UNKNOWN` | 否 | 可能与 branch 时间重复计算 |
| `safety_margin_s` | 不确定性与执行余量；s | 规则/物理/pilot 配置 | 否 | unknown → `AskTimingStatus=UNKNOWN` | 是 | 余量任意或重复计算不确定性 |
| `time_to_decision_s` | 最早 event/map/risk boundary 时间；s | 未来 deadline estimator | 否；新增日志 | unknown → `AskTimingStatus=UNKNOWN` | 是 | 对 boundary 和运动模型敏感 |
| `clarification_slack_s` | `T_decision-T_clarify`；s | 完整 timing 字段派生 | 否；新增日志 | unknown | 否 | 不确定输入会造成虚假精确性 |
| `ask_timing_status` | `FEASIBLE/INFEASIBLE_KNOWN/UNKNOWN` | 完整 timing 与 provenance | 否；派生 | UNKNOWN 保留且直接 fallback | 否 | 布尔压缩会掩盖缺失来源 |
| `ask_timing_reason_code` | 确定性 timing reason | 固定规则表 | 否；派生 | 必须说明具体未知项 | 否 | 原因优先级必须固定 |

机器可读约束和同样的来源、缺失、代理及审稿风险元数据见 `CONSEQUENCE_SCHEMA.json`。

## 7. 结构化分歧

对硬可行候选定义：

- `D_G`：goal region、road/lane/exit、wrong-goal 或 missed-turn 拓扑结果不等价；
- `D_S`：`safety_class` 不同，包括已知与 `UNKNOWN` 的差异；
- `D_R`：`severe_rule_conflict` 不同，包括已知与 `UNKNOWN` 的差异；
- `D_I`：`recoverability_class`、不可逆边界或剩余可达性不同。

\[
Critical_t=D_G\lor D_S\lor D_R\lor D_I.
\]

必需输入未知时 `Critical_t=UNKNOWN`。UNKNOWN 必须保持 UNKNOWN 并直接进入相应 reason-coded fallback，不得把它当作可继续级联的 `true` 或 `false`，也不得授权 ACT、ASK 或 HOLDING。

- **后果等价**：候选均硬可行；四类分歧全为 false；任务终点类别相同；所有必需比较字段已知。路径/speed 结构仍可不同。
- **决策关键**：只有全部必需比较字段已知且 `Critical_t=true`。
- **歧义但低后果**：语义不同，但在声明 horizon 内后果等价，且都保留后续选择。

示例：

- route L2 很大但不 critical：两条路径在同一宽车道/共享走廊内摆动，目标、安全、规则和可恢复性一致。
- route L2 很小但 critical：出口导流区或实线附近，厘米级差异落在不可逆边界两侧；一条保留出口，一条错过或越线。

Path ADE/FDE、最大横向分歧、分歧起点、speed difference 和 stopping-distance difference 只作为结构诊断记录，不能独立改变安全结论。

## 8. 决策剩余时间与三值 ASK 时序

v0 优先使用几何/事件式 TTD：

\[
T_{decision}=\min(T_{junction},T_{solid\_line},T_{exit},T_{stop\_line},T_{risk}).
\]

只有有效且相关的项参与最小值：

- 速度接近零（`|v|<v_eps`，`v_eps=TBD`）不代表时间无限；必须采用保守 holding/motion rollout，否则 map-only 时间为 `UNKNOWN`；
- 已越过边界时取 0，并删除或标记相关解释不可恢复；
- 不适用的地图边界是 `null`，缺失但必需的数据是 `UNKNOWN`；
- 候选 decision point 不同，取能保留全部选项的最早相关点；
- actor risk 可早于地图边界，因此有效 `T_risk` 参与同一最小值。

\[
T_{clarify}=T_{branch}+T_{question}+T_{response}+T_{replan}+T_{margin}.
\]

`T_branch` 优先使用在线 wall-clock 实测；当前默认 `1.25 s`，来自保留证据中的约 `0.624 s + 0.625 s`。其余项无依据时必须为 `TBD_REQUIRED`。

不得只使用 boolean `AskFeasible`。正式定义：

```text
AskTimingStatus ∈ {FEASIBLE, INFEASIBLE_KNOWN, UNKNOWN}
```

- `FEASIBLE`：所有必要 timing 已知、有效、未过期且来源可信，并严格满足 `T_decision > T_clarify`；这只是 ASK 必要条件，不是充分条件。
- `INFEASIBLE_KNOWN`：所有必要 timing 已知、有效、未过期且来源可信，并满足 `T_decision <= T_clarify`。只有该状态可以继续检查 holding 是否能延长窗口或保留选项。
- `UNKNOWN`：任一必要 timing 缺失、过期、无效或来源不可信。UNKNOWN 不转换成 false，不授权 ACT、ASK 或 HOLDING，直接进入具体 reason-coded fallback。

`T_decision == T_clarify` 属于 `INFEASIBLE_KNOWN`，不能 ASK。UNKNOWN reason 至少区分 decision deadline、branch/question/response/replan latency、safety margin、来源不可信、过期和无效。holding 自身 deadline 或 validity 为 UNKNOWN 时同样 fallback。

## 9. 安全保持策略候选

v0 至少生成：

- **H1——以安全速度保持车道**：留在当前合法车道，以场景校验后的安全速度行驶；速度可以非零或为零。
- **H2——保持车道并平顺减速**：在加速度/jerk 限制内降速，同时不能制造不可接受的后向风险。
- **H3——在合法位置受控停车**：只在经过验证的合法、不阻塞位置停车；不是默认 WAIT。
- **H4——在共享可行走廊内低速移动**：只沿所有有效解释共同允许的走廊移动，尽可能保留选择。

每个 `h` 必须在声明 horizon 内检查 front TTC、rear TTC、drivable area、红灯/停止线、路口阻塞、不可逆边界、舒适性、progress loss 和剩余解释可达性。所有必需安全/规则字段已知且通过时，holding 才算硬可行；rear risk 或 blocking 数据缺失时禁止判定安全。

\[
O(h)=\frac{|\{z\in Z_{before}:z\text{ 在 }h\text{ 后仍可行}\}|}{|Z_{before}|}.
\]

安全和严重规则优先于 `O(h)`。在硬可行 holding 中，依次偏好更高 `O(h)`、更小不可逆损失、更好舒适性和更小 progress loss。`O(h)=1` 是优选而非硬保证。共享走廊为空时进入 safety override/robust fallback，禁止强行使用 H4。立即停车只作为显式 baseline，可能因后车或路口阻塞而不安全。

TTC、减速度、jerk、合法停车 clearance、阻塞和最小 progress 阈值均为 `TBD_REQUIRED`，只能由交通规则、物理约束、pilot 或冻结的 development split 决定。

## 10. ACT / ASK / WAIT 级联规则

策略采用词典序 cascade，不使用单一加权损失：

1. **语义和地图校验**：删除无效 `z_k`；0 个 → fallback。
2. **计划生成和校验**：拒绝异常、shape 错误、NaN/Inf、过期观测和超时计划；0 个 → fallback。
3. **Cache 执行门**：cache 必须属于当前 episode、ID 关联一致、`validity_status=VALID` 且 `now < valid_until`；否则按 `EXPIRED/INVALIDATED/UNKNOWN/ID_MISMATCH` reason-coded fallback 或请求 slow refresh，禁止执行旧候选。
4. **硬安全与严重规则可行性**：已知不安全或严重违规的候选不可选；未知硬字段保持 UNKNOWN 且直接 reason-coded fallback；不得授权 ACT、ASK 或 HOLDING。
5. **三值 timing gate**：在任何 ACT/ASK/HOLDING 之前计算 `AskTimingStatus`。`UNKNOWN` 直接 fallback；`FEASIBLE` 才能检查 ASK；`INFEASIBLE_KNOWN` 才能检查 pre-question WAIT。
6. **只剩一个候选**：只有该候选当前硬可行、cache 可执行且 timing/控制有效性已知时 ACT，否则 fallback。
7. **后果等价**：若所有候选等价且全部必要硬字段已知，按 tie-break ACT；当前没有证据说明提问有价值。
8. **decision-critical 检查**：false 时按 tie-break ACT；true 时继续；UNKNOWN 直接使用其具体 unknown reason fallback。
9. **回答优先**：匹配回答必须在新问题之前处理；重新校验并通过 slow-loop refresh 从当前观测重生成，禁止给旧 path 换标签。
10. **ASK 可行性**：`AskTimingStatus=FEASIBLE`，且问题相关/不重复/可回答、安全 holding、预算、无未决问题和 wall-clock budget 全部已知满足时，才输出 `ASK(q)+HOLD`。
11. **提问后的 WAIT**：已有成功发送的问题、无有效回答、全部 holding deadline/validity 已知、未超时且 holding 硬可行时，输出独立 `WAIT+HOLD`。
12. **提问前的 WAIT**：仅 `AskTimingStatus=INFEASIBLE_KNOWN` 且 holding 能明确延长 decision window 或保留选项时 WAIT；每 tick 重算且不能超过已知的 `T_WAIT_MAX`、holding validity 和 deadline。
13. **ASK/WAIT 均不可用**：只在某个候选对所有仍可能解释都硬可行且全部必要执行字段已知时采用 robust ACT，否则 reason-coded safety override/fallback。

Tie-break 顺序：

1. 硬安全且无严重规则冲突；
2. 对所有仍可能解释都有效；
3. 最大 recoverability/option preservation；
4. 不造成 wrong goal 或 missed turn；
5. 更舒适；
6. 更高 progress；
7. 选择 ID 最小者保证可复现。

任务或进度永远不能与碰撞或严重违规交换。如果某个 tie-break 所需字段未知，则跳过该层，禁止填入“中性”数值。

## 11. 提问策略

MVP 只使用确定性模板。问题必须满足：

- `Relevant(q,Z_t)`：询问真正区分候选且可能改变动作的规范化槽位；
- `NonRedundant(q,H_t)`：本 episode 中未成功发送同一签名；
- `Answerable(q)`：提供两个已经落地、明确枚举的选项。

模板：

```text
动作歧义：“在前方路口，您的意思是【左转】还是【直行】？”
出口/车道歧义：“您的意思是【走当前出口/车道】还是【继续到下一个出口/车道】？”
目标对象歧义：“您的意思是【第一辆白色汽车】还是【第二辆白色汽车】？”
```

占位内容只能来自已校验候选和已落地的地图/对象描述。问题必须保存候选 ID 和顺序。v0 每 episode 只能成功发送一个语义问题，不训练也不调用开放式 question generator。

## 12. 回答处理与回退

| 情况 | 必需处理 |
|---|---|
| 正确 oracle 回答 | 绑定候选，按当前状态重新校验语义/地图，从当前观测重新生成计划，硬检查后提交或 fallback |
| 无回答 | 只有 holding 及其 timeout/deadline 全部已知可行时 WAIT 到最早边界，然后 robust fallback 或 safety override；未知则立即 reason-coded fallback |
| 延迟回答 | 仅在 episode/question 相同且解释未过期时采用，否则记录并忽略 |
| 无效回答 | 不恢复预算、不猜测；只有 holding 的安全、规则、deadline 和 validity 全部已知可行时才在 timeout 内继续，否则 fallback |
| 错误回答 | 运行时视为乘客声明的选择，但 validators 仍优先；评测日志单独记录 oracle mismatch |
| 仍然歧义 | v0 不再问第二个语义问题；只有 holding 的全部硬字段和时序已知可行时继续，之后 robust fallback |
| 回答修改指令 | 创建新 instruction/episode，重建解释并生成计划；丢弃旧候选张量 |
| 解释过期后回答 | 禁止执行过期选择；记录 `STALE_ANSWER`，按新指令重规划或 fallback |

Oracle passenger answer 必须独立于 DriveClarify consequences 分配。禁止用 DriveClarify 自己的 evaluator 生成 oracle ACT/ASK/WAIT 标签或 oracle 回答。

## 13. 算法 1

完整论文级双速率伪代码见 `ALGORITHM_1_PSEUDOCODE.md`：Fast Control Loop 每 tick 输出一个控制；Slow/Event-Driven Clarification Loop 只在合法 trigger 上执行 K 个顺序分支并更新 Candidate Cache。两者均为设计合同，尚未实现。

## 14. 与当前仓库的映射

以下只表示未来设计位置，本阶段没有创建任何实现文件。

| 模块 | 输入 | 输出 | 已有基础 | 建议未来位置 | 依赖 | 当前禁止内容 |
|---|---|---|---|---|---|---|
| `DriveClarifyAgent` | CARLA `input_data`、时间戳 | control + logs | wrapper gate 已证明挂接可行 | `driveclarify/agent.py` | SimLingo agent 和全部模块 | 当前运行 v0 或修改 SimLingo |
| `FastControlLoop` | ego/risk/answer/deadline/cache metadata、有效 plan/holding | 每 tick 唯一 control + refresh request | 无 | `driveclarify/fast_control.py` | reducer、validators、controller | 等待 K forwards 或无条件每 tick branching |
| `SlowClarificationLoop` | 合法 trigger、冻结观测、K 个条件 | Candidate Cache + semantic recommendation | branch gate 仅证明顺序分支接口 | `driveclarify/slow_clarification.py` | planner、consequences、deadline | 直接输出车辆控制 |
| `CandidateCache` | slow-loop 结果和生命周期 | 带版本 cache | 无 | `driveclarify/candidate_cache.py` | `CANDIDATE_CACHE_SCHEMA.json` | 执行过期/失效/UNKNOWN cache |
| `InstructionManager` | 指令和交互事件 | 带版本指令记录 | 无 | `driveclarify/instruction.py` | event clock/logger | 自由文本 parser |
| `InterpretationGenerator` | 指令、map/object snapshot | 两个 oracle 槽位记录 | 无 | `driveclarify/interpretations.py` | 场景标注 | 学习式或 LLM generator |
| `PromptAdapter` | 有效 `z_k`、共享输入 | 受约束 `DrivingInput` 条件 | 已审计 HLC prompt 路径 | `driveclarify/prompt_adapter.py` | SimLingo tokenizer/template | 任意乘客文本声明 |
| `CandidatePlanner` | 冻结观测和 K 个条件 | K 个 path-speed 记录 | branch/stability wrappers | `driveclarify/candidate_planner.py` | 同一模型实例 | 并行前向或 alternative PID |
| `ConsequenceEvaluator` | candidates、world/map snapshot | 分项 consequences | 无 | `driveclarify/consequences.py` | geometry、actor predictor、schema | 加权总分或自生成 oracle |
| `DeadlineEstimator` | candidates、map/events、actors | deadline record | 无 | `driveclarify/deadline.py` | topology/risk models | v0 学习式 TTD |
| `AskActWaitPolicy` | consequences、deadline、history | semantic decision | 无 | `driveclarify/policy.py` | 状态机 | 学习式策略或加权损失 |
| `ClarificationManager` | 问题/回答事件 | 带版本交互状态 | 无 | `driveclarify/clarification.py` | channel/templates | 开放式 generator 或真人结论 |
| `SafeHoldingController` | 当前状态、candidates、world/map | holding candidates/control | 无 | `driveclarify/holding.py` | CARLA controller 和 validators | 假设 ASK=停车 |
| `AnswerConditionedReplanner` | 当前观测和有效回答 | 重新生成的计划集合 | branch hook 是部分基础 | `driveclarify/replanner.py` | interpretation/prompt/planner | 给旧轨迹换标签 |
| `DriveClarifyLogger` | 所有阶段记录 | 带 schema 版本 JSONL | gate JSON/JSONL 模式 | `driveclarify/logger.py` | 单调时钟和仿真时钟 | 编造缺失值 |

`runtime/branch_interface_gate/` 和 `runtime/branch_stability_gate/` 是冻结证据，不是正式 package 位置。

## 15. 测试、基线和消融计划

完整协议见 `TEST_AND_ABLATION_PLAN.md`。必需 baselines：Never Ask/Always Act、Always Ask + same holding、Always Wait、Always Stop、language-only threshold、**Ambiguity-Only / Multi-Interpretation Trigger**、KnowNo-style calibrated set、Ask-to-Act-inspired ACT/ASK、risk-only、trajectory-only、oracle intent 和 oracle ask。

必需 ablations：no TTD、no speed、route-only、no irreversibility、no WAIT、ASK=immediate stop、WAIT without ASK、no answer-conditioned replanning 和 K=1/2/3。

Ask-to-Act-inspired baseline 可以读取当前 observation、ego state、original instruction、ambiguity features、question/answer history 和 query budget；禁止读取显式 counterfactual path-speed branches、pairwise TTC/rule/recoverability differences，以及独立 WAIT 动作。

公平比较必须共享 backbone、scenarios、seeds、candidate interpretations、oracle answer、response latency、query budget、initial state 和 wall-clock accounting。规则版 MVP 不训练，因此不使用“same training budget”。

## 16. 第一个预设真值试验定义

### 16.1 场景

**路口左转与直行歧义**。

| 项目 | Pilot 合同 |
|---|---|
| 歧义指令 | “到前面路口后，走那边。”这是脚本化 oracle pilot 文本，不代表当前支持自由文本。 |
| `z_1` | `maneuver=LEFT`；其余未用槽位为 null，并绑定左转地图 anchor |
| `z_2` | `maneuver=STRAIGHT`；其余未用槽位为 null，并绑定直行地图 anchor |
| 正确 oracle intent | 评测前固定，例如 `z_1`；不同场景变体中做平衡 |
| CARLA route/location | 可复现 seed 的接近车道；左转和直行均合法可达；标注 junction branch point、lane/road ID、必要的 stop-line/light；起点到边界距离足以覆盖 `T_clarify`；无未记录特殊规则 |
| 预期候选差异 | topology/goal branch 不同，path 应在路口前后产生分歧；差异大小不是通过标准 |
| 决策边界 | junction branch point、相关实线、停止线和 actor risk boundary 中最早者 |
| 预期策略 | critical 前若后果等价则 ACT；critical 且 ASK/holding 可行时 ASK+HOLD；未回答时 WAIT+HOLD；有效回答后重新生成并 ACT |
| 无回答 | 只等待到有界 expiry/deadline；若存在对全部解释硬可行的 robust action 则执行，否则 safety override/fallback |
| 延迟回答 | 过期前且重新校验通过才采用；commit、expiry 或越界后忽略 |

### 16.2 证伪条件

出现任一情况就阻断或推翻当前设计：

1. 两个解释不能在只改变 instruction condition 的情况下映射到受约束 HLC；
2. shared-observation baseline transparency 失败；
3. `1.25 s` branch 成本耗尽 decision window，且无安全 holding 能延长窗口；
4. candidate outputs 无法映射到已验证世界坐标系和 horizon；
5. 无法在不编造数据的情况下同步硬安全/规则信息；
6. front/rear/junction 约束下 H1–H4 全部不可行；
7. 回答条件重规划无法在 deadline 前完成；
8. cascade 发生振荡、重复问题或消费过期回答；
9. oracle ASK 标签或回答依赖 DriveClarify 自己的 evaluator。

该 pilot 通过后再考虑：当前出口与下一出口、第一辆与第二辆白车、障碍物左绕与右绕、两个停车目标。每类场景使用前都必须补充 grounding、合法边界和证伪标注。
