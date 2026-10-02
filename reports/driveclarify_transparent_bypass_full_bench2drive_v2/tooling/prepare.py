"""冻结激活谓词、资格路线和保护清单；尚不签发正式基准freeze。"""
import collections
import subprocess
from common import *
from driveclarify_transparent_bypass_v2.activation import INTERFACE_VERSION,PREDICATE_VERSION

def main():
    existing_predicate=load(OUT/'TRANSPARENT_BYPASS_ACTIVATION_CONTRACT.json')
    if existing_predicate:
        assert existing_predicate['predicate_source_sha256']==sha(ROOT/'driveclarify_transparent_bypass_v2/activation.py'), 'FROZEN_PREDICATE_CHANGED'
    files={}
    for name in ['simlingo_scientific','driveclarify_scientific','bench2drive_evaluator','bench2drive_scenario_runner','official_metric_scripts','carla_identity']:
        for r in load(OLD/'audit'/f'{name}_inventory.json')['files']:files[r['path']]=r
    for r in load(OLD/'audit/HISTORICAL_PRESERVATION_BEFORE.json')['files']:files[r['path']]=r
    for p in OLD.rglob('*'):
        if p.is_file() and '__pycache__' not in p.parts:files[str(p)]={'path':str(p),'sha256':sha(p),'size':p.stat().st_size}
    cp=load(OLD/'audit/CHECKPOINT_IDENTITIES.json')
    for r in cp.values():
        files[r['path']]={'path':r['path'],'sha256':r['sha256']}
        files[r['hydra_config']]={'path':r['hydra_config'],'sha256':r['hydra_config_sha256']}
    m=load(OLD/'FULL_B2D_ROUTE_MANIFEST.json')
    for r in m['routes']:
        for sf in r['split_files']:files[sf['path']]=sf
    files[m['path']]={'path':m['path'],'sha256':m['sha256']}
    mismatches=[p for p,r in files.items() if not Path(p).is_file() or sha(p)!=r['sha256']]
    assert not mismatches, mismatches
    save(OUT/'audit/PROTECTED_BEFORE.json',{'utc':now(),'files':list(files.values()),'mismatches':mismatches,'previous_V1_is_immutable':True})
    m.update(stage=INTERFACE_VERSION,formal_execution_frozen=False,prior_V1_freeze_reused=False)
    m.pop('digest',None);m['digest']=digest(m);save(OUT/'FULL_B2D_ROUTE_MANIFEST.json',m)
    predicate={'interface_version':INTERFACE_VERSION,'predicate_version':PREDICATE_VERSION,'frozen_utc':now(),
      'predicate_source':str(ROOT/'driveclarify_transparent_bypass_v2/activation.py'),
      'predicate_source_sha256':sha(ROOT/'driveclarify_transparent_bypass_v2/activation.py'),
      'required':['context mapping','nonempty passenger instruction','exactly two complete candidate representations with distinct IDs',
       'exactly two valid certified TaskSignatures with matching ordered candidate IDs','readable existing route source with each candidate field containing >=2 points',
       'existing active background policy operands'],
      'optional_temporal_contract':'if provided, must equal frozen R-JOINT(B2), 3.0s T_FIXED, 1.20s reserve, CARLA simulation clock, certified anchor',
      'absence_or_invalidity':'NO_CONTEXT_TRANSPARENT_BYPASS before old active class import/setup',
      'valid_context':'unchanged frozen RQ3 class via evaluator-only +save_name config argument adapter',
      'performance_dependence':False,'input_rewriting_on_bypass':False,'historical_science_rewritten':False}
    predicate['digest']=digest(predicate)
    if existing_predicate:predicate=existing_predicate
    else:save(OUT/'TRANSPARENT_BYPASS_ACTIVATION_CONTRACT.json',predicate)
    (OUT/'TRANSPARENT_BYPASS_SCIENTIFIC_CHANGE_SPEC.md').write_text('''# 前瞻无上下文透明旁路 V2

仅授权新行为：缺少完整澄清上下文时，在旧V11/RQ1/RQ3入口之前直接选择原生SimLingo。A0和A1旁路使用同一个ObservedNativeAgent class；其tick/run_step和class PID继承原生代码。不会用空instruction走旧提示路径。

激活谓词逐项与源码SHA冻结于TRANSPARENT_BYPASS_ACTIVATION_CONTRACT.json。完整有效上下文继续调用旧RQ3→RQ1→V11，保留1.20秒、ACT/ASK/WAIT和Full Replan语义。任务输入不复制、不规范化、不覆写。

资格验证只检查输入/控制身份。实时轨迹可因native stochasticity不同；每次真实model pre-hook均检查传入字段与原生DrivingInput对象相同，抽样记录tensor指纹；同一已生成原生输入在两臂的入口类完全相同。提示按原生源码在同一live状态重算字符，检查最终tokenization和隐藏task flags。观察器不调用world.tick/模型/控制写入；两臂相同，simulation clock在观察前后不变。

既有V1阻断目录及所有历史结果只读，不重跑历史正式实验。不强制确定性，不按资格路线得分选主机或改配置。
''')
    # 固定八条、八个场景类别，包含大地图及常规地图，先记录再运行。
    indexes=[0,7,24,66,79,148,146,202]
    rows=[m['routes'][i] for i in indexes]
    assert len({r['scenarios'][0]['type'] for r in rows})==8
    qualification={'status':'PROSPECTIVE_NON_FORMAL','created_utc':now(),'route_count':8,'native_executions_expected':16,
      'route_selection':'predeclared diverse scenario categories and large/conventional towns; no performance read',
      'seed':902609061,'counts_toward_formal':False,'routes':rows,
      'arm_order':[['A0','A1'] if i%2==0 else ['A1','A0'] for i in range(8)],
      'predicate_digest':predicate['digest'],'scores_not_qualification_criterion':True}
    qualification['digest']=digest(qualification);save(OUT/'TRANSPARENCY_QUALIFICATION_MANIFEST.json',qualification)
    save(OUT/'interface_config.json',{'interface_version':INTERFACE_VERSION,'context':None})
    old_version=load(OLD/'BENCHMARK_VERSION_AUDIT.json');old_version.update({'stage':INTERFACE_VERSION,
      'fresh_source_manifest_digest':digest(list(files.values())),'fresh_integrity_pass':True,
      'formal_checkpoint':cp['frozen_driveclarify_required'],'A0_A1_same_checkpoint':True,
      'previous_interface_blocker_resolved_by_new_authorized_version':True})
    save(OUT/'BENCHMARK_VERSION_AUDIT.json',old_version)
    log('prepare.py: immutable historical/source check; freeze activation predicate and 8-route NON_FORMAL roster')
    print([(r['route_id'],r['town'],r['scenarios'][0]['type']) for r in rows],flush=True)

if __name__=='__main__':main()
