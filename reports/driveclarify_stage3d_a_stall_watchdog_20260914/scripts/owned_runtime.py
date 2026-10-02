"""Stage3D 修订：总预算包含清理；预留量必须显式绑定 CPU 测量来源。"""
from __future__ import annotations

import ctypes
import datetime
import json
import os
from pathlib import Path
import signal
import subprocess
import threading
import time
import math

HARD_TOTAL_CAP = 420.0


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def write_json(path, value):
    # 只覆盖本轮自己的实时回执临时文件；原stdout/stderr从不改写。
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def proc_row(pid):
    try:
        base = Path("/proc") / str(pid)
        raw = (base / "stat").read_text()
        fields = raw[raw.rfind(")") + 2:].split()
        return {"pid": int(pid), "state": fields[0], "ppid": int(fields[1]), "pgid": int(fields[2]),
                "starttime_ticks": int(fields[19]),
                "argv": (base / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")[:1500]}
    except (OSError, ValueError, IndexError):
        return None


def process_rows():
    return {int(p.name): row for p in Path("/proc").iterdir() if p.name.isdigit()
            and (row := proc_row(int(p.name))) is not None}


def run_owned(command, environment, cwd, output, wall_seconds=HARD_TOTAL_CAP, *,
              cleanup_reserve_seconds, term_grace_seconds, reserve_source, native=False):
    started_utc, started = utc(), time.monotonic()
    if (not all(math.isfinite(v) for v in (wall_seconds, cleanup_reserve_seconds, term_grace_seconds))
            or not 0 < term_grace_seconds < cleanup_reserve_seconds < wall_seconds <= HARD_TOTAL_CAP
            or not isinstance(reserve_source, str) or not reserve_source.strip()):
        raise ValueError("INVALID_OR_UNBOUND_TOTAL_WALL_CONTRACT")
    deadline = started + wall_seconds
    trigger = deadline - cleanup_reserve_seconds
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError("OUTPUT_ALREADY_EXISTS_NO_RETRY:" + str(output))
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    # 本机Python构建未暴露pidfd接口；只复用同一Linux内核接口。
    # 本机 x86_64 /usr/include/x86_64-linux-gnu/asm/unistd_64.h：434/424。
    if os.uname().machine != "x86_64":
        raise RuntimeError("UNVERIFIED_SYSCALL_ARCHITECTURE")
    def open_pidfd(pid):
        fd = libc.syscall(434, pid, 0)
        if fd < 0:
            raise OSError(ctypes.get_errno(), "pidfd_open")
        return fd

    def send_pidfd(fd, sig):
        if libc.syscall(424, fd, int(sig), ctypes.c_void_p(), 0) < 0:
            raise OSError(ctypes.get_errno(), "pidfd_send_signal")

    check_fd = open_pidfd(os.getpid())
    os.close(check_fd)  # 只核验能力，绝不向自身/外部进程发送信号。
    if libc.prctl(36, 1, 0, 0, 0) != 0:  # Linux PR_SET_CHILD_SUBREAPER
        raise OSError(ctypes.get_errno(), "SUBREAPER_SETUP_FAILED")
    output.mkdir(parents=True, exist_ok=False)
    lock = threading.RLock()
    owned, signals = {}, []
    term_sent, kill_sent = set(), set()
    before = set(process_rows())
    process = None
    watchdog = {"triggered": False, "deadline_monotonic": deadline,
                "termination_trigger_monotonic": trigger, "trigger_actual_monotonic": None,
                "term_started_monotonic": None, "kill_started_monotonic": None}
    resource_samples = []
    stop_requested = {"reason": None}
    interrupted = None
    signal_errors = []
    cancel_watchdog = threading.Event()
    termination = {"started": None, "reason": None}

    def discover():
        with lock:
            rows = process_rows()
            parents = {os.getpid()} | {pid for pid, old in owned.items()
                       if pid in rows and rows[pid]["starttime_ticks"] == old["starttime_ticks"]}
            changed = True
            while changed:
                changed = False
                for pid, row in rows.items():
                    if pid not in before and pid != os.getpid() and row["ppid"] in parents and pid not in parents:
                        parents.add(pid)
                        changed = True
            for pid in parents - {os.getpid()}:
                if pid in rows:
                    owned.setdefault(pid, rows[pid])
            return rows

    def signal_owned(sig, reason):
        with lock:
            rows = discover()
            for pid, original in sorted(owned.items(), reverse=True):
                current = rows.get(pid)
                if not current or current["starttime_ticks"] != original["starttime_ticks"] or current["state"] == "Z":
                    continue
                sent = term_sent if sig == signal.SIGTERM else kill_sent
                identity = (pid, original["starttime_ticks"])
                if identity in sent:
                    continue
                descriptor = None
                try:
                    descriptor = open_pidfd(pid)
                    check = proc_row(pid)
                    if check is None or check["starttime_ticks"] != original["starttime_ticks"]:
                        continue
                    send_pidfd(descriptor, sig)
                    sent.add(identity)
                    at = time.monotonic()
                    field = "term_started_monotonic" if sig == signal.SIGTERM else "kill_started_monotonic"
                    if watchdog[field] is None:
                        watchdog[field] = at
                    signals.append({"pid": pid, "starttime_ticks": original["starttime_ticks"],
                                    "signal": sig.name, "reason": reason, "at_monotonic": at})
                except ProcessLookupError:
                    pass
                except OSError as exc:
                    signal_errors.append({"pid": pid, "signal": sig.name, "errno": exc.errno})
                finally:
                    if descriptor is not None:
                        os.close(descriptor)

    def begin_termination(reason):
        with lock:
            if termination["started"] is None:
                termination.update(started=time.monotonic(), reason=reason)
            signal_owned(signal.SIGTERM, reason)

    def timeout():
        if cancel_watchdog.wait(max(0, trigger - time.monotonic())):
            return
        watchdog.update(triggered=True, trigger_actual_monotonic=time.monotonic())
        begin_termination("TOTAL_WALL_RESERVE_TRIGGER")
        if not cancel_watchdog.wait(max(0, termination["started"] + term_grace_seconds - time.monotonic())):
            signal_owned(signal.SIGKILL, "TERM_GRACE_EXPIRED")

    def signal_handler(signum, frame):
        stop_requested["reason"] = "WRAPPER_SIGNAL_" + str(signum)

    previous_handlers = {sig: signal.signal(sig, signal_handler) for sig in (signal.SIGINT, signal.SIGTERM)}
    timer = threading.Thread(target=timeout)
    timer.daemon = True
    timer.start()
    write_json(output / "OWNED_ATTEMPT.json", {"started_utc": started_utc, "started_monotonic": started,
               "deadline_monotonic": deadline, "termination_trigger_monotonic": trigger,
               "cleanup_reserve_seconds": cleanup_reserve_seconds, "term_grace_seconds": term_grace_seconds,
               "reserve_source": reserve_source, "command": command, "cwd": str(cwd), "wrapper_pid": os.getpid(),
               "native": native, "max_attempts": 1, "wall_seconds": wall_seconds})
    last_sample = -float("inf")
    try:
        with (output / "stdout.log").open("xb") as stdout, (output / "stderr.log").open("xb") as stderr:
            if time.monotonic() >= trigger:
                raise RuntimeError("TERMINATION_TRIGGER_BEFORE_SPAWN")
            process = subprocess.Popen(command, cwd=cwd, env=environment, stdout=stdout, stderr=stderr, start_new_session=True)
            row = proc_row(process.pid)
            if row is not None:
                owned[process.pid] = row
            write_json(output / "OWNED_CHILD.json", {"pid": process.pid, "birth_identity": row, "recorded_at_utc": utc(), "command": command})
            while process.poll() is None:
                discover()
                if stop_requested["reason"]:
                    begin_termination(stop_requested["reason"])
                if termination["started"] is not None and time.monotonic() >= termination["started"] + term_grace_seconds:
                    signal_owned(signal.SIGKILL, "TERM_GRACE_EXPIRED")
                if time.monotonic() >= deadline:
                    break
                if native and not watchdog["triggered"] and time.monotonic() - last_sample >= 5:
                    last_sample = time.monotonic()
                    try:
                        sample = subprocess.run(["nvidia-smi", "--query-gpu=index,memory.used,memory.free,utilization.gpu", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=2)
                        mem = next(line for line in Path("/proc/meminfo").read_text().splitlines() if line.startswith("MemAvailable:"))
                        record = {"utc": utc(), "monotonic": time.monotonic(), "nvidia_smi_csv": sample.stdout.strip(), "exit_code": sample.returncode, "MemAvailable": mem, "disk_available_bytes": os.statvfs(output).f_bavail * os.statvfs(output).f_frsize}
                        resource_samples.append(record)
                        with (output / "RESOURCE_SAMPLES.jsonl").open("a") as stream:
                            stream.write(json.dumps(record) + "\n")
                    except (OSError, subprocess.TimeoutExpired) as exc:
                        resource_samples.append({"utc": utc(), "error": str(exc)})
                time.sleep(min(.02, max(.001, deadline - time.monotonic())))
            return_code = process.poll()
    except BaseException as exc:
        interrupted = {"type": type(exc).__name__, "reason": str(exc)}
        return_code = process.poll() if process is not None else None
    finally:
        cancel_watchdog.set()
        timer.join(timeout=max(0, deadline - time.monotonic()))
        cleanup_started = time.monotonic()
        begin_termination(stop_requested["reason"] or "POST_EXIT_OR_EXCEPTION_CLEANUP")
        # 已退出父进程的脱离会话后代由本subreaper接管，仍按PID出生身份清理。
        while True:
            discover()
            signal_owned(signal.SIGTERM, termination["reason"])
            if time.monotonic() >= termination["started"] + term_grace_seconds:
                signal_owned(signal.SIGKILL, "TERM_GRACE_EXPIRED")
            if process is not None:
                process.poll()  # 先由 Popen 回收直接子进程，保留其真实退出码。
            for adopted_pid in list(owned):
                if process is not None and adopted_pid == process.pid:
                    continue
                try:
                    os.waitpid(adopted_pid, os.WNOHANG)
                except ChildProcessError:
                    pass
            rows = discover()
            residual = [row for pid, row in rows.items() if pid in owned and row["starttime_ticks"] == owned[pid]["starttime_ticks"]]
            if not residual or time.monotonic() >= deadline:
                break
            time.sleep(.01)
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)
    ended = time.monotonic()
    receipt = {"start_utc": started_utc, "end_utc": utc(), "start_monotonic": started, "end_monotonic": ended,
               "total_wall_seconds_including_cleanup": ended - started, "wall_budget_seconds": wall_seconds,
               "cleanup_reserve_seconds": cleanup_reserve_seconds, "term_grace_seconds": term_grace_seconds,
               "termination_trigger_seconds": trigger - started, "reserve_source": reserve_source,
               "evaluator_or_dummy_pid": process.pid if process else None,
               "exit_code": process.returncode if process is not None else return_code,
               "watchdog": watchdog, "stop_reason": "WALL_TIMEOUT" if watchdog["triggered"] else (stop_requested["reason"] or ("WRAPPER_EXCEPTION" if interrupted else "CHILD_EXIT")),
               "interrupted": interrupted, "owned_process_birth_identities": list(owned.values()), "signals": signals,
               "signal_errors": signal_errors,
               "termination_to_cleanup_seconds": ended - termination["started"],
               "cleanup_finished_monotonic": ended,
               "exit_classification": ("TOTAL_CAP_OR_CLEANUP_FAILED" if residual or ended - started >= wall_seconds or signal_errors
                                        else "WATCHDOG_KILL" if watchdog["triggered"] and kill_sent
                                        else "WATCHDOG_TERM" if watchdog["triggered"] else "CHILD_EXIT_CLEANED"),
               "strict_total_cap_met_at_receipt_assembly": not residual and ended - started < wall_seconds,
               "receipt_publication_tail": "外部CPU测试另测完整wrapper进程返回；本时间点在最终fsync之前",
               "cleanup_seconds": ended - cleanup_started, "cleanup_status": "PASS" if not residual else "FAIL",
               "residual_owned_processes": residual, "resource_samples": resource_samples, "native": native,
               "carla_apply_control_independently_verified": None}
    write_json(output / "OWNED_RUNTIME_RECEIPT.json", receipt)
    return receipt
