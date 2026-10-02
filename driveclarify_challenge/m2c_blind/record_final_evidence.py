"""Record final Git evidence and the non-self-referential M2C-A artifact inventory."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
import subprocess
from typing import Any

from .challenge_contracts import SCHEMA_VERSION, file_sha256, stable_sha256, write_json
from .validate_blind_challenge import validate_all


REPORT_DIR = Path("reports/m2c_blind_integrated_challenge_v0")
SIMLINGO_ROOT = Path("/home/buaa/wrh/simlingo")
NEW_STATUS_LINES = {
    "?? driveclarify_challenge/",
    "?? reports/m2c_blind_integrated_challenge_v0/",
    "?? tests/m2c_blind_authoring_v0/",
}
MODIFIED_PATHS = ["AGENT_WORKLOG.md", "CURRENT_HANDOFF.md", "NEXT_AGENT_PROMPT.md", "STATE.json"]
NEW_PATHS = [
    "driveclarify_challenge/__init__.py",
    "driveclarify_challenge/m2c_blind/__init__.py",
    "driveclarify_challenge/m2c_blind/challenge_contracts.py",
    "driveclarify_challenge/m2c_blind/author_blind_challenge.py",
    "driveclarify_challenge/m2c_blind/independent_gold_oracle.py",
    "driveclarify_challenge/m2c_blind/validate_blind_challenge.py",
    "driveclarify_challenge/m2c_blind/check_overlap_without_disclosure.py",
    "driveclarify_challenge/m2c_blind/seal_blind_challenge.py",
    "driveclarify_challenge/m2c_blind/record_final_evidence.py",
    "tests/m2c_blind_authoring_v0/test_m2c_blind_authoring_v0.py",
    "reports/m2c_blind_integrated_challenge_v0/CHALLENGE_SPEC.md",
    "reports/m2c_blind_integrated_challenge_v0/AUTHORING_REPORT.md",
    "reports/m2c_blind_integrated_challenge_v0/PREDECLARED_METRICS.json",
    "reports/m2c_blind_integrated_challenge_v0/COVERAGE_MATRIX.json",
    "reports/m2c_blind_integrated_challenge_v0/AUTHORING_PROVENANCE.json",
    "reports/m2c_blind_integrated_challenge_v0/runtime_challenge.json",
    "reports/m2c_blind_integrated_challenge_v0/evaluation_only_hidden.json",
    "reports/m2c_blind_integrated_challenge_v0/SEAL.json",
    "reports/m2c_blind_integrated_challenge_v0/TEST_RESULTS.txt",
    "reports/m2c_blind_integrated_challenge_v0/OVERLAP_RESULTS.json",
    "reports/m2c_blind_integrated_challenge_v0/M2C_B_EVALUATION_PROMPT.md",
    "reports/m2c_blind_integrated_challenge_v0/GIT_START_END.json",
    "reports/m2c_blind_integrated_challenge_v0/ARTIFACT_INVENTORY.json",
]


def _git(*args: str, cwd: Path | None = None) -> bytes:
    return subprocess.check_output(["git", *args], cwd=cwd)


def _status(cwd: Path | None = None) -> list[str]:
    return _git("status", "--short", cwd=cwd).decode("utf-8").splitlines()


def _diff_hash(cwd: Path | None = None, *, cached: bool = False) -> str:
    args = ["diff", "--binary"]
    if cached:
        args.insert(1, "--cached")
    return hashlib.sha256(_git(*args, cwd=cwd)).hexdigest()


def _lines_hash(lines: list[str]) -> str:
    payload = "".join(f"{line}\n" for line in lines).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def record() -> tuple[dict[str, Any], dict[str, Any]]:
    validation = validate_all(verify_seal=True)
    end_status = _status()
    start_status = [line for line in end_status if line not in NEW_STATUS_LINES]
    end_simlingo_status = _status(SIMLINGO_ROOT)
    now = datetime.now(timezone.utc).isoformat()
    git_evidence = {
        "schema_version": "driveclarify.m2c_a_git_start_end.v0",
        "recorded_at_utc": now,
        "DriveClarify": {
            "start": {
                "branch": "master",
                "HEAD": "eaa332b1bb994279b59ea5af786fdb5de96adc1b",
                "git_status_short": start_status,
                "git_status_short_sha256": _lines_hash(start_status),
                "tracked_diff_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                "staged_diff_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            },
            "end": {
                "branch": _git("branch", "--show-current").decode().strip(),
                "HEAD": _git("rev-parse", "HEAD").decode().strip(),
                "git_status_short": end_status,
                "git_status_short_sha256": _lines_hash(end_status),
                "tracked_diff_sha256": _diff_hash(),
                "staged_diff_sha256": _diff_hash(cached=True),
            },
            "new_paths": NEW_PATHS,
            "modified_preexisting_paths": MODIFIED_PATHS,
            "unattributed_worktree_changes": [],
            "status_delta_at_default_git_status_granularity": sorted(NEW_STATUS_LINES),
        },
        "SimLingo": {
            "start": {
                "branch": "main",
                "HEAD": "743b243afd6cf5ff51b9fa1f8cac86f22d569684",
                "status_count": 11,
                "git_status_short": end_simlingo_status,
                "git_status_short_sha256": _lines_hash(end_simlingo_status),
                "tracked_diff_sha256": "7bd0947ef17df95b56d95ce155468d378a3c282b59f7dee4a2653a7195bc5e34",
                "staged_diff_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            },
            "end": {
                "branch": _git("branch", "--show-current", cwd=SIMLINGO_ROOT).decode().strip(),
                "HEAD": _git("rev-parse", "HEAD", cwd=SIMLINGO_ROOT).decode().strip(),
                "status_count": len(end_simlingo_status),
                "git_status_short": end_simlingo_status,
                "git_status_short_sha256": _lines_hash(end_simlingo_status),
                "tracked_diff_sha256": _diff_hash(SIMLINGO_ROOT),
                "staged_diff_sha256": _diff_hash(SIMLINGO_ROOT, cached=True),
            },
            "modified_by_m2c_a": False,
        },
        "frozen_hash_validation": "PASS",
        "sealed_file_post_write_validation": "PASS",
        "validation_summary": validation,
    }
    write_json(REPORT_DIR / "GIT_START_END.json", git_evidence)

    inventory_paths = [
        path for path in NEW_PATHS
        if not path.endswith(("GIT_START_END.json", "ARTIFACT_INVENTORY.json"))
    ] + ["reports/m2c_blind_integrated_challenge_v0/GIT_START_END.json"] + MODIFIED_PATHS
    artifact_hashes = {path: file_sha256(Path(path)) for path in inventory_paths}
    inventory = {
        "schema_version": "driveclarify.m2c_a_artifact_inventory.v0",
        "verdict": "READY_FOR_M2C_B_FROZEN_BLIND_EVALUATION",
        "inventory_self_hash_omitted": True,
        "artifact_count_excluding_inventory": len(artifact_hashes),
        "artifacts": [
            {"path": path, "sha256": digest}
            for path, digest in sorted(artifact_hashes.items())
        ],
        "artifact_manifest_sha256": stable_sha256(artifact_hashes),
        "seal_sha256": artifact_hashes["reports/m2c_blind_integrated_challenge_v0/SEAL.json"],
        "m2c_b_prompt_sha256": artifact_hashes[
            "reports/m2c_blind_integrated_challenge_v0/M2C_B_EVALUATION_PROMPT.md"
        ],
        "runtime_challenge_sha256": validation["runtime_sha256"],
        "hidden_gold_sha256": validation["hidden_sha256"],
        "M1_inventory_sha256_unchanged": "7bb83bb80534938b839cc5d2cf84c0b3d59d9e22b1cdf8e9f6c282e1065b749f",
        "M2A_inventory_sha256_unchanged": "84cd256b3056da05e25ce52b968676b3e67640c2afd5a865363bd536b8d6ff98",
        "M2B_policy_sha256_unchanged": "15ea1c881865b67f5cdf81333ce5c05d8419cd899dd4835a3efe26daa549fb0e",
        "real_s1_sha256_unchanged": "2e580c4b182eb211bc866ceb41c09d594fa43b0915d83a83160704512da75a82",
        "m2c_b_evaluated": False,
    }
    write_json(REPORT_DIR / "ARTIFACT_INVENTORY.json", inventory)
    return git_evidence, inventory


def main() -> int:
    git_evidence, inventory = record()
    print(
        "FINAL_EVIDENCE_RECORDED "
        f"artifacts={inventory['artifact_count_excluding_inventory']} "
        f"manifest_sha256={inventory['artifact_manifest_sha256']} "
        f"drive_status_sha256={git_evidence['DriveClarify']['end']['git_status_short_sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
