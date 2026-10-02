# 最新：图3/图4版式修复

图3必要标题、坐标、说明和图例已恢复；图4改为7.12×2.85英寸紧凑版式。两图数据不变，当前稿所有其他用户编辑保留，仍8页。详见../figure34_layout_revision_20260916/FINAL_REPORT.md。

# 8 页论文与离线增补交付（2026-09-16 更新）

**当前稿已实际编译为 8 页。离线增补与排版已完成；新增真实分支闭环和完整三故事 RGB 采集尚未完成，不能将整个实验目标标为完成。**

## 最新修改：主表与 Fig. 4

主表已按用户方案一移除两项 coverage，改为三组六指标，新增 HIGH Query Recall 与经逐条正确 ACT 验证的 LOW Direct-Act。Fig. 4 图内文字减少约70%，说明转入图注；原始数据未改。详细公式、逐行来源和本轮验收见 `MAIN_TABLE_REVISION.md`。当前 PDF 仍严格8页。

## 最新补充：Table IV 文献算法

已按用户要求加入SimLingo原论文Table 2的12条算法/配置，保留用户A0/A1两行，总14行。使用共同DS/SR列并保留训练专家/蒸馏信息；RC、碰撞率和ASK移入正文。文献结果与用户提供结果分组，未新增驾驶。详见 `../bench2drive_table_revision_20260916/FINAL_REPORT.md`。最终仍8页，Table IV在第8页。

## 文件入口

- 论文 PDF：`/home/buaa/wrh/DriveClarify/paper_ieeeconf_en/build/main.pdf`
- LaTeX 源文件：`/home/buaa/wrh/DriveClarify/paper_ieeeconf_en/main.tex`
- 可编译包：本目录 `DriveClarify_8page_LaTeX.zip`，PDF 位于包内 `paper_ieeeconf_en/build/main.pdf`。
- 逐记录结果：`data/main_record_results.csv`；当前主表汇总：`data/main_selectivity_summary.csv`；原评分汇总保留 `data/main_summary.csv`。
- 配对统计：`data/paired_layout_statistics.json`、`data/paired_ablation_statistics.json`、`data/historical_closed_loop_statistics.json`。
- 来源与验收：`figure_sources.json`、`DELIVERY_CHECKS.json`、`PACKAGE_CHECKS.json`。
- 用户最新分工：`SCOPE_UPDATES.md`。

## 本轮完成内容

冻结的 176 条输入（132 条有定义标签、44 条 undefined）和 22 个布局未改。新增 baseline 先输出并锁定预测，再连接评分标签；共 1,056 个记录—策略结果。30 份冻结来源摘要一致，原有三策略逐条指标及轨迹关系判定复现一致。未新增模型 forward、CARLA episode 或训练。

| 主表策略 | Correct % | Wrong task % | Query % | LOW unnecessary % | HIGH recall % | LOW direct-act % |
|---|---:|---:|---:|---:|---:|---:|
| Never Clarify | 83.33 | 16.67 | 0.00 | 0.00 | 0.00 | 100.00 |
| Always Clarify | 100.00 | 0.00 | 100.00 | 100.00 | 100.00 | 0.00 |
| Ambiguity-Only | 100.00 | 0.00 | 100.00 | 100.00 | 100.00 | 0.00 |
| Trajectory-Distance | 86.74 | 13.26 | 18.18 | 17.05 | 20.45 | 82.95 |
| Random-Query（解析期望） | 88.89 | 11.11 | 33.33 | 33.33 | 33.33 | 66.67 |
| DriveClarify | 100.00 | 0.00 | 33.33 | 0.00 | 100.00 | 100.00 |

正确/错误指标属于给定候选、结构化证据、理想回答和平衡反事实意图下的受控任务选择评估。每个 divergent 输入的两个意图等权评分，不构成两次真实乘客事件。所有方法在定义子集覆盖率为 100%；DriveClarify 对 44 条 undefined 弃权，全输入覆盖率 75%、全输入询问率 25%。其他方法的全输入覆盖率不表示 undefined 输入有已知正确性。

本数据每条输入都有两个有效候选，因此 Ambiguity-Only 与 Always Clarify 行为相同；六行不是六种不同实测行为。Random 行是每条输入以 1/3 概率询问的解析期望，没有挑选随机种子或最佳随机实现。轨迹阈值沿用冻结的 0.10 m，未调参。

配对推断分别重采样 DEV 的 8 个布局与 HIST 的 14 个布局，20,000 次 bootstrap。DriveClarify 相对轨迹 baseline 的正确率差为 DEV +14.58 pp（95% CI 11.46–16.67）、HIST +12.50 pp（8.93–15.48）。其他若干对照的布局差固定，零宽经验区间反映平衡构造，不意味着对新场景没有不确定性。分数型意图权重不适用二元 McNemar。

消融单列一张表，按各自分母报告受控主实验、原始 47 个时序端点和扩展 23 个端点。历史证据将充分端点从 17→29、0→17；及时端点分别为 12、7。去除时间判据额外放行 17、10 个晚触发。配对消融统计另附；原始按 seed 聚类、扩展按模板聚类，均仅 6 个簇，不作广泛泛化结论。两条缺失端点保留缺失。

## 论文结构与图

实验章只有 `Main Results`、`Ablation & Mechanism Analysis`、`Closed-Loop Validation` 三个一级块。六行主表、机制图与 CARLA 图跨栏；消融一表一图。按用户最新全局原则先写 `FIGURE_NECESSITY_AUDIT.md`，移除只重复计数的 family 柱图，主结果和 Bench2Drive 保留表格。机制图展示逐 episode 的 Current→History 状态转移矩阵、全部 132 条距离的 ECDF、首次证据充分时间与干预余量的真实坐标和边际箱体。所有长尾保留，两个时序 panel 共用 symlog 轴，标明线性区和零边界，没有随机 jitter 或 inset。长期规则保存于 `docs/paper/EXPERIMENT_FIGURE_PRINCIPLES_20260915.md`。

CARLA 图已按用户参考重做为 **2 行 × 4 列**，两行分别是历史 LMK-C/P02（HIGH）和 LMK-E/P06（LOW）。列依次为真实资格阶段场景+冻结候选、原生地图任务区域、新局部模型计划、双臂完整速度曲线。四臂共 2,741 帧，未裁掉速度峰值；询问、任务完成与碰撞结果对照历史配对 CSV。两例均没有 task-correct safe completion，图中如实标 0/0；资格照片没有冒充正式 RGB 时序。完整来源、PDF/SVG/PNG 与验收见 `../qualitative_revision_20260916/FINAL_REPORT.md`。旧 DEMO01 拼接图仅保留在其 `prior_version/`。

按用户最新要求，**方法图与开头图由另一台电脑绘制**。当前正文逐字节使用压缩包原图，早期草稿未进入交付包。替换接口分别为 `figures/method_overview_unified_v2.pdf`（跨栏）和 `figures/intro_story.pdf`（单栏）。新版图长宽比可能改变页数，收到后须再次编译。

原稿 Algorithm 1 与全部六个编号公式保留；主要压缩重复解释文字并合并实验展示。编译产物全部进入 `build/`；当前无 overfull box、无未解析引用，八页逐页渲染已检查。原宏包的 UTF-8 注释、导入 PDF 版本和 underfull box 仍有非致命警告，不宣称零警告。

## 闭环与 Bench2Drive 的实际边界

历史生命周期数据为 34/35 可评估：19 HIGH 均有 ASK/回答/重规划记录，15/19 路线完成；15 LOW 无 ASK 且路线完成。所有可评估 episode 都记录 stop-sign infraction，不能写成零违规安全结果，也不能据此认证最终任务正确。

历史局部停靠配对为 24 HIGH + 11 完整 LOW，另有一个 LOW baseline 崩溃。两臂 task-correct safe completion 均为 0。HIGH 风险差 0 pp，McNemar p=1，保守 95% CI [-14.25,14.25] pp；共同失败不能证明等价。这批历史实验未冒充本轮新增真实分支实验。

220 条 Bench2Drive 汇总按用户确认使用原论文（另一台机器完成）：A0/A1 的 DS 90.03/91.91、RC 98.54/99.60、SR 76.82/77.73%、碰撞路线 15.45/12.27%、ASK 0/0。**本机仍没有可给出的 A0/A1 最终逐路线结果路径。** 因此没有补算 paired CI，也没有把数值差归因于主动澄清或声明统计非劣效。Table IV已补入SimLingo论文的12条已发表驾驶对照，明确来源和配置差异，未将其作为受控主动澄清基线。

## 历史原生流程的一致性补充

逐条核对局部停靠研究全部36次A1后，确认安装后34,129条记录均保留原始模糊指令；24个HIGH的首个新局部输出与答案同帧，后续更晚帧预测存在。约1.80s余量来自固定3s协议减1.20s预留。图文已明确这三个边界。新增独立答案绑定接缝通过11项CPU检查和实际环境import，尚未完成真实驾驶资格，不计作新实验或方法收益。详细证据见`reports/driveclarify_paper_runtime_fidelity_20260916/FINAL_REPORT.md`。

## 尚待完成的两项

1. **12–20 组新增真实分支严格配对运行。** 尚无通过资格检查、可直接交出的 HIGH/LOW 真实分支场景路径。已有资产资格失败记录：`reports/driveclarify_stage4a_three_policy_native_smoke_prep_20260914/FINAL_REPORT.md`（走廊内 ±0.65 m 偏移未形成不同道路分支）、`reports/driveclarify_stage4b_native_branch_pair_mining_20260914/FINAL_REPORT.md`（完整原生 XML 库未找到满足合同的分支对）。没有将“存在历史启动命令”当作“新分支场景已验证可运行”。
2. **HIGH/LOW/时序各自 3–5 帧 RGB 的完整故事。** 现有正式案例缺 RGB，无法从事件日志恢复真实像素；当前两行四列图属于可追溯的部分交付；其两张资格照片不能替代正式运行的逐帧相机记录。后续需要实际合格场景的相机归档、独立目标判定及配对运行记录，才能展示 baseline 错任务而 DriveClarify 正确完成。

## 复现

LaTeX 包解压后在 `paper_ieeeconf_en/` 执行 `bash build.sh`；编译器需要 latexmk+pdfLaTeX 或 Tectonic，页数检查需要 pdfinfo。本机 Tectonic 已验证。

以下分析命令在现有仓库根目录执行（依赖冻结源模块/数据，分析附件本身不是独立 CARLA 运行环境）：

```bash
python deliverables/paper_revision_20260915/analyze.py predict
python deliverables/paper_revision_20260915/analyze.py analyze
python deliverables/paper_revision_20260915/analyze.py closed-loop
python deliverables/paper_revision_20260915/ablation_statistics.py
python deliverables/paper_revision_20260915/render_figures.py
python deliverables/paper_revision_20260915/main_table_selectivity.py
python deliverables/paper_revision_20260915/revise_paper.py
bash paper_ieeeconf_en/build.sh
python deliverables/paper_revision_20260915/audit_delivery.py
```

预测锁、协议及来源应一起保留；不要把本次离线主实验、历史运行版本、未执行的另批实验拼成一次端到端验证。
