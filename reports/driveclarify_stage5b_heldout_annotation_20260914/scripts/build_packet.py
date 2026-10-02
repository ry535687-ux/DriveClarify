"""只读历史去重 + 静态题面构造 + 冻结。python -I -S 执行，无第三方依赖。"""
import argparse
import ast
from collections import Counter, defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
import hashlib
import json
import os
from pathlib import Path
import random
import re
import runpy
import subprocess
import sys
import unicodedata
import xml.etree.ElementTree as ET

OUT = Path(__file__).resolve().parents[1]
ROOT = OUT.parents[1]
VERSION = 'stage5b0.static-packet-builder.1.0.0'
MAP = ROOT / 'reports/driveclarify_rq3_paired_comparison_v2_task_binding_qualification_and_execution/qualification/Town03_server.xodr'
CROOT = ROOT / 'reports/driveclarify_rq1_grounded_relation_v3_20260910'
AROOT = ROOT / 'reports/driveclarify_rq1_v4_ord_critical_stability_and_formal_v1'
BROOT = ROOT / 'reports/driveclarify_ablation_overnight_20260909_v1'
FORBIDDEN = {'truth_relation', 'gold_label', 'automatic_label', 'expected_label', 'intended_label',
             'target_class', 'driveclarify_relation', 'revised_relation', 'judge_output',
             'correct_candidate', 'true_intent', 'policy_action', 'ask_expected', 'should_ask',
             'historical_result', 'm3', 'm4', 'm5', 'prediction', 'predictions'}
TOP_KEYS = {'case_id', 'passenger_instruction', 'interpretation_A', 'interpretation_B',
            'scene_description', 'task_evidence', 'map_context'}
EVIDENCE_KEYS = {'referent_description', 'junction_or_execution_location',
                 'road_branch_description', 'task_completion_obligation'}


def guard(event, args):
    if event == 'import':
        name = str(args[0]).lower()
        if name.startswith(('torch', 'carla', 'driveclarify', 'judges', 'transformers', 'cuda')):
            raise RuntimeError('禁止本阶段导入方法或运行时: ' + name)
    if event in ('socket.connect', 'os.system', 'ctypes.dlopen'):
        raise RuntimeError('本阶段禁止外部运行时/网络: ' + event)
    if event == 'subprocess.Popen' and not (args[0] == 'rg' and args[1][:2] == ['rg', '--files']):
        raise RuntimeError('仅允许 rg 文件盘点子进程')


sys.addaudithook(guard)


def now():
    return datetime.now(timezone.utc).isoformat()


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha(path):
    return sha_bytes(path.read_bytes())


def compact(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def norm(value):
    return ' '.join(re.findall(r'[\w]+', unicodedata.normalize('NFKC', str(value)).casefold()))


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from strings(v)


def map_motifs():
    root = ET.parse(MAP).getroot()
    roads = {r.get('id'): r for r in root.findall('road')}
    graph = defaultdict(list)
    for r in roads.values():
        a, b = r.find('link/predecessor'), r.find('link/successor')
        if r.get('junction') == '-1' and a is not None and b is not None and a.get('elementType') == b.get('elementType') == 'junction':
            u, v = a.get('elementId'), b.get('elementId')
            if u != v:
                graph[u].append((v, r.get('id')))
                graph[v].append((u, r.get('id')))
    excluded = {'498', '576', '655'}  # C DEV/HIST 在 Town03 的历史单路口位置。
    motifs = []
    def extend(nodes, edges):
        if len(nodes) == 5:
            sides = [[edge for dst, edge in sorted(graph[nodes[i]]) if dst not in nodes] for i in (1, 2, 3)]
            if all(sides):
                motifs.append(dict(town='Town03', junction_path=nodes, main_roads=edges,
                    visible_junctions=dict(zip(('J1','J2','J3'), nodes[1:4])), side_roads=sides,
                    between_lengths_m=[float(roads[e].get('length')) for e in edges[1:3]],
                    evidence_kind='OpenDRIVE road endpoint adjacency; schematic task overlay',
                    source_xpath=[f"road[@id='{e}']/link" for e in edges + [s[0] for s in sides]]))
            return
        for nxt, edge in sorted(graph[nodes[-1]]):
            if nxt not in nodes and nxt not in excluded:
                extend(nodes + [nxt], edges + [edge])
    for n in sorted(graph):
        if n not in excluded:
            extend([n], [])
    random.Random(510017).shuffle(motifs)
    assert len(motifs) >= 30 and sum(len(m['side_roads'][0]) >= 2 for m in motifs) >= 7
    return motifs


IDENTITY_KEYS = {'sample_id', 'case_id', 'unit_id', 'layout_id', 'run_id', 'family'}
INPUT_KEYS = ('passenger_instruction', 'raw_instruction', 'instruction')
TEXT_KEYS = {'text', 'description', 'instruction', 'candidate_text'}
TASK_KEYS = {'task_evidence', 'candidate_obligations', 'task_signatures', 'given_task_signatures',
             'task_completion_region', 'completion_predicate', 'public_topology_target',
             'maneuver', 'ordering', 'constraint', 'task_target', 'candidate_region_map'}


def stripped_task(value):
    # 删除证书、候选 ID、自动关系等，仅保留历史任务结构的字面原子，绝不解释成答案。
    if isinstance(value, dict):
        return {k: stripped_task(v) for k, v in value.items()
                if k not in {'candidate_id', 'certificate_id', 'binding_id', 'certified', 'relevant_components', 'binding_evidence', 'field_sources'}
                and not any(x in k.lower() for x in ('label', 'truth', 'prediction', 'judge', 'relation', 'expected'))}
    if isinstance(value, list):
        return [stripped_task(v) for v in value]
    return value


def project_record(obj, path, pointer, group):
    inp = obj.get('method_input', obj)
    instruction = next((inp[k] for k in INPUT_KEYS if isinstance(inp.get(k), str)), '')
    if not instruction:
        return None
    candidates = inp.get('candidates', inp.get('alternatives', inp.get('candidate_texts', [])))
    if not candidates and 'interpretation_A' in inp:
        candidates = [inp['interpretation_A'], inp.get('interpretation_B', '')]
    if isinstance(candidates, dict):
        candidates = list(candidates.values())
    texts = []
    for candidate in candidates if isinstance(candidates, list) else []:
        if isinstance(candidate, str):
            texts.append(candidate)
        elif isinstance(candidate, dict):
            texts.append(next((candidate[k] for k in ('text','description','instruction','candidate_text') if isinstance(candidate.get(k), str)), ''))
    runtime = inp.get('runtime_context', {})
    task = {k: stripped_task(v) for owner in (inp, runtime) for k, v in owner.items() if k in TASK_KEYS}
    map_data = {k: inp[k] for k in ('route_context','map_context','ego_state','observation_anchor_xyz','route_source') if k in inp}
    identity = {k: obj[k] for k in IDENTITY_KEYS if k in obj}
    if 'layout_id' in identity:
        map_data['layout_id'] = identity['layout_id']
    row = dict(group=group, source_path=str(path.relative_to(ROOT)), pointer=pointer,
               identity=identity, instruction=instruction, candidates=texts,
               task=task, map=map_data)
    row['content_sha256'] = sha_bytes(compact({k: row[k] for k in ('instruction','candidates','task','map')}))
    return row


def descendants(value, pointer='$'):
    if isinstance(value, dict):
        yield pointer, value
        for k, v in value.items():
            # 只遍历原始/配置材料，跳过标签与预测子树。
            if not any(x in k.lower() for x in ('prediction','judge_output','truth','annotation','label','historical_result')):
                yield from descendants(v, pointer + '.' + k)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from descendants(v, f'{pointer}[{i}]')


def source_inventory():
    paths = {}
    for split in ('DEV', 'HIST'):
        paths[CROOT / f'method_inputs/{split}_METHOD_INPUTS.jsonl'] = f'C_{split}'
        paths[CROOT / f'label_authority/{split}_LABELS.jsonl'] = 'RQ1_IDENTITY_ONLY'
    for path in (AROOT / 'formal_run_configs').glob('*.json'):
        paths[path] = 'A_HISTORICAL_CONFIG'
    for path in (BROOT / 'formal_preparation/configs').glob('*.json'):
        paths[path] = 'B_HISTORICAL_CONFIG'
    for path in (BROOT / 'formal/native').glob('*/owner_evidence/ABL_CANDIDATE_TIMELINE.jsonl'):
        paths[path] = 'B_FIRST_DECISION_IDENTITY_ONLY'
    candidates = subprocess.run(['rg', '--files', 'reports', 'tests', 'configs', 'driveclarify_rq1_conditional_supplement'], cwd=ROOT, check=True, text=True, capture_output=True).stdout.splitlines()
    for rel in candidates:
        p = ROOT / rel
        low = rel.lower()
        if p.is_relative_to(OUT) or not any(x in low for x in ('rq1','ablation','task_relation','stage5a')):
            continue
        if any(x in low for x in ('/results/', '/figures/', '/cache/', '/native/', '/formal_runs/', '/logs/', '/predictions')):
            continue
        name = p.name.lower()
        chosen = p.suffix in ('.json','.jsonl') and (
            '/method_inputs/' in low or '/label_authority/' in low or
            '/formal_run_configs/' in low or '/formal_configs/' in low or '/formal_preparation/configs/' in low or
            'task_binding.json' in name or 'scene_manifest.json' in name or
            '/scenarios/' in low or '/fixtures/' in low or
            ('/configs/' in low and 'rq1' in low))
        if chosen:
            paths.setdefault(p, 'RQ1_IDENTITY_ONLY' if '/label_authority/' in low else 'RQ1_OTHER_RAW')
        if p.suffix == '.py' and low.startswith('tests/') and any(x in low for x in ('rq1','task_relation')):
            paths.setdefault(p, 'JUDGE_DEVELOPMENT_STATIC_LITERALS')
    rows, files, identities, literal_texts = [], [], [], []
    for path, group in sorted(paths.items()):
        data = path.read_bytes()
        item = dict(path=str(path.relative_to(ROOT)), sha256=sha_bytes(data), bytes=len(data), audit_role=group)
        before = len(rows)
        if group == 'B_FIRST_DECISION_IDENTITY_ONLY':
            payload = [json.loads(line) for line in data.decode().splitlines() if line.strip()]
            item['saved_candidate_rows'] = len(payload)
            item['linked_config'] = str((BROOT / 'formal_preparation/configs' / (path.parents[1].name + '.json')).relative_to(ROOT))
            assert (ROOT / item['linked_config']).exists()
        elif group == 'JUDGE_DEVELOPMENT_STATIC_LITERALS':
            tree = ast.parse(data.decode('utf-8'), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Dict):
                    for k, v in zip(node.keys, node.values):
                        if isinstance(k, ast.Constant) and k.value in INPUT_KEYS and isinstance(v, ast.Constant) and isinstance(v.value, str):
                            literal_texts.append(dict(source_path=item['path'], line=v.lineno, instruction=v.value))
        else:
            payloads = [json.loads(line) for line in data.decode().splitlines() if line.strip()] if path.suffix == '.jsonl' else [json.loads(data)]
            for line, payload in enumerate(payloads, 1):
                if group == 'RQ1_IDENTITY_ONLY':
                    # 仅输出身份投影；annotation / truth 等值绝不进入审计索引或题面。
                    identities.append(dict(source_path=item['path'], line=line,
                        **{k: payload[k] for k in IDENTITY_KEYS if k in payload}))
                    continue
                found_parent_input = set()
                for pointer, obj in descendants(payload, f'line:{line}'):
                    if any(pointer.startswith(q) for q in found_parent_input):
                        continue
                    row = project_record(obj, path, pointer, group)
                    if row:
                        rows.append(row)
                        if 'method_input' in obj:
                            found_parent_input.add(pointer + '.method_input')
        item['projected_task_records'] = len(rows) - before
        files.append(item)
    counts = Counter(x['group'] for x in rows)
    assert counts['C_DEV'] == 64 and counts['C_HIST'] == 112, counts
    assert counts['A_HISTORICAL_CONFIG'] == 48 and counts['B_HISTORICAL_CONFIG'] == 28, counts
    assert sum(x['audit_role']=='B_FIRST_DECISION_IDENTITY_ONLY' for x in files) == 27
    known_ids = {x['identity'].get('sample_id') for x in rows}
    for x in identities:
        if x.get('sample_id'):
            assert x['sample_id'] in known_ids, ('标签身份未关联原输入', x['sample_id'])
    return dict(files=files, records=rows, label_identity_projection=identities,
                judge_development_instruction_literals=literal_texts,
                group_counts=dict(counts), parse_errors=[])


def similarity(a, b):
    na, nb = norm(a), norm(b)
    ta, tb = set(na.split()), set(nb.split())
    overlap = 2 * len(ta & tb) / (len(ta) + len(tb)) if ta or tb else 0
    return round(max(overlap, SequenceMatcher(None, na, nb, autojunk=False).ratio()), 6)


def public_fingerprint(c):
    return sha_bytes(compact({k:v for k,v in c.items() if k != 'case_id'}))


def audit(cases, provenance, inventory):
    # 以下只比较字面内容、地图实体及原始任务字段，不调用/实现任务关系分类器。
    records = inventory['records']
    per_case = []
    hist_instrs = defaultdict(list)
    for row in records:
        hist_instrs[norm(row['instruction'])].append(row)
    literal_instrs = {norm(x['instruction']) for x in inventory['judge_development_instruction_literals']}
    for case, prov in zip(cases, provenance):
        instruction = case['passenger_instruction']
        candidate_text = ' '.join((case['interpretation_A'], case['interpretation_B']))
        exact_instr = hist_instrs[norm(instruction)]
        new_semantics = dict(instruction=instruction, candidates=[case['interpretation_A'],case['interpretation_B']],
                             task=case['task_evidence'], map=case['map_context'])
        signature = sha_bytes(compact(new_semantics))
        # 保存底层真实地图原子的指纹，不能只用新 case_id / J1 等局部 ID 排除重复。
        source_tuple = dict(town='Town03', junctions=prov['source_motif']['junction_path'][1:4],
                            approach_and_main_roads=prov['source_motif']['main_roads'],
                            side_roads=[x[0] for x in prov['source_motif']['side_roads']])
        nearest = {}
        for group in ('C_DEV','C_HIST','A_HISTORICAL_CONFIG','B_HISTORICAL_CONFIG','RQ1_OTHER_RAW'):
            ranked = []
            for row in records:
                if row['group'] != group:
                    continue
                s1 = similarity(instruction, row['instruction'])
                s2 = similarity(candidate_text, ' '.join(row['candidates']))
                ranked.append((max(s1, s2), s1, s2, row))
            ranked.sort(key=lambda x:(-x[0],x[3]['source_path'],x[3]['pointer']))
            nearest[group] = [dict(source_path=r['source_path'], pointer=r['pointer'], identity=r['identity'],
                instruction=r['instruction'], instruction_similarity=s1, candidate_similarity=s2,
                near_duplicate_flag=score >= .72) for score,s1,s2,r in ranked[:3]]
        exact = [r for r in records if signature == r['content_sha256']]
        text_pair = sorted(norm(x) for x in (case['interpretation_A'],case['interpretation_B']))
        swapped_text_exact = [dict(source_path=r['source_path'],pointer=r['pointer']) for r in records
                              if norm(instruction)==norm(r['instruction']) and text_pair==sorted(norm(x) for x in r['candidates'])]
        # 在所有已投影历史任务中查相同的真实地图组合，忽略记录和候选身份编号。
        same_map = []
        same_tuple = []
        new_task_text = sorted(norm(x) for x in strings(case['task_evidence']))
        for r in records:
            task_text = sorted(norm(x) for x in strings(r['task']))
            if task_text and new_task_text == task_text:
                same_tuple.append(dict(source_path=r['source_path'], pointer=r['pointer']))
            historical_map_text = ' '.join(strings(r['map']))
            # 必须匹配 Town03 以及全部三个真实 junction；单个数字子串不算命中。
            historical_junctions = set(re.findall(r'junction[:_ -]+(\d+)', historical_map_text, flags=re.I))
            if 'town03' in historical_map_text.lower() and set(source_tuple['junctions']).issubset(historical_junctions):
                same_map.append(dict(source_path=r['source_path'], pointer=r['pointer']))
        near = any(x['near_duplicate_flag'] for group in nearest.values() for x in group)
        per_case.append(dict(case_id=case['case_id'], exact_duplicate_C_DEV=sum(r['group']=='C_DEV' for r in exact),
            exact_duplicate_C_HIST=sum(r['group']=='C_HIST' for r in exact),
            exact_duplicate_all_sources=len(exact), normalized_instruction_duplicate_count=len(exact_instr),
            normalized_instruction_candidate_pair_duplicates_order_invariant=swapped_text_exact,
            judge_literal_instruction_duplicate=norm(instruction) in literal_instrs,
            same_task_evidence_tuple=same_tuple, same_map_task_combination=same_map,
            source_map_task_tuple=source_tuple, source_map_task_tuple_sha256=sha_bytes(compact(source_tuple)),
            nearest_historical_records=nearest, suspicious_near_duplicate=near,
            historical_relationship_review=(
                "复用低风险语言模板与 Town03 静态道路素材；重新组合三个连续路口、参照实体、措辞及解释。"
                "C 的 Town03 单路口 498/576/655 不在本题完整五节点路径内；C 历史候选为道路属性选择，"
                "本题是对象/地标/顺序/执行边界的静态任务。A 历史输入是旧指令配预给任务签名，"
                "B 是停车/暂停/继续任务；本题不沿用其任务义务或位置绑定。"
                "最近文本相似项逐项保留供管理员复核，不用字符串低相似度宣称绝对语义新颖。"),
            new_combination_dimensions=['source junction/road sequence','reference entities','instruction phrasing','candidate interpretation pair']))
    internal=[]
    for i,a in enumerate(cases):
        for j,b in enumerate(cases[:i]):
            score=similarity(a['passenger_instruction'],b['passenger_instruction'])
            if score >= .72:
                internal.append(dict(case_ids=[b['case_id'],a['case_id']],instruction_similarity=score,
                    exact_content_duplicate=public_fingerprint(a)==public_fingerprint(b),
                    same_source_motif=provenance[i]['source_motif']['junction_path']==provenance[j]['source_motif']['junction_path'],
                    review='共享允许的语言结构；不同静态参照物与真实路口组合。共享模板不计为独立模板泛化证据。'))
    summary = dict(C_DEV_records=64,C_HIST_records=112,A_historical_configs=48,B_historical_configs=28,
                   B_saved_first_decisions=27,RQ1_other_raw_records=inventory['group_counts'].get('RQ1_OTHER_RAW',0),
                   C_DEV_exact_duplicates=sum(x['exact_duplicate_C_DEV'] for x in per_case),
                   C_HIST_exact_duplicates=sum(x['exact_duplicate_C_HIST'] for x in per_case),
                   all_sources_exact_duplicates=sum(x['exact_duplicate_all_sources'] for x in per_case),
                   normalized_instruction_duplicates=sum(x['normalized_instruction_duplicate_count'] for x in per_case),
                   same_task_evidence_tuples=sum(len(x['same_task_evidence_tuple']) for x in per_case),
                   same_map_task_combinations=sum(len(x['same_map_task_combination']) for x in per_case),
                   suspicious_historical_near_duplicate_cases=sum(x['suspicious_near_duplicate'] for x in per_case),
                   internal_template_similarity_pairs=len(internal),
                   source_parse_errors=len(inventory['parse_errors']))
    return dict(audit_version='static-literal-and-source-map-audit-v1',summary=summary,
        normalization='Unicode NFKC + casefold + word-token punctuation/whitespace normalization; IDs excluded from exact content. Candidate text similarity uses both A/B texts. Task tuple check compares sorted normalized raw task values, not labels.',
        near_duplicate_screen='max(token Dice, character SequenceMatcher), threshold 0.72; top 3 per source group retained even below threshold; source map comparison uses actual Town03 junction numbers, not HOLDOUT/local aliases.',
        limitations='跨格式文本/任务原子与地图实体检查不是通用语义判定器。旧资料缺少可对齐的完整地图组合时，结合原始任务、参照物和来源审阅。测试代码只静态提取 instruction 字面量，不执行夹具。无绝对语义新颖性或独立地图抽样声明。',
        inventory_file='evidence/HISTORICAL_AUDIT_INDEX_PRIVATE.json',per_case=per_case,within_packet_near_duplicates=internal)


def validate(cases, a=None, b=None):
    assert len(cases)==30
    ids=[c['case_id'] for c in cases]
    assert ids==[f'HOLDOUT_{i:03d}' for i in range(1,31)] and len(set(ids))==30
    for c in cases:
        assert set(c)==TOP_KEYS
        assert c['interpretation_A'].strip() and c['interpretation_B'].strip()
        assert norm(c['interpretation_A'])!=norm(c['interpretation_B'])
        assert set(c['task_evidence'])=={'candidate_A','candidate_B'}
        assert set(c['map_context'])=={'public_description','optional_static_geometry_summary'}
        for e in c['task_evidence'].values():
            assert set(e)==EVIDENCE_KEYS
            assert all(v is None or isinstance(v,str) and v.strip() for v in e.values())
        for pointer,obj in descendants(c):
            for key in obj:
                assert key.casefold() not in FORBIDDEN and 'label' not in key.casefold(), (c['case_id'],key)
        text=json.dumps(c,ensure_ascii=False).casefold()
        assert not any(re.search(r'\b'+re.escape(x)+r'\b',text) for x in FORBIDDEN)
        assert not any(x in text for x in ('task_equivalent','task_divergent','insufficient_evidence','driveclarify','provenance','construction_coverage'))
    assert len({public_fingerprint(c) for c in cases})==30
    checks=dict(unique_30_ids=True,two_distinct_interpretations=True,strict_visible_key_allowlist=True,
        forbidden_fields_absent=True,sensitive_label_keys_absent=True,method_outputs_absent=True,
        map_fields_present=True,all_referenced_figures_exist=True,referenced_figure_count=0,
        provenance_excluded=True,internal_exact_duplicates_absent=True)
    if a is not None and b is not None:
        expected={c['case_id']:sha_bytes(compact(c)) for c in cases}
        assert len(a)==len(b)==30
        assert expected=={c['case_id']:sha_bytes(compact(c)) for c in a}=={c['case_id']:sha_bytes(compact(c)) for c in b}
        assert [c['case_id'] for c in a]!=[c['case_id'] for c in b]
        assert [c['case_id'] for c in a]!=ids and [c['case_id'] for c in b]!=ids
        checks.update(canonical_A_B_per_case_hashes_equal=True,A_B_only_order_differs=True)
    return checks


def prepare():
    assert not (OUT/'PACKET_FREEZE.json').exists(), '冻结后禁止原地构造；须新建 ANNOTATION_PACKET_V2'
    materials=runpy.run_path(str(OUT/'scripts/case_materials.py'))
    cases,provenance=materials['construct'](map_motifs())
    checks=validate(cases)
    inventory=source_inventory()
    result=audit(cases,provenance,inventory)
    for k in ('C_DEV_exact_duplicates','C_HIST_exact_duplicates','all_sources_exact_duplicates',
              'normalized_instruction_duplicates','same_task_evidence_tuples','same_map_task_combinations','source_parse_errors'):
        assert result['summary'][k]==0,(k,result['summary'][k])
    assert not any(x['judge_literal_instruction_duplicate'] for x in result['per_case'])
    dump(OUT/'evidence/CANDIDATES_PRIVATE.json', cases)
    dump(OUT/'evidence/HISTORICAL_AUDIT_INDEX_PRIVATE.json', inventory)
    dump(OUT/'HELDOUT_DUPLICATE_AUDIT.json',result)
    private=dict(builder_version=VERSION,prepared_at=now(),data_kind='新作者化静态任务卡；非采集、非闭环、非模型生成答案',
        construction_materials_source=dict(path=str(MAP.relative_to(ROOT)),sha256=sha(MAP)),
        material_derivation='仅解析 OpenDRIVE 普通道路的前后 junction 链接，保留连通关系与中间道路长度；使用新静态物体/招牌/边界覆盖层。侧路编号、入口线、商铺边界与入口顺序均是本题设定，不声称来自真实场景观测。无车道方向/转角/可执行驾驶认证。',
        source_boundary='C DEV/HIST、A/B、既有 RQ1 及判断器测试代码仅用于排重；历史输出、轨迹、标签、闭环结果均不用于构造新题或筛选结果。',
        design_coverage=dict(Counter(x['construction_coverage'] for x in provenance)),
        family_distribution=dict(Counter(x['family'] for x in provenance)),
        design_coverage_is_not_annotation_quota=True,gold_creation='仅在两位独立盲标员完成标注并经分歧仲裁后产生。本阶段无逐题答案；不得按 10/10/10 调整样本。',
        random_seeds=dict(motif_order=510017,canonical_id_assignment=510031,candidate_order_base=510100,packet_A=510201,packet_B=510202),
        per_case=provenance,structural_checks=checks,model_or_automatic_answer_outputs=[],
        stage_execution_counts=dict(CARLA=0,VLA_forward=0,CUDA=0,training=0,DriveClarify_prediction=0,judge_calls=0),
        execution_boundary='python -I -S 标准库；导入审计钩子阻断 carla/torch/driveclarify/judges 等；唯一子进程为 rg --files。',
        forbidden_runtime_modules_loaded=[k for k in sys.modules if k.lower().startswith(('carla','torch','cuda','driveclarify','judges','transformers'))])
    dump(OUT/'PACKET_PROVENANCE_PRIVATE.json',private)
    for p in (OUT/'evidence').glob('*PRIVATE.json'):
        p.chmod(0o600)
    (OUT/'PACKET_PROVENANCE_PRIVATE.json').chmod(0o600)
    print(json.dumps(dict(prepared=True,case_count=len(cases),family=private['family_distribution'],audit=result['summary']),ensure_ascii=False))


def freeze():
    assert not (OUT/'PACKET_FREEZE.json').exists(), '已冻结；拒绝覆盖'
    cases=read(OUT/'evidence/CANDIDATES_PRIVATE.json')
    review=read(OUT/'evidence/CONSTRUCTION_REVIEW_PRIVATE.json')
    assert review['all_cases_reviewed_for_material_consistency'] is True
    assert review['no_relation_annotations_or_predictions_created'] is True
    assert review['unresolved_material_errors']==[]
    assert review['draft_sha256']==sha(OUT/'evidence/CANDIDATES_PRIVATE.json')
    inventory=read(OUT/'evidence/HISTORICAL_AUDIT_INDEX_PRIVATE.json')
    for src in inventory['files']:
        assert sha(ROOT/src['path'])==src['sha256'],src['path']
    private=read(OUT/'PACKET_PROVENANCE_PRIVATE.json')
    assert sha(ROOT/private['construction_materials_source']['path'])==private['construction_materials_source']['sha256']
    pa,pb=list(cases),list(cases)
    random.Random(510201).shuffle(pa)
    random.Random(510202).shuffle(pb)
    checks=validate(cases,pa,pb)
    checks.update(historical_exact_duplicates_absent=True,source_hashes_unchanged=True,construction_material_review_complete=True)
    for name,obj in [('ANNOTATION_PACKET_CANONICAL.json',cases),('ANNOTATION_PACKET_A.json',pa),('ANNOTATION_PACKET_B.json',pb)]:
        assert not (OUT/name).exists()
        dump(OUT/name,obj)
    receipt=dict(canonical_sha256=sha(OUT/'ANNOTATION_PACKET_CANONICAL.json'),packet_A_sha256=sha(OUT/'ANNOTATION_PACKET_A.json'),
        packet_B_sha256=sha(OUT/'ANNOTATION_PACKET_B.json'),case_count=30,case_ids=[c['case_id'] for c in cases],
        per_case_content_sha256={c['case_id']:sha_bytes(compact(c)) for c in cases},created_at=now(),builder_version=VERSION,
        per_case_serialization='UTF-8 json.dumps(ensure_ascii=False,sort_keys=True,separators=(comma,colon)), no final newline; includes case_id',
        file_serialization='UTF-8 JSON indent=2, ensure_ascii=False, final LF; file SHA hashes exact bytes',
        guideline_sha256=sha(OUT/'ANNOTATION_GUIDELINE.md'),duplicate_audit_sha256=sha(OUT/'HELDOUT_DUPLICATE_AUDIT.json'),
        builder_source_sha256={p.name:sha(p) for p in sorted((OUT/'scripts').glob('*.py'))},
        status='STAGE5B_PACKET_FROZEN_READY_FOR_BLIND_ANNOTATION',frozen=True,
        change_policy='冻结后不得原地修改 packet；严重错误必须新建 ANNOTATION_PACKET_V2，并让两位标注员重新标注整套 A/B。',
        checks=checks,distribution_allowlist=dict(Annotator_A=['ANNOTATION_PACKET_A.json','ANNOTATION_GUIDELINE.md'],Annotator_B=['ANNOTATION_PACKET_B.json','ANNOTATION_GUIDELINE.md']),
        other_files_admin_only=True)
    dump(OUT/'PACKET_FREEZE.json',receipt)
    for name in ('ANNOTATION_PACKET_CANONICAL.json','ANNOTATION_PACKET_A.json','ANNOTATION_PACKET_B.json','ANNOTATION_GUIDELINE.md','PACKET_FREEZE.json'):
        (OUT/name).chmod(0o444)
    print(json.dumps(receipt,ensure_ascii=False,indent=2))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=('prepare','freeze'))
    args=parser.parse_args()
    prepare() if args.command=='prepare' else freeze()


if __name__=='__main__':
    main()
