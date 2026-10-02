"""Native Ubuntu physical-display preflight; never launches CARLA itself."""

from __future__ import annotations

import os
import platform
import re
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class NativeDisplayFacts:
    linux: bool
    ubuntu: bool
    display: str | None
    session_type: str | None
    remote_session: bool | None
    xdpyinfo_ok: bool
    x_server_vendor: str | None
    connected_outputs: tuple[str, ...]
    process_command_lines: tuple[str, ...]
    launch_arguments: tuple[str, ...]
    launch_environment: Mapping[str, str]
    carla_executable: bool
    leaderboard_evaluator_present: bool
    checkpoint_present: bool
    carla_port_free: bool
    traffic_manager_port_free: bool


@dataclass(frozen=True)
class NativeDisplayPreflightResult:
    passed: bool
    blocker_codes: tuple[str, ...]
    evidence: Mapping[str, Any]


_FORBIDDEN_TOKENS = (
    "renderoffscreen",
    "-nullrhi",
    "headless",
    "xvfb",
    "x11vnc",
    "vncserver",
    "tigervnc",
    "tightvnc",
    "novnc",
)
_PHYSICAL_OUTPUT = re.compile(r"^(DP|HDMI|DVI|eDP|LVDS|VGA)-?[A-Za-z0-9]*$")


def evaluate_native_display_preflight(
    facts: NativeDisplayFacts,
) -> NativeDisplayPreflightResult:
    """Evaluate fail-closed native-display requirements from captured facts."""

    blockers: list[str] = []
    if not facts.linux or not facts.ubuntu:
        blockers.append("NATIVE_UBUNTU_REQUIRED")
    if not facts.display:
        blockers.append("DISPLAY_ENVIRONMENT_MISSING")
    if (facts.session_type or "").lower() != "x11":
        blockers.append("NATIVE_X11_SESSION_REQUIRED")
    if facts.remote_session is not False:
        blockers.append("LOCAL_NONREMOTE_SESSION_NOT_PROVEN")
    if not facts.xdpyinfo_ok:
        blockers.append("X_DISPLAY_UNREACHABLE")
    physical = tuple(
        output for output in facts.connected_outputs if _PHYSICAL_OUTPUT.fullmatch(output)
    )
    if not physical:
        blockers.append("PHYSICAL_DISPLAY_OUTPUT_NOT_CONNECTED")

    process_text = "\n".join(facts.process_command_lines).lower()
    launch_text = "\n".join(facts.launch_arguments).lower()
    environment_text = "\n".join(
        f"{key}={value}" for key, value in sorted(facts.launch_environment.items())
    ).lower()
    for token in _FORBIDDEN_TOKENS:
        if token in process_text:
            blockers.append("FORBIDDEN_DISPLAY_PROCESS_PRESENT")
            break
    for token in _FORBIDDEN_TOKENS:
        if token in launch_text or token in environment_text:
            blockers.append("FORBIDDEN_HEADLESS_OR_REMOTE_LAUNCH_OPTION")
            break
    if facts.launch_environment.get("SDL_VIDEODRIVER", "").lower() in {
        "dummy",
        "offscreen",
    }:
        blockers.append("SDL_OFFSCREEN_DRIVER_FORBIDDEN")
    if not facts.carla_executable:
        blockers.append("CARLA_EXECUTABLE_UNAVAILABLE")
    if not facts.leaderboard_evaluator_present:
        blockers.append("LEADERBOARD_EVALUATOR_UNAVAILABLE")
    if not facts.checkpoint_present:
        blockers.append("SIMLINGO_CHECKPOINT_UNAVAILABLE")
    if not facts.carla_port_free:
        blockers.append("CARLA_PORT_NOT_FREE")
    if not facts.traffic_manager_port_free:
        blockers.append("TRAFFIC_MANAGER_PORT_NOT_FREE")
    unique = tuple(dict.fromkeys(blockers))
    return NativeDisplayPreflightResult(
        passed=not unique,
        blocker_codes=unique,
        evidence={
            "display": facts.display,
            "session_type": facts.session_type,
            "remote_session": facts.remote_session,
            "x_server_vendor": facts.x_server_vendor,
            "connected_outputs": list(facts.connected_outputs),
            "physical_outputs": list(physical),
            "forbidden_tokens": list(_FORBIDDEN_TOKENS),
            "native_visualization_required": True,
        },
    )


def _run_read_only(command: Sequence[str]) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            list(command),
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False, ""
    return result.returncode == 0, result.stdout


def _port_free(port: int) -> bool:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        probe.close()


def collect_native_display_facts(
    *,
    launch_arguments: Sequence[str],
    launch_environment: Mapping[str, str],
    carla_executable: Path,
    leaderboard_evaluator: Path,
    checkpoint: Path,
    carla_port: int = 2020,
    traffic_manager_port: int = 8020,
) -> NativeDisplayFacts:
    """Capture read-only facts immediately before launch; caller must retain them."""

    display = launch_environment.get("DISPLAY", os.environ.get("DISPLAY"))
    xdpy_ok, xdpy = _run_read_only(["xdpyinfo", "-display", display or ""])
    _, xrandr = _run_read_only(["xrandr", "--display", display or "", "--query"])
    _, processes = _run_read_only(["ps", "-eo", "args="])
    connected = []
    for line in xrandr.splitlines():
        match = re.match(r"^(\S+)\s+connected(?:\s|$)", line)
        if match:
            connected.append(match.group(1))
    vendor_match = re.search(r"^vendor string:\s*(.+)$", xdpy, re.MULTILINE)
    os_release = Path("/etc/os-release")
    release_text = os_release.read_text(encoding="utf-8", errors="replace") if os_release.is_file() else ""
    remote_raw = launch_environment.get("XDG_SESSION_REMOTE", os.environ.get("XDG_SESSION_REMOTE"))
    remote = None if remote_raw is None else remote_raw.lower() in {"1", "true", "yes"}
    return NativeDisplayFacts(
        linux=platform.system() == "Linux",
        ubuntu="ID=ubuntu" in release_text or 'ID="ubuntu"' in release_text,
        display=display,
        session_type=launch_environment.get(
            "XDG_SESSION_TYPE", os.environ.get("XDG_SESSION_TYPE")
        ),
        remote_session=remote,
        xdpyinfo_ok=xdpy_ok,
        x_server_vendor=vendor_match.group(1).strip() if vendor_match else None,
        connected_outputs=tuple(connected),
        process_command_lines=tuple(processes.splitlines()),
        launch_arguments=tuple(launch_arguments),
        launch_environment=dict(launch_environment),
        carla_executable=carla_executable.is_file() and os.access(carla_executable, os.X_OK),
        leaderboard_evaluator_present=leaderboard_evaluator.is_file(),
        checkpoint_present=checkpoint.is_file(),
        carla_port_free=_port_free(carla_port),
        traffic_manager_port_free=_port_free(traffic_manager_port),
    )
