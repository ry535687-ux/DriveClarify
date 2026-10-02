#!/usr/bin/env python3
"""Build Stage 6A freezes and aggregate explicit validation receipts.

The command never runs CARLA, a model, a regression suite, or Stage 6B.  A
blocked report exits 2; READY_STAGE6B_EXECUTION exits 0.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from driveclarify_paper_mvp_stage6a import (  # noqa: E402
    READY_STATUS,
    Stage6AFreezeError,
    build_stage6a_implementation_freeze,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Write BASELINE_FREEZE.md, real block-style BASELINE_CONFIG.yaml, "
            "BASELINE_HASHES.json, and Stage 6A implementation-freeze reports. "
            "Missing receipts are recorded as BLOCKED rather than inferred."
        )
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--scenario-live-promotion-dir",
        type=Path,
        help="explicit directory containing the 24x4 live CARLA receipt set",
    )
    parser.add_argument(
        "--scenario-runtime-root",
        type=Path,
        help="explicit static runtime/route root bound by the live receipts",
    )
    parser.add_argument(
        "--scenario-live-execution-receipt",
        type=Path,
        help=(
            "explicit real ScenarioRunner 24x4 handler execution receipt; "
            "spawn/destroy promotion receipts alone cannot satisfy executable status"
        ),
    )
    parser.add_argument(
        "--candidate-audit-receipt",
        type=Path,
        help="explicit candidate_generation_audit.json v1 receipt",
    )
    parser.add_argument(
        "--authority-binding-receipt",
        type=Path,
        help="explicit stage6a_live_binding_audit.json v1 receipt",
    )
    parser.add_argument(
        "--full-regression-receipt",
        type=Path,
        help=(
            "explicit v1 FULL_REGRESSION JSON receipt with configured full scope, "
            "stream hashes, and exit_code=0"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = build_stage6a_implementation_freeze(
            output_dir=args.output_dir,
            scenario_live_promotion_dir=args.scenario_live_promotion_dir,
            scenario_runtime_root=args.scenario_runtime_root,
            scenario_live_execution_receipt=args.scenario_live_execution_receipt,
            candidate_audit_receipt=args.candidate_audit_receipt,
            authority_binding_receipt=args.authority_binding_receipt,
            full_regression_receipt=args.full_regression_receipt,
        )
    except (OSError, Stage6AFreezeError, TypeError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "status": "BLOCKED_STAGE6A_FREEZE_BUILDER_FAILURE",
                    "reason": type(exc).__name__,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    print(
        json.dumps(
            {
                "status": report["status"],
                "ready_for_stage6b_execution": report["ready_for_stage6b_execution"],
                "output_dir": str(args.output_dir),
                "report_payload_sha256": report["report_payload_sha256"],
            },
            sort_keys=True,
        )
    )
    return 0 if report["status"] == READY_STATUS else 2


if __name__ == "__main__":
    raise SystemExit(main())
