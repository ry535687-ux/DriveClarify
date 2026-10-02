"""CLI for the CPU-only M2B runtime diagnostic and offline fixture evaluation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

from .offline_decision_evaluation import (
    evaluate_baselines_and_ablations,
    evaluate_fixture_set,
    load_runtime_fixture_set,
    run_runtime_record,
)


def _emit(value: Any, output: str | None) -> None:
    text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if output:
        Path(output).write_text(text, encoding="utf-8")
    else:
        print(text, end="")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DriveClarify M2B offline query-value policy")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run")
    run.add_argument("runtime_fixture")
    run.add_argument("--decision-id", required=True)
    run.add_argument("--output")
    evaluate = subparsers.add_parser("evaluate")
    evaluate.add_argument("runtime_fixture")
    evaluate.add_argument("evaluation_fixture")
    evaluate.add_argument("--output")
    compare = subparsers.add_parser("compare")
    compare.add_argument("runtime_fixture")
    compare.add_argument("evaluation_fixture")
    compare.add_argument("--output")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "evaluate":
        _emit(evaluate_fixture_set(args.runtime_fixture, args.evaluation_fixture), args.output)
        return 0
    if args.command == "compare":
        _emit(evaluate_baselines_and_ablations(args.runtime_fixture, args.evaluation_fixture), args.output)
        return 0
    records = [
        item
        for item in load_runtime_fixture_set(args.runtime_fixture)
        if item.get("decision_id") == args.decision_id
    ]
    if len(records) != 1:
        raise SystemExit("run requires exactly one matching decision_id")
    _emit(run_runtime_record(records[0]), args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
