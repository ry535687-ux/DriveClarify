"""一条原生官方路线的基础设施owner；只写本阶段，保留所有原始JSON。"""
import argparse
import os
import signal
import socket
import subprocess
import time
import xml.etree.ElementTree as ET
import psutil
from common import *


def environment(arm,route,output,qualification):
    # 防止历史实验开关渗入标准B2D；只影响新子进程。
    env={k:v for k,v in os.environ.items() if not k.startswith(('DRIVECLARIFY_','DC_B2D_'))}
    for k in ['CUBLAS_WORKSPACE_CONFIG','RECORD_PATH','HISTOGRAM','TP_STATS','RESUME','RANDOM_BACKGROUND_VEHICLE_COUNT']:
        env.pop(k,None)
    env.update({
      'CARLA_ROOT':str(OUT/'carla_bootstrap_proxy'),'WORK_DIR':str(SIM),
      'LEADERBOARD_ROOT':str(SIM/'Bench2Drive/leaderboard'),'SCENARIO_RUNNER_ROOT':str(SIM/'Bench2Drive/scenario_runner'),
      'PYTHONPATH':':'.join(map(str,[ROOT,SIM,SIM/'Bench2Drive/leaderboard',SIM/'Bench2Drive/scenario_runner',SIM/'team_code',Path('/home/buaa/CARLA_0.9.15/PythonAPI/carla')])),
      'DISPLAY':':1','XAUTHORITY':'/run/user/1000/gdm/Xauthority','XDG_SESSION_TYPE':'x11','XDG_SESSION_REMOTE':'false',
      '__NV_PRIME_RENDER_OFFLOAD':'1','__GLX_VENDOR_LIBRARY_NAME':'nvidia','SDL_VIDEODRIVER':'x11',
      'PYTHONDONTWRITEBYTECODE':'1','PYTHONUNBUFFERED':'1','HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','HF_DATASETS_OFFLINE':'1',
      # 工程显存修复：原生分配器使用可扩展段；不改变张量精度、算子或模型。
      'PYTORCH_CUDA_ALLOC_CONF':'expandable_segments:True',
      'DATAGEN':'0','SAVE_TF_LABELS':'0','TMP_VISU':'0','DEBUG_CHALLENGE':'0',
      'ROUTES':str(route),'SAVE_PATH':str(output/'official_data')+'/',
      'TOWN':ET.parse(route).getroot().find('route').get('town'),'REPETITION':'0',
      'DC_B2D_ARM':arm,'DC_B2D_ATTEMPT_DIR':str(output),
      'DC_B2D_INTERFACE_CONFIG':str(OUT/'interface_config.json'),'DC_B2D_QUALIFICATION':'1' if qualification else '0',
      'DRIVECLARIFY_PROBE_ENABLED':'0','DRIVECLARIFY_SHADOW_V0':'0','DRIVECLARIFY_PAPER_MVP_STAGE6A_LIVE':'0',
    })
    return env


def authoritative(path):
    try:data=load(path,{})
    except (ValueError,OSError):return None
    records=data.get('_checkpoint',{}).get('records',[])
    if len(records)!=1:return None
    r=records[0];status=r.get('status','')
    # 即便官方失败状态有数字零，基础设施在正式驾驶前崩溃的壳记录不具权威性。
    if status in ('Started','Failed - Agent couldn\'t be set up','Failed - Agent crashed','Failed - Simulation crashed'):
        return None
    if 'crashed' in status.lower() or 'couldn\'t' in status.lower():return None
    if not status.startswith(('Failed','Completed','Perfect')):return None
    if not all(k in r.get('scores',{}) for k in ['score_composed','score_route','score_penalty']):return None
    # debug=0不会写live中间评分；最终record先于清理/progress收尾已具权威性。
    # 不因进程在最终评分后退出而重跑已有有效低分结果。
    return r


def port_free(port):
    with socket.socket() as s:
        try:s.bind(('127.0.0.1',port));return True
        except OSError:return False


def run(arm,route,seed,port,output,qualification):
    output=Path(output).resolve();route=Path(route).resolve()
    if output.exists():raise RuntimeError('ATTEMPT_DIR_ALREADY_EXISTS')
    output.mkdir(parents=True)
    requested_port=port
    candidates=range(port,49000,10)
    port=next((candidate for candidate in candidates if all(port_free(p) for p in (candidate,candidate+1,candidate+200))),None)
    if port is None:raise RuntimeError('ISOLATED_DYNAMIC_PORT_POOL_EXHAUSTED')
    env=environment(arm,route,output,qualification)
    cmd=[PYTHON,'-B',str(SIM/'Bench2Drive/leaderboard/leaderboard/leaderboard_evaluator.py'),
      '--routes='+str(route),'--repetitions=1','--track=SENSORS','--checkpoint='+str(output/'official_checkpoint.json'),
      '--timeout=600','--agent='+str(ROOT/'driveclarify_transparent_bypass_v2/benchmark_agent.py'),
      '--agent-config='+str(CHECKPOINT),'--traffic-manager-seed='+str(seed),'--port='+str(port),
      '--traffic-manager-port='+str(port+200),'--debug=0','--gpu-rank=0']
    spec={'scope':'NON_FORMAL' if qualification else 'FORMAL','arm':arm,'route':str(route),'route_sha256':sha(route),
      'seed':seed,'RPC_port':port,'streaming_port':port+1,'TM_port':port+200,'command':cmd,'environment':env,
      'requested_base_port':requested_port,'dynamic_port_pool':True,
      'start_utc':now(),'checkpoint_sha256':load(OUT/'BENCHMARK_VERSION_AUDIT.json')['formal_checkpoint']['sha256']}
    # 仅保存运行所需env；不把继承的凭据/代理变量写盘。
    allowed_env={'CARLA_ROOT','WORK_DIR','LEADERBOARD_ROOT','SCENARIO_RUNNER_ROOT','PYTHONPATH','PYTHONDONTWRITEBYTECODE','PYTHONUNBUFFERED',
      'HF_HUB_OFFLINE','HF_DATASETS_OFFLINE','TRANSFORMERS_OFFLINE','DATAGEN','SAVE_PATH','SAVE_TF_LABELS','TMP_VISU','DEBUG_CHALLENGE',
      'ROUTES','TOWN','REPETITION','DISPLAY','XAUTHORITY','SDL_VIDEODRIVER','XDG_SESSION_TYPE','XDG_SESSION_REMOTE',
      '__NV_PRIME_RENDER_OFFLOAD','__GLX_VENDOR_LIBRARY_NAME','PYTORCH_CUDA_ALLOC_CONF'}
    spec['environment']={k:v for k,v in env.items() if k in allowed_env or k.startswith(('DC_B2D_','DRIVECLARIFY_'))}
    save(output/'RUN_SPEC.json',spec)
    log('run_route '+arm+' '+str(route)+' seed='+str(seed)+' output='+str(output))
    carla_processes={};started=time.time();watchdog=None
    with (output/'evaluator.log').open('w') as stream:
        process=subprocess.Popen(cmd,cwd=str(SIM),env=env,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
        try:
            try:process_created=psutil.Process(process.pid).create_time()
            except psutil.NoSuchProcess:process_created=None
            while process.poll() is None:
                try:
                    for child in psutil.Process(process.pid).children(recursive=True):
                        if 'CarlaUE4-Linux-Shipping' in ' '.join(child.cmdline()):carla_processes[child.pid]=child.create_time()
                except (psutil.NoSuchProcess,psutil.AccessDenied):pass
                save(output/'process_heartbeat.json',{'utc':now(),'evaluator_pid':process.pid,
                  'evaluator_create_time':process_created,
                  'carla_pids':list(carla_processes),'carla_create_times':carla_processes,
                  'evaluator_pgid':process.pid,'elapsed_s':time.time()-started})
                if time.time()-started>21600 and watchdog is None:
                    watchdog='SIX_HOUR_WALLCLOCK_ATTENTION_ONLY_NO_SCIENTIFIC_TERMINATION'
                    save(output/'WATCHDOG_ATTENTION.json',{'utc':now(),'evaluator_pid':process.pid,
                      'elapsed_s':time.time()-started,'reason':watchdog,
                      'action':'仅提醒诊断；持续原生执行。墙钟耗时不能单独证明基础设施失败或用于终止合法驾驶结果。'})
                time.sleep(5)
            try:exitcode=process.wait(timeout=30)
            except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);exitcode=process.wait()
        finally:
            if process.poll() is None:
                os.killpg(process.pid,signal.SIGTERM)
                try:process.wait(timeout=20)
                except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait()
            # 只清理这个evaluator实际派生、PID创建时间仍相同的CARLA。
            for pid,created in carla_processes.items():
                try:
                    p=psutil.Process(pid)
                    if p.create_time()==created:p.terminate()
                except psutil.NoSuchProcess:pass
            time.sleep(2)
            for pid,created in carla_processes.items():
                try:
                    p=psutil.Process(pid)
                    if p.create_time()==created and p.status()!=psutil.STATUS_ZOMBIE:p.kill()
                except psutil.NoSuchProcess:pass
    raw=output/'official_checkpoint.json';record=authoritative(raw) if raw.exists() else None
    result={'exit_code':exitcode,'watchdog':watchdog,'wall_seconds':time.time()-started,
      'authoritative':record is not None,'raw_json_path':str(raw) if raw.exists() else None,
      'raw_json_sha256':sha(raw) if raw.exists() else None,'finished_utc':now(),'owned_carla_pids':list(carla_processes)}
    save(output/'PROCESS_RECEIPT.json',result)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--arm',required=True);p.add_argument('--route',required=True)
    p.add_argument('--seed',type=int,required=True);p.add_argument('--port',type=int,default=27000)
    p.add_argument('--output',required=True);p.add_argument('--qualification',action='store_true');a=p.parse_args()
    print(json.dumps(run(a.arm,a.route,a.seed,a.port,a.output,a.qualification)),flush=True)
