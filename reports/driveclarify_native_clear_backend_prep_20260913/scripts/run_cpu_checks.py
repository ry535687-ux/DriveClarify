"""仅执行本轮 CPU 脚本，逐命令保存原始输出和退出码。"""
import datetime
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
REPORT = Path(__file__).resolve().parents[1]
DEV = Path("experiments/driveclarify_native_clear_backend_dev_20260913")
LOGS = REPORT / "logs"


def run(name, args, expected=0):
    command = [sys.executable, "-B", *args]
    started = datetime.datetime.now(datetime.timezone.utc).isoformat()
    stem = name + "_" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    (LOGS / (stem + ".stdout.txt")).write_text(result.stdout)
    (LOGS / (stem + ".stderr.txt")).write_text(result.stderr)
    row = {"started_at_utc": started, "argv": command, "cwd": str(ROOT), "exit_code": result.returncode,
           "expected_exit_code": expected, "stdout": stem + ".stdout.txt", "stderr": stem + ".stderr.txt"}
    with (LOGS / "COMMANDS.jsonl").open("a") as stream:
        stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(name, result.returncode, "expected", expected)
    return result.returncode == expected


if __name__ == "__main__":
    checks = [run("unit_tests", ["-m", "unittest", "discover", "-s", str(DEV / "tests"), "-v"])]
    for letter in "AB":
        checks.append(run("dry_run_" + letter, [str(DEV / "dry_run.py"), "--case", letter]))
    checks.append(run("forbidden_execute_argument", [str(DEV / "dry_run.py"), "--case", "A", "--execute"], 2))
    checks.append(run("missing_observer_input", [str(DEV / "extract_observer.py"), "--case", "A", "--world-state", str(REPORT / "NOT_A_RUNTIME_LOG.jsonl")], 2))
    (REPORT / "evidence/CPU_CHECK_RESULTS.json").write_text(json.dumps({"scope": "ONLY_STEP3B_CPU", "passed_commands": sum(checks), "total_commands": len(checks), "native_runs": 0, "historical_tests_rerun": False}, indent=2) + "\n")
    sys.exit(0 if all(checks) else 1)
