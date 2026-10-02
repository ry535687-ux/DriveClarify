"""Clock-safe answer-to-action latency audit for RQ2-T.

The simulator quantity is computed from integer CARLA frame identifiers.  Wall
timestamps are retained only as efficiency diagnostics and cannot enter a
deadline contract.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from .measurement import canonical_sha256


def _frame(value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("RQ2_T_TIMING_FRAME_INVALID:" + name)
    return value


def _wall_delta(timing: Mapping[str, Any], end: str, start: str) -> float | None:
    a, b = timing.get(end), timing.get(start)
    if not (
        isinstance(a, (int, float))
        and isinstance(b, (int, float))
        and math.isfinite(float(a))
        and math.isfinite(float(b))
    ):
        return None
    return float(a) - float(b)


def audit_answer_to_first_eligible_action(
    *,
    timing: Mapping[str, Any],
    timeline: Sequence[Mapping[str, Any]],
    per_step_timing: Sequence[Mapping[str, Any]],
    control_source_receipts: Sequence[Mapping[str, Any]],
    fixed_delta_seconds: float,
) -> dict[str, Any]:
    """Audit answer, plan-ready, eligibility, activation, and control frames."""

    if not (
        isinstance(fixed_delta_seconds, (int, float))
        and not isinstance(fixed_delta_seconds, bool)
        and math.isfinite(float(fixed_delta_seconds))
        and float(fixed_delta_seconds) > 0.0
    ):
        raise ValueError("RQ2_T_FIXED_DELTA_INVALID")
    answer = _frame(timing.get("answer_received_frame_id"), "answer_received")
    replan_started = _frame(
        timing.get("fresh_replan_started_frame_id"), "fresh_replan_started"
    )
    replan_completed = _frame(
        timing.get("fresh_replan_completed_frame_id"), "fresh_replan_completed"
    )
    plan_ready = _frame(
        timing.get("method_v2_1_plan_ready_frame_id"), "selected_plan_ready"
    )
    activated = _frame(
        timing.get("execution_activated_frame_id"), "execution_activated"
    )
    eligible_rows = [
        row
        for row in timeline
        if row.get("fresh_plan_state") == "SELECTED_PLAN_READY"
        and row.get("motion_eligibility") is True
        and row.get("pre_activation_feasibility", {}).get("feasible") is True
    ]
    if not eligible_rows:
        raise ValueError("RQ2_T_FIRST_ELIGIBLE_ACTION_NOT_OBSERVED")
    first_eligible = min(_frame(row.get("frame"), "timeline_eligible") for row in eligible_rows)
    act_decision_rows = [row for row in per_step_timing if row.get("decision_output") == "ACT"]
    if not act_decision_rows:
        raise ValueError("RQ2_T_FIRST_ACT_DECISION_NOT_OBSERVED")
    first_act_decision = min(
        _frame(row.get("control_world_frame_id"), "first_act_decision")
        for row in act_decision_rows
    )
    answer_conditioned_controls = [
        row
        for row in control_source_receipts
        if row.get("control_source") == "FRESH_UNIQUE_CANDIDATE"
        and row.get("decision_label") == "ACT"
    ]
    if not answer_conditioned_controls:
        raise ValueError("RQ2_T_FIRST_ANSWER_CONDITIONED_CONTROL_NOT_OBSERVED")
    first_control = min(
        _frame(row.get("source_frame_id"), "first_answer_conditioned_control")
        for row in answer_conditioned_controls
    )
    ordered = (answer, replan_started, replan_completed, plan_ready, first_eligible, activated, first_control)
    if any(right < left for left, right in zip(ordered, ordered[1:])):
        raise ValueError("RQ2_T_ANSWER_ACTION_TRANSITION_ORDER_INVALID")
    first_control_row = next(
        row
        for row in per_step_timing
        if row.get("control_world_frame_id") == first_control
    )
    action_ticks = first_eligible - answer
    result = {
        "schema_version": "driveclarify.rq2_t.answer_action_latency.v1",
        "status": "PASS_AUTHORITATIVE_STATIC_AND_RECEIPT_AUDIT",
        "scientific_clock_domain": "CARLA_SIMULATOR_TICKS",
        "fixed_delta_seconds": float(fixed_delta_seconds),
        "answer_accepted_frame": answer,
        "answer_conditioned_replan_started_frame": replan_started,
        "answer_conditioned_replan_completed_frame": replan_completed,
        "answer_conditioned_plan_ready_frame": plan_ready,
        "first_answer_conditioned_eligible_action_frame": first_eligible,
        "execution_activated_frame": activated,
        "first_act_decision_frame": first_act_decision,
        "selected_route_installed_frame": activated,
        "first_actual_answer_conditioned_control_frame": first_control,
        "first_selected_route_conditioned_model_frame": first_control,
        "selected_route_install_to_control_ticks": first_control - activated,
        "answer_to_plan_ready_ticks": plan_ready - answer,
        "answer_to_first_eligible_action_ticks": action_ticks,
        "answer_to_first_eligible_action_simulation_s": (
            action_ticks * float(fixed_delta_seconds)
        ),
        "answer_to_first_actual_control_ticks": first_control - answer,
        "wall_efficiency_diagnostics_only": {
            "answer_to_replan_start_wall_s": _wall_delta(
                timing, "fresh_replan_started_monotonic_s", "answer_received_monotonic_s"
            ),
            "answer_to_plan_ready_wall_s": _wall_delta(
                timing, "method_v2_1_plan_ready_monotonic_s", "answer_received_monotonic_s"
            ),
            "fresh_replan_compute_wall_s": timing.get("T_fresh_replan_s"),
            "answer_to_execution_activation_wall_s": _wall_delta(
                timing, "execution_activated_monotonic_s", "answer_received_monotonic_s"
            ),
            "answer_to_first_control_decision_complete_wall_s": (
                None
                if first_control_row.get("decision_complete_monotonic_s") is None
                else float(first_control_row["decision_complete_monotonic_s"])
                - float(timing["answer_received_monotonic_s"])
            ),
            "answer_to_first_control_return_wall_s": (
                None
                if first_control_row.get("final_control_return_monotonic_s") is None
                else float(first_control_row["final_control_return_monotonic_s"])
                - float(timing["answer_received_monotonic_s"])
            ),
        },
        "wall_latency_used_in_simulator_deadline": False,
        "owner_chain": [
            "PersistentAmbiguityRuntimeV1._poll_answer",
            "persistent semantic store ANSWER_ARRIVED_VALID; no SimLingo command-history mutation",
            "PersistentAmbiguityRuntimeV1._run_persistent_fresh_replan",
            "PersistentAmbiguityRuntimeV1._method_v2_1_stage_fresh_plan",
            "PersistentAmbiguityRuntimeV1._method_v2_1_try_activate_plan_ready",
            "atomic selected-route installation at activation",
            "next-tick selected-route-conditioned normal forward",
            "single existing control return path",
        ],
    }
    result["audit_digest"] = canonical_sha256(result)
    return result


__all__ = ["audit_answer_to_first_eligible_action"]
