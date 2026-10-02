#!/usr/bin/env python3
"""Run the single pre-registered Method V1 R3 targeted native validation.

This is a TRAIN-only validation harness.  It reuses the frozen visual-suite
fixture and production runtime, and contains no decision or control policy.
"""

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
from driveclarify_persistent_ambiguity_runtime_v1.runtime import FEATURE_FLAG as PERSISTENT_FLAG  # noqa: E402
from tools import run_method_v1_act_semantics_targeted_native_validation as targeted  # noqa: E402
from tools.run_grounded_language_v1_triad import run as run_grounded  # noqa: E402


REPORT_ROOT = ROOT / "reports/driveclarify_method_v1_r3_native_freeze_protocol_and_formal_train_preparation"
PHASE_ROOT = REPORT_ROOT / "01_TARGETED_NATIVE_VALIDATION"
ARTIFACT_ROOT = ROOT / "artifacts/driveclarify_method_v1_r3_targeted_native_validation"
PREREGISTRATION = PHASE_ROOT / "NATIVE_SCIENTIFIC_RUN_PREREGISTRATION.json"
RUN_ID = "DCMV1-R3-NATIVE-HR-20260815-R0"
FIXTURE_ID = "E1R1-ACT-PHYS-003"
SEED = 6301


def _history(live: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    value = live.get("persistent_decision_history")
    return [row for row in value if isinstance(row, Mapping)] if isinstance(value, list) else []


def _method_history(live: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    value = live.get("method_v1_decision_history")
    return [row for row in value if isinstance(row, Mapping)] if isinstance(value, list) else []


def complete(live: Mapping[str, Any]) -> bool:
    history = _history(live)
    method = _method_history(live)
    hard_rule_veto = any(
        isinstance(row.get("m2b_inputs"), Mapping)
        and row["m2b_inputs"].get("hard_safety_gate") is True
        and row["m2b_inputs"].get("hard_rule_gate") is False
        for row in history
    )
    convergence_gate_error = any(
        isinstance(row, Mapping)
        and row.get("stage") == "METHOD_V1_CONVERGENCE_FRESH_REPLAN"
        and row.get("message") == "CONVERGENCE_UNIQUE_M2B_FAIL_CLOSED"
        for row in (live.get("errors") or ())
    )
    candidate_forwards_before = max(
        (
            int(row.get("cumulative_compute_accounting", {}).get("candidate_forward_count") or 0)
            for row in history
        ),
        default=0,
    )
    return bool(
        live.get("official_adapter_on_production_path") is True
        and live.get("production_candidate_provider")
        == "driveclarify_official_dreaming_adapter.adapter.OfficialDreamingCandidateForwardProvider"
        and str(live.get("production_candidate_provider_object_identity", "")).startswith(
            "driveclarify_official_dreaming_adapter.adapter.OfficialDreamingCandidateForwardProvider@"
        )
        and live.get("convergence_effective_k") == 1
        and live.get("convergence_old_bundle_invalidated") is True
        and live.get("convergence_shared_authority_revoked") is True
        and live.get("convergence_stale_plan_selected") is False
        and int(live.get("candidate_simlingo_forward_count") or 0)
        == candidate_forwards_before + 1
        and live.get("convergence_fake_wait_count") == 0
        and live.get("internal_lifecycle_state") is None
        and live.get("decision_label") == "FALLBACK"
        and live.get("decision_reason") == "FRESH_REPLAN_FAILED"
        and hard_rule_veto
        and convergence_gate_error
        and any(row.get("decision_label") == "FALLBACK" for row in method)
    )


def _preflight() -> tuple[Any, Path, Mapping[str, Any]]:
    prereg = json.loads(PREREGISTRATION.read_text(encoding="utf-8"))
    if prereg.get("status") != "FROZEN_BEFORE_SCIENTIFIC_EXECUTION":
        raise RuntimeError("R3_NATIVE_PREREGISTRATION_NOT_FROZEN")
    if prereg.get("run_id") != RUN_ID or prereg.get("scientific_retry_policy") != 0:
        raise RuntimeError("R3_NATIVE_PREREGISTRATION_ID_OR_RETRY_MISMATCH")
    for item in prereg.get("protected_inputs", ()):
        path = ROOT / str(item["path"])
        if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            raise RuntimeError("R3_NATIVE_PROTECTED_INPUT_HASH_MISMATCH:" + str(path))
    targeted._manifest_preflight()
    spec = backend.resolve_episode(
        fixture_id=FIXTURE_ID,
        method_id="driveclarify_grounded_v1",
        episode_id=RUN_ID,
    )
    return replace(spec, seed=SEED), targeted.SCENARIO_ROOT, prereg


def execute(timeout_seconds: float) -> Mapping[str, Any]:
    spec, scenario_root, _ = _preflight()
    output = ARTIFACT_ROOT / RUN_ID
    if output.exists() and any(output.iterdir()):
        raise RuntimeError("R3_NATIVE_OUTPUT_NOT_EMPTY:" + str(output))
    overrides = {
        PERSISTENT_FLAG: "1",
        V3_FEATURE_FLAG: "1",
        "DRIVECLARIFY_GROUNDED_LANGUAGE_V1_E1_R1": "1",
        "DRIVECLARIFY_E1R1_FIXTURE_ID": FIXTURE_ID,
        "SCENARIO_RUNNER_ROOT": str(Path(scenario_root).resolve()),
        CONVERGENCE_OBSERVER_ENV: "1",
    }
    lease_receipt = None
    try:
        with backend.serial_gpu_lease() as lease:
            lease_receipt = lease
            result = run_grounded(
                output,
                case="act",
                seed=SEED,
                method_id="driveclarify_grounded_v1",
                device="cpu",
                control=True,
                answer="The nearer white van.",
                answer_delay=0.1,
                timeout_seconds=timeout_seconds,
                episode_spec=spec,
                visualization=True,
                post_hoc_world_state=True,
                terminate_on_runtime_terminal=True,
                capture_desktop=True,
                environment_overrides=overrides,
                completion_predicate=complete,
                desktop_capture_predicate=complete,
                accept_natural_evaluator_completion=True,
                allow_scientific_collision_outcome=True,
            )
    finally:
        if lease_receipt is not None and output.is_dir():
            backend._write_lease(output, lease_receipt)
    live_path = output / "GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json"
    live = json.loads(live_path.read_text(encoding="utf-8")) if live_path.is_file() else {}
    return {
        "schema_version": "driveclarify.method_v1.r3.targeted_native.execution.v1",
        "status": "PASS" if complete(live) else "BLOCKED",
        "run_id": RUN_ID,
        "scientific_run_count": 1,
        "scientific_retry_count": 0,
        "accepted_semantics_observed": complete(live),
        "desktop_capture_status": result.get("desktop_capture_status"),
        "native_runner_status": result.get("status"),
        "cleanup_status": result.get("cleanup_status"),
        "evaluator_return_code": result.get("evaluator_return_code"),
        "output": str(output),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout-seconds", type=float, default=600.0)
    parser.add_argument("--preflight-import-only", action="store_true")
    args = parser.parse_args()
    try:
        spec, scenario_root, prereg = _preflight()
        if args.preflight_import_only:
            heavy = sorted(
                name for name in sys.modules
                if name.split(".", 1)[0] in {"carla", "torch"}
            )
            value = {
                "status": "PASS" if not heavy else "BLOCKED_HEAVY_IMPORT",
                "run_id": RUN_ID,
                "scenario_id": spec.scenario_id,
                "seed": spec.seed,
                "scenario_root": str(scenario_root),
                "preregistration_sha256": hashlib.sha256(PREREGISTRATION.read_bytes()).hexdigest(),
                "scientific_run_count_planned": prereg["scientific_run_count_planned"],
                "heavy_modules": heavy,
            }
            print(json.dumps(value, indent=2, sort_keys=True))
            return 0 if not heavy else 2
        value = execute(args.timeout_seconds)
        print(json.dumps(value, indent=2, sort_keys=True))
        return 0 if value["status"] == "PASS" and value["desktop_capture_status"] == "PASS_VALIDATED_NATIVE_DESKTOP" else 3
    except Exception as error:
        print(json.dumps({
            "status": "BLOCKED_R3_TARGETED_NATIVE_ENGINEERING_FAILURE",
            "error_type": type(error).__name__,
            "error": str(error),
            "traceback": traceback.format_exc(),
        }, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
