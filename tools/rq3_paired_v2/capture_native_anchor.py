"""无策略相机/车身几何夹具：使用冻结原生传感器位姿及分辨率。"""
import json,math,queue,sys
from pathlib import Path
import carla
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from driveclarify_rq3_paired_v2.configuration import REPORT,make_runtime_config
Q=REPORT/'qualification'
client=carla.Client('127.0.0.1',28740);client.set_timeout(20);w=client.get_world();original=w.get_settings();settings=w.get_settings();settings.synchronous_mode=True;settings.fixed_delta_seconds=.05;w.apply_settings(settings)
probe=json.loads((Q/'MAP_PROBE_RECEIPT.json').read_text());row=probe['route_rows'][14];rows=[];owned=[]
def transform(p):return carla.Transform(carla.Location(**p['location']),carla.Rotation(**p['rotation']))
try:
 for t in json.loads((REPORT/'V2_TEMPLATE_CANDIDATE_MANIFEST.json').read_text())['templates']:
  cfg=make_runtime_config(t,'A0','CAMERA_ONLY',0)
  for item in cfg['method_input']['runtime_actors']:
   bp=w.get_blueprint_library().find(item['blueprint'])
   for k,v in item['attributes'].items():
    if bp.has_attribute(k):bp.set_attribute(k,str(v))
   a=w.try_spawn_actor(bp,transform(item['pose']));assert a is not None
   owned.append(a);w.tick();a.set_simulate_physics(False)
  p=carla.Transform(carla.Location(*row['official_xyz']),carla.Rotation(yaw=row['driving_yaw']))
  fixture=w.try_spawn_actor(w.get_blueprint_library().find('vehicle.lincoln.mkz_2020'),carla.Transform(carla.Location(p.location.x,p.location.y,p.location.z+.5),p.rotation));assert fixture
  owned.append(fixture);w.tick();fixture.set_simulate_physics(False)
  bbox=fixture.bounding_box
  bp=w.get_blueprint_library().find('sensor.camera.rgb');bp.set_attribute('image_size_x','1024');bp.set_attribute('image_size_y','512');bp.set_attribute('fov','110')
  camera=w.spawn_actor(bp,carla.Transform(carla.Location(x=-1.5,z=2)),attach_to=fixture);owned.append(camera);q=queue.Queue();camera.listen(q.put)
  for _ in range(10):w.tick()
  im=q.get(timeout=20)
  while not q.empty():im=q.get_nowait()
  path=Q/(t['condition']+'_NATIVE_ANCHOR_14.png');im.save_to_disk(str(path))
  rows.append({'condition':t['condition'],'image':str(path),'native_camera':{'position':[-1.5,0,2],'resolution':[1024,512],'fov':110},'anchor_index':14,'bbox_location':[bbox.location.x,bbox.location.y,bbox.location.z],'bbox_rotation':[bbox.rotation.roll,bbox.rotation.pitch,bbox.rotation.yaw],'bbox_extent':[bbox.extent.x,bbox.extent.y,bbox.extent.z],'model_forwards':0,'native_policy_runs':0})
  camera.stop()
  for a in reversed(owned):a.destroy()
  owned=[];w.tick()
finally:
 for a in reversed(owned):a.destroy()
 w.tick();w.apply_settings(original)
(Q/'NATIVE_ANCHOR_SENSOR_FIXTURES.json').write_text(json.dumps({'scope':'STATIC_SENSOR_FIXTURE_NOT_POLICY','rows':rows,'seeds_used':[],'scientific_exposure':0},indent=2)+'\n')
print(json.dumps(rows))
