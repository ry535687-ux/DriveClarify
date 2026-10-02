"""Stage3C 本地物理显示只读采证；不启动 CARLA，不修改显示配置。"""
import datetime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

REPORT = Path(__file__).resolve().parents[1]


def command(label, argv, env=None):
    started = datetime.datetime.now(datetime.timezone.utc).isoformat()
    try:
        cp = subprocess.run(argv, capture_output=True, text=True, env=env, timeout=15)
        stdout, stderr, code = cp.stdout, cp.stderr, cp.returncode
    except (OSError, subprocess.TimeoutExpired) as exc:
        stdout, stderr, code = "", str(exc), None
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%H%M%S%f")
    stem = label + "_" + stamp
    (REPORT / "logs" / (stem + ".stdout.txt")).write_text(stdout)
    (REPORT / "logs" / (stem + ".stderr.txt")).write_text(stderr)
    row = {"started_at_utc": started, "argv": argv, "exit_code": code,
           "stdout": "logs/" + stem + ".stdout.txt", "stderr": "logs/" + stem + ".stderr.txt"}
    with (REPORT / "logs/COMMANDS.jsonl").open("a") as stream:
        stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    return {**row, "stdout_text": stdout, "stderr_text": stderr}


def properties(text):
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)


def collect():
    identity = json.loads((REPORT / "evidence/ENTRY_IDENTITY.json").read_text())
    if identity["core_status"] != "PASS":
        raise ValueError("STOP_IDENTITY_GATE_NOT_PASSED")
    os_release = Path("/etc/os-release").read_text()
    session_list = command("display_sessions", ["loginctl", "list-sessions", "--no-legend", "--no-pager"])
    seat = command("display_seat0", ["loginctl", "show-seat", "seat0", "-p", "ActiveSession", "-p", "Sessions", "-p", "CanGraphical"])
    sessions = []
    for line in session_list["stdout_text"].splitlines():
        fields = line.split()
        if not fields:
            continue
        result = command("display_session_" + fields[0], ["loginctl", "show-session", fields[0],
                         "-p", "Name", "-p", "User", "-p", "Remote", "-p", "Active", "-p", "Type",
                         "-p", "Class", "-p", "Seat", "-p", "Display", "-p", "Leader", "-p", "Service", "-p", "State"])
        sessions.append({"session_id": fields[0], "properties": properties(result["stdout_text"]), "command": result})
    connectors = []
    for path in sorted(Path("/sys/class/drm").glob("card*-*/status")):
        try:
            connector = {"name": path.parent.name, "status": path.read_text().strip()}
            for key in ("enabled", "modes"):
                source = path.parent / key
                connector[key] = source.read_text().strip() if source.exists() else None
            edid = path.parent / "edid"
            connector["edid_bytes"] = len(edid.read_bytes()) if edid.exists() else None
            connectors.append(connector)
        except OSError as exc:
            connectors.append({"name": path.parent.name, "error": str(exc)})
    process_list = command("display_processes", ["ps", "-eo", "pid=,ppid=,user=,comm=,args="])
    display_processes = []
    for line in process_list["stdout_text"].splitlines():
        fields = line.split(None, 4)
        if len(fields) == 5 and fields[3].lower() in ("xorg", "xwayland", "xvfb", "xvnc", "xtigervnc", "gnome-session-b", "gnome-shell"):
            display_processes.append({"pid": int(fields[0]), "ppid": int(fields[1]), "user": fields[2], "comm": fields[3], "args": fields[4]})
    candidates = [s for s in sessions if all(s["properties"].get(k) == v for k, v in
                  {"Name": "buaa", "Remote": "no", "Active": "yes", "Type": "x11", "Seat": "seat0", "Class": "user"}.items())]
    probes = []
    for session in candidates:
        props = session["properties"]
        display = props.get("Display")
        # loginctl 没有给 DISPLAY 时，只读对应现有本地 Xorg 的 argv；不得猜测远端显示。
        if not display:
            xorg = [p for p in display_processes if p["comm"].lower() == "xorg" and "-seat seat0" in p["args"]]
            matches = [re.search(r"(?:^|\s)(:\d+)(?:\s|$)", p["args"]) for p in xorg]
            found = [match.group(1) for match in matches if match]
            display = found[0] if len(found) == 1 else None
        authority = "/run/user/1000/gdm/Xauthority"
        if not display or not re.fullmatch(r":\d+(?:\.\d+)?", display):
            probes.append({"session_id": session["session_id"], "reason": "SESSION_LOCAL_DISPLAY_NOT_BOUND"})
            continue
        env = dict(os.environ)
        env.update(DISPLAY=display, XAUTHORITY=authority)
        info = command("xdpyinfo", ["xdpyinfo", "-display", display], env)
        randr = command("xrandr", ["xrandr", "--display", display, "--query"], env)
        active_outputs = [line for line in randr["stdout_text"].splitlines()
                          if re.search(r"\bconnected(?: primary)? \d+x\d+\+\d+\+\d+", line)]
        probes.append({"session_id": session["session_id"], "display": display, "xauthority_path": authority,
                       "xauthority_access_via_xdpyinfo": info["exit_code"] == 0,
                       "xdpyinfo": info, "xrandr": randr, "active_outputs": active_outputs})
    connected = [c for c in connectors if c.get("status") == "connected" and c.get("enabled") == "enabled" and c.get("edid_bytes", 0)]
    active_session = properties(seat["stdout_text"]).get("ActiveSession")
    qualified = [p for p in probes if p.get("session_id") == active_session and p.get("xauthority_access_via_xdpyinfo") and p.get("active_outputs")]
    virtual = [p for p in display_processes if p["comm"].lower() in ("xvfb", "xvnc", "xtigervnc")]
    ok = ('ID=ubuntu' in os_release or 'ID="ubuntu"' in os_release) and bool(connected) and bool(qualified) and not virtual
    result = {"captured_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "status": "PASS" if ok else "FAIL",
              "blocker_code": None if ok else "STAGE3C_BLOCKED_NO_VERIFIED_LOCAL_PHYSICAL_DISPLAY",
              "os_release": os_release, "seat0": seat, "sessions": sessions, "drm_connectors": connectors,
              "display_processes": display_processes, "x_access_probes": probes,
              "qualified_local_display": qualified[0] if ok else None,
              "reason": "Ubuntu + 活跃本地seat0/X11 + X认证查询 + 有源输出/物理连接同时成立" if ok else
                        "物理连接、seat0本地活跃会话、DISPLAY绑定及有效X访问未同时确证；不启动CARLA",
              "carla_started": False, "ssh_environment_present": bool(os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY")),
              "transport_note": "执行终端是否远程与CARLA显示目的地分开；不允许X转发，只接受被验证的本地seat0物理显示"}
    (REPORT / "evidence/PHYSICAL_DISPLAY_PREFLIGHT.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: result[k] for k in ("status", "blocker_code", "drm_connectors", "display_processes", "reason")}, ensure_ascii=False, indent=2))
    return result


if __name__ == "__main__":
    collect()
