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


