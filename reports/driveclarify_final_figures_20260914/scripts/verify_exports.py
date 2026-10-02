"""Check frozen artifacts and prepare paper-width visual QA; no analysis."""
from pathlib import Path
import ast
import json
import re
import subprocess
import xml.etree.ElementTree as ET

from PIL import Image, ImageOps
import numpy as np
from figure_io import OUT, ROOT, verify_protected, sha

STEMS = ['FIG_A_MAIN_FINAL', 'FIG_B_MEMORY_FINAL',
         'FIG_C_TRAJECTORY_TASK_FINAL', 'FIG_D_EVIDENCE_TIMING_FINAL']
PREVIOUS = {
    STEMS[1]: ('driveclarify_stage5d_figure_b_memory_transitions_v2_20260914', 'figure_b_memory_transitions_v2'),
    STEMS[2]: ('driveclarify_stage5d_figure_c_v3_final_20260914', 'figure_c_v3_final'),
    STEMS[3]: ('driveclarify_stage5d_figures_cd_v2_20260914', 'figure_d_v2'),
}


def command(*args):
    return subprocess.check_output(args, text=True)


def main():
    protected = verify_protected()
    checks = {}
    for stem in STEMS:
        receipt = json.loads((OUT / 'qa' / f'{stem}_receipt.json').read_text())
        width, height = receipt['figsize']
        assert width == 6.8
        assert receipt['font'] == 'Liberation Serif'
        assert receipt['new_scientific_statistics'] is False
        assert receipt['analysis_rerun'] is False
        assert not receipt['text_outside_nominal_canvas']
        for rel, digest in receipt['scripts_sha256'].items():
            assert sha(OUT / rel) == digest, rel
        for suffix, digest in receipt['output_sha256'].items():
            assert sha(OUT / f'{stem}.{suffix}') == digest
        if stem in PREVIOUS:
            directory, name = PREVIOUS[stem]
            old = json.loads((ROOT / 'reports' / directory / 'qa' / f'{name}_receipt.json').read_text())
            # All pre-existing numerical display and evidence checks are identical.
            assert receipt['checks'] == old['checks'], stem
            assert receipt['plotted_observations_by_main_then_inset_axes'] == old['plotted_observations_by_main_then_inset_axes']
            for rel, digest in old['source_data_sha256'].items():
                assert receipt['source_data_sha256'][rel] == digest
        else:
            # Read A v2's existing value contract as syntax; never execute it.
            old_script = ROOT / 'reports/driveclarify_stage5d_figure_a_v2_20260914/figure_a_v2.py'
            expected = next(ast.literal_eval(node.value) for node in ast.walk(ast.parse(old_script.read_text()))
                            if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name)
                            and target.id == 'expected' for target in node.targets))
            assert np.allclose(receipt['checks']['frozen_policy_percentages'], expected, rtol=0, atol=1e-12)

        pdf = str(OUT / f'{stem}.pdf')
        info = command('pdfinfo', pdf)
        fonts = command('pdffonts', pdf)
        images = command('pdfimages', '-list', pdf)
        assert re.search(r'Pages:\s+1\b', info)
        page = re.search(r'Page size:\s+([\d.]+) x ([\d.]+) pts', info)
        assert abs(float(page[1]) / 72 - width) < .001
        assert abs(float(page[2]) / 72 - height) < .001
        rows = fonts.strip().splitlines()[2:]
        assert rows and all('LiberationSerif' in row and 'Type 3' not in row
                            and re.search(r'\byes\s+yes\s+yes\b', row) for row in rows)
        assert len(images.strip().splitlines()) == 2, 'PDF contains a raster image'
        text_xml = ET.fromstring(command('pdftotext', '-bbox', pdf, '-'))
        words = [e for e in text_xml.iter() if e.tag.endswith('}word')]
        assert words
        for word in words:
            x0, y0, x1, y1 = [float(word.attrib[k]) for k in ['xMin', 'yMin', 'xMax', 'yMax']]
            assert min(x0, y0) >= -.05 and x1 <= width * 72 + .05 and y1 <= height * 72 + .05
        svg = ET.parse(OUT / f'{stem}.svg').getroot()
        assert not [e for e in svg.iter() if e.tag.endswith('}image')]
        svg_text = [e for e in svg.iter() if e.tag.endswith('}text')]
        assert len(svg_text) > 20

        with Image.open(OUT / f'{stem}.png') as image:
            assert image.size == (round(width * 600), round(height * 600))
            # PNG pHYs stores integer pixels/metre; nominal 600 dpi reads 599.9988.
            assert all(round(value) == 600 for value in image.info['dpi'])
            assert image.convert('RGBA').getchannel('A').getextrema() == (255, 255)
            rgb = image.convert('RGB')
            assert all(rgb.getpixel(point) == (255, 255, 255) for point in
                       [(0, 0), (rgb.width - 1, 0), (0, rgb.height - 1), (rgb.width - 1, rgb.height - 1)])
            for dpi in [100, 150]:
                preview = rgb.resize((round(width * dpi), round(height * dpi)), Image.Resampling.LANCZOS)
                preview.save(OUT / 'qa' / f'{stem}_paper_{dpi}ppi.png', dpi=(dpi, dpi))
                ImageOps.grayscale(preview).save(OUT / 'qa' / f'{stem}_paper_{dpi}ppi_gray.png', dpi=(dpi, dpi))
        checks[stem] = dict(size_inches=[width, height], pixels=[round(width * 600), round(height * 600)],
                           nominal_png_dpi=600, vector_pdf=True, vector_svg=True,
                           pdf_embedded_searchable_truetype=True, pdf_image_count=0,
                           svg_image_count=0, svg_text_elements=len(svg_text),
                           canvas_text_bounds_pass=True, accepted_numerical_values_verified=True,
                           previous_receipt_checks_identical=True if stem in PREVIOUS else None)

    # A/B/C/D at the same 6.8-inch width in both color and grayscale.
    for gray in [False, True]:
        suffix = '_gray' if gray else ''
        panels = [Image.open(OUT / 'qa' / f'{stem}_paper_100ppi{suffix}.png').convert('RGB') for stem in STEMS]
        top = max(im.height for im in panels[:2]); bottom = max(im.height for im in panels[2:])
        board = Image.new('RGB', (1400, top + bottom + 30), 'white')
        for im, point in zip(panels, [(10, 0), (710, 0), (10, top + 20), (710, top + 20)]):
            board.paste(im, point)
        board.save(OUT / 'qa' / f'ALL_FIGURES_SAME_PAPER_WIDTH{suffix}.png', dpi=(100, 100))
    result = dict(automated_export_qa_pass=True, protected_files_unchanged=protected,
                  original_scientific_packet_manifest_verified=True, figures=checks)
    (OUT / 'qa/EXPORT_VERIFICATION.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
