# 代码与接口

对外只有 `driveclarify` 包和 `driveclarify` 命令。模块按责任组织：

| 模块 | 职责 |
| --- | --- |
| `core` | 任务关系、任务签名、轨迹及证据比较 |
| `temporal` | 证据有效期、询问截止条件与模拟时间统计 |
| `runtime` | 回答、候选、导航提交和新观测之间的绑定 |
| `native` | 原生 SimLingo 委托、执行监督和共有观测 |
| `tools` | 模型下载、后端还原、运行计划与结果收集 |
| `resources` | 固定输入、模型身份、后端补丁和路线 |

```python
from driveclarify import compare_tasks, AnswerBoundExecution

# value 满足 core.contracts 中的公开输入合同；不包含真值或答案。
result = compare_tasks(value, config)
```

任务关系输出为 `TASK_EQUIVALENT`、`TASK_DIVERGENT` 或 `UNKNOWN`。候选轨迹可以提供旁证；缺少任务结构证据时，单靠轨迹差异不能提升成确定任务分歧。配置默认轨迹阈值 0.1 m、对齐步长 0.05 s、窗口 2.5 s。未知证据保留为未知。

`AnswerBoundExecution` 必须先登记持久化询问，再接收匹配的回答。回答只能结合严格晚于回答帧的观测形成执行请求；导航 owner 提交失败时，原指令保持有效。原生接口不自行增加模型前向或第二个 ego 控制写入者。

原生入口为 `driveclarify.native.agent.DriveClarifyAgent`，由答案绑定、共有观测、时间条件、任务后果和原生导航委托组合构成。底层分层是同一个运行时的不同职责，不是可切换的多个开发版本。这个带真实回答的入口仍需目标场景的 GPU 资格验证；当前可启动的完整标准基准使用 `native.benchmark_agent` 的原生透明旁路。

受控评测包含 176 条保存输入、132 条任务真值有定义的输入、44 条未定义输入和六种策略。保存候选未来不代表新增 VLA 推理；评分是等权反事实意图下的离线计算。`resources/expected-*` 是算法回归数据。

模型权重、220 条路线字节、路线种子和 A0/A1 交替次序保持原身份。上游调试探针只保留禁用时的恒等行为；发布实现不收录启用的过程实验、影子控制或可视化分支。原生源文件及本包资源由摘要清单检查，运行副本另有完整摘要。

