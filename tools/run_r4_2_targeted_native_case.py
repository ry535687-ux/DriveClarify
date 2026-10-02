#!/usr/bin/env python3
"""Execute one preregistered R4.2 targeted-native attempt.

This is an execution-only wrapper around the already validated native triad
runner.  It contains no decision policy and never changes a scientific input.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import traceback
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driveclarify_decision_evidence_v3 import FEATURE_FLAG as V3_FEATURE_FLAG  # noqa: E402
from driveclarify_grounded_language_v1_extension_e1_r1 import backend  # noqa: E402
from driveclarify_persistent_ambiguity_runtime_v1.convergence_observer import (  # noqa: E402
    CONVERGENCE_OBSERVER_ENV,
)
from driveclarify_persistent_ambiguity_runtime_v1.runtime import FEATURE_FLAG as PERSISTENT_FLAG  # noqa: E402
from tools.run_grounded_language_v1_triad import run as run_grounded  # noqa: E402
from tools.run_visual_behavioral_acceptance_v1 import complete, spec_for  # noqa: E402


REPORT = ROOT / "reports/driveclarify_r4_2_runtime_readiness_adjudication_and_targeted_native"
PREREGISTRATION = REPORT / "TARGETED_NATIVE_PREREGISTRATION.json"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_preregistration() -> Mapping[str, Any]:
    value = json.loads(PREREGISTRATION.read_text(encoding="utf-8"))
    if value.get("status") != "FROZEN_BEFORE_FIRST_TARGETED_NATIVE_LAUNCH":
        raise RuntimeError("TARGETED_NATIVE_PREREGISTRATION_NOT_FROZEN")
    return value


def _row(value: Mapping[str, Any], case_id: str) -> Mapping[str, Any]:
    rows = [row for row in value.get("primary_cases", ()) if row.get("case_id") == case_id]
    if len(rows) != 1:
        raise RuntimeError("TARGETED_NATIVE_CASE_ID_NOT_UNIQUE:" + case_id)
    return rows[0]


def _environment_overrides(kind: str, scenario_root: Path) -> dict[str, str]:
    fixture_ids = {
        "01_ACT": "E1R1-ACT-PHYS-001",
        "02_ACT_SHARED": "E1R1-ACT-PHYS-003",
        "04_WAIT": "E1R1-ASK-PHYS-001",
        "07_HARD_RULE": "E1R1-ACT-PHYS-003",
    }
    values = {
        PERSISTENT_FLAG: "1",
        V3_FEATURE_FLAG: "1",
        "DRIVECLARIFY_GROUNDED_LANGUAGE_V1_E1_R1": "1",
        "DRIVECLARIFY_E1R1_FIXTURE_ID": fixture_ids[kind],
        "SCENARIO_RUNNER_ROOT": str(scenario_root.resolve()),
    }
    if kind == "07_HARD_RULE":
        values[CONVERGENCE_OBSERVER_ENV] = "1"
    return values


def execute(case_id: str, attempt: str) -> Mapping[str, Any]:
    prereg = _load_preregistration()
    row = _row(prereg, case_id)
    if attempt not in {"A01", "A02"}:
        raise RuntimeError("ATTEMPT_MUST_BE_A01_OR_A02")
    if attempt == "A02" and row.get("linked_a02_permitted_only_after_zero_exposure_a01") is not True:
        raise RuntimeError("LINKED_A02_NOT_PREREGISTERED")

    run_id = str(row["attempt_ids"][attempt])
    output = REPORT / "native_attempts" / case_id / attempt
    if output.exists() and any(output.iterdir()):
        raise RuntimeError("TARGETED_NATIVE_OUTPUT_NOT_EMPTY:" + str(output))

    for item in row.get("protected_inputs", ()):
        path = ROOT / str(item["path"])
        if _sha(path) != item["sha256"]:
            raise RuntimeError("TARGETED_NATIVE_PROTECTED_INPUT_HASH_MISMATCH:" + str(path))

    kind = str(row["validated_runner_case"])
    spec, answer_delay, scenario_root = spec_for(kind, run_id)
    spec = replace(spec, seed=int(row["seed"]), episode_id=run_id)
    if spec.scenario_id != row["scenario_id"] or spec.route_id != row["route_id"]:
        raise RuntimeError("TARGETED_NATIVE_FROZEN_CASE_IDENTITY_MISMATCH")

    lease_receipt = None
    try:
        with backend.serial_gpu_lease() as lease:
            lease_receipt = lease
            result = run_grounded(
                output,
                case=str(row["runner_case"]),
                seed=spec.seed,
                method_id="driveclarify_grounded_v1_r4_2",
                device="cpu",
                control=True,
                answer="The nearer white van.",
                answer_delay=float(answer_delay),
                timeout_seconds=float(row["wall_timeout_seconds"]),
                episode_spec=spec,
                visualization=True,
                post_hoc_world_state=True,
                terminate_on_runtime_terminal=True,
                capture_desktop=True,
                environment_overrides=_environment_overrides(kind, Path(scenario_root)),
                completion_predicate=lambda live: complete(kind, live),
                desktop_capture_predicate=lambda live: complete(kind, live),
                accept_natural_evaluator_completion=True,
                allow_scientific_collision_outcome=True,
            )
    finally:
        if lease_receipt is not None and output.is_dir():
            backend._write_lease(output, lease_receipt)

    live_path = output / "GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json"
    live = json.loads(live_path.read_text(encoding="utf-8")) if live_path.is_file() else {}
    history = live.get("method_v1_decision_history") or []
    persistent_history = live.get("persistent_decision_history") or []
    exposure = {
        "observation_count": int(live.get("carla_tick_count") or live.get("dashboard_refresh_count") or 0),
        "model_forward_count": int(live.get("normal_simlingo_forward_count") or 0),
        "candidate_forward_count": int(live.get("candidate_simlingo_forward_count") or 0),
        "decision_count": max(len(history), len(persistent_history)),
        "planner_pid_control_count": int(live.get("existing_pid_invocation_count") or 0),
        "vehicle_movement_scientific_evidence_count": int(live.get("control_observation_count") or 0),
    }
    return {
        "case_id": case_id,
        "attempt": attempt,
        "run_id": run_id,
        "result": result,
        "accepted_preregistered_opportunity_observed": complete(kind, live),
        "scientific_exposure": exposure,
        "live_receipt_sha256": _sha(live_path) if live_path.is_file() else None,
        "output": str(output),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("case_id")
    parser.add_argument("attempt", choices=("A01", "A02"))
    args = parser.parse_args()
    try:
        print(json.dumps(execute(args.case_id, args.attempt), indent=2, sort_keys=True))
        return 0
    except Exception as error:
        print(json.dumps({
            "status": "BLOCKED_TARGETED_NATIVE_EXECUTION_WRAPPER",
            "case_id": args.case_id,
            "attempt": args.attempt,
            "error_type": type(error).__name__,
            "error": str(error),
            "traceback": traceback.format_exc(),
        }, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
