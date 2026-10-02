"""本轮少量 CPU 命令留痕；不启动历史 runner、CARLA 或模型。"""
import datetime
import json
from pathlib import Path
import subprocess
import sys

REPORT = Path(__file__).resolve().parents[1]
ROOT = REPORT.parents[1]
DEV = "experiments/driveclarify_three_policy_dev"


def run(name, argv, *, cwd=ROOT):
    now = datetime.datetime.now(datetime.timezone.utc)
    stem = now.strftime("%H%M%S_%f") + "_" + name
    result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True)
    (REPORT / "logs" / (stem+".stdout.txt")).write_text(result.stdout)
    (REPORT / "logs" / (stem+".stderr.txt")).write_text(result.stderr)
    receipt = {"utc": now.isoformat(), "cwd": str(cwd), "argv": argv, "exit_code": result.returncode,
               "stdout": "logs/"+stem+".stdout.txt", "stderr": "logs/"+stem+".stderr.txt"}
    with (REPORT / "logs/COMMANDS.jsonl").open("a") as f:
        f.write(json.dumps(receipt, ensure_ascii=False)+"\n")
    print(json.dumps(receipt, ensure_ascii=False))
    if result.returncode:
        print(result.stdout, result.stderr)
        raise SystemExit(result.returncode)
    return receipt


if __name__ == "__main__":
    if "--prepare-assets" in sys.argv:
        run("prepare_assets", [sys.executable, str(REPORT/"scripts/prepare_assets.py")])
    run("unittest", [sys.executable, "-B", "-m", "unittest", "discover", "-s", DEV+"/tests", "-v"])
    base = [sys.executable, "-B", "-m", "experiments.driveclarify_three_policy_dev", "--enable-cpu-dev"]
    timestamp = datetime.datetime.now().strftime("%H%M%S_%f")
    run("cli_policy", base + ["policy", "--public", DEV+"/configs/SYNTHETIC.public.json", "--states", DEV+"/configs/SYNTHETIC.states.json",
        "--policy", "DRIVECLARIFY_CONTROLLED", "--receipts", str(REPORT/"logs"/("questions_"+timestamp)),
        "--answer-file", DEV+"/configs/SYNTHETIC.answer.json"])
    run("cli_evaluate", base + ["evaluate"] + [v for field in ("contract", "trace", "truth", "coverage", "safety")
        for v in ("--"+field, DEV+"/configs/SYNTHETIC."+field+".json")])
    run("cli_real_static_blocked", base + ["policy", "--public", DEV+"/configs/TOWN04_DIVERGENT_DEV.public.json",
        "--states", DEV+"/configs/SYNTHETIC.states.json", "--policy", "DRIVECLARIFY_CONTROLLED",
        "--receipts", str(REPORT/"logs"/("static_questions_"+timestamp)), "--answer-file", DEV+"/configs/SYNTHETIC.answer.json"])
