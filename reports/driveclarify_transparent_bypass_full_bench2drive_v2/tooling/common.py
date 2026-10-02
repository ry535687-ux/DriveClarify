import datetime
import hashlib
import json
from pathlib import Path

ROOT=Path('/home/buaa/wrh/DriveClarify')
SIM=Path('/home/buaa/wrh/simlingo')
OUT=ROOT/'reports/driveclarify_transparent_bypass_full_bench2drive_v2'
OLD=ROOT/'reports/driveclarify_full_bench2drive_standard_benchmark_v1'
PYTHON='/home/buaa/anaconda3/envs/simlingo/bin/python'
CHECKPOINT=ROOT/'reports/driveclarify_v3_short_prefix_a1_fast_track/a1_training_v2/selected/checkpoints/a1_selected.ckpt/pytorch_model.pt'

def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(4*1024*1024),b''):h.update(b)
    return h.hexdigest()

def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()

def load(path,default=None):
    p=Path(path)
    return json.loads(p.read_text()) if p.exists() else default

def save(path,value):
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True);t=p.with_suffix(p.suffix+'.tmp')
    t.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n');t.replace(p)

def log(command):
    with (OUT/'COMMAND_LOG.md').open('a') as f:f.write('\n- '+now()+' `'+command+'`\n')
