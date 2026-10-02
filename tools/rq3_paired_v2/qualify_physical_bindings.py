#!/usr/bin/env python3
"""用静止测量夹具验证真实 CARLA 状态提取；绝非 A0/A1 策略运行。"""
import json
import math
from pathlib import Path
import queue
import sys
import xml.etree.ElementTree as ET
import carla

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from driveclarify_rq3_paired_v2.task_evaluator import extract_task_outcome, score_task_truth

OUT = ROOT / 'reports/driveclarify_rq3_paired_comparison_v2_task_binding_qualification_and_execution'


def v(x):
    return [float(x.x), float(x.y), float(x.z)]


def pose_at(region, longitudinal, lateral, height):
    a = math.radians(region['yaw_degrees'])
    x,y,z = region['center_xyz']
    return {'location': {'x':x+longitudinal*math.cos(a)-lateral*math.sin(a), 'y':y+longitudinal*math.sin(a)+lateral*math.cos(a), 'z':z+height}, 'rotation': {'pitch':0., 'yaw':region['yaw_degrees'], 'roll':0.}}


def transform(p):
    return carla.Transform(carla.Location(**p['location']), carla.Rotation(**p['rotation']))


def state(world, actor):
    snap=world.get_snapshot()
    location=actor.get_location()
    lane=world.get_map().get_waypoint(location, project_to_road=False, lane_type=carla.LaneType.Driving | carla.LaneType.Parking)
    velocity=actor.get_velocity()
    return {'frame': snap.frame, 'simulation_time_s':snap.timestamp.elapsed_seconds, 'xyz':v(location), 'yaw_degrees':actor.get_transform().rotation.yaw, 'speed_mps':math.sqrt(velocity.x**2+velocity.y**2+velocity.z**2), 'bbox_extent_xy_m':[actor.bounding_box.extent.x,actor.bounding_box.extent.y], 'lane_type':str(lane.lane_type) if lane else 'Outside', 'road_id':lane.road_id if lane else None, 'lane_id':lane.lane_id if lane else None}


def main():
    client=carla.Client('127.0.0.1',28740);client.set_timeout(20)
    world=client.get_world()
    assert world.get_map().name.endswith('Town03')
    previous=world.get_settings()
    settings=world.get_settings();settings.synchronous_mode=True;settings.fixed_delta_seconds=.05
    world.apply_settings(settings)
    manifest=json.loads((OUT/'V2_TEMPLATE_CANDIDATE_MANIFEST.json').read_text())
    public_route=Path(manifest['templates'][0]['official_route_context']['official_route_path'])
    weather_node=ET.parse(public_route).getroot().find('./route/weathers/weather')
    weather_values={k:float(val) for k,val in weather_node.attrib.items() if k!='route_percentage'}
    weather=carla.WeatherParameters(**weather_values);world.set_weather(weather)
    receipts=[];observations={};owned=[]
    try:
        for entry in manifest['templates']:
            binding=json.loads(Path(entry['task_binding_path']).read_text())
            layout=[]
            for region in binding['regions']:
                for along in [-6,6]:
                    for lateral in [-1.65,1.65]:
                        layout.append({'runtime_track_id':entry['template_id']+'-cone-'+str(len(layout)), 'scientific_role':'PUBLIC_MARKED_PARKING_BAY_CORNER', 'blueprint':'static.prop.trafficcone01', 'pose':pose_at(region,along,lateral,.10), 'attributes':{}})
            if entry['family']=='REF':
                if entry['level']=='HIGH':
                    placement=json.loads((OUT/'qualification/REFERENCE_PLACEMENT_INFRASTRUCTURE_TRIALS.json').read_text())['selected_pose_offsets']
                    referents=[(r,placement[str(r['official_route_index'])]['longitudinal_m']) for r in binding['regions']]
                else:
                    referents=[(binding['regions'][0],-9),(binding['regions'][0],9)]
                for region,along in referents:
                    layout.append({'runtime_track_id':entry['template_id']+'-white-van-'+str(len(layout)), 'scientific_role':'PUBLIC_REFERENT_NOT_GOLD', 'blueprint':'vehicle.mercedes.sprinter','pose':pose_at(region,along,2.6,.50),'attributes':{'color':'255,255,255'},'stationary_fixture':True,'public_associated_region':region['region_id']})
            if entry['family']=='ORD':
                probe=json.loads((OUT/'qualification/MAP_PROBE_RECEIPT.json').read_text())['route_rows'][20]
                early={'center_xyz':probe['parking_xyz'],'yaw_degrees':probe['parking_yaw']}
                half=1.25 if entry['level']=='HIGH' else 6.0
                for along in [-half,half]:
                    for lateral in [-1.65,1.65]:
                        layout.append({'runtime_track_id':entry['template_id']+'-opening1-'+str(len(layout)), 'scientific_role':'FIRST_COUNTABLE_MARKED_OPENING_SMALL' if entry['level']=='HIGH' else 'FIRST_COUNTABLE_MARKED_OPENING_CAR_SIZE', 'blueprint':'static.prop.trafficcone01','pose':pose_at(early,along,lateral,.10),'attributes':{}})
            layout_path=OUT/'candidate_assets'/(entry['condition']+'_LAYOUT.json')
            layout_path.write_text(json.dumps({'template_id':entry['template_id'],'actors':layout,'official_weather':weather_values,'same_for_both_true_intents':True,'grounding_status':'PHYSICAL_LAYOUT_FIXTURE_ONLY_NOT_YET_A0_COMPARABILITY'},indent=2)+'\n')
            spawned=[]
            placement_trials=[]
            for item in layout:
                bp=world.get_blueprint_library().find(item['blueprint'])
                for key,value in item['attributes'].items():
                    if bp.has_attribute(key):bp.set_attribute(key,str(value))
                actor=world.try_spawn_actor(bp,transform(item['pose']))
                if actor is None and item.get('public_associated_region'):
                    region=next(r for r in binding['regions'] if r['region_id']==item['public_associated_region'])
                    for along in [0,-9,9,-12,12]:
                        for lateral in [3.2,4.0,4.8,2.6]:
                            candidate_pose=pose_at(region,along,lateral,.50)
                            actor=world.try_spawn_actor(bp,transform(candidate_pose))
                            world.tick()
                            placement_trials.append({'id':item['runtime_track_id'],'along':along,'lateral':lateral,'spawn_pass':actor is not None})
                            if actor is not None:
                                item['pose']=candidate_pose
                                break
                        if actor is not None:break
                assert actor is not None, 'LAYOUT_SPAWN_FAILED:'+item['runtime_track_id']
                owned.append(actor);spawned.append(actor)
                world.tick()
                actor.set_simulate_physics(False)
            layout_path.write_text(json.dumps({'template_id':entry['template_id'],'actors':layout,'official_weather':weather_values,'same_for_both_true_intents':True,'placement_trials':placement_trials,'grounding_status':'PHYSICAL_LAYOUT_FIXTURE_ONLY_NOT_YET_A0_COMPARABILITY'},indent=2)+'\n')
            world.tick()
            physical=[]
            for region in binding['regions']:
                key=str(region['official_route_index'])
                bp=world.get_blueprint_library().find('vehicle.lincoln.mkz_2020')
                actor=world.try_spawn_actor(bp,transform(pose_at(region,0,0,.50)))
                assert actor is not None,'MEASUREMENT_FIXTURE_SPAWN_FAILED'
                owned.append(actor);world.tick();actor.set_simulate_physics(False)
                frames=[]
                for _ in range(27):
                    world.tick();frames.append(state(world,actor))
                outcome=extract_task_outcome(frames,binding)
                observation_path=OUT/'qualification'/(entry['condition']+'_'+region['region_id']+'_STATE_FIXTURE.json')
                observation_path.write_text(json.dumps({'scope':'SCRIPTED_STATIC_MEASUREMENT_FIXTURE_NOT_POLICY_RUN','actor_blueprint':'vehicle.lincoln.mkz_2020','trace':frames,'outcome':outcome},indent=2)+'\n')
                physical.append({'region':region['region_id'],'path':str(observation_path),'authoritative_state_extraction_pass':outcome['completed_regions']==[region['region_id']],'physical_car_footprint_fits':outcome['completed_regions']==[region['region_id']],'measurements':len(frames)})
                actor.destroy();owned.remove(actor);world.tick()
            # 从实际道路上的共同观察位置渲染布局；不是模型驱动轨迹。
            probe=json.loads((OUT/'qualification/MAP_PROBE_RECEIPT.json').read_text())
            view=probe['route_rows'][24]
            bp=world.get_blueprint_library().find('sensor.camera.rgb')
            bp.set_attribute('image_size_x','1280');bp.set_attribute('image_size_y','720');bp.set_attribute('fov','110')
            xyz=view['official_xyz'];camera=world.spawn_actor(bp,carla.Transform(carla.Location(xyz[0],xyz[1],xyz[2]+2),carla.Rotation(yaw=view['driving_yaw'])))
            owned.append(camera);frames=queue.Queue();camera.listen(frames.put)
            for _ in range(5):world.tick()
            image=frames.get(timeout=15)
            while not frames.empty():image=frames.get_nowait()
            image_path=OUT/'qualification'/(entry['condition']+'_PUBLIC_LAYOUT.png');image.save_to_disk(str(image_path))
            camera.stop();camera.destroy();owned.remove(camera)
            for actor in spawned:actor.destroy();owned.remove(actor)
            world.tick()
            receipts.append({'condition':entry['condition'],'spawn_count':len(layout),'all_layout_actors_spawned':True,'physical_binding_checks':physical,'public_image':str(image_path),'official_weather_preserved':True,'native_policy_runs':0,'model_forwards':0,'seeds_used':[]})
            print(entry['condition'], 'physical bindings', [x['authoritative_state_extraction_pass'] for x in physical],flush=True)
    finally:
        world.tick()
        for actor in owned:
            try:actor.destroy()
            except RuntimeError:pass
        world.tick()
        world.apply_settings(previous)
    (OUT/'qualification/PHYSICAL_BINDING_QUALIFICATION.json').write_text(json.dumps({'scope':'NON_FORMAL_INSTRUMENTATION_FIXTURES_ONLY_NOT_AGENT_POLICY_RUNS','rows':receipts,'pass':all(c['authoritative_state_extraction_pass'] for r in receipts for c in r['physical_binding_checks']),'native_development_runs':0,'formal_exposures':0,'seeds_used':[],'actor_cleanup_complete':not owned},indent=2)+'\n')


if __name__=='__main__':main()
