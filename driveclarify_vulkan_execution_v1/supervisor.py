"""Owned-process M1 supervisor for bounded visible CARLA/Vulkan stress."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
import signal
import socket
import subprocess
import tempfile
import time
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
SIMLINGO = Path("/home/buaa/wrh/simlingo")
CARLA = Path("/home/buaa/CARLA_0.9.15")
PYTHON = Path("/home/buaa/anaconda3/envs/simlingo/bin/python")
LEADERBOARD = SIMLINGO / "Bench2Drive/leaderboard"
SCENARIO_RUNNER = SIMLINGO / "Bench2Drive/scenario_runner"
EVALUATOR = LEADERBOARD / "leaderboard/leaderboard_evaluator.py"
SCENARIO_ROOT = ROOT / "driveclarify_paper_mvp_scenarios/leaderboard_scenario_root"
AGENT = Path(__file__).with_name("stress_agent.py")
REPORT_ROOT = Path(os.environ.get(
    "DRIVECLARIFY_RUNTIME_REPORT_ROOT",
    str(ROOT / "reports/driveclarify_r4_2_bounded_vulkan_repair_and_targeted_native_validation"),
)).resolve()
PORTS = (2020, 2021, 8020)
RELEVANT_TOKENS = ("CarlaUE4", "leaderboard_evaluator.py", "scenario_runner.py")
CRASH_MARKERS = (
    "Signal 11 caught.", "CommonUnixCrashHandler: Signal=11",
    "Segmentation fault (core dumped)", "Fatal error:",
)
OLD_CRASH_SYMBOLS = ("FVulkanSurface::EvictSurface", "RHIEndFrame")


def _utc_now():
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def file_sha256(path: Path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@dataclass(frozen=True)
class CycleSpec:
    cycle_id: str
    route: Path
    expected_sha256: str
    phase: str
    seed: int


def _run(argv, env=None):
    return subprocess.run(
        list(argv), check=False, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, env=env,
    )


def _process_rows():
    result = _run(("ps", "-eo", "pid=,ppid=,pgid=,args="))
    rows = []
    for line in result.stdout.splitlines():
        columns = line.strip().split(None, 3)
        if len(columns) != 4:
            continue
        try:
            rows.append((int(columns[0]), int(columns[1]), int(columns[2]), columns[3]))
        except ValueError:
            pass
    return rows


def relevant_processes():
    return [row for row in _process_rows() if any(token in row[3] for token in RELEVANT_TOKENS)]


def descendants(root_pid: int):
    rows = _process_rows()
    selected = {int(root_pid)}
    changed = True
    while changed:
        changed = False
        for pid, ppid, _pgid, _args in rows:
            if ppid in selected and pid not in selected:
                selected.add(pid)
                changed = True
    return [row for row in rows if row[0] in selected]


def port_state():
    listeners = _run(("ss", "-ltnp")).stdout
    result = {}
    for port in PORTS:
        bindable = False
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.bind(("127.0.0.1", port))
                bindable = True
        except OSError:
            pass
        owner_lines = [line for line in listeners.splitlines() if f":{port} " in line]
        result[str(port)] = {"bindable": bindable, "owner_lines": owner_lines}
    return result


def project_gpu_processes():
    query = _run((
        "nvidia-smi", "--query-compute-apps=pid,process_name,used_memory",
        "--format=csv,noheader,nounits",
    ))
    found = []
    for line in query.stdout.splitlines():
        pieces = [piece.strip() for piece in line.split(",")]
        if not pieces or not pieces[0].isdigit():
            continue
        pid = int(pieces[0])
        try:
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
        except OSError:
            cmdline = ""
        if any(token in cmdline for token in (str(ROOT), str(SIMLINGO), str(CARLA), "CarlaUE4")):
            found.append({"pid": pid, "query": line, "cmdline": cmdline})
    return found


def quiescence_snapshot():
    ports = port_state()
    processes = relevant_processes()
    gpu = project_gpu_processes()
    return {
        "observed_at_utc": _utc_now(),
        "relevant_processes": processes,
        "ports": ports,
        "project_gpu_compute_processes": gpu,
        "pass": not processes and not gpu and all(
            row["bindable"] and not row["owner_lines"] for row in ports.values()
        ),
    }


def hold_quiescence(seconds=10.0, interval=0.5, settle_timeout=90.0):
    deadline = time.monotonic() + settle_timeout
    stable_since = None
    samples = []
    while True:
        snapshot = quiescence_snapshot()
        samples.append(snapshot)
        now = time.monotonic()
        if snapshot["pass"]:
            if stable_since is None:
                stable_since = now
            if now - stable_since >= seconds:
                return True, samples
        else:
            stable_since = None
        if now >= deadline:
            return False, samples
        time.sleep(min(interval, deadline - now))


def _environment(spec: CycleSpec, output: Path):
    pythonpath = os.pathsep.join((
        str(LEADERBOARD), str(SCENARIO_RUNNER), str(CARLA / "PythonAPI"),
        str(CARLA / "PythonAPI/carla"),
        str(CARLA / "PythonAPI/carla/dist/carla-0.9.15-py3.8-linux-x86_64.egg"),
        os.environ.get("PYTHONPATH", ""),
    ))
    env = dict(os.environ)
    env.update({
        "DISPLAY": ":1", "XAUTHORITY": "/run/user/1000/gdm/Xauthority",
        "XDG_SESSION_TYPE": "x11", "XDG_SESSION_REMOTE": "false",
        "__NV_PRIME_RENDER_OFFLOAD": "1", "__GLX_VENDOR_LIBRARY_NAME": "nvidia",
        "CARLA_ROOT": str(CARLA), "LEADERBOARD_ROOT": str(LEADERBOARD),
        "SCENARIO_RUNNER_ROOT": str(SCENARIO_ROOT), "PYTHONPATH": pythonpath,
        "ROUTES": str(spec.route), "TEAM_AGENT": str(AGENT),
        "TEAM_CONFIG": str(output), "CHECKPOINT_ENDPOINT": str(output / "leaderboard_results.json"),
        "IS_BENCH2DRIVE": "1", "CUDA_VISIBLE_DEVICES": "",
        "PYTHONUNBUFFERED": "1", "DRIVECLARIFY_VULKAN_CYCLE_ID": spec.cycle_id,
        "DRIVECLARIFY_VULKAN_AGENT_RECEIPT": str(output / "agent_receipt.json"),
        "DRIVECLARIFY_STAGE6A_GENERATED_ROOT": str(ROOT / "driveclarify_paper_mvp_scenarios/generated"),
        "DRIVECLARIFY_STAGE6A_PROMOTION_ROOT": str(ROOT / "artifacts/paper_mvp_stage6a/live_execution_v1"),
        "DRIVECLARIFY_STAGE6A_SELECTED_SEED": str(spec.seed),
    })
    return env


def _command(spec: CycleSpec, output: Path):
    return (
        str(PYTHON), "-u", str(EVALUATOR), "--routes=" + str(spec.route),
        "--repetitions=1", "--track=SENSORS",
        "--checkpoint=" + str(output / "leaderboard_results.json"),
        "--debug-checkpoint=" + str(output / "leaderboard_debug.txt"),
        "--agent=" + str(AGENT), "--agent-config=" + str(output),
        "--debug=0", "--timeout=600", "--port=2020",
        "--traffic-manager-port=8020", "--traffic-manager-seed=" + str(spec.seed), "--gpu-rank=0",
    )


def _window_snapshot():
    result = _run(("xwininfo", "-root", "-tree"), env={**os.environ, "DISPLAY": ":1", "XAUTHORITY": "/run/user/1000/gdm/Xauthority"})
    return [line.strip() for line in result.stdout.splitlines() if "Carla" in line or "CARLA" in line]


def _journal_cursor():
    result = _run(("journalctl", "-k", "-n", "0", "--show-cursor", "--no-pager"))
    cursor = None
    for line in result.stdout.splitlines():
        if line.startswith("-- cursor:"):
            cursor = line.split(":", 1)[1].strip()
    return {"returncode": result.returncode, "cursor": cursor, "output": result.stdout[-1000:]}


def _journal_after(cursor):
    if not cursor:
        return {"available": False, "xid_count": None, "oom_count": None, "lines": []}
    result = _run(("journalctl", "-k", "--after-cursor", cursor, "--no-pager"))
    lines = result.stdout.splitlines()
    return {
        "available": result.returncode == 0,
        "xid_count": sum("NVRM: Xid" in line for line in lines),
        "oom_count": sum("oom-kill" in line.lower() or "out of memory" in line.lower() for line in lines),
        "lines": [line for line in lines if "NVRM" in line or "oom" in line.lower()],
    }


def run_cycle(spec: CycleSpec, *, quiet_seconds=10.0, timeout_seconds=180.0):
    if file_sha256(spec.route) != spec.expected_sha256:
        raise RuntimeError("PREREGISTERED_ROUTE_HASH_MISMATCH:" + spec.cycle_id)
    output = REPORT_ROOT / "cycles" / spec.cycle_id
    output.mkdir(parents=True, exist_ok=False)
    pre_pass, pre_samples = hold_quiescence(quiet_seconds)
    if not pre_pass:
        raise RuntimeError("PRELAUNCH_QUIESCENCE_FAILED:" + spec.cycle_id)
    journal_start = _journal_cursor()
    log_path = output / "evaluator.log"
    command = _command(spec, output)
    started = _utc_now()
    observed_pids = set()
    observed_pgids = set()
    windows = []
    with log_path.open("wb") as log:
        process = subprocess.Popen(
            command, cwd=str(ROOT), env=_environment(spec, output), stdout=log,
            stderr=subprocess.STDOUT, start_new_session=True,
        )
        observed_pids.add(process.pid)
        observed_pgids.add(process.pid)
        deadline = time.monotonic() + timeout_seconds
        while process.poll() is None and time.monotonic() < deadline:
            for pid, _ppid, pgid, _args in descendants(process.pid):
                observed_pids.add(pid)
                observed_pgids.add(pgid)
            window = _window_snapshot()
            if window and window not in windows:
                windows.append(window)
            time.sleep(0.5)
        timed_out = process.poll() is None
        escalation = []
        if timed_out:
            os.killpg(process.pid, signal.SIGTERM)
            escalation.append("SIGTERM_EVALUATOR_PGID")
            try:
                process.wait(10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                escalation.append("SIGKILL_EVALUATOR_PGID")
                process.wait(10)
        returncode = process.returncode

    post_pass, post_samples = hold_quiescence(quiet_seconds)
    agent_path = output / "agent_receipt.json"
    agent = json.loads(agent_path.read_text(encoding="utf-8")) if agent_path.is_file() else {}
    log_text = log_path.read_text(encoding="utf-8", errors="replace")
    crash_markers = [marker for marker in CRASH_MARKERS if marker in log_text]
    old_symbols = [marker for marker in OLD_CRASH_SYMBOLS if marker in log_text]
    journal = _journal_after(journal_start["cursor"])
    receipt = {
        "schema_version": "driveclarify.r4_2.vulkan_stress_cycle_receipt.v1",
        "cycle_id": spec.cycle_id, "phase": spec.phase,
        "started_at_utc": started, "finished_at_utc": _utc_now(),
        "route": str(spec.route), "route_sha256": file_sha256(spec.route),
        "command": list(command), "physical_x11": True, "display": ":1",
        "headless": False, "render_off_screen": False,
        "evaluator_pid": process.pid, "observed_pids": sorted(observed_pids),
        "observed_pgids": sorted(observed_pgids), "fresh_carla_process": True,
        "window_evidence": windows, "agent": agent, "returncode": returncode,
        "timed_out": timed_out, "escalation": escalation,
        "crash_markers": crash_markers, "old_crash_symbols": old_symbols,
        "old_fingerprint_count": int(bool(old_symbols and crash_markers)),
        "signal_11_count": sum("Signal 11" in marker for marker in crash_markers),
        "other_crash_count": sum("Signal 11" not in marker for marker in crash_markers),
        "journal": journal, "pre_quiescence_samples": pre_samples,
        "post_quiescence_samples": post_samples,
        "normal_shutdown": not timed_out and not escalation,
        "cleanup_pass": post_pass,
        "pass": bool(
            agent.get("first_durable_observation")
            and agent.get("required_duration_reached")
            and not timed_out and not escalation and not crash_markers
            and post_pass and journal.get("xid_count") in (0, None)
            and journal.get("oom_count") in (0, None)
        ),
        "scientific_counts": {
            "official_model_forward": 0, "candidate_forward": 0,
            "driveclarify_decision": 0, "scientific_pid": 0,
            "paper_metric": 0, "checkpoint_load": 0, "cuda_context": 0,
        },
    }
    atomic_json(output / "cycle_receipt.json", receipt)
    with (REPORT_ROOT / "VULKAN_STRESS_CYCLE_RECEIPTS.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(receipt, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return receipt


def run_batch(specs: Iterable[CycleSpec], *, quiet_seconds=10.0):
    receipts = []
    for spec in specs:
        receipt = run_cycle(spec, quiet_seconds=quiet_seconds)
        receipts.append(receipt)
        if not receipt["pass"]:
            break
    return receipts
