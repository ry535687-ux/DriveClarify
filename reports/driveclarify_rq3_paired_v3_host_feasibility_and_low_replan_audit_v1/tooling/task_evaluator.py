"""冻结的离线、两解释共用、纯轨迹/原生状态任务评估器。"""
import math, sys
from common import ROOT
sys.path.insert(0,str(ROOT))
from driveclarify_rq3_paired_v2.task_evaluator import extract_complete_episode as validate_envelope

VERSION='V3_FIRST_ADJACENT_LANE_ENTRY_1'
def project(xyz, binding, target=False):
    key='target_xyz' if target else 'xyz'
    row=min(binding['lane_map'],key=lambda r:math.dist(xyz[:2],r[key][:2]))
    theta=math.radians(row['yaw']);dx=xyz[0]-row[key][0];dy=xyz[1]-row[key][1]
    return row['s_m']+dx*math.cos(theta)+dy*math.sin(theta),-dx*math.sin(theta)+dy*math.cos(theta),row

def extract_task(trace,binding,receipt,trace_sha,official_duration_s):
    # 已冻结 V2 全程原生证据包校验仅负责完整性；不复用停靠任务终点。
    checked=validate_envelope(trace,{'regions':[]},receipt,trace_sha,official_duration_s)
    if checked['status']!='KNOWN':return {'status':'UNKNOWN','reason':checked.get('reason'),'completed_zone':None}
    if trace[0]['road_id']!=binding['road_id'] or trace[0]['lane_id']!=binding['start_lane_id']:
        return {'status':'UNKNOWN','reason':'UNEXPECTED_NATIVE_START_LANE','completed_zone':None}
    first=None;zone=None;settled_start=None;completed=None;event=None;invalid=False
    for row in trace:
        s,_,geo=project(row['xyz'],binding)
        in_target=row['road_id']==binding['road_id'] and row['lane_id']==binding['target_lane_id'] and row['lane_type']=='Driving'
        if first is None and in_target:
            first={'frame':row['frame'],'s_m':s,'simulation_time_s':row['simulation_time_s']}
            hits=[z for z in binding['zones'] if z['s_min_m']<=s<=z['s_max_m']]
            zone=hits[0] if len(hits)==1 else None
            if zone is None:invalid=True
        if first is None or invalid or completed:continue
        if not in_target:
            # 已入邻道后返回、反复跨线不能用下一次进道替换第一次任务。
            invalid=True;continue
        projections=[project(v,binding,target=True) for v in row['bbox_world_vertices']]
        angle=abs((row['yaw_degrees']-geo['yaw']+180)%360-180)
        full=(all(zone['s_min_m']<=a<=zone['s_max_m'] and abs(b)<=g['lane_width_m']/2 and abs(v[2]-g['target_xyz'][2])<=2 for (a,b,g),v in zip(projections,row['bbox_world_vertices'])) and angle<=binding['heading_limit_degrees'] and abs(row['roll_degrees'])<=20 and abs(row['pitch_degrees'])<=20)
        if not full:settled_start=None
        elif settled_start is None:settled_start=row['simulation_time_s']
        elif row['simulation_time_s']-settled_start+1e-9>=binding['settled_min_duration_s']:
            completed=zone['zone_id'];event={'first_center_entry':first,'full_body_hold_start_s':settled_start,'completed_frame':row['frame'],'completed_time_s':row['simulation_time_s']}
        if s>zone['s_max_m'] and not completed:invalid=True
    return {'status':'KNOWN','completed_zone':completed,'first_adjacent_lane_center_entry':first,'completion_event':event,'version':VERSION}

def score_interpretation(outcome,binding,interpretation):
    if outcome['status']!='KNOWN':return {'correct_local_task':None,'wrong_local_task':None}
    correct=outcome['completed_zone']==binding['candidate_zone_map'][str(interpretation)]
    wrong=outcome['completed_zone'] is not None and not correct
    return {'correct_local_task':int(correct),'wrong_local_task':int(wrong)}
