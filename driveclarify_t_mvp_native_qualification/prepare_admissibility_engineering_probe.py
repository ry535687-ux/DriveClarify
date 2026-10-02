"""Freeze one fresh, engineering-only B1/T1 admissibility wiring probe."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any

from driveclarify_t_mvp.canonical import canonical_sha256
from driveclarify_t_mvp_native_qualification.admissibility_evidence import write_once


CASE_ID = "ADMISSIBILITY-ENG-B1-T1-COMPLETE"
BUCKET = "T1_BEFORE_COMMITMENT"
METHOD = "T-B1"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _policy_preservation(prior_freeze: Path, repository_root: Path) -> dict[str, Any]:
    frozen = json.loads(prior_freeze.read_text(encoding="utf-8"))
    preserved_roles = {
        "NATIVE_SIX_METHOD_WRAPPER_DISPATCHER",
        "B6_CANONICAL_RECEIPT_ENCODING",
        "SIX_FROZEN_BASELINE_BINDINGS",
        "B1_B6_FROZEN_BASELINES",
        "G_CONTRACT_V2",
        "G_TERMINAL_REGION_V2",
        "T_B2_FULL_GLOBAL_LOCAL_REPLAN",
        "OBSERVABLE_COMMITMENT",
        "T1_T4_PROSPECTIVE_INJECTOR",
        "NATIVE_T1_T4_INJECTOR",
    }
    rows = [row for row in frozen["files"] if row["runtime_role"] in preserved_roles]
    checks = [
        {
            "runtime_role": row["runtime_role"],
            "relative_path": row["relative_path"],
            "prior_sha256": row["sha256"],
            "current_sha256": _sha(repository_root / row["relative_path"]),
        }
        for row in rows
    ]
    return {
        "prior_freeze_sha256": _sha(prior_freeze),
        "required_roles": sorted(preserved_roles),
        "checks": checks,
        "all_frozen_scientific_policy_and_timing_files_exact": (
            {row["runtime_role"] for row in checks} == preserved_roles
            and all(row["prior_sha256"] == row["current_sha256"] for row in checks)
        ),
    }


def prepare(
    *,
    repository_root: Path,
    report: Path,
    seed: int,
    rpc_port: int,
    freeze_name: str,
) -> None:
    if seed in {9468329, 9674011, 9674027}:
        raise ValueError("QUARANTINED_OLD_DEV_SEED_FORBIDDEN")
    route = (
        repository_root
        / "reports/driveclarify_v11_sequence21_closure/engineering_routes/"
        "V11-DEV-TOWN05-01.xml"
    )
    if not route.is_file():
        raise FileNotFoundError("ENGINEERING_ROUTE_MISSING")
    identity = {"seed": int(seed), "bucket": BUCKET}
    runtime_case = {
        "attempt_id": CASE_ID + "-A1",
        "baseline_id": METHOD,
        "case_id": CASE_ID,
        "episode_id": CASE_ID + "-E1",
        "global_destination_identity": "official-route-311001-destination",
        "route_path": str(route),
        "route_subset": "311001",
        "run_id": "rq2_admissibility_eng_" + canonical_sha256(identity)[:12],
        "scene_id": "TOWN05-RQ2-ROUTE-311001",
        "seed": int(seed),
        "updated_branch_identity": "town05-road39-adjacent-lane2-delayed-merge",
    }
    oracle_case = {
        "episode_id": runtime_case["episode_id"],
        "expected_oracle_event": "EARLY_COMMON_DUAL_FEASIBLE_REGION_ENTERED",
        "injection_event_id": "INJECT-" + canonical_sha256(identity)[:20],
        "instruction_new": (
            "Use the adjacent right corridor and merge back before the unchanged destination."
        ),
        "instruction_old": "Continue in the current corridor to the destination.",
        "timing_bucket": BUCKET,
        "trigger": {"kind": "ENTER_ROAD_LANE", "road_id": 48, "lane_id": 1},
        "update_event_id": "UPDATE-" + canonical_sha256(identity)[:20],
    }
    engineering = report / "engineering"
    runtime_path = engineering / "runtime_cases.json"
    oracle_path = engineering / "oracle_injection_manifest.json"
    write_once(
        runtime_path,
        {
            "schema_version": "driveclarify.rq2.admissibility_engineering_runtime.v1",
            "engineering_only": True,
            "future_dev_or_test_eligible": False,
            "cases": {CASE_ID: runtime_case},
        },
    )
    write_once(
        oracle_path,
        {
            "schema_version": "driveclarify.rq2.admissibility_engineering_oracle.v1",
            "engineering_only": True,
            "future_dev_or_test_eligible": False,
            "cases": {CASE_ID: oracle_case},
        },
    )
    case_config_sha = canonical_sha256(runtime_case)
    registry_path = engineering / "ENGINEERING_EXPOSURE_REGISTRY.json"
    ledger_path = engineering / "ENGINEERING_EXECUTION_LEDGER.json"
    gate_path = engineering / "ENGINEERING_PREEXECUTION_GATE.json"
    write_once(
        registry_path,
        {
            "schema_version": "driveclarify.rq2.exposure_registry.v2",
            "immutable": True,
            "cases": [
                {
                    "engineering_case_id": CASE_ID,
                    "seed": int(seed),
                    "eligibility": {
                        "engineering_native": True,
                        "t_mvp_dev": False,
                        "t_formal_test": False,
                        "future_rq3_formal": False,
                    },
                }
            ],
        },
    )
    write_once(
        ledger_path,
        {
            "schema_version": "driveclarify.rq2.native_execution_ledger.v2",
            "execution_mode": "ENGINEERING_SMOKE",
            "immutable": True,
            "maximum_native_probes": 1,
            "maximum_pre_agent_attempts_per_probe": 3,
            "post_agent_retry_allowed": False,
            "probes": [
                {
                    "probe_id": "ADMISSIBILITY-ENG-001",
                    "engineering_case_id": CASE_ID,
                    "baseline_id": METHOD,
                    "seed": int(seed),
                    "case_config_sha256": case_config_sha,
                    "runtime_config_sha256": _sha(runtime_path),
                    "oracle_manifest_sha256": _sha(oracle_path),
                    "injection_event_id": oracle_case["injection_event_id"],
                    "update_event_id": oracle_case["update_event_id"],
                    "rpc_port": int(rpc_port),
                    "status": "NOT_STARTED",
                }
            ],
        },
    )
    write_once(
        gate_path,
        {
            "schema_version": "driveclarify.rq2.native_preexecution_gate.v2",
            "final_status": "PASS_NATIVE_ENGINEERING_PREEXECUTION_GATE",
            "native_execution_started": False,
            "future_episode_count": 1,
            "scientific_retries_allowed": False,
            "pre_agent_identical_retry_ceiling": 3,
        },
    )
    write_once(
        report / "ENGINEERING_PROBE_ROSTER.json",
        {
            "schema_version": "driveclarify.rq2.admissibility_engineering_roster.v1",
            "frozen_before_exposure": True,
            "maximum_agent_exposures": 1,
            "scientific_episode": False,
            "case": {
                "ordinal": 1,
                "case_id": CASE_ID,
                "episode_id": runtime_case["episode_id"],
                "method_id": METHOD,
                "timing_bucket": BUCKET,
                "seed": int(seed),
            },
        },
    )
    write_once(
        report / "ENGINEERING_SEED_SELECTION_RECEIPT.json",
        {
            "schema_version": "driveclarify.rq2.admissibility_engineering_seed.v1",
            "seed": int(seed),
            "randomly_generated_before_search": True,
            "prior_exact_string_occurrence_count": 0,
            "engineering_only": True,
            "future_dev_or_test_eligible": False,
            "old_dev_seed": False,
            "selected_from_old_dev_outcomes": False,
        },
    )
    prior_freeze = (
        repository_root
        / "reports/driveclarify_rq2_six_method_native_readiness_repair_and_dev_pilot_v2/"
        "PRACTICAL_RUNTIME_FREEZE.json"
    )
    preservation = _policy_preservation(prior_freeze, repository_root)
    if not preservation["all_frozen_scientific_policy_and_timing_files_exact"]:
        raise RuntimeError("FROZEN_SCIENTIFIC_POLICY_OR_TIMING_DRIFT")
    controlled = [
        repository_root / "driveclarify_t_mvp_native_qualification/agent.py",
        repository_root / "driveclarify_t_mvp_native_qualification/receipt_encoding.py",
        repository_root / "driveclarify_t_mvp_native_qualification/baseline_bindings.py",
        repository_root / "driveclarify_t_mvp/baselines.py",
        repository_root / "driveclarify_t_mvp/g_binding_v2.py",
        repository_root / "driveclarify_t_mvp_native_qualification/g_terminal_regions_v2.json",
        repository_root / "driveclarify_t_mvp/full_replan.py",
        repository_root / "driveclarify_t_mvp/commitment.py",
        repository_root / "driveclarify_t_mvp/injector.py",
        repository_root / "driveclarify_t_mvp_native_qualification/injector_process.py",
        repository_root / "driveclarify_t_mvp/admissibility.py",
        repository_root / "driveclarify_t_mvp_native_qualification/admissibility_evidence.py",
        repository_root / "driveclarify_t_mvp_native_qualification/preexecution_guard.py",
        repository_root / "driveclarify_t_mvp_native_qualification/run_native_episode.sh",
        repository_root / "driveclarify_t_mvp_native_qualification/rq2_dev_postprocess.py",
        route,
        runtime_path,
        oracle_path,
        registry_path,
        ledger_path,
        gate_path,
    ]
    rows = [
        {
            "absolute_path": str(path.resolve()),
            "relative_path": str(path.resolve().relative_to(repository_root)),
            "closure_category": "ADMISSIBILITY_GATE_ENGINEERING_CONTROLLED",
            "runtime_role": "ADMISSIBILITY_GATE_CONTROLLED_FILE",
            "tracked": False,
            "sha256": _sha(path),
            "size_bytes": path.stat().st_size,
        }
        for path in controlled
    ]
    write_once(
        report / freeze_name,
        {
            "schema_version": "driveclarify.rq2.admissibility_gate_runtime_freeze.v1",
            "frozen_before_engineering_probe": True,
            "git_head": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=repository_root, text=True
            ).strip(),
            "scientific_policy_preservation": preservation,
            "authorized_protocol_files_changed": [
                "driveclarify_t_mvp/admissibility.py",
                "driveclarify_t_mvp_native_qualification/admissibility_evidence.py",
                "driveclarify_t_mvp_native_qualification/preexecution_guard.py",
                "driveclarify_t_mvp_native_qualification/run_native_episode.sh",
                "driveclarify_t_mvp_native_qualification/rq2_dev_postprocess.py",
            ],
            "file_count": len(rows),
            "files": rows,
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--seed", required=True, type=int)
    parser.add_argument("--rpc-port", required=True, type=int)
    parser.add_argument(
        "--freeze-name", default="PRACTICAL_RUNTIME_FREEZE_GATE_V1.json"
    )
    args = parser.parse_args()
    prepare(
        repository_root=Path(args.repository_root).resolve(),
        report=Path(args.report).resolve(),
        seed=args.seed,
        rpc_port=args.rpc_port,
        freeze_name=args.freeze_name,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
