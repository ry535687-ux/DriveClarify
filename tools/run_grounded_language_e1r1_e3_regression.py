#!/usr/bin/env python3
"""Run the E3-required focused and repository regressions without touching E1-R1."""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "reports/grounded_language_v1_extension_e1_r1_e3/FULL_REGRESSION_RECEIPT.json"
TEST_PYTHON = Path("/home/buaa/anaconda3/envs/simlingo/bin/python")
SUITES = (
    ("static_language_v1", "tests/language_grounding_v1"),
    ("temporal_v1", "tests/temporal_grounding_v1"),
    ("controlled_integration", "tests/grounded_language_v1_controlled_integration"),
    ("extension_e1", "tests/grounded_language_v1_extension_e1"),
    ("extension_e1_r1", "tests/grounded_language_v1_extension_e1_r1"),
    ("extension_e3_harness", "tests/grounded_language_v1_extension_e1_r1_e3"),
    ("stage6b", "tests/paper_mvp_stage6b"),
    ("stage6b_r0", "tests/paper_mvp_stage6b_r0"),
    ("repository_required", "tests"),
)


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def counts(output: str) -> dict[str, int]:
    values = {name: 0 for name in ("passed", "failed", "errors", "skipped", "xfailed", "xpassed")}
    for number, name in re.findall(r"(\d+)\s+(passed|failed|errors?|skipped|xfailed|xpassed)", output):
        values["errors" if name in {"error", "errors"} else name] += int(number)
    return values


def main() -> None:
    rows = []
    for name, path in SUITES:
        started = now()
        paths = (
            [str(item.relative_to(ROOT)) for item in sorted((ROOT / "tests").rglob("test_*.py"))]
            if name == "repository_required"
            else [path]
        )
        outputs = []
        return_codes = []
        aggregate_counts = {key: 0 for key in ("passed", "failed", "errors", "skipped", "xfailed", "xpassed")}
        for isolated_path in paths:
            run = subprocess.run(
                [str(TEST_PYTHON), "-m", "pytest", "-q", isolated_path],
                cwd=str(ROOT),
                env={**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )
            outputs.append(isolated_path + "\n" + run.stdout)
            return_codes.append(run.returncode)
            observed = counts(run.stdout)
            for key, value in observed.items():
                aggregate_counts[key] += value
        combined_output = "\n".join(outputs)
        return_code = max(return_codes or [1])
        rows.append(
            {
                "suite": name,
                "path": path,
                "isolated_process_count": len(paths),
                "status": "PASS" if return_code == 0 else "FAIL",
                "exit_code": return_code,
                "counts": aggregate_counts,
                "output_tail": "\n".join(combined_output.splitlines()[-20:]),
                "started_at_utc": started,
                "ended_at_utc": now(),
            }
        )
    totals = {key: sum(row["counts"][key] for row in rows) for key in ("passed", "failed", "errors", "skipped", "xfailed", "xpassed")}
    receipt = {
        "schema_version": "driveclarify.grounded_language_v1_extension_e1_r1_e3.full_regression.v1",
        "status": "PASS_FULL_REGRESSION" if all(row["exit_code"] == 0 for row in rows) else "BLOCKED_FULL_REGRESSION",
        "suite_count": len(rows),
        "test_python": str(TEST_PYTHON),
        "suites": rows,
        "totals": totals,
        "extension_dev_attempt_count": 0,
        "extension_test_attempt_count": 0,
        "extension_test_consumed": False,
        "completed_at_utc": now(),
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT.with_name(OUTPUT.name + ".tmp")
    temporary.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(str(temporary), str(OUTPUT))
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
    raise SystemExit(0 if receipt["status"] == "PASS_FULL_REGRESSION" else 2)


if __name__ == "__main__":
    main()
