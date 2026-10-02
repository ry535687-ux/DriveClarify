"""CPU-only child used by the production M3E supervisor prelaunch selftest."""

from __future__ import annotations

import argparse
import os
import time


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hold-seconds", type=float, default=30.0)
    parser.add_argument("--token", required=True)
    parser.add_argument("--exec", dest="exec_path")
    args = parser.parse_args()
    if args.exec_path:
        os.execv(args.exec_path, [args.exec_path, str(args.hold_seconds)])
    time.sleep(args.hold_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

