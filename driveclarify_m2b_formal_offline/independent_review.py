"""Forward-free independent artifact verifier for Formal M1 / M2B integration."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


def _load(root: Path, name: str) -> Any:
    return json.loads((root / name).read_text(encoding="utf-8"))


def review(root: Path) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def check(name: str, condition: bool, detail: Any = None) -> None:
        checks.append({"check": name, "status": "PASS" if condition else "FAIL", "detail": detail})

    protocol = _load(root, "M2B_PROTOCOL_FREEZE.json")
    units = _load(root, "M2B_REAL_DEVELOPMENT_UNITS.json")["units"]
    profiles = _load(root, "M2B_OPERATING_PROFILES.json")["profiles"]
    cases = _load(root, "M2B_RUNTIME_CASES.json")["cases"]
    gold = _load(root, "M2B_EVALUATION_ONLY_GOLD.json")["records"]
    matrices = _load(root, "M2B_COUNTERFACTUAL_OUTCOME_MATRICES.json")["matrices"]
    learned = _load(root, "M2B_LEARNED_M1_DIAGNOSTICS.json")
    hybrid = _load(root, "M2B_HYBRID_RESULTS.json")["results"]
    isolation = _load(root, "M2B_GOLD_ISOLATION_AUDIT.json")
    access = _load(root, "M2B_TEST_ACCESS_AUDIT.json")
    control = _load(root, "M2B_CONTROL_AUTHORIZATION_AUDIT.json")
    monotonicity = _load(root, "M2B_MONOTONICITY_RESULTS.json")
    ablations = _load(root, "M2B_ABLATION_RESULTS.json")

    check("protocol_frozen_before_inference", protocol["freeze_order"]["profiles_before_learned_outputs"] is True)
    check("unit_count_36", len(units) == 36)
    check("profile_count_8", len(profiles) == 8)
    check("case_cross_product_288", len(cases) == 288 and len({row["episode_id"] for row in cases}) == 288)
    check("gold_physical_separation", len(gold) == 288 and isolation["runtime_evaluation_files_distinct"] is True)
    check("matrix_2x2", all(len(row["cells"]) == 4 for row in matrices))
    check("unknown_null_preserved", all(cell["task_error_cost"] is None and cell["wrong_goal_indicator"] is None for matrix in matrices for cell in matrix["cells"] if cell["task_outcome"] == "UNKNOWN"))
    check("learned_forward_budget", learned["runtime_audit"]["model_forward_count"] == 180)
    check("five_seeds_no_best", all(len(row["seed_outputs"]) == 5 and row["best_seed_selected"] is False for row in learned["units"]))
    check("hybrid_never_overrides_rule", all(row["learned_overrode_rule"] is False and row["rule_unknown_upgraded_by_learned"] is False for row in hybrid))
    check("gold_runtime_leakage_zero", isolation["runtime_forbidden_key_count"] == 0)
    check("test_data_plane_access_zero", access["test_data_plane_access_count"] == 0 and access["test_forward_count"] == 0)
    check("control_authorization_zero", control["control_authorization_count"] == 0)
    check("all_monotonicity_pass", all(row["status"] == "PASS" for row in monotonicity["checks"]))
    check("required_ablations_present", len(ablations["ablations"]) >= 8)
    check("candidate_swap_pass", ablations["contract_tests"]["candidate_swap"] == "PASS")
    check("no_default_a_pass", ablations["contract_tests"]["no_default_a"] == "PASS")
    check("active_query_no_reask", ablations["contract_tests"]["active_query_cannot_ask_again"] == "PASS")
    check("authorization_flags_false", all(not row["authorization_eligible"] and not row["control_authorized"] for row in hybrid))
    check("decision_total_288", sum(Counter(row["decision"] for row in hybrid).values()) == 288)
    passed = sum(row["status"] == "PASS" for row in checks)
    return {
        "schema_version": "driveclarify.independent_m2b_review.v1",
        "review_mode": "FORWARD_FREE_SEPARATE_MODULE_ARTIFACT_REVIEW",
        "independent_agent_claimed": False,
        "checks": checks,
        "passed": passed,
        "total": len(checks),
        "status": "PASS" if passed == len(checks) else "FAIL",
        "control_authorized": False,
    }
