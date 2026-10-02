# DriveClarify v0.1 双速率设计增量

状态：规范性设计合同，尚未实现。
适用关系：本文件修订 `DriveClarify_v0_Algorithm_Specification.md` 和 `ALGORITHM_1_PSEUDOCODE.md` 中“每个控制 tick 运行 K 次候选前向”的旧调度描述；其他未冲突条款继续有效。
阶段边界：本阶段只定义合同，不连接 SimLingo/CARLA/GPU，不实现线程、队列、控制器或 evaluator。

## 1. 修订结论

DriveClarify 的未来 live 系统必须分为：

1. **Fast Control Loop**：每个 CARLA tick 运行，必须及时输出一个且仅一个车辆控制；
2. **Slow / Event-Driven Clarification Loop**：只因规定事件触发，冻结一次观测后执行 K 次同实例顺序前向并更新候选缓存。

两个循环之间只通过带版本的事件、semantic recommendation 和 `candidate_cache` 交换状态。Slow loop 运行时，fast loop 继续执行上一个仍有效的 committed plan 或 holding；不得等待 K 次 forward 才输出控制。

## 2. Fast Control Loop 合同

### 2.1 运行频率与输入

每个 CARLA tick 运行。输入为：

- latest ego state；
- actor/rule validity summary；
- current committed plan 或 holding policy；
- answer events；
- timeout/deadline；
- candidate cache metadata；
- slow-loop semantic recommendation（如果存在且仍有效）。

### 2.2 职责

1. 读取并校验单调时钟、episode、control 和 cache 版本；
2. 检查 answer arrival、deadline、timeout、plan/holding validity；
3. 只执行一个已经验证且未过期的 committed plan 或 holding control；
4. 若新 recommendation 的 cache、episode 和有效期均匹配，则由 reducer 原子采纳；
5. 在需要时发出 slow-loop refresh request；
6. 任一必要控制、安全、规则、timing 或 cache validity 为 UNKNOWN 时，保持 UNKNOWN 并进入 reason-coded safety fallback；
7. 每 tick 向车辆发出一个且仅一个 control。

### 2.3 明确禁止

- 不得等待 K 次 VLA forward 后才输出控制；
- 不得每 tick 无条件重跑 branch planner；
- 不得执行 `validity_status != VALID` 或 `current_time >= valid_until_monotonic_s` 的 candidate cache；
- 不得让 late answer、cosmetic paraphrase 或 ordinary scene update 创建新 episode；
- 不得在没有有效 committed/holding control 时用旧候选填补控制空缺。

## 3. Slow / Event-Driven Clarification Loop 合同

### 3.1 唯一合法触发器

```text
EPISODE_OPEN
CANDIDATE_EXPIRED
MATERIAL_SCENE_CHANGE
TIMELY_ANSWER_RECEIVED
CANDIDATE_INVALIDATED
EXPLICIT_REFRESH_REQUEST
```

触发事件必须带 `episode_id`、单调时间、来源、provenance 和 reason code。没有上述触发器时 slow loop 不运行。

### 3.2 职责

1. 校验 trigger 与当前 episode；
2. 冻结一份不可变观测，只推进一次共享 preprocessing；
3. 在同一已加载模型实例上按确定顺序执行 K 次 forward；
4. 在任何候选进入控制器前 detach/copy 并校验 shape、有限值和 observation identity；
5. 分项提取 consequences，禁止加权总分；
6. 更新 Candidate Cache，并写入生成时间、有效期、plan age、刷新原因和有效性；
7. 计算结构化分歧、decision deadline 与三值 `AskTimingStatus`；
8. 生成带 cache ID、episode ID、有效期和 reason code 的 semantic recommendation，交给 fast loop；
9. 不直接向车辆发 control，不调用 alternative PID。

### 3.3 Slow loop 运行期间的控制

Fast loop 必须继续使用上一个仍满足以下条件的控制：

```text
control.validity_status == VALID
and current_monotonic_time < control.valid_until_monotonic_s
and safety/rule/timing validity 均为已知可行
```

如果不存在这样的 committed plan 或 holding policy，fast loop 立即进入 reason-coded safety fallback。Slow loop 计算尚未完成不是延迟车辆控制的理由。

## 4. Candidate Cache Contract

规范记录：`CANDIDATE_CACHE_SCHEMA.json`。

```text
candidate_cache = {
  cache_id,
  episode_id,
  source_observation_id,
  generated_at_monotonic_s,
  generated_at_sim_time_s,
  valid_until_monotonic_s,
  plan_age_s,
  refresh_reason,
  refresh_requested,
  candidate_ids,
  validity_status
}
```

### 4.1 派生量和执行门

```text
plan_age_s = current_monotonic_time - generated_at_monotonic_s

CacheExecutable(C, now) iff
  C.validity_status == VALID
  and now < C.valid_until_monotonic_s
  and C.episode_id == active_episode_id
  and C.source_observation_id is trusted and not stale
```

等号边界已经过期：`now == valid_until_monotonic_s` 不可执行。真实 `valid_until` 数值仍为 `TBD_REQUIRED`；offline fixture 中的有效期必须标记 `SYNTHETIC_TEST_ONLY`。

### 4.2 Refresh reason 映射

Candidate Cache 的 `refresh_reason` 至少支持：

```text
EPISODE_OPEN
ANSWER_RECEIVED
CANDIDATE_EXPIRED
MATERIAL_SCENE_CHANGE
PLAN_INVALIDATED
MANUAL_TEST_REFRESH
```

Slow-loop trigger 到 cache reason 的规范映射：

| Slow-loop trigger | Candidate Cache refresh reason |
|---|---|
| `EPISODE_OPEN` | `EPISODE_OPEN` |
| `CANDIDATE_EXPIRED` | `CANDIDATE_EXPIRED` |
| `MATERIAL_SCENE_CHANGE` | `MATERIAL_SCENE_CHANGE` |
| `TIMELY_ANSWER_RECEIVED` | `ANSWER_RECEIVED` |
| `CANDIDATE_INVALIDATED` | `PLAN_INVALIDATED` |
| `EXPLICIT_REFRESH_REQUEST` | `MANUAL_TEST_REFRESH`（offline）或带来源的对应 live reason（未来扩展） |

`validity_status` 只能是 `VALID/EXPIRED/INVALIDATED/UNKNOWN`。只有 `VALID` 可进入执行门；`UNKNOWN` 不能转换成 `EXPIRED` 或 `INVALIDATED`，而是以 `UNKNOWN_CACHE_VALIDITY` fallback 保留来源不确定性。

### 4.3 Consequence 关联

每条 consequence record 必须同时保存 `cache_id`、`episode_id`、`observation_id` 和 `candidate_id`。其中：

- `cache_id` 必须引用唯一 Candidate Cache；
- `observation_id == candidate_cache.source_observation_id`；
- `candidate_id` 必须属于 `candidate_cache.candidate_ids`；
- 任一关联不一致时记录 `CACHE_CONSEQUENCE_ID_MISMATCH`，不得执行该候选。

## 5. Timing 三值合同

```text
AskTimingStatus ∈ {FEASIBLE, INFEASIBLE_KNOWN, UNKNOWN}
```

| 状态 | 进入条件 | 允许的后续处理 |
|---|---|---|
| `FEASIBLE` | 所有必要 timing 已知、有效、未过期、来源可信，且 `T_decision > T_clarify` | 可继续检查 ASK 的其他必要条件 |
| `INFEASIBLE_KNOWN` | 所有必要 timing 已知、有效、未过期、来源可信，且 `T_decision <= T_clarify` | 仅此状态可检查 `HoldCanExtendWindowOrPreserveOptions` |
| `UNKNOWN` | 任一必要 timing 缺失、过期、无效或来源不可信 | 不得 ACT、ASK 或 HOLDING；直接 reason-coded fallback |

`UNKNOWN` 不是 boolean false。至少支持以下 fallback reason：

```text
UNKNOWN_DECISION_DEADLINE
UNKNOWN_BRANCH_LATENCY
UNKNOWN_QUESTION_LATENCY
UNKNOWN_RESPONSE_LATENCY
UNKNOWN_REPLAN_LATENCY
UNKNOWN_SAFETY_MARGIN
TIMING_SOURCE_UNTRUSTED
TIMING_STALE
TIMING_INVALID
UNKNOWN_HOLDING_DEADLINE
UNKNOWN_HOLDING_VALIDITY
```

`T_decision == T_clarify` 必须归类 `INFEASIBLE_KNOWN`，不能授权 ASK。

## 6. COMMITTED 重入合同

```text
CommitReentryCause ∈ {
  NEW_INSTRUCTION,
  NEW_AMBIGUITY,
  MATERIAL_INVALIDATION,
  NONE
}
```

只有前三者可以创建新 `episode_id`、清除 Candidate Cache 并进入 `AMBIGUITY_ACTIVE`：

- `NEW_INSTRUCTION`：带新 instruction ID 的指令事件；
- `NEW_AMBIGUITY`：独立检测事件，必须带 detector/provenance/reason code；
- `MATERIAL_INVALIDATION`：已提交解释或计划的实质失效，必须带具体失效原因。

以下事件必须归类 `NONE`：late answer、旧 episode answer、cosmetic paraphrase、ordinary scene update、重复事件或没有实质影响的 metadata 变化。`NONE` 不清除 cache、不创建 episode、不恢复 query budget。

## 7. 离线 v0.1 与 live 系统的边界

下一阶段 CPU-only offline evaluator 只实现纯数据合同：cache schema、age/expiry、三值 timing、reducer guard、reason codes 和手写 fixtures。它不实现两个实时循环、并发、SimLingo forward、CARLA tick、PID 或 holding dynamics。

本文件中的 dual-rate live 架构在未来 live integration 前仍需独立实现和验证；当前设计修订不能作为实时性或安全性证据。

## 8. 可视化合同

现有 v0 架构图和状态机图只能表达语义模块与七状态，不能再被解释为“每 tick 完成 K 次 forward”。未来更新图时必须明确画出：

- fast loop 的 per-tick control 路径；
- slow loop 的 event triggers 与 K sequential forwards；
- Candidate Cache 作为两循环之间的版本化边界；
- slow loop 运行时 fast loop 的有效旧控制或 reason-coded fallback；
- `AskTimingStatus=UNKNOWN` 不流向 ASK/WAIT；
- `CommitReentryCause=NONE` 不流向新 episode。

本阶段不修改 SVG，也不声称现有图已经完成上述双速率展示。
