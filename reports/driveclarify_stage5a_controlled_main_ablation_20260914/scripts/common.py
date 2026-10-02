"""本报告的离线文件工具；不启动任何外部程序。"""
from pathlib import Path
import csv
import hashlib
import json

REPORT = Path(__file__).resolve().parents[1]
REPO = REPORT.parents[1]
CROOT = REPO / 'reports/driveclarify_rq1_grounded_relation_v3_20260910'
ORIGINAL = REPO / 'reports/driveclarify_rq2_t_cg_formal_v3_prospective_evaluability_and_execution_v1'
EXTENSION = REPO / 'reports/driveclarify_rq2_extension_single_cell_evaluability_adjudication_v1'
POLICIES = ('NO_CLARIFICATION', 'IMMEDIATE_QUERY', 'DRIVECLARIFY')
EQ, DV, UNKNOWN = 'TASK_EQUIVALENT', 'TASK_DIVERGENT', 'UNKNOWN'
METRICS = ('correct_decision', 'wrong_task', 'query', 'unnecessary_query', 'missed_clarification', 'unresolved', 'coverage')

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()).hexdigest()

def read_json(path):
    return json.loads(Path(path).read_text())

def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]

def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n')

def write_jsonl(path, rows):
    Path(path).write_text(''.join(json.dumps(r, ensure_ascii=False, allow_nan=False) + '\n' for r in rows))

def write_csv(path, rows):
    assert rows
    with Path(path).open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

def verify_sources():
    manifest = read_json(REPORT / 'BENCHMARK_MANIFEST.json')
    for row in manifest['sources']:
        assert sha(REPO / row['path']) == row['sha256'], row['path']
    assert sha(REPORT / 'BENCHMARK_PROTOCOL.md') == manifest['protocol_sha256']
    return manifest
