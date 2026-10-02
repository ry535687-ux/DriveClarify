"""独立文件验收器：不导入 builder、不解答题目。只用标准库。"""
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import stat
import sys
import xml.etree.ElementTree as ET

OUT=Path(__file__).resolve().parents[1]
ROOT=OUT.parents[1]


def load(name):
    return json.loads((OUT/name).read_text(encoding='utf-8'))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def content_digest(obj):
    return hashlib.sha256(json.dumps(obj,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def walk(x):
    if isinstance(x,dict):
        for k,v in x.items():
            yield k,v
            yield from walk(v)
    elif isinstance(x,list):
        for v in x:
            yield from walk(v)


def verify():
    frozen=(OUT/'PACKET_FREEZE.json').exists()
    canonical=load('ANNOTATION_PACKET_CANONICAL.json' if frozen else 'evidence/CANDIDATES_PRIVATE.json')
    private=load('PACKET_PROVENANCE_PRIVATE.json')
    audit=load('HELDOUT_DUPLICATE_AUDIT.json')
    inventory=load('evidence/HISTORICAL_AUDIT_INDEX_PRIVATE.json')
    checks={}
    expected=[f'HOLDOUT_{i:03d}' for i in range(1,31)]
    assert [c['case_id'] for c in canonical]==expected
    assert len({content_digest({k:v for k,v in c.items() if k!='case_id'}) for c in canonical})==30
    checks['30_unique_cases_and_neutral_ids']=True
    top={'case_id','passenger_instruction','interpretation_A','interpretation_B','scene_description','task_evidence','map_context'}
    ek={'referent_description','junction_or_execution_location','road_branch_description','task_completion_obligation'}
    forbidden=re.compile(r'label|truth|automatic|expected|intended|target_class|driveclarify|revised_relation|judge|correct_candidate|true_intent|policy_action|ask_expected|should_ask|historical_result|prediction|provenance|construction|^m[345]$',re.I)
    for c in canonical:
        assert set(c)==top
        assert c['interpretation_A'].strip()!=c['interpretation_B'].strip()
        assert all(isinstance(c[k],str) and c[k].strip() for k in ('passenger_instruction','interpretation_A','interpretation_B','scene_description'))
        assert set(c['task_evidence'])=={'candidate_A','candidate_B'}
        for ev in c['task_evidence'].values():
            assert set(ev)==ek and all(v is None or isinstance(v,str) and v.strip() for v in ev.values())
        assert set(c['map_context'])=={'public_description','optional_static_geometry_summary'}
        assert all(isinstance(v,str) and v for v in c['map_context'].values())
        for k,v in walk(c):
            assert not forbidden.search(k),(c['case_id'],k)
        assert not re.search(r'TASK_EQUIVALENT|TASK_DIVERGENT|INSUFFICIENT_EVIDENCE|DriveClarify|\bM[345]\b',json.dumps(c,ensure_ascii=False),re.I)
    checks['schema_interpretations_map_fields_and_recursive_no_leak']=True
    assert not any(k=='figure_file' for k,v in walk(canonical))
    checks['all_referenced_figures_exist_no_figures_required']=True
    assert private['design_coverage']==dict(shared_interval=10,separate_interval=10,partial_record=10)
    assert Counter(p['family'] for p in private['per_case'])=={'Referential':8,'Landmark':8,'Order / ordinal':7,'Execution-location':7}
    assert private['model_or_automatic_answer_outputs']==[] and private['forbidden_runtime_modules_loaded']==[]
    assert all(v==0 for v in private['stage_execution_counts'].values())
    checks['private_coverage_and_runtime_boundary']=True
    for source in inventory['files']:
        assert digest(ROOT/source['path'])==source['sha256'],source['path']
    assert inventory['parse_errors']==[]
    assert inventory['group_counts']['C_DEV']==64 and inventory['group_counts']['C_HIST']==112
    assert inventory['group_counts']['A_HISTORICAL_CONFIG']==48 and inventory['group_counts']['B_HISTORICAL_CONFIG']==28
    assert sum(s['audit_role']=='B_FIRST_DECISION_IDENTITY_ONLY' for s in inventory['files'])==27
    checks['historical_source_counts_and_preservation']=True
    for key in ('C_DEV_exact_duplicates','C_HIST_exact_duplicates','all_sources_exact_duplicates','normalized_instruction_duplicates','same_task_evidence_tuples','same_map_task_combinations','source_parse_errors'):
        assert audit['summary'][key]==0,key
    assert len(audit['per_case'])==30
    assert all(not r['exact_content_duplicate'] and not r['same_source_motif'] for r in audit['within_packet_near_duplicates'])
    checks['exact_and_near_duplicate_audit_recorded']=True
    source=private['construction_materials_source']
    assert digest(ROOT/source['path'])==source['sha256']
    root=ET.parse(ROOT/source['path']).getroot()
    roads={r.get('id'):r for r in root.findall('road')}
    paths=[]
    for case,p in zip(canonical,private['per_case']):
        assert p['case_id']==case['case_id']
        motif=p['source_motif']; nodes=motif['junction_path']; edges=motif['main_roads']
        assert len(nodes)==5 and len(set(nodes))==5 and len(edges)==4
        assert not set(nodes)&{'498','576','655'}
        for a,b,rid in zip(nodes,nodes[1:],edges):
            r=roads[rid]; ends={r.find('link/predecessor').get('elementId'),r.find('link/successor').get('elementId')}
            assert ends=={a,b} and r.get('junction')=='-1'
        lengths=[float(roads[rid].get('length')) for rid in edges[1:3]]
        assert lengths==motif['between_lengths_m'] and all(n>=15 for n in lengths)
        assert all(f'{n:.1f} m' in case['map_context']['optional_static_geometry_summary'] for n in lengths)
        for node,side_list in zip(nodes[1:4],motif['side_roads']):
            for rid in side_list:
                r=roads[rid]
                ends={r.find('link/predecessor').get('elementId'),r.find('link/successor').get('elementId')}
                assert node in ends and len(ends&set(nodes))==1
        if p.get('requires_extra_side_at_J1'):
            assert len(motif['side_roads'][0])>=2
        paths.append(tuple(nodes[1:4]))
    assert len(set(paths))==30
    checks['all_30_map_motifs_and_lengths_rechecked_from_XML']=True
    guideline=(OUT/'ANNOTATION_GUIDELINE.md').read_text()
    assert all(x in guideline for x in ('TASK_EQUIVALENT','TASK_DIVERGENT','INSUFFICIENT_EVIDENCE','参照物','轨迹距离','不能默认'))
    assert not any(x in guideline for x in ('我们的贡献','DriveClarify应该','期望结果','主实验假设','10/10/10'))
    checks['neutral_guideline']=True
    # 检查可执行脚本的 import 节点；模块文本中存在禁令名称不等于导入。
    imports=[]
    for path in sorted((OUT/'scripts').glob('*.py')):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node,ast.Import): imports.extend(n.name for n in node.names)
            elif isinstance(node,ast.ImportFrom): imports.append(node.module or '')
    assert not any(x.startswith(('torch','carla','driveclarify','judges','transformers','cuda')) for x in imports)
    checks['builder_has_no_forbidden_imports']=True
    if frozen:
        freeze=load('PACKET_FREEZE.json')
        for key,name in [('canonical_sha256','ANNOTATION_PACKET_CANONICAL.json'),('packet_A_sha256','ANNOTATION_PACKET_A.json'),('packet_B_sha256','ANNOTATION_PACKET_B.json'),('guideline_sha256','ANNOTATION_GUIDELINE.md'),('duplicate_audit_sha256','HELDOUT_DUPLICATE_AUDIT.json')]:
            assert digest(OUT/name)==freeze[key]
        expected_hashes={c['case_id']:content_digest(c) for c in canonical}
        assert expected_hashes==freeze['per_case_content_sha256']
        a,b=load('ANNOTATION_PACKET_A.json'),load('ANNOTATION_PACKET_B.json')
        assert len(a)==len(b)==30
        assert {c['case_id']:content_digest(c) for c in a}=={c['case_id']:content_digest(c) for c in b}==expected_hashes
        assert a!=b and a!=canonical and b!=canonical
        for name in ('ANNOTATION_PACKET_CANONICAL.json','ANNOTATION_PACKET_A.json','ANNOTATION_PACKET_B.json','ANNOTATION_GUIDELINE.md','PACKET_FREEZE.json'):
            assert not stat.S_IMODE((OUT/name).stat().st_mode)&0o222
        checks['frozen_bytes_per_case_hashes_order_and_readonly_permissions']=True
        assert freeze['distribution_allowlist']==dict(Annotator_A=['ANNOTATION_PACKET_A.json','ANNOTATION_GUIDELINE.md'],Annotator_B=['ANNOTATION_PACKET_B.json','ANNOTATION_GUIDELINE.md'])
        for name,h in freeze['builder_source_sha256'].items(): assert digest(OUT/'scripts'/name)==h
        checks['distribution_isolation_and_builder_hashes']=True
    print(json.dumps(dict(mode='frozen' if frozen else 'pre_freeze',checks=checks,passed=len(checks),
        case_count=30,relation_annotations_created=0,predictions_created=0),ensure_ascii=False,indent=2))


if __name__=='__main__':
    verify()
