#!/usr/bin/env python3
"""Run only the authorized E1-R1 E1/E2 gates; E3 is intentionally absent."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driveclarify_grounded_language_v1_extension_e1_r1.campaign import run_e1, run_e2  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("e1", "e2"))
    parser.add_argument("--wall-timeout-seconds", type=float, default=240.0)
    args = parser.parse_args()
    result = (
        run_e1(timeout=args.wall_timeout_seconds)
        if args.stage == "e1"
        else run_e2(timeout=args.wall_timeout_seconds)
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
