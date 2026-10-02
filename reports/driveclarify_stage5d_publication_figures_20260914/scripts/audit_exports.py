"""Read-only data/export audit; --record saves audit and grayscale QA copies locally."""
from pathlib import Path
import argparse
import hashlib
import json
import re
import subprocess
import xml.etree.ElementTree as ET
from PIL import Image, ImageOps

OUT=Path(__file__).resolve().parents[1]
ROOT=OUT.parents[1]
SOURCE=ROOT/'reports/driveclarify_stage5c_final_experiment_freeze_20260914'
NAMES=['figure_a_main_tradeoff','figure_b_ablation_forest','figure_c_trajectory_task','figure_d_evidence_timing']


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(record=False):
    freeze=json.loads((OUT/'qa/SOURCE_FREEZE.json').read_text())
    assert sha(SOURCE/'ARTIFACT_MANIFEST.json')==freeze['source_manifest_sha256']
    for item in freeze['files']:
        path=SOURCE/item['path']
        assert path.stat().st_size==item['bytes'] and sha(path)==item['sha256'], item['path']
    assert {p.relative_to(SOURCE).as_posix() for p in SOURCE.rglob('*') if p.is_file() and '__pycache__' not in p.parts}=={r['path'] for r in freeze['files']}
    receipts=json.loads((OUT/'qa/RENDER_RECEIPTS.json').read_text())
    assert len(receipts['figures'])==8
    sources={r['path']:r['sha256'] for r in freeze['files']}
    for item in receipts['figures']:
        assert item['script_sha256']==sha(OUT/'scripts/render_publication_figures.py')
        assert item['source_hashes_match_after_export'] and not item['text_outside_nominal_canvas']
        for path,digest in item['source_data_sha256'].items():assert sources[path]==digest
        expected=dict(a=[66,66,66],b=[2,4,2],c=[64,112,13,14],d=[63,63,63,63])[item['figure']]
        assert item['scatter_points_by_panel']==expected
        if item['figure']=='a':assert item['checks']['no_jitter']
        if item['figure']=='b':assert item['checks']['horizontal_uncertainty_lines']==0
        if item['figure']=='c':assert item['checks']['y_values_unchanged']
        if item['figure']=='d':
            for panel in item['checks']['panels']:
                assert panel['drawn_medians_match_frozen']
                if not panel['zoomed_view']:assert all(x['visible']==x['total'] for x in panel['groups'])
            assert [r['n'] for r in item['checks']['frozen_summary_checks']]==[17,29,0,17,17,29,0,17]
            assert [r['complete_missing'] for r in item['checks']['frozen_summary_checks']]==[30,18,23,6,30,18,23,6]
    exports=[]
    for single in [False,True]:
        directory=OUT/'figures'/('single_column' if single else '')
        for name in NAMES:
            pdf=directory/f'{name}.pdf';svg=directory/f'{name}.svg';png=directory/f'{name}.png'
            info=subprocess.check_output(['pdfinfo',str(pdf)],text=True)
            assert re.search(r'Pages:\s+1\b',info)
            width,height=map(float,re.search(r'Page size:\s+([\d.]+) x ([\d.]+) pts',info).groups())
            assert (3.3<=width/72<=3.5) if single else (6.8<=width/72<=7.1)
            fonts=subprocess.check_output(['pdffonts',str(pdf)],text=True)
            assert 'Type 3' not in fonts and 'DejaVuSans' in fonts
            assert re.search(r'\byes\s+yes\s+yes\s+\d+\s+\d+',fonts)
            raster=subprocess.check_output(['pdfimages','-list',str(pdf)],text=True)
            assert len(raster.strip().splitlines())==2, 'Raster image in PDF'
            xml=ET.fromstring(subprocess.check_output(['pdftotext','-bbox',str(pdf),'-']))
            pages=[e for e in xml.iter() if e.tag.endswith('page')]
            words=[]
            for page in pages:
                w,h=float(page.attrib['width']),float(page.attrib['height'])
                for word in page.iter():
                    if not word.tag.endswith('word'):continue
                    a=word.attrib
                    assert float(a['xMin'])>=0 and float(a['yMin'])>=0 and float(a['xMax'])<=w and float(a['yMax'])<=h
                    words.append(word.text or '')
            assert len(words)>30
            xml_svg=ET.parse(svg).getroot()
            assert not any(e.tag.endswith('image') for e in xml_svg.iter())
            assert sum(e.tag.endswith('text') for e in xml_svg.iter())>15
            with Image.open(png) as img:
                dpi=img.info['dpi']
                # PNG stores pixels per metre as integers; nominal 600 dpi is 599.9988 after conversion.
                assert all(round(d)==600 for d in dpi)
                assert abs(img.width/(width/72)-600)<1
                assert img.convert('RGBA').getextrema()[3]==(255,255)
                assert img.convert('RGB').getpixel((0,0))==(255,255,255)
                size=img.size
            if record:
                qa=OUT/'qa'/f'{name}_{"single" if single else "double"}_150dpi.png'
                with Image.open(qa) as view:
                    ImageOps.grayscale(view.convert('RGB')).save(OUT/'qa'/f'{name}_{"single" if single else "double"}_grayscale.png',dpi=(150,150))
            exports.append(dict(figure=name,variant='single_column' if single else 'double_column',
                                width_in=width/72,height_in=height/72,png_pixels=size,png_dpi_metadata=dpi,
                                nominal_png_dpi=600,pdf_embedded_truetype=True,pdf_raster_images=0,
                                svg_text_preserved=True,svg_raster_images=0,all_pdf_words_in_bounds=True,
                                pdf_sha256=sha(pdf),svg_sha256=sha(svg),png_sha256=sha(png)))
    assert len(list((OUT/'figures').rglob('*.pdf')))==8
    assert len(list((OUT/'figures').rglob('*.svg')))==8
    assert len(list((OUT/'figures').rglob('*.png')))==8
    assert not list(OUT.rglob('*.csv')),'Stage5D must not create or replace a numeric CSV'
    result=dict(status='STAGE5D_PUBLICATION_FIGURES_READY',passed=True,
                stage5c_files_unchanged=len(freeze['files']),stage5c_manifest_sha256=freeze['source_manifest_sha256'],
                figure_variants_checked=len(exports),export_files_checked=24,versions=receipts['versions'],
                scientific_statistics_added=False,export_checks=exports)
    if record:(OUT/'qa/EXPORT_AUDIT.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--record',action='store_true');args=parser.parse_args()
    result=audit(args.record)
    print(json.dumps({k:v for k,v in result.items() if k!='export_checks'},ensure_ascii=False,indent=2))
