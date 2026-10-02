"""全覆盖后官方合并、原样metric脚本、配对统计和最终证据。"""
import csv
import contextlib
import io
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import psutil
import numpy as np
from common import *
from run_route import authoritative,environment,port_free
from paired_statistics import continuous,binary


def table(headers,rows):
    def fmt(x):
        if x is None:return 'N/A'
        if isinstance(x,float):return f'{x:.6f}'
        return str(x).replace('\n',' ')
    return '\n'.join(['| '+' | '.join(headers)+' |','| '+' | '.join(['---']*len(headers))+' |']+['| '+' | '.join(fmt(x) for x in row)+' |' for row in rows])+'\n'

def invoke(command,logfile,env=None,timeout=3600):
    log(' '.join(command))
    tracked={}
    with Path(logfile).open('w') as f:
        proc=subprocess.Popen(command,cwd=str(SIM),env=env,stdout=f,stderr=subprocess.STDOUT,start_new_session=True)
        started=time.time()
        try:
            while proc.poll() is None:
                try:
                    for p in psutil.Process(proc.pid).children(recursive=True):
                        if 'CarlaUE4-Linux-Shipping' in ' '.join(p.cmdline()):tracked[p.pid]=p.create_time()
                except psutil.NoSuchProcess:pass
                if time.time()-started>timeout:raise TimeoutError('OFFICIAL_METRIC_INFRASTRUCTURE_TIMEOUT')
                time.sleep(2)
            returncode=proc.wait()
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid,signal.SIGTERM)
                try:proc.wait(timeout=20)
                except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait()
            for pid,created in tracked.items():
                try:
                    p=psutil.Process(pid)
                    if p.create_time()==created:p.kill()
                except psutil.NoSuchProcess:pass
    if returncode:raise RuntimeError('OFFICIAL_TOOL_FAILED:'+str(logfile))

def route_success(record):
    # 与官方merge的二元定义逐项一致；不计算DS。
    return record['status'] in ('Completed','Perfect') and all(not v for k,v in record['infractions'].items() if k!='min_speed_infractions')


def main():
    freeze=load(OUT/'FULL_B2D_FREEZE_RECEIPT.json');manifest=load(OUT/'FULL_B2D_ROUTE_MANIFEST.json')
    assert freeze['formal_freeze_issued']
    records={};rows={};activity=[];metric_results={};metric_populations={};official_commands=[]
    expected_ids=[r['route_id'] for r in manifest['routes']]
    for arm in ['A0','A1']:
        ledger=load(OUT/(arm+'_EXECUTION_LEDGER.json'))
        assert ledger['authoritative_completed']==220 and len(ledger['entries'])==220
        folder=OUT/'official_merge'/arm;folder.mkdir(parents=True,exist_ok=True)
        metric_dir=OUT/'metric_input_binding'/arm;metric_dir.mkdir(parents=True,exist_ok=True)
        records[arm]=[];rows[arm]=[]
        for expected,entry in zip(expected_ids,ledger['entries']):
            assert expected==entry['route_id'] and entry['status']=='AUTHORITATIVE'
            attempt=Path(entry['authoritative_output']);raw=attempt/'official_checkpoint.json'
            assert sha(raw)==entry['raw_sha256']
            authoritative_attempts=[p for p in attempt.parent.glob('attempt_*') if (p/'official_checkpoint.json').exists() and authoritative(p/'official_checkpoint.json') is not None]
            assert authoritative_attempts==[attempt], 'DUPLICATE_AUTHORITATIVE_RESULT_REQUIRES_PROVENANCE_ADJUDICATION'
            record=authoritative(raw);assert record is not None
            assert record['route_id'].split('_')[1]==expected
            records[arm].append(record)
            destination=folder/(expected+'.json')
            if destination.exists():assert sha(destination)==sha(raw)
            else:shutil.copyfile(raw,destination)
            telemetry=load(attempt/'agent_terminal.json');assert telemetry['forward_count_contract'] and not telemetry['violations']
            source=Path(telemetry['metric_info_path'])
            target=metric_dir/record['save_name']/'metric_info.json';target.parent.mkdir(exist_ok=True)
            if target.exists():assert target.resolve()==source.resolve()
            else:target.symlink_to(source)
            row={'route_id':expected,'arm':arm,'status':record['status'],**record['scores'],
              'success':route_success(record),'collision_episode':any(record['infractions'].get(k,[]) for k in ['collisions_layout','collisions_vehicle','collisions_pedestrian']),
              'raw_json_path':str(raw),'raw_json_sha256':sha(raw)}
            rows[arm].append(row)
            if arm=='A1':activity.append({'route_id':expected,'output':str(attempt),'terminal_sha256':sha(attempt/'agent_terminal.json'),'metrics':telemetry})
        assert len({r['route_id'] for r in records[arm]})==220
        # merge目录除merged.json外仅含220个被锁定的首个权威原始JSON。
        assert len([p for p in folder.glob('*.json') if p.name!='merged.json'])==220
        cmd=[PYTHON,str(SIM/'Bench2Drive/tools/merge_route_json.py'),'-f',str(folder)]
        invoke(cmd,OUT/'audit'/f'{arm}_official_merge.log')
        merged=load(folder/'merged.json');assert merged['eval num']==220 and len(merged['_checkpoint']['records'])==220
        # 独立验证成功计数，DS主输出始终读取官方merge值。
        assert abs(merged['success rate']-sum(r['success'] for r in rows[arm])/220)<1e-12
        shutil.copyfile(folder/'merged.json',OUT/(arm+'_OFFICIAL_MERGED_RESULTS.json'))
        scoring_env=environment(arm,manifest['path'],OUT/'scoring_process',False)
        cmd=[PYTHON,str(SIM/'Bench2Drive/tools/efficiency_smoothness_benchmark.py'),'-f',str(folder/'merged.json'),'-m',str(metric_dir)]
        try:
            invoke(cmd,OUT/'audit'/f'{arm}_efficiency_smoothness.log',env=scoring_env)
            text=(OUT/'audit'/f'{arm}_efficiency_smoothness.log').read_text()
            efficiency=float(re.search(r'Driving Efficiency=([^\s]+)',text).group(1))
            smoothness=float(re.search(r'Driving Smoothness=([^\s]+)',text).group(1))
            assert math.isfinite(efficiency) and math.isfinite(smoothness)
            smooth_error=None
        except (RuntimeError,AttributeError,AssertionError) as exc:
            # 公式不支持个别缺测/过短轨迹时，不删除该路线或手造分数。
            efficiency=smoothness=None;smooth_error=str(exc)
        # 只记录原脚本分母，不重算分数或过滤传入官方脚本的路线。
        efficiency_candidates=sum(bool(r['infractions'].get('min_speed_infractions',[])) for r in records[arm])
        metric_populations[arm]={'merged_routes':220,'smoothness_input_routes':220,
          'efficiency_routes_with_min_speed_records':efficiency_candidates,
          'efficiency_mean_denominator':efficiency_candidates if smooth_error is None else None,
          'definition':'官方read_from_json跳过min_speed_infractions为空的路线，并跳过单个百分比>1000的记录；无可平均记录会导致原脚本失败，不补零或删路线。'}
        port=next(p for p in range(30000,49000,10) if port_free(p) and port_free(p+1))
        ability_json=OUT/(arm+'_OFFICIAL_MERGED_RESULTS_ability.json')
        cmd=[PYTHON,str(OUT/'tooling/official_ability_entry.py'),manifest['path'],str(OUT/(arm+'_OFFICIAL_MERGED_RESULTS.json')),str(port)]
        invoke(cmd,OUT/'audit'/f'{arm}_official_ability.log',env=scoring_env,timeout=3600)
        ability=load(ability_json);assert not ability['crashed']
        metric_results[arm]={'Driving Score':merged['driving score'],'Success Rate (%)':merged['success rate']*100,
          'Driving Efficiency':efficiency,'Driving Smoothness (%)':smoothness*100 if smoothness is not None else None,
          'Overtaking (%)':ability['Overtaking']*100,'Merging (%)':ability['Merging']*100,
          'Emergency Brake (%)':ability['Emergency_Brake']*100,'Give Way (%)':ability['Give_Way']*100,
          'Traffic Signs (%)':ability['Traffic_Signs']*100,'Multi-Ability Mean (%)':ability['mean']*100,
          'Route Completion mean (%)':float(np.mean([r['score_route'] for r in rows[arm]])),
          'Infraction Penalty mean':float(np.mean([r['score_penalty'] for r in rows[arm]]))}
        save(OUT/'audit'/f'{arm}_OFFICIAL_SCORING_RECEIPT.json',{'official_merge_routes':220,'official_merge_sha256':sha(OUT/(arm+'_OFFICIAL_MERGED_RESULTS.json')),
          'efficiency_smoothness_error':smooth_error,'metric_data_binding':'symlinks to immutable original native metric_info.json; no resampling or formula changes',
          'metric_populations':metric_populations[arm],
          'cadence_note':'native evaluator dt=0.05s; installed smoothness script time_interval default=0.1s. Report is exact bundled-script output; do not silently reinterpret as 20Hz derivative.',
          'ability_formula_unmodified':True,'ability_port_adapter':'int argument passed to original main to avoid original CLI nargs=1 list mismatch'})
        fields=list(rows[arm][0])
        with (OUT/('ALL_'+arm+'_ROUTE_RESULTS.csv')).open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows[arm])
    comparison={k:{'A0':metric_results['A0'][k],'A1':metric_results['A1'][k],
      'delta_A1_minus_A0':None if metric_results['A0'][k] is None or metric_results['A1'][k] is None else metric_results['A1'][k]-metric_results['A0'][k]} for k in metric_results['A0']}
    save(OUT/'FULL_B2D_OFFICIAL_COMPARISON.json',{'version':'Bench2Drive v0.0.3 SimLingo bundle','A0_routes':220,'A1_routes':220,
      'controlling_DS_SR_source':'unchanged official merge_route_json.py','metrics':comparison,'metric_populations':metric_populations,'external_reference_direct_comparison':False})
    paired={'n':220,'continuous':{},'success':binary([r['success'] for r in rows['A0']],[r['success'] for r in rows['A1']]),
      'collision_episode':binary([r['collision_episode'] for r in rows['A0']],[r['collision_episode'] for r in rows['A1']]),'noninferiority_margin':None}
    for key in ['score_composed','score_route','score_penalty']:
        paired['continuous'][key]=continuous([r[key] for r in rows['A0']],[r[key] for r in rows['A1']],freeze['bootstrap']['seed'],100000)
    save(OUT/'FULL_B2D_PAIRED_ROUTE_ANALYSIS.json',paired)
    with (OUT/'ALL_PAIRED_ROUTE_RESULTS.csv').open('w',newline='') as f:
        data=[]
        for a,b in zip(rows['A0'],rows['A1']):
            row={'route_id':a['route_id']}
            for k in ['score_composed','score_route','score_penalty','success','collision_episode']:
                row['A0_'+k]=a[k];row['A1_'+k]=b[k]
                if not isinstance(a[k],bool):row['delta_'+k]=b[k]-a[k]
            data.append(row)
        w=csv.DictWriter(f,fieldnames=list(data[0]));w.writeheader();w.writerows(data)
    categories=sorted(set().union(*(r['infractions'].keys() for arm in ['A0','A1'] for r in records[arm])))
    infractions={k:{arm:{'episode_count':sum(bool(r['infractions'].get(k,[])) for r in records[arm]),
       'event_count':sum(len(r['infractions'].get(k,[])) for r in records[arm]),'denominator':220} for arm in ['A0','A1']} for k in categories}
    infractions['ANY_COLLISION']={arm:{'episode_count':sum(r['collision_episode'] for r in rows[arm]),
      'event_count':sum(len(r['infractions'].get(k,[])) for r in records[arm] for k in ['collisions_layout','collisions_vehicle','collisions_pedestrian']),
      'denominator':220} for arm in ['A0','A1']}
    save(OUT/'FULL_B2D_INFRACTION_ANALYSIS.json',{'categories':infractions,
      'outside_route_lanes_note':'事件列表数与越线距离/比例是不同量，未将其相互代替。','route_completion':comparison['Route Completion mean (%)'],'infraction_penalty':comparison['Infraction Penalty mean']})
    keys=['ASK','WAIT','answer_events','candidate_comparisons','evidence_margin_decisions','clarification_triggered_full_replan',
      'nonclarification_DriveClarify_route_installation','nonclarification_full_replan','initial_native_route_installation',
      'direct_control_intervention','extra_model_forwards','second_control_writer']
    totals={k:sum(r['metrics'][k] for r in activity) for k in keys}
    totals.update(routes_active=sum(r['metrics']['DRIVECLARIFY_ACTIVE'] for r in activity),
      routes_bypass=sum(not r['metrics']['DRIVECLARIFY_ACTIVE'] for r in activity),
      routes_with_ASK=sum(r['metrics']['ASK']>0 for r in activity),duplicate_authoritative_execution=0,
      native_forward_count=sum(r['metrics']['model_pre_count'] for r in activity))
    save(OUT/'FULL_B2D_DRIVECLARIFY_ACTIVITY.json',{'A1_routes':220,'totals':totals,
      'nonclarification_route_or_task_installation_including_initial':totals['initial_native_route_installation']+totals['nonclarification_DriveClarify_route_installation'],
      'definition':'INITIAL_NATIVE_ROUTE is shared initialization, separately shown. Nonclarification Full Replan is subset of method route installation, not additive.',
      'routes':[{k:v for k,v in r.items() if k!='metrics'} for r in activity]})
    (OUT/'TABLE_FULL_BENCH2DRIVE_MAIN.md').write_text('# 全量 Bench2Drive v0.0.3（SimLingo分发）\n\n'+table(['指标 ↑','Native SimLingo A0','SimLingo + DriveClarify V2 A1','Delta A1−A0'],[[k,v['A0'],v['A1'],v['delta_A1_minus_A0']] for k,v in comparison.items() if k not in ['Route Completion mean (%)','Infraction Penalty mean']]))
    (OUT/'TABLE_FULL_BENCH2DRIVE_INFRACTIONS.md').write_text('# 违规与诊断\n\n'+table(['指标','A0','A1','Delta'],[[k,comparison[k]['A0'],comparison[k]['A1'],comparison[k]['delta_A1_minus_A0']] for k in ['Route Completion mean (%)','Infraction Penalty mean']])+'\n'+table(['类别','A0 episode/220','A1 episode/220','A0 events','A1 events'],[[k,v['A0']['episode_count'],v['A1']['episode_count'],v['A0']['event_count'],v['A1']['event_count']] for k,v in infractions.items()]))
    (OUT/'TABLE_FULL_BENCH2DRIVE_ACTIVITY.md').write_text('# A1活动（逐路线原始收据汇总）\n\n'+table(['指标','计数'],list(totals.items()))+'\n初始原生路线安装单列，不与DriveClarify方法侧Full Replan混同。\n')
    (OUT/'TABLE_FULL_BENCH2DRIVE_PAIRED_ANALYSIS.md').write_text('# 配对路线次级分析\n\n'+table(['指标','A0 mean/median','A1 mean/median','paired mean/median delta','95% CI'],[[k,f"{v['A0_mean']:.6f}/{v['A0_median']:.6f}",f"{v['A1_mean']:.6f}/{v['A1_median']:.6f}",f"{v['paired_mean_difference']:.6f}/{v['paired_median_difference']:.6f}",v['paired_bootstrap_95_CI']] for k,v in paired['continuous'].items()])+'\n'+table(['二元指标','A0 count','A1 count','discordance','exact McNemar p'],[[k,paired[k]['A0_count'],paired[k]['A1_count'],paired[k]['discordance_table'],paired[k]['exact_McNemar_p']] for k in ['success','collision_episode']]))
    # 最终历史、checkpoint、controller、evaluator完整性。
    protected=load(OUT/'audit/PROTECTED_BEFORE.json')['files'];mismatches=[r['path'] for r in protected if sha(r['path'])!=r['sha256']]
    scientific_mismatches=[p for p,h in freeze['scientific_files'].items() if sha(p)!=h]
    assert not mismatches and not scientific_mismatches
    save(OUT/'SOURCE_INTEGRITY_RECEIPT.json',{'pass':True,'protected_files_checked':len(protected),'mismatches':mismatches,
      'scientific_frozen_files_checked':len(freeze['scientific_files']),'scientific_mismatches':scientific_mismatches,
      'checkpoint_hash':sha(CHECKPOINT),'historical_RQ1_RQ2_RQ3_changed':False,'historical_blocked_V1_changed':False,'utc':now()})
    save(OUT/'CONTROL_INTEGRITY_RECEIPT.json',{'pass':True,'authoritative_routes_checked':440,'same_native_controller_delegate':True,
      'all_model_PID_counts_matched':True,'source_frozen':True,'second_control_writer_count':totals['second_control_writer'],
      'additional_VLA_forwards':totals['extra_model_forwards'],'runtime_receipts':'formal/<route_id>/<arm>/attempt_XX/agent_terminal.json'})
    save(OUT/'audit/ANALYSIS_VALIDATION.json',{'assessment':'Share with caveats','population_routes_per_arm':220,'pairs':220,
      'route_key_coverage_pass':True,'first_authoritative_only':True,'official_SR_independently_reconciled':True,
      'official_DS_not_reimplemented':True,'bootstrap_resamples':100000,'exact_McNemar':True,
      'caveats':['native stochastic trajectories not bitwise paired','no noninferiority margin','no ambiguity efficacy claim',
        'exact installed SimLingo v0.0.3 distribution, not an official leaderboard submission',
        'native metrics logged at20Hz while distributed smoothness script assumes0.1s; unchanged-script output disclosed'],
      'unavailable_metrics':{arm:[k for k,v in metric_results[arm].items() if v is None] for arm in ['A0','A1']}})
    notebook=load(OUT/'audit/DATA_QUALITY_REVIEW.ipynb');scope={}
    execution=0
    for cell in notebook['cells']:
        if cell['cell_type']!='code':continue
        execution+=1;stream=io.StringIO()
        with contextlib.redirect_stdout(stream):exec(compile(''.join(cell['source']),'DATA_QUALITY_REVIEW.ipynb','exec'),scope)
        cell['execution_count']=execution
        cell['outputs']=[{'output_type':'stream','name':'stdout','text':stream.getvalue().splitlines(True)}]
    save(OUT/'audit/DATA_QUALITY_REVIEW.ipynb',notebook)
    report=f'''# V2透明旁路与完整Bench2Drive结果

**PASS_TRANSPARENT_BYPASS_AND_FULL_BENCH2DRIVE_COMPLETE**

Bench2Drive **v0.0.3（已安装SimLingo分发）**，CARLA **0.9.15**，完整官方 **220条路线**。A0 **220/220**，A1 **220/220**，**440**个首个权威闭环结果，官方merge两臂均确认220条。

新接口摘要 `{freeze['interface_digest']}`；新正式freeze `{freeze['freeze_digest']}`。激活条件在资格验证前冻结；无完整上下文直接选择与A0相同的原生执行类，有有效上下文继续调用冻结RQ3/RQ1/V11。历史激活路径{load(OUT/"ACTIVE_PATH_REGRESSION_RECEIPT.json")["test_count"]}项回归通过，8/8路线、16次NON_FORMAL真实闭环透明性资格通过。

本实验仅回答无澄清上下文时的标准驾驶表现。两臂保留同一checkpoint、native视觉/导航输入、controller和官方背景交通，不强制确定性、不要求同轨迹。历史RQ1/RQ2/RQ3及V1阻断目录均未改动。

{(OUT/'TABLE_FULL_BENCH2DRIVE_MAIN.md').read_text()}

{(OUT/'TABLE_FULL_BENCH2DRIVE_PAIRED_ANALYSIS.md').read_text()}

{(OUT/'TABLE_FULL_BENCH2DRIVE_INFRACTIONS.md').read_text()}

{(OUT/'TABLE_FULL_BENCH2DRIVE_ACTIVITY.md').read_text()}

所有DS/SR来自原样官方merge，次级配对分析使用100,000次bootstrap（PCG64 seed {freeze['bootstrap']['seed']}）和exact McNemar。没有非劣效界值，不据此声称非劣效。A1活动来自实际派发及model/PID/route-generation收据，不以预期零代替观测。

官方Efficiency会跳过没有min_speed记录的路线，并排除单条>1000%的记录；其有效平均分母为A0 {metric_populations['A0']['efficiency_mean_denominator']}、A1 {metric_populations['A1']['efficiency_mean_denominator']}（None表示原脚本未成功产出该值），不能直接视为220。完整220条原始结果均传入官方工具；具体输入数量与错误见两臂OFFICIAL_SCORING_RECEIPT。

技术恢复：见TECHNICAL_RETRY_LEDGER.json；首次有效低分、碰撞、阻塞或策略超时全部保留，未选最佳结果。具体工程改动见ENGINEERING_REPAIR_LOG.md。最终source/checkpoint/controller完整性全部通过。

协议边界：使用SimLingo已安装的v0.0.3分发及其既有本地适配，不是当前排行榜提交。native metric_info按20Hz记录，而分发的smoothness脚本默认time_interval=0.1s；此处报告原样脚本输出并保留该局限，不静默重采样或修改公式。外部论文checkpoint与三训练seed汇总不与本地单checkpoint直接做delta。无歧义效果/新RQ结论，无手稿改动。

所有精确路线原始JSON、进程与控制收据在 `formal/<route_id>/<arm>/attempt_XX/`。完整路径及SHA256见FINAL_ARTIFACT_AUDIT.json。
'''
    (OUT/'FINAL_REPORT.md').write_text(report)
    excluded={'FINAL_ARTIFACT_AUDIT.json','FINAL_VALIDATION_RECEIPT.json','formal_supervisor.log','qualification_supervisor.log','continuation.log',
      'UNATTENDED_SUPERVISOR.log','UNATTENDED_SUPERVISOR_STATE.json','UNATTENDED_EXECUTION_COMPLETE.json'}
    artifacts=[{'path':str(p),'size':p.stat().st_size,'sha256':sha(p)} for p in sorted(OUT.rglob('*')) if p.is_file() and not p.is_symlink() and p.name not in excluded and '__pycache__' not in p.parts]
    save(OUT/'FINAL_ARTIFACT_AUDIT.json',{'complete':True,'files':artifacts,'exclusions':sorted(excluded),'authoritative_routes':{'A0':220,'A1':220},'missing_routes':{'A0':[],'A1':[]}})
    save(OUT/'FINAL_VALIDATION_RECEIPT.json',{'status':'PASS_TRANSPARENT_BYPASS_AND_FULL_BENCH2DRIVE_COMPLETE','A0_authoritative':220,'A1_authoritative':220,
      'official_merge_counts':[220,220],'qualified_routes':8,'source_integrity':True,'controller_integrity':True,'historical_results_changed':False,
      'artifact_audit_sha256':sha(OUT/'FINAL_ARTIFACT_AUDIT.json'),'freeze_digest':freeze['freeze_digest'],'utc':now()})
    print('PASS_TRANSPARENT_BYPASS_AND_FULL_BENCH2DRIVE_COMPLETE',flush=True)

if __name__=='__main__':main()
