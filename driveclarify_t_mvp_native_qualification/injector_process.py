"""Evaluator-side prospective topology injector.

This process alone reads the oracle/timing manifest.  It writes an evaluator
receipt containing the timing bucket and a separate oracle-free semantic update
object for the native agent namespace.
"""

from __future__ import annotations

from dataclasses import asdict
import argparse
import hashlib
import json
import os
from pathlib import Path
import time

from driveclarify_t_mvp.canonical import canonical_bytes, canonical_sha256
from driveclarify_t_mvp.injector import (
    ProspectiveInjectionEvent,
    ProspectiveT1T4Injector,
    TimingBucket,
)


def _write_once(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(
                json.dumps(
                    value,
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
                + b"\n"
            )
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)


def _matches(trigger: dict, boundary: dict, state: dict) -> bool:
    if int(boundary["road_id"]) != int(trigger["road_id"]):
        return False
    if int(boundary["lane_id"]) != int(trigger["lane_id"]):
        return False
    kind = trigger["kind"]
    right = bool(boundary["right_driving_lane"]["present"])
    if kind == "ENTER_ROAD_LANE":
        return state.get("previous_road_lane") != (
            int(boundary["road_id"]), int(boundary["lane_id"])
        )
    if kind == "ENTER_ROAD_LANE_WITH_RIGHT_DRIVING_LANE":
        return right and state.get("previous_road_lane") != (
            int(boundary["road_id"]), int(boundary["lane_id"])
        )
    if kind == "RIGHT_DRIVING_LANE_DISAPPEARS":
        return state.get("right_seen_on_target", False) and not right
    raise RuntimeError("UNKNOWN_PROSPECTIVE_TOPOLOGY_TRIGGER")


def _verified_truth(case_id: str, case: dict) -> dict | None:
    truth = case.get("evaluator_truth_artifact")
    bucket = str(case["timing_bucket"])
    if bucket not in {"T3_POST_COMMIT_RECOVERABLE", "T4_NO_SAFE_CURRENT_OPPORTUNITY"}:
        if truth is not None:
            raise RuntimeError("UNEXPECTED_TRUTH_ARTIFACT_FOR_T1_T2")
        return None
    if not isinstance(truth, dict):
        raise RuntimeError("T3_T4_TRUTH_ARTIFACT_REQUIRED")
    path = Path(str(truth.get("absolute_path", ""))).resolve()
    if not path.is_file():
        raise RuntimeError("T3_T4_TRUTH_ARTIFACT_MISSING")
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != truth.get("sha256"):
        raise RuntimeError("T3_T4_TRUTH_ARTIFACT_HASH_MISMATCH")
    proof = json.loads(path.read_text(encoding="utf-8"))
    expected_truth = (
        "OLD_EXCLUSIVE_RECOVERABLE"
        if bucket == "T3_POST_COMMIT_RECOVERABLE"
        else "NO_SAFE_CURRENT_OPPORTUNITY"
    )
    if (
        proof.get("status") != "PASS"
        or proof.get("case_id") != case_id
        or proof.get("truth_class") != expected_truth
        or proof.get("prospective_assertions", {}).get("frozen_before_native_launch") is not True
        or proof.get("prospective_assertions", {}).get("result_dependent_relabeling_allowed") is not False
    ):
        raise RuntimeError("T3_T4_TRUTH_ARTIFACT_CONTRACT_INVALID")
    return {
        "absolute_path": str(path),
        "sha256": actual,
        "truth_class": expected_truth,
        "frozen_before_native_launch": True,
    }


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
            boundary = json.loads(path.read_text(encoding="utf-8"))
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
                _write_once(
                    output / "exchange" / "acks" / f"ack_{int(boundary['sim_frame']):08d}.json",
                    {"sim_frame": int(boundary["sim_frame"]), "trigger_matched": False},
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
            _write_once(output / "evaluator" / "injection_receipt.json", evaluator_payload)
            _write_once(output / "exchange" / "runtime_update.json", asdict(runtime))
            return 0
        terminal = output / "agent_terminal.json"
        if terminal.exists():
            _write_once(
                output / "evaluator" / "injector_terminal_without_injection.json",
                {"case_id": args.case_id, "reason": "AGENT_TERMINATED_BEFORE_TRIGGER"},
            )
            return 2
        time.sleep(0.01)


if __name__ == "__main__":
    raise SystemExit(main())
