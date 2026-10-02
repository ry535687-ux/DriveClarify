#!/usr/bin/env python3
"""Write the final non-destructive Temporal Grounding V1 cleanup receipt."""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/temporal_grounding_v1/TEMPORAL_GROUNDING_V1_FINAL_CLEANUP_RECEIPT.json"
PROCESS_MARKERS = {
    "carla": ("CarlaUE4",),
    "scenario_runner": ("scenario_runner.py", "scenario_runner"),
    "evaluator": ("leaderboard_evaluator.py",),
    "temporal_runner": ("run_temporal_grounding_v1_live.py",),
    "visualizer": ("driveclarify_visualization", "DriveClarify Stage 6A/6B Live Runtime"),
    "tracking_worker": ("bytetrack_worker", "tracking_worker"),
    "grounding_dino_worker": ("grounding_dino_worker", "GroundingDINOWorker"),
}


def process_rows() -> List[Dict[str, Any]]:
    excluded = {os.getpid(), os.getppid()}
    rows = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) in excluded:
            continue
        try:
            command = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace").strip()
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        if not command or "audit_temporal_grounding_v1_cleanup.py" in command:
            continue
        matched = [name for name, markers in PROCESS_MARKERS.items() if any(marker in command for marker in markers)]
        if matched:
            rows.append({"pid": int(entry.name), "categories": matched, "command": command})
    return rows


def port_rows() -> List[str]:
    completed = subprocess.run(
        ["ss", "-H", "-ltnp"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False
    )
    selected = []
    for line in completed.stdout.splitlines():
        fields = line.split()
        if any(field.endswith(":{}".format(port)) for field in fields for port in (2020, 2021, 8020)):
            selected.append(line)
    return selected


def gpu_rows() -> List[Dict[str, Any]]:
    completed = subprocess.run(
        ["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory", "--format=csv,noheader,nounits"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    rows = []
    for line in completed.stdout.splitlines():
        parts = [item.strip() for item in line.split(",", 2)]
        if len(parts) != 3 or not parts[0].isdigit():
            continue
        pid = int(parts[0])
        try:
            command = (Path("/proc") / str(pid) / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
            cwd = os.readlink("/proc/{}/cwd".format(pid))
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            command, cwd = "", ""
        if any(marker in (command + " " + cwd) for marker in (str(ROOT), "/home/buaa/wrh/simlingo", "/home/buaa/CARLA_0.9.15")):
            rows.append({"pid": pid, "process_name": parts[1], "used_memory_mib": parts[2], "command": command, "cwd": cwd})
    return rows


def main() -> int:
    processes, ports, gpu = process_rows(), port_rows(), gpu_rows()
    counts = {name: 0 for name in PROCESS_MARKERS}
    for row in processes:
        for category in row["categories"]:
            counts[category] += 1
    passed = not processes and not ports and not gpu
    receipt = {
        "schema_version": "driveclarify.temporal_grounding_v1.final_cleanup.v1",
        "observed_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": "PASS" if passed else "BLOCKED_TEMPORAL_GROUNDING_V1_CLEANUP",
        "process_counts": counts,
        "matched_processes": processes,
        "ports": {"2020_2021_8020_listener_count": len(ports), "listeners": ports},
        "project_gpu_process_count": len(gpu),
        "project_gpu_processes": gpu,
        "destructive_cleanup_performed": False,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT.with_name(OUTPUT.name + ".tmp")
    temporary.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(OUTPUT)
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
