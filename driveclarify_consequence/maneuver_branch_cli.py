"""CPU-only CLI for a completed MANEUVER_BRANCH capture or its fixture."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from .maneuver_branch import write_evaluation_outputs


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(allow_abbrev=False)
    value.add_argument("--frozen-package", required=True, type=Path)
    value.add_argument("--candidate", action="append", required=True, type=Path)
    value.add_argument("--output-dir", required=True, type=Path)
    value.add_argument("--supervisor-status", type=Path)
    value.add_argument("--run-result", type=Path)
    return value


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if len(args.candidate) != 6:
        raise SystemExit("exactly six --candidate paths are required")
    cleanup_status = "NOT_PROVIDED"
    if args.supervisor_status is not None:
        supervisor = json.loads(args.supervisor_status.read_text(encoding="utf-8"))
        cleanup_status = "PASS" if supervisor.get("cleanup_complete") is True else "FAIL"
        if cleanup_status != "PASS":
            raise SystemExit("supervisor cleanup is not complete; mapping refused")
    if args.run_result is not None:
        run_result = json.loads(args.run_result.read_text(encoding="utf-8"))
        if run_result.get("final_status") != "PASS_REAL_MANEUVER_BRANCH_CAPTURE_PENDING_CPU_MAPPING":
            raise SystemExit("capture result is not complete; mapping refused")
        if run_result.get("candidate_count") != 6:
            raise SystemExit("capture does not contain exactly six candidates")
        calls = run_result.get("candidate_calls", {})
        if any(calls.get(name) != 0 for name in ("pid", "control", "planner_advancement", "tick", "wait_for_tick")):
            raise SystemExit("candidate side-effect invariant failed; mapping refused")
    outputs = write_evaluation_outputs(args.frozen_package, args.candidate, args.output_dir)
    pair = outputs["pair_equivalence"]
    report = "\n".join(
        [
            "# MANEUVER_BRANCH Real Mapping Pilot",
            "",
            "## 结果",
            "",
            f"- Predictor P pair class：`{pair['predictor_p_pair_class']}`",
            f"- Ground truth H pair class：`{pair['ground_truth_h_pair_class']}`",
            f"- Final pair class：`{pair['pair_class']}`",
            f"- Recommendation：`{pair['recommendation']}`",
            f"- CARLA/evaluator/GPU cleanup：`{cleanup_status}`",
            "",
            "本报告只表示 plan-to-Task 的离线、DIAGNOSTIC_ONLY 映射结果。它不授权",
            "ACT/ASK/WAIT、车辆控制、安全关键用途、训练或新的 observation/retry。",
            "",
        ]
    )
    (args.output_dir / "FINAL_PILOT_REPORT.md").write_text(report, encoding="utf-8")
    print(outputs["pair_equivalence"]["pair_class"])
    print(outputs["pair_equivalence"]["recommendation"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
