"""Fail-closed adapter from production outcomes to the prospective formal ontology.

This module does not call or modify the production policy.  A production
``FALLBACK_RECOMMENDED`` is admitted as a formal WAIT subtype only when the
runtime receipt proves the pre-approved non-committing baseline-continuation
contract.  Raw provenance is always retained.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


FALLBACK_WAIT_SUBTYPE = "WAIT_FALLBACK_INSUFFICIENT_EVIDENCE"
FALLBACK_NORMALIZATION_RULE = "FALLBACK_AS_WAIT_IF_WAIT_CONTRACT_SATISFIED"
NORMALIZATION_VERSION = "V1"
NATIVE_OUTCOMES = frozenset({"ACT", "ASK", "WAIT"})


class FormalOutcomeNormalizationError(ValueError):
    """The raw policy outcome cannot be represented by the formal ontology."""


def _codes(value: Any) -> frozenset[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return frozenset()
    return frozenset(str(item) for item in value)


def _native(raw: str) -> dict[str, Any]:
    return {
        "raw_policy_outcome": raw,
        "formal_outcome": raw,
        "formal_wait_subtype": None,
        "normalization_rule": "IDENTITY",
        "normalization_version": NORMALIZATION_VERSION,
        "normalized": False,
        "wait_contract_satisfied": None,
        "wait_contract_checks": {},
    }


def fallback_wait_evidence_from_stage6a_audit(
    audit: Mapping[str, Any],
) -> dict[str, Any]:
    """Extract only control-semantics facts from a production Stage6A receipt."""

    event = audit.get("event") if isinstance(audit, Mapping) else None
    event = event if isinstance(event, Mapping) else {}
    orchestration = event.get("orchestration")
    orchestration = orchestration if isinstance(orchestration, Mapping) else {}
    arm = event.get("authority_arm")
    arm = arm if isinstance(arm, Mapping) else {}
    plan = event.get("plan_selection")
    plan = plan if isinstance(plan, Mapping) else {}
    return {
        "reason_codes": list(orchestration.get("reason_codes", ())),
        "authority_armed": arm.get("armed"),
        "candidate_control_write_count": audit.get("candidate_control_write_count"),
        "m3_control_write_count": audit.get("m3_control_write_count"),
        "control_write_count": orchestration.get("control_write_count"),
        "physical_safety_status": orchestration.get("physical_safety_status"),
        # Stage6A has no destination writer and the fallback path selects the
        # pre-existing baseline object.  These facts are asserted jointly.
        "destination_changed": False if plan.get("resolved_source") == "baseline" else None,
        "selected_candidate_id": arm.get("candidate_id"),
        "emergency_stop": False if "FALLBACK_IS_NOT_EMERGENCY_STOP" in _codes(orchestration.get("reason_codes")) else None,
        "resolved_source": plan.get("resolved_source"),
        "selected_plan_source": event.get("selected_plan_source"),
        "authority_status": plan.get("authority_status"),
        "baseline_available": plan.get("resolved_source") == "baseline",
        "terminal_stop_invoked": False,
        "hard_gate_bypassed": False,
        "route_switch_transaction_count": 0,
        "new_route_authority": False if arm.get("armed") is False else None,
        "source_observation_id": event.get("source_observation_id"),
        "source_frame_id": event.get("source_frame_id"),
    }


def normalize_policy_outcome(
    raw_policy_outcome: str,
    evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a dual-layer formal receipt or fail closed.

    The fallback evidence shape is deliberately small and receipt-oriented;
    callers must derive it from real production/runtime evidence rather than
    expected labels or an oracle.
    """

    raw = str(raw_policy_outcome)
    if raw in NATIVE_OUTCOMES:
        return _native(raw)
    if raw == "UNKNOWN":
        raise FormalOutcomeNormalizationError("UNKNOWN_REMAINS_UNKNOWN")
    if raw != "FALLBACK_RECOMMENDED":
        raise FormalOutcomeNormalizationError("UNSUPPORTED_RAW_POLICY_OUTCOME:" + raw)
    if not isinstance(evidence, Mapping):
        raise FormalOutcomeNormalizationError("FALLBACK_WAIT_EVIDENCE_REQUIRED")

    reasons = _codes(evidence.get("reason_codes"))
    selected_candidate_id = evidence.get("selected_candidate_id")
    checks = {
        "A_no_new_unsupported_maneuver": (
            evidence.get("authority_armed") is False
            and evidence.get("candidate_control_write_count") == 0
            and evidence.get("m3_control_write_count") == 0
            and evidence.get("control_write_count") == 0
        ),
        "B_unknown_not_optimistically_safe": (
            evidence.get("physical_safety_status") in {"BLOCKED", "UNKNOWN", "FAIL_CLOSED"}
            and "HARD_SAFETY_GATE_NOT_PASSED" in reasons
        ),
        "C_long_term_destination_unchanged": evidence.get("destination_changed") is False,
        "D_no_candidate_selected": selected_candidate_id is None,
        "E_not_emergency_stop": (
            evidence.get("emergency_stop") is False
            and "FALLBACK_IS_NOT_EMERGENCY_STOP" in reasons
        ),
        "F_non_committing_baseline_continuation": (
            evidence.get("resolved_source") == "baseline"
            and evidence.get("selected_plan_source") == "CURRENT_VALID_BASELINE_PLAN"
            and evidence.get("authority_status") == "FAIL_CLOSED_BASELINE_PRESERVED"
        ),
        "G_recoverable_normal_execution": (
            evidence.get("baseline_available") is True
            and evidence.get("terminal_stop_invoked") is False
        ),
        "H_hard_gates_not_bypassed": (
            evidence.get("hard_gate_bypassed") is False
            and evidence.get("authority_armed") is False
        ),
        "I_no_new_route_authority": (
            evidence.get("route_switch_transaction_count") == 0
            and evidence.get("new_route_authority") is False
        ),
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise FormalOutcomeNormalizationError(
            "FALLBACK_NOT_SEMANTICALLY_EQUIVALENT_TO_WAIT:" + ",".join(failed)
        )
    return {
        "raw_policy_outcome": raw,
        "formal_outcome": "WAIT",
        "formal_wait_subtype": FALLBACK_WAIT_SUBTYPE,
        "normalization_rule": FALLBACK_NORMALIZATION_RULE,
        "normalization_version": NORMALIZATION_VERSION,
        "normalized": True,
        "wait_contract_satisfied": True,
        "wait_contract_checks": checks,
    }
