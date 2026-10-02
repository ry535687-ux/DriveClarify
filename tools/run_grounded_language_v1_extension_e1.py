#!/usr/bin/env python3
"""Run a gated stage of the frozen Grounded Language V1 E1 extension."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driveclarify_grounded_language_v1_extension_e1.aggregate import aggregate  # noqa: E402
from driveclarify_grounded_language_v1_extension_e1.campaign import run_stage  # noqa: E402
from driveclarify_grounded_language_v1_extension_e1.finalize import finalize  # noqa: E402
from driveclarify_grounded_language_v1_extension_e1.materialize import materialize  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("e0", "e1", "e2", "train", "finalize"))
    parser.add_argument("--wall-timeout-seconds", type=float, default=180.0)
    args = parser.parse_args()
    if args.stage == "e0":
        freeze = materialize(overwrite_ledger=False)
        tests = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "tests/grounded_language_v1_extension_e1"],
            cwd=str(ROOT), check=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            env={**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
        )
        result = {
            "stage": "E0",
            "status": "PASS_E0" if tests.returncode == 0 else "BLOCKED_E0_TESTS",
            "freeze_payload_sha256": freeze["freeze_payload_sha256"],
            "pytest_return_code": tests.returncode,
            "pytest_output": tests.stdout,
            "extension_dev_attempt_count": 0,
            "extension_test_attempt_count": 0,
            "extension_test_consumed": False,
        }
    elif args.stage == "finalize":
        result = finalize()
    else:
        result = run_stage(args.stage, wall_timeout_seconds=args.wall_timeout_seconds)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
