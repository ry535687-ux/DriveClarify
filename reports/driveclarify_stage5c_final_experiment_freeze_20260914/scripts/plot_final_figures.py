"""Render four publication figures from copied frozen CSVs; no method execution."""
from pathlib import Path
import csv
import json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D

OUT = Path(__file__).resolve().parents[1]
DATA = OUT / 'plot_data'
FIG = OUT / 'figures'
BLUE, ORANGE, INK, GREY = '#2864A0', '#BE6B20', '#292929', '#787878'
POLICIES = ['NO_CLARIFICATION', 'DRIVECLARIFY', 'IMMEDIATE_QUERY']
COLORS = dict(NO_CLARIFICATION=GREY, DRIVECLARIFY=BLUE, IMMEDIATE_QUERY=ORANGE)
MARKERS = dict(NO_CLARIFICATION='s', DRIVECLARIFY='o', IMMEDIATE_QUERY='^')
NAMES = dict(NO_CLARIFICATION='No clarification', DRIVECLARIFY='DriveClarify', IMMEDIATE_QUERY='Immediate query')
RENDER_QA = []
plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 10, 'axes.labelsize': 10,
                     'axes.titlesize': 11, 'axes.titleweight': 'semibold', 'text.color': INK,
                     'axes.labelcolor': INK, 'xtick.color': INK, 'ytick.color': INK,
                     'axes.edgecolor': GREY, 'axes.linewidth': .8, 'lines.linewidth': 1.1,
                     'pdf.fonttype': 42, 'ps.fonttype': 42, 'svg.fonttype': 'none',
                     'svg.hashsalt': 'driveclarify-stage5c', 'savefig.facecolor': 'white'})


def rows(name):
    with (DATA / name).open(newline='') as handle:
        return list(csv.DictReader(handle))


def style(ax, grid='both'):
    ax.spines[['top', 'right']].set_visible(False)
    ax.grid(axis=grid, color='#E6E6E6', lw=.6)
    ax.set_axisbelow(True)


def save(fig, name):
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    exterior_text = []
    for item in fig.findobj(matplotlib.text.Text):
        if not item.get_visible() or not item.get_text():
            continue
        box = item.get_window_extent(renderer)
        if box.x0 < -.5 or box.y0 < -.5 or box.x1 > fig.bbox.width+.5 or box.y1 > fig.bbox.height+.5:
            exterior_text.append(item.get_text())
    counts = [sum(len(c.get_offsets()) for c in ax.collections
                  if isinstance(c, matplotlib.collections.PathCollection)) for ax in fig.axes]
    if exterior_text:
        raise RuntimeError(f'{name}: text exceeds figure canvas: {exterior_text}')
    RENDER_QA.append(dict(figure=name, scatter_points_by_panel=counts, text_outside_canvas=exterior_text))
    for ext in ['pdf', 'svg', 'png']:
        metadata = {'Date': None} if ext == 'svg' else {'CreationDate': None, 'ModDate': None} if ext == 'pdf' else None
        fig.savefig(FIG / f'{name}.{ext}', dpi=220, metadata=metadata)
    plt.close(fig)


def figure_a():
    data = rows('MAIN_LAYOUT_SUMMARY.csv')
    fig, axs = plt.subplots(2, 2, figsize=(10, 8), gridspec_kw={'height_ratios': [1, 1.4]})
    fig.subplots_adjust(left=.15, right=.97, bottom=.13, top=.85, hspace=.40, wspace=.32)
    fig.suptitle('A  Main policy trade-off', x=.08, ha='left', y=.98, fontsize=15, weight='semibold')
    fig.text(.08, .94, 'Defined subset: DEV 48 records / 8 layouts; HIST 84 records / 14 layouts', fontsize=10)
    handles = [Line2D([], [], color=COLORS[p], marker=MARKERS[p], ls='', label=NAMES[p]) for p in POLICIES]
    fig.legend(handles=handles, loc='upper center', bbox_to_anchor=(.55, .92), ncol=3, frameon=False)
    for j, split in enumerate(['DEV', 'HIST']):
        subset = [r for r in data if r['split'] == split]
        layouts = sorted({r['layout_id'] for r in subset})
        ax = axs[0, j]
        for policy in POLICIES:
            values = [r for r in subset if r['policy'] == policy]
            x, y = [100*float(r['query']) for r in values], [100*float(r['wrong_task']) for r in values]
            ax.scatter(x, y, c=COLORS[policy], marker=MARKERS[policy], s=56, zorder=3)
            offset = (7, -23) if policy == 'NO_CLARIFICATION' else (-8, 9) if policy == 'IMMEDIATE_QUERY' else (0, 9)
            ax.annotate(f'({x[0]:.2f}, {y[0]:.2f})\n{len(values)} layouts overlap', (x[0], y[0]),
                        xytext=offset, textcoords='offset points', fontsize=8,
                        ha='right' if policy == 'IMMEDIATE_QUERY' else 'left' if policy == 'NO_CLARIFICATION' else 'center')
        ax.set(title=split, xlabel='Query rate (%)', ylabel='Wrong-task commitment rate (%)',
               xlim=(-5, 106), ylim=(-3, 21), xticks=[0, 33.3333333333, 66.6666666667, 100])
        ax.set_xticklabels(['0', '33.33', '66.67', '100']); style(ax)
        ax = axs[1, j]
        for i, layout in enumerate(layouts):
            values = {r['policy']: r for r in subset if r['layout_id'] == layout}
            ax.plot([100*float(values[p]['query']) for p in POLICIES], [i]*3, c='#BEBEBE', zorder=1)
            for p in POLICIES:
                ax.scatter(100*float(values[p]['query']), i, c=COLORS[p], marker=MARKERS[p], s=25, zorder=3)
        ax.set(yticks=range(len(layouts)), yticklabels=[x.replace(':junction:', ' / J') for x in layouts],
               xlabel='Paired query rate per layout (%)', xlim=(-5, 106), xticks=[0, 33.3333333333, 100])
        ax.set_xticklabels(['0', '33.33', '100']); ax.tick_params(axis='y', labelsize=8)
        ax.invert_yaxis(); style(ax, 'x')
    fig.text(.08, .045, 'Identical layout rates follow from balanced controlled construction and repeated layout composition.\n'
             '44 undefined records are outside the rate axes: all abstain under DriveClarify; all-input coverage = 75%.', fontsize=9)
    save(fig, 'FIGURE_A_MAIN_POLICY_TRADEOFF')


def figure_b():
    data = rows('FINAL_ABLATION_EFFECTS_PP.csv')
    fig, axs = plt.subplots(1, 3, figsize=(12, 4.8), gridspec_kw={'width_ratios': [1, 1.45, 1]})
    fig.subplots_adjust(left=.08, right=.96, bottom=.26, top=.70, wspace=.68)
    fig.suptitle('B  Ablation effects', x=.04, ha='left', y=.97, fontsize=15, weight='semibold')
    fig.text(.04, .91, 'Ablated minus full, in percentage points; denominators are panel-specific', fontsize=10)
    selections = [[r for r in data if r['ablation'] == 'WITHOUT_TASK_GATE' and r['metric'] == 'query'],
                  [r for r in data if r['ablation'] == 'WITHOUT_MEMORY' and r['metric'] in ['evidence_sufficient', 'actionable_windows']],
                  [r for r in data if r['ablation'] == 'WITHOUT_TIMING' and r['metric'] == 'late']]
    titles = ['w/o Task-Consequence Gate\nQuery; 8 / 14 paired layouts',
              'w/o Temporal Memory\nSufficiency / window; 47 / 23 endpoints',
              'w/o Timing Constraint\nLate trigger; 47 / 23 endpoints']
    limits = [(-5, 110), (-105, 10), (-5, 85)]
    for j, (ax, subset) in enumerate(zip(axs, selections)):
        labels = []
        for i, r in enumerate(subset):
            val = float(r['effect_percentage_points'])
            label = r['source']
            if j == 1: label += '\n' + ('sufficient' if r['metric'] == 'evidence_sufficient' else 'window')
            labels.append(label)
            ax.plot([0, val], [i, i], c='#C0C0C0', lw=1.1)
            marker = 'o' if r['source'] in ['DEV', 'ORIGINAL'] else 's'
            ax.scatter(val, i, marker=marker, facecolor=BLUE if marker == 'o' else 'white', edgecolor=BLUE, s=52, zorder=3)
            if r['ci_low_percentage_points'] != '':
                ax.errorbar(val, i, xerr=[[val-float(r['ci_low_percentage_points'])], [float(r['ci_high_percentage_points'])-val]],
                            fmt='none', color=BLUE, capsize=3)
            desc = f'{val:+.2f}'
            if r['full_count']: desc += f" pp\n{r['full_count']} → {r['ablated_count']}"
            ax.annotate(desc, (val, i), xytext=(-6 if val<0 else 6, 0), textcoords='offset points',
                        ha='right' if val<0 else 'left', va='center', fontsize=8)
        ax.axvline(0, c=INK, lw=.8)
        ax.set(yticks=range(len(subset)), yticklabels=labels, ylim=(len(subset)-.5, -.5), xlim=limits[j],
               xlabel='Effect (percentage points)', title=titles[j]); ax.tick_params(axis='y', labelsize=8)
        style(ax, 'x')
    fig.text(.04, .09, 'Task-gate CI bounds are the archived degenerate paired-layout intervals; they do not imply population certainty.\n'
             'Memory and timing are descriptive offline analyses of shared trajectories; no temporal CI is estimated.\n'
             'Timing removal leaves timely opportunities unchanged (12 / 7). Two incomplete endpoints remain unscored.', fontsize=9)
    save(fig, 'FIGURE_B_ABLATION_FOREST')


def offsets(n):
    return np.linspace(-.2, .2, n) if n > 1 else np.zeros(n)


def figure_c():
    data = rows('TRAJECTORY_TASK_POINTS.csv')
    sources = ['C_DEV', 'C_HIST', 'B_ABL_FULL', 'B_ABL_TRAJ_ONLY']
    titles = ['DEV', 'HIST', 'Historical Full arm', 'Historical trajectory-only arm']
    relations = ['TASK_EQUIVALENT', 'TASK_DIVERGENT', 'UNDEFINED']
    fig, axs = plt.subplots(2, 2, figsize=(10, 7.4), sharey=True)
    fig.subplots_adjust(left=.10, right=.97, top=.85, bottom=.14, hspace=.46, wspace=.22)
    fig.suptitle('C  Trajectory distance and task relation', x=.07, ha='left', y=.98, fontsize=15, weight='semibold')
    fig.text(.07, .935, '203 saved observations: 159 defined + 44 undefined; distances in metres, source-specific frozen thresholds', fontsize=9)
    for ax, source, title in zip(axs.flat, sources, titles):
        subset = [r for r in data if r['source'] == source]
        threshold = float(subset[0]['frozen_threshold'])
        counts = []
        for i, relation in enumerate(relations):
            values = sorted([r for r in subset if (r['truth_relation'] or 'UNDEFINED') == relation], key=lambda r: (float(r['trajectory_distance']), r['record_id']))
            counts.append(len(values))
            ax.scatter(i+offsets(len(values)), [float(r['trajectory_distance']) for r in values],
                       s=21, marker='x' if relation == 'UNDEFINED' else 'o',
                       c=GREY if relation == 'UNDEFINED' else BLUE, alpha=.75, linewidths=.7)
        ax.axhline(threshold, ls='--', c=INK, lw=1)
        ax.text(.02, .92, f'Frozen threshold: {threshold:.3f} m', transform=ax.transAxes, ha='left', fontsize=8)
        ax.set(title=f'{title} (n={len(subset)})', xticks=range(3),
               xticklabels=[f'{name}\nn={n}' for name,n in zip(['EQ','DV','Undefined'],counts)],
               xlim=(-.45,2.45), ylim=(0,2), ylabel='Trajectory distance (m)')
        style(ax, 'y')
    fig.text(.07, .045, 'Horizontal offsets only separate marks; exact distances are unchanged. Dashed lines show frozen distance thresholds.\n'
             'Historical arms have different first observations and are not pooled. Futures show no added relation gain over valid task structure.', fontsize=9)
    save(fig, 'FIGURE_C_TRAJECTORY_TASK_RELATION')


def figure_d():
    data = rows('TIMING_POINT_DATA.csv')
    groups = [('ORIGINAL','B1'),('ORIGINAL','B2'),('EXTENSION','B1'),('EXTENSION','B2')]
    labels = ['Original\ncurrent','Original\nhistory','Extension\ncurrent','Extension\nhistory']
    fig, axs = plt.subplots(2,2,figsize=(11,7.8))
    fig.subplots_adjust(left=.10,right=.97,top=.84,bottom=.16,hspace=.43,wspace=.26)
    fig.suptitle('D  Evidence sufficiency and intervention margin', x=.07,ha='left',y=.98,fontsize=15,weight='semibold')
    fig.text(.07,.90,'Shared saved trajectories: 47 original + 23 extension complete endpoints; one incomplete endpoint per cohort\n'
             'Boxes: median / IQR, whiskers: 1.5 × IQR; strips show every available observation; missing values are not zero.', fontsize=9)
    for row, metric in enumerate(['first_evidence_sufficient_time','remaining_margin_s']):
        ylabel = 'First sufficient evidence (s)' if row==0 else 'Remaining intervention margin (s)'
        for col in range(2):
            ax = axs[row,col]
            lim = ((0,115) if row==0 else (-5,105)) if col==0 else ((0,8) if row==0 else (-1.25,2))
            ticks = []
            for i, (source,memory) in enumerate(groups):
                values = sorted(float(r[metric]) for r in data if r['source']==source and r['memory_condition']==memory
                                and r['endpoint_complete']=='True' and r[metric]!='')
                n = len(values)
                if n:
                    ax.boxplot([values],positions=[i],widths=.45,showfliers=False,patch_artist=True,
                               boxprops={'facecolor':'#E7EEF5','edgecolor':BLUE,'linewidth':.9},
                               medianprops={'color':INK,'linewidth':1.1},
                               whiskerprops={'color':GREY,'linewidth':.8}, capprops={'color':GREY,'linewidth':.8})
                    ax.scatter(i+offsets(n), values, marker='o' if memory=='B2' else 's',
                               facecolor=BLUE if memory=='B2' else 'white',edgecolor=BLUE,s=19,lw=.7,zorder=3)
                else:
                    ax.text(i,.5,'No sufficient\nevidence',transform=ax.get_xaxis_transform(),ha='center',va='center',fontsize=8)
                count_text = f'n={n}' if col==0 else f'shown {sum(lim[0]<=v<=lim[1] for v in values)}/{n}'
                ticks.append(labels[i]+'\n'+count_text)
            if row==1:ax.axhline(0,c=INK,ls='--',lw=1)
            ax.set(xlim=(-.55,3.55),ylim=lim,xticks=range(4),xticklabels=ticks,ylabel=ylabel,
                   title=('Full scale' if col==0 else f'Detail: {lim[0]:g} to {lim[1]:g} s'))
            ax.tick_params(axis='x',labelsize=8);style(ax,'y')
    fig.text(.07,.045,'Margin = reference commitment time − 1.20 s reserve − first sufficient evidence time; positive margin permits timely intervention.\n'
             'Zoom panels repeat the same data and explicitly count visible points. These offline reference times are not an online deadline guarantee.',fontsize=9)
    save(fig,'FIGURE_D_EVIDENCE_TIMING')


if __name__ == '__main__':
    FIG.mkdir(exist_ok=True)
    figure_a(); figure_b(); figure_c(); figure_d()
    (OUT / 'evidence/FIGURE_RENDER_RECEIPT.json').write_text(json.dumps(RENDER_QA, indent=2) + '\n')
    print(json.dumps({'figure_files': sorted(p.name for p in FIG.iterdir()),
                      'renderer': 'matplotlib ' + matplotlib.__version__, 'numpy': np.__version__}))
