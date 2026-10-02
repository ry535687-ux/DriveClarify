# DriveClarify STEP3A CPU 开发原型

默认关闭。仅检验给定候选与合法任务证据下的选择性澄清决策和离线分支事件逻辑。这里没有 CARLA、模型推理、原生路线安装或车辆控制接口。

从仓库根目录运行实际交付代码：

```bash
python -B -m unittest discover -s experiments/driveclarify_three_policy_dev/tests -v
```

运行明确标为合成的询问样例（`--receipts` 使用一个新的目录）：

```bash
python -B -m experiments.driveclarify_three_policy_dev --enable-cpu-dev policy \
  --public experiments/driveclarify_three_policy_dev/configs/SYNTHETIC.public.json \
  --states experiments/driveclarify_three_policy_dev/configs/SYNTHETIC.states.json \
  --policy DRIVECLARIFY_CONTROLLED \
  --receipts /tmp/driveclarify_step3_new_questions \
  --answer-file experiments/driveclarify_three_policy_dev/configs/SYNTHETIC.answer.json
```

独立评价命令：

```bash
python -B -m experiments.driveclarify_three_policy_dev --enable-cpu-dev evaluate \
  --contract experiments/driveclarify_three_policy_dev/configs/SYNTHETIC.contract.json \
  --trace experiments/driveclarify_three_policy_dev/configs/SYNTHETIC.trace.json \
  --truth experiments/driveclarify_three_policy_dev/configs/SYNTHETIC.truth.json \
  --coverage experiments/driveclarify_three_policy_dev/configs/SYNTHETIC.coverage.json \
  --safety experiments/driveclarify_three_policy_dev/configs/SYNTHETIC.safety.json
```

`policy.py` 正常导入现有 `driveclarify_rq1_v2.consequence.compare_task_signatures`，不复制 AST 或导入离线 revised 判断器。必要任务字段缺失优先于已有差异；输入路线远近、参照 ID 均不能补足任务依据。两个候选及其两个单元素答案构成完整分区时，任务分歧才给完整方法提供回答区分价值。共同询问门没有分歧条件，等价任务上立即询问仍可询问。

公共 JSON 与答案文件分离，`NO_CLARIFICATION` 连答案文件也不打开。实际问题经独占创建、flush/fsync 并记录摘要后，受控服务才读答案文件。文件缺失、I/O、JSON/编码和答案格式故障会保存 `<query_id>.answer_failure.json`，保留已发问题及次数，CLI 输出结构化结果并返回 3；不重问、不造答案、不发路线请求。若持久化介质本身也失败，结构化输出明确 `persistence_error`，不声称落盘成功。回答绑定该问题回执；共同 CPU 后端要求回答之后更大的 frame 和仿真时间。它只记录 fresh replan 与路线安装请求，`computed_plan`、`native_installation`、`native_first_control_adoption` 始终为 null。`validate_plan_binding` 校验的是本 CPU stub 请求身份，不认证模型新计划，也不是原生路线每 tick 必须保持整个数组摘要相同的要求。

这是一问一答、单次路线请求的最小原型；不实现异步重试、多轮对话或持续重规划。问题未决、证据未知、截止超时均保留已有权限。软件 `WAIT` 不操作油门/刹车，也不证明 holding。运行入口先检查公共容器结构，非法 candidates/checks 等返回 `INVALID_PUBLIC_STRUCTURE`；合法结构下缺任务证据仍是 UNKNOWN，立即询问的共同门不包含分歧条件。

时间只接受公共起始事件和仿真时钟。合成测试的十秒预算标为 `CONTROLLED_PROTOCOL_DEADLINE`，不能用于 Town04；真实配置的未知预算和延迟为 null。`checks` 的 value、provenance、source 是调用方必须负责的证据合同，CPU 校验结构及适用类型，不自行产生或认证车辆安全证据。

STEP3A 所有新路线请求经过 `_request` 的共同执行/时间门。直接执行仅要求 `remaining > execution_reserve`，不收没有发生的回答预算；询问前要求 `remaining > interaction_budget + execution_reserve`。回答后新请求保留 STEP3 已披露的保守总预算，未调整为“只收剩余成本”。各阶段等号均拒绝新请求，未知必要预算/时钟及公共起点之前也拒绝。已有请求不因后续超时被重新发出或撤销。公共截止不随 ASK 或请求重置，详见 `DEVELOPMENT_CONTRACT_STEP3A.md`。

`evaluator.py` 只读独立任务门、实际采样形状、评价侧允许分支、轨迹覆盖与分项安全证据。轨迹仅接受 frame/sim_time_s/x/y，方法标签、关系、候选选择、TaskDetermined、ReplanSuccess、RC 均被拒绝。有限宽门采用 `da < 0 <= db` 的有向闭边界到达规则，依次要求 entry→exit→end；负侧→门线→负侧也产生到达事件，不代表已穿入正侧。旧 `correct_pass_observed` 是该按序到达端点的兼容名称，新输出同时给出 `correct_ordered_gate_arrival_observed` 与规则说明。缺帧断开次序，已确认事件按端点保留，整体任务仍可为 null；安全监测具有独立完整性。

`recorder_cadence` 为显式记录器合同接口。固定步长必须有 recorder_contract_id、来源、frame_period_s、tolerance_s；不匹配的观测间隔断开事件次序。缺字段或明确 `UNBOUND` 时不猜真实步长，`observed_intervals_consistent` 为 null。真实 Town04 保持未绑定；`STEP3A_SYNTHETIC_EVALUATION.contract.json` 的 0.05s 只来自合成夹具生成规则。多门同一段仍按 fraction 排序，旧夹具和 STEP3 归档不重评、不改写。

Town04 配置静态绑定历史地图连接段及当前预声明任务门，共享一个布局。它们尚无两条完整同终点可装入路线，执行、时间门保持 UNKNOWN；运行 CLI 得到 WAIT 是预期。沿地图线构造的测试轨迹是合成记录，不能作为真实驾驶、候选合法性或论文效果证据。

当前报告：`reports/driveclarify_experiment_restructure_step3a_20260912/FINAL_REPORT.md`，两个明确任务的未运行准备见同目录 `CLEAR_TASK_PROBE_PREP.md`。旧 STEP3 报告和归档保留，当前代码是新的开发版本。新复核包保留目录结构，附现有纯 CPU 比较器原文件，解压后可直接运行上述测试；不依赖完整历史数据或外部模型。
