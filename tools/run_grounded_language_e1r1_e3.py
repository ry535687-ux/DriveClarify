#!/usr/bin/env python3
"""Initialize, execute, snapshot, or finalize the E1-R1 E3 TRAIN campaign."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driveclarify_grounded_language_v1_extension_e1_r1_e3 import campaign


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("initialize", "run", "snapshot", "finalize"))
    parser.add_argument("--execution-id")
    parser.add_argument("--max-slots", type=int)
    args = parser.parse_args()
    if args.action == "initialize":
        if not args.execution_id:
            parser.error("--execution-id is required for initialize")
        result = campaign.initialize(args.execution_id)
    elif args.action == "run":
        result = campaign.run(max_slots=args.max_slots)
    elif args.action == "snapshot":
        result = campaign.snapshot()
    else:
        result = campaign.finalize()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
