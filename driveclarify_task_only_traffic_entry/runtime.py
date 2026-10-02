"""Install the existing task-only source gate, without changing driving agents."""
import hashlib
import json
import os
from pathlib import Path
import time

STAGE = 'DEV_TASK_ONLY_TRAFFIC_ABLATION_WIRING_NOT_LIVE_RUN'
POLICY = {
    'schema': 'driveclarify.task-only-traffic.v1',
    'enabled': True,
    'traffic_condition': 'TASK_ONLY_TRAFFIC',
    'gate_module': 'driveclarify_clear_task_diagnostic.traffic',
    'background_entries': ['_spawn_actor', '_spawn_actors', '_spawn_source_actor'],
    'current_round_execution': 'IMPORT_ONLY_NO_ADDITIONAL_LIVE_AUTHORIZED',
}
_INSTALLED = False
_STREAM = None


def canonical_digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def validate_config(config):
    if config.get('ablation_stage') != STAGE:
        raise ValueError('EXPLICIT_DEV_TRAFFIC_ONLY_STAGE_REQUIRED')
    if config.get('controlled_traffic') != POLICY:
        raise ValueError('EXPLICIT_UNCHANGED_TASK_ONLY_TRAFFIC_POLICY_REQUIRED')
    if config.get('mode') != 'DRIVECLARIFY' or config.get('training_performed') is not False:
        raise ValueError('EXISTING_UNTRAINED_DRIVECLARIFY_RUNTIME_REQUIRED')
    if config.get('ablation',{}).get('configuration_id') not in ('ABL_FULL','ABL_TRAJ_ONLY'):
        raise ValueError('ORIGINAL_ABLATION_ARM_REQUIRED')
    forbidden = {'diagnostic','clear_instruction','supplied_candidate_id','evaluation_candidate_id',
                 'true_intent','true_passenger_intent','oracle_choice','answer_candidate_id','selected_candidate_id'}
    def inspect(value, path=''):
        if isinstance(value,dict):
            for key,item in value.items():
                item_path=path+'.'+str(key) if path else str(key)
                if str(key).lower() in forbidden:
                    raise ValueError('CORRECT_ANSWER_DIAGNOSTIC_FIELD_FORBIDDEN:'+item_path)
                inspect(item,item_path)
        elif isinstance(value,list):
            for index,item in enumerate(value):
                inspect(item,path+'['+str(index)+']')
    inspect(config)
    method=config.get('method_input',{})
    if not isinstance(method.get('instruction'),str) or not method['instruction'].strip():
        raise ValueError('ORIGINAL_LANGUAGE_INSTRUCTION_REQUIRED')
    if config['ablation']['configuration_id']=='ABL_TRAJ_ONLY':
        signatures=method.get('task_signatures',[])
        if len(signatures)!=2 or any(set(row)!={'candidate_id','binding_id'} for row in signatures):
            raise ValueError('ORIGINAL_TRAJECTORY_TASK_SIGNATURE_FIREWALL_FAILED')
    return config['controlled_traffic']


def install():
    global _INSTALLED,_STREAM
    if _INSTALLED:
        raise RuntimeError('TASK_ONLY_GATE_ALREADY_INSTALLED_IN_THIS_PROCESS')
    config_path=Path(os.environ['DRIVECLARIFY_V11_CONFIG'])
    raw=config_path.read_bytes(); config=json.loads(raw)
    policy=validate_config(config)
    actual_sha=hashlib.sha256(raw).hexdigest()
    expected=os.environ.get('DRIVECLARIFY_V11_CONFIG_SHA256')
    if expected is not None and expected!=actual_sha:
        raise ValueError('TASK_ONLY_CONFIG_SHA256_MISMATCH')
    directory=Path(os.environ['DRIVECLARIFY_TASK_ONLY_TRAFFIC_RECEIPT_DIR'])
    directory.mkdir(parents=True,exist_ok=False)
    # These are the actual evaluator's module objects under its PYTHONPATH.
    # No CARLA client/world/model/agent object is created by this entry.
    from srunner.scenarios.background_activity import BackgroundBehavior
    from driveclarify_clear_task_diagnostic import traffic
    _STREAM=(directory/'BACKGROUND_GATE_TIMELINE.jsonl').open('x')
    def emit(row):
        value=dict(row,run_id=config['run_id'],wall_time_epoch=time.time(),
                   clock='WALL_CLOCK_ONLY_NO_WORLD_REQUEST_BY_TRAFFIC_LOGGER',
                   explicit_traffic_only_entry=True)
        _STREAM.write(json.dumps(value,sort_keys=True,allow_nan=False)+'\n')
        _STREAM.flush()
    originals=traffic.install_background_gate(BackgroundBehavior,enabled=True,emit=emit)
    _INSTALLED=True
    receipt={'schema':'driveclarify.task-only-traffic-entry-install.v1',
             'run_id':config['run_id'],'arm':config['ablation']['configuration_id'],
             'stage':config['ablation_stage'],'config_path':str(config_path),'config_sha256':actual_sha,
             'controlled_traffic':policy,'traffic_policy_sha256':canonical_digest(policy),
             'gate_source_path':traffic.__file__,'gate_source_sha256':hashlib.sha256(Path(traffic.__file__).read_bytes()).hexdigest(),
             'original_background_methods':{key:value.__module__+'.'+value.__qualname__ for key,value in originals.items()},
             'config_language_mutated':False,'agent_wrapped_or_subclassed':False,
             'diagnostic_correct_task_supplied':False,'generation_attempted_by_entry':False,
             'model_or_agent_instantiated_by_entry':False,'CARLA_client_or_service_started_by_entry':False,
             'live_validation':'NOT_PERFORMED_IMPORT_ONLY_THIS_ROUND'}
    (directory/'INSTALL_RECEIPT.json').write_text(json.dumps(receipt,indent=2,sort_keys=True)+'\n')
    return receipt
