# DriveClarify v0.1 交互状态机

状态：规范性规则设计，尚未实现。  
`ACT(z_k)`、`ASK(q)`、`WAIT` 是语义决策；`ASK` 和 `WAIT` 都必须同时输出独立的 holding 控制 `a_t=h_j(s_t)`。

## 1. 持久状态

```text
policy_state = {
  state,                    # 当前状态
  episode_id,               # 澄清回合 ID
  instruction_id,           # 指令 ID
  candidate_cache,          # CANDIDATE_CACHE_SCHEMA.json 记录或 NONE
  valid_candidate_ids,      # 有效候选 ID
  selected_candidate_id,    # 当前选中候选
  committed_candidate_id,   # 已提交候选
  question_id,              # 问题 ID
  question_signature,       # 防重复问题签名
  question_sent_at,         # 问题发送时间
  answer_deadline,          # 回答截止时间
  wait_started_at,          # WAIT 开始时间
  query_budget_remaining,   # 剩余询问预算
  late_answer_policy,       # 迟到回答策略
  last_safe_holding_id,     # 最近可行 holding
  ask_timing_status,        # FEASIBLE / INFEASIBLE_KNOWN / UNKNOWN
  commit_reentry_cause,     # 四值重入原因
  fallback_reason           # 确定性 reason code
}
```

所有 ID 和状态转移都必须同时记录单调 wall clock、CARLA frame 和仿真时间。状态转移不得删除历史事件。

## 2. 规范状态

| 状态 | 进入条件 | 允许的语义动作 | 每 tick 更新 | 退出条件 | 超时或无效处理 |
|---|---|---|---|---|---|
| `NORMAL`（正常） | 没有活动歧义 episode；普通计划仍有效 | 普通 ACT；开启新 episode | 检测新的 oracle 歧义或计划实质失效；保持普通安全检查 | 出现歧义 → `AMBIGUITY_ACTIVE`；计划无效 → `FALLBACK` | 无澄清超时 |
| `AMBIGUITY_ACTIVE`（歧义活动） | 新 episode 含两个原始 oracle 解释；slow refresh 已请求或得到 cache | `ACT(z_k)`、`ASK(q)+HOLD`、`WAIT+HOLD` 或 fallback | fast loop 检查 cache/risk/answer/deadline；slow loop 仅按事件更新候选、后果和 recommendation | 0 候选 → fallback；1 个安全合法候选或后果等价 → committed；问题已发 → question sent；已知太晚且可安全等待 → holding | 未知硬安全/规则/timing 或 cache validity 直接 reason-coded fallback，不得 ACT/ASK/HOLDING |
| `QUESTION_SENT`（问题已发送） | 唯一问题发送成功且预算减一 | `WAIT+HOLD`；处理回答；安全覆盖 | 检查回答、holding、截止时间和过期；禁止重发相同签名 | 选定 holding → `HOLDING`；收到回答 → `ANSWER_RECEIVED`；holding/时序失效 → fallback | 发送失败不扣预算并回到歧义状态；禁止重复发送 |
| `HOLDING`（保持中） | holding 的安全、规则、deadline、validity 全部已知可行 | `WAIT+HOLD`；处理回答；安全覆盖 | fast loop 每 tick 重新检查前后风险、规则、阻塞、舒适性、option、TTD、cache/control validity 和持续时间 | 有效回答 → answer received；尚未问且 `INFEASIBLE_KNOWN` 后窗口恢复为 `FEASIBLE` → question sent；robust act → committed；超时/失效/UNKNOWN → fallback | 最大持续时间为已知的 `min(T_WAIT_MAX, answer_deadline-now, decision_deadline-now, holding_valid_until-now)`；任一项 UNKNOWN 立即 fallback |
| `ANSWER_RECEIVED`（收到回答） | 回答的 `episode_id`、`question_id` 匹配且未过期 | 本 tick 不直接改变车辆动作；仅在旧 holding 仍有效时继续 | 解析固定模板回答；触发 `TIMELY_ANSWER_RECEIVED` slow refresh，从当前观测重建 cache 并重新检查 | 新 cache 中回答候选可行 → committed；回答是新指令 → 新 episode；仍歧义/无效/不可行 → 有界 holding 或 fallback | 回答永远不能绕过 validator；旧 cache 不得只换标签 |
| `COMMITTED`（已提交） | 通过后果等价、有效回答、无需提问决策或 robust fallback 选定候选 | `ACT(committed z_k)`；安全覆盖 | fast loop 校验 plan/cache；独立计算 `CommitReentryCause` | 仅 `NEW_INSTRUCTION/NEW_AMBIGUITY/MATERIAL_INVALIDATION` → 新 ambiguity active；`NONE` → 保持 committed；不安全/UNKNOWN → fallback | late answer、cosmetic paraphrase、ordinary scene update 均为 `NONE` |
| `FALLBACK`（回退） | 无有效候选、无硬可行候选、关键数据缺失、deadline 过期、无安全 holding、状态损坏或 wall-clock 超限 | 安全覆盖或另行规定的 robust fallback ACT | 重新评估最小风险控制、合法性和恢复可能性；除非显式开启新 episode，否则不再问 | 安全恢复且状态有效 → normal 或新 ambiguity active | 不等于立即停车；controller 选择必须依赖场景 |

## 3. 状态转移

| 起点 | 条件或事件 | 终点 | 输出 |
|---|---|---|---|
| `NORMAL` | 新歧义指令含两个 oracle 候选 | `AMBIGUITY_ACTIVE` | 仅在原计划有效期内继续原控制 |
| `AMBIGUITY_ACTIVE` | `|Z_valid|=0` | `FALLBACK` | 安全覆盖或 robust fallback |
| `AMBIGUITY_ACTIVE` | cache 过期、失效、UNKNOWN 或 ID 不一致 | `FALLBACK`，并可请求 slow refresh | `CACHE_EXPIRED/CACHE_INVALIDATED/UNKNOWN_CACHE_VALIDITY/CACHE_CONSEQUENCE_ID_MISMATCH` |
| `AMBIGUITY_ACTIVE` | 必需安全、规则或 timing 为 UNKNOWN | `FALLBACK` | 保留具体 UNKNOWN reason；禁止 ACT/ASK/HOLDING |
| `AMBIGUITY_ACTIVE` | 仅一个硬安全合法候选 | `COMMITTED` | `ACT(z_k)` |
| `AMBIGUITY_ACTIVE` | 多候选后果等价且硬可行 | `COMMITTED` | `ACT(tie_break(Z))` |
| `AMBIGUITY_ACTIVE` | critical，ASK 必要条件成立，问题发送成功 | `QUESTION_SENT` | `ASK(q)` 和 holding control |
| `AMBIGUITY_ACTIVE` | `AskTimingStatus=INFEASIBLE_KNOWN`，且 holding 的安全/规则/deadline/validity 全部已知并能保留选项 | `HOLDING` | `WAIT` 和 holding control |
| `QUESTION_SENT` | 无回答且 holding 仍可行 | `HOLDING` | `WAIT` 和 holding control |
| `QUESTION_SENT` 或 `HOLDING` | 收到匹配且及时的回答 | `ANSWER_RECEIVED` | 校验期间继续 holding |
| `ANSWER_RECEIVED` | 回答选定的解释重新校验通过 | `COMMITTED` | 重新规划后 `ACT(z_k)` |
| `ANSWER_RECEIVED` | 回答修改原指令 | `AMBIGUITY_ACTIVE` | 新 episode 和候选生成，禁止给旧计划换标签 |
| `ANSWER_RECEIVED` | 回答无效、过期、仍歧义或候选不可行 | `HOLDING` 或 `FALLBACK` | 只在 holding 仍可行且未超时时继续等待 |
| `COMMITTED` | `CommitReentryCause` 为 `NEW_INSTRUCTION`、`NEW_AMBIGUITY` 或 `MATERIAL_INVALIDATION` | `AMBIGUITY_ACTIVE` | 清除 cache，创建新 `episode_id`；记录 provenance/reason；旧回答不能选择新 episode |
| `COMMITTED` | `CommitReentryCause=NONE` | `COMMITTED` | 不清 cache、不建 episode、不恢复预算 |
| 任意状态 | 硬安全覆盖、状态损坏、无有效控制或 wall-clock 超限 | `FALLBACK` | 依场景选择最小风险控制 |

## 4. 提问和防重复合同

1. v0 每个澄清 episode 最多成功发送 **一个语义问题**。
2. 同一规范化 `question_signature=(episode_id, contrasted_slot, candidate_ids)` 最多发送一次。
3. 只有在未收到发送确认时，传输重试才不算第二个语义问题；传输重试次数为 `TBD_REQUIRED`，并单独记录。
4. 无效回答不会恢复预算。只有 UI 传输失败时才能重新展示相同固定选项，不能因为回答不利而再次提问。
5. 新指令或实质性新歧义创建新 episode 并获得新预算；表面改写或旧迟到回答不能重开 episode。

## 5. WAIT 与 holding 合同

- `WAIT` 表示“本 tick 不发送新问题，并执行已验证 holding 策略”，不表示速度必须为零或冻结仿真。
- `ASK(q)` 表示“发送一次问题，并在同一 tick 执行 holding 策略”。holding 可以是 H1、H2、H3 或 H4。
- `T_WAIT_MAX` 是必需配置项，当前为 `TBD`。确定前，交互执行必须 fail closed。
- 实际 WAIT 不能超过 `T_WAIT_MAX`、回答过期、holding 有效期和 decision deadline 中最早者。
- 若原 holding 失效，可在每 tick 切换到新的安全 holding，但切换本身也必须通过校验并记录。
- 只有 `AskTimingStatus=INFEASIBLE_KNOWN` 才能进入提问前 WAIT；`UNKNOWN` 不能进入 WAIT。
- holding 的 deadline 或 validity 为 UNKNOWN 时，不得沿用 `last_safe_holding_id`，必须 reason-coded fallback。

## 6. 回答时间与有效性

只有以下条件全部成立时才采用回答：

1. `episode_id` 和 `question_id` 与活动 episode 匹配；
2. 到达时间早于 `answer_deadline`，且引用的解释尚未过期；
3. 能唯一映射到固定选项，或明确被分类为新指令；
4. 解释通过语义、对象、地图和受约束 HLC 校验；
5. 基于当前状态新生成的计划通过有效性与硬安全/规则检查。

迟到回答处理：

- 在 `COMMITTED`、episode 关闭或过期后到达：记录 `LATE_IGNORED`，不改变控制；
- 过期前在 `HOLDING` 状态到达：正常处理；
- 旧解释已经不可行：禁止强制执行，只能按新指令重生成、在 holding 可行时继续等待，或进入 fallback。

v0 运行时无法从乘客真实意图判断“回答错误”。oracle pilot 可以在评测日志中标记 wrong answer，但运行策略只把它当作乘客声明的选项，同时继续执行全部安全和地图 validator。

## 7. 提交后的重新进入

`CommitReentryCause ∈ {NEW_INSTRUCTION, NEW_AMBIGUITY, MATERIAL_INVALIDATION, NONE}`。

`COMMITTED` 不是全局吸收态，但只有前三种原因能重新进入 clarification；必须清除当前 cache、创建新 `episode_id`、从当前观测请求 slow refresh，并且绝不消费旧 episode 的迟到回答。

- `NEW_AMBIGUITY` 必须是独立检测事件，包含 detector、provenance 和 reason code；
- `MATERIAL_INVALIDATION` 必须给出已提交解释或计划的具体失效原因；
- late answer、cosmetic paraphrase、ordinary scene update、重复事件均映射为 `NONE`；
- `NONE` 不清除 cache、不重置预算、不创建 episode。

## 8. 双速率 reducer 边界

Fast control loop 每 tick 只消费已验证的 control、事件、cache metadata 和 semantic recommendation，并输出一个 control。Slow loop 只在 `EPISODE_OPEN/CANDIDATE_EXPIRED/MATERIAL_SCENE_CHANGE/TIMELY_ANSWER_RECEIVED/CANDIDATE_INVALIDATED/EXPLICIT_REFRESH_REQUEST` 上更新 cache。

Slow loop 运行中 fast loop 继续执行仍有效的 committed plan 或 holding。没有仍有效控制时进入 reason-coded safety fallback，禁止等待 K 次 forward。
