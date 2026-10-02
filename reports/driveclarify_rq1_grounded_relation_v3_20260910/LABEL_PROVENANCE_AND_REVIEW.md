# 标签来源与复核

标签权威实现 `driveclarify_rq1_grounded_relation_v3/labels.py`，
落盘 `label_authority/{DEV,HIST}_LABELS.jsonl`（64 + 112 = 176 行）。
标注版本恒 `AUTOMATED_LABEL_V1_PENDING_HUMAN_REVIEW`，
`human_double_annotation_completed: false` 逐行记录。

## 1. 真值定义

任务等价 = 两候选指代表达在该布局公开 OpenDRIVE 上解析到的
**(分支任务等价类, 规定执行位置)** 元组相等。逐行记录
`same_branch_task_equivalence_class` 与 `same_execution_location` 两个分量，
便于复核是哪一维造成分歧。

重要限定：**重新汇聚到同一目的地不自动等于任务等价**。
`diagnosis/reconvergence_probe.py` 在公开地图上做车道级后继 BFS，
在 60/120/200/400 m 四个视距下检验分支是否重合，结果 8 个开发布局中
**只有 1 个**（Town03 j498）在 200 m 内汇聚。据此排除"汇聚即等价"这一定义。

第四态 `LABEL_UNDEFINED_UNDER_ALLOWED_INPUTS`：指代表达在全部允许输入下不可解析
（本轮即 `roadside_building_appearance` 需要观测里的建筑外观，不在允许输入内）。
该状态是**证据条件标注**，不是一般不可识别性证明。

## 2. 标注侧独立实现

| 环节 | 标签侧做法 | 方法侧做法（对照） |
|---|---|---|
| 表达识别 | 短语包含判断，内部符号 `EXTENT_MAX/MIN`、`CURVATURE_MIN/MAX`、`APPEARANCE_NOT_IN_MAP` | 正则表面形式表 |
| 道路长度 | `road/@length` | `road/@length`（**同一属性，已披露**） |
| 转向幅度 | `planView/geometry/@hdg` 首尾差 + arc 曲率修正 | 车道中心线采样首尾切向差 |
| 序数词 | `labels._ORDINAL_WORDS` 独立词表 | `taskstructure` 自带词表 |

`labels.py` 不 import `methods.py` / `taskstructure.py` / `referring.py`。
逐行落盘 `full_relation_function_called: false`、`method_output_read: false`，
176/176 全部为 false。`derived_from` 记为
`PUBLIC_OPENDRIVE_INDEPENDENT_LABEL_SIDE_MEASUREMENT` +
`LABEL_SIDE_INDEPENDENT_REFERRING_RESOLUTION`。

机制重叠（后继道路长度确实读同一 XML 属性）已在 `INDEPENDENCE_AUDIT.md` 主动披露，
不当作已消除的问题。

## 3. 标签在预测前固定

标签由 S2/S8 装配阶段写盘，早于 S5/S9 的预测；
`firewall.join_after_lock` 先校验预测锁哈希、之后才读标签文件。
证据不足子集的 UNKNOWN 标签**在预测前就已确定**，不存在事后补标签。

## 4. 自动标注的自检（`diagnosis/DESIGN_CHECK.json`，6 项全部成立）

| 检查 | 内容 | 实测 |
|---|---|---|
| C1 | 候选文本不含机动方向词 | 成立，0 违例 |
| C2 | 同一配置跨布局真值变化 | `CFG_LONGER_VS_STRAIGHTER` 与 `CFG_LONGER_VS_SHARPER` 均同时出现两种真值 |
| C3 | 纯词法不再充分 | M_LEX 全 176 返回 UNKNOWN |
| C4 | 两套独立解析器一致 | 一致 132，不一致 **0** |
| C5 | 对照配置行为符合规定 | `CFG_SAME_EXPRESSION` 全等价 44；`CFG_EVIDENCE_INSUFFICIENT` 全未定义 44 |
| C6 | 每布局两主配置互补 | 互补 44/44，不互补 0 |

真值分布：`TASK_EQUIVALENT` 88、`TASK_DIVERGENT` 44、
`LABEL_UNDEFINED_UNDER_ALLOWED_INPUTS` 44（DEV 32/16/16，HIST 56/28/28）。
标注争议 `annotation_dispute` 计数 **0**（该字段只在 `CFG_SAME_EXPRESSION` 违反恒等价时置位）。

`declared_config_expected_truth` 对两个主配置写
`LAYOUT_DEPENDENT_NOT_PREDECLARED`——真值不能从配置名预先声明，必须由地图解析得出。
只有对照配置 `CFG_SAME_EXPRESSION` 预声明为 `TASK_EQUIVALENT`。

## 5. 标注脆弱性（需人工复核的具体项）

C4 的一致性是**两套独立实现算出同一答案**，说明答案由公开地图唯一确定；
但它不检验该答案是否符合人类对指代表达的语言直觉。以下是量化的薄弱处：

**属性差额过窄。** 220 条 `extent_m` 解析的差额中位数 34.43 m，最小 **0.72 m**：

| 布局 | 差额 | 涉及条目 |
|---|---|---|
| `TOWN01_JUNCTION_26_UNIT01` | 0.73 m | 10 |
| `TOWN04_JUNCTION_483_UNIT01` | 0.72 m | 10 |

共 16 个样本落在 1 m 以内。"通向更长那条路的出口"在 0.72 m 差额下虽然
几何上唯一确定，但人类观察者未必认同这构成可辨识的指代——
**这是人工复核应优先看的子集**。`curvature_abs_deg` 无此问题
（88 条差额最小 2.47°，中位数 89.95°，绝大多数是直行对 90° 转弯）。

**未做的复核。** 人类双标注未做，故不存在标注者间一致性（Cohen's κ 之类）数字，
本轮**不声称**人类一致性检验完成。需要人工判断的是：
指代表达的语言可辨识性（尤其上表 16 个窄余量样本）、
`CFG_EVIDENCE_INSUFFICIENT` 是否确实无法从 RGB 之外解析、
以及四族标签仅作分层报告这一处理是否可接受。

## 6. 与 v2 标签的关系

v2 的 `labels.py:_independent_direction` 从候选文本里的方向词推等价类，
与方法侧同源，构成词法同义反复（`diagnosis/LEXICAL_SUFFICIENCY.json`，D1/D2 成立、
M_LEX 达 1.0）。v3 标签**不复用** v2 的任何标签、fixture 或等价类定义。
v2 目录原样保留，未修改、未覆盖、未重跑。
