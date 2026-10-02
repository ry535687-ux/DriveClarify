# 算法 1——DriveClarify v0.1 双速率有状态闭环策略

状态：论文级伪代码，不是可执行实现。
修订目的：fast control loop 不等待 K 次 VLA forward；slow clarification loop 只按事件运行；UNKNOWN timing 不得进入 ACT、ASK 或 HOLDING。

## 1. 输入、枚举与持久状态

```text
Fast loop 每 tick 输入：
  latest_ego_state_t
  actor_rule_validity_summary_t（允许字段为 UNKNOWN）
  answer_events_t、instruction_events_t、ambiguity_events_t
  material_invalidation_events_t、ordinary_scene_events_t
  timeout_deadline_t
  slow_loop_result_events_t
  monotonic_time_t、sim_time_t

Slow loop 事件输入：
  trigger ∈ {
    EPISODE_OPEN,
    CANDIDATE_EXPIRED,
    MATERIAL_SCENE_CHANGE,
    TIMELY_ANSWER_RECEIVED,
    CANDIDATE_INVALIDATED,
    EXPLICIT_REFRESH_REQUEST
  }
  trigger provenance、input_data、world_map_snapshot、时间戳

AskTimingStatus ∈ {FEASIBLE, INFEASIBLE_KNOWN, UNKNOWN}

CommitReentryCause ∈ {
  NEW_INSTRUCTION,
  NEW_AMBIGUITY,
  MATERIAL_INVALIDATION,
  NONE
}

持久状态：
  theta                    同一个已加载 SimLingo 模型实例（未来 live）
  X                        七状态交互记录
  H                        只追加历史
  C                        Candidate Cache 或 NONE
  P_commit                 已提交计划及其 validity，或 NONE
  H_active                 当前 holding 及其 validity，或 NONE
  R_slow                   最近 slow-loop recommendation，或 NONE
  Q_budget_default = 1
  T_WAIT_MAX = TBD_REQUIRED
  wall_clock_budget_fast   TBD_REQUIRED
  wall_clock_budget_slow   TBD_REQUIRED
```

Candidate Cache 必须符合 `CANDIDATE_CACHE_SCHEMA.json`。每条 consequence record 必须通过 `cache_id` 关联 C，并共享 episode、observation 和 candidate ID。

## 2. Fast Control Loop：每 tick 唯一控制路径

```text
过程 FAST_CONTROL_TICK(inputs_t)：

  fast_start ← 读取单调时钟()
  断言 X.state 属于 {NORMAL, AMBIGUITY_ACTIVE, QUESTION_SENT,
                     HOLDING, ANSWER_RECEIVED, COMMITTED, FALLBACK}

  # A. 事件绑定与 COMMITTED 重入原因。
  new_instruction ← 最新且未处理的有效新 instruction ID
  new_ambiguity ← 最新且带 detector/provenance/reason_code 的独立歧义事件
  material_invalidation ← 最新且带具体失效原因的计划/解释失效事件

  reentry_cause ← DERIVE_COMMIT_REENTRY_CAUSE(
                    new_instruction,
                    new_ambiguity,
                    material_invalidation,
                    answer_events_t,
                    ordinary_scene_events_t)

  如果 X.state = COMMITTED 且 reentry_cause ∈ {
       NEW_INSTRUCTION, NEW_AMBIGUITY, MATERIAL_INVALIDATION}：
      记录重入 provenance 和 reason code
      X ← 开启新 episode(budget=Q_budget_default,
                         cause=reentry_cause)
      C ← NONE
      P_commit ← NONE
      R_slow ← NONE
      X.state ← AMBIGUITY_ACTIVE
      发出 slow trigger EPISODE_OPEN
  否则如果 X.state = COMMITTED 且 reentry_cause = NONE：
      # late answer、cosmetic paraphrase、ordinary update 均不能重入。
      不创建 episode；不清除 C；不恢复预算
  结束如果

  # B. 回答先绑定 episode/question；迟到或旧回答只记录。
  matching_answer ← 匹配及时回答(answer_events_t,
                                   X.episode_id,
                                   X.question_id)
  记录并忽略所有迟到、旧 episode 或未匹配回答

  如果 matching_answer 存在：
      如果 回答过期或候选引用已过期：
          记录("STALE_ANSWER", matching_answer, H)
      否则如果 回答被分类为新指令：
          # 这是 NEW_INSTRUCTION，而不是旧 episode 的继续。
          X ← 开启新 episode(matching_answer.instruction,
                             budget=Q_budget_default,
                             cause=NEW_INSTRUCTION)
          C ← NONE；P_commit ← NONE；R_slow ← NONE
          X.state ← AMBIGUITY_ACTIVE
          发出 slow trigger EPISODE_OPEN
      否则：
          X.state ← ANSWER_RECEIVED
          发出 slow trigger TIMELY_ANSWER_RECEIVED，携带回答绑定
      结束如果
  结束如果

  # C. 更新 cache age 和生命周期；绝不把 UNKNOWN 改名为 EXPIRED。
  如果 C 存在：
      C.plan_age_s ← monotonic_time_t - C.generated_at_monotonic_s

      如果 monotonic_time_t ≥ C.valid_until_monotonic_s 且
         C.validity_status = VALID：
          C.validity_status ← EXPIRED
          C.refresh_requested ← true
          发出 slow trigger CANDIDATE_EXPIRED
      结束如果

      如果 material scene change 已由独立 detector 确认：
          C.refresh_requested ← true
          发出 slow trigger MATERIAL_SCENE_CHANGE
      结束如果
  结束如果

  # D. 原子接收 slow-loop 结果，但 fast loop 重新检查所有版本和有效期。
  result ← 最新完成的 slow_loop_result_events_t
  如果 result 存在：
      如果 result.episode_id ≠ X.episode_id：
          记录("STALE_SLOW_RESULT", result, H)
      否则如果 CACHE_LINKS_VALID(result.cache, result.consequences) = false：
          返回 EMIT_ONE_FALLBACK_CONTROL("CACHE_CONSEQUENCE_ID_MISMATCH")
      否则：
          C ← result.cache
          R_slow ← result.recommendation
      结束如果
  结束如果

  # E. Fast-loop 硬 UNKNOWN gate。
  unknown_reason ← FIRST_REQUIRED_UNKNOWN_REASON(
                     actor_rule_validity_summary_t,
                     timeout_deadline_t,
                     active control validity,
                     C 与 R_slow 中本 tick 必需字段)

  如果 unknown_reason 存在：
      # 保持 UNKNOWN；不改写成 UNSAFE 或 false。
      返回 EMIT_ONE_FALLBACK_CONTROL(unknown_reason)
  结束如果

  # F. 如果有已完成 recommendation，只有 cache 可执行时才能消费。
  如果 R_slow 存在：
      如果 CACHE_EXECUTABLE(C, monotonic_time_t, X.episode_id) = false：
          如果 C.validity_status = UNKNOWN：
              返回 EMIT_ONE_FALLBACK_CONTROL("UNKNOWN_CACHE_VALIDITY")
          如果 C.validity_status = EXPIRED：
              返回 EMIT_ONE_FALLBACK_CONTROL("CACHE_EXPIRED")
          如果 C.validity_status = INVALIDATED：
              返回 EMIT_ONE_FALLBACK_CONTROL("CACHE_INVALIDATED")
          返回 EMIT_ONE_FALLBACK_CONTROL("CACHE_NOT_EXECUTABLE")
      结束如果

      如果 R_slow.semantic_decision = FALLBACK：
          返回 EMIT_ONE_FALLBACK_CONTROL(R_slow.reason_code)

      如果 R_slow.semantic_decision = ACT：
          断言 R_slow.ask_timing_status ≠ UNKNOWN
          断言 selected plan 的安全、规则、timing、validity 全部已知可行
          P_commit ← 选定计划及其有效期
          X ← 提交(X, R_slow.candidate_id, C.cache_id)
          返回 EMIT_ONE_CONTROL(跟踪 P_commit)

      如果 R_slow.semantic_decision = ASK：
          断言 R_slow.ask_timing_status = FEASIBLE
          断言 holding 的安全、规则、deadline、validity 全部已知可行
          断言 X.query_budget_remaining > 0 且当前无未决问题
          ack ← 发送唯一模板问题(R_slow.question)
          如果 ack.success：
              X ← 记录问题并扣减预算(X, ack)
              X.state ← QUESTION_SENT
              H_active ← R_slow.holding
              返回 EMIT_ONE_CONTROL(执行 H_active)
          否则：
              记录("QUESTION_DISPATCH_FAILED", ack, H)
              返回 EMIT_ONE_FALLBACK_CONTROL("QUESTION_DISPATCH_FAILED")
          结束如果

      如果 R_slow.semantic_decision = WAIT：
          断言 R_slow.ask_timing_status = INFEASIBLE_KNOWN
                或 当前已有未决问题
          断言 holding 的安全、规则、deadline、validity 全部已知可行
          H_active ← R_slow.holding
          X.state ← HOLDING
          返回 EMIT_ONE_CONTROL(执行 H_active)
      结束如果
  结束如果

  # G. Slow loop 尚在运行时，不等待它。
  如果 P_commit 存在且 CONTROL_EXECUTABLE(P_commit, monotonic_time_t)：
      返回 EMIT_ONE_CONTROL(跟踪 P_commit)
  结束如果

  如果 H_active 存在且 CONTROL_EXECUTABLE(H_active, monotonic_time_t)：
      X.state ← HOLDING
      返回 EMIT_ONE_CONTROL(执行 H_active)
  结束如果

  # 没有有效旧控制，slow compute 不能成为等待理由。
  返回 EMIT_ONE_FALLBACK_CONTROL("NO_VALID_CONTROL_WHILE_REFRESHING")
结束过程
```

`EMIT_ONE_CONTROL` 和 `EMIT_ONE_FALLBACK_CONTROL` 都必须保证本 tick 恰好输出一个车辆控制。后者保存 reason code；它不等于默认急停，具体 controller 仍待未来安全合同定义。

## 3. Slow / Event-Driven Clarification Loop

```text
过程 SLOW_CLARIFICATION_REFRESH(trigger, input_data,
                                world_map_snapshot,
                                trigger_time)：

  slow_start ← 读取单调时钟()

  如果 trigger.type 不属于合法 slow triggers：
      返回 拒绝结果("ILLEGAL_REFRESH_TRIGGER")
  结束如果

  如果 trigger.episode_id ≠ X.episode_id：
      返回 拒绝结果("STALE_REFRESH_TRIGGER")
  结束如果

  cache_id ← 新建唯一 cache ID
  refresh_reason ← MAP_TRIGGER_TO_REFRESH_REASON(trigger)

  # A. 每次 refresh 只冻结一次共享观测。
  尝试：
      (tick_data, driving_input_base, observation_id) ←
          单次共享预处理(input_data, trigger.sim_time)
      o ← 冻结不可变观测(driving_input_base, observation_id)
      e ← 构造 ego 状态(tick_data, world_map_snapshot)
  捕获异常：
      返回 slow fallback recommendation("PREPROCESSING_FAILED")
  结束尝试

  # B. 读取或按及时回答重建严格 K=2 的 oracle 解释。
  如果 trigger.type = TIMELY_ANSWER_RECEIVED：
      Z_raw ← 从回答绑定重新构造解释；禁止给旧计划换标签
  否则：
      Z_raw ← 读取当前 episode 的 oracle 解释
  结束如果

  Z_valid ← 按 schema、非同义、对象、地图和受约束 HLC 顺序校验 Z_raw

  如果 |Z_valid| = 0：
      返回 slow fallback recommendation("NO_VALID_INTERPRETATION")
  结束如果

  # C. 同一模型实例顺序执行 K 次前向；slow loop 不输出车辆控制。
  Plans ← 空映射
  branch_start ← 读取单调时钟()

  对 Z_valid 中每个 z，按 candidate ID 确定顺序：
      如果 当前时钟 - slow_start ≥ wall_clock_budget_slow：
          记录候选删除(z.id, "SLOW_WALL_CLOCK_OVERRUN", H)
          继续
      结束如果

      condition ← 适配到受约束 HLC(z)
      model_input ← 只替换条件(o, condition)

      尝试：
          在 inference mode 下：
              (speed_wps, route, language) ← theta(model_input)
          p ← detach/copy(route, speed_wps,
                          expected_shapes=([1,20,2],[1,10,2]))
          删除 GPU 输出引用
      捕获异常：
          记录候选删除(z.id, "MODEL_FORWARD_FAILED", H)
          继续
      结束尝试

      如果 p 的 observation ID、shape 或有限值校验失败：
          记录候选删除(z.id, "PLAN_INVALID", H)
          继续
      结束如果

      Plans[z.id] ← p
  结束循环

  T_branch ← 当前单调时钟 - branch_start
  断言 slow loop 的 PID 调用次数 = 0

  如果 |Plans| = 0：
      返回 slow fallback recommendation("NO_VALID_PLAN")
  结束如果

  # D. 分项 consequences 和 cache 关联。
  Consequences ← 空映射
  对 Plans 中每个 (z_id, p)：
      c ← 提取分项后果(p, Z_valid[z_id], e, world_map_snapshot,
                        missing_policy="显式三值")
      c.schema_version ← "driveclarify.consequence.v0.1"
      c.cache_id ← cache_id
      c.episode_id ← X.episode_id
      c.observation_id ← observation_id
      c.candidate_id ← z_id
      c.timing.branch_forward_latency_s ← T_branch
      Consequences[z_id] ← c
  结束循环

  # E. 构造 Candidate Cache；真实 valid_until 仍为 TBD_REQUIRED。
  C_new ← {
    schema_version: "driveclarify.candidate_cache.v0.1",
    cache_id: cache_id,
    episode_id: X.episode_id,
    source_observation_id: observation_id,
    generated_at_monotonic_s: 当前单调时钟,
    generated_at_sim_time_s: trigger.sim_time,
    valid_until_monotonic_s: 从可信配置/estimator 取得，
    plan_age_s: 0,
    refresh_reason: refresh_reason,
    refresh_requested: false,
    candidate_ids: Plans 的有序 ID,
    validity_status: VALID 或 UNKNOWN
  }

  如果 C_new.valid_until 缺失、无效或来源不可信：
      C_new.validity_status ← UNKNOWN
      返回 (C_new, Consequences,
            fallback recommendation("UNKNOWN_CACHE_VALIDITY"))
  结束如果

  如果 CACHE_LINKS_VALID(C_new, Consequences) = false：
      C_new.validity_status ← INVALIDATED
      返回 (C_new, Consequences,
            fallback recommendation("CACHE_CONSEQUENCE_ID_MISMATCH"))
  结束如果

  # F. Required safety/rule UNKNOWN 直接 fallback，不参与动作级联。
  unknown_hard_reason ← FIRST_REQUIRED_UNKNOWN_REASON(Consequences)
  如果 unknown_hard_reason 存在：
      返回 (C_new, Consequences,
            fallback recommendation(unknown_hard_reason))
  结束如果

  HardFeasible ← 已知安全且无严重规则冲突的候选
  如果 |HardFeasible| = 0：
      返回 (C_new, Consequences,
            fallback recommendation("NO_HARD_FEASIBLE_CANDIDATE"))
  结束如果

  # G. 三值 timing；UNKNOWN 不转换成 false。
  timing_result ← COMPUTE_ASK_TIMING_STATUS(
                    T_decision,
                    T_branch,
                    T_question,
                    T_response,
                    T_replan,
                    T_margin,
                    validity/provenance/freshness)

  对 Consequences 中每个 c：
      c.timing.ask_timing_status ← timing_result.status
      c.timing.ask_timing_reason_code ← timing_result.reason_code
      c.timing.clarification_slack_s ← timing_result.slack 或 null
  结束循环

  如果 timing_result.status = UNKNOWN：
      返回 (C_new, Consequences,
            fallback recommendation(timing_result.reason_code))
  结束如果

  # H. 后果比较；结构 route/speed 指标只作诊断。
  D ← 比较后果(Consequences 限于 HardFeasible)
  critical ← 三值或(D_G, D_S, D_R, D_I)

  如果 critical = UNKNOWN：
      返回 (C_new, Consequences,
            fallback recommendation("UNKNOWN_CRITICAL_CONSEQUENCE"))
  结束如果

  如果 |HardFeasible| = 1 或 critical = FALSE：
      z_star ← 唯一候选或词典序选择
      返回 (C_new, Consequences,
            ACT recommendation(z_star,
                               timing_result.status,
                               reason="ROBUST_OR_EQUIVALENT_ACT"))
  结束如果

  # I. Holding 的所有硬字段、deadline 和 validity 必须已知。
  HoldingSet ← 枚举 H1–H4 的 fixture/live summaries
  holding_unknown_reason ← FIRST_HOLDING_UNKNOWN_REASON(HoldingSet)
  如果 holding_unknown_reason 存在：
      返回 (C_new, Consequences,
            fallback recommendation(holding_unknown_reason))
  结束如果

  h_star ← 在硬可行 holding 中按 safety → rule → option preservation
           → irreversibility → comfort → progress loss 选择

  如果 timing_result.status = FEASIBLE 且
     h_star 存在 且 query_budget > 0 且
     Relevant/NonRedundant/Answerable 全部为 true 且
     当前没有未决问题：
      q ← 生成固定模板问题
      返回 (C_new, Consequences,
            ASK recommendation(q, h_star,
                               timing_status=FEASIBLE,
                               reason="CRITICAL_ASK_TIMELY"))
  结束如果

  如果 timing_result.status = INFEASIBLE_KNOWN 且
     h_star 存在 且
     HoldCanExtendWindowOrPreserveOptions(h_star) = true：
      返回 (C_new, Consequences,
            WAIT recommendation(h_star,
                                timing_status=INFEASIBLE_KNOWN,
                                reason="ASK_TOO_LATE_HOLD_CAN_PRESERVE"))
  结束如果

  robust ← 查找对所有仍可能解释都已知硬可行的候选
  如果 robust 存在：
      返回 (C_new, Consequences,
            ACT recommendation(robust,
                               timing_result.status,
                               reason="ROBUST_FALLBACK_ACT"))
  结束如果

  返回 (C_new, Consequences,
        fallback recommendation("ASK_WAIT_AND_ROBUST_ACT_UNAVAILABLE"))
结束过程
```

## 4. 三值时序纯函数

```text
函数 COMPUTE_ASK_TIMING_STATUS(T_decision, T_branch, T_question,
                               T_response, T_replan, T_margin,
                               metadata)：

  如果任一值无效（负数、NaN、类型错误）：
      返回 {UNKNOWN, "TIMING_INVALID", slack=null}
  如果任一来源不可信：
      返回 {UNKNOWN, "TIMING_SOURCE_UNTRUSTED", slack=null}
  如果任一记录过期：
      返回 {UNKNOWN, "TIMING_STALE", slack=null}

  按固定顺序检查缺失：
      T_decision → "UNKNOWN_DECISION_DEADLINE"
      T_branch   → "UNKNOWN_BRANCH_LATENCY"
      T_question → "UNKNOWN_QUESTION_LATENCY"
      T_response → "UNKNOWN_RESPONSE_LATENCY"
      T_replan   → "UNKNOWN_REPLAN_LATENCY"
      T_margin   → "UNKNOWN_SAFETY_MARGIN"
  任一缺失立即返回 {UNKNOWN, 对应 reason, slack=null}

  T_clarify ← T_branch + T_question + T_response + T_replan + T_margin
  slack ← T_decision - T_clarify

  如果 T_decision > T_clarify：
      返回 {FEASIBLE, "TIMING_FEASIBLE", slack}
  否则：
      # 包含 equality。
      返回 {INFEASIBLE_KNOWN, "ASK_TOO_LATE_OR_EQUAL", slack}
结束函数
```

## 5. COMMITTED 重入纯函数

```text
函数 DERIVE_COMMIT_REENTRY_CAUSE(new_instruction,
                                 new_ambiguity,
                                 material_invalidation,
                                 answer_events,
                                 ordinary_scene_events)：
  如果 new_instruction 有新 ID 且有效：返回 NEW_INSTRUCTION
  如果 new_ambiguity 带独立 detector/provenance/reason：返回 NEW_AMBIGUITY
  如果 material_invalidation 带具体失效原因：返回 MATERIAL_INVALIDATION

  # 以下均不能重新打开 episode。
  记录 late answer、cosmetic paraphrase、ordinary scene update、重复事件
  返回 NONE
结束函数
```

## 6. 必须检查的不变量

1. Fast loop 每 CARLA tick 最多消费一个 semantic recommendation，并恰好输出一个 vehicle control。
2. Fast loop 不等待 slow loop，不运行 K 次 forward，不每 tick 无条件触发 branch planner。
3. Slow loop 只响应六种合法 trigger；每次 refresh 共享预处理一次，同一模型实例顺序 forward K 次，PID 零次。
4. `plan_age_s = now - generated_at_monotonic_s`；只有 `now < valid_until` 且 `validity_status=VALID` 的 cache 可执行。
5. consequence 的 cache/episode/observation/candidate ID 必须一致。
6. `AskTimingStatus` 始终保留三值；equality 为 `INFEASIBLE_KNOWN`。
7. required safety/rule/timing 或 holding deadline/validity 为 UNKNOWN 时，不得 ACT、ASK 或 HOLDING，必须 reason-coded fallback。
8. 只有 `INFEASIBLE_KNOWN` 可以检查提问前 WAIT；UNKNOWN 永远不能进入普通 WAIT。
9. ASK 必须同时包含成功 question dispatch 和已知可行 holding；WAIT 不发送问题。
10. 每 episode 最多成功发送一个语义问题；仍然歧义不得二次提问。
11. 回答必须绑定 episode/question；及时回答触发 slow refresh，禁止给旧张量换标签。
12. 只有三种 `CommitReentryCause` 可创建新 episode；`NONE` 不清 cache、不恢复预算。
13. consequence 各维度独立保存；不得恢复单一加权分数。
14. route L2/ADE/FDE 不得直接改变 safety class。
15. 任一断言失败都保留确定性 reason code；禁止编造 CARLA 缺失数据。
