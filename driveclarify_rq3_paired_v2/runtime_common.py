"""两臂共享的场景装配与只读观测，不实现控制、规划、真意判定。"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import time

import carla
from srunner.scenariomanager.carla_data_provider import CarlaDataProvider
from agent_simlingo import LingoAgent


def atomic_json(path, value):
    path=Path(path)
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(value,sort_keys=True,allow_nan=False)+'\n')
    tmp.replace(path)


def sha_file(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(8*1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def native_metric_info():
    hero=CarlaDataProvider.get_hero_actor();pose=hero.get_transform()
    vector=lambda x:[float(x.x),float(x.y),float(x.z)]
    return {'acceleration':vector(hero.get_acceleration()),'angular_velocity':vector(hero.get_angular_velocity()),'forward_vector':vector(pose.get_forward_vector()),'right_vector':vector(pose.get_right_vector()),'location':vector(pose.location),'rotation':[pose.rotation.roll,pose.rotation.pitch,pose.rotation.yaw]}


class PairedObservationMixin:
    """精确委托原控制路径，记录全程状态及同一个 forward 的计数。"""

    def _spawn_public_runtime_actors(self):
        world=CarlaDataProvider.get_world()
        self._spawned_case_actors=[]
        rows=[]
        for item in self._method_input.get('runtime_actors',[]):
            blueprint=world.get_blueprint_library().find(item['blueprint'])
            if blueprint.has_attribute('role_name'):
                blueprint.set_attribute('role_name','v2_public_'+item['runtime_track_id'])
            for key,value in item.get('attributes',{}).items():
                if blueprint.has_attribute(key):blueprint.set_attribute(key,str(value))
            p=item['pose']
            actor=world.try_spawn_actor(blueprint,carla.Transform(carla.Location(**p['location']),carla.Rotation(**p['rotation'])))
            if actor is None:
                raise RuntimeError('V2_PUBLIC_SCENE_ACTOR_SPAWN_FAILED:'+item['runtime_track_id'])
            self._spawned_case_actors.append(actor)
            # 仅控制不属于驾驶 ego 的静态场景实体；两臂相同，不写 ego VehicleControl。
            actor.set_simulate_physics(False)
            rows.append({'runtime_track_id':item['runtime_track_id'],'actor_id':actor.id,'blueprint':item['blueprint'],'pose':p})
        atomic_json(Path(os.environ['DRIVECLARIFY_V11_OWNER_DIR'])/'V2_PUBLIC_SCENE_RECEIPT.json',{'actors':rows,'ego_control_writes':0,'world_tick_calls':0,'truth_read':False})

    def setup(self,path_to_conf_file,route_index=None,traffic_manager=None):
        self._v2_model_forward_count=0
        self._v2_control_return_count=0
        self._v2_first_frame=None
        self._v2_last_frame=None
        self._v2_trace_errors=[]
        self._v2_trace_count=0
        self._v2_raw_prompt_mismatches=0
        self._v2_started_wall=time.time()
        super().setup(path_to_conf_file,route_index=route_index,traffic_manager=traffic_manager)
        self._v2_trace_path=Path(os.environ['DRIVECLARIFY_V11_OWNER_DIR'])/'V2_NATIVE_STATE_TRACE.jsonl'
        self._v2_trace_stream=self._v2_trace_path.open('x')
        self._v2_hook=self.model.register_forward_hook(self._v2_observe_forward)
        self._v2_setup_frame=CarlaDataProvider.get_world().get_snapshot().frame
        controller_entry=self.control_pid.__func__
        native_controller=controller_entry is LingoAgent.control_pid
        frozen_delegate=(controller_entry.__module__=='driveclarify_clear_passthrough_v11.simlingo_agent' and controller_entry.__qualname__=='DriveClarifyV11SimLingoAgent.control_pid')
        if not (native_controller or frozen_delegate):
            raise RuntimeError('V2_CONTROLLER_IDENTITY_NOT_NATIVE')
        atomic_json(self._v2_trace_path.parent/'V2_RUNTIME_IDENTITY.json',{'checkpoint_path':str(self._checkpoint_path),'checkpoint_sha256':self._v11_config['checkpoint_sha256'],'controller':'agent_simlingo.LingoAgent.control_pid','controller_entry':controller_entry.__module__+'.'+controller_entry.__qualname__,'entry_is_native':native_controller,'entry_is_existing_frozen_observer_delegate':frozen_delegate,'raw_instruction':self._method_input['instruction'],'mode':self._v11_config['mode'],'config_sha256':sha_file(path_to_conf_file),'second_control_writer':False,'true_intent_in_runtime_config':False,'additional_model_execution_by_observer':0})

    def _v2_observe_forward(self,module,inputs,output):
        self._v2_model_forward_count+=1
        native_language=inputs[0].prompt.language_string
        if self._method_input['instruction'] not in self.prompt or not all(self._method_input['instruction'] in x for x in native_language):
            self._v2_raw_prompt_mismatches+=1
        return None

    def _v2_observe_state(self,terminal=False):
        world=CarlaDataProvider.get_world();hero=CarlaDataProvider.get_hero_actor()
        if world is None or hero is None:return
        snap=world.get_snapshot()
        if self._v2_last_frame==snap.frame:return
        loc=hero.get_location();pose=hero.get_transform();vel=hero.get_velocity()
        wp=world.get_map().get_waypoint(loc,project_to_road=False,lane_type=carla.LaneType.Driving|carla.LaneType.Parking)
        row={'frame':snap.frame,'simulation_time_s':snap.timestamp.elapsed_seconds,'xyz':[loc.x,loc.y,loc.z],'yaw_degrees':pose.rotation.yaw,'speed_mps':math.sqrt(vel.x**2+vel.y**2+vel.z**2),'bbox_extent_xy_m':[hero.bounding_box.extent.x,hero.bounding_box.extent.y],'lane_type':str(wp.lane_type) if wp else 'Outside','road_id':wp.road_id if wp else None,'lane_id':wp.lane_id if wp else None,'terminal_observation':terminal,'model_forward_count':self._v2_model_forward_count,'control_return_count':self._v2_control_return_count,'native_prompt':getattr(self,'prompt',None),'metric_info':native_metric_info()}
        row.update(bbox_world_vertices=[[v.x,v.y,v.z] for v in hero.bounding_box.get_world_vertices(pose)],roll_degrees=pose.rotation.roll,pitch_degrees=pose.rotation.pitch)
        self._v2_trace_stream.write(json.dumps(row,sort_keys=True,allow_nan=False)+'\n')
        self._v2_trace_stream.flush()
        if self._v2_trace_count%20==0:os.fsync(self._v2_trace_stream.fileno())
        self._v2_trace_count+=1
        if self._v2_first_frame is None:self._v2_first_frame=snap.frame
        self._v2_last_frame=snap.frame

    def run_step(self,input_data,timestamp,sensors=None):
        control=super().run_step(input_data,timestamp,sensors=sensors)
        self._v2_control_return_count+=1
        try:self._v2_observe_state()
        except Exception as exc:
            self._v2_trace_errors.append(repr(exc))
        return control

    def destroy(self,results=None):
        try:
            if hasattr(self,'_v2_trace_stream'):
                self._v2_observe_state(terminal=True)
                self._v2_trace_stream.flush();os.fsync(self._v2_trace_stream.fileno());self._v2_trace_stream.close()
                atomic_json(self._v2_trace_path.parent/'V2_TRACE_TERMINAL_RECEIPT.json',{'trace_count':self._v2_trace_count,'setup_frame':self._v2_setup_frame,'first_frame':self._v2_first_frame,'last_frame':self._v2_last_frame,'model_forward_count':self._v2_model_forward_count,'control_return_count':self._v2_control_return_count,'trace_errors':self._v2_trace_errors,'raw_prompt_mismatches':self._v2_raw_prompt_mismatches,'trace_sha256':sha_file(self._v2_trace_path),'observer_model_calls':0,'observer_ego_control_writes':0,'observer_world_tick_calls':0,'destroy_observed':True})
        finally:
            if hasattr(self,'_v2_hook'):self._v2_hook.remove()
            super().destroy(results=results)
