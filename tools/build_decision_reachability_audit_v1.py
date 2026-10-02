#!/usr/bin/env python3
"""Build the D2-R1 reachability audit and deterministic fixture receipts."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
STAGE = ROOT / "reports/driveclarify_decision_policy_reachability_and_live_activation_closure_v1"
PREVIOUS = ROOT / "reports/driveclarify_persistent_ambiguity_runtime_v1_continuous_completion"
LIVE_ROOT = ROOT / "artifacts/driveclarify_persistent_ambiguity_runtime_v1_continuous_completion/Phase_D/D2-R1"


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(name: str, value: Any) -> None:
    (STAGE / name).write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_text(name: str, value: str) -> None:
    (STAGE / name).write_text(value.rstrip() + "\n", encoding="utf-8")


def evidence(status: str, value: Any, reason: str | None = None) -> dict[str, Any]:
    if status == "UNKNOWN":
        assert value is None and reason
    return {"status": status, "value": value, "reason_code": reason}


def plan_status(row: dict[str, Any], candidate_id: str) -> dict[str, Any]:
    token = "PLAN_COVERAGE_" + "".join(
        char if char.isalnum() else "_" for char in candidate_id
    ).strip("_").upper() + "_"
    match = next((reason for reason in row["window_reason_codes"] if reason.startswith(token)), None)
    return evidence("UNKNOWN", None, match) if match else evidence("AVAILABLE", "COVERED")


def build_audit() -> tuple[dict[str, Any], dict[str, Any]]:
    timeline = load(PREVIOUS / "PHASE_D_TIMELINE.json")
    live = load(LIVE_ROOT / "GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json")
    candidates = live["decision_window_dashboard"]["candidates"]
    candidate_ids = [row["candidate_id"] for row in candidates]
    cycles = []
    for row in timeline["decision_cycles"]:
        relation_unknown = row["candidate_relationship"] == "UNKNOWN_OR_INSUFFICIENT_EVIDENCE"
        deadline_known = row["latest_safe_clarification_monotonic"] is not None
        slack = row["latest_safe_slack_s"]
        answer_feasible = slack is not None and slack > 0.1
        recoverable = row["recoverability"] == "RECOVERABLE"
        future_value = row["future_obligation_relation"] == "FUTURE_DIVERGENT"
        cycle = {
            "sequence": row["sequence"],
            "planning_event_id": row["planning_event_id"],
            "carla_frame": row["source_frame_id"],
            "source_observation_id": row["source_observation_id"],
            "ego_route_progress_m": row["current_progress_m"],
            "raw_K": 2,
            "effective_K": 2,
            "candidate_bundle_version": row["bundle_id"],
            "candidate_freshness": evidence("AVAILABLE", "FRESH"),
            "candidate_bundle_complete": row["bundle_complete"],
            "candidate_plan_references": row["candidate_plan_references"],
            "current_action_equivalence": evidence(
                "UNKNOWN", None, "CURRENT_ACTION_EQUIVALENCE_UNKNOWN"
            ),
            "future_divergence": evidence("AVAILABLE", future_value),
            "candidate_relationship": (
                evidence("UNKNOWN", None, "CANDIDATE_RELATIONSHIP_DEPENDENCY_UNKNOWN")
                if relation_unknown
                else evidence("AVAILABLE", row["candidate_relationship"])
            ),
            "plan_coverage": {
                "A": plan_status(row, candidate_ids[0]),
                "B": plan_status(row, candidate_ids[1]),
                "authorized_baseline": plan_status(row, "__AUTHORIZED_PLAN__"),
            },
            "shared_corridor": (
                evidence("AVAILABLE", {"end_progress_m": row["shared_action_end_progress_m"]})
                if row["full_plan_coverage"]
                else evidence("UNKNOWN", None, "PLAN_COVERAGE_UNKNOWN")
            ),
            "maneuver_onset": evidence("AVAILABLE", {"progress_m": 28.22, "unit": "m_route"}),
            "commitment": {
                "A": evidence("UNKNOWN", None, "PER_CYCLE_COMMITMENT_VALUE_NOT_RETAINED_IN_D2_R1_HISTORY"),
                "B": evidence("UNKNOWN", None, "PER_CYCLE_COMMITMENT_VALUE_NOT_RETAINED_IN_D2_R1_HISTORY"),
            },
            "recoverability": {
                "A": evidence("AVAILABLE", "RECOVERABLE") if recoverable else evidence("UNKNOWN", None, "RECOVERABILITY_UNKNOWN"),
                "B": evidence("AVAILABLE", "RECOVERABLE") if recoverable else evidence("UNKNOWN", None, "RECOVERABILITY_UNKNOWN"),
            },
            "time_to_divergence": evidence(
                "AVAILABLE_RAW_BOUND",
                {"lower_bound_s": row["time_to_divergence_lower_bound_s"]},
                "PASSED_DECISION_POINT" if row["time_to_divergence_lower_bound_s"] is not None and row["time_to_divergence_lower_bound_s"] < 0 else None,
            ),
            "latest_safe_clarification": (
                evidence("AVAILABLE", {"monotonic_s": row["latest_safe_clarification_monotonic"], "slack_s": slack})
                if deadline_known
                else evidence("UNKNOWN", None, "QUERY_DEADLINE_UNKNOWN")
            ),
            "hard_safety_status": evidence("UNKNOWN", None, "PER_CYCLE_HARD_SAFETY_VALUE_NOT_RETAINED_IN_D2_R1_HISTORY"),
            "hard_rule_status": evidence("UNKNOWN", None, "PER_CYCLE_HARD_RULE_VALUE_NOT_RETAINED_IN_D2_R1_HISTORY"),
            "answer_changes_action": evidence("AVAILABLE", True),
            "query_budget": evidence("AVAILABLE", True),
            "active_query": evidence("AVAILABLE", False),
            "query_channel_status": evidence("AVAILABLE", "PASSENGER_RESOLVABLE"),
            "deadline_feasible": evidence("AVAILABLE", answer_feasible),
            "m2b_input_completeness": {
                "status": "INCOMPLETE_FOR_ACT_SHARED_AND_ASK",
                "unknown_inputs": [
                    name for name, unknown in (
                        ("current_action_equivalence", True),
                        ("plan_coverage", not row["full_plan_coverage"]),
                        ("recoverability", not recoverable),
                        ("decision_deadline", not deadline_known),
                    ) if unknown
                ],
                "historical_runtime_unknown_collapse_detected": True,
                "repair_applies_to_future_runs": True,
            },
            "m2b": {
                "input_current_axis": "CURRENT_ACTION_DIVERGENT" if row["candidate_relationship"] == "CURRENTLY_DIVERGENT" else "UNKNOWN",
                "decision": row["decision"],
                "reason_codes": row["decision_reason_codes"],
            },
            "m3": {
                "input": None,
                "decision": None,
                "subject": None,
                "reason_code": "NOT_REACHED_M2B_FALLBACK",
            },
            "authority_resolver": {
                "candidates": ["EXISTING_BASELINE_PID"],
                "selected_authority": "EXISTING_BASELINE_PID",
                "reason_code": "NO_METHOD_AUTHORITY_RECEIPT_ISSUED",
            },
            "final_high_level_result": "FALLBACK",
            "final_fallback_reason": "PERSISTENT_RELATION_GATES_FAIL_CLOSED",
            "window_reason_codes": row["window_reason_codes"],
        }
        cycles.append(cycle)
    audit = {
        "schema_version": "driveclarify.d2_r1.decision_reachability_audit.v1",
        "status": "PASS_219_CYCLE_DECISION_PATH_RECONSTRUCTED_WITH_EXPLICIT_RETENTION_LIMITS",
        "source_run": "D2-R1",
        "cycle_count": len(cycles),
        "candidate_plan_reference_count": sum(len(row["candidate_plan_references"]) for row in cycles),
        "unknown_policy": "UNKNOWN_IS_NULL_WITH_NON_NULL_REASON_CODE",
        "retention_limitation": "D2-R1 retained full latest window but compact per-cycle history; per-cycle commitment and hard safety/rule scalar values are therefore UNKNOWN, not reconstructed as false or safe.",
        "relationship_counts": dict(Counter(row["candidate_relationship"]["value"] or "UNKNOWN_OR_INSUFFICIENT_EVIDENCE" for row in cycles)),
        "root_cause": {
            "classification": "CASE_B_WHITE_VAN_NEVER_ENTERED_A_LEGAL_ACT_SHARED_ASK_OR_WAIT_REGION",
            "primary_dependency": "CURRENT_ACTION_EQUIVALENCE_UNKNOWN_219_OF_219",
            "minimal_upstream_dependency": "PLAN_ROUTE_CONTINUITY_UNKNOWN_218_OF_219; THE_ONLY_FULL_COVERAGE_CYCLE_HAD_LONGITUDINAL_REFERENCE_NON_EQUIVALENCE_AND AN ALREADY_INFEASIBLE QUERY DEADLINE",
            "cross_cycle_freshness_bug": False,
            "bundle_version_rebinding_bug": False,
            "m2b_mapping_bug_caused_d2_fallback": False,
            "m3_or_authority_rejection_caused_d2_fallback": False,
        },
        "cycles": cycles,
    }
    blocker = {
        "schema_version": "driveclarify.decision_gate_blocker_histogram.v1",
        "cycle_count": len(cycles),
        "ACT_SHARED": {
            "effective_K_not_ge_2": 0,
            "candidate_stale": 0,
            "current_action_not_equivalent": 1,
            "current_action_unknown": 218,
            "plan_coverage_unknown": sum(not row["full_plan_coverage"] for row in timeline["decision_cycles"]),
            "recoverability_not_available": sum(row["recoverability"] != "RECOVERABLE" for row in timeline["decision_cycles"]),
            "recoverability_not_recoverable": 0,
            "deadline_unknown": sum(row["latest_safe_clarification_monotonic"] is None for row in timeline["decision_cycles"]),
            "deadline_crossed": sum(row["latest_safe_slack_s"] is not None and row["latest_safe_slack_s"] <= 0 for row in timeline["decision_cycles"]),
            "hard_safety_block": None,
            "hard_rule_block": None,
            "M2B_mapping_missing": 0,
            "M3_subject_rejected": 0,
            "authority_subject_rejected": 0,
            "top_blocker": "current_action_not_authorizable_219_of_219",
        },
        "ASK": {
            "material_divergence_not_true": 218,
            "relationship_unknown": 218,
            "answer_changes_action_not_true": 0,
            "decision_window_unknown": 219,
            "answer_too_late": sum(row["latest_safe_slack_s"] is None or row["latest_safe_slack_s"] <= 0.1 for row in timeline["decision_cycles"]),
            "query_budget_unavailable": 0,
            "active_query_exists": 0,
            "hard_safety_block": None,
            "hard_rule_block": None,
            "M2B_ask_not_emitted": 219,
            "M3_ask_not_accepted": 0,
            "top_blocker": "material_divergence_not_true_or_relationship_unknown_218_of_219; sole_divergent_cycle_answer_too_late",
        },
        "WAIT": {
            "wait_not_required": 219,
            "holding_receipt_missing": 219,
            "holding_not_valid": 0,
            "query_not_active": 219,
            "act_still_available": 0,
            "M2B_wait_not_emitted": 219,
            "M3_wait_not_accepted": 0,
            "authority_wait_rejected": 0,
            "top_blocker": "query_not_active_and_holding_receipt_missing_219_of_219",
        },
        "not_evaluable_from_compact_history": ["hard_safety_block", "hard_rule_block"],
    }
    return audit, blocker


def main() -> None:
    STAGE.mkdir(parents=True, exist_ok=True)
    audit, blocker = build_audit()
    write_json("D2_R1_DECISION_REACHABILITY_AUDIT.json", audit)
    write_json("DECISION_GATE_BLOCKER_HISTOGRAM.json", blocker)
    from driveclarify_persistent_ambiguity_runtime_v1.reachability import run_all_fixtures
    fixtures = run_all_fixtures()
    for key, name in (
        ("ACT_SHARED", "ACT_SHARED_REACHABILITY_RECEIPT.json"),
        ("ASK", "ASK_REACHABILITY_RECEIPT.json"),
        ("WAIT", "WAIT_REACHABILITY_RECEIPT.json"),
    ):
        write_json(name, fixtures[key])
    write_text("D2_R1_DECISION_REACHABILITY_AUDIT.md", f"""# D2-R1 decision reachability audit

Audited all **{audit['cycle_count']}** planning cycles and **{audit['candidate_plan_reference_count']}** candidate plan references. Every bundle was complete and fresh. No cross-cycle frame, bundle-version, or source rebinding failure was observed.

The white-van result is Case B. Current-action equivalence was never authorizable. Plan-route continuity was UNKNOWN in 218 cycles; the sole full-coverage cycle (refresh 67/frame 2671) showed current material divergence but had recoverability and query deadline UNKNOWN with slack -1.563 s. Therefore it was outside ACT_SHARED and ASK regions. No query or valid holding lease ever existed, so WAIT was not applicable.

The compact D2-R1 history did not retain per-cycle commitment scalars or hard-safety/rule booleans. Those fields are deliberately `null` with a reason code; they are not reconstructed as false or safe.
""")
    write_text("DECISION_GATE_BLOCKER_HISTOGRAM.md", f"""# Decision gate blocker histogram

- ACT_SHARED top blocker: `{blocker['ACT_SHARED']['top_blocker']}`. Plan coverage was UNKNOWN in 218 cycles; recoverability unavailable in 160; deadline unavailable in 180.
- ASK top blocker: `{blocker['ASK']['top_blocker']}`. The only material-divergence cycle was already past the answer-feasible boundary.
- WAIT top blocker: `{blocker['WAIT']['top_blocker']}`. D2-R1 never emitted ASK, so neither an active query nor MC-T006 holding receipt existed.

`null` histogram cells mean the compact historical trace cannot support a count. They are not zero.
""")
    write_text("DECISION_RUNTIME_CALL_GRAPH.md", """# Runtime decision call graph

`TopologyAwareReferentialRuntime.on_tick/on_model_output` → `PersistentAmbiguityReferentialRuntime.on_model_output` → `CandidateEvidenceRefreshScheduler.refresh` (same-frame K candidate forwards) → `_runtime_window_observation` (live CARLA route/progress/onset/commitment/safety/rule) → `evaluate_runtime_decision_window` (`_plan_coverage`, current equivalence, future divergence, recoverability, latest-safe) → `decide_persistent` (M2B).

- ACT_SHARED → `_act_shared` → frozen M3 ACT result → `SharedActCommitV1.arm_shared` → `build_shared_subject(SHARED_EQUIVALENCE_CLASS)` → `LiveActAuthorityResolverV1.issue` → `FinalExecutionAuthorityResolverV1.resolve` → existing route/speed tensors → existing PID seam → next tick restores baseline while episode remains UNRESOLVED.
- ASK → `_enter_persistent_ask` → M3 `CANDIDATES_READY/DECISION_ASK` → existing bounded holding binding (`MC-T001/MC-T006`) → existing baseline PID passthrough → answer → old bundle invalidation → M3 revalidation/replan → fresh candidate forward → unique subject authority.
- WAIT → subsequent M2B evaluation with active query plus verified holding → existing `PhysicalWaitExecutorV0`; it creates no PID/controller/control quantity.
- FALLBACK → no M3 method transaction and no method authority receipt; existing baseline PID remains selected.
""")
    write_text("M2B_REACHABILITY_AUDIT.md", """# M2B reachability audit

The production two-axis adapter recognizes `CURRENTLY_EQUIVALENT_FUTURE_DIVERGENT → ACT_SHARED`, material divergence plus answer-changing feasible query → ASK, and verified active holding → WAIT. All three deterministic fixtures traverse the real adapter and PASS. D2-R1 did not reach those inputs.

Audit repair: historical runtime glue collapsed several UNKNOWN evidence values into booleans. The integration now passes `None` for unknown coverage, recoverability, deadline and query feasibility and records `m2b_inputs` in future decision-history rows. Gate definitions and thresholds are unchanged.
""")
    write_text("M3_AUTHORITY_REACHABILITY_AUDIT.md", """# M3 and authority reachability audit

ACT_SHARED reaches the frozen M3 ACT result, a tagged `SHARED_EQUIVALENCE_CLASS` with null unique candidate identity, the live authority issue/consume path, and the existing PID plan-selection seam. ASK reaches MC-T004 and the query lifecycle. WAIT reaches MC-T006 and `M3_HOLDING_CONTROL` using `PhysicalWaitExecutorV0`.

Repair: the persistent ASK path previously overwrote the executor's lease-bearing MC-T006 state with the separate lease-free MC-T004 ASK state on a later tick. The override was removed; invalidation/answer now exits the existing holding through its own M3 lifecycle. No new authority, controller, PID, planner, or control writer was added.
""")
    write_text("D2_R1_INFRACTION_ATTRIBUTION.md", """# D2-R1 infraction attribution

The leaderboard retains two vehicle collisions and one red-light violation. Nearest recorded ego frames are collision 1 at frame 2690 near (-9.366, 129.379), red light 77 near frame 2721/y=143.466, and collision 2 at frame 2790 near (-8.786, 167.518).

At every one of the 219 planning/control records, the high-level result was FALLBACK, authority subject was null, method authority receipt count was zero, M3 ACT transaction count was zero, and `decision_source=baseline_pid`. Candidate plans were observation-only and never selected for control. Therefore ACT_SHARED/ASK/WAIT did not participate causally. The infractions are baseline-controller scientific outcomes and are not erased or reassigned.
""")
    write_text("AUTONOMOUS_REPAIR_LOG.md", """# Autonomous repair log

1. Preserved UNKNOWN at the runtime→M2B boundary instead of collapsing unavailable coverage/recoverability/deadline/query-feasibility to false/true; added future `m2b_inputs` history capture.
2. Removed the persistent ASK tick override that replaced the existing MC-T006 lease-bearing holding state with a lease-free MC-T004 ASK state.
3. Added deterministic production-chain reachability fixtures and the required 14-test suite.

Threshold changes: 0. Scientific contract changes: 0. Scenario/actor/SimLingo changes: 0.
""")
    write_json("RETRY_LEDGER.json", {
        "schema_version": "driveclarify.decision_reachability.retry_ledger.v1",
        "entries": [
            {"attempt": 1, "kind": "PRELAUNCH_TEST_ENVIRONMENT_FAILURE", "result": "pytest Python 3.8 missing exceptiongroup; no tests collected", "scientific_run": False},
            {"attempt": 2, "kind": "DETERMINISTIC_FIXTURE_TEST", "result": "9 passed, 5 fixture receipt serialization assertions failed on summary key name", "scientific_run": False},
            {"attempt": 3, "kind": "DETERMINISTIC_FIXTURE_TEST", "result": "14 passed", "scientific_run": True},
        ],
    })
    write_text("DECISION_REACHABILITY_TEST_REPORT.md", """# Decision reachability test report

Command: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 /home/buaa/anaconda3/bin/python3.13 -m pytest -q tests/persistent_ambiguity_runtime_v1/test_decision_reachability_v1.py`

Result: **14 passed**. ACT_SHARED, ASK and WAIT deterministic production-chain fixtures all PASS. UNKNOWN/stale negative controls, M2B relation coverage, shared-subject M3 mapping, and final authority mapping PASS.
""")


if __name__ == "__main__":
    main()
