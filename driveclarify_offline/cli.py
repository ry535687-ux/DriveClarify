"""离线 fixture 执行与 pytest 摘要转换的精简 CLI。"""

from __future__ import annotations

import argparse
from pathlib import Path

from .fixtures import DEFAULT_FIXTURE_PATH
from .pytest_summary import build_pytest_summary
from .runner import run_fixtures


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DriveClarify v0.1 CPU-only evaluator")
    subparsers = parser.add_subparsers(dest="command", required=True)

    evaluate = subparsers.add_parser("evaluate", help="evaluate frozen synthetic fixtures")
    evaluate.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURE_PATH)
    evaluate.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/offline_v0/results"),
    )

    summary = subparsers.add_parser("pytest-summary", help="convert JUnit XML to JSON")
    summary.add_argument("--junit-xml", type=Path, required=True)
    summary.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "evaluate":
        output = run_fixtures(args.fixtures, args.output_dir)
        result = output["fixture_results"]
        print(
            f"{result['status']}: {result['passed']}/{result['total']} "
            "hand-authored synthetic fixtures matched"
        )
        return 0 if result["status"] == "PASS" else 1
    summary = build_pytest_summary(args.junit_xml, args.output)
    print(
        f"{summary['status']}: {summary['passed']}/{summary['tests']} pytest cases passed"
    )
    return 0 if summary["status"] == "PASS" else 1
