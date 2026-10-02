"""Outcome-blind native-trace evaluability for RQ2-T-CG Formal V3.

This module intentionally has no dependency on B1/B2 endpoint values.  It
decides whether the frozen native trace reached the material needed to build
the paired post-trace views; B1 and B2 results are never operands.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Mapping, Sequence


EVALUABLE = "EVALUABLE"
NON_EVALUABLE_NATIVE_NONCOMPLETION = "NON_EVALUABLE_NATIVE_NONCOMPLETION"
INTEGRITY_INVALID = "INTEGRITY_INVALID"
ZERO_EXPOSURE_INFRASTRUCTURE_FAILURE = "ZERO_EXPOSURE_INFRASTRUCTURE_FAILURE"

ALLOWED_CLASSIFICATIONS = (
    EVALUABLE,
    NON_EVALUABLE_NATIVE_NONCOMPLETION,
    INTEGRITY_INVALID,
    ZERO_EXPOSURE_INFRASTRUCTURE_FAILURE,
)

REQUIRED_BUILDER_MATERIAL = (
    "formal_valid",
    "same_source_identity_all_views",
    "commitment_observed",
    "commitment_time_s",
    "deadline_time_s",
    "source_row_count",
    "B1_precommitment_sufficiency",
    "B2_precommitment_sufficiency",
    "B1_window_observed",
    "B2_window_observed",
    "B1_window_duration_s",
    "B2_window_duration_s",
    "B1_first_sufficiency_TTCmt_s",
    "B2_first_sufficiency_TTCmt_s",
    "B2_only_same_frame_sufficiency_count",
    "rule_results",
)

# These are native integrity operands, not H-CG outcome operands.
REQUIRED_COMMON_INTEGRITY = (
    "static_route_admission",
    "exact_child_construction",
    "scenario_identity",
    "zero_control_mutation",
    "clean_teardown",
    "background_traffic_policy_runtime_pass",
    "random_background_generator_not_attached",
    "random_background_vehicle_requests_zero",
    "automatic_parked_mesh_requests_zero",
    "official_critical_integrity_clean",
    "error_absent",
)

REQUIRED_EVALUABLE_TRACE = (
    "first_legal_source_row",
    "all_event_starts",
    "event_windows_entered",
    "commitment_observed",
    "required_scientific_endpoint_reached",
    "runtime_route_persisted",
    "posttrace_builder_complete",
    "paired_source_identity",
    "complete_actionability_deadline_information",
    "all_compute_control_intent_invariants_zero",
)


def _false_keys(checks: Mapping[str, Any], keys: Sequence[str]) -> list[str]:
    return [key for key in keys if checks.get(key) is not True]


def _builder_material_complete(builder: Any) -> bool:
    return (
        isinstance(builder, Mapping)
        and all(key in builder for key in REQUIRED_BUILDER_MATERIAL)
        and builder.get("formal_valid") is True
        and builder.get("same_source_identity_all_views") is True
        and builder.get("commitment_observed") is True
        and isinstance(builder.get("source_row_count"), int)
        and int(builder["source_row_count"]) > 0
    )


def classify_episode(native: Mapping[str, Any]) -> Mapping[str, Any]:
    """Classify one attempt without reading any B1/B2 scientific result.

    ``native`` may contain builder output so its completeness can be checked,
    but endpoint *values* are deliberately never read.  The classification is
    a function only of exposure, lifecycle/integrity receipts, native endpoint
    reachability, and builder/source completeness.
    """

    exposed = native.get("exposed") is True
    checks = native.get("checks") if isinstance(native.get("checks"), Mapping) else {}
    builder = native.get("builder")
    runtime = native.get("runtime") if isinstance(native.get("runtime"), Mapping) else {}
    terminal = native.get("terminal") if isinstance(native.get("terminal"), Mapping) else {}
    official = native.get("official") if isinstance(native.get("official"), Mapping) else {}

    if not exposed:
        return {
            "classification": ZERO_EXPOSURE_INFRASTRUCTURE_FAILURE,
            "reason_code": "ZERO_EXPOSURE_ACTIVATION_NOT_REACHED",
            "primary_evaluable": False,
            "exposed": False,
            "commitment_reached": False,
            "route_completion_percent": official.get("route_completion_percent"),
            "reads_b1_b2_outcome": False,
        }

    common_failures = _false_keys(checks, REQUIRED_COMMON_INTEGRITY)
    natural_process = (
        runtime.get("termination_reason") == "NATURAL_EVALUATOR_COMPLETION"
        and runtime.get("evaluator_return_code") == 0
    )
    endpoint_reached = checks.get("required_scientific_endpoint_reached") is True
    commitment_reached = checks.get("commitment_observed") is True

    if common_failures:
        return {
            "classification": INTEGRITY_INVALID,
            "reason_code": "INTEGRITY_OPERAND_FAILED:" + ",".join(common_failures),
            "primary_evaluable": False,
            "exposed": True,
            "commitment_reached": commitment_reached,
            "route_completion_percent": official.get("route_completion_percent"),
            "reads_b1_b2_outcome": False,
        }

    if not endpoint_reached:
        if natural_process and not commitment_reached:
            reason = "NATIVE_NATURAL_TERMINATION_COMMITMENT_NOT_REACHED"
        elif natural_process:
            reason = "NATIVE_NATURAL_TERMINATION_SCIENTIFIC_ENDPOINT_INCOMPLETE"
        else:
            return {
                "classification": INTEGRITY_INVALID,
                "reason_code": "NONNATURAL_POSTEXPOSURE_TERMINATION",
                "primary_evaluable": False,
                "exposed": True,
                "commitment_reached": commitment_reached,
                "route_completion_percent": official.get("route_completion_percent"),
                "reads_b1_b2_outcome": False,
            }
        return {
            "classification": NON_EVALUABLE_NATIVE_NONCOMPLETION,
            "reason_code": reason,
            "primary_evaluable": False,
            "exposed": True,
            "commitment_reached": commitment_reached,
            "route_completion_percent": official.get("route_completion_percent"),
            "reads_b1_b2_outcome": False,
            "terminal_state": terminal.get("state"),
        }

    trace_failures = _false_keys(checks, REQUIRED_EVALUABLE_TRACE)
    if trace_failures or not _builder_material_complete(builder):
        failures = list(trace_failures)
        if not _builder_material_complete(builder):
            failures.append("complete_builder_material")
        return {
            "classification": INTEGRITY_INVALID,
            "reason_code": "EVALUABLE_TRACE_INTEGRITY_FAILED:" + ",".join(sorted(set(failures))),
            "primary_evaluable": False,
            "exposed": True,
            "commitment_reached": commitment_reached,
            "route_completion_percent": official.get("route_completion_percent"),
            "reads_b1_b2_outcome": False,
        }

    return {
        "classification": EVALUABLE,
        "reason_code": "FROZEN_SCIENTIFIC_ENDPOINT_AND_PAIRED_SOURCE_COMPLETE",
        "primary_evaluable": True,
        "exposed": True,
        "commitment_reached": True,
        "route_completion_percent": official.get("route_completion_percent"),
        "reads_b1_b2_outcome": False,
    }


def primary_gate(entries: Sequence[Mapping[str, Any]], scene_codes: Sequence[str]) -> Mapping[str, Any]:
    counts = Counter(
        str(row["scene_code"])
        for row in entries
        if row.get("classification") == EVALUABLE
    )
    by_scene = {str(code): int(counts.get(str(code), 0)) for code in scene_codes}
    total = sum(by_scene.values())
    checks = {
        "total_primary_evaluable_at_least_40_of_48": total >= 40,
        "every_scene_primary_evaluable_at_least_5_of_6": all(value >= 5 for value in by_scene.values()),
    }
    return {
        "pass": all(checks.values()),
        "total_primary_evaluable": total,
        "planned_total": 48,
        "per_scene_primary_evaluable": by_scene,
        "checks": checks,
        "failed_checks": [key for key, value in checks.items() if not value],
        "frames_used_as_independent_scientific_n": False,
    }


def prospective_gate_possible(
    entries: Sequence[Mapping[str, Any]],
    roster_cells: Sequence[Mapping[str, Any]],
    scene_codes: Sequence[str],
) -> Mapping[str, Any]:
    attempted = {str(row["cell_id"]) for row in entries}
    evaluable = Counter(
        str(row["scene_code"])
        for row in entries
        if row.get("classification") == EVALUABLE
    )
    remaining = Counter(
        str(row["scene_code"])
        for row in roster_cells
        if str(row["cell_id"]) not in attempted
    )
    maximum_by_scene = {
        str(code): int(evaluable.get(str(code), 0) + remaining.get(str(code), 0))
        for code in scene_codes
    }
    maximum_total = sum(evaluable.values()) + sum(remaining.values())
    possible = maximum_total >= 40 and all(value >= 5 for value in maximum_by_scene.values())
    return {
        "possible": possible,
        "maximum_total_primary_evaluable": maximum_total,
        "maximum_per_scene_primary_evaluable": maximum_by_scene,
        "hard_stop_reason": None if possible else (
            "TOTAL_MAXIMUM_BELOW_40" if maximum_total < 40
            else "SCENE_MAXIMUM_BELOW_5_OF_6"
        ),
    }
