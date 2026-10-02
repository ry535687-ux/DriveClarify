#!/usr/bin/env python3
"""Execute the single frozen R4.2 fresh-ACT native case.

This is execution plumbing only.  It reads the frozen V2 preregistration,
reuses the certified production runtime, and contains no decision policy.
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
from driveclarify_persistent_ambiguity_runtime_v1.runtime import (  # noqa: E402
    FEATURE_FLAG as PERSISTENT_FLAG,
)
from tools.run_grounded_language_v1_triad import (  # noqa: E402
    environment as grounded_environment,
    preflight as grounded_preflight,
    run as run_grounded,
)
from tools.run_visual_behavioral_acceptance_v1 import complete, spec_for  # noqa: E402


SOURCE_REPORT = ROOT / "reports/driveclarify_r4_2_route_local_minimal_adjudication_and_repair"
QA_REPORT = ROOT / "reports/driveclarify_r4_2_protocol_convergence_and_prefreeze_qa"
OUTPUT_ROOT = ROOT / "reports/driveclarify_r4_2_fresh_act_native_launch/native_attempts"
PREFLIGHT_ROOT = ROOT / "reports/driveclarify_r4_2_fresh_act_native_launch/preflight"
PREREGISTRATION = SOURCE_REPORT / "FRESH_ACT_OPPORTUNITY_PREREGISTRATION.json"
QA_RECEIPT = QA_REPORT / "ENGINEERING_QA_RECEIPT.json"
QA_MANIFEST = QA_REPORT / "ARTIFACT_HASHES.json"
ROUTE = SOURCE_REPORT / "R42AOV2_ACT_001.xml"
SCENARIO_ROOT = SOURCE_REPORT / "scenario_root"
PRIOR_TN01_REPORT = ROOT / "reports/driveclarify_r4_2_runtime_readiness_adjudication_and_targeted_native"
EXPECTED_QA_MANIFEST_SHA256 = "7b1e39d07bad9393578f1b3fb8a26de1a2e32700513164a36eee52a7d150f0ab"
EXPECTED_TN01_MANIFEST_SHA256 = "a385f932bec8040e5036029e2c1b5946ed251acf8581ffe31844e3054ae006a9"
EXPECTED_TN01_RECEIPT_SHA256 = "059cde0b722bd8e4032c2f95acdc5fefc1fe9e7322981cfd76b088b90a0f452a"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_sha(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _load() -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    prereg = json.loads(PREREGISTRATION.read_text(encoding="utf-8"))
    qa = json.loads(QA_RECEIPT.read_text(encoding="utf-8"))
    if prereg.get("status") != "FROZEN_BEFORE_A01":
        raise RuntimeError("FRESH_ACT_PREREGISTRATION_NOT_FROZEN")
    if qa.get("status") != "PASS_R4_2_ENGINEERING_QA_SEALED_READY_FOR_SEPARATE_NATIVE_AUTHORIZATION":
        raise RuntimeError("ENGINEERING_QA_NOT_SEALED")
    if qa.get("focused_gate", {}).get("passed") != 5 or qa.get("focused_gate", {}).get("failed") != 0:
        raise RuntimeError("ENGINEERING_QA_FOCUSED_GATE_NOT_EXACT")
    if _canonical_sha(prereg["protocol_preimage"]) != prereg.get("protocol_hash"):
        raise RuntimeError("FRESH_ACT_PROTOCOL_HASH_MISMATCH")
    if _sha(QA_MANIFEST) != EXPECTED_QA_MANIFEST_SHA256:
        raise RuntimeError("ENGINEERING_QA_MANIFEST_HASH_MISMATCH")
    if _sha(PRIOR_TN01_REPORT / "ARTIFACT_HASHES.json") != EXPECTED_TN01_MANIFEST_SHA256:
        raise RuntimeError("PRIOR_TN01_MANIFEST_HASH_MISMATCH")
    if _sha(PRIOR_TN01_REPORT / "TARGETED_NATIVE_CASE_RECEIPTS.jsonl") != EXPECTED_TN01_RECEIPT_SHA256:
        raise RuntimeError("PRIOR_TN01_RECEIPT_HASH_MISMATCH")
    for relative, expected in prereg.get("protected_inputs", {}).items():
        if _sha(ROOT / relative) != expected:
            raise RuntimeError("FRESH_ACT_PROTECTED_INPUT_HASH_MISMATCH:" + relative)
    return prereg, qa


def _spec(prereg: Mapping[str, Any], attempt: str):
    case = prereg["case"]
    run_id = str(case["attempt_ids"][attempt])
    base, _, _ = spec_for("01_ACT", run_id)
    return replace(
        base,
        episode_id=run_id,
        scenario_id=str(case["case_id"]),
        seed=int(case["seed"]),
        town=str(case["town"]),
        route_id=str(case["route_id"]),
        route_path=ROUTE.resolve(),
        raw_instruction=str(case["instruction"]),
        information_expected=False,
        schedule_sha256=_sha(ROUTE),
    )


def _overrides() -> dict[str, str]:
    return {
        PERSISTENT_FLAG: "1",
        V3_FEATURE_FLAG: "1",
        "DRIVECLARIFY_GROUNDED_LANGUAGE_V1_E1_R1": "1",
        "DRIVECLARIFY_E1R1_FIXTURE_ID": "E1R1-ACT-PHYS-001",
        "SCENARIO_RUNNER_ROOT": str(SCENARIO_ROOT.resolve()),
    }


def preflight_only(attempt: str) -> Mapping[str, Any]:
    prereg, _ = _load()
    if attempt != "A01":
        raise RuntimeError("A02_REQUIRES_SEPARATE_ZERO_EXPOSURE_A01_ADJUDICATION")
    output = PREFLIGHT_ROOT / str(prereg["case"]["case_id"]) / attempt
    if output.exists() and any(output.iterdir()):
        raise RuntimeError("FRESH_ACT_OUTPUT_NOT_EMPTY:" + str(output))
    spec = _spec(prereg, attempt)
    values = grounded_environment(
        spec,
        output,
        case="act",
        device="cpu",
        control=True,
        answer="The white van.",
        answer_delay=0.1,
        visualization=True,
        post_hoc_world_state=True,
    )
    values.update(_overrides())
    checked = grounded_preflight(spec, output, values, case="act", control=True)
    return {
        "status": checked["status"],
        "attempt": attempt,
        "run_id": spec.episode_id,
        "case_id": spec.scenario_id,
        "route_id": spec.route_id,
        "route_sha256": _sha(ROUTE),
        "scenario_module_sha256": prereg["protocol_preimage"]["scenario_module_sha256"],
        "protocol_hash": prereg["protocol_hash"],
        "native_preflight": checked,
        "output": str(output),
    }


def execute(attempt: str) -> Mapping[str, Any]:
    prereg, _ = _load()
    if attempt != "A01":
        raise RuntimeError("A02_REQUIRES_SEPARATE_ZERO_EXPOSURE_A01_ADJUDICATION")
    case = prereg["case"]
    output = OUTPUT_ROOT / str(case["case_id"]) / attempt
    if output.exists() and any(output.iterdir()):
        raise RuntimeError("FRESH_ACT_OUTPUT_NOT_EMPTY:" + str(output))
    spec = _spec(prereg, attempt)
    lease_receipt = None
    try:
        with backend.serial_gpu_lease() as lease:
            lease_receipt = lease
            result = run_grounded(
                output,
                case="act",
                seed=spec.seed,
                method_id="driveclarify_grounded_v1_r4_2",
                device="cpu",
                control=True,
                answer="The white van.",
                answer_delay=0.1,
                timeout_seconds=float(case["wall_timeout_seconds"]),
                episode_spec=spec,
                visualization=True,
                post_hoc_world_state=True,
                terminate_on_runtime_terminal=True,
                capture_desktop=True,
                environment_overrides=_overrides(),
                completion_predicate=lambda live: complete("01_ACT", live),
                desktop_capture_predicate=lambda live: complete("01_ACT", live),
                accept_natural_evaluator_completion=True,
                allow_scientific_collision_outcome=True,
            )
    finally:
        if lease_receipt is not None and output.is_dir():
            backend._write_lease(output, lease_receipt)

    live_path = output / "GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json"
    live = json.loads(live_path.read_text(encoding="utf-8")) if live_path.is_file() else {}
    method_history = live.get("method_v1_decision_history") or []
    persistent_history = live.get("persistent_decision_history") or []
    exposure = {
        "observation_count": int(live.get("carla_tick_count") or live.get("dashboard_refresh_count") or 0),
        "normal_model_forward_count": int(live.get("normal_simlingo_forward_count") or 0),
        "candidate_forward_count": int(live.get("candidate_simlingo_forward_count") or 0),
        "decision_count": max(len(method_history), len(persistent_history)),
        "planner_pid_control_count": int(live.get("existing_pid_invocation_count") or 0),
        "vehicle_movement_scientific_evidence_count": int(live.get("control_observation_count") or 0),
    }
    return {
        "case_id": str(case["case_id"]),
        "attempt": attempt,
        "run_id": spec.episode_id,
        "result": result,
        "accepted_preregistered_opportunity_observed": complete("01_ACT", live),
        "scientific_exposure": exposure,
        "live_receipt_sha256": _sha(live_path) if live_path.is_file() else None,
        "output": str(output),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("attempt", choices=("A01", "A02"))
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    try:
        result = preflight_only(args.attempt) if args.preflight_only else execute(args.attempt)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if result.get("status", "PASS") == "PASS" else 2
    except Exception as error:
        print(json.dumps({
            "status": "BLOCKED_R4_2_FRESH_ACT_NATIVE_WRAPPER",
            "attempt": args.attempt,
            "error_type": type(error).__name__,
            "error": str(error),
            "traceback": traceback.format_exc(),
        }, ensure_ascii=False, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
