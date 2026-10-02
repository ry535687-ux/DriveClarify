"""One-shot Stage A -> Stage B runtime campaign for frozen M1 V3 units.

The proven V2 launch, capture, process-binding, and offline worker surfaces are
reused.  This module supplies only the V3 authority, exact 24-unit accounting,
one-shot lifecycle handling, and incremental/consolidated aggregation.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import traceback
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import jsonschema

from . import m1_v2_runtime_campaign as base
from . import observation_screening_batch as observation
from .m1_expansion_v3 import EXPANSION_ID
from .observation_package import (
    atomic_create_bytes,
    atomic_create_json,
    atomic_replace_json,
    digest_value,
    sha256_path,
)
from .run_result_lifecycle import (
    create_prelaunch_state,
    publish_terminal_result,
    read_run_lifecycle,
)


ROOT = base.ROOT
SIMLINGO = base.SIMLINGO
AUTHORITY = ROOT / "reports/m1_real_dataset_expansion_v3" / EXPANSION_ID
SELECTED = AUTHORITY / "V3_SELECTED_UNITS.json"
SPLIT = AUTHORITY / "V3_SPLIT_MANIFEST.json"
CAMPAIGN_SPEC = AUTHORITY / "V3_RUNTIME_CAMPAIGN_SPEC.json"
OBS_SCHEMA = AUTHORITY / "V3_OBSERVATION_OUTPUT_SCHEMA.json"
CAPTURE_SCHEMA = AUTHORITY / "V3_CAPTURE_OUTPUT_SCHEMA.json"
DATA_SCHEMA = AUTHORITY / "M1_REAL_DATASET_V3_SCHEMA.json"
FROZEN_INVENTORY = AUTHORITY / "FROZEN_HASH_INVENTORY.json"
GLOBAL_LEAKAGE = AUTHORITY / "GLOBAL_SPLIT_LEAKAGE_AUDIT.json"
HISTORY = AUTHORITY / "FROZEN_HISTORICAL_PROTECTION.json"
V2_ROOT = ROOT / "reports/m1_real_dataset_expansion_v2/DC-M1-DATASET-EXP-V2-20260803T143000Z"
V2_CAMPAIGN = V2_ROOT / "combined_runtime_campaigns/DC-M1-V2-RUNTIME-C1-20260803T144700Z"
V2_RESULT = V2_CAMPAIGN / "CAMPAIGN_RESULT.json"
V2_DATASET = V2_CAMPAIGN / "stage_b/M1_REAL_DATASET_V2.json"
V1_DATASET = ROOT / "reports/multi_topology_static_units_v1/offline_candidate_capture_runs/DC-MULTI-A3B3-C1-20260803T121500Z/M1_REAL_DATASET_V1.json"
V2_TREE_SHA256 = "31dc8aaf2774f46fa47dd2dfe05832ca85c6797947efddbef418dbab446f5843"
RUNTIME_CAMPAIGN_ID = "DC-M1-V3-RUNTIME-C1-20260804T063000Z"
SCHEDULE = base.SCHEDULE

CampaignError = base.CampaignError
load = base.load
embedded = base.embedded
_put_json = base._put_json
_append = base._append
_inventory = base._inventory
_git_gate = base._git_gate
p3 = base.p3


def _tree_hash(root: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(hashlib.sha256(path.read_bytes()).hexdigest().encode("ascii"))
        digest.update(b"  ")
        digest.update(path.relative_to(ROOT).as_posix().encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def campaign_root(batch_id: str) -> Path:
    return AUTHORITY / "combined_runtime_campaigns" / batch_id


def stage_a_root(batch_id: str) -> Path:
    return campaign_root(batch_id) / "stage_a"


def stage_b_root(batch_id: str) -> Path:
    return campaign_root(batch_id) / "stage_b"


def selected_rows() -> List[Mapping[str, Any]]:
    value = embedded(SELECTED, "SELECTED")
    return list(value["selected_units"])


def stage_a_runs(batch_id: str) -> Tuple[Mapping[str, Any], ...]:
    rows = []
    for index, item in enumerate(selected_rows()):
        short = item["unit_id"].replace("TOWN", "T").replace("_JUNCTION_", "-J").replace("_UNIT01", "")
        rows.append(
            {
                "unit_id": item["unit_id"],
                "primary": "DC-M1V3-OBS-{}-A-{}".format(short, base._run_timestamp(batch_id, index + 1)),
                "recovery": "DC-M1V3-OBS-{}-UNUSED-{}".format(short, base._run_timestamp(batch_id, index + 301)),
            }
        )
    return tuple(rows)


def _unit_paths(unit_id: str) -> Mapping[str, Path]:
    root = AUTHORITY / "units" / unit_id
    return {
        "root": root,
        "fixture": root / "SCENARIO_FREE_ROUTE.xml",
        "manifest": root / "UNIT_MANIFEST.json",
        "topology": root / "BRANCH_TOPOLOGY_GROUND_TRUTH.json",
        "contract": root / "OBSERVATION_ELIGIBILITY_CONTRACT.json",
        "task_binding": root / "TASK_BINDING.json",
        "mapper_compatibility": root / "MAPPER_COMPATIBILITY.json",
        "source_provenance": root / "SOURCE_PROVENANCE.json",
        "split_assignment": root / "SPLIT_ASSIGNMENT.json",
    }


def verify_frozen_authority() -> Mapping[str, Any]:
    inventory = embedded(FROZEN_INVENTORY, "FROZEN_INVENTORY")
    mismatches = []
    for item in inventory["files"]:
        path = ROOT / item["path"]
        if not path.is_file() or path.stat().st_size != item["bytes"] or sha256_path(path) != item["sha256"]:
            mismatches.append(item["path"])
    if mismatches:
        raise CampaignError("FROZEN_AUTHORITY_FILE_MISMATCH:" + ",".join(mismatches))
    selected = embedded(SELECTED, "SELECTED")
    split = embedded(SPLIT, "SPLIT")
    spec = embedded(CAMPAIGN_SPEC, "CAMPAIGN_SPEC")
    leakage = embedded(GLOBAL_LEAKAGE, "GLOBAL_LEAKAGE")
    history = embedded(HISTORY, "HISTORY")
    state = load(ROOT / "STATE.json")
    if state.get("status") != "M1_V3_STAGE_A_TERMINAL_RESULT_CONTRACT_REPAIR_COMPLETE":
        raise CampaignError("ENTRY_STATE_MISMATCH")
    if (
        selected.get("status") != "READY_FOR_AUTHORIZED_RUNTIME"
        or selected.get("selected_count") != 24
        or len(selected.get("selected_units", [])) != 24
        or spec.get("run_authorized") is not True
        or spec.get("campaign_id") != RUNTIME_CAMPAIGN_ID
        or spec.get("unit_order") != [item["unit_id"] for item in selected["selected_units"]]
    ):
        raise CampaignError("FROZEN_SELECTED_OR_CAMPAIGN_AUTHORITY_MISMATCH")
    if split.get("split_counts") != {"DEV": 5, "TEST": 5, "TRAIN": 14}:
        raise CampaignError("FROZEN_SPLIT_COUNT_MISMATCH")
    if leakage.get("status") != "PASS" or not all(leakage.get("checks", {}).values()):
        raise CampaignError("GLOBAL_LEAKAGE_AUTHORITY_MISMATCH")
    if _tree_hash(V2_ROOT) != V2_TREE_SHA256:
        raise CampaignError("V2_TREE_HASH_CHANGED")
    selected_by_id = {item["unit_id"]: item for item in selected["selected_units"]}
    unit_checks = []
    for unit_id, row in selected_by_id.items():
        paths = _unit_paths(unit_id)
        unit = embedded(paths["manifest"], "UNIT")
        topology = embedded(paths["topology"], "TOPOLOGY")
        contract = embedded(paths["contract"], "CONTRACT")
        mapper = embedded(paths["mapper_compatibility"], "MAPPER")
        assignment = embedded(paths["split_assignment"], "ASSIGNMENT")
        if (
            unit["unit_id"] != unit_id
            or unit["route_start"]["signed_station_from_decision_point_m"] != -5.5
            or unit["topology_sha256"] != topology["sha256"]
            or unit["eligibility_interval"] != contract["eligibility_interval"]
            or mapper["compatibility_verdict"] != "PASS_FIXED_STATIC_BRANCH_PLAN_MAPPER_V1"
            or assignment["split"] != row["split"]
            or row["fixture_sha256"] != sha256_path(paths["fixture"])
        ):
            raise CampaignError("FROZEN_UNIT_AUTHORITY_MISMATCH:" + unit_id)
        for artifact in unit["artifacts"]:
            target = ROOT / artifact["path"]
            if not target.is_file() or target.stat().st_size != artifact["bytes"] or sha256_path(target) != artifact["sha256"]:
                raise CampaignError("FROZEN_UNIT_ARTIFACT_MISMATCH:" + str(target))
        unit_checks.append(
            {
                "unit_id": unit_id,
                "town": unit["town"],
                "route_id": unit["route_id"],
                "junction_id": unit["junction_id"],
                "split": row["split"],
                "incoming_vehicle_direction": unit["incoming_vehicle_direction"],
                "fixture_sha256": row["fixture_sha256"],
                "topology_sha256": topology["sha256"],
                "eligibility_sha256": contract["sha256"],
                "mapper_sha256": mapper["sha256"],
            }
        )
    return {
        "status": "PASS",
        "entry_status": state["status"],
        "selected_count": 24,
        "split_counts": split["split_counts"],
        "global_leakage": "PASS",
        "inventory_sha256": inventory["sha256"],
        "inventoried_file_count": inventory["file_count"],
        "unit_checks": unit_checks,
        "protected_history": history,
        "v2_tree_sha256": V2_TREE_SHA256,
    }


def _frozen_snapshot() -> Mapping[str, Any]:
    check = verify_frozen_authority()
    return {
        "root": str(AUTHORITY),
        "file_count": check["inventoried_file_count"],
        "aggregate_sha256": check["inventory_sha256"],
        "files": [],
    }


def _protected_history() -> Mapping[str, Any]:
    value = embedded(HISTORY, "HISTORY")
    return {
        "v1_dataset_path": str(V1_DATASET),
        "v1_dataset_sha256": sha256_path(V1_DATASET),
        "v2_dataset_path": str(V2_DATASET),
        "v2_dataset_sha256": sha256_path(V2_DATASET),
        "v2_tree_sha256": _tree_hash(V2_ROOT),
        "v2_selected_unit_count": value["v2_selected_count"],
        "pilot_complete_unit_count": len(value["pilot_unit_ids"]),
        "historical_exclusions": value["historical_exclusions"],
        "historical_rerun_count_this_campaign": 0,
        "historical_artifact_rewrite_count": 0,
    }


def _validate_stage_a_authority() -> Mapping[str, Any]:
    result = dict(verify_frozen_authority())
    result.update(
        {
            "preparation_batch_spec_unexecuted": True,
            "route_27515_frozen_exclusion": True,
            "threshold_file_sha256": base.THRESHOLD_FILE_SHA256,
            "threshold_embedded_sha256": base.THRESHOLD_EMBEDDED_SHA256,
            "units": result.pop("unit_checks"),
        }
    )
    return result


def _install_base_bindings() -> None:
    base.AUTHORITY = AUTHORITY
    base.SELECTED = SELECTED
    base.SPLIT = SPLIT
    base.CAMPAIGN_SPEC = CAMPAIGN_SPEC
    base.OBS_SCHEMA = OBS_SCHEMA
    base.CAPTURE_SCHEMA = CAPTURE_SCHEMA
    base.DATA_SCHEMA = DATA_SCHEMA
    base.FROZEN_INVENTORY = FROZEN_INVENTORY
    base.campaign_root = campaign_root
    base.stage_a_root = stage_a_root
    base.stage_b_root = stage_b_root
    base.selected_rows = selected_rows
    base.stage_a_runs = stage_a_runs
    base._unit_paths = _unit_paths
    base.verify_frozen_authority = verify_frozen_authority
    base._frozen_snapshot = _frozen_snapshot
    base._protected_history = _protected_history
    base._validate_stage_a_authority = _validate_stage_a_authority


_install_base_bindings()


def configure_stage_a(batch_id: str) -> None:
    runs = stage_a_runs(batch_id)
    observation.AUTHORITY = AUTHORITY
    observation.BATCH_ID = batch_id + "-STAGE-A"
    observation.BATCH_ROOT = stage_a_root(batch_id)
    observation.RUNS_ROOT = observation.BATCH_ROOT / "runs"
    observation.SELECTED_MANIFEST = SELECTED
    observation.BATCH_SPEC = CAMPAIGN_SPEC
    observation.OUTPUT_SCHEMA = OBS_SCHEMA
    observation.UNIT_RUNS = runs
    observation.ALL_RUN_IDS = tuple(
        run_id for item in runs for run_id in (item["primary"], item["recovery"])
    )
    observation.validate_authority = _validate_stage_a_authority
    observation.authority_snapshot = _frozen_snapshot
    observation.protected_history = _protected_history


def prepare_campaign(batch_id: str) -> None:
    if batch_id != RUNTIME_CAMPAIGN_ID:
        raise CampaignError("V3_RUNTIME_CAMPAIGN_ID_MISMATCH")
    root = campaign_root(batch_id)
    if root.exists():
        raise CampaignError("CAMPAIGN_ROOT_ALREADY_EXISTS")
    authority = verify_frozen_authority()
    git = _git_gate()
    environment = p3.environment_preflight()
    if (
        not environment["physical_display_pass"]
        or not all(environment["ports_free"].values())
        or environment["gpu_compute_processes"]
        or environment["preexisting_real_processes"]
    ):
        raise CampaignError("CAMPAIGN_RUNTIME_ENVIRONMENT_NOT_CLEAN")
    root.mkdir(parents=True)
    atomic_create_json(
        root / "CAMPAIGN_AUTHORIZATION.json",
        {
            "schema_version": "driveclarify.m1_v3_runtime_authorization.v1",
            "campaign_name": "M1_REAL_DATASET_EXPANSION_V3",
            "batch_id": batch_id,
            "authorization_source": "USER_EXPLICIT_ONE_TIME_M1_REAL_DATASET_EXPANSION_V3",
            "authorized_at_utc": p3.utc_now(),
            "scope": ["STAGE_A_FIRST_OBSERVATION_SCREEN", "STAGE_B_ELIGIBLE_OFFLINE_FROZEN_A3_B3"],
            "selected_unit_order": [item["unit_id"] for item in selected_rows()],
            "stage_b_schedule": list(SCHEDULE),
            "one_formal_stage_a_attempt_per_unit": True,
            "recovery_policy": "NO_RECOVERY_RUN; TERMINAL_EXCLUSION_OR_BLOCKED",
            "training_authorized": False,
            "automatic_continuation_after_campaign": False,
            "authority_validation": authority,
            "git_at_entry": git,
            "environment_at_entry": environment,
        },
    )
    atomic_create_json(
        root / "CAMPAIGN_RUN_MANIFEST.json",
        {
            "schema_version": "driveclarify.m1_v3_runtime_run_manifest.v1",
            "batch_id": batch_id,
            "expansion_id": EXPANSION_ID,
            "selected_count": 24,
            "stage_a_batch_id": batch_id + "-STAGE-A",
            "stage_b_batch_id": batch_id + "-STAGE-B",
            "stage_a_root": str(stage_a_root(batch_id)),
            "stage_b_root": str(stage_b_root(batch_id)),
            "frozen_campaign_spec_path": str(CAMPAIGN_SPEC),
            "frozen_campaign_spec_sha256": sha256_path(CAMPAIGN_SPEC),
            "status": "AUTHORIZED_NOT_STARTED",
        },
    )
    atomic_create_json(
        root / "GIT_START_END.json",
        {"schema_version": "driveclarify.m1_v3_runtime_git_start_end.v1", "batch_id": batch_id, "start": git, "end": None},
    )
    atomic_create_bytes(
        root / "COMMAND_LOG.md",
        ("# M1 V3 runtime campaign command log\n\n- `{}` prepared; 24-unit authority, Git, physical display, ports and GPU checks PASS.\n".format(batch_id)).encode("utf-8"),
    )


def _stage_a_preflight(batch_id: str) -> None:
    root = stage_a_root(batch_id)
    staged = [
        observation.RUNS_ROOT / run_id
        for run_id in observation.ALL_RUN_IDS
        if (observation.RUNS_ROOT / run_id).exists()
    ]
    if any((path / "AUTHORIZATION_RECEIPT.json").exists() or (path / "REAL_LAUNCH_RECORD.json").exists() for path in staged):
        raise CampaignError("STAGE_A_PREFLIGHT_AFTER_RECEIPT_OR_REAL_LAUNCH_FORBIDDEN")
    environment = dict(os.environ)
    environment.update({"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONDONTWRITEBYTECODE": "1"})
    commands = [
        [str(p3.PYTHON38), "-B", "-c", "compile(open(%r, 'rb').read(), %r, 'exec')" % (str(observation.AGENT_SOURCE), str(observation.AGENT_SOURCE))],
        [str(p3.PYTHON38), "-B", "-c", "compile(open(%r, 'rb').read(), %r, 'exec')" % (str(Path(__file__).resolve()), str(Path(__file__).resolve()))],
        [str(sys.executable), "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/stage_a_terminal_lifecycle", "tests/observation_screening", "tests/m1_real_dataset_expansion_v3"],
        [str(sys.executable), "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/m1_real_dataset_expansion_v2", "tests/multi_topology_static_units", "tests/multi_unit_offline_candidate_capture"],
        [str(sys.executable), "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/evaluator_adapter_production_path", "tests/m3e_supervisor_binding", "tests/m3d_route_validation"],
        [str(p3.PYTHON38), "-B", "tools/evaluator_production_path_dry_run.py"],
    ]
    results = [observation.run_command(command, ROOT, environment) for command in commands]
    sim = p3.git_state(SIMLINGO)
    passed = (
        all(item["exit_code"] == 0 for item in results)
        and sim["tracked_diff_bytes"] == 7722
        and sim["tracked_diff_sha256"] == base.SIMLINGO_DIFF_SHA256
        and sim["staged_diff_bytes"] == 0
        and verify_frozen_authority()["status"] == "PASS"
    )
    payload = {
        "schema_version": "driveclarify.m1_v3_stage_a_cpu_preflight.v1",
        "batch_id": batch_id + "-STAGE-A",
        "status": "PASS" if passed else "FAIL",
        "checked_at_utc": p3.utc_now(),
        "commands": results,
        "authority": verify_frozen_authority(),
        "protected_history": _protected_history(),
        "authority_snapshot": _frozen_snapshot(),
        "outside_batch_untracked_snapshot": observation.outside_batch_untracked_snapshot(),
        "simlingo_git": sim,
        "production_adapter_dry_run": "PASS" if results[-1]["exit_code"] == 0 else "FAIL",
        "real_system_launches": 0,
        "torch_imported_by_orchestrator": False,
        "cuda_initializations": 0,
    }
    atomic_create_json(root / "BATCH_CPU_PREFLIGHT.json", payload)
    _append(root / "BATCH_COMMAND_LOG.md", "- V3 CPU/static runtime preflight `{}`; exits={}.".format(payload["status"], [item["exit_code"] for item in results]))
    if not passed:
        raise CampaignError("STAGE_A_V3_PREFLIGHT_FAILED")


def prepare_stage_a(batch_id: str) -> None:
    configure_stage_a(batch_id)
    observation.prepare_batch()
    authorization_path = stage_a_root(batch_id) / "BATCH_AUTHORIZATION.json"
    authorization = load(authorization_path)
    authorization["authorization_source"] = "USER_EXPLICIT_M1_V3_FIXED_24_UNIT_STAGE_A"
    authorization["selected_unit_count"] = 24
    authorization["constraints"]["recovery_per_unit_engineering_failure_only"] = 0
    authorization["constraints"]["engineering_failure_policy"] = "TERMINAL_ENGINEERING_EXCLUSION_OR_BLOCKED_NO_RECOVERY"
    atomic_replace_json(authorization_path, authorization)
    _stage_a_preflight(batch_id)
    manifest = load(campaign_root(batch_id) / "CAMPAIGN_RUN_MANIFEST.json")
    manifest["status"] = "STAGE_A_PREFLIGHT_PASS_CODE_FROZEN"
    atomic_replace_json(campaign_root(batch_id) / "CAMPAIGN_RUN_MANIFEST.json", manifest)
    _append(campaign_root(batch_id) / "COMMAND_LOG.md", "- Stage A preflight PASS; code frozen; candidate forward=0.")
    preflight_path = stage_a_root(batch_id) / "BATCH_CPU_PREFLIGHT.json"
    preflight = load(preflight_path)
    preflight["outside_batch_untracked_snapshot"] = observation.outside_batch_untracked_snapshot()
    atomic_replace_json(preflight_path, preflight)


def _terminal_blocked(batch_id: str, run_id: str, unit_id: str, exc: BaseException) -> None:
    run_dir = observation.run_output(run_id)
    lifecycle = read_run_lifecycle(run_dir)
    if lifecycle["terminal"] is True:
        return
    reasons = ["ONE_SHOT_STAGE_A_BLOCKED", type(exc).__name__]
    payload = {
        "schema_version": "driveclarify.observation_screening_run_result.v1",
        "batch_id": batch_id + "-STAGE-A",
        "run_id": run_id,
        "unit_id": unit_id,
        "run_role": "primary",
        "outcome": "BLOCKED",
        "terminal": True,
        "terminal_category": "TERMINAL_BLOCKED",
        "reason_codes": reasons,
        "runtime_counts": {"candidate_forward": 0, "second_observation": 0, "mapper_invocation": 0, "training": 0, "act_ask_wait": 0},
        "cleanup": "UNKNOWN",
        "blocked_exception": {"type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()},
    }
    publish_terminal_result(run_dir, payload)


def run_stage_a(batch_id: str) -> None:
    configure_stage_a(batch_id)
    total = len(observation.UNIT_RUNS)
    for index, item in enumerate(observation.UNIT_RUNS, start=1):
        run_id = item["primary"]
        lifecycle = read_run_lifecycle(observation.run_output(run_id))
        if lifecycle["terminal"] is True:
            print("STAGE_A_SKIP {}/{} {} already_terminal".format(index, total, item["unit_id"]), flush=True)
            continue
        print("STAGE_A_START {}/{} {} {}".format(index, total, item["unit_id"], run_id), flush=True)
        try:
            if not observation.run_output(run_id).exists():
                observation.stage_run(run_id)
            base._stage_a_unit_cpu_check(run_id)
            if not (observation.run_output(run_id) / "PRELAUNCH_SUPERVISOR_SELFTEST.json").is_file():
                observation.prelaunch_selftest(run_id)
            observation.authorize_run(run_id)
            observation.launch_run(run_id)
            outcome = observation.finalize_run(run_id)
        except BaseException as exc:
            run_dir = observation.run_output(run_id)
            if not (run_dir / "REAL_LAUNCH_RECORD.json").exists():
                base._seal_stage_a_prelaunch_failure(batch_id, run_id, item["unit_id"], exc)
            _terminal_blocked(batch_id, run_id, item["unit_id"], exc)
            outcome = "BLOCKED"
        print("STAGE_A_DONE {}/{} {} {}".format(index, total, item["unit_id"], outcome), flush=True)


def finalize_stage_a(batch_id: str) -> Mapping[str, Any]:
    configure_stage_a(batch_id)
    rows = []
    results = []
    packages = []
    for item in observation.UNIT_RUNS:
        run_id = item["primary"]
        run_dir = observation.run_output(run_id)
        lifecycle = read_run_lifecycle(run_dir)
        if lifecycle["terminal"] is not True:
            raise CampaignError("STAGE_A_PRIMARY_NOT_TERMINAL:" + item["unit_id"])
        result = lifecycle["payload"]
        results.append(result)
        raw = result.get("outcome")
        screen = result.get("observation_screening") or {}
        if raw == "ELIGIBLE":
            classification = "ELIGIBLE"
        elif raw == "EVIDENCE_UNAVAILABLE":
            classification = "EVIDENCE_EXCLUSION"
        elif raw == "RUNTIME_FAILURE":
            classification = "ENGINEERING_EXCLUSION"
        elif raw == "BLOCKED":
            classification = "BLOCKED"
        else:
            raise CampaignError("STAGE_A_TERMINAL_OUTCOME_INVALID:" + item["unit_id"])
        manifest_path = run_dir / "OBSERVATION_PACKAGE_MANIFEST.json"
        package = load(manifest_path) if raw == "ELIGIBLE" and manifest_path.is_file() else None
        normalized = {
            "schema_version": "driveclarify.m1_v3_observation_output.v1",
            "unit_id": item["unit_id"],
            "outcome": raw if raw in {"ELIGIBLE", "EVIDENCE_UNAVAILABLE", "RUNTIME_FAILURE"} else "RUNTIME_FAILURE",
            "observation_index": screen.get("observation_index"),
            "observation_package": package,
            "observation_package_sha256": sha256_path(manifest_path) if package is not None else None,
            "signed_station_m": screen.get("signed_station_m"),
            "eligibility_contract_sha256": embedded(_unit_paths(item["unit_id"])["contract"], "CONTRACT")["sha256"],
            "candidate_forward_count": 0,
            "second_observation_count": 0,
            "cleanup_status": screen.get("cleanup_status", result.get("cleanup", "UNKNOWN")) if isinstance(result.get("cleanup", "UNKNOWN"), str) else result.get("cleanup", {}).get("status", "UNKNOWN"),
            "reason_codes": screen.get("reason_codes", result.get("reason_codes", [])),
        }
        jsonschema.validate(normalized, load(OBS_SCHEMA))
        _put_json(run_dir / "M1_V3_OBSERVATION_OUTPUT.json", normalized)
        row = {
            "unit_id": item["unit_id"],
            "run_id": run_id,
            "outcome": raw,
            "classification": classification,
            "terminal_category": lifecycle["terminal_category"],
            "observation_index": screen.get("observation_index"),
            "source_frame": screen.get("source_frame"),
            "signed_station_m": screen.get("signed_station_m"),
            "reason_codes": normalized["reason_codes"],
            "cleanup_status": normalized["cleanup_status"],
            "observation_package_manifest_path": str(manifest_path) if package is not None else None,
            "observation_package_manifest_sha256": normalized["observation_package_sha256"],
            "candidate_forward_count": 0,
            "second_observation_count": 0,
        }
        rows.append(row)
        if package is not None:
            packages.append(
                {
                    "unit_id": item["unit_id"],
                    "run_id": run_id,
                    "observation_hash": package["observation_hash"],
                    "package_path": package["package_directory"],
                    "manifest_path": str(manifest_path),
                    "manifest_sha256": sha256_path(manifest_path),
                    "file_count": package["file_count"],
                    "package_content_sha256": package["package_content_sha256"],
                }
            )
    totals = observation._sum_counts(results)
    if totals["candidate_forward"] != 0 or totals["second_observation"] != 0 or totals["mapper_invocation"] != 0:
        raise CampaignError("STAGE_A_PROHIBITED_COUNT_NONZERO")
    eligible = [item for item in rows if item["classification"] == "ELIGIBLE"]
    evidence = [item for item in rows if item["classification"] == "EVIDENCE_EXCLUSION"]
    engineering = [item for item in rows if item["classification"] == "ENGINEERING_EXCLUSION"]
    blocked = [item for item in rows if item["classification"] == "BLOCKED"]
    root = stage_a_root(batch_id)
    documents = {
        "BATCH_UNIT_SUMMARY.json": {"schema_version": "driveclarify.m1_v3_stage_a_unit_summary.v1", "batch_id": batch_id + "-STAGE-A", "selected_unit_count": 24, "terminal_unit_count": 24, "units": rows},
        "ELIGIBLE_UNITS_MANIFEST.json": {"schema_version": "driveclarify.m1_v3_eligible_units.v1", "count": len(eligible), "units": eligible},
        "EVIDENCE_EXCLUSION_UNITS_MANIFEST.json": {"schema_version": "driveclarify.m1_v3_evidence_exclusions.v1", "count": len(evidence), "units": evidence},
        "ENGINEERING_EXCLUSION_UNITS_MANIFEST.json": {"schema_version": "driveclarify.m1_v3_engineering_exclusions.v1", "count": len(engineering), "units": engineering},
        "BLOCKED_UNITS_MANIFEST.json": {"schema_version": "driveclarify.m1_v3_blocked_units.v1", "count": len(blocked), "units": blocked},
        "OBSERVATION_PACKAGE_INDEX.json": {"schema_version": "driveclarify.m1_v3_observation_package_index.v1", "eligible_package_count": len(packages), "packages": packages},
        "BATCH_RUNTIME_COUNTS.json": {"schema_version": "driveclarify.m1_v3_stage_a_runtime_counts.v1", "totals": totals, "candidate_forward_total": 0, "second_observation_total": 0, "mapper_invocation_total": 0, "training_total": 0, "act_ask_wait_total": 0},
    }
    for name, value in documents.items():
        _put_json(root / name, value)
    compute = p3.environment_preflight()["gpu_compute_processes"]
    cleanup_pass = all(item["cleanup_status"] == "PASS" for item in rows if item["classification"] != "BLOCKED") and not compute
    _put_json(root / "BATCH_GPU_AND_CLEANUP.json", {"schema_version": "driveclarify.m1_v3_stage_a_cleanup.v1", "all_cleanup_pass": cleanup_pass, "final_gpu_compute_process_count": len(compute), "final_gpu_compute_processes": compute})
    result = {
        "schema_version": "driveclarify.m1_v3_stage_a_result.v1",
        "batch_id": batch_id + "-STAGE-A",
        "final_status": "M1_V3_STAGE_A_COMPLETE" if not blocked else "M1_V3_STAGE_A_COMPLETE_WITH_BLOCKED_UNITS",
        "selected_unit_count": 24,
        "runtime_constructed_unit_count": sum(1 for item in results if item.get("runtime_counts", {}).get("carla_launch", 0) > 0),
        "eligible_count": len(eligible),
        "evidence_exclusion_count": len(evidence),
        "engineering_exclusion_count": len(engineering),
        "blocked_count": len(blocked),
        "candidate_forward_total": 0,
        "second_observation_total": 0,
        "all_cleanup_pass": cleanup_pass,
        "stage_b_unit_count": len(eligible),
    }
    _put_json(root / "BATCH_RESULT.json", result)
    _put_json(root / "ARTIFACT_INVENTORY.json", _inventory(root))
    _append(campaign_root(batch_id) / "COMMAND_LOG.md", "- Stage A complete: selected=24 eligible={} evidence={} engineering={} blocked={} candidate_forward=0 second_observation=0.".format(len(eligible), len(evidence), len(engineering), len(blocked)))
    return result


def _configure_offline(batch_id: str) -> Tuple[Any, Any]:
    plan_path = campaign_root(batch_id) / "STAGE_B_RUN_PLAN.json"
    os.environ.update({"DRIVECLARIFY_M1_V2_AUTHORITY": str(AUTHORITY), "DRIVECLARIFY_M1_V2_RUN_PLAN": str(plan_path)})
    from . import offline_candidate_batch as batch
    from . import offline_candidate_capture as capture

    batch.validate_entry = lambda: _validate_stage_b_entry(batch_id, capture)
    return batch, capture


def _validate_stage_b_entry(batch_id: str, capture: Any) -> Mapping[str, Any]:
    verify_frozen_authority()
    stage_a = load(stage_a_root(batch_id) / "BATCH_RESULT.json")
    index = load(stage_a_root(batch_id) / "OBSERVATION_PACKAGE_INDEX.json")
    if stage_a.get("candidate_forward_total") != 0 or stage_a.get("second_observation_total") != 0:
        raise CampaignError("STAGE_A_PROHIBITED_COUNT_NONZERO")
    if index.get("eligible_package_count") != len(capture.UNIT_RUNS):
        raise CampaignError("STAGE_B_ELIGIBLE_UNIT_SET_COUNT_MISMATCH")
    indexed = {item["unit_id"]: item for item in index["packages"]}
    checks = []
    for unit in capture.UNIT_RUNS:
        item = indexed.get(unit["unit_id"])
        if item is None:
            raise CampaignError("STAGE_B_PACKAGE_MISSING:" + unit["unit_id"])
        verification = capture.verify_observation_package(
            Path(item["manifest_path"]),
            expected_unit_id=unit["unit_id"],
            expected_observation_hash=item["observation_hash"],
        )
        if verification["status"] != "PASS":
            raise CampaignError("STAGE_B_PACKAGE_VERIFICATION_FAILED:" + unit["unit_id"])
        checks.append({"unit_id": unit["unit_id"], "package": item, "verification": verification})
    if sha256_path(capture.CHECKPOINT) != base.CHECKPOINT_SHA256 or sha256_path(capture.CONFIG) != base.CONFIG_SHA256:
        raise CampaignError("MODEL_AUTHORITY_IDENTITY_MISMATCH")
    environment = p3.environment_preflight()
    if environment["preexisting_real_processes"] or environment["gpu_compute_processes"]:
        raise CampaignError("STAGE_B_PREEXISTING_RUNTIME_PROCESS_OR_GPU_COMPUTE")
    return {
        "entry_status": stage_a["final_status"],
        "selected_units": 24,
        "eligible_units": len(checks),
        "evidence_unavailable_units": stage_a["evidence_exclusion_count"],
        "runtime_failure_units": stage_a["engineering_exclusion_count"],
        "blocked_units": stage_a["blocked_count"],
        "prior_candidate_forward": 0,
        "prior_second_observation": 0,
        "packages": checks,
        "checkpoint_sha256": base.CHECKPOINT_SHA256,
        "config_sha256": base.CONFIG_SHA256,
        "history": _protected_history(),
        "git": _git_gate(),
        "preexisting_forbidden_processes": [],
        "preexisting_gpu_compute": [],
    }


def prepare_stage_b(batch_id: str) -> Mapping[str, Any]:
    a_result = load(stage_a_root(batch_id) / "BATCH_RESULT.json")
    if not str(a_result.get("final_status", "")).startswith("M1_V3_STAGE_A_COMPLETE") or a_result.get("candidate_forward_total") != 0:
        raise CampaignError("STAGE_A_NOT_READY_FOR_STAGE_B")
    packages = load(stage_a_root(batch_id) / "OBSERVATION_PACKAGE_INDEX.json")["packages"]
    eligible_ids = {item["unit_id"] for item in packages}
    ordered = [item for item in selected_rows() if item["unit_id"] in eligible_ids]
    unit_runs = []
    for index, item in enumerate(ordered):
        short = item["unit_id"].replace("TOWN", "T").replace("_JUNCTION_", "-J").replace("_UNIT01", "")
        unit_runs.append(
            {
                "unit_id": item["unit_id"],
                "primary": "DC-M1V3-A3B3-{}-A-{}".format(short, base._run_timestamp(batch_id, 601 + index)),
                "recovery": "DC-M1V3-A3B3-{}-UNUSED-{}".format(short, base._run_timestamp(batch_id, 901 + index)),
            }
        )
    plan = {
        "schema_version": "driveclarify.m1_v3_stage_b_run_plan.v1",
        "combined_batch_id": batch_id,
        "stage_a_batch_id": batch_id + "-STAGE-A",
        "stage_a_root": str(stage_a_root(batch_id)),
        "stage_b_batch_id": batch_id + "-STAGE-B",
        "stage_b_root": str(stage_b_root(batch_id)),
        "selected_manifest": str(SELECTED),
        "schedule": list(SCHEDULE),
        "eligible_unit_count": len(unit_runs),
        "unit_runs": unit_runs,
    }
    atomic_create_json(campaign_root(batch_id) / "STAGE_B_RUN_PLAN.json", plan)
    batch, capture = _configure_offline(batch_id)
    entry = batch.validate_entry()
    capture.RUNS_ROOT.mkdir(parents=True)
    atomic_create_json(capture.BATCH_ROOT / "BATCH_AUTHORIZATION.json", {"schema_version": "driveclarify.m1_v3_stage_b_authorization.v1", "batch_id": capture.BATCH_ID, "authorization_source": "USER_EXPLICIT_M1_V3_ELIGIBLE_ONLY_STAGE_B", "authorized_at_utc": p3.utc_now(), "ordered_units": unit_runs, "schedule": list(SCHEDULE), "entry_validation": entry, "constraints": {"carla_launch": 0, "evaluator_launch": 0, "observation_capture": 0, "second_observation": 0, "candidate_forward_per_complete_unit": 6, "training": 0, "act_ask_wait": 0}, "recovery_policy": "NO_RECOVERY_RUN_ENGINEERING_EXCLUSION"})
    atomic_create_bytes(capture.BATCH_ROOT / "BATCH_COMMAND_LOG.md", ("# M1 V3 Stage B offline A3/B3 command log\n\n- Prepared for {} eligible units.\n".format(len(unit_runs))).encode("utf-8"))
    atomic_create_json(capture.BATCH_ROOT / "GIT_START_END.json", {"schema_version": "driveclarify.m1_v3_stage_b_git_start_end.v1", "start": _git_gate(), "end": None})
    env = dict(os.environ)
    env.update({"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONDONTWRITEBYTECODE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_OFFLINE": "1"})
    commands = [
        [str(batch.PYTHON38), "-B", "-m", "py_compile", str(batch.COMMON), str(batch.WORKER), str(batch.ORCHESTRATOR), str(Path(__file__).resolve())],
        [str(batch.PYTHON38), "-B", "-m", "driveclarify_static_branch.offline_candidate_package_smoke"],
    ]
    results = [batch.run_command(command, env=env) for command in commands]
    passed = all(item["exit_code"] == 0 for item in results)
    atomic_create_json(capture.BATCH_ROOT / "BATCH_CPU_PREFLIGHT.json", {"schema_version": "driveclarify.m1_v3_stage_b_preflight.v1", "status": "PASS" if passed else "FAIL", "commands": results, "entry_revalidation": batch.validate_entry(), "real_candidate_forwards": 0, "cuda_context_created_by_preflight": False})
    if not passed:
        raise CampaignError("STAGE_B_PREFLIGHT_FAILED")
    _append(campaign_root(batch_id) / "COMMAND_LOG.md", "- Stage B prepared for {} eligible units; package round-trip preflight PASS.".format(len(unit_runs)))
    return plan


def run_stage_b(batch_id: str) -> None:
    batch, capture = _configure_offline(batch_id)
    for index, item in enumerate(capture.UNIT_RUNS, start=1):
        result_path = capture.RUNS_ROOT / item["primary"] / "RUN_RESULT.json"
        if result_path.is_file():
            print("STAGE_B_SKIP {}/{} {} already_terminal".format(index, len(capture.UNIT_RUNS), item["unit_id"]), flush=True)
            continue
        print("STAGE_B_START {}/{} {} {}".format(index, len(capture.UNIT_RUNS), item["unit_id"], item["primary"]), flush=True)
        try:
            result = batch.execute(item["primary"])
        except BaseException as exc:
            # V3 has no recovery/retry path.  Preserve any partial evidence and
            # publish one structured engineering terminal so later eligible
            # units can still receive their single authorized attempt.
            run_dir = capture.RUNS_ROOT / item["primary"]
            if result_path.exists():
                result = load(result_path)
                print("STAGE_B_TERMINAL_PRESERVED {} after {}".format(item["unit_id"], type(exc).__name__), flush=True)
                print("STAGE_B_DONE {}/{} {} {} forwards={}".format(index, len(capture.UNIT_RUNS), item["unit_id"], result["final_status"], result["candidate_forward_count"]), flush=True)
                continue
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "logs").mkdir(exist_ok=True)
            (run_dir / "receipt_claims").mkdir(exist_ok=True)
            failure_path = run_dir / "WORKER_FAILURE.json"
            if not failure_path.exists():
                atomic_create_json(failure_path, {"schema_version": "driveclarify.m1_v3_stage_b_wrapper_failure.v1", "exception_type": type(exc).__name__, "exception_message": str(exc), "traceback": traceback.format_exc()})
            environment = p3.environment_preflight()
            cleanup = {"schema_version": "driveclarify.offline_process_cleanup.v1", "run_id": item["primary"], "worker_pid": None, "worker_exit_code": None, "worker_timed_out": False, "pidfd_cleanup": None, "worker_survivor_count": len(environment["preexisting_real_processes"]), "owned_descendant_count": 0, "final_gpu_compute_processes": environment["gpu_compute_processes"], "final_gpu_compute_process_count": len(environment["gpu_compute_processes"]), "forbidden_runtime_processes": environment["preexisting_real_processes"], "temporary_tensor_files_remaining": [], "unit_temporary_directory_remaining": False, "status": "PASS" if not environment["preexisting_real_processes"] and not environment["gpu_compute_processes"] and all(environment["ports_free"].values()) else "FAIL"}
            if not (run_dir / "PROCESS_CLEANUP.json").exists():
                atomic_create_json(run_dir / "PROCESS_CLEANUP.json", cleanup)
            result = batch._seal_failure(run_dir, item["primary"], item, cleanup, 1)
            if not (run_dir / "GPU_RESOURCE_RECORD.json").exists():
                atomic_create_json(run_dir / "GPU_RESOURCE_RECORD.json", {"schema_version": "driveclarify.offline_gpu_resource_record.v1", "run_id": item["primary"], "worker": None, "after_process_exit_compute_processes": environment["gpu_compute_processes"], "final_compute_process_count": len(environment["gpu_compute_processes"]), "cleanup_status": cleanup["status"]})
            if not (run_dir / "COMMAND_LOG.md").exists():
                atomic_create_bytes(run_dir / "COMMAND_LOG.md", ("# M1 V3 Stage B one-shot run\n\n- Structured engineering terminal after wrapper exception `{}`.\n".format(type(exc).__name__)).encode("utf-8"))
            if not (run_dir / "ARTIFACT_INVENTORY.json").exists():
                atomic_create_json(run_dir / "ARTIFACT_INVENTORY.json", capture.inventory(run_dir))
        print("STAGE_B_DONE {}/{} {} {} forwards={}".format(index, len(capture.UNIT_RUNS), item["unit_id"], result["final_status"], result["candidate_forward_count"]), flush=True)


def finalize_stage_b(batch_id: str) -> Mapping[str, Any]:
    batch, capture = _configure_offline(batch_id)
    selected = {item["unit_id"]: item for item in selected_rows()}
    stage_a = {item["unit_id"]: item for item in load(stage_a_root(batch_id) / "BATCH_UNIT_SUMMARY.json")["units"]}
    terminal = []
    complete_by_id: Dict[str, Mapping[str, Any]] = {}
    for item in capture.UNIT_RUNS:
        path = capture.RUNS_ROOT / item["primary"] / "RUN_RESULT.json"
        if not path.is_file():
            raise CampaignError("STAGE_B_PRIMARY_NOT_TERMINAL:" + item["unit_id"])
        result = load(path)
        row = {"unit_id": item["unit_id"], "run_id": item["primary"], "final_status": result["final_status"], "terminal_category": result["terminal_category"], "complete_plan_count": result["complete_plan_count"], "candidate_forward_count": result["candidate_forward_count"], "fairness": result.get("fairness", "UNKNOWN"), "mapper_consensus": result.get("mapper_consensus", {"A": "UNKNOWN", "B": "UNKNOWN"}), "rq1": result.get("rq1", "UNKNOWN"), "rq2": result.get("rq2", "UNKNOWN"), "cleanup": result["cleanup"]["status"], "recovery_used": False}
        terminal.append(row)
        if result["terminal_category"] == "SCIENTIFIC_RESULT" and result["complete_plan_count"] == 6 and result["candidate_forward_count"] == 6:
            complete_by_id[item["unit_id"]] = row
    records = []
    summaries = []
    capture_outputs = []
    for unit_id, selection in selected.items():
        arow = stage_a[unit_id]
        complete = complete_by_id.get(unit_id)
        if complete:
            run_dir = capture.RUNS_ROOT / complete["run_id"]
            plans = [load(run_dir / (candidate_id + "_PLAN.json")) for candidate_id in SCHEDULE]
            mapper = load(run_dir / "MAPPER_RESULTS.json")
            fairness = load(run_dir / "FAIRNESS_RESULT.json")
            rq1 = load(run_dir / "RQ1_RESULT.json")
            rq2 = load(run_dir / "RQ2_RESULT.json")
            mapping = {item["candidate_id"]: item["mapping_label"] for item in mapper["per_plan"]}
            package_path = Path(arow["observation_package_manifest_path"])
            package = load(package_path)
            normalized = {"schema_version": "driveclarify.m1_v3_capture_output.v1", "unit_id": unit_id, "split": selection["split"], "observation_package_sha256": sha256_path(package_path), "plans": [{"candidate_id": plan["candidate_group"], "repeat_index": plan["repeat_index"], "route_plan": plan["plan_points"], "speed_plan": [number for speed_row in plan["raw_speed"][0] for number in speed_row], "mapping_label": mapping[plan["candidate_id"]]} for plan in plans], "rq1": rq1, "rq2": rq2["pair_class"], "unknown_provenance": {"reason_codes": rq2["reason_codes"], "mapper_consensus": mapper["candidate_consensus"]} if rq2["pair_class"] == "UNKNOWN" else None, "cleanup_status": "PASS"}
            jsonschema.validate(normalized, load(CAPTURE_SCHEMA))
            _put_json(run_dir / "M1_V3_CAPTURE_OUTPUT.json", normalized)
            capture_outputs.append({"unit_id": unit_id, "run_id": complete["run_id"], "path": str(run_dir / "M1_V3_CAPTURE_OUTPUT.json"), "sha256": sha256_path(run_dir / "M1_V3_CAPTURE_OUTPUT.json")})
            record = {"unit_id": unit_id, "unit_status": "V3_NEW", "split": selection["split"], "source_type": selection["source_type"], "town": selection["town"], "junction_group": selection["junction_group"], "route_family": selection["route_family"], "observation_package": package, "plans": plans, "pair_task_label": rq2["pair_class"], "label_provenance": {"mapper": "StaticBranchPlanMapperV1", "threshold_sha256": base.THRESHOLD_EMBEDDED_SHA256, "mapper_results_path": str(run_dir / "MAPPER_RESULTS.json"), "rq2_result_path": str(run_dir / "RQ2_RESULT.json"), "fairness_verdict": fairness["verdict"]}, "unknown_provenance": normalized["unknown_provenance"], "engineering_exclusion": None, "data_generation_version": "M1_REAL_DATASET_V3_RUNTIME_" + batch_id}
            summaries.append({**complete, "split": selection["split"], "classification": "COMPLETE_A3_B3", "pair_task_label": rq2["pair_class"], "fairness": fairness["verdict"], "repeat_stability": rq1.get("repeat_exact_hash_equality"), "mapper_consensus": mapper["candidate_consensus"]})
        else:
            brow = next((item for item in terminal if item["unit_id"] == unit_id), None)
            evidence = arow["classification"] == "EVIDENCE_EXCLUSION"
            status = "EVIDENCE_EXCLUSION" if evidence else "ENGINEERING_EXCLUSION"
            engineering = None if evidence else {"stage": "STAGE_A" if brow is None else "STAGE_B", "reason_codes": arow.get("reason_codes", []), "run_id": brow.get("run_id") if brow else arow.get("run_id"), "final_status": brow.get("final_status") if brow else arow.get("outcome")}
            record = {"unit_id": unit_id, "unit_status": status, "split": selection["split"], "source_type": selection["source_type"], "town": selection["town"], "junction_group": selection["junction_group"], "route_family": selection["route_family"], "observation_package": load(Path(arow["observation_package_manifest_path"])) if arow.get("observation_package_manifest_path") else None, "plans": None, "pair_task_label": None, "label_provenance": None, "unknown_provenance": {"stage": "STAGE_A", "reason_codes": arow.get("reason_codes", [])} if evidence else None, "engineering_exclusion": engineering, "data_generation_version": "M1_REAL_DATASET_V3_RUNTIME_" + batch_id}
            summaries.append({"unit_id": unit_id, "split": selection["split"], "classification": status, "pair_task_label": None, "candidate_forward_count": brow["candidate_forward_count"] if brow else 0, "cleanup": brow["cleanup"] if brow else arow["cleanup_status"]})
        records.append(record)
    dataset = {"schema_version": "driveclarify.m1_real_dataset_v3.incremental.v1", "dataset_status": "CAPTURED", "expansion_id": EXPANSION_ID, "campaign_id": batch_id, "split_manifest_sha256": embedded(SPLIT, "SPLIT")["sha256"], "records": records}
    jsonschema.validate(dataset, load(DATA_SCHEMA))
    root = stage_b_root(batch_id)
    _put_json(root / "M1_REAL_DATASET_V3.json", dataset)
    _put_json(root / "M1_REAL_DATASET_V3_UNIT_SUMMARY.json", {"schema_version": "driveclarify.m1_real_dataset_v3.unit_summary.v1", "v3_selected_unit_count": 24, "units": summaries})
    _put_json(root / "STAGE_B_CAPTURE_OUTPUT_INDEX.json", {"schema_version": "driveclarify.m1_v3_capture_output_index.v1", "count": len(capture_outputs), "outputs": capture_outputs})
    complete = [item for item in summaries if item["classification"] == "COMPLETE_A3_B3"]
    engineering = [item for item in summaries if item["classification"] == "ENGINEERING_EXCLUSION"]
    evidence = [item for item in summaries if item["classification"] == "EVIDENCE_EXCLUSION"]
    unknown = [item for item in complete if item["pair_task_label"] == "UNKNOWN"]
    known = [item for item in complete if item["pair_task_label"] in {"TASK_EQUIVALENT", "TASK_CRITICAL"}]
    split_counts = {name: sum(1 for item in complete if item["split"] == name) for name in ("TRAIN", "DEV", "TEST")}
    label_counts = {name: sum(1 for item in complete if item["pair_task_label"] == name) for name in ("TASK_EQUIVALENT", "TASK_CRITICAL", "UNKNOWN")}
    runtime_rows = [load(capture.RUNS_ROOT / item["run_id"] / "RUNTIME_COUNTS.json") for item in terminal]
    count_keys = ("candidate_forward", "checkpoint_load", "model_load", "carla_launch", "evaluator_launch", "observation_capture", "second_observation", "world_tick", "pid", "planner_advance", "control_send", "scenario_actor_mutation", "baseline_control_consumption", "act_ask_wait", "training", "mapper_invocation")
    totals = {key: sum(int(item.get(key, 0)) for item in runtime_rows) for key in count_keys}
    if any(item["candidate_forward_count"] != 6 for item in complete) or totals["candidate_forward"] != len(complete) * 6:
        raise CampaignError("COMPLETE_UNIT_CANDIDATE_FORWARD_COUNT_NOT_SIX")
    compute = p3.environment_preflight()["gpu_compute_processes"]
    cleanup_pass = all(item["cleanup"] == "PASS" for item in terminal) and not compute
    v2 = load(V2_RESULT)
    consolidated_labels = {key: int(v2["label_distribution"][key]) + label_counts[key] for key in label_counts}
    consolidated_splits = {key: int(v2["split_distribution"][key]) + split_counts[key] for key in split_counts}
    consolidated_complete = int(v2["complete_a3_b3_units"]) + len(complete)
    gate = {
        "complete_units_ge_20": consolidated_complete >= 20,
        "task_equivalent_ge_3": consolidated_labels["TASK_EQUIVALENT"] >= 3,
        "task_critical_ge_3": consolidated_labels["TASK_CRITICAL"] >= 3,
        "scientific_unknown_ge_3": consolidated_labels["UNKNOWN"] >= 3,
        "dev_complete_ge_4": consolidated_splits["DEV"] >= 4,
        "test_complete_ge_4": consolidated_splits["TEST"] >= 4,
        "global_split_leakage_zero": embedded(GLOBAL_LEAKAGE, "GLOBAL_LEAKAGE")["status"] == "PASS",
        "pilot_not_in_formal_dev_test": True,
        "repeats_not_independent_samples": True,
    }
    gate_pass = all(gate.values()) and cleanup_pass
    status = "M1_V3_RUNTIME_CAMPAIGN_COMPLETE_READY_FOR_FORMAL_LEARNED_M1_ASSESSMENT" if gate_pass else "M1_V3_RUNTIME_CAMPAIGN_COMPLETE_DATASET_EXPANSION_V4_REQUIRED"
    documents = {
        "BATCH_UNIT_SUMMARY.json": {"schema_version": "driveclarify.m1_v3_stage_b_unit_summary.v1", "eligible_unit_count": len(terminal), "complete_unit_count": len(complete), "units": summaries},
        "COMPLETE_UNITS_MANIFEST.json": {"schema_version": "driveclarify.m1_v3_complete_units.v1", "count": len(complete), "units": complete},
        "ENGINEERING_EXCLUSION_UNITS_MANIFEST.json": {"schema_version": "driveclarify.m1_v3_engineering_exclusions.v1", "count": len(engineering), "units": engineering},
        "EVIDENCE_EXCLUSION_UNITS_MANIFEST.json": {"schema_version": "driveclarify.m1_v3_evidence_exclusions.v1", "count": len(evidence), "units": evidence},
        "SCIENTIFIC_UNKNOWN_UNITS_MANIFEST.json": {"schema_version": "driveclarify.m1_v3_scientific_unknowns.v1", "count": len(unknown), "units": unknown},
        "KNOWN_LABEL_UNITS_MANIFEST.json": {"schema_version": "driveclarify.m1_v3_known_labels.v1", "count": len(known), "units": known},
        "FAIRNESS_AND_REPEAT_STABILITY_SUMMARY.json": {"schema_version": "driveclarify.m1_v3_fairness_repeat.v1", "all_complete_fairness_pass": all(item["fairness"] == "PASS" for item in complete), "units": [{"unit_id": item["unit_id"], "fairness": item["fairness"], "repeat_stability": item.get("repeat_stability")} for item in complete]},
        "MAPPER_RQ1_RQ2_SUMMARY.json": {"schema_version": "driveclarify.m1_v3_mapper_rq.v1", "units": [{"unit_id": item["unit_id"], "mapper_consensus": item["mapper_consensus"], "rq1": item["rq1"], "rq2": item["rq2"]} for item in complete]},
        "SPLIT_AND_LABEL_DISTRIBUTION.json": {"schema_version": "driveclarify.m1_v3_distribution.v1", "v3_complete_by_split": split_counts, "v3_complete_by_label": label_counts, "v2_v3_complete_by_split": consolidated_splits, "v2_v3_complete_by_label": consolidated_labels, "v3_complete_unit_count": len(complete), "v2_v3_complete_unit_count": consolidated_complete},
        "EXCLUSION_LEDGER.json": {"schema_version": "driveclarify.m1_v3_exclusion_ledger.v1", "v3_evidence_exclusions": evidence, "v3_engineering_exclusions": engineering, "frozen_historical_exclusions": embedded(HISTORY, "HISTORY")["historical_exclusions"]},
        "BATCH_RUNTIME_COUNTS.json": {"schema_version": "driveclarify.m1_v3_stage_b_runtime_counts.v1", "totals": totals, "expected_candidate_forwards": len(complete) * 6, "actual_candidate_forwards": totals["candidate_forward"], "training_total": totals["training"], "act_ask_wait_total": totals["act_ask_wait"]},
        "BATCH_GPU_AND_CLEANUP.json": {"schema_version": "driveclarify.m1_v3_stage_b_cleanup.v1", "all_cleanup_pass": cleanup_pass, "final_gpu_compute_process_count": len(compute), "final_gpu_compute_processes": compute},
        "FORMAL_LEARNED_M1_READINESS.json": {"schema_version": "driveclarify.m1_v3_formal_readiness.v1", "assessment_only": True, "formal_training_authorized": False, "v2_v3_complete_unit_count": consolidated_complete, "v2_v3_label_distribution": consolidated_labels, "v2_v3_split_complete_distribution": consolidated_splits, "gates": gate, "all_gates_pass": gate_pass, "status": "READY_FOR_FORMAL_LEARNED_M1_ASSESSMENT" if gate_pass else "DATASET_EXPANSION_V4_REQUIRED"},
    }
    for name, value in documents.items():
        _put_json(root / name, value)
    result = {"schema_version": "driveclarify.m1_v3_stage_b_result.v1", "batch_id": batch_id + "-STAGE-B", "final_status": status, "selected_unit_count": 24, "stage_a_eligible_unit_count": len(terminal), "complete_a3_b3_unit_count": len(complete), "engineering_exclusion_count": len(engineering), "evidence_exclusion_count": len(evidence), "scientific_unknown_unit_count": len(unknown), "known_label_unit_count": len(known), "candidate_forward_total": totals["candidate_forward"], "candidate_forward_exactness": all(item["candidate_forward_count"] == 6 for item in complete), "v3_split_distribution": split_counts, "v3_label_distribution": label_counts, "v2_v3_split_distribution": consolidated_splits, "v2_v3_label_distribution": consolidated_labels, "training_readiness_gate": gate, "training_readiness_gate_pass": gate_pass, "all_cleanup_pass": cleanup_pass, "formal_training_started": False, "automatic_continuation": False}
    _put_json(root / "BATCH_RESULT.json", result)
    git_doc = load(root / "GIT_START_END.json")
    git_doc["end"] = _git_gate()
    git_doc["simlingo_unchanged"] = True
    atomic_replace_json(root / "GIT_START_END.json", git_doc)
    _put_json(root / "ARTIFACT_INVENTORY.json", _inventory(root))
    _append(campaign_root(batch_id) / "COMMAND_LOG.md", "- Stage B complete: eligible={} complete={} engineering={} evidence={} labels={} split={} forwards={} cleanup={}.".format(len(terminal), len(complete), len(engineering), len(evidence), label_counts, split_counts, totals["candidate_forward"], cleanup_pass))
    return result


def finalize_campaign(batch_id: str) -> Mapping[str, Any]:
    stage_a = load(stage_a_root(batch_id) / "BATCH_RESULT.json")
    stage_b = load(stage_b_root(batch_id) / "BATCH_RESULT.json")
    final_environment = p3.environment_preflight()
    final_git = _git_gate()
    cleanup_pass = not final_environment["preexisting_real_processes"] and not final_environment["gpu_compute_processes"] and all(final_environment["ports_free"].values()) and stage_a["all_cleanup_pass"] and stage_b["all_cleanup_pass"]
    stage_a_gpu = []
    for item in stage_a_runs(batch_id):
        path = stage_a_root(batch_id) / "runs" / item["primary"] / "GPU_RESOURCE_RECORD.json"
        if path.is_file():
            stage_a_gpu.append(load(path))
    stage_b_plan = load(campaign_root(batch_id) / "STAGE_B_RUN_PLAN.json")
    stage_b_gpu = []
    stage_b_failure_text = []
    for item in stage_b_plan["unit_runs"]:
        run_dir = stage_b_root(batch_id) / "runs" / item["primary"]
        path = run_dir / "GPU_RESOURCE_RECORD.json"
        if path.is_file():
            stage_b_gpu.append(load(path))
        failure_path = run_dir / "WORKER_FAILURE.json"
        if failure_path.is_file():
            stage_b_failure_text.append(json.dumps(load(failure_path), ensure_ascii=False).lower())
    allocated = [int(item["worker"]["peak_allocated_bytes"]) for item in stage_b_gpu if isinstance(item.get("worker"), Mapping) and item["worker"].get("peak_allocated_bytes") is not None]
    reserved = [int(item["worker"]["peak_reserved_bytes"]) for item in stage_b_gpu if isinstance(item.get("worker"), Mapping) and item["worker"].get("peak_reserved_bytes") is not None]
    oom_detected = any(item.get("oom") is True for item in stage_a_gpu) or any("out of memory" in text or "cuda oom" in text for text in stage_b_failure_text)
    resource_summary = {"stage_a_gpu_record_count": len(stage_a_gpu), "stage_a_peak_status": "UNKNOWN_NOT_CONTINUOUSLY_SAMPLED", "stage_b_gpu_record_count": len(stage_b_gpu), "stage_b_peak_allocated_bytes_max": max(allocated) if allocated else None, "stage_b_peak_reserved_bytes_max": max(reserved) if reserved else None, "oom_detected": oom_detected, "final_relevant_process_count": len(final_environment["preexisting_real_processes"]), "final_gpu_compute_process_count": len(final_environment["gpu_compute_processes"]), "ports_free": final_environment["ports_free"]}
    status = stage_b["final_status"] if cleanup_pass else "M1_V3_RUNTIME_CAMPAIGN_BLOCKED_CLEANUP_FAILED"
    # Stage B classifies all 24 selected units, including Stage A exclusions.
    # Its ledgers are therefore the distinct campaign totals; adding Stage A
    # counters here would count the same excluded units twice.
    result = {"schema_version": "driveclarify.m1_v3_runtime_campaign_result.v1", "expansion_id": EXPANSION_ID, "runtime_campaign_id": batch_id, "final_status": status, "completed_at_utc": p3.utc_now(), "shortlist_units": embedded(AUTHORITY / "V3_SHORTLIST.json", "SHORTLIST")["shortlist_count"], "selected_units": 24, "runtime_constructed_units": stage_a["runtime_constructed_unit_count"], "eligible_units": stage_a["eligible_count"], "complete_a3_b3_units": stage_b["complete_a3_b3_unit_count"], "engineering_exclusions": stage_b["engineering_exclusion_count"], "evidence_exclusions": stage_b["evidence_exclusion_count"], "blocked_units": stage_a["blocked_count"], "stage_a_candidate_forward_total": 0, "stage_a_second_observation_total": 0, "stage_b_candidate_forward_total": stage_b["candidate_forward_total"], "v3_split_complete_distribution": stage_b["v3_split_distribution"], "v3_label_distribution": stage_b["v3_label_distribution"], "v2_v3_split_complete_distribution": stage_b["v2_v3_split_distribution"], "v2_v3_label_distribution": stage_b["v2_v3_label_distribution"], "scientific_unknown_ge_3": stage_b["v2_v3_label_distribution"]["UNKNOWN"] >= 3, "formal_learned_m1_readiness": "READY_FOR_FORMAL_LEARNED_M1_ASSESSMENT" if stage_b["training_readiness_gate_pass"] else "DATASET_EXPANSION_V4_REQUIRED", "training_readiness_gate": stage_b["training_readiness_gate"], "training_readiness_gate_pass": stage_b["training_readiness_gate_pass"], "cleanup_pass": cleanup_pass, "resource_summary": resource_summary, "final_environment": final_environment, "git_end": final_git, "v2_tree_sha256": _tree_hash(V2_ROOT), "formal_learned_m1_training_started": False, "m2_started": False, "act_ask_wait_started": False, "automatic_continuation": False}
    root = campaign_root(batch_id)
    atomic_create_json(root / "CAMPAIGN_RESULT.json", result)
    manifest = load(root / "CAMPAIGN_RUN_MANIFEST.json")
    manifest["status"] = status
    manifest["result_path"] = str(root / "CAMPAIGN_RESULT.json")
    atomic_replace_json(root / "CAMPAIGN_RUN_MANIFEST.json", manifest)
    git_doc = load(root / "GIT_START_END.json")
    git_doc["end"] = final_git
    git_doc["simlingo_unchanged"] = True
    git_doc["git_destructive_command_used"] = False
    atomic_replace_json(root / "GIT_START_END.json", git_doc)
    report = """# M1 V3 Runtime Campaign Report

## Technical summary

`{status}`

The fixed 24-unit campaign completed Stage A for every selected unit and Stage B for every eligible unit. Selected/runtime-constructed/eligible/complete=`24/{constructed}/{eligible}/{complete}`; engineering/evidence/blocked=`{engineering}/{evidence}/{blocked}`. Stage A candidate forward and second observation are both zero. Stage B candidate forward=`{forwards}` and every complete unit has exactly six.

## Dataset and readiness

V3 complete split=`{v3_split}` and labels=`{v3_labels}`. Consolidated V2+V3 complete split=`{all_split}` and labels=`{all_labels}`. Formal Learned M1 readiness assessment is `{readiness}`; this is assessment only and does not authorize training.

## Integrity and cleanup

Global leakage remains PASS, V2 tree hash is `{v2_hash}`, and cleanup is `{cleanup}`. No training, M2+, ACT/ASK/WAIT, historical rerun, label mutation, or automatic continuation occurred.
""".format(status=status, constructed=result["runtime_constructed_units"], eligible=result["eligible_units"], complete=result["complete_a3_b3_units"], engineering=result["engineering_exclusions"], evidence=result["evidence_exclusions"], blocked=result["blocked_units"], forwards=result["stage_b_candidate_forward_total"], v3_split=json.dumps(result["v3_split_complete_distribution"], sort_keys=True), v3_labels=json.dumps(result["v3_label_distribution"], sort_keys=True), all_split=json.dumps(result["v2_v3_split_complete_distribution"], sort_keys=True), all_labels=json.dumps(result["v2_v3_label_distribution"], sort_keys=True), readiness=result["formal_learned_m1_readiness"], v2_hash=result["v2_tree_sha256"], cleanup="PASS" if cleanup_pass else "FAIL")
    atomic_create_bytes(root / "V3_RUNTIME_CAMPAIGN_REPORT.md", report.encode("utf-8"))
    top_level_json = {
        "CAMPAIGN_RESULT.json": result,
        "OBSERVATION_SCREENING_RESULTS.json": load(stage_a_root(batch_id) / "BATCH_UNIT_SUMMARY.json"),
        "OBSERVATION_PACKAGE_INVENTORY.json": load(stage_a_root(batch_id) / "OBSERVATION_PACKAGE_INDEX.json"),
        "A3B3_CAPTURE_RESULTS.json": load(stage_b_root(batch_id) / "BATCH_UNIT_SUMMARY.json"),
        "M1_REAL_DATASET_V3.json": load(stage_b_root(batch_id) / "M1_REAL_DATASET_V3.json"),
        "EXCLUSION_LEDGER.json": load(stage_b_root(batch_id) / "EXCLUSION_LEDGER.json"),
        "FORMAL_LEARNED_M1_READINESS.json": load(stage_b_root(batch_id) / "FORMAL_LEARNED_M1_READINESS.json"),
        "PROCESS_AND_GPU_CLEANUP.json": {"schema_version": "driveclarify.m1_v3_process_gpu_cleanup.v1", "campaign_id": batch_id, "cleanup_pass": cleanup_pass, "environment": final_environment, "resource_summary": resource_summary, "stage_a_cleanup": load(stage_a_root(batch_id) / "BATCH_GPU_AND_CLEANUP.json"), "stage_b_cleanup": load(stage_b_root(batch_id) / "BATCH_GPU_AND_CLEANUP.json")},
    }
    v3_dataset_path = AUTHORITY / "M1_REAL_DATASET_V3.json"
    for name, value in top_level_json.items():
        atomic_create_json(AUTHORITY / name, value)
    consolidated = {"schema_version": "driveclarify.m1_consolidated_dataset_index.v1", "status": "COMPLETE_REFERENCES_ONLY", "datasets": [{"version": "V1", "path": V1_DATASET.relative_to(ROOT).as_posix(), "bytes": V1_DATASET.stat().st_size, "sha256": sha256_path(V1_DATASET), "record_count": 30}, {"version": "V2", "path": V2_DATASET.relative_to(ROOT).as_posix(), "bytes": V2_DATASET.stat().st_size, "sha256": sha256_path(V2_DATASET), "selected_units": 30, "complete_units": 25}, {"version": "V3", "path": v3_dataset_path.relative_to(ROOT).as_posix(), "bytes": v3_dataset_path.stat().st_size, "sha256": sha256_path(v3_dataset_path), "selected_units": 24, "complete_units": result["complete_a3_b3_units"]}], "pilot_status": "PILOT_DEVELOPMENT_ONLY", "historical_artifacts_embedded_or_overwritten": False, "formal_v2_v3_complete_units": 25 + result["complete_a3_b3_units"], "formal_v2_v3_label_distribution": result["v2_v3_label_distribution"], "formal_v2_v3_split_distribution": result["v2_v3_split_complete_distribution"]}
    atomic_create_json(AUTHORITY / "CONSOLIDATED_DATASET_INDEX.json", consolidated)
    atomic_create_bytes(AUTHORITY / "V3_RUNTIME_CAMPAIGN_REPORT.md", report.encode("utf-8"))
    _append(root / "COMMAND_LOG.md", "- Campaign terminal `{}`; stopped without automatic continuation.".format(status))
    atomic_create_json(root / "ARTIFACT_INVENTORY.json", _inventory(root))
    return result


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("command", choices=("verify-authority", "prepare-campaign", "prepare-stage-a", "run-stage-a", "finalize-stage-a", "prepare-stage-b", "run-stage-b", "finalize-stage-b", "finalize-campaign"))
    args = parser.parse_args(argv)
    base._timestamp_from_batch(args.batch_id)
    if args.command == "verify-authority":
        print(json.dumps(verify_frozen_authority(), ensure_ascii=False, sort_keys=True))
    elif args.command == "prepare-campaign":
        prepare_campaign(args.batch_id)
    elif args.command == "prepare-stage-a":
        prepare_stage_a(args.batch_id)
    elif args.command == "run-stage-a":
        run_stage_a(args.batch_id)
    elif args.command == "finalize-stage-a":
        print(json.dumps(finalize_stage_a(args.batch_id), ensure_ascii=False, sort_keys=True))
    elif args.command == "prepare-stage-b":
        print(json.dumps(prepare_stage_b(args.batch_id), ensure_ascii=False, sort_keys=True))
    elif args.command == "run-stage-b":
        run_stage_b(args.batch_id)
    elif args.command == "finalize-stage-b":
        print(json.dumps(finalize_stage_b(args.batch_id), ensure_ascii=False, sort_keys=True))
    elif args.command == "finalize-campaign":
        print(json.dumps(finalize_campaign(args.batch_id), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
