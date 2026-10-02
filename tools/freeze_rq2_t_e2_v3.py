#!/usr/bin/env python3
"""Authorize E2 V3 blind execution by freezing source and parameters."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports/driveclarify_rq2_t_v2_e2_tracked_association_and_full_mechanism_v1"
ENTRY_HEAD = "eaa332b1bb994279b59ea5af786fdb5de96adc1b"
SIMLINGO = Path("/home/buaa/wrh/simlingo")
SIMLINGO_PYTHON = Path("/home/buaa/anaconda3/envs/simlingo/bin/python3.8")
EXPECTED_BLIND = {
    "BLIND-V3-E2-REF": ("925937c9f8342de1d128e411b47b8becd87773523d97572d2a7a3accd91873f1", "07d6dda62384a0a93953e886523d4804d3c2a7feaa2c1dbdd5b455777e460c10"),
    "BLIND-V3-E2-LMK": ("6d20eb3035858652fd92ee47246451479f2bbad5855cd5457687c3125e559b19", "61e36032e99758b63092132c831bba7e2e4073fe8b30fa2ebe5fb911dc6871c9"),
    "BLIND-V3-E5-ORD": ("826ca4555dbb1ac1786a883639caab678a264d6f93a9aa078c21316d6ad5147a", "06faaae6041a778952b4bc673754b4645997fe60907e492e3cc2851db6b4231f"),
    "BLIND-V3-USC": ("d872db49bc4a6cb88140bad22df49fa593ade413007f5e7bf95ae3a8df48aabc", "85108266cd640ca178f06aba8de2d40f7d584f22ce1ef76dbda18e7a63a63125"),
    "BLIND-V3-NONREVEAL": ("73251ec748c99b3d6806ff931dffd8041940de55337bc8acf2be670b36ea5a44", "160b4e2bac005fb6b68599d07a2481fe90ae5215853397549070d1b020047181"),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


def load(path: Path, default: Any = None) -> Any:
    return default if not path.is_file() else json.loads(path.read_text(encoding="utf-8"))


def jsonl(path: Path) -> list[Mapping[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(dict(value), handle, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def git(*args: str, cwd: Path = ROOT, binary: bool = False) -> Any:
    output = subprocess.check_output(("git",) + args, cwd=str(cwd))
    return output if binary else output.decode("utf-8").strip()


def run_tests(python: Any) -> Mapping[str, Any]:
    command = [str(python), "-m", "pytest", "-q", "tests/rq2_t_e2_v3", "tests/rq2_t_v2_e2_audit"]
    environment = dict(os.environ)
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    completed = subprocess.run(command, cwd=str(ROOT), env=environment, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    output = completed.stdout.strip()
    return {
        "command": " ".join(command), "returncode": completed.returncode,
        "output": output, "output_sha256": hashlib.sha256(completed.stdout.encode("utf-8")).hexdigest(),
        "passed": completed.returncode == 0 and "110 passed" in output,
    }


def main() -> int:
    scientific_sources = sorted(
        [path.relative_to(ROOT).as_posix() for path in (ROOT / "driveclarify_rq2_t_e2_v3").glob("*.py")]
        + [
            "driveclarify_grounded_language_v1/runtime.py",
            "driveclarify_rq2_t/measurement.py",
            "driveclarify_paper_mvp_scenarios/leaderboard_scenario_root/srunner/scenarios/driveclarify_rq2_t_e2_v3_engineering.py",
            "tools/prepare_rq2_t_e2_v3.py", "tools/run_rq2_t_e2_v3.py",
            "tools/certify_rq2_t_e2_v3.py", "tools/calibrate_rq2_t_e2_v3.py",
            "tools/finalize_usc_activation_qualification.py", "tools/finalize_rq2_t_e2_v3.py",
            "tools/freeze_rq2_t_e2_v3.py", "tests/rq2_t_e2_v3/test_e2_v3_contracts.py",
        ]
    )
    parameters = sorted(
        [path.relative_to(ROOT).as_posix() for path in (ROOT / "driveclarify_rq2_t_e2_v3/engineering_routes").glob("*.xml")]
        + [
            "reports/driveclarify_rq2_t_v2_e2_tracked_association_and_full_mechanism_v1/FROZEN_CALIBRATED_THRESHOLDS.json",
            "reports/driveclarify_rq2_t_v2_e2_tracked_association_and_full_mechanism_v1/IDENTITY_CALIBRATION_RESULTS.json",
            "reports/driveclarify_rq2_t_v2_e2_tracked_association_and_full_mechanism_v1/DEVELOPMENT_SCENE_MANIFEST.json",
            "reports/driveclarify_rq2_t_v2_e2_tracked_association_and_full_mechanism_v1/CALIBRATION_SCENE_MANIFEST.json",
            "reports/driveclarify_rq2_t_v2_e2_tracked_association_and_full_mechanism_v1/BLIND_SCENE_SEAL.json",
            "reports/driveclarify_rq2_t_v2_e2_tracked_association_and_full_mechanism_v1/CERTIFIED_CANDIDATE_SCHEMA.json",
            "reports/driveclarify_rq2_t_v2_e2_tracked_association_and_full_mechanism_v1/CANDIDATE_TRACK_ASSOCIATION_CONTRACT.json",
            "reports/driveclarify_rq2_t_v2_e2_tracked_association_and_full_mechanism_v1/E2_MEMORY_AND_INVALIDATION_CONTRACT.json",
            "reports/driveclarify_rq2_t_v2_e2_tracked_association_and_full_mechanism_v1/B0_B1_B2_CONTRACT.json",
            "reports/driveclarify_rq2_t_v2_e2_tracked_association_and_full_mechanism_v1/USC_ACTIVATION_ONLY_QUALIFICATION_RECEIPT.json",
        ]
    )
    missing = [name for name in scientific_sources + parameters if not (ROOT / name).is_file()]
    if missing:
        raise RuntimeError("E2_V3_FREEZE_INPUT_MISSING:" + ",".join(missing))

    blind_seal = load(REPORT / "BLIND_SCENE_SEAL.json", {})
    blind_checks: dict[str, Any] = {}
    for row in blind_seal.get("scenes", ()):
        scene = str(row["scene"])
        expected_scene, expected_route = EXPECTED_BLIND[scene]
        actual_route = sha256(ROOT / str(row["route_path"]))
        blind_checks[scene] = {
            "scene_configuration_sha256": row.get("scene_configuration_sha256"),
            "route_sha256": actual_route,
            "pass": row.get("scene_configuration_sha256") == expected_scene
            and row.get("route_sha256") == expected_route and actual_route == expected_route,
        }
    attempted = jsonl(REPORT / "ENGINEERING_ATTEMPT_LEDGER.jsonl")
    blind_attempts = [row for row in attempted if str(row.get("scene", "")).startswith("BLIND-")]
    registry = load(REPORT / "ENGINEERING_IDENTITY_REGISTRY.json", {})
    formal_exposures = sum(1 for row in attempted if row.get("formal_scientific_exposure") is True)
    formal_seeds = sum(1 for row in registry.get("identities", ()) if row.get("formal_seed") is True)
    activation = load(REPORT / "USC_ACTIVATION_ONLY_QUALIFICATION_RECEIPT.json", {})
    calibration = load(REPORT / "IDENTITY_CALIBRATION_RESULTS.json", {})
    thresholds = load(REPORT / "FROZEN_CALIBRATED_THRESHOLDS.json", {})
    tests_default = run_tests("python")
    tests_simlingo = run_tests(SIMLINGO_PYTHON)
    source_hashes = {name: sha256(ROOT / name) for name in scientific_sources}
    parameter_hashes = {name: sha256(ROOT / name) for name in parameters}
    checks = {
        "entry_head_unchanged": git("rev-parse", "HEAD") == ENTRY_HEAD,
        "tracked_diff_empty": git("diff", "--binary", binary=True) == b"",
        "staged_diff_empty": git("diff", "--cached", "--binary", binary=True) == b"",
        "simlingo_head_unchanged": git("rev-parse", "HEAD", cwd=SIMLINGO) == "743b243afd6cf5ff51b9fa1f8cac86f22d569684",
        "simlingo_diff_unchanged": hashlib.sha256(git("diff", "--binary", cwd=SIMLINGO, binary=True)).hexdigest() == "40298d8760c787d81038756932785e4ba8ab4da78d31c2cc66075dffed613c7a",
        "calibration_consumed_once": calibration.get("calibration_consumption_count") == 1,
        "calibration_false_association_zero": calibration.get("selected_metrics", {}).get("false_unique_bindings") == 0,
        "threshold_frozen": thresholds.get("status") == "FROZEN_BEFORE_BLIND_EXECUTION",
        "usc_activation_qualification_pass": activation.get("status") == "PASS_USC_NATIVE_ACTIVATION_QUALIFICATION",
        "blind_attempt_count_zero": len(blind_attempts) == 0,
        "blind_hashes_unchanged": len(blind_checks) == 5 and all(row["pass"] for row in blind_checks.values()),
        "formal_seed_count_zero": formal_seeds == 0,
        "formal_scientific_exposure_zero": formal_exposures == 0,
        "default_python_regressions_pass": tests_default["passed"],
        "simlingo_python38_regressions_pass": tests_simlingo["passed"],
    }
    receipt = {
        "schema_version": "driveclarify.e2_v3.source_and_parameter_freeze.v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "PASS_SOURCE_AND_PARAMETER_FREEZE" if all(checks.values()) else "SOURCE_FREEZE_FAILED_CLOSED",
        "blind_execution_authorized": all(checks.values()),
        "entry_head": ENTRY_HEAD, "exit_head_at_freeze": git("rev-parse", "HEAD"),
        "source_hashes": source_hashes, "source_manifest_digest": canonical(source_hashes),
        "parameter_hashes": parameter_hashes, "parameter_manifest_digest": canonical(parameter_hashes),
        "blind_hash_checks": blind_checks, "blind_attempt_count_at_freeze": len(blind_attempts),
        "formal_seed_count": formal_seeds, "formal_scientific_exposure_count": formal_exposures,
        "tests": {"default_python": tests_default, "simlingo_python38": tests_simlingo},
        "checks": checks,
    }
    receipt["freeze_receipt_digest"] = canonical(receipt)
    atomic_json(REPORT / "SOURCE_FREEZE_RECEIPT.json", receipt)
    atomic_json(REPORT / "SOURCE_AND_PARAMETER_FREEZE.json", receipt)
    atomic_json(REPORT / "SOURCE_FREEZE_ADDENDUM.json", {
        "schema_version": "driveclarify.e2_v3.usc_source_freeze_addendum.v1",
        "status": receipt["status"], "blind_execution_authorized": receipt["blind_execution_authorized"],
        "usc_activation_qualification_receipt_digest": activation.get("receipt_digest"),
        "blind_hash_checks": blind_checks, "formal_scientific_exposure_count": formal_exposures,
        "source_freeze_receipt_digest": receipt["freeze_receipt_digest"],
    })
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if receipt["blind_execution_authorized"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
