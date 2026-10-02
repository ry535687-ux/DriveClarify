"""Validate frozen evidence, manuscript contracts and exports without scientific execution."""
from pathlib import Path
from collections import Counter, defaultdict
import ast
import csv
import hashlib
import json
import math
import re
import subprocess
import xml.etree.ElementTree as ET

OUT = Path(__file__).resolve().parents[1]
ROOT = OUT.parents[1]
OLD = ROOT / 'reports/driveclarify_stage5a_controlled_main_ablation_20260914'


def read_json(path):
    return json.loads(path.read_text())


def read_csv(path):
    with path.open(newline='') as handle:
        return list(csv.DictReader(handle))


def close(a, b):
    return math.isclose(float(a), float(b), abs_tol=1e-9)


def run_checks(final_pass=False):
    checks = []

    def check(name, function):
        try:
            detail = function()
            checks.append(dict(name=name, passed=True, detail=detail))
        except Exception as error:
            checks.append(dict(name=name, passed=False, error=f'{type(error).__name__}: {error}'))

    def require(condition, message):
        if not condition:
            raise AssertionError(message)

    def sources():
        pins = read_json(OUT / 'evidence/SOURCE_INTEGRITY_ENTRY.json')
        for item in pins['sources']:
            data = (ROOT / item['path']).read_bytes()
            require(len(data) == item['bytes'] and hashlib.sha256(data).hexdigest() == item['sha256'], item['path'])
        require(subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip() == pins['git_head'], 'Git HEAD changed')
        require(hashlib.sha256(subprocess.check_output(['git','diff','--binary','HEAD'],cwd=ROOT)).hexdigest() == pins['git_tracked_diff_sha256'], 'Tracked diff changed')
        return dict(unchanged_sources=len(pins['sources']), prior_frozen_comparisons=len(pins['previous_hash_checks']))
    check('source_hashes_and_tracked_git_preserved', sources)

    def copied_data():
        for name in ['MAIN_POLICY_RESULTS.csv','MAIN_LAYOUT_SUMMARY.csv','ABLATION_EFFECTS.csv','TRAJECTORY_TASK_POINTS.csv','TIMING_POINT_DATA.csv']:
            require((OUT/'plot_data'/name).read_bytes() == (OLD/name).read_bytes(), name)
        return '5 original CSVs byte-identical'
    check('five_csvs_byte_identical', copied_data)

    base = read_csv(OUT/'plot_data/MAIN_POLICY_RESULTS.csv')
    layout = read_csv(OUT/'plot_data/MAIN_LAYOUT_SUMMARY.csv')
    summary = read_csv(OUT/'plot_data/FINAL_MAIN_SUMMARY.csv')
    comparison = read_json(OUT/'evidence/MAIN_COMPARISON.json')
    ablation = read_json(OUT/'evidence/ABLATION_RESULTS.json')

    def main_units():
        require(len(base)==528 and len(layout)==66 and len(summary)==9, 'row counts')
        require(len({(r['record_id'],r['policy']) for r in base})==528, 'duplicate record-policy identity')
        for split, n, k, defined in [('DEV',64,8,48),('HIST',112,14,84)]:
            group=[r for r in base if r['split']==split and r['policy']=='DRIVECLARIFY']
            require(len(group)==n and len({r['layout_id'] for r in group})==k, split)
            require(sum(r['truth_defined']=='True' for r in group)==defined, 'defined count '+split)
            require(Counter(r['truth_relation'] for r in group if r['truth_defined']=='True') == {'TASK_EQUIVALENT':defined*2//3,'TASK_DIVERGENT':defined//3}, 'balance '+split)
        return '176 base records / 22 layouts; 132 defined / 44 undefined'
    check('main_units_and_denominators', main_units)

    def main_rates():
        for r in summary:
            n=int(r['records_defined']);p=r['policy']
            targets={'NO_CLARIFICATION':(5/6,1/6,0),'IMMEDIATE_QUERY':(1,0,1),'DRIVECLARIFY':(1,0,1/3)}[p]
            for col, target in zip(['correct','wrong','query_defined'],targets):
                require(close(float(r[col])/n,target), str(r))
            if r['split']!='POOLED_DESCRIPTIVE':
                old=comparison['splits'][r['split']]['policies'][p]['defined']
                for col,key in [('correct','correct_decision'),('wrong','wrong_task'),('query_defined','query')]:
                    require(close(r[col], old[key]['numerator_base_equivalent']), col)
            if p=='DRIVECLARIFY':
                require(close(float(r['coverage_all'])/int(r['records_all']),.75), 'coverage')
                require(close(float(r['query_all'])/int(r['records_all']),.25), 'all query')
        return '16.67%→0 wrong; 100%→33.33% query; defined correct100%; all-input coverage75%'
    check('main_rates_match_archived_comparison', main_rates)

    def undefined():
        missing=[r for r in base if r['truth_defined']=='False']
        require(len(missing)==132, '44 undefined × 3 policies')
        for r in missing:
            require(r['correct_decision']=='' and r['wrong_task']=='' and r['correctness']=='', 'undefined was scored')
            if r['policy']=='DRIVECLARIFY':
                require(r['action_before_answer']=='ABSTAIN' and close(r['coverage'],0) and close(r['query'],0), 'abstention')
        return 'Undefined correctness remains null/empty, 44 DriveClarify abstentions'
    check('undefined_not_scored_or_zero_imputed', undefined)

    def pairing():
        for r in layout:
            group=[b for b in base if b['layout_id']==r['layout_id'] and b['split']==r['split'] and b['policy']==r['policy'] and b['truth_defined']=='True']
            for key in ['correct_decision','wrong_task','query']:
                require(close(sum(float(b[key]) for b in group)/len(group),r[key]), key)
            require(len(group)==6 and int(r['records_all'])==8 and int(r['records_undefined'])==2, 'repeated layout composition')
        return '66 paired layout-policy outcomes, each 6 defined / 8 total records'
    check('paired_layout_values_and_balanced_composition', pairing)

    def effects():
        old=read_csv(OUT/'plot_data/ABLATION_EFFECTS.csv'); new=read_csv(OUT/'plot_data/FINAL_ABLATION_EFFECTS_PP.csv')
        require(len(old)==len(new)==18, 'effect count')
        for a,b in zip(old,new):
            require(all(a[k]==b[k] for k in a), 'changed original effect fields')
            require(close(100*float(a['effect_ablated_minus_full']), b['effect_percentage_points']), 'pp transform')
            for side in ['low','high']:
                require((a['ci_'+side]=='' and b['ci_'+side+'_percentage_points']=='') or
                        (a['ci_'+side]!='' and close(100*float(a['ci_'+side]), b['ci_'+side+'_percentage_points'])), 'CI transform')
        require(ablation['task_gate']['new_duplicate_baseline_run'] is False, 'duplicate gate baseline')
        return '18 effects preserved; temporal CI remains missing'
    check('ablation_effects_and_units_preserved', effects)

    def temporal_counts():
        for source,n,full,without,windows,nowindows,late in [('ORIGINAL',47,29,17,12,6,17),('EXTENSION',23,17,0,7,0,10)]:
            a=ablation['temporal'][source]
            require(a['complete_endpoints']==n and a['incomplete_endpoints']==1, source)
            require(a['memory']['B2']['evidence_sufficient']==full and a['memory']['B1']['evidence_sufficient']==without,'memory sufficient')
            require(a['memory']['B2']['actionable_windows']==windows and a['memory']['B1']['actionable_windows']==nowindows,'memory windows')
            require(a['timing']['JOINT']['late']==0 and a['timing']['EVIDENCE_ONLY']['late']==late,'late')
            require(a['timing']['JOINT']['timely_trigger']==a['timing']['EVIDENCE_ONLY']['timely_trigger']==windows,'timely unchanged')
        return 'Memory29→17/17→0; window12→6/7→0; late0→17/0→10; timely12/7'
    check('temporal_ablation_counts', temporal_counts)

    timing=read_csv(OUT/'plot_data/TIMING_POINT_DATA.csv')
    def temporal_points():
        require(len(timing)==144 and len({(r['source'],r['record_id']) for r in timing})==72,'timing grain')
        require(sum(r['endpoint_complete']=='True' for r in timing)==140,'incomplete views')
        groups=defaultdict(list)
        for r in timing:groups[(r['source'],r['record_id'])].append(r)
        for pair in groups.values():
            require(len(pair)==2 and {r['memory_condition'] for r in pair}=={'B1','B2'},'view pairing')
            require(pair[0]['shared_trace_digest']==pair[1]['shared_trace_digest'],'shared trace')
        for source, memory, n in [('ORIGINAL','B1',17),('ORIGINAL','B2',29),('EXTENSION','B1',0),('EXTENSION','B2',17)]:
            values=[r for r in timing if r['source']==source and r['memory_condition']==memory and r['endpoint_complete']=='True' and r['first_evidence_sufficient_time']!='']
            require(len(values)==n,'sufficiency count')
            for r in values:
                require(close(float(r['deadline_time_s'])-float(r['first_evidence_sufficient_time']),r['remaining_margin_s']),'margin identity')
                require(close(float(r['reference_commitment_time_s'])-float(r['frozen_reserve_s']),r['deadline_time_s']),'deadline identity')
        return '144 rows / 72 units; 70 complete endpoints; 63 sufficient view observations'
    check('timing_point_pairing_missingness_and_margin', temporal_points)

    def trajectory_points():
        data=read_csv(OUT/'plot_data/TRAJECTORY_TASK_POINTS.csv')
        require(len(data)==203 and sum(r['truth_relation']=='' for r in data)==44,'trajectory count')
        for source,target in ablation['trajectory_mechanism'].items():
            subset=[r for r in data if r['source']==source]
            require(len(subset)==target['records_all'],source)
            require(sum(r['truth_relation']!='' for r in subset)==target['defined'],'defined')
            require(sum(r['truth_relation']=='TASK_DIVERGENT' and r['trajectory_relation']=='TASK_EQUIVALENT' for r in subset)==target['close_but_divergent'],'close DV')
            require(sum(r['truth_relation']=='TASK_EQUIVALENT' and r['trajectory_relation']=='TASK_DIVERGENT' for r in subset)==target['far_but_equivalent'],'far EQ')
        return '203 observations, 159 defined and 44 displayed undefined; source arms separate'
    check('trajectory_source_counts_and_mismatches', trajectory_points)

    def annotation():
        prior=read_json(OUT/'evidence/ANNOTATION_SOURCE_CHECK.json')
        labels=read_csv(ROOT/'reports/driveclarify_stage5b_heldout_annotation_20260914/annotations_B.csv')
        require(Counter(r['label'] for r in labels)==prior['B_label_counts'],'B labels')
        require(len(labels)==30 and prior['B_matches_user_consensus']==30,'30 consensus')
        require(prior['annotation_pass_agreement_as_confirmed_by_user']=={'exact_agreement':30,'agreement_rate':1.0,'cohen_kappa':1.0}, 'reported agreement')
        require(prior['gold_csv_materialized'] is False,'not gold freeze')
        return 'B file verified; A/agreement/human verification retained as user-supplied provenance, not recomputed A labels'
    check('annotation_provenance_and_balanced_counts', annotation)

    def closed_loop():
        hist=read_json(ROOT/'reports/driveclarify_rq3_v3_technical_validity_recovery_and_resume_v1/PART_B_FINAL_RESULTS.json')
        native=read_json(OUT/'evidence/CLOSED_LOOP_EVIDENCE.json')['clear_B']
        h=[v for k,v in hist['condition_native_completion'].items() if 'CRITICAL' in k]
        low=[v for k,v in hist['condition_native_completion'].items() if 'EQUIVALENT' in k]
        require((sum(v['numerator'] for v in h),sum(v['denominator'] for v in h))==(15,19),'HIGH completion')
        require((sum(v['numerator'] for v in low),sum(v['denominator'] for v in low))==(15,15),'LOW completion')
        require(hist['HIGH_lifecycle_composite']['numerator']==19 and hist['native_completion']['numerator']==30,'lifecycle')
        require(native['native_status']==['Completed'] and native['native_completion']==[100],'native completion')
        require(native['MinSpeedTest']=='FAILURE' and native['min_speed_records']==17 and native['safety_status']=='FAIL','safety fields')
        require(native['layers']['ACTUAL_APPLY_CONTROL_INDEPENDENTLY_VERIFIED']['status']=='UNKNOWN','control certification')
        motion=read_json(ROOT/'reports/driveclarify_stage3c_b_native_runtime_20260914/evidence/B_OBSERVED_MOTION.json')
        require(close(motion['max_xy_displacement_from_first_m'],52.82636046309565),'motion')
        return '19 lifecycles, completion15/19+15/15=30/34; native negative and UNKNOWN fields retained'
    check('closed_loop_endpoints_and_negative_evidence', closed_loop)

    def render_counts():
        rendered=read_json(OUT/'evidence/FIGURE_RENDER_RECEIPT.json')
        require([x['scatter_points_by_panel'] for x in rendered]==[[24,42,24,42],[2,4,2],[64,112,13,14],[63,63,63,63]],'actual plotted mark counts')
        require(all(not x['text_outside_canvas'] for x in rendered),'text canvas bounds')
        return 'All expected marks rendered; no text outside figure canvases'
    check('actual_figure_marks_and_text_canvas_bounds', render_counts)

    def formats():
        require(len(list((OUT/'figures').glob('*')))==12,'12 figure exports')
        details=[]
        for p in sorted((OUT/'figures').glob('*.pdf')):
            info=subprocess.check_output(['pdfinfo',str(p)],text=True)
            require(re.search(r'Pages:\s+1\b',info) is not None,'single-page PDF')
            text=subprocess.check_output(['pdftotext',str(p),'-'],text=True)
            require(len(text)>200,'searchable vector labels')
            require(p.with_suffix('.png').read_bytes().startswith(b'\x89PNG\r\n\x1a\n'),'PNG header')
            require(ET.parse(p.with_suffix('.svg')).getroot().tag.endswith('svg'),'SVG parse')
            details.append(p.name)
        return details
    check('pdf_svg_png_export_integrity', formats)

    def manuscript():
        text=(OUT/'EXPERIMENT_CHAPTER_V5_FINAL_DRAFT.md').read_text()
        for section in ['4.1 Experimental Setup','4.2 Main Experiment: Controlled Selective Clarification under Structured Task Evidence',
                        '4.3 Ablation Studies','4.4 Mechanism Analysis','4.5 Closed-Loop Validation','4.6 Limitations',
                        'Appendix A. Semantic Consistency Check on New Language Cases']:
            require(section in text,section)
        require('STAGE5' not in text and 'STAGE3' not in text,'engineering stages in manuscript')
        for phrase in ['two independent blinded model-assisted annotation passes, followed by human verification',
                       'structured task obligations rather than raw descriptions; therefore these cases were not scored as a method benchmark',
                       'MinSpeedTest FAILURE','safety endpoint FAIL','apply_control remains UNKNOWN',
                       'balanced' ,'repeated layout composition','75%','16.67%','33.33%']:
            require(phrase.lower() in text.lower(),phrase)
        require(text.count('![ ')==0 and len(re.findall(r'!\[.*?\]\(figures/',text))==4,'4 main embedded figures')
        return 'V5 structure, scoped metrics, annotation wording and negative endpoints present'
    check('manuscript_required_structure_and_claim_boundaries', manuscript)

    def patch_alignment():
        method=(OUT/'METHOD_INPUT_ASSUMPTION_PATCH.md').read_text()
        intro=(OUT/'INTRODUCTION_PATCH.md').read_text()
        contribution=(OUT/'CONTRIBUTION_PATCH.md').read_text()
        require('DriveClarify does not introduce a new free-form semantic parser.' in method,'method assumption')
        require('We evaluate selective clarification and temporal mechanisms under controlled task evidence, and separately validate the answer–replanning–execution chain in closed-loop driving.' in intro,'introduction')
        require('We integrate clarification outcomes with fresh replanning and validate the resulting execution chain in closed-loop runs.' in contribution,'contribution3')
        limit=(OUT/'LIMITATIONS_PATCH.md').read_text()
        require(limit.count('|')>=30,'nine-item limitations map')
        return 'Requested Method, Introduction and Contribution wording; nine limitations mapped'
    check('section_patches_aligned', patch_alignment)

    def links():
        count=0
        pending={'ARTIFACT_MANIFEST.json','ARTIFACT_VERIFICATION.json','evidence/ACCEPTANCE_RESULTS.json','FINAL_STATUS.json'}
        for file in OUT.rglob('*.md'):
            for target in re.findall(r'!?\[[^\]]+\]\(([^)]+)\)',file.read_text()):
                if target.startswith(('https://','http://','#')):continue
                target=target.split('#')[0]
                p=(file.parent/target).resolve()
                if not final_pass and p.is_relative_to(OUT) and str(p.relative_to(OUT)) in pending:continue
                require(p.exists(),str(file.relative_to(OUT))+' → '+target)
                count+=1
        return dict(existing_local_links=count)
    check('manuscript_and_report_local_links', links)

    def no_runtime_imports():
        forbidden={'carla','torch','transformers','driveclarify_rq1_conditional_supplement','driveclarify_rq1_grounded_relation_v3'}
        for file in (OUT/'scripts').glob('*.py'):
            tree=ast.parse(file.read_text())
            for node in ast.walk(tree):
                modules=[a.name for a in node.names] if isinstance(node,ast.Import) else [node.module or ''] if isinstance(node,ast.ImportFrom) else []
                require(not any(m.split('.')[0] in forbidden for m in modules),file.name)
        require(read_json(OUT/'evidence/DATA_DERIVATION.json')['method_predictions_executed']==0,'prediction receipt')
        return 'Only data formatting, plotting, static checks and packaging in new scripts'
    check('new_scripts_do_not_import_scientific_runtime', no_runtime_imports)

    def required_files():
        names=['FINAL_REPORT.md','FINAL_EXPERIMENT_STRUCTURE.md','FINAL_CLAIMS_BOUNDARY.md','EXPERIMENT_CHAPTER_V5_FINAL_DRAFT.md',
               'INTRODUCTION_PATCH.md','CONTRIBUTION_PATCH.md','METHOD_INPUT_ASSUMPTION_PATCH.md','LIMITATIONS_PATCH.md','CONCLUSION_PATCH.md',
               'FINAL_MAIN_TABLE.md','FINAL_ABLATION_TABLE.md','FINAL_CLOSED_LOOP_TABLE.md','ANNOTATION_SANITY_CHECK.md','PAPER_CHANGE_MAP.md','FIGURE_PLAN.md',
               'evidence/FIGURE_QA.md','COMMAND_LOG.md']
        for name in names:require((OUT/name).is_file() and (OUT/name).stat().st_size>0,name)
        for name in ['figures','plot_data','scripts']:require((OUT/name).is_dir(),name)
        return dict(required_documents=len(names), output_directories=3)
    check('required_deliverables_present', required_files)
    return dict(scope='Artifact/source/claim integrity only; no new scientific experiment or prediction.',
                passed=all(r['passed'] for r in checks), checks_passed=sum(r['passed'] for r in checks),
                checks_total=len(checks), checks=checks)


if __name__ == '__main__':
    result=run_checks()
    print(json.dumps(result,ensure_ascii=False,indent=2))
    raise SystemExit(0 if result['passed'] else 1)
