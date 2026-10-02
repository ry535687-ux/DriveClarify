"""Passive resource sampler for this round; no simulator or model API calls."""
import datetime
import json
import pathlib
import shutil
import subprocess
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
stop = ROOT / 'STOP_RESOURCE_MONITOR'
deadline = datetime.datetime.fromisoformat('2026-09-09T14:50:34+08:00').timestamp()
with (ROOT / 'RESOURCE_SAMPLES.jsonl').open('a', buffering=1) as stream:
    while time.time() < deadline and not stop.exists():
        gpu = subprocess.run(['nvidia-smi', '--query-gpu=memory.used,memory.free,utilization.gpu,power.draw',
                              '--format=csv,noheader,nounits'], capture_output=True, text=True)
        processes = subprocess.run(['ps', '-eo', 'pid=,ppid=,etimes=,%cpu=,rss=,args='], capture_output=True, text=True)
        owned = [x for x in processes.stdout.splitlines()
                 if ('28100' in x or '28101' in x or '28202' in x or str(ROOT) in x)
                 and 'resource_monitor.py' not in x]
        stream.write(json.dumps({'local_time': datetime.datetime.now().astimezone().isoformat(),
            'epoch': time.time(), 'host_gpu_memory_utilization_power': gpu.stdout.strip(),
            'gpu_scope': 'HOST_INCLUDES_DESKTOP', 'disk_free_bytes': shutil.disk_usage(ROOT).free,
            'matched_task_process_rows': owned}, ensure_ascii=False) + '\n')
        time.sleep(15)
