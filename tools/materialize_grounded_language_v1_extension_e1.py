#!/usr/bin/env python3
"""Materialize and freeze Grounded Language V1 Extension E1."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driveclarify_grounded_language_v1_extension_e1.materialize import materialize


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--overwrite-ledger", action="store_true")
    args = parser.parse_args()
    result = materialize(overwrite_ledger=args.overwrite_ledger)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
