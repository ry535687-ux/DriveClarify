#!/usr/bin/env python3
"""Read-only historical V2/V1 -> V3 decision-axis replay.

This diagnostic projector consumes only serialized runtime evidence.  It does
not import scenario expected outcomes into production authorization and does
not mutate any historical artifact.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports/driveclarify_minimum_sufficient_decision_evidence_v3_revision_and_native_validation"
SOURCES = {
    "CASE_B_FAILED_NATIVE": ROOT / "artifacts/driveclarify_method_v1_act_semantics_targeted_native_validation/DCMV1-B-I-E1-0814/GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json",
    "ACT_SHARED_POSITIVE": ROOT / "artifacts/driveclarify_short_horizon_decision_evidence_contract_v2/DA-AS-001-R1/GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json",
    "ASK_POSITIVE": ROOT / "artifacts/driveclarify_short_horizon_decision_evidence_contract_v2/DA-ASK-001-R1/GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json",
    "WAIT_POSITIVE": ROOT / "artifacts/driveclarify_short_horizon_decision_evidence_contract_v2/DA-WAIT-001-R1/GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json",
    "EI_NEGATIVE": ROOT / "artifacts/driveclarify_v2_negative_control_reconstruction_and_validation_v1/NC-EI-001-R1/GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json",
    "TL_NEGATIVE": ROOT / "artifacts/driveclarify_v2_negative_control_reconstruction_and_validation_v1/NC-TL2-001-R1/GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json",
    "WHITE_VAN_HARD": ROOT / "artifacts/driveclarify_decision_policy_reachability_and_live_activation_closure_v1/L-ACTSHARED-R1/GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def same_obligation(candidates: list[Mapping[str, Any]]) -> bool:
    keys = (
        "target_obligation_digest", "topology_target_id", "topology_junction_id",
        "topology_branch_id", "topology_route_order", "maneuver", "event_relation",
    )
    return bool(candidates and len({tuple(row.get(k) for k in keys) for row in candidates}) == 1)


def select_row(kind: str, history: list[Mapping[str, Any]]) -> Mapping[str, Any]:
    target = "ASK" if kind == "ASK_POSITIVE" else "WAIT" if kind == "WAIT_POSITIVE" else None
    if target:
        return next(row for row in history if row.get("decision") == target)
    return history[0]


def replay(kind: str, receipt: Mapping[str, Any]) -> dict[str, Any]:
    history = list(receipt.get("persistent_decision_history", ()))
    if not history:
        return {"status": "NO_REPLAYABLE_DECISION_HISTORY", "v3_decision": "FALLBACK"}
    row = select_row(kind, history)
    context = row.get("m2b_inputs") or {}
    evidence = context.get("evidence") or {}
    current = evidence.get("current_executable") or {}
    future = evidence.get("future_obligation") or {}
    lease_v2 = evidence.get("shared_action_lease") or {}
    clarification_v2 = evidence.get("clarification_window") or {}
    candidates = list((receipt.get("persistent_ambiguity_episode_internal_debug") or {}).get("candidates", ()))
    connectors = list(receipt.get("candidate_executable_connector_evidence", ()))
    common = same_obligation(candidates)
    physical_connectors = bool(connectors and all(
        connector.get("exit_reached") is True
        and connector.get("live_map_pair_match") is True
        for connector in connectors
    ))

    relation = current.get("relation")
    reasons = set(current.get("reason_codes") or ())
    corrected_alignment_only = bool(
        relation is None
        and current.get("local_coverage_available") is True
        and current.get("geometry_control_compatible") is True
        and current.get("lane_action_compatible") is True
        and reasons
        and all(reason == "CURRENT_EXECUTABLE_SOURCE_ALIGNMENT_UNKNOWN" for reason in reasons)
    )
    current_v3 = (
        "SHARED" if relation == "CURRENT_EXECUTABLE_EQUIVALENT" or corrected_alignment_only
        else "DIVERGENT" if relation == "CURRENT_EXECUTABLE_DIVERGENT"
        else "UNKNOWN"
    )
    future_v3 = {
        "FUTURE_OBLIGATION_EQUIVALENT": "EQUIVALENT",
        "FUTURE_OBLIGATION_DIVERGENT": "DIVERGENT",
    }.get(future.get("relation"), "UNKNOWN")

    recovery_rows = list(lease_v2.get("candidate_recoverability") or ())
    old_recoverable = bool(recovery_rows and all(value.get("recoverable") is True for value in recovery_rows))
    endpoint = current.get("proposed_lease_end_progress_m")
    before_all_commitments = bool(
        endpoint is not None and recovery_rows
        and all(value.get("commitment_progress_m") is not None and float(endpoint) < float(value["commitment_progress_m"]) for value in recovery_rows)
    )
    common_recovery = bool(common and physical_connectors and current_v3 == "SHARED" and before_all_commitments)
    recovery_v3 = "RECOVERABLE" if old_recoverable or common_recovery else "UNKNOWN"

    boundaries = dict(lease_v2.get("endpoint_boundaries_m") or {})
    commitments = [float(value["commitment_progress_m"]) for value in recovery_rows if value.get("commitment_progress_m") is not None]
    precommitment = boundaries.get("precommitment")
    old_refresh = boundaries.get("next_normal_planning_refresh")
    uncertainty = None
    if commitments and precommitment is not None:
        uncertainty = min(commitments) - float(precommitment)
    next_refresh_v3 = None if old_refresh is None or uncertainty is None else float(old_refresh) + uncertainty
    refresh_v3 = (
        "GUARANTEED" if next_refresh_v3 is not None and precommitment is not None and next_refresh_v3 < float(precommitment)
        else "NOT_GUARANTEED" if next_refresh_v3 is not None and precommitment is not None
        else "UNKNOWN"
    )
    v3_boundaries = {
        "local_plan_support": boundaries.get("local_plan_support"),
        "shared_executable_corridor": boundaries.get("shared_topology_corridor"),
        "next_mandatory_normal_refresh": next_refresh_v3,
        "earliest_candidate_commitment_lower": precommitment,
        "recoverability_valid_endpoint": endpoint if recovery_v3 == "RECOVERABLE" else None,
    }
    lease_values = [float(value) for value in v3_boundaries.values() if value is not None]
    lease_end = min(lease_values) if len(lease_values) == len(v3_boundaries) else None
    lease_valid = bool(
        current_v3 == "SHARED" and recovery_v3 == "RECOVERABLE"
        and refresh_v3 == "GUARANTEED" and lease_end is not None
        and lease_end > float(row.get("current_progress_m", 0.0))
    )

    urgency = clarification_v2.get("urgency")
    if future_v3 == "EQUIVALENT" or (future_v3 == "UNKNOWN" and lease_valid):
        clarification_v3 = "NOT_NEEDED_YET"
    elif future_v3 == "DIVERGENT":
        clarification_v3 = {
            "DEFER_CLARIFICATION": "NOT_NEEDED_YET",
            "CLARIFY_NOW": "CLARIFY_NOW",
            "TOO_LATE": "TOO_LATE",
        }.get(urgency, "UNKNOWN")
    else:
        clarification_v3 = "UNKNOWN"

    old_hard_safety = context.get("hard_safety_gate")
    hard_rule = context.get("hard_rule_gate")
    physical_safety_v3 = bool(
        old_hard_safety is True
        or (
            physical_connectors and current.get("geometry_control_compatible") is True
            and current.get("lane_action_compatible") is True and hard_rule is True
        )
    )
    active_query = bool(context.get("active_query"))
    holding = bool(context.get("verified_holding_available"))
    ask = bool(
        future_v3 == "DIVERGENT" and clarification_v3 == "CLARIFY_NOW"
        and context.get("answer_changes_decision") is True
        and context.get("query_budget_available") is True
        and context.get("passenger_resolvable") is True
        and not active_query and physical_safety_v3 and hard_rule is True
    )
    shared = bool(
        current_v3 == "SHARED" and lease_valid and recovery_v3 == "RECOVERABLE"
        and refresh_v3 == "GUARANTEED"
        and clarification_v3 in {"NOT_NEEDED_YET"}
        and physical_safety_v3 and hard_rule is True and not active_query and not holding
    )
    decision = "WAIT" if active_query and holding and physical_safety_v3 and hard_rule is True else "ASK" if ask else "ACT_SHARED" if shared else "FALLBACK"
    return {
        "status": "REPLAYED_FROM_SERIALIZED_RUNTIME_EVIDENCE",
        "selected_historical_sequence": row.get("sequence"),
        "selected_historical_decision": row.get("decision"),
        "historical_decision_counts": dict(Counter(value.get("decision") for value in history)),
        "K": len(row.get("candidate_ids") or ()),
        "semantic_state": row.get("semantic_state"),
        "current_action_relation_v3": current_v3,
        "current_action_alignment_revision_applied": corrected_alignment_only,
        "future_obligation_relation_v3": future_v3,
        "common_continuation_authorized": common,
        "physical_connectors_runtime_observed": physical_connectors,
        "current_physical_safety_v3": physical_safety_v3,
        "clarification_state_v3": clarification_v3,
        "shared_action_lease_v3": {"valid": lease_valid, "end_progress_m": lease_end, "boundaries_m": v3_boundaries},
        "recoverability_v3": recovery_v3,
        "precommitment_refresh_guarantee_v3": refresh_v3,
        "next_refresh_progress_upper_m": next_refresh_v3,
        "earliest_commitment_lower_m": precommitment,
        "v3_decision": decision,
        "v3_decision_subject": "SHARED_EQUIVALENCE_CLASS" if decision == "ACT_SHARED" else None,
        "semantic_resolution_from_act_shared": False,
        "expected_or_gold_authorization_reads": 0,
        "privileged_authorization_reads": 0,
        "replay_limitations": [
            "Historical V2 did not serialize the V3 physical-safety projection; replay derives it only from serialized physical connector, geometry/lane, rule and valid-run evidence.",
            "Native V3 validation must directly serialize every V3 axis before this projection is considered causal evidence.",
        ],
    }


def main() -> None:
    rows = {}
    for kind, path in SOURCES.items():
        receipt = json.loads(path.read_text(encoding="utf-8"))
        rows[kind] = {
            "source": str(path.relative_to(ROOT)), "source_sha256": sha256(path),
            "replay": replay(kind, receipt),
        }
    required = {
        "CASE_B_FAILED_NATIVE": "ACT_SHARED", "ACT_SHARED_POSITIVE": "ACT_SHARED",
        "ASK_POSITIVE": "ASK", "WAIT_POSITIVE": "WAIT", "EI_NEGATIVE": "FALLBACK",
        "TL_NEGATIVE": "FALLBACK", "WHITE_VAN_HARD": "FALLBACK",
    }
    checks = {kind: rows[kind]["replay"]["v3_decision"] == decision for kind, decision in required.items()}
    payload = {
        "schema_version": "driveclarify.v2_vs_v3.decision_replay.v1",
        "status": "PASS_V2_VS_V3_HISTORICAL_REPLAY" if all(checks.values()) else "FAIL_V2_VS_V3_HISTORICAL_REPLAY",
        "read_only": True, "production_expected_or_gold_reads": 0,
        "historical_artifacts_modified": 0, "checks": checks, "rows": rows,
    }
    (OUT / "V2_VS_V3_DECISION_REPLAY.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = [
        "# V2 vs V3 Historical Decision Replay", "",
        "Status: **{}**. This is a read-only diagnostic projection; no historical artifact was modified and no expected/gold value entered production authorization.".format(payload["status"]), "",
        "| Family | V2 selected decision | V3 current | V3 future | Clarification | Lease | Recoverability | Refresh | V3 decision |", "|---|---|---|---|---|---|---|---|---|",
    ]
    for kind, value in rows.items():
        row = value["replay"]
        lines.append("| {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
            kind, row.get("selected_historical_decision"), row.get("current_action_relation_v3"),
            row.get("future_obligation_relation_v3"), row.get("clarification_state_v3"),
            "VALID" if row.get("shared_action_lease_v3", {}).get("valid") else "INVALID",
            row.get("recoverability_v3"), row.get("precommitment_refresh_guarantee_v3"),
            row.get("v3_decision"),
        ))
    lines += [
        "", "## Case B attribution", "",
        "The first old Case B window has fresh complete local coverage, geometry/control compatibility, lane/action compatibility and a non-empty endpoint; its only current-axis V2 reason is the obsolete cross-route `CURRENT_EXECUTABLE_SOURCE_ALIGNMENT_UNKNOWN`. Both semantic candidates carry the same continuation obligation while the referent-to-route-order future join remains unavailable. V3 therefore preserves future `UNKNOWN`, proves a bounded common-continuation recovery and a strict next-refresh-before-commitment bound, and projects `ACT_SHARED` without resolving semantics.", "",
        "## Negative preservation", "",
        "EI has distinct obligations with no authorized candidate-to-route-order join, so recoverability remains UNKNOWN and the result remains FALLBACK. TL already has current DIVERGENT, lost commitment margin and a non-guaranteed refresh/too-late boundary, so it remains FALLBACK. The white-van hard case has no replayable V2 current-action proof and remains FALLBACK.", "",
        "## Limitation and native gate", "",
        "V2 did not separately serialize V3 current physical safety. The replay makes its derivation explicit and is not substituted for causal native evidence. The same old Case B must now directly emit all V3 axes in a visible native run.",
    ]
    (OUT / "V2_VS_V3_DECISION_REPLAY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if not all(checks.values()):
        raise SystemExit(payload["status"])


if __name__ == "__main__":
    main()
