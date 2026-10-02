"""Final C polish: full-scale source facets and fixed threshold-neighborhood insets.

All 203 Stage5C observations, labels, thresholds and mismatch counts are frozen.
The same exact x/y point arrays are reused in every main panel and its inset.
The only categorical x offsets are v2's stable deterministic offsets. Fixed
inset bounds are 0–0.35 m for DEV/HIST and 0–0.75 m for both historical arms.
No new inferential statistic or numerical transformation of distance is used.

Seaborn stripplot sizes are diameters in points, not areas: v2's actual values
were 2.0/2.3/2.7 points. Scale those by 0.88 to satisfy the requested shrinkage;
using the suggested 12–24 pt² ranges would enlarge the actual existing markers.
"""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import seaborn as sns
import matplotlib.pyplot as plt
from matplotlib.collections import PathCollection
from matplotlib.lines import Line2D
from paper_style import apply_style, clean_axes, panel_label, top_legend, PALETTE, MARKERS
from figure_io import read_csv, verify_protected, export

ORDER = ['EQ', 'DV', 'Undefined']
SIZE_SCALE = .88
KINDS = [('Other defined', 'other_defined', 'slate', False, 2.0 * SIZE_SCALE, .65),
         ('Undefined', 'undefined', 'undefined', False, 2.3 * SIZE_SCALE, .55),
         ('Close-DV', 'close_dv', 'orange', True, 2.7 * SIZE_SCALE, 1),
         ('Far-EQ', 'far_eq', 'orange', True, 2.7 * SIZE_SCALE, 1)]
SPECS = [('C_DEV', 'DEV', 64, 14, 1, [32, 16, 16], .35),
         ('C_HIST', 'HIST', 112, 21, 14, [56, 28, 28], .35),
         ('B_ABL_FULL', 'Historical Full', 13, 6, 3, [5, 8, 0], .75),
         ('B_ABL_TRAJ_ONLY', 'Trajectory-only', 14, 5, 4, [6, 8, 0], .75)]
INSET_BOUNDS = [.595, .33, .35, .31]


def display_plan(frame):
    """Build display coordinates once; retain v2's exact categorical offsets."""
    plan = {}
    for kind, *_ in KINDS:
        for index, category in enumerate(ORDER):
            rows = frame.loc[(frame.kind == kind) & (frame.relation == category)]
            y = rows.trajectory_distance.to_numpy(float)
            x = np.full(len(y), index, dtype=float)
            if len(y) > 1:
                x += np.linspace(-.10, .10, len(y))
            plan[(kind, category)] = np.column_stack([x, y])
    return plan


def draw_points(ax, frame, plan, *, inset=False):
    for kind, marker, color, hollow, size, alpha in KINDS:
        subset = frame.loc[frame.kind == kind]
        if subset.empty:
            continue
        first = len(ax.collections)
        sns.stripplot(data=subset, x='relation', y='trajectory_distance', order=ORDER,
                      jitter=False, size=size, marker=MARKERS[marker], color=PALETTE[color],
                      edgecolor=PALETTE[color], linewidth=.45, alpha=alpha, ax=ax, zorder=4)
        collections = ax.collections[first:]
        assert len(collections) == 3
        for category, collection in zip(ORDER, collections):
            points = np.asarray(collection.get_offsets(), dtype=float)
            wanted = plan[(kind, category)]
            assert np.array_equal(points[:, 1], wanted[:, 1])
            collection.set_offsets(wanted.copy())
            # At the full-scale zero baseline preserve the full marker edge.
            # Insets clip only to their explicitly declared display window.
            collection.set_clip_on(inset)
            if hollow:
                collection.set_facecolor('white')


def axes_style(ax, *, inset=False):
    clean_axes(ax, grid=None, inset=inset)
    ax.grid(axis='y', color=PALETTE['ink'], linewidth=.4, alpha=.12 if inset else .15)
    ax.tick_params(axis='both', left=True, bottom=True, direction='out',
                   width=.5 if inset else .55, length=1.8 if inset else 2.3,
                   pad=1 if inset else 2, labelsize=5.2 if inset else 6.7)
    for name in ['left', 'bottom']:
        ax.spines[name].set_linewidth(.4 if inset else .6)
        ax.spines[name].set_alpha(.8 if inset else 1)
    ax.set(xlim=(-.4, 2.6), xlabel='', ylabel='')


def threshold_line(ax, threshold):
    return ax.axhline(threshold, color=PALETTE['ink'], linewidth=.65,
                     alpha=.75, linestyle=(0, (3, 2)), zorder=3)


def render():
    verify_protected()
    font = apply_style()
    frame = read_csv('TRAJECTORY_TASK_POINTS.csv').sort_values('record_id', kind='stable')
    assert len(frame) == 203 and frame.truth_relation.eq('').sum() == 44
    frame['relation'] = frame.truth_relation.map({'TASK_EQUIVALENT': 'EQ', 'TASK_DIVERGENT': 'DV', '': 'Undefined'})
    assert frame.relation.notna().all()
    frame['kind'] = 'Other defined'
    frame.loc[frame.truth_relation == '', 'kind'] = 'Undefined'
    frame.loc[(frame.truth_relation == 'TASK_DIVERGENT') & (frame.trajectory_relation == 'TASK_EQUIVALENT'), 'kind'] = 'Close-DV'
    frame.loc[(frame.truth_relation == 'TASK_EQUIVALENT') & (frame.trajectory_relation == 'TASK_DIVERGENT'), 'kind'] = 'Far-EQ'
    fig = plt.figure(figsize=(6.8, 4.3))
    grid = fig.add_gridspec(2, 2, left=.073, right=.985, bottom=.09, top=.905,
                            hspace=.28, wspace=.19)
    axes = []
    for index in range(4):
        axes.append(fig.add_subplot(grid[index // 2, index % 2],
                                   sharex=axes[0] if axes else None,
                                   sharey=axes[0] if axes else None))
    handles = []
    for kind in ['Close-DV', 'Far-EQ', 'Other defined', 'Undefined']:
        _, marker, color, hollow, size, alpha = next(spec for spec in KINDS if spec[0] == kind)
        handles.append(Line2D([], [], ls='', marker=MARKERS[marker], color=PALETTE[color],
                       markerfacecolor='white' if hollow else PALETTE[color],
                       markersize=size, markeredgewidth=.45, alpha=alpha, label=kind))
    top_legend(fig, handles)
    fig.supylabel('Trajectory distance (m)', x=.018, fontsize=7.8)
    checks = []
    pairs = []
    for index, (ax, spec) in enumerate(zip(axes, SPECS)):
        source, title, n, close_dv, far_eq, category_ns, zoom_top = spec
        subset = frame.loc[frame.source == source]
        assert len(subset) == n
        assert (subset.kind == 'Close-DV').sum() == close_dv
        assert (subset.kind == 'Far-EQ').sum() == far_eq
        assert [int(subset.relation.eq(category).sum()) for category in ORDER] == category_ns
        assert subset.frozen_threshold.nunique() == 1
        threshold = float(subset.frozen_threshold.iloc[0])
        assert np.isclose(threshold, .1 if source.startswith('C_') else .27119792945561905)
        plan = display_plan(subset)
        draw_points(ax, subset, plan)
        axes_style(ax)
        threshold_line(ax, threshold)
        text = ax.text(2.56, threshold + .025,
                       'τ=0.10 m' if source.startswith('C_') else 'τ=0.271 m',
                       fontsize=6, ha='right', va='bottom', color=PALETTE['secondary'])
        counts_text = ax.text(.975, .905, f'close-DV: {close_dv}\nfar-EQ: {far_eq}',
                             transform=ax.transAxes, fontsize=5.8, ha='right', va='top',
                             linespacing=1.15, color='#727272')
        ax.set(ylim=(0, 2), yticks=[0, .5, 1, 1.5, 2],
               yticklabels=['0', '0.5', '1.0', '1.5', '2.0'],
               xticks=range(3), xticklabels=ORDER)
        ax.tick_params(labelleft=index % 2 == 0, left=True, labelbottom=True)
        for x, count in enumerate(category_ns):
            ax.text(x, -.10, f'n={count}', transform=ax.get_xaxis_transform(),
                    ha='center', va='top', fontsize=5.7, color=PALETTE['secondary'])
        panel_label(ax, chr(97 + index), title, n=n)
        assert subset.trajectory_distance.between(0, 2).all()

        detail = ax.inset_axes(INSET_BOUNDS)
        draw_points(detail, subset, plan, inset=True)
        axes_style(detail, inset=True)
        threshold_line(detail, threshold)
        ticks = [0, .1, .2, .35] if zoom_top == .35 else [0, .25, .5, .75]
        detail.set(ylim=(0, zoom_top), yticks=ticks,
                   yticklabels=['0', '0.10', '0.20', '0.35'] if zoom_top == .35 else ['0', '0.25', '0.50', '0.75'],
                   xticks=range(3), xticklabels=['EQ', 'DV', 'U'])
        detail.set_title('zoom', fontsize=5.3, fontweight='normal', pad=1.8)
        main_points = [c for c in ax.collections if isinstance(c, PathCollection)]
        inset_points = [c for c in detail.collections if isinstance(c, PathCollection)]
        assert len(main_points) == len(inset_points)
        for main_collection, inset_collection in zip(main_points, inset_points):
            assert np.array_equal(main_collection.get_offsets(), inset_collection.get_offsets())
            assert np.array_equal(main_collection.get_sizes(), inset_collection.get_sizes())
        pairs.append((ax, detail, text, counts_text))
        checks.append(dict(source=source, n=n, category_counts=category_ns,
                           close_DV=close_dv, far_EQ=far_eq, frozen_threshold=threshold,
                           full_scale_ylim=[0, 2], inset_ylim=[0, zoom_top],
                           main_and_inset_point_arrays_identical=True,
                           inset_relative_width=INSET_BOUNDS[2], inset_relative_height=INSET_BOUNDS[3],
                           numeric_y_values_unchanged=True))
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    for main, inset, label, count in pairs:
        bbox = inset.get_tightbbox(renderer)
        for collection in main.collections:
            if isinstance(collection, PathCollection):
                for point in main.transData.transform(np.asarray(collection.get_offsets(), dtype=float)):
                    assert not bbox.padded(2).contains(*point), 'Inset overlaps a main observation'
        assert not bbox.overlaps(label.get_window_extent(renderer)), 'Inset overlaps threshold label'
        assert not bbox.overlaps(count.get_window_extent(renderer)), 'Inset overlaps mismatch counts'
    export(fig, 'FIG_C_TRAJECTORY_TASK_FINAL', ['plot_data/TRAJECTORY_TASK_POINTS.csv'],
           dict(sources=checks, total_observations=203, undefined=44,
                full_scale_all_outliers_visible=True, random_jitter=False,
                horizontal_offsets_identical_to_v2=True, marker_diameter_scale_vs_v2=SIZE_SCALE,
                maximum_highlight_to_ordinary_marker_area_ratio=(2.7 / 2.0) ** 2,
                inset_does_not_obscure_observations_or_labels=True,
                main_panels=4, fixed_zoom_insets=4), font)
    plt.close(fig)


if __name__ == '__main__':
    render()
