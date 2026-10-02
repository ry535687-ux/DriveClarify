"""Freeze the single engineering-only RQ2 V3 native measurement probe."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from driveclarify_t_mvp.canonical import canonical_sha256


CASE_ID = "V3-NMQ-ENG-T1-TB2-001"
EPISODE_ID = CASE_ID + "-E1"
METHOD = "T-B2"
BUCKET = "T1_BEFORE_COMMITMENT"
SEED = 1776183095
RPC_PORT = 2740
PRIOR_GATE = (
    "reports/driveclarify_rq2_episode_admissibility_and_analysis_gate_v1/"
    "PRACTICAL_RUNTIME_FREEZE_GATE_V3.json"
)


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_once(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _freeze_row(repository_root: Path, path: Path, role: str) -> dict[str, Any]:
    resolved = path.resolve()
    return {
        "absolute_path": str(resolved),
        "relative_path": str(resolved.relative_to(repository_root)),
        "closure_category": "V3_NATIVE_MEASUREMENT_CONTROLLED",
        "runtime_role": role,
        "tracked": False,
        "sha256": _sha(resolved),
        "size_bytes": resolved.stat().st_size,
    }


def prepare(repository_root: Path, report: Path) -> None:
    if SEED in {9468329, 9674011, 9674027}:
        raise RuntimeError("OLD_DEV_SEED_FORBIDDEN")
    route = repository_root / (
        "reports/driveclarify_v11_sequence21_closure/engineering_routes/"
        "V11-DEV-TOWN05-01.xml"
    )
    engineering = report / "engineering"
    identity = {
        "stage": "RQ2_V3_NATIVE_MEASUREMENT_QUALIFICATION_V1",
        "case_id": CASE_ID,
        "seed": SEED,
        "method": METHOD,
        "timing_bucket": BUCKET,
    }
    token = canonical_sha256(identity)
    runtime_case = {
        "attempt_id": CASE_ID + "-A1",
        "baseline_id": METHOD,
        "case_id": CASE_ID,
        "episode_id": EPISODE_ID,
        "global_destination_identity": "official-route-311001-destination",
        "route_path": str(route),
        "route_subset": "311001",
        "run_id": "rq2_v3_nmq_" + token[:12],
        "scene_id": "TOWN05-RQ2-ROUTE-311001",
        "seed": SEED,
        "updated_branch_identity": "town05-road39-adjacent-lane2-delayed-merge",
    }
    oracle_case = {
        "episode_id": EPISODE_ID,
        "expected_oracle_event": "EARLY_COMMON_DUAL_FEASIBLE_REGION_ENTERED",
        "injection_event_id": "V3-NMQ-INJECT-" + token[:20],
        "instruction_new": (
            "Use the adjacent right corridor and merge back before the unchanged destination."
        ),
        "instruction_old": "Continue in the current corridor to the destination.",
        "timing_bucket": BUCKET,
        "trigger": {"kind": "ENTER_ROAD_LANE", "lane_id": 1, "road_id": 48},
        "update_event_id": "V3-NMQ-UPDATE-" + token[:20],
    }
    runtime_path = engineering / "runtime_cases.json"
    oracle_path = engineering / "oracle_injection_manifest.json"
    registry_path = engineering / "ENGINEERING_EXPOSURE_REGISTRY.json"
    ledger_path = report / "ENGINEERING_PROBE_LEDGER.json"
    gate_path = engineering / "ENGINEERING_PREEXECUTION_GATE.json"
    _write_once(runtime_path, {
        "schema_version": "driveclarify.rq2.v3_native_measurement.runtime.v1",
        "engineering_only": True,
        "future_dev_or_test_eligible": False,
        "cases": {CASE_ID: runtime_case},
    })
    _write_once(oracle_path, {
        "schema_version": "driveclarify.rq2.v3_native_measurement.oracle.v1",
        "engineering_only": True,
        "future_dev_or_test_eligible": False,
        "cases": {CASE_ID: oracle_case},
    })
    _write_once(registry_path, {
        "schema_version": "driveclarify.rq2.exposure_registry.v2",
        "immutable": True,
        "cases": [{
            "engineering_case_id": CASE_ID,
            "seed": SEED,
            "eligibility": {
                "engineering_native": True,
                "t_mvp_dev": False,
                "t_formal_test": False,
                "future_rq3_formal": False,
            },
            "prior_seed_designation": (
                "PERMANENT_ENGINEERING_ONLY_ADMISSIBILITY_QUALIFICATION_SEED"
            ),
            "prior_exposed_cell": "T-B1/T1",
            "this_cell": "T-B2/T1",
            "result_retry": False,
        }],
    })
    probe = {
        "probe_id": "V3-NMQ-ENG-001",
        "engineering_case_id": CASE_ID,
        "baseline_id": METHOD,
        "seed": SEED,
        "case_config_sha256": canonical_sha256(runtime_case),
        "runtime_config_sha256": _sha(runtime_path),
        "oracle_manifest_sha256": _sha(oracle_path),
        "injection_event_id": oracle_case["injection_event_id"],
        "update_event_id": oracle_case["update_event_id"],
        "rpc_port": RPC_PORT,
        "status": "NOT_STARTED",
        "run_ordinal": 1,
    }
    _write_once(ledger_path, {
        "schema_version": "driveclarify.rq2.native_execution_ledger.v2",
        "execution_mode": "ENGINEERING_SMOKE",
        "immutable": True,
        "maximum_native_probes": 1,
        "maximum_pre_agent_attempts_per_probe": 3,
        "post_agent_retry_allowed": False,
        "probes": [probe],
    })
    _write_once(gate_path, {
        "schema_version": "driveclarify.rq2.native_preexecution_gate.v2",
        "final_status": "PASS_NATIVE_ENGINEERING_PREEXECUTION_GATE",
        "native_execution_started": False,
        "future_episode_count": 1,
        "scientific_retries_allowed": False,
        "pre_agent_identical_retry_ceiling": 3,
        "rq2_t_window_seconds": 35,
        "rq2_l_authorized": False,
    })
    _write_once(report / "ENGINEERING_PROBE_ROSTER.json", {
        "schema_version": "driveclarify.rq2.v3_native_measurement.roster.v1",
        "frozen_before_exposure": True,
        "maximum_agent_exposures": 1,
        "scientific_episode": False,
        "selection_basis": (
            "NEW_T_B2_T1_CELL_EXERCISES_FULL_REPLAN_AND_CONSUMPTION_WITH_EXISTING_"
            "PERMANENTLY_ENGINEERING_ONLY_SEED"
        ),
        "case": {
            "ordinal": 1,
            "case_id": CASE_ID,
            "episode_id": EPISODE_ID,
            "method_id": METHOD,
            "timing_bucket": BUCKET,
            "seed": SEED,
            "agent_exposure_limit": 1,
            "scientific_retry_limit": 0,
        },
    })

    prior = json.loads((repository_root / PRIOR_GATE).read_text(encoding="utf-8"))
    config_names = {
        "runtime_cases.json", "oracle_injection_manifest.json",
        "ENGINEERING_EXPOSURE_REGISTRY.json", "ENGINEERING_EXECUTION_LEDGER.json",
        "ENGINEERING_PREEXECUTION_GATE.json",
    }
    rows = [row for row in prior["files"] if Path(row["relative_path"]).name not in config_names]
    rows.extend([
        _freeze_row(repository_root, runtime_path, "V3_NMQ_RUNTIME_CONFIG"),
        _freeze_row(repository_root, oracle_path, "V3_NMQ_ORACLE_MANIFEST"),
        _freeze_row(repository_root, registry_path, "V3_NMQ_EXPOSURE_REGISTRY"),
        _freeze_row(repository_root, ledger_path, "V3_NMQ_EXECUTION_LEDGER"),
        _freeze_row(repository_root, gate_path, "V3_NMQ_PREEXECUTION_GATE"),
    ])
    if len(rows) != 21:
        raise RuntimeError(f"FROZEN_RUNTIME_FILE_COUNT_CHANGED:{len(rows)}")
    for row in rows:
        path = Path(row["absolute_path"])
        if _sha(path) != row["sha256"] or path.stat().st_size != row["size_bytes"]:
            raise RuntimeError("PRE_NATIVE_FREEZE_MISMATCH:" + str(path))
    _write_once(report / "PRACTICAL_RUNTIME_FREEZE_GATE_V3.json", {
        "schema_version": "driveclarify.rq2.v3_native_measurement.freeze.v1",
        "frozen_before_engineering_probe": True,
        "git_head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repository_root, text=True
        ).strip(),
        "source_gate_inherited": PRIOR_GATE,
        "file_count": len(rows),
        "files": rows,
    })


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    prepare(Path(args.repository_root).resolve(), Path(args.report).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
