"""Bounded M3E P3 staging, preflight, launch, and terminalization.

The module is deliberately standard-library-only.  It does not import CARLA,
torch, SimLingo, or the production evaluator.  Real-system work is possible
only through the exact, receipt-bound ``launch`` subcommand.
"""

from __future__ import annotations

import argparse
import copy
import errno
import hashlib
import itertools
import json
import math
import os
import pty
import select
import socket
import statistics
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


ROOT = Path("/home/buaa/wrh/DriveClarify")
SIMLINGO = Path("/home/buaa/wrh/simlingo")
REPORT = ROOT / "reports/static_maneuver_branch_primary_mvp_v1"
PACKAGE = REPORT / "M3E_STATIC_BRANCH_PILOT"
RUN_OUTPUTS = PACKAGE / "run_outputs"
P1_ID = "DC-M3E-STATIC-P1-20260803T082200Z"
P2_ID = "DC-M3E-STATIC-P2-20260803T092100Z"
RUN_IDS = (
    "DC-M3E-STATIC-P3A-20260803T102300Z",
    "DC-M3E-STATIC-P3B-20260803T102400Z",
    "DC-M3E-STATIC-P3C-20260803T102500Z",
)
FIXED_TIMES = {
    RUN_IDS[0]: ("2026_08_03_10_23_00", "2026-08-03T10:23:00Z"),
    RUN_IDS[1]: ("2026_08_03_10_24_00", "2026-08-03T10:24:00Z"),
    RUN_IDS[2]: ("2026_08_03_10_25_00", "2026-08-03T10:25:00Z"),
}
SCHEDULE = ("A1", "A2", "A3", "B1", "B2", "B3")
TOPOLOGY_SHA256 = "cfd5bb11099680c1a887e847adbb1cc11bf62ec13ba490dfd72075929e03c7db"
THRESHOLD_SHA256 = "6dfeea8907eb987c512c2b180a824d78c38d7c57662385c24d0d884bf3a37553"
FIXTURE_SHA256 = "3d9e7b41471ddcd6cc27cf63ea69957a56062435f1c0dd1554653e2e1a78db9e"
EVALUATOR_SHA256 = "745b1635a820276f665b95d63c783cf2e8eec62363784a731877bd3062511964"
CHECKPOINT_SHA256 = "ec8943723d266ee9f5f56f45d153a163b22616960bfccb741965ea5daa700d28"
CONFIG_SHA256 = "d56a7c1ebf3b6fd7ff6edff87071f1b2fe7269563e3ea626379994bef7807417"
P1_AGGREGATE = "39e516539f2f8584b96eb06aad478d9389b702c0f162b8c51d44c287b6d6591c"
P2_INVENTORY_SHA256 = "1d3b48e74d2c5a0d0a6b9e1eb4c14d7c1240138f44c0ad937013b37824f310f4"
P2_RUN_RESULT_SHA256 = "e5d21fe5573be6c7dcec0330796516fc552a8be30f864a700314b3899084329b"
R2_AGGREGATE = "a8ba378ba8618a410fb6f6c5ae5b31628887c8d8523af93ebe18045f26709e7e"
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()

P2_DIR = RUN_OUTPUTS / P2_ID
P2_SPEC = P2_DIR / "RUN_SPEC_AUTHORIZED.json"
P2_AGENT = P2_DIR / "M3E_REAL_SIMLINGO_STATIC_BRANCH_AGENT.py"
P2_SITECUSTOMIZE = P2_DIR / "sitecustomize.py"
BASE_ADAPTER = ROOT / "reports/driveclarify_manual_phase0a_world_alias_fix/NO_LAUNCH_EVALUATOR_ADAPTER.py"
BASE_ADAPTER_SHA256 = "b59723b06b9aec6178c386987f9b782840979b327fd6a4fd94f0d77ffdaefc4f"

TOPOLOGY = REPORT / "BRANCH_TOPOLOGY_GROUND_TRUTH.json"
THRESHOLDS = REPORT / "MAPPING_THRESHOLD_PROVENANCE.json"
FIXTURE = REPORT / "fixtures/town03_route_27515_junction_238_scenario_free_v1.xml"
ROUTE_VALIDATION = ROOT / "driveclarify_static_branch/route_validation.py"
PROCESS_BINDING = ROOT / "driveclarify_static_branch/process_binding.py"
BOUND_SUPERVISOR = ROOT / "driveclarify_static_branch/m3e_bound_supervisor.py"
SELFTEST_SPEC = REPORT / "M3E_PRELAUNCH_SUPERVISOR_SELFTEST_SPEC.json"
FOUNDATION = ROOT / "runtime/candidate_sensitivity_pilot/REAL_SIMLINGO_SENSITIVITY_AGENT.py"
EVALUATOR = SIMLINGO / "Bench2Drive/leaderboard/leaderboard/leaderboard_evaluator.py"
AGENT_WRAPPER = SIMLINGO / "Bench2Drive/leaderboard/leaderboard/autoagents/agent_wrapper.py"
CHECKPOINT = SIMLINGO / "outputs/simlingo/checkpoints/epoch=013.ckpt/pytorch_model.pt"
CONFIG = SIMLINGO / "outputs/simlingo/.hydra/config.yaml"
PYTHON38 = Path("/home/buaa/anaconda3/envs/simlingo/bin/python3.8")
CARLA_BINARY = Path("/home/buaa/CARLA_0.9.15/CarlaUE4/Binaries/Linux/CarlaUE4-Linux-Shipping")
R2_ROOT = ROOT / "runtime/real_plan_semantic_mapping_m3b/DC-RPSM-M3B-R2-20260802T163815Z"


class CampaignError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest_value(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def embedded_sha256(value: Mapping[str, Any]) -> str:
    unsigned = dict(value)
    unsigned.pop("sha256", None)
    return digest_value(unsigned)


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def atomic_create_bytes(path: Path, raw: bytes, mode: int = 0o600) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(str(path), flags, mode)
    try:
        offset = 0
        while offset < len(raw):
            written = os.write(descriptor, raw[offset:])
            if written <= 0:
                raise OSError("SHORT_WRITE:" + str(path))
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_create_json(path: Path, value: Any) -> None:
    atomic_create_bytes(path, json_bytes(value))


def atomic_replace_json(path: Path, value: Any) -> None:
    raw = json_bytes(value)
    temporary = path.with_name("." + path.name + ".m3e-p3.tmp")
    atomic_create_bytes(temporary, raw)
    os.replace(str(temporary), str(path))


def append_command_log(output: Path, text: str) -> None:
    with (output / "COMMAND_LOG.md").open("a", encoding="utf-8") as stream:
        stream.write(text.rstrip() + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def require_run_id(run_id: str) -> Path:
    if run_id not in RUN_IDS:
        raise CampaignError("RUN_ID_NOT_PREAUTHORIZED:" + run_id)
    return RUN_OUTPUTS / run_id


def run_command(args: Sequence[str], cwd: Path, env: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    started = time.monotonic_ns()
    completed = subprocess.run(
        list(args),
        cwd=str(cwd),
        env=dict(env) if env is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    return {
        "argv": list(args),
        "cwd": str(cwd),
        "exit_code": completed.returncode,
        "duration_ns": time.monotonic_ns() - started,
        "output": completed.stdout,
    }


def spawn_with_pty(args: Sequence[str], cwd: Path, env: Mapping[str, str]) -> Tuple[subprocess.Popen, int]:
    """Start a session leader whose stdout/stderr remain a valid terminal fd."""

    master_fd, slave_fd = pty.openpty()
    try:
        process = subprocess.Popen(
            list(args),
            cwd=str(cwd),
            env=dict(env),
            stdin=subprocess.DEVNULL,
            stdout=slave_fd,
            stderr=slave_fd,
            shell=False,
            close_fds=True,
            start_new_session=True,
        )
    except BaseException:
        os.close(master_fd)
        raise
    finally:
        os.close(slave_fd)
    return process, master_fd


def drain_pty_to_log(process: subprocess.Popen, master_fd: int, log_fd: int, timeout_seconds: float) -> int:
    deadline = time.monotonic() + timeout_seconds
    try:
        while True:
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(process.args, timeout_seconds)
            ready, _, _ = select.select([master_fd], [], [], 0.25)
            if ready:
                try:
                    chunk = os.read(master_fd, 65536)
                except OSError as exc:
                    if exc.errno == errno.EIO:
                        chunk = b""
                    else:
                        raise
                if chunk:
                    offset = 0
                    while offset < len(chunk):
                        written = os.write(log_fd, chunk[offset:])
                        if written <= 0:
                            raise OSError("PTY_LOG_SHORT_WRITE")
                        offset += written
                elif process.poll() is not None:
                    break
            elif process.poll() is not None:
                break
        os.fsync(log_fd)
        return int(process.wait(timeout=1.0))
    finally:
        os.close(master_fd)


def git_state(repo: Path) -> Dict[str, Any]:
    def raw(args: Sequence[str]) -> bytes:
        return subprocess.run(list(args), cwd=str(repo), check=True, stdout=subprocess.PIPE).stdout

    tracked = raw(("git", "diff", "--no-ext-diff", "--binary"))
    staged = raw(("git", "diff", "--cached", "--no-ext-diff", "--binary"))
    untracked = raw(("git", "ls-files", "--others", "--exclude-standard", "-z"))
    paths = [item.decode("utf-8", errors="surrogateescape") for item in untracked.split(b"\0") if item]
    return {
        "branch": raw(("git", "branch", "--show-current")).decode().strip(),
        "head": raw(("git", "rev-parse", "HEAD")).decode().strip(),
        "tracked_diff_bytes": len(tracked),
        "tracked_diff_sha256": hashlib.sha256(tracked).hexdigest(),
        "staged_diff_bytes": len(staged),
        "staged_diff_sha256": hashlib.sha256(staged).hexdigest(),
        "untracked_file_count": len(paths),
        "untracked_path_list_nul_sha256": hashlib.sha256(untracked).hexdigest(),
        "untracked_paths": paths,
    }


def directory_aggregate(root: Path, prefix: str) -> Tuple[int, int, str]:
    rows: List[str] = []
    total = 0
    files = sorted(path for path in root.rglob("*") if path.is_file())
    for path in files:
        total += path.stat().st_size
        rows.append(sha256_path(path) + "  " + prefix + path.relative_to(root).as_posix() + "\n")
    return len(files), total, hashlib.sha256("".join(rows).encode("utf-8")).hexdigest()


def validate_embedded(path: Path, expected: str) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("sha256") != expected or embedded_sha256(value) != expected:
        raise CampaignError("EMBEDDED_SHA256_MISMATCH:" + str(path))
    return value


def protected_history() -> Dict[str, Any]:
    p1_count, p1_bytes, p1_digest = directory_aggregate(RUN_OUTPUTS / P1_ID, "reports/static_maneuver_branch_primary_mvp_v1/M3E_STATIC_BRANCH_PILOT/run_outputs/" + P1_ID + "/")
    if (p1_count, p1_bytes, p1_digest) != (27, 110911, P1_AGGREGATE):
        raise CampaignError("P1_HISTORY_PROTECTION_MISMATCH")
    p2_inventory = P2_DIR / "ARTIFACT_INVENTORY.json"
    p2_result = P2_DIR / "RUN_RESULT.json"
    if sha256_path(p2_inventory) != P2_INVENTORY_SHA256 or sha256_path(p2_result) != P2_RUN_RESULT_SHA256:
        raise CampaignError("P2_HISTORY_PROTECTION_MISMATCH")
    inventory = json.loads(p2_inventory.read_text(encoding="utf-8"))
    for row in inventory["artifacts"]:
        path = P2_DIR / row["path"]
        if not path.is_file() or path.stat().st_size != row["bytes"] or sha256_path(path) != row["sha256"]:
            raise CampaignError("P2_INVENTORY_ROW_MISMATCH:" + row["path"])
    r2_count, r2_bytes, r2_digest = directory_aggregate(R2_ROOT, "./")
    if (r2_count, r2_bytes, r2_digest) != (24, 628549, R2_AGGREGATE):
        raise CampaignError("R2_HISTORY_PROTECTION_MISMATCH")
    prior_campaign_runs = []
    for prior_run_id in RUN_IDS:
        prior_root = RUN_OUTPUTS / prior_run_id
        prior_inventory_path = prior_root / "ARTIFACT_INVENTORY.json"
        if not prior_inventory_path.is_file():
            continue
        prior_inventory = json.loads(prior_inventory_path.read_text(encoding="utf-8"))
        for row in prior_inventory.get("artifacts", []):
            path = prior_root / row["path"]
            if not path.is_file() or path.stat().st_size != row["bytes"] or sha256_path(path) != row["sha256"]:
                raise CampaignError("PRIOR_P3_INVENTORY_ROW_MISMATCH:" + prior_run_id + ":" + row["path"])
        prior_campaign_runs.append({
            "run_id": prior_run_id,
            "inventory_sha256": sha256_path(prior_inventory_path),
            "listed_rows_verified": len(prior_inventory.get("artifacts", [])),
            "terminal_status": prior_inventory.get("terminal_status"),
        })
    return {
        "p1": {"file_count": p1_count, "total_bytes": p1_bytes, "aggregate_sha256": p1_digest},
        "p2": {"inventory_sha256": sha256_path(p2_inventory), "run_result_sha256": sha256_path(p2_result), "listed_rows_verified": len(inventory["artifacts"])},
        "r2": {"file_count": r2_count, "total_bytes": r2_bytes, "aggregate_sha256": r2_digest},
        "prior_p3_runs": prior_campaign_runs,
    }


def port_free(port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", port))
        return True
    finally:
        sock.close()


def deep_replace(value: Any, old: str, new: str) -> Any:
    if isinstance(value, str):
        return value.replace(old, new)
    if isinstance(value, list):
        return [deep_replace(item, old, new) for item in value]
    if isinstance(value, dict):
        return {key: deep_replace(item, old, new) for key, item in value.items()}
    return value


def evaluator_entry_source() -> str:
    route_hash = sha256_path(ROUTE_VALIDATION)
    return '''#!/usr/bin/env python3
"""P3 production evaluator entry using the verified compatibility loader."""
from __future__ import annotations
import functools
import hashlib
import importlib.util
import sys
from pathlib import Path

ROOT = Path("/home/buaa/wrh/DriveClarify")
BASE_ADAPTER = ROOT / "reports/driveclarify_manual_phase0a_world_alias_fix/NO_LAUNCH_EVALUATOR_ADAPTER.py"
BASE_ADAPTER_SHA256 = "{base_hash}"
ROUTE_VALIDATION = ROOT / "driveclarify_static_branch/route_validation.py"
ROUTE_VALIDATION_SHA256 = "{route_hash}"

def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

if _sha(BASE_ADAPTER) != BASE_ADAPTER_SHA256:
    raise RuntimeError("M3E_BASE_EVALUATOR_ADAPTER_HASH_MISMATCH")
if _sha(ROUTE_VALIDATION) != ROUTE_VALIDATION_SHA256:
    raise RuntimeError("M3E_ROUTE_VALIDATION_ADAPTER_HASH_MISMATCH")
spec = importlib.util.spec_from_file_location("_driveclarify_m3e_p3_base_no_launch_adapter", str(BASE_ADAPTER))
if spec is None or spec.loader is None:
    raise RuntimeError("M3E_BASE_ADAPTER_SPEC_FAILED")
adapter = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = adapter
spec.loader.exec_module(adapter)
from driveclarify_static_branch.route_validation import load_compatible_evaluator_for_base_adapter
adapter._load_original = load_compatible_evaluator_for_base_adapter
if __name__ == "__main__":
    raise SystemExit(adapter.main())
'''.format(base_hash=BASE_ADAPTER_SHA256, route_hash=route_hash)


def validate_sequence_for_stage(run_id: str) -> None:
    index = RUN_IDS.index(run_id)
    for earlier in RUN_IDS[:index]:
        result_path = RUN_OUTPUTS / earlier / "RUN_RESULT.json"
        if not result_path.is_file():
            raise CampaignError("PREVIOUS_RUN_NOT_TERMINAL:" + earlier)
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if result.get("terminal_category") != "ENGINEERING_FAILURE":
            raise CampaignError("PREVIOUS_RUN_DID_NOT_AUTHORIZE_CONTINUATION:" + earlier)
    for later in RUN_IDS[index + 1 :]:
        if (RUN_OUTPUTS / later).exists():
            raise CampaignError("LATER_RUN_DIRECTORY_EXISTS_OUT_OF_ORDER:" + later)


def stage(run_id: str) -> None:
    output = require_run_id(run_id)
    validate_sequence_for_stage(run_id)
    if output.exists():
        raise CampaignError("RUN_OUTPUT_ALREADY_EXISTS:" + run_id)
    before = {"driveclarify": git_state(ROOT), "simlingo": git_state(SIMLINGO)}
    output.mkdir(parents=False)
    (output / "logs").mkdir()
    (output / "receipt_claims").mkdir()
    agent_source = P2_AGENT.read_text(encoding="utf-8")
    if agent_source.count(P2_ID) != 1:
        raise CampaignError("P2_AGENT_RUN_ID_OCCURRENCE_NOT_ONE")
    agent_source = agent_source.replace(P2_ID, run_id)
    atomic_create_bytes(output / "M3E_REAL_SIMLINGO_STATIC_BRANCH_AGENT.py", agent_source.encode("utf-8"))
    atomic_create_bytes(output / "M3E_EVALUATOR_ENTRY.py", evaluator_entry_source().encode("utf-8"))
    atomic_create_bytes(output / "sitecustomize.py", P2_SITECUSTOMIZE.read_bytes())
    capture = json.loads((PACKAGE / "CAPTURE_PLAN.json").read_text(encoding="utf-8"))
    capture.update({
        "planned_schedule": list(SCHEDULE),
        "run_authorized": True,
        "authorization_run_id": run_id,
        "schedule_provenance": "USER_EXPLICIT_P3_BOUNDED_CAMPAIGN_AUTHORIZATION_FROZEN_BEFORE_MODEL_OUTPUT",
    })
    capture["sha256"] = embedded_sha256(capture)
    atomic_create_json(output / "CAPTURE_PLAN.json", capture)
    atomic_create_json(output / "STAGING_CONTEXT.json", {
        "schema_version": "driveclarify.m3e_p3_staging_context.v1",
        "run_id": run_id,
        "staged_at_utc": utc_now(),
        "git_before_run_staging": before,
        "agent_derivation": {"template": str(P2_AGENT), "template_sha256": sha256_path(P2_AGENT), "only_semantic_edit": "EXACT_RUN_ID_SUBSTITUTION", "generated_sha256": sha256_path(output / "M3E_REAL_SIMLINGO_STATIC_BRANCH_AGENT.py")},
        "evaluator_entry": {"production_loader": "load_compatible_evaluator_for_base_adapter", "generated_sha256": sha256_path(output / "M3E_EVALUATOR_ENTRY.py")},
        "dedicated_m3e_runner_used": False,
    })
    atomic_create_bytes(output / "COMMAND_LOG.md", ("# M3E P3 run command log\n\n- Run ID: `" + run_id + "`\n- Staged at: `" + utc_now() + "`\n- Receipt: NOT_CREATED. Real launch: NOT_EXECUTED.\n").encode("utf-8"))


def cpu_check(run_id: str) -> None:
    output = require_run_id(run_id)
    if (output / "AUTHORIZATION_RECEIPT.json").exists():
        raise CampaignError("CPU_CHECK_AFTER_RECEIPT_FORBIDDEN")
    agent = output / "M3E_REAL_SIMLINGO_STATIC_BRANCH_AGENT.py"
    entry = output / "M3E_EVALUATOR_ENTRY.py"
    if not agent.is_file() or not entry.is_file():
        raise CampaignError("RUN_NOT_STAGED")
    isolated = dict(os.environ)
    isolated["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    commands = [
        [str(PYTHON38), "-B", "-c", "compile(open(%r, 'rb').read(), %r, 'exec')" % (str(agent), str(agent))],
        [str(PYTHON38), "-B", "-c", "compile(open(%r, 'rb').read(), %r, 'exec')" % (str(entry), str(entry))],
        [str(PYTHON38), "-B", "-m", "pytest", "-q", "tests/evaluator_adapter_production_path", "tests/m3d_route_validation"],
        [sys.executable, "-B", "-m", "pytest", "-q", "tests/m3e_supervisor_binding"],
        [sys.executable, "-B", "-m", "pytest", "-q", "reports/driveclarify_manual_phase0a_supervisor_process_identity_fix/test_process_identity_fix.py", "tests/static_branch_mvp", "tests/fairness_contract_v2", "tests/m3d_route_validation", "tests/evaluator_adapter_production_path", "tests/maneuver_branch_real_mapping/test_maneuver_branch.py", "tests/maneuver_branch_real_mapping/test_scenario_runtime_fairness.py", "tests/maneuver_branch_route_scenario_v2"],
        [sys.executable, "-B", "-m", "pytest", "-q", "tests/m3e_p3_campaign"],
        [str(PYTHON38), "-B", "tools/evaluator_production_path_dry_run.py"],
    ]
    results = [run_command(command, ROOT, isolated) for command in commands]
    contract = {
        "agent_exact_run_id_occurrences": agent.read_text(encoding="utf-8").count(run_id),
        "agent_old_run_id_occurrences": agent.read_text(encoding="utf-8").count(P2_ID),
        "agent_schedule_literal_present": 'SCHEDULE = ("A1", "A2", "A3", "B1", "B2", "B3")' in agent.read_text(encoding="utf-8"),
        "evaluator_uses_production_loader": "adapter._load_original = load_compatible_evaluator_for_base_adapter" in entry.read_text(encoding="utf-8"),
        "evaluator_old_builder_absent": "build_compatible_evaluator_ast" not in entry.read_text(encoding="utf-8"),
    }
    passed = all(item["exit_code"] == 0 for item in results) and contract == {
        "agent_exact_run_id_occurrences": 1,
        "agent_old_run_id_occurrences": 0,
        "agent_schedule_literal_present": True,
        "evaluator_uses_production_loader": True,
        "evaluator_old_builder_absent": True,
    }
    payload = {
        "schema_version": "driveclarify.m3e_p3_cpu_check.v1",
        "run_id": run_id,
        "checked_at_utc": utc_now(),
        "status": "PASS" if passed else "FAIL",
        "contract": contract,
        "commands": results,
        "real_system_launches": 0,
    }
    path = output / "CPU_CHECK_RESULTS.json"
    if path.exists():
        atomic_replace_json(path, payload)
    else:
        atomic_create_json(path, payload)
    append_command_log(output, "- CPU repair-loop check: `{}`; exit codes={}.".format(payload["status"], [item["exit_code"] for item in results]))
    if not passed:
        raise CampaignError("CPU_CHECK_FAILED")


def prelaunch_selftest(run_id: str) -> None:
    output = require_run_id(run_id)
    if (output / "AUTHORIZATION_RECEIPT.json").exists():
        raise CampaignError("SELFTEST_MUST_PRECEDE_RECEIPT")
    cpu = json.loads((output / "CPU_CHECK_RESULTS.json").read_text(encoding="utf-8"))
    if cpu.get("status") != "PASS":
        raise CampaignError("CPU_CHECK_NOT_PASS")
    evidence = output / "PRELAUNCH_SUPERVISOR_SELFTEST.json"
    if evidence.exists():
        raise CampaignError("SELFTEST_EVIDENCE_ALREADY_EXISTS")
    env = dict(os.environ)
    env.update({"PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1"})
    result = run_command([str(PYTHON38), "-B", "-m", "driveclarify_static_branch.m3e_bound_supervisor", "--selftest-only", "--selftest-output", str(evidence)], ROOT, env)
    if result["exit_code"] != 0 or not evidence.is_file():
        atomic_create_json(output / "logs/prelaunch_selftest_failure.json", result)
        raise CampaignError("PRELAUNCH_SELFTEST_COMMAND_FAILED")
    value = json.loads(evidence.read_text(encoding="utf-8"))
    if value.get("status") != "PASS" or value.get("real_system_launches") != 0 or value.get("binding", {}).get("binding_source") != "DIRECT_SPAWN_RETURN" or value.get("unrelated_pid_survived_bound_cleanup") is not True:
        raise CampaignError("PRELAUNCH_SELFTEST_EVIDENCE_INVALID")
    append_command_log(output, "- Pre-receipt production supervisor dummy self-test: `PASS`; direct binding and pidfd cleanup verified.")


def source_pins(output: Path) -> List[Dict[str, Any]]:
    rows = [
        (CARLA_BINARY, "final_unreal_executable"),
        (PYTHON38, "python_runtime"),
        (CHECKPOINT, "simlingo_checkpoint"),
        (CONFIG, "simlingo_config"),
        (FIXTURE, "scenario_free_route_fixture"),
        (TOPOLOGY, "branch_topology"),
        (THRESHOLDS, "mapping_thresholds"),
        (ROUTE_VALIDATION, "evaluator_compatibility_adapter"),
        (output / "M3E_REAL_SIMLINGO_STATIC_BRANCH_AGENT.py", "m3e_runtime_agent"),
        (output / "M3E_EVALUATOR_ENTRY.py", "m3e_evaluator_entry"),
        (PROCESS_BINDING, "process_binding_implementation"),
        (BOUND_SUPERVISOR, "bound_supervisor_adapter"),
        (SELFTEST_SPEC, "prelaunch_selftest_specification"),
        (BASE_ADAPTER, "base_no_launch_evaluator_adapter"),
        (FOUNDATION, "candidate_runtime_foundation"),
        (EVALUATOR, "production_evaluator_source"),
        (AGENT_WRAPPER, "production_agent_wrapper"),
    ]
    result = []
    for path, role in rows:
        row: Dict[str, Any] = {"path": str(path), "role": role, "sha256": sha256_path(path)}
        if role in {"final_unreal_executable", "python_runtime"}:
            row["bytes"] = path.stat().st_size
        result.append(row)
    return result


def environment_preflight() -> Dict[str, Any]:
    env = dict(os.environ)
    env.update({"DISPLAY": ":1", "XAUTHORITY": "/run/user/1000/gdm/Xauthority"})
    xrandr = run_command(["xrandr", "--query"], ROOT, env)
    compute = run_command(["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory", "--format=csv,noheader,nounits"], ROOT)
    process_rows = run_command(["ps", "-eo", "pid,ppid,stat,cmd"], ROOT)["output"]
    forbidden = [line for line in process_rows.splitlines() if any(token in line for token in ("CarlaUE4-Linux-Shipping", "M3E_EVALUATOR_ENTRY.py"))]
    return {
        "display": ":1",
        "xauthority": "/run/user/1000/gdm/Xauthority",
        "physical_display_pass": xrandr["exit_code"] == 0 and "DP-0 connected primary" in xrandr["output"],
        "xrandr_output": xrandr["output"],
        "headless_or_offscreen": False,
        "ports_free": {"2027": port_free(2027), "8027": port_free(8027)},
        "gpu_compute_query": compute,
        "gpu_compute_processes": [line for line in compute["output"].splitlines() if line.strip()],
        "preexisting_real_processes": forbidden,
    }


def authorize(run_id: str) -> None:
    output = require_run_id(run_id)
    if (output / "AUTHORIZATION_RECEIPT.json").exists():
        raise CampaignError("AUTHORIZATION_RECEIPT_ALREADY_EXISTS")
    selftest = json.loads((output / "PRELAUNCH_SUPERVISOR_SELFTEST.json").read_text(encoding="utf-8"))
    cpu = json.loads((output / "CPU_CHECK_RESULTS.json").read_text(encoding="utf-8"))
    if selftest.get("status") != "PASS" or cpu.get("status") != "PASS":
        raise CampaignError("PREAUTHORIZATION_CPU_GATES_NOT_PASS")
    topology = validate_embedded(TOPOLOGY, TOPOLOGY_SHA256)
    threshold = validate_embedded(THRESHOLDS, THRESHOLD_SHA256)
    if sha256_path(FIXTURE) != FIXTURE_SHA256 or sha256_path(EVALUATOR) != EVALUATOR_SHA256 or sha256_path(CHECKPOINT) != CHECKPOINT_SHA256 or sha256_path(CONFIG) != CONFIG_SHA256 or sha256_path(BASE_ADAPTER) != BASE_ADAPTER_SHA256:
        raise CampaignError("FROZEN_SOURCE_PIN_MISMATCH")
    history = protected_history()
    drive_git = git_state(ROOT)
    sim_git = git_state(SIMLINGO)
    if drive_git["branch"] != "master" or drive_git["head"] != "eaa332b1bb994279b59ea5af786fdb5de96adc1b" or drive_git["tracked_diff_bytes"] != 0 or drive_git["staged_diff_bytes"] != 0:
        raise CampaignError("DRIVECLARIFY_GIT_PREFLIGHT_MISMATCH")
    if sim_git["branch"] != "main" or sim_git["head"] != "743b243afd6cf5ff51b9fa1f8cac86f22d569684" or sim_git["tracked_diff_bytes"] != 7722 or sim_git["tracked_diff_sha256"] != "7bd0947ef17df95b56d95ce155468d378a3c282b59f7dee4a2653a7195bc5e34" or sim_git["staged_diff_bytes"] != 0:
        raise CampaignError("SIMLINGO_GIT_PREFLIGHT_MISMATCH")
    environment_state = environment_preflight()
    if not environment_state["physical_display_pass"] or not all(environment_state["ports_free"].values()) or environment_state["gpu_compute_processes"] or environment_state["preexisting_real_processes"]:
        raise CampaignError("REAL_SYSTEM_PREFLIGHT_NOT_CLEAN")

    base = json.loads(P2_SPEC.read_text(encoding="utf-8"))
    spec = deep_replace(base, P2_ID, run_id)
    setup_time, fixed_utc = FIXED_TIMES[run_id]
    spec.update({
        "schema_version": "driveclarify.m3e_static_branch_run_spec.p3.v1",
        "run_id": run_id,
        "run_authorized": True,
        "authorization_consumed": False,
        "authorization_receipt_present": True,
        "execution_status": "AUTHORIZED_NOT_STARTED",
        "automatic_continuation": False,
        "carla_launches_this_package": 0,
        "evaluator_launches_this_package": 0,
        "gpu_uses_this_package": 0,
        "cuda_initializations_this_package": 0,
        "model_forwards_this_package": 0,
        "simlingo_model_or_checkpoint_loads_this_package": 0,
        "source_pins": source_pins(output),
    })
    environment = spec["exact_command"]["environment"]
    environment.update({
        "DRIVECLARIFY_PHASE0A_RUN_ID": run_id,
        "DRIVECLARIFY_PHASE0A_FIXED_SETUP_TIME": setup_time,
        "DRIVECLARIFY_PHASE0A_FIXED_TIME_UTC": fixed_utc,
        "DRIVECLARIFY_M3E_CAPTURE_PLAN": str(output / "CAPTURE_PLAN.json"),
    })
    spec_path = output / "RUN_SPEC.json"
    receipt_path = output / "AUTHORIZATION_RECEIPT.json"
    spec["execution_command"] = [str(PYTHON38), "-B", "-m", "driveclarify_static_branch.m3e_bound_supervisor", "--run-spec", str(spec_path), "--authorization-receipt", str(receipt_path), "--receipt-claim-directory", str(output / "receipt_claims")]
    spec["sha256"] = embedded_sha256(spec)
    atomic_create_json(spec_path, spec)
    authorized_raw = spec_path.read_bytes()
    atomic_create_bytes(output / "RUN_SPEC_AUTHORIZED.json", authorized_raw)
    preflight = {
        "schema_version": "driveclarify.m3e_p3_entry_preflight.v1",
        "run_id": run_id,
        "status": "PASS_READY_FOR_SINGLE_REAL_LAUNCH",
        "verified_at_utc": utc_now(),
        "authority": "USER_EXPLICIT_P3_BOUNDED_AUTONOMOUS_REPAIR_AND_RUN_CAMPAIGN",
        "git_start": {"driveclarify": drive_git, "simlingo": sim_git},
        "protected_history": history,
        "environment": environment_state,
        "authority_hashes": {row["role"]: row["sha256"] for row in spec["source_pins"]},
        "topology_embedded_sha256": topology["sha256"],
        "threshold_embedded_sha256": threshold["sha256"],
        "production_python38_adapter_dry_check": "PASS",
        "pre_receipt_supervisor_selftest": "PASS",
        "candidate_schedule": list(SCHEDULE),
        "frozen_definitions_modified": False,
    }
    atomic_create_json(output / "ENTRY_PREFLIGHT.json", preflight)
    receipt = {
        "schema_version": "driveclarify.m3e_authorization_receipt.p3.v1",
        "receipt_id": "M3E-P3-AUTH-" + run_id + "-" + uuid.uuid4().hex,
        "run_id": run_id,
        "authorization_source": "USER_EXPLICIT_P3_BOUNDED_AUTONOMOUS_REPAIR_AND_RUN_CAMPAIGN",
        "authorization_timestamp_utc": utc_now(),
        "authorized_run_spec": {"path": str(spec_path), "immutable_copy": str(output / "RUN_SPEC_AUTHORIZED.json"), "file_sha256": hashlib.sha256(authorized_raw).hexdigest(), "embedded_sha256": spec["sha256"]},
        "pre_receipt_selftest_sha256": sha256_path(output / "PRELAUNCH_SUPERVISOR_SELFTEST.json"),
        "source_pins": {row["role"]: row["sha256"] for row in spec["source_pins"]},
        "capture_plan": {"file_sha256": sha256_path(output / "CAPTURE_PLAN.json"), "embedded_sha256": json.loads((output / "CAPTURE_PLAN.json").read_text())["sha256"], "frozen_schedule": list(SCHEDULE)},
        "constraints": {"carla_launch_limit": 1, "evaluator_launch_limit": 1, "observation_limit": 1, "candidate_batch_limit": 1, "candidate_forward_limit": 6, "retry": False, "resume": False, "second_observation": False, "headless_or_offscreen": False, "candidate_control_send": False, "training": False},
        "protected_history": history,
        "receipt_reuse_allowed": False,
    }
    atomic_create_json(receipt_path, receipt)
    append_command_log(output, "- Authority/hash/worktree/display/port/GPU/history preflight: `PASS`.\n- Unique authorization receipt created after the pre-receipt self-test; real launch remains NOT_EXECUTED.")


def launch(run_id: str) -> None:
    output = require_run_id(run_id)
    if (output / "REAL_LAUNCH_RECORD.json").exists() or list((output / "receipt_claims").glob("*.receipt-claim.json")):
        raise CampaignError("RUN_ID_ALREADY_LAUNCHED_OR_CLAIMED")
    spec = json.loads((output / "RUN_SPEC.json").read_text(encoding="utf-8"))
    receipt = json.loads((output / "AUTHORIZATION_RECEIPT.json").read_text(encoding="utf-8"))
    if spec.get("run_id") != run_id or receipt.get("run_id") != run_id or spec.get("run_authorized") is not True:
        raise CampaignError("LAUNCH_AUTHORITY_MISMATCH")
    current_environment = environment_preflight()
    if not current_environment["physical_display_pass"] or not all(current_environment["ports_free"].values()) or current_environment["gpu_compute_processes"] or current_environment["preexisting_real_processes"]:
        raise CampaignError("IMMEDIATE_PRELAUNCH_ENVIRONMENT_NOT_CLEAN")
    log_path = output / "logs/supervisor_execution.log"
    descriptor = os.open(str(log_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    launch_env = dict(os.environ)
    launch_env.update({"PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1", "DISPLAY": ":1", "XAUTHORITY": "/run/user/1000/gdm/Xauthority"})
    try:
        process, master_fd = spawn_with_pty(spec["execution_command"], SIMLINGO, launch_env)
    except BaseException:
        os.close(descriptor)
        raise
    record = {
        "schema_version": "driveclarify.m3e_p3_real_launch_record.v1",
        "run_id": run_id,
        "launched_at_utc": utc_now(),
        "supervisor_pid": process.pid,
        "execution_command": spec["execution_command"],
        "launch_count": 1,
        "retry_allowed": False,
        "start_new_session": True,
        "stdout_stderr_transport": "LOCAL_PSEUDOTERMINAL_DRAINED_TO_RUN_LOG",
        "headless_or_graphics_forwarding": False,
    }
    atomic_create_json(output / "REAL_LAUNCH_RECORD.json", record)
    append_command_log(output, "- REAL LAUNCH consumed exactly once at `{}`; supervisor PID={}.".format(record["launched_at_utc"], process.pid))
    try:
        exit_code = drain_pty_to_log(process, master_fd, descriptor, 360.0)
    except subprocess.TimeoutExpired:
        os.close(descriptor)
        append_command_log(output, "- Outer wait exceeded 360 seconds; no relaunch performed. Supervisor owns bounded cleanup.")
        raise CampaignError("REAL_SUPERVISOR_OUTER_WAIT_TIMEOUT")
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
    record["exit_code"] = exit_code
    record["terminal_observed_at_utc"] = utc_now()
    atomic_replace_json(output / "REAL_LAUNCH_RECORD.json", record)
    append_command_log(output, "- REAL LAUNCH terminal exit code: `{}`; no retry/resume.".format(exit_code))


def create_json_if_missing(path: Path, value: Any) -> None:
    if not path.exists():
        atomic_create_json(path, value)


def unknown_mapping(candidate_id: str, reason: str) -> Dict[str, Any]:
    return {
        "candidate_id": candidate_id,
        "mapping_label": "UNKNOWN",
        "projection_distance_m": None,
        "alignment_cosine": None,
        "branch_score": None,
        "score_margin": None,
        "per_branch_evidence": [],
        "evidence_status": "NOT_EVALUATED",
        "reason_codes": [reason],
        "candidate_id_used_for_geometry": False,
        "candidate_name_default_used": False,
        "control_authorized": False,
    }


def flatten_numeric(value: Any) -> Optional[List[float]]:
    result: List[float] = []

    def visit(item: Any) -> bool:
        if isinstance(item, bool):
            return False
        if isinstance(item, (int, float)):
            number = float(item)
            if not math.isfinite(number):
                return False
            result.append(number)
            return True
        if isinstance(item, (list, tuple)):
            return all(visit(child) for child in item)
        return False

    return result if visit(value) and result else None


def pair_metric(left: Any, right: Any) -> Dict[str, Any]:
    left_values = flatten_numeric(left)
    right_values = flatten_numeric(right)
    if left_values is None or right_values is None or len(left_values) != len(right_values):
        return {"status": "UNKNOWN_SHAPE_OR_NUMERIC_EVIDENCE", "l2": None, "rmse": None, "maximum_absolute_difference": None}
    differences = [a - b for a, b in zip(left_values, right_values)]
    squared = sum(value * value for value in differences)
    return {
        "status": "COMPLETE",
        "coordinate_count": len(differences),
        "l2": math.sqrt(squared),
        "rmse": math.sqrt(squared / len(differences)),
        "maximum_absolute_difference": max(abs(value) for value in differences),
        "exact_equal": all(value == 0.0 for value in differences),
    }


def pairwise_metrics(plans: Mapping[str, Mapping[str, Any]], ids: Iterable[Tuple[str, str]], field: str) -> List[Dict[str, Any]]:
    rows = []
    for left, right in ids:
        metric = pair_metric(plans[left].get(field), plans[right].get(field))
        rows.append({"left": left, "right": right, **metric})
    return rows


def probability_between_greater(between: Sequence[Mapping[str, Any]], within: Sequence[Mapping[str, Any]]) -> Optional[float]:
    between_values = [float(row["l2"]) for row in between if row.get("l2") is not None]
    within_values = [float(row["l2"]) for row in within if row.get("l2") is not None]
    comparisons = [(left, right) for left in between_values for right in within_values]
    if not comparisons:
        return None
    return sum(1 for left, right in comparisons if left > right) / len(comparisons)


def median_l2(rows: Sequence[Mapping[str, Any]]) -> Optional[float]:
    values = [float(row["l2"]) for row in rows if row.get("l2") is not None]
    return statistics.median(values) if values else None


def first_error_evidence(output: Path, runtime_result: Mapping[str, Any], supervisor_status: Mapping[str, Any]) -> Dict[str, Any]:
    error = runtime_result.get("first_error")
    if isinstance(error, Mapping):
        return dict(error)
    log_path = output / "logs/supervisor_execution.log"
    tail = ""
    if log_path.is_file():
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        tail = "\n".join(lines[-40:])
    return {
        "type": "SUPERVISOR_OR_EVALUATOR_TERMINAL",
        "message": supervisor_status.get("error"),
        "log_tail": tail,
    }


def scientific_failure_message(message: str) -> bool:
    tokens = (
        "FIRST_MODEL_READY_OBSERVATION_INELIGIBLE",
        "M3E_PRE_FORWARD_FROZEN_STATE_MISMATCH",
        "M3E_POST_FORWARD_FROZEN_STATE_MISMATCH",
        "FAIL_CLOSED_CONTROL_NONINTERFERENCE",
        "M3E_FAIRNESS_CORE_GATE_NOT_PASS",
        "MODEL_FORWARD_COUNT_NOT_SIX",
        "WITHIN_CANDIDATE_NONREPEATABLE",
    )
    return any(token in message for token in tokens)


def binding_artifact(run_id: str, role: str, child: Optional[Mapping[str, Any]], status: Mapping[str, Any]) -> Dict[str, Any]:
    audit = status.get("process_scan_audit", [])
    if child is None:
        return {
            "schema_version": "driveclarify.m3e_real_process_binding.v1",
            "run_id": run_id,
            "role": role,
            "binding_status": "NOT_EXECUTED",
            "reason": "REAL_PROCESS_NOT_SPAWNED_BEFORE_TERMINAL_FAILURE",
        }
    pid = child["pid"]
    revalidations = [row for row in audit if row.get("pid") == pid and row.get("status") == "DIRECT_BINDING_REVALIDATED"]
    required_stage = "CARLA_LAUNCHED" if role == "carla" else "EVALUATOR_LAUNCHED"
    stage_pass = any(row.get("stage") == required_stage for row in revalidations)
    value = {
        "schema_version": "driveclarify.m3e_real_process_binding.v1",
        "run_id": run_id,
        "role": role,
        "binding_status": "PASS_DIRECT_SPAWN_BINDING_AT_LAUNCH",
        "binding": child.get("supervisor_process_binding"),
        "identity_at_launch": child.get("identity_at_launch"),
        "launch_timestamp_utc": child.get("launch_timestamp_utc"),
        "auxiliary_tag_authority": child.get("run_id_tag_authority"),
        "explicit_direct_binding_revalidation": {
            "status": "PASS" if stage_pass else "FAIL_MISSING_PERSISTED_REVALIDATION",
            "required_stage": required_stage,
            "evidence": revalidations,
        },
        "exit_code": status.get("evaluator_exit_code") if role == "evaluator" else child.get("popen_state_after_cleanup", {}).get("poll"),
        "cleanup_terminal_observation": child.get("popen_state_after_cleanup"),
    }
    return value


def finalize(run_id: str) -> str:
    output = require_run_id(run_id)
    launch_record = json.loads((output / "REAL_LAUNCH_RECORD.json").read_text(encoding="utf-8"))
    status_path = output / "logs/supervisor_status.json"
    if not status_path.is_file():
        raise CampaignError("SUPERVISOR_STATUS_MISSING_AFTER_REAL_LAUNCH")
    status = json.loads(status_path.read_text(encoding="utf-8"))
    if status.get("run_id") != run_id:
        raise CampaignError("SUPERVISOR_STATUS_RUN_ID_MISMATCH")
    runtime_result_path = output / "RUN_RESULT.json"
    runtime_result: Dict[str, Any] = {}
    if runtime_result_path.is_file():
        runtime_result = json.loads(runtime_result_path.read_text(encoding="utf-8"))
    children = {row["role"]: row for row in status.get("children", [])}
    carla_binding = binding_artifact(run_id, "carla", children.get("carla"), status)
    evaluator_binding = binding_artifact(run_id, "evaluator", children.get("evaluator"), status)
    create_json_if_missing(output / "CARLA_PROCESS_BINDING.json", carla_binding)
    create_json_if_missing(output / "EVALUATOR_PROCESS_BINDING.json", evaluator_binding)

    capture = json.loads((output / "CAPTURE_PLAN.json").read_text(encoding="utf-8"))
    schedule_path = output / "CANDIDATE_SCHEDULE.json"
    if not schedule_path.exists():
        schedule = {
            "schema_version": "driveclarify.m3e_candidate_schedule.v1",
            "run_id": run_id,
            "candidate_order": list(SCHEDULE),
            "frozen_before_model_output": True,
            "derived_from_candidate_or_model_output": False,
            "source_capture_plan_embedded_sha256": capture["sha256"],
            "schedule_provenance": capture["schedule_provenance"],
            "candidate_batch_started": False,
        }
        schedule["schedule_sha256"] = digest_value(schedule)
        atomic_create_json(schedule_path, schedule)
    schedule = json.loads(schedule_path.read_text(encoding="utf-8"))

    first_path = output / "FIRST_OBSERVATION_EVIDENCE.json"
    if not first_path.exists():
        atomic_create_json(first_path, {
            "schema_version": "driveclarify.m3e_first_observation_evidence.v1",
            "run_id": run_id,
            "status": "UNKNOWN_NOT_REACHED_ENGINEERING_FAILURE",
            "observation_id": None,
            "observation_sequence_index": None,
            "signed_station_to_decision_point_m": None,
            "required_interval_m": [-8.3, 0.0],
            "required_interval_upper_exclusive": True,
            "eligibility": "UNKNOWN_NOT_EVALUATED",
            "second_observation_attempted": False,
            "reason_codes": ["EVALUATOR_OR_AGENT_FAILED_BEFORE_FIRST_MODEL_READY_OBSERVATION"],
        })
    first = json.loads(first_path.read_text(encoding="utf-8"))
    eligibility = first.get("plan_horizon_eligibility")
    if not isinstance(eligibility, Mapping):
        eligibility = runtime_result.get("observation_eligibility") if isinstance(runtime_result.get("observation_eligibility"), Mapping) else {}
    eligibility_pass = eligibility.get("verdict") == "PASS" and eligibility.get("eligible_for_candidate_batch") is True
    observation_reached = first.get("observation_id") is not None

    for candidate_id in SCHEDULE:
        create_json_if_missing(output / (candidate_id + "_PLAN.json"), {
            "schema_version": "driveclarify.m3e_static_branch_plan.v1",
            "run_id": run_id,
            "candidate_id": candidate_id,
            "candidate_group": candidate_id[0],
            "candidate_schedule_position": SCHEDULE.index(candidate_id) + 1,
            "completion_status": "NOT_EXECUTED",
            "reason_code": "PLAN_OUTPUT_MISSING_BEFORE_CANDIDATE_FORWARD",
            "source_observation_id": first.get("observation_id"),
            "model_forward_executed": False,
            "plan_hash": None,
            "speed_hash": None,
            "exception": status.get("error"),
            "control_authorized": False,
        })
    plans = {candidate_id: json.loads((output / (candidate_id + "_PLAN.json")).read_text(encoding="utf-8")) for candidate_id in SCHEDULE}
    complete_ids = [candidate_id for candidate_id, plan in plans.items() if plan.get("completion_status") == "COMPLETE"]

    counts_path = output / "RUNTIME_COUNTS.json"
    if counts_path.exists():
        runtime_counts = json.loads(counts_path.read_text(encoding="utf-8"))
    else:
        runtime_counts = {"run_id": run_id, "candidate_batch_started": False, "model_forward_count": 0, "world_tick": 0, "pid": 0, "planner_advance": 0, "control_send": 0, "scenario_actor_mutation": 0, "baseline_control_consumption": 0}
    runtime_counts.update({
        "schema_version": "driveclarify.m3e_runtime_counts.v1",
        "run_id": run_id,
        "pre_receipt_selftest": 1,
        "inner_launch_gate_selftest": 1,
        "carla_launch": 1 if "carla" in children else 0,
        "evaluator_launch": 1 if "evaluator" in children else 0,
        "checkpoint_load": 1 if observation_reached else 0,
        "model_load": 1 if observation_reached else 0,
        "model_ready_observation": 1 if observation_reached else 0,
        "model_forward": len(complete_ids),
        "candidate_A_complete": len([item for item in complete_ids if item.startswith("A")]),
        "candidate_B_complete": len([item for item in complete_ids if item.startswith("B")]),
        "candidate_batch": 1 if complete_ids else 0,
        "retry": 0,
        "second_observation": 0,
        "second_real_run_same_id": 0,
    })
    if counts_path.exists():
        atomic_replace_json(counts_path, runtime_counts)
    else:
        atomic_create_json(counts_path, runtime_counts)

    fairness_path = output / "FAIRNESS_V2_RESULT.json"
    if not fairness_path.exists():
        zero = {name: runtime_counts.get(name, 0) for name in ("world_tick", "pid", "planner_advance", "control_send", "scenario_actor_mutation", "baseline_control_consumption")}
        atomic_create_json(fairness_path, {
            "schema_version": "driveclarify.counterfactual_fairness.v2.not_evaluated",
            "run_id": run_id,
            "contract_name": "CounterfactualFairnessContractV2",
            "overall_candidate_comparison_eligibility": {"verdict": "UNKNOWN", "reason_codes": ["CANDIDATE_BATCH_NOT_COMPLETED"]},
            "state_identity_fairness": {"verdict": "UNKNOWN"},
            "model_state_stability": {"verdict": "UNKNOWN"},
            "candidate_semantic_isolation": {"verdict": "UNKNOWN"},
            "candidate_schedule_provenance": {"verdict": "PASS", "valid": True, "schedule_sha256": schedule.get("schedule_sha256")},
            "scenario_timing_suitability": {"classification": "UNKNOWN"},
            "control_noninterference": {"verdict": "PASS" if all(value == 0 for value in zero.values()) else "FAIL", "counts": zero},
        })
    fairness = json.loads(fairness_path.read_text(encoding="utf-8"))
    fairness_pass = fairness.get("overall_candidate_comparison_eligibility", {}).get("verdict") == "PASS"
    control_counts = {name: runtime_counts.get(name) for name in ("world_tick", "pid", "planner_advance", "control_send", "scenario_actor_mutation", "baseline_control_consumption")}
    control_zero = all(value == 0 for value in control_counts.values())

    mapper_results: Dict[str, Any] = {}
    mapper_invocations = 0
    if fairness_pass and len(complete_ids) == 6:
        from driveclarify_static_branch.mapper import StaticBranchPlanMapperV1

        topology = json.loads(TOPOLOGY.read_text(encoding="utf-8"))
        thresholds = json.loads(THRESHOLDS.read_text(encoding="utf-8"))
        ego_pose = first.get("ego_pose", {})
        rotation = ego_pose.get("rotation_roll_pitch_yaw_degrees", [None, None, None])
        location = ego_pose.get("location_xyz", [None, None, None])
        transform = {"source_frame": "CARLA_WORLD", "target_frame": "EGO_LOCAL_X_FORWARD_Y_RIGHT", "location_xy_world_m": location[:2], "yaw_degrees": rotation[2], "evidence_status": "VERIFIED" if first.get("world_frame") == "CARLA_WORLD" and first.get("world_unit") == "METRE" else "UNVERIFIED"}
        mapper = StaticBranchPlanMapperV1(thresholds)
        for candidate_id in SCHEDULE:
            mapper_results[candidate_id] = mapper.map_plan(topology, plans[candidate_id], transform)
            mapper_invocations += 1
        mapper_status = "COMPLETE_UNKNOWN_PRESERVED"
    else:
        reason = "FAIRNESS_CORE_GATE_NOT_PASS_NO_MAPPER_INVOCATION" if not fairness_pass else "SIX_COMPLETE_PLANS_REQUIRED_NO_MAPPER_INVOCATION"
        mapper_results = {candidate_id: unknown_mapping(candidate_id, reason) for candidate_id in SCHEDULE}
        mapper_status = "NOT_RUN_" + reason
    atomic_create_json(output / "MAPPER_RESULTS.json", {
        "schema_version": "driveclarify.m3e_static_branch_mapper_results.v1",
        "run_id": run_id,
        "mapper_name": "StaticBranchPlanMapperV1",
        "mapper_invocation_count": mapper_invocations,
        "status": mapper_status,
        "results": mapper_results,
        "unknown_preserved": True,
        "nearest_branch_forcing_used": False,
    })
    runtime_counts["mapper_invocation"] = mapper_invocations
    atomic_replace_json(counts_path, runtime_counts)

    if len(complete_ids) == 6:
        within_pairs_a = list(itertools.combinations(("A1", "A2", "A3"), 2))
        within_pairs_b = list(itertools.combinations(("B1", "B2", "B3"), 2))
        between_pairs = list(itertools.product(("A1", "A2", "A3"), ("B1", "B2", "B3")))
        route_within_a = pairwise_metrics(plans, within_pairs_a, "raw_predicted_route")
        route_within_b = pairwise_metrics(plans, within_pairs_b, "raw_predicted_route")
        route_between = pairwise_metrics(plans, between_pairs, "raw_predicted_route")
        speed_within_a = pairwise_metrics(plans, within_pairs_a, "raw_predicted_speed_profile")
        speed_within_b = pairwise_metrics(plans, within_pairs_b, "raw_predicted_speed_profile")
        speed_between = pairwise_metrics(plans, between_pairs, "raw_predicted_speed_profile")
        route_within = route_within_a + route_within_b
        speed_within = speed_within_a + speed_within_b
        labels_a = [mapper_results[item]["mapping_label"] for item in ("A1", "A2", "A3")]
        labels_b = [mapper_results[item]["mapping_label"] for item in ("B1", "B2", "B3")]
        repeatability = {"A": "3/3_AGREE:" + labels_a[0] if len(set(labels_a)) == 1 else "NONREPEATABLE", "B": "3/3_AGREE:" + labels_b[0] if len(set(labels_b)) == 1 else "NONREPEATABLE"}
        route_between_median, route_within_median = median_l2(route_between), median_l2(route_within)
        speed_between_median, speed_within_median = median_l2(speed_between), median_l2(speed_within)
        rq1_status = "COMPLETE_SUPPORTS_BETWEEN_GREATER_THAN_WITHIN" if route_between_median is not None and route_within_median is not None and route_between_median > route_within_median else "COMPLETE_DOES_NOT_SUPPORT_BETWEEN_GREATER_THAN_WITHIN"
        rq1 = {
            "schema_version": "driveclarify.m3e_rq1_behavioral_identifiability.v1",
            "run_id": run_id,
            "status": rq1_status,
            "experimental_unit_count": 1,
            "independent_sample_count_claimed": 0,
            "within_A_route": route_within_a,
            "within_B_route": route_within_b,
            "between_A_B_route": route_between,
            "within_A_speed": speed_within_a,
            "within_B_speed": speed_within_b,
            "between_A_B_speed": speed_between,
            "median_between_minus_within_route": None if route_between_median is None or route_within_median is None else route_between_median - route_within_median,
            "median_between_minus_within_speed": None if speed_between_median is None or speed_within_median is None else speed_between_median - speed_within_median,
            "probability_between_greater_than_within_route": probability_between_greater(route_between, route_within),
            "probability_between_greater_than_within_speed": probability_between_greater(speed_between, speed_within),
            "branch_label_repeatability": repeatability,
            "candidate_swap_consistency": "NOT_ESTIMABLE_SINGLE_PREDECLARED_SCHEDULE",
            "unknown_rate": sum(1 for row in mapper_results.values() if row["mapping_label"] == "UNKNOWN") / 6.0,
            "schedule_order_evidence": schedule,
        }
    else:
        rq1_status = "UNKNOWN_NO_SIX_COMPLETE_PLANS"
        rq1 = {
            "schema_version": "driveclarify.m3e_rq1_behavioral_identifiability.v1",
            "run_id": run_id,
            "status": rq1_status,
            "experimental_unit_count": 1,
            "independent_sample_count_claimed": 0,
            "within_A_route": [], "within_B_route": [], "between_A_B_route": [],
            "within_A_speed": [], "within_B_speed": [], "between_A_B_speed": [],
            "median_between_minus_within_route": None,
            "median_between_minus_within_speed": None,
            "probability_between_greater_than_within_route": None,
            "probability_between_greater_than_within_speed": None,
            "branch_label_repeatability": {"A": "UNKNOWN", "B": "UNKNOWN"},
            "unknown_rate": 1.0,
            "schedule_order_evidence": schedule,
            "reason_codes": ["SIX_COMPLETE_PLANS_REQUIRED", "COMPLETED=" + str(len(complete_ids))],
        }
    atomic_create_json(output / "RQ1_BEHAVIORAL_IDENTIFIABILITY.json", rq1)

    labels_a = [mapper_results[item]["mapping_label"] for item in ("A1", "A2", "A3")]
    labels_b = [mapper_results[item]["mapping_label"] for item in ("B1", "B2", "B3")]
    group_mapping: Dict[str, Mapping[str, Any]] = {}
    if len(set(labels_a)) == 1:
        group_mapping["A"] = {"mapping_label": labels_a[0]}
    if len(set(labels_b)) == 1:
        group_mapping["B"] = {"mapping_label": labels_b[0]}
    from driveclarify_static_branch.mapper import evaluate_task_pair
    task_pair = evaluate_task_pair(group_mapping, {"A": "STRAIGHT_BRANCH", "B": "RIGHT_TURN_BRANCH"}, {"STRAIGHT_BRANCH": "STRAIGHT_TASK", "RIGHT_TURN_BRANCH": "RIGHT_TASK"})
    rq2_label = task_pair["pair_class"]
    atomic_create_json(output / "RQ2_TASK_RELEVANCE.json", {
        "schema_version": "driveclarify.m3e_rq2_task_relevance.v1",
        "run_id": run_id,
        "candidate_status": task_pair["candidate_task_status"],
        "pair_label": rq2_label,
        "independent_task_binding": {"A": "STRAIGHT_BRANCH", "B": "RIGHT_TURN_BRANCH"},
        "independent_task_equivalence_classes": {"STRAIGHT_BRANCH": "STRAIGHT_TASK", "RIGHT_TURN_BRANCH": "RIGHT_TASK"},
        "mapping_evidence": mapper_results,
        "task_pair_evidence": task_pair,
        "unknown_preserved": True,
    })

    cleanup_pass = status.get("cleanup_complete") is True and all(status.get("ports_free_after_cleanup", {}).values()) and not status.get("surviving_processes") and not status.get("owned_descendants_after_cleanup") and not status.get("tagged_processes_after_cleanup") and not status.get("gpu_compute_after_cleanup", {}).get("processes")
    cleanup_saved = status.get("cleanup", {}).get("saved_children", {})
    atomic_create_json(output / "PROCESS_CLEANUP.json", {
        "schema_version": "driveclarify.m3e_process_cleanup.v1",
        "run_id": run_id,
        "status": "PASS_COMPLETE" if cleanup_pass else "FAIL_INCOMPLETE",
        "cleanup_attempted": status.get("cleanup_attempted"),
        "cleanup_completed": status.get("cleanup_completed"),
        "target_source": "DIRECT_POPEN_SAVED_IDENTITY_WITH_BOUND_PID_STARTTIME_EXECUTABLE_COMMAND",
        "transport": "PIDFD",
        "process_results": cleanup_saved.get("process_results", []),
        "signals_sent": cleanup_saved.get("signals_sent", []),
        "ports_free_after_cleanup": status.get("ports_free_after_cleanup"),
        "gpu_compute_after_cleanup": status.get("gpu_compute_after_cleanup"),
        "surviving_processes": status.get("surviving_processes"),
        "owned_descendants_after_cleanup": status.get("owned_descendants_after_cleanup"),
        "tagged_processes_after_cleanup": status.get("tagged_processes_after_cleanup"),
        "global_string_scan_signal_targets": 0,
    })
    gpu = run_command(["nvidia-smi", "--query-gpu=index,name,uuid,display_active,memory.total,memory.used", "--format=csv,noheader,nounits"], ROOT)
    compute = run_command(["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory", "--format=csv,noheader,nounits"], ROOT)
    log_text = (output / "logs/supervisor_execution.log").read_text(encoding="utf-8", errors="replace")
    atomic_create_json(output / "GPU_RESOURCE_RECORD.json", {
        "schema_version": "driveclarify.m3e_gpu_resource_record.v1",
        "run_id": run_id,
        "gpu_query_after_cleanup": gpu,
        "compute_processes_after_cleanup": [line for line in compute["output"].splitlines() if line.strip()],
        "cuda_or_model_used": observation_reached,
        "oom": "out of memory" in log_text.lower() or "cuda oom" in log_text.lower(),
        "gpu_peak_memory_mib": None,
        "gpu_peak_status": "UNKNOWN_NOT_CONTINUOUSLY_SAMPLED",
        "cleanup_compute_process_count": len(status.get("gpu_compute_after_cleanup", {}).get("processes", [])),
    })

    carla_revalidation_pass = carla_binding.get("explicit_direct_binding_revalidation", {}).get("status") == "PASS"
    evaluator_revalidation_pass = evaluator_binding.get("explicit_direct_binding_revalidation", {}).get("status") == "PASS"
    protocol_complete = eligibility_pass and len(complete_ids) == 6 and fairness_pass and control_zero and carla_revalidation_pass and evaluator_revalidation_pass and mapper_invocations == 6 and cleanup_pass
    evidence_text = json.dumps(first_error_evidence(output, runtime_result, status), sort_keys=True)
    if protocol_complete:
        terminal_category = "PROTOCOL_COMPLETE_VALID_RESULT"
        terminal_verdict = "P3_PROTOCOL_COMPLETE_VALID_RESULT"
    elif observation_reached and not eligibility_pass:
        terminal_category = "SCIENTIFIC_OR_FROZEN_GATE_RESULT"
        terminal_verdict = "P3_VALID_SCIENTIFIC_OR_EVIDENCE_RESULT:FIRST_OBSERVATION_INELIGIBLE"
    elif scientific_failure_message(evidence_text) or (len(complete_ids) == 6 and (not fairness_pass or not control_zero)):
        terminal_category = "SCIENTIFIC_OR_FROZEN_GATE_RESULT"
        terminal_verdict = "P3_VALID_SCIENTIFIC_OR_EVIDENCE_RESULT:FROZEN_GATE_OR_STABILITY_RESULT"
    else:
        terminal_category = "ENGINEERING_FAILURE"
        terminal_verdict = "P3_ENGINEERING_FAILURE_TERMINAL_RUN"
    if not cleanup_pass:
        terminal_category = "ENGINEERING_FAILURE"
        terminal_verdict = "P3_ENGINEERING_FAILURE_TERMINAL_RUN:CLEANUP_INCOMPLETE"

    claims = list((output / "receipt_claims").glob("*.receipt-claim.json"))
    receipt = json.loads((output / "AUTHORIZATION_RECEIPT.json").read_text(encoding="utf-8"))
    spec_path = output / "RUN_SPEC.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec.update({
        "run_authorized": False,
        "authorization_consumed": True,
        "execution_status": terminal_verdict,
        "carla_launches_this_package": runtime_counts["carla_launch"],
        "evaluator_launches_this_package": runtime_counts["evaluator_launch"],
        "gpu_uses_this_package": 1 if runtime_counts["carla_launch"] else 0,
        "cuda_initializations_this_package": 1 if observation_reached else 0,
        "model_forwards_this_package": len(complete_ids),
        "simlingo_model_or_checkpoint_loads_this_package": 1 if observation_reached else 0,
    })
    spec["sha256"] = embedded_sha256(spec)
    atomic_replace_json(spec_path, spec)
    atomic_create_json(output / "AUTHORIZATION_CONSUMPTION.json", {
        "schema_version": "driveclarify.m3e_authorization_consumption.p3.v1",
        "run_id": run_id,
        "receipt_id": receipt["receipt_id"],
        "receipt_sha256": sha256_path(output / "AUTHORIZATION_RECEIPT.json"),
        "claim_count": len(claims),
        "claims": [{"path": str(path), "sha256": sha256_path(path)} for path in claims],
        "authorized_run_spec_sha256": sha256_path(output / "RUN_SPEC_AUTHORIZED.json"),
        "terminal_run_spec_sha256": sha256_path(spec_path),
        "authorization_consumed": True,
        "reusable": False,
        "retry_allowed": False,
    })
    error_evidence = first_error_evidence(output, runtime_result, status)
    final_result = {
        "schema_version": "driveclarify.m3e_static_branch_run_result.p3.v1",
        "run_id": run_id,
        "terminal_category": terminal_category,
        "final_implementation_runtime_verdict": terminal_verdict,
        "original_runtime_result": runtime_result,
        "first_runtime_exception": error_evidence,
        "supervisor_terminal_error": status.get("error"),
        "supervisor_exit_code": launch_record.get("exit_code"),
        "prelaunch_selftest": "PASS",
        "carla_binding": carla_binding.get("binding_status"),
        "carla_pre_evaluator_revalidation": carla_binding.get("explicit_direct_binding_revalidation", {}).get("status"),
        "evaluator_binding": evaluator_binding.get("binding_status"),
        "evaluator_post_spawn_revalidation": evaluator_binding.get("explicit_direct_binding_revalidation", {}).get("status"),
        "observation_eligibility": eligibility,
        "fairness_v2": fairness.get("overall_candidate_comparison_eligibility"),
        "candidate_capture_status": "A{}_B{}".format(runtime_counts["candidate_A_complete"], runtime_counts["candidate_B_complete"]),
        "completed_candidates": complete_ids,
        "mapper_status": mapper_status,
        "rq1_status": rq1_status,
        "rq2_label": rq2_label,
        "runtime_counts": runtime_counts,
        "cleanup": "PASS_COMPLETE" if cleanup_pass else "FAIL_INCOMPLETE",
        "retry_attempted": False,
        "second_observation_attempted": False,
        "second_run_same_id_attempted": False,
        "dedicated_m3e_runner_used": False,
        "frozen_experiment_definition_modified": False,
        "automatic_continuation": terminal_category == "ENGINEERING_FAILURE" and run_id != RUN_IDS[-1],
        "terminalized_at_utc": utc_now(),
    }
    if runtime_result_path.exists():
        atomic_replace_json(runtime_result_path, final_result)
    else:
        atomic_create_json(runtime_result_path, final_result)
    append_command_log(output, "- Terminal category: `{}`.\n- Terminal verdict: `{}`.\n- Observation reached/eligible: `{}/{}`; plans: `{}/6`; fairness/core: `{}`; mapper invocations: `{}`.\n- Cleanup: `{}`; same-Run-ID retry/resume: 0.".format(terminal_category, terminal_verdict, observation_reached, eligibility_pass, len(complete_ids), fairness.get("overall_candidate_comparison_eligibility", {}).get("verdict"), mapper_invocations, "PASS" if cleanup_pass else "FAIL"))

    staging = json.loads((output / "STAGING_CONTEXT.json").read_text(encoding="utf-8"))
    drive_end = git_state(ROOT)
    sim_end = git_state(SIMLINGO)
    sim_start = staging["git_before_run_staging"]["simlingo"]
    sim_unchanged = all(sim_end[key] == sim_start[key] for key in ("branch", "head", "tracked_diff_bytes", "tracked_diff_sha256", "staged_diff_bytes", "staged_diff_sha256", "untracked_file_count", "untracked_path_list_nul_sha256"))
    history = protected_history()
    atomic_create_json(output / "GIT_START_END.json", {
        "schema_version": "driveclarify.m3e_p3_git_start_end.v1",
        "run_id": run_id,
        "start_before_staging": staging["git_before_run_staging"],
        "end": {"driveclarify": drive_end, "simlingo": sim_end},
        "driveclarify_new_untracked_paths": sorted(set(drive_end["untracked_paths"]) - set(staging["git_before_run_staging"]["driveclarify"]["untracked_paths"])),
        "simlingo_unchanged": sim_unchanged,
        "protected_history": history,
        "git_commit_reset_clean_restore_checkout_used": False,
    })
    required = [
        "AUTHORIZATION_RECEIPT.json", "RUN_RESULT.json", "ENTRY_PREFLIGHT.json", "PRELAUNCH_SUPERVISOR_SELFTEST.json",
        "CARLA_PROCESS_BINDING.json", "EVALUATOR_PROCESS_BINDING.json", "FIRST_OBSERVATION_EVIDENCE.json", "FAIRNESS_V2_RESULT.json",
        "CANDIDATE_SCHEDULE.json", "A1_PLAN.json", "A2_PLAN.json", "A3_PLAN.json", "B1_PLAN.json", "B2_PLAN.json", "B3_PLAN.json",
        "MAPPER_RESULTS.json", "RQ1_BEHAVIORAL_IDENTIFIABILITY.json", "RQ2_TASK_RELEVANCE.json", "RUNTIME_COUNTS.json",
        "GPU_RESOURCE_RECORD.json", "PROCESS_CLEANUP.json", "COMMAND_LOG.md",
    ]
    missing = [name for name in required if not (output / name).is_file()]
    if missing:
        raise CampaignError("REQUIRED_TERMINAL_ARTIFACTS_MISSING:" + ",".join(missing))
    artifacts = []
    for path in sorted(item for item in output.rglob("*") if item.is_file() and item.name != "ARTIFACT_INVENTORY.json"):
        artifacts.append({"path": path.relative_to(output).as_posix(), "bytes": path.stat().st_size, "sha256": sha256_path(path)})
    atomic_create_json(output / "ARTIFACT_INVENTORY.json", {
        "schema_version": "driveclarify.m3e_p3_artifact_inventory.v1",
        "run_id": run_id,
        "terminal_status": terminal_verdict,
        "required_artifacts_present": True,
        "artifact_count_excluding_inventory": len(artifacts),
        "artifacts": artifacts,
        "inventory_self_hash_included": False,
        "protected_history": history,
        "simlingo_unchanged": sim_unchanged,
        "synthetic_or_imputed_plan_count": 0,
        "unknown_preserved": True,
    })
    return terminal_category


def campaign_result() -> Dict[str, Any]:
    used = []
    for run_id in RUN_IDS:
        path = RUN_OUTPUTS / run_id / "RUN_RESULT.json"
        if path.is_file():
            value = json.loads(path.read_text(encoding="utf-8"))
            used.append({"run_id": run_id, "terminal_category": value.get("terminal_category"), "terminal_verdict": value.get("final_implementation_runtime_verdict")})
    if used and used[-1]["terminal_category"] == "PROTOCOL_COMPLETE_VALID_RESULT":
        status = "P3_PROTOCOL_COMPLETE_VALID_RESULT"
    elif used and used[-1]["terminal_category"] == "SCIENTIFIC_OR_FROZEN_GATE_RESULT":
        status = "P3_VALID_SCIENTIFIC_OR_EVIDENCE_RESULT"
    elif len(used) == 3 and all(row["terminal_category"] == "ENGINEERING_FAILURE" for row in used):
        status = "P3_ENGINEERING_ATTEMPTS_EXHAUSTED_AFTER_THREE_BOUND_RUNS"
    else:
        status = "P3_BLOCKED_REQUIRES_USER_DECISION"
    value = {
        "schema_version": "driveclarify.m3e_p3_bounded_campaign_result.v1",
        "campaign_status": status,
        "preauthorized_run_ids": list(RUN_IDS),
        "used_runs": used,
        "unused_runs": [run_id for run_id in RUN_IDS if run_id not in {row["run_id"] for row in used}],
        "dedicated_m3e_runner_used": False,
        "p1_p2_r2_history": protected_history(),
        "generated_at_utc": utc_now(),
    }
    path = REPORT / "M3E_P3_CAMPAIGN_RESULT.json"
    if path.exists():
        atomic_replace_json(path, value)
    else:
        atomic_create_json(path, value)
    return value


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("stage", "cpu-check", "selftest", "authorize", "launch", "finalize", "campaign-result"))
    parser.add_argument("--run-id", choices=RUN_IDS)
    args = parser.parse_args(argv)
    if args.action != "campaign-result" and not args.run_id:
        parser.error("--run-id is required")
    if args.action == "stage":
        stage(args.run_id)
    elif args.action == "cpu-check":
        cpu_check(args.run_id)
    elif args.action == "selftest":
        prelaunch_selftest(args.run_id)
    elif args.action == "authorize":
        authorize(args.run_id)
    elif args.action == "launch":
        launch(args.run_id)
    elif args.action == "finalize":
        print(finalize(args.run_id))
    else:
        print(json.dumps(campaign_result(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
