#!/usr/bin/env python3
"""Run the required E1-R1 regression families and emit one receipt."""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "reports/grounded_language_v1_extension_e1_r1/FULL_REGRESSION_RECEIPT.json"
SUITES = (
    ("static_language_v1", "tests/language_grounding_v1"),
    ("temporal_v1", "tests/temporal_grounding_v1"),
    ("controlled_integration", "tests/grounded_language_v1_controlled_integration"),
    ("extension", "tests/grounded_language_v1_extension_e1"),
    ("extension_e1_r1", "tests/grounded_language_v1_extension_e1_r1"),
    ("stage6b", "tests/paper_mvp_stage6b"),
    ("stage6b_r0", "tests/paper_mvp_stage6b_r0"),
)


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _counts(output: str) -> dict[str, int]:
    values = {name: 0 for name in ("passed", "failed", "errors", "skipped", "xfailed", "xpassed")}
    for number, name in re.findall(r"(\d+)\s+(passed|failed|errors?|skipped|xfailed|xpassed)", output):
        values["errors" if name in {"error", "errors"} else name] += int(number)
    return values


def main() -> None:
    records = []
    for name, path in SUITES:
        started = _now()
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", path],
            cwd=str(ROOT),
            env={**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        records.append(
            {
                "suite": name,
                "path": path,
                "status": "PASS" if result.returncode == 0 else "BLOCKED",
                "return_code": result.returncode,
                "counts": _counts(result.stdout),
                "output": result.stdout,
                "started_at_utc": started,
                "ended_at_utc": _now(),
            }
        )
    totals = {
        key: sum(row["counts"][key] for row in records)
        for key in ("passed", "failed", "errors", "skipped", "xfailed", "xpassed")
    }
    receipt = {
        "schema_version": "driveclarify.grounded_language_v1_extension_e1_r1.full_regression_receipt.v1",
        "status": "PASS_FULL_REGRESSION" if all(row["status"] == "PASS" for row in records) and totals["failed"] == totals["errors"] == 0 else "BLOCKED_FULL_REGRESSION",
        "suite_count": len(records),
        "suites": records,
        "totals": totals,
        "feature_flag_default_off": True,
        "dev_attempt_count": 0,
        "test_attempt_count": 0,
        "test_consumed": False,
        "completed_at_utc": _now(),
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT.with_name(OUTPUT.name + ".tmp")
    temporary.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(str(temporary), str(OUTPUT))
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
    raise SystemExit(0 if receipt["status"] == "PASS_FULL_REGRESSION" else 2)


if __name__ == "__main__":
    main()
