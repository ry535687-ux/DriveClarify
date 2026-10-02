"""Prepare the single authorized engineering-only safety snapshot probe."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path("/home/buaa/wrh/DriveClarify")
REPORT = ROOT / "reports/driveclarify_rq2_v3_forced_horizon_safety_snapshot_qualification_v1"
ENGINEERING = REPORT / "engineering"
CASE_ID = "RQ2-SAFETY-ENG-T1-TB4-001"
RPC_PORT = 2780


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def canonical_sha(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def main() -> None:
    seed_receipt = json.loads((ROOT / "reports/driveclarify_rq2_episode_admissibility_and_analysis_gate_v1/ENGINEERING_SEED_SELECTION_RECEIPT.json").read_text())
    if seed_receipt.get("engineering_only") is not True or seed_receipt.get("future_dev_or_test_eligible") is not False or seed_receipt.get("old_dev_seed") is not False:
        raise SystemExit("ENGINEERING_SEED_AUTHORITY_INVALID")
    seed = int(seed_receipt["seed"])
    fresh = json.loads((ROOT / "reports/driveclarify_rq2_v3_fresh_dev_roster_and_preexecution_freeze_v1/FRESH_DEV_SEED_FREEZE.json").read_text())
    if seed in {int(row["seed"]) for row in fresh["seeds"]}:
        raise SystemExit("RESERVED_DEV_SEED_FORBIDDEN")
    route = ROOT / "reports/driveclarify_v11_sequence21_closure/engineering_routes/V11-DEV-TOWN05-01.xml"
    token = hashlib.sha256((CASE_ID + ":forced-horizon-safety-v1").encode()).hexdigest()[:20]
    case = {
        "attempt_id": CASE_ID + "-A1",
        "baseline_id": "T-B4",
        "case_id": CASE_ID,
        "episode_id": CASE_ID + "-E1",
        "global_destination_identity": "official-route-311001-destination",
        "route_path": str(route),
        "route_subset": "311001",
        "run_id": "rq2_safety_snapshot_" + token[:12],
        "scene_id": "TOWN05-RQ2-ROUTE-311001",
        "seed": seed,
        "updated_branch_identity": "town05-road39-adjacent-lane2-delayed-merge"
    }
    runtime = {
        "cases": {CASE_ID: case},
        "engineering_only": True,
        "future_dev_or_test_eligible": False,
        "schema_version": "driveclarify.rq2.forced_horizon_safety.runtime.v1"
    }
    oracle_case = {
        "episode_id": case["episode_id"],
        "expected_oracle_event": "EARLY_COMMON_DUAL_FEASIBLE_REGION_ENTERED",
        "injection_event_id": "RQ2-SAFETY-INJECT-" + token,
        "instruction_new": "Use the adjacent right corridor and merge back before the unchanged destination.",
        "instruction_old": "Continue in the current corridor to the destination.",
        "timing_bucket": "T1_BEFORE_COMMITMENT",
        "trigger": {"kind": "ENTER_ROAD_LANE", "lane_id": 1, "road_id": 48},
        "update_event_id": "RQ2-SAFETY-UPDATE-" + token
    }
    oracle = {
        "cases": {CASE_ID: oracle_case},
        "engineering_only": True,
        "future_dev_or_test_eligible": False,
        "schema_version": "driveclarify.rq2.forced_horizon_safety.oracle.v1"
    }
    runtime_path = ENGINEERING / "runtime_cases.json"
    oracle_path = ENGINEERING / "oracle_injection_manifest.json"
    write_json(runtime_path, runtime)
    write_json(oracle_path, oracle)
    registry = {
        "cases": [{
            "eligibility": {"engineering_native": True, "future_rq3_formal": False, "t_formal_test": False, "t_mvp_dev": False},
            "engineering_case_id": CASE_ID,
            "prior_exposed_cells": ["T-B1/T1", "T-B2/T1"],
            "prior_seed_designation": "PERMANENT_ENGINEERING_ONLY_ADMISSIBILITY_QUALIFICATION_SEED",
            "result_retry": False,
            "seed": seed,
            "this_cell": "T-B4/T1"
        }],
        "immutable": True,
        "schema_version": "driveclarify.rq2.exposure_registry.v2"
    }
    gate = {
        "final_status": "PASS_NATIVE_ENGINEERING_PREEXECUTION_GATE",
        "future_episode_count": 1,
        "native_execution_started": False,
        "pre_agent_identical_retry_ceiling": 3,
        "rq2_l_authorized": False,
        "rq2_t_window_seconds": 35,
        "schema_version": "driveclarify.rq2.native_preexecution_gate.v2",
        "scientific_retries_allowed": False
    }
    write_json(ENGINEERING / "ENGINEERING_EXPOSURE_REGISTRY.json", registry)
    write_json(ENGINEERING / "ENGINEERING_PREEXECUTION_GATE.json", gate)
    probe = {
        "baseline_id": case["baseline_id"],
        "case_config_sha256": canonical_sha(case),
        "engineering_case_id": CASE_ID,
        "injection_event_id": oracle_case["injection_event_id"],
        "oracle_manifest_sha256": file_sha(oracle_path),
        "probe_id": "RQ2-SAFETY-ENG-001",
        "rpc_port": RPC_PORT,
        "run_ordinal": 1,
        "runtime_config_sha256": file_sha(runtime_path),
        "seed": seed,
        "status": "PROSPECTIVELY_FROZEN_NOT_STARTED",
        "update_event_id": oracle_case["update_event_id"]
    }
    ledger = {
        "execution_mode": "ENGINEERING_SMOKE",
        "immutable": True,
        "maximum_native_probes": 1,
        "maximum_pre_agent_attempts_per_probe": 3,
        "post_agent_retry_allowed": False,
        "probes": [probe],
        "schema_version": "driveclarify.rq2.native_execution_ledger.v2"
    }
    write_json(REPORT / "ENGINEERING_PROBE_LEDGER.json", ledger)
    print(json.dumps({"status": "PASS", "case_id": CASE_ID, "rpc_port": RPC_PORT, "seed_authority": str(seed_receipt), "seed": seed}, sort_keys=True))


if __name__ == "__main__":
    main()
