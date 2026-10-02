"""A0：原生 LingoAgent，仅原文输入、评测接口兼容和共用观测。"""
import json
import os
from pathlib import Path

from agent_simlingo import LingoAgent
from driveclarify_rq3_paired_v2.runtime_common import PairedObservationMixin,native_metric_info,sha_file


def get_entry_point():
    return 'V2NativeA0Agent'


class RawLanguageNativeAgent(LingoAgent):
    def _load_config(self):
        if hasattr(self,'_v11_config'):return
        path=Path(os.environ['DRIVECLARIFY_V11_CONFIG']).resolve()
        if sha_file(path)!=os.environ['DRIVECLARIFY_V11_CONFIG_SHA256']:
            raise RuntimeError('V2_A0_CONFIG_HASH_MISMATCH')
        config=json.loads(path.read_text())
        if config['mode']!='NATIVE_SIMLINGO':raise RuntimeError('V2_A0_WRONG_MODE')
        allowed={'instruction','runtime_actors','background_traffic_policy'}
        if set(config['method_input'])-allowed:raise RuntimeError('V2_A0_NON_NATIVE_TASK_INPUT_FORBIDDEN')
        self._checkpoint_path=Path(config['checkpoint'])
        if sha_file(self._checkpoint_path)!=config['checkpoint_sha256']:
            raise RuntimeError('V2_A0_CHECKPOINT_HASH_MISMATCH')
        self._v11_config=config
        self._method_input=config['method_input']

    def set_global_plan(self,global_plan_gps,global_plan_world_coord):
        self._load_config()
        self.org_dense_route_gps=global_plan_gps
        self.org_dense_route_world_coord=global_plan_world_coord
        LingoAgent.set_global_plan(self,global_plan_gps,global_plan_world_coord)

    def setup(self,path_to_conf_file,route_index=None,traffic_manager=None):
        self._load_config()
        if Path(path_to_conf_file).resolve()!=Path(os.environ['DRIVECLARIFY_V11_CONFIG']).resolve():
            raise RuntimeError('V2_A0_AGENT_CONFIG_PATH_MISMATCH')
        self._output=Path(os.environ['DRIVECLARIFY_V11_OWNER_DIR'])
        self._output.mkdir(parents=True,exist_ok=False)
        LingoAgent.setup(self,str(self._checkpoint_path)+'+v2_'+self._v11_config['run_id'],route_index=None)
        self.custom_prompt=self._method_input['instruction']
        self.user_flag=1
        self._spawn_public_runtime_actors()

    def get_metric_info(self):return native_metric_info()

    def destroy(self,results=None):
        for actor in getattr(self,'_spawned_case_actors',[]):
            try:actor.destroy()
            except RuntimeError:pass
        LingoAgent.destroy(self,results=results)


class V2NativeA0Agent(PairedObservationMixin,RawLanguageNativeAgent):
    pass
