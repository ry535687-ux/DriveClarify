"""只启动无害 Python dummy；不导入或启动原生驾驶入口。"""
import datetime
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile

REPORT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent


def child_run(spec_path):
    from owned_runtime import run_owned
    spec = json.loads(Path(spec_path).read_text())
    receipt = run_owned(spec["command"], dict(os.environ), spec["cwd"], spec["output"], spec["wall_seconds"])
    print(json.dumps(receipt, ensure_ascii=False))


def checks():
    root = Path(tempfile.mkdtemp(prefix="driveclarify_stage3c_dummy_"))
    sentinel = subprocess.Popen([sys.executable, "-B", "-c", "import time; time.sleep(90)"])
    results = []
    try:
        # 模拟 evaluator 正常/异常退出，及 CARLA 脱离为新会话后父进程已退出。
        child_code = "import socket,time,os,pathlib; s=socket.socket();s.bind(('127.0.0.1',0));s.listen();pathlib.Path('port.txt').write_text(str(s.getsockname()[1]));pathlib.Path('child_pid.txt').write_text(str(os.getpid()));time.sleep(90)"
        detached_code = "import subprocess,sys,time,os; p=subprocess.Popen([sys.executable,'-B','-c'," + repr(child_code) + "],start_new_session=True); time.sleep(.3); os._exit(7)"
        timed_code = "import subprocess,sys,time,signal;signal.signal(signal.SIGTERM,signal.SIG_IGN);p=subprocess.Popen([sys.executable,'-B','-c'," + repr(child_code) + "],start_new_session=True);time.sleep(90)"
        for name,code,wall in (("exit_code", "import sys;print('dummy stdout');print('dummy stderr',file=sys.stderr);sys.exit(7)", 5),
                               ("detached_child_cleanup", detached_code, 5), ("hard_timeout", timed_code, 1.2)):
            cwd=root/name;cwd.mkdir();output=cwd/'owned';spec={"command":[sys.executable,'-B','-c',code],"cwd":str(cwd),"output":str(output),"wall_seconds":wall};sp=cwd/'spec.json';sp.write_text(json.dumps(spec))
            argv=[sys.executable,'-B',str(Path(__file__).resolve()),'--child',str(sp)]
            started=datetime.datetime.now(datetime.timezone.utc).isoformat();cp=subprocess.run(argv,capture_output=True,text=True,timeout=12)
            stem='dummy_'+name+'_'+root.name
            (REPORT/'logs'/f'{stem}.stdout.txt').write_text(cp.stdout);(REPORT/'logs'/f'{stem}.stderr.txt').write_text(cp.stderr)
            with (REPORT/'logs/COMMANDS.jsonl').open('a') as stream:stream.write(json.dumps({'started_at_utc':started,'argv':argv,'cwd':str(Path.cwd()),'exit_code':cp.returncode,'stdout':f'logs/{stem}.stdout.txt','stderr':f'logs/{stem}.stderr.txt'})+'\n')
            assert cp.returncode==0,cp.stderr
            receipt=json.loads((output/'OWNED_RUNTIME_RECEIPT.json').read_text());assert receipt['cleanup_status']=='PASS'
            assert sentinel.poll() is None, 'UNRELATED_SENTINEL_WAS_KILLED'
            expected_exit=7 if name!='hard_timeout' else -9;assert receipt['exit_code']==expected_exit
            if name=='hard_timeout':
                assert receipt['watchdog']['triggered'] is True
                assert receipt['watchdog']['kill_started_monotonic']-receipt['watchdog']['deadline_monotonic']<.25
                assert receipt['total_wall_seconds_including_cleanup']<wall+1
            port=None
            if (cwd/'port.txt').exists():
                port=int((cwd/'port.txt').read_text())
                with socket.socket() as sock:sock.bind(('127.0.0.1',port))
                assert not Path('/proc/'+(cwd/'child_pid.txt').read_text()).exists()
            (REPORT/'evidence'/f'DUMMY_{name}_RECEIPT.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
            results.append({'name':name,'pass':True,'dummy_exit_code':receipt['exit_code'],'cleanup':receipt['cleanup_status'],'released_dummy_port':port,'unrelated_sentinel_survived':True,'wall_seconds':receipt['total_wall_seconds_including_cleanup']})
    finally:
        # 只结束本测试创建的哨兵，其他进程不动。
        sentinel.terminate();sentinel.wait(timeout=3)
    result={'status':'PASS','checks':results,'source':'STAGE3C harmless dummy processes, not driving evidence','native_runs':0,'temp_root':str(root)}
    (REPORT/'evidence/WRAPPER_CPU_CHECKS.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    if len(sys.argv)==3 and sys.argv[1]=='--child':child_run(sys.argv[2])
    else:checks()
