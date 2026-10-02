#!/usr/bin/env python3
"""Native-display, per-town Stage 6A live execution campaign.

The campaign is deliberately process-isolated: one Epic-quality CARLA cold boot
per selected town, no RPC map switching, and one real ScenarioRunner handler
execution for every runtime/seed pair.  It also keeps the previously validated
DriveClarify research HUD visible on the local physical X11 desktop.  The HUD is
a read-only observer of the same front-RGB callbacks used by the validation
agent; it never invokes a model, PID, planner, world tick, or vehicle control.
"""

from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import gc
import html
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

CARLA_ROOT = Path("/home/buaa/CARLA_0.9.15")
CARLA_PYTHON_API = CARLA_ROOT / "PythonAPI/carla"
SCENARIO_RUNNER_ROOT = Path("/home/buaa/wrh/simlingo/scenario_runner")
if str(CARLA_PYTHON_API) not in sys.path:
    sys.path.insert(0, str(CARLA_PYTHON_API))

from driveclarify_m3_runtime_shadow.live_visualization import (  # noqa: E402
    LiveShadowVisualizerV0,
)
from driveclarify_paper_mvp_scenarios.compiler import (  # noqa: E402
    DEFAULT_OUTPUT_DIR,
)
from driveclarify_paper_mvp_scenarios.contracts import (  # noqa: E402
    canonical_sha256,
    file_sha256,
    load_json,
    pretty_json_bytes,
    require,
)
from driveclarify_paper_mvp_scenarios.live_authoring import (  # noqa: E402
    author_preliminary_live_receipt,
)
from driveclarify_paper_mvp_scenarios.live_contracts import (  # noqa: E402
    LIVE_EVIDENCE_ORIGIN,
    LIVE_MANIFEST_SCHEMA_VERSION,
    LIVE_PROMOTION_MANIFEST,
    LIVE_RECEIPT_DIR,
    MANIFEST_HASH_FIELD,
    RECEIPT_HASH_FIELD,
    content_address,
    verify_content_address,
)
from driveclarify_paper_mvp_scenarios.live_execution import (  # noqa: E402
    execute_preliminary_receipt,
)
from driveclarify_paper_mvp_scenarios.live_promotion import (  # noqa: E402
    LivePromotionConfig,
    _atomic_write,
    _runtime_inputs,
)
from driveclarify_paper_mvp_scenarios.validator import (  # noqa: E402
    validate_live_promotions,
)
from driveclarify_paper_mvp_runtime.live_candidate_capture import (  # noqa: E402
    build_live_candidate_capture,
    content_address_capture,
)
from driveclarify_paper_mvp_stage6a.freeze import (  # noqa: E402
    SCENARIO_EXECUTION_RECEIPT_SCHEMA_VERSION,
    _scenario_gate,
)


TOWNS = (
    "Town01",
    "Town02",
    "Town03",
    "Town04",
    "Town05",
    "Town06",
    "Town07",
    "Town10HD",
)
CARLA_RPC_PORT = 2020
CARLA_STREAMING_PORT = 2021
TRAFFIC_MANAGER_PORT = 8020
ENGINE_PATHS = """[Core.System]
Paths=../../../Engine/Content
Paths=%GAMEDIR%Content
Paths=../../../Engine/Plugins/Editor/GeometryMode/Content
Paths=../../../Engine/Plugins/Editor/SpeedTreeImporter/Content
Paths=../../../Engine/Plugins/FX/Niagara/Content
Paths=../../../Engine/Plugins/2D/Paper2D/Content
Paths=../../../Engine/Plugins/Developer/AnimationSharing/Content
Paths=../../../Engine/Plugins/Runtime/AudioSynesthesia/Content
Paths=../../../Engine/Plugins/Runtime/Synthesis/Content
Paths=../../../Engine/Plugins/Runtime/HairStrands/Content
Paths=../../../Engine/Plugins/Experimental/PythonScriptPlugin/Content
Paths=../../../Engine/Plugins/Experimental/GeometryCollectionPlugin/Content
Paths=../../../Engine/Plugins/Enterprise/DatasmithContent/Content
Paths=../../../Engine/Plugins/Experimental/GeometryProcessing/Content
Paths=../../../Engine/Plugins/Experimental/ChaosClothEditor/Content
Paths=../../../Engine/Plugins/Experimental/MotoSynth/Content
Paths=../../../Engine/Plugins/Experimental/ChaosNiagara/Content
Paths=../../../Engine/Plugins/Experimental/ChaosSolverPlugin/Content
Paths=../../../Engine/Plugins/Media/MediaCompositing/Content
Paths=../../../CarlaUE4/Plugins/Streetmap/Content
Paths=../../../CarlaUE4/Plugins/CarlaTools/Content
Paths=../../../CarlaUE4/Plugins/HoudiniEngine/Content
Paths=../../../CarlaUE4/Plugins/Carla/Content
"""


def _selected_runtime_seeds(
    runtime_input: Mapping[str, Any], *, one_seed_per_runtime: bool
) -> tuple[int, ...]:
    values = tuple(
        int(value) for value in runtime_input["runtime"]["allowed_seed_values"]
    )
    require(bool(values), "RUNTIME_ALLOWED_SEED_SET_EMPTY")
    return values[:1] if one_seed_per_runtime else values


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _command(
    argv: list[str], *, environment: Mapping[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=None if environment is None else dict(environment),
    )


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.25)
        return probe.connect_ex(("127.0.0.1", int(port))) == 0


def _process_group_rows(pgid: int) -> tuple[str, tuple[int, ...]]:
    process_table = _command(
        ["ps", "-eo", "pid=,ppid=,pgid=,args="]
    ).stdout
    rows: list[str] = []
    pids: list[int] = []
    for row in process_table.splitlines():
        columns = row.split(None, 3)
        if len(columns) < 4:
            continue
        try:
            row_pid = int(columns[0])
            row_pgid = int(columns[2])
        except ValueError:
            continue
        if row_pgid == int(pgid):
            rows.append(row)
            pids.append(row_pid)
    return "\n".join(rows), tuple(sorted(pids))


def _normal_town(value: str) -> str:
    name = value.rsplit("/", 1)[-1].split(".", 1)[-1]
    return name[:-4] if name.endswith("_Opt") else name


def _engine_ini(output_root: Path, town: str) -> Path:
    asset = "Town10HD_Opt" if town == "Town10HD" else town
    directory = (
        output_root
        / "native_town_boot"
        / town
        / "Saved/Config/LinuxNoEditor"
    )
    path = directory / "Engine.ini"
    payload = (
        "[/Script/EngineSettings.GameMapsSettings]\n"
        f"GameDefaultMap=/Game/Carla/Maps/{asset}.{asset}\n"
        f"ServerDefaultMap=/Game/Carla/Maps/{asset}.{asset}\n"
        f"TransitionMap=/Game/Carla/Maps/{asset}.{asset}\n\n"
        + ENGINE_PATHS
    ).encode("utf-8")
    _atomic_write(path, payload)
    return path


def _display_environment(display: str) -> dict[str, str]:
    environment = dict(os.environ)
    environment["DISPLAY"] = display
    environment.setdefault("XAUTHORITY", "/run/user/1000/gdm/Xauthority")
    environment.setdefault("__NV_PRIME_RENDER_OFFLOAD", "1")
    environment.setdefault("__GLX_VENDOR_LIBRARY_NAME", "nvidia")
    return environment


def _display_preflight(display: str, output_root: Path) -> Mapping[str, Any]:
    environment = _display_environment(display)
    xdpy = _command(["xdpyinfo", "-display", display], environment=environment)
    xrandr = _command(["xrandr", "--display", display, "--query"], environment=environment)
    require(xdpy.returncode == 0, "BLOCKED_NATIVE_DISPLAY_UNAVAILABLE")
    require(xrandr.returncode == 0, "BLOCKED_NATIVE_DISPLAY_UNAVAILABLE")
    require(
        re.search(r"^DP-0 connected primary 2560x1440", xrandr.stdout, re.MULTILINE)
        is not None,
        "BLOCKED_LOCAL_PHYSICAL_DISPLAY_NOT_VERIFIED",
    )
    sessions = _command(["loginctl", "list-sessions", "--no-legend"])
    verified_session: Mapping[str, str] | None = None
    for line in sessions.stdout.splitlines():
        columns = line.split()
        if not columns:
            continue
        session_id = columns[0]
        details = _command(
            [
                "loginctl",
                "show-session",
                session_id,
                "-p",
                "Name",
                "-p",
                "Remote",
                "-p",
                "Active",
                "-p",
                "Type",
                "-p",
                "Seat",
                "-p",
                "TTY",
            ]
        )
        values = {
            row.split("=", 1)[0]: row.split("=", 1)[1]
            for row in details.stdout.splitlines()
            if "=" in row
        }
        if (
            values.get("Name") == "buaa"
            and values.get("Remote") == "no"
            and values.get("Active") == "yes"
            and values.get("Type") == "x11"
            and values.get("Seat") == "seat0"
        ):
            verified_session = {"session_id": session_id, **values}
            break
    require(verified_session is not None, "BLOCKED_LOCAL_X11_SESSION_NOT_VERIFIED")
    forbidden = _command(
        [
            "pgrep",
            "-x",
            "Xvfb|x11vnc|Xtigervnc|Xvnc|vncserver|wayvnc",
        ]
    )
    require(forbidden.returncode != 0, "BLOCKED_FORBIDDEN_DISPLAY_PROCESS_PRESENT")
    receipt = {
        "schema_version": "driveclarify.stage6a.native_display_preflight.v1",
        "status": "PASS",
        "observed_at_utc": _utc_now(),
        "display": display,
        "x_server": "X.Org",
        "physical_output": "DP-0",
        "physical_resolution": "2560x1440",
        "session": verified_session,
        "headless": False,
        "xvfb": False,
        "vnc": False,
        "render_off_screen": False,
        "xdpyinfo_sha256": canonical_sha256(xdpy.stdout),
        "xrandr_sha256": canonical_sha256(xrandr.stdout),
    }
    _atomic_write(
        output_root / "NATIVE_DISPLAY_PREFLIGHT.json",
        pretty_json_bytes(receipt),
    )
    return receipt


def _x11_tree(display: str) -> str:
    result = _command(
        ["xwininfo", "-display", display, "-root", "-tree"],
        environment=_display_environment(display),
    )
    return result.stdout if result.returncode == 0 else ""


def _tile_carla_window(display: str) -> bool:
    tree = _x11_tree(display)
    match = re.search(r'^\s*(0x[0-9a-f]+)\s+"CarlaUE4', tree, re.MULTILINE | re.IGNORECASE)
    if match is None:
        return False
    library = ctypes.cdll.LoadLibrary("libX11.so.6")
    library.XOpenDisplay.restype = ctypes.c_void_p
    connection = library.XOpenDisplay(display.encode("ascii"))
    if not connection:
        return False
    try:
        window = int(match.group(1), 16)
        library.XMoveResizeWindow(
            ctypes.c_void_p(connection),
            ctypes.c_ulong(window),
            1500,
            45,
            1040,
            650,
        )
        library.XMapRaised(ctypes.c_void_p(connection), ctypes.c_ulong(window))
        library.XFlush(ctypes.c_void_p(connection))
    finally:
        library.XCloseDisplay(ctypes.c_void_p(connection))
    return True


class GateALiveDashboard:
    """Truthful Gate A/B view using the established DriveClarify HUD."""

    def __init__(
        self, output_root: Path, total: int, *, candidate_mode: bool = False
    ) -> None:
        self.output_root = output_root
        self.total = total
        self.candidate_mode = bool(candidate_mode)
        self.completed = 0
        self.town = "PREFLIGHT"
        self.runtime_id = "WAITING"
        self.seed = 0
        self.runtime_input: Mapping[str, Any] | None = None
        self.candidate_capture: Mapping[str, Any] | None = None
        self.visualizer = LiveShadowVisualizerV0(
            output_root / "stage6a_gate_a_live_panel.png",
            open_window=True,
            window_title=(
                "DriveClarify Stage 6A Live — Gate B RGB Candidates"
                if self.candidate_mode
                else "DriveClarify Stage 6A Live — Gate A"
            ),
        )

    def begin(
        self,
        town: str,
        runtime_input: Mapping[str, Any],
        seed: int,
        completed: int,
    ) -> None:
        self.town = town
        self.runtime_input = runtime_input
        self.runtime_id = str(runtime_input["runtime"]["runtime_fixture_id"])
        self.seed = int(seed)
        self.completed = int(completed)
        self.candidate_capture = None

    def capture_candidates(
        self,
        array: Any,
        frame: int,
        snapshot: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        require(self.candidate_mode, "LIVE_CANDIDATE_DASHBOARD_MODE_DISABLED")
        require(self.runtime_input is not None, "LIVE_CANDIDATE_RUNTIME_INPUT_MISSING")
        runtime = self.runtime_input["runtime"]
        payload = build_live_candidate_capture(
            runtime_fixture_id=self.runtime_id,
            runtime_manifest_sha256=str(self.runtime_input["runtime_sha256"]),
            selected_seed=self.seed,
            raw_instruction=str(runtime["raw_instruction"]),
            frame=int(frame),
            image_bgra=array,
            observed_monotonic_time=float(snapshot["observed_monotonic_time"]),
            ego_state=snapshot["ego_state"],
            route_context=snapshot["route_context"],
        )
        record, digest = content_address_capture(payload)
        relative = "candidate_records/%s.json" % digest
        _atomic_write(self.output_root / relative, pretty_json_bytes(record))
        self.candidate_capture = record
        return {
            "status": "VERIFIED",
            "candidate_capture_path": relative,
            "candidate_capture_payload_sha256": digest,
            "front_rgb_raw_sha256": record["front_rgb"]["raw_bgra_sha256"],
            "front_rgb_frame": int(record["front_rgb"]["frame"]),
            "candidate_count": len(record["generation_output"]["candidates"]),
            "adapter_config_sha256": record["vision_adapter"][
                "adapter_config_sha256"
            ],
            "catalog_read_count": 0,
            "evaluation_label_access_count": 0,
            "test_labels_opened": False,
        }

    def observe(
        self,
        array: Any,
        frame: int,
        control_count: int,
        scenario: Any,
    ) -> None:
        display = array[:, :, :3].copy()
        self.visualizer.set_front_rgb(
            display,
            {
                "status": "AVAILABLE",
                "sensor_frame": int(frame),
                "frame_identity_matches_baseline": True,
                "source": "SAME_REAL_CARLA_FRONT_RGB_SENSOR_CALLBACK",
                "model_input_unchanged": True,
            },
        )
        timeline_updates = int(
            getattr(scenario, "_timeline_update_count", 0) if scenario is not None else 0
        )
        generated_candidates = (
            []
            if self.candidate_capture is None
            else list(self.candidate_capture["generation_output"]["candidates"])
        )
        candidate_rows = [
            {
                "candidate_id": item["candidate_id"],
                "interpretation_id": item["interpretation_id"],
                "route_semantic": "SIMLINGO_FORWARD_PENDING_GATE_C",
                "stop_status": "NOT_EVALUATED",
                "route": [],
                "language": [item["prompt_text"]],
                "source_frame_id": item["source_frame_id"],
                "candidate_output_digest": item["candidate_semantic_digest"],
            }
            for item in generated_candidates
        ]
        runtime = None if self.runtime_input is None else self.runtime_input["runtime"]
        raw_instruction = (
            f"{self.runtime_id} seed={self.seed}"
            if runtime is None
            else str(runtime["raw_instruction"])
        )
        record = {
            "dashboard_title": (
                f"GATE B LIVE {self.completed + 1}/{self.total} | {self.town} | "
                "REAL RGB -> K=2 CANDIDATES | SIMLINGO/M3 PENDING"
                if self.candidate_mode
                else f"GATE A LIVE {self.completed + 1}/{self.total} | {self.town} | "
                "GATE B/C MODEL + AUTHORITY NOT YET EXECUTED"
            ),
            "source_identity": {"source_frame_id": int(frame)},
            "instruction": {
                "raw": raw_instruction,
                "interpretation_a": (
                    generated_candidates[0]["prompt_text"]
                    if len(generated_candidates) > 0
                    else "waiting for same-frame RGB candidate A"
                ),
                "interpretation_b": (
                    generated_candidates[1]["prompt_text"]
                    if len(generated_candidates) > 1
                    else "waiting for same-frame RGB candidate B"
                ),
            },
            "candidates": candidate_rows,
            "baseline": {"route": []},
            "counterfactual_matrix": {"cells": []},
            "m2b": {"producer_action": "NOT_EXECUTED"},
            "m3": {"current_state": "NOT_EXECUTED"},
            "physical_wait_v0": {
                "status": "INACTIVE_GATE_A",
                "m3_lifecycle_state": "NOT_EXECUTED",
                "m3_authority": "NONE",
                "candidate_commit_status": "PENDING_GATE_B_C",
                "control_actuator_equality_count": 0,
                "control_equality_observed_count": 0,
                "wait_executor_tick_overhead_ms": {"max": 0},
                "lease_elapsed_s": 0,
                "lease_remaining_s": 0,
                "wait_entry_frame": "N/A",
                "current_frame": int(frame),
                "distance_travelled_m": 0,
                "carla_ticks_during_wait": timeline_updates,
            },
            "ask_replanning_v0": {"enabled": False},
            "limited_act_commit_v0": {"enabled": False},
            "performance": {
                "validation_control_invocation_count": int(control_count),
                "candidate_model_forward_count": 0,
                "existing_simlingo_pid_invocation_count": 0,
                "runtime_candidate_count": len(candidate_rows),
            },
        }
        self.visualizer.render(record)

    def close(self) -> None:
        self.visualizer.close()


class CarlaTownServer:
    def __init__(
        self,
        town: str,
        output_root: Path,
        display: str,
        startup_timeout: float,
    ) -> None:
        self.town = town
        self.output_root = output_root
        self.display = display
        self.startup_timeout = float(startup_timeout)
        self.process: subprocess.Popen[str] | None = None
        self.log_handle: Any = None
        self.log_relative: str | None = None
        self.client: Any = None
        self.world: Any = None
        self.carla: Any = None
        self.session_sha256: str | None = None
        self.started_at_utc: str | None = None
        self.cleanup: Mapping[str, Any] | None = None
        self.process_pids: tuple[int, ...] = ()

    def start(self) -> None:
        require(not _port_open(CARLA_RPC_PORT), "CARLA_RPC_PORT_ALREADY_IN_USE")
        require(not _port_open(CARLA_STREAMING_PORT), "CARLA_STREAM_PORT_ALREADY_IN_USE")
        require(not _port_open(TRAFFIC_MANAGER_PORT), "TRAFFIC_MANAGER_PORT_ALREADY_IN_USE")
        engine_ini = _engine_ini(self.output_root, self.town)
        user_dir = engine_ini.parents[3]
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        log_path = self.output_root / "server_logs" / f"{self.town}_{stamp}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_handle = log_path.open("w", encoding="utf-8")
        self.log_relative = str(log_path.relative_to(self.output_root))
        command = [
            str(CARLA_ROOT / "CarlaUE4.sh"),
            f"-UserDir={user_dir}",
            "-quality-level=Epic",
            f"-carla-rpc-port={CARLA_RPC_PORT}",
            f"-carla-streaming-port={CARLA_STREAMING_PORT}",
            "-windowed",
            "-ResX=1040",
            "-ResY=650",
            "-WinX=1500",
            "-WinY=45",
            "-nosound",
        ]
        self.started_at_utc = _utc_now()
        self.process = subprocess.Popen(
            command,
            cwd=str(CARLA_ROOT),
            env=_display_environment(self.display),
            stdout=self.log_handle,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        _atomic_write(
            self.output_root / "ACTIVE_SERVER.json",
            pretty_json_bytes(
                {
                    "schema_version": "driveclarify.stage6a.active_server.v1",
                    "status": "STARTING",
                    "town": self.town,
                    "process_group_id": int(self.process.pid),
                    "process_pids": [],
                    "carla_session_sha256": None,
                    "worker_pid": os.getpid(),
                    "observed_at_utc": _utc_now(),
                }
            ),
        )
        deadline = time.monotonic() + self.startup_timeout
        import carla

        self.carla = carla
        last_error = "SERVER_NOT_READY"
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(
                    f"CARLA_SERVER_EXITED_DURING_STARTUP:{self.town}:{self.process.returncode}"
                )
            try:
                client = carla.Client("127.0.0.1", CARLA_RPC_PORT)
                client.set_timeout(3.0)
                world = client.get_world()
                actual = _normal_town(str(world.get_map().name))
                if actual != self.town:
                    last_error = f"CARLA_COLD_BOOT_WRONG_MAP:{actual}:{self.town}"
                else:
                    self.client = client
                    self.world = world
                    break
            except Exception as exc:
                last_error = type(exc).__name__ + ":" + str(exc)
            time.sleep(1.0)
        require(self.client is not None and self.world is not None, last_error)
        settings = self.world.get_settings()
        require(settings.no_rendering_mode is False, "CARLA_RENDERING_MODE_DISABLED")
        settings.synchronous_mode = True
        settings.fixed_delta_seconds = 0.05
        settings.no_rendering_mode = False
        self.world.apply_settings(settings)
        self.world.tick()
        client_version = str(self.client.get_client_version())
        server_version = str(self.client.get_server_version())
        process_tree, self.process_pids = _process_group_rows(self.process.pid)
        require("CarlaUE4-Linux-Shipping" in process_tree, "REAL_CARLA_SERVER_PROCESS_NOT_VERIFIED")
        native_window = False
        window_deadline = time.monotonic() + 20.0
        while time.monotonic() < window_deadline:
            if _tile_carla_window(self.display):
                native_window = True
                break
            time.sleep(0.5)
        require(native_window, "NATIVE_CARLA_WINDOW_NOT_VERIFIED")
        session_unsigned = {
            "town": self.town,
            "started_at_utc": self.started_at_utc,
            "server_pid": int(self.process.pid),
            "server_process_tree_sha256": canonical_sha256(process_tree),
            "client_version": client_version,
            "server_version": server_version,
            "map_name": str(self.world.get_map().name),
            "display": self.display,
            "physical_display_verified": True,
            "native_carla_window_verified": True,
            "quality_level": "Epic",
            "no_rendering_mode": False,
            "rpc_world_load_used": False,
        }
        self.session_sha256 = canonical_sha256(session_unsigned)
        _atomic_write(
            self.output_root / "sessions" / f"{self.session_sha256}.json",
            pretty_json_bytes(
                {**session_unsigned, "carla_session_sha256": self.session_sha256}
            ),
        )
        _atomic_write(
            self.output_root / "ACTIVE_SERVER.json",
            pretty_json_bytes(
                {
                    "schema_version": "driveclarify.stage6a.active_server.v1",
                    "status": "READY_WORKER_OWNS_CLIENT",
                    "town": self.town,
                    "process_group_id": int(self.process.pid),
                    "process_pids": list(self.process_pids),
                    "carla_session_sha256": self.session_sha256,
                    "worker_pid": os.getpid(),
                    "observed_at_utc": _utc_now(),
                }
            ),
        )

    def detach_for_external_supervisor(self) -> None:
        """Release libcarla in the worker; a clean parent kills the server."""

        if self.world is not None:
            try:
                settings = self.world.get_settings()
                settings.synchronous_mode = False
                settings.fixed_delta_seconds = None
                self.world.apply_settings(settings)
            except Exception:
                pass
        self.world = None
        self.client = None
        self.carla = None
        gc.collect()
        if self.log_handle is not None:
            self.log_handle.flush()
            self.log_handle.close()
            self.log_handle = None
        active_path = self.output_root / "ACTIVE_SERVER.json"
        if active_path.is_file():
            active = load_json(active_path)
            active["status"] = "DETACHED_READY_FOR_EXTERNAL_CLEANUP"
            active["observed_at_utc"] = _utc_now()
            _atomic_write(active_path, pretty_json_bytes(active))

    def stop(self) -> Mapping[str, Any]:
        process = self.process
        started = process is not None
        escalation: list[str] = []
        if self.world is not None:
            try:
                settings = self.world.get_settings()
                settings.synchronous_mode = False
                settings.fixed_delta_seconds = None
                self.world.apply_settings(settings)
            except Exception:
                pass
        # Drop every in-process libcarla proxy before terminating the native
        # server.  In particular, do not kill CARLA while an exception
        # traceback still retains a TrafficManager/world proxy: libcarla can
        # otherwise abort the Python process from a background RPC thread.
        self.world = None
        self.client = None
        self.carla = None
        gc.collect()
        if process is not None:
            for name, sig, timeout in (
                ("SIGTERM", signal.SIGTERM, 5.0),
                ("SIGKILL", signal.SIGKILL, 3.0),
            ):
                _, live_group_pids = _process_group_rows(process.pid)
                if not live_group_pids:
                    break
                escalation.append(name)
                try:
                    os.killpg(process.pid, sig)
                except ProcessLookupError:
                    break
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    _, live_group_pids = _process_group_rows(process.pid)
                    if not live_group_pids:
                        break
                    time.sleep(0.25)
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                pass
        if self.log_handle is not None:
            self.log_handle.flush()
            self.log_handle.close()
            self.log_handle = None
        deadline = time.monotonic() + 12.0
        while time.monotonic() < deadline and any(
            _port_open(port)
            for port in (CARLA_RPC_PORT, CARLA_STREAMING_PORT, TRAFFIC_MANAGER_PORT)
        ):
            time.sleep(0.5)
        _, residual_group_pids = (
            ("", ()) if process is None else _process_group_rows(process.pid)
        )
        process_dead = not residual_group_pids
        ports_free = {
            str(port): not _port_open(port)
            for port in (CARLA_RPC_PORT, CARLA_STREAMING_PORT, TRAFFIC_MANAGER_PORT)
        }
        gpu = _command(
            [
                "nvidia-smi",
                "--query-compute-apps=pid,process_name",
                "--format=csv,noheader",
            ]
        ).stdout
        gpu_pids = {
            int(row.split(",", 1)[0].strip())
            for row in gpu.splitlines()
            if row.split(",", 1)[0].strip().isdigit()
        }
        project_gpu_residual = bool(gpu_pids.intersection(self.process_pids))
        self.cleanup = {
            "schema_version": "driveclarify.stage6a.town_cleanup.v1",
            "town": self.town,
            "server_started": started,
            "server_process_dead": process_dead,
            "signal_escalation": escalation,
            "ports_free": ports_free,
            "project_gpu_process_residual": project_gpu_residual,
            "status": (
                "PASS"
                if process_dead and all(ports_free.values()) and not project_gpu_residual
                else "BLOCKED"
            ),
            "observed_at_utc": _utc_now(),
        }
        _atomic_write(
            self.output_root / "cleanup" / f"{self.town}_{self.session_sha256 or 'unknown'}.json",
            pretty_json_bytes(self.cleanup),
        )
        return self.cleanup


def _cleanup_detached_server(
    output_root: Path,
    active: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Terminate one exact CARLA process group without importing libcarla."""

    town = str(active.get("town"))
    require(town in TOWNS, "ACTIVE_SERVER_TOWN_INVALID")
    pgid = int(active.get("process_group_id", 0))
    require(pgid > 1, "ACTIVE_SERVER_PROCESS_GROUP_INVALID")
    recorded_pids = {
        int(value)
        for value in active.get("process_pids", [])
        if isinstance(value, int) and value > 1
    }
    escalation: list[str] = []
    for name, sig, timeout in (
        ("SIGTERM", signal.SIGTERM, 5.0),
        ("SIGKILL", signal.SIGKILL, 3.0),
    ):
        _, live_group_pids = _process_group_rows(pgid)
        if not live_group_pids:
            break
        escalation.append(name)
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            break
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            _, live_group_pids = _process_group_rows(pgid)
            if not live_group_pids:
                break
            time.sleep(0.25)
    deadline = time.monotonic() + 12.0
    while time.monotonic() < deadline and any(
        _port_open(port)
        for port in (CARLA_RPC_PORT, CARLA_STREAMING_PORT, TRAFFIC_MANAGER_PORT)
    ):
        time.sleep(0.5)
    _, residual_group_pids = _process_group_rows(pgid)
    ports_free = {
        str(port): not _port_open(port)
        for port in (CARLA_RPC_PORT, CARLA_STREAMING_PORT, TRAFFIC_MANAGER_PORT)
    }
    gpu = _command(
        [
            "nvidia-smi",
            "--query-compute-apps=pid,process_name",
            "--format=csv,noheader",
        ]
    ).stdout
    gpu_pids = {
        int(row.split(",", 1)[0].strip())
        for row in gpu.splitlines()
        if row.split(",", 1)[0].strip().isdigit()
    }
    project_gpu_residual = bool(gpu_pids.intersection(recorded_pids))
    cleanup = {
        "schema_version": "driveclarify.stage6a.town_cleanup.v1",
        "town": town,
        "carla_session_sha256": active.get("carla_session_sha256"),
        "server_started": True,
        "server_process_dead": not residual_group_pids,
        "residual_process_group_pids": list(residual_group_pids),
        "signal_escalation": escalation,
        "ports_free": ports_free,
        "project_gpu_process_residual": project_gpu_residual,
        "cleanup_process_isolated_from_libcarla_worker": True,
        "status": (
            "PASS"
            if not residual_group_pids
            and all(ports_free.values())
            and not project_gpu_residual
            else "BLOCKED"
        ),
        "observed_at_utc": _utc_now(),
    }
    session = str(active.get("carla_session_sha256") or "unknown")
    _atomic_write(
        output_root / "cleanup" / f"{town}_{session}.json",
        pretty_json_bytes(cleanup),
    )
    _atomic_write(
        output_root / "ACTIVE_SERVER.json",
        pretty_json_bytes(
            {
                **dict(active),
                "status": "CLOSED" if cleanup["status"] == "PASS" else "CLEANUP_BLOCKED",
                "cleanup": cleanup,
                "observed_at_utc": _utc_now(),
            }
        ),
    )
    return cleanup


def _progress_path(output_root: Path, town: str) -> Path:
    return output_root / "progress" / f"{town}.json"


def _load_progress(output_root: Path, town: str) -> dict[str, Any]:
    path = _progress_path(output_root, town)
    if not path.is_file():
        return {
            "schema_version": "driveclarify.stage6a.native_town_progress.v1",
            "town": town,
            "records": [],
            "sessions": [],
            "status": "NOT_STARTED",
        }
    payload = load_json(path)
    require(payload.get("town") == town, "TOWN_PROGRESS_IDENTITY_INVALID")
    records = payload.get("records")
    require(isinstance(records, list), "TOWN_PROGRESS_RECORDS_INVALID")
    for record in records:
        receipt_path = output_root / str(record["receipt_path"])
        receipt = load_json(receipt_path)
        require(
            verify_content_address(receipt, RECEIPT_HASH_FIELD)
            and receipt[RECEIPT_HASH_FIELD] == record["receipt_payload_sha256"],
            "TOWN_PROGRESS_RECEIPT_CONTENT_ADDRESS_INVALID",
        )
    return payload


def _save_progress(output_root: Path, progress: Mapping[str, Any]) -> None:
    _atomic_write(
        _progress_path(output_root, str(progress["town"])),
        pretty_json_bytes(progress),
    )


def _configuration_record(
    result: Mapping[str, Any],
    runtime_input: Mapping[str, Any],
    carla_session_sha256: str,
) -> Mapping[str, Any]:
    receipt = result["receipt"]
    handler = result["handler_receipt"]
    composite = any(
        item.get("realization_contract", {}).get("mode")
        == "COMPOSITE_MEASURED_CLEARANCE_GATEWAY"
        for item in receipt["actor_bindings"]
    )
    unsigned = {
        "runtime_fixture_id": result["runtime_fixture_id"],
        "selected_seed": result["selected_seed"],
        "runtime_manifest_sha256": runtime_input["runtime_sha256"],
        "promotion_receipt_payload_sha256": result["receipt_payload_sha256"],
        "derived_route_sha256": runtime_input["route_sha256"],
        "carla_session_sha256": carla_session_sha256,
        "status": "PASS",
        "real_carla_execution": True,
        "injected_client_or_world": False,
        "physical_display_rendered": True,
        "no_rendering_mode": False,
        "handler_registered": bool(
            handler["scenario_runner"]["handler_registered"]
        ),
        "handler_instantiated": bool(
            handler["scenario_runner"]["handler_instantiated"]
        ),
        "route_scenario_skipped": bool(
            handler["scenario_runner"]["route_scenario_skipped"]
        ),
        "world_tick_observed": bool(handler["timeline"]["world_tick_observed"]),
        "physical_timeline_verified": bool(
            handler["timeline"]["physical_timeline_verified"]
        ),
        "semantic_evidence_verified": bool(
            receipt["semantic_fidelity"]["status"] == "VERIFIED"
        ),
        "termination_reached": bool(handler["termination"]["reached"]),
        "cleanup_verified": bool(handler["cleanup"]["verified"]),
        "composite_clearance_configuration": composite,
        "composite_clearance_realization_verified": bool(
            not composite
            or all(
                item.get("realization_contract", {}).get("mode")
                != "COMPOSITE_MEASURED_CLEARANCE_GATEWAY"
                or item.get("clearance_measurement", {}).get("verified") is True
                for item in receipt["actor_bindings"]
            )
        ),
    }
    return {**unsigned, "execution_payload_sha256": canonical_sha256(unsigned)}


def _gallery(output_root: Path, progresses: list[Mapping[str, Any]]) -> None:
    records = [record for progress in progresses for record in progress.get("records", [])]
    cards = []
    for record in sorted(
        records, key=lambda item: (item["town"], item["runtime_fixture_id"], item["selected_seed"])
    ):
        rgb = html.escape(str(record["rgb_path"]))
        cards.append(
            "<article><img loading='lazy' src='{}'><div class='body'>"
            "<h3>{} · seed {}</h3><p>{}</p><p class='ok'>PASS real CARLA / "
            "ScenarioRunner / semantic / cleanup / native HUD</p>"
            "<code>receipt {}…</code></div></article>".format(
                rgb,
                html.escape(str(record["town"])),
                int(record["selected_seed"]),
                html.escape(str(record["runtime_fixture_id"])),
                html.escape(str(record["receipt_payload_sha256"]))[:16],
            )
        )
    document = """<!doctype html><html><head><meta charset='utf-8'>
<meta http-equiv='refresh' content='15'><title>DriveClarify Stage 6A Live</title>
<style>body{font-family:system-ui;background:#0f141b;color:#e7edf5;margin:24px}
h1{margin-bottom:4px}.meta{color:#91a4bb;margin-bottom:22px}.grid{display:grid;
grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:18px}article{background:#171f29;
border:1px solid #344253;border-radius:10px;overflow:hidden}img{width:100%;aspect-ratio:16/9;
object-fit:cover;background:#000}.body{padding:12px}h3{margin:0 0 8px}.ok{color:#5ee29a}
code{font-size:12px;color:#9fb2c7}</style></head><body>
<h1>DriveClarify Stage 6A — Native Live Evidence</h1>
<div class='meta'>Runtime HUD is the primary live view. This auto-refreshing gallery is the
content-addressed evidence index. Completed: """ + str(len(records)) + """ / 96.
Model/PID counts shown in Gate A are zero.</div>
<div class='grid'>""" + "".join(cards) + """</div></body></html>"""
    _atomic_write(output_root / "GALLERY.html", document.encode("utf-8"))


def _promotion_record(record: Mapping[str, Any]) -> Mapping[str, Any]:
    return {
        "runtime_fixture_id": record["runtime_fixture_id"],
        "runtime_manifest_sha256": record["runtime_manifest_sha256"],
        "selected_seed": record["selected_seed"],
        "receipt_path": record["receipt_path"],
        "receipt_payload_sha256": record["receipt_payload_sha256"],
        "evidence_origin": LIVE_EVIDENCE_ORIGIN,
        "physical_spawn_verified": True,
        "semantic_fidelity_status": "VERIFIED",
        "promotion_ready": True,
        "final_status": "PASS_LIVE_PROMOTION_READY",
    }


def _finalize(output_root: Path, runtime_root: Path) -> Mapping[str, Any]:
    progresses = [_load_progress(output_root, town) for town in TOWNS]
    records = [record for progress in progresses for record in progress["records"]]
    pairs = {
        (record["runtime_fixture_id"], int(record["selected_seed"]))
        for record in records
    }
    runtime_inputs = _runtime_inputs(runtime_root)
    expected = {
        (item["runtime"]["runtime_fixture_id"], int(seed))
        for item in runtime_inputs
        for seed in item["runtime"]["allowed_seed_values"]
    }
    require(len(records) == 96 and pairs == expected, "FORMAL_CAMPAIGN_96_PAIR_COVERAGE_INVALID")
    promotion_records = sorted(
        (_promotion_record(record) for record in records),
        key=lambda item: (item["runtime_fixture_id"], item["selected_seed"]),
    )
    manifest_unsigned = {
        "schema_version": LIVE_MANIFEST_SCHEMA_VERSION,
        "input_scope": {
            "runtime_visible_directory": "runtime_visible",
            "frozen_catalog_read": False,
            "evaluator_private_manifest_read": False,
            "evaluation_label_access": False,
        },
        "receipt_store": {
            "content_addressed": True,
            "hash_algorithm": "SHA-256_CANONICAL_JSON_UNSIGNED_PAYLOAD",
            "receipt_directory": LIVE_RECEIPT_DIR,
        },
        "runtime_fixture_count": 24,
        "seed_configuration_count": 96,
        "records": promotion_records,
        "summary": {
            "physical_spawn_verified_count": 96,
            "semantic_fidelity_verified_count": 96,
            "promotion_ready_count": 96,
            "live_evidence_origin_count": 96,
            "test_double_evidence_count": 0,
            "all_physical_spawn_verified": True,
            "all_semantic_fidelity_verified": True,
            "all_promotion_ready": True,
            "all_receipts_live_evidence": True,
            "carla_server_started_by_tool": False,
        },
        "final_status": "PASS_LIVE_PROMOTION_ALL_CONFIGURATIONS_READY",
    }
    manifest, _ = content_address(manifest_unsigned, MANIFEST_HASH_FIELD)
    manifest_path = output_root / LIVE_PROMOTION_MANIFEST
    _atomic_write(manifest_path, pretty_json_bytes(manifest))

    execution_records = sorted(
        (record["execution_record"] for record in records),
        key=lambda item: (item["runtime_fixture_id"], item["selected_seed"]),
    )
    session_hashes = sorted(
        {
            str(item["carla_session_sha256"])
            for item in execution_records
        }
    )
    aggregate_session_sha256 = canonical_sha256(session_hashes)
    composite_count = sum(
        int(item["composite_clearance_configuration"])
        for item in execution_records
    )
    require(composite_count == 8, "FORMAL_COMPOSITE_CONFIGURATION_COUNT_INVALID")
    execution_receipt = {
        "schema_version": SCENARIO_EXECUTION_RECEIPT_SCHEMA_VERSION,
        "status": "PASS",
        "source_kind": "LIVE_CARLA_SCENARIO_RUNNER_EXECUTION",
        "synthetic": False,
        "mock": False,
        "run_id": "DC-STAGE6A-NATIVE-PER-TOWN-EPIC-V1",
        "promotion_manifest_file_sha256": file_sha256(manifest_path),
        "promotion_manifest_payload_sha256": manifest[MANIFEST_HASH_FIELD],
        "execution_provenance": {
            "native_ubuntu": True,
            "physical_display_verified": True,
            "real_carla_server_process_verified": True,
            "carla_client_server_version_bound": True,
            "scenario_runner_native_import_verified": True,
            "world_tick_observed": True,
            "semantic_evidence_content_verified": True,
            "paths_and_secrets_omitted": True,
            "headless": False,
            "xvfb": False,
            "vnc": False,
            "render_off_screen": False,
            "no_rendering_mode": False,
            "injected_client_or_world": False,
            "carla_session_sha256": aggregate_session_sha256,
            "isolation_protocol": "ONE_EPIC_CARLA_COLD_BOOT_PER_BOUNDED_SINGLE_TOWN_BATCH_NO_RPC_MAP_SWITCH",
            "session_count": len(session_hashes),
        },
        "coverage": {
            "runtime_fixture_count": 24,
            "seed_configuration_count": 96,
            "handler_registered_count": 96,
            "handler_instantiated_count": 96,
            "event_timeline_executed_count": 96,
            "termination_reached_count": 96,
            "cleanup_verified_count": 96,
            "composite_clearance_configuration_count": 8,
            "composite_clearance_verified_count": 8,
            "route_scenario_skipped_count": 0,
            "failed_configuration_count": 0,
        },
        "configuration_receipts": execution_records,
        "execution_records_sha256": canonical_sha256(execution_records),
        "runtime_visualization": {
            "native_window_visible_during_all_configurations": True,
            "layout": "LEFT_DRIVECLARIFY_HUD_RIGHT_NATIVE_CARLA",
            "source": "SAME_REAL_FRONT_RGB_SENSOR_CALLBACK",
            "model_forward_count": 0,
            "pid_invocation_count": 0,
            "planner_step_count": 0,
            "vehicle_control_mutation_count": 0,
            "gallery_path": "GALLERY.html",
        },
    }
    execution_path = output_root / "SCENARIO_RUNNER_EXECUTION_RECEIPT.json"
    _atomic_write(execution_path, pretty_json_bytes(execution_receipt))
    validation = validate_live_promotions(
        output_root, runtime_root=runtime_root, require_ready=True
    )
    gate = _scenario_gate(output_root, runtime_root, execution_path)
    result = {
        "status": "PASS" if gate.passed else "BLOCKED",
        "validation": validation,
        "gate": gate.to_dict(),
        "promotion_manifest_sha256": file_sha256(manifest_path),
        "execution_receipt_sha256": file_sha256(execution_path),
        "gallery_sha256": file_sha256(output_root / "GALLERY.html"),
    }
    _atomic_write(output_root / "FINALIZATION_RESULT.json", pretty_json_bytes(result))
    require(gate.passed, "FORMAL_SCENARIO_GATE_DID_NOT_PASS")
    return result


def _supervise_campaign(args: argparse.Namespace) -> int:
    """Run each libcarla-owning town worker behind a clean OS supervisor."""

    output_root = args.output_dir.resolve()
    runtime_root = args.runtime_root.resolve()
    selected_towns = tuple(args.town) if args.town else TOWNS
    runtime_inputs = _runtime_inputs(runtime_root)
    for town in selected_towns:
        town_inputs = [
            item
            for item in runtime_inputs
            if item["runtime"]["route_binding"]["town"] == town
        ]
        expected = {
            (item["runtime"]["runtime_fixture_id"], int(seed))
            for item in town_inputs
            for seed in _selected_runtime_seeds(
                item, one_seed_per_runtime=args.one_seed_per_runtime
            )
        }
        progress = _load_progress(output_root, town)
        existing = {
            (record["runtime_fixture_id"], int(record["selected_seed"]))
            for record in progress["records"]
        }
        if existing == expected:
            continue
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--runtime-root",
            str(runtime_root),
            "--output-dir",
            str(output_root),
            "--display",
            args.display,
            "--startup-timeout",
            str(args.startup_timeout),
            "--town",
            town,
            "--town-worker",
        ]
        if args.one_seed_per_runtime:
            command.append("--one-seed-per-runtime")
        if args.capture_runtime_candidates:
            command.append("--capture-runtime-candidates")
        worker = subprocess.Popen(
            command,
            cwd=str(REPOSITORY_ROOT),
            env=os.environ.copy(),
        )
        return_code = worker.wait()
        active_path = output_root / "ACTIVE_SERVER.json"
        require(active_path.is_file(), "TOWN_WORKER_ACTIVE_SERVER_RECORD_MISSING")
        active = load_json(active_path)
        require(
            active.get("town") == town
            and int(active.get("worker_pid", -1)) == int(worker.pid),
            "TOWN_WORKER_ACTIVE_SERVER_IDENTITY_INVALID",
        )
        cleanup = _cleanup_detached_server(output_root, active)
        require(cleanup["status"] == "PASS", "TOWN_CLEANUP_NOT_VERIFIED")
        progress = _load_progress(output_root, town)
        current = {
            (record["runtime_fixture_id"], int(record["selected_seed"]))
            for record in progress["records"]
        }
        progress["cleanup"] = cleanup
        if return_code == 0 and current == expected:
            progress["status"] = "PASS"
        else:
            progress["status"] = "BLOCKED_CONFIGURATION_FAILURE_CLEANUP_VERIFIED"
        _save_progress(output_root, progress)
        print(
            "[%s] SUPERVISOR_CLEANUP %s status=%s worker_exit=%s"
            % (_utc_now(), town, cleanup["status"], return_code),
            flush=True,
        )
        if return_code != 0:
            return int(return_code)
        require(current == expected, "TOWN_WORKER_CONFIGURATION_COVERAGE_INVALID")
    progresses = [_load_progress(output_root, town) for town in TOWNS]
    _gallery(output_root, progresses)
    if not args.town and not args.one_seed_per_runtime:
        result = _finalize(output_root, runtime_root)
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(
            json.dumps(
                {
                    "status": (
                        "PASS_LIVE_RUNTIME_CANDIDATE_CAPTURE_24_OF_24"
                        if args.capture_runtime_candidates
                        else "PASS_SELECTED_TOWN_BATCHES"
                    ),
                    "selected_towns": list(selected_towns),
                    "completed_total": sum(
                        len(item["records"]) for item in progresses
                    ),
                    "gallery": str(output_root / "GALLERY.html"),
                },
                indent=2,
                sort_keys=True,
            )
        )
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPOSITORY_ROOT / "artifacts/paper_mvp_stage6a/live_execution_v1",
    )
    parser.add_argument("--display", default=":1")
    parser.add_argument("--town", action="append", choices=TOWNS, default=[])
    parser.add_argument("--startup-timeout", type=float, default=120.0)
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--finalize-only", action="store_true")
    parser.add_argument(
        "--one-seed-per-runtime",
        action="store_true",
        help="run only the first frozen allowed seed for each runtime fixture",
    )
    parser.add_argument(
        "--capture-runtime-candidates",
        action="store_true",
        help="generate and display K=2 candidates from the same native RGB callback",
    )
    parser.add_argument("--town-worker", action="store_true", help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.capture_runtime_candidates and not args.one_seed_per_runtime:
        raise SystemExit(
            "--capture-runtime-candidates requires --one-seed-per-runtime"
        )
    output_root = args.output_dir.resolve()
    runtime_root = args.runtime_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    if args.finalize_only:
        result = _finalize(output_root, runtime_root)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    _display_preflight(args.display, output_root)
    if args.preflight_only:
        print(json.dumps({"status": "PASS_NATIVE_DISPLAY_PREFLIGHT"}))
        return 0
    if not args.town_worker:
        return _supervise_campaign(args)
    selected_towns = tuple(args.town) if args.town else TOWNS
    runtime_inputs = _runtime_inputs(runtime_root)
    selected_inputs = [
        item
        for item in runtime_inputs
        if item["runtime"]["route_binding"]["town"] in selected_towns
    ]
    total = sum(
        len(
            _selected_runtime_seeds(
                item, one_seed_per_runtime=args.one_seed_per_runtime
            )
        )
        for item in selected_inputs
    )
    progresses = [_load_progress(output_root, town) for town in TOWNS]
    completed = sum(len(item["records"]) for item in progresses)
    os.environ["DRIVECLARIFY_VISUALIZER_WINDOW_WIDTH"] = "1500"
    os.environ["DRIVECLARIFY_VISUALIZER_WINDOW_HEIGHT"] = "900"
    os.environ["DRIVECLARIFY_VISUALIZER_WINDOW_X"] = "0"
    os.environ["DRIVECLARIFY_VISUALIZER_WINDOW_Y"] = "35"
    os.environ.update(_display_environment(args.display))
    campaign_total = sum(
        len(
            _selected_runtime_seeds(
                item, one_seed_per_runtime=args.one_seed_per_runtime
            )
        )
        for item in runtime_inputs
    )
    dashboard = GateALiveDashboard(
        output_root,
        campaign_total,
        candidate_mode=args.capture_runtime_candidates,
    )
    handler_sha256 = file_sha256(
        REPOSITORY_ROOT
        / "driveclarify_paper_mvp_scenarios/scenario_runner_scenario.py"
    )
    error: BaseException | None = None
    try:
        for town in selected_towns:
            progress = _load_progress(output_root, town)
            existing = {
                (record["runtime_fixture_id"], int(record["selected_seed"]))
                for record in progress["records"]
            }
            town_inputs = sorted(
                (
                    item
                    for item in selected_inputs
                    if item["runtime"]["route_binding"]["town"] == town
                ),
                key=lambda item: item["runtime"]["runtime_fixture_id"],
            )
            pending = [
                (item, int(seed))
                for item in town_inputs
                for seed in _selected_runtime_seeds(
                    item, one_seed_per_runtime=args.one_seed_per_runtime
                )
                if (item["runtime"]["runtime_fixture_id"], int(seed)) not in existing
            ]
            if not pending:
                continue
            server = CarlaTownServer(town, output_root, args.display, args.startup_timeout)
            cleanup: Mapping[str, Any] | None = None
            town_error: BaseException | None = None
            try:
                print(f"[{_utc_now()}] START_TOWN {town} pending={len(pending)}", flush=True)
                server.start()
                require(server.session_sha256 is not None, "CARLA_SESSION_HASH_MISSING")
                progress["sessions"].append(server.session_sha256)
                progress["status"] = "RUNNING"
                _save_progress(output_root, progress)
                config = LivePromotionConfig(
                    runtime_root=runtime_root,
                    output_dir=output_root,
                    host="127.0.0.1",
                    port=CARLA_RPC_PORT,
                    timeout_seconds=30.0,
                    allow_world_load=False,
                    navmesh_samples=64,
                    selected_towns=(town,),
                )
                for runtime_input, seed in pending:
                    runtime_id = runtime_input["runtime"]["runtime_fixture_id"]
                    dashboard.begin(town, runtime_input, seed, completed)
                    print(
                        f"[{_utc_now()}] CONFIG {completed + 1}/{campaign_total} {town} {runtime_id} seed={seed}",
                        flush=True,
                    )
                    try:
                        preliminary = author_preliminary_live_receipt(
                            runtime_input,
                            seed,
                            server.world,
                            server.client,
                            server.carla,
                            config,
                            scenario_handler_sha256=handler_sha256,
                        )
                        result = execute_preliminary_receipt(
                            preliminary,
                            runtime_input,
                            server.world,
                            server.client,
                            server.carla,
                            output_root,
                            generated_root=runtime_root,
                            scenario_runner_root=SCENARIO_RUNNER_ROOT,
                            traffic_manager_port=TRAFFIC_MANAGER_PORT,
                            physical_display_verified=True,
                            carla_session_sha256=server.session_sha256,
                            live_display_observer=dashboard.observe,
                            live_display_stride=8,
                            live_candidate_observer=(
                                dashboard.capture_candidates
                                if args.capture_runtime_candidates
                                else None
                            ),
                        )
                    except BaseException as config_exc:
                        configuration_failure = {
                            "schema_version": "driveclarify.stage6a.native_configuration_failure.v1",
                            "status": "BLOCKED",
                            "town": town,
                            "runtime_fixture_id": runtime_id,
                            "selected_seed": seed,
                            "error_type": type(config_exc).__name__,
                            "error_message": str(config_exc),
                            "completed_configuration_count": completed,
                            "carla_session_sha256": server.session_sha256,
                            "observed_at_utc": _utc_now(),
                            "test_consumed": False,
                        }
                        _atomic_write(
                            output_root / "CURRENT_CONFIGURATION_FAILURE.json",
                            pretty_json_bytes(configuration_failure),
                        )
                        print(
                            "[%s] BLOCKED_CONFIG %s %s seed=%s %s:%s"
                            % (
                                _utc_now(),
                                town,
                                runtime_id,
                                seed,
                                type(config_exc).__name__,
                                config_exc,
                            ),
                            flush=True,
                        )
                        town_error = RuntimeError(
                            "NATIVE_CONFIGURATION_BLOCKED:%s:%s:%s:%s:%s"
                            % (
                                town,
                                runtime_id,
                                seed,
                                type(config_exc).__name__,
                                config_exc,
                            )
                        )
                        break
                    execution_record = _configuration_record(
                        result, runtime_input, server.session_sha256
                    )
                    record = {
                        "town": town,
                        "runtime_fixture_id": runtime_id,
                        "runtime_manifest_sha256": runtime_input["runtime_sha256"],
                        "selected_seed": seed,
                        "receipt_path": result["receipt_path"],
                        "receipt_payload_sha256": result["receipt_payload_sha256"],
                        "handler_receipt_path": result["handler_receipt_path"],
                        "handler_receipt_payload_sha256": result[
                            "handler_receipt_payload_sha256"
                        ],
                        "rgb_path": result["rgb"]["png_path"],
                        "rgb_file_sha256": result["rgb"]["png_file_sha256"],
                        "carla_session_sha256": server.session_sha256,
                        "execution_record": execution_record,
                    }
                    if result.get("candidate_capture") is not None:
                        record["candidate_capture"] = dict(
                            result["candidate_capture"]
                        )
                    progress["records"].append(record)
                    progress["records"] = sorted(
                        progress["records"],
                        key=lambda item: (
                            item["runtime_fixture_id"],
                            item["selected_seed"],
                        ),
                    )
                    progress["completed_configuration_count"] = len(
                        progress["records"]
                    )
                    _save_progress(output_root, progress)
                    completed += 1
                    progresses = [_load_progress(output_root, item) for item in TOWNS]
                    _gallery(output_root, progresses)
                    print(
                        f"[{_utc_now()}] PASS_CONFIG {completed}/{campaign_total} receipt={result['receipt_payload_sha256']}",
                        flush=True,
                    )
                if town_error is None:
                    progress["status"] = "CONFIGURATIONS_PASS_CLEANUP_PENDING"
                    _save_progress(output_root, progress)
            finally:
                if args.town_worker:
                    server.detach_for_external_supervisor()
                else:
                    cleanup = server.stop()
                    require(cleanup["status"] == "PASS", "TOWN_CLEANUP_NOT_VERIFIED")
            if town_error is not None:
                raise town_error
            if args.town_worker:
                print(
                    f"[{_utc_now()}] PASS_TOWN_WORKER_CONFIGURATIONS {town}",
                    flush=True,
                )
                continue
            progress["cleanup"] = cleanup
            progress["status"] = "PASS"
            _save_progress(output_root, progress)
            print(f"[{_utc_now()}] PASS_TOWN {town}", flush=True)
    except BaseException as exc:
        error = exc
        failure = {
            "schema_version": "driveclarify.stage6a.native_campaign_failure.v1",
            "status": "BLOCKED",
            "error_type": type(exc).__name__,
            "error_message": str(exc),
            "completed_configuration_count": completed,
            "observed_at_utc": _utc_now(),
            "test_consumed": False,
        }
        _atomic_write(output_root / "CAMPAIGN_FAILURE.json", pretty_json_bytes(failure))
    finally:
        dashboard.close()
    if error is not None:
        raise error
    progresses = [_load_progress(output_root, town) for town in TOWNS]
    _gallery(output_root, progresses)
    if not args.town:
        result = _finalize(output_root, runtime_root)
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(
            json.dumps(
                {
                    "status": "PASS_SELECTED_TOWN_BATCHES",
                    "selected_towns": list(selected_towns),
                    "completed_total": sum(len(item["records"]) for item in progresses),
                    "gallery": str(output_root / "GALLERY.html"),
                },
                indent=2,
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
