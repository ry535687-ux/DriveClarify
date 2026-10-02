"""Read-only frozen inputs, display-integrity checks, and final export receipts."""
from pathlib import Path
import hashlib
import inspect
import json
import sys

OUT = Path(__file__).resolve().parents[1]
ROOT = OUT.parents[1]
sys.path.insert(0, str(OUT))
from paper_style import SAVEFIG_SETTINGS
import matplotlib
from matplotlib.collections import PathCollection
import numpy as np
import pandas as pd
import seaborn as sns

SOURCE = ROOT / 'reports/driveclarify_stage5c_final_experiment_freeze_20260914'
MANIFEST_SHA = 'cb7549ceb54161d1b01222e01658b0cb49d6c04d2947b1b965f85a9d998e4ef9'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_protected():
    pin = json.loads((OUT / 'qa/PROTECTED_INPUTS.json').read_text())
    assert sha(SOURCE / 'ARTIFACT_MANIFEST.json') == MANIFEST_SHA
    for item in pin['files']:
        path = ROOT / item['path']
        assert path.stat().st_size == item['bytes'] and sha(path) == item['sha256'], item['path']
    current = {p.relative_to(ROOT).as_posix() for name in pin['directories']
               for p in (ROOT / 'reports' / name).rglob('*')
               if p.is_file() and '__pycache__' not in p.parts}
    assert current == {item['path'] for item in pin['files']}
    # Verify the authoritative packet's own pre-existing manifest as well.
    for item in json.loads((SOURCE / 'ARTIFACT_MANIFEST.json').read_text())['members']:
        assert sha(SOURCE / item['path']) == item['sha256'], item['path']
    return len(pin['files'])


def read_csv(name):
    return pd.read_csv(SOURCE / 'plot_data' / name, keep_default_na=False)


def export(fig, stem, inputs, checks, font):
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    escaped, font_families = [], set()
    for item in fig.findobj(match=matplotlib.text.Text):
        if item.get_visible() and item.get_text():
            bbox = item.get_window_extent(renderer)
            if (bbox.x0 < -.5 or bbox.y0 < -.5 or bbox.x1 > fig.bbox.width + .5
                    or bbox.y1 > fig.bbox.height + .5):
                escaped.append(item.get_text())
            font_families.add(item.get_fontproperties().get_name())
    assert not escaped, escaped
    assert font_families == {font}, font_families
    for suffix in ['pdf', 'svg', 'png']:
        metadata = {'Date': None} if suffix == 'svg' else (
            {'CreationDate': None, 'ModDate': None} if suffix == 'pdf' else None)
        fig.savefig(OUT / f'{stem}.{suffix}', bbox_extra_artists=[fig.patch],
                    metadata=metadata, **SAVEFIG_SETTINGS)
    axes = list(fig.axes) + [child for ax in fig.axes for child in ax.child_axes]
    counts, point_hashes = [], []
    for ax in axes:
        points = [np.asarray(c.get_offsets(), dtype='<f8') for c in ax.collections
                  if isinstance(c, PathCollection)]
        counts.append(sum(len(p) for p in points))
        point_hashes.append(hashlib.sha256(b''.join(p.tobytes() for p in points)).hexdigest())
    caller = Path(inspect.stack()[1].filename).resolve()
    receipt = dict(figure=stem, font=font, figsize=fig.get_size_inches().tolist(),
        source_data_sha256={name: sha((ROOT if name.startswith('reports/') else SOURCE) / name)
                            for name in inputs},
        scripts_sha256={p.relative_to(OUT).as_posix(): sha(p)
                        for p in [caller, OUT / 'paper_style.py', Path(__file__).resolve()]},
        protected_files_unchanged=verify_protected(), new_scientific_statistics=False,
        analysis_rerun=False, seaborn=sns.__version__, matplotlib=matplotlib.__version__,
        plotted_observations_by_main_then_inset_axes=counts,
        point_coordinate_sha256_by_axes=point_hashes,
        text_outside_nominal_canvas=escaped, all_text_font_families=sorted(font_families),
        checks=checks, output_sha256={s: sha(OUT / f'{stem}.{s}') for s in ['pdf', 'svg', 'png']})
    (OUT / 'qa' / f'{stem}_receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps({key: receipt[key] for key in ['figure', 'font', 'figsize',
        'plotted_observations_by_main_then_inset_axes', 'protected_files_unchanged']}))
