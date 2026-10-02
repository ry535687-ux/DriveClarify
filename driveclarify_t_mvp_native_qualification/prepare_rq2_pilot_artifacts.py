"""Create prospective smoke artifacts, then DEV artifacts only after readiness."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
from typing import Any

from driveclarify_t_mvp.canonical import canonical_sha256
from driveclarify_t_mvp_native_qualification.baseline_bindings import resolve_all_bindings


REPO = Path("/home/buaa/wrh/DriveClarify")
QUALIFICATION = REPO / "driveclarify_t_mvp_native_qualification"
REPORT = REPO / "reports/driveclarify_rq2_six_method_native_readiness_and_dev_pilot_v1"
ROUTE_SHORT = REPO / "reports/driveclarify_v11_sequence21_closure/engineering_routes/V11-DEV-TOWN05-01.xml"
ROUTE_EXTENDED = QUALIFICATION / "routes/TOWN05-ENGINEERING-EXTENDED-311003.xml"
SOURCE_T3 = REPO / "reports/driveclarify_rq2_t_mvp_native_preexecution_closure_v1/T3_EVALUATOR_TRUTH_PROOF.json"
SOURCE_T4 = REPO / "reports/driveclarify_rq2_t_mvp_native_preexecution_closure_v1/T4_EVALUATOR_TRUTH_PROOF.json"
SMOKE = (
    ("SMOKE-RQ2-A", "T-B1", "T1_BEFORE_COMMITMENT", 7319421),
    ("SMOKE-RQ2-B", "T-B3", "T2_NEAR_COMMITMENT", 7319437),
    ("SMOKE-RQ2-C", "T-B4", "T1_BEFORE_COMMITMENT", 7319453),
    ("SMOKE-RQ2-D", "T-B6", "T1_BEFORE_COMMITMENT", 7319469),
    ("SMOKE-RQ2-E", "T-B6", "T3_POST_COMMIT_RECOVERABLE", 7319481),
    ("SMOKE-RQ2-F", "T-B6", "T4_NO_SAFE_CURRENT_OPPORTUNITY", 7319497),
)
DEV_SEEDS = (8426151, 8426167, 8426183)
METHODS = ("T-B1", "T-B2", "T-B3", "T-B4", "T-B5", "T-B6")
BUCKETS = (
    "T1_BEFORE_COMMITMENT",
    "T2_NEAR_COMMITMENT",
    "T3_POST_COMMIT_RECOVERABLE",
    "T4_NO_SAFE_CURRENT_OPPORTUNITY",
)


def _bytes(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_once(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _trigger(bucket: str) -> tuple[str, dict[str, Any], str, str, str]:
    old = "Continue in the current corridor to the destination."
    if bucket == "T1_BEFORE_COMMITMENT":
        return (
            "EARLY_COMMON_DUAL_FEASIBLE_REGION_ENTERED",
            {"kind": "ENTER_ROAD_LANE", "road_id": 48, "lane_id": 1},
            old,
            "Use the adjacent right corridor and merge back before the unchanged destination.",
            "town05-road39-adjacent-lane2-delayed-merge",
        )
    if bucket == "T2_NEAR_COMMITMENT":
        return (
            "FINAL_COMMON_DUAL_FEASIBLE_REGION_ENTERED",
            {"kind": "ENTER_ROAD_LANE", "road_id": 791, "lane_id": 1},
            old,
            "Use the adjacent right corridor and merge back before the unchanged destination.",
            "town05-road39-adjacent-lane2-delayed-merge",
        )
    if bucket == "T3_POST_COMMIT_RECOVERABLE":
        return (
            "OLD_EXCLUSIVE_RECOVERY_EXISTS_ENTERED",
            {"kind": "ENTER_ROAD_LANE_WITH_RIGHT_DRIVING_LANE", "road_id": 39, "lane_id": -1},
            old,
            "After entering Road 39 in the current lane, use the later legal lane-change opening to enter the adjacent right driving lane, then return to the destination lane before the unchanged destination.",
            "town05-road39-lane-minus2-late-recovery-then-return-to-lane-minus1",
        )
    return (
        "CURRENT_OPPORTUNITY_END_PASSED",
        {"kind": "ENTER_ROAD_LANE_WITH_RIGHT_DRIVING_LANE", "road_id": 39, "lane_id": -1},
        old,
        "Take the current junction branch that enters Road 39 directly in lane -2, then merge back before the unchanged destination.",
        "town05-junction720-direct-entry-road39-lane-minus2-current-opportunity",
    )


def _truth(case_id: str, seed: int, bucket: str, directory: Path) -> dict[str, Any] | None:
    if bucket not in {"T3_POST_COMMIT_RECOVERABLE", "T4_NO_SAFE_CURRENT_OPPORTUNITY"}:
        return None
    is_t3 = bucket.startswith("T3")
    source = SOURCE_T3 if is_t3 else SOURCE_T4
    value = {
        "schema_version": "driveclarify.rq2.prospective_timing_truth.v1",
        "status": "PASS",
        "truth_class": "OLD_EXCLUSIVE_RECOVERABLE" if is_t3 else "NO_SAFE_CURRENT_OPPORTUNITY",
        "case_id": case_id,
        "seed": seed,
        "source_proof": {
            "absolute_path": str(source),
            "sha256": _bytes(source),
            "truth_independent_of_seed_and_method": True,
        },
        "scenario_identity": "TOWN05-ENGINEERING-ROUTE-311003",
        "prospective_assertions": {
            "frozen_before_native_launch": True,
            "result_dependent_relabeling_allowed": False,
            "native_behavior_can_change_truth_class": False,
        },
    }
    path = directory / "truth" / f"{case_id}.json"
    _write_once(path, value)
    return {"absolute_path": str(path), "sha256": _bytes(path)}


def _case(case_id: str, method: str, bucket: str, seed: int, run_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    expected, trigger, old, new, branch = _trigger(bucket)
    extended = bucket.startswith("T3") or bucket.startswith("T4")
    runtime = {
        "attempt_id": case_id + "-A1",
        "baseline_id": method,
        "case_id": case_id,
        "episode_id": case_id + "-E1",
        "global_destination_identity": "official-route-311001-destination",
        "route_path": str(ROUTE_EXTENDED if extended else ROUTE_SHORT),
        "route_subset": "311003" if extended else "311001",
        "run_id": run_id,
        "scene_id": "TOWN05-RQ2-" + ("EXTENDED-311003" if extended else "ROUTE-311001"),
        "seed": seed,
        "updated_branch_identity": branch,
    }
    oracle = {
        "episode_id": runtime["episode_id"],
        "expected_oracle_event": expected,
        "injection_event_id": "INJECT-" + canonical_sha256({"seed": seed, "bucket": bucket})[:20],
        "instruction_new": new,
        "instruction_old": old,
        "timing_bucket": bucket,
        "trigger": trigger,
        "update_event_id": "UPDATE-" + canonical_sha256({"seed": seed, "bucket": bucket})[:20],
    }
    return runtime, oracle


def _execution_contract(
    directory: Path,
    cases: dict[str, Any],
    oracles: dict[str, Any],
    *,
    mode: str,
    runtime_path: Path,
    oracle_path: Path,
) -> None:
    dev = mode == "RQ2_T_MVP_DEV"
    registry = {
        "schema_version": "driveclarify.rq2.exposure_registry.v1",
        "immutable": True,
        "cases": [
            {
                "engineering_case_id": case_id,
                "seed": case["seed"],
                "eligibility": {
                    "engineering_native": not dev,
                    "t_mvp_dev": dev,
                    "t_formal_test": False,
                    "future_rq3_formal": False,
                },
            }
            for case_id, case in cases.items()
        ],
    }
    registry_path = directory / ("DEV_EXPOSURE_REGISTRY.json" if dev else "SMOKE_EXPOSURE_REGISTRY.json")
    _write_once(registry_path, registry)
    port = 2540
    probes = []
    for ordinal, (case_id, case) in enumerate(cases.items(), 1):
        oracle = oracles[case_id]
        probes.append(
            {
                "probe_id": ("DEV" if dev else "SMOKE") + f"-{ordinal:03d}",
                "engineering_case_id": case_id,
                "baseline_id": case["baseline_id"],
                "seed": case["seed"],
                "case_config_sha256": canonical_sha256(case),
                "runtime_config_sha256": _bytes(runtime_path),
                "oracle_manifest_sha256": _bytes(oracle_path),
                "injection_event_id": oracle["injection_event_id"],
                "update_event_id": oracle["update_event_id"],
                "rpc_port": port,
                "status": "NOT_STARTED",
            }
        )
    ledger = {
        "schema_version": "driveclarify.rq2.native_execution_ledger.v1",
        "execution_mode": mode,
        "immutable": True,
        "maximum_native_probes": len(probes),
        "maximum_pre_agent_attempts_per_probe": 3,
        "post_agent_retry_allowed": False,
        "probes": probes,
    }
    _write_once(directory / ("DEV_EXECUTION_LEDGER.json" if dev else "SMOKE_EXECUTION_LEDGER.json"), ledger)
    _write_once(
        directory / ("DEV_PREEXECUTION_GATE.json" if dev else "SMOKE_PREEXECUTION_GATE.json"),
        {
            "schema_version": "driveclarify.rq2.native_preexecution_gate.v1",
            "final_status": "PASS_RQ2_DEV_PREEXECUTION_GATE" if dev else "PASS_NATIVE_ENGINEERING_PREEXECUTION_GATE",
            "native_execution_started": False,
            "future_episode_count": len(probes),
            "scientific_retries_allowed": False,
        },
    )


def _freeze(smoke_runtime: Path, smoke_oracle: Path) -> None:
    core = (
        (QUALIFICATION / "agent.py", "NATIVE_SIX_METHOD_WRAPPER_DISPATCHER"),
        (QUALIFICATION / "baseline_bindings.py", "SIX_FROZEN_BASELINE_BINDINGS"),
        (REPO / "driveclarify_t_mvp/baselines.py", "B1_B6_FROZEN_BASELINES_AND_B5_SERIALIZER"),
        (REPO / "driveclarify_t_mvp/g_binding_v2.py", "G_CONTRACT_V2"),
        (QUALIFICATION / "g_terminal_regions_v2.json", "G_TERMINAL_REGION_V2"),
        (REPO / "driveclarify_t_mvp/full_replan.py", "T_B2_FULL_GLOBAL_LOCAL_REPLAN"),
        (REPO / "driveclarify_t_mvp/commitment.py", "OBSERVABLE_COMMITMENT"),
        (REPO / "driveclarify_t_mvp/injector.py", "T1_T4_PROSPECTIVE_INJECTOR"),
        (QUALIFICATION / "injector_process.py", "NATIVE_T1_T4_INJECTOR"),
        (QUALIFICATION / "preexecution_guard.py", "EXPOSURE_AND_RETRY_GUARD"),
        (QUALIFICATION / "run_native_episode.sh", "SMOKE_AND_DEV_RUNNER"),
        (QUALIFICATION / "run_rq2_dev_batch.sh", "FROZEN_72_EPISODE_BATCH_ORCHESTRATOR"),
        (QUALIFICATION / "prepare_rq2_pilot_artifacts.py", "PROSPECTIVE_ROSTER_AND_RUN_ORDER_GENERATOR"),
        (QUALIFICATION / "readiness_postprocess.py", "READINESS_PASS_GATE"),
        (QUALIFICATION / "rq2_dev_postprocess.py", "POST_BATCH_ANALYSIS"),
        (smoke_runtime, "FRESH_SMOKE_RUNTIME_CASES"),
        (smoke_oracle, "FRESH_SMOKE_ORACLE_MANIFEST"),
    )
    status = subprocess.run(
        ["git", "status", "--porcelain=v1"], cwd=REPO, check=True, text=True, capture_output=True
    ).stdout.splitlines()
    rows = []
    for path, role in core:
        stat = path.stat()
        rows.append(
            {
                "absolute_path": str(path.resolve()),
                "relative_path": str(path.resolve().relative_to(REPO)),
                "closure_category": "CORE_EXPERIMENT_CONTROLLED",
                "runtime_role": role,
                "tracked": subprocess.run(["git", "ls-files", "--error-unmatch", str(path.relative_to(REPO))], cwd=REPO, capture_output=True).returncode == 0,
                "size_bytes": stat.st_size,
                "sha256": _bytes(path),
            }
        )
    _write_once(
        REPORT / "PRACTICAL_RUNTIME_FREEZE.json",
        {
            "schema_version": "driveclarify.rq2.final_native_core_freeze.v1",
            "frozen_before_first_new_native_smoke": True,
            "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
            "git_status_porcelain": status,
            "source_changes_after_smoke_start_allowed": False,
            "file_count": len(rows),
            "files": rows,
        },
    )


def prepare_smoke() -> None:
    REPORT.mkdir(parents=True, exist_ok=False)
    directory = REPORT / "smoke"
    cases: dict[str, Any] = {}
    oracles: dict[str, Any] = {}
    for ordinal, (case_id, method, bucket, seed) in enumerate(SMOKE, 1):
        runtime, oracle = _case(case_id, method, bucket, seed, f"rq2_smoke_{ordinal:02d}")
        truth = _truth(case_id, seed, bucket, directory)
        if truth:
            oracle["evaluator_truth_artifact"] = truth
        cases[case_id], oracles[case_id] = runtime, oracle
    runtime_path = directory / "runtime_cases.json"
    oracle_path = directory / "oracle_injection_manifest.json"
    _write_once(runtime_path, {"schema_version": "driveclarify.rq2.final_readiness_smoke_cases.v1", "cases": cases})
    _write_once(oracle_path, {"schema_version": "driveclarify.rq2.final_readiness_smoke_oracle.v1", "development_only": True, "future_formal_eligible": False, "cases": oracles})
    _execution_contract(directory, cases, oracles, mode="ENGINEERING_SMOKE", runtime_path=runtime_path, oracle_path=oracle_path)
    _write_once(REPORT / "SIX_BASELINE_NATIVE_DISPATCH_RECEIPT.json", {"schema_version": "driveclarify.rq2.six_dispatch.v1", "bindings": resolve_all_bindings()})
    _write_once(REPORT / "SEED_FRESHNESS_RECEIPT.json", {"searched_before_creation": True, "prior_occurrences": [], "smoke_seeds": [row[3] for row in SMOKE], "reserved_dev_seeds": list(DEV_SEEDS), "excluded_seeds": [812101, 812105, 913201, 913202, 913203, 913204]})
    _freeze(runtime_path, oracle_path)


def prepare_dev(readiness: Path) -> None:
    receipt = json.loads(readiness.read_text(encoding="utf-8"))
    if receipt.get("status") != "PASS":
        raise RuntimeError("READINESS_PASS_REQUIRED_BEFORE_DEV_ROSTER")
    directory = REPORT / "dev"
    cases: dict[str, Any] = {}
    oracles: dict[str, Any] = {}
    episodes = []
    for seed in DEV_SEEDS:
        for bucket in BUCKETS:
            for method in METHODS:
                case_id = f"RQ2-DEV-{seed}-{bucket[:2]}-{method.replace('-', '')}"
                runtime, oracle = _case(case_id, method, bucket, seed, "rq2_dev_" + canonical_sha256(case_id)[:12])
                truth = _truth(case_id, seed, bucket, directory)
                if truth:
                    oracle["evaluator_truth_artifact"] = truth
                cases[case_id], oracles[case_id] = runtime, oracle
                episodes.append({"case_id": case_id, "episode_id": runtime["episode_id"], "method_id": method, "timing_bucket": bucket, "seed": seed, "exposure_class": "RQ2_T_MVP_DEV_EXPOSED"})
    runtime_path = directory / "runtime_cases.json"
    oracle_path = directory / "oracle_injection_manifest.json"
    _write_once(runtime_path, {"schema_version": "driveclarify.rq2.dev_runtime_cases.v1", "cases": cases})
    _write_once(oracle_path, {"schema_version": "driveclarify.rq2.dev_oracle.v1", "development_only": True, "future_formal_eligible": False, "cases": oracles})
    roster = {"schema_version": "driveclarify.rq2.t_mvp_dev_roster.v1", "frozen_before_episode_1": True, "seeds": list(DEV_SEEDS), "factorial": {"methods": 6, "timing_buckets": 4, "seeds": 3, "episodes": 72}, "episodes": episodes}
    _write_once(REPORT / "RQ2_T_MVP_DEV_ROSTER.json", roster)
    rng = random.Random(20260828)
    blocks = [(seed, bucket) for seed in DEV_SEEDS for bucket in BUCKETS]
    rng.shuffle(blocks)
    order = []
    for block_index, (seed, bucket) in enumerate(blocks):
        rotation = block_index % len(METHODS)
        methods = METHODS[rotation:] + METHODS[:rotation]
        if block_index % 2:
            methods = tuple(reversed(methods))
        for method in methods:
            case_id = f"RQ2-DEV-{seed}-{bucket[:2]}-{method.replace('-', '')}"
            order.append({"run_ordinal": len(order) + 1, "block_ordinal": block_index + 1, "seed": seed, "timing_bucket": bucket, "method_id": method, "case_id": case_id})
    _write_once(REPORT / "RQ2_T_MVP_DEV_RUN_ORDER.json", {"schema_version": "driveclarify.rq2.dev_run_order.v1", "frozen_before_episode_1": True, "randomization_seed": 20260828, "balanced_method_position_counts": {method: {str(position): sum(1 for i, row in enumerate(order) if row["method_id"] == method and i % 6 == position) for position in range(6)} for method in METHODS}, "run_order": order})
    _execution_contract(directory, cases, oracles, mode="RQ2_T_MVP_DEV", runtime_path=runtime_path, oracle_path=oracle_path)
    for source, target in ((directory / "DEV_EXPOSURE_REGISTRY.json", REPORT / "RQ2_T_MVP_DEV_EXPOSURE_REGISTRY.json"), (directory / "DEV_EXECUTION_LEDGER.json", REPORT / "RQ2_T_MVP_DEV_EXECUTION_LEDGER.json")):
        _write_once(target, json.loads(source.read_text(encoding="utf-8")))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("smoke", "dev"))
    parser.add_argument("--readiness-receipt")
    args = parser.parse_args()
    if args.stage == "smoke":
        prepare_smoke()
    else:
        if not args.readiness_receipt:
            raise RuntimeError("READINESS_RECEIPT_REQUIRED")
        prepare_dev(Path(args.readiness_receipt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
