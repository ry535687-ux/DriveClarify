"""科学接口回归：冻结真实fixtures、主动路径身份以及无上下文边界。"""
import copy
import json
from pathlib import Path
import pytest

from driveclarify_transparent_bypass_v2.activation import activation,select_implementation
from driveclarify_transparent_bypass_v2.benchmark_agent import ObservedNativeAgent,active_loader
from driveclarify_rq3.simlingo_agent import DriveClarifyRQ3SimLingoAgent,DEADLINE_CONTRACT
from agent_simlingo import LingoAgent

ROOT=Path('/home/buaa/wrh/DriveClarify')
FIXTURES=sorted((ROOT/'reports/driveclarify_rq3_paired_comparison_v2_task_binding_qualification_and_execution/formal/runtime_configs').glob('*-A1.json'))

@pytest.mark.parametrize('path',FIXTURES,ids=lambda p:p.stem)
def test_all_frozen_active_fixtures_route_to_same_semantics(path):
    context=json.loads(path.read_text())['method_input'];before=copy.deepcopy(context)
    assert activation(context)==(True,[])
    cls,active,_=select_implementation('A1',context,ObservedNativeAgent,active_loader)
    assert active
    for name in ['_load_v11_config','set_global_plan','_init','tick','run_step','control_pid','_evaluate_consequences',
                 '_runtime_evidence_fields','_commit_resolved_route','_read_passenger_answer','_advance_supervision']:
        assert getattr(cls,name) is getattr(DriveClarifyRQ3SimLingoAgent,name)
    assert context==before
    assert DEADLINE_CONTRACT.total_reserved_simulation_s==pytest.approx(1.20)

@pytest.mark.parametrize('context',[None,{}, {'instruction':''},{'instruction':None},{'instruction':'Follow the assigned route.'},
 {'instruction':'x','alternatives':[],'task_signatures':[]}])
def test_incomplete_context_skips_active_loader_entirely(context):
    def forbidden():raise AssertionError('ACTIVE_PATH_MUST_NOT_BE_LOADED')
    for arm in ['A0','A1']:
        cls,active,_=select_implementation(arm,context,ObservedNativeAgent,forbidden)
        assert cls is ObservedNativeAgent and active is False
        assert cls.tick is LingoAgent.tick
        assert cls.run_step is LingoAgent.run_step
        assert cls.control_pid is LingoAgent.control_pid
        assert cls.set_global_plan is LingoAgent.set_global_plan

def test_active_setup_only_removes_evaluator_logging_suffix(monkeypatch):
    monkeypatch.delenv('DC_B2D_ACTIVE_CONFIG_PATH',raising=False)
    calls=[]
    def old_setup(self,path,route_index=None,traffic_manager=None):calls.append((path,route_index,traffic_manager))
    monkeypatch.setattr(DriveClarifyRQ3SimLingoAgent,'setup',old_setup)
    cls=active_loader();obj=object.__new__(cls)
    marker=object();obj.setup('/original/config.json+official_save_name',route_index=7,traffic_manager=marker)
    assert calls==[('/original/config.json',7,marker)]

def test_complete_context_binds_original_frozen_config_to_real_evaluator_entry(monkeypatch,tmp_path):
    import driveclarify_transparent_bypass_v2.benchmark_agent as entry
    original=FIXTURES[0];frozen=json.loads(original.read_text())
    interface=tmp_path/'interface.json';interface.write_text(json.dumps({'context':frozen['method_input'],'active_runtime_config_path':str(original)}))
    monkeypatch.setenv('DC_B2D_INTERFACE_CONFIG',str(interface));monkeypatch.setenv('DC_B2D_ARM','A1')
    monkeypatch.setenv('DC_B2D_ATTEMPT_DIR',str(tmp_path));monkeypatch.delenv('DRIVECLARIFY_V11_OWNER_DIR',raising=False)
    for name in ['DC_B2D_ACTIVE_CONFIG_PATH','DRIVECLARIFY_V11_CONFIG','DRIVECLARIFY_V11_CONFIG_SHA256']:
        monkeypatch.delenv(name,raising=False)
    assert entry.get_entry_point()=='SelectedAgent'
    calls=[]
    monkeypatch.setattr(DriveClarifyRQ3SimLingoAgent,'setup',lambda self,path,route_index=None,traffic_manager=None:calls.append(path))
    obj=object.__new__(entry.SelectedAgent);obj.setup(frozen['checkpoint']+'+official_log_suffix')
    assert calls==[str(original.resolve())]
    assert json.loads(original.read_text())==frozen

def test_no_context_entry_never_installs_active_environment(monkeypatch,tmp_path):
    import driveclarify_transparent_bypass_v2.benchmark_agent as entry
    interface=tmp_path/'interface.json';interface.write_text(json.dumps({'context':None}))
    monkeypatch.setenv('DC_B2D_INTERFACE_CONFIG',str(interface));monkeypatch.setenv('DC_B2D_ARM','A1')
    monkeypatch.setenv('DC_B2D_ATTEMPT_DIR',str(tmp_path))
    for name in ['DC_B2D_ACTIVE_CONFIG_PATH','DRIVECLARIFY_V11_CONFIG','DRIVECLARIFY_V11_CONFIG_SHA256']:
        monkeypatch.delenv(name,raising=False)
    assert entry.get_entry_point()=='SelectedAgent' and entry.SelectedAgent is ObservedNativeAgent
    import os
    assert 'DC_B2D_ACTIVE_CONFIG_PATH' not in os.environ and 'DRIVECLARIFY_V11_CONFIG' not in os.environ

def test_a0_never_activates_even_for_complete_context():
    context=json.loads(FIXTURES[0].read_text())['method_input']
    def forbidden():raise AssertionError('A0_MUST_REMAIN_NATIVE')
    cls,active,_=select_implementation('A0',context,ObservedNativeAgent,forbidden)
    assert cls is ObservedNativeAgent and not active

@pytest.mark.parametrize('field',['instruction','alternatives','task_signatures','route_source','background_traffic_policy'])
def test_missing_any_required_operand_prevents_activation(field):
    context=json.loads(FIXTURES[0].read_text())['method_input'];context.pop(field)
    assert activation(context)[0] is False
