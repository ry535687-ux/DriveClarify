"""One native CARLA/SimLingo episode backend shared by all eight methods.

Only ``DRIVECLARIFY_PAPER_MVP_STAGE6B_METHOD_ID`` varies by method.  Route,
scenario, weather, sensors, process lifecycle, existing PID, timeout, evaluator,
label firewall and cleanup are common.  The backend is TRAIN-only by contract.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import signal
import socket
import subprocess
import time
from contextlib import nullcontext
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from driveclarify_paper_mvp_evaluation.contracts import METHOD_ORDER
from driveclarify_paper_mvp_evaluation.display_preflight import (
    collect_native_display_facts,
    evaluate_native_display_preflight,
)

from .contracts import LabelFirewallCounters, Stage6BContractError
from .evaluator import (
    EpisodeIdentity,
    OnlineSafetyAccumulator,
    UnifiedPostEpisodeEvaluator,
)
from . import evidence_pipeline


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SIMLINGO_ROOT = Path("/home/buaa/wrh/simlingo")
CARLA_ROOT = Path("/home/buaa/CARLA_0.9.15")
PYTHON = Path("/home/buaa/anaconda3/envs/simlingo/bin/python")
LEADERBOARD_ROOT = SIMLINGO_ROOT / "Bench2Drive/leaderboard"
SCENARIO_RUNNER_PYTHON_ROOT = SIMLINGO_ROOT / "Bench2Drive/scenario_runner"
SCENARIO_DISCOVERY_ROOT = (
    REPOSITORY_ROOT
    / "driveclarify_paper_mvp_scenarios/leaderboard_scenario_root"
)
EVALUATOR = LEADERBOARD_ROOT / "leaderboard/leaderboard_evaluator.py"
AGENT = SIMLINGO_ROOT / "team_code/agent_simlingo.py"
OWNED_PROCESS_LAUNCHER = Path(__file__).with_name("owned_process_launcher.py")
CHECKPOINT = (
    SIMLINGO_ROOT
    / "outputs/simlingo/checkpoints/epoch=013.ckpt/pytorch_model.pt"
)
GENERATED_ROOT = REPOSITORY_ROOT / "driveclarify_paper_mvp_scenarios/generated"
PROMOTION_ROOT = (
    REPOSITORY_ROOT / "artifacts/paper_mvp_stage6a/live_execution_v1"
)
SCHEDULE_PATH = REPOSITORY_ROOT / "reports/paper_mvp_stage6b/EPISODE_SCHEDULE.json"
SCENARIO_MANIFEST_PATH = GENERATED_ROOT / "STAGE6A_SCENARIO_MANIFEST.json"
PROMOTION_MANIFEST_PATH = PROMOTION_ROOT / "LIVE_PROMOTION_MANIFEST.json"

RPC_PORT = 2020
STREAMING_PORT = 2021
TRAFFIC_MANAGER_PORT = 8020
DEFAULT_WALL_TIMEOUT_SECONDS = 420.0
DEFAULT_NO_PROGRESS_TIMEOUT_SECONDS = 30.0
DEFAULT_COLD_BOOT_QUIET_PERIOD_SECONDS = 3.0
SIMLINGO_PROTECTED_HEAD = "743b243afd6cf5ff51b9fa1f8cac86f22d569684"
SIMLINGO_PROTECTED_DIFF_SHA256 = (
    "112c1d006d398a10c91a1f25ae9a664bdecdf32a25246c8fed343c36daea970a"
)
CHECKPOINT_SHA256 = (
    "ec8943723d266ee9f5f56f45d153a163b22616960bfccb741965ea5daa700d28"
)


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    evidence_pipeline.atomic_json(path, value)


def _command(argv: Sequence[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(argv),
        cwd=None if cwd is None else str(cwd),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _git_head(root: Path) -> str:
    result = _command(("git", "rev-parse", "HEAD"), cwd=root)
    return result.stdout.strip() if result.returncode == 0 else "UNKNOWN"


def _git_diff_sha256(root: Path) -> str:
    result = subprocess.run(
        ["git", "diff", "--binary"],
        cwd=str(root),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return hashlib.sha256(result.stdout).hexdigest()


def _port_free(port: int) -> bool:
    probe = None
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("127.0.0.1", int(port)))
        return True
    except OSError:
        return False
    finally:
        if probe is not None:
            probe.close()


@dataclass(frozen=True)
class EpisodeSpec:
    episode_id: str
    runtime_config_id: str
    scenario_id: str
    runtime_fixture_id: str
    split: str
    seed: int
    method_id: str
    town: str
    route_id: str
    route_path: Path
    runtime_manifest_path: Path
    raw_instruction: str
    information_expected: bool
    schedule_sha256: str
    runtime_manifest_sha256: str
    promotion_receipt_path: Path
    promotion_receipt_payload_sha256: str

    def identity(self) -> EpisodeIdentity:
        return EpisodeIdentity(
            episode_id=self.episode_id,
            runtime_config_id=self.runtime_config_id,
            scenario_id=self.scenario_id,
            runtime_fixture_id=self.runtime_fixture_id,
            seed=self.seed,
            method_id=self.method_id,
            split=self.split,
        )

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        for key in (
            "route_path",
            "runtime_manifest_path",
            "promotion_receipt_path",
        ):
            value[key] = str(value[key])
        return value


def resolve_train_episode(
    *, scenario_id: str, seed: int, method_id: str
) -> EpisodeSpec:
    """Resolve one hash-bound TRAIN slot without opening DEV/TEST payloads."""

    if method_id not in METHOD_ORDER:
        raise Stage6BContractError("UNKNOWN_METHOD_ID:" + method_id)
    schedule = _load_json(SCHEDULE_PATH)
    matches = [
        item
        for item in schedule["episodes"]
        if item["scenario_id"] == scenario_id
        and int(item["seed"]) == int(seed)
        and item["method_id"] == method_id
        and item["split"] == "train"
    ]
    if len(matches) != 1:
        raise Stage6BContractError("TRAIN_SCHEDULE_BINDING_COUNT_NOT_ONE")
    slot = matches[0]
    scenario_manifest = _load_json(SCENARIO_MANIFEST_PATH)
    scenario_rows = [
        item
        for item in scenario_manifest["records"]
        if item["scenario_id"] == scenario_id and item["split"] == "train"
    ]
    if len(scenario_rows) != 1:
        raise Stage6BContractError("TRAIN_SCENARIO_BINDING_COUNT_NOT_ONE")
    scenario = scenario_rows[0]
    runtime_fixture_id = str(scenario["runtime_fixture_id"])
    runtime_path = GENERATED_ROOT / str(scenario["runtime_manifest_path"])
    route_path = GENERATED_ROOT / str(scenario["derived_route_path"])
    runtime = _load_json(runtime_path)
    if runtime["runtime_fixture_id"] != runtime_fixture_id:
        raise Stage6BContractError("RUNTIME_FIXTURE_IDENTITY_MISMATCH")
    allowed_seeds = tuple(int(value) for value in runtime["allowed_seed_values"])
    if int(seed) not in allowed_seeds:
        raise Stage6BContractError("SEED_NOT_ALLOWED_FOR_RUNTIME_FIXTURE")
    if runtime["route_binding"]["town"] not in {
        "Town01",
        "Town02",
        "Town03",
        "Town04",
        "Town05",
        "Town06",
        "Town07",
        "Town10HD",
    }:
        raise Stage6BContractError("RUNTIME_TOWN_OUTSIDE_BASE_MAP_SET")
    promotion = _load_json(PROMOTION_MANIFEST_PATH)
    promoted = [
        item
        for item in promotion["records"]
        if item["runtime_fixture_id"] == runtime_fixture_id
        and int(item["selected_seed"]) == int(seed)
        and item["final_status"] == "PASS_LIVE_PROMOTION_READY"
    ]
    if len(promoted) != 1:
        raise Stage6BContractError("LIVE_PROMOTION_BINDING_COUNT_NOT_ONE")
    promoted_row = promoted[0]
    receipt_path = PROMOTION_ROOT / str(promoted_row["receipt_path"])
    receipt = _load_json(receipt_path)
    if receipt.get("receipt_payload_sha256") != promoted_row.get(
        "receipt_payload_sha256"
    ):
        raise Stage6BContractError("LIVE_PROMOTION_RECEIPT_HASH_MISMATCH")
    information_expected = any(
        str(event.get("event_id", "")).startswith("EV04_")
        for event in runtime.get("event_timeline", ())
    )
    return EpisodeSpec(
        episode_id=str(slot["episode_id"]),
        runtime_config_id=str(slot["runtime_config_id"]),
        scenario_id=scenario_id,
        runtime_fixture_id=runtime_fixture_id,
        split="train",
        seed=int(seed),
        method_id=method_id,
        town=str(runtime["route_binding"]["town"]),
        route_id=str(runtime["route_binding"]["route_id"]),
        route_path=route_path.resolve(),
        runtime_manifest_path=runtime_path.resolve(),
        raw_instruction=str(runtime["raw_instruction"]),
        information_expected=information_expected,
        schedule_sha256=str(schedule["schedule_sha256"]),
        runtime_manifest_sha256=str(scenario["runtime_manifest_sha256"]),
        promotion_receipt_path=receipt_path.resolve(),
        promotion_receipt_payload_sha256=str(
            promoted_row["receipt_payload_sha256"]
        ),
    )


def _local_x11_session() -> Mapping[str, str] | None:
    listing = _command(("loginctl", "list-sessions", "--no-legend"))
    for line in listing.stdout.splitlines():
        columns = line.split()
        if not columns:
            continue
        result = _command(
            (
                "loginctl",
                "show-session",
                columns[0],
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
            )
        )
        values = {
            row.split("=", 1)[0]: row.split("=", 1)[1]
            for row in result.stdout.splitlines()
            if "=" in row
        }
        if (
            values.get("Name") == "buaa"
            and values.get("Remote") == "no"
            and values.get("Active") == "yes"
            and values.get("Type") == "x11"
            and values.get("Seat") == "seat0"
        ):
            values["session_id"] = columns[0]
            return values
    return None


def _pythonpath() -> str:
    values = (
        REPOSITORY_ROOT,
        # ``Bench2Drive/leaderboard`` also contains a legacy ``team_code``
        # package, but it does not contain the SimLingo agent modules.  Put
        # the protected SimLingo repository first so the absolute imports in
        # ``team_code/agent_simlingo.py`` resolve to their owning package.
        SIMLINGO_ROOT,
        LEADERBOARD_ROOT,
        SCENARIO_RUNNER_PYTHON_ROOT,
        CARLA_ROOT / "PythonAPI",
        CARLA_ROOT / "PythonAPI/carla",
        CARLA_ROOT / "PythonAPI/carla/dist/carla-0.9.15-py3.8-linux-x86_64.egg",
    )
    existing = os.environ.get("PYTHONPATH", "")
    return os.pathsep.join(str(value) for value in values) + (
        os.pathsep + existing if existing else ""
    )


def build_environment(
    spec: EpisodeSpec,
    output_dir: Path,
    *,
    visualization: bool,
) -> dict[str, str]:
    save_path = output_dir / "simlingo_save"
    save_path.mkdir(parents=True, exist_ok=True)
    probe_dir = output_dir / "probe"
    probe_dir.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ)
    environment.update(
        {
            "DISPLAY": ":1",
            "XAUTHORITY": "/run/user/1000/gdm/Xauthority",
            "XDG_SESSION_TYPE": "x11",
            "XDG_SESSION_REMOTE": "false",
            "__NV_PRIME_RENDER_OFFLOAD": "1",
            "__GLX_VENDOR_LIBRARY_NAME": "nvidia",
            "CARLA_ROOT": str(CARLA_ROOT),
            "LEADERBOARD_ROOT": str(LEADERBOARD_ROOT),
            "SCENARIO_RUNNER_ROOT": str(SCENARIO_DISCOVERY_ROOT),
            "PYTHONPATH": _pythonpath(),
            "ROUTES": str(spec.route_path),
            "TEAM_AGENT": str(AGENT),
            "TEAM_CONFIG": str(CHECKPOINT),
            "CHECKPOINT_ENDPOINT": str(output_dir / "leaderboard_results.json"),
            "SAVE_PATH": str(save_path) + os.sep,
            "IS_BENCH2DRIVE": "1",
            "CUDA_VISIBLE_DEVICES": "0",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "TOKENIZERS_PARALLELISM": "false",
            "PYTHONUNBUFFERED": "1",
            "DRIVECLARIFY_REPO": str(REPOSITORY_ROOT),
            "DRIVECLARIFY_COMMIT": _git_head(REPOSITORY_ROOT),
            "DRIVECLARIFY_CKPT_HASH": CHECKPOINT_SHA256,
            "DRIVECLARIFY_CONFIG_HASH": spec.schedule_sha256,
            "DRIVECLARIFY_PROBE_ENABLED": "1",
            "DRIVECLARIFY_PROBE_OUTPUT": str(probe_dir / "probe.jsonl"),
            "DRIVECLARIFY_PROBE_EQUIVALENCE": str(
                probe_dir / "PROBE_EQUIVALENCE.json"
            ),
            "DRIVECLARIFY_PROBE_RUN_ID": spec.episode_id,
            "DRIVECLARIFY_SHADOW_V0": "1",
            "DRIVECLARIFY_SHADOW_OUTPUT_DIR": str(output_dir),
            "DRIVECLARIFY_PAPER_MVP_STAGE6B_LIVE": "1",
            "DRIVECLARIFY_PAPER_MVP_STAGE6B_OUTPUT_DIR": str(output_dir),
            "DRIVECLARIFY_PAPER_MVP_STAGE6B_METHOD_ID": spec.method_id,
            "DRIVECLARIFY_PAPER_MVP_STAGE6B_EPISODE_ID": spec.episode_id,
            "DRIVECLARIFY_PAPER_MVP_STAGE6B_RUNTIME_FIXTURE_ID": spec.runtime_fixture_id,
            "DRIVECLARIFY_PAPER_MVP_STAGE6B_SEED": str(spec.seed),
            "DRIVECLARIFY_PAPER_MVP_STAGE6B_RAW_INSTRUCTION": spec.raw_instruction,
            "DRIVECLARIFY_PAPER_MVP_STAGE6B_INFORMATION_EXPECTED": (
                "1" if spec.information_expected else "0"
            ),
            "DRIVECLARIFY_PAPER_MVP_STAGE6B_VISUALIZATION": (
                "1" if visualization else "0"
            ),
            "DRIVECLARIFY_STAGE6A_GENERATED_ROOT": str(GENERATED_ROOT),
            "DRIVECLARIFY_STAGE6A_PROMOTION_ROOT": str(PROMOTION_ROOT),
            "DRIVECLARIFY_STAGE6A_SELECTED_SEED": str(spec.seed),
        }
    )
    for key in (
        "DRIVECLARIFY_PAPER_MVP_STAGE6A_LIVE",
        "DRIVECLARIFY_PAPER_MVP_STAGE6A_OUTPUT_DIR",
        "DRIVECLARIFY_PAPER_MVP_STAGE6A_VISUALIZATION",
    ):
        environment.pop(key, None)
    return environment


def build_command(spec: EpisodeSpec, output_dir: Path) -> tuple[str, ...]:
    return (
        str(PYTHON),
        "-u",
        str(EVALUATOR),
        "--routes=" + str(spec.route_path),
        "--repetitions=1",
        "--track=SENSORS",
        "--checkpoint=" + str(output_dir / "leaderboard_results.json"),
        "--debug-checkpoint=" + str(output_dir / "leaderboard_debug.txt"),
        "--agent=" + str(AGENT),
        "--agent-config=" + str(CHECKPOINT),
        "--debug=0",
        "--timeout=600",
        "--port=" + str(RPC_PORT),
        "--traffic-manager-port=" + str(TRAFFIC_MANAGER_PORT),
        "--traffic-manager-seed=" + str(spec.seed),
        "--gpu-rank=0",
    )


def native_preflight(
    spec: EpisodeSpec,
    output_dir: Path,
    *,
    visualization: bool,
) -> Mapping[str, Any]:
    command = build_command(spec, output_dir)
    environment = build_environment(
        spec, output_dir, visualization=visualization
    )
    session = _local_x11_session()
    facts = collect_native_display_facts(
        launch_arguments=command,
        launch_environment=environment,
        carla_executable=CARLA_ROOT / "CarlaUE4.sh",
        leaderboard_evaluator=EVALUATOR,
        checkpoint=CHECKPOINT,
        carla_port=RPC_PORT,
        traffic_manager_port=TRAFFIC_MANAGER_PORT,
    )
    result = evaluate_native_display_preflight(facts)
    blockers = list(result.blocker_codes)
    if session is None:
        blockers.append("LOCAL_SEAT0_X11_SESSION_NOT_VERIFIED")
    if _file_sha256(CHECKPOINT) != CHECKPOINT_SHA256:
        blockers.append("SIMLINGO_CHECKPOINT_HASH_MISMATCH")
    if _git_head(SIMLINGO_ROOT) != SIMLINGO_PROTECTED_HEAD:
        blockers.append("SIMLINGO_HEAD_MISMATCH")
    if _git_diff_sha256(SIMLINGO_ROOT) != SIMLINGO_PROTECTED_DIFF_SHA256:
        blockers.append("SIMLINGO_PROTECTED_DIFF_MISMATCH")
    receipt = {
        "schema_version": "driveclarify.paper_mvp_stage6b_native_preflight.v1",
        "status": "PASS" if not blockers else "BLOCKED",
        "observed_at_utc": _utc_now(),
        "episode": spec.to_dict(),
        "native_display": dict(result.evidence),
        "local_session": session,
        "blockers": list(dict.fromkeys(blockers)),
        "carla": {
            "root": str(CARLA_ROOT),
            "version": "0.9.15",
            "no_rendering_mode": False,
            "headless": False,
            "render_off_screen": False,
            "physical_display": ":1",
        },
        "simlingo": {
            "head": _git_head(SIMLINGO_ROOT),
            "protected_diff_sha256": _git_diff_sha256(SIMLINGO_ROOT),
            "checkpoint_sha256": _file_sha256(CHECKPOINT),
        },
        "command": list(command),
        "method_delta_environment_key": (
            "DRIVECLARIFY_PAPER_MVP_STAGE6B_METHOD_ID"
        ),
        "dev_attempt_count": 0,
        "test_attempt_count": 0,
        "test_consumed": False,
    }
    _atomic_json(output_dir / "NATIVE_PREFLIGHT.json", receipt)
    return receipt


@dataclass(frozen=True)
class _ProcessRow:
    pid: int
    ppid: int
    pgid: int
    args: str


def _process_rows() -> tuple[_ProcessRow, ...]:
    result = _command(("ps", "-eo", "pid=,ppid=,pgid=,args="))
    rows = []
    for line in result.stdout.splitlines():
        columns = line.strip().split(None, 3)
        if len(columns) != 4:
            continue
        try:
            rows.append(
                _ProcessRow(
                    pid=int(columns[0]),
                    ppid=int(columns[1]),
                    pgid=int(columns[2]),
                    args=columns[3],
                )
            )
        except ValueError:
            continue
    return tuple(rows)


def _descendants(root_pid: int, rows: Sequence[_ProcessRow]) -> tuple[_ProcessRow, ...]:
    selected = {int(root_pid)}
    changed = True
    while changed:
        changed = False
        for row in rows:
            if row.ppid in selected and row.pid not in selected:
                selected.add(row.pid)
                changed = True
    return tuple(row for row in rows if row.pid in selected)


def _safe_owned_row(row: _ProcessRow, observed_pids: set[int]) -> bool:
    if row.pid not in observed_pids or row.pid <= 1 or row.pgid <= 1:
        return False
    return any(
        token in row.args
        for token in (
            str(EVALUATOR),
            str(CARLA_ROOT / "CarlaUE4.sh"),
            "CarlaUE4-Linux-Shipping",
        )
    )


def _terminate_exact_owned_processes(
    observed_pids: set[int], observed_pgids: set[int]
) -> list[str]:
    escalation: list[str] = []
    for name, sig, pause in (
        ("SIGTERM", signal.SIGTERM, 2.0),
        ("SIGKILL", signal.SIGKILL, 1.0),
    ):
        rows = _process_rows()
        live = [row for row in rows if _safe_owned_row(row, observed_pids)]
        if not live:
            break
        groups = sorted(
            {
                row.pgid
                for row in live
                if row.pgid in observed_pgids and row.pgid != os.getpgrp()
            }
        )
        for group in groups:
            try:
                os.killpg(group, sig)
                escalation.append(name + ":pgid=" + str(group))
            except ProcessLookupError:
                continue
        deadline = time.monotonic() + pause
        while time.monotonic() < deadline:
            if not any(
                _safe_owned_row(row, observed_pids) for row in _process_rows()
            ):
                break
            time.sleep(0.1)
    return escalation


def _read_trace(path: Path) -> list[Mapping[str, Any]]:
    if not path.is_file():
        return []
    rows: list[Mapping[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, Mapping):
                rows.append(value)
    return rows


def _latest_trace_speed(path: Path) -> float | None:
    rows = _read_trace(path)
    for row in reversed(rows):
        control = row.get("control")
        if isinstance(control, Mapping) and isinstance(
            control.get("ego_velocity_mps"), (int, float)
        ):
            return abs(float(control["ego_velocity_mps"]))
    return None


def _owned_carla_crash_observed(path: Path) -> bool:
    if not path.is_file():
        return False
    payload = path.read_bytes()
    return any(
        marker in payload
        for marker in (
            b"Signal 11 caught.",
            b"CommonUnixCrashHandler: Signal=11",
            b"Segmentation fault (core dumped)",
        )
    )


def _full_brake_diagnosis(
    spec: EpisodeSpec, frame_rows: Sequence[Mapping[str, Any]]
) -> Mapping[str, Any]:
    desired = [
        row.get("pre_pid", {}).get("desired_speed_mps")
        for row in frame_rows
        if isinstance(row.get("pre_pid"), Mapping)
    ]
    desired = [float(value) for value in desired if isinstance(value, (int, float))]
    controls = [
        row.get("control", {}).get("vehicle_control", {})
        for row in frame_rows
        if isinstance(row.get("control"), Mapping)
    ]
    full_brake = [
        value
        for value in controls
        if isinstance(value, Mapping)
        and float(value.get("brake", 0.0) or 0.0) >= 0.999
        and float(value.get("throttle", 0.0) or 0.0) <= 1e-9
    ]
    first = frame_rows[0] if frame_rows else {}
    planner = first.get("route_planner_state", {})
    first_world = first.get("world", {})
    adapter_plan_sources = {
        row.get("pre_pid", {}).get("plan_source")
        for row in frame_rows
        if isinstance(row.get("pre_pid"), Mapping)
    }
    all_low = bool(desired) and all(value < 0.4 for value in desired)
    all_brake = bool(controls) and len(full_brake) == len(controls)
    return {
        "schema_version": "driveclarify.stage6b_full_brake_diagnosis.v1",
        "episode_identity": spec.to_dict(),
        "observed_frame_count": len(frame_rows),
        "desired_speed_observation_count": len(desired),
        "maximum_desired_speed_mps": max(desired) if desired else None,
        "control_observation_count": len(controls),
        "full_brake_control_count": len(full_brake),
        "all_observed_desired_speeds_below_existing_brake_threshold": all_low,
        "all_observed_controls_full_brake": all_brake,
        "route_planner_first_observation": planner,
        "initial_blocking_actor": (
            first_world.get("initial_blocking_actor")
            if isinstance(first_world, Mapping)
            else None
        ),
        "plan_sources": sorted(str(value) for value in adapter_plan_sources),
        "causal_classification": {
            "A_SIMLINGO_PREDICTED_ZERO_OR_NEAR_ZERO_SPEED": (
                "SUPPORTED" if all_low else "NOT_SUPPORTED_OR_INCOMPLETE"
            ),
            "B_EXISTING_LONGITUDINAL_CONTROLLER": (
                "SUPPORTED_IMMEDIATE_ACTUATOR_SOURCE" if all_brake else "NOT_ALL_FRAMES"
            ),
            "C_TRAFFIC_RULE_LOGIC": "NOT_CAUSALLY_SUPPORTED",
            "D_OBSTACLE_OR_SAFETY_LAYER": "NOT_CAUSALLY_SUPPORTED",
            "E_SCENARIO_INITIAL_CONDITION": "OBSERVED_NOT_CAUSALLY_SUPPORTED",
            "F_ROUTE_INITIALIZATION": (
                "NOT_SUPPORTED_ROUTE_PRESENT"
                if isinstance(planner, Mapping) and planner.get("route_len")
                else "UNKNOWN"
            ),
            "G_METHOD_ADAPTER": (
                "NOT_SUPPORTED_ORIGINAL_PLAN_PRESERVED"
                if spec.method_id == "original_simlingo"
                else "METHOD_DEPENDENT"
            ),
            "H_BASELINE_WRAPPER": "NOT_SUPPORTED_NO_DIRECT_CONTROL_WRITE",
            "I_UNKNOWN": (
                "UPSTREAM_REASON_FOR_MODEL_SPEED_OUTPUT_REMAINS_UNKNOWN"
                if all_low
                else "INSUFFICIENT_EVIDENCE"
            ),
        },
        "forced_throttle": False,
        "pid_bypass": False,
    }


def _cleanup_receipt(
    *,
    spec: EpisodeSpec,
    evaluator_pid: int,
    observed_pids: set[int],
    observed_pgids: set[int],
    timeout_signal: str | None,
) -> Mapping[str, Any]:
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        live = [
            row for row in _process_rows() if _safe_owned_row(row, observed_pids)
        ]
        if not live and all(
            _port_free(port)
            for port in (RPC_PORT, STREAMING_PORT, TRAFFIC_MANAGER_PORT)
        ):
            break
        time.sleep(0.25)
    escalation = _terminate_exact_owned_processes(observed_pids, observed_pgids)
    rows = _process_rows()
    live = [row for row in rows if _safe_owned_row(row, observed_pids)]
    process_text = "\n".join(row.args for row in rows)
    gpu = _command(
        (
            "nvidia-smi",
            "--query-compute-apps=pid,process_name",
            "--format=csv,noheader",
        )
    )
    gpu_pids = {
        int(line.split(",", 1)[0].strip())
        for line in gpu.stdout.splitlines()
        if line.split(",", 1)[0].strip().isdigit()
    }
    ports = {
        str(port): _port_free(port)
        for port in (RPC_PORT, STREAMING_PORT, TRAFFIC_MANAGER_PORT)
    }
    counts = {
        "carla_process_count": 0,
        "leaderboard_evaluator_process_count": sum(
            str(EVALUATOR) in row.args for row in rows
        ),
        "scenario_runner_process_count": sum(
            "scenario_runner.py" in row.args for row in rows
        ),
        "visualizer_process_count": sum(
            "stage6b_live_panel" in row.args for row in rows
        ),
    }
    # CARLA has two normal process rows (launcher and shipping binary) while
    # active; count rows rather than assuming one executable name.
    counts["carla_process_count"] = sum(
        "CarlaUE4" in row.args and str(os.getpid()) not in row.args for row in rows
    )
    project_gpu = sorted(gpu_pids.intersection(observed_pids))
    passed = bool(
        not live
        and all(ports.values())
        and not project_gpu
        and counts["carla_process_count"] == 0
        and counts["leaderboard_evaluator_process_count"] == 0
        and counts["scenario_runner_process_count"] == 0
        and counts["visualizer_process_count"] == 0
    )
    return {
        "schema_version": "driveclarify.paper_mvp_stage6b_cleanup.v1",
        "status": "PASS" if passed else "BLOCKED",
        "observed_at_utc": _utc_now(),
        "episode_id": spec.episode_id,
        "evaluator_pid": evaluator_pid,
        "timeout_signal": timeout_signal,
        "observed_owned_pids": sorted(observed_pids),
        "observed_owned_pgids": sorted(observed_pgids),
        "residual_owned_processes": [asdict(row) for row in live],
        "signal_escalation": escalation,
        "ports_free": ports,
        "project_gpu_compute_pids": project_gpu,
        "process_counts": counts,
        "process_table_sha256": _canonical_sha256(process_text),
    }


def run_native_episode(
    spec: EpisodeSpec,
    output_dir: Path,
    *,
    command: Sequence[str],
    environment: Mapping[str, str],
    cwd: Path,
    wall_timeout_seconds: float,
    wall_timeout_reason: str,
    poll_observer: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
    poll_interval_seconds: float = 1.0,
    stop_wait_seconds: float = 90.0,
    term_wait_seconds: float = 20.0,
    kill_wait_seconds: float = 10.0,
    launch_command: Sequence[str] | None = None,
    before_launch: Callable[[], None] | None = None,
    after_launch: Callable[[Any], None] | None = None,
    after_child_exit: Callable[[int], None] | None = None,
    cleanup_started: Callable[[BaseException | None], None] | None = None,
    cleanup_retry: Callable[[Mapping[str, Any]], Mapping[str, Any] | None] | None = None,
    cleanup_writer: Callable[[Mapping[str, Any]], None] | None = None,
    cleanup_complete: Callable[[Mapping[str, Any]], None] | None = None,
    process_guard_factory: Callable[[], Any] | None = None,
    cleanup_guard_factory: Callable[[], Any] | None = None,
) -> Mapping[str, Any]:
    """Run one policy-free native child lifecycle and exact owned cleanup.

    Split, slot, ledger, expected-label, scientific-status, and post-episode
    semantics deliberately remain with callers.  The optional callbacks carry
    caller-owned lifecycle receipts without exposing their policy to this
    mechanism.
    """

    if float(wall_timeout_seconds) <= 0.0:
        raise ValueError("POSITIVE_NATIVE_RUNTIME_WALL_TIMEOUT_REQUIRED")
    if min(
        float(poll_interval_seconds),
        float(stop_wait_seconds),
        float(term_wait_seconds),
        float(kill_wait_seconds),
    ) < 0.0:
        raise ValueError("NONNEGATIVE_NATIVE_RUNTIME_TIMING_REQUIRED")
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = output_dir / "evaluator_stdout.log"
    started_utc = _utc_now()
    started = time.monotonic()
    observed_pids: set[int] = set()
    observed_pgids: set[int] = set()
    evaluator_pid: int | None = None
    return_code: int | None = None
    termination_reason = "UNKNOWN"
    timeout_signal: str | None = None
    cleanup: Mapping[str, Any] | None = None
    interrupted: BaseException | None = None
    actual_launch_command = tuple(command if launch_command is None else launch_command)
    selected_stop_wait = float(stop_wait_seconds)
    selected_term_wait = float(term_wait_seconds)
    selected_kill_wait = float(kill_wait_seconds)

    def guard(factory: Callable[[], Any] | None):
        return nullcontext() if factory is None else factory()

    def request_signal(pid: int, name: str) -> None:
        selected = {
            "SIGINT": signal.SIGINT,
            "SIGTERM": signal.SIGTERM,
            "SIGKILL": signal.SIGKILL,
        }.get(name)
        if selected is None:
            raise ValueError("UNSUPPORTED_NATIVE_RUNTIME_SIGNAL:" + str(name))
        try:
            if name == "SIGINT":
                os.kill(pid, selected)
            else:
                os.killpg(pid, selected)
        except ProcessLookupError:
            pass

    try:
        with guard(process_guard_factory):
            with stdout_path.open("w", encoding="utf-8") as stdout:
                if before_launch is not None:
                    before_launch()
                process = subprocess.Popen(
                    list(actual_launch_command),
                    cwd=str(cwd),
                    env=dict(environment),
                    stdout=stdout,
                    stderr=subprocess.STDOUT,
                    text=True,
                    start_new_session=True,
                )
                evaluator_pid = int(process.pid)
                observed_pids.add(evaluator_pid)
                observed_pgids.add(evaluator_pid)
                if after_launch is not None:
                    after_launch(process)
                while process.poll() is None:
                    now = time.monotonic()
                    descendants = _descendants(evaluator_pid, _process_rows())
                    observed_pids.update(row.pid for row in descendants)
                    observed_pgids.update(
                        row.pgid for row in descendants if row.pgid > 1
                    )
                    action: Mapping[str, Any] = {}
                    if now - started >= float(wall_timeout_seconds):
                        action = {
                            "request_stop": True,
                            "termination_reason": wall_timeout_reason,
                            "signal": "SIGINT",
                        }
                    elif poll_observer is not None:
                        action = dict(
                            poll_observer(
                                {
                                    "now_monotonic": now,
                                    "started_monotonic": started,
                                    "evaluator_pid": evaluator_pid,
                                    "stdout_path": stdout_path,
                                    "observed_pids": tuple(sorted(observed_pids)),
                                    "observed_pgids": tuple(sorted(observed_pgids)),
                                }
                            )
                            or {}
                        )
                    if action.get("request_stop") is True:
                        termination_reason = str(
                            action.get("termination_reason") or "RUNTIME_STOP_REQUESTED"
                        )
                        signal_name = str(action.get("signal") or "SIGINT")
                        request_signal(evaluator_pid, signal_name)
                        timeout_signal = signal_name
                        selected_stop_wait = float(
                            action.get("stop_wait_seconds", selected_stop_wait)
                        )
                        selected_term_wait = float(
                            action.get("term_wait_seconds", selected_term_wait)
                        )
                        selected_kill_wait = float(
                            action.get("kill_wait_seconds", selected_kill_wait)
                        )
                        break
                    time.sleep(
                        float(action.get("poll_interval_seconds", poll_interval_seconds))
                    )
                if process.poll() is None:
                    try:
                        process.wait(timeout=selected_stop_wait)
                    except subprocess.TimeoutExpired:
                        request_signal(evaluator_pid, "SIGTERM")
                        timeout_signal = (
                            "SIGTERM"
                            if timeout_signal is None
                            else timeout_signal + "_THEN_SIGTERM"
                        )
                        try:
                            process.wait(timeout=selected_term_wait)
                        except subprocess.TimeoutExpired:
                            request_signal(evaluator_pid, "SIGKILL")
                            timeout_signal = (
                                "SIGKILL"
                                if timeout_signal is None
                                else timeout_signal + "_THEN_SIGKILL"
                            )
                            process.wait(timeout=selected_kill_wait)
                return_code = int(process.wait())
                if termination_reason == "UNKNOWN":
                    termination_reason = (
                        "NATURAL_EVALUATOR_COMPLETION"
                        if return_code == 0
                        else "CHILD_EXIT_NONZERO"
                    )
                if after_child_exit is not None:
                    after_child_exit(return_code)
    except BaseException as exc:
        interrupted = exc
    finally:
        if evaluator_pid is not None:
            if cleanup_started is not None:
                cleanup_started(interrupted)
            with guard(cleanup_guard_factory):
                cleanup = _cleanup_receipt(
                    spec=spec,
                    evaluator_pid=evaluator_pid,
                    observed_pids=observed_pids,
                    observed_pgids=observed_pgids,
                    timeout_signal=timeout_signal,
                )
                retry_details = (
                    None if cleanup_retry is None else cleanup_retry(cleanup)
                )
                if retry_details is not None:
                    cleanup = dict(
                        _cleanup_receipt(
                            spec=spec,
                            evaluator_pid=evaluator_pid,
                            observed_pids=observed_pids,
                            observed_pgids=observed_pgids,
                            timeout_signal=timeout_signal,
                        )
                    )
                    cleanup.update(dict(retry_details))
                if cleanup_writer is not None:
                    cleanup_writer(cleanup)
            if cleanup_complete is not None:
                cleanup_complete(cleanup)
    if interrupted is not None:
        raise interrupted
    if evaluator_pid is None or return_code is None or cleanup is None:
        raise RuntimeError("NATIVE_RUNTIME_CHILD_LIFECYCLE_INCOMPLETE")
    return {
        "schema_version": "driveclarify.common_native_runtime.result.v1",
        "status": (
            "PASS_NATIVE_RUNTIME_CLEANUP"
            if cleanup.get("status") == "PASS"
            else "BLOCKED_NATIVE_RUNTIME_CLEANUP"
        ),
        "start_utc": started_utc,
        "end_utc": _utc_now(),
        "duration_wall_seconds": time.monotonic() - started,
        "termination_reason": termination_reason,
        "evaluator_return_code": return_code,
        "evaluator_pid": evaluator_pid,
        "observed_owned_pids": sorted(observed_pids),
        "observed_owned_pgids": sorted(observed_pgids),
        "timeout_signal": timeout_signal,
        "stdout_path": str(stdout_path),
        "cleanup": dict(cleanup),
    }


class UnifiedNativeBackend:
    """Execute a single hash-bound episode using the shared native backend."""

    def __init__(
        self,
        *,
        wall_timeout_seconds: float = DEFAULT_WALL_TIMEOUT_SECONDS,
        no_progress_timeout_seconds: float = DEFAULT_NO_PROGRESS_TIMEOUT_SECONDS,
    ) -> None:
        self.wall_timeout_seconds = float(wall_timeout_seconds)
        self.no_progress_timeout_seconds = float(no_progress_timeout_seconds)
        if self.wall_timeout_seconds <= 0.0 or self.no_progress_timeout_seconds <= 0.0:
            raise Stage6BContractError("POSITIVE_BACKEND_TIMEOUT_REQUIRED")

    def run(
        self,
        spec: EpisodeSpec,
        output_dir: Path,
        *,
        visualization: bool = True,
        lifecycle_journal: evidence_pipeline.AttemptLifecycleJournal | None = None,
    ) -> Mapping[str, Any]:
        if spec.split != "train":
            raise Stage6BContractError("STAGE6B_R0_BACKEND_TRAIN_ONLY")
        output_dir = output_dir.resolve()
        if (output_dir / "EPISODE_RESULT.json").exists():
            raise Stage6BContractError("EPISODE_RESULT_ALREADY_EXISTS")
        output_dir.mkdir(parents=True, exist_ok=True)
        preflight = native_preflight(
            spec, output_dir, visualization=visualization
        )
        if preflight["status"] != "PASS":
            raise Stage6BContractError(
                "BLOCKED_NATIVE_PREFLIGHT:" + ",".join(preflight["blockers"])
            )
        command = build_command(spec, output_dir)
        environment = build_environment(
            spec, output_dir, visualization=visualization
        )
        launch_contract = {
            "schema_version": "driveclarify.paper_mvp_stage6b_launch.v1",
            "episode": spec.to_dict(),
            "command": list(command),
            "cwd": str(SIMLINGO_ROOT),
            "common_backend": True,
            "method_specific_environment": {
                "DRIVECLARIFY_PAPER_MVP_STAGE6B_METHOD_ID": spec.method_id
            },
            "normal_pid_owner": "EXISTING_SIMLINGO_PID",
            "new_pid_instances_allowed": 0,
            "direct_vehicle_control_writes_allowed": 0,
            "wall_timeout_seconds": self.wall_timeout_seconds,
            "no_progress_timeout_seconds": self.no_progress_timeout_seconds,
            "automatic_retry_count": 0,
            "durable_owned_process_launcher": (
                str(OWNED_PROCESS_LAUNCHER)
                if lifecycle_journal is not None
                else None
            ),
        }
        _atomic_json(output_dir / "EPISODE_LAUNCH_CONTRACT.json", launch_contract)
        stdout_path = output_dir / "evaluator_stdout.log"
        first_trace_monotonic: float | None = None
        launch_command = tuple(command)
        if lifecycle_journal is not None:
            launch_command = (
                str(PYTHON),
                "-u",
                str(OWNED_PROCESS_LAUNCHER),
                str(output_dir / evidence_pipeline.CHILD_IDENTITY_FILENAME),
                lifecycle_journal.identity_sha256,
                "--",
                *command,
            )

        def before_launch() -> None:
            if lifecycle_journal is not None:
                lifecycle_journal.advance(
                    "CHILD_LAUNCHING",
                    {"command_sha256": _canonical_sha256(list(command))},
                )

        def after_launch(process: Any) -> None:
            if lifecycle_journal is None:
                return
            handshake_path = output_dir / evidence_pipeline.CHILD_IDENTITY_FILENAME
            deadline = time.monotonic() + 5.0
            while (
                not handshake_path.is_file()
                and process.poll() is None
                and time.monotonic() < deadline
            ):
                time.sleep(0.01)
            if not handshake_path.is_file():
                raise Stage6BContractError(
                    "CHILD_IDENTITY_HANDSHAKE_NOT_DURABLE_BEFORE_EXEC"
                )
            lifecycle_journal.record_child_handshake(
                handshake_path, expected_pid=int(process.pid)
            )
            lifecycle_journal.advance("WAITING_FOR_CHILD")

        def observe_runtime(context: Mapping[str, Any]) -> Mapping[str, Any]:
            nonlocal first_trace_monotonic
            now = float(context["now_monotonic"])
            trace_path = output_dir / "stage6b_frame_trace.jsonl"
            if trace_path.is_file() and trace_path.stat().st_size > 0:
                if first_trace_monotonic is None:
                    first_trace_monotonic = now
                    if lifecycle_journal is not None:
                        lifecycle_journal.advance(
                            "FIRST_OBSERVATION_DURABLE",
                            {
                                "trace_path": str(trace_path),
                                "trace_bytes": trace_path.stat().st_size,
                            },
                        )
                speed = _latest_trace_speed(trace_path)
                if speed is not None and speed > 0.05:
                    first_trace_monotonic = now
            reason = None
            if (
                first_trace_monotonic is not None
                and now - first_trace_monotonic
                >= self.no_progress_timeout_seconds
            ):
                reason = "COMMON_NO_PROGRESS_WALL_TIMEOUT"
            elif _owned_carla_crash_observed(stdout_path):
                reason = "OWNED_CARLA_PROCESS_CRASH"
            if reason is None:
                return {}
            crash = reason == "OWNED_CARLA_PROCESS_CRASH"
            return {
                "request_stop": True,
                "termination_reason": reason,
                "signal": "SIGINT",
                "stop_wait_seconds": 5.0 if crash else 90.0,
                "term_wait_seconds": 5.0 if crash else 20.0,
            }

        def after_child_exit(return_code: int) -> None:
            if lifecycle_journal is not None:
                lifecycle_journal.advance(
                    "CHILD_EXITED", {"return_code": return_code}
                )

        def cleanup_started(interrupted: BaseException | None) -> None:
            if lifecycle_journal is not None:
                lifecycle_journal.advance(
                    "CLEANUP_STARTED",
                    {
                        "interruption": (
                            type(interrupted).__name__ + ":" + str(interrupted)
                            if interrupted is not None
                            else None
                        )
                    },
                )

        def cleanup_retry(cleanup: Mapping[str, Any]) -> Mapping[str, Any] | None:
            if cleanup["status"] == "PASS" or lifecycle_journal is None:
                return None
            journal_cleanup = evidence_pipeline.cleanup_owned_process(
                lifecycle_journal
            )
            return {"journal_exact_cleanup": journal_cleanup}

        def cleanup_complete(cleanup: Mapping[str, Any]) -> None:
            if lifecycle_journal is not None:
                lifecycle_journal.advance(
                    "CLEANUP_COMPLETE",
                    {
                        "cleanup_status": cleanup["status"],
                        "cleanup_receipt_sha256": _file_sha256(
                            output_dir / "CLEANUP_RECEIPT.json"
                        ),
                    },
                )

        runtime_result = run_native_episode(
            spec,
            output_dir,
            command=command,
            launch_command=launch_command,
            environment=environment,
            cwd=SIMLINGO_ROOT,
            wall_timeout_seconds=self.wall_timeout_seconds,
            wall_timeout_reason="COMMON_OPERATIONAL_WALL_TIMEOUT",
            poll_observer=observe_runtime,
            before_launch=before_launch,
            after_launch=after_launch,
            after_child_exit=after_child_exit,
            cleanup_started=cleanup_started,
            cleanup_retry=cleanup_retry,
            cleanup_writer=lambda value: _atomic_json(
                output_dir / "CLEANUP_RECEIPT.json", value
            ),
            cleanup_complete=cleanup_complete,
            process_guard_factory=evidence_pipeline.parent_interruption_guard,
            cleanup_guard_factory=evidence_pipeline.interruption_safe_cleanup,
        )
        return_code = int(runtime_result["evaluator_return_code"])
        cleanup = dict(runtime_result["cleanup"])
        termination_reason = str(runtime_result["termination_reason"])
        timeout_reason = (
            termination_reason
            if termination_reason
            in {
                "COMMON_OPERATIONAL_WALL_TIMEOUT",
                "COMMON_NO_PROGRESS_WALL_TIMEOUT",
                "OWNED_CARLA_PROCESS_CRASH",
            }
            else None
        )
        start_utc = str(runtime_result["start_utc"])
        end_utc = str(runtime_result["end_utc"])
        duration = float(runtime_result["duration_wall_seconds"])
        # A short common quiet period gives the native driver and UE4 child
        # process accounting time to settle before the next cold boot.  It is
        # identical for every method and occurs after measured episode time.
        time.sleep(DEFAULT_COLD_BOOT_QUIET_PERIOD_SECONDS)
        if lifecycle_journal is not None:
            lifecycle_journal.advance("JSONL_VALIDATION_STARTED")
        jsonl_validation = evidence_pipeline.write_jsonl_validation_receipt(
            output_dir, spec.episode_id
        )
        if lifecycle_journal is not None:
            lifecycle_journal.advance(
                "JSONL_VALIDATED",
                {
                    "status": jsonl_validation["status"],
                    "receipt_sha256": _file_sha256(
                        output_dir / evidence_pipeline.JSONL_VALIDATION_FILENAME
                    ),
                },
            )
        runtime_path = output_dir / "stage6b_runtime_audit.json"
        runtime_audit: dict[str, Any]
        if runtime_path.is_file():
            runtime_audit = dict(_load_json(runtime_path))
        else:
            setup_error = output_dir / "STAGE6B_LIVE_SETUP_ERROR.json"
            runtime_audit = {
                "terminal": True,
                "termination_reason": "STAGE6B_RUNTIME_AUDIT_MISSING",
                "decision_trace": [],
                "query_trace": [],
                "wait_trace": [],
                "candidate_trace": {},
                "interaction": {},
                "compute": {},
                "forward_accounting": {},
                "pid_accounting": {},
                "infrastructure_failure": True,
                "setup_error": (
                    _load_json(setup_error) if setup_error.is_file() else None
                ),
            }
        if timeout_reason is not None:
            runtime_audit["terminal"] = True
            runtime_audit["termination_reason"] = timeout_reason
        leaderboard_path = output_dir / "leaderboard_results.json"
        leaderboard = (
            _load_json(leaderboard_path) if leaderboard_path.is_file() else None
        )
        firewall_data = runtime_audit.get("label_firewall", {})
        firewall = LabelFirewallCounters(
            **{
                key: firewall_data[key]
                for key in asdict(LabelFirewallCounters())
                if isinstance(firewall_data, Mapping) and key in firewall_data
            }
        )
        safety_data = runtime_audit.get("safety_accumulator")
        safety = (
            OnlineSafetyAccumulator.from_mapping(safety_data)
            if isinstance(safety_data, Mapping)
            else None
        )
        if lifecycle_journal is not None:
            lifecycle_journal.advance(
                "TERMINAL_PRODUCER_CAPTURED",
                {
                    "runtime_audit_present": runtime_path.is_file(),
                    "leaderboard_present": leaderboard_path.is_file(),
                    "return_code": return_code,
                },
            )
        fingerprint = {
            "carla_version": "0.9.15",
            "carla_root": str(CARLA_ROOT),
            "native_display": ":1",
            "headless": False,
            "driveclarify_head": _git_head(REPOSITORY_ROOT),
            "simlingo_head": _git_head(SIMLINGO_ROOT),
            "simlingo_protected_diff_sha256": _git_diff_sha256(SIMLINGO_ROOT),
            "schedule_sha256": spec.schedule_sha256,
            "runtime_manifest_sha256": spec.runtime_manifest_sha256,
            "agent_sha256": _file_sha256(AGENT),
            "evaluator_sha256": _file_sha256(EVALUATOR),
        }
        episode_result = UnifiedPostEpisodeEvaluator().evaluate(
            identity=spec.identity(),
            leaderboard_payload=leaderboard,
            runtime_audit=runtime_audit,
            runtime_fingerprint=fingerprint,
            checkpoint={
                "path": str(CHECKPOINT),
                "sha256": _file_sha256(CHECKPOINT),
            },
            start_end={
                "start_utc": start_utc,
                "end_utc": end_utc,
                "duration_wall_seconds": duration,
            },
            cleanup_state=cleanup,
            firewall=firewall,
            goal_evidence=None,
            safety_accumulator=safety,
        ).to_dict()
        if lifecycle_journal is not None:
            lifecycle_journal.advance("EPISODE_RESULT_SERIALIZATION_STARTED")
        _atomic_json(output_dir / "EPISODE_RESULT.json", episode_result)
        if lifecycle_journal is not None:
            lifecycle_journal.advance(
                "EPISODE_RESULT_SERIALIZED",
                {
                    "sha256": _file_sha256(output_dir / "EPISODE_RESULT.json"),
                    "identity_sha256": _canonical_sha256(
                        episode_result.get("episode_identity", {})
                    ),
                },
            )
        frame_rows = _read_trace(output_dir / "stage6b_frame_trace.jsonl")
        diagnosis = _full_brake_diagnosis(spec, frame_rows)
        _atomic_json(output_dir / "FULL_BRAKE_DIAGNOSIS.json", diagnosis)
        runtime_present = runtime_path.is_file()
        status = (
            "COMPLETED_RECORDED_METHOD_RESULT"
            if runtime_present
            and cleanup["status"] == "PASS"
            and jsonl_validation["status"] == "PASS"
            else "EVIDENCE_CONTRACT_DEFECT"
            if jsonl_validation["status"] != "PASS"
            else "ENVIRONMENT_OR_SIMULATOR_FAILURE"
        )
        receipt = {
            "schema_version": "driveclarify.paper_mvp_stage6b_episode_receipt.v2",
            "status": status,
            "episode_identity": asdict(spec.identity()),
            "start_utc": start_utc,
            "end_utc": end_utc,
            "duration_wall_seconds": duration,
            "evaluator_return_code": return_code,
            "termination_reason": timeout_reason,
            "automatic_retry_count": 0,
            "real_native_carla": True,
            "physical_display": True,
            "headless": False,
            "runtime_audit_present": runtime_present,
            "cleanup_status": cleanup["status"],
            "jsonl_validation_status": jsonl_validation["status"],
            "artifacts": {
                "episode_result_sha256": _file_sha256(
                    output_dir / "EPISODE_RESULT.json"
                ),
                "runtime_audit_sha256": (
                    _file_sha256(runtime_path) if runtime_path.is_file() else None
                ),
                "evaluator_stdout_sha256": _file_sha256(stdout_path),
                "leaderboard_results_sha256": (
                    _file_sha256(leaderboard_path)
                    if leaderboard_path.is_file()
                    else None
                ),
                "full_brake_diagnosis_sha256": _file_sha256(
                    output_dir / "FULL_BRAKE_DIAGNOSIS.json"
                ),
                "jsonl_validation_receipt_sha256": _file_sha256(
                    output_dir / evidence_pipeline.JSONL_VALIDATION_FILENAME
                ),
            },
            "dev_attempt_count": 0,
            "test_attempt_count": 0,
            "test_consumed": False,
        }
        if lifecycle_journal is not None:
            lifecycle_journal.advance("EPISODE_RECEIPT_WRITE_STARTED")
        _atomic_json(output_dir / "EPISODE_RECEIPT.json", receipt)
        if lifecycle_journal is not None:
            lifecycle_journal.advance(
                "EPISODE_RECEIPT_SERIALIZED",
                {"sha256": _file_sha256(output_dir / "EPISODE_RECEIPT.json")},
            )
        return receipt


__all__ = [
    "DEFAULT_NO_PROGRESS_TIMEOUT_SECONDS",
    "DEFAULT_WALL_TIMEOUT_SECONDS",
    "EpisodeSpec",
    "UnifiedNativeBackend",
    "build_command",
    "build_environment",
    "native_preflight",
    "run_native_episode",
    "resolve_train_episode",
]
