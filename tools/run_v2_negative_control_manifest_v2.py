#!/usr/bin/env python3
"""Run one control from the single frozen manifest revision."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tools.run_v2_negative_control_v1 as runner  # noqa: E402
from driveclarify_v2_negative_control_reconstruction_v2.contracts import scenario  # noqa: E402
runner.MANIFEST = runner.REPORT / "TRAIN_V2_NEGATIVE_CONTROL_MANIFEST_V2.json"
runner.FREEZE = runner.REPORT / "PREFREEZE_HASH_RECEIPT_V2.json"
runner.SCENARIO_ROOT = ROOT / "driveclarify_v2_negative_control_reconstruction_v2/scenario_root"
runner.LIVE_MARKER = runner.REPORT / "LIVE_EXECUTION_STARTED_V2"
runner.scenario = scenario


def main() -> int:
    return runner.main()


if __name__ == "__main__":
    raise SystemExit(main())
