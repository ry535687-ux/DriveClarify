#!/usr/bin/env python3
"""Run every repository test directory in an isolated, versioned interpreter.

The repository contains historical suites frozen under both Python 3.8
(SimLingo/Torch ABI) and Python 3.13 (offline artifact generation and modern
dataclass/XML semantics).  A single pytest process is invalid because many
legacy tests intentionally inspect ``sys.modules`` and several directories
use the same top-level helper module name.  This runner therefore executes
every ``test_*.py`` file exactly once in a fresh process.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = ROOT / "tests"
PYTHON_313 = Path("/home/buaa/anaconda3/bin/python")
PYTHON_SIMLINGO_38 = Path("/home/buaa/anaconda3/envs/simlingo/bin/python")
DEFAULT_OUTPUT = ROOT / "artifacts/paper_mvp_stage6a/full_regression_matrix_v2"

# These suites import the installed Torch ABI directly.  All other suites are
# pure/offline contracts and use the Python 3.13 interpreter under which their
# deterministic XML/float/dataclass artifacts were generated.
SIMLINGO_PY38_SUITES = frozenset(
    {
        "candidate_sensitivity_pilot",
        "candidate_stability_offline",
        "learned_m1",
        "m3_runtime_shadow",
        "maneuver_branch_real_mapping",
        "multi_unit_offline_candidate_capture",
        "paper_mvp_stage6a_runtime",
        "paper_mvp_stage6b_r0",
    }
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def interpreter_for(suite: str) -> Path:
    return PYTHON_SIMLINGO_38 if suite in SIMLINGO_PY38_SUITES else PYTHON_313


def junit_counts(path: Path) -> Dict[str, int]:
    root = ET.parse(str(path)).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    return {
        key: sum(int(float(suite.attrib.get(key, "0"))) for suite in suites)
        for key in ("tests", "failures", "errors", "skipped")
    }


def run_suite(test_path: Path, output: Path) -> Dict[str, Any]:
    relative = test_path.relative_to(ROOT)
    top_level_suite = relative.parts[1]
    suite = relative.as_posix()
    artifact_stem = "__".join(relative.with_suffix("").parts[1:])
    interpreter = interpreter_for(top_level_suite)
    if not interpreter.is_file():
        raise RuntimeError("缺少冻结解释器: {}".format(interpreter))
    logs = output / "logs"
    junit_dir = output / "junit"
    logs.mkdir(parents=True, exist_ok=True)
    junit_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs / (artifact_stem + ".log")
    junit_path = junit_dir / (artifact_stem + ".xml")
    command = [
        str(interpreter),
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "--tb=short",
        "--junitxml={}".format(junit_path),
        str(relative),
    ]
    env = dict(os.environ)
    env.update(
        {
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
        }
    )
    started = time.monotonic()
    completed = subprocess.run(
        command,
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    duration = time.monotonic() - started
    log_path.write_bytes(completed.stdout)
    counts = (
        junit_counts(junit_path)
        if junit_path.is_file()
        else {"tests": 0, "failures": 0, "errors": 1, "skipped": 0}
    )
    passed = counts["tests"] - counts["failures"] - counts["errors"] - counts["skipped"]
    status = (
        "PASS"
        if completed.returncode == 0
        and counts["tests"] > 0
        and counts["failures"] == 0
        and counts["errors"] == 0
        and counts["skipped"] == 0
        else "FAIL"
    )
    result = {
        "suite": suite,
        "suite_path": str(relative),
        "top_level_suite": top_level_suite,
        "interpreter": str(interpreter),
        "python_version": subprocess.check_output(
            [str(interpreter), "--version"], stderr=subprocess.STDOUT
        ).decode().strip(),
        "interpreter_reason": (
            "SIMLINGO_TORCH_ABI_PYTHON_3_8"
            if top_level_suite in SIMLINGO_PY38_SUITES
            else "HISTORICAL_OFFLINE_ARTIFACT_PYTHON_3_13"
        ),
        "command": command,
        "exit_code": completed.returncode,
        "duration_seconds": round(duration, 6),
        "tests": counts["tests"],
        "passed": passed,
        "failures": counts["failures"],
        "errors": counts["errors"],
        "skipped": counts["skipped"],
        "status": status,
        "log": {
            "path": str(log_path.relative_to(ROOT)),
            "bytes": log_path.stat().st_size,
            "sha256": sha256_file(log_path),
        },
        "junit": (
            {
                "path": str(junit_path.relative_to(ROOT)),
                "bytes": junit_path.stat().st_size,
                "sha256": sha256_file(junit_path),
            }
            if junit_path.is_file()
            else None
        ),
    }
    print(
        "[{status}] {suite}: {passed}/{tests} passed, {failures}F/{errors}E/{skipped}S ({python})".format(
            status=status,
            suite=suite,
            passed=passed,
            tests=counts["tests"],
            failures=counts["failures"],
            errors=counts["errors"],
            skipped=counts["skipped"],
            python=result["python_version"],
        ),
        flush=True,
    )
    return result


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--only", nargs="*", default=None)
    args = parser.parse_args(argv)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    all_suites = sorted(TEST_ROOT.rglob("test_*.py"))
    selected_names = set(args.only or [])
    available_selectors = {
        path.relative_to(ROOT).as_posix() for path in all_suites
    } | {path.relative_to(TEST_ROOT).parts[0] for path in all_suites}
    suites = [
        path
        for path in all_suites
        if not selected_names
        or path.relative_to(ROOT).as_posix() in selected_names
        or path.relative_to(TEST_ROOT).parts[0] in selected_names
    ]
    missing = selected_names - available_selectors
    if missing:
        raise RuntimeError("未知 test suite: {}".format(sorted(missing)))

    generated_at = datetime.now(timezone.utc).isoformat()
    matrix = {
        "schema_version": "driveclarify.stage6a.full_regression_matrix.v2",
        "generated_at_utc": generated_at,
        "isolation_unit": "ONE_TEST_FILE_PER_FRESH_PYTEST_PROCESS",
        "plugin_autoload_disabled": True,
        "bytecode_writes_disabled": True,
        "pythonhashseed": 0,
        "all_repository_test_files_selected": len(suites) == len(all_suites),
        "repository_test_file_count": len(all_suites),
        "repository_test_directory_count": len(
            {path.relative_to(TEST_ROOT).parts[0] for path in all_suites}
        ),
        "selected_suite_count": len(suites),
        "python_3_8_suites": sorted(SIMLINGO_PY38_SUITES),
        "python_3_13_suites": sorted({
            path.relative_to(TEST_ROOT).parts[0]
            for path in all_suites
            if path.relative_to(TEST_ROOT).parts[0] not in SIMLINGO_PY38_SUITES
        }),
    }
    write_json(output / "FULL_REGRESSION_MATRIX.json", matrix)

    results: List[Dict[str, Any]] = []
    for suite in suites:
        results.append(run_suite(suite, output))

    totals = {
        key: sum(result[key] for result in results)
        for key in ("tests", "passed", "failures", "errors", "skipped")
    }
    all_pass = (
        len(results) == len(all_suites)
        and all(result["status"] == "PASS" for result in results)
        and totals["failures"] == totals["errors"] == totals["skipped"] == 0
        and totals["tests"] == totals["passed"]
    )
    matrix_result = {
        "schema_version": "driveclarify.stage6a.full_regression_matrix_result.v2",
        "generated_at_utc": generated_at,
        "status": "PASS_FULL_REPOSITORY_REGRESSION_MATRIX" if all_pass else "FAIL_FULL_REPOSITORY_REGRESSION_MATRIX",
        "aggregate_exit_code": 0 if all_pass else 1,
        "all_repository_test_files_selected": len(results) == len(all_suites),
        "suite_count": len(results),
        "totals": totals,
        "suites": results,
        "matrix": {
            "path": str((output / "FULL_REGRESSION_MATRIX.json").relative_to(ROOT)),
            "bytes": (output / "FULL_REGRESSION_MATRIX.json").stat().st_size,
            "sha256": sha256_file(output / "FULL_REGRESSION_MATRIX.json"),
        },
    }
    matrix_result_path = output / "FULL_REGRESSION_MATRIX_RESULT.json"
    write_json(matrix_result_path, matrix_result)

    combined_stdout = bytearray()
    for result in results:
        combined_stdout.extend(
            ("===== {} =====\n".format(result["suite"])).encode("utf-8")
        )
        combined_stdout.extend((ROOT / result["log"]["path"]).read_bytes())
        if not combined_stdout.endswith(b"\n"):
            combined_stdout.extend(b"\n")
    stdout_path = output / "FULL_REGRESSION_STDOUT.log"
    stderr_path = output / "FULL_REGRESSION_STDERR.log"
    stdout_path.write_bytes(bytes(combined_stdout))
    stderr_path.write_bytes(b"")

    receipt = {
        "schema_version": "driveclarify.paper_mvp_stage6a_full_regression_receipt.v1",
        "receipt_kind": "FULL_REGRESSION",
        "scope": "REPOSITORY_CONFIGURED_FULL_REGRESSION",
        "status": "PASS" if all_pass else "FAIL",
        "exit_code": 0 if all_pass else 1,
        "command_argv": [
            str(Path(sys.executable).resolve()),
            str(Path(__file__).resolve()),
            "--output",
            str(output),
        ],
        "synthetic_live_receipts_promoted": False,
        "stdout_sha256": sha256_file(stdout_path),
        "stderr_sha256": sha256_file(stderr_path),
        "matrix_result": {
            "path": str(matrix_result_path.relative_to(ROOT)),
            "bytes": matrix_result_path.stat().st_size,
            "sha256": sha256_file(matrix_result_path),
        },
        "suite_count": len(results),
        "totals": totals,
        "interpreter_matrix": {
            "python_3_8": str(PYTHON_SIMLINGO_38),
            "python_3_13": str(PYTHON_313),
            "one_suite_per_fresh_process": True,
        },
    }
    receipt_path = output / "FULL_REGRESSION_RECEIPT.json"
    write_json(receipt_path, receipt)
    print(json.dumps({
        "status": matrix_result["status"],
        "aggregate_exit_code": matrix_result["aggregate_exit_code"],
        "suite_count": matrix_result["suite_count"],
        "totals": totals,
        "receipt": str(receipt_path),
    }, ensure_ascii=False, indent=2, sort_keys=True), flush=True)
    return matrix_result["aggregate_exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
