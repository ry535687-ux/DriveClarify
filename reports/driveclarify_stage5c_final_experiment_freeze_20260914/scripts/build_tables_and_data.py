"""Copy frozen outputs and format tables. Never calls a predictor or a scorer."""
from pathlib import Path
from collections import Counter
import csv
import hashlib
import json
import shutil

OUT = Path(__file__).resolve().parents[1]
ROOT = OUT.parents[1]
OLD = ROOT / 'reports/driveclarify_stage5a_controlled_main_ablation_20260914'
POLICIES = ['NO_CLARIFICATION', 'IMMEDIATE_QUERY', 'DRIVECLARIFY']


def read_csv(path):
    with path.open(newline='') as handle:
        return list(csv.DictReader(handle))


def write_csv(path, rows):
    with path.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def check_sources():
    pins = json.loads((OUT / 'evidence/SOURCE_INTEGRITY_ENTRY.json').read_text())
    for item in pins['sources']:
        path = ROOT / item['path']
        if hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
            raise RuntimeError('Source changed: ' + item['path'])


def amount(value, denominator):
    return f'{value:g}/{denominator} ({100 * value / denominator:.2f}%)'


def main():
    check_sources()
    copied = []
    for name in ['MAIN_POLICY_RESULTS.csv', 'MAIN_LAYOUT_SUMMARY.csv', 'ABLATION_EFFECTS.csv',
                 'TRAJECTORY_TASK_POINTS.csv', 'TIMING_POINT_DATA.csv']:
        shutil.copyfile(OLD / name, OUT / 'plot_data' / name)
        copied.append(dict(source=str((OLD / name).relative_to(ROOT)),
                           output='plot_data/' + name, byte_identical=True))
    for name in ['MAIN_COMPARISON.json', 'ABLATION_RESULTS.json']:
        shutil.copyfile(OLD / name, OUT / 'evidence' / name)
    shutil.copyfile(OLD / 'evidence/CLOSED_LOOP_EVIDENCE.json', OUT / 'evidence/CLOSED_LOOP_EVIDENCE.json')
    annotation_path = ROOT / 'reports/driveclarify_stage5b_heldout_evaluation_20260914/ANNOTATION_SOURCE_CHECK.json'
    shutil.copyfile(annotation_path, OUT / 'evidence/ANNOTATION_SOURCE_CHECK.json')

    rows = read_csv(OUT / 'plot_data/MAIN_POLICY_RESULTS.csv')
    summary = []
    for split in ['DEV', 'HIST', 'POOLED_DESCRIPTIVE']:
        for policy in POLICIES:
            group = [r for r in rows if r['policy'] == policy and (split == 'POOLED_DESCRIPTIVE' or r['split'] == split)]
            defined = [r for r in group if r['truth_defined'] == 'True']
            eq = [r for r in defined if r['truth_relation'] == 'TASK_EQUIVALENT']
            summary.append(dict(split=split, policy=policy, records_all=len(group),
                                layouts=len({r['layout_id'] for r in group}),
                                records_defined=len(defined), records_undefined=len(group)-len(defined),
                                correct=sum(float(r['correct_decision']) for r in defined),
                                wrong=sum(float(r['wrong_task']) for r in defined),
                                query_defined=sum(float(r['query']) for r in defined),
                                query_all=sum(float(r['query']) for r in group),
                                coverage_all=sum(float(r['coverage']) for r in group),
                                eq_denominator=len(eq), unnecessary_eq=sum(float(r['query']) for r in eq)))
    write_csv(OUT / 'plot_data/FINAL_MAIN_SUMMARY.csv', summary)
    lines = ['# Final main comparison', '',
             'Controlled decision benchmark under supplied structured task obligations and idealized answers. '
             'The pooled row is descriptive. Event amounts average the frozen A/B intent pair within each divergent base record; they are not passenger counts.', '',
             '| Split | Policy | Correct decision, defined | Wrong-task commitment, defined | Query, defined |',
             '|---|---|---:|---:|---:|']
    for r in summary:
        n = r['records_defined']
        lines.append(f"| {r['split']} | {r['policy']} | {amount(r['correct'], n)} | {amount(r['wrong'], n)} | {amount(r['query_defined'], n)} |")
    lines += ['', 'DEV: 64 records / 8 layouts, 48 defined and 16 undefined. HIST: 112 / 14, 84 defined and 28 undefined. '
              'Total: 176 / 22, 132 defined and 44 undefined. The defined subset contains 88 EQ and 44 DV records.', '',
              '| Split | Policy | Query, all inputs | Decision coverage, all inputs | Unnecessary query, EQ only |',
              '|---|---|---:|---:|---:|']
    for r in summary:
        lines.append(f"| {r['split']} | {r['policy']} | {amount(r['query_all'], r['records_all'])} | {amount(r['coverage_all'], r['records_all'])} | {amount(r['unnecessary_eq'], r['eq_denominator'])} |")
    lines += ['', 'DriveClarify abstains on all 44 undefined inputs: coverage 132/176 = 75%, unresolved 44/176 = 25%, '
              'and all-input query 44/176 = 25%. Correctness is undefined for these 44 records. '
              'Baseline coverage on undefined inputs measures output resolution only, including a fixed-A service diagnostic for IMMEDIATE_QUERY.', '',
              'The 100% correct-decision rate applies only to the defined subset under this controlled construction. '
              'Balanced counterfactual intents and repeated layout composition make all per-layout rates identical; '
              'the archived paired-layout bootstrap intervals collapse to point values and provide no statistical advantage or generalization guarantee.', '',
              'Sources: [stored policy outcomes](plot_data/MAIN_POLICY_RESULTS.csv), '
              '[layout outcomes](plot_data/MAIN_LAYOUT_SUMMARY.csv), [frozen comparison](evidence/MAIN_COMPARISON.json).']
    (OUT / 'FINAL_MAIN_TABLE.md').write_text('\n'.join(lines) + '\n')

    effects = read_csv(OUT / 'plot_data/ABLATION_EFFECTS.csv')
    for r in effects:
        r['effect_percentage_points'] = 100 * float(r['effect_ablated_minus_full'])
        r['ci_low_percentage_points'] = '' if r['ci_low'] == '' else 100 * float(r['ci_low'])
        r['ci_high_percentage_points'] = '' if r['ci_high'] == '' else 100 * float(r['ci_high'])
        r['denominator_kind'] = 'paired layouts' if r['ablation'] == 'WITHOUT_TASK_GATE' else 'complete shared-trajectory endpoints'
    write_csv(OUT / 'plot_data/FINAL_ABLATION_EFFECTS_PP.csv', effects)
    ab = json.loads((OUT / 'evidence/ABLATION_RESULTS.json').read_text())
    lines = ['# Final ablation comparison', '', 'Effects are ablated minus full. Task Gate reuses IMMEDIATE_QUERY exactly; it is not another run.', '',
             '| Ablation | Cohort / denominator | Outcome | Full → ablated | Effect (percentage points) |',
             '|---|---|---|---:|---:|',
             '| w/o Task-Consequence Gate | DEV 48; HIST 84 defined records (8 / 14 paired layouts) | Query | 33.33% → 100% | +66.67 |',
             '| w/o Task-Consequence Gate | DEV 32; HIST 56 EQ records | Unnecessary query | 0% → 100% | +100.00 |',
             '| w/o Task-Consequence Gate | Defined subset | Correct decision | 100% → 100% | 0.00 |']
    for source in ['ORIGINAL', 'EXTENSION']:
        group = ab['temporal'][source]; n = group['complete_endpoints']
        for metric in ['evidence_sufficient', 'actionable_windows']:
            full, removed = group['memory']['B2'][metric], group['memory']['B1'][metric]
            lines.append(f'| w/o Temporal Memory | {source}, {n} complete endpoints | {metric} | {full} → {removed} | {100*(removed-full)/n:+.2f} |')
        full, removed = group['timing']['JOINT']['late'], group['timing']['EVIDENCE_ONLY']['late']
        lines.append(f'| w/o Timing Constraint | {source}, {n} complete endpoints | Late trigger | {full} → {removed} | {100*(removed-full)/n:+.2f} |')
        count = group['timing']['JOINT']['timely_trigger']
        lines.append(f'| w/o Timing Constraint | {source}, {n} complete endpoints | Timely opportunities / triggers | {count} → {count} | 0.00 |')
    lines += ['', 'Memory: full uses retained valid evidence (B2); ablated uses the current observation (B1). '
              'Timing: full uses the joint evidence-and-timing rule; ablated uses evidence only. '
              'These are offline analyses of shared saved trajectories, with 47/48 and 23/24 complete endpoints. '
              'The two incomplete endpoints remain in the archive with null outcomes; no zero imputation.', '',
              'Removing timing increases total triggers 12→29 / 7→17 but creates no additional timely opportunity. '
              'The 17 / 10 suppressed triggers occur after the protocol deadline, not after an independently certified physical safety boundary. '
              'No uncertainty interval is invented for temporal counts.', '',
              'Sources: [frozen results](evidence/ABLATION_RESULTS.json), [effects and units](plot_data/FINAL_ABLATION_EFFECTS_PP.csv).']
    (OUT / 'FINAL_ABLATION_TABLE.md').write_text('\n'.join(lines) + '\n')

    annotations = read_csv(ROOT / 'reports/driveclarify_stage5b_heldout_annotation_20260914/annotations_B.csv')
    counts = Counter(r['label'] for r in annotations)
    write_csv(OUT / 'plot_data/ANNOTATION_APPENDIX_COUNTS.csv', [dict(label=k, count=v, scope='semantic_annotation_only') for k, v in sorted(counts.items())])
    (OUT / 'evidence/DATA_DERIVATION.json').write_text(json.dumps(dict(
        copied_csv=copied, method_predictions_executed=0, scientific_experiments_added=0,
        transformations=['Byte copies of five Stage5A CSVs', 'Aggregate stored outcome fields without re-scoring',
                         'Multiply frozen rate effects and interval endpoints by 100', 'Count existing B annotations; no new annotation'],
        annotation_A_provenance='Prior user-supplied blind output and agreement record; no separate A CSV was available.'
    ), ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(dict(main_summary_rows=len(summary), ablation_effect_rows=len(effects), copied_csv=len(copied), annotation_counts=counts)))


if __name__ == '__main__':
    main()
