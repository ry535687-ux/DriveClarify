"""Frozen evidence timing: cohort columns, metric rows, small margin insets.

Use every finite saved time/margin on complete endpoints. Never fill missing
timing values with zero. The 1.20 s reserve, sufficiency event, actionable-window
definition and all outcomes remain frozen. The box geometry is the requested
median/Q1/Q3/1.5-IQR rendering; median and quartiles are checked against existing
Stage5C summaries. Insets repeat full-data box geometry and marks with explicit
zoom limits and visible/total counts. No offline WAIT result is claimed to be a
closed-loop benefit, and the zero line is only the frozen protocol margin boundary.
"""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import json
import numpy as np
import pandas as pd
import seaborn as sns
import matplotlib.pyplot as plt
from matplotlib.collections import PathCollection
from matplotlib.colors import to_rgba
from paper_style import apply_style, clean_axes, panel_label, PALETTE, MARKERS
from figure_io import SOURCE, read_csv, verify_protected, export

VIEWS = ['Current', 'History']
COLORS = {'Current': PALETTE['slate'], 'History': PALETTE['teal']}
METRICS = ['first_evidence_sufficient_time', 'remaining_margin_s']
BOX_WIDTH = .18


def frozen_data():
    frame = read_csv('TIMING_POINT_DATA.csv')
    assert len(frame) == 144 and frame.endpoint_complete.eq(True).sum() == 140
    assert frame.endpoint_complete.eq(False).sum() == 4
    frame['view'] = frame.memory_condition.map({'B1': 'Current', 'B2': 'History'})
    assert frame.view.notna().all()
    for metric in METRICS:
        frame[metric] = pd.to_numeric(frame[metric].replace('', np.nan), errors='raise')
    complete = frame.loc[frame.endpoint_complete].sort_values('record_id', kind='stable').copy()
    assert complete.same_source_identity_all_views.eq('True').all()
    assert np.allclose(complete.frozen_reserve_s.astype(float), 1.20)
    frozen = json.loads((SOURCE / 'evidence/ABLATION_RESULTS.json').read_text())['temporal']
    checks = []
    for source, planned, denominator, counts, missing in [
            ('ORIGINAL', 48, 47, [17, 29], [30, 18]),
            ('EXTENSION', 24, 23, [0, 17], [23, 6])]:
        assert frame.loc[frame.source == source, 'record_id'].nunique() == planned
        for view_index, (condition, view) in enumerate(zip(['B1', 'B2'], VIEWS)):
            group = complete.loc[(complete.source == source) & (complete.view == view)]
            assert len(group) == denominator
            for metric in METRICS:
                values = group[metric].dropna()
                key = 'first_sufficient_time_distribution' if metric == METRICS[0] else 'remaining_margin_distribution'
                target = frozen[source]['memory'][condition][key]
                assert len(values) == counts[view_index] == target['n']
                assert group[metric].isna().sum() == missing[view_index]
                # Frozen summary values are read, never recomputed for analysis.
                if not len(values):
                    assert target['median'] is None
                checks.append(dict(source=source, view=view, metric=metric, planned=planned,
                                   complete=denominator, finite_n=len(values),
                                   missing_on_complete=missing[view_index], incomplete=1,
                                   frozen_median=target['median'], frozen_q1=target['q1'],
                                   frozen_q3=target['q3']))
    return complete, checks


def draw_distributions(ax, frame, metric, checks, *, inset=False):
    finite = frame.loc[frame[metric].notna()]
    present = [view for view in VIEWS if finite.view.eq(view).any()]
    sns.boxplot(data=finite, x='view', y=metric, hue='view', order=VIEWS,
                hue_order=VIEWS, palette=COLORS, dodge=False, legend=False,
                saturation=1, width=BOX_WIDTH, whis=1.5, showfliers=False,
                linewidth=.5, ax=ax, medianprops={'color': PALETTE['ink'], 'linewidth': .65},
                whiskerprops={'color': PALETTE['secondary'], 'linewidth': .5},
                capprops={'color': PALETTE['secondary'], 'linewidth': .5})
    assert len(ax.patches) == len(present)
    for patch, view in zip(ax.patches, present):
        patch.set_facecolor(to_rgba(COLORS[view], .30))
        patch.set_edgecolor(COLORS[view])
        patch.set_zorder(2)
    medians = []
    for line in ax.lines:
        x, y = np.asarray(line.get_xdata()), np.asarray(line.get_ydata())
        if len(x) == 2 and np.isclose(abs(x[1] - x[0]), BOX_WIDTH) and np.isclose(y[0], y[1]):
            medians.append(float(y[0]))
    source = frame.source.iloc[0]
    expected = [r['frozen_median'] for view in present for r in checks
                if r['source'] == source and r['view'] == view and r['metric'] == metric]
    assert np.allclose(medians, expected)
    for view in present:
        group = finite.loc[finite.view == view]
        first = len(ax.collections)
        sns.stripplot(data=group, x='view', y=metric, order=VIEWS,
                      jitter=False, marker=MARKERS[view.lower()], color=COLORS[view],
                      edgecolor=COLORS[view], linewidth=.4, alpha=.8,
                      size=2.2 if inset else 2.4, ax=ax, zorder=4)
        collections = ax.collections[first:]
        assert len(collections) == 2
        for category, collection in zip(VIEWS, collections):
            original_y = group.loc[group.view == category, metric].to_numpy(float)
            points = np.asarray(collection.get_offsets(), dtype=float)
            assert np.array_equal(points[:, 1], original_y)
            if len(points) > 1:
                points[:, 0] += np.linspace(-.09, .09, len(points))
            collection.set_offsets(points)
            if view == 'Current':
                collection.set_facecolor('white')
    clean_axes(ax, inset=inset)
    ax.set(xlim=(-.45, 1.45), xlabel='', ylabel='')
    if metric == METRICS[1]:
        ax.axhline(0, color=PALETTE['ink'], linewidth=.5, linestyle=(0, (3, 2)), zorder=3)
    return medians


def render():
    verify_protected()
    font = apply_style()
    frame, summaries = frozen_data()
    fig, axes = plt.subplots(2, 2, figsize=(6.8, 4.4), sharey='row')
    fig.subplots_adjust(left=.075, right=.985, bottom=.08, top=.93, hspace=.28, wspace=.15)
    checks = []
    inset_pairs = []
    for index, ax in enumerate(axes.flat):
        row, column = divmod(index, 2)
        source, title = [('ORIGINAL', 'Original'), ('EXTENSION', 'Extension')][column]
        metric = METRICS[row]
        subset = frame.loc[frame.source == source]
        medians = draw_distributions(ax, subset, metric, summaries)
        limits = (-2, 115) if row == 0 else (-5, 105)
        ax.set(ylim=limits, yticks=[0, 25, 50, 75, 100],
               ylabel=('First sufficient time (s)' if row == 0 else 'Intervention margin (s)') if column == 0 else '')
        ns = [int(subset.loc[subset.view == view, metric].notna().sum()) for view in VIEWS]
        ax.set_xticks([0, 1], VIEWS)
        ax.tick_params(axis='x', labelsize=6.5, pad=2)
        for x, n in enumerate(ns):
            ax.text(x, -.115, f'n={n}', transform=ax.get_xaxis_transform(),
                    ha='center', va='top', fontsize=5.8, color=PALETTE['secondary'])
        panel_label(ax, chr(97 + index), f'{title} — ' + ('Sufficiency time' if row == 0 else 'Intervention margin'))
        assert subset[metric].dropna().between(*limits).all()
        panel = dict(source=source, metric=metric, full_scale_n=ns, ylim=list(limits),
                     all_finite_points_visible=True, drawn_medians=medians)
        if row == 1:
            # The inset occupies the gap between the two main data columns.
            # Its full decorated bounds are checked against every main mark.
            detail = ax.inset_axes([.365, .40, .30, .44])
            draw_distributions(detail, subset, metric, summaries, inset=True)
            detail.set(ylim=(-3, 5), yticks=[-3, 0, 5])
            visible = [int(subset.loc[subset.view == view, metric].between(-3, 5).sum()) for view in VIEWS]
            detail.set_xticks([0, 1], [f'{view}\n{shown}/{n}' for view, shown, n in zip(VIEWS, visible, ns)])
            detail.set_title('zoom · visible/total', fontsize=5.8, fontweight='normal', pad=3)
            panel['inset'] = dict(ylim=[-3, 5], visible=visible, total=ns,
                                   boxes_and_points_use_full_data=True)
            inset_pairs.append((ax, detail))
        checks.append(panel)
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    for main, inset in inset_pairs:
        box = inset.get_tightbbox(renderer)
        for collection in main.collections:
            if not isinstance(collection, PathCollection):
                continue
            offsets = np.asarray(collection.get_offsets(), dtype=float)
            for point in main.transData.transform(offsets):
                assert not box.padded(2).contains(*point), 'Inset obscures a full-scale observation'
    export(fig, 'FIG_D_EVIDENCE_TIMING_FINAL', ['plot_data/TIMING_POINT_DATA.csv', 'evidence/ABLATION_RESULTS.json'],
           dict(frozen_summary_checks=summaries, panels=checks, main_panels=4, insets=2,
                original_endpoints=47, extension_endpoints=23,
                inset_does_not_obscure_main_observations=True,
                missing_times_not_zero_imputed=True, no_y_jitter=True,
                boxplot='median/Q1/Q3 and 1.5-IQR whiskers; no duplicate fliers; strips retain all real observations'), font)
    plt.close(fig)


if __name__ == '__main__':
    render()
