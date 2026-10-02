#!/usr/bin/env python3
"""Execute one frozen R4.2 remaining-class targeted-native case."""

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

from driveclarify_decision_evidence_v3 import FEATURE_FLAG as V3_FEATURE_FLAG  # noqa: E402
from driveclarify_grounded_language_v1_extension_e1_r1 import backend  # noqa: E402
from driveclarify_persistent_ambiguity_runtime_v1.convergence_observer import (  # noqa: E402
    CONVERGENCE_OBSERVER_ENV,
)
from driveclarify_persistent_ambiguity_runtime_v1.runtime import (  # noqa: E402
    FEATURE_FLAG as PERSISTENT_FLAG,
)
from tools.run_grounded_language_v1_triad import (  # noqa: E402
    environment as grounded_environment,
    preflight as grounded_preflight,
    run as run_grounded,
)
from tools.run_visual_behavioral_acceptance_v1 import (  # noqa: E402
    capture_ready,
    complete,
    spec_for,
)


REPORT = ROOT / "reports/driveclarify_r4_2_remaining_targeted_native_and_final_freeze"
PREREGISTRATION = REPORT / "REMAINING_TARGETED_NATIVE_PREREGISTRATION.json"
OLD_PREREGISTRATION = ROOT / "reports/driveclarify_r4_2_runtime_readiness_adjudication_and_targeted_native/TARGETED_NATIVE_PREREGISTRATION.json"
OUTPUT_ROOT = REPORT / "native_attempts"
PREFLIGHT_ROOT = REPORT / "preflight"
SCENARIO_ROOT = REPORT / "scenario_root"
EXPECTED_PROTOCOL_HASH = "717fdfefa61e2e9eb79fbcc3650d881cd65a2b63185a639206ab4ecb4f850705"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _load() -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    prereg = json.loads(PREREGISTRATION.read_text(encoding="utf-8"))
    old = json.loads(OLD_PREREGISTRATION.read_text(encoding="utf-8"))
    if prereg.get("status") != "FROZEN_BEFORE_FIRST_REMAINING_NATIVE_LAUNCH":
        raise RuntimeError("REMAINING_PREREGISTRATION_NOT_FROZEN")
    if _canonical_sha(prereg["protocol_preimage"]) != prereg.get("protocol_hash"):
        raise RuntimeError("REMAINING_PROTOCOL_HASH_MISMATCH")
    if prereg.get("protocol_hash") != EXPECTED_PROTOCOL_HASH:
        raise RuntimeError("REMAINING_PROTOCOL_IDENTITY_MISMATCH")
    for item in prereg.get("protected_inputs", {}).values():
        if _sha(ROOT / item["path"]) != item["sha256"]:
            raise RuntimeError("REMAINING_PROTECTED_INPUT_HASH_MISMATCH:" + item["path"])
    return prereg, old


def _new_row(prereg: Mapping[str, Any], case_id: str) -> Mapping[str, Any]:
    rows = [row for row in prereg["protocol_preimage"]["new_cases"] if row["case_id"] == case_id]
    if len(rows) != 1:
        raise RuntimeError("REMAINING_NEW_CASE_BINDING_NOT_ONE:" + case_id)
    return rows[0]


def _old_fallback_row(old: Mapping[str, Any]) -> Mapping[str, Any]:
    rows = [row for row in old["primary_cases"] if row["case_id"] == "TN04_FALLBACK"]
    if len(rows) != 1:
        raise RuntimeError("TN04_FALLBACK_BINDING_NOT_ONE")
    return rows[0]


def _case(prereg: Mapping[str, Any], old: Mapping[str, Any], case_id: str, attempt: str):
    if attempt != "A01":
        raise RuntimeError("A02_REQUIRES_STRICT_ZERO_EXPOSURE_A01_ADJUDICATION")
    if case_id == "R42REM_ACT_SHARED_001":
        row = _new_row(prereg, case_id)
        kind, runner_case, fixture_id = "02_ACT_SHARED", "act", "E1R1-ACT-PHYS-003"
        route = REPORT / "R42REM_ACT_SHARED_001.xml"
        answer, delay = "The nearer white van.", 0.1
        base, _, _ = spec_for(kind, row["attempt_ids"][attempt])
        spec = replace(
            base,
            episode_id=row["attempt_ids"][attempt],
            scenario_id=case_id,
            seed=int(row["seed"]),
            town="Town03",
            route_id=str(row["route_id"]),
            route_path=route.resolve(),
            raw_instruction=str(row["instruction"]),
            information_expected=False,
            schedule_sha256=_sha(route),
        )
        scenario_root = SCENARIO_ROOT
    elif case_id == "R42REM_ASK_WAIT_001":
        row = _new_row(prereg, case_id)
        kind, runner_case, fixture_id = "04_WAIT", "ask", "E1R1-ASK-PHYS-001"
        route = REPORT / "R42REM_ASK_WAIT_001.xml"
        answer, delay = str(row["answer"]), float(row["answer_delay_seconds"])
        base, _, _ = spec_for(kind, row["attempt_ids"][attempt])
        spec = replace(
            base,
            episode_id=row["attempt_ids"][attempt],
            scenario_id=case_id,
            seed=int(row["seed"]),
            town="Town03",
            route_id=str(row["route_id"]),
            route_path=route.resolve(),
            raw_instruction=str(row["instruction"]),
            information_expected=True,
            schedule_sha256=_sha(route),
        )
        scenario_root = SCENARIO_ROOT
    elif case_id == "TN04_FALLBACK":
        row = _old_fallback_row(old)
        kind, runner_case, fixture_id = "07_HARD_RULE", "act", "E1R1-ACT-PHYS-003"
        answer, delay = "The nearer white van.", 0.1
        spec, _, scenario_root = spec_for(kind, row["attempt_ids"][attempt])
        spec = replace(spec, seed=int(row["seed"]), episode_id=row["attempt_ids"][attempt])
        for item in row["protected_inputs"]:
            if _sha(ROOT / item["path"]) != item["sha256"]:
                raise RuntimeError("TN04_PROTECTED_INPUT_HASH_MISMATCH:" + item["path"])
    else:
        raise RuntimeError("UNKNOWN_REMAINING_CASE:" + case_id)
    return row, kind, runner_case, fixture_id, spec, Path(scenario_root), answer, delay


def _overrides(kind: str, fixture_id: str, scenario_root: Path) -> dict[str, str]:
    values = {
        PERSISTENT_FLAG: "1",
        V3_FEATURE_FLAG: "1",
        "DRIVECLARIFY_GROUNDED_LANGUAGE_V1_E1_R1": "1",
        "DRIVECLARIFY_E1R1_FIXTURE_ID": fixture_id,
        "SCENARIO_RUNNER_ROOT": str(scenario_root.resolve()),
    }
    if kind == "07_HARD_RULE":
        values[CONVERGENCE_OBSERVER_ENV] = "1"
    return values


def _fallback_complete(live: Mapping[str, Any]) -> bool:
    method = live.get("method_v1_decision_history") or []
    gates = live.get("hard_gate_evidence_history") or []
    return bool(
        any(row.get("decision_label") == "FALLBACK" for row in method)
        and any(
            isinstance(row.get("route_local_hard_rule"), Mapping)
            and row["route_local_hard_rule"].get("status") == "VERIFIED_ROUTE_LOCAL_BLOCKED"
            and row.get("runtime_provider_identity_match") is True
            for row in gates
        )
    )


def _complete(kind: str, live: Mapping[str, Any]) -> bool:
    return _fallback_complete(live) if kind == "07_HARD_RULE" else complete(kind, live)


def preflight_only(case_id: str, attempt: str) -> Mapping[str, Any]:
    prereg, old = _load()
    row, kind, runner_case, fixture_id, spec, scenario_root, answer, delay = _case(
        prereg, old, case_id, attempt
    )
    output = PREFLIGHT_ROOT / case_id / attempt
    if output.exists() and any(output.iterdir()):
        raise RuntimeError("REMAINING_PREFLIGHT_OUTPUT_NOT_EMPTY:" + str(output))
    values = grounded_environment(
        spec,
        output,
        case=runner_case,
        device="cpu",
        control=True,
        answer=answer,
        answer_delay=delay,
        visualization=True,
        post_hoc_world_state=True,
    )
    values.update(_overrides(kind, fixture_id, scenario_root))
    checked = grounded_preflight(spec, output, values, case=runner_case, control=True)
    return {
        "status": checked["status"],
        "case_id": case_id,
        "attempt": attempt,
        "run_id": spec.episode_id,
        "route_id": spec.route_id,
        "seed": spec.seed,
        "protocol_hash": prereg["protocol_hash"],
        "native_preflight": checked,
        "output": str(output),
    }


def execute(case_id: str, attempt: str) -> Mapping[str, Any]:
    prereg, old = _load()
    row, kind, runner_case, fixture_id, spec, scenario_root, answer, delay = _case(
        prereg, old, case_id, attempt
    )
    output = OUTPUT_ROOT / case_id / attempt
    if output.exists() and any(output.iterdir()):
        raise RuntimeError("REMAINING_OUTPUT_NOT_EMPTY:" + str(output))
    lease_receipt = None
    try:
        with backend.serial_gpu_lease() as lease:
            lease_receipt = lease
            result = run_grounded(
                output,
                case=runner_case,
                seed=spec.seed,
                method_id="driveclarify_grounded_v1_r4_2",
                device="cpu",
                control=True,
                answer=answer,
                answer_delay=delay,
                timeout_seconds=600.0,
                episode_spec=spec,
                visualization=True,
                post_hoc_world_state=True,
                terminate_on_runtime_terminal=True,
                capture_desktop=True,
                environment_overrides=_overrides(kind, fixture_id, scenario_root),
                completion_predicate=lambda live: _complete(kind, live),
                desktop_capture_predicate=lambda live: (
                    _fallback_complete(live)
                    if kind == "07_HARD_RULE"
                    else capture_ready(kind, live)
                ),
                accept_natural_evaluator_completion=True,
                allow_scientific_collision_outcome=True,
            )
    finally:
        if lease_receipt is not None and output.is_dir():
            backend._write_lease(output, lease_receipt)
    live_path = output / "GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json"
    live = json.loads(live_path.read_text(encoding="utf-8")) if live_path.is_file() else {}
    method = live.get("method_v1_decision_history") or []
    persistent = live.get("persistent_decision_history") or []
    return {
        "status": "PASS" if _complete(kind, live) else "SCIENTIFIC_RESULT_NOT_ACCEPTED",
        "case_id": case_id,
        "attempt": attempt,
        "run_id": spec.episode_id,
        "accepted_behavior_observed": _complete(kind, live),
        "scientific_exposure": {
            "observation_count": int(live.get("carla_tick_count") or live.get("dashboard_refresh_count") or 0),
            "normal_model_forward_count": int(live.get("normal_simlingo_forward_count") or 0),
            "candidate_forward_count": int(live.get("candidate_simlingo_forward_count") or 0),
            "decision_count": max(len(method), len(persistent)),
            "existing_pid_control_count": int(live.get("existing_pid_invocation_count") or 0),
            "vehicle_scientific_movement_evidence_count": int(live.get("control_observation_count") or 0),
        },
        "native_result": result,
        "live_receipt_sha256": _sha(live_path) if live_path.is_file() else None,
        "output": str(output),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("case_id", choices=("R42REM_ACT_SHARED_001", "R42REM_ASK_WAIT_001", "TN04_FALLBACK"))
    parser.add_argument("attempt", choices=("A01", "A02"))
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    try:
        value = preflight_only(args.case_id, args.attempt) if args.preflight_only else execute(args.case_id, args.attempt)
        print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if value.get("status") == "PASS" else 3
    except Exception as error:
        print(json.dumps({
            "status": "BLOCKED_R4_2_REMAINING_NATIVE_WRAPPER",
            "case_id": args.case_id,
            "attempt": args.attempt,
            "error_type": type(error).__name__,
            "error": str(error),
            "traceback": traceback.format_exc(),
        }, ensure_ascii=False, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
