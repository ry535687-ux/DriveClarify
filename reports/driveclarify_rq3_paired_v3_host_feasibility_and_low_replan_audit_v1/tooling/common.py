import datetime, hashlib, json
from pathlib import Path
ROOT=Path('/home/buaa/wrh/DriveClarify')
R=ROOT/'reports/driveclarify_rq3_paired_v3_host_feasibility_and_low_replan_audit_v1'
CHECKPOINT=ROOT/'reports/driveclarify_v3_short_prefix_a1_fast_track/a1_training_v2/selected/checkpoints/a1_selected.ckpt/pytorch_model.pt'
CHECKPOINT_SHA='cc6873e2a7778140ff7af3fd7d3578114b26bd6a47974e955f0845ddd2178044'
FAMILIES=['REF-C','LMK-C','ORD-C','USC-C','REF-E','LMK-E','ORD-E']
def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def read(p):return json.loads(Path(p).read_text())
def sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for b in iter(lambda:f.read(8388608),b''):h.update(b)
 return h.hexdigest()
def digest(x):return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def write(p,x):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);t=p.with_suffix(p.suffix+'.tmp');t.write_text(json.dumps(x,indent=2,ensure_ascii=False,allow_nan=False)+'\n');t.replace(p)
