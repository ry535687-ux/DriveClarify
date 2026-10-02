#!/usr/bin/env python3
"""Run one frozen Method V1 targeted native TRAIN case."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import sys
import traceback
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driveclarify_grounded_language_v1_extension_e1_r1 import backend  # noqa: E402
from driveclarify_grounded_language_v1_extension_e1_r1.contracts import (  # noqa: E402
    SCENARIO_ROOT,
)
from driveclarify_persistent_ambiguity_runtime_v1.convergence_observer import (  # noqa: E402
    CONVERGENCE_OBSERVER_ENV,
)
from driveclarify_persistent_ambiguity_runtime_v1.runtime import (  # noqa: E402
    DECISION_EVIDENCE_V2_FEATURE_FLAG,
    FEATURE_FLAG,
)
from tools.run_grounded_language_v1_triad import run as run_grounded  # noqa: E402


REPORT = ROOT / "reports/driveclarify_method_v1_act_semantics_targeted_native_validation"
ARTIFACT = ROOT / "artifacts/driveclarify_method_v1_act_semantics_targeted_native_validation"
MANIFEST = REPORT / "CONVERGENCE_NATIVE_SCENARIO_MANIFEST.json"

CASES = {
    "A": {
        "fixture_id": "E1R1-ACT-PHYS-001",
        "initial_seed": 6101,
        "repeat_seed": 6102,
        "receipt": "K1_ACT_NATIVE_RECEIPT.json",
        "repeat_receipt": "K1_ACT_REPEAT_RECEIPT.json",
        "screenshot": "K1_ACT_NATIVE_VISIBLE.png",
    },
    "B": {
        "fixture_id": "E1R1-ACT-PHYS-003",
        "initial_seed": 6201,
        "repeat_seed": 6202,
        "receipt": "K2_EQUIVALENT_ACT_SHARED_NATIVE_RECEIPT.json",
        "repeat_receipt": "K2_EQUIVALENT_ACT_SHARED_REPEAT_RECEIPT.json",
        "screenshot": "K2_EQUIVALENT_ACT_SHARED_NATIVE_VISIBLE.png",
    },
    "C": {
        "fixture_id": "E1R1-ACT-PHYS-003",
        "initial_seed": 6301,
        "repeat_seed": 6302,
        "receipt": "K2_TO_K1_CONVERGENCE_ACT_NATIVE_RECEIPT.json",
        "repeat_receipt": "K2_TO_K1_CONVERGENCE_ACT_REPEAT_RECEIPT.json",
        "screenshot": "K2_TO_K1_CONVERGENCE_ACT_NATIVE_VISIBLE.png",
    },
}


def _utc() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(str(temporary), str(path))


def _load(path: Path) -> Mapping[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _manifest_preflight() -> Mapping[str, Any]:
    value = _load(MANIFEST)
    if value.get("revision") != 0 or value.get("frozen_before_first_outcome") is not True:
        raise RuntimeError("CONVERGENCE_MANIFEST_NOT_PREFROZEN_REVISION_ZERO")
    for relative, expected in value["protected_fixture_hashes"].items():
        path = ROOT / relative
        observed = hashlib.sha256(path.read_bytes()).hexdigest()
        if observed != expected:
            raise RuntimeError("TARGETED_FIXTURE_HASH_MISMATCH:" + relative)
    forbidden = json.dumps(value, sort_keys=True).casefold()
    for token in ("expected_surviving_candidate", "gold_candidate", "expected_decision_label"):
        if token in forbidden:
            raise RuntimeError("CONVERGENCE_MANIFEST_LABEL_LEAKAGE:" + token)
    return value


def _envelope(live: Mapping[str, Any]) -> Mapping[str, Any]:
    value = live.get("method_v1_decision_envelope")
    return value if isinstance(value, Mapping) else {}


def _control_rows(live: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    value = live.get("control_source_receipts")
    return [row for row in value if isinstance(row, Mapping)] if isinstance(value, list) else []


def _complete(case: str, live: Mapping[str, Any]) -> bool:
    envelope = _envelope(live)
    control = _control_rows(live)
    dashboard = live.get("decision_window_dashboard", {})
    visible_identity = bool(
        isinstance(dashboard, Mapping)
        and dashboard.get("method_v1_dashboard") is True
        and dashboard.get("run_id")
        and len(str(dashboard.get("run_id"))) <= 20
    )
    if case == "A":
        return bool(
            visible_identity
            and
            envelope.get("decision_label") == "ACT"
            and envelope.get("decision_reason") == "NO_AMBIGUITY"
            and envelope.get("effective_K") == 1
            and live.get("candidate_forward_count_for_initial_k1") == 0
            and live.get("initial_k1_plan_object_preserved") is True
            and any(
                row.get("decision_label") == "ACT"
                and row.get("same_plan_object") is True
                for row in control
            )
            and int(live.get("existing_pid_invocation_count") or 0) >= 1
        )
    if case == "B":
        episode = live.get("persistent_ambiguity_episode", {})
        return bool(
            visible_identity
            and
            envelope.get("decision_label") == "ACT_SHARED"
            and envelope.get("relationship") == "CURRENT_AND_FUTURE_EQUIVALENT"
            and envelope.get("decision_subject") == "SHARED_EQUIVALENCE_CLASS"
            and episode.get("semantic_state") == "UNRESOLVED"
            and live.get("shared_act", {}).get("window_consumed") is True
            and any(row.get("decision_label") == "ACT_SHARED" for row in control)
            and int(live.get("existing_pid_invocation_count") or 0) >= 1
        )
    return bool(
        visible_identity
        and
        envelope.get("decision_label") == "ACT"
        and envelope.get("decision_reason") == "ACT_AFTER_CONVERGENCE"
        and envelope.get("effective_K") == 1
        and live.get("convergence_old_bundle_invalidated") is True
        and live.get("convergence_shared_authority_revoked") is True
        and live.get("convergence_old_and_fresh_bundle_distinct") is True
        and live.get("convergence_fresh_unique_forward_count") == 1
        and live.get("hidden_convergence_forward_count") == 0
        and any(
            row.get("decision_label") == "ACT"
            and row.get("control_source") == "FRESH_UNIQUE_CANDIDATE"
            for row in control
        )
        and int(live.get("existing_pid_invocation_count") or 0) >= 1
    )


def _receipt(case: str, repeat: bool, output: Path, native_result: Mapping[str, Any]) -> Mapping[str, Any]:
    live_path = output / "GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json"
    live = _load(live_path) if live_path.is_file() else {}
    envelope = _envelope(live)
    complete = _complete(case, live)
    observer = live.get("runtime_grounding_convergence_observer", {})
    dashboard = live.get("decision_window_dashboard", {})
    native_visible_run_id = bool(
        native_result.get("desktop_capture_status") == "PASS_VALIDATED_NATIVE_DESKTOP"
        and isinstance(dashboard, Mapping)
        and dashboard.get("method_v1_dashboard") is True
        and dashboard.get("run_id")
        and len(str(dashboard.get("run_id"))) <= 20
    )
    result = {
        "schema_version": "driveclarify.method_v1_targeted_native_case_receipt.v1",
        "created_at_utc": _utc(),
        "case": case,
        "repeat": bool(repeat),
        "split": "TRAIN",
        "status": (
            "PASS"
            if complete
            else "ENGINEERING_NATIVE_PRELAUNCH_OR_ENGINE_FAILURE"
            if not live
            else "ENGINEERING_NATIVE_VISIBILITY_EVIDENCE_INVALID"
            if live and not native_visible_run_id
            else "SCIENTIFIC_OUTCOME_NOT_ACCEPTED"
        ),
        "valid_scientific_run": bool(live and native_visible_run_id),
        "native_visible_run_id": native_visible_run_id,
        "native_runner_status": native_result.get("status"),
        "live_status": live.get("status"),
        "run_id": live.get("decision_window_dashboard", {}).get("run_id"),
        "raw_k": live.get("raw_k"),
        "effective_k": envelope.get("effective_K"),
        "decision_label": envelope.get("decision_label"),
        "decision_reason": envelope.get("decision_reason"),
        "relationship": envelope.get("relationship"),
        "decision_subject": envelope.get("decision_subject"),
        "ambiguity_state": envelope.get("ambiguity_state"),
        "control_source": envelope.get("control_source"),
        "authority_subject": envelope.get("authority_subject"),
        "source_planning_event": envelope.get("source_planning_event"),
        "candidate_bundle_version": envelope.get("candidate_bundle_version"),
        "candidate_forward_count": live.get("candidate_simlingo_forward_count"),
        "fresh_unique_forward_count": live.get("convergence_fresh_unique_forward_count", 0),
        "hidden_convergence_forward_count": live.get("hidden_convergence_forward_count", 0),
        "dino_reacquisition_forward_count": observer.get("dino_reacquisition_forward_count", 0),
        "original_plan_identity_preserved": live.get("initial_k1_plan_object_preserved"),
        "old_bundle_id": live.get("convergence_old_bundle_version"),
        "old_bundle_invalidated": live.get("convergence_old_bundle_invalidated"),
        "old_authority_revoked": live.get("convergence_shared_authority_revoked"),
        "fresh_bundle_id": live.get("convergence_fresh_bundle_version"),
        "fresh_replan": live.get("convergence_fresh_replan"),
        "convergence_evidence": live.get("candidate_convergence_evidence"),
        "convergence_observer": observer,
        "old_plan_used_after_convergence": False if case == "C" and complete else None,
        "new_pid_count": live.get("new_pid_count"),
        "new_planner_count": live.get("new_planner_count"),
        "new_writer_count": live.get("direct_vehicle_control_write_count"),
        "existing_pid_invocation_count": live.get("existing_pid_invocation_count"),
        "vehicle_collision_count": native_result.get("vehicle_collision_count"),
        "desktop_capture_status": native_result.get("desktop_capture_status"),
        "live_receipt_path": str(live_path.relative_to(ROOT)) if live_path.is_file() else None,
        "native_receipt_path": str((output / "GROUNDED_LANGUAGE_V1_NATIVE_RUN_RECEIPT.json").relative_to(ROOT)),
        "dev_attempt_count": 0,
        "test_attempt_count": 0,
        "test_consumed": False,
    }
    return result


def execute(
    case: str, repeat: bool, timeout_seconds: float, engineering_retry: int = 0
) -> Mapping[str, Any]:
    _manifest_preflight()
    row = CASES[case]
    suffix = "REPEAT" if repeat else "INITIAL"
    retry_suffix = "-E{}".format(engineering_retry) if engineering_retry else ""
    run_id = "DCMV1-{}-{}{}-0814".format(
        case, "R" if repeat else "I", retry_suffix
    )
    output = ARTIFACT / run_id
    if output.exists() and any(output.iterdir()):
        raise RuntimeError("TARGETED_NATIVE_OUTPUT_NOT_EMPTY:" + str(output))
    spec = backend.resolve_episode(
        fixture_id=row["fixture_id"],
        method_id="driveclarify_grounded_v1",
        episode_id=run_id,
    )
    spec = replace(spec, seed=row["repeat_seed"] if repeat else row["initial_seed"])
    overrides = {
        FEATURE_FLAG: "1",
        DECISION_EVIDENCE_V2_FEATURE_FLAG: "1",
        "DRIVECLARIFY_GROUNDED_LANGUAGE_V1_E1_R1": "1",
        "SCENARIO_RUNNER_ROOT": str((ROOT / SCENARIO_ROOT).resolve()),
        "DRIVECLARIFY_E1R1_FIXTURE_ID": row["fixture_id"],
    }
    if case == "C":
        overrides[CONVERGENCE_OBSERVER_ENV] = "1"
    lease_receipt = None
    try:
        with backend.serial_gpu_lease() as lease:
            lease_receipt = lease
            native_result = run_grounded(
                output,
                case="act",
                seed=spec.seed,
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
                completion_predicate=lambda live: _complete(case, live),
                desktop_capture_predicate=lambda live: _complete(case, live),
                accept_natural_evaluator_completion=True,
                allow_scientific_collision_outcome=True,
            )
    finally:
        if lease_receipt is not None and output.is_dir():
            backend._write_lease(output, lease_receipt)
    receipt = _receipt(case, repeat, output, native_result)
    report_name = row["repeat_receipt"] if repeat else row["receipt"]
    report_path = REPORT / report_name
    if engineering_retry and report_path.is_file():
        archive = REPORT / (
            report_path.stem
            + "_ENGINEERING_INVALID_ATTEMPT_{}".format(engineering_retry)
            + report_path.suffix
        )
        if archive.exists():
            raise RuntimeError("ENGINEERING_RETRY_RECEIPT_ARCHIVE_EXISTS:" + str(archive))
        os.replace(str(report_path), str(archive))
    _write(report_path, receipt)
    if not repeat and receipt["status"] == "PASS":
        screenshot = output / "NATIVE_DESKTOP.png"
        if screenshot.is_file():
            screenshot_target = REPORT / row["screenshot"]
            if engineering_retry and screenshot_target.is_file():
                screenshot_archive = REPORT / (
                    screenshot_target.stem
                    + "_ENGINEERING_INVALID_ATTEMPT_{}".format(engineering_retry)
                    + screenshot_target.suffix
                )
                if screenshot_archive.exists():
                    raise RuntimeError(
                        "ENGINEERING_RETRY_SCREENSHOT_ARCHIVE_EXISTS:"
                        + str(screenshot_archive)
                    )
                os.replace(str(screenshot_target), str(screenshot_archive))
            shutil.copy2(screenshot, screenshot_target)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("case", choices=tuple(CASES))
    parser.add_argument("--repeat", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=420.0)
    parser.add_argument("--engineering-retry", type=int, default=0)
    parser.add_argument("--preflight-import-only", action="store_true")
    args = parser.parse_args()
    if args.preflight_import_only:
        manifest = _manifest_preflight()
        heavy = sorted(
            name for name in sys.modules if name.split(".", 1)[0] in {"carla", "torch"}
        )
        value = {
            "status": "PASS_IMPORT_AND_FROZEN_MANIFEST" if not heavy else "BLOCKED_HEAVY_IMPORT",
            "case": args.case,
            "split": "TRAIN",
            "manifest_revision": manifest["revision"],
            "heavy_modules": heavy,
        }
        print(json.dumps(value, indent=2, sort_keys=True))
        return 0 if not heavy else 2
    try:
        receipt = execute(
            args.case,
            args.repeat,
            args.timeout_seconds,
            engineering_retry=args.engineering_retry,
        )
        print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if receipt["status"] == "PASS" else 3
    except Exception as error:
        value = {
            "status": "BLOCKED_TARGETED_NATIVE_ENGINEERING_PRELAUNCH",
            "case": args.case,
            "repeat": args.repeat,
            "valid_scientific_run": False,
            "error_type": type(error).__name__,
            "error": str(error),
            "traceback": traceback.format_exc(),
        }
        print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
