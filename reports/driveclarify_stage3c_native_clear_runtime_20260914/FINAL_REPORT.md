# Stage3C：A已运行，B因墙钟监管资格复核失败而未运行

最终状态：**`STAGE3C_A_ONLY_COMPLETED_B_NOT_RUN`**。

本轮实际启动Task A一次，未启动Task B。A取得原生初始化路线、模型计划、返回控制和独立轨迹证据；没有观察到局部三门任务完成，没有原生最终route记录。A由watchdog结束，所属进程/端口/GPU compute清理通过。

**本轮存在明确监管偏差。** 我的wrapper在420s开始终止，A含信号发送和清理实际420.526189s，未严格满足用户给定的420s总墙钟上限。原dummy自测允许wall+1s，其PASS不足以认证严格上限；原A preflight的全PASS映射不充分。此偏差已原样披露，未补加宽限、未回写历史PASS记录。A后的复核将B墙钟硬门判FAIL，因此不启动B、不重跑A，也未现场改wrapper后继续驾驶。

## 实际身份与前置条件

| 仓库 | 实际branch / HEAD | 入口与出口diff |
|---|---|---|
| DriveClarify | master / `eaa332b1bb994279b59ea5af786fdb5de96adc1b` | tracked=0字节；staged=0字节；保留预存untracked |
| SimLingo | main / `743b243afd6cf5ff51b9fa1f8cac86f22d569684` | 预存tracked diff=65090字节，SHA-256 `112c1d006d398a10c91a1f25ae9a664bdecdf32a25246c8fed343c36daea970a`；staged=0；本轮未改 |

核心身份27项检查匹配；权重SHA-256为 `ec8943723d266ee9f5f56f45d153a163b22616960bfccb741965ea5daa700d28`，本轮只读完整hash一次。preflight只轻量import CARLA0.9.15，未创建Client、未导入模型或torch。原生模型仅在已启动的A中按正常入口加载/调用。

物理显示通过本地seat0/session2、X认证、活动输出和匹配EDID共同核验；A的CARLA原生窗口另实际观察为IsViewable。没有headless/RenderOffScreen/Xvfb/VNC/显示转发。资源、进程/端口和输出隔离在A前采证通过；总墙钟门在实测后撤销严格上限资格。详细原始证据及初始显示采证局限见 `PREFLIGHT_REPORT.md`。

A/B均使用已冻结完整XML、原默认TM seed0、repetition1、同一模型/PID/UKF/owner，hidden_intent=null、question_budget=0。没有ASK、CPURequestBackend、在线路线切换或DriveClarify live开关。`A_EFFECTIVE_COMMAND.*`/`B_EFFECTIVE_COMMAND.*`与环境JSON均已保存，B只有准备文本。

## A的十层结果

| 层级 | 结果 | 证据及限定 |
|---|---|---|
| NATIVE_INITIALIZATION | PASS | 初始化后世界状态、安装路线回执均存在 |
| ROUTE_ACCEPTANCE | PASS | 实际dense路线58点、planner安装4点；首次导航命令RIGHT。允许正常裁剪/重采样，不要求数组每tick相同 |
| MODEL_FORWARD_OBSERVED | PASS | 612组完整观测ID/帧/调用时间/输出回执；不是声称进程总调用次数恰为612 |
| PLAN_OUTPUT_OBSERVED | PASS | 612组已有原生调用产生的pred_route/pred_speed_wps实际值 |
| BASELINE_CONTROL_RETURNED | PASS | 612组同观测返回控制及control_ready时间 |
| ACTUAL_APPLY_CONTROL_INDEPENDENTLY_VERIFIED | UNKNOWN | 现有接口没有逐次apply_control持久回执，未临时修改scenario_manager |
| RECORDER_CADENCE_VALID | PASS | 已记录611个相邻区间满足帧差/时间差与snapshot_delta的0.05s合同；不推出全程覆盖或实时性能 |
| LOCAL_TASK_GATES | UNKNOWN | 未观察到entry→exit→local_end正事件；全程覆盖未认证，不以RC/方法状态/exit补判 |
| NATIVE_ROUTE_COMPLETION | UNKNOWN | 原生checkpoint停留Started，records为空；没有最终RC或route终止记录 |
| NATIVE_SAFETY_ENDPOINT | UNKNOWN | 无完整原生最终安全记录，不把空infractions当作安全 |

原始观察范围为frame1851–2462，snapshot elapsed0.950000–31.500000s。相对首点最大XY位移约1.542836m；585条控制记录brake>0，27条throttle>0。先前运行中报告的“位置基本未变、制动为主”只对应当时观测；完整记录包含后段小幅移动。没有完成分支任务的正证据，不把持续车辆生成警告虚构为根因。

A原生evaluator PID4111764；CARLA shell/PID为4111824、4111825、4111832。进程结束原因是本轮watchdog `WALL_TIMEOUT`，evaluator退出码-9；原生自身最终终止原因未知。两种原因分开保留。watchdog在420.000188s开始，最后初次终止信号420.023149s，回执总时长420.526189s，清理阶段0.422814s。不是exit0或科学PASS。

SIGKILL后没有生成 `PROBE_EQUIVALENCE.json` 和 `leaderboard_debug.txt`，原生最终records仍为空。这些缺失明确记录，不能声称拿到了正常关闭回执或所有底层调用总数。原始11个运行文件全部保留，包括612行原生metric_info，不补造缺失文件。

## B、资源与清理

B实际启动0次，输出目录不存在。`B_RUN_RECEIPT.json`等四份文件明确NOT_RUN，十层均UNKNOWN/NOT_RUN，不造成功数据。阻断为A之后新取得的严格墙钟资格反例；不是A任务没有成功所以按结果换路线或拒绝B。没有技术重试或科学重试。

GPU0为RTX4070 Ti，总显存12282MiB。A运行83次约5s间隔采样：整卡已用显存采样峰值11832MiB，最小可用20MiB，利用率采样峰值99%。这包括原有桌面/ToDesk和本轮，不是连续真峰值或模型独占显存。原始日志未观察到OOM标记。A前可用RAM约39.82GiB、磁盘约7.99GiB；A后均已记录。细项见 `RESOURCE_REPORT.json`，没有编造资源下限。

A清理后4个原生所属PID消失，2020/2021/2022/8020端口释放，本轮GPU compute PID为空。原ToDesk PID3861853保留，没有杀其他任务进程。清理通过与墙钟超出0.526s是不同事实，不能互相替代。证据见 `A_CLEANUP_VERIFICATION.json`、`PROCESS_AND_PORT_CLEANUP.json`。

## 勘误、保护与交付

`PAPER_ERRATA.md`确认Task B local_end权威值 **(-30.6,-161.4)**，原RUN_CARD的-161.5为文档笔误。evaluation_B.json、NATIVE_CLEAR_CASES.json、XML及历史报告均未回写。

59个预存Stage3B报告/开发文件摘要未变，所有核心源匹配入口，checkpoint仅做出口元数据复核；仓库branch/HEAD/tracked/staged diff保持入口身份。保留预存untracked及研究文件。没有commit/push/reset/clean/restore/checkout；没有修改生产/live/模型/PID/UKF/route owner/安全终止规则，没有训练/下载依赖/换checkpoint或seed。

原始A输出保留在 `experiments/driveclarify_native_clear_backend_dev_20260913/outputs/DEV_NATIVE_INIT_A_TOWN07_25968/`。本目录包含四类A/B回执、十层结果、原始命令/资源日志、入口出口Git、监管自测、勘误与复核包。全部路径与摘要见 `ARTIFACT_MANIFEST.json`；小包包含原始A数据，排除2.57GB模型权重和大历史交接全文。

本轮只完成一次公开任务开发曝光，不能支持方法收益、回答后切换、fixed neutral route、hidden-intent fairness、CPURequestBackend接通、九次烟测或正式主实验资格。不得把本轮开发结果作为未来未见测试。**唯一下一步：独立复核A的实际证据及B的墙钟阻断原因。** 本轮已停止。
