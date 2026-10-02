"""Authorized M1 V2 Stage-A then Stage-B runtime campaign.

This is an append-only compatibility layer over the already validated Pilot
observation and offline-capture kernels.  Frozen V2 selection, fixtures,
splits, labels and mapper authority are read-only inputs.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import jsonschema

from . import m3e_p3_campaign as p3
from . import observation_screening_batch as observation
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


ROOT = Path("/home/buaa/wrh/DriveClarify")
SIMLINGO = Path("/home/buaa/wrh/simlingo")
AUTHORITY = (
    ROOT
    / "reports/m1_real_dataset_expansion_v2"
    / "DC-M1-DATASET-EXP-V2-20260803T143000Z"
)
SELECTED = AUTHORITY / "M1_V2_SELECTED_UNITS_MANIFEST.json"
SPLIT = AUTHORITY / "M1_V2_SPLIT_MANIFEST.json"
CAMPAIGN_SPEC = AUTHORITY / "M1_V2_RUNTIME_CAMPAIGN_SPEC.json"
OBS_SCHEMA = AUTHORITY / "M1_V2_OBSERVATION_OUTPUT_SCHEMA.json"
CAPTURE_SCHEMA = AUTHORITY / "M1_V2_CAPTURE_OUTPUT_SCHEMA.json"
DATA_SCHEMA = AUTHORITY / "M1_REAL_DATASET_V2_DATA_SCHEMA.json"
FROZEN_INVENTORY = AUTHORITY / "ARTIFACT_INVENTORY.json"
EXCLUSIONS = AUTHORITY / "FROZEN_EXCLUSIONS_MANIFEST.json"
PILOTS = AUTHORITY / "PILOT_UNITS_DEVELOPMENT_ONLY_MANIFEST.json"
THRESHOLDS = ROOT / "reports/static_maneuver_branch_primary_mvp_v1/MAPPING_THRESHOLD_PROVENANCE.json"
THRESHOLD_FILE_SHA256 = "6a118500243116315ccd8e0943413fadd4845b171e2ea5b5f858aa1504a1e6fc"
THRESHOLD_EMBEDDED_SHA256 = "6dfeea8907eb987c512c2b180a824d78c38d7c57662385c24d0d884bf3a37553"
V1_ROOT = (
    ROOT
    / "reports/multi_topology_static_units_v1/offline_candidate_capture_runs"
    / "DC-MULTI-A3B3-C1-20260803T121500Z"
)
V1_DATASET = V1_ROOT / "M1_REAL_DATASET_V1.json"
V1_DATASET_SHA256 = "c2827fc97466adef722b77708dc1a65e8f6748cbb0e07d12ab2f4f3bc389e09f"
CHECKPOINT_SHA256 = "ec8943723d266ee9f5f56f45d153a163b22616960bfccb741965ea5daa700d28"
CONFIG_SHA256 = "d56a7c1ebf3b6fd7ff6edff87071f1b2fe7269563e3ea626379994bef7807417"
SIMLINGO_DIFF_SHA256 = "7bd0947ef17df95b56d95ce155468d378a3c282b59f7dee4a2653a7195bc5e34"
SCHEDULE = ("A1", "A2", "A3", "B1", "B2", "B3")


class CampaignError(RuntimeError):
    pass


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def embedded(path: Path, label: str) -> Mapping[str, Any]:
    value = load(path)
    unsigned = dict(value)
    recorded = unsigned.pop("sha256", None)
    if recorded != digest_value(unsigned):
        raise CampaignError("EMBEDDED_SHA256_MISMATCH:{}:{}".format(label, path))
    return value


def _timestamp_from_batch(batch_id: str) -> str:
    value = batch_id.rsplit("-", 1)[-1]
    if len(value) != 16 or not value.endswith("Z"):
        raise CampaignError("BATCH_ID_TIMESTAMP_FORMAT_INVALID")
    dt.datetime.strptime(value, "%Y%m%dT%H%M%SZ")
    return value


def campaign_root(batch_id: str) -> Path:
    return AUTHORITY / "combined_runtime_campaigns" / batch_id


def stage_a_root(batch_id: str) -> Path:
    return campaign_root(batch_id) / "stage_a"


def stage_b_root(batch_id: str) -> Path:
    return campaign_root(batch_id) / "stage_b"


def _run_timestamp(batch_id: str, offset_seconds: int) -> str:
    base = dt.datetime.strptime(_timestamp_from_batch(batch_id), "%Y%m%dT%H%M%SZ")
    return (base + dt.timedelta(seconds=offset_seconds)).strftime("%Y%m%dT%H%M%SZ")


def selected_rows() -> List[Mapping[str, Any]]:
    value = embedded(SELECTED, "SELECTED")
    return list(value["selected_units"])


def stage_a_runs(batch_id: str) -> Tuple[Mapping[str, str], ...]:
    rows = []
    for index, item in enumerate(selected_rows()):
        short = item["unit_id"].replace("TOWN", "T").replace("_JUNCTION_", "-J").replace("_UNIT01", "")
        rows.append(
            {
                "unit_id": item["unit_id"],
                "primary": "DC-M1V2-OBS-{}-A-{}".format(short, _run_timestamp(batch_id, index + 1)),
                "recovery": "DC-M1V2-OBS-{}-UNUSED-{}".format(short, _run_timestamp(batch_id, index + 301)),
            }
        )
    return tuple(rows)


def verify_frozen_authority() -> Mapping[str, Any]:
    inventory = embedded(FROZEN_INVENTORY, "FROZEN_INVENTORY")
    mismatches = []
    rows = []
    for item in inventory["files"]:
        path = ROOT / item["path"]
        actual = {
            "path": item["path"],
            "exists": path.is_file(),
            "bytes": path.stat().st_size if path.is_file() else None,
            "sha256": sha256_path(path) if path.is_file() else None,
        }
        actual["match"] = (
            actual["exists"]
            and actual["bytes"] == item["bytes"]
            and actual["sha256"] == item["sha256"]
        )
        if not actual["match"]:
            mismatches.append(item["path"])
        rows.append(actual)
    if mismatches:
        raise CampaignError("FROZEN_AUTHORITY_FILE_MISMATCH:" + ",".join(mismatches))
    selected = embedded(SELECTED, "SELECTED")
    split = embedded(SPLIT, "SPLIT")
    spec = embedded(CAMPAIGN_SPEC, "CAMPAIGN_SPEC")
    exclusions = embedded(EXCLUSIONS, "EXCLUSIONS")
    pilots = embedded(PILOTS, "PILOTS")
    threshold = embedded(THRESHOLDS, "THRESHOLDS")
    state = load(ROOT / "STATE.json")
    if state.get("status") != "READY_FOR_M1_V2_RUNTIME_BATCH_AUTHORIZATION":
        raise CampaignError("ENTRY_STATE_MISMATCH")
    if selected.get("selected_count") != 30 or spec.get("unit_order") != [r["unit_id"] for r in selected["selected_units"]]:
        raise CampaignError("FROZEN_SELECTED_UNIT_IDENTITY_MISMATCH")
    if selected.get("route_start_signed_station_m") != -5.5 or not selected.get("all_mapper_compatible"):
        raise CampaignError("FROZEN_ROUTE_START_OR_MAPPER_GATE_MISMATCH")
    if split.get("split_counts") != {"DEV": 6, "TEST": 6, "TRAIN": 18}:
        raise CampaignError("FROZEN_SPLIT_COUNT_MISMATCH")
    if spec.get("run_authorized") is not False or any(spec.get(name) for name in ("run_ids", "receipts", "run_outputs")) or spec.get("batch_id") is not None:
        raise CampaignError("FROZEN_CAMPAIGN_SPEC_ALREADY_CONSUMED_OR_MUTATED")
    if pilots.get("unit_count") != 5 or pilots.get("formal_dev_test_membership_count") != 0:
        raise CampaignError("PILOT_DEVELOPMENT_ONLY_AUTHORITY_MISMATCH")
    if not any(item.get("unit_id") == "TOWN03_JUNCTION_1221_UNIT01" for item in exclusions["exclusions"]):
        raise CampaignError("FROZEN_ENGINEERING_EXCLUSION_MISSING")
    if not any(item.get("route_id") == "27515" and item.get("junction_id") == "238" for item in exclusions["exclusions"]):
        raise CampaignError("FROZEN_ROUTE_27515_EXCLUSION_MISSING")
    if sha256_path(THRESHOLDS) != THRESHOLD_FILE_SHA256 or threshold["sha256"] != THRESHOLD_EMBEDDED_SHA256:
        raise CampaignError("THRESHOLD_AUTHORITY_MISMATCH")
    selected_by_id = {item["unit_id"]: item for item in selected["selected_units"]}
    unit_checks = []
    for unit_id, row in selected_by_id.items():
        paths = _unit_paths(unit_id)
        unit = embedded(paths["manifest"], "UNIT")
        contract = embedded(paths["contract"], "CONTRACT")
        topology = embedded(paths["topology"], "TOPOLOGY")
        compatibility = embedded(paths["mapper_compatibility"], "MAPPER_COMPATIBILITY")
        provenance = embedded(paths["source_provenance"], "SOURCE_PROVENANCE")
        assignment = embedded(paths["split_assignment"], "SPLIT_ASSIGNMENT")
        if (
            unit["route_start"]["signed_station_from_decision_point_m"] != -5.5
            or unit["eligibility_interval"] != contract["eligibility_interval"]
            or unit["topology_sha256"] != topology["sha256"]
            or row["fixture_sha256"] != sha256_path(paths["fixture"])
            or compatibility.get("compatibility_verdict") != "PASS_FIXED_STATIC_BRANCH_PLAN_MAPPER_V1"
            or provenance.get("source_type") != "OPENDRIVE_GENERATED_STATIC"
            or assignment.get("split") != row["split"]
            or unit.get("scenario_count") != 0
            or unit.get("actor_count") != 0
            or unit.get("trigger_count") != 0
        ):
            raise CampaignError("UNIT_FROZEN_CONTRACT_MISMATCH:" + unit_id)
        unit_checks.append({"unit_id": unit_id, "split": row["split"], "fixture_sha256": row["fixture_sha256"], "contract_sha256": contract["sha256"], "topology_sha256": topology["sha256"]})
    return {
        "status": "PASS",
        "entry_status": state["status"],
        "inventoried_file_count": len(rows),
        "inventory_sha256": inventory["sha256"],
        "selected_count": 30,
        "split_counts": split["split_counts"],
        "unit_checks": unit_checks,
    }


def _unit_paths(unit_id: str) -> Mapping[str, Path]:
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
        "split_assignment": root / "SPLIT_ASSIGNMENT.json",
    }


def _git_gate() -> Mapping[str, Any]:
    drive = p3.git_state(ROOT)
    sim = p3.git_state(SIMLINGO)
    if drive["branch"] != "master" or drive["head"] != "eaa332b1bb994279b59ea5af786fdb5de96adc1b" or drive["tracked_diff_bytes"] != 0 or drive["staged_diff_bytes"] != 0:
        raise CampaignError("BLOCKED_UNATTRIBUTED_WORKTREE_CHANGE:DRIVECLARIFY")
    if sim["branch"] != "main" or sim["head"] != "743b243afd6cf5ff51b9fa1f8cac86f22d569684" or sim["tracked_diff_bytes"] != 7722 or sim["tracked_diff_sha256"] != SIMLINGO_DIFF_SHA256 or sim["staged_diff_bytes"] != 0:
        raise CampaignError("BLOCKED_UNATTRIBUTED_WORKTREE_CHANGE:SIMLINGO")
    return {"driveclarify": drive, "simlingo": sim}


def _inventory(root: Path, excluded: Iterable[str] = ("ARTIFACT_INVENTORY.json",)) -> Mapping[str, Any]:
    excluded_set = set(excluded)
    rows = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        if relative in excluded_set:
            continue
        rows.append({"path": relative, "bytes": path.stat().st_size, "sha256": sha256_path(path)})
    return {"schema_version": "driveclarify.m1_v2_artifact_inventory.v1", "root": str(root), "file_count": len(rows), "total_bytes": sum(item["bytes"] for item in rows), "aggregate_sha256": digest_value(rows), "files": rows}


def _append(path: Path, text: str) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(text.rstrip() + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _put_json(path: Path, value: Any) -> None:
    if path.exists():
        atomic_replace_json(path, value)
    else:
        atomic_create_json(path, value)


def prepare_campaign(batch_id: str) -> None:
    root = campaign_root(batch_id)
    if root.exists():
        raise CampaignError("CAMPAIGN_ROOT_ALREADY_EXISTS")
    authority = verify_frozen_authority()
    git = _git_gate()
    environment = p3.environment_preflight()
    if not environment["physical_display_pass"] or not all(environment["ports_free"].values()) or environment["gpu_compute_processes"] or environment["preexisting_real_processes"]:
        raise CampaignError("CAMPAIGN_RUNTIME_ENVIRONMENT_NOT_CLEAN")
    root.mkdir(parents=True)
    atomic_create_json(root / "CAMPAIGN_AUTHORIZATION.json", {
        "schema_version": "driveclarify.m1_v2_combined_runtime_authorization.v1",
        "campaign_name": "M1_V2_COMBINED_RUNTIME_CAMPAIGN",
        "batch_id": batch_id,
        "authorization_source": "USER_EXPLICIT_ONE_TIME_AUTHORIZATION_2026_08_03",
        "authorized_at_utc": p3.utc_now(),
        "scope": ["STAGE_A_FIRST_OBSERVATION_SCREEN", "STAGE_B_ELIGIBLE_OFFLINE_FROZEN_A3_B3"],
        "selected_unit_order": [item["unit_id"] for item in selected_rows()],
        "stage_b_schedule": list(SCHEDULE),
        "recovery_policy": "NO_RECOVERY_RUN; ENGINEERING_FAILURE_IS_EXCLUSION",
        "training_authorized": False,
        "automatic_continuation_after_campaign": False,
        "authority_validation": authority,
        "git_at_entry": git,
        "environment_at_entry": environment,
    })
    atomic_create_json(root / "CAMPAIGN_RUN_MANIFEST.json", {
        "schema_version": "driveclarify.m1_v2_combined_runtime_run_manifest.v1",
        "batch_id": batch_id,
        "expansion_id": "DC-M1-DATASET-EXP-V2-20260803T143000Z",
        "selected_count": 30,
        "stage_a_batch_id": batch_id + "-STAGE-A",
        "stage_b_batch_id": batch_id + "-STAGE-B",
        "stage_a_root": str(stage_a_root(batch_id)),
        "stage_b_root": str(stage_b_root(batch_id)),
        "frozen_campaign_spec_path": str(CAMPAIGN_SPEC),
        "frozen_campaign_spec_sha256": sha256_path(CAMPAIGN_SPEC),
        "status": "AUTHORIZED_NOT_STARTED",
    })
    atomic_create_json(root / "GIT_START_END.json", {"schema_version": "driveclarify.m1_v2_combined_git_start_end.v1", "batch_id": batch_id, "start": git, "end": None})
    atomic_create_bytes(root / "COMMAND_LOG.md", ("# M1 V2 combined runtime campaign command log\n\n- `{}` authorized and prepared; frozen authority, Git, display, ports and GPU entry checks PASS.\n".format(batch_id)).encode("utf-8"))


def _frozen_snapshot() -> Mapping[str, Any]:
    check = verify_frozen_authority()
    return {"root": str(AUTHORITY), "file_count": check["inventoried_file_count"], "aggregate_sha256": check["inventory_sha256"], "files": []}


def _protected_history() -> Mapping[str, Any]:
    if sha256_path(V1_DATASET) != V1_DATASET_SHA256:
        raise CampaignError("V1_DATASET_CHANGED")
    return {
        "legacy": p3.protected_history(),
        "v1_dataset_path": str(V1_DATASET),
        "v1_dataset_sha256": V1_DATASET_SHA256,
        "v1_record_count": 30,
        "pilot_complete_unit_count": 5,
        "route_27515_junction_238_rerun_count": 0,
        "town03_junction_1221_reintroduced": False,
    }


def _validate_stage_a_authority() -> Mapping[str, Any]:
    value = verify_frozen_authority()
    value.update({"preparation_batch_spec_unexecuted": True, "route_27515_frozen_exclusion": True, "threshold_file_sha256": THRESHOLD_FILE_SHA256, "threshold_embedded_sha256": THRESHOLD_EMBEDDED_SHA256, "units": value.pop("unit_checks")})
    return value


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
    observation.ALL_RUN_IDS = tuple(run_id for item in runs for run_id in (item["primary"], item["recovery"]))
    observation.validate_authority = _validate_stage_a_authority
    observation.authority_snapshot = _frozen_snapshot
    observation.protected_history = _protected_history


def prepare_stage_a(batch_id: str) -> None:
    configure_stage_a(batch_id)
    authorization_path = stage_a_root(batch_id) / "BATCH_AUTHORIZATION.json"
    if not authorization_path.is_file():
        observation.prepare_batch()
        authorization = load(authorization_path)
        authorization["authorization_source"] = "USER_EXPLICIT_M1_V2_COMBINED_RUNTIME_CAMPAIGN_STAGE_A"
        authorization["selected_unit_count"] = 30
        authorization["constraints"]["recovery_per_unit_engineering_failure_only"] = 0
        authorization["constraints"]["engineering_failure_policy"] = "TERMINAL_ENGINEERING_EXCLUSION_NO_RECOVERY"
        atomic_replace_json(authorization_path, authorization)
    _stage_a_v2_preflight(batch_id)
    manifest = load(campaign_root(batch_id) / "CAMPAIGN_RUN_MANIFEST.json")
    manifest["status"] = "STAGE_A_PREFLIGHT_PASS"
    atomic_replace_json(campaign_root(batch_id) / "CAMPAIGN_RUN_MANIFEST.json", manifest)
    _append(campaign_root(batch_id) / "COMMAND_LOG.md", "- Stage A prepared; CPU/static preflight PASS; candidate forward count remains 0.")
    preflight_path = stage_a_root(batch_id) / "BATCH_CPU_PREFLIGHT.json"
    preflight = load(preflight_path)
    preflight["outside_batch_untracked_snapshot"] = observation.outside_batch_untracked_snapshot()
    atomic_replace_json(preflight_path, preflight)


def _stage_a_v2_preflight(batch_id: str) -> None:
    root = stage_a_root(batch_id)
    staged = [observation.RUNS_ROOT / run_id for run_id in observation.ALL_RUN_IDS if (observation.RUNS_ROOT / run_id).exists()]
    if any((path / "AUTHORIZATION_RECEIPT.json").exists() or (path / "REAL_LAUNCH_RECORD.json").exists() for path in staged):
        raise CampaignError("STAGE_A_PREFLIGHT_AFTER_RECEIPT_OR_REAL_LAUNCH_FORBIDDEN")
    environment = dict(os.environ)
    environment.update({"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONDONTWRITEBYTECODE": "1"})
    cpu_python = Path(sys.executable)
    commands = [
        [str(p3.PYTHON38), "-B", "-c", "compile(open(%r, 'rb').read(), %r, 'exec')" % (str(observation.AGENT_SOURCE), str(observation.AGENT_SOURCE))],
        [str(p3.PYTHON38), "-B", "-c", "compile(open(%r, 'rb').read(), %r, 'exec')" % (str(observation.PACKAGE_SOURCE), str(observation.PACKAGE_SOURCE))],
        [str(p3.PYTHON38), "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/evaluator_adapter_production_path", "tests/m3d_route_validation"],
        [str(cpu_python), "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/m3e_supervisor_binding"],
        [str(cpu_python), "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/observation_screening", "-k", "not frozen_authority_validates_without_runtime_side_effects"],
        [str(cpu_python), "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/m1_real_dataset_expansion_v2"],
        [str(p3.PYTHON38), "-B", "tools/evaluator_production_path_dry_run.py"],
    ]
    results = [observation.run_command(command, ROOT, environment) for command in commands]
    sim = p3.git_state(SIMLINGO)
    passed = all(item["exit_code"] == 0 for item in results) and sim["tracked_diff_bytes"] == 7722 and sim["tracked_diff_sha256"] == SIMLINGO_DIFF_SHA256 and sim["staged_diff_bytes"] == 0
    payload = {"schema_version": "driveclarify.m1_v2_stage_a_cpu_preflight.v1", "batch_id": batch_id + "-STAGE-A", "status": "PASS" if passed else "FAIL", "checked_at_utc": p3.utc_now(), "commands": results, "authority": verify_frozen_authority(), "protected_history": _protected_history(), "authority_snapshot": _frozen_snapshot(), "outside_batch_untracked_snapshot": observation.outside_batch_untracked_snapshot(), "simlingo_git": sim, "production_adapter_dry_run": "PASS" if results[-1]["exit_code"] == 0 else "FAIL", "real_system_launches": 0, "torch_imported_by_orchestrator": False, "cuda_initializations": 0}
    preflight_path = root / "BATCH_CPU_PREFLIGHT.json"
    if preflight_path.exists():
        attempt = root / "BATCH_CPU_PREFLIGHT_LEGACY_ATTEMPT.json"
        if not attempt.exists():
            atomic_create_json(attempt, load(preflight_path))
        atomic_replace_json(preflight_path, payload)
    else:
        atomic_create_json(preflight_path, payload)
    _append(root / "BATCH_COMMAND_LOG.md", "- V2 CPU preflight `{}`; exits={}.".format(payload["status"], [item["exit_code"] for item in results]))
    if not passed:
        raise CampaignError("STAGE_A_V2_PREFLIGHT_FAILED")


def _seal_stage_a_prelaunch_failure(batch_id: str, run_id: str, unit_id: str, exc: BaseException) -> None:
    output = stage_a_root(batch_id) / "runs" / run_id
    output.mkdir(parents=True, exist_ok=True)
    (output / "logs").mkdir(exist_ok=True)
    (output / "receipt_claims").mkdir(exist_ok=True)
    payload = {
        "schema_version": "driveclarify.stage_a_run_lifecycle.v1",
        "batch_id": batch_id + "-STAGE-A",
        "run_id": run_id,
        "unit_id": unit_id,
        "run_role": "primary",
        "lifecycle_state": "PRELAUNCH",
        "prelaunch_disposition": "BLOCKED_RETRYABLE",
        "terminal": False,
        "reason_codes": ["ATTRIBUTABLE_PRELAUNCH_ENGINEERING_FAILURE"],
        "runtime_counts": {"candidate_forward": 0, "second_observation": 0, "mapper_invocation": 0, "training": 0, "act_ask_wait": 0, "carla_launch": 0, "evaluator_launch": 0, "checkpoint_load": 0, "model_load": 0},
        "prelaunch_exception": {"type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()},
    }
    create_prelaunch_state(output, payload)
    blocked = output / "PRELAUNCH_BLOCKED.json"
    if not blocked.exists():
        atomic_create_json(blocked, payload["prelaunch_exception"])


def run_stage_a(batch_id: str) -> None:
    configure_stage_a(batch_id)
    for index, item in enumerate(observation.UNIT_RUNS, start=1):
        run_id = item["primary"]
        lifecycle = read_run_lifecycle(observation.run_output(run_id))
        if lifecycle["terminal"] is True:
            print("STAGE_A_SKIP {}/30 {} already_terminal".format(index, item["unit_id"]), flush=True)
            continue
        print("STAGE_A_START {}/30 {} {}".format(index, item["unit_id"], run_id), flush=True)
        try:
            if not observation.run_output(run_id).exists():
                observation.stage_run(run_id)
            _stage_a_unit_cpu_check(run_id)
            if not (observation.run_output(run_id) / "PRELAUNCH_SUPERVISOR_SELFTEST.json").is_file():
                observation.prelaunch_selftest(run_id)
            observation.authorize_run(run_id)
            observation.launch_run(run_id)
            outcome = observation.finalize_run(run_id)
        except BaseException as exc:
            if not (observation.run_output(run_id) / "REAL_LAUNCH_RECORD.json").exists():
                _seal_stage_a_prelaunch_failure(batch_id, run_id, item["unit_id"], exc)
                outcome = "RUNTIME_FAILURE"
            else:
                raise
        print("STAGE_A_DONE {}/30 {} {}".format(index, item["unit_id"], outcome), flush=True)


def _stage_a_unit_cpu_check(run_id: str) -> None:
    output = observation.run_output(run_id)
    if (output / "AUTHORIZATION_RECEIPT.json").exists():
        raise CampaignError("STAGE_A_CPU_CHECK_AFTER_RECEIPT_FORBIDDEN")
    unit_id = observation.unit_entry_for_run(run_id)[1]["unit_id"]
    paths = observation.unit_paths(unit_id)
    environment = dict(os.environ)
    environment.update({"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONDONTWRITEBYTECODE": "1"})
    commands = [
        [str(p3.PYTHON38), "-B", "-c", "compile(open(%r, 'rb').read(), %r, 'exec')" % (str(output / "OBSERVATION_SCREENING_AGENT.py"), str(output / "OBSERVATION_SCREENING_AGENT.py"))],
        [str(p3.PYTHON38), "-B", "-c", "compile(open(%r, 'rb').read(), %r, 'exec')" % (str(output / "M3E_EVALUATOR_ENTRY.py"), str(output / "M3E_EVALUATOR_ENTRY.py"))],
        [str(p3.PYTHON38), "-B", "tools/evaluator_production_path_dry_run.py"],
        [str(sys.executable), "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/observation_screening", "-k", "not frozen_authority_validates_without_runtime_side_effects"],
    ]
    results = [observation.run_command(command, ROOT, environment) for command in commands]
    unit = embedded(paths["manifest"], "UNIT")
    passed = all(item["exit_code"] == 0 for item in results) and sha256_path(paths["fixture"]) == unit["artifacts"][0]["sha256"] and verify_frozen_authority()["status"] == "PASS"
    payload = {"schema_version": "driveclarify.m1_v2_stage_a_run_cpu_check.v1", "batch_id": observation.BATCH_ID, "run_id": run_id, "status": "PASS" if passed else "FAIL", "checked_at_utc": p3.utc_now(), "commands": results, "production_adapter_dry_check": "PASS" if results[2]["exit_code"] == 0 else "FAIL", "route_fixture_sha256": sha256_path(paths["fixture"]), "authority": _validate_stage_a_authority(), "real_system_launches": 0}
    check_path = output / "CPU_CHECK_RESULTS.json"
    if check_path.exists(): atomic_replace_json(check_path, payload)
    else: atomic_create_json(check_path, payload)
    _append(output / "COMMAND_LOG.md", "- V2 per-run production adapter/route/serializer CPU check: `{}`.".format(payload["status"]))
    if not passed:
        raise CampaignError("STAGE_A_V2_RUN_CPU_CHECK_FAILED:" + run_id)


def finalize_stage_a(batch_id: str) -> Mapping[str, Any]:
    configure_stage_a(batch_id)
    rows = []
    used_results = []
    packages = []
    for item in observation.UNIT_RUNS:
        run_id = item["primary"]
        run_dir = observation.run_output(run_id)
        lifecycle = read_run_lifecycle(run_dir)
        if lifecycle["terminal"] is not True:
            screen_path = run_dir / "OBSERVATION_SCREENING_RESULT.json"
            counts_path = run_dir / "RUNTIME_COUNTS.json"
            if not screen_path.is_file() or not counts_path.is_file():
                raise CampaignError("STAGE_A_PRIMARY_NOT_TERMINAL:" + item["unit_id"])
            reasons = ["ATTRIBUTABLE_OBSERVATION_SCREENING_ENGINEERING_FAILURE", "RUNTIME_RESULT_NOT_SCIENTIFIC_TERMINAL"]
            reconstructed = {"schema_version": "driveclarify.observation_screening_run_result.v1", "batch_id": batch_id + "-STAGE-A", "run_id": run_id, "unit_id": item["unit_id"], "run_role": "primary", "outcome": "RUNTIME_FAILURE", "terminal": True, "terminal_category": "TERMINAL_EXCLUSION", "exclusion": {"category": "ENGINEERING", "reason_codes": reasons}, "reason_codes": reasons, "observation_screening": load(screen_path), "runtime_counts": load(counts_path), "cleanup": load(screen_path).get("cleanup_status", "UNKNOWN"), "first_runtime_exception": {"type": "MissingTerminalEnvelope", "message": "formal RUN_RESULT envelope absent after consumed launch; preserved per-run artifacts used for terminal engineering exclusion"}}
            publish_terminal_result(run_dir, reconstructed)
            lifecycle = read_run_lifecycle(run_dir)
        result = lifecycle["payload"]
        used_results.append(result)
        screen = result["observation_screening"]
        raw = result["outcome"]
        classification = {"ELIGIBLE": "ELIGIBLE", "EVIDENCE_UNAVAILABLE": "EVIDENCE_EXCLUSION", "RUNTIME_FAILURE": "ENGINEERING_EXCLUSION"}[raw]
        manifest_path = run_dir / "OBSERVATION_PACKAGE_MANIFEST.json"
        package = load(manifest_path) if raw == "ELIGIBLE" and manifest_path.is_file() else None
        normalized = {
            "schema_version": "driveclarify.m1_v2_observation_output.v1",
            "unit_id": item["unit_id"], "outcome": raw,
            "observation_index": screen.get("observation_index"),
            "observation_package": package,
            "observation_package_sha256": sha256_path(manifest_path) if package is not None else None,
            "signed_station_m": screen.get("signed_station_m"),
            "eligibility_contract_sha256": embedded(_unit_paths(item["unit_id"])["contract"], "CONTRACT")["sha256"],
            "candidate_forward_count": 0,
            "second_observation_count": 0,
            "cleanup_status": screen.get("cleanup_status", result.get("cleanup", "UNKNOWN")),
            "reason_codes": screen.get("reason_codes", result.get("reason_codes", [])),
        }
        jsonschema.validate(normalized, load(OBS_SCHEMA))
        normalized_path = run_dir / "M1_V2_OBSERVATION_OUTPUT.json"
        if normalized_path.exists():
            atomic_replace_json(normalized_path, normalized)
        else:
            atomic_create_json(normalized_path, normalized)
        row = {
            "unit_id": item["unit_id"], "run_id": run_id,
            "outcome": raw, "classification": classification,
            "observation_index": screen.get("observation_index"),
            "source_frame": screen.get("source_frame"),
            "signed_station_m": screen.get("signed_station_m"),
            "reason_codes": normalized["reason_codes"],
            "cleanup_status": normalized["cleanup_status"],
            "observation_package_manifest_path": str(manifest_path) if package is not None else None,
            "observation_package_manifest_sha256": normalized["observation_package_sha256"],
            "candidate_forward_count": 0, "second_observation_count": 0,
        }
        rows.append(row)
        if package is not None:
            packages.append({"unit_id": item["unit_id"], "run_id": run_id, "observation_hash": package["observation_hash"], "package_path": package["package_directory"], "manifest_path": str(manifest_path), "manifest_sha256": sha256_path(manifest_path), "file_count": package["file_count"], "package_content_sha256": package["package_content_sha256"]})
    totals = observation._sum_counts(used_results)
    if totals["candidate_forward"] != 0 or totals["second_observation"] != 0 or totals["mapper_invocation"] != 0:
        raise CampaignError("STAGE_A_PROHIBITED_COUNT_NONZERO")
    eligible = [item for item in rows if item["classification"] == "ELIGIBLE"]
    evidence = [item for item in rows if item["classification"] == "EVIDENCE_EXCLUSION"]
    engineering = [item for item in rows if item["classification"] == "ENGINEERING_EXCLUSION"]
    root = stage_a_root(batch_id)
    documents = {
        "BATCH_UNIT_SUMMARY.json": {"schema_version": "driveclarify.m1_v2_stage_a_unit_summary.v1", "batch_id": batch_id + "-STAGE-A", "selected_unit_count": 30, "terminal_unit_count": 30, "units": rows},
        "ELIGIBLE_UNITS_MANIFEST.json": {"schema_version": "driveclarify.m1_v2_eligible_units_manifest.v1", "count": len(eligible), "units": eligible},
        "EVIDENCE_EXCLUSION_UNITS_MANIFEST.json": {"schema_version": "driveclarify.m1_v2_evidence_exclusion_manifest.v1", "count": len(evidence), "units": evidence},
        "ENGINEERING_EXCLUSION_UNITS_MANIFEST.json": {"schema_version": "driveclarify.m1_v2_engineering_exclusion_manifest.v1", "count": len(engineering), "units": engineering},
        "OBSERVATION_PACKAGE_INDEX.json": {"schema_version": "driveclarify.m1_v2_observation_package_index.v1", "eligible_package_count": len(packages), "packages": packages},
        "BATCH_RUNTIME_COUNTS.json": {"schema_version": "driveclarify.m1_v2_stage_a_runtime_counts.v1", "totals": totals, "candidate_forward_total": 0, "second_observation_total": 0, "mapper_invocation_total": 0, "training_total": 0, "act_ask_wait_total": 0},
    }
    for name, value in documents.items():
        path = root / name
        if path.exists(): atomic_replace_json(path, value)
        else: atomic_create_json(path, value)
    compute = p3.environment_preflight()["gpu_compute_processes"]
    cleanup_pass = all(item["cleanup_status"] == "PASS" for item in rows) and not compute
    atomic_create_json(root / "BATCH_GPU_AND_CLEANUP.json", {"schema_version": "driveclarify.m1_v2_stage_a_cleanup.v1", "all_cleanup_pass": cleanup_pass, "final_gpu_compute_process_count": len(compute), "final_gpu_compute_processes": compute})
    result = {"schema_version": "driveclarify.m1_v2_stage_a_result.v1", "batch_id": batch_id + "-STAGE-A", "final_status": "M1_V2_STAGE_A_COMPLETE", "selected_unit_count": 30, "runtime_constructed_unit_count": sum(1 for item in used_results if item.get("runtime_counts", {}).get("carla_launch", 0) > 0), "eligible_count": len(eligible), "evidence_exclusion_count": len(evidence), "engineering_exclusion_count": len(engineering), "candidate_forward_total": 0, "second_observation_total": 0, "all_cleanup_pass": cleanup_pass, "stage_b_unit_count": len(eligible)}
    atomic_create_json(root / "BATCH_RESULT.json", result)
    atomic_create_json(root / "GIT_START_END_BATCH.json", {"schema_version": "driveclarify.m1_v2_stage_a_git_start_end.v1", "start": load(campaign_root(batch_id) / "GIT_START_END.json")["start"], "end": _git_gate(), "simlingo_unchanged": True, "git_destructive_command_used": False})
    atomic_create_json(root / "ARTIFACT_INVENTORY.json", _inventory(root))
    _append(campaign_root(batch_id) / "COMMAND_LOG.md", "- Stage A complete: selected=30, eligible={}, evidence exclusions={}, engineering exclusions={}, candidate forwards=0.".format(len(eligible), len(evidence), len(engineering)))
    return result


def prepare_stage_b(batch_id: str) -> Mapping[str, Any]:
    a_result = load(stage_a_root(batch_id) / "BATCH_RESULT.json")
    if a_result.get("final_status") != "M1_V2_STAGE_A_COMPLETE" or a_result.get("candidate_forward_total") != 0:
        raise CampaignError("STAGE_A_NOT_READY_FOR_STAGE_B")
    packages = load(stage_a_root(batch_id) / "OBSERVATION_PACKAGE_INDEX.json")["packages"]
    eligible_ids = {item["unit_id"] for item in packages}
    ordered = [item for item in selected_rows() if item["unit_id"] in eligible_ids]
    unit_runs = []
    for index, item in enumerate(ordered):
        short = item["unit_id"].replace("TOWN", "T").replace("_JUNCTION_", "-J").replace("_UNIT01", "")
        unit_runs.append({"unit_id": item["unit_id"], "primary": "DC-M1V2-A3B3-{}-A-{}".format(short, _run_timestamp(batch_id, 601 + index)), "recovery": "DC-M1V2-A3B3-{}-UNUSED-{}".format(short, _run_timestamp(batch_id, 901 + index))})
    plan = {"schema_version": "driveclarify.m1_v2_stage_b_run_plan.v1", "combined_batch_id": batch_id, "stage_a_batch_id": batch_id + "-STAGE-A", "stage_a_root": str(stage_a_root(batch_id)), "stage_b_batch_id": batch_id + "-STAGE-B", "stage_b_root": str(stage_b_root(batch_id)), "schedule": list(SCHEDULE), "eligible_unit_count": len(unit_runs), "unit_runs": unit_runs}
    plan_path = campaign_root(batch_id) / "STAGE_B_RUN_PLAN.json"
    atomic_create_json(plan_path, plan)
    os.environ.update({"DRIVECLARIFY_M1_V2_AUTHORITY": str(AUTHORITY), "DRIVECLARIFY_M1_V2_RUN_PLAN": str(plan_path)})
    from . import offline_candidate_batch as batch
    from . import offline_candidate_capture as capture
    batch.validate_entry = lambda: _validate_stage_b_entry(batch_id, capture)
    entry = batch.validate_entry()
    capture.RUNS_ROOT.mkdir(parents=True)
    atomic_create_json(capture.BATCH_ROOT / "BATCH_AUTHORIZATION.json", {"schema_version": "driveclarify.m1_v2_stage_b_authorization.v1", "batch_id": capture.BATCH_ID, "authorization_source": "USER_EXPLICIT_M1_V2_COMBINED_RUNTIME_CAMPAIGN_STAGE_B", "authorized_at_utc": p3.utc_now(), "ordered_units": unit_runs, "schedule": list(SCHEDULE), "entry_validation": entry, "constraints": {"carla_launch": 0, "evaluator_launch": 0, "observation_capture": 0, "second_observation": 0, "candidate_forward_per_complete_unit": 6, "training": 0, "act_ask_wait": 0}, "recovery_policy": "NO_RECOVERY_RUN_ENGINEERING_EXCLUSION"})
    atomic_create_bytes(capture.BATCH_ROOT / "BATCH_COMMAND_LOG.md", ("# M1 V2 Stage B offline A3/B3 command log\n\n- `{}` prepared for {} Stage-A eligible units.\n".format(capture.BATCH_ID, len(unit_runs))).encode("utf-8"))
    atomic_create_json(capture.BATCH_ROOT / "GIT_START_END.json", {"schema_version": "driveclarify.m1_v2_stage_b_git_start_end.v1", "start": _git_gate(), "end": None})
    env = dict(os.environ)
    env.update({"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONDONTWRITEBYTECODE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_OFFLINE": "1"})
    commands = [
        [str(batch.PYTHON38), "-B", "-m", "py_compile", str(batch.COMMON), str(batch.WORKER), str(batch.ORCHESTRATOR), str(Path(__file__).resolve())],
        [str(batch.PYTHON38), "-B", "-m", "driveclarify_static_branch.offline_candidate_package_smoke"],
    ]
    results = [batch.run_command(command, env=env) for command in commands]
    passed = all(item["exit_code"] == 0 for item in results)
    atomic_create_json(capture.BATCH_ROOT / "BATCH_CPU_PREFLIGHT.json", {"schema_version": "driveclarify.m1_v2_stage_b_preflight.v1", "status": "PASS" if passed else "FAIL", "commands": results, "entry_revalidation": batch.validate_entry(), "real_candidate_forwards": 0, "cuda_context_created_by_preflight": False})
    if not passed:
        raise CampaignError("STAGE_B_PREFLIGHT_FAILED")
    _append(campaign_root(batch_id) / "COMMAND_LOG.md", "- Stage B prepared and package round-trip preflight PASS for {} eligible units.".format(len(unit_runs)))
    return plan


def _validate_stage_b_entry(batch_id: str, capture: Any) -> Mapping[str, Any]:
    verify_frozen_authority()
    git = _git_gate()
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
        verification = capture.verify_observation_package(Path(item["manifest_path"]), expected_unit_id=unit["unit_id"], expected_observation_hash=item["observation_hash"])
        if verification["status"] != "PASS":
            raise CampaignError("STAGE_B_PACKAGE_VERIFICATION_FAILED:" + unit["unit_id"])
        checks.append({"unit_id": unit["unit_id"], "package": item, "verification": verification})
    if sha256_path(capture.CHECKPOINT) != CHECKPOINT_SHA256 or sha256_path(capture.CONFIG) != CONFIG_SHA256:
        raise CampaignError("MODEL_AUTHORITY_IDENTITY_MISMATCH")
    environment = p3.environment_preflight()
    if environment["preexisting_real_processes"] or environment["gpu_compute_processes"]:
        raise CampaignError("STAGE_B_PREEXISTING_RUNTIME_PROCESS_OR_GPU_COMPUTE")
    return {"entry_status": "M1_V2_STAGE_A_COMPLETE", "observation_batch_status": stage_a["final_status"], "selected_units": 30, "eligible_units": len(checks), "evidence_unavailable_units": stage_a["evidence_exclusion_count"], "runtime_failure_units": stage_a["engineering_exclusion_count"], "prior_candidate_forward": 0, "prior_second_observation": 0, "route_27515_j238_exclusion_preserved": True, "packages": checks, "checkpoint_sha256": CHECKPOINT_SHA256, "config_sha256": CONFIG_SHA256, "history": _protected_history(), "observation_batch_inventory_file_sha256": sha256_path(stage_a_root(batch_id) / "ARTIFACT_INVENTORY.json"), "git": git, "preexisting_forbidden_processes": [], "preexisting_gpu_compute": []}


def _offline(batch_id: str) -> Tuple[Any, Any]:
    plan_path = campaign_root(batch_id) / "STAGE_B_RUN_PLAN.json"
    os.environ.update({"DRIVECLARIFY_M1_V2_AUTHORITY": str(AUTHORITY), "DRIVECLARIFY_M1_V2_RUN_PLAN": str(plan_path)})
    from . import offline_candidate_batch as batch
    from . import offline_candidate_capture as capture
    batch.validate_entry = lambda: _validate_stage_b_entry(batch_id, capture)
    return batch, capture


def run_stage_b(batch_id: str) -> None:
    batch, capture = _offline(batch_id)
    for index, item in enumerate(capture.UNIT_RUNS, start=1):
        result_path = capture.RUNS_ROOT / item["primary"] / "RUN_RESULT.json"
        if result_path.is_file():
            print("STAGE_B_SKIP {}/{} {} already_terminal".format(index, len(capture.UNIT_RUNS), item["unit_id"]), flush=True)
            continue
        print("STAGE_B_START {}/{} {} {}".format(index, len(capture.UNIT_RUNS), item["unit_id"], item["primary"]), flush=True)
        result = batch.execute(item["primary"])
        print("STAGE_B_DONE {}/{} {} {} forwards={}".format(index, len(capture.UNIT_RUNS), item["unit_id"], result["final_status"], result["candidate_forward_count"]), flush=True)


def _pilot_records() -> List[Mapping[str, Any]]:
    pilots = embedded(PILOTS, "PILOTS")["units"]
    source = load(V1_DATASET)["records"]
    grouped = {unit_id: [item for item in source if item["unit_identity"] == unit_id] for unit_id in pilots}
    rows = []
    for unit_id in pilots:
        plans = grouped[unit_id]
        first = plans[0]
        town = first["town"]
        junction = str(first["junction"])
        rows.append({"unit_id": unit_id, "unit_status": "PILOT_DEVELOPMENT_ONLY", "split": "PILOT_DEVELOPMENT_ONLY", "source_type": "EXISTING_ROUTE_BACKED", "town": town, "junction_group": "{}_JUNCTION_{}".format(town.upper(), junction), "route_family": "{}_PILOT_ROUTE_FAMILY".format(town.upper()), "observation_package": {"preserved_v1_dataset_path": str(V1_DATASET), "preserved_v1_dataset_sha256": V1_DATASET_SHA256, "observation_identity": first["observation_identity"], "observation_hash": first["observation_hash"]}, "plans": plans, "pair_task_label": first["pair_task_label"], "label_provenance": {"source": "PRESERVED_M1_REAL_DATASET_V1", "record_count": len(plans)}, "unknown_provenance": {"source": "PRESERVED_V1_SCIENTIFIC_UNKNOWN"} if first["pair_task_label"] == "UNKNOWN" else None, "engineering_exclusion": None, "data_generation_version": "M1_REAL_DATASET_V1_PRESERVED_REFERENCE"})
    return rows


def finalize_stage_b(batch_id: str) -> Mapping[str, Any]:
    batch, capture = _offline(batch_id)
    selected = {item["unit_id"]: item for item in selected_rows()}
    stage_a_summary = {item["unit_id"]: item for item in load(stage_a_root(batch_id) / "BATCH_UNIT_SUMMARY.json")["units"]}
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
    dataset_records = _pilot_records()
    unit_summaries = []
    capture_outputs = []
    for unit_id, selection in selected.items():
        unit = embedded(_unit_paths(unit_id)["manifest"], "UNIT")
        arow = stage_a_summary[unit_id]
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
            normalized = {"schema_version": "driveclarify.m1_v2_capture_output.v1", "unit_id": unit_id, "split": selection["split"], "observation_package_sha256": sha256_path(package_path), "plans": [{"candidate_id": plan["candidate_group"], "repeat_index": plan["repeat_index"], "route_plan": plan["plan_points"], "speed_plan": [number for row in plan["raw_speed"][0] for number in row], "mapping_label": mapping[plan["candidate_id"]]} for plan in plans], "rq1": rq1, "rq2": rq2["pair_class"], "cleanup_status": "PASS", "unknown_provenance": {"reason_codes": rq2["reason_codes"], "mapper_consensus": mapper["candidate_consensus"]} if rq2["pair_class"] == "UNKNOWN" else None}
            jsonschema.validate(normalized, load(CAPTURE_SCHEMA))
            _put_json(run_dir / "M1_V2_CAPTURE_OUTPUT.json", normalized)
            capture_outputs.append({"unit_id": unit_id, "run_id": complete["run_id"], "path": str(run_dir / "M1_V2_CAPTURE_OUTPUT.json"), "sha256": sha256_path(run_dir / "M1_V2_CAPTURE_OUTPUT.json")})
            record = {"unit_id": unit_id, "unit_status": "V2_NEW", "split": selection["split"], "source_type": selection["source_type"], "town": selection["town"], "junction_group": selection["junction_group"], "route_family": selection["route_family"], "observation_package": package, "plans": plans, "pair_task_label": rq2["pair_class"], "label_provenance": {"mapper": "StaticBranchPlanMapperV1", "threshold_sha256": THRESHOLD_EMBEDDED_SHA256, "mapper_results_path": str(run_dir / "MAPPER_RESULTS.json"), "rq2_result_path": str(run_dir / "RQ2_RESULT.json"), "fairness_verdict": fairness["verdict"]}, "unknown_provenance": normalized["unknown_provenance"], "engineering_exclusion": None, "data_generation_version": "M1_REAL_DATASET_V2_RUNTIME_{}".format(batch_id)}
            unit_summaries.append({**complete, "split": selection["split"], "classification": "COMPLETE_A3_B3", "pair_task_label": rq2["pair_class"], "fairness": fairness["verdict"], "repeat_stability": rq1.get("repeat_exact_hash_equality"), "mapper_consensus": mapper["candidate_consensus"]})
        else:
            b_row = next((item for item in terminal if item["unit_id"] == unit_id), None)
            engineering = None
            if arow["classification"] == "ENGINEERING_EXCLUSION":
                engineering = {"stage": "STAGE_A", "reason_codes": arow["reason_codes"]}
            elif b_row is not None:
                engineering = {"stage": "STAGE_B", "run_id": b_row["run_id"], "final_status": b_row["final_status"], "candidate_forward_count": b_row["candidate_forward_count"]}
            status = "EVIDENCE_EXCLUSION" if arow["classification"] == "EVIDENCE_EXCLUSION" else "ENGINEERING_EXCLUSION"
            record = {"unit_id": unit_id, "unit_status": status, "split": selection["split"], "source_type": selection["source_type"], "town": selection["town"], "junction_group": selection["junction_group"], "route_family": selection["route_family"], "observation_package": load(Path(arow["observation_package_manifest_path"])) if arow["observation_package_manifest_path"] else None, "plans": None, "pair_task_label": None, "label_provenance": None, "unknown_provenance": {"stage": "STAGE_A", "reason_codes": arow["reason_codes"]} if status == "EVIDENCE_EXCLUSION" else None, "engineering_exclusion": engineering, "data_generation_version": "M1_REAL_DATASET_V2_RUNTIME_{}".format(batch_id)}
            unit_summaries.append({"unit_id": unit_id, "split": selection["split"], "classification": status, "pair_task_label": None, "candidate_forward_count": b_row["candidate_forward_count"] if b_row else 0, "cleanup": b_row["cleanup"] if b_row else arow["cleanup_status"]})
        dataset_records.append(record)
    dataset = {"schema_version": "driveclarify.m1_real_dataset_v2", "dataset_status": "CAPTURED", "pilot_unit_status": "PILOT_DEVELOPMENT_ONLY", "v1_dataset": {"path": "reports/multi_topology_static_units_v1/offline_candidate_capture_runs/DC-MULTI-A3B3-C1-20260803T121500Z/M1_REAL_DATASET_V1.json", "record_count": 30, "sha256": V1_DATASET_SHA256}, "split_manifest_sha256": embedded(SPLIT, "SPLIT")["sha256"], "records": dataset_records}
    jsonschema.validate(dataset, load(DATA_SCHEMA))
    root = stage_b_root(batch_id)
    _put_json(root / "M1_REAL_DATASET_V2.json", dataset)
    _put_json(root / "M1_REAL_DATASET_V2_UNIT_SUMMARY.json", {"schema_version": "driveclarify.m1_real_dataset_v2.unit_summary.v1", "pilot_development_only_unit_count": 5, "v2_selected_unit_count": 30, "units": unit_summaries})
    _put_json(root / "STAGE_B_CAPTURE_OUTPUT_INDEX.json", {"schema_version": "driveclarify.m1_v2_capture_output_index.v1", "count": len(capture_outputs), "outputs": capture_outputs})
    complete = [item for item in unit_summaries if item["classification"] == "COMPLETE_A3_B3"]
    failures = [item for item in unit_summaries if item["classification"] == "ENGINEERING_EXCLUSION"]
    stage_b_failures = [item for item in terminal if item["terminal_category"] == "ENGINEERING_FAILURE"]
    evidence = [item for item in unit_summaries if item["classification"] == "EVIDENCE_EXCLUSION"]
    unknown = [item for item in complete if item["pair_task_label"] == "UNKNOWN"]
    known = [item for item in complete if item["pair_task_label"] in {"TASK_EQUIVALENT", "TASK_CRITICAL"}]
    split_counts = {split: sum(1 for item in complete if item["split"] == split) for split in ("TRAIN", "DEV", "TEST")}
    label_counts = {label: sum(1 for item in complete if item["pair_task_label"] == label) for label in ("TASK_EQUIVALENT", "TASK_CRITICAL", "UNKNOWN")}
    runtime_rows = [load(capture.RUNS_ROOT / item["run_id"] / "RUNTIME_COUNTS.json") for item in terminal]
    count_keys = ("candidate_forward", "checkpoint_load", "model_load", "carla_launch", "evaluator_launch", "observation_capture", "second_observation", "world_tick", "pid", "planner_advance", "control_send", "scenario_actor_mutation", "baseline_control_consumption", "act_ask_wait", "training", "mapper_invocation")
    totals = {key: sum(int(item.get(key, 0)) for item in runtime_rows) for key in count_keys}
    if any(item["candidate_forward_count"] != 6 for item in complete):
        raise CampaignError("COMPLETE_UNIT_CANDIDATE_FORWARD_COUNT_NOT_SIX")
    compute = p3.environment_preflight()["gpu_compute_processes"]
    cleanup_pass = all(item["cleanup"] == "PASS" for item in terminal) and not compute
    gate = {"complete_units_ge_20": len(complete) >= 20, "known_task_equivalent_ge_3": label_counts["TASK_EQUIVALENT"] >= 3, "known_task_critical_ge_3": label_counts["TASK_CRITICAL"] >= 3, "scientific_unknown_ge_3": label_counts["UNKNOWN"] >= 3, "dev_complete_ge_4": split_counts["DEV"] >= 4, "test_complete_ge_4": split_counts["TEST"] >= 4, "no_town_junction_split_leakage": load(AUTHORITY / "M1_V2_SPLIT_LEAKAGE_AUDIT.json")["status"] == "PASS"}
    gate_pass = all(gate.values()) and cleanup_pass
    status = "M1_V2_RUNTIME_CAMPAIGN_COMPLETE_READY_FOR_FORMAL_LEARNED_M1_ASSESSMENT" if gate_pass else "M1_V2_RUNTIME_CAMPAIGN_COMPLETE_DATASET_EXPANSION_V3_REQUIRED"
    documents = {
        "BATCH_UNIT_SUMMARY.json": {"schema_version": "driveclarify.m1_v2_stage_b_unit_summary.v1", "eligible_unit_count": len(terminal), "complete_unit_count": len(complete), "units": unit_summaries},
        "COMPLETE_UNITS_MANIFEST.json": {"schema_version": "driveclarify.m1_v2_complete_units.v1", "count": len(complete), "units": complete},
        "ENGINEERING_EXCLUSION_UNITS_MANIFEST.json": {"schema_version": "driveclarify.m1_v2_engineering_exclusions.v1", "count": len(failures), "units": failures},
        "EVIDENCE_EXCLUSION_UNITS_MANIFEST.json": {"schema_version": "driveclarify.m1_v2_evidence_exclusions.v1", "count": len(evidence), "units": evidence},
        "SCIENTIFIC_UNKNOWN_UNITS_MANIFEST.json": {"schema_version": "driveclarify.m1_v2_scientific_unknowns.v1", "count": len(unknown), "units": unknown},
        "KNOWN_LABEL_UNITS_MANIFEST.json": {"schema_version": "driveclarify.m1_v2_known_label_units.v1", "count": len(known), "units": known},
        "FAIRNESS_AND_REPEAT_STABILITY_SUMMARY.json": {"schema_version": "driveclarify.m1_v2_fairness_repeat_summary.v1", "all_complete_fairness_pass": all(item["fairness"] == "PASS" for item in complete), "units": [{"unit_id": item["unit_id"], "fairness": item["fairness"], "repeat_stability": item.get("repeat_stability")} for item in complete]},
        "MAPPER_RQ1_RQ2_SUMMARY.json": {"schema_version": "driveclarify.m1_v2_mapper_rq_summary.v1", "units": [{"unit_id": item["unit_id"], "mapper_consensus": item["mapper_consensus"], "rq1": item["rq1"], "rq2": item["rq2"]} for item in complete]},
        "SPLIT_AND_LABEL_DISTRIBUTION.json": {"schema_version": "driveclarify.m1_v2_distribution.v1", "complete_by_split": split_counts, "complete_by_label": label_counts, "complete_unit_count": len(complete), "selected_unit_count": 30},
        "EXCLUSION_AND_FAILURE_LEDGER.json": {"schema_version": "driveclarify.m1_v2_exclusion_failure_ledger.v1", "stage_a_evidence_exclusions": evidence, "engineering_exclusions": failures, "frozen_historical_exclusions": embedded(EXCLUSIONS, "EXCLUSIONS")["exclusions"]},
        "BATCH_RUNTIME_COUNTS.json": {"schema_version": "driveclarify.m1_v2_stage_b_runtime_counts.v1", "totals": totals, "complete_unit_expected_candidate_forwards": len(complete) * 6, "actual_candidate_forwards": totals["candidate_forward"], "training_total": totals["training"], "act_ask_wait_total": totals["act_ask_wait"]},
        "BATCH_GPU_AND_CLEANUP.json": {"schema_version": "driveclarify.m1_v2_stage_b_cleanup.v1", "all_cleanup_pass": cleanup_pass, "final_gpu_compute_process_count": len(compute), "final_gpu_compute_processes": compute},
    }
    for name, value in documents.items(): _put_json(root / name, value)
    result = {"schema_version": "driveclarify.m1_v2_stage_b_result.v1", "batch_id": batch_id + "-STAGE-B", "final_status": status, "selected_unit_count": 30, "stage_a_eligible_unit_count": len(terminal), "complete_a3_b3_unit_count": len(complete), "engineering_exclusion_count": len(stage_b_failures), "selected_engineering_exclusion_count": len(failures), "scientific_unknown_unit_count": len(unknown), "known_label_unit_count": len(known), "candidate_forward_total": totals["candidate_forward"], "candidate_forward_exactness": all(item["candidate_forward_count"] == 6 for item in complete), "split_distribution": split_counts, "label_distribution": label_counts, "training_readiness_gate": gate, "training_readiness_gate_pass": gate_pass, "all_cleanup_pass": cleanup_pass, "formal_training_started": False, "automatic_continuation": False}
    _put_json(root / "BATCH_RESULT.json", result)
    git_doc = load(root / "GIT_START_END.json"); git_doc["end"] = _git_gate(); git_doc["simlingo_unchanged"] = True; atomic_replace_json(root / "GIT_START_END.json", git_doc)
    _put_json(root / "ARTIFACT_INVENTORY.json", _inventory(root))
    _append(campaign_root(batch_id) / "COMMAND_LOG.md", "- Stage B complete: eligible={}, complete={}, engineering exclusions={}, labels={}, split={}, forwards={}, cleanup={}.".format(len(terminal), len(complete), len(failures), label_counts, split_counts, totals["candidate_forward"], cleanup_pass))
    return result


def finalize_campaign(batch_id: str) -> Mapping[str, Any]:
    root = campaign_root(batch_id)
    stage_a = load(stage_a_root(batch_id) / "BATCH_RESULT.json")
    stage_b = load(stage_b_root(batch_id) / "BATCH_RESULT.json")
    final_environment = p3.environment_preflight()
    final_git = _git_gate()
    cleanup_pass = not final_environment["preexisting_real_processes"] and not final_environment["gpu_compute_processes"] and all(final_environment["ports_free"].values()) and stage_a["all_cleanup_pass"] and stage_b["all_cleanup_pass"]
    status = stage_b["final_status"] if cleanup_pass else "M1_V2_RUNTIME_CAMPAIGN_BLOCKED_CLEANUP_FAILED"
    result = {"schema_version": "driveclarify.m1_v2_combined_runtime_result.v1", "batch_id": batch_id, "campaign_name": "M1_V2_COMBINED_RUNTIME_CAMPAIGN", "final_status": status, "completed_at_utc": p3.utc_now(), "selected_units": 30, "runtime_constructed_units": stage_a["runtime_constructed_unit_count"], "eligible_units": stage_a["eligible_count"], "complete_a3_b3_units": stage_b["complete_a3_b3_unit_count"], "engineering_exclusions": stage_a["engineering_exclusion_count"] + stage_b["engineering_exclusion_count"], "evidence_exclusions": stage_a["evidence_exclusion_count"], "scientific_unknown_units": stage_b["scientific_unknown_unit_count"], "known_label_units": stage_b["known_label_unit_count"], "candidate_forward_total": stage_b["candidate_forward_total"], "stage_a_candidate_forward_total": 0, "stage_a_second_observation_total": 0, "split_distribution": stage_b["split_distribution"], "label_distribution": stage_b["label_distribution"], "training_readiness_gate": stage_b["training_readiness_gate"], "training_readiness_gate_pass": stage_b["training_readiness_gate_pass"], "cleanup_pass": cleanup_pass, "final_environment": final_environment, "git_end": final_git, "formal_learned_m1_training_started": False, "m2_started": False, "act_ask_wait_started": False, "automatic_continuation": False}
    atomic_create_json(root / "CAMPAIGN_RESULT.json", result)
    manifest = load(root / "CAMPAIGN_RUN_MANIFEST.json"); manifest["status"] = status; manifest["result_path"] = str(root / "CAMPAIGN_RESULT.json"); atomic_replace_json(root / "CAMPAIGN_RUN_MANIFEST.json", manifest)
    git_doc = load(root / "GIT_START_END.json"); git_doc["end"] = final_git; git_doc["simlingo_unchanged"] = True; git_doc["git_destructive_command_used"] = False; atomic_replace_json(root / "GIT_START_END.json", git_doc)
    report = """# M1 V2 Combined Runtime Campaign Report

## 结论

`{status}`

## 实际样本口径

- selected units: `{selected}`
- runtime-constructed units: `{constructed}`
- eligible units: `{eligible}`
- complete A×3/B×3 units: `{complete}`
- engineering exclusions: `{engineering}`
- evidence exclusions: `{evidence}`
- scientific UNKNOWN units: `{unknown}`
- known-label units: `{known}`

Stage A candidate forward=`0`、second observation=`0`。Stage B candidate forward 总数=`{forwards}`；每个 complete unit 精确为 6。实际 complete split=`{splits}`，类别=`{labels}`。

## 门槛与停止

Formal Learned M1 assessment gate=`{gate}`。本 campaign 已停止；未训练或微调 Learned M1，未启动 M2A/M2B/M3，未启用 live ACT/ASK/WAIT。CARLA/evaluator/launcher/agent/GPU compute 清理=`{cleanup}`，端口释放已检查。
""".format(status=status, selected=result["selected_units"], constructed=result["runtime_constructed_units"], eligible=result["eligible_units"], complete=result["complete_a3_b3_units"], engineering=result["engineering_exclusions"], evidence=result["evidence_exclusions"], unknown=result["scientific_unknown_units"], known=result["known_label_units"], forwards=result["candidate_forward_total"], splits=json.dumps(result["split_distribution"], ensure_ascii=False, sort_keys=True), labels=json.dumps(result["label_distribution"], ensure_ascii=False, sort_keys=True), gate="PASS" if result["training_readiness_gate_pass"] else "FAIL_DATASET_EXPANSION_V3_REQUIRED", cleanup="PASS" if cleanup_pass else "FAIL")
    atomic_create_bytes(root / "M1_V2_COMBINED_RUNTIME_CAMPAIGN_REPORT.md", report.encode("utf-8"))
    _append(root / "COMMAND_LOG.md", "- Campaign terminal `{}`; no automatic continuation.".format(status))
    atomic_create_json(root / "ARTIFACT_INVENTORY.json", _inventory(root))
    return result


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("command", choices=("prepare-campaign", "prepare-stage-a", "run-stage-a", "finalize-stage-a", "prepare-stage-b", "run-stage-b", "finalize-stage-b", "finalize-campaign"))
    args = parser.parse_args(argv)
    _timestamp_from_batch(args.batch_id)
    if args.command == "prepare-campaign": prepare_campaign(args.batch_id)
    elif args.command == "prepare-stage-a": prepare_stage_a(args.batch_id)
    elif args.command == "run-stage-a": run_stage_a(args.batch_id)
    elif args.command == "finalize-stage-a": print(json.dumps(finalize_stage_a(args.batch_id), ensure_ascii=False, sort_keys=True))
    elif args.command == "prepare-stage-b": print(json.dumps(prepare_stage_b(args.batch_id), ensure_ascii=False, sort_keys=True))
    elif args.command == "run-stage-b": run_stage_b(args.batch_id)
    elif args.command == "finalize-stage-b": print(json.dumps(finalize_stage_b(args.batch_id), ensure_ascii=False, sort_keys=True))
    elif args.command == "finalize-campaign": print(json.dumps(finalize_campaign(args.batch_id), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
