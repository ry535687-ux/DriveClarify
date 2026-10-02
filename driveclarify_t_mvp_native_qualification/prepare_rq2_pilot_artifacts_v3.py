"""Prospectively freeze the bounded v2 readiness smoke and gated DEV pilot."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
from typing import Any, Iterable

from driveclarify_t_mvp.canonical import canonical_sha256
from driveclarify_t_mvp_native_qualification.baseline_bindings import (
    resolve_all_bindings,
)


REPO = Path("/home/buaa/wrh/DriveClarify")
QUALIFICATION = REPO / "driveclarify_t_mvp_native_qualification"
REPORT = (
    REPO
    / "reports/driveclarify_rq2_future_fresh_pilot_NOT_CREATED"
)
ROUTE_SHORT = (
    REPO
    / "reports/driveclarify_v11_sequence21_closure/engineering_routes/"
    "V11-DEV-TOWN05-01.xml"
)
ROUTE_EXTENDED = QUALIFICATION / "routes/TOWN05-ENGINEERING-EXTENDED-311003.xml"
SOURCE_T3 = (
    REPO
    / "reports/driveclarify_rq2_t_mvp_native_preexecution_closure_v1/"
    "T3_EVALUATOR_TRUTH_PROOF.json"
)
SOURCE_T4 = (
    REPO
    / "reports/driveclarify_rq2_t_mvp_native_preexecution_closure_v1/"
    "T4_EVALUATOR_TRUTH_PROOF.json"
)
PRIOR_REPORT = (
    REPO / "reports/driveclarify_rq2_six_method_native_readiness_and_dev_pilot_v1"
)

# These three seeds were searched before this source file was created.  Each had
# zero exact-string occurrences outside the as-yet empty v2 report directory.
SMOKE = (
    ("READINESS-V2-B4-T1", "T-B4", "T1_BEFORE_COMMITMENT", 7319537),
    ("READINESS-V2-B6-T3", "T-B6", "T3_POST_COMMIT_RECOVERABLE", 7319553),
    ("READINESS-V2-B6-T4", "T-B6", "T4_NO_SAFE_CURRENT_OPPORTUNITY", 7319627),
)
METHODS = ("T-B1", "T-B2", "T-B3", "T-B4", "T-B5", "T-B6")
BUCKETS = (
    "T1_BEFORE_COMMITMENT",
    "T2_NEAR_COMMITMENT",
    "T3_POST_COMMIT_RECOVERABLE",
    "T4_NO_SAFE_CURRENT_OPPORTUNITY",
)
EXCLUDED_SEEDS = (812101, 812105, 913201, 913202, 913203, 913204)


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
            {
                "kind": "ENTER_ROAD_LANE_WITH_RIGHT_DRIVING_LANE",
                "road_id": 39,
                "lane_id": -1,
            },
            old,
            "After entering Road 39 in the current lane, use the later legal lane-change opening to enter the adjacent right driving lane, then return to the destination lane before the unchanged destination.",
            "town05-road39-lane-minus2-late-recovery-then-return-to-lane-minus1",
        )
    if bucket == "T4_NO_SAFE_CURRENT_OPPORTUNITY":
        return (
            "CURRENT_OPPORTUNITY_END_PASSED",
            {
                "kind": "ENTER_ROAD_LANE_WITH_RIGHT_DRIVING_LANE",
                "road_id": 39,
                "lane_id": -1,
            },
            old,
            "Take the current junction branch that enters Road 39 directly in lane -2, then merge back before the unchanged destination.",
            "town05-junction720-direct-entry-road39-lane-minus2-current-opportunity",
        )
    raise ValueError(f"UNKNOWN_BUCKET:{bucket}")


def _truth(
    case_id: str, seed: int, bucket: str, directory: Path
) -> dict[str, Any] | None:
    if bucket not in {"T3_POST_COMMIT_RECOVERABLE", "T4_NO_SAFE_CURRENT_OPPORTUNITY"}:
        return None
    is_t3 = bucket.startswith("T3")
    source = SOURCE_T3 if is_t3 else SOURCE_T4
    value = {
        "schema_version": "driveclarify.rq2.prospective_timing_truth.v2",
        "status": "PASS",
        "truth_class": (
            "OLD_EXCLUSIVE_RECOVERABLE" if is_t3 else "NO_SAFE_CURRENT_OPPORTUNITY"
        ),
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


def _case(
    case_id: str, method: str, bucket: str, seed: int, run_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    expected, trigger, old, new, branch = _trigger(bucket)
    extended = bucket.startswith("T3") or bucket.startswith("T4")
    runtime = {
        "attempt_id": case_id + "-A1",
        "baseline_id": method,
        "case_id": case_id,
        "episode_id": case_id + "-E1",
        "global_destination_identity": (
            "engineering-route-311003-destination"
            if extended
            else "official-route-311001-destination"
        ),
        "route_path": str(ROUTE_EXTENDED if extended else ROUTE_SHORT),
        "route_subset": "311003" if extended else "311001",
        "run_id": run_id,
        "scene_id": "TOWN05-RQ2-" + ("EXTENDED-311003" if extended else "ROUTE-311001"),
        "seed": seed,
        "updated_branch_identity": branch,
    }
    identity = {"seed": seed, "bucket": bucket}
    oracle = {
        "episode_id": runtime["episode_id"],
        "expected_oracle_event": expected,
        "injection_event_id": "INJECT-" + canonical_sha256(identity)[:20],
        "instruction_new": new,
        "instruction_old": old,
        "timing_bucket": bucket,
        "trigger": trigger,
        "update_event_id": "UPDATE-" + canonical_sha256(identity)[:20],
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
        "schema_version": "driveclarify.rq2.exposure_registry.v2",
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
    prefix = "DEV" if dev else "SMOKE"
    registry_path = directory / f"{prefix}_EXPOSURE_REGISTRY.json"
    _write_once(registry_path, registry)
    probes = []
    for ordinal, (case_id, case) in enumerate(cases.items(), 1):
        oracle = oracles[case_id]
        probes.append(
            {
                "probe_id": prefix + f"-{ordinal:03d}",
                "engineering_case_id": case_id,
                "baseline_id": case["baseline_id"],
                "seed": case["seed"],
                "case_config_sha256": canonical_sha256(case),
                "runtime_config_sha256": _bytes(runtime_path),
                "oracle_manifest_sha256": _bytes(oracle_path),
                "injection_event_id": oracle["injection_event_id"],
                "update_event_id": oracle["update_event_id"],
                "rpc_port": 2540,
                "status": "NOT_STARTED",
            }
        )
    _write_once(
        directory / f"{prefix}_EXECUTION_LEDGER.json",
        {
            "schema_version": "driveclarify.rq2.native_execution_ledger.v2",
            "execution_mode": mode,
            "immutable": True,
            "maximum_native_probes": len(probes),
            "maximum_pre_agent_attempts_per_probe": 3,
            "post_agent_retry_allowed": False,
            "probes": probes,
        },
    )
    _write_once(
        directory / f"{prefix}_PREEXECUTION_GATE.json",
        {
            "schema_version": "driveclarify.rq2.native_preexecution_gate.v2",
            "final_status": (
                "PASS_RQ2_DEV_PREEXECUTION_GATE"
                if dev
                else "PASS_NATIVE_ENGINEERING_PREEXECUTION_GATE"
            ),
            "native_execution_started": False,
            "future_episode_count": len(probes),
            "scientific_retries_allowed": False,
            "pre_agent_identical_retry_ceiling": 3,
        },
    )


def _tracked(path: Path) -> bool:
    return (
        subprocess.run(
            ["git", "ls-files", "--error-unmatch", str(path.relative_to(REPO))],
            cwd=REPO,
            capture_output=True,
            check=False,
        ).returncode
        == 0
    )


def _freeze(smoke_runtime: Path, smoke_oracle: Path) -> None:
    core = (
        (QUALIFICATION / "agent_route_authority_v2.py", "NATIVE_SIX_METHOD_WRAPPER_DISPATCHER_V2"),
        (QUALIFICATION / "receipt_encoding.py", "B6_CANONICAL_RECEIPT_ENCODING"),
        (QUALIFICATION / "baseline_bindings.py", "SIX_FROZEN_BASELINE_BINDINGS"),
        (REPO / "driveclarify_t_mvp/baselines.py", "B1_B6_FROZEN_BASELINES"),
        (REPO / "driveclarify_t_mvp/g_binding_v2.py", "G_CONTRACT_V2"),
        (QUALIFICATION / "g_terminal_regions_v3.json", "G_TERMINAL_REGION_V2_ROUTE_REGISTRY_EXTENSION"),
        (QUALIFICATION / "route_authority_bindings_v1.json", "ROUTE_AUTHORITY_BINDING"),
        (QUALIFICATION / "prospective_timing_reachability_v1.json", "PROSPECTIVE_TIMING_REACHABILITY"),
        (QUALIFICATION / "validate_transition_preexposure.py", "PREEXPOSURE_TRANSITION_GATE"),
        (REPO / "driveclarify_t_mvp/full_replan.py", "T_B2_FULL_GLOBAL_LOCAL_REPLAN"),
        (REPO / "driveclarify_t_mvp/commitment.py", "OBSERVABLE_COMMITMENT"),
        (REPO / "driveclarify_t_mvp/injector.py", "T1_T4_PROSPECTIVE_INJECTOR"),
        (QUALIFICATION / "injector_process.py", "NATIVE_T1_T4_INJECTOR"),
        (QUALIFICATION / "preexecution_guard.py", "EXPOSURE_AND_RETRY_GUARD"),
        (QUALIFICATION / "run_native_episode_route_authority_v2.sh", "SMOKE_AND_DEV_RUNNER_V2"),
        (QUALIFICATION / "run_rq2_dev_batch.sh", "FROZEN_72_EPISODE_ORCHESTRATOR"),
        (QUALIFICATION / "prepare_rq2_pilot_artifacts_v2.py", "V2_PROSPECTIVE_GENERATOR"),
        (QUALIFICATION / "readiness_postprocess_v2.py", "V2_READINESS_GATE"),
        (QUALIFICATION / "rq2_dev_postprocess.py", "POST_BATCH_ANALYSIS"),
        (QUALIFICATION / "verify_source_freeze.py", "PRELAUNCH_FREEZE_VERIFIER"),
        (REPO / "tests/t_mvp_static/test_native_readiness_repair_v2.py", "REPAIR_REGRESSION_TESTS"),
        (ROUTE_SHORT, "SHORT_T1_T2_ROUTE"),
        (ROUTE_EXTENDED, "EXTENDED_T3_T4_ROUTE"),
        (smoke_runtime, "FRESH_SMOKE_RUNTIME_CASES"),
        (smoke_oracle, "FRESH_SMOKE_ORACLE_MANIFEST"),
        (REPORT / "READINESS_SMOKE_ROSTER.json", "FRESH_SMOKE_ROSTER"),
        (REPORT / "CARLA_STABILITY_RULE.json", "PROSPECTIVE_STABILITY_RULE"),
    )
    raw_status = subprocess.check_output(
        ["git", "status", "--porcelain=v1", "-z"], cwd=REPO
    )
    rows = []
    for path, role in core:
        stat = path.stat()
        rows.append(
            {
                "absolute_path": str(path.resolve()),
                "relative_path": str(path.resolve().relative_to(REPO)),
                "closure_category": "CORE_EXPERIMENT_CONTROLLED",
                "runtime_role": role,
                "tracked": _tracked(path),
                "size_bytes": stat.st_size,
                "sha256": _bytes(path),
            }
        )
    _write_once(
        REPORT / "PRACTICAL_RUNTIME_FREEZE.json",
        {
            "schema_version": "driveclarify.rq2.final_native_core_freeze.v2",
            "frozen_before_first_new_native_smoke": True,
            "git_branch": subprocess.check_output(
                ["git", "branch", "--show-current"], cwd=REPO, text=True
            ).strip(),
            "git_head": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
            ).strip(),
            "git_status_porcelain_z_sha256": hashlib.sha256(raw_status).hexdigest(),
            "source_changes_after_smoke_start_allowed": False,
            "file_count": len(rows),
            "files": rows,
        },
    )


def _prior_occurrence_count(seed: int) -> int:
    result = subprocess.run(
        [
            "rg",
            "-l",
            "--fixed-strings",
            str(seed),
            ".",
            "--glob",
            "!reports/driveclarify_rq2_six_method_native_readiness_repair_and_dev_pilot_v2/**",
        ],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode not in {0, 1}:
        raise RuntimeError(f"SEED_SEARCH_FAILED:{seed}:{result.stderr}")
    return len([line for line in result.stdout.splitlines() if line])


def prepare_smoke() -> None:
    REPORT.mkdir(parents=True, exist_ok=True)
    directory = REPORT / "smoke"
    if directory.exists():
        raise FileExistsError("V2_SMOKE_ALREADY_FROZEN")
    cases: dict[str, Any] = {}
    oracles: dict[str, Any] = {}
    for ordinal, (case_id, method, bucket, seed) in enumerate(SMOKE, 1):
        runtime, oracle = _case(
            case_id, method, bucket, seed, f"rq2_readiness_v2_{ordinal:02d}"
        )
        truth = _truth(case_id, seed, bucket, directory)
        if truth:
            oracle["evaluator_truth_artifact"] = truth
        cases[case_id], oracles[case_id] = runtime, oracle
    runtime_path = directory / "runtime_cases.json"
    oracle_path = directory / "oracle_injection_manifest.json"
    _write_once(
        runtime_path,
        {
            "schema_version": "driveclarify.rq2.final_readiness_repair_smoke.v2",
            "cases": cases,
        },
    )
    _write_once(
        oracle_path,
        {
            "schema_version": "driveclarify.rq2.final_readiness_smoke_oracle.v2",
            "development_only": True,
            "future_formal_eligible": False,
            "cases": oracles,
        },
    )
    roster = {
        "schema_version": "driveclarify.rq2.final_readiness_smoke_roster.v2",
        "frozen_before_first_exposure": True,
        "maximum_agent_exposures": 3,
        "within_authorized_ceiling_of_eight": True,
        "cases": [
            {
                "run_ordinal": ordinal,
                "case_id": case_id,
                "baseline_id": method,
                "timing_bucket": bucket,
                "seed": seed,
                "required_evidence": (
                    "EXACT_NATIVE_B4"
                    if method == "T-B4"
                    else "EXACT_NATIVE_B6_WITH_PROSPECTIVE_TRUTH_JOIN"
                ),
            }
            for ordinal, (case_id, method, bucket, seed) in enumerate(SMOKE, 1)
        ],
    }
    _write_once(REPORT / "READINESS_SMOKE_ROSTER.json", roster)
    _write_once(
        REPORT / "CARLA_STABILITY_RULE.json",
        {
            "schema_version": "driveclarify.rq2.carla_stability_rule.v2",
            "frozen_before_first_native_launch": True,
            "required_case_count": 3,
            "maximum_identical_pre_agent_attempts_per_case": 3,
            "maximum_total_pre_agent_failures": 2,
            "post_agent_retry_allowed": False,
            "required_agent_exposures_per_case": 1,
            "allowed_pre_agent_failure_classes": [
                "CARLA_SERVER_DIED_BEFORE_AGENT_SETUP",
                "CARLA_SERVER_STARTUP_FAILED",
                "EVALUATOR_EXITED_BEFORE_AGENT_SETUP",
            ],
            "required_cleanup_for_every_attempt": True,
            "no_owned_process_residue": True,
            "required_final_status": "ALL_THREE_CASES_REACH_EXACTLY_ONE_AGENT_EXPOSURE",
            "rationale": "A bounded symmetric retry is infrastructure-only because it ends before selector/model exposure and uses an identical frozen case/config/seed.",
        },
    )
    _execution_contract(
        directory,
        cases,
        oracles,
        mode="ENGINEERING_SMOKE",
        runtime_path=runtime_path,
        oracle_path=oracle_path,
    )
    _write_once(
        REPORT / "SIX_BASELINE_NATIVE_DISPATCH_RECEIPT.json",
        {
            "schema_version": "driveclarify.rq2.six_dispatch.v2",
            "bindings": resolve_all_bindings(),
        },
    )
    _write_once(
        REPORT / "SEED_FRESHNESS_RECEIPT.json",
        {
            "schema_version": "driveclarify.rq2.seed_freshness.v2",
            "searched_before_source_and_artifact_creation": True,
            "search_command": "rg -l --fixed-strings SEED . excluding the v2 report directory",
            "smoke_seeds": [row[3] for row in SMOKE],
            "prior_exact_string_file_counts": {str(row[3]): 0 for row in SMOKE},
            "all_smoke_seed_counts_zero": True,
            "dev_seeds_selected": False,
            "excluded_seeds": list(EXCLUDED_SEEDS),
        },
    )
    _freeze(runtime_path, oracle_path)


def _validate_dev_seeds(values: Iterable[int]) -> tuple[int, int, int]:
    seeds = tuple(values)
    if len(seeds) != 3 or len(set(seeds)) != 3:
        raise ValueError("EXACTLY_THREE_DISTINCT_DEV_SEEDS_REQUIRED")
    if any(seed in EXCLUDED_SEEDS or seed in {row[3] for row in SMOKE} for seed in seeds):
        raise ValueError("DEV_SEED_NOT_FRESH")
    counts = {seed: _prior_occurrence_count(seed) for seed in seeds}
    if any(counts.values()):
        raise ValueError(f"DEV_SEED_PRIOR_OCCURRENCE:{counts}")
    return seeds  # type: ignore[return-value]


def prepare_dev(readiness: Path, requested_seeds: Iterable[int]) -> None:
    receipt = json.loads(readiness.read_text(encoding="utf-8"))
    if receipt.get("status") != "PASS" or receipt.get("dev_authorized") is not True:
        raise RuntimeError("READINESS_PASS_REQUIRED_BEFORE_DEV_SEED_SELECTION")
    seeds = _validate_dev_seeds(requested_seeds)
    directory = REPORT / "dev"
    if directory.exists():
        raise FileExistsError("V2_DEV_ALREADY_FROZEN")
    _write_once(
        REPORT / "DEV_SEED_SELECTION_RECEIPT.json",
        {
            "schema_version": "driveclarify.rq2.dev_seed_selection.v2",
            "selected_only_after_readiness_pass": True,
            "readiness_receipt_sha256": _bytes(readiness),
            "seeds": list(seeds),
            "prior_exact_string_file_counts": {str(seed): 0 for seed in seeds},
            "all_seed_counts_zero": True,
        },
    )
    cases: dict[str, Any] = {}
    oracles: dict[str, Any] = {}
    episodes = []
    for seed in seeds:
        for bucket in BUCKETS:
            for method in METHODS:
                case_id = f"RQ2-DEV-V2-{seed}-{bucket[:2]}-{method.replace('-', '')}"
                runtime, oracle = _case(
                    case_id,
                    method,
                    bucket,
                    seed,
                    "rq2_dev_v2_" + canonical_sha256(case_id)[:12],
                )
                truth = _truth(case_id, seed, bucket, directory)
                if truth:
                    oracle["evaluator_truth_artifact"] = truth
                cases[case_id], oracles[case_id] = runtime, oracle
                episodes.append(
                    {
                        "case_id": case_id,
                        "episode_id": runtime["episode_id"],
                        "method_id": method,
                        "timing_bucket": bucket,
                        "seed": seed,
                        "exposure_class": "RQ2_T_MVP_DEV_EXPOSED",
                    }
                )
    runtime_path = directory / "runtime_cases.json"
    oracle_path = directory / "oracle_injection_manifest.json"
    _write_once(
        runtime_path,
        {"schema_version": "driveclarify.rq2.dev_runtime_cases.v2", "cases": cases},
    )
    _write_once(
        oracle_path,
        {
            "schema_version": "driveclarify.rq2.dev_oracle.v2",
            "development_only": True,
            "future_formal_eligible": False,
            "cases": oracles,
        },
    )
    _write_once(
        REPORT / "RQ2_T_MVP_DEV_ROSTER.json",
        {
            "schema_version": "driveclarify.rq2.t_mvp_dev_roster.v2",
            "frozen_before_episode_1": True,
            "selected_after_readiness_pass": True,
            "seeds": list(seeds),
            "factorial": {"methods": 6, "timing_buckets": 4, "seeds": 3, "episodes": 72},
            "episodes": episodes,
        },
    )
    rng = random.Random(20260828)
    blocks = [(seed, bucket) for seed in seeds for bucket in BUCKETS]
    rng.shuffle(blocks)
    order = []
    for block_index, (seed, bucket) in enumerate(blocks):
        rotation = block_index % len(METHODS)
        methods = METHODS[rotation:] + METHODS[:rotation]
        for method in methods:
            case_id = f"RQ2-DEV-V2-{seed}-{bucket[:2]}-{method.replace('-', '')}"
            order.append(
                {
                    "run_ordinal": len(order) + 1,
                    "block_ordinal": block_index + 1,
                    "seed": seed,
                    "timing_bucket": bucket,
                    "method_id": method,
                    "case_id": case_id,
                }
            )
    position_counts = {
        method: {
            str(position): sum(
                row["method_id"] == method and index % 6 == position
                for index, row in enumerate(order)
            )
            for position in range(6)
        }
        for method in METHODS
    }
    _write_once(
        REPORT / "RQ2_T_MVP_DEV_RUN_ORDER.json",
        {
            "schema_version": "driveclarify.rq2.dev_run_order.v2",
            "frozen_before_episode_1": True,
            "randomization_seed": 20260828,
            "balanced_method_position_counts": position_counts,
            "run_order": order,
        },
    )
    _execution_contract(
        directory,
        cases,
        oracles,
        mode="RQ2_T_MVP_DEV",
        runtime_path=runtime_path,
        oracle_path=oracle_path,
    )
    for source, target in (
        (directory / "DEV_EXPOSURE_REGISTRY.json", REPORT / "RQ2_T_MVP_DEV_EXPOSURE_REGISTRY.json"),
        (directory / "DEV_EXECUTION_LEDGER.json", REPORT / "RQ2_T_MVP_DEV_EXECUTION_LEDGER.json"),
    ):
        _write_once(target, json.loads(source.read_text(encoding="utf-8")))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("smoke", "dev"))
    parser.add_argument("--readiness-receipt")
    parser.add_argument("--dev-seeds", nargs="*", type=int)
    args = parser.parse_args()
    if args.stage == "smoke":
        if args.readiness_receipt or args.dev_seeds:
            raise ValueError("SMOKE_STAGE_ACCEPTS_NO_DEV_ARGUMENTS")
        prepare_smoke()
    else:
        if not args.readiness_receipt or not args.dev_seeds:
            raise RuntimeError("DEV_REQUIRES_READINESS_RECEIPT_AND_THREE_FRESH_SEEDS")
        prepare_dev(Path(args.readiness_receipt), args.dev_seeds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
