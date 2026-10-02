#!/usr/bin/env python3
"""Run one excluded E2-audit witness with passive per-frame RGB persistence.

This launcher reuses the already-qualified RQ2-T V2 engineering launcher and
the existing default-off CP3B post-control image recorder.  It changes no E2
provider, detector, tracker, parser, planner, PID, control, or scene semantics.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import run_rq2_t_v2_pre_science as native_runner  # noqa: E402
from driveclarify_rq2_t_v2.e2_forensics import (  # noqa: E402
    is_unrelated_vscode_copilot_only,
)


REPORT = ROOT / "reports/driveclarify_rq2_t_v2_e2_grounding_root_cause_audit_v1"


def _configure_isolated_audit_paths() -> None:
    native_runner.REGISTRY = REPORT / "ENGINEERING_IDENTITY_REGISTRY.json"
    native_runner.ATTEMPT_LEDGER = REPORT / "ENGINEERING_ATTEMPT_LEDGER.jsonl"
    native_runner.EXECUTION_LEDGER = REPORT / "ENGINEERING_EXECUTION_LEDGER.jsonl"
    native_runner.EVIDENCE_ROOT = REPORT / "NEW_PROBES"


def _install_passive_rgb_recorder() -> None:
    original = native_runner.episode_environment

    def audit_environment(
        spec: Any, output: Path, row: Mapping[str, Any], scene: str
    ) -> dict[str, str]:
        values = original(spec, output, row, scene)
        values.update(
            {
                "DRIVECLARIFY_CP3B_IMAGE_DIR": str(output / "RAW_RGB_0"),
                "DRIVECLARIFY_CP3B_ROUTE_ID": str(spec.route_id),
                "DRIVECLARIFY_CP3B_EPISODE_ID": str(spec.episode_id),
            }
        )
        return values

    native_runner.episode_environment = audit_environment


def _install_unrelated_process_preflight_fix() -> None:
    """Reject real headless/remote runtimes but ignore VS Code Copilot's flag."""

    original = native_runner.native.native_preflight

    def audit_preflight(spec: Any, output: Path, *, visualization: bool) -> Mapping[str, Any]:
        receipt = dict(original(spec, output, visualization=visualization))
        if receipt.get("blockers") != ["FORBIDDEN_DISPLAY_PROCESS_PRESENT"]:
            return receipt
        process = subprocess.run(
            ["ps", "-eo", "pid=,args="],
            check=False,
            capture_output=True,
            text=True,
        )
        forbidden = []
        tokens = (
            "renderoffscreen", "-nullrhi", "headless", "xvfb", "x11vnc",
            "vncserver", "tigervnc", "tightvnc", "novnc",
        )
        for line in process.stdout.splitlines():
            normalized = line.casefold()
            if any(token in normalized for token in tokens):
                forbidden.append(line.strip())
        unrelated = is_unrelated_vscode_copilot_only(forbidden)
        if not unrelated:
            return receipt
        receipt.update(
            {
                "status": "PASS",
                "blockers": [],
                "engineering_diagnostic_preflight_fix": {
                    "classification": "UNRELATED_PROCESS_TOKEN_FALSE_POSITIVE",
                    "original_blocker": "FORBIDDEN_DISPLAY_PROCESS_PRESENT",
                    "ignored_process_rows": forbidden,
                    "carla_headless_or_remote_option_accepted": False,
                },
            }
        )
        native_runner.atomic_json(output / "NATIVE_PREFLIGHT.json", receipt)
        return receipt

    native_runner.native.native_preflight = audit_preflight


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--identity", default="RQ2TV2-E2AUD-001")
    parser.add_argument("--wall-timeout-seconds", type=float, default=1800.0)
    args = parser.parse_args()
    _configure_isolated_audit_paths()
    _install_passive_rgb_recorder()
    _install_unrelated_process_preflight_fix()
    result = native_runner.run_identity(
        args.identity, None, float(args.wall_timeout_seconds)
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("status") == "VALID_ENGINEERING_EPISODE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
