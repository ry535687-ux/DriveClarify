"""仅完成 STEP2 文稿交付、交接、空表、完整性检查和小型复核包。"""
from pathlib import Path
import ast
import csv
import datetime
import hashlib
import io
import json
import re
import subprocess
import zipfile

ROOT=Path(__file__).resolve().parents[3]
OUT=Path(__file__).resolve().parents[1]
REL=OUT.relative_to(ROOT).as_posix()
STATUS='STEP2_PARTIAL_WITH_EXPLICIT_GAPS'
FOUR=['REVIEW_RESOLUTION_AND_METHOD_PATCH.md','EXPERIMENT_CHAPTER_V2_DRAFT.md','MAIN_EXPERIMENT_MINIMAL_PROTOCOL.md','FINAL_REPORT.md']

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def write_json(p,v): p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def git(*args): return subprocess.check_output(['git',*args],cwd=ROOT)

entry=json.loads((OUT/'evidence/ENTRY.json').read_text())
baseline=json.loads((OUT/'evidence/PROTECTED_SOURCE_ENTRY.json').read_text())
source_drift=[n for n,h in baseline.items() if not (ROOT/n).is_file() or sha(ROOT/n)!=h]
assert not source_drift, source_drift
word=Path('/home/buaa/下载/论文20260909改2.docx')
assert sha(word)=='d8bd6e9d4ef6ee11e086b637b2e3d061c0760699ed214dd0149afdd46abe299e'

tables={
 'MAIN_RESULTS_TEMPLATE.csv':'strategy_id,layout_count,template_count,paired_configuration_count,planned_runs,attempted_runs,exposed_runs,evaluable_runs,infrastructure_unknown,normal_task_failures,correct_task_completions,question_count,unnecessary_query_equivalent_n,unnecessary_query_equivalent_denominator,wrong_goal_n,safety_event_run_n,source_digest',
 'PAIRED_COMPARISONS_TEMPLATE.csv':'comparison,condition,layout_id,template_id,common_evaluable_configurations,both_complete,full_only_complete,baseline_only_complete,neither_complete,completion_difference,question_count_difference,wrong_goal_difference,safety_difference,missingness_note',
 'EPISODE_LOG_SCHEMA.csv':'protocol_digest,source_digest,checkpoint_digest,controller_digest,evaluator_digest,layout_id,template_id,paired_config_id,strategy_id,restricted_seed_reference,attempt_id,run_order,observation_id,public_input_digest,task_schema_digest,evidence_source,evidence_validity,pair_relations,set_relation,simulation_time_s,wall_time_s,deadline_source,deadline_s,query_checks_source,requested_action,durable_query_id,answer_received_at,task_determined_event,replan_requested_at,route_installed_at,plan_source_frame,first_adopted_control_frame,trace_path,terminal_status,independent_task_outcome,wrong_goal,safety_events,infrastructure_reason',
 'TEMPLATE_QUALIFICATION_TEMPLATE.csv':'template_id,layout_id,condition,instruction,candidate_a,candidate_b,required_task_fields,route_a_source,route_b_source,observable_task_predicate,evaluator_version,public_route_neutrality,both_routes_expressible,query_authority_source,evidence_interface_source,qualification_status,exclusion_reason,source_digest'
}
for name,header in tables.items():
    p=OUT/'tables'/name
    if not p.exists(): p.write_text(header+'\n')
    assert len(list(csv.reader(io.StringIO(p.read_text()))))==1

now=datetime.datetime.now().astimezone().isoformat()
marker='## STEP2 2026-09-12 — 文稿与最小协议交付'
note=f'''{marker}

本轮状态：`{STATUS}`。四份文稿/协议已写成；未解决的证据和科学边界明确保留。
入口：`{REL}/FINAL_REPORT.md`；独立复核包：`{REL}/REVIEW_PACKET.zip`。

本轮用户已指定 Q1/Q2 的文稿研究方向，旧“等待 Q1/Q2”不是当前指令；没有授权 live 迁移或新实验。
实际原 Word SHA-256 匹配，10 条批注已映射。实际式(5)逐对、式(6)集合、式(7)t_div；不能沿用旧稿号。
Q1 缺失优先是相关任务字段的证据判断；集合仍存在分歧优先。参照与任务目标分开，不删除 task_completion_region。
Q2 revised 按实际代码无任务依据拒答；原版 L205–208 一致性分支和 revised 注释冲突另列，未改历史。
Q3 live ABL_FULL 为签名判断、ABL_TRAJ_ONLY 为轨迹判断；保存输入 M5/revised 的负分差只属离线。
Q5 已定位下游部分询问/时机检查；无完整 QueryOK 证明，UNKNOWN 的 WAIT 枚举只保留原生权限，非物理停车许可。
31+18=49 为样本—条件改判；19/12仅标签已定义总体；revised/M4 731同判仅限关系输出。
B2D旧账本101标记不是全部完成数裁定；补核3904/A1单臂Completed身份，220/440仍未闭合。
新主实验仅计划8×4×3，正式种子/新驾驶/模型加载/前向/CUDA均0；八模板和独立分支端点尚未合格；时变证据先离线。
11项本轮CPU逻辑检查通过；旧47项检查复用未重跑。图形批注9待制作，Word未覆盖，原批注保留。
唯一下一步：独立复核本轮review packet中的文稿、Q1/Q2/Q5边界和路线任务资格合同；不自动进入实现或运行。

记录时间：{now}。旧交接原文保留如下。

'''
for name in ['CURRENT_HANDOFF.md','NEXT_AGENT_PROMPT.md']:
    p=ROOT/name
    if marker not in p.read_text(): p.write_bytes(note.encode()+p.read_bytes())
for name in ['AGENT_WORKLOG.md','COMMAND_LOG.md']:
    p=ROOT/name
    if marker not in p.read_text():
        appendix='\n\n'+note.replace('旧交接原文保留如下。','本轮命令详见 '+REL+'/COMMAND_LOG.md。')
        with p.open('ab') as f:f.write(appendix.encode())

state_path=ROOT/'STATE.json'
state=json.loads(state_path.read_text())
old=json.loads((OUT/'evidence/STATE_ENTRY.json').read_text())
if 'experiment_restructure_step2' not in state:
    state['_prev_current_task_before_step2']=old.get('current_task')
    state['_prev_status_before_step2']=old.get('status')
    state['_prev_step_before_step2']=old.get('step')
    state['current_task']='STEP2 复核落实、方法替换稿、实验新章与最小主实验协议'
    state['status']=STATUS
    state['step']='四份文稿/协议完成并待独立复核；图待制作、Q2/Q5与路线任务资格缺口显式保留；未迁移未运行'
    state['experiment_restructure_step2']={
        'status':STATUS,'time':now,'output_directory':REL,'deliverables':[REL+'/'+x for x in FOUR],
        'review_packet':REL+'/REVIEW_PACKET.zip','entry_head':entry['head'],'drafts_written':4,
        'comments_mapped':10,'figure_comment_status':'PENDING_PRODUCTION',
        'paper_sha256':sha(word),'paper_modified':False,'production_modified':False,
        'historical_labels_modified':False,'live_migration_performed':False,
        'cpu_targeted_tests_passed':11,'historical_tests_rerun':False,
        'new_experiment_runs':0,'formal_seeds_generated':0,'models_loaded':0,'new_vla_forwards':0,
        'carla_launches':0,'cuda_contexts_created':0,'benchmark_restarted':False,
        'bench2drive_full_220_440_identity':'UNRESOLVED_NOT_A_TOTAL_COMPLETION_CENSUS',
        'next_single_task':'独立复核本轮review packet中的文稿与协议，重点Q1/Q2/Q5边界和路线任务资格合同',
        'next_action':'STOP_AFTER_DELIVERY_NO_AUTOMATIC_IMPLEMENTATION_OR_EXECUTION'}
    write_json(state_path,state)

preserved={}
for name in ['CURRENT_HANDOFF.md','NEXT_AGENT_PROMPT.md','AGENT_WORKLOG.md','COMMAND_LOG.md']:
    b=(ROOT/name).read_bytes();record=entry['handoff_baseline'][name];n=record['bytes']
    old_part=b[-n:] if name in ['CURRENT_HANDOFF.md','NEXT_AGENT_PROMPT.md'] else b[:n]
    preserved[name]=hashlib.sha256(old_part).hexdigest()==record['sha256']
assert all(preserved.values()),preserved
allowed_changed={'current_task','status','step'}
unchanged_old_state={k:state.get(k)==v for k,v in old.items() if k not in allowed_changed}
assert all(unchanged_old_state.values())

exit_status=git('status','--porcelain=v1','--untracked-files=normal')
(OUT/'evidence/GIT_EXIT_STATUS.txt').write_bytes(exit_status)
entry_lines=set((OUT/'evidence/GIT_ENTRY_STATUS.txt').read_text().splitlines())
exit_lines=set(exit_status.decode().splitlines())
exit_head=git('rev-parse','HEAD').decode().strip()
exit_record={'time':now,'head':exit_head,'entry_head_matches':exit_head==entry['head'],
             'entry_status_lines':len(entry_lines),'exit_status_lines':len(exit_lines),
             'new_status_lines':sorted(exit_lines-entry_lines),'removed_status_lines':sorted(entry_lines-exit_lines),
             'tracked_diff':git('diff','--stat').decode(),'staged_diff':git('diff','--cached','--stat').decode(),
             'protected_source_files':len(baseline),'protected_source_drift':source_drift,
             'original_word_unchanged':True,'existing_handoff_text_preserved':preserved,
             'old_state_nodes_preserved_except_current_pointer':all(unchanged_old_state.values()),
             'authorized_existing_file_changes':['CURRENT_HANDOFF.md','NEXT_AGENT_PROMPT.md','AGENT_WORKLOG.md','COMMAND_LOG.md','STATE.json']}
assert not exit_record['tracked_diff'] and not exit_record['staged_diff']
assert not exit_record['removed_status_lines']
assert exit_head==entry['head']
write_json(OUT/'evidence/EXIT.json',exit_record)

chapter=(OUT/FOUR[1]).read_text()
expected=['4.1 Experimental Setup','4.2 Research Questions','4.3 Evaluation Metrics','4.4 Main Comparisons','4.5 Ablation and Mechanism Analysis','4.6 Closed-Loop Execution and Standard Driving','4.7 Failure Analysis and Limitations']
headings=re.findall(r'^## (.+)$',chapter,re.M)
assert headings==expected,headings
comments=json.loads((OUT/'evidence/PAPER_COMMENTS.json').read_text())['comments']
patch=(OUT/FOUR[0]).read_text()
assert len(comments)==10 and all(re.search(r'^\| '+c['id']+r' / ',patch,re.M) for c in comments)
assert '待制作' in patch and '待制作' in chapter
assert '\\tag{5}' in patch and '\\tag{6}' in patch and '\\tag{7}' not in patch
assert '计划实验，尚未执行' in chapter and '计划实验，尚未执行' in (OUT/FOUR[2]).read_text()
assert not re.search(r'^#{2,4} .*（RQ[123]）',chapter,re.M)
for p in (OUT/'scripts').glob('*.py'): ast.parse(p.read_text())
for p in OUT.rglob('*.json'): json.loads(p.read_text())

# 明确纳入复核包；不复制原始 Word、大型数据或仓库树。
s1=ROOT/'reports/driveclarify_experiment_restructure_step1_20260912'
members=[OUT/x for x in FOUR]+[s1/x for x in ['FINAL_REPORT.md','METHOD_CONTRACT_CANDIDATE.md','PAPER_ERRATA.md','EVIDENCE_LEDGER.csv']]
members += [OUT/'evidence'/x for x in ['CODE_EXCERPTS.md','CODE_SOURCE_MANIFEST.json','COUNT_SUMMARY.json','PAPER_IDENTITY.json','PAPER_COMMENTS.json','TARGETED_CHECKS.json','TARGETED_CHECKS.log']]
members += [OUT/'scripts/check_targeted_contract.py']
members += list((OUT/'tables').glob('*.csv'))
readme='''# DriveClarify STEP2 review packet

状态：STEP2_PARTIAL_WITH_EXPLICIT_GAPS。四份文稿已成稿，未迁移、未运行。
先读 reports/driveclarify_experiment_restructure_step2_20260912/FINAL_REPORT.md，再读方法替换稿、实验新章、最小协议。
目录结构保留仓库相对路径；STEP1 报告原样保留，其旧公式号/旧待决状态须结合 STEP2 勘误理解。
evidence/CODE_EXCERPTS.md 保留实际源码行号与 SHA-256；COUNT_SUMMARY 是 STEP1 计数摘录，不是新实验。
缺失原始驾驶数据、未打包的 RQ2/RQ3 报告和其他深层引用需在原仓库按文稿路径访问；不声称本包能独立重跑全部历史实验。
Q2 的旧一致性出口、revised 注释冲突、Q1 离线校验差异、Q5 授权边界均在方法稿；图形批注待制作。
原Word SHA-256匹配；本包含身份和10条批注，不包含Word/图片/模型权重。
所有tables CSV仅有表头；正式种子未物化。
PACKET_MANIFEST.json逐文件校验，压缩包外另有REVIEW_PACKET.sha256。
'''
packet_manifest={'status':STATUS,'members':[{'path':p.relative_to(ROOT).as_posix(),'sha256':sha(p),'bytes':p.stat().st_size} for p in members],
                 'README.md_sha256':hashlib.sha256(readme.encode()).hexdigest()}
write_json(OUT/'evidence/PACKET_MANIFEST.json',packet_manifest)
with zipfile.ZipFile(OUT/'REVIEW_PACKET.zip','w',zipfile.ZIP_DEFLATED) as z:
    z.writestr('README.md',readme)
    z.writestr('PACKET_MANIFEST.json',json.dumps(packet_manifest,ensure_ascii=False,indent=2))
    for p in members:z.write(p,p.relative_to(ROOT).as_posix())
(OUT/'REVIEW_PACKET.sha256').write_text(sha(OUT/'REVIEW_PACKET.zip')+'  REVIEW_PACKET.zip\n')
with zipfile.ZipFile(OUT/'REVIEW_PACKET.zip') as z:
    assert z.testzip() is None
    for item in packet_manifest['members']:
        assert hashlib.sha256(z.read(item['path'])).hexdigest()==item['sha256']

broken=[]
for name in FOUR:
    p=OUT/name
    for target in re.findall(r'\[[^\]]*\]\(([^)]+)\)',p.read_text()):
        if target.startswith(('http:','https:','#')):continue
        destination=target.split('#')[0]
        if destination=='evidence/DELIVERY_QA.json':continue
        if not (p.parent/destination).exists():broken.append({'file':name,'target':target})
assert not broken,broken
tests=json.loads((OUT/'evidence/TARGETED_CHECKS.json').read_text())
assert tests['all_passed'] and tests['tests_run']==11
qa={'status':'PASS_DOCUMENT_DELIVERY_CHECKS_ONLY','scientific_status':STATUS,'time':now,
    'four_requested_documents_present':all((OUT/p).is_file() for p in FOUR),
    'chapter_headings':headings,'comments_mapped':len(comments),'figure_status':'PENDING_PRODUCTION',
    'original_docx_hash_matches':True,'word_visual_full_review':False,
    'local_links_broken':broken,'empty_result_csv_headers_only':len(tables),
    'production_source_and_step1_protected_files':len(baseline),'source_hash_drift':source_drift,
    'handoff_old_text_preserved':preserved,'existing_state_nodes_preserved':True,
    'targeted_cpu_tests':11,'historical_suite_rerun':False,'new_experiment_runs':0,
    'new_formal_seed_values_materialized':0,'review_packet_payload_files':len(members),
    'review_packet_bytes':(OUT/'REVIEW_PACKET.zip').stat().st_size,'review_packet_sha256':sha(OUT/'REVIEW_PACKET.zip'),
    'packet_member_hashes_verified':True,'script_ast_parse':'PASS','json_parse':'PASS',
    'limitations':['文稿结构与完整性校验不证明方法有效','Q1/Q2/Q5残余冲突详见方法稿','B2D完整220/440未闭合','八模板/独立分支端点未合格','未制作新图或全文视觉验收']}
write_json(OUT/'evidence/DELIVERY_QA.json',qa)
write_json(OUT/'MANIFEST.json',{'status':STATUS,'time':now,'entry_head':entry['head'],'exit_head':exit_head,
           'files':[{'path':p.relative_to(OUT).as_posix(),'sha256':sha(p),'bytes':p.stat().st_size}
                    for p in sorted(OUT.rglob('*')) if p.is_file() and p.name!='MANIFEST.json'],
           'excluded_from_self_hash':['MANIFEST.json']})
print(json.dumps({'status':STATUS,'delivery_QA':qa['status'],'protected_files':len(baseline),'targeted_tests':11,
                  'exit_git_lines':len(exit_lines),'git_added':exit_record['new_status_lines'],
                  'packet_bytes':qa['review_packet_bytes'],'packet_sha256':qa['review_packet_sha256']},ensure_ascii=False,indent=2))
