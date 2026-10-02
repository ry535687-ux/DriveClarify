#!/usr/bin/env python3
"""Execute one controlled action for the frozen Method V1 R3 TRAIN campaign."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driveclarify_method_v1_r3_formal_train_execution import orchestrator  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "freeze-entry", "run-one", "status", "closeout"))
    args = parser.parse_args()
    if args.action == "prepare":
        result = orchestrator.prepare_execution()
    elif args.action == "freeze-entry":
        result = orchestrator.freeze_execution_entry()
    elif args.action == "run-one":
        if os.environ.get("DISPLAY") != ":1":
            raise SystemExit("DISPLAY=:1_REQUIRED")
        result = orchestrator.run_one()
    elif args.action == "status":
        result = orchestrator.status()
    else:
        result = orchestrator.closeout()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
