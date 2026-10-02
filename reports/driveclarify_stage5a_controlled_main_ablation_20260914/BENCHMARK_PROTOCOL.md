# 受控选择性澄清决策协议 V1

协议身份：DC_CONTROLLED_SELECTION_V1_20260914。本次先固定协议、输入摘要和算法，再生成本轮策略输出。历史 DEV/HIST 已暴露，既有结果也已知；这不是结果盲预注册。仅 CPU，不启动驾驶，不加载模型。默认关闭，所有输出隔离在本报告目录。

## 1. 总体与独立单位

仅用 C 的原始 method_inputs 与独立 label_authority，DEV/HIST 分开；预期 64/112 条、8/14 个布局，其中定义标签 48/84，未定义 16/28。输入逐条一对一连接，若身份/摘要不符则中止，不删不利记录。来源 A/B、闭环、mask 副本、时序实例不进入主分母。原始记录包含同布局下的重复措辞与配置；records 不是独立场景。

没有独立乘客意图字段。独立分歧标签的每个 base record 建立 evaluator-only BALANCED_COUNTERFACTUAL_INTENT_PAIR（K1=A，K2=B，各权重 1/2）。两个变体复用逐字相同的公开输入和预先锁定的策略建议。先聚合为一个 base record，再聚合 layout。等价记录只需一个等价类变体，回答映射测试固定 A，不宣称该回答来自乘客。未定义标签保留一个 OUTPUT_ONLY_RESPONSE_A 服务映射诊断：如建议 ASK，评估器在建议之后提供固定 A；不为其指定“真实任务”，所有正确/错误、unnecessary/missed 字段均为 null。这个附加回答只使 ASK resolution 的覆盖率可测，不证明缺失证据下的选择正确。另列 ASK 前原始输出分布。

## 2. 真值隔离及执行次序

1. prepare/predict 进程只读取原始 method_inputs，投影公开白名单：候选、观测、任务义务、已有候选未来、自车/公共路线/原始指令。sample_id 换为不含配置语义的摘要 ID；split/layout/source ID 只留在索引，不交策略。
2. 三策略只接收公开对象。公共顺序始终 K1、K2，固定默认 K1；不按真值或回答排序。检查两候选 ID 不同且文本非空。这里候选的合理性是历史输入提供的受控前提，不是新 grounding 结果。
3. 先写 PUBLIC_INPUTS、POLICY_DECISIONS 并锁摘要；评分进程验证锁后才连接标签、创建意图变体和回答。答案仅在策略已返回 ASK 后应用，不参与是否 ASK 的判断。策略模块不得读文件或导入标签实现。
4. P2 必须实际调用 `judges_revised.evaluate_c_m5_revised`；该冻结函数先计算保存数组的轨迹旁证，再由完整合法 task obligations 判定；无任务依据闭合 UNKNOWN。旧注释与实际代码不同处以实际函数为准（没有拓扑时也不接纳 grounding/trajectory 一致作为充分任务依据）。旧 live 判断器不修改、不冒名。

## 3. 策略与受控服务

- NO_CLARIFICATION：合法双候选选择 K1，零 ASK；无合法候选 unresolved。
- IMMEDIATE_QUERY：合法双候选即 ASK，不计算关系；有效受控回答映射至其候选。
- DRIVECLARIFY：revised EQ→K1；DV→ASK；UNKNOWN→ABSTAIN。query-available=true 是独立受控条件，不是原生 QueryOK，也不通过 CPU 原型的未知时钟取得驾驶授权。

评分不模拟回答错误/延迟、执行失败或 WAIT 控制。分歧子集上受控回答正确提供意图，故立即询问是此条件下的理想答案基线。新 forward/CUDA/驾驶均为 0；函数返回的候选 forward 计数属于保存证据的历史成本，不能写成新调用，也不能据此声称在线算力节省。

## 4. 指标与分母

每个变体先计算结果，按 base 内权重平均。正确：已选择且匹配意图任务，或独立 EQ 类内任一合法候选。错误承诺：独立 DV 且确定选择另一候选。ABSTAIN 的 correct=0、wrong=0、unresolved=1，含义是既没有正确决策事件也没有错误承诺事件；不把它重编码成错误类别。另输出 correctness=null 供“已决策准确率”排除 unresolved，报告 resolved_accuracy 与总体 correct_decision_rate 区分。

主表正确决策/错误/询问/未决/覆盖均使用标签定义 records；正确决策分母包括未决以不抬高成功比例，同时未决单列。未定义记录的正确/错误事件也为 null，永不参与正确率。另输出全部 records 的 query、coverage、unresolved 及标签未定义输出分布。

unnecessary_query 仅以独立 EQ 为分母；missed_clarification 仅以独立 DV 为分母，事件为 no ASK 且 wrong。DV 的半数错误可表现为每 base 0.5，计数称“基记录等权事件量”，不是实际乘客事件。

## 5. 统计冻结

只比较 P2−P0 与 P2−P1。输出事件量/分母、记录率、每布局率及布局均值/中位数/IQR。区间对同 split 的配对 layout 指标差作有放回 cluster bootstrap：重采样 layout，保留其所有 base/变体；每次取布局差均值，10,000 次，numpy RNG seed=20260914（仅统计复现种子，无驾驶种子）。95% percentile CI，无 p 值。DEV/HIST 各自抽样；pooled 只描述不作 CI。只有 8/14 个布局；重复且平衡的设计可产生零宽 CI，不能解释为无总体不确定性。布局均值是推断目标，记录率仅描述，两者不混称。

## 6. 消融和机制数据

Task gate 消融直接引用 IMMEDIATE_QUERY 同一套结果，效应定义 ablated−full，不再跑另一基线。Memory 消融从原始/扩展保存的同轨迹 B1/B2 结果重新汇总（不是重跑 frame-level reducer，更不是闭环 WAIT 反事实）。原始 48 计划/47 端点，扩展 24/23；不完整端点另存 null 和原因，不补零。核对 same_source_identity_all_views 及 rule trace_digest。B1=current observation，B2=有效历史保留。sufficient、window、unresolved=无充分证据分别计数；first time/margin 只在首次充分时有值。

Timing 消融复用 B2 evidence-only 与 joint 保存规则；共同冻结 reserve=1.20s，deadline=commitment−reserve，margin=deadline−first sufficient。严格沿用记录的 actionability，不调整历史等号规则；提取器核对触发时正 margin（本数据不含精确零）与分类一致。timely=trigger before frozen protocol condition，late=after；不称物理 unsafe。报告 trigger、late、存在窗口、错过已有窗口、证据来晚、无充分证据、规则未触发。对没有任何可用窗口的 late 事件，不称错失实际可行机会。

轨迹机制：B 两臂分别保留（首帧不同），C DEV/HIST，C 阈值 0.10m、B 0.27119792945561905m，距离 > 阈值为 DV，否则 EQ；不调阈值。仅保存的等时间轨迹比较，不用等距离几何头冒充时间轨迹。标签未定义仍落 CSV，agreement=null。

## 7. 解释边界与交付

标签为自动地图测量，尚未人工双标；方法和标签是独立实现，但共享地图事实。特别保留约 0.72/0.73m 道路长度差所涉语言可辨识风险，不能声称视觉 grounding 或 unseen 泛化。任务结构完整时拓扑优先，本数据不能证明 futures 的增量价值。三策略收益是给定任务证据与理想答案下的受控 trade-off。

闭环证据另列实际回答/历史重规划/原生完成及各自证据缺口。公开明确任务的 RC=100 保留 MinSpeedTest FAILURE；不当方法样本。原三策略真实分支闭环设计停止作为主实验前提。未来唯一建议是独立复核本轮冻结协议、实现与主张，不启动路线工作。
