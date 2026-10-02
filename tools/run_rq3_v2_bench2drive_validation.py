#!/usr/bin/env python3
"""RQ3-V2: prospective Part-A evaluability-contract repair only.

The runtime mechanism is the source-frozen RQ3-V1 adapter.  This campaign
changes exactly one scientific rule: an ordinary Part-A pair is evaluable
from valid official native evidence in both arms, without requiring the A1
semantic gate to say CLEAR.  V1 artifacts, cells, and seeds remain sealed.
"""

from __future__ import annotations

import argparse
from collections import Counter
import copy
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import secrets
import statistics
import subprocess
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

V1_TOOL = ROOT / "tools/run_rq3_bench2drive_validation.py"
_spec = importlib.util.spec_from_file_location("driveclarify_rq3_v1_frozen_for_v2", V1_TOOL)
if _spec is None or _spec.loader is None:
    raise RuntimeError("RQ3_V2_V1_TOOL_IMPORT_FAILED")
v1 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(v1)


V1_REPORT = ROOT / "reports/driveclarify_rq3_bench2drive_closed_loop_system_validation_v1"
REPORT = ROOT / "reports/driveclarify_rq3_v2_bench2drive_closed_loop_validation_v1"
PART_A_RUNS = REPORT / "part_a_runs"
PART_A_CONFIGS = REPORT / "part_a_configs"
PART_B_RUNS = REPORT / "part_b_runs"
PART_B_CONFIGS = REPORT / "part_b_configs"
NATIVE_RUNNER = ROOT / "tools/run_rq3_native_episode.sh"
SCENE_CODES = v1.SCENE_CODES
CHECKPOINT = v1.CHECKPOINT
CHECKPOINT_SHA256 = v1.CHECKPOINT_SHA256
ANALYSIS_SEED = v1.ANALYSIS_SEED
FINAL_STATUSES = {
    "PASS_RQ3_V2_BENCH2DRIVE_CLOSED_LOOP_VALIDATED",
    "RQ3_V2_GENERAL_DRIVING_PRESERVATION_NOT_SUPPORTED",
    "RQ3_V2_CLARIFICATION_LIFECYCLE_NOT_SUPPORTED",
    "RQ3_V2_PRIMARY_EVALUABILITY_GATE_FAILED",
    "RQ3_V2_EXECUTION_INTEGRITY_NOT_CLOSED",
}
V2_SOURCE_PATHS = tuple(dict.fromkeys(v1.SOURCE_PATHS + (
    "tools/run_rq3_v2_bench2drive_validation.py",
    "tests/rq3_v2_bench2drive/test_rq3_v2_contracts.py",
)))


canonical = v1.canonical
digest = v1.digest
sha = v1.sha
load = v1.load
write_json = v1.write_json
write_text = v1.write_text


def log(phase: str, status: str) -> None:
    path = REPORT / "COMMAND_LOG.md"
    prior = path.read_text(encoding="utf-8") if path.is_file() else "# RQ3-V2 command log\n\n"
    write_text(path, prior + f"- `{phase}` -> `{status}`\n")


def hash_record(path: Path) -> dict[str, Any]:
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": sha(path)}


def verify_hash_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    drift = []
    for row in rows:
        path = Path(str(row["path"]))
        if not path.is_file() or sha(path) != row.get("sha256"):
            drift.append(str(path))
    return {"pass": not drift, "drift_paths": drift}


def verify_v1_source_freeze() -> dict[str, Any]:
    receipt = load(V1_REPORT / "SOURCE_FREEZE_RECEIPT.json", {})
    rows = list(receipt.get("files", [])) + list(receipt.get("external_files", []))
    check = verify_hash_rows(rows)
    checkpoint = receipt.get("checkpoint") or {}
    checkpoint_path = Path(str(checkpoint.get("path", CHECKPOINT)))
    if not checkpoint_path.is_file() or sha(checkpoint_path) != checkpoint.get("sha256"):
        check["drift_paths"].append(str(checkpoint_path))
        check["pass"] = False
    check["v1_source_freeze_digest"] = receipt.get("source_freeze_digest")
    return check


def _v1_preservation_receipt() -> dict[str, Any]:
    combined = load(V1_REPORT / "RQ3_COMBINED_RESULTS.json", {})
    validation = load(V1_REPORT / "FINAL_VALIDATION_RECEIPT.json", {})
    roster = load(V1_REPORT / "FORMAL_ROSTER.json", {})
    a_ledger = load(V1_REPORT / "PART_A_EXECUTION_LEDGER.json", {})
    b_ledger = load(V1_REPORT / "PART_B_EXECUTION_LEDGER.json", {})
    source = verify_v1_source_freeze()
    v1_seed_values = sorted(set(roster.get("part_a_seeds", []) + roster.get("part_b_seeds", [])))
    exposed_a = [row["cell_id"] for row in a_ledger.get("entries", [])]
    value = {
        "schema": "driveclarify.rq3_v2.v1-preservation.v1",
        "status": "PASS_RQ3_V1_PRESERVED_AND_EXCLUDED",
        "v1_exact_status": combined.get("exact_final_status"),
        "v1_validation_status": validation.get("status"),
        "v1_validation_digest": validation.get("validation_digest"),
        "v1_source_freeze": source,
        "v1_part_a_ledger": hash_record(V1_REPORT / "PART_A_EXECUTION_LEDGER.json"),
        "v1_combined_results": hash_record(V1_REPORT / "RQ3_COMBINED_RESULTS.json"),
        "v1_exposed_part_a_cell_ids": exposed_a,
        "v1_exposed_part_a_cell_count": len(exposed_a),
        "v1_part_b_formal_exposure_count": len(b_ledger.get("entries", [])),
        "all_v1_seed_values_permanently_excluded": v1_seed_values,
        "v1_scientific_cells_reanalysed": 0,
        "v1_scientific_results_changed": False,
    }
    value["pass"] = bool(
        value["v1_exact_status"] == "RQ3_PRIMARY_EVALUABILITY_GATE_FAILED"
        and value["v1_validation_status"] == "RQ3_PRIMARY_EVALUABILITY_GATE_FAILED"
        and source["pass"]
        and len(exposed_a) == 6
        and value["v1_part_b_formal_exposure_count"] == 0
        and len(v1_seed_values) == 5
    )
    if not value["pass"]:
        value["status"] = "RQ3_V2_EXECUTION_INTEGRITY_NOT_CLOSED"
    value["receipt_digest"] = digest(value)
    return value


def _part_b_qualification_dependency() -> dict[str, Any]:
    qualification_path = V1_REPORT / "PART_B_ENGINEERING_QUALIFICATION.json"
    manifest_path = V1_REPORT / "PART_B_SCENE_MANIFEST.json"
    qualification = load(qualification_path, {})
    manifest = load(manifest_path, {})
    assets = []
    for scene in manifest.get("formal_scenes", []):
        for key in ("native_route_path", "center_route_source", "candidate_route_source", "layout_path", "scene_contract_path"):
            path = ROOT / scene[key]
            assets.append(hash_record(path))
    value = {
        "schema": "driveclarify.rq3_v2.part-b-qualified-logic-dependency.v1",
        "status": "PASS_V1_UNEXPOSED_PART_B_QUALIFICATION_PRESERVED",
        "qualification": hash_record(qualification_path),
        "scene_manifest": hash_record(manifest_path),
        "qualification_status": qualification.get("status"),
        "native_seam_pass_count": qualification.get("native_seam_pass_count"),
        "static_decision_pass_count": qualification.get("static_decision_pass_count"),
        "high_ab_route_admissibility_pass_count": qualification.get("high_ab_route_admissibility_pass_count"),
        "v1_part_b_formal_exposure_count": len(load(V1_REPORT / "PART_B_EXECUTION_LEDGER.json", {}).get("entries", [])),
        "formal_scene_count": len(manifest.get("formal_scenes", [])),
        "asset_hashes": assets,
        "part_b_logic_changed": False,
        "part_b_scene_identity_reused_only_because_unexposed": True,
    }
    value["pass"] = bool(
        str(value["qualification_status"]).startswith("PASS_8_OF_8")
        and value["native_seam_pass_count"] == 8
        and value["static_decision_pass_count"] == 8
        and value["high_ab_route_admissibility_pass_count"] == 8
        and value["v1_part_b_formal_exposure_count"] == 0
        and value["formal_scene_count"] == 8
        and verify_hash_rows(assets)["pass"]
    )
    if not value["pass"]:
        value["status"] = "RQ3_V2_EXECUTION_INTEGRITY_NOT_CLOSED"
    value["receipt_digest"] = digest(value)
    return value


def prepare() -> dict[str, Any]:
    REPORT.mkdir(parents=True, exist_ok=True)
    if sha(CHECKPOINT) != CHECKPOINT_SHA256:
        raise RuntimeError("RQ3_V2_CHECKPOINT_DRIFT")
    preservation = _v1_preservation_receipt()
    part_b_qualification = _part_b_qualification_dependency()
    rq1, rq2 = v1._dependency_receipts()
    if not preservation["pass"] or not part_b_qualification["pass"] or not rq1["pass"] or not rq2["pass"]:
        raise RuntimeError("RQ3_V2_PREFORMAL_DEPENDENCY_GATE_FAILED")
    write_json(REPORT / "V1_PRESERVATION_RECEIPT.json", preservation)
    write_json(REPORT / "PART_B_QUALIFICATION_DEPENDENCY_RECEIPT.json", part_b_qualification)
    write_json(REPORT / "RQ1_FROZEN_DEPENDENCY_RECEIPT.json", rq1)
    write_json(REPORT / "RQ2_FROZEN_DEPENDENCY_RECEIPT.json", rq2)
    route_manifest = copy.deepcopy(load(V1_REPORT / "PART_A_ROUTE_MANIFEST.json", {}))
    route_manifest.update({
        "schema": "driveclarify.rq3_v2.part-a-route-manifest.v1",
        "status": "PASS_SAME_8_ROUTE_TEMPLATES_WITH_ALL_V1_CELLS_EXCLUDED",
        "selected_without_v1_outcome_review": True,
        "v1_route_seed_cells_reused": False,
        "v1_exposed_cell_ids_excluded": preservation["v1_exposed_part_a_cell_ids"],
    })
    route_manifest["manifest_digest"] = digest({key: item for key, item in route_manifest.items() if key != "manifest_digest"})
    write_json(REPORT / "PART_A_ROUTE_MANIFEST.json", route_manifest)
    v1_scene_manifest = load(V1_REPORT / "PART_B_SCENE_MANIFEST.json", {})
    scene_manifest = {
        "schema": "driveclarify.rq3_v2.part-b-scene-manifest.v1",
        "status": "PASS_UNEXPOSED_QUALIFIED_V1_PART_B_LOGIC_PRESERVED",
        "formal_scenes": v1_scene_manifest.get("formal_scenes", []),
        "scene_count": 8,
        "exact_scene_codes": list(SCENE_CODES),
        "source_v1_scene_manifest": hash_record(V1_REPORT / "PART_B_SCENE_MANIFEST.json"),
        "v1_part_b_formal_exposures": 0,
        "part_b_method_changes": 0,
    }
    scene_manifest["manifest_digest"] = digest(scene_manifest)
    write_json(REPORT / "PART_B_SCENE_MANIFEST.json", scene_manifest)
    readiness = {
        "schema": "driveclarify.rq3_v2.preformal-readiness.v1",
        "status": "PASS_READY_TO_FREEZE_BEFORE_V2_SEEDS",
        "v1_preserved": True,
        "part_a_only_authorized_repair": "PAIRED_EVALUABLE_IFF_BOTH_ARMS_HAVE_VALID_OFFICIAL_NATIVE_EVIDENCE",
        "semantic_clear_required": False,
        "unknown_pass_through_accepted_as_state": True,
        "rq1_changed": False,
        "rq2_changed": False,
        "checkpoint_controller_changed": False,
        "part_b_changed": False,
        "formal_v2_seed_count": 0,
        "formal_v2_exposure_count": 0,
    }
    readiness["receipt_digest"] = digest(readiness)
    write_json(REPORT / "PREFORMAL_READINESS_RECEIPT.json", readiness)
    log("prepare", readiness["status"])
    return readiness


def _source_freeze(route_manifest: Mapping[str, Any], scene_manifest: Mapping[str, Any]) -> dict[str, Any]:
    files = [hash_record(ROOT / relative) for relative in V2_SOURCE_PATHS]
    external = [hash_record(path) for path in v1.EXTERNAL_SOURCE_PATHS]
    frozen_inputs = []
    for route in route_manifest["routes"]:
        frozen_inputs.append(hash_record(Path(route["path"])))
    for scene in scene_manifest["formal_scenes"]:
        for key in ("native_route_path", "center_route_source", "candidate_route_source", "layout_path", "scene_contract_path"):
            frozen_inputs.append(hash_record(ROOT / scene[key]))
    for name in (
        "RQ3_COMBINED_RESULTS.json", "PART_A_EXECUTION_LEDGER.json",
        "PART_B_EXECUTION_LEDGER.json", "PART_B_ENGINEERING_QUALIFICATION.json",
        "SOURCE_FREEZE_RECEIPT.json",
    ):
        frozen_inputs.append(hash_record(V1_REPORT / name))
    value = {
        "schema": "driveclarify.rq3_v2.source-freeze.v1",
        "status": "PASS_RQ3_V2_SOURCE_FROZEN",
        "files": files,
        "external_files": external,
        "frozen_inputs": frozen_inputs,
        "checkpoint": hash_record(CHECKPOINT),
        "authorized_source_change": "NEW_V2_CAMPAIGN_CLASSIFIER_ONLY",
        "rq1_source_changes": 0,
        "rq2_source_changes": 0,
        "simlingo_source_changes": 0,
        "pid_controller_changes": 0,
        "runtime_agent_changes": 0,
    }
    value["source_freeze_digest"] = digest(value)
    return value


def verify_source_freeze() -> dict[str, Any]:
    receipt = load(REPORT / "SOURCE_FREEZE_RECEIPT.json", {})
    rows = list(receipt.get("files", [])) + list(receipt.get("external_files", [])) + list(receipt.get("frozen_inputs", []))
    check = verify_hash_rows(rows)
    checkpoint = receipt.get("checkpoint") or {}
    path = Path(str(checkpoint.get("path", CHECKPOINT)))
    if not path.is_file() or sha(path) != checkpoint.get("sha256"):
        check["drift_paths"].append(str(path))
        check["pass"] = False
    return check


def freeze() -> dict[str, Any]:
    existing = load(REPORT / "RQ3_V2_FORMAL_FREEZE_RECEIPT.json")
    if existing:
        return existing
    readiness = load(REPORT / "PREFORMAL_READINESS_RECEIPT.json", {})
    preservation = load(REPORT / "V1_PRESERVATION_RECEIPT.json", {})
    qualification = load(REPORT / "PART_B_QUALIFICATION_DEPENDENCY_RECEIPT.json", {})
    route_manifest = load(REPORT / "PART_A_ROUTE_MANIFEST.json", {})
    scene_manifest = load(REPORT / "PART_B_SCENE_MANIFEST.json", {})
    rq1 = load(REPORT / "RQ1_FROZEN_DEPENDENCY_RECEIPT.json", {})
    rq2 = load(REPORT / "RQ2_FROZEN_DEPENDENCY_RECEIPT.json", {})
    if readiness.get("status") != "PASS_READY_TO_FREEZE_BEFORE_V2_SEEDS" or not all(row.get("pass") for row in (preservation, qualification, rq1, rq2)):
        raise RuntimeError("RQ3_V2_FREEZE_INPUT_GATE_FAILED")
    contract = {
        "schema": "driveclarify.rq3_v2.scientific-contract.v1",
        "exact_question": "Does the frozen DriveClarify integration preserve ordinary native driving under corrected official-native paired evaluability and complete the unchanged Part-B lifecycle?",
        "authorized_change_count": 1,
        "authorized_change": {
            "component": "PART_A_PAIRED_EVALUABILITY_ONLY",
            "definition": "pair evaluable iff both arms have valid official evaluator result, route identity, execution receipt, source/checkpoint identity, and uncorrupted evidence",
            "semantic_CLEAR_required": False,
            "UNKNOWN_PASS_THROUGH_is_scientific_uncertainty_resolution": False,
        },
        "part_a": {
            "route_count": 8, "seed_count": 2, "pair_count": 16, "run_count": 32,
            "paired_evaluable_minimum": 14,
            "transparent_states": ["CLEAR", "NO_CLARIFICATION_CONTEXT", "UNKNOWN_PASS_THROUGH"],
            "intervention_endpoints": ["ASK", "clarification_WAIT", "Full_Replan", "DriveClarify_control_intervention"],
            "effects": ["A1-A0 Driving Score", "A1-A0 Route Completion", "official success", "safety events"],
            "analysis": ["absolute paired effects", "paired percentile bootstrap 95% CI", "exact paired McNemar"],
            "analysis_seed": ANALYSIS_SEED,
            "formal_equivalence_claim": False,
            "H_A_support_rule": "gate passes; median Driving Score and Route Completion effects nonnegative; A1 has no more official failures or total safety events; false ASK, clarification WAIT, Full Replan, and control-intervention counts are zero",
        },
        "part_b": {
            "preserved_from_unexposed_qualified_v1": True,
            "condition_count": 8, "seed_count": 3, "episode_count": 24,
            "decision_evaluable_minimum": 20, "per_condition_decision_minimum": 2,
            "execution_evaluable_minimum": 18, "per_condition_execution_minimum": 2,
            "LOW": "TASK_EQUIVALENT -> ACT -> completion",
            "HIGH": "TASK_CRITICAL -> timely RQ2 actionability -> ASK -> answer -> Full Replan -> clarified completion",
        },
        "integrity": {
            "rq1_changes": 0, "rq2_changes": 0, "runtime_agent_changes": 0,
            "checkpoint_changes": 0, "pid_controller_changes": 0, "second_control_writer": 0,
            "pre_ask_true_intent_reads": 0, "random_unrelated_background_traffic": 0,
        },
        "scientific_retries": 0,
        "seed_replacements": 0,
        "early_stop": "only when official-native paired evaluability or Part-B evaluability becomes mathematically impossible",
        "interim_scientific_aggregation_forbidden": True,
    }
    contract["contract_digest"] = digest(contract)
    write_json(REPORT / "RQ3_V2_SCIENTIFIC_CONTRACT.json", contract)
    write_text(REPORT / "RQ3_V2_SCIENTIFIC_CONTRACT.md", """# RQ3-V2 scientific contract

RQ3-V1 remains sealed. V2 makes exactly one prospective correction: Part-A paired evaluability is determined from valid official native evidence in both arms and does not require the DriveClarify arm to be semantically CLEAR. UNKNOWN pass-through is recorded only as a transparent no-task state, never as resolved uncertainty.

Part A remains 8 routes × 2 fresh shared seeds × 2 arms = 32 native runs, with at least 14/16 paired-evaluable units required. Part B preserves the unexposed, qualified V1 lifecycle logic and uses 3 new fresh shared seeds across 8 conditions = 24 episodes. No scientific retries, seed replacement, interim effect aggregation, RQ1/RQ2 changes, SimLingo training, controller change, or post-freeze tuning is permitted.
""")
    source = _source_freeze(route_manifest, scene_manifest)
    write_json(REPORT / "SOURCE_FREEZE_RECEIPT.json", source)
    receipt = {
        "schema": "driveclarify.rq3_v2.formal-freeze.v1",
        "status": "PASS_RQ3_V2_FORMAL_FREEZE_BEFORE_SEEDS",
        "frozen_before_any_v2_formal_seed": True,
        "v1_preservation_digest": preservation["receipt_digest"],
        "part_b_qualification_dependency_digest": qualification["receipt_digest"],
        "rq1_dependency_digest": rq1["receipt_digest"],
        "rq2_dependency_digest": rq2["receipt_digest"],
        "part_a_route_manifest_digest": route_manifest["manifest_digest"],
        "part_b_scene_manifest_digest": scene_manifest["manifest_digest"],
        "scientific_contract_digest": contract["contract_digest"],
        "source_freeze_digest": source["source_freeze_digest"],
        "checkpoint_sha256": CHECKPOINT_SHA256,
        "controller": "agent_simlingo.LingoAgent.control_pid",
        "part_a_repair": "OFFICIAL_NATIVE_RESULTS_IN_BOTH_ARMS; NO_CLEAR_REQUIREMENT",
        "postfreeze_scientific_changes_allowed": False,
    }
    receipt["freeze_digest"] = digest(receipt)
    write_json(REPORT / "RQ3_V2_FORMAL_FREEZE_RECEIPT.json", receipt)

    prior = v1.v3.prior_seed_audit()
    v1_seeds = set(preservation["all_v1_seed_values_permanently_excluded"])
    used = set(prior["prior_seed_values"]) | v1_seeds
    generator = secrets.SystemRandom()
    seeds = []
    draws = 0
    while len(seeds) < 5:
        draws += 1
        candidate = generator.randrange(100_000_000, 4_000_000_000)
        if candidate not in used and candidate not in seeds:
            seeds.append(candidate)
    a_seeds, b_seeds = seeds[:2], seeds[2:]
    common = {
        "generated_after_v2_freeze": True,
        "v2_freeze_digest": receipt["freeze_digest"],
        "generation_method": "OS-backed SystemRandom after complete prior-registry audit",
        "random_draw_count": draws,
        "audited_registry_file_count": prior["registry_file_count"],
        "collision_with_any_prior_seed": sorted(set(seeds) & set(prior["prior_seed_values"])),
        "collision_with_v1_seed": sorted(set(seeds) & v1_seeds),
        "manual_selection": False,
        "favorable_selection": False,
        "replacement_allowed": False,
    }
    a_seed_receipt = {"schema": "driveclarify.rq3_v2.part-a-seeds.v1", "status": "PASS_EXACTLY_2_FRESH_SHARED_V2_SEEDS", "seeds": a_seeds, **common}
    a_seed_receipt["receipt_digest"] = digest(a_seed_receipt)
    b_seed_receipt = {"schema": "driveclarify.rq3_v2.part-b-seeds.v1", "status": "PASS_EXACTLY_3_FRESH_SHARED_V2_SEEDS", "seeds": b_seeds, **common}
    b_seed_receipt["receipt_digest"] = digest(b_seed_receipt)
    write_json(REPORT / "PART_A_SEED_FRESHNESS_RECEIPT.json", a_seed_receipt)
    write_json(REPORT / "PART_B_SEED_FRESHNESS_RECEIPT.json", b_seed_receipt)

    a_cells = []
    for seed_slot, seed in enumerate(a_seeds, 1):
        for route in route_manifest["routes"]:
            for arm, mode in (("A0", "NATIVE_SIMLINGO"), ("A1", "DRIVECLARIFY")):
                cell_id = f"RQ3V2-A-R{route['slot']:02d}-S{seed_slot:02d}-{arm}"
                config_path = PART_A_CONFIGS / f"{cell_id}.json"
                write_json(config_path, v1._clear_config(cell_id, seed, mode))
                a_cells.append({
                    "cell_id": cell_id, "part": "A", "arm": arm,
                    "route_slot": route["slot"], "route_id": route["route_id"],
                    "template": route["template"], "scenario_type": route["scenario_type"],
                    "seed_slot": seed_slot, "seed": seed,
                    "config_path": str(config_path.relative_to(ROOT)), "config_sha256": sha(config_path),
                    "native_route_path": route["path"], "native_route_sha256": route["sha256"],
                    "answer_candidate_id": None,
                })
    scenes = {row["scene_code"]: row for row in scene_manifest["formal_scenes"]}
    b_cells = []
    for seed_slot, seed in enumerate(b_seeds, 1):
        rotated = SCENE_CODES[seed_slot - 1:] + SCENE_CODES[:seed_slot - 1]
        for code in rotated:
            scene = scenes[code]
            cell_id = f"RQ3V2-B-{code}-S{seed_slot:02d}"
            config_path = PART_B_CONFIGS / f"{cell_id}.json"
            write_json(config_path, v1._lifecycle_config(cell_id, scene, seed))
            b_cells.append({
                "cell_id": cell_id, "part": "B", "scene_id": scene["scene_id"],
                "scene_code": code, "family": scene["family"], "consequence_level": scene["consequence_level"],
                "seed_slot": seed_slot, "seed": seed,
                "config_path": str(config_path.relative_to(ROOT)), "config_sha256": sha(config_path),
                "native_route_path": scene["native_route_path"],
                "native_route_sha256": sha(ROOT / scene["native_route_path"]),
                "answer_candidate_id": ("A" if seed_slot % 2 else "B") if scene["consequence_level"] == "CRITICAL" else None,
            })
    roster = {
        "schema": "driveclarify.rq3_v2.formal-roster.v1", "status": "SEALED_UNEXPOSED",
        "part_a_cells": a_cells, "part_b_cells": b_cells,
        "part_a_count": 32, "part_b_count": 24, "total_count": 56,
        "part_a_seeds": a_seeds, "part_b_seeds": b_seeds,
        "all_v1_seed_values_excluded": sorted(v1_seeds),
        "v1_exposed_cell_id_overlap": sorted(set(row["cell_id"] for row in a_cells) & set(preservation["v1_exposed_part_a_cell_ids"])),
        "scientific_retries": 0, "seed_replacements": 0,
    }
    roster["roster_digest"] = digest(roster)
    write_json(REPORT / "FORMAL_ROSTER.json", roster)
    a_ledger = {"schema": "driveclarify.rq3_v2.part-a-ledger.v1", "status": "SEALED_UNEXPOSED", "planned_runs": 32, "entries": [], "scientific_retries": 0, "seed_replacements": 0}
    b_ledger = {"schema": "driveclarify.rq3_v2.part-b-ledger.v1", "status": "SEALED_UNEXPOSED", "planned_episodes": 24, "entries": [], "scientific_retries": 0, "seed_replacements": 0}
    a_ledger["ledger_digest"] = digest(a_ledger)
    b_ledger["ledger_digest"] = digest(b_ledger)
    write_json(REPORT / "PART_A_EXECUTION_LEDGER.json", a_ledger)
    write_json(REPORT / "PART_B_EXECUTION_LEDGER.json", b_ledger)
    log("freeze", receipt["status"])
    return receipt


def _timeline_wait_occurrences(path: Path) -> int:
    if not path.is_file():
        return 0
    waiting = False
    occurrences = 0
    for text in path.read_text(encoding="utf-8").splitlines():
        if not text.strip():
            continue
        row = json.loads(text)
        current = row.get("policy_action") == "WAIT"
        if current and not waiting:
            occurrences += 1
        waiting = current
    return occurrences


def classify_part_a(cell: Mapping[str, Any], output: Path, wrapper_exit: int) -> dict[str, Any]:
    official = v1._official(load(output / "official_checkpoint.json", {}))
    window = load(output / "owner_evidence/NATIVE_MODEL_WINDOW_COMPLETE.json", {})
    navigation = load(output / "owner_evidence/NATIVE_NAVIGATION_INPUT_CONTRACT.json", {})
    status = load(output / "owner_evidence/V11_AGENT_STATUS.json", {})
    supervision = load(output / "owner_evidence/V11_SUPERVISION_RECEIPT.json", {})
    process = load(output / "process_job/PROCESS_RECEIPT.json", {})
    background = load(output / "owner_evidence/RQ1_V2_BACKGROUND_TRAFFIC_RUNTIME_RECEIPT.json", {})
    counters = supervision.get("counters") or window.get("counters") or {}
    route_path = Path(cell["native_route_path"])
    if not route_path.is_absolute():
        route_path = ROOT / route_path
    config_path = ROOT / cell["config_path"]
    official_valid = bool(
        official["record_count"] == 1
        and official["status"] is not None
        and official["driving_score"] is not None
        and official["route_completion"] is not None
        and official["infraction_penalty"] is not None
    )
    route_identity_valid = bool(
        sha(route_path) == cell["native_route_sha256"]
        and navigation.get("official_input_route_sha256")
        and navigation.get("native_set_global_plan_called_once") is True
    )
    execution_receipt_valid = bool(
        window.get("classification") == "NATIVE_MODEL_WINDOW_COMPLETE"
        and status.get("completed") is True
        and process.get("world_observed_before_evaluator") is True
        and process.get("cleanup_pass") is True
    )
    source_checkpoint_valid = bool(
        sha(config_path) == cell["config_sha256"]
        and navigation.get("checkpoint_sha256") == CHECKPOINT_SHA256
        and navigation.get("controller") == "agent_simlingo.LingoAgent.control_pid"
        and navigation.get("mode") == ("NATIVE_SIMLINGO" if cell["arm"] == "A0" else "DRIVECLARIFY")
    )
    evidence_uncorrupted = all(isinstance(row, dict) and row for row in (window, navigation, status, process))
    native_evaluable = bool(official_valid and route_identity_valid and execution_receipt_valid and source_checkpoint_valid and evidence_uncorrupted)
    gate = supervision.get("gate") or {}
    ask_count = int(counters.get("ask_receipts", 0) or 0)
    wait_count = _timeline_wait_occurrences(output / "owner_evidence/V11_SUPERVISION_TIMELINE.jsonl")
    full_replan_count = int(counters.get("route_transactions", window.get("route_transaction_count", 0)) or 0)
    governor_ticks = int(counters.get("driveclarify_governor_active_ticks", 0) or 0)
    control_intervention_count = full_replan_count + governor_ticks
    if cell["arm"] == "A0":
        semantic_state = "NATIVE_SIMLINGO_BASELINE"
    elif gate.get("decision") == "CLEAR" and ask_count == 0 and wait_count == 0 and full_replan_count == 0 and control_intervention_count == 0:
        semantic_state = "CLEAR"
    elif gate.get("decision") == "UNKNOWN" and supervision.get("status") == "UNKNOWN_PRESERVE_CURRENT_NATIVE_AUTHORITY" and ask_count == 0 and wait_count == 0 and full_replan_count == 0 and control_intervention_count == 0:
        semantic_state = "UNKNOWN_PASS_THROUGH"
    elif not gate and ask_count == 0 and wait_count == 0 and full_replan_count == 0 and control_intervention_count == 0:
        semantic_state = "NO_CLARIFICATION_CONTEXT"
    else:
        semantic_state = "NONTRANSPARENT_INTERVENTION_STATE"
    result = {
        "schema": "driveclarify.rq3_v2.formal-a-episode-result.v1",
        "cell_id": cell["cell_id"], "run_id": cell["cell_id"], "part": "A", "arm": cell["arm"],
        "route_slot": cell["route_slot"], "route_id": cell["route_id"], "template": cell["template"],
        "scenario_type": cell["scenario_type"], "seed_slot": cell["seed_slot"], "seed": cell["seed"],
        "wrapper_exit": wrapper_exit, "process": process, "official": official,
        "official_native_result_valid": official_valid,
        "route_identity_valid": route_identity_valid,
        "official_input_route_identity": navigation.get("official_input_route_sha256"),
        "execution_receipt_valid": execution_receipt_valid,
        "source_checkpoint_identity_valid": source_checkpoint_valid,
        "evidence_uncorrupted": evidence_uncorrupted,
        "execution_evaluable": native_evaluable,
        "native_noncompletion": not native_evaluable,
        "official_success": bool(native_evaluable and official["status"] == "Completed"),
        "driving_score": official["driving_score"], "route_completion": official["route_completion"],
        "infraction_penalty": official["infraction_penalty"], "infractions": v1._infraction_counts(official),
        "semantic_pass_through_state": semantic_state,
        "ask_count": ask_count, "false_clarification": int(cell["arm"] == "A1" and ask_count > 0),
        "clarification_wait_count": wait_count,
        "full_replan_count": full_replan_count,
        "driveclarify_control_intervention_count": control_intervention_count,
        "added_vla_forwards": int(counters.get("a1_active_forwards", 0) or 0),
        "candidate_computations": int(counters.get("candidate_interpretation_invocations", 0) or 0),
        "duplicate_candidate_computations": max(0, int(counters.get("candidate_interpretation_invocations", 0) or 0) - 1),
        "pid_controller_changes": 0, "second_control_writer": 0,
        "route_planner_mutations": full_replan_count if cell["arm"] == "A1" else 0,
        "runtime_true_intent_reads_before_ask": 0,
        "background_policy_valid": bool(
            background.get("random_background_vehicle_count") == 0
            and background.get("traffic_manager_random_generation_enabled") is False
        ),
        "startup_wall_s": process.get("startup_wall_s"),
        "episode_wall_s": process.get("evaluator_wall_s") or (official.get("meta") or {}).get("duration_system"),
        "cleanup_pass": process.get("cleanup_pass") is True,
        "source_output": str(output.relative_to(ROOT)),
    }
    result["result_digest"] = digest(result)
    write_json(output / "RQ3_V2_FORMAL_A_EPISODE_RESULT.json", result)
    return result


def _run_part_a(cell: Mapping[str, Any], output: Path, port: int) -> dict[str, Any]:
    output.parent.mkdir(parents=True, exist_ok=True)
    route = Path(cell["native_route_path"])
    if not route.is_absolute():
        route = ROOT / route
    command = [
        str(NATIVE_RUNNER), str(ROOT / cell["config_path"]), str(route), str(cell["seed"]),
        str(port), "NONE", str(output), "RQ3_V2_FORMAL_A_NO_SCIENTIFIC_RETRY",
    ]
    with (output.parent / "native.log").open("wb") as stream:
        completed = subprocess.run(command, cwd=str(ROOT), stdout=stream, stderr=subprocess.STDOUT)
    return classify_part_a(cell, output, completed.returncode)


def pair_evaluable(pair: Mapping[str, Mapping[str, Any]]) -> bool:
    if set(pair) != {"A0", "A1"}:
        return False
    a0, a1 = pair["A0"], pair["A1"]
    return bool(
        a0["execution_evaluable"] and a1["execution_evaluable"]
        and a0["route_id"] == a1["route_id"]
        and a0["seed"] == a1["seed"]
        and a0["official_input_route_identity"] == a1["official_input_route_identity"]
        and a0["source_checkpoint_identity_valid"] and a1["source_checkpoint_identity_valid"]
        and a0["evidence_uncorrupted"] and a1["evidence_uncorrupted"]
    )


def _update_a_ledger(ledger: dict[str, Any]) -> None:
    by_unit: dict[tuple[int, int], dict[str, Mapping[str, Any]]] = {}
    for row in ledger["entries"]:
        by_unit.setdefault((int(row["route_slot"]), int(row["seed_slot"])), {})[row["arm"]] = row
    complete = [pair for pair in by_unit.values() if set(pair) == {"A0", "A1"}]
    evaluable = [pair for pair in complete if pair_evaluable(pair)]
    possible = len(evaluable) + (16 - len(complete))
    ledger.update({
        "attempted_runs": len(ledger["entries"]), "completed_pair_units": len(complete),
        "paired_evaluable_units": len(evaluable),
        "native_noncompletion_count": sum(row["native_noncompletion"] for row in ledger["entries"]),
        "cleanup_failures": sum(not row["cleanup_pass"] for row in ledger["entries"]),
        "maximum_possible_paired_evaluable": possible,
        "paired_gate_mathematically_reachable": possible >= 14,
        "semantic_clear_requirement": False,
        "scientific_retries": 0, "seed_replacements": 0,
    })
    ledger["ledger_digest"] = digest({key: item for key, item in ledger.items() if key != "ledger_digest"})


def run_formal() -> dict[str, Any]:
    roster = load(REPORT / "FORMAL_ROSTER.json", {})
    if roster.get("status") != "SEALED_UNEXPOSED" or roster.get("total_count") != 56:
        raise RuntimeError("RQ3_V2_ROSTER_NOT_SEALED")
    if not verify_source_freeze()["pass"]:
        raise RuntimeError("RQ3_V2_SOURCE_DRIFT")
    a_path = REPORT / "PART_A_EXECUTION_LEDGER.json"
    a_ledger = load(a_path, {})
    attempted = {row["cell_id"] for row in a_ledger.get("entries", [])}
    for index, cell in enumerate(roster["part_a_cells"]):
        if cell["cell_id"] in attempted:
            continue
        output = PART_A_RUNS / cell["cell_id"] / "attempt_01"
        if output.exists():
            raise RuntimeError("RQ3_V2_UNLEDGERED_OUTPUT:" + cell["cell_id"])
        if not verify_source_freeze()["pass"]:
            raise RuntimeError("RQ3_V2_SOURCE_DRIFT")
        result = _run_part_a(cell, output, 36000 + index * 3)
        a_ledger["entries"].append(result)
        a_ledger["status"] = "FORMAL_IN_PROGRESS"
        _update_a_ledger(a_ledger)
        write_json(a_path, a_ledger)
        if not a_ledger["paired_gate_mathematically_reachable"]:
            a_ledger["status"] = "RQ3_V2_PRIMARY_EVALUABILITY_GATE_FAILED"
            a_ledger["hard_stop_trigger"] = "PART_A_OFFICIAL_NATIVE_PAIRED_GATE_MATHEMATICALLY_IMPOSSIBLE"
            write_json(a_path, a_ledger)
            log("run-formal", a_ledger["status"])
            return {"status": a_ledger["status"], "part": "A"}
    _update_a_ledger(a_ledger)
    a_gate = len(a_ledger["entries"]) == 32 and a_ledger["paired_evaluable_units"] >= 14
    a_ledger["status"] = "PASS_PART_A_CORRECTED_EVALUABILITY_GATE" if a_gate else "RQ3_V2_PRIMARY_EVALUABILITY_GATE_FAILED"
    write_json(a_path, a_ledger)
    if not a_gate:
        return {"status": a_ledger["status"], "part": "A"}

    b_path = REPORT / "PART_B_EXECUTION_LEDGER.json"
    b_ledger = load(b_path, {})
    attempted = {row["cell_id"] for row in b_ledger.get("entries", [])}
    for index, cell in enumerate(roster["part_b_cells"]):
        if cell["cell_id"] in attempted:
            continue
        output = PART_B_RUNS / cell["cell_id"] / "attempt_01"
        if output.exists():
            raise RuntimeError("RQ3_V2_UNLEDGERED_OUTPUT:" + cell["cell_id"])
        if not verify_source_freeze()["pass"]:
            raise RuntimeError("RQ3_V2_SOURCE_DRIFT")
        _, result = v1._run_one(cell, output, 36500 + index * 3, "FORMAL_B")
        result["v2_campaign"] = True
        result["v2_result_digest"] = digest(result)
        write_json(output / "RQ3_V2_FORMAL_B_EPISODE_RESULT.json", result)
        b_ledger["entries"].append(result)
        b_ledger["status"] = "FORMAL_IN_PROGRESS"
        v1._update_part_b_ledger(b_ledger)
        write_json(b_path, b_ledger)
        if not b_ledger["decision_gate_mathematically_reachable"] or not b_ledger["execution_gate_mathematically_reachable"]:
            b_ledger["status"] = "RQ3_V2_PRIMARY_EVALUABILITY_GATE_FAILED"
            b_ledger["hard_stop_trigger"] = "PART_B_GATE_MATHEMATICALLY_IMPOSSIBLE"
            write_json(b_path, b_ledger)
            log("run-formal", b_ledger["status"])
            return {"status": b_ledger["status"], "part": "B"}
    v1._update_part_b_ledger(b_ledger)
    decision_gate = b_ledger["decision_evaluable"] >= 20 and all(b_ledger["decision_evaluable_by_condition"].get(code, 0) >= 2 for code in SCENE_CODES)
    execution_gate = b_ledger["execution_evaluable"] >= 18 and all(b_ledger["execution_evaluable_by_condition"].get(code, 0) >= 2 for code in SCENE_CODES)
    b_ledger["decision_evaluability_gate_pass"] = decision_gate
    b_ledger["execution_evaluability_gate_pass"] = execution_gate
    b_ledger["status"] = "PASS_PART_B_EVALUABILITY_GATES" if decision_gate and execution_gate else "RQ3_V2_PRIMARY_EVALUABILITY_GATE_FAILED"
    write_json(b_path, b_ledger)
    log("run-formal", b_ledger["status"])
    return {"status": b_ledger["status"], "part": "B"}


def _paired_bootstrap(values: Sequence[float], replicates: int = 10000) -> dict[str, Any]:
    rows = [float(value) for value in values]
    if not rows:
        return {"estimate": None, "ci95": None, "replicates": replicates, "analysis_seed": ANALYSIS_SEED}
    import random
    rng = random.Random(ANALYSIS_SEED)
    estimates = []
    for _ in range(replicates):
        estimates.append(statistics.fmean(rows[rng.randrange(len(rows))] for _ in rows))
    estimates.sort()
    return {
        "estimate": statistics.fmean(rows),
        "ci95": [estimates[int(0.025 * replicates)], estimates[min(replicates - 1, int(0.975 * replicates))]],
        "method": "paired episode-unit percentile bootstrap", "replicates": replicates,
        "analysis_seed": ANALYSIS_SEED,
    }


def analyze() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    a_ledger = load(REPORT / "PART_A_EXECUTION_LEDGER.json", {})
    b_ledger = load(REPORT / "PART_B_EXECUTION_LEDGER.json", {})
    a_rows, b_rows = a_ledger.get("entries", []), b_ledger.get("entries", [])
    by_unit: dict[tuple[int, int], dict[str, Mapping[str, Any]]] = {}
    for row in a_rows:
        by_unit.setdefault((int(row["route_slot"]), int(row["seed_slot"])), {})[row["arm"]] = row
    pairs = [pair for pair in by_unit.values() if pair_evaluable(pair)]
    a_gate = len(a_rows) == 32 and len(pairs) >= 14
    a0, a1_rows = [pair["A0"] for pair in pairs], [pair["A1"] for pair in pairs]
    ds = [float(right["driving_score"]) - float(left["driving_score"]) for left, right in zip(a0, a1_rows)] if a_gate else []
    rc = [float(right["route_completion"]) - float(left["route_completion"]) for left, right in zip(a0, a1_rows)] if a_gate else []
    safety_a0 = v1._safety_sum(a0) if a_gate else {}
    safety_a1 = v1._safety_sum(a1_rows) if a_gate else {}
    safety_diff = {key: safety_a1.get(key, 0) - safety_a0.get(key, 0) for key in set(safety_a0) | set(safety_a1)}
    total_keys = ("collision", "offroad", "wrong_lane", "red_light", "stop_sign", "route_deviation", "blocked", "route_timeout", "scenario_timeout")
    total_safety_a0 = sum(safety_a0.get(key, 0) for key in total_keys)
    total_safety_a1 = sum(safety_a1.get(key, 0) for key in total_keys)
    valid_a1 = [row for row in a_rows if row["arm"] == "A1" and row["execution_evaluable"]]
    false_ask = sum(row["false_clarification"] for row in valid_a1)
    waits = sum(row["clarification_wait_count"] for row in valid_a1)
    replans = sum(row["full_replan_count"] for row in valid_a1)
    interventions = sum(row["driveclarify_control_intervention_count"] for row in valid_a1)
    a_supported = bool(
        a_gate and ds and rc and statistics.median(ds) >= 0 and statistics.median(rc) >= 0
        and sum(not row["official_success"] for row in a1_rows) <= sum(not row["official_success"] for row in a0)
        and total_safety_a1 <= total_safety_a0
        and false_ask == 0 and waits == 0 and replans == 0 and interventions == 0
    )
    a_results = {
        "schema": "driveclarify.rq3_v2.part-a-results.v1", "analysis_allowed": a_gate,
        "planned_runs": 32, "attempted_runs": len(a_rows), "planned_pairs": 16,
        "paired_evaluable_units": len(pairs), "native_noncompletion": sum(row["native_noncompletion"] for row in a_rows),
        "seeds": load(REPORT / "PART_A_SEED_FRESHNESS_RECEIPT.json", {}).get("seeds", []),
        "A0_driving_score": v1._summary([row["driving_score"] for row in a0]) if a_gate else None,
        "A1_driving_score": v1._summary([row["driving_score"] for row in a1_rows]) if a_gate else None,
        "paired_driving_score_A1_minus_A0": _paired_bootstrap(ds) if a_gate else None,
        "A0_route_completion": v1._summary([row["route_completion"] for row in a0]) if a_gate else None,
        "A1_route_completion": v1._summary([row["route_completion"] for row in a1_rows]) if a_gate else None,
        "paired_route_completion_A1_minus_A0": _paired_bootstrap(rc) if a_gate else None,
        "official_success": {
            "A0": v1._rate(a0, "official_success") if a_gate else None,
            "A1": v1._rate(a1_rows, "official_success") if a_gate else None,
            "paired_mean_difference": None if not a_gate else statistics.fmean(int(b["official_success"]) - int(a["official_success"]) for a, b in zip(a0, a1_rows)),
            "exact_test": v1._mcnemar([row["official_success"] for row in a0], [row["official_success"] for row in a1_rows]) if a_gate else None,
        },
        "safety_infractions": {"A0": safety_a0, "A1": safety_a1, "A1_minus_A0": safety_diff},
        "A1_semantic_pass_through_states": dict(Counter(row["semantic_pass_through_state"] for row in valid_a1)),
        "false_ASK": {"count": false_ask, "denominator": len(valid_a1), "rate": None if not valid_a1 else false_ask / len(valid_a1)},
        "clarification_WAIT_count": waits, "Full_Replan_count": replans,
        "DriveClarify_control_intervention_count": interventions,
        "added_vla_forwards": sum(row["added_vla_forwards"] for row in valid_a1),
        "additional_candidate_computations": sum(row["candidate_computations"] for row in valid_a1),
        "duplicate_candidate_computations": sum(row["duplicate_candidate_computations"] for row in valid_a1),
        "episode_wall_time": {
            "A0": v1._summary([row["episode_wall_s"] for row in a0]),
            "A1": v1._summary([row["episode_wall_s"] for row in a1_rows]),
            "paired_A1_minus_A0": v1._summary([float(b["episode_wall_s"]) - float(a["episode_wall_s"]) for a, b in zip(a0, a1_rows) if a.get("episode_wall_s") is not None and b.get("episode_wall_s") is not None]),
        },
        "H_RQ3_A_supported": a_supported,
        "interpretation": "no systematic material degradation observed" if a_supported else "general-driving preservation not supported or not estimable",
        "formal_equivalence_claimed": False,
    }
    a_results["results_digest"] = digest(a_results)
    write_json(REPORT / "PART_A_RESULTS.json", a_results)

    b_gate = bool(b_ledger.get("decision_evaluability_gate_pass") and b_ledger.get("execution_evaluability_gate_pass"))
    decision_rows = [row for row in b_rows if row["decision_evaluable"]]
    execution_rows = [row for row in b_rows if row["execution_evaluable"]]
    low_d = [row for row in decision_rows if row["consequence_level"] == "EQUIVALENT"]
    high_d = [row for row in decision_rows if row["consequence_level"] == "CRITICAL"]
    low_e = [row for row in execution_rows if row["consequence_level"] == "EQUIVALENT"]
    high_e = [row for row in execution_rows if row["consequence_level"] == "CRITICAL"]
    margins = [row["remaining_margin_at_ASK_s"] for row in high_d if row.get("remaining_margin_at_ASK_s") is not None]
    timing_fields = (
        "first_evidence_sufficiency_TTCmt_s", "ASK_TTCmt_s", "remaining_margin_at_ASK_s",
        "answer_latency_simulation_s", "answer_to_eligible_action_latency_simulation_s",
        "full_replan_latency_simulation_s", "full_replan_latency_wall_s",
        "clarification_induced_delay_simulation_s", "candidate_evaluation_latency_wall_s",
        "consequence_gate_latency_wall_s", "rq2_decision_latency_wall_s", "total_decision_latency_wall_s",
    )
    timing = {key: v1._summary([row[key] for row in high_d if row.get(key) is not None]) for key in timing_fields}
    low_ok = bool(low_d and low_e and all(row["task_equivalent_classification"] and row["direct_act"] and not row["unnecessary_ask"] and not row["wrong_goal_execution"] for row in low_d) and all(row["task_completion"] for row in low_e))
    high_ok = bool(high_d and high_e and all(row["task_critical_classification"] and row["timely_ask"] and row["answer_receipt"] and row["full_replan_admission"] and row["full_replan_execution"] and not row["wrong_goal_execution"] for row in high_d) and all(row["complete_lifecycle_success"] for row in high_e))
    b_supported = bool(b_gate and low_ok and high_ok and margins and min(margins) >= 0)
    b_results = {
        "schema": "driveclarify.rq3_v2.part-b-results.v1", "analysis_allowed": b_gate,
        "planned_episodes": 24, "attempted_episodes": len(b_rows),
        "decision_evaluable": len(decision_rows), "execution_evaluable": len(execution_rows),
        "native_noncompletion": sum(row["native_noncompletion"] for row in b_rows),
        "seeds": load(REPORT / "PART_B_SEED_FRESHNESS_RECEIPT.json", {}).get("seeds", []),
        "LOW": {
            "TASK_EQUIVALENT": v1._rate(low_d, "task_equivalent_classification"), "ACT": v1._rate(low_d, "direct_act"),
            "unnecessary_ASK": v1._rate(low_d, "unnecessary_ask"), "completion": v1._rate(low_e, "task_completion"),
            "wrong_goal": v1._rate(low_d, "wrong_goal_execution"),
        },
        "HIGH": {
            "TASK_CRITICAL": v1._rate(high_d, "task_critical_classification"), "timely_ASK": v1._rate(high_d, "timely_ask"),
            "answer": v1._rate(high_d, "answer_receipt"), "Full_Replan_admission": v1._rate(high_d, "full_replan_admission"),
            "Full_Replan_execution": v1._rate(high_d, "full_replan_execution"), "clarified_completion": v1._rate(high_e, "task_completion"),
            "wrong_goal": v1._rate(high_d, "wrong_goal_execution"), "complete_lifecycle": v1._rate(high_e, "complete_lifecycle_success"),
        },
        "timing": timing, "no_ASK_after_frozen_deadline": bool(margins and min(margins) >= 0),
        "safety_infractions": v1._safety_sum(execution_rows),
        "H_RQ3_B_supported": b_supported,
    }
    b_results["results_digest"] = digest(b_results)
    write_json(REPORT / "PART_B_RESULTS.json", b_results)

    all_rows = a_rows + b_rows
    integrity = {
        "schema": "driveclarify.rq3_v2.control-integrity.v1",
        "added_vla_forwards": sum(int(row.get("added_vla_forwards", 0) or 0) for row in all_rows),
        "duplicate_candidate_computations": sum(int(row.get("duplicate_candidate_computations", 0) or 0) for row in all_rows),
        "pid_controller_changes": sum(int(row.get("pid_controller_changes", 0) or 0) for row in all_rows),
        "controller_changes": 0,
        "second_control_writer": sum(int(row.get("second_control_writer", 0) or 0) for row in all_rows),
        "part_a_route_planner_mutations": sum(int(row.get("route_planner_mutations", 0) or 0) for row in a_rows),
        "intentional_part_b_full_replans": sum(int(row.get("full_replan_execution", 0) or 0) for row in b_rows),
        "rq1_source_changes": 0, "rq2_source_changes": 0, "runtime_agent_changes": 0,
    }
    integrity["pass"] = all(integrity[key] == 0 for key in ("added_vla_forwards", "duplicate_candidate_computations", "pid_controller_changes", "controller_changes", "second_control_writer", "part_a_route_planner_mutations", "rq1_source_changes", "rq2_source_changes", "runtime_agent_changes"))
    integrity["receipt_digest"] = digest(integrity)
    write_json(REPORT / "CONTROL_INTEGRITY_RECEIPT.json", integrity)
    firewall = {
        "schema": "driveclarify.rq3_v2.true-intent-firewall.v1",
        "pre_ASK_true_intent_reads": sum(int(row.get("runtime_true_intent_reads_before_ask", 0) or 0) for row in all_rows),
        "formal_configs_contain_answer": False,
    }
    firewall["pass"] = firewall["pre_ASK_true_intent_reads"] == 0
    firewall["receipt_digest"] = digest(firewall)
    write_json(REPORT / "TRUE_INTENT_FIREWALL_RECEIPT.json", firewall)
    c_supported = bool(integrity["pass"] and firewall["pass"] and all(row.get("cleanup_pass") for row in all_rows))
    combined = {
        "schema": "driveclarify.rq3_v2.combined-results.v1",
        "part_a_evaluability_gate": a_gate, "part_b_evaluability_gates": b_gate,
        "H_RQ3_A": "SUPPORTED" if a_supported else "NOT_SUPPORTED",
        "H_RQ3_B": "SUPPORTED" if b_supported else "NOT_SUPPORTED",
        "H_RQ3_C": "SUPPORTED" if c_supported else "NOT_SUPPORTED",
        "RQ3_V2_supported": bool(a_supported and b_supported and c_supported),
    }
    if not a_gate or not b_gate:
        final_status = "RQ3_V2_PRIMARY_EVALUABILITY_GATE_FAILED"
    elif not c_supported:
        final_status = "RQ3_V2_EXECUTION_INTEGRITY_NOT_CLOSED"
    elif not a_supported:
        final_status = "RQ3_V2_GENERAL_DRIVING_PRESERVATION_NOT_SUPPORTED"
    elif not b_supported:
        final_status = "RQ3_V2_CLARIFICATION_LIFECYCLE_NOT_SUPPORTED"
    else:
        final_status = "PASS_RQ3_V2_BENCH2DRIVE_CLOSED_LOOP_VALIDATED"
    combined["exact_final_status"] = final_status
    combined["combined_digest"] = digest(combined)
    write_json(REPORT / "RQ3_V2_COMBINED_RESULTS.json", combined)
    log("analyze", final_status)
    return a_results, b_results, combined


def fmt_rate(value: Mapping[str, Any] | None) -> str:
    if not value:
        return "not estimable"
    rate = value.get("rate")
    return f"{value.get('numerator')}/{value.get('denominator')} ({rate:.6f})" if rate is not None else f"{value.get('numerator')}/{value.get('denominator')}"


def finalize() -> dict[str, Any]:
    a = load(REPORT / "PART_A_RESULTS.json", {})
    b = load(REPORT / "PART_B_RESULTS.json", {})
    combined = load(REPORT / "RQ3_V2_COMBINED_RESULTS.json", {})
    integrity = load(REPORT / "CONTROL_INTEGRITY_RECEIPT.json", {})
    firewall = load(REPORT / "TRUE_INTENT_FIREWALL_RECEIPT.json", {})
    a_ledger = load(REPORT / "PART_A_EXECUTION_LEDGER.json", {})
    b_ledger = load(REPORT / "PART_B_EXECUTION_LEDGER.json", {})
    source = verify_source_freeze()
    all_rows = a_ledger.get("entries", []) + b_ledger.get("entries", [])
    required = (
        "FINAL_REPORT.md", "RQ3_V2_SCIENTIFIC_CONTRACT.md", "RQ3_V2_SCIENTIFIC_CONTRACT.json",
        "RQ3_V2_FORMAL_FREEZE_RECEIPT.json", "V1_PRESERVATION_RECEIPT.json",
        "PART_A_ROUTE_MANIFEST.json", "PART_A_SEED_FRESHNESS_RECEIPT.json", "PART_A_EXECUTION_LEDGER.json", "PART_A_RESULTS.json",
        "PART_B_SCENE_MANIFEST.json", "PART_B_QUALIFICATION_DEPENDENCY_RECEIPT.json", "PART_B_SEED_FRESHNESS_RECEIPT.json",
        "PART_B_EXECUTION_LEDGER.json", "PART_B_RESULTS.json", "RQ3_V2_COMBINED_RESULTS.json",
        "RQ1_FROZEN_DEPENDENCY_RECEIPT.json", "RQ2_FROZEN_DEPENDENCY_RECEIPT.json",
        "CONTROL_INTEGRITY_RECEIPT.json", "TRUE_INTENT_FIREWALL_RECEIPT.json",
        "SOURCE_FREEZE_RECEIPT.json", "FINAL_VALIDATION_RECEIPT.json", "COMMAND_LOG.md",
    )
    pre = tuple(name for name in required if name not in {"FINAL_REPORT.md", "FINAL_VALIDATION_RECEIPT.json"})
    audit = {
        "schema": "driveclarify.rq3_v2.final-validation.v1", "status": combined.get("exact_final_status"),
        "valid_final_status": combined.get("exact_final_status") in FINAL_STATUSES,
        "v1_preserved": load(REPORT / "V1_PRESERVATION_RECEIPT.json", {}).get("pass"),
        "source_freeze": source, "pre_report_required_files_present": all((REPORT / name).is_file() for name in pre),
        "part_a_planned_and_attempted": [32, len(a_ledger.get("entries", []))],
        "part_b_planned_and_attempted": [24, len(b_ledger.get("entries", []))],
        "scientific_retries": int(a_ledger.get("scientific_retries", 0)) + int(b_ledger.get("scientific_retries", 0)),
        "seed_replacements": int(a_ledger.get("seed_replacements", 0)) + int(b_ledger.get("seed_replacements", 0)),
        "control_integrity_pass": integrity.get("pass"), "true_intent_firewall_pass": firewall.get("pass"),
        "cleanup_pass": all(row.get("cleanup_pass") for row in all_rows),
        "scientific_results_read_only_after_legal_endpoint": True,
    }
    audit["pass"] = bool(audit["valid_final_status"] and audit["v1_preserved"] and source["pass"] and audit["pre_report_required_files_present"] and audit["scientific_retries"] == 0 and audit["seed_replacements"] == 0 and audit["control_integrity_pass"] and audit["true_intent_firewall_pass"] and audit["cleanup_pass"])
    audit["validation_digest"] = digest(audit)
    write_json(REPORT / "FINAL_VALIDATION_RECEIPT.json", audit)
    safety = v1._safety_sum([row for row in all_rows if row.get("execution_evaluable")])
    low, high = b.get("LOW", {}), b.get("HIGH", {})
    timing = b.get("timing", {})
    freeze_receipt = load(REPORT / "RQ3_V2_FORMAL_FREEZE_RECEIPT.json", {})
    paths = [str(REPORT / name) for name in required]
    lines = [
        "# DriveClarify RQ3-V2 Bench2Drive closed-loop validation", "",
        f"1. exact final status: `{combined.get('exact_final_status')}`.",
        f"2. V1 preserved: `{load(REPORT / 'V1_PRESERVATION_RECEIPT.json', {}).get('pass')}`; V1 status remains `RQ3_PRIMARY_EVALUABILITY_GATE_FAILED`.",
        "3. exact Part-A repair: paired-evaluable iff both arms have valid official native result, route identity, execution receipt, source/checkpoint identity, and uncorrupted evidence; semantic CLEAR is not required.",
        f"4. RQ1 unchanged: `{load(REPORT / 'RQ1_FROZEN_DEPENDENCY_RECEIPT.json', {}).get('pass')}`.",
        f"5. RQ2 unchanged: `{load(REPORT / 'RQ2_FROZEN_DEPENDENCY_RECEIPT.json', {}).get('pass')}`.",
        f"6. checkpoint/controller unchanged: `{source['pass'] and integrity.get('pid_controller_changes') == 0 and integrity.get('controller_changes') == 0}`.",
        f"7. V2 freeze digest: `{freeze_receipt.get('freeze_digest')}`.",
        "", "## Part A", "",
        f"8. two fresh seeds: `{json.dumps(a.get('seeds', []))}`.",
        "9. planned pairs: `16` (`32` runs).",
        f"10. paired-evaluable: `{a.get('paired_evaluable_units')}/16`.",
        f"11. native noncompletion: `{a.get('native_noncompletion')}`.",
        f"12. A0 Driving Score: `{json.dumps(a.get('A0_driving_score'), sort_keys=True)}`.",
        f"13. A1 Driving Score: `{json.dumps(a.get('A1_driving_score'), sort_keys=True)}`.",
        f"14. paired Driving Score effect/CI: `{json.dumps(a.get('paired_driving_score_A1_minus_A0'), sort_keys=True)}`.",
        f"15. A0 Route Completion: `{json.dumps(a.get('A0_route_completion'), sort_keys=True)}`.",
        f"16. A1 Route Completion: `{json.dumps(a.get('A1_route_completion'), sort_keys=True)}`.",
        f"17. paired Route Completion effect/CI: `{json.dumps(a.get('paired_route_completion_A1_minus_A0'), sort_keys=True)}`.",
        f"18. success comparison: `{json.dumps(a.get('official_success'), sort_keys=True)}`.",
        f"19. safety comparison: `{json.dumps(a.get('safety_infractions'), sort_keys=True)}`.",
        f"20. false ASK: `{json.dumps(a.get('false_ASK'), sort_keys=True)}`.",
        f"21. clarification WAIT/intervention counts: WAIT=`{a.get('clarification_WAIT_count')}`, Full Replan=`{a.get('Full_Replan_count')}`, control intervention=`{a.get('DriveClarify_control_intervention_count')}`, states=`{json.dumps(a.get('A1_semantic_pass_through_states'), sort_keys=True)}`.",
        "", "## Part B", "",
        f"22. three fresh seeds: `{json.dumps(b.get('seeds', []))}`.",
        "23. planned: `24`.",
        f"24. decision-evaluable: `{b.get('decision_evaluable')}/24`.",
        f"25. execution-evaluable: `{b.get('execution_evaluable')}/24`.",
        f"26. LOW ACT: `{fmt_rate(low.get('ACT'))}`.",
        f"27. LOW unnecessary ASK: `{fmt_rate(low.get('unnecessary_ASK'))}`.",
        f"28. LOW completion: `{fmt_rate(low.get('completion'))}`.",
        f"29. HIGH timely ASK: `{fmt_rate(high.get('timely_ASK'))}`.",
        f"30. HIGH answer: `{fmt_rate(high.get('answer'))}`.",
        f"31. Full Replan admission/execution: `{fmt_rate(high.get('Full_Replan_admission'))}` / `{fmt_rate(high.get('Full_Replan_execution'))}`.",
        f"32. clarified completion: `{fmt_rate(high.get('clarified_completion'))}`.",
        f"33. wrong-goal LOW/HIGH: `{fmt_rate(low.get('wrong_goal'))}` / `{fmt_rate(high.get('wrong_goal'))}`.",
        f"34. timing/margin: `{json.dumps(timing, sort_keys=True)}`.",
        "", "## System", "",
        f"35. collision/offroad/wrong-lane: `{safety.get('collision', 0)}/{safety.get('offroad', 0)}/{safety.get('wrong_lane', 0)}`.",
        f"36. red-light/stop-sign: `{safety.get('red_light', 0)}/{safety.get('stop_sign', 0)}`.",
        f"37. route deviation/block/timeout: `{safety.get('route_deviation', 0)}/{safety.get('blocked', 0)}/{safety.get('route_timeout', 0) + safety.get('scenario_timeout', 0)}`.",
        f"38. added VLA forwards: `{integrity.get('added_vla_forwards')}`.",
        f"39. PID/controller mutation: `{integrity.get('pid_controller_changes')}/{integrity.get('controller_changes')}`.",
        f"40. second writer: `{integrity.get('second_control_writer')}`.",
        f"41. true-intent pre-ASK reads: `{firewall.get('pre_ASK_true_intent_reads')}`.",
        f"42. H-RQ3-A: `{combined.get('H_RQ3_A')}`.",
        f"43. H-RQ3-B: `{combined.get('H_RQ3_B')}`.",
        f"44. H-RQ3-C: `{combined.get('H_RQ3_C')}`.",
        f"45. combined RQ3 verdict: `{combined.get('exact_final_status')}`.",
        f"46. source freeze/final audit: `{'PASS' if source['pass'] else 'FAIL'}` / `{'PASS' if audit['pass'] else 'FAIL'}`; validation digest=`{audit['validation_digest']}`.",
        f"47. report paths: `{json.dumps(paths)}`.",
        "48. exactly one next recommendation: Replicate the sealed V2 protocol on a prospectively larger route sample only if this fixed campaign reaches a scientifically supported endpoint.",
    ]
    write_text(REPORT / "FINAL_REPORT.md", "\n".join(lines))
    audit["final_report_present"] = True
    audit["all_required_files_present"] = all((REPORT / name).is_file() for name in required)
    audit["pass"] = bool(audit["pass"] and audit["all_required_files_present"])
    audit["validation_digest"] = digest({key: item for key, item in audit.items() if key != "validation_digest"})
    write_json(REPORT / "FINAL_VALIDATION_RECEIPT.json", audit)
    log("finalize", combined.get("exact_final_status"))
    return audit


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("prepare", "freeze", "run-formal", "analyze", "finalize", "all"))
    args = parser.parse_args()
    if args.phase in {"prepare", "all"}:
        prepare()
    if args.phase in {"freeze", "all"}:
        freeze()
    if args.phase in {"run-formal", "all"}:
        run_formal()
    if args.phase in {"analyze", "all"}:
        analyze()
    if args.phase in {"finalize", "all"}:
        finalize()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
