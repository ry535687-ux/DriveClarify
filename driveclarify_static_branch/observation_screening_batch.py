"""Authorized multi-topology first-observation screening batch runner.

The orchestration layer is standard-library-only.  Real CARLA/SimLingo work is
reachable solely through one receipt-bound launch command per authorized Run
ID.  Scientific non-eligibility never authorizes a recovery run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from . import m3e_p3_campaign as p3
from .observation_package import (
    atomic_create_bytes,
    atomic_create_json,
    atomic_replace_json,
    digest_value,
    sha256_path,
    unavailable_manifest,
)
from .run_result_lifecycle import (
    STAGE_A_RUNTIME_RESULT_FILE,
    DuplicateTerminalResultError,
    publish_terminal_result,
    read_run_lifecycle,
)


ROOT = Path("/home/buaa/wrh/DriveClarify")
SIMLINGO = Path("/home/buaa/wrh/simlingo")
AUTHORITY = ROOT / "reports/multi_topology_static_units_v1"
BATCH_ID = "DC-OBS-SCREEN-B1-20260803T113000Z"
BATCH_ROOT = AUTHORITY / "observation_screening_runs" / BATCH_ID
RUNS_ROOT = BATCH_ROOT / "runs"
AGENT_SOURCE = ROOT / "driveclarify_static_branch/observation_screening_agent.py"
PACKAGE_SOURCE = ROOT / "driveclarify_static_branch/observation_package.py"
ORCHESTRATOR_SOURCE = ROOT / "driveclarify_static_branch/observation_screening_batch.py"
OUTPUT_SCHEMA = AUTHORITY / "OBSERVATION_SCREENING_OUTPUT_SCHEMA.json"
SELECTED_MANIFEST = AUTHORITY / "SELECTED_STATIC_UNITS_MANIFEST.json"
BATCH_SPEC = AUTHORITY / "OBSERVATION_SCREENING_BATCH_SPEC.json"
THRESHOLDS = ROOT / "reports/static_maneuver_branch_primary_mvp_v1/MAPPING_THRESHOLD_PROVENANCE.json"
THRESHOLD_FILE_SHA256 = "6a118500243116315ccd8e0943413fadd4845b171e2ea5b5f858aa1504a1e6fc"
THRESHOLD_EMBEDDED_SHA256 = "6dfeea8907eb987c512c2b180a824d78c38d7c57662385c24d0d884bf3a37553"
P3A_INVENTORY_SHA256 = "2ccfa04d6816a230419c8a3a4820ca807fa2e41e45872748d0141ce91c12eadf"
P3B_INVENTORY_SHA256 = "23d2598ec414b5d727a21f8e57ce992628c7153691bdeddd346be1126a83966b"
SIMLINGO_TRACKED_DIFF_SHA256 = "7bd0947ef17df95b56d95ce155468d378a3c282b59f7dee4a2653a7195bc5e34"

# V1 records the exact top-level project states accepted when the frozen
# observation-screening authority was authored.  Do not replace these values
# with a newer project state: they are part of the historical contract.
HISTORICAL_AUTHORITY_ENTRY_STATES_V1 = frozenset(
    {
        "READY_FOR_BATCH_OBSERVATION_ELIGIBILITY_SCREEN",
        "READY_FOR_MULTI_UNIT_OFFLINE_FROZEN_A3_B3_CANDIDATE_CAPTURE_AUTHORIZATION",
        "M1_V3_STAGE_A_TERMINAL_RESULT_CONTRACT_REPAIR_COMPLETE",
    }
)
HISTORICAL_ENTRY_STATE_CONTRACT_VERSION = (
    "driveclarify.observation_screening_entry_state_compatibility.v1"
)
POST_BATCH_ENTRY_STATE_CONTRACT_VERSION = (
    "driveclarify.observation_screening_entry_state_compatibility.v2"
)
POST_BATCH_STATE_KEY = "multi_topology_batch_observation_screen_b1"
POST_BATCH_TERMINAL_STATUS = "BATCH_OBSERVATION_ELIGIBILITY_SCREEN_COMPLETE"

UNIT_RUNS: Tuple[Mapping[str, Any], ...] = (
    {
        "unit_id": "TOWN03_JUNCTION_1221_UNIT01",
        "primary": "DC-OBS-T03-J1221-A-20260803T113100Z",
        "recovery": "DC-OBS-T03-J1221-B-20260803T114100Z",
    },
    {
        "unit_id": "TOWN04_JUNCTION_53_UNIT01",
        "primary": "DC-OBS-T04-J53-A-20260803T113200Z",
        "recovery": "DC-OBS-T04-J53-B-20260803T114200Z",
    },
    {
        "unit_id": "TOWN04_JUNCTION_278_UNIT01",
        "primary": "DC-OBS-T04-J278-A-20260803T113300Z",
        "recovery": "DC-OBS-T04-J278-B-20260803T114300Z",
    },
    {
        "unit_id": "TOWN04_JUNCTION_1452_UNIT01",
        "primary": "DC-OBS-T04-J1452-A-20260803T113400Z",
        "recovery": "DC-OBS-T04-J1452-B-20260803T114400Z",
    },
    {
        "unit_id": "TOWN05_JUNCTION_1574_UNIT01",
        "primary": "DC-OBS-T05-J1574-A-20260803T113500Z",
        "recovery": "DC-OBS-T05-J1574-B-20260803T114500Z",
    },
    {
        "unit_id": "TOWN05_JUNCTION_1722_UNIT01",
        "primary": "DC-OBS-T05-J1722-A-20260803T113600Z",
        "recovery": "DC-OBS-T05-J1722-B-20260803T114600Z",
    },
)

ALL_RUN_IDS = tuple(
    run_id for item in UNIT_RUNS for run_id in (item["primary"], item["recovery"])
)
P3B_OUTPUT = (
    ROOT
    / "reports/static_maneuver_branch_primary_mvp_v1/M3E_STATIC_BRANCH_PILOT/run_outputs"
    / p3.RUN_IDS[1]
)
BASE_SPEC = P3B_OUTPUT / "RUN_SPEC_AUTHORIZED.json"
BASE_SITE_CUSTOMIZE = P3B_OUTPUT / "sitecustomize.py"


class BatchError(RuntimeError):
    pass


def utc_now() -> str:
    return p3.utc_now()


def embedded_hash(value: Mapping[str, Any]) -> str:
    unsigned = dict(value)
    unsigned.pop("sha256", None)
    return digest_value(unsigned)


def load_embedded(path: Path, label: str) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("sha256") != embedded_hash(value):
        raise BatchError("EMBEDDED_SHA256_MISMATCH:{}:{}".format(label, path))
    return value


def unit_entry_for_run(run_id: str) -> Tuple[int, Mapping[str, Any], str]:
    for index, item in enumerate(UNIT_RUNS):
        if run_id == item["primary"]:
            return index, item, "primary"
        if run_id == item["recovery"]:
            return index, item, "recovery"
    raise BatchError("RUN_ID_NOT_AUTHORIZED:" + run_id)


def unit_paths(unit_id: str) -> Mapping[str, Path]:
    root = AUTHORITY / "units" / unit_id
    return {
        "root": root,
        "manifest": root / "UNIT_MANIFEST.json",
        "fixture": root / "SCENARIO_FREE_ROUTE.xml",
        "contract": root / "OBSERVATION_ELIGIBILITY_CONTRACT.json",
        "topology": root / "BRANCH_TOPOLOGY_GROUND_TRUTH.json",
        "task_binding": root / "TASK_BINDING.json",
        "mapper_compatibility": root / "MAPPER_COMPATIBILITY.json",
        "source_provenance": root / "SOURCE_PROVENANCE.json",
    }


def run_output(run_id: str) -> Path:
    if run_id not in ALL_RUN_IDS:
        raise BatchError("RUN_ID_NOT_AUTHORIZED:" + run_id)
    return RUNS_ROOT / run_id


def run_command(
    args: Sequence[str], cwd: Path = ROOT, env: Optional[Mapping[str, str]] = None
) -> Mapping[str, Any]:
    return p3.run_command(args, cwd, env)


def append_log(path: Path, text: str) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(text.rstrip() + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def append_batch_log(text: str) -> None:
    append_log(BATCH_ROOT / "BATCH_COMMAND_LOG.md", text)


def _inventory_rows(root: Path, *, exclude: Iterable[Path] = ()) -> List[Mapping[str, Any]]:
    excluded = {path.resolve() for path in exclude}
    rows = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        resolved = path.resolve()
        if any(excluded_path == resolved or excluded_path in resolved.parents for excluded_path in excluded):
            continue
        rows.append(
            {
                "path": path.relative_to(root).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256_path(path),
            }
        )
    return rows


def authority_snapshot() -> Mapping[str, Any]:
    rows = _inventory_rows(AUTHORITY, exclude=(AUTHORITY / "observation_screening_runs",))
    return {
        "root": str(AUTHORITY),
        "file_count": len(rows),
        "total_bytes": sum(int(row["bytes"]) for row in rows),
        "aggregate_sha256": digest_value(rows),
        "files": rows,
    }


def outside_batch_untracked_snapshot() -> Mapping[str, Any]:
    raw = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        cwd=str(ROOT),
        check=True,
        stdout=subprocess.PIPE,
    ).stdout
    prefix = BATCH_ROOT.relative_to(ROOT).as_posix() + "/"
    paths = sorted(
        item.decode("utf-8", errors="surrogateescape")
        for item in raw.split(b"\0")
        if item and not item.decode("utf-8", errors="surrogateescape").startswith(prefix)
    )
    rows = []
    for relative in paths:
        path = ROOT / relative
        if path.is_file():
            rows.append(
                {
                    "path": relative,
                    "bytes": path.stat().st_size,
                    "sha256": sha256_path(path),
                }
            )
    return {
        "file_count": len(rows),
        "aggregate_sha256": digest_value(rows),
        "rows": rows,
    }


def verify_inventory_file(path: Path, expected_file_sha256: str) -> Mapping[str, Any]:
    if sha256_path(path) != expected_file_sha256:
        raise BatchError("PROTECTED_INVENTORY_FILE_SHA256_MISMATCH:" + str(path))
    value = json.loads(path.read_text(encoding="utf-8"))
    root = path.parent
    for row in value.get("artifacts", []):
        target = root / row["path"]
        if (
            not target.is_file()
            or target.stat().st_size != row["bytes"]
            or sha256_path(target) != row["sha256"]
        ):
            raise BatchError("PROTECTED_INVENTORY_ROW_MISMATCH:" + str(target))
    return {
        "path": str(path),
        "sha256": expected_file_sha256,
        "listed_rows_verified": len(value.get("artifacts", [])),
    }


def protected_history() -> Mapping[str, Any]:
    legacy = p3.protected_history()
    p3_root = (
        ROOT
        / "reports/static_maneuver_branch_primary_mvp_v1/M3E_STATIC_BRANCH_PILOT/run_outputs"
    )
    p3a = verify_inventory_file(
        p3_root / p3.RUN_IDS[0] / "ARTIFACT_INVENTORY.json", P3A_INVENTORY_SHA256
    )
    p3b = verify_inventory_file(
        p3_root / p3.RUN_IDS[1] / "ARTIFACT_INVENTORY.json", P3B_INVENTORY_SHA256
    )
    return {
        "p1_p2_r2_and_prior_p3": legacy,
        "p3a": p3a,
        "p3b": p3b,
        "route_27515_rerun_count_this_batch": 0,
        "p3c_consumed_this_batch": 0,
    }


def entry_state_compatibility(state: Mapping[str, Any]) -> Mapping[str, Any]:
    """Version the historical entry gate without coupling it to today's state.

    The original V1 states remain exact.  Once this particular batch completed,
    later project milestones are compatible through the immutable batch identity
    and terminal record rather than by continually appending unrelated top-level
    project statuses to the historical V1 allow-list.
    """

    current_status = state.get("status")
    if current_status in HISTORICAL_AUTHORITY_ENTRY_STATES_V1:
        return {
            "contract_version": HISTORICAL_ENTRY_STATE_CONTRACT_VERSION,
            "mode": "HISTORICAL_ENTRY_STATE_V1",
            "current_status": current_status,
        }

    completed = state.get(POST_BATCH_STATE_KEY)
    if isinstance(completed, Mapping) and (
        completed.get("batch_id") == BATCH_ID
        and completed.get("status") == POST_BATCH_TERMINAL_STATUS
        and completed.get("selected_unit_count") == len(UNIT_RUNS)
        and completed.get("terminal_unit_count") == len(UNIT_RUNS)
        and completed.get("frozen_authority_modified") is False
        and completed.get("protected_history_modified") is False
    ):
        return {
            "contract_version": POST_BATCH_ENTRY_STATE_CONTRACT_VERSION,
            "mode": "POST_BATCH_TERMINAL_LINEAGE_V2",
            "current_status": current_status,
            "lineage_state_key": POST_BATCH_STATE_KEY,
            "lineage_terminal_status": POST_BATCH_TERMINAL_STATUS,
        }

    raise BatchError("ENTRY_STATE_MISMATCH")


def validate_authority() -> Mapping[str, Any]:
    state = json.loads((ROOT / "STATE.json").read_text(encoding="utf-8"))
    selected = load_embedded(SELECTED_MANIFEST, "SELECTED_MANIFEST")
    spec = load_embedded(BATCH_SPEC, "BATCH_SPEC")
    threshold = load_embedded(THRESHOLDS, "THRESHOLDS")
    state_compatibility = entry_state_compatibility(state)
    if selected.get("selected_count") != 6 or spec.get("selected_unit_count") != 6:
        raise BatchError("SELECTED_UNIT_COUNT_NOT_SIX")
    expected_order = [item["unit_id"] for item in UNIT_RUNS]
    if spec.get("unit_order") != expected_order:
        raise BatchError("UNIT_ORDER_MISMATCH")
    if (
        spec.get("run_authorized") is not False
        or spec.get("batch_id") is not None
        or spec.get("run_ids") != []
        or spec.get("receipts") != []
        or spec.get("run_outputs") != []
    ):
        raise BatchError("PREPARATION_BATCH_ALREADY_EXECUTED_OR_MUTATED")
    if selected.get("excluded_frozen_reference") != {
        "junction_id": "238",
        "route_id": "27515",
        "status": "UNKNOWN_EXCLUSION_EVIDENCE_UNAVAILABLE_FIRST_OBSERVATION_TOO_EARLY",
        "town": "Town03",
    }:
        raise BatchError("ROUTE_27515_EXCLUSION_MISMATCH")
    if (
        sha256_path(THRESHOLDS) != THRESHOLD_FILE_SHA256
        or threshold.get("sha256") != THRESHOLD_EMBEDDED_SHA256
    ):
        raise BatchError("MAPPER_THRESHOLD_AUTHORITY_MISMATCH")
    selected_by_id = {item["unit_id"]: item for item in selected["selected_units"]}
    unit_checks = []
    for item in UNIT_RUNS:
        paths = unit_paths(item["unit_id"])
        unit = load_embedded(paths["manifest"], "UNIT_MANIFEST")
        contract = load_embedded(paths["contract"], "ELIGIBILITY_CONTRACT")
        topology = load_embedded(paths["topology"], "TOPOLOGY")
        row = selected_by_id[item["unit_id"]]
        if (
            unit["unit_id"] != item["unit_id"]
            or unit["route_id"] == "27515"
            or unit["route_start"]["signed_station_from_decision_point_m"] != -5.5
            or unit["eligibility_interval"] != contract["eligibility_interval"]
            or unit["topology_sha256"] != topology["sha256"]
            or row["fixture_sha256"] != sha256_path(paths["fixture"])
            or row["topology_sha256"] != topology["sha256"]
            or unit["threshold_sha256"] != THRESHOLD_EMBEDDED_SHA256
            or unit["fixture_scenario_free"] is not True
            or unit["fixture_dynamic_actor_or_trigger_count"] != 0
        ):
            raise BatchError("UNIT_AUTHORITY_MISMATCH:" + item["unit_id"])
        for artifact in unit["artifacts"]:
            target = ROOT / artifact["path"]
            if (
                not target.is_file()
                or target.stat().st_size != artifact["bytes"]
                or sha256_path(target) != artifact["sha256"]
            ):
                raise BatchError("UNIT_ARTIFACT_MISMATCH:" + str(target))
        unit_checks.append(
            {
                "unit_id": item["unit_id"],
                "town": unit["town"],
                "route_id": unit["route_id"],
                "junction_id": unit["junction_id"],
                "eligibility_interval": contract["eligibility_interval"],
                "route_start_signed_station_m": -5.5,
                "fixture_sha256": sha256_path(paths["fixture"]),
                "contract_sha256": contract["sha256"],
                "topology_sha256": topology["sha256"],
            }
        )
    return {
        "entry_status": state["status"],
        "entry_state_contract_version": state_compatibility["contract_version"],
        "entry_state_compatibility_mode": state_compatibility["mode"],
        "selected_count": 6,
        "preparation_batch_spec_unexecuted": True,
        "route_27515_frozen_exclusion": True,
        "threshold_file_sha256": THRESHOLD_FILE_SHA256,
        "threshold_embedded_sha256": THRESHOLD_EMBEDDED_SHA256,
        "units": unit_checks,
    }


def prepare_batch() -> None:
    if BATCH_ROOT.exists():
        raise BatchError("BATCH_ROOT_ALREADY_EXISTS")
    authority = validate_authority()
    history = protected_history()
    drive_git = p3.git_state(ROOT)
    sim_git = p3.git_state(SIMLINGO)
    if (
        drive_git["branch"] != "master"
        or drive_git["head"] != "eaa332b1bb994279b59ea5af786fdb5de96adc1b"
        or drive_git["tracked_diff_bytes"] != 0
        or drive_git["staged_diff_bytes"] != 0
    ):
        raise BatchError("DRIVECLARIFY_WORKTREE_PREFLIGHT_MISMATCH")
    if (
        sim_git["branch"] != "main"
        or sim_git["head"] != "743b243afd6cf5ff51b9fa1f8cac86f22d569684"
        or sim_git["tracked_diff_bytes"] != 7722
        or sim_git["tracked_diff_sha256"] != SIMLINGO_TRACKED_DIFF_SHA256
        or sim_git["staged_diff_bytes"] != 0
    ):
        raise BatchError("SIMLINGO_WORKTREE_PREFLIGHT_MISMATCH")
    RUNS_ROOT.mkdir(parents=True)
    atomic_create_json(
        BATCH_ROOT / "BATCH_AUTHORIZATION.json",
        {
            "schema_version": "driveclarify.batch_observation_authorization.v1",
            "batch_id": BATCH_ID,
            "authorization_source": "USER_EXPLICIT_MULTI_TOPOLOGY_BATCH_OBSERVATION_ELIGIBILITY_SCREEN",
            "authorized_at_utc": utc_now(),
            "ordered_units": [dict(item) for item in UNIT_RUNS],
            "constraints": {
                "primary_per_unit": 1,
                "recovery_per_unit_engineering_failure_only": 1,
                "model_ready_observation_max_per_run": 1,
                "required_observation_index": 0,
                "candidate_forward_count": 0,
                "second_observation_count": 0,
                "mapper_invocation_count": 0,
                "training_count": 0,
                "act_ask_wait_count": 0,
                "new_run_ids_outside_authorized_list": 0,
            },
            "authority_validation": authority,
            "protected_history": history,
            "git_at_batch_entry": {"driveclarify": drive_git, "simlingo": sim_git},
            "automatic_repair_scope": "DRIVECLARIFY_OBSERVATION_SCREENING_ENGINEERING_PATH_CPU_ONLY",
        },
    )
    atomic_create_bytes(
        BATCH_ROOT / "BATCH_COMMAND_LOG.md",
        (
            "# Batch observation screening command log\n\n"
            "- Batch ID: `{}`\n"
            "- Authorization/authority/history/Git entry checks: `PASS`.\n"
            "- Real Run IDs consumed: none.\n"
        ).format(BATCH_ID).encode("utf-8"),
    )


def preflight_batch() -> None:
    if not (BATCH_ROOT / "BATCH_AUTHORIZATION.json").is_file():
        raise BatchError("BATCH_NOT_PREPARED")
    if any((RUNS_ROOT / run_id).exists() for run_id in ALL_RUN_IDS):
        raise BatchError("BATCH_PREFLIGHT_AFTER_RUN_STAGING_FORBIDDEN")
    env = dict(os.environ)
    env.update(
        {
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    commands = [
        [str(p3.PYTHON38), "-B", "-c", "compile(open(%r, 'rb').read(), %r, 'exec')" % (str(AGENT_SOURCE), str(AGENT_SOURCE))],
        [str(p3.PYTHON38), "-B", "-c", "compile(open(%r, 'rb').read(), %r, 'exec')" % (str(PACKAGE_SOURCE), str(PACKAGE_SOURCE))],
        [str(p3.PYTHON38), "-B", "-c", "compile(open(%r, 'rb').read(), %r, 'exec')" % (str(ORCHESTRATOR_SOURCE), str(ORCHESTRATOR_SOURCE))],
        [str(p3.PYTHON38), "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/evaluator_adapter_production_path", "tests/m3d_route_validation"],
        [sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/m3e_supervisor_binding"],
        [sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/observation_screening"],
        [sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/evaluator_adapter_production_path", "tests/m3e_supervisor_binding", "tests/m3d_route_validation", "tests/multi_topology_static_units", "tests/observation_screening"],
        [str(p3.PYTHON38), "-B", "tools/evaluator_production_path_dry_run.py"],
    ]
    results = [run_command(command, ROOT, env) for command in commands]
    authority = validate_authority()
    history = protected_history()
    sim_git = p3.git_state(SIMLINGO)
    passed = (
        all(result["exit_code"] == 0 for result in results)
        and sim_git["tracked_diff_bytes"] == 7722
        and sim_git["tracked_diff_sha256"] == SIMLINGO_TRACKED_DIFF_SHA256
        and sim_git["staged_diff_bytes"] == 0
    )
    payload = {
        "schema_version": "driveclarify.batch_observation_cpu_preflight.v1",
        "batch_id": BATCH_ID,
        "status": "PASS" if passed else "FAIL",
        "checked_at_utc": utc_now(),
        "commands": results,
        "authority": authority,
        "protected_history": history,
        "authority_snapshot": authority_snapshot(),
        "outside_batch_untracked_snapshot": outside_batch_untracked_snapshot(),
        "simlingo_git": sim_git,
        "production_adapter_dry_run": "PASS" if results[-1]["exit_code"] == 0 else "FAIL",
        "serializer_smoke_test": "PASS" if results[5]["exit_code"] == 0 else "FAIL",
        "real_system_launches": 0,
        "torch_imported_by_orchestrator": False,
        "cuda_initializations": 0,
    }
    atomic_create_json(BATCH_ROOT / "BATCH_CPU_PREFLIGHT.json", payload)
    append_batch_log(
        "- Batch CPU preflight: `{}`; command exit codes={}.".format(
            payload["status"], [result["exit_code"] for result in results]
        )
    )
    if not passed:
        raise BatchError("BATCH_CPU_PREFLIGHT_FAILED")


def record_repair(repair_reason: str) -> None:
    """Rebaseline only the explicitly allowed DriveClarify repair surface."""

    preflight_path = BATCH_ROOT / "BATCH_CPU_PREFLIGHT.json"
    preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
    if preflight.get("status") != "PASS":
        raise BatchError("BATCH_CPU_PREFLIGHT_NOT_PASS")
    baseline_rows = {
        row["path"]: row for row in preflight["outside_batch_untracked_snapshot"]["rows"]
    }
    current_before = outside_batch_untracked_snapshot()
    current_rows = {row["path"]: row for row in current_before["rows"]}
    changed = sorted(
        path
        for path in set(baseline_rows) | set(current_rows)
        if baseline_rows.get(path) != current_rows.get(path)
    )
    allowed_exact = {
        "driveclarify_static_branch/observation_package.py",
        "driveclarify_static_branch/observation_screening_agent.py",
        "driveclarify_static_branch/observation_screening_batch.py",
        "tests/multi_topology_static_units/test_multi_topology_static_units.py",
    }
    if any(
        path not in allowed_exact and not path.startswith("tests/observation_screening/")
        for path in changed
    ):
        raise BatchError("BLOCKED_UNATTRIBUTED_WORKTREE_CHANGE:REPAIR_SURFACE:" + ",".join(changed))
    env = dict(os.environ)
    env.update({"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONDONTWRITEBYTECODE": "1"})
    commands = [
        [str(p3.PYTHON38), "-B", "-c", "compile(open(%r, 'rb').read(), %r, 'exec')" % (str(AGENT_SOURCE), str(AGENT_SOURCE))],
        [str(p3.PYTHON38), "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/evaluator_adapter_production_path", "tests/m3d_route_validation"],
        [sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/m3e_supervisor_binding"],
        [sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/observation_screening"],
        [sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/evaluator_adapter_production_path", "tests/m3e_supervisor_binding", "tests/m3d_route_validation", "tests/multi_topology_static_units", "tests/observation_screening"],
        [str(p3.PYTHON38), "-B", "tools/evaluator_production_path_dry_run.py"],
    ]
    results = [run_command(command, ROOT, env) for command in commands]
    authority = validate_authority()
    history = protected_history()
    drive_git = p3.git_state(ROOT)
    sim_git = p3.git_state(SIMLINGO)
    passed = (
        all(result["exit_code"] == 0 for result in results)
        and authority_snapshot()["aggregate_sha256"]
        == preflight["authority_snapshot"]["aggregate_sha256"]
        and drive_git["tracked_diff_bytes"] == 0
        and drive_git["staged_diff_bytes"] == 0
        and sim_git["tracked_diff_bytes"] == 7722
        and sim_git["tracked_diff_sha256"] == SIMLINGO_TRACKED_DIFF_SHA256
        and sim_git["staged_diff_bytes"] == 0
    )
    repair = {
        "iteration": len(preflight.get("repair_iterations", [])) + 1,
        "reason": repair_reason,
        "changed_paths": changed,
        "checked_at_utc": utc_now(),
        "status": "PASS" if passed else "FAIL",
        "commands": results,
        "production_adapter_dry_run": "PASS" if results[-1]["exit_code"] == 0 else "FAIL",
        "authority": authority,
        "protected_history": history,
        "driveclarify_git": drive_git,
        "simlingo_git": sim_git,
        "real_system_launches": 0,
    }
    preflight.setdefault("repair_iterations", []).append(repair)
    if passed:
        preflight["outside_batch_untracked_snapshot"] = outside_batch_untracked_snapshot()
        preflight["latest_repair_status"] = "PASS"
    else:
        preflight["latest_repair_status"] = "FAIL"
    atomic_replace_json(preflight_path, preflight)
    append_batch_log(
        "- CPU repair iteration {} `{}`: `{}`; changed paths={}.".format(
            repair["iteration"], repair_reason, repair["status"], changed
        )
    )
    if not passed:
        raise BatchError("OBSERVATION_SCREENING_REPAIR_REGRESSION_FAILED")


def validate_no_unattributed_change() -> None:
    preflight = json.loads((BATCH_ROOT / "BATCH_CPU_PREFLIGHT.json").read_text(encoding="utf-8"))
    if preflight.get("status") != "PASS":
        raise BatchError("BATCH_CPU_PREFLIGHT_NOT_PASS")
    current_authority = authority_snapshot()
    baseline_authority = preflight["authority_snapshot"]
    if current_authority["aggregate_sha256"] != baseline_authority["aggregate_sha256"]:
        raise BatchError("BLOCKED_UNATTRIBUTED_WORKTREE_CHANGE:AUTHORITY")
    current_outside = outside_batch_untracked_snapshot()
    baseline_outside = preflight["outside_batch_untracked_snapshot"]
    if current_outside["aggregate_sha256"] != baseline_outside["aggregate_sha256"]:
        raise BatchError("BLOCKED_UNATTRIBUTED_WORKTREE_CHANGE:OUTSIDE_BATCH")
    drive_git = p3.git_state(ROOT)
    sim_git = p3.git_state(SIMLINGO)
    if drive_git["tracked_diff_bytes"] != 0 or drive_git["staged_diff_bytes"] != 0:
        raise BatchError("BLOCKED_UNATTRIBUTED_WORKTREE_CHANGE:DRIVECLARIFY_TRACKED")
    if (
        sim_git["tracked_diff_bytes"] != 7722
        or sim_git["tracked_diff_sha256"] != SIMLINGO_TRACKED_DIFF_SHA256
        or sim_git["staged_diff_bytes"] != 0
    ):
        raise BatchError("BLOCKED_UNATTRIBUTED_WORKTREE_CHANGE:SIMLINGO")


def validate_stage_sequence(run_id: str) -> None:
    index, item, role = unit_entry_for_run(run_id)
    for prior in UNIT_RUNS[:index]:
        used = [
            candidate
            for candidate in (prior["recovery"], prior["primary"])
            if read_run_lifecycle(run_output(candidate))["terminal"] is True
        ]
        if not used:
            raise BatchError("PRIOR_UNIT_NOT_TERMINAL:" + prior["unit_id"])
    if role == "primary":
        if (run_output(item["recovery"])).exists():
            raise BatchError("RECOVERY_EXISTS_BEFORE_PRIMARY")
    else:
        primary_lifecycle = read_run_lifecycle(run_output(item["primary"]))
        if primary_lifecycle["terminal"] is not True:
            raise BatchError("RECOVERY_WITHOUT_TERMINAL_PRIMARY")
        primary = primary_lifecycle["payload"]
        if primary.get("outcome") != "RUNTIME_FAILURE":
            raise BatchError("RECOVERY_NOT_AUTHORIZED_BY_ENGINEERING_FAILURE")


def stage_run(run_id: str) -> None:
    validate_no_unattributed_change()
    validate_stage_sequence(run_id)
    output = run_output(run_id)
    if output.exists():
        raise BatchError("RUN_OUTPUT_ALREADY_EXISTS:" + run_id)
    _, item, role = unit_entry_for_run(run_id)
    output.mkdir(parents=False)
    (output / "logs").mkdir()
    (output / "receipt_claims").mkdir()
    atomic_create_bytes(output / "OBSERVATION_SCREENING_AGENT.py", AGENT_SOURCE.read_bytes())
    atomic_create_bytes(output / "M3E_EVALUATOR_ENTRY.py", p3.evaluator_entry_source().encode("utf-8"))
    atomic_create_bytes(output / "sitecustomize.py", BASE_SITE_CUSTOMIZE.read_bytes())
    atomic_create_json(
        output / "STAGING_CONTEXT.json",
        {
            "schema_version": "driveclarify.observation_screening_staging.v1",
            "batch_id": BATCH_ID,
            "run_id": run_id,
            "unit_id": item["unit_id"],
            "run_role": role,
            "staged_at_utc": utc_now(),
            "agent_source_sha256": sha256_path(AGENT_SOURCE),
            "staged_agent_sha256": sha256_path(output / "OBSERVATION_SCREENING_AGENT.py"),
            "git_before_staging": {"driveclarify": p3.git_state(ROOT), "simlingo": p3.git_state(SIMLINGO)},
        },
    )
    atomic_create_bytes(
        output / "COMMAND_LOG.md",
        (
            "# Observation screening Run command log\n\n"
            "- Batch ID: `{}`\n- Run ID: `{}`\n- Unit: `{}`\n"
            "- Role: `{}`\n- Receipt: NOT_CREATED; real launch: NOT_EXECUTED.\n"
        ).format(BATCH_ID, run_id, item["unit_id"], role).encode("utf-8"),
    )
    append_batch_log("- Staged `{}` for `{}` as `{}`; no receipt or real launch.".format(run_id, item["unit_id"], role))


def cpu_check_run(run_id: str) -> None:
    output = run_output(run_id)
    if (output / "AUTHORIZATION_RECEIPT.json").exists():
        raise BatchError("CPU_CHECK_AFTER_RECEIPT_FORBIDDEN")
    env = dict(os.environ)
    env.update({"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONDONTWRITEBYTECODE": "1"})
    paths = unit_paths(unit_entry_for_run(run_id)[1]["unit_id"])
    commands = [
        [str(p3.PYTHON38), "-B", "-c", "compile(open(%r, 'rb').read(), %r, 'exec')" % (str(output / "OBSERVATION_SCREENING_AGENT.py"), str(output / "OBSERVATION_SCREENING_AGENT.py"))],
        [str(p3.PYTHON38), "-B", "-c", "compile(open(%r, 'rb').read(), %r, 'exec')" % (str(output / "M3E_EVALUATOR_ENTRY.py"), str(output / "M3E_EVALUATOR_ENTRY.py"))],
        [str(p3.PYTHON38), "-B", "tools/evaluator_production_path_dry_run.py"],
        [sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/observation_screening"],
    ]
    results = [run_command(command, ROOT, env) for command in commands]
    authority = validate_authority()
    fixture_hash = sha256_path(paths["fixture"])
    unit = load_embedded(paths["manifest"], "UNIT_MANIFEST")
    passed = all(result["exit_code"] == 0 for result in results) and fixture_hash == unit["artifacts"][0]["sha256"]
    payload = {
        "schema_version": "driveclarify.observation_screening_run_cpu_check.v1",
        "batch_id": BATCH_ID,
        "run_id": run_id,
        "status": "PASS" if passed else "FAIL",
        "checked_at_utc": utc_now(),
        "commands": results,
        "production_adapter_dry_check": "PASS" if results[2]["exit_code"] == 0 else "FAIL",
        "route_fixture_sha256": fixture_hash,
        "authority": authority,
        "real_system_launches": 0,
    }
    atomic_create_json(output / "CPU_CHECK_RESULTS.json", payload)
    append_log(output / "COMMAND_LOG.md", "- Per-run production adapter/route/serializer CPU check: `{}`.".format(payload["status"]))
    if not passed:
        raise BatchError("RUN_CPU_CHECK_FAILED:" + run_id)


def prelaunch_selftest(run_id: str) -> None:
    output = run_output(run_id)
    if (output / "AUTHORIZATION_RECEIPT.json").exists():
        raise BatchError("SELFTEST_AFTER_RECEIPT_FORBIDDEN")
    cpu = json.loads((output / "CPU_CHECK_RESULTS.json").read_text(encoding="utf-8"))
    if cpu.get("status") != "PASS":
        raise BatchError("RUN_CPU_CHECK_NOT_PASS")
    evidence = output / "PRELAUNCH_SUPERVISOR_SELFTEST.json"
    env = dict(os.environ)
    env.update({"PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1"})
    result = run_command(
        [
            str(p3.PYTHON38),
            "-B",
            "-m",
            "driveclarify_static_branch.m3e_bound_supervisor",
            "--selftest-only",
            "--selftest-output",
            str(evidence),
        ],
        ROOT,
        env,
    )
    if result["exit_code"] != 0 or not evidence.is_file():
        atomic_create_json(output / "logs/prelaunch_selftest_failure.json", result)
        raise BatchError("PRELAUNCH_SELFTEST_COMMAND_FAILED")
    value = json.loads(evidence.read_text(encoding="utf-8"))
    if (
        value.get("status") != "PASS"
        or value.get("real_system_launches") != 0
        or value.get("binding", {}).get("binding_source") != "DIRECT_SPAWN_RETURN"
        or value.get("unrelated_pid_survived_bound_cleanup") is not True
    ):
        raise BatchError("PRELAUNCH_SELFTEST_INVALID")
    append_log(output / "COMMAND_LOG.md", "- Pre-receipt supervisor direct-binding CPU self-test: `PASS`.")


def _fixed_time(run_id: str) -> Tuple[str, str]:
    suffix = run_id.rsplit("-", 1)[-1]
    if len(suffix) != 16 or not suffix.endswith("Z"):
        raise BatchError("RUN_ID_TIMESTAMP_FORMAT_INVALID")
    compact = suffix[:-1]
    fixed_utc = "{}-{}-{}T{}:{}:{}Z".format(
        compact[0:4], compact[4:6], compact[6:8], compact[9:11], compact[11:13], compact[13:15]
    )
    setup = "{}_{}_{}_{}_{}_{}".format(
        compact[0:4], compact[4:6], compact[6:8], compact[9:11], compact[11:13], compact[13:15]
    )
    return setup, fixed_utc


def _source_pins(output: Path, paths: Mapping[str, Path]) -> List[Mapping[str, Any]]:
    rows = [
        (p3.CARLA_BINARY, "final_unreal_executable"),
        (p3.PYTHON38, "python_runtime"),
        (p3.CHECKPOINT, "simlingo_checkpoint"),
        (p3.CONFIG, "simlingo_config"),
        (paths["fixture"], "scenario_free_route_fixture"),
        (paths["manifest"], "unit_manifest"),
        (paths["contract"], "observation_eligibility_contract"),
        (paths["topology"], "branch_topology"),
        (paths["task_binding"], "task_binding"),
        (paths["mapper_compatibility"], "mapper_compatibility"),
        (paths["source_provenance"], "source_provenance"),
        (THRESHOLDS, "mapping_thresholds"),
        (p3.ROUTE_VALIDATION, "evaluator_compatibility_adapter"),
        (output / "OBSERVATION_SCREENING_AGENT.py", "observation_screening_runtime_agent"),
        (output / "M3E_EVALUATOR_ENTRY.py", "observation_screening_evaluator_entry"),
        (p3.PROCESS_BINDING, "process_binding_implementation"),
        (p3.BOUND_SUPERVISOR, "bound_supervisor_adapter"),
        (p3.BASE_ADAPTER, "base_no_launch_evaluator_adapter"),
        (p3.EVALUATOR, "production_evaluator_source"),
        (p3.AGENT_WRAPPER, "production_agent_wrapper"),
        (PACKAGE_SOURCE, "observation_package_serializer"),
        (ORCHESTRATOR_SOURCE, "observation_batch_orchestrator"),
    ]
    result = []
    for path, role in rows:
        row: Dict[str, Any] = {"path": str(path), "role": role, "sha256": sha256_path(path)}
        if role in {"final_unreal_executable", "python_runtime"}:
            row["bytes"] = path.stat().st_size
        result.append(row)
    return result


def _build_spec(run_id: str, output: Path, item: Mapping[str, Any]) -> Mapping[str, Any]:
    paths = unit_paths(item["unit_id"])
    base = json.loads(BASE_SPEC.read_text(encoding="utf-8"))
    old_output = str(P3B_OUTPUT)
    spec = p3.deep_replace(base, old_output, str(output))
    spec = p3.deep_replace(spec, p3.RUN_IDS[1], run_id)
    spec = p3.deep_replace(spec, str(p3.FIXTURE), str(paths["fixture"]))
    spec = p3.deep_replace(spec, str(p3.TOPOLOGY), str(paths["topology"]))
    evaluator_argv = json.loads(spec["exact_command"]["environment"]["DRIVECLARIFY_PHASE0A_EVALUATOR_ARGV_JSON"])
    for index, value in enumerate(evaluator_argv):
        if value.endswith("/M3E_REAL_SIMLINGO_STATIC_BRANCH_AGENT.py"):
            evaluator_argv[index] = str(output / "OBSERVATION_SCREENING_AGENT.py")
        elif value == str(paths["fixture"]):
            evaluator_argv[index] = str(paths["fixture"])
    environment = spec["exact_command"]["environment"]
    for key in list(environment):
        if key.startswith("DRIVECLARIFY_M3E_"):
            del environment[key]
    setup_time, fixed_utc = _fixed_time(run_id)
    environment.update(
        {
            "DRIVECLARIFY_PHASE0A_RUN_ID": run_id,
            "DRIVECLARIFY_PHASE0A_FIXED_SETUP_TIME": setup_time,
            "DRIVECLARIFY_PHASE0A_FIXED_TIME_UTC": fixed_utc,
            "DRIVECLARIFY_PHASE0A_INTENTIONAL_STOP_REASON": "DRIVECLARIFY_STOP_AFTER_FIRST_MODEL_READY_OBSERVATION_SCREEN",
            "DRIVECLARIFY_PHASE0A_CANDIDATE_OUTPUT": str(output / STAGE_A_RUNTIME_RESULT_FILE),
            "DRIVECLARIFY_PHASE0A_EVALUATOR_ARGV_JSON": json.dumps(evaluator_argv, separators=(",", ":")),
            "DRIVECLARIFY_PHASE0A_EVALUATOR_OUTPUT": str(output / "logs/evaluator_result.json"),
            "DRIVECLARIFY_PHASE0A_LEADERBOARD_RAW": str(output / "logs/leaderboard_raw.json"),
            "DRIVECLARIFY_PHASE0A_LEADERBOARD_DEBUG": str(output / "logs/leaderboard_debug.json"),
            "DRIVECLARIFY_PHASE0A_SUPERVISOR_STATUS": str(output / "logs/supervisor_status.json"),
            "DRIVECLARIFY_OBS_RUN_ID": run_id,
            "DRIVECLARIFY_OBS_UNIT_ID": item["unit_id"],
            "DRIVECLARIFY_OBS_UNIT_MANIFEST": str(paths["manifest"]),
            "DRIVECLARIFY_OBS_ELIGIBILITY_CONTRACT": str(paths["contract"]),
            "DRIVECLARIFY_OBS_TOPOLOGY": str(paths["topology"]),
            "DRIVECLARIFY_OBS_FIRST_EVIDENCE": str(output / "FIRST_OBSERVATION_EVIDENCE.json"),
            "DRIVECLARIFY_OBS_SCREENING_RESULT": str(output / "OBSERVATION_SCREENING_RESULT.json"),
            "DRIVECLARIFY_OBS_RUNTIME_COUNTS": str(output / "RUNTIME_COUNTS.json"),
            "DRIVECLARIFY_OBS_PACKAGE_ROOT": str(output / "observation_package"),
            "DRIVECLARIFY_OBS_PACKAGE_MANIFEST": str(output / "OBSERVATION_PACKAGE_MANIFEST.json"),
            "DRIVECLARIFY_OBS_ROUTE_FIXTURE_SHA256": sha256_path(paths["fixture"]),
            "DRIVECLARIFY_OBS_CHECKPOINT_PATH": str(p3.CHECKPOINT),
            "DRIVECLARIFY_OBS_CONFIG_PATH": str(p3.CONFIG),
            "ROUTES": str(paths["fixture"]),
            "SAVE_PATH": str(output / "logs/simlingo_save"),
        }
    )
    spec.update(
        {
            "schema_version": "driveclarify.observation_screening_run_spec.v1",
            "batch_id": BATCH_ID,
            "run_id": run_id,
            "unit_id": item["unit_id"],
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
            "source_pins": _source_pins(output, paths),
            "first_observation_eligibility_contract": str(paths["contract"]),
        }
    )
    spec.pop("capture_plan", None)
    return spec


def authorize_run(run_id: str) -> None:
    validate_no_unattributed_change()
    output = run_output(run_id)
    if (output / "AUTHORIZATION_RECEIPT.json").exists():
        raise BatchError("AUTHORIZATION_RECEIPT_ALREADY_EXISTS")
    selftest = json.loads((output / "PRELAUNCH_SUPERVISOR_SELFTEST.json").read_text(encoding="utf-8"))
    cpu = json.loads((output / "CPU_CHECK_RESULTS.json").read_text(encoding="utf-8"))
    if selftest.get("status") != "PASS" or cpu.get("status") != "PASS":
        raise BatchError("PREAUTHORIZATION_GATES_NOT_PASS")
    _, item, role = unit_entry_for_run(run_id)
    authority = validate_authority()
    history = protected_history()
    if (
        sha256_path(p3.CHECKPOINT) != p3.CHECKPOINT_SHA256
        or sha256_path(p3.CONFIG) != p3.CONFIG_SHA256
        or sha256_path(p3.EVALUATOR) != p3.EVALUATOR_SHA256
        or sha256_path(p3.BASE_ADAPTER) != p3.BASE_ADAPTER_SHA256
        or sha256_path(p3.CARLA_BINARY)
        != "1da989e4ff136e0658fdea421bc1da2b0e5cb018c6e767efc4f6129c21ab1a81"
    ):
        raise BatchError("FROZEN_PRODUCTION_SOURCE_PIN_MISMATCH")
    environment_state = p3.environment_preflight()
    if (
        not environment_state["physical_display_pass"]
        or not all(environment_state["ports_free"].values())
        or environment_state["gpu_compute_processes"]
        or environment_state["preexisting_real_processes"]
    ):
        raise BatchError("REAL_SYSTEM_PREFLIGHT_NOT_CLEAN")
    spec = dict(_build_spec(run_id, output, item))
    spec_path = output / "RUN_SPEC.json"
    receipt_path = output / "AUTHORIZATION_RECEIPT.json"
    spec["execution_command"] = [
        str(p3.PYTHON38),
        "-B",
        "-m",
        "driveclarify_static_branch.m3e_bound_supervisor",
        "--run-spec",
        str(spec_path),
        "--authorization-receipt",
        str(receipt_path),
        "--receipt-claim-directory",
        str(output / "receipt_claims"),
    ]
    spec["sha256"] = embedded_hash(spec)
    atomic_create_json(spec_path, spec)
    atomic_create_bytes(output / "RUN_SPEC_AUTHORIZED.json", spec_path.read_bytes())
    preflight = {
        "schema_version": "driveclarify.observation_screening_entry_preflight.v1",
        "batch_id": BATCH_ID,
        "run_id": run_id,
        "unit_id": item["unit_id"],
        "run_role": role,
        "status": "PASS_READY_FOR_SINGLE_REAL_LAUNCH",
        "verified_at_utc": utc_now(),
        "authority": authority,
        "protected_history": history,
        "environment": environment_state,
        "source_pins": {row["role"]: row["sha256"] for row in spec["source_pins"]},
        "production_adapter_dry_check": "PASS",
        "pre_receipt_supervisor_selftest": "PASS",
        "candidate_forward_limit": 0,
        "model_ready_observation_limit": 1,
        "required_observation_index": 0,
        "frozen_authority_modified": False,
    }
    atomic_create_json(output / "ENTRY_PREFLIGHT.json", preflight)
    authorized_raw = spec_path.read_bytes()
    receipt = {
        "schema_version": "driveclarify.observation_screening_authorization_receipt.v1",
        "receipt_id": "OBS-SCREEN-AUTH-{}-{}".format(run_id, uuid.uuid4().hex),
        "batch_id": BATCH_ID,
        "run_id": run_id,
        "unit_id": item["unit_id"],
        "run_role": role,
        "authorization_source": "USER_EXPLICIT_MULTI_TOPOLOGY_BATCH_OBSERVATION_ELIGIBILITY_SCREEN",
        "authorization_timestamp_utc": utc_now(),
        "authorized_run_spec": {
            "path": str(spec_path),
            "immutable_copy": str(output / "RUN_SPEC_AUTHORIZED.json"),
            "file_sha256": hashlib.sha256(authorized_raw).hexdigest(),
            "embedded_sha256": spec["sha256"],
        },
        "pre_receipt_selftest_sha256": sha256_path(output / "PRELAUNCH_SUPERVISOR_SELFTEST.json"),
        "source_pins": {row["role"]: row["sha256"] for row in spec["source_pins"]},
        "constraints": {
            "carla_launch_limit": 1,
            "evaluator_launch_limit": 1,
            "checkpoint_or_model_load_limit": 1,
            "observation_limit": 1,
            "required_observation_index": 0,
            "candidate_forward_limit": 0,
            "second_observation": False,
            "retry_same_run_id": False,
            "mapper": False,
            "training": False,
            "act_ask_wait": False,
            "headless_or_offscreen": False,
        },
        "protected_history": history,
        "receipt_reuse_allowed": False,
    }
    atomic_create_json(receipt_path, receipt)
    append_log(
        output / "COMMAND_LOG.md",
        "- Authority/hash/worktree/display/ports/GPU/history preflight: `PASS`.\n"
        "- Unique receipt created after supervisor self-test; real launch remains NOT_EXECUTED.",
    )


def launch_run(run_id: str) -> None:
    output = run_output(run_id)
    if (output / "REAL_LAUNCH_RECORD.json").exists() or list((output / "receipt_claims").glob("*.receipt-claim.json")):
        raise BatchError("RUN_ID_ALREADY_LAUNCHED_OR_CLAIMED")
    spec = json.loads((output / "RUN_SPEC.json").read_text(encoding="utf-8"))
    receipt = json.loads((output / "AUTHORIZATION_RECEIPT.json").read_text(encoding="utf-8"))
    if spec.get("run_id") != run_id or receipt.get("run_id") != run_id or spec.get("run_authorized") is not True:
        raise BatchError("LAUNCH_AUTHORITY_MISMATCH")
    validate_no_unattributed_change()
    current = p3.environment_preflight()
    if (
        not current["physical_display_pass"]
        or not all(current["ports_free"].values())
        or current["gpu_compute_processes"]
        or current["preexisting_real_processes"]
    ):
        raise BatchError("IMMEDIATE_PRELAUNCH_ENVIRONMENT_NOT_CLEAN")
    log_path = output / "logs/supervisor_execution.log"
    descriptor = os.open(
        str(log_path),
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    launch_env = dict(os.environ)
    launch_env.update(
        {
            "PYTHONPATH": str(ROOT),
            "PYTHONDONTWRITEBYTECODE": "1",
            "DISPLAY": ":1",
            "XAUTHORITY": "/run/user/1000/gdm/Xauthority",
        }
    )
    try:
        process, master_fd = p3.spawn_with_pty(spec["execution_command"], SIMLINGO, launch_env)
    except BaseException:
        os.close(descriptor)
        raise
    record: Dict[str, Any] = {
        "schema_version": "driveclarify.observation_screening_real_launch_record.v1",
        "batch_id": BATCH_ID,
        "run_id": run_id,
        "launched_at_utc": utc_now(),
        "supervisor_pid": process.pid,
        "execution_command": spec["execution_command"],
        "launch_count": 1,
        "retry_allowed": False,
        "stdout_stderr_transport": "LOCAL_PSEUDOTERMINAL_DRAINED_TO_RUN_LOG",
        "headless_or_graphics_forwarding": False,
    }
    atomic_create_json(output / "REAL_LAUNCH_RECORD.json", record)
    append_log(output / "COMMAND_LOG.md", "- REAL LAUNCH consumed exactly once; supervisor PID={}.".format(process.pid))
    try:
        exit_code = p3.drain_pty_to_log(process, master_fd, descriptor, 360.0)
    except subprocess.TimeoutExpired:
        append_log(output / "COMMAND_LOG.md", "- Outer 360 s wait timeout; no relaunch. Supervisor remains cleanup owner.")
        raise BatchError("REAL_SUPERVISOR_OUTER_WAIT_TIMEOUT")
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
    record["exit_code"] = exit_code
    record["terminal_observed_at_utc"] = utc_now()
    atomic_replace_json(output / "REAL_LAUNCH_RECORD.json", record)
    append_log(output / "COMMAND_LOG.md", "- REAL LAUNCH returned exit code `{}`; no same-ID retry/resume.".format(exit_code))


def _create_or_replace(path: Path, value: Any) -> None:
    if path.exists():
        atomic_replace_json(path, value)
    else:
        atomic_create_json(path, value)


def _binding_artifact(
    run_id: str, role: str, child: Optional[Mapping[str, Any]], status: Mapping[str, Any]
) -> Mapping[str, Any]:
    return p3.binding_artifact(run_id, role, child, status)


def _package_verification(output: Path, manifest: Mapping[str, Any]) -> Mapping[str, Any]:
    status = manifest.get("status")
    if status != "COMPLETE_FROZEN_MODEL_READY_OBSERVATION":
        return {
            "status": status,
            "verified": status in {
                "NOT_CREATED_EVIDENCE_UNAVAILABLE",
                "NOT_CREATED_RUNTIME_FAILURE",
            },
            "listed_file_count": len(manifest.get("files", [])),
        }
    package_root = Path(str(manifest.get("package_directory")))
    if package_root != output / "observation_package":
        return {"status": status, "verified": False, "reason": "PACKAGE_ROOT_MISMATCH"}
    failures = []
    for row in manifest.get("files", []):
        path = package_root / row["relative_path"]
        if (
            not path.is_file()
            or path.stat().st_size != row["bytes"]
            or sha256_path(path) != row["sha256"]
        ):
            failures.append(row["relative_path"])
    rows = sorted(manifest.get("files", []), key=lambda item: item["relative_path"])
    if digest_value(rows) != manifest.get("package_content_sha256"):
        failures.append("PACKAGE_CONTENT_SHA256")
    return {
        "status": status,
        "verified": not failures and len(rows) == manifest.get("file_count"),
        "listed_file_count": len(rows),
        "failures": failures,
    }


def _first_error(
    output: Path, runtime_result: Mapping[str, Any], supervisor_status: Mapping[str, Any]
) -> Mapping[str, Any]:
    if isinstance(runtime_result.get("first_error"), Mapping):
        return dict(runtime_result["first_error"])
    return p3.first_error_evidence(output, runtime_result, supervisor_status)


def finalize_run(run_id: str) -> str:
    output = run_output(run_id)
    existing_lifecycle = read_run_lifecycle(output)
    if existing_lifecycle["terminal"] is True:
        raise DuplicateTerminalResultError(
            "DUPLICATE_TERMINAL_RESULT", output / "RUN_RESULT.json"
        )
    launch_record = json.loads((output / "REAL_LAUNCH_RECORD.json").read_text(encoding="utf-8"))
    status_path = output / "logs/supervisor_status.json"
    if not status_path.is_file():
        raise BatchError("SUPERVISOR_STATUS_MISSING_AFTER_REAL_LAUNCH")
    supervisor_status = json.loads(status_path.read_text(encoding="utf-8"))
    if supervisor_status.get("run_id") != run_id:
        raise BatchError("SUPERVISOR_STATUS_RUN_ID_MISMATCH")
    _, item, role = unit_entry_for_run(run_id)
    paths = unit_paths(item["unit_id"])
    unit = load_embedded(paths["manifest"], "UNIT_MANIFEST")
    contract = load_embedded(paths["contract"], "ELIGIBILITY_CONTRACT")
    topology = load_embedded(paths["topology"], "TOPOLOGY")
    runtime_path = output / STAGE_A_RUNTIME_RESULT_FILE
    runtime_result: Mapping[str, Any] = {}
    if runtime_path.is_file():
        runtime_result = json.loads(runtime_path.read_text(encoding="utf-8"))

    children = {row["role"]: row for row in supervisor_status.get("children", [])}
    carla_binding = _binding_artifact(run_id, "carla", children.get("carla"), supervisor_status)
    evaluator_binding = _binding_artifact(run_id, "evaluator", children.get("evaluator"), supervisor_status)
    _create_or_replace(output / "CARLA_PROCESS_BINDING.json", carla_binding)
    _create_or_replace(output / "EVALUATOR_PROCESS_BINDING.json", evaluator_binding)

    first_path = output / "FIRST_OBSERVATION_EVIDENCE.json"
    if not first_path.exists():
        atomic_create_json(
            first_path,
            {
                "schema_version": "driveclarify.first_observation_evidence.v1",
                "run_id": run_id,
                "unit_id": item["unit_id"],
                "observation_identity": None,
                "observation_index": None,
                "source_frame": None,
                "observation_hash": None,
                "signed_station_m": None,
                "eligibility_interval": contract["eligibility_interval"],
                "outcome_at_observation_gate": None,
                "reason_codes": ["ENGINEERING_FAILURE_BEFORE_FIRST_MODEL_READY_OBSERVATION"],
                "candidate_forward_count": 0,
                "second_observation_count": 0,
            },
        )
    first = json.loads(first_path.read_text(encoding="utf-8"))
    observation_reached = first.get("observation_index") == 0

    manifest_path = output / "OBSERVATION_PACKAGE_MANIFEST.json"
    if not manifest_path.exists():
        unavailable_manifest(
            manifest_path=manifest_path,
            run_id=run_id,
            unit_id=item["unit_id"],
            status="NOT_CREATED_RUNTIME_FAILURE",
            reason_codes=["ENGINEERING_FAILURE_BEFORE_COMPLETE_PACKAGE_MANIFEST"],
            eligibility_contract_sha256=contract["sha256"],
            topology_sha256=topology["sha256"],
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    package_check = _package_verification(output, manifest)

    cleanup_pass = (
        supervisor_status.get("cleanup_complete") is True
        and all(supervisor_status.get("ports_free_after_cleanup", {}).values())
        and not supervisor_status.get("surviving_processes")
        and not supervisor_status.get("owned_descendants_after_cleanup")
        and not supervisor_status.get("tagged_processes_after_cleanup")
        and not supervisor_status.get("gpu_compute_after_cleanup", {}).get("processes")
    )
    cleanup_saved = supervisor_status.get("cleanup", {}).get("saved_children", {})
    process_cleanup = {
        "schema_version": "driveclarify.observation_screening_process_cleanup.v1",
        "batch_id": BATCH_ID,
        "run_id": run_id,
        "status": "PASS" if cleanup_pass else "FAIL",
        "cleanup_attempted": supervisor_status.get("cleanup_attempted"),
        "cleanup_completed": supervisor_status.get("cleanup_completed"),
        "target_source": "DIRECT_POPEN_SAVED_IDENTITY_WITH_BOUND_PID_STARTTIME_EXECUTABLE_COMMAND",
        "transport": "PIDFD",
        "process_results": cleanup_saved.get("process_results", []),
        "signals_sent": cleanup_saved.get("signals_sent", []),
        "ports_free_after_cleanup": supervisor_status.get("ports_free_after_cleanup"),
        "gpu_compute_after_cleanup": supervisor_status.get("gpu_compute_after_cleanup"),
        "surviving_processes": supervisor_status.get("surviving_processes"),
        "owned_descendants_after_cleanup": supervisor_status.get("owned_descendants_after_cleanup"),
        "tagged_processes_after_cleanup": supervisor_status.get("tagged_processes_after_cleanup"),
        "global_string_scan_signal_targets": 0,
        "physical_display": {"display": ":1", "xauthority": "/run/user/1000/gdm/Xauthority"},
    }
    _create_or_replace(output / "PROCESS_CLEANUP.json", process_cleanup)

    gpu = run_command(
        [
            "nvidia-smi",
            "--query-gpu=index,name,uuid,display_active,memory.total,memory.used",
            "--format=csv,noheader,nounits",
        ]
    )
    compute = run_command(
        [
            "nvidia-smi",
            "--query-compute-apps=pid,process_name,used_memory",
            "--format=csv,noheader,nounits",
        ]
    )
    log_text = (output / "logs/supervisor_execution.log").read_text(
        encoding="utf-8", errors="replace"
    )
    gpu_record = {
        "schema_version": "driveclarify.observation_screening_gpu_resource_record.v1",
        "batch_id": BATCH_ID,
        "run_id": run_id,
        "gpu_query_after_cleanup": gpu,
        "compute_processes_after_cleanup": [
            line for line in compute["output"].splitlines() if line.strip()
        ],
        "checkpoint_or_model_used": observation_reached,
        "oom": "out of memory" in log_text.lower() or "cuda oom" in log_text.lower(),
        "gpu_peak_memory_mib": None,
        "gpu_peak_status": "UNKNOWN_NOT_CONTINUOUSLY_SAMPLED",
        "cleanup_compute_process_count": len(
            supervisor_status.get("gpu_compute_after_cleanup", {}).get("processes", [])
        ),
    }
    _create_or_replace(output / "GPU_RESOURCE_RECORD.json", gpu_record)

    counts_path = output / "RUNTIME_COUNTS.json"
    if counts_path.exists():
        counts: Dict[str, Any] = dict(json.loads(counts_path.read_text(encoding="utf-8")))
    else:
        counts = {
            "schema_version": "driveclarify.observation_screening_runtime_counts.v1",
            "run_id": run_id,
            "model_ready_observation": 0,
            "observation_index_zero_reads": 0,
            "second_observation": 0,
            "candidate_forward": 0,
            "candidate_semantic_payload": 0,
            "mapper_invocation": 0,
            "training": 0,
            "act_ask_wait": 0,
            "pid": 0,
            "planner_advance": 0,
            "control_send_after_observation": 0,
        }
    counts.update(
        {
            "batch_id": BATCH_ID,
            "run_id": run_id,
            "pre_receipt_selftest": 1,
            "inner_launch_gate_selftest": 1,
            "carla_launch": 1 if "carla" in children else 0,
            "evaluator_launch": 1 if "evaluator" in children else 0,
            "checkpoint_load": 1 if observation_reached else 0,
            "model_load": 1 if observation_reached else 0,
            "model_ready_observation": 1 if observation_reached else 0,
            "observation_index_zero_reads": 1 if observation_reached else 0,
            "candidate_forward": int(counts.get("candidate_forward", 0)),
            "second_observation": int(counts.get("second_observation", 0)),
            "retry_same_run_id": 0,
            "second_real_run_same_id": 0,
            "mapper_invocation": 0,
        }
    )
    _create_or_replace(counts_path, counts)

    carla_revalidated = (
        carla_binding.get("explicit_direct_binding_revalidation", {}).get("status") == "PASS"
    )
    evaluator_revalidated = (
        evaluator_binding.get("explicit_direct_binding_revalidation", {}).get("status") == "PASS"
    )
    raw_outcome = runtime_result.get("outcome")
    protocol_zero = counts["candidate_forward"] == 0 and counts["second_observation"] == 0
    package_ok = package_check.get("verified") is True
    if (
        raw_outcome in {"ELIGIBLE", "EVIDENCE_UNAVAILABLE"}
        and observation_reached
        and protocol_zero
        and package_ok
        and cleanup_pass
        and carla_revalidated
        and evaluator_revalidated
    ):
        outcome = str(raw_outcome)
        reason_codes = list(first.get("reason_codes", []))
    else:
        outcome = "RUNTIME_FAILURE"
        reason_codes = ["ATTRIBUTABLE_OBSERVATION_SCREENING_ENGINEERING_FAILURE"]
        if not cleanup_pass:
            reason_codes.append("PROCESS_CLEANUP_FAILED")
        if not carla_revalidated:
            reason_codes.append("CARLA_DIRECT_BINDING_REVALIDATION_FAILED")
        if not evaluator_revalidated:
            reason_codes.append("EVALUATOR_DIRECT_BINDING_REVALIDATION_FAILED")
        if not protocol_zero:
            reason_codes.append("PROHIBITED_RUNTIME_COUNT_NONZERO")
        if not package_ok:
            reason_codes.append("OBSERVATION_PACKAGE_PERSISTENCE_OR_MANIFEST_FAILED")
        if raw_outcome not in {"ELIGIBLE", "EVIDENCE_UNAVAILABLE"}:
            reason_codes.append("RUNTIME_RESULT_NOT_SCIENTIFIC_TERMINAL")
    interval = contract["eligibility_interval"]
    runtime_metadata = {
        "batch_id": BATCH_ID,
        "run_id": run_id,
        "town": unit["town"],
        "route_id": unit["route_id"],
        "junction_id": unit["junction_id"],
        "eligibility_interval": interval,
        "package_path": manifest.get("package_directory") if outcome == "ELIGIBLE" else None,
        "package_verification": package_check,
        "run_role": role,
    }
    screening = {
        "schema_version": "driveclarify.observation_screening_output.v1",
        "unit_id": item["unit_id"],
        "outcome": outcome,
        "observation_index": first.get("observation_index") if observation_reached else None,
        "observation_identity": first.get("observation_identity"),
        "signed_station_m": first.get("signed_station_m"),
        "eligibility_contract_sha256": contract["sha256"],
        "candidate_forward_count": counts["candidate_forward"],
        "second_observation_count": counts["second_observation"],
        "cleanup_status": "PASS" if cleanup_pass else "FAIL",
        "reason_codes": reason_codes,
        "observation_hash": first.get("observation_hash"),
        "source_frame": first.get("source_frame"),
        "ego_pose": first.get("ego_pose"),
        "runtime_metadata": runtime_metadata,
    }
    _create_or_replace(output / "OBSERVATION_SCREENING_RESULT.json", screening)

    receipt = json.loads((output / "AUTHORIZATION_RECEIPT.json").read_text(encoding="utf-8"))
    claims = list((output / "receipt_claims").glob("*.receipt-claim.json"))
    spec_path = output / "RUN_SPEC.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec.update(
        {
            "run_authorized": False,
            "authorization_consumed": True,
            "execution_status": outcome,
            "carla_launches_this_package": counts["carla_launch"],
            "evaluator_launches_this_package": counts["evaluator_launch"],
            "gpu_uses_this_package": 1 if counts["carla_launch"] else 0,
            "cuda_initializations_this_package": 1 if observation_reached else 0,
            "model_forwards_this_package": 0,
            "simlingo_model_or_checkpoint_loads_this_package": 1 if observation_reached else 0,
        }
    )
    spec["sha256"] = embedded_hash(spec)
    atomic_replace_json(spec_path, spec)
    atomic_create_json(
        output / "AUTHORIZATION_CONSUMPTION.json",
        {
            "schema_version": "driveclarify.observation_screening_authorization_consumption.v1",
            "batch_id": BATCH_ID,
            "run_id": run_id,
            "receipt_id": receipt["receipt_id"],
            "receipt_sha256": sha256_path(output / "AUTHORIZATION_RECEIPT.json"),
            "claim_count": len(claims),
            "claims": [{"path": str(path), "sha256": sha256_path(path)} for path in claims],
            "authorized_run_spec_sha256": sha256_path(output / "RUN_SPEC_AUTHORIZED.json"),
            "terminal_run_spec_sha256": sha256_path(spec_path),
            "authorization_consumed": True,
            "reusable": False,
            "retry_same_run_id_allowed": False,
        },
    )
    final_result = {
        "schema_version": "driveclarify.observation_screening_run_result.v1",
        "batch_id": BATCH_ID,
        "run_id": run_id,
        "unit_id": item["unit_id"],
        "run_role": role,
        "outcome": outcome,
        "terminal": True,
        "reason_codes": reason_codes,
        "observation_screening": screening,
        "package_verification": package_check,
        "first_runtime_exception": _first_error(output, runtime_result, supervisor_status),
        "supervisor_terminal_error": supervisor_status.get("error"),
        "supervisor_exit_code": launch_record.get("exit_code"),
        "carla_binding": carla_binding.get("binding_status"),
        "carla_revalidation": carla_binding.get("explicit_direct_binding_revalidation", {}).get("status"),
        "evaluator_binding": evaluator_binding.get("binding_status"),
        "evaluator_revalidation": evaluator_binding.get("explicit_direct_binding_revalidation", {}).get("status"),
        "runtime_counts": counts,
        "cleanup": "PASS" if cleanup_pass else "FAIL",
        "recovery_authorized": outcome == "RUNTIME_FAILURE" and role == "primary",
        "retry_same_run_id_attempted": False,
        "second_observation_attempted": False,
        "candidate_forward_attempted": False,
        "mapper_invoked": False,
        "training_invoked": False,
        "act_ask_wait_invoked": False,
        "terminalized_at_utc": utc_now(),
    }
    if outcome == "ELIGIBLE":
        final_result["terminal_category"] = "TERMINAL_SUCCESS"
        final_result["exclusion"] = None
    else:
        exclusion_category = "EVIDENCE" if outcome == "EVIDENCE_UNAVAILABLE" else "ENGINEERING"
        final_result["terminal_category"] = "TERMINAL_EXCLUSION"
        final_result["exclusion"] = {
            "category": exclusion_category,
            "reason_codes": list(reason_codes),
        }
    publish_terminal_result(output, final_result)
    append_log(
        output / "COMMAND_LOG.md",
        "- Terminal outcome: `{}`; signed station=`{}`; interval=`[{}, {})`.\n"
        "- candidate forward=0; second observation=0; cleanup=`{}`; same-ID retry=0.".format(
            outcome,
            screening["signed_station_m"],
            interval["lower_inclusive_m"],
            interval["upper_exclusive_m"],
            screening["cleanup_status"],
        ),
    )
    staging = json.loads((output / "STAGING_CONTEXT.json").read_text(encoding="utf-8"))
    sim_start = staging["git_before_staging"]["simlingo"]
    sim_end = p3.git_state(SIMLINGO)
    sim_unchanged = all(
        sim_start[key] == sim_end[key]
        for key in (
            "branch",
            "head",
            "tracked_diff_bytes",
            "tracked_diff_sha256",
            "staged_diff_bytes",
            "staged_diff_sha256",
            "untracked_file_count",
            "untracked_path_list_nul_sha256",
        )
    )
    atomic_create_json(
        output / "GIT_START_END.json",
        {
            "schema_version": "driveclarify.observation_screening_git_start_end.v1",
            "batch_id": BATCH_ID,
            "run_id": run_id,
            "start_before_staging": staging["git_before_staging"],
            "end": {"driveclarify": p3.git_state(ROOT), "simlingo": sim_end},
            "simlingo_unchanged": sim_unchanged,
            "protected_history": protected_history(),
            "frozen_authority_aggregate_sha256": authority_snapshot()["aggregate_sha256"],
            "git_commit_reset_clean_restore_checkout_used": False,
        },
    )
    required = [
        "AUTHORIZATION_RECEIPT.json",
        "RUN_RESULT.json",
        STAGE_A_RUNTIME_RESULT_FILE,
        "ENTRY_PREFLIGHT.json",
        "PRELAUNCH_SUPERVISOR_SELFTEST.json",
        "CARLA_PROCESS_BINDING.json",
        "EVALUATOR_PROCESS_BINDING.json",
        "OBSERVATION_SCREENING_RESULT.json",
        "FIRST_OBSERVATION_EVIDENCE.json",
        "OBSERVATION_PACKAGE_MANIFEST.json",
        "RUNTIME_COUNTS.json",
        "GPU_RESOURCE_RECORD.json",
        "PROCESS_CLEANUP.json",
        "COMMAND_LOG.md",
    ]
    missing = [name for name in required if not (output / name).is_file()]
    if missing:
        raise BatchError("REQUIRED_RUN_ARTIFACTS_MISSING:" + ",".join(missing))
    artifacts = _inventory_rows(output, exclude=(output / "ARTIFACT_INVENTORY.json",))
    atomic_create_json(
        output / "ARTIFACT_INVENTORY.json",
        {
            "schema_version": "driveclarify.observation_screening_artifact_inventory.v1",
            "batch_id": BATCH_ID,
            "run_id": run_id,
            "terminal_outcome": outcome,
            "required_artifacts_present": True,
            "artifact_count_excluding_inventory": len(artifacts),
            "artifacts": artifacts,
            "simlingo_unchanged": sim_unchanged,
            "candidate_forward_count": 0,
            "second_observation_count": 0,
            "mapper_invocation_count": 0,
        },
    )
    return outcome


def _terminal_for_unit(item: Mapping[str, Any]) -> Tuple[Mapping[str, Any], List[str], str]:
    primary_lifecycle = read_run_lifecycle(run_output(item["primary"]))
    recovery_lifecycle = read_run_lifecycle(run_output(item["recovery"]))
    if primary_lifecycle["terminal"] is not True:
        raise BatchError("PRIMARY_NOT_TERMINAL:" + item["unit_id"])
    primary = primary_lifecycle["payload"]
    if primary.get("outcome") in {"ELIGIBLE", "EVIDENCE_UNAVAILABLE"}:
        if recovery_lifecycle["terminal"] is True or run_output(item["recovery"]).exists():
            raise BatchError("RECOVERY_USED_AFTER_SCIENTIFIC_RESULT:" + item["unit_id"])
        return primary, [item["primary"]], "NOT_USED"
    if primary.get("outcome") != "RUNTIME_FAILURE":
        raise BatchError("PRIMARY_OUTCOME_INVALID:" + item["unit_id"])
    if recovery_lifecycle["terminal"] is not True:
        raise BatchError("RECOVERY_REQUIRED_BUT_NOT_TERMINAL:" + item["unit_id"])
    recovery = recovery_lifecycle["payload"]
    disposition = (
        "USED_ENGINEERING_FAILURE_RECOVERY_SUCCEEDED"
        if recovery.get("outcome") != "RUNTIME_FAILURE"
        else "USED_ENGINEERING_FAILURE_RECOVERY_EXHAUSTED"
    )
    return recovery, [item["primary"], item["recovery"]], disposition


def _sum_counts(results: Sequence[Mapping[str, Any]]) -> Mapping[str, int]:
    names = (
        "pre_receipt_selftest",
        "inner_launch_gate_selftest",
        "carla_launch",
        "evaluator_launch",
        "checkpoint_load",
        "model_load",
        "model_ready_observation",
        "observation_index_zero_reads",
        "candidate_forward",
        "second_observation",
        "mapper_invocation",
        "training",
        "act_ask_wait",
        "pid",
        "planner_advance",
        "control_send_after_observation",
        "retry_same_run_id",
        "second_real_run_same_id",
    )
    return {
        name: sum(int(result.get("runtime_counts", {}).get(name, 0)) for result in results)
        for name in names
    }


def finalize_batch() -> None:
    validate_no_unattributed_change()
    unit_rows = []
    terminal_results = []
    all_used_results = []
    recovery_rows = []
    for item in UNIT_RUNS:
        terminal, used_run_ids, recovery_disposition = _terminal_for_unit(item)
        terminal_results.append(terminal)
        for used in used_run_ids:
            all_used_results.append(
                json.loads((run_output(used) / "RUN_RESULT.json").read_text(encoding="utf-8"))
            )
        recovery_rows.append(
            {
                "unit_id": item["unit_id"],
                "recovery_run_id": item["recovery"],
                "disposition": recovery_disposition,
            }
        )
        screening = terminal["observation_screening"]
        metadata = screening.get("runtime_metadata", {})
        unit_rows.append(
            {
                "unit_id": item["unit_id"],
                "primary_run_id": item["primary"],
                "recovery_run_id": item["recovery"],
                "used_run_ids": used_run_ids,
                "town": metadata.get("town"),
                "route_id": metadata.get("route_id"),
                "junction_id": metadata.get("junction_id"),
                "observation_index": screening.get("observation_index"),
                "source_frame": screening.get("source_frame"),
                "signed_station_m": screening.get("signed_station_m"),
                "eligibility_interval": metadata.get("eligibility_interval"),
                "outcome": screening["outcome"],
                "terminal_disposition": (
                    "RUNTIME_FAILURE_EXHAUSTED"
                    if screening["outcome"] == "RUNTIME_FAILURE"
                    and recovery_disposition.endswith("EXHAUSTED")
                    else screening["outcome"]
                ),
                "reason_codes": screening.get("reason_codes", []),
                "observation_hash": screening.get("observation_hash"),
                "package_path": metadata.get("package_path"),
                "cleanup": screening.get("cleanup_status"),
                "runtime_counts": terminal.get("runtime_counts", {}),
                "recovery_disposition": recovery_disposition,
            }
        )
    if len(unit_rows) != 6:
        raise BatchError("TERMINAL_UNIT_COUNT_NOT_SIX")
    totals = _sum_counts(all_used_results)
    if (
        totals["candidate_forward"] != 0
        or totals["second_observation"] != 0
        or totals["mapper_invocation"] != 0
        or totals["training"] != 0
        or totals["act_ask_wait"] != 0
    ):
        raise BatchError("PROHIBITED_BATCH_RUNTIME_COUNT_NONZERO")
    if any(row["cleanup"] != "PASS" for row in unit_rows):
        raise BatchError("BATCH_CLEANUP_NOT_PASS")
    if any(
        row["outcome"] == "ELIGIBLE" and not row.get("package_path") for row in unit_rows
    ):
        raise BatchError("ELIGIBLE_UNIT_PACKAGE_MISSING")
    eligible = [row for row in unit_rows if row["outcome"] == "ELIGIBLE"]
    unavailable = [row for row in unit_rows if row["outcome"] == "EVIDENCE_UNAVAILABLE"]
    failures = [row for row in unit_rows if row["outcome"] == "RUNTIME_FAILURE"]
    used_run_ids = [run_id for row in unit_rows for run_id in row["used_run_ids"]]
    authority_end = validate_authority()
    authority_state = authority_snapshot()
    preflight = json.loads((BATCH_ROOT / "BATCH_CPU_PREFLIGHT.json").read_text(encoding="utf-8"))
    if authority_state["aggregate_sha256"] != preflight["authority_snapshot"]["aggregate_sha256"]:
        raise BatchError("FROZEN_AUTHORITY_CHANGED_DURING_BATCH")
    history = protected_history()
    sim_end = p3.git_state(SIMLINGO)
    sim_start = json.loads((BATCH_ROOT / "BATCH_AUTHORIZATION.json").read_text(encoding="utf-8"))[
        "git_at_batch_entry"
    ]["simlingo"]
    sim_unchanged = all(
        sim_start[key] == sim_end[key]
        for key in (
            "branch",
            "head",
            "tracked_diff_bytes",
            "tracked_diff_sha256",
            "staged_diff_bytes",
            "staged_diff_sha256",
            "untracked_file_count",
            "untracked_path_list_nul_sha256",
        )
    )
    if not sim_unchanged:
        raise BatchError("SIMLINGO_CHANGED_DURING_BATCH")

    atomic_create_json(
        BATCH_ROOT / "BATCH_UNIT_SUMMARY.json",
        {
            "schema_version": "driveclarify.batch_observation_unit_summary.v1",
            "batch_id": BATCH_ID,
            "unit_count": 6,
            "units": unit_rows,
        },
    )
    atomic_create_json(
        BATCH_ROOT / "ELIGIBLE_UNITS_MANIFEST.json",
        {
            "schema_version": "driveclarify.eligible_observation_units_manifest.v1",
            "batch_id": BATCH_ID,
            "count": len(eligible),
            "units": eligible,
        },
    )
    atomic_create_json(
        BATCH_ROOT / "EVIDENCE_UNAVAILABLE_UNITS_MANIFEST.json",
        {
            "schema_version": "driveclarify.evidence_unavailable_observation_units_manifest.v1",
            "batch_id": BATCH_ID,
            "count": len(unavailable),
            "units": unavailable,
        },
    )
    atomic_create_json(
        BATCH_ROOT / "RUNTIME_FAILURE_UNITS_MANIFEST.json",
        {
            "schema_version": "driveclarify.runtime_failure_observation_units_manifest.v1",
            "batch_id": BATCH_ID,
            "count": len(failures),
            "units": failures,
            "all_runtime_failure_attempts": [
                {
                    "run_id": result["run_id"],
                    "unit_id": result["unit_id"],
                    "run_role": result["run_role"],
                    "reason_codes": result["reason_codes"],
                }
                for result in all_used_results
                if result["outcome"] == "RUNTIME_FAILURE"
            ],
        },
    )
    atomic_create_json(
        BATCH_ROOT / "OBSERVATION_PACKAGE_INDEX.json",
        {
            "schema_version": "driveclarify.observation_package_index.v1",
            "batch_id": BATCH_ID,
            "eligible_package_count": len(eligible),
            "packages": [
                {
                    "unit_id": row["unit_id"],
                    "run_id": row["used_run_ids"][-1],
                    "observation_hash": row["observation_hash"],
                    "package_path": row["package_path"],
                    "manifest_path": str(
                        run_output(row["used_run_ids"][-1]) / "OBSERVATION_PACKAGE_MANIFEST.json"
                    ),
                    "manifest_sha256": sha256_path(
                        run_output(row["used_run_ids"][-1]) / "OBSERVATION_PACKAGE_MANIFEST.json"
                    ),
                }
                for row in eligible
            ],
        },
    )
    atomic_create_json(
        BATCH_ROOT / "BATCH_RUNTIME_COUNTS.json",
        {
            "schema_version": "driveclarify.batch_observation_runtime_counts.v1",
            "batch_id": BATCH_ID,
            "used_run_ids": used_run_ids,
            "used_run_count": len(used_run_ids),
            "totals": totals,
            "candidate_forward_total": 0,
            "second_observation_total": 0,
            "mapper_invocation_total": 0,
            "training_total": 0,
            "act_ask_wait_total": 0,
        },
    )
    gpu_cleanup_rows = []
    for run_id in used_run_ids:
        output = run_output(run_id)
        gpu_cleanup_rows.append(
            {
                "run_id": run_id,
                "gpu": json.loads((output / "GPU_RESOURCE_RECORD.json").read_text(encoding="utf-8")),
                "cleanup": json.loads((output / "PROCESS_CLEANUP.json").read_text(encoding="utf-8")),
            }
        )
    atomic_create_json(
        BATCH_ROOT / "BATCH_GPU_AND_CLEANUP.json",
        {
            "schema_version": "driveclarify.batch_observation_gpu_cleanup.v1",
            "batch_id": BATCH_ID,
            "all_run_cleanup_pass": True,
            "final_gpu_compute_process_count": sum(
                len(row["gpu"]["compute_processes_after_cleanup"]) for row in gpu_cleanup_rows
            ),
            "runs": gpu_cleanup_rows,
        },
    )
    next_step = (
        "MULTI_UNIT_OFFLINE_FROZEN_A3_B3_CANDIDATE_CAPTURE"
        if len(eligible) >= 3
        else (
            "PREPARE_ADDITIONAL_NEW_TOPOLOGY_UNITS"
            if len(eligible) >= 1
            else "USER_DECISION_ON_DEDICATED_OBSERVATION_CAPTURE_OR_REPLAY_DESIGN"
        )
    )
    batch_result = {
        "schema_version": "driveclarify.batch_observation_result.v1",
        "batch_id": BATCH_ID,
        "final_status": "BATCH_OBSERVATION_ELIGIBILITY_SCREEN_COMPLETE",
        "completed_at_utc": utc_now(),
        "selected_unit_count": 6,
        "terminal_unit_count": 6,
        "used_run_ids": used_run_ids,
        "eligible_count": len(eligible),
        "evidence_unavailable_count": len(unavailable),
        "runtime_failure_count": len(failures),
        "recovery": recovery_rows,
        "candidate_forward_total": 0,
        "second_observation_total": 0,
        "all_cleanup_pass": True,
        "simlingo_unchanged": True,
        "frozen_authority_unchanged": True,
        "protected_history_unchanged": True,
        "autonomous_repair_summary": {
            "repair_iteration_count": len(preflight.get("repair_iterations", [])),
            "failed_validation_iteration_count": sum(
                1
                for repair in preflight.get("repair_iterations", [])
                if repair.get("status") != "PASS"
            ),
            "production_fix_count": sum(
                1 for row in recovery_rows if row["disposition"] != "NOT_USED"
            ),
            "recovery_run_count": sum(
                1 for row in recovery_rows if row["disposition"] != "NOT_USED"
            ),
            "details": recovery_rows,
        },
        "may_enter_multi_unit_offline_frozen_a3_b3": len(eligible) >= 3,
        "unique_next_step": next_step,
        "authority_end": authority_end,
        "protected_history": history,
    }
    atomic_create_json(BATCH_ROOT / "BATCH_RESULT.json", batch_result)
    append_batch_log(
        "- Batch terminal: `BATCH_OBSERVATION_ELIGIBILITY_SCREEN_COMPLETE`; "
        "eligible/evidence-unavailable/runtime-failure=`{}/{}/{}`.\n"
        "- candidate forward total=0; second observation total=0; all cleanup PASS."
        .format(len(eligible), len(unavailable), len(failures))
    )
    atomic_create_json(
        BATCH_ROOT / "GIT_START_END_BATCH.json",
        {
            "schema_version": "driveclarify.batch_observation_git_start_end.v1",
            "batch_id": BATCH_ID,
            "start": json.loads((BATCH_ROOT / "BATCH_AUTHORIZATION.json").read_text(encoding="utf-8"))[
                "git_at_batch_entry"
            ],
            "end": {"driveclarify": p3.git_state(ROOT), "simlingo": sim_end},
            "simlingo_unchanged": True,
            "authority_snapshot": authority_state,
            "protected_history": history,
            "git_commit_reset_clean_restore_checkout_used": False,
        },
    )
    required = [
        "BATCH_AUTHORIZATION.json",
        "BATCH_RESULT.json",
        "BATCH_UNIT_SUMMARY.json",
        "ELIGIBLE_UNITS_MANIFEST.json",
        "EVIDENCE_UNAVAILABLE_UNITS_MANIFEST.json",
        "RUNTIME_FAILURE_UNITS_MANIFEST.json",
        "OBSERVATION_PACKAGE_INDEX.json",
        "BATCH_RUNTIME_COUNTS.json",
        "BATCH_GPU_AND_CLEANUP.json",
        "BATCH_COMMAND_LOG.md",
    ]
    missing = [name for name in required if not (BATCH_ROOT / name).is_file()]
    if missing:
        raise BatchError("REQUIRED_BATCH_ARTIFACTS_MISSING:" + ",".join(missing))
    artifacts = _inventory_rows(BATCH_ROOT, exclude=(BATCH_ROOT / "ARTIFACT_INVENTORY.json",))
    atomic_create_json(
        BATCH_ROOT / "ARTIFACT_INVENTORY.json",
        {
            "schema_version": "driveclarify.batch_observation_artifact_inventory.v1",
            "batch_id": BATCH_ID,
            "final_status": batch_result["final_status"],
            "required_artifacts_present": True,
            "artifact_count_excluding_inventory": len(artifacts),
            "artifacts": artifacts,
            "candidate_forward_total": 0,
            "second_observation_total": 0,
            "simlingo_unchanged": True,
            "frozen_authority_unchanged": True,
            "protected_history_unchanged": True,
        },
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("prepare-batch")
    subparsers.add_parser("preflight-batch")
    repair = subparsers.add_parser("record-repair")
    repair.add_argument("--reason", required=True)
    for name in ("stage-run", "cpu-check-run", "selftest-run", "authorize-run", "launch-run", "finalize-run"):
        command = subparsers.add_parser(name)
        command.add_argument("run_id", choices=ALL_RUN_IDS)
    subparsers.add_parser("finalize-batch")
    args = parser.parse_args(argv)
    if args.command == "prepare-batch":
        prepare_batch()
    elif args.command == "preflight-batch":
        preflight_batch()
    elif args.command == "record-repair":
        record_repair(args.reason)
    elif args.command == "stage-run":
        stage_run(args.run_id)
    elif args.command == "cpu-check-run":
        cpu_check_run(args.run_id)
    elif args.command == "selftest-run":
        prelaunch_selftest(args.run_id)
    elif args.command == "authorize-run":
        authorize_run(args.run_id)
    elif args.command == "launch-run":
        launch_run(args.run_id)
    elif args.command == "finalize-run":
        print(finalize_run(args.run_id))
    elif args.command == "finalize-batch":
        finalize_batch()
    else:
        raise BatchError("UNKNOWN_COMMAND")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
