# DriveClarify v0 来源审计

审计日期：2026-07-22（Asia/Shanghai）  
审计方式：只读检查本地证据；本文件是本阶段首先创建的设计产物  
项目根目录：`/home/buaa/wrh/DriveClarify`  
设计任务指定的外部运行目录：`/home/buaa/wrh/simlingo`、`/home/buaa/CARLA_0.9.15`、`/home/buaa/anaconda3/envs/simlingo`

## 1. 分类规则

- **工程事实**：由本地保留的报告、JSON/JSONL 结果、源码审计报告或路径检查直接支持。
- **论文设计**：由本阶段任务书要求的 v0 算法、接口、状态、阈值或实验合同；不代表已经实现。
- **未验证假设**：合理但尚无保留证据支持，使用前必须标成 `TBD`、补充日志或完成测试。
- **缺失（MISSING）**：在项目目录、`/home/buaa/wrh` 和 `/home/buaa` 下按规定文件名搜索后仍未发现。无关软件产生的 `state.json` 不计作项目证据。

请求中列出的 PDF 均不存在，因此本设计没有根据文件名推测其内容。

## 2. 必读来源清单

| 请求的文件或证据组 | 实际路径 | 是否存在 | 支持的结论 | 分类 |
|---|---|---:|---|---|
| `CURRENT_HANDOFF*.md` | `MISSING` | 否 | 不能把任何结论归因于“当前 DriveClarify 交接文档”。已有 `HANDOFF.md` 属于 OpenArm 打包，不能替代。 | 缺失 |
| `STATE*.json` | DriveClarify 项目中 `MISSING` | 否 | 当前没有 DriveClarify 算法状态快照。 | 缺失 |
| `NEXT_CHAT_PROMPT*.md` | `MISSING` | 否 | 当前没有上一阶段的后续任务合同。 | 缺失 |
| `DriveClarify_Algorithm_Reading_Guide.pdf` | `MISSING` | 否 | 本设计未使用任何阅读指南结论。 | 缺失 |
| `DriveClarify_Algorithm_Reading_Guide.md` | `MISSING` | 否 | 本设计未使用任何阅读指南结论。 | 缺失 |
| `DriveClarify_RP*.pdf` | `MISSING` | 否 | 本设计未使用任何 RP 论文结论。 | 缺失 |
| `reading_algorithm_design/` | `MISSING` | 否 | 未根据预期目录名推断内容。 | 缺失 |
| 第一次原始冒烟测试报告 | `/home/buaa/wrh/DriveClarify/runtime/original_smoke_1956/ORIGINAL_SMOKE_REPORT.md` | 是 | 第一次启动未到达 CARLA RPC 和模型初始化；这是失败证据，不能否定后续成功重试。SHA-256：`d04d967607acda41afffd24b0d8773941a35c3fe091be5e06b0c193f3d8f219b`。 | 工程事实 |
| 原始冒烟测试重试报告 | `/home/buaa/wrh/DriveClarify/runtime/original_smoke_1956_retry1/ORIGINAL_SMOKE_RETRY1_REPORT.md` | 是 | CARLA 0.9.15、原始 SimLingo checkpoint 和 agent 初始化成功并完成一条路线；路线完成度 100%，记录了 17 次最低速度违规。 | 工程事实 |
| 原始冒烟测试结果 | `/home/buaa/wrh/DriveClarify/runtime/original_smoke_1956_retry1/result_1956_retry1.json` | 是 | `entry_status=Finished`、`eligible=true`、Route 1956 完成，以及评测器保留的违规和传感器列表。 | 工程事实 |
| 原始冒烟测试清单 | `/home/buaa/wrh/DriveClarify/runtime/original_smoke_1956/run_manifest.json`；`/home/buaa/wrh/DriveClarify/runtime/original_smoke_1956_retry1/run_manifest.json` | 是 | 两次运行的命令、路径、时间、进程、运行身份和完整性检查。 | 工程事实 |
| 接口审计 | `/home/buaa/wrh/DriveClarify/runtime/branch_interface_gate/INTERFACE_AUDIT.md` | 是 | 当前 `run_step`、`tick`、模型和 PID 数据流；一次 tick 后、PID 前的安全挂接点；受约束 HLC 接口；不支持声称自由文本；实际输出形状。 | 工程事实 |
| 分支接口门报告 | `/home/buaa/wrh/DriveClarify/runtime/branch_interface_gate/BRANCH_INTERFACE_GATE_REPORT.md` | 是 | 同一观测、同一模型实例、两次顺序前向、一次 baseline PID、规划头产生非零条件效应，且未修改 SimLingo。 | 工程事实 |
| 分支候选 JSON | `/home/buaa/wrh/DriveClarify/runtime/branch_interface_gate/run1/branch_candidates.json` | 是 | `pred_route=[1,20,2]`、`pred_speed_wps=[1,10,2]`；受约束命令 4 与 3；route L2 为 `0.5192239881`，speed L2 为 `0.1624957770`。两者只能解释为结构差异。 | 工程事实 |
| 稳定性门报告 | `/home/buaa/wrh/DriveClarify/runtime/branch_stability_gate/BRANCH_STABILITY_GATE_REPORT.md` | 是 | Single-mode 与 Branch-mode baseline 完全相同；连续 10 个 probe；状态/PID 隔离；前向延迟和能力边界。 | 工程事实 |
| 透明性 JSON | `/home/buaa/wrh/DriveClarify/runtime/branch_stability_gate/TRANSPARENCY_CHECK.json` | 是 | baseline 的 route、speed、language 完全相同；仅一个模型实例；alternative 有条件效应；无效的 RNG 摘要比较明确未用于门禁结论。 | 工程事实 |
| 稳定性 probes | `/home/buaa/wrh/DriveClarify/runtime/branch_stability_gate/run1/probes.jsonl` | 是 | 共 10 条；10/10 的 route 和 speed 均有非零效应；每条一次 tick、两次前向、一次 baseline PID、零次 alternative PID；平均延迟约 `0.6238127 s` 和 `0.6245611 s`。 | 工程事实 |
| 用户提供的 v0 任务书 | `/home/buaa/.codex/attachments/b309f9f5-600a-42f6-8b98-b4d654118f4d/pasted-text.txt` | 是 | 本阶段的范围、输出、原则、字段、状态、质量门槛和禁止事项。它不证明算法已实现或实时性能成立。 | 论文设计 |
| 当前项目目录树 | `/home/buaa/wrh/DriveClarify/` | 是 | 确认上述路径、`design/v0/` 原先不存在，并确认项目根目录没有 `.git`。 | 工程事实 |

## 3. 实际读取的补充背景

| 文件或路径 | 读取原因 | 用途与限制 | 分类 |
|---|---|---|---|
| `/home/buaa/wrh/DriveClarify/HANDOFF.md` | 判断它能否替代缺失的 `CURRENT_HANDOFF*` | 这是 OpenArm Docker 打包交接，状态为 `BLOCKED_PROJECT_PATH_CONTAMINATION`；不用于定义 DriveClarify v0。 | 工程事实，范围外背景 |
| `/home/buaa/wrh/DriveClarify/docs/HOST_TAKEOVER_AUDIT.md` | 确认 SimLingo 为外部同级仓库并保留来源关系 | 记录了 SimLingo 路径、脏工作树、checkpoint、配置和结果位置，以及 220 条路线 benchmark 未完成。 | 工程事实 |
| `/home/buaa/wrh/simlingo` | 只读检查 Git 和路径身份 | 独立 Git 仓库，`main` 分支，已有修改和未跟踪文件；本阶段禁止修改。 | 工程事实 |

## 4. 当前能力边界

已经证实：

1. 原始 SimLingo checkpoint 在 CARLA 0.9.15 中完成过一条路线；这只是冒烟结果，不是 DriveClarify 安全结果。
2. DriveClarify 侧 wrapper 能复用同一预处理观测和同一模型实例，顺序生成两个受约束 HLC 候选；形状为 `pred_route=[1,20,2]` 和 `pred_speed_wps=[1,10,2]`。
3. 保留的透明性检查中，Single-mode 与 Branch-mode baseline 完全相同。
4. 10 个 probe 的 route 和 speed 都有非零条件效应；每个 probe 一次 tick、两次顺序前向、一次 baseline PID、零次 alternative PID。
5. 两个平均前向耗时约 `0.624 s` 和 `0.625 s`；因此在线计时实现前，v0 使用 `T_branch=1.25 s` 作为保守工程默认值。
6. gate 证据表明 alternative branch 没有污染 route planner、UKF、command history、PID 或 baseline CPU 副本。

尚未证实：

- 自由文本解析、解释语义正确性、任意乘客语言、后果评估、决策截止时间、ACT/ASK/WAIT、holding、回答处理、回答条件重规划、形式化安全、实时运行、benchmark 改进或泛化；
- path L2、ADE、FDE 或其他几何差异是安全指标；
- 当前运行时已经记录 actor 包围盒/速度、lane/road/exit ID、停止线、实线/导流区、rear TTC、clearance、加速度、jerk、steering rate 或合法停车位置。这些都需要新增同步日志和验证。

## 5. 仍属假设或待定的设计输入

解释本体校验、拓扑/目标匹配、安全与规则评估、可恢复性、决策边界提取、安全共享走廊、问答通道、回答延迟模型和 holding controller 都没有当前实现证据。相关阈值必须保持 `TBD`，以后只能依据交通规则、物理约束、pilot 或冻结的 development split 确定，不能让 DriveClarify 自己的 evaluator 生成 oracle 决策标签。

## 6. 审计结论

`SOURCE_AUDIT_PASS_WITH_MISSING_REFERENCES`（来源审计通过，但存在缺失参考资料）

缺失的阅读指南、RP、交接和状态文件不允许被虚构，但不妨碍根据本阶段任务书和已保留接口证据制定自洽的 v0 合同。如果以后补充这些资料，实现前必须重新做来源差异审查。
