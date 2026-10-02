#!/usr/bin/env python3
"""输出科学接口阻断报告；所有未执行结果明确为空，绝不伪装官方输出。"""
import ast
import csv
import datetime
import json
from pathlib import Path
import subprocess
from audit_preflight import ROOT, SIM, OUT, sha, digest, save

STATUS = 'BLOCKED_FULL_BENCH2DRIVE_SCIENTIFIC_CHANGE_REQUIRED'
REASON = '冻结 RQ3→RQ1-V2→V11 入口没有不提供任务上下文的合法配置，且 V11.setup 无条件启用 instruction-following 模型输入。'

def load(name):
    return json.loads((OUT/name).read_text())

def write(name, text):
    (OUT/name).write_text(text.rstrip()+'\n')

def table(headers, rows):
    return '\n'.join(['| '+' | '.join(headers)+' |','| '+' | '.join(['---']*len(headers))+' |']+
                     ['| '+' | '.join(str(v).replace('\n',' ') for v in row)+' |' for row in rows])+'\n'

def official_file(rel):
    p=SIM/rel
    expected=subprocess.check_output(['git','-C',str(SIM),'show','HEAD:'+rel])
    return {'path':str(p),'sha256':sha(p),'matches_simlingo_HEAD':p.read_bytes()==expected}

def main():
    source=load('audit/SOURCE_IDENTITIES.json');manifest=load('FULL_B2D_ROUTE_MANIFEST.json')
    checkpoint=source['checkpoint']['frozen_driveclarify_required'];native_checkpoint=source['checkpoint']['native_release_local']
    groups=source['groups'];now=datetime.datetime.now(datetime.timezone.utc).isoformat()
    metric_files={n:official_file('Bench2Drive/tools/'+n) for n in ['merge_route_json.py','ability_benchmark.py','efficiency_smoothness_benchmark.py']}
    stats=official_file('Bench2Drive/leaderboard/leaderboard/utils/statistics_manager.py')
    version={'status':'VERSION_AND_ROSTER_AUDITED_EXECUTION_NOT_FROZEN','selected_version':'v0.0.3',
      'exact_label':'Bench2Drive v0.0.3, SimLingo embedded distribution, existing local patches disclosed',
      'embedded_path':str(SIM/'Bench2Drive'),'separate_Bench2Drive_git_HEAD':None,
      'embedded_git_tree_at_parent_HEAD':source['bench2drive_tree_git_object_at_simlingo_HEAD'],
      'SimLingo_HEAD':source['simlingo_HEAD'],'DriveClarify_HEAD':source['driveclarify_HEAD'],
      'version_evidence':[str(SIM/'README.md')+':216',str(SIM/'Bench2Drive/README.md')+':137'],
      'CARLA_version':source['carla_version'],'CARLA_server_started_this_stage':False,
      'CARLA_version_evidence':'installation VERSION=0.9.15; installed Python distribution=0.9.15; binary hashed; live server version not queried',
      'scenario_runner_path':str(SIM/'Bench2Drive/scenario_runner'),'scenario_runner_identity':groups['bench2drive_scenario_runner'],
      'evaluator_path':str(SIM/'Bench2Drive/leaderboard/leaderboard/leaderboard_evaluator.py'),
      'evaluator_identity':groups['bench2drive_evaluator'],'metrics':metric_files,'statistics_manager':stats,
      'manifest_path':manifest['path'],'manifest_sha256':manifest['sha256'],'official_route_count':220,'scenario_type_count':len(manifest['scenario_type_counts']),
      'SimLingo_native_entry':str(SIM/'team_code/agent_simlingo.py')+'::LingoAgent',
      'SimLingo_native_launcher':str(SIM/'start_eval_simlingo.py'),
      'DriveClarify_frozen_entry':str(ROOT/'driveclarify_rq3/simlingo_agent.py')+'::DriveClarifyRQ3SimLingoAgent',
      'checkpoint_required_by_frozen_DriveClarify':checkpoint,'native_launcher_checkpoint':native_checkpoint,
      'same_checkpoint_for_both_arms_required':True,'formal_checkpoint_selected':False,
      'historical_RQ3_launcher_stack_is_standard_B2D':False,
      'historical_RQ3_evaluator':str(SIM/'leaderboard_autopilot/leaderboard/leaderboard_evaluator.py'),
      'historical_RQ3_scenario_runner':str(SIM/'scenario_runner_autopilot'),
      'v004_port_attempted':False,'v004_note':'选用已安装且由当前 SimLingo README 明确支持的 v0.0.3；未发现本项目已合格的 v0.0.4 替代入口，也未尝试迁移。',
      'preexisting_changes':[
        'evaluator: windowed CARLA and shorter startup sleep; engineering differences',
        'route_scenario: opt-in RQ2 background/parked-actor suppression; must remain unset for standard evaluation',
        'scenario_manager: opt-in historical probe termination hooks; must remain inactive',
        'SimLingo agent/model/navigation: preexisting frozen DriveClarify extensions; exact patch and source hashes preserved'],
      'native_bundle_tick_limit_note':'当前 SimLingo HEAD 中 4000-tick TickRuntimeError 检查已注释；不是本阶段修改，不能仅凭 Bench2Drive README 声称启用了该限制。',
      'standard_background_policy':'官方 BackgroundBehavior、场景车辆和 parked actors 必须保留；不得复用历史 RQ3 关闭随机交通的启动环境。',
      'remaining_preflight':'无合法 A1 无上下文输入；未进行 A0/A1 model-load/route-start/result smoke。'}
    save('BENCHMARK_VERSION_AUDIT.json',version)
    write('BENCHMARK_VERSION_AUDIT.md',f'''# Bench2Drive 版本审计

本机选择 **v0.0.3（SimLingo 内嵌分发，已有本地补丁）**。这不是 v0.0.4 或已提交的官方榜单成绩。正式执行尚未冻结。

SimLingo HEAD：`{source['simlingo_HEAD']}`。Bench2Drive 无独立 Git 仓库；父 HEAD 的子树对象：`{source['bench2drive_tree_git_object_at_simlingo_HEAD']}`。
版本文字证据：`/home/buaa/wrh/simlingo/README.md:216`、`Bench2Drive/README.md:137`；本地源码和远端固定提交 README 一致标注 v0.0.3。

CARLA 安装及 Python 包均为 **0.9.15**；服务器二进制已哈希，本阶段没有启动服务器查询运行时版本。环境见 `audit/ENVIRONMENT_AUDIT.json`。

路线文件：`{manifest['path']}`；SHA-256：`{manifest['sha256']}`。220 个唯一稀疏官方 route ID，44 类场景，每条路线一个场景；220 个原有 split 文件与总 XML 的路线内容逐一相等。ID 不是人为生成的 0–219。映射与场景计数见 `FULL_B2D_ROUTE_MANIFEST.json`。

标准 evaluator 必须使用 `Bench2Drive/leaderboard`，scenario_runner 必须使用 `Bench2Drive/scenario_runner`。历史 RQ3 启动器使用 `leaderboard_autopilot`/`scenario_runner_autopilot`，不能直接充当标准 Bench2Drive 运行。两个包的选择是启动工程配置，不是此次停止的根本原因。

官方 merge、ability、efficiency/smoothness 三个脚本和 statistics_manager 全部与 SimLingo HEAD 相同。已有 evaluator 的窗口/等待补丁、默认关闭的 RQ2 background/probe hooks 已记录在 `audit/simlingo_preexisting_changes.patch`。本阶段没有修改它们。标准基准必须保留官方背景交通。当前分发的 4000 tick 限制在 HEAD 已注释，不能把该分发描述成未经适配的上游实现。

冻结 DriveClarify 强制检查 checkpoint `{checkpoint['sha256']}`；原生 launcher 配置为 `{native_checkpoint['sha256']}`。逐 tensor 只读比较：共同 992 项完全相等，冻结文件额外包含 896 参数的 `route_switch_adapter.delta_switch`。文件不相同，不能给 A0/A1 分别使用两份权重；本阶段尚未冻结实际执行配置。

入口：A0 为原生 `team_code/agent_simlingo.py::LingoAgent`；A1 为 `driveclarify_rq3/simlingo_agent.py::DriveClarifyRQ3SimLingoAgent`。后者当前无法满足无任务上下文的标准输入合同，详见 `audit/NO_CONTEXT_SOURCE_PROBE.json`。
''')

    infraction_names=['collisions_layout','collisions_pedestrian','collisions_vehicle','outside_route_lanes','route_dev','red_light','stop_infraction','vehicle_blocked','route_timeout','scenario_timeouts','yield_emergency_vehicle_infractions','min_speed_infractions']
    metrics={'status':'AUDITED_NOT_COMPUTED','official_scripts':metric_files,'statistics_manager':stats,
      'driving_score':'由官方 merge_route_json.py 读取官方 score_composed 汇总，不自行重算。',
      'success':'官方 merge: Completed/Perfect 且除 min_speed_infractions 外没有任何 infraction；输出比例，表格转百分比。',
      'merge_agent_crash_behavior':'官方 merge 跳过 Failed - Agent crashed；必须先审计权威性，若缺少合法可合并结果则不能声称220全量。',
      'efficiency':'官方 read_from_json 提取 min_speed_infractions 的百分数，过滤 >1000，按有记录的路线计算；没有观测不填零。',
      'smoothness':'官方 seg_compute_comfort_metric；0.1s 原生记录周期、20样本分段、Savitzky–Golay及原阈值原样使用；缺测不补造。',
      'ability':'使用官方 ability_benchmark.py；含 CARLA map 路口 completion 规则，Traffic_Signs 不能只按类别手工平均。',
      'multi_ability_mean':'官方脚本五项能力算术均值。',
      'diagnostics':infraction_names,'episode_incidence':'至少一个对应事件的路线数 / 有权威结果路线数。',
      'raw_event_count':'原始 infraction list 长度总和；outside_route_lanes 的列表数量不是越线距离或比例。',
      'route_success_secondary_matches_official_merge':True,'ambiguity_metrics_requested_or_computed':[]}
    save('FULL_B2D_METRIC_AUDIT.json',metrics)
    write('FULL_B2D_METRIC_AUDIT.md','# 官方指标审计\n\n'+ '\n\n'.join(f'**{k}**：{v}' for k,v in metrics.items() if isinstance(v,str))+'\n\n诊断字段：'+', '.join(infraction_names)+'。所有性能/活动指标本阶段均未观测。\n')
    analysis_plan={'status':'PROSPECTIVE_SPEC_RECORDED_BUT_FORMAL_FREEZE_NOT_ISSUED',
      'unit':'official route, complete authoritative A0/A1 pair','continuous_metrics':['score_composed','score_route','score_penalty'],
      'summaries':['A0 mean','A0 median','A1 mean','A1 median','paired mean difference A1-A0','paired median difference A1-A0'],
      'bootstrap':{'resamples':100000,'rng':'numpy.random.Generator(PCG64)','seed':2026090601,
       'sample':'resample paired route indices with replacement, same indices for both arms','statistic':'mean(A1-A0)',
       'interval':'percentile 2.5%, 97.5% with linear interpolation'},
      'binary':['official route success','any collision episode'],
      'mcnemar':'two-sided exact binomial discordant-pair test, p=min(1,2*BinomialCDF(min(b,c), b+c,0.5)); zero discordance p=1 only when pairs exist',
      'noninferiority_margin':None,'formal_route_seed':None,'seed_note':'尚未正式冻结或执行，不把候选 seed 宣称为已用运行 seed。',
      'native_stochasticity':'不设置人工确定性；不声称逐位轨迹配对。'}
    activity_fields=['routes_with_ASK','ASK_count','WAIT_ticks','answer_events','clarification_triggered_full_replan',
      'nonclarification_route_or_task_installation','nonclarification_full_replan','direct_control_intervention_ticks',
      'additional_VLA_forwards','unauthorized_additional_VLA_forwards','duplicate_VLA_forwards','duplicate_execution_count',
      'mean_decision_latency_s','median_decision_latency_s','mean_replan_latency_s','median_replan_latency_s']
    contract={'status':STATUS,'formal_freeze_issued':False,'question':'完整标准 Bench2Drive 上本地 A0 与 A1 的驾驶性能比较；不评价歧义解决。',
      'arms':{'A0':'frozen native SimLingo standard entry','A1':'same frozen SimLingo + current frozen DriveClarify, no injected ambiguity context'},
      'shared':['version','route roster','CARLA','scenario_runner','evaluator','metric scripts','checkpoint','native visual/navigation inputs','PID/controller','route/scenario config'],
      'manifest_digest':manifest['digest'],'paired_analysis':analysis_plan,'transparency_fields':activity_fields,
      'transparency_semantics':'nonclarification_full_replan 是 nonclarification_route_or_task_installation 子集；INITIAL_NATIVE_ROUTE 单独列；无ASK不等于无Full Replan。',
      'authoritative_result_rule':'首个合法权威结果永久保留；不以低分、碰撞、agent blocked/timeout失败重跑；重复权威结果暂停该路线聚合并审计首个合法结果。',
      'technical_retry_policy':{'default_max_attempts':3,'same_scientific_configuration':True,
       'allowed_only_before_authoritative_result':['CARLA/evaluator crash','RPC disconnect','port collision','host OOM','corrupted nonauthoritative output'],
       'exception':'超过3次仅在基础设施原因确凿、无权威结果、同配置且记录原因时允许'},
      'proposed_execution_layout':'non_formal/<arm>/<attempt>/ and formal/<route_id>/<arm>/attempt_<n>/; no runs created',
      'checkpoint_resume_rule':'权威原始 JSON -> SHA256 -> 原子账本；仅恢复缺少权威结果的路线；未达到可实现执行的科学前提。',
      'blocked_reason':REASON,'stop_basis':'用户第24节：不得改动模型科学输入语义及冻结方法行为。'}
    save('FULL_B2D_SCIENTIFIC_CONTRACT.json',contract)
    write('FULL_B2D_SCIENTIFIC_CONTRACT.md',f'''# 全量标准基准科学合同

状态：`{STATUS}`。正式冻结未签发，不能执行候选运行。用户原文完整保留于 `audit/USER_REQUEST.txt`。

目标：220 条官方路线逐条运行 A0/A1，共 440 次原生闭环。A0 为冻结原生标准 SimLingo；A1 启用当前冻结 DriveClarify，不注入歧义任务。两臂共享版本、checkpoint、图像/原生导航、PID、官方背景交通、evaluator和计分脚本。

顺序在任何运行前记录：按官方 XML，零基偶数路线 A0→A1，奇数 A1→A0，各 110 对；不按表现重排。

配对次级分析预先指定：100,000 次 paired route bootstrap，PCG64，RNG seed **2026090601**；平均差 A1−A0 的 percentile 95% CI。DS、RC、Penalty 的两臂 mean/median 和配对差均报告。成功和碰撞 incidence 使用配对 discordance 与双侧 exact McNemar。没有样本时均为 null。没有非劣效界值，不声称非劣效。

每个首个合法权威结果永久保留；低分、碰撞、agent blocked/timeout 不因表现重试。仅无权威结果的基础设施故障可同配置最多三次技术尝试；确凿根因修复的例外必须单列。缺路线不能宣称完整基准。正式环境 seeds 尚未签发。

活动口径遵守 LOW 审计：无 ASK 的真实 ACT/Full Replan 不得压制；非澄清 Full Replan 是非澄清安装的子集，初始原生 set_global_plan 单列。活动为未观测时必须 null。此合同为阻断时保存的前瞻规格，不冒充已经生效的正式执行 freeze。
''')

    for arm in ['A0','A1']:
        rows=[{'route_id':r['route_id'],'canonical_index':r['canonical_index'],'arm':arm,'status':'NOT_ATTEMPTED_SCIENTIFIC_INTERFACE_BLOCK',
          'authoritative_result':None,'raw_json_path':None,'raw_json_sha256':None,'attempts':0,'technical_retries':0} for r in manifest['routes']]
        save(arm+'_EXECUTION_LEDGER.json',{'status':STATUS,'expected_routes':220,'attempted_routes':0,'authoritative_routes':0,
          'missing_route_ids':[r['route_id'] for r in rows],'entries':rows})
        # 这是缺失产物说明，不含 _checkpoint，不可被官方 merge 当作结果读取。
        save(arm+'_OFFICIAL_MERGED_RESULTS.json',{'artifact_kind':'UNAVAILABLE_RESULT_NOTICE_NOT_OFFICIAL_MERGE_OUTPUT',
          'status':'NOT_EXECUTED','generated_by_official_merge':False,'arm':arm,'authoritative_routes':0,'expected_routes':220,
          'driving_score':None,'success_rate':None,'reason':REASON})
        with (OUT/('ALL_'+arm+'_ROUTE_RESULTS.csv')).open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=['route_id','arm','status','score_composed','score_route','score_penalty','success','raw_json_path'])
            w.writeheader();w.writerows({'route_id':r['route_id'],'arm':arm,'status':r['status']} for r in rows)
    paired=[{'route_id':r['route_id'],'A0_authoritative':False,'A1_authoritative':False,'complete_pair':False,'status':'NOT_ATTEMPTED'} for r in manifest['routes']]
    save('PAIRED_ROUTE_LEDGER.json',{'complete_pairs':0,'expected_pairs':220,'entries':paired})
    with (OUT/'ALL_PAIRED_ROUTE_RESULTS.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=['route_id','A0_authoritative','A1_authoritative','complete_pair','status']);w.writeheader();w.writerows(paired)
    save('TECHNICAL_RETRY_LEDGER.json',{'formal_attempts':0,'nonformal_native_attempts':0,'total_technical_retries':0,'routes_requiring_retries':[],
      'entries':[],'note':'CPU 配置反例不是闭环路线尝试，不能计为路线技术重试。'})
    metric_names=['Driving Score','Success Rate','Driving Efficiency','Comfort / Driving Smoothness','Overtaking','Merging','Emergency Brake','Give Way','Traffic Signs','Multi-Ability Mean']
    save('FULL_B2D_OFFICIAL_COMPARISON.json',{'status':'NOT_COMPUTED','complete_benchmark':False,'A0_routes':0,'A1_routes':0,
      'metrics':{n:{'A0':None,'A1':None,'delta_A1_minus_A0':None,'protocol_compatible_external_reference':None} for n in metric_names}})
    save('FULL_B2D_PAIRED_ROUTE_ANALYSIS.json',{'status':'NOT_COMPUTED_NO_PAIRS','complete_pairs':0,'analysis_plan':analysis_plan,
      'continuous':{n:{'A0_mean':None,'A0_median':None,'A1_mean':None,'A1_median':None,'paired_mean_difference':None,'paired_median_difference':None,'bootstrap_95_CI':None} for n in analysis_plan['continuous_metrics']},
      'binary':{n:{'A0_success_count':None,'A1_success_count':None,'discordance_table':None,'exact_mcnemar_p':None} for n in analysis_plan['binary']}})
    save('FULL_B2D_INFRACTION_ANALYSIS.json',{'status':'NOT_OBSERVED','authoritative_route_denominators':{'A0':0,'A1':0},
      'route_completion':{'A0':None,'A1':None},'infraction_penalty':{'A0':None,'A1':None},
      'infractions':{n:{a:{'episode_incidence':None,'raw_event_count':None} for a in ['A0','A1']} for n in infraction_names}})
    save('FULL_B2D_DRIVECLARIFY_ACTIVITY.json',{'status':'NOT_OBSERVED','A1_authoritative_routes':0,'metrics':{n:None for n in activity_fields},
      'initial_native_route_installations':None,'no_ASK_implies_no_replan':False,'historical_LOW_events_imported_as_current_results':False})
    write('TABLE_FULL_BENCH2DRIVE_MAIN.md','# 全量官方指标（未执行）\n\nN/A 表示未观测，不是 0。没有兼容外部 reference 可放入直接比较列。\n\n'+table(['指标 ↑','Native SimLingo A0','SimLingo + DriveClarify A1','Delta A1−A0','协议兼容外部 reference'],[[n,'N/A','N/A','N/A','N/A'] for n in metric_names]))
    write('TABLE_FULL_BENCH2DRIVE_INFRACTIONS.md','# 诊断与违规（未观测）\n\n'+table(['指标','A0','A1'],[[n,'N/A','N/A'] for n in ['Route Completion','Infraction Penalty']])+'\n'+table(['违规类别','A0 incidence','A1 incidence','A0 event count','A1 event count'],[[n,'N/A','N/A','N/A','N/A'] for n in infraction_names])+'\nincidence 是发生过该事件的路线数量/有权威结果的路线数量；event count 是原始事件列表元素数。outside_route_lanes 事件数不表示越线距离。')
    write('TABLE_FULL_BENCH2DRIVE_DRIVECLARIFY_ACTIVITY.md','# DriveClarify 活动（未观测）\n\nA1 没有执行，不能把任何活动、延迟或控制审计项写成实测零。\n\n'+table(['指标','A1'],[[n,'N/A'] for n in activity_fields])+'\n非澄清 Full Replan 是非澄清安装的子集，不相加；初始共同路线安装单列。')
    write('TABLE_FULL_BENCH2DRIVE_PAIRED_ROUTE_ANALYSIS.md','# 配对路线次级分析（0/220 完整对）\n\n'+table(['指标','A0 mean/median','A1 mean/median','配对 mean/median 差','95% CI'],[[n,'N/A','N/A','N/A','N/A'] for n in ['Driving Score','Route Completion','Infraction Penalty']])+'\n'+table(['二元结果','A0计数','A1计数','配对discordance','exact McNemar p'],[[n,'N/A','N/A','N/A','N/A'] for n in ['route success','collision episode incidence']])+'\n预先记录100,000次 paired bootstrap，PCG64 seed 2026090601；无样本，没有运行统计分析。')

    external={'status':'NOT_DIRECTLY_COMPARABLE','reference':'SimLingo paper Table 2','url':'https://arxiv.org/abs/2503.09594',
      'DS':'85.07 ± 0.95','SR_percent':'67.27 ± 2.11','Efficiency':'259.23 ± 5.59','Comfort_percent':'33.67 ± 5.72',
      'source_pdf_path':str(OUT/'audit/simlingo_paper.pdf'),'source_pdf_sha256':sha(OUT/'audit/simlingo_paper.pdf'),
      'benchmark_version_identity':'released repository says v0.0.3; exact paper evaluation checkout not established by Table 2',
      'reason':'论文汇总三个训练 seeds；发布仓库说明其 checkpoint 为复现、非论文原 checkpoint。本地文件额外有896参数route-switch adapter且源码有既存修改，未证明论文完全相同的checkpoint/evaluator/输入协议。',
      'repository_reference':'https://github.com/RenzKa/simlingo/blob/743b243afd6cf5ff51b9fa1f8cac86f22d569684/README.md',
      'use_as_local_A0':False,'external_delta_computed':False}
    save('audit/EXTERNAL_SIMLINGO_REFERENCE.json',external)
    blocker={'status':STATUS,'reason':REASON,'evidence':'audit/NO_CONTEXT_SOURCE_PROBE.json',
      'required_change':'定义并实现无任务上下文时的冻结入口合同，涉及 instruction/task-signature/background-policy 校验及 custom_prompt/user_flag 输入模式；或改动研究输入合同。',
      'why_not_routine_launcher_repair':'仅修 evaluator 的参数后缀、路径、dense-route alias 无法消除强制任务签名和提示模式；修改这部分会改变当前冻结模型科学输入语义。',
      'alternatives_audited':[
        {'option':'缺少或 null instruction','outcome':'冻结 V11_INSTRUCTION_REQUIRED 异常'},
        {'option':'空 instruction，真实无 task_signatures','outcome':'冻结 RQ1_V2_EXACTLY_TWO_TASK_SIGNATURES_REQUIRED 异常'},
        {'option':'复制历史 CLEAR 占位签名及人工 Follow the assigned route instruction','outcome':'可通过校验，但构造额外任务输入并启用 INSTRUCTION_FOLLOWING；不能悄悄当成原生标准输入'},
        {'option':'历史签名+空 instruction','outcome':'仍启用 INSTRUCTION_FOLLOWING，移除原生预测请求，且保留人工任务合同'},
        {'option':'恢复custom_prompt=None,user_flag=None并绕过RQ1配置要求','outcome':'需要改变冻结输入/运行合同，未执行'},
        {'option':'换用历史V10/V11绕开冻结RQ1/RQ2','outcome':'不是当前冻结DriveClarify方法，未执行'},
        {'option':'向A0也注入历史instruction','outcome':'A0不再是标准原生基准输入，未执行'}],
      'background_contract_note':'冻结 RQ1 校验及收据要求随机背景为0；标准B2D要求原背景行为。未向配置写入与标准执行不相符的已执行政策声明。',
      'native_smoke_started':False,'formal_runs_started':False,'performance_inspected_for_selection':False,
      'historical_science_changed':False}
    save('audit/SCIENTIFIC_INTERFACE_BLOCKER.json',blocker)
    # 最终逐文件复核，检查历史、科学源、模型和官方文件均未变化。
    inventories=[load('audit/'+n+'_inventory.json')['files'] for n in groups]
    protected=load('audit/HISTORICAL_PRESERVATION_BEFORE.json')['files']
    allrows={r['path']:r for rows in inventories+[protected] for r in rows}
    for cp in source['checkpoint'].values():
        for path,h in [(cp['path'],cp['sha256']),(cp['hydra_config'],cp['hydra_config_sha256'])]:allrows[path]={'path':path,'sha256':h}
    allrows[manifest['path']]={'path':manifest['path'],'sha256':manifest['sha256']}
    for r in manifest['routes']:
        for r2 in r['split_files']:allrows[r2['path']]=r2
    mismatches=[]
    for p,r in allrows.items():
        actual=sha(p) if Path(p).is_file() else None
        if actual!=r['sha256']:mismatches.append({'path':p,'expected':r['sha256'],'actual':actual})
    integrity={'status':'PASS' if not mismatches else 'FAIL','files_checked':len(allrows),'historical_files_checked':len(protected),
      'mismatches':mismatches,'source_groups':groups,'scope':'本阶段审计前后完整性；不是闭环运行期间的完整性证明。',
      'benchmark_runtime_integrity':'NOT_EVALUATED_NO_EXECUTION','scientific_source_changes_made':0,
      'historical_report_changes_made':0,'checkpoint_changes_made':0,'official_formula_changes_made':0}
    save('audit/POST_AUDIT_SOURCE_INTEGRITY.json',integrity)
    assert not mismatches
    freeze_source={'status':'AUDIT_SNAPSHOT_ONLY_NOT_FORMAL_EXECUTION_FREEZE','source':source,'post_audit_integrity':integrity}
    freeze_source['receipt_digest']=digest(freeze_source);save('FULL_B2D_SOURCE_FREEZE_RECEIPT.json',freeze_source)
    receipt={'status':STATUS,'formal_freeze_issued':False,'freeze_timestamp':None,'audit_timestamp':now,
      'route_manifest_digest':manifest['digest'],'source_audit_digest':freeze_source['receipt_digest'],
      'A0_native_smoke':'NOT_EXECUTED','A1_native_smoke':'NOT_EXECUTED','scientific_compatibility':'FAIL',
      'bootstrap_spec_recorded':analysis_plan['bootstrap'],'reason':REASON,
      'receipt_kind':'REFUSAL_TO_ISSUE_FORMAL_FREEZE'}
    receipt['receipt_digest']=digest(receipt);save('FULL_B2D_FREEZE_RECEIPT.json',receipt)
    write('FULL_B2D_FREEZE_RECEIPT.json.sha256',sha(OUT/'FULL_B2D_FREEZE_RECEIPT.json')+'  FULL_B2D_FREEZE_RECEIPT.json')
    repairs=[{'file':str(OUT/'tooling'/n),'region':'all functions; newly created audit-only tooling','original_problem':'没有该阶段版本、配置阻断和产物审计文件',
      'modification':'添加只读CPU源码/路线检查和阻断报告生成','classification':'ENGINEERING_AUDIT_ONLY','benchmark_behavior_changed':False,
      'A0_A1_fairness_changed':False,'before_sha256':None,'after_sha256':sha(OUT/'tooling'/n)} for n in ['audit_preflight.py','finalize_blocked.py']]
    save('audit/ENGINEERING_CHANGES.json',{'existing_code_repairs':[],'new_tooling':repairs})
    write('ENGINEERING_REPAIR_LOG.md','# 工程记录\n\n既有代码/配置修复 **0**，科学修改 **0**。先前存在的 patch 只留档未修改。新增本阶段审计工具如下；没有运行 launcher、修改控制器或启动恢复进程。\n\n'+table(['文件/region','问题与改动','分类','行为/公平性变化','before hash','after hash'],[[r['file']+'::'+r['region'],r['original_problem']+'；'+r['modification'],r['classification'],'均否','N/A（新文件）',r['after_sha256']] for r in repairs]))
    write('COMMAND_LOG.md',f'''# 命令与执行记录

本阶段开始于2026-09-06北京时间约15:01；报告生成时间UTC {now}。所有写操作限于本阶段目录；历史文件只读。

初始只读审计：读取用户附件、查找 AGENTS.md、读取 CLAUDE.md；`git rev-parse HEAD`、`git status --short`、`git diff`；`rg`/`sed` 检查 RQ3/V11/RQ1 入口、原生 SimLingo、Bench2Drive 官方指标及 LOW 审计；`nvidia-smi`、`free -h`、`df -h`、`nproc`、`ps` 盘点环境。错误的大小写路径、`gate.py`及`Version.txt`查询返回缺失后已更正为实际目录、`ambiguity_gate.py`及`VERSION`；不是运行故障或科学重试。早期检查过0–219整数范围，最终以官方稀疏ID原文为准，不据此生成缺失路线。

完整可复查脚本：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/home/buaa/wrh/DriveClarify python -B reports/driveclarify_full_bench2drive_standard_benchmark_v1/tooling/audit_preflight.py > reports/driveclarify_full_bench2drive_standard_benchmark_v1/audit/preflight.log 2>&1
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/home/buaa/wrh/DriveClarify python -B reports/driveclarify_full_bench2drive_standard_benchmark_v1/tooling/finalize_blocked.py
```

只读checkpoint核对使用 `/home/buaa/anaconda3/envs/simlingo/bin/python -B`、`torch.load(...,map_location='cpu')` 和逐共享key `torch.equal`；未实例化model。结果 `audit/CHECKPOINT_TENSOR_COMPARISON.json`。安装包查询 `importlib.metadata.version` 验证carla/torch/transformers等版本。

外部检索：GitHub固定提交README、CVF论文页面；CVF PDF读取403后，下载官方 `https://arxiv.org/pdf/2503.09594` 到 `audit/simlingo_paper.pdf`，`pdftotext -layout` 提取表2及训练seed说明；身份见外部reference JSON。没有安装包、benchmark assets或不同版本；完整官方文件已在本地。

未启动 CARLA/evaluator/原生smoke/正式路线；CPU配置反例不产生路线结果。没有官方合并、bootstrap抽样或McNemar计算，没有重试、后台运行或待恢复任务。
''')
    # 逐一覆盖用户最终要求的62项，并明确缺失而非填0。
    return_rows=[
      (1,'执行状态',STATUS),(2,'Bench2Drive version',version['exact_label']),(3,'CARLA version','0.9.15（安装/Python包；未启动服务）'),
      (4,'SimLingo source HEAD',source['simlingo_HEAD']),
      (5,'DriveClarify HEAD / scientific digest',source['driveclarify_HEAD']+' / '+groups['driveclarify_scientific']['digest']),
      (6,'checkpoint hash','冻结A1强制文件：'+checkpoint['sha256']+'；两臂正式选择尚未冻结'),
      (7,'官方manifest identity',manifest['path']+' / '+manifest['sha256']),
      (8,'official route count',220),(9,'A0 authoritative/total','0/220'),(10,'A1 authoritative/total','0/220'),
      (11,'technical retries',0),(12,'routes requiring retries','[]（没有原生尝试）')]
    labels=['A0 Driving Score','A1 Driving Score','Driving Score delta','paired route DS difference +95%CI',
      'A0 Success Rate','A1 Success Rate','Success Rate delta','paired success discordance + McNemar',
      'A0 Route Completion','A1 Route Completion','A0 Infraction Penalty','A1 Infraction Penalty',
      'A0 Efficiency','A1 Efficiency','A0 Comfort/Smoothness','A1 Comfort/Smoothness','A0 Overtaking','A1 Overtaking',
      'A0 Merging','A1 Merging','A0 Emergency Brake','A1 Emergency Brake','A0 Give Way','A1 Give Way',
      'A0 Traffic Signs','A1 Traffic Signs','A0 multi-ability mean','A1 multi-ability mean','collision episode comparison',
      'collision event comparison','outside-route-lanes comparison','route-deviation comparison','red-light comparison','stop-sign comparison',
      'vehicle-blocked comparison','timeout comparison','A1 routes with ASK','A1 total ASK count','A1 WAIT ticks',
      'A1 clarification-triggered Full Replans','A1 nonclarification route/task installations','A1 nonclarification Full Replans',
      'A1 direct-control intervention ticks','unauthorized additional VLA forwards','duplicate execution count']
    return_rows.extend((i,label,'N/A（未执行/未观测）') for i,label in enumerate(labels,13))
    assert return_rows[-1][0]==57
    return_rows.extend([(58,'published/reference SimLingo','Table 2: DS 85.07±0.95; SR 67.27±2.11%; efficiency 259.23±5.59; comfort 33.67±5.72；精确同版本/同协议未认证'),
      (59,'published directly comparable','NOT_DIRECTLY_COMPARABLE：三个训练seed汇总，发布checkpoint为复现，精确源码/evaluator/输入和权重身份未匹配'),
      (60,'source/evaluator integrity',f"PASS，{integrity['files_checked']}个文件前后哈希相同；{integrity['historical_files_checked']}个历史文件受保护；闭环运行完整性未测"),
      (61,'engineering repairs','既有代码/配置修复0；仅新增两份审计脚本，详见ENGINEERING_REPAIR_LOG.md'),
      (62,'exact output artifacts',str(OUT)+'；下列完整路径索引及FINAL_ARTIFACT_AUDIT.json')])
    save('REQUIRED_FINAL_RETURN.json',{'status':STATUS,'items':[{'number':i,'field':k,'value':v} for i,k,v in return_rows]})
    artifact_paths=sorted(str(p) for p in OUT.glob('*') if p.is_file())+[
      str(OUT/'FINAL_REPORT.md'),str(OUT/'FINAL_ARTIFACT_AUDIT.json'),str(OUT/'FINAL_VALIDATION_RECEIPT.json')]
    report=f'''# 完整标准 Bench2Drive 执行审计

**{STATUS}**

正式闭环未开始：A0 **0/220**，A1 **0/220**，完整配对 **0/220**，技术重试 **0**。这份交付是阻断证据，不是完整benchmark结果。没有后台任务。

## 阻断原因与边界

冻结入口 `DriveClarifyRQ3SimLingoAgent` 继承 RQ1-V2 与 V11。真实无上下文配置被 `V11_INSTRUCTION_REQUIRED` 或 `RQ1_V2_EXACTLY_TWO_TASK_SIGNATURES_REQUIRED` 拒绝。V11 setup 第267–268行无条件设置 `custom_prompt=instruction`、`user_flag=1`；原生 SimLingo setup 第126–128行则为 None。tick 第700–714行因此使用不同科学模型输入。

CPU探针执行从当前源码AST原样提取的校验与提示分支：空instruction也会添加 `<INSTRUCTION_FOLLOWING>`，并移除 `Predict the waypoints.` / `What should the ego do next?` 的原生请求。结果见 `audit/NO_CONTEXT_SOURCE_PROBE.json`。探针不加载model、不开CARLA、不生成路线分数；不能冒充闭环smoke。

历史普通路线配置含 `Follow the assigned route.` 和两个 `RQ3_CLEAR_UNUSED` 签名。该历史输入合同不能自动等同于本次无乘客上下文的标准原生输入；本阶段不重判其历史结果。仅复制占位签名、添加任务指令或给A0同样换输入，不能作为此次科学等价性的证明。冻结RQ1还要求零随机背景的政策声明；标准B2D背景行为应保留。

让当前入口在无上下文时仍保留标准原生prompt，至少需要调整配置合同及冻结setup的输入模式。这触及用户第24节禁止的模型科学输入语义/方法行为修改，因此停止于正式freeze之前。evaluator参数后缀、dense-route别名、端口和日志等工程问题不能解决此根因。没有压制LOW的真实非澄清Full Replan，没有回退到旧方法或注入新歧义任务。

## 已完成的只读验证

本地 **Bench2Drive v0.0.3（SimLingo内嵌分发）**、CARLA安装/Python包 **0.9.15**；220个唯一官方route ID、44类场景；220个split与总XML逐条匹配，且总XML与HEAD原文一致。原始HEAD与所有本地既存补丁分别保存，不能只凭HEAD忽略未提交源码。

共同992个checkpoint tensor完全相同；冻结A1文件额外有896参数的 `route_switch_adapter.delta_switch`。这不是本阶段训练；文件身份不同已记录，正式A0/A1不会混用两份文件。

官方merge、statistics_manager、efficiency/smoothness及ability脚本均与SimLingo HEAD相同。尚未调用合并或计分。所有源、checkpoint、evaluator、路线与受保护历史文件共 **{integrity['files_checked']}** 个前后哈希一致，历史文件 **{integrity['historical_files_checked']}** 个；这是本阶段只读完整性，不是运行时控制审计。

已记录完整路线账本、每条精确缺失ID、交替臂顺序和100,000次paired bootstrap前瞻规格。`FULL_B2D_FREEZE_RECEIPT.json` 明确 `formal_freeze_issued=false`，其SHA只认证这份拒绝签发收据，不能授权运行。

## 官方结果与外部参考

Our A0：**未执行，全部指标N/A**。Our A1：**未执行，全部指标N/A**。DS/SR差、bootstrap CI、McNemar、infractions、ASK/WAIT/replan/control/forward/latency均未观测；不填0。

Published SimLingo reference：论文Table 2提供 DS **85.07±0.95**、SR **67.27±2.11%**、Efficiency **259.23±5.59**、Comfort **33.67±5.72%**；论文汇总三个训练seed。[原始论文](https://arxiv.org/abs/2503.09594)

**NOT_DIRECTLY_COMPARABLE**：发布仓库将其benchmark标为v0.0.3，但说明发布checkpoint为复现而非论文原始模型；本地又有冻结route-switch扩展和既存代码适配，精确论文evaluation commit/输入/权重身份未闭合。没有将外部分数放入本地delta列。[固定提交README](https://github.com/RenzKa/simlingo/blob/743b243afd6cf5ff51b9fa1f8cac86f22d569684/README.md)

本次不支持“性能保持”“非劣效”或任何歧义改进结论。历史RQ1/RQ2/RQ3及paired/LOW审计不变。

## 用户要求的62项返回

{table(['序号','项目','结果'],return_rows)}

## 产物解释与精确路径

`A0/A1_OFFICIAL_MERGED_RESULTS.json` 是明确标注的 **UNAVAILABLE_RESULT_NOTICE_NOT_OFFICIAL_MERGE_OUTPUT**，没有 `_checkpoint`，没有伪造官方结果。三张CSV按220条路线列出NOT_ATTEMPTED/缺失值；不是观测分数。全部原生smoke均未执行，所有性能表是缺失状态说明。完整文件hash清单见FINAL_ARTIFACT_AUDIT.json。

'''+ '\n'.join('- `'+p+'`' for p in sorted(set(artifact_paths)))+'\n'
    write('FINAL_REPORT.md',report)
    print('blocked report written; source checks',integrity['files_checked'],flush=True)
    # 避免自引用哈希：artifact audit 不含自身和最终validation；validation绑定audit哈希。
    excluded={'FINAL_ARTIFACT_AUDIT.json','FINAL_VALIDATION_RECEIPT.json','FINAL_VALIDATION_RECEIPT.json.sha256'}
    artifacts=[{'path':str(p),'sha256':sha(p),'size':p.stat().st_size} for p in sorted(OUT.rglob('*')) if p.is_file() and p.name not in excluded and '__pycache__' not in p.parts]
    artifact_audit={'status':'PASS_BLOCKED_EVIDENCE_PACKAGE','full_benchmark_complete':False,'official_merged_outputs_present':False,
      'files':artifacts,'excluded_self_reference_files':sorted(excluded),'required_return_item_count':len(return_rows),
      'authoritative_results':{'A0':0,'A1':0},'missing_route_ids':{arm:[r['route_id'] for r in manifest['routes']] for arm in ['A0','A1']}}
    save('FINAL_ARTIFACT_AUDIT.json',artifact_audit)
    validation={'execution_status':STATUS,'artifact_validation':'PASS_BLOCKED_EVIDENCE_ONLY','source_integrity':integrity['status'],
      'full_benchmark_complete':False,'formal_freeze_issued':False,'A0_completed':0,'A1_completed':0,'total_expected':440,
      'native_smoke_passed':False,'official_merge_executed':False,'paired_analysis_executed':False,
      'artifact_audit_sha256':sha(OUT/'FINAL_ARTIFACT_AUDIT.json'),'freeze_receipt_sha256':sha(OUT/'FULL_B2D_FREEZE_RECEIPT.json'),
      'scientific_blocker_sha256':sha(OUT/'audit/SCIENTIFIC_INTERFACE_BLOCKER.json'),'required_return_sha256':sha(OUT/'REQUIRED_FINAL_RETURN.json'),
      'background_processes_started_by_stage':0,'utc':now}
    save('FINAL_VALIDATION_RECEIPT.json',validation)
    write('FINAL_VALIDATION_RECEIPT.json.sha256',sha(OUT/'FINAL_VALIDATION_RECEIPT.json')+'  FINAL_VALIDATION_RECEIPT.json')

if __name__=='__main__':main()
