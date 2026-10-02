"""软件测试夹具，不是 A* 结果或原生执行。"""
import copy,math
from common import *
from task_evaluator import extract_task,score_interpretation

def witness(binding,zone,speed=2.,duration=.5,oversize=False):
 geo=binding['lane_map'][round((zone['s_min_m']+zone['s_max_m'])/2)]
 def row(i,xyz,lane):
  theta=math.radians(geo['yaw']);verts=[]
  for a in [-2.4,2.4]:
   for b in ([-3.,3.] if oversize else [-1.,1.]):
    for z in [0.,1.5]:verts.append([xyz[0]+a*math.cos(theta)-b*math.sin(theta),xyz[1]+a*math.sin(theta)+b*math.cos(theta),xyz[2]+z])
  return {'frame':i+1,'simulation_time_s':i*.05,'xyz':xyz,'yaw_degrees':geo['yaw'],'speed_mps':speed,'bbox_extent_xy_m':[2.4,1.],'bbox_world_vertices':verts,'roll_degrees':0.,'pitch_degrees':0.,'lane_type':'Driving','road_id':binding['road_id'],'lane_id':lane}
 trace=[row(0,binding['lane_map'][0]['xyz'],binding['start_lane_id'])]+[row(i,geo['target_xyz'],binding['target_lane_id']) for i in range(1,round(duration/.05)+2)]
 return trace

def envelope(trace):return {'destroy_observed':True,'trace_errors':[],'trace_sha256':'TEST','trace_count':len(trace),'first_frame':trace[0]['frame'],'last_frame':trace[-1]['frame'],'control_return_count':len(trace)}
def score(trace,b):return extract_task(trace,b,envelope(trace),'TEST',trace[-1]['simulation_time_s']-trace[0]['simulation_time_s'])
def main():
 checks=[]
 for t in read(R/'V3_HOST_CANDIDATE_POOL.json')['templates']:
  b=read(t['task_binding_path'])
  for zone in b['zones']:
   tr=witness(b,zone);out=score(tr,b)
   for interpretation in ['1','2']:
    expected=int(b['candidate_zone_map'][interpretation]==zone['zone_id']);actual=score_interpretation(out,b,interpretation)['correct_local_task']
    checks.append({'template':t['template_id'],'test':'positive/other-interpretation specificity','zone':zone['zone_id'],'interpretation':interpretation,'pass':expected==actual})
   checks += [{'template':t['template_id'],'test':'oversize body cannot count','pass':score(witness(b,zone,oversize=True),b)['completed_zone'] is None},{'template':t['template_id'],'test':'too brief cannot count','pass':score(witness(b,zone,duration=.1),b)['completed_zone'] is None}]
   broken=copy.deepcopy(tr);broken[1]['frame']+=1
   checks.append({'template':t['template_id'],'test':'frame gap UNKNOWN','pass':score(broken,b)['status']=='UNKNOWN'})
   checks.append({'template':t['template_id'],'test':'missing terminal UNKNOWN','pass':extract_task(tr,b,{},'TEST',.5)['status']=='UNKNOWN'})
  tr=witness(b,b['zones'][0]);tr=[{**x,'lane_id':b['start_lane_id'],'xyz':b['lane_map'][0]['xyz']} for x in tr]
  checks.append({'template':t['template_id'],'test':'no lane change is known zero','pass':score(tr,b)['status']=='KNOWN' and score(tr,b)['completed_zone'] is None})
 assert all(c['pass'] for c in checks),[c for c in checks if not c['pass']]
 write(R/'geometry/TASK_EVALUATOR_VALIDATION.json',{'scope':'SYNTHETIC_SOFTWARE_ONLY_NO_MODEL','checks':checks,'pass':True,'native_runs':0})
 print('PASS',len(checks))
if __name__=='__main__':main()
