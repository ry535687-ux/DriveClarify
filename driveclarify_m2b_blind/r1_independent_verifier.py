"""Forward-free independent verifier for the M2B blind R1 contract package."""

from __future__ import annotations

import ast
import json
from collections import Counter
from pathlib import Path
from typing import Any

from .contracts import canonical_bytes, file_sha256, runtime_leak_paths
from .design_generator import _gold_extension
from .independent_verifier import independent_decision
from .r1_contracts import (
    evaluation_only_paths, policy_input_leak_paths, validate_partition_manifest,
    verify_commitment_bundle,
)
from .reference_solver import solve_case


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _current_inventory_matches(root: Path, frozen: dict[str, Any]) -> bool:
    expected = {row["filename"]: row for row in frozen["files"]}
    actual_names = {p.name for p in root.iterdir() if p.is_file()}
    if actual_names != set(expected):
        return False
    for name, row in expected.items():
        path = root / name
        if path.stat().st_size != row["bytes"]:
            return False
        if row.get("content_opened") is False:
            if not row.get("stat_matches_commitment") or path.stat().st_size != row["expected_bytes"]:
                return False
        elif file_sha256(path) != row["sha256"]:
            return False
    return True


def verify_r1(repo: Path, output: Path) -> dict[str, Any]:
    parent = repo / "reports/m2b_sealed_blind_decision_evaluation_design/DC-M2B-BLIND-DESIGN-20260804T101836Z"
    blocked = repo / "reports/m2b_sealed_blind_decision_evaluation_preexecution/DC-M2B-BLIND-PREFLIGHT-20260804T105747Z"
    runtime = _load(parent / "M2B_BLIND_RUNTIME_INPUT_PACKAGE.json")
    profiles = _load(parent / "M2B_BLIND_PROFILES.json")
    partition = _load(output / "M2B_BLIND_EVALUATION_PARTITION_MANIFEST.json")
    bundle = _load(output / "M2B_BLIND_EXECUTION_COMMITMENT_BUNDLE.json")
    unchanged = _load(output / "M2B_BLIND_SCIENTIFIC_CONTENT_UNCHANGED_AUDIT.json")
    checks: list[dict[str, Any]] = []

    def check(name: str, passed: bool, detail: Any = None) -> None:
        checks.append({"check": name, "status": "PASS" if passed else "FAIL", "detail": detail})

    try:
        validate_partition_manifest(partition)
        partition_valid = True
    except Exception as exc:  # verifier must report rather than hide failures
        partition_valid = False
        partition_error = f"{type(exc).__name__}:{exc}"
    check("partition_manifest_contract", partition_valid, None if partition_valid else partition_error)
    check("partition_zero_evaluation_fields", not evaluation_only_paths(partition), evaluation_only_paths(partition)[:10])
    bundle_result = verify_commitment_bundle(bundle, repo)
    check("complete_commitment_bundle", bundle_result["status"] == "PASS", bundle_result)
    check("runtime_zero_gold_fields", not runtime_leak_paths(runtime), runtime_leak_paths(runtime)[:10])
    policy_leaks = [(case["case_id"], policy_input_leak_paths(case)) for case in runtime["cases"] if policy_input_leak_paths(case)]
    check("policy_input_zero_membership_or_gold_fields", not policy_leaks, policy_leaks[:3])
    rows = partition["partitions"]
    check("track_r_216", sum(r["track_r"] for r in rows) == 216)
    check("track_s_full_pool_1152", sum(r["track_s_full_pool"] for r in rows) == 1152)
    check("track_s_primary_core_128", sum(r["track_s_primary_core"] for r in rows) == 128)
    check("boundary_420", sum(r["boundary_stress"] for r in rows) == 420)
    check("case_order_1368_sorted", len(rows) == 1368 and [r["case_id"] for r in rows] == sorted(r["case_id"] for r in rows))
    classification = {p["profile_id"]: p["design_classification"]
                      for group in (profiles["track_r_profiles"], profiles["track_s_profiles"]) for p in group}
    reference = [_gold_extension(case, solve_case(case), classification[case["profile_id"]]) for case in runtime["cases"]]
    core_candidates = [r for r in reference if r["track"] == "S" and r["boundary_classification"] == "CORE_NONBOUNDARY"]
    by_action = {a: [] for a in ("ACT", "ASK", "WAIT", "FALLBACK")}
    for row in sorted(core_candidates, key=lambda x: x["case_id"]):
        by_action[row["gold_action_type"]].append(row)
    core_ids = {row["case_id"] for action in by_action for row in by_action[action][:32]}
    partition_core = {row["case_id"] for row in rows if row["track_s_primary_core"]}
    check("primary_membership_reproduced", core_ids == partition_core,
          {"reference_count": len(core_ids), "partition_count": len(partition_core)})
    distribution = dict(Counter(r["gold_action_type"] for r in reference if r["case_id"] in core_ids))
    check("primary_distribution_32_each", distribution == {"ACT": 32, "ASK": 32, "WAIT": 32, "FALLBACK": 32}, distribution)
    disagreements = []
    for case, expected in zip(runtime["cases"], reference):
        actual = independent_decision(case)
        wanted = (expected["gold_action_type"], expected["gold_candidate_id"], expected["act_subtype"])
        if actual != wanted:
            disagreements.append({"case_id": case["case_id"], "decimal": wanted, "fraction": actual})
    check("decimal_fraction_1368_agreement", not disagreements and len(reference) == 1368,
          {"agreement": 1368 - len(disagreements), "total": 1368, "examples": disagreements[:3]})
    archetypes = _load(parent / "M2B_BLIND_MATRIX_ARCHETYPES.json")["archetypes"]
    explicit = [m for m in archetypes if m.get("candidate_swap_pair_id")]
    case_map = {(c["matrix_id"], c["profile_id"]): c for c in runtime["cases"] if c["track"] == "S"}
    swap_failures = []
    profile_ids = sorted({c["profile_id"] for c in runtime["cases"] if c["track"] == "S"})
    for pair_id in sorted({m["candidate_swap_pair_id"] for m in explicit}):
        pair = {m["candidate_swap_orientation"]: m for m in explicit if m["candidate_swap_pair_id"] == pair_id}
        for profile_id in profile_ids:
            left = independent_decision(case_map[(pair["ORIGINAL"]["archetype_id"], profile_id)])
            right = independent_decision(case_map[(pair["SWAPPED"]["archetype_id"], profile_id)])
            expected_candidate = None if left[1] is None else (
                pair["ORIGINAL"]["candidate_ids"][1] if left[1] == pair["ORIGINAL"]["candidate_ids"][0]
                else pair["ORIGINAL"]["candidate_ids"][0]
            )
            if (left[0], expected_candidate, left[2]) != right:
                swap_failures.append({"pair_id": pair_id, "profile_id": profile_id})
    check("candidate_swap_96_pass", not swap_failures and len({m["candidate_swap_pair_id"] for m in explicit}) * len(profile_ids) == 96,
          {"checks": 96, "failures": swap_failures[:3]})
    solver_tree = ast.parse((repo / "driveclarify_m2b_blind/reference_solver.py").read_text(encoding="utf-8"))
    imports = {node.module or "" for node in ast.walk(solver_tree) if isinstance(node, ast.ImportFrom)}
    forbidden_imports = [name for name in imports if "query_value" in name or "integrated" in name]
    check("reference_solver_no_tested_policy_import", not forbidden_imports, forbidden_imports)
    check("original_design_unchanged", _current_inventory_matches(parent, unchanged["parent_inventory_before"]))
    check("blocked_preflight_unchanged", _current_inventory_matches(blocked, unchanged["blocked_preflight_inventory_before"]))
    zero = _load(output / "M2B_BLIND_R1_ZERO_EXECUTION_AUDIT.json")
    check("zero_real_execution_prediction_metric_gold_unseal", all(zero[name] == 0 for name in (
        "blind_policy_execution_count", "blind_prediction_count", "blind_metric_count", "real_gold_unseal_count")))
    sandbox_path = output / "M2B_BLIND_SANDBOX_DUMMY_TEST_RESULTS.json"
    if sandbox_path.exists():
        sandbox = _load(sandbox_path)
        check("sandbox_dummy_pass", sandbox.get("status") == "PASS", sandbox)
    status = "PASS" if all(c["status"] == "PASS" for c in checks) else "FAIL"
    return {
        "schema_version": "driveclarify.m2b_blind_r1_independent_review.v1",
        "status": status,
        "design_id": bundle["design_id"],
        "algorithm": "FORWARD_FREE_RECONSTRUCTION_AND_FRACTION_REENUMERATION_NO_TESTED_POLICY_IMPORT",
        "checks": checks,
        "passed": sum(c["status"] == "PASS" for c in checks),
        "total": len(checks),
        "verifier_source_sha256": file_sha256(Path(__file__)),
    }
