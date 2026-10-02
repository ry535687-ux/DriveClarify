"""所有正式尝试结束后执行冻结分析；不重试任何 native cell。"""
import csv,hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from driveclarify_rq3_paired_v2.configuration import REPORT as R
from driveclarify_rq3_paired_v2.scoring import evaluate_run
from driveclarify_rq3_paired_v2.analysis_statistics import paired_binary,paired_continuous

def read(p):return json.loads(Path(p).read_text())
def write(p,x):Path(p).write_text(json.dumps(x,indent=2,ensure_ascii=False,allow_nan=False)+'\n')
def sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for b in iter(lambda:f.read(8388608),b''):h.update(b)
 return h.hexdigest()
def main():
 frozen=read(R/'formal/FREEZE_RECEIPT.json');assert sha(R/'formal/FREEZE_MANIFEST.json')==frozen['freeze_digest']
 roster=read(R/'formal/FORMAL_RUN_ROSTER.json')['runs'];assert len(roster)==72
 progress=read(R/'formal_native/CURRENT_PROGRESS.json');assert progress['state']=='ALL_72_ATTEMPTS_COMPLETE'
 templates={t['condition']:t for t in read(R/'V2_TEMPLATE_CANDIDATE_MANIFEST.json')['templates']}
 truth={p['pair_id']:p for p in read(R/'evaluation_only/TRUE_INTENT_ROSTER.json')['pairs']}
 results=R/'results';results.mkdir(exist_ok=True);rows=[]
 for run in roster:
  x=evaluate_run(run,truth[run['pair_id']],templates[run['condition']]);rows.append(x);write(results/(run['run_id']+'_RESULT.json'),x)
 pairs=[]
 for pair_id in dict.fromkeys(x['pair_id'] for x in roster):
  arms={x['arm']:x for x in rows if x['pair_id']==pair_id};assert set(arms)=={'A0','A1'}
  t=truth[pair_id];pairs.append({'pair_id':pair_id,'condition':t['condition'],'level':templates[t['condition']]['level'],'true_intent':t['candidate_id'],'A0':arms['A0'],'A1':arms['A1'],'TCSC_evaluable':all(arms[a]['TCSC'] is not None for a in ['A0','A1'])})
 high=[p for p in pairs if p['level']=='HIGH'];low=[p for p in pairs if p['level']=='LOW']
 binary=['TCSC','correct_goal','wrong_goal','native_completion','safe_completion','collision','official_success'];continuous=['Driving_Score','Route_Completion','Driving_Efficiency','Driving_Smoothness']
 def analyses(group):
  output={}
  for key in binary+continuous:
   valid=[p for p in group if all(p[a].get(key) is not None and not p[a]['integrity_issues'] for a in ['A0','A1'])]
   fn=paired_binary if key in binary else paired_continuous
   output[key]={**fn([p['A0'][key] for p in valid],[p['A1'][key] for p in valid]),'planned_pairs':len(group),'missing_pairs':len(group)-len(valid),'included_pair_ids':[p['pair_id'] for p in valid]}
  return output
 high_results=analyses(high);low_results=analyses(low)
 by_condition={c:analyses([p for p in pairs if p['condition']==c]) for c in templates}
 counts={c:sum(p['TCSC_evaluable'] for p in pairs if p['condition']==c) for c in templates}
 high_gate=sum(p['TCSC_evaluable'] for p in high)>=22 and all(counts[c]>=5 for c in templates if templates[c]['level']=='HIGH')
 low_gate=sum(p['TCSC_evaluable'] for p in low)>=10 and all(counts[c]>=3 for c in templates if templates[c]['level']=='LOW')
 mismatches=[]
 for row in read(R/'formal/FREEZE_MANIFEST.json')['files']:
  if not Path(row['path']).is_file() or sha(row['path'])!=row['sha256']:mismatches.append(row['path'])
 preservation=[]
 for snapshot in [ROOT/'reports/driveclarify_rq3_paired_ambiguous_closed_loop_comparison_v1/audit/PRESERVATION_BEFORE.json',R/'qualification/V1_PRESERVATION_BEFORE.json']:
  source=read(snapshot);bad=[]
  for item in source['files']:
   if not Path(item['path']).is_file() or sha(item['path'])!=item['sha256']:bad.append(item['path'])
  preservation.append({'snapshot':str(snapshot),'checked_files':len(source['files']),'mismatches':bad})
 issues=[{'run_id':r['run_id'],'issues':r['integrity_issues']} for r in rows if r['integrity_issues']]
 integrity=not (mismatches or issues or any(p['mismatches'] for p in preservation))
 endpoint=high_results['TCSC']
 supported=high_gate and integrity and endpoint['risk_difference']>0 and endpoint['exact_mcnemar_p']<.05 and endpoint['ci95'][0]>0
 verdict='NOT_EVALUABLE_DRIVECLARIFY_PAIRED_SUPERIORITY' if not high_gate or not integrity else ('SUPPORTED_DRIVECLARIFY_IMPROVES_TASK_CORRECT_SAFE_COMPLETION' if supported else 'NOT_SUPPORTED_DRIVECLARIFY_IMPROVES_TASK_CORRECT_SAFE_COMPLETION')
 status='PASS_RQ3_PAIRED_V2_COMPARISON_COMPLETE' if integrity else 'BLOCKED_RQ3_PAIRED_V2_OTHER_INTEGRITY_FAILURE'
 # 全24对的保守缺失界限，同时保留完整证据对子集的冻结推断。
 lower=sum((p['A1']['TCSC'] if p['A1']['TCSC'] is not None else 0)-(p['A0']['TCSC'] if p['A0']['TCSC'] is not None else 1) for p in high)/24
 upper=sum((p['A1']['TCSC'] if p['A1']['TCSC'] is not None else 1)-(p['A0']['TCSC'] if p['A0']['TCSC'] is not None else 0) for p in high)/24
 summary={'stage':'RQ3_PAIRED_COMPARISON_V2_TASK_BINDING_QUALIFICATION_AND_EXECUTION','execution_status':status,'scientific_verdict':verdict,'freeze_digest':frozen['freeze_digest'],'planned_pairs':36,'planned_native_runs':72,'completed_formal_attempts':72,'native_runs_exposed':sum(r['native_forward_count'] is not None for r in rows),'high_planned_pairs':24,'high_evaluable_pairs':sum(p['TCSC_evaluable'] for p in high),'low_planned_pairs':12,'low_evaluable_pairs':sum(p['TCSC_evaluable'] for p in low),'per_condition_evaluable_pairs':counts,'high_evaluability_gate':high_gate,'low_evaluability_gate':low_gate,'HIGH':high_results,'LOW':low_results,'per_condition':by_condition,'full_24_pair_TCSC_RD_missing_data_bounds':[lower,upper],'LOW_interaction_counts':{k:sum(p['A1'][k] or 0 for p in low) for k in ['ASK_count','WAIT_ticks','Full_Replan_committed','unnecessary_ASK','direct_control_intervention_ticks']},'integrity_pass':integrity,'freeze_mismatches':mismatches,'run_integrity_issues':issues,'historical_preservation':preservation,'scientific_retry_count':0,'seed_replacement_count':0,'development_native_runs':2,'permanently_excluded_development_seeds':[1731584188,1529594113],'scientific_exposure_before_freeze':0,'benchmark_label':'Bench2Drive evaluator metrics on the paired ambiguity subset','official_full_leaderboard_claim':False,'historical_RQ3_V3_unchanged':True}
 write(R/'PAIRED_COMPARISON_RESULTS.json',summary);write(R/'PAIRED_EPISODE_RESULTS.json',{'pairs':pairs});write(R/'INTEGRITY_AUDIT.json',{'pass':integrity,'freeze_mismatches':mismatches,'run_issues':issues,'historical_preservation':preservation})
 fields=['run_id','pair_id','condition','arm','seed','true_intent_evaluation_only','TCSC','correct_goal','wrong_goal','native_completion','safe_completion','collision','official_success','Driving_Score','Route_Completion','infraction_penalty','Driving_Efficiency','Driving_Smoothness','outside_route_lanes_union_count','route_deviation','red_light','stop_sign','route_timeout','scenario_timeouts','vehicle_blocked','ASK_count','WAIT_ticks','Full_Replan_committed','execution_evaluable']
 with (R/'ALL_72_NATIVE_RESULTS.csv').open('w',newline='') as f:
  writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(rows)
 def table(data,title):
  lines=['# '+title,'','| 指标 | 有效对/计划对 | A0 | A1 | A1−A0 | 配对95%CI | exact McNemar p |','|---|---:|---:|---:|---:|---|---:|']
  for k,v in data.items():
   if k in binary: a,b=v.get('a0_successes'),v.get('a1_successes');d=v.get('risk_difference')
   else:a,b=v.get('a0_mean'),v.get('a1_mean');d=v.get('mean_paired_difference')
   lines.append('| '+' | '.join(map(str,[k,str(v['n'])+'/'+str(v['planned_pairs']),a,b,d,v.get('ci95'),v.get('exact_mcnemar_p')]))+' |')
  return '\n'.join(lines)+'\n'
 (R/'TABLE_HIGH_PAIRED_RESULTS.md').write_text(table(high_results,'HIGH配对结果'))
 (R/'TABLE_LOW_NEGATIVE_CONTROL.md').write_text(table(low_results,'LOW独立对照结果')+'\nA1交互计数：`'+json.dumps(summary['LOW_interaction_counts'])+'`。\n')
 lines=['# V2 配对比较最终报告','', '`'+status+'`。科学结论：`'+verdict+'`。','', '七个必需模板在正式冻结前均通过资格。正式冻结摘要：`'+frozen['freeze_digest']+'`。','', f"完成{summary['completed_formal_attempts']}/72个正式尝试，实际native暴露{summary['native_runs_exposed']}次。HIGH有效{summary['high_evaluable_pairs']}/24对，LOW有效{summary['low_evaluable_pairs']}/12对。",'', '每族有效对：`'+json.dumps(counts)+'`。','', '## 主终点','',f"HIGH TCSC：A0={endpoint.get('a0_successes')}/{endpoint['n']}，A1={endpoint.get('a1_successes')}/{endpoint['n']}；配对风险差={endpoint.get('risk_difference')}，保守95%CI={endpoint.get('ci95')}，双侧exact McNemar p={endpoint.get('exact_mcnemar_p')}。",'', '完整2×2表（行A0/列A1；00双方失败、01仅A1成功、10仅A0成功、11双方成功）：`'+json.dumps(endpoint.get('table_rows_A0_columns_A1'))+'`。','', '该科学结论只由冻结 HIGH TCSC 规则控制；ASK、重规划或 Driving Score 不替代主终点。未建立优效也不等于证明两臂等效。','', '## 资格与开发隔离','', '七族 route-neutral、A0 comparable、任务可观测、对称 evaluator、两种真意可平衡均YES。HIGH各族真意3/3；LOW2/2。开发native2次，永久排除seeds1731584188、1529594113；另一次启动在world/evaluator前端口占用退出。正式冻结前科学暴露0。完整资格见 V2_QUALIFICATION_RECEIPT.json。','', '## 官方评测与完整性','', 'Bench2Drive evaluator metrics on the paired ambiguity subset；这是单一官方路线背景下的受控歧义子集，不是官方全榜结果。安装的 evaluator、规则和公式未变。offroad/wrong lane 只能报告联合 outside_route_lanes，不能拆造两项。efficiency/smoothness 通过冻结的10Hz输入桥接调用 installed 官方函数；缺失保留null。','', '全部72行见 ALL_72_NATIVE_RESULTS.csv，全部36对及每次trace/原生收据路径见 PAIRED_EPISODE_RESULTS.json。HIGH及LOW完整配对表分别见 TABLE_HIGH_PAIRED_RESULTS.md 与 TABLE_LOW_NEGATIVE_CONTROL.md。','', '无科学重试、无种子替换；历史7566文件与V1 41文件的最终哈希检查见 INTEGRITY_AUDIT.json。历史 RQ3-V3 的 H-A NOT_EVALUABLE / H-B NOT_SUPPORTED / H-C SUPPORTED / RQ3_V3_NOT_SUPPORTED_INSUFFICIENT_EVALUABILITY 永久不变。未修改论文或开展第二骨干工作。']
 (R/'FINAL_REPORT.md').write_text('\n'.join(lines)+'\n')
 with (R/'COMMAND_LOG.md').open('a') as f:f.write('\n17. analyze_formal.py：72次正式尝试结束后执行冻结统计、生成全行结果及历史/冻结源哈希审计；不调用任何native运行。\n')
 print(json.dumps({'status':status,'verdict':verdict,'high_TCSC':endpoint,'high_evaluable':summary['high_evaluable_pairs'],'low_evaluable':summary['low_evaluable_pairs'],'integrity_pass':integrity}))
if __name__=='__main__':main()
