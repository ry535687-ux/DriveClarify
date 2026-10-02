# B2D_FINAL_AUTHORITATIVE_SUMMARY

## A. 最终来源与覆盖范围

**未找到完整最终来源。** 已核验的正式协议是 `/home/buaa/wrh/DriveClarify/reports/driveclarify_transparent_bypass_full_bench2drive_v2/FULL_B2D_FREEZE_RECEIPT.json`，版本 `DRIVECLARIFY_NO_CONTEXT_TRANSPARENT_BYPASS_V2.0`，冻结时间 `2026-09-06T17:31:41.673692+00:00`；它定义 A0 为原生 SimLingo，A1 为同一冻结骨干经 DriveClarify 无上下文旁路，原生控制器相同。配套清单与官方 XML 的内容/哈希一致，确有 **220 个唯一目标 route ID**，并逐路线安排 A0/A1；但没有找到对应的全量最终结果。故**最终唯一配对路线数、每组最终纳入运行数及最终缺失/未完成数均为 MISSING**，有效结果分母未确认。220 是目标清单数，不能据此报告已完成440次运行，也不能把重复尝试计成新路线。

| 来源 | 内容核验与采用范围 |
|---|---|
| [正式冻结协议](/home/buaa/wrh/DriveClarify/reports/driveclarify_transparent_bypass_full_bench2drive_v2/FULL_B2D_FREEZE_RECEIPT.json) | `formal_freeze_issued=true`；`arm_config`、`checkpoint`、`controller`、`pair_order`定义配置和计划配对。**协议来源，不是最终结果来源。** |
| [路线清单](/home/buaa/wrh/DriveClarify/reports/driveclarify_transparent_bypass_full_bench2drive_v2/FULL_B2D_ROUTE_MANIFEST.json) | `route_count=unique_route_id_count=220`；`routes[*].route_id`无重复，与[官方 XML](/home/buaa/wrh/simlingo/leaderboard/data/bench2drive220.xml)一致。 |
| [候选配对账本](/home/buaa/wrh/DriveClarify/reports/driveclarify_transparent_bypass_full_bench2drive_v2/PAIRED_ROUTE_LEDGER.json)及[A0](/home/buaa/wrh/DriveClarify/reports/driveclarify_transparent_bypass_full_bench2drive_v2/A0_EXECUTION_LEDGER.json)/[A1](/home/buaa/wrh/DriveClarify/reports/driveclarify_transparent_bypass_full_bench2drive_v2/A1_EXECUTION_LEDGER.json)账本 | 属于用户排除的旧101对中途账本；没有全量闭合。仅作为排除证据，不采用其部分指标，不将其当作最新完成数。 |
| [旧“最终验证”](/home/buaa/wrh/DriveClarify/reports/driveclarify_full_bench2drive_standard_benchmark_v1/FINAL_VALIDATION_RECEIPT.json)及同目录 A0/A1_OFFICIAL_MERGED_RESULTS.json | `full_benchmark_complete=false`；所谓 merged 文件实际为 `UNAVAILABLE_RESULT_NOTICE_NOT_OFFICIAL_MERGE_OUTPUT`，DS/SR为null。文件名不能证明最终性。 |

V2 的冻结收据 SHA-256 为 `e55c3dd55d83b7ef372c130c991b485837170524791b0bc83c9d994706d5be54`，路线清单 SHA-256 为 `479e348b5eb0b4436839812402ff456cbbb4911a8d62618a6aa2725ffd2dfcfa`。最终结果的生成时间/版本为 **MISSING**；各候选文件的绝对路径、修改时间、哈希和角色完整列于 JSON 的 `source_files`。另检出的其他220记录 merged 文件缺少本实验 A0/A1 冻结配置及配对绑定，也未采用。只读校验见 [qa_read_only.ipynb](/home/buaa/wrh/DriveClarify/reports/driveclarify_b2d_final_authoritative_handoff_20260914/qa_read_only.ipynb)。

## B. 可直接填入论文的两行主表

**当前仅为缺失项交接表，不能作为已完成实验的数值结果表。**

| 配置 | DS | RC | Success | Collision | False ASK |
|---|---|---|---|---|---|
| SimLingo | MISSING | MISSING | MISSING（n/N、%均缺） | MISSING（n/N、%均缺） | MISSING |
| SimLingo + DriveClarify | MISSING | MISSING | MISSING（n/N、%均缺） | MISSING（n/N、%均缺） | MISSING |

DS单位为分值，RC单位为%；Success和Collision必须同时有有效分母、路线数与百分比。False ASK主表应报告**请求次数**；发生误询问的路线数和比例也未找到。只有完整最终记录确认全部为无需澄清任务，且ASK字段确实计澄清模块发出的请求时，总ASK才可对应False ASK。代码中的旁路零值模板或没有活动日志，均不能证明完整220条路线的误询问为0。

以下为**候选协议/输出代码中的字段定位，不是已找到的最终数值来源**。全部指标的最终 `source_file/source_field` 在 JSON 中为 null；预期字段另列，避免混淆。

| 指标 | 预期最终文件与字段 | 定义/当前缺口 |
|---|---|---|
| DS | V2目录 `A0_OFFICIAL_MERGED_RESULTS.json` / `A1_OFFICIAL_MERGED_RESULTS.json`：`["driving score"]`、`["eval num"]` | 最终文件未找到；不从部分路线反推。 |
| RC | V2目录 `FULL_B2D_OFFICIAL_COMPARISON.json`：`metrics["Route Completion mean (%)"]["A0"/"A1"]` | 最终文件未找到。 |
| Success | 官方 merged 的 `["success rate"]`、`["eval num"]`及完整`_checkpoint.records` | 官方条件为 `status` 是 `Completed` 或 `Perfect`，且除 `min_speed_infractions` 外所有 infraction 列表为空；不是RC=100。 |
| Collision | V2目录 `FULL_B2D_INFRACTION_ANALYSIS.json`：`categories.ANY_COLLISION.A0/A1.episode_count`、`denominator` | 行人 `collisions_pedestrian`、车辆 `collisions_vehicle`、静态/场景物体 `collisions_layout` 的路线并集；不是三类事件总数之和。最终文件未找到。 |
| False ASK | V2目录 `FULL_B2D_DRIVECLARIFY_ACTIVITY.json`：`totals.ASK`、`totals.routes_with_ASK`；各臂完整最终 `agent_terminal.json: ASK` | 既有输出代码只汇总A1；两臂均缺完整最终证据，不填0。 |

官方成功定义只读核验于 [merge_route_json.py](/home/buaa/wrh/simlingo/Bench2Drive/tools/merge_route_json.py) 的 `merge_route_json()`，文件哈希与冻结协议一致；未执行该脚本。预期汇总字段来自 [/home/buaa/wrh/DriveClarify/reports/driveclarify_transparent_bypass_full_bench2drive_v2/tooling/analyze.py](/home/buaa/wrh/DriveClarify/reports/driveclarify_transparent_bypass_full_bench2drive_v2/tooling/analyze.py) 的静态读取，未执行任何合并或分析。

## C. 补充事件字段

| 配置 | 澄清等待次数 | Full Replan次数 | 控制介入次数 | 来源字段 |
|---|---|---|---|---|
| A0：SimLingo | MISSING | MISSING | MISSING | 最终来源未找到；候选终点字段为 `WAIT` / `clarification_triggered_full_replan` / `direct_control_intervention` |
| A1：SimLingo + DriveClarify | MISSING | MISSING | MISSING | 同上；须有本臂完整最终记录 |

候选字段定义位于 [benchmark_agent.py](/home/buaa/wrh/DriveClarify/driveclarify_transparent_bypass_v2/benchmark_agent.py) 的 `ObservedNativeAgent.destroy()`，预期终点文件路径为 `/home/buaa/wrh/DriveClarify/reports/driveclarify_transparent_bypass_full_bench2drive_v2/formal/<route_id>/<arm>/<authoritative_attempt>/agent_terminal.json`。这里只核对了字段模板，未将模板零值计作观测。澄清WAIT仅指模块导致的等待，不包括低速、停车或交通等待；非零 `WAIT` 究竟按tick还是独立事件计数，缺少最终记录验证，口径为 **UNVERIFIED**。Full Replan仅指回答后澄清触发的完整重新规划，不含周期规划、初始原生路线安装或非澄清重规划。控制介入仅指DriveClarify介入；原生 `PID_count` 不属于该事件，非零事件/tick口径亦需最终证据确认。

## D. 可用于论文的观察

未找到覆盖完整220条配对标准路线的最终结果，因此目前不能描述接入前后DS、RC、官方成功率或碰撞发生率的变化。误询问、澄清等待、回答后完整重新规划及控制介入也缺少全量最终事件证据，不能写为零。当前证据不足以在第4.4节支持普通路线透明接入、基础驾驶能力保持、统计等效或非劣效结论。

## E. 仍缺少的字段

- **A0：**完整最终运行纳入清单、最终缺失/未完成记录数、DS均值及分母、RC均值及单位、官方成功路线数/分母/成功率、任一碰撞路线数/分母/发生率、False ASK次数及涉及路线数/比例、澄清等待次数及计数单位、回答后Full Replan次数、DriveClarify控制介入次数及事件定义。
- **A1：**同样缺少上述全部完整最终字段；旁路协议或代码声明不能替代最终驾驶表现和全量事件记录。
- **共同缺口：**完整220个唯一配对route ID的最终权威账本及每路线A0/A1唯一纳入结果绑定；对应的官方合并结果、RC/碰撞汇总、两臂终点事件汇总和最终来源版本/时间。V2目录未找到 `A0_OFFICIAL_MERGED_RESULTS.json`、`A1_OFFICIAL_MERGED_RESULTS.json`、`FULL_B2D_OFFICIAL_COMPARISON.json`、`FULL_B2D_INFRACTION_ANALYSIS.json`、`FULL_B2D_DRIVECLARIFY_ACTIVITY.json`、`ALL_PAIRED_ROUTE_RESULTS.csv`、`FINAL_REPORT.md`、`CONTROL_INTEGRITY_RECEIPT.json`；完整绝对路径见JSON的 `expected_final_files_not_found`。等价名称的完整、可验证最终来源也未找到。

**未找到完整最终来源，已停止填值。** 全部不可用数值为 `MISSING` / JSON null；没有将证据缺失记作0或N/A，没有拼接旧记录或采用旧Word、聊天、部分路线及外部参考数字。未启动CARLA、模型推理、路线执行或评测，未修改原始结果。本交接只说明第4.4节目前缺少哪些完整最终证据。
