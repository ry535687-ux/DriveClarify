"""封存后生成开发种子；继承全历史保守整数超集并扫描新增/变更文本。"""
import os,re,secrets,time
from common import *

def main():
 receipt=read(R/'V3_CANDIDATE_POOL_FREEZE_RECEIPT.json');assert receipt['total_candidate_templates_frozen']==14
 assert sha(R/'audit/QUALIFICATION_FREEZE_MANIFEST.json')==receipt['freeze_digest']
 assert not (R/'A_STAR_DEVELOPMENT_SEED_RECEIPT.json').exists()
 q=ROOT/'reports/driveclarify_rq3_paired_comparison_v2_task_binding_qualification_and_execution/qualification'
 prior=read(q/'HISTORICAL_SEED_SOURCE_INVENTORY.json');assert not prior['errors']
 assert sha(prior['exclusion_set_path'])==prior['exclusion_set_sha256']
 excluded=set(read(prior['exclusion_set_path']));old={x['path']:x for x in prior['files']};cutoff=(q/'HISTORICAL_SEED_SOURCE_INVENTORY.json').stat().st_mtime
 dirs_ex=set(prior['excluded_directories']);exts=set(prior['extensions']);delta=[];errors=[];started=now()
 for root in prior['roots']:
  for parent,dirs,names in os.walk(root):
   dirs[:]=[d for d in dirs if d not in dirs_ex and str(Path(parent)/d)!=str(R)]
   for name in names:
    p=Path(parent)/name
    if p.suffix.lower() not in exts:continue
    try:
     st=p.stat();previous=old.get(str(p))
     if previous and st.st_size==previous['bytes'] and st.st_mtime<=cutoff:continue
     h=__import__('hashlib').sha256()
     with p.open('rb') as f:
      for line in f:
       h.update(line)
       for x in re.finditer(rb'(?<![0-9])[0-9]{1,10}(?![0-9])',line):
        n=int(x.group())
        if n<=2147483647:excluded.add(n)
     delta.append({'path':str(p),'sha256':h.hexdigest(),'bytes':st.st_size})
    except OSError as e:errors.append({'path':str(p),'error':repr(e)})
 assert not errors,errors
 write(R/'audit/DEVELOPMENT_SEED_FRESHNESS_DELTA_INVENTORY.json',{'started_utc':started,'completed_utc':now(),'inherited_inventory':str(q/'HISTORICAL_SEED_SOURCE_INVENTORY.json'),'inherited_inventory_sha256':sha(q/'HISTORICAL_SEED_SOURCE_INVENTORY.json'),'inherited_exclusion_set_sha256':prior['exclusion_set_sha256'],'inherited_file_count':prior['file_count'],'inherited_total_bytes':prior['total_bytes'],'delta_rule':'new path, different size, or mtime after inherited inventory; unchanged older-size-matched files inherit conservative integer exclusions','delta_files':delta,'errors':errors,'combined_excluded_integer_count':len(excluded),'metadata_trust':'local scientific workspace file mtimes; no adversarial metadata-restoration assertion'})
 generation=now();assigned=[];rejected=[]
 for t in read(R/'V3_HOST_CANDIDATE_POOL.json')['templates']:
  for interpretation in ['1','2']:
   for i in range(1,4):
    while True:
     s=secrets.randbelow(2147483646)+1
     if s in excluded:rejected.append(s);continue
     excluded.add(s);break
    assigned.append({'template_id':t['template_id'],'condition':t['condition'],'interpretation':interpretation,'replicate':i,'seed':s,'run_id':f'ASTAR-{t["condition"]}-P{t["rank"]}-I{interpretation}-S{i}'})
 write(R/'A_STAR_DEVELOPMENT_SEED_RECEIPT.json',{'scope':'DEVELOPMENT_ONLY_NEVER_FORMAL','generation_started_utc':generation,'pool_sealed_utc':receipt['sealed_utc'],'candidate_pool_freeze_digest':receipt['freeze_digest'],'assigned':assigned,'all_development_seeds':[r['seed'] for r in assigned],'allocated_count':84,'allocation_note':'all14 x6 reserved after freeze; unneeded P2 seeds remain unexecuted and permanently excluded too','permanently_excluded_from_all_future_formal_experiments':True,'historical_intersection':[],'freshness_pass':True,'rejected_before_allocation':rejected,'development_seed_replacements':0,'formal_seeds_generated':0})
 print('84 fresh DEVELOPMENT seeds reserved; formal seeds 0',flush=True)
if __name__=='__main__':main()
