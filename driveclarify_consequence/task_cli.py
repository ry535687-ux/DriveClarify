"""CLI for CPU-only, diagnostic Task consequence analysis."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .task_pipeline import build_outputs, write_outputs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="driveclarify_consequence.task_cli",
        description="CPU-only UNKNOWN-preserving Task consequence diagnostics.",
    )
    parser.add_argument("--input", required=True, help="Hand-authored fixture-set JSON.")
    parser.add_argument("--output-dir", required=True, help="Output directory.")
    parser.add_argument("--s1-artifact", help="Optional read-only S1 CANDIDATES.json.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not Path(args.input).is_file():
        sys.stderr.write(f"INPUT_NOT_FOUND:{args.input}\n")
        return 2
    if args.s1_artifact and not Path(args.s1_artifact).is_file():
        sys.stderr.write(f"S1_ARTIFACT_NOT_FOUND:{args.s1_artifact}\n")
        return 2
    try:
        outputs = build_outputs(args.input, s1_artifact_path=args.s1_artifact)
        write_outputs(args.output_dir, outputs)
    except (OSError, ValueError, TypeError) as exc:
        sys.stderr.write(f"OFFLINE_TASK_CONSEQUENCE_FAILED:{exc.__class__.__name__}:{exc}\n")
        return 3
    summary = outputs["summary"]
    sys.stdout.write(
        "PASS_DIAGNOSTIC_ONLY "
        f"fixtures={summary['fixture_case_count']} "
        f"expectations_match={summary['all_hand_authored_expectations_match']} "
        f"s1={summary.get('s1_status') or 'NOT_EVALUATED'}\n"
    )
    return 0 if summary["all_hand_authored_expectations_match"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

