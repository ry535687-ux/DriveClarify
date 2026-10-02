"""把本轮结果写入审阅报告；数字读取已生成 JSON，不重算历史结果。"""
from common import *
import platform
import numpy as np
import matplotlib


def run():
    main = read_json(REPORT / 'MAIN_COMPARISON.json')
    ablation = read_json(REPORT / 'ABLATION_RESULTS.json')
    bdir = REPO / 'reports/driveclarify_stage3c_b_native_runtime_20260914'
    native = read_json(bdir / 'B_NATIVE_RESULT_SUMMARY.json')
    layers = read_json(bdir / 'B_TEN_LAYER_RESULTS.json')
    histpath = REPO / 'reports/driveclarify_rq3_v3_technical_validity_recovery_and_resume_v1/PART_B_FINAL_RESULTS.json'
    hist = read_json(histpath)
    evidence = {'included_in_primary': False, 'new_runs': 0,
                'historical_RQ3': {'planned': hist['planned_episodes'], 'evaluable': hist['technically_evaluable']['decision'],
                    'HIGH_ASK_and_historical_lifecycle': hist['HIGH_lifecycle_composite'],
                    'native_completion': hist['native_completion'], 'support': hist['H_RQ3_B_without_H_RQ3_C_conjunction'],
                    'new_plan_control_consumption_independent_certification': None,
                    'independent_task_correctness_certification': None,
                    'stop_sign_events_in_legacy_endpoint': hist['official_infractions']['stop_sign']},
                'clear_B': {'native_completion': native['route_completion'], 'native_status': native['status'],
                    'safety_status': layers['results']['NATIVE_SAFETY_ENDPOINT']['status'], 'MinSpeedTest': 'FAILURE',
                    'min_speed_records': len(native['infractions']['min_speed_infractions']),
                    'layers': layers['results'], 'new_clarification_effect_sample': False},
                'clear_A': {'role': '失败诊断；无最终原生完成证据，不纳入主结果'},
                'source_files': [{'path': str(p.relative_to(REPO)), 'sha256': sha(p)} for p in
                    (histpath, bdir/'B_NATIVE_RESULT_SUMMARY.json', bdir/'B_TEN_LAYER_RESULTS.json', bdir/'B_ROUTE_PLAN_CONTROL_SUMMARY.json')]}
    write_json(REPORT / 'evidence/CLOSED_LOOP_EVIDENCE.json', evidence)
    write_json(REPORT / 'evidence/ENVIRONMENT.json', {'python': platform.python_version(), 'platform': platform.platform(),
        'numpy': np.__version__, 'matplotlib': matplotlib.__version__, 'plot_backend': 'Agg',
        'new_CARLA': 0, 'new_model_forward': 0, 'new_CUDA': 0, 'new_training': 0})
    policy_names = {'NO_CLARIFICATION': '不澄清', 'IMMEDIATE_QUERY': '立即询问', 'DRIVECLARIFY': 'DriveClarify'}
    def count(v):
        return f"{v['numerator_base_equivalent']:g}/{v['denominator_records']} ({100*v['rate']:.2f}%)"
    table = '| 切分 | 策略 | 正确决策 | 错误任务承诺 | 询问 |\n|---|---|---|---|---|\n'
    extra = '| 切分 | 策略 | 等价子集不必要询问 | 分歧子集错失澄清 | 未决 / 覆盖（标签定义） |\n|---|---|---|---|---|\n'
    for split in ('DEV', 'HIST'):
        for policy in POLICIES:
            s = main['splits'][split]['policies'][policy]['defined']
            table += f"| {split} | {policy_names[policy]} | {count(s['correct_decision'])} | {count(s['wrong_task'])} | {count(s['query'])} |\n"
            extra += f"| {split} | {policy_names[policy]} | {count(s['unnecessary_query'])} | {count(s['missed_clarification'])} | {s['unresolved']['rate']*100:.0f}% / {s['coverage']['rate']*100:.0f}% |\n"
    memory = '| 总体 | 端点 / 计划 | 当前观测：充分 / 窗口 / 无充分证据 | 有效历史：充分 / 窗口 / 无充分证据 |\n|---|---|---|---|\n'
    timing = '| 总体 | 规则 | 总触发 | 协议条件之后触发 | 及时触发 / 可行动机会 | 未触发 |\n|---|---|---|---|---|---|\n'
    for source, t in ablation['temporal'].items():
        b1, b2 = t['memory']['B1'], t['memory']['B2']
        memory += f"| {source} | {t['complete_endpoints']}/{t['planned_executed']} | {b1['evidence_sufficient']} / {b1['actionable_windows']} / {b1['unresolved_no_evidence']} | {b2['evidence_sufficient']} / {b2['actionable_windows']} / {b2['unresolved_no_evidence']} |\n"
        for rule, s in t['timing'].items():
            timing += f"| {source} | {rule} | {s['trigger']} | {s['late']} | {s['timely_trigger']} / {s['window']} | {s['rule_unresolved']} |\n"
    trajectory = '| 保存来源 | 标签定义 / 总记录 | 近但任务分歧 | 远但任务等价 | 关系一致 |\n|---|---|---|---|---|\n'
    for source, s in ablation['trajectory_mechanism'].items():
        trajectory += f"| {source} | {s['defined']}/{s['records_all']} | {s['close_but_divergent']} | {s['far_but_equivalent']} | {s['agreement']}/{s['defined']} |\n"
    chapter = '''# 实验章节 V3：受控决策、消融与闭环执行验证

## 4.1 Experimental Setup

我们将实验分为受控任务选择、保存轨迹上的机制消融和历史闭环执行验证。前者研究在给定候选解释和合法任务证据时，是否需要询问；后两者分别检验证据累积与协议时机的作用，以及已有执行组件被实际使用到的层级。本轮所有计算均在 CPU 上完成，使用保存输入和历史结果，没有新增模拟驾驶、模型前向或训练。

主数据包含已暴露的 DEV（64 条记录、8 个布局）与 HIST（112 条、14 个布局）。其中可定义任务关系的记录分别为 48 和 84，其余 16 和 28 条保留作输出与覆盖分析。每个布局只有一个保存观测，包含不同配置和措辞；176 条记录或 132 条定义标签记录均不代表等量独立场景。两个切分都用于既有开发或分析，不能据此评价未见布局泛化。

标签侧独立实现公共地图测量及指代表达解析，不读取方法输出；方法侧使用候选任务义务与保存的同观测未来。两侧共享公开地图事实，标签仍是待人工复核的自动标注，因此这里的隔离是实现和输出隔离，并非信息来源完全独立。候选解释与任务证据由受控输入提供，不评价端到端视觉 grounding。任务等价依照分支任务等价类及规定执行位置定义，不等同于终点相同。

原记录没有独立的乘客意图。对每条独立任务分歧记录，我们仅在评估侧设置 A/B 两个等权反事实意图，策略输入完全相同；先在原记录内平均，再按布局汇总。等价记录的任一合法候选均属于正确任务类。反事实变体不是新增场景，也不是实际乘客回答。所有策略的公开默认候选固定为 K1。立即询问在两候选有效时建议 ASK；DriveClarify 实际调用修订后的关系函数，等价时选择默认、分歧时询问、证据不足时 abstain。受控答案在 ASK 建议之后才提供，且正确反映评估侧意图；这是理想答案服务条件，未模拟回答噪声、延迟或驾驶执行权限。

统计报告事件量、记录率、布局均值、中位数和四分位数。比较仅包括 DriveClarify 对不澄清、对立即询问；区间以同切分配对布局为抽样单位进行 10,000 次 cluster bootstrap，不将反事实变体或同布局记录作为独立场景。合并结果仅作描述。

## 4.2 Main Experiment: Controlled Selective Clarification Benchmark

本实验问题是：在上述受控条件下，选择性澄清是否降低错误任务承诺，同时避免不必要询问。正确决策事件要求明确选择正确任务；错误承诺仅指已选择分歧的错误任务。abstain 既不是正确也不是错误承诺，单列未决，且不用于抬高已决策准确率。未定义标签不进入任何正确率分母。

''' + table + '\n' + extra + '''
分子是基记录内反事实等权的事件量，不是实际乘客事件计数。在两个切分中，DriveClarify 相对不澄清减少错误承诺 16.67 个百分点；相对立即询问，正确决策率差为 0，询问率减少 66.67 个百分点。在独立等价子集，不必要询问由 100% 降为 0；在独立分歧子集，不澄清的错失澄清率为 50%。这些分母由标签侧关系确定，不按方法预测的子集筛选。

各布局的上述比例恰好相同，故布局均值、中位数和第一/第三四分位数均等于相应记录率。DEV 8 个布局及 HIST 14 个布局各自的错误承诺差 95% CI 均为 [−16.67, −16.67] 个百分点；询问差均为 [−66.67, −66.67]，正确决策差（对立即询问）为 [0, 0]。零宽区间来自重复、平衡的设计及理想答案，并不表示真实总体不确定性为零；不提供泛化或人机交互效应的显著性结论。合并描述为不澄清正确 110/132、错误 22/132，另两策略正确 132/132；DriveClarify 询问 44/132。

44 条未定义标签全部保留：DriveClarify 对其输出 abstain，因而全部 176 条输入上的覆盖为 132/176（75%），未决为 25%，询问为 44/176（25%）。不澄清在该子集选默认，立即询问建议 ASK；其输出映射诊断固定返回 A，二者均有确定候选，不能据此认定选择正确。包括该服务诊断时，二者全部记录的覆盖为 100%，询问率分别为 0 和 100%。标签定义子集的三策略覆盖均为 100%、未决为 0。覆盖高低与任务正确性需要分开理解。

## 4.3 Ablation Study

### w/o Task-Consequence Gate

移除任务后果门等价于主比较的立即询问基线，复用同一份输出，未生成第二套基线结果。DEV/HIST 中，不必要询问增加 100 个百分点、整体询问增加 66.67 个百分点，正确决策率不变。该结果说明，在理想回答和既定等价/分歧构成下，任务后果门主要减少询问负担；它不证明真实乘客更满意，也不证明候选未来本身带来增量收益。

### w/o Temporal Evidence Memory

本消融重汇总保存的同轨迹 B1（当前观测）和 B2（有效历史证据保留）分析。每对视图共享轨迹及其记录身份，不是不同 WAIT 控制下的两次驾驶。原始与扩展总体分开，原始 1/48 和扩展 1/24 的端点未完成实例继续保留；其完整时间端点为缺失，不填零。

''' + memory + '''
去掉记忆使原始总体的充分证据率减少 25.53 个百分点、可行动窗口率减少 12.77 个百分点；扩展总体分别减少 73.91 和 30.43 个百分点。原始异步层 18 条中，充分证据由 0 增至 12，可行动窗口由 0 增至 6；同步层 12 条的两种视图均充分 12、窗口 6。扩展 ACTIONABLE 层为 B2 充分/窗口 7/12；TOO-LATE 层充分 10/11、窗口 0/11。保留有效历史并不保证及时性。

### w/o Timing Constraint

完整规则要求证据与冻结协议时间条件共同成立；消融规则在证据充分时即建议 ASK。协议预留固定为 1.20 秒，剩余余量等于 commitment reference 减首次充分时刻再减预留。以下统计来自保存 episode 内的规则回执及其共享 trace 身份，不复制旧正文后拟合数字。

''' + timing + '''
去掉时间约束使原始/扩展总体分别增加 17/47（36.17 个百分点）和 10/23（43.48 个百分点）的协议条件之后触发，及时触发数不变。两规则都未错过已有的可行动机会（0/12、0/7）；联合规则另外保留证据来晚且没有可用窗口的 17、10 条，以及证据始终不充分的 18、6 条。未触发总数分别为 35/47 和 16/23，不能把这些 abstention 当作完成任务。历史分类名中的“错失窗口”在这里明确区分为证据来晚，并不虚构此前存在可用窗口。这里不把 late 称作物理不安全或不可恢复。

## 4.4 Mechanism Analysis

### Trajectory Difference vs. Task Relation

比较保存的等时间候选轨迹距离与独立任务关系。原历史两臂使用 0.27119792945561905 米阈值，主数据使用 0.10 米，均保持原冻结值。历史两臂的首决策帧不同，因此分别展示，不加入主策略分母。

''' + trajectory + '''
近但分歧与远但等价均存在，说明短期轨迹距离不能直接替代任务关系。C 的 44 条未定义标签仍在绘图数据中，但不强行放置真值纵坐标。历史两臂计划 28 条、保存可用首记录 27 条，缺失的一条保留原生技术中断事实。该图不证明某个新融合器的泛化能力；修订规则在合法任务结构可用时由拓扑主导，本数据不支持 futures 相对任务结构的额外贡献。

### Evidence Sufficiency vs. Intervention Margin

连续点数据逐条保留首次充分时刻、commitment reference、deadline、预留、剩余余量、视图及端点状态。原始 B1/B2 的首次充分余量中位数都约为 −0.300 秒，但样本分别为 17、29，不能把两者的条件分布中位数当作相同总体的因果效应。扩展 B1 没有充分点，B2 为 17 点，中位数约 −0.900 秒；余量为正的个别实例可超过 40 秒，全部保留，不截尾。图同时给出全尺度和明确标注的边界细节，不用小样本平滑 violin。零线对应已扣除 1.20 秒预留后的边界。

## 4.5 Closed-Loop Execution Validation

历史受控执行的 35 个计划实例中，34 个技术可评价。19 个需要澄清的实例记录了 ASK、答案接收与历史 Full Replan 生命周期；其中 15/19 原生完成，15 个直接执行实例为 15/15，总计 30/34。这里的 Full Replan 是历史事件与事务链证据，不将其回执自动升级为逐条独立认证的新计划实际控制采用，也不把内部 wrong-goal=0 当作独立用户任务正确性。历史支持裁定仍未通过，4 个正常未完成实例保留。

另一次从初始化公开给定任务的原生诊断记录了初始化、路线接受、268 组绑定观测的 forward/计划/返回控制、约 52.826 米最大 XY 位移以及连续三道有向闭边界到达事件，原生 Completed、RC=100。记录区间 267 个时间间隔符合 0.05 秒 cadence；全任务覆盖仍未独立认证，逐次 apply_control 回执缺失，故该层保持 UNKNOWN。原生 MinSpeedTest FAILURE、17 条相关记录及安全端点 FAIL 原样保留。该诊断说明冻结执行后端曾实际驾驶，不是澄清有效性样本，也不证明途中答案切换。

## 4.6 Failure Analysis and Limitations

主比较依赖合法候选、给定地图任务义务和理想答案服务。分歧记录的 A/B 均衡与每布局相同的标签构成，使不澄清和立即询问的部分表现可由实验设计直接推得；其意义在于验证选择性决策规则的条件化 trade-off，而不是发现未知分布上的端到端优势。44 条等价控制记录来自同一指代表达任务的语义保持配置，进一步限制了语料的自然多样性。

标签尚未完成人工双标，两套解析器一致并不保证符合人类语言直觉；部分道路长度差仅约 0.72/0.73 米，既有来源指出 16 条窄余量记录需要优先复核。没有乘客意图采集、视觉 grounding 评价、未见布局评价或真实回答误差实验。未定义标签的覆盖率不携带正确性保证，不能把安全 abstention 当作安全驾驶认证。

时序消融使用共同保存轨迹和既有协议参考时刻，不证明部署时可知未来 deadline，也不能推断 WAIT 会产生更好的实际轨迹。扩展的一条运行在 commitment 前科学未完成，原始另一条未达到 commitment，二者均保留为不完整端点。时机约束避免 late trigger 的代价包括保持未决。

历史执行中回答和重规划并不保证任务完成。另一公开任务诊断持续低速且未记录局部门事件，最终无原生完成端点，只作为失败分析。已有共同起终点真实分支三策略前瞻设计因资产资格不足而停止作为主实验依赖；先前计划的 96 次闭环矩阵未执行，不列为空结果表，不通过改路线终点或放宽 guard 补救。本章不声称三策略真实闭环公平对照、闭环用户任务完成率提升、在线修订关系验证或 HRI 收益。

## 附录：复现与来源说明（非正文）

本稿替代 STEP2 的旧平行 RQ 章节结构，不修改历史文稿。工程阶段 STAGE5A 的入口为当前报告目录；MAIN_POLICY_RESULTS/MAIN_LAYOUT_SUMMARY 对应主结果，ABLATION_RESULTS/ABLATION_EFFECTS 对应消融，TRAJECTORY_TASK_POINTS/TIMING_POINT_DATA 对应机制图；BENCHMARK_MANIFEST 固定输入和 revised 代码摘要，PREDICTION_LOCK 固定评估前的策略建议。来源路径及 Git 身份只在本附录及审阅报告中给出。

- 主语料：`../driveclarify_rq1_grounded_relation_v3_20260910/` 的 method_inputs、label_authority；标注限制见其 INDEPENDENCE_AUDIT 与 LABEL_PROVENANCE_AND_REVIEW。
- 时序：`../driveclarify_rq2_t_cg_formal_v3_prospective_evaluability_and_execution_v1/FORMAL_V3_EPISODE_LEVEL_PRIMARY_TABLE.json`、`../driveclarify_rq2_extension_single_cell_evaluability_adjudication_v1/RQ2_EXTENSION_RESULTS.json`。
- 历史执行：`../driveclarify_rq3_v3_technical_validity_recovery_and_resume_v1/PART_B_FINAL_RESULTS.json` 及生命周期分析。
- 公开诊断：`../driveclarify_stage3c_b_native_runtime_20260914/` 的 B_TEN_LAYER_RESULTS、B_NATIVE_RESULT_SUMMARY、B_ROUTE_PLAN_CONTROL_SUMMARY；失败 A 见对应 stage3c/stage3d 报告。
- 预览图是数据核验版，正式图型与外部独立复核尚待完成；所有本轮数值为实际 CPU/offline 输出。
'''
    (REPORT / 'EXPERIMENT_CHAPTER_V3_PIVOT_DRAFT.md').write_text(chapter)
    boundary = '''# 论文主张与证据边界

| 可用表述 | 证据与限定 | 不允许推论 |
|---|---|---|
| 给定候选与合法任务证据、理想答案时，选择性澄清降低错误承诺/询问负担 | 新 CPU 三策略建议，先锁定再连接标签；反事实意图，不是乘客数据 | 三策略闭环任务完成率提升、真实 HRI 收益 |
| 任务后果门减少等价记录询问 | 直接复用 IMMEDIATE_QUERY；含等价控制样本，标签构成平衡 | 所有指标最好；对自然歧义分布的普遍效应 |
| 有效历史增加本保存轨迹内的证据充分/窗口数量 | 原始47、扩展23端点，另保留2不完整实例 | 真正 WAIT 控制的反事实驾驶收益 |
| 联合时间条件避免协议预留耗尽后的触发 | 原始17/扩展10个late差，及时机会不增，未决仍在 | 物理 unsafe/recoverability 已认证 |
| 保存短期轨迹距离与任务关系不等价 | B两臂/C DEV/HIST分列，阈值不变，44 undefined保留 | 候选 futures 对拓扑已有可判定任务的增量价值 |
| 历史 ASK、回答、Full Replan/事务和原生完成链已被 exercised | 19/19历史生命周期、15/19完成；新计划控制实际采用未逐条独立认证 | answer 字段直接等同实际执行，wrong_goal=0 等同独立任务正确 |
| clear B 的冻结执行后端曾完成公开路线 | RC=100、约52.826m、三道闭边界；MinSpeedTest FAILURE，安全 FAIL | 方法有效，在线 switch，固定中立宿主或隐藏意图公平性 |

最大审稿风险是条件化结论被误读成端到端效果：方法/标签共享地图事实，标签未人工双标，平衡意图和理想回答使部分分数由设计确定。22个布局只有22个保存观测；重复措辞不增加独立场景数。布局 bootstrap 零宽区间不能消除这些不确定性。DEV/HIST均已暴露。未定义标签附加固定A回答仅用于输出服务映射，不可称为真实意图或参与正确率。

历史 Full Replan 的毫秒级墙钟事件延迟不等于重新调用 VLA 的完整延迟。旧 safe_completion=30/34 使用历史定义，同一原报告还有34个 stop_sign 事件；本轮不将其重新解释成零违规安全完成，也不重评分历史。公开任务导航条件不可泄漏给未来隐藏意图主实验。

停止旧的96次三策略真实分支矩阵作为论文主结果前提；保留历史记录和失败，不再路线挖掘。唯一下一步：独立复核本轮协议、标签隔离、结果和上述主张边界。
'''
    (REPORT / 'PAPER_CLAIMS_BOUNDARY.md').write_text(boundary)
    report = '''# STAGE5A 最终报告

状态：`STAGE5A_MAIN_AND_ABLATION_READY_FOR_REVIEW`。

已完成受控主比较、三个消融、五份核心 plot-ready CSV、四张 PNG 检查预览及实验章节 V3。READY 仅表示 CPU/offline 结果可审阅。CARLA、launcher/evaluator 启动、模型加载/forward、CUDA、训练、新路线工作均为 0。既有 STEP3A/live/历史数据未修改，无 Git 写操作。原生执行证据只读引用。

## 主结果及分母

C DEV 64条/8布局（48定义、16未定义），C HIST 112条/14布局（84定义、28未定义）；总计176基记录/22布局。没有原始乘客意图字段。44条DV各2个等权评估侧意图，88条EQ各1类，44条undefined各1个输出诊断，共220评估变体/策略；它们仍只构成176基记录。落盘528条base×policy、660条variant×policy、66条layout×policy。主正确率分母132，不混入B、A、闭环或时序记录。

''' + table + '\n' + extra + '''
合并仅描述：不澄清正确110/132、错误22/132；另两策略正确132/132。相对不澄清，Full错误承诺下降16.67个百分点；相对立即询问，正确率不变、询问下降66.67个百分点。各布局率相同，配对布局95% bootstrap区间退化为对应点值；代码以8/14个布局重采样，不以132记录抽样。无p值，无未见泛化结论。

Full在44条undefined全部abstain；全176条的覆盖75%、未决25%、询问25%。另外两策略在undefined输出默认ACT或ASK后固定A服务映射，正确/错误字段null，不给虚假真意；其覆盖100%不能推出任务正确。主表的询问率33.33%和全输入25%分母不同，均明确保存。

## 消融与机制

''' + memory + '\n' + timing + '''
Task gate消融严格是主基线别名，未重复生成数字。Memory去除使充分证据率下降25.53/73.91个百分点、窗口下降12.77/30.43个百分点（原始/扩展）。Timing去除增加17/10次late、及时机会仍12/7；联合规则未触发35/16，其中证据始终不足18/6，证据来晚17/10。已有实际窗口漏触发两规则均0，不能将late的历史分类名称误读为存在过可用机会。

时序数据144行=72个计划/已执行实例×2记忆视图，其中140行属于70个完整端点，4行是两条不完整运行的两个视图。缺失时刻/余量为null，未作为0加入分布。提取器核验same_source_identity_all_views、配对trace_digest、deadline−reference关系及实际触发分类。本轮重汇总保存episode分析，不重跑frame reducer，不伪装新闭环消融。

''' + trajectory + '''
轨迹数据203行：B两臂13/14，C64/112，159个定义标签、44个未定义；仅FULL_INPUT，不把mask副本算记录。来自STEP1已核验的逐样本表，冻结阈值不变，不重算731旧检查。B两臂首帧不同，不合并作主比较。

四图已实际渲染并逐图查看：主图为配对布局点、消融为效应点（仅task gate有cluster CI且宽度零）、轨迹为散点、时间为box/strip并给全尺度与标注边界细节。没有小样本violin或对确定计数乱加误差条。所有预览为检查版。

## 闭环证据及负面结果

历史RQ3：35计划、34可评价，HIGH19条有ASK/答案/历史Full Replan生命周期，15/19原生完成，LOW15/15完成，总30/34；4条正常未完成保留，历史支持裁定仍NOT_SUPPORTED。该事件计数不替代独立新计划控制采用或任务正确性；旧wrong_goal/safe_completion不改为本轮独立认证。历史stop_sign34条仍存在。

clear B：历史Completed/RC100，268组实际调用/计划/返回控制、约52.826m最大XY位移、三道闭边界与已记录267段cadence通过；apply_control逐次独立认证UNKNOWN，全任务覆盖未知。MinSpeedTest FAILURE、17条min_speed记录、安全端点FAIL保留。clear A仅失败诊断，本轮不修复或重跑。详见evidence/CLOSED_LOOP_EVIDENCE.json及源文件摘要。

## 实际实现、复现与验收

scripts/policies.py 无文件或标签服务访问；公开白名单去除标签/来源配置名，固定K1顺序。predict.py先保存并锁定528条建议，analyze.py验锁后读取标签/产生评估侧答案。P2实际调用冻结revised函数，无任务依据不会被远轨迹升级为DV。新代码只写本报告目录；所有旧输入及函数以BENCHMARK_MANIFEST摘要固定。

测试见logs/unittest.stderr.txt及ACCEPTANCE_RESULTS.json；覆盖用户13项验收与定向接口反例。CPU运行退出码见logs/EXIT_CODES.json。未重跑STEP3A46项、旧731检查或任何历史大流水线。本地复现（仓库根目录，输出仍隔离在本报告目录）：

```bash
python -B reports/driveclarify_stage5a_controlled_main_ablation_20260914/scripts/freeze.py
python -B reports/driveclarify_stage5a_controlled_main_ablation_20260914/scripts/predict.py
python -B reports/driveclarify_stage5a_controlled_main_ablation_20260914/scripts/analyze.py
python -B reports/driveclarify_stage5a_controlled_main_ablation_20260914/scripts/plot.py
python -B reports/driveclarify_stage5a_controlled_main_ablation_20260914/scripts/write_reports.py
python -B reports/driveclarify_stage5a_controlled_main_ablation_20260914/scripts/run_checks.py
```

历史路径由manifest固定；本目录不复制模型/地图/大型原始运行，不是脱离仓库即可使用的全量数据包。复现覆盖本轮派生文件属于显式命令行为，不覆盖历史；如需独立复核原始数据，请按manifest在隔离仓库快照使用这些命令。实际复现必须检查source hashes，不能只凭CSV输出认证policy隔离。

## 主张边界与剩余风险

最大风险：独立实现共享地图事实、标签未人工双标、理想回答与平衡样本造成条件化分数上限。已有16条窄余量语言样本需外部复核；本轮不重标、不排除、不调阈值。Full的75%全输入覆盖是明确保守边界，不是能力缺失已解决。没有新视觉grounding、真实乘客、闭环三策略公平对照、revised live或WAIT驾驶收益证据。

EXPERIMENT_CHAPTER_V3_PIVOT_DRAFT.md 已按4.1–4.6重写，旧96次空主表移除，仅作为未执行的工程限制保留；阶段/路径/版本置于附录。PAPER_CLAIMS_BOUNDARY.md逐项限定可写结论。

唯一下一步：交由独立复核者审阅本轮冻结协议、实现、图表数据与主张边界。本轮完成后停止，不启动CARLA，不再路线工作。
'''
    (REPORT / 'FINAL_REPORT.md').write_text(report)
    print('V3 chapter, final report, claim boundaries and exact closed-loop source references written')

if __name__ == '__main__':
    run()
