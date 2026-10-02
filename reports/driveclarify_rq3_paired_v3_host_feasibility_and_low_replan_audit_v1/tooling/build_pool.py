"""仅按公开地图、任务几何和语义生成；无策略表现读取。"""
import carla, math, xml.etree.ElementTree as E
from common import *

def main():
 assert not (R/'V3_CANDIDATE_POOL_FREEZE_RECEIPT.json').exists()
 route=Path('/home/buaa/wrh/simlingo/leaderboard/data/bench2drive_split/bench2drive_167.xml')
 xodr=Path('/home/buaa/CARLA_0.9.15/CarlaUE4/Content/Carla/Maps/OpenDrive/Town03.xodr')
 m=carla.Map('Town03',xodr.read_text());r=E.parse(route).getroot().find('route')
 raw=[carla.Location(**{k:float(v) for k,v in p.attrib.items()}) for p in r.findall('./waypoints/position')]
 start=m.get_waypoint(raw[0]);rows=[]
 # 原生地图一米采样，给离线评估器与公开标记定位；绝不输入模型/RoutePlanner。
 for i in range(96):
  ws=start.next(float(i)) if i else [start]
  if not ws:break
  w=ws[0];left=w.get_left_lane();right=w.get_right_lane()
  assert w.road_id==67 and w.lane_id==-2 and left.lane_id==-1 and left.lane_type==carla.LaneType.Driving
  assert not w.is_junction and str(w.lane_change) in ['Left','Both']
  p=w.transform.location;t=left.transform.location
  rows.append({'s_m':i,'xyz':[p.x,p.y,p.z],'yaw':w.transform.rotation.yaw,'road_id':w.road_id,'start_lane_id':w.lane_id,'target_lane_id':left.lane_id,'lane_width_m':left.lane_width,'target_xyz':[t.x,t.y,t.z],'lane_change':str(w.lane_change),'shoulder_width_m':right.lane_width})
 def pose(s,offset):
  row=rows[round(s)];theta=math.radians(row['yaw']);x,y,z=row['xyz']
  return {'location':{'x':x-math.sin(theta)*offset,'y':y+math.cos(theta)*offset,'z':z+.08},'rotation':{'pitch':0.,'roll':0.,'yaw':row['yaw']}}
 templates=[]
 for condition in FAMILIES:
  family=condition.split('-')[0];high=condition.endswith('-C')
  for rank in [1,2]:
   tid=f'V3-HOST-{condition}-P{rank}'
   # P2: 同一合法长弧上的预先固定 +2 m 局部机会变体。
   offset=2*(rank-1);bounds=[[10+offset,25+offset],[28+offset,43+offset]] if high else [[20+offset,37+offset]]
   zones=[{'zone_id':tid+f'-Z{i+1}','s_min_m':a,'s_max_m':b} for i,(a,b) in enumerate(bounds)]
   mapping={'1':zones[0]['zone_id'],'2':zones[-1]['zone_id']};actors=[]
   def add(bp,s,o,attrs=None,role='PUBLIC_TASK_MARKER'):
    p=pose(s,o)
    if bp.startswith('vehicle.'):p['location']['z']+=.45
    actors.append({'runtime_track_id':tid+'-'+str(len(actors)),'blueprint':bp,'pose':p,'attributes':attrs or {},'scientific_role':role})
   for a,b in bounds:
    # 标记在肩部，不占任一行车道；两个边界标出机会的纵向范围。
    add('static.prop.trafficcone01',a,2.4);add('static.prop.trafficcone01',b,2.4)
   near=sum(bounds[0])/2;far=sum(bounds[-1])/2
   if family=='REF':
    ss=[near,far] if high else [near-3,near+3]
    for s in ss:add('vehicle.mercedes.sprinter',s,3.3,{'color':'255,255,255'},'PUBLIC_STATIONARY_REFERENT')
    ambiguous='Change into the lane immediately to your left in the cone-marked zone beside the white van, then continue on the assigned route.'
    meanings=['the nearer white van','the farther white van'];rationale='两个白色厢式车指称；HIGH 分属两个机会，LOW 同一机会内两个指称。'
   elif family=='LMK':
    ss=[near,far] if high else [near-3,near+3]
    for s in ss:add('static.prop.kiosk_01',s,3.3,role='PUBLIC_FIXED_ROADSIDE_LANDMARK')
    ambiguous='Change into the lane immediately to your left in the cone-marked zone beside the small roadside kiosk building, then continue on the assigned route.'
    meanings=['the nearer fixed roadside kiosk','the farther fixed roadside kiosk'];rationale='两个原生静态路边 kiosk 地标；HIGH 分属两个机会，LOW 共享一个机会。地标不以运行时候选标签替代。'
   elif family=='ORD':
    # 路肩告示牌定义计数域；HIGH 第一对锥桶没有告示牌，LOW 两对都有。
    extra=[4+offset,7+offset]
    add('static.prop.trafficcone01',extra[0],2.4);add('static.prop.trafficcone01',extra[1],2.4)
    add('static.prop.streetsign',sum(extra)/2,3.3)
    add('static.prop.streetsign',far,3.3)
    ambiguous='Change into the lane immediately to your left at the second marked roadside opportunity, then continue on the assigned route.'
    meanings=['the second pair of roadside cones, counting every pair'+(' including the unsigned pair' if high else ''),'the second pair of roadside cones with a roadside sign, counting only signed pairs']
    rationale='second 序数不变；计数全部锥桶对或仅附告示牌的锥桶对。HIGH all 的第二项是 Z1、signed 的第二项是 Z2；LOW 两种第二项均为唯一任务区。最初的短标记仅是计数线索，不是待执行任务。'
   else:
    ambiguous='Change into the lane immediately to your left at a suitable distance ahead in a cone-marked zone, then continue on the assigned route.'
    meanings=[f'a suitable distance means the near zone {bounds[0][0]} to {bounds[0][1]} metres ahead of the starting position',f'a suitable distance means the far zone {bounds[-1][0]} to {bounds[-1][1]} metres ahead of the starting position']
    rationale='适宜距离约束欠定，两个合法距离区间对应不同局部变道时机；路线终点不编码距离选择。'
   explicit={}
   for k,meaning in zip(['1','2'],meanings):
    zone=next(z for z in zones if z['zone_id']==mapping[k]);which='nearer' if mapping[k]==zones[0]['zone_id'] else 'farther'
    zonephrase=f'the {which} cone-bounded zone' if high else 'the single long cone-bounded zone'
    explicit[k]=f'Your unambiguous local task is to change exactly one lane to the left in {zonephrase}. I mean {meaning}. Stay in your current lane until that zone; complete the change fully inside its two roadside cone boundaries, and remain fully in the adjacent lane for at least 0.25 seconds. The chosen zone extends from {zone["s_min_m"]} to {zone["s_max_m"]} metres along the road from the starting position. Then continue safely on the assigned route. Do not perform the local change in another marked zone.'
   binding={'version':'V3_FIRST_ADJACENT_LANE_ENTRY_1','template_id':tid,'lane_map':rows,'road_id':67,'start_lane_id':-2,'target_lane_id':-1,'zones':zones,'candidate_zone_map':mapping,'full_body_margin_m':0.,'settled_min_duration_s':.25,'max_sample_gap_s':.1,'heading_limit_degrees':20.,'unknown_policy':'missing authoritative episode evidence -> UNKNOWN; valid failure incl wrong zone/no change/collision/noncompletion -> TCSC 0','no_A1_internal_variables':True}
   path=R/'candidate_assets'/tid;path.mkdir(parents=True,exist_ok=True)
   write(path/'TASK_BINDING.json',binding);write(path/'PUBLIC_ACTORS.json',actors)
   common={'route':str(route),'route_sha256':sha(route),'destination':[raw[-1].x,raw[-1].y,raw[-1].z],'public_actors_sha256':digest(actors),'ambiguous_instruction':ambiguous}
   templates.append({'template_id':tid,'condition':condition,'family':family,'level':'HIGH' if high else 'LOW','rank':rank,'instruction':ambiguous,'interpretations':dict(zip(['1','2'],meanings)),'explicit_instructions':explicit,'family_rationale':rationale,'task_relation':'TASK_CRITICAL' if high else 'TASK_EQUIVALENT','task_binding_path':str(path/'TASK_BINDING.json'),'actors_path':str(path/'PUBLIC_ACTORS.json'),'official_route':str(route),'official_route_sha256':sha(route),'common_route_context':common,'route_neutrality':{'truth_is_runtime_route_operand':False,'route_context_digest_interpretation1':digest(common),'route_context_digest_interpretation2':digest(common),'same_official_target_points_and_destination':True,'official_route_contains_no_local_lane_switch':True,'performance_independence_claim':False},'A0_interface':'native raw text + shared scene, no candidate geometry or truth','generation_criteria':['daylight public markers; native camera visibility qualification','same-direction adjacent driving lane with legal Left lane change','legal adjacent-lane opportunities independent of the official emergency scenario','unchanged official XML and scenario','deterministic first lane-entry evaluator','frozen pre-performance P1 then P2 +2m geometry variant'],'historical_template_reused':False,'development_performance_used':False,'ambiguous_performance_used':False})
 pool={'stage':'RQ3_PAIRED_V3_HOST_FEASIBILITY_AND_LOW_REPLAN_AUDIT_V1','created_utc':now(),'templates':templates,'total_templates':14,'USC_E_excluded':True,'family_order':FAMILIES,'within_family_order':['P1','P2'],'generation_inputs':[{'path':str(route),'sha256':sha(route)},{'path':str(xodr),'sha256':sha(xodr)}],'native_policy_exposure_at_creation':0,'qualification_threshold_frozen':'all 6 evaluable, >=2/3 per interpretation, validity/neutrality/leakage/source PASS','P3_forbidden':True}
 write(R/'V3_HOST_CANDIDATE_POOL.json',pool)
 (R/'V3_HOST_CANDIDATE_POOL.md').write_text('# 前瞻任务宿主候选池\n\n14 个新变道时机模板；不复用 V2 停靠任务。共同背景是未修改的官方 bench2drive_167.xml，Town03、road67，原车道 -2、左邻同向车道 -1；地图明确允许 Left 变道。所有模型输入只使用同一官方路线和原生语言接口。保留原生应急车辆场景；场景 actor 仅在路肩。P1 先于 P2，P2 仅作固定 +2 m 变体，不按表现择优。\n\n不以预测能成功作为资格；真正的可执行性只由冻结后的 A* 判定。\n\n|模板|关系|解释 1|解释 2|\n|---|---|---|---|\n'+'\n'.join(f'|{t["template_id"]}|{t["task_relation"]}|{t["interpretations"]["1"]}|{t["interpretations"]["2"]}|' for t in templates)+'\n')
 print('created',len(templates))
if __name__=='__main__':main()
