"""Seal the validated M2C challenge and verify all frozen inputs."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

from .challenge_contracts import (
    CHALLENGE_NAME,
    CHALLENGE_SEED_SHA256,
    M1_ARTIFACT_INVENTORY_SHA256,
    M2A_ARTIFACT_INVENTORY_SHA256,
    M2B_QUERY_VALUE_POLICY_SHA256,
    REAL_S1_SHA256,
    SCHEMA_VERSION,
    file_sha256,
    load_json,
    stable_sha256,
    write_json,
)
from .validate_blind_challenge import validate_all


REPORT_DIR = Path("reports/m2c_blind_integrated_challenge_v0")
SEAL_PATH = REPORT_DIR / "SEAL.json"
SIMLINGO_ROOT = Path("/home/buaa/wrh/simlingo")

DRIVECLARIFY_HEAD = "eaa332b1bb994279b59ea5af786fdb5de96adc1b"
SIMLINGO_HEAD = "743b243afd6cf5ff51b9fa1f8cac86f22d569684"
SIMLINGO_DIFF_SHA256 = "7bd0947ef17df95b56d95ce155468d378a3c282b59f7dee4a2653a7195bc5e34"
M2B_CORE_HASHES = {
    "driveclarify_decision/decision_contracts.py": "50fd74a40a61c11a47fd3a8a43f7022f835c82129ad13fae00b15d277aea916d",
    "driveclarify_decision/counterfactual_matrix.py": "0d56a46d8e17b600c41ec82c831eb6fa116669e9f2e19e838392a0d5f4ac02a7",
    "driveclarify_decision/query_value_policy.py": M2B_QUERY_VALUE_POLICY_SHA256,
    "driveclarify_decision/offline_decision_evaluation.py": "3457b4adabbacea1e9fa743c9e48437eeea4209d74978e8db9bd4efb37cb7a8f",
    "driveclarify_decision/decision_cli.py": "a3b96b6d4ee547f09e8eba4cf5d09917de0491028b80785a5573e8f1a157a019",
}
REAL_S1_PATH = Path(
    "reports/driveclarify_candidate_sensitivity_pilot/"
    "DC-CSENS-S1-20260730T082509Z/CANDIDATES.json"
)

SEALED_REPORT_FILES = (
    "CHALLENGE_SPEC.md",
    "AUTHORING_REPORT.md",
    "PREDECLARED_METRICS.json",
    "COVERAGE_MATRIX.json",
    "AUTHORING_PROVENANCE.json",
    "runtime_challenge.json",
    "evaluation_only_hidden.json",
    "TEST_RESULTS.txt",
    "OVERLAP_RESULTS.json",
)
AUTHORING_CODE_FILES = (
    "driveclarify_challenge/__init__.py",
    "driveclarify_challenge/m2c_blind/__init__.py",
    "driveclarify_challenge/m2c_blind/challenge_contracts.py",
    "driveclarify_challenge/m2c_blind/author_blind_challenge.py",
    "driveclarify_challenge/m2c_blind/independent_gold_oracle.py",
    "driveclarify_challenge/m2c_blind/validate_blind_challenge.py",
    "driveclarify_challenge/m2c_blind/check_overlap_without_disclosure.py",
    "driveclarify_challenge/m2c_blind/seal_blind_challenge.py",
)
AUTHORING_TEST_FILES = (
    "tests/m2c_blind_authoring_v0/test_m2c_blind_authoring_v0.py",
    "reports/m2c_blind_integrated_challenge_v0/TEST_RESULTS.txt",
)


def _git(*args: str, cwd: Path | None = None) -> bytes:
    return subprocess.check_output(["git", *args], cwd=cwd)


def _git_diff_sha256(cwd: Path, *, cached: bool = False) -> str:
    args = ["diff", "--binary"]
    if cached:
        args.insert(1, "--cached")
    return hashlib.sha256(_git(*args, cwd=cwd)).hexdigest()


def _manifest_digest(paths: tuple[str, ...]) -> tuple[str, dict[str, str]]:
    manifest = {path: file_sha256(Path(path)) for path in paths}
    return stable_sha256(manifest), manifest


def _verify_frozen_inputs() -> dict[str, Any]:
    if _git("branch", "--show-current").decode().strip() != "master":
        raise RuntimeError("BLOCKED_FROZEN_ARTIFACT_CHANGE:DRIVECLARIFY_BRANCH")
    if _git("rev-parse", "HEAD").decode().strip() != DRIVECLARIFY_HEAD:
        raise RuntimeError("BLOCKED_FROZEN_ARTIFACT_CHANGE:DRIVECLARIFY_HEAD")
    if _git("branch", "--show-current", cwd=SIMLINGO_ROOT).decode().strip() != "main":
        raise RuntimeError("BLOCKED_FROZEN_ARTIFACT_CHANGE:SIMLINGO_BRANCH")
    if _git("rev-parse", "HEAD", cwd=SIMLINGO_ROOT).decode().strip() != SIMLINGO_HEAD:
        raise RuntimeError("BLOCKED_FROZEN_ARTIFACT_CHANGE:SIMLINGO_HEAD")
    if _git_diff_sha256(SIMLINGO_ROOT) != SIMLINGO_DIFF_SHA256:
        raise RuntimeError("BLOCKED_FROZEN_ARTIFACT_CHANGE:SIMLINGO_DIFF")
    if file_sha256(Path("reports/task_conditioned_pairwise_consequence_method_v0/ARTIFACT_INVENTORY.json")) != M1_ARTIFACT_INVENTORY_SHA256:
        raise RuntimeError("BLOCKED_FROZEN_ARTIFACT_CHANGE:M1_INVENTORY")
    if file_sha256(Path("reports/structured_language_interaction_v0/ARTIFACT_INVENTORY.json")) != M2A_ARTIFACT_INVENTORY_SHA256:
        raise RuntimeError("BLOCKED_FROZEN_ARTIFACT_CHANGE:M2A_INVENTORY")
    for path, expected in M2B_CORE_HASHES.items():
        if file_sha256(Path(path)) != expected:
            raise RuntimeError(f"BLOCKED_FROZEN_ARTIFACT_CHANGE:{path}")
    if file_sha256(REAL_S1_PATH) != REAL_S1_SHA256:
        raise RuntimeError("BLOCKED_FROZEN_ARTIFACT_CHANGE:REAL_S1")
    return {
        "driveclarify_branch": "master",
        "driveclarify_head": DRIVECLARIFY_HEAD,
        "driveclarify_tracked_diff_sha256": _git_diff_sha256(Path.cwd()),
        "driveclarify_staged_diff_sha256": _git_diff_sha256(Path.cwd(), cached=True),
        "simlingo_branch": "main",
        "simlingo_head": SIMLINGO_HEAD,
        "simlingo_status_count": len(_git("status", "--short", cwd=SIMLINGO_ROOT).decode().splitlines()),
        "simlingo_diff_sha256": SIMLINGO_DIFF_SHA256,
        "simlingo_staged_diff_sha256": _git_diff_sha256(SIMLINGO_ROOT, cached=True),
    }


def seal() -> dict[str, Any]:
    if SEAL_PATH.exists():
        raise RuntimeError("SEAL_ALREADY_EXISTS_REFUSING_OVERWRITE")
    if "torch" in sys.modules:
        raise RuntimeError("BLOCKED_ARTIFACT_INTEGRITY:TORCH_LOADED")
    validation = validate_all(verify_seal=False)
    overlap = load_json(REPORT_DIR / "OVERLAP_RESULTS.json")
    if not overlap["required_zero_checks_pass"]:
        raise RuntimeError("BLOCKED_OLD_FIXTURE_OVERLAP")
    frozen = _verify_frozen_inputs()
    coverage = load_json(REPORT_DIR / "COVERAGE_MATRIX.json")
    tests_digest, tests_manifest = _manifest_digest(AUTHORING_TEST_FILES)
    code_digest, code_manifest = _manifest_digest(AUTHORING_CODE_FILES)
    sealed_files = {name: file_sha256(REPORT_DIR / name) for name in SEALED_REPORT_FILES}

    seal_document = {
        "schema_version": SCHEMA_VERSION,
        "challenge_name": CHALLENGE_NAME,
        "seal_status": "SEALED",
        "sealed_at_utc": datetime.now(timezone.utc).isoformat(),
        "challenge_seed_sha256": CHALLENGE_SEED_SHA256,
        "runtime_challenge_path": str((REPORT_DIR / "runtime_challenge.json").resolve()),
        "runtime_challenge_sha256": sealed_files["runtime_challenge.json"],
        "hidden_gold_path": str((REPORT_DIR / "evaluation_only_hidden.json").resolve()),
        "hidden_gold_sha256": sealed_files["evaluation_only_hidden.json"],
        "challenge_spec_sha256": sealed_files["CHALLENGE_SPEC.md"],
        "predeclared_metrics_sha256": sealed_files["PREDECLARED_METRICS.json"],
        "coverage_matrix_sha256": sealed_files["COVERAGE_MATRIX.json"],
        "case_count": validation["case_count"],
        "base_count": validation["case_type_counts"]["BASE"],
        "swap_count": validation["case_type_counts"]["SWAP_COMPANION"],
        "monotonic_companion_count": validation["case_type_counts"]["MONOTONIC_COMPANION"],
        "group_count": validation["group_count"],
        "category_counts": coverage["category_counts"],
        "M1_inventory_sha256": M1_ARTIFACT_INVENTORY_SHA256,
        "M2A_inventory_sha256": M2A_ARTIFACT_INVENTORY_SHA256,
        "M2B_core_hashes": M2B_CORE_HASHES,
        "real_s1_sha256": REAL_S1_SHA256,
        "DriveClarify_HEAD": DRIVECLARIFY_HEAD,
        "SimLingo_HEAD": SIMLINGO_HEAD,
        "SimLingo_diff_sha256": SIMLINGO_DIFF_SHA256,
        "authoring_tests_sha256": tests_digest,
        "authoring_test_file_hashes": tests_manifest,
        "authoring_code_sha256": code_digest,
        "authoring_code_file_hashes": code_manifest,
        "sealed_file_hashes": sealed_files,
        "overlap_check_sha256": sealed_files["OVERLAP_RESULTS.json"],
        "overlap_required_zero_checks_pass": True,
        "frozen_environment": frozen,
        "schema_validation": "PASS",
        "runtime_gold_leakage_validation": "PASS",
        "independent_oracle_validation": "PASS",
        "candidate_swap_validation": "PASS",
        "monotonic_validation": "PASS",
        "deterministic_regeneration": "PASS",
        "torch_loaded": False,
        "cuda_initialized": False,
        "post_seal_mutation_allowed": False,
        "m2c_b_evaluated": False,
    }
    write_json(SEAL_PATH, seal_document)
    for name, digest in sealed_files.items():
        if file_sha256(REPORT_DIR / name) != digest:
            raise RuntimeError(f"BLOCKED_POST_SEAL_MUTATION:{name}")
    return seal_document


def main() -> int:
    result = seal()
    print(
        "SEALED "
        f"cases={result['case_count']} base={result['base_count']} swap={result['swap_count']} "
        f"monotonic={result['monotonic_companion_count']} groups={result['group_count']} "
        f"runtime_sha256={result['runtime_challenge_sha256']} hidden_sha256={result['hidden_gold_sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

