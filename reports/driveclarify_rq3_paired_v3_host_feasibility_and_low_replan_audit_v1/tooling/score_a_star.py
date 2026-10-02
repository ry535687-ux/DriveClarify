import sys
from common import *
from task_evaluator import extract_task,score_interpretation
sys.path.insert(0,str(ROOT))
from driveclarify_rq3_paired_v2.official_metrics import extract_official

def evaluate(run,template):
 out=Path(run['output']);owner=out/'owner_evidence';issues=[]
 def optional(p):return read(p) if Path(p).exists() else {}
 process=optional(out/'process_job/PROCESS_RECEIPT.json');identity=optional(owner/'V2_RUNTIME_IDENTITY.json');receipt=optional(owner/'V2_TRACE_TERMINAL_RECEIPT.json')
 if not process.get('cleanup_pass'):issues.append('PROCESS_CLEANUP_NOT_CONFIRMED')
 if identity.get('checkpoint_sha256')!=CHECKPOINT_SHA:issues.append('NATIVE_IDENTITY_MISSING_OR_CHECKPOINT_MISMATCH')
 if identity.get('raw_instruction')!=template['explicit_instructions'][run['interpretation']]:issues.append('EXPLICIT_TASK_IDENTITY_MISMATCH')
 if identity.get('mode')!='NATIVE_SIMLINGO' or not identity.get('entry_is_native'):issues.append('NOT_FROZEN_NATIVE_CONTROLLER')
 if receipt.get('raw_prompt_mismatches')!=0:issues.append('NATIVE_PROMPT_EVIDENCE_MISSING_OR_MISMATCH')
 if receipt.get('observer_model_calls')!=0 or receipt.get('observer_ego_control_writes')!=0 or receipt.get('observer_world_tick_calls')!=0:issues.append('OBSERVER_EVIDENCE_MISSING_OR_INTERVENTION')
 if (owner/'oracle_exchange').exists() or (owner/'ONLINE_ROUTE_INSTALL_RECEIPT.json').exists():issues.append('A_STAR_FORBIDDEN_ORACLE_OR_ROUTE_INSTALL')
 metrics={};task={'status':'UNKNOWN','reason':'NO_NATIVE_TERMINAL_RECORD'};trace=[]
 trace_path=owner/'V2_NATIVE_STATE_TRACE.jsonl'
 if trace_path.exists():
  for line in trace_path.read_text().splitlines():
   try:trace.append(__import__('json').loads(line))
   except ValueError:issues.append('CORRUPT_NATIVE_TRACE_LINE')
 recs=optional(out/'official_checkpoint.json').get('_checkpoint',{}).get('records',[])
 if len(recs)==1:
  try:
   metrics=extract_official(recs[0]);p=owner/'V2_NATIVE_STATE_TRACE.jsonl'
   if p.exists():
    task=extract_task(trace,read(template['task_binding_path']),receipt,sha(p),metrics['duration_game_s'])
  except (KeyError,TypeError,ValueError) as e:issues.append('CORRUPT_EVIDENCE:'+repr(e))
 binding=read(template['task_binding_path']);scores={k:score_interpretation(task,binding,k) for k in ['1','2']};evaluable=bool(metrics and task['status']=='KNOWN' and not issues)
 tcsc=int(scores[run['interpretation']]['correct_local_task']==1 and metrics['native_completion']==1 and not metrics['major_safety_failure']) if evaluable else None
 return {**run,'scope':'DEVELOPMENT_QUALIFICATION_ONLY','A_STAR_TCSC':tcsc,'execution_evaluable':evaluable,'task_outcome':task,'both_interpretation_scores':scores,'official_metrics':metrics,'integrity_issues':issues,'native_forward_count':receipt.get('model_forward_count'),'native_exposure_observed':bool(receipt.get('model_forward_count',0) or any(x.get('model_forward_count',0) for x in trace)),'valid_scientific_failure':evaluable and tcsc==0,'retried':False,'seed_replaced':False,'process_receipt':process,'trace_sha256':receipt.get('trace_sha256')}

def summarize(template,results):
 by={k:[r for r in results if r['interpretation']==k] for k in ['1','2']};counts={k:sum(r['A_STAR_TCSC']==1 for r in rows) for k,rows in by.items()};evaluable=len(results)==6 and all(r['execution_evaluable'] for r in results)
 passed=evaluable and all(len(by[k])==3 and counts[k]>=2 for k in by)
 return {'template_id':template['template_id'],'condition':template['condition'],'tested':bool(results),'native_runs':len(results),'interpretation1':{'successes':counts['1'],'denominator':3,'evaluable':sum(r['execution_evaluable'] for r in by['1'])},'interpretation2':{'successes':counts['2'],'denominator':3,'evaluable':sum(r['execution_evaluable'] for r in by['2'])},'all_six_evaluable':evaluable,'qualified':passed,'result':'PASS' if passed else 'FAIL' if results else 'NOT_EXECUTED','selection_performance_rule':'P1 first, P2 only if P1 does not pass','run_ids':[r['run_id'] for r in results]}
