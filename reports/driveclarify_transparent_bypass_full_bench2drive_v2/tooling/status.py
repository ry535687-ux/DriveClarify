"""仅显示正式账本和当前原生进度，不读取或计算驾驶成绩。"""
import re
from common import *

def main():
    result={'utc':now()}
    for arm in ['A0','A1']:
        ledger=load(OUT/(arm+'_EXECUTION_LEDGER.json'))
        result[arm+'_authoritative']=ledger['authoritative_completed'] if ledger else None
    pairs=load(OUT/'PAIRED_ROUTE_LEDGER.json')
    result['complete_pairs']=pairs['complete_pairs'] if pairs else None
    retry=load(OUT/'TECHNICAL_RETRY_LEDGER.json')
    if retry:
        result['formal_retries']=retry.get('formal_technical_retries',0)
        result['qualification_retries']=retry.get('qualification_technical_retries',0)
    log_path=OUT/'audit/formal_supervisor.log'
    if log_path.exists():
        lines=log_path.read_text(errors='replace').splitlines()
        result['supervisor_latest']=lines[-1] if lines else None
        starts=[s for s in lines if s.startswith('FORMAL_START ')]
        if starts:
            _,index,route,arm,_,attempt=starts[-1].split()
            folder=OUT/'formal'/route/arm/('attempt_%02d'%int(attempt))
            result['latest_attempt']={'index':int(index),'route_id':route,'arm':arm,'attempt':int(attempt)}
            progress=folder/'evaluator.log'
            if progress.exists():
                with progress.open('rb') as stream:
                    stream.seek(max(0,progress.stat().st_size-4096));tail=stream.read().decode(errors='replace')
                times=re.findall(r'System time = ([0-9.]+) -- Game time = ([0-9.]+)',tail)
                if times:result['latest_native_time']={'wall_s':float(times[-1][0]),'simulation_s':float(times[-1][1])}
            result['latest_process_finished']=(folder/'PROCESS_RECEIPT.json').exists()
    result['final_status']=load(OUT/'FINAL_VALIDATION_RECEIPT.json',{}).get('status')
    print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':main()
