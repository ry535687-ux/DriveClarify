"""Evidence-only auditor for the Stage 3 closed-loop clarification demo.

The auditor composes artifacts emitted by the existing ASK/replan, Physical
WAIT, candidate authority, and limited ACT integrations.  It has no runtime
control API and cannot invoke a model, planner, PID, or CARLA.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


PASS_STATUS = (
    "PASS_DRIVECLARIFY_CLOSED_LOOP_PROTOTYPE_V1_READY_FOR_PAPER_MVP_EXPERIMENTS"
)
BLOCKED_ASK_WAIT_ACT_CHAIN = "BLOCKED_ASK_WAIT_ACT_CHAIN_FAILED"
BLOCKED_FRESH_REPLAN = "BLOCKED_FRESH_REPLAN_NOT_VERIFIED"
BLOCKED_OLD_CANDIDATE = "BLOCKED_OLD_CANDIDATE_INVALIDATION_FAILED"
BLOCKED_WAIT_OWNERSHIP = "BLOCKED_WAIT_CONTROL_OWNERSHIP_FAILED"
BLOCKED_SINGLE_PID = "BLOCKED_SINGLE_PID_INVARIANT_FAILED"
BLOCKED_CARLA = "BLOCKED_CARLA_RUNTIME_FAILED"
BLOCKED_VISUALIZATION = "BLOCKED_CARLA_NATIVE_VISUALIZATION_UNAVAILABLE"

REQUIRED_SCREENSHOT_STAGES = (
    "ASK_SENT_WAIT_ACTIVE",
    "LATEST_OBSERVATION_CAPTURED",
    "REPLANNING_FROM_LATEST_OBSERVATION",
    "REPLAN_COMPLETE_RESUME_READY",
    "CANDIDATE_AUTHORITY_GRANTED",
    "ACT_CONTROL_TICK_ACTIVE",
    "ACT_CARLA_NEXT_FRAME_OBSERVED",
    "CONTROL_RETURNED_TO_BASELINE",
)


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"JSON_OBJECT_REQUIRED:{path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _check(actual: Any, expected: Any = True) -> dict[str, Any]:
    return {"passed": actual == expected, "actual": actual, "expected": expected}


def _all_checks(checks: Mapping[str, Mapping[str, Any]]) -> bool:
    return bool(checks) and all(bool(value.get("passed")) for value in checks.values())


def _as_int(value: Any, default: int = -1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _owner_rows(act: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    sources = act.get("pre_pid_plan_source_by_frame", {})
    if not isinstance(sources, Mapping):
        return rows
    for frame, source in sorted(sources.items(), key=lambda item: _as_int(item[0])):
        rows.append(
            {
                "frame": _as_int(frame),
                "owner": (
                    "DRIVECLARIFY_CANDIDATE_CONTROL"
                    if source == "CANDIDATE"
                    else "BASELINE_CONTROL"
                    if source == "BASELINE"
                    else str(source)
                ),
                "candidate_selected": source == "CANDIDATE",
                "baseline_selected": source == "BASELINE",
            }
        )
    return rows


def _screenshot_status(
    live: Mapping[str, Any],
    screenshot_files: Mapping[str, Path] | None,
) -> dict[str, Any]:
    visualization = live.get("visualization", {})
    if not isinstance(visualization, Mapping):
        visualization = {}
    configured = visualization.get("ask_lifecycle_stage_screenshots", {})
    if not isinstance(configured, Mapping):
        configured = {}
    rows: list[dict[str, Any]] = []
    for stage in REQUIRED_SCREENSHOT_STAGES:
        path = None
        if screenshot_files is not None:
            path = screenshot_files.get(stage)
        if path is None and configured.get(stage):
            path = Path(str(configured[stage]))
        exists = path is not None and path.is_file()
        size = path.stat().st_size if exists else 0
        png_signature = False
        if exists:
            with path.open("rb") as handle:
                png_signature = handle.read(8) == b"\x89PNG\r\n\x1a\n"
        rows.append(
            {
                "stage": stage,
                "path": None if path is None else str(path),
                "exists": exists,
                "bytes": size,
                "png_signature_valid": png_signature,
                "valid": bool(exists and size > 0 and png_signature),
            }
        )
    front = visualization.get("front_camera", {})
    if not isinstance(front, Mapping):
        front = {}
    return {
        "native_window_open": visualization.get("open_window") is True,
        "main_view_source": visualization.get("main_view_source"),
        "front_camera_available": front.get("status") == "AVAILABLE",
        "front_frame_identity_verified": front.get("frame_identity_matches_baseline") is True,
        "model_input_unchanged": front.get("model_input_unchanged") is True,
        "required_stage_screenshots": rows,
        "all_required_screenshots_valid": all(row["valid"] for row in rows),
    }


def audit_closed_loop_documents(
    live: Mapping[str, Any],
    ask: Mapping[str, Any],
    act: Mapping[str, Any],
    equivalence: Mapping[str, Any],
    *,
    screenshot_files: Mapping[str, Path] | None = None,
) -> dict[str, Any]:
    """Audit one composed lifecycle and return a precise terminal status."""

    instruction = live.get("instruction", {})
    candidates = live.get("candidates", [])
    candidate_runtime = live.get("candidate_runtime", {})
    matrix = live.get("counterfactual_matrix", {})
    m2b = live.get("m2b", {})
    wait = live.get("physical_wait_v0", {})
    query = ask.get("query", {})
    oracle = ask.get("oracle", {})
    invalidation = ask.get("old_candidate_invalidation", {})
    latest = ask.get("latest_observation", {})
    replan = ask.get("replan", {})
    ask_invariants = ask.get("invariants", {})
    m3 = ask.get("m3_lifecycle", {})
    receipt = act.get("receipt", {})
    old_rejection = act.get("old_candidate_rejection", {})
    owner_rows = _owner_rows(act)
    visualization = _screenshot_status(live, screenshot_files)

    interpretations = []
    if isinstance(instruction, Mapping):
        interpretations = [
            value
            for value in (
                instruction.get("interpretation_a"),
                instruction.get("interpretation_b"),
            )
            if isinstance(value, str) and value.strip()
        ]
    semantic_candidate_count = (
        candidate_runtime.get("semantic_candidate_count")
        if isinstance(candidate_runtime, Mapping)
        else None
    )
    initial_checks = {
        "ambiguous_instruction_present": _check(
            bool(isinstance(instruction, Mapping) and instruction.get("raw"))
        ),
        "at_least_two_interpretations": _check(len(interpretations) >= 2),
        "at_least_two_semantic_candidates": _check(
            isinstance(candidates, Sequence)
            and not isinstance(candidates, (str, bytes))
            and len(candidates) >= 2
            and _as_int(semantic_candidate_count, 0) >= 2
        ),
        "shared_vla_candidate_forwards": _check(
            _as_int(candidate_runtime.get("candidate_model_forward_count"), 0) >= 2
            if isinstance(candidate_runtime, Mapping)
            else False
        ),
        "consequence_matrix_complete": _check(
            isinstance(matrix, Mapping)
            and matrix.get("matrix_status") == "COMPLETE_KNOWN"
            and len(matrix.get("cells", [])) >= 4
        ),
        "m2b_decision_present": _check(
            isinstance(m2b, Mapping) and bool(m2b.get("producer_action"))
        ),
    }

    ask_checks = {
        "one_query_identity": _check(
            isinstance(query, Mapping)
            and bool(query.get("query_id"))
            and bool(query.get("question"))
        ),
        "single_active_query_invariant": _check(
            ask_invariants.get("single_active_query") is True
        ),
        "no_second_ask": _check(
            query.get("second_query_created_count") == 0
            and query.get("second_query_attempt_count") == 0
        ),
        "valid_query_lifecycle": _check(
            ask.get("timeline")
            == [
                "AMBIGUITY",
                "ASK",
                "WAIT",
                "ANSWER",
                "INVALIDATE",
                "LATEST_FRAME",
                "REPLAN",
                "READY",
            ]
        ),
        "answer_resolved_same_query": _check(
            oracle.get("answer_status") == "RESOLVED"
            and oracle.get("selected_interpretation_id")
            == replan.get("resolved_interpretation_id")
        ),
        "m3_resume_ready": _check(
            m3.get("exact_state") == "RESUME_READY"
            and m3.get("authority") == "BASELINE_CONTROL"
            and m3.get("query_active") is False
        ),
    }

    wait_controls = wait.get("baseline_controls", []) if isinstance(wait, Mapping) else []
    wait_checks = {
        "physical_wait_exited_on_answer": _check(
            wait.get("status") == "EXITED" and wait.get("exit_reason") == "M3_LEFT_WAIT"
        ),
        "world_and_frames_continued": _check(
            wait.get("environment_continued") is True
            and _as_int(wait.get("carla_ticks_during_wait"), 0) >= 1
            and _as_int(wait.get("frames_observed_during_wait"), 0) >= 2
            and len(wait.get("frame_ids_observed", [])) >= 2
        ),
        "baseline_only_during_wait": _check(
            bool(wait_controls)
            and all(
                row.get("control_owner") == "BASELINE_SIMLINGO_CURRENT_VALID_PLAN"
                and row.get("exact_equal_at_return_boundary") is True
                and row.get("object_identity_preserved") is True
                for row in wait_controls
                if isinstance(row, Mapping)
            )
        ),
        "no_candidate_act_during_wait": _check(
            wait.get("candidate_commit_count_during_wait") == 0
            and wait.get("driveclarify_low_level_control_writes") == 0
            and wait.get("invariants", {}).get("no_candidate_route_used_for_control") is True
        ),
    }

    invalidation_checks = {
        "old_candidate_invalidated": _check(
            invalidation.get("old_candidate_authorized") is False
            and invalidation.get("old_candidate_status") == "STALE_NOT_AUTHORIZED_FOR_ACT"
        ),
        "old_candidate_receipt_zero": _check(
            old_rejection.get("receipt_issued") is False
            and old_rejection.get("status") == "UNAUTHORIZED_INVALIDATED"
        ),
        "old_candidate_cannot_execute": _check(
            invalidation.get("post_answer_old_candidate_commit_count") == 0
            and old_rejection.get("plan_selected") is False
            and old_rejection.get("pid_invocations") == 0
        ),
        "old_and_stale_writes_zero": _check(
            invalidation.get("post_answer_old_candidate_control_writes") == 0
            and act.get("old_candidate_control_writes") == 0
            and act.get("stale_candidate_control_writes") == 0
        ),
    }

    old_frame = _as_int(latest.get("old_frame_id"))
    new_frame = _as_int(latest.get("new_frame_id"))
    answer_time = oracle.get("answer_received_monotonic")
    invalidated_time = invalidation.get("old_candidate_invalidated_at")
    replan_started = replan.get("replan_started_monotonic")
    replan_checks = {
        "answer_before_or_at_invalidation": _check(
            isinstance(answer_time, (int, float))
            and isinstance(invalidated_time, (int, float))
            and answer_time <= invalidated_time
        ),
        "invalidation_before_latest_observation": _check(
            invalidation.get("invalidated_before_latest_observation") is True
            and new_frame > old_frame
        ),
        "latest_observation_before_new_inference": _check(
            invalidation.get("invalidated_before_replan") is True
            and isinstance(replan_started, (int, float))
            and isinstance(invalidated_time, (int, float))
            and invalidated_time <= replan_started
        ),
        "fresh_model_computation": _check(
            replan.get("fresh_model_computation") is True
            and _as_int(replan.get("post_answer_model_forward_count"), 0) >= 1
        ),
        "fresh_plan_bound_to_new_observation": _check(
            bool(replan.get("plan_id"))
            and replan.get("plan_id") != invalidation.get("old_candidate_set_id")
            and str(replan.get("source_frame_id")) == str(latest.get("new_frame_id"))
            and replan.get("source_observation_id") == latest.get("new_observation_id")
        ),
    }

    candidate_rows = [row for row in owner_rows if row["candidate_selected"]]
    allowed_owners = {"BASELINE_CONTROL", "DRIVECLARIFY_CANDIDATE_CONTROL"}
    ownership_checks = {
        "one_owner_per_tick": _check(
            bool(owner_rows)
            and all(
                row["owner"] in allowed_owners
                and row["candidate_selected"] != row["baseline_selected"]
                for row in owner_rows
            )
        ),
        "exactly_one_candidate_tick": _check(len(candidate_rows) == 1),
        "no_unexpected_owner": _check(act.get("unexpected_control_owners") == 0),
        "baseline_return_after_candidate": _check(
            act.get("ownership_returned_to_baseline") is True
            and bool(candidate_rows)
            and any(
                row["frame"] > candidate_rows[0]["frame"]
                and row["owner"] == "BASELINE_CONTROL"
                for row in owner_rows
            )
        ),
    }

    act_checks = {
        "one_receipt_issued": _check(act.get("authority_receipts_issued"), 1),
        "one_receipt_consumed": _check(act.get("authority_receipts_consumed"), 1),
        "receipt_one_tick_bound": _check(
            receipt.get("issued") is True
            and receipt.get("consumed") is True
            and receipt.get("max_control_ticks") == 1
        ),
        "one_control_window": _check(act.get("candidate_control_window_count"), 1),
        "one_candidate_tick": _check(act.get("candidate_control_tick_count"), 1),
        "one_existing_pid_invocation": _check(act.get("pid_invocations_on_act_tick"), 1),
        "one_vehicle_control_submission": _check(act.get("candidate_control_submissions"), 1),
        "one_candidate_control_write": _check(act.get("candidate_control_writes"), 1),
    }

    carla_checks = {
        "carla_frame_progressed": _check(
            act.get("world_frame_progressed") is True
            and _as_int(act.get("next_carla_frame")) > _as_int(act.get("act_entry_frame"))
        ),
        "submitted_control_observed": _check(
            act.get("actual_control_exact_match") is True
            and act.get("actual_control_actuator_match") is True
        ),
        "probe_single_forward_and_pid_per_tick": _check(
            bool(equivalence.get("forward_call_count_per_tick"))
            and bool(equivalence.get("pid_call_count_per_tick"))
            and all(value == 1 for value in equivalence.get("forward_call_count_per_tick", []))
            and all(value == 1 for value in equivalence.get("pid_call_count_per_tick", []))
            and equivalence.get("probe_induced_model_calls") == 0
            and equivalence.get("probe_induced_pid_calls") == 0
            and equivalence.get("probe_induced_route_planner_steps") == 0
        ),
    }

    visualization_checks = {
        "native_visualizer_window": _check(visualization["native_window_open"]),
        "real_rgb_0_source": _check(
            visualization["main_view_source"] == "REAL_SIMLINGO_INPUT_DATA_RGB_0"
            and visualization["front_camera_available"]
            and visualization["front_frame_identity_verified"]
            and visualization["model_input_unchanged"]
        ),
        "all_lifecycle_screenshots": _check(
            visualization["all_required_screenshots_valid"]
        ),
    }

    groups = {
        "initial_clarification": initial_checks,
        "ask": ask_checks,
        "wait": wait_checks,
        "old_candidate_invalidation": invalidation_checks,
        "fresh_replanning": replan_checks,
        "ownership": ownership_checks,
        "bounded_act": act_checks,
        "carla": carla_checks,
        "native_visualization": visualization_checks,
    }
    failed_groups = [name for name, checks in groups.items() if not _all_checks(checks)]

    if "native_visualization" in failed_groups:
        status = BLOCKED_VISUALIZATION
    elif "wait" in failed_groups:
        status = BLOCKED_WAIT_OWNERSHIP
    elif "old_candidate_invalidation" in failed_groups:
        status = BLOCKED_OLD_CANDIDATE
    elif "fresh_replanning" in failed_groups:
        status = BLOCKED_FRESH_REPLAN
    elif "bounded_act" in failed_groups or "ownership" in failed_groups:
        status = BLOCKED_SINGLE_PID
    elif "carla" in failed_groups:
        status = BLOCKED_CARLA
    elif failed_groups:
        status = BLOCKED_ASK_WAIT_ACT_CHAIN
    else:
        status = PASS_STATUS

    candidate_identity = act.get("candidate_identity", {})
    return {
        "schema_version": "driveclarify.end_to_end_closed_loop_demo.live_invariant_audit.v1",
        "run_id": live.get("run_id"),
        "status": status,
        "passed": status == PASS_STATUS,
        "failed_groups": failed_groups,
        "lifecycle": [
            "AMBIGUITY",
            "CANDIDATES",
            "CONSEQUENCES",
            "M2B_DECISION",
            "ASK",
            "PHYSICAL_WAIT",
            "DELAYED_ANSWER",
            "INVALIDATE_OLD",
            "LATEST_OBSERVATION",
            "FRESH_SIMLINGO_REPLAN",
            "CANDIDATE_AUTHORITY",
            "ONE_EXISTING_PID",
            "ONE_VEHICLE_CONTROL",
            "CARLA_RESPONSE",
            "BASELINE_RETURN",
        ],
        "checks": groups,
        "evidence": {
            "instruction": dict(instruction) if isinstance(instruction, Mapping) else {},
            "candidate_ids": [
                row.get("candidate_id")
                for row in candidates
                if isinstance(row, Mapping)
            ],
            "candidate_runtime_schedule": candidate_runtime.get("actual_schedule"),
            "m2b_decision": m2b.get("producer_action"),
            "m3_transitions": m3.get("transition_ids"),
            "query_id": query.get("query_id"),
            "ask_timestamp_monotonic": wait.get("wait_entry_monotonic_time"),
            "wait_duration_s": wait.get("actual_wait_duration_s"),
            "wait_frames": wait.get("frame_ids_observed"),
            "answer_timestamp_monotonic": oracle.get("answer_received_monotonic"),
            "invalidated_candidate_ids": [
                invalidation.get("old_candidate_set_id")
            ],
            "fresh_observation_frame": latest.get("new_frame_id"),
            "fresh_observation_id": latest.get("new_observation_id"),
            "fresh_plan_id": replan.get("plan_id"),
            "fresh_candidate_id": (
                candidate_identity.get("candidate_id")
                if isinstance(candidate_identity, Mapping)
                else None
            ),
            "receipt_id": receipt.get("receipt_id"),
            "authority_transitions": owner_rows,
            "pid_count_on_act_tick": act.get("pid_invocations_on_act_tick"),
            "vehicle_control_count": act.get("candidate_control_submissions"),
            "act_frame": act.get("act_entry_frame"),
            "carla_response_frame": act.get("next_carla_frame"),
            "visualization": visualization,
        },
        "scientific_boundary": {
            "demonstrated": "One bounded CARLA ASK-to-ACT integration episode only.",
            "not_demonstrated": [
                "policy improvement",
                "safety improvement",
                "real-world deployment",
                "long-horizon autonomous takeover",
                "human interaction effectiveness",
            ],
        },
    }


def audit_closed_loop_bundle(
    live_dir: str | Path,
    equivalence_path: str | Path,
) -> dict[str, Any]:
    live_path = Path(live_dir)
    screenshot_names = {
        "ASK_SENT_WAIT_ACTIVE": "ask_sent_wait_active.png",
        "LATEST_OBSERVATION_CAPTURED": "answer_received_old_candidates_invalidated.png",
        "REPLANNING_FROM_LATEST_OBSERVATION": "replanning_from_latest_observation.png",
        "REPLAN_COMPLETE_RESUME_READY": "replan_complete_resume_ready.png",
        "CANDIDATE_AUTHORITY_GRANTED": "candidate_authority_granted.png",
        "ACT_CONTROL_TICK_ACTIVE": "act_control_tick_active.png",
        "ACT_CARLA_NEXT_FRAME_OBSERVED": "act_carla_next_frame_observed.png",
        "CONTROL_RETURNED_TO_BASELINE": "control_returned_to_baseline.png",
    }
    screenshots = {stage: live_path / name for stage, name in screenshot_names.items()}
    return audit_closed_loop_documents(
        _load_json(live_path / "LIVE_SHADOW_EVENT.json"),
        _load_json(live_path / "ASK_REPLAN_EVENT.json"),
        _load_json(live_path / "LIMITED_ACT_COMMIT_V0.json"),
        _load_json(Path(equivalence_path)),
        screenshot_files=screenshots,
    )


def build_evidence_index(paths: Iterable[str | Path]) -> dict[str, Any]:
    rows = []
    for value in paths:
        path = Path(value)
        rows.append(
            {
                "path": str(path.resolve()),
                "exists": path.is_file(),
                "bytes": path.stat().st_size if path.is_file() else 0,
                "sha256": _sha256(path) if path.is_file() else None,
            }
        )
    return {
        "schema_version": "driveclarify.end_to_end_closed_loop_demo.evidence_index.v1",
        "all_evidence_present": bool(rows) and all(row["exists"] for row in rows),
        "artifacts": rows,
    }

