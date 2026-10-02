#!/usr/bin/env python3
"""只读源码/路线审计；不启动 CARLA、不调用模型、不产生基准分数。"""
import ast
import collections
import copy
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import types
import xml.etree.ElementTree as ET

ROOT = Path('/home/buaa/wrh/DriveClarify')
SIM = Path('/home/buaa/wrh/simlingo')
OUT = ROOT / 'reports/driveclarify_full_bench2drive_standard_benchmark_v1'
PRIOR = ROOT / 'reports/driveclarify_rq3_paired_v3_host_feasibility_and_low_replan_audit_v1'

def sha(p):
    h = hashlib.sha256()
    with Path(p).open('rb') as f:
        for b in iter(lambda: f.read(4 * 1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()

def digest(d):
    return hashlib.sha256(json.dumps(d, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()

def save(name, d):
    p = OUT / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, indent=2, ensure_ascii=False, allow_nan=False) + '\n')

def git(*args):
    return subprocess.check_output(['git', '-C', str(SIM), *args])

def tree_inventory(paths):
    files = sorted({p.resolve() for path in paths for p in ([path] if path.is_file() else path.rglob('*'))
                    if p.is_file() and '__pycache__' not in p.parts and '.git' not in p.parts
                    and p.suffix not in {'.pyc', '.pyo'}})
    rows = [{'path': str(p), 'sha256': sha(p), 'size': p.stat().st_size} for p in files]
    return {'count': len(rows), 'digest': digest(rows), 'files': rows}

def route_payload(e):
    return [e.tag, sorted((k, v) for k, v in e.attrib.items() if not (e.tag == 'route' and k == 'id')),
            (e.text or '').strip(), [route_payload(c) for c in e]]

def node(source, cls, method):
    c = next(n for n in ast.parse(Path(source).read_text()).body if isinstance(n, ast.ClassDef) and n.name == cls)
    return copy.deepcopy(next(n for n in c.body if isinstance(n, ast.FunctionDef) and n.name == method))

def main():
    # 全部输出限定在本阶段；记录当前现场而非重置历史。
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    groups = {
        'simlingo_scientific': [SIM/'team_code', SIM/'simlingo_training'],
        'driveclarify_scientific': [p for p in ROOT.glob('driveclarify_*') if p.is_dir() or p.suffix == '.py'],
        'bench2drive_evaluator': [SIM/'Bench2Drive/leaderboard'],
        'bench2drive_scenario_runner': [SIM/'Bench2Drive/scenario_runner'],
        'official_metric_scripts': [SIM/'Bench2Drive/tools'/n for n in ['merge_route_json.py', 'ability_benchmark.py', 'efficiency_smoothness_benchmark.py']],
        'carla_identity': [Path('/home/buaa/CARLA_0.9.15/VERSION'), Path('/home/buaa/CARLA_0.9.15/CarlaUE4.sh'), Path('/home/buaa/CARLA_0.9.15/CarlaUE4/Binaries/Linux/CarlaUE4-Linux-Shipping')],
    }
    identities = {}
    for name, paths in groups.items():
        inv = tree_inventory(paths)
        save('audit/' + name + '_inventory.json', inv)
        identities[name] = {k: inv[k] for k in ['count', 'digest']}
        print('hashed', name, inv['count'], flush=True)
    native = SIM/'outputs/simlingo/checkpoints/epoch=013.ckpt/pytorch_model.pt'
    frozen = ROOT/'reports/driveclarify_v3_short_prefix_a1_fast_track/a1_training_v2/selected/checkpoints/a1_selected.ckpt/pytorch_model.pt'
    checkpoint = {name: {'path': str(p), 'size': p.stat().st_size, 'sha256': sha(p),
                         'hydra_config': str(p.parents[2]/'.hydra/config.yaml'),
                         'hydra_config_sha256': sha(p.parents[2]/'.hydra/config.yaml')}
                  for name, p in [('native_release_local', native), ('frozen_driveclarify_required', frozen)]}
    save('audit/CHECKPOINT_IDENTITIES.json', checkpoint)
    print('checkpoints hashed', flush=True)

    prior = json.loads((PRIOR/'audit/PRESERVATION_BEFORE.json').read_text())
    current = []; mismatches = []
    for r in prior['files']:
        p = Path(r['path']); h = sha(p) if p.is_file() else None
        current.append({'path': str(p), 'sha256': h, 'size': p.stat().st_size if p.is_file() else None})
        if h != r['sha256']:
            mismatches.append({'path': str(p), 'expected': r['sha256'], 'actual': h})
    # 最新阶段和 LOW 审计全目录亦纳入本次前后保护。
    extra = tree_inventory([PRIOR, ROOT/'reports/driveclarify_rq3_paired_v3_formal_comparison_end_to_end_v1',
                            ROOT/'reports/driveclarify_rq2_actionable_window_robustness_extension_v1'])
    combined = {r['path']: r for r in current + extra['files']}
    save('audit/HISTORICAL_PRESERVATION_BEFORE.json', {'utc': now, 'files': list(combined.values()),
         'previous_manifest_checked': len(current), 'previous_manifest_mismatches': mismatches,
         'previous_manifest_path': str(PRIOR/'audit/PRESERVATION_BEFORE.json')})
    print('historical files checked', len(combined), 'mismatches', len(mismatches), flush=True)

    manifest = SIM/'leaderboard/data/bench2drive220.xml'
    routes = ET.parse(manifest).getroot().findall('route')
    ability_source = SIM/'Bench2Drive/tools/ability_benchmark.py'
    ability_ast = ast.parse(ability_source.read_text())
    ability = ast.literal_eval(next(n.value for n in ability_ast.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'Ability' for t in n.targets)))
    split = list((manifest.parent/'bench2drive_split').glob('*.xml'))
    split_by_id = collections.defaultdict(list)
    for p in split:
        for r in ET.parse(p).getroot().findall('route'):
            split_by_id[r.get('id')].append((p, r))
    rows = []
    for i, r in enumerate(routes):
        matches = split_by_id[r.get('id')]
        scenarios = [s.attrib for s in r.findall('scenarios/scenario')]
        rows.append({'canonical_index': i, 'route_id': r.get('id'), 'town': r.get('town'),
          'scenarios': scenarios, 'scenario_count': len(scenarios),
          'ability_membership': [a for a, ss in ability.items() if any(s['type'] in ss for s in scenarios)],
          'route_payload_digest': digest(route_payload(r)),
          'split_files': [{'path': str(p), 'sha256': sha(p), 'identical_route_payload': route_payload(r) == route_payload(sr),
                           'identical_route_id': r.get('id') == sr.get('id')} for p, sr in matches],
          'first_arm': 'A0' if i % 2 == 0 else 'A1'})
    ids = [r['route_id'] for r in rows]
    m = {'status': 'AUDITED_COMPLETE_OFFICIAL_ROSTER', 'formal_execution_frozen': False,
       'path': str(manifest), 'sha256': sha(manifest), 'matches_simlingo_HEAD_blob': git('show', 'HEAD:leaderboard/data/bench2drive220.xml') == manifest.read_bytes(),
       'route_count': len(rows), 'unique_route_id_count': len(set(ids)), 'duplicate_ids': [k for k, v in collections.Counter(ids).items() if v > 1],
       'split_count': len(split), 'split_missing_ids': sorted(set(ids)-set(split_by_id)), 'split_extra_ids': sorted(set(split_by_id)-set(ids)),
       'all_split_payloads_identical': all(len(r['split_files']) == 1 and r['split_files'][0]['identical_route_payload'] for r in rows),
       'scenario_type_counts': dict(collections.Counter(s['type'] for r in rows for s in r['scenarios'])),
       'ability_mapping': ability, 'ability_mapping_source': str(ability_source), 'ability_mapping_sha256': sha(ability_source),
       'ability_mapping_note': 'Traffic_Signs 官方算法另含路口 waypoint completion 计算；此处仅记录类别映射，不能直接平均替代官方脚本。',
       'routes': rows}
    m['digest'] = digest(m); save('FULL_B2D_ROUTE_MANIFEST.json', m)
    save('FULL_B2D_PAIR_ORDER_RECEIPT.json', {'formal_execution_frozen': False, 'status': 'PROSPECTIVE_ORDER_RECORDED_BEFORE_ANY_EXECUTION',
       'manifest_digest': m['digest'], 'method': 'canonical XML order, even zero-based index A0 first, odd A1 first',
       'A0_first': 110, 'A1_first': 110, 'pairs': [{'route_id': r['route_id'], 'canonical_index': r['canonical_index'],
       'arm_order': ['A0','A1'] if r['first_arm']=='A0' else ['A1','A0']} for r in rows]})
    assert len(rows) == len(set(ids)) == 220 and m['all_split_payloads_identical'] and m['matches_simlingo_HEAD_blob']
    save('audit/SOURCE_IDENTITIES.json', {'utc': now, 'simlingo_HEAD': git('rev-parse','HEAD').decode().strip(),
        'driveclarify_HEAD': subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD']).decode().strip(),
        'groups': identities, 'checkpoint': checkpoint,
        'bench2drive_tree_git_object_at_simlingo_HEAD': git('rev-parse','HEAD:Bench2Drive').decode().strip(),
        'carla_version': Path('/home/buaa/CARLA_0.9.15/VERSION').read_text().strip()})

    # 提取并原样执行冻结配置加载函数；替身只隔离外部模型/仿真依赖。
    v11 = ROOT/'driveclarify_clear_passthrough_v11/simlingo_agent.py'
    rq1 = ROOT/'driveclarify_rq1_v2/simlingo_agent.py'
    namespace = {'os': os, 'Path': Path, 'json': json,
       '_sha_file': lambda p: checkpoint['frozen_driveclarify_required']['sha256'] if Path(p).resolve()==frozen.resolve() else sha(p),
       'CONFIG_SCHEMA': 'driveclarify.v11.native-runtime-config.v1',
       'VALID_MODES': frozenset({'DRIVECLARIFY','NATIVE_SIMLINGO','NO_CLARIFICATION'}),
       'EXPECTED_A1_SHA256': checkpoint['frozen_driveclarify_required']['sha256']}
    from driveclarify_clear_passthrough_v11.contracts import assert_runtime_label_firewall
    namespace['assert_runtime_label_firewall'] = assert_runtime_label_firewall
    rq1_ast = ast.parse(rq1.read_text())
    helper = next(n for n in rq1_ast.body if isinstance(n, ast.FunctionDef) and n.name == '_forbidden_intent_paths')
    classes = [ast.ClassDef(name='V11Probe', bases=[], keywords=[], body=[node(v11,'DriveClarifyV11SimLingoAgent','_load_v11_config')], decorator_list=[]),
       ast.ClassDef(name='RQ1Probe', bases=[ast.Name(id='V11Probe',ctx=ast.Load())],keywords=[], body=[node(rq1,'DriveClarifyRQ1V2SimLingoAgent','_load_v11_config')],decorator_list=[])]
    mod=ast.fix_missing_locations(ast.Module(body=[helper]+classes,type_ignores=[]));exec(compile(mod,'<unchanged-source-config-probe>','exec'),namespace)
    base=json.loads((ROOT/'reports/driveclarify_rq3_bench2drive_closed_loop_system_validation_v1/part_a_configs/RQ3-A-R01-S01-A1.json').read_text())
    probes=[]
    for label, mi in [('missing_instruction',{}),('null_instruction',{'instruction':None}),('empty_instruction_no_task',{'instruction':''}),
                     ('historical_clear_config',base['method_input']),('historical_clear_config_empty_instruction',{**base['method_input'],'instruction':''})]:
        conf={**base,'run_id':'NON_FORMAL_CONFIG_PROBE_'+label,'method_input':mi}
        p=OUT/'audit/config_probes'/(label+'.json');p.parent.mkdir(exist_ok=True);p.write_text(json.dumps(conf,indent=2)+'\n')
        os.environ['DRIVECLARIFY_V11_CONFIG']=str(p);os.environ['DRIVECLARIFY_V11_CONFIG_SHA256']=sha(p)
        error=None
        try: namespace['RQ1Probe']()._load_v11_config()
        except Exception as e: error=type(e).__name__+': '+str(e)
        probes.append({'case':label,'result':'ACCEPTED' if error is None else 'REJECTED','error':error})
    # 原样执行 prompt 条件分支，保留 native 和冻结 A1 的实际字段值。
    native_file=SIM/'team_code/agent_simlingo.py'
    tick=node(native_file,'LingoAgent','tick')
    prompt_nodes=[n for n in tick.body if isinstance(n,ast.If) and (ast.unparse(n.test)=='self.config.use_cot' or
      'self.custom_prompt is not None'==ast.unparse(n.test) or ast.unparse(n.test)=='self.user_flag == 1 or self.user_flag == 2')]
    assert len(prompt_nodes)==3
    prompt_code=compile(ast.fix_missing_locations(ast.Module(body=prompt_nodes,type_ignores=[])),'<unchanged-source-prompt-probe>','exec')
    prompts=[]
    for label,custom,flag in [('native_standard',None,None),('A1_empty_instruction','',1),('A1_historical_clear','Follow the assigned route.',1)]:
        for cot in [False,True]:
            env={'self':types.SimpleNamespace(config=types.SimpleNamespace(use_cot=cot),custom_prompt=custom,user_flag=flag),
                 'speed':3.0,'prompt_tp':'Target waypoint: <TARGET_POINT><TARGET_POINT>.'}
            exec(prompt_code,env)
            prompts.append({'case':label,'use_cot':cot,'prompt':env['prompt']})
    result={'scope':'NON_FORMAL_CPU_SOURCE_FUNCTION_PROBE_ONLY','model_loads':0,'CARLA_launches':0,'route_executions':0,
      'source_sha256':{str(v11):sha(v11),str(rq1):sha(rq1),str(native_file):sha(native_file)},
      'limitation':'仅运行原样提取的配置/提示构造分支，未模拟或证明完整闭环；已有历史 CLEAR 输入只用于反例诊断，未作为新任务安装。',
      'config_probes':probes,'prompt_probes':prompts,
      'no_context_config_supported':False,'empty_instruction_recovers_native_prompt':False,
      'no_clarification_instruction_is_required_for_standard_protocol':True}
    assert probes[0]['error'].endswith('V11_INSTRUCTION_REQUIRED')
    assert probes[2]['error'].endswith('RQ1_V2_EXACTLY_TWO_TASK_SIGNATURES_REQUIRED')
    assert all(p['prompt'].startswith('<INSTRUCTION_FOLLOWING>') for p in prompts if p['case'].startswith('A1'))
    save('audit/NO_CONTEXT_SOURCE_PROBE.json',result)
    print('route roster and CPU source probes complete',flush=True)

if __name__=='__main__':main()
