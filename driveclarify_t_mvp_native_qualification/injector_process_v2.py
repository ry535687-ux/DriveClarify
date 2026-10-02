"""Race-safe evaluator-side prospective topology injector.

Scientific trigger construction and matching are imported unchanged from the
frozen v1 injector.  V2 changes only cross-process file publication: incomplete
legacy boundary files are retried, and ACK/update/receipt files become visible
only after their complete bytes are durable.
"""

from __future__ import annotations

from dataclasses import asdict
import argparse
import hashlib
import json
from pathlib import Path
import time

from driveclarify_t_mvp.canonical import canonical_sha256
from driveclarify_t_mvp.injector import (
    ProspectiveInjectionEvent,
    ProspectiveT1T4Injector,
    TimingBucket,
)
from driveclarify_t_mvp_native_qualification.atomic_io import write_json_once
from driveclarify_t_mvp_native_qualification.injector_process import (
    _matches,
    _verified_truth,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    manifest_path = Path(args.manifest).resolve()
    output = Path(args.output).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    case = manifest["cases"][args.case_id]
    verified_truth = _verified_truth(args.case_id, case)
    event = ProspectiveInjectionEvent(
        episode_id=case["episode_id"],
        case_id=args.case_id,
        bucket=TimingBucket(case["timing_bucket"]),
        injection_event_id=case["injection_event_id"],
        expected_oracle_event=case["expected_oracle_event"],
        source_scenario_version=manifest["schema_version"],
        source_scenario_sha256=canonical_sha256(manifest),
        instruction_old=case["instruction_old"],
        instruction_new=case["instruction_new"],
        update_event_id=case["update_event_id"],
    )
    injector = ProspectiveT1T4Injector(event)
    state: dict = {}
    seen: set[Path] = set()
    boundary_dir = output / "boundaries"
    while True:
        for path in sorted(boundary_dir.glob("boundary_*.json")):
            if path in seen:
                continue
            try:
                boundary = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                # V1 boundary producers opened the final name before writing.
                # Retrying without marking the path seen preserves the exact
                # boundary while preventing a transient empty read from killing
                # the sole evaluator-side injector.
                continue
            seen.add(path)
            if (
                boundary.get("captured_before_policy") is not True
                or boundary.get("boundary_phase")
                != "AFTER_CURRENT_CONTROL_BEFORE_NEXT_POLICY"
            ):
                raise RuntimeError("BOUNDARY_NOT_BEFORE_POLICY")
            trigger = case["trigger"]
            road_lane = (int(boundary["road_id"]), int(boundary["lane_id"]))
            right = bool(boundary["right_driving_lane"]["present"])
            matched = _matches(trigger, boundary, state)
            if road_lane == (int(trigger["road_id"]), int(trigger["lane_id"])) and right:
                state["right_seen_on_target"] = True
            state["previous_road_lane"] = road_lane
            if not matched:
                write_json_once(
                    output
                    / "exchange"
                    / "acks"
                    / f"ack_{int(boundary['sim_frame']):08d}.json",
                    {
                        "sim_frame": int(boundary["sim_frame"]),
                        "trigger_matched": False,
                    },
                )
                continue
            receipt, runtime = injector.inject(
                observed_oracle_event=event.expected_oracle_event,
                actual_frame=int(boundary["sim_frame"]),
                simulation_timestamp_s=float(boundary["sim_time_s"]),
                before_policy=True,
            )
            evaluator_payload = {
                "schema_version": "driveclarify.rq2.native_evaluator_injection.v1",
                "manifest_path": str(manifest_path),
                "manifest_sha256": canonical_sha256(manifest),
                "trigger": trigger,
                "evaluator_truth_artifact": verified_truth,
                "boundary_sha256": canonical_sha256(boundary),
                "receipt": asdict(receipt),
            }
            write_json_once(
                output / "evaluator" / "injection_receipt.json",
                evaluator_payload,
            )
            write_json_once(
                output / "exchange" / "runtime_update.json",
                asdict(runtime),
            )
            return 0
        terminal = output / "agent_terminal.json"
        if terminal.exists():
            write_json_once(
                output / "evaluator" / "injector_terminal_without_injection.json",
                {
                    "case_id": args.case_id,
                    "reason": "AGENT_TERMINATED_BEFORE_TRIGGER",
                },
            )
            return 2
        time.sleep(0.01)


if __name__ == "__main__":
    raise SystemExit(main())
