"""每一臂重新采证并冻结本轮有效参数；本脚本不启动驾驶。"""
import argparse
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import sys
import time

from display_preflight import command, properties
from owned_runtime import process_rows
from resource_preflight import collect as collect_resources

REPORT = Path(__file__).resolve().parents[1]
ROOT = REPORT.parents[1]
DEV = ROOT / "experiments/driveclarify_native_clear_backend_dev_20260913"


def save(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def hash_file(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def display_now(letter):
    seat = command(letter + "_seat", ["loginctl", "show-seat", "seat0", "-p", "ActiveSession"])
    session_id = properties(seat["stdout_text"]).get("ActiveSession")
    if not session_id:
        return {"status": "FAIL", "reason": "NO_ACTIVE_SEAT0_SESSION"}
    session = command(letter + "_session", ["loginctl", "show-session", session_id, "-p", "Name", "-p", "Remote", "-p", "Active", "-p", "Type", "-p", "Seat"])
    props = properties(session["stdout_text"])
    env_binding = None
    for pid, row in process_rows().items():
        if "gnome-session-binary" not in row["argv"]:
            continue
        try:
            values = {}
            for item in (Path("/proc") / str(pid) / "environ").read_bytes().split(b"\0"):
                key, _, value = item.partition(b"=")
                if key in (b"DISPLAY", b"XAUTHORITY", b"XDG_SESSION_ID", b"XDG_SEAT"):
                    values[key.decode()] = value.decode()
            if values.get("XDG_SESSION_ID") == session_id and values.get("XDG_SEAT") == "seat0":
                env_binding = {"pid": pid, **values}
                break
        except OSError:
            continue
    local = all(props.get(k) == v for k, v in {"Name": "buaa", "Remote": "no", "Active": "yes", "Type": "x11", "Seat": "seat0"}.items())
    if not local or not env_binding or not re.fullmatch(r":\d+(?:\.\d+)?", env_binding.get("DISPLAY", "")):
        return {"status": "FAIL", "reason": "NO_BOUND_LOCAL_X11", "session": session, "binding": env_binding}
    env = dict(os.environ)
    env.update(DISPLAY=env_binding["DISPLAY"], XAUTHORITY=env_binding["XAUTHORITY"])
    info = command(letter + "_xdpyinfo", ["xdpyinfo", "-display", env["DISPLAY"]], env)
    randr = command(letter + "_xrandr", ["xrandr", "--display", env["DISPLAY"], "--props"], env)
    power = command(letter + "_xset", ["xset", "-display", env["DISPLAY"], "q"], env)
    outputs, current = {}, None
    reading = False
    for line in randr["stdout_text"].splitlines():
        if line and not line[0].isspace():
            current = line.split()[0]
            outputs[current] = {"line": line, "edid_hex": ""}
            reading = False
        elif line.strip() == "EDID:":
            reading = True
        elif reading and re.fullmatch(r"[0-9a-fA-F]{32}", line.strip()):
            outputs[current]["edid_hex"] += line.strip().lower()
        elif reading:
            reading = False
    matches = []
    for name, output in outputs.items():
        if not re.search(r"\bconnected(?: primary)? \d+x\d+\+\d+\+\d+", output["line"]) or not output["edid_hex"]:
            continue
        for path in Path("/sys/class/drm").glob("card*-*/edid"):
            if path.read_bytes().hex() == output["edid_hex"] and (path.parent / "status").read_text().strip() == "connected":
                matches.append({"x_output": name, "drm_connector": path.parent.name, "edid_sha256": hash_file(path), "active_mode": output["line"]})
    passed = local and bool(matches) and info["exit_code"] == 0 and power["exit_code"] == 0 and "Monitor is On" in power["stdout_text"]
    return {"status": "PASS" if passed else "FAIL", "binding": env_binding, "physical_matches": matches,
            "session": session, "xdpyinfo": info, "xrandr": randr, "xset": power}


def prepare(letter):
    gate = {"case": letter, "created_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "created_monotonic": time.monotonic(), "user_authorized": True, "native_started": False}
    entry = json.loads((REPORT / "evidence/ENTRY_IDENTITY.json").read_text())
    b = json.loads((DEV / "configs/BACKEND_IDENTITY.json").read_text())
    # checkpoint只复用本轮一次摘要及未变的文件出生/时间/大小；不再hash 2.57GB。
    checkpoint = next(c for c in entry["core_checks"] if c["name"] == "checkpoint")
    stat = Path(checkpoint["path"]).stat()
    unchanged = {"device": stat.st_dev, "inode": stat.st_ino, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "ctime_ns": stat.st_ctime_ns} == checkpoint["stat_after"]
    git_same = True
    for name, repo in (("DriveClarify", ROOT), ("SimLingo", Path(b["simlingo_root"]))):
        for key, args in (("DIFF", ["diff", "--binary"]), ("STAGED", ["diff", "--cached", "--binary"]), ("HEAD", ["rev-parse", "HEAD"])):
            result = command(letter + "_integrity_" + name + "_" + key, ["git", "-C", str(repo), *args])
            git_same &= result["exit_code"] == 0 and hashlib.sha256(result["stdout_text"].encode()).hexdigest() == entry["repositories"][name][key]["sha256"]
    gate["identity"] = "PASS" if unchanged and git_same and entry["core_status"] == "PASS" else "FAIL"
    if gate["identity"] != "PASS":
        save(REPORT / f"{letter}_PREFLIGHT_GATE.json", gate)
        return False
    display = display_now(letter)
    save(REPORT / "evidence" / f"{letter}_FINAL_DISPLAY.json", display)
    gate["physical_display"] = display["status"]
    if display["status"] != "PASS":
        save(REPORT / f"{letter}_PREFLIGHT_GATE.json", gate)
        return False
    resources = collect_resources("FINAL_BEFORE_" + letter)
    # 只允许已经辨明的小型桌面会话；任何新增compute占用交由停止后的复核，不杀进程。
    compute_lines = [line for line in resources["commands"]["compute"]["stdout_text"].splitlines() if line.strip()]
    approved_existing_compute = all("/opt/todesk/bin/ToDesk_Session" in line for line in compute_lines)
    commands_ok = all(v["exit_code"] == 0 for v in resources["commands"].values())
    # 数值资源下限不编造；本次人工复核过的当前余量保留原始快照。
    gate["resource"] = "PASS" if commands_ok and approved_existing_compute else "FAIL"
    gate["process_port"] = "PASS" if commands_ok and not resources["conflicting_native_processes"] and not resources["requested_port_occupancy"] else "FAIL"
    gate["output_isolation"] = "PASS" if not resources["output_dirs_exist"][letter] else "FAIL"
    tests = json.loads((REPORT / "evidence/WRAPPER_CPU_CHECKS.json").read_text())
    gate["wall_guard"] = "PASS" if tests["status"] == "PASS" else "FAIL"
    if letter == "B":
        cleanup = json.loads((REPORT / "A_CLEANUP_VERIFICATION.json").read_text())
        gate["previous_cleanup"] = cleanup["status"]
    if any(v == "FAIL" for v in gate.values()):
        save(REPORT / f"{letter}_PREFLIGHT_GATE.json", gate)
        return False
    spec = importlib.util.spec_from_file_location("stage3b_dry", DEV / "dry_run.py")
    dry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dry)
    effective = dry.prepare(DEV / f"configs/case_{letter}.json")
    effective["environment"]["DISPLAY"] = display["binding"]["DISPLAY"]
    effective["environment"]["XAUTHORITY"] = display["binding"]["XAUTHORITY"]
    effective["stage3c_authorization_source_sha256"] = entry["authorization_sha256"]
    effective["stage3c_wall_seconds"] = 420
    effective["stage3c_wrapper_sha256"] = hash_file(REPORT / "scripts/owned_runtime.py")
    # 原Stage3B的launch_allowed=false是该dry工具的界限；本轮授权由独立gate和明确提示词给出。
    save(REPORT / f"{letter}_EFFECTIVE_COMMAND.json", effective)
    save(REPORT / f"{letter}_EFFECTIVE_ENV.json", effective["environment"])
    (REPORT / f"{letter}_EFFECTIVE_COMMAND.txt").write_text("cwd=" + effective["cwd"] + "\n" + shlex.join(effective["argv"]) + "\n环境见同名 EFFECTIVE_ENV.json；必须经本轮420s owned wrapper。\n")
    gate.update(status="PASS", approved_wall_seconds=420, effective_command_sha256=hash_file(REPORT / f"{letter}_EFFECTIVE_COMMAND.json"), wrapper_sha256=effective["stage3c_wrapper_sha256"])
    save(REPORT / f"{letter}_PREFLIGHT_GATE.json", gate)
    print(json.dumps(gate, ensure_ascii=False, indent=2))
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=("A", "B"), required=True)
    sys.exit(0 if prepare(parser.parse_args().case) else 3)
