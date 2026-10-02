"""Command-line entrypoints for the CPU-only M2A language prototype."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from .offline_language_evaluation import evaluate_fixture_set, load_runtime_fixture_set
from .structured_interaction import run_runtime_pipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DriveClarify M2A offline structured-language pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="run one runtime-observable episode")
    run.add_argument("runtime_fixture")
    run.add_argument("--episode-id")
    run.add_argument("--answer")
    run.add_argument("--answer-timestamp", type=float)
    run.add_argument("--output")
    evaluate = subparsers.add_parser("evaluate", help="score predeclared evaluation-only fixtures")
    evaluate.add_argument("runtime_fixture")
    evaluate.add_argument("evaluation_fixture")
    evaluate.add_argument("--output")
    return parser


def _emit(value: dict, output: str | None) -> None:
    text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if output:
        Path(output).write_text(text, encoding="utf-8")
    else:
        print(text, end="")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "evaluate":
        _emit(evaluate_fixture_set(args.runtime_fixture, args.evaluation_fixture), args.output)
        return 0
    records = load_runtime_fixture_set(args.runtime_fixture)
    if args.episode_id:
        records = [item for item in records if item.get("episode_id") == args.episode_id]
    if len(records) != 1:
        raise SystemExit("run requires exactly one selected runtime episode")
    _emit(run_runtime_pipeline(records[0], args.answer, args.answer_timestamp), args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
