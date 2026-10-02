"""仅正式结束后的独立评估进程可调用；驾驶 agent 不导入本模块。"""
import hashlib,json,math
from pathlib import Path
from .task_evaluator import extract_complete_episode,score_task_truth
from .official_metrics import extract_official,run_official_postprocessing
from .configuration import REPORT,CHECKPOINT_SHA256

def read(path,default=None):
    path=Path(path)
    return json.loads(path.read_text()) if path.exists() else default

def evaluate_run(run,truth,template):
    output=Path(run['output']);owner=output/'owner_evidence';issues=[];unknown=[]
    process=read(output/'process_job/PROCESS_RECEIPT.json',{})
    if not process.get('cleanup_pass'):issues.append('OWNED_PROCESS_CLEANUP_NOT_CONFIRMED')
    identity=read(owner/'V2_RUNTIME_IDENTITY.json',{})
    receipt=read(owner/'V2_TRACE_TERMINAL_RECEIPT.json',{})
    if identity:
        if identity.get('checkpoint_sha256')!=CHECKPOINT_SHA256:issues.append('CHECKPOINT_IDENTITY_MISMATCH')
        if identity.get('raw_instruction')!=template['instruction']:issues.append('RAW_TEXT_IDENTITY_MISMATCH')
        if identity.get('additional_model_execution_by_observer')!=0:issues.append('OBSERVER_EXTRA_FORWARD')
        if identity.get('second_control_writer') is not False:issues.append('SECOND_CONTROL_WRITER')
    else:unknown.append('NO_NATIVE_RUNTIME_IDENTITY')
    if receipt and (receipt.get('raw_prompt_mismatches')!=0 or receipt.get('observer_ego_control_writes')!=0):issues.append('NATIVE_RAW_INPUT_OR_WRITER_INTEGRITY')
    exchange=owner/'oracle_exchange';release=read(exchange/'FORMAL_ANSWER_RELEASE_RECEIPT.json',{})
    if run['arm']=='A0' and exchange.exists():issues.append('A0_ORACLE_EXCHANGE_EXISTS')
    if release and (release.get('answer_released_after_durable_ask') is not True or release.get('pre_ask_answer_access_count')!=0):issues.append('PRE_ASK_TRUE_INTENT_LEAK')
    checkpoint=read(output/'official_checkpoint.json',{});records=checkpoint.get('_checkpoint',{}).get('records',[])
    metric={};task={'status':'UNKNOWN','reason':'NO_AUTHORITATIVE_TERMINAL_RECORD'};trace=[]
    if len(records)==1 and identity:
        rec=records[0]
        try:
            assert all(isinstance(rec['scores'][k],(float,int)) and math.isfinite(rec['scores'][k]) for k in ['score_composed','score_route','score_penalty'])
            assert rec['meta']['duration_game']>0
            metric=extract_official(rec)
            path=owner/'V2_NATIVE_STATE_TRACE.jsonl'
            if path.exists():
                trace=[json.loads(x) for x in path.read_text().splitlines()]
                task=extract_complete_episode(trace,read(template['task_binding_path']),receipt,hashlib.sha256(path.read_bytes()).hexdigest(),rec['meta']['duration_game'])
        except (AssertionError,KeyError,TypeError,ValueError,json.JSONDecodeError) as exc:
            unknown.append('AUTHORITATIVE_SCHEMA_UNAVAILABLE:'+repr(exc));metric={}
    else:unknown.append('AUTHORITATIVE_RECORD_OR_IDENTITY_UNAVAILABLE')
    goal=score_task_truth(task,read(template['task_binding_path']),truth)
    # 不用科学表现决定可评估性；任何完整记录的失败均保留。
    evaluable=bool(metric and task['status']=='KNOWN' and not issues)
    tcsc=int(bool(goal['correct_goal'] and metric['native_completion'] and not metric['major_safety_failure'])) if evaluable else None
    post={'Driving_Efficiency':None,'Driving_Smoothness':None,'availability_reason':'NO_COMPLETE_NATIVE_TRACE'}
    bridge=output/'offline_official_postprocessing'
    if evaluable:
        if bridge.exists():
            post=read(bridge/'OFFICIAL_POSTPROCESSING_RECEIPT.json',post)
        else:post=run_official_postprocessing(output,records[0],trace,bridge)
    supervision=read(owner/'V11_SUPERVISION_RECEIPT.json',{});counter=supervision.get('counters') or {}
    timeline=owner/'V11_SUPERVISION_TIMELINE.jsonl';steps=[json.loads(x) for x in timeline.read_text().splitlines()] if timeline.exists() else []
    asks=int(counter.get('ask_receipts',0) or 0)
    replan=read(owner/'RQ1_V2_FULL_REPLAN_RECEIPT.json',{});online=read(owner/'ONLINE_ROUTE_INSTALL_RECEIPT.json',{})
    if replan.get('additional_vla_forwards',0)!=0 or replan.get('second_control_writer',False):issues.append('FROZEN_METHOD_EXTRA_FORWARD_OR_WRITER')
    if issues:tcsc=None;evaluable=False
    return {'run_id':run['run_id'],'pair_id':run['pair_id'],'condition':run['condition'],'arm':run['arm'],'seed':run['seed'],'true_intent_evaluation_only':truth['candidate_id'],'TCSC':tcsc,'execution_evaluable':evaluable,'task_outcome':task,**goal,**metric,'Driving_Efficiency':post.get('Driving_Efficiency'),'Driving_Smoothness':post.get('Driving_Smoothness'),'official_postprocessing':post,'ASK_count':asks,'WAIT_ticks':sum(x.get('policy_action')=='WAIT' for x in steps),'Full_Replan_committed':int(bool(online.get('committed'))),'unnecessary_ASK':int(template['level']=='LOW' and asks>0),'direct_control_intervention_ticks':0 if not issues else None,'native_forward_count':receipt.get('model_forward_count'),'native_control_return_count':receipt.get('control_return_count'),'observer_forward_count':receipt.get('observer_model_calls'),'observer_control_write_count':receipt.get('observer_ego_control_writes'),'pre_ask_true_intent_reads':release.get('pre_ask_answer_access_count',0),'integrity_issues':issues,'missing_evidence_reasons':unknown,'process_receipt':process,'output':str(output)}
