"""过度包含的历史种子排除审计：把历史文本所有 32 位非负整数都排除。"""
import hashlib,json,re,time,os
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];Q=ROOT/'reports/driveclarify_rq3_paired_comparison_v2_task_binding_qualification_and_execution/qualification'
# 主项目所有历史实验文本及模型仓库的运行配置/路线/工具文本。
roots=[ROOT,Path('/home/buaa/wrh/simlingo')]
exts={'.json','.jsonl','.ndjson','.txt','.log','.md','.csv','.tsv','.yaml','.yml','.py','.sh','.xml','.toml','.ini','.cfg','.out','.err'}
exclude_dirs={'.git','__pycache__','.cache','node_modules','.venv','venv','cache','DerivedDataCache'}
values=set();files=[];errors=[];started=time.time()
def candidates(root):
 for parent,dirs,names in os.walk(root):
  dirs[:]=[x for x in dirs if x not in exclude_dirs]
  for name in names:
   p=Path(parent)/name
   if p.suffix.lower() in exts:yield p
for root in roots:
 for p in candidates(root):
  if str(Q.parent) in str(p):continue # 本 V2 尚无正式种子；development 单独加入。
  h=hashlib.sha256();size=0
  try:
   with p.open('rb') as f:
    for line in f:
     h.update(line);size+=len(line)
     for match in re.finditer(rb'(?<![0-9])[0-9]{1,10}(?![0-9])',line):
      n=int(match.group())
      if n<=2147483647:values.add(n)
   files.append({'path':str(p),'bytes':size,'sha256':h.hexdigest()})
  except (OSError,MemoryError) as e:errors.append({'path':str(p),'error':repr(e)})
for s in json.loads((Q/'DEVELOPMENT_REGISTRATION.json').read_text())['permanently_excluded_seeds']:values.add(s)
np=Q/'HISTORICAL_INTEGER_EXCLUSION_SET.json';np.write_text(json.dumps(sorted(values),separators=(',',':'))+'\n')
manifest={'scope':'所有历史文本整数的保守超集排除，避免嵌套 seed list 或日志格式导致漏取；并非称所有数字都是实际 seeds。','roots':[str(x) for x in roots],'extensions':sorted(exts),'excluded_directories':sorted(exclude_dirs),'exclusions_explanation':'git/cache/compiled/dependency/env directories are not experiment seed authorities. Binary model/image/video/archive files are not read as text; seed-bearing manifests/configurations/logs in every historical experiment tree are included.','files':files,'file_count':len(files),'total_bytes':sum(x['bytes'] for x in files),'integer_count':len(values),'exclusion_set_path':str(np),'exclusion_set_sha256':hashlib.sha256(np.read_bytes()).hexdigest(),'errors':errors,'elapsed_wall_s':time.time()-started,'formal_seeds_generated':0}
(Q/'HISTORICAL_SEED_SOURCE_INVENTORY.json').write_text(json.dumps(manifest,indent=2,ensure_ascii=False)+'\n');print(json.dumps({k:v for k,v in manifest.items() if k!='files'}));assert not errors
