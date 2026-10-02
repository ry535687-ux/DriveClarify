"""Minimal CLI for a future authorized production prediction event."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from driveclarify_m2b_prediction_runtime.orchestrator import execute_one_shot, execute_r3_one_shot
from driveclarify_m2b_prediction_runtime.policy import predict_runtime_case


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--partition", type=Path, required=True)
    parser.add_argument("--comparisons", type=Path, required=True)
    parser.add_argument("--hashes", type=Path, required=True)
    parser.add_argument("--design-id", required=True)
    parser.add_argument("--prediction-event-id", required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--envelope", type=Path, required=True)
    parser.add_argument("--r3", action="store_true")
    args = parser.parse_args(argv)
    hashes = json.loads(args.hashes.read_text(encoding="utf-8"))
    runner = execute_r3_one_shot if args.r3 else execute_one_shot
    extra = {"prediction_start_precommitted": True} if args.r3 else {}
    outcome = runner(
        runtime_path=args.runtime, partition_path=args.partition,
        comparison_set_path=args.comparisons, hashes=hashes,
        design_id=args.design_id, prediction_event_id=args.prediction_event_id,
        journal_path=args.journal, envelope_path=args.envelope,
        predictor=predict_runtime_case,
        **extra,
    )
    return 0 if outcome["envelope"]["completeness"] == "COMPLETE" else 75


if __name__ == "__main__":
    raise SystemExit(main())
