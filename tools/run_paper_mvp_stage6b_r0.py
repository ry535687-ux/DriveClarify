#!/usr/bin/env python3
"""DriveClarify Stage 6B-R0 TRAIN-only gate/campaign entrypoint."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from driveclarify_paper_mvp_stage6b.backend import (  # noqa: E402
    DEFAULT_NO_PROGRESS_TIMEOUT_SECONDS,
    DEFAULT_WALL_TIMEOUT_SECONDS,
    UnifiedNativeBackend,
    resolve_train_episode,
)
from driveclarify_paper_mvp_stage6b.campaign import (  # noqa: E402
    freeze_contracts,
    run_formal_train,
    run_t0,
    run_t1,
    run_t2,
    run_t3,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Stage 6B-R0 unified native TRAIN-only execution"
    )
    parser.add_argument(
        "command",
        choices=("t0", "one", "t1", "t2", "t3", "freeze", "train"),
    )
    parser.add_argument("--execution-id", default="DC-STAGE6B-R0-GATES")
    parser.add_argument("--scenario-id")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--method-id")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument(
        "--wall-timeout-seconds",
        type=float,
        default=DEFAULT_WALL_TIMEOUT_SECONDS,
    )
    parser.add_argument(
        "--no-progress-timeout-seconds",
        type=float,
        default=DEFAULT_NO_PROGRESS_TIMEOUT_SECONDS,
    )
    parser.add_argument("--max-new-episodes", type=int)
    parser.add_argument("--no-visualization", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "t0":
        result = run_t0()
    else:
        backend = UnifiedNativeBackend(
            wall_timeout_seconds=args.wall_timeout_seconds,
            no_progress_timeout_seconds=args.no_progress_timeout_seconds,
        )
        if args.command == "one":
            if (
                args.scenario_id is None
                or args.seed is None
                or args.method_id is None
                or args.output_dir is None
            ):
                raise SystemExit(
                    "one requires --scenario-id --seed --method-id --output-dir"
                )
            spec = resolve_train_episode(
                scenario_id=args.scenario_id,
                seed=args.seed,
                method_id=args.method_id,
            )
            result = backend.run(
                spec,
                args.output_dir,
                visualization=not args.no_visualization,
            )
        elif args.command == "t1":
            result = run_t1(execution_id=args.execution_id, backend=backend)
        elif args.command == "t2":
            result = run_t2(execution_id=args.execution_id, backend=backend)
        elif args.command == "t3":
            result = run_t3(execution_id=args.execution_id, backend=backend)
        elif args.command == "freeze":
            result = freeze_contracts(execution_id=args.execution_id)
        else:
            result = run_formal_train(
                execution_id=args.execution_id,
                backend=backend,
                max_new_episodes=args.max_new_episodes,
            )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if str(result.get("status", "")).startswith("PASS") or str(
        result.get("status", "")
    ).startswith("COMPLETED") or str(result.get("status", "")).startswith(
        "FORMAL_TRAIN_TERMINAL"
    ) or str(result.get("status", "")).startswith(
        "PARTIAL_STAGE6B"
    ) else 2


if __name__ == "__main__":
    raise SystemExit(main())
