"""native 地图/实体/相机资格；无 ego、模型、种子、策略表现。"""
import carla,math,queue,time
from common import *

def main():
 c=carla.Client('127.0.0.1',28860);c.set_timeout(90);w=c.get_world();assert w.get_map().name.endswith('Town03');m=w.get_map()
 w.set_weather(carla.WeatherParameters(cloudiness=70.,fog_density=50.,precipitation=60.,precipitation_deposits=60.,sun_altitude_angle=45.,sun_azimuth_angle=-1.,wetness=0.,wind_intensity=60.))
 pool=read(R/'V3_HOST_CANDIDATE_POOL.json');receipts=[]
 for t in pool['templates']:
  actors=[];rows=[];b=read(t['task_binding_path'])
  try:
   for a in read(t['actors_path']):
    bp=w.get_blueprint_library().find(a['blueprint'])
    for k,v in a['attributes'].items():
     if bp.has_attribute(k):bp.set_attribute(k,str(v))
    pose=carla.Transform(carla.Location(**a['pose']['location']),carla.Rotation(**a['pose']['rotation']))
    obj=w.try_spawn_actor(bp,pose);assert obj is not None,a
    obj.set_simulate_physics(False);actors.append(obj)
    verts=obj.bounding_box.get_world_vertices(pose)
    # 检查公开标记完整包围盒未占行车道（车辆最高顶点可能找不到路，仍核查二维投影）。
    native=m.get_waypoint(pose.location,project_to_road=False,lane_type=carla.LaneType.Driving)
    occupied=[]
    for v in verts:
     ground=carla.Location(x=v.x,y=v.y,z=pose.location.z)
     if m.get_waypoint(ground,project_to_road=False,lane_type=carla.LaneType.Driving) is not None:occupied.append([v.x,v.y,v.z])
    rows.append({'id':a['runtime_track_id'],'actor_id':obj.id,'blueprint':a['blueprint'],'pose':a['pose'],'bbox_extent':[obj.bounding_box.extent.x,obj.bounding_box.extent.y,obj.bounding_box.extent.z],'bbox_world_vertices':[[v.x,v.y,v.z] for v in verts],'center_in_driving_lane':native is not None,'bbox_vertices_in_driving_lane':occupied,'pass':native is None and not occupied})
   bp=w.get_blueprint_library().find('sensor.camera.rgb');bp.set_attribute('image_size_x','1280');bp.set_attribute('image_size_y','720');bp.set_attribute('fov','110');p=b['lane_map'][0];xyz=p['xyz'];camera=w.spawn_actor(bp,carla.Transform(carla.Location(x=xyz[0],y=xyz[1],z=xyz[2]+2),carla.Rotation(yaw=p['yaw'])));actors.append(camera);q=queue.Queue();camera.listen(q.put)
   for _ in range(5):im=q.get(timeout=30)
   out=R/'geometry'/(t['template_id']+'.png');im.save_to_disk(str(out));camera.stop()
   row={'template_id':t['template_id'],'actors':rows,'screenshot':str(out),'screenshot_sha256':sha(out),'frame':im.frame,'model_forwards':0,'native_policy_runs':0,'seeds':[],'pass':all(x['pass'] for x in rows)}
   receipts.append(row);write(R/'geometry/NATIVE_SCENE_QUALIFICATION.json',{'created_utc':now(),'scope':'NO_EGO_NO_POLICY_NO_SEEDS','templates':receipts,'pass':len(receipts)==14 and all(x['pass'] for x in receipts)})
   print(t['template_id'],row['pass'],flush=True)
  finally:
   for a in reversed(actors):
    if a.is_alive:a.destroy()
if __name__=='__main__':main()
