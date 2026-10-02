#!/usr/bin/env python3
"""Prospective RQ2-T-CG Formal V3 evaluability campaign.

Formal V2 remains sealed.  V3 reuses its final eight scientific scene and
route contracts byte-for-byte and changes only the prospectively frozen
primary-evaluability denominator/gate.
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import itertools
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driveclarify_grounded_language_v1_extension_e1_r1 import backend as episode_backend
from driveclarify_paper_mvp_stage6b import backend as native
from driveclarify_rq2_t.measurement import canonical_sha256
from driveclarify_rq2_t_cg_formal_execution.builder import build_formal_episode
from driveclarify_rq2_t_cg_formal_execution.child_admission_v2 import (
    FirstLegalRowWatchdog,
    build_exact_formal_child_environment,
    persist_child_construction_receipt,
)
from driveclarify_rq2_t_cg_formal_execution.routes import static_route_admission
from driveclarify_rq2_t_cg_formal_freeze.contracts import (
    ANALYSIS_PLAN as V2_ANALYSIS_PLAN,
    ENDPOINTS,
    HYPOTHESES,
    RULES,
    VIEWS,
)
from driveclarify_rq2_t_cg_formal_freeze.protocols import planned_run_order
from driveclarify_rq2_t_cg_formal_freeze.scenes import SCENE_ORDER
from driveclarify_rq2_t_cg_formal_v3.evaluability import (
    EVALUABLE,
    INTEGRITY_INVALID,
    NON_EVALUABLE_NATIVE_NONCOMPLETION,
    ZERO_EXPOSURE_INFRASTRUCTURE_FAILURE,
    classify_episode,
    primary_gate,
    prospective_gate_possible,
)
from tools import run_rq2_t_cg_ord_late_replacement as v2_entry
from tools.run_rq2_t_cg_formal_v2 import _no_control_effect
from tools.run_rq2_t_cg_v2_calibration import _fresh_batch
from tools.run_rq2_t_e2_v3 import _repair_irrelevant_display_process_false_positive


REPORT = ROOT / "reports" / "driveclarify_rq2_t_cg_formal_v3_prospective_evaluability_and_execution_v1"
V2_REPORT = ROOT / "reports" / "driveclarify_rq2_t_cg_v2_ord_late_reveal_replacement_and_formal_v1"
V2_SCENES = V2_REPORT / "FORMAL_V2_SCENES"
V2_ROUTES = V2_REPORT / "FORMAL_V2_ROUTES"
V2_SEAM_SCENES = V2_REPORT / "EXECUTION_SEAM_CONFIGS"
V2_SEAM_ROUTES = V2_REPORT / "EXECUTION_SEAM_ROUTES"
V3_SCENES = REPORT / "FORMAL_V3_SCENES"
V3_ROUTES = REPORT / "FORMAL_V3_ROUTES"
V3_RUNS = REPORT / "FORMAL_V3_RUNS"
ENGINEERING = REPORT / "ENGINEERING_ONLY"
SIMLINGO = Path("/home/buaa/wrh/simlingo")
CHECKPOINT = SIMLINGO / "outputs/simlingo/checkpoints/epoch=013.ckpt/pytorch_model.pt"

V2_FINAL_STATUS = "FORMAL_V2_EXECUTION_INTEGRITY_NOT_CLOSED"
V2_PLANNED = 48
V2_EXPOSED = 25
V2_VALID = 24
V2_INVALID_CELL = "RQ2TCG-FV2-ORD-ASYNC-S04-80ABD7D421F3615E"
V2_FINAL_FREEZE_DIGEST = "a4b184880589265ccf342da99316650af19263305f233d11bc040683fb213a0a"
CALIBRATION_FREEZE_DIGEST = "94a933897ded5c6d48df35020e79373db04a362c89d6338b78e10677f556d371"
CHECKPOINT_SHA256 = "ec8943723d266ee9f5f56f45d153a163b22616960bfccb741965ea5daa700d28"

CRITICAL_INFRACTIONS = (
    "collisions_layout", "collisions_pedestrian", "collisions_vehicle", "red_light",
    "stop_infraction", "outside_route_lanes", "yield_emergency_vehicle_infractions",
    "scenario_timeouts", "route_dev", "vehicle_blocked", "route_timeout",
)

SOURCE_PATHS = (
    "driveclarify_rq2_t/measurement.py",
    "driveclarify_rq2_t_cg/contracts.py",
    "driveclarify_rq2_t_cg/interface.py",
    "driveclarify_rq2_t_cg/memory.py",
    "driveclarify_rq2_t_cg/rules.py",
    "driveclarify_rq2_t_cg_formal_execution/builder.py",
    "driveclarify_rq2_t_cg_formal_execution/child_admission_v2.py",
    "driveclarify_rq2_t_cg_formal_execution/native_scenario.py",
    "driveclarify_rq2_t_cg_formal_execution/route_binding_v2.py",
    "driveclarify_rq2_t_cg_formal_execution/routes.py",
    "driveclarify_rq2_t_cg_formal_execution/scene_io.py",
    "driveclarify_rq2_t_cg_background_traffic/policy.py",
    "driveclarify_rq2_t_cg_v2_calibration/scenes.py",
    "driveclarify_rq2_t_cg_formal_v3/__init__.py",
    "driveclarify_rq2_t_cg_formal_v3/evaluability.py",
    "tests/rq2_t_cg_formal_v3/test_evaluability.py",
    "tools/run_rq2_t_cg_formal_v3.py",
)
EXTERNAL_SOURCE_PATHS = (
    Path("/home/buaa/wrh/simlingo/team_code/driveclarify_probe_hook.py"),
    Path("/home/buaa/wrh/simlingo/team_code/nav_planner.py"),
    Path("/home/buaa/wrh/simlingo/team_code/agent_simlingo.py"),
    Path("/home/buaa/wrh/simlingo/Bench2Drive/leaderboard/leaderboard/scenarios/route_scenario.py"),
)

V3_EVALUABILITY_CONTRACT = {
    "estimand": (
        "Among prospectively eligible native traces reaching the frozen scientific observation "
        "endpoint, does B2 temporal retention improve evidence sufficiency/actionable-window "
        "availability relative to B1?"
    ),
    "primary_evaluable_requires": [
        "required evidence/event observation interval",
        "commitment and frozen natural scientific endpoint",
        "complete paired B1/B2 builder output",
        "complete source-frame identity",
        "complete actionability/deadline information",
        "no integrity corruption",
    ],
    "noncompletion_is_not_b1_or_b2_outcome": True,
    "evaluability_reads_b1_b2_outcome": False,
    "planned_cells": 48,
    "total_primary_evaluable_minimum": 40,
    "per_scene_primary_evaluable_minimum": 5,
    "per_scene_planned": 6,
    "replacement_seeds": 0,
    "scientific_retries": 0,
    "zero_exposure_infrastructure_retries_permitted": 0,
    "frame_rows_are_independent_scientific_n": False,
}

V3_ANALYSIS_PLAN = {
    "primary_unit": "scene x shared-seed episode",
    "primary_population": "PRIMARY-EVALUABLE episodes only",
    "async_positive_scene_codes": ["REF-ASYNC", "LMK-ASYNC", "ORD-ASYNC"],
    "sync_control_scene_codes": ["REF-SYNC", "LMK-SYNC"],
    "co_primary_endpoints": ["precommitment_sufficiency", "actionable_window_presence"],
    "paired_test": "two-sided exact McNemar per co-primary endpoint with Holm adjustment",
    "h_cg1_supported_if": (
        "both pooled ASYNC paired B2-B1 effects are positive and both Holm-adjusted p values < 0.05"
    ),
    "scene_family_superiority_not_individually_required": True,
    "confidence_interval": (
        "95% exhaustive shared-seed cluster bootstrap percentile interval over evaluable paired episodes"
    ),
    "continuous_descriptives": ["first-sufficiency TTCmt", "actionable-window duration"],
    "h_cg2": "descriptive SYNC memory-placebo comparison; no equivalence claim",
    "h_cg3": "offline same-trace R-EVIDENCE-ONLY, R-TIME-ONLY, R-JOINT classifications",
    "h_cg4": "eligible negative-control exact event counts and Clopper-Pearson bounds",
    "complete_scene_sensitivity": (
        "restrict to 6/6-evaluable scene families and recompute direction of pooled ASYNC "
        "precommitment-sufficiency B2-B1 effect"
    ),
    "missing_native_noncompletion_imputed": False,
    "v2_analysis_plan_preserved_reference_digest": canonical_sha256(V2_ANALYSIS_PLAN),
}


def _configure_legacy() -> None:
    # Uses the already-qualified native episode/spec/environment implementation.
    # No legacy report is written by V3.
    v2_entry._configure()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path, default: Any = None) -> Any:
    return default if not path.is_file() else json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(str(temporary), str(path))


def _write_md(path: Path, title: str, lines: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# " + title + "\n\n" + "\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _append_command(command: str, status: str) -> None:
    REPORT.mkdir(parents=True, exist_ok=True)
    with (REPORT / "COMMAND_LOG.md").open("a", encoding="utf-8") as handle:
        handle.write("- `{}` -> `{}`\n".format(command, status))


def _command(args: Sequence[str], cwd: Path = ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(list(args), cwd=str(cwd), text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, check=False)


def _source_freeze() -> Mapping[str, Any]:
    files = [
        {"path": path, "bytes": (ROOT / path).stat().st_size, "sha256": _sha(ROOT / path)}
        for path in SOURCE_PATHS
    ]
    external = [
        {"path": str(path), "bytes": path.stat().st_size, "sha256": _sha(path)}
        for path in EXTERNAL_SOURCE_PATHS
    ]
    v2_source = _load(V2_REPORT / "SOURCE_FREEZE_RECEIPT.json", {})
    value = {
        "schema_version": "driveclarify.rq2_t_cg.formal_v3.source_freeze.v1",
        "files": files,
        "external_files": external,
        "file_count": len(files),
        "external_file_count": len(external),
        "checkpoint_sha256": _sha(CHECKPOINT),
        "checkpoint_unchanged": _sha(CHECKPOINT) == CHECKPOINT_SHA256,
        "calibration_freeze_digest": CALIBRATION_FREEZE_DIGEST,
        "v2_final_source_freeze_digest": v2_source.get("receipt_digest"),
        "v2_science_source_hashes_unchanged": all(
            (ROOT / row["path"]).is_file() and _sha(ROOT / row["path"]) == row["sha256"]
            for row in v2_source.get("files", [])
        ),
        "source_mutations_after_v2": [
            "driveclarify_rq2_t_cg_formal_v3/evaluability.py",
            "tools/run_rq2_t_cg_formal_v3.py",
            "tests/rq2_t_cg_formal_v3/test_evaluability.py",
        ],
        "scientific_mechanism_mutations": [],
        "PID_controller_changes": 0,
        "checkpoint_changes": 0,
    }
    value["pass"] = (
        value["checkpoint_unchanged"] and value["v2_science_source_hashes_unchanged"]
    )
    value["receipt_digest"] = canonical_sha256(value)
    return value


def _verify_source_freeze() -> Mapping[str, Any]:
    receipt = _load(REPORT / "SOURCE_FREEZE_RECEIPT.json", {})
    failures = []
    for row in receipt.get("files", []):
        path = ROOT / row["path"]
        if not path.is_file() or _sha(path) != row["sha256"]:
            failures.append(row["path"])
    for row in receipt.get("external_files", []):
        path = Path(row["path"])
        if not path.is_file() or _sha(path) != row["sha256"]:
            failures.append(row["path"])
    if _sha(CHECKPOINT) != receipt.get("checkpoint_sha256"):
        failures.append(str(CHECKPOINT))
    return {"pass": not failures, "drift_paths": failures}


def seal_formal_v2() -> Mapping[str, Any]:
    existing = REPORT / "FORMAL_V2_EXCLUSION_RECEIPT.json"
    if existing.is_file():
        return _load(existing)
    final = _load(V2_REPORT / "FINAL_VALIDATION_RECEIPT.json", {})
    roster = _load(V2_REPORT / "FORMAL_V2_ROSTER.json", {})
    ledger = _load(V2_REPORT / "FORMAL_V2_EXECUTION_LEDGER.json", {})
    audit = _load(V2_REPORT / "FINAL_INDEPENDENT_INTEGRITY_AUDIT.json", {})
    critical_paths = (
        "FINAL_VALIDATION_RECEIPT.json", "FORMAL_V2_ROSTER.json",
        "FORMAL_V2_EXECUTION_LEDGER.json", "FINAL_INDEPENDENT_INTEGRITY_AUDIT.json",
    )
    checks = {
        "v2_status_exact": final.get("status") == V2_FINAL_STATUS,
        "planned_48": roster.get("cell_count") == V2_PLANNED,
        "exposed_25": ledger.get("formal_scientific_exposures") == V2_EXPOSED,
        "valid_24": ledger.get("formal_episode_valid_count") == V2_VALID,
        "first_invalid_exact": ledger.get("entries", [])[-1].get("cell_id") == V2_INVALID_CELL,
        "scientific_retries_zero": ledger.get("formal_scientific_retry_count") == 0,
        "infrastructure_retries_zero": ledger.get("formal_infrastructure_retry_count") == 0,
        "hcg_not_run": ledger.get("hcg_analysis_legally_run") is False,
        "all_48_cells_preserved": len(roster.get("cells", [])) == 48,
    }
    value = {
        "schema_version": "driveclarify.rq2_t_cg.formal_v3.v2_exclusion.v1",
        "status": "PASS_FORMAL_V2_PERMANENTLY_SEALED_AND_EXCLUDED" if all(checks.values()) else "FAIL_FORMAL_V2_SEAL",
        "checks": checks,
        "failed_checks": [key for key, passed in checks.items() if not passed],
        "v2_final_status": final.get("status"),
        "v2_final_freeze_digest": V2_FINAL_FREEZE_DIGEST,
        "v2_roster_digest": roster.get("roster_digest"),
        "v2_seed_values": roster.get("seed_values", []),
        "v2_planned": V2_PLANNED,
        "v2_exposed": V2_EXPOSED,
        "v2_valid": V2_VALID,
        "v2_first_invalid_cell": V2_INVALID_CELL,
        "v2_hcg_analysis_run": False,
        "protocol_development_use_only": True,
        "all_formal_v2_cells": [
            {
                "cell_id": row["cell_id"], "scene_code": row["scene_code"],
                "seed_slot": row["seed_slot"], "seed": row["seed"],
                "status": "EXCLUDED_FROM_FORMAL_V3",
            }
            for row in roster.get("cells", [])
        ],
        "critical_artifact_sha256": {name: _sha(V2_REPORT / name) for name in critical_paths},
        "independent_audit_digest": audit.get("audit_digest") or audit.get("receipt_digest"),
    }
    value["receipt_digest"] = canonical_sha256(value)
    _write_json(existing, value)
    if not all(checks.values()):
        raise RuntimeError("FORMAL_V2_SEAL_FAILED")
    return value


def _copy_frozen_scenes() -> Mapping[str, Any]:
    final_freeze = _load(V2_REPORT / "FINAL_FORMAL_V2_FREEZE_RECEIPT.json", {})
    V3_SCENES.mkdir(parents=True, exist_ok=True)
    V3_ROUTES.mkdir(parents=True, exist_ok=True)
    rows = {}
    for code in SCENE_ORDER:
        source_scene = V2_SCENES / (code + ".json")
        source_route = V2_ROUTES / (code + ".xml")
        target_scene = V3_SCENES / source_scene.name
        target_route = V3_ROUTES / source_route.name
        if not target_scene.exists():
            shutil.copy2(str(source_scene), str(target_scene))
        if not target_route.exists():
            shutil.copy2(str(source_route), str(target_route))
        scene = _load(target_scene)
        admission = static_route_admission(target_route, scene)
        rows[code] = {
            "scene_sha256": _sha(target_scene),
            "scene_contract_digest": scene["formal_scene_digest"],
            "route_sha256": _sha(target_route),
            "route_admission": admission["status"],
            "scene_byte_exact_v2": _sha(target_scene) == _sha(source_scene),
            "route_byte_exact_v2": _sha(target_route) == _sha(source_route),
            "scene_digest_exact_v2_freeze": (
                scene["formal_scene_digest"] == final_freeze["scene_contract_hashes"][code]
            ),
            "route_hash_exact_v2_freeze": (
                _sha(target_route) == final_freeze["native_route_hashes"][code]
            ),
        }
    result = {
        "status": "PASS_EIGHT_V2_SCIENTIFIC_SCENES_REUSED_EXACTLY" if all(
            row["route_admission"] == "PASS_STATIC_FORMAL_ROUTE_ADMISSION"
            and row["scene_byte_exact_v2"] and row["route_byte_exact_v2"]
            and row["scene_digest_exact_v2_freeze"] and row["route_hash_exact_v2_freeze"]
            for row in rows.values()
        ) else "FAIL_V3_SCENE_REUSE",
        "scenes": rows,
        "scene_count": len(rows),
    }
    result["receipt_digest"] = canonical_sha256(result)
    _write_json(REPORT / "V3_SCIENCE_SCENE_REUSE_RECEIPT.json", result)
    return result


def focused_evaluability_tests() -> Mapping[str, Any]:
    default = _command((
        "env", "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1", sys.executable, "-m", "pytest", "-q",
        "tests/rq2_t_cg_formal_v3/test_evaluability.py",
    ))
    py38 = _command((
        "env", "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1", "/home/buaa/anaconda3/envs/simlingo/bin/python",
        "-m", "pytest", "-q", "tests/rq2_t_cg_formal_v3/test_evaluability.py",
    ))
    value = {
        "schema_version": "driveclarify.rq2_t_cg.formal_v3.focused_tests.v1",
        "status": "PASS_13_FOCUSED_V3_EVALUABILITY_TESTS" if (
            default.returncode == 0 and py38.returncode == 0
            and "13 passed" in default.stdout and "13 passed" in py38.stdout
        ) else "FAIL_V3_EVALUABILITY_TESTS",
        "default_return_code": default.returncode,
        "python38_return_code": py38.returncode,
        "default_output": default.stdout[-8000:],
        "python38_output": py38.stdout[-8000:],
        "test_count": 13,
        "full_historical_regression_rerun": False,
    }
    value["receipt_digest"] = canonical_sha256(value)
    _write_json(REPORT / "FORMAL_V3_EVALUABILITY_TEST_RECEIPT.json", value)
    return value


def _latest_jsonl_row(path: Path) -> Mapping[str, Any]:
    if not path.is_file() or path.stat().st_size == 0:
        return {}
    last = None
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                last = line
    if last is None:
        return {}
    try:
        return json.loads(last)
    except json.JSONDecodeError:
        return {}


def _ego_speed(row: Mapping[str, Any]) -> Any:
    ego = row.get("ego") if isinstance(row.get("ego"), Mapping) else {}
    velocity = ego.get("velocity_world_mps_xyz") or ego.get("velocity_mps_xyz")
    if isinstance(velocity, Sequence) and len(velocity) >= 3:
        return math.sqrt(sum(float(value) ** 2 for value in velocity[:3]))
    if isinstance(ego.get("speed_mps"), (int, float)):
        return float(ego["speed_mps"])
    return None


def _timing_operands(row: Mapping[str, Any]) -> Mapping[str, Any]:
    control = row.get("baseline_control") if isinstance(row.get("baseline_control"), Mapping) else {}
    return {
        "CARLA_GameTime_s": row.get("agent_timestamp_seconds") or row.get("gametime_seconds"),
        "simulation_timestamp_s": row.get("agent_timestamp_seconds") or row.get("gametime_seconds"),
        "ego_speed_mps": _ego_speed(row),
        "throttle": control.get("throttle"),
        "brake": control.get("brake"),
    }


class StartupTimingObserver:
    def __init__(self, output: Path) -> None:
        self.output = output
        self.process_launch_monotonic = None
        self.events = {}

    def after_launch(self, process: Any) -> None:
        self.process_launch_monotonic = time.monotonic()
        self._record("process_launch", self.process_launch_monotonic, {})

    def _record(self, name: str, now: float, row: Mapping[str, Any]) -> None:
        if name in self.events:
            return
        launch = self.process_launch_monotonic if self.process_launch_monotonic is not None else now
        self.events[name] = {
            "wall_seconds_from_process_launch": max(0.0, float(now - launch)),
            **_timing_operands(row),
        }

    def __call__(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
        now = float(context["now_monotonic"])
        text = ""
        stdout = Path(str(context["stdout_path"]))
        if stdout.is_file():
            try:
                text = stdout.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
        latest = _latest_jsonl_row(self.output / "post_hoc_world_state.jsonl")
        if "load_world success" in text:
            self._record("CARLA_ready", now, latest)
        if "> Setting up the agent" in text:
            self._record("agent_setup_started", now, latest)
        if "trainable params:" in text:
            self._record("checkpoint_model_ready", now, latest)
        if "> Running the route" in text:
            self._record("agent_setup_ready", now, latest)
        probe = _latest_jsonl_row(self.output / "probe" / "probe.jsonl")
        if probe:
            self._record("first_VLA_forward", now, latest or probe)
        if (self.output / "FIRST_LEGAL_SOURCE_ROW_RECEIPT.json").is_file():
            self._record("first_legal_source_row", now, latest)
        speed = _ego_speed(latest)
        if speed is not None and speed > 0.05:
            self._record("first_nonzero_ego_motion", now, latest)
        return {}


def startup_audit(wall_timeout_s: float) -> Mapping[str, Any]:
    receipt_path = REPORT / "STARTUP_TIMING_AUDIT_RECEIPT.json"
    if receipt_path.is_file():
        return _load(receipt_path)
    _configure_legacy()
    fresh = _fresh_batch(("RQ2TCG-V3-ENGINEERING-STARTUP-AUDIT-",))[0]
    identity, seed = str(fresh["identity"]), int(fresh["seed"])
    output = ENGINEERING / identity / "attempt_01"
    output.mkdir(parents=True, exist_ok=False)
    scene_path = V2_SEAM_SCENES / "REF-ASYNC.json"
    route_path = V2_SEAM_ROUTES / "REF-ASYNC.xml"
    scene = _load(scene_path)
    cell = {
        "cell_id": identity, "scene_code": "REF-ASYNC", "seed_slot": "ENG-STARTUP-01",
        "seed": seed, "engineering_qualification": True,
        "formal_scene_digest": scene["formal_scene_digest"],
        "execution_scene_manifest": str(scene_path.resolve()),
        "native_route_path": str(route_path.resolve()),
    }
    exclusion = {
        "schema_version": "driveclarify.rq2_t_cg.formal_v3.engineering_exclusion.v1",
        "identity": identity, "seed": seed, "status": "ENGINEERING_ONLY_PERMANENTLY_EXCLUDED",
        "formal_v3_excluded": True, "freshness_witness": fresh,
    }
    exclusion["registry_digest"] = canonical_sha256(exclusion)
    _write_json(REPORT / "ENGINEERING_EXCLUSION_REGISTRY_V3.json", exclusion)

    spec = v2_entry.campaign._episode_spec(identity, seed, scene, route_path)
    native.SIMLINGO_PROTECTED_DIFF_SHA256 = native._git_diff_sha256(native.SIMLINGO_ROOT)
    preflight = _repair_irrelevant_display_process_false_positive(
        native.native_preflight(spec, output, visualization=True)
    )
    command = native.build_command(spec, output)
    environment = build_exact_formal_child_environment(spec, output, cell)
    persist_child_construction_receipt(
        output / "FORMAL_CHILD_CONSTRUCTION_RECEIPT.json", command=command,
        environment=environment, cell=cell,
    )
    timer = StartupTimingObserver(output)
    watchdog = FirstLegalRowWatchdog(output)

    def observe(context: Mapping[str, Any]) -> Mapping[str, Any]:
        timer(context)
        return watchdog(context)

    runtime = error = lease = None
    if preflight.get("status") == "PASS":
        try:
            with episode_backend.serial_gpu_lease() as lease_row:
                lease = lease_row
                runtime = native.run_native_episode(
                    spec, output, command=command, environment=environment,
                    cwd=native.SIMLINGO_ROOT, wall_timeout_seconds=wall_timeout_s,
                    wall_timeout_reason="RQ2_T_CG_FORMAL_V3_STARTUP_AUDIT_WALL_CONTAINMENT",
                    poll_observer=observe, after_launch=timer.after_launch,
                    cleanup_writer=lambda value: _write_json(output / "CLEANUP_RECEIPT.json", value),
                )
        except BaseException as exc:
            error = {"type": type(exc).__name__, "message": str(exc)}
    else:
        error = {"type": "PreflightBlocked", "message": ",".join(preflight.get("blockers", []))}
    # Capture final events in case they appeared between the last poll and exit.
    timer({"now_monotonic": time.monotonic(), "stdout_path": output / "evaluator_stdout.log"})
    scenario = _load(output / "FORMAL_SCENARIO_RECEIPT.json", {})
    builder = None
    terminal = scenario.get("terminal") if isinstance(scenario.get("terminal"), Mapping) else {}
    if error is None and terminal.get("state") == "NATURAL_HORIZON_OBSERVED":
        try:
            builder = build_formal_episode(output, cell)
        except BaseException as exc:
            error = {"type": type(exc).__name__, "message": str(exc), "stage": "AUDIT_BUILDER"}
    first = timer.events.get("first_VLA_forward", {})
    motion = timer.events.get("first_nonzero_ego_motion", {})
    pre_sim_wall = first.get("wall_seconds_from_process_launch")
    pre_sim_game = first.get("CARLA_GameTime_s")
    classification = (
        "A. PRE-SIMULATION / INITIALIZATION OVERHEAD"
        if pre_sim_wall is not None and float(pre_sim_wall) >= 10.0
        and (pre_sim_game is None or float(pre_sim_game) <= 0.25)
        else "B. NATIVE VEHICLE NONMOTION"
    )
    required_events = (
        "process_launch", "CARLA_ready", "agent_setup_ready", "checkpoint_model_ready",
        "first_VLA_forward", "first_legal_source_row", "first_nonzero_ego_motion",
    )
    checks = {
        "fresh_engineering_identity_seed": fresh.get("prior_match_paths") == [],
        "permanently_excluded": exclusion["formal_v3_excluded"] is True,
        "preflight_pass": preflight.get("status") == "PASS",
        "runtime_natural": isinstance(runtime, Mapping) and runtime.get("termination_reason") == "NATURAL_EVALUATOR_COMPLETION",
        "all_required_timing_events_recorded": all(name in timer.events for name in required_events),
        "first_legal_recorder_row": (output / "FIRST_LEGAL_SOURCE_ROW_RECEIPT.json").is_file(),
        "builder_complete": isinstance(builder, Mapping),
        "error_absent": error is None,
    }
    value = {
        "schema_version": "driveclarify.rq2_t_cg.formal_v3.startup_timing_audit.v1",
        "status": "PASS_ONE_BOUNDED_STARTUP_TIMING_AUDIT" if all(checks.values()) else "FAIL_STARTUP_TIMING_AUDIT",
        "classification": classification,
        "identity": identity, "seed": seed,
        "formal_scientific_exposure": False,
        "events": timer.events,
        "process_launch_to_first_VLA_wall_s": pre_sim_wall,
        "process_launch_to_first_nonzero_motion_wall_s": motion.get("wall_seconds_from_process_launch"),
        "optimization_eligible": classification.startswith("A."),
        "optimization_enabled": False,
        "optimization_decision": (
            "NOT_ENABLED_LOW_RISK_PERSISTENT_NATIVE_LIFECYCLE_NOT_ALREADY_QUALIFIED; "
            "proceeding in original launch mode avoids scientific trajectory risk"
        ),
        "persistent_model_CARLA_reuse_enabled": False,
        "startup_wall_time_before_s": pre_sim_wall,
        "startup_wall_time_after_s": None,
        "equivalence_gate": "NOT_REQUIRED_NO_RUNTIME_PERSISTENCE_OR_REUSE_OPTIMIZATION",
        "checks": checks,
        "failed_checks": [key for key, passed in checks.items() if not passed],
        "preflight": preflight, "runtime": runtime, "lease": lease, "error": error,
        "output_path": str(output.relative_to(ROOT)),
    }
    value["receipt_digest"] = canonical_sha256(value)
    _write_json(receipt_path, value)
    _append_command("startup-audit", value["status"])
    if not all(checks.values()):
        raise RuntimeError("STARTUP_TIMING_AUDIT_FAILED")
    return value


def prepare(wall_timeout_s: float) -> Mapping[str, Any]:
    REPORT.mkdir(parents=True, exist_ok=True)
    v2_seal = seal_formal_v2()
    scenes = _copy_frozen_scenes()
    tests = focused_evaluability_tests()
    audit = startup_audit(wall_timeout_s)
    checks = {
        "formal_v2_sealed": v2_seal["status"].startswith("PASS_"),
        "eight_scene_contracts_exact": scenes["status"].startswith("PASS_"),
        "focused_tests_pass": tests["status"].startswith("PASS_"),
        "startup_audit_pass": audit["status"].startswith("PASS_"),
        "checkpoint_unchanged": _sha(CHECKPOINT) == CHECKPOINT_SHA256,
        "recorder_works": audit["checks"]["first_legal_recorder_row"],
        "formal_v3_seeds_not_generated": not (REPORT / "FORMAL_V3_SEED_FRESHNESS_RECEIPT.json").exists(),
    }
    value = {
        "schema_version": "driveclarify.rq2_t_cg.formal_v3.admission.v1",
        "status": "PASS_MINIMUM_V3_ADMISSION" if all(checks.values()) else "FAIL_V3_ADMISSION",
        "checks": checks,
        "failed_checks": [key for key, passed in checks.items() if not passed],
        "v2_exclusion_digest": v2_seal["receipt_digest"],
        "scene_reuse_digest": scenes["receipt_digest"],
        "focused_tests_digest": tests["receipt_digest"],
        "startup_audit_digest": audit["receipt_digest"],
    }
    value["receipt_digest"] = canonical_sha256(value)
    _write_json(REPORT / "FORMAL_V3_ADMISSION_RECEIPT.json", value)
    _append_command("prepare", value["status"])
    if not all(checks.values()):
        raise RuntimeError("FORMAL_V3_ADMISSION_FAILED")
    return value


def freeze_and_generate_seeds() -> Mapping[str, Any]:
    roster_path = REPORT / "FORMAL_V3_ROSTER.json"
    if roster_path.is_file():
        return _load(roster_path)
    admission = _load(REPORT / "FORMAL_V3_ADMISSION_RECEIPT.json", {})
    if admission.get("status") != "PASS_MINIMUM_V3_ADMISSION":
        raise RuntimeError("FORMAL_V3_ADMISSION_REQUIRED")
    scene_reuse = _load(REPORT / "V3_SCIENCE_SCENE_REUSE_RECEIPT.json")
    v2_freeze = _load(V2_REPORT / "FINAL_FORMAL_V2_FREEZE_RECEIPT.json")
    v2_base_freeze = _load(V2_REPORT / "FORMAL_V2_FREEZE_RECEIPT.json")
    source = _source_freeze()
    checks = {
        "admission_pass": admission.get("status") == "PASS_MINIMUM_V3_ADMISSION",
        "eight_scene_contracts_exact_v2": scene_reuse.get("status") == "PASS_EIGHT_V2_SCIENTIFIC_SCENES_REUSED_EXACTLY",
        "calibration_exact": v2_base_freeze.get("calibration_freeze_digest") == CALIBRATION_FREEZE_DIGEST,
        "checkpoint_exact": source["checkpoint_unchanged"],
        "frozen_science_sources_exact": source["v2_science_source_hashes_unchanged"],
        "v2_permanently_excluded": _load(REPORT / "FORMAL_V2_EXCLUSION_RECEIPT.json")["status"].startswith("PASS_"),
        "startup_audit_exactly_one": _load(REPORT / "STARTUP_TIMING_AUDIT_RECEIPT.json")["status"].startswith("PASS_"),
        "no_runtime_optimization": _load(REPORT / "STARTUP_TIMING_AUDIT_RECEIPT.json")["optimization_enabled"] is False,
        "formal_seeds_zero_before_freeze": not (REPORT / "FORMAL_V3_SEED_FRESHNESS_RECEIPT.json").exists(),
        "formal_exposures_zero_before_freeze": not V3_RUNS.exists(),
    }
    freeze = {
        "schema_version": "driveclarify.rq2_t_cg.formal_v3.protocol_freeze.v1",
        "status": "PASS_FORMAL_V3_PROTOCOL_FROZEN" if all(checks.values()) else "FAIL_FORMAL_V3_PROTOCOL_FREEZE",
        "v2_final_freeze_digest": V2_FINAL_FREEZE_DIGEST,
        "eight_scene_contracts": {
            code: {
                "formal_scene_digest": scene_reuse["scenes"][code]["scene_contract_digest"],
                "scene_file_sha256": scene_reuse["scenes"][code]["scene_sha256"],
                "native_route_sha256": scene_reuse["scenes"][code]["route_sha256"],
            }
            for code in SCENE_ORDER
        },
        "calibration_freeze_digest": CALIBRATION_FREEZE_DIGEST,
        "source_freeze_digest": source["receipt_digest"],
        "checkpoint_sha256": source["checkpoint_sha256"],
        "background_traffic_policy_sha256": _sha(V2_REPORT / "BACKGROUND_TRAFFIC_POLICY_V2.json"),
        "B1_B2_contract_digest": canonical_sha256(VIEWS),
        "evidence_contract_digest": canonical_sha256({
            "views": VIEWS, "endpoints": ENDPOINTS,
            "scene_events": {code: _load(V3_SCENES / (code + ".json"))["events"] for code in SCENE_ORDER},
        }),
        "commitment_deadline_digest": canonical_sha256({
            code: {
                "commitment": _load(V3_SCENES / (code + ".json"))["commitment"],
                "deadline": _load(V3_SCENES / (code + ".json"))["deadline"],
            }
            for code in SCENE_ORDER
        }),
        "evaluability_contract": V3_EVALUABILITY_CONTRACT,
        "retry_policy": {
            "scientific_retries": 0, "seed_substitution_count": 0,
            "zero_exposure_infrastructure_retries": 0,
        },
        "analysis_plan": V3_ANALYSIS_PLAN,
        "decision_rules_digest": canonical_sha256(RULES),
        "H_CG_definitions": HYPOTHESES,
        "source_checkpoint_identity": source,
        "checks": checks,
        "failed_checks": [key for key, passed in checks.items() if not passed],
        "formal_seed_values_generated_at_freeze": 0,
        "formal_scientific_exposures_at_freeze": 0,
        "scientific_protocol_changes_after_freeze_allowed": 0,
    }
    freeze["freeze_digest"] = canonical_sha256(freeze)
    _write_json(REPORT / "FORMAL_V3_PROTOCOL_FREEZE_RECEIPT.json", freeze)
    _write_json(REPORT / "SOURCE_FREEZE_RECEIPT.json", source)
    if not all(checks.values()):
        raise RuntimeError("FORMAL_V3_PROTOCOL_FREEZE_FAILED")

    witnesses = _fresh_batch(tuple("RQ2TCG-FORMAL-V3-SEED-WITNESS-" for _ in range(6)))
    seeds = [int(row["seed"]) for row in witnesses]
    campaign_token = os.urandom(8).hex().upper()
    planned = planned_run_order(seeds)
    cells = []
    sequence = 0
    for slot in planned:
        for code in slot["scene_order"]:
            sequence += 1
            scene = _load(V3_SCENES / (code + ".json"))
            cell_id = "RQ2TCG-FV3-{}-{}-{}".format(code, slot["slot_id"], campaign_token)
            cells.append({
                "run_sequence": sequence,
                "cell_id": cell_id,
                "scene_code": code,
                "seed_slot": slot["slot_id"],
                "seed": int(slot["seed"]),
                "engineering_qualification": False,
                "formal_scene_id": scene["formal_scene_id"],
                "formal_scene_digest": scene["formal_scene_digest"],
                "route_spec_digest": scene["route"]["route_spec_digest"],
                "native_route_sha256": _sha(V3_ROUTES / (code + ".xml")),
                "execution_scene_manifest": str((V3_SCENES / (code + ".json")).resolve()),
                "native_route_path": str((V3_ROUTES / (code + ".xml")).resolve()),
                "scientific_retry_allowed_after_exposure": False,
                "seed_substitution_allowed": False,
            })
    freshness = {
        "schema_version": "driveclarify.rq2_t_cg.formal_v3.seed_freshness.v1",
        "status": "PASS_SIX_COMPLETELY_FRESH_SHARED_FORMAL_V3_SEEDS",
        "generated_seed_count": 6,
        "seed_values": seeds,
        "shared_across_all_eight_scenes": True,
        "prior_occurrence_count": 0,
        "generation_witnesses": witnesses,
        "freeze_preceded_generation": True,
        "scan_roots": [str(ROOT), str(SIMLINGO)],
    }
    freshness["receipt_digest"] = canonical_sha256(freshness)
    roster = {
        "schema_version": "driveclarify.rq2_t_cg.formal_v3.roster.v1",
        "status": "SEALED_8_BY_6_FORMAL_V3_ROSTER",
        "freeze_digest": freeze["freeze_digest"],
        "scene_count": 8, "seed_count": 6, "cell_count": 48,
        "seed_values": seeds, "cells": cells,
        "formal_scientific_exposures": 0,
    }
    roster["roster_digest"] = canonical_sha256(roster)
    ledger = {
        "schema_version": "driveclarify.rq2_t_cg.formal_v3.execution_ledger.v1",
        "status": "READY_FOR_48_FORMAL_V3_EPISODES",
        "freeze_digest": freeze["freeze_digest"],
        "roster_digest": roster["roster_digest"],
        "planned_episode_count": 48,
        "formal_episode_attempt_count": 0,
        "formal_scientific_exposures": 0,
        "formal_primary_evaluable_count": 0,
        "formal_native_noncompletion_count": 0,
        "formal_integrity_invalid_count": 0,
        "formal_zero_exposure_infrastructure_failure_count": 0,
        "scientific_retry_count": 0,
        "infrastructure_retry_count": 0,
        "seed_substitution_count": 0,
        "entries": [],
        "hcg_analysis_legally_run": False,
    }
    ledger["ledger_digest"] = canonical_sha256(ledger)
    _write_json(REPORT / "FORMAL_V3_SEED_FRESHNESS_RECEIPT.json", freshness)
    _write_json(roster_path, roster)
    _write_json(REPORT / "FORMAL_V3_EXECUTION_LEDGER.json", ledger)
    exclusion = _load(REPORT / "ENGINEERING_EXCLUSION_REGISTRY_V3.json")
    exclusion["formal_v3_cells"] = [
        {"cell_id": row["cell_id"], "seed": row["seed"], "scene_code": row["scene_code"],
         "future_formal_use_excluded": True}
        for row in cells
    ]
    exclusion["formal_v3_seed_values"] = seeds
    exclusion["registry_digest"] = canonical_sha256({key: value for key, value in exclusion.items() if key != "registry_digest"})
    _write_json(REPORT / "ENGINEERING_EXCLUSION_REGISTRY_V3.json", exclusion)
    _append_command("freeze-and-generate-seeds", "PASS_FORMAL_V3_FREEZE_AND_ROSTER")
    return roster


def _official_summary(official: Mapping[str, Any]) -> Mapping[str, Any]:
    records = official.get("_checkpoint", {}).get("records", [])
    if len(records) != 1:
        return {
            "record_count": len(records), "status": None, "route_completion_percent": None,
            "critical_infractions_clean": False, "infractions": {}, "game_duration_s": None,
        }
    row = records[0]
    infractions = row.get("infractions") or {}
    return {
        "record_count": 1,
        "status": row.get("status"),
        "route_completion_percent": (row.get("scores") or {}).get("score_route"),
        "critical_infractions_clean": all(not infractions.get(key) for key in CRITICAL_INFRACTIONS),
        "infractions": infractions,
        "game_duration_s": (row.get("meta") or {}).get("duration_game"),
    }


def _run_one(cell: Mapping[str, Any], wall_timeout_s: float) -> Mapping[str, Any]:
    _configure_legacy()
    output = V3_RUNS / str(cell["cell_id"]) / "attempt_01"
    output.mkdir(parents=True, exist_ok=False)
    scene_path = Path(str(cell["execution_scene_manifest"]))
    route_path = Path(str(cell["native_route_path"]))
    scene = _load(scene_path)
    admission = static_route_admission(route_path, scene)
    spec = v2_entry.campaign._episode_spec(str(cell["cell_id"]), int(cell["seed"]), scene, route_path)
    native.SIMLINGO_PROTECTED_DIFF_SHA256 = native._git_diff_sha256(native.SIMLINGO_ROOT)
    preflight = _repair_irrelevant_display_process_false_positive(
        native.native_preflight(spec, output, visualization=True)
    )
    command = native.build_command(spec, output)
    environment = build_exact_formal_child_environment(spec, output, cell)
    construction = persist_child_construction_receipt(
        output / "FORMAL_CHILD_CONSTRUCTION_RECEIPT.json", command=command,
        environment=environment, cell=cell,
    )
    watchdog = FirstLegalRowWatchdog(output)
    runtime = error = lease = None
    if preflight.get("status") == "PASS":
        try:
            with episode_backend.serial_gpu_lease() as lease_row:
                lease = lease_row
                runtime = native.run_native_episode(
                    spec, output, command=command, environment=environment,
                    cwd=native.SIMLINGO_ROOT, wall_timeout_seconds=wall_timeout_s,
                    wall_timeout_reason="RQ2_T_CG_FORMAL_V3_WALL_CONTAINMENT",
                    poll_observer=watchdog,
                    cleanup_writer=lambda value: _write_json(output / "CLEANUP_RECEIPT.json", value),
                )
        except BaseException as exc:
            error = {"type": type(exc).__name__, "message": str(exc)}
    else:
        error = {"type": "PreflightBlocked", "message": ",".join(preflight.get("blockers", []))}
    activation = _load(output / "FORMAL_ACTIVATION_RECEIPT.json", {})
    scenario = _load(output / "FORMAL_SCENARIO_RECEIPT.json", {})
    first_row = _load(output / "FIRST_LEGAL_SOURCE_ROW_RECEIPT.json", {})
    cleanup = _load(output / "CLEANUP_RECEIPT.json", {})
    background = _load(output / "BACKGROUND_TRAFFIC_RUNTIME_RECEIPT.json", {})
    equivalence = _load(output / "probe/PROBE_EQUIVALENCE.json", {})
    official = _official_summary(_load(output / "leaderboard_results.json", {}))
    terminal = scenario.get("terminal") if isinstance(scenario.get("terminal"), Mapping) else {}
    exposed = activation.get("formal_scientific_exposure") is True
    commitment = scenario.get("commitment") if isinstance(scenario.get("commitment"), Mapping) else None
    all_events = len(scenario.get("event_start_rows", [])) == len(scene.get("events", []))
    event_windows = all(row.get("window_entry_observed") is True for row in scenario.get("event_start_rows", []))
    endpoint_reached = (
        commitment is not None and all_events and event_windows
        and terminal.get("state") == "NATURAL_HORIZON_OBSERVED"
    )
    builder = None
    if error is None and exposed and endpoint_reached:
        try:
            builder = build_formal_episode(output, cell)
        except BaseException as exc:
            error = {"type": type(exc).__name__, "message": str(exc), "stage": "POSTTRACE_FORMAL_V3_BUILDER"}
    no_control = _no_control_effect(equivalence)
    zero_invariants = isinstance(builder, Mapping) and all(builder.get(key) == 0 for key in (
        "observer_added_vla_forwards", "duplicate_candidate_computations", "PID_controller_changes",
        "second_control_writer", "RoutePlanner_mutations", "UKF_mutations", "command_history_mutations",
        "runtime_true_intent_reads", "online_ask_count",
    ))
    complete_actionability = isinstance(builder, Mapping) and all(key in builder for key in (
        "commitment_time_s", "deadline_time_s", "B1_window_observed", "B2_window_observed",
        "B1_window_duration_s", "B2_window_duration_s", "B1_first_sufficiency_TTCmt_s",
        "B2_first_sufficiency_TTCmt_s",
    ))
    checks = {
        "static_route_admission": admission.get("status") == "PASS_STATIC_FORMAL_ROUTE_ADMISSION",
        "exact_child_construction": construction.get("probe_enabled") is True,
        "first_legal_source_row": first_row.get("status") == "PASS_FIRST_LEGAL_SOURCE_ROW",
        "formal_exposure_observed": exposed,
        "scenario_identity": scenario.get("formal_scene_digest") == scene.get("formal_scene_digest"),
        "all_event_starts": all_events,
        "event_windows_entered": event_windows,
        "commitment_observed": commitment is not None,
        "required_scientific_endpoint_reached": endpoint_reached,
        "runtime_route_persisted": (output / "AGENT_ROUTE_BINDING_RUNTIME_RECEIPT.json").is_file(),
        "posttrace_builder_complete": isinstance(builder, Mapping) and builder.get("formal_valid") is True,
        "paired_source_identity": isinstance(builder, Mapping) and builder.get("same_source_identity_all_views") is True,
        "complete_actionability_deadline_information": complete_actionability,
        "zero_control_mutation": no_control["pass"],
        "all_compute_control_intent_invariants_zero": zero_invariants,
        "clean_teardown": cleanup.get("status") == "PASS",
        "background_traffic_policy_runtime_pass": background.get("status") == "PASS_RANDOM_BACKGROUND_TRAFFIC_DISABLED",
        "random_background_generator_not_attached": background.get("background_behavior_attached") is False,
        "random_background_vehicle_requests_zero": background.get("traffic_manager_generated_background_vehicles_requested") == 0,
        "automatic_parked_mesh_requests_zero": background.get("automatic_parked_mesh_actors_requested") == 0,
        "official_critical_integrity_clean": official["critical_infractions_clean"],
        "error_absent": error is None,
    }
    classification = classify_episode({
        "exposed": exposed, "checks": checks, "builder": builder,
        "runtime": runtime or {}, "terminal": terminal, "official": official,
    })
    value = {
        "schema_version": "driveclarify.rq2_t_cg.formal_v3.execution_result.v1",
        "cell_id": cell["cell_id"], "scene_code": cell["scene_code"],
        "seed_slot": cell["seed_slot"], "seed": cell["seed"],
        "attempt": 1, "scientific_retry": False, "infrastructure_retry": False,
        "exposed": exposed, "classification": classification["classification"],
        "reason_code": classification["reason_code"],
        "primary_evaluable": classification["primary_evaluable"],
        "commitment_reached": classification["commitment_reached"],
        "route_completion_percent": classification["route_completion_percent"],
        "checks": checks,
        "builder": builder, "terminal": terminal, "official": official,
        "preflight": preflight, "runtime": runtime, "lease": lease, "error": error,
        "background_traffic_runtime_receipt": background,
        "output_path": str(output.relative_to(ROOT)),
    }
    value["record_digest"] = canonical_sha256(value)
    _write_json(output / "FORMAL_V3_EXECUTION_RESULT.json", value)
    return value


def _update_ledger_counts(ledger: dict[str, Any]) -> None:
    entries = ledger["entries"]
    ledger["formal_episode_attempt_count"] = len(entries)
    ledger["formal_scientific_exposures"] = sum(bool(row["exposed"]) for row in entries)
    ledger["formal_primary_evaluable_count"] = sum(row["classification"] == EVALUABLE for row in entries)
    ledger["formal_native_noncompletion_count"] = sum(
        row["classification"] == NON_EVALUABLE_NATIVE_NONCOMPLETION for row in entries
    )
    ledger["formal_integrity_invalid_count"] = sum(row["classification"] == INTEGRITY_INVALID for row in entries)
    ledger["formal_zero_exposure_infrastructure_failure_count"] = sum(
        row["classification"] == ZERO_EXPOSURE_INFRASTRUCTURE_FAILURE for row in entries
    )


def run_formal(wall_timeout_s: float) -> Mapping[str, Any]:
    roster = _load(REPORT / "FORMAL_V3_ROSTER.json", {})
    freeze = _load(REPORT / "FORMAL_V3_PROTOCOL_FREEZE_RECEIPT.json", {})
    ledger_path = REPORT / "FORMAL_V3_EXECUTION_LEDGER.json"
    ledger = _load(ledger_path, {})
    if roster.get("cell_count") != 48 or freeze.get("status") != "PASS_FORMAL_V3_PROTOCOL_FROZEN":
        raise RuntimeError("FORMAL_V3_ROSTER_OR_FREEZE_REQUIRED")
    drift = _verify_source_freeze()
    if not drift["pass"]:
        raise RuntimeError("FORMAL_V3_SOURCE_FREEZE_DRIFT:" + ",".join(drift["drift_paths"]))
    attempted = {row["cell_id"] for row in ledger.get("entries", [])}
    for cell in roster["cells"]:
        if cell["cell_id"] in attempted:
            continue
        result = _run_one(cell, wall_timeout_s)
        ledger["entries"].append({
            "cell_id": result["cell_id"], "scene_code": result["scene_code"],
            "seed_slot": result["seed_slot"], "seed": result["seed"],
            "classification": result["classification"], "reason_code": result["reason_code"],
            "primary_evaluable": result["primary_evaluable"], "exposed": result["exposed"],
            "commitment_reached": result["commitment_reached"],
            "route_completion_percent": result["route_completion_percent"],
            "scientific_retry": False, "infrastructure_retry": False,
            "record_digest": result["record_digest"], "output_path": result["output_path"],
        })
        _update_ledger_counts(ledger)
        possibility = prospective_gate_possible(ledger["entries"], roster["cells"], SCENE_ORDER)
        ledger["prospective_gate_possible"] = possibility
        ledger["status"] = "FORMAL_V3_IN_PROGRESS" if possibility["possible"] else "FORMAL_V3_MANDATORY_HARD_STOP_GATE_IMPOSSIBLE"
        ledger["ledger_digest"] = canonical_sha256({key: value for key, value in ledger.items() if key != "ledger_digest"})
        _write_json(ledger_path, ledger)
        if not possibility["possible"]:
            break
    gate = primary_gate(ledger["entries"], SCENE_ORDER)
    ledger["primary_evaluability_gate"] = gate
    if gate["pass"]:
        ledger["status"] = "PASS_FORMAL_V3_PRIMARY_EVALUABILITY_GATE"
    elif ledger["formal_episode_attempt_count"] == 48 or not ledger["prospective_gate_possible"]["possible"]:
        ledger["status"] = "FORMAL_V3_PRIMARY_EVALUABILITY_GATE_FAILED"
    _update_ledger_counts(ledger)
    ledger["ledger_digest"] = canonical_sha256({key: value for key, value in ledger.items() if key != "ledger_digest"})
    _write_json(ledger_path, ledger)
    _write_json(REPORT / "NATIVE_NONCOMPLETION_LEDGER.json", {
        "schema_version": "driveclarify.rq2_t_cg.formal_v3.native_noncompletion_ledger.v1",
        "planned_cells": 48,
        "entries": [row for row in ledger["entries"] if row["classification"] != EVALUABLE],
        "native_noncompletion_count": ledger["formal_native_noncompletion_count"],
        "all_planned_cells_silently_dropped": False,
    })
    _append_command("run-formal", ledger["status"])
    return ledger


def _mcnemar(pairs: Sequence[tuple[bool, bool]]) -> Mapping[str, Any]:
    b = sum((not left) and right for left, right in pairs)
    c = sum(left and (not right) for left, right in pairs)
    n = b + c
    if n == 0:
        p = 1.0
    else:
        tail = sum(math.comb(n, index) for index in range(0, min(b, c) + 1)) / (2.0 ** n)
        p = min(1.0, 2.0 * tail)
    return {
        "discordant_B1_false_B2_true": b,
        "discordant_B1_true_B2_false": c,
        "discordant_total": n,
        "two_sided_exact_p": p,
    }


def _holm(pvalues: Mapping[str, float]) -> Mapping[str, float]:
    ordered = sorted(pvalues, key=pvalues.get)
    adjusted = {}
    running = 0.0
    for rank, key in enumerate(ordered):
        running = max(running, min(1.0, (len(ordered) - rank) * float(pvalues[key])))
        adjusted[key] = running
    return adjusted


def _percentile(values: Sequence[float], probability: float) -> Any:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * probability
    low, high = int(math.floor(position)), int(math.ceil(position))
    if low == high:
        return ordered[low]
    fraction = position - low
    return ordered[low] * (1.0 - fraction) + ordered[high] * fraction


def _numeric_summary(values: Sequence[float]) -> Mapping[str, Any]:
    rows = [float(value) for value in values]
    return {
        "count": len(rows),
        "mean": None if not rows else sum(rows) / len(rows),
        "median": _percentile(rows, 0.5),
        "minimum": None if not rows else min(rows),
        "maximum": None if not rows else max(rows),
    }


def _clopper_pearson(events: int, total: int, alpha: float = 0.05) -> Any:
    if total == 0:
        return None
    from scipy.stats import beta
    low = 0.0 if events == 0 else float(beta.ppf(alpha / 2.0, events, total - events + 1))
    high = 1.0 if events == total else float(beta.ppf(1.0 - alpha / 2.0, events + 1, total - events))
    return [low, high]


def _seed_cluster_interval(rows: Sequence[Mapping[str, Any]], left_key: str, right_key: str) -> Mapping[str, Any]:
    by_slot = defaultdict(list)
    for row in rows:
        by_slot[row["seed_slot"]].append(float(row[right_key]) - float(row[left_key]))
    slots = sorted(by_slot)
    distribution = []
    for indices in itertools.product(range(len(slots)), repeat=len(slots)):
        sampled = []
        for index in indices:
            sampled.extend(by_slot[slots[index]])
        if sampled:
            distribution.append(sum(sampled) / len(sampled))
    observed = [value for rows_for_slot in by_slot.values() for value in rows_for_slot]
    return {
        "method": "EXHAUSTIVE_SHARED_SEED_CLUSTER_BOOTSTRAP_WITH_REPLACEMENT",
        "seed_block_count": len(slots),
        "resample_count": len(distribution),
        "estimate": None if not observed else sum(observed) / len(observed),
        "percentile_95_interval": [_percentile(distribution, 0.025), _percentile(distribution, 0.975)],
        "seed_block_evaluable_counts": {key: len(by_slot[key]) for key in slots},
    }


def _endpoint_result(rows: Sequence[Mapping[str, Any]], left: str, right: str) -> Mapping[str, Any]:
    pairs = [(bool(row[left]), bool(row[right])) for row in rows]
    b1 = sum(value for value, _ in pairs)
    b2 = sum(value for _, value in pairs)
    total = len(pairs)
    return {
        "B1_count": b1, "B2_count": b2, "denominator": total,
        "B1_rate": None if total == 0 else b1 / total,
        "B2_rate": None if total == 0 else b2 / total,
        "B1_clopper_pearson_95_interval": _clopper_pearson(b1, total),
        "B2_clopper_pearson_95_interval": _clopper_pearson(b2, total),
        "paired_risk_difference_B2_minus_B1": None if total == 0 else (b2 - b1) / total,
        "paired_effect_95_interval": _seed_cluster_interval(rows, left, right),
        "mcnemar": _mcnemar(pairs),
        "scene_strata": {
            code: {
                "B1_count": sum(bool(row[left]) for row in rows if row["scene_code"] == code),
                "B2_count": sum(bool(row[right]) for row in rows if row["scene_code"] == code),
                "denominator": sum(row["scene_code"] == code for row in rows),
            }
            for code in sorted({row["scene_code"] for row in rows})
        },
    }


def analyze() -> Mapping[str, Any]:
    results_path = REPORT / "FORMAL_V3_HCG_RESULTS.json"
    if results_path.is_file():
        return _load(results_path)
    ledger = _load(REPORT / "FORMAL_V3_EXECUTION_LEDGER.json", {})
    roster = _load(REPORT / "FORMAL_V3_ROSTER.json", {})
    gate = primary_gate(ledger.get("entries", []), SCENE_ORDER)
    if not gate["pass"]:
        raise RuntimeError("FORMAL_V3_PRIMARY_EVALUABILITY_GATE_REQUIRED")
    result_records = []
    rows = []
    for entry in ledger["entries"]:
        result = _load(ROOT / entry["output_path"] / "FORMAL_V3_EXECUTION_RESULT.json")
        result_records.append((entry, result))
        if entry["classification"] == EVALUABLE:
            row = copy.deepcopy(result["builder"])
            row["seed_slot"] = entry["seed_slot"]
            rows.append(row)
    scene_counts = Counter(row["scene_code"] for row in rows)
    quality_checks = {
        "primary_evaluability_gate_pass": gate["pass"],
        "evaluable_rows_match_ledger": len(rows) == ledger["formal_primary_evaluable_count"],
        "total_at_least_40": len(rows) >= 40,
        "every_scene_at_least_5": all(scene_counts[code] >= 5 for code in SCENE_ORDER),
        "all_rows_from_frozen_roster": {row["cell_id"] for row in rows}.issubset(
            {cell["cell_id"] for cell in roster["cells"]}
        ),
        "all_evaluable_records_same_source": all(row["same_source_identity_all_views"] is True for row in rows),
        "all_evaluable_builders_formal_valid": all(row["formal_valid"] is True for row in rows),
        "scientific_retries_zero": ledger["scientific_retry_count"] == 0,
        "seed_substitution_zero": ledger["seed_substitution_count"] == 0,
        "frame_rows_not_independent_n": True,
    }
    quality = {
        "schema_version": "driveclarify.rq2_t_cg.formal_v3.data_quality_gate.v1",
        "status": "PASS_FORMAL_V3_DATA_QUALITY_GATE" if all(quality_checks.values()) else "FAIL_FORMAL_V3_DATA_QUALITY_GATE",
        "primary_unit": "scene x shared-seed episode",
        "observed_evaluable_row_count": len(rows),
        "scene_counts": dict(sorted(scene_counts.items())),
        "checks": quality_checks,
        "failed_checks": [key for key, passed in quality_checks.items() if not passed],
        "analysis_started": False,
    }
    quality["receipt_digest"] = canonical_sha256(quality)
    _write_json(REPORT / "FORMAL_V3_DATA_QUALITY_GATE.json", quality)
    if not all(quality_checks.values()):
        raise RuntimeError("FORMAL_V3_DATA_QUALITY_GATE_FAILED")
    table = {
        "schema_version": "driveclarify.rq2_t_cg.formal_v3.primary_table.v1",
        "primary_unit": "scene x shared-seed episode", "row_count": len(rows), "rows": rows,
    }
    table["table_digest"] = canonical_sha256(table)
    _write_json(REPORT / "FORMAL_V3_EPISODE_LEVEL_PRIMARY_TABLE.json", table)
    scalar_fields = sorted({key for row in rows for key, value in row.items() if not isinstance(value, (dict, list))})
    with (REPORT / "FORMAL_V3_EPISODE_LEVEL_PRIMARY_TABLE.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=scalar_fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in scalar_fields})

    async_codes = set(V3_ANALYSIS_PLAN["async_positive_scene_codes"])
    sync_codes = set(V3_ANALYSIS_PLAN["sync_control_scene_codes"])
    async_rows = [row for row in rows if row["scene_code"] in async_codes]
    sync_rows = [row for row in rows if row["scene_code"] in sync_codes]
    endpoint_keys = {
        "precommitment_sufficiency": ("B1_precommitment_sufficiency", "B2_precommitment_sufficiency"),
        "actionable_window_presence": ("B1_window_observed", "B2_window_observed"),
    }
    endpoints = {name: _endpoint_result(async_rows, left, right) for name, (left, right) in endpoint_keys.items()}
    adjusted = _holm({name: row["mcnemar"]["two_sided_exact_p"] for name, row in endpoints.items()})
    for name in endpoints:
        endpoints[name]["holm_adjusted_p"] = adjusted[name]
    supported = all(
        row["paired_risk_difference_B2_minus_B1"] is not None
        and row["paired_risk_difference_B2_minus_B1"] > 0.0
        and row["holm_adjusted_p"] < 0.05
        for row in endpoints.values()
    )
    hcg1 = {
        "status": "SUPPORTED" if supported else "NOT_SUPPORTED",
        "population_count": len(async_rows), "endpoints": endpoints,
        "B2_only_same_frame_episode_count": sum(int(row["B2_only_same_frame_sufficiency_count"]) > 0 for row in async_rows),
        "B2_only_same_frame_witness_count": sum(int(row["B2_only_same_frame_sufficiency_count"]) for row in async_rows),
    }
    hcg1["B2_only_same_frame_episode_rate"] = hcg1["B2_only_same_frame_episode_count"] / len(async_rows)
    hcg1["B2_only_same_frame_episode_95_interval"] = _clopper_pearson(
        hcg1["B2_only_same_frame_episode_count"], len(async_rows)
    )
    async_descriptive = {
        "first_sufficiency_TTCmt_s": {
            "B1": _numeric_summary([row["B1_first_sufficiency_TTCmt_s"] for row in async_rows if row["B1_first_sufficiency_TTCmt_s"] is not None]),
            "B2": _numeric_summary([row["B2_first_sufficiency_TTCmt_s"] for row in async_rows if row["B2_first_sufficiency_TTCmt_s"] is not None]),
            "paired_B2_minus_B1_jointly_observed": _numeric_summary([
                float(row["B2_first_sufficiency_TTCmt_s"]) - float(row["B1_first_sufficiency_TTCmt_s"])
                for row in async_rows
                if row["B1_first_sufficiency_TTCmt_s"] is not None and row["B2_first_sufficiency_TTCmt_s"] is not None
            ]),
            "no_sufficiency_imputed_as_zero": False,
        },
        "actionable_window_duration_s": {
            "B1": _numeric_summary([row["B1_window_duration_s"] for row in async_rows]),
            "B2": _numeric_summary([row["B2_window_duration_s"] for row in async_rows]),
            "paired_B2_minus_B1": _seed_cluster_interval(async_rows, "B1_window_duration_s", "B2_window_duration_s"),
        },
        "by_scene": {
            code: {
                "episode_count": sum(row["scene_code"] == code for row in async_rows),
                "B1_precommitment_sufficiency_count": sum(bool(row["B1_precommitment_sufficiency"]) for row in async_rows if row["scene_code"] == code),
                "B2_precommitment_sufficiency_count": sum(bool(row["B2_precommitment_sufficiency"]) for row in async_rows if row["scene_code"] == code),
                "B1_actionable_window_count": sum(bool(row["B1_window_observed"]) for row in async_rows if row["scene_code"] == code),
                "B2_actionable_window_count": sum(bool(row["B2_window_observed"]) for row in async_rows if row["scene_code"] == code),
            }
            for code in sorted(async_codes)
        },
    }

    sync_endpoints = {name: _endpoint_result(sync_rows, left, right) for name, (left, right) in endpoint_keys.items()}
    systematic_sync = any(
        row["paired_risk_difference_B2_minus_B1"] is not None
        and row["paired_risk_difference_B2_minus_B1"] > 0
        and row["mcnemar"]["two_sided_exact_p"] < 0.05
        for row in sync_endpoints.values()
    )
    hcg2 = {
        "status": "SYSTEMATIC_B2_ONLY_ADVANTAGE_OBSERVED" if systematic_sync else "NO_SYSTEMATIC_B2_ONLY_ADVANTAGE_DETECTED",
        "population_count": len(sync_rows), "endpoints": sync_endpoints,
        "equivalence_claimed": False,
    }

    stratum_for = {
        "REF-ASYNC": "ASYNC", "LMK-ASYNC": "ASYNC", "ORD-ASYNC": "ASYNC",
        "REF-SYNC": "SYNC", "LMK-SYNC": "SYNC", "ORD-LATE-REVEAL": "LATE_REVEAL",
        "NONREVEAL": "NONREVEAL", "USC-INTRINSIC": "USC",
    }
    hcg3_strata = {}
    pooled = Counter()
    rule_keys = ("R-EVIDENCE-ONLY(B2)", "R-TIME-ONLY(NONE)", "R-JOINT(B2)")
    for stratum in ("ASYNC", "SYNC", "LATE_REVEAL", "NONREVEAL", "USC"):
        group = [row for row in rows if stratum_for[row["scene_code"]] == stratum]
        classifications = {
            key: dict(Counter(row["rule_results"][key]["classification"] for row in group))
            for key in rule_keys
        }
        for key in rule_keys:
            for classification_name, count in classifications[key].items():
                pooled[(key, classification_name)] += count
        hcg3_strata[stratum] = {
            "episode_count": len(group), "classification_counts": classifications,
            "proposed_query_TTCmt_s": {
                key: _numeric_summary([
                    row["rule_results"][key]["proposed_query"]["TTCmt_s"]
                    for row in group if row["rule_results"][key].get("proposed_query") is not None
                ]) for key in rule_keys
            },
            "remaining_margin_at_query_s": {
                key: _numeric_summary([
                    row["rule_results"][key]["proposed_query"]["remaining_margin_s"]
                    for row in group if row["rule_results"][key].get("proposed_query") is not None
                ]) for key in rule_keys
            },
        }
    time_premature = pooled[("R-TIME-ONLY(NONE)", "PREMATURE_UNSUPPORTED_TRIGGER")]
    joint_premature = pooled[("R-JOINT(B2)", "PREMATURE_UNSUPPORTED_TRIGGER")]
    evidence_late = pooled[("R-EVIDENCE-ONLY(B2)", "TOO_LATE_RULE_TRIGGER")]
    joint_late = pooled[("R-JOINT(B2)", "TOO_LATE_RULE_TRIGGER")]
    hcg3 = {
        "status": "DIRECTIONALLY_SUPPORTED" if joint_premature < time_premature and joint_late < evidence_late else "NOT_DIRECTIONALLY_SUPPORTED",
        "strata": hcg3_strata,
        "directional_comparison": {
            "R_TIME_ONLY_premature_unsupported": time_premature,
            "R_JOINT_premature_unsupported": joint_premature,
            "R_EVIDENCE_ONLY_too_late": evidence_late,
            "R_JOINT_too_late": joint_late,
        },
        "extra_CARLA_episodes": 0, "extra_VLA_forwards": 0,
    }

    nonreveal = [row for row in rows if row["scene_code"] == "NONREVEAL"]
    usc = [row for row in rows if row["scene_code"] == "USC-INTRINSIC"]
    invalidation = [row for row in rows if row.get("invalidation_observed")]
    controls = {
        "NONREVEAL_false_sufficiency": {
            "events": sum(bool(row["false_sufficiency_B1"] or row["false_sufficiency_B2"]) for row in nonreveal),
            "denominator": len(nonreveal),
        },
        "USC_fabricated_semantic_resolution": {
            "events": sum(int(row["fabricated_semantic_resolution_count"]) > 0 for row in usc),
            "denominator": len(usc),
        },
        "invalidation_invalid_retention": {
            "events": sum(bool(row["invalid_retention_failure"]) for row in invalidation),
            "stale_evidence_survival_count": sum(int(row["stale_evidence_survival_after_invalidation_count"]) for row in invalidation),
            "denominator": len(invalidation),
        },
    }
    for value in controls.values():
        value["rate"] = None if value["denominator"] == 0 else value["events"] / value["denominator"]
        value["clopper_pearson_95_interval"] = _clopper_pearson(value["events"], value["denominator"])
    hcg4 = {
        "status": "PASS_ZERO_INTEGRITY_FAILURES" if all(value["events"] == 0 for value in controls.values()) else "INTEGRITY_FAILURE_OBSERVED",
        "eligible_negative_controls_only": True,
        "unavailable_controls_imputed_as_zero": False,
        "endpoints": controls,
    }
    complete_codes = {code for code in SCENE_ORDER if scene_counts[code] == 6}
    sensitivity_rows = [row for row in async_rows if row["scene_code"] in complete_codes]
    sensitivity_effect = (
        None if not sensitivity_rows else
        sum(float(row["B2_precommitment_sufficiency"]) - float(row["B1_precommitment_sufficiency"]) for row in sensitivity_rows) / len(sensitivity_rows)
    )
    primary_effect = endpoints["precommitment_sufficiency"]["paired_risk_difference_B2_minus_B1"]
    sensitivity = {
        "status": "NOT_ESTIMABLE_NO_COMPLETE_ASYNC_SCENE" if sensitivity_effect is None else "ESTIMATED",
        "complete_scene_codes": sorted(complete_codes),
        "complete_async_scene_codes": sorted(complete_codes.intersection(async_codes)),
        "episode_count": len(sensitivity_rows),
        "precommitment_sufficiency_paired_B2_minus_B1": sensitivity_effect,
        "primary_direction": "POSITIVE" if primary_effect > 0 else ("NEGATIVE" if primary_effect < 0 else "ZERO"),
        "sensitivity_direction": None if sensitivity_effect is None else (
            "POSITIVE" if sensitivity_effect > 0 else ("NEGATIVE" if sensitivity_effect < 0 else "ZERO")
        ),
    }
    sensitivity["direction_agrees_with_primary"] = (
        None if sensitivity_effect is None else sensitivity["sensitivity_direction"] == sensitivity["primary_direction"]
    )
    results = {
        "schema_version": "driveclarify.rq2_t_cg.formal_v3.hcg_results.v1",
        "status": "PASS_FROZEN_FORMAL_V3_HCG_ANALYSIS_EXECUTED_ONCE",
        "analysis_execution_count": 1,
        "formal_primary_evaluable_count": len(rows),
        "H_CG1": hcg1, "H_CG2": hcg2, "H_CG3": hcg3, "H_CG4": hcg4,
        "ASYNC_descriptive": async_descriptive,
        "supporting_controls": controls,
        "complete_scene_sensitivity": sensitivity,
        "frozen_analysis_plan": V3_ANALYSIS_PLAN,
        "parameters_or_hypotheses_modified": False,
        "frames_used_as_independent_n": False,
        "data_quality_gate_digest": quality["receipt_digest"],
    }
    results["results_digest"] = canonical_sha256(results)
    _write_json(results_path, results)
    _write_json(REPORT / "H_CG1_RESULTS.json", hcg1)
    _write_json(REPORT / "H_CG2_RESULTS.json", hcg2)
    _write_json(REPORT / "H_CG3_RESULTS.json", hcg3)
    _write_json(REPORT / "H_CG4_RESULTS.json", hcg4)
    _write_json(REPORT / "COMPLETE_SCENE_SENSITIVITY.json", sensitivity)
    ledger["hcg_analysis_legally_run"] = True
    ledger["hcg_results_digest"] = results["results_digest"]
    ledger["ledger_digest"] = canonical_sha256({key: value for key, value in ledger.items() if key != "ledger_digest"})
    _write_json(REPORT / "FORMAL_V3_EXECUTION_LEDGER.json", ledger)
    _append_command("analyze", results["status"])
    return results


def _all_json_parse() -> Mapping[str, Any]:
    failures = []
    count = 0
    for path in REPORT.rglob("*.json"):
        count += 1
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except BaseException as exc:
            failures.append({"path": str(path.relative_to(ROOT)), "error": type(exc).__name__ + ":" + str(exc)})
    return {"pass": not failures, "json_file_count": count, "failures": failures}


def finalize() -> Mapping[str, Any]:
    existing = REPORT / "FINAL_VALIDATION_RECEIPT.json"
    if existing.is_file():
        return _load(existing)
    freeze = _load(REPORT / "FORMAL_V3_PROTOCOL_FREEZE_RECEIPT.json", {})
    seeds = _load(REPORT / "FORMAL_V3_SEED_FRESHNESS_RECEIPT.json", {})
    roster = _load(REPORT / "FORMAL_V3_ROSTER.json", {})
    ledger = _load(REPORT / "FORMAL_V3_EXECUTION_LEDGER.json", {})
    audit = _load(REPORT / "STARTUP_TIMING_AUDIT_RECEIPT.json", {})
    hcg = _load(REPORT / "FORMAL_V3_HCG_RESULTS.json", {})
    gate = primary_gate(ledger.get("entries", []), SCENE_ORDER)
    source_check = _verify_source_freeze()
    freeze_recomputes = freeze.get("freeze_digest") == canonical_sha256({
        key: value for key, value in freeze.items() if key != "freeze_digest"
    })
    roster_recomputes = roster.get("roster_digest") == canonical_sha256({
        key: value for key, value in roster.items() if key != "roster_digest"
    })
    seed_values = seeds.get("seed_values", [])
    roster_seed_values = roster.get("seed_values", [])
    result_digest_pass = True
    evaluable_builders = []
    for entry in ledger.get("entries", []):
        result = _load(ROOT / entry["output_path"] / "FORMAL_V3_EXECUTION_RESULT.json", {})
        recomputed = canonical_sha256({key: value for key, value in result.items() if key != "record_digest"})
        result_digest_pass = result_digest_pass and result.get("record_digest") == entry.get("record_digest") == recomputed
        if entry["classification"] == EVALUABLE:
            evaluable_builders.append(result["builder"])
    v2_exclusion = _load(REPORT / "FORMAL_V2_EXCLUSION_RECEIPT.json", {})
    v2_unchanged = all(
        _sha(V2_REPORT / name) == expected
        for name, expected in v2_exclusion.get("critical_artifact_sha256", {}).items()
    )
    json_parse = _all_json_parse()
    scene_counts = gate["per_scene_primary_evaluable"]
    category_counts = Counter(row["classification"] for row in ledger.get("entries", []))
    checks = {
        "freeze_digest_unchanged": freeze_recomputes,
        "six_seeds_unchanged": len(seed_values) == 6 and seed_values == roster_seed_values,
        "roster_digest_unchanged": roster_recomputes,
        "planned_cells_48": roster.get("cell_count") == 48,
        "counts_reconcile": ledger.get("formal_episode_attempt_count") == len(ledger.get("entries", [])),
        "per_scene_counts_reconcile": sum(scene_counts.values()) == ledger.get("formal_primary_evaluable_count"),
        "native_noncompletion_ledger_present": (REPORT / "NATIVE_NONCOMPLETION_LEDGER.json").is_file(),
        "scientific_retries_zero": ledger.get("scientific_retry_count") == 0,
        "infrastructure_retries_zero": ledger.get("infrastructure_retry_count") == 0,
        "seed_substitution_zero": ledger.get("seed_substitution_count") == 0,
        "all_result_digests_recompute": result_digest_pass,
        "all_evaluable_B1_B2_same_source": all(row.get("same_source_identity_all_views") is True for row in evaluable_builders),
        "source_hashes_unchanged": source_check["pass"],
        "checkpoint_hash_unchanged": _sha(CHECKPOINT) == CHECKPOINT_SHA256,
        "calibration_hash_unchanged": freeze.get("calibration_freeze_digest") == CALIBRATION_FREEZE_DIGEST,
        "v2_evidence_unchanged": v2_unchanged,
        "added_vla_forwards_zero": all(row.get("observer_added_vla_forwards") == 0 for row in evaluable_builders),
        "duplicate_candidates_zero": all(row.get("duplicate_candidate_computations") == 0 for row in evaluable_builders),
        "PID_controller_changes_zero": all(row.get("PID_controller_changes") == 0 for row in evaluable_builders),
        "second_control_writer_zero": all(row.get("second_control_writer") == 0 for row in evaluable_builders),
        "true_intent_reads_zero": all(row.get("runtime_true_intent_reads") == 0 for row in evaluable_builders),
        "all_required_json_parse": json_parse["pass"],
        "analysis_only_if_gate_pass": not hcg or gate["pass"],
        "analysis_run_if_gate_pass": not gate["pass"] or hcg.get("status") == "PASS_FROZEN_FORMAL_V3_HCG_ANALYSIS_EXECUTED_ONCE",
    }
    if not all(checks.values()):
        status = "FORMAL_V3_EXECUTION_INTEGRITY_NOT_CLOSED"
        recommendation = "Preserve the sealed V3 roster and receipts, and independently audit only the failed final-integrity checks before any scientific use."
    elif not gate["pass"]:
        status = "FORMAL_V3_PRIMARY_EVALUABILITY_GATE_FAILED"
        recommendation = "Preserve the stopped V3 campaign and report the prospective native-evaluability shortfall without seed replacement, retry, or H-CG inference."
    elif hcg.get("H_CG1", {}).get("status") == "SUPPORTED":
        status = "PASS_RQ2_T_CG_FORMAL_V3_B2_SUPPORTED"
        recommendation = "Preserve this frozen V3 evidence package and use its prospectively bounded claim, including the separately reported native noncompletion rate."
    else:
        status = "PASS_RQ2_T_CG_FORMAL_V3_B2_NOT_SUPPORTED"
        recommendation = "Preserve this frozen V3 result as the confirmatory answer and report B2 as not supported under the prospectively defined estimand."
    endpoints = hcg.get("H_CG1", {}).get("endpoints", {})
    sufficiency = endpoints.get("precommitment_sufficiency", {})
    window = endpoints.get("actionable_window_presence", {})
    descriptive = hcg.get("ASYNC_descriptive", {})
    controls = hcg.get("supporting_controls", {})
    values = [
        ("exact_final_status", status),
        ("V3_protocol_freeze_digest", freeze.get("freeze_digest")),
        ("six_fresh_V3_seeds", seed_values),
        ("planned_cells", roster.get("cell_count", 0)),
        ("exposed_cells", ledger.get("formal_scientific_exposures", 0)),
        ("evaluable_episodes", ledger.get("formal_primary_evaluable_count", 0)),
        ("non_evaluable_native_episodes", ledger.get("formal_native_noncompletion_count", 0)),
        ("evaluability_percentage", 100.0 * ledger.get("formal_primary_evaluable_count", 0) / 48.0),
        ("per_scene_evaluability_counts", scene_counts),
        ("scientific_retries", ledger.get("scientific_retry_count", 0)),
        ("infrastructure_retries", ledger.get("infrastructure_retry_count", 0)),
        ("startup_audit_classification", audit.get("classification")),
        ("startup_wall_time_before_after_s", {"before": audit.get("startup_wall_time_before_s"), "after": audit.get("startup_wall_time_after_s")}),
        ("persistent_model_CARLA_reuse_enabled", audit.get("persistent_model_CARLA_reuse_enabled")),
        ("equivalence_gate_result", audit.get("equivalence_gate")),
        ("B1_ASYNC_sufficiency", {"count": sufficiency.get("B1_count"), "denominator": sufficiency.get("denominator"), "rate": sufficiency.get("B1_rate")}),
        ("B2_ASYNC_sufficiency", {"count": sufficiency.get("B2_count"), "denominator": sufficiency.get("denominator"), "rate": sufficiency.get("B2_rate")}),
        ("paired_B2_minus_B1_sufficiency_effect", sufficiency.get("paired_risk_difference_B2_minus_B1")),
        ("B1_actionable_window_rate", window.get("B1_rate")),
        ("B2_actionable_window_rate", window.get("B2_rate")),
        ("paired_actionable_window_effect", window.get("paired_risk_difference_B2_minus_B1")),
        ("same_frame_B1_false_B2_true_incidence", {
            "episodes": hcg.get("H_CG1", {}).get("B2_only_same_frame_episode_count"),
            "episode_rate": hcg.get("H_CG1", {}).get("B2_only_same_frame_episode_rate"),
            "frame_witnesses": hcg.get("H_CG1", {}).get("B2_only_same_frame_witness_count"),
        }),
        ("first_sufficiency_TTCmt", descriptive.get("first_sufficiency_TTCmt_s")),
        ("window_duration", descriptive.get("actionable_window_duration_s")),
        ("confidence_intervals_95", {
            "sufficiency_effect": sufficiency.get("paired_effect_95_interval"),
            "actionable_window_effect": window.get("paired_effect_95_interval"),
            "same_frame_incidence": hcg.get("H_CG1", {}).get("B2_only_same_frame_episode_95_interval"),
        }),
        ("H_CG1", hcg.get("H_CG1", "NOT_RUN")),
        ("H_CG2", hcg.get("H_CG2", "NOT_RUN")),
        ("H_CG3", hcg.get("H_CG3", "NOT_RUN")),
        ("H_CG4", hcg.get("H_CG4", "NOT_RUN")),
        ("complete_scene_sensitivity_result", hcg.get("complete_scene_sensitivity", "NOT_RUN")),
        ("NONREVEAL_false_sufficiency", controls.get("NONREVEAL_false_sufficiency")),
        ("USC_fabricated_resolution", controls.get("USC_fabricated_semantic_resolution")),
        ("invalid_retention_failures", controls.get("invalidation_invalid_retention")),
        ("added_VLA_forwards", sum(int(row.get("observer_added_vla_forwards", 0)) for row in evaluable_builders)),
        ("PID_controller_changes", sum(int(row.get("PID_controller_changes", 0)) for row in evaluable_builders)),
        ("second_control_writer", sum(int(row.get("second_control_writer", 0)) for row in evaluable_builders)),
        ("true_intent_reads", sum(int(row.get("runtime_true_intent_reads", 0)) for row in evaluable_builders)),
        ("source_freeze", {"pass": source_check["pass"], "digest": freeze.get("source_freeze_digest"), "checkpoint_sha256": freeze.get("checkpoint_sha256")}),
        ("final_validation", "PASS" if all(checks.values()) else "FAIL"),
        ("FINAL_REPORT_path", str((REPORT / "FINAL_REPORT.md").relative_to(ROOT))),
        ("H_CG_result_paths", {
            "combined": str((REPORT / "FORMAL_V3_HCG_RESULTS.json").relative_to(ROOT)),
            "H_CG1": str((REPORT / "H_CG1_RESULTS.json").relative_to(ROOT)),
            "H_CG2": str((REPORT / "H_CG2_RESULTS.json").relative_to(ROOT)),
            "H_CG3": str((REPORT / "H_CG3_RESULTS.json").relative_to(ROOT)),
            "H_CG4": str((REPORT / "H_CG4_RESULTS.json").relative_to(ROOT)),
        }),
        ("exactly_one_next_recommendation", recommendation),
    ]
    final = {
        "schema_version": "driveclarify.rq2_t_cg.formal_v3.final_validation.v1",
        "status": status,
        "required_final_return": {"{:02d}_{}".format(index, key): value for index, (key, value) in enumerate(values, 1)},
        "required_final_return_field_count": len(values),
        "category_counts": dict(category_counts),
        "checks": checks,
        "failed_checks": [key for key, passed in checks.items() if not passed],
        "json_parse_audit": json_parse,
        "one_next_recommendation": recommendation,
    }
    final["receipt_digest"] = canonical_sha256(final)
    _write_json(existing, final)
    report_lines = ["Exact final status: `{}`.".format(status), ""]
    for index, (key, value) in enumerate(values, 1):
        report_lines.append("{}. **{}**: `{}`".format(index, key, json.dumps(value, ensure_ascii=False, sort_keys=True)))
    report_lines.extend([
        "",
        "Primary paired analysis was prospectively restricted to episodes reaching the frozen evidence-evaluable endpoint. Native noncompletion was recorded separately and was not imputed as a B1/B2 outcome.",
    ])
    _write_md(REPORT / "FINAL_REPORT.md", "DriveClarify RQ2-T-CG Formal V3 final report", report_lines)
    _append_command("finalize", status)
    return final


def run_all(wall_timeout_s: float) -> Mapping[str, Any]:
    if not (REPORT / "FORMAL_V3_ADMISSION_RECEIPT.json").is_file():
        prepare(wall_timeout_s)
    if not (REPORT / "FORMAL_V3_ROSTER.json").is_file():
        freeze_and_generate_seeds()
    ledger = run_formal(wall_timeout_s)
    if ledger.get("primary_evaluability_gate", {}).get("pass") is True:
        analyze()
    return finalize()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "startup-audit", "freeze", "run-formal", "analyze", "finalize", "run-all"))
    parser.add_argument("--wall-timeout-seconds", type=float, default=2400.0)
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare(args.wall_timeout_seconds)
    elif args.command == "startup-audit":
        result = startup_audit(args.wall_timeout_seconds)
    elif args.command == "freeze":
        result = freeze_and_generate_seeds()
    elif args.command == "run-formal":
        result = run_formal(args.wall_timeout_seconds)
    elif args.command == "analyze":
        result = analyze()
    elif args.command == "finalize":
        result = finalize()
    else:
        result = run_all(args.wall_timeout_seconds)
    status = str(result.get("status", ""))
    return 0 if status.startswith("PASS_") else 2


if __name__ == "__main__":
    raise SystemExit(main())
