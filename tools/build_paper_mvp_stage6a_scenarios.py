#!/usr/bin/env python3
"""Build or verify the frozen paper-MVP Stage 6A scenario fixtures."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from driveclarify_paper_mvp_scenarios import (  # noqa: E402
    DEFAULT_OUTPUT_DIR,
    ScenarioFixtureValidationError,
    compile_scenarios,
    validate_generated,
)
from driveclarify_paper_mvp_scenarios.contracts import (  # noqa: E402
    ScenarioFixtureContractError,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Deterministically compile 24 frozen successor route fixtures. "
            "This command never imports or launches CARLA."
        )
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"artifact directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate existing artifacts byte-for-byte without writing",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        compilation = None if args.check else compile_scenarios(args.output_dir)
        validation = validate_generated(args.output_dir, compare_expected=True)
    except (ScenarioFixtureContractError, ScenarioFixtureValidationError) as exc:
        print(
            json.dumps(
                {
                    "status": "FAIL_CLOSED_STAGE6A_SCENARIO_COMPILATION",
                    "reason": str(exc),
                    "carla_launch_count": 0,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    result = {
        "operation": "check" if args.check else "build_and_check",
        "compilation": compilation,
        "validation": validation,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

