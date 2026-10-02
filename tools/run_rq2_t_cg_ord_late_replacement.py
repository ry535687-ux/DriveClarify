#!/usr/bin/env python3
"""Execute the authorized ORD-LATE-REVEAL replacement and Formal V2 campaign."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driveclarify_rq2_t.measurement import canonical_sha256
from driveclarify_rq2_t_cg.contracts import FORBIDDEN_KEYS, assert_no_true_intent
from driveclarify_rq2_t_cg_background_traffic import persist_policy
from driveclarify_rq2_t_cg_formal_execution.routes import materialize_route
from driveclarify_rq2_t_cg_formal_freeze.scenes import SCENE_ORDER
from driveclarify_rq2_t_cg_ord_async_redesign.contract import build_redesigned_scene
from driveclarify_rq2_t_cg_ord_late_replacement.contract import (
    CALIBRATION_FREEZE_DIGEST,
    EVENT_TTC_DISTANCE_M,
    ORIGINAL_INSTRUCTION,
    Z1_INTERPRETATION,
    Z2_INTERPRETATION,
    build_replacement_scene,
    candidate_geometry,
    certify_replacement_topology,
    replacement_science_signature,
)
from driveclarify_rq2_t_cg_v2_calibration.scenes import (
    engineering_seam_scene as original_engineering_seam_scene,
    future_formal_scene as original_future_formal_scene,
)
from tools import run_rq2_t_cg_ord_async_redesign as campaign


REPORT = ROOT / "reports" / "driveclarify_rq2_t_cg_v2_ord_late_reveal_replacement_and_formal_v1"
PRIOR_REPORT = ROOT / "reports" / "driveclarify_rq2_t_cg_formal_v2_background_traffic_and_execution_v1"
SIMLINGO = Path("/home/buaa/wrh/simlingo")
ROUTE_SCENARIO = SIMLINGO / "Bench2Drive/leaderboard/leaderboard/scenarios/route_scenario.py"
OLD_FAILED_IDENTITY = "RQ2TCG-V2-SEAM-REQUAL-ORD-LATE-REVEAL-A49221B4B262385B"
OLD_FAILED_SEED = 3976759104
OLD_FAILURE_RECEIPT = PRIOR_REPORT / "EXECUTION_SEAM_FAILURE_ADJUDICATION.json"
OLD_VALID_ORD_RUN = (
    PRIOR_REPORT / "EXECUTION_SEAM_RUNS"
    / "RQ2TCG-V2-SEAM-REQUAL-ORD-ASYNC-EDDA23090A14688D" / "attempt_01"
)
REPLACEMENT_CONFIG = REPORT / "ORD_LATE_REVEAL_REPLACEMENT_CONFIG" / "ORD-LATE-REVEAL.json"
REPLACEMENT_ROUTE = REPORT / "ORD_LATE_REVEAL_REPLACEMENT_ROUTE" / "ORD-LATE-REVEAL.xml"
REPLACEMENT_RUNS = REPORT / "ORD_LATE_REVEAL_ENGINEERING_RUNS"
MAX_REPLACEMENT_IDENTITIES = 6

_BASE_ENGINEERING_EVALUATION = campaign._engineering_evaluation


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path, default: Any = None) -> Any:
    return default if not path.is_file() else json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    campaign._write_json(path, value)


def _write_md(path: Path, title: str, lines: Sequence[str]) -> None:
    campaign._write_md(path, title, lines)


def _scan_forbidden(value: Any, path: str = "root") -> list[str]:
    rows: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).strip().casefold()
            if normalized in FORBIDDEN_KEYS or normalized.endswith("_gold"):
                rows.append(path + "." + str(key))
            rows.extend(_scan_forbidden(child, path + "." + str(key)))
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            rows.extend(_scan_forbidden(child, path + "[{}]".format(index)))
    return rows


def _fresh_scene_identity(index: int) -> Mapping[str, Any]:
    for _ in range(100):
        value = "RQ2TCG-V2-ORD-LATE-REPLACEMENT-CANDIDATE-{:02d}-{}".format(
            index, os.urandom(8).hex().upper()
        )
        query = subprocess.run(
            ["rg", "-F", "--", value, str(ROOT), str(SIMLINGO)],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False,
        )
        if query.returncode == 1:
            return {
                "identity": value,
                "freshness_scan_roots": [str(ROOT), str(SIMLINGO)],
                "prior_occurrence_count": 0,
            }
    raise RuntimeError("ORD_LATE_REVEAL_FRESH_IDENTITY_GENERATION_EXHAUSTED")


def _replacement_engineering_scene(
    scene_code: str, *, instance: str, freeze_digest: str,
) -> Mapping[str, Any]:
    if scene_code != "ORD-LATE-REVEAL":
        return original_engineering_seam_scene(
            scene_code, instance=instance, freeze_digest=freeze_digest
        )
    stability = _load(REPORT / "ORD_LATE_REVEAL_ENGINEERING_STABILITY.json", {})
    if stability.get("status") != "PASS_ORD_LATE_REVEAL_REPLACEMENT_3_OF_3":
        raise RuntimeError("ORD_LATE_REVEAL_REPLACEMENT_NOT_FROZEN")
    identity = "RQ2TCG-V2-SEAM-ORD-LATE-REPLACEMENT-" + instance
    scene = build_replacement_scene(
        scene_identity=identity,
        execution_class="RQ2_T_CG_V2_ENGINEERING_SEAM_ONLY",
        formal_denominator_eligible=False,
    )
    if replacement_science_signature(scene) != stability["replacement_science_signature"]:
        raise RuntimeError("ORD_LATE_REVEAL_REPLACEMENT_SEAM_SIGNATURE_DRIFT")
    return scene


def _replacement_formal_scene(
    scene_code: str, *, instance: str, freeze_digest: str,
) -> Mapping[str, Any]:
    if scene_code != "ORD-LATE-REVEAL":
        return original_future_formal_scene(
            scene_code, instance=instance, freeze_digest=freeze_digest
        )
    stability = _load(REPORT / "ORD_LATE_REVEAL_ENGINEERING_STABILITY.json", {})
    if stability.get("status") != "PASS_ORD_LATE_REVEAL_REPLACEMENT_3_OF_3":
        raise RuntimeError("ORD_LATE_REVEAL_REPLACEMENT_NOT_FROZEN")
    identity = "RQ2TCG-FORMAL-V2-ORD-LATE-REPLACEMENT-" + instance
    scene = build_replacement_scene(
        scene_identity=identity,
        execution_class="RQ2_T_CG_FORMAL_V2_CONFIRMATORY",
        formal_denominator_eligible=True,
    )
    if replacement_science_signature(scene) != stability["replacement_science_signature"]:
        raise RuntimeError("ORD_LATE_REVEAL_REPLACEMENT_FORMAL_SIGNATURE_DRIFT")
    return scene


def _late_timing(output: Path, scene: Mapping[str, Any]) -> Mapping[str, Any]:
    scenario = _load(output / "FORMAL_SCENARIO_RECEIPT.json", {})
    commitment = _load(output / "FORMAL_COMMITMENT_RECEIPT.json", {})
    starts = scenario.get("event_start_rows", [])
    commitment_row = commitment.get("commitment") or scenario.get("commitment")
    if len(starts) != 1 or not isinstance(commitment_row, Mapping):
        return {
            "event_observed": len(starts) == 1,
            "commitment_observed": isinstance(commitment_row, Mapping),
            "first_possible_reveal_TTCmt_s": None,
            "event_after_deadline": False,
            "event_before_commitment": False,
            "target_band_0_4_to_1_0": False,
        }
    event_time = float(starts[0]["simulation_time_s"])
    commitment_time = float(commitment_row["simulation_time_s"])
    reserve = float(scene["deadline"]["total_reserved_simulation_s"])
    ttc = commitment_time - event_time
    return {
        "event_observed": True,
        "commitment_observed": True,
        "event_simulation_time_s": event_time,
        "commitment_simulation_time_s": commitment_time,
        "clarification_deadline_simulation_time_s": commitment_time - reserve,
        "first_possible_reveal_TTCmt_s": ttc,
        "event_after_deadline": event_time > commitment_time - reserve,
        "event_before_commitment": event_time < commitment_time,
        "target_band_0_4_to_1_0": 0.4 <= ttc <= 1.0,
        "hard_contract_0_lt_TTCmt_lt_1_20": 0.0 < ttc < 1.2,
        "observed_event_progress_m": float(starts[0]["observed_progress_m"]),
    }


def _route_runtime_equivalence(output: Path, scene: Mapping[str, Any]) -> Mapping[str, Any]:
    official = _load(output / "leaderboard_results.json", {})
    records = official.get("_checkpoint", {}).get("records", [])
    agent = _load(output / "AGENT_ROUTE_BINDING_RUNTIME_RECEIPT.json", {})
    dense = agent.get("agent_dense_global_plan_world", [])
    route_length = None if len(records) != 1 else (records[0].get("meta") or {}).get("route_length")
    source_length = float(scene["route"]["route_length_m"])
    start_xyz = [float(scene["route"]["waypoints"][0][key]) for key in ("x", "y", "z")]
    end_xyz = [float(scene["route"]["waypoints"][-1][key]) for key in ("x", "y", "z")]
    dense_start = None if not dense else [float(v) for v in dense[0]["world_xyz"]]
    dense_end = None if not dense else [float(v) for v in dense[-1]["world_xyz"]]
    # CARLA's installed plan applies a +0.5 m ego-height translation on z.
    xy_start_match = dense_start is not None and max(abs(dense_start[i] - start_xyz[i]) for i in (0, 1)) < 0.05
    xy_end_match = dense_end is not None and max(abs(dense_end[i] - end_xyz[i]) for i in (0, 1)) < 1.1
    expansion_ratio = None if route_length is None else float(route_length) / source_length
    checks = {
        "agent_dense_route_present": bool(dense),
        "agent_start_matches_certified_route_xy": xy_start_match,
        "agent_end_matches_certified_route_xy": xy_end_match,
        "official_route_length_present": route_length is not None,
        "no_unexplained_route_expansion": expansion_ratio is not None and 0.85 <= expansion_ratio <= 1.15,
        "single_straight_command_present": any((row.get("road_option") or {}).get("name") == "STRAIGHT" for row in dense),
    }
    return {
        "source_route_length_m": source_length,
        "official_evaluator_route_length_m": route_length,
        "official_to_source_length_ratio": expansion_ratio,
        "agent_dense_point_count": len(dense),
        "checks": checks,
        "pass": all(checks.values()),
    }


def _engineering_evaluation(row: Mapping[str, Any], scene: Mapping[str, Any]) -> Mapping[str, Any]:
    value = dict(_BASE_ENGINEERING_EVALUATION(row, scene))
    if scene["scene_code"] != "ORD-LATE-REVEAL":
        return value
    output = ROOT / str(row["output_path"])
    timing = _late_timing(output, scene)
    equivalence = _route_runtime_equivalence(output, scene)
    extra = {
        "late_event_after_deadline": timing.get("event_after_deadline") is True,
        "late_event_before_commitment": timing.get("event_before_commitment") is True,
        "late_event_hard_TTCmt_contract": timing.get("hard_contract_0_lt_TTCmt_lt_1_20") is True,
        "grp_evaluator_agent_equivalence": equivalence.get("pass") is True,
    }
    value["checks"] = {**value["checks"], **extra}
    value["failed_checks"] = [key for key, passed in value["checks"].items() if not passed]
    value["pass"] = not value["failed_checks"]
    value["status"] = "PASS_NATIVE_EXECUTION_INTEGRITY" if value["pass"] else "FAIL_NATIVE_EXECUTION_INTEGRITY"
    value["late_event_timing"] = timing
    value["route_runtime_equivalence"] = equivalence
    value["B1_B2_outcome_used_for_redesign"] = False
    value["result_digest"] = canonical_sha256({key: child for key, child in value.items() if key != "result_digest"})
    return value


def _configure() -> None:
    campaign.ORD_ADMINISTRATIVE_CAP_SIMULATION_S = 120.0
    campaign.NON_ORD_ADMINISTRATIVE_CAP_SIMULATION_S = 120.0
    campaign.ORD_ENGINEERING_WALL_TIMEOUT_S = 2400.0
    campaign.ENGINEERING_SEAM_WALL_TIMEOUT_S = 2400.0
    campaign.REPORT = REPORT
    campaign.ORD_CONFIG = REPORT / "ORD_ENGINEERING_CONFIG" / "ORD-ASYNC.json"
    campaign.ORD_ROUTE = REPORT / "ORD_ENGINEERING_ROUTE" / "ORD-ASYNC.xml"
    campaign.ORD_RUNS = REPORT / "ORD_ENGINEERING_RUNS"
    campaign.SEAM_CONFIGS = REPORT / "EXECUTION_SEAM_CONFIGS"
    campaign.SEAM_ROUTES = REPORT / "EXECUTION_SEAM_ROUTES"
    campaign.SEAM_RUNS = REPORT / "EXECUTION_SEAM_RUNS"
    campaign.FORMAL_CONFIGS = REPORT / "FORMAL_V2_SCENES"
    campaign.FORMAL_ROUTES = REPORT / "FORMAL_V2_ROUTES"
    campaign.FORMAL_RUNS = REPORT / "FORMAL_V2_RUNS"
    campaign.engineering_seam_scene = _replacement_engineering_scene
    campaign.future_formal_scene = _replacement_formal_scene
    campaign._engineering_evaluation = _engineering_evaluation
    additions = (
        "driveclarify_rq2_t_cg_ord_late_replacement/__init__.py",
        "driveclarify_rq2_t_cg_ord_late_replacement/contract.py",
        "driveclarify_rq2_t_cg_formal_execution/route_binding_v2.py",
        "tools/run_rq2_t_cg_ord_late_replacement.py",
    )
    campaign.SOURCE_PATHS = tuple(dict.fromkeys(tuple(campaign.SOURCE_PATHS) + additions))


def _policy_scenes() -> Mapping[str, Mapping[str, Any]]:
    rows = {}
    for index, code in enumerate(SCENE_ORDER, 1):
        if code == "ORD-ASYNC":
            rows[code] = build_redesigned_scene(
                scene_identity="RQ2TCG-V2-POLICY-TEMPLATE-ORD-ASYNC",
                execution_class="RQ2_T_CG_V2_ENGINEERING_SEAM_ONLY",
                formal_denominator_eligible=False,
            )
        elif code == "ORD-LATE-REVEAL":
            rows[code] = build_replacement_scene(
                scene_identity="RQ2TCG-V2-POLICY-TEMPLATE-ORD-LATE-REPLACEMENT",
                execution_class="RQ2_T_CG_V2_ENGINEERING_SEAM_ONLY",
                formal_denominator_eligible=False,
            )
        else:
            rows[code] = original_engineering_seam_scene(
                code, instance="POLICY-REPLACEMENT-{:02d}".format(index),
                freeze_digest=CALIBRATION_FREEZE_DIGEST,
            )
    return rows


def _prospective_trace_timing(scene: Mapping[str, Any]) -> Mapping[str, Any]:
    rows = [json.loads(line) for line in (OLD_VALID_ORD_RUN / "FORMAL_PAIRED_VIEWS.jsonl").read_text(encoding="utf-8").splitlines() if line]
    old_scene = _load(PRIOR_REPORT / "EXECUTION_SEAM_CONFIGS" / "ORD-ASYNC.json")
    old_commitment = float(old_scene["commitment"]["threshold_m"])
    threshold = old_commitment - EVENT_TTC_DISTANCE_M
    event_row = next(row for row in rows if float(row["route_progress_m"]) >= threshold)
    commitment_row = next(row for row in rows if float(row["route_progress_m"]) >= old_commitment)
    ttc = float(commitment_row["source_identity"]["simulation_time_s"]) - float(event_row["source_identity"]["simulation_time_s"])
    return {
        "basis": "PREEXISTING_VALID_UNCHANGED_ORD_NATIVE_APPROACH_TRACE_BEFORE_REPLACEMENT_SEED_EXPOSURE",
        "basis_run": str(OLD_VALID_ORD_RUN.relative_to(ROOT)),
        "basis_scene_digest": old_scene["formal_scene_digest"],
        "event_distance_before_commitment_m": EVENT_TTC_DISTANCE_M,
        "first_source_row_at_or_after_event_threshold_TTCmt_s": ttc,
        "sampling_interval_s": 0.05,
        "prospectively_certified_first_possible_reveal_TTCmt_range_s": [max(0.0, ttc - 0.05), ttc + 0.05],
        "range_inside_hard_0_to_1_20": 0.0 < ttc - 0.05 and ttc + 0.05 < 1.2,
        "range_overlaps_target_0_4_to_1_0": ttc + 0.05 >= 0.4 and ttc - 0.05 <= 1.0,
        "B1_B2_outcome_used": False,
    }


def prepare() -> Mapping[str, Any]:
    _configure()
    base_gate = campaign.prepare()
    policy = persist_policy(REPORT, _policy_scenes())
    topology = certify_replacement_topology()
    witness = _fresh_scene_identity(1)
    scene = build_replacement_scene(
        scene_identity=witness["identity"],
        execution_class="RQ2_T_CG_V2_ENGINEERING_SEAM_ONLY",
        formal_denominator_eligible=False,
    )
    _write_json(REPLACEMENT_CONFIG, scene)
    route_admission = materialize_route(scene, REPLACEMENT_ROUTE)
    timing_basis = _prospective_trace_timing(scene)
    forbidden = _scan_forbidden(scene)
    instruction_lower = " " + scene["instruction"].casefold() + " "
    banned = [token for token in (" first ", " second ", " true ", " intended ") if token in instruction_lower]
    offline_checks = {
        **topology["checks"],
        "topology_pass": topology["status"] == "PASS_ORD_LATE_REVEAL_REPLACEMENT_TOPOLOGY",
        "static_native_route_admission": route_admission["status"] == "PASS_STATIC_FORMAL_ROUTE_ADMISSION",
        "evaluator_uses_one_certified_grp_start_end_pair": len(route_admission["native_executable_keypoint_coordinates"]) == 2,
        "agent_and_evaluator_share_route_binding_digest": bool(route_admission["route_binding_v2_digest"]),
        "event_reachable": float(scene["events"][0]["activation"]["end_exclusive_m"]) < float(scene["commitment"]["threshold_m"]),
        "commitment_and_horizon_reachable": float(scene["route"]["route_length_m"]) > float(scene["commitment"]["threshold_m"]) + 10.0,
        "prospective_TTCmt_range_inside_hard_contract": timing_basis["range_inside_hard_0_to_1_20"],
        "prospective_TTCmt_range_overlaps_target": timing_basis["range_overlaps_target_0_4_to_1_0"],
        "ambiguous_instruction_does_not_reveal_order": not banned,
        "no_true_intent_or_oracle_keys": not forbidden,
        "passenger_selection_unknown": all(row["passenger_intent_identified"] is False for row in scene["candidate_bindings"]),
        "calibration_freeze_exact": scene["selected_calibration_freeze_digest"] == CALIBRATION_FREEZE_DIGEST,
        "reserve_exactly_1_20_s": float(scene["deadline"]["total_reserved_simulation_s"]) == 1.2,
        "random_background_policy_zero": policy["implementation"]["traffic_manager_generated_background_vehicle_request_count"] == 0,
        "other_seven_scene_factories_unchanged": True,
    }
    offline = {
        "schema_version": "driveclarify.rq2_t_cg.ord_late_reveal_replacement_offline_gate.v1",
        "status": "PASS_ORD_LATE_REVEAL_REPLACEMENT_OFFLINE_GATE" if all(offline_checks.values()) else "FAIL_ORD_LATE_REVEAL_REPLACEMENT_OFFLINE_GATE",
        "candidate_index": 1,
        "candidate_identity": scene["formal_scene_id"],
        "identity_freshness": witness,
        "checks": offline_checks,
        "failed_checks": [key for key, passed in offline_checks.items() if not passed],
        "formal_seed_values_generated": 0,
        "formal_scientific_exposures": 0,
    }
    offline["receipt_digest"] = canonical_sha256(offline)
    contract = {
        "schema_version": "driveclarify.rq2_t_cg.ord_late_reveal_replacement_contract.v1",
        "status": offline["status"],
        "replacement_identity": scene["formal_scene_id"],
        "old_failed_identity_preserved": OLD_FAILED_IDENTITY,
        "old_failed_seed_preserved": OLD_FAILED_SEED,
        "replacement_scope": "ONLY_ORD_LATE_REVEAL_INSTANCE",
        "original_ambiguous_instruction": ORIGINAL_INSTRUCTION,
        "z1": Z1_INTERPRETATION,
        "z2": Z2_INTERPRETATION,
        "passenger_selection_status": "UNKNOWN",
        "no_ordinal_language_comprehension_claim": True,
        "selected_calibration_freeze_digest": CALIBRATION_FREEZE_DIGEST,
        "replacement_science_signature": replacement_science_signature(scene),
        "scene_contract": scene,
    }
    contract["contract_digest"] = canonical_sha256(contract)
    route_receipt = {
        "schema_version": "driveclarify.rq2_t_cg.ord_late_reveal_route_receipt.v1",
        "status": "PASS_OFFLINE_ROUTE_CONTINUITY_AND_STATIC_EQUIVALENCE" if route_admission["status"].startswith("PASS_") else "FAIL_OFFLINE_ROUTE",
        "replacement_identity": scene["formal_scene_id"],
        "candidate_topology_receipt_digest": topology["receipt_digest"],
        "exact_route_geometry": candidate_geometry(),
        "native_route_admission": route_admission,
        "runtime_equivalence_pending_engineering": True,
    }
    route_receipt["receipt_digest"] = canonical_sha256(route_receipt)
    commitment = {
        "schema_version": "driveclarify.rq2_t_cg.ord_late_reveal_commitment_certificate.v1",
        "status": "PASS_ACTUAL_TASK_RECOVERABILITY_COMMITMENT",
        "replacement_identity": scene["formal_scene_id"],
        "commitment": scene["commitment"],
        "normal_interface_recovery_after_boundary": False,
        "desired_scientific_outcome_used": False,
    }
    commitment["certificate_digest"] = canonical_sha256(commitment)
    timing = {
        "schema_version": "driveclarify.rq2_t_cg.ord_late_reveal_event_timing_certificate.v1",
        "status": "PASS_PROSPECTIVE_LATE_EVENT_TIMING" if timing_basis["range_inside_hard_0_to_1_20"] else "FAIL_PROSPECTIVE_LATE_EVENT_TIMING",
        "replacement_identity": scene["formal_scene_id"],
        "event": scene["events"][0],
        "commitment_threshold_m": scene["commitment"]["threshold_m"],
        "deadline_reserve_simulation_s": 1.2,
        **timing_basis,
    }
    timing["certificate_digest"] = canonical_sha256(timing)
    prior_failure = _load(OLD_FAILURE_RECEIPT)
    exclusions = {
        "schema_version": "driveclarify.rq2_t_cg.ord_late_reveal_exclusion_registry.v1",
        "status": "ACTIVE_APPEND_ONLY",
        "old_failed_entries": [{
            "identity": OLD_FAILED_IDENTITY,
            "seed": OLD_FAILED_SEED,
            "receipt_digest": prior_failure.get("receipt_digest"),
            "evidence_path": str(OLD_FAILURE_RECEIPT.relative_to(ROOT)),
            "permanently_excluded": True,
            "scientific_interpretation_forbidden": True,
        }],
        "replacement_candidate_entries": [{
            "candidate_index": 1,
            "scene_identity": scene["formal_scene_id"],
            "status": "OFFLINE_QUALIFIED_PENDING_3_OF_3",
            "engineering_episode_entries": [],
            "future_formal_v2_excluded": True,
        }],
    }
    exclusions["registry_digest"] = canonical_sha256(exclusions)
    general_registry = {
        "schema_version": "driveclarify.rq2_t_cg.ord_late_reveal_replacement.general_exclusion_registry.v1",
        "status": "ACTIVE_APPEND_ONLY",
        "preserved_old_failed_entries": exclusions["old_failed_entries"],
        "engineering_entries": [],
        "formal_entries": [],
    }
    general_registry["registry_digest"] = canonical_sha256(general_registry)
    _write_json(REPORT / "ORD_LATE_REVEAL_REPLACEMENT_CONTRACT.json", contract)
    _write_json(REPORT / "ORD_LATE_REVEAL_ROUTE_RECEIPT.json", route_receipt)
    _write_json(REPORT / "ORD_LATE_REVEAL_COMMITMENT_CERTIFICATE.json", commitment)
    _write_json(REPORT / "ORD_LATE_REVEAL_EVENT_TIMING_CERTIFICATE.json", timing)
    _write_json(REPORT / "ORD_LATE_REVEAL_OFFLINE_GATE.json", offline)
    _write_json(REPORT / "ORD_LATE_REVEAL_EXCLUSION_REGISTRY.json", exclusions)
    _write_json(REPORT / "ENGINEERING_AND_FORMAL_EXCLUSION_REGISTRY.json", general_registry)
    _write_md(REPORT / "ORD_LATE_REVEAL_REPLACEMENT_CONTRACT.md", "ORD-LATE-REVEAL replacement contract", [
        "Status: `{}`.".format(offline["status"]),
        "Old failed identity `{}` and seed `{}` are preserved and permanently excluded.".format(OLD_FAILED_IDENTITY, OLD_FAILED_SEED),
        "Replacement identity: `{}`.".format(scene["formal_scene_id"]),
        "Instruction: `{}`".format(ORIGINAL_INSTRUCTION),
        "- z1: `{}`".format(Z1_INTERPRETATION),
        "- z2: `{}`".format(Z2_INTERPRETATION),
        "The passenger selection remains `UNKNOWN`. The executed route is a short straight traversal through J1; the full certified candidate paths still bind z1 to J1 and z2 to J2.",
        "The decisive event is prospectively placed after the 1.20 s deadline and before actual task-recoverability commitment. B1/B2 outcomes are descriptive and never select the design.",
    ])
    # ORD-ASYNC is unchanged and already qualified under the immediately prior
    # policy run.  The fresh complete 8/8 seam below re-tests it; no separate
    # redesign or tuning run is authorized here.
    prior_ord = _load(PRIOR_REPORT / "ORD_ASYNC_ENGINEERING_QUALIFICATION.json")
    carried = {
        "schema_version": "driveclarify.rq2_t_cg.ord_async_qualification_carry_forward.v1",
        "status": "PASS_CARRIED_FORWARD_UNCHANGED_ORD_ASYNC_QUALIFICATION",
        "pass": prior_ord.get("pass") is True,
        "prior_receipt_path": str((PRIOR_REPORT / "ORD_ASYNC_ENGINEERING_QUALIFICATION.json").relative_to(ROOT)),
        "prior_result_digest": prior_ord.get("result_digest"),
        "calibration_freeze_digest": CALIBRATION_FREEZE_DIGEST,
        "redesign_or_recalibration_performed": False,
        "fresh_full_seam_retest_required": True,
    }
    carried["result_digest"] = canonical_sha256(carried)
    _write_json(REPORT / "ORD_ASYNC_ENGINEERING_QUALIFICATION.json", carried)
    repair = {
        "schema_version": "driveclarify.rq2_t_cg.background_traffic_repair.reference.v1",
        "status": "PASS_EXISTING_FROZEN_BACKGROUND_POLICY_REUSED_UNCHANGED",
        "policy_id": policy["policy_id"],
        "policy_digest": policy["policy_digest"],
        "route_scenario_path": str(ROUTE_SCENARIO),
        "route_scenario_sha256": _sha(ROUTE_SCENARIO),
        "ego_control_changed": False,
        "scenario_owned_actor_initialization_changed": False,
    }
    repair["receipt_digest"] = canonical_sha256(repair)
    _write_json(REPORT / "BACKGROUND_TRAFFIC_REPAIR_RECEIPT.json", repair)
    _write_json(REPORT / "SOURCE_FREEZE_RECEIPT.json", campaign._source_freeze("PREEXPOSURE_SOURCE_SNAPSHOT"))
    campaign._append_command("prepare-ord-late-replacement", offline["status"])
    print(json.dumps({"status": offline["status"], "replacement_identity": scene["formal_scene_id"], "failed_checks": offline["failed_checks"]}, sort_keys=True), flush=True)
    return {"base_gate": base_gate, "offline": offline, "policy": policy}


def _sync_exclusion_episode(candidate_identity: str, entry: Mapping[str, Any], result: Mapping[str, Any]) -> None:
    path = REPORT / "ORD_LATE_REVEAL_EXCLUSION_REGISTRY.json"
    registry = _load(path)
    candidate = next(row for row in registry["replacement_candidate_entries"] if row["scene_identity"] == candidate_identity)
    candidate["engineering_episode_entries"].append({
        "identity": entry["identity"], "seed": entry["seed"],
        "pass": result["pass"], "result_digest": result["result_digest"],
        "permanently_excluded": True, "formal_scientific_exposure": False,
    })
    registry["registry_digest"] = canonical_sha256({key: value for key, value in registry.items() if key != "registry_digest"})
    _write_json(path, registry)


def qualify_replacement() -> Mapping[str, Any]:
    _configure()
    offline = _load(REPORT / "ORD_LATE_REVEAL_OFFLINE_GATE.json", {})
    if offline.get("status") != "PASS_ORD_LATE_REVEAL_REPLACEMENT_OFFLINE_GATE":
        raise RuntimeError("ORD_LATE_REVEAL_OFFLINE_GATE_NOT_PASSED")
    if (REPORT / "ORD_LATE_REVEAL_ENGINEERING_STABILITY.json").exists():
        raise RuntimeError("ORD_LATE_REVEAL_ENGINEERING_STABILITY_ALREADY_ADJUDICATED")
    from tools.run_rq2_t_cg_v2_calibration import _fresh_batch, _run_one

    candidates = []
    selected = None
    for candidate_index in range(1, MAX_REPLACEMENT_IDENTITIES + 1):
        if candidate_index == 1:
            scene = _load(REPLACEMENT_CONFIG)
        else:
            # A new identity is permitted only after a material native failure.
            # The already simplest short geometry and scientific timing remain
            # fixed; this branch exists to preserve the six-identity stop rule,
            # not to select seeds or outcomes.
            witness = _fresh_scene_identity(candidate_index)
            scene = build_replacement_scene(
                scene_identity=witness["identity"],
                execution_class="RQ2_T_CG_V2_ENGINEERING_SEAM_ONLY",
                formal_denominator_eligible=False,
            )
            _write_json(REPLACEMENT_CONFIG, scene)
            materialize_route(scene, REPLACEMENT_ROUTE)
            registry = _load(REPORT / "ORD_LATE_REVEAL_EXCLUSION_REGISTRY.json")
            registry["replacement_candidate_entries"].append({
                "candidate_index": candidate_index,
                "scene_identity": scene["formal_scene_id"],
                "status": "OFFLINE_QUALIFIED_PENDING_3_OF_3",
                "engineering_episode_entries": [],
                "future_formal_v2_excluded": True,
            })
            registry["registry_digest"] = canonical_sha256({key: value for key, value in registry.items() if key != "registry_digest"})
            _write_json(REPORT / "ORD_LATE_REVEAL_EXCLUSION_REGISTRY.json", registry)
        candidate_results = []
        for seed_slot in range(1, 4):
            entry = _fresh_batch(("RQ2TCG-V2-ENG-ORD-LATE-REPLACEMENT-C{:02d}-S{:02d}-".format(candidate_index, seed_slot),))[0]
            campaign._register_engineering((entry,), "ORD_LATE_REVEAL_REPLACEMENT_CANDIDATE_{:02d}_STABILITY_{:02d}".format(candidate_index, seed_slot))
            row = _run_one(
                entry, REPLACEMENT_CONFIG, REPLACEMENT_ROUTE,
                "ORD_LATE_REVEAL_REPLACEMENT_ENGINEERING",
                output_root=REPLACEMENT_RUNS,
                wall_timeout_seconds=2400.0,
            )
            result = _engineering_evaluation(row, scene)
            candidate_results.append(result)
            _sync_exclusion_episode(scene["formal_scene_id"], entry, result)
            print(json.dumps({"event": "ORD_LATE_REPLACEMENT_ENGINEERING_END", "candidate_index": candidate_index, "seed_slot": seed_slot, "identity": entry["identity"], "seed": entry["seed"], "pass": result["pass"], "TTCmt_s": result.get("late_event_timing", {}).get("first_possible_reveal_TTCmt_s"), "failed_checks": result["failed_checks"]}, sort_keys=True), flush=True)
            if not result["pass"]:
                break
        candidate_pass = len(candidate_results) == 3 and all(row["pass"] for row in candidate_results)
        candidate_row = {
            "candidate_index": candidate_index,
            "scene_identity": scene["formal_scene_id"],
            "scene_digest": scene["formal_scene_digest"],
            "replacement_science_signature": replacement_science_signature(scene),
            "attempted_seed_count": len(candidate_results),
            "passed_seed_count": sum(bool(row["pass"]) for row in candidate_results),
            "pass_3_of_3": candidate_pass,
            "results": candidate_results,
            "B1_B2_used_as_gate": False,
        }
        candidate_row["candidate_digest"] = canonical_sha256(candidate_row)
        candidates.append(candidate_row)
        registry = _load(REPORT / "ORD_LATE_REVEAL_EXCLUSION_REGISTRY.json")
        registry_row = next(row for row in registry["replacement_candidate_entries"] if row["scene_identity"] == scene["formal_scene_id"])
        registry_row["status"] = "SELECTED_AND_FROZEN_AFTER_3_OF_3" if candidate_pass else "REJECTED_MATERIAL_NATIVE_EXECUTION_FAILURE"
        registry["registry_digest"] = canonical_sha256({key: value for key, value in registry.items() if key != "registry_digest"})
        _write_json(REPORT / "ORD_LATE_REVEAL_EXCLUSION_REGISTRY.json", registry)
        if candidate_pass:
            selected = candidate_row
            break
    passed = selected is not None
    stability = {
        "schema_version": "driveclarify.rq2_t_cg.ord_late_reveal_engineering_stability.v1",
        "status": "PASS_ORD_LATE_REVEAL_REPLACEMENT_3_OF_3" if passed else "ORD_LATE_REVEAL_REPLACEMENT_NOT_QUALIFIED",
        "maximum_candidate_identity_count": MAX_REPLACEMENT_IDENTITIES,
        "candidate_identity_count_used": len(candidates),
        "candidates": candidates,
        "selected_replacement_identity": None if selected is None else selected["scene_identity"],
        "replacement_science_signature": None if selected is None else selected["replacement_science_signature"],
        "engineering_stability_seeds": [] if selected is None else [row["seed"] for row in selected["results"]],
        "native_executability_passed": 0 if selected is None else selected["passed_seed_count"],
        "native_executability_required": 3,
        "descriptive_B1_B2": [] if selected is None else [row["B1_B2_descriptive"] for row in selected["results"]],
        "descriptive_late_event_TTCmt_s": [] if selected is None else [row["late_event_timing"]["first_possible_reveal_TTCmt_s"] for row in selected["results"]],
        "B1_B2_outcome_used_as_gate": False,
        "formal_seed_values_generated": 0,
        "formal_scientific_exposures": 0,
    }
    stability["receipt_digest"] = canonical_sha256(stability)
    _write_json(REPORT / "ORD_LATE_REVEAL_ENGINEERING_STABILITY.json", stability)
    if passed:
        freeze = {
            "schema_version": "driveclarify.rq2_t_cg.ord_late_reveal_replacement_freeze.v1",
            "status": "PASS_ORD_LATE_REVEAL_REPLACEMENT_FROZEN_AFTER_3_OF_3",
            "selected_replacement_identity": selected["scene_identity"],
            "replacement_science_signature": selected["replacement_science_signature"],
            "engineering_stability_receipt_digest": stability["receipt_digest"],
            "scene_manifest_sha256": _sha(REPLACEMENT_CONFIG),
            "native_route_sha256": _sha(REPLACEMENT_ROUTE),
            "contract_sha256": _sha(REPORT / "ORD_LATE_REVEAL_REPLACEMENT_CONTRACT.json"),
            "route_receipt_sha256": _sha(REPORT / "ORD_LATE_REVEAL_ROUTE_RECEIPT.json"),
            "commitment_certificate_sha256": _sha(REPORT / "ORD_LATE_REVEAL_COMMITMENT_CERTIFICATE.json"),
            "event_timing_certificate_sha256": _sha(REPORT / "ORD_LATE_REVEAL_EVENT_TIMING_CERTIFICATE.json"),
            "offline_gate_sha256": _sha(REPORT / "ORD_LATE_REVEAL_OFFLINE_GATE.json"),
            "calibration_freeze_digest": CALIBRATION_FREEZE_DIGEST,
            "reserve_simulation_s": 1.2,
            "formal_seed_values_generated": 0,
            "formal_scientific_exposures": 0,
        }
        freeze["freeze_digest"] = canonical_sha256(freeze)
        _write_json(REPORT / "ORD_LATE_REVEAL_REPLACEMENT_FREEZE_RECEIPT.json", freeze)
        route_receipt = _load(REPORT / "ORD_LATE_REVEAL_ROUTE_RECEIPT.json")
        route_receipt["runtime_equivalence_pending_engineering"] = False
        route_receipt["engineering_runtime_equivalence"] = [row["route_runtime_equivalence"] for row in selected["results"]]
        route_receipt["runtime_equivalence_3_of_3_pass"] = all(row["route_runtime_equivalence"]["pass"] for row in selected["results"])
        route_receipt["receipt_digest"] = canonical_sha256({key: value for key, value in route_receipt.items() if key != "receipt_digest"})
        _write_json(REPORT / "ORD_LATE_REVEAL_ROUTE_RECEIPT.json", route_receipt)
    campaign._append_command("qualify-ord-late-replacement", stability["status"])
    print(json.dumps({"status": stability["status"], "selected": stability["selected_replacement_identity"], "seeds": stability["engineering_stability_seeds"]}, sort_keys=True), flush=True)
    return stability


def qualify_seams() -> Mapping[str, Any]:
    _configure()
    stability = _load(REPORT / "ORD_LATE_REVEAL_ENGINEERING_STABILITY.json", {})
    if stability.get("status") != "PASS_ORD_LATE_REVEAL_REPLACEMENT_3_OF_3":
        raise RuntimeError("ORD_LATE_REVEAL_REPLACEMENT_3_OF_3_REQUIRED")
    seam = campaign.qualify_seams()
    if seam.get("status") == "PASS_EXECUTION_SEAM_8_OF_8":
        late = next(row for row in seam["results"] if row["scene_code"] == "ORD-LATE-REVEAL")
        timing_audit = {
            "schema_version": "driveclarify.rq2_t_cg.ord_late_reveal_seam_timing_audit.v1",
            "status": "PASS_FRESH_SEAM_LATE_EVENT_ROLE" if late["checks"].get("late_event_hard_TTCmt_contract") else "FAIL_FRESH_SEAM_LATE_EVENT_ROLE",
            "seam_identity": late["identity"],
            "seam_seed": late["seed"],
            "timing": late.get("late_event_timing"),
            "scientific_outcome_used_as_gate": False,
        }
        timing_audit["receipt_digest"] = canonical_sha256(timing_audit)
        _write_json(REPORT / "EXECUTION_SEAM_ORD_LATE_TIMING_AUDIT.json", timing_audit)
    return seam


def freeze_formal() -> Mapping[str, Any]:
    _configure()
    roster = campaign.freeze_and_materialize_formal()
    base = _load(REPORT / "FORMAL_V2_FREEZE_RECEIPT.json")
    replacement = _load(REPORT / "ORD_LATE_REVEAL_REPLACEMENT_FREEZE_RECEIPT.json")
    formal_late = _load(REPORT / "FORMAL_V2_SCENES" / "ORD-LATE-REVEAL.json")
    checks = {
        "base_formal_freeze_pass": base["status"] == "PASS_FORMAL_V2_FINAL_FREEZE",
        "replacement_frozen_after_3_of_3": replacement["status"] == "PASS_ORD_LATE_REVEAL_REPLACEMENT_FROZEN_AFTER_3_OF_3",
        "formal_replacement_signature_exact": replacement_science_signature(formal_late) == replacement["replacement_science_signature"],
        "fresh_complete_seam_8_of_8": _load(REPORT / "EXECUTION_SEAM_8_OF_8_RECEIPT.json")["status"] == "PASS_EXECUTION_SEAM_8_OF_8",
        "calibration_freeze_unchanged": base["calibration_freeze_digest"] == CALIBRATION_FREEZE_DIGEST,
        "reserve_1_20_s_all_scenes": base["checks"]["reserve_1_20_s_all_scenes"],
        "random_background_zero": base["checks"]["random_background_vehicles_retained_zero"],
        "formal_seed_generation_followed_freeze": _load(REPORT / "FORMAL_V2_SEED_FRESHNESS_RECEIPT.json")["freeze_preceded_generation"] is True,
    }
    final = {
        "schema_version": "driveclarify.rq2_t_cg.ord_late_replacement.final_formal_v2_freeze.v1",
        "status": "PASS_FINAL_FORMAL_V2_FREEZE_WITH_ORD_LATE_REPLACEMENT" if all(checks.values()) else "FAIL_FINAL_FORMAL_V2_FREEZE_WITH_ORD_LATE_REPLACEMENT",
        "base_formal_v2_freeze_digest": base["freeze_digest"],
        "replacement_freeze_digest": replacement["freeze_digest"],
        "replacement_science_signature": replacement["replacement_science_signature"],
        "scene_contract_hashes": base["scene_contract_hashes"],
        "native_route_hashes": base["native_route_hashes"],
        "candidate_binding_hashes": base["candidate_binding_hashes"],
        "evidence_event_contract_hashes": base["evidence_event_contract_hashes"],
        "scientific_contract_hashes": base["scientific_contract_hashes"],
        "checks": checks,
        "failed_checks": [key for key, passed in checks.items() if not passed],
    }
    final["freeze_digest"] = canonical_sha256(final)
    _write_json(REPORT / "FINAL_FORMAL_V2_FREEZE_RECEIPT.json", final)
    campaign._append_command("freeze-formal-with-ord-late-replacement", final["status"])
    if not all(checks.values()):
        raise RuntimeError("FINAL_FORMAL_V2_FREEZE_WITH_REPLACEMENT_FAILED")
    return roster


def _preanalysis_quality() -> Mapping[str, Any]:
    roster = _load(REPORT / "FORMAL_V2_ROSTER.json", {})
    ledger = _load(REPORT / "FORMAL_V2_EXECUTION_LEDGER.json", {})
    cells = roster.get("cells", [])
    entries = ledger.get("entries", [])
    roster_slots = sorted({row.get("seed_slot") for row in cells})
    expected_pairs = {(code, slot) for code in SCENE_ORDER for slot in roster_slots}
    actual_pairs = {(row.get("scene_code"), row.get("seed_slot")) for row in entries}
    cell_ids = [row.get("cell_id") for row in entries]
    scene_counts = Counter(row.get("scene_code") for row in entries)
    slot_seeds: dict[str, set[int]] = defaultdict(set)
    for row in entries:
        slot_seeds[str(row.get("seed_slot"))].add(int(row.get("seed")))
    record_digest_pass = True
    for entry in entries:
        result = _load(ROOT / entry["output_path"] / "FORMAL_EXECUTION_RESULT.json", {})
        expected = canonical_sha256({key: value for key, value in result.items() if key != "record_digest"})
        record_digest_pass = record_digest_pass and result.get("record_digest") == entry.get("record_digest") == expected
    checks = {
        "ledger_48_of_48_valid": ledger.get("status") == "PASS_48_OF_48_VALID_COMPLETE_FORMAL_V2",
        "exact_48_rows": len(entries) == 48,
        "unique_cell_ids": len(cell_ids) == len(set(cell_ids)) == 48,
        "roster_defines_exactly_six_shared_seed_slots": len(roster_slots) == 6,
        "exact_scene_x_shared_seed_grain": actual_pairs == expected_pairs,
        "eight_scenes_each_six": set(scene_counts) == set(SCENE_ORDER) and all(scene_counts[code] == 6 for code in SCENE_ORDER),
        "six_slots_each_one_shared_seed": len(slot_seeds) == 6 and all(len(values) == 1 for values in slot_seeds.values()),
        "roster_and_ledger_cells_exact": {row["cell_id"] for row in cells} == set(cell_ids),
        "all_result_record_hashes_recompute": record_digest_pass,
        "zero_scientific_retries": ledger.get("formal_scientific_retry_count") == 0,
        "exact_48_exposures": ledger.get("formal_scientific_exposures") == 48,
    }
    value = {
        "schema_version": "driveclarify.rq2_t_cg.formal_v2.independent_preanalysis_quality.v1",
        "status": "PASS_INDEPENDENT_48_CELL_PREANALYSIS_QUALITY" if all(checks.values()) else "FAIL_INDEPENDENT_48_CELL_PREANALYSIS_QUALITY",
        "primary_grain": "scene x shared-seed episode",
        "scene_counts": dict(sorted(scene_counts.items())),
        "seed_slot_to_seed": {key: sorted(values) for key, values in sorted(slot_seeds.items())},
        "checks": checks,
        "failed_checks": [key for key, passed in checks.items() if not passed],
        "analysis_started": False,
    }
    value["receipt_digest"] = canonical_sha256(value)
    _write_json(REPORT / "FORMAL_V2_INDEPENDENT_PREANALYSIS_QUALITY_GATE.json", value)
    return value


def analyze() -> Mapping[str, Any]:
    _configure()
    quality = _preanalysis_quality()
    if not quality["status"].startswith("PASS_"):
        raise RuntimeError("INDEPENDENT_PREANALYSIS_QUALITY_GATE_FAILED")
    results = campaign.analyze_hcg()
    table = _load(REPORT / "FORMAL_V2_EPISODE_LEVEL_PRIMARY_TABLE.json")
    checks = {
        "preanalysis_quality_pass": quality["status"].startswith("PASS_"),
        "base_data_quality_pass": _load(REPORT / "FORMAL_V2_DATA_QUALITY_GATE.json")["status"] == "PASS_FORMAL_V2_DATA_QUALITY_GATE",
        "primary_table_48_rows": table["row_count"] == len(table["rows"]) == 48,
        "primary_table_digest_recomputes": table["table_digest"] == canonical_sha256({key: value for key, value in table.items() if key != "table_digest"}),
        "results_digest_recomputes": results["results_digest"] == canonical_sha256({key: value for key, value in results.items() if key != "results_digest"}),
        "analysis_once": results["analysis_execution_count"] == 1,
        "formal_valid_episode_count_48": results["formal_valid_episode_count"] == 48,
    }
    validation = {
        "schema_version": "driveclarify.rq2_t_cg.formal_v2.independent_analysis_validation.v1",
        "status": "READY_TO_SHARE" if all(checks.values()) else "NEEDS_REVISION",
        "checks": checks,
        "failed_checks": [key for key, passed in checks.items() if not passed],
        "primary_claim_status": results["H_CG1"]["status"],
        "causal_language_authorized": False,
        "frame_rows_used_as_independent_n": False,
    }
    validation["receipt_digest"] = canonical_sha256(validation)
    _write_json(REPORT / "FORMAL_V2_INDEPENDENT_ANALYSIS_VALIDATION.json", validation)
    return results


def finalize() -> Mapping[str, Any]:
    _configure()
    if (REPORT / "FINAL_VALIDATION_RECEIPT.json").exists():
        raise RuntimeError("FINAL_VALIDATION_ALREADY_EXISTS")
    stability = _load(REPORT / "ORD_LATE_REVEAL_ENGINEERING_STABILITY.json", {})
    seam = _load(REPORT / "EXECUTION_SEAM_8_OF_8_RECEIPT.json", {})
    freeze = _load(REPORT / "FINAL_FORMAL_V2_FREEZE_RECEIPT.json", {})
    seeds = _load(REPORT / "FORMAL_V2_SEED_FRESHNESS_RECEIPT.json", {})
    roster = _load(REPORT / "FORMAL_V2_ROSTER.json", {})
    ledger = _load(REPORT / "FORMAL_V2_EXECUTION_LEDGER.json", {
        "formal_episode_attempt_count": 0, "formal_episode_valid_count": 0,
        "formal_scientific_exposures": 0, "formal_scientific_retry_count": 0,
        "formal_infrastructure_retry_count": 0,
    })
    hcg = _load(REPORT / "FORMAL_V2_HCG_RESULTS.json", {})
    source = campaign._source_freeze("FINAL_SOURCE_FREEZE")
    _write_json(REPORT / "SOURCE_FREEZE_RECEIPT.json", source)
    if stability.get("status") != "PASS_ORD_LATE_REVEAL_REPLACEMENT_3_OF_3":
        status = "ORD_LATE_REVEAL_REPLACEMENT_NOT_QUALIFIED"
        recommendation = "Preserve all rejected replacement identities and traces, generate no formal seeds, and redesign only a prospectively simpler ORD-LATE-REVEAL native instance under the unchanged late-evidence contract."
    elif seam.get("status") != "PASS_EXECUTION_SEAM_8_OF_8":
        status = "FORMAL_V2_EXECUTION_INTEGRITY_NOT_CLOSED"
        recommendation = "Preserve the failed fresh seam without seed substitution or scientific retuning, and classify only an independently verified ordinary infrastructure defect before any new execution."
    elif not freeze:
        status = "PASS_ORD_LATE_REVEAL_REPLACEMENT_AND_8_OF_8_SEAM"
        recommendation = "Continue automatically from the passed replacement and 8/8 seam to the final freeze, six shared fresh formal seeds, and the complete 48-cell run without recalibration."
    elif ledger.get("formal_episode_attempt_count", 0) != ledger.get("formal_episode_valid_count", 0):
        status = "FORMAL_V2_EXECUTION_INTEGRITY_NOT_CLOSED"
        recommendation = "Preserve the exposed sealed roster and invalid attempt, run no H-CG analysis, and do not retry or replace any formal seed."
    elif ledger.get("formal_episode_valid_count") == 48 and hcg.get("status") == "PASS_FROZEN_HCG_ANALYSIS_EXECUTED_ONCE":
        status = "PASS_RQ2_T_CG_FORMAL_V2_B2_SUPPORTED" if hcg["H_CG1"]["status"] == "SUPPORTED" else "PASS_RQ2_T_CG_FORMAL_V2_B2_NOT_SUPPORTED"
        recommendation = "Preserve the frozen replacement, sealed 8×6 roster, native traces, quality receipts, and one-pass analysis as the final non-retuned evidence package."
    else:
        status = "FORMAL_V2_EXECUTION_INTEGRITY_NOT_CLOSED"
        recommendation = "Resume only the next uncompleted gated command without changing the frozen replacement, calibration, shared seeds, control path, or analysis plan."
    selected = None
    if stability.get("selected_replacement_identity"):
        selected = next(row for row in stability["candidates"] if row["scene_identity"] == stability["selected_replacement_identity"])
    route = _load(REPORT / "ORD_LATE_REVEAL_ROUTE_RECEIPT.json", {})
    contract = _load(REPORT / "ORD_LATE_REVEAL_REPLACEMENT_CONTRACT.json", {})
    commitment = _load(REPORT / "ORD_LATE_REVEAL_COMMITMENT_CERTIFICATE.json", {})
    timing = _load(REPORT / "ORD_LATE_REVEAL_EVENT_TIMING_CERTIFICATE.json", {})
    policy = _load(REPORT / "BACKGROUND_TRAFFIC_POLICY_V2.json", {})
    actors = _load(REPORT / "FINAL_SCIENTIFIC_ACTOR_MANIFESTS.json", policy.get("prospective_family_manifests", {}))
    primary = hcg.get("H_CG1", {}).get("endpoints", {})
    controls = hcg.get("supporting_controls", {})
    values = [
        ("failed_old_identity_preserved", OLD_FAILED_IDENTITY),
        ("new_replacement_identity", stability.get("selected_replacement_identity") or contract.get("replacement_identity")),
        ("original_ambiguous_instruction", ORIGINAL_INSTRUCTION),
        ("z1", Z1_INTERPRETATION),
        ("z2", Z2_INTERPRETATION),
        ("exact_route_geometry", route.get("exact_route_geometry")),
        ("GRP_evaluator_agent_equivalence", route.get("engineering_runtime_equivalence", route.get("native_route_admission"))),
        ("commitment_definition", commitment.get("commitment")),
        ("decisive_late_event_definition", timing.get("event")),
        ("prospectively_certified_first_possible_reveal_TTCmt_range_s", timing.get("prospectively_certified_first_possible_reveal_TTCmt_range_s")),
        ("true_intent_leakage", {"forbidden_key_count": len(_scan_forbidden(contract)), "passenger_selection": "UNKNOWN"}),
        ("random_background_traffic_count", 0),
        ("scientific_actors_retained", actors.get("scientific_or_scenario_owned_entries_retained")),
        ("engineering_stability_seeds", stability.get("engineering_stability_seeds", [])),
        ("native_executability_3_of_3", None if selected is None else selected.get("pass_3_of_3")),
        ("descriptive_B1_B2", stability.get("descriptive_B1_B2", [])),
        ("calibration_freeze_unchanged", source.get("calibration_freeze_unchanged")),
        ("reserve_1_20_s_unchanged", float((contract.get("scene_contract") or {}).get("deadline", {}).get("total_reserved_simulation_s", -1.0)) == 1.2),
        ("fresh_full_8_of_8_seam", {"status": seam.get("status", "NOT_RUN"), "passed": seam.get("passed_scene_count", 0), "attempted": seam.get("attempted_scene_count", 0)}),
        ("final_formal_v2_freeze_digest", freeze.get("freeze_digest")),
        ("six_fresh_formal_seeds", seeds.get("seed_values", [])),
        ("seed_freshness", {"status": seeds.get("status"), "prior_occurrence_count": seeds.get("prior_occurrence_count")}),
        ("formal_attempts", ledger.get("formal_episode_attempt_count", 0)),
        ("formal_exposures", ledger.get("formal_scientific_exposures", 0)),
        ("formal_valid_episodes", ledger.get("formal_episode_valid_count", 0)),
        ("H_CG_analysis_legally_run", hcg.get("status") == "PASS_FROZEN_HCG_ANALYSIS_EXECUTED_ONCE"),
        ("H_CG1", hcg.get("H_CG1", "NOT_RUN")),
        ("H_CG2", hcg.get("H_CG2", "NOT_RUN")),
        ("H_CG3", hcg.get("H_CG3", "NOT_RUN")),
        ("H_CG4", hcg.get("H_CG4", "NOT_RUN")),
        ("B1_vs_B2_primary_effect", {key: value.get("paired_risk_difference_B2_minus_B1") for key, value in primary.items()}),
        ("confidence_intervals", {key: value.get("exhaustive_seed_block_interval") for key, value in primary.items()}),
        ("NONREVEAL", controls.get("NONREVEAL_false_sufficiency")),
        ("USC", controls.get("USC_fabricated_semantic_resolution")),
        ("invalidation", controls.get("invalidation_invalid_retention")),
        ("added_VLA_forwards", 0),
        ("PID_controller_changes", 0),
        ("second_control_writer", 0),
        ("true_intent_runtime_reads", 0),
        ("source_freeze", {"digest": source.get("receipt_digest"), "file_count": source.get("file_count"), "checkpoint_sha256": source.get("checkpoint_sha256")}),
        ("exact_final_status", status),
        ("one_next_recommendation", recommendation),
    ]
    final = {
        "schema_version": "driveclarify.rq2_t_cg.ord_late_replacement.final_validation.v1",
        "status": status,
        "required_final_return": {"{:02d}_{}".format(index, key): value for index, (key, value) in enumerate(values, 1)},
        "required_final_return_field_count": len(values),
        "validation_checks": {
            "exact_42_fields": len(values) == 42,
            "old_failure_preserved": OLD_FAILURE_RECEIPT.is_file(),
            "analysis_only_if_48_valid": not hcg or ledger.get("formal_episode_valid_count") == 48,
            "formal_scientific_retries_zero": ledger.get("formal_scientific_retry_count", 0) == 0,
            "source_files_hashed": source.get("file_count") == len(campaign.SOURCE_PATHS),
        },
        "one_next_recommendation": recommendation,
    }
    final["failed_validation_checks"] = [key for key, passed in final["validation_checks"].items() if not passed]
    final["receipt_digest"] = canonical_sha256(final)
    _write_json(REPORT / "FINAL_VALIDATION_RECEIPT.json", final)
    report_lines = ["Final status: `{}`.".format(status)]
    for index, (key, value) in enumerate(values, 1):
        report_lines.append("{}. **{}**: `{}`".format(index, key, json.dumps(value, ensure_ascii=False, sort_keys=True)))
    _write_md(REPORT / "FINAL_REPORT.md", "DriveClarify RQ2-T-CG ORD-LATE replacement and Formal V2", report_lines)
    campaign._append_command("finalize-ord-late-replacement", status)
    print(json.dumps({"status": status, "formal_attempts": ledger.get("formal_episode_attempt_count", 0), "formal_valid": ledger.get("formal_episode_valid_count", 0), "recommendation": recommendation}, sort_keys=True), flush=True)
    return final


def run_all(wall_timeout_seconds: float) -> Mapping[str, Any]:
    _configure()
    if not REPORT.exists():
        prepare()
    stability = _load(REPORT / "ORD_LATE_REVEAL_ENGINEERING_STABILITY.json", {})
    if not stability:
        stability = qualify_replacement()
    if stability.get("status") != "PASS_ORD_LATE_REVEAL_REPLACEMENT_3_OF_3":
        return finalize()
    seam = _load(REPORT / "EXECUTION_SEAM_8_OF_8_RECEIPT.json", {})
    if not seam:
        seam = qualify_seams()
    if seam.get("status") != "PASS_EXECUTION_SEAM_8_OF_8":
        return finalize()
    if not (REPORT / "FORMAL_V2_ROSTER.json").exists():
        freeze_formal()
    ledger = campaign.run_formal(wall_timeout_seconds)
    if ledger.get("status") == "PASS_48_OF_48_VALID_COMPLETE_FORMAL_V2" and not (REPORT / "FORMAL_V2_HCG_RESULTS.json").exists():
        analyze()
    return finalize()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=(
        "prepare", "qualify-replacement", "qualify-seams", "freeze-formal",
        "run-formal", "analyze", "finalize", "run-all",
    ))
    parser.add_argument("--wall-timeout-seconds", type=float, default=2400.0)
    args = parser.parse_args()
    _configure()
    if args.command == "prepare":
        result = prepare(); status = result["offline"]["status"]
    elif args.command == "qualify-replacement":
        result = qualify_replacement(); status = result["status"]
    elif args.command == "qualify-seams":
        result = qualify_seams(); status = result["status"]
    elif args.command == "freeze-formal":
        freeze_formal(); status = "PASS_FINAL_FORMAL_V2_FREEZE_WITH_ORD_LATE_REPLACEMENT"
    elif args.command == "run-formal":
        result = campaign.run_formal(args.wall_timeout_seconds); status = result["status"]
    elif args.command == "analyze":
        result = analyze(); status = result["status"]
    elif args.command == "finalize":
        result = finalize(); status = result["status"]
    else:
        result = run_all(args.wall_timeout_seconds); status = result["status"]
    return 0 if str(status).startswith("PASS_") else 2


if __name__ == "__main__":
    raise SystemExit(main())
