"""从已捕获的公开实体、顺序及距离构造语义证据；不读取策略输出。"""
import json,math
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
R=ROOT/'reports/driveclarify_rq3_paired_comparison_v2_task_binding_qualification_and_execution';Q=R/'qualification'
p=R/'V2_TEMPLATE_CANDIDATE_MANIFEST.json';m=json.loads(p.read_text());probe=json.loads((Q/'MAP_PROBE_RECEIPT.json').read_text())
rows=[]
for t in m['templates']:
 b=json.loads(Path(t['task_binding_path']).read_text());regions=b['regions'];high=t['level']=='HIGH';family=t['family'];a,breg=regions[0],regions[-1]
 evidence={};desc=[]
 if family=='REF':
  layout=json.loads((R/'candidate_assets'/(t['condition']+'_LAYOUT.json')).read_text());vans=[x for x in layout['actors'] if x['blueprint']=='vehicle.mercedes.sprinter'];assert len(vans)==2
  for i,v in enumerate(vans):
   assert v['attributes']['color']=='255,255,255'
   rr=regions[i] if high else regions[0]
   assert v['public_associated_region']==rr['region_id']
   desc.append(('The nearer white van; ' if i==0 else 'the farther white van; ')+('use the marked bay beside that van, pause briefly, then rejoin the assigned route.'))
  evidence={'two_physical_referents':vans,'constraint':'只接受原文限定的 marked bay；不同车的指称不是不同颜色或不同任务类型。'}
 elif family=='LMK':
  prefixes=['BP_Block06_5_','BP_House01_2_'] if high else ['BP_House01_2_','BP_House01_53_']
  groups=[]
  for i,prefix in enumerate(prefixes):
   members=[x for x in probe['buildings'] if x['name'].startswith(prefix)];assert members,prefix
   center=members[0]['transform_xyz'];rr=regions[i] if high else regions[0]
   otherdist=[math.dist(center[:2],x['center_xyz'][:2]) for x in regions]
   assert otherdist.index(min(otherdist))==i if high else True
   groups.append({'public_building_group':prefix,'object_ids':[x['id'] for x in members],'root_transform_xyz':center,'associated_region':rr['region_id'],'center_to_region_distances_m':otherdist})
   desc.append('The '+('nearer' if i==0 else 'farther')+' roadside building adjacent to a marked bay; stop in that bay, then rejoin the assigned route.')
  evidence={'public_building_groups':groups,'domain_restriction':'场景确有更多建筑构件；不声称整条道路只有两栋建筑。由 marked bay 限定任务域：HIGH 两个标记任务区，LOW 一个；同区内多建筑指称合并为同一任务后果。'}
 elif family=='ORD':
  positions=[20,32,50] if high else [20,40]
  lengths=[2.5,12,12] if high else [12,12]
  allsecond=positions[1];usable=[x for x,l in zip(positions,lengths) if l>=2*2.44619083404541][1]
  assert allsecond==a['official_route_index'] and usable==breg['official_route_index']
  desc=['Count every cone-marked opening, including the short opening; stop at the second, then rejoin the assigned route.','Count only cone-marked openings large enough for this car; stop at the second, then rejoin the assigned route.']
  evidence={'ordered_route_indices':positions,'opening_lengths_m':lengths,'actual_ego_body_length_m':2*2.44619083404541,'all_openings_second':allsecond,'car_usable_openings_second':usable,'semantic_scope':'second 的序数保持不变；歧义来自被计数集合是否包含小开口，不将 second 改成 first 或 third。'}
 else:
  points=[x['official_xyz'] for x in probe['route_rows']];dist=[0]
  for u,v in zip(points,points[1:]):dist.append(dist[-1]+math.dist(u,v))
  distances=[dist[x['official_route_index']]-dist[14] for x in regions]
  assert len(distances)==2 and distances[0]<distances[1]
  desc=['A suitable distance means the nearer marked pull-over bay; pause there, then continue on the assigned route.','A suitable distance means the farther marked pull-over bay; pause there, then continue on the assigned route.']
  evidence={'distance_from_public_anchor_along_official_route_m':distances,'omitted_parameter':'乘客对适宜停靠距离的接受范围；原文未提供近/远界限。','constraint_realizations':[{'candidate':'A','accepted_marked_region':a['region_id']},{'candidate':'B','accepted_marked_region':breg['region_id']}],'family_preserved':'SUITABLE_DISTANCE 约束欠定；不是改写历史 USC 路线，也不是仅给两条同结果路径贴不同标签。'}
 t['candidate_interpretations']={'A':desc[0],'B':desc[1]};t['semantic_evidence_path']=str(Q/'FAMILY_SEMANTIC_EVIDENCE.json')
 rows.append({'condition':t['condition'],'family_kind':t['expected_family_kind'],'interpretations':t['candidate_interpretations'],'evidence':evidence,'task_relation':t['task_relation_static'],'plausible_both':True,'balanced_truth_structurally_possible':True,'review_basis':'前瞻公开实体/原生视角/地图状态的语义判断；没有使用模型效果，未声称独立人类标注或统计语义效度验证。','scope_limit':'七个受控模板；不外推所有自然语言歧义场景。'})
p.write_text(json.dumps(m,indent=2,ensure_ascii=False)+'\n');(Q/'FAMILY_SEMANTIC_EVIDENCE.json').write_text(json.dumps({'rows':rows,'native_policy_results_read':False,'formal_exposure':0},indent=2,ensure_ascii=False)+'\n')
print('SEMANTIC_EVIDENCE',len(rows))
