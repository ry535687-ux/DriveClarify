# Task B 单次原生后端开发探针

最终状态：**`STAGE3C_B_SINGLE_DEVELOPMENT_RUN_COMPLETED_REVIEW_REQUIRED`**。六项preflight全部PASS，B实际启动1次、重试0次；A/第三路线/三策略/正式实验均未运行。

B取得原生 `Completed`、RC=100和独立三门按序正事件；原生评价同时报告 **MinSpeedTest FAILURE**，`min_speed_infractions`有17条，原生安全端点按本次预声明规则为FAIL。进程exit0、DS/RC=100、任务门通过均不抵消该记录，也不将这17条说成17次碰撞。**不诊断或修改该原生指标，不重跑。**

## 身份、入口与十层结果

公开任务 `DEV_NATIVE_INIT_B_TOWN07_26458`；完整原始split193 XML，Town07/T_Junction，进入world X减小支路。原seed0、repetitions1、GPU0、checkpoint/agent/PID/UKF/owner不变；原生记录中的weather_id=8是天气身份，不是换seed。hidden_intent=null、question_budget=0，无ASK/CPURequestBackend/在线切换。

修订wrapper SHA-256 `a8734561827bd9b724395f8f709e919802463bad5be1e258da1856d4db603ef8`，逐字节匹配Stage3D；本轮单次入口 `scripts/execute_b.py` 显式绑定420/3.9/416.1/0.5秒合同。原Stage3C旧wrapper/旧失败门原样保留。完整命令、有效环境和现场证据在 `PREFLIGHT_REPORT.md`、`B_EFFECTIVE_COMMAND.*`、`B_EFFECTIVE_ENV.json`。

| 层 | 结果 | 限定依据 |
|---|---|---|
| NATIVE_INITIALIZATION | PASS | 已有初始化后世界状态与原生安装路线回执 |
| ROUTE_ACCEPTANCE | PASS | 初次原生安装路线与dense实际回执 |
| MODEL_FORWARD_OBSERVED | PASS | 原生同观测调用时间及输出回执 |
| PLAN_OUTPUT_OBSERVED | PASS | 实际pred_route/speed值 |
| BASELINE_CONTROL_RETURNED | PASS | 同观测返回控制值和control_ready |
| ACTUAL_APPLY_CONTROL_INDEPENDENTLY_VERIFIED | UNKNOWN | 现有接口无逐次apply_control持久回执 |
| RECORDER_CADENCE_VALID | PASS | 只评价已记录区间的frame/time/snapshot_delta一致性；不推出全程覆盖 |
| LOCAL_TASK_GATES | PASS | 观察到连续按序三门闭边界到达 |
| NATIVE_ROUTE_COMPLETION | PASS | 原生最终route记录单独判读 |
| NATIVE_SAFETY_ENDPOINT | FAIL | 已有明确原生违规事件可判FAIL；空infractions不单独认证安全，缺独立完整安全端点时保持UNKNOWN |

实际原生dense路线77点、planner安装4点，初次消耗LEFT；记录中LEFT为95条、LANEFOLLOW为173条。允许同一初始化路线的正常裁剪/派生身份，不要求每tick数组摘要恒定。原始完整trace/RoadOption见 `ROUTE_BINDING_RUNTIME.json`。

268组观测包含实际model start/end、输出和返回控制值；最终PROBE_EQUIVALENCE持久回执记录268个单调用site、控制值/身份保持及world-state写入，错误计数0。该计数只是辅助，forward/计划结论另有实际时间和数值记录；actual apply_control独立回执仍UNKNOWN。

## 轨迹、计划与独立任务事件

frame2013–2280，snapshot elapsed **0.950000–14.300000s**；267个已记录相邻区间cadence=0.05s通过。GameTime为0.100000–13.450000s，时间原点单列，不混用。全任务观测覆盖仍null，不认证未记录区间。

首位置(-0.949232,-123.089882,-0.040700)，末位置(-37.085049,-161.623337,0.136038)；相对首点最大XY位移 **52.826360m**，不是路径总长。ego速度中位5.411428、最大10.815282m/s。

按冻结原生公式 `2*norm(speed_wps[0]-speed_wps[2])` 重建目标标量：min/median/max=0.078221/6.628310/10.239087（源码按m/s使用，非直接PID内部变量日志，未执行PID）。brake>0共48条、throttle>0共206条；原始route折线长度18.771890–19.225302，保留局部原始单位，不当世界米。

独立冻结合同 `DC_NATIVE_CLEAR_INIT_DIAG_V1_B_GATES_DRAFT`，local_end权威(-30.6,-161.4)，有向闭边界到达：

| 门 | 相邻frame | 插值snapshot elapsed |
|---|---|---:|
| entry | 2238→2239 | 12.212097s |
| exit | 2251→2252 | 12.860597s |
| local_end | 2264→2265 | 13.529630s |

事件仅来自ego世界XY/时钟，未读取方法真值或RC。门定义不变，接触后退也记事件；不升级为严格穿入、物理不可恢复或正式歧义模板资格。原生Completed/RC=100按原生终止判定保留，不把它解释成车体到达XML最后点(-44.6,-161.6,0.1)。

原生最终统计：Completed、RC=100、score_penalty=1.0、score_composed=100；结果表FAILURE来自MinSpeedTest。collision三类、red_light、stop、route_dev、vehicle_blocked、route_timeout、scenario_timeouts等列表为空；只报告原生未记录相应事件，不以空列表证明安全。现有traffic_light记录为空，不能推断GREEN；scenario执行状态没有持久字段，保持null。没有现场postmortem、灯/route/HLC/seed/控制修改。

与A的已有记录仅作描述性对照：A最大XY位移1.54m、585/612条制动、局部门无正事件；B最大52.83m、48/268条制动、有三门正事件。两者起点、场景/天气和公开任务不同，不能构成方法收益或隐藏意图公平性比较。

## 结束、预算、资源与清理

原生自行结束，evaluator PID159050 exit0；watchdog未触发，没有发送TERM/KILL。开始UTC 2026-09-13T17:40:26.005564+00:00，cleanup结束UTC 2026-09-13T17:44:52.722809+00:00。含清理回执总时长 **266.717236616s**，到wrapper返回 **266.720139655s**，严格<420；本次runtime总预算PASS。

这次提前正常结束没有验证真实CARLA的超时TERM→KILL路径，不宣称该路径普遍受保证。原wrapper未记录精确process-exit瞬间，`B_PROCESS_TIMELINE.json`明确保留null及观测上界，不以cleanup结束冒充精确退出时刻。

54次约5秒间隔整卡GPU0采样，已用峰值11395MiB、最小free458MiB、利用率峰值99%；含桌面/既有ToDesk，非连续或模型独占峰值。stdout/stderr没有OOM标记。清理后本轮4个出生身份绑定PID（159050/159099/159100/159108）消失，2020/2021/2022/8020释放，本轮GPU compute为空；既有ToDesk3861853保留。详见 `RESOURCE_REPORT.json`、`PROCESS_AND_PORT_CLEANUP.json`。

## 交付与停止

原始B输出12个文件全部保留，包含stdout/stderr、probe/world-state、实际路线绑定、最终原生统计、PROBE_EQUIVALENCE、资源与owned回执、native metric。`leaderboard_debug.txt`虽在命令中指定但未产生，保留缺失事实，不补造。来源目录：`experiments/driveclarify_native_clear_backend_dev_20260913/outputs/DEV_NATIVE_INIT_B_TOWN07_26458/`。

本轮只做必要静态绑定、现场preflight和离线提取/复核包重放；不重跑9次dummy、旧46/14/RQ。A原始输出、冻结配置及native源受保护；checkpoint完整hash一次，出口仅检查元数据。两仓库branch/HEAD及tracked/staged diff与入口一致；无commit/push/reset/clean/restore/checkout，预存untracked保留。增量交接已更新，旧阶段记录不改写。

`ARTIFACT_MANIFEST.json`列文件/摘要，`REVIEW_PACKET.zip`及sha256提供B原始记录、可重放CPU提取代码、配置、必要源和报告；不含模型权重。仅CPU重放入口见 `REVIEW_README.md`，不会启动CARLA/模型。准备/整理脚本实际diff另存 `evidence/DEVELOPMENT_CHANGES.patch`。

唯一下一步：独立复核B证据及原生MinSpeedTest/安全端点记录；不再运行或修改驾驶。没有正式实验资格、在线回答切换或方法有效性结论。本轮完成停止。
