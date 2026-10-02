# STEP3A 有限开发合同

版本 `STEP3A_CPU_BOUNDARIES_V1`，默认关闭。旧 STEP3 上传归档及原 28 项测试文件保留。本合同只前瞻规定新开发代码，不改历史科学标签。

## 时间与公共结构

所有新路线请求经 `_request` 检查有效执行依据与时间，无原生执行豁免。公共截止 `d=公共起始时刻+公共预算` 固定。起点之前、必要字段未知均不发请求。

| 阶段 | 必要预留 | 允许条件 |
|---|---|---|
| 直接执行（CLEAR、无澄清、等价完整方法） | execution_reserve_s | now≥start 且 d−now>execution_reserve_s |
| 询问前 | interaction_budget_s+execution_reserve_s | now≥start 且余量严格大于总预留 |
| 回答后新状态请求 | 同上，保留原保守选择 | frame/time 均晚于回答，且余量严格大于总预留 |

等号拒绝新请求。手写合成合同 d=10、execution=1、interaction=1：t=8.5 可直接执行而不能询问；t=8 不允许询问/回答后请求；t=9 不允许直接执行；t=10/11 均拒绝。直接执行无需未知的 interaction_budget；execution_reserve 未知仍拒绝。拒绝保留既有权限，不停车，不撤销已发的合法请求或冒称旧计划非法。

`public_structure_errors` 只检查入口实际使用的容器形状；运行结构错误为 WAIT/INVALID_PUBLIC_STRUCTURE，未进入关系判断或请求。结构合法的任务字段缺失仍为 UNKNOWN；立即询问在有效歧义与共同门满足时仍可询问，完整方法不因 UNKNOWN 获得权限。

## 单次答案服务故障

实际问题先写入并 fsync。仅此后读取答案文件；预期文件/I/O/编码/JSON/答案形状错误使用受控失败，保留 query_count=1、问题回执和 authority。另存不可覆盖的 answer_failure 回执；CLI 同时输出 JSON 并返回 3。再次 step 保持未决状态及原失败，不重读、不重问、不给默认答案、不请求路线。仅捕获明确服务异常类型；不以 except Exception 隐藏程序错误。日志介质故障也明确披露，不能保证不可写介质的持久性。

## 评价事件与记录器

规则固定为 `NEGATIVE_TO_NONNEGATIVE_CLOSED_BOUNDARY`：有限宽门上 da<0<=db 即到达。接触后退会记一次；接触后进入不重复；逆向到达不计；终点门线到达即可满足 end。entry/exit/end 是兼容字段名，不代表严格穿入正侧。`correct_pass_observed` 与新别名 `correct_ordered_gate_arrival_observed` 同值；对错误任务门的闭边界到达按本开发义务锁存，未证明物理不可恢复。

保留同段多门 fraction 排序、缺记录的端点可识别性及分项安全交集。未新增迟滞或车辆动力学模型。

`recorder_cadence`：`UNBOUND` 时周期和容差均 null，时步一致性不作已检查结论；绑定 `FIXED_STEP_RECORDER_CONTRACT` 需显式来源、recorder_contract_id、正 frame_period_s 和非负 tolerance_s。相邻合法 frame 区间验证 `abs(Δsim_time−Δframe*period)≤tolerance`；超差作为 gap，既有已确认事件仍保留。该接口不自行认证来源。Town04 真记录器未绑定；合成 0.05s 来自 `tests/fixtures.py:trace` 的明确生成规则，容差 1e−9 为该合成浮点序列的声明，不推广到真实 recorder。

原生准备只包含公开明确的 A/B 两任务。CPU stub 的 route_digest 比较不约束原生合法裁剪/重采样；未来原生证据应追溯同一事务的派生身份。新计划须有重算和新观测绑定证据，不要求数值必然变化。
