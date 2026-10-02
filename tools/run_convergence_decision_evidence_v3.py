#!/usr/bin/env python3
"""Run one immutable visible-native V3.1 convergence preservation case."""

from __future__ import annotations

import argparse
import datetime as dt
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
from driveclarify_persistent_ambiguity_runtime_v1.runtime import (  # noqa: E402
    FEATURE_FLAG as V1_FEATURE_FLAG,
)
from tools import run_method_v1_act_semantics_targeted_native_validation as frozen  # noqa: E402
from tools.run_grounded_language_v1_triad import run as run_grounded  # noqa: E402


REPORT = ROOT / "reports/driveclarify_minimum_sufficient_decision_evidence_v3_revision_and_native_validation"
ARTIFACT = ROOT / "artifacts/driveclarify_minimum_sufficient_decision_evidence_v3_revision_and_native_validation"
CASES = {
    "INITIAL": {
        "run_id": "DCV3-C-I-0814", "seed": 6301,
        "receipt": "CONVERGENCE_V3_NATIVE_RECEIPT.json",
    },
    "REPEAT": {
        "run_id": "DCV3-C-R-0814", "seed": 6302,
        "receipt": "CONVERGENCE_V3_REPEAT_RECEIPT.json",
    },
    "INITIAL_R2": {
        "run_id": "DCV31-C-I2-0814", "seed": 6301,
        "receipt": "CONVERGENCE_V3_NATIVE_RECEIPT_R2.json",
    },
}


def _utc() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(dict(value), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _load(path: Path) -> Mapping[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def complete(live: Mapping[str, Any]) -> bool:
    return bool(
        live.get("decision_evidence_contract_version") == "3.1"
        and frozen._complete("C", live)
    )


def dashboard_ready(live: Mapping[str, Any]) -> bool:
    dashboard = live.get("decision_window_dashboard")
    return bool(
        isinstance(dashboard, Mapping)
        and dashboard.get("method_v1_dashboard") is True
        and dashboard.get("decision_evidence_contract_version") == "3.1"
        and dashboard.get("run_id")
        and int(live.get("dashboard_native_refresh_count") or 0) > 0
    )


def _receipt(kind: str, output: Path, native: Mapping[str, Any]) -> Mapping[str, Any]:
    live_path = output / "GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json"
    live = _load(live_path) if live_path.is_file() else {}
    observer = live.get("runtime_grounding_convergence_observer", {})
    envelope = live.get("method_v1_decision_envelope", {})
    convergence = live.get("candidate_convergence_evidence")
    valid_scientific_run = bool(
        live
        and live.get("closed") is True
        and not live.get("errors")
        and native.get("status") == "PASS_NATIVE_UNIFIED_TRIAD_CAPTURE_AND_CLEANUP"
        and native.get("desktop_capture_status") == "PASS_VALIDATED_NATIVE_DESKTOP"
        and native.get("cleanup_status") == "PASS"
        and native.get("evaluator_return_code") == 0
        and dashboard_ready(live)
    )
    passed = bool(valid_scientific_run and complete(live))
    status = (
        "PASS"
        if passed
        else "SCIENTIFIC_NONCONVERGENCE_RUNTIME_EVIDENCE_NOT_ESTABLISHED"
        if valid_scientific_run
        else "ENGINEERING_NATIVE_OR_VISIBILITY_INVALID"
    )
    return {
        "schema_version": "driveclarify.minimum_sufficient_decision_evidence_v3.convergence_native.v1",
        "created_at_utc": _utc(), "contract_version": "3.1",
        "kind": kind, "run_id": CASES[kind]["run_id"], "seed": CASES[kind]["seed"],
        "split": "TRAIN", "status": status,
        "valid_scientific_run": valid_scientific_run,
        "native_runner_status": native.get("status"),
        "native_live_status": live.get("status"),
        "desktop_capture_status": native.get("desktop_capture_status"),
        "cleanup_status": native.get("cleanup_status"),
        "initial_k": live.get("raw_k"),
        "effective_k": envelope.get("effective_K") if isinstance(envelope, Mapping) else None,
        "convergence_event": convergence,
        "observer_status": observer.get("status") if isinstance(observer, Mapping) else None,
        "observer": observer,
        "old_bundle_id": live.get("convergence_old_bundle_version"),
        "old_bundle_invalidated": live.get("convergence_old_bundle_invalidated"),
        "old_shared_authority_revoked": live.get("convergence_shared_authority_revoked"),
        "old_and_fresh_bundle_distinct": live.get("convergence_old_and_fresh_bundle_distinct"),
        "fresh_bundle_id": live.get("convergence_fresh_bundle_version"),
        "fresh_replan": live.get("convergence_fresh_replan"),
        "fresh_unique_forward_count": live.get("convergence_fresh_unique_forward_count", 0),
        "hidden_convergence_forward_count": live.get("hidden_convergence_forward_count", 0),
        "dino_reacquisition_forward_count": observer.get("dino_reacquisition_forward_count", 0) if isinstance(observer, Mapping) else 0,
        "decision": envelope.get("decision_label") if isinstance(envelope, Mapping) else None,
        "decision_reason": envelope.get("decision_reason") if isinstance(envelope, Mapping) else None,
        "decision_subject": envelope.get("decision_subject") if isinstance(envelope, Mapping) else None,
        "control_source": envelope.get("control_source") if isinstance(envelope, Mapping) else None,
        "old_plan_used_after_convergence": False if passed else None,
        "normal_forwards": live.get("normal_simlingo_forward_count"),
        "candidate_forwards": live.get("candidate_simlingo_forward_count"),
        "new_pid_count": live.get("new_pid_count"),
        "new_planner_count": live.get("new_planner_count"),
        "new_writer_count": live.get("direct_vehicle_control_write_count"),
        "candidate_direct_control_write_count": live.get("candidate_direct_vehicle_control_write_count"),
        "existing_pid_invocation_count": live.get("existing_pid_invocation_count"),
        "expected_decision_reads": live.get("expected_decision_reads"),
        "gold_policy_label_reads": live.get("gold_policy_label_reads"),
        "privileged_state_policy_reads": live.get("privileged_state_policy_read_count"),
        "vehicle_collision_count": native.get("vehicle_collision_count"),
        "live_receipt": str(live_path.relative_to(ROOT)) if live_path.is_file() else None,
        "native_receipt": str((output / "GROUNDED_LANGUAGE_V1_NATIVE_RUN_RECEIPT.json").relative_to(ROOT)),
        "dev_attempt_count": 0, "test_attempt_count": 0, "test_consumed": False,
    }


def _write_timeline(kind: str, receipt: Mapping[str, Any]) -> None:
    path = REPORT / "CONVERGENCE_V3_TIMELINE.json"
    timeline = _load(path) if path.is_file() else {
        "schema_version": "driveclarify.minimum_sufficient_decision_evidence_v3.convergence_timeline.v1",
        "runs": {},
    }
    timeline["runs"][kind] = {
        key: receipt.get(key) for key in (
            "run_id", "status", "initial_k", "effective_k", "convergence_event",
            "old_shared_authority_revoked", "old_bundle_invalidated",
            "old_and_fresh_bundle_distinct", "fresh_bundle_id", "fresh_replan",
            "fresh_unique_forward_count", "hidden_convergence_forward_count",
            "decision", "decision_reason", "control_source",
        )
    }
    timeline["status"] = (
        "PASS_CONVERGENCE_V3_2_OF_2"
        if timeline["runs"].get("INITIAL_R2", {}).get("status") == "PASS"
        and timeline["runs"].get("REPEAT", {}).get("status") == "PASS"
        else "INITIAL_PASS_REPEAT_PENDING"
        if timeline["runs"].get("INITIAL_R2", {}).get("status") == "PASS"
        else "SCIENTIFIC_NONCONVERGENCE_OR_ENGINEERING_FAILURE"
    )
    _write(path, timeline)


def execute(kind: str, timeout_seconds: float) -> Mapping[str, Any]:
    manifest = frozen._manifest_preflight()
    row = CASES[kind]
    output = ARTIFACT / row["run_id"]
    if output.exists() and any(output.iterdir()):
        raise RuntimeError("V3_CONVERGENCE_OUTPUT_NOT_EMPTY:" + str(output))
    spec = backend.resolve_episode(
        fixture_id="E1R1-ACT-PHYS-003", method_id="driveclarify_grounded_v1",
        episode_id=row["run_id"],
    )
    spec = replace(spec, seed=row["seed"])
    lease_receipt = None
    try:
        with backend.serial_gpu_lease() as lease:
            lease_receipt = lease
            native = run_grounded(
                output, case="act", seed=spec.seed,
                method_id="driveclarify_grounded_v1", device="cpu", control=True,
                answer="The nearer white van.", answer_delay=0.1,
                timeout_seconds=timeout_seconds, episode_spec=spec,
                visualization=True, post_hoc_world_state=True,
                terminate_on_runtime_terminal=True, capture_desktop=True,
                environment_overrides={
                    V1_FEATURE_FLAG: "1", V3_FEATURE_FLAG: "1",
                    CONVERGENCE_OBSERVER_ENV: "1",
                    "DRIVECLARIFY_GROUNDED_LANGUAGE_V1_E1_R1": "1",
                    "SCENARIO_RUNNER_ROOT": str((ROOT / frozen.SCENARIO_ROOT).resolve()),
                    "DRIVECLARIFY_E1R1_FIXTURE_ID": "E1R1-ACT-PHYS-003",
                },
                completion_predicate=lambda live: complete(live),
                desktop_capture_predicate=lambda live: dashboard_ready(live),
                accept_natural_evaluator_completion=True,
                allow_scientific_collision_outcome=True,
            )
    finally:
        if lease_receipt is not None and output.is_dir():
            backend._write_lease(output, lease_receipt)
    receipt = _receipt(kind, output, native)
    _write(REPORT / row["receipt"], receipt)
    _write_timeline(kind, receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=tuple(CASES))
    parser.add_argument("--timeout-seconds", type=float, default=420.0)
    parser.add_argument("--preflight-import-only", action="store_true")
    args = parser.parse_args()
    if args.preflight_import_only:
        manifest = frozen._manifest_preflight()
        heavy = sorted(name for name in sys.modules if name.split(".", 1)[0] in {"carla", "torch"})
        value = {
            "status": "PASS_V3_CONVERGENCE_PREFLIGHT" if not heavy else "BLOCKED_HEAVY_IMPORT",
            "kind": args.kind, "run_id": CASES[args.kind]["run_id"],
            "seed": CASES[args.kind]["seed"], "split": "TRAIN",
            "manifest_revision": manifest["revision"], "heavy_modules": heavy,
        }
        print(json.dumps(value, indent=2, sort_keys=True))
        return 0 if not heavy else 2
    try:
        receipt = execute(args.kind, args.timeout_seconds)
        print(json.dumps(receipt, indent=2, sort_keys=True))
        return 0 if receipt.get("status") == "PASS" else 3
    except Exception as error:
        value = {
            "status": "BLOCKED_V3_CONVERGENCE_ENGINEERING_PRELAUNCH",
            "kind": args.kind, "valid_scientific_run": False,
            "error_type": type(error).__name__, "error": str(error),
            "traceback": traceback.format_exc(),
        }
        print(json.dumps(value, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
