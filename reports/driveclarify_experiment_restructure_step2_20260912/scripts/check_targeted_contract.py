"""本轮独立 CPU 逻辑案例；不导入 live，不改历史 expected，不作为论文样本。"""
from pathlib import Path
import ast
from dataclasses import dataclass
from enum import Enum
import json
from types import SimpleNamespace
import unittest
from typing import Any, Mapping, Optional, Tuple, List, Union

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parents[1]

def extract(path, names, namespace):
    tree=ast.parse((ROOT/path).read_text())
    selected=[x for x in tree.body if isinstance(x,(ast.FunctionDef,ast.ClassDef)) and x.name in names]
    module=ast.fix_missing_locations(ast.Module(body=selected,type_ignores=[]))
    exec(compile(module,str(OUT/'scripts/isolated_source_copy'), 'exec'), namespace)
    return namespace

env={'__name__':__name__,'dataclass':dataclass,'Enum':Enum,'Any':Any,'Mapping':Mapping,
     'Optional':Optional,'Tuple':Tuple,'List':List,'Union':Union,
     'TASK_COMPONENTS':('terminal_task_region','terminal_road_or_corridor','maneuver_obligation',
                        'irreversible_branch_obligation','goal_lane_or_side_obligation_if_task_relevant','task_completion_region')}
extract('driveclarify_rq1_v2/consequence.py',{'AmbiguityStatus','ConsequenceRelation','GateAction','TaskSignature','ConsequenceComparison','GateDecision','compare_task_signatures','consequence_gate'},env)
S=env['TaskSignature']; R=env['ConsequenceRelation']; G=env['GateAction']; A=env['AmbiguityStatus']

def signature(cid, values, relevant=('maneuver_obligation','task_completion_region')):
    return S(cid,relevant,values,True,'test-certificate-'+cid)

class TaskRelation(Enum):
    EQUIVALENT='TASK_EQUIVALENT'
    DIVERGENT='TASK_DIVERGENT'
    UNKNOWN='UNKNOWN'

class ContractError(Exception): pass
class CostBearingContractError(ContractError):
    def __init__(self,reason,**costs):
        super().__init__(reason)
        self.costs=costs

def isolated_fusion(revised, topology, trajectory, grounding):
    def component(value):
        if value is None: raise ContractError('HAND_AUTHORED_MISSING_COMPONENT')
        return value, {'hand_authored':True}
    mocks={'_trajectory':lambda *_:component(trajectory),'_grounding':lambda *_:component(grounding),
           '_topology':lambda *_:component(topology)}
    scope={'TaskRelation':TaskRelation,'Mapping':Mapping,'Any':Any,'ContractError':ContractError,
           'CostBearingContractError':CostBearingContractError,'FULL_FUSION_RULE':'original-test-copy',
           'REVISED_C_FUSION_RULE':'revised-test-copy','REVISION_SPEC':{'revision_id':'isolated'},
           'v3_methods':SimpleNamespace(**mocks,CostBearingContractError=CostBearingContractError),**mocks}
    path='driveclarify_rq1_conditional_supplement/judges_revised.py' if revised else 'driveclarify_rq1_grounded_relation_v3/methods.py'
    fn='c_m5_full_revised' if revised else '_full'
    extract(path,{fn},scope)
    return scope[fn]({}, {'grounding_threshold':0.5})

class AcceptedPrincipleCases(unittest.TestCase):
    # Expected 从用户 Q1/Q2 指定原则手写；运行不生成 expected。
    def test_complete_equal(self):
        v={'maneuver_obligation':'right','task_completion_region':'r1'}
        self.assertEqual(env['compare_task_signatures'](signature('a',v),signature('b',v)).relation,R.TASK_EQUIVALENT)
    def test_complete_divergent(self):
        a={'maneuver_obligation':'right','task_completion_region':'r1'}
        b={'maneuver_obligation':'left','task_completion_region':'r1'}
        self.assertEqual(env['compare_task_signatures'](signature('a',a),signature('b',b)).relation,R.TASK_CRITICAL)
    def test_missing_outranks_difference_on_other_required_field(self):
        a={'maneuver_obligation':'right','task_completion_region':'r1'}
        b={'maneuver_obligation':'left','task_completion_region':None}
        result=env['compare_task_signatures'](signature('a',a),signature('b',b))
        self.assertEqual(result.relation,R.UNKNOWN)
        self.assertEqual(result.differing_components,())
    def test_invalid_certificate_abstains(self):
        a=signature('a',{'task_completion_region':'r1'},('task_completion_region',))
        b=S('b',a.relevant_components,a.values,False,'test-invalid')
        self.assertEqual(env['compare_task_signatures'](a,b).relation,R.UNKNOWN)
    def test_nonrequired_fields_do_not_force_unknown(self):
        a=signature('a',{'task_completion_region':'r1'},('task_completion_region',))
        b=signature('b',{'task_completion_region':'r1'},('task_completion_region',))
        self.assertEqual(env['compare_task_signatures'](a,b).relation,R.TASK_EQUIVALENT)
    def test_unknown_grants_no_new_candidate(self):
        result=env['consequence_gate'](A.AMBIGUOUS,R.UNKNOWN,deterministic_candidate_id='a')
        self.assertEqual(result.action,G.UNKNOWN)
        self.assertIsNone(result.selected_candidate_id)
    def test_equivalent_without_candidate_is_not_act(self):
        result=env['consequence_gate'](A.AMBIGUOUS,R.TASK_EQUIVALENT,deterministic_candidate_id=None)
        self.assertEqual(result.action,G.UNKNOWN)
    def test_revised_no_task_large_trajectory_abstains(self):
        with self.assertRaises(CostBearingContractError):
            isolated_fusion(True,None,TaskRelation.DIVERGENT,None)
    def test_revised_no_task_agreement_equivalent_abstains(self):
        with self.assertRaises(CostBearingContractError):
            isolated_fusion(True,None,TaskRelation.EQUIVALENT,TaskRelation.EQUIVALENT)
    def test_revised_no_task_agreement_divergent_abstains(self):
        with self.assertRaises(CostBearingContractError):
            isolated_fusion(True,None,TaskRelation.DIVERGENT,TaskRelation.DIVERGENT)
    def test_revised_valid_task_not_overruled_by_trajectory(self):
        result=isolated_fusion(True,TaskRelation.EQUIVALENT,TaskRelation.DIVERGENT,None)
        self.assertEqual(result[0],TaskRelation.EQUIVALENT)

if __name__=='__main__':
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(AcceptedPrincipleCases)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    # 对新发现 Q2 注释矛盾的定向见证，不构成真实数据重算或 grounding provider。
    old_eq=isolated_fusion(False,None,TaskRelation.EQUIVALENT,TaskRelation.EQUIVALENT)[0].value
    old_div=isolated_fusion(False,None,TaskRelation.DIVERGENT,TaskRelation.DIVERGENT)[0].value
    receipt={'scope':'独立 AST 函数副本+手写输入；CPU-only；无项目模块导入；不改历史测试或标签',
             'tests_run':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),
             'all_passed':result.wasSuccessful(),'historical_tests_rerun':False,
             'q2_additional_branch_witness':{'no_topology_two_equivalent_original':old_eq,
                 'no_topology_two_divergent_original':old_div,'revised_both_cases':'CostBearingContractError -> UNKNOWN wrapper',
                 'scientific_status':'原版一致性分支确实存在，revised 实际均拒答；源码注释冲突保留；此非历史样本或可部署证据'}}
    (OUT/'evidence/TARGETED_CHECKS.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
    raise SystemExit(0 if result.wasSuccessful() else 1)
