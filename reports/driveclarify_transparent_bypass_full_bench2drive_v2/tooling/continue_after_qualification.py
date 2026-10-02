"""独立持久门控：8/8通过后立即运行新的正式freeze与完整roster。"""
import subprocess
import time
from common import *

print('WAITING_FOR_PROSPECTIVE_TRANSPARENCY_8_OF_8',flush=True)
while True:
    receipt=load(OUT/'TRANSPARENCY_QUALIFICATION_RECEIPT.json',{})
    if receipt.get('pass') is True:
        assert receipt['qualified_routes']==8 and receipt['native_executions']==16
        assert sha(OUT/'TRANSPARENCY_QUALIFICATION_RESULTS.json')==receipt['results_sha256']
        print('QUALIFICATION_GATE_PASSED_STARTING_FORMAL_AUTOMATICALLY',flush=True)
        with (OUT/'audit/formal_supervisor.log').open('a') as stream:
            result=subprocess.call([PYTHON,'-B',str(OUT/'tooling/formal.py')],cwd=str(ROOT),stdout=stream,stderr=subprocess.STDOUT)
        print('FORMAL_SUPERVISOR_RETURNED',result,flush=True)
        break
    time.sleep(5)
