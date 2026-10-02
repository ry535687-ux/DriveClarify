"""Final renderer of the accepted, frozen paired transition aggregates.

No pairing, state derivation, contingency calculation, or scientific analysis is
performed here. Empty-row percentages stay undefined in the source and labels.
"""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import json
import numpy as np
import seaborn as sns
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap, Normalize
from matplotlib.patches import Rectangle
from paper_style import apply_style, clean_axes, panel_label, PALETTE
from figure_io import ROOT, verify_protected, export

STATES = ['INSUFFICIENT', 'SUFFICIENT_LATE', 'ACTIONABLE']
LABELS = ['Insufficient', 'Sufficient\nbut late', 'Actionable']
COHORTS = [('ORIGINAL', 'Original', 48, 47, [17, 6, 29, 12]),
           ('EXTENSION', 'Extension', 24, 23, [0, 0, 17, 7])]
PAIR_KEYS = ['source', 'record_id', 'shared_trace_digest']

def render(metadata):
    font = apply_style()
    fig, axes = plt.subplots(1, 2, figsize=(6.8, 2.7))
    fig.subplots_adjust(left=.14, right=.944, top=.875, bottom=.30, wspace=.16)
    cmap = LinearSegmentedColormap.from_list('memory_row_fraction', ['#F7F9F8', PALETTE['teal']])
    norm = Normalize(vmin=0, vmax=100)
    for index, (ax, title) in enumerate(zip(axes, ['Original', 'Extension'])):
        info = metadata['cohorts'][title]
        counts = np.array(info['raw_counts'])
        pct = np.array([[np.nan if value is None else value for value in row] for row in info['row_percentages']])
        # Undefined percentages receive the same near-white background as zero,
        # but are explicitly distinguished by an em dash in every cell label.
        sns.heatmap(np.nan_to_num(pct, nan=0), ax=ax, cmap=cmap, norm=norm,
                    cbar=False, annot=False, linewidths=.35, linecolor='white',
                    xticklabels=LABELS, yticklabels=LABELS)
        assert np.array_equal(np.asarray(ax.collections[0].get_array()), np.nan_to_num(pct, nan=0))
        assert ax.collections[0].norm.vmin == 0 and ax.collections[0].norm.vmax == 100
        for row in range(3):
            for col in range(3):
                percentage = pct[row, col]
                dark = np.isfinite(percentage) and percentage >= 75
                color = 'white' if dark else PALETTE['ink']
                nonzero = counts[row, col] > 0
                ax.text(col + .5, row + .42, str(counts[row, col]), ha='center', va='center',
                        fontsize=7, fontweight='bold' if nonzero else 'normal',
                        color=color if nonzero else PALETTE['secondary'])
                percent_text = '(—)' if np.isnan(percentage) else f'({percentage:.0f}%)'
                ax.text(col + .5, row + .70, percent_text, ha='center', va='center',
                        fontsize=5.8, color='#E4EEEC' if dark else PALETTE['secondary'])
                if col > row and counts[row, col] > 0:
                    ax.add_patch(Rectangle((col + .025, row + .025), .95, .95,
                                 fill=False, edgecolor=PALETTE['teal'], linewidth=.35, alpha=.7))
        ax.set(xlabel='', ylabel='')
        ax.tick_params(axis='both', labelsize=6.5, length=0, pad=3)
        ax.set_xticklabels(LABELS, rotation=0)
        ax.set_yticklabels(LABELS if index == 0 else ['', '', ''], rotation=0)
        clean_axes(ax, grid=None)
        for spine in ax.spines.values():
            spine.set_visible(False)
        panel_label(ax, chr(97 + index), title)
        ax.text(1, 1.045, f"n={info['complete']}", transform=ax.transAxes,
                ha='right', va='bottom', fontsize=6.2, color=PALETTE['secondary'])
    fig.text(.5, .975, 'Current → Retained history', ha='center', va='top', fontsize=6.2)
    fig.text(.018, .60, 'Current observation', ha='center', va='center', rotation=90, fontsize=7.8)
    fig.text(.55, .20, 'Retained history', ha='center', va='center', fontsize=7.8)
    colorbar_ax = fig.add_axes([.965, .32, .006, .52])
    colorbar = fig.colorbar(plt.cm.ScalarMappable(norm=norm, cmap=cmap), cax=colorbar_ax, ticks=[0, 50, 100])
    colorbar.outline.set_visible(False)
    colorbar.solids.set_rasterized(False)
    colorbar.ax.tick_params(labelsize=6, width=.5, length=2, pad=2)
    colorbar_ax.set_title('Row %', fontsize=6, fontweight='normal', pad=4)
    fig.add_artist(plt.Line2D([.035, .95], [.150, .150], transform=fig.transFigure,
                             color=PALETTE['ink'], linewidth=.4, alpha=.15))
    fig.text(.04, .12, 'Episode-level state change', fontsize=6, va='center')
    for x, title in zip([.47, .67, .86], ['Improved', 'Unchanged', 'Regressed']):
        fig.text(x, .12, title, ha='center', va='center', fontsize=6)
    for y, title in zip([.078, .039], ['Original', 'Extension']):
        info = metadata['cohorts'][title]
        fig.text(.28, y, title, fontsize=5.8, ha='right', va='center')
        for x, key in zip([.47, .67, .86], ['improved', 'unchanged', 'regressed']):
            value = info[key]
            fig.text(x, y, f"{value['count']} ({value['percentage']:.0f}%)",
                     ha='center', va='center', fontsize=5.8)
    export(fig, 'FIG_B_MEMORY_FINAL',
           [MATRIX_PATH, 'plot_data/TIMING_POINT_DATA.csv', 'evidence/ABLATION_RESULTS.json'],
           dict(real_identity_pairs=70, cohort_pairs=[47, 23],
                all_frozen_marginals_match=True, matrix_cells=18,
                drawn_counts=[metadata['cohorts'][title]['raw_counts'] for title in ['Original', 'Extension']],
                shared_color_normalization=[0, 100], empty_rows_not_reported_as_zero_percent=True,
                source_definitions_unchanged=True), font)
    plt.close(fig)


MATRIX_PATH = 'reports/driveclarify_stage5d_figure_b_memory_transitions_v2_20260914/FIGURE_B_TRANSITION_MATRIX.json'

if __name__ == '__main__':
    verify_protected()
    metadata = json.loads((ROOT / MATRIX_PATH).read_text())
    assert metadata['state_order'] == STATES
    render(metadata)
