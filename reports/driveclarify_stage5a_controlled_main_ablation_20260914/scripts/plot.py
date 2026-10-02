"""使用 CSV 生成检查预览；不调用模型或原生入口。"""
from common import *
import csv
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

COLORS = ['#4b5563', '#b86f1c', '#226e96']
plt.rcParams.update({'font.size': 10, 'axes.spines.top': False, 'axes.spines.right': False,
                     'savefig.dpi': 150, 'figure.facecolor': 'white'})

def rows(name):
    return list(csv.DictReader((REPORT / name).open()))

def save(fig, name):
    fig.savefig(REPORT / name, bbox_inches='tight')
    plt.close(fig)

def main_plot():
    data = rows('MAIN_LAYOUT_SUMMARY.csv')
    fig, axes = plt.subplots(2, 4, figsize=(14, 6.4), sharey=True)
    for i, split in enumerate(('DEV', 'HIST')):
        for j, (metric, title) in enumerate((('correct_decision', 'Correct task decision'), ('wrong_task', 'Wrong-task commitment'),
                                            ('query', 'ASK recommendation'), ('all_coverage_rate', 'Coverage: all records'))):
            ax = axes[i, j]
            layouts = sorted({r['layout_id'] for r in data if r['split'] == split})
            for li, layout in enumerate(layouts):
                vals = [100 * float(next(r[metric] for r in data if r['split'] == split and r['layout_id'] == layout and r['policy'] == p)) for p in POLICIES]
                x = np.arange(3) + (li - (len(layouts)-1)/2) * .015
                ax.plot(x, vals, color='#a9afb8', alpha=.45, linewidth=.65)
                ax.scatter(x, vals, c=COLORS, s=18, zorder=3)
            ax.set(xticks=[0, 1, 2], xticklabels=['P0', 'P1', 'P2'], ylim=(-4, 104), yticks=[0, 25, 50, 75, 100])
            if i == 0: ax.set_title(title)
            if j == 0: ax.set_ylabel(f'{split}: {len(layouts)} layouts\nRate (%)')
            ax.grid(axis='y', color='#e8e8e8')
    fig.suptitle('Controlled decision benchmark | paired layout points', fontsize=15)
    fig.text(.07, .01, 'P0: no clarification   P1: immediate query   P2: DriveClarify. First 3 panels: defined labels (DEV 48 / HIST 84).\nCoverage panels include 16 / 28 undefined labels. Ideal answer service; no new driving. Identical layout rates overlap.', fontsize=9)
    fig.tight_layout(rect=(0, .10, 1, .95))
    save(fig, 'FIG_MAIN_TRADEOFF_PREVIEW.png')

def ablation_plot():
    data = rows('ABLATION_EFFECTS.csv')
    fig, axes = plt.subplots(1, 3, figsize=(15, 5.4))
    metric_names = {'query': 'Query rate', 'unnecessary_query': 'Unnecessary query', 'correct_decision': 'Correct decision',
                    'evidence_sufficient': 'Sufficient evidence', 'actionable_windows': 'Actionable window',
                    'unresolved_no_evidence': 'No sufficient evidence', 'trigger': 'Any trigger', 'late': 'Late trigger', 'rule_unresolved': 'No trigger'}
    for ax, ablation, title in zip(axes, ('WITHOUT_TASK_GATE', 'WITHOUT_MEMORY', 'WITHOUT_TIMING'),
                                    ('Task gate removal', 'Memory removal', 'Timing removal')):
        selected = [r for r in data if r['ablation'] == ablation]
        for y, r in enumerate(selected):
            x = 100 * float(r['effect_ablated_minus_full'])
            ax.scatter(x, y, color='#226e96', s=36)
            if r['ci_low']:
                lo, hi = 100 * float(r['ci_low']), 100 * float(r['ci_high'])
                ax.hlines(y, lo, hi, color='#226e96', linewidth=2)
            ax.annotate(f'{x:+.1f}', (x, y), xytext=(5, 6), textcoords='offset points', fontsize=8)
        ax.set_yticks(range(len(selected)), [f"{r['source'].title()} | {metric_names[r['metric']]}" for r in selected], fontsize=8)
        ax.invert_yaxis()
        ax.axvline(0, color='#999999', linestyle='--', linewidth=1)
        ax.set_title(title)
        ax.set_xlim(-110, 115)
        ax.set_xlabel('Ablated minus full (percentage points)')
        ax.grid(axis='x', color='#eeeeee')
    fig.suptitle('Ablation effects | controlled gate comparison and offline shared trajectories', fontsize=14)
    fig.text(.07, .01, 'Task gate: paired-layout 95% bootstrap CIs have zero width in this balanced corpus.\nMemory / timing: descriptive paired-episode effects only; no error bars or closed-loop counterfactual interpretation.', fontsize=9)
    fig.tight_layout(rect=(0, .1, 1, .94))
    save(fig, 'FIG_ABLATION_PREVIEW.png')

def trajectory_plot():
    data = rows('TRAJECTORY_TASK_POINTS.csv')
    fig, axes = plt.subplots(2, 2, figsize=(11, 7.4))
    for ax, source in zip(axes.flat, ('B_ABL_FULL', 'B_ABL_TRAJ_ONLY', 'C_DEV', 'C_HIST')):
        all_rows = [r for r in data if r['source'] == source]
        selected = [r for r in all_rows if r['truth_relation'] in (EQ, DV)]
        for truth, y in ((EQ, 0), (DV, 1)):
            group = [r for r in selected if r['truth_relation'] == truth]
            offsets = np.linspace(-.12, .12, len(group))
            ax.scatter([float(r['trajectory_distance']) for r in group], y + offsets,
                       color=COLORS[y+1], s=22, alpha=.7, edgecolors='none')
        threshold = float(all_rows[0]['frozen_threshold'])
        ax.axvline(threshold, color='#222222', linestyle='--', linewidth=1, label=f'Frozen threshold = {threshold:.3g} m')
        ax.set(yticks=[0, 1], yticklabels=['Equivalent', 'Divergent'], ylim=(-.4, 1.4), xlabel='Saved equal-time trajectory distance (m)', xlim=(-.02, None))
        ax.set_title(f'{source}: {len(selected)} defined / {len(all_rows)} saved records')
        ax.legend(loc='upper right', fontsize=8)
        ax.grid(axis='x', color='#eeeeee')
    fig.suptitle('Trajectory difference and independent task relation', fontsize=15)
    fig.text(.06, .01, 'Vertical offsets only separate overlapping points. C: 44 undefined labels remain in CSV but have no truth y-coordinate.\nB arms have different first frames and are not paired primary-policy observations. Thresholds are unchanged.', fontsize=9)
    fig.tight_layout(rect=(0, .09, 1, .96))
    save(fig, 'FIG_TRAJECTORY_TASK_PREVIEW.png')

def timing_plot():
    data = rows('TIMING_POINT_DATA.csv')
    fig, axes = plt.subplots(2, 2, figsize=(11, 8))
    for col, source in enumerate(('ORIGINAL', 'EXTENSION')):
        selected = [r for r in data if r['source'] == source and r['remaining_margin_s'] != '']
        for zoom in (0, 1):
            ax = axes[zoom, col]
            for x, memory in enumerate(('B1', 'B2')):
                group = [float(r['remaining_margin_s']) for r in selected if r['memory_condition'] == memory]
                visible = [v for v in group if not zoom or -1.5 <= v <= 1.5]
                if group and not zoom:
                    ax.boxplot([group], positions=[x], widths=.35, showfliers=False, medianprops={'color': '#111111'})
                if visible:
                    ax.scatter(x + np.linspace(-.14, .14, len(visible)), visible, s=22, color=COLORS[x+1], alpha=.65, zorder=3)
                ax.text(x, .98, f'n={len(visible)}' + (f'/{len(group)} visible' if zoom else ''),
                        ha='center', va='top', transform=ax.get_xaxis_transform(), fontsize=9)
            ax.axhline(0, color='#a24637', linestyle='--', linewidth=1.2)
            ax.set(xticks=[0, 1], xticklabels=['B1 current only', 'B2 valid memory'], xlim=(-.5, 1.5),
                   ylabel='Remaining protocol margin (s)')
            ax.set_title(f'{source.title()} | ' + ('boundary detail: [-1.5, 1.5] s' if zoom else 'all first-sufficient observations'))
            if zoom: ax.set_ylim(-1.5, 1.5)
            else: ax.margins(y=.18)
            ax.grid(axis='y', color='#eeeeee')
    fig.suptitle('Evidence timing | saved shared trajectories', fontsize=15)
    fig.text(.07, .01, 'Margin = commitment time - first sufficiency - frozen 1.20 s reserve. Dashed zero is the reserve boundary.\nNo sufficiency: Original B1 30/47, B2 18/47; Extension B1 23/23, B2 6/23.\nOne incomplete episode in each cohort remains censored in CSV (4 memory rows), never imputed to zero.', fontsize=9)
    fig.tight_layout(rect=(0, .11, 1, .95))
    save(fig, 'FIG_TIMING_PREVIEW.png')

if __name__ == '__main__':
    for plotter in (main_plot, ablation_plot, trajectory_plot, timing_plot):
        plotter()
    print('4 PNG previews generated from published CSVs; CPU / Agg only')
