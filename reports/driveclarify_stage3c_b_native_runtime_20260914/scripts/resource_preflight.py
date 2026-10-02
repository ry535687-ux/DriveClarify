"""现场资源/进程只读快照；不处理非本轮进程，不创建运行目录。"""
import argparse
import datetime
import importlib.util
import json
import os
from pathlib import Path
import shutil

REPORT = Path(__file__).resolve().parents[1]
ROOT = REPORT.parents[1]
spec = importlib.util.spec_from_file_location("display_capture", REPORT / "scripts/display_preflight.py")
capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture)


def collect(label):
    display = json.loads((REPORT / "evidence/PHYSICAL_DISPLAY_GATE_DECISION.json").read_text())
    if display["status"] != "PASS":
        raise ValueError("STOP_DISPLAY_GATE_NOT_PASSED")
    results = {}
    for key, argv in (
        ("nvidia_smi", ["nvidia-smi"]),
        ("gpu", ["nvidia-smi", "--query-gpu=index,name,memory.total,memory.used,memory.free,utilization.gpu", "--format=csv,noheader,nounits"]),
        ("compute", ["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory", "--format=csv,noheader,nounits"]),
        ("processes", ["ps", "-eo", "pid=,ppid=,pgid=,user=,stat=,etime=,comm=,args="]),
        ("ports", ["ss", "-lntup"]),
    ):
        results[key] = capture.command(label + "_" + key, argv)
    memory = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, value = line.split(":", 1)
        if key in ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree"):
            memory[key + "_KiB"] = int(value.split()[0])
    disks = {str(p): dict(zip(("total_bytes", "used_bytes", "free_bytes"), shutil.disk_usage(p)))
             for p in (ROOT, Path("/home/buaa/wrh/simlingo"), REPORT)}
    processes = []
    conflicts = []
    for line in results["processes"]["stdout_text"].splitlines():
        fields = line.split(None, 7)
        if len(fields) != 8:
            continue
        row = dict(zip(("pid", "ppid", "pgid", "user", "stat", "etime", "comm", "args"), fields))
        if any(token in row["comm"].lower() for token in ("carla", "python", "leaderboard")) or "leaderboard_evaluator.py" in row["args"]:
            processes.append(row)
        # 只匹配真实可执行进程，避免包含本脚本代码文本的 shell 自身误报。
        if "carla" in row["comm"].lower() or (row["comm"].startswith("python") and any(token in row["args"] for token in ("leaderboard_evaluator.py", "scenario_runner.py"))):
            conflicts.append(row)
    occupied = [line for line in results["ports"]["stdout_text"].splitlines()
                if any(token in line for token in (":2020 ", ":2021 ", ":2022 ", ":8020 "))]
    outputs = {letter: ROOT / "experiments/driveclarify_native_clear_backend_dev_20260913/outputs" /
               ("DEV_NATIVE_INIT_A_TOWN07_25968" if letter == "A" else "DEV_NATIVE_INIT_B_TOWN07_26458") for letter in "AB"}
    body = {"captured_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "label": label,
            "commands": results, "memory": memory, "disks": disks, "cpu_load": list(os.getloadavg()),
            "related_processes": processes, "conflicting_native_processes": conflicts,
            "requested_port_occupancy": occupied, "output_dirs_exist": {k: v.exists() for k, v in outputs.items()},
            "python_exists": Path("/home/buaa/anaconda3/envs/simlingo/bin/python").is_file(),
            "resource_floor_source": "已知 backend/display_preflight 未定义显存/RAM/磁盘数值下限；按授权只记录状态并判明显冲突，不发明阈值",
            "resource_gate_status": "PENDING_REVIEW_OF_CAPTURED_FACTS", "gpu_peak": None, "capture_only_no_launch_by_this_script": True}
    (REPORT / "evidence" / (label + "_RESOURCE_SNAPSHOT.json")).write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"gpu": results["gpu"]["stdout_text"], "compute": results["compute"]["stdout_text"],
                      "memory": memory, "disks": disks, "cpu_load": body["cpu_load"], "conflicts": conflicts,
                      "ports": occupied, "outputs": body["output_dirs_exist"], "command_exit_codes": {k: v["exit_code"] for k,v in results.items()}}, ensure_ascii=False, indent=2))
    return body


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    collect(parser.parse_args().label)
