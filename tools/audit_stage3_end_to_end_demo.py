#!/usr/bin/env python3
"""Write the Stage 3 live audit and evidence index from one live bundle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from driveclarify_m3_runtime_shadow.end_to_end_demo_v1 import (
    audit_closed_loop_bundle,
    build_evidence_index,
)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live-dir", required=True)
    parser.add_argument("--equivalence", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--extra-evidence", action="append", default=[])
    args = parser.parse_args()

    live_dir = Path(args.live_dir)
    output_dir = Path(args.output_dir)
    audit = audit_closed_loop_bundle(live_dir, args.equivalence)
    evidence = [
        live_dir / "LIVE_SHADOW_EVENT.json",
        live_dir / "ASK_REPLAN_EVENT.json",
        live_dir / "LIMITED_ACT_COMMIT_V0.json",
        Path(args.equivalence),
        live_dir / "ask_sent_wait_active.png",
        live_dir / "answer_received_old_candidates_invalidated.png",
        live_dir / "replanning_from_latest_observation.png",
        live_dir / "replan_complete_resume_ready.png",
        live_dir / "candidate_authority_granted.png",
        live_dir / "act_control_tick_active.png",
        live_dir / "act_carla_next_frame_observed.png",
        live_dir / "control_returned_to_baseline.png",
        *[Path(value) for value in args.extra_evidence],
    ]
    index = build_evidence_index(evidence)
    index["run_id"] = audit.get("run_id")
    _write_json(output_dir / "LIVE_INVARIANT_AUDIT.json", audit)
    _write_json(output_dir / "EVIDENCE_INDEX.json", index)
    print(audit["status"])
    return 0 if audit["passed"] and index["all_evidence_present"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

