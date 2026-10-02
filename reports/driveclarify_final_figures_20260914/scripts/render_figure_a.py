"""Final visual polish of accepted Figure A v2; frozen rates only."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
import seaborn as sns
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from paper_style import apply_style, clean_axes, panel_label, top_legend, PALETTE, MARKERS as SHAPES
from figure_io import SOURCE, verify_protected, export
POLICIES = ['NO_CLARIFICATION', 'IMMEDIATE_QUERY', 'DRIVECLARIFY']
NAMES = ['No Clarification', 'Immediate Query', 'DriveClarify']
COLORS = [PALETTE[k] for k in ['slate', 'orange', 'teal']]
MARKERS = [SHAPES[k] for k in ['no_clarification', 'immediate_query', 'driveclarify']]
METRICS = ['correct_decision', 'query', 'wrong_task']
METRIC_NAMES = ['Correct Decision', 'Query Rate', 'Wrong Commitment']
HATCHES = ['', '///', 'xx']
INK = PALETTE['ink']
SIZE = (6.8, 2.55)


def frozen_values():
    frame = pd.read_csv(SOURCE / 'plot_data/MAIN_LAYOUT_SUMMARY.csv')
    assert len(frame) == 66
    values = []
    expected = [[83.33333333333334, 0, 16.666666666666668],
                [100, 100, 0], [100, 33.33333333333333, 0]]
    for policy, target in zip(POLICIES, expected):
        rows = frame.loc[frame.policy == policy]
        assert rows.groupby('split').size().to_dict() == {'DEV': 8, 'HIST': 14}
        assert all(rows[metric].nunique() == 1 for metric in METRICS)
        common = rows[METRICS].iloc[0].to_numpy(float) * 100
        assert np.allclose(common, target, rtol=0, atol=1e-12)
        values.append(common)
    return np.asarray(values)


def render():
    verify_protected()
    values = frozen_values()
    family = apply_style()
    fig, (left, right) = plt.subplots(1, 2, figsize=SIZE,
                                     gridspec_kw={'width_ratios': [1.08, .92]})
    fig.subplots_adjust(left=.062, right=.985, bottom=.16, top=.835, wspace=.27)
    for ax in [left, right]:
        clean_axes(ax)

    centers = np.arange(3)
    width = .17
    for j, hatch in enumerate(HATCHES):
        bars = left.bar(centers + (j - 1) * .22, values[:, j], width=width,
                        color=COLORS, edgecolor=INK, linewidth=.35, alpha=.88,
                        hatch=hatch, zorder=3)
        assert np.allclose([bar.get_height() for bar in bars], values[:, j])
    left.set(ylim=(0, 105), xlim=(-.49, 2.47), yticks=[0, 25, 50, 75, 100],
             ylabel='Rate (%)', xticks=centers,
             xticklabels=['No Clarification', 'Immediate Query', 'DriveClarify'])
    for i, j, label in [(0, 2, '16.7'), (2, 1, '33.3'), (2, 0, '100')]:
        left.annotate(label, (i + (j - 1) * .22, values[i, j]),
                      xytext=(0, 2), textcoords='offset points',
                      ha='center', va='bottom', fontsize=6.5)
    panel_label(left, 'a', 'Policy comparison')

    for i, (name, color, marker) in enumerate(zip(NAMES, COLORS, MARKERS)):
        q, w = values[i, 1], values[i, 2]
        sns.scatterplot(x=[q], y=[w], color=color, marker=marker, s=17,
                        edgecolor=INK, linewidth=.4, ax=right, legend=False, zorder=4)
        offset, alignment = [((6, 0), 'left'), ((-6, 7), 'right'), ((6, 7), 'left')][i]
        right.annotate(name, (q, w), xytext=offset, textcoords='offset points',
                       ha=alignment, va='center', fontsize=6.7)
    right.set(xlim=(-4, 105), ylim=(-1, 18.8), xticks=[0, 25, 50, 75, 100],
              yticks=[0, 5, 10, 15], xlabel='Query Rate (%)',
              ylabel='Wrong-task Commitment (%)')
    right.xaxis.labelpad = 3
    right.yaxis.labelpad = 4
    panel_label(right, 'b', 'Query–commitment trade-off')
    # Two compact, direct effect labels. No connector implies a frontier or
    # introduces an unmeasured intermediate operating point.
    right.text(66.7, 2.65, '−66.7 pp queries', ha='center', va='bottom',
               fontsize=6, color=PALETTE['secondary'])
    right.text(3.4, 14.7, '−16.7 pp wrong commitment', ha='left', va='top',
               fontsize=6, color=PALETTE['secondary'])

    handles = [Patch(facecolor='.83', edgecolor=INK, linewidth=.35, hatch=h,
                     label=name) for h, name in zip(HATCHES, METRIC_NAMES)]
    top_legend(fig, handles)
    export(fig, 'FIG_A_MAIN_FINAL', ['plot_data/MAIN_LAYOUT_SUMMARY.csv'],
           dict(frozen_policy_percentages=values.tolist(), main_panels=2,
                random_jitter=False, coordinates_are_frozen_rates=True), family)
    plt.close(fig)


if __name__ == '__main__':
    render()
