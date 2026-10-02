"""只读现有证据；仅在 STEP2 目录写入小型摘录，不运行项目模块。"""
from pathlib import Path
import ast
import hashlib
import json

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parents[1]
S1 = ROOT / 'reports/driveclarify_experiment_restructure_step1_20260912'

def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

SLICES = {
    'driveclarify_rq1_grounded_relation_v3/methods.py': [(86,121),(153,218)],
    'driveclarify_rq1_conditional_supplement/judges_revised.py': [(86,94),(135,161),(190,224)],
    'driveclarify_rq1_conditional_supplement/judges.py': [(89,108),(111,137)],
    'driveclarify_rq1_v2/consequence.py': [(15,22),(61,78),(99,143),(157,202)],
    'driveclarify_rq1_v2/simlingo_agent.py': [(101,113),(141,160)],
    'driveclarify_ablation_overnight/agent.py': [(24,49),(173,207)],
    'driveclarify_rq3/simlingo_agent.py': [(193,215),(220,225),(242,268),(278,280),(310,310),(342,348)],
    'driveclarify_rq2_t/measurement.py': [(567,600),(604,633)],
    'driveclarify_clear_passthrough_v11/supervisor.py': [(100,107),(131,157),(198,219),(234,253)],
    'driveclarify_clear_passthrough_v11/oracle.py': [(13,38)],
}
manifest = []
lines = ['# Q1/Q2/Q5 短代码证据', '', '这是带原行号的只读摘录，不能当成可执行完整模块。当前文件身份不反推历史运行身份。', '']
for name, ranges in SLICES.items():
    p = ROOT/name
    source = p.read_text().splitlines()
    digest = sha(p)
    tree = ast.parse(p.read_text())
    functions = [{'name':x.name,'start':x.lineno,'end':x.end_lineno} for x in ast.walk(tree) if isinstance(x,(ast.FunctionDef,ast.AsyncFunctionDef))]
    manifest.append({'path':name,'sha256':digest,'ranges':ranges,'functions':functions})
    lines.extend([f'## `{name}`', '', f'SHA-256: `{digest}`', ''])
    for start,end in ranges:
        lines.extend([f'原文件 L{start}–L{end}', '', '```python'])
        lines.extend(f'{i:4d}  {source[i-1]}' for i in range(start,min(end,len(source))+1))
        lines.extend(['```',''])
(OUT/'evidence/CODE_EXCERPTS.md').write_text('\n'.join(lines))
(OUT/'evidence/CODE_SOURCE_MANIFEST.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))

# 复用 STEP1 汇总，不加载其 4359/1462 行输入、不重算历史结果。
d=json.loads((S1/'results/RECOMPUTED_RQ1.json').read_text())
summary={k:v for k,v in d.items() if k in ['inputs','balanced_accuracy_definition','frozen_thresholds_m_verified_not_retuned','inventory','table1_four_cell','table2_method_comparison_original','full_vs_m4_identity']}
summary['revision_counts']={'sample_condition_pairs':731,'csv_rows_two_versions':1462,'changed_defined':31,'changed_undefined':18,'changed_total':49,'wrong_definite_withdrawn_defined_only':19,'correct_definite_abandoned_defined_only':12,'available_task_evidence_pairs':277,'revised_equals_M4_pairs':731}
summary['provenance']={'path':str((S1/'results/RECOMPUTED_RQ1.json').relative_to(ROOT)),'sha256':sha(S1/'results/RECOMPUTED_RQ1.json'),'method':'STEP1汇总引用；本轮未全量重算'}
(OUT/'evidence/COUNT_SUMMARY.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))

# 后续出口检查保护范围：本地方法源码、历史测试及 STEP1 产物。
protected=[]
for base in sorted(ROOT.glob('driveclarify*')):
    protected.extend(base.rglob('*.py') if base.is_dir() else [base])
protected.extend((ROOT/'tests').rglob('*.py'))
protected.extend(p for p in S1.rglob('*') if p.is_file())
protected=[p for p in protected if '__pycache__' not in p.parts]
before=OUT/'evidence/PROTECTED_SOURCE_ENTRY.json'
if not before.exists():
    before.write_text(json.dumps({str(p.relative_to(ROOT)):sha(p) for p in sorted(set(protected))},ensure_ascii=False,indent=2))
print(json.dumps({'code_files':len(manifest),'protected_files':len(set(protected)),'no_project_imports':True}))
