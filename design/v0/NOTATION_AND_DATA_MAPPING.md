# DriveClarify v0.1 符号与数据映射

状态：规范性设计合同，不代表已经实现。  
坐标要求：每条候选计划必须携带明确的 `frame_id`。现有 gate 只证明二维张量形状，没有证明全局坐标系和单位合同。

## 0. 保留英文标识符的中文含义

以下英文只作为代码、字段或论文接口标识保留，正文含义统一按中文理解：

| 保留标识符 | 中文含义 |
|---|---|
| `oracle` | 由场景预先提供、独立于 DriveClarify 评分器的真值或上界信息 |
| `candidate` | 候选解释或候选计划 |
| `candidate cache` | slow loop 生成、供 fast loop 校验和读取的带版本候选缓存 |
| `refresh` | 由合法事件触发的候选缓存重新生成 |
| `holding` | ASK/WAIT 期间实际控制车辆的安全保持策略 |
| `fallback` | 正常澄清不可行时的回退处理 |
| `baseline` | 用于公平比较的基线方法 |
| `consequence` | 分项保存的候选后果 |
| `deadline` | 不可逆决策截止时间 |
| `episode` | 一次独立的澄清回合 |
| `robust` | 对所有仍可能解释都保持可行的鲁棒选择 |
| `parser` / `prompt` | 解析器 / 输入模型的提示条件 |
| `wrapper` / `gate` | DriveClarify 侧包装器 / 工程门禁实验 |
| `evaluator` / `logger` / `controller` | 评估器 / 日志记录器 / 车辆控制器 |
| `ACT` / `ASK` / `WAIT` | 执行某解释 / 提问并保持 / 不提新问题并保持 |
| `AskTimingStatus` | ASK 时序三值状态：可行、已知不可行或未知 |
| `CommitReentryCause` | COMMITTED 开启新 episode 的枚举原因 |
| `NORMAL` 等七个大写状态名 | 状态机的固定接口枚举，中文说明见状态机文档 |

## 1. 核心符号

| 符号 | 类型或形状 | 单位 | 含义 | 来源与状态 |
|---|---|---|---|---|
| `t` | 整数 tick 索引 | tick | 当前闭环更新时间步 | 未来 logger；设计项 |
| `o_t` | 结构化观测 | 混合 | 所有分支共享的一份不可变预处理观测 | 当前 wrapper 已有基础；相机张量形状随配置变化 |
| `e_t` | ego 状态记录 | 混合 | 位姿、速度及带有效位的可选动力学量 | GNSS/IMU/speedometer 部分可用；完整动力学尚未记录 |
| `u` | UTF-8 字符串 | 无 | 原始歧义指令 | v0 由 oracle 注入；当前没有自由文本 parser |
| `z_k` | 六槽位记录 | 混合 | 第 `k` 个候选解释 | v0 为人工/oracle |
| `Z_t={z_1,z_2}` | 0–2 个有效记录的集合 | 无 | 当前经过校验的解释集合 | 校验前 K=2，之后可能减少 |
| `H_t` | 有序事件列表 | 混合 | 问题、回答、决策、过期和 episode ID | 未来 logger；设计项 |
| `B_t` | 非负整数 | 个问题 | 当前澄清 episode 的剩余询问预算 | v0 默认 1 |
| `d_t` | 单调时钟时间戳 | s | 当前不可逆决策截止时间 | 未来 estimator；当前不可用 |
| `x_t` | 枚举和持久字段 | 无 | 交互策略状态 | 未来状态机 |
| `C_t` | Candidate Cache 记录 | 混合 | 当前 episode 的带版本候选、生成时间、有效期和刷新原因 | 未来 slow loop；schema 已定义 |
| `p_t^k=(tau_t^k,v_t^k)` | 一对张量 | 模型坐标 | 候选路径和 speed waypoint 表示 | 当前分支接口已验证 |
| `tau_t^k` | 去掉 batch 后 `[20,2]` | 模型坐标；是否为米仍需验证 | 累积 route waypoints | 当前 `pred_route=[1,20,2]`，形状已验证 |
| `v_t^k` | 去掉 batch 后 `[10,2]` | 模型坐标；不是标量 m/s | PID 使用的累积 speed waypoints | 当前 `pred_speed_wps=[1,10,2]`，形状已验证 |
| `c_t^k` | 结构化记录 | 混合 | 未聚合的任务、安全、规则、不可逆性、舒适性和时序后果 | 未来 evaluator |
| `D_G,D_S,D_R,D_I` | 布尔值或 `UNKNOWN` | 无 | 目标、安全等级、严重规则、可恢复性分歧 | 未来 evaluator |
| `Critical_t` | 三值布尔 | 无 | `D_G OR D_S OR D_R OR D_I`；`UNKNOWN` 不能转成 false | 设计项 |
| `T_decision` | 非负数、0 或 `UNKNOWN` | s | 最早有效的地图、事件或风险边界时间 | 未来 estimator |
| `T_clarify` | 非负数 | s | 分支、提问、回答、重规划和余量的总延迟 | 分支默认值有证据，其余待定 |
| `AskTimingStatus_t` | 三值枚举 | 无 | `FEASIBLE/INFEASIBLE_KNOWN/UNKNOWN` | 未来 evaluator；禁止压成布尔值 |
| `CommitReentryCause_t` | 四值枚举 | 无 | `NEW_INSTRUCTION/NEW_AMBIGUITY/MATERIAL_INVALIDATION/NONE` | 未来 reducer |
| `m_t` | 带标签联合类型 | 无 | `ACT(z_k)`、`ASK(q)` 或 `WAIT` | 语义策略输出 |
| `a_t` | `(steer,throttle,brake)` | CARLA 归一化控制 | 实际车辆控制 | 未来选定计划 PID 或 holding controller |
| `h_j` | holding 策略候选 | 无 | ASK 或 WAIT 期间使用的 H1–H4 运动策略 | 未来 holding controller |
| `O(h)` | `[0,1]` 比例 | 无 | holding 后仍可行的解释占 holding 前可行解释的比例 | 未来 evaluator；优先级低于安全 |

## 2. 形式化状态映射

`s_t = (observation, ego_state, original_instruction, interpretation_set, interaction_history, query_budget, current_deadline, candidate_cache, policy_state)`

| 字段 | 数据类型或形状 | 单位 | 运行来源 | 是否 oracle | 更新频率 | 缺失值合同 |
|---|---|---|---|---:|---:|---|
| `observation` | 模型就绪相机张量、车速、目标点、基础 prompt、`frame_id` 和时间戳 | 混合 | 当前 `tick()` 和 `DrivingInput` | 否 | slow-loop refresh 时冻结；fast loop 只读最新轻量状态摘要 | 必需张量缺失或时间不单调时，不生成新 cache；fast loop 使用仍有效控制，否则 fallback |
| `ego_state` | 位姿 `[x,y,z,roll,pitch,yaw]`、速度 `[vx,vy,vz]`、可选加速度/偏航角速度及有效位 | m、rad、m/s、m/s² | CARLA GNSS/IMU/speedometer 和未来 logger | 否 | 每 tick | 必需速度或位姿缺失时 fallback；可选动力学保持 `UNKNOWN` |
| `original_instruction` | 字符串、`instruction_id` 和接收时间 | 无、s | oracle pilot 注入；未来乘客通道 | v0 是 | 事件触发 | 空或格式错误的输入不能开启澄清 episode |
| `interpretation_set` | 长度 0–2 的本体记录数组 | 混合 | 人工/oracle 生成器和 validators | 是 | 指令、回答或失效时 | 删除无效候选并记录原因，禁止静默修复 |
| `interaction_history` | 只追加事件列表 | 混合 | 未来 `ClarificationManager` 和 logger | 回答在 MVP 是 | 事件或每 tick | 历史丢失会禁用防重复 ASK，并强制采用不再提问的 fallback |
| `query_budget` | 整数 | 个问题 | episode 状态 | 协议项 | ASK 或新 episode 时 | 每 episode 默认 1；仅成功发送问题后减一 |
| `current_deadline` | `{boundary_type,time_s,validity,computed_at}` | s | 未来 `DeadlineEstimator` | 否 | fast loop 每 tick 检查；slow loop refresh 时重估 | 必需值未知时 `AskTimingStatus=UNKNOWN`，直接 reason-coded fallback；已越界时 `time_s=0` |
| `candidate_cache` | `CANDIDATE_CACHE_SCHEMA.json` 定义的对象 | 混合 | 未来 slow loop | 否 | 合法 refresh trigger | 缺失、过期、失效或 UNKNOWN 均不可执行；不得用旧 candidate 填补 |
| `policy_state` | 枚举、episode/question/commit ID、`CommitReentryCause` 和计时器 | 混合 | 未来状态机 | 否 | 事件或 fast tick | 状态损坏时进入 reason-coded `FALLBACK` |

## 3. 解释记录

每个 `z_k` 必须包含以下六个槽位；未使用的槽位显式写成 `null`。

| 槽位 | 类型 | 单位或词表 | v0 来源 | 校验方式 |
|---|---|---|---|---|
| `maneuver` | 枚举 | `LEFT`、`RIGHT`、`STRAIGHT`、`FOLLOW`、`STOP`、`PARK`、`BYPASS_LEFT`、`BYPASS_RIGHT`、`null` | oracle/人工 | 必须映射到已支持的受约束 HLC 或明确声明的 adapter 能力 |
| `lane` | 对象 | `{road_id,lane_id,relative_lane}` | oracle/人工 + CARLA map | 道路和车道必须存在且可达 |
| `target_object` | 对象 | 稳定 actor/landmark ID 及属性 | oracle/人工 + world snapshot | 对象必须存在、唯一落地且未过期 |
| `timing` | 对象 | 事件枚举和/或秒 | oracle/人工 | 非负且与场景时钟一致 |
| `distance` | 数值或区间 | m | oracle/人工 | 有限、非负且符合地图/可见性 |
| `speed` | 数值或区间 | m/s | oracle/人工 | 非负，并接受物理和规则校验；不自动等于安全 |

两个候选只有在规范化槽位至少一项不同，并且差异能够改变目标/拓扑、安全等级、严重规则、可恢复性或允许计划集合时，才算不同解释。纯同义改写必须在分支前合并。

## 4. 与现有 SimLingo 的映射

| 现有元素 | 当前位置或签名 | v0 映射 | 限制 |
|---|---|---|---|
| Fast 状态更新 | 当前无独立已验证接口 | 每个 CARLA tick 更新 ego/risk/answer/timeout/validity 并立即输出一个控制 | 未来必须与 K 次模型前向解耦；当前只定义合同 |
| 候选共享预处理 | `LingoAgent.tick(input_data)` 的现有基础 | 每次合法 slow-loop refresh 只生成一次共享 `o_t` | 会修改 route planner、UKF 和 history，不能按候选重复执行，也不能每 tick 无条件触发 K 次前向 |
| 模型调用 | `self.model(model_input)` | `f_theta(o_t,z_k)` | 当前只验证了受约束 HLC prompt |
| Route 输出 | `pred_route [1,20,2]` | `tau_t^k [20,2]` | 解释指标前必须记录并验证坐标系和 horizon |
| Speed 输出 | `pred_speed_wps [1,10,2]` | `v_t^k [10,2]` | 是 PID 使用的二维 waypoint 表示，不是 10 个标量速度 |
| PID | `control_pid(pred_route,gt_velocity,pred_speed_wps)` | 只用于 ACT 选中的计划 | gate 中 baseline PID 一次，alternative PID 为零 |
| HLC prompt | 受约束命令码 1–6 | `PromptAdapter(z_k)` 的目标接口 | 不证明支持任意自由文本 |
| 冒烟结果中的传感器 | camera、IMU、GNSS、speedometer | 部分 observation/ego state | 没有 actor、交通灯或车道边界传感合同 |

## 5. 后果字段可用性代码

- `NOW_DIRECT`：当前证据中已经直接存在，例如分支延迟和候选数组。
- `NOW_DERIVABLE_WITH_METADATA`：补充并验证坐标系、单位或 horizon 后才可计算。
- `NEW_CARLA_QUERY`：需要新增 CARLA world/map 查询和同步日志。
- `NEW_PREDICTOR`：需要明确的前向预测模型；rollout 后真值不能冒充在线预测。
- `ORACLE_PILOT`：由第一轮 pilot 的场景标注提供。
- `UNAVAILABLE_V0`：必须保持 `null/UNKNOWN`，禁止编造。

所有硬校验使用 `{true,false,UNKNOWN}` 三值逻辑。安全、严重规则、语义有效性、计划有效性、cache validity 和 ASK/holding 时序中，`UNKNOWN` 都保持 UNKNOWN 并按 non-feasible 处理。它不得转换成 `false` 或 `UNSAFE`，不得授权 ACT、ASK 或 HOLDING，必须输出保留来源的 reason-coded fallback。描述性结构指标缺失时保持 null，不改变硬等级。

## 6. 时序核算

所有阶段使用单调 wall clock，不能用 CARLA 仿真时间推测耗时。

`T_clarify = T_branch + T_question + T_response + T_replan + T_margin`。

- `T_branch`：K 次顺序前向的在线实测总和；实现前使用证据默认值 `1.25 s`。
- `T_question`、`T_response`、`T_replan`、`T_margin`：`TBD_REQUIRED`；未配置或未测量时不能判定 ASK 可行。
- `clarification_slack_s = T_decision - T_clarify`；任一项无效时为 `UNKNOWN`。
- `AskTimingStatus=FEASIBLE`：所有必要项已知、有效、未过期、来源可信，且严格满足 `T_decision > T_clarify`。
- `AskTimingStatus=INFEASIBLE_KNOWN`：所有必要项已知、有效、未过期、来源可信，且 `T_decision <= T_clarify`；只有此状态允许检查 holding 能否延长窗口或保留选项。
- `AskTimingStatus=UNKNOWN`：任一必要项缺失、过期、无效或来源不可信；不得进入 ASK 或普通 pre-question WAIT，直接输出对应 reason-coded fallback。
- `T_decision == T_clarify` 属于 `INFEASIBLE_KNOWN`，不是 ASK 可行。

## 7. Candidate Cache 与重入枚举

`plan_age_s = current_monotonic_time - generated_at_monotonic_s`。

Candidate Cache 只有同时满足以下条件才可执行：

```text
current_monotonic_time < valid_until_monotonic_s
and validity_status == VALID
and episode_id == active_episode_id
and source_observation_id 可信且未过期
```

`validity_status` 为 `EXPIRED/INVALIDATED/UNKNOWN` 或时间恰好等于 `valid_until` 时均不可执行。真实有效期为 `TBD_REQUIRED`；offline 数值只能来自标记为 `SYNTHETIC_TEST_ONLY` 的配置。

`CommitReentryCause` 只有 `NEW_INSTRUCTION`、`NEW_AMBIGUITY`、`MATERIAL_INVALIDATION` 可以创建新 episode 并清空 cache。`NONE` 覆盖 late answer、cosmetic paraphrase、ordinary scene update 等非实质事件，不得恢复询问预算。

## 8. 结构差异指标

只有在坐标系和点对应关系验证后，才允许计算：

- path ADE：20 个对应 route waypoint 的平均欧氏距离；
- path FDE：最后一对 route waypoint 的欧氏距离；
- 最大横向分歧：声明参考走廊中的最大横向距离；
- 分歧起点：首次超过 `TBD` 结构阈值的 horizon 索引或时间；
- speed difference：从 speed waypoint 表示中按已验证转换得到的 speed proxy 差异；
- stopping-distance difference：在明确动力学假设下计算的停车距离差异。

这些量只描述候选分支结构，任何一个都不能单独充当碰撞、合法性或安全标签。
