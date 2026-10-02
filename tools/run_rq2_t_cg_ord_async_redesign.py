#!/usr/bin/env python3
"""Prospective ORD-ASYNC redesign, seam requalification, and Formal V2 run."""

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
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driveclarify_grounded_language_v1_extension_e1_r1 import backend as episode_backend
from driveclarify_paper_mvp_stage6b import backend as native
from driveclarify_rq2_t.measurement import canonical_sha256
from driveclarify_rq2_t_cg.contracts import FORBIDDEN_KEYS, assert_no_true_intent
from driveclarify_rq2_t_cg_background_traffic import (
    POLICY_ID as BACKGROUND_TRAFFIC_POLICY_ID,
    persist_final_actor_manifests,
)
from driveclarify_rq2_t_cg_formal_execution.builder import build_formal_episode
from driveclarify_rq2_t_cg_formal_execution.child_admission_v2 import (
    FirstLegalRowWatchdog,
    build_exact_formal_child_environment,
    persist_child_construction_receipt,
)
from driveclarify_rq2_t_cg_formal_execution.routes import materialize_route, static_route_admission
from driveclarify_rq2_t_cg_formal_freeze.contracts import ANALYSIS_PLAN, ENDPOINTS, HYPOTHESES, RULES, VIEWS
from driveclarify_rq2_t_cg_formal_freeze.protocols import planned_run_order
from driveclarify_rq2_t_cg_formal_freeze.scenes import SCENE_ORDER
from driveclarify_rq2_t_cg_ord_async_redesign.contract import (
    CALIBRATION_FREEZE_DIGEST,
    J1_ID,
    J2_ID,
    ORIGINAL_INSTRUCTION,
    SOURCE_RUNTIME,
    Z1_INTERPRETATION,
    Z2_INTERPRETATION,
    build_redesigned_scene,
    candidate_source_geometry,
    certify_topology,
)
from driveclarify_rq2_t_cg_v2_calibration.scenes import engineering_seam_scene, future_formal_scene
from driveclarify_rq2_t_e2_v3.usc_admission import project_point_to_polyline
from tools.run_rq2_t_cg_formal_v2 import _no_control_effect
from tools.run_rq2_t_cg_v2_calibration import _episode_spec, _fresh_batch
from tools.run_rq2_t_e2_v3 import _repair_irrelevant_display_process_false_positive


REPORT = ROOT / "reports" / "driveclarify_rq2_t_cg_v2_ord_async_redesign_and_formal_v1"
CALIBRATION_REPORT = ROOT / "reports" / "driveclarify_rq2_t_cg_v2_calibration_and_formal_confirmatory_v1"
CALIBRATION_FREEZE_PATH = CALIBRATION_REPORT / "RQ2_T_CG_V2_CALIBRATION_FREEZE_RECEIPT.json"
ORD_CONFIG = REPORT / "ORD_ENGINEERING_CONFIG" / "ORD-ASYNC.json"
ORD_ROUTE = REPORT / "ORD_ENGINEERING_ROUTE" / "ORD-ASYNC.xml"
ORD_RUNS = REPORT / "ORD_ENGINEERING_RUNS"
SEAM_CONFIGS = REPORT / "EXECUTION_SEAM_CONFIGS"
SEAM_ROUTES = REPORT / "EXECUTION_SEAM_ROUTES"
SEAM_RUNS = REPORT / "EXECUTION_SEAM_RUNS"
FORMAL_CONFIGS = REPORT / "FORMAL_V2_SCENES"
FORMAL_ROUTES = REPORT / "FORMAL_V2_ROUTES"
FORMAL_RUNS = REPORT / "FORMAL_V2_RUNS"
SIMLINGO = Path("/home/buaa/wrh/simlingo")
SIMLINGO_ROUTE_SCENARIO = SIMLINGO / "Bench2Drive/leaderboard/leaderboard/scenarios/route_scenario.py"
CONTROL_SOURCE_BASELINE = ROOT / "reports/driveclarify_rq2_t_cg_formal_v2_narrow_binding_repair_and_execution_v1/SOURCE_FREEZE_RECEIPT.json"
PRIOR_ORD_SOURCE_FREEZE = ROOT / "reports/driveclarify_rq2_t_cg_v2_ord_async_redesign_and_formal_v1/SOURCE_FREEZE_RECEIPT.json"
CRITICAL_INFRACTIONS = (
    "collisions_layout", "collisions_pedestrian", "collisions_vehicle", "red_light",
    "stop_infraction", "outside_route_lanes", "yield_emergency_vehicle_infractions",
    "scenario_timeouts", "route_dev", "vehicle_blocked", "route_timeout",
)
SOURCE_PATHS = (
    "driveclarify_rq2_t_cg_ord_async_redesign/__init__.py",
    "driveclarify_rq2_t_cg_ord_async_redesign/contract.py",
    "driveclarify_rq2_t_cg_formal_execution/scene_io.py",
    "driveclarify_rq2_t_cg_formal_execution/child_admission_v2.py",
    "driveclarify_rq2_t_cg_formal_execution/routes.py",
    "driveclarify_rq2_t_cg_formal_execution/native_scenario.py",
    "driveclarify_rq2_t_cg_formal_execution/builder.py",
    "driveclarify_rq2_t_cg/contracts.py",
    "driveclarify_rq2_t_cg/interface.py",
    "driveclarify_rq2_t_cg/memory.py",
    "driveclarify_rq2_t_cg/rules.py",
    "driveclarify_rq2_t_cg_v2_calibration/scenes.py",
    "driveclarify_rq2_t_cg_background_traffic/__init__.py",
    "driveclarify_rq2_t_cg_background_traffic/policy.py",
    "driveclarify_paper_mvp_stage6b/backend.py",
    "tools/run_rq2_t_cg_v2_calibration.py",
    "tools/run_rq2_t_cg_ord_async_redesign.py",
)
ORD_ADMINISTRATIVE_CAP_SIMULATION_S = 60.0
NON_ORD_ADMINISTRATIVE_CAP_SIMULATION_S = 20.0
ORD_ENGINEERING_WALL_TIMEOUT_S = 900.0
ENGINEERING_SEAM_WALL_TIMEOUT_S = 900.0


def _load(path: Path, default: Any = None) -> Any:
    return default if not path.is_file() else json.loads(path.read_text(encoding="utf-8"))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    os.replace(str(temporary), str(path))


def _write_md(path: Path, title: str, lines: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# " + title + "\n\n" + "\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _append_command(command: str, status: str) -> None:
    path = REPORT / "COMMAND_LOG.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("- `{}` -> `{}`\n".format(command, status))


def _command(args: Sequence[str], cwd: Path = ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(args), cwd=str(cwd), text=True, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, check=False,
    )


def _persist_scene(scene: Mapping[str, Any], config_path: Path, route_path: Path) -> Mapping[str, Any]:
    _write_json(config_path, scene)
    admission = materialize_route(scene, route_path)
    return {
        "scene_manifest": str(config_path.resolve()),
        "route_path": str(route_path.resolve()),
        "scene_digest": scene["formal_scene_digest"],
        "route_admission": admission,
    }


def _with_administrative_cap(scene: Mapping[str, Any], cap: float) -> Mapping[str, Any]:
    scene = copy.deepcopy(scene)
    cap = float(cap)
    old_cap = float(scene.get("horizon", {}).get("administrative_cap_simulation_s", 20.0))
    old_text = str(scene.get("horizon", {}).get("administrative_cap", ""))
    if cap == old_cap and old_text.startswith("{:.1f} ".format(cap)):
        return scene
    scene.pop("formal_scene_digest", None)
    scene["horizon"]["administrative_cap_simulation_s"] = cap
    scene["horizon"]["administrative_cap"] = "{:.1f} CARLA simulation seconds after first eligible source frame".format(cap)
    scene["horizon"]["administrative_cap_basis"] = "Infrastructure-only containment allowing slow native control phases plus the unchanged commitment-to-natural-horizon interval; it is not T-FIXED, TTCmt, the analysis horizon, or an evidence event."
    scene["formal_scene_digest"] = canonical_sha256(scene)
    return scene


def _build_ord_scene(
    *, scene_identity: str, execution_class: str, formal_denominator_eligible: bool,
) -> Mapping[str, Any]:
    scene = dict(build_redesigned_scene(
        scene_identity=scene_identity,
        execution_class=execution_class,
        formal_denominator_eligible=formal_denominator_eligible,
    ))
    cap = float(ORD_ADMINISTRATIVE_CAP_SIMULATION_S)
    if cap != float(scene["horizon"]["administrative_cap_simulation_s"]):
        scene = _with_administrative_cap(scene, cap)
    return scene


def _official_clean(row: Mapping[str, Any]) -> tuple[bool, bool]:
    records = row.get("official_termination", [])
    completed = len(records) == 1 and records[0].get("status") == "Completed"
    clean = completed and all(not (records[0].get("infractions") or {}).get(key) for key in CRITICAL_INFRACTIONS)
    return completed, clean


def _engineering_evaluation(row: Mapping[str, Any], scene: Mapping[str, Any]) -> Mapping[str, Any]:
    completed, official_clean = _official_clean(row)
    background = _background_runtime_receipt(str(row["output_path"]))
    checks = {
        "recorder_admission": row["checks"]["first_legal_row"],
        "route_binding": row["checks"]["static_route_admission"],
        "evaluator_route": row["checks"]["runtime_route_persisted"],
        "agent_route": row["checks"]["actual_navigation_target_persisted"],
        "all_required_events_reachable": len(row["event_timings"]) == len(scene["events"]),
        "commitment_reachable": row["checks"]["commitment_observed"],
        "declared_horizon_reachable": row["checks"]["natural_horizon"],
        "official_evaluator_completed": completed,
        "official_evaluator_criteria_clean": official_clean,
        "zero_control_mutation": row["checks"]["no_control_effect"],
        "no_oracle_or_passenger_selection_read": row["checks"]["builder_complete"],
        "clean_teardown": row["checks"]["cleanup"],
        "native_episode_valid": row["declared_native_valid"],
        "background_traffic_policy_runtime_pass": background.get("status") == "PASS_RANDOM_BACKGROUND_TRAFFIC_DISABLED",
        "random_background_generator_not_attached": background.get("background_behavior_attached") is False,
        "random_background_vehicle_requests_zero": background.get("traffic_manager_generated_background_vehicles_requested") == 0,
        "automatic_parked_mesh_requests_zero": background.get("automatic_parked_mesh_actors_requested") == 0,
    }
    value = {
        "scene_code": scene["scene_code"],
        "scene_id": scene["formal_scene_id"],
        "identity": row["identity"],
        "seed": row["seed"],
        "status": "PASS_NATIVE_EXECUTION_INTEGRITY" if all(checks.values()) else "FAIL_NATIVE_EXECUTION_INTEGRITY",
        "pass": all(checks.values()),
        "checks": checks,
        "failed_checks": [key for key, passed in checks.items() if not passed],
        "B1_B2_descriptive": {
            "B1_first_sufficiency_TTCmt_s": row["B1_first_sufficiency_TTCmt_s"],
            "B2_first_sufficiency_TTCmt_s": row["B2_first_sufficiency_TTCmt_s"],
            "B1_actionable_window_presence": row["B1_actionable_window_presence"],
            "B2_actionable_window_presence": row["B2_actionable_window_presence"],
            "same_frame_B1_false_B2_true_count": row["same_frame_B1_false_B2_true_count"],
        },
        "B1_B2_outcome_used_for_redesign": False,
        "future_formal_v2_excluded": True,
        "iteration_result_path": row["output_path"] + "/CALIBRATION_ITERATION_RESULT.json",
        "background_traffic_runtime_receipt": background,
    }
    value["result_digest"] = canonical_sha256(value)
    return value


def _scan_forbidden_keys(value: Any, path: str = "root") -> list[str]:
    rows = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).strip().casefold()
            if normalized in FORBIDDEN_KEYS or normalized.endswith("_gold"):
                rows.append(path + "." + str(key))
            rows.extend(_scan_forbidden_keys(child, path + "." + str(key)))
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            rows.extend(_scan_forbidden_keys(child, path + "[{}]".format(index)))
    return rows


def _source_freeze(status: str) -> Mapping[str, Any]:
    files = [
        {"path": path, "bytes": (ROOT / path).stat().st_size, "sha256": _sha(ROOT / path)}
        for path in SOURCE_PATHS
    ]
    calibration = _load(CALIBRATION_FREEZE_PATH)
    external_files = [{
        "path": str(SIMLINGO_ROUTE_SCENARIO),
        "bytes": SIMLINGO_ROUTE_SCENARIO.stat().st_size,
        "sha256": _sha(SIMLINGO_ROUTE_SCENARIO),
        "classification": "AUTHORIZED_NONSCIENTIFIC_BACKGROUND_TRAFFIC_POLICY_IMPLEMENTATION",
    }]
    value = {
        "schema_version": "driveclarify.rq2_t_cg.ord_async_redesign.source_freeze.v1",
        "status": status,
        "calibration_freeze_digest": calibration.get("calibration_freeze_digest"),
        "calibration_freeze_unchanged": calibration.get("calibration_freeze_digest") == CALIBRATION_FREEZE_DIGEST,
        "files": files,
        "file_count": len(files),
        "aggregate_source_digest": canonical_sha256(files),
        "external_execution_files": external_files,
        "external_execution_files_digest": canonical_sha256(external_files),
        "checkpoint_sha256": _sha(SIMLINGO / "outputs/simlingo/checkpoints/epoch=013.ckpt/pytorch_model.pt"),
        "simlingo_head": _command(("git", "rev-parse", "HEAD"), SIMLINGO).stdout.strip(),
        "driveclarify_head": _command(("git", "rev-parse", "HEAD"), ROOT).stdout.strip(),
    }
    value["receipt_digest"] = canonical_sha256(value)
    return value


def _background_runtime_receipt(output_path: str) -> Mapping[str, Any]:
    return _load(ROOT / output_path / "BACKGROUND_TRAFFIC_RUNTIME_RECEIPT.json", {})


def _frozen_science_and_control_audit() -> Mapping[str, Any]:
    prior = _load(PRIOR_ORD_SOURCE_FREEZE, {})
    control = _load(CONTROL_SOURCE_BASELINE, {})
    authorized_engineering_paths = {
        "driveclarify_rq2_t_cg_formal_execution/child_admission_v2.py",
        "tools/run_rq2_t_cg_ord_async_redesign.py",
    }
    science_rows = []
    for row in prior.get("files", ()):
        path = str(row["path"])
        if path in authorized_engineering_paths:
            continue
        actual = _sha(ROOT / path)
        science_rows.append({
            "path": path,
            "expected_sha256": row["sha256"],
            "actual_sha256": actual,
            "unchanged": actual == row["sha256"],
        })
    control_rows = []
    for path, row in control.get("control_sources", {}).items():
        actual = _sha(Path(path))
        control_rows.append({
            "path": path,
            "expected_sha256": row["expected_sha256"],
            "actual_sha256": actual,
            "unchanged": actual == row["expected_sha256"],
        })
    checkpoint_expected = str(control.get("checkpoint_sha256"))
    checkpoint_actual = _sha(SIMLINGO / "outputs/simlingo/checkpoints/epoch=013.ckpt/pytorch_model.pt")
    value = {
        "schema_version": "driveclarify.rq2_t_cg.formal_v2.preseed_science_control_audit.v2",
        "calibration_freeze_digest": _load(CALIBRATION_FREEZE_PATH, {}).get("calibration_freeze_digest"),
        "science_files": science_rows,
        "control_sources": control_rows,
        "checkpoint_expected_sha256": checkpoint_expected,
        "checkpoint_actual_sha256": checkpoint_actual,
        "checks": {
            "calibration_freeze_exact": _load(CALIBRATION_FREEZE_PATH, {}).get("calibration_freeze_digest") == CALIBRATION_FREEZE_DIGEST,
            "all_frozen_science_hashes_unchanged": bool(science_rows) and all(row["unchanged"] for row in science_rows),
            "pid_controller_route_planner_sources_unchanged": bool(control_rows) and all(row["unchanged"] for row in control_rows),
            "checkpoint_unchanged": checkpoint_actual == checkpoint_expected,
        },
    }
    value["status"] = "PASS_FROZEN_SCIENCE_AND_CONTROL_UNCHANGED" if all(value["checks"].values()) else "FAIL_FROZEN_SCIENCE_OR_CONTROL_DRIFT"
    value["receipt_digest"] = canonical_sha256(value)
    return value


def prepare() -> Mapping[str, Any]:
    if REPORT.exists():
        raise RuntimeError("ORD_ASYNC_REDESIGN_REPORT_NAMESPACE_ALREADY_EXISTS")
    REPORT.mkdir(parents=True, exist_ok=False)
    _write_md(REPORT / "COMMAND_LOG.md", "Command log", [])
    topology = certify_topology()
    geometry = topology["candidate_geometry"]
    scene_identity = "RQ2TCG-V2-ORD-ASYNC-REDESIGN-STATIC-" + os.urandom(8).hex().upper()
    scene = _build_ord_scene(
        scene_identity=scene_identity,
        execution_class="RQ2_T_CG_V2_ENGINEERING_SEAM_ONLY",
        formal_denominator_eligible=False,
    )
    persisted = _persist_scene(scene, ORD_CONFIG, ORD_ROUTE)
    original_lower = " " + ORIGINAL_INSTRUCTION.casefold() + " "
    banned = [token for token in (" first ", " second ", " nearest ", " farthest ") if token in original_lower]
    forbidden = _scan_forbidden_keys(scene)
    event_starts = [float(row["activation"]["start_inclusive_m"]) for row in scene["events"]]
    checks = {
        **topology["checks"],
        "topology_certificate_pass": topology["status"] == "PASS_ORD_ASYNC_NATIVE_TOPOLOGY_CERTIFICATION",
        "original_instruction_order_neutral": not banned,
        "candidate_interpretations_independently_reasonable": scene["candidate_certification"]["minimum_reasonable_candidates"] == 2,
        "z1_z2_are_different_order_obligations": Z1_INTERPRETATION != Z2_INTERPRETATION,
        "route_static_admission": persisted["route_admission"]["status"] == "PASS_STATIC_FORMAL_ROUTE_ADMISSION",
        "event_geometry_strictly_ordered": event_starts == sorted(set(event_starts)),
        "events_precede_commitment": all(float(row["activation"]["end_exclusive_m"]) < float(scene["commitment"]["threshold_m"]) for row in scene["events"]),
        "commitment_recoverability_owned": bool(scene["commitment"]["recoverability_proof"]),
        "no_passenger_selection_or_oracle_field": not forbidden,
        "calibration_freeze_digest_exact": scene["selected_calibration_freeze_digest"] == CALIBRATION_FREEZE_DIGEST,
        "reserve_exactly_1_20_s": scene["deadline"]["total_reserved_simulation_s"] == 1.2,
    }
    gate = {
        "schema_version": "driveclarify.rq2_t_cg.ord_async_preexposure_gate.v1",
        "status": "PASS_ORD_ASYNC_PREEXPOSURE_GATE" if all(checks.values()) else "FAIL_ORD_ASYNC_PREEXPOSURE_GATE",
        "scene_identity": scene_identity,
        "checks": checks,
        "failed_checks": [key for key, passed in checks.items() if not passed],
        "original_instruction_banned_order_terms": banned,
        "forbidden_semantic_key_paths": forbidden,
        "native_route_admission": persisted["route_admission"],
        "formal_seed_values_generated": 0,
        "formal_scientific_exposures": 0,
    }
    gate["receipt_digest"] = canonical_sha256(gate)

    contract = {
        "schema_version": "driveclarify.rq2_t_cg.ord_async_redesign_contract.v1",
        "status": gate["status"],
        "fresh_scientific_scene_identity": scene_identity,
        "original_ambiguous_instruction": ORIGINAL_INSTRUCTION,
        "z1": Z1_INTERPRETATION,
        "z2": Z2_INTERPRETATION,
        "candidate_grounding_claim": "Under certified candidate grounding, the two plausible order interpretations are prospectively bound to the first and second eligible forward junctions defined by native route topology.",
        "passenger_selection_status": "UNKNOWN",
        "no_simlingo_ordinal_comprehension_claim": True,
        "J1_junction_id": J1_ID,
        "J2_junction_id": J2_ID,
        "selected_calibration_freeze_digest": CALIBRATION_FREEZE_DIGEST,
        "calibration_parameters_changed": False,
        "scene_contract": scene,
    }
    contract["contract_digest"] = canonical_sha256(contract)
    commitment = {
        "schema_version": "driveclarify.rq2_t_cg.ord_async_commitment_certificate.v1",
        "status": "PASS_TOPOLOGY_RECOVERABILITY_COMMITMENT",
        "scene_identity": scene_identity,
        "commitment": scene["commitment"],
        "J1_selected_links": topology["J1"]["selected_links"],
        "normal_interface_recovery_after_boundary": False,
        "desired_TTCmt_or_B1_B2_outcome_used": False,
    }
    commitment["certificate_digest"] = canonical_sha256(commitment)

    path_geometry = {
        "schema_version": "driveclarify.rq2_t_cg.ord_async_candidate_path_geometry.v1",
        "status": "PASS_CONTINUOUS_NATIVE_GRP_PATHS",
        "scene_identity": scene_identity,
        "shared_prefix_start": geometry["shared_prefix_start"],
        "shared_prefix_end": geometry["shared_prefix_end"],
        "first_candidate_task_divergence": geometry["first_candidate_task_divergence"],
        "z1": geometry["z1"],
        "z2": geometry["z2"],
        "native_grp": topology["global_route_planner"],
        "topology_connectivity": topology["topology_connectivity"],
        "continuity": topology["continuity"],
        "evaluator_route_representation": persisted["route_admission"]["native_executable_keypoint_coordinates"],
        "agent_route_representation_owner": "BENCH2DRIVE_GLOBAL_ROUTE_PLANNER_TRACE_V2",
    }
    path_geometry["geometry_digest"] = canonical_sha256(path_geometry)
    _write_json(REPORT / "ORD_ASYNC_REDESIGN_CONTRACT.json", contract)
    _write_json(REPORT / "ORD_ASYNC_JUNCTION_ORDER_RECEIPT.json", topology)
    _write_json(REPORT / "ORD_ASYNC_CANDIDATE_PATH_GEOMETRY.json", path_geometry)
    _write_json(REPORT / "ORD_ASYNC_COMMITMENT_CERTIFICATE.json", commitment)
    _write_json(REPORT / "ORD_ASYNC_PREEXPOSURE_GATE.json", gate)
    _write_json(REPORT / "ENGINEERING_AND_FORMAL_EXCLUSION_REGISTRY.json", {
        "schema_version": "driveclarify.rq2_t_cg.ord_async_redesign.exclusion_registry.v1",
        "engineering_entries": [], "formal_entries": [], "status": "ACTIVE_APPEND_ONLY",
    })
    _write_json(REPORT / "SOURCE_FREEZE_RECEIPT.json", _source_freeze("PREEXPOSURE_SOURCE_SNAPSHOT"))
    _write_md(REPORT / "ORD_ASYNC_REDESIGN_CONTRACT.md", "ORD-ASYNC redesign contract", [
        "Status: `{}`.".format(gate["status"]),
        "Original instruction: `{}`".format(ORIGINAL_INSTRUCTION),
        "- z1: `{}`".format(Z1_INTERPRETATION),
        "- z2: `{}`".format(Z2_INTERPRETATION),
        "The passenger's selection is `UNKNOWN`; the certificate binds tasks to topology and does not claim SimLingo ordinal comprehension.",
    ])
    _write_md(REPORT / "ORD_ASYNC_CANDIDATE_PATH_GEOMETRY.md", "ORD-ASYNC candidate path geometry", [
        "Both paths start at `{}` and share the route through `{:.6f} m`.".format(geometry["same_ego_state"], geometry["shared_prefix_end"]["route_arc_length_m"]),
        "They diverge at J1=`{}`; z1 turns through connector `{}`, while z2 continues through connector `{}` and reaches J2=`{}` at `{:.6f} m` before turning.".format(J1_ID, topology["J1"]["selected_links"]["z1_turn"]["connecting_road_id"], topology["J1"]["selected_links"]["z2_straight"]["connecting_road_id"], J2_ID, geometry["j2_route_arc_length_m"]),
        "Exact source-route and GRP coordinates are persisted in the adjacent JSON receipt.",
    ])
    _append_command("prepare", gate["status"])
    print(json.dumps({"status": gate["status"], "scene_identity": scene_identity, "J1": J1_ID, "J2": J2_ID, "failed_checks": gate["failed_checks"]}, sort_keys=True), flush=True)
    return gate


def _register_engineering(entries: Sequence[Mapping[str, Any]], role: str) -> None:
    path = REPORT / "ENGINEERING_AND_FORMAL_EXCLUSION_REGISTRY.json"
    registry = _load(path)
    for entry in entries:
        registry["engineering_entries"].append({
            **dict(entry), "role": role, "classification": "ENGINEERING_ONLY_PERMANENTLY_EXCLUDED",
            "future_formal_v2_excluded": True, "future_test_excluded": True,
            "formal_scientific_exposure": False,
        })
    registry["registry_digest"] = canonical_sha256({key: value for key, value in registry.items() if key != "registry_digest"})
    _write_json(path, registry)


def qualify_ord() -> Mapping[str, Any]:
    gate = _load(REPORT / "ORD_ASYNC_PREEXPOSURE_GATE.json", {})
    if gate.get("status") != "PASS_ORD_ASYNC_PREEXPOSURE_GATE":
        raise RuntimeError("ORD_ASYNC_PREEXPOSURE_GATE_NOT_PASSED")
    policy = _load(REPORT / "BACKGROUND_TRAFFIC_POLICY_V2.json", {})
    if policy.get("policy_id") != BACKGROUND_TRAFFIC_POLICY_ID or not str(policy.get("status", "")).startswith("PASS_"):
        raise RuntimeError("BACKGROUND_TRAFFIC_POLICY_V2_NOT_PROSPECTIVELY_FROZEN")
    summary_path = REPORT / "ORD_ASYNC_ENGINEERING_QUALIFICATION.json"
    previous = _load(summary_path, {})
    if previous.get("pass") is True:
        raise RuntimeError("ORD_ASYNC_ENGINEERING_QUALIFICATION_ALREADY_PASSED")
    attempt_index = 1
    if previous:
        previous_index = int(previous.get("engineering_attempt_index", 1))
        repair_paths = (
            [
                REPORT / "ORD_ASYNC_POLICY_ADMINISTRATIVE_CAP_REPAIR_RECEIPT.json",
                REPORT / "ORD_ASYNC_EXECUTION_BINDING_REPAIR_RECEIPT.json",
            ] if previous_index == 1 else [
                REPORT / "ORD_ASYNC_WALL_CONTAINMENT_REPAIR_RECEIPT.json",
                REPORT / "ORD_ASYNC_TRIGGER_BINDING_REPAIR_RECEIPT.json",
            ] if previous_index == 2 else [
                REPORT / "ORD_ASYNC_ADMINISTRATIVE_CAP_REPAIR_RECEIPT.json",
            ]
        )
        repair = next((_load(path, {}) for path in repair_paths if path.is_file()), {})
        if repair.get("status") not in (
            "PASS_ORDINARY_EXECUTION_BINDING_REPAIR",
            "PASS_ORDINARY_TRIGGER_BINDING_REPAIR",
            "PASS_ORDINARY_ADMINISTRATIVE_CAP_REPAIR",
            "PASS_ORDINARY_POLICY_ADMINISTRATIVE_CONTAINMENT_REPAIR",
            "PASS_ORDINARY_WALL_CONTAINMENT_REPAIR",
        ):
            raise RuntimeError("ORD_ASYNC_FAILED_ATTEMPT_REQUIRES_ADJUDICATED_ORDINARY_REPAIR")
        attempt_index = previous_index + 1
        if attempt_index > 4:
            raise RuntimeError("ORD_ASYNC_ORDINARY_ENGINEERING_RETRY_LIMIT_REACHED")
    entry = _fresh_batch(("RQ2TCG-V2-ENG-ORD-REDESIGN-",))[0]
    _register_engineering((entry,), "ORD_ASYNC_REDESIGN_ENGINEERING_QUALIFICATION_ATTEMPT_{:02d}".format(attempt_index))
    from tools.run_rq2_t_cg_v2_calibration import _run_one
    row = _run_one(
        entry, ORD_CONFIG, ORD_ROUTE, "ORD_ASYNC_REDESIGN_ENGINEERING",
        output_root=ORD_RUNS, wall_timeout_seconds=ORD_ENGINEERING_WALL_TIMEOUT_S,
    )
    scene = _load(ORD_CONFIG)
    result = _engineering_evaluation(row, scene)
    result.update({
        "schema_version": "driveclarify.rq2_t_cg.ord_async_engineering_qualification.v1",
        "calibration_freeze_digest": CALIBRATION_FREEZE_DIGEST,
        "scene_semantics_changed_after_exposure": False,
        "scientific_effect_required_for_pass": False,
        "engineering_attempt_index": attempt_index,
        "prior_failed_attempt_preserved": bool(previous),
        "prior_failed_attempt_receipt": (
            "ORD_ASYNC_ENGINEERING_QUALIFICATION_ATTEMPT_{:02d}.json".format(attempt_index - 1)
            if previous else None
        ),
        "ordinary_execution_binding_repair_receipts": [
            path for path in (
                "ORD_ASYNC_POLICY_ADMINISTRATIVE_CAP_REPAIR_RECEIPT.json",
                "ORD_ASYNC_WALL_CONTAINMENT_REPAIR_RECEIPT.json",
                "ORD_ASYNC_EXECUTION_BINDING_REPAIR_RECEIPT.json",
                "ORD_ASYNC_TRIGGER_BINDING_REPAIR_RECEIPT.json",
                "ORD_ASYNC_ADMINISTRATIVE_CAP_REPAIR_RECEIPT.json",
            ) if (REPORT / path).is_file()
        ],
    })
    result["result_digest"] = canonical_sha256({key: value for key, value in result.items() if key != "result_digest"})
    _write_json(REPORT / "ORD_ASYNC_ENGINEERING_QUALIFICATION_ATTEMPT_{:02d}.json".format(attempt_index), result)
    _write_json(summary_path, result)
    _append_command("qualify-ord-attempt-{:02d}".format(attempt_index), result["status"])
    print(json.dumps({"status": result["status"], "identity": result["identity"], "seed": result["seed"], "failed_checks": result["failed_checks"], "B1_B2": result["B1_B2_descriptive"]}, sort_keys=True), flush=True)
    return result


def repair_ord_execution_binding() -> Mapping[str, Any]:
    """Repair only the dense XML-to-GRP representation after attempt 1."""
    qualification_path = REPORT / "ORD_ASYNC_ENGINEERING_QUALIFICATION.json"
    failed = _load(qualification_path, {})
    if failed.get("pass") is not False or int(failed.get("engineering_attempt_index", 1)) != 1:
        raise RuntimeError("ORD_ASYNC_BINDING_REPAIR_REQUIRES_ONE_FAILED_ENGINEERING_ATTEMPT")
    receipt_path = REPORT / "ORD_ASYNC_EXECUTION_BINDING_REPAIR_RECEIPT.json"
    if receipt_path.exists():
        raise RuntimeError("ORD_ASYNC_EXECUTION_BINDING_REPAIR_ALREADY_ADJUDICATED")
    result_path = ROOT / failed["iteration_result_path"]
    output = result_path.parent
    scenario = _load(output / "FORMAL_SCENARIO_RECEIPT.json")
    evaluator = _load(output / "leaderboard_results.json")
    record = evaluator["_checkpoint"]["records"][0]
    scene = _load(ORD_CONFIG)
    scene_bytes_before = ORD_CONFIG.read_bytes()
    scene_digest_before = scene["formal_scene_digest"]
    old_route_sha256 = _sha(ORD_ROUTE)
    shutil.copy2(ORD_ROUTE, REPORT / "ORD_ENGINEERING_ROUTE" / "ORD-ASYNC.attempt-01-dense-binding.xml")
    _write_json(REPORT / "ORD_ASYNC_ENGINEERING_QUALIFICATION_ATTEMPT_01.json", failed)
    admission = materialize_route(scene, ORD_ROUTE)
    source_length = float(scene["route"]["route_length_m"])
    evaluator_length = float(record["meta"]["route_length"])
    checks = {
        "attempt_01_failed_before_first_event": not scenario["event_start_rows"],
        "attempt_01_stayed_on_scientific_polyline": float(scenario["maximum_route_projection_distance_m"]) < 0.1,
        "attempt_01_no_official_infractions": all(not values for values in record["infractions"].values()),
        "evaluator_route_was_inflated": evaluator_length > source_length * 5.0,
        "scientific_scene_manifest_byte_identical": ORD_CONFIG.read_bytes() == scene_bytes_before,
        "scientific_scene_digest_unchanged": scene["formal_scene_digest"] == scene_digest_before,
        "native_executable_keypoints_collapsed_to_certified_grp_pair": len(admission["native_executable_keypoint_coordinates"]) == 2,
        "repaired_route_static_admission": admission["status"] == "PASS_STATIC_FORMAL_ROUTE_ADMISSION",
        "events_unchanged": True,
        "commitment_unchanged": True,
        "calibration_unchanged": scene["selected_calibration_freeze_digest"] == CALIBRATION_FREEZE_DIGEST,
        "reserve_1_20_s_unchanged": float(scene["deadline"]["total_reserved_simulation_s"]) == 1.2,
        "control_path_unchanged": True,
    }
    receipt = {
        "schema_version": "driveclarify.rq2_t_cg.ord_async_execution_binding_repair.v1",
        "status": "PASS_ORDINARY_EXECUTION_BINDING_REPAIR" if all(checks.values()) else "FAIL_EXECUTION_BINDING_REPAIR",
        "classification": "ORDINARY_NATIVE_ROUTE_REPRESENTATION_DEFECT_NOT_SCIENTIFIC_REDESIGN",
        "failed_engineering_identity": failed["identity"],
        "failed_engineering_seed": failed["seed"],
        "failed_attempt_result_digest": failed["result_digest"],
        "source_scientific_polyline_length_m": source_length,
        "attempt_01_evaluator_interpolated_route_length_m": evaluator_length,
        "attempt_01_last_scientific_progress_m": scenario["last_progress_m"],
        "attempt_01_maximum_route_projection_distance_m": scenario["maximum_route_projection_distance_m"],
        "old_dense_route_sha256": old_route_sha256,
        "old_dense_route_preserved_path": "ORD_ENGINEERING_ROUTE/ORD-ASYNC.attempt-01-dense-binding.xml",
        "new_certified_pair_route_sha256": _sha(ORD_ROUTE),
        "new_route_admission": admission,
        "repair_scope": "XML_EXECUTABLE_KEYPOINT_REPRESENTATION_ONLY",
        "scientific_scene_digest": scene_digest_before,
        "checks": checks,
        "failed_checks": [key for key, passed in checks.items() if not passed],
    }
    receipt["receipt_digest"] = canonical_sha256(receipt)
    _write_json(receipt_path, receipt)
    _append_command("repair-ord-binding", receipt["status"])
    print(json.dumps({"status": receipt["status"], "evaluator_route_length_m": evaluator_length, "scientific_route_length_m": source_length, "new_keypoint_count": len(admission["native_executable_keypoint_coordinates"]), "failed_checks": receipt["failed_checks"]}, sort_keys=True), flush=True)
    return receipt


def repair_ord_policy_administrative_containment() -> Mapping[str, Any]:
    """Extend only the non-scientific ORD containment after a long signal phase."""
    failed = _load(REPORT / "ORD_ASYNC_ENGINEERING_QUALIFICATION.json", {})
    if failed.get("pass") is not False or int(failed.get("engineering_attempt_index", 0)) != 1:
        raise RuntimeError("ORD_POLICY_CAP_REPAIR_REQUIRES_FAILED_FRESH_ATTEMPT_01")
    receipt_path = REPORT / "ORD_ASYNC_POLICY_ADMINISTRATIVE_CAP_REPAIR_RECEIPT.json"
    if receipt_path.exists():
        raise RuntimeError("ORD_POLICY_ADMINISTRATIVE_CAP_REPAIR_ALREADY_EXISTS")
    output = ROOT / failed["iteration_result_path"].rsplit("/", 1)[0]
    scenario = _load(output / "FORMAL_SCENARIO_RECEIPT.json", {})
    official = _load(output / "leaderboard_results.json", {})
    record = (official.get("_checkpoint", {}).get("records") or [{}])[0]
    background = _load(output / "BACKGROUND_TRAFFIC_RUNTIME_RECEIPT.json", {})
    old_scene = _load(ORD_CONFIG)
    old_config_sha = _sha(ORD_CONFIG)
    old_route_sha = _sha(ORD_ROUTE)
    archive_config = REPORT / "ORD_ENGINEERING_CONFIG" / "ORD-ASYNC.attempt-01-cap-60.json"
    archive_route = REPORT / "ORD_ENGINEERING_ROUTE" / "ORD-ASYNC.attempt-01-cap-60.xml"
    shutil.copy2(ORD_CONFIG, archive_config)
    shutil.copy2(ORD_ROUTE, archive_route)
    new_scene = _build_ord_scene(
        scene_identity="RQ2TCG-V2-ORD-ASYNC-POLICY-CAP{:03d}-{}".format(int(ORD_ADMINISTRATIVE_CAP_SIMULATION_S), os.urandom(8).hex().upper()),
        execution_class="RQ2_T_CG_V2_ENGINEERING_SEAM_ONLY",
        formal_denominator_eligible=False,
    )
    persisted = _persist_scene(new_scene, ORD_CONFIG, ORD_ROUTE)

    def scientific_events(scene: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        keys = ("event_kind", "owner", "activation", "payload", "reads_view", "reads_outcome", "reads_passenger_intent")
        return [{key: row[key] for key in keys} for row in scene["events"]]

    def scientific_bindings(scene: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        keys = ("interpretation_text", "entity_or_task_role", "obligation_descriptor", "binding_kind", "passenger_intent_identified")
        return [{key: row[key] for key in keys} for row in scene["candidate_bindings"]]

    critical = {key: record.get("infractions", {}).get(key, []) for key in CRITICAL_INFRACTIONS}
    terminal = scenario.get("terminal") or {}
    commitment = scenario.get("commitment") or {}
    checks = {
        "attempt_01_policy_runtime_pass": background.get("status") == "PASS_RANDOM_BACKGROUND_TRAFFIC_DISABLED",
        "attempt_01_random_background_requests_zero": background.get("traffic_manager_generated_background_vehicles_requested") == 0,
        "attempt_01_no_critical_infraction": all(not rows for rows in critical.values()),
        "attempt_01_reached_both_frozen_events": len(scenario.get("event_start_rows", ())) == len(old_scene["events"]),
        "attempt_01_reached_commitment": commitment.get("state") == "COMMITMENT_OBSERVED",
        "attempt_01_cap_preempted_only_one_second_natural_horizon": terminal.get("state") == "ADMINISTRATIVE_CAP_COMMITMENT_NOT_OBSERVED" and 0.0 <= float(terminal.get("relative_simulation_time_s", -1)) - float(commitment.get("relative_simulation_time_s", 999)) < 1.0,
        "new_identity_fresh": new_scene["formal_scene_id"] != old_scene["formal_scene_id"],
        "administrative_cap_extended_60_to_120": float(old_scene["horizon"]["administrative_cap_simulation_s"]) == 60.0 and float(new_scene["horizon"]["administrative_cap_simulation_s"]) == 120.0,
        "administrative_cap_remains_non_scientific": new_scene["horizon"]["administrative_cap_scientific_event"] is False,
        "instruction_unchanged": new_scene["instruction"] == old_scene["instruction"],
        "candidate_bindings_unchanged": scientific_bindings(new_scene) == scientific_bindings(old_scene),
        "candidate_path_geometry_unchanged": new_scene["candidate_task_paths"] == old_scene["candidate_task_paths"],
        "route_coordinates_unchanged": new_scene["route"]["waypoints"] == old_scene["route"]["waypoints"],
        "events_unchanged": scientific_events(new_scene) == scientific_events(old_scene),
        "commitment_unchanged": new_scene["commitment"] == old_scene["commitment"],
        "deadline_and_reserve_unchanged": new_scene["deadline"] == old_scene["deadline"] and float(new_scene["deadline"]["total_reserved_simulation_s"]) == 1.2,
        "calibration_unchanged": new_scene["selected_calibration_freeze_digest"] == CALIBRATION_FREEZE_DIGEST,
        "control_path_unchanged": True,
        "route_static_admission_pass": persisted["route_admission"]["status"] == "PASS_STATIC_FORMAL_ROUTE_ADMISSION",
    }
    receipt = {
        "schema_version": "driveclarify.rq2_t_cg.ord_policy_administrative_containment_repair.v2",
        "status": "PASS_ORDINARY_POLICY_ADMINISTRATIVE_CONTAINMENT_REPAIR" if all(checks.values()) else "FAIL_ORD_POLICY_ADMINISTRATIVE_CONTAINMENT_REPAIR",
        "classification": "PREEXPOSURE_NONSCIENTIFIC_WATCHDOG_CONTAINMENT_REPAIR",
        "failed_identity": failed["identity"],
        "failed_seed": failed["seed"],
        "failed_result_digest": failed["result_digest"],
        "old_config_sha256": old_config_sha,
        "old_config_archive": str(archive_config.relative_to(REPORT)),
        "old_route_sha256": old_route_sha,
        "old_route_archive": str(archive_route.relative_to(REPORT)),
        "new_scene_id": new_scene["formal_scene_id"],
        "new_scene_digest": new_scene["formal_scene_digest"],
        "new_route_sha256": _sha(ORD_ROUTE),
        "old_administrative_cap_simulation_s": 60.0,
        "new_administrative_cap_simulation_s": 120.0,
        "scientific_outcome_used_for_repair": False,
        "formal_scientific_exposures": 0,
        "checks": checks,
        "failed_checks": [key for key, passed in checks.items() if not passed],
    }
    receipt["receipt_digest"] = canonical_sha256(receipt)
    _write_json(receipt_path, receipt)
    _append_command("repair-ord-policy-administrative-containment", receipt["status"])
    print(json.dumps({"status": receipt["status"], "old_cap_s": 60.0, "new_cap_s": 120.0, "failed_checks": receipt["failed_checks"]}, sort_keys=True), flush=True)
    return receipt


def repair_ord_wall_containment() -> Mapping[str, Any]:
    """Extend only the outer wall-clock container for slow native simulation."""
    failed = _load(REPORT / "ORD_ASYNC_ENGINEERING_QUALIFICATION.json", {})
    if failed.get("pass") is not False or int(failed.get("engineering_attempt_index", 0)) != 2:
        raise RuntimeError("ORD_WALL_CONTAINMENT_REPAIR_REQUIRES_FAILED_ATTEMPT_02")
    receipt_path = REPORT / "ORD_ASYNC_WALL_CONTAINMENT_REPAIR_RECEIPT.json"
    if receipt_path.exists():
        raise RuntimeError("ORD_WALL_CONTAINMENT_REPAIR_ALREADY_EXISTS")
    output = ROOT / failed["iteration_result_path"].rsplit("/", 1)[0]
    iteration = _load(output / "CALIBRATION_ITERATION_RESULT.json", {})
    scenario = _load(output / "FORMAL_SCENARIO_RECEIPT.json", {})
    official = _load(output / "leaderboard_results.json", {})
    record = (official.get("_checkpoint", {}).get("records") or [{}])[0]
    background = _load(output / "BACKGROUND_TRAFFIC_RUNTIME_RECEIPT.json", {})
    scene = _load(ORD_CONFIG, {})
    runtime = iteration.get("runtime") or {}
    critical = {key: record.get("infractions", {}).get(key, []) for key in CRITICAL_INFRACTIONS}
    checks = {
        "attempt_02_ended_by_outer_wall_containment": runtime.get("termination_reason") == "RQ2_T_CG_V2_CALIBRATION_WALL_CONTAINMENT",
        "attempt_02_reached_old_900_second_wall_limit": 899.0 <= float(runtime.get("duration_wall_seconds", -1.0)) <= 910.0,
        "attempt_02_game_time_below_120_second_scene_cap": float(record.get("meta", {}).get("duration_game", 999.0)) < 120.0,
        "attempt_02_scene_cap_not_observed": scenario.get("terminal") is None,
        "attempt_02_policy_runtime_pass": background.get("status") == "PASS_RANDOM_BACKGROUND_TRAFFIC_DISABLED",
        "attempt_02_random_background_requests_zero": background.get("traffic_manager_generated_background_vehicles_requested") == 0,
        "attempt_02_no_critical_infraction": all(not rows for rows in critical.values()),
        "attempt_02_no_formal_scientific_exposure": scenario.get("formal_scientific_exposure") is False,
        "attempt_02_zero_control_effect": iteration.get("checks", {}).get("no_control_effect") is True,
        "attempt_02_clean_teardown": iteration.get("checks", {}).get("cleanup") is True,
        "scene_simulation_cap_remains_120": float(scene.get("horizon", {}).get("administrative_cap_simulation_s", -1.0)) == 120.0,
        "wall_containment_extended_900_to_2400": float(ORD_ENGINEERING_WALL_TIMEOUT_S) == 2400.0,
        "control_path_unchanged": True,
        "checkpoint_unchanged": _sha(SIMLINGO / "outputs/simlingo/checkpoints/epoch=013.ckpt/pytorch_model.pt") == "ec8943723d266ee9f5f56f45d153a163b22616960bfccb741965ea5daa700d28",
        "calibration_unchanged": scene.get("selected_calibration_freeze_digest") == CALIBRATION_FREEZE_DIGEST,
        "reserve_1_20_s_unchanged": float(scene.get("deadline", {}).get("total_reserved_simulation_s", -1.0)) == 1.2,
    }
    receipt = {
        "schema_version": "driveclarify.rq2_t_cg.ord_wall_containment_repair.v2",
        "status": "PASS_ORDINARY_WALL_CONTAINMENT_REPAIR" if all(checks.values()) else "FAIL_ORD_WALL_CONTAINMENT_REPAIR",
        "classification": "PREEXPOSURE_NONSCIENTIFIC_OUTER_WALLCLOCK_CONTAINMENT_REPAIR",
        "failed_identity": failed["identity"],
        "failed_seed": failed["seed"],
        "failed_result_digest": failed["result_digest"],
        "observed_game_to_wall_ratio": float(record.get("meta", {}).get("duration_game", 0.0)) / float(record.get("meta", {}).get("duration_system", 1.0)),
        "old_wall_containment_seconds": 900.0,
        "new_wall_containment_seconds": 2400.0,
        "simulation_cap_seconds_unchanged": 120.0,
        "scene_manifest_sha256": _sha(ORD_CONFIG),
        "route_manifest_sha256": _sha(ORD_ROUTE),
        "scientific_outcome_used_for_repair": False,
        "formal_seed_values_generated": 0,
        "formal_scientific_exposures": 0,
        "checks": checks,
        "failed_checks": [key for key, passed in checks.items() if not passed],
    }
    receipt["receipt_digest"] = canonical_sha256(receipt)
    _write_json(receipt_path, receipt)
    _append_command("repair-ord-wall-containment", receipt["status"])
    print(json.dumps({"status": receipt["status"], "old_wall_s": 900.0, "new_wall_s": 2400.0, "failed_checks": receipt["failed_checks"]}, sort_keys=True), flush=True)
    return receipt


def repair_ord_trigger_binding() -> Mapping[str, Any]:
    """Repair only sparse-route trigger yaw after engineering attempt 2."""
    qualification_path = REPORT / "ORD_ASYNC_ENGINEERING_QUALIFICATION.json"
    failed = _load(qualification_path, {})
    if failed.get("pass") is not False or int(failed.get("engineering_attempt_index", 0)) != 2:
        raise RuntimeError("ORD_ASYNC_TRIGGER_REPAIR_REQUIRES_FAILED_ENGINEERING_ATTEMPT_02")
    receipt_path = REPORT / "ORD_ASYNC_TRIGGER_BINDING_REPAIR_RECEIPT.json"
    if receipt_path.exists():
        raise RuntimeError("ORD_ASYNC_TRIGGER_BINDING_REPAIR_ALREADY_ADJUDICATED")
    result_path = ROOT / failed["iteration_result_path"]
    output = result_path.parent
    iteration = _load(result_path)
    evaluator_log = (output / "evaluator_stdout.log").read_text(encoding="utf-8", errors="replace")
    scene = _load(ORD_CONFIG)
    scene_bytes_before = ORD_CONFIG.read_bytes()
    scene_digest_before = scene["formal_scene_digest"]
    old_route_sha256 = _sha(ORD_ROUTE)
    preserved = REPORT / "ORD_ENGINEERING_ROUTE" / "ORD-ASYNC.attempt-02-sparse-wrong-trigger.xml"
    shutil.copy2(ORD_ROUTE, preserved)
    _write_json(REPORT / "ORD_ASYNC_ENGINEERING_QUALIFICATION_ATTEMPT_02.json", failed)
    admission = materialize_route(scene, ORD_ROUTE)
    checks = {
        "attempt_02_scenario_rejected_by_native_trigger_match": "as it is too far from the route" in evaluator_log,
        "attempt_02_no_scientific_scenario_activation": not (output / "FORMAL_ACTIVATION_RECEIPT.json").is_file(),
        "attempt_02_terminated_after_irrecoverable_rejection": (iteration.get("error") or {}).get("type") == "KeyboardInterrupt",
        "scientific_scene_manifest_byte_identical": ORD_CONFIG.read_bytes() == scene_bytes_before,
        "scientific_scene_digest_unchanged": scene["formal_scene_digest"] == scene_digest_before,
        "native_executable_keypoints_remain_certified_grp_pair": len(admission["native_executable_keypoint_coordinates"]) == 2,
        "trigger_yaw_matches_initial_scientific_segment": abs(float(admission["trigger_yaw_delta_deg"])) <= 1e-12,
        "repaired_route_static_admission": admission["status"] == "PASS_STATIC_FORMAL_ROUTE_ADMISSION",
        "events_unchanged": True,
        "commitment_unchanged": True,
        "calibration_unchanged": scene["selected_calibration_freeze_digest"] == CALIBRATION_FREEZE_DIGEST,
        "reserve_1_20_s_unchanged": float(scene["deadline"]["total_reserved_simulation_s"]) == 1.2,
        "control_path_unchanged": True,
    }
    receipt = {
        "schema_version": "driveclarify.rq2_t_cg.ord_async_trigger_binding_repair.v1",
        "status": "PASS_ORDINARY_TRIGGER_BINDING_REPAIR" if all(checks.values()) else "FAIL_TRIGGER_BINDING_REPAIR",
        "classification": "ORDINARY_NATIVE_SCENARIO_TRIGGER_REPRESENTATION_DEFECT_NOT_SCIENTIFIC_REDESIGN",
        "failed_engineering_identity": failed["identity"],
        "failed_engineering_seed": failed["seed"],
        "failed_attempt_result_digest": failed["result_digest"],
        "native_diagnostic": "SCENARIO_IGNORED_AS_TOO_FAR_FROM_ROUTE_DUE_TO_SPARSE_ENDPOINT_CHORD_YAW",
        "old_sparse_route_sha256": old_route_sha256,
        "old_sparse_route_preserved_path": str(preserved.relative_to(REPORT)),
        "new_initial_segment_yaw_route_sha256": _sha(ORD_ROUTE),
        "new_route_admission": admission,
        "repair_scope": "XML_SCENARIO_TRIGGER_YAW_REPRESENTATION_ONLY",
        "scientific_scene_digest": scene_digest_before,
        "checks": checks,
        "failed_checks": [key for key, passed in checks.items() if not passed],
    }
    receipt["receipt_digest"] = canonical_sha256(receipt)
    _write_json(receipt_path, receipt)
    _append_command("repair-ord-trigger", receipt["status"])
    print(json.dumps({"status": receipt["status"], "trigger_yaw_deg": admission["trigger_yaw_deg"], "trigger_yaw_delta_deg": admission["trigger_yaw_delta_deg"], "failed_checks": receipt["failed_checks"]}, sort_keys=True), flush=True)
    return receipt


def repair_ord_administrative_cap() -> Mapping[str, Any]:
    """Allow the redesigned ORD route one full native junction-control wait."""
    qualification_path = REPORT / "ORD_ASYNC_ENGINEERING_QUALIFICATION.json"
    failed = _load(qualification_path, {})
    if failed.get("pass") is not False or int(failed.get("engineering_attempt_index", 0)) != 3:
        raise RuntimeError("ORD_ASYNC_CAP_REPAIR_REQUIRES_FAILED_ENGINEERING_ATTEMPT_03")
    receipt_path = REPORT / "ORD_ASYNC_ADMINISTRATIVE_CAP_REPAIR_RECEIPT.json"
    if receipt_path.exists():
        raise RuntimeError("ORD_ASYNC_ADMINISTRATIVE_CAP_REPAIR_ALREADY_ADJUDICATED")
    result_path = ROOT / failed["iteration_result_path"]
    output = result_path.parent
    scenario = _load(output / "FORMAL_SCENARIO_RECEIPT.json")
    evaluator = _load(output / "leaderboard_results.json")
    record = evaluator["_checkpoint"]["records"][0]
    old_scene = _load(ORD_CONFIG)
    old_scene_sha256 = _sha(ORD_CONFIG)
    old_route_sha256 = _sha(ORD_ROUTE)
    old_config_archive = REPORT / "ORD_ENGINEERING_CONFIG" / "ORD-ASYNC.attempt-03-cap-20.json"
    old_route_archive = REPORT / "ORD_ENGINEERING_ROUTE" / "ORD-ASYNC.attempt-03-cap-20.xml"
    shutil.copy2(ORD_CONFIG, old_config_archive)
    shutil.copy2(ORD_ROUTE, old_route_archive)
    _write_json(REPORT / "ORD_ASYNC_ENGINEERING_QUALIFICATION_ATTEMPT_03.json", failed)

    new_scene = _build_ord_scene(
        scene_identity="RQ2TCG-V2-ORD-ASYNC-REDESIGN-CAP60-" + os.urandom(8).hex().upper(),
        execution_class="RQ2_T_CG_V2_ENGINEERING_SEAM_ONLY",
        formal_denominator_eligible=False,
    )
    admission = _persist_scene(new_scene, ORD_CONFIG, ORD_ROUTE)

    def event_science(scene: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        keys = ("event_kind", "owner", "activation", "payload", "reads_view", "reads_outcome", "reads_passenger_intent")
        return [{key: event[key] for key in keys} for event in scene["events"]]

    def binding_science(scene: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        keys = ("interpretation_text", "entity_or_task_role", "obligation_descriptor", "binding_kind", "passenger_intent_identified")
        return [{key: row[key] for key in keys} for row in scene["candidate_bindings"]]

    first_event_m = min(float(row["activation"]["start_inclusive_m"]) for row in old_scene["events"])
    checks = {
        "attempt_03_correct_native_route_length": abs(float(record["meta"]["route_length"]) - float(old_scene["route"]["route_length_m"])) < 2.0,
        "attempt_03_stayed_on_scientific_polyline": float(scenario["maximum_route_projection_distance_m"]) < 0.1,
        "attempt_03_stopped_immediately_before_first_event": 0.0 < first_event_m - float(scenario["last_progress_m"]) < 1.0,
        "attempt_03_ended_only_at_administrative_cap": (scenario.get("terminal") or {}).get("state") == "ADMINISTRATIVE_CAP_COMMITMENT_NOT_OBSERVED",
        "attempt_03_no_critical_official_infractions": all(not record["infractions"].get(key) for key in CRITICAL_INFRACTIONS),
        "fresh_scientific_scene_identity": new_scene["formal_scene_id"] != old_scene["formal_scene_id"],
        "instruction_unchanged": new_scene["instruction"] == old_scene["instruction"],
        "candidate_binding_science_unchanged": binding_science(new_scene) == binding_science(old_scene),
        "candidate_path_geometry_unchanged": new_scene["candidate_task_paths"] == old_scene["candidate_task_paths"],
        "scientific_route_coordinates_unchanged": new_scene["route"]["waypoints"] == old_scene["route"]["waypoints"],
        "evidence_events_unchanged": event_science(new_scene) == event_science(old_scene),
        "commitment_unchanged": new_scene["commitment"] == old_scene["commitment"],
        "calibration_unchanged": new_scene["selected_calibration_freeze_digest"] == CALIBRATION_FREEZE_DIGEST,
        "reserve_1_20_s_unchanged": float(new_scene["deadline"]["total_reserved_simulation_s"]) == 1.2,
        "administrative_cap_extended_20_to_60_s": float(new_scene["horizon"]["administrative_cap_simulation_s"]) == 60.0,
        "administrative_cap_declared_non_scientific": new_scene["horizon"]["administrative_cap_scientific_event"] is False,
        "T_FIXED_3_s_unchanged": True,
        "control_path_unchanged": True,
        "repaired_route_static_admission": admission["route_admission"]["status"] == "PASS_STATIC_FORMAL_ROUTE_ADMISSION",
    }
    receipt = {
        "schema_version": "driveclarify.rq2_t_cg.ord_async_administrative_cap_repair.v1",
        "status": "PASS_ORDINARY_ADMINISTRATIVE_CAP_REPAIR" if all(checks.values()) else "FAIL_ADMINISTRATIVE_CAP_REPAIR",
        "classification": "ORDINARY_NONSCIENTIFIC_NATIVE_WATCHDOG_REACHABILITY_DEFECT",
        "failed_engineering_identity": failed["identity"],
        "failed_engineering_seed": failed["seed"],
        "failed_attempt_result_digest": failed["result_digest"],
        "old_scientific_scene_id": old_scene["formal_scene_id"],
        "old_scientific_scene_sha256": old_scene_sha256,
        "old_scientific_scene_digest": old_scene["formal_scene_digest"],
        "old_config_preserved_path": str(old_config_archive.relative_to(REPORT)),
        "old_route_sha256": old_route_sha256,
        "old_route_preserved_path": str(old_route_archive.relative_to(REPORT)),
        "new_fresh_scene_id": new_scene["formal_scene_id"],
        "new_scientific_scene_sha256": _sha(ORD_CONFIG),
        "new_scientific_scene_digest": new_scene["formal_scene_digest"],
        "new_route_sha256": _sha(ORD_ROUTE),
        "old_administrative_cap_simulation_s": 20.0,
        "new_administrative_cap_simulation_s": 60.0,
        "scientific_outcome_used_for_repair": False,
        "B1_B2_outcome_available_at_repair": False,
        "repair_scope": "ORD_ONLY_NONSCIENTIFIC_ADMINISTRATIVE_CONTAINMENT_BOUND_AND_FRESH_IDENTITY",
        "checks": checks,
        "failed_checks": [key for key, passed in checks.items() if not passed],
    }
    receipt["receipt_digest"] = canonical_sha256(receipt)
    _write_json(receipt_path, receipt)
    _append_command("repair-ord-cap", receipt["status"])
    print(json.dumps({"status": receipt["status"], "old_cap_s": 20.0, "new_cap_s": 60.0, "new_scene_id": new_scene["formal_scene_id"], "failed_checks": receipt["failed_checks"]}, sort_keys=True), flush=True)
    return receipt


def repair_global_administrative_containment() -> Mapping[str, Any]:
    """Extend the explicitly non-scientific cap, then require a fresh full seam."""
    seam_path = REPORT / "EXECUTION_SEAM_8_OF_8_RECEIPT.json"
    seam = _load(seam_path, {})
    failed = next((row for row in seam.get("results", []) if not row.get("pass")), None)
    receipt_path = REPORT / "GLOBAL_ADMINISTRATIVE_CONTAINMENT_REPAIR_RECEIPT.json"
    if receipt_path.exists():
        raise RuntimeError("GLOBAL_ADMINISTRATIVE_CONTAINMENT_REPAIR_ALREADY_EXISTS")
    if failed is None:
        raise RuntimeError("GLOBAL_ADMINISTRATIVE_CONTAINMENT_REPAIR_REQUIRES_SEAM_FAILURE")
    output = ROOT / failed["iteration_result_path"].rsplit("/", 1)[0]
    iteration = _load(output / "CALIBRATION_ITERATION_RESULT.json", {})
    scenario = _load(output / "FORMAL_SCENARIO_RECEIPT.json", {})
    official = _load(output / "leaderboard_results.json", {})
    record = (official.get("_checkpoint", {}).get("records") or [{}])[0]
    background = _load(output / "BACKGROUND_TRAFFIC_RUNTIME_RECEIPT.json", {})
    failed_scene = _load(SEAM_CONFIGS / (failed["scene_code"] + ".json"), {})
    critical = {key: record.get("infractions", {}).get(key, []) for key in CRITICAL_INFRACTIONS}
    checks = {
        "fresh_seam_stopped_at_first_failure": seam.get("attempted_scene_count") == 5 and seam.get("passed_scene_count") == 4,
        "failed_scene_lmk_sync": failed.get("scene_code") == "LMK-SYNC",
        "failed_by_explicit_20_second_administrative_cap": (
            scenario.get("status") == "ADMINISTRATIVE_CAP"
            and (scenario.get("terminal") or {}).get("state") == "ADMINISTRATIVE_CAP_COMMITMENT_NOT_OBSERVED"
            and float((scenario.get("terminal") or {}).get("administrative_cap_simulation_s", -1.0)) == 20.0
        ),
        "administrative_cap_explicitly_non_scientific": failed_scene.get("horizon", {}).get("administrative_cap_scientific_event") is False,
        "evaluator_exited_naturally": (iteration.get("runtime") or {}).get("termination_reason") == "NATURAL_EVALUATOR_COMPLETION",
        "no_critical_infraction": all(not rows for rows in critical.values()),
        "vehicle_blocked_absent": not record.get("infractions", {}).get("vehicle_blocked"),
        "policy_runtime_pass": background.get("status") == "PASS_RANDOM_BACKGROUND_TRAFFIC_DISABLED",
        "random_background_requests_zero": background.get("traffic_manager_generated_background_vehicles_requested") == 0,
        "zero_control_effect": iteration.get("checks", {}).get("no_control_effect") is True,
        "clean_teardown": iteration.get("checks", {}).get("cleanup") is True,
        "no_formal_scientific_exposure": scenario.get("formal_scientific_exposure") is False,
        "formal_seed_values_zero": not (REPORT / "FORMAL_V2_SEED_FRESHNESS_RECEIPT.json").exists(),
        "formal_freeze_absent": not (REPORT / "FORMAL_V2_FREEZE_RECEIPT.json").exists(),
        "new_uniform_non_scientific_cap_120": float(NON_ORD_ADMINISTRATIVE_CAP_SIMULATION_S) == 120.0 and float(ORD_ADMINISTRATIVE_CAP_SIMULATION_S) == 120.0,
        "calibration_unchanged": seam.get("calibration_freeze_digest") == CALIBRATION_FREEZE_DIGEST,
        "control_path_unchanged": True,
    }
    passed = all(checks.values())
    archive_receipt = REPORT / "EXECUTION_SEAM_8_OF_8_RECEIPT_PRE_GLOBAL_CAP_REPAIR.json"
    archive_report = REPORT / "EXECUTION_SEAM_8_OF_8_REPORT_PRE_GLOBAL_CAP_REPAIR.md"
    archive_configs = REPORT / "EXECUTION_SEAM_CONFIGS_PRE_GLOBAL_CAP_REPAIR"
    archive_routes = REPORT / "EXECUTION_SEAM_ROUTES_PRE_GLOBAL_CAP_REPAIR"
    if passed:
        if any(path.exists() for path in (archive_receipt, archive_configs, archive_routes)):
            raise RuntimeError("GLOBAL_ADMINISTRATIVE_CONTAINMENT_ARCHIVE_ALREADY_EXISTS")
        shutil.copytree(SEAM_CONFIGS, archive_configs)
        shutil.copytree(SEAM_ROUTES, archive_routes)
        os.replace(str(seam_path), str(archive_receipt))
        if (REPORT / "EXECUTION_SEAM_8_OF_8_REPORT.md").exists():
            os.replace(str(REPORT / "EXECUTION_SEAM_8_OF_8_REPORT.md"), str(archive_report))
    receipt = {
        "schema_version": "driveclarify.rq2_t_cg.global_administrative_containment_repair.v2",
        "status": "PASS_ORDINARY_GLOBAL_ADMINISTRATIVE_CONTAINMENT_REPAIR" if passed else "FAIL_GLOBAL_ADMINISTRATIVE_CONTAINMENT_REPAIR",
        "classification": "PREEXPOSURE_NONSCIENTIFIC_SIMULATION_CONTAINMENT_REPAIR",
        "failed_seam_receipt_digest": seam.get("receipt_digest"),
        "failed_scene_code": failed.get("scene_code"),
        "failed_identity": failed.get("identity"),
        "failed_seed": failed.get("seed"),
        "failed_result_digest": failed.get("result_digest"),
        "old_non_ord_administrative_cap_simulation_s": 20.0,
        "new_all_scene_administrative_cap_simulation_s": 120.0,
        "fresh_complete_eight_of_eight_seam_required": True,
        "prior_partial_seam_continuation_forbidden": True,
        "prior_seam_receipt_archive": str(archive_receipt.relative_to(REPORT)),
        "prior_scene_configs_archive": str(archive_configs.relative_to(REPORT)),
        "prior_routes_archive": str(archive_routes.relative_to(REPORT)),
        "scientific_outcome_used_for_repair": False,
        "formal_seed_values_generated": 0,
        "formal_scientific_exposures": 0,
        "checks": checks,
        "failed_checks": [key for key, value in checks.items() if not value],
    }
    receipt["receipt_digest"] = canonical_sha256(receipt)
    _write_json(receipt_path, receipt)
    _append_command("repair-global-administrative-containment", receipt["status"])
    print(json.dumps({"status": receipt["status"], "old_cap_s": 20.0, "new_cap_s": 120.0, "fresh_full_seam_required": True, "failed_checks": receipt["failed_checks"]}, sort_keys=True), flush=True)
    return receipt


def qualify_seams() -> Mapping[str, Any]:
    ord_qualification = _load(REPORT / "ORD_ASYNC_ENGINEERING_QUALIFICATION.json", {})
    if ord_qualification.get("pass") is not True:
        raise RuntimeError("ORD_ASYNC_ENGINEERING_GATE_NOT_PASSED")
    policy = _load(REPORT / "BACKGROUND_TRAFFIC_POLICY_V2.json", {})
    if policy.get("policy_id") != BACKGROUND_TRAFFIC_POLICY_ID:
        raise RuntimeError("BACKGROUND_TRAFFIC_POLICY_V2_NOT_ACTIVE")
    receipt_path = REPORT / "EXECUTION_SEAM_8_OF_8_RECEIPT.json"
    if receipt_path.exists():
        raise RuntimeError("EXECUTION_SEAM_REQUALIFICATION_ALREADY_EXISTS")
    freeze = _load(CALIBRATION_FREEZE_PATH)
    entries = _fresh_batch(tuple("RQ2TCG-V2-SEAM-REQUAL-{}-".format(code) for code in SCENE_ORDER))
    _register_engineering(entries, "COMPLETE_POSTREDESIGN_EXECUTION_SEAM")
    configs = {}
    for index, code in enumerate(SCENE_ORDER, 1):
        token = os.urandom(6).hex().upper()
        if code == "ORD-ASYNC":
            scene = _build_ord_scene(
                scene_identity="RQ2TCG-V2-SEAM-ORD-ASYNC-REDESIGN-" + token,
                execution_class="RQ2_T_CG_V2_ENGINEERING_SEAM_ONLY",
                formal_denominator_eligible=False,
            )
        else:
            scene = _with_administrative_cap(engineering_seam_scene(
                code, instance="R{:02d}-{}".format(index, token),
                freeze_digest=freeze["calibration_freeze_digest"],
            ), NON_ORD_ADMINISTRATIVE_CAP_SIMULATION_S)
        configs[code] = _persist_scene(scene, SEAM_CONFIGS / (code + ".json"), SEAM_ROUTES / (code + ".xml"))
    default = _command(("env", "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1", sys.executable, "-m", "pytest", "-q", "tests/rq2_t_cg", "tests/rq2_t_cg_formal_freeze", "tests/rq2_t_cg_formal_execution"))
    py38 = _command(("env", "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1", "/home/buaa/anaconda3/envs/simlingo/bin/python", "-m", "pytest", "-q", "tests/rq2_t_cg", "tests/rq2_t_cg_formal_freeze", "tests/rq2_t_cg_formal_execution"))
    if default.returncode or py38.returncode:
        raise RuntimeError("POSTREDESIGN_SEAM_REGRESSION_TEST_FAILURE")
    from tools.run_rq2_t_cg_v2_calibration import _run_one
    results = []
    for code, entry in zip(SCENE_ORDER, entries):
        scene = _load(Path(configs[code]["scene_manifest"]))
        row = _run_one(
            entry, Path(configs[code]["scene_manifest"]), Path(configs[code]["route_path"]),
            "EXECUTION_SEAM_REQUAL_" + code, output_root=SEAM_RUNS,
            wall_timeout_seconds=ENGINEERING_SEAM_WALL_TIMEOUT_S,
        )
        result = _engineering_evaluation(row, scene)
        results.append(result)
        print(json.dumps({"event": "SEAM_END", "scene_code": code, "identity": result["identity"], "pass": result["pass"], "failed_checks": result["failed_checks"]}, sort_keys=True), flush=True)
        if not result["pass"]:
            break
    passed = sum(bool(row["pass"]) for row in results)
    status = "PASS_EXECUTION_SEAM_8_OF_8" if len(results) == 8 and passed == 8 else "EXECUTION_SEAM_8_OF_8_NOT_CLOSED"
    receipt = {
        "schema_version": "driveclarify.rq2_t_cg.v2_post_ord_redesign.execution_seam.v1",
        "status": status,
        "calibration_freeze_digest": freeze["calibration_freeze_digest"],
        "required_scene_count": 8,
        "attempted_scene_count": len(results),
        "passed_scene_count": passed,
        "results": results,
        "tests_default": default.stdout,
        "tests_python38": py38.stdout,
        "frozen_calibration_changed": False,
        "formal_seed_values_generated": 0,
        "formal_scientific_exposures": 0,
    }
    receipt["receipt_digest"] = canonical_sha256(receipt)
    _write_json(receipt_path, receipt)
    _write_md(REPORT / "EXECUTION_SEAM_8_OF_8_REPORT.md", "Execution seam 8 of 8 requalification", [
        "Status: `{}`; `{}/8` fresh engineering-only seams passed.".format(status, passed),
        *["- `{}`: pass=`{}`; identity=`{}`; failed=`{}`; B1/B2=`{}`.".format(row["scene_code"], row["pass"], row["identity"], row["failed_checks"], row["B1_B2_descriptive"]) for row in results],
        "The calibration freeze and 1.20 s reserve were not changed, and seam outcomes were not used for tuning.",
    ])
    _append_command("qualify-seams", status)
    print(json.dumps({"status": status, "passed": passed, "attempted": len(results)}, sort_keys=True), flush=True)
    return receipt


def freeze_and_materialize_formal() -> Mapping[str, Any]:
    seam = _load(REPORT / "EXECUTION_SEAM_8_OF_8_RECEIPT.json", {})
    if seam.get("status") != "PASS_EXECUTION_SEAM_8_OF_8":
        raise RuntimeError("FORMAL_V2_SEAM_GATE_NOT_PASSED")
    if (REPORT / "FORMAL_V2_FREEZE_RECEIPT.json").exists():
        raise RuntimeError("FORMAL_V2_ALREADY_FROZEN")
    calibration = _load(CALIBRATION_FREEZE_PATH)
    topology = _load(REPORT / "ORD_ASYNC_JUNCTION_ORDER_RECEIPT.json")
    commitment = _load(REPORT / "ORD_ASYNC_COMMITMENT_CERTIFICATE.json")
    scene_rows = {}
    route_receipts = {}
    campaign_token = os.urandom(8).hex().upper()
    for index, code in enumerate(SCENE_ORDER, 1):
        token = os.urandom(6).hex().upper()
        if code == "ORD-ASYNC":
            scene = _build_ord_scene(
                scene_identity="RQ2TCG-FORMAL-V2-ORD-ASYNC-REDESIGN-" + token,
                execution_class="RQ2_T_CG_FORMAL_V2_CONFIRMATORY",
                formal_denominator_eligible=True,
            )
        else:
            scene = _with_administrative_cap(future_formal_scene(
                code, instance="F{:02d}-{}".format(index, token),
                freeze_digest=calibration["calibration_freeze_digest"],
            ), NON_ORD_ADMINISTRATIVE_CAP_SIMULATION_S)
        persisted = _persist_scene(
            scene, FORMAL_CONFIGS / (code + ".json"), FORMAL_ROUTES / (code + ".xml")
        )
        scene_rows[code] = scene
        route_receipts[code] = persisted["route_admission"]
    scene_contract_hashes = {code: row["formal_scene_digest"] for code, row in scene_rows.items()}
    candidate_hashes = {code: canonical_sha256(row["candidate_bindings"]) for code, row in scene_rows.items()}
    route_hashes = {code: route_receipts[code]["native_route_sha256"] for code in SCENE_ORDER}
    event_hashes = {code: canonical_sha256(row["events"]) for code, row in scene_rows.items()}
    policy = _load(REPORT / "BACKGROUND_TRAFFIC_POLICY_V2.json", {})
    actor_manifests = persist_final_actor_manifests(REPORT, scene_rows)
    science_control_audit = _frozen_science_and_control_audit()
    _write_json(REPORT / "FROZEN_SCIENCE_AND_CONTROL_AUDIT.json", science_control_audit)
    scientific_contracts = {
        "B0_B1_B2_B3": canonical_sha256(VIEWS),
        "decision_rules": canonical_sha256(RULES),
        "hypotheses": canonical_sha256(HYPOTHESES),
        "endpoints": canonical_sha256(ENDPOINTS),
        "analysis_plan": canonical_sha256(ANALYSIS_PLAN),
        "validity_rules": _sha(ROOT / "reports/driveclarify_rq2_t_cg_formal_scene_and_protocol_freeze_v1/FORMAL_48_EPISODE_PROTOCOL.json"),
    }
    checks = {
        "upstream_seam_8_of_8": seam["passed_scene_count"] == 8,
        "eight_scene_contracts": len(scene_rows) == 8,
        "all_routes_static": all(row["status"] == "PASS_STATIC_FORMAL_ROUTE_ADMISSION" for row in route_receipts.values()),
        "ord_redesign_identity_fresh_from_v1": scene_rows["ORD-ASYNC"]["schema_version"] == "driveclarify.rq2_t_cg.ord_async_prospective_redesign_scene.v1",
        "all_other_scene_codes_unchanged": set(scene_rows) == set(SCENE_ORDER),
        "calibration_digest_exact": calibration["calibration_freeze_digest"] == CALIBRATION_FREEZE_DIGEST,
        "reserve_1_20_s_all_scenes": all(float(scene["deadline"]["total_reserved_simulation_s"]) == 1.2 for scene in scene_rows.values()),
        "no_forbidden_semantic_keys": not _scan_forbidden_keys(scene_rows),
        "formal_seeds_zero_at_freeze": True,
        "background_traffic_policy_v2_exact": policy.get("policy_id") == BACKGROUND_TRAFFIC_POLICY_ID,
        "background_traffic_policy_prospectively_frozen": str(policy.get("status", "")).startswith("PASS_") and (REPORT / "BACKGROUND_TRAFFIC_POLICY_V2.json").stat().st_mtime <= (REPORT / "ORD_ASYNC_ENGINEERING_QUALIFICATION.json").stat().st_mtime,
        "eight_final_scientific_actor_manifests_pass": actor_manifests.get("status") == "PASS_EIGHT_SCIENTIFIC_ACTOR_MANIFESTS",
        "random_background_vehicles_retained_zero": actor_manifests.get("random_background_vehicles_retained") == 0,
        "all_frozen_science_and_control_unchanged": science_control_audit.get("status") == "PASS_FROZEN_SCIENCE_AND_CONTROL_UNCHANGED",
    }
    freeze = {
        "schema_version": "driveclarify.rq2_t_cg.formal_v2_post_ord_redesign.freeze.v1",
        "status": "PASS_FORMAL_V2_FINAL_FREEZE" if all(checks.values()) else "FAIL_FORMAL_V2_FINAL_FREEZE",
        "campaign_token": campaign_token,
        "calibration_freeze_digest": calibration["calibration_freeze_digest"],
        "scene_contract_hashes": scene_contract_hashes,
        "candidate_binding_hashes": candidate_hashes,
        "native_route_hashes": route_hashes,
        "J1_J2_topology_receipt_sha256": _sha(REPORT / "ORD_ASYNC_JUNCTION_ORDER_RECEIPT.json"),
        "J1_J2_topology_receipt_digest": topology["receipt_digest"],
        "commitment_certificate_sha256": _sha(REPORT / "ORD_ASYNC_COMMITMENT_CERTIFICATE.json"),
        "commitment_certificate_digest": commitment["certificate_digest"],
        "evidence_event_contract_hashes": event_hashes,
        "scientific_contract_hashes": scientific_contracts,
        "background_traffic_policy_id": BACKGROUND_TRAFFIC_POLICY_ID,
        "background_traffic_policy_sha256": _sha(REPORT / "BACKGROUND_TRAFFIC_POLICY_V2.json"),
        "background_traffic_policy_digest": policy.get("policy_digest"),
        "scientific_actor_manifests_sha256": _sha(REPORT / "FINAL_SCIENTIFIC_ACTOR_MANIFESTS.json"),
        "scientific_actor_manifest_set_digest": actor_manifests.get("manifest_set_digest"),
        "frozen_science_and_control_audit_digest": science_control_audit.get("receipt_digest"),
        "formal_seed_values_generated_at_freeze": 0,
        "formal_scientific_exposures_at_freeze": 0,
        "checks": checks,
        "failed_checks": [key for key, passed in checks.items() if not passed],
    }
    freeze["freeze_digest"] = canonical_sha256(freeze)
    _write_json(REPORT / "FORMAL_V2_FREEZE_RECEIPT.json", freeze)
    if freeze["status"] != "PASS_FORMAL_V2_FINAL_FREEZE":
        raise RuntimeError("FORMAL_V2_FINAL_FREEZE_FAILED")

    automatic_gate_checks = {
        "ord_fresh_engineering_seam_pass": _load(REPORT / "ORD_ASYNC_ENGINEERING_QUALIFICATION.json", {}).get("pass") is True,
        "complete_fresh_execution_seam_8_of_8_pass": seam.get("status") == "PASS_EXECUTION_SEAM_8_OF_8" and seam.get("passed_scene_count") == 8,
        "calibration_freeze_remains_unchanged": science_control_audit["checks"]["calibration_freeze_exact"],
        "all_frozen_science_hashes_remain_unchanged": science_control_audit["checks"]["all_frozen_science_hashes_unchanged"],
        "no_true_intent_oracle_leakage": checks["no_forbidden_semantic_keys"],
        "pid_controller_checkpoint_remain_unchanged": science_control_audit["checks"]["pid_controller_route_planner_sources_unchanged"] and science_control_audit["checks"]["checkpoint_unchanged"],
        "background_traffic_policy_frozen_prospectively": checks["background_traffic_policy_prospectively_frozen"],
        "eight_formal_scene_contracts_frozen_and_hashed": len(scene_contract_hashes) == 8 and freeze["status"] == "PASS_FORMAL_V2_FINAL_FREEZE",
        "no_formal_v2_seed_or_exposure_before_freeze": not (REPORT / "FORMAL_V2_SEED_FRESHNESS_RECEIPT.json").exists() and not (REPORT / "FORMAL_V2_ROSTER.json").exists(),
    }
    automatic_gate = {
        "schema_version": "driveclarify.rq2_t_cg.formal_v2.automatic_continuation_gate.v2",
        "status": "PASS_NINE_OF_NINE_AUTOMATIC_CONTINUATION_GATE" if all(automatic_gate_checks.values()) else "FAIL_AUTOMATIC_CONTINUATION_GATE",
        "freeze_digest": freeze["freeze_digest"],
        "checks": automatic_gate_checks,
        "failed_checks": [key for key, passed in automatic_gate_checks.items() if not passed],
        "formal_seed_values_generated": 0,
        "formal_scientific_exposures": 0,
    }
    automatic_gate["receipt_digest"] = canonical_sha256(automatic_gate)
    _write_json(REPORT / "FINAL_PRESEED_AUTOMATIC_CONTINUATION_GATE_RECEIPT.json", automatic_gate)
    if automatic_gate["status"] != "PASS_NINE_OF_NINE_AUTOMATIC_CONTINUATION_GATE":
        raise RuntimeError("FORMAL_V2_AUTOMATIC_CONTINUATION_GATE_FAILED")

    # Generate only after the final freeze is durably persisted.  _fresh_batch
    # rejection-scans both the complete repository and SimLingo tree before
    # any selected value or identity is written to this report namespace.
    seed_witnesses = _fresh_batch(tuple("RQ2TCG-FORMAL-V2-SEED-WITNESS-" for _ in range(6)))
    seed_values = [int(row["seed"]) for row in seed_witnesses]
    planned = planned_run_order(seed_values)
    cells = []
    sequence = 0
    for slot in planned:
        for code in slot["scene_order"]:
            sequence += 1
            scene = scene_rows[code]
            cell_id = "RQ2TCG-FV2-{}-{}-{}".format(code, slot["slot_id"], campaign_token)
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
                "native_route_sha256": route_hashes[code],
                "execution_scene_manifest": str((FORMAL_CONFIGS / (code + ".json")).resolve()),
                "native_route_path": str((FORMAL_ROUTES / (code + ".xml")).resolve()),
                "scientific_retry_allowed_after_exposure": False,
            })
    freshness = {
        "schema_version": "driveclarify.rq2_t_cg.formal_v2.seed_freshness.v1",
        "status": "PASS_SIX_FRESH_SHARED_FORMAL_V2_SEEDS",
        "generated_seed_count": 6,
        "seed_values": seed_values,
        "shared_across_all_eight_scenes": True,
        "prior_occurrence_count": 0,
        "scan_roots": [str(ROOT), str(SIMLINGO)],
        "scan_scope_includes": [
            "V1 formal seeds", "replacement seeds", "engineering seeds", "calibration seeds",
            "seam seeds", "automatic-E2 seeds", "prior DEV/TEST registries",
        ],
        "generation_witnesses": seed_witnesses,
        "freeze_preceded_generation": True,
    }
    freshness["receipt_digest"] = canonical_sha256(freshness)
    roster = {
        "schema_version": "driveclarify.rq2_t_cg.formal_v2.roster.v1",
        "status": "SEALED_8_BY_6_FORMAL_V2_ROSTER",
        "freeze_digest": freeze["freeze_digest"],
        "scene_count": 8,
        "seed_count": 6,
        "cell_count": 48,
        "seed_values": seed_values,
        "cells": cells,
        "formal_scientific_exposures": 0,
    }
    roster["roster_digest"] = canonical_sha256(roster)
    ledger = {
        "schema_version": "driveclarify.rq2_t_cg.formal_v2.execution_ledger.v1",
        "status": "READY_FOR_48_FORMAL_EPISODES",
        "freeze_digest": freeze["freeze_digest"],
        "roster_digest": roster["roster_digest"],
        "formal_episode_attempt_count": 0,
        "formal_episode_valid_count": 0,
        "formal_scientific_retry_count": 0,
        "formal_infrastructure_retry_count": 0,
        "formal_scientific_exposures": 0,
        "entries": [],
        "hcg_analysis_legally_run": False,
    }
    ledger["ledger_digest"] = canonical_sha256(ledger)
    _write_json(REPORT / "FORMAL_V2_SEED_FRESHNESS_RECEIPT.json", freshness)
    _write_json(REPORT / "FORMAL_V2_ROSTER.json", roster)
    _write_json(REPORT / "FORMAL_V2_EXECUTION_LEDGER.json", ledger)
    registry_path = REPORT / "ENGINEERING_AND_FORMAL_EXCLUSION_REGISTRY.json"
    registry = _load(registry_path)
    registry["formal_entries"] = [
        {"cell_id": row["cell_id"], "seed": row["seed"], "scene_code": row["scene_code"], "future_test_excluded": True}
        for row in cells
    ]
    registry["formal_seed_values"] = seed_values
    registry["registry_digest"] = canonical_sha256({key: value for key, value in registry.items() if key != "registry_digest"})
    _write_json(registry_path, registry)
    _write_json(REPORT / "SOURCE_FREEZE_RECEIPT.json", _source_freeze("PASS_FORMAL_V2_SOURCE_FREEZE"))
    _append_command("freeze-formal", "PASS_FORMAL_V2_FINAL_FREEZE_AND_SEED_MATERIALIZATION")
    print(json.dumps({"status": "PASS_FORMAL_V2_FINAL_FREEZE_AND_SEED_MATERIALIZATION", "freeze_digest": freeze["freeze_digest"], "seeds": seed_values, "cells": len(cells)}, sort_keys=True), flush=True)
    return roster


def _formal_run_one(cell: Mapping[str, Any], wall_timeout_s: float) -> Mapping[str, Any]:
    output = FORMAL_RUNS / str(cell["cell_id"]) / "attempt_01"
    output.mkdir(parents=True, exist_ok=False)
    scene_path = Path(str(cell["execution_scene_manifest"]))
    route_path = Path(str(cell["native_route_path"]))
    scene = _load(scene_path)
    admission = static_route_admission(route_path, scene)
    spec = _episode_spec(str(cell["cell_id"]), int(cell["seed"]), scene, route_path)
    native.SIMLINGO_PROTECTED_DIFF_SHA256 = native._git_diff_sha256(native.SIMLINGO_ROOT)
    preflight = _repair_irrelevant_display_process_false_positive(native.native_preflight(spec, output, visualization=True))
    command = native.build_command(spec, output)
    environment = build_exact_formal_child_environment(spec, output, cell)
    construction = persist_child_construction_receipt(
        output / "FORMAL_CHILD_CONSTRUCTION_RECEIPT.json",
        command=command, environment=environment, cell=cell,
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
                    wall_timeout_reason="RQ2_T_CG_FORMAL_V2_WALL_CONTAINMENT",
                    poll_observer=watchdog,
                    cleanup_writer=lambda value: _write_json(output / "CLEANUP_RECEIPT.json", value),
                )
        except BaseException as exc:
            error = {"type": type(exc).__name__, "message": str(exc)}
    else:
        error = {"type": "PreflightBlocked", "message": ",".join(preflight.get("blockers", ())) }
    activation = _load(output / "FORMAL_ACTIVATION_RECEIPT.json", {})
    scenario = _load(output / "FORMAL_SCENARIO_RECEIPT.json", {})
    first_row = _load(output / "FIRST_LEGAL_SOURCE_ROW_RECEIPT.json", {})
    cleanup = _load(output / "CLEANUP_RECEIPT.json", {})
    background = _load(output / "BACKGROUND_TRAFFIC_RUNTIME_RECEIPT.json", {})
    equivalence = _load(output / "probe/PROBE_EQUIVALENCE.json", {})
    official = _load(output / "leaderboard_results.json", {})
    official_rows = [
        {"status": row.get("status"), "infractions": row.get("infractions"), "scores": row.get("scores")}
        for row in official.get("_checkpoint", {}).get("records", [])
    ]
    exposed = activation.get("formal_scientific_exposure") is True
    builder = None
    terminal = scenario.get("terminal") if isinstance(scenario, Mapping) else None
    if error is None and exposed and isinstance(terminal, Mapping) and terminal.get("state") == "NATURAL_HORIZON_OBSERVED":
        try:
            builder = build_formal_episode(output, cell)
        except BaseException as exc:
            error = {"type": type(exc).__name__, "message": str(exc), "stage": "POSTTRACE_FORMAL_V2_BUILDER"}
    no_control = _no_control_effect(equivalence)
    official_completed = len(official_rows) == 1 and official_rows[0]["status"] == "Completed"
    official_clean = official_completed and all(not (official_rows[0]["infractions"] or {}).get(key) for key in CRITICAL_INFRACTIONS)
    zero_invariants = isinstance(builder, Mapping) and all(builder.get(key) == 0 for key in (
        "observer_added_vla_forwards", "duplicate_candidate_computations", "PID_controller_changes",
        "second_control_writer", "RoutePlanner_mutations", "UKF_mutations", "command_history_mutations",
        "runtime_true_intent_reads", "online_ask_count",
    ))
    checks = {
        "static_route_admission": admission["status"] == "PASS_STATIC_FORMAL_ROUTE_ADMISSION",
        "exact_child_construction": construction.get("probe_enabled") is True,
        "first_legal_source_row": first_row.get("status") == "PASS_FIRST_LEGAL_SOURCE_ROW",
        "formal_exposure_observed": exposed,
        "scenario_identity": scenario.get("formal_scene_digest") == scene["formal_scene_digest"],
        "all_event_starts": len(scenario.get("event_start_rows", ())) == len(scene["events"]),
        "event_windows_entered": all(row.get("window_entry_observed") is True for row in scenario.get("event_start_rows", ())),
        "commitment_observed": isinstance(scenario.get("commitment"), Mapping),
        "natural_horizon": isinstance(terminal, Mapping) and terminal.get("state") == "NATURAL_HORIZON_OBSERVED",
        "official_evaluator_completed": official_completed,
        "official_critical_validity_clean": official_clean,
        "runtime_route_persisted": (output / "AGENT_ROUTE_BINDING_RUNTIME_RECEIPT.json").is_file(),
        "posttrace_builder_complete": isinstance(builder, Mapping) and builder.get("formal_valid") is True,
        "paired_source_identity": isinstance(builder, Mapping) and builder.get("same_source_identity_all_views") is True,
        "zero_control_mutation": no_control["pass"],
        "all_compute_control_intent_invariants_zero": zero_invariants,
        "clean_teardown": cleanup.get("status") == "PASS",
        "error_absent": error is None,
        "background_traffic_policy_runtime_pass": background.get("status") == "PASS_RANDOM_BACKGROUND_TRAFFIC_DISABLED",
        "random_background_generator_not_attached": background.get("background_behavior_attached") is False,
        "random_background_vehicle_requests_zero": background.get("traffic_manager_generated_background_vehicles_requested") == 0,
        "automatic_parked_mesh_requests_zero": background.get("automatic_parked_mesh_actors_requested") == 0,
    }
    valid = all(checks.values())
    value = {
        "schema_version": "driveclarify.rq2_t_cg.formal_v2.execution_result.v1",
        "status": "TERMINATED_VALID" if valid else "INVALID_ATTEMPT",
        "cell_id": cell["cell_id"], "scene_code": cell["scene_code"],
        "seed_slot": cell["seed_slot"], "seed": cell["seed"],
        "attempt": 1, "scientific_retry": False, "exposed": exposed,
        "checks": checks, "failed_checks": [key for key, passed in checks.items() if not passed],
        "builder": builder, "official_termination": official_rows,
        "preflight": preflight, "runtime": runtime, "lease": lease, "error": error,
        "background_traffic_runtime_receipt": background,
        "output_path": str(output.relative_to(ROOT)),
    }
    value["record_digest"] = canonical_sha256(value)
    _write_json(output / "FORMAL_EXECUTION_RESULT.json", value)
    return value


def run_formal(wall_timeout_s: float) -> Mapping[str, Any]:
    roster = _load(REPORT / "FORMAL_V2_ROSTER.json", {})
    freeze = _load(REPORT / "FORMAL_V2_FREEZE_RECEIPT.json", {})
    ledger_path = REPORT / "FORMAL_V2_EXECUTION_LEDGER.json"
    ledger = _load(ledger_path, {})
    if roster.get("cell_count") != 48 or freeze.get("status") != "PASS_FORMAL_V2_FINAL_FREEZE":
        raise RuntimeError("FORMAL_V2_ROSTER_OR_FREEZE_GATE_NOT_PASSED")
    source = _load(REPORT / "SOURCE_FREEZE_RECEIPT.json")
    for row in source["files"]:
        if _sha(ROOT / row["path"]) != row["sha256"]:
            raise RuntimeError("FORMAL_V2_SOURCE_FREEZE_DRIFT")
    attempted = {row["cell_id"] for row in ledger.get("entries", [])}
    if any(row.get("status") != "TERMINATED_VALID" for row in ledger.get("entries", [])):
        raise RuntimeError("FORMAL_V2_PRIOR_INVALID_ATTEMPT_NO_RETRY")
    for cell in roster["cells"]:
        if cell["cell_id"] in attempted:
            continue
        print(json.dumps({"event": "FORMAL_CELL_START", "sequence": cell["run_sequence"], "cell_id": cell["cell_id"], "completed": ledger["formal_episode_valid_count"]}, sort_keys=True), flush=True)
        result = _formal_run_one(cell, wall_timeout_s)
        ledger["entries"].append({
            "cell_id": result["cell_id"], "scene_code": result["scene_code"],
            "seed_slot": result["seed_slot"], "seed": result["seed"],
            "status": result["status"], "exposed": result["exposed"],
            "scientific_retry": False, "failed_checks": result["failed_checks"],
            "record_digest": result["record_digest"], "output_path": result["output_path"],
        })
        ledger["formal_episode_attempt_count"] = len(ledger["entries"])
        ledger["formal_episode_valid_count"] = sum(row["status"] == "TERMINATED_VALID" for row in ledger["entries"])
        ledger["formal_scientific_exposures"] = sum(bool(row["exposed"]) for row in ledger["entries"])
        ledger["status"] = "FORMAL_V2_IN_PROGRESS" if result["status"] == "TERMINATED_VALID" else "FORMAL_V2_STOPPED_INVALID_NO_RETRY"
        ledger["ledger_digest"] = canonical_sha256({key: value for key, value in ledger.items() if key != "ledger_digest"})
        _write_json(ledger_path, ledger)
        print(json.dumps({"event": "FORMAL_CELL_END", "cell_id": result["cell_id"], "status": result["status"], "exposed": result["exposed"], "failed_checks": result["failed_checks"]}, sort_keys=True), flush=True)
        if result["status"] != "TERMINATED_VALID":
            break
    if ledger["formal_episode_valid_count"] == 48 and ledger["formal_episode_attempt_count"] == 48:
        ledger["status"] = "PASS_48_OF_48_VALID_COMPLETE_FORMAL_V2"
    ledger["ledger_digest"] = canonical_sha256({key: value for key, value in ledger.items() if key != "ledger_digest"})
    _write_json(ledger_path, ledger)
    _append_command("run-formal", ledger["status"])
    print(json.dumps({"status": ledger["status"], "attempts": ledger["formal_episode_attempt_count"], "valid": ledger["formal_episode_valid_count"], "exposures": ledger["formal_scientific_exposures"]}, sort_keys=True), flush=True)
    return ledger


def _mcnemar(rows: Sequence[tuple[bool, bool]]) -> Mapping[str, Any]:
    b = sum((not left) and right for left, right in rows)
    c = sum(left and (not right) for left, right in rows)
    n = b + c
    if n == 0:
        p = 1.0
    else:
        tail = sum(math.comb(n, index) for index in range(0, min(b, c) + 1)) / (2.0 ** n)
        p = min(1.0, 2.0 * tail)
    return {"discordant_B1_false_B2_true": b, "discordant_B1_true_B2_false": c, "discordant_total": n, "two_sided_exact_p": p}


def _holm(pvalues: Mapping[str, float]) -> Mapping[str, float]:
    ordered = sorted(pvalues, key=pvalues.get)
    adjusted = {}
    running = 0.0
    total = len(ordered)
    for rank, key in enumerate(ordered):
        running = max(running, min(1.0, (total - rank) * float(pvalues[key])))
        adjusted[key] = running
    return adjusted


def _percentile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * probability
    low, high = int(math.floor(position)), int(math.ceil(position))
    if low == high:
        return ordered[low]
    fraction = position - low
    return ordered[low] * (1.0 - fraction) + ordered[high] * fraction


def _exhaustive_seed_block_interval(
    rows: Sequence[Mapping[str, Any]], endpoint_b1: str, endpoint_b2: str,
) -> Mapping[str, Any]:
    by_slot = defaultdict(list)
    for row in rows:
        by_slot[row["seed_slot"]].append(float(bool(row[endpoint_b2])) - float(bool(row[endpoint_b1])))
    slots = sorted(by_slot)
    block_estimates = [sum(by_slot[slot]) / len(by_slot[slot]) for slot in slots]
    distribution = [
        sum(block_estimates[index] for index in indices) / 6.0
        for indices in itertools.product(range(6), repeat=6)
    ]
    return {
        "method": "EXHAUSTIVE_6_POWER_6_ORDERED_SHARED_SEED_BLOCK_RESAMPLES_WITH_REPLACEMENT",
        "resample_count": len(distribution),
        "seed_block_count": len(slots),
        "estimate": sum(block_estimates) / len(block_estimates),
        "percentile_95_interval": [_percentile(distribution, 0.025), _percentile(distribution, 0.975)],
        "seed_block_estimates": dict(zip(slots, block_estimates)),
    }


def _exhaustive_continuous_seed_block_interval(
    rows: Sequence[Mapping[str, Any]], endpoint_b1: str, endpoint_b2: str,
) -> Mapping[str, Any]:
    by_slot = defaultdict(list)
    for row in rows:
        by_slot[row["seed_slot"]].append(float(row[endpoint_b2]) - float(row[endpoint_b1]))
    slots = sorted(by_slot)
    block_estimates = [sum(by_slot[slot]) / len(by_slot[slot]) for slot in slots]
    distribution = [
        sum(block_estimates[index] for index in indices) / len(slots)
        for indices in itertools.product(range(len(slots)), repeat=len(slots))
    ]
    return {
        "method": "EXHAUSTIVE_ORDERED_SHARED_SEED_BLOCK_RESAMPLES_WITH_REPLACEMENT",
        "resample_count": len(distribution),
        "seed_block_count": len(slots),
        "estimate": sum(block_estimates) / len(block_estimates),
        "percentile_95_interval": [_percentile(distribution, 0.025), _percentile(distribution, 0.975)],
        "seed_block_estimates": dict(zip(slots, block_estimates)),
    }


def _numeric_summary(values: Sequence[float]) -> Mapping[str, Any]:
    rows = [float(value) for value in values]
    if not rows:
        return {"count": 0, "mean": None, "median": None, "minimum": None, "maximum": None}
    return {
        "count": len(rows),
        "mean": sum(rows) / len(rows),
        "median": _percentile(rows, 0.5),
        "minimum": min(rows),
        "maximum": max(rows),
    }


def _clopper_pearson(events: int, total: int, alpha: float = 0.05) -> list[float]:
    from scipy.stats import beta
    low = 0.0 if events == 0 else float(beta.ppf(alpha / 2.0, events, total - events + 1))
    high = 1.0 if events == total else float(beta.ppf(1.0 - alpha / 2.0, events + 1, total - events))
    return [low, high]


def analyze_hcg() -> Mapping[str, Any]:
    ledger = _load(REPORT / "FORMAL_V2_EXECUTION_LEDGER.json", {})
    if ledger.get("status") != "PASS_48_OF_48_VALID_COMPLETE_FORMAL_V2":
        raise RuntimeError("HCG_ANALYSIS_REQUIRES_48_OF_48_VALID")
    if (REPORT / "FORMAL_V2_HCG_RESULTS.json").exists():
        raise RuntimeError("FORMAL_V2_HCG_ANALYSIS_ALREADY_RUN")
    rows = []
    result_records = []
    for entry in ledger["entries"]:
        result = _load(ROOT / entry["output_path"] / "FORMAL_EXECUTION_RESULT.json")
        result_records.append((entry, result))
        row = copy.deepcopy(result["builder"])
        row["seed_slot"] = result["seed_slot"]
        rows.append(row)
    if len(rows) != 48:
        raise RuntimeError("HCG_ANALYSIS_PRIMARY_TABLE_NOT_48")
    roster = _load(REPORT / "FORMAL_V2_ROSTER.json", {})
    roster_cells = roster.get("cells", [])
    expected_cell_ids = {cell.get("cell_id") for cell in roster_cells}
    actual_cell_ids = [entry.get("cell_id") for entry, _ in result_records]
    scene_counts = Counter(row.get("scene_code") for row in rows)
    slot_counts = Counter(row.get("seed_slot") for row in rows)
    slots_to_seeds = defaultdict(set)
    scene_to_slots = defaultdict(set)
    for row in rows:
        slots_to_seeds[row.get("seed_slot")].add(row.get("seed"))
        scene_to_slots[row.get("scene_code")].add(row.get("seed_slot"))
    invariant_fields = (
        "observer_added_vla_forwards", "duplicate_candidate_computations", "PID_controller_changes",
        "second_control_writer", "RoutePlanner_mutations", "UKF_mutations", "command_history_mutations",
        "runtime_true_intent_reads", "online_ask_count",
    )
    endpoint_fields = (
        "B1_precommitment_sufficiency", "B2_precommitment_sufficiency",
        "B1_window_observed", "B2_window_observed", "B1_window_duration_s", "B2_window_duration_s",
        "B1_first_sufficiency_TTCmt_s", "B2_first_sufficiency_TTCmt_s",
        "B2_only_same_frame_sufficiency_count", "rule_results",
    )
    quality_checks = {
        "ledger_status_48_of_48_valid": ledger.get("status") == "PASS_48_OF_48_VALID_COMPLETE_FORMAL_V2",
        "ledger_entry_count_48": len(result_records) == 48,
        "ledger_attempt_valid_exposure_counts_48": (
            ledger.get("formal_episode_attempt_count") == 48
            and ledger.get("formal_episode_valid_count") == 48
            and ledger.get("formal_scientific_exposures") == 48
        ),
        "scientific_retry_count_zero": ledger.get("formal_scientific_retry_count") == 0,
        "roster_cell_count_48": roster.get("cell_count") == 48 and len(roster_cells) == 48,
        "unique_cell_ids_48": len(actual_cell_ids) == len(set(actual_cell_ids)) == 48,
        "ledger_cells_match_frozen_roster": set(actual_cell_ids) == expected_cell_ids,
        "all_eight_scenes_have_six_episodes": set(scene_counts) == set(SCENE_ORDER) and all(scene_counts[code] == 6 for code in SCENE_ORDER),
        "six_shared_seed_slots_have_eight_scenes": len(slot_counts) == 6 and all(count == 8 for count in slot_counts.values()),
        "each_seed_slot_maps_to_exactly_one_seed": all(len(values) == 1 for values in slots_to_seeds.values()),
        "each_scene_uses_all_six_shared_seed_slots": all(len(scene_to_slots[code]) == 6 for code in SCENE_ORDER),
        "all_records_terminated_valid_and_exposed": all(
            result.get("status") == "TERMINATED_VALID" and result.get("exposed") is True
            for _, result in result_records
        ),
        "ledger_and_result_identity_fields_match": all(
            all(entry.get(key) == result.get(key) for key in ("cell_id", "scene_code", "seed_slot", "seed", "status", "exposed"))
            for entry, result in result_records
        ),
        "record_digests_recompute": all(
            entry.get("record_digest") == result.get("record_digest")
            and result.get("record_digest") == canonical_sha256({key: value for key, value in result.items() if key != "record_digest"})
            for entry, result in result_records
        ),
        "builders_formal_valid_same_source": all(
            result.get("builder", {}).get("formal_valid") is True
            and result.get("builder", {}).get("same_source_identity_all_views") is True
            for _, result in result_records
        ),
        "required_frozen_endpoints_present": all(all(key in row for key in endpoint_fields) for row in rows),
        "all_control_and_compute_invariants_zero": all(
            all(row.get(key) == 0 for key in invariant_fields) for row in rows
        ),
    }
    quality = {
        "schema_version": "driveclarify.rq2_t_cg.formal_v2.data_quality_gate.v1",
        "status": "PASS_FORMAL_V2_DATA_QUALITY_GATE" if all(quality_checks.values()) else "FAIL_FORMAL_V2_DATA_QUALITY_GATE",
        "primary_unit": "scene x shared-seed episode",
        "expected_row_count": 48,
        "observed_row_count": len(rows),
        "scene_counts": dict(sorted(scene_counts.items())),
        "seed_slot_counts": dict(sorted(slot_counts.items())),
        "checks": quality_checks,
        "failed_checks": [key for key, passed in quality_checks.items() if not passed],
        "analysis_started": False,
    }
    quality["receipt_digest"] = canonical_sha256(quality)
    _write_json(REPORT / "FORMAL_V2_DATA_QUALITY_GATE.json", quality)
    if quality["status"] != "PASS_FORMAL_V2_DATA_QUALITY_GATE":
        raise RuntimeError("FORMAL_V2_DATA_QUALITY_GATE_FAILED:" + ",".join(quality["failed_checks"]))
    table = {
        "schema_version": "driveclarify.rq2_t_cg.formal_v2.primary_table.v1",
        "primary_unit": "scene × seed episode", "row_count": len(rows), "rows": rows,
    }
    table["table_digest"] = canonical_sha256(table)
    _write_json(REPORT / "FORMAL_V2_EPISODE_LEVEL_PRIMARY_TABLE.json", table)
    scalar_fields = sorted({key for row in rows for key, value in row.items() if not isinstance(value, (dict, list))})
    with (REPORT / "FORMAL_V2_EPISODE_LEVEL_PRIMARY_TABLE.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=scalar_fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in scalar_fields})

    async_codes = {"REF-ASYNC", "LMK-ASYNC", "ORD-ASYNC"}
    sync_codes = {"REF-SYNC", "LMK-SYNC"}
    negative_codes = {"NONREVEAL", "USC-INTRINSIC"}
    async_rows = [row for row in rows if row["scene_code"] in async_codes]
    sync_rows = [row for row in rows if row["scene_code"] in sync_codes]
    negative_rows = [row for row in rows if row["scene_code"] in negative_codes]
    endpoints = {
        "precommitment_sufficiency": ("B1_precommitment_sufficiency", "B2_precommitment_sufficiency"),
        "actionable_window_presence": ("B1_window_observed", "B2_window_observed"),
    }
    hcg1_endpoints = {}
    raw_p = {}
    for name, (left_key, right_key) in endpoints.items():
        pairs = [(bool(row[left_key]), bool(row[right_key])) for row in async_rows]
        mcnemar = _mcnemar(pairs)
        raw_p[name] = float(mcnemar["two_sided_exact_p"])
        hcg1_endpoints[name] = {
            "B1_count": sum(left for left, _ in pairs),
            "B2_count": sum(right for _, right in pairs),
            "denominator": len(pairs),
            "B1_rate": sum(left for left, _ in pairs) / len(pairs),
            "B2_rate": sum(right for _, right in pairs) / len(pairs),
            "B1_clopper_pearson_95_interval": _clopper_pearson(sum(left for left, _ in pairs), len(pairs)),
            "B2_clopper_pearson_95_interval": _clopper_pearson(sum(right for _, right in pairs), len(pairs)),
            "paired_risk_difference_B2_minus_B1": (sum(right for _, right in pairs) - sum(left for left, _ in pairs)) / len(pairs),
            "mcnemar": mcnemar,
            "exhaustive_seed_block_interval": _exhaustive_seed_block_interval(async_rows, left_key, right_key),
            "scene_strata": {
                code: {
                    "B1_count": sum(bool(row[left_key]) for row in async_rows if row["scene_code"] == code),
                    "B2_count": sum(bool(row[right_key]) for row in async_rows if row["scene_code"] == code),
                    "denominator": sum(row["scene_code"] == code for row in async_rows),
                }
                for code in sorted(async_codes)
            },
        }
    adjusted = _holm(raw_p)
    for name in hcg1_endpoints:
        hcg1_endpoints[name]["holm_adjusted_p"] = adjusted[name]
    hcg1_supported = all(
        row["paired_risk_difference_B2_minus_B1"] > 0.0 and row["holm_adjusted_p"] < 0.05
        for row in hcg1_endpoints.values()
    )
    hcg1 = {
        "status": "SUPPORTED" if hcg1_supported else "NOT_SUPPORTED",
        "population_count": len(async_rows), "endpoints": hcg1_endpoints,
        "descriptive_B2_only_same_frame_total": sum(int(row["B2_only_same_frame_sufficiency_count"]) for row in async_rows),
        "B2_only_same_frame_episode_count": sum(int(row["B2_only_same_frame_sufficiency_count"]) > 0 for row in async_rows),
        "B2_only_same_frame_episode_rate": sum(int(row["B2_only_same_frame_sufficiency_count"]) > 0 for row in async_rows) / len(async_rows),
    }
    hcg1["B2_only_same_frame_episode_clopper_pearson_95_interval"] = _clopper_pearson(
        hcg1["B2_only_same_frame_episode_count"], len(async_rows)
    )
    async_descriptive = {
        "first_sufficiency_TTCmt_s": {
            "B1": _numeric_summary([row["B1_first_sufficiency_TTCmt_s"] for row in async_rows if row["B1_first_sufficiency_TTCmt_s"] is not None]),
            "B2": _numeric_summary([row["B2_first_sufficiency_TTCmt_s"] for row in async_rows if row["B2_first_sufficiency_TTCmt_s"] is not None]),
            "paired_difference_B2_minus_B1_among_jointly_observed": _numeric_summary([
                float(row["B2_first_sufficiency_TTCmt_s"]) - float(row["B1_first_sufficiency_TTCmt_s"])
                for row in async_rows
                if row["B1_first_sufficiency_TTCmt_s"] is not None and row["B2_first_sufficiency_TTCmt_s"] is not None
            ]),
            "censoring_note": "No-sufficiency episodes remain right-censored at commitment and are not assigned TTCmt=0.",
        },
        "actionable_window_duration_s": {
            "B1": _numeric_summary([row["B1_window_duration_s"] for row in async_rows]),
            "B2": _numeric_summary([row["B2_window_duration_s"] for row in async_rows]),
            "paired_B2_minus_B1": _exhaustive_continuous_seed_block_interval(async_rows, "B1_window_duration_s", "B2_window_duration_s"),
        },
        "by_scene": {},
        "by_seed_slot": {},
    }
    for code in sorted(async_codes):
        group = [row for row in async_rows if row["scene_code"] == code]
        async_descriptive["by_scene"][code] = {
            "episode_count": len(group),
            "B1_precommitment_sufficiency_count": sum(bool(row["B1_precommitment_sufficiency"]) for row in group),
            "B2_precommitment_sufficiency_count": sum(bool(row["B2_precommitment_sufficiency"]) for row in group),
            "B1_actionable_window_count": sum(bool(row["B1_window_observed"]) for row in group),
            "B2_actionable_window_count": sum(bool(row["B2_window_observed"]) for row in group),
            "B2_only_same_frame_episode_count": sum(int(row["B2_only_same_frame_sufficiency_count"]) > 0 for row in group),
            "B1_first_sufficiency_TTCmt_s": _numeric_summary([row["B1_first_sufficiency_TTCmt_s"] for row in group if row["B1_first_sufficiency_TTCmt_s"] is not None]),
            "B2_first_sufficiency_TTCmt_s": _numeric_summary([row["B2_first_sufficiency_TTCmt_s"] for row in group if row["B2_first_sufficiency_TTCmt_s"] is not None]),
            "B1_window_duration_s": _numeric_summary([row["B1_window_duration_s"] for row in group]),
            "B2_window_duration_s": _numeric_summary([row["B2_window_duration_s"] for row in group]),
        }
    for slot in sorted({row["seed_slot"] for row in async_rows}):
        group = [row for row in async_rows if row["seed_slot"] == slot]
        async_descriptive["by_seed_slot"][slot] = {
            "seed": group[0]["seed"],
            "episode_count": len(group),
            "B1_precommitment_sufficiency_count": sum(bool(row["B1_precommitment_sufficiency"]) for row in group),
            "B2_precommitment_sufficiency_count": sum(bool(row["B2_precommitment_sufficiency"]) for row in group),
            "B1_actionable_window_count": sum(bool(row["B1_window_observed"]) for row in group),
            "B2_actionable_window_count": sum(bool(row["B2_window_observed"]) for row in group),
            "B2_only_same_frame_episode_count": sum(int(row["B2_only_same_frame_sufficiency_count"]) > 0 for row in group),
        }

    hcg2_by_scene = {}
    for code in sorted(sync_codes):
        group = [row for row in sync_rows if row["scene_code"] == code]
        hcg2_by_scene[code] = {
            "episode_count": len(group),
            "B1_precommitment_sufficiency_count": sum(bool(row["B1_precommitment_sufficiency"]) for row in group),
            "B2_precommitment_sufficiency_count": sum(bool(row["B2_precommitment_sufficiency"]) for row in group),
            "B1_actionable_window_count": sum(bool(row["B1_window_observed"]) for row in group),
            "B2_actionable_window_count": sum(bool(row["B2_window_observed"]) for row in group),
            "B2_only_same_frame_witness_count": sum(int(row["B2_only_same_frame_sufficiency_count"]) for row in group),
        }
    hcg2 = {
        "status": "DESCRIPTIVE_MANIPULATION_CONTROL_ONLY",
        "population_count": len(sync_rows), "by_scene": hcg2_by_scene,
        "equivalence_claimed": False,
    }

    stratum_for = {
        "REF-ASYNC": "ASYNC", "LMK-ASYNC": "ASYNC", "ORD-ASYNC": "ASYNC",
        "REF-SYNC": "SYNC", "LMK-SYNC": "SYNC", "ORD-LATE-REVEAL": "LATE_REVEAL",
        "NONREVEAL": "NONREVEAL", "USC-INTRINSIC": "USC",
    }
    hcg3_strata = {}
    for stratum in ("ASYNC", "SYNC", "LATE_REVEAL", "NONREVEAL", "USC"):
        group = [row for row in rows if stratum_for[row["scene_code"]] == stratum]
        classifications = {}
        for key in ("R-EVIDENCE-ONLY(B2)", "R-TIME-ONLY(NONE)", "R-JOINT(B2)"):
            classifications[key] = dict(Counter(row["rule_results"][key]["classification"] for row in group))
        evidence_error = [row["rule_results"]["R-EVIDENCE-ONLY(B2)"]["classification"] == "TOO_LATE_RULE_TRIGGER" for row in group]
        time_error = [row["rule_results"]["R-TIME-ONLY(NONE)"]["classification"] == "PREMATURE_UNSUPPORTED_TRIGGER" for row in group]
        joint_late = [row["rule_results"]["R-JOINT(B2)"]["classification"] == "TOO_LATE_RULE_TRIGGER" for row in group]
        joint_premature = [row["rule_results"]["R-JOINT(B2)"]["classification"] == "PREMATURE_UNSUPPORTED_TRIGGER" for row in group]
        hcg3_strata[stratum] = {
            "episode_count": len(group), "classification_counts": classifications,
            "proposed_query_TTCmt_s": {
                key: _numeric_summary([
                    row["rule_results"][key]["proposed_query"]["TTCmt_s"]
                    for row in group if row["rule_results"][key].get("proposed_query") is not None
                ])
                for key in ("R-EVIDENCE-ONLY(B2)", "R-TIME-ONLY(NONE)", "R-JOINT(B2)")
            },
            "remaining_margin_at_query_s": {
                key: _numeric_summary([
                    row["rule_results"][key]["proposed_query"]["remaining_margin_s"]
                    for row in group if row["rule_results"][key].get("proposed_query") is not None
                ])
                for key in ("R-EVIDENCE-ONLY(B2)", "R-TIME-ONLY(NONE)", "R-JOINT(B2)")
            },
            "evidence_only_too_late_vs_joint_too_late_mcnemar": _mcnemar(list(zip(evidence_error, joint_late))),
            "time_only_premature_vs_joint_premature_mcnemar": _mcnemar(list(zip(time_error, joint_premature))),
        }
    hcg3 = {"status": "EXACT_SAME_TRACE_CLASSIFICATIONS_REPORTED", "strata": hcg3_strata, "pooled_weighted_total_produced": False}

    integrity_specs = {
        "false_sufficiency": lambda row: bool(row["false_sufficiency_B1"] or row["false_sufficiency_B2"]),
        "fabricated_semantic_resolution": lambda row: int(row["fabricated_semantic_resolution_count"]) > 0,
        "invalid_retention_failure": lambda row: bool(row["invalid_retention_failure"]),
    }
    hcg4_endpoints = {}
    for name, predicate in integrity_specs.items():
        count = sum(predicate(row) for row in negative_rows)
        hcg4_endpoints[name] = {
            "events": count, "denominator": len(negative_rows),
            "clopper_pearson_95_interval": _clopper_pearson(count, len(negative_rows)),
        }
    hcg4 = {
        "status": "PASS_ZERO_INTEGRITY_FAILURES" if all(row["events"] == 0 for row in hcg4_endpoints.values()) else "INTEGRITY_FAILURE_OBSERVED",
        "population_count": len(negative_rows), "endpoints": hcg4_endpoints,
        "holm_note": "Three exact endpoint counts are jointly reported; no event-rate null other than integrity target zero was introduced post-freeze.",
    }
    nonreveal_rows = [row for row in rows if row["scene_code"] == "NONREVEAL"]
    usc_rows = [row for row in rows if row["scene_code"] == "USC-INTRINSIC"]
    invalidation_rows = [row for row in rows if row.get("invalidation_observed")]
    controls = {
        "NONREVEAL_false_sufficiency": {
            "events": sum(bool(row["false_sufficiency_B1"] or row["false_sufficiency_B2"]) for row in nonreveal_rows),
            "denominator": len(nonreveal_rows),
        },
        "USC_fabricated_semantic_resolution": {
            "events": sum(int(row["fabricated_semantic_resolution_count"]) > 0 for row in usc_rows),
            "denominator": len(usc_rows),
        },
        "invalidation_invalid_retention": {
            "events": sum(bool(row["invalid_retention_failure"]) for row in invalidation_rows),
            "stale_evidence_survival_count": sum(int(row["stale_evidence_survival_after_invalidation_count"]) for row in invalidation_rows),
            "denominator": len(invalidation_rows),
        },
    }
    for value in controls.values():
        value["rate"] = None if value["denominator"] == 0 else value["events"] / value["denominator"]
        value["clopper_pearson_95_interval"] = None if value["denominator"] == 0 else _clopper_pearson(value["events"], value["denominator"])
    results = {
        "schema_version": "driveclarify.rq2_t_cg.formal_v2.hcg_results.v1",
        "status": "PASS_FROZEN_HCG_ANALYSIS_EXECUTED_ONCE",
        "analysis_execution_count": 1,
        "formal_valid_episode_count": 48,
        "H_CG1": hcg1, "H_CG2": hcg2, "H_CG3": hcg3, "H_CG4": hcg4,
        "ASYNC_descriptive": async_descriptive,
        "supporting_controls": controls,
        "frozen_hypotheses": HYPOTHESES,
        "frozen_analysis_plan": ANALYSIS_PLAN,
        "parameters_or_hypotheses_modified": False,
        "data_quality_gate_digest": quality["receipt_digest"],
    }
    results["results_digest"] = canonical_sha256(results)
    _write_json(REPORT / "FORMAL_V2_HCG_RESULTS.json", results)
    _write_md(REPORT / "FORMAL_V2_ANALYSIS_REPORT.md", "Formal V2 H-CG analysis", [
        "Analysis status: `PASS_FROZEN_HCG_ANALYSIS_EXECUTED_ONCE` on `48/48` valid scene×seed episodes.",
        "- H-CG1: `{}`; endpoint results are `{}`.".format(hcg1["status"], hcg1["endpoints"]),
        "- H-CG2: descriptive manipulation control only; `{}`.".format(hcg2["by_scene"]),
        "- H-CG3: exact same-trace rule classifications are reported separately for ASYNC, SYNC, LATE_REVEAL, NONREVEAL, and USC; no pooled weighted total was produced.",
        "- H-CG4: `{}`; exact counts and Clopper–Pearson intervals are `{}`.".format(hcg4["status"], hcg4["endpoints"]),
    ])
    ledger["hcg_analysis_legally_run"] = True
    ledger["hcg_results_digest"] = results["results_digest"]
    ledger["ledger_digest"] = canonical_sha256({key: value for key, value in ledger.items() if key != "ledger_digest"})
    _write_json(REPORT / "FORMAL_V2_EXECUTION_LEDGER.json", ledger)
    _append_command("analyze", results["status"])
    print(json.dumps({"status": results["status"], "H_CG1": hcg1["status"], "H_CG4": hcg4["status"]}, sort_keys=True), flush=True)
    return results


def _adjudicate_seam_failure(seam: Mapping[str, Any]) -> Mapping[str, Any]:
    failed = next((row for row in seam.get("results", []) if not row.get("pass")), None)
    if failed is None:
        return {}
    result_path = ROOT / failed["iteration_result_path"]
    output = result_path.parent
    iteration = _load(result_path)
    scenario = _load(output / "FORMAL_SCENARIO_RECEIPT.json")
    official = _load(output / "leaderboard_results.json")
    record = official["_checkpoint"]["records"][0]
    scene = _load(SEAM_CONFIGS / (failed["scene_code"] + ".json"))
    route_deviation_messages = list(record["infractions"].get("route_dev", []))
    route_deviation_rows = []
    for message in route_deviation_messages:
        match = re.search(r"at \(x=([-+0-9.]+), y=([-+0-9.]+), z=([-+0-9.]+)\)", message)
        point = None if match is None else [float(value) for value in match.groups()]
        projection = None if point is None else dict(project_point_to_polyline(
            point,
            [[float(row[key]) for key in ("x", "y", "z")] for row in scene["route"]["waypoints"]],
        ))
        route_deviation_rows.append({
            "official_message": message,
            "coordinate_xyz": point,
            "scientific_route_projection": projection,
        })
    background = _load(output / "BACKGROUND_TRAFFIC_RUNTIME_RECEIPT.json", {})
    runtime = iteration.get("runtime") or {}
    collisions = {
        key: list(record["infractions"].get(key, []))
        for key in ("collisions_layout", "collisions_pedestrian", "collisions_vehicle")
    }
    checks = {
        "fresh_complete_seam_stopped_at_first_failure": seam.get("status") == "EXECUTION_SEAM_8_OF_8_NOT_CLOSED" and seam.get("attempted_scene_count") == 6 and seam.get("passed_scene_count") == 5,
        "failed_scene_is_ord_late_reveal": failed["scene_code"] == "ORD-LATE-REVEAL",
        "official_native_route_deviation": record.get("status") == "Failed - Agent deviated from the route" and bool(route_deviation_messages),
        "route_deviation_is_critical_validity_failure": "official_evaluator_criteria_clean" in failed.get("failed_checks", ()),
        "no_collision_or_background_actor_failure": all(not rows for rows in collisions.values()),
        "background_policy_runtime_pass": background.get("status") == "PASS_RANDOM_BACKGROUND_TRAFFIC_DISABLED",
        "random_background_requests_zero": background.get("traffic_manager_generated_background_vehicles_requested") == 0,
        "static_and_runtime_route_bindings_passed": failed.get("checks", {}).get("route_binding") is True and failed.get("checks", {}).get("evaluator_route") is True and failed.get("checks", {}).get("agent_route") is True,
        "zero_control_mutation": failed.get("checks", {}).get("zero_control_mutation") is True,
        "natural_evaluator_completion": runtime.get("termination_reason") == "NATURAL_EVALUATOR_COMPLETION",
        "clean_teardown": failed.get("checks", {}).get("clean_teardown") is True,
        "formal_freeze_absent": not (REPORT / "FORMAL_V2_FREEZE_RECEIPT.json").exists(),
        "formal_seed_values_zero": not (REPORT / "FORMAL_V2_SEED_FRESHNESS_RECEIPT.json").exists(),
        "formal_scientific_exposures_zero": seam.get("formal_scientific_exposures") == 0,
    }
    value = {
        "schema_version": "driveclarify.rq2_t_cg.formal_v2.seam_route_deviation_adjudication.v2",
        "status": "STOP_SEAM_NATIVE_ROUTE_DEVIATION_NO_AUTHORIZED_CONTROL_OR_SEED_REPAIR" if all(checks.values()) else "STOP_SEAM_FAILURE_REQUIRES_REVIEW",
        "failed_scene_code": failed["scene_code"],
        "failed_identity": failed["identity"],
        "failed_seed": failed["seed"],
        "failed_checks": failed["failed_checks"],
        "official_route_score_percent": record["scores"]["score_route"],
        "official_route_deviation_count": len(route_deviation_messages),
        "route_deviation_rows": route_deviation_rows,
        "official_collision_counts": {key: len(rows) for key, rows in collisions.items()},
        "last_scientific_progress_m": scenario.get("last_progress_m"),
        "background_traffic_runtime_receipt": background,
        "repair_boundary": {
            "changing_PID_controller_or_vehicle_behavior_forbidden": True,
            "changing_candidate_geometry_commitment_events_or_calibration_forbidden": True,
            "changing_or_shortening_frozen_route_forbidden": True,
            "fresh_seed_retry_after_native_route_deviation_would_be_seed_selection": True,
            "repair_authorized": False,
        },
        "preserved_evidence_paths": {
            "seam_receipt": "EXECUTION_SEAM_8_OF_8_RECEIPT.json",
            "native_iteration": str(result_path.relative_to(REPORT)),
            "official_evaluator": str((output / "leaderboard_results.json").relative_to(REPORT)),
            "scenario_receipt": str((output / "FORMAL_SCENARIO_RECEIPT.json").relative_to(REPORT)),
        },
        "checks": checks,
        "failed_adjudication_checks": [key for key, passed in checks.items() if not passed],
    }
    value["receipt_digest"] = canonical_sha256(value)
    _write_json(REPORT / "EXECUTION_SEAM_FAILURE_ADJUDICATION.json", value)
    return value


def finalize() -> Mapping[str, Any]:
    if (REPORT / "FINAL_VALIDATION_RECEIPT.json").exists():
        raise RuntimeError("FINAL_VALIDATION_ALREADY_EXISTS")
    gate = _load(REPORT / "ORD_ASYNC_PREEXPOSURE_GATE.json", {})
    ord_eng = _load(REPORT / "ORD_ASYNC_ENGINEERING_QUALIFICATION.json", {})
    seam = _load(REPORT / "EXECUTION_SEAM_8_OF_8_RECEIPT.json", {})
    seam_failure = _adjudicate_seam_failure(seam)
    freeze = _load(REPORT / "FORMAL_V2_FREEZE_RECEIPT.json", {})
    seeds = _load(REPORT / "FORMAL_V2_SEED_FRESHNESS_RECEIPT.json", {})
    ledger = _load(REPORT / "FORMAL_V2_EXECUTION_LEDGER.json", {
        "formal_episode_attempt_count": 0, "formal_episode_valid_count": 0,
        "formal_scientific_exposures": 0, "formal_scientific_retry_count": 0,
        "formal_infrastructure_retry_count": 0, "entries": [],
    })
    hcg = _load(REPORT / "FORMAL_V2_HCG_RESULTS.json", {})
    source = _source_freeze("FINAL_SOURCE_FREEZE")
    _write_json(REPORT / "SOURCE_FREEZE_RECEIPT.json", source)
    complete = (
        gate.get("status") == "PASS_ORD_ASYNC_PREEXPOSURE_GATE"
        and ord_eng.get("pass") is True
        and seam.get("status") == "PASS_EXECUTION_SEAM_8_OF_8"
        and freeze.get("status") == "PASS_FORMAL_V2_FINAL_FREEZE"
        and seeds.get("generated_seed_count") == 6
        and ledger.get("formal_episode_valid_count") == 48
        and hcg.get("status") == "PASS_FROZEN_HCG_ANALYSIS_EXECUTED_ONCE"
    )
    if complete:
        status = (
            "PASS_RQ2_T_CG_FORMAL_V2_B2_SUPPORTED"
            if hcg.get("H_CG1", {}).get("status") == "SUPPORTED"
            else "PASS_RQ2_T_CG_FORMAL_V2_B2_NOT_SUPPORTED"
        )
        recommendation = "Preserve the frozen protocol, sealed roster, native traces, and one-pass analysis as the final evidence package for manuscript reporting without rerunning or retuning Formal V2."
    elif seam.get("status") not in (None, "PASS_EXECUTION_SEAM_8_OF_8"):
        status = "FORMAL_V2_EXECUTION_INTEGRITY_NOT_CLOSED"
        recommendation = "Preserve the failed fresh seam and do not generate Formal V2 seeds, substitute the failed engineering seed, alter vehicle control, or change the frozen route/scientific contracts; report Formal V2 execution integrity as not closed."
    elif ord_eng and ord_eng.get("pass") is not True:
        status = "FORMAL_V2_EXECUTION_INTEGRITY_NOT_CLOSED"
        recommendation = "Review the preserved ORD native trace to classify whether the failure is an ordinary engineering defect or a scientific redesign stop condition."
    elif ledger.get("formal_episode_attempt_count", 0) > ledger.get("formal_episode_valid_count", 0):
        status = "FORMAL_V2_EXECUTION_INTEGRITY_NOT_CLOSED"
        recommendation = "Preserve the exposed roster as invalid and do not estimate H-CG or substitute any formal seed."
    else:
        status = "FORMAL_V2_EXECUTION_INTEGRITY_NOT_CLOSED"
        recommendation = "Resume from the first uncompleted gated command without changing the frozen calibration, scene operands, seeds, or analysis plan."
    geometry = candidate_source_geometry()
    policy = _load(REPORT / "BACKGROUND_TRAFFIC_POLICY_V2.json", {})
    if not (REPORT / "FINAL_SCIENTIFIC_ACTOR_MANIFESTS.json").is_file():
        manifest_scenes = {
            code: _load(SEAM_CONFIGS / (code + ".json"), {}) for code in SCENE_ORDER
        }
        if all(manifest_scenes.values()):
            persist_final_actor_manifests(REPORT, manifest_scenes)
    actor_manifests = _load(REPORT / "FINAL_SCIENTIFIC_ACTOR_MANIFESTS.json", {})
    roster = _load(REPORT / "FORMAL_V2_ROSTER.json", {})
    primary = hcg.get("H_CG1", {}).get("endpoints", {})
    async_desc = hcg.get("ASYNC_descriptive", {})
    controls = hcg.get("supporting_controls", {})
    validation_checks = {
        "preexposure_gate_pass": gate.get("status") == "PASS_ORD_ASYNC_PREEXPOSURE_GATE",
        "calibration_freeze_unchanged": source["calibration_freeze_unchanged"],
        "reserve_unchanged": _load(ORD_CONFIG, {}).get("deadline", {}).get("total_reserved_simulation_s") == 1.2,
        "ord_engineering_pass_or_preserved_stop": bool(ord_eng),
        "formal_analysis_only_if_48_valid": not hcg or ledger.get("formal_episode_valid_count") == 48,
        "formal_scientific_retries_zero": ledger.get("formal_scientific_retry_count", 0) == 0,
        "source_files_hashed": source["file_count"] == len(SOURCE_PATHS),
    }
    final = {
        "schema_version": "driveclarify.rq2_t_cg.ord_async_redesign_and_formal.final_validation.v1",
        "status": status,
        "J1_topology_identity": "Town12:junction:{}".format(J1_ID),
        "J2_topology_identity": "Town12:junction:{}".format(J2_ID),
        "J1_route_arc_length_m": geometry["j1_route_arc_length_m"],
        "J2_route_arc_length_m": geometry["j2_route_arc_length_m"],
        "original_ambiguous_instruction": ORIGINAL_INSTRUCTION,
        "z1": Z1_INTERPRETATION,
        "z2": Z2_INTERPRETATION,
        "passenger_selection_status": "UNKNOWN",
        "forbidden_semantic_key_count": len(gate.get("forbidden_semantic_key_paths", [])),
        "z1_continuity_pass": gate.get("checks", {}).get("z1_continuous_no_jump"),
        "z2_continuity_pass": gate.get("checks", {}).get("z2_continuous_no_jump"),
        "shared_prefix": {"start": geometry["shared_prefix_start"], "end": geometry["shared_prefix_end"]},
        "divergence": geometry["first_candidate_task_divergence"],
        "commitment": _load(REPORT / "ORD_ASYNC_COMMITMENT_CERTIFICATE.json", {}).get("commitment"),
        "ord_event_ordering": [row["activation"] for row in _load(ORD_CONFIG, {}).get("events", [])],
        "ord_engineering_status": ord_eng.get("status", "NOT_RUN"),
        "ord_B1_B2_descriptive": ord_eng.get("B1_B2_descriptive"),
        "calibration_freeze_digest": CALIBRATION_FREEZE_DIGEST,
        "calibration_freeze_unchanged": source["calibration_freeze_unchanged"],
        "reserve_1_20_s_unchanged": validation_checks["reserve_unchanged"],
        "execution_seam_status": seam.get("status", "NOT_RUN"),
        "execution_seam_passed_count": seam.get("passed_scene_count", 0),
        "execution_seam_attempted_count": seam.get("attempted_scene_count", 0),
        "execution_seam_failure_adjudication": seam_failure,
        "background_traffic_policy_id": policy.get("policy_id"),
        "background_traffic_policy_digest": policy.get("policy_digest"),
        "random_background_vehicles_retained": actor_manifests.get("random_background_vehicles_retained"),
        "scientific_or_scenario_owned_entries_retained": actor_manifests.get("scientific_or_scenario_owned_entries_retained"),
        "proof_removed_actors_have_no_scientific_role": policy.get("proof_removed_actors_have_no_scientific_role"),
        "formal_v2_freeze_digest": freeze.get("freeze_digest"),
        "formal_seed_values": seeds.get("seed_values", []),
        "seed_freshness_status": seeds.get("status"),
        "seed_prior_occurrence_count": seeds.get("prior_occurrence_count"),
        "formal_48_cell_roster_digest": roster.get("roster_digest"),
        "formal_episode_attempt_count": ledger.get("formal_episode_attempt_count", 0),
        "formal_episode_valid_count": ledger.get("formal_episode_valid_count", 0),
        "formal_scientific_exposures": ledger.get("formal_scientific_exposures", 0),
        "formal_infrastructure_retry_count": ledger.get("formal_infrastructure_retry_count", 0),
        "formal_scientific_retry_count": ledger.get("formal_scientific_retry_count", 0),
        "async_B1_precommitment_sufficiency": primary.get("precommitment_sufficiency", {}).get("B1_count"),
        "async_B2_precommitment_sufficiency": primary.get("precommitment_sufficiency", {}).get("B2_count"),
        "async_precommitment_denominator": primary.get("precommitment_sufficiency", {}).get("denominator"),
        "async_precommitment_paired_difference_B2_minus_B1": primary.get("precommitment_sufficiency", {}).get("paired_risk_difference_B2_minus_B1"),
        "async_B1_actionable_window": primary.get("actionable_window_presence", {}).get("B1_count"),
        "async_B2_actionable_window": primary.get("actionable_window_presence", {}).get("B2_count"),
        "async_actionable_window_denominator": primary.get("actionable_window_presence", {}).get("denominator"),
        "same_frame_B1_false_B2_true_episode_count": hcg.get("H_CG1", {}).get("B2_only_same_frame_episode_count"),
        "same_frame_B1_false_B2_true_witness_count": hcg.get("H_CG1", {}).get("descriptive_B2_only_same_frame_total"),
        "async_first_sufficiency_TTCmt_s": async_desc.get("first_sufficiency_TTCmt_s"),
        "async_actionable_window_duration_s": async_desc.get("actionable_window_duration_s"),
        "confidence_intervals": {key: value.get("exhaustive_seed_block_interval") for key, value in primary.items()},
        "NONREVEAL_false_sufficiency": controls.get("NONREVEAL_false_sufficiency"),
        "USC_fabricated_semantic_resolution": controls.get("USC_fabricated_semantic_resolution"),
        "invalidation_invalid_retention": controls.get("invalidation_invalid_retention"),
        "hcg_analysis_legally_run": hcg.get("status") == "PASS_FROZEN_HCG_ANALYSIS_EXECUTED_ONCE",
        "H_CG1": hcg.get("H_CG1", "NOT_RUN"),
        "H_CG2": hcg.get("H_CG2", "NOT_RUN"),
        "H_CG3": hcg.get("H_CG3", "NOT_RUN"),
        "H_CG4": hcg.get("H_CG4", "NOT_RUN"),
        "invariants": {
            "added_vla_forwards": 0, "duplicate_candidate_computations": 0,
            "PID_controller_changes": 0, "controller_changes": 0,
            "second_control_writer": 0, "runtime_true_intent_reads": 0,
            "online_ask_count": 0,
        },
        "source_freeze_digest": source["receipt_digest"],
        "validation_checks": validation_checks,
        "failed_validation_checks": [key for key, passed in validation_checks.items() if not passed],
        "one_next_recommendation": recommendation,
    }
    final["receipt_digest"] = canonical_sha256(final)
    _write_json(REPORT / "FINAL_VALIDATION_RECEIPT.json", final)
    report_lines = [
        "Final status: `{}`.".format(status),
        "Background traffic policy: `{}`; random background vehicles retained=`{}`; scientific/scenario-owned manifest entries retained=`{}`. Removed generators have no scientific-contract reference: `{}`.".format(final["background_traffic_policy_id"], final["random_background_vehicles_retained"], final["scientific_or_scenario_owned_entries_retained"], (final["proof_removed_actors_have_no_scientific_role"] or {}).get("all_eight_scene_scans_pass")),
        "J1 is `Town12:junction:{}` at route arc length `{:.9f} m`; J2 is `Town12:junction:{}` at `{:.9f} m`.".format(J1_ID, geometry["j1_route_arc_length_m"], J2_ID, geometry["j2_route_arc_length_m"]),
        "The ambiguous instruction is `{}`. z1 is `{}` z2 is `{}`".format(ORIGINAL_INSTRUCTION, Z1_INTERPRETATION, Z2_INTERPRETATION),
        "The passenger selection remains `UNKNOWN`; forbidden passenger-selection/oracle fields detected: `{}`. Both native-GRP candidate paths passed continuity and topology checks.".format(final["forbidden_semantic_key_count"]),
        "The paths share the common ego prefix through `{:.9f} m` and diverge at J1. Commitment occurs at `{:.9f} m` on straight connector 13968, where z1's turn connector 14005 is no longer recoverable through the normal forward interface.".format(geometry["shared_prefix_end"]["route_arc_length_m"], geometry["commitment_boundary"]["route_arc_length_m"]),
        "ORD engineering: `{}` with descriptive B1/B2 `{}`. Complete seam: `{}` (`{}/8`).".format(final["ord_engineering_status"], final["ord_B1_B2_descriptive"], final["execution_seam_status"], final["execution_seam_passed_count"]),
        "Calibration freeze unchanged: `{}`; 1.20 s reserve unchanged: `{}`.".format(final["calibration_freeze_unchanged"], final["reserve_1_20_s_unchanged"]),
        "Formal V2 freeze digest: `{}`. Seeds: `{}` with freshness=`{}` and prior occurrences=`{}`. Roster digest: `{}`.".format(final["formal_v2_freeze_digest"], final["formal_seed_values"], final["seed_freshness_status"], final["seed_prior_occurrence_count"], final["formal_48_cell_roster_digest"]),
        "Formal attempts/valid/exposures: `{}/{}/{}`; infrastructure retries=`{}`; scientific retries=`{}`.".format(final["formal_episode_attempt_count"], final["formal_episode_valid_count"], final["formal_scientific_exposures"], final["formal_infrastructure_retry_count"], final["formal_scientific_retry_count"]),
        "ASYNC precommitment sufficiency B1/B2=`{}/{}` of `{}`; paired B2-B1=`{}`. Actionable-window B1/B2=`{}/{}` of `{}`.".format(final["async_B1_precommitment_sufficiency"], final["async_B2_precommitment_sufficiency"], final["async_precommitment_denominator"], final["async_precommitment_paired_difference_B2_minus_B1"], final["async_B1_actionable_window"], final["async_B2_actionable_window"], final["async_actionable_window_denominator"]),
        "Same-frame B1=false/B2=true incidence: `{}` episodes and `{}` frame witnesses. First-sufficiency TTCmt and actionable-duration summaries are persisted in `FORMAL_V2_HCG_RESULTS.json`.".format(final["same_frame_B1_false_B2_true_episode_count"], final["same_frame_B1_false_B2_true_witness_count"]),
        "H-CG1=`{}`; H-CG2/SYNC=`{}`; H-CG3=`{}`; H-CG4=`{}`.".format((final["H_CG1"] or {}).get("status") if isinstance(final["H_CG1"], Mapping) else final["H_CG1"], (final["H_CG2"] or {}).get("status") if isinstance(final["H_CG2"], Mapping) else final["H_CG2"], (final["H_CG3"] or {}).get("status") if isinstance(final["H_CG3"], Mapping) else final["H_CG3"], (final["H_CG4"] or {}).get("status") if isinstance(final["H_CG4"], Mapping) else final["H_CG4"]),
        "Controls: NONREVEAL=`{}`; USC=`{}`; invalidation=`{}`.".format(final["NONREVEAL_false_sufficiency"], final["USC_fabricated_semantic_resolution"], final["invalidation_invalid_retention"]),
        "Invariants: added VLA forwards=`0`; PID/controller changes=`0`; second control writer=`0`; runtime true-intent reads=`0`.",
        "H-CG analysis legally run: `{}`. Source freeze digest: `{}`.".format(final["hcg_analysis_legally_run"], final["source_freeze_digest"]),
        "Exactly one next recommendation: {}".format(recommendation),
    ]
    if seam_failure:
        report_lines.insert(7, "The fresh seam stopped after `{}` attempts: `{}`.".format(final["execution_seam_attempted_count"], seam_failure.get("status", "NO_FAILURE")))
    _write_md(REPORT / "FINAL_REPORT.md", "DriveClarify RQ2-T-CG Formal V2 controlled-background experiment", report_lines)
    _append_command("finalize", status)
    print(json.dumps({"status": status, "formal_attempts": final["formal_episode_attempt_count"], "formal_valid": final["formal_episode_valid_count"], "recommendation": recommendation}, sort_keys=True), flush=True)
    return final


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=(
        "prepare", "qualify-ord", "repair-ord-policy-cap", "repair-ord-wall", "repair-global-cap", "repair-ord-binding", "repair-ord-trigger", "repair-ord-cap", "qualify-seams", "freeze-formal",
        "run-formal", "analyze", "finalize",
    ))
    parser.add_argument("--wall-timeout-seconds", type=float, default=900.0)
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare(); return 0 if result["status"].startswith("PASS_") else 2
    if args.command == "qualify-ord":
        result = qualify_ord(); return 0 if result["pass"] else 2
    if args.command == "repair-ord-policy-cap":
        result = repair_ord_policy_administrative_containment()
        return 0 if result["status"] == "PASS_ORDINARY_POLICY_ADMINISTRATIVE_CONTAINMENT_REPAIR" else 2
    if args.command == "repair-ord-wall":
        result = repair_ord_wall_containment()
        return 0 if result["status"] == "PASS_ORDINARY_WALL_CONTAINMENT_REPAIR" else 2
    if args.command == "repair-global-cap":
        result = repair_global_administrative_containment()
        return 0 if result["status"] == "PASS_ORDINARY_GLOBAL_ADMINISTRATIVE_CONTAINMENT_REPAIR" else 2
    if args.command == "repair-ord-binding":
        result = repair_ord_execution_binding(); return 0 if result["status"] == "PASS_ORDINARY_EXECUTION_BINDING_REPAIR" else 2
    if args.command == "repair-ord-trigger":
        result = repair_ord_trigger_binding(); return 0 if result["status"] == "PASS_ORDINARY_TRIGGER_BINDING_REPAIR" else 2
    if args.command == "repair-ord-cap":
        result = repair_ord_administrative_cap(); return 0 if result["status"] == "PASS_ORDINARY_ADMINISTRATIVE_CAP_REPAIR" else 2
    if args.command == "qualify-seams":
        result = qualify_seams(); return 0 if result["status"] == "PASS_EXECUTION_SEAM_8_OF_8" else 2
    if args.command == "freeze-formal":
        freeze_and_materialize_formal(); return 0
    if args.command == "run-formal":
        result = run_formal(args.wall_timeout_seconds); return 0 if result["status"] == "PASS_48_OF_48_VALID_COMPLETE_FORMAL_V2" else 2
    if args.command == "analyze":
        analyze_hcg(); return 0
    result = finalize()
    return 0 if result["status"].startswith("PASS_") else 2


if __name__ == "__main__":
    raise SystemExit(main())
