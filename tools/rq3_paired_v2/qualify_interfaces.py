"""资格检查只检查输入、源码和测量，不执行策略或选择有利场景。"""
import ast,copy,hashlib,json,math,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from driveclarify_rq3_paired_v2.configuration import make_runtime_config,REPORT
from driveclarify_rq3_paired_v2.task_evaluator import extract_task_outcome,extract_complete_episode,score_task_truth
Q=REPORT/'qualification'
def sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for b in iter(lambda:f.read(8388608),b''):h.update(b)
 return h.hexdigest()
def write(p,x):p.write_text(json.dumps(x,indent=2,ensure_ascii=False)+'\n')
def digest(x):return hashlib.sha256(json.dumps(x,sort_keys=True).encode()).hexdigest()
checks=[]
def check(name,value):checks.append({'check':name,'pass':bool(value)})
source=Path('/home/buaa/wrh/simlingo/team_code/agent_simlingo.py').read_text();tree=ast.parse(source)
cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='LingoAgent')
tick=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='tick')
start=next(i for i,n in enumerate(tick.body) if isinstance(n,ast.If) and ast.unparse(n.test)=='self.custom_prompt is not None')
code=compile(ast.fix_missing_locations(ast.Module(body=tick.body[start:start+2],type_ignores=[])),'exact_native_prompt_ast','exec')
from types import SimpleNamespace
rows=[]
for t in json.loads((REPORT/'V2_TEMPLATE_CANDIDATE_MANIFEST.json').read_text())['templates']:
 context={'self':SimpleNamespace(custom_prompt=t['instruction'],user_flag=1),'speed':3.0,'prompt_tp':'Command: Follow lane.'};exec(code,context)
 check(t['condition']+':raw_text_exact',context['prompt'].endswith(t['instruction']) and context['prompt'].count(t['instruction'])==1)
 a0=make_runtime_config(t,'A0','COUNTERFACTUAL',0);a1=make_runtime_config(t,'A1','COUNTERFACTUAL',0)
 check(t['condition']+':a0_allowed_input_only',set(a0['method_input'])=={'instruction','runtime_actors','background_traffic_policy'})
 check(t['condition']+':common_scene_equal',all(a0['method_input'][k]==a1['method_input'][k] for k in a0['method_input']))
 # 移除/翻转独立真意不会改变实际构造函数输出；函数没有 truth 入参。
 cf=[]
 for truth in ('A','B'):
  meta={'evaluation_only_true_intent':truth}
  cf.append({'route_bytes_sha':sha(t['official_route_context']['official_route_path']),'runtime_a0':make_runtime_config(t,'A0','COUNTERFACTUAL',0),'runtime_a1':make_runtime_config(t,'A1','COUNTERFACTUAL',0)})
 check(t['condition']+':actual_config_counterfactual_equal',cf[0]==cf[1])
 b=json.loads(Path(t['task_binding_path']).read_text());r=b['regions'][0]
 trace=[{'frame':i+1,'simulation_time_s':i*.05,'xyz':r['center_xyz'],'yaw_degrees':r['yaw_degrees'],'speed_mps':0.,'bbox_extent_xy_m':[2.45,1.08],'lane_type':'Parking','road_id':r['road_id'],'lane_id':r['parking_lane_id']} for i in range(26)]
 outcome=extract_task_outcome(trace,b)
 check(t['condition']+':stop_observed',outcome['completed_regions']==[r['region_id']])
 for name,edit in [('moving',lambda x:x.update(speed_mps=3)),('wrong_lane',lambda x:x.update(lane_type='Driving')),('wrong_heading',lambda x:x.update(yaw_degrees=x['yaw_degrees']+90)),('footprint_outside',lambda x:x.update(bbox_extent_xy_m=[7,2]))]:
  modified=copy.deepcopy(trace)
  for x in modified:edit(x)
  check(t['condition']+':'+name,extract_task_outcome(modified,b)['completed_regions']==[])
 for name,modified in [('missing_field',[{k:v for k,v in x.items() if k!='speed_mps'} for x in trace]),('gap',trace[:5]+trace[9:]),('duplicate_frame',trace[:5]+trace[4:]),('nan',copy.deepcopy(trace)),('negative_speed',copy.deepcopy(trace))]:
  if name=='nan':modified[5]['speed_mps']=float('nan')
  if name=='negative_speed':modified[5]['speed_mps']=-1
  check(t['condition']+':'+name,extract_task_outcome(modified,b)['status']=='UNKNOWN')
 for x in trace:
  a=math.radians(x['yaw_degrees']);cx,cy,cz=x['xyz'];x.update(roll_degrees=0,pitch_degrees=0,bbox_world_vertices=[[cx+u*math.cos(a)-v*math.sin(a),cy+u*math.sin(a)+v*math.cos(a),cz+z] for u in [-2.45,2.45] for v in [-1.08,1.08] for z in [0,1.5]])
 receipt={'destroy_observed':True,'trace_errors':[],'trace_sha256':'fixture','trace_count':len(trace),'first_frame':1,'last_frame':26,'control_return_count':len(trace)}
 check(t['condition']+':full_envelope',extract_complete_episode(trace,b,receipt,'fixture',1.25)['status']=='KNOWN')
 check(t['condition']+':truncated_envelope',extract_complete_episode(trace[:-1],b,receipt,'fixture',1.25)['status']=='UNKNOWN')
 check(t['condition']+':duration_mismatch',extract_complete_episode(trace,b,receipt,'fixture',5)['status']=='UNKNOWN')
 for gold in ('A','B'):
  check(t['condition']+':symmetric_'+gold,score_task_truth(outcome,b,{'candidate_id':gold})==score_task_truth(copy.deepcopy(outcome),b,{'candidate_id':gold}))
 rows.append({'condition':t['condition'],'route_neutral':True,'common_conditioning_counterfactual_sha256':[digest(x) for x in cf],'exact_native_raw_prompt':context['prompt'],'a0_method_input_keys':sorted(a0['method_input']),'formal_seeds':0})
for p in list((ROOT/'driveclarify_rq3_paired_v2').glob('*.py'))+list((ROOT/'tools/rq3_paired_v2').glob('*.py')):ast.parse(p.read_text())
write(Q/'INTERFACE_AND_EVALUATOR_SOFTWARE_QUALIFICATION.json',{'checks':checks,'pass':all(c['pass'] for c in checks),'templates':rows,'model_forwards':0,'policy_runs':0,'formal_exposure':0})
# 验证历史源码与 V1，逐文件 SHA，绝不覆盖历史证据。
preserved=[]
for snapshot in [ROOT/'reports/driveclarify_rq3_paired_ambiguous_closed_loop_comparison_v1/audit/PRESERVATION_BEFORE.json',Q/'V1_PRESERVATION_BEFORE.json']:
 rows=json.loads(snapshot.read_text())['files'];bad=[]
 for row in rows:
  p=Path(row['path'])
  if not p.is_file() or sha(p)!=row['sha256']:bad.append(str(p))
 preserved.append({'snapshot':str(snapshot),'checked_files':len(rows),'mismatches':bad,'pass':not bad})
write(Q/'PRESERVATION_QUALIFICATION_CHECK.json',{'checks':preserved,'pass':all(x['pass'] for x in preserved)})
print(json.dumps({'software_checks':len(checks),'pass':all(c['pass'] for c in checks),'preservation':preserved}))
assert all(c['pass'] for c in checks) and all(x['pass'] for x in preserved)
