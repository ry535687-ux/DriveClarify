#!/usr/bin/env python3
"""Run one bounded native TRAIN-only decision activation scenario."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import traceback
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driveclarify_decision_activation_v1.contracts import scenario  # noqa: E402
from driveclarify_grounded_language_v1_extension_e1_r1 import backend  # noqa: E402
from driveclarify_persistent_ambiguity_runtime_v1.runtime import FEATURE_FLAG  # noqa: E402
from tools.run_grounded_language_v1_triad import run as run_grounded  # noqa: E402


RUNS = {
    "L-ACTSHARED-R1": ("DA-ACTSHARED-001", "ACT_SHARED", 0.1),
    "L-ACTSHARED-R2": ("DA-ACTSHARED-001", "ACT_SHARED", 0.1),
    "L-ASK-R1": ("DA-ASK-001", "ASK", 0.1),
    "L-ASK-R2": ("DA-ASK-001", "ASK", 0.1),
    "L-WAIT-R1": ("DA-WAIT-001", "WAIT", 1.0),
    "L-WAIT-R2": ("DA-WAIT-001", "WAIT", 1.0),
}
ARTIFACT_ROOT = ROOT / "artifacts/driveclarify_decision_policy_reachability_and_live_activation_closure_v1"


def _history(live: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    value = live.get("persistent_decision_history")
    return [row for row in value if isinstance(row, Mapping)] if isinstance(value, list) else []


def desktop_ready(target: str, live: Mapping[str, Any]) -> bool:
    return bool(
        isinstance(live.get("decision_window_dashboard"), Mapping)
        and any(row.get("decision") == target for row in _history(live))
    )


def lifecycle_ready(target: str, live: Mapping[str, Any]) -> bool:
    history = _history(live)
    indexes = [index for index, row in enumerate(history) if row.get("decision") == target]
    if not indexes:
        return False
    episode = live.get("persistent_ambiguity_episode")
    if not isinstance(episode, Mapping):
        return False
    events = {
        row.get("event_type")
        for row in episode.get("history", ())
        if isinstance(row, Mapping)
    }
    if target == "ACT_SHARED":
        first = history[indexes[0]]
        return bool(
            episode.get("semantic_state") == "UNRESOLVED"
            and "DECISION_ACT_SHARED" in events
            and "SHARED_WINDOW_CONSUMED" in events
            and any(
                str(row.get("source_frame_id")) != str(first.get("source_frame_id"))
                and str(row.get("bundle_id")) != str(first.get("bundle_id"))
                for row in history[indexes[0] + 1 :]
            )
        )
    if target == "ASK":
        return bool(
            "ASK_ISSUED" in events
            and "ANSWER_ARRIVED_VALID" in events
            and "LATEST_REPLAN_UNIQUE" in events
            and live.get("persistent_old_bundle_invalidated_before_replan") is True
            and isinstance(live.get("fresh_replan"), Mapping)
        )
    return bool(
        "WAIT" in {row.get("decision") for row in history}
        and live.get("persistent_answer_matched_active_query") is True
        and not bool(live.get("holding_active"))
    )


def episode_spec(run_id: str):
    scenario_id, _target, _delay = RUNS[run_id]
    row = scenario(scenario_id)
    base = backend.resolve_episode(
        fixture_id="E1R1-ASK-PHYS-001",
        method_id="driveclarify_grounded_v1",
        episode_id="decision-activation-base",
    )
    route_path = (ROOT / "driveclarify_decision_activation_v1/fixtures" / (scenario_id + ".xml")).resolve()
    route_sha256 = hashlib.sha256(route_path.read_bytes()).hexdigest()
    return replace(
        base,
        episode_id="DC-DECISION-ACTIVATION-{}-20260813".format(run_id),
        scenario_id=scenario_id,
        split="train",
        seed=6101 if run_id.endswith("R1") else 6102,
        town=row["town"],
        route_id=row["route_id"],
        route_path=route_path,
        raw_instruction=row["instruction"],
        schedule_sha256=route_sha256,
    )


def execute(run_id: str, timeout_seconds: float) -> Mapping[str, Any]:
    scenario_id, target, answer_delay = RUNS[run_id]
    output = ARTIFACT_ROOT / run_id
    if output.exists() and any(output.iterdir()):
        raise RuntimeError("DECISION_ACTIVATION_OUTPUT_NOT_EMPTY:" + str(output))
    spec = episode_spec(run_id)
    lease_receipt = None
    try:
        with backend.serial_gpu_lease() as lease:
            lease_receipt = lease
            return run_grounded(
                output,
                case="ask",
                seed=spec.seed,
                method_id="driveclarify_grounded_v1",
                device="cpu",
                control=True,
                answer="The nearer white van.",
                answer_delay=answer_delay,
                timeout_seconds=timeout_seconds,
                episode_spec=spec,
                visualization=True,
                post_hoc_world_state=True,
                terminate_on_runtime_terminal=True,
                capture_desktop=True,
                environment_overrides={
                    FEATURE_FLAG: "1",
                    "DRIVECLARIFY_GROUNDED_LANGUAGE_V1_E1_R1": "1",
                    "SCENARIO_RUNNER_ROOT": str(
                        (ROOT / "driveclarify_decision_activation_v1/scenario_root").resolve()
                    ),
                },
                completion_predicate=lambda live: lifecycle_ready(target, live),
                desktop_capture_predicate=lambda live: desktop_ready(target, live),
                accept_natural_evaluator_completion=True,
                allow_scientific_collision_outcome=True,
            )
    finally:
        if lease_receipt is not None and output.is_dir():
            backend._write_lease(output, lease_receipt)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id", choices=tuple(RUNS))
    parser.add_argument("--timeout-seconds", type=float, default=420.0)
    parser.add_argument("--preflight-import-only", action="store_true")
    args = parser.parse_args()
    if args.preflight_import_only:
        heavy = sorted(name for name in sys.modules if name.split(".", 1)[0] in {"carla", "torch"})
        print(json.dumps({
            "status": "PASS_IMPORT_ONLY" if not heavy else "BLOCKED_HEAVY_IMPORT",
            "run_id": args.run_id,
            "scenario": RUNS[args.run_id][0],
            "split": "TRAIN",
            "heavy_modules": heavy,
        }, indent=2))
        return 0 if not heavy else 2
    try:
        result = execute(args.run_id, args.timeout_seconds)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if str(result.get("status", "")).startswith("PASS_") else 2
    except Exception as error:
        print(json.dumps({
            "status": "BLOCKED_DECISION_ACTIVATION_ENGINEERING_FAILURE",
            "run_id": args.run_id,
            "error_type": type(error).__name__,
            "error": str(error),
            "traceback": traceback.format_exc(),
            "valid_scientific_run": False,
        }, ensure_ascii=False, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
