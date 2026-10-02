"""编写 Phase A 文件；不签发正式冻结、不生成正式种子。"""
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];R=ROOT/'reports/driveclarify_rq3_paired_comparison_v2_task_binding_qualification_and_execution';Q=R/'qualification'
manifest=json.loads((R/'V2_TEMPLATE_CANDIDATE_MANIFEST.json').read_text());sem=json.loads((Q/'FAMILY_SEMANTIC_EVIDENCE.json').read_text())
def write(name,text):(R/name).write_text(text.strip()+'\n')
write('V2_SCIENTIFIC_DESIGN.md','''# V2 前瞻资格设计（Phase A）

阶段：`RQ3_PAIRED_COMPARISON_V2_TASK_BINDING_QUALIFICATION_AND_EXECUTION`。本文件不是正式冻结收据；正式种子与正式运行均为0。只有全部七个资格条件通过，才进入 Phase B。

V1 永久保留 `BLOCKED_RQ3_PAIRED_COMPARISON_SOURCE_OR_INTEGRITY_FAILURE`。历史 RQ3-V3 的 H-A NOT_EVALUABLE / H-B NOT_SUPPORTED / H-C SUPPORTED / RQ3_V3_NOT_SUPPORTED_INSUFFICIENT_EVALUABILITY 不变。历史源码、模型、RQ1/RQ2/PID/controller/RoutePlanner/Full Replan 均不改。

采用新受控模板：同一 installed Bench2Drive `bench2drive_145.xml` / route24206 / Town03 / ParkingCrossingPedestrian，原 XML、天气、官方场景与原生终点不变。通过地图拓扑扫描，按文件序选择第一个具有完整停车车道的路线；未读取任何策略效果用于选择。

共同前缀 → 按歧义解释进入一个局部标记停车区、短暂停靠 → 汇入共同原生路线 → 继续到共同终点。HIGH 两个真实任务区各对应一种解释；LOW 两种解释对应同一区。所有候选末5个点及终点相同。保留原场景行人和车辆；它们造成的碰撞、阻塞、等待均不能成为换场景理由。

A0 为冻结原生 SimLingo 加薄原文接口。A1 为同一 checkpoint/config/PID 下冻结 DriveClarify。A0 只见原文、共同路线和公共场景；A1 的候选集合来自经过资格验证的公共场景，两种候选同时可见，只有合法 ASK 后的乘客回答提供选定意图。公共候选策划属于本受控场景的输入条件，不宣称开放世界视觉 grounding 能力。

拟定 HIGH 4族×6对、LOW 3族×4对，共36对72次。HIGH 每族真意 A/B=3/3；每族 A0先/A1先=3/3；LOW 顺序2/2。USC-E 排除。Phase B 将单独冻结具体顺序、真意表及分析，然后才生成正式种子并审计新鲜性。

TCSC = 真实执行任务匹配独立真意 AND 原生完成 AND 无继承的重大安全失败。原生完成继承 official success 或 RC≥95%；重大安全失败继承 collision / outside_route_lanes 联合项 / route deviation。红灯、停牌等保持官方单独记录，不能偷偷并入或移出继承 major 定义。

有效配对门槛继承 HIGH≥22/24且每族≥5/6；LOW≥10/12且每族≥3/4。任何合法碰撞、堵塞、超时、错误任务、未完成都是结局。缺少权威证据保留 UNKNOWN，不替换种子、不做科学重试。LOW 不并入 HIGH 优效分析。

设计的外推范围为七个受控模板、一个官方路线背景的重复运行；不声称七个自然场景总体或官方全榜成绩。原生 installed evaluator 的 seed 参数只显式设置 Traffic Manager RNG；本实验不额外修改模型 RNG/确定性算法或场景 RNG。两臂同参数不等于保证数值完全相同轨迹。

全部资格通过后才冻结 exact McNemar、paired RD/95%CI、2×2表及连续指标配对区间。任何当前统计代码仅是 Phase A 候选实现。
''')
write('V2_A0_INTERFACE_AUDIT.md','''# A0 原文接口审计

沿用分类 `B — THIN_NONSCIENTIFIC_INTERFACE_ADAPTER_REQUIRED`。新 A0 继承 `agent_simlingo.LingoAgent`，未继承 DriveClarify 决策类。薄适配只校验配置/模型哈希、把 evaluator 的相同 dense plan 绑定到原生所需 aliases、给原生 `custom_prompt` 赋原始 instruction、设置原生 `user_flag=1`，以及两臂共用场景装配/只读记录。

实际原生 `tick` 源码 AST 被直接执行测试：七条完整原文逐字保留在 speed+route command 后，经 tokenizer 进入同一正常 forward 的 `DrivingInput.prompt` / `prompt_inference`。没有重写成候选，没有输入 candidate id 或 evaluation truth，没有目标点按 gold 调整。

A0 method_input 白名单只有 instruction/runtime_actors/background_traffic_policy。原生路线、导航命令和图像提供公共行驶背景；原文 PULL_OVER/暂停/继续足以表达可执行任务，不需要 oracle 才能选择一个可见停车区。资格通过表示合法接口和任务可表达，不保证模型会服从、停车或成功。正常不服从属于科学结局，不能用来拒绝模板。

A0 直接使用原生 control_pid；A1 继承原有冻结的 V11 检查封装，传入未修改张量调用同一 `LingoAgent.control_pid`。该已有封装及 post-switch rejection 属于冻结方法，不在本阶段增删。新包装器不增加 VehicleControl writer、model forward、planner 或 ego tick。

证据：qualification/INTERFACE_AND_EVALUATOR_SOFTWARE_QUALIFICATION.json、A0_NATIVE_INTERFACE_QUALIFICATION.json 和各原生 receipt。A0 开发运行687帧、686次原生模型调用、原文失配0，完整轨迹与权威评测终结记录可对应；没有计算或公布开发策略效果比较。
''')
lines=['# 七族语义资格证据','', '以下是基于公开物体、原生相机照片与地图的前瞻语义审查，不是独立人类标注研究；解析器识别与物理任务差异分别验证，不能互相替代。','']
for t in sem['rows']:
 lines += ['## '+t['condition'],'', '解释 A：'+t['interpretations']['A'],'','解释 B：'+t['interpretations']['B'],'','冻结族类型：`'+t['family_kind']+'`；冻结 RQ1 后果：`'+t['task_relation']+'`。','', '物理及语义依据：`'+json.dumps(t['evidence'],ensure_ascii=False,sort_keys=True)+'`。','']
lines += ['LOW 仅一个实际停靠区，仍有两个指称或计数解释；任务后果等价。HIGH 两个区域实际分离，解释互换可以改变正确停靠区，且两种意图都可在相同公共环境与官方终点下成立。正式3/3分配尚未冻结。','', '完整对象 ID / 坐标 / 尺寸 / 距离及图片路径见 qualification/FAMILY_SEMANTIC_EVIDENCE.json、NATIVE_ANCHOR_SENSOR_FIXTURES.json 与各 *_NATIVE_ANCHOR_14.png。']
write('V2_FAMILY_SEMANTICS_CERTIFICATION.md','\n'.join(lines))
write('V2_ROUTE_NEUTRALITY_AUDIT.md','''# 共同路线真意泄漏审计

七个模板均为 ROUTE_NEUTRAL。测试实际 `make_runtime_config(template, arm, run_id, scenario_seed)` 构造路径：函数没有 truth 入参；交换独立 evaluation truth A/B 后，A0 完整配置、A1 ASK 前完整配置、原 XML 字节哈希均一致。两臂共用的 instruction、actors、background policy 也逐项一致。

原生目标点和命令来自相同 evaluator route 输入，终点相同，A0 的 set_global_plan 直接委托原生实现，不查候选路线。A1 同时拥有所有公开候选，ASK 前没有“正确”候选；只允许冻结的 DriveClarify 在合法回答后切换条件。切换后的两臂 target point 不必相同，这是允许的澄清/重规划结果。

公共路线可能使模型偏好继续驾驶或某个停车位置，但同一路线在真意 A 和 B 下完全不变，因此不能唯一解出 gold。HIGH 两种实际停靠任务均在该背景下有效；不是只做哈希相等而让其中一种 gold 不可执行。

证据：qualification/INTERFACE_AND_EVALUATOR_SOFTWARE_QUALIFICATION.json 的实际配置反事实哈希；FROZEN_REPLAN_AND_MAP_COMPATIBILITY.json 的14条路线同终点/共同汇合/冻结重规划检查；FAMILY_SEMANTIC_EVIDENCE.json 的两个有效意图。
''')
write('V2_TASK_BINDING_CERTIFICATION.md','''# 对称任务绑定资格

每个新模板有独立 `candidate_assets/*_TASK_BINDING.json`。HIGH 为两个 Parking lane 的定向12m×3.5m矩形；LOW 为一个。坐标来自真实 CARLA road20/lane-2，不由选中候选或 gold 推导。

11个静止车辆测量夹具各27帧直接读取 CARLA pose/speed/lane/bounding box，全部正确提取所在任务区。14条候选路线通过原有 Full Replan 和 installed OutsideRouteLanesTest 几何检查。静止测量证明区域和测量可用，不证明模型会驶入或停车。初期 strict waypoint projection 的亚毫米边界不一致及场景物体摆放修复均保留；没有调整官方容差或根据策略胜负选图。

完整正式入口 `extract_complete_episode` 要求终结 receipt、文件哈希、全帧覆盖、control-return 数量和官方 duration 一致；不完整记录保留 UNKNOWN。原始 `extract_task_outcome` 只用于测量夹具/经验证完整的输入。其参数无 arm、gold、A1 selected id；独立离线 `score_task_truth` 才加入评估真意。

只在预先绑定的区域完整停靠才能成为任务事件。单次驶过、仍在 Driving lane、车身跨边界、停靠不足1秒均不计。两个不同区都停靠不能通过任一 HIGH 单一真意。LOW 两种解释映射同一区，不因内部 A/B 标签不同而误记 wrong-goal。

所有开发数据永久排除，正式种子尚未产生；没有使用开发策略表现改变区域、阈值或候选路线。
''')
write('V2_TASK_EVALUATOR_SPEC.md','''# 局部任务评估器资格规格（尚未正式冻结）

数据来源是每个 native control return 时的 CARLA world frame/timestamp、ego pose/yaw/roll/pitch/speed、真实完整包围盒8个world vertices、road/lane/type。记录路径两臂相同，只读，不调用模型、控制器或 world.tick。车身顶点包含 CARLA 实测非零包围盒局部偏移，避免用车体原点代替完整车身。

区域检测：8个顶点的平面投影都在预先绑定的定向矩形内；车体位于指定 Parking road/lane；yaw差≤20°，|roll|/|pitch|≤20°，高度与区域中心差≤2m；speed≤0.20m/s，连续时长≥1.00s。采样时间必须有限且严格递增，间隙≤0.10s。正式入口还要求连续 world frame，无缺帧；完整记录时长与权威 duration_game 差≤0.10s（原生首帧计时边界为0.05s）。

正式完整性：terminal receipt 必须存在、destroy_observed=true、trace_errors为空；trace SHA、行数与首末frame一致；行数为control_return_count或该数+1（终结时的额外观测）；必须有真实8顶点与roll/pitch。任一缺失/损坏不能默认为任务失败。

任务提取输出所有满足停靠条件的区域身份集合。独立真意评分：恰为期望单一区域则 correct_goal=1；有其他区或同时两个区则 wrong_goal=1；完整轨迹无合格停车则两项均0（任务未完成）；UNKNOWN保留null。不能从 A1 selected_candidate_id、ASK answer、Full Replan commit 或模型意图声明代替实际执行。

TCSC 还必须联合同一个 installed 官方 evaluator 的原生完成和继承 major 安全结果。原生 RC≥95或官方成功才完成；major为碰撞/联合 outside_route_lanes/route deviation。官方 offroad 与 wrong lane 在该 installed schema 中共用 outside_route_lanes，不捏造分开的计数。完整最终公式及缺失处理须在 Phase B 冻结后才可用于正式推断。
''')
write('COMMAND_LOG.md','''# V2 命令与证据索引

仅记录本 V2 工作；历史目录未改。各 Python 脚本保留可审阅源码，原始服务、评测与失败日志留在 qualification 下。

1. 读取两份用户授权、项目约定、冻结语义/接口/原生 evaluator；记录 V1 41个文件初始哈希。
2. 官方路线静态停车车道扫描，见 STATIC_OFFICIAL_ROUTE_PARKING_SURVEY.json；选中 route24206，未用模型表现筛选。
3. 自有 CARLA map-only 服务28740，capture_map.py 捕获地图/物体/照片；build_candidates.py 构造7模板。
4. qualify_physical_bindings.py 做11个真实静止测量、7个布局渲染。摆放与同步工程诊断保留 LAYOUT_ENGINEERING_REPAIR_01、LAYOUT_OWNED_ACTOR_RECOVERY、REFERENCE_PLACEMENT_INFRASTRUCTURE_TRIALS 和初始REF-C attempt。不存在科学重试。
5. qualify_geometry.py 检查14条 Full Replan/官方几何；strict projection初始诊断单独保留。
6. 新 a0_agent/a1_agent/runtime_common 仅接口/公开布局/只读全程观测。原生 import/MRO 审核没有加载模型；A1冻结PID委托封装已识别，不误当作控制器变化。
7. qualify_interfaces.py 执行原生prompt AST/实际配置反事实/任务提取与完整性检查，并复验7566历史文件+41个V1文件。
8. capture_native_anchor.py 用原生1024×512/FOV110/[-1.5,0,2]传感器位姿捕获7个anchor14图像；静止车辆bbox偏移测量。关闭自有map服务，见 MAP_PROBE_STOP_RECEIPT。
9. DEVELOPMENT_REGISTRATION.json 在运行前登记2个不配对工程run，seeds1731584188、1529594113永久排除。A0首启动在world/evaluator前退出66，保留旧输出并仅重启未暴露的基础设施，见 DEVELOPMENT_PREWORLD_RESTART_RECEIPT。
10. V2新runner启动两次实际native接口验证；每个进程的command/evaluator/server/output/cleanup receipt在 qualification/development。开发检验仅检查接口、记录、ASK防火墙、官方schema，不计算两臂效果。
11. certify_semantics.py 绑定公开实体/计数/距离证据；A1前启动描述补全有独立receipt；不改场景、候选路线或控制方法。
12. 检查 installed native blocked180s及route timeout≥300s后，前瞻将新包装器墙钟watchdog从600延长至7200，官方规则不改，见 PROSPECTIVE_WALL_WATCHDOG_COMPATIBILITY。
13. inventory_historical_numbers.py 在正式种子生成前审计历史文本整数排除集；此步骤不生成正式种子。

后续资格终结、正式冻结/种子/运行若获资格允许，会追加记录；当前文件不证明 Phase B 已开始。
''')
print('QUALIFICATION_DOCUMENTS_WRITTEN')
