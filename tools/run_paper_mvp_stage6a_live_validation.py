#!/usr/bin/env python3
"""Connect to an existing CARLA server and author Stage 6A live receipts.

This tool never starts CARLA.  World loading is disabled unless explicitly
authorized with ``--allow-world-load``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from driveclarify_paper_mvp_scenarios import (  # noqa: E402
    ScenarioFixtureValidationError,
    validate_live_promotions,
)
from driveclarify_paper_mvp_scenarios.compiler import DEFAULT_OUTPUT_DIR  # noqa: E402
from driveclarify_paper_mvp_scenarios.contracts import (  # noqa: E402
    ScenarioFixtureContractError,
)
from driveclarify_paper_mvp_scenarios.live_contracts import (  # noqa: E402
    DEFAULT_LIVE_PROMOTION_DIR,
)
from driveclarify_paper_mvp_scenarios.live_promotion import (  # noqa: E402
    LivePromotionConfig,
    LivePromotionError,
    run_live_promotion,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Probe Stage 6A fixtures against an already-running CARLA server. "
            "The command does not launch CARLA and never reads evaluator-private labels."
        )
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2000)
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_LIVE_PROMOTION_DIR)
    parser.add_argument(
        "--allow-world-load",
        action="store_true",
        help="allow the connected server to load the six required towns",
    )
    parser.add_argument(
        "--town",
        action="append",
        default=[],
        help="limit probing to a town; repeatable (complete validator still requires all towns)",
    )
    parser.add_argument("--navmesh-samples", type=int, default=64)
    parser.add_argument(
        "--semantic-attestation",
        type=Path,
        help="optional content-addressed, label-free human/map semantic attestation",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate existing 24x4 receipts without importing or connecting to CARLA",
    )
    parser.add_argument(
        "--accept-blocked-receipts",
        action="store_true",
        help="return zero for structurally valid receipts that remain semantically blocked",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        live_manifest = None
        if not args.check:
            config = LivePromotionConfig(
                runtime_root=args.runtime_root,
                output_dir=args.output_dir,
                host=args.host,
                port=args.port,
                timeout_seconds=args.timeout_seconds,
                allow_world_load=args.allow_world_load,
                navmesh_samples=args.navmesh_samples,
                semantic_attestation_path=args.semantic_attestation,
                selected_towns=tuple(args.town),
            )
            live_manifest = run_live_promotion(config)
        validation = validate_live_promotions(
            args.output_dir,
            runtime_root=args.runtime_root,
            require_ready=False,
        )
    except (
        LivePromotionError,
        ScenarioFixtureContractError,
        ScenarioFixtureValidationError,
    ) as exc:
        print(
            json.dumps(
                {
                    "status": "FAIL_CLOSED_STAGE6A_LIVE_VALIDATION",
                    "reason": str(exc),
                    "carla_server_started_by_tool": False,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    result = {
        "operation": "check" if args.check else "live_probe_and_check",
        "live_manifest": live_manifest,
        "validation": validation,
        "carla_server_started_by_tool": False,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    if validation["all_promotion_ready"] or args.accept_blocked_receipts:
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

