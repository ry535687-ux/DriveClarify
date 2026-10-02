"""只读 V2；仅在本阶段生成裁定。"""
import datetime, hashlib, json, math
from pathlib import Path

ROOT = Path('/home/buaa/wrh/DriveClarify')
R = ROOT/'reports/driveclarify_rq3_paired_v3_host_feasibility_and_low_replan_audit_v1'
V2 = ROOT/'reports/driveclarify_rq3_paired_comparison_v2_task_binding_qualification_and_execution'
def read(p): return json.loads(Path(p).read_text())
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(n,x): (R/n).write_text(json.dumps(x,indent=2,ensure_ascii=False)+'\n')

def main():
    runs=read(V2/'formal/FORMAL_RUN_ROSTER.json')['runs']
    templates={x['condition']:x for x in read(V2/'V2_TEMPLATE_CANDIDATE_MANIFEST.json')['templates']}
    ledger=[]
    chain=[
        ('driveclarify_rq1_v2/simlingo_agent.py','DriveClarifyRQ1V2SimLingoAgent._evaluate_consequences','TASK_EQUIVALENT -> ACT, rank-one A'),
        ('driveclarify_clear_passthrough_v11/supervisor.py','ClearPassThroughSafeReplanSupervisor.process','ACT selected route not navigation-equivalent -> propose'),
        ('driveclarify_clear_passthrough_v11/replan.py','RouteTransitionManager.propose','ReplanAdmissibility.assess -> COMMIT_NOW'),
        ('driveclarify_clear_passthrough_v11/replan.py','RouteTransitionManager._commit','invoke common commit callback exactly once'),
        ('driveclarify_rq3/simlingo_agent.py','DriveClarifyRQ3SimLingoAgent._commit_resolved_route','time common full replan'),
        ('driveclarify_rq1_v2/simlingo_agent.py','DriveClarifyRQ1V2SimLingoAgent._commit_resolved_route','record full route; answer receipt optional'),
        ('driveclarify_clear_passthrough_v11/simlingo_agent.py','DriveClarifyV11SimLingoAgent._commit_resolved_route','install_reconnected_route; write ONLINE_ROUTE_INSTALL_RECEIPT'),
        ('/home/buaa/wrh/simlingo/team_code/nav_planner.py','SimLingoOnlineRouteUpdateOwner._install_reconnected_route','atomic _commit_prepared_route(online=True), cache reset'),
        ('driveclarify_rq3_paired_v2/scoring.py','evaluate_run','Full_Replan_committed = int(bool(online.get("committed")))')]
    for run in runs:
        if run['arm']!='A1' or not run['condition'].endswith('-E'): continue
        owner=Path(run['output'])/'owner_evidence'
        names=['ONLINE_ROUTE_INSTALL_RECEIPT.json','RQ1_V2_FULL_REPLAN_RECEIPT.json','RQ1_V2_CONSEQUENCE_DECISION_RECEIPT.json','V11_SUPERVISION_RECEIPT.json','V11_SUPERVISION_TIMELINE.jsonl','RQ3_LIFECYCLE_TIMING_RECEIPT.json','V11_RUNTIME_TIMELINE.jsonl','POST_SWITCH_PLAN_ACCEPTANCE.json','NATIVE_NAVIGATION_INPUT_CONTRACT.json','V2_RUNTIME_IDENTITY.json']
        online=read(owner/names[0]);replan=read(owner/names[1]);decision=read(owner/names[2]);supervision=read(owner/names[3]);timing=read(owner/names[5]);timeline=[json.loads(s) for s in (owner/names[6]).read_text().splitlines()]
        first=timeline[0];a0=next(x for x in runs if x['pair_id']==run['pair_id'] and x['arm']=='A0');a0owner=Path(a0['output'])/'owner_evidence'
        template=templates[run['condition']];routes=read(template['candidate_route_path']);nominal=template['official_route_context']['nominal_route'];selected=routes['candidate_A']
        max_delta=max(math.dist(x['xyz'][:2],y['xyz'][:2]) for x,y in zip(nominal,selected))
        asks=list(owner.rglob('*ASK*'));answers=list(owner.rglob('*ANSWER*'))
        checks={'ACT':decision['gate']['action']=='ACT','TASK_EQUIVALENT':decision['comparison']['relation']=='TASK_EQUIVALENT','no_ask':supervision['counters']['ask_receipts']==0 and not asks and timing['ask'] is None,'no_answer':supervision['counters']['passenger_answer_reads']==0 and not answers and timing['answer'] is None,'one_admission':supervision['counters']['replan_admissibility_invocations']==1,'one_transaction':supervision['counters']['route_transactions']==1,'commit_now':supervision['transition']['disposition']=='COMMIT_NOW','common_full_replan_receipt':replan['installation_receipt']==online,'changed_identity':online['active_route_identity_before']!=online['installed_route_identity'],'changed_interior_geometry':max_delta>1.,'native_consumed':first['last_observation']['active_route_identity']==online['installed_route_identity'],'native_initial_route_preexisted':read(owner/'NATIVE_NAVIGATION_INPUT_CONTRACT.json')['native_set_global_plan_called_once'],'counter_one':int(bool(online['committed']))==1,'post_switch_plan_accepted':read(owner/'POST_SWITCH_PLAN_ACCEPTANCE.json')['accepted']}
        ledger.append({'run_id':run['run_id'],'pair_id':run['pair_id'],'condition':run['condition'],'primary_classification':'C','provenance_pass':all(checks.values()),'checks':checks,'source_events':[{'path':str(owner/n),'sha256':sha(owner/n)} for n in names], 'source_code_chain':[{'path':str(ROOT/p) if not p.startswith('/') else p,'function':f,'event':e,'sha256':sha(ROOT/p)} for p,f,e in chain], 'ASK_receipts':list(map(str,asks)),'answer_receipts':list(map(str,answers)),'ACT_decision_receipt':decision,'route_install_event':online,'full_replan_request':{'evidence':'SOURCE_PATH_AND_ADMISSION_COUNTER','function':'ClearPassThroughSupervisor.process -> RouteTransitionManager.propose','standalone_durable_request_receipt':None,'separate_request_timestamp':None},'full_replan_admission_event':supervision['transition'],'full_replan_commit_event':replan,'selected_candidate_id':supervision['selected_candidate_id'],'selected_task_state':decision['comparison'],'route_state_before':{'route_id':supervision['transaction']['route_id_before'],'native_active_route_identity':online['active_route_identity_before'],'is_last':online['is_last_before'],'distance_digest':online['route_distances_digest_before']},'route_state_after':{'route_id':supervision['transaction']['route_id_after'],'native_active_route_identity':online['installed_route_identity'],'geometry_digest':online['fresh_route_geometry_digest'],'is_last':online['is_last_after'],'distance_digest':online['route_distances_digest_after']},'before_native_geometry_only_digest':None,'before_native_geometry_only_digest_limitation':'历史未单独保存；不把 route identity 伪称为 geometry-only hash。变化另由公开候选内点与名义路线几何差验证。','selected_vs_nominal_max_interior_point_delta_m':max_delta,'native_geometry_changed':True,'official_destination_changed':False,'first_consumption':{'frame':first['last_observation']['frame'],'wall_epoch':first['wall_time_epoch'],'route_identity':first['last_observation']['active_route_identity'],'target_point':first['last_observation']['target_point'],'route_switch_active':first['last_observation']['route_switch_active'],'native_forward_return_count':first['model_forward_return_count']},'timestamps':timing,'ordering':'ACT decision -> request -> admission -> common full-route commit -> native model/control; same simulator tick; source-order proven, distinct ACT/request timestamps not recorded','owner':online['online_route_owner_identity'],'controller_target_source':'SimLingo model output conditioned on installed native route; unchanged agent_simlingo.LingoAgent.control_pid','A0_analogous_operation':'PARTIAL','A0_detail':{'run_id':a0['run_id'],'initial_set_global_plan':'YES, same native LingoAgent initial setup','mid_episode_selected_full_route_installation':'NO','online_receipt_present':(a0owner/'ONLINE_ROUTE_INSTALL_RECEIPT.json').exists(),'runtime_identity':read(a0owner/'V2_RUNTIME_IDENTITY.json')},'can_affect_driving_behavior':True,'zero_interaction_cost_wording':'REQUIRES_NARROWER_WORDING'})
    assert len(ledger)==12 and all(x['provenance_pass'] for x in ledger)
    write('LOW_FULL_REPLAN_EVENT_LEDGER.json',{'episodes':ledger,'count':12,'read_only_historical_audit':True})
    receipt={'primary_classification':'C','classification_name':'GENUINE_DRIVECLARIFY_FULL_REPLAN_WITHOUT_ASK','source_event_provenance_12_of_12':'PASS','actual_route_geometry_changed':'YES','ASK_triggered':'NO','clarification_triggered':'NO','A0_analogous_operation':'PARTIAL','can_affect_driving_behavior':'YES','historical_zero_interaction_cost_wording':'REQUIRES_NARROWER_WORDING','required_future_metrics':['clarification_triggered_full_replan','nonclarification_route_or_task_installation','nonclarification_full_replan'],'scientific_method_changed':'NO','total_real_nonclarification_full_replans':12,'historical_counter_preserved':True,'recorded_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'why_C_controls':'ACT 是触发分支，但 12 次均走 ASK/answer 分支相同的科学 Full Replan admission/transaction/online owner，实际替换原生路线内部几何及缓存并被模型消费。不能降格为初始安装或仅元数据任务绑定。'}
    write('LOW_FULL_REPLAN_CLASSIFICATION_RECEIPT.json',receipt)
    lines=['# LOW Full Replan 只读裁定','','主分类：**C — GENUINE_DRIVECLARIFY_FULL_REPLAN_WITHOUT_ASK**。12/12 源码—事件溯源 PASS。','','TASK_EQUIVALENT 只表示两个候选任务彼此等价，不表示选中候选与当前原生路线等价。冻结 RQ1 决策选择 ACT、候选 A；V11 supervisor 检出当前路线与选中路线不等价，调用与 ASK 后相同的 admissibility、transaction、Full Replan 回调和在线路线 owner。各次 COMMIT_NOW，原生路线身份改变，候选停靠偏移内点与名义路线相差超过 1 m；官方终点不变。后续原生观测证明模型消费了新路线。','','分类 B 能描述 ACT 触发时序，但不能覆盖已经执行同一科学 Full Replan 机制的事实，所以由 C 控制。历史 answer-conditioned receipt 的 schema 名称不证明发生了回答：其 answer_receipt=false，ASK/answer 时钟均 null。','','|运行|提交仿真时间 s|提交 frame|路线内点最大差 m|前 route identity|后 route identity|','|---|---:|---:|---:|---|---|']
    for x in ledger:lines.append(f"|{x['run_id']}|{x['timestamps']['full_replan']['started_simulation_time_s']:.6f}|{x['first_consumption']['frame']}|{x['selected_vs_nominal_max_interior_point_delta_m']:.6f}|{x['route_state_before']['native_active_route_identity']}|{x['route_state_after']['native_active_route_identity']}|")
    lines += ['','A0 有共同的初始 set_global_plan，因此概括返回 PARTIAL；对应的中途候选任务 Full Replan 为 NO。12 次 A0 均没有在线安装收据，A0 源码也无此事务路径。','','控制器仍为 agent_simlingo.LingoAgent.control_pid；安装本身不写 VehicleControl、不调用额外模型、不新建全局 planner，但改变后续模型路线条件、route-switch 状态与缓存，因此可能改变驾驶行为。不能以直接控制写入为 0 推导方法干预为 0。','','推荐历史展示措辞：LOW 的 ASK=0、WAIT ticks=0、直接控制干预 ticks=0；同时发生 12 次无 ASK 的真实方法侧 Full Replan。只能称“未产生乘客问答或 WAIT 成本”，不能称总交互/执行成本为零。V2 已有最终报告没有作零干扰声明，科学判决及历史计数全部保留。','','证据限度：历史未为 request、admission、ACT 分别记录独立时钟，也未单独保存安装前 geometry-only digest；账本明确保留 null。源码顺序、commit 仿真时钟、同 tick 原生消费、前后 route identity、候选内点几何和每个原始文件 SHA256 联合支撑裁定，不制造缺失事件。','','逐次完整证据见 LOW_FULL_REPLAN_EVENT_LEDGER.json；源代码与函数及 SHA256 均逐次列出。']
    (R/'LOW_FULL_REPLAN_FORENSICS.md').write_text('\n'.join(lines)+'\n')
    (R/'FUTURE_REPLAN_METRIC_SEMANTICS.md').write_text('''# 未来重规划指标语义

`clarification_triggered_full_replan`：有可追溯 durable ASK 与其对应合法 answer，且由该回答解析任务触发并成功提交的完整路线变更次数。不能仅因 receipt schema 含 answer-conditioned 就计入。

`nonclarification_route_or_task_installation`：没有 ASK/answer 因果链的已提交路线或任务安装次数；逐事件必须区分 INITIAL_NATIVE_ROUTE、ACT_SELECTED_TASK、FULL_REPLAN 等 mechanism subtype。

`nonclarification_full_replan`：前项中实际使用科学 Full Replan admission/commit/owner 的子集。此次 LOW=12，属于真实 A1 方法侧干预/执行成本。此项不是与父项相加的第三个互斥计数。

冻结方法未变时，未来 LOW 必须保留这些真实 Full Replan，分别报告次数、原因、几何是否改变、路线消费证据和延迟。不允许压制事件或仅更名去除成本。初始 A0/A1 共同行为另以 INITIAL_NATIVE_ROUTE subtype 展示，比较增量时明确分母与阶段。ASK_count、WAIT_ticks、直接控制写入各自独立。

未来事件至少保存 event_id、trigger、cause ASK/answer ID（可空）、mechanism subtype、owner、request/admission/commit timestamps、pre/post route identity 与 geometry hash、native consumption、第二控制写入及额外 forward。历史不足不补造。
''')
    print(json.dumps(receipt))
if __name__=='__main__':main()
