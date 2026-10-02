#!/usr/bin/env python3
"""Run one authorized linked-aware action for frozen Method V1 R3 TRAIN."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driveclarify_method_v1_r3_formal_train_execution import linked_retry  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("preflight-a02", "run-a02", "run-next", "status", "closeout"))
    parser.add_argument("--authorization-file", type=Path)
    parser.add_argument("--authorization-sha256")
    args = parser.parse_args()
    if args.action in ("run-a02", "run-next") and os.environ.get("DISPLAY") != ":1":
        raise SystemExit("DISPLAY=:1_REQUIRED")
    if args.action == "run-next" and (
        args.authorization_file is None or args.authorization_sha256 is None
    ):
        parser.error(
            "run-next requires --authorization-file ABSOLUTE_PATH "
            "and --authorization-sha256 SHA256"
        )
    if args.action != "run-next" and (
        args.authorization_file is not None or args.authorization_sha256 is not None
    ):
        parser.error("authorization arguments are accepted only for run-next")
    if args.action == "preflight-a02":
        result = linked_retry.write_a02_preflight()
    elif args.action == "run-a02":
        result = linked_retry.run_a02()
    elif args.action == "run-next":
        result = linked_retry.run_next(
            authorization_path=args.authorization_file,
            authorization_sha256=args.authorization_sha256,
        )
    elif args.action == "status":
        result = linked_retry.status()
    else:
        result = linked_retry.closeout()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
