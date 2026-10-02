"""只接受本阶段已通过的单臂preflight；没有循环、重试或下一臂调度。"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

from owned_runtime import run_owned, write_json

REPORT = Path(__file__).resolve().parents[1]


def execute(letter):
    gate_path = REPORT / f"{letter}_PREFLIGHT_GATE.json"
    gate = json.loads(gate_path.read_text())
    required = ("identity", "physical_display", "resource", "process_port", "output_isolation", "wall_guard")
    if gate.get("status") != "PASS" or not all(gate.get(k) == "PASS" for k in required):
        raise RuntimeError("PREFLIGHT_NOT_ALL_PASS")
    if not 0 <= time.monotonic() - gate["created_monotonic"] < 180:
        raise RuntimeError("PREFLIGHT_STALE")
    path = REPORT / f"{letter}_EFFECTIVE_COMMAND.json"
    if hashlib.sha256(path.read_bytes()).hexdigest() != gate["effective_command_sha256"]:
        raise RuntimeError("EFFECTIVE_COMMAND_CHANGED")
    if hashlib.sha256((REPORT / "scripts/owned_runtime.py").read_bytes()).hexdigest() != gate["wrapper_sha256"]:
        raise RuntimeError("WRAPPER_CHANGED_AFTER_PREFLIGHT")
    effective = json.loads(path.read_text())
    output = Path(effective["output_dir"])
    if output.exists() or (REPORT / f"{letter}_ATTEMPT_STARTED.json").exists():
        raise RuntimeError("CASE_ALREADY_EXPOSED_NO_RETRY")
    with (REPORT / f"{letter}_ATTEMPT_STARTED.json").open("x") as stream:
        json.dump({"case": letter, "case_id": effective["case_id"], "max_attempts": 1,
                   "effective_sha256": gate["effective_command_sha256"], "monotonic": time.monotonic()}, stream)
    receipt = run_owned(effective["argv"], effective["environment"], effective["cwd"], output, 420, native=True)
    receipt.update(case_id=effective["case_id"], effective_command_sha256=gate["effective_command_sha256"],
                   actual_vehicle_application_independently_verified=None)
    write_json(REPORT / f"{letter}_RUN_RECEIPT.json", receipt)
    print(json.dumps({k: receipt[k] for k in ("case_id", "exit_code", "stop_reason", "total_wall_seconds_including_cleanup", "cleanup_status", "evaluator_or_dummy_pid")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=("A", "B"), required=True)
    execute(parser.parse_args().case)
