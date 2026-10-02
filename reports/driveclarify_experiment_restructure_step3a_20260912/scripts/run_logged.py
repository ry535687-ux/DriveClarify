"""本轮命令留痕，调用方显式给出有限 CPU 命令。"""
import datetime
import json
from pathlib import Path
import subprocess
import sys

REPORT = Path(__file__).resolve().parents[1]
ROOT = REPORT.parents[1]


def run(name, argv, *, cwd=ROOT, expected=0):
    now = datetime.datetime.now(datetime.timezone.utc)
    stem = now.strftime("%H%M%S_%f")+"_"+name
    result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True)
    for key, content in (("stdout", result.stdout), ("stderr", result.stderr)):
        (REPORT/"logs"/(stem+"."+key+".txt")).write_text(content)
    receipt = {"utc": now.isoformat(), "argv": argv, "cwd": str(cwd), "exit_code": result.returncode,
               "stdout": "logs/"+stem+".stdout.txt", "stderr": "logs/"+stem+".stderr.txt"}
    with (REPORT/"logs/COMMANDS.jsonl").open("a") as stream:
        stream.write(json.dumps(receipt, ensure_ascii=False)+"\n")
    print(json.dumps(receipt, ensure_ascii=False))
    if result.returncode != expected:
        print(result.stdout, result.stderr)
        raise SystemExit(result.returncode or 1)
    return receipt


if __name__ == "__main__":
    run(sys.argv[1], sys.argv[2:])
