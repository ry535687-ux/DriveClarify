# DriveClarify v0 设计审查报告

审查日期：2026-07-22  
审查范围：仅算法规格设计产物  
结论：**通过（PASS）**

这里的“通过”表示 v0 设计合同可审查并满足任务书质量门槛，不表示算法已实现、安全、实时或经过实验验证。

中文化状态：全部 Markdown 正文、JSON Schema 的说明元数据以及两张 SVG 的可见说明文字均已改为中文。为保证未来代码和机器接口兼容，规定的文件名、JSON 字段名、模块名、状态枚举、ACT/ASK/WAIT、PID、HLC 和数学符号保留原始标识，并在 `NOTATION_AND_DATA_MAPPING.md` 中提供中文术语表。

## 1. 交付物审查

| 必需产物 | 状态 | 审查结论 |
|---|---:|---|
| `SOURCE_AUDIT.md` | 通过 | 实际路径、存在性、用途和证据分类齐全；缺失资料已标记 |
| `DriveClarify_v0_Algorithm_Specification.md` | 通过 | 含规定的 16 章、字段级后果审计、cascade 和 oracle pilot |
| `NOTATION_AND_DATA_MAPPING.md` | 通过 | shape、单位、来源、oracle/tick 状态和三值缺失合同齐全 |
| `DRIVECLARIFY_STATE_MACHINE.md` | 通过 | 七个状态、超时、防重复、延迟/无效回答和重新进入规则齐全 |
| `CONSEQUENCE_SCHEMA.json` | 通过 | JSON Schema 可解析；全部字段存在；无加权标量总分 |
| `ALGORITHM_1_PSEUDOCODE.md` | 通过 | 含持久逐 tick 循环、顺序分支、deadline、holding、回答、timeout、fallback 和 wall-clock budget |
| `TEST_AND_ABLATION_PLAN.md` | 通过 | 所有规定 baseline/ablation 和 Ask-to-Act 信息防火墙齐全 |
| 架构 SVG | 通过 | XML 可解析；流程完整；ASK 明确等于“发送问题 + holding control” |
| 状态机 SVG | 通过 | XML 可解析；七个状态和主要转移齐全 |
| `DESIGN_REVIEW_REPORT.md` | 通过 | 本文件 |
| `NEXT_STAGE_OFFLINE_EVALUATOR_PROMPT.md` | 通过 | 明确下一阶段仅离线、CPU-only |

当前 shell 没有 SVG raster renderer，因此图只完成 XML 和几何结构检查，没有生成 raster 截图。发布前仍应在图形界面渲染复核。

## 2. 强制质量门槛

| 编号 | 阻断条件 | 结果 | 证据 |
|---:|---|---:|---|
| 1 | 使用未验证或不存在的文件 | 通过 | 缺失来源明确标记为 `MISSING` 且未使用 |
| 2 | 把 constrained HLC 写成自由文本能力 | 通过 | 所有 scope、mapping 和 pilot 均否认自由文本能力 |
| 3 | 把 route L2 当作安全 | 通过 | route L2/ADE/FDE 只作结构诊断 |
| 4 | 忽略约 1.25 秒双前向 | 通过 | timing、schema、cascade、伪代码和测试均计入该成本 |
| 5 | 把 ASK 写成车辆停止 | 通过 | ASK 是 question dispatch 和独立选择的 H1–H4 holding |
| 6 | 没有独立 WAIT | 通过 | 状态机、cascade、伪代码和 baseline 均有独立 WAIT |
| 7 | 用任意权重合并碰撞和询问 | 通过 | 使用词典序 cascade，维度独立，schema 禁止聚合 |
| 8 | 缺少无回答、延迟或错误回答 fallback | 通过 | 主规格和状态机均覆盖 |
| 9 | 自身 evaluator 生成 oracle 标签 | 通过 | 要求独立标注并在输出前冻结 |
| 10 | 缺少 Ask-to-Act-inspired 或 no-WAIT baseline | 通过 | 两者均存在且有信息限制 |
| 11 | 没有缺失值处理 | 通过 | 使用三值逻辑和字段级 missing policy |
| 12 | 未授权修改算法代码 | 通过 | 只创建 `design/v0/` 文档、schema 和 SVG；无 Python |
| 13 | 启动 CARLA、GPU 或训练 | 通过 | 本阶段只做文件、文本、JSON 和 XML 检查 |
| 14 | 编造当前 CARLA 可计算量 | 通过 | 未有证据的 actor/map/rule/dynamics 字段全部标成 unavailable/new log/TBD |

## 3. 跨文档不变量

- `K=2` 是校验前两个 oracle 候选，校验后可变成 0 或 1。
- 共享预处理只执行一次；候选使用同一模型实例并顺序前向。
- 当前 route shape 为 `[1,20,2]`，speed waypoint shape 为 `[1,10,2]`。
- alternative branch 不进入 PID；未来也只有 policy 选中的分支进入 PID。
- 未知硬安全、规则和时序字段不能默认安全或可行。
- `Critical = D_G OR D_S OR D_R OR D_I`；几何分歧分开记录。
- ASK timing 只是必要条件，不是充分条件。
- ASK 和 WAIT 都执行 holding；WAIT 不发送问题。
- 回答必须绑定 episode/question、重新校验，并从当前观测重生成计划。
- Oracle interpretations、answers 和 oracle ask 均为独立上界。

## 4. 尚未解决的必需定义

1. `T_WAIT_MAX`、问题传输重试、回答 expiry 和 response latency。
2. safety margin、consequence/replanning latency budget 和每 tick wall-clock budget。
3. `pred_route` 和 `pred_speed_wps` 的坐标系、单位、horizon 及 scalar-speed 转换。
4. collision、TTC、clearance 和 required deceleration 使用的 actor-motion model 与 horizon。
5. 硬安全阈值、comfort 阈值、speed tolerance 和 severe-rule taxonomy。
6. branch point、stop line、solid line、exit gore、goal region 和 legal holding location 的地图标注方法。
7. rear risk、junction blocking 和 shared corridor feasibility 定义。
8. pilot 样本量、development/test split、置信区间和晋级阈值。

这些定义只能来自交通规则、物理约束、pilot 或冻结的 development split。未配置必需运行参数时，实现必须 fail closed。

## 5. 需要用户确认的决定

1. 接受每个 clarification episode 最多成功发送一个语义问题。
2. 接受保守三值逻辑：未知的必需安全、规则和时序字段均不可行。
3. 确认 `COMMITTED` 仅因新指令、新歧义或实质失效而在新 episode 下重新澄清。
4. 确认下一阶段只开发使用 synthetic/schema-complete records 的离线 evaluator，不启动 CARLA。
5. 如果需要与缺失 Reading Guide、RP、current handoff/state 保持一致，请补充这些文件。

## 6. 修改与安全记录

仅在 `/home/buaa/wrh/DriveClarify/design/v0/` 下创建八份 Markdown、一个 JSON Schema 和两张 SVG。

DriveClarify 根目录不是 Git 仓库，因此没有 repository `git diff`。未写入 `/home/buaa/wrh/simlingo`、`/home/buaa/CARLA_0.9.15` 或 Conda 环境；未启动 CARLA、GPU inference、下载、训练、微调、LoRA 或 RL。

## 7. 审查结论

`PASS — 已完成，等待用户审查`

用户批准后的下一步：使用 schema-complete synthetic fixtures 构建并测试离线 consequence validator 和 rule cascade；该阶段仍不启动 CARLA。
