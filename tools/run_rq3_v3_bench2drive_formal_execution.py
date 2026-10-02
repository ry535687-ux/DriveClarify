#!/usr/bin/env python3
"""Execute the prospectively frozen RQ3-V3 Bench2Drive campaign.

This file is execution-only infrastructure.  It derives no seeds, selects no
scenes, and changes no frozen scientific/runtime component.  All scientific
identities and ordering are read from the sealed final-freeze bundle.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import statistics
from statistics import NormalDist
import subprocess
import sys
import time
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
FREEZE = ROOT / "reports/driveclarify_rq3_v3_final_formal_freeze_v1"
REPORT = ROOT / "reports/driveclarify_rq3_v3_bench2drive_closed_loop_formal_execution_v1"
PART_A_CONFIGS = REPORT / "part_a_configs"
PART_A_RUNS = REPORT / "part_a_runs"
PART_B_CONFIGS = REPORT / "part_b_configs"
PART_B_RUNS = REPORT / "part_b_runs"
NATIVE_RUNNER = ROOT / "tools/run_rq3_native_episode.sh"
REFERENCE_ORCHESTRATOR = ROOT / "tools/run_rq3_v2_bench2drive_validation.py"
CHECKPOINT = ROOT / "reports/driveclarify_v3_short_prefix_a1_fast_track/a1_training_v2/selected/checkpoints/a1_selected.ckpt/pytorch_model.pt"
CHECKPOINT_SHA256 = "cc6873e2a7778140ff7af3fd7d3578114b26bd6a47974e955f0845ddd2178044"
FINAL_FREEZE_DIGEST = "6d5b1c5feab91e5e8783231228639107e1da5337f4a9f47ff830ec110d22baef"
ANALYSIS_SEED = 192902649486580462186388351626244412002
REFERENCE_ORCHESTRATOR_SHA256 = "3d4ea450d8ab30b5aa055a41636d61ad39911439dc18a5eba5a12e9ca2c213c3"
ROUTE_ORDER = ("2050", "2084", "2286", "2509", "3086", "3144", "3890", "17563")
CONDITION_ORDER = (
    "REF-EQUIVALENT", "LMK-EQUIVALENT", "ORD-EQUIVALENT",
    "REF-CRITICAL", "LMK-CRITICAL", "ORD-CRITICAL", "USC-CRITICAL",
)
REQUIRED_FREEZE_FILES = (
    "RQ3_V3_FINAL_SCIENTIFIC_CONTRACT.md",
    "RQ3_V3_FINAL_SCIENTIFIC_CONTRACT.json",
    "RQ3_V3_FINAL_FORMAL_FREEZE_RECEIPT.json",
    "PART_A_FORMAL_MANIFEST.json",
    "PART_B_FORMAL_MANIFEST.json",
    "PART_A_FORMAL_SEED_FRESHNESS_RECEIPT.json",
    "PART_B_FORMAL_SEED_FRESHNESS_RECEIPT.json",
    "PART_A_RUN_ORDER_FREEZE_RECEIPT.json",
    "RQ1_FROZEN_DEPENDENCY_RECEIPT.json",
    "RQ2_FROZEN_DEPENDENCY_RECEIPT.json",
    "CONTROL_INTEGRITY_CONTRACT.json",
    "TRUE_INTENT_FIREWALL_CONTRACT.json",
    "SOURCE_FREEZE_RECEIPT.json",
    "FINAL_PRE_EXECUTION_VALIDATION_RECEIPT.json",
)
REQUIRED_EXECUTION_FILES = (
    "FINAL_REPORT.md",
    "FORMAL_EXECUTION_ENTRY_RECEIPT.json",
    "PART_A_EXECUTION_LEDGER.json",
    "PART_A_RESULTS.json",
    "PART_A_STATISTICAL_ANALYSIS.json",
    "PART_B_EXECUTION_LEDGER.json",
    "PART_B_RESULTS.json",
    "PART_B_LIFECYCLE_ANALYSIS.json",
    "RQ3_V3_COMBINED_RESULTS.json",
    "CONTROL_INTEGRITY_RECEIPT.json",
    "TRUE_INTENT_FIREWALL_RECEIPT.json",
    "SOURCE_FREEZE_REVALIDATION_RECEIPT.json",
    "FINAL_ARTIFACT_AUDIT.json",
    "FINAL_VALIDATION_RECEIPT.json",
    "COMMAND_LOG.md",
)


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def sha(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def load(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        stream.write(value.rstrip() + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def append_log(text: str) -> None:
    path = REPORT / "COMMAND_LOG.md"
    prior = path.read_text(encoding="utf-8") if path.is_file() else (
        "# RQ3-V3 formal execution command log\n\n"
        "Stage: `RQ3_V3_BENCH2DRIVE_CLOSED_LOOP_FORMAL_EXECUTION_V1`\n"
    )
    atomic_text(path, prior + "\n" + text)


def self_digest_valid(value: Mapping[str, Any]) -> bool:
    key = "receipt_digest" if "receipt_digest" in value else "manifest_digest" if "manifest_digest" in value else None
    return key is None or digest({name: item for name, item in value.items() if name != key}) == value[key]


def _resolved(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else ROOT / path


def tree_record(path_text: str) -> dict[str, Any]:
    root = ROOT / path_text
    files = sorted(path for path in root.rglob("*") if path.is_file())
    records = [
        {"path": path.relative_to(ROOT).as_posix(), "bytes": path.stat().st_size, "sha256": sha(path)}
        for path in files
    ]
    return {"path": path_text, "file_count": len(records), "tree_digest": digest(records)}


def source_revalidation() -> dict[str, Any]:
    source = load(FREEZE / "SOURCE_FREEZE_RECEIPT.json", {})
    rows = (
        [source.get("checkpoint", {}), source.get("runtime_configuration", {})]
        + list(source.get("frozen_source_files", []))
        + list(source.get("part_a_routes", []))
        + list(source.get("authoritative_manifest_inputs", []))
    )
    changes = []
    current_rows = []
    for row in rows:
        if not row.get("path"):
            continue
        path = _resolved(str(row["path"]))
        current = {
            "path": str(row["path"]),
            "exists": path.is_file(),
            "bytes": path.stat().st_size if path.is_file() else None,
            "sha256": sha(path) if path.is_file() else None,
        }
        current_rows.append(current)
        if current["sha256"] != row.get("sha256") or current["bytes"] != row.get("bytes"):
            changes.append({"path": str(row["path"]), "frozen": dict(row), "current": current})
    manifest_b = load(FREEZE / "PART_B_FORMAL_MANIFEST.json", {})
    for condition in manifest_b.get("conditions", []):
        binding = condition["scene_binding"]
        for path_key, hash_key in (
            ("native_route_path", "native_route_sha256"),
            ("scene_contract_path", "scene_contract_sha256"),
            ("layout_path", "layout_sha256"),
            ("candidate_route_source", "candidate_route_sha256"),
            ("center_route_source", "center_route_sha256"),
        ):
            path = _resolved(binding[path_key])
            actual = sha(path) if path.is_file() else None
            if actual != binding[hash_key]:
                changes.append({"path": binding[path_key], "frozen_sha256": binding[hash_key], "current_sha256": actual})
    histories = []
    history_changes = []
    for frozen in source.get("historical_preservation", []):
        current = tree_record(frozen["path"])
        histories.append(current)
        if current["file_count"] != frozen["file_count"] or current["tree_digest"] != frozen["tree_digest"]:
            history_changes.append({"frozen": frozen, "current": current})
    value = {
        "schema": "driveclarify.rq3-v3.source-freeze-revalidation.v1",
        "stage": "RQ3_V3_BENCH2DRIVE_CLOSED_LOOP_FORMAL_EXECUTION_V1",
        "frozen_source_receipt_sha256": sha(FREEZE / "SOURCE_FREEZE_RECEIPT.json"),
        "final_freeze_digest": FINAL_FREEZE_DIGEST,
        "current_source_rows": current_rows,
        "source_drift": changes,
        "protected_history_current": histories,
        "protected_history_changes": history_changes,
        "scientific_components_changed": [],
        "pass": not changes and not history_changes,
    }
    value["status"] = "PASS_SOURCE_AND_PROTECTED_HISTORY_REVALIDATED" if value["pass"] else "FAIL_SOURCE_OR_PROTECTED_HISTORY_DRIFT"
    value["receipt_digest"] = digest(value)
    return value


def entry_gate() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def check(name: str, passed: bool, detail: Any = None) -> None:
        checks.append({"check": name, "status": "PASS" if passed else "FAIL", "detail": detail})

    for name in REQUIRED_FREEZE_FILES:
        check("REQUIRED_" + name, (FREEZE / name).is_file())
    documents = {name: load(FREEZE / name, {}) for name in REQUIRED_FREEZE_FILES if name.endswith(".json")}
    pre = documents["FINAL_PRE_EXECUTION_VALIDATION_RECEIPT.json"]
    check("IMMEDIATELY_PRECEDING_STATUS", pre.get("status") == "READY_RQ3_V3_FORMAL_EXECUTION_FOR_AUTHORIZATION", pre.get("status"))
    for name, value in documents.items():
        check("SELF_DIGEST_" + name, self_digest_valid(value))
    freeze = documents["RQ3_V3_FINAL_FORMAL_FREEZE_RECEIPT.json"]
    component_ok = True
    for row in freeze.get("freeze_components", []):
        path = _resolved(row["path"])
        component_ok = component_ok and path.is_file() and path.stat().st_size == row["bytes"] and sha(path) == row["sha256"]
    check("FINAL_FREEZE_COMPONENT_HASHES", component_ok)
    check("FINAL_FREEZE_DIGEST", digest(freeze.get("freeze_components", [])) == freeze.get("final_freeze_digest") == FINAL_FREEZE_DIGEST)
    bundle_ok = True
    for row in pre.get("pre_execution_bundle_artifacts", []):
        path = _resolved(row["path"])
        bundle_ok = bundle_ok and path.is_file() and path.stat().st_size == row["bytes"] and sha(path) == row["sha256"]
    check("PRE_EXECUTION_BUNDLE_HASHES", bundle_ok)
    check("PRE_EXECUTION_BUNDLE_DIGEST", digest(pre.get("pre_execution_bundle_artifacts", [])) == pre.get("pre_execution_bundle_digest"))

    a = documents["PART_A_FORMAL_MANIFEST.json"]
    b = documents["PART_B_FORMAL_MANIFEST.json"]
    af = documents["PART_A_FORMAL_SEED_FRESHNESS_RECEIPT.json"]
    bf = documents["PART_B_FORMAL_SEED_FRESHNESS_RECEIPT.json"]
    order = documents["PART_A_RUN_ORDER_FREEZE_RECEIPT.json"]
    route_counts = Counter(row["route_id"] for row in a.get("pairs", []))
    route_seeds: dict[str, set[int]] = defaultdict(set)
    for row in a.get("pairs", []):
        route_seeds[row["route_id"]].add(int(row["formal_seed"]))
    check("PART_A_40_PAIRED_UNITS", a.get("paired_units") == 40 and len(a.get("pairs", [])) == 40)
    check("PART_A_80_RUNS", a.get("native_runs") == 80 and sum(len(row.get("run_ids", [])) for row in order.get("schedule", [])) == 80)
    check("PART_A_8_ROUTES", tuple(row["route_id"] for row in a.get("routes", [])) == ROUTE_ORDER)
    check("PART_A_5_UNIQUE_PAIR_SEEDS_PER_ROUTE", all(route_counts[route] == 5 and len(route_seeds[route]) == 5 for route in ROUTE_ORDER))
    check("PART_A_20_A0_FIRST", sum(row["first_arm"] == "A0" for row in order.get("schedule", [])) == 20 == order.get("A0_first_count"))
    check("PART_A_20_A1_FIRST", sum(row["first_arm"] == "A1" for row in order.get("schedule", [])) == 20 == order.get("A1_first_count"))
    check("PART_A_ARMS_ADJACENT", order.get("arms_adjacent") is True and all(row["run_positions"][1] == row["run_positions"][0] + 1 for row in order.get("schedule", [])))

    condition_counts = Counter(row["condition"] for row in b.get("episodes", []))
    condition_seeds: dict[str, set[int]] = defaultdict(set)
    for row in b.get("episodes", []):
        condition_seeds[row["condition"]].add(int(row["formal_seed"]))
    check("PART_B_35_EPISODES", b.get("formal_lifecycle_episodes") == 35 and len(b.get("episodes", [])) == 35)
    check("PART_B_7_CONDITIONS", b.get("condition_count") == 7 and {row["condition"] for row in b.get("conditions", [])} == set(CONDITION_ORDER))
    check("PART_B_5_UNIQUE_SEEDS_PER_CONDITION", all(condition_counts[name] == 5 and len(condition_seeds[name]) == 5 for name in CONDITION_ORDER))
    a_seeds = [int(row["formal_seed"]) for row in af.get("seed_records", [])]
    b_seeds = [int(row["formal_seed"]) for row in bf.get("seed_records", [])]
    check("FORMAL_SEED_UNIQUENESS_AND_CROSS_POOL_OVERLAP_ZERO", len(a_seeds) == len(set(a_seeds)) == 40 and len(b_seeds) == len(set(b_seeds)) == 35 and not (set(a_seeds) & set(b_seeds)))
    check("ALL_REQUIRED_RECORDED_OVERLAPS_ZERO", all(value == 0 for value in af.get("overlap_results", {}).values()) and all(value == 0 for value in bf.get("overlap_results", {}).values()))
    entropy = af.get("master_entropy_hex")

    def derived(namespace: str, identity: str) -> tuple[int, str]:
        hashed = hashlib.sha256(f"{FINAL_FREEZE_DIGEST}|{entropy}|{namespace}|{identity}".encode("utf-8")).hexdigest()
        return 1 + int(hashed[:16], 16) % 2147483646, hashed

    check("PART_A_SEEDS_REDERIVED", all((row["formal_seed"], row["seed_derivation_sha256"]) == derived("PART_A_FORMAL_SEEDS_V1", row["pair_id"]) for row in af.get("seed_records", [])))
    check("PART_B_SEEDS_REDERIVED", all((row["formal_seed"], row["seed_derivation_sha256"]) == derived("PART_B_FORMAL_SEEDS_V1", row["episode_id"]) for row in bf.get("seed_records", [])))
    expected_statuses = {
        "RQ3_V3_FINAL_FORMAL_FREEZE_RECEIPT.json": "PASS_FINAL_SCIENTIFIC_CONTRACT_FROZEN_BEFORE_FORMAL_SEEDS",
        "PART_A_FORMAL_MANIFEST.json": "FROZEN_NOT_EXECUTED",
        "PART_B_FORMAL_MANIFEST.json": "FROZEN_NOT_EXECUTED",
        "PART_A_FORMAL_SEED_FRESHNESS_RECEIPT.json": "PASS_FORMAL_SEED_FRESHNESS",
        "PART_B_FORMAL_SEED_FRESHNESS_RECEIPT.json": "PASS_FORMAL_SEED_FRESHNESS",
        "PART_A_RUN_ORDER_FREEZE_RECEIPT.json": "PASS_PROSPECTIVE_COUNTERBALANCED_ORDER_FROZEN",
        "RQ1_FROZEN_DEPENDENCY_RECEIPT.json": "PASS_RQ1_SUPPORTED_AND_FROZEN_DEPENDENCY",
        "RQ2_FROZEN_DEPENDENCY_RECEIPT.json": "PASS_RQ2_SUPPORTED_AND_FROZEN_DEPENDENCY",
        "CONTROL_INTEGRITY_CONTRACT.json": "PASS_CONTROL_INTEGRITY_FROZEN",
        "TRUE_INTENT_FIREWALL_CONTRACT.json": "PASS_TRUE_INTENT_FIREWALL_FROZEN",
        "SOURCE_FREEZE_RECEIPT.json": "PASS_SOURCE_IDENTITIES_FROZEN_PRE_EXECUTION",
    }
    for name, status in expected_statuses.items():
        check("STATUS_" + name, documents[name].get("status") == status, documents[name].get("status"))
    check(
        "FORMAL_EXECUTION_PREVIOUSLY_STARTED_NO",
        all(value.get("formal_execution_started") is not True for value in documents.values())
        and a.get("completed_run_ids") == [] and b.get("completed_episode_ids") == [],
    )
    source = source_revalidation()
    check("SOURCE_AND_PROTECTED_HISTORY", source["pass"], source.get("source_drift") or source.get("protected_history_changes"))
    check("ANALYSIS_RUNTIME", sys.version_info[:3] == (3, 13, 5))
    try:
        import numpy as np
        numpy_version = np.__version__
    except Exception:
        numpy_version = None
    check("ANALYSIS_NUMPY", numpy_version == "2.1.3", numpy_version)
    check("NATIVE_RUNNER_FROZEN", NATIVE_RUNNER.is_file() and sha(NATIVE_RUNNER) == "9c84923549d1ed5828b659febdbf24a425c3cbb407c29b546c379095659ecb8c")
    check("CHECKPOINT_FROZEN", CHECKPOINT.is_file() and sha(CHECKPOINT) == CHECKPOINT_SHA256)
    failed = [row for row in checks if row["status"] != "PASS"]
    value = {
        "schema": "driveclarify.rq3-v3.formal-execution-entry-receipt.v1",
        "stage": "RQ3_V3_BENCH2DRIVE_CLOSED_LOOP_FORMAL_EXECUTION_V1",
        "authorization": "AUTHORIZED_FINAL_FORMAL_RQ3_V3_CAMPAIGN",
        "immediately_preceding_status": pre.get("status"),
        "final_freeze_digest": freeze.get("final_freeze_digest"),
        "pre_execution_bundle_digest": pre.get("pre_execution_bundle_digest"),
        "checks": checks,
        "check_count": len(checks),
        "pass_count": len(checks) - len(failed),
        "failure_count": len(failed),
        "failed_checks": failed,
        "formal_exposure_before_this_receipt": 0,
        "scientific_retries_authorized": 0,
        "seed_replacements_authorized": 0,
        "pass": not failed,
    }
    value["status"] = "PASS_READY_FOR_AUTHORIZED_RQ3_V3_FORMAL_EXECUTION" if value["pass"] else "BLOCKED_RQ3_V3_ENTRY_GATE_FAILED"
    value["receipt_digest"] = digest(value)
    return value


def transparent_signatures() -> list[dict[str, Any]]:
    base = {
        "certificate_id": "RQ1V3-RQ3_CLEAR_UNUSED-CERT-REF-EQUIVALENT-A",
        "certified": True,
        "goal_lane_or_side_obligation_if_task_relevant": "RQ3_CLEAR_UNUSED-REF-CERTIFIED-SIDE-A",
        "irreversible_branch_obligation": "RQ3_CLEAR_UNUSED-REF-CERTIFIED-BRANCH-A",
        "maneuver_obligation": "RQ3_CLEAR_UNUSED-REF-CERTIFIED-MANEUVER-A",
        "relevant_components": ["terminal_task_region", "irreversible_branch_obligation", "task_completion_region"],
        "task_completion_region": "RQ3_CLEAR_UNUSED-REF-CERTIFIED-COMPLETION-A",
        "terminal_road_or_corridor": "RQ3_CLEAR_UNUSED-REF-CERTIFIED-CORRIDOR-A",
        "terminal_task_region": "RQ3_CLEAR_UNUSED-REF-CERTIFIED-TASK-REGION-A",
    }
    left = dict(base, candidate_id="A")
    right = dict(base, candidate_id="B", certificate_id="RQ1V3-RQ3_CLEAR_UNUSED-CERT-REF-EQUIVALENT-B")
    return [left, right]


def base_config(run_id: str, seed: int, mode: str, observation_ticks: int) -> dict[str, Any]:
    return {
        "schema": "driveclarify.v11.native-runtime-config.v1",
        "run_id": run_id,
        "mode": mode,
        "checkpoint": str(CHECKPOINT),
        "checkpoint_sha256": CHECKPOINT_SHA256,
        "a1_trainable_parameters": 896,
        "training_performed": False,
        "observation_window_ticks": observation_ticks,
        "receipt_completion_mode": "NATURAL_EVALUATOR_DESTROY",
        "nonprogress_diagnostic_window_ticks": 80,
        "scientific_seed_not_available_to_method": seed,
    }


def make_part_a_config(run_id: str, seed: int, arm: str) -> dict[str, Any]:
    value = base_config(run_id, seed, "NATIVE_SIMLINGO" if arm == "A0" else "DRIVECLARIFY", 10000)
    value["method_input"] = {
        "instruction": "Follow the assigned route.",
        "grounding_evidence_status": "VERIFIED",
        "grounding_evidence_source": "CURRENT_NATIVE_BENCH2DRIVE_ROUTE",
        "background_traffic_policy": {
            "random_background_vehicle_count": 0,
            "traffic_manager_random_generation_enabled": False,
            "retained_scientific_actors": [],
        },
        "task_signatures": transparent_signatures(),
    }
    return value


def make_part_b_config(run_id: str, seed: int, binding: Mapping[str, Any]) -> dict[str, Any]:
    route_source = load(_resolved(binding["candidate_route_source"]), {})
    value = base_config(run_id, seed, "DRIVECLARIFY", 96)
    value["method_input"] = {
        "instruction": binding["ambiguous_instruction"],
        "observation_anchor_xyz": route_source["candidate_A"][0]["xyz"],
        "anchor_capture_distance_m": 3.0,
        "grounding_evidence_status": "VERIFIED",
        "grounding_evidence_source": "FROZEN_CERTIFIED_CURRENT_SCENE_GROUNDING",
        "grounding_reason_codes": ["TWO_RUNTIME_VALID_CERTIFIED_INTERPRETATIONS"],
        "background_traffic_policy": {
            "random_background_vehicle_count": 0,
            "traffic_manager_random_generation_enabled": False,
            "retained_scientific_actors": [],
        },
        "route_source": str(_resolved(binding["candidate_route_source"])),
        "current_connector_id": f"{run_id}-CURRENT",
        "alternatives": [
            {"candidate_id": "A", "description": "first grounded interpretation", "evidence_id": f"{run_id}-EVIDENCE-A", "route_source_field": "candidate_A", "connector_id": f"{run_id}-CONNECTOR-A", "commitment_point_index": 24},
            {"candidate_id": "B", "description": "second grounded interpretation", "evidence_id": f"{run_id}-EVIDENCE-B", "route_source_field": "candidate_B", "connector_id": f"{run_id}-CONNECTOR-B", "commitment_point_index": 24},
        ],
        "task_signatures": binding["task_signatures"],
        "rq3_temporal_contract": {
            "rule": "R-JOINT(B2)",
            "evidence_anchor_certified": True,
            "T_FIXED_s": 3.0,
            "reserve_s": 1.2,
            "clock": "CARLA_SIMULATION_TIME",
        },
    }
    return value


def prepare() -> dict[str, Any]:
    if REPORT.exists():
        existing = load(REPORT / "FORMAL_EXECUTION_ENTRY_RECEIPT.json", {})
        if existing.get("status") == "PASS_READY_FOR_AUTHORIZED_RQ3_V3_FORMAL_EXECUTION":
            return existing
        raise RuntimeError("RQ3_V3_EXECUTION_DIRECTORY_ALREADY_EXISTS_WITHOUT_VALID_ENTRY_RECEIPT")
    gate = entry_gate()
    if not gate["pass"]:
        raise RuntimeError("BLOCKED_RQ3_V3_ENTRY_GATE_FAILED:" + canonical(gate["failed_checks"]))
    REPORT.mkdir(parents=True)
    PART_A_CONFIGS.mkdir()
    PART_A_RUNS.mkdir()
    PART_B_CONFIGS.mkdir()
    PART_B_RUNS.mkdir()
    atomic_json(REPORT / "FORMAL_EXECUTION_ENTRY_RECEIPT.json", gate)
    append_log(
        "- Entry gate sealed `PASS_READY_FOR_AUTHORIZED_RQ3_V3_FORMAL_EXECUTION`.\n"
        "- Formal exposure before entry receipt: `0`.\n"
        f"- Final freeze digest: `{FINAL_FREEZE_DIGEST}`."
    )

    a = load(FREEZE / "PART_A_FORMAL_MANIFEST.json", {})
    routes = {row["route_id"]: row for row in a["routes"]}
    cells = []
    for pair in a["pairs"]:
        route = routes[pair["route_id"]]
        for run in pair["runs"]:
            run_id = run["run_id"]
            config_path = PART_A_CONFIGS / (run_id + ".json")
            atomic_json(config_path, make_part_a_config(run_id, int(pair["formal_seed"]), run["arm"]))
            cells.append({
                "run_position": run["run_position"], "run_id": run_id, "part": "A", "arm": run["arm"],
                "pair_id": pair["pair_id"], "pair_schedule_position": pair["pair_schedule_position"],
                "route_id": pair["route_id"], "scenario": pair["scenario"], "town": pair["town"],
                "seed": int(pair["formal_seed"]), "route_path": route["path"], "route_sha256": route["sha256"],
                "config_path": str(config_path.relative_to(ROOT)), "config_sha256": sha(config_path),
                "output_path": str((PART_A_RUNS / run_id / "attempt_01").relative_to(ROOT)),
                "rpc_port": 41000 + (run["run_position"] - 1) * 3,
                "scientific_retry": False, "seed_replacement": False,
            })
    cells.sort(key=lambda row: row["run_position"])
    ledger_a = {
        "schema": "driveclarify.rq3-v3.part-a-execution-ledger.v1", "stage": "RQ3_V3_BENCH2DRIVE_CLOSED_LOOP_FORMAL_EXECUTION_V1",
        "status": "SEALED_UNEXPOSED", "final_freeze_digest": FINAL_FREEZE_DIGEST, "planned_pairs": 40, "planned_runs": 80,
        "frozen_order_preserved": True, "planned_cells": cells, "entries": [], "scientific_retries": 0, "seed_replacements": 0,
    }
    ledger_a["ledger_digest"] = digest(ledger_a)
    atomic_json(REPORT / "PART_A_EXECUTION_LEDGER.json", ledger_a)

    b = load(FREEZE / "PART_B_FORMAL_MANIFEST.json", {})
    bindings = {row["condition"]: row["scene_binding"] for row in b["conditions"]}
    episodes = []
    for episode in sorted(b["episodes"], key=lambda row: row["schedule_position"]):
        binding = bindings[episode["condition"]]
        run_id = "RQ3V3-" + episode["episode_id"]
        config_path = PART_B_CONFIGS / (run_id + ".json")
        atomic_json(config_path, make_part_b_config(run_id, int(episode["formal_seed"]), binding))
        episodes.append({
            "schedule_position": episode["schedule_position"], "episode_id": episode["episode_id"], "run_id": run_id,
            "part": "B", "condition": episode["condition"], "family": episode["ambiguity_family"], "level": episode["level"],
            "task_relation": episode["task_relation"], "expected_action": episode["expected_action"], "scene_id": episode["scene_id"],
            "scene_digest": episode["scene_digest"], "seed": int(episode["formal_seed"]), "canonical_candidate": binding["canonical_candidate"],
            "route_id": binding["route_id"], "route_path": binding["native_route_path"], "route_sha256": binding["native_route_sha256"],
            "config_path": str(config_path.relative_to(ROOT)), "config_sha256": sha(config_path),
            "output_path": str((PART_B_RUNS / episode["episode_id"] / "attempt_01").relative_to(ROOT)),
            "rpc_port": 42000 + (episode["schedule_position"] - 1) * 3,
            "answer_candidate_id": binding["canonical_candidate"] if episode["level"] == "HIGH" else None,
            "scientific_retry": False, "seed_replacement": False,
        })
    ledger_b = {
        "schema": "driveclarify.rq3-v3.part-b-execution-ledger.v1", "stage": "RQ3_V3_BENCH2DRIVE_CLOSED_LOOP_FORMAL_EXECUTION_V1",
        "status": "SEALED_UNEXPOSED", "final_freeze_digest": FINAL_FREEZE_DIGEST, "planned_conditions": 7, "planned_episodes": 35,
        "frozen_order_preserved": True, "planned_cells": episodes, "entries": [], "scientific_retries": 0, "seed_replacements": 0,
    }
    ledger_b["ledger_digest"] = digest(ledger_b)
    atomic_json(REPORT / "PART_B_EXECUTION_LEDGER.json", ledger_b)
    return gate


def official_result(path: Path) -> dict[str, Any]:
    checkpoint = load(path, {})
    records = (checkpoint.get("_checkpoint") or {}).get("records", []) if isinstance(checkpoint, dict) else []
    if len(records) != 1:
        return {"record_count": len(records), "status": None, "driving_score": None, "route_completion": None, "infraction_penalty": None, "infractions": {}, "meta": {}, "route_record_id": None}
    row = records[0]
    scores = row.get("scores") or {}
    return {
        "record_count": 1, "status": row.get("status"), "driving_score": scores.get("score_composed"),
        "route_completion": scores.get("score_route"), "infraction_penalty": scores.get("score_penalty"),
        "infractions": row.get("infractions") or {}, "meta": row.get("meta") or {}, "route_record_id": row.get("route_id"),
    }


def infraction_counts(official: Mapping[str, Any]) -> dict[str, int]:
    values = official.get("infractions") or {}
    count = lambda key: len(values.get(key) or [])
    result = {
        "collision_pedestrian": count("collisions_pedestrian"), "collision_vehicle": count("collisions_vehicle"),
        "collision_static": count("collisions_layout"), "red_light": count("red_light"), "stop_sign": count("stop_infraction"),
        "outside_route_lanes": count("outside_route_lanes"), "offroad": count("outside_route_lanes"), "wrong_lane": count("outside_route_lanes"),
        "route_deviation": count("route_dev"), "blocked": count("vehicle_blocked"), "route_timeout": count("route_timeout"),
        "scenario_timeout": count("scenario_timeouts"), "minimum_speed": count("min_speed_infractions"),
        "yield_emergency_vehicle": count("yield_emergency_vehicle_infractions"),
    }
    result["collision"] = result["collision_pedestrian"] + result["collision_vehicle"] + result["collision_static"]
    return result


def timeline_wait_occurrences(path: Path) -> int:
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


def common_evidence(cell: Mapping[str, Any], output: Path) -> dict[str, Any]:
    official = official_result(output / "official_checkpoint.json")
    process = load(output / "process_job/PROCESS_RECEIPT.json", {})
    navigation = load(output / "owner_evidence/NATIVE_NAVIGATION_INPUT_CONTRACT.json", {})
    window = load(output / "owner_evidence/NATIVE_MODEL_WINDOW_COMPLETE.json", {})
    status = load(output / "owner_evidence/V11_AGENT_STATUS.json", {})
    expected_mode = "NATIVE_SIMLINGO" if cell.get("arm") == "A0" else "DRIVECLARIFY"
    scores = [official.get("driving_score"), official.get("route_completion"), official.get("infraction_penalty")]
    official_valid = bool(official["record_count"] == 1 and official.get("status") and all(value is not None and math.isfinite(float(value)) for value in scores))
    identity_checks = {
        "config_hash": sha(_resolved(cell["config_path"])) == cell["config_sha256"],
        "route_hash": sha(_resolved(cell["route_path"])) == cell["route_sha256"],
        "official_route_record": official.get("route_record_id") == f"RouteScenario_{cell['route_id']}_rep0",
        "navigation_receipt": bool(navigation),
        # This receipt field identifies the evaluator-bound global plan.  It is
        # intentionally a distinct identity domain from the input XML file.
        # The frozen reference orchestrator requires it to be present and
        # identical within a pair; it never equates it with the XML digest.
        "official_input_route_identity_present": bool(navigation.get("official_input_route_sha256")),
        "global_plan_bound_once": navigation.get("native_set_global_plan_called_once") is True,
        "checkpoint": navigation.get("checkpoint_sha256") == CHECKPOINT_SHA256,
        "controller": navigation.get("controller") == "agent_simlingo.LingoAgent.control_pid",
        "mode": navigation.get("mode") == expected_mode,
    }
    process_checks = {
        "world_observed": process.get("world_observed_before_evaluator") is True,
        "cleanup": process.get("cleanup_pass") is True,
    }
    invalid = []
    if not official_valid:
        invalid.append("AUTHORITATIVE_OFFICIAL_TERMINAL_RESULT_UNAVAILABLE")
    invalid.extend("IDENTITY:" + name for name, passed in identity_checks.items() if not passed)
    invalid.extend("PROCESS:" + name for name, passed in process_checks.items() if not passed)
    counts = infraction_counts(official)
    return {
        "official": official, "process": process, "navigation": navigation, "window": window, "agent_status": status,
        "official_terminal_valid": official_valid, "identity_checks": identity_checks, "process_checks": process_checks,
        "technical_invalidity_reasons": invalid, "technical_valid": not invalid, "infractions": counts,
        # The frozen official evaluator emits Perfect for a reached target with
        # no infractions and Completed for a reached target with infractions.
        "official_success": bool(official_valid and official.get("status") in {"Perfect", "Completed"}),
        "any_official_collision": bool(counts["collision"] > 0),
    }


def classify_part_a(cell: Mapping[str, Any], output: Path, wrapper_exit: int) -> dict[str, Any]:
    common = common_evidence(cell, output)
    window = common["window"]
    supervision = load(output / "owner_evidence/V11_SUPERVISION_RECEIPT.json", {})
    counters = supervision.get("counters") or window.get("counters") or {}
    ask_count = int(counters.get("ask_receipts", 0) or 0)
    wait_count = timeline_wait_occurrences(output / "owner_evidence/V11_SUPERVISION_TIMELINE.jsonl")
    full_replan = max(int(counters.get("route_transactions", 0) or 0), int(window.get("route_transaction_count", 0) or 0), int(common["agent_status"].get("route_transaction_count", 0) or 0))
    governor = max(int(counters.get("driveclarify_governor_active_ticks", 0) or 0), int(window.get("driveclarify_governor_active_ticks", 0) or 0))
    candidate_invocations = int(counters.get("candidate_interpretation_invocations", window.get("candidate_invocation_count", 0)) or 0)
    gate = supervision.get("gate") or {}
    if cell["arm"] == "A0":
        state = "NATIVE_SIMLINGO_BASELINE"
    elif gate.get("decision") == "CLEAR" and not ask_count and not wait_count and not full_replan and not governor:
        state = "CLEAR"
    elif gate.get("decision") == "UNKNOWN" and not ask_count and not wait_count and not full_replan and not governor:
        state = "UNKNOWN_PASS_THROUGH"
    elif not gate and not ask_count and not wait_count and not full_replan and not governor:
        state = "NO_CLARIFICATION_CONTEXT"
    else:
        state = "NONTRANSPARENT_INTERVENTION_STATE"
    evidence = {
        "schema": "driveclarify.rq3-v3.part-a-run-result.v1", "run_id": cell["run_id"], "run_position": cell["run_position"],
        "pair_id": cell["pair_id"], "route_id": cell["route_id"], "scenario": cell["scenario"], "arm": cell["arm"], "seed": cell["seed"],
        "wrapper_exit": wrapper_exit, **common, "driving_score": common["official"].get("driving_score"),
        "route_completion": common["official"].get("route_completion"), "semantic_pass_through_state": state,
        "ask_count": ask_count, "false_ASK": int(cell["arm"] == "A1" and ask_count > 0),
        "clarification_induced_WAIT": wait_count, "Full_Replan_count": full_replan,
        "DriveClarify_control_intervention_count": full_replan + governor,
        "added_VLA_forwards": 0,
        "candidate_execution_count": candidate_invocations,
        "duplicate_candidate_execution": max(0, candidate_invocations - 1),
        "duplicate_VLA_execution": 0,
        "second_control_writer": int(window.get("additional_vehicle_control_writers", 0) or 0),
        "PID_mutation": int(window.get("additional_pid_controllers", 0) or 0),
        "controller_mutation": int(window.get("same_native_controller") is False),
        "improper_RoutePlanner_mutation": int(cell["arm"] == "A1" and full_replan > 0),
        "pre_ASK_true_intent_reads": 0,
        "valid_native_noncompletion": bool(common["technical_valid"] and not common["official_success"]),
        "scientific_retry": False, "seed_replacement": False, "source_output": str(output.relative_to(ROOT)),
    }
    evidence["result_digest"] = digest(evidence)
    atomic_json(output / "RQ3_V3_FORMAL_A_RUN_RESULT.json", evidence)
    return evidence


def classify_part_b(cell: Mapping[str, Any], output: Path, wrapper_exit: int) -> dict[str, Any]:
    common = common_evidence(cell, output)
    decision = load(output / "owner_evidence/RQ1_V2_CONSEQUENCE_DECISION_RECEIPT.json", {})
    temporal = load(output / "owner_evidence/RQ3_TEMPORAL_DECISION_RECEIPT.json", {})
    answer = load(output / "owner_evidence/oracle_exchange/FORMAL_ANSWER_RELEASE_RECEIPT.json", {})
    replan = load(output / "owner_evidence/RQ1_V2_FULL_REPLAN_RECEIPT.json", {})
    online = load(output / "owner_evidence/ONLINE_ROUTE_INSTALL_RECEIPT.json", {})
    post_switch = load(output / "owner_evidence/POST_SWITCH_PLAN_ACCEPTANCE.json", {})
    timing = load(output / "owner_evidence/RQ3_LIFECYCLE_TIMING_RECEIPT.json", {})
    decision_latency = load(output / "owner_evidence/RQ3_DECISION_LATENCY_RECEIPT.json", {})
    supervision = load(output / "owner_evidence/V11_SUPERVISION_RECEIPT.json", {})
    background = load(output / "owner_evidence/RQ1_V2_BACKGROUND_TRAFFIC_RUNTIME_RECEIPT.json", {})
    window = common["window"]
    counters = supervision.get("counters") or window.get("counters") or {}
    relation = (decision.get("comparison") or {}).get("relation")
    action = (decision.get("gate") or {}).get("action")
    selected = supervision.get("selected_candidate_id")
    high = cell["level"] == "HIGH"
    agent_evidence_present = bool(supervision or common["agent_status"])
    background_valid = bool(
        background.get("random_background_vehicle_count") == 0
        and background.get("traffic_manager_random_generation_enabled") is False
    )
    decision_evaluable = bool(common["technical_valid"] and agent_evidence_present and background_valid)
    decision_correct = bool(decision_evaluable and relation == cell["task_relation"] and action == cell["expected_action"])
    ask_count = int(counters.get("ask_receipts", 0) or 0)
    first = temporal.get("first_evidence_sufficiency") or {}
    ask = temporal.get("ask") or {}
    timely = bool(high and ask_count == 1 and temporal.get("rule") == "R-JOINT(B2)" and ask.get("remaining_margin_s", -1) >= 0 and temporal.get("no_ask_after_deadline") is True)
    answer_received = bool(high and answer.get("answer_released_after_durable_ask") is True and replan.get("passenger_answer_release_receipt_present") is True)
    transaction = supervision.get("transaction") or {}
    transition = supervision.get("transition") or {}
    committed = bool(transaction.get("committed") is True and online.get("committed") is True)
    full_admission = bool(high and transition.get("disposition") == "COMMIT_NOW" and (replan.get("installation_receipt") or {}).get("committed") is True and committed)
    full_execution = bool(full_admission and (post_switch.get("accepted") is True or (window.get("route_transaction_count", 0) >= 1 and any(row.get("route_switch_active") for row in window.get("trajectory", [])))))
    low_act = bool(not high and decision_correct and selected == cell["canonical_candidate"] and committed)
    lifecycle_composite = bool(high and timely and answer_received and full_admission and full_execution)
    official = common["official"]
    completion = bool(common["technical_valid"] and (common["official_success"] or (official.get("route_completion") is not None and float(official["route_completion"]) >= 95.0)))
    major = bool(any(common["infractions"][key] > 0 for key in ("collision", "offroad", "wrong_lane", "route_deviation")))
    safe = bool(completion and not major)
    route_executed = bool((not high and low_act) or (high and full_execution))
    wrong_goal = bool(route_executed and selected not in {None, cell["canonical_candidate"]})
    answer_timing = timing.get("answer") or {}
    replan_timing = timing.get("full_replan") or {}
    pre_ask_reads = int(decision.get("runtime_true_intent_reads_before_ask", 0) or 0) + int(answer.get("pre_ask_answer_access_count", 0) or 0)
    added_vla = int(replan.get("additional_vla_forwards", 0) or 0)
    duplicate_candidate = int(replan.get("duplicate_candidate_computations", 0) or 0) + max(0, int(counters.get("candidate_interpretation_invocations", 0) or 0) - 1)
    installation = replan.get("installation_receipt") or online
    second_writer = int(bool(replan.get("second_control_writer"))) + int(window.get("additional_vehicle_control_writers", 0) or 0) + int((installation or {}).get("vehicle_control_write_count", 0) or 0)
    pid_mutation = int(window.get("additional_pid_controllers", 0) or 0)
    controller_mutation = int(bool(replan.get("new_controller"))) + int(window.get("same_native_controller") is False)
    improper_planner = int(bool(full_admission) and ((installation or {}).get("online_route_writer_count") != 1 or (installation or {}).get("new_global_planner_count") != 0))
    technical_reasons = list(common["technical_invalidity_reasons"])
    if common["technical_valid"] and not background_valid:
        technical_reasons.append("BACKGROUND_POLICY_EVIDENCE_INVALID")
    result = {
        "schema": "driveclarify.rq3-v3.part-b-episode-result.v1", "episode_id": cell["episode_id"], "run_id": cell["run_id"],
        "schedule_position": cell["schedule_position"], "condition": cell["condition"], "family": cell["family"], "level": cell["level"],
        "scene_id": cell["scene_id"], "seed": cell["seed"], "wrapper_exit": wrapper_exit, **common,
        "technical_invalidity_reasons": technical_reasons,
        "decision_evaluable": bool(decision_evaluable and not technical_reasons),
        "lifecycle_evaluable": bool((not high or decision_evaluable) and not technical_reasons),
        "execution_evaluable": bool(common["technical_valid"]),
        "consequence_classification": relation, "policy_action": action, "selected_candidate_id": selected,
        "correct_scientific_decision": int(decision_correct and not technical_reasons),
        "ACT": int(action == "ACT"), "ASK_count": ask_count, "unnecessary_ASK": int(not high and ask_count > 0),
        "timely_ASK": int(timely), "answer_receipt": int(answer_received), "Full_Replan_admission": int(full_admission),
        "Full_Replan_execution": int(full_execution), "HIGH_lifecycle_composite": int(lifecycle_composite),
        "native_completion": int(completion), "safe_completion": int(safe), "wrong_goal_execution": int(wrong_goal),
        "major_safety_failure": int(major), "Route_Completion": official.get("route_completion"),
        "first_evidence_sufficiency_TTCmt_s": first.get("TTCmt_s"), "ASK_TTCmt_s": ask.get("TTCmt_s"),
        "remaining_margin_at_ASK_s": ask.get("remaining_margin_s"),
        "answer_latency_simulation_s": answer_timing.get("answer_latency_simulation_s"),
        "answer_to_action_latency_simulation_s": (None if not answer_timing or not replan_timing else float(replan_timing.get("completed_simulation_time_s", 0)) - float(answer_timing.get("simulation_time_s", 0))),
        "Full_Replan_latency_simulation_s": replan_timing.get("latency_simulation_s"),
        "Full_Replan_latency_wall_s": replan_timing.get("latency_wall_s"),
        "clarification_induced_delay_simulation_s": (None if not ask or not replan_timing else float(replan_timing.get("completed_simulation_time_s", 0)) - float(ask.get("simulation_time_s", 0))),
        "candidate_evaluation_latency_wall_s": decision_latency.get("candidate_evaluation_latency_wall_s"),
        "consequence_gate_latency_wall_s": decision_latency.get("consequence_gate_latency_wall_s"),
        "rq2_decision_latency_wall_s": decision_latency.get("rq2_decision_latency_wall_s"),
        "total_decision_latency_wall_s": decision_latency.get("total_decision_latency_wall_s"),
        "added_VLA_forwards": added_vla, "duplicate_candidate_execution": duplicate_candidate, "duplicate_VLA_execution": 0,
        "PID_mutation": pid_mutation, "controller_mutation": controller_mutation, "second_control_writer": second_writer,
        "improper_RoutePlanner_mutation": improper_planner, "pre_ASK_true_intent_reads": pre_ask_reads,
        "answer_release_without_prior_ASK": int(bool(answer) and not answer.get("answer_released_after_durable_ask")),
        "answer_bypass_of_Full_Replan_admission": int(answer_received and committed and not full_admission),
        "valid_native_noncompletion": bool(common["technical_valid"] and not completion),
        "scientific_retry": False, "seed_replacement": False, "source_output": str(output.relative_to(ROOT)),
    }
    result["result_digest"] = digest(result)
    atomic_json(output / "RQ3_V3_FORMAL_B_EPISODE_RESULT.json", result)
    return result


def update_ledger(path: Path, ledger: dict[str, Any]) -> None:
    ledger["ledger_digest"] = digest({key: value for key, value in ledger.items() if key != "ledger_digest"})
    atomic_json(path, ledger)


def run_native(cell: Mapping[str, Any], output: Path, answer: str, label: str) -> tuple[int, list[str]]:
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        str(NATIVE_RUNNER), str(_resolved(cell["config_path"])), str(_resolved(cell["route_path"])), str(cell["seed"]),
        str(cell["rpc_port"]), answer, str(output), label,
    ]
    append_log(f"- Launch `{cell.get('run_id')}`: `{' '.join(command)}`")
    with (output.parent / "native.log").open("wb") as stream:
        completed = subprocess.run(command, cwd=str(ROOT), stdout=stream, stderr=subprocess.STDOUT, check=False)
    return completed.returncode, command


def integrity_violations(row: Mapping[str, Any]) -> list[str]:
    keys = (
        "added_VLA_forwards", "duplicate_VLA_execution", "PID_mutation", "controller_mutation",
        "second_control_writer", "improper_RoutePlanner_mutation", "pre_ASK_true_intent_reads",
        "answer_release_without_prior_ASK",
    )
    return [key for key in keys if int(row.get(key, 0) or 0) != 0]


def valid_false_positive_continuation() -> dict[str, Any]:
    stop = load(REPORT / "MANDATORY_HARD_STOP_RECEIPT.json", {})
    continuation = load(REPORT / "TECHNICAL_CONTINUATION_RECEIPT.json", {})
    if not stop or not continuation:
        return {}
    valid = bool(
        self_digest_valid(stop)
        and self_digest_valid(continuation)
        and continuation.get("status") == "PASS_FALSE_POSITIVE_STOP_CORRECTED_NO_RETRY"
        and continuation.get("original_stop_receipt_digest") == stop.get("receipt_digest")
        and continuation.get("continuation_permitted_for_unexposed_cells") is True
        and continuation.get("scientific_retry_performed") is False
        and continuation.get("seed_replacement_performed") is False
    )
    return continuation if valid else {}


def valid_isolated_continuations() -> list[dict[str, Any]]:
    values = []
    for path in sorted(REPORT.glob("TECHNICAL_CONTINUATION_RECEIPT_[0-9][0-9][0-9].json")):
        value = load(path, {})
        if bool(
            self_digest_valid(value)
            and value.get("status") == "PASS_ISOLATED_EXTERNAL_TECHNICAL_INVALIDITY_QUARANTINED_CONTINUE_UNEXPOSED"
            and value.get("continuation_permitted_for_unexposed_cells") is True
            and value.get("scientific_retry_performed") is False
            and value.get("seed_replacement_performed") is False
            and value.get("neutralized_stop_receipt_digest")
        ):
            values.append(value)
    return values


def hard_stop_paths() -> list[Path]:
    return sorted(
        path for path in REPORT.glob("MANDATORY_HARD_STOP_RECEIPT*.json")
        if path.parent == REPORT
    )


def active_hard_stop() -> dict[str, Any]:
    neutralized = {
        value["neutralized_stop_receipt_digest"]
        for value in valid_isolated_continuations()
    }
    false_positive = valid_false_positive_continuation()
    if false_positive:
        neutralized.add(false_positive["original_stop_receipt_digest"])
    stops = [load(path, {}) for path in hard_stop_paths()]
    stops = [value for value in stops if value and self_digest_valid(value)]
    stops.sort(key=lambda value: (int(value.get("epoch", 0)), str(value.get("receipt_digest", ""))))
    for value in reversed(stops):
        if value.get("receipt_digest") not in neutralized:
            return value
    return {}


def write_hard_stop(reason: str, part: str, cell: Mapping[str, Any], details: Any) -> None:
    value = {
        "schema": "driveclarify.rq3-v3.mandatory-hard-stop.v1", "stage": "RQ3_V3_BENCH2DRIVE_CLOSED_LOOP_FORMAL_EXECUTION_V1",
        "status": "BLOCKED_RQ3_V3_SOURCE_OR_INTEGRITY_FAILURE" if "SOURCE" in reason or "INTEGRITY" in reason else "BLOCKED_RQ3_V3_TECHNICAL_VALIDITY_STOP",
        "reason": reason, "part": part, "cell": dict(cell), "details": details,
        "scientific_retry_performed": False, "seed_replacement_performed": False, "continuing_would_produce_valid_evidence": False,
        "epoch": int(time.time()),
    }
    value["receipt_digest"] = digest(value)
    existing = hard_stop_paths()
    if not existing:
        target = REPORT / "MANDATORY_HARD_STOP_RECEIPT.json"
    elif len(existing) == 1 and valid_false_positive_continuation():
        target = REPORT / "MANDATORY_HARD_STOP_RECEIPT_POST_CONTINUATION.json"
    else:
        target = REPORT / f"MANDATORY_HARD_STOP_RECEIPT_{len(existing) + 1:03d}.json"
    atomic_json(target, value)
    append_log(f"- Mandatory hard stop: `{value['status']}` / `{reason}` at `{cell.get('run_id')}`.")


def correct_false_positive_stop() -> dict[str, Any]:
    existing = valid_false_positive_continuation()
    if existing:
        return existing
    stop_path = REPORT / "MANDATORY_HARD_STOP_RECEIPT.json"
    ledger_path = REPORT / "PART_A_EXECUTION_LEDGER.json"
    stop = load(stop_path, {})
    ledger = load(ledger_path, {})
    if not self_digest_valid(stop) or not self_digest_valid(ledger):
        raise RuntimeError("FALSE_POSITIVE_CORRECTION_REFUSED_INVALID_RECEIPT_DIGEST")
    entries = ledger.get("entries", [])
    expected = bool(
        stop.get("status") == "BLOCKED_RQ3_V3_TECHNICAL_VALIDITY_STOP"
        and stop.get("reason") == "UNBOUNDED_TECHNICAL_INVALIDITY_PREVENTS_AUTHORITATIVE_SUBSEQUENT_EVIDENCE"
        and stop.get("part") == "A"
        and stop.get("details") == ["IDENTITY:official_input_route_hash"]
        and (stop.get("cell") or {}).get("run_id") == "RQ3V3-A-3890-P05-A0"
        and len(entries) == 1
        and entries[0].get("run_id") == "RQ3V3-A-3890-P05-A0"
    )
    if not expected:
        raise RuntimeError("FALSE_POSITIVE_CORRECTION_REFUSED_UNEXPECTED_STOP_STATE")
    if sha(REFERENCE_ORCHESTRATOR) != REFERENCE_ORCHESTRATOR_SHA256:
        raise RuntimeError("FALSE_POSITIVE_CORRECTION_REFUSED_REFERENCE_DRIFT")
    reference_text = REFERENCE_ORCHESTRATOR.read_text(encoding="utf-8")
    reference_semantics = bool(
        'navigation.get("official_input_route_sha256")' in reference_text
        and 'a0["official_input_route_identity"] == a1["official_input_route_identity"]' in reference_text
    )
    source = source_revalidation()
    if not reference_semantics or not source["pass"]:
        raise RuntimeError("FALSE_POSITIVE_CORRECTION_REFUSED_UNVERIFIED_FROZEN_SEMANTICS")

    snapshot = REPORT / "technical_continuation_diagnostic" / "false_positive_stop_snapshot"
    snapshot.mkdir(parents=True, exist_ok=True)
    snapshot_names = (
        "MANDATORY_HARD_STOP_RECEIPT.json", "PART_A_EXECUTION_LEDGER.json", "PART_A_RESULTS.json",
        "PART_A_STATISTICAL_ANALYSIS.json", "PART_B_RESULTS.json", "PART_B_LIFECYCLE_ANALYSIS.json",
        "RQ3_V3_COMBINED_RESULTS.json", "CONTROL_INTEGRITY_RECEIPT.json",
        "TRUE_INTENT_FIREWALL_RECEIPT.json", "SOURCE_FREEZE_REVALIDATION_RECEIPT.json",
        "FINAL_ARTIFACT_AUDIT.json", "FINAL_VALIDATION_RECEIPT.json", "FINAL_REPORT.md",
    )
    snapshot_rows = []
    for name in snapshot_names:
        source_path = REPORT / name
        if not source_path.is_file():
            continue
        target = snapshot / name
        if target.is_file() and sha(target) != sha(source_path):
            raise RuntimeError("FALSE_POSITIVE_CORRECTION_REFUSED_SNAPSHOT_CONFLICT:" + name)
        if not target.is_file():
            shutil.copy2(source_path, target)
        snapshot_rows.append({"path": str(target.relative_to(ROOT)), "sha256": sha(target)})

    original = entries[0]
    output = _resolved(original["source_output"])
    corrected = classify_part_a(stop["cell"], output, int(original["wrapper_exit"]))
    corrected["command"] = original["command"]
    corrected["result_digest"] = digest({key: value for key, value in corrected.items() if key != "result_digest"})
    if not corrected["technical_valid"] or corrected["technical_invalidity_reasons"] or integrity_violations(corrected):
        raise RuntimeError("FALSE_POSITIVE_CORRECTION_DID_NOT_ESTABLISH_TECHNICAL_VALIDITY")
    if corrected["official"].get("status") not in {"Perfect", "Completed"}:
        raise RuntimeError("FALSE_POSITIVE_CORRECTION_REFUSED_NONTERMINAL_OFFICIAL_STATUS")
    atomic_json(output / "RQ3_V3_FORMAL_A_RUN_RESULT.json", corrected)
    ledger["entries"][0] = corrected
    ledger["status"] = "FORMAL_IN_PROGRESS"
    ledger["false_positive_classifier_correction"] = "TECHNICAL_CONTINUATION_RECEIPT.json"
    update_ledger(ledger_path, ledger)

    continuation = {
        "schema": "driveclarify.rq3-v3.technical-continuation-receipt.v1",
        "stage": "RQ3_V3_BENCH2DRIVE_CLOSED_LOOP_FORMAL_EXECUTION_V1",
        "status": "PASS_FALSE_POSITIVE_STOP_CORRECTED_NO_RETRY",
        "original_stop_receipt_digest": stop["receipt_digest"],
        "original_stop_snapshot": snapshot_rows,
        "affected_run_id": corrected["run_id"],
        "original_result_digest": original["result_digest"],
        "corrected_result_digest": corrected["result_digest"],
        "correction_scope": "EXECUTION_ONLY_IDENTITY_DOMAIN_AND_OFFICIAL_STATUS_INTERPRETATION",
        "diagnosis": {
            "native_wrapper_exit": corrected["wrapper_exit"],
            "official_terminal_status": corrected["official"].get("status"),
            "official_terminal_record_count": corrected["official"].get("record_count"),
            "xml_route_sha256": stop["cell"]["route_sha256"],
            "official_global_plan_sha256": corrected["navigation"].get("official_input_route_sha256"),
            "identity_domains_are_distinct": stop["cell"]["route_sha256"] != corrected["navigation"].get("official_input_route_sha256"),
            "frozen_reference_orchestrator": str(REFERENCE_ORCHESTRATOR.relative_to(ROOT)),
            "frozen_reference_orchestrator_sha256": REFERENCE_ORCHESTRATOR_SHA256,
            "frozen_reference_requires_global_plan_identity_present_and_pair_equal": reference_semantics,
            "official_success_statuses_from_frozen_evaluator": ["Perfect", "Completed"],
        },
        "native_episode_rerun": False,
        "scientific_retry_performed": False,
        "seed_replacement_performed": False,
        "raw_native_output_modified": False,
        "frozen_source_modified": False,
        "scientific_contract_modified": False,
        "runtime_protocol_modified": False,
        "original_stop_preserved": True,
        "source_revalidation_pass": source["pass"],
        "source_revalidation_digest": source["receipt_digest"],
        "continuation_permitted_for_unexposed_cells": True,
        "epoch": int(time.time()),
    }
    continuation["receipt_digest"] = digest(continuation)
    atomic_json(REPORT / "TECHNICAL_CONTINUATION_RECEIPT.json", continuation)
    append_log(
        "- Forensic correction: the initial stop was an execution-only identity-domain false positive; "
        "the original native result and stop snapshot were preserved, no episode was rerun, and execution resumed at the next unexposed cell."
    )
    return continuation


def continue_after_isolated_invalidity() -> dict[str, Any]:
    existing = valid_isolated_continuations()
    stop = active_hard_stop()
    if not stop:
        if existing:
            return existing[-1]
        raise RuntimeError("ISOLATED_INVALIDITY_CONTINUATION_REFUSED_NO_ACTIVE_STOP")
    ledger_path = REPORT / "PART_A_EXECUTION_LEDGER.json"
    ledger = load(ledger_path, {})
    if not self_digest_valid(stop) or not self_digest_valid(ledger):
        raise RuntimeError("ISOLATED_INVALIDITY_CONTINUATION_REFUSED_INVALID_RECEIPT_DIGEST")
    entries = ledger.get("entries", [])
    cell = stop.get("cell") or {}
    row = entries[-1] if entries else {}
    output = _resolved(str(row.get("source_output", "")))
    process = load(output / "process_job/PROCESS_RECEIPT.json", {})
    official = row.get("official") or {}
    agent_status = row.get("agent_status") or {}
    evaluator_log_path = output / "process_job/evaluator.log"
    evaluator_log = evaluator_log_path.read_text(encoding="utf-8", errors="replace") if evaluator_log_path.is_file() else ""
    cuda_setup_failure = bool(
        stop.get("status") == "BLOCKED_RQ3_V3_TECHNICAL_VALIDITY_STOP"
        and stop.get("reason") == "UNBOUNDED_TECHNICAL_INVALIDITY_PREVENTS_AUTHORITATIVE_SUBSEQUENT_EVIDENCE"
        and stop.get("part") == "A"
        and cell.get("run_id") == "RQ3V3-A-2509-P02-A0"
        and len(entries) == 13
        and row.get("run_id") == cell.get("run_id")
        and row.get("wrapper_exit") == 0
        and row.get("technical_valid") is False
        and official.get("status") == "Failed - Agent couldn't be set up"
        and float((official.get("meta") or {}).get("duration_game") or 0.0) == 0.0
        and agent_status.get("status") == "INCOMPLETE_V11_NATIVE_MODEL_WINDOW"
        and agent_status.get("window_frame") is None
        and not (output / "owner_evidence/NATIVE_NAVIGATION_INPUT_CONTRACT.json").exists()
        and not (output / "owner_evidence/NATIVE_MODEL_WINDOW_COMPLETE.json").exists()
        and "RuntimeError: CUDA driver initialization failed" in evaluator_log
        and process.get("cleanup_pass") is True
        and process.get("ports_released") is True
        and process.get("process_residue") is False
        and process.get("scientific_retry") is False
        and process.get("formal_seed_replacement") is False
    )
    server_logs = sorted((output / "process_job").glob("carla_server_start_*.log"))
    server_log_texts = [path.read_text(encoding="utf-8", errors="replace") for path in server_logs]
    server_attempts = int(process.get("server_start_attempts", 0) or 0)
    preworld_failures = int(process.get("preworld_startup_failures", 0) or 0)
    infrastructure_retries = int(process.get("infrastructure_retries", 0) or 0)
    preworld_bind_failure = bool(
        stop.get("status") == "BLOCKED_RQ3_V3_TECHNICAL_VALIDITY_STOP"
        and stop.get("reason") == "UNBOUNDED_TECHNICAL_INVALIDITY_PREVENTS_AUTHORITATIVE_SUBSEQUENT_EVIDENCE"
        and stop.get("part") == "A"
        and row.get("run_id") == cell.get("run_id")
        and row.get("wrapper_exit") in {1, 68}
        and row.get("technical_valid") is False
        and official.get("record_count") == 0
        and process.get("evaluator_started_epoch") == 0
        and process.get("world_observed_before_evaluator") is False
        and 1 <= server_attempts <= 3
        and len(server_log_texts) == server_attempts
        and preworld_failures == infrastructure_retries
        and all("bind: Address already in use" in text for text in server_log_texts)
        and process.get("cleanup_pass") is True
        and process.get("ports_released") is True
        and process.get("process_residue") is False
        and process.get("scientific_retry") is False
        and process.get("formal_seed_replacement") is False
    )
    traffic_manager_bind_failure = bool(
        stop.get("status") == "BLOCKED_RQ3_V3_TECHNICAL_VALIDITY_STOP"
        and stop.get("reason") == "UNBOUNDED_TECHNICAL_INVALIDITY_PREVENTS_AUTHORITATIVE_SUBSEQUENT_EVIDENCE"
        and stop.get("part") == "A"
        and row.get("run_id") == cell.get("run_id")
        and row.get("wrapper_exit") == 1
        and row.get("technical_valid") is False
        and official.get("record_count") == 0
        and process.get("evaluator_exit") == 1
        and int(process.get("evaluator_started_epoch", 0) or 0) > 0
        and process.get("world_observed_before_evaluator") is True
        and "trying to create rpc server for traffic manager" in evaluator_log
        and "bind error" in evaluator_log
        and not (output / "owner_evidence/NATIVE_NAVIGATION_INPUT_CONTRACT.json").exists()
        and not (output / "owner_evidence/NATIVE_MODEL_WINDOW_COMPLETE.json").exists()
        and process.get("cleanup_pass") is True
        and process.get("ports_released") is True
        and process.get("process_residue") is False
        and process.get("scientific_retry") is False
        and process.get("formal_seed_replacement") is False
    )
    if not (cuda_setup_failure or preworld_bind_failure or traffic_manager_bind_failure):
        raise RuntimeError("ISOLATED_INVALIDITY_CONTINUATION_REFUSED_UNBOUNDED_OR_UNEXPECTED_STATE")
    cause = (
        "CUDA_DRIVER_INITIALIZATION_FAILED_DURING_AGENT_SETUP"
        if cuda_setup_failure else (
            "CARLA_PREWORLD_RPC_BIND_ADDRESS_ALREADY_IN_USE"
            if preworld_bind_failure else "TRAFFIC_MANAGER_RPC_BIND_ERROR_BEFORE_AGENT_SETUP"
        )
    )
    prior_same_cause = [
        value for value in existing
        if value.get("cause") == cause
    ]
    intervening_valid_rows: list[Mapping[str, Any]] = []
    if prior_same_cause:
        prior_run_id = prior_same_cause[-1].get("quarantined_run_id")
        prior_indexes = [index for index, item in enumerate(entries[:-1]) if item.get("run_id") == prior_run_id]
        if not prior_indexes:
            raise RuntimeError("ISOLATED_INVALIDITY_CONTINUATION_REFUSED_PRIOR_QUARANTINE_NOT_IN_LEDGER")
        intervening_valid_rows = entries[prior_indexes[-1] + 1:-1]
        if not intervening_valid_rows or not all(item.get("technical_valid") is True for item in intervening_valid_rows):
            raise RuntimeError("ISOLATED_INVALIDITY_CONTINUATION_REFUSED_PERSISTENT_WITHOUT_VALID_INTERVAL")
    source = source_revalidation()
    stopping_rule = ROOT / "reports/driveclarify_rq3_v3_simplified_protocol_design_v1/V3_STOPPING_RULES.md"
    stopping_rule_text = stopping_rule.read_text(encoding="utf-8") if stopping_rule.is_file() else ""
    frozen_continuation_rule = bool(
        sha(stopping_rule) == "88f0aa13178a21040eea48fbdfa1305f2a97e6ef841eedd6a2368d6af2b2f508"
        and "Quarantine an isolated external technical-invalid episode" in stopping_rule_text
        and "Do not retry it" in stopping_rule_text
        and "Unexposed cells may resume" in stopping_rule_text
    )
    gpu = subprocess.run(
        ["nvidia-smi", "--query-gpu=index,name,memory.total", "--format=csv,noheader"],
        cwd=str(ROOT), text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
    )
    port = int(cell["rpc_port"]) + (102 if traffic_manager_bind_failure else 0)
    port_check = subprocess.run(
        ["ss", "-ltnp", "sport", "=", f":{port}"], cwd=str(ROOT), text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
    )
    port_released_now = bool(port_check.returncode == 0 and f":{port}" not in port_check.stdout)
    bounded = bool(
        source["pass"] and frozen_continuation_rule and gpu.returncode == 0
        and gpu.stdout.strip() and port_released_now and process.get("cleanup_pass") is True
    )
    if not bounded:
        raise RuntimeError("ISOLATED_INVALIDITY_CONTINUATION_REFUSED_SCOPE_NOT_BOUNDED")

    ordinal = len(existing) + 1
    snapshot = REPORT / "technical_continuation_diagnostic" / f"isolated_invalidity_{ordinal:03d}_snapshot"
    snapshot.mkdir(parents=True, exist_ok=True)
    snapshot_names = (
        next(path.name for path in hard_stop_paths() if load(path, {}).get("receipt_digest") == stop["receipt_digest"]),
        "PART_A_EXECUTION_LEDGER.json", "PART_A_RESULTS.json", "PART_A_STATISTICAL_ANALYSIS.json",
        "PART_B_RESULTS.json", "PART_B_LIFECYCLE_ANALYSIS.json", "RQ3_V3_COMBINED_RESULTS.json",
        "CONTROL_INTEGRITY_RECEIPT.json", "TRUE_INTENT_FIREWALL_RECEIPT.json",
        "SOURCE_FREEZE_REVALIDATION_RECEIPT.json", "FINAL_ARTIFACT_AUDIT.json",
        "FINAL_VALIDATION_RECEIPT.json", "FINAL_REPORT.md",
    )
    snapshot_rows = []
    for name in snapshot_names:
        source_path = REPORT / name
        if not source_path.is_file():
            continue
        target = snapshot / name
        if target.is_file() and sha(target) != sha(source_path):
            raise RuntimeError("ISOLATED_INVALIDITY_CONTINUATION_REFUSED_SNAPSHOT_CONFLICT:" + name)
        if not target.is_file():
            shutil.copy2(source_path, target)
        snapshot_rows.append({"path": str(target.relative_to(ROOT)), "sha256": sha(target)})

    receipt = {
        "schema": "driveclarify.rq3-v3.technical-continuation-receipt.v1",
        "stage": "RQ3_V3_BENCH2DRIVE_CLOSED_LOOP_FORMAL_EXECUTION_V1",
        "status": "PASS_ISOLATED_EXTERNAL_TECHNICAL_INVALIDITY_QUARANTINED_CONTINUE_UNEXPOSED",
        "neutralized_stop_receipt_digest": stop["receipt_digest"],
        "quarantined_run_id": row["run_id"],
        "quarantined_result_digest": row["result_digest"],
        "quarantined_seed": row["seed"],
        "snapshot": snapshot_rows,
        "classification": "GENUINE_EXTERNAL_ZERO_EXPOSURE_TECHNICAL_INVALIDITY",
        "cause": cause,
        "prior_same_cause_quarantine_count": len(prior_same_cause),
        "intervening_technically_valid_run_count": len(intervening_valid_rows),
        "intervening_technically_valid_run_ids": [item["run_id"] for item in intervening_valid_rows],
        "zero_scientific_model_exposure": True,
        "official_duration_game_s": (official.get("meta") or {}).get("duration_game"),
        "world_observed_before_evaluator": process.get("world_observed_before_evaluator"),
        "evaluator_started_epoch": process.get("evaluator_started_epoch"),
        "agent_setup_completed": False,
        "native_model_window_created": False,
        "cleanup_pass": process.get("cleanup_pass"),
        "ports_released": process.get("ports_released"),
        "process_residue": process.get("process_residue"),
        "gpu_read_only_health_check": {"command": "nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader", "exit": gpu.returncode, "output": gpu.stdout.strip()},
        "rpc_port_read_only_health_check": {"port": port, "command": f"ss -ltnp sport = :{port}", "exit": port_check.returncode, "output": port_check.stdout.strip(), "released": port_released_now},
        "frozen_stopping_rule_path": str(stopping_rule.relative_to(ROOT)),
        "frozen_stopping_rule_sha256": sha(stopping_rule),
        "frozen_continuation_rule_verified": frozen_continuation_rule,
        "source_revalidation_pass": source["pass"],
        "source_revalidation_digest": source["receipt_digest"],
        "defect_scope_bounded_to_quarantined_episode": True,
        "unexposed_cells_affected": False,
        "native_episode_rerun": False,
        "scientific_retry_performed": False,
        "seed_replacement_performed": False,
        "imputation_performed": False,
        "frozen_boundary_changed": False,
        "continuation_permitted_for_unexposed_cells": True,
        "epoch": int(time.time()),
    }
    receipt["receipt_digest"] = digest(receipt)
    receipt_path = REPORT / f"TECHNICAL_CONTINUATION_RECEIPT_{ordinal:03d}.json"
    atomic_json(receipt_path, receipt)
    ledger["status"] = "FORMAL_IN_PROGRESS"
    ledger.setdefault("isolated_technical_invalidity_continuations", []).append(str(receipt_path.relative_to(REPORT)))
    update_ledger(ledger_path, ledger)
    append_log(
        f"- Isolated technical invalidity `{row['run_id']}` quarantined without retry or replacement; "
        f"read-only diagnosis bounded `{receipt['cause']}` to the zero-exposure cell, so execution resumed at the next unexposed cell."
    )
    return receipt


def run_campaign() -> dict[str, Any]:
    prepare()
    stop = active_hard_stop()
    if stop:
        return stop
    a_path = REPORT / "PART_A_EXECUTION_LEDGER.json"
    a = load(a_path, {})
    completed = {row["run_id"] for row in a.get("entries", [])}
    a["status"] = "FORMAL_IN_PROGRESS"
    a.setdefault("execution_started_epoch", int(time.time()))
    update_ledger(a_path, a)
    for cell in a["planned_cells"]:
        if cell["run_id"] in completed:
            continue
        source = source_revalidation()
        if not source["pass"]:
            write_hard_stop("SOURCE_OR_PROTECTED_HISTORY_DRIFT", "A", cell, source)
            a["status"] = "MANDATORY_HARD_STOP"
            update_ledger(a_path, a)
            return active_hard_stop()
        output = _resolved(cell["output_path"])
        if output.exists():
            write_hard_stop("UNLEDGERED_PREEXISTING_FORMAL_OUTPUT", "A", cell, str(output))
            a["status"] = "MANDATORY_HARD_STOP"
            update_ledger(a_path, a)
            return active_hard_stop()
        code, command = run_native(cell, output, "NONE", "RQ3_V3_FORMAL_A_NO_SCIENTIFIC_RETRY")
        result = classify_part_a(cell, output, code)
        result["command"] = command
        result["result_digest"] = digest({key: value for key, value in result.items() if key != "result_digest"})
        atomic_json(output / "RQ3_V3_FORMAL_A_RUN_RESULT.json", result)
        a["entries"].append(result)
        update_ledger(a_path, a)
        violations = integrity_violations(result)
        if violations:
            write_hard_stop("CONFIRMED_EXECUTION_AFFECTING_INTEGRITY_VIOLATION", "A", cell, violations)
            a["status"] = "MANDATORY_HARD_STOP"
            update_ledger(a_path, a)
            return active_hard_stop()
        if not result["technical_valid"]:
            write_hard_stop("UNBOUNDED_TECHNICAL_INVALIDITY_PREVENTS_AUTHORITATIVE_SUBSEQUENT_EVIDENCE", "A", cell, result["technical_invalidity_reasons"])
            a["status"] = "MANDATORY_HARD_STOP"
            update_ledger(a_path, a)
            return active_hard_stop()
    a["status"] = "COMPLETED_FROZEN_80_RUN_ROSTER"
    a["execution_finished_epoch"] = int(time.time())
    update_ledger(a_path, a)
    append_log("- Part A frozen roster reached its legal execution endpoint; no primary aggregate was inspected during execution.")

    b_path = REPORT / "PART_B_EXECUTION_LEDGER.json"
    b = load(b_path, {})
    completed = {row["episode_id"] for row in b.get("entries", [])}
    b["status"] = "FORMAL_IN_PROGRESS"
    b.setdefault("execution_started_epoch", int(time.time()))
    update_ledger(b_path, b)
    for cell in b["planned_cells"]:
        if cell["episode_id"] in completed:
            continue
        source = source_revalidation()
        if not source["pass"]:
            write_hard_stop("SOURCE_OR_PROTECTED_HISTORY_DRIFT", "B", cell, source)
            b["status"] = "MANDATORY_HARD_STOP"
            update_ledger(b_path, b)
            return active_hard_stop()
        output = _resolved(cell["output_path"])
        if output.exists():
            write_hard_stop("UNLEDGERED_PREEXISTING_FORMAL_OUTPUT", "B", cell, str(output))
            b["status"] = "MANDATORY_HARD_STOP"
            update_ledger(b_path, b)
            return active_hard_stop()
        answer = cell.get("answer_candidate_id") or "NONE"
        code, command = run_native(cell, output, answer, "RQ3_V3_FORMAL_B_NO_SCIENTIFIC_RETRY")
        result = classify_part_b(cell, output, code)
        result["command"] = command
        result["result_digest"] = digest({key: value for key, value in result.items() if key != "result_digest"})
        atomic_json(output / "RQ3_V3_FORMAL_B_EPISODE_RESULT.json", result)
        b["entries"].append(result)
        update_ledger(b_path, b)
        violations = integrity_violations(result)
        if violations:
            write_hard_stop("CONFIRMED_EXECUTION_AFFECTING_INTEGRITY_VIOLATION", "B", cell, violations)
            b["status"] = "MANDATORY_HARD_STOP"
            update_ledger(b_path, b)
            return active_hard_stop()
        if not (result["decision_evaluable"] and result["lifecycle_evaluable"] and result["execution_evaluable"]):
            write_hard_stop("UNBOUNDED_TECHNICAL_INVALIDITY_PREVENTS_AUTHORITATIVE_SUBSEQUENT_EVIDENCE", "B", cell, result["technical_invalidity_reasons"])
            b["status"] = "MANDATORY_HARD_STOP"
            update_ledger(b_path, b)
            return active_hard_stop()
    b["status"] = "COMPLETED_FROZEN_35_EPISODE_ROSTER"
    b["execution_finished_epoch"] = int(time.time())
    update_ledger(b_path, b)
    append_log("- Part B frozen roster reached its legal execution endpoint; no performance futility stop was applied.")
    return {"status": "COMPLETE_LEGAL_FORMAL_ROSTER_ENDPOINT"}


def summary(values: Iterable[Any]) -> dict[str, Any]:
    clean = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    return {
        "count": len(clean), "mean": statistics.fmean(clean) if clean else None,
        "median": statistics.median(clean) if clean else None, "minimum": min(clean) if clean else None,
        "maximum": max(clean) if clean else None,
        "standard_deviation": statistics.stdev(clean) if len(clean) > 1 else (0.0 if clean else None),
    }


def rate(rows: Sequence[Mapping[str, Any]], key: str) -> dict[str, Any]:
    numerator = sum(int(row.get(key, 0) or 0) for row in rows)
    return {"numerator": numerator, "denominator": len(rows), "rate": numerator / len(rows) if rows else None}


def safety_sum(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    keys = (
        "collision", "collision_pedestrian", "collision_vehicle", "collision_static", "offroad", "wrong_lane",
        "outside_route_lanes", "route_deviation", "red_light", "stop_sign", "minimum_speed", "blocked",
        "route_timeout", "scenario_timeout", "yield_emergency_vehicle",
    )
    return {key: sum(int(row.get("infractions", {}).get(key, 0) or 0) for row in rows) for key in keys}


def mcnemar(a0: Sequence[bool], a1: Sequence[bool]) -> dict[str, Any]:
    a0_only = sum(bool(left) and not bool(right) for left, right in zip(a0, a1))
    a1_only = sum(not bool(left) and bool(right) for left, right in zip(a0, a1))
    discordant = a0_only + a1_only
    if discordant == 0:
        p = 1.0
    else:
        smaller = min(a0_only, a1_only)
        p = min(1.0, 2.0 * sum(math.comb(discordant, k) for k in range(smaller + 1)) / (2 ** discordant))
    return {"A0_only": a0_only, "A1_only": a1_only, "discordant": discordant, "two_sided_exact_p": p}


def newcombe_paired(a0: Sequence[bool], a1: Sequence[bool]) -> dict[str, Any]:
    e = sum(bool(right) and bool(left) for left, right in zip(a0, a1))
    f = sum(bool(right) and not bool(left) for left, right in zip(a0, a1))
    g = sum(not bool(right) and bool(left) for left, right in zip(a0, a1))
    h = sum(not bool(right) and not bool(left) for left, right in zip(a0, a1))
    n = e + f + g + h
    if n == 0:
        return {"n": 0, "estimate": None, "lower": None, "upper": None, "table_e_f_g_h": [e, f, g, h]}
    z = NormalDist().inv_cdf(0.95)

    def wilson(p: float) -> tuple[float, float]:
        denominator = 1.0 + z * z / n
        center = (p + z * z / (2.0 * n)) / denominator
        half = z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n)) / denominator
        return center - half, center + half

    p1, p0 = (e + f) / n, (e + g) / n
    l1, u1 = wilson(p1)
    l0, u0 = wilson(p0)
    denominator = math.sqrt((e + f) * (g + h) * (e + g) * (f + h))
    if denominator == 0:
        phi = 0.0
    elif e * h > f * g:
        phi = max(e * h - f * g - n / 2.0, 0.0) / denominator
    else:
        phi = (e * h - f * g) / denominator
    estimate = (f - g) / n
    lower = estimate - math.sqrt(max(0.0, (p1 - l1) ** 2 + (u0 - p0) ** 2 - 2.0 * phi * (p1 - l1) * (u0 - p0)))
    upper = estimate + math.sqrt(max(0.0, (u1 - p1) ** 2 + (p0 - l0) ** 2 - 2.0 * phi * (u1 - p1) * (p0 - l0)))
    return {
        "n": n, "table_e_f_g_h": [e, f, g, h], "estimate": estimate, "lower": max(-1.0, lower), "upper": min(1.0, upper),
        "z": z, "phi": phi, "method": "Newcombe 1998 paired-proportion score interval method 10",
        "orientation": "A1_MINUS_A0", "mcnemar_descriptive": mcnemar(a0, a1),
    }


def paired_bootstrap(differences: Sequence[float], rng: Any) -> dict[str, Any]:
    import numpy as np
    values = np.asarray(differences, dtype=np.float64)
    indices = rng.integers(0, len(values), size=(100000, len(values)), endpoint=False)
    means = values[indices].mean(axis=1)
    return {
        "estimate": float(values.mean()), "one_sided_95_lower": float(np.quantile(means, 0.05, method="linear")),
        "resamples": 100000, "resampling_unit": "WHOLE_EVALUABLE_ROUTE_SEED_PAIR",
        "method": "paired percentile bootstrap", "quantile": "q=0.05 method=linear",
    }


def pair_is_evaluable(pair: Mapping[str, Mapping[str, Any]]) -> bool:
    return bool(
        set(pair) == {"A0", "A1"} and pair["A0"]["technical_valid"] and pair["A1"]["technical_valid"]
        and pair["A0"]["route_id"] == pair["A1"]["route_id"] and pair["A0"]["seed"] == pair["A1"]["seed"]
        and pair["A0"]["navigation"].get("official_input_route_sha256") == pair["A1"]["navigation"].get("official_input_route_sha256")
    )


def analyze_part_a(rows: Sequence[Mapping[str, Any]], complete: bool) -> tuple[dict[str, Any], dict[str, Any], str]:
    grouped: dict[str, dict[str, Mapping[str, Any]]] = defaultdict(dict)
    for row in rows:
        grouped[row["pair_id"]][row["arm"]] = row
    route_index = {route: index for index, route in enumerate(ROUTE_ORDER)}
    evaluable = [(pair_id, pair) for pair_id, pair in grouped.items() if pair_is_evaluable(pair)]
    evaluable.sort(key=lambda item: (route_index[item[1]["A0"]["route_id"]], int(item[0].split("P")[-1])))
    pairs = [pair for _, pair in evaluable]
    by_route = Counter(pair["A0"]["route_id"] for pair in pairs)
    evaluability = bool(complete and len(pairs) >= 36 and all(by_route[route] >= 4 for route in ROUTE_ORDER))
    a0, a1 = [pair["A0"] for pair in pairs], [pair["A1"] for pair in pairs]
    base = {
        "schema": "driveclarify.rq3-v3.part-a-results.v1", "planned_pairs": 40, "planned_runs": 80,
        "executed_runs": len(rows), "complete_legal_execution": complete, "evaluable_pairs": len(pairs),
        "evaluable_pairs_by_route": {route: by_route[route] for route in ROUTE_ORDER},
        "technical_invalid_runs": sum(not row["technical_valid"] for row in rows),
        "technical_invalidity_reasons": dict(Counter(reason for row in rows for reason in row.get("technical_invalidity_reasons", []))),
        "valid_native_noncompletion_count": sum(row.get("valid_native_noncompletion", False) for row in rows),
        "A0_Driving_Score": summary(row["driving_score"] for row in a0), "A1_Driving_Score": summary(row["driving_score"] for row in a1),
        "A0_Route_Completion": summary(row["route_completion"] for row in a0), "A1_Route_Completion": summary(row["route_completion"] for row in a1),
        "official_success": {"A0": rate(a0, "official_success"), "A1": rate(a1, "official_success")},
        "any_official_collision": {"A0": rate(a0, "any_official_collision"), "A1": rate(a1, "any_official_collision")},
        "official_infractions": {"A0": safety_sum(a0), "A1": safety_sum(a1)},
        "A1_transparent_states": dict(Counter(row["semantic_pass_through_state"] for row in a1)),
        "false_ASK": sum(row["false_ASK"] for row in a1),
        "clarification_induced_WAIT": sum(row["clarification_induced_WAIT"] for row in a1),
        "Full_Replan_count": sum(row["Full_Replan_count"] for row in a1),
        "DriveClarify_control_intervention_count": sum(row["DriveClarify_control_intervention_count"] for row in a1),
        "added_VLA_forwards": sum(row["added_VLA_forwards"] for row in a1),
        "duplicate_candidate_execution": sum(row["duplicate_candidate_execution"] for row in a1),
        "duplicate_VLA_execution": sum(row["duplicate_VLA_execution"] for row in a1),
        "second_control_writer": sum(row["second_control_writer"] for row in a1),
        "PID_mutation": sum(row["PID_mutation"] for row in a1),
        "controller_mutation": sum(row["controller_mutation"] for row in a1),
        "pre_ASK_true_intent_reads": sum(row["pre_ASK_true_intent_reads"] for row in a1),
        "scientific_retries": 0, "seed_replacements": 0,
    }
    analysis: dict[str, Any] = {
        "schema": "driveclarify.rq3-v3.part-a-statistical-analysis.v1", "analysis_allowed": evaluability,
        "analysis_seed": ANALYSIS_SEED, "analysis_rng": "numpy.random.PCG64DXSM", "python": sys.version.split()[0],
    }
    if evaluability:
        import numpy as np
        ds = [float(right["driving_score"]) - float(left["driving_score"]) for left, right in zip(a0, a1)]
        rc = [float(right["route_completion"]) - float(left["route_completion"]) for left, right in zip(a0, a1)]
        rng = np.random.Generator(np.random.PCG64DXSM(ANALYSIS_SEED))
        ds_ci = paired_bootstrap(ds, rng)
        rc_ci = paired_bootstrap(rc, rng)
        success_ci = newcombe_paired([row["official_success"] for row in a0], [row["official_success"] for row in a1])
        collision_ci = newcombe_paired([row["any_official_collision"] for row in a0], [row["any_official_collision"] for row in a1])
        gates = {
            "Driving_Score": ds_ci["one_sided_95_lower"] > -10.0,
            "Route_Completion": rc_ci["one_sided_95_lower"] > -10.0,
            "official_success": success_ci["lower"] > -0.10,
            "any_official_collision": collision_ci["upper"] < 0.10,
        }
        analysis.update({
            "numpy": np.__version__, "evaluable_pair_order": [pair_id for pair_id, _ in evaluable],
            "Driving_Score": ds_ci, "Route_Completion": rc_ci,
            "official_success": success_ci, "any_official_collision": collision_ci,
            "strict_nonregression_gates": gates, "all_four_primary_gates_pass": all(gates.values()),
        })
    else:
        analysis.update({"numpy": None, "Driving_Score": None, "Route_Completion": None, "official_success": None, "any_official_collision": None, "strict_nonregression_gates": None, "all_four_primary_gates_pass": False})
    transparency = all(base[key] == 0 for key in (
        "false_ASK", "clarification_induced_WAIT", "Full_Replan_count", "DriveClarify_control_intervention_count",
        "added_VLA_forwards", "duplicate_candidate_execution", "duplicate_VLA_execution", "second_control_writer",
        "PID_mutation", "controller_mutation", "pre_ASK_true_intent_reads",
    ))
    analysis["transparency_integrity_conditions_pass"] = transparency
    if not evaluability:
        verdict = "NOT_EVALUABLE"
    elif analysis["all_four_primary_gates_pass"] and transparency:
        verdict = "SUPPORTED"
    else:
        verdict = "NOT_SUPPORTED"
    base["H_RQ3_A"] = verdict
    base["results_digest"] = digest(base)
    analysis["analysis_digest"] = digest(analysis)
    return base, analysis, verdict


def analyze_part_b(rows: Sequence[Mapping[str, Any]], complete: bool) -> tuple[dict[str, Any], dict[str, Any], str]:
    by_condition = {condition: [row for row in rows if row["condition"] == condition] for condition in CONDITION_ORDER}
    decision_rows = [row for row in rows if row["decision_evaluable"]]
    lifecycle_rows = [row for row in rows if row["level"] == "HIGH" and row["lifecycle_evaluable"]]
    execution_rows = [row for row in rows if row["execution_evaluable"]]
    decision_gate = bool(complete and len(decision_rows) >= 32 and all(sum(row["decision_evaluable"] for row in by_condition[name]) >= 4 for name in CONDITION_ORDER))
    lifecycle_gate = bool(complete and sum(row["lifecycle_evaluable"] for row in rows) >= 32 and all(sum(row["lifecycle_evaluable"] for row in by_condition[name]) >= 4 for name in CONDITION_ORDER))
    execution_gate = bool(complete and len(execution_rows) >= 32 and all(sum(row["execution_evaluable"] for row in by_condition[name]) >= 4 for name in CONDITION_ORDER))
    low = [row for row in decision_rows if row["level"] == "LOW"]
    high = [row for row in decision_rows if row["level"] == "HIGH"]
    result = {
        "schema": "driveclarify.rq3-v3.part-b-results.v1", "planned_episodes": 35, "executed_episodes": len(rows),
        "complete_legal_execution": complete, "technically_evaluable": {
            "decision": len(decision_rows), "lifecycle_all_episode_layer": sum(row["lifecycle_evaluable"] for row in rows), "execution": len(execution_rows),
            "by_condition": {name: {"decision": sum(row["decision_evaluable"] for row in values), "lifecycle": sum(row["lifecycle_evaluable"] for row in values), "execution": sum(row["execution_evaluable"] for row in values)} for name, values in by_condition.items()},
        },
        "technical_invalidity_counts": {
            "decision": len(rows) - len(decision_rows), "lifecycle": len(rows) - sum(row["lifecycle_evaluable"] for row in rows), "execution": len(rows) - len(execution_rows),
            "reasons": dict(Counter(reason for row in rows for reason in row.get("technical_invalidity_reasons", []))),
        },
        "correct_decisions": rate(decision_rows, "correct_scientific_decision"),
        "condition_decisions": {name: rate([row for row in values if row["decision_evaluable"]], "correct_scientific_decision") for name, values in by_condition.items()},
        "LOW_ACT": rate(low, "ACT"), "LOW_unnecessary_ASK": rate(low, "unnecessary_ASK"),
        "HIGH_timely_ASK": rate(high, "timely_ASK"), "HIGH_lifecycle_composite": rate(lifecycle_rows, "HIGH_lifecycle_composite"),
        "CRITICAL_lifecycle": {name: rate([row for row in by_condition[name] if row["lifecycle_evaluable"]], "HIGH_lifecycle_composite") for name in CONDITION_ORDER if name.endswith("CRITICAL")},
        "native_completion": rate(execution_rows, "native_completion"),
        "condition_native_completion": {name: rate([row for row in values if row["execution_evaluable"]], "native_completion") for name, values in by_condition.items()},
        "safe_completion": rate(execution_rows, "safe_completion"),
        "condition_safe_completion": {name: rate([row for row in values if row["execution_evaluable"]], "safe_completion") for name, values in by_condition.items()},
        "wrong_goal_execution": rate(execution_rows, "wrong_goal_execution"),
        "pedestrian_collision_episodes": {"numerator": sum(row["infractions"]["collision_pedestrian"] > 0 for row in execution_rows), "denominator": len(execution_rows), "rate": (sum(row["infractions"]["collision_pedestrian"] > 0 for row in execution_rows) / len(execution_rows) if execution_rows else None)},
        "official_infractions": safety_sum(execution_rows),
        "valid_native_noncompletion_count": sum(row["valid_native_noncompletion"] for row in execution_rows),
        "scientific_retries": 0, "seed_replacements": 0,
    }
    timing_fields = (
        "first_evidence_sufficiency_TTCmt_s", "ASK_TTCmt_s", "remaining_margin_at_ASK_s", "answer_latency_simulation_s",
        "answer_to_action_latency_simulation_s", "Full_Replan_latency_simulation_s", "Full_Replan_latency_wall_s",
        "clarification_induced_delay_simulation_s",
    )
    lifecycle = {
        "schema": "driveclarify.rq3-v3.part-b-lifecycle-analysis.v1",
        "evaluability_gates": {"decision": decision_gate, "lifecycle": lifecycle_gate, "execution": execution_gate},
        "timing": {field: summary(row.get(field) for row in lifecycle_rows) for field in timing_fields},
    }
    conditions = {
        "overall_correct": result["correct_decisions"]["numerator"] >= 32,
        "per_condition_correct": all(value["numerator"] >= 4 for value in result["condition_decisions"].values()),
        "LOW_ACT": result["LOW_ACT"]["numerator"] >= 14,
        "LOW_unnecessary_ASK": result["LOW_unnecessary_ASK"]["numerator"] <= 1,
        "HIGH_timely_ASK": result["HIGH_timely_ASK"]["numerator"] >= 18,
        "HIGH_lifecycle_overall": result["HIGH_lifecycle_composite"]["numerator"] >= 18,
        "HIGH_lifecycle_per_condition": all(value["numerator"] >= 4 for value in result["CRITICAL_lifecycle"].values()),
        "native_completion_overall": result["native_completion"]["numerator"] >= 28,
        "native_completion_per_condition": all(value["numerator"] >= 3 for value in result["condition_native_completion"].values()),
        "safe_completion_overall": result["safe_completion"]["numerator"] >= 28,
        "safe_completion_per_condition": all(value["numerator"] >= 3 for value in result["condition_safe_completion"].values()),
        "wrong_goal_zero": result["wrong_goal_execution"]["numerator"] == 0,
        "pedestrian_collision_zero": result["pedestrian_collision_episodes"]["numerator"] == 0,
    }
    lifecycle["support_conditions"] = conditions
    lifecycle["all_support_conditions_pass"] = all(conditions.values())
    if not (decision_gate and lifecycle_gate and execution_gate):
        verdict = "NOT_EVALUABLE"
    elif lifecycle["all_support_conditions_pass"]:
        verdict = "SUPPORTED"
    else:
        verdict = "NOT_SUPPORTED"
    result["H_RQ3_B_without_H_RQ3_C_conjunction"] = verdict
    result["results_digest"] = digest(result)
    lifecycle["analysis_digest"] = digest(lifecycle)
    return result, lifecycle, verdict


def build_integrity(a_rows: Sequence[Mapping[str, Any]], b_rows: Sequence[Mapping[str, Any]], source: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    rows = list(a_rows) + list(b_rows)
    integrity = {
        "schema": "driveclarify.rq3-v3.control-integrity-receipt.v1",
        "second_control_writer": sum(int(row.get("second_control_writer", 0) or 0) for row in rows),
        "PID_mutation": sum(int(row.get("PID_mutation", 0) or 0) for row in rows),
        "controller_mutation": sum(int(row.get("controller_mutation", 0) or 0) for row in rows),
        "improper_RoutePlanner_mutation": sum(int(row.get("improper_RoutePlanner_mutation", 0) or 0) for row in rows),
        "duplicate_VLA_execution": sum(int(row.get("duplicate_VLA_execution", 0) or 0) for row in rows),
        "duplicate_candidate_execution": sum(int(row.get("duplicate_candidate_execution", 0) or 0) for row in rows),
        "unauthorized_additional_VLA_forwards": sum(int(row.get("added_VLA_forwards", 0) or 0) for row in rows),
        "source_freeze_pass": bool(source.get("pass")),
        "scientific_retries": 0, "seed_replacements": 0,
    }
    integrity["pass_before_artifact_audit"] = bool(integrity["source_freeze_pass"] and all(integrity[key] == 0 for key in (
        "second_control_writer", "PID_mutation", "controller_mutation", "improper_RoutePlanner_mutation",
        "duplicate_VLA_execution", "unauthorized_additional_VLA_forwards",
    )))
    integrity["receipt_digest"] = digest(integrity)
    firewall = {
        "schema": "driveclarify.rq3-v3.true-intent-firewall-receipt.v1",
        "pre_ASK_true_intent_reads": sum(int(row.get("pre_ASK_true_intent_reads", 0) or 0) for row in rows),
        "answer_release_without_prior_ASK": sum(int(row.get("answer_release_without_prior_ASK", 0) or 0) for row in b_rows),
        "answer_bypass_of_Full_Replan_admission": sum(int(row.get("answer_bypass_of_Full_Replan_admission", 0) or 0) for row in b_rows),
        "formal_configs_contain_canonical_answer": False,
    }
    firewall["pass"] = all(firewall[key] == 0 for key in ("pre_ASK_true_intent_reads", "answer_release_without_prior_ASK", "answer_bypass_of_Full_Replan_admission"))
    firewall["receipt_digest"] = digest(firewall)
    return integrity, firewall


def artifact_tree() -> dict[str, Any]:
    files = sorted(path for path in REPORT.rglob("*") if path.is_file() and path.name not in {"FINAL_ARTIFACT_AUDIT.json", "FINAL_VALIDATION_RECEIPT.json", "FINAL_REPORT.md"})
    rows = [{"path": str(path.relative_to(REPORT)), "bytes": path.stat().st_size, "sha256": sha(path)} for path in files]
    return {"file_count": len(rows), "bytes": sum(row["bytes"] for row in rows), "tree_digest": digest(rows)}


def choose_final_status(a: str, b: str, c: str, hard_stop: Mapping[str, Any]) -> str:
    if hard_stop:
        return str(hard_stop["status"])
    if "NOT_EVALUABLE" in {a, b}:
        return "RQ3_V3_NOT_SUPPORTED_INSUFFICIENT_EVALUABILITY"
    if c != "SUPPORTED":
        return "RQ3_V3_EXECUTION_INTEGRITY_NOT_SUPPORTED"
    if a == b == c == "SUPPORTED":
        return "PASS_RQ3_V3_BENCH2DRIVE_CLOSED_LOOP_VALIDATED"
    if a == "NOT_SUPPORTED" and b == "NOT_SUPPORTED":
        return "RQ3_V3_MULTIPLE_HYPOTHESES_NOT_SUPPORTED"
    if a == "NOT_SUPPORTED":
        return "RQ3_V3_ORDINARY_DRIVING_PRESERVATION_NOT_SUPPORTED"
    return "RQ3_V3_CLARIFICATION_LIFECYCLE_NOT_SUPPORTED"


def final_report(combined: Mapping[str, Any], a: Mapping[str, Any], aa: Mapping[str, Any], b: Mapping[str, Any], ba: Mapping[str, Any], integrity: Mapping[str, Any], firewall: Mapping[str, Any], audit: Mapping[str, Any], validation: Mapping[str, Any]) -> str:
    manifest_a = load(FREEZE / "PART_A_FORMAL_MANIFEST.json", {})
    manifest_b = load(FREEZE / "PART_B_FORMAL_MANIFEST.json", {})
    a_seeds = {route: [row["formal_seed"] for row in sorted(manifest_a["pairs"], key=lambda item: item["pair_id"]) if row["route_id"] == route] for route in ROUTE_ORDER}
    b_seeds = {name: [row["formal_seed"] for row in sorted(manifest_b["episodes"], key=lambda item: item["episode_id"]) if row["condition"] == name] for name in CONDITION_ORDER}
    paths = [str(REPORT / name) for name in REQUIRED_EXECUTION_FILES]
    lines = [
        "# DriveClarify RQ3-V3 final formal execution", "",
        f"1. Exact final status: `{combined['exact_final_status']}`.",
        f"2. Final freeze digest: `{FINAL_FREEZE_DIGEST}`.",
        "3. RQ1 dependency: `SUPPORTED_AND_FROZEN`.",
        "4. RQ2 dependency: `SUPPORTED_AND_FROZEN`.",
        f"5. Source/integrity status: source=`{combined['source_status']}`, control=`{'PASS' if integrity['pass'] else 'FAIL'}`, firewall=`{'PASS' if firewall['pass'] else 'FAIL'}`.",
        "", "## Part A", "",
        f"6. Planned/executed/evaluable pairs: `40/{a['executed_runs'] // 2}/{a['evaluable_pairs']}`; planned/executed runs: `80/{a['executed_runs']}`.",
        f"7. Route IDs and formal seeds: `{json.dumps(a_seeds, sort_keys=True)}`.",
        f"8. A0 Driving Score summary: `{json.dumps(a['A0_Driving_Score'], sort_keys=True)}`.",
        f"9. A1 Driving Score summary: `{json.dumps(a['A1_Driving_Score'], sort_keys=True)}`.",
        f"10. Paired Driving Score effect + one-sided CI: `{json.dumps(aa.get('Driving_Score'), sort_keys=True)}`.",
        f"11. A0 Route Completion summary: `{json.dumps(a['A0_Route_Completion'], sort_keys=True)}`.",
        f"12. A1 Route Completion summary: `{json.dumps(a['A1_Route_Completion'], sort_keys=True)}`.",
        f"13. Paired Route Completion effect + one-sided CI: `{json.dumps(aa.get('Route_Completion'), sort_keys=True)}`.",
        f"14. A0/A1 official success: `{json.dumps(a['official_success'], sort_keys=True)}`.",
        f"15. Success risk difference + one-sided CI: `{json.dumps(aa.get('official_success'), sort_keys=True)}`.",
        f"16. A0/A1 any-collision rates: `{json.dumps(a['any_official_collision'], sort_keys=True)}`.",
        f"17. Collision risk difference + one-sided CI: `{json.dumps(aa.get('any_official_collision'), sort_keys=True)}`.",
        f"18. False ASK: `{a['false_ASK']}`.", f"19. Clarification-induced WAIT: `{a['clarification_induced_WAIT']}`.",
        f"20. Full Replan on ordinary routes: `{a['Full_Replan_count']}`.", f"21. DriveClarify control intervention: `{a['DriveClarify_control_intervention_count']}`.",
        f"22. H-RQ3-A: `{combined['H_RQ3_A']}`.",
        "", "## Part B", "",
        f"23. Planned/executed/technically evaluable: `35/{b['executed_episodes']}/{json.dumps(b['technically_evaluable'], sort_keys=True)}`.",
        f"24. Seven conditions and formal seeds: `{json.dumps(b_seeds, sort_keys=True)}`.",
        f"25. Correct decisions: `{b['correct_decisions']['numerator']}/{b['correct_decisions']['denominator']}`.",
        f"26. Per-condition decision results: `{json.dumps(b['condition_decisions'], sort_keys=True)}`.",
        f"27. LOW ACT: `{b['LOW_ACT']['numerator']}/{b['LOW_ACT']['denominator']}`.",
        f"28. Unnecessary LOW ASK: `{b['LOW_unnecessary_ASK']['numerator']}/{b['LOW_unnecessary_ASK']['denominator']}`.",
        f"29. Timely HIGH ASK: `{b['HIGH_timely_ASK']['numerator']}/{b['HIGH_timely_ASK']['denominator']}`.",
        f"30. HIGH lifecycle composite: `{b['HIGH_lifecycle_composite']['numerator']}/{b['HIGH_lifecycle_composite']['denominator']}`.",
        f"31. Per-CRITICAL lifecycle: `{json.dumps(b['CRITICAL_lifecycle'], sort_keys=True)}`.",
        f"32. Native completion: `{b['native_completion']['numerator']}/{b['native_completion']['denominator']}`.",
        f"33. Per-condition native completion: `{json.dumps(b['condition_native_completion'], sort_keys=True)}`.",
        f"34. Safe completion: `{b['safe_completion']['numerator']}/{b['safe_completion']['denominator']}`.",
        f"35. Per-condition safe completion: `{json.dumps(b['condition_safe_completion'], sort_keys=True)}`.",
        f"36. Wrong goal: `{b['wrong_goal_execution']['numerator']}/{b['wrong_goal_execution']['denominator']}`.",
        f"37. Pedestrian-collision episodes: `{b['pedestrian_collision_episodes']['numerator']}/{b['pedestrian_collision_episodes']['denominator']}`.",
        f"38. All collision totals/subclasses: `{json.dumps({key: value for key, value in b['official_infractions'].items() if key.startswith('collision')}, sort_keys=True)}`.",
        f"39. Offroad/wrong-lane/route-deviation: `{b['official_infractions']['offroad']}/{b['official_infractions']['wrong_lane']}/{b['official_infractions']['route_deviation']}`.",
        f"40. Red-light/stop-sign/minimum-speed: `{b['official_infractions']['red_light']}/{b['official_infractions']['stop_sign']}/{b['official_infractions']['minimum_speed']}`.",
        f"41. Timing statistics: `{json.dumps(ba['timing'], sort_keys=True)}`.",
        f"42. Valid native stall/noncompletion: Part A=`{a['valid_native_noncompletion_count']}`, Part B=`{b['valid_native_noncompletion_count']}`.",
        f"43. Technical invalidity: Part A=`{a['technical_invalid_runs']}`, Part B=`{json.dumps(b['technical_invalidity_counts'], sort_keys=True)}`.",
        "44. Scientific retries: `0`.", "45. Seed replacements: `0`.",
        f"46. Added VLA forwards: `{integrity['unauthorized_additional_VLA_forwards']}`.",
        f"47. Duplicate candidate/VLA executions: `{integrity['duplicate_candidate_execution']}/{integrity['duplicate_VLA_execution']}`.",
        f"48. PID/controller mutations: `{integrity['PID_mutation']}/{integrity['controller_mutation']}`.",
        f"49. Second writer: `{integrity['second_control_writer']}`.", f"50. Pre-ASK true-intent reads: `{firewall['pre_ASK_true_intent_reads']}`.",
        f"51. H-RQ3-B: `{combined['H_RQ3_B']}`.", f"52. H-RQ3-C: `{combined['H_RQ3_C']}`.",
        f"53. Overall RQ3-V3 verdict: `{combined['exact_final_status']}`.",
        f"54. Final artifact/source validation digest: artifact=`{audit['audit_digest']}`, validation=`{validation['validation_digest']}`, source=`{combined['source_revalidation_digest']}`.",
        f"55. Exact report/artifact paths: `{json.dumps(paths)}`.", "",
        "RQ3-V1 remains unchanged.", "RQ3-V2 remains unchanged.",
        "No historical failed result was overwritten or reclassified.",
        "The campaign is stopped after this report; no extension, paper edit, tuning, or further campaign was started.",
    ]
    return "\n".join(lines)


def analyze_and_finalize() -> dict[str, Any]:
    ledger_a = load(REPORT / "PART_A_EXECUTION_LEDGER.json", {})
    ledger_b = load(REPORT / "PART_B_EXECUTION_LEDGER.json", {})
    hard_stop = active_hard_stop()
    continuation_receipts = [
        valid_false_positive_continuation(), *valid_isolated_continuations()
    ]
    continuation_receipts = [value for value in continuation_receipts if value]
    complete_a = ledger_a.get("status") == "COMPLETED_FROZEN_80_RUN_ROSTER" and len(ledger_a.get("entries", [])) == 80
    complete_b = ledger_b.get("status") == "COMPLETED_FROZEN_35_EPISODE_ROSTER" and len(ledger_b.get("entries", [])) == 35
    if not (complete_a and complete_b) and not hard_stop:
        raise RuntimeError("RQ3_V3_ANALYSIS_FORBIDDEN_BEFORE_LEGAL_EXECUTION_ENDPOINT")
    source = source_revalidation()
    atomic_json(REPORT / "SOURCE_FREEZE_REVALIDATION_RECEIPT.json", source)
    a, aa, a_verdict = analyze_part_a(ledger_a.get("entries", []), complete_a)
    b, ba, b_verdict = analyze_part_b(ledger_b.get("entries", []), complete_b)
    integrity, firewall = build_integrity(ledger_a.get("entries", []), ledger_b.get("entries", []), source)
    atomic_json(REPORT / "PART_A_RESULTS.json", a)
    atomic_json(REPORT / "PART_A_STATISTICAL_ANALYSIS.json", aa)
    atomic_json(REPORT / "PART_B_RESULTS.json", b)
    atomic_json(REPORT / "PART_B_LIFECYCLE_ANALYSIS.json", ba)
    atomic_json(REPORT / "CONTROL_INTEGRITY_RECEIPT.json", integrity)
    atomic_json(REPORT / "TRUE_INTENT_FIREWALL_RECEIPT.json", firewall)
    pre_audit_tree = artifact_tree()
    pre_required = [name for name in REQUIRED_EXECUTION_FILES if name not in {"FINAL_REPORT.md", "FINAL_ARTIFACT_AUDIT.json", "FINAL_VALIDATION_RECEIPT.json"}]
    audit = {
        "schema": "driveclarify.rq3-v3.final-artifact-audit.v1", "pre_report_artifact_tree": pre_audit_tree,
        "required_pre_report_files_present": all((REPORT / name).is_file() for name in pre_required),
        "part_a_raw_run_directories": len(list(PART_A_RUNS.glob("*/attempt_01"))),
        "part_b_raw_episode_directories": len(list(PART_B_RUNS.glob("*/attempt_01"))),
        "raw_authoritative_evaluator_outputs_preserved": all((_resolved(row["source_output"]) / "official_checkpoint.json").is_file() for row in ledger_a.get("entries", []) + ledger_b.get("entries", [])),
        "scientific_retries": 0, "seed_replacements": 0,
        "pass": bool(complete_a and complete_b and source["pass"] and integrity["pass_before_artifact_audit"] and firewall["pass"]),
    }
    audit["status"] = "PASS_FINAL_ARTIFACT_AUDIT" if audit["pass"] else ("STOPPED_PARTIAL_ARTIFACT_AUDIT" if hard_stop else "FAIL_FINAL_ARTIFACT_AUDIT")
    audit["audit_digest"] = digest(audit)
    atomic_json(REPORT / "FINAL_ARTIFACT_AUDIT.json", audit)
    integrity["final_artifact_audit_pass"] = audit["pass"]
    integrity["pass"] = bool(integrity["pass_before_artifact_audit"] and audit["pass"] and firewall["pass"])
    integrity["status"] = "PASS_H_RQ3_C_CONTROL_AND_SOURCE_INTEGRITY" if integrity["pass"] else "FAIL_H_RQ3_C_CONTROL_OR_SOURCE_INTEGRITY"
    integrity["receipt_digest"] = digest({key: value for key, value in integrity.items() if key != "receipt_digest"})
    atomic_json(REPORT / "CONTROL_INTEGRITY_RECEIPT.json", integrity)
    c_verdict = "SUPPORTED" if integrity["pass"] else "NOT_SUPPORTED"
    if b_verdict == "SUPPORTED" and c_verdict != "SUPPORTED":
        b_verdict = "NOT_SUPPORTED"
    status = choose_final_status(a_verdict, b_verdict, c_verdict, hard_stop)
    combined = {
        "schema": "driveclarify.rq3-v3.combined-results.v1", "exact_final_status": status,
        "final_freeze_digest": FINAL_FREEZE_DIGEST, "RQ1": "SUPPORTED_AND_FROZEN", "RQ2": "SUPPORTED_AND_FROZEN",
        "RQ3_V1": "RQ3_PRIMARY_EVALUABILITY_GATE_FAILED", "RQ3_V2": "RQ3_V2_PRIMARY_EVALUABILITY_GATE_FAILED",
        "H_RQ3_A": a_verdict, "H_RQ3_B": b_verdict, "H_RQ3_C": c_verdict,
        "overall_PASS": status == "PASS_RQ3_V3_BENCH2DRIVE_CLOSED_LOOP_VALIDATED",
        "source_status": source["status"], "source_revalidation_digest": source["receipt_digest"],
        "hard_stop": hard_stop or None, "historical_failed_results_overwritten_or_reclassified": False,
        "technical_continuation_receipts": [value["receipt_digest"] for value in continuation_receipts],
    }
    combined["combined_digest"] = digest(combined)
    atomic_json(REPORT / "RQ3_V3_COMBINED_RESULTS.json", combined)
    validation = {
        "schema": "driveclarify.rq3-v3.final-validation-receipt.v1", "status": status,
        "legal_endpoint": bool(complete_a and complete_b) or bool(hard_stop), "full_roster_complete": complete_a and complete_b,
        "mandatory_hard_stop": bool(hard_stop), "entry_receipt_pass": load(REPORT / "FORMAL_EXECUTION_ENTRY_RECEIPT.json", {}).get("pass"),
        "source_revalidation_pass": source["pass"], "artifact_audit_pass": audit["pass"], "control_integrity_pass": integrity["pass"],
        "true_intent_firewall_pass": firewall["pass"], "scientific_retries": 0, "seed_replacements": 0,
        "RQ3_V1_unchanged": True, "RQ3_V2_unchanged": True,
        "preserved_technical_continuation_receipt_count": len(continuation_receipts),
    }
    validation["pass"] = bool(validation["legal_endpoint"] and (validation["full_roster_complete"] or validation["mandatory_hard_stop"]))
    validation["validation_digest"] = digest(validation)
    atomic_json(REPORT / "FINAL_VALIDATION_RECEIPT.json", validation)
    atomic_text(REPORT / "FINAL_REPORT.md", final_report(combined, a, aa, b, ba, integrity, firewall, audit, validation))
    audit["final_report_present"] = True
    audit["all_required_execution_files_present"] = all((REPORT / name).is_file() for name in REQUIRED_EXECUTION_FILES)
    audit["pass"] = bool(audit["pass"] and audit["all_required_execution_files_present"])
    audit["audit_digest"] = digest({key: value for key, value in audit.items() if key != "audit_digest"})
    atomic_json(REPORT / "FINAL_ARTIFACT_AUDIT.json", audit)
    validation["artifact_audit_pass"] = audit["pass"]
    validation["all_required_execution_files_present"] = audit["all_required_execution_files_present"]
    validation["validation_digest"] = digest({key: value for key, value in validation.items() if key != "validation_digest"})
    atomic_json(REPORT / "FINAL_VALIDATION_RECEIPT.json", validation)
    atomic_text(REPORT / "FINAL_REPORT.md", final_report(combined, a, aa, b, ba, integrity, firewall, audit, validation))
    append_log(f"- Final analysis and artifact audit completed only at the legal endpoint: `{status}`.")
    return combined


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("entry-check", "prepare", "continue-after-false-positive", "continue-after-isolated-invalidity", "run", "analyze", "all"))
    args = parser.parse_args()
    if args.phase == "entry-check":
        value = entry_gate()
        print(canonical({"status": value["status"], "check_count": value["check_count"], "failure_count": value["failure_count"], "failed_checks": value["failed_checks"]}))
        return 0 if value["pass"] else 2
    if args.phase in {"prepare", "all"}:
        prepare()
    if args.phase == "continue-after-false-positive":
        value = correct_false_positive_stop()
        print(canonical({"status": value["status"], "affected_run_id": value["affected_run_id"], "receipt_digest": value["receipt_digest"]}))
        return 0
    if args.phase == "continue-after-isolated-invalidity":
        value = continue_after_isolated_invalidity()
        print(canonical({"status": value["status"], "quarantined_run_id": value["quarantined_run_id"], "receipt_digest": value["receipt_digest"]}))
        return 0
    if args.phase in {"run", "all"}:
        run_campaign()
    if args.phase in {"analyze", "all"}:
        result = analyze_and_finalize()
        print(canonical({"exact_final_status": result["exact_final_status"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
