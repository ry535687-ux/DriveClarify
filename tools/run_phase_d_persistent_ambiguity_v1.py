#!/usr/bin/env python3
"""Run one append-only bounded native Phase D persistent-lifecycle episode."""

from __future__ import annotations

import argparse
import json
import math
import sys
import traceback
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driveclarify_grounded_language_v1_extension_e1_r1 import backend  # noqa: E402
from driveclarify_persistent_ambiguity_runtime_v1.runtime import (  # noqa: E402
    FEATURE_FLAG,
)
from tools.run_grounded_language_v1_triad import run as run_grounded  # noqa: E402


STAGE_SLUG = "driveclarify_persistent_ambiguity_runtime_v1_continuous_completion"
FIXTURE_ID = "E1R1-ASK-PHYS-001"
METHOD_ID = "driveclarify_grounded_v1"
RAW_INSTRUCTION = "Turn after the white van."
BOUNDED_RUN_IDS = ("D0", "D1", "D2", "D2-R1")


def _progress_uncertainty_m(live: Mapping[str, Any]) -> float | None:
    """Read the existing calibrated route-progress uncertainty from evidence."""

    window = live.get("persistent_decision_window")
    if not isinstance(window, Mapping):
        return None
    coverage = window.get("plan_coverage_evidence")
    if not isinstance(coverage, Mapping):
        return None
    values = []
    for row in coverage.values():
        if not isinstance(row, Mapping):
            continue
        diagnostics = row.get("projection_and_validity")
        if not isinstance(diagnostics, Mapping):
            continue
        value = diagnostics.get("calibrated_transform_uncertainty_m")
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(numeric) and numeric >= 0.0:
            values.append(numeric)
    return max(values) if values else None


def _act_shared_fresh_progress_cycle(
    decision_history: list[Any], *, progress_uncertainty_m: float
) -> bool:
    """Require ACT_SHARED followed by a distinct, physically progressed cycle."""

    first_index = next(
        (
            index
            for index, row in enumerate(decision_history)
            if isinstance(row, Mapping) and row.get("decision") == "ACT_SHARED"
        ),
        None,
    )
    if first_index is None:
        return False
    first = decision_history[first_index]
    try:
        first_progress = float(first.get("current_progress_m"))
    except (TypeError, ValueError):
        return False
    if not math.isfinite(first_progress):
        return False
    for row in decision_history[first_index + 1 :]:
        if not isinstance(row, Mapping) or row.get("bundle_complete") is not True:
            continue
        if str(row.get("source_frame_id")) == str(first.get("source_frame_id")):
            continue
        if str(row.get("source_observation_id")) == str(
            first.get("source_observation_id")
        ):
            continue
        if str(row.get("bundle_id")) == str(first.get("bundle_id")):
            continue
        try:
            later_progress = float(row.get("current_progress_m"))
        except (TypeError, ValueError):
            continue
        if (
            math.isfinite(later_progress)
            and later_progress - first_progress > float(progress_uncertainty_m)
        ):
            return True
    return False


def phase_d_native_desktop_capture_ready(live: Mapping[str, Any]) -> bool:
    """Capture a current Phase D panel after real motion or natural ACT_SHARED."""

    if str(live.get("status", "")).startswith("BLOCKED_"):
        return False
    if int(live.get("effective_k") or 0) < 2:
        return False
    dashboard = live.get("decision_window_dashboard")
    if not isinstance(dashboard, Mapping):
        return False
    required_dashboard_fields = {
        "run_id",
        "planning_event_id",
        "bundle_version",
        "raw_instruction",
        "candidates",
        "ego_route_progress",
        "plan_coverage",
        "shared_corridor",
        "maneuver_onset",
        "commitment_boundary",
        "recoverability",
        "time_to_divergence",
        "latest_safe_clarification",
        "current_relation",
        "decision",
        "episode_state",
        "freshness",
        "forward_accounting",
        "authority_subject",
    }
    if not required_dashboard_fields.issubset(dashboard):
        return False
    decision_history = live.get("persistent_decision_history")
    if not isinstance(decision_history, list) or not decision_history:
        return False
    if any(
        isinstance(row, Mapping) and row.get("decision") == "ACT_SHARED"
        for row in decision_history
    ):
        return True
    uncertainty = _progress_uncertainty_m(live)
    if uncertainty is None:
        return False
    progress = []
    for row in decision_history:
        if not isinstance(row, Mapping):
            continue
        try:
            value = float(row.get("current_progress_m"))
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            progress.append(value)
    return bool(progress and max(progress) - min(progress) > uncertainty)


def phase_d_lifecycle_capture_ready(live: Mapping[str, Any]) -> bool:
    """Read-only stop condition for a naturally completed lifecycle capture."""

    if str(live.get("status", "")).startswith("BLOCKED_"):
        return False
    if live.get("persistent_ambiguity_runtime_v1") is not True:
        return False
    if int(live.get("effective_k") or 0) < 2:
        return False
    if live.get("candidate_specific_numeric_target") is not False:
        return False
    if int(live.get("visualization_extra_forward_count") or 0) != 0:
        return False
    if int(live.get("new_pid_count") or 0) != 0:
        return False
    if int(live.get("new_planner_count") or 0) != 0:
        return False
    if int(live.get("direct_vehicle_control_write_count") or 0) != 0:
        return False
    for key in (
        "gold_policy_label_reads",
        "expected_label_reads",
        "forced_decision_count",
        "privileged_state_read_count",
    ):
        if int(live.get(key) or 0) != 0:
            return False

    episode = live.get("persistent_ambiguity_episode")
    if not isinstance(episode, Mapping):
        return False
    candidates = episode.get("candidates")
    if not isinstance(candidates, list) or len(candidates) < 2:
        return False
    if not isinstance(live.get("persistent_decision_window"), Mapping):
        return False
    if not isinstance(live.get("persistent_m2b_recommendation"), Mapping):
        return False
    decision_history = live.get("persistent_decision_history")
    if not isinstance(decision_history, list) or len(decision_history) < 2:
        return False
    if len(
        {
            str(row.get("source_frame_id"))
            for row in decision_history
            if isinstance(row, Mapping)
        }
    ) < 2:
        return False

    history = episode.get("history")
    if not isinstance(history, list):
        return False
    complete = [
        row
        for row in history
        if isinstance(row, Mapping)
        and row.get("event_type") == "REFRESH_BUNDLE_COMPLETE_VALID"
    ]
    lifecycle_events = {
        row.get("event_type") for row in history if isinstance(row, Mapping)
    }
    accounting = episode.get("compute_accounting")
    if not isinstance(accounting, Mapping):
        return False
    shared_act = live.get("shared_act")
    if not isinstance(shared_act, Mapping):
        return False
    if int(shared_act.get("authority_receipts_issued") or 0) < 1:
        return False
    if int(shared_act.get("authority_receipts_consumed") or 0) < 1:
        return False
    if int(shared_act.get("shared_control_windows") or 0) < 1:
        return False
    if int(shared_act.get("existing_pid_invocation_count") or 0) < 1:
        return False
    m3_transactions = live.get("m3_act_transactions")
    if not isinstance(m3_transactions, list) or not m3_transactions:
        return False
    progress_uncertainty = _progress_uncertainty_m(live)
    if progress_uncertainty is None or not _act_shared_fresh_progress_cycle(
        decision_history, progress_uncertainty_m=progress_uncertainty
    ):
        return False

    unresolved_refresh = bool(
        episode.get("semantic_state") == "UNRESOLVED"
        and any(
            isinstance(row, Mapping) and row.get("decision") == "ACT_SHARED"
            for row in decision_history
        )
        and isinstance(decision_history[-1], Mapping)
        and decision_history[-1].get("decision") == "ACT_SHARED"
        and float(decision_history[-1].get("latest_safe_slack_s") or 0.0) > 0.0
        and len(complete) >= 2
        and len({str(row.get("source_frame_id")) for row in complete}) >= 2
        and int(accounting.get("normal_planning_event_count") or 0) >= 2
        and int(accounting.get("normal_forward_count") or 0) >= 2
        and int(accounting.get("candidate_forward_count") or 0) >= 4
        and "DECISION_ACT_SHARED" in lifecycle_events
        and "SHARED_WINDOW_CONSUMED" in lifecycle_events
    )
    resolved_answer = bool(
        episode.get("semantic_state") == "RESOLVED"
        and live.get("persistent_old_bundle_invalidated_before_replan") is True
        and isinstance(live.get("fresh_replan"), Mapping)
        and live.get("post_answer_decision") == "ACT"
        and "ANSWER_ARRIVED_VALID" in lifecycle_events
        and "LATEST_REPLAN_UNIQUE" in lifecycle_events
    )
    return unresolved_refresh or resolved_answer


def output_directory(run_id: str) -> Path:
    if run_id not in set(BOUNDED_RUN_IDS):
        raise ValueError("PHASE_D_RUN_ID_NOT_BOUNDED_D0_D2")
    return ROOT / "artifacts" / STAGE_SLUG / "Phase_D" / run_id


def import_preflight_receipt(run_id: str) -> dict[str, Any]:
    heavy = sorted(
        name
        for name in sys.modules
        if name.split(".", 1)[0] in {"carla", "torch", "transformers"}
    )
    return {
        "schema_version": "driveclarify.phase_d.import_preflight.v1",
        "status": "PASS_PHASE_D_RUNNER_IMPORT_ONLY" if not heavy else "BLOCKED_HEAVY_IMPORT",
        "run_id": run_id,
        "fixture_id": FIXTURE_ID,
        "raw_instruction": RAW_INSTRUCTION,
        "repository_root_on_sys_path": str(ROOT) in sys.path,
        "heavy_runtime_modules_loaded": heavy,
        "carla_process_launch_count": 0,
        "model_initialization_count": 0,
        "checkpoint_load_count": 0,
        "dino_initialization_count": 0,
        "vehicle_control_write_count": 0,
        "artifact_write_count": 0,
    }


def execute(run_id: str, timeout_seconds: float) -> Mapping[str, Any]:
    output = output_directory(run_id)
    if output.exists() and any(output.iterdir()):
        raise RuntimeError("PHASE_D_ARTIFACT_DIRECTORY_NOT_EMPTY:" + str(output))
    spec = backend.resolve_episode(
        fixture_id=FIXTURE_ID,
        method_id=METHOD_ID,
        episode_id="DC-PERSISTENT-AMBIGUITY-PHASE-D-{}-20260813".format(run_id),
    )
    if spec.raw_instruction != RAW_INSTRUCTION:
        raise RuntimeError("PHASE_D_RAW_INSTRUCTION_MISMATCH")
    fixture = backend.physical_fixture(FIXTURE_ID)
    lease_receipt = None
    try:
        with backend.serial_gpu_lease() as lease:
            lease_receipt = lease
            return run_grounded(
                output,
                case="ask",
                seed=spec.seed,
                method_id=METHOD_ID,
                device="cpu",
                control=True,
                answer="The nearer white van.",
                answer_delay=0.1,
                timeout_seconds=float(timeout_seconds),
                episode_spec=spec,
                visualization=True,
                post_hoc_world_state=True,
                terminate_on_runtime_terminal=True,
                capture_desktop=True,
                environment_overrides={
                    FEATURE_FLAG: "1",
                    "DRIVECLARIFY_GROUNDED_LANGUAGE_V1_E1_R1": "1",
                    "SCENARIO_RUNNER_ROOT": str(
                        (ROOT / backend.SCENARIO_ROOT).resolve()
                    ),
                    "DRIVECLARIFY_E1R1_FIXTURE_ID": FIXTURE_ID,
                    "DRIVECLARIFY_E1R1_WAIT_ACTOR_MOTION_SOURCE": str(
                        fixture["motion_source"]
                    ),
                },
                completion_predicate=phase_d_lifecycle_capture_ready,
                desktop_capture_predicate=phase_d_native_desktop_capture_ready,
                accept_natural_evaluator_completion=True,
                allow_scientific_collision_outcome=True,
            )
    finally:
        if lease_receipt is not None and output.is_dir():
            backend._write_lease(output, lease_receipt)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run one bounded native Phase D persistent ambiguity lifecycle."
    )
    parser.add_argument("run_id", choices=BOUNDED_RUN_IDS)
    parser.add_argument("--timeout-seconds", type=float, default=360.0)
    parser.add_argument("--preflight-import-only", action="store_true")
    args = parser.parse_args()
    if args.preflight_import_only:
        receipt = import_preflight_receipt(args.run_id)
        print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if receipt["status"].startswith("PASS_") else 2
    try:
        result = execute(args.run_id, args.timeout_seconds)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if str(result.get("status", "")).startswith("PASS_") else 2
    except Exception as error:
        failure = {
            "schema_version": "driveclarify.phase_d.execution_failure.v1",
            "status": "BLOCKED_PHASE_D_ENGINEERING_EXECUTION_FAILURE",
            "run_id": args.run_id,
            "error_type": type(error).__name__,
            "error": str(error),
            "traceback": traceback.format_exc(),
            "valid_scientific_run": False,
        }
        print(json.dumps(failure, ensure_ascii=False, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
